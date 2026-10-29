from __future__ import annotations

import logging
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import pdfplumber
from PIL import Image, ImageStat

from .geology_axis import GRADE_COLORS


logging.getLogger("pdfminer").setLevel(logging.ERROR)


def render_pdf_previews(pdf_path: Path, preview_dir: Path, *, dpi: int = 130) -> list[Path]:
    preview_dir.mkdir(parents=True, exist_ok=True)
    for existing in preview_dir.glob("page-*.png"):
        existing.unlink()
    prefix = preview_dir / "page"
    completed = subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {completed.stderr[-1200:]}")
    pages = sorted(preview_dir.glob("page-*.png"))
    if not pages:
        raise RuntimeError(f"No preview pages rendered for {pdf_path}")
    return pages


def audit_report_artifacts(
    *,
    plc: dict[str, Any],
    geology: dict[str, Any],
    compact: dict[str, Any],
    axis_model: dict[str, Any],
    axis_render: dict[str, Any],
    pdf_path: Path,
    preview_paths: list[Path],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: Any = None, *, severity: str = "error") -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "severity": severity,
                "details": details,
            }
        )

    selected = set(compact.get("selected_content_unit_ids", []))
    mapped = set(compact.get("content_unit_display_map", {}))
    required = set(compact.get("required_content_unit_ids", []))
    add("all_selected_content_units_have_display_mapping", selected <= mapped, sorted(selected - mapped))
    add("all_required_content_units_have_display_mapping", required <= mapped, sorted(required - mapped))
    add(
        "axis_grade_colors_match_config",
        all(row.get("color") == GRADE_COLORS.get(row.get("grade")) for row in axis_model.get("segments", [])),
    )
    add(
        "forecast_segments_have_valid_chainage",
        all(row["end_m"] > row["start_m"] for row in axis_model.get("segments", [])),
    )
    add(
        "risk_geometry_preserved",
        all(
            (row["geometry"] == "point" and row["start_m"] == row["end_m"])
            or (row["geometry"] == "interval" and row["end_m"] > row["start_m"])
            for row in axis_model.get("risks", [])
        ),
    )
    add(
        "historical_observations_are_points_only",
        all("chainage_m" in row and "start_m" not in row for row in axis_model.get("observations", [])),
    )
    add(
        "axis_labels_do_not_overlap",
        not axis_render.get("label_overlap_pairs"),
        axis_render.get("label_overlap_pairs"),
        severity="review",
    )
    add(
        "axis_not_silently_cropped",
        not axis_render.get("cropped", False),
        {"x_min_m": axis_render.get("x_min_m"), "x_max_m": axis_render.get("x_max_m")},
    )
    references = geology.get("source_references", [])
    add(
        "source_references_traceable",
        all(row.get("source_file") and row.get("source_report_id") for row in references),
        [row for row in references if not row.get("source_file") or not row.get("source_report_id")],
    )
    add(
        "source_pages_not_invented",
        all(
            row.get("source_page") is None
            or (isinstance(row.get("source_page"), int) and row["source_page"] > 0)
            for row in references
        ),
    )
    add(
        "upstream_review_items_preserved",
        all(row.get("handling") for row in geology.get("review_items", [])),
        geology.get("review_items", []),
    )

    page_details: list[dict[str, Any]] = []
    pdf_text_parts: list[str] = []
    pdf_error = None
    try:
        with pdfplumber.open(pdf_path) as document:
            for index, page in enumerate(document.pages, 1):
                page_text = page.extract_text() or ""
                pdf_text_parts.append(page_text)
                page_details.append(
                    {
                        "page": index,
                        "width_pt": page.width,
                        "height_pt": page.height,
                        "a4_portrait": abs(page.width - 595.28) < 8 and abs(page.height - 841.89) < 8,
                        "extractable_text_characters": len(page_text),
                        "embedded_image_count": len(page.images),
                    }
                )
    except Exception as exc:  # pragma: no cover
        pdf_error = f"{type(exc).__name__}: {exc}"
    add("pdf_readable", bool(page_details), pdf_error)
    add("pdf_pages_are_a4_portrait", bool(page_details) and all(row["a4_portrait"] for row in page_details), page_details)
    add(
        "pdf_text_extractable_on_every_page",
        bool(page_details) and all(row["extractable_text_characters"] > 20 for row in page_details),
        page_details,
        severity="review",
    )
    add(
        "pdf_contains_embedded_figures",
        sum(row["embedded_image_count"] for row in page_details) >= 3,
        page_details,
    )
    pdf_text = unicodedata.normalize("NFKC", "\n".join(pdf_text_parts))
    source_refs = [str(row.get("source_ref") or "") for row in references]
    add(
        "pdf_contains_all_source_references",
        all(source_ref and source_ref in pdf_text for source_ref in source_refs),
        [source_ref for source_ref in source_refs if not source_ref or source_ref not in pdf_text],
    )
    required_sections = ["地质信息里程轴", "关键地质信息", "原始资料来源"]
    add(
        "pdf_contains_compact_geology_sections",
        all(section in pdf_text for section in required_sections),
        [section for section in required_sections if section not in pdf_text],
    )

    preview_details: list[dict[str, Any]] = []
    for path in preview_paths:
        with Image.open(path).convert("L") as image:
            stat = ImageStat.Stat(image)
            white_fraction = sum(image.histogram()[248:]) / float(image.width * image.height)
            preview_details.append(
                {
                    "path": str(path.resolve()),
                    "width_px": image.width,
                    "height_px": image.height,
                    "white_fraction": round(white_fraction, 6),
                    "mean_luminance": round(stat.mean[0], 3),
                }
            )
    add(
        "preview_pages_have_visible_content",
        bool(preview_details) and all(row["white_fraction"] < 0.985 for row in preview_details),
        preview_details,
        severity="review",
    )
    add(
        "preview_page_count_matches_pdf",
        len(preview_details) == len(page_details),
        {"previews": len(preview_details), "pdf_pages": len(page_details)},
    )

    error_failures = [row for row in checks if not row["passed"] and row["severity"] == "error"]
    review_items = [row for row in checks if not row["passed"] and row["severity"] == "review"]
    return {
        "schema_version": "tbm_final_report_artifact_audit_v1",
        "date": plc["date"],
        "passed": not error_failures,
        "review_required": bool(review_items),
        "checks": checks,
        "error_failures": error_failures,
        "review_items": review_items,
        "pdf_page_count": len(page_details),
        "page_details": page_details,
        "claim_limit": (
            "This audit checks deterministic consistency, traceability and presentation artifacts; "
            "it is not an engineering-ground-truth accuracy score."
        ),
    }
