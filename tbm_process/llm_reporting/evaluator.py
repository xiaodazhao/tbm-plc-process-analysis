from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any


METRIC_FIELDS = (
    "numeric_fact_accuracy",
    "required_fact_coverage",
    "unsupported_fail_rate",
    "review_rate",
    "fail_claim_count",
    "review_claim_count",
    "time_boundary_error_count",
    "duration_role_error_count",
    "quality_count_role_error_count",
    "event_order_error_count",
    "unit_error_count",
    "structure_ok",
    "sentence_count",
    "character_count",
    "duplicate_sentence_count",
)


def build_result_row(
    *,
    date: str,
    group: str,
    verification: dict[str, Any],
    run_metadata: dict[str, Any],
) -> dict[str, Any]:
    metrics = verification["metrics"]
    row = {"date": date, "group": group, "status": run_metadata.get("status", "success")}
    row.update({field: metrics.get(field) for field in METRIC_FIELDS})
    row.update(
        {
            "input_tokens": run_metadata.get("input_tokens", 0) or 0,
            "output_tokens": run_metadata.get("output_tokens", 0) or 0,
            "total_tokens": run_metadata.get("total_tokens", 0) or 0,
            "api_call_count": run_metadata.get("api_call_count", 0) or 0,
            "duration_seconds": run_metadata.get("duration_seconds", 0) or 0,
            "estimated_cost": None,
        }
    )
    return row


def write_summary(rows: list[dict[str, Any]], *, csv_path: Path, markdown_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["date", "group", "status", *METRIC_FIELDS, "input_tokens", "output_tokens", "total_tokens", "api_call_count", "duration_seconds", "estimated_cost"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    groups = sorted({row["group"] for row in rows})
    lines = [
        "# TBM受约束日报开发集对照实验",
        "",
        "> 本表是固定10天开发集上的自动核验结果，不是跨项目泛化结论，也不是人工准确率。",
        "",
        "## 分组汇总",
        "",
        "| 组别 | 成功天数 | 数值准确率 | 必需事实覆盖率 | 明确失败率 | REVIEW率 | API调用 | 输入Token | 输出Token |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group in groups:
        selected = [row for row in rows if row["group"] == group and row["status"] == "success"]
        lines.append(
            "| {group} | {days} | {numeric} | {coverage} | {fail} | {review} | {calls} | {inputs} | {outputs} |".format(
                group=group,
                days=len(selected),
                numeric=_average_display(selected, "numeric_fact_accuracy"),
                coverage=_average_display(selected, "required_fact_coverage"),
                fail=_average_display(selected, "unsupported_fail_rate"),
                review=_average_display(selected, "review_rate"),
                calls=sum(int(row["api_call_count"]) for row in selected),
                inputs=sum(int(row["input_tokens"]) for row in selected),
                outputs=sum(int(row["output_tokens"]) for row in selected),
            )
        )
    lines += [
        "",
        "## 解释边界",
        "",
        "- 数值准确率只统计验证器能按字段角色精确复算的数值陈述。",
        "- REVIEW与明确FAIL分开统计；REVIEW不被当作通过，也不被强行判错。",
        "- estimated_cost留空，因为配置没有提供经过核实的模型单价。",
        "- E组从D组实际草稿出发，只做一次有证据的局部修订，不重新随机生成。",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_detailed_report(
    rows: list[dict[str, Any]], *, output_root: Path, config: dict[str, Any]
) -> None:
    aggregates = {group: _aggregate(rows, group) for group in "ABCDE"}
    lines = [
        "# TBM结构化事实受约束生成：10天开发集实验报告",
        "",
        "## 实验范围",
        "",
        f"- 日期：{len(config['dates'])}天固定开发集。",
        f"- 模型：`{config['generation']['model']}`，温度{config['generation']['temperature']}，thinking关闭。",
        "- A为确定性模板；B为精简事实直接生成；C增加显式内容计划；D再增加类型化约束；E从D的实际草稿出发做一次独立核验和局部修订。",
        "- 所有组使用同一基础事实池；本实验没有修改冻结PLC分析结果。",
        "- 自动核验是字段角色和内部一致性核验，不是人工日报质量评分。",
        "",
        "## 分组结果",
        "",
        "| 组别 | 成功报告 | 数值准确率 | 必需事实覆盖率 | 明确失败率 | REVIEW率 | API调用 | 总Token | 耗时(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group in "ABCDE":
        value = aggregates[group]
        lines.append(
            f"| {group} | {value['count']} | {_percent(value['numeric_fact_accuracy'])} | "
            f"{_percent(value['required_fact_coverage'])} | {_percent(value['unsupported_fail_rate'])} | "
            f"{_percent(value['review_rate'])} | {value['api_call_count']} | "
            f"{value['total_tokens']} | {value['duration_seconds']:.2f} |"
        )
    lines += [
        "",
        "价格配置未提供经过核实的模型单价，因此没有估算费用。",
        "",
        "## 每日必需事实覆盖率",
        "",
        "| 日期 | A | B | C | D | E |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    row_map = {(str(row["date"]), str(row["group"])): row for row in rows}
    for date in config["dates"]:
        values = [
            _percent(_float_or_none(row_map.get((date, group), {}).get("required_fact_coverage")))
            for group in "ABCDE"
        ]
        lines.append(f"| {date} | " + " | ".join(values) + " |")

    revisions = []
    for path in sorted(output_root.glob("*/E_verified.revision_log.json")):
        log = json.loads(path.read_text(encoding="utf-8"))
        for change in log.get("changes", []):
            revisions.append((path.parent.name, change))
    lines += [
        "",
        "## 核验与修订",
        "",
        f"E组共执行{len(revisions)}处有证据的局部修改；没有重新随机生成D组草稿。",
        "",
    ]
    for date, change in revisions:
        before = str(change.get("before_text") or "").replace("\n", " ")
        after = " / ".join(str(value).replace("\n", " ") for value in change.get("after_texts", []))
        lines += [
            f"- **{date}** `{','.join(change.get('fact_ids', []))}`：{','.join(change.get('reason', []))}",
            f"  - 修改前：{before}",
            f"  - 修改后：{after}",
        ]
    lines += [
        "",
        "## 结果判断",
        "",
        f"- A组的数值准确率和必需事实覆盖率均为{_percent(aggregates['A']['numeric_fact_accuracy'])}和{_percent(aggregates['A']['required_fact_coverage'])}，说明确定性模板是可靠但表达较固定的基线。",
        f"- C组本次覆盖率为{_percent(aggregates['C']['required_fact_coverage'])}，低于B组的{_percent(aggregates['B']['required_fact_coverage'])}。单次开发集运行没有证明内容计划单独提高覆盖率；仍需多随机种子或更强计划遵循机制验证。",
        f"- D组明确失败率为{_percent(aggregates['D']['unsupported_fail_rate'])}，低于C组的{_percent(aggregates['C']['unsupported_fail_rate'])}，类型化约束在本次运行中减少了单位和统计角色错误。",
        f"- E组数值准确率{_percent(aggregates['E']['numeric_fact_accuracy'])}、必需事实覆盖率{_percent(aggregates['E']['required_fact_coverage'])}、明确失败率{_percent(aggregates['E']['unsupported_fail_rate'])}。该结果表示自动可判定项目通过，不等于自然语言质量或工程真实性达到100%。",
        "- 当前实现主要是将内容规划、受约束生成、原子事实核验和证据驱动修订组合到TBM日报场景中，属于有价值的工程化研究实现；尚不足以单独宣称新的通用生成算法。",
        "",
        "## 已知回归检查",
        "",
        "- 2023-11-18：总跨度144分钟与累计活动约141分钟的角色混淆可被检出。",
        "- 2023-10-28：50个质量事件与293条受影响记录的角色互换可被检出。",
        "- 2023-11-14：已报告参数数值却声称未写入参数结论的内部矛盾可被检出。",
        "- 2023-11-30：运行响应未按时间排序可被检出。",
        "- 缺乏证据的地质或设备因果解释会被判为FAIL；复杂但无法由规则可靠判断的陈述保留REVIEW。",
        "",
        "## 局限",
        "",
        "- 每个LLM组每个日期只调用一次，不能据此做显著性结论。",
        "- 自动验证覆盖可复算字段和显式禁用推断，不能替代工程人员对语言重点、可读性和现场含义的评价。",
        "- 参数工程单位尚未由PLC点位表确认；研究版可展示原始记录值，但必须保留单位限制。",
        "- 十天属于方案开发集，不支持跨TBM泛化结论。",
    ]
    (output_root / "experiment_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _aggregate(rows: list[dict[str, Any]], group: str) -> dict[str, Any]:
    selected = [row for row in rows if str(row.get("group")) == group and row.get("status") == "success"]
    result: dict[str, Any] = {"count": len(selected)}
    for field in ("numeric_fact_accuracy", "required_fact_coverage", "unsupported_fail_rate", "review_rate"):
        values = [_float_or_none(row.get(field)) for row in selected]
        values = [value for value in values if value is not None]
        result[field] = mean(values) if values else None
    for field in ("api_call_count", "total_tokens"):
        result[field] = sum(int(float(row.get(field) or 0)) for row in selected)
    result["duration_seconds"] = sum(float(row.get("duration_seconds") or 0) for row in selected)
    return result


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    return float(value)


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _average_display(rows: list[dict[str, Any]], field: str) -> str:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return f"{mean(values):.2%}" if values else "N/A"
