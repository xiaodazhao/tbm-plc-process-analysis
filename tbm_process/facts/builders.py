from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from ..ingestion.io import iso, safe_number
from ..process.segmentation import parameter_summary


def build_daily_result(
    frame: pd.DataFrame,
    *,
    date: str,
    path: Path,
    intervals: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    construction_events: list[dict[str, Any]],
    quality_events: list[dict[str, Any]],
    config: dict[str, Any],
    analysis_scopes: list[dict[str, Any]] | None = None,
    selected_semantic_segments: list[dict[str, Any]] | None = None,
    selected_coupled_parameter_events: list[dict[str, Any]] | None = None,
    semantic_segment_count_before_selection: int = 0,
    coupled_event_count_before_selection: int = 0,
) -> dict[str, Any]:
    analysis_scopes = analysis_scopes or []
    selected_semantic_segments = selected_semantic_segments or []
    selected_coupled_parameter_events = selected_coupled_parameter_events or []
    durations = state_durations(frame, config)
    chainage = frame["chainage"].dropna()
    cumulative = frame["cumulative_advance"].dropna()
    sample_interval = frame["time"].diff().dt.total_seconds().dropna()
    observed_duration_seconds = float(frame["sample_dt_seconds"].fillna(0).sum())
    valid_speed_distance_m = float(
        (
            frame["advance_speed"].fillna(0)
            * frame["sample_dt_seconds"].fillna(0)
            / 60_000.0
        ).sum()
    )
    invalid_speed_count = int(frame["advance_speed_quality"].ne("usable").sum())
    valid_interval_seconds = sum(float(item["duration_seconds"] or 0) for item in intervals)
    quality_summary = dict(Counter(event["subtype"] for event in quality_events))
    top_intervals = sorted(
        intervals,
        key=lambda item: float(item["duration_seconds"] or 0),
        reverse=True,
    )[: int(config["top_process_count"])]
    phase_by_interval: dict[str, list[dict[str, Any]]] = {}
    for phase in phases:
        phase_by_interval.setdefault(phase["interval_id"], []).append(phase)
    event_by_interval: dict[str, list[dict[str, Any]]] = {}
    for event in construction_events:
        event_by_interval.setdefault(event["interval_id"], []).append(event)
    processes = []
    for interval in top_intervals:
        processes.append(
            {
                **interval,
                "phases": phase_by_interval.get(interval["interval_id"], []),
                "construction_events": event_by_interval.get(interval["interval_id"], []),
            }
        )
    return {
        "schema_version": config["schema_version"],
        "date": date,
        "source_file": path.name,
        "overall": {
            "row_count": int(len(frame)),
            "timestamp_start": iso(frame["time"].min()),
            "timestamp_end": iso(frame["time"].max()),
            "sample_interval_seconds_median": (
                safe_number(sample_interval.median()) if not sample_interval.empty else None
            ),
            "data_coverage": {
                "continuity_block_count": int(frame["continuity_block_id"].nunique()),
                "hard_sampling_gap_count": int(frame["hard_gap_before"].fillna(False).sum()),
                "observed_duration_seconds": safe_number(observed_duration_seconds),
                "hard_gap_elapsed_seconds_total": safe_number(
                    frame.loc[
                        frame["hard_gap_before"].fillna(False), "gap_before_seconds"
                    ].sum()
                ),
                "hard_gaps_excluded_from_state_duration": True,
            },
            "chainage_start": safe_number(chainage.iloc[0]) if not chainage.empty else None,
            "chainage_end": safe_number(chainage.iloc[-1]) if not chainage.empty else None,
            "chainage_change_m": (
                safe_number(abs(chainage.iloc[-1] - chainage.iloc[0]))
                if len(chainage) >= 2
                else None
            ),
            "cumulative_advance_change_m": (
                safe_number(abs(cumulative.iloc[-1] - cumulative.iloc[0]))
                if len(cumulative) >= 2
                else None
            ),
            "valid_speed_integrated_distance_m": safe_number(valid_speed_distance_m),
            "distance_evidence_note": "separate_evidence_not_automatically_reconciled",
            "machine_state_duration_seconds": durations,
            "valid_advance_interval_count": int(len(intervals)),
            "valid_advance_duration_seconds": safe_number(valid_interval_seconds),
            "core_parameter_distribution": parameter_summary(
                frame[frame["valid_advance_interval"]]
            ),
            "data_quality": {
                "invalid_or_missing_speed_sample_count": invalid_speed_count,
                "invalid_or_missing_speed_sample_ratio": (
                    safe_number(invalid_speed_count / len(frame)) if len(frame) else None
                ),
                "rpm_provisional_mapping_sample_count": int(
                    frame["rpm_quality"].eq("usable_provisional_mapping").sum()
                ),
                "quality_event_count": int(len(quality_events)),
                "quality_event_types": quality_summary,
            },
        },
        "main_processes": processes,
        "construction_response_events": construction_events,
        "selected_parameter_facts": selected_semantic_segments,
        "selected_coupled_parameter_events": selected_coupled_parameter_events,
        "data_quality_events": quality_events,
        "selection_metadata": {
            "result_type": "filtered_daily_summary",
            "cycle_selection": f"top_{int(config['top_process_count'])}_by_duration",
            "cycle_count_before_selection": int(len(intervals)),
            "cycle_count_after_selection": int(len(processes)),
            "cycle_count_not_in_compact_summary": int(
                max(len(intervals) - len(processes), 0)
            ),
            "construction_event_limit": int(config["daily_construction_event_limit"]),
            "semantic_fact_limit": int(config.get("daily_semantic_segment_limit", 20)),
            "analysis_scope_count": int(len(analysis_scopes)),
            "semantic_fact_count_before_selection": int(
                semantic_segment_count_before_selection
            ),
            "semantic_fact_count_after_selection": int(len(selected_semantic_segments)),
            "semantic_fact_count_not_in_compact_summary": int(
                max(semantic_segment_count_before_selection - len(selected_semantic_segments), 0)
            ),
            "coupled_event_count_before_selection": int(coupled_event_count_before_selection),
            "coupled_event_count_after_selection": int(
                len(selected_coupled_parameter_events)
            ),
            "coupled_event_count_not_in_compact_summary": int(
                max(
                    coupled_event_count_before_selection
                    - len(selected_coupled_parameter_events),
                    0,
                )
            ),
            "unselected_data_location": (
                "structured_full_days/<date>.json and complete CSV tables"
            ),
        },
        "method_limits": [
            "rpm_canonical_is_a_provisional_mapping_pending_plc_point_table_confirmation",
            "four_phase_labels_are_rule_based_candidates_for_manual_validation",
            "free_rotation_and_free_advancing_are_merged_into_startup_for_low_frequency_data",
            "cycles_without_low_slope_low_variance_stable_evidence_are_left_unsegmented",
            "unsegmented_cycles_still_receive_interval_level_dynamic_analysis",
            "unidentified_parts_of_segmented_cycles_receive_separate_dynamic_analysis_scopes",
            "construction_events_describe_machine_response_not_geology_or_fault_causes",
            "invalid_speed_values_are_excluded_not_imputed",
            "cycle_splitting_requires_sustained_multivariate_unload_and_reload_evidence",
            "missing_calendar_dates_are_not_interpreted_as_stopped_operation",
        ],
    }


def build_complete_daily_result(
    frame: pd.DataFrame,
    *,
    date: str,
    path: Path,
    intervals: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    analysis_scopes: list[dict[str, Any]],
    descriptors: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    trend_transitions: list[dict[str, Any]],
    coupled_parameter_events: list[dict[str, Any]],
    construction_events: list[dict[str, Any]],
    quality_events: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    summary = build_daily_result(
        frame,
        date=date,
        path=path,
        intervals=intervals,
        phases=phases,
        analysis_scopes=analysis_scopes,
        construction_events=construction_events,
        selected_semantic_segments=[],
        selected_coupled_parameter_events=[],
        quality_events=quality_events,
        config=config,
    )
    scope_by_interval: dict[str, list[dict[str, Any]]] = {}
    for scope in analysis_scopes:
        scope_by_interval.setdefault(scope["interval_id"], []).append(scope)
    descriptor_by_phase: dict[str, list[dict[str, Any]]] = {}
    for descriptor in descriptors:
        descriptor_by_phase.setdefault(descriptor["phase_id"], []).append(descriptor)
    segment_by_phase: dict[str, list[dict[str, Any]]] = {}
    for segment in semantic_segments:
        segment_by_phase.setdefault(segment["phase_id"], []).append(segment)
    transition_by_phase: dict[str, list[dict[str, Any]]] = {}
    for transition in trend_transitions:
        transition_by_phase.setdefault(transition["phase_id"], []).append(transition)
    coupled_by_interval: dict[str, list[dict[str, Any]]] = {}
    for event in coupled_parameter_events:
        coupled_by_interval.setdefault(event["interval_id"], []).append(event)

    cycles = []
    for interval in intervals:
        cycle_phases = []
        for phase in scope_by_interval.get(interval["interval_id"], []):
            phase_id = phase["phase_id"]
            cycle_phases.append(
                {
                    "phase_summary": phase,
                    "parameter_descriptors": descriptor_by_phase.get(phase_id, []),
                    "merged_semantic_segments": segment_by_phase.get(phase_id, []),
                    "trend_transitions": transition_by_phase.get(phase_id, []),
                }
            )
        cycles.append(
            {
                "cycle_summary": interval,
                "cycle_status": interval.get("segmentation_status"),
                "no_stable_reasons": interval.get("no_stable_reasons", []),
                "phases": cycle_phases,
                "coupled_parameter_events": coupled_by_interval.get(
                    interval["interval_id"], []
                ),
            }
        )

    hard_gaps = frame[frame["hard_gap_before"]]
    observed_duration = float(frame["sample_dt_seconds"].fillna(0).sum())
    state_duration_values = state_durations(frame, config)
    state_counts = {
        str(key): int(value) for key, value in frame["machine_state"].value_counts().items()
    }
    return {
        "schema_version": config["schema_version"],
        "date": date,
        "source_file": path.name,
        "result_type": "complete_traceable_fact_package",
        "overall": summary["overall"],
        "data_coverage": {
            "row_count": int(len(frame)),
            "continuity_block_count": int(frame["continuity_block_id"].nunique()),
            "hard_sampling_gap_count": int(len(hard_gaps)),
            "observed_capped_duration_seconds": safe_number(observed_duration),
            "hard_gaps_not_inferred_as_stopped": True,
        },
        "machine_states": {
            "duration_seconds": state_duration_values,
            "sample_count": state_counts,
            "separate_from_excavation_phase_labels": True,
        },
        "excavation_cycles": cycles,
        "construction_response_events": construction_events,
        "data_quality_events": quality_events,
        "selection_metadata": {
            "result_type": "complete_unfiltered_analysis",
            "cycle_count": int(len(cycles)),
            "all_accepted_cycles_included": True,
            "filtered_summary_saved_separately": True,
        },
        "method_limits": summary["method_limits"],
    }


def state_durations(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, float]:
    if "sample_dt_seconds" in frame:
        dt = pd.to_numeric(frame["sample_dt_seconds"], errors="coerce").fillna(0.0)
    else:
        dt = frame["time"].shift(-1).sub(frame["time"]).dt.total_seconds().clip(
            lower=0,
            upper=float(config["maximum_sample_duration_seconds"]),
        )
        if "hard_gap_before" in frame:
            crosses_hard_gap = frame["hard_gap_before"].shift(
                -1, fill_value=False
            ).astype(bool)
            dt = dt.mask(crosses_hard_gap, 0.0)
    result = frame.assign(_dt=dt).groupby("machine_state", dropna=False)["_dt"].sum()
    return {str(key): float(value) for key, value in result.items()}

