"""Inventory every original export and its row-local layout without editing it."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from tbm_process.ingestion.chainage import normalize_guidance_chainage
from tbm_process.ingestion.io import write_json

RAW_ROOTS = [Path.home() / "Desktop" / p for p in ["TBM9_2023", "tbm9-2024-part1", "tbm9-2024-part2"]]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def frame_layout(raw):
    profile = json.loads((REPO / "configs/field_profiles/tbm9_2023.json").read_text())
    return normalize_guidance_chainage(raw, direct_chainage=raw.get("导向盾首里程", pd.Series(np.nan, index=raw.index)),
        direct_source_field="导向盾首里程", config=profile["guidance_chainage_mapping"])


def boolean_mismatches(raw, shift):
    # Independent discrete-type anchors, not gas values or speed/penetration.
    fields = [f"M{i}减速机温度开关" for i in range(1, 15)] + [f"M{i}电机开关" for i in range(1, 15)]
    fields += ["内循环水罐压力低", "变频器液位低", "泡沫手动", "泡沫半自动"]
    result = pd.Series(0, index=raw.index)
    for field in fields:
        if field not in raw:
            continue
        pos = raw.columns.get_loc(field) + shift
        if not 0 <= pos < len(raw.columns):
            result += 1
            continue
        values = pd.to_numeric(raw.iloc[:, pos], errors="coerce")
        result += (~values.isin([0, 1])).astype(int)
    return result


def run():
    (ROOT / "row_layouts").mkdir(parents=True, exist_ok=True)
    files, anomalies, transitions, signatures = [], [], [], []
    for path in sorted(p for root in RAW_ROOTS for p in root.rglob("*.csv")):
        counts = {}
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            for i, row in enumerate(reader, 2):
                counts[len(row)] = counts.get(len(row), 0) + 1
                if len(row) != len(header):
                    anomalies.append({"source": str(path), "source_row": i, "row_fields": len(row), "header_fields": len(header)})
        record = {"source": str(path), "sha256": sha(path), "bytes": path.stat().st_size,
                  "header_fields": len(header), "row_field_counts": counts,
                  "duplicate_headers": len(header) - len(set(header)),
                  "header_sha256": hashlib.sha256(json.dumps(header, ensure_ascii=False).encode()).hexdigest()}
        if any(n != len(header) for n in counts) or len(header) != len(set(header)):
            record["status"] = "quarantined_structural_error"
            files.append(record)
            continue
        raw = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        times = pd.to_datetime(raw["运行时间-time"], errors="coerce", format="mixed")
        layout = frame_layout(raw)
        mode = layout.chainage_mapping_mode
        dates = times.dt.strftime("%Y-%m-%d").dropna().unique().tolist()
        day = dates[0] if len(dates) == 1 else "mixed_dates_" + path.stem
        scores = {s: boolean_mismatches(raw, s) for s in [-1, 0, 1]}
        out = pd.DataFrame({"source_row": np.arange(len(raw)) + 2, "time": times, "navigation_mode": mode,
                            "canonical_chainage": layout.chainage, "chainage_quality": layout.chainage_quality})
        for shift, score in scores.items():
            out[f"boolean_violations_shift_{shift}"] = score
        switches = mode.ne(mode.shift()) & mode.shift().notna()
        for i in np.flatnonzero(switches.to_numpy()):
            gap = (times.iloc[i] - times.iloc[i-1]).total_seconds()
            transitions.append({"date": day, "source": str(path), "previous_source_row": int(i)+1,
                                "source_row": int(i)+2, "previous_mode": mode.iloc[i-1], "mode": mode.iloc[i], "gap_seconds": gap})
        for label, group in out.groupby("navigation_mode"):
            signatures.append({"date": day, "mode": label, "rows": len(group),
                               **{f"boolean_shift_{s}_median": float(group[f"boolean_violations_shift_{s}"].median()) for s in scores},
                               **{f"boolean_shift_{s}_perfect": int(group[f"boolean_violations_shift_{s}"].eq(0).sum()) for s in scores}})
        row_path = ROOT / "row_layouts" / f"{day}.{record['sha256'][:8]}.csv.gz"
        out.to_csv(row_path, index=False, encoding="utf-8-sig", compression="gzip")
        record.update({"date": day, "dates_in_file": dates, "rows": len(raw), "invalid_times": int(times.isna().sum()),
                       "duplicate_timestamps": int(times.duplicated().sum()), "backwards_times": int(times.diff().dt.total_seconds().lt(0).sum()),
                       "mode_counts": {str(k): int(v) for k,v in mode.value_counts().items()},
                       "mode_switches": int(switches.sum()), "row_layout_file": str(row_path),
                       "columns": header, "status": "audited"})
        files.append(record)
        if len(files) % 15 == 0:
            print(f"Audited {len(files)} raw files, latest {day}: {record['mode_counts']}", flush=True)
        write_json(ROOT / "source_inventory.json", files)
    pd.DataFrame(anomalies, columns=["source", "source_row", "row_fields", "header_fields"]).to_csv(ROOT / "structural_quarantine.csv", index=False)
    pd.DataFrame(transitions).to_csv(ROOT / "layout_transitions.csv", index=False)
    pd.DataFrame(signatures).to_csv(ROOT / "independent_boolean_signatures.csv", index=False)
    write_json(ROOT / "audit_summary.json", {"source_files": len(files), "bytes": sum(f["bytes"] for f in files),
               "rows": sum(f.get("rows", 0) for f in files), "structural_bad_rows": len(anomalies),
               "header_shapes": sorted(set(f["header_fields"] for f in files)),
               "unique_header_hashes": len(set(f["header_sha256"] for f in files)),
               "audited_files": sum(f["status"] == "audited" for f in files)})


if __name__ == "__main__":
    run()
