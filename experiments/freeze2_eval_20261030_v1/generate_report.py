"""Single whole-report entry for the new frozen snapshot; paid use is explicit."""
import argparse
from common import *


if __name__=='__main__':
    from tbm_process.engineering_reporting.pipeline import run
    p=argparse.ArgumentParser();p.add_argument('--date',required=True)
    p.add_argument('--output-dir',required=True,type=Path);p.add_argument('--llm',action='store_true')
    args=p.parse_args()
    if not freeze_check()['passed']:raise RuntimeError('frozen_sources_changed')
    result=run(args.date,args.output_dir.resolve(),PATHS,dry_run=not args.llm,env_file=ROOT/'.env')
    print(json.dumps(result,ensure_ascii=False))
