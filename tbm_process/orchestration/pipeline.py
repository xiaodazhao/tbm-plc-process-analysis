from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..contracts.samples import (
    validate_canonical_samples,
    validate_process_samples,
    validate_quality_controlled_samples,
)
from ..config import layer_config
from ..dynamics.descriptors import analyze_phases, select_important_events
from ..dynamics.semantics import analyze_phase_semantics, select_semantic_facts
from ..facts.builders import build_complete_daily_result, build_daily_result
from ..ingestion.io import canonicalize, discover_files, iso, read_daily_file, safe_number
from ..process.segmentation import (
    PARAMETERS,
    classify_machine_state,
    extract_advance_intervals,
    rebuild_phase_rows_from_assignments,
    segment_interval_phases,
)
from ..process.scopes import build_analysis_scopes
from ..quality.preprocessing import (
    derive_dataset_thresholds,
    preprocess_day,
    validate_day_consistency,
)


def run_dataset(
    *,
    plc_dir: Path,
    config: dict[str, Any],
    max_files: int | None = None,
    field_aliases: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    paths = discover_files(plc_dir)
    if max_files is not None:
        paths = paths[: max(max_files, 0)]
    days: list[pd.DataFrame] = []
    resolved_rows = []
    for path in paths:
        raw = read_daily_file(path)
        canonical = canonicalize(raw, source_file=path, field_aliases=field_aliases)
        validate_canonical_samples(canonical)
        resolved_rows.append(
            {
                "date": _date(canonical, path),
                "source_file": path.name,
                **{f"field_{key}": value for key, value in canonical.attrs["resolved_columns"].items()},
                "row_count": int(len(canonical)),
            }
        )
        days.append(canonical)
    thresholds = derive_dataset_thresholds(days, layer_config(config, "quality"))
    daily_results = []
    complete_daily_results = []
    all_intervals: list[dict[str, Any]] = []
    all_phases: list[dict[str, Any]] = []
    all_descriptors: list[dict[str, Any]] = []
    all_construction_events: list[dict[str, Any]] = []
    all_construction_event_candidates: list[dict[str, Any]] = []
    all_quality_events: list[dict[str, Any]] = []
    all_validation: list[dict[str, Any]] = []
    all_phase_candidates: list[dict[str, Any]] = []
    all_semantic_windows: list[dict[str, Any]] = []
    all_semantic_segments: list[dict[str, Any]] = []
    all_trend_transitions: list[dict[str, Any]] = []
    all_local_outliers: list[dict[str, Any]] = []
    all_coupled_events: list[dict[str, Any]] = []
    all_analysis_scopes: list[dict[str, Any]] = []
    all_selected_semantic_segments: list[dict[str, Any]] = []
    all_selected_coupled_events: list[dict[str, Any]] = []
    processed_samples: list[pd.DataFrame] = []
    for frame, path in zip(days, paths, strict=True):
        result = analyze_day(frame, path=path, thresholds=thresholds, config=config)
        daily_results.append(result["structured"])
        complete_daily_results.append(result["complete_structured"])
        all_intervals.extend(result["intervals"])
        all_phases.extend(result["phases"])
        all_descriptors.extend(result["descriptors"])
        all_construction_events.extend(result["construction_events"])
        all_construction_event_candidates.extend(result["construction_event_candidates"])
        all_quality_events.extend(result["quality_events"])
        all_validation.append(result["preprocessing_validation"])
        all_phase_candidates.extend(result["phase_candidates"])
        all_semantic_windows.extend(result["semantic_windows"])
        all_semantic_segments.extend(result["semantic_segments"])
        all_trend_transitions.extend(result["trend_transitions"])
        all_local_outliers.extend(result["local_outliers"])
        all_coupled_events.extend(result["coupled_parameter_events"])
        all_analysis_scopes.extend(result["analysis_scopes"])
        all_selected_semantic_segments.extend(result["selected_semantic_segments"])
        all_selected_coupled_events.extend(result["selected_coupled_parameter_events"])
        processed_samples.append(result["samples"])
    return {
        "schema_version": config["schema_version"],
        "plc_dir": str(plc_dir),
        "read_only_source": True,
        "report_or_llm_pipeline_called": False,
        "config": config,
        "derived_thresholds": thresholds,
        "resolved_fields": resolved_rows,
        "daily_results": daily_results,
        "complete_daily_results": complete_daily_results,
        "advance_intervals": all_intervals,
        "phases": all_phases,
        "phase_descriptors": all_descriptors,
        "construction_events": all_construction_events,
        "construction_event_candidates": all_construction_event_candidates,
        "quality_events": all_quality_events,
        "preprocessing_validation": all_validation,
        "phase_candidates": all_phase_candidates,
        "semantic_windows": all_semantic_windows,
        "semantic_segments": all_semantic_segments,
        "trend_transitions": all_trend_transitions,
        "local_outliers": all_local_outliers,
        "coupled_parameter_events": all_coupled_events,
        "analysis_scopes": all_analysis_scopes,
        "selected_semantic_segments": all_selected_semantic_segments,
        "selected_coupled_parameter_events": all_selected_coupled_events,
        "processed_samples": processed_samples,
        "summary": _dataset_summary(
            paths,
            daily_results,
            all_intervals,
            all_phases,
            all_construction_events,
            all_quality_events,
            all_semantic_windows,
            all_semantic_segments,
            all_coupled_events,
            all_analysis_scopes,
            all_selected_semantic_segments,
            all_selected_coupled_events,
        ),
    }


def analyze_day(
    frame: pd.DataFrame,
    *,
    path: Path,
    thresholds: dict[str, float],
    config: dict[str, Any],
) -> dict[str, Any]:
    date = _date(frame, path)
    quality_config = layer_config(config, "quality")
    process_config = layer_config(config, "process")
    dynamics_config = layer_config(config, "dynamics")
    orchestration_config = layer_config(config, "orchestration")
    processed, quality_events = preprocess_day(
        frame,
        thresholds=thresholds,
        config=quality_config,
    )
    validate_quality_controlled_samples(processed)
    quality_events.extend(validate_day_consistency(processed, config=quality_config))
    processed = classify_machine_state(processed, process_config)
    intervals, accepted_mask = extract_advance_intervals(processed, process_config, date=date)
    quality_events.extend(
        _annotate_interval_distance(processed, intervals, orchestration_config)
    )
    processed["valid_advance_interval"] = accepted_mask
    processed["interval_id"] = None
    processed["phase_id"] = None
    processed["phase_label"] = None
    processed["analysis_scope_id"] = None
    processed["analysis_scope_label"] = None
    validate_process_samples(processed)
    phases: list[dict[str, Any]] = []
    phase_candidates: list[dict[str, Any]] = []
    descriptors: list[dict[str, Any]] = []
    construction_events: list[dict[str, Any]] = []
    semantic_windows: list[dict[str, Any]] = []
    semantic_segments: list[dict[str, Any]] = []
    trend_transitions: list[dict[str, Any]] = []
    local_outliers: list[dict[str, Any]] = []
    coupled_parameter_events: list[dict[str, Any]] = []
    analysis_scopes: list[dict[str, Any]] = []
    for interval_number, interval in enumerate(intervals):
        lower_bound = int(intervals[interval_number - 1]["end_index"]) + 1 if interval_number else 0
        upper_bound = (
            int(intervals[interval_number + 1]["start_index"]) - 1
            if interval_number + 1 < len(intervals)
            else len(processed) - 1
        )
        phase_start, phase_end = _phase_context_bounds(
            processed,
            interval,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            config=orchestration_config,
        )
        block = processed.iloc[phase_start : phase_end + 1]
        interval["phase_context_start_time"] = iso(block["time"].iloc[0])
        interval["phase_context_end_time"] = iso(block["time"].iloc[-1])
        interval["phase_context_pre_samples"] = int(int(interval["start_index"]) - phase_start)
        interval["phase_context_post_samples"] = int(phase_end - int(interval["end_index"]))
        terminal_stop_time = None
        next_position = int(interval["end_index"]) + 1
        if next_position < len(processed):
            next_sample = processed.iloc[next_position]
            core_end_sample = processed.iloc[int(interval["end_index"])]
            same_continuity_block = (
                next_sample["continuity_block_id"] == core_end_sample["continuity_block_id"]
            )
            seconds_to_next = (next_sample["time"] - core_end_sample["time"]).total_seconds()
            if (
                same_continuity_block
                and pd.notna(next_sample["raw_state"])
                and float(next_sample["raw_state"]) == 0.0
                and 0 <= seconds_to_next <= float(
                    process_config.get("terminal_stop_max_gap_seconds", 120.0)
                )
            ):
                terminal_stop_time = pd.Timestamp(next_sample["time"])
        # Context may overlap for boundary detection. Formal ownership follows the
        # original deterministic order, so the later cycle replaces the earlier
        # cycle on shared context rows before summaries are rebuilt.
        processed.loc[block.index, "interval_id"] = interval["interval_id"]
        processed.loc[block.index, ["phase_id", "phase_label"]] = None
        labels, phase_rows, segmentation, candidates = segment_interval_phases(
            block,
            interval_id=interval["interval_id"],
            config=process_config,
            terminal_stop_time=terminal_stop_time,
        )
        phases.extend(phase_rows)
        for candidate in candidates:
            candidate["selected"] = bool(
                candidate["start_time"] == segmentation.get("stable_start_time")
                and candidate["end_time"] == segmentation.get("stable_end_time")
            )
            candidate["selection_status"] = (
                "selected" if candidate["selected"] else "not_selected_lower_candidate_score_or_duration"
            )
        phase_candidates.extend(candidates)
        interval.update(segmentation)
        processed.loc[labels.index, "phase_label"] = labels
        for phase in phase_rows:
            phase_index = labels.index[
                labels.eq(phase["phase_label"])
                & block["source_row"].between(
                    int(phase["source_row_start"]),
                    int(phase["source_row_end"]),
                )
            ]
            processed.loc[phase_index, "phase_id"] = phase["phase_id"]

    phases = rebuild_phase_rows_from_assignments(processed, phases, process_config)
    phases_by_interval: dict[str, list[dict[str, Any]]] = {}
    for phase in phases:
        phases_by_interval.setdefault(phase["interval_id"], []).append(phase)
    for interval in intervals:
        interval["identified_phase_count"] = int(
            len(phases_by_interval.get(interval["interval_id"], []))
        )

    for interval in intervals:
        interval_id = interval["interval_id"]
        block = processed[processed["interval_id"].eq(interval_id)]
        labels = processed.loc[block.index, "phase_label"]
        phase_rows = phases_by_interval.get(interval_id, [])
        interval_scopes = build_analysis_scopes(
            block,
            labels=labels,
            phase_rows=phase_rows,
            interval=interval,
            date=date,
            config=orchestration_config,
        )
        analysis_scopes.extend(interval_scopes)
        for scope in interval_scopes:
            scope_mask = (
                processed["interval_id"].eq(interval_id)
                & processed["source_row"].between(
                    int(scope["source_row_start"]),
                    int(scope["source_row_end"]),
                )
            )
            processed.loc[scope_mask, "analysis_scope_id"] = scope["phase_id"]
            processed.loc[scope_mask, "analysis_scope_label"] = scope["phase_label"]
        desc, events = analyze_phases(block, interval_scopes, config=dynamics_config)
        descriptors.extend(desc)
        construction_events.extend(events)
        semantic = analyze_phase_semantics(block, interval_scopes, config=dynamics_config)
        semantic_windows.extend(semantic["window_descriptors"])
        semantic_segments.extend(semantic["semantic_segments"])
        trend_transitions.extend(semantic["trend_transitions"])
        local_outliers.extend(semantic["local_outliers"])
        coupled_parameter_events.extend(semantic["coupled_parameter_events"])
    selected_semantic_segments, selected_coupled_parameter_events = select_semantic_facts(
        semantic_segments,
        coupled_parameter_events,
        config=dynamics_config,
    )
    construction_event_candidates = construction_events
    priority_interval_ids = [
        item["interval_id"]
        for item in sorted(
            intervals,
            key=lambda item: float(item.get("duration_seconds") or 0),
            reverse=True,
            )[: int(orchestration_config["top_process_count"])]
    ]
    construction_events = select_important_events(
        construction_event_candidates,
        config=dynamics_config,
        priority_interval_ids=priority_interval_ids,
    )
    for index, event in enumerate(quality_events, start=1):
        event["date"] = date
        event["event_id"] = f"{date}-Q{index:03d}"
        event["interpretation"] = "data_quality_issue_not_machine_fault"
    structured = build_daily_result(
        processed,
        date=date,
        path=path,
        intervals=intervals,
        phases=phases,
        analysis_scopes=analysis_scopes,
        construction_events=construction_events,
        selected_semantic_segments=selected_semantic_segments,
        selected_coupled_parameter_events=selected_coupled_parameter_events,
        semantic_segment_count_before_selection=len(semantic_segments),
        coupled_event_count_before_selection=len(coupled_parameter_events),
        quality_events=quality_events,
        config=orchestration_config,
    )
    complete_structured = build_complete_daily_result(
        processed,
        date=date,
        path=path,
        intervals=intervals,
        phases=phases,
        analysis_scopes=analysis_scopes,
        descriptors=descriptors,
        semantic_segments=semantic_segments,
        trend_transitions=trend_transitions,
        coupled_parameter_events=coupled_parameter_events,
        construction_events=construction_events,
        quality_events=quality_events,
        config=orchestration_config,
    )
    return {
        "structured": structured,
        "complete_structured": complete_structured,
        "samples": processed,
        "intervals": intervals,
        "phases": phases,
        "analysis_scopes": analysis_scopes,
        "descriptors": descriptors,
        "construction_events": construction_events,
        "construction_event_candidates": construction_event_candidates,
        "quality_events": quality_events,
        "preprocessing_validation": _preprocessing_validation(
            processed,
            date=date,
            thresholds=thresholds,
        ),
        "phase_candidates": phase_candidates,
        "semantic_windows": semantic_windows,
        "semantic_segments": semantic_segments,
        "trend_transitions": trend_transitions,
        "local_outliers": local_outliers,
        "coupled_parameter_events": coupled_parameter_events,
        "selected_semantic_segments": selected_semantic_segments,
        "selected_coupled_parameter_events": selected_coupled_parameter_events,
    }


def _phase_context_bounds(
    frame: pd.DataFrame,
    interval: dict[str, Any],
    *,
    lower_bound: int,
    upper_bound: int,
    config: dict[str, Any],
) -> tuple[int, int]:
    core_start = int(interval["start_index"])
    core_end = int(interval["end_index"])
    maximum_seconds = float(config.get("transition_context_max_seconds", 180.0))
    torque_min = float(config.get("transition_context_torque_min", 50.0))
    rpm_min = float(config.get("transition_context_rpm_min", 0.25))
    torque = pd.to_numeric(frame["torque"], errors="coerce").fillna(0.0)
    rpm = pd.to_numeric(frame["rpm_canonical"], errors="coerce").fillna(0.0)
    raw_off = pd.to_numeric(frame["raw_state"], errors="coerce").fillna(0.0).eq(0.0)
    transition_active = raw_off & (torque.ge(torque_min) | rpm.ge(rpm_min))
    hard_before = frame["hard_gap_before"].fillna(False).to_numpy(dtype=bool)

    phase_start = core_start
    position = core_start - 1
    while position >= lower_bound:
        if not bool(transition_active.iloc[position]) or bool(hard_before[position + 1]):
            break
        if (frame["time"].iloc[core_start] - frame["time"].iloc[position]).total_seconds() > maximum_seconds:
            break
        phase_start = position
        position -= 1

    phase_end = core_end
    position = core_end + 1
    while position <= upper_bound:
        if not bool(transition_active.iloc[position]) or bool(hard_before[position]):
            break
        if (frame["time"].iloc[position] - frame["time"].iloc[core_end]).total_seconds() > maximum_seconds:
            break
        phase_end = position
        position += 1
    return phase_start, phase_end


def _annotate_interval_distance(
    frame: pd.DataFrame,
    intervals: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    warning_ratio = float(config["quality_distance_ratio_warning"])
    for interval in intervals:
        block = frame.iloc[interval["start_index"] : interval["end_index"] + 1]
        dt = block["time"].diff().dt.total_seconds().clip(
            lower=0,
            upper=float(config["maximum_sample_duration_seconds"]),
        ).fillna(0)
        integrated = float((block["advance_speed"].fillna(0) * dt / 60_000.0).sum())
        cumulative = pd.to_numeric(block["cumulative_advance"], errors="coerce").dropna()
        recorded = float(abs(cumulative.iloc[-1] - cumulative.iloc[0])) if len(cumulative) >= 2 else np.nan
        chainage = pd.to_numeric(block["chainage"], errors="coerce").dropna()
        chainage_change = float(abs(chainage.iloc[-1] - chainage.iloc[0])) if len(chainage) >= 2 else np.nan
        ratio = (
            max(integrated, recorded) / max(min(integrated, recorded), 0.1)
            if np.isfinite(recorded)
            else np.nan
        )
        jumps = cumulative.diff().abs() if len(cumulative) >= 2 else pd.Series(dtype=float)
        jump_count = int(jumps.gt(float(config["cumulative_step_jump_m"])).sum())
        status = "consistent_within_threshold"
        if not np.isfinite(recorded):
            status = "recorded_distance_unavailable"
        elif ratio > warning_ratio:
            status = "distance_mismatch_review_required"
        interval.update(
            {
                "valid_speed_integrated_distance_m": safe_number(integrated),
                "recorded_cumulative_advance_change_m": safe_number(recorded),
                "interval_chainage_change_m": safe_number(chainage_change),
                "speed_to_recorded_distance_ratio": safe_number(ratio),
                "distance_consistency_status": status,
                "cumulative_step_jump_count": jump_count,
                "cumulative_step_jump_max_m": safe_number(jumps.max()) if not jumps.empty else None,
            }
        )
        if status == "distance_mismatch_review_required":
            events.append(
                {
                    "event_type": "data_quality",
                    "subtype": "interval_speed_distance_mismatch",
                    "interval_id": interval["interval_id"],
                    "start_time": interval["start_time"],
                    "end_time": interval["end_time"],
                    "speed_integrated_distance_m": safe_number(integrated),
                    "recorded_cumulative_advance_change_m": safe_number(recorded),
                    "distance_ratio": safe_number(ratio),
                    "action": "retain_interval_but_do_not_treat_distance_as_verified",
                    "source_file": interval["source_file"],
                    "source_row_start": interval["source_row_start"],
                    "source_row_end": interval["source_row_end"],
                }
            )
        if jump_count:
            events.append(
                {
                    "event_type": "data_quality",
                    "subtype": "interval_cumulative_advance_step_jump",
                    "interval_id": interval["interval_id"],
                    "start_time": interval["start_time"],
                    "end_time": interval["end_time"],
                    "step_jump_count": jump_count,
                    "step_jump_max_m": safe_number(jumps.max()),
                    "action": "flag_cumulative_advance_as_unverified_for_interval",
                    "source_file": interval["source_file"],
                    "source_row_start": interval["source_row_start"],
                    "source_row_end": interval["source_row_end"],
                }
            )
    return events


def _preprocessing_validation(
    frame: pd.DataFrame,
    *,
    date: str,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    dt = frame["sample_dt_seconds"].fillna(0)
    raw_integral = float((frame["advance_speed_raw"].fillna(0) * dt / 60_000.0).sum())
    valid_integral = float((frame["advance_speed"].fillna(0) * dt / 60_000.0).sum())
    cumulative = frame["cumulative_advance"].dropna()
    chainage = frame["chainage"].dropna()
    recorded = float(abs(cumulative.iloc[-1] - cumulative.iloc[0])) if len(cumulative) >= 2 else np.nan
    ratio = (
        max(valid_integral, recorded) / max(min(valid_integral, recorded), 0.1)
        if np.isfinite(recorded)
        else np.nan
    )
    working = frame[
        frame["raw_state"].fillna(0).ne(0)
        & frame["rpm_canonical"].gt(0)
        & frame["motor_frequency_m1"].gt(0)
    ]
    rpm_ratio = working["rpm_canonical"] / working["motor_frequency_m1"]
    rpm_error = working["rpm_reference_relative_error"].replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "date": date,
        "source_file": str(frame["source_file"].iloc[0]),
        "speed_valid_upper_bound": safe_number(thresholds["speed_valid_upper_bound"]),
        "speed_invalid_extreme_count": int(frame["advance_speed_quality"].eq("invalid_extreme").sum()),
        "speed_missing_count": int(frame["advance_speed_quality"].eq("missing").sum()),
        "raw_speed_integrated_distance_m": safe_number(raw_integral),
        "valid_speed_integrated_distance_m": safe_number(valid_integral),
        "recorded_cumulative_advance_change_m": safe_number(recorded),
        "chainage_change_m": safe_number(abs(chainage.iloc[-1] - chainage.iloc[0])) if len(chainage) >= 2 else None,
        "valid_speed_to_recorded_distance_ratio": safe_number(ratio),
        "rpm_source_actual_count": int(frame["rpm_source"].eq("刀盘实际转速").sum()),
        "rpm_source_alternate_count": int(frame["rpm_source"].eq("刀盘给定频率").sum()),
        "rpm_excluded_sample_count": int(frame["rpm_canonical"].isna().sum()),
        "working_rpm_to_m1_ratio_p50": safe_number(rpm_ratio.median()) if not rpm_ratio.empty else None,
        "working_rpm_to_m1_ratio_p05": safe_number(rpm_ratio.quantile(0.05)) if not rpm_ratio.empty else None,
        "working_rpm_to_m1_ratio_p95": safe_number(rpm_ratio.quantile(0.95)) if not rpm_ratio.empty else None,
        "working_rpm_reference_relative_error_p50": safe_number(rpm_error.median()) if not rpm_error.empty else None,
        "working_rpm_reference_relative_error_p95": safe_number(rpm_error.quantile(0.95)) if not rpm_error.empty else None,
        "rpm_mapping_status": "provisional_pending_plc_point_table_confirmation",
    }


def _date(frame: pd.DataFrame, path: Path) -> str:
    valid = frame["time"].dropna()
    if not valid.empty:
        return str(valid.iloc[0].date())
    token = path.stem.rsplit("_", 1)[-1]
    return f"{token[:4]}-{token[4:6]}-{token[6:8]}"


def _dataset_summary(
    paths: list[Path],
    daily: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    construction_events: list[dict[str, Any]],
    quality_events: list[dict[str, Any]],
    semantic_windows: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    coupled_events: list[dict[str, Any]],
    analysis_scopes: list[dict[str, Any]],
    selected_semantic_segments: list[dict[str, Any]],
    selected_coupled_events: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = sum(int(item["overall"]["row_count"]) for item in daily)
    dates = [item["date"] for item in daily]
    observed = pd.to_datetime(pd.Series(dates), errors="coerce").dropna()
    missing_dates: list[str] = []
    if not observed.empty:
        expected = pd.date_range(observed.min(), observed.max(), freq="D")
        present = set(observed.dt.strftime("%Y-%m-%d"))
        missing_dates = [str(item.date()) for item in expected if str(item.date()) not in present]
    return {
        "file_count": int(len(paths)),
        "day_count": int(len(daily)),
        "row_count": int(rows),
        "advance_interval_count": int(len(intervals)),
        "phase_count": int(len(phases)),
        "construction_response_event_count": int(len(construction_events)),
        "data_quality_event_count": int(len(quality_events)),
        "semantic_window_descriptor_count": int(len(semantic_windows)),
        "merged_semantic_segment_count": int(len(semantic_segments)),
        "coupled_parameter_event_count": int(len(coupled_events)),
        "analysis_scope_count": int(len(analysis_scopes)),
        "selected_semantic_segment_count": int(len(selected_semantic_segments)),
        "selected_coupled_parameter_event_count": int(len(selected_coupled_events)),
        "dates": dates,
        "missing_calendar_dates": missing_dates,
        "missing_calendar_dates_not_inferred_as_stopped": True,
        "report_or_llm_pipeline_called": False,
    }

