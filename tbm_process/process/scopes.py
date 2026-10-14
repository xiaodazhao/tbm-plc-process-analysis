from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..ingestion.io import iso, safe_number


def build_analysis_scopes(
    block: pd.DataFrame,
    *,
    labels: pd.Series,
    phase_rows: list[dict[str, Any]],
    interval: dict[str, Any],
    date: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not phase_rows:
        return [
            _analysis_scope_row(
                block,
                date=date,
                interval_id=interval["interval_id"],
                scope_id=f"{interval['interval_id']}-U00",
                label="unsegmented_cycle",
                config=config,
                dynamic_only=True,
            )
        ]

    scopes = [{**phase, "dynamic_analysis_scope_only": False} for phase in phase_rows]
    unidentified_number = 0
    missing = labels.isna().to_numpy(dtype=bool)
    for start, end in _array_true_runs(missing):
        unidentified_number += 1
        portion = block.iloc[start : end + 1]
        scopes.append(
            _analysis_scope_row(
                portion,
                date=date,
                interval_id=interval["interval_id"],
                scope_id=f"{interval['interval_id']}-U{unidentified_number:02d}",
                label="unidentified_process",
                config=config,
                dynamic_only=True,
            )
        )
    return sorted(scopes, key=lambda row: row["start_time"])


def block_duration(block: pd.DataFrame, config: dict[str, Any]) -> float:
    if block.empty:
        return 0.0
    dt = block["time"].shift(-1).sub(block["time"]).dt.total_seconds().clip(
        lower=0,
        upper=float(config["maximum_sample_duration_seconds"]),
    )
    if "hard_gap_before" in block:
        crosses_hard_gap = block["hard_gap_before"].shift(-1, fill_value=False).astype(bool)
        dt = dt.mask(crosses_hard_gap, 0.0)
    return float(dt.fillna(0).sum())


def _analysis_scope_row(
    block: pd.DataFrame,
    *,
    date: str,
    interval_id: str,
    scope_id: str,
    label: str,
    config: dict[str, Any],
    dynamic_only: bool,
) -> dict[str, Any]:
    return {
        "date": date,
        "interval_id": interval_id,
        "phase_id": scope_id,
        "phase_label": label,
        "start_time": iso(block["time"].iloc[0]),
        "end_time": iso(block["time"].iloc[-1]),
        "duration_seconds": safe_number(block_duration(block, config)),
        "sample_count": int(len(block)),
        "dynamic_analysis_scope_only": dynamic_only,
        "source_file": str(block["source_file"].iloc[0]),
        "source_row_start": int(block["source_row"].iloc[0]),
        "source_row_end": int(block["source_row"].iloc[-1]),
    }


def _array_true_runs(values: np.ndarray) -> list[tuple[int, int]]:
    mask = np.asarray(values, dtype=bool)
    if not mask.any():
        return []
    starts = np.flatnonzero(mask & np.r_[True, ~mask[:-1]])
    ends = np.flatnonzero(mask & np.r_[~mask[1:], True])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))

