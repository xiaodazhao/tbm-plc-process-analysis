from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import format_chainage, overlap


def build_compact_geology_presentation(
    *, axis_model: dict[str, Any], geology: dict[str, Any]
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    segments = axis_model.get("segments", [])
    by_source: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        by_source[segment["source_type"]].append(segment)

    comparisons: list[dict[str, Any]] = []
    seen_comparisons: set[tuple[float, float, str, str]] = set()
    for tsp in by_source.get("tsp", []):
        for hsp in by_source.get("hsp", []):
            start = max(float(tsp["start_m"]), float(hsp["start_m"]))
            end = min(float(tsp["end_m"]), float(hsp["end_m"]))
            if not overlap(tsp["start_m"], tsp["end_m"], hsp["start_m"], hsp["end_m"]):
                continue
            if tsp["grade"] == hsp["grade"]:
                continue
            key = (start, end, tsp["source_fact_id"], hsp["source_fact_id"])
            if key in seen_comparisons:
                continue
            seen_comparisons.add(key)
            comparisons.append(
                {
                    "start_m": start,
                    "end_m": end,
                    "tsp_grade": tsp["grade"],
                    "hsp_grade": hsp["grade"],
                    "source_refs": [tsp["source_ref"], hsp["source_ref"]],
                    "content_unit_ids": list(dict.fromkeys(tsp["content_unit_ids"] + hsp["content_unit_ids"])),
                }
            )

    for comparison in _merge_comparisons(comparisons):
        items.append(
            {
                "item_id": f"grade_difference::{len(items) + 1:03d}",
                "category": "prediction_difference",
                "chainage": f"{format_chainage(comparison['start_m'])}～{format_chainage(comparison['end_m'])}",
                "text": (
                    f"TSP建议{comparison['tsp_grade']}级，HSP建议{comparison['hsp_grade']}级；"
                    "两类预测并列保留，不作平均或取舍。"
                ),
                "source_refs": comparison["source_refs"],
                "content_unit_ids": comparison["content_unit_ids"],
            }
        )

    for segment in segments:
        salient = _salient_forecast(segment.get("forecast_conclusion", ""))
        if not salient:
            continue
        items.append(
            {
                "item_id": f"forecast_detail::{len(items) + 1:03d}",
                "category": "forecast_detail",
                "chainage": f"{segment['start_chainage']}～{segment['end_chainage']}",
                "text": f"{segment['source_type'].upper()}原报告预测：{salient}",
                "source_refs": [segment["source_ref"]],
                "content_unit_ids": segment["content_unit_ids"],
            }
        )

    for risk in _merge_risks(axis_model.get("risks", [])):
        geometry_text = (
            risk["start_chainage"]
            if risk["geometry"] == "point"
            else f"{risk['start_chainage']}～{risk['end_chainage']}"
        )
        items.append(
            {
                "item_id": f"risk::{len(items) + 1:03d}",
                "category": "forecast_risk",
                "chainage": geometry_text,
                "text": (
                    f"{risk['source_type'].upper()}原报告预测{risk['risk_type']}"
                    f"（{risk['geometry'] == 'point' and '点位' or '区间'}）；"
                    "仅作为预测提示，不写成已经发生。"
                ),
                "source_refs": risk.get("source_refs", [risk["source_ref"]]),
                "content_unit_ids": risk["content_unit_ids"],
            }
        )

    note_by_ref = {row["source_ref"]: row for row in geology.get("historical_observations", [])}
    for observation in axis_model.get("observations", []):
        note = note_by_ref.get(observation["source_ref"], {})
        summary = str(note.get("summary") or observation.get("summary") or "")
        summary = _compact(summary, 132)
        location = observation["chainage"] if observation.get("plottable") else "里程待核实"
        grade = observation.get("grade")
        grade_text = f"，原报告评估{grade}级" if grade and grade != "未知" else ""
        items.append(
            {
                "item_id": f"history::{len(items) + 1:03d}",
                "category": "historical_observation",
                "chainage": location,
                "text": (
                    f"{observation.get('observation_date') or '日期待核实'}历史素描{grade_text}："
                    f"{summary or '未形成可直接展示的综合描述。'}"
                    "该记录仅代表历史单点观察。"
                ),
                "source_refs": [observation["source_ref"]],
                "content_unit_ids": observation["content_unit_ids"],
            }
        )

    display_map: dict[str, dict[str, Any]] = {}
    for segment in axis_model.get("segments", []):
        for unit_id in segment["content_unit_ids"]:
            _register(display_map, unit_id, "axis_forecast_segment", segment["axis_id"])
    for risk in axis_model.get("risks", []):
        for unit_id in risk["content_unit_ids"]:
            _register(display_map, unit_id, "axis_risk", risk["axis_id"])
    for observation in axis_model.get("observations", []):
        component = "axis_historical_point" if observation.get("plottable") else "key_information"
        for unit_id in observation["content_unit_ids"]:
            _register(display_map, unit_id, component, observation["axis_id"])
    for item in items:
        for unit_id in item["content_unit_ids"]:
            _register(display_map, unit_id, "key_information", item["item_id"])

    selected = list(axis_model.get("selected_content_unit_ids", []))
    missing = sorted(set(selected) - set(display_map))
    return {
        "schema_version": "tbm_compact_geology_presentation_v1",
        "date": axis_model["date"],
        "key_information": items,
        "source_references": geology.get("source_references", []),
        "review_items": geology.get("review_items", []),
        "content_unit_display_map": display_map,
        "selected_content_unit_ids": selected,
        "required_content_unit_ids": sorted(
            set(selected) - set(geology.get("unrendered_required_content_unit_ids", []))
        ),
        "unmapped_selected_content_unit_ids": missing,
        "presentation_limits": [
            "TSP/HSP为预测，不改写为实际揭露。",
            "历史素描按原记录位置作为单点背景，不向相邻区间外推。",
            "导向盾首当日范围不等同于已确认掌子面范围。",
        ],
    }


def _register(
    mapping: dict[str, dict[str, Any]], unit_id: str, component: str, display_id: str
) -> None:
    row = mapping.setdefault(unit_id, {"components": [], "display_ids": []})
    if component not in row["components"]:
        row["components"].append(component)
    if display_id not in row["display_ids"]:
        row["display_ids"].append(display_id)


def _compact(text: str, limit: int) -> str:
    value = "".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _merge_comparisons(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda value: (value["start_m"], value["end_m"])):
        if (
            merged
            and abs(merged[-1]["end_m"] - row["start_m"]) < 1e-9
            and merged[-1]["tsp_grade"] == row["tsp_grade"]
            and merged[-1]["hsp_grade"] == row["hsp_grade"]
            and merged[-1]["source_refs"] == row["source_refs"]
        ):
            merged[-1]["end_m"] = row["end_m"]
            merged[-1]["content_unit_ids"] = list(
                dict.fromkeys(merged[-1]["content_unit_ids"] + row["content_unit_ids"])
            )
        else:
            merged.append(dict(row))
    return merged


def _salient_forecast(text: str) -> str:
    value = "".join(str(text or "").split())
    if not value:
        return ""
    clauses = []
    for clause in value.replace("；", "。").split("。"):
        if any(keyword in clause for keyword in ("断层破碎带", "出水", "涌水", "渗水")):
            if clause and clause not in clauses:
                clauses.append(clause)
    return _compact("；".join(clauses[:2]), 105)


def _merge_risks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row["source_type"],
            row["geometry"],
            row["risk_type"],
            row["start_m"],
            row["end_m"],
        )
        if key not in grouped:
            grouped[key] = dict(row)
            grouped[key]["source_refs"] = [row["source_ref"]]
            continue
        merged = grouped[key]
        merged["source_refs"] = list(dict.fromkeys(merged["source_refs"] + [row["source_ref"]]))
        merged["content_unit_ids"] = list(
            dict.fromkeys(merged["content_unit_ids"] + row["content_unit_ids"])
        )
    return sorted(grouped.values(), key=lambda value: (value["start_m"], value["source_type"]))
