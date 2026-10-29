#!/usr/bin/env python3
"""Run the independent six-chapter TBM engineering report; offline by default."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tbm_process.reporting_v2 import VERSION
from tbm_process.reporting_v2.facts import build_facts, digest, write_json
from tbm_process.reporting_v2.narrative import generate_narrative
from tbm_process.reporting_v2.rendering import extract_sketch_images, make_pdf, render_figures, render_html, validate_artifacts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", required=True)
    p.add_argument("--layout", choices=["six", "three"], default="six", help="Opt in to the source-specific three-chapter report")
    p.add_argument("--analysis-dir", type=Path, default=ROOT/"experiments/plc_mapping_repair_v1/replay/after")
    p.add_argument("--catalog", type=Path, default=ROOT/"datasets/portable_30/geology/extracted/geology_catalog_right_line.json")
    p.add_argument("--registry", type=Path, default=Path.home()/"Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json")
    p.add_argument("--source-root", type=Path, default=Path.home()/"Desktop/伯舒拉岭数据")
    p.add_argument("--output-dir", type=Path, default=ROOT/"outputs/engineering_reports_v2")
    p.add_argument("--config", type=Path, default=ROOT/"tbm_process/reporting_v2/defaults.json")
    p.add_argument("--mode", choices=["offline", "llm"], default="offline")
    p.add_argument("--model", default="deepseek-chat")
    p.add_argument("--env-file", type=Path, default=ROOT/".env")
    p.add_argument("--chrome", type=Path)
    args = p.parse_args()
    if args.layout == "three":
        from run_three_chapter_report import prepare, llm
        output = args.output_dir.resolve()/args.date/datetime.now().strftime("%Y%m%dT%H%M%S")
        output.mkdir(parents=True, exist_ok=False)
        prepare(output, [args.date], analysis=args.analysis_dir)
        if args.mode == "llm":
            llm(output, args.date, args.env_file)
        print("OUTPUT_DIRECTORY="+str(output))
        return
    config = json.loads(args.config.read_text())
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = args.output_dir.resolve()/args.date/run_id
    output.mkdir(parents=True, exist_ok=False)
    protected = {str(f.relative_to(ROOT)): digest(f) for f in (ROOT/"tbm_process").rglob("*.py") if "reporting_v2" not in f.parts}
    for f in [ROOT/"config.json", ROOT/"configs/platform_analysis.json"]:
        protected[str(f.relative_to(ROOT))] = digest(f)
    write_json(output/"protected_sources.before.json", protected)
    package, samples = build_facts(report_day=args.date, project=ROOT, analysis=args.analysis_dir, catalog_path=args.catalog,
                                   registry_path=args.registry, source_root=args.source_root, config=config)
    write_json(output/"report_facts.json", package)
    write_json(output/"method_config.used.json", config)
    write_json(output/"engineering_performance.json", package["performance"])
    write_json(output/"performance_windows.json", package["performance"]["windows"])
    write_json(output/"geology_matching.json", package["geology"])
    write_json(output/"attention_selection.json", package["attention"])
    write_json(output/"data_quality.json", {"performance": package["performance"]["quality"], "coverage": package["daily_overview"], "geology_exclusions": package["geology"]["exclusions"]})
    columns = ["time", "source_file", "source_row", "chainage", "machine_state", "phase_label", "experimental_platform_id", "advance_speed", "thrust", "torque", "rpm_canonical", "pr", "fpi", "se", "performance_eligible"]
    samples[columns].to_csv(output/"performance_samples.csv", index=False, encoding="utf-8-sig")
    api_key = None
    if args.mode == "llm":
        from tbm_process.final_reporting.common import load_deepseek_api_key
        api_key = load_deepseek_api_key(args.env_file)
    narrative, generation = generate_narrative(package, output, mode=args.mode, model=args.model, api_key=api_key)
    figures = render_figures(package, samples, output)
    images = extract_sketch_images(package, output)
    html = render_html(package, narrative, images, output, generation)
    pdf = output/f"{args.date}.engineering_report.pdf"
    make_pdf(html, pdf, args.chrome)
    validation = validate_artifacts(package, narrative, figures, pdf)
    unchanged = all(digest(ROOT/name) == sha for name, sha in protected.items())
    validation["protected_code_unchanged"] = unchanged
    validation["passed"] &= unchanged and generation["passed"]
    write_json(output/"validation.json", validation)
    summary = {"version": VERSION, "date": args.date, "status": "offline_complete_candidate" if args.mode == "offline" else generation["mode"],
               "pdf": str(pdf), "html": str(html), "validation_passed": validation["passed"], "pdf_pages": validation["pdf_pages"],
               "platform_count": package["platform_count"], "performance_samples": package["performance"]["daily"]["sample_count"],
               "performance_statistics": package["performance"]["daily"]["statistics"], "performance_windows": len(package["performance"]["windows"]),
               "day_geology_segments": len(package["geology"]["day_partitions"]), "ahead_range_m": package["geology"]["ahead_range_m"],
               "sketch_images": len(images["images"]), "same_day_sketches_pending": len(package["geology"]["same_day_pending_observations"]),
               "attention_selected": len(package["attention"]["selected"]), "generation": generation,
               "llm_api_calls": generation["api_calls"], "protected_code_unchanged": unchanged}
    write_json(output/"run_summary.json", summary)
    write_json(output/"MANIFEST.json", [{"path": str(f.relative_to(output)), "bytes": f.stat().st_size, "sha256": digest(f)} for f in sorted(output.rglob("*")) if f.is_file() and f.name != "MANIFEST.json"])
    print(json.dumps(summary, ensure_ascii=False))
    if not validation["passed"]:
        raise SystemExit("Artifacts retained but validation failed; see validation.json")


if __name__ == "__main__":
    main()
