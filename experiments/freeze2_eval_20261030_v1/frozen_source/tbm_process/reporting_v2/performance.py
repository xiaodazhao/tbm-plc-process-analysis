from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tbm_process.dynamics.descriptors import trend_descriptor


METRIC_UNITS = {"pr": "mm/r", "fpi": "kN/(把·mm)", "se": "MJ/m³"}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def statistics(values: pd.Series) -> dict[str, Any]:
    valid = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return clean({"count": len(valid), "median": valid.median(), "p10": valid.quantile(.1),
                  "p90": valid.quantile(.9), "mean": valid.mean(), "minimum": valid.min(), "maximum": valid.max()})


def row_evidence(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Keep exact row membership, not just an envelope containing excluded rows."""
    if frame.empty:
        return []
    result = []
    for source, group in frame.groupby("source_file", sort=False):
        result.append({"source_file": str(source), "source_rows": group.source_row.astype(int).tolist(),
                       "start_time": group.time.iloc[0].isoformat(), "end_time": group.time.iloc[-1].isoformat()})
    return result


def calculate_metrics(frame: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = frame.copy()
    out["time"] = pd.to_datetime(out.time)
    missing_inputs = []
    for column in ["advance_speed", "thrust", "torque", "rpm_canonical", "advance_speed_quality", "rpm_quality"]:
        if column not in out:
            missing_inputs.append(column)
            out[column] = np.nan if not column.endswith("quality") else "missing"
    a, equipment = config["analysis"], config["equipment"]
    if equipment["diameter_m"] <= 0 or equipment["cutter_count"] <= 0:
        raise ValueError("Positive diameter and nominal cutter count are required")
    for column in ["advance_speed", "thrust", "torque", "rpm_canonical"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    stable_column = "experimental_phase_label" if "experimental_phase_label" in out else "phase_label"
    masks = {
        "stable_label": out[stable_column].eq("stable"),
        "advancing_state": out.machine_state.eq("advancing"),
        "speed_quality": out.advance_speed_quality.eq("usable"),
        "rpm_quality": out.rpm_quality.eq("usable_provisional_mapping"),
        "positive_motion": out.advance_speed.gt(a["minimum_speed_mm_min"]) & out.rpm_canonical.gt(a["minimum_rpm"]),
        "positive_loads": out.thrust.gt(0) & out.torque.gt(0),
        "finite_inputs": out[["advance_speed", "thrust", "torque", "rpm_canonical"]].notna().all(axis=1),
    }
    if "rpmsource" in out:
        masks["resolved_actual_rpm"] = ~out.rpmsource.fillna("unresolved").astype(str).str.contains("unresolved|unavailable", regex=True)
    if "mapping_core_positions_confirmed" in out:
        masks["resolved_core"] = out.mapping_core_positions_confirmed.astype(str).str.lower().eq("true")
    valid = pd.Series(True, index=out.index)
    for mask in masks.values():
        valid &= mask
    area = math.pi * float(equipment["diameter_m"]) ** 2 / 4
    pr = out.advance_speed / out.rpm_canonical.replace(0, np.nan)
    valid &= pr.ge(a["minimum_pr_mm_rev"])
    out["performance_eligible"] = valid
    out["pr"] = pr.where(valid)
    out["fpi"] = (out.thrust / (equipment["cutter_count"] * pr)).where(valid)
    # kN and kN*m become N and N*m; p is converted from mm to m.
    force_n, torque_nm = out.thrust * 1000, out.torque * 1000
    se_si = (force_n / area + 2 * math.pi * torque_nm / (area * pr / 1000)) / 1e6
    omega, speed_ms = out.rpm_canonical * 2 * math.pi / 60, out.advance_speed / 60000
    se_power = (torque_nm * omega + force_n * speed_ms) / (area * speed_ms.replace(0, np.nan)) / 1e6
    if valid.any() and not np.allclose(se_si[valid], se_power[valid], rtol=1e-10, atol=1e-10):
        raise ValueError("Mechanical-energy formulas disagree")
    out["se"] = se_si.where(valid)
    out["rotation_power_kw"] = (out.torque * omega).where(valid)
    out["thrust_reference_percent"] = (100 * out.thrust / equipment["reference_thrust_kn"]).where(valid)
    out["torque_rated_percent"] = (100 * out.torque / equipment["rated_torque_knm"]).where(valid)
    return out, clean({
        "scope": "existing_stable_label_intersect_existing_advancing_and_report_quality",
        "total_samples": len(out), "missing_required_input_columns": missing_inputs, "stable_label_samples": int(masks["stable_label"].sum()),
        "eligible_samples": int(valid.sum()), "independent_rejection_counts_not_additive": {k: int((~v).sum()) for k, v in masks.items()},
        "denominator_guard_rejections": int((pr.lt(a["minimum_pr_mm_rev"]) & masks["stable_label"]).sum()),
        "excluded_negative_thrust_count": int(out.thrust.lt(0).sum()), "area_m2": area,
        "formulae": {"pr": "v_mm_per_min / n_rev_per_min", "fpi": "F_kN / (70 * PR_mm_per_rev)",
                     "se": "(F_N/A_m2 + 2*pi*T_Nm/(A_m2*PR_m_per_rev))/1e6"},
        "formula_equivalence_checked": True, "units": config["units"],
        "unit_evidence": equipment["evidence"],
        "limitations": ["FPI uses PLC total thrust and installed cutter count, not measured individual cutter forces",
                        "SE is apparent theoretical mechanical energy, not total electrical consumption",
                        "PR/FPI/SE share inputs and are not independent geological evidence"],
    })


def summarize_performance(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    good = frame.loc[frame.performance_eligible].copy()
    duration = float(pd.to_numeric(good.sample_dt_seconds, errors="coerce").fillna(0).sum())
    reliable = len(good) >= config["analysis"]["minimum_samples"] and duration >= config["analysis"]["minimum_duration_seconds"]
    fields = ["advance_speed", "thrust", "torque", "rpm_canonical", "pr", "fpi", "se"]
    return {"status": "usable_descriptive" if reliable else "insufficient",
            "sample_count": len(good), "supported_duration_seconds": duration,
            "statistics": {k: statistics(good[k]) for k in fields}, "source": row_evidence(good),
            "scope": "qualified_stable_advancing_samples; sample_quantiles_not_time_weighted"}


def performance_windows(frame: pd.DataFrame, config: dict[str, Any], phase_config: dict[str, Any], *,
                        eligibility_column: str = "performance_eligible",
                        parameters: tuple[str, ...] = ("advance_speed", "thrust", "torque", "pr"),
                        boundary_columns: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    a = config["analysis"]
    good = frame.loc[frame[eligibility_column]].copy()
    if good.empty:
        return []
    platform_col = "experimental_platform_id" if "experimental_platform_id" in good else "interval_id"
    phase_col = "experimental_phase_id" if "experimental_phase_id" in good else "phase_id"
    boundaries = pd.Series(False, index=good.index)
    for column in boundary_columns:
        boundaries |= good[column].ne(good[column].shift())
    breaks = (boundaries | good.time.diff().dt.total_seconds().gt(a["hard_gap_seconds"])
              | good[platform_col].ne(good[platform_col].shift())
              | good[phase_col].ne(good[phase_col].shift())
              | pd.Series(good.index, index=good.index).diff().ne(1)).cumsum()
    result = []
    for _, block in good.groupby(breaks, sort=False):
        buckets = ((block.time - block.time.iloc[0]).dt.total_seconds() // a["window_seconds"]).astype(int)
        for _, window in block.groupby(buckets, sort=False):
            elapsed = (window.time.iloc[-1] - window.time.iloc[0]).total_seconds()
            if len(window) < a["minimum_samples"] or elapsed < a["minimum_duration_seconds"]:
                continue
            desc = {k: trend_descriptor(window.time, window[k], phase_config) for k in parameters}
            mean = float(window.torque.mean())
            cv = float(window.torque.std(ddof=1) / mean) if mean > 1e-9 else None
            result.append(clean({"window_id": f"W{len(result)+1:04d}", "platform_id": str(window[platform_col].iloc[0]),
                                 "start_time": window.time.iloc[0], "end_time": window.time.iloc[-1],
                                 "elapsed_seconds": elapsed, "sample_count": len(window), "torque_cv": cv,
                                 "torque_cv_definition": "sample_std_ddof1 / positive_mean_same_contiguous_window",
                                 "parameter_changes": desc, "performance": summarize_performance(window, config),
                                 "source": row_evidence(window)}))
    return result


def compare_statistics(current: dict[str, Any], reference: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    if reference is None or current["status"] == "insufficient" or reference["status"] == "insufficient":
        return {"status": "insufficient_data", "reason": "no_sufficient_preceding_comparable_reference"}
    a = config["analysis"]
    for field, tolerance in [("rpm_canonical", a["comparison_rpm_relative_tolerance"]), ("thrust", a["comparison_thrust_relative_tolerance"])]:
        now, before = current["statistics"][field]["median"], reference["statistics"][field]["median"]
        if now is None or before is None or before <= 0 or abs(now / before - 1) > tolerance:
            return {"status": "insufficient_data", "reason": "control_or_loading_conditions_not_comparable", "field": field}
    changes = {}
    for field in ["advance_speed", "thrust", "torque", "pr", "fpi", "se"]:
        now, before = current["statistics"][field]["median"], reference["statistics"][field]["median"]
        if now is None or before is None or abs(before) <= 1e-9:
            continue
        changes[field] = {"current": now, "reference": before, "difference": now-before,
                          "percent": 100*(now-before)/before, "threshold_met": abs(now/before-1) >= a["notable_relative_change"]}
    return {"status": "observed_change" if any(x["threshold_met"] for x in changes.values()) else "criterion_not_met",
            "changes": changes, "criterion": "absolute_relative_difference_of_qualified_medians",
            "threshold": a["notable_relative_change"], "reference": reference,
            "interpretation": "descriptive_comparison_not_geological_causation_or_fault_diagnosis"}
