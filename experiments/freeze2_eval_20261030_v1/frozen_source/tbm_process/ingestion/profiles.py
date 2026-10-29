from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIELD_PROFILE_PATH = PROJECT_ROOT / "configs" / "field_profiles" / "tbm9_2023.json"


def load_field_profile(path: Path | None = None) -> dict[str, Any]:
    profile_path = path or DEFAULT_FIELD_PROFILE_PATH
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases")
    if not isinstance(aliases, dict) or "time" not in aliases:
        raise ValueError(f"Invalid TBM field profile: {profile_path}")
    return payload


def load_field_aliases(path: Path | None = None) -> dict[str, list[str]]:
    profile = load_field_profile(path)
    return {
        str(name): [str(alias) for alias in aliases]
        for name, aliases in profile["aliases"].items()
    }


def load_guidance_chainage_mapping(path: Path | None = None) -> dict[str, Any] | None:
    profile = load_field_profile(path)
    mapping = profile.get("guidance_chainage_mapping")
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        raise ValueError("guidance_chainage_mapping must be a JSON object")
    return mapping
