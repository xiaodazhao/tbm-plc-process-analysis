from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber


INTERNAL_ID_RE = re.compile(
    r"\b(?:CU\d+|\d{4}-\d{2}-\d{2}-E\d{3}(?:-P\d{2})?)\b"
)


def validate_report(
    *,
    plc: dict[str, Any],
    geology: dict[str, Any],
    compact: dict[str, Any],
    axis_model: dict[str, Any],
    axis_render: dict[str, Any],
    html_path: Path,
    pdf_path: Path,
) -> dict[str, Any]:
    body = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    overview = plc["daily_overview"]
    add(
        "front_facts_present",
        isinstance(plc.get("platforms"), list)
        and int(plc.get("platform_count", -1)) == len(plc["platforms"]),
        plc.get("platform_count"),
    )
    add(
        "daily_duration_identity",
        abs(
            overview["working_duration_seconds"]
            + overview["stopped_duration_seconds"]
            - overview["effective_state_duration_seconds"]
        )
        < 1e-6,
    )
    add(
        "daily_advance_source_unchanged",
        overview["daily_advance"]["source_field"] == "chainage",
        overview["daily_advance"],
    )
    add(
        "all_selected_required_geology_rendered",
        not geology["unrendered_required_content_unit_ids"],
        geology["unrendered_required_content_unit_ids"],
    )
    add(
        "all_selected_geology_mapped_to_final_presentation",
        not compact["unmapped_selected_content_unit_ids"],
        compact["unmapped_selected_content_unit_ids"],
    )
    add(
        "axis_segment_count_matches_model",
        axis_render["plotted_segment_count"] == len(axis_model["segments"]),
    )
    add(
        "axis_risk_count_matches_model",
        axis_render["plotted_risk_count"] == len(axis_model["risks"]),
    )
    add(
        "axis_observation_count_matches_plottable_model",
        axis_render["plotted_observation_count"]
        == sum(1 for row in axis_model["observations"] if row["plottable"]),
    )
    add(
        "suspicious_source_ranges_are_reviewed",
        all(row.get("handling") for row in geology.get("review_items", [])),
        geology.get("review_items", []),
    )
    add("html_nonempty", bool(body), str(html_path))
    add(
        "html_contains_core_daily_values",
        all(
            token in body
            for token in (
                f"{overview['daily_advance']['net_change_m']:.2f} m",
                f"{overview['utilization_percent']:.1f}%",
                "主要掘进参数统计",
                "主要施工变化",
                "地质信息汇总",
                "地质信息里程轴",
                "关键地质信息",
                "原始资料来源",
            )
        ),
    )
    add(
        "html_hides_internal_ids",
        INTERNAL_ID_RE.search(body) is None,
        INTERNAL_ID_RE.findall(body)[:20],
    )
    add(
        "html_uses_reader_friendly_parameter_names",
        not any(token in body for token in ("rpm同步", "rpm上升", "rpm下降")),
    )
    add(
        "html_does_not_publish_known_bad_range",
        "DyK1017+347~DyK1016+357" not in body
        and "DyK1017+347～DyK1016+357" not in body,
    )
    page_count = 0
    pdf_error = None
    if pdf_path.is_file() and pdf_path.stat().st_size:
        try:
            with pdfplumber.open(pdf_path) as document:
                page_count = len(document.pages)
        except Exception as exc:  # pragma: no cover - recorded in artifact audit
            pdf_error = type(exc).__name__
    add("pdf_readable", page_count > 0, {"pages": page_count, "error": pdf_error})
    return {
        "schema_version": "tbm_final_report_validation_v1",
        "date": plc["date"],
        "passed": all(row["passed"] for row in checks),
        "checks": checks,
        "pdf_page_count": page_count,
        "claim_limit": (
            "Internal consistency and presentation checks only; this is not a claim of "
            "100% engineering truth or segmentation accuracy."
        ),
    }
