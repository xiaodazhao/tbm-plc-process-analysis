from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..config import ARCHITECTURE_VERSION, PROJECT_ROOT
from ..ingestion.io import write_csv, write_json


FROZEN_SOURCE_FILES = [
    "config.json",
    "configs/field_profiles/tbm9_2023.json",
    "configs/dynamic_parameters.json",
    "tbm_process/config.py",
    "tbm_process/contracts/samples.py",
    "tbm_process/ingestion/io.py",
    "tbm_process/ingestion/profiles.py",
    "tbm_process/quality/preprocessing.py",
    "tbm_process/process/segmentation.py",
    "tbm_process/process/scopes.py",
    "tbm_process/dynamics/descriptors.py",
    "tbm_process/dynamics/registry.py",
    "tbm_process/dynamics/semantics.py",
    "tbm_process/facts/builders.py",
    "tbm_process/orchestration/pipeline.py",
    "tbm_process/outputs/writer.py",
]


SAMPLE_COLUMNS = [
    "time",
    "source_file",
    "source_row",
    "raw_state",
    "chainage",
    "chainage_raw",
    "chainage_canonical_candidate",
    "chainage_middle_canonical",
    "chainage_mapping_mode",
    "chainage_source_field",
    "chainage_middle_source_field",
    "chainage_quality",
    "chainage_mapping_version",
    "chainage_step_gap_seconds",
    "chainage_step_delta_m",
    "chainage_step_quality",
    "cumulative_advance",
    "advance_speed_raw",
    "advance_speed",
    "advance_speed_quality",
    "advance_speed_set",
    "gap_before_seconds",
    "sample_dt_seconds",
    "hard_gap_before",
    "continuity_block_id",
    "thrust",
    "torque",
    "rpm_actual_raw",
    "rpm_alternate_raw",
    "rpm_canonical_candidate",
    "rpm_canonical",
    "rpm_source",
    "rpm_quality",
    "rpm_mapping_version",
    "rpm_mapping_status",
    "rpm_mapping_evidence",
    "motor_frequency_m1",
    "penetration_raw",
    "penetration_reference",
    "penetration_reference_relative_error",
    "penetration_quality",
    "penetration_analysis",
    "penetration_analysis_source",
    "machine_state",
    "excavation_cycle_candidate",
    "valid_advance_interval",
    "interval_id",
    "phase_id",
    "phase_label",
    "analysis_scope_id",
    "analysis_scope_label",
]


def write_outputs(result: dict[str, Any], out_dir: Path, *, plot_dates: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "analysis_summary.json", result["summary"])
    write_json(out_dir / "method_manifest.json", _method_manifest(result))
    write_csv(out_dir / "daily_overview.csv", [_flatten_daily(item) for item in result["daily_results"]])
    write_csv(out_dir / "advance_intervals.csv", result["advance_intervals"])
    write_csv(out_dir / "phases.csv", result["phases"])
    write_csv(out_dir / "analysis_scopes.csv", result["analysis_scopes"])
    write_csv(out_dir / "stable_phase_candidates.csv", result["phase_candidates"])
    write_csv(out_dir / "phase_descriptors.csv", result["phase_descriptors"])
    write_csv(out_dir / "semantic_window_descriptors.csv", result["semantic_windows"])
    write_csv(out_dir / "merged_semantic_segments.csv", result["semantic_segments"])
    write_csv(out_dir / "selected_semantic_segments.csv", result["selected_semantic_segments"])
    write_csv(out_dir / "trend_transitions.csv", result["trend_transitions"])
    write_csv(out_dir / "local_outliers.csv", result["local_outliers"])
    write_csv(
        out_dir / "coupled_parameter_events.csv",
        [_flatten_parameter_states(item) for item in result["coupled_parameter_events"]],
    )
    write_csv(
        out_dir / "selected_coupled_parameter_events.csv",
        [_flatten_parameter_states(item) for item in result["selected_coupled_parameter_events"]],
    )
    write_csv(out_dir / "construction_events.csv", [_flatten_event(item) for item in result["construction_events"]])
    write_csv(
        out_dir / "construction_event_candidates.csv",
        [_flatten_event(item) for item in result["construction_event_candidates"]],
    )
    write_csv(out_dir / "data_quality_events.csv", result["quality_events"])
    write_csv(out_dir / "preprocessing_validation.csv", result["preprocessing_validation"])
    structured_dir = out_dir / "structured_days"
    for item in result["daily_results"]:
        write_json(structured_dir / f"{item['date']}.json", item)
    full_structured_dir = out_dir / "structured_full_days"
    for item in result["complete_daily_results"]:
        write_json(full_structured_dir / f"{item['date']}.json", item)
    samples_dir = out_dir / "processed_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    for frame in result["processed_samples"]:
        if frame.empty:
            continue
        date = str(frame["time"].dropna().iloc[0].date()) if frame["time"].notna().any() else "unknown"
        frame[[column for column in SAMPLE_COLUMNS if column in frame.columns]].to_csv(
            samples_dir / f"{date}.csv",
            index=False,
            encoding="utf-8-sig",
        )
    write_readme(out_dir, result)
    write_charts(out_dir / "charts", result["processed_samples"], plot_dates)


def write_readme(out_dir: Path, result: dict[str, Any]) -> None:
    summary = result["summary"]
    text = f"""# PLC construction-process analysis output

This directory is generated by frozen method `tbm_four_phase_v1_2026-10-12` without invoking a report or LLM pipeline.

## Run summary

- Files: {summary['file_count']}
- Rows: {summary['row_count']}
- Four-phase excavation-cycle candidates: {summary['advance_interval_count']}
- Candidate phases: {summary['phase_count']}
- Construction-response events: {summary['construction_response_event_count']}
- Data-quality events: {summary['data_quality_event_count']}

## Important limits

- `rpm_canonical` is a provisional mapping. Confirm it against the PLC point table before treating it as ground truth.
- Extreme speed values are excluded rather than imputed.
- Free rotation and free advancing are merged into `startup` for the approximately 20-second sampling interval.
- Stable phases require low slope, low variance, and a minimum duration; cycles without that evidence remain unsegmented.
- Unsegmented cycles still receive interval-level dynamic analysis.
- Ten reviewed sample days passed a researcher visual reasonableness check. This is not a quantitative accuracy result.
- Phase labels and construction events remain rule-based research outputs pending formal annotation-based accuracy evaluation.
- Construction-response events do not diagnose geology or equipment faults.

## Main files

- `daily_overview.csv`: one row per day.
- `advance_intervals.csv`: every accepted four-phase excavation-cycle candidate and its segmentation status.
- `phases.csv`: candidate phases inside advancing intervals.
- `analysis_scopes.csv`: complete dynamic-analysis coverage, including unidentified portions between phases.
- `stable_phase_candidates.csv`: selected and unselected stable-platform evidence.
- `phase_descriptors.csv`: trend and fluctuation descriptors.
- `semantic_window_descriptors.csv`: phase-bounded calculation windows, including stable windows.
- `merged_semantic_segments.csv`: complete continuous parameter semantics recomputed from merged samples.
- `selected_semantic_segments.csv`: compact deterministic facts intended for later report-model input.
- `trend_transitions.csv`: refined changes between adjacent semantic segments.
- `local_outliers.csv`: local time-series deviations kept separate from data-quality events.
- `coupled_parameter_events.csv`: overlapping multi-parameter machine-response facts.
- `selected_coupled_parameter_events.csv`: compact coupled-response facts; full events remain available.
- `construction_events.csv`: traceable machine-response events.
- `construction_event_candidates.csv`: unfiltered dynamic candidates retained for method review.
- `data_quality_events.csv`: data-quality events kept separate from construction events.
- `preprocessing_validation.csv`: daily RPM-mapping and speed-distance validation evidence.
- `structured_days/*.json`: compact daily fact package intended for later constrained LLM input.
- `structured_full_days/*.json`: unfiltered traceable fact package containing every accepted cycle.
- `processed_samples/*.csv`: normalized sample-level data with source rows.
- `method_manifest.json`: all thresholds, field mappings, and derived bounds.
- `validation_report.json`: full-dataset continuity, ownership, traceability, anomaly-isolation, and recomputation checks.
- `验证说明.md`: Chinese explanation of what passed and what has not yet been validated.
- `visualizations/`: complete-day and representative-cycle figures for manual review.
"""
    (out_dir / "README.md").write_text(text, encoding="utf-8")


def _method_manifest(result: dict[str, Any]) -> dict[str, Any]:
    config = result["config"]
    config_bytes = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checksums = {}
    for relative in FROZEN_SOURCE_FILES:
        path = PROJECT_ROOT / relative
        if path.exists():
            checksums[relative] = _sha256(path)
    return {
        "schema_version": result["schema_version"],
        "software_architecture_version": ARCHITECTURE_VERSION,
        "method_name": "TBM PLC four-phase construction-process analysis",
        "method_version": config["phase_segmentation_method_version"],
        "method_status": config["phase_segmentation_method_status"],
        "frozen_date": "2026-10-12",
        "data_quality_method_version": config["data_quality_method_version"],
        "read_only_source": result["read_only_source"],
        "report_or_llm_pipeline_called": result["report_or_llm_pipeline_called"],
        "sampling_condition": "typically_about_20_seconds_with_irregular_gaps",
        "phase_fields": ["thrust", "torque", "penetration_analysis"],
        "supporting_fields": [
            "time",
            "raw_state",
            "advance_speed",
            "rpm_canonical",
            "chainage",
        ],
        "manual_review_record": "研究人员对10天样本曲线进行了目视合理性检查，未发现明显不合理分段。",
        "accuracy_statement": "No formal annotation-based quantitative accuracy has been calculated.",
        "known_limits": [
            "approximately_20_second_sampling_can_hide_short_free_rotation_or_free_advancing_states",
            "cycles_without_reliable_stable_evidence_are_not_forced_into_four_phases",
            "rpm_canonical_mapping_is_provisional_pending_plc_point_table_confirmation",
            "construction_response_events_do_not_diagnose_geology_wear_jamming_or_faults",
        ],
        "config": config,
        "config_canonical_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "source_sha256": checksums,
        "derived_thresholds": result["derived_thresholds"],
        "resolved_fields": result["resolved_fields"],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_charts(charts_dir: Path, frames: list[pd.DataFrame], plot_dates: list[str]) -> None:
    charts_dir.mkdir(parents=True, exist_ok=True)
    requested = set(plot_dates)
    for frame in frames:
        if frame.empty or not frame["time"].notna().any():
            continue
        date = str(frame["time"].dropna().iloc[0].date())
        if requested and date not in requested:
            continue
        fig, axes = plt.subplots(4, 1, figsize=(16, 10), sharex=True, constrained_layout=True)
        series = [
            ("advance_speed", "Advance speed"),
            ("thrust", "Thrust"),
            ("torque", "Cutterhead torque"),
            ("rpm_canonical", "Canonical RPM"),
        ]
        for axis, (column, label) in zip(axes, series, strict=True):
            axis.plot(frame["time"], frame[column], linewidth=0.8, color="#1f5f99")
            invalid = frame["advance_speed_quality"].eq("invalid_extreme") if column == "advance_speed" else pd.Series(False, index=frame.index)
            if invalid.any():
                axis.scatter(frame.loc[invalid, "time"], frame.loc[invalid, "advance_speed_raw"], s=8, color="#c73e1d", label="excluded raw speed")
                axis.legend(loc="upper right", fontsize=8)
            _shade_phases(axis, frame)
            axis.set_ylabel(label)
            axis.grid(alpha=0.2)
        axes[0].set_title(f"{date} normalized PLC signals and candidate phases")
        axes[-1].set_xlabel("Time")
        fig.savefig(charts_dir / f"{date}.png", dpi=150)
        plt.close(fig)


def _shade_phases(axis: Any, frame: pd.DataFrame) -> None:
    colors = {
        "startup": "#f2c14e",
        "rising": "#7fc8a9",
        "stable": "#8ecae6",
        "declining": "#e07a5f",
    }
    valid = frame[frame["phase_label"].notna()]
    if valid.empty:
        return
    groups = valid["phase_label"].ne(valid["phase_label"].shift()).cumsum()
    for _, block in valid.groupby(groups, sort=False):
        label = str(block["phase_label"].iloc[0])
        axis.axvspan(block["time"].iloc[0], block["time"].iloc[-1], color=colors.get(label, "#cccccc"), alpha=0.10)


def _flatten_daily(item: dict[str, Any]) -> dict[str, Any]:
    overall = item["overall"]
    row = {"date": item["date"], "source_file": item["source_file"]}
    for key, value in overall.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                row[f"{key}_{subkey}"] = subvalue if not isinstance(subvalue, dict) else str(subvalue)
        else:
            row[key] = value
    row["construction_response_event_count"] = len(item["construction_response_events"])
    row["data_quality_event_count"] = len(item["data_quality_events"])
    return row


def _flatten_event(item: dict[str, Any]) -> dict[str, Any]:
    row = {key: value for key, value in item.items() if key != "parameter_changes"}
    changes = item.get("parameter_changes", {})
    for name, value in changes.items():
        for metric, metric_value in value.items():
            row[f"{name}_{metric}"] = metric_value
    return row


def _flatten_parameter_states(item: dict[str, Any]) -> dict[str, Any]:
    row = {key: value for key, value in item.items() if key != "parameter_states"}
    for name, value in item.get("parameter_states", {}).items():
        for metric, metric_value in value.items():
            row[f"{name}_{metric}"] = metric_value
    return row

