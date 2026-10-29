"""Independent read-only arithmetic/source audit of a completed geology-store run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

import fitz
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tbm_process.geology_store import GeologyStore


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def save(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


def independent_union(intervals):
    length = 0
    end = None
    for lo, hi in sorted(intervals):
        if end is None or lo > end:
            length += hi-lo
            end = hi
        elif hi > end:
            length += hi-end
            end = hi
    return length


def audit(run):
    output = run/"audit"
    output.mkdir(exist_ok=True)
    db = GeologyStore(run/"geology.sqlite")
    summary = json.loads((run/"summary.json").read_text())
    checks = []
    def check(name, passed, detail=None):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
    sources = []
    for did, doc in db.documents.items():
        p = Path(doc["original_file_path"])
        actual = sha(p) if p.is_file() else None
        sources.append({"document_id": did, "kind": doc["document_type"], "original_path": str(p), "expected_sha256": doc["source_version"],
                        "actual_sha256": actual, "passed": actual == doc["source_version"]})
    save(output/"all_source_hashes.json", sources)
    check("all_original_sources_present_and_hash_match", all(s["passed"] for s in sources), len(sources))
    intervals = []
    original_ranges = []
    pages = {}
    for r in db.con.execute("SELECT data_json FROM facts WHERE fact_type='geological_description' AND geometry='interval' AND information_role='forecast' AND quality_status='PASS'"):
        f = json.loads(r[0])
        d = db.documents[f["document_id"]]
        if d["document_type"] not in ("TSP", "HSP"):
            continue
        intervals.append((f, d))
        source = f["source"]
        key = source["path"], source["page"]
        if key not in pages:
            with fitz.open(source["path"]) as pdf:
                pages[key] = pdf[source["page"]-1].get_text()
        text = re.sub(r"\s+", "", pages[key]).lower()
        tokens = []
        for value in (f["start_m"], f["end_m"]):
            km, metres = divmod(value, 1000)
            padded = f"{metres:07.3f}".rstrip("0").rstrip(".")
            tokens.append([f"{int(km)}+{metres:g}", f"{int(km)}+{padded}"])
        found = all(any(t in text for t in alternatives) for alternatives in tokens)
        original_ranges.append({"fact_id": f["fact_id"], "document_id": f["document_id"], "source": source["path"],
                                "page": source["page"], "start_m": f["start_m"], "end_m": f["end_m"],
                                "coordinate_tokens_on_original_page": found,
                                "result": "coordinate_tokens_found_not_semantic_validation" if found else "REVIEW_page_format_or_range_needs_manual_check",
                                "source_quote": source.get("quote")})
    with (output/"original_segment_page_checks.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        w = csv.DictWriter(stream, fieldnames=list(original_ranges[0]));w.writeheader();w.writerows(original_ranges)
    for when in (None, "2024-03-15", "2024-03-16", "2024-03-19"):
        lower, upper = summary["plc_range_m"] if when is None else (1015392, 1015431)
        chosen = [(f, d) for f, d in intervals if when is None or (d.get("submission_date") and d["submission_date"] < when and d["date_audit"]["status"] == "PASS")]
        lengths = {}
        for kind in ("TSP", "HSP", "BOTH"):
            spans = [(max(lower, f["start_m"]), min(upper, f["end_m"])) for f, d in chosen
                     if (kind == "BOTH" or d["document_type"] == kind) and f["start_m"] < upper and f["end_m"] > lower]
            lengths[kind] = independent_union(spans)
        result = db.query_geological_coverage(lower, upper, as_of=when)
        check("independent_interval_union_"+str(when), lengths["TSP"] == result["tsp_m"] and lengths["HSP"] == result["hsp_m"] and lengths["BOTH"] == result["union_m"], lengths)
        check("partition_total_equals_range_"+str(when), sum(p["length_m"] for p in result["partitions"]) == upper-lower)
        check("repeat_query_is_deterministic_"+str(when), result == db.query_geological_coverage(lower, upper, as_of=when))
    for row in summary["reports"]:
        folder = run/"reports"/row["date"]
        applications = json.loads((folder/"applications.json").read_text())
        frame = pd.read_csv(folder/"performance_samples.csv", low_memory=False)
        frame["time"] = pd.to_datetime(frame.time)
        current_rows = set()
        for response in applications["construction"]["responses"]:
            keys = {(s["source_file"], int(i)) for s in response["summary"]["source"] for i in s["source_rows"]}
            duplicate = current_rows & keys
            current_rows |= keys
            block = frame.loc[[(str(r.source_file), int(r.source_row)) in keys for r in frame.itertuples()]]
            check("no_duplicate_interval_samples_"+response["response_id"], not duplicate)
            check("source_row_sample_count_"+response["response_id"], len(block) == response["summary"]["sample_count"])
            for field in ("advance_speed", "thrust", "torque", "rpm_canonical", "pr", "fpi", "se"):
                expected = float(block[field].median()) if len(block) else None
                actual = response["summary"]["statistics"][field]["median"]
                check(field+"_source_median_"+response["response_id"], expected is None and actual is None or expected is not None and np.isclose(expected, actual, rtol=1e-10))
            ref = response["comparison"].get("reference")
            if ref:
                refkeys = {(s["source_file"], int(i)) for s in ref["source"] for i in s["source_rows"]}
                old = frame.loc[[(str(r.source_file), int(r.source_row)) in refkeys for r in frame.itertuples()]]
                check("reference_precedes_current_"+response["response_id"], len(old) > 0 and len(block) > 0 and old.time.max() < block.time.min())
                for field, change in response["comparison"].get("changes", {}).items():
                    expected = (change["current"]-change["reference"])/abs(change["reference"])*100
                    check("actual_percentage_"+field+response["response_id"], np.isclose(expected, change["percent"]))
            for window in response.get("continuous_windows", []):
                check("continuous_window_rows_belong_to_response_"+window["window_id"],
                      {(s["source_file"], int(i)) for s in window["source"] for i in s["source_rows"]} <= keys)
        narrative = json.loads((folder/"narrative.json").read_text())
        with fitz.open(row["pdf"]) as pdf:
            text = re.sub(r"\s+", "", "".join(p.get_text() for p in pdf))
            exact = [re.sub(r"\s+", "", c["text"]) for ch in narrative["chapters"] for c in ch["claims"]]
            check("verified_narrative_is_final_PDF_"+row["date"], all(s in text for s in exact), len(exact))
            prohibited = [s for s in ("名义里程交集", "无合格预报", "对照到0项等级差异记录", "有数据支持的推进状态", "达到既定比较变化判据0段") if s in text]
            check("reader_prose_not_audit_log_"+row["date"], not prohibited, prohibited)
        check("no_distant_history_sketch_fallback_"+row["date"], all(
            r["document"]["metadata"].get("available_date") == row["date"] or r["document"]["metadata"].get("observation_date") == row["date"]
            for r in applications["new_sketches"]["records"]))
    check("frozen_code_hashes_unchanged", all(sha(ROOT/name) == value for name, value in json.loads((run/"protected_sources.before.json").read_text()).items()))
    check("all_sketch_copies_unchanged", all(sha(Path(r["path"])) == r["sha256"] for r in db.metadata["sketch_audit"]["entries"]))
    result = {"checks": checks, "passed": all(c["passed"] for c in checks), "check_count": len(checks),
              "source_documents": len(sources), "original_forecast_segments": len(original_ranges),
              "original_coordinate_token_review": sum(not r["coordinate_tokens_on_original_page"] for r in original_ranges),
              "not_independent_expert_factual_accuracy": True}
    save(output/"independent_validation.json", result)
    db.close()
    print(json.dumps({k:v for k,v in result.items() if k != "checks"}, ensure_ascii=False, indent=2))
    if not result["passed"]:
        print(json.dumps([c for c in checks if not c["passed"]], ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    audit(p.parse_args().run)
