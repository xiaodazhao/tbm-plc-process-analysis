"""Thin orchestration for daily and whole-dataset execution."""

from .pipeline import analyze_day, run_dataset

__all__ = ["analyze_day", "run_dataset"]

