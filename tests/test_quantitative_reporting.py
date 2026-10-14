from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tbm_process.reporting.quantitative import (
    build_quantitative_report_payload,
    load_report_parameter_registry,
    render_quantitative_report,
)


ROOT = Path(__file__).resolve().parents[1]
DATE = "2023-11-03"


@pytest.fixture(scope="module")
def payload() -> dict:
    frozen_path = ROOT / "outputs_frozen_v1" / "structured_full_days" / f"{DATE}.json"
    semantic_path = ROOT / "outputs" / "report_semantics" / f"{DATE}.json"
    samples_path = ROOT / "outputs_frozen_v1" / "processed_samples" / f"{DATE}.csv"
    registry_path = ROOT / "configs" / "report_parameter_units.json"
    missing_paths = [
        path
        for path in (frozen_path, semantic_path, samples_path)
        if not path.exists()
    ]
    if missing_paths:
        pytest.skip(
            "requires locally generated frozen-analysis artifacts: "
            + ", ".join(str(path.relative_to(ROOT)) for path in missing_paths)
        )
    return build_quantitative_report_payload(
        frozen_result=json.loads(frozen_path.read_text(encoding="utf-8")),
        semantic_payload=json.loads(semantic_path.read_text(encoding="utf-8")),
        samples=pd.read_csv(samples_path),
        unit_registry=load_report_parameter_registry(registry_path),
    )


def _fact(payload: dict, section: str, fact_type: str) -> dict:
    return next(
        row for row in payload[section] if row.get("fact_type") == fact_type
    )


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_today_advance_uses_only_guidance_chainage(payload: dict) -> None:
    fact = _fact(payload, "daily_overview", "daily_advance")
    assert fact["distance_source"] == "guidance_shield_head_chainage"
    assert fact["chainage_start"] == pytest.approx(1_013_901.0)
    assert fact["chainage_end"] == pytest.approx(1_013_910.0)
    assert fact["distance_m"] == pytest.approx(9.0)


def test_state_percentages_use_observed_state_duration_denominator(payload: dict) -> None:
    fact = _fact(payload, "daily_overview", "machine_state_duration")
    assert fact["percentage_denominator"]["duration_seconds"] == pytest.approx(86_360.0)
    assert fact["percentage_denominator"]["observed_duration_seconds"] == pytest.approx(
        86_360.0
    )
    assert fact["unclassified_duration_seconds"] == pytest.approx(0.0)
    percentages = {
        key: value["percentage"] for key, value in fact["state_statistics"].items()
    }
    assert percentages["advancing"] == pytest.approx(39.242705, rel=1e-6)
    assert percentages["free_rotation"] == pytest.approx(7.78138, rel=1e-6)
    assert percentages["stopped"] == pytest.approx(52.975915, rel=1e-6)
    assert sum(percentages.values()) == pytest.approx(100.0)


def test_daily_parameter_scope_is_advancing_state_only(payload: dict) -> None:
    fact = _fact(
        payload, "daily_overview", "daily_advancing_parameter_statistics"
    )
    assert fact["scope"]["filter"] == "machine_state == advancing"
    assert fact["scope"]["scope_sample_count"] == 1968
    speed = fact["parameter_statistics"]["advance_speed"]
    assert speed["valid_count"] == 1968
    assert speed["valid_ratio"] == pytest.approx(1.0)
    assert speed["median"] == pytest.approx(3.674805)
    assert speed["p10"] == pytest.approx(0.55151369)
    assert speed["p90"] == pytest.approx(48.50708)


def test_unconfirmed_units_are_never_guessed(payload: dict) -> None:
    registry = payload["parameter_registry"]["parameters"]
    for parameter in ("advance_speed", "thrust", "torque", "rpm"):
        assert registry[parameter]["engineering_unit"] is None
        assert registry[parameter]["formal_numeric_reporting"] is False


def test_formal_llm_input_withholds_unconfirmed_parameter_values(payload: dict) -> None:
    keys = set(_walk_keys(payload["llm_input_packages"]["formal"]))
    forbidden = {
        "p10",
        "median",
        "p90",
        "initial_representative_value",
        "final_representative_value",
        "absolute_change",
        "actual_percent_change_from_raw_records",
        "left_value",
        "right_value",
        "raw_value_difference",
        "actual_percent_difference_from_raw_records",
        "current_value",
        "lower_threshold",
        "upper_threshold",
    }
    assert not (keys & forbidden)


def test_research_input_retains_raw_values_with_warning(payload: dict) -> None:
    package_text = json.dumps(
        payload["llm_input_packages"]["research_preview"], ensure_ascii=False
    )
    assert "原始PLC记录值，工程单位待核实" in package_text
    assert '"median"' in package_text
    assert "actual_percent_change_from_raw_records" in package_text


def test_representative_activity_statistics_match_frozen_stable_phase(payload: dict) -> None:
    process = next(
        row
        for row in payload["main_processes"]
        if row.get("fact_id") == f"{DATE}-P06"
    )
    activity = next(
        row for row in process["activities"] if row["start_time"].startswith(f"{DATE}T13:31")
    )
    assert activity["frozen_phase_statistics_match"] is True
    assert activity["statistics_scope"]["scope_sample_count"] == 148
    stats = activity["report_parameter_statistics"]
    assert stats["advance_speed"]["median"] == pytest.approx(42.07605)
    assert stats["thrust"]["median"] == pytest.approx(21_562.74)
    assert stats["torque"]["median"] == pytest.approx(4_259.813)


def test_response_actual_change_and_percentage_are_independent_of_internal_score(
    payload: dict,
) -> None:
    response = next(
        row
        for row in payload["construction_responses"]
        if row["start_time"].startswith(f"{DATE}T13:34")
    )
    speed = response["parameter_states"]["advance_speed"]
    assert speed["initial_representative_value"] == pytest.approx(31.05182)
    assert speed["final_representative_value"] == pytest.approx(37.75842)
    assert speed["absolute_change"] == pytest.approx(6.7066)
    assert speed["actual_percent_change_from_raw_records"] == pytest.approx(
        21.59809, rel=1e-6
    )
    assert speed["absolute_change_matches_frozen_event"] is True
    research_text = json.dumps(
        payload["llm_input_packages"]["research_preview"], ensure_ascii=False
    )
    assert "relative_change_internal_only" not in research_text


def test_intermittent_process_preserves_activity_and_gap_meanings(payload: dict) -> None:
    process = next(
        row
        for row in payload["main_processes"]
        if row.get("fact_id") == f"{DATE}-P06"
    )
    assert process["continuity_class"] == "intermittent_activities"
    assert len(process["activities"]) == 3
    assert len(process["intervening_gaps"]) == 2
    accounting = process["duration_accounting"]
    assert accounting["elapsed_timestamp_span_seconds"] == pytest.approx(11_110.0)
    assert accounting["activity_duration_sum_seconds"] == pytest.approx(6_760.0)
    assert accounting["intervening_timestamp_gap_sum_seconds"] == pytest.approx(4_410.0)
    assert accounting["activity_plus_gap_minus_elapsed_seconds"] == pytest.approx(60.0)


def test_quality_event_count_is_not_record_count(payload: dict) -> None:
    by_subtype = {row["subtype"]: row for row in payload["data_quality"]}
    assert by_subtype["unreliable_advance_speed"]["event_count"] == 1
    assert by_subtype["unreliable_advance_speed"]["affected_record_count"] == 2
    assert by_subtype["unreliable_canonical_rpm"]["event_count"] == 17
    assert by_subtype["unreliable_canonical_rpm"]["affected_record_count"] == 57
    assert by_subtype["unreliable_penetration"]["event_count"] == 12
    assert by_subtype["unreliable_penetration"]["affected_record_count"] == 14


def test_rendered_reports_respect_formal_and_research_boundaries(payload: dict) -> None:
    formal = render_quantitative_report(payload, mode="formal")
    research = render_quantitative_report(payload, mode="research_preview")
    assert "42.08" not in formal
    assert "21,562.74" not in formal
    assert "42.08" in research
    assert "21,562.74" in research
    assert "原始PLC记录值，工程单位待核实" in research
    for unsupported in ("围岩变硬", "刀具磨损", "设备故障", "维修处置"):
        assert unsupported not in formal
        assert unsupported not in research


def test_quantitative_preview_is_not_marked_as_llm_output(payload: dict) -> None:
    assert payload["generation"]["llm_invoked"] is False
    assert payload["generation"]["frozen_analysis_modified"] is False
    assert payload["validation"]["passed"] is True
