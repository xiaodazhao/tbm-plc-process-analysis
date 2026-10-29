from __future__ import annotations

import html
import math
from pathlib import Path
import re
import shutil
from typing import Any

import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

from tbm_process.final_reporting.common import format_chainage
from tbm_process.final_reporting.plots import configure_chinese_font
from tbm_process.final_reporting.rendering import export_pdf

from .facts import digest, duration, write_json
from .narrative import TITLES


def engineering_styles():
    return """@page{size:A4;margin:14mm 14mm 16mm}*{box-sizing:border-box}
body{font-family:'PingFang SC','Heiti SC','Microsoft YaHei',sans-serif;color:#222;font-size:10pt;line-height:1.65;margin:0}
h1{font-size:20pt;margin:0 0 4mm}h2{font-size:14pt;margin:6mm 0 3mm;border-bottom:1px solid #678980;padding-bottom:2mm;break-after:avoid}
h3{font-size:11pt;break-after:avoid;margin:4mm 0 2mm}p{margin:2.5mm 0;orphans:3;widows:3}
figure{margin:4mm 0;break-inside:avoid}figure img{display:block;width:100%;height:auto}figcaption{font-size:8pt;color:#555}
.mode{font-size:9pt;color:#51666a;background:#f0f4f2;padding:2mm}.note{font-size:8.5pt;color:#555}
.warning{color:#7c4234}.sources{font-size:8pt;overflow-wrap:anywhere}footer{font-size:8pt;color:#555;margin-top:5mm}"""


def _fmt(value, digits=2):
    return "不足/缺失" if value is None or not math.isfinite(float(value)) else f"{float(value):.{digits}f}"


def _table(headers, rows, widths=None):
    colgroup = "" if not widths else "<colgroup>"+"".join(f"<col style='width:{w}%'>" for w in widths)+"</colgroup>"
    return "<table>"+colgroup+"<thead><tr>"+"".join(f"<th>{html.escape(str(x))}</th>" for x in headers)+"</tr></thead><tbody>"+"".join("<tr>"+"".join(f"<td>{html.escape(str(x))}</td>" for x in row)+"</tr>" for row in rows)+"</tbody></table>"


def _clock_axis(axes, day):
    start = pd.Timestamp(day)
    for ax in axes:
        ax.set_xlim(start, start+pd.Timedelta(days=1))
        ax.grid(color="#dde3e4", lw=.45)
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def render_figures(package, samples, output, *, basic_only=False):
    configure_chinese_font()
    for font_path in ["/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/PingFang.ttc", "C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if Path(font_path).is_file():
            font_manager.fontManager.addfont(font_path)
            family = font_manager.FontProperties(fname=font_path).get_name()
            plt.rcParams["font.family"] = family
            plt.rcParams["font.sans-serif"] = [family, "DejaVu Sans"]
            break
    figures = output/"figures"
    figures.mkdir(parents=True, exist_ok=True)
    day = package["date"]
    evidence = {"platform_rectangles": [{"start_time": p["start_time"], "end_time": p["end_time"]} for p in package["platforms"]],
                "units": package["config"]["units"], "curve_source_sha256": package["source"]["samples_sha256"],
                "interpolation": False, "curves_are_pointwise_not_platform_medians": True}
    colors = {"advancing": "#4d8d87", "stopped": "#d8dddd", "other": "#c4b979"}
    fig, ax = plt.subplots(figsize=(11, 1.5), constrained_layout=True)
    origin = pd.Timestamp(day)
    for state, block in samples.groupby("machine_state", sort=False):
        starts = mdates.date2num(block.time)
        widths = block.sample_dt_seconds.fillna(0).to_numpy()/86400
        ax.broken_barh(list(zip(starts, widths)), (0.1, .32), color=colors.get(state, colors["other"]), linewidth=0)
    for p in package["platforms"]:
        start, end = map(pd.Timestamp, (p["start_time"], p["end_time"]))
        ax.broken_barh([(mdates.date2num(start), (end-start).total_seconds()/86400)], (.63, .25), color="#7392ac")
    ax.set_yticks([.25, .75], ["运行状态", "识别运行区间"])
    ax.set_ylim(0, 1)
    _clock_axis([ax], day)
    ax.set_title("全天状态与运行区间（推进：绿；停止：灰；其他已观测状态：黄；缺采留白）", fontsize=10)
    fig.savefig(figures/"timeline.png", dpi=200, facecolor="white")
    plt.close(fig)
    fields = [("advance_speed", "推进速度", "mm/min", "#28779b"), ("thrust", "总推力", "kN", "#81629a"),
              ("torque", "刀盘扭矩", "kN·m", "#bd6846"), ("rpm_canonical", "刀盘实际转速", "r/min", "#408779")]
    fig, axes = plt.subplots(4, 1, figsize=(11, 5.5), sharex=True)
    gap = samples.time.diff().dt.total_seconds().gt(package["config"]["analysis"]["hard_gap_seconds"])
    for ax, (column, name, unit, color) in zip(axes, fields):
        for p in package["platforms"]:
            ax.axvspan(pd.Timestamp(p["start_time"]), pd.Timestamp(p["end_time"]), color="#cbdcd7", alpha=.35, lw=0, zorder=0)
        values = pd.to_numeric(samples[column], errors="coerce").where(lambda s: s.ge(0))
        if column == "advance_speed":
            values = values.where(samples.advance_speed_quality.eq("usable"))
        if column == "rpm_canonical":
            values = values.where(samples.rpm_quality.eq("usable_provisional_mapping"))
        values = values.where(~gap)
        ax.plot(samples.time, values, color=color, lw=.65, zorder=2)
        ax.set_ylabel(f"{name}\n{unit}", fontsize=9)
    _clock_axis(axes, day)
    axes[0].set_title("当日实际PLC参数（背景为识别运行区间；无效值与缺口不插值）", fontsize=11)
    fig.tight_layout()
    fig.savefig(figures/"parameters.png", dpi=220, facecolor="white")
    plt.close(fig)
    if basic_only:
        write_json(output/"figures.provenance.json", evidence)
        return evidence
    if samples.performance_eligible.any():
        fig, axes = plt.subplots(3, 1, figsize=(11, 3.6), sharex=True)
        for ax, field, label, unit in zip(axes, ["pr", "fpi", "se"], ["每转贯入度PR", "总推力口径FPI", "理论机械比能SE"], ["mm/r", "kN/(把·mm)", "MJ/m³"]):
            ax.plot(samples.time, samples[field], color="#497b8b", lw=.65)
            ax.set_ylabel(f"{label}\n{unit}", fontsize=9)
        _clock_axis(axes, day)
        axes[0].set_title("合格稳定推进样本的性能指标（断线表示无合格样本，不代表停机）", fontsize=10)
        fig.tight_layout()
        fig.savefig(figures/"performance.png", dpi=220, facecolor="white")
        plt.close(fig)
    g = package["geology"]
    ranges = g["day_range_m"]+(g["ahead_range_m"] or [])
    nearby = [o for o in g["selected_observations"] if abs(o["point_m"]-g["shield_end_m"]) < 100]
    ranges += [o["point_m"] for o in nearby]
    base = math.floor(min(ranges)/1000)*1000
    left, right = min(ranges)-2, max(ranges)+2
    if right-left < 5:
        right = left+5
    fig, ax = plt.subplots(figsize=(11, 2.9), constrained_layout=True)
    for y, kind, color in [(2, "tsp", "#7893b0"), (1, "hsp", "#b5a15f")]:
        for record in g["forecast_records"].values():
            if record["source_type"] != kind:
                continue
            lo, hi = max(left, record["start_m"]), min(right, record["end_m"])
            if lo < hi:
                ax.broken_barh([(lo-base, hi-lo)], (y-.20, .36), color=color, alpha=.8, edgecolor="white")
    ax.broken_barh([(g["day_range_m"][0]-base, g["day_range_m"][1]-g["day_range_m"][0])], (3-.20, .36), color="#4d8d87")
    if g["ahead_range_m"]:
        ax.axvspan(g["ahead_range_m"][0]-base, g["ahead_range_m"][1]-base, color="#d9e6e1", alpha=.4)
    for o in nearby:
        ax.scatter(o["point_m"]-base, 0, marker="D", color="#a06574", s=30)
    for risk in g["ahead_risks"]:
        lo, hi = risk["display_range_m"]
        if lo == hi:
            ax.scatter(lo-base, -0.65, marker="^", s=35, color="#b4773d")
        else:
            ax.broken_barh([(lo-base, hi-lo)], (-.82, .25), color="#b4773d", alpha=.6)
    ax.axvline(g["shield_end_m"]-base, color="#444", lw=.8, linestyle="--")
    ax.set_yticks([3, 2, 1, 0, -.7], ["当日盾首范围", "TSP预测", "HSP预测", "历史/已核实素描点", "前方预测风险"])
    ax.set_xlim(left-base, right-base)
    ax.set_ylim(-1.1, 3.6)
    ax.set_xlabel(f"DyK{base//1000}+ 相对米数；阴影为盾首参考前方窗口")
    ax.set_title("施工里程与地质资料对应（预测、观察性质不同；不作因果解释）", fontsize=10)
    ax.grid(axis="x", color="#dde3e4", lw=.5)
    fig.savefig(figures/"geology_axis.png", dpi=220, facecolor="white")
    plt.close(fig)
    evidence["geology_bounds_m"] = [left, right]
    evidence["ahead_range_m"] = g["ahead_range_m"]
    write_json(output/"figures.provenance.json", evidence)
    return evidence


def extract_sketch_images(package, output):
    images, failures = [], []
    target = output/"sketches"
    target.mkdir(exist_ok=True)
    for i, record in enumerate(package["geology"]["selected_observations"], 1):
        source = record["source"]
        path = Path(source["path"]) if source.get("path") else None
        if path is None or not path.is_file():
            failures.append({"record_id": record["record_id"], "reason": "original_pdf_missing"})
            continue
        if digest(path) != source["sha256"]:
            failures.append({"record_id": record["record_id"], "reason": "source_sha_mismatch"})
            continue
        try:
            with fitz.open(path) as doc:
                page_index = int(source["page"])-1
                if not 0 <= page_index < len(doc):
                    raise ValueError("source_page_out_of_bounds")
                # Render the complete original page, preserving symbols and text.
                image_path = target/f"sketch_{i:02d}.png"
                page = doc[page_index]
                page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False).save(image_path)
                images.append({"record_id": record["record_id"], "image": str(image_path), "source": source,
                               "observation_date": record["observation_date"], "point_m": record["point_m"],
                               "role": record["reporting_role"], "aspect_ratio": page.rect.width/page.rect.height,
                               "method": "whole_original_pdf_page_render_no_AI_no_symbol_edit"})
        except Exception as exc:
            failures.append({"record_id": record["record_id"], "reason": type(exc).__name__})
    result = {"images": images, "unavailable": failures}
    write_json(output/"sketch_images.json", result)
    return result


def render_html(package, narrative, image_info, output, provenance, *, layout="six"):
    if layout == "three":
        from .three_chapter_rendering import render_three_html
        return render_three_html(package, narrative, image_info, output, provenance)
    g = package["geology"]
    comparison_cn = {"explicit_reported_grade_difference": "原报告等级明确不同", "description_difference_needs_semantic_review": "描述不同，语义待核查",
                     "partially_consistent": "部分可比属性一致", "consistent_in_compared_attributes": "已比较属性一致", "indeterminate": "无法判断",
                     "same_literal_attributes": "原文属性相同", "different_descriptions": "原文描述不同", "source_only_or_unknown": "一方独有/另一方未说明", "not_comparable": "均未明确，无法比较"}
    attribute_cn = {"grade": "等级", "lithology": "岩性", "integrity": "岩体完整性", "weathering": "风化", "water": "地下水", "joints": "节理", "special": "特殊描述"}
    refs = {}
    def ref(source):
        key = source["report_id"]
        if key not in refs:
            refs[key] = {"number": len(refs)+1, **source}
        return f"[{refs[key]['number']}] 第{source['page']}页"
    def paragraphs(chapter):
        c = next(x for x in narrative["chapters"] if x["chapter_id"] == chapter)
        return "".join(f"<p>{html.escape(x['text'])}</p>" for x in c["claims"])
    def figure(name, caption):
        p = output/"figures"/name
        return f"<figure><img src='{p.as_uri()}'><figcaption>{html.escape(caption)}</figcaption></figure>" if p.exists() else ""
    def interval_rows(platforms):
        rows = []
        for p in platforms:
            advance = p["interval_advance"]
            s = p["performance"]["statistics"]
            usable = p["performance"]["status"] != "insufficient"
            rows.append([p["start_time"][11:19]+"至"+p["end_time"][11:19], duration(p["working_duration_seconds"]),
                         _fmt(advance["net_change_m"]) if advance["use_status"] == "adoptable" else "待核实",
                         _fmt(s["pr"]["median"]) if usable else "不足", _fmt(s["fpi"]["median"]) if usable else "不足",
                         _fmt(s["se"]["median"]) if usable else "不足", p["performance"]["sample_count"]])
        return rows
    headers = ["运行起止", "实际工作时长", "累计字段进尺m", "PR mm/r", "FPI kN/(把·mm)", "SE MJ/m³", "性能样本数"]
    selected = [p for p in package["platforms"] if p["platform_id"] in package["selected_platform_ids"]]
    parameters = package["daily_parameter_statistics"]
    parameter_rows = []
    for name, label in [("advance_speed", "推进速度"), ("thrust", "总推力"), ("torque", "刀盘扭矩"), ("rpm_canonical", "刀盘实际转速")]:
        s = parameters[name]
        parameter_rows.append([label, s["unit"], s["count"], _fmt(s["median"]), _fmt(s["p10"])+"至"+_fmt(s["p90"]), _fmt(s["valid_ratio"]*100) + "%" if s["valid_ratio"] is not None else "不足"])
    geology_rows = []
    for part in g["day_partitions"]:
        place = format_chainage(part["start_m"])+"至"+format_chainage(part["end_m"])
        if not part["record_ids"]:
            geology_rows.append([place, "无合格预报", "未补造地质内容"])
        for identity in part["record_ids"]:
            r = g["forecast_records"][identity]
            geology_rows.append([place, r["source_type"].upper()+" "+ref(r["source"]), r["text"]])
    joint_rows = []
    status_cn = {"observed_change": "达到比较变化判据", "criterion_not_met": "未达到比较变化判据", "insufficient_data": "数据/可比参考不足"}
    for row in g["joint_analysis"]:
        s = row["summary"]["statistics"]
        ok = row["summary"]["status"] != "insufficient"
        joint_rows.append([format_chainage(row["range_m"][0])+"至"+format_chainage(row["range_m"][1]), row["summary"]["sample_count"],
                           _fmt(s["pr"]["median"]) if ok else "不足", _fmt(s["fpi"]["median"]) if ok else "不足",
                           _fmt(s["se"]["median"]) if ok else "不足", status_cn[row["response_comparison"]["status"]]])
    difference_rows = []
    for part in g["day_partitions"]:
        for c in part["comparisons"]:
            left, right = g["forecast_records"][c["left_id"]], g["forecast_records"][c["right_id"]]
            changed = [key for key, value in c["attributes"].items() if value["status"] == "different_descriptions"]
            if changed:
                labels = {"grade": "等级", "lithology": "岩性", "integrity": "破碎/完整性", "weathering": "风化", "water": "水文", "joints": "节理", "special": "特殊描述"}
                difference_rows.append([format_chainage(part["start_m"])+"至"+format_chainage(part["end_m"]), ref(left["source"])+" / "+ref(right["source"]),
                                        "；".join(labels[k]+"："+"、".join(c["attributes"][k]["left"])+" / "+"、".join(c["attributes"][k]["right"]) for k in changed)])
    sketch_html = ""
    for obs_index, row in enumerate(g["selected_observations"]):
        attrs = row["attributes"]
        sketch_html += f"<h3>{html.escape(row['observation_date'])} {'当日' if row['reporting_role']=='same_day_observation' else '历史'}记录，{format_chainage(row['point_m'])} {ref(row['source'])}</h3>"
        if obs_index == 0:
            sketch_html += "<p>"+html.escape(row["text"] or "原文含推测或建议，未自动转成现场观察。")+"</p>"
            sketch_html += _table(["属性", "原文明确载明的内容"], [[attribute_cn[k], "、".join(v["values"]) or "未明确，不等于无"] for k,v in attrs.items()], [22, 78])
        else:
            sketch_html += "<p class='note'>该历史记录的原图与完整属性保存在附录及事实包，正文仅作有信息增量的属性对照。</p>"
        if row["grade_conflict"]:
            sketch_html += "<p class='warning'>原记录等级字段与正文存在差异，未自动选定唯一等级。</p>"
    comparison_rows = []
    for c in g["forecast_observation_comparisons"]:
        forecast, observation = c["forecast"], c["observation"]
        comparison_rows.append([format_chainage(observation["point_m"]), ref(forecast["source"])+" / "+ref(observation["source"]),
                                "原报告等级："+"、".join(c["attributes"]["grade"]["left"])+" / "+"、".join(c["attributes"]["grade"]["right"]), comparison_cn[c["status"]]])
    if g["observation_comparisons"]:
        sketch_html += "<h3>已有素描描述对照</h3>"
        for c in g["observation_comparisons"]:
            sketch_html += "<p>"+html.escape(f"{c['left']['observation_date']} {format_chainage(c['left']['point_m'])}与{c['right']['observation_date']} {format_chainage(c['right']['point_m'])}对照；属于不同观察位置的资料描述比较，不自动判定真实地质变化。")+"</p>"
            changed = [[attribute_cn[k], "、".join(v["left"]) or "未明确", "、".join(v["right"]) or "未明确", comparison_cn[v["status"]]] for k,v in c["attributes"].items() if v["status"] == "different_descriptions"]
            sketch_html += _table(["属性", "此前记录", "本次引用记录", "比较状态"], changed) if changed else "<p class='note'>已有可比原文属性未见明确差异；未记载的属性不当作一致或不存在。</p>"
    ahead_rows = []
    for part in g["ahead_partitions"]:
        place = format_chainage(part["start_m"])+"至"+format_chainage(part["end_m"])
        if not part["record_ids"]:
            ahead_rows.append([place, "无有效预报", "未将空白推断为良好地质"])
        for identity in part["record_ids"]:
            r = g["forecast_records"][identity]
            ahead_rows.append([place, r["source_type"].upper()+" "+ref(r["source"]), r["text"]])
    risk_rows = []
    for row in g["ahead_risks"]:
        risk_rows.append([row.get("risk_type", "原报告风险"), "至".join(format_chainage(v) for v in row["display_range_m"]),
                          "至".join(_fmt(v)+"m" for v in row["signed_distances_from_shield_end_m"]), ref(row["source"])])
    if g["ahead_updates"].get("reference_date"):
        u = g["ahead_updates"]
        update_text = f"相对{u['reference_date']}窗口，新进入或新可用预测记录{len(u['newly_visible_record_ids'])}条，继续覆盖记录{len(u['retained_record_ids'])}条；完整当前意见仍保留，不因去重删除风险。"
    else:
        update_text = "未取得合格前一日参考，未判断前方窗口更新情况。"
    # All source references are collected before assembling the final source table.
    image_html = ""
    for image in image_info["images"]:
        ref(image["source"])
        image_html += f"<section class='sourcepage'><h2>附录B 原始地质素描</h2><p class='caption'>{html.escape(image['observation_date'])} 历史/已核验记录，{format_chainage(image['point_m'])}，{ref(image['source'])}<br>{html.escape(image['source']['original_filename'])}</p><img class='sourceimage' src='{Path(image['image']).as_uri()}'><p class='caption'>原PDF整页等比例渲染，未改写图中符号。原页尺寸等载值不作为本工程TBM设备参数。</p></section>"
    source_rows = [[f"[{r['number']}]", r["original_filename"], r["availability"].get("document_date", "待核实") if r.get("availability") else "待核实", r["sha256"][:16]+"…"] for r in refs.values()]
    mode_text = "离线完整核验稿：正文由确定性程序生成，未调用LLM" if provenance["mode"] != "real_llm_candidate_requires_semantic_review" else "真实LLM候选稿：完整语义仍待工程审查"
    notices = [f"性能采用既有稳定标签、推进状态与质量交集；排除异常负推力{package['performance']['quality']['excluded_negative_thrust_count']}条。",
               "新指标单位依据用户2026-10-27确认；其余字段不自动获得单位认证。",
               "地质资料先时间准入再里程匹配；同日无可用时刻的素描不当作当日揭露。",
               "导向盾首与掌子面偏移未知；图中空间对应不是因果关系。",
               "性能分母保护和比较门限是本版实验参数，不是安全报警限值。"]
    css = """@page{size:A4;margin:14mm 14mm 16mm}*{box-sizing:border-box}body{font-family:'PingFang SC','Heiti SC','Microsoft YaHei',sans-serif;color:#222;font-size:10pt;line-height:1.65;margin:0}h1{font-size:20pt;margin:0 0 4mm}h2{font-size:14pt;margin:7mm 0 3mm;border-bottom:1px solid #678980;padding-bottom:2mm;break-after:avoid}h3{font-size:11pt;break-after:avoid;margin:4mm 0 2mm}p{margin:2.5mm 0;orphans:3;widows:3}table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:8.3pt;margin:3mm 0 5mm}th,td{border:1px solid #c8d2d2;padding:2mm 1.5mm;overflow-wrap:anywhere;vertical-align:top}th{background:#e9f0ed;font-weight:600}thead{display:table-header-group}tr{break-inside:avoid}figure{margin:4mm 0;break-inside:avoid}figure img{width:100%;height:auto}figcaption,.caption{font-size:8pt;color:#555}.mode{font-size:9pt;color:#51666a;background:#f0f4f2;padding:2mm}.note{font-size:8.5pt;color:#555}.warning{color:#7c4234}.chapter{break-before:page}.sourcepage{break-before:page;break-inside:avoid}.sourceimage{display:block;max-width:100%;max-height:225mm;width:auto;height:auto;margin:2mm auto}.sourcepage h2{margin-top:0}.small{font-size:8pt}footer{font-size:8pt;color:#555;margin-top:5mm}"""
    css += "table{break-inside:avoid}h2{margin-top:5mm}figure{margin:2.5mm 0}body{font-size:9.5pt}.chapter{break-before:auto}"
    body = f"<h1>{package['date']} TBM施工日报</h1><div class='mode'>{mode_text}</div>"
    body += "<h2>一、施工总体情况</h2>"+paragraphs(1)
    body += "<h2>二、主要施工过程与TBM运行分析</h2>"+paragraphs(2)
    body += _table(headers, interval_rows(selected), [22, 16, 12, 11, 15, 12, 12])+"<p class='note'>各段进尺用开累进尺首末差；不是导向盾首日进尺，不能直接相加代替日总进尺。工作时长排除段内停止记录；性能仅用合格稳定推进样本。</p>"
    body += _table(["参数", "单位", "有效记录", "中位数", "P10至P90", "有效比例"], parameter_rows)+"<p class='note'>本表范围为全天非停止记录，剔除既有速度/转速不可信值、非有限值及负推力；与性能稳定样本范围不同。</p>"
    windows = [w for w in package["performance"]["windows"] if w["platform_id"] in package["selected_platform_ids"]][:4]
    if windows:
        body += "<h3>连续稳定窗口的变化与波动</h3>"+_table(["实际窗口", "扭矩CV", "速度趋势 (mm/min)/min", "扭矩趋势 kN·m/min", "PR首末窗口差 mm/r"],
                    [[w["start_time"][11:19]+"至"+w["end_time"][11:19], _fmt(w["torque_cv"],3), _fmt(w["parameter_changes"]["advance_speed"]["slope_per_minute"]),
                      _fmt(w["parameter_changes"]["torque"]["slope_per_minute"]), _fmt(w["parameter_changes"]["pr"]["absolute_change"])] for w in windows])
        body += "<p class='note'>按时间列示前4个合格连续窗口，不按异常分数挑选；CV=同窗口样本标准差/均值，趋势复用真实时间稳健斜率。PR差为窗口末、首四分之一中位数之差，不是按时间变化率。</p>"
    body += figure("timeline.png", "图1 全天真实运行状态与识别区间。缺测不归入非推进或停机。")+figure("parameters.png", "图2 实际逐点参数。浅背景严格采用既有运行区间。")+figure("performance.png", "图3 合格逐点性能计算；未插值；不以原始贯入度字段替代PR。")
    body += "<section class='chapter'><h2>三、当日施工区段地质分析</h2>"+paragraphs(3)
    body += _table(["名义里程交集", "预测来源", "原报告预测描述"], geology_rows, [24, 20, 56]) if geology_rows else "<p>当日范围没有通过时间及空间检查的有效预报。</p>"
    if difference_rows:
        body += "<h3>不同来源意见</h3>"+_table(["适用范围", "来源", "明确描述差异（不选择正确一方）"], difference_rows, [24, 22, 54])
    if g["design_records"]:
        body += "<h3>经来源核对的设计资料</h3>"+_table(["原设计范围", "原文", "对应预报比较"],
                     [[format_chainage(r["start_m"])+"至"+format_chainage(r["end_m"]), r["source_quote"], "；".join(comparison_cn[c["status"]] for c in r["comparisons"]) or "无可比预报"] for r in g["design_records"]])
    body += "<h3>预报范围内的实际机器响应</h3>"+_table(["名义区段", "合格样本", "PR mm/r", "FPI kN/(把·mm)", "SE MJ/m³", "比较结论"], joint_rows)
    body += "<p class='note'>分段依原预报边界而非固定网格；样本按半开区间分配，日终点归最后一段。样本不覆盖的里程不插补。未满足参考工况与数据量条件时只列统计，不判断发生变化。</p>"+figure("geology_axis.png", "图4 多来源地质与施工位置对照；历史素描是点记录，不扩为整段观察。")+"</section>"
    body += "<section class='chapter'><h2>四、现场地质素描与揭露情况</h2>"+paragraphs(4)+sketch_html
    if comparison_rows:
        body += "<h3>与形成更早的预测对照</h3>"+_table(["观察位置", "预测/观察来源", "原记录等级", "属性比较结果"], comparison_rows)
    if image_info["images"]:
        body += "<p>原始素描整页图见附录B；保留图中全部文字和标记。</p>"
    if image_info["unavailable"]:
        body += "<p class='note'>部分原图未插入，具体缺失或校验原因保存在sketch_images.json；未生成替代图。</p>"
    body += f"</section><section class='chapter'><h2>五、前方地质预报（{package['config']['analysis']['ahead_metres']:g}m窗口）</h2>"+paragraphs(5)
    body += _table(["盾首参考前方交集", "来源", "原报告预测意见"], ahead_rows, [24, 20, 56]) if ahead_rows else "<p>前方方向或合格预测不足，没有补造预测。</p>"
    if risk_rows:
        body += "<h3>原报告明确提示的位置或区间</h3>"+_table(["预测事项", "实际位置/范围", "距盾首终点", "来源"], risk_rows)
    body += "<p>"+html.escape(update_text)+"</p></section><h2>六、主要关注事项</h2>"+paragraphs(6)
    body += "<h3>数据与解释边界</h3><ul>"+"".join("<li>"+html.escape(x)+"</li>" for x in notices)+"</ul>"
    body += "<h3>原始地质资料来源</h3>"+_table(["索引", "原始文件", "资料日期", "SHA-256前16位"], source_rows, [8, 58, 14, 20])
    body += "<section class='chapter'><h2>附录A 全部运行区间</h2>"+_table(headers, interval_rows(package["platforms"]), [22, 16, 12, 11, 15, 12, 12])+"<p class='note'>全部运行区间一个不少。源行号、阶段、排除样本和统计口径在report_facts.json、performance_samples.csv及performance_windows.json中完整保留。</p></section>"+image_html
    result = "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>TBM施工日报</title><style>"+css+"</style></head><body>"+body+"<footer>版本："+package["schema_version"]+"；报告为可审查候选，不自动生成施工处置指令。</footer></body></html>"
    path = output/f"{package['date']}.engineering_report.html"
    path.write_text(result, encoding="utf-8")
    write_json(output/"report_source_index.json", list(refs.values()))
    return path


def make_pdf(html_path, pdf_path, chrome=None):
    executable = chrome or next((Path(x) for x in [shutil.which("google-chrome"), shutil.which("chromium"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"] if x and Path(x).exists()), None)
    if executable is None:
        raise RuntimeError("Chrome/Chromium required; HTML and facts remain available")
    export_pdf(html_path, pdf_path, executable)


def validate_artifacts(package, narrative, figures, pdf_path, *, chapter_titles=None):
    checks = []
    def add(name, passed, details=None):
        checks.append({"name": name, "passed": bool(passed), "details": details})
    o, g = package["daily_overview"], package["geology"]
    add("state_duration_identity", abs(o["working_duration_seconds"]+o["stopped_duration_seconds"]-o["effective_state_duration_seconds"]) < 1e-6)
    add("advancing_complement_identity", abs(o["advancing_seconds"]+o["nonadvancing_observed_seconds"]-o["effective_state_duration_seconds"]) < 1e-6)
    add("daily_advance_unique_source", o["daily_advance"]["source_original_field"] == "导向盾首里程")
    add("platform_work_excludes_stop", all(abs(p["working_duration_seconds"]+p["stopped_duration_seconds_inside_platform"]-p["observed_duration_seconds_inside_platform"]) < 1e-6 for p in package["platforms"]))
    add("all_platforms_retained", len(package["platforms"]) == package["platform_count"])
    add("plot_platform_boundaries_identical", figures["platform_rectangles"] == [{"start_time": p["start_time"], "end_time": p["end_time"]} for p in package["platforms"]])
    add("ahead_exact_configured_width", g["ahead_range_m"] is None or abs(g["ahead_range_m"][1]-g["ahead_range_m"][0]-package["config"]["analysis"]["ahead_metres"]) < 1e-8)
    add("current_forecasts_predate_day", all(g["forecast_records"][i]["source"]["availability"]["document_date"] < package["date"] for p in g["day_partitions"] for i in p["record_ids"]))
    source_rows = [(s["source_file"], r) for j in g["joint_analysis"] for s in j["source"] for r in s["source_rows"]]
    add("joint_segments_do_not_double_count_samples", len(source_rows) == len(set(source_rows)))
    covered = sum(x["end_m"]-x["start_m"] for x in g["ahead_partitions"])
    add("ahead_partition_covers_window_without_gaps_or_overcount", g["ahead_range_m"] is None or abs(covered-package["config"]["analysis"]["ahead_metres"]) < 1e-8)
    with fitz.open(pdf_path) as doc:
        text = "".join(page.get_text() for page in doc)
        normalized = re.sub(r"\s+", "", text)
        missing = [c["text"] for ch in narrative["chapters"] for c in ch["claims"] if re.sub(r"\s+", "", c["text"]) not in normalized]
        add("checked_narrative_is_in_final_pdf", not missing, {"missing_claim_count": len(missing)})
        add("all_six_chapter_titles_in_pdf" if chapter_titles is None else "all_requested_chapter_titles_in_pdf", all(re.sub(r"\s+", "", title) in normalized for title in (TITLES if chapter_titles is None else chapter_titles)))
        add("pdf_no_empty_pages", all(len(page.get_text().strip()) > 0 for page in doc))
        outside = []
        for pi, page in enumerate(doc):
            for block in page.get_text("blocks"):
                if block[0] < -1 or block[1] < -1 or block[2] > page.rect.width+1 or block[3] > page.rect.height+1:
                    outside.append(pi+1)
        add("pdf_text_inside_page", not outside, sorted(set(outside)))
        pages = len(doc)
    return {"passed": all(c["passed"] for c in checks), "checks": checks, "pdf_pages": pages,
            "semantic_status": "requires_engineering_review_not_certified_accuracy", "pdf_sha256": digest(pdf_path)}
