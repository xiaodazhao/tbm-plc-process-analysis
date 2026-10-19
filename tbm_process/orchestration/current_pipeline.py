from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tbm_process.process.platform_detection import analyze_platform_first_day
from tbm_process.dynamics.descriptors import analyze_phases, select_important_events
from tbm_process.dynamics.semantics import analyze_phase_semantics, select_semantic_facts
from tbm_process.facts.builders import build_complete_daily_result, build_daily_result
from tbm_process.ingestion.io import safe_number
from tbm_process.process.scopes import build_analysis_scopes


DOWNSTREAM_METHOD = "tbm_platform_first_downstream_v1"


def analyze_platform_first_downstream_day(
    frame: pd.DataFrame,
    *,
    date: str,
    base_result: dict[str, Any],
    experiment_config: dict[str, Any],
    phase_config: dict[str, Any],
    dynamics_config: dict[str, Any],
    orchestration_config: dict[str, Any],
) -> dict[str, Any]:
    """Run existing downstream analysis over isolated platform-first assignments."""
    experiment, processed = analyze_platform_first_day(
        frame,
        date=date,
        experiment_config=experiment_config,
        phase_config=phase_config,
    )
    processed = _adopt_experimental_assignments(processed)
    intervals = [_platform_interval(row) for row in experiment["platforms"]]
    distance_events = _annotate_platform_distance(
        processed,
        intervals,
        config=orchestration_config,
    )
    phases = [deepcopy(row) for row in experiment["phases"]]
    phases_by_interval: dict[str, list[dict[str, Any]]] = {}
    for phase in phases:
        phases_by_interval.setdefault(str(phase["interval_id"]), []).append(phase)

    descriptors: list[dict[str, Any]] = []
    construction_event_candidates: list[dict[str, Any]] = []
    semantic_windows: list[dict[str, Any]] = []
    semantic_segments: list[dict[str, Any]] = []
    trend_transitions: list[dict[str, Any]] = []
    local_outliers: list[dict[str, Any]] = []
    coupled_parameter_events: list[dict[str, Any]] = []
    analysis_scopes: list[dict[str, Any]] = []

    phase_eligibility = {
        str(phase["phase_id"]): bool(phase.get("dynamic_analysis_eligible", True))
        for phase in phases
    }
    for interval in intervals:
        interval_id = str(interval["interval_id"])
        block = processed.loc[processed["interval_id"].eq(interval_id)].copy()
        labels = processed.loc[block.index, "phase_label"]
        scopes = build_analysis_scopes(
            block,
            labels=labels,
            phase_rows=phases_by_interval.get(interval_id, []),
            interval=interval,
            date=date,
            config=orchestration_config,
        )
        for scope in scopes:
            scope["dynamic_analysis_eligible"] = phase_eligibility.get(
                str(scope["phase_id"]), True
            )
            mask = processed["interval_id"].eq(interval_id) & processed[
                "source_row"
            ].between(int(scope["source_row_start"]), int(scope["source_row_end"]))
            processed.loc[mask, "analysis_scope_id"] = scope["phase_id"]
            processed.loc[mask, "analysis_scope_label"] = scope["phase_label"]
        analysis_scopes.extend(scopes)

        eligible_scopes = [
            scope for scope in scopes if scope["dynamic_analysis_eligible"]
        ]
        desc, events = analyze_phases(
            block,
            eligible_scopes,
            config=dynamics_config,
        )
        descriptors.extend(desc)
        construction_event_candidates.extend(events)
        semantic = analyze_phase_semantics(
            block,
            eligible_scopes,
            config=dynamics_config,
        )
        semantic_windows.extend(semantic["window_descriptors"])
        semantic_segments.extend(semantic["semantic_segments"])
        trend_transitions.extend(semantic["trend_transitions"])
        local_outliers.extend(semantic["local_outliers"])
        coupled_parameter_events.extend(semantic["coupled_parameter_events"])

    selected_segments, selected_coupled = select_semantic_facts(
        semantic_segments,
        coupled_parameter_events,
        config=dynamics_config,
    )
    priority_interval_ids = [
        row["interval_id"]
        for row in sorted(
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
    quality_events = _quality_events_for_experiment(
        base_result.get("data_quality_events", []),
        distance_events,
        date=date,
    )
    source_path = Path(str(base_result.get("source_file") or f"{date}.csv"))
    structured = build_daily_result(
        processed,
        date=date,
        path=source_path,
        intervals=intervals,
        phases=phases,
        analysis_scopes=analysis_scopes,
        construction_events=construction_events,
        selected_semantic_segments=selected_segments,
        selected_coupled_parameter_events=selected_coupled,
        semantic_segment_count_before_selection=len(semantic_segments),
        coupled_event_count_before_selection=len(coupled_parameter_events),
        quality_events=quality_events,
        config=orchestration_config,
    )
    complete = build_complete_daily_result(
        processed,
        date=date,
        path=source_path,
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
    termination_observations = [
        {
            "interval_id": row["interval_id"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            **deepcopy(row["termination_observation"]),
        }
        for row in intervals
    ]
    metadata = {
        "method": DOWNSTREAM_METHOD,
        "status": "experimental_not_frozen",
        "source": "frozen_quality_controlled_samples_with_recomputed_process_layer",
        "frozen_outputs_modified": False,
        "termination_observation_counts": experiment["summary"][
            "termination_observation_counts"
        ],
        "instantaneous_stop_boundaries_excluded_from_dynamic_analysis": True,
        "llm_invoked": False,
    }
    for result in (structured, complete):
        result["analysis_variant"] = metadata
        result["termination_observations"] = termination_observations
        result["method_limits"] = list(result.get("method_limits", [])) + [
            "platform_first_segmentation_is_an_experimental_candidate_not_the_frozen_baseline",
            "instantaneous_stop_boundaries_are_traceability_markers_not_sustained_declining_processes",
            "termination_during_sampling_gaps_or_after_daily_record_end_is_not_inferred",
        ]

    return {
        "date": date,
        "experiment": experiment,
        "samples": processed,
        "intervals": intervals,
        "phases": phases,
        "analysis_scopes": analysis_scopes,
        "descriptors": descriptors,
        "construction_events": construction_events,
        "construction_event_candidates": construction_event_candidates,
        "quality_events": quality_events,
        "semantic_windows": semantic_windows,
        "semantic_segments": semantic_segments,
        "trend_transitions": trend_transitions,
        "local_outliers": local_outliers,
        "coupled_parameter_events": coupled_parameter_events,
        "selected_semantic_segments": selected_segments,
        "selected_coupled_parameter_events": selected_coupled,
        "structured": structured,
        "complete_structured": complete,
    }


def _adopt_experimental_assignments(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["valid_advance_interval"] = result["experimental_platform_id"].notna()
    result["interval_id"] = result["experimental_platform_id"]
    result["phase_id"] = result["experimental_phase_id"]
    result["phase_label"] = result["experimental_phase_label"]
    result["analysis_scope_id"] = None
    result["analysis_scope_label"] = None
    return result


def _platform_interval(row: dict[str, Any]) -> dict[str, Any]:
    excluded = {"phases", "phase_candidates"}
    interval = {key: deepcopy(value) for key, value in row.items() if key not in excluded}
    interval["interval_id"] = row["platform_id"]
    interval["interval_type"] = "experimental_platform_first_operating_episode"
    interval["continuity_block_id"] = None
    return interval


def _annotate_platform_distance(
    frame: pd.DataFrame,
    intervals: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    warning_ratio = float(config["quality_distance_ratio_warning"])
    for interval in intervals:
        block = frame.iloc[
            int(interval["start_index"]) : int(interval["end_index"]) + 1
        ]
        dt = block["time"].diff().dt.total_seconds().clip(
            lower=0,
            upper=float(config["maximum_sample_duration_seconds"]),
        ).fillna(0.0)
        speed = pd.to_numeric(block["advance_speed"], errors="coerce").fillna(0.0)
        integrated = float((speed * dt / 60_000.0).sum())
        cumulative = pd.to_numeric(block["cumulative_advance"], errors="coerce").dropna()
        recorded = (
            float(abs(cumulative.iloc[-1] - cumulative.iloc[0]))
            if len(cumulative) >= 2
            else np.nan
        )
        chainage = pd.to_numeric(block["chainage"], errors="coerce").dropna()
        chainage_change = (
            float(abs(chainage.iloc[-1] - chainage.iloc[0]))
            if len(chainage) >= 2
            else np.nan
        )
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
                "cumulative_step_jump_max_m": (
                    safe_number(jumps.max()) if not jumps.empty else None
                ),
            }
        )
        if status == "distance_mismatch_review_required":
            events.append(
                _distance_event(
                    interval,
                    subtype="interval_speed_distance_mismatch",
                    details={
                        "speed_integrated_distance_m": safe_number(integrated),
                        "recorded_cumulative_advance_change_m": safe_number(recorded),
                        "distance_ratio": safe_number(ratio),
                    },
                )
            )
        if jump_count:
            events.append(
                _distance_event(
                    interval,
                    subtype="interval_cumulative_advance_step_jump",
                    details={
                        "step_jump_count": jump_count,
                        "step_jump_max_m": safe_number(jumps.max()),
                    },
                )
            )
    return events


def _distance_event(
    interval: dict[str, Any], *, subtype: str, details: dict[str, Any]
) -> dict[str, Any]:
    return {
        "event_type": "data_quality",
        "subtype": subtype,
        "interval_id": interval["interval_id"],
        "start_time": interval["start_time"],
        "end_time": interval["end_time"],
        **details,
        "action": "retain_platform_but_do_not_treat_distance_as_verified",
        "source_file": interval["source_file"],
        "source_row_start": interval["source_row_start"],
        "source_row_end": interval["source_row_end"],
    }


def _quality_events_for_experiment(
    frozen_events: list[dict[str, Any]],
    platform_events: list[dict[str, Any]],
    *,
    date: str,
) -> list[dict[str, Any]]:
    interval_specific = {
        "interval_speed_distance_mismatch",
        "interval_cumulative_advance_step_jump",
    }
    rows = [
        deepcopy(row)
        for row in frozen_events
        if row.get("subtype") not in interval_specific
    ]
    rows.extend(deepcopy(platform_events))
    for number, row in enumerate(rows, start=1):
        row["date"] = date
        row["event_id"] = f"{date}-PEQ{number:03d}"
        row["interpretation"] = "data_quality_issue_not_machine_fault"
    return rows
