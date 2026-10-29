"""Replay original/repaired PLC inputs through the unchanged frozen core.

Examples (no model or reporting API is imported):
    python replay.py --dates 2024-04-07 --workers 2
    python replay.py --all --workers 4

Completed dates resume only when input, code and output fingerprints agree.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any
import zipfile

for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tbm_process.config import layer_config
from tbm_process.contracts.samples import validate_canonical_samples
from tbm_process.ingestion.chainage import annotate_chainage_steps
from tbm_process.ingestion.io import canonicalize, json_ready, read_daily_file
from tbm_process.orchestration.current_pipeline import analyze_platform_first_downstream_day
from tbm_process.orchestration.pipeline import analyze_day

SCHEMA = "plc_mapping_repair_replay_v1"
GASES = ("CO2检测", "H2S检测", "SO2检测", "NO2检测", "NO检测", "CH4检测")
RTOL = 1e-9
ATOL = 1e-9


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(json_ready(value), sort_keys=True,
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(json_ready(value), ensure_ascii=False,
        indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    compression = {"method": "gzip", "compresslevel": 3, "mtime": 0} if path.name.endswith(".gz") else None
    frame.to_csv(temporary, index=False, encoding="utf-8-sig", compression=compression)
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_by_date(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    rows = payload if isinstance(payload, list) else payload.get("files", payload.get("inventory"))
    if not isinstance(rows, list):
        raise ValueError("Inventory must be a list, or an object with a files/inventory list")
    indexed = {}
    for row in rows:
        date = row.get("date") or row.get("analysis_date")
        if not date:
            raise ValueError("Inventory entry has no date")
        if date in indexed:
            raise ValueError(f"Duplicate inventory date: {date}")
        indexed[str(date)] = row
    return indexed


def frozen_context(archive: Path, prefix: str) -> dict[str, Any]:
    with zipfile.ZipFile(archive) as zf:
        manifest_name = prefix + "base_analysis/method_manifest.json"
        manifest = json.loads(zf.read(manifest_name))
        project_prefix = prefix.split("outputs/", 1)[0]
        profile = json.loads(zf.read(project_prefix + "configs/field_profiles/tbm9_2023.json"))
        platform = json.loads(zf.read(project_prefix + "configs/platform_analysis.json"))
        summary = json.loads(zf.read(prefix + "base_analysis/analysis_summary.json"))
        checks = {}
        for relative, expected in manifest["source_sha256"].items():
            path = (REPO / relative).resolve()
            if not path.is_relative_to(REPO) or path.suffix not in {".py", ".json"}:
                raise ValueError(f"Unexpected frozen source path: {relative}")
            actual = sha256(path)
            if actual != expected:
                raise ValueError(f"Frozen source/config differs from archive manifest: {relative}")
            checks[relative] = actual
        # These replay dependencies were omitted from the original method manifest.
        for relative in ("tbm_process/ingestion/chainage.py",
                "tbm_process/process/platform_detection.py",
                "tbm_process/orchestration/current_pipeline.py"):
            expected = hashlib.sha256(zf.read(project_prefix + relative)).hexdigest()
            actual = sha256(REPO / relative)
            if actual != expected:
                raise ValueError(f"Replay dependency differs from archive snapshot: {relative}")
            checks[relative] = actual
    return {"config": manifest["config"], "thresholds": manifest["derived_thresholds"],
        "profile": profile, "platform_config": platform, "source_sha256": checks,
        "manifest_sha256": digest_json(manifest), "archive_dates": summary["dates"],
        "archive": str(archive), "archive_prefix": prefix,
        "script_sha256": sha256(Path(__file__)),
        "runtime": {"python": sys.version, "pandas": pd.__version__, "numpy": np.__version__}}


def same_values(left: pd.Series, right: pd.Series) -> np.ndarray:
    if len(left) != len(right):
        raise ValueError("Cannot compare rows with different lengths")
    left = left.reset_index(drop=True)
    right = right.reset_index(drop=True)
    if pd.api.types.is_numeric_dtype(left) or pd.api.types.is_numeric_dtype(right):
        return np.isclose(pd.to_numeric(left, errors="coerce").to_numpy(dtype=float),
            pd.to_numeric(right, errors="coerce").to_numpy(dtype=float),
            rtol=RTOL, atol=ATOL, equal_nan=True)
    return (left.fillna("<missing>").astype(str).to_numpy()
        == right.fillna("<missing>").astype(str).to_numpy())


def compare_samples(actual: pd.DataFrame, expected: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {"passed": True, "actual_rows": len(actual),
        "expected_rows": len(expected), "rtol": RTOL, "atol": ATOL, "differences": {}}
    if len(actual) != len(expected):
        result["passed"] = False
        return result
    for column in expected:
        if column not in actual:
            result["differences"][column] = {"missing_column": True}
            continue
        left, right = actual[column], expected[column]
        if column == "time":
            left = pd.to_datetime(left).astype(str)
            right = pd.to_datetime(right).astype(str)
        mismatch = np.flatnonzero(~same_values(left, right))
        if len(mismatch):
            result["differences"][column] = {"changed_rows": int(len(mismatch)),
                "examples": [{"source_row": int(actual.iloc[i]["source_row"]),
                    "actual": json_ready(left.iloc[i]), "archive": json_ready(right.iloc[i])}
                    for i in mismatch[:5]]}
    result["passed"] = not result["differences"]
    result["checked_columns"] = list(expected.columns)
    return result


def compare_json(actual: Any, expected: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    count = 0

    def difference(path: str, left: Any, right: Any) -> None:
        nonlocal count
        count += 1
        if len(errors) < 30:
            errors.append({"path": path, "actual": left, "archive": right})

    def visit(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                if key not in left or key not in right:
                    difference(f"{path}.{key}", left.get(key), right.get(key))
                else:
                    visit(left[key], right[key], f"{path}.{key}")
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                difference(path + ".length", len(left), len(right))
            for i, (a, b) in enumerate(zip(left, right)):
                visit(a, b, f"{path}[{i}]")
        elif left != right:
            difference(path, left, right)

    visit(json_ready(actual), expected, "$")
    return {"passed": count == 0, "difference_count": count, "examples": errors,
        "strict_numeric_equality": True, "ignored_paths": []}


def repaired_canonical(raw: pd.DataFrame, repaired: pd.DataFrame, *,
        original_canonical: pd.DataFrame,
        date: str, source_name: str, context: dict[str, Any],
        rpm_value_column: str | None, rpm_source_column: str) -> pd.DataFrame:
    if len(raw) != len(repaired):
        raise ValueError(f"Repaired input changes row count for {date}")
    aliases = context["profile"]["aliases"]
    time_column = next((name for name in aliases["time"] if name in repaired), None)
    if time_column is None:
        raise ValueError("Repaired input is missing its timestamp column")
    raw_time_column = next(name for name in aliases["time"] if name in raw)
    raw_times = pd.to_datetime(raw[raw_time_column], errors="raise", format="mixed")
    new_times = pd.to_datetime(repaired[time_column], errors="raise", format="mixed")
    if not raw_times.equals(new_times):
        raise ValueError("Repaired row/time order differs from original input")
    if "source_row" in repaired and not np.array_equal(
            repaired["source_row"].to_numpy(), np.arange(len(raw)) + 2):
        raise ValueError("Repaired source_row must retain original CSV row numbers")
    if "mapping_source_row" in repaired and not np.array_equal(
            repaired["mapping_source_row"].to_numpy(), np.arange(len(raw)) + 2):
        raise ValueError("Repaired mapping_source_row differs from original CSV row numbers")
    if rpm_source_column in repaired:
        rpm_sources = repaired[rpm_source_column]
    elif rpm_source_column == "rpmsource" and {"mapping_rpm_source_index", "mapping_rpm_layout"} <= set(repaired):
        def rpm_origin(index: Any, layout: Any) -> str:
            position = int(index) if pd.notna(index) else -1
            field = str(raw.columns[position]) if 0 <= position < len(raw.columns) else "unresolved"
            return f"index={position};field={field};layout={layout}"
        rpm_sources = pd.Series([rpm_origin(i, label) for i, label in
            zip(repaired["mapping_rpm_source_index"], repaired["mapping_rpm_layout"])])
    else:
        raise ValueError(f"Missing true-RPM provenance column: {rpm_source_column}")
    if rpm_value_column is None:
        rpm_value_column = next((name for name in
            ("rpm_true", "rpm_repaired", "rpm_actual_raw", "刀盘实际转速") if name in repaired), None)
    if rpm_value_column not in repaired:
        raise ValueError("No repaired true-RPM column; specify --rpm-value-column")

    frame = canonicalize(repaired, source_file=Path(source_name), field_aliases=aliases,
        guidance_chainage_mapping=None)
    # canonicalize sorts by time; source_row keeps provenance and RPM on the same row.
    order = frame["source_row"].to_numpy(dtype=int) - 2
    rpm = pd.to_numeric(repaired[rpm_value_column], errors="coerce").iloc[order].to_numpy()
    frame["rpm_actual_raw"] = rpm
    frame["rpm_alternate_raw"] = rpm
    frame["rpmsource"] = rpm_sources.iloc[order].to_numpy()
    original = original_canonical.set_index("source_row").loc[frame["source_row"]]
    for column in ("chainage_raw", "chainage_quality", "chainage_mapping_mode", "chainage_mapping_version",
            "chainage_source_field", "chainage_middle_source_field"):
        frame[column] = original[column].to_numpy()
    middle_field = context["profile"].get("guidance_chainage_mapping", {}).get("fields", {}).get("middle")
    if isinstance(middle_field, str) and middle_field in repaired:
        frame["chainage_middle_canonical"] = pd.to_numeric(
            repaired[middle_field], errors="coerce").iloc[order].to_numpy()
    for column in ("mapping_rpm_source_index", "mapping_rpm_layout", "mapping_version", "mapping_navigation_mode"):
        if column in repaired:
            frame[column] = repaired[column].iloc[order].to_numpy()
    frame["replay_rpm_value_column"] = rpm_value_column
    frame = annotate_chainage_steps(frame, config=context["profile"].get("guidance_chainage_mapping"))
    validate_canonical_samples(frame)
    return frame


def analyze(frame: pd.DataFrame, source_name: str, date: str,
        context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    config = context["config"]
    base = analyze_day(frame, path=Path(source_name), thresholds=context["thresholds"], config=config)
    current = analyze_platform_first_downstream_day(base["samples"], date=date,
        base_result=base["complete_structured"], experiment_config=context["platform_config"],
        phase_config=layer_config(config, "process"),
        dynamics_config=layer_config(config, "dynamics"),
        orchestration_config=layer_config(config, "orchestration"))
    return base, current


def append_registered_fields(result: dict[str, Any], raw: pd.DataFrame, *,
        original_path: Path, gas_source_path: Path, aliases: dict[str, list[str]]) -> None:
    missing = set(GASES) - set(raw)
    time_column = next(name for name in aliases["time"] if name in raw)
    gas = raw.reindex(columns=GASES).copy()
    gas["source_row"] = raw.get("mapping_source_row", pd.Series(np.arange(len(raw)) + 2)).to_numpy()
    gas["_gas_timestamp"] = pd.to_datetime(raw[time_column], errors="raise", format="mixed").to_numpy()
    samples = result["samples"].merge(gas, on="source_row", how="left", sort=False, validate="one_to_one")
    if not samples["time"].equals(samples.pop("_gas_timestamp")):
        raise ValueError(f"Gas source-row/time keys differ: {gas_source_path}")
    samples["raw_source_path"] = str(original_path)
    samples["gas_source_path"] = str(gas_source_path)
    samples["missing_gas_source_fields"] = "|".join(sorted(missing))
    result["samples"] = samples


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    frame = result["samples"]
    chainage = pd.to_numeric(frame["chainage"], errors="coerce").dropna()
    dt = pd.to_numeric(frame["sample_dt_seconds"], errors="coerce").fillna(0)
    states = frame["machine_state"].astype(str)
    durations = {str(state): float(dt.loc[states.eq(state)].sum()) for state in states.unique()}
    stopped = float(durations.get("stopped", 0))
    observed = float(dt.sum())
    return {"row_count": len(frame), "coverage_start": frame["time"].min(),
        "coverage_end": frame["time"].max(), "chainage_start_m": chainage.iloc[0] if len(chainage) else None,
        "chainage_end_m": chainage.iloc[-1] if len(chainage) else None,
        "chainage_min_m": chainage.min() if len(chainage) else None,
        "chainage_max_m": chainage.max() if len(chainage) else None,
        "daily_advance_m": chainage.iloc[-1] - chainage.iloc[0] if len(chainage) >= 2 else None,
        "observed_seconds": observed, "stopped_seconds": stopped,
        "working_seconds": observed - stopped,
        "utilization_percent": 100 * (observed - stopped) / observed if observed else None,
        "state_seconds": durations, "interval_count": len(result["intervals"]),
        "phase_count": len(result["phases"]),
        "phase_counts": pd.Series([p["phase_label"] for p in result["phases"]], dtype=object).value_counts().to_dict(),
        "hard_gap_count": int(frame["hard_gap_before"].sum()),
        "rpm_quality_counts": frame["rpm_quality"].value_counts().to_dict()}


def compare_branches(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    a = before["samples"].sort_values("source_row").reset_index(drop=True)
    b = after["samples"].sort_values("source_row").reset_index(drop=True)
    if not a["source_row"].equals(b["source_row"]) or not a["time"].equals(b["time"]):
        raise ValueError("Branch source-row/time keys differ")
    detail = a[["source_row", "time"]].copy()
    changed = {}
    any_changed = np.zeros(len(a), dtype=bool)
    for column in ("chainage", "rpm_actual_raw", "rpm_alternate_raw", "rpm_canonical",
            "rpm_quality", "penetration_analysis", "penetration_quality", "machine_state",
            "valid_advance_interval", "interval_id", "phase_id", "phase_label", "analysis_scope_label", *GASES):
        mask = ~same_values(a[column], b[column])
        changed[column] = int(mask.sum())
        any_changed |= mask
        detail["before_" + column] = a[column]
        detail["after_" + column] = b[column]
    detail["rpmsource"] = b.get("rpmsource", pd.Series(None, index=b.index, dtype=object))
    keys = ("interval_id", "phase_id", "phase_label", "start_time", "end_time",
        "source_row_start", "source_row_end", "sample_count", "duration_seconds", "segmentation_status")
    ranges = lambda rows: [{key: row[key] for key in keys if key in row} for row in rows]
    first, second = summarize(before), summarize(after)
    delta = {key: second[key] - first[key] for key in first
        if isinstance(first[key], (int, float, np.number)) and isinstance(second[key], (int, float, np.number))}
    return {"before": first, "after": second, "delta_after_minus_before": delta,
        "changed_rows_by_column": changed, "any_changed_row_count": int(any_changed.sum()),
        "interval_ranges_before": ranges(before["intervals"]), "interval_ranges_after": ranges(after["intervals"]),
        "phase_ranges_before": ranges(before["phases"]), "phase_ranges_after": ranges(after["phases"]),
        "assignment_note": "IDs are compared as labels; renumbering is not an interval correspondence claim."}, detail.loc[any_changed]


def save_branch(output: Path, branch: str, date: str, base: dict[str, Any],
        current: dict[str, Any]) -> list[Path]:
    paths = []
    for directory, result in ((output / branch / "base_analysis", base), (output / branch, current)):
        sample = directory / "processed_samples" / f"{date}.csv.gz"
        full = directory / "structured_full_days" / f"{date}.json"
        write_frame(sample, result["samples"])
        write_json(full, result["complete_structured"])
        paths.extend((sample, full))
    tables = output / branch / "tables" / "days" / f"{date}.json"
    write_json(tables, {"platforms": current["intervals"], "phases": current["phases"]})
    return paths + [tables]


def original_path(row: dict[str, Any], inventory: Path) -> Path:
    value = row.get("source") or row.get("raw_source_path") or row.get("source_path") or row.get("raw_source")
    if not value:
        raise ValueError("Inventory entry needs source/raw_source_path/source_path")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (inventory.parent / path).resolve()


def run_day(task: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    date, context = task["date"], task["context"]
    output, source, repaired_path = map(Path, (task["output"], task["source"], task["repaired"]))
    done_path = output / "status" / f"{date}.json"
    archive_check_path = output / "comparisons" / f"{date}.archive.json"
    try:
        source_hash, repaired_hash = sha256(source), sha256(repaired_path)
        if task.get("expected_source_sha256") and source_hash != task["expected_source_sha256"]:
            raise ValueError("Original CSV hash differs from inventory")
        prefix = context["archive_prefix"]
        members = {f"{layer}_{kind}": prefix + f"{layer}_analysis/" +
            (f"processed_samples/{date}.csv" if kind == "samples" else f"structured_full_days/{date}.json")
            for layer in ("base", "current") for kind in ("samples", "full")}
        with zipfile.ZipFile(context["archive"]) as zf:
            archive_members = {name: {"path": member, "crc": zf.getinfo(member).CRC,
                "size": zf.getinfo(member).file_size} for name, member in members.items()}
            inputs = {"context": context, "date": date, "source": str(source), "source_sha256": source_hash,
                "repaired": str(repaired_path), "repaired_sha256": repaired_hash,
                "archive_members": archive_members, "rpm_value_column": task["rpm_value_column"],
                "rpm_source_column": task["rpm_source_column"]}
            fingerprint = digest_json(inputs)
            if task["resume"] and done_path.is_file():
                previous = read_json(done_path)
                if (previous.get("status") == "complete" and previous.get("input_fingerprint") == fingerprint
                        and previous.get("archive_verification_passed")
                        and all((output / p).is_file() and sha256(output / p) == h
                            for p, h in previous.get("artifact_sha256", {}).items())
                        and previous.get("artifact_sha256")):
                    return {"date": date, "status": "resumed", "elapsed_seconds": time.monotonic() - started}
            write_json(done_path, {"date": date, "status": "running", "input_fingerprint": fingerprint})
            historical = {key: (pd.read_csv(io.BytesIO(zf.read(member)), parse_dates=["time"], low_memory=False)
                if key.endswith("samples") else json.loads(zf.read(member))) for key, member in members.items()}

        raw = read_daily_file(source)
        source_name = historical["base_full"]["source_file"]
        if Path(source_name).name != source_name:
            raise ValueError("Archive source_file must be a normalized filename")
        canonical = canonicalize(raw, source_file=Path(source_name), field_aliases=context["profile"]["aliases"],
            guidance_chainage_mapping=context["profile"].get("guidance_chainage_mapping"))
        validate_canonical_samples(canonical)
        base_before, current_before = analyze(canonical, source_name, date, context)
        checks = {"base_samples": compare_samples(base_before["samples"], historical["base_samples"]),
            "base_full": compare_json(base_before["complete_structured"], historical["base_full"]),
            "current_samples": compare_samples(current_before["samples"], historical["current_samples"]),
            "current_full": compare_json(current_before["complete_structured"], historical["current_full"])}
        verified = all(check["passed"] for check in checks.values())
        write_json(archive_check_path, {"date": date, "passed": verified, "checks": checks})
        if not verified:
            raise ValueError(f"Before branch does not reproduce archive; see {archive_check_path}")

        repaired = pd.read_csv(repaired_path, encoding="utf-8-sig", low_memory=False)
        after_canonical = repaired_canonical(raw, repaired, original_canonical=canonical,
            date=date, source_name=source_name, context=context,
            rpm_value_column=task["rpm_value_column"], rpm_source_column=task["rpm_source_column"])
        base_after, current_after = analyze(after_canonical, source_name, date, context)
        # Enrichment is deliberately after all four archive comparisons.
        for results, gas_raw, gas_path in (((base_before, current_before), raw, source),
                ((base_after, current_after), repaired, repaired_path)):
            for result in results:
                append_registered_fields(result, gas_raw, original_path=source,
                    gas_source_path=gas_path, aliases=context["profile"]["aliases"])
        comparison = {"date": date, "archive_verification_passed": True, "llm_invoked": False,
            "frozen_core_modified": False, "fixed_derived_thresholds": context["thresholds"],
            "rpm_bridge": "after rpm_actual_raw and rpm_alternate_raw both use row-local repaired true RPM; rpmsource retained"}
        artifacts = [archive_check_path]
        for layer, a, b in (("base", base_before, base_after), ("current", current_before, current_after)):
            comparison[layer], changes = compare_branches(a, b)
            changes_path = output / "comparisons" / f"{date}.{layer}.changed_rows.csv"
            write_frame(changes_path, changes)
            artifacts.append(changes_path)
        artifacts.extend(save_branch(output, "before", date, base_before, current_before))
        artifacts.extend(save_branch(output, "after", date, base_after, current_after))
        comparison_path = output / "comparisons" / f"{date}.json"
        write_json(comparison_path, comparison)
        artifacts.append(comparison_path)
        if sha256(source) != source_hash or sha256(repaired_path) != repaired_hash:
            raise ValueError("Input changed while replay was running")
        record = {"date": date, "status": "complete", "schema_version": SCHEMA,
            "input_fingerprint": fingerprint, "inputs": inputs, "archive_verification_passed": True,
            "artifact_sha256": {str(path.relative_to(output)): sha256(path) for path in artifacts},
            "elapsed_seconds": time.monotonic() - started, "llm_invoked": False}
        write_json(done_path, record)
        return {key: record[key] for key in ("date", "status", "elapsed_seconds", "archive_verification_passed")}
    except Exception as exc:
        failure = {"date": date, "status": "failed", "error": str(exc),
            "traceback": traceback.format_exc(), "elapsed_seconds": time.monotonic() - started}
        write_json(output / "failures" / f"{date}.json", failure)
        write_json(done_path, failure)
        return failure


def aggregate_tables(output: Path) -> None:
    dates = sorted(path.stem for path in (output / "status").glob("*.json")
        if read_json(path).get("status") == "complete")
    for branch in ("before", "after"):
        rows: dict[str, list[dict[str, Any]]] = {"platforms": [], "phases": []}
        for date in dates:
            path = output / branch / "tables" / "days" / f"{date}.json"
            if path.is_file():
                day = read_json(path)
                for key in rows:
                    rows[key].extend(day[key])
        for key, name, empty_columns in (
            ("platforms", "实验平台明细.csv", ["date", "platform_id", "start_time", "end_time"]),
            ("phases", "实验四阶段明细.csv", ["interval_id", "phase_id", "phase_label", "start_time", "end_time"])):
            frame = pd.DataFrame(rows[key]) if rows[key] else pd.DataFrame(columns=empty_columns)
            write_frame(output / branch / "tables" / name, frame)
    summaries = []
    for date in dates:
        comparison = read_json(output / "comparisons" / f"{date}.json")
        row = {"date": date, "archive_verification_passed": comparison["archive_verification_passed"]}
        for layer in ("base", "current"):
            for scope in ("before", "after", "delta_after_minus_before", "changed_rows_by_column"):
                row.update({f"{layer}.{scope}.{key}": value for key, value in comparison[layer][scope].items()
                    if not isinstance(value, (dict, list))})
        summaries.append(row)
    write_frame(output / "comparison_summary.csv", pd.DataFrame(summaries, columns=None if summaries else ["date"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Contains inventory.json and repaired_raw/DATE.csv.gz")
    parser.add_argument("--inventory", type=Path, help="Defaults to root/inventory.json, then root/source_inventory.json")
    parser.add_argument("--archive", type=Path, default=Path.home() / "Desktop/tbm-plc-process-analysis.zip")
    parser.add_argument("--archive-prefix", default="tbm-plc-process-analysis/outputs/paper_experiment_rebuild_v1/")
    parser.add_argument("--output-dir", type=Path, help="Defaults to root/replay")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--dates", nargs="+", help="Only replay these ISO dates")
    selection.add_argument("--all", action="store_true", help="Replay all dates from the original frozen archive")
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), default=2)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rpm-value-column", help="Default: rpm_true, rpm_repaired, rpm_actual_raw, or actual-RPM raw header")
    parser.add_argument("--rpm-source-column", default="rpmsource")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.expanduser().resolve()
    inventory = (args.inventory or root / "inventory.json").expanduser().resolve()
    if args.inventory is None and not inventory.is_file():
        inventory = root / "source_inventory.json"
    indexed = inventory_by_date(inventory)
    context = frozen_context(args.archive.expanduser().resolve(), args.archive_prefix.rstrip("/") + "/")
    dates = context["archive_dates"] if args.all else sorted(set(args.dates))
    output = (args.output_dir or root / "replay").expanduser().resolve()
    tasks = []
    for date in dates:
        if date not in context["archive_dates"] or date not in indexed:
            raise ValueError(f"Date missing from archive or inventory: {date}")
        row = indexed[date]
        if str(row.get("status", "")).startswith("quarantined"):
            raise ValueError(f"Quarantined original input: {date}")
        source = original_path(row, inventory)
        repaired = root / "repaired_raw" / f"{date}.csv.gz"
        for path in (source, repaired):
            if not path.is_file():
                raise FileNotFoundError(path)
            if output == path or output in path.parents:
                raise ValueError("Replay output directory must not contain an input file")
        tasks.append({"date": date, "context": context, "source": str(source), "repaired": str(repaired),
            "expected_source_sha256": row.get("sha256") or row.get("file_sha256") or row.get("raw_sha256"),
            "output": str(output), "resume": args.resume,
            "rpm_value_column": args.rpm_value_column, "rpm_source_column": args.rpm_source_column})
    write_json(output / "run_manifest.json", {"schema_version": SCHEMA, "context": context,
        "inventory": str(inventory), "inventory_sha256": sha256(inventory), "requested_dates": dates,
        "workers": args.workers, "resume": args.resume, "llm_invoked": False})
    outcomes = []

    def record(result: dict[str, Any]) -> None:
        outcomes.append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "traceback"}, ensure_ascii=False), flush=True)

    if args.workers == 1:
        for task in tasks:
            record(run_day(task))
    else:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {pool.submit(run_day, task): task["date"] for task in tasks}
            for future in as_completed(futures):
                try:
                    record(future.result())
                except Exception as exc:
                    record({"date": futures[future], "status": "failed", "error": str(exc)})
    aggregate_tables(output)
    summary = {"schema_version": SCHEMA, "requested_day_count": len(dates),
        "complete_or_resumed": sum(row["status"] in {"complete", "resumed"} for row in outcomes),
        "failed": sum(row["status"] == "failed" for row in outcomes),
        "llm_invoked": False, "days": sorted(outcomes, key=lambda row: row["date"])}
    write_json(output / "run_summary.json", summary)
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
