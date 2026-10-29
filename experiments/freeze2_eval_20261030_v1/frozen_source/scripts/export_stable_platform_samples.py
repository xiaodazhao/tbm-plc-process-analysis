#!/usr/bin/env python3
"""Detect operating platforms and export only stable excavation samples.

The input must be the project's quality-controlled ``processed_samples`` files,
not raw PLC exports.  Platform boundaries are detected first with the current
platform-first method.  The frozen four-phase segmenter is then applied inside
each platform, but only rows labelled ``stable`` are written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.config import layer_config, load_config
from tbm_process.process.platform_detection import analyze_platform_first_day


DEFAULT_PLATFORM_CONFIG = PROJECT_ROOT / "configs" / "platform_analysis.json"
DEFAULT_PHASE_CONFIG = PROJECT_ROOT / "config.json"
DATE_PATTERN = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing quality-controlled YYYY-MM-DD.csv or .csv.gz files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New or empty output directory.",
    )
    parser.add_argument(
        "--dates",
        nargs="*",
        help="Optional YYYY-MM-DD dates. If omitted, all CSV files are processed.",
    )
    parser.add_argument(
        "--platform-config",
        type=Path,
        default=DEFAULT_PLATFORM_CONFIG,
    )
    parser.add_argument(
        "--phase-config",
        type=Path,
        default=DEFAULT_PHASE_CONFIG,
    )
    return parser.parse_args()


def discover_inputs(input_dir: Path, dates: list[str] | None) -> list[Path]:
    if not input_dir.is_dir():
        raise FileNotFoundError(input_dir)
    selected = set(dates or [])
    paths = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and (path.name.endswith(".csv") or path.name.endswith(".csv.gz"))
    )
    if selected:
        paths = [path for path in paths if _date_from_name(path) in selected]
        missing = sorted(selected - {_date_from_name(path) for path in paths})
        if missing:
            raise FileNotFoundError(f"Missing input dates: {', '.join(missing)}")
    if not paths:
        raise FileNotFoundError(f"No CSV inputs found in {input_dir}")
    return paths


def export_stable_samples(
    *,
    input_paths: list[Path],
    output_dir: Path,
    platform_config: dict[str, Any],
    phase_config: dict[str, Any],
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_dir}")
    stable_dir = output_dir / "stable_samples"
    stable_dir.mkdir(parents=True, exist_ok=True)

    stable_segments: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []

    for path in input_paths:
        samples = pd.read_csv(path, parse_dates=["time"], low_memory=False)
        date = _analysis_date(path, samples)
        payload, assignments = analyze_platform_first_day(
            samples,
            date=date,
            experiment_config=platform_config,
            phase_config=phase_config,
        )

        stable = assignments.loc[
            assignments["experimental_phase_label"].eq("stable")
            & assignments["experimental_platform_id"].notna()
        ].copy()
        stable["platform_id"] = stable["experimental_platform_id"]
        stable["stable_phase_id"] = stable["experimental_phase_id"]
        stable["phase_label"] = "stable"
        stable = stable.sort_values(["time", "source_row"], kind="stable")
        stable.to_csv(
            stable_dir / f"{date}.stable.csv.gz",
            index=False,
            encoding="utf-8-sig",
            compression="gzip",
        )

        day_stable_segments = [
            {"date": date, **row}
            for row in payload["phases"]
            if row.get("phase_label") == "stable"
        ]
        stable_segments.extend(day_stable_segments)
        day_summaries.append(
            {
                "date": date,
                "source_file": str(path.resolve()),
                "input_sample_count": int(len(samples)),
                "platform_count": int(payload["summary"]["experimental_platform_count"]),
                "stable_platform_count": int(
                    payload["summary"]["platforms_with_stable_phase"]
                ),
                "stable_segment_count": int(len(day_stable_segments)),
                "stable_sample_count": int(len(stable)),
                "stable_output": str(
                    (stable_dir / f"{date}.stable.csv.gz").resolve()
                ),
            }
        )

    _write_rows(output_dir / "stable_segments.csv", stable_segments)
    _write_rows(output_dir / "daily_summary.csv", day_summaries)
    summary = {
        "method": {
            "platform": platform_config.get("schema_version"),
            "phase": phase_config.get("phase_segmentation_method_version"),
        },
        "input_requirement": "quality-controlled processed_samples",
        "day_count": len(day_summaries),
        "platform_count": sum(row["platform_count"] for row in day_summaries),
        "stable_platform_count": sum(
            row["stable_platform_count"] for row in day_summaries
        ),
        "stable_segment_count": len(stable_segments),
        "stable_sample_count": sum(row["stable_sample_count"] for row in day_summaries),
        "outputs": {
            "stable_samples": str(stable_dir.resolve()),
            "stable_segments": str((output_dir / "stable_segments.csv").resolve()),
            "daily_summary": str((output_dir / "daily_summary.csv").resolve()),
        },
        "platform_config": platform_config,
        "phase_config": phase_config,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    frame = pd.json_normalize(rows, sep=".") if rows else pd.DataFrame()
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _date_from_name(path: Path) -> str:
    match = DATE_PATTERN.search(path.name)
    if not match:
        raise ValueError(f"Input filename does not contain YYYY-MM-DD: {path.name}")
    return match.group(1)


def _analysis_date(path: Path, samples: pd.DataFrame) -> str:
    date = _date_from_name(path)
    observed = samples["time"].dropna().dt.date.astype(str).unique().tolist()
    if observed != [date]:
        raise ValueError(
            f"Input must contain exactly its filename date {date}: {path}: {observed}"
        )
    return date


def main() -> None:
    args = parse_args()
    platform_config = json.loads(
        args.platform_config.read_text(encoding="utf-8")
    )
    phase_config = layer_config(load_config(args.phase_config), "process")
    paths = discover_inputs(args.input_dir, args.dates)
    summary = export_stable_samples(
        input_paths=paths,
        output_dir=args.output_dir,
        platform_config=platform_config,
        phase_config=phase_config,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
