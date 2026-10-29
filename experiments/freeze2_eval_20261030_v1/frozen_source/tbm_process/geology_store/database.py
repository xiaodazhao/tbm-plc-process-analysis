from __future__ import annotations

from collections import Counter
from copy import deepcopy
from itertools import combinations
import json
import math
from pathlib import Path
import sqlite3

from tbm_process.evidence_graph.catalog import build_catalog_graph
from tbm_process.evidence_graph.sketches import load_current_catalog
from tbm_process.evidence_graph.store import identity
from tbm_process.reporting_v2.facts import digest
from tbm_process.reporting_v2.geology import attributes, compare_attributes

from . import VERSION
from .spatial import chainage, coverage_partition, union
from .time_policy import time_decision


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE documents(document_id TEXT PRIMARY KEY, document_type TEXT NOT NULL,
 line TEXT, chainage_system TEXT, start_m REAL, end_m REAL, test_date TEXT,
 document_date TEXT, submission_date TEXT, available_at TEXT, available_date TEXT,
 observation_date TEXT, time_precision TEXT, file_hash TEXT, original_path TEXT,
 data_json TEXT NOT NULL, catalog_json TEXT NOT NULL);
CREATE INDEX documents_time ON documents(document_type,submission_date,available_date);
CREATE TABLE document_aliases(document_id TEXT REFERENCES documents, original_path TEXT,
 PRIMARY KEY(document_id,original_path));
CREATE TABLE time_evidence(document_id TEXT REFERENCES documents, field TEXT, date_value TEXT,
 source_page INTEGER, original_text TEXT, source_json TEXT NOT NULL);
CREATE TABLE sources(source_id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents,
 original_path TEXT, file_hash TEXT, source_page INTEGER, source_pointer TEXT,
 original_quote TEXT, quality_json TEXT NOT NULL);
CREATE TABLE facts(fact_id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents,
 source_id TEXT REFERENCES sources, record_id TEXT, fact_type TEXT, information_role TEXT,
 geometry TEXT, line TEXT, chainage_system TEXT, start_m REAL, end_m REAL, point_m REAL,
 raw_chainage TEXT, quality_status TEXT, data_json TEXT NOT NULL);
CREATE INDEX facts_interval_start ON facts(chainage_system,line,start_m,end_m);
CREATE INDEX facts_interval_end ON facts(chainage_system,line,end_m,start_m);
CREATE INDEX facts_document ON facts(document_id,record_id,fact_type,information_role);
CREATE TABLE plc_days(date TEXT PRIMARY KEY,start_m REAL,end_m REAL,direction INTEGER,
 spatial_allowed INTEGER,source_path TEXT,source_hash TEXT,data_json TEXT NOT NULL);
CREATE TABLE plc_responses(response_id TEXT PRIMARY KEY,date TEXT,data_json TEXT NOT NULL);
CREATE TABLE response_facts(response_id TEXT REFERENCES plc_responses,fact_id TEXT REFERENCES facts,
 PRIMARY KEY(response_id,fact_id));
"""


def import_catalog(path, *, base_catalog, registry, source_root, sketch_root, plc_facts, plc_matches, graph_snapshot=None):
    """Reuse source extraction unchanged; populate a query-oriented relational projection."""
    path = Path(path)
    if path.exists():
        raise FileExistsError("Database is immutable after import; choose a new path")
    path.parent.mkdir(parents=True, exist_ok=True)
    base = json.loads(Path(base_catalog).read_text())
    catalog, sketch_audit = load_current_catalog(base, Path(sketch_root))
    graph = build_catalog_graph(catalog, json.loads(Path(registry).read_text()), Path(source_root))
    reports = {r["report_metadata"]["source_report_id"]: r for r in catalog["reports"]}
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    docs = {n["id"]: n["data"] for n in graph.typed("GeologyDocument")}
    for did, d in docs.items():
        m = d["metadata"]
        report = reports[m["source_report_id"]]
        d.update({"document_number": m.get("document_number") or m.get("report_number"),
                  "file_name": d["source_file"], "file_hash": d["source_version"], "original_path": d["original_file_path"],
                  "report_start": d["start_m"], "report_end": d["end_m"], "test_date": m.get("test_date"),
                  "submission_date": m.get("submission_date"), "observation_date": m.get("observation_date"),
                  "time_precision": "timestamp" if m.get("available_at") else "day",
                  "date_source": d["date_audit"]["date_evidence"]})
        con.execute("INSERT INTO documents VALUES ("+",".join("?" for _ in range(17))+")", (
            did, d["document_type"], d["line"], d["chainage_system"], d["start_m"], d["end_m"],
            m.get("test_date"), m.get("cover_date"), m.get("submission_date"), m.get("available_at"), m.get("available_date"),
            m.get("observation_date"), "timestamp" if m.get("available_at") else "day", d["source_version"],
            d["original_file_path"], encode(d), encode(report)))
        for alias in m.get("all_identical_source_paths", [d["original_file_path"]]):
            con.execute("INSERT OR IGNORE INTO document_aliases VALUES (?,?)", (did, alias))
        for entry in d["date_audit"].get("date_evidence", []):
            field = entry.get("field", "confirmed_available_date")
            source = entry["source"]
            con.execute("INSERT INTO time_evidence VALUES (?,?,?,?,?,?)", (
                did, field, entry.get("value") or entry.get("confirmed_available_date"), source.get("page"),
                source.get("quote"), encode(entry)))

    facts = [deepcopy(n["data"]) for n in graph.typed("GeologyFact")]
    # Keep recommendations as their own role; never reinterpret them as observations.
    for did, d in docs.items():
        report = reports[d["metadata"]["source_report_id"]]
        raw = report.get("current_structured_sketch", {})
        if raw.get("后续建议"):
            value = raw["后续建议"]
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            facts.append({"fact_id": identity("GF", did, "recommendation_original_field"), "document_id": did,
                          "record_id": d["metadata"]["source_report_id"]+":recommendation", "fact_type": "recommendation",
                          "information_role": "recommendation", "geometry": "unresolved", "start_m": None, "end_m": None,
                          "point_m": None, "line": d["line"], "chainage_system": d["chainage_system"], "value": value,
                          "attributes": attributes(text), "original_description": text, "normalized_description": text,
                          "source": {**d["source"], "source_pointer": "/后续建议", "quote": text},
                          "extraction_quality": {"status": "PASS", "method": "verbatim_JSON_field", "semantic_status": "REVIEW"}})
    role_map = {"observed": "observation", "inferred": "inference", "design_assessment": "design"}
    for f in facts:
        d = docs[f["document_id"]]
        s = f["source"]
        sid = identity("SOURCE", f["fact_id"])
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", (
            sid, f["document_id"], s.get("path"), s.get("sha256"), s.get("page"), s.get("source_pointer"),
            s.get("quote"), encode(f.get("extraction_quality", {}))))
        f["information_role"] = role_map.get(f["information_role"], f["information_role"])
        f["upstream_fact_id"] = f["fact_id"]
        f["source_id"] = sid
        f["document_type"] = d["document_type"]
        f["raw_chainage"] = (d["metadata"].get("coverage_source_quote") or
                             reports[d["metadata"]["source_report_id"]].get("current_structured_sketch", {}).get("里程"))
        f["original_text"] = f["original_description"]
        f["normalized_text"] = f["normalized_description"]
        f["source_page"] = s.get("page")
        raw_report = reports[d["metadata"]["source_report_id"]]
        raw_row = next((r for r in raw_report.get("forecast_intervals", [])
                        if f["record_id"] == d["metadata"]["source_report_id"]+":"+r["interval_id"]), {})
        f["source_section"], f["source_table"] = raw_row.get("source_section"), raw_row.get("source_table")
        if raw_row:
            f["raw_chainage"] = {k: raw_row[k] for k in ("start_chainage", "end_chainage", "source_quote") if k in raw_row}
        f["geology_type"] = f["fact_type"]
        f["surrounding_rock_grade"] = f["value"] if f["fact_type"] == "rock_grade" else None
        for field, attr in [("lithology", "lithology"), ("fragmentation", "integrity"), ("joint_description", "joints"),
                            ("fault_description", "special"), ("groundwater_description", "water")]:
            f[field] = f["attributes"].get(attr, {}).get("values", [])
        f["geological_forecast"] = f["original_text"] if f["information_role"] == "forecast" else None
        f["risk_description"] = f["original_text"] if f["fact_type"] == "risk" else None
        con.execute("INSERT INTO facts VALUES ("+",".join("?" for _ in range(15))+")", (
            f["fact_id"], f["document_id"], sid, f["record_id"], f["fact_type"], f["information_role"], f["geometry"],
            f["line"], f["chainage_system"], f.get("start_m"), f.get("end_m"), f.get("point_m"),
            encode(f["raw_chainage"]) if isinstance(f["raw_chainage"], (dict, list)) else f["raw_chainage"],
            f["extraction_quality"]["status"], encode(f)))
    dates = []
    for p in sorted(Path(plc_matches).glob("*.json")):
        match = json.loads(p.read_text())
        c = match["plc_context"]
        facts_path = Path(plc_facts)/(p.stem+".json")
        allowed = bool(c.get("spatial_matching_allowed")) and facts_path.is_file()
        lo, hi = c.get("guide_shield_start_m"), c.get("guide_shield_end_m")
        direction = (1 if hi > lo else -1 if hi < lo else None) if lo is not None and hi is not None else None
        data = {"plc_context": c, "match_source": str(p), "match_sha256": digest(p), "facts_path": str(facts_path),
                "chainage_system": "DyK", "line": "right", "source_original_field": "导向盾首里程"}
        con.execute("INSERT INTO plc_days VALUES (?,?,?,?,?,?,?,?)", (
            p.stem, lo, hi, direction, int(allowed), str(p), digest(p), encode(data)))
        if allowed and lo is not None and hi is not None:
            dates.append((min(lo, hi), max(lo, hi)))
    graph_source = Path(graph_snapshot) if graph_snapshot else None
    if graph_source and graph_source.is_file():
        previous = json.loads(graph_source.read_text())
        nodes = previous["nodes"].values() if isinstance(previous["nodes"], dict) else previous["nodes"]
        known = {f["fact_id"] for f in facts}
        for n in nodes:
            if n["type"] == "PLCResponse":
                d = n["data"]
                con.execute("INSERT INTO plc_responses VALUES (?,?,?)", (n["id"], d["date"], encode(d)))
                eligible = [f for f in facts if f["fact_id"] in known and f["document_type"] in ("TSP", "HSP")
                            and f.get("start_m") is not None and f["start_m"] < d["end_m"] and f["end_m"] > d["start_m"]
                            and time_decision(docs[f["document_id"]], d["date"])["eligible"]]
                for f in eligible:
                    con.execute("INSERT INTO response_facts VALUES (?,?)", (n["id"], f["fact_id"]))
    meta = {"version": VERSION, "source_catalog": str(base_catalog), "source_catalog_sha256": digest(Path(base_catalog)),
            "sketch_audit": sketch_audit, "source_date_audits": graph.metadata["source_audit"],
            "plc_audit_range_m": [min(r[0] for r in dates), max(r[1] for r in dates)],
            "plc_observed_union_m": union(dates), "source_graph": str(graph_source),
            "spatial_index": "SQLite composite B-tree REAL64; no float32 RTree precision loss",
            "role_counts": dict(Counter(f["information_role"] for f in facts)), "paid_api_calls": 0}
    con.executemany("INSERT INTO metadata VALUES (?,?)", [(k, encode(v)) for k, v in meta.items()])
    con.commit()
    assert not con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    return meta


class GeologyStore:
    def __init__(self, path):
        self.path = Path(path)
        self.con = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.documents = {r["document_id"]: json.loads(r["data_json"]) for r in self.con.execute("SELECT document_id,data_json FROM documents")}

    def close(self):
        self.con.close()

    @property
    def metadata(self):
        return {r["key"]: json.loads(r["value_json"]) for r in self.con.execute("SELECT * FROM metadata")}

    def document(self, document_id):
        return deepcopy(self.documents[document_id])

    def document_facts(self, document_id):
        return [json.loads(r[0]) for r in self.con.execute("SELECT data_json FROM facts WHERE document_id=? ORDER BY fact_id", (document_id,))]

    def query_geology_by_chainage(self, start, end, *, as_of=None, source_types=None, line="right", system="DyK"):
        lo, hi = chainage(start, system), chainage(end, system)
        if lo > hi:
            raise ValueError("Ordered start/end required; use ahead query for reverse advance")
        kinds = tuple(source_types or ("TSP", "HSP", "SKETCH", "DESIGN"))
        if not kinds or set(kinds)-{"TSP", "HSP", "SKETCH", "DESIGN"}:
            raise ValueError("Unknown geological source type")
        sql = "SELECT f.data_json FROM facts f JOIN documents d USING(document_id) WHERE f.line=? AND f.chainage_system=? AND f.start_m<=? AND f.end_m>=? AND d.document_type IN ("+",".join("?" for _ in kinds)+") ORDER BY f.start_m,f.end_m,f.fact_id"
        rows = [json.loads(r[0]) for r in self.con.execute(sql, (line, system, hi, lo, *kinds))]
        result, excluded, doc_ids = [], [], set()
        for f in rows:
            if lo != hi and f["geometry"] != "point" and not (f["start_m"] < hi and f["end_m"] > lo):
                continue
            decision = time_decision(self.documents[f["document_id"]], as_of)
            if decision["eligible"]:
                result.append({**f, "query_overlap_m": [max(lo, f["start_m"]), min(hi, f["end_m"])], "time_decision": decision})
                doc_ids.add(f["document_id"])
            else:
                excluded.append({"fact_id": f["fact_id"], "document_id": f["document_id"], **decision})
        return {"range_m": [lo, hi], "as_of": str(as_of) if as_of else None, "line": line, "chainage_system": system,
                "mode": "all_history" if as_of is None else "historical_cutoff_exclusive", "facts": result,
                "documents": [self.document(i) for i in sorted(doc_ids)], "excluded": excluded,
                "unknown_geometry_not_spatially_broadcast": True}

    def query_geology_by_point(self, value, **kwargs):
        return self.query_geology_by_chainage(value, value, **kwargs)

    def query_geological_coverage(self, start, end, *, as_of=None, line="right", system="DyK"):
        query = self.query_geology_by_chainage(start, end, as_of=as_of, source_types=("TSP", "HSP"), line=line, system=system)
        lo, hi = query["range_m"]
        if hi <= lo:
            raise ValueError("Coverage requires a positive-length interval")
        records = [f for f in query["facts"] if f["fact_type"] == "geological_description" and f["geometry"] == "interval"
                   and f["information_role"] == "forecast" and f["extraction_quality"]["status"] == "PASS"]
        parts = coverage_partition(lo, hi, records)
        sums = {key: sum(p["length_m"] for p in parts if p["category"] == key) for key in ("tsp_only", "hsp_only", "both", "gap")}
        intervals = lambda keys: union([[p["start_m"], p["end_m"]] for p in parts if p["category"] in keys])
        return {"range_m": [lo, hi], "length_m": hi-lo, "as_of": str(as_of) if as_of else None, "mode": query["mode"],
                "tsp_m": sums["tsp_only"]+sums["both"], "hsp_m": sums["hsp_only"]+sums["both"],
                "union_m": hi-lo-sums["gap"], "overlap_m": sums["both"], "tsp_only_m": sums["tsp_only"], "hsp_only_m": sums["hsp_only"],
                "gap_m": sums["gap"], "coverage_ratio": (hi-lo-sums["gap"])/(hi-lo),
                "tsp_intervals": intervals({"tsp_only", "both"}), "hsp_intervals": intervals({"hsp_only", "both"}),
                "union_intervals": intervals({"tsp_only", "hsp_only", "both"}), "overlap_intervals": intervals({"both"}),
                "tsp_only_intervals": intervals({"tsp_only"}), "hsp_only_intervals": intervals({"hsp_only"}),
                "gaps": [{"start_m": a, "end_m": b, "length_m": b-a} for a, b in intervals({"gap"})],
                "partitions": parts, "excluded_documents": sorted({r["document_id"] for r in query["excluded"]}),
                "definition": "union_of_actual_structured_forecast_segments; risk_points_add_zero_length; not_report_header_envelopes"}

    def query_geological_gaps(self, start, end, **kwargs):
        return self.query_geological_coverage(start, end, **kwargs)["gaps"]

    def query_forecasts_ahead(self, report_date, current_chainage, distance, direction, *, system="DyK", line="right"):
        from tbm_process.reporting_v2.geology import forward_bounds
        current = chainage(current_chainage, system)
        if direction not in (-1, 1):
            raise ValueError("Verified advance direction +1 or -1 is required")
        if not math.isfinite(float(distance)):
            raise ValueError("Ahead distance must be finite")
        if report_date is None:
            raise ValueError("Historical ahead query requires a cutoff date")
        lo, hi = forward_bounds(current, direction, float(distance))
        query = self.query_geology_by_chainage(lo, hi, as_of=report_date, source_types=("TSP", "HSP"), line=line, system=system)
        query["facts"] = [f for f in query["facts"] if f["geometry"] != "point" or direction*(f["point_m"]-current) > 0]
        for f in query["facts"]:
            f["distances_from_current_m"] = sorted(direction*(v-current) for v in f["query_overlap_m"])
        return {"date": report_date, "current_m": current, "distance_m": distance, "direction": direction,
                "query": query, "coverage": self.query_geological_coverage(lo, hi, as_of=report_date, line=line, system=system),
                "comparisons": self.forecast_comparisons(query), "source_scope": "TSP_HSP_only"}

    def query_previous_forecasts_for_excavation(self, report_date, start, end):
        lower, upper = sorted((chainage(start), chainage(end)))
        return self.query_geology_by_chainage(lower, upper, as_of=report_date, source_types=("TSP", "HSP"))

    def forecast_comparisons(self, query):
        facts = query["facts"]
        by_record = {}
        for f in facts:
            if f["fact_type"] == "geological_description" and f["geometry"] == "interval":
                by_record[f["record_id"]] = deepcopy(f)
        for f in facts:
            if f["fact_type"] == "rock_grade" and f["record_id"] in by_record:
                by_record[f["record_id"]]["attributes"]["grade"] = f["attributes"]["grade"]
        output = []
        for a, b in combinations(by_record.values(), 2):
            if a["document_id"] == b["document_id"]:
                continue
            lo, hi = max(a["query_overlap_m"][0], b["query_overlap_m"][0]), min(a["query_overlap_m"][1], b["query_overlap_m"][1])
            if lo < hi:
                result = compare_attributes(a, b)
                output.append({**result, "range_m": [lo, hi], "left_document_id": a["document_id"], "right_document_id": b["document_id"],
                               "fact_ids": [a["fact_id"], b["fact_id"]], "not_expert_semantic_truth": True})
        return output

    def export_catalog(self, as_of):
        reports = [json.loads(r["catalog_json"]) for r in self.con.execute("SELECT document_id,catalog_json FROM documents")
                   if time_decision(self.documents[r["document_id"]], as_of)["eligible"]]
        return {"reports": reports, "report_count": len(reports), "source": VERSION}

    def day(self, day):
        row = self.con.execute("SELECT * FROM plc_days WHERE date=?", (day,)).fetchone()
        if row is None or not row["spatial_allowed"]:
            raise ValueError("PLC day absent or spatial matching not allowed")
        return {**dict(row), "data": json.loads(row["data_json"])}

    def daily_sketches(self, day):
        return [self.document(k) for k, d in self.documents.items() if d["document_type"] == "SKETCH" and
                (d["metadata"].get("observation_date") or d["metadata"].get("available_date")) == day and d["date_audit"]["status"] == "PASS"]

    def compare_forecast_with_sketch(self, document_id):
        doc = self.document(document_id)
        if doc["document_type"] != "SKETCH":
            raise ValueError("A SKETCH document is required")
        day = doc["metadata"].get("observation_date") or doc["metadata"].get("available_date")
        observations = [f for f in self.document_facts(document_id) if f["information_role"] == "observation"]
        results, unresolved = [], []
        for obs in observations:
            if obs.get("start_m") is None:
                unresolved.append(obs["fact_id"])
                continue
            query = self.query_previous_forecasts_for_excavation(day, obs["start_m"], obs["end_m"])
            forecast = {}
            for f in query["facts"]:
                if f["fact_type"] == "geological_description":
                    forecast[f["record_id"]] = deepcopy(f)
            for f in query["facts"]:
                if f["fact_type"] == "rock_grade" and f["record_id"] in forecast:
                    forecast[f["record_id"]]["attributes"]["grade"] = f["attributes"]["grade"]
            for f in forecast.values():
                check = compare_attributes(f, obs)
                if check["status"] == "indeterminate":
                    continue
                bounds = [max(obs["start_m"], f["start_m"]), min(obs["end_m"], f["end_m"])]
                results.append({**check, "range_m": bounds, "forecast": f, "observation": obs,
                                "observation_date_confirmed": bool(doc["metadata"].get("observation_date")),
                                "use_status": "source_attribute_comparison_not_forecast_accuracy"})
        return {"document": doc, "observations": observations, "comparisons": results,
                "unlocated_observation_fact_ids": unresolved, "original_images": doc.get("local_images", []),
                "observation_date": doc["metadata"].get("observation_date"), "report_available_date": doc["metadata"].get("available_date"),
                "limitations": ["report_available_date_is_not_actual_observation_date", "reported_range_not_independently_verified_face_extent",
                                "unlocated_clauses_are_not_broadcast_over_the_header_range"]}
