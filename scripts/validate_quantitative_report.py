#!/usr/bin/env python3
"""Independently validate one generated quantitative-report delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.ingestion.io import write_json
from tbm_process.reporting.quantitative import (
    render_quantitative_report,
    validate_quantitative_payload,
)


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "report_quantitative_v1"
DEFAULT_MANIFEST = PROJECT_ROOT / "outputs_frozen_v1" / "method_manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--method-manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    date = args.date
    output_dir = args.output_dir
    paths = {
        "payload": output_dir / f"{date}.quantitative.json",
        "formal_input": output_dir / f"{date}.llm_input.formal.json",
        "research_input": output_dir / f"{date}.llm_input.research.json",
        "formal_report": output_dir / f"{date}.report.formal.deterministic.md",
        "research_report": output_dir / f"{date}.report.research.deterministic.md",
        "formal_request": output_dir / f"{date}.llm_request.formal.json",
        "research_request": output_dir / f"{date}.llm_request.research.json",
    }
    missing = [str(path) for path in (*paths.values(), args.method_manifest) if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing required delivery files: {missing}")

    payload = _load_json(paths["payload"])
    formal_input = _load_json(paths["formal_input"])
    research_input = _load_json(paths["research_input"])
    formal_request = _load_json(paths["formal_request"])
    research_request = _load_json(paths["research_request"])
    manifest = _load_json(args.method_manifest)

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    embedded = validate_quantitative_payload(payload)
    add("quantitative_payload_validation", embedded["passed"], embedded["checks"])
    add(
        "formal_input_matches_payload",
        formal_input == payload["llm_input_packages"]["formal"],
    )
    add(
        "research_input_matches_payload",
        research_input == payload["llm_input_packages"]["research_preview"],
    )
    add(
        "formal_report_reproducible",
        paths["formal_report"].read_text(encoding="utf-8")
        == render_quantitative_report(payload, mode="formal"),
    )
    add(
        "research_report_reproducible",
        paths["research_report"].read_text(encoding="utf-8")
        == render_quantitative_report(payload, mode="research_preview"),
    )

    invocation_states = {
        "formal": formal_request.get("generation_status", {}).get("llm_invoked"),
        "research": research_request.get("generation_status", {}).get("llm_invoked"),
    }
    add(
        "request_artifacts_do_not_claim_llm_output",
        invocation_states == {"formal": False, "research": False},
        invocation_states,
    )

    frozen_hash_mismatches = []
    for relative_path, expected in manifest.get("source_sha256", {}).items():
        source = PROJECT_ROOT / relative_path
        actual = _sha256_file(source) if source.is_file() else None
        if actual != expected:
            frozen_hash_mismatches.append(
                {"path": relative_path, "expected": expected, "actual": actual}
            )
    add(
        "frozen_analysis_sources_match_method_manifest",
        not frozen_hash_mismatches,
        frozen_hash_mismatches,
    )

    report = {
        "schema_version": "tbm_quantitative_report_delivery_validation_v1",
        "date": date,
        "passed": all(check["passed"] for check in checks),
        "llm_invoked": False,
        "checks": checks,
    }
    report_path = output_dir / f"{date}.delivery_validation.json"
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
