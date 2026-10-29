from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tbm_process.quality.preprocessing import preprocess_day


def test_rpm_mapping_switch_and_extreme_speed_exclusion() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2023-11-30 19:12:10", "2023-11-30 19:12:20"]),
            "raw_state": [1, 1],
            "rpm_actual_raw": [5.0, 80.0],
            "rpm_alternate_raw": [80.0, 5.0],
            "motor_frequency_m1": [82.0, 82.0],
            "advance_speed_raw": [30.0, 7105.0],
            "advance_speed_set": [30.0, 30.0],
            "penetration_raw": [6.0, 6.0],
            "source_file": ["x.csv", "x.csv"],
            "source_row": [2, 3],
        }
    )
    config = {
        "rpm_switch_timestamp": "2023-11-30T19:12:20",
        "rpm_relative_error_warning": 0.15,
        "rpm_analysis_upper_bound": 10.0,
        "large_gap_seconds": 180,
        "maximum_sample_duration_seconds": 120,
        "penetration_reference_relative_error_warning": 0.15,
        "rpm_mapping_version": "test",
        "rpm_mapping_evidence": "test",
    }
    thresholds = {"rpm_motor_ratio_reference": 0.061, "speed_valid_upper_bound": 200.0}
    processed, events = preprocess_day(frame, thresholds=thresholds, config=config)
    assert processed["rpm_canonical"].tolist() == [5.0, 5.0]
    assert processed["rpm_source"].tolist() == ["刀盘实际转速", "刀盘给定频率"]
    assert processed["advance_speed"].iloc[0] == 30.0
    assert np.isnan(processed["advance_speed"].iloc[1])
    assert processed["penetration_quality"].iloc[1] == "suspicious_linked_speed"
    assert np.isnan(processed["penetration_analysis"].iloc[1])
    assert any(event["subtype"] == "unreliable_advance_speed" for event in events)


def test_rpm_reference_mismatch_is_preserved_as_candidate_but_excluded() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2023-11-03 12:00:00"]),
            "raw_state": [1],
            "rpm_actual_raw": [40.0],
            "rpm_alternate_raw": [80.0],
            "motor_frequency_m1": [80.0],
            "advance_speed_raw": [30.0],
            "advance_speed_set": [30.0],
            "penetration_raw": [6.0],
            "source_file": ["x.csv"],
            "source_row": [2],
        }
    )
    config = {
        "rpm_switch_timestamp": "2023-11-30T19:12:20",
        "rpm_relative_error_warning": 0.15,
        "rpm_analysis_upper_bound": 10.0,
        "large_gap_seconds": 180,
        "maximum_sample_duration_seconds": 120,
        "penetration_reference_relative_error_warning": 0.15,
        "rpm_mapping_version": "test",
        "rpm_mapping_evidence": "test",
    }
    thresholds = {"rpm_motor_ratio_reference": 0.061, "speed_valid_upper_bound": 200.0}
    processed, events = preprocess_day(frame, thresholds=thresholds, config=config)
    assert processed["rpm_canonical_candidate"].iloc[0] == 40.0
    assert np.isnan(processed["rpm_canonical"].iloc[0])
    assert processed["rpm_quality"].iloc[0] == "invalid_extreme"
    assert any(event["subtype"] == "unreliable_canonical_rpm" for event in events)

