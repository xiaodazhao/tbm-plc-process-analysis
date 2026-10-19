from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator

from .common import format_chainage, normalize_text, parse_chainage, read_json


plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Heiti SC",
    "Arial Unicode MS",
    "Microsoft YaHei",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


GRADE_COLORS = {
    "Ⅰ": "#2f7d4a",
    "Ⅱ": "#67a95c",
    "Ⅲ": "#9ac66d",
    "Ⅳ": "#e2a94b",
    "Ⅴ": "#b44d43",
    "Ⅵ": "#7f2831",
    "未知": "#a8afb1",
}
RISK_STYLES = {
    "掉块风险": {"color": "#9d2d35", "marker": "^", "short": "掉块"},
    "溜坍风险": {"color": "#c96b2c", "marker": "v", "short": "溜坍"},
    "出水风险": {"color": "#277da1", "marker": "D", "short": "出水"},
}
SOURCE_PREFIX = {"tsp": "T", "hsp": "H", "sketch": "S"}


def build_geology_axis_model(
    *, package_path: Path, plan_path: Path, source_references: list[dict[str, Any]]
) -> dict[str, Any]:
    package = read_json(package_path)
    plan = read_json(plan_path)
    selected_ids = _selected_unit_ids(plan)
    unit_map = {str(row["unit_id"]): row for row in package.get("content_units", [])}
    selected = [unit_map[value] for value in selected_ids if value in unit_map]
    ref_by_report = {
        str(row["source_report_id"]): str(row["source_ref"])
        for row in source_references
    }

    segments: list[dict[str, Any]] = []
    seen_segments: set[tuple[str, str]] = set()
    for unit in selected:
        if unit.get("unit_type") != "forecast_segment_portion":
            continue
        source = unit.get("source", {})
        evidence = unit.get("evidence", {})
        source_type = str(unit.get("source_type") or source.get("source_type") or "")
        fact_id = str(unit.get("source_fact_id"))
        report_id = str(source.get("source_report_id") or source.get("source_file"))
        key = (fact_id, report_id)
        if key in seen_segments:
            continue
        seen_segments.add(key)
        start = parse_chainage(
            evidence.get("original_start_chainage")
            or evidence.get("applicable_start_chainage")
        )
        end = parse_chainage(
            evidence.get("original_end_chainage")
            or evidence.get("applicable_end_chainage")
        )
        if start is None or end is None or end <= start:
            continue
        grade = _normalize_grade(evidence.get("recommended_grade"))
        segments.append(
            {
                "axis_id": f"segment::{source_type}::{len(segments) + 1:03d}",
                "source_type": source_type,
                "source_ref": ref_by_report.get(report_id, source_type.upper()),
                "source_report_id": report_id,
                "source_fact_id": fact_id,
                "content_unit_ids": _unit_ids_for_fact(selected, fact_id, "forecast_segment_portion"),
                "start_m": start,
                "end_m": end,
                "start_chainage": format_chainage(start),
                "end_chainage": format_chainage(end),
                "grade": grade,
                "color": GRADE_COLORS[grade],
                "forecast_conclusion": normalize_text(evidence.get("forecast_conclusion")),
                "geophysical_result": normalize_text(evidence.get("geophysical_result")),
                "source_page": source.get("source_page"),
            }
        )

    risks: list[dict[str, Any]] = []
    seen_risks: set[tuple[str, str]] = set()
    for unit in selected:
        unit_type = str(unit.get("unit_type") or "")
        if not unit_type.startswith("forecast_risk_"):
            continue
        source = unit.get("source", {})
        evidence = unit.get("evidence", {})
        fact_id = str(unit.get("source_fact_id"))
        report_id = str(source.get("source_report_id") or source.get("source_file"))
        key = (fact_id, report_id)
        if key in seen_risks:
            continue
        seen_risks.add(key)
        geometry = "point" if unit_type == "forecast_risk_point" else "interval"
        point = parse_chainage(evidence.get("risk_chainage")) if geometry == "point" else None
        start = (
            parse_chainage(
                evidence.get("original_start_chainage")
                or evidence.get("applicable_start_chainage")
            )
            if geometry == "interval"
            else point
        )
        end = (
            parse_chainage(
                evidence.get("original_end_chainage")
                or evidence.get("applicable_end_chainage")
            )
            if geometry == "interval"
            else point
        )
        if start is None or end is None or (geometry == "interval" and end <= start):
            continue
        source_type = str(unit.get("source_type") or source.get("source_type") or "")
        risk_type = normalize_text(evidence.get("risk_type")) or "其他风险"
        style = RISK_STYLES.get(
            risk_type, {"color": "#6d597a", "marker": "P", "short": risk_type[:4]}
        )
        risks.append(
            {
                "axis_id": f"risk::{source_type}::{len(risks) + 1:03d}",
                "source_type": source_type,
                "source_ref": ref_by_report.get(report_id, source_type.upper()),
                "source_report_id": report_id,
                "source_fact_id": fact_id,
                "content_unit_ids": _unit_ids_for_fact(selected, fact_id, "forecast_risk_"),
                "geometry": geometry,
                "risk_type": risk_type,
                "risk_short": style["short"],
                "color": style["color"],
                "marker": style["marker"],
                "start_m": start,
                "end_m": end,
                "start_chainage": format_chainage(start),
                "end_chainage": format_chainage(end),
                "source_page": source.get("source_page"),
            }
        )

    observations: list[dict[str, Any]] = []
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in selected:
        if str(unit.get("unit_type") or "").startswith("historical_"):
            grouped[str(unit.get("source_fact_id"))].append(unit)
    for fact_id, units in grouped.items():
        primary = next(
            (row for row in units if row.get("unit_type") == "historical_observation_summary"),
            units[0],
        )
        evidence = primary.get("evidence", {})
        source = primary.get("source", {})
        chainage = parse_chainage(evidence.get("observation_chainage"))
        if chainage is None:
            for unit in units:
                chainage = parse_chainage(unit.get("evidence", {}).get("observation_chainage"))
                if chainage is not None:
                    break
        report_id = str(source.get("source_report_id") or source.get("source_file"))
        observations.append(
            {
                "axis_id": f"observation::{len(observations) + 1:03d}",
                "source_type": "sketch",
                "source_ref": ref_by_report.get(report_id, "S"),
                "source_report_id": report_id,
                "source_fact_id": fact_id,
                "content_unit_ids": [str(row["unit_id"]) for row in units],
                "chainage_m": chainage,
                "chainage": format_chainage(chainage),
                "observation_date": evidence.get("observation_date"),
                "grade": _normalize_grade(evidence.get("source_assessed_grade")),
                "summary": normalize_text(evidence.get("source_geological_summary")),
                "source_page": source.get("source_page"),
                "plottable": chainage is not None,
            }
        )

    plc = package["plc_context"]
    day_start = float(plc["guide_shield_start_m"])
    day_end = float(plc["guide_shield_end_m"])
    bounds = [day_start, day_end]
    bounds.extend(value for row in segments for value in (row["start_m"], row["end_m"]))
    bounds.extend(value for row in risks for value in (row["start_m"], row["end_m"]))
    bounds.extend(
        float(row["chainage_m"])
        for row in observations
        if row.get("chainage_m") is not None
    )
    return {
        "schema_version": "tbm_geology_axis_model_v1",
        "date": package["date"],
        "method_version": package.get("method_version"),
        "day_range": {
            "start_m": day_start,
            "end_m": day_end,
            "start_chainage": format_chainage(day_start),
            "end_chainage": format_chainage(day_end),
            "zero_advance": abs(day_end - day_start) < 1e-9,
        },
        "segments": sorted(segments, key=lambda row: (row["source_type"], row["start_m"])),
        "risks": sorted(risks, key=lambda row: (row["start_m"], row["source_type"])),
        "observations": sorted(
            observations,
            key=lambda row: (
                row["chainage_m"] is None,
                row["chainage_m"] if row["chainage_m"] is not None else 0.0,
            ),
        ),
        "data_bounds_m": {"minimum": min(bounds), "maximum": max(bounds)},
        "selected_content_unit_ids": selected_ids,
        "grade_colors": GRADE_COLORS,
        "risk_styles": RISK_STYLES,
        "semantics": {
            "forecast": "prediction_only",
            "observation": "historical_point_context_only",
            "day_range": "guide_shield_chainage_only",
        },
    }


def render_geology_axis(
    model: dict[str, Any], *, png_path: Path, pdf_path: Path, dpi: int = 220
) -> dict[str, Any]:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    minimum = float(model["data_bounds_m"]["minimum"])
    maximum = float(model["data_bounds_m"]["maximum"])
    span = max(1.0, maximum - minimum)
    padding = max(4.0, span * 0.04)
    x_min, x_max = minimum - padding, maximum + padding

    fig, ax = plt.subplots(figsize=(11.7, 4.35), constrained_layout=True)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0.15, 3.75)
    ax.set_yticks([3.05, 2.05, 1.05])
    ax.set_yticklabels(["TSP预测", "HSP预测", "历史素描"])
    ax.grid(axis="x", color="#dce2e3", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=9, steps=[1, 2, 5, 10]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: _compact_chainage(value, x_min)))
    ax.tick_params(axis="x", labelrotation=28, labelsize=8.5)
    ax.tick_params(axis="y", labelsize=9.2, length=0)
    ax.set_xlabel("里程", fontsize=9.5)
    ax.set_title(f"{model['date']} 地质信息里程轴", fontsize=12.5, pad=10)
    for y in (1.05, 2.05, 3.05):
        ax.axhline(y, color="#bbc4c6", linewidth=0.8, zorder=0)

    lane_centers = {"tsp": 3.05, "hsp": 2.05}
    segments = model["segments"]
    label_records: list[dict[str, Any]] = []
    for source_type in ("tsp", "hsp"):
        source_rows = [row for row in segments if row["source_type"] == source_type]
        lanes = _assign_lanes(source_rows)
        lane_count = max(lanes.values(), default=0) + 1
        for row in source_rows:
            lane = lanes[row["axis_id"]]
            y = lane_centers[source_type] + (lane - (lane_count - 1) / 2.0) * 0.20
            width = row["end_m"] - row["start_m"]
            rect = Rectangle(
                (row["start_m"], y - 0.18),
                width,
                0.36,
                facecolor=row["color"],
                edgecolor="white",
                linewidth=0.9,
                zorder=2,
            )
            ax.add_patch(rect)
            label = f"{row['source_ref']}·{row['grade']}"
            if width >= max(2.0, (x_max - x_min) * 0.028):
                artist = ax.text(
                    row["start_m"] + width / 2,
                    y,
                    label,
                    ha="center",
                    va="center",
                    fontsize=7.5,
                    color="white" if row["grade"] in {"Ⅰ", "Ⅴ", "Ⅵ"} else "#263238",
                    fontweight="bold",
                    clip_on=True,
                    zorder=4,
                )
                label_records.append({"id": row["axis_id"], "artist": artist, "kind": "inside"})
            else:
                artist = ax.text(
                    row["start_m"] + width / 2,
                    y + 0.25,
                    label,
                    ha="center",
                    va="bottom",
                    fontsize=7.0,
                    color="#3f4b4f",
                    clip_on=True,
                    zorder=4,
                )
                label_records.append({"id": row["axis_id"], "artist": artist, "kind": "outside"})

    risk_offsets: defaultdict[tuple[str, int], int] = defaultdict(int)
    for risk in model["risks"]:
        y = lane_centers.get(risk["source_type"], 2.55)
        if risk["geometry"] == "interval":
            ax.plot(
                [risk["start_m"], risk["end_m"]],
                [y - 0.30, y - 0.30],
                color=risk["color"],
                linewidth=3.5,
                solid_capstyle="butt",
                zorder=5,
            )
            mid = (risk["start_m"] + risk["end_m"]) / 2.0
            ax.text(mid, y - 0.43, risk["risk_short"], ha="center", va="top", fontsize=6.8, color=risk["color"])
        else:
            key = (risk["source_type"], round(risk["start_m"] * 10))
            offset = risk_offsets[key]
            risk_offsets[key] += 1
            marker_y = y - 0.30 - offset * 0.16
            ax.scatter(
                [risk["start_m"]],
                [marker_y],
                color=risk["color"],
                marker=risk["marker"],
                s=34,
                edgecolors="white",
                linewidths=0.5,
                zorder=6,
            )

    observation_offsets: defaultdict[int, int] = defaultdict(int)
    for observation in model["observations"]:
        chainage = observation.get("chainage_m")
        if chainage is None:
            continue
        key = round(float(chainage) * 10)
        offset = observation_offsets[key]
        observation_offsets[key] += 1
        y = 1.05 + offset * 0.18
        ax.scatter(
            [chainage], [y], marker="o", s=46, color="#4c6f82", edgecolors="white", linewidths=0.7, zorder=6
        )
        ax.text(
            chainage,
            y + 0.18,
            f"{observation['source_ref']}·{observation['grade']}",
            ha="center",
            va="bottom",
            fontsize=7.0,
            color="#36515f",
            zorder=7,
        )

    day = model["day_range"]
    start, end = float(day["start_m"]), float(day["end_m"])
    if day["zero_advance"]:
        ax.axvline(start, color="#126e82", linewidth=2.0, linestyle="--", zorder=7)
        ax.scatter([start], [0.43], marker="D", s=45, color="#126e82", zorder=8)
        ax.text(start, 3.55, "当日起止位置重合", ha="center", va="bottom", fontsize=7.5, color="#126e82")
    else:
        left, right = sorted((start, end))
        ax.axvspan(left, right, color="#74c0b2", alpha=0.13, zorder=1)
        ax.axvline(start, color="#217a5b", linewidth=1.8, linestyle="--", zorder=7)
        ax.axvline(end, color="#126e82", linewidth=1.8, linestyle="--", zorder=7)
        ax.text(start, 3.55, "当日起点", ha="right", va="bottom", fontsize=7.2, color="#217a5b")
        ax.text(end, 3.55, "当日终点", ha="left", va="bottom", fontsize=7.2, color="#126e82")

    legend_items = [
        Patch(facecolor=GRADE_COLORS[grade], edgecolor="none", label=f"{grade}级")
        for grade in ("Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "未知")
        if any(row["grade"] == grade for row in segments)
    ]
    legend_items.extend(
        [
            Line2D([0], [0], marker="^", color="none", markerfacecolor="#9d2d35", label="风险点", markersize=6),
            Line2D([0], [0], color="#9d2d35", linewidth=3.5, label="风险区间"),
            Line2D([0], [0], marker="o", color="none", markerfacecolor="#4c6f82", label="历史素描", markersize=6),
        ]
    )
    ax.legend(
        handles=legend_items,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=min(8, max(1, len(legend_items))),
        frameon=False,
        fontsize=7.3,
    )
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#8f9a9d")

    fig.canvas.draw()
    overlap_pairs = _label_overlaps(fig, [row["artist"] for row in label_records])
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return {
        "png_path": str(png_path.resolve()),
        "pdf_path": str(pdf_path.resolve()),
        "x_min_m": x_min,
        "x_max_m": x_max,
        "cropped": False,
        "label_overlap_pairs": overlap_pairs,
        "plotted_segment_count": len(segments),
        "plotted_risk_count": len(model["risks"]),
        "plotted_observation_count": sum(1 for row in model["observations"] if row["plottable"]),
    }


def _selected_unit_ids(plan: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for section in plan.get("sections", []):
        for paragraph in section.get("paragraphs", []):
            values.extend(str(value) for value in paragraph.get("content_unit_ids", []))
    return list(dict.fromkeys(values))


def _unit_ids_for_fact(
    units: list[dict[str, Any]], fact_id: str, unit_type_prefix: str
) -> list[str]:
    return [
        str(row["unit_id"])
        for row in units
        if str(row.get("source_fact_id")) == fact_id
        and str(row.get("unit_type") or "").startswith(unit_type_prefix)
    ]


def _normalize_grade(value: Any) -> str:
    text = normalize_text(value).replace("级", "")
    replacements = {"I": "Ⅰ", "II": "Ⅱ", "III": "Ⅲ", "IV": "Ⅳ", "V": "Ⅴ", "VI": "Ⅵ"}
    return text if text in GRADE_COLORS else replacements.get(text.upper(), "未知")


def _assign_lanes(rows: list[dict[str, Any]]) -> dict[str, int]:
    lane_ends: list[float] = []
    assigned: dict[str, int] = {}
    for row in sorted(rows, key=lambda value: (value["start_m"], value["end_m"])):
        lane = next(
            (index for index, end in enumerate(lane_ends) if row["start_m"] >= end),
            len(lane_ends),
        )
        if lane == len(lane_ends):
            lane_ends.append(row["end_m"])
        else:
            lane_ends[lane] = row["end_m"]
        assigned[row["axis_id"]] = lane
    return assigned


def _compact_chainage(value: float, minimum: float) -> str:
    base_km = int(minimum // 1000)
    value_km = int(value // 1000)
    if value_km != base_km:
        return format_chainage(value)
    metres = value - value_km * 1000
    return f"+{metres:.0f}" if abs(metres - round(metres)) < 1e-6 else f"+{metres:.1f}"


def _label_overlaps(fig: Any, artists: list[Any]) -> list[list[str]]:
    renderer = fig.canvas.get_renderer()
    boxes = [artist.get_window_extent(renderer=renderer).expanded(1.02, 1.08) for artist in artists]
    pairs: list[list[str]] = []
    for left in range(len(boxes)):
        for right in range(left + 1, len(boxes)):
            if boxes[left].overlaps(boxes[right]):
                pairs.append([str(left), str(right)])
    return pairs
