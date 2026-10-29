from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


MAPPING_VERSION = "tbm9_guidance_chainage_alignment_v1_2026-10-17"


def normalize_guidance_chainage(
    frame: pd.DataFrame,
    *,
    direct_chainage: pd.Series,
    direct_source_field: str | None,
    config: dict[str, Any] | None,
) -> pd.DataFrame:
    """Return traceable, row-level TBM guidance-chainage alignment results.

    The TBM9 exports contain three observed layouts inside the navigation block.
    This function never edits the raw frame. It selects the head/middle pair only
    when exactly one configured layout satisfies the project chainage range and
    physical head-to-middle spacing constraints.
    """
    result = pd.DataFrame(index=frame.index)
    result["chainage_raw"] = pd.to_numeric(direct_chainage, errors="coerce")
    result["chainage_middle_canonical"] = np.nan
    result["chainage_mapping_mode"] = "direct_source_unverified"
    result["chainage_source_field"] = direct_source_field
    result["chainage_middle_source_field"] = None
    result["chainage_quality"] = "usable_direct_source_unverified"
    result["chainage_mapping_version"] = MAPPING_VERSION

    if not config or not bool(config.get("enabled", False)):
        result["chainage"] = result["chainage_raw"]
        return result

    fields = config.get("fields", {})
    resolved = {
        name: _resolve_exact_column(frame, fields.get(name))
        for name in ("roll", "head", "middle", "tail")
    }
    available_layouts = {
        "aligned": resolved["head"] is not None and resolved["middle"] is not None,
        "shifted_left": resolved["roll"] is not None and resolved["head"] is not None,
        "shifted_right": resolved["middle"] is not None and resolved["tail"] is not None,
    }
    if not any(available_layouts.values()):
        result["chainage"] = result["chainage_raw"]
        result["chainage_quality"] = "usable_direct_source_mapping_fields_unavailable"
        return result

    values = {
        name: pd.to_numeric(frame[column], errors="coerce")
        for name, column in resolved.items()
        if column is not None
    }
    minimum = float(config["valid_chainage_min"])
    maximum = float(config["valid_chainage_max"])
    spacing_minimum = float(config["head_middle_spacing_min_m"])
    spacing_maximum = float(config["head_middle_spacing_max_m"])

    layout = {
        "aligned": ("head", "middle"),
        "shifted_left": ("roll", "head"),
        "shifted_right": ("middle", "tail"),
    }
    candidates = {}
    for name, (head_key, middle_key) in layout.items():
        if available_layouts[name]:
            candidates[name] = _plausible_pair(
                values[head_key],
                values[middle_key],
                minimum=minimum,
                maximum=maximum,
                spacing_minimum=spacing_minimum,
                spacing_maximum=spacing_maximum,
            )
        else:
            candidates[name] = pd.Series(False, index=frame.index, dtype=bool)
    candidate_count = sum(mask.astype("int8") for mask in candidates.values())
    unique = candidate_count.eq(1)

    head = pd.Series(np.nan, index=frame.index, dtype=float)
    middle = pd.Series(np.nan, index=frame.index, dtype=float)
    mode = pd.Series("unavailable", index=frame.index, dtype=object)
    head_source = pd.Series(None, index=frame.index, dtype=object)
    middle_source = pd.Series(None, index=frame.index, dtype=object)

    for name, (head_key, middle_key) in layout.items():
        selected = unique & candidates[name]
        if not available_layouts[name]:
            continue
        head.loc[selected] = values[head_key].loc[selected]
        middle.loc[selected] = values[middle_key].loc[selected]
        mode.loc[selected] = name
        head_source.loc[selected] = resolved[head_key]
        middle_source.loc[selected] = resolved[middle_key]

    ambiguous = candidate_count.gt(1)
    quality = pd.Series("unavailable_mapping", index=frame.index, dtype=object)
    quality.loc[mode.eq("aligned")] = "usable_aligned_mapping"
    quality.loc[mode.isin(["shifted_left", "shifted_right"])] = (
        "usable_recovered_mapping"
    )
    quality.loc[ambiguous] = "unavailable_ambiguous_mapping"
    mode.loc[ambiguous] = "ambiguous"

    result["chainage"] = head
    result["chainage_middle_canonical"] = middle
    result["chainage_mapping_mode"] = mode
    result["chainage_source_field"] = head_source
    result["chainage_middle_source_field"] = middle_source
    result["chainage_quality"] = quality
    return result


def annotate_chainage_steps(
    frame: pd.DataFrame,
    *,
    config: dict[str, Any] | None,
) -> pd.DataFrame:
    """Add short-gap step evidence without changing canonical chainage values."""
    out = frame.copy()
    out["chainage_canonical_candidate"] = pd.to_numeric(
        out["chainage"], errors="coerce"
    )
    out["chainage_step_gap_seconds"] = out["time"].diff().dt.total_seconds()
    out["chainage_step_delta_m"] = out["chainage_canonical_candidate"].diff()
    quality = pd.Series("usable", index=out.index, dtype=object)
    unavailable = out["chainage_canonical_candidate"].isna()
    previous_unavailable = out["chainage_canonical_candidate"].shift().isna()
    quality.loc[unavailable | previous_unavailable] = "not_assessed_missing_chainage"
    if len(quality):
        quality.iloc[0] = "not_assessed_no_previous_sample"

    if config:
        maximum_gap = float(config.get("maximum_short_gap_seconds", 120.0))
        maximum_step = float(config.get("maximum_short_gap_step_m", 30.0))
        across_gap = out["chainage_step_gap_seconds"].gt(maximum_gap)
        quality.loc[across_gap & ~unavailable] = "not_assessed_across_sampling_gap"
        suspicious = (
            out["chainage_step_gap_seconds"].between(0, maximum_gap, inclusive="both")
            & out["chainage_step_delta_m"].abs().gt(maximum_step)
            & ~unavailable
            & ~previous_unavailable
        )
        quality.loc[suspicious] = "suspicious_short_gap_jump"
    out["chainage_step_quality"] = quality
    return out


def _plausible_pair(
    head: pd.Series,
    middle: pd.Series,
    *,
    minimum: float,
    maximum: float,
    spacing_minimum: float,
    spacing_maximum: float,
) -> pd.Series:
    spacing = head - middle
    return (
        head.between(minimum, maximum, inclusive="both")
        & middle.between(minimum, maximum, inclusive="both")
        & spacing.between(spacing_minimum, spacing_maximum, inclusive="both")
    ).fillna(False)


def _resolve_exact_column(frame: pd.DataFrame, configured: Any) -> str | None:
    if not configured:
        return None
    names = [str(configured)] if isinstance(configured, str) else [str(x) for x in configured]
    exact = {str(column).strip().lower(): str(column) for column in frame.columns}
    for name in names:
        match = exact.get(name.strip().lower())
        if match is not None:
            return match
    return None
