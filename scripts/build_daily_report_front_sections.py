#!/usr/bin/env python3
"""Build the first two TBM daily-report chapters from existing analysis outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.reporting.front_sections import (
    ReportSources,
    build_front_sections_facts,
    interval_table_frame,
    load_report_config,
    validate_front_sections_facts,
)
from tbm_process.reporting.report_rendering import (
    export_html_to_pdf,
    render_html_report,
    render_parameter_plot,
    render_timeline,
    validate_rendered_outputs,
)
from tbm_process.reporting.report_narrative import (
    build_default_narrative,
    build_report_narrative_request,
    call_report_narrative_model,
    load_deepseek_api_key,
    validate_report_narrative,
)


DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "daily_report_front_sections.json"
DEFAULT_CURRENT_OUTPUT = PROJECT_ROOT / "outputs" / "current_analysis"
DEFAULT_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_CURRENT_OUTPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument(
        "--report-text-mode",
        choices=("deterministic", "llm"),
        default="deterministic",
    )
    parser.add_argument("--llm-model", default="deepseek-v4-flash")
    parser.add_argument("--llm-base-url", default="https://api.deepseek.com")
    parser.add_argument("--llm-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_report_config(args.config)
    sources = ReportSources(
        samples=args.analysis_dir / "processed_samples" / f"{args.date}.csv",
        platforms=args.analysis_dir / "tables" / "实验平台明细.csv",
        phases=args.analysis_dir / "tables" / "实验四阶段明细.csv",
    )
    for path in (sources.samples, sources.platforms, sources.phases):
        if not path.is_file():
            raise FileNotFoundError(path)

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    facts, samples = build_front_sections_facts(
        date=args.date,
        sources=sources,
        config=config,
    )
    descriptions = [
        {
            "platform_id": row["platform_id"],
            "description": row["reader_description"],
            "provenance": row["description_provenance"],
            "evidence": row["description_evidence"],
            "llm_invoked": False,
        }
        for row in facts["platforms"]
    ]

    facts_path = output / f"{args.date}.daily_report_facts.json"
    descriptions_path = output / f"{args.date}.platform_descriptions.json"
    table_path = output / f"{args.date}.platform_details.csv"
    validation_path = output / f"{args.date}.validation.json"
    timeline_path = output / "figures" / f"{args.date}.timeline.png"
    parameter_path = output / "figures" / f"{args.date}.parameters.png"
    html_path = output / f"{args.date}.TBM施工日报_前两章.html"
    pdf_path = output / f"{args.date}.TBM施工日报_前两章.pdf"
    narrative_path = output / f"{args.date}.report_narrative.json"
    narrative_artifacts = output / "report_narrative_generation"
    narrative_artifacts.mkdir(parents=True, exist_ok=True)

    narrative = build_default_narrative(facts)
    narrative_metadata: dict[str, object] = {
        "requested_mode": args.report_text_mode,
        "applied_mode": "deterministic_fallback",
        "llm_invoked": False,
        "validation_errors": [],
    }
    narrative_validation: dict[str, object] = {
        "passed": True,
        "errors": [],
    }
    if args.report_text_mode == "llm":
        narrative_request, compact_facts = build_report_narrative_request(
            facts, model=args.llm_model
        )
        (narrative_artifacts / "request.json").write_text(
            json.dumps(
                narrative_request,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        api_key = load_deepseek_api_key(args.env_file)
        if api_key:
            narrative_metadata["llm_invoked"] = True
            try:
                payload, response, api_metadata = call_report_narrative_model(
                    narrative_request,
                    api_key=api_key,
                    base_url=args.llm_base_url,
                    timeout_seconds=args.llm_timeout_seconds,
                )
                (narrative_artifacts / "response.json").write_text(
                    json.dumps(
                        response,
                        ensure_ascii=False,
                        indent=2,
                        allow_nan=False,
                    ),
                    encoding="utf-8",
                )
                narrative_validation = validate_report_narrative(
                    payload, facts, compact_facts
                )
                narrative_metadata.update(api_metadata)
                narrative_metadata["validation_errors"] = narrative_validation[
                    "errors"
                ]
                if narrative_validation["passed"]:
                    narrative = narrative_validation["normalized"]
                    narrative_metadata["applied_mode"] = "llm_generated_narrative"
                else:
                    narrative_metadata["status"] = (
                        "invalid_response_fallback_applied"
                    )
            except Exception as exc:
                narrative_metadata["status"] = "api_failure_fallback_applied"
                narrative_metadata["error"] = type(exc).__name__
                narrative_validation = {
                    "passed": False,
                    "errors": [f"api_failure:{type(exc).__name__}"],
                }
        else:
            narrative_metadata["status"] = "api_key_missing_fallback_applied"
            narrative_validation = {
                "passed": False,
                "errors": ["api_key_missing"],
            }

    facts["generation"]["llm_invoked"] = bool(narrative_metadata["llm_invoked"])
    table = interval_table_frame(facts)
    validation = validate_front_sections_facts(facts, samples, config)
    validation["checks"].append(
        {
            "name": "report_narrative_valid_or_fallback_applied",
            "passed": bool(narrative.get("chapter1") and narrative.get("chapter2")),
            "details": {
                "requested_mode": args.report_text_mode,
                "applied_mode": narrative_metadata["applied_mode"],
                "model_validation": narrative_validation,
            },
        }
    )
    (narrative_artifacts / "metadata.json").write_text(
        json.dumps(narrative_metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    facts_path.write_text(
        json.dumps(facts, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    descriptions_path.write_text(
        json.dumps(
            {
                "schema_version": config["schema_version"],
                "date": args.date,
                "llm_invoked": False,
                "generation_mode": "deterministic_fact_based",
                "items": descriptions,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    narrative_path.write_text(
        json.dumps(narrative, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    table.to_csv(table_path, index=False, encoding="utf-8-sig")
    render_timeline(facts, timeline_path, config)
    render_parameter_plot(facts, samples, parameter_path, config)
    render_html_report(
        facts,
        timeline_path=timeline_path,
        parameter_plot_path=parameter_path,
        output_path=html_path,
        narrative=narrative,
    )
    if not args.skip_pdf:
        export_html_to_pdf(html_path, pdf_path, args.chrome)
    validation["checks"].extend(
        validate_rendered_outputs(
            facts,
            html_path=html_path,
            timeline_path=timeline_path,
            parameter_plot_path=parameter_path,
            pdf_path=None if args.skip_pdf else pdf_path,
        )
    )
    validation["passed"] = all(row["passed"] for row in validation["checks"])
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    summary = {
        "date": args.date,
        "platform_count": facts["platform_count"],
        "working_duration_seconds": facts["daily_overview"]["working_duration_seconds"],
        "stopped_duration_seconds": facts["daily_overview"]["stopped_duration_seconds"],
        "utilization_percent": facts["daily_overview"]["utilization_percent"],
        "daily_advance_m": facts["daily_overview"]["daily_advance"]["net_change_m"],
        "validation_passed": validation["passed"],
        "llm_invoked": bool(narrative_metadata["llm_invoked"]),
        "report_text_mode_applied": narrative_metadata["applied_mode"],
        "outputs": {
            "facts": str(facts_path),
            "descriptions": str(descriptions_path),
            "narrative": str(narrative_path),
            "table": str(table_path),
            "timeline": str(timeline_path),
            "parameters": str(parameter_path),
            "html": str(html_path),
            "pdf": str(pdf_path) if not args.skip_pdf else None,
            "validation": str(validation_path),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not validation["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
