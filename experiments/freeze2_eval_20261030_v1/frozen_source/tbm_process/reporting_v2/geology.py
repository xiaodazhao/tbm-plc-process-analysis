from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path
import re
from typing import Any

import pandas as pd

from .performance import compare_statistics, row_evidence, summarize_performance


def availability(metadata: dict[str, Any], report_day: str, source_type: str) -> dict[str, Any]:
    if metadata.get("date_conflicts"):
        return {"status": "review", "reason": "document_date_conflict"}
    day = date.fromisoformat(report_day)
    field = "observation_date" if source_type == "sketch" else "submission_date"
    basis = metadata.get(field) or (metadata.get("cover_date") if source_type != "sketch" else None)
    explicit = metadata.get("available_at")
    if not basis:
        return {"status": "review", "reason": "missing_document_date_not_inferred_from_filename"}
    try:
        document_day = date.fromisoformat(str(basis))
    except ValueError:
        return {"status": "review", "reason": "invalid_document_date"}
    if document_day > day:
        return {"status": "excluded", "reason": "future_document", "document_date": str(basis)}
    if explicit:
        stamp = pd.Timestamp(explicit)
        if stamp.tzinfo is None or not metadata.get("actual_distribution_time_verified"):
            return {"status": "review", "reason": "unverified_availability_timestamp", "document_date": str(basis)}
        stamp = stamp.tz_convert("Asia/Shanghai")
        end = pd.Timestamp(day + timedelta(days=1), tz="Asia/Shanghai")
        if stamp >= end or stamp.date() < document_day:
            return {"status": "excluded", "reason": "unavailable_by_report_end_or_inconsistent_date"}
        return {"status": "eligible", "document_date": str(basis), "available_at": stamp.isoformat(),
                "role": "same_day_new" if stamp.date() == day else "prior_document", "basis_field": field,
                "distribution_time_verified": True}
    if document_day == day:
        return {"status": "review", "reason": "same_day_availability_time_unknown", "document_date": str(basis)}
    return {"status": "eligible", "document_date": str(basis), "available_at": str(basis)+"T00:00:00+08:00",
            "role": "prior_document", "basis_field": field if metadata.get(field) else "cover_date",
            "distribution_time_verified": False, "assumption": "date_only_prior_day_policy_not_verified_receipt_time"}


def prefix_compatible(prefix: str, text: str = "") -> bool:
    if prefix.lower() != "dyk":
        return False
    found = re.findall(r"\b(DyK|DK)\s*\d+\s*\+", text, re.I)
    return all(x.lower() == "dyk" for x in found)


def forward_bounds(end_m: float, direction: int | None, metres: float) -> tuple[float, float] | None:
    if direction not in (-1, 1):
        return None
    if metres <= 0:
        raise ValueError("Ahead window must be positive")
    return tuple(sorted((float(end_m), float(end_m) + direction * metres)))


def partition_intervals(records: list[dict[str, Any]], lower: float, upper: float) -> list[dict[str, Any]]:
    if upper < lower:
        raise ValueError("Partition bounds must be ordered")
    if upper == lower:
        return []
    boundaries = {lower, upper}
    for row in records:
        lo, hi = max(lower, row["start_m"]), min(upper, row["end_m"])
        if lo < hi:
            boundaries.update((lo, hi))
    edges = sorted(boundaries)
    result = []
    for lo, hi in zip(edges, edges[1:]):
        active = [r for r in records if r["start_m"] < hi and r["end_m"] > lo]
        result.append({"start_m": lo, "end_m": hi, "record_ids": [r["record_id"] for r in active],
                       "covered": bool(active), "comparisons": [compare_attributes(a, b) for a, b in combinations(active, 2)
                                                               if a["source"]["report_id"] != b["source"]["report_id"]]})
    return result


def attributes(text: str, grade: str | None = None) -> dict[str, Any]:
    text = re.sub(r"\s+", "", text)
    patterns = {
        "lithology": r"变质砂岩|砂质板岩|炭质板岩|板岩|片岩|花岗岩|灰岩|泥岩|砂岩",
        "integrity": r"破碎[-～~至]极破碎|极破碎|较破碎|岩体破碎|较完整|岩体完整|完整性差",
        "weathering": r"全风化|强风化|弱风化|微风化|未风化",
        "water": r"无地下水|未见(?:明显)?(?:出水|地下水)|无水|滴渗水|渗滴水|滴水|渗水|线[-～~至]股状出水|线状出水|股状出水|涌水|突水",
        "joints": r"节理裂隙发育密集|节理裂隙密集|节理裂隙发育|节理裂隙不发育|节理裂隙较发育",
        "special": r"断层破碎带|断层影响带|断层|掉块|变形|挤出",
    }
    out = {}
    for name, pattern in patterns.items():
        matches = list(re.finditer(pattern, text))
        values = []
        for m in matches:
            # Preserve negated statements; do not turn "no fault" into a fault assertion.
            before = text[max(0, m.start()-5):m.start()]
            value = ("未见/否定：" if re.search(r"无|未见|不存在|不发育", before) else "") + m.group(0)
            if value not in values:
                values.append(value)
        out[name] = {"values": values, "status": "literal_terms_only" if values else "not_stated_not_assumed_absent"}
    normalized_grade = str(grade).translate(str.maketrans({"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V", "Ⅵ": "VI"})).replace("级", "").strip() if grade else None
    out["grade"] = {"values": [normalized_grade] if normalized_grade else [], "original_value": grade,
                    "status": "reported_not_ground_truth" if grade else "not_stated"}
    return out


def compare_attributes(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    results = {}
    for key in ["grade", "lithology", "integrity", "weathering", "water", "joints", "special"]:
        left = a["attributes"].get(key, {}).get("values", [])
        right = b["attributes"].get(key, {}).get("values", [])
        if not left or not right:
            status = "source_only_or_unknown" if left or right else "not_comparable"
        else:
            status = "same_literal_attributes" if set(left) == set(right) else "different_descriptions"
        results[key] = {"left": left, "right": right, "status": status}
    differing = [k for k, v in results.items() if v["status"] == "different_descriptions"]
    comparable = [k for k, v in results.items() if v["status"] in {"different_descriptions", "same_literal_attributes"}]
    return {"left_id": a["record_id"], "right_id": b["record_id"], "attributes": results,
            "status": "explicit_reported_grade_difference" if "grade" in differing else
                      "description_difference_needs_semantic_review" if differing else
                      "partially_consistent" if comparable and len(comparable) < len(results) else
                      "consistent_in_compared_attributes" if comparable else "indeterminate",
            "semantic_verification": "REVIEW; literal comparison is not geological validation"}


def _source(metadata: dict[str, Any], row: dict[str, Any], sources: dict[str, Any], root: Path) -> dict[str, Any]:
    digest = metadata.get("source_sha256")
    located = sources.get(digest, {})
    candidates = [root / p for p in located.get("paths", []) if str(p).lower().endswith(".pdf")]
    path = next((p for p in candidates if p.is_file()), None)
    return {"report_id": metadata["source_report_id"], "source_type": metadata["source_type"],
            "original_filename": metadata["source_file"], "path": str(path) if path else None,
            "sha256": digest, "page": row.get("source_page", 1), "quote": row.get("source_quote", ""),
            "availability": None, "prefix": metadata.get("chainage_prefix"),
            "local_source_status": "located_by_existing_sha_index" if path else "original_pdf_not_located"}


def observation_clauses(raw: str) -> tuple[str, list[str]]:
    clauses = [s.strip() for s in re.split(r"[。；\n]", raw) if s.strip()]
    kept, excluded = [], []
    mode = "observation"
    for clause in clauses:
        if re.search(r"推测|预测|预计", clause):
            mode = "forecast"
            excluded.append(clause)
            continue
        if re.search(r"当前开挖|现场(?:实际)?|实际揭露|洞身段出露", clause):
            mode = "observation"
        if re.search(r"^建议[:：]?", clause):
            mode = "recommendation"
        if mode != "observation" or re.search(r"建议|风险|可能", clause):
            excluded.append(clause)
        else:
            kept.append(clause)
    return "。".join(kept), excluded


def build_geology(catalog: dict[str, Any], registry: dict[str, Any], root: Path, report_day: str,
                  start_m: float, end_m: float, direction: int | None, config: dict[str, Any]) -> dict[str, Any]:
    source_index = {s["sha256"]: s for s in registry.get("source_registry", [])}
    forecasts, risks, observations, excluded, pending = [], [], [], [], []
    source_ids = set()
    for report in catalog["reports"]:
        m = report["report_metadata"]
        kind = m["source_type"]
        identity = (m.get("source_sha256"), m["source_report_id"])
        if identity in source_ids:
            continue
        source_ids.add(identity)
        admissibility = availability(m, report_day, kind)
        if not prefix_compatible(m.get("chainage_prefix", "")) or m.get("alignment") != "right":
            admissibility = {"status": "review", "reason": "chainage_system_or_alignment_unverified"}
        source = _source(m, {}, source_index, root)
        source["availability"] = admissibility
        if admissibility["status"] != "eligible":
            item = {"report_id": m["source_report_id"], "source_type": kind, **admissibility,
                    "source": source, "chainage_m": m.get("coverage_start_m")}
            excluded.append(item)
            if kind == "sketch" and admissibility.get("reason") == "same_day_availability_time_unknown":
                pending.append(item)
            continue
        if kind in {"tsp", "hsp"}:
            for row in report.get("forecast_intervals", []):
                if row.get("start_m") is None or row.get("end_m") is None or row["start_m"] >= row["end_m"]:
                    excluded.append({"report_id": m["source_report_id"], "reason": "invalid_forecast_range"})
                    continue
                if not prefix_compatible(m["chainage_prefix"], row.get("start_chainage", "")+row.get("end_chainage", "")):
                    excluded.append({"report_id": m["source_report_id"], "reason": "DK_DyK_not_automatically_converted"})
                    continue
                original = _source(m, row, source_index, root)
                original["availability"] = admissibility
                text = row.get("forecast_conclusion", "")
                grade = row.get("recommended_grade_for_use") or row.get("recommended_grade_table")
                forecasts.append({"record_id": m["source_report_id"]+":"+row["interval_id"], "source_type": kind,
                                  "semantics": "forecast", "start_m": row["start_m"], "end_m": row["end_m"],
                                  "text": text, "attributes": attributes(text, grade), "source": original,
                                  "geophysical_text": row.get("geophysical_analysis") or row.get("geophysical_result"),
                                  "source_grade_conflict": row.get("grade_conflict", False)})
            for row in report.get("risk_records", []):
                risk = deepcopy(row)
                if risk.get("geometry") not in {"point", "interval"}:
                    excluded.append({"report_id": m["source_report_id"], "reason": "risk_geometry_unknown"})
                    continue
                risk["record_id"] = m["source_report_id"]+":"+row["risk_id"]
                risk["source"] = _source(m, row, source_index, root)
                risk["source"]["availability"] = admissibility
                risk["semantics"] = "forecast_risk_not_observed_incident"
                risks.append(risk)
        elif kind == "sketch" and report.get("observation"):
            row = report["observation"]
            text, omitted = observation_clauses(row.get("geological_description", ""))
            original = _source(m, row, source_index, root)
            original["availability"] = admissibility
            observations.append({"record_id": m["source_report_id"]+":"+row["observation_id"],
                                 "source_type": "sketch", "semantics": "observed_at_reported_point_not_entire_forward_range",
                                 "point_m": row["chainage_m"], "text": text,
                                 "original_geological_description": row.get("geological_description", ""),
                                 "text_extraction_status": "conservative_context_rule_not_expert_semantic_verification",
                                 "excluded_inference_or_recommendation_clauses": omitted,
                                 "attributes": attributes(text, row.get("table_suggested_grade")),
                                 "selected_table_fields": row.get("selected_fields", {}),
                                 "design_grade_as_recorded_in_sketch": row.get("design_grade"),
                                 "grade_conflict": row.get("grade_conflict", False), "source": original,
                                 "observation_date": admissibility["document_date"],
                                 "reporting_role": "same_day_observation" if admissibility["role"] == "same_day_new" else "historical_observation"})
    lo, hi = sorted((start_m, end_m))
    ahead = forward_bounds(end_m, direction, config["analysis"]["ahead_metres"])
    day_records = [r for r in forecasts if r["source"]["availability"]["role"] == "prior_document" and r["start_m"] <= hi and r["end_m"] >= lo]
    ahead_records = [r for r in forecasts if ahead and r["start_m"] < ahead[1] and r["end_m"] > ahead[0]]
    day_parts = partition_intervals(day_records, lo, hi)
    ahead_parts = partition_intervals(ahead_records, *ahead) if ahead else []
    selected_observations = sorted(observations, key=lambda o: (abs(o["point_m"]-end_m), -date.fromisoformat(o["observation_date"]).toordinal()))[:config["analysis"]["maximum_sketch_images"]]
    sketch_comparisons = []
    forecast_observation = []
    for obs in selected_observations:
        prior = [o for o in observations if o["observation_date"] < obs["observation_date"]]
        if prior:
            previous = min(prior, key=lambda o: (abs(o["point_m"]-obs["point_m"]), -date.fromisoformat(o["observation_date"]).toordinal()))
            sketch_comparisons.append({**compare_attributes(previous, obs), "scope": "different_observation_points_description_comparison_not_confirmed_change",
                                       "left": previous, "right": obs})
        relevant = [r for r in forecasts if r["start_m"] <= obs["point_m"] < r["end_m"]
                    and r["source"]["availability"]["document_date"] < obs["observation_date"]]
        for forecast in relevant:
            forecast_observation.append({**compare_attributes(forecast, obs), "scope": "nominal_point_comparison_offset_unknown_not_forecast_accuracy",
                                         "forecast": forecast, "observation": obs})
    def relevant_risks(bounds, ahead_scope=False):
        result = []
        if bounds is None:
            return result
        for r in risks:
            if not ahead_scope and r["source"]["availability"]["role"] != "prior_document":
                continue
            if r["geometry"] == "point":
                p = r.get("chainage_m", r.get("point_m"))
                if p is None or not bounds[0] <= p <= bounds[1]:
                    continue
                if ahead_scope and (direction is None or direction*(p-end_m) <= 0):
                    continue
                location = [p, p]
            else:
                if r.get("start_m") is None or r.get("end_m") is None or r["end_m"] <= r["start_m"]:
                    continue
                location = [max(bounds[0], r["start_m"]), min(bounds[1], r["end_m"])]
                if location[0] >= location[1]:
                    continue
            copy = deepcopy(r)
            copy["display_range_m"] = location
            copy["signed_distances_from_shield_end_m"] = [direction*(x-end_m) for x in location] if direction else None
            result.append(copy)
        return result
    design, design_rejected = [], []
    for record in config.get("design_records", []):
        # An interface is useful, but an OCR or filename is not a verified segment.
        if not record.get("verified") or not record.get("source_quote") or not record.get("source_sha256") or not record.get("source_page"):
            design_rejected.append({"record": record, "reason": "unverified_design_segment"})
            continue
        gate = availability({"submission_date": record.get("available_date"), "date_conflicts": record.get("date_conflicts", [])}, report_day, "design")
        if gate["status"] != "eligible" or not prefix_compatible(record.get("chainage_prefix", "")):
            design_rejected.append({"record": record, "reason": "design_time_or_chainage_unverified"})
        elif record["start_m"] < max(hi, ahead[1] if ahead else hi) and record["end_m"] > min(lo, ahead[0] if ahead else lo):
            from .facts import digest
            original = Path(record.get("source_file", ""))
            if not original.is_absolute():
                original = root/original
            if not original.is_file() or digest(original) != record["source_sha256"]:
                design_rejected.append({"record": record, "reason": "design_original_missing_or_hash_mismatch"})
                continue
            candidate = deepcopy(record)
            candidate["attributes"] = record.get("attributes") or attributes(record["source_quote"], record.get("reported_grade"))
            candidate["record_id"] = record["record_id"]
            candidate["source"] = {"report_id": record["record_id"], "source_type": "design", "original_filename": original.name,
                                   "path": str(original), "sha256": record["source_sha256"], "page": record["source_page"], "availability": gate}
            candidate["comparisons"] = [{**compare_attributes(candidate, f), "overlap_range_m": [max(candidate["start_m"],f["start_m"]),min(candidate["end_m"],f["end_m"])]}
                                        for f in day_records+ahead_records if candidate["start_m"]<f["end_m"] and candidate["end_m"]>f["start_m"]]
            design.append(candidate)
    return {"day_range_m": [lo, hi], "shield_end_m": end_m, "direction": direction,
            "ahead_range_m": list(ahead) if ahead else None, "ahead_reference": "guide_shield_not_confirmed_face",
            "forecast_records": {r["record_id"]: r for r in day_records+ahead_records},
            "day_partitions": day_parts, "ahead_partitions": ahead_parts,
            "day_risks": relevant_risks((lo, hi)), "ahead_risks": relevant_risks(ahead, True),
            "selected_observations": selected_observations, "same_day_pending_observations": pending,
            "observation_comparisons": sketch_comparisons, "forecast_observation_comparisons": forecast_observation,
            "design_records": design, "design_rejections": design_rejected,
            "design_status": "verified_segments_available" if design else "no_verified_segmented_design_supplied",
            "exclusions": excluded, "total_catalog_reports": len(catalog["reports"]),
            "coverage": {"day_length_m": hi-lo, "day_covered_m": sum(p["end_m"]-p["start_m"] for p in day_parts if p["covered"]),
                         "ahead_length_m": config["analysis"]["ahead_metres"] if ahead else None,
                         "ahead_covered_m": sum(p["end_m"]-p["start_m"] for p in ahead_parts if p["covered"])},
            "position_limits": ["shield_to_face_offset_unknown", "DyK_reference_only_no_DK_conversion", "no_causal_inference"]}


def joint_analysis(geology: dict[str, Any], frame: pd.DataFrame, config: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    parts = geology["day_partitions"]
    prior_summary = None
    prior_end = None
    order = sorted(parts, key=lambda p: p["start_m"], reverse=geology["direction"] == -1)
    for index, part in enumerate(order):
        in_range = frame.chainage.ge(part["start_m"]) & frame.chainage.lt(part["end_m"])
        if part["end_m"] == geology["day_range_m"][1]:
            in_range |= frame.chainage.eq(part["end_m"])
        scope = frame.loc[in_range].copy()
        if "chainage_quality" in scope:
            scope = scope.loc[scope.chainage_quality.fillna("").str.startswith("usable")]
        qualified = scope.loc[scope.performance_eligible]
        summary = summarize_performance(scope, config)
        reference = prior_summary if len(qualified) and prior_end and prior_end <= qualified.time.iloc[0] else None
        comparison = compare_statistics(summary, reference, config)
        result.append({"joint_id": f"J{index+1:03d}", "range_m": [part["start_m"], part["end_m"]],
                       "forecast_record_ids": part["record_ids"], "summary": summary,
                       "unique_observed_chainages": int(qualified.chainage.nunique()),
                       "response_comparison": comparison,
                       "interpretation": "nominal_spatial_co_location_only_offset_unknown",
                       "source": row_evidence(qualified)})
        if summary["status"] != "insufficient" and len(qualified):
            prior_summary, prior_end = summary, qualified.time.iloc[-1]
    return result
