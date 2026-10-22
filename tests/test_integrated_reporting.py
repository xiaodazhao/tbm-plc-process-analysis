from __future__ import annotations

from copy import deepcopy
from argparse import Namespace
import json
import sys
from types import SimpleNamespace

import pytest

from tbm_process.final_reporting.integrated_facts import build_integrated_facts, build_llm_input
from tbm_process.final_reporting.integrated_llm import (
    DRAFT_SCHEMA,
    ModelResponseError,
    PLAN_SCHEMA,
    build_generation_request,
    build_generation_repair_request,
    build_plan_request,
    build_plan_repair_request,
    call_json_model,
    validate_draft,
    validate_plan,
)
from tbm_process.final_reporting.rendering import _narrative_html
from scripts.run_integrated_report import _geology_plan, _run_model


def _plc() -> dict:
    return {
        "date": "2024-01-06",
        "daily_overview": {
            "record_count": 100,
            "coverage_start": "2024-01-06T00:00:00",
            "coverage_end": "2024-01-06T23:59:40",
            "hard_gap_count": 0,
            "daily_advance": {"start_value_m": 1014726.0, "end_value_m": 1014743.0, "net_change_m": 17.0},
            "working_duration_seconds": 3600,
            "stopped_duration_seconds": 3600,
            "effective_state_duration_seconds": 7200,
            "utilization_percent": 50.0,
            "duration_definition": "capped_observed_intervals",
        },
        "daily_parameter_statistics": {"unit_note": "原始PLC记录值，工程单位待核实", "parameters": {}},
        "platforms": [
            {
                "platform_id": "2024-01-06-E001",
                "start_time": "2024-01-06T09:00:00",
                "end_time": "2024-01-06T09:10:00",
                "working_duration_seconds": 600,
                "stopped_duration_seconds_inside_platform": 0,
                "chainage_start": 1014728.0,
                "chainage_end": 1014730.0,
                "interval_advance": {"use_status": "adoptable", "net_change_m": 0.5},
                "working_parameter_statistics": {"advance_speed": {"median": 25.0}},
                "stable_phase": {"exists": True},
                "reader_description": "速度总体平稳",
                "source_row_start": 10,
                "source_row_end": 40,
            }
        ],
        "major_construction_changes": [],
    }


def _geology() -> dict:
    source = {"source_report_id": "R1", "source_type": "hsp", "source_file": "R1.pdf", "source_page": 10}
    return {
        "date": "2024-01-06",
        "plc_context": {"guide_shield_start_m": 1014726.0, "guide_shield_end_m": 1014743.0, "source_file": "raw.csv"},
        "planning_obligations": [],
        "content_units": [
            {
                "unit_id": "CU001",
                "source_fact_id": "F1",
                "unit_type": "forecast_segment_portion",
                "source_type": "hsp",
                "semantics": "prediction",
                "allowed_section": "3.1",
                "required_for_plan": True,
                "evidence": {
                    "original_start_chainage": "DyK1014+720",
                    "original_end_chainage": "DyK1014+735",
                    "applicable_start_chainage": "DyK1014+726",
                    "applicable_end_chainage": "DyK1014+735",
                    "recommended_grade": "Ⅳ",
                    "report_date_basis": {"status": "eligible", "basis_date": "2024-01-05"},
                },
                "source": source,
            },
            {
                "unit_id": "CU002",
                "source_fact_id": "F2",
                "unit_type": "forecast_risk_point",
                "source_type": "hsp",
                "semantics": "prediction",
                "allowed_section": "3.1",
                "required_for_plan": True,
                "evidence": {"risk_chainage": "DyK1014+729", "risk_type": "掉块风险"},
                "source": source,
            },
        ],
    }


def _plan() -> dict:
    return {
        "schema_version": PLAN_SCHEMA,
        "chapters": [
            {"chapter_id": "1", "paragraphs": [{"paragraph_id": "C1P1", "section_id": None, "purpose": "当日概况", "fact_ids": ["PLC:daily_advance", "PLC:machine_time", "PLC:coverage"], "link_ids": []}]},
            {"chapter_id": "2", "paragraphs": [{"paragraph_id": "C2P1", "section_id": None, "purpose": "运行过程", "fact_ids": ["PLC:platform:2024-01-06-E001"], "link_ids": []}]},
            {"chapter_id": "3", "paragraphs": [{"paragraph_id": "C3P1", "section_id": "3.1", "purpose": "里程对应与预测", "fact_ids": ["GEO:CU001", "GEO:CU002", "PLC:platform:2024-01-06-E001"], "link_ids": ["LINK:0001"]}]},
        ],
    }


def _draft() -> dict:
    return {
        "schema_version": DRAFT_SCHEMA,
        "chapters": [
            {"chapter_id": "1", "paragraphs": [{"paragraph_id": "C1P1", "claims": [
                {"text": "2024年1月6日，导向盾首里程由DyK1014+726至DyK1014+743，今日进尺17.00 m。", "supporting_fact_ids": ["PLC:daily_advance", "PLC:coverage"], "supporting_link_ids": []},
                {"text": "设备工作1小时0分0秒，停机1小时0分0秒，时间利用率50.0%。", "supporting_fact_ids": ["PLC:machine_time"], "supporting_link_ids": []},
            ]}]},
            {"chapter_id": "2", "paragraphs": [{"paragraph_id": "C2P1", "claims": [
                {"text": "该时段记录到运行平台。", "supporting_fact_ids": ["PLC:platform:2024-01-06-E001"], "supporting_link_ids": []},
            ]}]},
            {"chapter_id": "3", "paragraphs": [{"paragraph_id": "C3P1", "claims": [
                {"text": "该运行平台与预报分段的适用里程存在重叠。", "supporting_fact_ids": ["PLC:platform:2024-01-06-E001", "GEO:CU001"], "supporting_link_ids": ["LINK:0001"]},
                {"text": "HSP报告提示掉块风险。", "supporting_fact_ids": ["GEO:CU002"], "supporting_link_ids": []},
            ]}]},
        ],
    }


def test_joint_package_contains_both_sources_and_spatial_link() -> None:
    package = build_integrated_facts(_plc(), _geology())
    assert {row["source"]["domain"] for row in package["facts"]} == {"plc", "geology"}
    assert package["cross_source_links"][0]["relation"] == "spatial_co_location_only"
    risk = next(row for row in package["facts"] if row["fact_id"] == "GEO:CU002")
    assert risk["data"]["available_date_basis"] == "same_source_report_forecast_unit"
    llm_input = build_llm_input(package)
    request = build_plan_request(llm_input, model="test-model")
    assert "PLC:platform:2024-01-06-E001" in request["messages"][1]["content"]
    assert "GEO:CU001" in request["messages"][1]["content"]


def test_mismatched_chainage_and_future_report_fail_closed() -> None:
    geology = _geology()
    geology["plc_context"]["guide_shield_start_m"] += 1
    with pytest.raises(ValueError, match="different daily chainage"):
        build_integrated_facts(_plc(), geology)
    geology = _geology()
    geology["content_units"][0]["evidence"]["report_date_basis"]["basis_date"] = "2024-01-07"
    with pytest.raises(ValueError, match="not available"):
        build_integrated_facts(_plc(), geology)


def test_conflicting_or_ineligible_geology_date_fails_closed() -> None:
    geology = _geology()
    second = deepcopy(geology["content_units"][0])
    second["unit_id"] = "CU003"
    second["evidence"]["report_date_basis"]["basis_date"] = "2024-01-04"
    geology["content_units"].append(second)
    with pytest.raises(ValueError, match="Conflicting availability dates"):
        build_integrated_facts(_plc(), geology)

    geology = _geology()
    geology["content_units"][1]["evidence"]["report_date_basis"] = {"status": "ineligible"}
    with pytest.raises(ValueError, match="ineligible date evidence"):
        build_integrated_facts(_plc(), geology)


def test_empty_geology_has_explicit_availability_fact() -> None:
    geology = _geology()
    geology["content_units"] = []
    package = build_integrated_facts(_plc(), geology)
    availability = next(row for row in package["facts"] if row["fact_id"] == "GEO:availability")
    assert availability["required_for_plan"]
    assert availability["data"]["eligible_content_unit_count"] == 0
    assert package["cross_source_links"] == []


def test_global_plan_and_draft_are_traceable() -> None:
    package = build_integrated_facts(_plc(), _geology())
    plan = _plan()
    assert validate_plan(plan, package)["passed"]
    request = build_generation_request(build_llm_input(package), plan, model="test-model")
    assert "PLC:daily_advance" in request["messages"][1]["content"]
    assert "GEO:CU001" in request["messages"][1]["content"]
    check = validate_draft(_draft(), plan, package)
    assert check["passed"], check["errors"]
    assert check["review_items"]
    assert all(row["status"] == "REVIEW" for row in check["review_items"])


def test_wrong_number_unlinked_cross_source_and_observation_claim_fail() -> None:
    package = build_integrated_facts(_plc(), _geology())
    plan = _plan()
    wrong_number = deepcopy(_draft())
    wrong_number["chapters"][0]["paragraphs"][0]["claims"][0]["text"] = wrong_number["chapters"][0]["paragraphs"][0]["claims"][0]["text"].replace("17.00 m", "99.00 m")
    assert any("unsupported_numbers" in row for row in validate_draft(wrong_number, plan, package)["errors"])
    missing_link = deepcopy(_draft())
    missing_link["chapters"][2]["paragraphs"][0]["claims"][0]["supporting_link_ids"] = []
    assert any("cross_source_claim_without_link" in row for row in validate_draft(missing_link, plan, package)["errors"])
    false_observation = deepcopy(_draft())
    false_observation["chapters"][2]["paragraphs"][0]["claims"][0]["text"] = "该预报范围的地质已实际揭露。"
    assert any("forecast_written_as_observation" in row for row in validate_draft(false_observation, plan, package)["errors"])

    wrong_chainage = deepcopy(_draft())
    wrong_chainage["chapters"][0]["paragraphs"][0]["claims"][0]["text"] = wrong_chainage["chapters"][0]["paragraphs"][0]["claims"][0]["text"].replace("DyK1014+726", "DyK1024+726")
    assert any("unsupported_numbers" in row for row in validate_draft(wrong_chainage, plan, package)["errors"])


def test_required_geology_cannot_be_silently_dropped() -> None:
    package = build_integrated_facts(_plc(), _geology())
    plan = _plan()
    plan["chapters"][2]["paragraphs"][0]["fact_ids"].remove("GEO:CU002")
    assert any("missing_required_facts" in row for row in validate_plan(plan, package)["errors"])


def test_global_plan_maps_to_geology_renderer_and_escapes_prose() -> None:
    geology_plan = _geology_plan(_plan())
    assert geology_plan["sections"][0]["paragraphs"][0]["content_unit_ids"] == ["CU001", "CU002"]
    assert geology_plan["sections"][1]["paragraphs"] == []
    draft = _draft()
    draft["chapters"][1]["paragraphs"][0]["claims"][0]["text"] = "<script>"
    assert "&lt;script&gt;" in _narrative_html(draft, "2")
    assert "<script>" not in _narrative_html(draft, "2")


def test_third_chapter_cannot_drop_geology_entirely() -> None:
    package = build_integrated_facts(_plc(), _geology())
    plan = _plan()
    plan["chapters"][2]["paragraphs"][0]["fact_ids"] = ["PLC:platform:2024-01-06-E001"]
    plan["chapters"][2]["paragraphs"][0]["link_ids"] = []
    assert "chapter3_has_no_geology_fact" in validate_plan(plan, package)["errors"]


def test_plan_repair_request_contains_errors_and_original_plan() -> None:
    package = build_integrated_facts(_plc(), _geology())
    plan = _plan()
    plan["chapters"][2]["paragraphs"][0]["fact_ids"].remove("PLC:platform:2024-01-06-E001")
    errors = validate_plan(plan, package)["errors"]
    assert "link_facts_not_in_paragraph:LINK:0001" in errors
    request = build_plan_repair_request(build_llm_input(package), plan, errors, model="test-model")
    assert "link_facts_not_in_paragraph:LINK:0001" in request["messages"][1]["content"]
    assert "GEO:CU001" in request["messages"][1]["content"]
    assert "data.section" in request["messages"][0]["content"]


def test_same_source_sketch_date_can_be_inherited_with_provenance() -> None:
    geology = _geology()
    source = {"source_report_id": "S1", "source_type": "sketch", "source_file": "S1.pdf", "source_sha256": "abc", "source_page": 1}
    dated = {
        "unit_id": "CU003", "source_fact_id": "S1", "unit_type": "historical_observation",
        "source_type": "sketch", "semantics": "observation", "allowed_section": "3.1",
        "required_for_plan": True, "evidence": {"observation_date": "2024-01-05"}, "source": source,
    }
    undated = deepcopy(dated)
    undated["unit_id"] = "CU004"
    undated["evidence"] = {"observation": "节理发育"}
    geology["content_units"].extend([dated, undated])
    package = build_integrated_facts(_plc(), geology)
    fact = next(row for row in package["facts"] if row["fact_id"] == "GEO:CU004")
    assert fact["data"]["available_date"] == "2024-01-05"
    assert fact["data"]["available_date_basis"] == "same_source_report_observation_unit"

    unrelated = deepcopy(geology)
    unrelated["content_units"][-1]["source"]["source_sha256"] = "different"
    with pytest.raises(ValueError, match="lacks a date"):
        build_integrated_facts(_plc(), unrelated)

    no_sha = deepcopy(geology)
    for unit in no_sha["content_units"][-2:]:
        unit["source"].pop("source_sha256")
    with pytest.raises(ValueError, match="lacks a date"):
        build_integrated_facts(_plc(), no_sha)

    conflicting = deepcopy(geology)
    conflicting["content_units"][-1]["evidence"]["observation_date"] = "2024-01-04"
    with pytest.raises(ValueError, match="Conflicting observation dates"):
        build_integrated_facts(_plc(), conflicting)

    future = deepcopy(geology)
    future["content_units"][-2]["evidence"]["observation_date"] = "2024-01-06"
    with pytest.raises(ValueError, match="not available"):
        build_integrated_facts(_plc(), future)


@pytest.mark.parametrize("phrase", ["并非实际揭露", "不得改写为实际揭露", "预测而非实际揭露"])
def test_forecast_negation_does_not_trigger_false_observation(phrase: str) -> None:
    package = build_integrated_facts(_plc(), _geology())
    draft = _draft()
    draft["chapters"][2]["paragraphs"][0]["claims"][1]["text"] = f"HSP报告仅作预测，{phrase}。"
    assert validate_draft(draft, _plan(), package)["passed"]
    draft["chapters"][2]["paragraphs"][0]["claims"][1]["text"] += "但该段已实际揭露。"
    assert any("forecast_written_as_observation" in item for item in validate_draft(draft, _plan(), package)["errors"])


def test_model_request_explicitly_disables_thinking() -> None:
    request = build_plan_request(build_llm_input(build_integrated_facts(_plc(), _geology())), model="test-model")
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["max_tokens"] >= 10000


def test_historical_observation_negation_and_repair_request() -> None:
    geology = _geology()
    geology["content_units"].append({
        "unit_id": "CU003", "source_fact_id": "S1", "unit_type": "historical_observation",
        "source_type": "sketch", "semantics": "observation", "allowed_section": "3.1",
        "required_for_plan": True, "evidence": {"observation_date": "2024-01-05"},
        "source": {"source_report_id": "S1", "source_type": "sketch", "source_file": "S1.pdf", "source_page": 1},
    })
    package = build_integrated_facts(_plc(), geology)
    plan = _plan()
    plan["chapters"][2]["paragraphs"][0]["fact_ids"].append("GEO:CU003")
    draft = _draft()
    draft["chapters"][2]["paragraphs"][0]["claims"].append({
        "text": "此前观察仅为历史背景，不代表当日实际揭露。",
        "supporting_fact_ids": ["GEO:CU003"], "supporting_link_ids": [],
    })
    assert validate_draft(draft, plan, package)["passed"]
    draft["chapters"][2]["paragraphs"][0]["claims"][-1]["text"] = "当日已实际揭露该地层。"
    check = validate_draft(draft, plan, package)
    assert any("historical_observation_written_as_current" in row for row in check["errors"])
    request = build_generation_repair_request(build_llm_input(package), plan, draft, check["errors"], model="test-model")
    assert "validation_errors" in request["messages"][1]["content"]
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


def test_platform_ordinal_and_formatted_chainage_are_checked_against_source() -> None:
    package = build_integrated_facts(_plc(), _geology())
    draft = _draft()
    claim = draft["chapters"][1]["paragraphs"][0]["claims"][0]
    claim["text"] = "第1个平台位于DyK1014+728至DyK1014+730。"
    assert validate_draft(draft, _plan(), package)["passed"]
    claim["text"] = "第2个平台位于DyK1014+728至DyK1014+730。"
    assert any("unsupported_numbers:C2P1:2" in row for row in validate_draft(draft, _plan(), package)["errors"])
    claim["text"] = "第1个平台位于DyK1014+728至DyK1014+999。"
    assert any("unsupported_numbers:C2P1:999" in row for row in validate_draft(draft, _plan(), package)["errors"])


def test_truncated_model_response_keeps_raw_record(tmp_path, monkeypatch) -> None:
    class FakeResponse:
        choices = [SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"incomplete":'))]
        usage = SimpleNamespace(prompt_tokens=21, completion_tokens=9)
        model = "test-model"
        id = "response-1"

        def model_dump(self, *, mode):
            return {"id": self.id, "model": self.model, "choices": [{"finish_reason": "length", "message": {"content": '{"incomplete":'}}]}

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: FakeResponse())))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kwargs: fake_client))
    with pytest.raises(ModelResponseError, match="finish_reason_length") as error:
        call_json_model({"model": "test-model"}, api_key="test-key", base_url="https://example.invalid", timeout_seconds=1)
    assert error.value.metadata["input_tokens"] == 21
    with pytest.raises(ModelResponseError):
        _run_model({"model": "test-model"}, stage="plan", output=tmp_path, api_key="test-key", args=Namespace(base_url="https://example.invalid", timeout_seconds=1))
    assert json.loads((tmp_path / "plan.response.json").read_text())["id"] == "response-1"
    assert json.loads((tmp_path / "plan.failure.json").read_text())["reason"] == "finish_reason_length"
