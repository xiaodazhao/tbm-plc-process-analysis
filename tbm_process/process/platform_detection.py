from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tbm_process.ingestion.io import iso, safe_number
from tbm_process.process.segmentation import parameter_summary, segment_interval_phases


EXPERIMENT_METHOD = "tbm_platform_first_experiment_v1"


def analyze_platform_first_day(
    frame: pd.DataFrame,
    *,
    date: str,
    experiment_config: dict[str, Any],
    phase_config: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Detect broad operating platforms, then reuse the frozen four-phase segmenter."""
    working = _prepare_frame(frame)
    smoothed, votes = _activity_votes(working, experiment_config)
    core = votes.ge(int(experiment_config["core_required_active_signals"]))
    core = _bridge_short_internal_gaps(
        working,
        core,
        max_seconds=float(experiment_config["bridge_internal_gap_seconds"]),
    )
    core_runs = [
        run
        for run in _bounded_runs(working, core)
        if _run_qualifies(
            working,
            run,
            minimum_seconds=float(experiment_config["minimum_core_seconds"]),
            minimum_samples=int(experiment_config["minimum_core_samples"]),
        )
    ]
    support = votes.ge(int(experiment_config["support_required_active_signals"]))
    support |= pd.to_numeric(working["raw_state"], errors="coerce").fillna(0).ne(0)
    episodes = _expand_and_resolve(
        working,
        core_runs,
        support,
        maximum_seconds=float(experiment_config["boundary_extension_seconds"]),
    )
    episodes = [
        episode
        for episode in episodes
        if _duration_seconds(working, episode[0], episode[1])
        >= float(experiment_config["minimum_episode_seconds"])
    ]

    assignments = working.copy()
    assignments["experimental_activity_votes"] = votes.astype(int)
    assignments["experimental_platform_core"] = core.astype(bool)
    assignments["experimental_platform_id"] = None
    assignments["experimental_phase_label"] = None
    assignments["experimental_phase_id"] = None
    assignments["experimental_phase_evidence_type"] = None

    platform_rows: list[dict[str, Any]] = []
    all_phase_rows: list[dict[str, Any]] = []
    for number, (start, end, core_start, core_end) in enumerate(episodes, start=1):
        platform_id = f"{date}-E{number:03d}"
        block = working.iloc[start : end + 1].copy()
        phase_block = block.copy()
        phase_block["raw_state_original"] = phase_block["raw_state"]
        phase_block["raw_state"] = 1.0
        active_positions = votes.iloc[start : end + 1].ge(
            int(experiment_config["core_required_active_signals"])
        )
        phase_block["machine_state"] = np.where(
            active_positions.to_numpy(dtype=bool),
            "advancing",
            "platform_transition",
        )
        terminal_stop_time = _next_sample_time(working, end, phase_config)
        labels, phase_rows, diagnostics, candidates = segment_interval_phases(
            phase_block,
            interval_id=platform_id,
            config=phase_config,
            terminal_stop_time=terminal_stop_time,
        )
        termination = _classify_termination_observation(
            working,
            end=end,
            phase_rows=phase_rows,
            diagnostics=diagnostics,
            phase_config=phase_config,
        )
        for phase in phase_rows:
            if phase["phase_label"] == "declining":
                phase["decline_observation_type"] = termination["observation_type"]
                phase["dynamic_analysis_eligible"] = bool(
                    termination["eligible_for_dynamic_analysis"]
                )
            else:
                phase["dynamic_analysis_eligible"] = True

        assignments.loc[block.index, "experimental_platform_id"] = platform_id
        assignments.loc[labels.index, "experimental_phase_label"] = labels
        for phase in phase_rows:
            phase_mask = labels.eq(phase["phase_label"]) & phase_block["source_row"].between(
                int(phase["source_row_start"]), int(phase["source_row_end"])
            )
            assignments.loc[labels.index[phase_mask], "experimental_phase_id"] = phase["phase_id"]
            if phase["phase_label"] == "declining":
                assignments.loc[
                    labels.index[phase_mask], "experimental_phase_evidence_type"
                ] = termination["observation_type"]

        frozen_ids = sorted(
            set(
                block.loc[
                    _formal_interval_mask(block) & block["interval_id"].notna(),
                    "interval_id",
                ].astype(str)
            )
        )
        raw_zero = pd.to_numeric(block["raw_state"], errors="coerce").fillna(0).eq(0)
        platform_rows.append(
            {
                "date": date,
                "platform_id": platform_id,
                "method": EXPERIMENT_METHOD,
                "start_index": int(start),
                "end_index": int(end),
                "core_start_index": int(core_start),
                "core_end_index": int(core_end),
                "start_time": iso(block["time"].iloc[0]),
                "end_time": iso(block["time"].iloc[-1]),
                "core_start_time": iso(working["time"].iloc[core_start]),
                "core_end_time": iso(working["time"].iloc[core_end]),
                "duration_seconds": safe_number(_duration_seconds(working, start, end)),
                "core_duration_seconds": safe_number(
                    _duration_seconds(working, core_start, core_end)
                ),
                "sample_count": int(len(block)),
                "raw_state_zero_sample_count": int(raw_zero.sum()),
                "raw_state_zero_sample_ratio": safe_number(raw_zero.mean()),
                "frozen_interval_count_inside": int(len(frozen_ids)),
                "frozen_interval_ids_inside": frozen_ids,
                "segmentation_status": diagnostics["segmentation_status"],
                "identified_phase_count": int(len(phase_rows)),
                "stable_phase_exists": any(
                    row["phase_label"] == "stable" for row in phase_rows
                ),
                "no_stable_reasons": diagnostics.get("no_stable_reasons", []),
                "termination_observation": termination,
                "source_file": str(block["source_file"].iloc[0]),
                "source_row_start": int(block["source_row"].iloc[0]),
                "source_row_end": int(block["source_row"].iloc[-1]),
                "chainage_start": _edge_number(block["chainage"], first=True),
                "chainage_end": _edge_number(block["chainage"], first=False),
                "phase_candidates": candidates,
                "phases": phase_rows,
                **parameter_summary(block),
            }
        )
        all_phase_rows.extend(phase_rows)

    frozen_interval_ids = sorted(
        set(
            working.loc[
                _formal_interval_mask(working) & working["interval_id"].notna(),
                "interval_id",
            ].astype(str)
        )
    )
    summary = {
        "date": date,
        "method": EXPERIMENT_METHOD,
        "status": "experimental_not_frozen",
        "source": "frozen_quality_controlled_processed_samples",
        "frozen_outputs_modified": False,
        "record_count": int(len(working)),
        "frozen_interval_count": int(len(frozen_interval_ids)),
        "experimental_platform_count": int(len(platform_rows)),
        "platforms_with_stable_phase": int(
            sum(row["stable_phase_exists"] for row in platform_rows)
        ),
        "platforms_without_stable_phase": int(
            sum(not row["stable_phase_exists"] for row in platform_rows)
        ),
        "frozen_intervals_grouped_into_platforms": int(
            sum(row["frozen_interval_count_inside"] for row in platform_rows)
        ),
        "raw_state_zero_samples_inside_platforms": int(
            sum(row["raw_state_zero_sample_count"] for row in platform_rows)
        ),
        "termination_observation_counts": _count_termination_observations(platform_rows),
    }
    payload = {
        "schema_version": EXPERIMENT_METHOD,
        "summary": summary,
        "rules": experiment_config,
        "platforms": platform_rows,
        "phases": all_phase_rows,
        "interpretation_limits": [
            "Experimental platforms are algorithmic operating episodes, not verified excavation cycles.",
            "The original excavation-state field is preserved but is not a hard experimental boundary.",
            "Four-phase thresholds are reused from the frozen method after platform detection.",
            "A one-sample terminal boundary is retained for traceability but is not treated as a sustained declining process.",
            "Termination during a sampling gap or after the final daily record is not inferred.",
            "No frozen source file or frozen output is modified by this experiment.",
        ],
    }
    return payload, assignments


def _classify_termination_observation(
    frame: pd.DataFrame,
    *,
    end: int,
    phase_rows: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    phase_config: dict[str, Any],
) -> dict[str, Any]:
    stable_exists = any(row["phase_label"] == "stable" for row in phase_rows)
    if not stable_exists:
        return {
            "observation_type": "not_applicable_no_reliable_stable_phase",
            "eligible_for_dynamic_analysis": False,
            "decline_sample_count": 0,
            "decline_duration_seconds": None,
            "evidence": "No reliable stable phase was identified, so a terminal decline is not asserted.",
        }

    decline_rows = [row for row in phase_rows if row["phase_label"] == "declining"]
    if decline_rows:
        sample_count = sum(int(row.get("sample_count") or 0) for row in decline_rows)
        duration = sum(float(row.get("duration_seconds") or 0.0) for row in decline_rows)
        instantaneous = any(
            row.get("phase_review_warning")
            == "instantaneous_decline_inferred_from_state_transition_no_ramp_samples"
            for row in decline_rows
        )
        observation_type = (
            "instantaneous_stop_boundary"
            if instantaneous
            else "observed_declining_phase"
        )
        return {
            "observation_type": observation_type,
            "eligible_for_dynamic_analysis": not instantaneous,
            "decline_sample_count": sample_count,
            "decline_duration_seconds": safe_number(duration),
            "start_time": decline_rows[0].get("start_time"),
            "end_time": decline_rows[-1].get("end_time"),
            "source_row_start": decline_rows[0].get("source_row_start"),
            "source_row_end": decline_rows[-1].get("source_row_end"),
            "terminal_stop_time": diagnostics.get("terminal_stop_time"),
            "evidence": (
                "A sustained multi-sample terminal decrease was observed."
                if not instantaneous
                else "The next observed sample indicates stopping, but no multi-sample decline ramp was recorded."
            ),
        }

    last = frame.iloc[end]
    base = {
        "eligible_for_dynamic_analysis": False,
        "decline_sample_count": 0,
        "decline_duration_seconds": None,
        "last_observed_time": iso(last["time"]),
        "last_observed_source_row": int(last["source_row"]),
    }
    if end + 1 >= len(frame):
        return {
            **base,
            "observation_type": "termination_unobserved_recording_end",
            "evidence": "The daily record ended while the platform was still loaded; no terminal decline was observed.",
        }

    following = frame.iloc[end + 1]
    gap_seconds = float((following["time"] - last["time"]).total_seconds())
    same_block = following["continuity_block_id"] == last["continuity_block_id"]
    hard_gap = bool(following.get("hard_gap_before", False)) or not same_block
    maximum_gap = float(phase_config.get("terminal_stop_max_gap_seconds", 120.0))
    if hard_gap or gap_seconds > maximum_gap:
        return {
            **base,
            "observation_type": "termination_unobserved_sampling_gap",
            "next_observed_time": iso(following["time"]),
            "next_observed_source_row": int(following["source_row"]),
            "gap_seconds": safe_number(gap_seconds),
            "evidence": "The platform termination falls inside a sampling gap and is not reconstructed.",
        }
    return {
        **base,
        "observation_type": "no_terminal_decline_evidence",
        "next_observed_time": iso(following["time"]),
        "next_observed_source_row": int(following["source_row"]),
        "gap_seconds": safe_number(gap_seconds),
        "evidence": "No reliable terminal decline met the frozen four-phase evidence rule.",
    }


def _count_termination_observations(
    platform_rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in platform_rows:
        key = str(row["termination_observation"]["observation_type"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "time",
        "source_file",
        "source_row",
        "raw_state",
        "advance_speed",
        "thrust",
        "torque",
        "rpm_canonical",
        "penetration_analysis",
        "penetration_analysis_source",
        "chainage",
        "hard_gap_before",
        "continuity_block_id",
        "valid_advance_interval",
        "interval_id",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Processed samples missing columns: {missing}")
    working = frame.copy()
    working["time"] = pd.to_datetime(working["time"], errors="raise")
    return working.sort_values(["time", "source_row"], kind="stable").reset_index(drop=True)


def _activity_votes(
    frame: pd.DataFrame, config: dict[str, Any]
) -> tuple[pd.DataFrame, pd.Series]:
    smoothed = pd.DataFrame(index=frame.index)
    votes = pd.Series(0, index=frame.index, dtype=int)
    for _, positions in frame.groupby("continuity_block_id", sort=False).groups.items():
        block = frame.loc[positions]
        index = pd.DatetimeIndex(block["time"])
        for column, floor in config["activity_columns"].items():
            values = pd.to_numeric(block[column], errors="coerce")
            rolling = pd.Series(values.to_numpy(dtype=float), index=index).rolling(
                f"{max(1, int(float(config['smoothing_seconds'])))}s",
                center=True,
                min_periods=1,
            ).median()
            smoothed.loc[positions, column] = rolling.to_numpy()
            votes.loc[positions] += rolling.gt(float(floor)).to_numpy(dtype=int)
    return smoothed, votes


def _bridge_short_internal_gaps(
    frame: pd.DataFrame, mask: pd.Series, *, max_seconds: float
) -> pd.Series:
    result = mask.fillna(False).astype(bool).copy()
    for _, positions in frame.groupby("continuity_block_id", sort=False).groups.items():
        positions = np.asarray(list(positions), dtype=int)
        local = result.iloc[positions].to_numpy(dtype=bool)
        for start, end in _array_runs(~local):
            if start == 0 or end == len(local) - 1:
                continue
            if not local[start - 1] or not local[end + 1]:
                continue
            global_start = int(positions[start])
            global_end = int(positions[end])
            if _duration_seconds(frame, global_start, global_end) <= max_seconds:
                result.iloc[positions[start : end + 1]] = True
    return result


def _bounded_runs(frame: pd.DataFrame, mask: pd.Series) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for _, positions in frame.groupby("continuity_block_id", sort=False).groups.items():
        positions = np.asarray(list(positions), dtype=int)
        local = mask.iloc[positions].fillna(False).to_numpy(dtype=bool)
        for start, end in _array_runs(local):
            runs.append((int(positions[start]), int(positions[end])))
    return runs


def _run_qualifies(
    frame: pd.DataFrame,
    run: tuple[int, int],
    *,
    minimum_seconds: float,
    minimum_samples: int,
) -> bool:
    start, end = run
    return bool(
        end - start + 1 >= minimum_samples
        and _duration_seconds(frame, start, end) >= minimum_seconds
    )


def _expand_and_resolve(
    frame: pd.DataFrame,
    core_runs: list[tuple[int, int]],
    support: pd.Series,
    *,
    maximum_seconds: float,
) -> list[tuple[int, int, int, int]]:
    expanded: list[list[int]] = []
    hard = frame["hard_gap_before"].fillna(False).to_numpy(dtype=bool)
    blocks = frame["continuity_block_id"].to_numpy()
    for core_start, core_end in core_runs:
        start = core_start
        while start > 0:
            candidate = start - 1
            if blocks[candidate] != blocks[core_start] or hard[start]:
                break
            if not bool(support.iloc[candidate]):
                break
            if (frame["time"].iloc[core_start] - frame["time"].iloc[candidate]).total_seconds() > maximum_seconds:
                break
            start = candidate
        end = core_end
        while end + 1 < len(frame):
            candidate = end + 1
            if blocks[candidate] != blocks[core_end] or hard[candidate]:
                break
            if not bool(support.iloc[candidate]):
                break
            if (frame["time"].iloc[candidate] - frame["time"].iloc[core_end]).total_seconds() > maximum_seconds:
                break
            end = candidate
        expanded.append([start, end, core_start, core_end])

    for index in range(1, len(expanded)):
        previous = expanded[index - 1]
        current = expanded[index]
        if current[0] > previous[1]:
            continue
        split = (previous[2 + 1] + current[2]) // 2
        previous[1] = max(previous[2 + 1], split)
        current[0] = min(current[2], split + 1)
    return [tuple(row) for row in expanded]


def _next_sample_time(
    frame: pd.DataFrame, end: int, phase_config: dict[str, Any]
) -> pd.Timestamp | None:
    if end + 1 >= len(frame):
        return None
    if frame["continuity_block_id"].iloc[end + 1] != frame["continuity_block_id"].iloc[end]:
        return None
    gap = (frame["time"].iloc[end + 1] - frame["time"].iloc[end]).total_seconds()
    if gap < 0 or gap > float(phase_config.get("terminal_stop_max_gap_seconds", 120.0)):
        return None
    return pd.Timestamp(frame["time"].iloc[end + 1])


def _duration_seconds(frame: pd.DataFrame, start: int, end: int) -> float:
    if end < start:
        return 0.0
    observed = (frame["time"].iloc[end] - frame["time"].iloc[start]).total_seconds()
    differences = frame["time"].iloc[start : end + 1].diff().dt.total_seconds().dropna()
    tail = float(differences.median()) if not differences.empty else 0.0
    return float(observed + max(tail, 0.0))


def _array_runs(values: np.ndarray) -> list[tuple[int, int]]:
    mask = np.asarray(values, dtype=bool)
    if not mask.any():
        return []
    starts = np.flatnonzero(mask & np.r_[True, ~mask[:-1]])
    ends = np.flatnonzero(mask & np.r_[~mask[1:], True])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _formal_interval_mask(frame: pd.DataFrame) -> pd.Series:
    values = frame["valid_advance_interval"]
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().eq("true")


def _edge_number(values: pd.Series, *, first: bool) -> float | None:
    valid = pd.to_numeric(values, errors="coerce").dropna()
    if valid.empty:
        return None
    return safe_number(valid.iloc[0] if first else valid.iloc[-1])
