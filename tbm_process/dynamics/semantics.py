from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .descriptors import PARAMETERS, trend_descriptor
from ..ingestion.io import iso, safe_number


def analyze_phase_semantics(
    frame: pd.DataFrame,
    scopes: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    windows: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    outliers: list[dict[str, Any]] = []
    for scope in scopes:
        block = frame[
            frame["time"].between(pd.Timestamp(scope["start_time"]), pd.Timestamp(scope["end_time"]))
        ].copy()
        if block.empty:
            continue
        for _, continuous in block.groupby("continuity_block_id", sort=False):
            scope_windows = _window_descriptors(continuous, scope=scope, config=config)
            windows.extend(scope_windows)
            scope_segments, scope_transitions, scope_outliers = _merge_and_recompute(
                continuous,
                scope=scope,
                windows=scope_windows,
                config=config,
            )
            segments.extend(scope_segments)
            transitions.extend(scope_transitions)
            outliers.extend(scope_outliers)
    for index, row in enumerate(windows, start=1):
        row["window_descriptor_id"] = f"{row['phase_id']}-W{index:04d}"
    for index, row in enumerate(segments, start=1):
        row["semantic_segment_id"] = f"{row['phase_id']}-S{index:04d}"
    for index, row in enumerate(transitions, start=1):
        row["transition_id"] = f"{row['phase_id']}-T{index:04d}"
    for index, row in enumerate(outliers, start=1):
        row["local_outlier_id"] = f"{row['phase_id']}-O{index:04d}"
    coupled = _coupled_events(segments, frame, config)
    return {
        "window_descriptors": windows,
        "semantic_segments": segments,
        "trend_transitions": transitions,
        "local_outliers": outliers,
        "coupled_parameter_events": coupled,
    }


def select_semantic_facts(
    segments: list[dict[str, Any]],
    coupled_events: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create a compact, deterministic view without discarding the complete fact layer."""
    minimum_duration = float(config["semantic_selection_min_seconds"])
    eligible = [
        row.copy()
        for row in segments
        if float(row.get("duration_seconds") or 0) >= minimum_duration
        and row.get("trend") != "insufficient_data"
    ]
    for row in eligible:
        change = abs(float(row.get("relative_change") or 0))
        fluctuation = {"low": 0.0, "moderate": 0.5, "high": 1.0}.get(
            str(row.get("fluctuation_level")), 0.0
        )
        trend = 0.0 if row.get("trend") == "stable" else 1.0
        duration = min(float(row.get("duration_seconds") or 0) / 600.0, 2.0)
        row["selection_score"] = safe_number(duration + min(change, 2.0) + fluctuation + trend)
        row["selection_reason"] = (
            "persistent_change_or_fluctuation"
            if trend or fluctuation
            else "representative_persistent_stable_operation"
        )

    selected: list[dict[str, Any]] = []
    # Preserve at least one representative fact per parameter when available.
    for parameter in sorted({str(row["parameter"]) for row in eligible}):
        candidates = [row for row in eligible if row["parameter"] == parameter]
        selected.append(max(candidates, key=_selection_sort_key))
    selected_ids = {row.get("semantic_segment_id") for row in selected}
    for row in sorted(eligible, key=_selection_sort_key, reverse=True):
        if row.get("semantic_segment_id") in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row.get("semantic_segment_id"))
        if len(selected) >= int(config["daily_semantic_segment_limit"]):
            break
    selected = sorted(selected[: int(config["daily_semantic_segment_limit"])], key=lambda row: row["start_time"])

    selected_coupled = []
    for row in coupled_events:
        item = row.copy()
        states = item.get("parameter_states", {})
        changing = sum(value.get("trend") not in {"stable", "insufficient_data"} for value in states.values())
        fluctuating = sum(value.get("fluctuation_level") in {"moderate", "high"} for value in states.values())
        item["selection_score"] = safe_number(
            changing + 0.5 * fluctuating + min(float(item.get("duration_seconds") or 0) / 600.0, 2.0)
        )
        item["selection_reason"] = "overlapping_multi_parameter_response"
        selected_coupled.append(item)
    selected_coupled = sorted(selected_coupled, key=_selection_sort_key, reverse=True)[
        : int(config["daily_coupled_event_limit"])
    ]
    selected_coupled.sort(key=lambda row: row["start_time"])
    return selected, selected_coupled


def _selection_sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
    return (
        float(row.get("selection_score") or 0),
        float(row.get("duration_seconds") or 0),
        str(row.get("start_time") or ""),
    )


def _window_descriptors(
    block: pd.DataFrame,
    *,
    scope: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    width = float(config["dynamic_window_seconds"])
    cursor = block["time"].iloc[0]
    finish = block["time"].iloc[-1]
    rows: list[dict[str, Any]] = []
    while cursor <= finish:
        boundary = min(cursor + pd.Timedelta(seconds=width), finish)
        window = block[block["time"].between(cursor, boundary)]
        for parameter, column in PARAMETERS.items():
            valid = window[["time", column]].dropna()
            descriptor = _describe_valid(valid, column, config)
            rows.append(
                {
                    "date": scope["date"],
                    "interval_id": scope["interval_id"],
                    "phase_id": scope["phase_id"],
                    "phase_label": scope["phase_label"],
                    "continuity_block_id": int(window["continuity_block_id"].iloc[0]),
                    "parameter": parameter,
                    "start_time": iso(window["time"].iloc[0]),
                    "end_time": iso(window["time"].iloc[-1]),
                    "duration_seconds": safe_number(_duration(window)),
                    "window_method": f"phase_bounded_nonoverlapping_{width:g}s",
                    "source_file": str(window["source_file"].iloc[0]),
                    "source_row_start": int(window["source_row"].iloc[0]),
                    "source_row_end": int(window["source_row"].iloc[-1]),
                    **descriptor,
                }
            )
        cursor = boundary + pd.Timedelta(microseconds=1)
    return rows


def _merge_and_recompute(
    block: pd.DataFrame,
    *,
    scope: dict[str, Any],
    windows: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    segments: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    outliers: list[dict[str, Any]] = []
    for parameter, column in PARAMETERS.items():
        parameter_windows = sorted(
            (row for row in windows if row["parameter"] == parameter),
            key=lambda row: row["start_time"],
        )
        if not parameter_windows:
            continue
        groups: list[list[dict[str, Any]]] = [[parameter_windows[0]]]
        for row in parameter_windows[1:]:
            previous = groups[-1][-1]
            gap = (pd.Timestamp(row["start_time"]) - pd.Timestamp(previous["end_time"])).total_seconds()
            if _semantic_signature(row) == _semantic_signature(previous) and gap <= float(
                config["maximum_sample_duration_seconds"]
            ):
                groups[-1].append(row)
            else:
                groups.append([row])

        ranges = [
            [pd.Timestamp(group[0]["start_time"]), pd.Timestamp(group[-1]["end_time"])]
            for group in groups
        ]
        for index in range(len(ranges) - 1):
            if _semantic_signature(groups[index][0]) == _semantic_signature(groups[index + 1][0]):
                continue
            left_end, right_start = _refine_transition(
                block,
                column=column,
                left_start=ranges[index][0],
                rough_boundary=ranges[index][1],
                right_end=ranges[index + 1][1],
                config=config,
            )
            ranges[index][1] = left_end
            ranges[index + 1][0] = right_start

        parameter_segments: list[dict[str, Any]] = []
        for group, (start, end) in zip(groups, ranges, strict=True):
            segment_block = block[block["time"].between(start, end)]
            valid = segment_block[["time", column]].dropna()
            descriptor = _describe_valid(valid, column, config)
            row = {
                "date": scope["date"],
                "interval_id": scope["interval_id"],
                "phase_id": scope["phase_id"],
                "phase_label": scope["phase_label"],
                "continuity_block_id": int(segment_block["continuity_block_id"].iloc[0]),
                "parameter": parameter,
                "start_time": iso(segment_block["time"].iloc[0]),
                "end_time": iso(segment_block["time"].iloc[-1]),
                "duration_seconds": safe_number(_duration(segment_block)),
                "merged_window_count": int(len(group)),
                "statistics_recomputed_from_merged_raw_samples": True,
                "chainage_start": _edge_value(segment_block["chainage"], first=True),
                "chainage_end": _edge_value(segment_block["chainage"], first=False),
                "source_file": str(segment_block["source_file"].iloc[0]),
                "source_row_start": int(segment_block["source_row"].iloc[0]),
                "source_row_end": int(segment_block["source_row"].iloc[-1]),
                **descriptor,
            }
            parameter_segments.append(row)
        parameter_segments = _coalesce_recomputed_segments(
            block,
            parameter_segments,
            column=column,
            config=config,
        )
        for row in parameter_segments:
            segment_block = block[
                block["time"].between(pd.Timestamp(row["start_time"]), pd.Timestamp(row["end_time"]))
            ]
            outliers.extend(_local_outlier_rows(segment_block, row, column, config))
        segments.extend(parameter_segments)

        for left, right in zip(parameter_segments, parameter_segments[1:]):
            if _semantic_signature(left) == _semantic_signature(right):
                continue
            transitions.append(
                {
                    "date": scope["date"],
                    "interval_id": scope["interval_id"],
                    "phase_id": scope["phase_id"],
                    "phase_label": scope["phase_label"],
                    "parameter": parameter,
                    "transition_time": right["start_time"],
                    "from_trend": left["trend"],
                    "to_trend": right["trend"],
                    "from_fluctuation_level": left["fluctuation_level"],
                    "to_fluctuation_level": right["fluctuation_level"],
                    "boundary_method": "local_slope_contrast_near_window_change",
                    "source_file": right["source_file"],
                    "source_row": right["source_row_start"],
                }
            )
    return segments, transitions, outliers


def _coalesce_recomputed_segments(
    block: pd.DataFrame,
    segments: list[dict[str, Any]],
    *,
    column: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not segments:
        return []
    merged = [segments[0].copy()]
    for segment in segments[1:]:
        previous = merged[-1]
        gap = (pd.Timestamp(segment["start_time"]) - pd.Timestamp(previous["end_time"])).total_seconds()
        if _semantic_signature(previous) != _semantic_signature(segment) or gap > float(
            config["maximum_sample_duration_seconds"]
        ):
            merged.append(segment.copy())
            continue
        combined = block[
            block["time"].between(
                pd.Timestamp(previous["start_time"]),
                pd.Timestamp(segment["end_time"]),
            )
        ]
        valid = combined[["time", column]].dropna()
        descriptor = _describe_valid(valid, column, config)
        previous.update(
            {
                "end_time": iso(combined["time"].iloc[-1]),
                "duration_seconds": safe_number(_duration(combined)),
                "merged_window_count": int(previous["merged_window_count"])
                + int(segment["merged_window_count"]),
                "statistics_recomputed_from_merged_raw_samples": True,
                "chainage_end": _edge_value(combined["chainage"], first=False),
                "source_row_end": int(combined["source_row"].iloc[-1]),
                **descriptor,
            }
        )
    return merged


def _refine_transition(
    block: pd.DataFrame,
    *,
    column: str,
    left_start: pd.Timestamp,
    rough_boundary: pd.Timestamp,
    right_end: pd.Timestamp,
    config: dict[str, Any],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    valid = block.loc[block["time"].between(left_start, right_end), ["time", column]].dropna()
    if len(valid) < 8:
        return rough_boundary, pd.Timestamp(valid["time"].iloc[-1]) if len(valid) == 1 else pd.Timestamp(rough_boundary)
    times = valid["time"].reset_index(drop=True)
    values = valid[column].to_numpy(dtype=float)
    rough_index = int(np.argmin(np.abs((times - rough_boundary).dt.total_seconds().to_numpy(dtype=float))))
    radius = float(config["dynamic_window_seconds"])
    eligible = [
        index
        for index in range(3, len(valid) - 3)
        if abs((times.iloc[index] - rough_boundary).total_seconds()) <= radius
    ]
    if not eligible:
        split = min(max(rough_index, 1), len(valid) - 1)
    else:
        x = valid["time"].sub(valid["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
        scores = []
        for index in eligible:
            left_slope = np.polyfit(x[:index], values[:index], 1)[0]
            right_slope = np.polyfit(x[index:], values[index:], 1)[0]
            proximity = abs(index - rough_index) / max(len(valid), 1)
            scores.append((abs(left_slope - right_slope) - 0.05 * proximity, index))
        split = max(scores)[1]
    return pd.Timestamp(times.iloc[split - 1]), pd.Timestamp(times.iloc[split])


def _local_outlier_rows(
    block: pd.DataFrame,
    segment: dict[str, Any],
    column: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    values = pd.to_numeric(block[column], errors="coerce")
    valid = values.dropna()
    if len(valid) < int(config["minimum_dynamic_samples"]):
        return []
    median = float(valid.median())
    mad = float((valid - median).abs().median())
    if not np.isfinite(mad) or mad <= 1e-9:
        return []
    robust_z = (values - median).abs() / (1.4826 * mad)
    rows = []
    for index in block.index[robust_z >= float(config["local_outlier_robust_z"])]:
        rows.append(
            {
                "event_type": "time_series_local_outlier",
                "interpretation": "local_parameter_deviation_not_data_quality_or_fault_diagnosis",
                "date": segment["date"],
                "interval_id": segment["interval_id"],
                "phase_id": segment["phase_id"],
                "phase_label": segment["phase_label"],
                "parameter": segment["parameter"],
                "time": iso(block.loc[index, "time"]),
                "value": safe_number(values.loc[index]),
                "segment_median": safe_number(median),
                "robust_z": safe_number(robust_z.loc[index]),
                "chainage": safe_number(block.loc[index, "chainage"]),
                "source_file": str(block.loc[index, "source_file"]),
                "source_row": int(block.loc[index, "source_row"]),
            }
        )
    return rows


def _coupled_events(
    segments: list[dict[str, Any]],
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for segment in segments:
        grouped.setdefault((segment["interval_id"], segment["phase_id"]), []).append(segment)
    for (interval_id, phase_id), items in grouped.items():
        boundaries = sorted(
            {pd.Timestamp(item["start_time"]) for item in items}
            | {pd.Timestamp(item["end_time"]) for item in items}
        )
        for start, end in zip(boundaries, boundaries[1:]):
            duration = (end - start).total_seconds()
            if duration < float(config["coupled_event_minimum_overlap_seconds"]):
                continue
            active = [
                item
                for item in items
                if pd.Timestamp(item["start_time"]) <= start
                and pd.Timestamp(item["end_time"]) >= end
            ]
            by_parameter = {item["parameter"]: item for item in active}
            if len(by_parameter) < 2:
                continue
            if not any(
                item["trend"] not in {"stable", "insufficient_data"}
                or item["fluctuation_level"] in {"moderate", "high"}
                for item in by_parameter.values()
            ):
                continue
            sample = frame[frame["time"].between(start, end)]
            if sample.empty:
                continue
            parameter_states = _coupled_parameter_states(
                sample,
                sorted(by_parameter),
                config,
            )
            if not any(
                state["trend"] not in {"stable", "insufficient_data"}
                or state["fluctuation_level"] in {"moderate", "high"}
                for state in parameter_states.values()
            ):
                continue
            candidates.append(
                {
                    "event_type": "coupled_parameter_response",
                    "interpretation": "overlapping_machine_response_not_causal_diagnosis",
                    "date": items[0]["date"],
                    "interval_id": interval_id,
                    "phase_id": phase_id,
                    "phase_label": items[0]["phase_label"],
                    "start_time": iso(sample["time"].iloc[0]),
                    "end_time": iso(sample["time"].iloc[-1]),
                    "duration_seconds": safe_number(_duration(sample)),
                    "parameter_states": parameter_states,
                    "statistics_recomputed_from_event_raw_samples": True,
                    "chainage_start": _edge_value(sample["chainage"], first=True),
                    "chainage_end": _edge_value(sample["chainage"], first=False),
                    "source_file": str(sample["source_file"].iloc[0]),
                    "source_row_start": int(sample["source_row"].iloc[0]),
                    "source_row_end": int(sample["source_row"].iloc[-1]),
                }
            )
    merged: list[dict[str, Any]] = []
    for event in sorted(candidates, key=lambda row: (row["phase_id"], row["start_time"])):
        signature = _coupled_signature(event)
        if merged:
            previous = merged[-1]
            gap = (pd.Timestamp(event["start_time"]) - pd.Timestamp(previous["end_time"])).total_seconds()
            if previous["phase_id"] == event["phase_id"] and _coupled_signature(previous) == signature and gap <= float(
                config["maximum_sample_duration_seconds"]
            ):
                combined = frame[
                    frame["time"].between(
                        pd.Timestamp(previous["start_time"]),
                        pd.Timestamp(event["end_time"]),
                    )
                ]
                previous["end_time"] = iso(combined["time"].iloc[-1])
                previous["duration_seconds"] = safe_number(_duration(combined))
                previous["parameter_states"] = _coupled_parameter_states(
                    combined,
                    sorted(previous["parameter_states"]),
                    config,
                )
                previous["statistics_recomputed_from_event_raw_samples"] = True
                previous["chainage_end"] = _edge_value(combined["chainage"], first=False)
                previous["source_row_end"] = int(combined["source_row"].iloc[-1])
                continue
        merged.append(event.copy())
    for index, event in enumerate(merged, start=1):
        event["coupled_event_id"] = f"{event['phase_id']}-M{index:04d}"
    return merged


def _coupled_parameter_states(
    sample: pd.DataFrame,
    parameters: list[str],
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for name in parameters:
        column = PARAMETERS[name]
        valid = sample[["time", column]].dropna()
        descriptor = _describe_valid(valid, column, config)
        states[name] = {
            "trend": descriptor["trend"],
            "fluctuation_level": descriptor["fluctuation_level"],
            "median": descriptor["median"],
            "slope_per_minute": descriptor["slope_per_minute"],
            "absolute_change": descriptor["absolute_change"],
            "relative_change": descriptor["relative_change"],
            "sample_count": descriptor["sample_count"],
        }
    return states


def _describe_valid(valid: pd.DataFrame, column: str, config: dict[str, Any]) -> dict[str, Any]:
    if len(valid) < int(config["minimum_dynamic_samples"]):
        value = safe_number(valid[column].median()) if not valid.empty else None
        return {
            "trend": "insufficient_data",
            "slope_per_minute": None,
            "spearman_r": None,
            "initial_median": value,
            "final_median": value,
            "absolute_change": None,
            "relative_change": None,
            "residual_mad": None,
            "fluctuation_ratio": None,
            "persistent_fluctuation": False,
            "fluctuation_level": "insufficient_data",
            "median": value,
            "p10": value,
            "p90": value,
            "iqr": None,
            "mean": value,
            "std": None,
            "minimum": value,
            "maximum": value,
            "local_outlier_count": 0,
            "sample_count": int(len(valid)),
        }
    return trend_descriptor(valid["time"], valid[column], config)


def _semantic_signature(row: dict[str, Any]) -> tuple[str, bool]:
    return str(row["trend"]), bool(row.get("persistent_fluctuation"))


def _coupled_signature(row: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (name, value["trend"], value["fluctuation_level"])
            for name, value in row["parameter_states"].items()
        )
    )


def _duration(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    observed = (frame["time"].iloc[-1] - frame["time"].iloc[0]).total_seconds()
    differences = frame["time"].diff().dt.total_seconds().dropna()
    tail = float(differences.median()) if not differences.empty else 0.0
    return float(observed + max(tail, 0.0))


def _edge_value(values: pd.Series, *, first: bool) -> float | None:
    valid = values.dropna()
    if valid.empty:
        return None
    return safe_number(valid.iloc[0] if first else valid.iloc[-1])
