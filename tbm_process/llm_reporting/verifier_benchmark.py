from __future__ import annotations

from copy import deepcopy
from typing import Any

from .draft import DRAFT_SCHEMA, render_fact_sentence
from .fact_adapter import fact_map
from .verifier import verify_report


def run_verifier_benchmark(
    pool: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Run fixed positive and error-injection cases on one real fact package."""
    mapping = fact_map(pool)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in mapping.values():
        by_type.setdefault(str(fact["fact_type"]), []).append(fact)

    coverage = by_type["data_coverage"][0]
    advance = by_type["daily_advance"][0]
    states = by_type["machine_state_duration"][0]
    daily_parameters = by_type["daily_advancing_parameter_statistics"][0]
    process = next(
        fact
        for fact in by_type["representative_construction_period"]
        if float(fact["data"].get("elapsed_span_seconds") or 0)
        != float(fact["data"].get("total_activity_duration_seconds") or 0)
    )
    quality = next(
        fact
        for fact in by_type["data_quality_limitation"]
        if fact["data"].get("event_count") != fact["data"].get("affected_record_count")
    )
    state = states["data"]["states"][0]
    parameter_key, parameter = next(
        (key, value)
        for key, value in daily_parameters["data"]["parameters"].items()
        if value.get("median") is not None
    )
    distance = float(advance["data"]["distance_m"])
    sample_count = int(coverage["data"]["sample_count"])
    elapsed = float(process["data"]["elapsed_span_seconds"])
    activity = float(process["data"]["total_activity_duration_seconds"])
    gap = float(process["data"].get("total_intervening_gap_seconds") or 0)
    event_count = int(quality["data"].get("event_count") or 0)
    affected = int(quality["data"].get("affected_record_count") or 0)
    state_duration = float(state["duration_seconds"])
    median = float(parameter["median"])

    positive_cases = [
        _case(
            "positive_chinese_meter",
            "overview",
            f"今日进尺{distance:g}米。",
            [advance["fact_id"]],
            "PASS",
        ),
        _case(
            "positive_compound_two_facts",
            "overview",
            f"今日进尺{distance:g}米，PLC数据共记录{sample_count}条数据。",
            [advance["fact_id"], coverage["fact_id"]],
            "PASS",
        ),
        _case(
            "positive_distinct_process_durations",
            "main_process",
            (
                f"时段总跨度约{elapsed:g}秒，累计活动时长约{activity:g}秒，"
                f"中间间隔合计约{gap:g}秒。"
            ),
            [process["fact_id"]],
            "PASS",
        ),
        _case(
            "positive_quality_roles",
            "overview",
            (
                f"{_quality_name(quality)}质量事件{event_count}处，"
                f"涉及{affected}条PLC记录。"
            ),
            [quality["fact_id"]],
            "PASS",
        ),
        _case(
            "positive_template_fact",
            "overview",
            render_fact_sentence(advance),
            [advance["fact_id"]],
            "PASS",
        ),
    ]
    injected_cases = [
        _case(
            "injected_wrong_chinese_meter_value",
            "overview",
            f"今日进尺{distance + 7:g}米。",
            [advance["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_extra_unsupported_assertion",
            "overview",
            f"今日进尺{distance:g}米，设备运行完全正常。",
            [advance["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_extra_geology_claim",
            "overview",
            f"今日进尺{distance:g}米，说明围岩稳定。",
            [advance["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_swapped_process_duration_roles",
            "main_process",
            f"时段总跨度约{activity:g}秒，累计活动时长约{elapsed:g}秒。",
            [process["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_machine_state_duration",
            "overview",
            f"{state['display_name']}状态累计{state_duration + 600:g}秒。",
            [states["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_swapped_quality_count_roles",
            "overview",
            (
                f"{_quality_name(quality)}质量事件{affected}处，"
                f"涉及{event_count}条PLC记录。"
            ),
            [quality["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_wrong_parameter_median",
            "overview",
            (
                f"{parameter['display_name']}中位数{median + 123.45:.2f}，"
                "为原始PLC记录值，单位待核实。"
            ),
            [daily_parameters["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_existing_but_wrong_fact_id",
            "overview",
            f"今日进尺{distance:g}米。",
            [coverage["fact_id"]],
            "NOT_PASS",
        ),
        _case(
            "injected_untyped_parameter_number",
            "overview",
            f"{parameter['display_name']}达到{median:g}。",
            [daily_parameters["fact_id"]],
            "NOT_PASS",
        ),
    ]

    outcomes = [
        _run_case(pool, plan, value)
        for value in positive_cases + injected_cases
    ]
    positive = [row for row in outcomes if row["expected"] == "PASS"]
    injected = [row for row in outcomes if row["expected"] == "NOT_PASS"]
    false_rejections = [row for row in positive if row["observed"] != "PASS"]
    missed_injections = [row for row in injected if row["observed"] == "PASS"]
    return {
        "schema_version": "tbm_verifier_v2_injection_benchmark_v1",
        "date": pool["date"],
        "case_count": len(outcomes),
        "positive_case_count": len(positive),
        "positive_pass_count": len(positive) - len(false_rejections),
        "false_rejection_count": len(false_rejections),
        "false_rejection_rate": _ratio(len(false_rejections), len(positive)),
        "injected_error_case_count": len(injected),
        "detected_injection_count": len(injected) - len(missed_injections),
        "missed_injection_count": len(missed_injections),
        "injected_error_miss_rate": _ratio(len(missed_injections), len(injected)),
        "explicit_fail_detection_count": sum(row["observed"] == "FAIL" for row in injected),
        "conservative_review_detection_count": sum(row["observed"] == "REVIEW" for row in injected),
        "cases": outcomes,
        "scope_warning": (
            "This benchmark measures only the listed deterministic regression cases. "
            "It is not a general estimate of verifier accuracy or engineering truth."
        ),
    }


def _case(
    case_id: str,
    section_id: str,
    text: str,
    fact_ids: list[str],
    expected: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "section_id": section_id,
        "text": text,
        "supporting_fact_ids": fact_ids,
        "expected": expected,
    }


def _run_case(
    pool: dict[str, Any], plan: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    draft = _single_claim_draft(pool["date"], case)
    verification = verify_report(draft, pool, plan)
    row = next(
        row
        for row in verification["sentences"]
        if row["section_id"] == case["section_id"]
    )
    return {
        **deepcopy(case),
        "observed": row["status"],
        "error_types": row["error_types"],
        "checked_roles": row["checked_roles"],
    }


def _single_claim_draft(date: str, case: dict[str, Any]) -> dict[str, Any]:
    fallback = [{"text": "现有信息不足，不能确认。", "supporting_fact_ids": []}]
    sections = []
    specs = (
        ("overview", "一、当日施工概况"),
        ("main_process", "二、主要施工过程"),
        ("responses", "三、值得关注的运行情况"),
    )
    for section_id, heading in specs:
        sentences = fallback
        if section_id == case["section_id"]:
            sentences = [{
                "text": case["text"],
                "supporting_fact_ids": case["supporting_fact_ids"],
            }]
        sections.append({"section_id": section_id, "heading": heading, "sentences": sentences})
    return {
        "schema_version": DRAFT_SCHEMA,
        "date": date,
        "method_group": "verifier_v2_benchmark",
        "sections": sections,
    }


def _quality_name(fact: dict[str, Any]) -> str:
    return {
        "unreliable_advance_speed": "推进速度不可信",
        "interval_speed_distance_mismatch": "区间速度与距离不匹配",
        "unreliable_canonical_rpm": "刀盘转速可疑",
        "unreliable_penetration": "贯入度可疑",
        "hard_sampling_gap": "硬采样缺口",
        "sampling_gap": "采样缺口",
    }.get(str(fact["data"].get("subtype")), "数据质量")


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
