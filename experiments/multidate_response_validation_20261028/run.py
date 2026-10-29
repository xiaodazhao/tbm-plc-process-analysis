#!/usr/bin/env python3
"""Run only isolated experiment code; existing methods and data are read-only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import traceback

from .common import HERE, protection_snapshot, read, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['scan','B','offline','generate','evaluate','deliver'])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    protocol=read(HERE/'protocol.json')
    output=args.output
    if args.stage=='scan':
        output.mkdir(parents=True,exist_ok=False)
        write_json(output/'protocol.frozen.json',protocol)
        write_json(output/'protected.before.json',protection_snapshot())
    try:
        if args.stage=='scan':
            from .scan_and_explore import scan
            scan(output,protocol)
        elif args.stage=='B':
            from .scan_and_explore import explore
            print(json.dumps(explore(output,protocol),ensure_ascii=False,default=lambda value:value.item()),flush=True)
        elif args.stage=='offline':
            from .report_validation import offline
            offline(output,protocol)
        elif args.stage=='generate':
            from .report_validation import generate
            generate(output,protocol)
        elif args.stage=='evaluate':
            from .report_validation import evaluate
            evaluate(output,protocol)
        elif args.stage=='deliver':
            from .delivery import deliver
            deliver(output,protocol)
    except Exception as exc:
        write_json(output/(args.stage+'.execution_failure.json'),{'type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()})
        raise


if __name__=='__main__':
    main()
