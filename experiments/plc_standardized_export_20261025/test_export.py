"""Contract tests; synthetic fixtures are never exported as project data."""
import importlib.util
import sys
from pathlib import Path
import tempfile
import json
import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
SPEC = importlib.util.spec_from_file_location("standardized_export", HERE / "export.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
from experiments.plc_mapping_repair_v1.test_repair import fixture


def test_name_uses_observation_date_not_download_name():
    day, _ = M.day_from_timestamps(pd.DataFrame({M.TIME_FIELD: ["2023-12-11 12:00:00", "2023-12-11 13:00:00"]}))
    assert M.filename_for_day(day) == "tbm_data_20231211.csv"


def test_mixed_dates_and_invalid_dates_are_not_guessed():
    for dates in [["2023-12-11", "2023-12-12"], ["bad", "2023-12-11"]]:
        with pytest.raises(ValueError):
            M.day_from_timestamps(pd.DataFrame({M.TIME_FIELD: dates}))


@pytest.mark.parametrize("relocated", [False, True])
def test_original_tokens_preserved_for_relocated_and_direct_rows(relocated):
    raw = fixture(relocated).astype(str)
    raw.loc[0, "推力"] = "20000.123456789123456789"
    repaired, evidence = M.repair_frame(raw)
    fixed, evidence = M.exact_mapping_tokens(raw, repaired, evidence)
    assert fixed.loc[0, "推力"] == "20000.123456789123456789"
    assert fixed.loc[0, "刀盘实际转速"] == "4.5"
    assert M.validate_exact(raw, fixed, evidence) > 0
    assert raw.loc[0, M.TIME_FIELD] == fixed.loc[0, M.TIME_FIELD]


def test_unresolved_mapping_stays_missing_without_erasing_original():
    raw = fixture().astype(str)
    raw.loc[0, "unused_187"] = "90"
    repaired, evidence = M.repair_frame(raw)
    fixed, evidence = M.exact_mapping_tokens(raw, repaired, evidence)
    assert fixed.loc[0, "刀盘实际转速"] == ""
    assert fixed.loc[0, "raw__刀盘实际转速"] == raw.loc[0, "刀盘实际转速"]


def test_compatibility_does_not_choose_the_old_alternate_column_after_switch():
    raw = fixture(True).astype(str)
    repaired, evidence = M.repair_frame(raw)
    fixed, evidence = M.exact_mapping_tokens(raw, repaired, evidence)
    profile = M.build_profile(json.loads((M.REPO / "configs/field_profiles/tbm9_2023.json").read_text()))
    assert profile["aliases"]["rpm_alternate_raw"] == ["刀盘实际转速"]
    assert profile["guidance_chainage_mapping"]["enabled"] is False
    assert M.compatibility_check(fixed, profile, {})


def test_old_preprocessor_receives_repaired_rpm_on_both_sides_of_switch():
    original_profile = json.loads((M.REPO / "configs/field_profiles/tbm9_2023.json").read_text())
    profile = M.build_profile(original_profile)
    config = json.loads((M.REPO / "config.json").read_text())
    for date in ["2023-11-03", "2023-12-11"]:
        raw = fixture(True).astype(str)
        raw.loc[0, M.TIME_FIELD] = date + " 12:00:00"
        repaired, evidence = M.repair_frame(raw)
        fixed, _ = M.exact_mapping_tokens(raw, repaired, evidence)
        canonical = M.canonicalize(fixed, source_file=Path("test.csv"), field_aliases=profile["aliases"],
                                   guidance_chainage_mapping=profile["guidance_chainage_mapping"])
        processed, _ = M.preprocess_day(canonical, thresholds={"speed_valid_upper_bound": 120,
                                      "rpm_motor_ratio_reference": .061}, config=config)
        assert processed.loc[0, "rpm_canonical_candidate"] == 4.5


def test_duplicate_daily_names_are_detected_before_overwrite(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ["download (1).csv", "download (2).csv"]:
        pd.DataFrame({M.TIME_FIELD: ["2023-12-11 12:00:00"]}).to_csv(source / name, index=False)
    with pytest.raises(ValueError, match="collisions"):
        M.inventory([source], tmp_path / "output")


def test_date_does_not_change_row_repair_result():
    frames = []
    for date in ["2023-11-03", "2023-12-11", "2024-04-07"]:
        raw = fixture(True).astype(str)
        raw.loc[0, M.TIME_FIELD] = date + " 12:00:00"
        repaired, evidence = M.repair_frame(raw)
        fixed, _ = M.exact_mapping_tokens(raw, repaired, evidence)
        frames.append(fixed.loc[0, "刀盘实际转速"])
    assert frames == ["4.5", "4.5", "4.5"]
