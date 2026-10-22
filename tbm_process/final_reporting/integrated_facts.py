from __future__ import annotations

from copy import deepcopy
from datetime import date as date_type
from typing import Any

from .common import format_chainage, parse_chainage


SCHEMA_VERSION = "tbm_integrated_daily_facts_v1"


def _duration(seconds: float | int) -> str:
    total = int(round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}小时{minutes}分{seconds}秒"


def _check_geology_date(
    unit: dict[str, Any], report_date: str, report_dates: dict[str, str],
    observation_dates: dict[tuple[str, str, str], str],
) -> tuple[str, str]:
    evidence = unit.get("evidence") or {}
    if str(unit.get("source_type")) in {"tsp", "hsp"}:
        basis = evidence.get("report_date_basis") or {}
        if basis.get("status") not in (None, "eligible"):
            raise ValueError(f"Geology unit has ineligible date evidence: {unit['unit_id']}")
        available_date = basis.get("basis_date")
        if not available_date:
            available_date = report_dates.get(str(unit["source"]["source_report_id"]))
        elif basis.get("status") != "eligible":
            raise ValueError(f"Geology unit lacks eligible date evidence: {unit['unit_id']}")
        if not available_date:
            raise ValueError(f"Geology unit lacks eligible date evidence: {unit['unit_id']}")
        date_basis = "unit_evidence" if basis.get("basis_date") else "same_source_report_forecast_unit"
    else:
        available_date = evidence.get("observation_date")
        if not available_date:
            available_date = observation_dates.get(_observation_source_key(unit))
            if not available_date:
                raise ValueError(f"Historical observation lacks a date: {unit['unit_id']}")
            date_basis = "same_source_report_observation_unit"
        else:
            date_basis = "unit_evidence"
    if date_type.fromisoformat(str(available_date)) >= date_type.fromisoformat(report_date):
        raise ValueError(f"Geology unit was not available before {report_date}: {unit['unit_id']}")
    return str(available_date), date_basis


def _observation_source_key(unit: dict[str, Any]) -> tuple[str, str, str] | None:
    source = unit["source"]
    key = (
        str(source["source_report_id"]),
        str(source.get("source_file") or ""),
        str(source.get("source_sha256") or ""),
    )
    return key if all(key) else None


def build_integrated_facts(
    plc: dict[str, Any], geology_package: dict[str, Any]
) -> dict[str, Any]:
    report_date = str(plc["date"])
    if report_date != str(geology_package["date"]):
        raise ValueError("PLC and geology packages have different report dates")

    overview = plc["daily_overview"]
    advance = overview["daily_advance"]
    geo_context = geology_package["plc_context"]
    start_m = float(advance["start_value_m"])
    end_m = float(advance["end_value_m"])
    if (
        abs(start_m - float(geo_context["guide_shield_start_m"])) > 0.01
        or abs(end_m - float(geo_context["guide_shield_end_m"])) > 0.01
    ):
        raise ValueError("PLC and geology packages use different daily chainage ranges")

    facts: list[dict[str, Any]] = []

    def add(
        fact_id: str,
        kind: str,
        allowed_chapters: list[str],
        required_for_plan: bool,
        data: dict[str, Any],
        source: dict[str, Any],
    ) -> None:
        facts.append(
            {
                "fact_id": fact_id,
                "kind": kind,
                "allowed_chapters": allowed_chapters,
                "required_for_plan": required_for_plan,
                "data": data,
                "source": source,
            }
        )

    plc_source = {"domain": "plc", "original_field": "导向盾首里程"}
    add(
        "PLC:daily_advance", "daily_advance", ["1"], True,
        {
            "start_chainage": format_chainage(start_m),
            "end_chainage": format_chainage(end_m),
            "net_change_m": advance["net_change_m"],
            "display": f"{float(advance['net_change_m']):.2f} m",
            "definition": "first_to_last_valid_guide_shield_chainage",
        },
        plc_source,
    )
    add(
        "PLC:machine_time", "machine_time", ["1"], True,
        {
            "working_seconds": overview["working_duration_seconds"],
            "working_display": _duration(overview["working_duration_seconds"]),
            "stopped_seconds": overview["stopped_duration_seconds"],
            "stopped_display": _duration(overview["stopped_duration_seconds"]),
            "effective_seconds": overview["effective_state_duration_seconds"],
            "utilization_percent": overview["utilization_percent"],
            "utilization_display": f"{overview['utilization_percent']:.1f}%",
            "definition": overview["duration_definition"],
        },
        {"domain": "plc", "source_field": "machine_state"},
    )
    add(
        "PLC:coverage", "data_coverage", ["1"], True,
        {
            "report_date": report_date,
            "record_count": overview["record_count"],
            "coverage_start": overview["coverage_start"],
            "coverage_end": overview["coverage_end"],
            "hard_gap_count": overview["hard_gap_count"],
        },
        {"domain": "plc", "source_field": "sampling_time"},
    )
    add(
        "PLC:parameters", "daily_parameter_statistics", ["1", "2"], False,
        deepcopy(plc["daily_parameter_statistics"]),
        {"domain": "plc", "statistical_scope": "non_stopped_samples_inside_platforms"},
    )
    add(
        "PLC:platform_count", "platform_count", ["2"], False,
        {"count": len(plc["platforms"]), "meaning": "algorithmically_identified_operating_platforms"},
        {"domain": "plc", "source_field": "platform_first_analysis"},
    )

    for platform in plc["platforms"]:
        platform_id = str(platform["platform_id"])
        add(
            f"PLC:platform:{platform_id}", "operating_platform", ["2", "3"], False,
            {
                "start_time": platform["start_time"],
                "end_time": platform["end_time"],
                "working_duration_seconds": platform["working_duration_seconds"],
                "working_duration_display": _duration(platform["working_duration_seconds"]),
                "stopped_duration_inside_seconds": platform["stopped_duration_seconds_inside_platform"],
                "chainage_start_m": platform.get("chainage_start"),
                "chainage_end_m": platform.get("chainage_end"),
                "interval_advance": platform["interval_advance"],
                "parameter_statistics": platform["working_parameter_statistics"],
                "stable_phase_exists": platform["stable_phase"]["exists"],
                "reader_description": platform["reader_description"],
            },
            {
                "domain": "plc",
                "source_file": platform.get("source_file") or geo_context.get("source_file"),
                "source_row_start": platform["source_row_start"],
                "source_row_end": platform["source_row_end"],
            },
        )

    for index, change in enumerate(plc.get("major_construction_changes", []), start=1):
        add(
            f"PLC:change:{index:03d}", "observed_parameter_change", ["2", "3"], False,
            {
                "start_time": change["start_time"],
                "end_time": change["end_time"],
                "duration_seconds": change["duration_seconds"],
                "parameter_changes": change["parameter_changes"],
                "interpretation_limit": change["interpretation_limit"],
            },
            {
                "domain": "plc",
                "source_file": change.get("source_file"),
                "source_row_start": change.get("source_row_start"),
                "source_row_end": change.get("source_row_end"),
            },
        )

    report_dates: dict[str, str] = {}
    observation_dates: dict[tuple[str, str, str], str] = {}
    for unit in geology_package["content_units"]:
        if unit.get("source_type") == "sketch":
            observation_date = (unit.get("evidence") or {}).get("observation_date")
            if observation_date:
                source_key = _observation_source_key(unit)
                if source_key is not None:
                    if source_key in observation_dates and observation_dates[source_key] != str(observation_date):
                        raise ValueError(f"Conflicting observation dates for geology report: {source_key[0]}")
                    observation_dates[source_key] = str(observation_date)
            continue
        if unit.get("source_type") not in {"tsp", "hsp"}:
            continue
        basis = (unit.get("evidence") or {}).get("report_date_basis") or {}
        if basis.get("status") != "eligible" or not basis.get("basis_date"):
            continue
        report_id = str(unit["source"]["source_report_id"])
        available_date = str(basis["basis_date"])
        if report_id in report_dates and report_dates[report_id] != available_date:
            raise ValueError(f"Conflicting availability dates for geology report: {report_id}")
        report_dates[report_id] = available_date
    for unit in geology_package["content_units"]:
        available_date, available_date_basis = _check_geology_date(
            unit, report_date, report_dates, observation_dates
        )
        add(
            f"GEO:{unit['unit_id']}", unit["unit_type"], ["3"],
            bool(unit["required_for_plan"]),
            {
                "semantics": unit["semantics"],
                "source_type": unit["source_type"],
                "section": unit["allowed_section"],
                "reporting_role": unit.get("reporting_role"),
                "available_date": available_date,
                "available_date_basis": available_date_basis,
                "evidence": deepcopy(unit["evidence"]),
            },
            {"domain": "geology", **deepcopy(unit["source"]), "source_fact_id": unit.get("source_fact_id")},
        )

    if not geology_package["content_units"]:
        add(
            "GEO:availability", "geology_availability", ["3"], True,
            {
                "section": "3.1",
                "source_type": "availability",
                "eligible_content_unit_count": 0,
                "statement": "截至当日00:00，当前地质候选包没有可用于本日的内容单元。",
            },
            {"domain": "geology", "source_package": "03_v2_content_units.json"},
        )

    links: list[dict[str, Any]] = []
    forecast_units = [
        fact for fact in facts
        if fact["source"]["domain"] == "geology"
        and fact["kind"] == "forecast_segment_portion"
    ]
    for platform in plc["platforms"]:
        p_start = platform.get("chainage_start")
        p_end = platform.get("chainage_end")
        if p_start is None or p_end is None:
            continue
        lower, upper = sorted((float(p_start), float(p_end)))
        for forecast in forecast_units:
            evidence = forecast["data"]["evidence"]
            f_start = parse_chainage(evidence.get("applicable_start_chainage"))
            f_end = parse_chainage(evidence.get("applicable_end_chainage"))
            if f_start is None or f_end is None:
                continue
            intersection_start = max(lower, f_start)
            intersection_end = min(upper, f_end)
            if intersection_start > intersection_end:
                continue
            if intersection_start == intersection_end and lower != upper:
                continue
            links.append(
                {
                    "link_id": f"LINK:{len(links) + 1:04d}",
                    "plc_fact_id": f"PLC:platform:{platform['platform_id']}",
                    "geology_fact_id": forecast["fact_id"],
                    "relation": "spatial_co_location_only",
                    "overlap_start_m": intersection_start,
                    "overlap_end_m": intersection_end,
                    "limits": [
                        "guide_shield_chainage_is_not_confirmed_face_chainage",
                        "machine_response_does_not_verify_forecast_accuracy",
                    ],
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "date": report_date,
        "geology_as_of": f"{report_date}T00:00:00",
        "daily_guide_shield_range_m": [start_m, end_m],
        "plc_platform_count": len(plc["platforms"]),
        "facts": facts,
        "cross_source_links": links,
        "planning_obligations": deepcopy(geology_package.get("planning_obligations", [])),
        "limits": [
            "Current-day PLC evidence is available after the construction day; geology candidates must predate day start.",
            "Cross-source links encode spatial co-location, not geological verification or causation.",
            "Unknown PLC engineering units must remain labeled as original PLC record values.",
            "The complete platform table and figures are rendered deterministically even when prose selects fewer platforms.",
        ],
    }


def build_llm_input(package: dict[str, Any]) -> dict[str, Any]:
    facts = []
    for fact in package["facts"]:
        source = fact["source"]
        facts.append(
            {
                "fact_id": fact["fact_id"],
                "kind": fact["kind"],
                "allowed_chapters": fact["allowed_chapters"],
                "required_for_plan": fact["required_for_plan"],
                "data": fact["data"],
                "source": {
                    key: source.get(key)
                    for key in ("domain", "source_type", "source_report_id", "source_page", "source_section")
                    if source.get(key) is not None
                },
            }
        )
    return {
        "schema_version": package["schema_version"],
        "date": package["date"],
        "geology_as_of": package["geology_as_of"],
        "plc_platform_count": package["plc_platform_count"],
        "facts": facts,
        "cross_source_links": package["cross_source_links"],
        "planning_obligations": package["planning_obligations"],
        "limits": package["limits"],
    }
