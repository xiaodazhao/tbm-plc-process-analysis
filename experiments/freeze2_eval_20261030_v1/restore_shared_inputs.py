"""Restore only manifest-verified duplicate input copies in an extracted delivery."""
import argparse
import shutil
from common import *


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
    rows=csv_read(HERE/'SHARED_INPUT_RESTORE.csv')
    for row in rows:
        source=HERE/row['restore_from'];target=HERE/row['omitted_group_file']
        if sha(source)!=row['same_SHA256']:raise RuntimeError('shared_source_SHA_mismatch')
        if target.exists():
            if sha(target)!=row['same_SHA256']:raise RuntimeError('existing_group_file_not_overwritten')
            continue
        if args.apply:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    print({'verified_manifest_rows':len(rows),'copied_missing_files':args.apply})
