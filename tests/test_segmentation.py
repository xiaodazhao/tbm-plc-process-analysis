from __future__ import annotations

import numpy as np
import pandas as pd

from tbm_process.process.segmentation import (
    PHASE_LABELS,
    _robust_normalize,
    classify_machine_state,
    extract_advance_intervals,
    segment_interval_phases,
)
from tbm_process.orchestration.pipeline import _phase_context_bounds


def _config() -> dict[str, float | int]:
    return {
        "advancing_speed_min": 0.5,
        "phase_segmentation_method_version": "tbm_four_phase_v1_2026-10-12",
        "advancing_thrust_min": 1000,
        "advancing_torque_min": 100,
        "advancing_rpm_min": 0.5,
        "bridge_advance_gap_seconds": 60,
        "maximum_sample_duration_seconds": 120,
        "minimum_advance_interval_samples": 6,
        "minimum_advance_interval_seconds": 120,
        "score_kneedle_smoothing_fraction": 1 / 6,
        "phase_smoothing_min_seconds": 40,
        "phase_smoothing_max_seconds": 120,
        "kneedle_minimum_prominence": 0.02,
        "knee_minimum_distance_seconds": 40,
        "knee_snap_seconds": 60,
        "transition_window_seconds": 160,
        "transition_minimum_seconds": 40,
        "transition_minimum_signals": 2,
        "stable_window_seconds": 100,
        "stable_window_minimum_samples": 4,
        "stable_minimum_seconds": 160,
        "stable_minimum_samples": 8,
        "stable_normalized_std_max": 0.12,
        "stable_normalized_slope_per_minute_max": 0.05,
        "stable_minimum_activity_level": 0.35,
        "stable_required_signal_count": 2,
        "stable_occupancy_window_seconds": 300,
        "stable_minimum_occupancy": 0.5,
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
    }


def _phase_frame(thrust: np.ndarray, torque: np.ndarray, penetration: np.ndarray) -> pd.DataFrame:
    count = len(thrust)
    return pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03 10:00:00", periods=count, freq="20s"),
            "raw_state": [1] * count,
            "advance_speed": penetration * 5,
            "thrust": thrust,
            "torque": torque,
            "rpm_canonical": [5.0] * count,
            "penetration_raw": penetration,
            "penetration_reference": penetration,
            "penetration_analysis": penetration,
            "penetration_analysis_source": ["penetration_raw_quality_controlled"] * count,
            "machine_state": ["advancing"] * count,
            "hard_gap_before": [False] * count,
            "continuity_block_id": [0] * count,
            "chainage": np.linspace(100, 101, count),
            "source_file": ["x.csv"] * count,
            "source_row": np.arange(2, count + 2),
        }
    )


def test_short_gap_is_bridged_and_startup_is_retained_in_cycle() -> None:
    time = pd.date_range("2023-11-03 10:00:00", periods=12, freq="20s")
    frame = pd.DataFrame(
        {
            "time": time,
            "raw_state": [1] * 12,
            "advance_speed": [0, 0, 20, 20, 0, 20, 20, 20, 20, 20, 20, 20],
            "thrust": [0, 1200] + [2000] * 10,
            "torque": [200] * 12,
            "rpm_canonical": [5] * 12,
            "chainage": [100] * 12,
            "source_file": ["x.csv"] * 12,
            "source_row": list(range(2, 14)),
            "hard_gap_before": [False] * 12,
            "continuity_block_id": [0] * 12,
        }
    )
    classified = classify_machine_state(frame, _config())
    intervals, mask = extract_advance_intervals(classified, _config(), date="2023-11-03")
    assert len(intervals) == 1
    assert int(mask.sum()) == 12
    assert intervals[0]["interval_type"] == "four_phase_excavation_cycle"
    assert classified["machine_state"].iloc[0] == "free_rotation"


def test_zero_excavation_state_is_a_hard_stop_even_with_active_parameters() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03 10:00:00", periods=16, freq="20s"),
            "raw_state": [1] * 7 + [0] + [1] * 8,
            "advance_speed": [20.0] * 16,
            "thrust": [3000.0] * 16,
            "torque": [500.0] * 16,
            "rpm_canonical": [5.0] * 16,
            "chainage": np.linspace(100, 101, 16),
            "source_file": ["x.csv"] * 16,
            "source_row": np.arange(2, 18),
            "hard_gap_before": [False] * 16,
            "continuity_block_id": [0] * 16,
        }
    )

    classified = classify_machine_state(frame, _config())
    intervals, mask = extract_advance_intervals(classified, _config(), date="2023-11-03")

    assert classified["machine_state"].iloc[7] == "stopped"
    assert not bool(mask.iloc[7])
    assert len(intervals) == 2


def test_four_phase_score_kneedle_identifies_a_stable_platform() -> None:
    startup_thrust = np.linspace(300, 700, 5)
    rising_thrust = np.linspace(900, 5000, 8)
    stable_thrust = np.array([5000, 5030, 4980, 5010, 4990] * 4, dtype=float)
    decline_thrust = np.linspace(4500, 300, 7)
    thrust = np.r_[startup_thrust, rising_thrust, stable_thrust, decline_thrust]

    startup_torque = np.linspace(100, 160, 5)
    rising_torque = np.linspace(180, 800, 8)
    stable_torque = np.array([800, 805, 795, 802, 798] * 4, dtype=float)
    decline_torque = np.linspace(700, 80, 7)
    torque = np.r_[startup_torque, rising_torque, stable_torque, decline_torque]

    penetration = np.r_[
        np.linspace(0.2, 0.5, 5),
        np.linspace(0.8, 5.0, 8),
        np.array([5.0, 5.1, 4.9, 5.0, 5.05] * 4),
        np.linspace(4.5, 0.2, 7),
    ]
    frame = _phase_frame(thrust, torque, penetration)
    labels, rows, diagnostics, candidates = segment_interval_phases(
        frame, interval_id="2023-11-03-A001", config=_config()
    )

    produced = [row["phase_label"] for row in rows]
    assert "stable" in produced
    assert "startup_rising" not in produced
    assert set(labels.dropna().unique()).issubset(PHASE_LABELS)
    assert all(row["phase_method"] == "tbm_four_phase_v1_2026-10-12" for row in rows)
    stable = next(row for row in rows if row["phase_label"] == "stable")
    assert stable["sample_count"] >= _config()["stable_minimum_samples"]
    assert stable["duration_seconds"] >= _config()["stable_minimum_seconds"]
    assert diagnostics["segmentation_status"] == "stable_phase_identified"
    assert candidates


def test_cycle_without_stable_platform_is_left_unsegmented() -> None:
    count = 30
    thrust = np.linspace(500, 7000, count) + np.where(np.arange(count) % 2, 900, -900)
    torque = np.linspace(100, 1200, count) + np.where(np.arange(count) % 2, 180, -180)
    penetration = np.linspace(0.5, 8.0, count) + np.where(np.arange(count) % 2, 1.5, -1.5)
    frame = _phase_frame(thrust, torque, penetration)
    labels, rows, diagnostics, _ = segment_interval_phases(
        frame, interval_id="2023-11-03-A002", config=_config()
    )

    assert rows == []
    assert labels.isna().all()
    assert diagnostics["no_stable_reasons"]


def test_stable_platform_survives_short_parameter_disturbances() -> None:
    startup = 8
    stable_count = 72
    decline = 8
    thrust = np.r_[
        np.linspace(500, 4800, startup),
        np.full(stable_count, 5000.0),
        np.linspace(4700, 500, decline),
    ]
    torque = np.r_[
        np.linspace(100, 760, startup),
        np.full(stable_count, 800.0),
        np.linspace(740, 100, decline),
    ]
    penetration = np.r_[
        np.linspace(0.5, 4.8, startup),
        np.full(stable_count, 5.0),
        np.linspace(4.5, 0.5, decline),
    ]
    torque[startup + 22 : startup + 29] += np.linspace(0, 260, 7)
    thrust[startup + 42 : startup + 49] += np.array([0, 350, -250, 400, -300, 200, 0])
    frame = _phase_frame(thrust, torque, penetration)

    labels, rows, diagnostics, _ = segment_interval_phases(
        frame,
        interval_id="2023-11-03-A004",
        config=_config(),
    )

    stable = next(row for row in rows if row["phase_label"] == "stable")
    assert diagnostics["segmentation_status"] == "stable_phase_identified"
    assert stable["duration_seconds"] >= 15 * 60
    stable_labels = labels.eq("stable").to_numpy()
    assert stable_labels[startup + 25]
    assert stable_labels[startup + 45]


def test_hard_sampling_gap_splits_running_cycle() -> None:
    first = pd.date_range("2023-11-03 10:00:00", periods=8, freq="20s")
    second = pd.date_range("2023-11-03 11:00:00", periods=8, freq="20s")
    frame = pd.DataFrame(
        {
            "time": first.append(second),
            "raw_state": [1] * 16,
            "advance_speed": [20.0] * 16,
            "thrust": [3000.0] * 16,
            "torque": [500.0] * 16,
            "rpm_canonical": [5.0] * 16,
            "chainage": np.linspace(100, 102, 16),
            "source_file": ["x.csv"] * 16,
            "source_row": np.arange(2, 18),
            "hard_gap_before": [False] * 8 + [True] + [False] * 7,
            "continuity_block_id": [0] * 8 + [1] * 8,
        }
    )
    classified = classify_machine_state(frame, _config())
    intervals, _ = extract_advance_intervals(classified, _config(), date="2023-11-03")
    assert len(intervals) == 2
    assert intervals[0]["continuity_block_id"] != intervals[1]["continuity_block_id"]


def test_internal_dip_followed_by_reload_is_not_a_terminal_decline() -> None:
    thrust = np.r_[
        np.linspace(500, 4500, 8),
        np.full(20, 5000.0),
        np.linspace(4800, 1800, 8),
        np.linspace(2200, 6500, 15),
    ]
    torque = np.r_[
        np.linspace(100, 700, 8),
        np.full(20, 800.0),
        np.linspace(760, 250, 8),
        np.linspace(300, 1100, 15),
    ]
    penetration = np.r_[
        np.linspace(0.5, 4.5, 8),
        np.full(20, 5.0),
        np.linspace(4.8, 1.2, 8),
        np.linspace(1.5, 7.0, 15),
    ]
    frame = _phase_frame(thrust, torque, penetration)
    labels, rows, _, _ = segment_interval_phases(
        frame, interval_id="2023-11-03-A003", config=_config()
    )
    declining = np.flatnonzero(labels.to_numpy() == "declining")
    assert len(declining) == 0
    assert labels.iloc[-1] == "stable"


def test_rising_covers_the_complete_loading_ramp_before_stable() -> None:
    startup_count = 3
    ramp_count = 12
    stable_count = 24
    thrust = np.r_[
        np.linspace(0, 500, startup_count),
        np.linspace(800, 5000, ramp_count),
        np.full(stable_count, 5000.0),
    ]
    torque = np.r_[
        np.linspace(0, 80, startup_count),
        np.linspace(120, 800, ramp_count),
        np.full(stable_count, 800.0),
    ]
    penetration = np.r_[
        np.linspace(0, 0.3, startup_count),
        np.linspace(0.5, 5.0, ramp_count),
        np.full(stable_count, 5.0),
    ]
    frame = _phase_frame(thrust, torque, penetration)

    labels, rows, diagnostics, _ = segment_interval_phases(
        frame, interval_id="2023-11-03-A005", config=_config()
    )

    rising = np.flatnonzero(labels.to_numpy() == "rising")
    stable = np.flatnonzero(labels.to_numpy() == "stable")
    assert len(rising) >= ramp_count - 2
    assert rising.max() + 1 == stable.min()
    assert diagnostics["rising_start_time"] is not None
    assert not labels.iloc[: stable.min()].isna().any()


def test_state_transition_marks_an_abrupt_terminal_decline_without_labeling_stop() -> None:
    thrust = np.r_[np.linspace(500, 5000, 10), np.full(24, 5000.0)]
    torque = np.r_[np.linspace(100, 800, 10), np.full(24, 800.0)]
    penetration = np.r_[np.linspace(0.5, 5.0, 10), np.full(24, 5.0)]
    frame = _phase_frame(thrust, torque, penetration)
    stop_time = frame["time"].iloc[-1] + pd.Timedelta(seconds=20)

    labels, rows, diagnostics, _ = segment_interval_phases(
        frame,
        interval_id="2023-11-03-A006",
        config=_config(),
        terminal_stop_time=stop_time,
    )

    declining = np.flatnonzero(labels.to_numpy() == "declining")
    assert declining.tolist() == [len(frame) - 1]
    decline_row = next(row for row in rows if row["phase_label"] == "declining")
    assert diagnostics["decline_boundary_type"] == "state_1_to_0"
    assert decline_row["phase_review_warning"] == (
        "instantaneous_decline_inferred_from_state_transition_no_ramp_samples"
    )


def test_phase_context_includes_adjacent_rotation_but_not_inactive_samples() -> None:
    count = 13
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03 10:00:00", periods=count, freq="20s"),
            "raw_state": [0, 0, 0] + [1] * 6 + [0, 0, 0, 0],
            "torque": [0, 80, 120] + [500] * 6 + [400, 200, 60, 0],
            "rpm_canonical": [0, 1, 3] + [5] * 6 + [4, 2, 0.5, 0],
            "hard_gap_before": [False] * count,
        }
    )
    interval = {"start_index": 3, "end_index": 8}

    start, end = _phase_context_bounds(
        frame,
        interval,
        lower_bound=0,
        upper_bound=count - 1,
        config={
            "transition_context_max_seconds": 180,
            "transition_context_torque_min": 50,
            "transition_context_rpm_min": 0.25,
        },
    )

    assert (start, end) == (1, 11)


def test_sustained_unload_bracketed_by_loading_splits_cycle() -> None:
    count = 26
    unloaded = np.zeros(count, dtype=bool)
    unloaded[8:18] = True
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03 10:00:00", periods=count, freq="20s"),
            "raw_state": [1] * count,
            "advance_speed": np.where(unloaded, 0.0, 20.0),
            "thrust": np.where(unloaded, 0.0, 3000.0),
            "torque": np.where(unloaded, 200.0, 500.0),
            "rpm_canonical": np.where(unloaded, 0.0, 5.0),
            "chainage": np.linspace(100, 101, count),
            "source_file": ["x.csv"] * count,
            "source_row": np.arange(2, count + 2),
            "hard_gap_before": [False] * count,
            "continuity_block_id": [0] * count,
        }
    )
    classified = classify_machine_state(frame, _config())
    intervals, _ = extract_advance_intervals(classified, _config(), date="2023-11-03")
    assert len(intervals) == 2
    assert all(row["split_from_unload_reload_evidence"] for row in intervals)


def test_normalization_only_interpolates_short_bounded_gaps() -> None:
    time = pd.Series(pd.date_range("2023-11-03", periods=9, freq="20s"))
    values = pd.Series([1.0, 2.0, np.nan, 4.0, np.nan, np.nan, np.nan, 8.0, 9.0])
    normalized = _robust_normalize(values, time=time, max_gap_seconds=40.0)
    assert np.isfinite(normalized.iloc[2])
    assert normalized.iloc[4:7].isna().all()


def test_relative_multivariate_unload_can_split_while_state_stays_advancing() -> None:
    count = 26
    valley = np.zeros(count, dtype=bool)
    valley[8:18] = True
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03 10:00:00", periods=count, freq="20s"),
            "raw_state": [1] * count,
            "advance_speed": np.where(valley, 5.0, 25.0),
            "thrust": np.where(valley, 1200.0, 5000.0),
            "torque": np.where(valley, 150.0, 800.0),
            "rpm_canonical": np.where(valley, 1.0, 5.0),
            "chainage": np.linspace(100, 101, count),
            "source_file": ["x.csv"] * count,
            "source_row": np.arange(2, count + 2),
            "hard_gap_before": [False] * count,
            "continuity_block_id": [0] * count,
        }
    )
    classified = classify_machine_state(frame, _config())
    assert classified["machine_state"].eq("advancing").all()
    intervals, _ = extract_advance_intervals(classified, _config(), date="2023-11-03")
    assert len(intervals) == 2
    assert intervals[1]["split_boundary_rule"] == "relative_multivariate_unload_reload"
