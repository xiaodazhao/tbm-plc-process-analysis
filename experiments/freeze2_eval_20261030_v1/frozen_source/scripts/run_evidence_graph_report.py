#!/usr/bin/env python3
"""Build source-bound TBM evidence queries and offline daily reports. No paid API calls."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tbm_process.evidence_graph import VERSION
from tbm_process.evidence_graph.analysis import add_daily_analysis
from tbm_process.evidence_graph.catalog import build_catalog_graph, eligible_catalog
from tbm_process.evidence_graph.claims import check_claim, organize_claims
from tbm_process.evidence_graph.queries import compare_retrieval, daily_subgraph, query_day
from tbm_process.evidence_graph.reporting import add_evidence_appendix, insert_current_sketches, make_report_input
from tbm_process.evidence_graph.sketches import load_current_catalog
from tbm_process.evidence_graph.validation import future_isolation_test, validate_graph
from tbm_process.evidence_graph.visualization import draw_graph, select_trace_examples
from tbm_process.reporting_v2.facts import build_facts, digest, write_json
from tbm_process.reporting_v2.rendering import extract_sketch_images, make_pdf, render_figures, render_html, validate_artifacts


def run(args):
    config = json.loads(args.config.read_text())
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = args.output_root/timestamp
    output.mkdir(parents=True, exist_ok=False)
    protected = {str(p.relative_to(ROOT)): digest(p) for p in (ROOT/"tbm_process").rglob("*")
                 if p.is_file() and p.suffix in {".py", ".json"} and "evidence_graph" not in p.parts}
    for p in [ROOT/"config.json", ROOT/"configs/platform_analysis.json", ROOT/"scripts/run_integrated_report.py", ROOT/"scripts/run_engineering_report_v2.py"]:
        protected[str(p.relative_to(ROOT))] = digest(p)
    write_json(output/"protected_sources.before.json", protected)
    original_catalog = json.loads(args.catalog.read_text())
    catalog, sketch_audit = load_current_catalog(original_catalog, args.sketch_root)
    write_json(output/"current_sketch_source_audit.json", sketch_audit)
    write_json(output/"input_catalog.current_sources.json", catalog)
    registry = json.loads(args.registry.read_text()) if args.registry.is_file() else {"source_registry": []}
    graph = build_catalog_graph(catalog, registry, args.source_root, config.get("design_records", []))
    graph.metadata["current_sketch_source_policy"] = {k:v for k,v in sketch_audit.items() if k != "entries"}
    graph.metadata["input_sources"] = {"catalog_path": str(args.catalog), "catalog_sha256": digest(args.catalog),
                                      "sketch_root": str(args.sketch_root), "registry_path": str(args.registry),
                                      "registry_sha256": digest(args.registry) if args.registry.is_file() else None}
    write_json(output/"source_date_audit.json", graph.metadata["source_audit"])
    summaries = []
    for day in args.dates:
        folder = output/day
        folder.mkdir()
        filtered, decisions = eligible_catalog(catalog, graph, day)
        write_json(folder/"time_admissible_catalog.json", filtered)
        write_json(folder/"source_time_decisions.json", decisions)
        package, samples = build_facts(report_day=day, project=ROOT, analysis=args.analysis_dir,
                                      catalog_path=folder/"time_admissible_catalog.json", registry_path=args.registry,
                                      source_root=args.source_root, config=config)
        package["schema_version"] += "+"+VERSION
        package["current_sketch_policy"] = graph.metadata["current_sketch_source_policy"]
        package["geology"]["total_catalog_reports"] = len(catalog["reports"])
        package["geology"]["exclusions"] = [r for r in decisions if r["status"] != "eligible"]
        package["geology"]["same_day_pending_observations"] = [r for r in decisions if r.get("reason") == "same_day_availability_time_unknown"]
        write_json(folder/"report_facts.json", package)
        samples[["time", "source_file", "source_row", "chainage", "machine_state", "experimental_phase_label",
                 "advance_speed", "thrust", "torque", "rpm_canonical", "pr", "fpi", "se", "performance_eligible"]].to_csv(folder/"performance_samples.csv", index=False, encoding="utf-8-sig")
        phase_config = json.loads((ROOT/"config.json").read_text())
        add_daily_analysis(graph, package, samples, phase_config)
        write_json(folder/"report_facts.json", package)
        organize_claims(graph, day)
        graph_check = validate_graph(graph, day)
        write_json(folder/"graph.validation.json", graph_check)
        if not graph_check["passed"]:
            raise ValueError("Graph validation failed for "+day+"; artifacts retained")
        write_json(folder/"queries.graph.json", query_day(graph, day, method="graph"))
        write_json(folder/"queries.flat.json", query_day(graph, day, method="flat"))
        retrieval = compare_retrieval(graph, day)
        write_json(folder/"retrieval_comparison.json", retrieval)
        if not retrieval["all_equal"]:
            raise ValueError("Flat/graph input scope mismatch; do not claim graph improvement")
        write_json(folder/"future_isolation.json", future_isolation_test(graph, day))
        traces = select_trace_examples(graph, day)
        write_json(folder/"claim_traces.json", traces)
        selected_ids = graph.metadata["claim_selection"][day]["selected_ids"]
        rng = random.Random(20261027)
        selected_check = rng.sample(selected_ids, min(5,len(selected_ids)))
        write_json(folder/"random_claim_audit.json", {"seed":20261027, "sampled_claim_ids":selected_check,
                   "checks":[check_claim(graph,i,day) for i in selected_check], "human_labels":None})
        visual = draw_graph(graph,traces,folder/"evidence_graph")
        write_json(folder/"evidence_graph.visualization.json", visual)
        narrative = make_report_input(package, graph, day, folder)
        figures = render_figures(package,samples,folder)
        images = extract_sketch_images(package,folder)
        html = render_html(package,narrative,images,folder,{"mode":"offline_graph_evidence_not_llm_generated"})
        insert_current_sketches(html,graph,day,package)
        add_evidence_appendix(html,Path(visual["png"]),traces)
        pdf = folder/f"{day}.evidence_graph_report.pdf"
        make_pdf(html,pdf,args.chrome)
        validation = validate_artifacts(package,narrative,figures,pdf)
        validation["all_selected_graph_claims_in_pdf"] = next(c["passed"] for c in validation["checks"] if c["name"] == "checked_narrative_is_in_final_pdf")
        validation["protected_code_unchanged"] = all(digest(ROOT/name)==value for name,value in protected.items())
        validation["passed"] &= validation["protected_code_unchanged"]
        write_json(folder/"report.validation.json",validation)
        if not validation["passed"]:
            raise ValueError("Final report consistency failed for "+day)
        subgraph = daily_subgraph(graph,day)
        subgraph.save_sqlite(folder/"daily_evidence.sqlite")
        write_json(folder/"daily_evidence.json",subgraph.snapshot())
        write_json(folder/"selected_and_omitted_claims.json",graph.metadata["claim_selection"][day])
        query = query_day(graph,day)
        summary = {"version": VERSION, "date": day, "pdf": str(pdf), "pages": validation["pdf_pages"],
                   "platform_count": package["platform_count"], "performance_samples": package["performance"]["daily"]["sample_count"],
                   "current_forecast_facts": len(query["A_current_geology"]["fact_ids"]),
                   "ahead_forecast_facts": len(query["D_ahead_geology"]["fact_ids"]),
                   "observed_sketch_facts": len(query["C_sketches"]["observed_fact_ids"]),
                   "PLC_responses": len(query["B_PLC_response"]["response_ids"]),
                   "comparison_count": len(query["B_PLC_response"]["comparison_ids"]),
                   "selected_claims": len(selected_ids), "graph_nodes":len(subgraph.nodes), "graph_edges":len(subgraph.edges),
                   "actual_api_calls":0, "generation_mode":"offline_program_generated_not_LLM",
                   "validation_passed":validation["passed"], "retrieval_sets_equal":retrieval["all_equal"],
                   "sketch_source":"current_desktop_structured_JSON_only_no_legacy_fallback"}
        write_json(folder/"run_summary.json",summary)
        summaries.append(summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    graph.save_sqlite(output/"all_evidence.sqlite")
    write_json(output/"all_evidence.json",graph.snapshot())
    write_json(output/"summary.json", {"version":VERSION,"runs":summaries,"actual_api_calls":0,
               "node_types":dict(Counter(n["type"] for n in graph.nodes.values())),
               "relation_types":dict(Counter(e["type"] for e in graph.edges.values())),
               "source_time_audits":dict(Counter(a["status"] for a in graph.metadata["source_audit"]))})
    write_json(output/"MANIFEST.json",[{"path":str(p.relative_to(output)),"bytes":p.stat().st_size,"sha256":digest(p)}
                                      for p in sorted(output.rglob("*")) if p.is_file() and p.name!="MANIFEST.json"])
    print("OUTPUT_DIRECTORY="+str(output),flush=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates",nargs="+",default=["2024-04-26","2023-11-18","2024-04-25","2024-03-15"])
    parser.add_argument("--analysis-dir",type=Path,default=ROOT/"experiments/plc_mapping_repair_v1/replay/after")
    parser.add_argument("--catalog",type=Path,default=ROOT/"datasets/portable_30/geology/extracted/geology_catalog_right_line.json")
    parser.add_argument("--registry",type=Path,default=Path.home()/"Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json")
    parser.add_argument("--source-root",type=Path,default=Path.home()/"Desktop/伯舒拉岭数据")
    parser.add_argument("--sketch-root",type=Path,default=Path.home()/"Desktop/伯舒拉岭数据/右线地质资料")
    parser.add_argument("--config",type=Path,default=ROOT/"tbm_process/reporting_v2/defaults.json")
    parser.add_argument("--output-root",type=Path,default=ROOT/"outputs/evidence_graph_v1")
    parser.add_argument("--chrome",type=Path)
    args = parser.parse_args()
    if len(set(args.dates)) != len(args.dates):
        parser.error("Report dates must not repeat")
    run(args)


if __name__=="__main__":
    main()
