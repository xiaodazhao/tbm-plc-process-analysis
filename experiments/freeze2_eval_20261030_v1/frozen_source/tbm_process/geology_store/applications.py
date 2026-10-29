from __future__ import annotations

import json
import sqlite3

from tbm_process.evidence_graph.store import identity
from tbm_process.reporting_v2.performance import performance_windows


def construction_analysis(store, day, package, samples=None, phase_config=None):
    plc = store.day(day)
    query = store.query_previous_forecasts_for_excavation(day, plc["start_m"], plc["end_m"])
    allowed_records = {r["record_id"] for r in query["facts"]}
    summaries = []
    for joint in package["geology"]["joint_analysis"]:
        if not joint["forecast_record_ids"]:
            continue
        if not set(joint["forecast_record_ids"]) <= allowed_records:
            raise ValueError("Existing PLC comparison used facts outside the unified historical query")
        windows = []
        if samples is not None:
            lo, hi = joint["range_m"]
            mask = samples.chainage.ge(lo) & samples.chainage.lt(hi)
            if hi == package["geology"]["day_range_m"][1]:
                mask |= samples.chainage.eq(hi)
            scope = samples.loc[mask]
            if "chainage_quality" in scope:
                scope = scope.loc[scope.chainage_quality.fillna("").str.startswith("usable")]
            windows = performance_windows(scope, package["config"], phase_config)
        summaries.append({"response_id": identity("PLC", day, *joint["range_m"]), "date": day, "range_m": joint["range_m"],
                          "summary": joint["summary"], "comparison": joint["response_comparison"],
                          "continuous_windows": windows, "torque_cv": [w["torque_cv"] for w in windows],
                          "parameter_trends": [w["parameter_changes"] for w in windows],
                          "forecast_record_ids": joint["forecast_record_ids"],
                          "support_fact_ids": [r["fact_id"] for r in query["facts"] if r["record_id"] in joint["forecast_record_ids"]],
                          "source_trace": {"samples": package["source"]["samples"], "samples_sha256": package["source"]["samples_sha256"],
                                           "rows": joint["summary"]["source"], "formulae_quality": package["performance"]["quality"]},
                          "use_status": "descriptive_position_reference_only", "spatial_offset_verified": False})
    return {"application": "excavation_prior_forecasts_and_PLC_response", "date": day,
            "query": query, "coverage": store.query_geological_coverage(*sorted([plc["start_m"], plc["end_m"]]), as_of=day),
            "forecast_comparisons": store.forecast_comparisons(query), "responses": summaries,
            "limitations": ["guide_shield_head_to_face_offset_unknown", "no_causal_or_fault_diagnosis",
                            "same_day_submission_without_time_excluded", "formulae_and_sample_gates_reused_unchanged"]}


def daily_observations(store, day):
    documents = store.daily_sketches(day)
    return {"application": "new_sketch_records_and_previous_forecasts", "date": day,
            "records": [store.compare_forecast_with_sketch(d["document_id"]) for d in documents],
            "available_date_policy": "user_confirmed_report_availability_on_day_not_independent_observation_time",
            "no_new_sketch_policy": "do_not_force_nearest_distant_historical_sketch_into_report",
            "confirmed_actual_observation_date_count": sum(bool(d["metadata"].get("observation_date")) for d in documents)}


def persist_responses(store, result):
    """Append calculated summaries to the new database; never alter source facts."""
    con = sqlite3.connect(store.path)
    con.execute("PRAGMA foreign_keys=ON")
    for row in result["responses"]:
        data = json.dumps(row, ensure_ascii=False, allow_nan=False)
        # A previous graph response may have the same ID and an older serialization.
        key = identity("STORE_RESPONSE", row["date"], row["range_m"])
        old = con.execute("SELECT data_json FROM plc_responses WHERE response_id=?", (key,)).fetchone()
        if old and old[0] != data:
            raise ValueError("Existing response content differs; use a fresh output database")
        con.execute("INSERT OR IGNORE INTO plc_responses VALUES (?,?,?)", (key, row["date"], data))
        for fid in row["support_fact_ids"]:
            con.execute("INSERT OR IGNORE INTO response_facts VALUES (?,?)", (key, fid))
    con.commit()
    con.close()
