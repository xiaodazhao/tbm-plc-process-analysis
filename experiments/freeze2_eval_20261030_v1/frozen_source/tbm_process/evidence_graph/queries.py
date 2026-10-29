from __future__ import annotations

from .catalog import document_gate
from .store import EvidenceGraph, intersects


def fact_is_eligible(graph: EvidenceGraph, fact_id: str, day: str, *, prior_forecast=False):
    fact = graph.nodes[fact_id]["data"]
    document = graph.nodes[fact["document_id"]]["data"]
    gate = document_gate(document, day)
    if gate["status"] != "eligible":
        return False
    if fact["extraction_quality"]["status"] != "PASS":
        return False
    if prior_forecast and gate.get("role") != "prior_document":
        return False
    return True


def _spatial(graph, day, scope, method):
    context = graph.metadata["daily_contexts"][day]
    target_id = context["day_segment_id" if scope == "day" else "ahead_segment_id"]
    if target_id is None:
        return set()
    target = graph.nodes[target_id]["data"]
    if method == "graph":
        segments = set(graph.neighbors(target_id, "OVERLAPS_WITH", incoming=True))
        candidate = set()
        for sid in segments:
            segment = graph.nodes[sid]["data"]
            if not intersects(segment,target):
                continue
            candidate.update(fid for fid in graph.neighbors(sid, "COVERS", incoming=True)
                             if all(graph.nodes[fid]["data"].get(k) == segment.get(k) for k in ("start_m","end_m","line","chainage_system")))
    elif method == "flat":
        # Same source facts and date policy; no graph traversal or hidden extra evidence.
        candidate = {n["id"] for n in graph.typed("GeologyFact") if intersects(n["data"], target)}
    else:
        raise ValueError("method must be flat or graph")
    result = set()
    for fid in candidate:
        fact = graph.nodes[fid]["data"]
        if fact["information_role"] not in {"forecast", "design_assessment"}:
            continue
        document = graph.nodes[fact["document_id"]]["data"]
        if fact["information_role"] == "design_assessment" and document["document_type"] != "DESIGN":
            continue
        if not fact_is_eligible(graph, fid, day, prior_forecast=True):
            continue
        # Recheck geometry, not only the existence of a graph edge.
        if not intersects(fact, target):
            continue
        if scope == "ahead" and fact["geometry"] == "point":
            direction = context["direction"]
            if direction is None or direction*(fact["point_m"]-context["shield_end_m"]) <= 0:
                continue
        result.add(fid)
    return result


def query_day(graph: EvidenceGraph, day: str, *, method="graph") -> dict:
    context = graph.metadata["daily_contexts"][day]
    current, ahead = _spatial(graph, day, "day", method), _spatial(graph, day, "ahead", method)
    observation_docs = set(context["observation_document_ids"])
    if method == "graph":
        observation = {fid for did in observation_docs for fid in graph.neighbors(did, "HAS_FACT")
                       if fact_is_eligible(graph, fid, day)}
    else:
        observation = {n["id"] for n in graph.typed("GeologyFact")
                       if n["data"]["document_id"] in observation_docs and fact_is_eligible(graph, n["id"], day)}
    # Inferred sketch clauses remain separate from observed table/description facts.
    observed = {i for i in observation if graph.nodes[i]["data"]["information_role"] == "observed"}
    inferred = {i for i in observation if graph.nodes[i]["data"]["information_role"] == "inferred"}
    recorded_design = {i for i in observation if graph.nodes[i]["data"]["information_role"] == "design_assessment"}
    earlier_forecasts = {i for i in context.get("sketch_forecast_fact_ids", []) if fact_is_eligible(graph, i, day, prior_forecast=True)}
    comparison_ids, response_ids = set(), set()
    selected = current | ahead | observation | earlier_forecasts
    for comparison in graph.typed("EngineeringComparison"):
        data = comparison["data"]
        if data["report_date"] != day:
            continue
        if method == "graph":
            linked = set(graph.neighbors(comparison["id"], "COMPARES"))
            gs = {i for i in linked if graph.nodes[i]["type"] == "GeologyFact"}
            rs = {i for i in linked if graph.nodes[i]["type"] == "PLCResponse"}
        else:
            gs, rs = set(data["source_fact_ids"]), set(data["source_response_ids"])
        if gs and gs <= selected:
            comparison_ids.add(comparison["id"])
            response_ids.update(rs)
    claims = [n["id"] for n in graph.typed("ReportClaim") if n["data"]["report_date"] == day and n["data"]["selected_for_report"]]
    dates = {}
    for fid in selected:
        did = graph.nodes[fid]["data"]["document_id"]
        dates[did] = document_gate(graph.nodes[did]["data"], day)
    def records(ids):
        return [graph.nodes[i] for i in sorted(ids)]
    return {"date": day, "method": method,
            "A_current_geology": {"fact_ids": sorted(current), "facts": records(current)},
            "B_PLC_response": {"response_ids": sorted(response_ids), "responses": records(response_ids),
                               "comparison_ids": sorted(comparison_ids), "comparisons": records(comparison_ids)},
            "C_sketches": {"observed_fact_ids": sorted(observed), "inferred_fact_ids": sorted(inferred),
                           "recorded_design_assessment_fact_ids": sorted(recorded_design),
                           "earlier_forecast_fact_ids": sorted(earlier_forecasts),
                           "facts": records(observation | earlier_forecasts), "document_ids": sorted(observation_docs)},
            "D_ahead_geology": {"range_m": context["ahead_range_m"], "fact_ids": sorted(ahead), "facts": records(ahead),
                                "distances_from_shield_end_m": {
                                    fid: [context["direction"]*(graph.nodes[fid]["data"][k]-context["shield_end_m"])
                                          for k in ("start_m", "end_m")] for fid in sorted(ahead)}},
            "E_report_information": {"claim_ids": sorted(claims), "claims": records(claims)},
            "source_time_decisions": dates,
            "limits": ["reference_spatial_relation_not_causation", "literal_attributes_not_semantic_ground_truth",
                       "date_only_prior_day_policy_not_verified_distribution", "independent_relevance_labels_not_available"]}


def compare_retrieval(graph, day):
    a, b = query_day(graph, day, method="flat"), query_day(graph, day, method="graph")
    rows = []
    keys = [("A_current_geology", "fact_ids"), ("B_PLC_response", "response_ids"),
            ("B_PLC_response", "comparison_ids"), ("C_sketches", "observed_fact_ids"),
            ("C_sketches", "inferred_fact_ids"), ("C_sketches","recorded_design_assessment_fact_ids"),
            ("D_ahead_geology", "fact_ids")]
    for section, field in keys:
        left, right = set(a[section][field]), set(b[section][field])
        rows.append({"query": section+":"+field, "flat_count": len(left), "graph_count": len(right),
                     "common_count": len(left & right), "flat_only": sorted(left-right), "graph_only": sorted(right-left),
                     "same_results": left == right})
    return {"date": day, "checks": rows, "same_fact_pool": True, "same_temporal_policy": True,
            "all_equal": all(r["same_results"] for r in rows),
            "interpretation": "set_agreement_not_retrieval_accuracy_or_graph_superiority; both may reconstruct relations"}


def daily_subgraph(graph: EvidenceGraph, day: str) -> EvidenceGraph:
    query = query_day(graph, day)
    selected = set(query["A_current_geology"]["fact_ids"]+query["D_ahead_geology"]["fact_ids"]+
                   query["C_sketches"]["observed_fact_ids"]+query["C_sketches"]["inferred_fact_ids"]+
                   query["C_sketches"]["earlier_forecast_fact_ids"]+
                   query["C_sketches"]["recorded_design_assessment_fact_ids"]+
                   query["B_PLC_response"]["response_ids"]+query["B_PLC_response"]["comparison_ids"]+
                   query["E_report_information"]["claim_ids"])
    for key in list(selected):
        selected.update(n["id"] for n in graph.trace(key)["nodes"])
    context = graph.metadata["daily_contexts"][day]
    selected.update(i for i in (context["day_segment_id"], context["ahead_segment_id"]) if i)
    # Keep only eligible source facts; a trace must not bring future evidence back in.
    selected = {i for i in selected if graph.nodes[i]["type"] != "GeologyFact" or fact_is_eligible(graph, i, day)}
    used_docs = {graph.nodes[i]["data"]["document_id"] for i in selected if graph.nodes[i]["type"] == "GeologyFact"}
    selected = {i for i in selected if graph.nodes[i]["type"] != "GeologyDocument" or i in used_docs}
    out = EvidenceGraph()
    out.metadata = {**graph.metadata, "snapshot_report_date": day,
                    "daily_contexts": {day: context}, "source_audit": [a for a in graph.metadata.get("source_audit", []) if a["document_id"] in used_docs]}
    for i in sorted(selected):
        n = graph.nodes[i]
        out.add_node(n["type"], i, n["data"])
    for e in graph.edges.values():
        if e["source"] in selected and e["target"] in selected:
            out.add_edge(e["type"], e["source"], e["target"], e["evidence"])
    return out
