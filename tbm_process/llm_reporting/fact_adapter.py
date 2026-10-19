from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


FACT_POOL_SCHEMA = "tbm_typed_report_fact_pool_v1"

CATEGORY_BY_SECTION = {
    "daily_overview": "direct_numeric_fact",
    "main_processes": "time_process_or_comparison_fact",
    "construction_responses": "observed_response_fact",
    "data_quality": "quality_uncertainty_fact",
}


def build_typed_fact_pool(
    input_path: Path,
    *,
    quantitative_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a compact fact pool without recalculating any PLC result.

    The LLM-facing pool contains report facts and use limits. Full local source-row
    traceability remains in a separate evidence index.
    """
    raw = input_path.read_bytes()
    source = json.loads(raw.decode("utf-8-sig"))
    structured = source.get("structured_facts")
    if not isinstance(structured, dict):
        raise ValueError(f"Missing structured_facts in {input_path}")
    date = str(source.get("date") or "")
    if not date:
        raise ValueError(f"Missing report date in {input_path}")

    traceability: dict[str, Any] = {}
    quantitative_sha = None
    if quantitative_path is not None and quantitative_path.is_file():
        quantitative_raw = quantitative_path.read_bytes()
        quantitative = json.loads(quantitative_raw.decode("utf-8-sig"))
        traceability = copy.deepcopy(quantitative.get("traceability", {}))
        quantitative_sha = hashlib.sha256(quantitative_raw).hexdigest()

    facts: list[dict[str, Any]] = []
    evidence_facts: dict[str, Any] = {}
    for section in (
        "daily_overview",
        "main_processes",
        "construction_responses",
        "data_quality",
    ):
        rows = structured.get(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"structured_facts.{section} must be a list")
        for row in rows:
            fact = _adapt_fact(section, row)
            facts.append(fact)
            fact_id = fact["fact_id"]
            evidence_facts[fact_id] = {
                "source_fact_id": fact_id,
                "source_section": section,
                "source_input": str(input_path.resolve()),
                "source_traceability": copy.deepcopy(traceability.get(fact_id, {})),
            }

    ids = [fact["fact_id"] for fact in facts]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate fact_id values in source package")

    fact_pool = {
        "schema_version": FACT_POOL_SCHEMA,
        "date": date,
        "source": {
            "input_path": str(input_path.resolve()),
            "input_schema_version": source.get("schema_version"),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "quantitative_path": str(quantitative_path.resolve())
            if quantitative_path is not None and quantitative_path.is_file()
            else None,
            "quantitative_sha256": quantitative_sha,
            "semantic_expressions_in_source": bool(source.get("semantic_expressions")),
            "semantic_expressions_in_llm_pool": False,
        },
        "report_mode": source.get("report_mode", "research_preview"),
        "facts": facts,
        "fact_count": len(facts),
        "fact_count_by_category": _count_by(facts, "category"),
        "global_limits": [
            "平台是算法识别的运行过程，不等于现场确认的真实掘进循环。",
            "导向盾首里程首末差是今日进尺的唯一报告口径。",
            "运行状态时长采用原状态分类规则，硬采样缺口不计为停止时间。",
            "总跨度、累计活动时长、状态时长和间隔时长是不同统计口径。",
            "质量事件数与受影响PLC记录数是不同统计量。",
            "参数变化只表示观测响应，不支持地质、故障、刀具或人员原因推断。",
            "工程单位未确认的参数只能作为原始PLC记录值用于研究检查。",
            "瞬时终止边界不是观测到的持续下降过程。",
        ],
    }
    evidence_index = {
        "schema_version": "tbm_report_fact_evidence_index_v1",
        "date": date,
        "input_sha256": fact_pool["source"]["input_sha256"],
        "facts": evidence_facts,
        "note": "This local index is not transmitted to the LLM.",
    }
    validate_fact_pool(fact_pool)
    return fact_pool, evidence_index


def validate_fact_pool(pool: dict[str, Any]) -> None:
    if pool.get("schema_version") != FACT_POOL_SCHEMA:
        raise ValueError("Unsupported typed fact-pool schema")
    facts = pool.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValueError("Typed fact pool must contain facts")
    required = {
        "fact_id",
        "fact_type",
        "category",
        "source_section",
        "data",
        "quality_status",
        "unit_status",
        "verification_rule",
    }
    for fact in facts:
        missing = required - set(fact)
        if missing:
            raise ValueError(f"Fact {fact.get('fact_id')} missing {sorted(missing)}")
    if any("semantic_expressions" in fact for fact in facts):
        raise ValueError("Prewritten semantic expressions leaked into typed facts")


def fact_map(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(fact["fact_id"]): fact for fact in pool["facts"]}


def _adapt_fact(section: str, row: dict[str, Any]) -> dict[str, Any]:
    fact_id = str(row.get("fact_id") or "")
    fact_type = str(row.get("fact_type") or "")
    if not fact_id or not fact_type:
        raise ValueError(f"Fact in {section} is missing fact_id or fact_type")
    data, quality_status, unit_status, verification = _compact_by_type(fact_type, row)
    category = CATEGORY_BY_SECTION[section]
    if fact_type == "within_day_parameter_comparison":
        category = "comparison_fact"
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "category": category,
        "source_section": section,
        "data": data,
        "quality_status": quality_status,
        "unit_status": unit_status,
        "verification_rule": verification,
    }


def _compact_by_type(
    fact_type: str, row: dict[str, Any]
) -> tuple[dict[str, Any], str, Any, dict[str, Any]]:
    if fact_type == "data_coverage":
        return (
            {
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "sample_count": row.get("sample_count"),
            },
            "accepted_observed_coverage",
            {"time": "timestamp", "sample_count": "records"},
            {
                "kind": "data_coverage",
                "numeric_roles": {
                    "sample_count": "sample_count",
                    "start_time": "coverage_start",
                    "end_time": "coverage_end",
                },
            },
        )
    if fact_type == "daily_advance":
        return (
            {
                "distance_source": row.get("distance_source"),
                "chainage_start": row.get("chainage_start"),
                "chainage_end": row.get("chainage_end"),
                "distance_m": row.get("distance_m"),
            },
            "accepted_report_advance_basis",
            {"chainage": "m", "distance": "m"},
            {
                "kind": "daily_advance",
                "numeric_roles": {
                    "distance_m": "daily_advance",
                    "chainage_start": "chainage_start",
                    "chainage_end": "chainage_end",
                },
            },
        )
    if fact_type == "machine_state_duration":
        states = []
        for state, values in row.get("state_statistics", {}).items():
            states.append(
                {
                    "state": state,
                    "display_name": values.get("display_name", state),
                    "duration_seconds": values.get("duration_seconds"),
                    "percentage": values.get("percentage"),
                }
            )
        return (
            {
                "states": states,
                "denominator_seconds": row.get("percentage_denominator", {}).get(
                    "duration_seconds"
                ),
                "denominator_definition": row.get("percentage_denominator", {}).get(
                    "definition"
                ),
                "hard_gaps_excluded": row.get("percentage_denominator", {}).get(
                    "hard_gaps_excluded"
                ),
                "unclassified_duration_seconds": row.get(
                    "unclassified_duration_seconds"
                ),
            },
            "accepted_original_state_classification",
            {"duration": "seconds", "percentage": "%"},
            {
                "kind": "machine_state_duration",
                "numeric_roles": {
                    "states[].duration_seconds": "state_duration",
                    "states[].percentage": "state_percentage",
                    "denominator_seconds": "observed_state_duration_denominator",
                },
            },
        )
    if fact_type == "coverage_limitation":
        return (
            {
                "hard_sampling_gap_count": row.get("hard_sampling_gap_count"),
            },
            "observed_coverage_limitation",
            {"hard_sampling_gap_count": "episodes"},
            {
                "kind": "coverage_limitation",
                "numeric_roles": {
                    "hard_sampling_gap_count": "hard_sampling_gap_episode_count"
                },
            },
        )
    if fact_type == "daily_advancing_parameter_statistics":
        parameters = _compact_parameter_collection(row.get("parameter_statistics", {}))
        return (
            {
                "statistical_scope": {
                    "name": row.get("scope", {}).get("scope_name"),
                    "filter": row.get("scope", {}).get("filter"),
                    "sample_count": row.get("scope", {}).get("scope_sample_count"),
                },
                "parameters": parameters,
            },
            "quality_controlled_declared_scope",
            _parameter_unit_map(parameters),
            {
                "kind": "parameter_distribution",
                "numeric_roles": {
                    "parameters.*.p10": "p10",
                    "parameters.*.median": "median",
                    "parameters.*.p90": "p90",
                    "parameters.*.valid_count": "valid_record_count",
                },
            },
        )
    if fact_type == "representative_construction_period":
        activities = [
            {
                "start_time": activity.get("start_time"),
                "end_time": activity.get("end_time"),
                "duration_seconds": activity.get("duration_seconds"),
                "activity_expression_class": activity.get(
                    "activity_expression_class"
                ),
            }
            for activity in row.get("activities", [])
        ]
        parameters = _compact_parameter_collection(
            row.get("report_parameter_statistics", {})
        )
        return (
            {
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "continuity_class": row.get("continuity_class"),
                "activity_expression_class": row.get("activity_expression_class"),
                "elapsed_span_seconds": row.get("elapsed_span_seconds"),
                "total_activity_duration_seconds": row.get(
                    "total_process_duration_seconds"
                ),
                "total_intervening_gap_seconds": row.get(
                    "total_intervening_gap_seconds"
                ),
                "comparable_scope_seconds": row.get("comparable_scope_seconds"),
                "activities": activities,
                "relative_parameter_levels": copy.deepcopy(
                    row.get("relative_parameter_levels", {})
                ),
                "statistical_scope": {
                    "name": row.get("report_statistics_scope", {}).get("scope_name"),
                    "sample_count": row.get("report_statistics_scope", {}).get(
                        "scope_sample_count"
                    ),
                    "comparable_scope_seconds": row.get(
                        "report_statistics_scope", {}
                    ).get("comparable_scope_seconds"),
                },
                "parameters": parameters,
            },
            "selected_representative_process",
            _parameter_unit_map(parameters),
            {
                "kind": "representative_process",
                "numeric_roles": {
                    "elapsed_span_seconds": "elapsed_timestamp_span",
                    "total_activity_duration_seconds": "cumulative_activity_duration",
                    "total_intervening_gap_seconds": "intervening_gap_duration",
                    "activities[].duration_seconds": "individual_activity_duration",
                },
                "continuity_rule": row.get("continuity_class"),
            },
        )
    if fact_type == "within_day_parameter_comparison":
        values: dict[str, Any] = {}
        for parameter, item in row.get("comparison_values", {}).items():
            values[parameter] = {
                "display_name": _parameter_display_name(parameter),
                "left_value": item.get("left_value"),
                "right_value": item.get("right_value"),
                "difference": item.get("raw_value_difference"),
                "percent_difference": item.get(
                    "actual_percent_difference_from_raw_records"
                ),
                "unit_status": item.get("unit_status"),
            }
        return (
            {
                "left_period": row.get("left_period"),
                "right_period": row.get("right_period"),
                "relative_differences": copy.deepcopy(
                    row.get("relative_differences", {})
                ),
                "parameters": values,
                "comparison_basis": row.get("comparison_basis"),
            },
            "comparable_sustained_operation_scopes",
            _parameter_unit_map(values),
            {
                "kind": "within_day_comparison",
                "numeric_roles": {
                    "parameters.*.left_value": "left_period_value",
                    "parameters.*.right_value": "right_period_value",
                    "parameters.*.difference": "raw_value_difference",
                    "parameters.*.percent_difference": "raw_value_percent_difference",
                },
            },
        )
    if fact_type == "observed_construction_response":
        states: dict[str, Any] = {}
        for parameter, item in row.get("parameter_states", {}).items():
            states[parameter] = {
                "display_name": item.get(
                    "display_name", _parameter_display_name(parameter)
                ),
                "trend": item.get("trend"),
                "fluctuation_level": item.get("fluctuation_level"),
                "initial_value": item.get("initial_representative_value"),
                "final_value": item.get("final_representative_value"),
                "absolute_change": item.get("absolute_change"),
                "percent_change": item.get(
                    "actual_percent_change_from_raw_records"
                ),
                "valid_count": item.get("valid_count"),
                "unit_status": item.get("unit_status"),
            }
        return (
            {
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "duration_seconds": row.get("duration_seconds"),
                "related_main_process_fact_id": row.get(
                    "related_main_process_fact_id"
                ),
                "parameters": states,
            },
            "observed_response_not_diagnosis",
            _parameter_unit_map(states),
            {
                "kind": "construction_response",
                "numeric_roles": {
                    "duration_seconds": "response_duration",
                    "parameters.*.initial_value": "initial_raw_median",
                    "parameters.*.final_value": "final_raw_median",
                    "parameters.*.absolute_change": "raw_value_change",
                    "parameters.*.percent_change": "raw_value_percent_change",
                },
            },
        )
    if fact_type == "data_quality_limitation":
        return (
            {
                "subtype": row.get("subtype"),
                "event_count": row.get("event_count"),
                "event_count_definition": row.get("event_count_definition"),
                "affected_record_count": row.get("affected_record_count"),
                "episode_span_record_count": row.get("episode_span_record_count"),
            },
            "report_as_data_quality_limitation_only",
            None,
            {
                "kind": "data_quality_count",
                "numeric_roles": {
                    "event_count": "quality_episode_count",
                    "affected_record_count": "affected_plc_record_count",
                    "episode_span_record_count": "episode_span_record_count",
                },
            },
        )
    raise ValueError(f"Unsupported fact type: {fact_type}")


def _compact_parameter_collection(values: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for parameter, item in values.items():
        compact[parameter] = {
            key: value
            for key, value in {
                "display_name": item.get(
                    "display_name", _parameter_display_name(parameter)
                ),
                "valid_count": item.get("valid_count"),
                "valid_ratio": item.get("valid_ratio"),
                "p10": item.get("p10"),
                "median": item.get("median"),
                "p90": item.get("p90"),
                "unit_status": item.get("unit_status"),
            }.items()
            if value is not None
        }
    return compact


def _parameter_unit_map(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get("unit_status")
        for key, value in parameters.items()
        if isinstance(value, dict)
    }


def _parameter_display_name(parameter: str) -> str:
    return {
        "advance_speed": "推进速度",
        "thrust": "总推力",
        "torque": "刀盘扭矩",
        "rpm": "刀盘转速",
    }.get(parameter, parameter)


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        counts[value] = counts.get(value, 0) + 1
    return counts
