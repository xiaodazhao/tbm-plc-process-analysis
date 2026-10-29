"""Unified evidence packs and bounded reuse of the existing planner/generator."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from tbm_process.final_reporting.common import format_chainage
from tbm_process.final_reporting.integrated_llm import (ModelResponseError, _numbers, _number_is_supported,
                                                       _asserts_forecast_observation, _asserts_current_observation, call_json_model)
from tbm_process.reporting_v2.facts import digest, duration, write_json
from tbm_process.reporting_v2.narrative import (build_writing_facts, check_draft, check_plan,
                                               offline_draft, offline_plan, requests_for)
from tbm_process.reporting_v2.response_features import BASE, NAMES, response_facts

from .queries import query_day
from .relations import prohibited_assertions


VERSION = "unified_response_relation_evidence_pack_v2_20261027_r1"


class ModelSession:
    def __init__(self, output, policy, api_key=None):
        self.output, self.policy, self.api_key = Path(output), policy, api_key
        self.records = []
        self.available = bool(api_key)

    def call(self, request, folder, stage):
        folder = Path(folder)
        write_json(folder/(stage+".request.json"), request)
        if not self.available:
            write_json(folder/(stage+".NOT_SENT.json"), {"reason":"API_disabled_or_unavailable","actual_model_output":False})
            return None
        if len(self.records) >= self.policy["llm"]["maximum_calls"]:
            write_json(folder/(stage+".NOT_SENT.json"), {"reason":"hard_call_budget_reached"})
            return None
        record = {"index":len(self.records)+1,"stage":stage,"folder":str(folder),
                  "time_utc":datetime.now(timezone.utc).isoformat(),"status":"started","request_sha256":digest(folder/(stage+".request.json")),
                  "transport_retries":0,"model_requested":request["model"]}
        record["date"] = next((p for p in folder.parts if re.fullmatch(r"\d{4}-\d{2}-\d{2}",p)),None)
        record["group"] = next((p for p in reversed(folder.parts) if p in {"A","B","C"}),"relations")
        self.records.append(record)
        write_json(self.output/"model_calls.json",self.records)
        try:
            payload, raw, meta = call_json_model(request, api_key=self.api_key, base_url=self.policy["llm"]["base_url"],
                timeout_seconds=self.policy["llm"]["timeout_seconds"],max_retries=0)
            write_json(folder/(stage+".response.json"),raw)
            write_json(folder/(stage+".payload.json"),payload)
            write_json(folder/(stage+".metadata.json"),meta)
            record.update(meta,status="success")
            return payload
        except Exception as exc:
            if isinstance(exc,ModelResponseError):
                write_json(folder/(stage+".response.json"),exc.response)
                record.update(exc.metadata)
            status = getattr(exc,"status_code",None)
            record.update(status="failed",error_type=type(exc).__name__,http_status=status)
            # Never persist arbitrary SDK exception text which could include headers.
            write_json(folder/(stage+".failure.json"), {"error_type":type(exc).__name__,"http_status":status,
                                                       "raw_response_saved":isinstance(exc,ModelResponseError)})
            if status in {401,402,403,404} or type(exc).__name__ in {"APIConnectionError","APITimeoutError"}:
                self.available = False
            return None
        finally:
            write_json(self.output/"model_calls.json",self.records)


def _compact(value):
    if isinstance(value,dict):
        return {k:_compact(v) for k,v in value.items() if k not in {"source_rows","exact_rows","source_trace","raw_values","source","parameter_changes"}}
    if isinstance(value,list):
        return [_compact(v) for v in value]
    return value


def build_evidence_pack(package, graph, daily_features, policy):
    day = package["date"]
    facts = [deepcopy(f) for f in build_writing_facts(package)
             if not f["fact_id"].startswith(("GEO:","RISK:","SKETCH:")) and f["fact_id"] not in {"JOINT_SCOPE","SKETCH_SCOPE"}]
    for fact in facts:
        fact["graph_support_ids"] = []
        if fact["fact_id"].startswith("PLATFORM:"):
            p = fact["data"]
            fact["writer_data"] = {k:p.get(k) for k in ["platform_id","start_time","end_time","working_duration_seconds","elapsed_span_seconds","interval_advance","working_parameter_statistics","stable_phase"]}
            fact["writer_data"]["performance"] = _compact(p["performance"])
        elif fact["fact_id"] == "TIME":
            o = package["daily_overview"]
            fact["writer_data"] = {k:o[k] for k in ["working_duration_seconds","stopped_duration_seconds","effective_state_duration_seconds","utilization_percent","coverage_ratio"]}
        else:
            fact["writer_data"] = _compact(fact["data"])
    l, o = package["daily_overview"]["daily_advance"], package["daily_overview"]
    next(f for f in facts if f["fact_id"]=="OVERVIEW")["required_text_anchors"] = [l["start_chainage"],l["end_chainage"],f"{l['net_change_m']:.2f}m"]
    next(f for f in facts if f["fact_id"]=="TIME")["required_text_anchors"] = [duration(o["working_duration_seconds"]),duration(o["stopped_duration_seconds"]),f"{o['utilization_percent']:.1f}%"]
    metrics = daily_features["metrics"]
    text = "基础推进统计不要求PR通过分母保护，但仍要求既有稳定及推进状态和基础信号质量。"+"；".join(
        f"{NAMES[k]}采用{s['count']}条记录，中位数{s['median']:.2f}{s['unit']}" for k,s in metrics.items() if k in BASE and s["status"]=="usable_descriptive")+"。"
    facts.append({"fact_id":"BASIC_RESPONSE_SCOPE","chapter":2,"canonical_text":text,"data":daily_features,
                  "writer_data":{"metrics":{k:_compact(s) for k,s in metrics.items() if k in BASE},"limitations":daily_features["limits"]},
                  "required":True,"nature":"deterministic_PLC_calculation","source":package["source"],"graph_support_ids":[]})
    query = query_day(graph,day)
    categories = [(3,query["A_current_geology"]["fact_ids"]),(5,query["D_ahead_geology"]["fact_ids"]),
                  (4,query["C_sketches"]["observed_fact_ids"]+query["C_sketches"]["inferred_fact_ids"]+query["C_sketches"]["earlier_forecast_fact_ids"])]
    seen = set()
    for chapter, ids in categories:
        for fid in ids:
            if fid in seen:
                continue
            seen.add(fid)
            d=graph.nodes[fid]["data"]; doc=graph.nodes[d["document_id"]]["data"]
            nature = "原报告预测" if d["information_role"]=="forecast" else "历史记录中的观察或推测，按原属性理解"
            facts.append({"fact_id":fid,"chapter":chapter,"canonical_text":f"{doc['document_type']}{nature}：{d['original_description']}",
                          "data":d,"writer_data":{"value":d["value"],"text":d["original_description"],"information_role":d["information_role"],
                            "range_m":[d["start_m"],d["end_m"]],
                            "range_display":[format_chainage(d["start_m"]),format_chainage(d["end_m"])],
                            "source_type":doc["document_type"],"source_file":doc["source_file"],
                            "source_page":d["source_page"],"source_date":doc["metadata"].get("submission_date") or doc["metadata"].get("available_date"),
                            "observation_date":doc["metadata"].get("observation_date")},
                          "source":d["source"],"required":False,"nature":d["information_role"],"graph_support_ids":[fid]})
    omitted_dynamics=[]
    for node in graph.typed("PLCResponse"):
        d=node["data"]
        if d["report_date"]!=day or not d.get("features_v2"):
            continue
        v=d["features_v2"]
        facts.append({"fact_id":node["id"],"chapter":3,"canonical_text":f"{format_chainage(d['start_m'])}至{format_chainage(d['end_m'])}的机器响应按合格样本统计，实际时序和来源单独保留，不把首末跨度写成连续作业。",
                      "data":d,"writer_data":{"metrics":_compact(v["metrics"]),"time_scope":v["time_scope"],
                          "range_display":[format_chainage(d["start_m"]),format_chainage(d["end_m"])],
                          "platform_count":len(v["platform_composition"]),"spatial_quality":v["spatial_quality"],
                          "comparisons":_compact(d["comparisons_v2"])},"required":False,"nature":"PLC_response",
                      "source":d["source_trace"],"graph_support_ids":[node["id"]]})
        dynamic=[r for r in d["response_facts_v2"] if r["kind"] in {"within_window_trend","adjacent_window_torque_cv_change"}]
        # Dimensionless existing trend effect, not raw changes in incompatible units.
        dynamic.sort(key=lambda r:(r["kind"]!="within_window_trend",-abs(float(r["data"].get("relative_change",0) or 0)),r["fact_id"]))
        selected=dynamic[:policy["content_limits"]["maximum_dynamic_facts_per_response"]]
        chosen={r["fact_id"] for r in selected}
        omitted_dynamics.extend({"fact_id":r["fact_id"],"reason":"writing_budget_only_full_dynamic_fact_retained_in_graph"} for r in dynamic if r["fact_id"] not in chosen)
        for row in d["response_facts_v2"]:
            facts.append({"fact_id":row["fact_id"],"chapter":3,"canonical_text":row["canonical_text"],"data":row,
                          "writer_data":{k:_compact(row[k]) for k in ["data","kind","metric","range_m","time_range","limits"]},
                          "source":row["source"],"required":False,"nature":row["kind"],"graph_support_ids":[node["id"]],
                          "presentation":None if row["fact_id"] in chosen else "detail_retained_not_selected_for_writer"})
    # For a no-forecast control day, still expose real PLC dynamics without inventing
    # a geological association or requiring a spatial graph response node.
    if not query["B_PLC_response"]["responses"]:
        dynamic=[r for r in response_facts(daily_features,day,package["config"]) if r["kind"]=="within_window_trend"]
        for r in dynamic[:policy["content_limits"]["maximum_dynamic_facts_per_response"]]:
            facts.append({"fact_id":r["fact_id"],"chapter":2,"canonical_text":r["canonical_text"],"data":r,
                          "writer_data":_compact(r),"required":False,"nature":r["kind"],"source":r["source"],"graph_support_ids":[]})
    selected=[]; excluded=[]
    for fact in facts:
        reason=None
        if fact.get("presentation"):
            reason=fact["presentation"]
        if fact["fact_id"].startswith("PLATFORM:") and fact["fact_id"].removeprefix("PLATFORM:") in package["selected_platform_ids"]:
            reason=None
        if fact["fact_id"]=="PROGRESS_COMPARISON":
            reason="preserved_in_evidence_not_required_for_response_relation_pilot"
        if reason and not fact["required"]:
            excluded.append({"fact_id":fact["fact_id"],"reason":reason})
        else:
            selected.append(fact["fact_id"])
    table={f["fact_id"]:f for f in facts}
    relations=[]
    for n in graph.typed("EngineeringComparison"):
        d=n["data"]
        ids=d.get("source_fact_ids",[])+d.get("source_response_ids",[])
        if d["report_date"]==day and ids and set(ids)<=set(selected) and d["comparison_type"] in {"geological_forecast_disagreement","geology_plc_response_comparison"}:
            relations.append({"relation_id":n["id"],"evidence_fact_ids":ids,"range_m":d["chainage_range_m"],
                              "type":d["comparison_type"],"result":d["result"],"status":"PROGRAM_RELATION_NOT_SEMANTIC_TRUTH",
                              "limitations":d.get("limitations",[])})
    return {"schema_version":VERSION,"date":day,"facts":facts,"writer_fact_ids":selected,"writer_omissions":excluded,
            "dynamic_writer_omissions":omitted_dynamics,"program_relations":relations,"semantic_relation_candidates":[],
            "generation_constraints":["no_causal_or_fault_inference","predictions_are_not_observations","unverified_semantic_relations_are_layout_hints_only",
                                      "sample_support_duration_not_elapsed_span","relative_change_not_percent","FPI_total_thrust_SE_theoretical"],
            "source":package["source"],"human_labels":None}


def writer_facts(pack):
    table={f["fact_id"]:f for f in pack["facts"]}
    return [{k:v for k,v in {**table[i],"data":table[i].get("writer_data",{})}.items()
             if k in {"fact_id","chapter","canonical_text","data","required","nature","required_text_anchors"}} for i in pack["writer_fact_ids"]]


def writing_requests(pack, policy):
    facts=writer_facts(pack)
    example=offline_plan(facts)
    plan, draft=requests_for(facts,example,policy["llm"]["model"])
    for req in (plan,draft):
        req.update(temperature=policy["llm"]["temperature"],max_tokens=policy["llm"]["max_tokens"])
    relations={"program_relations":pack["program_relations"],"semantic_relation_candidates":pack["semantic_relation_candidates"]}
    plan["messages"][0]["content"] += ("章节仍为既定六章。每章增加paragraphs数组[{paragraph_id,fact_ids,relation_ids,purpose}]，chapter.fact_ids应为其段落fact_ids的并集。"
        "引用relation_ids时，必须把其evidence_fact_ids全部选入同一段落的fact_ids；不准备同时叙述两端证据时，不引用该关系，relation_ids可为空。没有可用事实的章节可为空，不编造。"
        "必须自行选择、合并和排序，不能把全部候选逐条照抄；省略需说明理由。语义候选仅帮助共同组织原事实，不能当作已证实的一致性或地质解释。"
        "required_text_anchors必须在正文保持，数值用给定显示方式，不自行计算。")
    plan["messages"][1]["content"]=json.dumps({"facts":facts,"relations":relations,"example_schema":example},ensure_ascii=False)
    draft["messages"][0]["content"] += ("每条claim可引用多个事实，但须逐一支持所写内容；增加relation_ids数组表示使用的关系。"
        "写成连贯、简短的工程文字，不输出内部ID或研究指标堆砌。区分总体水平、窗口变化、跨区段差异。"
        "不得把未核实语义关系写为预测正确、地质原因或实际揭露。必须原样保留required_text_anchors，不能把累计时长写成连续时长。")
    return facts,plan,draft


def plan_quality(plan, pack):
    facts=writer_facts(pack)
    try:
        check=check_plan(plan,facts,chapter_ids=pack.get("chapter_ids"))
    except (KeyError,TypeError,AttributeError):
        return {"passed":False,"errors":["invalid_plan_schema"],"selected_fact_ids":[]}
    relation_ids={r["relation_id"] for r in pack["program_relations"]+pack["semantic_relation_candidates"]}
    relations={r["relation_id"]:r for r in pack["program_relations"]+pack["semantic_relation_candidates"]}
    fact_table={f["fact_id"]:f for f in pack["facts"]}
    for chapter in plan.get("chapters",[]):
        paragraphs=chapter.get("paragraphs")
        if not isinstance(paragraphs,list) or (chapter.get("fact_ids") and not paragraphs):
            check["errors"].append("missing_paragraph_plan")
            continue
        seen=[]
        for p in paragraphs:
            seen.extend(p.get("fact_ids",[]))
            if not set(p.get("relation_ids",[])) <= relation_ids:
                check["errors"].append("unknown_planning_relation")
            support=set(p.get("fact_ids",[])) | {g for i in p.get("fact_ids",[]) if i in fact_table for g in fact_table[i].get("graph_support_ids",[])}
            for rid in p.get("relation_ids",[]):
                if rid in relations and not set(relations[rid]["evidence_fact_ids"]) <= support:
                    check["errors"].append("planning_relation_missing_endpoint_facts")
        if set(seen)!=set(chapter.get("fact_ids",[])):
            check["errors"].append("paragraph_chapter_membership_mismatch")
    check["passed"]=not check["errors"]
    if pack.get("schema_version", "").startswith("three_chapter"):
        from .three_chapter import check_relation_planning
        check["errors"].extend(check_relation_planning(plan, pack))
        check["passed"] = not check["errors"]
    return check


def _quantities(text):
    return [(float(m.group(1)),m.group(2)) for m in re.finditer(r"([-+]?\d+(?:\.\d+)?)\s*(mm/min|kN·m|kN|r/min|mm/r|MJ/m³|米|m|秒|条|%)",str(text))]


def _numeric_unit_pairs(facts):
    pairs=[]
    def walk(value):
        if isinstance(value,dict):
            unit=value.get("unit")
            # Only explicit, precomputed percentages; never normalize relative_change.
            if isinstance(value.get("percent"),(int,float)):
                pairs.append((float(value['percent']),'%'))
            if unit:
                for k in ("value","median","p10","p90","mean","minimum","maximum","current","reference","difference","initial_median","final_median","absolute_change"):
                    if isinstance(value.get(k),(int,float)):
                        pairs.append((float(value[k]),unit))
            for key,item in value.items():
                if isinstance(item,(int,float)) and key.endswith('_seconds'):
                    pairs.append((float(item),'秒'))
                if isinstance(item,(int,float)) and key in {'count','sample_count','current_count','reference_count','qualified_sample_count'}:
                    pairs.append((float(item),'条'))
            for v in value.values(): walk(v)
        elif isinstance(value,list):
            for v in value: walk(v)
    for fact in facts:
        pairs.extend(_quantities(fact["canonical_text"]))
        from .report_reliability import duration_values
        pairs.extend((seconds,'秒') for _,seconds in duration_values(fact['canonical_text']))
        walk(fact.get("data",{}))
    return pairs


def draft_quality(draft, plan, pack):
    three = pack.get('schema_version', '').startswith('three_chapter')
    if three:
        from .report_reliability import asserts_observation, finish_quality, percentage_check,claim_facts,duration_check,mask_report_cutoff
        from .three_chapter import apply_window_choices
        if 'window_decisions' in plan:pack=apply_window_choices(pack,plan)
    reviews=[]
    facts=writer_facts(pack)
    try:
        initial=check_draft(draft,plan,facts,False,chapter_ids=pack.get("chapter_ids"))
    except (KeyError,TypeError,AttributeError):
        return {"passed":False,"errors":["invalid_draft_schema"],"claim_checks":[],"review":[],"covered_fact_ids":[]}
    if three:
        # The legacy check uses unscoped digits (including ID fragments and
        # compound-duration components). The claim-level typed check below
        # replaces it, rather than counting the same number with two contracts.
        initial['legacy_unscoped_numeric_findings']=[e for e in initial['errors'] if e=='unsupported_numeric_content']
        initial['errors']=[e for e in initial['errors'] if e!='unsupported_numeric_content']
    table={f["fact_id"]:f for f in facts}; full={f["fact_id"]:f for f in pack["facts"]}
    links={r["relation_id"]:r for r in pack["program_relations"]+pack["semantic_relation_candidates"]}
    items=[]
    for ci,chapter in enumerate(draft.get("chapters",[])):
        for index,claim in enumerate(chapter.get("claims",[])):
            text=claim.get("text",""); ids=claim.get("fact_ids",[])
            errors=[]
            if not isinstance(text,str) or not isinstance(ids,list) or not ids or not set(ids)<=set(table):
                items.append({"chapter_index":ci,"claim_index":index,"errors":["invalid_claim"],"status":"FAIL"})
                continue
            available=[table[i] for i in ids]
            if three:available=claim_facts(claim,available,pack)
            pairs=_numeric_unit_pairs(available)
            numeric_text=text
            if three:
                numeric_text,duration_errors=duration_check(text,pairs)
                numeric_text=mask_report_cutoff(numeric_text,pack.get('date'))
                errors.extend(duration_errors)
            permitted=_numbers(json.dumps(available,ensure_ascii=False))
            if any(not _number_is_supported(n,permitted) for n in _numbers(numeric_text)):
                errors.append("unsupported_numeric_content")
            errors.extend(prohibited_assertions(text))
            for value,unit in _quantities(numeric_text):
                if three and unit == '%':
                    continue
                normalized="m" if unit=="米" else unit
                if not any(("m" if u=="米" else u)==normalized and abs(value-v)<=max(.0051,abs(v)*.000001) for v,u in pairs):
                    errors.append("unsupported_value_unit_pair:"+str(value)+unit)
            if three:
                percent_errors,percent_review=percentage_check(text,available,pairs)
                errors.extend(percent_errors)
                reviews.extend(f'claim:{ci}:{index}:'+r for r in percent_review)
            for i in ids:
                role=full[i].get("nature")
                role_text = re.sub(r'不等于(?=实际揭露)', '并非', text) if pack.get('chapter_ids') == [1, 2, 3] else text
                if role=="forecast" and (asserts_observation(text) if three else _asserts_forecast_observation(role_text)):
                    errors.append("forecast_written_as_observation")
                if role=="observed" and (asserts_observation(text,current=True) if three else _asserts_current_observation(text)):
                    errors.append("historical_record_written_as_current_observation")
                data=full[i].get("writer_data",{})
                continuous=data.get("time_scope",{}).get("continuous")
                if continuous is False and re.search(r"连续(?:掘进|推进|工作|作业)",text) and not re.search(r"不连续|并非连续|不表示连续|不代表连续|不能.*连续|不把.*连续|不将.*连续|不应.*连续",text):
                    errors.append("intermittent_response_written_as_continuous")
            for rid in claim.get("relation_ids",[]):
                if rid not in links:
                    errors.append("unknown_relation_id")
                    continue
                supported={g for i in ids for g in full[i].get("graph_support_ids",[])}|set(ids)
                if not set(links[rid]["evidence_fact_ids"])<=supported:
                    errors.append("wrong_relation_support_membership")
            items.append({"chapter_index":ci,"claim_index":index,"text":text,"fact_ids":ids,"relation_ids":claim.get("relation_ids",[]),
                          "errors":sorted(set(errors)),"status":"FAIL" if errors else "REVIEW",
                          "meaning":"program_checks_not_complete_semantic_or_human_confirmation"})
    for fact in facts:
        if fact.get("required_text_anchors"):
            text="".join(c.get("text","") for ch in draft.get("chapters",[]) for c in ch.get("claims",[]) if fact["fact_id"] in c.get("fact_ids",[]))
            normalized=re.sub(r"\s+","",text)
            for anchor in fact["required_text_anchors"]:
                if re.sub(r"\s+","",anchor) not in normalized:
                    initial["errors"].append("required_display_missing:"+fact["fact_id"]+":"+anchor)
    errors=initial["errors"]+[e for item in items for e in item["errors"]]
    if pack.get("schema_version", "").startswith("three_chapter"):
        from .three_chapter import check_three_draft
        errors.extend(check_three_draft(draft, plan, pack))
    result={**initial,"passed":not errors,"errors":sorted(set(errors)),"claim_checks":items,
            "semantic_status":"REVIEW_no_independent_human_gold_labels"}
    return finish_quality({**result,'review_items':reviews},pack,draft) if three else result


def retention_check(before, after, targets, pack):
    errors=[]; allowed=set(targets)
    facts={f["fact_id"]:f for f in writer_facts(pack)}
    for ci,ch in enumerate(before.get("chapters",[])):
        after_claims=after.get("chapters",[])[ci].get("claims",[]) if ci<len(after.get("chapters",[])) else []
        if len(ch.get("claims",[]))!=len(after_claims):
            errors.append("claim_deleted_or_added")
            continue
        for i,old in enumerate(ch.get("claims",[])):
            new=after_claims[i]
            if (ci,i) not in allowed and old!=new:
                errors.append("non_target_claim_modified")
            if not new.get("text") or not set(old.get("fact_ids",[]))<=set(new.get("fact_ids",[])):
                errors.append("support_or_text_deleted")
            permitted=_numbers(json.dumps([facts[x] for x in old.get("fact_ids",[]) if x in facts],ensure_ascii=False))
            old_text,new_text=old.get('text',''),new.get('text','')
            if pack.get('schema_version','').startswith('three_chapter'):
                from .report_reliability import engineering_prose
                old_text,new_text=engineering_prose(old_text),engineering_prose(new_text)
            correct={n for n in _numbers(old_text) if _number_is_supported(n,permitted)}
            present=_numbers(new_text)
            if any(not _number_is_supported(n,present) for n in correct):
                errors.append("previously_supported_numeric_content_lost")
    return {"passed":not errors,"errors":sorted(set(errors)),"scope":"unchanged_non_targets_fact_IDs_and_supported_numbers_not_complete_semantic_retention"}


def normalize_three_chapter_ids(payload):
    """Coerce only unambiguous chapter identifier types, never content or membership."""
    result = deepcopy(payload)
    conversions = []
    for i, chapter in enumerate(result.get('chapters', [])):
        value = chapter.get('chapter_id')
        if isinstance(value, str) and value in {'1', '2', '3'}:
            chapter['chapter_id'] = int(value)
            conversions.append({'chapter_index': i, 'before': value, 'after': int(value)})
    return result, conversions


def run_writer(pack, output, policy, session, *, revise=False, reuse=None, rejected_plan=None, request_builder=None, supplied_plan=None):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    facts,plan_request,generation=(request_builder or writing_requests)(pack,policy)
    write_json(output/"evidence_pack.json",pack)
    if supplied_plan is not None:
        plan = deepcopy(supplied_plan)
        write_json(output/'plan.provenance.json', {'type': 'supplied_explicitly_compiled_plan', 'new_planning_calls': 0})
    elif rejected_plan is not None:
        original_rejected = rejected_plan
        if pack.get('chapter_ids') == [1, 2, 3]:
            rejected_plan, conversions = normalize_three_chapter_ids(rejected_plan)
            write_json(output/'rejected_plan.type_conversions.json', conversions)
        before=plan_quality(rejected_plan,pack)
        write_json(output/'rejected_plan.original.json',original_rejected)
        write_json(output/'rejected_plan.validation.json',before)
        request=deepcopy(plan_request)
        request['messages'][0]['content']+='这是唯一一次提纲修正。只修正反馈错误，不编造事实。所有未选的候选必须逐ID记录省略原因；关系端点不齐时补齐已有事实或不引用该关系。'
        known=set(pack['writer_fact_ids'])
        selected={i for ch in rejected_plan.get('chapters',[]) for i in ch.get('fact_ids',[])}
        explained={r.get('fact_id') for r in rejected_plan.get('omitted',[]) if r.get('reason')}
        request['messages'][1]['content']=json.dumps({'facts':facts,'rejected_plan':rejected_plan,'errors':before,
            'unaccounted_fact_ids':sorted(known-selected-explained),
            'construction_phenomena':pack.get('construction_phenomena', []),
            'program_relations':_compact(pack['program_relations']) if pack.get('chapter_ids') == [1,2,3] else pack['program_relations'],
            'semantic_relation_candidates':pack['semantic_relation_candidates']},ensure_ascii=False)
        plan=session.call(request,output,'plan_repair')
    else:
        plan=deepcopy(reuse["plan"]) if reuse else session.call(plan_request,output,"plan")
    if reuse:
        write_json(output/"reuse.json",{"source":reuse["source"],"plan_and_draft_reused":True,"new_planning_generation_calls":0})
    if plan is None:
        write_json(output/"status.json",{"status":"not_generated","reason":"API_unavailable_or_failed","actual_llm_report":False})
        return None,{"status":"not_generated"}
    if pack.get('chapter_ids') == [1, 2, 3]:
        plan, conversions = normalize_three_chapter_ids(plan)
        write_json(output/'plan.type_conversions.json', conversions)
    check=plan_quality(plan,pack); write_json(output/"plan.validation.json",check)
    if not check["passed"]:
        write_json(output/"status.json",{"status":"failed_plan","errors":check["errors"],"no_automatic_plan_retry":True})
        return None,{"status":"failed_plan"}
    write_json(output/"content_plan.json",plan)
    if pack.get('window_selection_contract'):
        from .three_chapter import apply_window_choices
        pack=apply_window_choices(pack,plan)
        facts=writer_facts(pack)
        write_json(output/'evidence_pack.selected.json',pack)
    report_relations = _compact(pack["program_relations"]) if pack.get("chapter_ids") == [1,2,3] else pack["program_relations"]
    generation["messages"][1]["content"]=json.dumps({"facts":facts,"plan":plan,
        "construction_phenomena":pack.get("construction_phenomena", []),
        "program_relations":report_relations,"semantic_relation_candidates":pack["semantic_relation_candidates"]},ensure_ascii=False)
    draft=deepcopy(reuse["draft"]) if reuse else session.call(generation,output,"generation")
    if draft is None:
        return None,{"status":"failed_generation"}
    if pack.get('chapter_ids') == [1, 2, 3]:
        draft, conversions = normalize_three_chapter_ids(draft)
        write_json(output/'generation.type_conversions.json', conversions)
    write_json(output/"draft.before.json",draft)
    quality=draft_quality(draft,plan,pack); write_json(output/"quality.before.json",quality)
    original=deepcopy(draft); retention=None; revised=False
    if revise and not quality["passed"] and policy["llm"]["maximum_local_revision_calls_per_report"]:
        targets={(r["chapter_index"],r["claim_index"]) for r in quality["claim_checks"] if r["errors"]}
        for error in quality["errors"]:
            located = re.match(r"claim:(\d+):(\d+):", error)
            if located:
                targets.add(tuple(map(int, located.groups())))
        # Required-value omissions can be repaired only in the claims already bound
        # to those facts; no whole-report regeneration or deletion is allowed.
        if any(e.startswith("required_display_missing:") for e in quality["errors"]):
            for ci,ch in enumerate(draft["chapters"]):
                targets.update((ci,i) for i,c in enumerate(ch["claims"]) if set(c.get("fact_ids",[])) & {"OVERVIEW","TIME"})
        request={**{k:v for k,v in generation.items() if k!="messages"},"messages":[
            {"role":"system","content":"只修正列出的错误陈述，不删除事实、不修改其他陈述、不新增数值或原因。返回JSON {replacements:[{chapter_index,claim_index,text,fact_ids,relation_ids}]}。只能修改targets，保留原支持ID和已经正确的数值。"},
            {"role":"user","content":json.dumps({"targets":sorted(targets),"draft":draft,"errors":quality,"facts":facts},ensure_ascii=False)}]}
        if pack.get('chapter_ids') == [1, 2, 3]:
            request['messages'][0]['content'] += ('内部DOC/GF/PLC/GM等标识符及其数字不是工程数值，严禁出现在text；被检查器标出的内部标识符必须从正文移除，支持ID仍完整保留在JSON字段中。'
                '删除内部标识符不等于删除工程事实，但不得连带删除有效日期、里程、参数和适用范围。不要原样返回已报错的句子。'
                '目标claim的statistic_scope、analysis_unit_id、response_id、geology_assignments、window_ids可在原计划允许的范围内纠正并返回，未修改字段可以省略。'
                'statistic_scope只能为segment_comparison/single_segment/daily_background/no_qualified_response，全天统计须明确称全天稳定推进背景，不可绑定为某个局部区段实测值。'
                '空间不相交的素描只能标为此前背景，不能作为当前区段的地质判断。'
                '前方窗口外的风险点不得列为前方风险，可保留原位置并明确说明在窗口外。'
                '必须重写或移除错误的主句，不能保留错误主句再追加“需说明”或免责声明。'
                '先写历史素描自身实际覆盖里程，再说明它与当前PLC区段不重叠；不能开头仍称当前区段素描显示。'
                '前方段落先写窗口内真实交集，窗口外风险另句明确为后方/窗口外，不要包含在“窗口内”主句中。'
                '所有里程边界必须写完整DyK桩号，禁止把DyK1015+840缩写成“+840”。'
                '保留原文已经正确的里程和数值，但纠正它们的适用范围；不要新增支持ID或修改非目标段落。')
        patch=session.call(request,output,"local_revision") if targets else None
        if patch:
            candidate=deepcopy(draft); edits=[]
            for change in patch.get("replacements",[]):
                location=(change.get("chapter_index"),change.get("claim_index"))
                if location not in targets:
                    edits.append({"invalid_location":location})
                    break
                ci,i=location
                new={**candidate["chapters"][ci]["claims"][i], **{k:change[k] for k in ("text","fact_ids","relation_ids",
                    "statistic_scope","analysis_unit_id","response_id","geology_assignments","window_ids") if k in change}}
                edits.append({"location":location,"before":candidate["chapters"][ci]["claims"][i],"after":new})
                candidate["chapters"][ci]["claims"][i]=new
            retention=retention_check(draft,candidate,targets,pack)
            if any("invalid_location" in e for e in edits):
                retention={"passed":False,"errors":["patch_attempted_non_target_edit"]}
            after=draft_quality(candidate,plan,pack)
            write_json(output/"revision.log.json",{"edits":edits,"retention":retention,"quality_after":after})
            if retention["passed"] and after["passed"]:
                draft,quality,revised=candidate,after,bool(edits)
    write_json(output/"narrative.json",draft)
    write_json(output/"quality.final.json",quality)
    write_json(output/"source_traces.json",[{"text":c["text"],"phenomenon_ids":c.get("phenomenon_ids",[]),"fact_ids":c.get("fact_ids",[]),
        "relation_ids":c.get("relation_ids",[]),"source_records":[{"fact_id":i,"source":next(f for f in pack["facts"] if f["fact_id"]==i).get("source"),
            "graph_support_ids":next(f for f in pack["facts"] if f["fact_id"]==i).get("graph_support_ids",[])} for i in c.get("fact_ids",[]) if any(f["fact_id"]==i for f in pack["facts"])]}
        for ch in draft.get("chapters",[]) for c in ch.get("claims",[])])
    status={"status":"real_llm_program_checked_candidate" if quality["passed"] else "real_llm_failed_quality_retained",
            "actual_llm_report":True,"program_checks_passed":quality["passed"],"semantic_status":"REVIEW",
            "quality_status":quality.get('status'),
            "revised":revised,"retention":retention,"changed_claims":sum(a!=b for x,y in zip(original["chapters"],draft["chapters"]) for a,b in zip(x["claims"],y["claims"]))}
    write_json(output/"status.json",status)
    return draft,status
