from __future__ import annotations

from pathlib import Path

from tbm_process.llm_reporting.content_planner import build_content_plan
from tbm_process.llm_reporting.draft import (
    DRAFT_SCHEMA,
    build_template_draft,
    render_fact_sentence,
)
from tbm_process.llm_reporting.fact_adapter import build_typed_fact_pool, fact_map
from tbm_process.llm_reporting.reviser import revise_draft
from tbm_process.llm_reporting.verifier import verify_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "tests/fixtures/current_llm_inputs"


def load_day(date: str):
    pool, _ = build_typed_fact_pool(
        SOURCE_ROOT / f"{date}.llm_input.research.json",
    )
    return pool, build_content_plan(pool)


def make_draft(date: str, *, overview=None, main=None, responses=None):
    fallback = [{"text": "现有信息不足，不能确认。", "supporting_fact_ids": []}]
    return {
        "schema_version": DRAFT_SCHEMA,
        "date": date,
        "method_group": "regression",
        "sections": [
            {"section_id": "overview", "heading": "一、当日施工概况", "sentences": overview or fallback},
            {"section_id": "main_process", "heading": "二、主要施工过程", "sentences": main or fallback},
            {"section_id": "responses", "heading": "三、值得关注的运行情况", "sentences": responses or fallback},
        ],
    }


def errors(verification):
    result = [error for row in verification["sentences"] for error in row["error_types"]]
    result += [check.get("error_type") for check in verification["global_checks"] if check.get("error_type")]
    return result


def test_2023_11_18_span_cannot_use_activity_duration() -> None:
    pool, plan = load_day("2023-11-18")
    value = make_draft(
        "2023-11-18",
        main=[{
            "text": "00:05—02:29时段总跨度约141分钟。",
            "supporting_fact_ids": ["2023-11-18-P01"],
        }],
    )
    result = verify_report(value, pool, plan)

    assert "numeric_role_mismatch:elapsed_timestamp_span" in errors(result)


def test_2023_10_28_quality_events_and_records_are_not_interchangeable() -> None:
    pool, plan = load_day("2023-10-28")
    value = make_draft(
        "2023-10-28",
        overview=[{
            "text": "推进速度不可信事件293处，涉及50条PLC记录。",
            "supporting_fact_ids": ["2023-10-28-Q01"],
        }],
    )
    result = verify_report(value, pool, plan)

    assert "numeric_role_mismatch:quality_episode_count" in errors(result)
    assert "numeric_role_mismatch:affected_plc_record_count" in errors(result)


def test_2023_11_14_parameter_value_conflicts_with_not_reported_statement() -> None:
    pool, plan = load_day("2023-11-14")
    mapping = fact_map(pool)
    parameter_id = next(fact_id for fact_id, fact in mapping.items() if fact["fact_type"] == "daily_advancing_parameter_statistics")
    value = make_draft(
        "2023-11-14",
        overview=[
            {"text": render_fact_sentence(mapping[parameter_id]), "supporting_fact_ids": [parameter_id]},
            {"text": "刀盘转速可疑记录未写入参数结论。", "supporting_fact_ids": ["2023-11-14-Q01"]},
        ],
    )
    result = verify_report(value, pool, plan)

    assert "parameter_value_quality_statement_contradiction" in errors(result)


def test_2023_11_30_response_events_must_be_chronological() -> None:
    pool, plan = load_day("2023-11-30")
    mapping = fact_map(pool)
    value = make_draft(
        "2023-11-30",
        responses=[
            {"text": render_fact_sentence(mapping["2023-11-30-R04"]), "supporting_fact_ids": ["2023-11-30-R04"]},
            {"text": render_fact_sentence(mapping["2023-11-30-R01"]), "supporting_fact_ids": ["2023-11-30-R01"]},
        ],
    )
    result = verify_report(value, pool, plan)

    assert "response_event_order_error" in errors(result)


def test_local_revision_uses_actual_draft_and_removes_explicit_time_error() -> None:
    pool, plan = load_day("2023-12-23")
    value = build_template_draft(pool, plan)
    value["method_group"] = "D"
    response_section = next(
        section for section in value["sections"] if section["section_id"] == "responses"
    )
    response_section["sentences"][0] = {
        "text": (
            "18:36:10至18:43:00出现响应；该响应关联18:04:30至21:50:20的活动时段。"
        ),
        "supporting_fact_ids": ["2023-12-23-R01"],
    }
    before = verify_report(value, pool, plan)
    revised, log, after = revise_draft(
        value, pool, plan, verification_before=before
    )

    assert "time_boundary_mismatch" in errors(before)
    assert log["change_count"] == 1
    assert revised["method_group"] == "E"
    assert after["metrics"]["fail_claim_count"] == 0
    assert after["metrics"]["required_fact_coverage"] == 1.0
