from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..ingestion.io import iso, safe_number


def derive_dataset_thresholds(days: list[pd.DataFrame], config: dict[str, Any]) -> dict[str, float]:
    values = []
    setpoints = []
    for frame in days:
        mask = frame["raw_state"].fillna(0).ne(0)
        speed = frame.loc[mask, "advance_speed_raw"]
        values.append(speed[(speed > 0) & np.isfinite(speed)])
        given = frame.loc[mask, "advance_speed_set"]
        setpoints.append(given[(given > 0) & np.isfinite(given)])
    speed = pd.concat(values, ignore_index=True) if values else pd.Series(dtype=float)
    given = pd.concat(setpoints, ignore_index=True) if setpoints else pd.Series(dtype=float)
    median = float(speed.median()) if not speed.empty else 0.0
    mad = float((speed - median).abs().median()) if not speed.empty else 0.0
    robust_upper = median + float(config["speed_mad_multiplier"]) * mad
    setpoint_upper = (
        float(given.quantile(0.999)) * float(config["speed_setpoint_multiplier"])
        if not given.empty
        else robust_upper
    )
    speed_upper = max(
        float(config["minimum_speed_upper_bound"]),
        min(robust_upper, setpoint_upper),
    )
    return {
        "positive_working_speed_median": median,
        "positive_working_speed_mad": mad,
        "speed_robust_upper_candidate": robust_upper,
        "speed_setpoint_upper_candidate": setpoint_upper,
        "speed_valid_upper_bound": speed_upper,
        "rpm_motor_ratio_reference": float(config["rpm_motor_ratio_reference"]),
    }


def preprocess_day(
    frame: pd.DataFrame,
    *,
    thresholds: dict[str, float],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    out = frame.copy()
    quality_events: list[dict[str, Any]] = []
    gap_before_seconds = out["time"].diff().dt.total_seconds()
    out["gap_before_seconds"] = gap_before_seconds
    out["hard_gap_before"] = gap_before_seconds.gt(float(config["large_gap_seconds"])).fillna(False)
    out["continuity_block_id"] = out["hard_gap_before"].cumsum().astype(int)
    suspicious_chainage = out.get(
        "chainage_step_quality",
        pd.Series("not_assessed", index=out.index),
    ).eq("suspicious_short_gap_jump")
    for index in out.index[suspicious_chainage]:
        previous = index - 1 if index > 0 else None
        quality_events.append(
            {
                "event_type": "data_quality",
                "subtype": "unreliable_guidance_chainage_jump",
                "start_time": iso(out.loc[previous, "time"]) if previous is not None else None,
                "end_time": iso(out.loc[index, "time"]),
                "duration_seconds": safe_number(out.loc[index, "chainage_step_gap_seconds"]),
                "chainage_step_delta_m": safe_number(out.loc[index, "chainage_step_delta_m"]),
                "action": "exclude_daily_chainage_advance_from_report_pending_review",
                "source_file": str(out.loc[index, "source_file"]),
                "source_row_start": int(out.loc[previous, "source_row"]) if previous is not None else None,
                "source_row_end": int(out.loc[index, "source_row"]),
            }
        )
    switch = pd.Timestamp(config["rpm_switch_timestamp"])
    before = out["time"] < switch
    out["rpm_canonical_candidate"] = out["rpm_actual_raw"].where(
        before, out["rpm_alternate_raw"]
    )
    out["rpm_source"] = np.where(
        before,
        "刀盘实际转速",
        "刀盘给定频率",
    )
    out["rpm_mapping_version"] = str(config["rpm_mapping_version"])
    out["rpm_mapping_status"] = "provisional_pending_plc_point_table_confirmation"
    out["rpm_mapping_evidence"] = str(config["rpm_mapping_evidence"])
    expected_rpm = out["motor_frequency_m1"] * thresholds["rpm_motor_ratio_reference"]
    denominator = expected_rpm.abs().clip(lower=0.2)
    out["rpm_reference_relative_error"] = (
        out["rpm_canonical_candidate"] - expected_rpm
    ).abs() / denominator
    motor_running = out["motor_frequency_m1"].gt(1.0)
    out["rpm_quality"] = np.select(
        [
            out["rpm_canonical_candidate"].isna(),
            out["rpm_canonical_candidate"] < 0,
            out["rpm_canonical_candidate"] > float(config["rpm_analysis_upper_bound"]),
            motor_running
            & (out["rpm_reference_relative_error"] > float(config["rpm_relative_error_warning"])),
            ~motor_running & out["rpm_canonical_candidate"].gt(10.0),
        ],
        [
            "missing",
            "invalid_negative",
            "invalid_extreme",
            "reference_mismatch",
            "implausible_without_m1",
        ],
        default="usable_provisional_mapping",
    )
    out["rpm_canonical"] = out["rpm_canonical_candidate"].where(
        out["rpm_quality"] == "usable_provisional_mapping"
    )

    speed = out["advance_speed_raw"]
    upper = thresholds["speed_valid_upper_bound"]
    out["advance_speed_quality"] = np.select(
        [speed.isna(), speed < 0, speed > upper],
        ["missing", "invalid_negative", "invalid_extreme"],
        default="usable",
    )
    out["advance_speed"] = speed.where(out["advance_speed_quality"] == "usable")
    out["penetration_reference"] = out["advance_speed"] / out["rpm_canonical"].replace(0, np.nan)
    _assign_penetration_quality(out, config)
    out["sample_dt_seconds"] = out["time"].shift(-1).sub(out["time"]).dt.total_seconds()
    out["sample_dt_seconds"] = out["sample_dt_seconds"].clip(
        lower=0,
        upper=float(config["maximum_sample_duration_seconds"]),
    )
    crosses_hard_gap = out["hard_gap_before"].shift(-1, fill_value=False).astype(bool)
    out.loc[crosses_hard_gap, "sample_dt_seconds"] = 0.0

    invalid_speed = out["advance_speed_quality"].eq("invalid_extreme")
    for start, end in contiguous_true_runs(out, invalid_speed):
        block = out.iloc[start : end + 1]
        quality_events.append(
            {
                "event_type": "data_quality",
                "subtype": "unreliable_advance_speed",
                "start_time": iso(block["time"].iloc[0]),
                "end_time": iso(block["time"].iloc[-1]),
                "sample_count": int(len(block)),
                "raw_max": safe_number(block["advance_speed_raw"].max()),
                "valid_upper_bound": safe_number(upper),
                "action": "excluded_from_speed_statistics_and_state_analysis",
                "source_file": str(block["source_file"].iloc[0]),
                "source_row_start": int(block["source_row"].iloc[0]),
                "source_row_end": int(block["source_row"].iloc[-1]),
            }
        )

    invalid_rpm = out["rpm_quality"].isin(
        ["invalid_extreme", "reference_mismatch", "implausible_without_m1"]
    )
    for start, end in nearby_true_runs(out, invalid_rpm, max_gap_seconds=120.0):
        block = out.iloc[start : end + 1]
        quality_events.append(
            {
                "event_type": "data_quality",
                "subtype": "unreliable_canonical_rpm",
                "start_time": iso(block["time"].iloc[0]),
                "end_time": iso(block["time"].iloc[-1]),
                "sample_count": int(invalid_rpm.iloc[start : end + 1].sum()),
                "episode_span_sample_count": int(len(block)),
                "candidate_max": safe_number(block["rpm_canonical_candidate"].max()),
                "reference_relative_error_max": safe_number(
                    block["rpm_reference_relative_error"].max()
                ),
                "action": "excluded_from_rpm_statistics_and_state_analysis",
                "source_file": str(block["source_file"].iloc[0]),
                "source_row_start": int(block["source_row"].iloc[0]),
                "source_row_end": int(block["source_row"].iloc[-1]),
            }
        )

    for index in out.index[out["hard_gap_before"]]:
        quality_events.append(
            {
                "event_type": "data_quality",
                "subtype": "sampling_gap",
                "start_time": iso(out.loc[index - 1, "time"]) if index > 0 else None,
                "end_time": iso(out.loc[index, "time"]),
                "duration_seconds": safe_number(
                    (out.loc[index, "time"] - out.loc[index - 1, "time"]).total_seconds()
                ) if index > 0 else None,
                "action": "do_not_bridge_process_intervals_across_gap",
                "source_file": str(out.loc[index, "source_file"]),
                "source_row_start": int(out.loc[index - 1, "source_row"]) if index > 0 else None,
                "source_row_end": int(out.loc[index, "source_row"]),
            }
        )

    suspicious_penetration = out["penetration_quality"].isin(
        ["suspicious_linked_speed", "suspicious_reference_mismatch"]
    )
    for start, end in nearby_true_runs(out, suspicious_penetration, max_gap_seconds=120.0):
        block = out.iloc[start : end + 1]
        quality_events.append(
            {
                "event_type": "data_quality",
                "subtype": "unreliable_penetration",
                "start_time": iso(block["time"].iloc[0]),
                "end_time": iso(block["time"].iloc[-1]),
                "sample_count": int(suspicious_penetration.iloc[start : end + 1].sum()),
                "episode_span_sample_count": int(len(block)),
                "raw_max": safe_number(block["penetration_raw"].max()),
                "quality_reasons": sorted(block.loc[suspicious_penetration.iloc[start : end + 1].to_numpy(), "penetration_quality"].dropna().unique().tolist()),
                "action": "excluded_from_phase_and_dynamic_analysis",
                "source_file": str(block["source_file"].iloc[0]),
                "source_row_start": int(block["source_row"].iloc[0]),
                "source_row_end": int(block["source_row"].iloc[-1]),
            }
        )
    return out, quality_events


def _assign_penetration_quality(out: pd.DataFrame, config: dict[str, Any]) -> None:
    raw = pd.to_numeric(out["penetration_raw"], errors="coerce")
    reference = pd.to_numeric(out["penetration_reference"], errors="coerce")
    denominator = reference.abs().clip(lower=0.1)
    relative_error = (raw - reference).abs() / denominator
    out["penetration_reference_relative_error"] = relative_error

    speed_usable = out["advance_speed_quality"].eq("usable")
    reference_usable = reference.notna() & reference.ge(0)
    raw_present = raw.notna()
    raw_nonnegative = raw.ge(0)
    tolerance = float(config["penetration_reference_relative_error_warning"])

    quality = np.select(
        [
            ~raw_present & ~reference_usable,
            raw_present & ~raw_nonnegative,
            raw_present & ~speed_usable,
            raw_present & reference_usable & relative_error.gt(tolerance),
            raw_present & reference_usable,
            raw_present & speed_usable,
            ~raw_present & reference_usable,
        ],
        [
            "missing",
            "invalid_negative",
            "suspicious_linked_speed",
            "suspicious_reference_mismatch",
            "usable_consistent_raw",
            "usable_unverified_raw",
            "usable_reference_fallback",
        ],
        default="unavailable",
    )
    out["penetration_quality"] = quality

    usable_raw = pd.Series(quality, index=out.index).isin(
        ["usable_consistent_raw", "usable_unverified_raw"]
    )
    analysis = raw.where(usable_raw)
    source = pd.Series(None, index=out.index, dtype=object)
    source.loc[usable_raw] = "penetration_raw_quality_controlled"
    reference_fallback = analysis.isna() & reference_usable
    analysis = analysis.where(~reference_fallback, reference)
    source.loc[reference_fallback] = "advance_speed_divided_by_canonical_rpm"
    out["penetration_analysis"] = analysis
    out["penetration_analysis_source"] = source


def validate_day_consistency(
    frame: pd.DataFrame,
    *,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    valid = frame["advance_speed"].notna() & frame["sample_dt_seconds"].notna()
    integrated_m = float(
        (frame.loc[valid, "advance_speed"] * frame.loc[valid, "sample_dt_seconds"] / 60_000.0).sum()
    )
    cumulative = frame["cumulative_advance"].dropna()
    recorded_m = float(abs(cumulative.iloc[-1] - cumulative.iloc[0])) if len(cumulative) >= 2 else np.nan
    ratio = (
        max(integrated_m, recorded_m) / max(min(integrated_m, recorded_m), 0.1)
        if np.isfinite(recorded_m)
        else np.nan
    )
    if np.isfinite(ratio) and ratio > float(config["quality_distance_ratio_warning"]):
        events.append(
            {
                "event_type": "data_quality",
                "subtype": "daily_speed_integral_distance_mismatch",
                "start_time": iso(frame["time"].min()),
                "end_time": iso(frame["time"].max()),
                "integrated_valid_speed_m": safe_number(integrated_m),
                "recorded_cumulative_advance_change_m": safe_number(recorded_m),
                "distance_ratio": safe_number(ratio),
                "action": "review_day_before_using_distance_or_speed_summary",
                "source_file": str(frame["source_file"].iloc[0]),
            }
        )
    rpm_work = frame[
        frame["raw_state"].fillna(0).ne(0)
        & frame["rpm_canonical"].gt(0)
        & frame["motor_frequency_m1"].gt(0)
    ]
    if not rpm_work.empty:
        ratio_series = rpm_work["rpm_canonical"] / rpm_work["motor_frequency_m1"]
        median_ratio = float(ratio_series.median())
        relative_error = abs(median_ratio - float(config["rpm_motor_ratio_reference"])) / float(
            config["rpm_motor_ratio_reference"]
        )
        if relative_error > float(config["rpm_relative_error_warning"]):
            events.append(
                {
                    "event_type": "data_quality",
                    "subtype": "rpm_mapping_reference_mismatch",
                    "start_time": iso(rpm_work["time"].min()),
                    "end_time": iso(rpm_work["time"].max()),
                    "median_rpm_to_m1_ratio": safe_number(median_ratio),
                    "expected_ratio": safe_number(config["rpm_motor_ratio_reference"]),
                    "action": "treat_canonical_rpm_as_unverified",
                    "source_file": str(frame["source_file"].iloc[0]),
                }
            )
    return events


def contiguous_true_runs(frame: pd.DataFrame, mask: pd.Series) -> list[tuple[int, int]]:
    values = mask.fillna(False).to_numpy(dtype=bool)
    if not values.any():
        return []
    starts = np.flatnonzero(values & np.r_[True, ~values[:-1]])
    ends = np.flatnonzero(values & np.r_[~values[1:], True])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def nearby_true_runs(
    frame: pd.DataFrame,
    mask: pd.Series,
    *,
    max_gap_seconds: float,
) -> list[tuple[int, int]]:
    positions = np.flatnonzero(mask.fillna(False).to_numpy(dtype=bool))
    if not positions.size:
        return []
    runs: list[tuple[int, int]] = []
    start = int(positions[0])
    previous = int(positions[0])
    for position_value in positions[1:]:
        position = int(position_value)
        gap = (frame["time"].iloc[position] - frame["time"].iloc[previous]).total_seconds()
        if gap > max_gap_seconds:
            runs.append((start, previous))
            start = position
        previous = position
    runs.append((start, previous))
    return runs

