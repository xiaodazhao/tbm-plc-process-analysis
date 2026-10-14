from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..config import PROJECT_ROOT
from ..contracts.results import validate_complete_daily_result
from ..ingestion.io import write_json


SCHEMA_VERSION = "tbm_construction_semantics_v3"
CORE_PARAMETERS = ("advance_speed", "thrust", "torque")
PARAMETER_NAMES = {
    "advance_speed": "推进速度",
    "thrust": "总推力",
    "torque": "刀盘扭矩",
    "rpm": "刀盘转速",
}
STATE_NAMES = {
    "advancing": "推进",
    "free_rotation": "空转",
    "thrusting_no_advance": "加压但未满足推进条件",
    "standby": "待机",
    "cycle_bridged_gap": "短时运行过渡",
    "stopped": "停止",
}
QUALITY_NAMES = {
    "unreliable_advance_speed": "推进速度不可信记录",
    "unreliable_penetration": "贯入度可疑记录",
    "unreliable_canonical_rpm": "刀盘转速可疑记录",
    "interval_speed_distance_mismatch": "推进速度与进尺记录不一致的时段",
    "hard_sampling_gap": "较长采样缺口",
}
FORBIDDEN_READER_TERMS = (
    "有效推进区间",
    "候选区间",
    "候选片段",
    "速度积分",
    "startup",
    "rising",
    "stable",
    "declining",
    "Spearman",
    "重要性分数",
)
FORBIDDEN_CAUSAL_TERMS = ("围岩变硬", "刀具磨损", "卡机", "设备故障", "维修处置")


def load_report_semantics_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_construction_semantics(
    complete_daily_result: dict[str, Any],
    *,
    source_result_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an auditable semantic layer without changing frozen PLC facts."""
    validate_complete_daily_result(complete_daily_result)
    rules = config or _default_config()
    candidates = extract_candidate_facts(complete_daily_result, rules)
    organized = select_and_organize_facts(candidates, rules)
    expressed = express_engineering_facts(organized)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": expressed["date"],
        "source_file": complete_daily_result.get("source_file"),
        "source_frozen_result": str(source_result_path) if source_result_path else None,
        "generation": {
            "method": "deterministic_fact_selection_and_template_expression",
            "llm_invoked": False,
            "segmentation_modified": False,
            "causal_diagnosis_performed": False,
            "rule_config": "report_semantics_config.json",
        },
        "daily_overview": expressed["daily_overview"],
        "main_processes": expressed["main_processes"],
        "construction_responses": expressed["construction_responses"],
        "data_quality": expressed["data_quality"],
        "traceability": expressed["traceability"],
        "selection_metadata": organized["selection_metadata"],
        "parameter_unit_status": rules["parameter_units"],
        "expression_limits": _expression_limits(),
    }
    return payload


def extract_candidate_facts(
    complete_daily_result: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Step A: extract typed facts; do not compose final Chinese here."""
    cycles = complete_daily_result.get("excavation_cycles", [])
    process_candidates = [_extract_process_candidate(cycle, config) for cycle in cycles]
    response_candidates = []
    for cycle in cycles:
        summary = _cycle_summary(cycle)
        for event in cycle.get("coupled_parameter_events", []):
            response_candidates.append(_extract_response_candidate(event, summary, config))
    return {
        "date": str(complete_daily_result["date"]),
        "source_file": complete_daily_result.get("source_file"),
        "overall": complete_daily_result.get("overall", {}),
        "machine_states": complete_daily_result.get("machine_states", {}),
        "process_candidates": process_candidates,
        "response_candidates": response_candidates,
        "quality_candidates": complete_daily_result.get("data_quality_events", []),
    }


def select_and_organize_facts(
    candidates: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Step B: transparent variable-count selection, grouping and deduplication."""
    process_rules = config["process_selection"]
    eligible = []
    process_log = []
    for candidate in candidates["process_candidates"]:
        selected, reasons = _process_is_eligible(candidate, process_rules)
        process_log.append(
            {
                "internal_interval_id": candidate["internal_interval_id"],
                "decision": "eligible_for_session_organization" if selected else "excluded",
                "reasons": reasons,
            }
        )
        if selected:
            eligible.append(candidate)

    selected_sessions = []
    session_log = []
    for index, session in enumerate(_group_process_sessions(eligible, process_rules), start=1):
        session["internal_session_id"] = f"{candidates['date']}-ES{index:02d}"
        selected, reasons = _session_is_reportable(session, process_rules)
        session_log.append(
            {
                "internal_session_id": session["internal_session_id"],
                "internal_interval_ids": session["internal_interval_ids"],
                "decision": "selected" if selected else "excluded",
                "reasons": reasons,
            }
        )
        if selected:
            selected_sessions.append(session)

    _assign_relative_parameter_levels(selected_sessions, process_rules)
    period_comparisons = _build_period_comparisons(eligible, config["period_comparison"])
    selected_responses, response_log = _select_responses(
        candidates["response_candidates"], selected_sessions, config["response_selection"]
    )
    return {
        **candidates,
        "selected_sessions": selected_sessions,
        "period_comparisons": period_comparisons,
        "selected_responses": selected_responses,
        "selection_metadata": {
            "process_candidate_count": len(candidates["process_candidates"]),
            "eligible_process_count": len(eligible),
            "selected_process_session_count": len(selected_sessions),
            "response_candidate_count": len(candidates["response_candidates"]),
            "selected_response_count": len(selected_responses),
            "process_decisions": process_log,
            "session_decisions": session_log,
            "response_decisions": response_log,
            "selection_policy": (
                "Variable-count rule selection using duration, core-parameter validity, "
                "comparable sustained-operation evidence, temporal coverage, response persistence, "
                "multivariate evidence and duplicate removal."
            ),
        },
    }


def express_engineering_facts(organized: dict[str, Any]) -> dict[str, Any]:
    """Steps C/D: create engineering expressions and attach evidence to each fact."""
    traceability: dict[str, dict[str, Any]] = {}
    return {
        "date": organized["date"],
        "daily_overview": _express_overview(organized, traceability),
        "main_processes": _express_processes(organized, traceability),
        "construction_responses": _express_responses(organized, traceability),
        "data_quality": _express_quality(organized, traceability),
        "traceability": traceability,
    }


def write_construction_semantics(
    payload: dict[str, Any], output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date = str(payload["date"])
    json_path = output_dir / f"{date}.json"
    markdown_path = output_dir / f"{date}.md"
    write_json(json_path, payload)
    markdown_path.write_text(render_semantics_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def render_semantics_markdown(payload: dict[str, Any]) -> str:
    sections = [
        ("当日施工概况", payload["daily_overview"]),
        ("掘进过程与运行表现", payload["main_processes"]),
        ("值得关注的运行情况", payload["construction_responses"]),
    ]
    lines = [
        f"# {payload['date']} PLC 施工语义事实",
        "",
        "> 由冻结版 PLC 分析结果通过确定性规则生成，未调用大语言模型。",
        "",
    ]
    for title, rows in sections:
        lines.extend([f"## {title}", ""])
        if rows:
            lines.extend(f"- {row['text']}" for row in rows)
        elif title == "值得关注的运行情况":
            lines.append("- 当日没有满足当前证据规则的重点运行响应。")
        else:
            lines.append("- 当前数据不足以形成可靠描述。")
        lines.append("")
    if payload["data_quality"]:
        lines.extend(["## 数据质量说明", ""])
        lines.extend(f"- {row['text']}" for row in payload["data_quality"])
        lines.append("")
    lines.extend(["内部编号、阶段标签、筛选记录和原始行号仅保存在同名 JSON 中。", ""])
    return "\n".join(lines)


def validate_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    all_rows = [
        row
        for section in ("daily_overview", "main_processes", "construction_responses", "data_quality")
        for row in payload.get(section, [])
    ]
    all_text = "\n".join(str(row.get("text") or "") for row in all_rows)
    add(
        "today_advance_uses_chainage_only",
        any(
            row.get("fact_type") == "daily_advance"
            and row.get("distance_source") == "guidance_shield_head_chainage"
            for row in payload.get("daily_overview", [])
        ),
    )
    add(
        "reader_text_has_no_internal_vocabulary",
        not any(term in all_text for term in FORBIDDEN_READER_TERMS)
        and re.search(r"\bA\d{3}\b", all_text) is None,
    )
    add(
        "reader_text_has_no_unsupported_causal_claims",
        not any(term in all_text for term in FORBIDDEN_CAUSAL_TERMS),
    )
    missing_trace = [
        row.get("fact_id") for row in all_rows if row.get("fact_id") not in payload.get("traceability", {})
    ]
    add("every_reader_fact_is_traceable", not missing_trace, missing_trace)
    duplicates = [text for text, count in Counter(row["text"] for row in all_rows).items() if count > 1]
    add("no_duplicate_reader_facts", not duplicates, duplicates)
    add("legacy_llm_package_is_not_embedded", "llm_input_package" not in payload)
    process_rows = [
        row
        for row in payload.get("main_processes", [])
        if row.get("fact_type") == "representative_construction_period"
    ]
    weak_expression_errors = [
        row["fact_id"]
        for row in process_rows
        if row.get("activity_expression_class") == "advance_related_operation"
        and "推进相关运行活动" not in row.get("text", "")
    ]
    add(
        "weak_progress_evidence_uses_conservative_activity_wording",
        not weak_expression_errors,
        weak_expression_errors,
    )
    intermittent_errors = [
        row["fact_id"]
        for row in process_rows
        if row.get("process_count", 0) > 1
        and (
            row.get("continuity_class") != "intermittent_activities"
            or "不表示连续掘进" not in row.get("text", "")
            or len(row.get("activities", [])) != row.get("process_count")
            or len(row.get("intervening_gaps", [])) != row.get("process_count") - 1
        )
    ]
    add(
        "intermittent_activity_span_is_not_described_as_continuous",
        not intermittent_errors,
        intermittent_errors,
    )
    comparison_basis_errors = [
        row["fact_id"]
        for row in process_rows
        if any(
            parameter not in row.get("parameter_comparison_basis", {})
            for parameter in row.get("relative_parameter_levels", {})
        )
    ]
    add("relative_levels_have_explicit_comparison_basis", not comparison_basis_errors, comparison_basis_errors)
    response_link_errors = [
        row["fact_id"]
        for row in payload.get("construction_responses", [])
        if row.get("related_main_process_fact_id")
        not in {process.get("fact_id") for process in process_rows}
    ]
    add("responses_link_to_main_process_context", not response_link_errors, response_link_errors)
    exaggerated_terms = [term for term in ("持续上升", "持续下降", "持续较强波动") if term in all_text]
    add("trend_and_fluctuation_wording_is_not_exaggerated", not exaggerated_terms, exaggerated_terms)
    return {"date": payload.get("date"), "passed": all(row["passed"] for row in checks), "checks": checks}


def _extract_process_candidate(cycle: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    summary = _cycle_summary(cycle)
    sample_count = max(int(summary.get("sample_count") or 0), 1)
    valid_ratios = {
        parameter: float(summary.get(f"{parameter}_count") or 0) / sample_count
        for parameter in ("advance_speed", "thrust", "torque", "rpm")
    }
    stable_scopes = [
        phase["phase_summary"]
        for phase in cycle.get("phases", [])
        if phase.get("phase_summary", {}).get("phase_label") == "stable"
        and not phase.get("phase_summary", {}).get("dynamic_analysis_scope_only", False)
        and _range_inside(phase.get("phase_summary", {}), summary)
    ]
    stable = max(stable_scopes, key=lambda row: float(row.get("duration_seconds") or 0), default=None)
    statistics = {}
    if stable:
        for parameter in ("advance_speed", "thrust", "torque", "rpm"):
            statistics[parameter] = {
                "median": _number(stable.get(f"{parameter}_p50")),
                "p10": _number(stable.get(f"{parameter}_p10")),
                "p90": _number(stable.get(f"{parameter}_p90")),
                "valid_ratio": _number(stable.get(f"{parameter}_valid_ratio")),
                "unit": config["parameter_units"][parameter]["unit"],
                "unit_status": config["parameter_units"][parameter]["status"],
                "allowed_for_numeric_reporting": False,
                "scope": "comparable_sustained_operation",
            }
    distance_evidence = {
        "guidance_chainage_change_m": _number(summary.get("interval_chainage_change_m")),
        "recorded_cumulative_advance_change_m": _number(
            summary.get("recorded_cumulative_advance_change_m")
        ),
        "valid_speed_integrated_distance_m": _number(
            summary.get("valid_speed_integrated_distance_m")
        ),
        "distance_consistency_status": summary.get("distance_consistency_status"),
    }
    activity_evidence = _classify_activity_evidence(
        distance_evidence,
        config["activity_evidence"],
    )
    return {
        "internal_interval_id": summary.get("interval_id"),
        "start_time": summary.get("start_time"),
        "end_time": summary.get("end_time"),
        "duration_seconds": float(summary.get("duration_seconds") or 0),
        "sample_count": int(summary.get("sample_count") or 0),
        "segmentation_status": summary.get("segmentation_status"),
        "valid_ratios": valid_ratios,
        "stable_scope_seconds": float(stable.get("duration_seconds") or 0) if stable else 0.0,
        "parameter_statistics": statistics,
        "chainage_change_m": distance_evidence["guidance_chainage_change_m"],
        "recorded_cumulative_advance_change_m": distance_evidence[
            "recorded_cumulative_advance_change_m"
        ],
        "valid_speed_integrated_distance_m": distance_evidence[
            "valid_speed_integrated_distance_m"
        ],
        "distance_consistency_status": summary.get("distance_consistency_status"),
        "distance_evidence": distance_evidence,
        "activity_evidence": activity_evidence,
        "source": _cycle_source(summary),
    }


def _classify_activity_evidence(
    distance_evidence: dict[str, Any], rules: dict[str, Any]
) -> dict[str, Any]:
    threshold = float(rules["minimum_confirmed_distance_m"])
    tolerance = float(rules["zero_distance_tolerance_m"])
    chainage = float(distance_evidence.get("guidance_chainage_change_m") or 0.0)
    cumulative = float(distance_evidence.get("recorded_cumulative_advance_change_m") or 0.0)
    integrated = float(distance_evidence.get("valid_speed_integrated_distance_m") or 0.0)
    consistency = distance_evidence.get("distance_consistency_status")
    supporting_sources = [
        name
        for name, value in (
            ("guidance_shield_head_chainage", chainage),
            ("recorded_cumulative_advance", cumulative),
            ("quality_controlled_speed_integration", integrated),
        )
        if value >= threshold
    ]
    chainage_support = chainage > tolerance
    mutually_supported_distance = (
        len(supporting_sources) >= int(rules["minimum_confirmed_distance_source_count"])
        and consistency == "consistent_within_threshold"
    )
    confirmed_excavation = chainage_support or mutually_supported_distance
    any_positive_distance = max(chainage, cumulative, integrated) > tolerance
    if consistency == "distance_mismatch_review_required" and not chainage_support:
        strength = "conflicting"
        confirmed_excavation = False
    elif confirmed_excavation:
        strength = "sufficient"
    elif any_positive_distance:
        strength = "weak"
    else:
        strength = "none"
    return {
        "machine_operation_supported": True,
        "advance_related_response_supported": True,
        "confirmed_excavation_supported": confirmed_excavation,
        "confirmed_local_progress_supported": chainage_support,
        "distance_evidence_strength": strength,
        "supporting_distance_sources": supporting_sources,
        "decision_rule": (
            "guidance chainage increase, or at least two distance sources above the configured "
            "threshold with consistent distance evidence"
        ),
        "thresholds": {
            "minimum_confirmed_distance_m": threshold,
            "minimum_confirmed_distance_source_count": int(
                rules["minimum_confirmed_distance_source_count"]
            ),
            "zero_distance_tolerance_m": tolerance,
        },
    }


def _extract_response_candidate(
    event: dict[str, Any], cycle_summary: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    states = {
        parameter: {
            **state,
            "unit": config["parameter_units"][parameter]["unit"],
            "unit_status": config["parameter_units"][parameter]["status"],
        }
        for parameter, state in event.get("parameter_states", {}).items()
        if parameter in CORE_PARAMETERS
    }
    threshold = float(config["response_selection"]["significant_relative_change"])
    changing = [
        parameter
        for parameter, state in states.items()
        if state.get("trend") in {"increasing", "decreasing"}
        and abs(float(state.get("relative_change") or 0)) >= threshold
    ]
    fluctuating = [
        parameter for parameter, state in states.items() if state.get("fluctuation_level") == "high"
    ]
    directions = {states[parameter].get("trend") for parameter in changing}
    return {
        "internal_event_id": event.get("coupled_event_id"),
        "internal_interval_id": event.get("interval_id"),
        "internal_phase_id": event.get("phase_id"),
        "internal_phase_label": event.get("phase_label"),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "duration_seconds": float(event.get("duration_seconds") or 0),
        "parameter_states": states,
        "changing_parameters": changing,
        "high_fluctuation_parameters": fluctuating,
        "opposing_directions": len(directions) >= 2,
        "inside_formal_process": _range_inside(event, cycle_summary),
        "source": {
            "source_file": event.get("source_file"),
            "source_row_start": event.get("source_row_start"),
            "source_row_end": event.get("source_row_end"),
        },
    }


def _process_is_eligible(
    candidate: dict[str, Any], rules: dict[str, Any]
) -> tuple[bool, list[str]]:
    if candidate["duration_seconds"] < float(rules["minimum_duration_seconds"]):
        return False, ["duration_below_minimum"]
    core_valid = min(candidate["valid_ratios"][parameter] for parameter in CORE_PARAMETERS)
    if core_valid < float(rules["minimum_core_parameter_valid_ratio"]):
        return False, ["insufficient_core_parameter_validity"]
    reasons = []
    if candidate["stable_scope_seconds"] >= float(rules["minimum_stable_scope_seconds"]):
        reasons.append("comparable_sustained_operation_available")
    elif candidate["duration_seconds"] >= float(rules["unsegmented_minimum_duration_seconds"]):
        reasons.append("long_process_without_comparable_sustained_scope")
    else:
        return False, ["no_comparable_scope_and_not_long_enough"]
    if not candidate.get("chainage_change_m"):
        reasons.append("zero_interval_chainage_not_used_as_exclusion")
    if candidate.get("distance_consistency_status") == "distance_mismatch_review_required":
        reasons.append("distance_mismatch_retained_with_quality_limit")
    return True, reasons


def _group_process_sessions(
    candidates: list[dict[str, Any]], rules: dict[str, Any]
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda row: row["start_time"] or "")
    if not ordered:
        return []
    groups = [[ordered[0]]]
    limit = float(rules["session_maximum_gap_seconds"])
    for candidate in ordered[1:]:
        gap = (_parse_time(candidate["start_time"]) - _parse_time(groups[-1][-1]["end_time"])).total_seconds()
        proposed_span = (
            _parse_time(candidate["end_time"]) - _parse_time(groups[-1][0]["start_time"])
        ).total_seconds()
        if (
            gap <= limit
            and proposed_span <= float(rules["session_maximum_span_seconds"])
            and _process_profiles_are_comparable(groups[-1][-1], candidate, rules)
        ):
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    return [_summarize_session(group, rules) for group in groups]


def _process_profiles_are_comparable(
    left: dict[str, Any], right: dict[str, Any], rules: dict[str, Any]
) -> bool:
    ratios = []
    for parameter in CORE_PARAMETERS:
        left_value = left.get("parameter_statistics", {}).get(parameter, {}).get("median")
        right_value = right.get("parameter_statistics", {}).get(parameter, {}).get("median")
        if left_value is None or right_value is None or left_value <= 0 or right_value <= 0:
            continue
        ratios.append(max(left_value, right_value) / min(left_value, right_value))
    minimum_count = int(rules["session_minimum_comparable_parameter_count"])
    if len(ratios) < minimum_count:
        return False
    limit = float(rules["session_maximum_parameter_ratio"])
    return sum(ratio <= limit for ratio in ratios) >= minimum_count


def _summarize_session(
    group: list[dict[str, Any]], rules: dict[str, Any]
) -> dict[str, Any]:
    statistics = {}
    for parameter in CORE_PARAMETERS:
        weighted = [
            (candidate["parameter_statistics"][parameter]["median"], candidate["stable_scope_seconds"])
            for candidate in group
            if candidate["parameter_statistics"].get(parameter, {}).get("median") is not None
        ]
        if weighted:
            statistics[parameter] = {
                "representative_median": _weighted_median(weighted),
                "source_process_count": len(weighted),
                "scope": "weighted_median_of_comparable_sustained_process_medians",
                "unit": None,
                "unit_status": "unconfirmed_plc_point_table",
                "allowed_for_numeric_reporting": False,
            }
    activities = [
        {
            "internal_interval_id": row["internal_interval_id"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "duration_seconds": row["duration_seconds"],
            "activity_expression_class": (
                "confirmed_excavation"
                if row["activity_evidence"]["confirmed_excavation_supported"]
                else "advance_related_operation"
            ),
            "activity_evidence": row["activity_evidence"],
            "distance_evidence": row["distance_evidence"],
            "source": row["source"],
        }
        for row in group
    ]
    gaps = []
    for left, right in zip(group, group[1:]):
        gap_seconds = max(
            (_parse_time(right["start_time"]) - _parse_time(left["end_time"])).total_seconds(),
            0.0,
        )
        gaps.append(
            {
                "start_time": left["end_time"],
                "end_time": right["start_time"],
                "duration_seconds": gap_seconds,
                "interpretation": "intervening_time_not_counted_as_selected_activity",
            }
        )
    elapsed = max(
        (_parse_time(group[-1]["end_time"]) - _parse_time(group[0]["start_time"])).total_seconds(),
        0.0,
    )
    confirmed_count = sum(
        activity["activity_expression_class"] == "confirmed_excavation"
        for activity in activities
    )
    if confirmed_count == len(activities):
        activity_class = "confirmed_excavation"
    elif confirmed_count:
        activity_class = "mixed_excavation_and_advance_related_operation"
    else:
        activity_class = "advance_related_operation"
    return {
        "start_time": group[0]["start_time"],
        "end_time": group[-1]["end_time"],
        "process_count": len(group),
        "continuity_class": "single_continuous_activity" if len(group) == 1 else "intermittent_activities",
        "activity_expression_class": activity_class,
        "elapsed_span_seconds": elapsed,
        "total_process_duration_seconds": sum(row["duration_seconds"] for row in group),
        "total_intervening_gap_seconds": sum(row["duration_seconds"] for row in gaps),
        "total_stable_scope_seconds": sum(row["stable_scope_seconds"] for row in group),
        "activities": activities,
        "intervening_gaps": gaps,
        "merge_basis": {
            "maximum_gap_seconds": float(rules["session_maximum_gap_seconds"]),
            "maximum_span_seconds": float(rules["session_maximum_span_seconds"]),
            "maximum_parameter_ratio": float(rules["session_maximum_parameter_ratio"]),
            "minimum_comparable_parameter_count": int(
                rules["session_minimum_comparable_parameter_count"]
            ),
            "meaning": "report_organization_only_not_continuous_operation",
        },
        "internal_interval_ids": [row["internal_interval_id"] for row in group],
        "sources": [row["source"] for row in group],
        "parameter_statistics": statistics,
        "parameter_levels": {},
        "parameter_comparison_basis": {},
    }


def _session_is_reportable(
    session: dict[str, Any], rules: dict[str, Any]
) -> tuple[bool, list[str]]:
    if session["total_process_duration_seconds"] >= float(rules["session_minimum_total_duration_seconds"]):
        return True, ["sufficient_total_process_duration"]
    if session["process_count"] >= int(rules["session_minimum_process_count"]):
        return True, ["multiple_adjacent_processes_cover_same_work_period"]
    return False, ["isolated_short_process_not_representative"]


def _assign_relative_parameter_levels(
    sessions: list[dict[str, Any]], rules: dict[str, Any]
) -> None:
    for parameter in CORE_PARAMETERS:
        values = [
            session["parameter_statistics"][parameter]["representative_median"]
            for session in sessions
            if parameter in session["parameter_statistics"]
        ]
        minimum_cohort = int(rules["relative_level_minimum_cohort_size"])
        minimum_spread = float(rules["relative_level_minimum_spread_ratio"])
        if len(values) < minimum_cohort or min(values) <= 0 or max(values) / min(values) < minimum_spread:
            continue
        low = _percentile(values, float(rules["relative_level_lower_quantile"]))
        high = _percentile(values, float(rules["relative_level_upper_quantile"]))
        for session in sessions:
            statistic = session["parameter_statistics"].get(parameter)
            if not statistic:
                continue
            value = statistic["representative_median"]
            classification = (
                "lower" if value <= low else "higher" if value >= high else "middle"
            )
            session["parameter_levels"][parameter] = classification
            session["parameter_comparison_basis"][parameter] = {
                "comparison_cohort": "selected_sessions_with_comparable_sustained_operation",
                "cohort_size": len(values),
                "statistic": "weighted_median_of_sustained_operation_medians",
                "current_value": value,
                "lower_threshold": low,
                "upper_threshold": high,
                "classification": classification,
                "minimum_required_spread_ratio": minimum_spread,
                "observed_spread_ratio": max(values) / min(values),
                "value_validity": "quality_controlled_comparable_scope",
                "unit": statistic.get("unit"),
                "unit_status": statistic.get("unit_status"),
            }


def _build_period_comparisons(
    candidates: list[dict[str, Any]], rules: dict[str, Any]
) -> list[dict[str, Any]]:
    summaries = []
    for band in rules["bands"]:
        members = [row for row in candidates if row["parameter_statistics"] and _in_hour_band(row, band)]
        if len(members) < int(rules["minimum_process_count_per_band"]):
            continue
        duration = sum(row["stable_scope_seconds"] for row in members)
        if duration < float(rules["minimum_stable_duration_seconds_per_band"]):
            continue
        values = {}
        for parameter in CORE_PARAMETERS:
            weighted = [
                (row["parameter_statistics"][parameter]["median"], row["stable_scope_seconds"])
                for row in members
                if row["parameter_statistics"].get(parameter, {}).get("median") is not None
            ]
            if weighted:
                values[parameter] = _weighted_median(weighted)
        summaries.append(
            {
                "label": band["label"],
                "process_count": len(members),
                "stable_scope_seconds": duration,
                "values": values,
                "internal_interval_ids": [row["internal_interval_id"] for row in members],
                "sources": [row["source"] for row in members],
            }
        )
    comparisons = []
    for left, right in zip(summaries, summaries[1:]):
        differences = {}
        comparison_values = {}
        for parameter in CORE_PARAMETERS:
            left_value = left["values"].get(parameter)
            right_value = right["values"].get(parameter)
            if not left_value or right_value is None:
                continue
            ratio = right_value / left_value
            comparison_values[parameter] = {
                "left_value": left_value,
                "right_value": right_value,
                "right_to_left_ratio": ratio,
                "statistic": "weighted_median_of_comparable_sustained_operation_medians",
                "unit": None,
                "unit_status": "unconfirmed_plc_point_table",
            }
            if ratio >= float(rules["higher_ratio"]):
                differences[parameter] = "higher"
            elif ratio <= float(rules["lower_ratio"]):
                differences[parameter] = "lower"
        if len(differences) >= int(rules["minimum_different_parameter_count"]):
            comparisons.append(
                {
                    "left": left,
                    "right": right,
                    "differences": differences,
                    "comparison_values": comparison_values,
                    "comparison_basis": {
                        "scope": "comparable_sustained_operation_only",
                        "higher_ratio": float(rules["higher_ratio"]),
                        "lower_ratio": float(rules["lower_ratio"]),
                        "minimum_different_parameter_count": int(
                            rules["minimum_different_parameter_count"]
                        ),
                    },
                }
            )
    return comparisons


def _select_responses(
    candidates: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    rules: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    session_by_interval = {
        interval_id: session for session in sessions for interval_id in session["internal_interval_ids"]
    }
    eligible = []
    log = []
    for original in candidates:
        candidate = dict(original)
        reasons = []
        if candidate["internal_interval_id"] not in session_by_interval:
            decision, reasons = "excluded", ["not_in_selected_main_process_session"]
        elif not candidate["inside_formal_process"]:
            decision, reasons = "excluded", ["context_extended_outside_formal_process"]
        elif candidate["internal_phase_label"] in {"startup", "rising", "declining"}:
            decision, reasons = "excluded", ["expected_transition_not_promoted_to_attention_event"]
        elif candidate["duration_seconds"] < float(rules["minimum_duration_seconds"]):
            decision, reasons = "excluded", ["duration_below_attention_minimum"]
        elif not _response_is_notable(candidate, rules):
            decision, reasons = "excluded", ["ordinary_change_or_insufficient_multivariate_evidence"]
        else:
            decision, reasons = "eligible", ["persistent_nontransition_multivariate_response"]
            session = session_by_interval[candidate["internal_interval_id"]]
            candidate["internal_session_id"] = session["internal_session_id"]
            candidate["session_activity_expression_class"] = session[
                "activity_expression_class"
            ]
            candidate["selection_score"] = _response_score(candidate)
            eligible.append(candidate)
        log.append(
            {"internal_event_id": candidate["internal_event_id"], "decision": decision, "reasons": reasons}
        )

    selected = []
    for candidate in sorted(eligible, key=lambda row: row["selection_score"], reverse=True):
        duplicate = next(
            (
                row
                for row in selected
                if row["internal_session_id"] == candidate["internal_session_id"]
                and _same_response(candidate, row, float(rules["duplicate_overlap_ratio"]))
            ),
            None,
        )
        if duplicate:
            record = next(row for row in log if row["internal_event_id"] == candidate["internal_event_id"])
            record.update(
                {
                    "decision": "merged_as_duplicate",
                    "reasons": [f"same_phenomenon_as_{duplicate['internal_event_id']}"],
                }
            )
            continue
        selected.append(candidate)
    selected.sort(key=lambda row: row["start_time"] or "")
    return selected, log


def _response_is_notable(candidate: dict[str, Any], rules: dict[str, Any]) -> bool:
    changing = len(candidate["changing_parameters"])
    fluctuating = len(candidate["high_fluctuation_parameters"])
    duration = candidate["duration_seconds"]
    if changing >= int(rules["strong_changing_parameter_count"]):
        return True
    if candidate["opposing_directions"] and duration >= float(rules["opposing_change_minimum_duration_seconds"]):
        return True
    if fluctuating >= 2 and duration >= float(rules["sustained_fluctuation_minimum_duration_seconds"]):
        return True
    return fluctuating >= int(rules["strong_fluctuation_parameter_count"]) and duration >= float(
        rules["strong_fluctuation_minimum_duration_seconds"]
    )


def _response_score(candidate: dict[str, Any]) -> float:
    return (
        len(candidate["changing_parameters"])
        + 0.5 * len(candidate["high_fluctuation_parameters"])
        + min(candidate["duration_seconds"] / 600.0, 3.0)
        + (1.0 if candidate["opposing_directions"] else 0.0)
    )


def _same_response(left: dict[str, Any], right: dict[str, Any], threshold: float) -> bool:
    overlap = _time_overlap_seconds(left, right)
    shorter = min(left["duration_seconds"], right["duration_seconds"])
    if shorter <= 0 or overlap / shorter < threshold:
        return False
    left_signature = {
        (parameter, left["parameter_states"][parameter].get("trend"))
        for parameter in left["changing_parameters"]
    } | {(parameter, "high_fluctuation") for parameter in left["high_fluctuation_parameters"]}
    right_signature = {
        (parameter, right["parameter_states"][parameter].get("trend"))
        for parameter in right["changing_parameters"]
    } | {(parameter, "high_fluctuation") for parameter in right["high_fluctuation_parameters"]}
    union = left_signature | right_signature
    return bool(union) and len(left_signature & right_signature) / len(union) >= 0.5


def _express_overview(
    organized: dict[str, Any], traceability: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    overall = organized["overall"]
    source_file = organized.get("source_file")
    rows: list[dict[str, Any]] = []

    start = overall.get("timestamp_start")
    end = overall.get("timestamp_end")
    row_count = int(overall.get("row_count") or 0)
    fact_id = f"{organized['date']}-O01"
    rows.append(
        {
            "fact_id": fact_id,
            "fact_type": "data_coverage",
            "start_time": start,
            "end_time": end,
            "sample_count": row_count,
            "text": f"当日 PLC 数据覆盖 {_clock(start)}—{_clock(end)}，共记录 {row_count} 条数据。",
        }
    )
    traceability[fact_id] = {
        "source_file": source_file,
        "field_paths": ["overall.timestamp_start", "overall.timestamp_end", "overall.row_count"],
        "scope": "daily_observed_records",
    }

    chainage_start = _number(overall.get("chainage_start"))
    chainage_end = _number(overall.get("chainage_end"))
    advance = _number(overall.get("chainage_change_m"))
    if advance is not None:
        fact_id = f"{organized['date']}-O02"
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "daily_advance",
                "distance_source": "guidance_shield_head_chainage",
                "chainage_start": chainage_start,
                "chainage_end": chainage_end,
                "distance_m": advance,
                "text": (
                    f"按导向盾首里程计算，今日进尺为 {_format_distance(advance)}"
                    f"（{_format_chainage(chainage_start)} 至 {_format_chainage(chainage_end)}）。"
                ),
            }
        )
        traceability[fact_id] = {
            "source_file": source_file,
            "field_paths": [
                "overall.chainage_start",
                "overall.chainage_end",
                "overall.chainage_change_m",
            ],
            "scope": "daily_guidance_shield_head_chainage",
            "quality_limit": "No speed integration or cumulative counter is used as today's advance.",
        }
    else:
        fact_id = f"{organized['date']}-O02"
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "daily_advance",
                "distance_source": "guidance_shield_head_chainage",
                "availability": "insufficient_records",
                "chainage_start": chainage_start,
                "chainage_end": chainage_end,
                "distance_m": None,
                "text": "当日记录不足，无法根据导向盾首里程首末变化可靠计算今日进尺。",
            }
        )
        traceability[fact_id] = {
            "source_file": source_file,
            "field_paths": [
                "overall.chainage_start",
                "overall.chainage_end",
                "overall.chainage_change_m",
                "overall.row_count",
            ],
            "scope": "daily_guidance_shield_head_chainage",
            "quality_limit": "Insufficient records; no alternative distance field is substituted.",
        }

    durations = organized.get("machine_states", {}).get("duration_seconds", {})
    duration_items = [
        f"{STATE_NAMES.get(state, state)} {_format_duration(seconds)}"
        for state, seconds in durations.items()
        if float(seconds or 0) > 0
    ]
    if duration_items:
        fact_id = f"{organized['date']}-O03"
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "machine_state_duration",
                "duration_seconds": {key: float(value) for key, value in durations.items()},
                "text": f"按冻结状态规则，有数据支持的时长为：{'，'.join(duration_items)}。",
            }
        )
        traceability[fact_id] = {
            "source_file": source_file,
            "field_paths": ["machine_states.duration_seconds"],
            "scope": "observed_capped_sample_durations",
            "quality_limit": "Hard sampling gaps are not inferred as stopped time.",
        }

    coverage = overall.get("data_coverage", {})
    gap_count = int(coverage.get("hard_sampling_gap_count") or 0)
    if gap_count:
        fact_id = f"{organized['date']}-O04"
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "coverage_limitation",
                "hard_sampling_gap_count": gap_count,
                "text": f"当日存在 {gap_count} 处较长采样缺口，缺口时段未计入运行或停止时长。",
            }
        )
        traceability[fact_id] = {
            "source_file": source_file,
            "field_paths": ["overall.data_coverage.hard_sampling_gap_count"],
            "scope": "daily_data_coverage",
        }
    return rows


def _express_processes(
    organized: dict[str, Any], traceability: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, session in enumerate(organized["selected_sessions"], start=1):
        fact_id = f"{organized['date']}-P{index:02d}"
        session["reader_fact_id"] = fact_id
        activity_class = session["activity_expression_class"]
        if session["process_count"] == 1:
            if activity_class == "confirmed_excavation":
                lead = (
                    f"{_clock(session['start_time'])}—{_clock(session['end_time'])}，设备开展了一段掘进作业，"
                    f"实际纳入统计的作业时长约 {_format_duration(session['total_process_duration_seconds'])}"
                )
            else:
                lead = (
                    f"{_clock(session['start_time'])}—{_clock(session['end_time'])}，设备出现推进相关运行活动，"
                    f"活动时长约 {_format_duration(session['total_process_duration_seconds'])}；"
                    "现有局部进尺证据不足以确认明显进尺"
                )
        else:
            if activity_class == "confirmed_excavation":
                activity_text = "设备先后开展多段掘进作业"
            elif activity_class == "mixed_excavation_and_advance_related_operation":
                activity_text = "设备先后开展掘进作业，并伴有局部进尺证据较弱的推进相关运行活动"
            else:
                activity_text = "设备先后出现多段推进相关运行活动，现有局部进尺证据整体较弱"
            lead = (
                f"{_clock(session['start_time'])}—{_clock(session['end_time'])}期间，{activity_text}，"
                f"实际纳入统计的累计活动时长约 {_format_duration(session['total_process_duration_seconds'])}；"
                "各次活动之间存在间隔，该时间跨度不表示连续掘进"
            )
        profile = _parameter_level_text(session["parameter_levels"])
        text = f"{lead}{'；按可比持续运行片段统计，' + profile if profile else ''}。"
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "representative_construction_period",
                "start_time": session["start_time"],
                "end_time": session["end_time"],
                "process_count": session["process_count"],
                "continuity_class": session["continuity_class"],
                "activity_expression_class": activity_class,
                "elapsed_span_seconds": session["elapsed_span_seconds"],
                "total_process_duration_seconds": session["total_process_duration_seconds"],
                "total_intervening_gap_seconds": session["total_intervening_gap_seconds"],
                "comparable_scope_seconds": session["total_stable_scope_seconds"],
                "activities": session["activities"],
                "intervening_gaps": session["intervening_gaps"],
                "merge_basis": session["merge_basis"],
                "relative_parameter_levels": session["parameter_levels"],
                "parameter_statistics": session["parameter_statistics"],
                "parameter_comparison_basis": session["parameter_comparison_basis"],
                "text": text,
            }
        )
        traceability[fact_id] = {
            "internal_session_id": session["internal_session_id"],
            "internal_interval_ids": session["internal_interval_ids"],
            "sources": session["sources"],
            "scope": "selected_representative_construction_period",
            "activity_evidence": [activity["activity_evidence"] for activity in session["activities"]],
            "distance_evidence": [activity["distance_evidence"] for activity in session["activities"]],
            "merge_basis": session["merge_basis"],
            "intervening_gaps": session["intervening_gaps"],
            "parameter_comparison_basis": session["parameter_comparison_basis"],
            "quality_limits": [
                "Process grouping is for report organization and does not change frozen segmentation.",
                "Relative parameter levels are within-day comparisons only.",
                "Raw parameter values are withheld from reader text while units are unconfirmed.",
            ],
        }

    offset = len(rows)
    for index, comparison in enumerate(organized["period_comparisons"], start=1):
        fact_id = f"{organized['date']}-P{offset + index:02d}"
        higher = [PARAMETER_NAMES[key] for key, value in comparison["differences"].items() if value == "higher"]
        lower = [PARAMETER_NAMES[key] for key, value in comparison["differences"].items() if value == "lower"]
        clauses = []
        if higher:
            clauses.append(f"{_join_chinese(higher)}整体较高")
        if lower:
            clauses.append(f"{_join_chinese(lower)}整体较低")
        text = (
            f"与 {comparison['left']['label']} 相比，{comparison['right']['label']} 的可比持续运行片段中，"
            f"{'，'.join(clauses)}。该比较仅使用统计口径一致的持续运行片段。"
        )
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "within_day_parameter_comparison",
                "left_period": comparison["left"]["label"],
                "right_period": comparison["right"]["label"],
                "relative_differences": comparison["differences"],
                "comparison_values": comparison["comparison_values"],
                "comparison_basis": comparison["comparison_basis"],
                "text": text,
            }
        )
        traceability[fact_id] = {
            "left_internal_interval_ids": comparison["left"]["internal_interval_ids"],
            "right_internal_interval_ids": comparison["right"]["internal_interval_ids"],
            "left_sources": comparison["left"]["sources"],
            "right_sources": comparison["right"]["sources"],
            "scope": "weighted_comparison_of_comparable_sustained_operation",
            "comparison_values": comparison["comparison_values"],
            "comparison_basis": comparison["comparison_basis"],
            "quality_limits": [
                "Comparison is descriptive and does not imply causation.",
                "Absolute values are not published while engineering units are unconfirmed.",
            ],
        }
    return rows


def _express_responses(
    organized: dict[str, Any], traceability: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for index, response in enumerate(organized["selected_responses"], start=1):
        fact_id = f"{organized['date']}-R{index:02d}"
        session = next(
            item
            for item in organized["selected_sessions"]
            if item["internal_session_id"] == response["internal_session_id"]
        )
        related_fact_id = session.get("reader_fact_id")
        description = _response_description(response)
        activity_context = (
            "掘进作业中"
            if response["session_activity_expression_class"] == "confirmed_excavation"
            else "推进相关运行活动中"
        )
        text = (
            f"{_clock(response['start_time'])}—{_clock(response['end_time'])}，{activity_context}，"
            f"{description}；该变化时段约 {_format_duration(response['duration_seconds'])}。"
        )
        states = {}
        for parameter, state in response["parameter_states"].items():
            states[parameter] = {
                "trend": state.get("trend"),
                "fluctuation_level": state.get("fluctuation_level"),
                "representative_value": _number(state.get("median")),
                "initial_representative_value": _number(state.get("initial_median")),
                "final_representative_value": _number(state.get("final_median")),
                "absolute_change": _number(state.get("absolute_change")),
                "slope_per_minute": _number(state.get("slope_per_minute")),
                "relative_change_internal_only": _number(state.get("relative_change")),
                "sample_count": int(state.get("sample_count") or 0),
                "trend_interpretation": "overall_statistical_trend_not_monotonic_at_every_sample",
                "fluctuation_interpretation": "descriptor_level_not_fault_diagnosis",
                "unit": state.get("unit"),
                "unit_status": state.get("unit_status"),
                "allowed_for_numeric_reporting": False,
            }
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "observed_construction_response",
                "start_time": response["start_time"],
                "end_time": response["end_time"],
                "duration_seconds": response["duration_seconds"],
                "related_main_process_fact_id": related_fact_id,
                "parameter_states": states,
                "text": text,
            }
        )
        traceability[fact_id] = {
            "internal_event_id": response["internal_event_id"],
            "internal_session_id": response["internal_session_id"],
            "internal_interval_id": response["internal_interval_id"],
            "internal_phase_id": response["internal_phase_id"],
            "internal_phase_label": response["internal_phase_label"],
            "related_main_process_fact_id": related_fact_id,
            **response["source"],
            "scope": "persistent_multivariate_response_within_selected_process",
            "quality_limits": [
                "Relative change is an internal normalized score and must not be written as a percentage.",
                "Observed response is not a geological or equipment-fault diagnosis.",
                "RPM is withheld from the reader expression while mapping and unit remain provisional.",
            ],
        }
    return rows


def _express_quality(
    organized: dict[str, Any], traceability: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in organized["quality_candidates"]:
        grouped.setdefault(str(event.get("subtype") or "other"), []).append(event)
    rows = []
    for index, (subtype, events) in enumerate(sorted(grouped.items()), start=1):
        fact_id = f"{organized['date']}-Q{index:02d}"
        name = QUALITY_NAMES.get(subtype, subtype)
        if subtype == "unreliable_advance_speed":
            action = "这些记录未用于正常推进速度统计，也未据此补造真实速度"
        elif subtype == "unreliable_penetration":
            action = "这些记录未作为可靠贯入度证据使用"
        elif subtype == "unreliable_canonical_rpm":
            action = "刀盘转速仅保留作待核验信息，未写入面向读者的参数结论"
        elif subtype == "hard_sampling_gap":
            action = "缺口时段未推定为运行或停止时长"
        else:
            action = "相关结论保留相应数据限制"
        text = f"当日识别到 {len(events)} 处{name}；{action}。"
        rows.append(
            {
                "fact_id": fact_id,
                "fact_type": "data_quality_limitation",
                "subtype": subtype,
                "event_count": len(events),
                "text": text,
            }
        )
        traceability[fact_id] = {
            "events": [
                {
                    "event_id": event.get("event_id"),
                    "start_time": event.get("start_time"),
                    "end_time": event.get("end_time"),
                    "source_file": event.get("source_file"),
                    "source_row_start": event.get("source_row_start"),
                    "source_row_end": event.get("source_row_end"),
                }
                for event in events
            ],
            "scope": "data_quality_only_not_construction_response",
        }
    return rows


def _parameter_level_text(levels: dict[str, str]) -> str:
    parts = []
    for level, suffix in (("higher", "相对较高"), ("lower", "相对较低")):
        names = [PARAMETER_NAMES[key] for key in CORE_PARAMETERS if levels.get(key) == level]
        if names:
            parts.append(f"{_join_chinese(names)}{suffix}")
    return "，".join(parts)


def _response_description(response: dict[str, Any]) -> str:
    clauses = []
    for trend, phrase in (("increasing", "整体呈上升趋势"), ("decreasing", "整体呈下降趋势")):
        names = [
            PARAMETER_NAMES[key]
            for key in CORE_PARAMETERS
            if key in response["changing_parameters"]
            and response["parameter_states"][key].get("trend") == trend
        ]
        if names:
            clauses.append(f"{_join_chinese(names)}{phrase}")
    fluctuating = [PARAMETER_NAMES[key] for key in CORE_PARAMETERS if key in response["high_fluctuation_parameters"]]
    if fluctuating:
        clauses.append(f"{_join_chinese(fluctuating)}的波动程度较高")
    return "，".join(clauses) if clauses else "出现有持续时间证据的多参数变化"


def _expression_limits() -> list[str]:
    return [
        "Parameter engineering units remain unconfirmed without the PLC point table.",
        "Canonical cutterhead RPM uses a provisional mapping and is not published as a reader-facing value.",
        "Relative change values are internal trend evidence, not percentages.",
        "Observed parameter responses do not support automatic geology, cutter-wear or fault diagnoses.",
        "Representative process descriptions are selected coverage, not an exhaustive list of every internal cycle.",
    ]


def _default_config() -> dict[str, Any]:
    return load_report_semantics_config(PROJECT_ROOT / "report_semantics_config.json")


def _cycle_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    return cycle.get("cycle_summary") or cycle.get("interval_summary") or {}


def _cycle_source(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file": summary.get("source_file"),
        "source_row_start": summary.get("source_row_start"),
        "source_row_end": summary.get("source_row_end"),
        "start_time": summary.get("start_time"),
        "end_time": summary.get("end_time"),
    }


def _range_inside(inner: dict[str, Any], outer: dict[str, Any]) -> bool:
    if not inner.get("start_time") or not inner.get("end_time"):
        return False
    if not outer.get("start_time") or not outer.get("end_time"):
        return False
    return _parse_time(inner["start_time"]) >= _parse_time(outer["start_time"]) and _parse_time(
        inner["end_time"]
    ) <= _parse_time(outer["end_time"])


def _in_hour_band(candidate: dict[str, Any], band: dict[str, Any]) -> bool:
    midpoint = _parse_time(candidate["start_time"]) + (
        _parse_time(candidate["end_time"]) - _parse_time(candidate["start_time"])
    ) / 2
    hour = midpoint.hour + midpoint.minute / 60 + midpoint.second / 3600
    return float(band["start_hour"]) <= hour < float(band["end_hour"])


def _weighted_median(weighted_values: Iterable[tuple[float, float]]) -> float:
    values = sorted((float(value), float(weight)) for value, weight in weighted_values if weight > 0)
    total = sum(weight for _, weight in values)
    threshold = total / 2
    cumulative = 0.0
    for value, weight in values:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return values[-1][0]


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    portion = position - lower
    return ordered[lower] * (1 - portion) + ordered[upper] * portion


def _time_overlap_seconds(left: dict[str, Any], right: dict[str, Any]) -> float:
    start = max(_parse_time(left["start_time"]), _parse_time(right["start_time"]))
    end = min(_parse_time(left["end_time"]), _parse_time(right["end_time"]))
    return max((end - start).total_seconds(), 0.0)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value))


def _clock(value: Any) -> str:
    if not value:
        return "时间未知"
    return _parse_time(str(value)).strftime("%H:%M")


def _format_duration(seconds: Any) -> str:
    raw_seconds = max(float(seconds or 0), 0.0)
    if 0 < raw_seconds < 30:
        return "不足 1 分钟"
    minutes = max(int(raw_seconds / 60 + 0.5), 0)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _format_chainage(value: float | None) -> str:
    return "里程未知" if value is None else f"导向盾首里程 {value:,.0f} m"


def _format_distance(value: float) -> str:
    return f"{value:,.2f} m"


def _join_chinese(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return "、".join(items)

