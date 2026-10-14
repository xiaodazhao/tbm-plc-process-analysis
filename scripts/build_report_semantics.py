from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.ingestion.io import write_json
from tbm_process.reporting.semantics import (
    SCHEMA_VERSION,
    build_construction_semantics,
    load_report_semantics_config,
    validate_semantic_payload,
    write_construction_semantics,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate frozen TBM PLC facts into reader-facing Chinese construction semantics."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs_frozen_v1",
        help="Frozen output directory containing structured_full_days.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "report_semantics",
        help="Separate destination for semantic-expression results.",
    )
    parser.add_argument(
        "--dates",
        nargs="*",
        help="Dates to process (YYYY-MM-DD). Omit to process every available day.",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        help="Process at most this many selected days, in date order.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "report_semantics_config.json",
        help="Engineering-semantic selection and expression configuration.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.input_dir / "structured_full_days"
    if not source_dir.is_dir():
        raise SystemExit(f"Missing frozen full-result directory: {source_dir}")
    paths = sorted(source_dir.glob("*.json"))
    if args.dates:
        requested = set(args.dates)
        paths = [path for path in paths if path.stem in requested]
        missing = sorted(requested - {path.stem for path in paths})
        if missing:
            raise SystemExit(f"Frozen daily results not found for: {', '.join(missing)}")
    if not paths:
        raise SystemExit("No frozen daily results selected.")
    if args.max_days is not None:
        if args.max_days <= 0:
            raise SystemExit("--max-days must be positive.")
        paths = paths[: args.max_days]

    rules = load_report_semantics_config(args.config)

    generated = []
    for path in paths:
        complete = json.loads(path.read_text(encoding="utf-8"))
        payload = build_construction_semantics(
            complete,
            source_result_path=path.resolve(),
            config=rules,
        )
        validation = validate_semantic_payload(payload)
        json_path, markdown_path = write_construction_semantics(payload, args.output_dir)
        generated.append(
            {
                "date": payload["date"],
                "json": str(json_path.resolve()),
                "markdown": str(markdown_path.resolve()),
                "overview_fact_count": len(payload["daily_overview"]),
                "main_process_fact_count": len(payload["main_processes"]),
                "construction_response_fact_count": len(payload["construction_responses"]),
                "data_quality_fact_count": len(payload["data_quality"]),
                "validation_passed": validation["passed"],
                "validation": validation,
            }
        )

    index = {
        "schema_version": SCHEMA_VERSION,
        "generation_method": "deterministic_fact_selection_and_template_expression",
        "llm_invoked": False,
        "source_directory": str(source_dir.resolve()),
        "config_path": str(args.config.resolve()),
        "generated_day_count": len(generated),
        "all_days_validated": all(row["validation_passed"] for row in generated),
        "days_without_main_process_facts": [
            row["date"] for row in generated if row["main_process_fact_count"] == 0
        ],
        "days_without_construction_responses": [
            row["date"] for row in generated if row["construction_response_fact_count"] == 0
        ],
        "generated_days": generated,
    }
    write_json(args.output_dir / "index.json", index)
    print(f"Construction semantic expressions written to {args.output_dir.resolve()}")
    print(
        json.dumps(
            {
                "schema_version": index["schema_version"],
                "generated_day_count": index["generated_day_count"],
                "all_days_validated": index["all_days_validated"],
                "days_without_main_process_facts": index[
                    "days_without_main_process_facts"
                ],
                "days_without_construction_responses": index[
                    "days_without_construction_responses"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
