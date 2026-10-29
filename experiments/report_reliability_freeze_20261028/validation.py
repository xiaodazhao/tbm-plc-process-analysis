"""Precommitted reliability probes and held-out-date functional verification."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import difflib
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import traceback
import zipfile

import fitz
import pandas as pd

from .run import ANALYSIS,DB,MODIFIED,OLD,OUT,PROTECTED,ROOT,csv,protection,read,rebuild
from tbm_process.reporting_v2.facts import digest,write_json


def probes():
    from tbm_process.evidence_graph.joint_reporting import draft_quality
    day='2024-06-20';base=OUT/'history_replay_v2'/day
    pack=read(base/'evidence_pack.json');plan=read(base/'content_plan.json')
    draft=read(OLD/'A_reports'/day/'generated/draft.before.json')
    cases=[]
    def case(name,expected,change=None):
        p,d=deepcopy(pack),deepcopy(draft)
        if change:change(p,d)
        cases.append((name,expected,p,d))
    def replace(d,old,new):
        done=False
        for ch in d['chapters']:
            for claim in ch['claims']:
                if old in claim['text']:claim['text']=claim['text'].replace(old,new);done=True
        if not done:raise ValueError('probe_source_literal_not_found:'+old)
    case('unchanged_real_model_output','NONBLOCKING')
    case('wrong_decrease_direction','FAIL',lambda p,d:replace(d,'推进速度降低6.43%','推进速度提高6.43%'))
    case('wrong_decrease_magnitude','FAIL',lambda p,d:replace(d,'推进速度降低6.43%','推进速度降低16.43%'))
    case('wrong_load_direction','FAIL',lambda p,d:replace(d,'总推力提高10.19%','总推力降低10.19%'))
    def wrong_id(p,d):d['chapters'][2]['claims'][0]['analysis_unit_id']='invented'
    case('invented_analysis_unit','FAIL',wrong_id)
    def wrong_range(p,d):d['chapters'][2]['claims'][0]['geology_assignments'][0]['plc_range_m']=[1.,2.]
    case('geology_assigned_to_wrong_range','FAIL',wrong_range)
    def wrong_relation(p,d):d['chapters'][2]['claims'][0]['relation_ids']=['invented']
    case('invented_relation','FAIL',wrong_relation)
    def future(p,d):
        ids=set(d['chapters'][2]['claims'][1]['fact_ids'])
        for f in p['facts']:
            if f['fact_id'] in ids and f['writer_data'].get('source_type')=='SKETCH':
                f['writer_data']['source_date']='2099-01-01'
    case('future_source_metadata_no_future_text','FAIL',future)
    case('historical_observation_promoted_to_today','FAIL',lambda p,d:d['chapters'][2]['claims'][1].update(text='当日新增揭示Ⅳ级围岩。'))
    case('availability_date_misrepresented_as_observation','FAIL',lambda p,d:d['chapters'][2]['claims'][1].update(text='2024年6月18日现场测得Ⅳ级围岩。'))
    case('negated_current_reveal_is_legal','NONBLOCKING',lambda p,d:d['chapters'][2]['claims'][1].update(
        text='并非当日新增揭示，不构成当天实际揭露。原始观测日期未记录，仅作为历史背景。'))
    case('unsupported_fault_in_compound_sentence','FAIL',lambda p,d:d['chapters'][2]['claims'][0].update(
        text=d['chapters'][2]['claims'][0]['text']+'已进入F99999断层。'))
    case('strict_controls_not_established','FAIL',lambda p,d:d['chapters'][2]['claims'][0].update(
        text=d['chapters'][2]['claims'][0]['text']+'已经排除了操作因素。'))
    case('source_relative_reference_transferred','FAIL',lambda p,d:replace(d,
        '原报告以DyK1016+476至DyK1016+510为参照','原报告以DyK1016+505至DyK1016+510为参照'))
    write_json(OUT/'probe_protocol.before_execution.json',[{'case':n,'expected':e,'kind':'controlled_perturbation_not_field_error_label'} for n,e,_,_ in cases])
    rows=[]
    for name,expected,p,d in cases:
        q=draft_quality(d,plan,p)
        ok=(q['status']=='FAIL') if expected=='FAIL' else not q['errors']
        dest=OUT/'probes'/name;dest.mkdir(parents=True,exist_ok=False)
        for file,obj in [('draft',d),('pack',p),('quality',q)]:write_json(dest/(file+'.json'),obj)
        rows.append({'case':name,'expected':expected,'actual':q['status'],'met_expectation':ok,
                     'errors':q['errors'],'review':q['review_items'],'not_fault_detection_or_engineering_accuracy':True})
    csv(OUT/'probe_results.csv',rows);write_json(OUT/'probe_results.json',rows)
    return rows


def code_hashes():
    files=MODIFIED+['tbm_process/evidence_graph/report_reliability.py']
    return {name:digest(ROOT/name) for name in files}


def select():
    if (OUT/'validation_selection.frozen.json').exists():raise ValueError('selection_already_frozen')
    log=(OUT/'full_tests.final_pre_API.log').read_text()
    if not re.search(r'\d+ passed',log) or re.search(r'\d+ failed|ERRORS',log):raise ValueError('full_tests_not_passed')
    prior=read(OUT/'historical_replay.v2.json')
    if sum(r['status']=='offline_passed' for r in prior)!=11:raise ValueError('historical_offline_replay_not_passed')
    results=probes()
    if not all(r['met_expectation'] for r in results):raise ValueError('precommitted_reliability_probe_failed')
    frame=pd.read_csv(OLD/'329day_screening.csv')
    excluded=set(read(OLD/'date_selection.frozen.json')['candidate_dates'])|{'2023-11-03','2024-03-15'}
    pool=frame.loc[~frame.date.isin(excluded)&frame.spatial_allowed].copy()
    normal=pool.loc[(pool.coverage_ratio>=.8)&(pool.net_change_m>0)&(pool.qualified_samples>=30)&(pool.eligible_forecast_units>0)]
    scarce=pool.loc[(pool.coverage_ratio>=.8)&(pool.net_change_m>0)&(pool.qualified_samples>=30)&(pool.eligible_forecast_units==0)]
    special=pool.loc[(pool.qualified_samples==0)&((pool.net_change_m==0)|(pool.coverage_ratio<.25))]
    seed=2026102802;rng=random.Random(seed);chosen=[]
    for category,candidates in [('normal_with_forecast',normal),('advancing_without_forecast_intersection',scarce),('insufficient_or_zero_advance',special)]:
        candidates=candidates.sort_values('date')
        if candidates.empty:continue
        row=candidates.iloc[rng.randrange(len(candidates))].to_dict()
        chosen.append({'date':row['date'],'category':category,'selection_basis':row})
    freeze={'seed':seed,'development_dates_excluded':sorted(excluded),'candidate_pool':pool.to_dict('records'),
        'category_pools':{'normal':normal.date.tolist(),'scarce':scarce.date.tolist(),'special':special.date.tolist()},
        'selected':chosen,'maximum_new_calls':3,'retry_policy':'zero_transport_zero_quality_retries',
        'planning_branch':'existing_deterministic_phenomenon_plan_one_generation_call_per_day',
        'not_an_independent_test_of_LLM_planner_benefit':True,
        'no_replacement_no_tuning_on_validation_outputs':True,'code_sha256':code_hashes()}
    write_json(OUT/'validation_selection.frozen.json',freeze)
    rows=[]
    for chosen_day in chosen:
        day=chosen_day['date'];folder=OUT/'independent_validation'/day
        try:
            _,_,_,pack,plan,checks=rebuild(day,folder)
            row={**chosen_day,'preflight':checks,'can_generate':all(checks[k] for k in ['graph','analysis','plan']),
                 'pack_sha256':digest(folder/'evidence_pack.json'),'plan_sha256':digest(folder/'content_plan.json')}
        except Exception as exc:
            row={**chosen_day,'can_generate':False,'reason':str(exc)}
        rows.append(row);write_json(OUT/'independent_preflight.json',rows)
        print(json.dumps({'date':day,'category':chosen_day['category'],'can_generate':row['can_generate']},ensure_ascii=False),flush=True)
    write_json(OUT/'pre_API_freeze.json',{'selection_sha256':digest(OUT/'validation_selection.frozen.json'),
        'code_sha256':code_hashes(),'inputs':rows,'paid_calls_so_far':0,'protected_sha256':protection()})


def generate():
    from tbm_process.evidence_graph.joint_reporting import ModelSession,run_writer
    from tbm_process.evidence_graph.three_chapter import requests
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.final_reporting.common import load_deepseek_api_key
    from tbm_process.reporting_v2.performance import calculate_metrics
    from scripts.run_three_chapter_report import render
    freeze=read(OUT/'pre_API_freeze.json')
    if code_hashes()!=freeze['code_sha256']:raise ValueError('core_changed_after_validation_selection')
    if (OUT/'model_calls.json').exists():raise ValueError('existing_ledger_no_rerun')
    if not all(row['can_generate'] for row in freeze['inputs']):raise ValueError('independent_offline_preflight_failed_no_API')
    policy=read(ROOT/'configs/plc_response_relations.json')
    policy['llm'].update(maximum_calls=3,transport_retries=0,maximum_local_revision_calls_per_report=0)
    key=load_deepseek_api_key(Path.home()/'Desktop/tbm-bitemporal-twin/.env')
    session=ModelSession(OUT,policy,key);rows=[]
    for item in freeze['inputs']:
        day=item['date'];folder=OUT/'independent_validation'/day
        if digest(folder/'evidence_pack.json')!=item['pack_sha256'] or digest(folder/'content_plan.json')!=item['plan_sha256']:
            raise ValueError('validation_input_changed')
        pack=read(folder/'evidence_pack.json');plan=read(folder/'content_plan.json')
        draft,status=run_writer(pack,folder/'model',policy,session,revise=False,supplied_plan=plan,request_builder=requests)
        row={'date':day,'category':item['category'],**status}
        if draft:
            quality=read(folder/'model/quality.final.json')
            final=folder/'report';final.mkdir(exist_ok=False)
            write_json(final/'narrative.json',draft);write_json(final/'quality.json',quality)
            package=read(folder/'report_facts.json')
            samples,_=calculate_metrics(pd.read_csv(package['source']['samples']),package['config']);samples.time=pd.to_datetime(samples.time)
            graph=EvidenceGraph.from_snapshot(read(folder/'daily_evidence_graph.json'))
            row.update(render(package,samples,graph,pack,plan,draft,final,{'actual_llm_report':True,
                'program_checks_passed':quality['passed'],'quality_status':quality['status']}))
            row.update(quality_status=quality['status'],errors=quality['errors'],review=quality['review_items'],
                       independent_human_acceptance='NOT_COMPLETED')
        rows.append(row);write_json(OUT/'independent_results.json',rows)
        print(json.dumps(row,ensure_ascii=False),flush=True)


def deliver():
    from .delivery import finish
    finish()
