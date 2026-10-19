from __future__ import annotations

from copy import deepcopy

from tbm_process.reporting.semantics import (
    build_construction_semantics,
    render_semantics_markdown,
    validate_semantic_payload,
)


def _cycle(
    interval_id: str,
    start: str,
    end: str,
    row_start: int,
    chainage_change: float,
    *,
    cumulative_change: float | None = None,
    integrated_distance: float | None = None,
) -> dict:
    cumulative_change = chainage_change if cumulative_change is None else cumulative_change
    integrated_distance = chainage_change if integrated_distance is None else integrated_distance
    summary = {
        "date": "2023-11-03",
        "interval_id": interval_id,
        "start_time": start,
        "end_time": end,
        "duration_seconds": 1800,
        "sample_count": 91,
        "interval_chainage_change_m": chainage_change,
        "recorded_cumulative_advance_change_m": cumulative_change,
        "valid_speed_integrated_distance_m": integrated_distance,
        "distance_consistency_status": "consistent_within_threshold",
        "source_file": "tbm_data_20231103.csv",
        "source_row_start": row_start,
        "source_row_end": row_start + 90,
        "advance_speed_count": 91,
        "thrust_count": 91,
        "torque_count": 91,
        "rpm_count": 91,
        "segmentation_status": "stable_phase_identified",
    }
    phase_summary = {
        "interval_id": interval_id,
        "phase_id": f"{interval_id}-P03",
        "phase_label": "stable",
        "start_time": start,
        "end_time": end,
        "duration_seconds": 1800,
        "dynamic_analysis_scope_only": False,
        "advance_speed_p50": 40.0,
        "advance_speed_p10": 35.0,
        "advance_speed_p90": 45.0,
        "advance_speed_valid_ratio": 1.0,
        "thrust_p50": 22000.0,
        "thrust_p10": 21000.0,
        "thrust_p90": 23000.0,
        "thrust_valid_ratio": 1.0,
        "torque_p50": 4000.0,
        "torque_p10": 3500.0,
        "torque_p90": 4500.0,
        "torque_valid_ratio": 1.0,
        "rpm_p50": 5.5,
        "rpm_p10": 5.2,
        "rpm_p90": 5.8,
        "rpm_valid_ratio": 1.0,
    }
    response = {
        "coupled_event_id": f"{interval_id}-P03-M0001",
        "interval_id": interval_id,
        "phase_id": f"{interval_id}-P03",
        "phase_label": "stable",
        "start_time": start,
        "end_time": end,
        "duration_seconds": 1800,
        "parameter_states": {
            "advance_speed": {
                "trend": "increasing",
                "fluctuation_level": "moderate",
                "relative_change": 0.8,
                "absolute_change": 10.0,
            },
            "thrust": {
                "trend": "decreasing",
                "fluctuation_level": "moderate",
                "relative_change": -0.7,
                "absolute_change": -1000.0,
            },
            "torque": {
                "trend": "increasing",
                "fluctuation_level": "high",
                "relative_change": 0.9,
                "absolute_change": 900.0,
            },
        },
        "source_file": "tbm_data_20231103.csv",
        "source_row_start": row_start,
        "source_row_end": row_start + 90,
    }
    return {
        "cycle_summary": summary,
        "phases": [{"phase_summary": phase_summary}],
        "coupled_parameter_events": [response],
    }


def _daily_result() -> dict:
    first = _cycle(
        "2023-11-03-A060",
        "2023-11-03T13:31:30",
        "2023-11-03T14:01:30",
        2800,
        0,
        cumulative_change=0,
        integrated_distance=0.02,
    )
    second = _cycle(
        "2023-11-03-A061", "2023-11-03T14:10:00", "2023-11-03T14:40:00", 2900, 1
    )
    second["coupled_parameter_events"][0]["phase_label"] = "startup"
    return {
        "schema_version": "tbm_process_analysis_v3",
        "date": "2023-11-03",
        "source_file": "tbm_data_20231103.csv",
        "result_type": "complete_traceable_fact_package",
        "overall": {
            "row_count": 100,
            "timestamp_start": "2023-11-03T00:00:10",
            "timestamp_end": "2023-11-03T23:59:30",
            "chainage_start": 1013901,
            "chainage_end": 1013910,
            "chainage_change_m": 9,
            "data_coverage": {"hard_sampling_gap_count": 0},
        },
        "machine_states": {
            "duration_seconds": {"advancing": 3600, "free_rotation": 600, "stopped": 3000}
        },
        "data_coverage": {"row_count": 100, "hard_sampling_gap_count": 0},
        "excavation_cycles": [first, second],
        "construction_response_events": [],
        "data_quality_events": [
            {
                "event_id": "2023-11-03-Q001",
                "subtype": "unreliable_advance_speed",
                "start_time": "2023-11-03T04:42:40",
                "end_time": "2023-11-03T04:42:50",
                "source_file": "tbm_data_20231103.csv",
                "source_row_start": 50,
                "source_row_end": 50,
            }
        ],
        "selection_metadata": {
            "result_type": "complete_unfiltered_analysis",
            "cycle_count": 2,
        },
        "method_limits": [],
    }


def test_semantics_hide_internal_vocabulary_and_use_chainage_advance() -> None:
    payload = build_construction_semantics(_daily_result())
    markdown = render_semantics_markdown(payload)
    for forbidden in ("A060", "P03", "startup", "stable", "有效推进区间", "速度积分"):
        assert forbidden not in markdown
    assert "今日进尺为 9.00 m" in markdown
    advance = next(row for row in payload["daily_overview"] if row["fact_type"] == "daily_advance")
    assert advance["distance_source"] == "guidance_shield_head_chainage"


def test_experimental_source_is_named_without_claiming_frozen_segmentation() -> None:
    payload = build_construction_semantics(_daily_result())
    payload["generation"]["source_analysis_variant"] = "tbm_platform_first_downstream_v1"
    markdown = render_semantics_markdown(payload)
    assert "由平台优先候选分析结果" in markdown
    assert "由冻结版 PLC 分析结果" not in markdown


def test_zero_chainage_does_not_exclude_process_and_transition_event_is_not_reported() -> None:
    payload = build_construction_semantics(_daily_result())
    decision = next(
        row
        for row in payload["selection_metadata"]["process_decisions"]
        if row["internal_interval_id"] == "2023-11-03-A060"
    )
    assert decision["decision"] == "eligible_for_session_organization"
    assert "zero_interval_chainage_not_used_as_exclusion" in decision["reasons"]
    process = next(
        row
        for row in payload["main_processes"]
        if row["fact_type"] == "representative_construction_period"
    )
    weak_activity = process["activities"][0]
    assert weak_activity["activity_expression_class"] == "advance_related_operation"
    assert weak_activity["activity_evidence"]["distance_evidence_strength"] == "weak"
    assert "局部进尺证据" in process["text"]
    assert len(payload["construction_responses"]) == 1
    response_text = payload["construction_responses"][0]["text"]
    assert "推进速度、刀盘扭矩整体呈上升趋势" in response_text
    assert "总推力整体呈下降趋势" in response_text
    assert "持续上升" not in response_text


def test_confirmed_excavation_and_intermittent_span_are_distinguished() -> None:
    payload = build_construction_semantics(_daily_result())
    process = next(
        row
        for row in payload["main_processes"]
        if row["fact_type"] == "representative_construction_period"
    )
    assert process["continuity_class"] == "intermittent_activities"
    assert process["activity_expression_class"] == "mixed_excavation_and_advance_related_operation"
    assert process["activities"][1]["activity_expression_class"] == "confirmed_excavation"
    assert process["process_count"] == 2
    assert len(process["intervening_gaps"]) == 1
    assert process["intervening_gaps"][0]["duration_seconds"] == 510
    assert process["total_process_duration_seconds"] == 3600
    assert process["elapsed_span_seconds"] == 4110
    assert "各次活动之间存在间隔" in process["text"]
    assert "该时间跨度不表示连续掘进" in process["text"]


def test_single_normal_process_is_described_as_excavation() -> None:
    daily = _daily_result()
    daily["excavation_cycles"] = [daily["excavation_cycles"][1]]
    payload = build_construction_semantics(daily)
    process = payload["main_processes"][0]
    assert process["activity_expression_class"] == "confirmed_excavation"
    assert process["continuity_class"] == "single_continuous_activity"
    assert "设备开展了一段掘进作业" in process["text"]
    assert "推进相关运行活动" not in process["text"]


def test_reader_facts_are_traceable_and_units_are_not_published() -> None:
    payload = build_construction_semantics(_daily_result())
    validation = validate_semantic_payload(payload)
    assert validation["passed"] is True
    process = payload["main_processes"][0]
    evidence = payload["traceability"][process["fact_id"]]
    assert evidence["internal_interval_ids"] == ["2023-11-03-A060", "2023-11-03-A061"]
    assert all(
        stat["allowed_for_numeric_reporting"] is False
        for stat in process["parameter_statistics"].values()
    )
    assert len(evidence["activity_evidence"]) == 2
    assert evidence["intervening_gaps"][0]["duration_seconds"] == 510


def test_quality_and_construction_responses_are_separate_sections() -> None:
    payload = build_construction_semantics(_daily_result())
    quality_text = " ".join(row["text"] for row in payload["data_quality"])
    response_text = " ".join(row["text"] for row in payload["construction_responses"])
    assert "未据此补造真实速度" in quality_text
    assert "不可信" not in response_text
    assert "llm_input_package" not in payload


def test_overlapping_duplicate_response_is_reported_once() -> None:
    daily = _daily_result()
    duplicate = deepcopy(daily["excavation_cycles"][0]["coupled_parameter_events"][0])
    duplicate["coupled_event_id"] = "2023-11-03-A060-P03-M0002"
    duplicate["start_time"] = "2023-11-03T13:35:00"
    duplicate["end_time"] = "2023-11-03T14:00:00"
    duplicate["duration_seconds"] = 1500
    daily["excavation_cycles"][0]["coupled_parameter_events"].append(duplicate)
    payload = build_construction_semantics(daily)
    assert len(payload["construction_responses"]) == 1
    decisions = payload["selection_metadata"]["response_decisions"]
    assert any(row["decision"] == "merged_as_duplicate" for row in decisions)


def test_parameter_levels_record_explicit_same_scope_baseline() -> None:
    daily = _daily_result()
    cycles = []
    for number, (hour, speed, thrust, torque) in enumerate(
        ((1, 10, 30000, 1000), (4, 20, 25000, 2000), (8, 40, 20000, 4000)),
        start=1,
    ):
        cycle = _cycle(
            f"2023-11-03-A{number:03d}",
            f"2023-11-03T{hour:02d}:00:00",
            f"2023-11-03T{hour:02d}:30:00",
            number * 100,
            1,
        )
        phase = cycle["phases"][0]["phase_summary"]
        phase["advance_speed_p50"] = speed
        phase["thrust_p50"] = thrust
        phase["torque_p50"] = torque
        cycles.append(cycle)
    daily["excavation_cycles"] = cycles
    payload = build_construction_semantics(daily)
    processes = [
        row
        for row in payload["main_processes"]
        if row["fact_type"] == "representative_construction_period"
    ]
    assert len(processes) == 3
    for process in processes:
        for parameter in process["relative_parameter_levels"]:
            basis = process["parameter_comparison_basis"][parameter]
            assert basis["comparison_cohort"] == "selected_sessions_with_comparable_sustained_operation"
            assert basis["statistic"] == "weighted_median_of_sustained_operation_medians"
            assert basis["current_value"] is not None
            assert basis["lower_threshold"] <= basis["upper_threshold"]


def test_chain_merge_stops_at_configured_maximum_span() -> None:
    daily = _daily_result()
    cycles = []
    for number, hour in enumerate((0, 1, 2, 3, 4), start=1):
        cycles.append(
            _cycle(
                f"2023-11-03-A{number:03d}",
                f"2023-11-03T{hour:02d}:00:00",
                f"2023-11-03T{hour:02d}:30:00",
                number * 100,
                1,
            )
        )
    daily["excavation_cycles"] = cycles
    payload = build_construction_semantics(daily)
    processes = [
        row
        for row in payload["main_processes"]
        if row["fact_type"] == "representative_construction_period"
    ]
    assert len(processes) == 2
    assert all(row["elapsed_span_seconds"] <= 4 * 3600 for row in processes)


def test_response_retains_quantitative_evidence_without_publishing_units() -> None:
    payload = build_construction_semantics(_daily_result())
    response = payload["construction_responses"][0]
    speed = response["parameter_states"]["advance_speed"]
    assert speed["absolute_change"] == 10
    assert speed["relative_change_internal_only"] == 0.8
    assert speed["unit_status"] == "unconfirmed_plc_point_table"
    assert speed["allowed_for_numeric_reporting"] is False
    assert response["related_main_process_fact_id"] in {
        row["fact_id"] for row in payload["main_processes"]
    }
    assert "整体呈上升趋势" in response["text"]
