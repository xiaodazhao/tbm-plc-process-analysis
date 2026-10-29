from __future__ import annotations

import html
from pathlib import Path
import shutil

from tbm_process.reporting_v2.facts import digest, duration, write_json
from tbm_process.reporting_v2.narrative import (build_writing_facts, offline_draft, offline_plan, check_draft, check_plan, requests_for)
from tbm_process.reporting_v2.rendering import render_html

from .spatial import format_chainage


def location(bounds):
    return format_chainage(bounds[0]) if bounds[0] == bounds[1] else format_chainage(bounds[0])+"—"+format_chainage(bounds[1])


def make_narrative(package, construction, observations, ahead, output):
    facts = [f for f in build_writing_facts(package) if f["chapter"] in (1, 2, 6)]
    for f in facts:
        if f["fact_id"] == "TIME":
            o = package["daily_overview"]
            f["canonical_text"] = f"设备累计工作{duration(o['working_duration_seconds'])}，停止{duration(o['stopped_duration_seconds'])}，工作时间利用率{o['utilization_percent']:.1f}%。"
        if f["fact_id"] == "PROCESS_SCOPE":
            f["canonical_text"] = f"当天记录{package['platform_count']}段运行过程，主要过程列于下表，全部明细见附录。"
        if f["fact_id"] == "PERFORMANCE" and package["performance"]["daily"]["status"] != "insufficient":
            d = package["performance"]["daily"]
            s = d["statistics"]
            f["canonical_text"] = (f"稳定推进性能统计采用{d['sample_count']}条记录，每转贯入度（PR）中位数为{s['pr']['median']:.2f}mm/r，"
                                   f"总推力口径贯入指数（FPI）中位数为{s['fpi']['median']:.2f}kN/(把·mm)，"
                                   f"理论机械比能（SE）中位数为{s['se']['median']:.2f}MJ/m³。")
        if f["chapter"] == 6:
            f["canonical_text"] = f["canonical_text"].replace("这是合格连续记录中的参数响应，未据此判定故障或地质原因。", "")
            if f["fact_id"] == "NO_ATTENTION":
                f["canonical_text"] = "现有记录未筛选出需单列的重点运行变化。"
    def add(fid, chapter, text, data):
        facts.append({"fact_id": fid, "chapter": chapter, "canonical_text": text, "data": data,
                      "source": {"database": "../../geology.sqlite", "application": "applications.json"},
                      "required": True, "nature": "deterministic_source_bound_not_LLM"})
    coverage = construction["coverage"]
    bounds = construction["query"]["range_m"]
    if coverage["union_m"]:
        add("STORE:DAY_SCOPE", 3, f"当日施工范围为{location(bounds)}，此前提交的TSP/HSP覆盖其中{coverage['union_m']:.2f}m。各来源预测分段及结论列于下表。", coverage)
    else:
        add("STORE:DAY_SCOPE", 3, "当日施工范围未查到此前已提交的TSP/HSP预报。", coverage)
    for i, c in enumerate(construction["forecast_comparisons"]):
        if c["status"] != "explicit_reported_grade_difference":
            continue
        values = c["attributes"]["grade"]
        docs = [next(d for d in construction["query"]["documents"] if d["document_id"] == c[k]) for k in ("left_document_id", "right_document_id")]
        add(f"STORE:GRADE:{i}", 3, f"{location(c['range_m'])}的预测等级存在差异：{docs[0]['document_type']}为{'、'.join(values['left'])}级，{docs[1]['document_type']}为{'、'.join(values['right'])}级。", c)
    for i, row in enumerate(construction["responses"]):
        s = row["summary"]
        if s["status"] == "insufficient":
            continue
        v = s["statistics"]
        text = (f"{location(row['range_m'])}对应的{s['sample_count']}条稳定推进记录中，推进速度、推力和刀盘扭矩中位数分别为"
                f"{v['advance_speed']['median']:.2f}mm/min、{v['thrust']['median']:.2f}kN和{v['torque']['median']:.2f}kN·m。")
        change = row["comparison"]
        if change["status"] == "observed_change":
            parameters = change.get("changes", {})
            selected = [(key, label) for key, label in (("advance_speed", "推进速度"), ("pr", "每转贯入度"), ("fpi", "总推力口径贯入指数"))
                        if parameters.get(key, {}).get("threshold_met")]
            if selected:
                text += "与此前可比运行区段相比，"+"，".join(
                    label+("增加" if parameters[key]["percent"] >= 0 else "降低")+f"{abs(parameters[key]['percent']):.2f}%" for key, label in selected)+"。"
        add(f"STORE:PLC:{i}", 3, text, row)
    if observations["records"]:
        add("STORE:NEW_SKETCH", 4, f"当天新增{len(observations['records'])}份素描记录，以下按原记录列示施工分级、明确位置的观察及此前预报。日期采用报告可用日期口径，实际观察日期未单独记载。", observations)
    else:
        add("STORE:NEW_SKETCH", 4, "当天未查到新增素描记录。", observations)
    a = ahead["coverage"]
    add("STORE:AHEAD", 5, f"以当日导向盾首终点为参照，前方{ahead['distance_m']:g}m范围为{location(a['range_m'])}，此前TSP/HSP覆盖{a['union_m']:.2f}m。"+
        ("尚无预报的范围保留在下表。" if a["gap_m"] else "已有预测分段及风险位置列于下表。"), ahead)
    plan = offline_plan(facts)
    narrative = offline_draft(plan, facts)
    pc, nc = check_plan(plan, facts), check_draft(narrative, plan, facts, True)
    assert pc["passed"] and nc["passed"]
    pr, gr = requests_for(facts, plan, "deepseek-chat")
    for name, data in [("writing_facts.json", facts), ("content_plan.json", plan), ("narrative.json", narrative),
                       ("plan.validation.json", pc), ("narrative.validation.json", nc),
                       ("plan.request.NOT_SENT.json", pr), ("generation.request.NOT_SENT.json", gr)]:
        write_json(output/name, data)
    return narrative


def table(headers, rows):
    if not rows:
        return ""
    cells = lambda tag, values: "".join("<"+tag+">"+html.escape(str(x))+"</"+tag+">" for x in values)
    return "<table><thead><tr>"+cells("th", headers)+"</tr></thead><tbody>"+"".join("<tr>"+cells("td", r)+"</tr>" for r in rows)+"</tbody></table>"


def render_report(package, narrative, construction, observations, ahead, output):
    path = render_html(package, narrative, {"images": [], "unavailable": []}, output, {"mode": "offline_no_API"})
    value = path.read_text()
    def paragraphs(chapter):
        return "".join("<p>"+html.escape(c["text"])+"</p>" for c in narrative["chapters"][chapter-1]["claims"])
    def reference(f):
        s = f["source"]
        return f"{f['document_type']}，第{s['page']}页" if s.get("page") else f"素描原字段{s.get('source_pointer','')}"
    def forecasts(query):
        return [[location(f["query_overlap_m"]), reference(f), f["original_text"]] for f in query["facts"]
                if f["fact_type"] == "geological_description" and f["extraction_quality"]["status"] == "PASS"]
    section = "<section class='chapter'><h2>三、当日施工区段地质分析</h2>"+paragraphs(3)
    section += table(["施工区段", "来源", "原报告预测"], forecasts(construction["query"]))
    response_rows = []
    for r in construction["responses"]:
        s = r["summary"]
        if s["status"] != "insufficient":
            stats = s["statistics"]
            response_rows.append([location(r["range_m"]), s["sample_count"], *[f"{stats[k]['median']:.2f}" for k in ("pr", "fpi", "se")]])
    section += table(["区段", "有效样本", "每转贯入度 mm/r", "总推力口径FPI kN/(把·mm)", "理论机械比能 MJ/m³"], response_rows)
    section += "<figure><img src='figures/unified_axis.png'><figcaption>统一里程轴：原预报分段、明确观察位置与施工范围。</figcaption></figure></section>"
    section += "<section class='chapter'><h2>四、现场地质素描与揭露情况</h2>"+paragraphs(4)
    for index, record in enumerate(observations["records"], 1):
        doc = record["document"]
        section += "<h3>"+html.escape(location([doc["start_m"], doc["end_m"]]))+"的新增记录</h3>"
        section += "<p class='caption'>来源："+html.escape(doc["source_file"])+"，原始结构化字段及图片。</p>"
        rows = [[location([f["start_m"], f["end_m"]]) if f.get("start_m") is not None else "原文未注明具体位置",
                 f["original_text"]] for f in record["observations"]]
        section += table(["原记录位置", "施工分级与观察原文"], rows)
        compared = set()
        comparison_rows = []
        for c in record["comparisons"]:
            g = c["attributes"]["grade"]
            if not g["left"] or not g["right"]:
                continue
            key = (c["forecast"]["document_id"], c["range_m"][0], c["range_m"][1], tuple(g["left"]), tuple(g["right"]))
            if key in compared:
                continue
            compared.add(key)
            comparison_rows.append([location(c["range_m"]), c["forecast"]["document_type"], "、".join(g["left"]), "、".join(g["right"])])
        section += table(["原记录范围", "此前预报", "预测等级", "素描原表施工分级"], comparison_rows)
        image = next((r for r in record["original_images"] if Path(r["path"]).name == "图0.jpg"), None)
        if image and Path(image["path"]).is_file() and digest(Path(image["path"])) == image["sha256"]:
            target = output/"sketches"/f"new_sketch_{index}.jpg"
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(image["path"], target)
            section += f"<figure><img src='sketches/{target.name}'><figcaption>原始素描图，报告可用日期{record['report_available_date']}。</figcaption></figure>"
    section += f"</section><section class='chapter'><h2>五、前方地质预报（{ahead['distance_m']:g}m窗口）</h2>"+paragraphs(5)
    section += table(["前方范围", "来源", "原报告预测"], forecasts(ahead["query"]))
    risk_rows = [[location(f["query_overlap_m"]), str(f["value"]), "—".join(f"{v:.2f}m" for v in dict.fromkeys(f["distances_from_current_m"])), reference(f)]
                 for f in ahead["query"]["facts"] if f["fact_type"] == "risk" and f["extraction_quality"]["status"] == "PASS"]
    section += table(["预测位置或范围", "预测事项", "距当日终点", "来源"], risk_rows)
    section += table(["尚无既有预报的范围", "长度 m"], [[location([g["start_m"], g["end_m"]]), f"{g['length_m']:.2f}"] for g in ahead["coverage"]["gaps"]])+"</section>"
    start = value.index("<section class='chapter'><h2>三、")
    end = value.index("<h2>六、主要关注事项</h2>")
    value = value[:start]+section+value[end:]
    # Reader-facing output omits program audits; full restrictions remain in JSON.
    a = value.index("<h3>数据与解释边界</h3>")
    b = value.index("<h3>原始地质资料来源</h3>", a)
    value = value[:a]+"<p class='note'>地质位置以报告里程与导向盾首记录对照；两者与实际掌子面的几何关系尚未标定。</p>"+value[b:]
    sources = {}
    def add_source(f):
        s = f["source"]
        key = s.get("sha256") or f["document_id"]
        if key not in sources:
            sources[key] = {"name": s.get("original_filename", Path(s.get("path", "来源")).name),
                            "date": f.get("time_decision", {}).get("available_date"), "pages": set(),
                            "path": s.get("path"), "sha256": s.get("sha256")}
        if s.get("page"):
            sources[key]["pages"].add(s["page"])
    for f in construction["query"]["facts"]+ahead["query"]["facts"]:
        add_source(f)
    for r in observations["records"]:
        d = r["document"]
        sources[d["source_version"]] = {"name": d["source_file"], "date": d["metadata"].get("available_date"), "pages": set(),
                                        "path": d["original_file_path"], "sha256": d["source_version"]}
        for c in r["comparisons"]:
            add_source(c["forecast"])
    source_rows = [[s["name"], s["date"] or "原资料见索引", "、".join(map(str, sorted(s["pages"]))) if s["pages"] else "JSON原字段及原图"] for s in sources.values()]
    a = value.index("<h3>原始地质资料来源</h3>")
    b = value.index("<section class='chapter'><h2>附录A", a)
    value = value[:a]+("<h3>原始地质资料来源</h3>"+table(["来源资料", "提交/可用日期", "PDF页码或原字段"], source_rows) if source_rows else "")+value[b:]
    write_json(output/"unified_report_source_index.json", [{**s, "pages": sorted(s["pages"])} for s in sources.values()])
    value = value.replace("</h1>", "</h1><p class='caption'>时空地质证据库离线核验稿，未调用LLM。</p>", 1)
    footer_start, footer_end = value.index("<footer>"), value.index("</footer>")+len("</footer>")
    value = value[:footer_start]+value[footer_end:]
    value = value.replace(output.as_uri()+"/", "")
    path.write_text(value)
    write_json(output/"report_integration.json", {"source": "single_geology_database", "actual_api_calls": 0,
               "chapters_1_2_use_existing_analysis": True, "geo_sections_use_query_results": True,
               "checked_narrative_used_directly": True, "historical_nearest_sketch_fallback": False})
    return path
