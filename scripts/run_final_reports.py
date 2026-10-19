#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tbm_process.final_reporting.audit import audit_report_artifacts, render_pdf_previews
from tbm_process.final_reporting.common import read_json, write_json
from tbm_process.final_reporting.geology import build_geology_presentation
from tbm_process.final_reporting.geology_axis import build_geology_axis_model, render_geology_axis
from tbm_process.final_reporting.geology_presentation import build_compact_geology_presentation
from tbm_process.final_reporting.plc import build_front_facts, enrich_plc_facts
from tbm_process.final_reporting.rendering import export_pdf, render_report_html
from tbm_process.final_reporting.validation import validate_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate final TBM daily reports.")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/final_report.json")
    parser.add_argument("--date", action="append", dest="dates")
    parser.add_argument("--scope", choices=("priority", "all"), default="all")
    parser.add_argument("--plc-project", type=Path)
    parser.add_argument("--analysis-dir", type=Path)
    parser.add_argument("--geology-candidate-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--chrome", type=Path)
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--skip-preview", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = read_json(args.config)
    dates = args.dates or config[f"{args.scope}_dates" if args.scope == "priority" else "completed_dates"]
    if not dates:
        raise SystemExit("No report dates were selected.")
    paths = _resolve_paths(config, args)
    output_root = paths["output_root"]
    output_root.mkdir(parents=True, exist_ok=True)
    audit_rows: list[dict[str, object]] = []
    progress_path = output_root / "progress.json"

    for date in dates:
        day_output = output_root / "days" / date
        figures = day_output / "figures"
        day_output.mkdir(parents=True, exist_ok=True)
        facts, samples, front_meta = build_front_facts(
            date,
            plc_project=paths["plc_project"],
            analysis_dir=paths["analysis_dir"],
        )
        facts = enrich_plc_facts(
            facts,
            samples,
            structured_day_path=paths["analysis_dir"] / "structured_full_days" / f"{date}.json",
        )
        package_path = paths["geology_candidate_root"] / "evidence" / "prepared_days" / date / "03_v2_content_units.json"
        plan_path = paths["geology_candidate_root"] / "evidence" / "days" / date / "content_plan.json"
        geology = build_geology_presentation(package_path=package_path, plan_path=plan_path)
        axis_model = build_geology_axis_model(
            package_path=package_path,
            plan_path=plan_path,
            source_references=geology["source_references"],
        )
        compact = build_compact_geology_presentation(axis_model=axis_model, geology=geology)

        from tbm_process.reporting.report_rendering import render_parameter_plot, render_timeline

        timeline = figures / f"{date}.timeline.png"
        parameters = figures / f"{date}.parameters.png"
        geology_axis_png = figures / f"{date}.geology_axis.png"
        geology_axis_pdf = figures / f"{date}.geology_axis.pdf"
        render_timeline(facts, timeline, front_meta["config"])
        render_parameter_plot(facts, samples, parameters, front_meta["config"])
        axis_render = render_geology_axis(
            axis_model,
            png_path=geology_axis_png,
            pdf_path=geology_axis_pdf,
            dpi=int(config.get("axis", {}).get("dpi", 220)),
        )

        html_path = day_output / f"{date}.TBM施工日报_最终版.html"
        pdf_path = day_output / f"{date}.TBM施工日报_最终版.pdf"
        render_report_html(
            plc=facts,
            geology=geology,
            geology_compact=compact,
            timeline_path=timeline,
            parameter_path=parameters,
            geology_axis_path=geology_axis_png,
            output_path=html_path,
        )
        if not args.skip_pdf:
            export_pdf(html_path, pdf_path, paths["chrome"])
        validation = (
            validate_report(
                plc=facts,
                geology=geology,
                compact=compact,
                axis_model=axis_model,
                axis_render=axis_render,
                html_path=html_path,
                pdf_path=pdf_path,
            )
            if not args.skip_pdf
            else {"passed": front_meta["validation"]["passed"], "pdf_page_count": 0, "checks": []}
        )

        write_json(day_output / f"{date}.report_facts.json", facts)
        write_json(day_output / f"{date}.geology_source_table.json", geology)
        write_json(day_output / f"{date}.geology_axis_model.json", axis_model)
        write_json(day_output / f"{date}.geology_presentation.json", compact)
        write_json(day_output / f"{date}.axis_render.json", axis_render)
        write_json(day_output / f"{date}.validation.json", validation)

        preview_paths: list[Path] = []
        artifact_audit: dict[str, object] = {"passed": bool(validation["passed"]), "review_required": True, "pdf_page_count": 0}
        if not args.skip_pdf and not args.skip_preview:
            preview_paths = render_pdf_previews(pdf_path, output_root / "previews" / date)
            artifact_audit = audit_report_artifacts(
                plc=facts,
                geology=geology,
                compact=compact,
                axis_model=axis_model,
                axis_render=axis_render,
                pdf_path=pdf_path,
                preview_paths=preview_paths,
            )
            write_json(day_output / f"{date}.artifact_audit.json", artifact_audit)

        _copy_artifact(pdf_path, output_root / "reports_pdf" / pdf_path.name)
        _copy_artifact(geology_axis_png, output_root / "geology_axis_png" / geology_axis_png.name)
        _copy_artifact(geology_axis_pdf, output_root / "geology_axis_pdf" / geology_axis_pdf.name)

        row = {
            "date": date,
            "report_generated": pdf_path.is_file() if not args.skip_pdf else html_path.is_file(),
            "plc_record_count": facts["daily_overview"]["record_count"],
            "platform_count": facts["platform_count"],
            "major_change_count": len(facts["major_construction_changes"]),
            "geology_segment_count": len(axis_model["segments"]),
            "geology_risk_count": len(axis_model["risks"]),
            "historical_observation_count": len(axis_model["observations"]),
            "key_information_count": len(compact["key_information"]),
            "source_count": len(compact["source_references"]),
            "mapped_content_unit_count": len(compact["content_unit_display_map"]),
            "unmapped_content_unit_count": len(compact["unmapped_selected_content_unit_ids"]),
            "deterministic_validation_passed": bool(validation["passed"]),
            "artifact_audit_passed": bool(artifact_audit.get("passed")),
            "artifact_review_required": bool(artifact_audit.get("review_required")),
            "pdf_page_count": int(artifact_audit.get("pdf_page_count", 0)),
            "llm_calls": 0,
        }
        audit_rows.append(row)
        write_json(
            progress_path,
            {
                "task": "TBM9 final daily report",
                "status": "running",
                "completed_dates": [value["date"] for value in audit_rows],
                "last_completed_date": date,
                "remaining_dates": [value for value in dates if value not in {r["date"] for r in audit_rows}],
            },
        )
        print(json.dumps(row, ensure_ascii=False))

    audit_dir = output_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    write_json(audit_dir / "final_report_audit.json", audit_rows)
    with (audit_dir / "final_report_audit.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(audit_rows[0]))
        writer.writeheader()
        writer.writerows(audit_rows)
    write_json(
        progress_path,
        {
            "task": "TBM9 final daily report",
            "status": "batch_complete",
            "completed_dates": dates,
            "output_root": str(output_root.resolve()),
        },
    )


def _copy_artifact(source: Path, target: Path) -> None:
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _resolve_paths(config: dict[str, object], args: argparse.Namespace) -> dict[str, Path]:
    configured = config.get("paths")
    if not isinstance(configured, dict):
        raise SystemExit("The final report config must contain a paths object.")

    overrides = {
        "plc_project": args.plc_project,
        "analysis_dir": args.analysis_dir,
        "geology_candidate_root": args.geology_candidate_root,
        "output_root": args.output_dir,
        "chrome": args.chrome,
    }
    result: dict[str, Path] = {}
    for key in ("plc_project", "analysis_dir", "geology_candidate_root", "output_root"):
        raw = overrides[key] if overrides[key] is not None else configured.get(key)
        if raw in (None, ""):
            flag = "--geology-candidate-root" if key == "geology_candidate_root" else f"--{key.replace('_', '-')}"
            raise SystemExit(f"Missing required path {key!r}; set it in the config or pass {flag}.")
        result[key] = _repo_relative_path(Path(raw).expanduser())

    for key in ("analysis_dir", "geology_candidate_root"):
        if not result[key].is_dir():
            raise SystemExit(f"Required directory does not exist: {result[key]}")
    if not (result["plc_project"] / "configs/daily_report_front_sections.json").is_file():
        raise SystemExit(f"Not a TBM PLC project directory: {result['plc_project']}")

    chrome_raw = overrides["chrome"] if overrides["chrome"] is not None else configured.get("chrome")
    if args.skip_pdf:
        result["chrome"] = Path(str(chrome_raw or "chrome-not-required"))
    else:
        result["chrome"] = _find_chrome(chrome_raw)
    return result


def _repo_relative_path(path: Path) -> Path:
    return path if path.is_absolute() else (ROOT / path).resolve()


def _find_chrome(configured: object) -> Path:
    if configured:
        candidate = _repo_relative_path(Path(str(configured)).expanduser())
        if candidate.is_file():
            return candidate
        resolved = shutil.which(str(configured))
        if resolved:
            return Path(resolved)

    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ]
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value)
    raise SystemExit("Chrome/Chromium was not found. Pass --chrome or use --skip-pdf.")


if __name__ == "__main__":
    main()
