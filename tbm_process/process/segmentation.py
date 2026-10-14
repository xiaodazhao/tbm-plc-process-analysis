from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from scipy.stats import spearmanr

from ..ingestion.io import iso, safe_number


PARAMETERS = ["advance_speed", "thrust", "torque", "rpm_canonical"]
PHASE_LABELS = ("startup", "rising", "stable", "declining")


def classify_machine_state(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    state_on = out["raw_state"].fillna(0).ne(0)
    speed_on = out["advance_speed"].gt(float(config["advancing_speed_min"]))
    thrust_on = out["thrust"].gt(float(config["advancing_thrust_min"]))
    torque_on = out["torque"].gt(float(config["advancing_torque_min"]))
    rpm_on = out["rpm_canonical"].gt(float(config["advancing_rpm_min"]))
    advancing = state_on & speed_on & thrust_on & torque_on & rpm_on
    rotating = state_on & ~advancing & rpm_on & torque_on
    thrusting = state_on & ~advancing & ~rotating & thrust_on
    standby = state_on & ~advancing & ~rotating & ~thrusting
    out["machine_state"] = np.select(
        [advancing, rotating, thrusting, standby],
        ["advancing", "free_rotation", "thrusting_no_advance", "standby"],
        default="stopped",
    )

    # Low-frequency samples cannot reliably separate free rotation from free
    # advancing, so both are retained as possible startup observations.
    cycle_candidate = state_on & (speed_on | thrust_on | torque_on | rpm_on)
    out["advance_candidate"] = cycle_candidate
    out["excavation_cycle_candidate"] = cycle_candidate
    out = _bridge_short_cycle_gaps(out, config)
    return out


def _bridge_short_cycle_gaps(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    mask = out["advance_candidate"].to_numpy(dtype=bool)
    limit = float(config["bridge_advance_gap_seconds"])
    for start, end in _bounded_true_runs(out, ~out["advance_candidate"]):
        if start == 0 or end == len(out) - 1 or not mask[start - 1] or not mask[end + 1]:
            continue
        if out["raw_state"].iloc[start : end + 1].fillna(0).eq(0).any():
            continue
        if _has_hard_boundary(out, start, end + 1):
            continue
        block = out.iloc[start : end + 1]
        duration = (block["time"].iloc[-1] - block["time"].iloc[0]).total_seconds()
        boundary_gap = (out["time"].iloc[end + 1] - out["time"].iloc[start - 1]).total_seconds()
        if duration <= limit and boundary_gap <= limit + float(config["maximum_sample_duration_seconds"]):
            mask[start : end + 1] = True
            out.loc[start:end, "machine_state"] = "cycle_bridged_gap"
    out["advance_candidate"] = mask
    out["excavation_cycle_candidate"] = mask
    return out


def extract_advance_intervals(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    date: str,
) -> tuple[list[dict[str, Any]], pd.Series]:
    accepted_mask = pd.Series(False, index=frame.index)
    rows: list[dict[str, Any]] = []
    interval_number = 0
    for parent_number, (parent_start, parent_end) in enumerate(
        _bounded_true_runs(frame, frame["advance_candidate"]), start=1
    ):
        split_ranges = _split_cycle_run(frame, parent_start, parent_end, config)
        for subcycle_number, (start, end, split_before_time, split_rule) in enumerate(split_ranges, start=1):
            block = frame.iloc[start : end + 1]
            duration = _block_duration_seconds(block, config)
            if (
                len(block) < int(config["minimum_advance_interval_samples"])
                or duration < float(config["minimum_advance_interval_seconds"])
            ):
                continue
            interval_number += 1
            interval_id = f"{date}-A{interval_number:03d}"
            accepted_mask.iloc[start : end + 1] = True
            rows.append(
                {
                "date": date,
                "interval_id": interval_id,
                "interval_type": "four_phase_excavation_cycle",
                "parent_candidate_number": int(parent_number),
                "parent_candidate_id": f"{date}-C{parent_number:03d}",
                "subcycle_number": int(subcycle_number),
                "split_from_unload_reload_evidence": bool(len(split_ranges) > 1),
                "split_boundary_before_time": split_before_time,
                "split_boundary_rule": split_rule,
                "start_index": int(start),
                "end_index": int(end),
                "start_time": iso(block["time"].iloc[0]),
                "end_time": iso(block["time"].iloc[-1]),
                "duration_seconds": safe_number(duration),
                "sample_count": int(len(block)),
                "continuity_block_id": int(block["continuity_block_id"].iloc[0]),
                "chainage_start": safe_number(block["chainage"].dropna().iloc[0]) if block["chainage"].notna().any() else None,
                "chainage_end": safe_number(block["chainage"].dropna().iloc[-1]) if block["chainage"].notna().any() else None,
                "source_file": str(block["source_file"].iloc[0]),
                "source_row_start": int(block["source_row"].iloc[0]),
                "source_row_end": int(block["source_row"].iloc[-1]),
                    **parameter_summary(block),
                }
            )
    return rows, accepted_mask


def _split_cycle_run(
    frame: pd.DataFrame,
    start: int,
    end: int,
    config: dict[str, Any],
) -> list[tuple[int, int, str | None, str | None]]:
    """Split a broad candidate only when sustained unloading is bracketed by real loading."""
    block = frame.iloc[start : end + 1]
    loaded = block["machine_state"].eq("advancing").to_numpy(dtype=bool)
    raw_state_on = block["raw_state"].fillna(0).ne(0).to_numpy(dtype=bool)
    speed_low = block["advance_speed"].fillna(0).le(float(config["advancing_speed_min"])).to_numpy(dtype=bool)
    thrust_low = block["thrust"].fillna(0).le(float(config["advancing_thrust_min"])).to_numpy(dtype=bool)
    torque_low = block["torque"].fillna(0).le(float(config["advancing_torque_min"])).to_numpy(dtype=bool)
    rpm_low = block["rpm_canonical"].fillna(0).le(float(config["advancing_rpm_min"])).to_numpy(dtype=bool)
    unloaded = raw_state_on & speed_low & thrust_low & (torque_low | rpm_low)
    loaded_runs = _array_true_runs(loaded)
    split_reasons: dict[int, str] = {}
    for unload_start, unload_end in _array_true_runs(unloaded):
        if _position_duration_seconds(block, unload_start, unload_end) < float(
            config.get("cycle_split_unloaded_seconds", 180.0)
        ):
            continue
        before = [run for run in loaded_runs if run[1] < unload_start]
        after = [run for run in loaded_runs if run[0] > unload_end]
        if not before or not after:
            continue
        if not _loaded_run_qualifies(block, before[-1], config):
            continue
        if not _loaded_run_qualifies(block, after[0], config):
            continue
        split_reasons[start + (unload_start + unload_end) // 2] = "absolute_unload_reload"

    relative_low = np.zeros(len(block), dtype=int)
    signal_columns = ["advance_speed", "thrust", "torque", "rpm_canonical"]
    for column in signal_columns:
        values = pd.to_numeric(block[column], errors="coerce")
        reference = float(values.quantile(0.75)) if values.notna().any() else np.nan
        if np.isfinite(reference) and reference > 0:
            relative_low += values.fillna(np.inf).le(
                float(config.get("cycle_split_relative_level_fraction", 0.5)) * reference
            ).to_numpy(dtype=bool)
    minimum_signals = int(config.get("cycle_split_relative_signal_count", 3))
    shoulder_samples = int(config.get("cycle_split_min_loaded_samples", 6))
    for unload_start, unload_end in _array_true_runs(relative_low >= minimum_signals):
        if _position_duration_seconds(block, unload_start, unload_end) < float(
            config.get("cycle_split_relative_unloaded_seconds", 120.0)
        ):
            continue
        if unload_start < shoulder_samples or unload_end + shoulder_samples >= len(block):
            continue
        before = block.iloc[unload_start - shoulder_samples : unload_start]
        valley = block.iloc[unload_start : unload_end + 1]
        after = block.iloc[unload_end + 1 : unload_end + 1 + shoulder_samples]
        reload_count = 0
        for column in signal_columns:
            valley_level = float(pd.to_numeric(valley[column], errors="coerce").median())
            before_level = float(pd.to_numeric(before[column], errors="coerce").median())
            after_level = float(pd.to_numeric(after[column], errors="coerce").median())
            reference = max(valley_level, 1e-9)
            ratio = float(config.get("cycle_split_reload_ratio", 1.5))
            if np.isfinite(before_level) and np.isfinite(after_level) and before_level >= ratio * reference and after_level >= ratio * reference:
                reload_count += 1
        if reload_count >= minimum_signals:
            split_reasons.setdefault(
                start + (unload_start + unload_end) // 2,
                "relative_multivariate_unload_reload",
            )

    split_positions = sorted(split_reasons)
    boundaries = [start] + [position + 1 for position in split_positions] + [end + 1]
    ranges: list[tuple[int, int, str | None, str | None]] = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        before_time = iso(frame["time"].iloc[left]) if index > 0 else None
        rule = split_reasons.get(split_positions[index - 1]) if index > 0 else None
        ranges.append((left, right - 1, before_time, rule))
    return ranges


def _loaded_run_qualifies(
    block: pd.DataFrame,
    run: tuple[int, int],
    config: dict[str, Any],
) -> bool:
    start, end = run
    return bool(
        end - start + 1 >= int(config.get("cycle_split_min_loaded_samples", 6))
        and _position_duration_seconds(block, start, end) >= float(config.get("cycle_split_min_loaded_seconds", 120.0))
    )


def segment_interval_phases(
    block: pd.DataFrame,
    *,
    interval_id: str,
    config: dict[str, Any],
    terminal_stop_time: pd.Timestamp | None = None,
) -> tuple[pd.Series, list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    empty = pd.Series(None, index=block.index, dtype=object)
    diagnostics: dict[str, Any] = {
        "segmentation_status": "no_reliable_stable_phase",
        "no_stable_reasons": [],
        "penetration_signal_source": "unavailable",
        "knee_candidate_count": 0,
        "stable_candidate_count": 0,
    }
    if block.empty:
        diagnostics["no_stable_reasons"] = ["empty_cycle"]
        return empty, [], diagnostics, []

    signals, penetration_source = _phase_signals(block)
    diagnostics["penetration_signal_source"] = penetration_source
    minimum_samples = int(config["stable_minimum_samples"])
    missing_reasons = []
    if len(block) < minimum_samples:
        missing_reasons.append("insufficient_cycle_samples")
    for column in ("thrust", "torque", "penetration"):
        if int(signals[column].notna().sum()) < minimum_samples:
            missing_reasons.append(f"insufficient_{column}_samples")
    if missing_reasons:
        diagnostics["no_stable_reasons"] = missing_reasons
        return empty, [], diagnostics, []

    duration = max((block["time"].iloc[-1] - block["time"].iloc[0]).total_seconds(), 0.0)
    smooth_seconds = np.clip(
        duration * float(config["score_kneedle_smoothing_fraction"]),
        float(config["phase_smoothing_min_seconds"]),
        float(config["phase_smoothing_max_seconds"]),
    )
    normalized = pd.DataFrame(index=block.index)
    smoothed = pd.DataFrame(index=block.index)
    for column in signals:
        normalized[column] = _robust_normalize(
            signals[column],
            time=block["time"],
            max_gap_seconds=float(config.get("phase_signal_interpolation_max_seconds", 40.0)),
        )
        smoothed[column] = _time_rolling_median(block["time"], normalized[column], smooth_seconds)

    elapsed = block["time"].sub(block["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    knee_strengths = _score_kneedle_candidates(elapsed, smoothed, config)
    diagnostics["knee_candidate_count"] = int(len(knee_strengths))
    stable_runs, stable_metrics = _stable_runs(block, smoothed, config)
    excavation_on = pd.to_numeric(block["raw_state"], errors="coerce").fillna(0.0).ne(0.0)
    stable_metrics["stable_mask"] = np.asarray(stable_metrics["stable_mask"], dtype=bool) & excavation_on.to_numpy(
        dtype=bool
    )
    stable_runs = [
        (start, end)
        for start, end in _array_true_runs(stable_metrics["stable_mask"])
        if end - start + 1 >= int(config["stable_minimum_samples"])
        and _position_duration_seconds(block, start, end) >= float(config["stable_minimum_seconds"])
    ]
    candidate_rows = _stable_candidate_rows(
        block,
        interval_id=interval_id,
        metrics=stable_metrics,
        knees=knee_strengths,
        config=config,
    )
    diagnostics["stable_candidate_count"] = int(len(stable_runs))
    if not stable_runs:
        diagnostics["no_stable_reasons"] = _stable_failure_reasons(stable_metrics, config)
        return empty, [], diagnostics, candidate_rows

    stable_core_start, stable_core_end, stable_score = _select_stable_run(
        block,
        smoothed,
        stable_runs,
        stable_metrics,
        knee_strengths,
        config,
    )
    stable_core_start = _validated_snap_boundary_to_knee(
        block,
        stable_core_start,
        knee_strengths,
        stable_metrics["stable_mask"],
        float(config["knee_snap_seconds"]),
    )
    stable_core_start = max(0, min(stable_core_start, stable_core_end))
    stable_phase_start = _loading_plateau_start(
        block,
        signals,
        stable_core_start=stable_core_start,
        stable_core_end=stable_core_end,
        config=config,
    )

    rising_run, rising_evidence = _ordered_rising_run(
        block,
        signals,
        smoothed,
        stable_start=stable_phase_start,
        reference_start=stable_core_start,
        reference_end=stable_core_end,
        config=config,
    )
    declining_run, decline_evidence = _terminal_declining_run(
        block,
        smoothed,
        stable_end=stable_core_end,
        knees=knee_strengths,
        terminal_stop_time=terminal_stop_time,
        config=config,
    )

    labels = np.full(len(block), None, dtype=object)
    decline_start = declining_run[0] if declining_run is not None else len(block)
    stable_phase_end = max(stable_phase_start, decline_start - 1)
    labels[stable_phase_start : stable_phase_end + 1] = "stable"
    if rising_run is not None:
        rising_start, _ = rising_run
        startup_end = rising_start - 1
        if startup_end >= 0 and _position_duration_seconds(block, 0, startup_end) >= float(
            config["transition_minimum_seconds"]
        ):
            labels[: startup_end + 1] = "startup"
        else:
            rising_start = 0
        labels[rising_start:stable_phase_start] = "rising"
    if declining_run is not None:
        labels[declining_run[0] :] = "declining"

    abrupt_terminal_decline = decline_evidence.get("boundary_type") == "state_1_to_0"
    labels = _remove_undersampled_transitions(
        block,
        labels,
        config,
        preserve_terminal_decline=abrupt_terminal_decline,
    )
    phase_series = pd.Series(labels, index=block.index, dtype=object)
    rows = _phase_rows(
        block,
        phase_series,
        interval_id=interval_id,
        stable_score=stable_score,
        rising_evidence=rising_evidence,
        decline_evidence=decline_evidence,
        penetration_source=penetration_source,
        smoothing_seconds=float(smooth_seconds),
        config=config,
    )
    if not any(row["phase_label"] == "stable" for row in rows):
        diagnostics["no_stable_reasons"] = ["stable_removed_by_minimum_duration_rule"]
        return empty, [], diagnostics, candidate_rows
    diagnostics.update(
        {
            "segmentation_status": "stable_phase_identified",
            "no_stable_reasons": [],
            "unlabeled_sample_count": int(phase_series.isna().sum()),
            "stable_start_time": iso(block["time"].iloc[stable_core_start]),
            "stable_end_time": iso(block["time"].iloc[stable_core_end]),
            "stable_phase_start_time": iso(block["time"].iloc[stable_phase_start]),
            "stable_phase_end_time": iso(block["time"].iloc[stable_phase_end]),
            "rising_start_time": (
                iso(block["time"].iloc[rising_run[0]]) if rising_run is not None else None
            ),
            "decline_boundary_type": decline_evidence.get("boundary_type"),
            "terminal_stop_time": iso(terminal_stop_time) if terminal_stop_time is not None else None,
            "stable_selection_score": safe_number(stable_score),
        }
    )
    return phase_series, rows, diagnostics, candidate_rows


def parameter_summary(frame: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name in PARAMETERS:
        values = pd.to_numeric(frame[name], errors="coerce").dropna()
        prefix = "rpm" if name == "rpm_canonical" else name
        summary[f"{prefix}_count"] = int(len(values))
        summary[f"{prefix}_p10"] = safe_number(values.quantile(0.1)) if not values.empty else None
        summary[f"{prefix}_p50"] = safe_number(values.median()) if not values.empty else None
        summary[f"{prefix}_p90"] = safe_number(values.quantile(0.9)) if not values.empty else None
        summary[f"{prefix}_mean"] = safe_number(values.mean()) if not values.empty else None
        summary[f"{prefix}_std"] = safe_number(values.std()) if len(values) > 1 else None
    return summary


def rebuild_phase_rows_from_assignments(
    frame: pd.DataFrame,
    phase_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Recompute phase summaries from the final one-row/one-phase assignments."""
    rebuilt: list[dict[str, Any]] = []
    for row in phase_rows:
        phase = frame[
            frame["interval_id"].eq(row["interval_id"])
            & frame["phase_id"].eq(row["phase_id"])
            & frame["phase_label"].eq(row["phase_label"])
        ]
        if phase.empty:
            continue
        updated = dict(row)
        updated.update(
            {
                "date": str(phase["time"].iloc[0].date()),
                "start_time": iso(phase["time"].iloc[0]),
                "end_time": iso(phase["time"].iloc[-1]),
                "duration_seconds": safe_number(_block_duration_seconds(phase, config)),
                "sample_count": int(len(phase)),
                "chainage_start": (
                    safe_number(phase["chainage"].dropna().iloc[0])
                    if phase["chainage"].notna().any()
                    else None
                ),
                "chainage_end": (
                    safe_number(phase["chainage"].dropna().iloc[-1])
                    if phase["chainage"].notna().any()
                    else None
                ),
                "source_file": str(phase["source_file"].iloc[0]),
                "source_row_start": int(phase["source_row"].iloc[0]),
                "source_row_end": int(phase["source_row"].iloc[-1]),
                **_phase_quality_coverage(phase),
                **parameter_summary(phase),
            }
        )
        rebuilt.append(updated)
    return rebuilt


def _phase_signals(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    signals = pd.DataFrame(index=frame.index)
    signals["thrust"] = pd.to_numeric(frame["thrust"], errors="coerce")
    signals["torque"] = pd.to_numeric(frame["torque"], errors="coerce")
    controlled = pd.to_numeric(frame.get("penetration_analysis"), errors="coerce")
    speed = pd.to_numeric(frame.get("advance_speed"), errors="coerce")
    controlled = controlled.where(controlled.ge(0))
    if _usable_signal(controlled, len(frame)):
        signals["penetration"] = controlled
        sources = frame.loc[controlled.notna(), "penetration_analysis_source"].dropna().unique()
        source = "+".join(sorted(map(str, sources))) if len(sources) else "quality_controlled_penetration"
    else:
        signals["penetration"] = speed
        source = "quality_controlled_advance_speed_fallback"
    return signals, source


def _usable_signal(values: pd.Series, length: int) -> bool:
    valid = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(valid) < max(3, int(np.ceil(length * 0.4))):
        return False
    q10, q90 = valid.quantile([0.1, 0.9])
    return bool(np.isfinite(q10) and np.isfinite(q90) and q90 > q10)


def _robust_normalize(
    values: pd.Series,
    *,
    time: pd.Series | None = None,
    max_gap_seconds: float | None = None,
) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=values.index, dtype=float)
    low, high = valid.quantile([0.1, 0.9])
    span = float(high - low)
    if not np.isfinite(span) or span <= 1e-9:
        result = pd.Series(np.nan, index=values.index, dtype=float)
        result.loc[numeric.notna()] = 0.5
        return result
    result = ((numeric - low) / span).clip(0, 1)
    if time is not None and max_gap_seconds is not None:
        result = _interpolate_short_gaps(time, result, max_gap_seconds)
    return result


def _interpolate_short_gaps(time: pd.Series, values: pd.Series, max_seconds: float) -> pd.Series:
    result = values.copy()
    missing = result.isna().to_numpy(dtype=bool)
    for start, end in _array_true_runs(missing):
        if start == 0 or end == len(result) - 1:
            continue
        elapsed = (pd.Timestamp(time.iloc[end + 1]) - pd.Timestamp(time.iloc[start - 1])).total_seconds()
        if elapsed > max_seconds:
            continue
        left = float(result.iloc[start - 1])
        right = float(result.iloc[end + 1])
        times = pd.to_datetime(time.iloc[start - 1 : end + 2])
        denominator = max((times.iloc[-1] - times.iloc[0]).total_seconds(), 1e-9)
        for position in range(start, end + 1):
            fraction = (pd.Timestamp(time.iloc[position]) - times.iloc[0]).total_seconds() / denominator
            result.iloc[position] = left + fraction * (right - left)
    return result


def _score_kneedle_candidates(
    elapsed: np.ndarray,
    signals: pd.DataFrame,
    config: dict[str, Any],
) -> dict[int, float]:
    if len(elapsed) < 5 or elapsed[-1] <= elapsed[0]:
        return {}
    x = (elapsed - elapsed[0]) / max(elapsed[-1] - elapsed[0], 1e-9)
    typical_dt = np.nanmedian(np.diff(elapsed)) if len(elapsed) > 1 else 1.0
    minimum_distance = max(1, int(float(config["knee_minimum_distance_seconds"]) / max(typical_dt, 1.0)))
    prominence = float(config["kneedle_minimum_prominence"])
    strengths: dict[int, float] = {}
    for column in signals:
        y = signals[column].to_numpy(dtype=float)
        if not np.isfinite(y).any() or np.nanmax(y) - np.nanmin(y) <= 1e-9:
            continue
        y = (y - np.nanmin(y)) / max(np.nanmax(y) - np.nanmin(y), 1e-9)
        for transformed in (y, 1.0 - y):
            for difference in (transformed - x, x - transformed):
                peaks, properties = find_peaks(
                    difference,
                    prominence=prominence,
                    distance=minimum_distance,
                )
                for position, strength in zip(peaks, properties.get("prominences", []), strict=True):
                    strengths[int(position)] = strengths.get(int(position), 0.0) + float(strength)

        gradient = np.gradient(y, np.maximum.accumulate(elapsed + np.arange(len(elapsed)) * 1e-6))
        curvature = np.abs(np.gradient(gradient, np.maximum.accumulate(elapsed + np.arange(len(elapsed)) * 1e-6)))
        if np.isfinite(curvature).any():
            finite = curvature[np.isfinite(curvature)]
            center = np.nanmedian(finite)
            threshold = float(center + 2.5 * np.nanmedian(np.abs(finite - center)))
            for position in np.flatnonzero(curvature >= max(threshold, 1e-9)):
                strengths[int(position)] = strengths.get(int(position), 0.0) + 0.05
    return strengths


def _stable_runs(
    block: pd.DataFrame,
    signals: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[list[tuple[int, int]], dict[str, np.ndarray]]:
    elapsed = block["time"].sub(block["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    width = float(config["stable_window_seconds"])
    minimum_window_samples = int(config["stable_window_minimum_samples"])
    std_limit = float(config["stable_normalized_std_max"])
    slope_limit = float(config["stable_normalized_slope_per_minute_max"])
    metrics: dict[str, np.ndarray] = {}
    masks = []
    stability_columns = ("thrust", "torque", "penetration")
    for column in stability_columns:
        std, slope = _rolling_std_and_slope(elapsed, signals[column].to_numpy(dtype=float), width, minimum_window_samples)
        metrics[f"{column}_std"] = std
        metrics[f"{column}_slope"] = slope
        mask = (std <= std_limit) & (np.abs(slope) <= slope_limit)
        masks.append(mask)
        metrics[f"{column}_stable_mask"] = mask
    activity = signals[["thrust", "torque", "penetration"]].median(axis=1).to_numpy(dtype=float)
    activity_mask = activity >= float(config["stable_minimum_activity_level"])
    vote_count = np.sum(np.vstack(masks), axis=0)
    required_votes = int(config.get("stable_required_signal_count", 2))
    point_evidence = (vote_count >= required_votes) & activity_mask
    occupancy = _time_rolling_fraction(
        block["time"],
        point_evidence,
        float(config.get("stable_occupancy_window_seconds", width)),
        minimum_window_samples,
    )
    stable = (occupancy >= float(config.get("stable_minimum_occupancy", 0.5))) & activity_mask
    positive_advance_mask = (
        block["machine_state"].eq("advancing").to_numpy(dtype=bool)
        if "machine_state" in block
        else activity_mask
    )
    stable = _bridge_boolean_gaps(
        block,
        stable,
        float(config["stable_bridge_gap_seconds"]),
        required_mask=positive_advance_mask,
    )
    metrics["activity"] = activity
    metrics["activity_mask"] = activity_mask
    metrics["stability_vote_count"] = vote_count
    metrics["stability_occupancy"] = occupancy
    metrics["stable_mask"] = stable

    runs = []
    for start, end in _array_true_runs(stable):
        sample_count = end - start + 1
        duration = _position_duration_seconds(block, start, end)
        if (
            sample_count >= int(config["stable_minimum_samples"])
            and duration >= float(config["stable_minimum_seconds"])
        ):
            runs.append((start, end))
    return runs, metrics


def _rolling_std_and_slope(
    elapsed: np.ndarray,
    values: np.ndarray,
    width_seconds: float,
    minimum_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    std = np.full(len(values), np.inf, dtype=float)
    slope = np.full(len(values), np.inf, dtype=float)
    half = width_seconds / 2.0
    for index, center in enumerate(elapsed):
        positions = np.flatnonzero(np.abs(elapsed - center) <= half)
        valid = positions[np.isfinite(values[positions])]
        if len(valid) < minimum_samples:
            continue
        x = (elapsed[valid] - elapsed[valid][0]) / 60.0
        y = values[valid]
        std[index] = float(np.std(y, ddof=1)) if len(y) > 1 else 0.0
        slope[index] = float(np.polyfit(x, y, 1)[0]) if np.ptp(x) > 0 else 0.0
    return std, slope


def _time_rolling_fraction(
    time: pd.Series,
    values: np.ndarray,
    width_seconds: float,
    minimum_samples: int,
) -> np.ndarray:
    indexed = pd.Series(
        np.asarray(values, dtype=float),
        index=pd.DatetimeIndex(time),
    )
    fraction = indexed.rolling(
        f"{max(1, int(width_seconds))}s",
        center=True,
        min_periods=minimum_samples,
    ).mean()
    return fraction.to_numpy(dtype=float)


def _select_stable_run(
    block: pd.DataFrame,
    signals: pd.DataFrame,
    runs: list[tuple[int, int]],
    metrics: dict[str, np.ndarray],
    knees: dict[int, float],
    config: dict[str, Any],
) -> tuple[int, int, float]:
    best: tuple[float, int, int] | None = None
    total_duration = max(_position_duration_seconds(block, 0, len(block) - 1), 1.0)
    std_limit = float(config["stable_normalized_std_max"])
    slope_limit = float(config["stable_normalized_slope_per_minute_max"])
    for start, end in runs:
        duration_score = _position_duration_seconds(block, start, end) / total_duration
        std_values = np.r_[
            metrics["thrust_std"][start : end + 1],
            metrics["torque_std"][start : end + 1],
            metrics["penetration_std"][start : end + 1],
        ]
        slope_values = np.r_[
            np.abs(metrics["thrust_slope"][start : end + 1]),
            np.abs(metrics["torque_slope"][start : end + 1]),
            np.abs(metrics["penetration_slope"][start : end + 1]),
        ]
        stability_score = 1.0 - 0.5 * min(float(np.nanmedian(std_values)) / max(std_limit, 1e-9), 1.0)
        stability_score -= 0.5 * min(float(np.nanmedian(slope_values)) / max(slope_limit, 1e-9), 1.0)
        level_score = float(np.nanmedian(metrics["activity"][start : end + 1]))
        rise = _directional_evidence(block, signals, max(0, start - 8), start, direction=1)
        knee_score = max((strength for position, strength in knees.items() if abs(position - start) <= 3), default=0.0)
        score = 2.0 * duration_score + stability_score + 0.5 * level_score + 0.25 * float(rise["score"]) + 0.25 * knee_score
        if best is None or score > best[0]:
            best = (score, start, end)
    assert best is not None
    return best[1], best[2], best[0]


def _stable_candidate_rows(
    block: pd.DataFrame,
    *,
    interval_id: str,
    metrics: dict[str, np.ndarray],
    knees: dict[int, float],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stable_mask = metrics.get("stable_mask", np.zeros(len(block), dtype=bool))
    for number, (start, end) in enumerate(_array_true_runs(stable_mask), start=1):
        duration = _position_duration_seconds(block, start, end)
        sample_count = end - start + 1
        rows.append(
            {
                "date": str(block["time"].iloc[start].date()),
                "interval_id": interval_id,
                "candidate_id": f"{interval_id}-S{number:02d}",
                "start_time": iso(block["time"].iloc[start]),
                "end_time": iso(block["time"].iloc[end]),
                "duration_seconds": safe_number(duration),
                "sample_count": int(sample_count),
                "qualifies_minimum_duration": bool(
                    duration >= float(config["stable_minimum_seconds"])
                    and sample_count >= int(config["stable_minimum_samples"])
                ),
                "activity_median": safe_number(np.nanmedian(metrics["activity"][start : end + 1])),
                "thrust_std_median": safe_number(np.nanmedian(metrics["thrust_std"][start : end + 1])),
                "torque_std_median": safe_number(np.nanmedian(metrics["torque_std"][start : end + 1])),
                "penetration_std_median": safe_number(
                    np.nanmedian(metrics["penetration_std"][start : end + 1])
                ),
                "thrust_abs_slope_median": safe_number(
                    np.nanmedian(np.abs(metrics["thrust_slope"][start : end + 1]))
                ),
                "torque_abs_slope_median": safe_number(
                    np.nanmedian(np.abs(metrics["torque_slope"][start : end + 1]))
                ),
                "penetration_abs_slope_median": safe_number(
                    np.nanmedian(np.abs(metrics["penetration_slope"][start : end + 1]))
                ),
                "stability_vote_count_median": safe_number(
                    np.nanmedian(metrics["stability_vote_count"][start : end + 1])
                ),
                "stability_occupancy_median": safe_number(
                    np.nanmedian(metrics["stability_occupancy"][start : end + 1])
                ),
                "nearby_knee_strength": safe_number(
                    max((value for position, value in knees.items() if start - 3 <= position <= end + 3), default=0.0)
                ),
                "source_file": str(block["source_file"].iloc[start]),
                "source_row_start": int(block["source_row"].iloc[start]),
                "source_row_end": int(block["source_row"].iloc[end]),
            }
        )
    return rows


def _stable_failure_reasons(metrics: dict[str, np.ndarray], config: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for column in ("thrust", "torque", "penetration"):
        if not np.asarray(metrics[f"{column}_stable_mask"], dtype=bool).any():
            reasons.append(f"{column}_no_low_variance_low_slope_evidence")
    if not np.asarray(metrics["activity_mask"], dtype=bool).any():
        reasons.append("activity_level_insufficient")
    joint = np.asarray(metrics["stable_mask"], dtype=bool)
    if not joint.any():
        reasons.append("no_overlapping_stable_evidence")
    elif not any(
        end - start + 1 >= int(config["stable_minimum_samples"])
        for start, end in _array_true_runs(joint)
    ):
        reasons.append("stable_evidence_too_few_samples")
    else:
        reasons.append("stable_evidence_too_short")
    return reasons


def _find_directional_run(
    block: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    search_start: int,
    search_end: int,
    anchor: int,
    direction: int,
    knees: dict[int, float],
    config: dict[str, Any],
) -> tuple[tuple[int, int] | None, dict[str, float | int]]:
    empty = {"score": 0.0, "signal_count": 0}
    if search_end < search_start or search_start < 0 or search_end >= len(block):
        return None, empty
    elapsed = block["time"].sub(block["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    width = float(config["transition_window_seconds"])
    minimum_signals = int(config["transition_minimum_signals"])
    evidence_mask = np.zeros(len(block), dtype=bool)
    point_scores = np.zeros(len(block), dtype=float)
    for position in range(search_start, search_end + 1):
        left = max(search_start, int(np.searchsorted(elapsed, elapsed[position] - width / 2.0)))
        right = min(
            search_end + 1,
            int(np.searchsorted(elapsed, elapsed[position] + width / 2.0, side="right")),
        )
        if right - left < 3:
            continue
        evidence = _directional_evidence(block, signals, left, right, direction=direction)
        if int(evidence["signal_count"]) >= minimum_signals:
            evidence_mask[position] = True
            point_scores[position] = float(evidence["score"])
    evidence_mask = _bridge_boolean_gaps(
        block,
        evidence_mask,
        float(config["directional_bridge_gap_seconds"]),
    )
    candidates: list[tuple[float, int, int]] = []
    for start, end in _array_true_runs(evidence_mask):
        start = max(start, search_start)
        end = min(end, search_end)
        if _position_duration_seconds(block, start, end) < float(config["transition_minimum_seconds"]):
            continue
        distance = abs(anchor - (end if direction > 0 else start))
        nearby_knee = max(
            (strength for position, strength in knees.items() if start - 2 <= position <= end + 2),
            default=0.0,
        )
        local_score = float(np.nanmean(point_scores[start : end + 1]))
        score = local_score + 0.2 * nearby_knee - 0.002 * distance
        candidates.append((score, start, end))
    if not candidates:
        return None, empty
    _, start, end = max(candidates, key=lambda item: item[0])
    evidence = _directional_evidence(block, signals, start, end + 1, direction=direction)
    if int(evidence["signal_count"]) < minimum_signals:
        return None, empty
    evidence["local_evidence_start"] = int(start)
    evidence["local_evidence_end"] = int(end)
    return (start, end), evidence


def _ordered_rising_run(
    block: pd.DataFrame,
    raw_signals: pd.DataFrame,
    smoothed_signals: pd.DataFrame,
    *,
    stable_start: int,
    reference_start: int,
    reference_end: int,
    config: dict[str, Any],
) -> tuple[tuple[int, int] | None, dict[str, Any]]:
    empty: dict[str, Any] = {"score": 0.0, "signal_count": 0, "boundary_type": None}
    if stable_start <= 0:
        return None, empty

    references = raw_signals.iloc[reference_start : reference_end + 1].median(axis=0, skipna=True)
    relative_levels = pd.DataFrame(index=raw_signals.index)
    gain_count = 0
    shoulder = max(1, int(config.get("rising_reference_shoulder_samples", 3)))
    for column in raw_signals:
        reference = float(references.get(column, np.nan))
        if not np.isfinite(reference) or reference <= 0:
            continue
        relative_levels[column] = pd.to_numeric(raw_signals[column], errors="coerce") / reference
        initial = float(pd.to_numeric(raw_signals[column].iloc[:shoulder], errors="coerce").median())
        if np.isfinite(initial) and (reference - initial) / reference >= float(
            config.get("rising_minimum_relative_gain", 0.20)
        ):
            gain_count += 1
    if relative_levels.empty or gain_count < int(config["transition_minimum_signals"]):
        return None, empty

    minimum_valid = int(config["transition_minimum_signals"])
    composite = relative_levels.median(axis=1, skipna=True).where(
        relative_levels.notna().sum(axis=1).ge(minimum_valid)
    )
    crossing = composite.ge(float(config.get("rising_start_level_fraction", 0.15))).to_numpy(dtype=bool)
    crossing[stable_start:] = False
    minimum_samples = int(config.get("rising_start_minimum_samples", 3))
    runs = [run for run in _array_true_runs(crossing) if run[1] - run[0] + 1 >= minimum_samples]
    if not runs:
        return None, empty

    rising_start = max(0, runs[0][0] - 1)
    evidence: dict[str, Any] = _directional_evidence(
        block,
        smoothed_signals,
        rising_start,
        stable_start + 1,
        direction=1,
    )
    evidence["signal_count"] = max(int(evidence["signal_count"]), gain_count)
    evidence["boundary_type"] = "stable_reference_level_crossing"
    evidence["stable_reference_fraction"] = float(config.get("rising_start_level_fraction", 0.15))
    return (rising_start, stable_start - 1), evidence


def _loading_plateau_start(
    block: pd.DataFrame,
    raw_signals: pd.DataFrame,
    *,
    stable_core_start: int,
    stable_core_end: int,
    config: dict[str, Any],
) -> int:
    if stable_core_start <= 0:
        return stable_core_start

    references = raw_signals.iloc[stable_core_start : stable_core_end + 1].median(
        axis=0,
        skipna=True,
    )
    relative_levels = pd.DataFrame(index=raw_signals.index)
    for column in raw_signals:
        reference = float(references.get(column, np.nan))
        if np.isfinite(reference) and reference > 0:
            relative_levels[column] = pd.to_numeric(raw_signals[column], errors="coerce") / reference
    if relative_levels.empty:
        return stable_core_start

    minimum_valid = int(config["transition_minimum_signals"])
    composite = relative_levels.median(axis=1, skipna=True).where(
        relative_levels.notna().sum(axis=1).ge(minimum_valid)
    )
    composite = _time_rolling_median(
        block["time"],
        composite,
        float(config.get("plateau_boundary_smoothing_seconds", 40.0)),
    )
    high_level = float(config.get("plateau_entry_level_fraction", 0.70))
    maximum_future_gain = float(config.get("plateau_maximum_future_gain", 0.03))
    confirmation_samples = int(config.get("plateau_confirmation_samples", 3))
    values = composite.to_numpy(dtype=float)
    for position in range(stable_core_start):
        if not np.isfinite(values[position]) or values[position] < high_level:
            continue
        future_end = min(stable_core_start + 1, position + 1 + confirmation_samples)
        future = values[position + 1 : future_end]
        future = future[np.isfinite(future)]
        if len(future) < 2:
            continue
        future_level = float(np.nanmedian(future))
        if future_level >= high_level and future_level - values[position] <= maximum_future_gain:
            return position
    return stable_core_start


def _terminal_declining_run(
    block: pd.DataFrame,
    smoothed_signals: pd.DataFrame,
    *,
    stable_end: int,
    knees: dict[int, float],
    terminal_stop_time: pd.Timestamp | None,
    config: dict[str, Any],
) -> tuple[tuple[int, int] | None, dict[str, Any]]:
    candidate, evidence = _find_directional_run(
        block,
        smoothed_signals,
        search_start=stable_end + 1,
        search_end=len(block) - 1,
        anchor=len(block) - 1,
        direction=-1,
        knees=knees,
        config=config,
    )
    if candidate is not None:
        seconds_to_end = (
            block["time"].iloc[-1] - block["time"].iloc[candidate[1]]
        ).total_seconds()
        if seconds_to_end <= float(config.get("terminal_decline_max_gap_seconds", 120.0)):
            evidence["boundary_type"] = "observed_terminal_decrease"
            return (candidate[0], len(block) - 1), evidence

    raw_off = pd.to_numeric(block["raw_state"], errors="coerce").fillna(0.0).eq(0.0).to_numpy()
    terminal_off_positions = np.flatnonzero(raw_off & (np.arange(len(block)) > stable_end))
    if len(terminal_off_positions):
        decline_start = int(terminal_off_positions[0])
        return (
            (decline_start, len(block) - 1),
            {
                "score": 1.0,
                "signal_count": 3,
                "boundary_type": "state_1_to_0_with_decay_context",
            },
        )

    if terminal_stop_time is None or block.empty:
        return None, {"score": 0.0, "signal_count": 0, "boundary_type": None}
    stop_gap = (pd.Timestamp(terminal_stop_time) - block["time"].iloc[-1]).total_seconds()
    if stop_gap < 0 or stop_gap > float(config.get("terminal_stop_max_gap_seconds", 120.0)):
        return None, {"score": 0.0, "signal_count": 0, "boundary_type": None}

    return (
        (len(block) - 1, len(block) - 1),
        {
            "score": 1.0,
            "signal_count": 3,
            "boundary_type": "state_1_to_0",
            "stop_gap_seconds": float(stop_gap),
        },
    )


def _validated_snap_boundary_to_knee(
    block: pd.DataFrame,
    position: int,
    knees: dict[int, float],
    stable_mask: np.ndarray,
    max_seconds: float,
) -> int:
    if not knees:
        return position
    time = block["time"].iloc[position]
    candidates = []
    for candidate, strength in knees.items():
        if candidate > position:
            continue
        if abs((block["time"].iloc[candidate] - time).total_seconds()) > max_seconds:
            continue
        if np.asarray(stable_mask[candidate : position + 1], dtype=bool).all():
            candidates.append((strength, -abs(candidate - position), candidate))
    return max(candidates)[2] if candidates else position


def _directional_evidence(
    block: pd.DataFrame,
    signals: pd.DataFrame,
    start: int,
    end: int,
    *,
    direction: int,
) -> dict[str, float | int]:
    end = min(max(end, start + 1), len(block))
    scores = []
    count = 0
    for column in signals:
        y = signals[column].iloc[start:end].to_numpy(dtype=float)
        x = block["time"].iloc[start:end].sub(block["time"].iloc[start]).dt.total_seconds().to_numpy(dtype=float)
        valid = np.isfinite(y) & np.isfinite(x)
        y = y[valid]
        x = x[valid]
        if len(y) < 3 or np.ptp(x) <= 0:
            continue
        edge = max(1, len(y) // 3)
        change = direction * (float(np.nanmedian(y[-edge:])) - float(np.nanmedian(y[:edge])))
        rho = float(spearmanr(x, y).statistic) if np.ptp(y) > 0 else 0.0
        signed_rho = direction * rho
        if change >= 0.10 and signed_rho >= 0.35:
            count += 1
            scores.append(change + signed_rho)
    return {"score": float(np.mean(scores)) if scores else 0.0, "signal_count": int(count)}


def _remove_undersampled_transitions(
    block: pd.DataFrame,
    labels: np.ndarray,
    config: dict[str, Any],
    *,
    preserve_terminal_decline: bool = False,
) -> np.ndarray:
    result = labels.copy()
    minimum = float(config["transition_minimum_seconds"])
    for start, end in _label_runs(result):
        label = result[start]
        if label not in {"startup", "rising", "declining"}:
            continue
        if preserve_terminal_decline and label == "declining" and end == len(result) - 1:
            continue
        if _position_duration_seconds(block, start, end) >= minimum:
            continue
        result[start : end + 1] = None
    return result


def _phase_rows(
    block: pd.DataFrame,
    labels: pd.Series,
    *,
    interval_id: str,
    stable_score: float,
    rising_evidence: dict[str, Any],
    decline_evidence: dict[str, Any],
    penetration_source: str,
    smoothing_seconds: float,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    valid = labels.notna()
    if not valid.any():
        return rows
    phase_number = 0
    groups = labels.ne(labels.shift()).cumsum()
    for _, positions in labels[valid].groupby(groups[valid], sort=False).groups.items():
        phase = block.loc[positions]
        label = str(labels.loc[positions[0]])
        phase_number += 1
        strength = _rule_strength(
            label,
            stable_score=stable_score,
            rising_evidence=rising_evidence,
            decline_evidence=decline_evidence,
        )
        duration_seconds = _block_duration_seconds(phase, config)
        rows.append(
            {
                "date": str(phase["time"].iloc[0].date()),
                "interval_id": interval_id,
                "phase_id": f"{interval_id}-P{phase_number:02d}",
                "phase_label": label,
                "start_time": iso(phase["time"].iloc[0]),
                "end_time": iso(phase["time"].iloc[-1]),
                "duration_seconds": safe_number(duration_seconds),
                "sample_count": int(len(phase)),
                "chainage_start": safe_number(phase["chainage"].dropna().iloc[0]) if phase["chainage"].notna().any() else None,
                "chainage_end": safe_number(phase["chainage"].dropna().iloc[-1]) if phase["chainage"].notna().any() else None,
                "phase_method": config.get(
                    "phase_segmentation_method_version",
                    "four_phase_score_kneedle_stability",
                ),
                "phase_rule_strength": strength,
                "phase_confidence": strength,
                "phase_review_warning": _phase_review_warning(
                    label,
                    duration_seconds=duration_seconds,
                    decline_evidence=decline_evidence,
                ),
                "penetration_signal_source": penetration_source,
                "smoothing_window_seconds": safe_number(smoothing_seconds),
                **_phase_quality_coverage(phase),
                "source_file": str(phase["source_file"].iloc[0]),
                "source_row_start": int(phase["source_row"].iloc[0]),
                "source_row_end": int(phase["source_row"].iloc[-1]),
                **parameter_summary(phase),
            }
        )
    return rows


def _rule_strength(
    label: str,
    *,
    stable_score: float,
    rising_evidence: dict[str, Any],
    decline_evidence: dict[str, Any],
) -> str:
    if label == "stable":
        return "high" if stable_score >= 2.0 else "medium" if stable_score >= 1.0 else "low"
    if label == "rising":
        count = int(rising_evidence["signal_count"])
        return "high" if count >= 3 else "medium" if count >= 2 else "low"
    if label == "declining":
        count = int(decline_evidence["signal_count"])
        return "high" if count >= 3 else "medium" if count >= 2 else "low"
    return "medium"


def _phase_review_warning(
    label: str,
    *,
    duration_seconds: float,
    decline_evidence: dict[str, Any],
) -> str | None:
    if label == "startup" and duration_seconds > 1800:
        return "extended_startup_requires_manual_review"
    if label == "declining" and decline_evidence.get("boundary_type") == "state_1_to_0":
        return "instantaneous_decline_inferred_from_state_transition_no_ramp_samples"
    return None


def _time_rolling_median(time: pd.Series, values: pd.Series, seconds: float) -> pd.Series:
    indexed = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(time))
    result = indexed.rolling(f"{max(1, int(seconds))}s", center=True, min_periods=2).median()
    return pd.Series(result.to_numpy(), index=values.index).where(values.notna())


def _phase_quality_coverage(phase: pd.DataFrame) -> dict[str, Any]:
    count = max(len(phase), 1)
    coverage = {
        "advance_speed_valid_ratio": safe_number(phase["advance_speed"].notna().sum() / count),
        "penetration_valid_ratio": safe_number(phase["penetration_analysis"].notna().sum() / count),
        "rpm_valid_ratio": safe_number(phase["rpm_canonical"].notna().sum() / count),
        "thrust_valid_ratio": safe_number(phase["thrust"].notna().sum() / count),
        "torque_valid_ratio": safe_number(phase["torque"].notna().sum() / count),
    }
    warnings = [key.removesuffix("_valid_ratio") for key, value in coverage.items() if value is not None and value < 0.8]
    coverage["parameter_quality_warnings"] = warnings
    return coverage


def _bridge_boolean_gaps(
    frame: pd.DataFrame,
    values: np.ndarray,
    max_seconds: float,
    *,
    required_mask: np.ndarray | None = None,
) -> np.ndarray:
    result = values.astype(bool).copy()
    for start, end in _array_true_runs(~result):
        if start == 0 or end == len(result) - 1 or not result[start - 1] or not result[end + 1]:
            continue
        if _has_hard_boundary(frame, start, end + 1):
            continue
        if required_mask is not None and not np.asarray(required_mask[start : end + 1], dtype=bool).all():
            continue
        if _position_duration_seconds(frame, start, end) <= max_seconds:
            result[start : end + 1] = True
    return result


def _bounded_true_runs(frame: pd.DataFrame, values: pd.Series | np.ndarray) -> list[tuple[int, int]]:
    mask = np.asarray(values, dtype=bool)
    if len(mask) != len(frame) or not mask.any():
        return []
    hard_before = (
        frame["hard_gap_before"].fillna(False).to_numpy(dtype=bool)
        if "hard_gap_before" in frame
        else np.zeros(len(frame), dtype=bool)
    )
    starts = mask & np.r_[True, ~mask[:-1]]
    starts |= mask & hard_before
    ends = mask & np.r_[~mask[1:], True]
    ends |= mask & np.r_[hard_before[1:], False]
    return list(
        zip(
            np.flatnonzero(starts).tolist(),
            np.flatnonzero(ends).tolist(),
            strict=True,
        )
    )


def _has_hard_boundary(frame: pd.DataFrame, start: int, end: int) -> bool:
    if "hard_gap_before" not in frame or end <= start:
        return False
    return bool(frame["hard_gap_before"].iloc[start + 1 : end + 1].fillna(False).any())


def _array_true_runs(values: np.ndarray) -> list[tuple[int, int]]:
    mask = np.asarray(values, dtype=bool)
    if not mask.any():
        return []
    starts = np.flatnonzero(mask & np.r_[True, ~mask[:-1]])
    ends = np.flatnonzero(mask & np.r_[~mask[1:], True])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _label_runs(values: np.ndarray) -> list[tuple[int, int]]:
    if not len(values):
        return []
    series = pd.Series(values, dtype=object)
    changes = series.ne(series.shift()).cumsum()
    return [
        (int(indexes[0]), int(indexes[-1]))
        for indexes in pd.Series(np.arange(len(values))).groupby(changes).groups.values()
    ]


def _position_duration_seconds(frame: pd.DataFrame, start: int, end: int) -> float:
    if end < start or frame.empty:
        return 0.0
    observed = (frame["time"].iloc[end] - frame["time"].iloc[start]).total_seconds()
    differences = frame["time"].diff().dt.total_seconds().dropna()
    typical = float(differences.median()) if not differences.empty else 0.0
    if not np.isfinite(typical):
        typical = 0.0
    return float(observed + max(typical, 0.0))


def _block_duration_seconds(frame: pd.DataFrame, config: dict[str, Any]) -> float:
    if frame.empty:
        return 0.0
    observed = (frame["time"].iloc[-1] - frame["time"].iloc[0]).total_seconds()
    differences = frame["time"].diff().dt.total_seconds().dropna()
    tail = differences.median() if not differences.empty else 0.0
    if not np.isfinite(tail):
        tail = 0.0
    return float(observed + min(max(tail, 0.0), float(config["maximum_sample_duration_seconds"])))
