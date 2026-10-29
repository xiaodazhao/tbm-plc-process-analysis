"""Synthetic regression fixtures only; none are used in real daily reports."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tbm_process.evidence_graph.catalog import SourceAudit, _clauses, build_catalog_graph, document_gate
from tbm_process.evidence_graph.claims import check_claim, organize_claims
from tbm_process.evidence_graph.queries import compare_retrieval, daily_subgraph, query_day
from tbm_process.evidence_graph.sketches import load_current_catalog, mileage_geometry
from tbm_process.evidence_graph.store import EvidenceGraph, identity, intersects
from tbm_process.evidence_graph.validation import future_isolation_test, validate_graph
from tbm_process.reporting_v2.facts import digest
from tbm_process.reporting_v2.geology import attributes


DAY = "2024-04-26"


def fixture_graph(source_day="2024-04-18", prefix="DyK", role="forecast"):
    g = EvidenceGraph()
    metadata = {"source_type":"hsp","source_report_id":"synthetic-HSP", "source_file":"synthetic.pdf", "submission_date":source_day,
                "date_conflicts":[],"alignment":"right","chainage_prefix":prefix}
    doc = {"document_id":"D", "document_type":"HSP", "metadata":metadata,
           "date_audit":{"status":"PASS"},"source_file":"synthetic.pdf", "source":{}}
    g.add_node("GeologyDocument","D",doc)
    spatial = {"start_m":1015822.,"end_m":1015827.,"line":"right","chainage_system":prefix,"geometry":"interval"}
    fact = {**spatial,"fact_id":"F", "document_id":"D", "record_id":"synthetic-HSP:H1", "fact_type":"rock_grade",
            "value":"V","information_role":role,"attributes":attributes("", "V"),"source_page":1,
            "source":{},"extraction_quality":{"status":"PASS"},"original_description":"synthetic grade V"}
    g.add_node("GeologyFact","F",fact)
    g.add_node("ConstructionSegment","S",spatial)
    g.add_node("ConstructionSegment","DAY",{**spatial,"start_m":1015819.,"end_m":1015828.,"chainage_system":"DyK"})
    g.add_node("ConstructionSegment","AHEAD",{**spatial,"start_m":1015828.,"end_m":1015848.,"chainage_system":"DyK"})
    g.add_edge("HAS_FACT","D","F",{"method":"fixture_membership"})
    g.add_edge("COVERS","F","S",{"method":"fixture_geometry"})
    g.add_edge("OVERLAPS_WITH","S","DAY",{"method":"fixture_intersection"})
    g.metadata["daily_contexts"] = {DAY:{"day_segment_id":"DAY","ahead_segment_id":"AHEAD","day_range_m":[1015819.,1015828.],
        "ahead_range_m":[1015828.,1015848.],"shield_end_m":1015828.,"direction":1,"observation_document_ids":[],"response_ids":[]}}
    return g


@pytest.mark.parametrize("relation",["CAUSES","PROVES","CONFIRMS","RANDOM"])
def test_forbidden_relation_names(relation):
    g = fixture_graph()
    with pytest.raises(ValueError):
        g.add_edge(relation,"D","F",{"method":"not_allowed"})


def test_no_dangling_or_wrong_endpoint_types():
    g = fixture_graph()
    with pytest.raises(ValueError):
        g.add_edge("HAS_FACT","D","missing",{"method":"fixture"})
    with pytest.raises(ValueError):
        g.add_edge("HAS_FACT","F","D",{"method":"fixture"})


def test_duplicate_identity_conflict_is_not_silent_override():
    g = fixture_graph()
    with pytest.raises(ValueError):
        g.add_node("GeologyFact","F",{"value":"IV"})


def test_adding_comparison_nodes_while_iterating_facts_is_safe():
    g = fixture_graph()
    for fact in g.typed("GeologyFact"):
        g.add_node("EngineeringComparison","new",{"source":fact["id"]})
    assert "new" in g.nodes


def test_baseline_and_graph_use_same_pool():
    g = fixture_graph()
    assert compare_retrieval(g,DAY)["all_equal"]
    assert query_day(g,DAY)["A_current_geology"]["fact_ids"] == ["F"]


def test_actual_future_overlap_is_excluded_even_if_path_exists():
    g = fixture_graph("2024-04-27")
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]
    evidence = future_isolation_test(g,DAY)
    assert evidence["passed"] and evidence["real_future_overlapping_facts"][0]["excluded"]


def test_same_day_no_time_not_prior_forecast():
    g = fixture_graph(DAY)
    assert document_gate(g.nodes["D"]["data"],DAY)["status"] == "review"
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_verified_same_day_timestamp_still_not_preexisting_forecast():
    g = fixture_graph(DAY)
    g.nodes["D"]["data"]["metadata"].update({"available_at":DAY+"T15:00:00+08:00","actual_distribution_time_verified":True})
    assert document_gate(g.nodes["D"]["data"],DAY)["status"] == "eligible"
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_unresolved_document_date_conflict_is_excluded():
    g = fixture_graph()
    g.nodes["D"]["data"]["metadata"]["date_conflicts"] = ["unresolved"]
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_DK_not_silently_converted_to_DyK():
    g = fixture_graph(prefix="DK")
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_source_date_audit_failure_blocks_source():
    g = fixture_graph()
    g.nodes["D"]["data"]["date_audit"]["status"] = "REVIEW"
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_design_grade_requoted_by_sketch_is_not_independent_design_evidence():
    g = fixture_graph(role="design_assessment")
    g.nodes["D"]["data"]["document_type"] = "SKETCH"
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_relation_path_is_not_enough_to_pass_content_validation():
    g = fixture_graph()
    g.nodes["S"]["data"] = {**g.nodes["S"]["data"],"start_m":1020000.,"end_m":1020005.}
    assert not validate_graph(g,DAY)["passed"]
    assert not query_day(g,DAY)["A_current_geology"]["fact_ids"]


def test_wrong_document_id_relation_detected():
    g = fixture_graph()
    g.nodes["F"]["data"]["document_id"] = "wrong"
    assert not validate_graph(g)["passed"]


def test_forecast_cannot_be_cast_as_observed_relation():
    g = fixture_graph()
    g.add_edge("OBSERVED_AT","F","S",{"method":"incorrect_cast"})
    assert not validate_graph(g)["passed"]


def test_inferred_sketch_context_not_turned_into_observation():
    parts = list(_clauses("推测前方岩体破碎。节理发育。当前开挖段落见线状出水。建议加强支护。"))
    assert [p[1] for p in parts] == ["inferred","inferred","observed"]
    assert all("建议" not in p[0] for p in parts)


def test_SQLite_and_JSON_roundtrip_preserve_real_relations(tmp_path):
    g = fixture_graph()
    path = tmp_path/"evidence.sqlite"
    g.save_sqlite(path)
    again = EvidenceGraph.load_sqlite(path)
    assert again.snapshot() == g.snapshot()
    assert compare_retrieval(again,DAY)["all_equal"]
    with pytest.raises(FileExistsError):
        g.save_sqlite(path)


def test_daily_snapshot_drops_future_sources():
    g = fixture_graph("2024-04-27")
    sub = daily_subgraph(g,DAY)
    assert "F" not in sub.nodes and "D" not in sub.nodes


def add_claim(g):
    payload = {"report_date":DAY,"support_ids":["F"],"statement":"原报告预测5米范围为V级。", "selected_for_report":True}
    g.add_node("ReportClaim","C",payload)
    g.add_edge("SUPPORTED_BY","C","F",{"method":"fixture_claim_support"})


def test_changed_Chinese_meter_value_detected():
    g = fixture_graph()
    add_claim(g)
    assert check_claim(g,"C",DAY,text="原报告预测9米范围为V级。")["status"] == "FAIL"


def test_extra_non_numeric_assertion_is_not_automatically_PASS():
    g = fixture_graph()
    add_claim(g)
    check = check_claim(g,"C",DAY,text="原报告预测5米范围为V级。围岩完整无水。")
    assert check["status"] == "REVIEW"


def test_correct_ID_cannot_legitimize_causal_hallucination():
    g = fixture_graph()
    add_claim(g)
    assert check_claim(g,"C",DAY,text="原报告预测5米范围为V级，导致设备故障。")["status"] == "FAIL"


def test_wrong_support_id_or_deleted_support_detected():
    g = fixture_graph()
    add_claim(g)
    assert check_claim(g,"C",DAY,supports=[])["status"] == "FAIL"
    assert check_claim(g,"C",DAY,supports=["missing"])["status"] == "FAIL"


def test_source_trace_reaches_original_document():
    g = fixture_graph()
    add_claim(g)
    assert {n["type"] for n in g.trace("C")["nodes"]} >= {"ReportClaim","GeologyFact","GeologyDocument"}
    assert check_claim(g,"C",DAY)["status"] == "PROGRAM_SUPPORTED"


@pytest.mark.parametrize("text,geometry,lo,hi",[
    ("DyK1015+789~DyK1015+819", "interval",1015789.,1015819.),
    ("DyK1015+819.0～DyK1015+849.0", "interval",1015819.,1015849.),
    ("DyK1015+764附近", "point",1015764.,1015764.),
    ("线状出水", "unresolved",None,None),
])
def test_current_JSON_clause_geometry_not_header_broadcast(text,geometry,lo,hi):
    actual = mileage_geometry(text)
    assert (actual["geometry"],actual["start_m"],actual["end_m"]) == (geometry,lo,hi)


def test_current_sketch_source_replaces_old_sketches_and_preserves_files(tmp_path):
    folder = tmp_path/"伯舒拉岭右线洞身素描1"/"record"
    folder.mkdir(parents=True)
    path = folder/"数据.json"
    path.write_text(json.dumps({"里程":"DyK1015+819～DyK1015+849","素描图":[{"图":"https://example.invalid/img/2024/4/27/a.jpg"}],
                               "施工揭示":[{"水文地质特征":"线状出水"}]},ensure_ascii=False))
    before = path.read_bytes()
    base = {"reports":[{"report_metadata":{"source_type":"sketch"}},{"report_metadata":{"source_type":"hsp"}}]}
    new, audit = load_current_catalog(base,tmp_path)
    assert path.read_bytes() == before
    assert len(new["reports"]) == 2 and audit["legacy_sketches_removed_from_input"] == 1
    assert audit["confirmed_availability_dates"] == 1
    metadata = new["reports"][-1]["report_metadata"]
    assert metadata["observation_date"] is None and metadata["available_date"] == "2024-04-27"
    assert "user_confirmed" in metadata["availability_basis"]


@pytest.mark.parametrize("available,expected",[("2024-04-25","eligible"),(DAY,"review"),("2024-04-27","excluded")])
def test_user_confirmed_availability_preserves_date_precision(available,expected):
    document = {"metadata":{"available_date_user_confirmed":True,"available_date":available,
                "availability_basis":"user_confirmed","chainage_prefix":"DyK","alignment":"right"},
                "date_audit":{"status":"PASS"}}
    result = document_gate(document,DAY)
    assert result["status"] == expected
    assert not result.get("available_at")


def test_JSON_pointer_lineage_and_hash_checked(tmp_path):
    p = tmp_path/"数据.json"
    p.write_text('{"字段":"真实原文"}')
    ref = {"path":str(p),"sha256":digest(p),"page":None,"source_pointer":"/字段","quote":"真实原文"}
    check, text = SourceAudit().page(ref)
    assert check["source_pointer_verified"] and check["quote_exact_after_whitespace"]
    assert not check["page_verified"]
    bad = {**ref,"source_pointer":"/不存在"}
    assert SourceAudit().page(bad)[0]["status"] == "FAIL"


def test_false_grade_difference_edge_rejected_by_content():
    g = fixture_graph()
    second = {**deepcopy(g.nodes["F"]["data"]),"fact_id":"F2"}
    g.add_node("GeologyFact","F2",second)
    g.add_edge("DIFFERS_FROM","F","F2",{"method":"wrong_equal_grades"})
    assert not validate_graph(g)["passed"]


def test_PLC_baseline_must_precede_current_response():
    g = fixture_graph()
    g.add_node("PLCResponse","P",{"start_time":"2024-04-26T09:00:00","end_time":"2024-04-26T10:00:00"})
    g.add_node("PLCResponse","R",{"start_time":"2024-04-26T11:00:00","end_time":"2024-04-26T12:00:00"})
    g.add_node("EngineeringComparison","CP",{"comparison_type":"historical_response_comparison","source_fact_ids":[],"source_response_ids":["P","R"]})
    for p in ("P","R"):
        g.add_edge("COMPARES","CP",p,{"method":"fixture_membership"})
    assert any(e["reason"]=="future_or_overlapping_PLC_baseline" for e in validate_graph(g)["errors"])


def test_risk_without_explicit_risk_word_in_saved_quote_is_REVIEW(tmp_path):
    p = tmp_path/"source.json"
    p.write_text('{"字段":"断层影响区间"}')
    metadata = {"source_report_id":"fixture-risk","source_type":"sketch","source_file":p.name,
                "structured_source_path":str(p),"source_sha256":digest(p),"chainage_prefix":"DyK","alignment":"right"}
    report = {"report_metadata":metadata,"risk_records":[{"risk_id":"R","risk_type":"掉块风险","geometry":"point", "chainage_m":20.,
                "source_pointer":"/字段","source_quote":"断层影响区间"}]}
    g = build_catalog_graph({"reports":[report]},{"source_registry":[]},tmp_path)
    fact = next(g.typed("GeologyFact"))["data"]
    assert fact["extraction_quality"]["status"] == "REVIEW"


def test_conflicting_URL_dates_are_not_arbitrarily_resolved(tmp_path):
    folder = tmp_path/"伯舒拉岭右线洞身素描1"/"record"
    folder.mkdir(parents=True)
    (folder/"数据.json").write_text(json.dumps({"里程":"DyK1015+819～DyK1015+849", "素描图":[{"x":"https://x.invalid/img/2024/4/25/a.jpg"}],
                                           "现场照片":[{"x":"https://x.invalid/img/2024/4/27/b.jpg"}]}))
    new,audit = load_current_catalog({"reports":[]},tmp_path)
    assert audit["confirmed_availability_dates"] == 0
    assert new["reports"][0]["report_metadata"]["available_date"] is None


def test_identical_JSON_copies_keep_all_paths_but_one_document(tmp_path):
    for name in ("a","b"):
        folder=tmp_path/"伯舒拉岭右线洞身素描1"/name
        folder.mkdir(parents=True)
        (folder/"数据.json").write_text('{"里程":"DyK1015+819～DyK1015+849"}')
    catalog,audit=load_current_catalog({"reports":[]},tmp_path)
    assert audit["current_sketch_count"]==2 and audit["unique_sketch_records"]==1
    assert len(catalog["reports"])==1
    assert len(catalog["reports"][0]["report_metadata"]["all_identical_source_paths"])==2
