#!/usr/bin/env python3
"""Run the current platform-first process analysis and build LLM-ready facts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.orchestration.current_pipeline import (
    DOWNSTREAM_METHOD,
    analyze_platform_first_downstream_day,
)
from tbm_process.config import layer_config, load_config
from tbm_process.ingestion.io import write_json
from tbm_process.reporting.quantitative import (
    build_quantitative_report_payload,
    load_report_parameter_registry,
    validate_quantitative_payload,
)
from tbm_process.reporting.semantics import (
    build_construction_semantics,
    load_report_semantics_config,
    validate_semantic_payload,
    write_construction_semantics,
)


DEFAULT_ANALYSIS_CONFIG = PROJECT_ROOT / "configs" / "platform_analysis.json"
DEFAULT_BASE_DIR = PROJECT_ROOT / "outputs" / "base_analysis"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "current_analysis"
DEFAULT_REPORT_CONFIG = PROJECT_ROOT / "report_semantics_config.json"
DEFAULT_UNIT_REGISTRY = PROJECT_ROOT / "configs" / "report_parameter_units.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_ANALYSIS_CONFIG)
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-config", type=Path, default=DEFAULT_REPORT_CONFIG)
    parser.add_argument("--unit-registry", type=Path, default=DEFAULT_UNIT_REGISTRY)
    parser.add_argument("--dates", nargs="*", help="Override configured analysis dates")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    analysis_config = json.loads(args.config.read_text(encoding="utf-8"))
    dates = args.dates or analysis_config["selected_dates"]
    config = load_config()
    phase_config = layer_config(config, "process")
    dynamics_config = layer_config(config, "dynamics")
    orchestration_config = layer_config(config, "orchestration")
    report_config = load_report_semantics_config(args.report_config)
    unit_registry = load_report_parameter_registry(args.unit_registry)

    directories = _output_directories(args.output_dir)
    aggregate: dict[str, list[dict[str, Any]]] = {
        "platforms": [],
        "phases": [],
        "analysis_scopes": [],
        "semantic_segments": [],
        "coupled_events": [],
        "construction_events": [],
        "termination_observations": [],
    }
    day_rows: list[dict[str, Any]] = []
    for date in dates:
        sample_path = args.base_dir / "processed_samples" / f"{date}.csv"
        base_path = args.base_dir / "structured_full_days" / f"{date}.json"
        for path in (sample_path, base_path):
            if not path.is_file():
                raise FileNotFoundError(path)
        samples = pd.read_csv(sample_path, parse_dates=["time"])
        base_result = json.loads(base_path.read_text(encoding="utf-8"))
        result = analyze_platform_first_downstream_day(
            samples,
            date=date,
            base_result=base_result,
            experiment_config=analysis_config,
            phase_config=phase_config,
            dynamics_config=dynamics_config,
            orchestration_config=orchestration_config,
        )

        compact_path = directories["structured"] / f"{date}.json"
        complete_path = directories["complete"] / f"{date}.json"
        write_json(compact_path, result["structured"])
        write_json(complete_path, result["complete_structured"])
        result["samples"].to_csv(
            directories["samples"] / f"{date}.csv",
            index=False,
            encoding="utf-8-sig",
        )

        semantic = build_construction_semantics(
            result["complete_structured"],
            source_result_path=complete_path.resolve(),
            config=report_config,
        )
        semantic["generation"].update(
            {
                "source_analysis_variant": DOWNSTREAM_METHOD,
                "segmentation_modified": True,
                "frozen_baseline_modified": False,
                "instantaneous_stop_boundaries_used_as_dynamic_events": False,
            }
        )
        semantic["expression_limits"].extend(
            [
                "The source uses the experimental platform-first candidate, not the frozen baseline segmentation.",
                "Instantaneous stop boundaries and unobserved terminations are not reader-facing construction responses.",
            ]
        )
        semantic_validation = validate_semantic_payload(semantic)
        write_construction_semantics(semantic, directories["semantics"])

        quantitative = build_quantitative_report_payload(
            frozen_result=result["complete_structured"],
            semantic_payload=semantic,
            samples=result["samples"],
            unit_registry=unit_registry,
            source_paths={
                "candidate_complete_result": complete_path,
                "candidate_processed_samples": directories["samples"] / f"{date}.csv",
                "candidate_semantics": directories["semantics"] / f"{date}.json",
                "parameter_registry": args.unit_registry,
            },
        )
        quantitative["generation"].update(
            {
                "source_analysis_variant": DOWNSTREAM_METHOD,
                "experimental_segmentation_used": True,
                "frozen_baseline_modified": False,
            }
        )
        for package in quantitative["llm_input_packages"].values():
            package["generation_constraints"].extend(
                [
                    "The source analysis uses an experimental platform-first candidate pending researcher approval.",
                    "Do not describe instantaneous stop boundaries as sustained declining phases.",
                    "Do not infer a decline where termination was unobserved because of a sampling gap or daily record end.",
                ]
            )
        quantitative["validation"] = validate_quantitative_payload(quantitative)
        quantitative_path = directories["quantitative"] / f"{date}.quantitative.json"
        formal_path = directories["llm"] / f"{date}.llm_input.formal.json"
        research_path = directories["llm"] / f"{date}.llm_input.research.json"
        write_json(quantitative_path, quantitative)
        write_json(formal_path, quantitative["llm_input_packages"]["formal"])
        write_json(
            research_path,
            quantitative["llm_input_packages"]["research_preview"],
        )

        day_validation = _validate_day(
            date=date,
            result=result,
            base_result=base_result,
            semantic=semantic,
            semantic_validation=semantic_validation,
            quantitative=quantitative,
        )
        write_json(directories["validation"] / f"{date}.json", day_validation)
        day_rows.append(
            {
                "date": date,
                "platform_count": len(result["intervals"]),
                "stable_platform_count": sum(
                    row.get("stable_phase_exists", False) for row in result["intervals"]
                ),
                "observed_decline_count": _termination_count(
                    result["intervals"], "observed_declining_phase"
                ),
                "instantaneous_stop_count": _termination_count(
                    result["intervals"], "instantaneous_stop_boundary"
                ),
                "unobserved_termination_count": sum(
                    str(row["termination_observation"]["observation_type"]).startswith(
                        "termination_unobserved"
                    )
                    for row in result["intervals"]
                ),
                "semantic_main_process_count": len(semantic["main_processes"]),
                "semantic_response_count": len(semantic["construction_responses"]),
                "formal_llm_fact_count": _llm_fact_count(
                    quantitative["llm_input_packages"]["formal"]
                ),
                "research_llm_fact_count": _llm_fact_count(
                    quantitative["llm_input_packages"]["research_preview"]
                ),
                "validation_passed": day_validation["passed"],
                "llm_invoked": False,
            }
        )
        _extend_aggregate(aggregate, result)

    _write_aggregate_tables(directories["tables"], aggregate)
    validation_report = {
        "method": DOWNSTREAM_METHOD,
        "status": "experimental_not_frozen",
        "day_count": len(day_rows),
        "all_days_passed": all(row["validation_passed"] for row in day_rows),
        "llm_invoked": False,
        "frozen_outputs_modified": False,
        "days": day_rows,
    }
    write_json(args.output_dir / "validation_report.json", validation_report)
    (args.output_dir / "10天LLM素材审核索引.md").write_text(
        _render_index(day_rows), encoding="utf-8"
    )
    print(json.dumps(validation_report, ensure_ascii=False, indent=2))
    if not validation_report["all_days_passed"]:
        raise SystemExit(1)


def _output_directories(root: Path) -> dict[str, Path]:
    rows = {
        "structured": root / "structured_days",
        "complete": root / "structured_full_days",
        "samples": root / "processed_samples",
        "semantics": root / "construction_semantics",
        "quantitative": root / "quantitative",
        "llm": root / "llm_inputs",
        "validation": root / "validation",
        "tables": root / "tables",
    }
    for path in rows.values():
        path.mkdir(parents=True, exist_ok=True)
    return rows


def _validate_day(
    *,
    date: str,
    result: dict[str, Any],
    base_result: dict[str, Any],
    semantic: dict[str, Any],
    semantic_validation: dict[str, Any],
    quantitative: dict[str, Any],
) -> dict[str, Any]:
    instant_phase_ids = {
        str(row["phase_id"])
        for row in result["phases"]
        if row.get("decline_observation_type") == "instantaneous_stop_boundary"
    }
    dynamic_phase_ids = {
        str(row.get("phase_id"))
        for key in (
            "descriptors",
            "semantic_segments",
            "coupled_parameter_events",
            "construction_events",
        )
        for row in result[key]
        if row.get("phase_id") is not None
    }
    platform_ids = [str(row["interval_id"]) for row in result["intervals"]]
    assigned = result["samples"].loc[
        result["samples"]["interval_id"].notna(), "interval_id"
    ]
    base_overall = base_result.get("overall", {})
    candidate_overall = result["complete_structured"].get("overall", {})
    checks = [
        _check("semantic_validation_passed", semantic_validation["passed"]),
        _check("quantitative_validation_passed", quantitative["validation"]["passed"]),
        _check(
            "instantaneous_stop_boundaries_excluded_from_dynamic_analysis",
            not (instant_phase_ids & dynamic_phase_ids),
            sorted(instant_phase_ids & dynamic_phase_ids),
        ),
        _check(
            "platform_ids_are_unique",
            len(platform_ids) == len(set(platform_ids)),
        ),
        _check(
            "each_sample_has_at_most_one_platform",
            int(assigned.index.duplicated().sum()) == 0,
        ),
        _check(
            "daily_chainage_advance_unchanged",
            _close(
                candidate_overall.get("chainage_change_m"),
                base_overall.get("chainage_change_m"),
            ),
        ),
        _check(
            "machine_state_durations_unchanged",
            _mapping_close(
                result["complete_structured"]["machine_states"]["duration_seconds"],
                base_result.get("machine_states", {}).get("duration_seconds", {}),
            ),
        ),
        _check(
            "llm_inputs_do_not_expose_internal_phase_labels",
            not _internal_phase_paths(quantitative["llm_input_packages"]),
            _internal_phase_paths(quantitative["llm_input_packages"]),
        ),
        _check("llm_was_not_invoked", True),
    ]
    return {
        "date": date,
        "passed": all(row["passed"] for row in checks),
        "checks": checks,
        "semantic_fact_counts": {
            "daily_overview": len(semantic["daily_overview"]),
            "main_processes": len(semantic["main_processes"]),
            "construction_responses": len(semantic["construction_responses"]),
            "data_quality": len(semantic["data_quality"]),
        },
    }


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _close(left: Any, right: Any) -> bool:
    try:
        return bool(abs(float(left) - float(right)) <= 1e-8)
    except (TypeError, ValueError):
        return left is right


def _mapping_close(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return set(left) == set(right) and all(_close(left[key], right[key]) for key in left)


def _internal_phase_paths(value: Any, path: str = "$") -> list[str]:
    forbidden_keys = {
        "phase_id",
        "phase_label",
        "internal_phase_id",
        "internal_phase_label",
        "stable_phase_ids",
    }
    rows: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden_keys:
                rows.append(child_path)
            rows.extend(_internal_phase_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_internal_phase_paths(child, f"{path}[{index}]"))
    return rows


def _termination_count(intervals: list[dict[str, Any]], label: str) -> int:
    return sum(
        row["termination_observation"]["observation_type"] == label
        for row in intervals
    )


def _llm_fact_count(package: dict[str, Any]) -> int:
    return sum(len(rows) for rows in package["structured_facts"].values())


def _extend_aggregate(
    aggregate: dict[str, list[dict[str, Any]]], result: dict[str, Any]
) -> None:
    aggregate["platforms"].extend(result["intervals"])
    aggregate["phases"].extend(result["phases"])
    aggregate["analysis_scopes"].extend(result["analysis_scopes"])
    aggregate["semantic_segments"].extend(result["semantic_segments"])
    aggregate["coupled_events"].extend(result["coupled_parameter_events"])
    aggregate["construction_events"].extend(result["construction_events"])
    aggregate["termination_observations"].extend(
        {
            "date": result["date"],
            "interval_id": row["interval_id"],
            "platform_start_time": row["start_time"],
            "platform_end_time": row["end_time"],
            **row["termination_observation"],
        }
        for row in result["intervals"]
    )


def _write_aggregate_tables(
    directory: Path, aggregate: dict[str, list[dict[str, Any]]]
) -> None:
    names = {
        "platforms": "实验平台明细.csv",
        "phases": "实验四阶段明细.csv",
        "analysis_scopes": "动态分析作用域.csv",
        "semantic_segments": "连续动态语义区间.csv",
        "coupled_events": "多参数共同响应.csv",
        "construction_events": "筛选施工响应.csv",
        "termination_observations": "平台结束证据.csv",
    }
    for key, filename in names.items():
        pd.DataFrame(aggregate[key]).to_csv(
            directory / filename,
            index=False,
            encoding="utf-8-sig",
        )


def _render_index(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# 10天平台优先方法：LLM素材审核索引",
        "",
        "> 独立候选结果；未修改冻结基线，未调用大语言模型。",
        "",
        "## 处理链",
        "",
        "平台识别 → 四阶段与终止证据 → 阶段内动态分析 → 工程语义 → 定量事实包 → LLM输入JSON",
        "",
        "## 每日结果",
        "",
        "| 日期 | 平台 | 稳定 | 连续下降 | 瞬时终止 | 结束未观测 | 主要过程事实 | 响应事实 | LLM事实 | 验证 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        status = "通过" if row["validation_passed"] else "未通过"
        lines.append(
            f"| {row['date']} | {row['platform_count']} | {row['stable_platform_count']} | "
            f"{row['observed_decline_count']} | {row['instantaneous_stop_count']} | "
            f"{row['unobserved_termination_count']} | {row['semantic_main_process_count']} | "
            f"{row['semantic_response_count']} | {row['research_llm_fact_count']} | {status} |"
        )
    lines.extend(["", "## 文件说明", ""])
    for row in rows:
        date = row["date"]
        lines.extend(
            [
                f"### {date}",
                "",
                f"- [工程语义](construction_semantics/{date}.md)",
                f"- [正式LLM事实包](llm_inputs/{date}.llm_input.formal.json)",
                f"- [研究定量LLM事实包](llm_inputs/{date}.llm_input.research.json)",
                f"- [完整可追溯结果](structured_full_days/{date}.json)",
                f"- [验证结果](validation/{date}.json)",
                "",
            ]
        )
    lines.extend(
        [
            "## 使用边界",
            "",
            "- 连续下降可以进入阶段内动态分析。",
            "- 瞬时终止仅保留边界证据，不作为持续下降事件。",
            "- 采样缺口或日末造成的结束未观测不进行补推。",
            "- 四阶段标签、平台编号和算法术语不进入LLM读者事实。",
            "- 参数工程单位未确认；正式包隐藏绝对值，研究包明确标注原始记录值。",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
