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

from tbm_process.dynamics.descriptors import PARAMETERS as DYNAMIC_PARAMETERS
from tbm_process.dynamics.descriptors import trend_descriptor
from tbm_process.ingestion.io import write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate frozen TBM PLC analysis outputs.")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "outputs_frozen_v1"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    manifest = _read_json(out_dir / "method_manifest.json")
    summary = _read_json(out_dir / "analysis_summary.json")
    config = manifest["config"]
    intervals = _read_csv(out_dir / "advance_intervals.csv")
    phases = _read_csv(out_dir / "phases.csv")
    scopes = _read_csv(out_dir / "analysis_scopes.csv")
    segments = _read_csv(out_dir / "merged_semantic_segments.csv")
    coupled = _read_csv(out_dir / "coupled_parameter_events.csv")
    samples = _load_samples(out_dir)

    checks = {
        "execution": _validate_execution(summary, samples),
        "ownership_and_phase_summary": _validate_ownership(samples, phases, config),
        "time_continuity_and_duration": _validate_time(samples, intervals, phases, segments),
        "analysis_scope_coverage": _validate_scope_coverage(samples, scopes),
        "semantic_recomputation": _validate_semantics(samples, segments, config),
        "coupled_event_recomputation": _validate_coupled_events(samples, coupled),
        "traceability": _validate_traceability(samples, phases, scopes, segments, coupled),
        "quality_isolation": _validate_quality_isolation(samples),
        "fact_package_completeness": _validate_fact_packages(out_dir, intervals, segments),
    }
    regression_path = out_dir / "regression_10day/regression_report.json"
    regression = _read_json(regression_path) if regression_path.exists() else {
        "status": "not_run",
        "passed": False,
    }
    automated_passed = all(bool(item["passed"]) for item in checks.values())
    regression_passed = regression.get("status") == "passed"
    report = {
        "method_version": manifest["method_version"],
        "validation_scope": {
            "day_count": int(len(samples)),
            "row_count": int(sum(len(frame) for frame in samples.values())),
            "human_ground_truth_available": False,
            "interpretation": "internal_consistency_and_rule_verification_not_phase_accuracy",
        },
        "program_execution": {
            "status": "passed" if checks["execution"]["passed"] else "failed",
        },
        "data_processing_consistency": {
            "status": "passed" if automated_passed else "failed",
        },
        "automatic_rule_verification": {
            "status": "passed" if automated_passed and regression_passed else "failed",
            "note": "Verifies frozen-rule reproducibility, ownership, continuity and arithmetic; it is not an accuracy metric.",
        },
        "human_visual_reasonableness_check": {
            "status": "completed_for_10_days",
            "statement": "研究人员对10天样本曲线进行了目视合理性检查，未发现明显不合理分段。",
        },
        "formal_quantitative_accuracy": {
            "status": "not_performed",
            "accuracy_claimed": False,
            "reason": "No formal phase ground-truth annotation is available yet.",
        },
        "checks": checks,
        "frozen_10day_regression": regression,
        "overall_status": "passed" if automated_passed and regression_passed else "failed",
        "remaining_limits": manifest["known_limits"],
    }
    write_json(out_dir / "validation_report.json", report)
    _write_chinese_note(out_dir / "验证说明.md", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["overall_status"] != "passed":
        raise SystemExit(1)


def _validate_execution(summary: dict[str, Any], samples: dict[str, pd.DataFrame]) -> dict[str, Any]:
    expected = 91
    passed = int(summary.get("file_count", 0)) == expected and len(samples) == expected
    return {
        "expected_file_count": expected,
        "summary_file_count": int(summary.get("file_count", 0)),
        "processed_day_count": int(len(samples)),
        "passed": passed,
    }


def _validate_ownership(
    samples: dict[str, pd.DataFrame],
    phases: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    duplicate_source_rows = 0
    phase_without_interval = 0
    for frame in samples.values():
        duplicate_source_rows += int(frame.duplicated(["source_file", "source_row"]).sum())
        assigned = frame[frame["phase_id"].notna()]
        phase_without_interval += int(assigned["interval_id"].isna().sum())

    table_mismatches = 0
    maximum_numeric_error = 0.0
    for row in phases.to_dict("records"):
        frame = samples[str(row["date"])]
        block = frame[frame["phase_id"].eq(row["phase_id"])]
        if block.empty:
            table_mismatches += 1
            continue
        expected = {
            "interval_id": str(block["interval_id"].iloc[0]),
            "phase_label": str(block["phase_label"].iloc[0]),
            "start_time": pd.Timestamp(block["time"].iloc[0]).isoformat(),
            "end_time": pd.Timestamp(block["time"].iloc[-1]).isoformat(),
            "source_row_start": int(block["source_row"].iloc[0]),
            "source_row_end": int(block["source_row"].iloc[-1]),
            "sample_count": int(len(block)),
            "duration_seconds": _phase_duration(block, float(config["maximum_sample_duration_seconds"])),
        }
        row_failed = False
        for key in ("interval_id", "phase_label", "start_time", "end_time"):
            row_failed |= str(row[key]) != str(expected[key])
        for key in ("source_row_start", "source_row_end", "sample_count"):
            row_failed |= int(row[key]) != int(expected[key])
        duration_error = abs(float(row["duration_seconds"]) - expected["duration_seconds"])
        maximum_numeric_error = max(maximum_numeric_error, duration_error)
        row_failed |= duration_error > 1e-7
        for parameter in ("advance_speed", "thrust", "torque"):
            actual = float(row[f"{parameter}_p50"])
            values = pd.to_numeric(block[parameter], errors="coerce").dropna()
            target = float(values.median()) if not values.empty else np.nan
            error = abs(actual - target)
            maximum_numeric_error = max(maximum_numeric_error, error)
            row_failed |= not np.isclose(actual, target, rtol=1e-9, atol=1e-9, equal_nan=True)
        table_mismatches += int(row_failed)

    overlap_count = 0
    for _, day in phases.groupby("date"):
        rows = day.sort_values(["source_row_start", "source_row_end"]).to_dict("records")
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if int(right["source_row_start"]) > int(left["source_row_end"]):
                    break
                if left["interval_id"] != right["interval_id"]:
                    overlap_count += 1
    return {
        "duplicate_source_record_count": duplicate_source_rows,
        "phase_without_interval_count": phase_without_interval,
        "cross_cycle_phase_range_overlap_count": overlap_count,
        "phase_table_point_mismatch_count": table_mismatches,
        "maximum_recomputation_error": maximum_numeric_error,
        "passed": duplicate_source_rows == 0
        and phase_without_interval == 0
        and overlap_count == 0
        and table_mismatches == 0,
    }


def _validate_time(
    samples: dict[str, pd.DataFrame],
    intervals: pd.DataFrame,
    phases: pd.DataFrame,
    segments: pd.DataFrame,
) -> dict[str, Any]:
    del intervals, phases
    pre_gap_nonzero = 0
    cycle_crossing = 0
    phase_crossing = 0
    segment_crossing = 0
    for frame in samples.values():
        hard_next = frame["hard_gap_before"].shift(-1, fill_value=False).astype(bool)
        pre_gap_nonzero += int(frame.loc[hard_next, "sample_dt_seconds"].fillna(0).ne(0).sum())
        for _, group in frame[frame["interval_id"].notna()].groupby("interval_id"):
            cycle_crossing += int(group["continuity_block_id"].nunique() > 1)
        for _, group in frame[frame["phase_id"].notna()].groupby("phase_id"):
            phase_crossing += int(group["continuity_block_id"].nunique() > 1)
    for row in segments.to_dict("records"):
        block = _trace_block(samples, row, "analysis_scope_id")
        segment_crossing += int(not block.empty and block["continuity_block_id"].nunique() > 1)
    return {
        "pre_gap_sample_with_nonzero_duration_count": pre_gap_nonzero,
        "cycle_crossing_hard_gap_count": cycle_crossing,
        "phase_crossing_hard_gap_count": phase_crossing,
        "semantic_segment_crossing_hard_gap_count": segment_crossing,
        "passed": pre_gap_nonzero == 0
        and cycle_crossing == 0
        and phase_crossing == 0
        and segment_crossing == 0,
    }


def _validate_scope_coverage(
    samples: dict[str, pd.DataFrame],
    scopes: pd.DataFrame,
) -> dict[str, Any]:
    accepted = 0
    covered = 0
    duplicate_scope_ids = int(scopes["phase_id"].duplicated().sum())
    for frame in samples.values():
        core = frame["valid_advance_interval"].fillna(False).astype(bool)
        accepted += int(core.sum())
        covered += int(frame.loc[core, "analysis_scope_id"].notna().sum())
    return {
        "accepted_cycle_sample_count": accepted,
        "accepted_cycle_sample_with_scope_count": covered,
        "duplicate_analysis_scope_id_count": duplicate_scope_ids,
        "passed": accepted == covered and duplicate_scope_ids == 0,
    }


def _validate_semantics(
    samples: dict[str, pd.DataFrame],
    segments: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    checked = 0
    mismatches = 0
    maximum_error = 0.0
    for row in segments.to_dict("records"):
        block = _trace_block(samples, row, "analysis_scope_id")
        column = DYNAMIC_PARAMETERS[str(row["parameter"])]
        valid = block[["time", column]].dropna()
        if len(valid) < int(config["minimum_dynamic_samples"]):
            continue
        expected = trend_descriptor(valid["time"], valid[column], config)
        checked += 1
        failed = False
        for metric in ("median", "slope_per_minute", "absolute_change"):
            actual = row.get(metric)
            target = expected.get(metric)
            if pd.isna(actual) and target is None:
                continue
            error = abs(float(actual) - float(target))
            maximum_error = max(maximum_error, error)
            failed |= error > 1e-7 * max(abs(float(target)), 1.0)
        mismatches += int(failed)
    return {
        "semantic_segment_count_checked": checked,
        "recomputation_mismatch_count": mismatches,
        "maximum_absolute_error": maximum_error,
        "passed": mismatches == 0,
    }


def _validate_traceability(
    samples: dict[str, pd.DataFrame],
    phases: pd.DataFrame,
    scopes: pd.DataFrame,
    segments: pd.DataFrame,
    coupled: pd.DataFrame,
) -> dict[str, Any]:
    missing = 0
    checked = 0
    for table, id_column in (
        (phases, "phase_id"),
        (scopes, "analysis_scope_id"),
        (segments, "analysis_scope_id"),
        (coupled, "analysis_scope_id"),
    ):
        for row in table.to_dict("records"):
            checked += 1
            block = _trace_block(samples, row, id_column)
            if block.empty:
                missing += 1
                continue
            missing += int(int(row["source_row_start"]) != int(block["source_row"].iloc[0]))
            missing += int(int(row["source_row_end"]) != int(block["source_row"].iloc[-1]))
    return {
        "traceable_record_count_checked": checked,
        "missing_or_boundary_mismatch_count": missing,
        "passed": missing == 0,
    }


def _validate_coupled_events(
    samples: dict[str, pd.DataFrame],
    coupled: pd.DataFrame,
) -> dict[str, Any]:
    checked = 0
    mismatches = 0
    maximum_error = 0.0
    columns = {
        "advance_speed": "advance_speed",
        "thrust": "thrust",
        "torque": "torque",
        "rpm": "rpm_canonical",
    }
    for row in coupled.to_dict("records"):
        block = _trace_block(samples, row, "analysis_scope_id")
        for parameter, column in columns.items():
            field = f"{parameter}_median"
            actual = row.get(field)
            if actual is None or pd.isna(actual):
                continue
            target = pd.to_numeric(block[column], errors="coerce").median()
            checked += 1
            error = abs(float(actual) - float(target))
            maximum_error = max(maximum_error, error)
            mismatches += int(not np.isclose(actual, target, rtol=1e-9, atol=1e-9))
    return {
        "parameter_state_count_checked": checked,
        "recomputation_mismatch_count": mismatches,
        "maximum_absolute_error": maximum_error,
        "passed": mismatches == 0,
    }


def _validate_quality_isolation(samples: dict[str, pd.DataFrame]) -> dict[str, Any]:
    bad_speed_in_analysis = 0
    bad_penetration_raw_in_analysis = 0
    for frame in samples.values():
        bad_speed = frame["advance_speed_quality"].ne("usable")
        bad_speed_in_analysis += int(frame.loc[bad_speed, "advance_speed"].notna().sum())
        suspicious = frame["penetration_quality"].isin(
            ["suspicious_linked_speed", "suspicious_reference_mismatch"]
        )
        raw_selected = frame["penetration_analysis_source"].eq("penetration_raw_quality_controlled")
        bad_penetration_raw_in_analysis += int((suspicious & raw_selected).sum())
    return {
        "unusable_speed_entering_analysis_count": bad_speed_in_analysis,
        "suspicious_raw_penetration_entering_analysis_count": bad_penetration_raw_in_analysis,
        "passed": bad_speed_in_analysis == 0 and bad_penetration_raw_in_analysis == 0,
    }


def _validate_fact_packages(
    out_dir: Path,
    intervals: pd.DataFrame,
    segments: pd.DataFrame,
) -> dict[str, Any]:
    full_days = sorted((out_dir / "structured_full_days").glob("*.json"))
    compact_days = sorted((out_dir / "structured_days").glob("*.json"))
    cycle_count = 0
    for path in full_days:
        cycle_count += len(_read_json(path).get("excavation_cycles", []))
    duplicate_segment_ids = int(segments["semantic_segment_id"].duplicated().sum())
    return {
        "complete_fact_package_day_count": len(full_days),
        "compact_fact_package_day_count": len(compact_days),
        "accepted_cycle_count": int(len(intervals)),
        "cycles_in_complete_fact_packages": int(cycle_count),
        "duplicate_semantic_segment_id_count": duplicate_segment_ids,
        "passed": len(full_days) == 91
        and len(compact_days) == 91
        and cycle_count == len(intervals)
        and duplicate_segment_ids == 0,
    }


def _trace_block(
    samples: dict[str, pd.DataFrame],
    row: dict[str, Any],
    id_column: str,
) -> pd.DataFrame:
    frame = samples.get(str(row.get("date")))
    if frame is None:
        return pd.DataFrame()
    start = pd.Timestamp(row["start_time"])
    end = pd.Timestamp(row["end_time"])
    mask = frame["time"].between(start, end)
    identifier = row.get("phase_id")
    if id_column in frame and identifier is not None:
        mask &= frame[id_column].eq(identifier)
    return frame[mask]


def _phase_duration(block: pd.DataFrame, maximum: float) -> float:
    observed = (block["time"].iloc[-1] - block["time"].iloc[0]).total_seconds()
    differences = block["time"].diff().dt.total_seconds().dropna()
    tail = float(differences.median()) if not differences.empty else 0.0
    return float(observed + min(max(tail, 0.0), maximum))


def _load_samples(out_dir: Path) -> dict[str, pd.DataFrame]:
    result = {}
    for path in sorted((out_dir / "processed_samples").glob("*.csv")):
        result[path.stem] = pd.read_csv(path, parse_dates=["time"])
    return result


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_chinese_note(path: Path, report: dict[str, Any]) -> None:
    checks = report["checks"]
    text = f"""# 冻结版验证说明

## 结论

- 程序运行：{report['program_execution']['status']}
- 数据处理一致性：{report['data_processing_consistency']['status']}
- 冻结规则自动核验：{report['automatic_rule_verification']['status']}
- 10天回归检查：{report['frozen_10day_regression'].get('status')}
- 正式定量准确率：未开展

研究人员对10天样本曲线进行了目视合理性检查，未发现明显不合理分段。

这句话只表示样本曲线的目视合理性检查，不表示阶段识别准确率达到100%，也不替代人工标注后的准确率、召回率或边界误差计算。

## 自动检查

- 处理天数：{report['validation_scope']['day_count']}；记录数：{report['validation_scope']['row_count']}。
- 跨循环阶段范围重叠：{checks['ownership_and_phase_summary']['cross_cycle_phase_range_overlap_count']}。
- 阶段表与逐点表不一致：{checks['ownership_and_phase_summary']['phase_table_point_mismatch_count']}。
- 循环、阶段、动态语义跨硬缺口：{checks['time_continuity_and_duration']['cycle_crossing_hard_gap_count']} / {checks['time_continuity_and_duration']['phase_crossing_hard_gap_count']} / {checks['time_continuity_and_duration']['semantic_segment_crossing_hard_gap_count']}。
- 硬缺口前仍计入时长的记录：{checks['time_continuity_and_duration']['pre_gap_sample_with_nonzero_duration_count']}。
- 有效循环样本进入动态分析范围：{checks['analysis_scope_coverage']['accepted_cycle_sample_with_scope_count']} / {checks['analysis_scope_coverage']['accepted_cycle_sample_count']}。
- 合并动态语义独立复算不一致：{checks['semantic_recomputation']['recomputation_mismatch_count']}。
- 多参数事件参数状态复算不一致：{checks['coupled_event_recomputation']['recomputation_mismatch_count']}。
- 无法追溯或边界不一致：{checks['traceability']['missing_or_boundary_mismatch_count']}。

## 仍未解决

1. 尚无正式人工阶段真值，因此不能报告定量识别准确率。
2. 刀盘转速统一映射仍是待PLC点位表核实的暂定映射。
3. 约20秒采样可能遗漏非常短的空转、空推或快速升降过程。
4. 参数共同变化只表示机器响应，不能自动解释为地质恶化、刀具磨损、卡机或设备故障。
"""
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
