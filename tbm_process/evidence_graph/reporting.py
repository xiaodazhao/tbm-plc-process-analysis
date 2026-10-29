from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from tbm_process.reporting_v2.facts import write_json
from tbm_process.reporting_v2.narrative import (build_writing_facts, check_draft, check_plan,
                                               offline_draft, offline_plan, requests_for)

from .claims import check_claim


def make_report_input(package, graph, day, output):
    facts = [f for f in build_writing_facts(package) if f["fact_id"] not in {"JOINT_SCOPE","SKETCH_SCOPE"}]
    selected = [n for n in graph.typed("ReportClaim") if n["data"]["report_date"] == day and n["data"]["selected_for_report"]]
    for node in selected:
        claim = node["data"]
        check = check_claim(graph, node["id"], day)
        if check["status"] != "PROGRAM_SUPPORTED":
            raise ValueError("Graph claim failed source checks: " + node["id"])
        facts.append({"fact_id": "GRAPH:"+node["id"], "chapter": claim["chapter"],
                      "canonical_text": claim["statement"], "data": claim,
                      "source": {"graph_claim_id": node["id"], "support_ids": claim["support_ids"],
                                 "trace_file": "claim_traces.json"},
                      "required": True, "nature": "evidence_graph_derived_source_bound_statement_not_diagnosis"})
    if package.get("current_sketch_policy"):
        policy = package["current_sketch_policy"]
        text = (f"素描仅使用当前伯舒拉岭数据目录的结构化来源，共{policy['current_sketch_count']}份；"
                f"按内容哈希对应{policy['unique_sketch_records']}份唯一记录，完全重复副本不作为独立证据重复计数；"
                f"其中{policy['confirmed_availability_dates']}份按用户确认采用图片路径日期作为报告可用日期，精度为日。"
                f"本日展示{len(package.get('current_sketch_documents',[]))}份此前可用记录；未回退使用旧素描副本，未据此补造实际观察时刻，同日时刻不明记录不用于解释此前运行。")
        facts.append({"fact_id": "CURRENT_SKETCH_TIME_LIMIT", "chapter": 4, "canonical_text": text,
                      "data": {k: policy[k] for k in ("current_sketch_count", "explicitly_dated_sketches","confirmed_availability_dates")},
                      "source": "current_sketch_source_audit.json", "required": True, "nature": "data_quality_not_geological_condition"})
    plan = offline_plan(facts)
    draft = offline_draft(plan, facts)
    plan_check = check_plan(plan, facts)
    check = check_draft(draft, plan, facts, True)
    if not plan_check["passed"] or not check["passed"]:
        raise ValueError("Graph integration input failed consistency checks")
    plan_request, generation_request = requests_for(facts, plan, "deepseek-chat")
    for path, value in [("writing_facts.json", facts), ("content_plan.json", plan), ("narrative.json", draft),
                        ("plan.validation.json", plan_check), ("narrative.validation.json", check),
                        ("plan.request.NOT_SENT.json", plan_request), ("generation.request.NOT_SENT.json", generation_request)]:
        write_json(output/path, value)
    write_json(output/"graph_consumption.json", {
        "selected_graph_claim_ids": [c["id"] for c in selected],
        "writing_fact_ids": ["GRAPH:"+c["id"] for c in selected],
        "plan_consumes_all_selected_graph_claims": all("GRAPH:"+c["id"] in plan_check["selected_fact_ids"] for c in selected),
        "narrative_consumes_all_selected_graph_claims": all("GRAPH:"+c["id"] in check["covered_fact_ids"] for c in selected),
        "actual_api_calls": 0, "mode": "explicit_deterministic_offline_graph_integration",
        "scope": "whole_report_still_uses_original_daily_PLC_facts; graph_only_adds_spatial_geology_and_responses"})
    return draft


def insert_current_sketches(html_path, graph, day, package):
    sections, images = [], []
    for number, document in enumerate(package.get("current_sketch_documents",[]),1):
        facts = [graph.nodes[i]["data"] for i in graph.neighbors(document["document_id"],"HAS_FACT")]
        metadata = document["metadata"]
        sections.append(f"<h3>可用日期{metadata['available_date']}，原记录范围{document['start_m']:.2f}至{document['end_m']:.2f}m</h3>")
        sections.append("<p class='note'>日期口径依据用户确认；该日期不是已经独立核定的实际揭露时刻。以下分别保存原表字段、推测文字和带明确里程的观察描述，不将整段推测写成实测揭露。</p>")
        sections.append("<table><thead><tr><th>原记录性质</th><th>原文/字段值</th><th>适用范围说明</th></tr></thead><tbody>")
        for fact in facts:
            if fact["fact_type"] == "mixed_original_conclusion":
                continue
            role = {"observed":"原记录观察/施工分级", "inferred":"原记录推测", "design_assessment":"原记录转述设计"}[fact["information_role"]]
            location = "无明确分句里程，未广播到表头全范围" if fact["start_m"] is None else f"{fact['start_m']:.2f}至{fact['end_m']:.2f}m"
            sections.append("<tr><td>"+role+"</td><td>"+html.escape(str(fact["value"]))+"</td><td>"+location+"</td></tr>")
        sections.append("</tbody></table>")
        local = document.get("local_images",[])
        image = next((r for r in local if Path(r["path"]).name == "图0.jpg"),local[0] if local else None)
        if image:
            from tbm_process.reporting_v2.facts import digest
            source = Path(image["path"])
            if source.is_file() and digest(source) == image["sha256"]:
                target = html_path.parent/"sketches"/f"current_sketch_{number}.jpg"
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source,target)
                sections.append(f"<figure><img src='{target.as_uri()}'><figcaption>本地原始素描图；可用日期{metadata['available_date']}。未重绘；图中前方推测与已开挖范围不得混同。</figcaption></figure>")
                images.append({"source":image,"copy":str(target),"document_id":document["document_id"]})
    write_json(html_path.parent/"current_sketch_images.json",images)
    value = html_path.read_text()
    marker = "<section class='chapter'><h2>五、前方地质预报"
    value = value.replace(marker,"".join(sections)+marker)
    html_path.write_text(value)
    return images


def add_evidence_appendix(html_path: Path, graph_png: Path, traces: list[dict]):
    body = ["<section class='evidenceappendix' style='break-before:page'><h2>附录C 本日报使用的多源证据关系</h2>",
            "<p>下图只展示三条代表性陈述的真实证据子集。空间关系与来源支持不是因果证明；完整关系、未采用内容及时间审计另存。</p>",
            f"<figure><img src='{graph_png.as_uri()}'><figcaption>图5 真实来源—区段响应—对照结果—日报陈述关系（局部）</figcaption></figure>"]
    for i, trace in enumerate(traces, 1):
        claim = next(n for n in trace["nodes"] if n["id"] == trace["root_id"])["data"]
        docs = [n["data"] for n in trace["nodes"] if n["type"] == "GeologyDocument"]
        responses = [n["data"] for n in trace["nodes"] if n["type"] == "PLCResponse"]
        body.append(f"<h3>陈述{i}的来源路径</h3><p>{html.escape(claim['statement'])}</p>")
        for doc in docs:
            source = doc["source"]
            pages = sorted({n["data"]["source_page"] for n in trace["nodes"] if n["type"] == "GeologyFact" and n["data"]["document_id"] == doc["document_id"] and n["data"]["source_page"] is not None})
            page_text = "、".join(map(str,pages)) or "结构化JSON指针，非PDF页码"
            body.append("<p class='small'>来源："+html.escape(doc["source_file"])+"；页码："+page_text+"；原记录日期："+
                        html.escape(str(doc["metadata"].get("submission_date") or doc["metadata"].get("observation_date") or doc["metadata"].get("available_date")))+"（JSON素描为用户确认的可用日期）。</p>")
        for response in responses:
            body.append(f"<p class='small'>PLC对应：{response['start_time']}至{response['end_time']}，合格样本{response['effective_sample_count']}条；源文件SHA及精确原行集合保存在该陈述的追溯JSON中。</p>")
    body.append("</section>")
    value = html_path.read_text().replace("<footer>", "".join(body)+"<footer>")
    footer = re.search(r"<footer>(.*?)</footer>",value,re.S)
    if footer:
        value = value.replace(footer.group(0),"")
        value = value.replace("</h1>","</h1><p class='caption'>"+footer.group(1)+"</p>",1)
    # Generated local asset references remain usable after copying the whole run folder.
    value = value.replace(html_path.parent.as_uri()+"/", "")
    html_path.write_text(value)
