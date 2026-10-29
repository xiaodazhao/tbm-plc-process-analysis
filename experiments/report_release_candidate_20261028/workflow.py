"""Finite development replay and two locked, fresh-generation acceptance cases."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys
import traceback

import pandas as pd

from .run import OUT,OLD,PREVIOUS,ROOT,FILES,DB,PROTECTED,protected,read,rebuild
from tbm_process.reporting_v2.facts import digest,write_json


def hashes():return {f:digest(ROOT/f) for f in FILES}


def replay():
    from tbm_process.evidence_graph.joint_reporting import draft_quality
    expected=read(PREVIOUS/'protected.before.json')[str(DB)]
    if digest(DB)!=expected:raise ValueError('restored_database_differs_from_previous_version')
    write_json(OUT/'data_access.restored.json',{'database':str(DB),'sha256':digest(DB),
        'identical_to_previous_audited_database':True,'fallback_catalog_not_used':True})
    days=sorted(set(read(OLD/'date_selection.frozen.json')['candidate_dates']) |
                {x['date'] for x in read(PREVIOUS/'independent_preflight.json')})
    rows=[]
    for day in days:
        folder=OUT/'development_replay'/day
        try:
            _,_,_,pack,plan,checks=rebuild(day,folder,prior=True)
            row={'date':day,**checks,'analysis_units':len(next(f for f in pack['facts'] if f['fact_id']=='SEGMENT_RESPONSE_SYNTHESIS')['writer_data']['analysis_units']),
                 'quarantine':pack['source_quarantine'],'status':'offline_passed' if all(checks[k] for k in ('graph','analysis','plan')) else 'offline_failed'}
            sources=[OLD/'A_reports'/day/'generated',PREVIOUS/'independent_validation'/day/'model']
            for src in sources:
                if not (src/'draft.before.json').exists():continue
                parent=src.parent
                oldpack=read(parent/'evidence_pack.json')
                oldplan=read(parent/('content_plan.json'))
                q=draft_quality(read(src/'draft.before.json'),oldplan,oldpack)
                write_json(folder/'unchanged_historical_model.quality.json',q)
                row['historical_raw_model_replay']={'status':q['status'],'errors':q['errors'],
                    'same_old_input':True,'new_scope_contract_not_retroactively_added':True}
        except Exception as exc:
            folder.mkdir(parents=True,exist_ok=True)
            row={'date':day,'status':'blocked','error_type':type(exc).__name__,'reason':str(exc)}
            write_json(folder/'failure.json',{**row,'traceback':traceback.format_exc()})
        rows.append(row);write_json(OUT/'development_replay.json',rows)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    pd.DataFrame([{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows]).to_csv(OUT/'development_replay.csv',index=False,encoding='utf-8-sig')


def select():
    if (OUT/'validation_selection.frozen.json').exists():raise FileExistsError('selection_already_locked')
    tests=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,capture_output=True,text=True)
    (OUT/'tests.pre_API.log').write_text(tests.stdout+tests.stderr)
    if tests.returncode:raise ValueError('tests_failed_no_API')
    replayed=read(OUT/'development_replay.json')
    unexpected=[r for r in replayed if r['status']!='offline_passed' and r['date']!='2023-11-05']
    if unexpected:raise ValueError('development_preflight_failed:'+str([r['date'] for r in unexpected]))
    frame=pd.read_csv(OLD/'329day_screening.csv')
    excluded={r['date'] for r in replayed}|{'2023-11-03','2024-03-15'}
    pool=frame.loc[~frame.date.isin(excluded)&frame.spatial_allowed&(frame.coverage_ratio>=.8)&
                   (frame.net_change_m>0)&(frame.qualified_samples>=30)].sort_values('date')
    seed=2026102803;rng=random.Random(seed);selection=[];attempts=[]
    for kind,part in [('usable_segment_comparison',pool.loc[pool.eligible_forecast_units>=2]),
                      ('no_forecast_overlap',pool.loc[pool.eligible_forecast_units==0])]:
        dates=part.date.tolist();rng.shuffle(dates)
        for day in dates[:20]:
            folder=OUT/'selection_preflight'/day
            try:
                _,_,_,pack,plan,checks=rebuild(day,folder)
                count=len(next(f for f in pack['facts'] if f['fact_id']=='SEGMENT_RESPONSE_SYNTHESIS')['writer_data']['analysis_units'])
                eligible=all(checks[k] for k in ('graph','analysis','plan')) and (count>0 if kind=='usable_segment_comparison' else True)
                attempt={'date':day,'category':kind,'checks':checks,'analysis_units':count,'eligible':eligible}
            except Exception as exc:
                eligible=False;attempt={'date':day,'category':kind,'eligible':False,'reason':str(exc)}
            attempts.append(attempt);write_json(OUT/'selection_preflight.json',attempts)
            if eligible:
                selection.append({**attempt,'folder':str(folder),'pack_sha256':digest(folder/'evidence_pack.json')});break
        if not selection or selection[-1]['category']!=kind:raise ValueError('no_eligible_case_in_bounded_preflight:'+kind)
    freeze={'seed':seed,'selected':selection,'full_screened_pool':pool.to_dict('records'),
        'excluded_development_dates':sorted(excluded),'code_sha256':hashes(),'protected_sha256':protected(),
        'selection_by_preexisting_data_support_not_generated_text':True,'no_post_model_replacement_or_prompt_tuning':True,
        'interpretation':'fresh_model_generation_functional_validation; all_days_previously_in_offline_inventory_not_pristine_independent_test',
        'maximum_calls':12,'one_quality_repair_per_day':True,'transport_retries':0}
    write_json(OUT/'validation_selection.frozen.json',freeze)
    print(json.dumps({'selected':[r['date'] for r in selection],'API_calls_so_far':0}),flush=True)


def generate():
    from tbm_process.evidence_graph.joint_reporting import ModelSession,run_writer
    from tbm_process.evidence_graph.three_chapter import requests,apply_window_choices
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.final_reporting.common import load_deepseek_api_key
    from tbm_process.reporting_v2.performance import calculate_metrics
    from scripts.run_three_chapter_report import render
    freeze=read(OUT/'validation_selection.frozen.json')
    if hashes()!=freeze['code_sha256']:raise ValueError('code_changed_after_freeze')
    if (OUT/'model_calls.json').exists():raise FileExistsError('paid_run_already_started_no_implicit_rerun')
    policy=read(ROOT/'configs/plc_response_relations.json')
    policy['llm'].update(maximum_calls=12,transport_retries=0,maximum_local_revision_calls_per_report=1)
    write_json(OUT/'effective_model_policy.json',policy)
    session=ModelSession(OUT,policy,load_deepseek_api_key(Path.home()/'Desktop/tbm-bitemporal-twin/.env'))
    rows=[]
    for item in freeze['selected']:
        day=item['date'];folder=Path(item['folder'])
        if digest(folder/'evidence_pack.json')!=item['pack_sha256']:raise ValueError('input_changed')
        pack=read(folder/'evidence_pack.json')
        draft,status=run_writer(pack,folder/'model',policy,session,revise=True,request_builder=requests)
        if status['status']=='failed_plan':
            raw=read(folder/'model/plan.payload.json')
            draft,status=run_writer(pack,folder/'plan_repair',policy,session,revise=False,
                                    rejected_plan=raw,request_builder=requests)
            model=folder/'plan_repair'
        else:model=folder/'model'
        row={'date':day,'category':item['category'],**status,'output':str(folder)}
        if draft:
            plan=read(model/'content_plan.json');quality=read(model/'quality.final.json')
            active=apply_window_choices(pack,plan)
            package=read(folder/'report_facts.json')
            samples,_=calculate_metrics(pd.read_csv(package['source']['samples']),package['config']);samples.time=pd.to_datetime(samples.time)
            graph=EvidenceGraph.from_snapshot(read(folder/'daily_evidence_graph.json'))
            report=folder/'report';report.mkdir(exist_ok=False)
            row.update(render(package,samples,graph,active,plan,draft,report,{'actual_llm_report':True,
                'program_checks_passed':quality['passed'],'quality_status':quality['status']}))
            row.update(quality_status=quality['status'],errors=quality['errors'],window_decisions=plan.get('window_decisions'),
                       engineering_human_acceptance='NOT_COMPLETED')
        rows.append(row);write_json(OUT/'generation_results.json',rows)
        print(json.dumps(row,ensure_ascii=False),flush=True)


def deliver():
    from .delivery import deliver as finish
    finish()


def repair_interface():
    """One disclosed presentation-contract fix; not a new independent experiment."""
    from tbm_process.evidence_graph.joint_reporting import ModelSession,run_writer
    from tbm_process.evidence_graph.three_chapter import requests,apply_window_choices
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.final_reporting.common import load_deepseek_api_key
    from tbm_process.reporting_v2.performance import calculate_metrics
    from scripts.run_three_chapter_report import render
    if (OUT/'interface_fix_results.json').exists():raise FileExistsError('one_supplement_only')
    write_json(OUT/'INTERFACE_FIX_AMENDMENT.before_API.json',{
        'reason':'internal_document_ID_in_body_was_wrongly_treated_as_a_number_to_preserve_during_local_repair',
        'initial_acceptance_results_retained':'generation_results.json','is_post_validation_development_fix':True,
        'not_a_2_of_2_independent_success_claim':True,'date_or_fact_or_plan_reselection':False,
        'one_additional_targeted_revision_only':True,'code_sha256':hashes()})
    tests=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,capture_output=True,text=True)
    (OUT/'tests.after_interface_fix.log').write_text(tests.stdout+tests.stderr)
    if tests.returncode:raise ValueError('tests_failed_no_supplemental_API')
    source=OUT/'selection_preflight/2023-12-20'
    pack=read(source/'evidence_pack.json');plan=read(source/'model/content_plan.json')
    policy=read(ROOT/'configs/plc_response_relations.json')
    policy['llm'].update(maximum_calls=12,transport_retries=0,maximum_local_revision_calls_per_report=1)
    session=ModelSession(OUT,policy,load_deepseek_api_key(Path.home()/'Desktop/tbm-bitemporal-twin/.env'))
    session.records=read(OUT/'model_calls.json')
    model=source/'interface_fix'
    draft,status=run_writer(pack,model,policy,session,revise=True,request_builder=requests,
        reuse={'plan':plan,'draft':read(source/'model/draft.before.json'),'source':str(source/'model')})
    quality=read(model/'quality.final.json')
    package=read(source/'report_facts.json')
    samples,_=calculate_metrics(pd.read_csv(package['source']['samples']),package['config']);samples.time=pd.to_datetime(samples.time)
    graph=EvidenceGraph.from_snapshot(read(source/'daily_evidence_graph.json'))
    report=source/'report_after_interface_fix';report.mkdir(exist_ok=False)
    result={**status,**render(package,samples,graph,apply_window_choices(pack,plan),plan,draft,report,
        {'actual_llm_report':True,'program_checks_passed':quality['passed'],'quality_status':quality['status']}),
        'quality_status':quality['status'],'errors':quality['errors'],
        'post_validation_development_replay_not_independent':True}
    write_json(OUT/'interface_fix_results.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


def verify_patch():
    """Recheck the saved model patch after contract tests; no new model call."""
    from tbm_process.evidence_graph.joint_reporting import draft_quality,retention_check
    from tbm_process.evidence_graph.three_chapter import apply_window_choices
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.reporting_v2.performance import calculate_metrics
    from scripts.run_three_chapter_report import render
    tests=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,capture_output=True,text=True)
    (OUT/'tests.final.log').write_text(tests.stdout+tests.stderr)
    if tests.returncode:raise ValueError('final_tests_failed')
    folder=OUT/'selection_preflight/2023-12-20';pack=read(folder/'evidence_pack.json')
    plan=read(folder/'model/content_plan.json');before=read(folder/'model/draft.before.json')
    patch=read(folder/'interface_fix/local_revision.payload.json');candidate=deepcopy(before)
    targets={(2,1)}
    for change in patch['replacements']:
        loc=(change['chapter_index'],change['claim_index'])
        if loc not in targets:raise ValueError('patch_modified_non_target')
        c=candidate['chapters'][loc[0]]['claims'][loc[1]]
        c.update({k:change[k] for k in ('text','fact_ids','relation_ids') if k in change})
    retention=retention_check(before,candidate,targets,pack);quality=draft_quality(candidate,plan,pack)
    write_json(folder/'saved_patch_recheck.json',{'quality':quality,'retention':retention,
        'actual_saved_model_patch_not_manually_written_text':True,'no_new_API_call':True,'code_sha256':hashes()})
    if not retention['passed'] or not quality['passed']:raise ValueError('saved_patch_still_not_acceptable')
    model=folder/'accepted_saved_patch';model.mkdir(exist_ok=False)
    for name,value in [('narrative',candidate),('content_plan',plan),('quality.final',quality),('retention',retention)]:
        write_json(model/(name+'.json'),value)
    write_json(model/'provenance.json',{'initial_generation':str(folder/'model/generation.response.json'),
        'actual_local_patch':str(folder/'interface_fix/local_revision.response.json'),
        'post_validation_development_fix_not_independent_success':True})
    package=read(folder/'report_facts.json')
    samples,_=calculate_metrics(pd.read_csv(package['source']['samples']),package['config']);samples.time=pd.to_datetime(samples.time)
    graph=EvidenceGraph.from_snapshot(read(folder/'daily_evidence_graph.json'))
    final=folder/'report_final';final.mkdir(exist_ok=False)
    result={'date':'2023-12-20','quality_status':quality['status'],'program_checks_passed':True,
        'retention':retention,'changed_claims':1,'new_API_calls':0,
        'fresh_acceptance_result_unchanged':'FAIL','post_validation_development_replay':True,
        **render(package,samples,graph,apply_window_choices(pack,plan),plan,candidate,final,
                 {'actual_llm_report':True,'program_checks_passed':True,'quality_status':quality['status']})}
    write_json(OUT/'saved_patch_final_result.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
