"""Archive dependency failures before a single input preparation retry; no model calls."""
import shutil
from common import *


def main():
    moved=[]
    for p in sorted((HERE/'inputs').glob('*/preparation.result.json')):
        record=load(p)
        if record['status']=='PREPARATION_FAILED' and record.get('error_class')=='OSError':
            target=HERE/'preparation_attempts/cache_missing'/p.parent.name
            if target.exists():raise RuntimeError('dependency_retry_already_recorded')
            shutil.move(str(p.parent),str(target))
            moved.append({'date':p.parent.name,'original_failure':record,'archived_path':str(target)})
    save(HERE/'DEPENDENCY_REPAIR.json',{'kind':'environment_path_not_method_change',
        'cause':'frozen_source_snapshot_did_not_initially_link_existing_local_embedding_model_cache',
        'fix':'frozen_source/.cache links to existing project .cache; same pinned model revision',
        'API_calls':0,'date_changes':False,'attempts':moved,'retry_round':1})


if __name__=='__main__':main()
