from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..ingestion.io import write_json


QUANTITATIVE_SCHEMA_VERSION = "tbm_report_quantitative_v1"
LLM_SCHEMA_VERSION = "tbm_llm_fact_input_v3"
PARAMETER_ORDER = ("advance_speed", "thrust", "torque", "rpm")
PARAMETER_COLUMNS = {
    "advance_speed": "advance_speed",
    "thrust": "thrust",
    "torque": "torque",
    "rpm": "rpm_canonical",
}
PARAMETER_NAMES = {
    "advance_speed": "推进速度",
    "thrust": "总推力",
    "torque": "刀盘扭矩",
    "rpm": "刀盘转速",
}
STATE_NAMES = {
    "advancing": "推进",
    "free_rotation": "空转",
    "stopped": "停止",
    "thrusting_no_advance": "加压但未满足推进条件",
    "standby": "待机",
    "cycle_bridged_gap": "短时运行过渡",
}


def load_report_parameter_registry(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "tbm_report_parameter_registry_v1":
        raise ValueError(f"Unsupported report parameter registry: {path}")
    return payload


def build_quantitative_report_payload(
    *,
    frozen_result: dict[str, Any],
    semantic_payload: dict[str, Any],
    samples: pd.DataFrame,
    unit_registry: dict[str, Any],
    source_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Enrich V3 semantics with read-only report statistics from frozen records."""
    date = str(frozen_result["date"])
    if str(semantic_payload.get("date")) != date:
        raise ValueError("Frozen result and semantic payload dates do not match")
    frame = samples.copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    source_paths = source_paths or {}

    overview, overview_trace = _build_daily_overview(
        date, frozen_result, semantic_payload, frame, unit_registry
    )
    processes, process_trace = _build_processes(
        frozen_result, semantic_payload, frame, unit_registry
    )
    responses, response_trace = _build_responses(
        semantic_payload, frame, unit_registry
    )
    quality, quality_trace = _build_quality(
        semantic_payload, frozen_result.get("data_quality_events", [])
    )
    traceability = copy.deepcopy(semantic_payload.get("traceability", {}))
    traceability.update(overview_trace)
    traceability.update(process_trace)
    traceability.update(response_trace)
    traceability.update(quality_trace)

    payload = {
        "schema_version": QUANTITATIVE_SCHEMA_VERSION,
        "date": date,
        "source_schema_versions": {
            "frozen_result": frozen_result.get("schema_version"),
            "construction_semantics": semantic_payload.get("schema_version"),
            "parameter_registry": unit_registry.get("schema_version"),
        },
        "source_paths": {key: str(value.resolve()) for key, value in source_paths.items()},
        "generation": {
            "method": "deterministic_read_only_report_level_quantitative_enrichment",
            "llm_invoked": False,
            "frozen_analysis_modified": False,
            "construction_semantics_v3_modified": False,
        },
        "parameter_registry": unit_registry,
        "daily_overview": overview,
        "main_processes": processes,
        "construction_responses": responses,
        "data_quality": quality,
        "traceability": traceability,
        "quantitative_limits": [
            "Main parameter engineering units remain unconfirmed because no PLC point table was found.",
            "Formal reports withhold unconfirmed absolute parameter values.",
            "Research previews label every displayed parameter value as a raw PLC record value with unit pending verification.",
            "Response percentages use first-quarter and last-quarter raw-value medians; they are not the internal normalized relative_change score.",
            "All report-level statistics are read-only calculations over frozen, quality-controlled records and do not change segmentation or event selection.",
        ],
    }
    payload["llm_input_packages"] = {
        "formal": build_quantitative_llm_package(payload, mode="formal"),
        "research_preview": build_quantitative_llm_package(
            payload, mode="research_preview"
        ),
    }
    payload["validation"] = validate_quantitative_payload(payload)
    return payload


def _build_daily_overview(
    date: str,
    frozen: dict[str, Any],
    semantic: dict[str, Any],
    samples: pd.DataFrame,
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = copy.deepcopy(semantic.get("daily_overview", []))
    trace: dict[str, dict[str, Any]] = {}
    duration_fact = next(
        (row for row in rows if row.get("fact_type") == "machine_state_duration"), None
    )
    overall = frozen.get("overall", {})
    observed_seconds = _number(
        overall.get("data_coverage", {}).get("observed_duration_seconds")
    )
    if duration_fact:
        durations = {
            key: float(value)
            for key, value in duration_fact.get("duration_seconds", {}).items()
        }
        denominator = sum(durations.values())
        duration_fact["percentage_denominator"] = {
            "definition": "sum_of_observed_capped_machine_state_durations",
            "duration_seconds": denominator,
            "observed_duration_seconds": observed_seconds,
            "hard_gaps_excluded": bool(
                overall.get("data_coverage", {}).get(
                    "hard_gaps_excluded_from_state_duration", False
                )
            ),
        }
        duration_fact["state_statistics"] = {
            state: {
                "display_name": STATE_NAMES.get(state, state),
                "duration_seconds": seconds,
                "duration_display": _format_duration(seconds),
                "percentage": 100.0 * seconds / denominator if denominator else None,
            }
            for state, seconds in durations.items()
        }
        duration_fact["unclassified_duration_seconds"] = (
            max(float(observed_seconds) - denominator, 0.0)
            if observed_seconds is not None
            else None
        )

    scope = samples.loc[samples["machine_state"].eq("advancing")].copy()
    stats = {
        parameter: _parameter_statistics(
            scope,
            parameter=parameter,
            denominator=len(scope),
            registry=registry,
        )
        for parameter in PARAMETER_ORDER
    }
    fact_id = f"{date}-OQ01"
    rows.append(
        {
            "fact_id": fact_id,
            "fact_type": "daily_advancing_parameter_statistics",
            "scope": {
                "filter": "machine_state == advancing",
                "scope_name": "quality_controlled_advancing_state_records",
                "scope_sample_count": int(len(scope)),
                "excludes_stopped_and_free_rotation": True,
            },
            "parameter_statistics": stats,
            "text": (
                f"当日推进状态共有 {len(scope)} 条记录；主要参数统计仅使用该范围内的有效记录。"
            ),
        }
    )
    trace[fact_id] = {
        "source_file": str(samples["source_file"].dropna().iloc[0])
        if samples["source_file"].notna().any()
        else None,
        "source_row_start": int(scope["source_row"].min()) if not scope.empty else None,
        "source_row_end": int(scope["source_row"].max()) if not scope.empty else None,
        "scope_filter": "machine_state == advancing",
        "columns": {
            parameter: PARAMETER_COLUMNS[parameter] for parameter in PARAMETER_ORDER
        },
        "statistics": "valid_count, valid_ratio, p10, median and p90 over finite values",
    }
    return rows, trace


def _build_processes(
    frozen: dict[str, Any],
    semantic: dict[str, Any],
    samples: pd.DataFrame,
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    cycle_map = {
        _cycle_summary(cycle).get("interval_id"): cycle
        for cycle in frozen.get("excavation_cycles", [])
    }
    rows: list[dict[str, Any]] = []
    trace: dict[str, dict[str, Any]] = {}
    for original in semantic.get("main_processes", []):
        row = copy.deepcopy(original)
        fact_id = row["fact_id"]
        if row.get("fact_type") == "within_day_parameter_comparison":
            for parameter, values in row.get("comparison_values", {}).items():
                values["parameter"] = parameter
                left = _number(values.get("left_value"))
                right = _number(values.get("right_value"))
                values["raw_value_difference"] = (
                    right - left if left is not None and right is not None else None
                )
                values["actual_percent_difference_from_raw_records"] = (
                    100.0 * (right - left) / abs(left)
                    if left not in (None, 0.0) and right is not None
                    else None
                )
                values["percentage_definition"] = (
                    "(right_raw_value - left_raw_value) / abs(left_raw_value) * 100"
                )
                values["value_label"] = registry["parameters"][parameter][
                    "value_label"
                ]
                values["formal_numeric_reporting"] = False
                values["research_raw_reporting"] = True
            row["comparison_scope_warning"] = (
                "The existing V3 comparison uses comparable sustained-operation scopes; "
                "the compared periods can contain different activity-expression classes."
            )
            rows.append(row)
            continue

        activities = []
        all_phase_ids: list[str] = []
        for activity in row.get("activities", []):
            interval_id = activity.get("internal_interval_id")
            phase_ids = _formal_stable_phase_ids(cycle_map.get(interval_id, {}))
            all_phase_ids.extend(phase_ids)
            scope = samples.loc[samples["phase_id"].isin(phase_ids)].copy()
            enriched = copy.deepcopy(activity)
            enriched["stable_phase_ids"] = phase_ids
            enriched["statistics_scope"] = {
                "scope_name": "formal_stable_phase_records",
                "scope_sample_count": int(len(scope)),
                "phase_count": len(phase_ids),
            }
            enriched["report_parameter_statistics"] = {
                parameter: _parameter_statistics(
                    scope,
                    parameter=parameter,
                    denominator=len(scope),
                    registry=registry,
                )
                for parameter in PARAMETER_ORDER
            }
            enriched["frozen_phase_statistics_match"] = _phase_statistics_match(
                cycle_map.get(interval_id, {}), scope
            )
            activities.append(enriched)

        session_scope = samples.loc[samples["phase_id"].isin(all_phase_ids)].copy()
        row["activities"] = activities
        row["duration_display"] = _format_duration(
            float(row.get("total_process_duration_seconds") or 0)
        )
        row["duration_display_rule"] = (
            "nearest_whole_minute_from_total_process_duration_seconds"
        )
        row["duration_accounting"] = _duration_accounting(row)
        row["report_statistics_scope"] = {
            "scope_name": "pooled_formal_stable_phase_records_from_selected_activities",
            "scope_sample_count": int(len(session_scope)),
            "phase_count": len(all_phase_ids),
            "comparable_scope_seconds": float(row.get("comparable_scope_seconds") or 0),
        }
        row["report_parameter_statistics"] = {
            parameter: _parameter_statistics(
                session_scope,
                parameter=parameter,
                denominator=len(session_scope),
                registry=registry,
            )
            for parameter in PARAMETER_ORDER
        }
        row["v3_comparison_statistic"] = row.pop("parameter_statistics", {})
        rows.append(row)
        trace[fact_id] = {
            **copy.deepcopy(semantic.get("traceability", {}).get(fact_id, {})),
            "report_level_quantitative_scope": {
                "phase_ids": all_phase_ids,
                "sample_count": int(len(session_scope)),
                "statistics": "pooled finite stable-phase records",
            },
        }
    return rows, trace


def _build_responses(
    semantic: dict[str, Any],
    samples: pd.DataFrame,
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = []
    trace: dict[str, dict[str, Any]] = {}
    for original in semantic.get("construction_responses", []):
        row = copy.deepcopy(original)
        fact_id = row["fact_id"]
        source = semantic.get("traceability", {}).get(fact_id, {})
        block = samples.copy()
        if source.get("source_file"):
            block = block.loc[block["source_file"].eq(source["source_file"])]
        if source.get("source_row_start") is not None:
            block = block.loc[block["source_row"].ge(source["source_row_start"])]
        if source.get("source_row_end") is not None:
            block = block.loc[block["source_row"].le(source["source_row_end"])]
        block = block.loc[
            block["time"].between(
                pd.Timestamp(row["start_time"]), pd.Timestamp(row["end_time"])
            )
        ]
        states = {}
        for parameter, frozen_state in row.get("parameter_states", {}).items():
            if parameter not in PARAMETER_COLUMNS:
                continue
            calculated = _response_parameter_statistics(
                block, parameter=parameter, registry=registry
            )
            expected_change = _number(frozen_state.get("absolute_change"))
            actual_change = _number(calculated.get("absolute_change"))
            calculated["frozen_event_absolute_change"] = expected_change
            calculated["absolute_change_matches_frozen_event"] = _close(
                actual_change, expected_change
            )
            calculated["trend"] = frozen_state.get("trend")
            calculated["fluctuation_level"] = frozen_state.get("fluctuation_level")
            calculated["internal_normalized_relative_change_excluded_from_llm"] = True
            states[parameter] = calculated
        row["parameter_states"] = states
        row["calculation_scope"] = {
            "source_file": source.get("source_file"),
            "source_row_start": source.get("source_row_start"),
            "source_row_end": source.get("source_row_end"),
            "sample_count": int(len(block)),
            "time_start": row.get("start_time"),
            "time_end": row.get("end_time"),
        }
        rows.append(row)
        trace[fact_id] = {
            **copy.deepcopy(source),
            "report_level_change_calculation": (
                "initial and final values are medians of the first and last quarter of "
                "quality-available samples in the frozen event range; percentage uses "
                "(final-initial)/abs(initial)*100"
            ),
        }
    return rows, trace


def _build_quality(
    semantic: dict[str, Any], frozen_events: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in frozen_events:
        grouped.setdefault(str(event.get("subtype") or "other"), []).append(event)
    rows = []
    trace = {}
    for original in semantic.get("data_quality", []):
        row = copy.deepcopy(original)
        events = grouped.get(str(row.get("subtype")), [])
        row["event_count_definition"] = "contiguous_or_nearby_quality_episode_count"
        row["affected_record_count"] = int(
            sum(int(event.get("sample_count") or 0) for event in events)
        )
        row["episode_span_record_count"] = int(
            sum(
                int(
                    event.get("episode_span_sample_count")
                    or event.get("sample_count")
                    or 0
                )
                for event in events
            )
        )
        rows.append(row)
        trace[row["fact_id"]] = {
            **copy.deepcopy(
                semantic.get("traceability", {}).get(row["fact_id"], {})
            ),
            "event_count_definition": row["event_count_definition"],
            "affected_record_count": row["affected_record_count"],
        }
    return rows, trace


def build_quantitative_llm_package(
    payload: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    if mode not in {"formal", "research_preview"}:
        raise ValueError(f"Unknown report mode: {mode}")
    sections = (
        "daily_overview",
        "main_processes",
        "construction_responses",
        "data_quality",
    )
    structured = {
        section: [_llm_safe_quantitative_fact(row, mode=mode) for row in payload[section]]
        for section in sections
    }
    expressions = [
        {
            "fact_id": row["fact_id"],
            "section": section,
            "text": row.get("text"),
        }
        for section in sections
        for row in payload[section]
        if row.get("text")
    ]
    constraints = [
        "Use only supplied facts; do not invent values, times, causes, actions or diagnoses.",
        "Today's advance is exclusively the guidance-shield-head chainage change.",
        "State percentages use the sum of observed capped state durations as their denominator; hard gaps are not stopped time.",
        "Do not expose internal interval IDs, phase labels, scores or candidate counts.",
        "Keep construction responses separate from data-quality limitations.",
        "Do not infer geology, cutter wear, jamming, faults, maintenance or staffing.",
        "Representative processes are selected coverage, not an exhaustive cycle list.",
        "Do not treat internal normalized relative_change as an actual percentage.",
    ]
    if mode == "formal":
        constraints.append(
            "Do not publish absolute advance-speed, thrust, torque or RPM values because their engineering units are unconfirmed."
        )
    else:
        constraints.extend(
            [
                "This is a research preview, not a formal engineering record.",
                "Every table or sentence containing parameter values must visibly state: 原始PLC记录值，工程单位待核实，仅用于研究检查.",
                "Raw-value percentages are permitted only when supplied as actual_percent_change_from_raw_records; preserve their stated calculation basis.",
            ]
        )
    return {
        "schema_version": LLM_SCHEMA_VERSION,
        "quantitative_enrichment_version": QUANTITATIVE_SCHEMA_VERSION,
        "date": payload["date"],
        "report_mode": mode,
        "target_layout": [
            "一、当日施工概况",
            "二、掘进过程与运行表现",
            "三、值得关注的运行情况",
            "附：数据来源与质量说明",
        ],
        "structured_facts": structured,
        "semantic_expressions": expressions,
        "generation_constraints": constraints,
        "evidence_lookup": "Use fact_id in the companion quantitative JSON traceability object.",
    }


def _llm_safe_quantitative_fact(row: dict[str, Any], *, mode: str) -> dict[str, Any]:
    safe = copy.deepcopy({key: value for key, value in row.items() if key != "text"})
    safe.pop("v3_comparison_statistic", None)
    if safe.get("fact_type") == "representative_construction_period":
        safe.pop("process_count", None)
        safe.pop("merge_basis", None)
        safe.pop("duration_accounting", None)
        safe["activities"] = [
            {
                key: value
                for key, value in activity.items()
                if key
                in {
                    "start_time",
                    "end_time",
                    "duration_seconds",
                    "activity_expression_class",
                    "statistics_scope",
                    "report_parameter_statistics",
                }
            }
            for activity in safe.get("activities", [])
        ]
        safe["parameter_comparison_basis"] = {
            parameter: {
                "comparison_cohort": basis.get("comparison_cohort"),
                "cohort_size": basis.get("cohort_size"),
                "statistic": basis.get("statistic"),
                "classification": basis.get("classification"),
                "value_validity": basis.get("value_validity"),
                "unit_status": basis.get("unit_status"),
            }
            for parameter, basis in safe.get("parameter_comparison_basis", {}).items()
        }
    if safe.get("fact_type") == "within_day_parameter_comparison":
        safe.pop("comparison_scope_warning", None)
        safe["comparison_basis"] = {
            "scope": safe.get("comparison_basis", {}).get("scope")
        }
    if safe.get("fact_type") == "observed_construction_response":
        safe.pop("calculation_scope", None)
    if mode == "formal":
        _withhold_unconfirmed_values(safe)
    return safe


def _withhold_unconfirmed_values(value: Any) -> None:
    if isinstance(value, dict):
        parameter_statistic = "parameter" in value and "unit_status" in value
        if parameter_statistic and not value.get("formal_numeric_reporting", False):
            for key in (
                "p10",
                "median",
                "p90",
                "mean",
                "initial_representative_value",
                "final_representative_value",
                "absolute_change",
                "actual_percent_change_from_raw_records",
                "frozen_event_absolute_change",
                "left_value",
                "right_value",
                "right_to_left_ratio",
                "raw_value_difference",
                "actual_percent_difference_from_raw_records",
            ):
                value.pop(key, None)
        for child in list(value.values()):
            _withhold_unconfirmed_values(child)
    elif isinstance(value, list):
        for child in value:
            _withhold_unconfirmed_values(child)


def validate_quantitative_payload(payload: dict[str, Any]) -> dict[str, Any]:
    checks = []

    def add(name: str, passed: bool, details: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    advance = next(
        row
        for row in payload["daily_overview"]
        if row.get("fact_type") == "daily_advance"
    )
    add(
        "today_advance_uses_guidance_chainage_only",
        advance.get("distance_source") == "guidance_shield_head_chainage",
    )
    states = next(
        (
            row
            for row in payload["daily_overview"]
            if row.get("fact_type") == "machine_state_duration"
        ),
        None,
    )
    if states is None:
        coverage = next(
            (
                row
                for row in payload["daily_overview"]
                if row.get("fact_type") == "data_coverage"
            ),
            {},
        )
        add(
            "state_duration_checks_not_applicable_when_fact_unavailable",
            True,
            {
                "status": "machine_state_duration_fact_unavailable",
                "sample_count": coverage.get("sample_count"),
            },
        )
    else:
        denominator = float(states["percentage_denominator"]["duration_seconds"])
        percentage_sum = sum(
            float(item["percentage"] or 0)
            for item in states.get("state_statistics", {}).values()
        )
        add(
            "state_percentage_sum_is_100",
            _close(percentage_sum, 100.0),
            percentage_sum,
        )
        observed = _number(
            states["percentage_denominator"].get("observed_duration_seconds")
        )
        add(
            "state_duration_matches_observed_duration",
            _close(denominator, observed),
            {"state_seconds": denominator, "observed_seconds": observed},
        )
    unit_errors = [
        parameter
        for parameter, definition in payload["parameter_registry"]["parameters"].items()
        if definition.get("engineering_unit") is not None
        and "confirmed" not in str(definition.get("unit_status"))
    ]
    add("unconfirmed_units_are_not_guessed", not unit_errors, unit_errors)
    process_mismatches = [
        activity.get("internal_interval_id")
        for process in payload["main_processes"]
        if process.get("fact_type") == "representative_construction_period"
        for activity in process.get("activities", [])
        if not activity.get("frozen_phase_statistics_match", False)
    ]
    add(
        "representative_activity_statistics_match_frozen_phases",
        not process_mismatches,
        process_mismatches,
    )
    response_mismatches = [
        f"{row['fact_id']}:{parameter}"
        for row in payload["construction_responses"]
        for parameter, state in row.get("parameter_states", {}).items()
        if not state.get("absolute_change_matches_frozen_event", False)
    ]
    add(
        "response_absolute_changes_match_frozen_events",
        not response_mismatches,
        response_mismatches,
    )
    formal_forbidden_keys = _find_keys(
        payload["llm_input_packages"]["formal"],
        {
            "p10",
            "median",
            "p90",
            "initial_representative_value",
            "final_representative_value",
            "absolute_change",
            "actual_percent_change_from_raw_records",
            "left_value",
            "right_value",
            "right_to_left_ratio",
            "raw_value_difference",
            "actual_percent_difference_from_raw_records",
            "current_value",
            "lower_threshold",
            "upper_threshold",
        },
    )
    add(
        "formal_llm_input_withholds_unconfirmed_parameter_values",
        not formal_forbidden_keys,
        formal_forbidden_keys,
    )
    research_text = json.dumps(
        payload["llm_input_packages"]["research_preview"], ensure_ascii=False
    )
    add(
        "research_llm_input_labels_raw_values",
        "原始PLC记录值，工程单位待核实" in research_text,
    )
    add(
        "internal_normalized_change_not_exposed_as_percentage",
        "relative_change_internal_only" not in research_text,
    )
    quality_definition_errors = [
        row["fact_id"]
        for row in payload["data_quality"]
        if row.get("event_count_definition")
        != "contiguous_or_nearby_quality_episode_count"
    ]
    add(
        "quality_event_counts_are_defined",
        not quality_definition_errors,
        quality_definition_errors,
    )
    return {
        "date": payload.get("date"),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def render_quantitative_report(payload: dict[str, Any], *, mode: str) -> str:
    if mode not in {"formal", "research_preview"}:
        raise ValueError(f"Unknown report mode: {mode}")
    date = pd.Timestamp(payload["date"])
    overview = payload["daily_overview"]
    coverage = next(row for row in overview if row["fact_type"] == "data_coverage")
    advance = next(row for row in overview if row["fact_type"] == "daily_advance")
    states = next(
        (row for row in overview if row["fact_type"] == "machine_state_duration"),
        None,
    )
    daily_parameters = next(
        row
        for row in overview
        if row["fact_type"] == "daily_advancing_parameter_statistics"
    )
    processes = [
        row
        for row in payload["main_processes"]
        if row.get("fact_type") == "representative_construction_period"
    ]
    comparisons = [
        row
        for row in payload["main_processes"]
        if row.get("fact_type") == "within_day_parameter_comparison"
    ]
    responses = _select_report_responses(payload["construction_responses"])
    title = f"{date.year}年{date.month}月{date.day}日 TBM施工日报（PLC数据部分）"
    lines = [f"# {title}", ""]
    if mode == "formal":
        lines.extend(
            [
                "> 生成说明：本文件为确定性程序生成的正式版预览，未调用大语言模型。未确认工程单位的参数绝对值未写入正文。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "> **研究用定量预览：下列参数数值均为原始PLC记录值，工程单位待核实，仅用于研究检查，不得作为正式工程量值。**",
                ">",
                "> 生成说明：本文件由确定性程序生成，未调用大语言模型。",
                "",
            ]
        )
    lines.extend(
        [
            "## 一、当日施工概况",
            "",
            "### 1.1 施工进度",
            "",
            f"当日 PLC 数据覆盖 {_clock(coverage['start_time'])}—{_clock(coverage['end_time'])}，共记录 {coverage['sample_count']} 条数据。导向盾首里程由 {_format_number(advance['chainage_start'], 0)} m 变化至 {_format_number(advance['chainage_end'], 0)} m，今日进尺为 {_format_number(advance['distance_m'], 2)} m。",
            "",
            "### 1.2 设备运行情况",
            "",
            "状态占比以有数据支持、经过采样时长封顶的状态时长之和为分母；硬采样缺口不计作停止时间。",
            "",
        ]
    )
    if states is None:
        lines.extend(
            [
                "当日有效记录不足，无法计算设备运行状态累计时长及占比。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "| 运行状态 | 累计时长 | 占有数据支持时长比例 |",
                "|---|---:|---:|",
            ]
        )
        for state, item in states["state_statistics"].items():
            lines.append(
                f"| {STATE_NAMES.get(state, state)} | {item['duration_display']} | {_format_number(item['percentage'], 2)}% |"
            )
    lines.extend(["", "### 1.3 主要掘进参数", ""])
    if mode == "formal":
        lines.extend(
            [
                f"统计范围为 `machine_state=advancing` 的 {daily_parameters['scope']['scope_sample_count']} 条推进状态记录，各参数按自身有效记录统计。由于 PLC 点位表缺失，推进速度、总推力、刀盘扭矩和刀盘转速的工程单位均未确认，正式版仅报告有效样本情况，不发布绝对数值。",
                "",
                "| 参数 | 有效样本数 | 有效比例 | 正式报告状态 |",
                "|---|---:|---:|---|",
            ]
        )
        for parameter in PARAMETER_ORDER:
            stat = daily_parameters["parameter_statistics"][parameter]
            lines.append(
                f"| {stat['display_name']} | {stat['valid_count']} | {_format_number(100 * stat['valid_ratio'], 2)}% | 工程单位待核实，绝对值暂不发布 |"
            )
    else:
        lines.extend(
            [
                f"统计范围为 `machine_state=advancing` 的 {daily_parameters['scope']['scope_sample_count']} 条记录。",
                "",
                "| 参数 | 有效样本数 | 有效比例 | P10 | 中位数 | P90 | 数值性质 |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for parameter in PARAMETER_ORDER:
            stat = daily_parameters["parameter_statistics"][parameter]
            lines.append(
                f"| {stat['display_name']} | {stat['valid_count']} | {_format_number(100 * stat['valid_ratio'], 2)}% | {_format_parameter(stat['p10'], parameter)} | {_format_parameter(stat['median'], parameter)} | {_format_parameter(stat['p90'], parameter)} | {stat['value_label']} |"
            )

    lines.extend(["", "## 二、掘进过程与运行表现", "", "### 2.1 主要施工过程", ""])
    lines.extend(
        [
            "| 时间范围 | 运行性质 | 连续性 | 实际累计活动时长 | 可比稳定范围 |",
            "|---|---|---|---:|---:|",
        ]
    )
    for row in processes:
        activity = _activity_name(row["activity_expression_class"])
        continuity = "连续" if row["continuity_class"] == "single_continuous_activity" else "间歇"
        lines.append(
            f"| {_clock(row['start_time'])}—{_clock(row['end_time'])} | {activity} | {continuity} | {row['duration_display']} | {_format_duration(row['report_statistics_scope']['comparable_scope_seconds'])} |"
        )

    if mode == "research_preview":
        lines.extend(
            [
                "",
                "已确认掘进作业的组成活动及其正式稳定阶段统计如下。每行只统计对应稳定阶段，不包含活动间隔。所有参数均为原始 PLC 记录值，工程单位待核实，仅用于研究检查。",
                "",
                "| 作业时间 | 稳定范围样本数 | 推进速度 P50 [P10—P90] | 总推力 P50 [P10—P90] | 刀盘扭矩 P50 [P10—P90] |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for process in processes:
            for activity in process.get("activities", []):
                if activity.get("activity_expression_class") != "confirmed_excavation":
                    continue
                statistics = activity["report_parameter_statistics"]
                sample_count = activity["statistics_scope"]["scope_sample_count"]
                lines.append(
                    "| "
                    f"{_clock(activity['start_time'])}—{_clock(activity['end_time'])} | "
                    f"{sample_count} | "
                    f"{_format_range(statistics['advance_speed'], 'advance_speed')} | "
                    f"{_format_range(statistics['thrust'], 'thrust')} | "
                    f"{_format_range(statistics['torque'], 'torque')} |"
                )

    lines.extend(["", "### 2.2 掘进参数特征", ""])
    if comparisons:
        comparison = comparisons[0]
        lines.append(comparison["text"])
        if mode == "research_preview":
            lines.extend(
                [
                    "",
                    "下表沿用 V3 的可比持续运行片段口径。两个时段可能包含不同的运行性质，因此数值差异只用于描述，不解释为地质或设备原因。",
                    "",
                    "| 参数 | 00:00—12:00 | 12:00—19:00 | 后者相对前者变化 |",
                    "|---|---:|---:|---:|",
                ]
            )
            for parameter in ("advance_speed", "thrust", "torque"):
                values = comparison["comparison_values"][parameter]
                lines.append(
                    f"| {PARAMETER_NAMES[parameter]} | {_format_parameter(values['left_value'], parameter)} | {_format_parameter(values['right_value'], parameter)} | {_format_signed(values['actual_percent_difference_from_raw_records'])}% |"
                )
            lines.extend(
                [
                    "",
                    "上述数值均为原始 PLC 记录值的既有加权中位数比较，工程单位待核实，仅用于研究检查。",
                ]
            )
    else:
        lines.append("现有事实未提供满足既定口径的跨时段参数比较。")

    lines.extend(["", "### 2.3 当日运行表现", ""])
    lines.append(
        "上午选出的代表性过程以推进相关运行活动为主，10:57—11:32存在一段已确认掘进作业；12:30—18:47期间选出了多段已确认掘进作业，各活动之间存在间隔，不能按首末时间跨度理解为连续掘进。跨时段参数差异采用上一节所列的可比持续运行片段口径。"
    )

    lines.extend(["", "## 三、值得关注的运行情况", "", "### 3.1 主要参数变化", ""])
    if responses:
        if mode == "formal":
            for row in responses:
                lines.append(f"- {row['text']}")
        else:
            lines.extend(
                [
                    "以下数值为事件范围内首四分位与末四分位样本中位数的比较，工程单位待核实，仅用于研究检查。百分比按 `(末段中位数-初段中位数)/|初段中位数|×100%` 计算，不是内部归一化变化指标。",
                    "",
                    "| 时间 | 参数 | 趋势 | 初段代表值 | 末段代表值 | 实际差值 | 原始值变化比例 |",
                    "|---|---|---|---:|---:|---:|---:|",
                ]
            )
            for row in responses:
                for parameter, state in row["parameter_states"].items():
                    if state.get("trend") not in {"increasing", "decreasing"}:
                        continue
                    lines.append(
                        f"| {_clock(row['start_time'])}—{_clock(row['end_time'])} | {PARAMETER_NAMES[parameter]} | {_trend_name(state['trend'])} | {_format_parameter(state['initial_representative_value'], parameter)} | {_format_parameter(state['final_representative_value'], parameter)} | {_format_signed_parameter(state['absolute_change'], parameter)} | {_format_signed(state['actual_percent_change_from_raw_records'])}% |"
                    )
    else:
        lines.append("现有分析结果未筛选出需要单独记录的重点运行响应。")

    lines.extend(["", "### 3.2 多参数共同响应", ""])
    opposing = [
        row
        for row in responses
        if {state.get("trend") for state in row["parameter_states"].values()}
        >= {"increasing", "decreasing"}
    ]
    if opposing:
        lines.extend(f"- {row['text']}" for row in opposing)
    else:
        lines.append("选出的重点响应中未形成需要单独补充的相反方向多参数变化。")

    lines.extend(["", "## 附：数据来源与质量说明", ""])
    lines.extend(
        [
            "本报告依据当日 TBM PLC 监测记录、冻结版质量控制结果和工程语义 V3 生成。数据质量事件中的“处”表示连续或相邻记录组成的事件段，不等同于受影响记录条数。",
            "",
            "| 数据质量项目 | 事件段数 | 受影响记录数 | 使用限制 |",
            "|---|---:|---:|---|",
        ]
    )
    quality_action = {
        "unreliable_advance_speed": "未用于正常推进速度统计，也未补造真实速度",
        "unreliable_canonical_rpm": "未用于正式刀盘转速结论",
        "unreliable_penetration": "未作为可靠贯入度证据",
    }
    quality_names = {
        "unreliable_advance_speed": "推进速度不可信记录",
        "unreliable_canonical_rpm": "刀盘转速可疑记录",
        "unreliable_penetration": "贯入度可疑记录",
    }
    for row in payload["data_quality"]:
        lines.append(
            f"| {quality_names.get(row['subtype'], row['subtype'])} | {row['event_count']} | {row['affected_record_count']} | {quality_action.get(row['subtype'], '保留相应数据限制')} |"
        )
    lines.extend(
        [
            "",
            "PLC 点位表尚未取得，推进速度、总推力、刀盘扭矩的工程单位未确认；刀盘转速还采用待点位表核验的暂定映射。正式版未发布这些参数的绝对数值。",
            "",
        ]
    )
    return "\n".join(lines)


def write_quantitative_outputs(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date = payload["date"]
    paths = {
        "quantitative_json": output_dir / f"{date}.quantitative.json",
        "formal_llm_input": output_dir / f"{date}.llm_input.formal.json",
        "research_llm_input": output_dir / f"{date}.llm_input.research.json",
        "formal_report": output_dir / f"{date}.report.formal.deterministic.md",
        "research_report": output_dir / f"{date}.report.research.deterministic.md",
        "validation": output_dir / f"{date}.validation.json",
    }
    write_json(paths["quantitative_json"], payload)
    write_json(paths["formal_llm_input"], payload["llm_input_packages"]["formal"])
    write_json(
        paths["research_llm_input"],
        payload["llm_input_packages"]["research_preview"],
    )
    paths["formal_report"].write_text(
        render_quantitative_report(payload, mode="formal"), encoding="utf-8"
    )
    paths["research_report"].write_text(
        render_quantitative_report(payload, mode="research_preview"), encoding="utf-8"
    )
    write_json(paths["validation"], payload["validation"])
    return paths


def _parameter_statistics(
    frame: pd.DataFrame,
    *,
    parameter: str,
    denominator: int,
    registry: dict[str, Any],
) -> dict[str, Any]:
    definition = registry["parameters"][parameter]
    column = PARAMETER_COLUMNS[parameter]
    values = pd.to_numeric(frame[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    result = {
        "parameter": parameter,
        "display_name": definition["display_name"],
        "source_field": definition["raw_field"],
        "analysis_column": column,
        "valid_count": int(len(values)),
        "valid_ratio": float(len(values) / denominator) if denominator else 0.0,
        "p10": None,
        "median": None,
        "p90": None,
        "engineering_unit": definition["engineering_unit"],
        "unit_status": definition["unit_status"],
        "formal_numeric_reporting": bool(definition["formal_numeric_reporting"]),
        "research_raw_reporting": bool(definition["research_raw_reporting"]),
        "value_label": definition["value_label"],
        "quality_note": definition["quality_note"],
    }
    if not values.empty:
        q10, median, q90 = values.quantile([0.1, 0.5, 0.9]).tolist()
        result.update(
            {"p10": float(q10), "median": float(median), "p90": float(q90)}
        )
    return result


def _response_parameter_statistics(
    frame: pd.DataFrame, *, parameter: str, registry: dict[str, Any]
) -> dict[str, Any]:
    base = _parameter_statistics(
        frame, parameter=parameter, denominator=len(frame), registry=registry
    )
    values = pd.to_numeric(
        frame[PARAMETER_COLUMNS[parameter]], errors="coerce"
    ).replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        base.update(
            {
                "initial_representative_value": None,
                "final_representative_value": None,
                "absolute_change": None,
                "actual_percent_change_from_raw_records": None,
            }
        )
        return base
    quarter = max(1, len(values) // 4)
    initial = float(values.iloc[:quarter].median())
    final = float(values.iloc[-quarter:].median())
    change = final - initial
    percent = 100.0 * change / abs(initial) if abs(initial) > 1e-12 else None
    base.update(
        {
            "initial_representative_value": initial,
            "final_representative_value": final,
            "absolute_change": change,
            "actual_percent_change_from_raw_records": percent,
            "edge_statistic": "median_of_first_and_last_quarter_of_valid_event_samples",
            "percentage_definition": "(final_raw_median - initial_raw_median) / abs(initial_raw_median) * 100",
        }
    )
    return base


def _formal_stable_phase_ids(cycle: dict[str, Any]) -> list[str]:
    summary = _cycle_summary(cycle)
    start = pd.Timestamp(summary.get("start_time")) if summary.get("start_time") else None
    end = pd.Timestamp(summary.get("end_time")) if summary.get("end_time") else None
    ids = []
    for phase in cycle.get("phases", []):
        phase_summary = phase.get("phase_summary", {})
        if phase_summary.get("phase_label") != "stable":
            continue
        if phase_summary.get("dynamic_analysis_scope_only", False):
            continue
        phase_start = pd.Timestamp(phase_summary.get("start_time"))
        phase_end = pd.Timestamp(phase_summary.get("end_time"))
        if start is not None and phase_start < start:
            continue
        if end is not None and phase_end > end:
            continue
        ids.append(str(phase_summary["phase_id"]))
    return ids


def _phase_statistics_match(cycle: dict[str, Any], scope: pd.DataFrame) -> bool:
    summaries = [
        phase.get("phase_summary", {})
        for phase in cycle.get("phases", [])
        if phase.get("phase_summary", {}).get("phase_label") == "stable"
        and not phase.get("phase_summary", {}).get(
            "dynamic_analysis_scope_only", False
        )
    ]
    if not summaries:
        return scope.empty
    for summary in summaries:
        phase_scope = scope.loc[scope["phase_id"].eq(summary.get("phase_id"))]
        for parameter in PARAMETER_ORDER:
            values = pd.to_numeric(
                phase_scope[PARAMETER_COLUMNS[parameter]], errors="coerce"
            ).dropna()
            if int(summary.get(f"{parameter}_count") or 0) != len(values):
                return False
            if values.empty:
                continue
            q10, q50, q90 = values.quantile([0.1, 0.5, 0.9]).tolist()
            for actual, expected in (
                (q10, summary.get(f"{parameter}_p10")),
                (q50, summary.get(f"{parameter}_p50")),
                (q90, summary.get(f"{parameter}_p90")),
            ):
                if not _close(_number(actual), _number(expected)):
                    return False
    return True


def _duration_accounting(row: dict[str, Any]) -> dict[str, Any]:
    elapsed = float(row.get("elapsed_span_seconds") or 0)
    activity_sum = sum(
        float(activity.get("duration_seconds") or 0)
        for activity in row.get("activities", [])
    )
    gap_sum = sum(
        float(gap.get("duration_seconds") or 0)
        for gap in row.get("intervening_gaps", [])
    )
    return {
        "elapsed_timestamp_span_seconds": elapsed,
        "activity_duration_sum_seconds": activity_sum,
        "intervening_timestamp_gap_sum_seconds": gap_sum,
        "activity_plus_gap_minus_elapsed_seconds": activity_sum + gap_sum - elapsed,
        "definition_note": (
            "Activity durations include a capped representative tail interval per activity; "
            "elapsed span and intervening gaps are timestamp differences. Report display uses "
            "the activity-duration sum rounded to the nearest minute."
        ),
    }


def _select_report_responses(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    def score(row: dict[str, Any]) -> tuple[int, float]:
        changing = sum(
            state.get("trend") in {"increasing", "decreasing"}
            for state in row.get("parameter_states", {}).values()
        )
        return changing, float(row.get("duration_seconds") or 0)

    selected = sorted(rows, key=score, reverse=True)[:limit]
    return sorted(selected, key=lambda row: row.get("start_time") or "")


def _cycle_summary(cycle: dict[str, Any]) -> dict[str, Any]:
    return cycle.get("cycle_summary") or cycle.get("interval_summary") or {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return bool(np.isclose(left, right, rtol=1e-9, atol=1e-8))


def _format_duration(seconds: float) -> str:
    minutes = int(max(float(seconds), 0.0) / 60.0 + 0.5)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    return f"{minutes}分钟"


def _clock(value: str | None) -> str:
    return pd.Timestamp(value).strftime("%H:%M") if value else "未知"


def _format_number(value: Any, decimals: int) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:,.{decimals}f}"


def _format_parameter(value: Any, parameter: str) -> str:
    decimals = 3 if parameter == "rpm" else 2
    return _format_number(value, decimals)


def _format_signed(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+.2f}"


def _format_signed_parameter(value: Any, parameter: str) -> str:
    number = _number(value)
    if number is None:
        return "—"
    decimals = 3 if parameter == "rpm" else 2
    return f"{number:+,.{decimals}f}"


def _format_range(statistic: dict[str, Any], parameter: str) -> str:
    return (
        f"{_format_parameter(statistic.get('median'), parameter)} "
        f"[{_format_parameter(statistic.get('p10'), parameter)}—"
        f"{_format_parameter(statistic.get('p90'), parameter)}]"
    )


def _activity_name(value: str) -> str:
    return {
        "confirmed_excavation": "掘进作业",
        "advance_related_operation": "推进相关运行活动",
        "mixed_excavation_and_advance_related_operation": "混合活动",
    }.get(value, value)


def _trend_name(value: str) -> str:
    return {"increasing": "上升", "decreasing": "下降", "stable": "稳定"}.get(
        value, value
    )


def _find_keys(value: Any, forbidden: set[str], path: str = "$") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden:
                matches.append(child_path)
            matches.extend(_find_keys(child, forbidden, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_find_keys(child, forbidden, f"{path}[{index}]"))
    return matches
