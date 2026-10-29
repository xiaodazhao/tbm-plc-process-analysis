from __future__ import annotations

from copy import deepcopy

from tbm_process.reporting_v2.geology import compare_attributes
from tbm_process.reporting_v2.performance import performance_windows, row_evidence

from .store import EvidenceGraph, identity, intersects
from .catalog import document_gate


def fact_index(graph):
    result = {}
    for node in graph.typed("GeologyFact"):
        result.setdefault(node["data"]["record_id"], {}).setdefault(node["data"]["fact_type"], []).append(node["id"])
    return result


def add_daily_analysis(graph: EvidenceGraph, package, frame, phase_config, *, response_policy=None, literal_relations=False):
    """Reuse V2 statistics; add only spatially relevant summaries and relations."""
    day, geology = package["date"], package["geology"]
    facts = fact_index(graph)
    day_id = identity("SEG", day, "actual")
    day_range = geology["day_range_m"]
    graph.add_node("ConstructionSegment", day_id, {
        "report_date": day, "segment_role": "actual_daily_construction_reference",
        "start_m": day_range[0], "end_m": day_range[1], "geometry": "interval" if day_range[0] != day_range[1] else "point",
        "segment_length_m": day_range[1]-day_range[0], "line": "right", "chainage_system": "DyK",
        "spatial_reference": "guide_shield_head", "spatial_quality": "offset_to_face_unverified",
        "source": package["source"]})
    forward_id = None
    if geology["ahead_range_m"]:
        lo, hi = geology["ahead_range_m"]
        forward_id = identity("SEG", day, "ahead")
        graph.add_node("ConstructionSegment", forward_id, {
            "report_date": day, "segment_role": "not_yet_excavated_forward_reference_window",
            "start_m": lo, "end_m": hi, "geometry": "interval", "segment_length_m": hi-lo,
            "line": "right", "chainage_system": "DyK", "direction": geology["direction"],
            "shield_end_m": geology["shield_end_m"], "spatial_reference": "guide_shield_head",
            "spatial_quality": "offset_to_face_unverified"})
    targets = [day_id]+([forward_id] if forward_id else [])
    original_segments = list(graph.typed("ConstructionSegment"))
    for segment in original_segments:
        if segment["id"] in targets or segment["data"].get("report_date"):
            continue
        for target in targets:
            if intersects(segment["data"], graph.nodes[target]["data"]):
                graph.add_edge("OVERLAPS_WITH", segment["id"], target, {
                    "method": "same_line_chainage_system_geometry_intersection",
                    "nominal_only": True, "not_causal_or_accuracy_evidence": True})

    def ids(record, field="geological_description"):
        return facts.get(record, {}).get(field, [])

    def comparison(kind, supports, bounds, result, measured=None, limitations=None, time_range=None, attribute=None):
        supports = list(dict.fromkeys(supports))
        if not supports:
            return None
        key = identity("CP", day, kind, supports, bounds, attribute)
        geology_ids = [i for i in supports if graph.nodes[i]["type"] == "GeologyFact"]
        response_ids = [i for i in supports if graph.nodes[i]["type"] == "PLCResponse"]
        graph.add_node("EngineeringComparison", key, {
            "report_date": day, "comparison_type": kind, "chainage_range_m": bounds,
            "time_range": time_range, "source_fact_ids": geology_ids, "source_response_ids": response_ids,
            "attribute": attribute, "result": result, "measured_difference": measured,
            "comparison_method": "existing_V2_literal_attributes_or_qualified_median_comparison",
            "limitations": limitations or ["source_descriptions_not_geological_truth", "not_causal"],
            "evidence_status": "program_checkable_source_difference" if result == "explicit_reported_grade_difference" else "descriptive_requires_engineering_review"})
        for support in supports:
            graph.add_edge("COMPARES", key, support, {"method": "explicit_comparison_input_membership"})
            graph.add_edge("DERIVED_FROM", key, support, {"method": "saved_comparison_input_membership"})
        return key

    for part in geology["day_partitions"]+geology["ahead_partitions"]:
        for result in part["comparisons"]:
            left, right = result["left_id"], result["right_id"]
            for field, detail in result["attributes"].items():
                if literal_relations and field != "grade":
                    continue
                if detail["status"] == "not_comparable":
                    continue
                attr = "rock_grade" if field == "grade" else field
                supports = (ids(left, attr) or ids(left))+(ids(right, attr) or ids(right))
                status = "explicit_reported_grade_difference" if field == "grade" and detail["status"] == "different_descriptions" else detail["status"]
                cp = comparison("geological_forecast_disagreement", supports, [part["start_m"], part["end_m"]],
                                status, detail, attribute=attr)
                if cp and status == "explicit_reported_grade_difference" and ids(left, attr) and ids(right, attr):
                    graph.add_edge("DIFFERS_FROM", ids(left, attr)[0], ids(right, attr)[0], {
                        "method": "same_attribute_explicit_unequal_reported_grade",
                        "overlap_range_m": [max(graph.nodes[ids(left, attr)[0]]["data"]["start_m"], graph.nodes[ids(right, attr)[0]]["data"]["start_m"]),
                                            min(graph.nodes[ids(left, attr)[0]]["data"]["end_m"], graph.nodes[ids(right, attr)[0]]["data"]["end_m"])]})
    for record in geology["design_records"]:
        design_ids = ids(record["record_id"]+":DESIGN-I01","rock_grade") or ids(record["record_id"]+":DESIGN-I01")
        if not design_ids or document_gate(graph.nodes[graph.nodes[design_ids[0]]["data"]["document_id"]]["data"],day)["status"] != "eligible":
            continue
        for value in record["comparisons"]:
            supports = design_ids+(ids(value["right_id"],"rock_grade") or ids(value["right_id"]))
            comparison("design_forecast_difference",supports,value["overlap_range_m"],value["status"],value["attributes"],
                       ["verified_design_source_and_date_required", "reported_assessments_not_unique_ground_truth"],attribute="rock_grade")

    responses, response_by_rows = [], {}
    preceding_features = None
    for joint in geology["joint_analysis"]:
        if not joint["forecast_record_ids"]:
            continue
        lo, hi = joint["range_m"]
        in_range = frame.chainage.ge(lo) & frame.chainage.lt(hi)
        if hi == day_range[1]:
            in_range |= frame.chainage.eq(hi)
        scope = frame.loc[in_range].copy()
        if "chainage_quality" in scope:
            scope = scope.loc[scope.chainage_quality.fillna("").str.startswith("usable")]
        good = scope.loc[scope.performance_eligible]
        windows = performance_windows(scope, package["config"], phase_config)
        key = identity("PLC", day, lo, hi)
        seg = identity("SEG", day, lo, hi, "response")
        graph.add_node("ConstructionSegment", seg, {
            "report_date": day, "segment_role": "geological_partition_PLC_reference",
            "start_m": lo, "end_m": hi, "segment_length_m": hi-lo, "geometry": "interval",
            "line": "right", "chainage_system": "DyK", "spatial_reference": "guide_shield_head",
            "spatial_quality": "nominal_partition_not_metre_level_validated_face_mapping"})
        graph.add_edge("OVERLAPS_WITH", seg, day_id, {"method": "same_line_chainage_system_geometry_intersection", "nominal_only": True})
        for source_seg in original_segments:
            if intersects(source_seg["data"], graph.nodes[seg]["data"]):
                graph.add_edge("OVERLAPS_WITH", source_seg["id"], seg, {"method": "same_line_chainage_system_geometry_intersection", "nominal_only": True})
        data = {"response_id": key, "report_date": day, "date": day, "start_m": lo, "end_m": hi,
                "start_time": good.time.iloc[0].isoformat() if len(good) else None,
                "end_time": good.time.iloc[-1].isoformat() if len(good) else None,
                "location_reference": "guide_shield_head_DyK_not_verified_face_position",
                "stable_duration_seconds": joint["summary"]["supported_duration_seconds"],
                "duration_definition": "existing_quality_sample_support_not_span_or_continuous_operation",
                "effective_sample_count": joint["summary"]["sample_count"], "total_sample_count": len(scope),
                "effective_ratio": len(good)/len(scope) if len(scope) else None,
                "summary": joint["summary"], "torque_cv": [w["torque_cv"] for w in windows],
                "continuous_windows": windows, "response_trends": [w["parameter_changes"] for w in windows],
                "baseline_reference": deepcopy(joint["response_comparison"]),
                "data_quality": {"status": joint["summary"]["status"], "unique_observed_chainages": int(good.chainage.nunique()),
                                 "spatial_offset_verified": False, "fine_fault_boundary_analysis_allowed": False,
                                 "no_valid_rpm_no_PR_FPI_SE": True},
                "source_trace": {"samples_path": package["source"]["samples"], "samples_sha256": package["source"]["samples_sha256"],
                                 "exact_rows": row_evidence(good), "units_and_formulae": package["performance"]["quality"],
                                 "sample_assignment": "half_open_intervals_final_daily_endpoint_in_last_segment"}}
        graph.add_node("PLCResponse", key, data)
        graph.add_edge("MEASURED_OVER", key, seg, {"method": "existing_qualified_PLC_rows_in_nominal_partition",
                                                 "not_verified_face_geometry": True})
        current_rows = tuple((r["source_file"], tuple(r["source_rows"])) for r in joint["summary"]["source"])
        response_by_rows[current_rows] = key
        supports = [i for r in joint["forecast_record_ids"] for i in ids(r)]+[key]
        ref = joint["response_comparison"].get("reference")
        reference_id = None
        if ref:
            ref_rows = tuple((r["source_file"], tuple(r["source_rows"])) for r in ref["source"])
            reference_id = response_by_rows.get(ref_rows)
            if reference_id and reference_id != key:
                supports.append(reference_id)
        data["baseline_response_id"] = reference_id
        if response_policy is not None:
            from tbm_process.reporting_v2.response_features import build_response_features, compare_responses, response_facts
            positions = sorted(frame.chainage.dropna().unique())
            increments = [b-a for a, b in zip(positions, positions[1:]) if b>a]
            features = build_response_features(scope, package["config"], phase_config, response_id=key,
                                               bounds=[lo, hi], day_resolution=min(increments) if increments else None)
            data["features_v2"] = features
            data["comparisons_v2"] = compare_responses(features, preceding_features, package["config"], response_policy)
            data["response_facts_v2"] = response_facts(features, day, package["config"])
            for fact in data["response_facts_v2"]:
                if fact["kind"] == "segment_parameter_level":
                    continue
                comparison("within_window_PLC_response", [key] if literal_relations else supports, [lo, hi], "descriptive_dynamic_fact",
                           fact, ["same_contiguous_window_or_explicit_adjacent_window_comparison", "not_diagnosis_or_geological_cause"],
                           fact["time_range"], attribute=fact["fact_id"])
            if any(s["status"] == "usable_descriptive" for s in features["metrics"].values()):
                preceding_features = features
        comparison("geology_plc_response_comparison", supports, [lo, hi], joint["response_comparison"]["status"],
                   joint["response_comparison"], ["nominal_spatial_co_location_only", "shield_to_face_offset_unknown",
                    "PR_FPI_SE_share_inputs_not_independent_evidence", "no_geological_causation", "no_precise_fault_boundary_inference"],
                   [data["start_time"], data["end_time"]])
        if reference_id:
            comparison("historical_response_comparison", [key, reference_id], [lo, hi],
                       joint["response_comparison"]["status"], joint["response_comparison"], time_range=[data["start_time"],data["end_time"]])
        responses.append(key)

    for value in geology["forecast_observation_comparisons"]:
        forecast, obs = value["forecast"], value["observation"]
        supports = (ids(forecast["record_id"], "rock_grade") or ids(forecast["record_id"]))+(ids(obs["record_id"], "rock_grade") or ids(obs["record_id"]))
        comparison("forecast_observation_comparison", supports, [obs["point_m"],obs["point_m"]], value["status"], value["attributes"],
                   ["reported_observation_not_perfect_ground_truth", "nominal_point_reference_offset_unknown", "forecast_predates_observation"], attribute="rock_grade")
    for value in geology["observation_comparisons"]:
        left, right = value["left"], value["right"]
        supports = (ids(left["record_id"], "rock_grade") or ids(left["record_id"]))+(ids(right["record_id"], "rock_grade") or ids(right["record_id"]))
        comparison("geological_condition_change", supports, sorted([left["point_m"],right["point_m"]]), value["status"], value["attributes"],
                   ["different_points_description_difference_not_confirmed_temporal_geological_change"], attribute="rock_grade")
    current_docs = [n for n in graph.typed("GeologyDocument") if n["data"]["metadata"].get("structured_source_path")
                    and document_gate(n["data"],day)["status"] == "eligible"
                    and n["data"]["start_m"] is not None and n["data"]["end_m"] is not None]
    current_docs.sort(key=lambda n:(min(abs(n["data"]["start_m"]-geology["shield_end_m"]),abs(n["data"]["end_m"]-geology["shield_end_m"])),
                                    -int(n["data"]["metadata"]["available_date"].replace("-",""))))
    current_docs = current_docs[:package["config"]["analysis"]["maximum_sketch_images"]]
    package["current_sketch_documents"] = [n["data"] for n in current_docs]
    new_forecast_ids = set()
    for doc in current_docs:
        observed_grades = [i for i in graph.neighbors(doc["id"],"HAS_FACT") if graph.nodes[i]["data"]["fact_type"] == "rock_grade"
                           and graph.nodes[i]["data"]["information_role"] == "observed"]
        for obs_id in observed_grades:
            obs = graph.nodes[obs_id]["data"]
            for forecast_node in graph.typed("GeologyFact"):
                forecast = forecast_node["data"]
                if forecast["fact_type"] != "rock_grade" or forecast["information_role"] != "forecast" or not intersects(forecast,obs):
                    continue
                gate = document_gate(graph.nodes[forecast["document_id"]]["data"],doc["data"]["metadata"]["available_date"])
                if gate["status"] != "eligible" or gate.get("role") != "prior_document":
                    continue
                same = forecast["attributes"]["grade"]["values"] == obs["attributes"]["grade"]["values"]
                bounds = [max(forecast["start_m"],obs["start_m"]),min(forecast["end_m"],obs["end_m"])]
                comparison("forecast_observation_comparison",[forecast_node["id"],obs_id],bounds,
                           "same_reported_grade_observation_time_unknown" if same else "different_reported_grade_observation_time_unknown",
                           {"forecast_grade":forecast["value"],"sketch_reported_grade":obs["value"]},
                           ["user_confirmed_report_availability_date_not_actual_observation_time", "source_range_only_not_metre_level_face_alignment",
                            "not_forecast_accuracy_or_confirmed_forecast_validation"],attribute="rock_grade")
                new_forecast_ids.add(forecast_node["id"])
    if len(current_docs) == 2:
        grade_sets = [[i for i in graph.neighbors(doc["id"],"HAS_FACT") if graph.nodes[i]["data"]["fact_type"]=="rock_grade"
                       and graph.nodes[i]["data"]["information_role"]=="observed"] for doc in current_docs]
        if all(grade_sets):
            a,b = [graph.nodes[items[0]]["data"] for items in grade_sets]
            comparison("geological_condition_change",[grade_sets[0][0],grade_sets[1][0]],
                       [min(a["start_m"],b["start_m"]),max(a["end_m"],b["end_m"])],
                       "same_reported_grade" if a["attributes"]["grade"]["values"]==b["attributes"]["grade"]["values"] else "different_reported_grade",
                       {"left":a["value"],"right":b["value"]},["different_record_ranges_not_confirmed_temporal_geological_change"],attribute="rock_grade")
    observation_records = [obs for pair in geology["observation_comparisons"] for obs in (pair["left"], pair["right"])] + geology["selected_observations"]
    graph.metadata.setdefault("daily_contexts", {})[day] = {
        "day_segment_id": day_id, "ahead_segment_id": forward_id, "day_range_m": day_range,
        "ahead_range_m": geology["ahead_range_m"], "shield_end_m": geology["shield_end_m"], "direction": geology["direction"],
        "observation_document_ids": sorted({graph.nodes[f]["data"]["document_id"] for obs in observation_records for f in ids(obs["record_id"])}|{n["id"] for n in current_docs}),
        "sketch_forecast_fact_ids": sorted({f for pair in geology["forecast_observation_comparisons"]
                                           for field in facts.get(pair["forecast"]["record_id"], {}).values() for f in field}|new_forecast_ids),
        "response_ids": responses, "position_limits": geology["position_limits"]}
    if response_policy is not None:
        graph.metadata["response_feature_policy"] = response_policy
    return responses
