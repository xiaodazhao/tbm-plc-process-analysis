from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import (
    format_chainage,
    normalize_text,
    overlap,
    parse_chainage,
    read_json,
    redact_suspicious_chainage_ranges,
)


DIFFERENCE_LABELS = {
    "grade": "围岩等级",
    "geophysics": "物探响应",
    "rock_mass": "岩体完整程度",
    "water": "水文描述",
    "geology": "地质描述",
}


def build_geology_presentation(
    *, package_path: Path, plan_path: Path
) -> dict[str, Any]:
    package = read_json(package_path)
    plan = read_json(plan_path)
    selected_ids = _selected_unit_ids(plan)
    unit_map = {str(row["unit_id"]): row for row in package["content_units"]}
    selected = [unit_map[unit_id] for unit_id in selected_ids if unit_id in unit_map]
    refs = _source_references(selected)
    forecast_units = [row for row in selected if row["unit_type"] == "forecast_segment_portion"]
    risk_units = [row for row in selected if row["unit_type"].startswith("forecast_risk_")]
    history_units = [row for row in selected if row["unit_type"].startswith("historical_")]
    rows, rendered_forecast_ids, rendered_risk_ids = _build_forecast_rows(
        package, forecast_units, risk_units, refs
    )
    history, rendered_history_ids, review_items = _build_historical_notes(
        history_units, refs
    )
    rendered = set(rendered_forecast_ids + rendered_risk_ids + rendered_history_ids)
    required = set(package.get("required_content_unit_ids", []))
    selected_set = set(selected_ids)
    unrendered_selected = sorted(selected_set - rendered)
    unrendered_required = sorted((required & selected_set) - rendered)
    return {
        "schema_version": "tbm_integrated_geology_table_v1",
        "date": package["date"],
        "method_version": package.get("method_version"),
        "source_package": str(package_path.resolve()),
        "source_plan": str(plan_path.resolve()),
        "plc_context": package["plc_context"],
        "selected_content_unit_ids": selected_ids,
        "rows": rows,
        "historical_observations": history,
        "source_references": refs,
        "rendered_content_unit_ids": sorted(rendered),
        "unrendered_selected_content_unit_ids": unrendered_selected,
        "unrendered_required_content_unit_ids": unrendered_required,
        "review_items": review_items,
        "constraints": package.get("generation_constraints", []),
        "presentation_rules": {
            "forecast_semantics": "prediction_only",
            "observation_semantics": "historical_single_location_context_only",
            "spatial_relation_basis": "daily_guide_shield_chainage",
            "guide_shield_equals_face_assumed": False,
            "source_fact_selection_changed": False,
        },
    }


def _selected_unit_ids(plan: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for section in plan.get("sections", []):
        for paragraph in section.get("paragraphs", []):
            values.extend(str(value) for value in paragraph.get("content_unit_ids", []))
    return list(dict.fromkeys(values))


def _source_references(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: defaultdict[str, int] = defaultdict(int)
    prefix = {"tsp": "T", "hsp": "H", "sketch": "S"}
    by_report: dict[str, dict[str, Any]] = {}
    for unit in units:
        source = unit.get("source", {})
        report_id = str(source.get("source_report_id") or source.get("source_file"))
        if report_id in by_report:
            continue
        source_type = str(source.get("source_type") or unit.get("source_type") or "source")
        counters[source_type] += 1
        evidence = unit.get("evidence", {})
        date_basis = evidence.get("report_date_basis", {})
        by_report[report_id] = {
            "source_ref": f"{prefix.get(source_type, 'R')}{counters[source_type]}",
            "source_report_id": report_id,
            "source_type": source_type,
            "source_file": source.get("source_file"),
            "source_page": source.get("source_page"),
            "source_section": source.get("source_section"),
            "source_sha256": source.get("source_sha256"),
            "availability_basis_field": date_basis.get("basis_field"),
            "availability_basis_date": date_basis.get("basis_date"),
        }
    return list(by_report.values())


def _build_forecast_rows(
    package: dict[str, Any],
    forecast_units: list[dict[str, Any]],
    risk_units: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    ref_by_report = {row["source_report_id"]: row["source_ref"] for row in references}
    usable: list[dict[str, Any]] = []
    boundaries: set[float] = set()
    for unit in forecast_units:
        evidence = unit.get("evidence", {})
        start = parse_chainage(evidence.get("applicable_start_chainage"))
        end = parse_chainage(evidence.get("applicable_end_chainage"))
        if start is None or end is None or end <= start:
            continue
        copy = dict(unit)
        copy["_start_m"] = start
        copy["_end_m"] = end
        usable.append(copy)
        boundaries.update((start, end))
    plc = package["plc_context"]
    day_start = float(plc["guide_shield_start_m"])
    day_end = float(plc["guide_shield_end_m"])
    boundaries.update((day_start, day_end))
    sorted_bounds = sorted(boundaries)
    rows: list[dict[str, Any]] = []
    rendered_forecast: set[str] = set()
    rendered_risks: set[str] = set()
    obligations = package.get("planning_obligations", [])
    for start, end in zip(sorted_bounds, sorted_bounds[1:]):
        active = [row for row in usable if overlap(start, end, row["_start_m"], row["_end_m"])]
        if not active:
            continue
        tsp = _forecast_cell(active, "tsp", ref_by_report)
        hsp = _forecast_cell(active, "hsp", ref_by_report)
        rendered_forecast.update(str(row["unit_id"]) for row in active)
        risk_texts, risk_ids = _risks_for_interval(
            risk_units, start, end, ref_by_report, is_last=end == sorted_bounds[-1]
        )
        rendered_risks.update(risk_ids)
        difference = _difference_text(active, obligations, start, end)
        combined = "；".join(value for value in [difference, *risk_texts] if value)
        rows.append(
            {
                "start_m": start,
                "end_m": end,
                "chainage_range": f"{format_chainage(start)}～{format_chainage(end)}",
                "position_relation": _position_relation(start, end, day_start, day_end),
                "tsp_forecast": tsp,
                "hsp_forecast": hsp,
                "differences_and_risks": combined or "未形成需单列的来源差异或风险提示。",
                "content_unit_ids": [str(row["unit_id"]) for row in active] + risk_ids,
            }
        )
    if not rows:
        rows.append(
            {
                "start_m": day_start,
                "end_m": day_end,
                "chainage_range": f"{format_chainage(day_start)}～{format_chainage(day_end)}",
                "position_relation": "当日经过范围（按导向盾首里程）",
                "tsp_forecast": "无符合本日时间与空间约束的TSP内容。",
                "hsp_forecast": "无符合本日时间与空间约束的HSP内容。",
                "differences_and_risks": "无可进行的多来源预测比较。",
                "content_unit_ids": [],
            }
        )
    # A point exactly on a partition boundary is assigned to the interval starting there.
    # Any residual point at the final boundary is attached to the last row.
    for unit in risk_units:
        unit_id = str(unit["unit_id"])
        if unit_id in rendered_risks:
            continue
        evidence = unit.get("evidence", {})
        point = parse_chainage(evidence.get("risk_chainage"))
        if point is None or not rows:
            continue
        if abs(point - rows[-1]["end_m"]) < 1e-9:
            text = _risk_text(unit, ref_by_report)
            rows[-1]["differences_and_risks"] += f"；{text}"
            rows[-1]["content_unit_ids"].append(unit_id)
            rendered_risks.add(unit_id)
    return rows, sorted(rendered_forecast), sorted(rendered_risks)


def _forecast_cell(
    active: list[dict[str, Any]], source_type: str, ref_by_report: dict[str, str]
) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for unit in active:
        if unit.get("source_type") != source_type:
            continue
        fact_id = str(unit.get("source_fact_id"))
        if fact_id in seen:
            continue
        seen.add(fact_id)
        evidence = unit.get("evidence", {})
        source = unit.get("source", {})
        ref = ref_by_report.get(str(source.get("source_report_id")), source_type.upper())
        conclusion = normalize_text(evidence.get("forecast_conclusion"))
        geophysics = normalize_text(evidence.get("geophysical_result"))
        grade = normalize_text(evidence.get("recommended_grade"))
        pieces = [f"[{ref}] {conclusion}" if conclusion else f"[{ref}] 预测描述未结构化"]
        if grade and f"{grade}级" not in conclusion:
            pieces.append(f"建议围岩等级{grade}级")
        if geophysics:
            pieces.append(f"物探结果：{geophysics}")
        parts.append("；".join(pieces))
    return "\n".join(parts) if parts else "—"


def _risks_for_interval(
    units: list[dict[str, Any]],
    start: float,
    end: float,
    ref_by_report: dict[str, str],
    *,
    is_last: bool,
) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    ids: list[str] = []
    for unit in units:
        evidence = unit.get("evidence", {})
        unit_type = unit.get("unit_type")
        matched = False
        if unit_type == "forecast_risk_point":
            point = parse_chainage(evidence.get("risk_chainage"))
            matched = point is not None and (start <= point < end or (is_last and point == end))
        elif unit_type == "forecast_risk_interval_portion":
            risk_start = parse_chainage(
                evidence.get("applicable_start_chainage") or evidence.get("original_start_chainage")
            )
            risk_end = parse_chainage(
                evidence.get("applicable_end_chainage") or evidence.get("original_end_chainage")
            )
            matched = (
                risk_start is not None
                and risk_end is not None
                and overlap(start, end, risk_start, risk_end)
            )
        if matched:
            texts.append(_risk_text(unit, ref_by_report))
            ids.append(str(unit["unit_id"]))
    return texts, ids


def _risk_text(unit: dict[str, Any], ref_by_report: dict[str, str]) -> str:
    evidence = unit.get("evidence", {})
    source = unit.get("source", {})
    ref = ref_by_report.get(str(source.get("source_report_id")), unit.get("source_type", "").upper())
    risk_type = normalize_text(evidence.get("risk_type")) or "风险提示"
    if unit.get("unit_type") == "forecast_risk_point":
        geometry = normalize_text(evidence.get("risk_chainage"))
        return f"[{ref}] 原报告预测在{geometry}附近存在{risk_type}。"
    start = normalize_text(
        evidence.get("original_start_chainage") or evidence.get("applicable_start_chainage")
    )
    end = normalize_text(
        evidence.get("original_end_chainage") or evidence.get("applicable_end_chainage")
    )
    return f"[{ref}] 原报告预测{start}～{end}为{risk_type}区间。"


def _difference_text(
    active: list[dict[str, Any]], obligations: list[dict[str, Any]], start: float, end: float
) -> str:
    source_types = {row.get("source_type") for row in active}
    if not {"tsp", "hsp"}.issubset(source_types):
        only = "TSP" if "tsp" in source_types else "HSP"
        return f"本里程段仅有{only}匹配内容，未作缺失来源的推断。"
    grades: dict[str, set[str]] = defaultdict(set)
    for row in active:
        grade = normalize_text(row.get("evidence", {}).get("recommended_grade"))
        if grade:
            grades[str(row.get("source_type"))].add(grade)
    parts: list[str] = []
    if grades.get("tsp") != grades.get("hsp"):
        parts.append(
            f"围岩等级意见不同（TSP：{'/'.join(sorted(grades.get('tsp', {'未给出'})))}；"
            f"HSP：{'/'.join(sorted(grades.get('hsp', {'未给出'})))}）"
        )
    categories: set[str] = set()
    for obligation in obligations:
        for difference in obligation.get("pairwise_differences", []):
            left = parse_chainage(difference.get("overlap_start_chainage"))
            right = parse_chainage(difference.get("overlap_end_chainage"))
            if left is None or right is None or not overlap(start, end, left, right):
                continue
            categories.update(difference.get("substantive_difference_categories", []))
    translated = [DIFFERENCE_LABELS[value] for value in sorted(categories) if value in DIFFERENCE_LABELS]
    if translated:
        parts.append(f"V2规划记录两类来源在{'、'.join(translated)}方面存在差异")
    if not parts:
        parts.append("TSP与HSP均有匹配内容，原始意见并列保留")
    return "；".join(parts) + "。"


def _position_relation(start: float, end: float, day_start: float, day_end: float) -> str:
    if end <= day_start:
        return "当日起点之前（按导向盾首里程）"
    if start >= day_end:
        return "当日终点之后（按导向盾首里程）"
    if start >= day_start and end <= day_end:
        return "当日经过范围（按导向盾首里程）"
    return "跨越当日边界（按导向盾首里程）"


def _build_historical_notes(
    units: list[dict[str, Any]], references: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    ref_by_report = {row["source_report_id"]: row["source_ref"] for row in references}
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        grouped[str(unit.get("source_fact_id"))].append(unit)
    notes: list[dict[str, Any]] = []
    rendered: list[str] = []
    review_items: list[dict[str, Any]] = []
    for source_fact_id, rows in grouped.items():
        summary = next(
            (row for row in rows if row.get("unit_type") == "historical_observation_summary"),
            None,
        )
        context = next(
            (row for row in rows if row.get("unit_type") == "historical_observation_context"),
            None,
        )
        if not summary and not context:
            continue
        unit = summary or context
        evidence = unit.get("evidence", {})
        source = unit.get("source", {})
        ref = ref_by_report.get(str(source.get("source_report_id")), "S")
        raw_summary = normalize_text(evidence.get("source_geological_summary"))
        safe_summary, findings = redact_suspicious_chainage_ranges(raw_summary)
        if findings:
            review_items.append(
                {
                    "type": "source_document_chainage_range_requires_review",
                    "source_fact_id": source_fact_id,
                    "source_file": source.get("source_file"),
                    "findings": findings,
                    "handling": (
                        "verified against original PDF; retained as source issue, exact range "
                        "redacted from report prose and excluded from spatial reasoning"
                    ),
                }
            )
        selected_fields = evidence.get("selected_observation_fields", {})
        water = evidence.get("water_entry", {})
        notes.append(
            {
                "source_ref": ref,
                "observation_date": evidence.get("observation_date")
                or (context or {}).get("evidence", {}).get("observation_date"),
                "observation_chainage": evidence.get("observation_chainage")
                or (context or {}).get("evidence", {}).get("observation_chainage"),
                "summary": safe_summary or "原素描未形成可直接展示的综合描述。",
                "selected_observation_fields": selected_fields,
                "water_entry": water,
                "source_assessed_grade": evidence.get("source_assessed_grade"),
                "semantics_limit": (
                    "历史单点观察，不代表当日全部施工范围的实际揭露，且不假设"
                    "导向盾首与掌子面具有固定偏移。"
                ),
                "content_unit_ids": [str(row["unit_id"]) for row in rows],
            }
        )
        rendered.extend(str(row["unit_id"]) for row in rows)
    return notes, rendered, review_items
