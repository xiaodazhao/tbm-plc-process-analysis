from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import re

from tbm_process.reporting_v2.facts import digest


def mileage_geometry(text):
    interval = re.search(r"DyK(\d+)\+(\d+(?:\.\d+)?)\s*[~～—至-]\s*(?:DyK(\d+)\+)?(\d+(?:\.\d+)?)", text, re.I)
    if interval:
        start = float(interval[1])*1000+float(interval[2])
        end = float(interval[3] or interval[1])*1000+float(interval[4])
        return {"geometry": "interval", "start_m": min(start,end), "end_m": max(start,end),
                "range_direction_in_source": 1 if end >= start else -1, "range_source_text": interval.group()}
    point = re.search(r"DyK(\d+)\+(\d+(?:\.\d+)?)(?:附近|处|点)", text, re.I)
    if point:
        value = float(point[1])*1000+float(point[2])
        return {"geometry": "point", "start_m": value, "end_m": value, "range_source_text": point.group()}
    return {"geometry": "unresolved", "start_m": None, "end_m": None}


def load_current_catalog(base: dict, sketch_root: Path):
    """Replace, rather than supplement, legacy portable sketch records."""
    reports = [deepcopy(r) for r in base["reports"] if r["report_metadata"]["source_type"] != "sketch"]
    entries, seen = [], {}
    folders = ("伯舒拉岭右线洞身素描",) if (sketch_root/"伯舒拉岭右线洞身素描").is_dir() else ("伯舒拉岭右线洞身素描1", "伯舒拉岭右线洞身素描2")
    for folder in folders:
        for path in sorted((sketch_root/folder).glob("**/数据.json")):
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            geometry = mileage_geometry(raw.get("里程", ""))
            sha = digest(path)
            key = "CURRENT-SKETCH-"+sha[:20]
            def parsed_day(value):
                try:
                    return date.fromisoformat(value).isoformat() if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None
                except ValueError:
                    return None
            date_key = next((k for k in ("观测日期", "记录日期", "日期") if raw.get(k)), None)
            explicit_date = parsed_day(raw.get(date_key))
            report_date = parsed_day(raw.get("预报时间"))
            url_days = sorted({"%04d-%02d-%02d" % tuple(map(int, match))
                               for match in re.findall(r"/img/(\d{4})/(\d{1,2})/(\d{1,2})/", json.dumps(raw.get("素描图", [])+raw.get("现场照片", [])))})
            # Report dates and image upload dates have different roles. Invalid explicit dates never fall back silently.
            has_report_date = "预报时间" in raw
            available_day = report_date if has_report_date else url_days[0] if len(url_days) == 1 else None
            basis = "user_confirmed_report_date_from_updated_JSON_forecast_date" if has_report_date else "user_confirmed_2026-10-27_image_path_date_is_report_available_date"
            metadata = {"source_report_id": key, "source_type": "sketch", "source_file": str(path.relative_to(sketch_root)),
                        "source_sha256": sha, "structured_source_path": str(path), "source_format": "json",
                        "alignment": "right", "chainage_prefix": "DyK", "coverage_start_m": geometry["start_m"],
                        "coverage_end_m": geometry["end_m"], "observation_date": explicit_date,
                        "observation_date_source": {"source_page": None, "source_quote": explicit_date, "source_pointer": "/"+date_key} if date_key else None,
                        "date_conflicts": [], "actual_distribution_time_verified": False,
                        "available_date": available_day, "available_date_user_confirmed": True,
                        "report_date": report_date,
                        "report_date_source": {"source_pointer": "/预报时间", "source_quote": raw.get("预报时间"), "source_page": None} if has_report_date else None,
                        "availability_basis": basis,
                        "availability_date_candidates": [report_date] if has_report_date and report_date else [] if has_report_date else url_days,
                        "availability_source_pointer": "/预报时间" if has_report_date else None,
                        "image_upload_date_candidates": url_days,
                        "invalid_explicit_report_date": has_report_date and report_date is None,
                        "date_limitation": "day_precision_only; actual_observation_time_and_intraday_receipt_time_not_inferred"}
            report = {"report_metadata": metadata, "current_structured_sketch": raw,
                      "forecast_intervals": [], "risk_records": [],
                      "local_images": [{"path": str(p), "sha256": digest(p), "role": "original_local_sketch_or_photo_not_reconstructed"}
                                       for p in sorted(path.parent.glob("*.jpg"))]}
            if sha not in seen:
                metadata["all_identical_source_paths"] = [str(path)]
                seen[sha] = report
                reports.append(report)
            else:
                seen[sha]["report_metadata"]["all_identical_source_paths"].append(str(path))
                seen[sha]["local_images"].extend(report["local_images"])
            entries.append({"record_id": key, "path": str(path), "sha256": sha,
                            "explicit_date": explicit_date, "report_date": report_date, "image_count": len(report["local_images"]),
                            "confirmed_available_date": available_day,
                            "geometry": geometry, "source_keys": list(raw)})
    if not entries:
        raise FileNotFoundError("No current structured sketches under " + str(sketch_root))
    return {**base, "reports": reports, "report_count": len(reports),
            "current_sketch_source_root": str(sketch_root)}, {
                "current_sketch_count": len(entries), "legacy_sketches_removed_from_input": sum(r["report_metadata"]["source_type"] == "sketch" for r in base["reports"]),
                "explicitly_dated_sketches": sum(bool(r["explicit_date"]) for r in entries),
                "explicit_report_date_records": sum(bool(r["report_date"]) for r in entries),
                "observation_date_records": sum(bool(r["explicit_date"]) for r in entries),
                "confirmed_availability_dates": sum(bool(r["confirmed_available_date"]) for r in entries),
                "unique_sketch_records": len(seen), "exact_duplicate_copies": len(entries)-len(seen),
                "entries": entries, "policy": "current_JSON_only; explicit_report_date_precedes_legacy_user_confirmed_URL_date; observation_and_upload_dates_kept_separate; no_intraday_timestamp_invention"}
