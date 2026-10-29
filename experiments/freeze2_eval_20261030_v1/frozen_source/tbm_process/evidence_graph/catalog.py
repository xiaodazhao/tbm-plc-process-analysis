from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import re
from pathlib import Path

import fitz

from tbm_process.reporting_v2.facts import digest
from tbm_process.reporting_v2.geology import _source, attributes, availability, prefix_compatible

from .store import EvidenceGraph, identity
from .sketches import mileage_geometry


def compact(text):
    return re.sub(r"\s+", "", str(text or "")).replace("．", ".")


class SourceAudit:
    def __init__(self):
        self.hashes = {}
        self.pages = {}

    def page(self, source):
        path, page = source.get("path"), source.get("page")
        if not path or not Path(path).is_file():
            return {"status": "REVIEW", "reason": "original_pdf_missing"}, ""
        if path not in self.hashes:
            self.hashes[path] = digest(Path(path))
        if self.hashes[path] != source.get("sha256"):
            return {"status": "FAIL", "reason": "original_sha_mismatch"}, ""
        if Path(path).suffix == ".json":
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            pointer = source.get("source_pointer", "")
            value = raw
            try:
                for key in pointer.strip("/").split("/"):
                    if key:
                        value = value[int(key)] if isinstance(value, list) else value[key.replace("~1", "/").replace("~0", "~")]
            except (KeyError, ValueError, IndexError, TypeError):
                return {"status": "FAIL", "reason": "JSON_pointer_missing"}, ""
            text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            quote = compact(source.get("quote", ""))
            return {"status": "PASS", "file_sha_verified": True, "page_verified": False,
                    "source_pointer_verified": True, "quote_exact_after_whitespace": bool(quote and quote in compact(text)),
                    "semantic_status": "REVIEW_not_expert_confirmation"}, text
        key = (path, page)
        if key not in self.pages:
            with fitz.open(path) as doc:
                if not isinstance(page, int) or not 1 <= page <= len(doc):
                    return {"status": "FAIL", "reason": "page_out_of_bounds"}, ""
                self.pages[key] = doc[page-1].get_text()
        text = self.pages[key]
        quote = compact(source.get("quote", ""))
        found = bool(quote and quote in compact(text))
        return {"status": "PASS", "file_sha_verified": True, "page_verified": True,
                "quote_exact_after_whitespace": found,
                "semantic_status": "REVIEW_not_expert_confirmation"}, text

    def metadata(self, metadata, source):
        if metadata.get("available_date_user_confirmed"):
            check, text = self.page(source)
            candidates = metadata.get("availability_date_candidates", [])
            pointer = metadata.get("availability_source_pointer")
            explicit_supported = True
            if pointer:
                date_check, value = self.page({**source, "source_pointer": pointer, "quote": metadata.get("available_date")})
                explicit_supported = date_check.get("quote_exact_after_whitespace", False) and value == metadata.get("available_date")
            return {"status": "PASS" if check["status"] == "PASS" and len(candidates) == 1 and candidates[0] == metadata.get("available_date") and explicit_supported else "REVIEW",
                    "basis_field": "available_date", "date_evidence": [{"source": source, "confirmed_available_date": metadata.get("available_date"),
                                                                           "availability_date_candidates": candidates,
                                                                           "image_upload_date_candidates": metadata.get("image_upload_date_candidates", candidates),
                                                                           "source_pointer": pointer, "confirmation_basis": metadata["availability_basis"]}],
                    "filename_dates": [], "filename_date_mismatch": False,
                    "resolution": "explicit_user_confirmed_availability_date_not_inferred_document_or_event_date",
                    "receipt_time_verified": False, "time_precision": "day",
                    "limitation": "actual_observation_date_and_intraday_availability_not_confirmed"}
        items = []
        for field in ("cover_date", "test_date", "submission_date", "observation_date"):
            entry = metadata.get(field+"_source")
            if not entry:
                continue
            ref = {**source, "page": entry.get("source_page"), "quote": entry.get("source_quote", ""),
                   "source_pointer": entry.get("source_pointer")}
            check, _ = self.page(ref)
            items.append({"field": field, "value": metadata.get(field), "source": ref,
                          "check": check, "year_resolution_note": entry.get("year_resolution_note")})
        primary = "observation_date" if metadata["source_type"] == "sketch" else "submission_date"
        if not metadata.get(primary) and primary != "observation_date":
            primary = "cover_date"
        primary_item = next((r for r in items if r["field"] == primary), None)
        supported = bool(primary_item and primary_item["check"].get("quote_exact_after_whitespace"))
        filename_days = re.findall(r"(?<!\d)(20\d{6})(?!\d)", metadata["source_file"])
        filename_days = [s[:4]+"-"+s[4:6]+"-"+s[6:] for s in filename_days]
        basis = metadata.get(primary)
        mismatch = bool(filename_days and basis not in filename_days)
        # A filename is audited, never promoted to the report's availability time.
        independent = sum(bool(r["check"].get("quote_exact_after_whitespace")) for r in items)
        blocked = bool(metadata.get("date_conflicts")) or not supported or (mismatch and independent < 2)
        return {"status": "REVIEW" if blocked else "PASS", "basis_field": primary,
                "date_evidence": items, "filename_dates": filename_days,
                "filename_date_mismatch": mismatch,
                "resolution": "unresolved_do_not_use" if blocked else
                    "independent_PDF_body_date_evidence_retained_filename_discrepancy_logged" if mismatch else "PDF_date_quote_supported",
                "receipt_time_verified": bool(metadata.get("actual_distribution_time_verified")),
                "limitation": "document_date_is_not_independently_verified_receipt_time"}


def document_gate(document: dict, day: str) -> dict:
    metadata = document["metadata"]
    if metadata.get("available_date_user_confirmed"):
        if document["date_audit"]["status"] != "PASS" or not metadata.get("available_date"):
            return {"status": "review", "reason": "availability_date_missing_or_conflicting"}
        if not prefix_compatible(metadata.get("chainage_prefix", "")) or metadata.get("alignment") != "right":
            return {"status": "review", "reason": "unverified_line_or_chainage_system"}
        value = metadata["available_date"]
        if value > day:
            return {"status": "excluded", "reason": "future_document", "available_date": value, "document_date": None}
        if value == day:
            return {"status": "review", "reason": "same_day_availability_time_unknown", "available_date": value,
                    "basis": metadata["availability_basis"], "time_precision": "day"}
        return {"status": "eligible", "role": "prior_document", "available_date": value,
                "document_date": metadata.get("observation_date"), "available_at": None,
                "time_precision": "day", "basis_field": "user_confirmed_available_date", "basis": metadata["availability_basis"]}
    try:
        gate = availability(metadata, day, metadata["source_type"])
    except (ValueError, TypeError):
        return {"status": "review", "reason": "unparseable_time"}
    if not prefix_compatible(metadata.get("chainage_prefix", "")) or metadata.get("alignment") != "right":
        return {"status": "review", "reason": "unverified_line_or_chainage_system"}
    if gate["status"] == "eligible" and document["date_audit"]["status"] != "PASS":
        return {"status": "review", "reason": "original_document_date_not_supported", "upstream_gate": gate}
    return gate


def _segment(graph, geometry, lo, hi, role, source_id):
    data = {"geometry": geometry, "start_m": lo, "end_m": hi, "segment_length_m": hi-lo if lo is not None and hi is not None else None,
            "line": "right", "chainage_system": "DyK", "segment_role": role,
            "spatial_reference": "original_document_nominal_chainage", "spatial_quality": "not_validated_against_guide_shield_position",
            "source": source_id}
    sid = identity("SEG", geometry, lo, hi, role, source_id)
    graph.add_node("ConstructionSegment", sid, data)
    return sid


def _clauses(text):
    mode = "observed"
    for match in re.finditer(r"[^。；\n]+[。；]?", text):
        raw = match.group().strip()
        if not raw:
            continue
        if re.search(r"当前开挖|现场(?:实际)?|实际揭露|洞身段出露", raw):
            mode = "observed"
        if re.search(r"推测|预测|预计", raw):
            mode = "inferred"
        if re.search(r"^建议|^应(?:加强|及时|采取)", raw):
            mode = "recommendation"
        if "建议" in raw:
            before, _ = raw.split("建议", 1)
            if before.strip() and mode != "recommendation":
                yield before, mode, [match.start(), match.start()+len(before)]
            mode = "recommendation"
            continue
        if mode != "recommendation":
            yield raw, mode, [match.start(), match.end()]


def build_catalog_graph(catalog: dict, registry: dict, source_root: Path, design_records=()):
    graph, auditor = EvidenceGraph(), SourceAudit()
    registry_index = {r["sha256"]: r for r in registry.get("source_registry", [])}
    audits, excluded = [], []
    design_reports = []
    for record in design_records:
        if not record.get("verified"):
            continue
        path = Path(record.get("source_file", ""))
        if not path.is_absolute():
            path = source_root/path
        metadata = {"source_report_id": record["record_id"], "source_type":"design", "source_file":path.name,
                    "source_sha256":record.get("source_sha256"), "original_source_path":str(path),
                    "alignment":"right", "chainage_prefix":record.get("chainage_prefix"),
                    "coverage_start_m":record.get("start_m"), "coverage_end_m":record.get("end_m"),
                    "submission_date":record.get("available_date"), "submission_date_source":record.get("available_date_source"),
                    "cover_date":record.get("available_date"), "date_conflicts":record.get("date_conflicts",[])}
        row = {"interval_id":"DESIGN-I01", "start_m":record.get("start_m"), "end_m":record.get("end_m"),
               "forecast_conclusion":record.get("source_quote",""), "source_quote":record.get("source_quote",""),
               "source_page":record.get("source_page"), "recommended_grade_table":record.get("reported_grade")}
        design_reports.append({"report_metadata":metadata,"forecast_intervals":[row]})
    for report in catalog["reports"]+design_reports:
        metadata = deepcopy(report["report_metadata"])
        def source_for(row):
            if metadata.get("original_source_path"):
                return {"report_id":metadata["source_report_id"],"source_type":metadata["source_type"],
                        "path":metadata["original_source_path"],"sha256":metadata["source_sha256"],"page":row.get("source_page",1),
                        "quote":row.get("source_quote",""),"original_filename":metadata["source_file"],"prefix":metadata["chainage_prefix"]}
            if metadata.get("structured_source_path"):
                return {"report_id": metadata["source_report_id"], "source_type": "sketch", "path": metadata["structured_source_path"],
                        "sha256": metadata["source_sha256"], "page": None, "source_pointer": row.get("source_pointer", ""),
                        "quote": row.get("source_quote", ""), "original_filename": metadata["source_file"], "prefix": metadata["chainage_prefix"]}
            return _source(metadata, row, registry_index, source_root)
        source = source_for({})
        did = identity("DOC", metadata["source_report_id"], metadata.get("source_sha256"))
        date_check = auditor.metadata(metadata, source)
        graph.add_node("GeologyDocument", did, {
            "document_id": did, "document_type": metadata["source_type"].upper(),
            "document_date": metadata.get("observation_date") or metadata.get("cover_date"),
            "available_at": metadata.get("available_at"),
            "available_time_precision": "timestamp" if metadata.get("available_at") else "date",
            "event_date": metadata.get("observation_date") or metadata.get("test_date"),
            "line": metadata.get("alignment"), "chainage_system": metadata.get("chainage_prefix"),
            "start_m": metadata.get("coverage_start_m"), "end_m": metadata.get("coverage_end_m"),
            "source_file": metadata["source_file"], "source_version": metadata.get("source_sha256"),
            "original_file_path": source.get("path"), "metadata": metadata, "source": source,
            "date_audit": date_check, "local_images": report.get("local_images", [])})
        audits.append({"document_id": did, "report_id": metadata["source_report_id"], **date_check})

        def add_fact(key, fact_type, value, role, row, geometry, lo, hi, text, span=None, extra=None):
            fid = identity("GF", did, key)
            ref = source_for(row)
            source_check, page_text = auditor.page(ref)
            if fact_type == "risk":
                terms = [word for word in ("掉块","溜坍","出水","突涌水","断层") if word in str(value)]
                if terms and any(word not in compact(ref.get("quote","")) for word in terms):
                    source_check = {**source_check,"status":"REVIEW","reason":"risk_type_not_explicit_in_saved_source_quote; do_not_publish_automatically"}
            attrs = attributes(text, value if fact_type == "rock_grade" else None)
            data = {"fact_id": fid, "document_id": did, "record_id": metadata["source_report_id"]+":"+str(key).split("|")[0],
                    "fact_type": fact_type, "value": value, "information_role": role,
                    "geometry": geometry, "start_m": lo, "end_m": hi,
                    "point_m": lo if geometry == "point" else None, "line": metadata.get("alignment"),
                    "chainage_system": metadata.get("chainage_prefix"),
                    "attributes": attrs, "original_description": text, "normalized_description": compact(text),
                    "source": ref, "source_page": ref["page"],
                    "source_text_span": {"basis": "structured_source_field", "field": fact_type, "offsets": span,
                                         "quote": text, "not_pdf_character_offsets": True},
                    "extraction_method": "existing_structured_fields_and_conservative_clause_rules",
                    "extraction_quality": {**source_check, "semantic_status": "REVIEW"},
                    "value_literal_on_page": bool(compact(value) and compact(value) in compact(page_text)),
                    **(extra or {})}
            graph.add_node("GeologyFact", fid, data)
            graph.add_edge("HAS_FACT", did, fid, {"method": "existing_report_id_and_source_field", "page": ref["page"]})
            if lo is None or hi is None or hi < lo:
                excluded.append({"fact_id": fid, "reason": "unparseable_or_reversed_geometry"})
            else:
                sid = _segment(graph, geometry, lo, hi, role, data["record_id"])
                graph.add_edge("OBSERVED_AT" if role == "observed" else "COVERS", fid, sid,
                               {"method": "explicit_source_geometry", "geometry": geometry})
            return fid

        for row in report.get("forecast_intervals", []):
            interval_role = "design_assessment" if metadata["source_type"] == "design" else "forecast"
            key, text = row["interval_id"], row.get("forecast_conclusion", "")
            lo, hi = row.get("start_m"), row.get("end_m")
            add_fact(key+"|description", "geological_description", text, interval_role, row, "interval", lo, hi, text)
            grade = row.get("recommended_grade_for_use") or row.get("recommended_grade_table")
            if grade:
                add_fact(key+"|grade", "rock_grade", grade, interval_role, row, "interval", lo, hi, text,
                         extra={"grade_resolution_rule": row.get("grade_resolution_rule"), "grade_conflict": row.get("grade_conflict")})
            for field, values in attributes(text).items():
                if field != "grade" and values["values"]:
                    add_fact(key+"|"+field, field, values["values"], interval_role, row, "interval", lo, hi, text)
            physical = row.get("geophysical_analysis") or row.get("geophysical_result")
            if physical:
                add_fact(key+"|geophysical", "geophysical_description", physical, "forecast", row, "interval", lo, hi, physical)
        for row in report.get("risk_records", []):
            if row.get("geometry") == "point":
                lo = hi = row.get("chainage_m", row.get("point_m"))
            elif row.get("geometry") == "interval":
                lo, hi = row.get("start_m"), row.get("end_m")
            else:
                excluded.append({"record": row, "reason": "risk_geometry_unknown"})
                continue
            add_fact(row["risk_id"]+"|risk", "risk", row.get("risk_type"), "forecast", row,
                     row["geometry"], lo, hi, row.get("source_quote", ""), extra={"risk_geometry_preserved": True})
        row = report.get("observation")
        if report.get("current_structured_sketch"):
            raw = report["current_structured_sketch"]
            header = mileage_geometry(raw.get("里程", ""))
            for field, role, kind in [("设计围岩分级", "design_assessment", "rock_grade"),
                                      ("施工围岩分级", "observed", "rock_grade"),
                                      ("设计工程地质、水文地质情况", "design_assessment", "geological_description")]:
                if raw.get(field):
                    add_fact(field, kind, raw[field], role, {"source_pointer": "/"+field, "source_quote": raw[field]},
                             header["geometry"], header["start_m"], header["end_m"], raw[field],
                             extra={"spatial_quality": "reported_table_range_not_independently_verified_face_extent",
                                    "independent_design_document": False})
            for i, group in enumerate(raw.get("施工揭示", [])):
                for field, value in group.items():
                    pointer = f"/施工揭示/{i}/"+field
                    for j, (text, role, span) in enumerate(_clauses(str(value))):
                        geometry = mileage_geometry(text)
                        add_fact(f"{pointer}|{j}", "geological_description", text, role,
                                 {"source_pointer": pointer, "source_quote": text}, geometry["geometry"], geometry["start_m"], geometry["end_m"], text, span,
                                 {"spatial_quality": "explicit_clause_geometry" if geometry["start_m"] is not None else "no_clause_geometry_not_broadcast_over_header_range"})
            # Conclusions remain inspectable but are not recast as independent observations.
            for i, group in enumerate(raw.get("结论", [])):
                if group.get("探测结论"):
                    text = group["探测结论"]
                    add_fact(f"conclusion{i}", "mixed_original_conclusion", text, "inferred",
                             {"source_pointer": f"/结论/{i}/探测结论", "source_quote": text},
                             header["geometry"], header["start_m"], header["end_m"], text,
                             extra={"mixed_semantics_preserved_not_eligible_as_unqualified_observation": True})
        if metadata["source_type"] == "sketch" and row:
            key, point = row["observation_id"], row.get("chainage_m")
            for i, (text, role, span) in enumerate(_clauses(row.get("geological_description", ""))):
                add_fact(key+f"|clause{i}", "geological_description", text, role, row, "point", point, point, text, span)
            if row.get("table_suggested_grade"):
                add_fact(key+"|grade", "rock_grade", row["table_suggested_grade"], "observed", row, "point", point, point,
                         "原表建议围岩等级："+row["table_suggested_grade"], extra={"grade_is_reported_assessment_not_perfect_ground_truth": True,
                         "grade_conflict": row.get("grade_conflict")})
            for field, value in row.get("selected_fields", {}).items():
                if value is not None and value != [] and value != "":
                    add_fact(key+"|table:"+field, field, value, "observed", row, "point", point, point,
                             str(value), extra={"table_field": field})
    graph.metadata.update({"catalog_report_count": len(catalog["reports"]), "source_audit": audits,
                           "excluded_geometry": excluded, "design_input_count": len(design_records),
                           "design_note": "Verified design records are integrated per-day through the existing V2 interface; no unverified drawings extracted."})
    return graph


def eligible_catalog(catalog: dict, graph: EvidenceGraph, day: str):
    docs = {n["data"]["metadata"]["source_report_id"]: n["data"] for n in graph.typed("GeologyDocument")}
    accepted, decisions = [], []
    for report in catalog["reports"]:
        key = report["report_metadata"]["source_report_id"]
        gate = document_gate(docs[key], day)
        decisions.append({"report_id": key, "document_id": docs[key]["document_id"], **gate})
        if gate["status"] == "eligible":
            accepted.append(deepcopy(report))
    return {**catalog, "reports": accepted, "report_count": len(accepted)}, decisions
