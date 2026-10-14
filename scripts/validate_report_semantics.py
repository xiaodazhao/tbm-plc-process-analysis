from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.ingestion.io import write_json
from tbm_process.reporting.semantics import validate_semantic_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate generated TBM engineering-semantic facts.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "report_semantics",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "validation",
    )
    return parser.parse_args()


def _distribution(values: list[int]) -> dict[str, float | int]:
    return {
        "minimum": min(values, default=0),
        "median": statistics.median(values) if values else 0,
        "maximum": max(values, default=0),
        "zero_day_count": sum(value == 0 for value in values),
    }


def main() -> None:
    args = parse_args()
    paths = sorted(
        path
        for path in args.input_dir.glob("*.json")
        if path.name not in {"index.json", "batch_validation.json"}
    )
    if not paths:
        raise SystemExit(f"No semantic JSON files found in {args.input_dir}")

    days = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validation = validate_semantic_payload(payload)
        process_rows = [
            row
            for row in payload.get("main_processes", [])
            if row.get("fact_type") == "representative_construction_period"
        ]
        days.append(
            {
                "date": payload["date"],
                "passed": validation["passed"],
                "checks": validation["checks"],
                "main_process_fact_count": len(process_rows),
                "parameter_comparison_fact_count": sum(
                    row.get("fact_type") == "within_day_parameter_comparison"
                    for row in payload.get("main_processes", [])
                ),
                "construction_response_fact_count": len(payload.get("construction_responses", [])),
                "data_quality_fact_count": len(payload.get("data_quality", [])),
                "intermittent_process_fact_count": sum(
                    row.get("continuity_class") == "intermittent_activities"
                    for row in process_rows
                ),
                "maximum_process_span_seconds": max(
                    (float(row.get("elapsed_span_seconds") or 0) for row in process_rows),
                    default=0.0,
                ),
                "process_span_over_four_hours_count": sum(
                    float(row.get("elapsed_span_seconds") or 0) > 4 * 3600
                    for row in process_rows
                ),
            }
        )

    first_thirty = days[:30]
    report = {
        "schema_version": "tbm_report_semantics_batch_validation_v1",
        "input_directory": str(args.input_dir.resolve()),
        "validated_day_count": len(days),
        "all_days_passed": all(day["passed"] for day in days),
        "failed_dates": [day["date"] for day in days if not day["passed"]],
        "fact_count_distribution": {
            "main_processes": _distribution([day["main_process_fact_count"] for day in days]),
            "construction_responses": _distribution(
                [day["construction_response_fact_count"] for day in days]
            ),
            "data_quality": _distribution([day["data_quality_fact_count"] for day in days]),
        },
        "days_without_main_process_facts": [
            day["date"] for day in days if day["main_process_fact_count"] == 0
        ],
        "days_without_construction_responses": [
            day["date"] for day in days if day["construction_response_fact_count"] == 0
        ],
        "days_with_more_than_eight_construction_responses": [
            day["date"] for day in days if day["construction_response_fact_count"] > 8
        ],
        "session_organization": {
            "intermittent_process_fact_count": sum(
                day["intermittent_process_fact_count"] for day in days
            ),
            "maximum_process_span_seconds": max(
                (day["maximum_process_span_seconds"] for day in days), default=0.0
            ),
            "process_span_over_four_hours_count": sum(
                day["process_span_over_four_hours_count"] for day in days
            ),
        },
        "first_30_days": {
            "date_start": first_thirty[0]["date"] if first_thirty else None,
            "date_end": first_thirty[-1]["date"] if first_thirty else None,
            "validated_day_count": len(first_thirty),
            "main_processes": _distribution(
                [day["main_process_fact_count"] for day in first_thirty]
            ),
            "construction_responses": _distribution(
                [day["construction_response_fact_count"] for day in first_thirty]
            ),
        },
        "days": days,
        "interpretation": {
            "zero_main_process_facts": "Allowed when evidence does not satisfy process-selection rules.",
            "zero_construction_responses": "Allowed; the system must not invent an attention event.",
            "passed": "Internal consistency and expression-constraint checks, not semantic accuracy.",
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "batch_validation.json"
    markdown_path = args.output_dir / "batch_validation_zh.md"
    write_json(json_path, report)
    distribution = report["fact_count_distribution"]
    lines = [
        "# 施工语义批量验证",
        "",
        f"- 验证日期数：{report['validated_day_count']}",
        f"- 内部一致性与表达约束：{'全部通过' if report['all_days_passed'] else '存在失败'}",
        f"- 失败日期：{'、'.join(report['failed_dates']) if report['failed_dates'] else '无'}",
        "",
        "## 事实数量分布",
        "",
        (
            f"- 主要施工过程：最少 {distribution['main_processes']['minimum']}，"
            f"中位数 {distribution['main_processes']['median']}，"
            f"最多 {distribution['main_processes']['maximum']}。"
        ),
        (
            f"- 超过 8 条重点响应的日期："
            f"{'、'.join(report['days_with_more_than_eight_construction_responses']) if report['days_with_more_than_eight_construction_responses'] else '无'}。"
        ),
        (
            f"- 合并作业最大时间跨度："
            f"{report['session_organization']['maximum_process_span_seconds'] / 3600:.2f} 小时；"
            f"超过 4 小时的语义事实 {report['session_organization']['process_span_over_four_hours_count']} 条。"
        ),
        (
            f"- 值得关注的运行情况：最少 {distribution['construction_responses']['minimum']}，"
            f"中位数 {distribution['construction_responses']['median']}，"
            f"最多 {distribution['construction_responses']['maximum']}。"
        ),
        "",
        "没有主要过程或重点响应并非程序错误：证据不足时允许输出简短结果，不补造事实。",
        "本报告只验证结构、追溯和表达约束，不代表施工语义准确率。",
        "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "days"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
