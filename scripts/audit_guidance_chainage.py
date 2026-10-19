#!/usr/bin/env python3
"""Audit TBM9 row-level guidance-chainage alignment without editing source data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.ingestion.io import canonicalize, discover_files, read_daily_file
from tbm_process.ingestion.profiles import (
    DEFAULT_FIELD_PROFILE_PATH,
    load_field_aliases,
    load_guidance_chainage_mapping,
)


DEFAULT_PLC_DIR = Path.home() / "Desktop" / "TBM9_PLC_2023_2024_研究数据视图"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "TBM9_导向盾首里程统一审核_20261017"
REVIEW_DATES = {
    "2023-11-30",
    "2024-04-03",
    "2024-04-22",
    "2024-10-10",
    "2024-10-11",
    "2024-10-12",
}


def _configure_plot_font() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("PingFang SC", "Arial Unicode MS", "Heiti TC"):
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate]
            plt.rcParams["axes.unicode_minus"] = False
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plc-dir", type=Path, default=DEFAULT_PLC_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--field-profile", type=Path, default=DEFAULT_FIELD_PROFILE_PATH)
    args = parser.parse_args()
    _configure_plot_font()

    aliases = load_field_aliases(args.field_profile)
    mapping = load_guidance_chainage_mapping(args.field_profile)
    paths = discover_files(args.plc_dir.expanduser())
    if not paths:
        raise FileNotFoundError(f"No standardized PLC files found in {args.plc_dir}")

    output_dir = args.out_dir.expanduser()
    details_dir = output_dir / "典型日期逐点数据"
    charts_dir = output_dir / "典型日期对照图"
    output_dir.mkdir(parents=True, exist_ok=True)
    details_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    daily_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    for path in paths:
        raw = read_daily_file(path)
        frame = canonicalize(
            raw,
            source_file=path,
            field_aliases=aliases,
            guidance_chainage_mapping=mapping,
        )
        valid_time = frame["time"].dropna()
        date = str(valid_time.iloc[0].date()) if not valid_time.empty else path.stem
        daily_rows.append(_daily_summary(frame, date=date, source_file=path.name))
        episode_rows.extend(_mapping_episodes(frame, date=date, source_file=path.name))
        if date in REVIEW_DATES:
            review = frame[_review_columns(frame)].copy()
            review.to_csv(details_dir / f"{date}.csv", index=False, encoding="utf-8-sig")
            _plot_day(review, date=date, output_path=charts_dir / f"{date}.png")

    daily = pd.DataFrame(daily_rows).sort_values("date")
    episodes = pd.DataFrame(episode_rows).sort_values(
        ["date", "source_row_start"], kind="stable"
    )
    daily.to_csv(output_dir / "每日里程映射审核.csv", index=False, encoding="utf-8-sig")
    episodes.to_csv(output_dir / "映射模式连续区间.csv", index=False, encoding="utf-8-sig")

    summary = _overall_summary(daily)
    (output_dir / "审核摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "审核说明.md").write_text(
        _readme(summary), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Audit written to {output_dir}")


def _daily_summary(frame: pd.DataFrame, *, date: str, source_file: str) -> dict[str, Any]:
    modes = frame["chainage_mapping_mode"].value_counts(dropna=False)
    raw = pd.to_numeric(frame["chainage_raw"], errors="coerce")
    canonical = pd.to_numeric(frame["chainage"], errors="coerce")
    cumulative = pd.to_numeric(frame["cumulative_advance"], errors="coerce")
    spacing = pd.to_numeric(frame["chainage_middle_canonical"], errors="coerce")
    spacing = canonical - spacing
    usable = canonical.notna()
    observed_modes = {
        mode for mode in ("aligned", "shifted_left", "shifted_right")
        if int(modes.get(mode, 0)) > 0
    }
    return {
        "date": date,
        "source_file": source_file,
        "sample_count": int(len(frame)),
        "aligned_count": int(modes.get("aligned", 0)),
        "shifted_left_count": int(modes.get("shifted_left", 0)),
        "shifted_right_count": int(modes.get("shifted_right", 0)),
        "unavailable_count": int(modes.get("unavailable", 0)),
        "ambiguous_count": int(modes.get("ambiguous", 0)),
        "usable_count": int(usable.sum()),
        "usable_ratio": float(usable.mean()) if len(frame) else None,
        "mapping_status": (
            "mixed" if len(observed_modes) > 1
            else next(iter(observed_modes), "unavailable")
        ),
        "mapping_transition_count": _transition_count(frame["chainage_mapping_mode"]),
        "raw_chainage_start": _edge(raw, first=True),
        "raw_chainage_end": _edge(raw, first=False),
        "raw_chainage_change_m": _edge_change(raw),
        "canonical_chainage_start": _edge(canonical, first=True),
        "canonical_chainage_end": _edge(canonical, first=False),
        "canonical_chainage_change_m": _edge_change(canonical),
        "cumulative_advance_change_m": _edge_change(cumulative),
        "raw_max_short_gap_jump_m": _maximum_jump(
            raw, time=frame["time"], maximum_gap_seconds=120.0
        ),
        "canonical_max_short_gap_jump_m": _maximum_jump(
            canonical, time=frame["time"], maximum_gap_seconds=120.0
        ),
        "canonical_max_all_adjacent_jump_m": _maximum_jump(canonical),
        "canonical_short_gap_jump_over_5m_count": _jump_count(
            canonical,
            time=frame["time"],
            threshold=5.0,
            maximum_gap_seconds=120.0,
        ),
        "configured_suspicious_jump_count": int(
            frame["chainage_step_quality"].eq("suspicious_short_gap_jump").sum()
        ),
        "head_middle_spacing_median_m": _number(spacing.median()),
        "head_middle_spacing_p10_m": _number(spacing.quantile(0.1)),
        "head_middle_spacing_p90_m": _number(spacing.quantile(0.9)),
    }


def _mapping_episodes(
    frame: pd.DataFrame, *, date: str, source_file: str
) -> list[dict[str, Any]]:
    modes = frame["chainage_mapping_mode"].fillna("unavailable")
    groups = modes.ne(modes.shift()).cumsum()
    rows = []
    for _, block in frame.groupby(groups, sort=False):
        rows.append(
            {
                "date": date,
                "source_file": source_file,
                "mapping_mode": str(block["chainage_mapping_mode"].iloc[0]),
                "start_time": _iso(block["time"].iloc[0]),
                "end_time": _iso(block["time"].iloc[-1]),
                "sample_count": int(len(block)),
                "source_row_start": int(block["source_row"].iloc[0]),
                "source_row_end": int(block["source_row"].iloc[-1]),
                "canonical_chainage_start": _edge(block["chainage"], first=True),
                "canonical_chainage_end": _edge(block["chainage"], first=False),
            }
        )
    return rows


def _plot_day(frame: pd.DataFrame, *, date: str, output_path: Path) -> None:
    figure, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True, constrained_layout=True)
    axes[0].plot(frame["time"], frame["chainage_raw"], color="#b6482b", linewidth=0.8)
    axes[0].set_ylabel("原始列值")
    axes[0].set_title(f"{date} 导向盾首里程字段统一前后对照")
    axes[1].plot(frame["time"], frame["chainage"], color="#156f62", linewidth=0.8)
    axes[1].set_ylabel("统一盾首里程")
    axes[1].set_xlabel("时间")
    for axis in axes:
        axis.grid(True, alpha=0.25)
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def _overall_summary(daily: pd.DataFrame) -> dict[str, Any]:
    return {
        "schema_version": "tbm9_guidance_chainage_audit_v1",
        "source_files_modified": False,
        "day_count": int(len(daily)),
        "sample_count": int(daily["sample_count"].sum()),
        "usable_sample_count": int(daily["usable_count"].sum()),
        "usable_sample_ratio": float(
            daily["usable_count"].sum() / daily["sample_count"].sum()
        ),
        "aligned_sample_count": int(daily["aligned_count"].sum()),
        "recovered_sample_count": int(
            daily["shifted_left_count"].sum() + daily["shifted_right_count"].sum()
        ),
        "unavailable_sample_count": int(daily["unavailable_count"].sum()),
        "ambiguous_sample_count": int(daily["ambiguous_count"].sum()),
        "aligned_only_day_count": int(daily["mapping_status"].eq("aligned").sum()),
        "recovered_only_day_count": int(
            daily["mapping_status"].isin(["shifted_left", "shifted_right"]).sum()
        ),
        "mixed_mapping_day_count": int(daily["mapping_status"].eq("mixed").sum()),
        "days_with_canonical_short_gap_jump_over_5m": int(
            daily["canonical_short_gap_jump_over_5m_count"].gt(0).sum()
        ),
        "days_excluded_by_configured_jump_quality": int(
            daily["configured_suspicious_jump_count"].gt(0).sum()
        ),
        "mapping_version": "tbm9_guidance_chainage_alignment_v1_2026-10-17",
    }


def _review_columns(frame: pd.DataFrame) -> list[str]:
    requested = [
        "time", "source_file", "source_row", "chainage_raw", "chainage",
        "chainage_middle_canonical", "chainage_mapping_mode", "chainage_source_field",
        "chainage_middle_source_field", "chainage_quality", "cumulative_advance",
        "raw_state", "advance_speed_raw", "thrust", "torque",
    ]
    return [column for column in requested if column in frame.columns]


def _edge(series: pd.Series, *, first: bool) -> float | None:
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if valid.empty:
        return None
    return float(valid.iloc[0] if first else valid.iloc[-1])


def _edge_change(series: pd.Series) -> float | None:
    start = _edge(series, first=True)
    end = _edge(series, first=False)
    return None if start is None or end is None else float(end - start)


def _maximum_jump(
    series: pd.Series,
    *,
    time: pd.Series | None = None,
    maximum_gap_seconds: float | None = None,
) -> float | None:
    jumps = pd.to_numeric(series, errors="coerce").diff().abs().dropna()
    if time is not None and maximum_gap_seconds is not None:
        gaps = pd.to_datetime(time, errors="coerce").diff().dt.total_seconds()
        jumps = jumps.loc[gaps.between(0, maximum_gap_seconds, inclusive="both")]
    return None if jumps.empty else float(jumps.max())


def _jump_count(
    series: pd.Series,
    *,
    time: pd.Series,
    threshold: float,
    maximum_gap_seconds: float,
) -> int:
    jumps = pd.to_numeric(series, errors="coerce").diff().abs()
    gaps = pd.to_datetime(time, errors="coerce").diff().dt.total_seconds()
    valid = gaps.between(0, maximum_gap_seconds, inclusive="both")
    return int((valid & jumps.gt(threshold)).sum())


def _transition_count(series: pd.Series) -> int:
    values = series.fillna("unavailable").astype(str)
    return int(values.ne(values.shift()).sum() - (1 if len(values) else 0))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _iso(value: Any) -> str | None:
    return None if pd.isna(value) else pd.Timestamp(value).isoformat()


def _readme(summary: dict[str, Any]) -> str:
    return f"""# TBM9导向盾首里程统一审核

本目录由逐记录确定性规则生成，不修改原始PLC文件，不进行插值。

## 结果

- 文件/施工日：{summary['day_count']}
- 记录数：{summary['sample_count']}
- 可确定统一盾首里程：{summary['usable_sample_count']}
- 可用比例：{summary['usable_sample_ratio']:.4%}
- 直接按表头对齐的记录：{summary['aligned_sample_count']}
- 从相邻导航列恢复的记录：{summary['recovered_sample_count']}
- 无法判断的记录：{summary['unavailable_sample_count']}
- 多种模式同时成立的记录：{summary['ambiguous_sample_count']}
- 统一后仍在120秒内跳变超过5 m的日期：{summary['days_with_canonical_short_gap_jump_over_5m']}
- 因超过配置跳变阈值而停用当日日进尺的日期：{summary['days_excluded_by_configured_jump_quality']}

## 文件

- `每日里程映射审核.csv`：每天的映射计数、首末值、进尺及最大相邻跳变。
- `映射模式连续区间.csv`：每个连续映射模式的时间和原始行号。
- `典型日期逐点数据/`：保留原始值、统一值、来源字段和主要PLC参数。
- `典型日期对照图/`：原始列值与统一盾首里程的时间序列对照。

该结果为数据一致性恢复；PLC点位表仍是确认字段物理定义的最高等级依据。
"""


if __name__ == "__main__":
    main()
