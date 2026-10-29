"""Apply the existing reviewed row mapping, preserving CSV value tokens."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from experiments.plc_mapping_repair_v1.repair import GASES, MOTORS, VERSION, repair_frame
from experiments.plc_mapping_repair_v1.audit_all import frame_layout
from tbm_process.ingestion.io import canonicalize, discover_files
from tbm_process.quality.preprocessing import preprocess_day

DEFAULT_ROOTS = [Path.home() / "Desktop" / name for name in (
    "TBM9-2023-6f-伯舒拉岭右线_副本", "TBM9-2024-part1", "TBM9-2024-part2")]
DEFAULT_OUT = Path.home() / "Desktop/TBM9_PLC_修复标准化_20261025"
EXPORT_VERSION = "tbm9_lossless_standardized_export_v1_20261025"
TIME_FIELD = "运行时间-time"
CORE = ["刀盘扭矩", "推力", "推进速度", "贯入度", "推进给定速度"]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def read_tokens(path, encoding="utf-8-sig"):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding, low_memory=False)


def day_from_timestamps(raw):
    if TIME_FIELD not in raw:
        raise ValueError("Missing original timestamp column")
    times = pd.to_datetime(raw[TIME_FIELD], errors="coerce", format="mixed")
    dates = times.dt.strftime("%Y-%m-%d").dropna().unique().tolist()
    if len(dates) != 1 or times.isna().any():
        raise ValueError(f"Cannot assign a unique daily name: dates={dates}, invalid={int(times.isna().sum())}")
    return dates[0], times


def filename_for_day(day):
    return "tbm_data_" + datetime.strptime(day, "%Y-%m-%d").strftime("%Y%m%d") + ".csv"


def inventory(roots, output):
    previous = json.loads((REPO / "experiments/plc_mapping_repair_v1/source_inventory.json").read_text())
    by_hash = {item["sha256"]: item for item in previous}
    rows = []
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(root)
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".") and p.suffix.lower() == ".csv"):
            counts = Counter()
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                header = next(reader)
                try:
                    ti = header.index(TIME_FIELD)
                except ValueError as exc:
                    raise ValueError(f"Missing timestamp field: {path}") from exc
                stamps = []
                for row in reader:
                    counts[len(row)] += 1
                    if len(row) != len(header):
                        raise ValueError(f"CSV structure mismatch: {path}, row {len(stamps)+2}")
                    stamps.append(row[ti])
            if len(set(header)) != len(header):
                raise ValueError(f"Duplicate header: {path}")
            day, times = day_from_timestamps(pd.DataFrame({TIME_FIELD: stamps}))
            digest = sha(path)
            historic = by_hash.get(digest)
            record = {"source": str(path), "source_root": str(root), "source_name": path.name,
                      "sha256": digest, "bytes": path.stat().st_size, "date": day,
                      "rows": len(stamps), "header_fields": len(header),
                      "header_sha256": hashlib.sha256(json.dumps(header, ensure_ascii=False).encode()).hexdigest(),
                      "row_field_counts": dict(counts), "encoding": "utf-8-sig",
                      "first_timestamp": stamps[0], "last_timestamp": stamps[-1],
                      "duplicate_timestamps": int(times.duplicated().sum()),
                      "backward_timestamp_steps": int(times.diff().dt.total_seconds().lt(0).sum()),
                      "historical_audit_exact_sha_match": historic is not None,
                      "historical_audit_source": historic["source"] if historic else None,
                      "target_name": filename_for_day(day)}
            if historic and (historic["date"] != day or historic["rows"] != len(stamps)):
                raise ValueError(f"Historical source identity mismatch: {path}")
            rows.append(record)
            if len(rows) % 30 == 0:
                print(f"Inventory {len(rows)} files; last date {day}", flush=True)
    counts = Counter(item["target_name"] for item in rows)
    duplicate_names = [name for name, n in counts.items() if n > 1]
    if duplicate_names:
        save_json(output / "audit/naming_conflicts.json", rows)
        raise ValueError(f"Date/name collisions must not overwrite data: {duplicate_names}")
    save_json(output / "audit/source_inventory.json", sorted(rows, key=lambda item: item["date"]))
    return sorted(rows, key=lambda item: item["date"])


def exact_mapping_tokens(raw, repaired, meta):
    """Detection uses numbers, but exported values retain original CSV tokens."""
    out = repaired.copy()
    evidence = meta.copy()
    source = raw.to_numpy(dtype=object)
    for name in [c for c in evidence if c.startswith("source_index__")]:
        field = name.removeprefix("source_index__")
        positions = evidence[name].to_numpy(int)
        good = positions >= 0
        values = np.full(len(raw), "", dtype=object)
        values[good] = source[np.flatnonzero(good), positions[good]]
        numeric_expected = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy()
        numeric_actual = pd.to_numeric(repaired[field], errors="coerce").to_numpy()
        if not np.allclose(numeric_actual, numeric_expected, rtol=1e-12, atol=1e-12, equal_nan=True):
            raise ValueError(f"Token restoration changes mapping result: {field}")
        out[field] = values
    # Navigation is independently located and must not be repaired a second time.
    layouts = {
        "aligned": ("导向盾首里程", "导向盾中里程"),
        "shifted_left": ("导向滚动角", "导向盾首里程"),
        "shifted_right": ("导向盾中里程", "导向盾尾里程"),
    }
    for offset, field in enumerate(["导向盾首里程", "导向盾中里程"]):
        positions = np.full(len(raw), -1, dtype=int)
        values = np.full(len(raw), "", dtype=object)
        for mode, fields in layouts.items():
            chosen = evidence.navigation_mode.eq(mode).to_numpy()
            if fields[offset] in raw:
                index = raw.columns.get_loc(fields[offset])
                values[chosen] = source[chosen, index]
                positions[chosen] = index
        if not np.allclose(pd.to_numeric(pd.Series(values), errors="coerce"),
                           pd.to_numeric(repaired[field], errors="coerce"), rtol=1e-12, atol=1e-12, equal_nan=True):
            raise ValueError(f"Navigation token reconstruction mismatch: {field}")
        out[field] = values
        evidence["source_index__" + field] = positions
    for field in CORE:
        if field in raw:
            values = raw[field].copy()
            values.loc[evidence.navigation_mode.eq("shifted_right")] = ""
            out[field] = values
    return out, evidence


def add_quality_flags(raw, out, config, chainage_config):
    _, times = day_from_timestamps(raw)
    gap = times.diff().dt.total_seconds()
    out["quality_duplicate_timestamp"] = times.duplicated(keep=False)
    out["quality_backward_timestamp"] = gap.lt(0)
    out["quality_hard_gap_before"] = gap.gt(float(config["large_gap_seconds"]))
    for field, label in [("推力", "thrust"), ("刀盘扭矩", "torque"), ("推进速度", "advance_speed"), ("贯入度", "penetration")]:
        out["quality_negative_" + label] = pd.to_numeric(out[field], errors="coerce").lt(0)
    head = pd.to_numeric(out["导向盾首里程"], errors="coerce")
    out["quality_chainage_short_gap_jump"] = gap.between(0, float(chainage_config["maximum_short_gap_seconds"])) & head.diff().abs().gt(float(chainage_config["maximum_short_gap_step_m"]))
    out["quality_chainage_missing"] = head.isna()
    out["mapping_export_version"] = EXPORT_VERSION
    return out


def validate_exact(raw, fixed, evidence):
    if not fixed[TIME_FIELD].equals(raw[TIME_FIELD]):
        raise ValueError("Original timestamp strings changed")
    if len(raw) != len(fixed) or not np.array_equal(fixed.mapping_source_row.to_numpy(int), np.arange(len(raw)) + 2):
        raise ValueError("Source row count/order changed")
    values = raw.to_numpy(dtype=object)
    altered = set()
    checked = 0
    for key in [k for k in evidence if k.startswith("source_index__")]:
        field = key.removeprefix("source_index__")
        altered.add(field)
        pos = evidence[key].to_numpy(int)
        expected = np.full(len(raw), "", dtype=object)
        valid = pos >= 0
        expected[valid] = values[np.flatnonzero(valid), pos[valid]]
        if not np.array_equal(fixed[field].to_numpy(), expected):
            raise ValueError(f"Exact original token lineage mismatch: {field}")
        checked += len(raw)
    altered.update(CORE)
    for field in raw:
        if field not in altered and not raw[field].equals(fixed[field]):
            raise ValueError(f"Unrelated source column changed: {field}")
    for field in CORE:
        if field not in raw:
            continue
        good = ~evidence.navigation_mode.eq("shifted_right")
        if not raw.loc[good, field].equals(fixed.loc[good, field]):
            raise ValueError(f"Unrelated core value changed: {field}")
        if not fixed.loc[~good, field].eq("").all():
            raise ValueError(f"Unresolved core record not masked: {field}")
    for field in fixed:
        if field.startswith("raw__") and field[5:] in raw and not fixed[field].equals(raw[field[5:]]):
            raise ValueError(f"Original shadow values changed: {field}")
    return checked


def compare_historical(day, fixed):
    previous = REPO / "experiments/plc_mapping_repair_v1/repaired_raw" / (day + ".csv.gz")
    if not previous.is_file():
        return {"available": False, "changed_fields": None}
    old = pd.read_csv(previous, low_memory=False)
    if len(old) != len(fixed):
        raise ValueError(f"Historical row count mismatch: {day}")
    fields = ["刀盘实际转速", "刀盘给定转速", "导向盾首里程", "导向盾中里程", *CORE, *GASES, *MOTORS]
    changes = {}
    for field in fields:
        a, b = pd.to_numeric(old[field], errors="coerce"), pd.to_numeric(fixed[field], errors="coerce")
        different = ~np.isclose(a, b, rtol=1e-12, atol=1e-12, equal_nan=True)
        if different.any():
            changes[field] = int(different.sum())
    return {"available": True, "changed_fields": changes}


def build_profile(original):
    profile = json.loads(json.dumps(original))
    profile["profile_id"] = "tbm9_repaired_row_mapping_v1r2"
    profile["aliases"]["rpm_actual_raw"] = ["刀盘实际转速"]
    profile["aliases"]["rpm_alternate_raw"] = ["刀盘实际转速"]
    profile["guidance_chainage_mapping"]["enabled"] = False
    profile["repair_input_contract"] = {
        "required_mapping_version": VERSION,
        "note": "Actual RPM already resolved per row. Both canonical RPM slots read that same column, so the legacy date switch cannot select an unrepaired neighboring signal. Original 刀盘给定频率 remains untouched.",
        "navigation": "Head/middle values are already aligned. Do not run the original navigation layout detector again.",
        "provenance": "mapping_* columns and provenance sidecars are authoritative. The legacy rpm_source label is not the original physical source identity.",
        "signal_quality": "Existing downstream speed/RPM/penetration quality rules still apply. Mapping resolution is not sensor or unit certification."}
    return profile


def compatibility_check(fixed, profile, config):
    frame = canonicalize(fixed, source_file=Path("compatibility.csv"), field_aliases=profile["aliases"],
                         guidance_chainage_mapping=profile["guidance_chainage_mapping"])
    order = frame.source_row.to_numpy(int) - 2
    expected = pd.to_numeric(fixed["刀盘实际转速"], errors="coerce").iloc[order].to_numpy()
    for field in ["rpm_actual_raw", "rpm_alternate_raw"]:
        if not np.allclose(frame[field], expected, rtol=0, atol=0, equal_nan=True):
            raise ValueError("Compatibility profile reintroduces calendar RPM switching")
    return True


def protected_files():
    paths = list((REPO / "tbm_process").rglob("*.py"))
    paths += [REPO / "config.json", REPO / "configs/field_profiles/tbm9_2023.json"]
    paths += [REPO / "experiments/plc_mapping_repair_v1/repair.py", REPO / "experiments/plc_mapping_repair_v1/audit_all.py"]
    return {str(path): sha(path) for path in paths}


def run(output, roots, resume=False):
    started = time.monotonic()
    if output.exists() and not resume:
        raise FileExistsError(f"Refusing to overwrite output directory: {output}; use --resume to verify and continue")
    output.mkdir(parents=True, exist_ok=True)
    for directory in ["data", "audit/daily", "audit/provenance", "code"]:
        (output / directory).mkdir(parents=True, exist_ok=True)
    protected = protected_files()
    save_json(output / "audit/protected_code_sha256.json", protected)
    sources = inventory(roots, output)
    base_profile = json.loads((REPO / "configs/field_profiles/tbm9_2023.json").read_text())
    config = json.loads((REPO / "config.json").read_text())
    profile = build_profile(base_profile)
    save_json(output / "field_profile_repaired.json", profile)
    save_json(output / "audit/method.json", {"mapping_version": VERSION, "export_version": EXPORT_VERSION,
        "historical_switch_timestamp": config["rpm_switch_timestamp"], "date_used_for_mapping": False,
        "date_used_for_naming": "All original timestamps inside each daily file, never filename/export date",
        "originals_overwritten": False, "raw_numeric_tokens_preserved": True,
        "normalizing_units": False, "llm_calls": 0, "frozen_segmentation_modified": False})
    daily, fields_changed = [], []
    for index, source in enumerate(sources, 1):
        day = source["date"]
        destination = output / "data" / source["target_name"]
        provenance = output / "audit/provenance" / (day + ".csv.gz")
        checkpoint = output / "audit/daily" / (day + ".json")
        if resume and checkpoint.exists():
            record = json.loads(checkpoint.read_text())
            if record["source_sha256"] != source["sha256"] or sha(destination) != record["output_sha256"] or sha(provenance) != record["provenance_sha256"]:
                raise ValueError(f"Resume integrity mismatch: {day}")
            daily.append(record)
            fields_changed.extend(record.pop("field_change_records", []))
            continue
        if destination.exists():
            raise FileExistsError(f"Uncheckpointed target must not be overwritten: {destination}")
        raw = read_tokens(source["source"])
        actual_day, _ = day_from_timestamps(raw)
        if actual_day != day:
            raise ValueError("Source date changed after inventory")
        repaired, evidence = repair_frame(raw)
        fixed, evidence = exact_mapping_tokens(raw, repaired, evidence)
        fixed = add_quality_flags(raw, fixed, config, base_profile["guidance_chainage_mapping"])
        lineage = validate_exact(raw, fixed, evidence)
        compatibility_check(fixed, profile, config)
        historical = compare_historical(day, fixed)
        if source["historical_audit_exact_sha_match"] and historical["available"] and historical["changed_fields"]:
            raise ValueError(f"Unexplained difference from reviewed repair: {day}: {historical}")
        fixed.to_csv(destination, index=False, encoding="utf-8-sig")
        evidence.to_csv(provenance, index=False, encoding="utf-8-sig", compression={"method": "gzip", "compresslevel": 1, "mtime": 0})
        reread = read_tokens(destination)
        reread["mapping_source_row"] = pd.to_numeric(reread.mapping_source_row)
        validate_exact(raw, reread, evidence)
        # Appended booleans are stored as text in the independent CSV round trip.
        for field in fixed:
            if field not in raw and not field.startswith("raw__") and field != "mapping_source_row":
                expected = fixed[field].fillna("").astype(str).to_numpy()
                if not np.array_equal(expected, reread[field].to_numpy()):
                    raise ValueError(f"Appended evidence round-trip mismatch: {field}")
        if sha(source["source"]) != source["sha256"]:
            raise ValueError(f"Source changed during processing: {source['source']}")
        changes = []
        for field in ["刀盘实际转速", "刀盘给定转速", "导向盾首里程", "导向盾中里程", *CORE, *GASES, *MOTORS]:
            before = pd.to_numeric(raw[field], errors="coerce") if field in raw else pd.Series(np.nan, index=raw.index)
            after = pd.to_numeric(fixed[field], errors="coerce")
            count = int((~np.isclose(before, after, rtol=1e-12, atol=1e-12, equal_nan=True)).sum())
            if count:
                changes.append({"date": day, "field": field, "changed_rows": count,
                                "output_missing_rows": int(after.isna().sum())})
        fields_changed.extend(changes)
        record = {"date": day, "source": source["source"], "source_sha256": source["sha256"],
                  "output": destination.relative_to(output).as_posix(), "output_sha256": sha(destination),
                  "output_bytes": destination.stat().st_size, "provenance_sha256": sha(provenance),
                  "rows": len(raw), "raw_columns": len(raw.columns), "output_columns": len(fixed.columns),
                  "rpm_resolved": int(evidence.rpm_layout.ne("unresolved").sum()),
                  "rpm_relocated": int(evidence.rpm_layout.eq("relocated_after_brake").sum()),
                  "rpm_unresolved": int(evidence.rpm_layout.eq("unresolved").sum()),
                  "motor_unresolved": int(evidence.motor_block_start_index.lt(0).sum()),
                  "gas_resolved": int(evidence.gas_block_start_index.ge(0).sum()),
                  "gas_unresolved": int(evidence.gas_block_start_index.lt(0).sum()),
                  "navigation_missing": int(pd.to_numeric(fixed["导向盾首里程"], errors="coerce").isna().sum()),
                  "navigation_mode_counts": {str(k): int(v) for k, v in evidence.navigation_mode.value_counts().items()},
                  "core_quarantined": int(evidence.navigation_mode.eq("shifted_right").sum()),
                  "negative_thrust_rows": int(fixed.quality_negative_thrust.sum()),
                  "short_gap_chainage_jump_rows": int(fixed.quality_chainage_short_gap_jump.sum()),
                  "hard_gaps": int(fixed.quality_hard_gap_before.sum()),
                  "lineage_cells_checked": lineage, "all_original_value_tokens_verified": True,
                  "csv_write_read_verified": True, "historical_effective_value_comparison": historical,
                  "field_change_records": changes}
        save_json(checkpoint, record)
        daily.append(record)
        print(f"Export {index}/{len(sources)} {day}: RPM resolved {record['rpm_resolved']}/{len(raw)}, gas {record['gas_resolved']}/{len(raw)}", flush=True)
    if any(sha(path) != digest for path, digest in protected.items()):
        raise ValueError("Protected source code/configuration changed during export")
    if len(discover_files(output / "data")) != len(sources):
        raise ValueError("Standard CLI file discovery does not match exported file count")
    for record in daily:
        if sha(record["source"]) != record["source_sha256"]:
            raise ValueError("Original file changed before finalization")
    pd.DataFrame([{k: v for k, v in r.items() if k not in ["navigation_mode_counts", "historical_effective_value_comparison", "field_change_records"]}
                  for r in daily]).to_csv(output / "文件命名与修复清单.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(fields_changed).to_csv(output / "audit/field_changes.csv", index=False, encoding="utf-8-sig")
    summary = {"completed_at_utc": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic()-started,
               "source_files": len(sources), "output_files": len(daily), "rows": sum(r["rows"] for r in daily),
               "first_date": min(r["date"] for r in daily), "last_date": max(r["date"] for r in daily),
               "historical_sha_matching_sources": sum(s["historical_audit_exact_sha_match"] for s in sources),
               "historical_effective_values_changed_days": [r["date"] for r in daily if r["historical_effective_value_comparison"]["changed_fields"]],
               **{key: sum(r[key] for r in daily) for key in ["rpm_resolved", "rpm_relocated", "rpm_unresolved", "gas_resolved", "gas_unresolved", "motor_unresolved", "navigation_missing", "core_quarantined", "negative_thrust_rows", "short_gap_chainage_jump_rows", "lineage_cells_checked", "output_bytes"]},
               "original_sources_unchanged": True, "protected_code_unchanged": True,
               "all_csv_roundtrips_validated": True, "units_confirmed": False,
               "all_fields_repaired_claimed": False, "mapping_version": VERSION, "export_version": EXPORT_VERSION,
               "api_calls": 0, "data_directory": str(output / "data")}
    save_json(output / "SUMMARY.json", summary)
    for src in [Path(__file__), HERE / "test_export.py"]:
        if src.exists():
            shutil.copy2(src, output / "code" / src.name)
    for folder in ["experiments/plc_mapping_repair_v1", "tbm_process/ingestion"]:
        destination = output / "code" / folder
        destination.mkdir(parents=True, exist_ok=True)
        names = ["repair.py", "audit_all.py"] if folder.startswith("experiments") else ["chainage.py"]
        for name in names:
            shutil.copy2(REPO / folder / name, destination / name)
    shutil.copy2(REPO / "configs/field_profiles/tbm9_2023.json", output / "code/original_field_profile.json")
    write_readme(output, summary, config)
    with (output / "MANIFEST.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["relative_path", "bytes", "sha256"])
        for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name != "MANIFEST.csv"):
            writer.writerow([path.relative_to(output).as_posix(), path.stat().st_size, sha(path)])
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def write_readme(output, summary, config):
    text = f"""# TBM9 PLC字段修复与统一命名

## 直接使用哪个文件夹

`data/`：{summary['output_files']}份UTF-8 BOM CSV，名称统一为`tbm_data_YYYYMMDD.csv`。
日期从原始记录时间戳确定，不使用下载日期、原文件编号或文件修改时间。
范围：{summary['first_date']}至{summary['last_date']}，共{summary['rows']:,}条采样；不是每个自然日都有数据。
三个原始目录没有改名、没有覆盖、没有删除。本目录为独立修复输出。

## 找回并实际采用的历史修复

- 使用已有审查版`{VERSION}`逐行判断电机、转速、气体局部布局，独立处理导航里程，不整行统一平移。
- 旧配置的日期切换点为`{config['rpm_switch_timestamp']}`，不是11月3日。本次日期只参与命名，不决定字段来源。
- 实际转速保存在`刀盘实际转速`；不能恢复的记录留空，不使用给定值、相邻值或速度/贯入度比值补造。
- 导向盾首/盾中里程使用原有独立布局修复规则；原始跳变没有插值或强行拉平。
- 气体修复仅说明局部结构与量化步长有支持，不确认ppm、%vol或%LEL，不作安全判断。
- 外围液压、冷却、润滑等未认证字段保持原始透传，不声称全字段物理身份都已确认。

## 原值、精度、行号

未改变字段的原始CSV字符串完整保留；已定位字段直接复制对应原始单元格字符串，不重算、不四舍五入。
`raw__字段名`保留被处理字段在原表头位置的原值；`mapping_source_row`是原CSV行号（表头为第1行，数据从第2行开始）。
`audit/provenance/`记录每行各修复字段的原始零基列索引，-1表示未确认；文件名日期通过命名清单关联原文件和SHA-256。
`quality_*`只记录既有口径下的负值、短间隔里程突跳、硬缺采等问题，不将缺失补成0。
异常负推力目前不能恢复真实值，保留并标记，不应解释为真实工程推力。
修复映射不等于传感器有效，既有速度、转速、贯入度质量筛选仍需在分析阶段使用。

## 仍然存在的限制

- 实际转速未确认：{summary['rpm_unresolved']:,}条。
- 气体块未确认：{summary['gas_unresolved']:,}条。
- 电机块未确认：{summary['motor_unresolved']:,}条。
- 导向里程缺失：{summary['navigation_missing']:,}条。
- 核心字段右移家族隔离：{summary['core_quarantined']:,}条。
这些计数可重叠，不能相加当成不同记录总数。不影响其他有独立依据的字段保留。

## 在现有分析程序中读取

务必使用本目录的`field_profile_repaired.json`，不要对这些CSV再次套用原始导航纠偏或按日期读取原`刀盘给定频率`列：

```sh
python run_analysis.py --plc-dir "{output / 'data'}" --field-profile "{output / 'field_profile_repaired.json'}" --out-dir outputs/repaired_standardized_new_analysis
```

配置让两个内部转速入口都读取已修复的`刀盘实际转速`，不会改写原`刀盘给定频率`字段，也不会修改冻结算法。
旧分析程序中的`rpm_source`显示名可能仍是旧入口名；真实来源以本包逐行溯源为准。
本轮只修复和导出采样数据，没有重新分段、生成日报或上传Drive。

## 核验

329份旧审计文件是否与本次源文件逐字节相同、修复有效数值是否保持历史审查版一致，见`SUMMARY.json`及逐日审计。
所有映射单元格、原值影子列、无关列、原始时间戳和行号均经过写出再读回校验。
冻结代码/配置与原始CSV均通过前后SHA-256检查；自动核验不代表全部工程字段获得现场认证。

复现入口在项目`experiments/plc_standardized_export_20261025/export.py`。`code/`是本次及依赖修复逻辑的证据快照，不是完整项目替代品。
"""
    (output / "先读我.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sources", type=Path, nargs="+", default=DEFAULT_ROOTS)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.output, args.sources, args.resume)
