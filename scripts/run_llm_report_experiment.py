#!/usr/bin/env python3
"""Run A-E constrained TBM report experiments over current fact packages."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.llm_reporting.content_planner import build_content_plan
from tbm_process.llm_reporting.draft import (
    build_template_draft,
    render_draft_markdown,
    validate_draft,
)
from tbm_process.llm_reporting.evaluator import (
    build_result_row,
    write_detailed_report,
    write_summary,
)
from tbm_process.llm_reporting.fact_adapter import build_typed_fact_pool
from tbm_process.llm_reporting.generator import (
    build_generation_request,
    call_deepseek,
)
from tbm_process.llm_reporting.reviser import revise_draft
from tbm_process.llm_reporting.verifier import verify_report


GROUP_NAMES = {
    "A": "A_template",
    "B": "B_direct",
    "C": "C_planned",
    "D": "D_constrained",
    "E": "E_verified",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs/llm_report_experiment.json",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--date", help="Run one configured date (YYYY-MM-DD)")
    selection.add_argument("--all", action="store_true", help="Run all configured dates")
    parser.add_argument(
        "--groups",
        default="A,B,C,D,E",
        help="Comma-separated subset of A,B,C,D,E",
    )
    parser.add_argument("--dry-run", action="store_true", help="Prepare facts and prompts without API calls")
    parser.add_argument("--resume", action="store_true", help="Reuse successful request-matched LLM outputs")
    parser.add_argument("--max-api-calls", type=int, default=None, help="Limit new API calls in this invocation")
    args = parser.parse_args()

    config_path = args.config.expanduser().resolve()
    config = _read_json(config_path)
    dates = list(config["dates"]) if args.all else [args.date]
    unknown_dates = sorted(set(dates) - set(config["dates"]))
    if unknown_dates:
        raise SystemExit(f"Dates not configured: {unknown_dates}")
    groups = [value.strip().upper() for value in args.groups.split(",") if value.strip()]
    invalid_groups = sorted(set(groups) - set(GROUP_NAMES))
    if invalid_groups:
        raise SystemExit(f"Unknown groups: {invalid_groups}")
    if "E" in groups and "D" not in groups:
        # E may still reuse an existing D output; this is checked per date.
        print("提示：E组将读取已有D组实际草稿；若不存在则该日E组失败。")

    source_root = _resolve_project_path(config["source_root"])
    output_root = _resolve_project_path(config["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    configured_limit = int(config["generation"]["maximum_new_api_calls"])
    call_limit = args.max_api_calls if args.max_api_calls is not None else configured_limit
    new_calls = 0
    api_key: str | None = None
    summary_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for date in dates:
        date_dir = output_root / date
        date_dir.mkdir(parents=True, exist_ok=True)
        input_path = source_root / config["input_pattern"].format(date=date)
        quantitative_path = source_root / config["quantitative_pattern"].format(date=date)
        if not input_path.is_file():
            failures.append({"date": date, "group": "shared", "error": f"missing input: {input_path}"})
            continue
        pool, evidence = build_typed_fact_pool(input_path, quantitative_path=quantitative_path)
        plan = build_content_plan(
            pool,
            max_response_facts=int(config["planning"]["maximum_response_facts"]),
        )
        _write_json(date_dir / "input_facts.json", pool)
        _write_json(date_dir / "evidence_index.json", evidence)
        _write_json(date_dir / "content_plan.json", plan)
        _write_json(
            date_dir / "required_facts.json",
            {
                "date": date,
                "defined_before_generation": True,
                "required_fact_ids": plan["required_fact_ids"],
                "required_fact_count": plan["required_fact_count"],
            },
        )

        if "A" in groups:
            draft = build_template_draft(pool, plan)
            verification = verify_report(draft, pool, plan)
            metadata = _non_api_metadata(date, "A", config_path, pool)
            _save_group(date_dir, "A", draft, verification, metadata)
            summary_rows.append(build_result_row(date=date, group="A", verification=verification, run_metadata=metadata))

        for group in [value for value in ("B", "C", "D") if value in groups]:
            request = build_generation_request(
                pool,
                plan,
                group=group,
                model=config["generation"]["model"],
                temperature=float(config["generation"]["temperature"]),
                max_tokens=int(config["generation"]["max_tokens"]),
            )
            prefix = GROUP_NAMES[group]
            _write_json(date_dir / f"{prefix}.request.json", request)
            cached = _load_cached(date_dir, group, request) if args.resume else None
            if cached:
                draft, response_record, metadata = cached
                metadata = dict(metadata)
                metadata["cache_reused"] = True
            elif args.dry_run:
                print(f"{date} {group}: dry-run，已保存请求，未调用API")
                continue
            else:
                if new_calls >= call_limit:
                    failure = _failure_record(date, group, "maximum_new_api_calls_reached", request)
                    _write_json(date_dir / f"{prefix}.run.json", failure)
                    failures.append(failure)
                    continue
                if api_key is None:
                    api_key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass(
                        "输入 DeepSeek API Key（不回显，也不会写入文件）："
                    ).strip()
                if not api_key:
                    raise SystemExit("未提供DEEPSEEK_API_KEY；没有伪造LLM输出。")
                try:
                    draft, response_record, metadata = call_deepseek(
                        request,
                        api_key=api_key,
                        base_url=config["generation"]["base_url"],
                        timeout_seconds=float(config["generation"]["timeout_seconds"]),
                    )
                    new_calls += 1
                except Exception as exc:
                    failure = _failure_record(date, group, f"{type(exc).__name__}: {exc}", request)
                    _write_json(date_dir / f"{prefix}.run.json", failure)
                    failures.append(failure)
                    print(f"{date} {group}: 失败，{failure['error']}")
                    continue
            validate_draft(draft)
            verification = verify_report(draft, pool, plan)
            metadata.update(
                {
                    "source_input_path": pool["source"]["input_path"],
                    "source_input_sha256": pool["source"]["input_sha256"],
                    "source_quantitative_path": pool["source"]["quantitative_path"],
                    "source_quantitative_sha256": pool["source"]["quantitative_sha256"],
                    "config_sha256": _file_sha256(config_path),
                }
            )
            _write_json(date_dir / f"{prefix}.response.json", response_record)
            _save_group(date_dir, group, draft, verification, metadata)
            summary_rows.append(build_result_row(date=date, group=group, verification=verification, run_metadata=metadata))
            print(f"{date} {group}: 完成，coverage={verification['metrics']['required_fact_coverage']:.1%}")

        if "E" in groups and not args.dry_run:
            d_prefix = GROUP_NAMES["D"]
            d_draft_path = date_dir / f"{d_prefix}.draft.json"
            d_verify_path = date_dir / f"{d_prefix}.verification.json"
            if not d_draft_path.is_file() or not d_verify_path.is_file():
                failure = _failure_record(date, "E", "missing_actual_D_draft", None)
                _write_json(date_dir / f"{GROUP_NAMES['E']}.run.json", failure)
                failures.append(failure)
            else:
                d_draft = _read_json(d_draft_path)
                d_verification = _read_json(d_verify_path)
                revised, revision_log, verification = revise_draft(
                    d_draft, pool, plan, verification_before=d_verification
                )
                metadata = _non_api_metadata(date, "E", config_path, pool)
                metadata.update(
                    {
                        "source_group": "D",
                        "source_draft_sha256": _file_sha256(d_draft_path),
                        "revision_passes_used": revision_log["revision_passes_used"],
                    }
                )
                _write_json(date_dir / f"{GROUP_NAMES['E']}.revision_log.json", revision_log)
                _save_group(date_dir, "E", revised, verification, metadata)
                summary_rows.append(build_result_row(date=date, group="E", verification=verification, run_metadata=metadata))
                print(f"{date} E: 完成，局部修改{revision_log['change_count']}项")

    existing_rows = _load_existing_summary(output_root / "summary.csv", dates, groups)
    combined = _merge_rows(existing_rows, summary_rows)
    write_summary(combined, csv_path=output_root / "summary.csv", markdown_path=output_root / "summary.md")
    write_detailed_report(combined, output_root=output_root, config=config)
    _write_json(
        output_root / "experiment_manifest.json",
        {
            "experiment_name": config["experiment_name"],
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "config_path": str(config_path),
            "config_sha256": _file_sha256(config_path),
            "development_set_dates": config["dates"],
            "result_count": len(combined),
            "successful_result_count": sum(
                str(row.get("status")) == "success" for row in combined
            ),
            "result_count_by_group": {
                group: sum(str(row.get("group")) == group for row in combined)
                for group in "ABCDE"
            },
            "recorded_api_call_count": sum(
                int(float(row.get("api_call_count") or 0)) for row in combined
            ),
            "recorded_input_tokens": sum(
                int(float(row.get("input_tokens") or 0)) for row in combined
            ),
            "recorded_output_tokens": sum(
                int(float(row.get("output_tokens") or 0)) for row in combined
            ),
            "pricing": config.get("pricing"),
            "estimated_cost": None,
            "source_plc_analysis_modified": False,
            "note": "Counts describe stored experiment artifacts; run_manifest.json describes only the latest invocation.",
        },
    )
    manifest = {
        "experiment_name": config["experiment_name"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path),
        "config_sha256": _file_sha256(config_path),
        "dates_requested": dates,
        "groups_requested": groups,
        "dry_run": args.dry_run,
        "resume": args.resume,
        "new_api_call_count": new_calls,
        "api_call_limit": call_limit,
        "successful_result_count_this_run": len(summary_rows),
        "failures": failures,
        "source_plc_analysis_modified": False,
    }
    _write_json(output_root / "run_manifest.json", manifest)
    print(f"实验输出：{output_root}")
    print(f"本次新API调用：{new_calls}；成功结果：{len(summary_rows)}；失败：{len(failures)}")
    return 1 if failures else 0


def _save_group(
    date_dir: Path,
    group: str,
    draft: dict[str, Any],
    verification: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    prefix = GROUP_NAMES[group]
    _write_json(date_dir / f"{prefix}.draft.json", draft)
    (date_dir / f"{prefix}.report.md").write_text(
        render_draft_markdown(draft), encoding="utf-8"
    )
    _write_json(date_dir / f"{prefix}.verification.json", verification)
    _write_human_review_csv(date_dir / f"{prefix}.human_review.csv", verification)
    _write_json(date_dir / f"{prefix}.run.json", metadata)


def _load_cached(
    date_dir: Path, group: str, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    prefix = GROUP_NAMES[group]
    paths = [
        date_dir / f"{prefix}.draft.json",
        date_dir / f"{prefix}.response.json",
        date_dir / f"{prefix}.run.json",
    ]
    if not all(path.is_file() for path in paths):
        return None
    draft, response, metadata = (_read_json(path) for path in paths)
    if metadata.get("status") != "success":
        return None
    if metadata.get("request_sha256") != request["request_sha256"]:
        return None
    return draft, response, metadata


def _non_api_metadata(
    date: str, group: str, config_path: Path, pool: dict[str, Any]
) -> dict[str, Any]:
    return {
        "status": "success",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "group": group,
        "method": "deterministic_template" if group == "A" else "deterministic_local_revision",
        "config_path": str(config_path),
        "source_input_sha256": pool["source"]["input_sha256"],
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "api_call_count": 0,
        "duration_seconds": 0.0,
    }


def _failure_record(
    date: str, group: str, error: str, request: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "status": "failed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "group": group,
        "error": error,
        "request_sha256": request.get("request_sha256") if request else None,
        "api_call_count": 0,
        "note": "No template output was substituted for a failed LLM result.",
    }


def _write_human_review_csv(path: Path, verification: dict[str, Any]) -> None:
    fields = ["section", "sentence", "supporting_fact_ids", "automatic_result", "error_types", "evidence"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in verification["manual_review_table"]:
            writer.writerow({
                **row,
                "supporting_fact_ids": json.dumps(row["supporting_fact_ids"], ensure_ascii=False),
                "error_types": json.dumps(row["error_types"], ensure_ascii=False),
                "evidence": json.dumps(row["evidence"], ensure_ascii=False),
            })


def _load_existing_summary(path: Path, dates: list[str], groups: list[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if not (row["date"] in dates and row["group"] in groups)]


def _merge_rows(existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {(str(row["date"]), str(row["group"])): row for row in existing}
    for row in current:
        merged[(str(row["date"]), str(row["group"]))] = row
    return [merged[key] for key in sorted(merged)]


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
