from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tbm_process.final_reporting.common import format_chainage
from tbm_process.reporting.front_sections import ReportSources, build_front_sections_facts, load_report_config, validate_front_sections_facts

from . import VERSION
from .geology import build_geology, joint_analysis
from .performance import calculate_metrics, clean, performance_windows, row_evidence, statistics, summarize_performance


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def duration(seconds: float) -> str:
    value = int(round(float(seconds)))
    hours, rest = divmod(value, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}小时{minutes:02d}分{secs:02d}秒" if hours else f"{minutes}分{secs:02d}秒"


def progress_comparison(current: dict[str, Any], reference: dict[str, Any] | None) -> dict[str, Any]:
    t = current["machine_state_duration_seconds"].get("advancing", 0)/3600
    length = current["daily_advance"]["net_change_m"]
    if not reference or t <= 0 or length is None or length < 0:
        return {"status": "insufficient_data", "reason": "no_usable_preceding_day_or_positive_advancing_time"}
    before = reference["machine_state_duration_seconds"].get("advancing", 0)/3600
    old_length = reference["daily_advance"]["net_change_m"]
    if before <= 0 or old_length is None or old_length < 0:
        return {"status": "insufficient_data", "reason": "reference_progress_invalid"}
    q, q0 = length/t, old_length/before
    time_component, rate_component = (t-before)*(q+q0)/2, (q-q0)*(t+before)/2
    return {"status": "descriptive_arithmetic_only", "reference_date": reference["date"],
            "current_advance_m": length, "reference_advance_m": old_length, "difference_m": length-old_length,
            "current_advancing_hours": t, "reference_advancing_hours": before,
            "nominal_m_per_advancing_hour": q, "reference_nominal_m_per_advancing_hour": q0,
            "time_component_m": time_component, "rate_component_m": rate_component,
            "identity_error_m": (time_component+rate_component)-(length-old_length),
            "definition": "symmetric_arithmetic_decomposition_L=T*q; q_is_chainage_change_over_state_time_not_speed_integration",
            "limit": "not_causal_attribution; reference_day_not_certified_identical_geology_or_operation"}


def find_prior_overview(project: Path, analysis: Path, current: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    day = date.fromisoformat(current["date"])
    # Existing facts are reused, not future PLC data or a new historical model fit.
    base = project / "experiments/plc_mapping_repair_v1/impact/report_facts/after"
    if analysis.resolve() != (project / "experiments/plc_mapping_repair_v1/replay/after").resolve():
        return None
    for offset in range(1, config["analysis"]["history_lookback_days"]+1):
        p = base / f"{day-timedelta(days=offset)}.json"
        if not p.is_file():
            continue
        row = json.loads(p.read_text())["facts"]["daily_overview"]
        advance = row["daily_advance"]["net_change_m"]
        if row["effective_state_duration_seconds"]/86400 < config["analysis"]["minimum_daily_coverage_ratio"] or advance <= 0:
            continue
        if row["machine_state_duration_seconds"].get("advancing", 0) <= 0:
            continue
        row = deepcopy(row)
        row["source_fact_path"] = str(p)
        row["source_fact_sha256"] = digest(p)
        return row
    return None


def _safe_parameter_statistics(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for field in ["advance_speed", "thrust", "torque", "rpm_canonical"]:
        values = pd.to_numeric(frame[field], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if field == "advance_speed":
            values = values.where(frame.advance_speed_quality.eq("usable"))
        elif field == "rpm_canonical":
            values = values.where(frame.rpm_quality.eq("usable_provisional_mapping"))
        values = values.where(values.ge(0))
        result[field] = {**statistics(values), "unit": config["units"][field],
                         "scope_count": len(frame), "valid_ratio": float(values.notna().mean()) if len(frame) else None}
    return result


def attention_candidates(windows: list[dict[str, Any]], geology: dict[str, Any], overview: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for window in windows:
        changes = window["parameter_changes"]
        moving = [name for name in ["advance_speed", "thrust", "torque"] if changes[name]["trend"] in {"increasing", "decreasing"}]
        effects = {name: state["absolute_change"]/abs(state["initial_median"])
                   for name, state in changes.items() if state["initial_median"] is not None and abs(state["initial_median"]) > 1e-6}
        if len(moving) < 2 or not any(abs(effects.get(name, 0)) >= config["analysis"]["notable_relative_change"] for name in moving):
            continue
        candidates.append({"event_id": "RESPONSE:"+window["window_id"], "type": "persistent_parameter_response_not_fault",
                           "platform_id": window["platform_id"], "time": [window["start_time"], window["end_time"]],
                           "elapsed_seconds": window["elapsed_seconds"], "observation": changes,
                           "quantitative_basis": effects, "comparison_baseline": "first_and_last_quarter_of_same_contiguous_window",
                           "geology_background": None, "source": window["source"], "reliability": "qualified_stable_advancing",
                           "allowed_conclusion": "measured_parameter_change_only", "forbidden": ["fault_diagnosis", "geology_caused_response"],
                           "magnitude": max(abs(v) for v in effects.values()), "chapter_owner": 6})
    for part in geology["day_partitions"]+geology["ahead_partitions"]:
        for comparison in part["comparisons"]:
            if comparison["status"] == "explicit_reported_grade_difference":
                candidates.append({"event_id": "GEO:"+comparison["left_id"]+":"+comparison["right_id"],
                                   "type": "different_forecast_grades", "range_m": [part["start_m"], part["end_m"]],
                                   "observation": comparison, "quantitative_basis": comparison["attributes"]["grade"],
                                   "comparison_baseline": "two_prior_sources_same_nominal_range", "source": [comparison["left_id"], comparison["right_id"]],
                                   "reliability": "source_reported_not_verified_ground_truth", "magnitude": 0,
                                   "allowed_conclusion": "preserve_both_predictions", "forbidden": ["choose_correct_grade"], "chapter_owner": 3})
    if overview["coverage_ratio"] < config["analysis"]["minimum_daily_coverage_ratio"]:
        candidates.append({"event_id": "QUALITY:COVERAGE", "type": "data_quality_not_machine_event", "magnitude": 0,
                           "observation": {"coverage_ratio": overview["coverage_ratio"]}, "chapter_owner": 6,
                           "allowed_conclusion": "incomplete_daily_observation", "forbidden": ["unobserved_time_is_stopped"]})
    selected, omitted, used = [], [], set()
    for row in sorted(candidates, key=lambda x: -x["magnitude"]):
        key = row.get("platform_id") or row["event_id"]
        if key in used:
            omitted.append({"event_id": row["event_id"], "reason": "same_operating_period_already_represented"})
        elif row["chapter_owner"] != 6:
            omitted.append({"event_id": row["event_id"], "reason": "fully_presented_in_geology_chapter_no_duplicate_prose"})
        elif len(selected) >= config["analysis"]["maximum_attention_items"]:
            omitted.append({"event_id": row["event_id"], "reason": "report_attention_limit_complete_candidates_retained"})
        else:
            selected.append(row)
            used.add(key)
    return {"candidates": candidates, "selected": selected, "omitted": omitted,
            "selection": "quality_gate_then_observed_effect_and_duration; merge_by_platform; no_fault_or_risk_score"}


def build_facts(*, report_day: str, project: Path, analysis: Path, catalog_path: Path, registry_path: Path,
                source_root: Path, config: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    samples_path = analysis / "processed_samples" / f"{report_day}.csv"
    if not samples_path.exists():
        samples_path = samples_path.with_suffix(".csv.gz")
    sources = ReportSources(samples=samples_path, platforms=analysis/"tables/实验平台明细.csv", phases=analysis/"tables/实验四阶段明细.csv")
    front_config = load_report_config(project/"configs/daily_report_front_sections.json")
    front, samples = build_front_sections_facts(date=report_day, sources=sources, config=front_config)
    old_check = validate_front_sections_facts(front, samples, front_config)
    if any(not row["passed"] for row in old_check["checks"] if row["name"] != "descriptions_are_constrained"):
        raise ValueError("Frozen-source facts failed a structural consistency check")
    samples, quality = calculate_metrics(samples, config)
    phase_config = json.loads((project/"config.json").read_text())
    windows = performance_windows(samples, config, phase_config)
    overview = deepcopy(front["daily_overview"])
    overview["coverage_ratio"] = overview["effective_state_duration_seconds"]/86400
    overview["daily_comparison_allowed"] = overview["coverage_ratio"] >= config["analysis"]["minimum_daily_coverage_ratio"]
    overview["advancing_seconds"] = overview["machine_state_duration_seconds"].get("advancing", 0)
    overview["nonadvancing_observed_seconds"] = overview["effective_state_duration_seconds"]-overview["advancing_seconds"]
    overview["advancing_share_of_observed_percent"] = 100*overview["advancing_seconds"]/overview["effective_state_duration_seconds"] if overview["effective_state_duration_seconds"] else None
    overview["terminology"] = "state-classified advancing time is not independently measured rock-cutting time"
    advance = overview["daily_advance"]
    overview["daily_advance"]["start_chainage"] = format_chainage(advance["start_value_m"])
    overview["daily_advance"]["end_chainage"] = format_chainage(advance["end_value_m"])
    prior = find_prior_overview(project, analysis, overview, config) if overview["daily_comparison_allowed"] else None
    overview["history_comparison"] = progress_comparison(overview, prior)
    overview["history_comparison"]["reference_source"] = {k: prior[k] for k in ["source_fact_path", "source_fact_sha256"]} if prior else None
    net = advance["net_change_m"]
    direction = 1 if net > 0 else -1 if net < 0 else None
    direction_basis = "quality_checked_daily_chainage_net_sign" if direction else "not_confirmed_for_zero_advance"
    if direction is None and prior and prior["daily_advance"]["net_change_m"] != 0:
        direction = 1 if prior["daily_advance"]["net_change_m"] > 0 else -1
        direction_basis = "prior_observed_chainage_direction_not_machine_heading_measurement"
    selected_platforms = []
    for platform in front["platforms"]:
        block = samples.loc[samples.experimental_platform_id.eq(platform["platform_id"])]
        copy = deepcopy(platform)
        copy["working_parameter_statistics"] = _safe_parameter_statistics(block.loc[~block.machine_state.eq("stopped")], config)
        copy["performance"] = summarize_performance(block, config)
        advance_value = copy["interval_advance"]
        if advance_value["net_change_m"] is not None and advance_value["net_change_m"] < 0:
            advance_value["use_status"] = "review_required"
            advance_value["quality_reasons"].append("negative_interval_change_not_reported_as_excavation")
        copy["window_ids"] = [w["window_id"] for w in windows if w["platform_id"] == copy["platform_id"]]
        copy["reader_description"] = "按实际工作记录统计；稳定性能另见合格样本"
        selected_platforms.append(copy)
    def rank(row):
        adv = row["interval_advance"]
        return (adv["net_change_m"] if adv["use_status"] == "adoptable" and adv["net_change_m"] is not None else -1,
                row["working_duration_seconds"])
    selected = sorted(sorted(selected_platforms, key=rank, reverse=True)[:config["analysis"]["maximum_selected_platforms"]], key=lambda x: x["start_time"])
    catalog = json.loads(catalog_path.read_text()) if catalog_path.is_file() else {"reports": []}
    registry = json.loads(registry_path.read_text()) if registry_path.is_file() else {"source_registry": []}
    geology = build_geology(catalog, registry, source_root,
                            report_day, advance["start_value_m"], advance["end_value_m"], direction, config)
    geology["direction_basis"] = direction_basis
    geology["input_availability"] = {"catalog_exists": catalog_path.is_file(), "registry_exists": registry_path.is_file(),
                                     "missing_data_policy": "retain_PLC_report_and_mark_geology_or_original_images_unavailable"}
    geology["joint_analysis"] = joint_analysis(geology, samples, config)
    if prior:
        prev_end = prior["daily_advance"]["end_value_m"]
        prev_ahead = sorted((prev_end, prev_end+direction*config["analysis"]["ahead_metres"])) if direction else None
        now_ids = {i for part in geology["ahead_partitions"] for i in part["record_ids"]}
        previous_ids = {i for i, row in geology["forecast_records"].items() if prev_ahead and row["start_m"] < prev_ahead[1] and row["end_m"] > prev_ahead[0]
                        and row["source"]["availability"]["document_date"] < prior["date"]}
        geology["ahead_updates"] = {"reference_date": prior["date"], "previous_range_m": prev_ahead,
                                   "newly_visible_record_ids": sorted(now_ids-previous_ids),
                                   "retained_record_ids": sorted(now_ids&previous_ids), "basis": "same_catalog_historical_time_gate_and_window_intersection"}
    else:
        geology["ahead_updates"] = {"status": "no_valid_preceding_day_reference"}
    attention = attention_candidates(windows, geology, overview, config)
    return clean({"schema_version": VERSION, "date": report_day, "config": config, "daily_overview": overview,
                  "platform_count": len(selected_platforms), "platforms": selected_platforms, "selected_platform_ids": [p["platform_id"] for p in selected],
                  "platform_selection": {"rule": "adoptable_cumulative_advance_then_working_duration", "total": len(selected_platforms), "selected": len(selected),
                                         "unselected_scope": "all_remaining_platforms_retained_in_appendix_and_JSON"},
                  "daily_parameter_statistics": _safe_parameter_statistics(samples.loc[~samples.machine_state.eq("stopped")], config),
                  "performance": {"quality": quality, "daily": summarize_performance(samples, config), "windows": windows},
                  "geology": geology, "attention": attention, "legacy_fact_checks": old_check,
                  "source": {"analysis_directory": str(analysis), "samples": str(samples_path), "samples_sha256": digest(samples_path),
                             "platform_table": str(sources.platforms), "phase_table": str(sources.phases),
                             "catalog": str(catalog_path), "catalog_sha256": digest(catalog_path) if catalog_path.is_file() else None, "registry": str(registry_path),
                             "registry_sha256": digest(registry_path) if registry_path.is_file() else None},
                  "limits": ["operating_platforms_not_verified_excavation_cycles", "no_machine_fault_or_geological_causal_diagnosis",
                             "all_historical_outputs_unchanged", "performance_values_use_user_confirmed_units",
                             "same_day_sketch_without_verified_availability_is_not_used_as_current_observation"]}), samples
