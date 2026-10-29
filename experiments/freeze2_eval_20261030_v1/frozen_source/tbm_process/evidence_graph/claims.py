from __future__ import annotations

import re

from tbm_process.final_reporting.common import format_chainage

from .catalog import document_gate
from .queries import query_day
from .store import identity


def place(bounds):
    if bounds[0] == bounds[1]:
        return format_chainage(bounds[0])+"点位"
    return format_chainage(bounds[0])+"至"+format_chainage(bounds[1])


def organize_claims(graph, day):
    result = query_day(graph, day)
    selected, candidates = [], []

    def add(kind, chapter, statement, supports, bounds, reason, use=True, status="program_supported_nominal_reference_only", time_range=None):
        supports = list(dict.fromkeys(supports))
        key = identity("CLAIM", day, kind, supports, bounds, statement)
        data = {"claim_id": key, "report_date": day, "claim_type": kind, "chapter": chapter,
                "chainage_range_m": bounds, "time_range": time_range, "statement": statement,
                "support_ids": supports, "evidence_status": status,
                "selection_reason": reason, "selected_for_report": use,
                "semantic_verification": "not_independent_expert_confirmation", "construction_method": "deterministic_source_bound_statement"}
        graph.add_node("ReportClaim", key, data)
        for support in supports:
            graph.add_edge("SUPPORTED_BY", key, support, {"method": "typed_explicit_claim_support_membership"})
        candidates.append(key)
        if use:
            selected.append(key)
        return key

    current = result["A_current_geology"]["fact_ids"]
    ahead = result["D_ahead_geology"]["fact_ids"]
    context = graph.metadata["daily_contexts"][day]
    for scope, identities, chapter in [("current", current, 3), ("ahead", ahead, 5)]:
        bounds = context["day_range_m" if scope == "current" else "ahead_range_m"]
        for fid in identities:
            fact = graph.nodes[fid]["data"]
            doc = graph.nodes[fact["document_id"]]["data"]
            if fact["fact_type"] not in {"rock_grade", "risk"}:
                continue
            gate = document_gate(doc, day)
            original = [fact["start_m"], fact["end_m"]]
            portion = [max(bounds[0], original[0]), min(bounds[1], original[1])]
            if fact["fact_type"] == "risk":
                statement = f"{doc['document_type']}原报告对{place(original)}提示{fact['value']}；与本日盾首参考{'前方窗口' if scope == 'ahead' else '施工范围'}的交集为{place(portion)}。这是预测事项，不是已发生事件。"
                if scope == "ahead":
                    distances = [context["direction"]*(x-context["shield_end_m"]) for x in original]
                    statement = statement.replace("。这是预测事项", f"，原位置距盾首终点{distances[0]:.2f}至{distances[1]:.2f}m。该记录的几何类型为{'点' if fact['geometry']=='point' else '区间'}。这是预测事项")
                add("ahead_risk" if scope == "ahead" else "current_forecast_risk", chapter, statement, [fid], portion,
                    "explicit_source_risk_geometry_and_prior_date_retained", True)
            else:
                statement = f"依据{gate['document_date']}形成的{doc['document_type']}，{place(portion)}对应原预报分段的建议围岩等级为{fact['value']}级。"
                add("forecast_fact", chapter, statement, [fid], portion,
                    "complete_forecast_table_already_displays_this_record", False, "source_reported_forecast_not_observation")

    for cid in result["B_PLC_response"]["comparison_ids"]:
        comparison = graph.nodes[cid]["data"]
        kind, supports = comparison["comparison_type"], comparison["source_fact_ids"]+comparison["source_response_ids"]+[cid]
        bounds = comparison["chainage_range_m"]
        if kind == "geological_forecast_disagreement" and comparison["result"] == "explicit_reported_grade_difference":
            descriptions = []
            for fid in comparison["source_fact_ids"]:
                fact = graph.nodes[fid]["data"]
                doc = graph.nodes[fact["document_id"]]["data"]
                descriptions.append(f"{doc['document_type']}记载{fact['value']}级")
            chapter = 3 if max(bounds[0], context["day_range_m"][0]) < min(bounds[1], context["day_range_m"][1]) else 5
            add("forecast_disagreement", chapter, f"{place(bounds)}的既有预测存在等级差异："+"，".join(descriptions)+"；两种意见分别保留，不据此判定哪一来源正确。", supports, bounds,
                "explicit_comparable_unequal_grades_same_nominal_range")
        elif kind == "geology_plc_response_comparison":
            response = graph.nodes[comparison["source_response_ids"][0]]["data"]
            if comparison["result"] == "observed_change":
                names = {"advance_speed": ("推进速度", "mm/min"), "thrust": ("总推力", "kN"), "torque": ("刀盘扭矩", "kN·m"),
                         "pr": ("PR", "mm/r"), "fpi": ("总推力口径FPI", "kN/(把·mm)"), "se": ("理论机械SE", "MJ/m³")}
                for metric, change in comparison["measured_difference"].get("changes", {}).items():
                    if not change["threshold_met"]:
                        continue
                    name, unit = names[metric]
                    text = (f"在{place(bounds)}已有预报范围的导向盾首名义对应记录中，{name}中位数为{change['current']:.2f}{unit}；"
                            f"相较此前可比区段的{change['reference']:.2f}{unit}，差值{change['difference']:+.2f}{unit}（{change['percent']:+.2f}%）。"
                            "位置偏移尚未核定，此结果不证明地质导致该变化。")
                    add("geology_plc_response_comparison:"+metric, 3, text, supports, bounds,
                        "existing_qualified_comparison_threshold_met_with_prior_reference", time_range=comparison["time_range"])
            elif response["summary"]["status"] != "insufficient":
                stats = response["summary"]["statistics"]
                text = (f"{place(bounds)}已有预报范围的导向盾首名义对应中，有{response['effective_sample_count']}条合格稳定推进记录；"
                        f"PR、总推力口径FPI、理论机械SE中位数分别为{stats['pr']['median']:.2f}mm/r、"
                        f"{stats['fpi']['median']:.2f}kN/(把·mm)、{stats['se']['median']:.2f}MJ/m³。")
                text += "未取得可用的先前可比基准，不判断偏高或异常。" if comparison["result"] == "insufficient_data" else "与先前可比区段的差异未达到本版变化判据，不等同于机器状态正常。"
                add("PLC_segment_response", 3, text, supports, bounds, "qualified_summary_without_unjustified_geological_explanation",
                    time_range=comparison["time_range"])
            else:
                add("insufficient_PLC_segment_evidence", 3, f"{place(bounds)}对应的合格稳定推进数据不足，本轮不发布该段性能比较结论。",
                    supports, bounds, "explicitly_report_missing_qualified_evidence", True, "insufficient_data")
    groups = {}
    for cid in result["B_PLC_response"]["comparison_ids"]:
        comparison = graph.nodes[cid]["data"]
        if comparison["comparison_type"] != "forecast_observation_comparison":
            continue
        fs = [graph.nodes[i]["data"] for i in comparison["source_fact_ids"]]
        key = (tuple((f["document_id"],tuple(f["attributes"]["grade"]["values"])) for f in fs),comparison["result"])
        groups.setdefault(key,[]).append((cid,comparison))
    for values in groups.values():
        blocks = []
        for value in sorted(values,key=lambda v:v[1]["chainage_range_m"]):
            bounds = value[1]["chainage_range_m"]
            if not blocks or bounds[0] > blocks[-1]["bounds"][1]:
                blocks.append({"bounds":list(bounds),"comparisons":[value]})
            else:
                blocks[-1]["bounds"][1] = max(blocks[-1]["bounds"][1],bounds[1])
                blocks[-1]["comparisons"].append(value)
        for block in blocks:
            first = block["comparisons"][0][1]
            parts = []
            for fid in first["source_fact_ids"]:
                fact = graph.nodes[fid]["data"]
                doc = graph.nodes[fact["document_id"]]["data"]
                label = {"SKETCH":"地质素描","DESIGN":"设计资料"}.get(doc["document_type"],doc["document_type"])
                parts.append(f"{label}原记录为{str(fact['value']).removesuffix('级')}级" if fact["fact_type"] == "rock_grade" else label+"原文描述")
            supports = [i for cid,c in block["comparisons"] for i in [cid]+c["source_fact_ids"]]
            text = f"{place(block['bounds'])}的更早预测与历史素描作名义范围对照："+"，".join(parts)+"。仅合并来源及等级相同的连续交集；素描可用日期不等于实际观察时刻，不据此给出预报准确率。"
            add("forecast_observation_comparison",4,text,supports,block["bounds"],"merge_contiguous_same_document_pair_and_grade_only; complete_comparisons_retained")
    graph.metadata.setdefault("claim_selection", {})[day] = {"candidate_ids": candidates, "selected_ids": selected,
        "omitted_ids": [i for i in candidates if i not in selected],
        "rule": "retain_explicit_source_disagreements_and_risks_and_available_segment_responses; omit_grade_repetition_already_in_tables",
        "fixed_daily_claim_quota": None}
    return [graph.nodes[i] for i in candidates]


def check_claim(graph, claim_id, day, *, text=None, supports=None):
    claim = graph.nodes[claim_id]["data"]
    text = claim["statement"] if text is None else text
    supports = claim["support_ids"] if supports is None else supports
    failures = []
    if not supports or set(supports) != set(claim["support_ids"]):
        failures.append("support_membership_changed_or_missing")
    if any(i not in graph.nodes for i in supports):
        failures.append("unknown_support_id")
    canonical_numbers = set(re.findall(r"[-+]?\d+(?:\.\d+)?", claim["statement"]))
    if any(n not in canonical_numbers for n in re.findall(r"[-+]?\d+(?:\.\d+)?", text)):
        failures.append("new_unsupported_numeric_token")
    for clause in re.split(r"[。；]", text):
        if re.search(r"导致|造成|证实预报|证明预报|刀具磨损|设备故障", clause) and not re.search(r"不|未|不能|不得|没有", clause):
            failures.append("unsupported_causal_or_diagnostic_claim")
    trace = graph.trace(claim_id)
    for node in trace["nodes"]:
        if node["type"] == "GeologyDocument" and document_gate(node["data"], day)["status"] != "eligible":
            failures.append("ineligible_document_in_support_trace")
    changed = text != claim["statement"]
    return {"claim_id": claim_id, "status": "FAIL" if failures else "REVIEW" if changed else "PROGRAM_SUPPORTED",
            "errors": failures, "noncanonical_text": changed,
            "scope": "typed_source_and_computation_support_not_independent_semantic_truth",
            "semantic_status": "REVIEW; altered_or_LLM_paraphrases_are_not_automatically_certified",
            "trace_node_count": len(trace["nodes"]), "trace_edge_count": len(trace["edges"])}
