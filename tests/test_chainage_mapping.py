from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tbm_process.ingestion.io import canonicalize
from tbm_process.ingestion.profiles import load_field_profile
from tbm_process.ingestion.chainage import (
    annotate_chainage_steps,
    normalize_guidance_chainage,
)


CONFIG = load_field_profile()["guidance_chainage_mapping"]


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["导向滚动角", "导向盾首里程", "导向盾中里程", "导向盾尾里程"],
    )


def test_row_level_mapping_recovers_all_three_observed_layouts() -> None:
    raw = _frame(
        [
            (-0.4, 1015804, 1015789, 0),
            (1015804, 1015789, 0, 45.0),
            (24.4, 0.07, 1017380, 1017366),
        ]
    )
    result = normalize_guidance_chainage(
        raw,
        direct_chainage=raw["导向盾首里程"],
        direct_source_field="导向盾首里程",
        config=CONFIG,
    )

    assert result["chainage_mapping_mode"].tolist() == [
        "aligned",
        "shifted_left",
        "shifted_right",
    ]
    assert result["chainage"].tolist() == [1015804, 1015804, 1017380]
    assert result["chainage_middle_canonical"].tolist() == [1015789, 1015789, 1017366]
    assert result["chainage_source_field"].tolist() == [
        "导向盾首里程",
        "导向滚动角",
        "导向盾中里程",
    ]


def test_unresolvable_mapping_is_not_silently_filled() -> None:
    raw = _frame([(0.1, 0.2, 0.3, 0.4)])
    result = normalize_guidance_chainage(
        raw,
        direct_chainage=raw["导向盾首里程"],
        direct_source_field="导向盾首里程",
        config=CONFIG,
    )

    assert np.isnan(result.loc[0, "chainage"])
    assert result.loc[0, "chainage_mapping_mode"] == "unavailable"
    assert result.loc[0, "chainage_quality"] == "unavailable_mapping"
    assert result.loc[0, "chainage_raw"] == 0.2


def test_missing_mapping_fields_preserve_direct_source_for_other_tbms() -> None:
    raw = pd.DataFrame(
        {
            "运行时间-time": ["2024/01/01 00:00:00"],
            "导向盾首里程": [1234.5],
        }
    )
    result = canonicalize(
        raw,
        source_file=Path("other_tbm.csv"),
        field_aliases={"time": ["运行时间-time"], "chainage": ["导向盾首里程"]},
        guidance_chainage_mapping=CONFIG,
    )

    assert result.loc[0, "chainage"] == 1234.5
    assert result.loc[0, "chainage_raw"] == 1234.5
    assert result.loc[0, "chainage_quality"] == "usable_direct_source_mapping_fields_unavailable"


def test_alternating_layouts_produce_continuous_canonical_chainage() -> None:
    raw = _frame(
        [
            (-0.4, 1015804, 1015789, 0),
            (1015804, 1015789, 0, 45.0),
            (-0.3, 1015804, 1015789, 0),
            (1015805, 1015790, 0, 44.0),
        ]
    )
    result = normalize_guidance_chainage(
        raw,
        direct_chainage=raw["导向盾首里程"],
        direct_source_field="导向盾首里程",
        config=CONFIG,
    )

    assert result["chainage"].tolist() == [1015804, 1015804, 1015804, 1015805]
    assert result["chainage"].diff().abs().dropna().max() == 1


def test_missing_middle_column_still_recovers_left_shift_layout() -> None:
    raw = pd.DataFrame(
        {
            "导向滚动角": [1015629, 1015630],
            "导向盾首里程": [1015615, 1015616],
            "导向盾尾里程": [6.9, 7.0],
        }
    )
    result = normalize_guidance_chainage(
        raw,
        direct_chainage=raw["导向盾首里程"],
        direct_source_field="导向盾首里程",
        config=CONFIG,
    )

    assert result["chainage_mapping_mode"].tolist() == [
        "shifted_left",
        "shifted_left",
    ]
    assert result["chainage"].tolist() == [1015629, 1015630]


def test_short_gap_large_step_is_flagged_without_overwriting_candidate() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2023-11-05 18:00:10", "2023-11-05 18:00:30"]
            ),
            "chainage": [1013929.0, 1014116.0],
        }
    )
    result = annotate_chainage_steps(frame, config=CONFIG)

    assert result["chainage"].tolist() == [1013929.0, 1014116.0]
    assert result["chainage_canonical_candidate"].tolist() == [1013929.0, 1014116.0]
    assert result.loc[1, "chainage_step_quality"] == "suspicious_short_gap_jump"


def test_large_step_across_sampling_gap_is_not_mislabeled_as_short_gap_jump() -> None:
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-02-27 02:35:40", "2024-02-27 09:07:00"]
            ),
            "chainage": [1015215.0, 1015223.0],
        }
    )
    result = annotate_chainage_steps(frame, config=CONFIG)

    assert result.loc[1, "chainage_step_quality"] == "not_assessed_across_sampling_gap"
