"""Machine-state, excavation-cycle, and frozen four-phase recognition."""

from .segmentation import (
    PHASE_LABELS,
    classify_machine_state,
    extract_advance_intervals,
    segment_interval_phases,
)
from .scopes import block_duration, build_analysis_scopes

__all__ = [
    "PHASE_LABELS",
    "block_duration",
    "build_analysis_scopes",
    "classify_machine_state",
    "extract_advance_intervals",
    "segment_interval_phases",
]

