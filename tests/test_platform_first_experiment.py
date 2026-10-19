from __future__ import annotations

import numpy as np
import pandas as pd

from tbm_process.process.platform_detection import (
    _classify_termination_observation,
    analyze_platform_first_day,
)


def _experiment_config() -> dict:
    return {
        "schema_version": "tbm_platform_first_experiment_v1",
        "status": "experimental_not_frozen",
        "activity_columns": {
            "advance_speed": 0.5,
            "thrust": 1000.0,
            "torque": 100.0,
            "rpm_canonical": 0.5,
        },
        "smoothing_seconds": 60.0,
        "core_required_active_signals": 2,
        "support_required_active_signals": 1,
        "bridge_internal_gap_seconds": 120.0,
        "boundary_extension_seconds": 180.0,
        "minimum_core_seconds": 180.0,
        "minimum_core_samples": 8,
        "minimum_episode_seconds": 180.0,
    }


def _phase_config() -> dict:
    return {
        "phase_segmentation_method_version": "tbm_four_phase_v1_2026-10-12",
        "phase_signal_interpolation_max_seconds": 40,
        "score_kneedle_smoothing_fraction": 1 / 6,
        "phase_smoothing_min_seconds": 60,
        "phase_smoothing_max_seconds": 300,
        "kneedle_minimum_prominence": 0.03,
        "knee_minimum_distance_seconds": 60,
        "knee_snap_seconds": 120,
        "transition_window_seconds": 180,
        "transition_minimum_seconds": 40,
        "transition_minimum_signals": 2,
        "stable_window_seconds": 180,
        "stable_window_minimum_samples": 5,
        "stable_minimum_seconds": 180,
        "stable_minimum_samples": 8,
        "stable_normalized_std_max": 0.10,
        "stable_normalized_slope_per_minute_max": 0.04,
        "stable_minimum_activity_level": 0.35,
        "stable_required_signal_count": 2,
        "stable_occupancy_window_seconds": 300,
        "stable_minimum_occupancy": 0.50,
        "stable_bridge_gap_seconds": 300,
        "directional_bridge_gap_seconds": 40,
        "rising_start_level_fraction": 0.15,
        "rising_start_minimum_samples": 3,
        "rising_reference_shoulder_samples": 3,
        "rising_minimum_relative_gain": 0.20,
        "plateau_entry_level_fraction": 0.70,
        "plateau_boundary_smoothing_seconds": 40,
        "plateau_confirmation_samples": 3,
        "plateau_maximum_future_gain": 0.01,
        "terminal_decline_max_gap_seconds": 120,
        "terminal_stop_max_gap_seconds": 120,
        "maximum_sample_duration_seconds": 120,
    }


def _frame(*, hard_gap: bool = False, sustained_unload: bool = False) -> pd.DataFrame:
    count = 48
    time = pd.date_range("2023-11-03 10:00:00", periods=count, freq="20s")
    if hard_gap:
        time = time[:24].append(time[24:] + pd.Timedelta(hours=1))
    active = np.ones(count, dtype=bool)
    if sustained_unload:
        active[20:29] = False
    raw_state = np.ones(count)
    raw_state[12] = 0
    hard = np.zeros(count, dtype=bool)
    block = np.zeros(count, dtype=int)
    if hard_gap:
        hard[24] = True
        block[24:] = 1
    return pd.DataFrame(
        {
            "time": time,
            "source_file": ["x.csv"] * count,
            "source_row": np.arange(2, count + 2),
            "raw_state": raw_state,
            "advance_speed": np.where(active, 30.0, 0.0),
            "thrust": np.where(active, 20000.0, 0.0),
            "torque": np.where(active, 3000.0, 0.0),
            "rpm_canonical": np.where(active, 5.0, 0.0),
            "penetration_analysis": np.where(active, 6.0, 0.0),
            "penetration_analysis_source": ["test"] * count,
            "chainage": np.linspace(1000, 1001, count),
            "hard_gap_before": hard,
            "continuity_block_id": block,
            "valid_advance_interval": [False] * count,
            "interval_id": [None] * count,
        }
    )


def test_single_raw_state_zero_does_not_split_loaded_platform() -> None:
    payload, assignments = analyze_platform_first_day(
        _frame(),
        date="2023-11-03",
        experiment_config=_experiment_config(),
        phase_config=_phase_config(),
    )
    assert payload["summary"]["experimental_platform_count"] == 1
    assert payload["platforms"][0]["raw_state_zero_sample_count"] == 1
    assert (
        payload["platforms"][0]["termination_observation"]["observation_type"]
        == "termination_unobserved_recording_end"
    )
    assert assignments["experimental_platform_id"].nunique() == 1


def test_sustained_multivariate_unload_splits_platforms() -> None:
    payload, _ = analyze_platform_first_day(
        _frame(sustained_unload=True),
        date="2023-11-03",
        experiment_config=_experiment_config(),
        phase_config=_phase_config(),
    )
    assert payload["summary"]["experimental_platform_count"] == 2
    assert payload["summary"]["termination_observation_counts"] == {
        "observed_declining_phase": 1,
        "termination_unobserved_recording_end": 1,
    }
    observed = payload["platforms"][0]["termination_observation"]
    assert observed["eligible_for_dynamic_analysis"] is True
    decline = next(
        row
        for row in payload["platforms"][0]["phases"]
        if row["phase_label"] == "declining"
    )
    assert decline["dynamic_analysis_eligible"] is True


def test_instantaneous_stop_boundary_is_not_dynamic_analysis_scope() -> None:
    frame = _frame()
    observation = _classify_termination_observation(
        frame,
        end=20,
        phase_rows=[
            {"phase_label": "stable"},
            {
                "phase_label": "declining",
                "sample_count": 1,
                "duration_seconds": 0.0,
                "start_time": "2023-11-03T10:06:40",
                "end_time": "2023-11-03T10:06:40",
                "source_row_start": 22,
                "source_row_end": 22,
                "phase_review_warning": (
                    "instantaneous_decline_inferred_from_state_transition_no_ramp_samples"
                ),
            },
        ],
        diagnostics={"terminal_stop_time": "2023-11-03T10:07:00"},
        phase_config=_phase_config(),
    )
    assert observation["observation_type"] == "instantaneous_stop_boundary"
    assert observation["eligible_for_dynamic_analysis"] is False


def test_hard_sampling_gap_always_splits_platforms() -> None:
    payload, _ = analyze_platform_first_day(
        _frame(hard_gap=True),
        date="2023-11-03",
        experiment_config=_experiment_config(),
        phase_config=_phase_config(),
    )
    assert payload["summary"]["experimental_platform_count"] == 2
    assert (
        payload["platforms"][0]["termination_observation"]["observation_type"]
        == "termination_unobserved_sampling_gap"
    )
