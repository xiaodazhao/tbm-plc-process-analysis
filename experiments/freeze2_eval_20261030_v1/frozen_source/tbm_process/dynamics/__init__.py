"""Within-scope parameter trends, fluctuations, transitions, and coupling."""

from .descriptors import analyze_phases, trend_descriptor
from .semantics import analyze_phase_semantics, select_semantic_facts

__all__ = [
    "analyze_phase_semantics",
    "analyze_phases",
    "select_semantic_facts",
    "trend_descriptor",
]

