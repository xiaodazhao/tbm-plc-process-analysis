from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import clock, read_json


PARAMETERS = {
    "advance_speed": {
        "display_name": "推进速度",
        "raw_field": "推进速度",
        "quality_column": "advance_speed_quality",
        "allowed_quality": {"usable"},
    },
    "thrust": {
        "display_name": "总推力",
        "raw_field": "推力",
    },
    "torque": {
        "display_name": "刀盘扭矩",
        "raw_field": "刀盘扭矩",
    },
    "rpm_canonical": {
        "display_name": "刀盘转速",
        "raw_field": "暂定统一刀盘转速字段",
        "quality_column": "rpm_quality",
        "allowed_quality": {"usable_provisional_mapping"},
    },
}

TREND_CN = {"increasing": "上升", "decreasing": "下降", "stable": "总体稳定"}
PARAMETER_SHORT_CN = {
    "advance_speed": "推进速度",
    "thrust": "总推力",
    "torque": "刀盘扭矩",
    "rpm": "刀盘转速",
    "rpm_canonical": "刀盘转速",
    "cutterhead_rpm": "刀盘转速",
    "penetration": "贯入度",
}


def configure_plc_import(plc_project: Path) -> None:
    value = str(plc_project.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)


def build_front_facts(
    date: str, *, plc_project: Path, analysis_dir: Path
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    configure_plc_import(plc_project)
    from tbm_process.reporting.front_sections import (
        ReportSources,
        build_front_sections_facts,
        load_report_config,
        validate_front_sections_facts,
    )

    report_config = load_report_config(
        plc_project / "configs" / "daily_report_front_sections.json"
    )
    sources = ReportSources(
        samples=analysis_dir / "processed_samples" / f"{date}.csv",
        platforms=analysis_dir / "tables" / "实验平台明细.csv",
        phases=analysis_dir / "tables" / "实验四阶段明细.csv",
    )
    facts, samples = build_front_sections_facts(
        date=date,
        sources=sources,
        config=report_config,
    )
    validation = validate_front_sections_facts(facts, samples, report_config)
    return facts, samples, {"config": report_config, "validation": validation}


def enrich_plc_facts(
    facts: dict[str, Any],
    samples: pd.DataFrame,
    *,
    structured_day_path: Path,
) -> dict[str, Any]:
    structured = read_json(structured_day_path)
    facts["presentation_layer"] = {
        "version": "tbm_daily_report_presentation_v1_2026-10-19",
        "frozen_plc_algorithms_modified": False,
        "llm_invoked": False,
        "parameter_scope": (
            "non-stopped processed samples assigned to the existing platform-first "
            "operating periods"
        ),
    }
    scope = samples.loc[
        samples["experimental_platform_id"].notna()
        & ~samples["machine_state"].astype(str).eq("stopped")
    ].copy()
    facts["daily_parameter_statistics"] = build_parameter_statistics(scope)
    all_events = structured.get("construction_response_events", [])
    selected_changes = select_major_changes(all_events, samples, maximum=3)
    facts["major_construction_changes"] = selected_changes
    facts["construction_change_selection"] = {
        "source_event_count": len(all_events),
        "selected_count": len(selected_changes),
        "selection_rule": (
            "rank existing frozen construction-response events; prefer stable or "
            "unsegmented scopes, multi-parameter changes and higher existing importance; "
            "retain at most one event per operating period and at most three per day"
        ),
        "event_detection_recomputed": False,
    }
    improve_interval_descriptions(facts, all_events)
    return facts


def build_parameter_statistics(scope: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scope_name": "已识别运行时段内的非停止记录",
        "scope_sample_count": int(len(scope)),
        "zero_values_from_stopped_state_excluded": True,
        "unit_note": "原始PLC记录值，工程单位待点位表核实",
        "parameters": {},
    }
    for column, metadata in PARAMETERS.items():
        values = pd.to_numeric(scope[column], errors="coerce")
        quality_column = metadata.get("quality_column")
        if quality_column and quality_column in scope:
            values = values.where(scope[quality_column].isin(metadata["allowed_quality"]))
        values = values.replace([np.inf, -np.inf], np.nan)
        valid = values.dropna()
        denominator = len(scope)
        result["parameters"][column] = {
            "display_name": metadata["display_name"],
            "source_field": metadata["raw_field"],
            "value_label": "原始PLC记录值",
            "engineering_unit": None,
            "unit_status": "unconfirmed",
            "scope_sample_count": int(denominator),
            "valid_sample_count": int(len(valid)),
            "valid_ratio": float(len(valid) / denominator) if denominator else None,
            "mean": _number(valid.mean()) if len(valid) else None,
            "median": _number(valid.median()) if len(valid) else None,
            "p10": _number(valid.quantile(0.1)) if len(valid) else None,
            "p90": _number(valid.quantile(0.9)) if len(valid) else None,
        }
    return result


def select_major_changes(
    events: list[dict[str, Any]], samples: pd.DataFrame, *, maximum: int
) -> list[dict[str, Any]]:
    eligible = [row for row in events if row.get("parameter_changes")]

    def rank(row: dict[str, Any]) -> tuple[float, float, float]:
        phase_bonus = {
            "stable": 2.0,
            "unsegmented_cycle": 1.0,
        }.get(str(row.get("phase_label")), 0.0)
        type_bonus = 1.0 if row.get("subtype") == "multi_parameter_change" else 0.0
        return (
            phase_bonus,
            type_bonus,
            float(row.get("importance_score") or 0.0),
        )

    chosen: list[dict[str, Any]] = []
    used_intervals: set[str] = set()
    for event in sorted(eligible, key=rank, reverse=True):
        interval_id = str(event.get("interval_id") or "")
        if interval_id in used_intervals:
            continue
        detail = _event_report_detail(event, samples)
        if not detail["parameter_changes"]:
            continue
        chosen.append(detail)
        used_intervals.add(interval_id)
        if len(chosen) >= maximum:
            break
    return sorted(chosen, key=lambda row: row["start_time"])


def improve_interval_descriptions(
    facts: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    by_interval: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_interval.setdefault(str(event.get("interval_id") or ""), []).append(event)
    for interval in facts["platforms"]:
        candidates = by_interval.get(interval["platform_id"], [])
        candidates.sort(key=lambda row: float(row.get("importance_score") or 0), reverse=True)
        description = _event_short_description(candidates[0]) if candidates else None
        if description:
            interval["reader_description"] = description
            interval["description_provenance"] = "existing_frozen_dynamic_event"
            interval["description_evidence"] = [
                candidates[0].get("event_id"),
                candidates[0].get("subtype"),
            ]


def _event_short_description(event: dict[str, Any]) -> str | None:
    changes = event.get("parameter_changes", {})
    moving = [
        (PARAMETER_SHORT_CN.get(name, name), values.get("trend"))
        for name, values in changes.items()
        if values.get("trend") in {"increasing", "decreasing"}
    ]
    if len(moving) >= 2 and len({trend for _, trend in moving}) == 1:
        return f"{''.join(name + '、' for name, _ in moving).rstrip('、')}同步{TREND_CN[moving[0][1]]}"
    if len(moving) >= 2:
        return "，".join(f"{name}{TREND_CN[trend]}" for name, trend in moving)
    if len(moving) == 1:
        return f"{moving[0][0]}持续{TREND_CN[moving[0][1]]}"
    fluctuating = [
        PARAMETER_SHORT_CN.get(name, name)
        for name, values in changes.items()
        if values.get("persistent_fluctuation")
    ]
    if len(fluctuating) >= 2:
        return "主要参数持续波动"
    return None


def _event_report_detail(
    event: dict[str, Any], samples: pd.DataFrame
) -> dict[str, Any]:
    block = samples.copy()
    if event.get("source_file"):
        block = block.loc[block["source_file"].eq(event["source_file"])]
    if event.get("source_row_start") is not None:
        block = block.loc[block["source_row"].ge(int(event["source_row_start"]))]
    if event.get("source_row_end") is not None:
        block = block.loc[block["source_row"].le(int(event["source_row_end"]))]
    start = pd.Timestamp(event["start_time"])
    end = pd.Timestamp(event["end_time"])
    block = block.loc[pd.to_datetime(block["time"]).between(start, end)].copy()
    rows: list[dict[str, Any]] = []
    for parameter, state in event.get("parameter_changes", {}).items():
        if parameter not in {"advance_speed", "thrust", "torque"}:
            continue
        trend = state.get("trend")
        if trend not in {"increasing", "decreasing"} and not state.get(
            "persistent_fluctuation"
        ):
            continue
        values = pd.to_numeric(block[parameter], errors="coerce")
        if parameter == "advance_speed" and "advance_speed_quality" in block:
            values = values.where(block["advance_speed_quality"].eq("usable"))
        valid = values.replace([np.inf, -np.inf], np.nan).dropna()
        if len(valid) < 4:
            continue
        quarter = max(1, int(math.ceil(len(valid) / 4)))
        initial = float(valid.iloc[:quarter].median())
        final = float(valid.iloc[-quarter:].median())
        rows.append(
            {
                "parameter": parameter,
                "display_name": PARAMETER_SHORT_CN[parameter],
                "trend": trend,
                "trend_display": TREND_CN.get(trend, "波动"),
                "persistent_fluctuation": bool(state.get("persistent_fluctuation")),
                "initial_median": initial,
                "final_median": final,
                "absolute_change": final - initial,
                "value_label": "原始PLC记录值",
                "calculation": "first_quarter_median_to_last_quarter_median",
                "valid_sample_count": int(len(valid)),
            }
        )
    return {
        "event_id": event.get("event_id"),
        "interval_id": event.get("interval_id"),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "time_display": f"{clock(event['start_time'])}～{clock(event['end_time'])}",
        "duration_seconds": float(event.get("duration_seconds") or 0.0),
        "subtype": event.get("subtype"),
        "phase_label": event.get("phase_label"),
        "importance_score": float(event.get("importance_score") or 0.0),
        "parameter_changes": rows,
        "interpretation_limit": "observed_machine_response_not_geology_or_fault_diagnosis",
        "source_file": event.get("source_file"),
        "source_row_start": event.get("source_row_start"),
        "source_row_end": event.get("source_row_end"),
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
