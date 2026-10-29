from __future__ import annotations

from typing import Any, TypedDict


class CompleteDailyResult(TypedDict):
    schema_version: str
    date: str
    source_file: str
    result_type: str
    overall: dict[str, Any]
    data_coverage: dict[str, Any]
    machine_states: dict[str, Any]
    excavation_cycles: list[dict[str, Any]]
    construction_response_events: list[dict[str, Any]]
    data_quality_events: list[dict[str, Any]]
    selection_metadata: dict[str, Any]
    method_limits: list[str]


class ResultContractError(ValueError):
    """Raised when a serialized fact package violates a layer boundary."""


COMPLETE_DAILY_RESULT_KEYS = frozenset(CompleteDailyResult.__required_keys__)


def validate_complete_daily_result(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ResultContractError("reporting layer requires a complete daily result object")
    missing = sorted(COMPLETE_DAILY_RESULT_KEYS - set(payload))
    if missing:
        raise ResultContractError(
            "complete daily result is missing required keys: " + ", ".join(missing)
        )
    if payload.get("result_type") != "complete_traceable_fact_package":
        raise ResultContractError(
            "reporting layer only accepts result_type=complete_traceable_fact_package"
        )
    if not isinstance(payload.get("excavation_cycles"), list):
        raise ResultContractError("excavation_cycles must be a list")

