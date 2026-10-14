from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch


COLORS = {
    "stopped": "#a9adb2",
    "other_running": "#9b82bd",
    "unsegmented_advancing": "#d5d8dc",
    "startup": "#e3aa32",
    "rising": "#3f9f72",
    "stable": "#3d93c4",
    "declining": "#cc624d",
}

LABELS = {
    "stopped": "停机",
    "other_running": "其他运行状态",
    "unsegmented_advancing": "推进但未划入四阶段",
    "startup": "启动",
    "rising": "上升",
    "stable": "稳定",
    "declining": "下降",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw a readable six-hour TBM phase preview.")
    parser.add_argument("--date", default="2023-11-03")
    parser.add_argument("--source", default="outputs_frozen_v1/processed_samples")
    parser.add_argument("--output", default="outputs_frozen_v1/visualizations/daily_overview")
    args = parser.parse_args()

    source = Path(args.source) / f"{args.date}.csv"
    if not source.exists():
        raise FileNotFoundError(source)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    frame = pd.read_csv(source, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    frame["display_state"] = "other_running"
    frame.loc[frame["machine_state"].eq("stopped"), "display_state"] = "stopped"
    frame.loc[
        frame["machine_state"].eq("advancing") & frame["phase_label"].isna(),
        "display_state",
    ] = "unsegmented_advancing"
    for phase in ("startup", "rising", "stable", "declining"):
        frame.loc[frame["phase_label"].eq(phase), "display_state"] = phase

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, axes = plt.subplots(
        12,
        1,
        figsize=(18, 18),
        gridspec_kw={"height_ratios": [0.72, 2.25, 2.25] * 4},
        constrained_layout=True,
    )
    figure.suptitle(f"{args.date} TBM运行状态与四阶段分段（每段6小时）", fontsize=16)
    figure.legend(
        handles=[Patch(facecolor=COLORS[key], label=LABELS[key]) for key in COLORS],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.985),
        ncol=7,
        frameon=False,
        fontsize=10,
    )

    day_start = pd.Timestamp(args.date)
    speed_limit = _upper_limit(frame["advance_speed"])
    torque_limit = _upper_limit(frame["torque"])
    for block_number in range(4):
        block_start = day_start + pd.Timedelta(hours=6 * block_number)
        block_end = block_start + pd.Timedelta(hours=6)
        block = frame[(frame["time"] >= block_start) & (frame["time"] < block_end)].copy()
        state_axis, speed_axis, torque_axis = axes[block_number * 3 : block_number * 3 + 3]

        _draw_state_track(state_axis, block)
        state_axis.set_title(
            f"{block_start:%H:%M} - {block_end:%H:%M}",
            loc="left",
            fontsize=11,
            pad=2,
        )
        _draw_parameter(speed_axis, block, "advance_speed", "推进速度", "#246b93")
        _draw_parameter(torque_axis, block, "torque", "刀盘扭矩", "#a6532c")
        speed_axis.set_ylim(0, speed_limit)
        torque_axis.set_ylim(0, torque_limit)

        for axis in (state_axis, speed_axis, torque_axis):
            axis.set_xlim(block_start, block_end)
            axis.xaxis.set_major_locator(mdates.HourLocator(interval=1))
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        state_axis.tick_params(axis="x", labelbottom=False)
        speed_axis.tick_params(axis="x", labelbottom=False)
        torque_axis.set_xlabel("时间")

    destination = output_dir / f"{args.date}_六小时分段预览.png"
    figure.savefig(destination, dpi=160)
    plt.close(figure)
    print(destination.resolve())


def _upper_limit(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return 1.0
    return max(float(numeric.max()) * 1.05, 1.0)


def _draw_state_track(axis: Any, frame: pd.DataFrame) -> None:
    axis.set_ylim(0, 1)
    axis.set_yticks([])
    axis.set_ylabel("阶段", rotation=0, ha="right", va="center")
    if frame.empty:
        return
    boundary = frame["display_state"].ne(frame["display_state"].shift())
    boundary |= frame["continuity_block_id"].ne(frame["continuity_block_id"].shift())
    for _, block in frame.groupby(boundary.cumsum(), sort=False):
        state = str(block["display_state"].iloc[0])
        start = block["time"].iloc[0]
        end = block["time"].iloc[-1]
        if end <= start:
            end = start + pd.Timedelta(seconds=20)
        axis.axvspan(start, end, color=COLORS[state], alpha=1.0, linewidth=0)
        if state in {"startup", "rising", "stable", "declining"} and end - start >= pd.Timedelta(minutes=8):
            axis.text(
                start + (end - start) / 2,
                0.5,
                LABELS[state],
                ha="center",
                va="center",
                fontsize=8,
                color="white" if state != "startup" else "#3b2d06",
            )
    axis.grid(axis="x", alpha=0.18)
    _draw_terminal_decline_markers(axis, frame)


def _draw_parameter(
    axis: Any,
    frame: pd.DataFrame,
    column: str,
    label: str,
    line_color: str,
) -> None:
    if not frame.empty:
        for _, block in frame.groupby("continuity_block_id", sort=False):
            axis.plot(block["time"], block[column], color=line_color, linewidth=0.9)
        _shade_phases(axis, frame)
        _draw_terminal_decline_markers(axis, frame)
    axis.set_ylabel(label)
    axis.grid(alpha=0.2)


def _shade_phases(axis: Any, frame: pd.DataFrame) -> None:
    valid = frame[frame["phase_label"].notna()]
    if valid.empty:
        return
    boundary = valid["phase_label"].ne(valid["phase_label"].shift())
    boundary |= valid["continuity_block_id"].ne(valid["continuity_block_id"].shift())
    for _, block in valid.groupby(boundary.cumsum(), sort=False):
        phase = str(block["phase_label"].iloc[0])
        axis.axvspan(
            block["time"].iloc[0],
            block["time"].iloc[-1],
            color=COLORS.get(phase, "#999999"),
            alpha=0.17,
            linewidth=0,
        )


def _draw_terminal_decline_markers(axis: Any, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    decline = frame[frame["phase_label"].eq("declining")]
    if decline.empty:
        return
    groups = decline["time"].diff().gt(pd.Timedelta(seconds=120)).cumsum()
    for _, phase in decline.groupby(groups, sort=False):
        if len(phase) == 1:
            axis.axvline(
                phase["time"].iloc[0],
                color=COLORS["declining"],
                linewidth=1.6,
                alpha=0.95,
                zorder=5,
            )


if __name__ == "__main__":
    main()
