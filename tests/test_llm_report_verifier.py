from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from tbm_process.llm_reporting.content_planner import build_content_plan
from tbm_process.llm_reporting.draft import DRAFT_SCHEMA, build_template_draft
from tbm_process.llm_reporting.fact_adapter import build_typed_fact_pool, fact_map
from tbm_process.llm_reporting.verifier import (
    compare_draft_information,
    verify_report,
)
from tbm_process.llm_reporting.verifier_benchmark import run_verifier_benchmark


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "tests/fixtures/current_llm_inputs"
EXPERIMENT_DATES = (
    "2023-09-23",
    "2023-10-18",
    "2023-10-28",
    "2023-11-03",
    "2023-11-14",
    "2023-11-18",
    "2023-11-28",
    "2023-11-30",
    "2023-12-06",
    "2023-12-23",
)


def load_day(date: str = "2023-11-03"):
    pool, _ = build_typed_fact_pool(
        SOURCE_ROOT / f"{date}.llm_input.research.json",
    )
    return pool, build_content_plan(pool)


def make_draft(date: str, text: str, ids: list[str]) -> dict:
    fallback = [{"text": "现有信息不足，不能确认。", "supporting_fact_ids": []}]
    return {
        "schema_version": DRAFT_SCHEMA,
        "date": date,
        "method_group": "v2_test",
        "sections": [
            {
                "section_id": "overview",
                "heading": "一、当日施工概况",
                "sentences": [{"text": text, "supporting_fact_ids": ids}],
            },
            {"section_id": "main_process", "heading": "二、主要施工过程", "sentences": fallback},
            {"section_id": "responses", "heading": "三、值得关注的运行情况", "sentences": fallback},
        ],
    }


def first_row(result: dict) -> dict:
    return result["sentences"][0]


def test_chinese_meter_value_is_checked_instead_of_ignored() -> None:
    pool, plan = load_day()
    mapping = fact_map(pool)
    advance = next(fact for fact in mapping.values() if fact["fact_type"] == "daily_advance")
    correct = float(advance["data"]["distance_m"])

    good = verify_report(
        make_draft(pool["date"], f"今日进尺{correct:g}米。", [advance["fact_id"]]),
        pool,
        plan,
    )
    bad = verify_report(
        make_draft(pool["date"], f"今日进尺{correct + 5:g}米。", [advance["fact_id"]]),
        pool,
        plan,
    )

    assert first_row(good)["status"] == "PASS"
    assert first_row(bad)["status"] == "FAIL"
    assert "numeric_role_mismatch:daily_advance" in first_row(bad)["error_types"]


def test_compound_sentence_does_not_hide_extra_unsupported_claim() -> None:
    pool, plan = load_day()
    mapping = fact_map(pool)
    advance = next(fact for fact in mapping.values() if fact["fact_type"] == "daily_advance")
    distance = float(advance["data"]["distance_m"])
    result = verify_report(
        make_draft(
            pool["date"],
            f"今日进尺{distance:g}米，设备运行完全正常。",
            [advance["fact_id"]],
        ),
        pool,
        plan,
    )

    row = first_row(result)
    assert row["status"] == "REVIEW"
    assert len(row["claims"]) == 2
    assert row["claims"][0]["status"] == "PASS"
    assert row["claims"][1]["status"] == "REVIEW"


def test_duration_roles_and_quality_count_roles_are_not_interchangeable() -> None:
    pool, plan = load_day("2023-10-28")
    mapping = fact_map(pool)
    process = next(
        fact
        for fact in mapping.values()
        if fact["fact_type"] == "representative_construction_period"
        and fact["data"]["elapsed_span_seconds"] != fact["data"]["total_activity_duration_seconds"]
    )
    quality = next(
        fact
        for fact in mapping.values()
        if fact["fact_type"] == "data_quality_limitation"
        and fact["data"]["event_count"] != fact["data"]["affected_record_count"]
    )
    duration_result = verify_report(
        make_draft(
            pool["date"],
            (
                f"时段总跨度约{process['data']['total_activity_duration_seconds']}秒，"
                f"累计活动时长约{process['data']['elapsed_span_seconds']}秒。"
            ),
            [process["fact_id"]],
        ),
        pool,
        plan,
    )
    quality_result = verify_report(
        make_draft(
            pool["date"],
            (
                f"推进速度不可信质量事件{quality['data']['affected_record_count']}处，"
                f"涉及{quality['data']['event_count']}条PLC记录。"
            ),
            [quality["fact_id"]],
        ),
        pool,
        plan,
    )

    assert first_row(duration_result)["status"] == "FAIL"
    assert first_row(quality_result)["status"] == "FAIL"
    assert any("elapsed_timestamp_span" in value for value in first_row(duration_result)["error_types"])
    assert any("quality_episode_count" in value for value in first_row(quality_result)["error_types"])
    assert any("affected_plc_record_count" in value for value in first_row(quality_result)["error_types"])


def test_wrong_existing_fact_id_is_review_not_pass() -> None:
    pool, plan = load_day()
    mapping = fact_map(pool)
    advance = next(fact for fact in mapping.values() if fact["fact_type"] == "daily_advance")
    coverage = next(fact for fact in mapping.values() if fact["fact_type"] == "data_coverage")
    result = verify_report(
        make_draft(
            pool["date"],
            f"今日进尺{advance['data']['distance_m']}米。",
            [coverage["fact_id"]],
        ),
        pool,
        plan,
    )

    assert first_row(result)["status"] == "REVIEW"
    assert "supporting_fact_id_mismatch" in first_row(result)["error_types"]


def test_information_retention_detects_removed_numeric_roles() -> None:
    pool, plan = load_day()
    before = build_template_draft(pool, plan)
    before["method_group"] = "D"
    after = deepcopy(before)
    after["method_group"] = "E"
    overview = after["sections"][0]["sentences"]
    overview[:] = [item for item in overview if "主要参数统计" not in item["text"]]

    before_verification = verify_report(before, pool, plan)
    after_verification = verify_report(after, pool, plan)
    retention = compare_draft_information(
        before, after, before_verification, after_verification
    )

    assert retention["possible_information_loss"] is True
    assert retention["declared_fact_ids"]["retention_rate"] < 1.0
    assert retention["numeric_role_signatures"]["retention_rate"] < 1.0


def test_fixed_positive_and_error_injection_benchmark_has_no_known_misses() -> None:
    pool, plan = load_day()
    benchmark = run_verifier_benchmark(pool, plan)

    assert benchmark["positive_case_count"] >= 5
    assert benchmark["injected_error_case_count"] >= 9
    assert benchmark["false_rejection_count"] == 0
    assert benchmark["missed_injection_count"] == 0


def test_distinct_machine_state_name_is_not_double_counted_as_advancing() -> None:
    pool, plan = load_day("2023-10-28")
    mapping = fact_map(pool)
    states = next(fact for fact in mapping.values() if fact["fact_type"] == "machine_state_duration")
    state = next(item for item in states["data"]["states"] if item["state"] == "thrusting_no_advance")
    result = verify_report(
        make_draft(
            pool["date"],
            (
                f"加压但未满足推进条件状态累计{state['duration_seconds']:g}秒，"
                f"占比{state['percentage']:.2f}%。"
            ),
            [states["fact_id"]],
        ),
        pool,
        plan,
    )

    row = first_row(result)
    assert row["status"] == "PASS"
    assert not any("machine_state_duration:advancing" in value for value in row["error_types"])


def test_process_duration_without_literal_shichang_keeps_activity_role() -> None:
    pool, plan = load_day("2023-10-18")
    mapping = fact_map(pool)
    process = next(
        fact
        for fact in mapping.values()
        if fact["fact_type"] == "representative_construction_period"
        and fact["data"]["continuity_class"] != "intermittent_activities"
    )
    duration = process["data"]["total_activity_duration_seconds"]
    for wording in (f"累计活动{duration:g}秒。", f"单一连续掘进活动持续{duration:g}秒。"):
        result = verify_report(
            make_draft(pool["date"], wording, [process["fact_id"]]),
            pool,
            plan,
        )
        assert first_row(result)["status"] == "PASS"


def test_quality_event_and_affected_record_counts_are_independent_roles() -> None:
    pool, plan = load_day("2023-10-18")
    mapping = fact_map(pool)
    quality = next(
        fact
        for fact in mapping.values()
        if fact["fact_type"] == "data_quality_limitation"
        and fact["data"]["affected_record_count"]
    )
    result = verify_report(
        make_draft(
            pool["date"],
            (
                f"推进速度不可靠事件{quality['data']['event_count']}起，"
                f"受影响PLC记录{quality['data']['affected_record_count']}条。"
            ),
            [quality["fact_id"]],
        ),
        pool,
        plan,
    )
    row = first_row(result)
    assert row["status"] == "PASS"
    assert any("quality_episode_count" in role for role in row["checked_roles"])
    assert any("affected_plc_record_count" in role for role in row["checked_roles"])


def test_all_ten_template_baselines_are_fully_verified() -> None:
    for date in EXPERIMENT_DATES:
        pool, plan = load_day(date)
        draft = build_template_draft(pool, plan)
        draft["method_group"] = "A"
        verification = verify_report(draft, pool, plan)
        non_pass = [
            claim
            for row in verification["sentences"]
            for claim in row["claims"]
            if claim["status"] not in {"PASS", "N/A"}
        ]
        assert non_pass == [], (date, non_pass)
        assert verification["metrics"]["required_fact_verified_coverage"] == 1.0
        assert verification["metrics"]["deterministic_decision_rate"] == 1.0
