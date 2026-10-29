from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, theilslopes

from ..ingestion.io import iso, safe_number
from .registry import load_dynamic_parameter_columns


PARAMETERS = load_dynamic_parameter_columns()


def analyze_phases(
    frame: pd.DataFrame,
    phase_rows: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    descriptors: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for phase in phase_rows:
        block = frame[
            frame["time"].between(pd.Timestamp(phase["start_time"]), pd.Timestamp(phase["end_time"]))
        ]
        phase_descriptors = describe_block(block, phase_id=phase["phase_id"], config=config)
        descriptors.extend(phase_descriptors)
        events.extend(_window_events(block, phase=phase, config=config))
    return descriptors, merge_adjacent_events(events, config=config)


def describe_block(
    block: pd.DataFrame,
    *,
    phase_id: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for name, column in PARAMETERS.items():
        if column not in block.columns:
            continue
        valid = block[["time", column]].dropna()
        if len(valid) < int(config["minimum_dynamic_samples"]):
            continue
        descriptor = trend_descriptor(valid["time"], valid[column], config)
        rows.append(
            {
                "date": str(valid["time"].iloc[0].date()),
                "phase_id": phase_id,
                "parameter": name,
                "start_time": iso(valid["time"].iloc[0]),
                "end_time": iso(valid["time"].iloc[-1]),
                **descriptor,
            }
        )
    return rows


def _window_events(
    block: pd.DataFrame,
    *,
    phase: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if block.empty:
        return []
    width = float(config["dynamic_window_seconds"])
    start = block["time"].iloc[0]
    finish = block["time"].iloc[-1]
    events = []
    cursor = start
    while cursor <= finish:
        end = min(cursor + pd.Timedelta(seconds=width), finish)
        window = block[block["time"].between(cursor, end)]
        changes: dict[str, dict[str, Any]] = {}
        for name, column in PARAMETERS.items():
            valid = window[["time", column]].dropna()
            if len(valid) < int(config["minimum_dynamic_samples"]):
                continue
            descriptor = trend_descriptor(valid["time"], valid[column], config)
            if descriptor["trend"] != "stable" or descriptor["persistent_fluctuation"]:
                changes[name] = descriptor
        changing = [name for name, value in changes.items() if value["trend"] != "stable"]
        fluctuating = [name for name, value in changes.items() if value["persistent_fluctuation"]]
        if len(changing) >= 2 or fluctuating:
            event_type = "multi_parameter_change" if len(changing) >= 2 else "persistent_fluctuation"
            events.append(
                {
                    "event_type": "construction_response",
                    "subtype": event_type,
                    "date": phase["date"],
                    "interval_id": phase["interval_id"],
                    "phase_id": phase["phase_id"],
                    "phase_label": phase["phase_label"],
                    "start_time": iso(window["time"].iloc[0]),
                    "end_time": iso(window["time"].iloc[-1]),
                    "duration_seconds": safe_number(
                        (window["time"].iloc[-1] - window["time"].iloc[0]).total_seconds()
                    ),
                    "parameter_changes": {
                        name: {
                            "trend": value["trend"],
                            "relative_change": value["relative_change"],
                            "slope_per_minute": value["slope_per_minute"],
                            "spearman_r": value["spearman_r"],
                            "persistent_fluctuation": value["persistent_fluctuation"],
                            "fluctuation_ratio": value["fluctuation_ratio"],
                        }
                        for name, value in changes.items()
                    },
                    "chainage_start": safe_number(window["chainage"].dropna().iloc[0]) if window["chainage"].notna().any() else None,
                    "chainage_end": safe_number(window["chainage"].dropna().iloc[-1]) if window["chainage"].notna().any() else None,
                    "source_file": str(window["source_file"].iloc[0]),
                    "source_row_start": int(window["source_row"].iloc[0]),
                    "source_row_end": int(window["source_row"].iloc[-1]),
                    "interpretation": "observed_machine_response_not_geology_or_fault_diagnosis",
                }
            )
        cursor = end + pd.Timedelta(microseconds=1)
    return events


def trend_descriptor(time: pd.Series, values: pd.Series, config: dict[str, Any]) -> dict[str, Any]:
    x = time.sub(time.iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
    y = values.to_numpy(dtype=float)
    if len(y) < 2 or np.nanmax(x) <= 0:
        return _empty_descriptor(y)
    slope = float(theilslopes(y, x).slope)
    if len(y) >= 3 and np.nanmax(y) > np.nanmin(y):
        rho = float(spearmanr(x, y).statistic)
    else:
        rho = 0.0
    q10, q90 = np.nanquantile(y, [0.1, 0.9])
    scale = max(float(q90 - q10), abs(float(np.nanmedian(y))) * 0.05, 1e-9)
    quarter = max(1, len(y) // 4)
    start_value = float(np.nanmedian(y[:quarter]))
    end_value = float(np.nanmedian(y[-quarter:]))
    relative_change = (end_value - start_value) / scale
    if (
        abs(relative_change) >= float(config["trend_min_relative_change"])
        and abs(rho) >= float(config["trend_min_spearman_abs"])
    ):
        trend = "increasing" if relative_change > 0 else "decreasing"
    else:
        trend = "stable"
    fitted = np.nanmedian(y) + slope * (x - np.nanmedian(x))
    residual = y - fitted
    residual_mad = float(np.nanmedian(np.abs(residual - np.nanmedian(residual))))
    fluctuation_ratio = residual_mad / scale
    median = float(np.nanmedian(y))
    mad = float(np.nanmedian(np.abs(y - median)))
    robust_scale = max(1.4826 * mad, 1e-9)
    local_outlier_mask = np.abs(y - median) / robust_scale >= float(config["local_outlier_robust_z"])
    if fluctuation_ratio >= float(config["fluctuation_residual_ratio"]):
        fluctuation_level = "high"
    elif fluctuation_ratio >= float(config["fluctuation_moderate_ratio"]):
        fluctuation_level = "moderate"
    else:
        fluctuation_level = "low"
    q25, q75 = np.nanquantile(y, [0.25, 0.75])
    return {
        "trend": trend,
        "slope_per_minute": safe_number(slope),
        "spearman_r": safe_number(rho),
        "initial_median": safe_number(start_value),
        "final_median": safe_number(end_value),
        "absolute_change": safe_number(end_value - start_value),
        "relative_change": safe_number(relative_change),
        "residual_mad": safe_number(residual_mad),
        "fluctuation_ratio": safe_number(fluctuation_ratio),
        "persistent_fluctuation": bool(
            fluctuation_ratio >= float(config["fluctuation_residual_ratio"])
        ),
        "fluctuation_level": fluctuation_level,
        "median": safe_number(median),
        "p10": safe_number(q10),
        "p90": safe_number(q90),
        "iqr": safe_number(q75 - q25),
        "mean": safe_number(np.nanmean(y)),
        "std": safe_number(np.nanstd(y, ddof=1)) if len(y) > 1 else None,
        "minimum": safe_number(np.nanmin(y)),
        "maximum": safe_number(np.nanmax(y)),
        "local_outlier_count": int(local_outlier_mask.sum()),
        "sample_count": int(len(y)),
    }


def _empty_descriptor(y: np.ndarray) -> dict[str, Any]:
    value = safe_number(np.nanmedian(y)) if len(y) else None
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
        "sample_count": int(len(y)),
    }


# Backward-compatible name for existing tests and downstream notebooks.
_trend_descriptor = trend_descriptor


def merge_adjacent_events(
    events: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not events:
        return []
    merged = [events[0].copy()]
    max_gap = float(config["maximum_sample_duration_seconds"])
    for event in events[1:]:
        previous = merged[-1]
        same_phase = event["phase_id"] == previous["phase_id"]
        same_signature = _event_signature(event) == _event_signature(previous)
        gap = (pd.Timestamp(event["start_time"]) - pd.Timestamp(previous["end_time"])).total_seconds()
        if same_phase and same_signature and gap <= max_gap:
            previous["end_time"] = event["end_time"]
            previous["duration_seconds"] = safe_number(
                (pd.Timestamp(previous["end_time"]) - pd.Timestamp(previous["start_time"])).total_seconds()
            )
            previous["chainage_end"] = event.get("chainage_end")
            previous["source_row_end"] = event.get("source_row_end")
        else:
            merged.append(event.copy())
    for index, event in enumerate(merged, start=1):
        event["event_id"] = f"{event['date']}-C{index:03d}"
    return merged


def _event_signature(event: dict[str, Any]) -> tuple[Any, ...]:
    changes = event.get("parameter_changes", {})
    return tuple(sorted((name, value.get("trend"), value.get("persistent_fluctuation")) for name, value in changes.items()))


def select_important_events(
    candidates: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    priority_interval_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    qualified: list[dict[str, Any]] = []
    minimum_duration = float(config["minimum_event_seconds"])
    minimum_effect = float(config["event_min_relative_change"])
    minimum_parameters = int(config["event_min_changing_parameters"])
    for candidate in candidates:
        duration = float(candidate.get("duration_seconds") or 0)
        if duration < minimum_duration:
            continue
        changes = candidate.get("parameter_changes", {})
        changing = {
            name: value
            for name, value in changes.items()
            if value.get("trend") in {"increasing", "decreasing"}
            and abs(float(value.get("relative_change") or 0)) >= minimum_effect
        }
        fluctuating = {
            name: value for name, value in changes.items() if value.get("persistent_fluctuation")
        }
        if len(changing) < minimum_parameters and len(fluctuating) < minimum_parameters:
            continue
        score = (
            sum(min(abs(float(value.get("relative_change") or 0)), 2.0) for value in changing.values())
            + 0.5 * len(fluctuating)
            + min(duration / 600.0, 2.0)
        )
        event = candidate.copy()
        event["importance_score"] = safe_number(score)
        event["selection_reason"] = (
            f"duration>={minimum_duration:g}s;changing={len(changing)};"
            f"fluctuating={len(fluctuating)}"
        )
        qualified.append(event)
    qualified.sort(key=lambda item: float(item.get("importance_score") or 0), reverse=True)
    limit = int(config["daily_construction_event_limit"])
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str, str]] = set()
    for interval_id in priority_interval_ids or []:
        representative = next(
            (event for event in qualified if event.get("interval_id") == interval_id),
            None,
        )
        if representative is None:
            continue
        key = (representative["interval_id"], representative["start_time"], representative["end_time"])
        selected.append(representative)
        selected_keys.add(key)
        if len(selected) >= limit:
            break
    for event in qualified:
        key = (event["interval_id"], event["start_time"], event["end_time"])
        if key in selected_keys:
            continue
        selected.append(event)
        selected_keys.add(key)
        if len(selected) >= limit:
            break
    selected.sort(key=lambda item: item["start_time"])
    for index, event in enumerate(selected, start=1):
        event["event_id"] = f"{event['date']}-E{index:03d}"
    return selected

