from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


class DataContractError(ValueError):
    """Raised when one analysis layer receives an invalid upstream payload."""


CANONICAL_SAMPLE_COLUMNS = frozenset(
    {
        "time",
        "source_file",
        "source_row",
        "raw_state",
        "chainage",
        "cumulative_advance",
        "advance_speed_raw",
        "advance_speed_set",
        "thrust",
        "torque",
        "rpm_actual_raw",
        "rpm_alternate_raw",
        "motor_frequency_m1",
        "penetration_raw",
    }
)

QUALITY_CONTROLLED_SAMPLE_COLUMNS = CANONICAL_SAMPLE_COLUMNS | frozenset(
    {
        "advance_speed",
        "advance_speed_quality",
        "rpm_canonical",
        "rpm_quality",
        "penetration_analysis",
        "penetration_quality",
        "sample_dt_seconds",
        "hard_gap_before",
        "continuity_block_id",
    }
)

PROCESS_SAMPLE_COLUMNS = QUALITY_CONTROLLED_SAMPLE_COLUMNS | frozenset(
    {
        "machine_state",
        "advance_candidate",
        "valid_advance_interval",
        "interval_id",
        "phase_id",
        "phase_label",
        "analysis_scope_id",
        "analysis_scope_label",
    }
)


def validate_canonical_samples(frame: pd.DataFrame) -> None:
    _require_columns(frame, CANONICAL_SAMPLE_COLUMNS, layer="ingestion")


def validate_quality_controlled_samples(frame: pd.DataFrame) -> None:
    _require_columns(frame, QUALITY_CONTROLLED_SAMPLE_COLUMNS, layer="quality")


def validate_process_samples(frame: pd.DataFrame) -> None:
    _require_columns(frame, PROCESS_SAMPLE_COLUMNS, layer="process")


def _require_columns(frame: pd.DataFrame, required: Iterable[str], *, layer: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise DataContractError(f"{layer} layer must return a pandas DataFrame")
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise DataContractError(
            f"{layer} layer output is missing required columns: {', '.join(missing)}"
        )

