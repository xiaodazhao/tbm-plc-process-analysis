from __future__ import annotations

import math
import re


def chainage(value, system="DyK"):
    if isinstance(value, (int, float)):
        result = float(value)
    elif re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", str(value)):
        result = float(value)
    else:
        match = re.fullmatch(r"\s*(DyK|DK)\s*(\d+)\s*\+\s*(\d+(?:\.\d+)?)\s*", str(value), re.I)
        if not match or match[1].lower() != system.lower():
            raise ValueError("Expected explicit " + system + " chainage; DK/DyK conversion is not established")
        if float(match[3]) >= 1000:
            raise ValueError("Chainage metre suffix must be less than 1000")
        result = int(match[2])*1000 + float(match[3])
    if not math.isfinite(result):
        raise ValueError("Chainage must be finite")
    return result


def format_chainage(value):
    km = math.floor(float(value)/1000)
    metres = float(value)-km*1000
    suffix = f"{metres:07.3f}".rstrip("0").rstrip(".")
    return f"DyK{km}+{suffix}"


def union(intervals):
    merged = []
    for lo, hi in sorted((float(a), float(b)) for a, b in intervals if b > a):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def coverage_partition(lower, upper, records):
    """Exact double-precision source boundaries, not a fixed spatial grid."""
    bounds = {lower, upper}
    for r in records:
        if r["start_m"] < upper and r["end_m"] > lower:
            bounds.update((max(lower, r["start_m"]), min(upper, r["end_m"])))
    result = []
    for lo, hi in zip(sorted(bounds), sorted(bounds)[1:]):
        if hi <= lo:
            continue
        active = [r for r in records if r["start_m"] < hi and r["end_m"] > lo]
        kinds = sorted({r["document_type"] for r in active})
        category = "both" if {"TSP", "HSP"} <= set(kinds) else "tsp_only" if "TSP" in kinds else "hsp_only" if "HSP" in kinds else "gap"
        result.append({"start_m": lo, "end_m": hi, "length_m": hi-lo, "category": category,
                       "source_types": kinds, "fact_ids": sorted({r["fact_id"] for r in active}),
                       "document_ids": sorted({r["document_id"] for r in active})})
    return result
