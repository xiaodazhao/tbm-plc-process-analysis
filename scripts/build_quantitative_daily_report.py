#!/usr/bin/env python3
"""Build quantitative LLM inputs and deterministic previews for one frozen day."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.ingestion.io import write_json
from tbm_process.reporting.quantitative import (
    build_quantitative_report_payload,
    load_report_parameter_registry,
    write_quantitative_outputs,
)


DEFAULT_FROZEN_DIR = PROJECT_ROOT / "outputs_frozen_v1"
DEFAULT_SEMANTIC_DIR = PROJECT_ROOT / "outputs" / "report_semantics"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "report_quantitative_v1"
DEFAULT_REGISTRY = PROJECT_ROOT / "configs" / "report_parameter_units.json"
DEFAULT_PROMPT = PROJECT_ROOT / "prompts" / "tbm_daily_report_system_v2_quantitative.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--frozen-dir", type=Path, default=DEFAULT_FROZEN_DIR)
    parser.add_argument("--semantic-dir", type=Path, default=DEFAULT_SEMANTIC_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--unit-registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    return parser.parse_args()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_request(
    *, date: str, mode: str, system_prompt: str, fact_package: dict[str, Any]
) -> dict[str, Any]:
    mode_name = "正式日报" if mode == "formal" else "研究用定量预览"
    user_prompt = (
        f"请根据以下 TBM 施工事实包，编写 {date} 的{mode_name}。\n"
        "严格遵守 System Prompt 和事实包中的 generation_constraints。\n"
        "直接输出 Markdown 日报，不要输出证据编号、JSON、代码块或生成过程。\n\n"
        "<tbm_fact_package>\n"
        f"{_json_text(fact_package)}\n"
        "</tbm_fact_package>"
    )
    return {
        "schema_version": "tbm_daily_report_llm_request_v2",
        "date": date,
        "report_mode": mode,
        "prompt_version": "tbm_daily_report_system_v2_quantitative",
        "system_prompt_sha256": _sha256(system_prompt),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "generation_status": {
            "request_built": True,
            "llm_invoked": False,
            "llm_output_file": None,
            "note": "This file is a request artifact, not model output.",
        },
    }


def _write_request_preview(path: Path, request: dict[str, Any]) -> None:
    lines = [
        f"# {request['date']} {request['report_mode']} LLM 完整请求",
        "",
        "> 本文件仅展示将交给模型的全部消息，尚未调用模型。",
        "",
        "## System message",
        "",
        request["messages"][0]["content"],
        "",
        "## User message",
        "",
        request["messages"][1]["content"],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    date = args.date
    frozen_path = args.frozen_dir / "structured_full_days" / f"{date}.json"
    samples_path = args.frozen_dir / "processed_samples" / f"{date}.csv"
    semantic_path = args.semantic_dir / f"{date}.json"
    for path in (frozen_path, samples_path, semantic_path, args.unit_registry, args.prompt):
        if not path.is_file():
            raise SystemExit(f"Missing required input: {path}")

    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    samples = pd.read_csv(samples_path)
    registry = load_report_parameter_registry(args.unit_registry)
    payload = build_quantitative_report_payload(
        frozen_result=frozen,
        semantic_payload=semantic,
        samples=samples,
        unit_registry=registry,
        source_paths={
            "frozen_result": frozen_path,
            "processed_samples": samples_path,
            "construction_semantics_v3": semantic_path,
            "parameter_registry": args.unit_registry,
        },
    )
    paths = write_quantitative_outputs(payload, args.output_dir)
    system_prompt = args.prompt.read_text(encoding="utf-8").strip()
    for mode, package_key, label in (
        ("formal", "formal", "formal"),
        ("research_preview", "research_preview", "research"),
    ):
        request = _build_request(
            date=date,
            mode=mode,
            system_prompt=system_prompt,
            fact_package=payload["llm_input_packages"][package_key],
        )
        request_path = args.output_dir / f"{date}.llm_request.{label}.json"
        preview_path = args.output_dir / f"{date}.llm_request.{label}.md"
        write_json(request_path, request)
        _write_request_preview(preview_path, request)
        paths[f"{label}_llm_request"] = request_path
        paths[f"{label}_llm_request_preview"] = preview_path

    result = {
        "date": date,
        "validation_passed": payload["validation"]["passed"],
        "llm_invoked": False,
        "outputs": {key: str(path.resolve()) for key, path in paths.items()},
    }
    print(_json_text(result))
    if not payload["validation"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
