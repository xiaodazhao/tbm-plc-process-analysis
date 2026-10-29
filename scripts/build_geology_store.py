"""Import existing geology, audit real coverage, and replay three offline report applications."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tbm_process.geology_store import VERSION, GeologyStore, import_catalog
from tbm_process.geology_store.applications import construction_analysis, daily_observations, persist_responses
from tbm_process.geology_store.reporting import make_narrative, render_report
from tbm_process.geology_store.visualization import plot_axis
from tbm_process.reporting_v2.facts import build_facts, digest, write_json
from tbm_process.reporting_v2.rendering import render_figures, make_pdf, validate_artifacts


def csv_rows(path, rows, headers=None):
    rows = list(rows)
    headers = headers or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        w = csv.DictWriter(stream, fieldnames=headers)
        w.writeheader()
        for row in rows:
            w.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in headers})


def run(args):
    output = args.output_root/datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    print("OUTPUT_DIRECTORY="+str(output), flush=True)
    protected = {str(p.relative_to(ROOT)): digest(p) for p in (ROOT/"tbm_process").rglob("*")
                 if p.is_file() and p.suffix in {".py", ".json", ".html"} and "geology_store" not in p.parts}
    for p in [ROOT/"config.json", ROOT/"configs/platform_analysis.json", ROOT/"scripts/run_integrated_report.py"]:
        protected[str(p.relative_to(ROOT))] = digest(p)
    write_json(output/"protected_sources.before.json", protected)
    database = output/"geology.sqlite"
    meta = import_catalog(database, base_catalog=args.catalog, registry=args.registry, source_root=args.source_root,
                          sketch_root=args.sketch_root, plc_facts=args.plc_facts, plc_matches=args.plc_matches)
    print("Imported sources", flush=True)
    store = GeologyStore(database)
    write_json(output/"import_metadata.json", meta)
    documents = list(store.documents.values())
    facts = [json.loads(r[0]) for r in store.con.execute("SELECT data_json FROM facts ORDER BY fact_id")]
    write_json(output/"documents.json", documents)
    write_json(output/"facts.json", facts)
    csv_rows(output/"document_index.csv", [dict(r) for r in store.con.execute("SELECT document_id,document_type,line,chainage_system,start_m,end_m,test_date,document_date,submission_date,available_at,available_date,observation_date,time_precision,file_hash,original_path FROM documents")])
    csv_rows(output/"fact_index.csv", [dict(r) for r in store.con.execute("SELECT fact_id,document_id,source_id,record_id,fact_type,information_role,geometry,line,chainage_system,start_m,end_m,point_m,raw_chainage,quality_status FROM facts")])
    csv_rows(output/"time_evidence.csv", [dict(r) for r in store.con.execute("SELECT * FROM time_evidence")])
    audit_range = meta["plc_audit_range_m"]
    historical = store.query_geological_coverage(*audit_range)
    write_json(output/"coverage.all_history.json", historical)
    csv_rows(output/"coverage.all_partitions.csv", historical["partitions"])
    csv_rows(output/"coverage.all_gaps.csv", historical["gaps"], ["start_m", "end_m", "length_m"])
    plot_axis(store, *audit_range, output=output/"coverage.all_history.png")
    hist_cutoff = store.query_geological_coverage(*audit_range, as_of="2024-03-15")
    write_json(output/"coverage.as_of_2024-03-15.json", hist_cutoff)
    plot_axis(store, *audit_range, as_of="2024-03-15", output=output/"coverage.as_of_2024-03-15.png")
    daily_rows = []
    for row in store.con.execute("SELECT date,start_m,end_m,spatial_allowed FROM plc_days ORDER BY date"):
        if not row["spatial_allowed"] or row["start_m"] == row["end_m"]:
            daily_rows.append({"date": row["date"], "start_m": row["start_m"], "end_m": row["end_m"], "status": "spatial_excluded_or_zero_length"})
            continue
        lo, hi = sorted((row["start_m"], row["end_m"]))
        cov = store.query_geological_coverage(lo, hi, as_of=row["date"])
        daily_rows.append({"date": row["date"], "start_m": lo, "end_m": hi, "status": "historical_cutoff",
                           **{k: cov[k] for k in ("length_m", "tsp_m", "hsp_m", "union_m", "gap_m", "coverage_ratio")}})
    csv_rows(output/"all_PLC_days_historical_coverage.csv", daily_rows, ["date", "start_m", "end_m", "status", "length_m", "tsp_m", "hsp_m", "union_m", "gap_m", "coverage_ratio"])
    cases = output/"cases"
    cases.mkdir()
    temporal = []
    for when in (None, "2024-03-15", "2024-03-16", "2024-03-19"):
        q = store.query_geology_by_chainage(1015392, 1015431, as_of=when, source_types=("TSP", "HSP"))
        cov = store.query_geological_coverage(1015392, 1015431, as_of=when)
        day_query = store.query_geology_by_chainage(1015392, 1015411, as_of=when, source_types=("TSP", "HSP"))
        ahead_query = store.query_geology_by_chainage(1015411, 1015431, as_of=when, source_types=("TSP", "HSP"))
        day_coverage = store.query_geological_coverage(1015392, 1015411, as_of=when)
        ahead_coverage = store.query_geological_coverage(1015411, 1015431, as_of=when)
        label = when or "all_history"
        write_json(cases/("2024-03-15_time_"+label+".json"), {"query": q, "coverage": cov,
                   "construction_day_query": day_query, "construction_day_coverage": day_coverage,
                   "ahead_query": ahead_query, "ahead_coverage": ahead_coverage})
        temporal.append({"as_of": label, "covered_m": cov["union_m"], "gap_m": cov["gap_m"],
                         "day_covered_m": day_coverage["union_m"], "ahead_covered_m": ahead_coverage["union_m"],
                         "reports": [d["source_file"] for d in q["documents"]]})
    csv_rows(cases/"2024-03-15_time_comparison.csv", temporal)
    pdf_dates = []
    for d in documents:
        if d["document_type"] == "HSP" and d["start_m"] in (1015323, 1015405):
            record = {"document_id": d["document_id"], "path": d["original_file_path"], "sha256": digest(Path(d["original_file_path"])),
                      "test_date": d["metadata"]["test_date"], "submission_date": d["metadata"]["submission_date"], "date_audit": d["date_audit"]}
            preflight = Path.home()/".codex/skills/academic-research-suite/ars/scripts/pdf_read_preflight.py"
            reader_python = sys.executable
            bundled = Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
            if not importlib.util.find_spec("pypdf") and bundled.is_file():
                reader_python = str(bundled)
            result = subprocess.run([reader_python, str(preflight), d["original_file_path"]], capture_output=True, text=True)
            record["structural_preflight"] = {"exit_code": result.returncode, "output": result.stdout, "stderr": result.stderr}
            pdf_dates.append(record)
    write_json(cases/"original_HSP_date_evidence.json", pdf_dates)
    assert len(pdf_dates) == 2
    assert not temporal[1]["covered_m"] and temporal[2]["covered_m"] == 31 and temporal[3]["covered_m"] == 39
    for distance in (10, 13, 20, 50):
        write_json(cases/f"2024-04-26_ahead_{distance}m.json", store.query_forecasts_ahead("2024-04-26", 1015828, distance, 1))
    write_json(cases/"2024-04-26_ahead_reverse_13m.json", store.query_forecasts_ahead("2024-04-26", 1015828, 13, -1))
    summaries = []
    for day in args.dates:
        folder = output/"reports"/day
        folder.mkdir(parents=True)
        catalog_path = folder/"time_admissible_catalog.json"
        write_json(catalog_path, store.export_catalog(day))
        config = json.loads(args.config.read_text())
        config["analysis"]["ahead_metres"] = args.ahead
        package, samples = build_facts(report_day=day, project=ROOT, analysis=args.analysis_dir, catalog_path=catalog_path,
                                      registry_path=args.registry, source_root=args.source_root, config=config)
        package["schema_version"] += "+"+VERSION
        current = construction_analysis(store, day, package, samples, json.loads((ROOT/"config.json").read_text()))
        observations = daily_observations(store, day)
        end = package["geology"]["shield_end_m"]
        direction = package["geology"]["direction"]
        ahead = store.query_forecasts_ahead(day, end, args.ahead, direction)
        application = {"construction": current, "new_sketches": observations, "ahead": ahead}
        write_json(folder/"applications.json", application)
        persist_responses(store, current)
        write_json(folder/"report_facts.json", package)
        samples.to_csv(folder/"performance_samples.csv", index=False, encoding="utf-8-sig")
        draft = make_narrative(package, current, observations, ahead, folder)
        figures = render_figures(package, samples, folder)
        lower = min(package["geology"]["day_range_m"][0], ahead["coverage"]["range_m"][0])
        upper = max(package["geology"]["day_range_m"][1], ahead["coverage"]["range_m"][1])
        plot_axis(store, lower, upper, as_of=day, plc_day=day, ahead=ahead["coverage"]["range_m"], output=folder/"figures/unified_axis.png")
        page = render_report(package, draft, current, observations, ahead, folder)
        pdf = folder/(day+".geology_store_report.pdf")
        make_pdf(page, pdf, args.chrome)
        check = validate_artifacts(package, draft, figures, pdf)
        write_json(folder/"validation.json", check)
        if not check["passed"]:
            raise ValueError("Report validation failed; partial outputs retained: "+day)
        summary = {"date": day, "pdf": str(pdf), "pages": check["pdf_pages"], "platforms": package["platform_count"],
                   "day_forecast_coverage_m": current["coverage"]["union_m"], "response_summaries": len(current["responses"]),
                   "new_sketch_records": len(observations["records"]), "confirmed_observation_dates": observations["confirmed_actual_observation_date_count"],
                   "ahead_m": args.ahead, "ahead_forecast_coverage_m": ahead["coverage"]["union_m"], "actual_api_calls": 0, "validation_passed": True}
        write_json(folder/"summary.json", summary)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    checks = {"protected_code_unchanged": all(digest(ROOT/name) == sha for name, sha in protected.items()),
              "original_sketches_unchanged": all(digest(Path(r["path"])) == r["sha256"] for r in meta["sketch_audit"]["entries"]),
              "foreign_keys_ok": not store.con.execute("PRAGMA foreign_key_check").fetchall(),
              "integrity_ok": store.con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"}
    summary = {"version": VERSION, "database": str(database), "documents": dict(Counter(d["document_type"] for d in documents)),
               "fact_count": len(facts), "fact_types": dict(Counter(f["fact_type"] for f in facts)),
               "source_segments": len({(f["document_id"], f["record_id"]) for f in facts if f["fact_type"] == "geological_description" and f["document_type"] in {"TSP", "HSP"}}),
               "plc_days": len(daily_rows), "plc_spatial_allowed_days": store.con.execute("SELECT COUNT(*) FROM plc_days WHERE spatial_allowed=1").fetchone()[0],
               "plc_excluded_days": [dict(r) for r in store.con.execute("SELECT date,data_json FROM plc_days WHERE spatial_allowed=0")],
               "plc_range_m": audit_range, "coverage": historical, "date_case": temporal,
               "reports": summaries, "checks": checks, "actual_api_calls": 0,
               "unresolved": {"independent_design_documents": 0, "sketch_actual_observation_dates": sum(bool(d["metadata"].get("observation_date")) for d in documents if d["document_type"] == "SKETCH"),
                              "unlocated_facts": sum(f.get("start_m") is None for f in facts),
                              "source_review_facts": sum(f["extraction_quality"]["status"] != "PASS" for f in facts),
                              "filename_date_mismatch_documents": sum(bool(d["date_audit"].get("filename_date_mismatch")) for d in documents)}}
    write_json(output/"summary.json", summary)
    write_json(args.output_root/"latest.json", {"directory": str(output), "database": str(database)})
    assert all(checks.values())
    store.close()
    print("COMPLETE="+str(output), flush=True)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, default=ROOT/"datasets/portable_30/geology/extracted/geology_catalog_right_line.json")
    p.add_argument("--registry", type=Path, default=Path.home()/"Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json")
    p.add_argument("--source-root", type=Path, default=Path.home()/"Desktop/伯舒拉岭数据")
    p.add_argument("--sketch-root", type=Path, default=Path.home()/"Desktop/伯舒拉岭数据/右线地质资料")
    p.add_argument("--plc-facts", type=Path, default=ROOT/"experiments/plc_mapping_repair_v1/impact/report_facts/after")
    p.add_argument("--plc-matches", type=Path, default=ROOT/"experiments/plc_mapping_repair_v1/impact/geology_matches/after")
    p.add_argument("--analysis-dir", type=Path, default=ROOT/"experiments/plc_mapping_repair_v1/replay/after")
    p.add_argument("--config", type=Path, default=ROOT/"tbm_process/reporting_v2/defaults.json")
    p.add_argument("--dates", nargs="+", default=["2024-04-26", "2024-03-15", "2024-04-22"])
    p.add_argument("--ahead", type=float, default=20)
    p.add_argument("--output-root", type=Path, default=Path.home()/"Desktop/TBM_时空地质证据库_v1_20261027")
    p.add_argument("--chrome", type=Path)
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
