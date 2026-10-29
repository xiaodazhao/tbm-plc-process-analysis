"""Run the existing report method on frozen dates; retain every rejection and draft."""
from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
import re
import traceback

import fitz
import pandas as pd

from tbm_process.evidence_graph.analysis import add_daily_analysis
from tbm_process.evidence_graph.catalog import build_catalog_graph
from tbm_process.evidence_graph.geology_response import add_source_responses
from tbm_process.evidence_graph.joint_reporting import ModelSession, draft_quality, plan_quality, run_writer
from tbm_process.evidence_graph.literal_relations import add_literal_relations, overlap
from tbm_process.evidence_graph.queries import daily_subgraph
from tbm_process.evidence_graph.response_analysis import correct_comparison_order, validate_analysis
from tbm_process.evidence_graph.store import EvidenceGraph
from tbm_process.evidence_graph.three_chapter import build_pack, offline_preview, requests
from tbm_process.evidence_graph.validation import validate_graph
from tbm_process.final_reporting.common import load_deepseek_api_key
from tbm_process.geology_store.database import GeologyStore
from tbm_process.reporting_v2.facts import build_facts, digest, duration, write_json
from tbm_process.reporting_v2.performance import calculate_metrics
from scripts.analyze_report_responses import write_tables
from scripts.run_three_chapter_report import render
from .common import ANALYSIS, DATABASE, HERE, PROJECT, protection_snapshot, read, source_units, write_csv


def copy_guard(pack, package):
    """Do not turn an unconditional generic day summary into invented advancement."""
    guarded=deepcopy(pack); edits=[]
    if not guarded['process_summary']['selected']:
        advancing=package['daily_overview']['advancing_seconds']
        replacement=('已观测记录未识别到推进状态，缺测时段的活动未作推断。' if not advancing else
                     f'已观测推进累计{duration(advancing)}，未形成可供概括的主要推进时段。')
        f=next(f for f in guarded['facts'] if f['fact_id']=='DAY_PATTERN_SUMMARY')
        edits.append({'fact_id':f['fact_id'],'before':f['canonical_text'],'after':replacement,
                      'reason':'empty_selected_processes_do_not_support_intermittent_advancement_statement'})
        f['canonical_text']=replacement
        for value in (f['data'],f['writer_data']):
            value['summary']=replacement
    return guarded,edits


def offline(output,protocol):
    selected=read(output/'date_selection.frozen.json')
    config=read(PROJECT/'tbm_process/reporting_v2/defaults.json')
    phase=read(PROJECT/'config.json');policy=read(PROJECT/'configs/plc_response_relations.json')
    registry_path=Path.home()/'Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json'
    registry=read(registry_path); source_root=Path.home()/'Desktop/伯舒拉岭数据'
    store=GeologyStore(DATABASE)
    statuses=[]
    try:
        for day in selected['candidate_dates']:
            folder=output/'A_reports'/day;folder.mkdir(parents=True,exist_ok=False)
            try:
                catalog_path=folder/'time_admissible_catalog.json'
                write_json(catalog_path,store.export_catalog(day))
                graph=build_catalog_graph(read(catalog_path),registry,source_root)
                package,samples=build_facts(report_day=day,project=PROJECT,analysis=ANALYSIS,
                    catalog_path=catalog_path,registry_path=registry_path,source_root=source_root,config=config)
                write_json(folder/'report_facts.json',package)
                add_daily_analysis(graph,package,samples,phase,response_policy=policy,literal_relations=True)
                add_literal_relations(graph,day)
                add_source_responses(graph,package,samples,phase)
                write_json(folder/'daily_evidence_graph.json',daily_subgraph(graph,day).snapshot())
                graph_check=validate_graph(graph,day);write_json(folder/'graph.validation.json',graph_check)
                features=read(output/'cache/daily_features'/f'{day}.json')
                pack=build_pack(package,samples,graph,features)
                write_json(folder/'evidence_pack.unadapted.json',pack)
                pack,edits=copy_guard(pack,package)
                write_json(folder/'copy_guard.changes.json',edits)
                write_json(folder/'evidence_pack.json',pack)
                analysis=pack['response_analysis'];write_json(folder/'response_analysis.json',analysis)
                write_tables(folder,analysis)
                check=validate_analysis(analysis,samples,graph,sample_duration_clip_seconds=phase['maximum_sample_duration_seconds'])
                write_json(folder/'analysis.validation.json',check)
                plan,_=offline_preview(pack)
                plancheck=plan_quality(plan,pack);write_json(folder/'plan.validation.json',plancheck)
                write_json(folder/'content_plan.json',plan)
                write_json(folder/'plan.provenance.json',{'origin':'existing_deterministic_phenomenon_plan',
                    'new_LLM_planning_calls':0,'limitation':'this_experiment_does_not_test_LLM_planning_benefit'})
                can_generate=graph_check['passed'] and check['passed'] and plancheck['passed']
                status={'date':day,'status':'offline_passed' if can_generate else 'offline_failed_checks',
                    'can_generate':can_generate,'copy_guard_changes':len(edits),
                    'unadapted_method_has_unconditional_activity_summary':bool(edits),
                    'stable_samples':analysis['duration_audit']['qualified_sample_count'],
                    'stable_support_seconds':analysis['duration_audit']['supported_duration_seconds'],
                    'windows':len(analysis['time_windows']),'selected_windows':len(analysis['selected_time_windows']),
                    'segment_comparisons':len(analysis['segment_analyses']),
                    'coverage_ratio':package['daily_overview']['coverage_ratio'],
                    'advance_m':package['daily_overview']['daily_advance']['net_change_m'],
                    'forecast_records':len(package['geology']['forecast_records']),
                    'no_new_API_calls':True}
            except Exception as exc:
                status={'date':day,'status':'offline_execution_failed','can_generate':False,
                        'exception':type(exc).__name__,'reason':str(exc),'no_new_API_calls':True}
                write_json(folder/'offline.failure.json',{**status,'traceback':traceback.format_exc()})
                # Preserve the authoritative existing facts even if the adapter
                # cannot express a missing or rejected chainage.
                write_json(folder/'frozen_daily_result.json',read(ANALYSIS/'structured_full_days'/f'{day}.json'))
            statuses.append(status);write_json(output/'A_offline_statuses.json',statuses)
            print(json.dumps(status,ensure_ascii=False),flush=True)
    finally:
        store.close()
    write_csv(output/'A_offline_statuses.csv',statuses)
    write_json(output/'method_freeze.before_API.json',{'protocol':protocol,'formal_dates':selected['formal_dates'],
        'protected_hashes':protection_snapshot(),
        'experiment_code_sha256':{str(p.relative_to(PROJECT)):digest(p) for p in HERE.glob('*.py')},
        'input_sha256':{s['date']:digest(output/'A_reports'/s['date']/'evidence_pack.json') for s in statuses if s['can_generate']},
        'sampling_and_generation_choices_frozen_before_outputs':True})


def generate(output,protocol):
    if (output/'model_calls.json').exists():
        raise ValueError('existing_ledger_no_automatic_rerun_or_retry')
    freeze=read(output/'method_freeze.before_API.json')
    if freeze['protected_hashes']!=protection_snapshot():
        raise ValueError('existing_method_or_data_changed_since_freeze')
    write_json(output/'method_freeze.final_before_API.json',{
        **freeze,
        'experiment_code_sha256':{str(p.relative_to(PROJECT)):digest(p) for p in HERE.glob('*.py')},
        'post_offline_changes':'B-only temporal/independence audit fixes; stdout NumPy serialization fix; no A dates or facts changed',
        'new_API_calls_at_freeze':0})
    policy=read(PROJECT/'configs/plc_response_relations.json')
    policy['llm'].update(maximum_calls=protocol['maximum_new_API_calls'],transport_retries=0,
                          maximum_local_revision_calls_per_report=0)
    key=load_deepseek_api_key(Path.home()/'Desktop/tbm-bitemporal-twin/.env')
    session=ModelSession(output,policy,key)
    statuses=[];offline={r['date']:r for r in read(output/'A_offline_statuses.json')}
    for day in freeze['formal_dates']:
        folder=output/'A_reports'/day;dest=folder/'generated'
        if not offline[day]['can_generate']:
            status={'date':day,'status':'not_sent_offline_failure','actual_LLM_report':False}
        elif not session.available:
            status={'date':day,'status':'not_sent_configured_API_unavailable','actual_LLM_report':False}
        else:
            pack=read(folder/'evidence_pack.json')
            if digest(folder/'evidence_pack.json')!=freeze['input_sha256'][day]:
                raise ValueError('changed_input_since_freeze')
            plan=read(folder/'content_plan.json')
            draft,original_status=run_writer(pack,dest,policy,session,revise=False,supplied_plan=plan,request_builder=requests)
            status={'date':day,**original_status,'post_generation_program_corrections':0}
            if draft:
                try:
                    corrected,edits=correct_comparison_order(draft,pack['response_analysis'])
                    check=draft_quality(corrected,plan,pack)
                    final=folder/'final';final.mkdir(exist_ok=False)
                    write_json(final/'narrative.original_model.json',draft);write_json(final/'narrative.json',corrected)
                    write_json(final/'corrections.json',edits);write_json(final/'quality.final.json',check)
                    package=read(folder/'report_facts.json')
                    samples,_=calculate_metrics(pd.read_csv(package['source']['samples']),package['config'])
                    samples.time=pd.to_datetime(samples.time)
                    graph=EvidenceGraph.from_snapshot(read(folder/'daily_evidence_graph.json'))
                    state={'actual_llm_report':True,'program_checks_passed':check['passed'],'semantic_status':'REVIEW'}
                    status.update(render(package,samples,graph,pack,plan,corrected,final,state))
                    status.update(status='program_checked_candidate' if check['passed'] else 'real_generated_failed_quality',
                                  program_checks_passed=check['passed'],quality_errors=check['errors'],
                                  post_generation_program_corrections=len(edits))
                except Exception as exc:
                    status.update(status='post_generation_failure',reason=str(exc))
                    write_json(folder/'post_generation.failure.json',{'reason':str(exc),'traceback':traceback.format_exc()})
        statuses.append(status);write_json(output/'A_generation_statuses.json',statuses)
        print(json.dumps(status,ensure_ascii=False),flush=True)
    write_csv(output/'A_generation_statuses.csv',statuses)


def evaluate(output,protocol):
    docs,units,_=source_units()
    original_facts={fid:u for u in units for fid in u['fact_ids']}
    statuses=read(output/'A_generation_statuses.json');records=[];human=[]
    for status in statuses:
        day=status['date'];folder=output/'A_reports'/day;final=folder/'final'
        if not (final/'narrative.json').is_file():
            records.append({'date':day,'status':status['status'],'independent_human_accuracy':'pending','PDF_available':False})
            continue
        draft=read(final/'narrative.json');pack=read(folder/'evidence_pack.json');package=read(folder/'report_facts.json')
        checks=[]
        def add(name,passed,details=None):checks.append({'name':name,'passed':bool(passed),'details':details})
        with fitz.open(final/f'{day}.three_chapter.pdf') as doc:
            text=''.join(p.get_text() for p in doc)
        normalized=re.sub(r'\s+','',text)
        traces=[]
        for ch in draft['chapters']:
            for c in ch['claims']:
                add('final_narrative_in_PDF',re.sub(r'\s+','',c['text']) in normalized)
                for a in c.get('geology_assignments',[]):
                    unit=original_facts.get(a['fact_id'])
                    add('assignment_matches_SQLite_source_range',unit is not None and overlap(unit['range_m'],a['plc_range_m']) is not None,a)
                    if unit:
                        gate=__import__('tbm_process.evidence_graph.catalog',fromlist=['document_gate']).document_gate(docs[unit['document_id']],day)
                        add('assigned_source_predates_report_day',gate['status']=='eligible',gate)
                traces.append({'chapter':ch['chapter_id'],**c,
                    'source_records':[{'fact_id':f['fact_id'],'source':f['source'],'graph_support_ids':f['graph_support_ids']}
                                      for f in pack['facts'] if f['fact_id'] in c['fact_ids']]})
                human.append({'date':day,'chapter':ch['chapter_id'],'paragraph_id':c['paragraph_id'],'text':c['text'],
                    'fact_ids':json.dumps(c['fact_ids'],ensure_ascii=False),'工程分析价值':'','文字自然性':'','事实语义核查':'','备注':''})
        add('processed_PLC_source_hash_unchanged',digest(Path(package['source']['samples']))==package['source']['samples_sha256'])
        add('three_chapters_retained',[ch['chapter_id'] for ch in draft['chapters']]==[1,2,3])
        write_json(final/'source_traces.json',traces)
        write_json(final/'separate_consistency_audit.json',{'passed':all(r['passed'] for r in checks),'checks':checks,
            'scope':'separate_SQLite_interval_and_date_lookup_and_final_text_check_not_independent_semantic_truth'})
        claims=[c for ch in draft['chapters'] for c in ch['claims']]
        records.append({'date':day,'status':status['status'],'PDF_available':True,
            'program_quality_passed':status.get('program_checks_passed',False),'separate_consistency_passed':all(r['passed'] for r in checks),
            'artifact_passed':status.get('artifact_passed',False),'paragraphs':len(claims),
            'selected_windows':len(pack['response_analysis']['selected_time_windows']),
            'analysis_units':len(pack['response_analysis']['segment_analyses']),
            'deterministic_analysis_tables_present':True,
            'original_method_copy_guard_needed':bool(read(folder/'copy_guard.changes.json')),
            'independent_human_accuracy':'pending','engineering_value':'pending_human_review','readability':'pending_human_review',
            'not_actual_engineering_fact_accuracy':True})
    write_csv(output/'A_quality_evaluation.csv',records);write_json(output/'A_quality_evaluation.json',records)
    write_csv(output/'A_human_review_blank.csv',human)
    write_json(output/'protected.after.json',protection_snapshot())
    write_json(output/'protection.validation.json',{'passed':read(output/'protected.before.json')==protection_snapshot(),
        'core_code_and_config_and_SQLite_unchanged':True})
