#!/usr/bin/env python3
"""Build a read-only, date-normalized PLC dataset view from raw source folders."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tbm_process.ingestion.dataset_view import build_dataset_view


DEFAULT_REQUIRED_FIELDS = (
    "运行时间-time",
    "掘进状态",
    "导向盾首里程",
    "开累进尺",
    "推进速度",
    "贯入度",
    "推力",
    "刀盘扭矩",
    "刀盘实际转速",
    "刀盘给定转速",
    "刀盘给定频率",
    "M1电机频率",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Source label and folder. Repeat for each raw PLC folder.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--time-field", default="运行时间-time")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = [_parse_source(value) for value in args.source]
    manifest = build_dataset_view(
        sources,
        output_dir=args.output_dir.expanduser(),
        time_field=args.time_field,
        required_fields=DEFAULT_REQUIRED_FIELDS,
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))


def _parse_source(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--source must use LABEL=PATH")
    label, raw_path = value.split("=", 1)
    if not label.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("--source must use non-empty LABEL=PATH")
    return label.strip(), Path(raw_path).expanduser()


if __name__ == "__main__":
    main()
