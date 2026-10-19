from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd


def render_report_html(
    *,
    plc: dict[str, Any],
    geology: dict[str, Any],
    geology_compact: dict[str, Any],
    timeline_path: Path,
    parameter_path: Path,
    geology_axis_path: Path,
    output_path: Path,
) -> None:
    overview = plc["daily_overview"]
    advance = overview["daily_advance"]
    date = pd.Timestamp(plc["date"])
    date_cn = f"{date.year}年{date.month}月{date.day}日"
    timeline_rel = timeline_path.relative_to(output_path.parent).as_posix()
    parameter_rel = parameter_path.relative_to(output_path.parent).as_posix()
    geology_axis_rel = geology_axis_path.relative_to(output_path.parent).as_posix()
    coverage_start = pd.Timestamp(overview["coverage_start"]).strftime("%H:%M:%S")
    coverage_end = pd.Timestamp(overview["coverage_end"]).strftime("%H:%M:%S")
    gap_text = (
        f"程序识别{overview['hard_gap_count']}处硬缺口，缺采时段不计入工作或停机时长。"
        if overview["hard_gap_count"]
        else ""
    )
    chapter1 = (
        f"{date_cn}，TBM导向盾首里程由{_dyk(advance['start_dk'])}推进至"
        f"{_dyk(advance['end_dk'])}，按导向盾首里程首末差计算，当日进尺"
        f"{advance['net_change_m']:.2f} m。设备累计工作"
        f"{_duration(overview['working_duration_seconds'])}，停机"
        f"{_duration(overview['stopped_duration_seconds'])}；有效状态统计时长"
        f"{_duration(overview['effective_state_duration_seconds'])}，设备时间利用率"
        f"{overview['utilization_percent']:.1f}%。PLC记录覆盖{coverage_start}～"
        f"{coverage_end}，共{overview['record_count']}条。{gap_text}"
    )
    parameter_rows = _parameter_rows(plc["daily_parameter_statistics"])
    interval_rows = _interval_rows(plc["platforms"])
    changes = _changes_html(plc["major_construction_changes"])
    geology_items = _key_geology_rows(geology_compact["key_information"])
    source_notes = _source_notes(geology_compact["source_references"])
    geology_warning = _geology_review_note(geology_compact.get("review_items", []))
    unit_note = html.escape(plc["daily_parameter_statistics"]["unit_note"])
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{date_cn} TBM施工日报</title>
<style>
@page {{ size: A4 portrait; margin: 13mm 12mm 13mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #20282b; background: white; font-family: "PingFang SC", "Heiti SC", "Microsoft YaHei", sans-serif; font-size: 9.6pt; line-height: 1.52; }}
main {{ max-width: 186mm; margin: 0 auto; }}
h1 {{ margin: 0 0 7mm; text-align: center; font-size: 18pt; font-weight: 650; }}
h2 {{ margin: 6mm 0 2.8mm; padding: 0 0 1.3mm; border-bottom: 1.2px solid #819094; font-size: 13.2pt; font-weight: 650; break-after: avoid; }}
h3 {{ margin: 4mm 0 2mm; font-size: 10.8pt; font-weight: 650; color: #314045; break-after: avoid; }}
p {{ margin: 0 0 3mm; text-align: justify; }}
.lead {{ font-size: 10pt; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 8.4pt; margin: 1.5mm 0 2mm; }}
thead {{ display: table-header-group; }}
th, td {{ border: 0.7px solid #a8b2b5; padding: 1.25mm 1.35mm; vertical-align: top; overflow-wrap: anywhere; }}
th {{ background: #e9eeee; font-weight: 650; text-align: center; color: #243136; }}
tr {{ break-inside: avoid; }}
.center {{ text-align: center; vertical-align: middle; }}
.nowrap {{ white-space: nowrap; }}
.parameter th:nth-child(1) {{ width: 18%; }} .parameter th:nth-child(2) {{ width: 16%; }} .parameter th:nth-child(3) {{ width: 16%; }} .parameter th:nth-child(4) {{ width: 18%; }} .parameter th:nth-child(5) {{ width: 16%; }} .parameter th:nth-child(6) {{ width: 16%; }}
.interval th:nth-child(1) {{ width: 25%; }} .interval th:nth-child(2) {{ width: 20%; }} .interval th:nth-child(3) {{ width: 13%; }} .interval th:nth-child(4) {{ width: 42%; }}
.geology-key {{ font-size: 8pt; }}
.geology-key th:nth-child(1) {{ width: 20%; }} .geology-key th:nth-child(2) {{ width: 67%; }} .geology-key th:nth-child(3) {{ width: 13%; }}
.cell-lines {{ white-space: pre-line; }}
.relation {{ margin-top: 1mm; color: #5c696d; font-size: 7.2pt; }}
.note {{ margin: 1.3mm 0 3mm; padding: 1.6mm 2mm; border-left: 3px solid #78898e; background: #f3f6f6; color: #465357; font-size: 8pt; }}
.warning {{ border-left-color: #b06b34; background: #fbf5ef; }}
.figure {{ margin: 4mm 0 1mm; break-inside: avoid; }}
.figure img {{ display: block; width: 100%; height: auto; }}
.caption {{ margin-top: 0.8mm; text-align: center; color: #536064; font-size: 8pt; }}
.changes {{ margin: 0; padding-left: 5mm; }} .changes li {{ margin: 0 0 1.5mm; }}
.source-list {{ margin: 1mm 0 0; padding-left: 5mm; font-size: 7.4pt; color: #566267; }}
.source-list li {{ margin: 0 0 0.8mm; overflow-wrap: anywhere; }}
.keep {{ break-inside: avoid; }}
</style></head><body><main>
<h1>{date_cn} TBM施工日报</h1>
<h2>一、当日施工总体情况</h2>
<p class="lead">{html.escape(chapter1)}</p>
<h3>主要掘进参数统计</h3>
<table class="parameter"><thead><tr><th>参数</th><th>有效样本</th><th>有效比例</th><th>均值</th><th>中位数</th><th>P10～P90</th></tr></thead><tbody>{parameter_rows}</tbody></table>
<p class="note">统计范围：程序识别运行时段内的非停止记录；停机零值不纳入。表中数值为{unit_note}，不得据此补写未经确认的工程单位。</p>
<h2>二、主要施工过程</h2>
<table class="interval"><thead><tr><th>起止时间</th><th>工作时长</th><th>区间进尺（m）</th><th>主要运行情况</th></tr></thead><tbody>{interval_rows}</tbody></table>
<p class="note">区间进尺取PLC“开累进尺”字段首末差并执行既有质量检查，仅用于区间记录；当日进尺仍以导向盾首里程首末差为准。运行时段是算法识别结果，不等同于经人工确认的完整掘进循环。</p>
<section class="figure"><img src="{html.escape(timeline_rel)}" alt="全天运行时间轴"><div class="caption">全天运行时间轴（空白处不据此推断现场全部工序停止）</div></section>
<section class="figure"><img src="{html.escape(parameter_rel)}" alt="当日主要运行参数变化"></section>
<h3>主要施工变化</h3>{changes}
<h2>三、地质信息汇总</h2>
<h3>A. 地质信息里程轴</h3>
<section class="figure"><img src="{html.escape(geology_axis_rel)}" alt="地质信息里程轴"><div class="caption">TSP/HSP色带表示原报告预测围岩等级，风险几何独立显示；历史素描为既往单点记录，不向邻段外推。浅绿色范围仅表示当日导向盾首起止里程，不等同于已确认掌子面范围。</div></section>
<h3>B. 关键地质信息</h3>
<table class="geology-key"><thead><tr><th>位置</th><th>关键信息</th><th>来源</th></tr></thead><tbody>{geology_items}</tbody></table>
{geology_warning}
<h3>C. 原始资料来源</h3><ul class="source-list">{source_notes}</ul>
</main></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")


def export_pdf(html_path: Path, pdf_path: Path, chrome_path: Path) -> None:
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
    if completed.returncode != 0 or not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(
            "Chrome PDF export failed: "
            + json.dumps(
                {"returncode": completed.returncode, "stderr": completed.stderr[-2000:]},
                ensure_ascii=False,
            )
        )


def _parameter_rows(summary: dict[str, Any]) -> str:
    if not summary["scope_sample_count"]:
        return (
            "<tr><td class='center' colspan='6'>"
            "本日未识别到可用于该统计口径的运行时段样本。"
            "</td></tr>"
        )
    rows: list[str] = []
    for values in summary["parameters"].values():
        rows.append(
            "<tr>"
            f"<td class='center'>{html.escape(values['display_name'])}</td>"
            f"<td class='center'>{values['valid_sample_count']}/{values['scope_sample_count']}</td>"
            f"<td class='center'>{_percent(values['valid_ratio'])}</td>"
            f"<td class='center'>{_value(values['mean'])}</td>"
            f"<td class='center'>{_value(values['median'])}</td>"
            f"<td class='center'>{_value(values['p10'])}～{_value(values['p90'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _interval_rows(intervals: list[dict[str, Any]]) -> str:
    if not intervals:
        return (
            "<tr><td class='center' colspan='4'>"
            "本日未识别到符合既有平台规则的运行时段。"
            "</td></tr>"
        )
    rows: list[str] = []
    for interval in intervals:
        advance = interval["interval_advance"]
        advance_text = (
            f"{advance['net_change_m']:.2f}"
            if advance.get("use_status") == "adoptable" and advance.get("net_change_m") is not None
            else "待核实"
        )
        rows.append(
            "<tr>"
            f"<td class='center nowrap'>{interval['start_time'][11:19]}～{interval['end_time'][11:19]}</td>"
            f"<td class='center nowrap'>{_duration(interval['working_duration_seconds'])}</td>"
            f"<td class='center'>{advance_text}</td>"
            f"<td>{html.escape(interval['reader_description'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _changes_html(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return '<p class="note">现有冻结动态分析结果未筛选出需要单列的代表性施工变化；这不等同于设备运行完全正常。</p>'
    items: list[str] = []
    for change in changes:
        parameter_text: list[str] = []
        for parameter in change["parameter_changes"]:
            initial = _value(parameter["initial_median"])
            final = _value(parameter["final_median"])
            delta = _signed_value(parameter["absolute_change"])
            trend = parameter["trend_display"]
            fluctuation = "并伴持续波动" if parameter["persistent_fluctuation"] else ""
            parameter_text.append(
                f"{parameter['display_name']}{trend}{fluctuation}（{initial}→{final}，变化{delta}）"
            )
        text = (
            f"{change['time_display']}，持续约{_duration(change['duration_seconds'])}："
            + "；".join(parameter_text)
            + "。"
        )
        items.append(f"<li>{html.escape(text)}</li>")
    return '<ol class="changes">' + "".join(items) + "</ol><p class='note'>变化量按事件范围首四分之一与末四分之一有效样本的中位数复算，均为原始PLC记录值；仅描述观测到的机器响应，不据此推断地质、刀具或设备故障原因。</p>"


def _key_geology_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return (
            "<tr><td class='center' colspan='3'>"
            "本日V2内容计划未选出需要在日报中单列的关键地质信息。"
            "</td></tr>"
        )
    rows: list[str] = []
    for item in items:
        refs = "、".join(f"[{value}]" for value in item.get("source_refs", [])) or "—"
        rows.append(
            "<tr>"
            f"<td class='center'>{html.escape(item['chainage'])}</td>"
            f"<td>{html.escape(item['text'])}</td>"
            f"<td class='center'>{html.escape(refs)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _geology_review_note(review_items: list[dict[str, Any]]) -> str:
    if not review_items:
        return ""
    return (
        '<div class="note warning"><strong>待核实：</strong>'
        "关联的原始素描资料存在里程文字异常。程序未擅自修正，"
        "相关范围未用于空间判断；具体证据保留在审计JSON中。</div>"
    )


def _source_notes(references: list[dict[str, Any]]) -> str:
    rows = []
    for source in references:
        page = f"第{source['source_page']}页" if source.get("source_page") else "页码待核实"
        date = source.get("availability_basis_date")
        date_text = f"，可用日期依据{date}" if date else ""
        rows.append(
            f"<li>[{html.escape(source['source_ref'])}] {html.escape(source['source_type'].upper())}："
            f"{html.escape(source.get('source_file') or '源文件待核实')}，{page}{html.escape(date_text)}。</li>"
        )
    return "".join(rows) or "<li>本日无可列示的地质来源。</li>"


def _duration(seconds: float) -> str:
    value = int(round(float(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{secs:02d}秒"
    return f"{minutes}分{secs:02d}秒"


def _value(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.1f}"
    return f"{number:.2f}"


def _signed_value(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:+,.1f}"
    return f"{number:+.2f}"


def _percent(value: Any) -> str:
    return "—" if value is None else f"{100.0 * float(value):.1f}%"


def _dyk(value: str) -> str:
    return str(value).replace("DK", "DyK", 1)
