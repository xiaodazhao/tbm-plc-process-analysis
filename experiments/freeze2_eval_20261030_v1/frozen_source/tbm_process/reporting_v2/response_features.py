"""Versioned report-only response scopes; frozen state and phase labels are immutable."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json

import numpy as np
import pandas as pd

from .performance import calculate_metrics, clean, performance_windows, row_evidence, statistics


VERSION = "plc_response_features_v2_20261027"
BASE = ("advance_speed", "thrust", "torque")
DEPENDENT = ("rpm_canonical", "pr", "fpi", "se")
NAMES = {"advance_speed": "推进速度", "thrust": "总推力", "torque": "刀盘扭矩", "rpm_canonical": "刀盘转速",
         "pr": "计算贯入度", "fpi": "总推力口径贯入指数", "se": "理论机械比能"}


def key(kind, *parts):
    value = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return kind+":"+hashlib.sha256(value.encode()).hexdigest()[:20]


def response_samples(frame, config):
    out, quality = calculate_metrics(frame, config)
    stable = "experimental_phase_label" if "experimental_phase_label" in out else "phase_label"
    base = (out[stable].eq("stable") & out.machine_state.eq("advancing")
            & out.advance_speed_quality.eq("usable")
            & out.advance_speed.gt(config["analysis"]["minimum_speed_mm_min"])
            & out.thrust.gt(0) & out.torque.gt(0)
            & out[list(BASE)].notna().all(axis=1))
    if "mapping_core_positions_confirmed" in out:
        base &= out.mapping_core_positions_confirmed.astype(str).str.lower().eq("true")
    # The base scope does not reinterpret an unresolved RPM as motion: the existing
    # advancing state is still mandatory, even when that limits recovered coverage.
    out["basic_response_eligible"] = base
    rpm = base & out.rpm_quality.eq("usable_provisional_mapping") & out.rpm_canonical.gt(config["analysis"]["minimum_rpm"])
    if "rpmsource" in out:
        rpm &= ~out.rpmsource.fillna("unresolved").astype(str).str.contains("unresolved|unavailable", regex=True)
    out["rpm_response_eligible"] = rpm
    out["performance_eligible"] &= base
    return out, quality


def metric_summary(frame, field, config):
    column = "basic_response_eligible" if field in BASE else "rpm_response_eligible" if field == "rpm_canonical" else "performance_eligible"
    good = frame.loc[frame[column] & frame[field].notna()]
    seconds = float(pd.to_numeric(good.sample_dt_seconds, errors="coerce").fillna(0).sum())
    enough = len(good) >= config["analysis"]["minimum_samples"] and seconds >= config["analysis"]["minimum_duration_seconds"]
    return clean({"field": field, "unit": config["units"][field], **statistics(good[field]),
                  "supported_duration_seconds": seconds, "valid_ratio_of_scope": len(good)/len(frame) if len(frame) else None,
                  "status": "usable_descriptive" if enough else "insufficient", "source": row_evidence(good),
                  "sample_scope": column, "statistics_weighting": "samples_not_time_weighted",
                  "mapping_status": "existing_processed_core_fields_no_new_mapping_inference" if field in BASE else "row_resolved_RPM_provisional_identity",
                  "unit_status": config["equipment"]["evidence"]})


def rows_from_evidence(frame, sources):
    mask = pd.Series(False, index=frame.index)
    for source in sources:
        mask |= frame.source_file.eq(source["source_file"]) & frame.source_row.isin(source["source_rows"])
    return frame.loc[mask]


def coverage(frame, flag, windows, hard_gap_seconds):
    good = frame.loc[frame[flag]]
    described = set()
    for window in windows:
        described.update((r["source_file"], int(i)) for r in window["source"] for i in r["source_rows"])
    represented = pd.Series([(str(r.source_file), int(r.source_row)) in described for r in good.itertuples()], index=good.index, dtype=bool)
    missing = good.loc[~represented]
    pieces = []
    if len(missing):
        groups = (pd.Series(missing.index, index=missing.index).diff().ne(1)
                  | missing.source_file.ne(missing.source_file.shift())
                  | missing.time.diff().dt.total_seconds().gt(hard_gap_seconds)).cumsum()
        for _, block in missing.groupby(groups, sort=False):
            pieces.append({"source": row_evidence(block), "count": len(block),
                           "reason": "not_retained_by_existing_window_minimum_samples_or_elapsed_span; no_invented_trend"})
    return {"qualified_count": len(good), "described_count": int(represented.sum()), "undescribed_count": len(missing),
            "described_ratio": float(represented.mean()) if len(good) else None, "undescribed_ranges": pieces}


def build_response_features(scope, config, phase_config, *, response_id, bounds, day_resolution=None):
    frame, quality = response_samples(scope, config)
    summaries = {f: metric_summary(frame, f, config) for f in BASE+DEPENDENT}
    windows = {}
    for group, flag, fields in [("basic", "basic_response_eligible", BASE), ("performance", "performance_eligible", ("pr",))]:
        raw = performance_windows(frame, config, phase_config, eligibility_column=flag, parameters=fields,
                                  boundary_columns=("source_file",))
        windows[group] = []
        for window in raw:
            block = rows_from_evidence(frame, window["source"])
            phase_col = "experimental_phase_id" if "experimental_phase_id" in block else "phase_id"
            item = {k: v for k, v in window.items() if k != "performance"}
            item.update({"window_id": key("RW", response_id, group, window["source"]), "group": group,
                         "phase_id": str(block[phase_col].iloc[0]),
                         "metric_statistics": {f: metric_summary(block, f, config) for f in fields},
                         "supported_duration_seconds": float(block.sample_dt_seconds.fillna(0).sum())})
            windows[group].append(item)
    base = frame.loc[frame.basic_response_eligible]
    platform = "experimental_platform_id" if "experimental_platform_id" in frame else "interval_id"
    composition = [{"platform_id": str(i), "count": len(b), "supported_duration_seconds": float(b.sample_dt_seconds.fillna(0).sum()),
                    "source": row_evidence(b)} for i, b in base.groupby(platform, sort=False)]
    values = sorted(base.chainage.dropna().unique()) if "chainage" in base else []
    return clean({"schema_version": VERSION, "response_id": response_id, "bounds_m": bounds,
                  "total_scope_count": len(frame), "legacy_performance_count": int(frame.performance_eligible.sum()),
                  "basic_count": len(base), "rpm_count": int(frame.rpm_response_eligible.sum()), "metrics": summaries,
                  "platform_composition": composition, "windows": windows,
                  "window_coverage": {g: coverage(frame, flag, windows[g], config["analysis"]["hard_gap_seconds"]) for g, flag in [("basic", "basic_response_eligible"), ("performance", "performance_eligible")]},
                  "time_scope": {"first": base.time.iloc[0] if len(base) else None, "last": base.time.iloc[-1] if len(base) else None,
                                 "elapsed_span_seconds": (base.time.iloc[-1]-base.time.iloc[0]).total_seconds() if len(base) else None,
                                 "supported_duration_seconds": float(base.sample_dt_seconds.fillna(0).sum()),
                                 "continuous": len(composition) == 1 and len(base) > 0 and pd.Series(base.index).diff().iloc[1:].eq(1).all()
                                 and base.time.diff().dt.total_seconds().dropna().le(config["analysis"]["hard_gap_seconds"]).all()},
                  "spatial_quality": {"unique_observed_chainages": len(values), "observed_chainages_m": values,
                                      "day_observed_minimum_positive_step_m": day_resolution,
                                      "sensor_resolution_independently_verified": False,
                                      "single_observed_position": len(values) <= 1, "shield_to_face_offset_verified": False},
                  "quality": quality, "limits": ["no_state_or_phase_reclassification", "no_unresolved_core_mapping_admission",
                    "base_still_requires_existing_advancing_state_so_missing_RPM_may_remain_indirectly_excluded",
                    "no_fault_or_geological_cause", "sample_support_not_continuous_excavation_time"]})


def response_facts(features, date, config):
    facts = []
    response_id, bounds = features["response_id"], features["bounds_m"]
    def add(kind, field, text, data, window=None, sources=None):
        identity = key("RF", response_id, kind, field, window and window["window_id"])
        facts.append({"fact_id": identity, "response_id": response_id, "date": date, "kind": kind, "metric": field,
                      "range_m": bounds, "time_range": [window["start_time"], window["end_time"]] if window else
                      [features["time_scope"]["first"], features["time_scope"]["last"]], "canonical_text": text,
                      "data": data, "source": sources or (window["source"] if window else features["metrics"][field]["source"]),
                      "limits": ["nominal_chainage_reference", "not_continuous_unless_explicit_window", "not_causal_or_diagnostic"],
                      "comparison_object": None, "comparison_selection_reason": "same_contiguous_window_first_and_last_quarter" if window else None})
        return facts[-1]
    for field, s in features["metrics"].items():
        if s["status"] == "usable_descriptive":
            add("segment_parameter_level", field, f"该区段{s['count']}条合格记录中，{NAMES[field]}中位数为{s['median']:.2f}{s['unit']}。", s)
    for group, windows in features["windows"].items():
        for window in windows:
            stamp = window["start_time"][11:19]+"至"+window["end_time"][11:19]
            for field, change in window["parameter_changes"].items():
                unit = config["units"][field]
                if change["trend"] in {"increasing", "decreasing"}:
                    text = f"{stamp}的连续稳定推进窗口内，{NAMES[field]}整体{'上升' if change['trend']=='increasing' else '下降'}，首末四分之一记录中位数由{change['initial_median']:.2f}变为{change['final_median']:.2f}{unit}。"
                    add("within_window_trend", field, text, {**change, "unit": unit, "sample_count": window["sample_count"],
                        "supported_duration_seconds": window["supported_duration_seconds"], "platform_id": window["platform_id"]}, window)
                elif change["fluctuation_level"] == "low":
                    add("within_window_no_directional_trend_low_fluctuation", field,
                        f"{stamp}的窗口内，{NAMES[field]}未达到明确升降趋势判据，去趋势波动标签为低；不据此判断设备正常。", change, window)
            if group == "basic":
                add("within_window_torque_fluctuation", "torque", f"{stamp}的窗口内，刀盘扭矩变异系数为{window['torque_cv']:.4f}，仅描述该窗口的相对波动。",
                    {"torque_cv": window["torque_cv"], "sample_count": window["sample_count"], "supported_duration_seconds": window["supported_duration_seconds"]}, window)
        if group != "basic":
            continue
        for previous, current in zip(windows, windows[1:]):
            if previous["platform_id"] != current["platform_id"] or previous["phase_id"] != current["phase_id"]:
                continue
            before_rows, now_rows = previous["source"], current["source"]
            if len(before_rows) != 1 or len(now_rows) != 1 or before_rows[0]["source_file"] != now_rows[0]["source_file"]:
                continue
            if now_rows[0]["source_rows"][0] != before_rows[0]["source_rows"][-1]+1:
                continue
            if (pd.Timestamp(current["start_time"])-pd.Timestamp(previous["end_time"])).total_seconds() > config["analysis"]["hard_gap_seconds"]:
                continue
            a, b = previous["torque_cv"], current["torque_cv"]
            if a is None or b is None or a == b:
                continue
            row = add("adjacent_window_torque_cv_change", "torque", f"同一运行过程相邻窗口的刀盘扭矩变异系数由{a:.4f}变为{b:.4f}，相对波动{'增大' if b>a else '减小'}，不等于设备异常。",
                      {"previous_cv": a, "current_cv": b, "difference": b-a, "previous_window_id": previous["window_id"], "current_window_id": current["window_id"]},
                      current, previous["source"]+current["source"])
            row["time_range"][0] = previous["start_time"]
            row["comparison_object"] = previous["window_id"]
            row["comparison_selection_reason"] = "adjacent_same_platform_phase_and_source_rows_no_new_magnitude_threshold"
    return clean(facts)


def compare_responses(current, reference, config, policy):
    if reference is None:
        return {"descriptive": {"status": "insufficient_data", "reason": "no_preceding_response"},
                "controlled": {"status": "not_established", "reasons": ["no_preceding_response"]}}
    changes = {}
    for field in BASE+DEPENDENT:
        a, b = current["metrics"][field], reference["metrics"][field]
        if a["status"] == "insufficient" or b["status"] == "insufficient":
            continue
        if not a["source"] or not b["source"] or max(r["end_time"] for r in b["source"]) > min(r["start_time"] for r in a["source"]):
            continue
        now, before = a["median"], b["median"]
        changes[field] = {"current": now, "reference": before, "difference": now-before,
                          "percent": 100*(now-before)/before if abs(before)>1e-9 else None,
                          "unit": a["unit"], "current_count": a["count"], "reference_count": b["count"],
                          "current_support_seconds": a["supported_duration_seconds"], "reference_support_seconds": b["supported_duration_seconds"],
                          "source": {"current": a["source"], "reference": b["source"]}}
    diagnostics = {"reference_response_id": reference["response_id"], "current_response_id": current["response_id"],
                   "selection_reason": "preceding_qualified_geological_partition_in_same_day_not_best_matching_future_data",
                   "platform_composition": {"current": current["platform_composition"], "reference": reference["platform_composition"]},
                   "distribution_quantiles": {f: {"current": {k: current["metrics"][f][k] for k in ("p10","median","p90")},
                                                   "reference": {k: reference["metrics"][f][k] for k in ("p10","median","p90")}}
                                               for f in ("rpm_canonical","thrust","torque")},
                   "spatial_quality": {"current": current["spatial_quality"], "reference": reference["spatial_quality"]}}
    gates = {}
    for f, name in [("rpm_canonical", "comparison_rpm_relative_tolerance"), ("thrust", "comparison_thrust_relative_tolerance")]:
        value = changes.get(f, {}).get("percent")
        gates[f] = {"relative_difference": value/100 if value is not None else None,
                    "tolerance": config["analysis"][name], "passed": value is not None and abs(value)/100 <= config["analysis"][name]}
    required = policy["controlled_comparison"]["required_control_fields"]
    verified = policy["controlled_comparison"]["verified_control_fields"]
    reasons = ["unverified_control_field:"+f for f in required if f not in verified]
    # A median gate is only a diagnostic, not proof of identical settings or loading.
    controlled = {"status": "not_established", "reasons": reasons or ["exact_verified_control_matching_not_available_in_summary"],
                  "legacy_median_gates": gates, "diagnostics": diagnostics,
                  "target_specific_policy": {"thrust_or_torque_description": "never_suppressed_by_thrust_proximity_gate",
                                             "controlled_performance": "requires_verified_control_matching_not_merely_median_gates"}}
    sensitivity = []
    for factor in policy["sensitivity_tolerance_factors"]:
        sensitivity.append({"factor": factor, "legacy_median_gates_passed": all(g["relative_difference"] is not None and abs(g["relative_difference"]) <= factor*g["tolerance"] for g in gates.values()),
                            "change_fields": [f for f, c in changes.items() if c["percent"] is not None and abs(c["percent"])/100 >= factor*config["analysis"]["notable_relative_change"]]})
    return clean({"descriptive": {"status": "available" if changes else "insufficient_data", "changes": changes,
                                   "reference_response_id": reference["response_id"], "limits": ["operating_conditions_may_differ", "not_efficiency_ranking_or_geological_cause"]},
                  "controlled": controlled, "sensitivity": sensitivity})
