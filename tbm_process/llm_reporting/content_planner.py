from __future__ import annotations

from datetime import datetime
from typing import Any

from .fact_adapter import fact_map


PLAN_SCHEMA = "tbm_report_content_plan_v1"


def build_content_plan(
    pool: dict[str, Any], *, max_response_facts: int = 3
) -> dict[str, Any]:
    """Select and order report facts without writing report prose."""
    facts = pool["facts"]
    overview = [fact for fact in facts if fact["source_section"] == "daily_overview"]
    quality = [fact for fact in facts if fact["source_section"] == "data_quality"]
    processes = [
        fact
        for fact in facts
        if fact["fact_type"] == "representative_construction_period"
    ]
    comparisons = [
        fact for fact in facts if fact["fact_type"] == "within_day_parameter_comparison"
    ]
    responses = [
        fact for fact in facts if fact["source_section"] == "construction_responses"
    ]

    processes.sort(key=lambda fact: _time_key(fact["data"].get("start_time")))
    selected_responses, response_decisions = _select_responses(
        responses, maximum=max_response_facts
    )

    overview_required = [
        fact["fact_id"]
        for fact in overview
        if fact["fact_type"] in {
            "data_coverage",
            "daily_advance",
            "machine_state_duration",
            "coverage_limitation",
        }
    ]
    overview_optional = [
        fact["fact_id"]
        for fact in overview
        if fact["fact_id"] not in overview_required
    ]
    quality_ids = [fact["fact_id"] for fact in quality]
    process_ids = [fact["fact_id"] for fact in processes]
    comparison_ids = [fact["fact_id"] for fact in comparisons]
    response_ids = [fact["fact_id"] for fact in selected_responses]

    required = overview_required + quality_ids + process_ids + comparison_ids + response_ids
    plan = {
        "schema_version": PLAN_SCHEMA,
        "date": pool["date"],
        "source_fact_pool_sha256": pool["source"]["input_sha256"],
        "sections": [
            {
                "section_id": "overview",
                "heading": "一、当日施工概况",
                "required_fact_ids": overview_required + quality_ids,
                "optional_fact_ids": overview_optional,
                "order_rule": "coverage_then_advance_then_state_then_parameters_then_quality",
            },
            {
                "section_id": "main_process",
                "heading": "二、主要施工过程",
                "required_fact_ids": process_ids + comparison_ids,
                "optional_fact_ids": [],
                "order_rule": "chronological_processes_then_within_day_comparison",
            },
            {
                "section_id": "responses",
                "heading": "三、值得关注的运行情况",
                "required_fact_ids": response_ids,
                "optional_fact_ids": [],
                "order_rule": "chronological_selected_responses",
            },
        ],
        "required_fact_ids": required,
        "required_fact_count": len(required),
        "response_selection": {
            "candidate_count": len(responses),
            "selected_count": len(selected_responses),
            "maximum": max_response_facts,
            "decisions": response_decisions,
            "note": "Unselected responses remain in the shared fact pool.",
        },
        "planning_limits": [
            "The plan selects facts and order only; it contains no prewritten report prose.",
            "Intermittent processes must retain both elapsed span and cumulative activity duration.",
            "Selected response facts must be narrated chronologically.",
        ],
    }
    validate_content_plan(plan, pool)
    return plan


def validate_content_plan(plan: dict[str, Any], pool: dict[str, Any]) -> None:
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError("Unsupported content plan schema")
    known = set(fact_map(pool))
    planned = [
        fact_id
        for section in plan.get("sections", [])
        for key in ("required_fact_ids", "optional_fact_ids")
        for fact_id in section.get(key, [])
    ]
    missing = sorted(set(planned) - known)
    if missing:
        raise ValueError(f"Content plan references missing facts: {missing}")
    response_section = next(
        section for section in plan["sections"] if section["section_id"] == "responses"
    )
    mapping = fact_map(pool)
    times = [
        mapping[fact_id]["data"].get("start_time")
        for fact_id in response_section["required_fact_ids"]
    ]
    if times != sorted(times):
        raise ValueError("Selected response facts are not chronological")


def _select_responses(
    responses: list[dict[str, Any]], *, maximum: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scored = [(response, _response_score(response)) for response in responses]
    ranked = sorted(
        scored,
        key=lambda item: (-item[1], _time_key(item[0]["data"].get("start_time"))),
    )
    selected: list[dict[str, Any]] = []
    selected_processes: set[str] = set()
    for response, _ in ranked:
        if len(selected) >= maximum:
            break
        process_id = str(response["data"].get("related_main_process_fact_id") or "")
        if process_id and process_id in selected_processes:
            continue
        selected.append(response)
        if process_id:
            selected_processes.add(process_id)
    if len(selected) < maximum:
        for response, _ in ranked:
            if response in selected:
                continue
            selected.append(response)
            if len(selected) >= maximum:
                break
    selected.sort(key=lambda fact: _time_key(fact["data"].get("start_time")))
    selected_ids = {fact["fact_id"] for fact in selected}
    decisions = [
        {
            "fact_id": fact["fact_id"],
            "score": score,
            "decision": "selected" if fact["fact_id"] in selected_ids else "not_selected",
            "reason": _response_reason(fact, score),
        }
        for fact, score in sorted(scored, key=lambda item: _time_key(item[0]["data"].get("start_time")))
    ]
    return selected, decisions


def _response_score(fact: dict[str, Any]) -> float:
    data = fact["data"]
    parameters = data.get("parameters", {})
    trends = {
        value.get("trend")
        for value in parameters.values()
        if value.get("trend") in {"increasing", "decreasing"}
    }
    high_fluctuation = sum(
        value.get("fluctuation_level") == "high" for value in parameters.values()
    )
    duration = float(data.get("duration_seconds") or 0.0)
    return round(
        min(duration / 600.0, 3.0)
        + max(len(parameters) - 1, 0) * 1.25
        + (1.0 if len(trends) > 1 else 0.0)
        + high_fluctuation * 0.5,
        4,
    )


def _response_reason(fact: dict[str, Any], score: float) -> str:
    parameters = fact["data"].get("parameters", {})
    return (
        f"duration={fact['data'].get('duration_seconds')}s; "
        f"parameter_count={len(parameters)}; deterministic_score={score}"
    )


def _time_key(value: Any) -> datetime:
    if not value:
        return datetime.max
    return datetime.fromisoformat(str(value))
