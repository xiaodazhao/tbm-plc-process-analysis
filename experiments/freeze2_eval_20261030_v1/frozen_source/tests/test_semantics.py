from __future__ import annotations

import numpy as np
import pandas as pd

from tbm_process.dynamics.descriptors import trend_descriptor
from tbm_process.dynamics.semantics import analyze_phase_semantics, select_semantic_facts


def _config() -> dict[str, float | int]:
    return {
        "dynamic_window_seconds": 120,
        "minimum_dynamic_samples": 5,
        "trend_min_relative_change": 0.15,
        "trend_min_spearman_abs": 0.45,
        "fluctuation_residual_ratio": 0.2,
        "fluctuation_moderate_ratio": 0.1,
        "local_outlier_robust_z": 3.5,
        "maximum_sample_duration_seconds": 120,
        "coupled_event_minimum_overlap_seconds": 40,
    }


def _frame() -> pd.DataFrame:
    count = 42
    time = pd.date_range("2023-11-03 11:00:00", periods=count, freq="20s")
    return pd.DataFrame(
        {
            "time": time,
            "advance_speed": np.linspace(5, 35, count),
            "thrust": np.linspace(2000, 6000, count),
            "torque": np.r_[np.linspace(300, 800, 24), np.full(18, 800.0)],
            "rpm_canonical": np.full(count, 5.0),
            "chainage": np.linspace(100, 101, count),
            "source_file": ["x.csv"] * count,
            "source_row": np.arange(2, count + 2),
            "continuity_block_id": [0] * count,
        }
    )


def test_adjacent_same_trend_windows_are_merged_and_recomputed() -> None:
    frame = _frame()
    scope = {
        "date": "2023-11-03",
        "interval_id": "2023-11-03-A001",
        "phase_id": "2023-11-03-A001-P01",
        "phase_label": "stable",
        "start_time": frame["time"].iloc[0].isoformat(),
        "end_time": frame["time"].iloc[-1].isoformat(),
    }
    result = analyze_phase_semantics(frame, [scope], config=_config())
    speed_segments = [row for row in result["semantic_segments"] if row["parameter"] == "advance_speed"]
    merged = next(row for row in speed_segments if row["trend"] == "increasing")
    assert merged["merged_window_count"] >= 2
    block = frame[frame["time"].between(pd.Timestamp(merged["start_time"]), pd.Timestamp(merged["end_time"]))]
    independently = trend_descriptor(block["time"], block["advance_speed"], _config())
    assert np.isclose(merged["slope_per_minute"], independently["slope_per_minute"])
    assert np.isclose(merged["median"], independently["median"])
    assert merged["statistics_recomputed_from_merged_raw_samples"] is True


def test_stable_phase_can_contain_local_trend_and_transition() -> None:
    frame = _frame()
    scope = {
        "date": "2023-11-03",
        "interval_id": "2023-11-03-A001",
        "phase_id": "2023-11-03-A001-P01",
        "phase_label": "stable",
        "start_time": frame["time"].iloc[0].isoformat(),
        "end_time": frame["time"].iloc[-1].isoformat(),
    }
    result = analyze_phase_semantics(frame, [scope], config=_config())
    torque = [row for row in result["semantic_segments"] if row["parameter"] == "torque"]
    assert {row["trend"] for row in torque} >= {"increasing", "stable"}
    assert any(row["parameter"] == "torque" for row in result["trend_transitions"])
    assert result["coupled_parameter_events"]
    for event in result["coupled_parameter_events"]:
        event_block = frame[
            frame["time"].between(
                pd.Timestamp(event["start_time"]),
                pd.Timestamp(event["end_time"]),
            )
        ]
        for parameter, state in event["parameter_states"].items():
            column = {
                "advance_speed": "advance_speed",
                "thrust": "thrust",
                "torque": "torque",
                "rpm": "rpm_canonical",
            }[parameter]
            assert np.isclose(state["median"], event_block[column].median())
        assert event["statistics_recomputed_from_event_raw_samples"] is True


def test_unsegmented_cycle_still_receives_dynamic_semantics() -> None:
    frame = _frame()
    scope = {
        "date": "2023-11-03",
        "interval_id": "2023-11-03-A009",
        "phase_id": "2023-11-03-A009-U00",
        "phase_label": "unsegmented_cycle",
        "start_time": frame["time"].iloc[0].isoformat(),
        "end_time": frame["time"].iloc[-1].isoformat(),
    }
    result = analyze_phase_semantics(frame, [scope], config=_config())
    assert result["semantic_segments"]
    assert {row["phase_label"] for row in result["semantic_segments"]} == {"unsegmented_cycle"}


def test_semantic_selection_is_bounded_and_keeps_parameter_coverage() -> None:
    segments = []
    for index, parameter in enumerate(["advance_speed", "thrust", "torque", "rpm"] * 4):
        segments.append(
            {
                "semantic_segment_id": f"S{index}",
                "parameter": parameter,
                "start_time": f"2023-11-03T10:{index:02d}:00",
                "duration_seconds": 300 + index,
                "trend": "increasing" if index % 2 else "stable",
                "relative_change": 0.1 * index,
                "fluctuation_level": "moderate" if index % 3 == 0 else "low",
            }
        )
    selected, _ = select_semantic_facts(
        segments,
        [],
        config={
            "semantic_selection_min_seconds": 120,
            "daily_semantic_segment_limit": 6,
            "daily_coupled_event_limit": 3,
        },
    )
    assert len(selected) == 6
    assert {row["parameter"] for row in selected} == {"advance_speed", "thrust", "torque", "rpm"}
