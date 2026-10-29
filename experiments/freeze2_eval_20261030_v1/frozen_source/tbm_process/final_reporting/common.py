from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any


CHAINAGE_RE = re.compile(r"(?i)D?y?K\s*(\d+)\s*\+\s*(\d+(?:\.\d+)?)")
CHAINAGE_RANGE_RE = re.compile(
    r"(?i)(D?y?K\s*\d+\s*\+\s*\d+(?:\.\d+)?)\s*[~～—-]\s*"
    r"(D?y?K\s*\d+\s*\+\s*\d+(?:\.\d+)?)"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def load_deepseek_api_key(env_file: Path | None = None) -> str | None:
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value
    if env_file is None or not env_file.is_file():
        return None
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "DEEPSEEK_API_KEY":
            return value.strip().strip('"').strip("'") or None
    return None


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def parse_chainage(value: Any) -> float | None:
    match = CHAINAGE_RE.search(str(value or ""))
    if not match:
        return None
    return float(match.group(1)) * 1000.0 + float(match.group(2))


def format_chainage(value: float | int | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "里程待核实"
    number = float(value)
    km = int(number // 1000)
    metres = number - km * 1000
    if abs(metres - round(metres)) < 1e-9:
        metre_text = f"{int(round(metres)):03d}"
    else:
        metre_text = f"{metres:06.2f}".rstrip("0").rstrip(".")
    return f"DyK{km}+{metre_text}"


def suspicious_chainage_ranges(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in CHAINAGE_RANGE_RE.finditer(text or ""):
        start_text, end_text = match.groups()
        start = parse_chainage(start_text)
        end = parse_chainage(end_text)
        if start is None or end is None:
            continue
        if end < start:
            rows.append(
                {
                    "text": match.group(0),
                    "start_m": start,
                    "end_m": end,
                    "reason": "range_end_precedes_range_start",
                }
            )
    return rows


def redact_suspicious_chainage_ranges(text: str) -> tuple[str, list[dict[str, Any]]]:
    findings = suspicious_chainage_ranges(text)
    cleaned = text
    for finding in findings:
        cleaned = cleaned.replace(
            finding["text"], "【原报告里程范围待核实，未用于空间判断】"
        )
    return cleaned, findings


def overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def clock(iso_value: str) -> str:
    return iso_value[11:19]
