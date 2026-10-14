from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tbm_process.facts.builders import build_daily_result, state_durations
from tbm_process.orchestration.pipeline import _phase_context_bounds
from tbm_process.process.scopes import block_duration, build_analysis_scopes
from tbm_process.process.segmentation import rebuild_phase_rows_from_assignments


def test_daily_summary_limit_is_explicit_and_complete_count_is_retained() -> None:
    count = 20
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03", periods=count, freq="20s"),
            "chainage": np.linspace(100, 101, count),
            "cumulative_advance": np.linspace(0, 1, count),
            "sample_dt_seconds": [20.0] * count,
            "gap_before_seconds": [0.0] * count,
            "hard_gap_before": [False] * count,
            "continuity_block_id": [0] * count,
            "advance_speed_quality": ["usable"] * count,
            "rpm_quality": ["usable_provisional_mapping"] * count,
            "valid_advance_interval": [True] * count,
            "machine_state": ["advancing"] * count,
            "advance_speed": [20.0] * count,
            "thrust": [3000.0] * count,
            "torque": [500.0] * count,
            "rpm_canonical": [5.0] * count,
        }
    )
    intervals = [
        {
            "interval_id": f"2023-11-03-A{index:03d}",
            "duration_seconds": float(index * 100),
            "start_time": frame["time"].iloc[0].isoformat(),
            "end_time": frame["time"].iloc[-1].isoformat(),
        }
        for index in range(1, 11)
    ]
    result = build_daily_result(
        frame,
        date="2023-11-03",
        path=Path("x.csv"),
        intervals=intervals,
        phases=[],
        construction_events=[],
        quality_events=[],
        config={
            "schema_version": "test",
            "top_process_count": 8,
            "daily_construction_event_limit": 12,
            "maximum_sample_duration_seconds": 120,
        },
    )
    assert len(result["main_processes"]) == 8
    assert result["selection_metadata"]["cycle_count_before_selection"] == 10
    assert result["selection_metadata"]["cycle_count_after_selection"] == 8
    assert result["selection_metadata"]["result_type"] == "filtered_daily_summary"


def test_analysis_scopes_cover_unidentified_rows_between_phases() -> None:
    count = 10
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-11-03", periods=count, freq="20s"),
            "chainage": np.linspace(100, 101, count),
            "source_file": ["x.csv"] * count,
            "source_row": np.arange(2, count + 2),
        }
    )
    labels = pd.Series(["startup"] * 3 + [None] * 3 + ["stable"] * 4)
    phases = [
        {
            "date": "2023-11-03",
            "interval_id": "2023-11-03-A001",
            "phase_id": "2023-11-03-A001-P01",
            "phase_label": "startup",
            "start_time": frame["time"].iloc[0].isoformat(),
            "end_time": frame["time"].iloc[2].isoformat(),
        },
        {
            "date": "2023-11-03",
            "interval_id": "2023-11-03-A001",
            "phase_id": "2023-11-03-A001-P02",
            "phase_label": "stable",
            "start_time": frame["time"].iloc[6].isoformat(),
            "end_time": frame["time"].iloc[9].isoformat(),
        },
    ]
    scopes = build_analysis_scopes(
        frame,
        labels=labels,
        phase_rows=phases,
        interval={"interval_id": "2023-11-03-A001"},
        date="2023-11-03",
        config={"maximum_sample_duration_seconds": 120},
    )
    assert {row["phase_label"] for row in scopes} == {"startup", "stable", "unidentified_process"}
    assert sum(row.get("sample_count", 0) for row in scopes if row["phase_label"] == "unidentified_process") == 3


def test_adjacent_cycle_contexts_may_overlap_for_detection() -> None:
    count = 20
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-09-23", periods=count, freq="20s"),
            "raw_state": [0] * count,
            "torque": [200.0] * count,
            "rpm_canonical": [2.0] * count,
            "hard_gap_before": [False] * count,
        }
    )
    first = {"start_index": 4, "end_index": 8}
    second = {"start_index": 11, "end_index": 15}
    config = {
        "transition_context_max_seconds": 180,
        "transition_context_torque_min": 50,
        "transition_context_rpm_min": 0.25,
    }
    first_bounds = _phase_context_bounds(
        frame,
        first,
        lower_bound=0,
        upper_bound=second["start_index"] - 1,
        config=config,
    )
    second_bounds = _phase_context_bounds(
        frame,
        second,
        lower_bound=first["end_index"] + 1,
        upper_bound=count - 1,
        config=config,
    )
    first_rows = set(range(first_bounds[0], first_bounds[1] + 1))
    second_rows = set(range(second_bounds[0], second_bounds[1] + 1))
    assert first_rows & second_rows == {9, 10}


def test_phase_summary_is_recomputed_from_final_point_assignments() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2023-09-23", periods=6, freq="20s"),
            "source_file": ["x.csv"] * 6,
            "source_row": np.arange(2, 8),
            "interval_id": ["A001"] * 3 + ["A002"] * 3,
            "phase_id": ["A001-P01"] * 3 + ["A002-P01"] * 3,
            "phase_label": ["declining"] * 3 + ["startup"] * 3,
            "chainage": np.linspace(100, 101, 6),
            "advance_speed": np.arange(6, dtype=float),
            "thrust": np.arange(6, dtype=float) + 1000,
            "torque": np.arange(6, dtype=float) + 100,
            "rpm_canonical": np.arange(6, dtype=float) + 1,
            "advance_speed_quality": ["usable"] * 6,
            "penetration_analysis": np.arange(6, dtype=float) + 0.1,
        }
    )
    provisional = [
        {
            "interval_id": "A001",
            "phase_id": "A001-P01",
            "phase_label": "declining",
            "source_row_start": 2,
            "source_row_end": 7,
        },
        {
            "interval_id": "A002",
            "phase_id": "A002-P01",
            "phase_label": "startup",
            "source_row_start": 2,
            "source_row_end": 7,
        },
    ]
    rebuilt = rebuild_phase_rows_from_assignments(
        frame,
        provisional,
        {"maximum_sample_duration_seconds": 120},
    )
    assert [(row["source_row_start"], row["source_row_end"]) for row in rebuilt] == [(2, 4), (5, 7)]
    assert [row["sample_count"] for row in rebuilt] == [3, 3]


def test_hard_gap_contributes_no_machine_state_or_scope_duration() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    "2023-11-03 10:00:00",
                    "2023-11-03 10:00:20",
                    "2023-11-03 11:00:00",
                    "2023-11-03 11:00:20",
                ]
            ),
            "hard_gap_before": [False, False, True, False],
            "sample_dt_seconds": [20.0, 0.0, 20.0, np.nan],
            "machine_state": ["advancing", "advancing", "stopped", "stopped"],
        }
    )
    durations = state_durations(frame, {"maximum_sample_duration_seconds": 120})
    assert durations == {"advancing": 20.0, "stopped": 20.0}
    assert block_duration(frame, {"maximum_sample_duration_seconds": 120}) == 40.0
