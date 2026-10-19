from __future__ import annotations

import argparse
import json
from pathlib import Path

from tbm_process.config import DEFAULT_PLC_DIR, PROJECT_ROOT, load_config
from tbm_process.ingestion.profiles import (
    DEFAULT_FIELD_PROFILE_PATH,
    load_field_aliases,
    load_guidance_chainage_mapping,
)
from tbm_process.orchestration.pipeline import run_dataset
from tbm_process.outputs.writer import write_outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build traceable structured TBM PLC process results without calling any report/LLM code."
    )
    parser.add_argument("--plc-dir", default=str(DEFAULT_PLC_DIR))
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    parser.add_argument(
        "--field-profile",
        default=str(DEFAULT_FIELD_PROFILE_PATH),
        help="JSON mapping from project-specific PLC columns to canonical fields.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "outputs" / "base_analysis"),
        help="Base quality-controlled output consumed by scripts/run_current_analysis.py.",
    )
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument(
        "--plot-dates",
        default="2023-10-28,2023-11-03,2023-11-14,2023-11-30,2023-12-01",
        help="Comma-separated dates. Use an empty value to plot all dates.",
    )
    args = parser.parse_args()
    config = load_config(Path(args.config))
    field_aliases = load_field_aliases(Path(args.field_profile))
    guidance_chainage_mapping = load_guidance_chainage_mapping(Path(args.field_profile))
    result = run_dataset(
        plc_dir=Path(args.plc_dir).expanduser(),
        config=config,
        max_files=args.max_files,
        field_aliases=field_aliases,
        guidance_chainage_mapping=guidance_chainage_mapping,
    )
    plot_dates = [item.strip() for item in args.plot_dates.split(",") if item.strip()]
    write_outputs(result, Path(args.out_dir).expanduser(), plot_dates=plot_dates)
    print(f"Structured PLC process analysis written to {Path(args.out_dir).expanduser()}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

