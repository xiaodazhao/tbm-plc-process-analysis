"""Full-corpus value-lineage validation, separate from segmentation checks."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from experiments.plc_mapping_repair_v1.audit_all import sha
from experiments.plc_mapping_repair_v1.repair import GASES, MOTORS
from tbm_process.ingestion.io import write_json


def run():
    sources = json.loads((ROOT / "source_inventory.json").read_text())
    lineage, fields, issues, signals, relations = [], [], [], [], []
    for item in sources:
        day = item["date"]
        raw = pd.read_csv(item["source"], low_memory=False, encoding="utf-8-sig")
        repaired = pd.read_csv(ROOT / "repaired_raw" / (day + ".csv.gz"), low_memory=False)
        provenance = pd.read_csv(ROOT / "repair_provenance" / (day + ".csv.gz"), low_memory=False)
        values = raw.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        rows = np.arange(len(raw))
        checked, mismatch, moved, masked = 0, 0, 0, 0
        altered = set()
        for key in [key for key in provenance if key.startswith("source_index__")]:
            field = key.removeprefix("source_index__")
            altered.add(field)
            pos = provenance[key].to_numpy(dtype=int)
            expected = np.full(len(raw), np.nan)
            good = pos >= 0
            expected[good] = values[rows[good], pos[good]]
            actual = pd.to_numeric(repaired[field], errors="coerce").to_numpy()
            equal = np.isclose(actual, expected, rtol=1e-12, atol=1e-12, equal_nan=True)
            mismatch += int((~equal).sum())
            checked += len(raw)
            moved += int((good & (pos != (raw.columns.get_loc(field) if field in raw else -2))).sum())
            masked += int((~good).sum())
            if not equal.all():
                issues.append({"date": day, "field": field, "kind": "lineage_mismatch", "rows": (np.flatnonzero(~equal)+2).tolist()})
        altered.update(["导向盾首里程", "导向盾中里程", "刀盘扭矩", "推力", "推进速度", "贯入度", "推进给定速度"])
        unrelated_changes = 0
        for field in raw:
            if field in altered:
                continue
            a, b = raw[field], repaired[field]
            if field == "运行时间-time":
                equal = a.equals(b)
            else:
                equal = bool(np.isclose(pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce"),
                                       rtol=1e-12, atol=1e-12, equal_nan=True).all())
            if not equal:
                unrelated_changes += 1
                issues.append({"date": day, "field": field, "kind": "unrelated_field_changed"})
        for field in ["刀盘扭矩", "推力", "推进速度", "贯入度", "推进给定速度"]:
            good = ~provenance.navigation_mode.eq("shifted_right")
            equal = np.isclose(pd.to_numeric(raw.loc[good, field], errors="coerce"), pd.to_numeric(repaired.loc[good, field], errors="coerce"),
                               rtol=1e-12, atol=1e-12, equal_nan=True)
            if not equal.all():
                issues.append({"date": day, "field": field, "kind": "unrelated_core_changed"})
        lineage.append({"date": day, "rows": len(raw), "source_sha_unchanged": sha(item["source"]) == item["sha256"],
                        "source_row_and_time_unchanged": bool(np.array_equal(provenance.source_row, np.arange(len(raw))+2) and raw["运行时间-time"].equals(repaired["运行时间-time"])),
                        "lineage_cells_checked": checked, "lineage_mismatches": mismatch, "moved_cells": moved,
                        "masked_unresolved_cells": masked, "unrelated_fields_changed": unrelated_changes})
        for index, field in enumerate(raw):
            numeric = pd.to_numeric(raw[field], errors="coerce").replace([np.inf, -np.inf], np.nan)
            usable = numeric.dropna()
            status = "raw_passthrough_not_mapping_certified"
            if field in GASES:
                status = "block_supported_or_masked_units_unconfirmed"
            elif field in MOTORS:
                status = "sequence_block_supported_or_masked_channel_identity_not_independently_certified"
            elif field in ["刀盘实际转速", "刀盘给定转速"]:
                status = "role_supported_or_masked_see_row_provenance"
            elif field in ["导向盾首里程", "导向盾中里程"]:
                status = "existing_navigation_recovery"
            elif index <= 28:
                status = "early_core_header_position_retained_subject_to_signal_quality"
            fields.append({"date": day, "header_sha256": item["header_sha256"], "original_index": index, "field": field,
                           "numeric_samples": len(usable), "missing_or_nonnumeric": int(numeric.isna().sum()),
                           "unique_values": int(raw[field].nunique()), "min_raw": usable.min() if len(usable) else None,
                           "median_raw": usable.median() if len(usable) else None,
                           "max_raw": usable.max() if len(usable) else None, "mapping_status": status})
        for field in ["推力", "刀盘扭矩", "推进速度", "贯入度"]:
            vals = pd.to_numeric(raw[field], errors="coerce")
            for i in np.flatnonzero(vals.lt(0).to_numpy()):
                signals.append({"date": day, "source": item["source"], "source_row": int(i)+2,
                                "time": str(raw["运行时间-time"].iloc[i]), "field": field, "raw_value": vals.iloc[i],
                                "interpretation": "negative_signal_requires_quality_review_not_equipment_diagnosis"})
        speed = pd.to_numeric(repaired["推进速度"], errors="coerce")
        pen = pd.to_numeric(repaired["贯入度"], errors="coerce")
        rpm = pd.to_numeric(repaired["刀盘实际转速"], errors="coerce")
        active = speed.gt(0) & pen.gt(0) & rpm.gt(0)
        ratio = (pen * rpm / speed).where(active).dropna()
        relations.append({"date": day, "secondary_relation_samples": len(ratio),
                          "penetration_times_rpm_over_speed_median": ratio.median() if len(ratio) else None,
                          "fraction_within_15_percent": float((ratio-1).abs().le(.15).mean()) if len(ratio) else None,
                          "independent_proof": False, "warning": "penetration may be derived; only a secondary consistency check"})
        if len(lineage) % 30 == 0:
            print(f"Lineage validated {len(lineage)}/{len(sources)}", flush=True)
    for filename, records in [("lineage_validation.csv", lineage), ("all_fields_mapping_status.csv", fields),
                              ("negative_signal_evidence.csv", signals), ("secondary_physical_relation.csv", relations)]:
        pd.DataFrame(records).to_csv(ROOT / filename, index=False, encoding="utf-8-sig")
    write_json(ROOT / "lineage_validation.json", {"days": len(lineage), "all_raw_sha_unchanged": all(r["source_sha_unchanged"] for r in lineage),
        "lineage_cells_checked": sum(r["lineage_cells_checked"] for r in lineage),
        "lineage_mismatches": sum(r["lineage_mismatches"] for r in lineage), "issues": issues,
        "all_row_time_keys_unchanged": all(r["source_row_and_time_unchanged"] for r in lineage),
        "engineering_accuracy_claimed": False, "units_confirmed": False})


if __name__ == "__main__":
    run()
