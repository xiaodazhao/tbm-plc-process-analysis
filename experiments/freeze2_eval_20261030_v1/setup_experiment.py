"""Source locking, conservative date exposure audit, and input-only preparation."""
from collections import Counter, defaultdict
import os
from pathlib import Path
import random
import re
import shutil

import pandas as pd

from common import *

OLD=ROOT/'experiments/frozen_eval_20261029_v1'
PREVIOUS=ROOT/'experiments/practical_report_policy_20261030_v1'


def lock_source():
    if (HERE/'FREEZE.json').exists():raise RuntimeError('source_already_locked')
    if not freeze_check()['passed']:raise RuntimeError('original_freeze_changed')
    source=HERE/'frozen_source'
    manifest={str(p.relative_to(source)):sha(p) for p in sorted(source.rglob('*'))
        if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts
        and '.pytest_cache' not in p.parts and '.cache' not in p.parts}
    changes=[]
    for name in manifest:
        original=PREVIOUS/'accepted_candidate'/name
        if not original.is_file() or sha(original)!=manifest[name]:changes.append(name)
    save(HERE/'FREEZE.json',{'version':VERSION,'created_utc':now(),'source_SHA256':manifest,
        'parent':'practical-candidate4.1','parent_freeze1_preserved':True,
        'changes_from_parent':changes,
        'allowed_scope':'correct_mixed_risk_point_view_and_no_comparison_prose_instruction_only',
        'entry':'run.py generate --scope interface|formal',
        'not_silently_installed_over_freeze1':True,'paid_API_default':False})
    log('freeze2 source locked; files='+str(len(manifest)))


def exposure():
    exposed=defaultdict(list)
    for row in csv_read(OLD/'DEVELOPMENT_EXCLUSIONS.csv'):
        exposed[row['date']].append({'kind':'previous_documented_exposure','path':str(OLD/'DEVELOPMENT_EXCLUSIONS.csv')})
    for name in ['formal_dates','interface_dates']:
        for day in load(OLD/'SAMPLING.json')[name]:
            exposed[day].append({'kind':'previous_experiment_or_stability_dates','path':str(OLD/'SAMPLING.json')})
    for day in DEVELOPMENT:
        exposed[day].append({'kind':'explicit_interface_development','path':'current_preregistration'})
    roots=[ROOT/'experiments',ROOT/'outputs',ROOT/'local_data',Path('/Users/zhaoxiaoda/TBM历史归档/20261029_冻结前')]
    excluded={'node_modules','.git','.venv','__pycache__','.cache','frozen_source','candidate',
        'accepted_candidate','human_blind','source_code','source_snapshots','datasets'}
    rows=[]
    for base in roots:
        for folder,dirs,files in os.walk(base,followlinks=False):
            dirs[:]=[d for d in dirs if d not in excluded and not re.search('sealed|unblind|blind.*key|密钥',d,re.I)
                     and Path(folder,d).resolve()!=HERE.resolve()]
            for name in files:
                if re.search('sealed|unblind|blind.*key|group.*key|密钥',name,re.I):continue
                if name not in {'model_calls.json','api_calls.json','final_narrative.json','content_plan.json'}:continue
                p=Path(folder)/name;dates=set(re.findall(r'20\d{2}-\d{2}-\d{2}',str(p.relative_to(base))))
                try:value=load(p)
                except (ValueError,OSError):continue
                if isinstance(value,dict) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}',str(value.get('date',''))):dates.add(value['date'])
                if isinstance(value,list):
                    dates.update(r['date'] for r in value if isinstance(r,dict) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}',str(r.get('date',''))))
                for day in dates:
                    exposed[day].append({'kind':'existing_model_or_report_artifact','path':str(p)})
                if dates:rows.append({'path':str(p),'sha256':sha(p),'dates':sorted(dates)})
    for root in [ROOT/'tests',PREVIOUS/'tests',HERE/'frozen_source/tests']:
        for p in root.glob('*.py'):
            for day in set(re.findall(r'20\d{2}-\d{2}-\d{2}',p.read_text())):
                exposed[day].append({'kind':'test_fixture_or_evaluation_definition','path':str(p)})
    csv_write(HERE/'HISTORY_EXPOSURE_EVIDENCE.csv',rows)
    return exposed


def sample():
    if (HERE/'SAMPLING.json').exists():raise RuntimeError('sampling_already_locked')
    if not freeze_check()['passed']:raise RuntimeError('source_integrity_failed')
    seen=exposure();rows=[]
    for old in csv_read(OLD/'CANDIDATE_POOL.csv'):
        day=old['date'];reasons=json.loads(old['exclusion_reasons'])
        processed=Path(old['processed_path']);standard=Path(old['standardized_path'])
        if not processed.is_file() or sha(processed)!=old['processed_sha256']:reasons.append('processed_input_missing_or_changed')
        if not standard.is_file():reasons.append('standardized_input_missing')
        if day in seen:reasons.append('prior_report_model_or_evaluation_exposure')
        rows.append({**old,'prior_exposure':day in seen,'exclusion_reasons':sorted(set(reasons)),
                     'eligible_candidate':not reasons})
    pool=sorted(r['date'] for r in rows if r['eligible_candidate'])
    dates=sorted(random.Random(SEED).sample(pool,min(30,len(pool))))
    human=sorted(random.Random(SEED+1).sample(dates,min(12,len(dates))))
    lookup={r['date']:r for r in rows}
    csv_write(HERE/'CANDIDATE_POOL.csv',rows)
    csv_write(HERE/'DEVELOPMENT_EXCLUSIONS.csv',[{'date':d,'evidence':e} for d,e in sorted(seen.items())])
    csv_write(HERE/'TEST_DATES.csv',[lookup[d]|{'human_review_selected':d in human} for d in dates])
    csv_write(HERE/'INTERFACE_DATES.csv',[{'date':d,'role':'development_only'} for d in DEVELOPMENT])
    result={'seed':SEED,'method':'simple_random_without_replacement_from_sorted_available_unexposed_pool',
        'pool':pool,'pool_count':len(pool),'formal_dates':dates,'interface_dates':DEVELOPMENT,
        'human_dates':human,'human_seed':SEED+1,'locked_at':now(),
        'date_replacement_after_generation':False,
        'independence':'no_prior_exposure_found_in_accessible_artifacts; unavailable_deleted_history_not_proven',
        'history_search_roots':[str(r) for r in [ROOT/'experiments',ROOT/'outputs',ROOT/'local_data']],
        'existing_blind_or_sealed_allocation_not_read':True,
        'low_coverage_no_platform_and_zero_advance_not_automatic_exclusions':True}
    save(HERE/'SAMPLING.json',result)
    save(HERE/'FREEZE_AUDIT.json',{'source':freeze_check(),'pool_count':len(pool),
        'original_rows':len(rows),'eligible_before_new_exposure_exclusion':sum(r['eligible_candidate']=='True' for r in csv_read(OLD/'CANDIDATE_POOL.csv')),
        'new_API_calls':0,'paths':{k:{'path':str(p),'exists':p.exists()} for k,p in PATHS.items()}})
    log('sample locked; pool='+str(len(pool))+' dates='+','.join(dates))


def prepare_inputs():
    from tbm_process.engineering_reporting.pipeline import prepare
    sampling=load(HERE/'SAMPLING.json');records=[];sharing=[]
    for day in DEVELOPMENT+sampling['formal_dates']:
        folder=HERE/'inputs'/day;record=folder/'preparation.result.json'
        if record.exists():records.append(load(record));continue
        try:
            log('prepare '+day)
            pack=prepare(day,folder,PATHS)
            forecasts=load(folder/'admissible_forecasts.json')
            roles=Counter(r['document_type'] for r in forecasts if r['day_intersection_m'])
            docs=sorted({r['document_id'] for r in forecasts if r['day_intersection_m'] or r['ahead_intersection_m']})
            for doc in docs:sharing.append({'date':day,'forecast_document_id':doc})
            overview=next(f['data'] for f in pack['facts'] if f['fact_id']=='DAY')
            row={'date':day,'status':'PREPARED','input_sha256':sha(folder/'unified_report_input.json'),
                'fact_count':len(pack['facts']),'coverage_ratio':overview['coverage_ratio'],
                'stable_phase_count':len(load(folder/'evidence_pack.original.json').get('response_analysis',{}).get('time_windows',[])),
                'TSP':roles['TSP'],'HSP':roles['HSP'],'forecast_document_ids':docs,
                'ahead_covered_m':pack['coverage']['ahead']['forecast_union_m'],
                'historical_sketch':any(f['role']=='historical_observation_not_today' for f in pack['facts']),
                'new_API_calls':0}
        except Exception as exc:
            folder.mkdir(parents=True,exist_ok=True)
            row={'date':day,'status':'PREPARATION_FAILED','error_class':type(exc).__name__,
                 'reason':str(exc) if isinstance(exc,(ValueError,KeyError,FileNotFoundError)) else 'see_preparation_checks',
                 'new_API_calls':0,'date_not_replaced':True}
        save(record,row);records.append(row);log(day+' '+row['status'])
    csv_write(HERE/'INPUT_PREPARATION.csv',records)
    sharing=[]
    for r in records:
        p=HERE/'inputs'/r['date']/'admissible_forecasts.json'
        if p.exists():
            for doc in sorted({r['document_id'] for r in load(p) if r['day_intersection_m'] or r['ahead_intersection_m']}):
                sharing.append({'date':r['date'],'forecast_document_id':doc})
    csv_write(HERE/'DATE_SOURCE_SHARING.csv',sharing,['date','forecast_document_id'])
    eligible=sorted(r['date'] for r in records if r['date'] in sampling['formal_dates'] and r['status']=='PREPARED'
                    and (r['TSP']+r['HSP']>1 or r['historical_sketch']))
    selected=sorted(random.Random(SEED+2).sample(eligible,min(10,len(eligible))))
    save(HERE/'RELATION_SUBSAMPLE.json',{'seed':SEED+2,'dates':selected,'selection_before_model_outputs':True,
         'criterion':'multiple_forecasts_or_historical_sketch; no_model_quality_selection'})


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','sample','prepare']);a=p.parse_args()
    {'freeze':lock_source,'sample':sample,'prepare':prepare_inputs}[a.stage]()
