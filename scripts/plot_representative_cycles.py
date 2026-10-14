from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

from plot_daily_segmentation_preview import (
    COLORS,
    LABELS,
    _draw_parameter,
    _draw_state_track,
    _upper_limit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw the longest reliable TBM cycles for one day.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()

    source = Path(args.source) / f"{args.date}.csv"
    frame = pd.read_csv(source, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    frame["display_state"] = "other_running"
    frame.loc[frame["machine_state"].eq("stopped"), "display_state"] = "stopped"
    frame.loc[
        frame["machine_state"].eq("advancing") & frame["phase_label"].isna(),
        "display_state",
    ] = "unsegmented_advancing"
    for phase in ("startup", "rising", "stable", "declining"):
        frame.loc[frame["phase_label"].eq(phase), "display_state"] = phase

    selected = _select_intervals(frame, args.count)
    if not selected:
        raise ValueError(f"No reliable stable cycles found for {args.date}")

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, axes = plt.subplots(
        len(selected) * 3,
        1,
        figsize=(18, 4.6 * len(selected)),
        gridspec_kw={"height_ratios": [0.72, 2.4, 2.4] * len(selected)},
        constrained_layout=True,
    )
    axes = np.atleast_1d(axes)
    figure.suptitle(f"{args.date} 最长的{len(selected)}个可靠掘进循环", fontsize=16)
    figure.legend(
        handles=[Patch(facecolor=COLORS[key], label=LABELS[key]) for key in COLORS],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.982),
        ncol=7,
        frameon=False,
        fontsize=10,
    )

    for number, interval_id in enumerate(selected):
        phase = frame[frame["interval_id"].eq(interval_id) & frame["phase_label"].notna()]
        start = phase["time"].min() - pd.Timedelta(minutes=2)
        end = phase["time"].max() + pd.Timedelta(minutes=2)
        window = frame[frame["time"].between(start, end)].copy()
        state_axis, speed_axis, torque_axis = axes[number * 3 : number * 3 + 3]
        _draw_state_track(state_axis, window)
        _draw_parameter(speed_axis, window, "advance_speed", "推进速度", "#246b93")
        _draw_parameter(torque_axis, window, "torque", "刀盘扭矩", "#a6532c")
        state_axis.set_title(
            f"{interval_id}  {phase['time'].min():%H:%M:%S} - {phase['time'].max():%H:%M:%S}",
            loc="left",
            fontsize=11,
            pad=2,
        )
        speed_axis.set_ylim(0, _upper_limit(window["advance_speed"]))
        torque_axis.set_ylim(0, _upper_limit(window["torque"]))
        locator = mdates.AutoDateLocator(minticks=5, maxticks=12)
        for axis in (state_axis, speed_axis, torque_axis):
            axis.set_xlim(start, end)
            axis.xaxis.set_major_locator(locator)
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        state_axis.tick_params(axis="x", labelbottom=False)
        speed_axis.tick_params(axis="x", labelbottom=False)
        torque_axis.set_xlabel("时间")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{args.date}_代表性循环放大图.png"
    figure.savefig(destination, dpi=170)
    plt.close(figure)
    print(destination.resolve())


def _select_intervals(frame: pd.DataFrame, count: int) -> list[str]:
    stable = frame[frame["phase_label"].eq("stable") & frame["interval_id"].notna()].copy()
    if stable.empty:
        return []
    durations = stable.groupby("interval_id")["time"].agg(lambda values: (values.max() - values.min()).total_seconds())
    return durations.sort_values(ascending=False).head(max(count, 1)).index.astype(str).tolist()


if __name__ == "__main__":
    main()
