#!/usr/bin/env python3
"""Join PLC and geology facts before planning a complete three-chapter report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tbm_process.final_reporting.common import load_deepseek_api_key, read_json, write_json
from tbm_process.final_reporting.geology import build_geology_presentation
from tbm_process.final_reporting.geology_axis import build_geology_axis_model, render_geology_axis
from tbm_process.final_reporting.geology_presentation import build_compact_geology_presentation
from tbm_process.final_reporting.integrated_facts import build_integrated_facts, build_llm_input
from tbm_process.final_reporting.integrated_llm import (
    ModelResponseError,
    build_generation_repair_request,
    build_generation_request,
    build_plan_request,
    build_plan_repair_request,
    call_json_model,
    validate_draft,
    validate_plan,
)
from tbm_process.final_reporting.plc import build_front_facts, enrich_plc_facts
from tbm_process.final_reporting.plots import render_parameter_plot, render_timeline
from tbm_process.final_reporting.rendering import export_pdf, render_report_html
from tbm_process.final_reporting.validation import validate_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--analysis-dir", type=Path,
        default=ROOT / "datasets/portable_30/analysis",
    )
    parser.add_argument(
        "--geology-candidate-root", type=Path,
        default=ROOT / "datasets/portable_30/geology",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/integrated_reports")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--chrome", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Write joint facts and the complete-report LLM request without calling an API.")
    parser.add_argument("--resume", action="store_true", help="Reuse this date's saved model payloads, then perform at most one missing plan repair.")
    parser.add_argument("--skip-pdf", action="store_true")
    return parser.parse_args()


def _geology_plan(global_plan: dict[str, Any]) -> dict[str, Any]:
    chapter = next(row for row in global_plan["chapters"] if row["chapter_id"] == "3")
    return {
        "sections": [
            {
                "section_id": section_id,
                "paragraphs": [
                    {
                        "paragraph_id": row["paragraph_id"],
                        "content_unit_ids": [
                            fact_id.removeprefix("GEO:")
                            for fact_id in row["fact_ids"]
                            if fact_id.startswith("GEO:")
                        ],
                    }
                    for row in chapter["paragraphs"]
                    if row["section_id"] == section_id
                ],
            }
            for section_id in ("3.1", "3.2")
        ]
    }


def _run_model(
    request: dict[str, Any], *, stage: str, output: Path, api_key: str, args: argparse.Namespace
) -> dict[str, Any]:
    write_json(output / f"{stage}.request.json", request)
    if not api_key:
        raise RuntimeError(f"DEEPSEEK_API_KEY is required to call the model for {stage}")
    try:
        payload, response, metadata = call_json_model(
            request,
            api_key=api_key,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
        )
    except ModelResponseError as exc:
        write_json(output / f"{stage}.response.json", exc.response)
        write_json(output / f"{stage}.metadata.json", exc.metadata)
        write_json(output / f"{stage}.failure.json", {"error_type": type(exc).__name__, "reason": exc.reason})
        raise
    except Exception as exc:
        write_json(output / f"{stage}.failure.json", {"error_type": type(exc).__name__, "message": str(exc)})
        raise
    write_json(output / f"{stage}.response.json", response)
    write_json(output / f"{stage}.metadata.json", metadata)
    write_json(output / f"{stage}.payload.json", payload)
    return payload


def _render(
    date: str,
    *,
    plc: dict[str, Any],
    samples: Any,
    front_config: dict[str, Any],
    geology_path: Path,
    plan: dict[str, Any],
    draft: dict[str, Any],
    output: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    figures = output / "figures"
    timeline = figures / f"{date}.timeline.png"
    parameters = figures / f"{date}.parameters.png"
    geology_axis_png = figures / f"{date}.geology_axis.png"
    geology_axis_pdf = figures / f"{date}.geology_axis.pdf"
    geology_plan_path = output / "geology_plan_from_global_plan.json"
    write_json(geology_plan_path, _geology_plan(plan))
    geology = build_geology_presentation(package_path=geology_path, plan_path=geology_plan_path)
    axis_model = build_geology_axis_model(
        package_path=geology_path,
        plan_path=geology_plan_path,
        source_references=geology["source_references"],
    )
    compact = build_compact_geology_presentation(axis_model=axis_model, geology=geology)
    render_timeline(plc, timeline, front_config)
    render_parameter_plot(plc, samples, parameters, front_config)
    axis_render = render_geology_axis(axis_model, png_path=geology_axis_png, pdf_path=geology_axis_pdf, dpi=220)
    html_path = output / f"{date}.integrated_report.html"
    pdf_path = output / f"{date}.integrated_report.pdf"
    render_report_html(
        plc=plc,
        geology=geology,
        geology_compact=compact,
        timeline_path=timeline,
        parameter_path=parameters,
        geology_axis_path=geology_axis_png,
        output_path=html_path,
        narrative=draft,
    )
    if not args.skip_pdf:
        chrome = args.chrome
        if chrome is None:
            candidates = [
                shutil.which("google-chrome"),
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
            chrome = next((Path(value) for value in candidates if value and Path(value).is_file()), None)
            if chrome is None:
                raise RuntimeError("Chrome/Chromium not found; pass --chrome or --skip-pdf")
        export_pdf(html_path, pdf_path, chrome)
    validation = (
        validate_report(
            plc=plc,
            geology=geology,
            compact=compact,
            axis_model=axis_model,
            axis_render=axis_render,
            html_path=html_path,
            pdf_path=pdf_path,
        )
        if not args.skip_pdf
        else {"passed": True, "note": "PDF validation skipped by user option"}
    )
    write_json(output / "render_validation.json", validation)
    return {"html": str(html_path), "pdf": str(pdf_path) if not args.skip_pdf else None, "validation_passed": validation["passed"]}


def main() -> None:
    args = parse_args()
    date = args.date
    output = args.output_dir.expanduser().resolve() / date
    output.mkdir(parents=True, exist_ok=True)
    geology_path = (
        args.geology_candidate_root.expanduser().resolve()
        / "evidence" / "prepared_days" / date / "03_v2_content_units.json"
    )
    if not geology_path.is_file():
        raise FileNotFoundError(geology_path)
    plc, samples, front_meta = build_front_facts(
        date, plc_project=ROOT, analysis_dir=args.analysis_dir.expanduser().resolve()
    )
    plc = enrich_plc_facts(
        plc,
        samples,
        structured_day_path=args.analysis_dir.expanduser().resolve() / "structured_full_days" / f"{date}.json",
    )
    if not front_meta["validation"]["passed"]:
        write_json(output / "plc_preflight.json", front_meta["validation"])
        raise RuntimeError("PLC report facts failed preflight validation")
    geology_package = read_json(geology_path)
    package = build_integrated_facts(plc, geology_package)
    llm_input = build_llm_input(package)
    write_json(output / "integrated_facts.full.json", package)
    write_json(output / "integrated_facts.llm_input.json", llm_input)
    write_json(output / "plc_preflight.json", front_meta["validation"])

    plan_request = build_plan_request(llm_input, model=args.model)
    write_json(output / "plan.request.json", plan_request)
    if args.dry_run:
        result = {
            "status": "dry_run_no_llm_called",
            "date": date,
            "fact_count": len(package["facts"]),
            "plc_platform_count": package["plc_platform_count"],
            "geology_fact_count": sum(row["source"]["domain"] == "geology" for row in package["facts"]),
            "cross_source_link_count": len(package["cross_source_links"]),
            "output": str(output),
        }
        write_json(output / "run_summary.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return

    api_key = load_deepseek_api_key(args.env_file)
    llm_calls_this_run = 0
    if args.resume and (output / "plan_repair.payload.json").is_file():
        plan = read_json(output / "plan_repair.payload.json")
        plan_stage = "plan_repair"
    elif args.resume and (output / "plan.payload.json").is_file():
        plan = read_json(output / "plan.payload.json")
        plan_stage = "plan"
    else:
        plan = _run_model(plan_request, stage="plan", output=output, api_key=api_key, args=args)
        plan_stage = "plan"
        llm_calls_this_run += 1
    plan_check = validate_plan(plan, package)
    write_json(output / f"{plan_stage}.validation.json", plan_check)
    if not plan_check["passed"]:
        if (output / "plan_repair.payload.json").is_file():
            raise RuntimeError("Repaired global plan failed deterministic validation; no further retries")
        repair_request = build_plan_repair_request(llm_input, plan, plan_check["errors"], model=args.model)
        plan = _run_model(repair_request, stage="plan_repair", output=output, api_key=api_key, args=args)
        llm_calls_this_run += 1
        plan_check = validate_plan(plan, package)
        write_json(output / "plan_repair.validation.json", plan_check)
        if not plan_check["passed"]:
            raise RuntimeError("Repaired global plan failed deterministic validation; no further retries")
    generation_request = build_generation_request(llm_input, plan, model=args.model)
    if args.resume and (output / "generation_repair.payload.json").is_file():
        draft = read_json(output / "generation_repair.payload.json")
        draft_stage = "generation_repair"
    elif args.resume and (output / "generation.payload.json").is_file():
        draft = read_json(output / "generation.payload.json")
        draft_stage = "generation"
    else:
        draft = _run_model(generation_request, stage="generation", output=output, api_key=api_key, args=args)
        draft_stage = "generation"
        llm_calls_this_run += 1
    draft_check = validate_draft(draft, plan, package)
    write_json(output / f"{draft_stage}.validation.json", draft_check)
    if not draft_check["passed"]:
        if draft_stage == "generation_repair":
            raise RuntimeError("Repaired three-chapter draft failed deterministic validation; no further retries")
        original_covered = set(draft_check["covered_fact_ids"])
        repair_request = build_generation_repair_request(llm_input, plan, draft, draft_check["errors"], model=args.model)
        draft = _run_model(repair_request, stage="generation_repair", output=output, api_key=api_key, args=args)
        llm_calls_this_run += 1
        draft_check = validate_draft(draft, plan, package)
        lost_facts = sorted(original_covered - set(draft_check["covered_fact_ids"]))
        write_json(output / "generation_repair.retention.json", {"lost_supported_fact_ids": lost_facts})
        if lost_facts:
            draft_check["errors"].append("repair_removed_supported_facts:" + ",".join(lost_facts))
            draft_check["passed"] = False
        write_json(output / "generation_repair.validation.json", draft_check)
        if not draft_check["passed"]:
            raise RuntimeError("Repaired three-chapter draft failed deterministic validation; no further retries")
    rendered = _render(
        date,
        plc=plc,
        samples=samples,
        front_config=front_meta["config"],
        geology_path=geology_path,
        plan=plan,
        draft=draft,
        output=output,
        args=args,
    )
    result = {
        "status": "generated_candidate_requires_semantic_review",
        "date": date,
        "llm_calls_this_run": llm_calls_this_run,
        "recorded_successful_llm_calls": len(list(output.glob("*.payload.json"))),
        "recorded_model_responses": len(list(output.glob("*.response.json"))),
        "fact_count": len(package["facts"]),
        "cross_source_link_count": len(package["cross_source_links"]),
        "automated_validation_passed": draft_check["passed"] and rendered["validation_passed"],
        **rendered,
    }
    write_json(output / "run_summary.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
