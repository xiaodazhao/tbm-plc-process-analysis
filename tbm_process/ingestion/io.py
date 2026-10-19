from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .profiles import load_field_aliases
from .chainage import annotate_chainage_steps, normalize_guidance_chainage


FILE_RE = re.compile(r"tbm_data_(\d{8})\.(csv|xlsx|xls)$", re.IGNORECASE)

ALIASES = load_field_aliases()


def discover_files(plc_dir: Path) -> list[Path]:
    if not plc_dir.is_dir():
        return []
    return sorted(
        path for path in plc_dir.iterdir() if path.is_file() and FILE_RE.match(path.name)
    )


def read_daily_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return pd.read_csv(path, encoding=encoding, low_memory=False)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error:
            raise last_error
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported PLC file: {path}")


def canonicalize(
    frame: pd.DataFrame,
    *,
    source_file: Path,
    field_aliases: dict[str, list[str]] | None = None,
    guidance_chainage_mapping: dict[str, Any] | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    resolved: dict[str, str | None] = {}
    aliases_by_field = field_aliases or ALIASES
    for canonical, aliases in aliases_by_field.items():
        raw = find_column(frame, aliases)
        resolved[canonical] = raw
        if canonical == "time":
            out[canonical] = (
                pd.to_datetime(frame[raw], errors="coerce", format="mixed")
                if raw
                else pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
            )
        else:
            out[canonical] = (
                pd.to_numeric(frame[raw], errors="coerce")
                if raw
                else pd.Series(np.nan, index=frame.index, dtype=float)
            )
    out["source_file"] = source_file.name
    out["source_row"] = np.arange(len(frame), dtype=int) + 2
    chainage = normalize_guidance_chainage(
        frame,
        direct_chainage=out["chainage"],
        direct_source_field=resolved.get("chainage"),
        config=guidance_chainage_mapping,
    )
    for column in chainage.columns:
        out[column] = chainage[column]
    out.attrs["resolved_columns"] = resolved
    out.attrs["guidance_chainage_mapping"] = guidance_chainage_mapping
    sorted_out = out.sort_values("time", kind="stable").reset_index(drop=True)
    sorted_out = annotate_chainage_steps(
        sorted_out,
        config=guidance_chainage_mapping,
    )
    sorted_out.attrs.update(out.attrs)
    return sorted_out


def find_column(frame: pd.DataFrame, aliases: list[str]) -> str | None:
    columns = [str(col) for col in frame.columns]
    lower = {col.strip().lower(): col for col in columns}
    for alias in aliases:
        if alias.lower() in lower:
            return lower[alias.lower()]
    for alias in aliases:
        for column in columns:
            if alias.lower() in column.lower():
                return column
    return None


def safe_number(value: Any) -> float | int | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return iso(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return safe_number(value)
    if value is pd.NA:
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

