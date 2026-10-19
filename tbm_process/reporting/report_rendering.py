from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

from tbm_process.reporting.front_sections import format_duration_cn, interval_table_frame


def configure_chinese_font() -> None:
    candidates = [
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            font_manager.fontManager.addfont(str(path))
            family = font_manager.FontProperties(fname=str(path)).get_name()
            plt.rcParams["font.sans-serif"] = [family]
            break
    plt.rcParams["axes.unicode_minus"] = False


def render_timeline(
    facts: dict[str, Any], output_path: Path, config: dict[str, Any]
) -> None:
    configure_chinese_font()
    date = pd.Timestamp(facts["date"])
    start = date.normalize()
    end = start + pd.Timedelta(days=1)
    colors = config["plot"]["platform_colors"]
    fig, ax = plt.subplots(figsize=(13.2, 1.55), constrained_layout=True)
    ax.set_facecolor("#f6f7f7")
    for index, interval in enumerate(facts["platforms"]):
        left = pd.Timestamp(interval["start_time"])
        right = pd.Timestamp(interval["end_time"])
        ax.axvspan(
            left,
            right,
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
    gap = plot_frame["time"].diff().dt.total_seconds().gt(
        float(config["plot"]["gap_break_seconds"])
    )

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
        axis.plot(
            plot_frame["time"],
            values,
            color=line_colors[parameter],
            linewidth=0.72,
            zorder=2,
        )
        metadata = config["parameters"][parameter]
        axis.set_ylabel(
            f"{metadata['display_name']}\n（{metadata['value_label']}）", fontsize=9
        )
        axis.grid(color="#dce1e3", linewidth=0.45, alpha=0.9)
        axis.set_xlim(start, end)
        axis.margins(x=0)
    axes[0].set_title("图2-1 当日主要运行参数变化", fontsize=12, pad=8)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 25, 2)))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].set_xlabel("时间")
    fig.tight_layout(rect=(0.0, 0.055, 1.0, 1.0))
    fig.text(
        0.5,
        0.018,
        "注：浅色背景表示程序识别的运行时段；曲线为质量控制后的逐时刻PLC记录，缺采处不插值。",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#454b4e",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, facecolor="white")
    plt.close(fig)


def render_html_report(
    facts: dict[str, Any],
    *,
    timeline_path: Path,
    parameter_plot_path: Path,
    output_path: Path,
    narrative: dict[str, Any] | None = None,
) -> None:
    overview = facts["daily_overview"]
    advance = overview["daily_advance"]
    date = pd.Timestamp(facts["date"])
    date_cn = f"{date.year}年{date.month}月{date.day}日"
    if narrative is None:
        chapter1_paragraphs = [
            f"{date_cn}，TBM施工里程为{advance['start_dk']}～{advance['end_dk']}，"
            f"当日进尺{advance['net_change_m']:.2f} m。设备累计工作"
            f"{format_duration_cn(overview['working_duration_seconds'])}，停机"
            f"{format_duration_cn(overview['stopped_duration_seconds'])}，设备时间利用率为"
            f"{overview['utilization_percent']:.1f}%。"
        ]
        chapter2_paragraphs: list[str] = []
    else:
        chapter1_paragraphs = narrative["chapter1"]["paragraphs"]
        chapter2_paragraphs = narrative["chapter2"]["paragraphs"]
    chapter1_html = "".join(
        f"<p>{html.escape(str(paragraph))}</p>" for paragraph in chapter1_paragraphs
    )
    chapter2_html = "".join(
        f"<p>{html.escape(str(paragraph))}</p>" for paragraph in chapter2_paragraphs
    )
    table = interval_table_frame(facts)
    rows = "\n".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(value))}</td>" for value in row)
        + "</tr>"
        for row in table.itertuples(index=False, name=None)
    )
    timeline_rel = timeline_path.relative_to(output_path.parent).as_posix()
    parameter_rel = parameter_plot_path.relative_to(output_path.parent).as_posix()
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{date_cn} TBM施工日报（前两章）</title>
<style>
@page {{ size: A4 portrait; margin: 14mm 13mm 13mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #1d2427; background: #fff; font-family: "Heiti SC", "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 10.5pt; line-height: 1.58; }}
.report {{ max-width: 184mm; margin: 0 auto; }}
h1 {{ margin: 0 0 9mm; text-align: center; font-size: 18pt; font-weight: 650; letter-spacing: 0; }}
h2 {{ margin: 7mm 0 3mm; padding-bottom: 1.5mm; border-bottom: 1px solid #9ca7ab; font-size: 13.5pt; font-weight: 650; }}
p {{ margin: 0 0 4mm; text-align: justify; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 9.2pt; page-break-inside: auto; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid #aeb8bc; padding: 1.6mm 1.7mm; vertical-align: middle; }}
th {{ background: #eef2f2; font-weight: 650; text-align: center; }}
td:nth-child(1), td:nth-child(2), td:nth-child(3) {{ text-align: center; white-space: nowrap; }}
th:nth-child(1) {{ width: 28%; }} th:nth-child(2) {{ width: 22%; }} th:nth-child(3) {{ width: 14%; }} th:nth-child(4) {{ width: 36%; }}
.figure {{ margin: 5mm 0 0; page-break-inside: avoid; }}
.figure img {{ display: block; width: 100%; height: auto; }}
.caption {{ margin-top: 1mm; text-align: center; font-size: 8.6pt; color: #4b5559; }}
.note {{ margin-top: 2mm; color: #4b5559; font-size: 8.5pt; }}
</style>
</head>
<body><main class="report">
<h1>{date_cn} TBM施工日报（PLC数据部分）</h1>
<h2>一、当日施工总体情况</h2>
{chapter1_html}
<h2>二、各次运行过程</h2>
{chapter2_html}
<table>
<thead><tr><th>起止时间</th><th>工作时长</th><th>进尺（m）</th><th>主要情况</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="note">注：各段进尺取PLC“开累进尺”字段的区间首末差，不作为当日总进尺的计算依据。</p>
<section class="figure"><img src="{html.escape(timeline_rel)}" alt="全天运行时间轴"><div class="caption">全天运行时间轴（空白处不据此判定现场全部工序停止）</div></section>
<section class="figure"><img src="{html.escape(parameter_rel)}" alt="当日主要运行参数变化"></section>
</main></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")


def export_html_to_pdf(html_path: Path, pdf_path: Path, chrome_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(chrome_path),
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not pdf_path.is_file():
        raise RuntimeError(
            "Chrome PDF export failed: "
            + json.dumps(
                {"returncode": completed.returncode, "stderr": completed.stderr[-2000:]},
                ensure_ascii=False,
            )
        )


def validate_rendered_outputs(
    facts: dict[str, Any],
    *,
    html_path: Path,
    timeline_path: Path,
    parameter_plot_path: Path,
    pdf_path: Path | None,
) -> list[dict[str, Any]]:
    body = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
    overview = facts["daily_overview"]
    advance = overview["daily_advance"]
    table = interval_table_frame(facts)
    advance_tokens = (
        f"{advance['net_change_m']:.2f} m",
        f"{advance['net_change_m']:.2f}米",
        f"{advance['net_change_m']:.2f} 米",
    )
    checks = [
        {
            "name": "html_exists_and_is_nonempty",
            "passed": bool(body),
            "details": str(html_path),
        },
        {
            "name": "html_daily_values_match_facts",
            "passed": (
                all(
                    token in body
                    for token in (
                        advance["start_dk"],
                        advance["end_dk"],
                        format_duration_cn(overview["working_duration_seconds"]),
                        format_duration_cn(overview["stopped_duration_seconds"]),
                        f"{overview['utilization_percent']:.1f}%",
                    )
                )
                and any(token in body for token in advance_tokens)
            ),
            "details": None,
        },
        {
            "name": "html_contains_all_platform_rows",
            "passed": all(str(row[0]) in body for row in table.itertuples(index=False, name=None)),
            "details": {"expected_rows": len(table)},
        },
        {
            "name": "html_hides_internal_platform_ids",
            "passed": not any(row["platform_id"] in body for row in facts["platforms"]),
            "details": None,
        },
        {
            "name": "timeline_image_exists",
            "passed": timeline_path.is_file() and timeline_path.stat().st_size > 0,
            "details": str(timeline_path),
        },
        {
            "name": "parameter_plot_exists",
            "passed": parameter_plot_path.is_file() and parameter_plot_path.stat().st_size > 0,
            "details": str(parameter_plot_path),
        },
    ]
    if pdf_path is not None:
        checks.append(
            {
                "name": "pdf_exists_and_is_nonempty",
                "passed": pdf_path.is_file() and pdf_path.stat().st_size > 0,
                "details": str(pdf_path),
            }
        )
    return checks
