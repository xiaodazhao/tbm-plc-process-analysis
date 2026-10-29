#!/usr/bin/env python3
"""Independent read-only recomputation of saved V2 samples and source lineage."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import fitz
import numpy as np
import pandas as pd


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def verify(directory, raw_root):
    package=json.loads((directory/"report_facts.json").read_text())
    samples=pd.read_csv(directory/"performance_samples.csv")
    good=samples.loc[samples.performance_eligible.astype(str).str.lower().eq("true")].copy()
    e=package["config"]["equipment"]
    area=math.pi*(e["diameter_m"]/2)**2
    expected_pr=good.advance_speed/good.rpm_canonical
    expected_fpi=good.thrust/(e["cutter_count"]*expected_pr)
    # An independent mixed-unit form, unlike the generator's SI formula.
    expected_se=good.thrust/(1000*area)+2*math.pi*good.torque/(area*expected_pr)
    checks=[]
    def add(name,passed,count=1):
        checks.append({"check":name,"passed":bool(passed),"checked_values":int(count)})
    for field,expected in [("pr",expected_pr),("fpi",expected_fpi),("se",expected_se)]:
        add("pointwise_"+field,np.allclose(good[field],expected,rtol=1e-10,atol=1e-8),len(good))
        actual=package["performance"]["daily"]["statistics"][field]
        for key,q in [("p10",.1),("median",.5),("p90",.9)]:
            add(field+"_"+key,math.isclose(actual[key],float(expected.quantile(q)),rel_tol=1e-10,abs_tol=1e-8))
    add("no_negative_thrust_in_performance",good.thrust.gt(0).all(),len(good))
    add("same_sample_count",len(good)==package["performance"]["daily"]["sample_count"])
    add("source_processed_sha",sha(package["source"]["samples"])==package["source"]["samples_sha256"])
    original=raw_root/("tbm_data_"+package["date"].replace("-","")+".csv")
    fields={"advance_speed":"推进速度","thrust":"推力","torque":"刀盘扭矩","rpm_canonical":"刀盘实际转速"}
    raw=pd.read_csv(original,usecols=["mapping_source_row",*fields.values()],low_memory=False).set_index("mapping_source_row")
    original_values=raw.reindex(good.source_row)
    for field,raw_name in fields.items():
        add("raw_source_rows_"+field,np.allclose(good[field].to_numpy(),original_values[raw_name].to_numpy(),rtol=1e-9,atol=1e-8,equal_nan=False),len(good))
    source_pages=[]
    for observation in package["geology"]["selected_observations"]:
        source=observation["source"]
        if not source.get("path"):
            source_pages.append({"status":"REVIEW","reason":"original_missing"})
            continue
        add("sketch_source_sha:"+source["report_id"],sha(source["path"])==source["sha256"])
        with fitz.open(source["path"]) as doc:
            add("sketch_page_bounds:"+source["report_id"],1<=source["page"]<=len(doc))
            source_pages.append({"report_id":source["report_id"],"page":source["page"],"source_text_characters":len(doc[source["page"]-1].get_text()),
                                 "status":"page_located_semantic_support_requires_review"})
    pdf=next(directory.glob("*.engineering_report.pdf"))
    with fitz.open(pdf) as doc:
        add("actual_pdf_readable",len(doc)>0)
    return {"date":package["date"],"checks":checks,"passed":all(c["passed"] for c in checks),
            "checked_scalar_values":sum(c["checked_values"] for c in checks),"source_pages":source_pages,
            "raw_standardized_file":str(original),"raw_standardized_sha256":sha(original),
            "interpretation":"recomputation_and_lineage_only_not_sensor_accuracy_or_expert_truth"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir",type=Path,required=True)
    p.add_argument("--raw-root",type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(verify(args.run_dir,args.raw_root),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
