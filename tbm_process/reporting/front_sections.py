from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SCHEMA_VERSION = "tbm_daily_report_front_sections_v1"
PROHIBITED_DESCRIPTION_TERMS = (
    "故障",
    "地质",
    "围岩",
    "刀具磨损",
    "效率低下",
    "显著异常",
)


@dataclass(frozen=True)
class ReportSources:
    samples: Path
    platforms: Path
    phases: Path


def load_report_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_front_sections_facts(
    *,
    date: str,
    sources: ReportSources,
    config: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    samples = pd.read_csv(sources.samples, parse_dates=["time"])
    platforms = pd.read_csv(sources.platforms)
    phases = pd.read_csv(sources.phases) if sources.phases.is_file() else pd.DataFrame()
    _validate_source_columns(samples, platforms, config)

    day = samples.loc[samples["time"].dt.strftime("%Y-%m-%d").eq(date)].copy()
    if day.empty:
        raise ValueError(f"No processed samples found for {date}")
    day = day.sort_values("time").reset_index(drop=True)
    platform_rows = platforms.loc[platforms["date"].astype(str).eq(date)].copy()
    platform_rows = platform_rows.sort_values("start_time").reset_index(drop=True)

    overview = _build_daily_overview(day, date=date, config=config)
    intervals: list[dict[str, Any]] = []
    for _, platform in platform_rows.iterrows():
        platform_id = str(platform["platform_id"])
        block = day.loc[day[config["platform_id_column"]].eq(platform_id)].copy()
        if block.empty:
            raise ValueError(f"Platform {platform_id} has no assigned samples")
        intervals.append(
            _build_platform_fact(
                block,
                platform=platform,
                phases=phases,
                config=config,
            )
        )

    descriptions = build_platform_descriptions(intervals)
    by_id = {row["platform_id"]: row for row in descriptions}
    for interval in intervals:
        description = by_id[interval["platform_id"]]
        interval["reader_description"] = description["description"]
        interval["description_provenance"] = description["provenance"]
        interval["description_evidence"] = description["evidence"]

    facts = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "generation": {
            "source_layer": "current_platform_first_processed_outputs",
            "source_files": {
                "processed_samples": str(sources.samples.resolve()),
                "platforms": str(sources.platforms.resolve()),
                "phases": str(sources.phases.resolve()),
            },
            "segmentation_recomputed": False,
            "frozen_or_current_analysis_modified": False,
            "llm_invoked": False,
        },
        "daily_overview": overview,
        "platform_count": len(intervals),
        "platforms": intervals,
        "reporting_limits": [
            "Platforms are algorithmically identified operating periods, not independently verified excavation cycles.",
            "Per-platform advance uses the PLC cumulative-advance field and is not substituted for daily chainage advance.",
            "Parameter values retain the processed PLC record scale because engineering units are not asserted here.",
            "A missing reliable stable phase is retained as a segmentation limitation, not interpreted as a fault.",
        ],
    }
    return facts, day


def build_platform_descriptions(
    intervals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    medians: dict[str, np.ndarray] = {}
    for parameter in ("advance_speed", "thrust", "torque"):
        medians[parameter] = np.asarray(
            [
                float(row["working_parameter_statistics"][parameter]["median"])
                if row["working_parameter_statistics"][parameter]["median"] is not None
                else np.nan
                for row in intervals
            ],
            dtype=float,
        )
    thresholds = {
        name: (
            float(np.nanquantile(values, 0.35)),
            float(np.nanquantile(values, 0.65)),
        )
        for name, values in medians.items()
    }

    rows: list[dict[str, Any]] = []
    for index, interval in enumerate(intervals):
        work_seconds = float(interval["working_duration_seconds"])
        stable = bool(interval["stable_phase"]["exists"])
        speed = medians["advance_speed"][index]
        thrust = medians["thrust"][index]
        torque = medians["torque"][index]
        evidence: list[str] = []

        if work_seconds < 600:
            description = "短时运行，随后停止"
            evidence.append("working_duration_below_600_seconds")
        elif not stable:
            description = "运行时间较短，阶段未完整识别"
            evidence.append("no_reliable_stable_phase")
        elif _level(speed, thresholds["advance_speed"]) == "low" and _level(
            thrust, thresholds["thrust"]
        ) == "high":
            description = "低速运行，推力相对较高"
            evidence.extend(["advance_speed_lower_group", "thrust_upper_group"])
        elif _level(speed, thresholds["advance_speed"]) == "high" and _level(
            torque, thresholds["torque"]
        ) == "high":
            description = "速度较高，扭矩相对较高"
            evidence.extend(["advance_speed_upper_group", "torque_upper_group"])
        elif _level(speed, thresholds["advance_speed"]) == "high":
            description = "速度相对较高，运行总体连续"
            evidence.append("advance_speed_upper_group")
        elif _level(torque, thresholds["torque"]) == "low":
            description = "扭矩相对较低，运行总体平稳"
            evidence.append("torque_lower_group")
        else:
            description = "运行参数总体平稳"
            evidence.append("stable_phase_identified")

        rows.append(
            {
                "platform_id": interval["platform_id"],
                "description": description,
                "provenance": "deterministic_fact_based_rule",
                "evidence": evidence,
                "llm_invoked": False,
            }
        )
    return rows


def validate_front_sections_facts(
    facts: dict[str, Any], samples: pd.DataFrame, config: dict[str, Any]
) -> dict[str, Any]:
    intervals = facts["platforms"]
    ids = [row["platform_id"] for row in intervals]
    expected_ids = list(
        dict.fromkeys(
            samples[config["platform_id_column"]].dropna().astype(str).tolist()
        )
    )
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    overview = facts["daily_overview"]
    state_total = sum(overview["machine_state_duration_seconds"].values())
    add("all_platforms_included_once", ids == expected_ids, {"facts": ids, "samples": expected_ids})
    add("platform_count_matches_assignments", len(ids) == len(expected_ids), len(ids))
    add("platform_ids_unique", len(ids) == len(set(ids)), ids)
    add(
        "daily_duration_denominator_matches_states",
        abs(state_total - overview["effective_state_duration_seconds"]) < 1e-6,
        {"state_total": state_total, "denominator": overview["effective_state_duration_seconds"]},
    )
    add(
        "daily_work_plus_stop_matches_denominator",
        abs(
            overview["working_duration_seconds"]
            + overview["stopped_duration_seconds"]
            - overview["effective_state_duration_seconds"]
        )
        < 1e-6,
        overview,
    )
    add(
        "daily_advance_uses_chainage",
        overview["daily_advance"]["source_field"] == "chainage",
        overview["daily_advance"],
    )
    add(
        "interval_advance_uses_cumulative_field",
        all(row["interval_advance"]["source_field"] == "cumulative_advance" for row in intervals),
        [row["interval_advance"]["source_field"] for row in intervals],
    )
    add(
        "platform_work_excludes_stopped",
        all(
            abs(
                row["working_duration_seconds"]
                + row["stopped_duration_seconds_inside_platform"]
                - row["observed_duration_seconds_inside_platform"]
            )
            < 1e-6
            for row in intervals
        ),
        None,
    )
    add(
        "descriptions_are_constrained",
        all(
            4 <= len(row["reader_description"]) <= 20
            and not any(term in row["reader_description"] for term in PROHIBITED_DESCRIPTION_TERMS)
            for row in intervals
        ),
        [row["reader_description"] for row in intervals],
    )
    add(
        "platform_boundaries_match_samples",
        all(_boundary_matches(row, samples, config["platform_id_column"]) for row in intervals),
        None,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "date": facts["date"],
        "passed": all(row["passed"] for row in checks),
        "checks": checks,
        "limitations": [
            "This validates data lineage and internal consistency, not engineering truth or segmentation accuracy.",
            "Per-platform table descriptions were generated by deterministic rules; report narrative generation is recorded separately.",
        ],
    }


def interval_table_frame(facts: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for interval in facts["platforms"]:
        advance = interval["interval_advance"]
        rows.append(
            {
                "起止时间": f"{_clock(interval['start_time'])}～{_clock(interval['end_time'])}",
                "工作时长": format_duration_cn(interval["working_duration_seconds"]),
                "进尺（m）": (
                    f"{advance['net_change_m']:.2f}"
                    if advance["use_status"] == "adoptable" and advance["net_change_m"] is not None
                    else "待核实"
                ),
                "主要情况": interval["reader_description"],
            }
        )
    return pd.DataFrame(rows)


def format_duration_cn(seconds: float) -> str:
    value = int(round(float(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{secs:02d}秒"
    return f"{minutes}分{secs:02d}秒"


def format_dk(value: float) -> str:
    rounded = int(round(float(value)))
    kilometres, metres = divmod(rounded, 1000)
    return f"DK{kilometres}+{metres:03d}"


def _build_daily_overview(
    day: pd.DataFrame, *, date: str, config: dict[str, Any]
) -> dict[str, Any]:
    dt = pd.to_numeric(day["sample_dt_seconds"], errors="coerce").fillna(0.0)
    states = day["machine_state"].astype(str)
    state_durations = {
        str(state): float(dt.loc[states.eq(str(state))].sum())
        for state in states.dropna().unique()
    }
    stopped = float(state_durations.get(config["stopped_state"], 0.0))
    effective = float(sum(state_durations.values()))
    working = float(effective - stopped)
    if (
        "chainage_step_quality" in day
        and day["chainage_step_quality"].eq("suspicious_short_gap_jump").any()
    ):
        raise ValueError(
            "Daily chainage contains a suspicious short-gap jump and cannot be reported"
        )
    chainage = pd.to_numeric(day["chainage"], errors="coerce").dropna()
    if len(chainage) < 2:
        raise ValueError("At least two valid chainage samples are required")
    start = float(chainage.iloc[0])
    end = float(chainage.iloc[-1])
    return {
        "date": date,
        "record_count": int(len(day)),
        "coverage_start": day["time"].iloc[0].isoformat(),
        "coverage_end": day["time"].iloc[-1].isoformat(),
        "hard_gap_count": int(day["hard_gap_before"].fillna(False).sum()),
        "daily_advance": {
            "source_field": "chainage",
            "source_original_field": "导向盾首里程",
            "start_value_m": start,
            "end_value_m": end,
            "start_dk": format_dk(start),
            "end_dk": format_dk(end),
            "net_change_m": end - start,
        },
        "machine_state_duration_seconds": state_durations,
        "working_duration_seconds": working,
        "stopped_duration_seconds": stopped,
        "effective_state_duration_seconds": effective,
        "utilization_percent": 100.0 * working / effective if effective else None,
        "duration_definition": "sum_of_capped_observed_sample_durations; hard gaps excluded",
        "working_definition": "all classified non-stopped machine states",
    }


def _build_platform_fact(
    block: pd.DataFrame,
    *,
    platform: pd.Series,
    phases: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    dt = pd.to_numeric(block["sample_dt_seconds"], errors="coerce").fillna(0.0)
    stopped_mask = block["machine_state"].astype(str).eq(config["stopped_state"])
    work = block.loc[~stopped_mask].copy()
    stable = block.loc[block[config["phase_label_column"]].eq("stable")].copy()
    platform_id = str(platform["platform_id"])
    phase_rows = (
        [
            {key: _json_scalar(value) for key, value in row.items()}
            for row in phases.loc[
                phases["interval_id"].astype(str).eq(platform_id)
            ].to_dict("records")
        ]
        if not phases.empty and "interval_id" in phases
        else []
    )
    return {
        "platform_id": platform_id,
        "start_time": block["time"].iloc[0].isoformat(),
        "end_time": block["time"].iloc[-1].isoformat(),
        "elapsed_span_seconds": float(
            (block["time"].iloc[-1] - block["time"].iloc[0]).total_seconds()
        ),
        "observed_duration_seconds_inside_platform": float(dt.sum()),
        "working_duration_seconds": float(dt.loc[~stopped_mask].sum()),
        "stopped_duration_seconds_inside_platform": float(dt.loc[stopped_mask].sum()),
        "sample_count": int(len(block)),
        "working_sample_count": int((~stopped_mask).sum()),
        "source_row_start": int(block["source_row"].iloc[0]),
        "source_row_end": int(block["source_row"].iloc[-1]),
        "chainage_start": _edge_number(block["chainage"], first=True),
        "chainage_end": _edge_number(block["chainage"], first=False),
        "interval_advance": _interval_advance_quality(block, config["advance_quality"]),
        "working_parameter_statistics": _parameter_statistics(work, config),
        "stable_phase": {
            "exists": bool(len(stable)),
            "sample_count": int(len(stable)),
            "statistics": _parameter_statistics(stable, config) if len(stable) else None,
        },
        "phase_count": len(phase_rows),
        "phases": phase_rows,
        "segmentation_status": str(platform.get("segmentation_status", "unknown")),
        "data_lineage": {
            "platform_boundaries": "existing platform-first analysis output",
            "machine_states": "existing quality-controlled processed samples",
            "parameter_statistics_scope": "non-stopped samples assigned to this platform",
        },
    }


def _interval_advance_quality(
    block: pd.DataFrame, config: dict[str, Any]
) -> dict[str, Any]:
    values = pd.to_numeric(block["cumulative_advance"], errors="coerce")
    valid = values.dropna()
    missing_count = int(values.isna().sum())
    missing_ratio = float(missing_count / len(values)) if len(values) else 1.0
    result: dict[str, Any] = {
        "source_field": "cumulative_advance",
        "source_original_field": "开累进尺",
        "unit": "m",
        "first_value": None,
        "last_value": None,
        "net_change_m": None,
        "valid_sample_count": int(len(valid)),
        "missing_sample_count": missing_count,
        "missing_ratio": missing_ratio,
        "negative_step_count": 0,
        "minor_backtrack_count": 0,
        "reset_count": 0,
        "positive_jump_count": 0,
        "maximum_positive_step_m": None,
        "maximum_negative_step_m": None,
        "use_status": "unusable",
        "quality_reasons": [],
        "evidence_rows": [],
        "use_limit": "PLC cumulative field; not a substitute for daily chainage advance",
    }
    if len(valid) < 2:
        result["quality_reasons"].append("fewer_than_two_valid_samples")
        return result

    first_index = valid.index[0]
    last_index = valid.index[-1]
    first = float(valid.iloc[0])
    last = float(valid.iloc[-1])
    diffs = valid.diff().dropna()
    negative = diffs.loc[diffs.lt(0)]
    reset = negative.loc[negative.le(-float(config["reset_drop_threshold_m"]))]
    minor = negative.loc[
        negative.abs().le(float(config["minor_backtrack_tolerance_m"]))
    ]
    positive_jump = diffs.loc[diffs.gt(float(config["positive_jump_threshold_m"]))]
    net = last - first
    reasons: list[str] = []
    if missing_ratio > float(config["maximum_missing_ratio"]):
        reasons.append("excessive_missing_values")
    if len(reset):
        reasons.append("cumulative_reset_or_large_backtrack")
    if len(positive_jump):
        reasons.append("cumulative_positive_jump")
    if net < float(config["minimum_adoptable_net_change_m"]):
        reasons.append("negative_net_change")
    status = "adoptable" if not reasons else "review_required"
    evidence_rows = sorted(
        {
            int(block.loc[index, "source_row"])
            for index in list(reset.index) + list(positive_jump.index) + list(minor.index)
        }
    )
    result.update(
        {
            "first_value": first,
            "last_value": last,
            "first_source_row": int(block.loc[first_index, "source_row"]),
            "last_source_row": int(block.loc[last_index, "source_row"]),
            "net_change_m": net,
            "negative_step_count": int(len(negative)),
            "minor_backtrack_count": int(len(minor)),
            "reset_count": int(len(reset)),
            "positive_jump_count": int(len(positive_jump)),
            "maximum_positive_step_m": float(diffs.max()) if len(diffs) else None,
            "maximum_negative_step_m": float(diffs.min()) if len(diffs) else None,
            "use_status": status,
            "quality_reasons": reasons or ["no_disqualifying_cumulative_field_issue"],
            "evidence_rows": evidence_rows,
        }
    )
    return result


def _parameter_statistics(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column, metadata in config["parameters"].items():
        values = pd.to_numeric(frame[column], errors="coerce")
        quality_column = metadata.get("quality_column")
        if quality_column and quality_column in frame:
            values = values.where(frame[quality_column].isin(metadata["allowed_quality"]))
        valid = values.dropna()
        result[column] = {
            "display_name": metadata["display_name"],
            "value_label": metadata["value_label"],
            "valid_sample_count": int(len(valid)),
            "median": _safe_quantile(valid, 0.5),
            "p10": _safe_quantile(valid, 0.1),
            "p90": _safe_quantile(valid, 0.9),
        }
    return result


def _safe_quantile(values: pd.Series, quantile: float) -> float | None:
    return float(values.quantile(quantile)) if len(values) else None


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _edge_number(values: pd.Series, *, first: bool) -> float | None:
    valid = pd.to_numeric(values, errors="coerce").dropna()
    if valid.empty:
        return None
    return float(valid.iloc[0] if first else valid.iloc[-1])


def _level(value: float, bounds: tuple[float, float]) -> str:
    if not np.isfinite(value):
        return "unknown"
    if value <= bounds[0]:
        return "low"
    if value >= bounds[1]:
        return "high"
    return "middle"


def _boundary_matches(
    interval: dict[str, Any], samples: pd.DataFrame, platform_column: str
) -> bool:
    block = samples.loc[samples[platform_column].eq(interval["platform_id"])]
    if block.empty:
        return False
    return (
        pd.Timestamp(interval["start_time"]) == pd.Timestamp(block["time"].iloc[0])
        and pd.Timestamp(interval["end_time"]) == pd.Timestamp(block["time"].iloc[-1])
        and int(interval["source_row_start"]) == int(block["source_row"].iloc[0])
        and int(interval["source_row_end"]) == int(block["source_row"].iloc[-1])
    )


def _clock(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%H:%M:%S")


def _validate_source_columns(
    samples: pd.DataFrame, platforms: pd.DataFrame, config: dict[str, Any]
) -> None:
    required_samples = {
        "time",
        "source_row",
        "chainage",
        "cumulative_advance",
        "machine_state",
        "sample_dt_seconds",
        "hard_gap_before",
        config["platform_id_column"],
        config["phase_label_column"],
        *config["parameters"].keys(),
    }
    required_platforms = {"date", "platform_id", "start_time", "end_time"}
    missing_samples = sorted(required_samples.difference(samples.columns))
    missing_platforms = sorted(required_platforms.difference(platforms.columns))
    if missing_samples or missing_platforms:
        raise ValueError(
            f"Missing report source columns: samples={missing_samples}, platforms={missing_platforms}"
        )
