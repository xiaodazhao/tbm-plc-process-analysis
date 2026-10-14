from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.config import DEFAULT_PLC_DIR, load_config
from tbm_process.ingestion.io import canonicalize, discover_files, read_daily_file, write_json
from tbm_process.orchestration.pipeline import analyze_day
from tbm_process.quality.preprocessing import derive_dataset_thresholds


REVIEW_DATES = (
    "2023-09-15",
    "2023-09-17",
    "2023-09-23",
    "2023-09-24",
    "2023-09-26",
    "2023-10-21",
    "2023-11-16",
    "2023-12-06",
    "2023-12-11",
    "2023-12-25",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen 10-day phase regression check.")
    parser.add_argument("--plc-dir", default=str(DEFAULT_PLC_DIR))
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    parser.add_argument(
        "--reference",
        default=str(PROJECT_ROOT / "docs/validation/frozen_v1_10day_review"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs_frozen_v1/regression_10day"),
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    reference = Path(args.reference)

    frames: dict[str, tuple[pd.DataFrame, Path]] = {}
    threshold_frames: list[pd.DataFrame] = []
    for path in discover_files(Path(args.plc_dir).expanduser()):
        canonical = canonicalize(read_daily_file(path), source_file=path)
        threshold_frames.append(canonical)
        valid_time = canonical["time"].dropna()
        if not valid_time.empty:
            frames[str(valid_time.iloc[0].date())] = (canonical, path)
    thresholds = derive_dataset_thresholds(threshold_frames, config)

    all_intervals: list[dict[str, Any]] = []
    all_phases: list[dict[str, Any]] = []
    point_differences: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    samples_dir = output / "processed_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    for date in REVIEW_DATES:
        canonical, path = frames[date]
        result = analyze_day(canonical, path=path, thresholds=thresholds, config=config)
        current = result["samples"].copy()
        current.to_csv(samples_dir / f"{date}.csv", index=False, encoding="utf-8-sig")
        all_intervals.extend(result["intervals"])
        all_phases.extend(result["phases"])

        previous = pd.read_csv(reference / "processed_samples" / f"{date}.csv")
        keys = ["source_file", "source_row"]
        columns = keys + ["valid_advance_interval", "interval_id", "phase_id", "phase_label"]
        comparison = previous[columns].merge(
            current[columns],
            on=keys,
            how="outer",
            suffixes=("_before", "_after"),
            indicator=True,
        )
        changed = pd.Series(False, index=comparison.index)
        for column in ("interval_id", "phase_id", "phase_label"):
            before = comparison[f"{column}_before"].fillna("<NA>").astype(str)
            after = comparison[f"{column}_after"].fillna("<NA>").astype(str)
            changed |= before.ne(after)
        differences = comparison[changed | comparison["_merge"].ne("both")].copy()
        differences.insert(0, "date", date)
        differences["core_cycle_sample"] = (
            differences["valid_advance_interval_before"].fillna(False).astype(bool)
            | differences["valid_advance_interval_after"].fillna(False).astype(bool)
        )
        differences["formal_label_or_cycle_changed"] = (
            differences["interval_id_before"].fillna("<NA>").astype(str).ne(
                differences["interval_id_after"].fillna("<NA>").astype(str)
            )
            | differences["phase_label_before"].fillna("<NA>").astype(str).ne(
                differences["phase_label_after"].fillna("<NA>").astype(str)
            )
        )
        point_differences.extend(differences.to_dict("records"))
        daily_rows.append(
            {
                "date": date,
                "sample_count": int(len(current)),
                "cycle_count": int(len(result["intervals"])),
                "stable_cycle_count": int(
                    sum(
                        item.get("segmentation_status") == "stable_phase_identified"
                        for item in result["intervals"]
                    )
                ),
                "point_assignment_difference_count": int(len(differences)),
                "core_cycle_point_difference_count": int(
                    (differences["core_cycle_sample"] & differences["formal_label_or_cycle_changed"]).sum()
                ),
            }
        )

    current_intervals = pd.DataFrame(all_intervals)
    current_phases = pd.DataFrame(all_phases)
    previous_intervals = pd.read_csv(reference / "推进循环.csv")
    previous_phases = pd.read_csv(reference / "四阶段明细.csv")
    previous_intervals = previous_intervals[previous_intervals["date"].astype(str).isin(REVIEW_DATES)]
    previous_phases = previous_phases[previous_phases["date"].astype(str).isin(REVIEW_DATES)]

    cycle_differences = _table_differences(
        previous_intervals,
        current_intervals,
        keys=["interval_id"],
        values=[
            "start_time",
            "end_time",
            "stable_start_time",
            "stable_end_time",
            "stable_phase_start_time",
            "stable_phase_end_time",
            "segmentation_status",
        ],
    )
    phase_differences = _table_differences(
        previous_phases,
        current_phases,
        keys=["interval_id", "phase_label"],
        values=["start_time", "end_time", "source_row_start", "source_row_end", "sample_count"],
    )

    point_frame = pd.DataFrame(point_differences)
    cycle_differences.to_csv(output / "cycle_differences.csv", index=False, encoding="utf-8-sig")
    phase_differences.to_csv(output / "phase_boundary_differences.csv", index=False, encoding="utf-8-sig")
    point_frame.to_csv(output / "point_assignment_differences.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(daily_rows).to_csv(output / "daily_regression_summary.csv", index=False, encoding="utf-8-sig")
    current_intervals.to_csv(output / "advance_intervals.csv", index=False, encoding="utf-8-sig")
    current_phases.to_csv(output / "phases.csv", index=False, encoding="utf-8-sig")

    core_point_changes = int(
        (
            point_frame.get("core_cycle_sample", pd.Series(dtype=bool))
            & point_frame.get("formal_label_or_cycle_changed", pd.Series(dtype=bool))
        ).sum()
    )
    core_boundary_changes = int(
        cycle_differences.get("field", pd.Series(dtype=str)).isin(
            [
                "start_time",
                "end_time",
                "stable_start_time",
                "stable_end_time",
                "stable_phase_start_time",
                "stable_phase_end_time",
                "segmentation_status",
            ]
        ).sum()
    )
    report = {
        "method_version": config["phase_segmentation_method_version"],
        "review_dates": list(REVIEW_DATES),
        "reference_material": str(reference.resolve()),
        "researcher_visual_review_statement": "研究人员对10天样本曲线进行了目视合理性检查，未发现明显不合理分段。",
        "quantitative_accuracy_claimed": False,
        "cycle_or_stable_boundary_difference_count": core_boundary_changes,
        "point_assignment_difference_count": int(len(point_frame)),
        "core_cycle_point_difference_count": core_point_changes,
        "context_only_point_difference_count": int(len(point_frame) - core_point_changes),
        "status": "passed" if core_boundary_changes == 0 and core_point_changes == 0 else "review_required",
        "interpretation": (
            "Only non-core context ownership changes are permitted after the ownership fix."
        ),
    }
    write_json(output / "regression_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _table_differences(
    before: pd.DataFrame,
    after: pd.DataFrame,
    *,
    keys: list[str],
    values: list[str],
) -> pd.DataFrame:
    merged = before[keys + values].merge(
        after[keys + values],
        on=keys,
        how="outer",
        suffixes=("_before", "_after"),
        indicator=True,
    )
    rows: list[dict[str, Any]] = []
    for row in merged.to_dict("records"):
        if row["_merge"] != "both":
            rows.append({**{key: row.get(key) for key in keys}, "field": "row_presence", "before": row["_merge"], "after": row["_merge"]})
            continue
        for field in values:
            left = row.get(f"{field}_before")
            right = row.get(f"{field}_after")
            if _equal(left, right):
                continue
            rows.append(
                {
                    **{key: row.get(key) for key in keys},
                    "field": field,
                    "before": left,
                    "after": right,
                }
            )
    return pd.DataFrame(rows, columns=keys + ["field", "before", "after"])


def _equal(left: Any, right: Any) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    try:
        return bool(np.isclose(float(left), float(right), rtol=0, atol=1e-9))
    except (TypeError, ValueError):
        pass
    return str(left) == str(right)


if __name__ == "__main__":
    main()
