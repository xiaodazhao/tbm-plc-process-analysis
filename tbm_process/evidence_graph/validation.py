from __future__ import annotations

from collections import Counter
import re

import pandas as pd

from .catalog import document_gate
from .claims import check_claim
from .queries import query_day
from .store import RELATIONS, intersects


def validate_graph(graph, day=None):
    errors, counts = [], Counter()
    for edge in graph.edges.values():
        kind, source, target = edge["type"], edge["source"], edge["target"]
        counts[kind] += 1
        if kind not in RELATIONS or source not in graph.nodes or target not in graph.nodes:
            errors.append({"edge_id": edge["id"], "reason": "forbidden_or_dangling_relation"})
            continue
        a, b = graph.nodes[source], graph.nodes[target]
        ok = a["type"] in RELATIONS[kind][0] and b["type"] in RELATIONS[kind][1]
        left, right = a["data"], b["data"]
        if kind == "HAS_FACT":
            ok &= right["document_id"] == source
        elif kind in {"COVERS", "OBSERVED_AT"}:
            ok &= all(left.get(k) == right.get(k) for k in ("start_m", "end_m", "line", "chainage_system"))
            if kind == "OBSERVED_AT":
                ok &= left["information_role"] == "observed"
        elif kind == "OVERLAPS_WITH":
            ok &= intersects(left, right)
        elif kind == "MEASURED_OVER":
            ok &= left["start_m"] == right["start_m"] and left["end_m"] == right["end_m"]
        elif kind == "COMPARES":
            ok &= target in left["source_fact_ids"]+left["source_response_ids"]
        elif kind == "DERIVED_FROM":
            if a["type"] == "EngineeringComparison":
                ok &= target in left["source_fact_ids"]+left["source_response_ids"]
        elif kind == "SUPPORTED_BY":
            ok &= target in left["support_ids"]
        elif kind == "DIFFERS_FROM":
            ok &= left["fact_type"] == right["fact_type"] == "rock_grade"
            ok &= left["attributes"]["grade"]["values"] != right["attributes"]["grade"]["values"]
            ok &= intersects(left, right)
        if not ok:
            errors.append({"edge_id": edge["id"], "reason": "relation_content_not_supported"})
    for comparison in graph.typed("EngineeringComparison"):
        data = comparison["data"]
        expected = set(data["source_fact_ids"]+data["source_response_ids"])
        actual = set(graph.neighbors(comparison["id"], "COMPARES"))
        if actual != expected:
            errors.append({"node_id": comparison["id"], "reason": "missing_or_extra_comparison_links"})
        if data["comparison_type"] == "historical_response_comparison" and len(data["source_response_ids"]) == 2:
            current, previous = [graph.nodes[i]["data"] for i in data["source_response_ids"]]
            if not previous["end_time"] or not current["start_time"] or pd.Timestamp(previous["end_time"]) > pd.Timestamp(current["start_time"]):
                errors.append({"node_id": comparison["id"], "reason": "future_or_overlapping_PLC_baseline"})
        if data["comparison_type"] == "forecast_observation_comparison":
            fs = [graph.nodes[i]["data"] for i in data["source_fact_ids"]]
            forecasts = [f for f in fs if f["information_role"] == "forecast"]
            observations = [f for f in fs if f["information_role"] == "observed"]
            for forecast in forecasts:
                for obs in observations:
                    doc = graph.nodes[forecast["document_id"]]["data"]
                    metadata = graph.nodes[obs["document_id"]]["data"]["metadata"]
                    obs_day = metadata.get("observation_date") or metadata.get("available_date")
                    gate = document_gate(doc, obs_day) if obs_day else {}
                    if not intersects(forecast, obs) or gate.get("status") != "eligible" or gate.get("role") != "prior_document":
                        errors.append({"node_id": comparison["id"], "reason": "forecast_not_prior_to_observation_or_not_same_nominal_point"})
    if day:
        query = query_day(graph, day)
        for did, decision in query["source_time_decisions"].items():
            if decision["status"] != "eligible":
                errors.append({"node_id": did, "reason": "ineligible_source_retrieved"})
        for response in query["B_PLC_response"]["responses"]:
            data = response["data"]
            if data["report_date"] != day or (data["end_time"] and pd.Timestamp(data["end_time"]).date().isoformat() > day):
                errors.append({"node_id": response["id"], "reason": "PLC_response_outside_report_date"})
        claims = [check_claim(graph, c["id"], day) for c in query["E_report_information"]["claims"]]
        for claim in claims:
            if claim["status"] == "FAIL":
                errors.append({"node_id": claim["claim_id"], "reason": "report_claim_support_failed", "detail": claim})
    else:
        claims = []
    return {"passed": not errors, "errors": errors, "node_count": len(graph.nodes), "edge_count": len(graph.edges),
            "node_types": dict(Counter(n["type"] for n in graph.nodes.values())), "relation_types": dict(counts),
            "claim_checks": claims, "meaning": "relation_and_timing_consistency_not_independent_geological_accuracy"}


def future_isolation_test(graph, day):
    """Use real later records in the same database; do not manufacture measurements."""
    query = query_day(graph, day)
    candidates = []
    target = graph.nodes[graph.metadata["daily_contexts"][day]["day_segment_id"]]["data"]
    returned = set(query["A_current_geology"]["fact_ids"]+query["D_ahead_geology"]["fact_ids"])
    for node in graph.typed("GeologyFact"):
        fact = node["data"]
        if fact["information_role"] != "forecast" or not intersects(fact, target):
            continue
        doc = graph.nodes[fact["document_id"]]["data"]
        gate = document_gate(doc, day)
        if gate.get("reason") == "future_document":
            candidates.append({"fact_id": node["id"], "document_id": fact["document_id"],
                               "document_date": gate.get("document_date"), "available_date":gate.get("available_date"),"excluded": node["id"] not in returned})
    return {"date": day, "real_future_overlapping_facts": candidates,
            "passed": all(row["excluded"] for row in candidates),
            "coverage": "real_later_overlapping_records_checked" if candidates else "no_real_later_overlap_for_this_day; synthetic_regression_test_separate"}
