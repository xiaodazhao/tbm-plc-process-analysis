from __future__ import annotations

import numpy as np
import pandas as pd

from tbm_process.dynamics.descriptors import describe_block


def test_monotonic_change_is_described_as_increasing() -> None:
    time = pd.date_range("2023-11-03 11:16:00", periods=20, freq="20s")
    frame = pd.DataFrame({"time": time, "advance_speed": np.linspace(5, 30, len(time))})
    config = {
        "minimum_dynamic_samples": 6,
        "trend_min_relative_change": 0.15,
        "trend_min_spearman_abs": 0.45,
        "fluctuation_residual_ratio": 0.2,
        "fluctuation_moderate_ratio": 0.1,
        "local_outlier_robust_z": 3.5,
    }
    rows = describe_block(frame, phase_id="P1", config=config)
    assert rows[0]["trend"] == "increasing"
    assert rows[0]["slope_per_minute"] > 0

