from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "configs" / "dynamic_parameters.json"


def load_dynamic_parameter_registry(path: Path | None = None) -> dict[str, dict[str, Any]]:
    registry_path = path or DEFAULT_REGISTRY_PATH
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError(f"Invalid dynamic parameter registry: {registry_path}")
    for name, definition in parameters.items():
        if not isinstance(definition, dict) or not definition.get("column"):
            raise ValueError(f"Dynamic parameter {name!r} has no source column")
    return parameters


def load_dynamic_parameter_columns(path: Path | None = None) -> dict[str, str]:
    return {
        name: str(definition["column"])
        for name, definition in load_dynamic_parameter_registry(path).items()
    }
