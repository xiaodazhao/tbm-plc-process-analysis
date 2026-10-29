from __future__ import annotations

from pathlib import Path
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, Rectangle

from tbm_process.final_reporting.common import format_chainage


def select_trace_examples(graph, day):
    claims = [n for n in graph.typed("ReportClaim") if n["data"]["report_date"] == day and n["data"]["selected_for_report"]]
    claims.sort(key=lambda n:(n["data"]["chainage_range_m"],n["data"]["claim_type"],n["id"]))
    result = []
    for prefix in ("forecast_disagreement", "geology_plc_response_comparison:", "ahead_risk"):
        match = next((n for n in claims if n["data"]["claim_type"].startswith(prefix)), None)
        if match:
            result.append(match)
    result += [n for n in claims if n not in result]
    return [graph.trace(n["id"]) for n in result[:3]]


def draw_graph(graph, traces, output):
    font = Path("/System/Library/Fonts/STHeiti Light.ttc")
    if font.is_file():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    types = ["GeologyDocument", "GeologyFact", "ConstructionSegment", "PLCResponse", "EngineeringComparison", "ReportClaim"]
    titles = ["来源文档", "地质事实", "名义里程区段", "PLC响应", "工程对照", "日报陈述"]
    colors = ["#dde9e5", "#e3e8f0", "#ebe8df", "#deedf1", "#f0e3e1", "#e8e2ef"]
    chosen = {n["id"] for trace in traces for n in trace["nodes"]}
    if not chosen:
        context = graph.metadata["daily_contexts"][output.parent.name]
        chosen = {context["day_segment_id"]}
    groups = [[graph.nodes[i] for i in sorted(chosen) if graph.nodes[i]["type"] == kind] for kind in types]
    rows = max(2, max(map(len,groups)))
    fig, ax = plt.subplots(figsize=(11.8, max(7, rows*1.4+1)))
    ax.set_xlim(-.2, 6*3.2-.25)
    ax.set_ylim(-.8, rows+.8)
    ax.axis("off")
    positions, labels = {}, {}
    for col, group in enumerate(groups):
        ax.text(col*3.2+1.25, rows+.3, titles[col], ha="center", va="center", fontsize=13, weight="bold")
        for row, node in enumerate(group):
            x, y = col*3.2, rows-1-row-(rows-len(group))/2
            positions[node["id"]] = (x+1.25,y+.3)
            data = node["data"]
            if node["type"] == "GeologyDocument":
                label = data["document_type"]+"\n"+str(data["metadata"].get("submission_date") or data["metadata"].get("observation_date") or data["metadata"].get("available_date"))
            elif node["type"] == "GeologyFact":
                label = {"rock_grade":"围岩等级", "geological_description":"原报告描述", "risk":"预测事项"}.get(data["fact_type"],data["fact_type"])
                value = data["value"] if data["fact_type"] in {"rock_grade","risk"} else data["information_role"]
                label += "\n"+str(value)+"\n"+(f"原页{data['source_page']}" if data['source_page'] is not None else "JSON原字段")
            elif node["type"] == "ConstructionSegment":
                label = format_chainage(data["start_m"])+"\n至"+format_chainage(data["end_m"])
            elif node["type"] == "PLCResponse":
                label = "合格样本"+str(data["effective_sample_count"])+"条\n"+format_chainage(data["start_m"])+"至\n"+format_chainage(data["end_m"])
            elif node["type"] == "EngineeringComparison":
                kind = data["comparison_type"]
                label = {"geological_forecast_disagreement":"来源等级对照", "geology_plc_response_comparison":"地质—机器对照", "forecast_observation_comparison":"预报—素描对照"}.get(kind,"区段对照")+"\n"+{"observed_change":"达到描述性变化判据", "explicit_reported_grade_difference":"明确等级差异"}.get(data["result"],"描述性结果，非因果")
            else:
                kind = data["claim_type"]
                name = "机器响应对照" if kind.startswith("geology_plc_response") else "预报等级差异" if kind=="forecast_disagreement" else "前方预测事项" if kind=="ahead_risk" else "原资料属性对照"
                lo,hi = data["chainage_range_m"]
                label = "陈述"+str(next(i+1 for i,t in enumerate(traces) if t["root_id"]==node["id"]))+"\n"+name+"\n"+format_chainage(lo)+("\n至"+format_chainage(hi) if lo!=hi else "点位")
            label = "\n".join("\n".join(textwrap.wrap(line,width=8)) if sum("\u4e00"<=c<="\u9fff" for c in line)>8 else line for line in label.split("\n"))
            labels[node["id"]] = label
            ax.add_patch(Rectangle((x,y-.12),2.5,.85, facecolor=colors[col],edgecolor="#8a9696",lw=.7,zorder=3))
            ax.text(x+1.25,y+.3,label,ha="center",va="center",fontsize=12,zorder=4)
    edges = {}
    for trace in traces:
        for edge in trace["edges"]:
            edges[edge["id"]] = edge
    for edge in edges.values():
        a, b = edge["source"], edge["target"]
        if a not in positions or b not in positions:
            continue
        start, end = positions[a], positions[b]
        sign = 1 if end[0] > start[0] else -1
        start = (start[0]+sign*1.27,start[1])
        end = (end[0]-sign*1.27,end[1])
        ax.add_patch(FancyArrowPatch(start,end,arrowstyle="-|>",mutation_scale=8,color="#94a0a3",lw=.65,alpha=.7,zorder=1,
                                     connectionstyle="arc3,rad=0.025"))
    ax.text(0,-.6,"连线仅表示原文包含、空间对应、计算输入或陈述支持；不表示致因或预报已被证实。完整方向和关系类型见同目录JSON。",fontsize=10)
    fig.tight_layout()
    png, svg = output.with_suffix(".png"), output.with_suffix(".svg")
    fig.savefig(png,dpi=170,bbox_inches="tight")
    fig.savefig(svg,bbox_inches="tight")
    plt.close(fig)
    return {"png": str(png), "svg": str(svg), "shown_node_ids": sorted(chosen), "shown_edge_ids": sorted(edges),
            "complete_graph_nodes": len(graph.nodes), "complete_graph_edges": len(graph.edges),
            "selection": "trace_three_real_claims_not_entire_network", "labels": labels}
