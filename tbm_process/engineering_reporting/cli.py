from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path

from .common import ROOT, save


def main():
    parser = argparse.ArgumentParser(description='Whole-report integrated engineering pipeline; facts precede one planner and writer.')
    parser.add_argument('--date', required=True)
    parser.add_argument('--analysis-dir', type=Path, default=ROOT/'experiments/plc_mapping_repair_v1/replay/after')
    parser.add_argument('--source-root', type=Path, default=ROOT.parent/'伯舒拉岭数据')
    parser.add_argument('--database', type=Path, default=ROOT.parent/'TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite')
    parser.add_argument('--registry', type=Path, default=ROOT.parent/'TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json')
    parser.add_argument('--resources', type=Path, default=ROOT/'local_data/engineering_context')
    parser.add_argument('--context-config', type=Path, default=ROOT/'configs/engineering_context.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/unified_engineering_reports')
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env')
    parser.add_argument('--model', default='deepseek-v4-flash')
    parser.add_argument('--dry-run', action='store_true', help='Compute all facts and save an unsent planner request; no API call or report prose.')
    parser.add_argument('--resume', action='store_true', help='Continue an offline-prepared, hash-locked input; never overwrite an existing API run.')
    parser.add_argument('--skip-pdf', action='store_true')
    args = parser.parse_args()
    day = date.fromisoformat(args.date).isoformat()
    folder = args.output_dir.resolve()/day
    os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'.cache/unified_matplotlib'))
    paths = {name: getattr(args, name).resolve() for name in
             ['analysis_dir', 'source_root', 'database', 'registry', 'resources', 'context_config']}
    paths['analysis'] = paths.pop('analysis_dir')
    from .pipeline import run
    try:
        result = run(day, folder, paths, dry_run=args.dry_run, resume=args.resume,
                     env_file=args.env_file, model=args.model, skip_pdf=args.skip_pdf)
        save(folder/'run_summary.json', result)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Do not persist service exception strings or credentials.
        if folder.exists():
            save(folder/'run.failure.json', {'error_class': type(exc).__name__,
                 'reason': str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else 'see_saved_stage_checks_or_failure_record'})
        raise
