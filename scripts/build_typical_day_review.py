from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch


PHASE_COLORS = {
    "startup": "#e3aa32",
    "rising": "#3f9f72",
    "stable": "#3d93c4",
    "declining": "#cc624d",
}
TREND_COLORS = {
    "increasing": "#198754",
    "decreasing": "#c0392b",
    "stable": "#2471a3",
    "insufficient_data": "#7f8c8d",
}
PARAMETERS = {
    "advance_speed": ("advance_speed", "推进速度"),
    "thrust": ("thrust", "推力"),
    "torque": ("torque", "刀盘扭矩"),
    "rpm": ("rpm_canonical", "刀盘转速"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a traceable typical-day dynamic review.")
    parser.add_argument("--date", default="2023-11-03")
    parser.add_argument("--out-dir", default="outputs_frozen_v1")
    args = parser.parse_args()
    root = Path(args.out_dir)
    destination = root / "examples" / args.date
    destination.mkdir(parents=True, exist_ok=True)

    samples = pd.read_csv(root / "processed_samples" / f"{args.date}.csv", parse_dates=["time"])
    intervals = pd.read_csv(root / "advance_intervals.csv")
    phases = pd.read_csv(root / "phases.csv")
    segments = pd.read_csv(root / "merged_semantic_segments.csv")
    coupled = pd.read_csv(root / "coupled_parameter_events.csv")
    compact = json.loads((root / "structured_days" / f"{args.date}.json").read_text(encoding="utf-8"))

    day_intervals = intervals[intervals["date"].astype(str).eq(args.date)].copy()
    day_phases = phases[phases["date"].astype(str).eq(args.date)].copy()
    stable = day_phases[day_phases["phase_label"].eq("stable")].sort_values(
        "duration_seconds", ascending=False
    )
    if stable.empty:
        raise ValueError(f"No stable phase found for {args.date}")
    representative = stable.iloc[0]
    interval_id = str(representative["interval_id"])
    phase_id = str(representative["phase_id"])
    interval_phases = day_phases[day_phases["interval_id"].eq(interval_id)].sort_values("start_time")
    phase_segments = segments[segments["phase_id"].eq(phase_id)].sort_values(
        ["parameter", "start_time"]
    )
    phase_coupled = coupled[coupled["phase_id"].eq(phase_id)].sort_values("start_time")

    interval_phases.to_csv(destination / f"{interval_id}_四阶段.csv", index=False, encoding="utf-8-sig")
    phase_segments.to_csv(destination / f"{phase_id}_连续趋势区间.csv", index=False, encoding="utf-8-sig")
    phase_coupled.to_csv(destination / f"{phase_id}_多参数共同变化.csv", index=False, encoding="utf-8-sig")
    chart_path = destination / f"{interval_id}_动态语义核查.png"
    _plot_review(samples, interval_id, phase_id, interval_phases, phase_segments, phase_coupled, chart_path)
    _write_review(
        destination / "2023-11-03_施工事实示例.md",
        date=args.date,
        compact=compact,
        day_intervals=day_intervals,
        day_phases=day_phases,
        representative=representative,
        interval_phases=interval_phases,
        phase_segments=phase_segments,
        phase_coupled=phase_coupled,
        chart_path=chart_path,
    )
    print(destination.resolve())


def _plot_review(
    samples: pd.DataFrame,
    interval_id: str,
    phase_id: str,
    phases: pd.DataFrame,
    segments: pd.DataFrame,
    coupled: pd.DataFrame,
    destination: Path,
) -> None:
    interval = samples[samples["interval_id"].eq(interval_id)].copy()
    start = interval["time"].min() - pd.Timedelta(minutes=1)
    end = interval["time"].max() + pd.Timedelta(minutes=1)
    frame = samples[samples["time"].between(start, end)].copy()
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, axes = plt.subplots(
        5,
        1,
        figsize=(18, 13),
        sharex=True,
        gridspec_kw={"height_ratios": [0.55, 2, 2, 2, 2]},
        constrained_layout=True,
    )
    figure.suptitle(f"{interval_id} 四阶段与稳定阶段连续动态语义", fontsize=16)
    phase_axis = axes[0]
    phase_axis.set_ylim(0, 1)
    phase_axis.set_yticks([])
    phase_axis.set_ylabel("阶段", rotation=0, ha="right")
    for row in phases.to_dict("records"):
        left = pd.Timestamp(row["start_time"])
        right = pd.Timestamp(row["end_time"])
        phase_axis.axvspan(left, right, color=PHASE_COLORS[row["phase_label"]], alpha=1.0)
        if right - left >= pd.Timedelta(minutes=2):
            phase_axis.text(left + (right - left) / 2, 0.5, _phase_cn(row["phase_label"]), ha="center", va="center", color="white")
    for row in coupled.to_dict("records"):
        phase_axis.axvline(pd.Timestamp(row["start_time"]), color="#7d6608", linewidth=0.8, alpha=0.7)

    for axis, (parameter, (column, label)) in zip(axes[1:], PARAMETERS.items(), strict=True):
        axis.plot(frame["time"], frame[column], color="#aab2bd", linewidth=0.8, label="逐点数据")
        for phase in phases.to_dict("records"):
            axis.axvspan(
                pd.Timestamp(phase["start_time"]),
                pd.Timestamp(phase["end_time"]),
                color=PHASE_COLORS[phase["phase_label"]],
                alpha=0.08,
                linewidth=0,
            )
        for segment in segments[segments["parameter"].eq(parameter)].to_dict("records"):
            block = frame[
                frame["time"].between(
                    pd.Timestamp(segment["start_time"]),
                    pd.Timestamp(segment["end_time"]),
                )
            ]
            axis.plot(
                block["time"],
                block[column],
                color=TREND_COLORS[str(segment["trend"])],
                linewidth=2.0,
            )
        axis.set_ylabel(label)
        axis.grid(alpha=0.2)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].set_xlabel("时间")
    figure.legend(
        handles=[Patch(facecolor=color, label=_trend_cn(trend)) for trend, color in TREND_COLORS.items()],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        ncol=4,
        frameon=False,
    )
    figure.savefig(destination, dpi=180)
    plt.close(figure)


def _write_review(
    path: Path,
    *,
    date: str,
    compact: dict[str, Any],
    day_intervals: pd.DataFrame,
    day_phases: pd.DataFrame,
    representative: pd.Series,
    interval_phases: pd.DataFrame,
    phase_segments: pd.DataFrame,
    phase_coupled: pd.DataFrame,
    chart_path: Path,
) -> None:
    stable_cycles = int(
        day_intervals["segmentation_status"].eq("stable_phase_identified").sum()
    )
    overall = compact["overall"]
    lines = [
        f"# {date} 施工过程事实示例",
        "",
        "这是一份PLC分析事实示例，不是大模型生成的施工日报，也不包含地质或故障原因推断。",
        "",
        "## 全天总览",
        "",
        f"- 原始记录：{overall['row_count']} 条。",
        f"- 有效推进循环：{len(day_intervals)} 个，其中 {stable_cycles} 个识别出可靠稳定阶段。",
        f"- 有数据支持的状态时长：`{json.dumps(overall['machine_state_duration_seconds'], ensure_ascii=False)}`。",
        f"- 硬采样缺口：{overall['data_coverage']['hard_sampling_gap_count']} 个；缺口时间未计入运行或停机时长。",
        f"- 速度积分进尺证据：{_fmt(overall['valid_speed_integrated_distance_m'])} m；累计进尺变化：{_fmt(overall['cumulative_advance_change_m'])} m。两者作为独立证据保留，未自动互相替代。",
        "",
        "## 代表循环",
        "",
        f"选择当天稳定阶段最长的循环 `{representative['interval_id']}`。稳定阶段为 {representative['start_time']} 至 {representative['end_time']}，持续 {_fmt(representative['duration_seconds'] / 60)} 分钟。",
        "",
        "| 阶段 | 开始 | 结束 | 分钟 | 速度中位数 | 推力中位数 | 扭矩中位数 | 转速中位数 | 原始行 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in interval_phases.to_dict("records"):
        lines.append(
            f"| {_phase_cn(row['phase_label'])} | {row['start_time']} | {row['end_time']} | "
            f"{_fmt(float(row['duration_seconds']) / 60)} | {_fmt(row['advance_speed_p50'])} | "
            f"{_fmt(row['thrust_p50'])} | {_fmt(row['torque_p50'])} | {_fmt(row['rpm_p50'])} | "
            f"{int(row['source_row_start'])}–{int(row['source_row_end'])} |"
        )
    lines.extend(
        [
            "",
            "## 稳定阶段内部的连续变化",
            "",
            "下表来自阶段内300秒局部窗口，经相邻同类合并后，使用合并区间的原始有效记录重新计算。短于最小样本要求的片段保留为“证据不足”。",
            "",
            "| 参数 | 开始 | 结束 | 分钟 | 趋势 | 波动 | 中位数 | 变化量 | 原始行 |",
            "|---|---|---|---:|---|---|---:|---:|---|",
        ]
    )
    for row in phase_segments.to_dict("records"):
        lines.append(
            f"| {_parameter_cn(row['parameter'])} | {row['start_time']} | {row['end_time']} | "
            f"{_fmt(float(row['duration_seconds']) / 60)} | {_trend_cn(row['trend'])} | "
            f"{_fluctuation_cn(row['fluctuation_level'])} | {_fmt(row['median'])} | "
            f"{_fmt(row['absolute_change'])} | {int(row['source_row_start'])}–{int(row['source_row_end'])} |"
        )
    lines.extend(
        [
            "",
            "## 多参数共同变化",
            "",
            f"该稳定阶段共形成 {len(phase_coupled)} 个有持续时间证据的多参数共同变化区间。事件统计均在各自精确重叠时段内重新计算。",
            "",
            "| 开始 | 结束 | 分钟 | 参数状态 | 原始行 |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in phase_coupled.to_dict("records"):
        states = []
        for parameter in PARAMETERS:
            trend = row.get(f"{parameter}_trend")
            if pd.isna(trend):
                continue
            fluctuation = row.get(f"{parameter}_fluctuation_level")
            suffix = ""
            if fluctuation in {"moderate", "high"}:
                suffix = f"（{_fluctuation_cn(str(fluctuation))}波动）"
            states.append(f"{_parameter_cn(parameter)}{_trend_cn(str(trend))}{suffix}")
        lines.append(
            f"| {row['start_time']} | {row['end_time']} | {_fmt(float(row['duration_seconds']) / 60)} | "
            f"{'；'.join(states)} | {int(row['source_row_start'])}–{int(row['source_row_end'])} |"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- “上升、下降、稳定、波动”是PLC曲线的客观描述。",
            "- 这些事实不能自动改写为地质恶化、刀具磨损、卡机或设备故障。",
            "- 刀盘转速为待PLC点位表确认的暂定统一映射。",
            "- 图：`" + str(chart_path.name) + "`。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.2f}"


def _phase_cn(value: str) -> str:
    return {"startup": "启动", "rising": "上升", "stable": "稳定", "declining": "下降"}.get(value, value)


def _trend_cn(value: str) -> str:
    return {"increasing": "上升", "decreasing": "下降", "stable": "稳定", "insufficient_data": "证据不足"}.get(value, value)


def _fluctuation_cn(value: str) -> str:
    return {"low": "低", "moderate": "中", "high": "高", "insufficient_data": "证据不足"}.get(value, value)


def _parameter_cn(value: str) -> str:
    return {"advance_speed": "推进速度", "thrust": "推力", "torque": "刀盘扭矩", "rpm": "刀盘转速"}.get(value, value)


if __name__ == "__main__":
    main()
