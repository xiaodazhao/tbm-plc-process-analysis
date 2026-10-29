from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager


def configure_chinese_font() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for family in ("Microsoft YaHei", "SimHei", "PingFang SC", "Heiti SC", "Noto Sans CJK SC"):
        if family in available:
            plt.rcParams["font.sans-serif"] = [family]
            break
    plt.rcParams["axes.unicode_minus"] = False


def render_timeline(facts: dict[str, Any], output_path: Path, config: dict[str, Any]) -> None:
    configure_chinese_font()
    date = pd.Timestamp(facts["date"])
    start = date.normalize()
    end = start + pd.Timedelta(days=1)
    colors = config["plot"]["platform_colors"]
    fig, ax = plt.subplots(figsize=(13.2, 1.55), constrained_layout=True)
    ax.set_facecolor("#f6f7f7")
    for index, interval in enumerate(facts["platforms"]):
        ax.axvspan(
            pd.Timestamp(interval["start_time"]),
            pd.Timestamp(interval["end_time"]),
            ymin=0.27,
            ymax=0.76,
            color=colors[index % len(colors)],
            alpha=0.96,
        )
    ax.set_xlim(start, end)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 25, 2)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(axis="x", color="#d7dcde", linewidth=0.55)
    ax.set_title("全天运行时间轴", fontsize=12, pad=8)
    ax.set_xlabel("时间")
    for spine in ("left", "right", "top"):
        ax.spines[spine].set_visible(False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, facecolor="white")
    plt.close(fig)


def render_parameter_plot(
    facts: dict[str, Any],
    samples: pd.DataFrame,
    output_path: Path,
    config: dict[str, Any],
) -> None:
    configure_chinese_font()
    date = pd.Timestamp(facts["date"])
    start = date.normalize()
    end = start + pd.Timedelta(days=1)
    parameters = ["advance_speed", "thrust", "torque"]
    colors = config["plot"]["platform_colors"]
    line_colors = config["plot"]["line_colors"]
    plot_frame = samples.sort_values("time").copy()
    gap = plot_frame["time"].diff().dt.total_seconds().gt(float(config["plot"]["gap_break_seconds"]))

    fig, axes = plt.subplots(3, 1, figsize=(13.2, 7.4), sharex=True)
    for axis, parameter in zip(axes, parameters):
        for index, interval in enumerate(facts["platforms"]):
            axis.axvspan(
                pd.Timestamp(interval["start_time"]),
                pd.Timestamp(interval["end_time"]),
                color=colors[index % len(colors)],
                alpha=0.48,
                linewidth=0,
                zorder=0,
            )
        values = pd.to_numeric(plot_frame[parameter], errors="coerce").copy()
        values.loc[gap] = np.nan
        axis.plot(plot_frame["time"], values, color=line_colors[parameter], linewidth=0.72, zorder=2)
        metadata = config["parameters"][parameter]
        axis.set_ylabel(f"{metadata['display_name']}\n（{metadata['value_label']}）", fontsize=9)
        axis.grid(color="#dce1e3", linewidth=0.45, alpha=0.9)
        axis.set_xlim(start, end)
        axis.margins(x=0)
    axes[0].set_title("图2-1 当日主要运行参数变化", fontsize=12, pad=8)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 25, 2)))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].set_xlabel("时间")
    fig.tight_layout(rect=(0.0, 0.055, 1.0, 1.0))
    fig.text(
        0.5, 0.018,
        "注：浅色背景表示程序识别的运行时段；曲线为质量控制后的逐时刻PLC记录，缺采处不插值。",
        ha="center", va="bottom", fontsize=8.5, color="#454b4e",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, facecolor="white")
    plt.close(fig)
