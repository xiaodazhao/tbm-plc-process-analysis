#!/usr/bin/env python3
"""Independent opt-in PLCResponse/semantic-relation pilot; offline unless --mode llm."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from tbm_process.evidence_graph.analysis import add_daily_analysis
from tbm_process.evidence_graph.catalog import build_catalog_graph
from tbm_process.evidence_graph.joint_reporting import (ModelSession, build_evidence_pack, run_writer,
    writer_facts, writing_requests)
from tbm_process.evidence_graph.queries import daily_subgraph, query_day
from tbm_process.evidence_graph.relations import (PROMPT_VERSION, attach_relations, build_candidates,
    check_relations, relation_request)
from tbm_process.evidence_graph.validation import validate_graph
from tbm_process.final_reporting.common import load_deepseek_api_key
from tbm_process.geology_store.database import GeologyStore
from tbm_process.reporting_v2.facts import build_facts, digest, write_json
from tbm_process.reporting_v2.narrative import offline_plan, offline_draft
from tbm_process.reporting_v2.rendering import render_figures, render_html, make_pdf, validate_artifacts
from tbm_process.reporting_v2.response_features import build_response_features


CASE_REASONS={
    '2024-04-26':'requested_trace_case_multiple_geological_partitions_and_measured_PLC_differences',
    '2024-04-25':'overlapping_forecast_descriptions_and_reported_grade_disagreement',
    '2023-11-18':'single_response_partition_no_valid_cross_partition_reference_control',
    '2024-03-15':'no_current_forecast_control_do_not_force_geology_machine_relationship'}


def protected_sources(args):
    paths=[]
    for module in ('ingestion','quality','process','dynamics','orchestration','outputs','contracts'):
        paths.extend((ROOT/'tbm_process'/module).rglob('*.py'))
    paths.extend([ROOT/'config.json',ROOT/'configs/platform_analysis.json',args.database,args.registry,args.config,args.policy])
    for day in args.dates:
        paths.extend([args.analysis_dir/'processed_samples'/f'{day}.csv.gz',
                      args.analysis_dir/'structured_full_days'/f'{day}.json'])
    paths.extend((args.analysis_dir/'tables').glob('*.csv'))
    return {str(p):digest(p) for p in paths if p.is_file()}


def save_csv(path, rows):
    if rows:
        pd.DataFrame(rows).to_csv(path,index=False,encoding='utf-8-sig')
    else:
        pd.DataFrame(columns=['date','status']).to_csv(path,index=False,encoding='utf-8-sig')


def render_case(package,samples,draft,folder,status,chrome=None):
    figures=render_figures(package,samples,folder)
    # Keep the established template; no new PDF/image extraction from source reports.
    html=render_html(package,draft,{'images':[],'unavailable':[]},folder,
                     {'mode':'real_llm_candidate_requires_semantic_review','status':status['status']})
    warning=('实验候选：程序检查已通过，完整工程语义仍待人工审查。' if status.get('program_checks_passed') else
             '研究失败稿：未通过程序事实检查，仅供审查，不得作为正式工程日报。')
    html.write_text(html.read_text(encoding='utf-8').replace('<body>','<body><p class="warning">'+warning+'</p>',1),encoding='utf-8')
    pdf=folder/(package['date']+'.candidate.pdf')
    make_pdf(html,pdf,chrome)
    check=validate_artifacts(package,draft,figures,pdf)
    check['release_status']='candidate_requires_semantic_review' if status.get('program_checks_passed') else 'failed_quality_for_research_only_not_approved'
    write_json(folder/'artifact.validation.json',check)
    markdown='# '+package['date']+' TBM施工日报（实验候选）\n\n'
    for ch in draft['chapters']:
        markdown+='## '+str(ch['chapter_id'])+'\n\n'+'\n\n'.join(c['text'] for c in ch['claims'])+'\n\n'
    (folder/'report_text.md').write_text(markdown,encoding='utf-8')
    return {'pdf':str(pdf),'artifact_checks_passed':check['passed'],'pages':check['pdf_pages']}


def run(args):
    if len(args.dates)>4 or len(set(args.dates))!=len(args.dates):
        raise ValueError('At most four unique dates are authorized')
    policy=json.loads(args.policy.read_text()); config=json.loads(args.config.read_text())
    if policy['llm']['maximum_calls']>24 or policy['llm']['base_url'].rstrip('/')!='https://api.deepseek.com' or policy['llm']['transport_retries']!=0:
        raise ValueError('Consent boundary: DeepSeek official, maximum24, no retries')
    prior_records=[r for path in args.prior_call_ledger for r in json.loads(path.read_text())]
    if len(prior_records)>=24:
        raise ValueError('Task-level 24-call budget already used')
    policy['llm']['maximum_calls']=min(policy['llm']['maximum_calls'],24-len(prior_records))
    key=load_deepseek_api_key(args.env_file) if args.mode=='llm' else None
    if args.mode=='llm' and not key:
        raise ValueError('No configured API key; no request sent')
    output=args.output_root/datetime.now().strftime('%Y%m%dT%H%M%S')
    output.mkdir(parents=True,exist_ok=False)
    policy_path=output/'method_config.json'; write_json(policy_path,{'response_policy':policy,'legacy_report_config':config})
    write_json(output/'budget.json',{'prior_ledger_paths':[str(p) for p in args.prior_call_ledger],
        'prior_calls':len(prior_records),'maximum_new_calls':policy['llm']['maximum_calls'],'task_cap':24})
    protected=protected_sources(args); write_json(output/'protected_sources.before.json',protected)
    write_json(output/'case_selection.json',{'dates':args.dates,'selection':'purposive_development_pilot_not_independent_accuracy_test',
        'reasons':{d:CASE_REASONS.get(d,'user_selected') for d in args.dates},
        'negative_control_limit':'No reference/no forecast is an evidence-availability control, not proof that the machine had no physical change.',
        'new_model_call_cap':24,'human_labels':None})
    print('OUTPUT_DIRECTORY='+str(output),flush=True)
    session=ModelSession(output,policy,key); registry=json.loads(args.registry.read_text())
    phase=json.loads((ROOT/'config.json').read_text()); store=GeologyStore(args.database)
    summaries=[]; comparisons=[]; manual=[]; coverage=[]; sensitivity=[]
    try:
        for day in args.dates:
            folder=output/day; folder.mkdir()
            catalog=store.export_catalog(day)
            write_json(folder/'time_admissible_catalog.json',catalog)
            graph=build_catalog_graph(catalog,registry,args.source_root,config.get('design_records',[]))
            package,samples=build_facts(report_day=day,project=ROOT,analysis=args.analysis_dir,
                catalog_path=folder/'time_admissible_catalog.json',registry_path=args.registry,source_root=args.source_root,config=config)
            write_json(folder/'legacy_report_facts.json',package)
            legacy_package_digest=digest(folder/'legacy_report_facts.json')
            add_daily_analysis(graph,package,samples,phase,response_policy=policy)
            validation=validate_graph(graph,day); write_json(folder/'graph.validation.json',validation)
            if not validation['passed']:
                raise ValueError('Graph structural validation failed for '+day)
            daily=build_response_features(samples,config,phase,response_id='DAILY:'+day,
                bounds=package['geology']['day_range_m'])
            write_json(folder/'daily_response_features.json',daily)
            samples[['time','source_file','source_row','chainage','machine_state','experimental_platform_id',
                'experimental_phase_label','advance_speed','thrust','torque','rpm_canonical','pr','fpi','se',
                'performance_eligible']].to_csv(folder/'source_sample_index.csv.gz',index=True,compression='gzip',encoding='utf-8')
            pack=build_evidence_pack(package,graph,daily,policy)
            if len({f['fact_id'] for f in pack['facts']})!=len(pack['facts']):
                raise ValueError('Duplicate evidence fact ID')
            write_json(folder/'evidence_pack.A.json',pack)
            candidates=build_candidates(graph,day,policy)
            writer_ids=set(pack['writer_fact_ids'])
            rejected=[r for r in candidates['candidates'] if not set(r['supporting_evidence_ids'])<=writer_ids]
            candidates['candidates']=[r for r in candidates['candidates'] if r not in rejected]
            candidates['omitted'].extend({'candidate_id':r['candidate_id'],'reason':'not_in_identical_A_B_writer_fact_pool'} for r in rejected)
            write_json(folder/'relation_candidates.json',candidates)
            for row in candidates['candidates']:
                manual.append({'date':day,'candidate_id':row['candidate_id'],'semantic_kind':row['semantic_kind'],
                    'evidence_ids':'|'.join(row['supporting_evidence_ids']),
                    'expert_relation_supported':'','expert_important_omission':'','expert_notes':''})
            for response in [{'id':'DAILY:'+day,'data':{'features_v2':daily,'summary':package['performance']['daily']}}]+list(graph.typed('PLCResponse')):
                d=response['data']; v=d['features_v2']; old=d['summary']
                for metric,s in v['metrics'].items():
                    coverage.append({'date':day,'response_id':response['id'],'metric':metric,
                        'legacy_count':old['sample_count'],'new_count':s['count'],'new_support_seconds':s['supported_duration_seconds'],
                        'legacy_median':old['statistics'][metric]['median'],'new_median':s['median'],
                        'window_coverage':v['window_coverage']['basic' if metric in ('advance_speed','thrust','torque') else 'performance']['described_ratio']})
                if 'comparisons_v2' in d:
                    comparisons.append({'date':day,'response_id':response['id'],'legacy':d['baseline_reference'],'new':d['comparisons_v2']})
                    sensitivity.extend({'date':day,'response_id':response['id'],**s} for s in d['comparisons_v2'].get('sensitivity',[]))
            q=query_day(graph,day)
            day_summary={'date':day,'platforms':package['platform_count'],'legacy_samples':package['performance']['daily']['sample_count'],
                'basic_samples':daily['basic_count'],'rpm_samples':daily['rpm_count'],'current_geo_facts':len(q['A_current_geology']['fact_ids']),
                'responses':len(q['B_PLC_response']['responses']),'candidate_count':len(candidates['candidates']),
                'all_candidate_count':candidates['all_candidate_count'],'groups':{}}
            if args.mode=='llm':
                request=relation_request(candidates,policy['llm']['model'],policy)
                if args.repair_from:
                    previous=args.repair_from/day
                    shutil.copytree(previous/'relations',folder/'relations')
                    payload=json.loads((previous/'relations/relations.payload.json').read_text())
                    write_json(folder/'plan_repair_source.json',{'prior_run':str(args.repair_from),
                        'relation_call_reused':True,'one_plan_repair_per_group':True})
                else:
                    payload=session.call(request,folder/'relations','relations') if candidates['candidates'] else None
                if payload is not None:
                    checked=check_relations(payload,candidates,graph,day,policy)
                else:
                    checked={'passed':False,'relations':[],'errors':['not_called_no_candidates' if not candidates['candidates'] else 'API_failed_or_unavailable'],
                             'human_labels':None,'semantic_status':'REVIEW'}
                write_json(folder/'relations'/'validation.json',checked)
                provenance={'model_requested':policy['llm']['model'],'prompt_version':PROMPT_VERSION,
                            'request_sha256':digest(folder/'relations'/'relations.request.json') if (folder/'relations'/'relations.request.json').exists() else None}
                if (folder/'relations'/'relations.metadata.json').exists():
                    provenance.update(json.loads((folder/'relations'/'relations.metadata.json').read_text()))
                admitted=attach_relations(graph,day,checked,provenance=provenance)
                bpack=deepcopy(pack)
                bpack['semantic_relation_candidates']=[{'relation_id':i,
                    'evidence_fact_ids':graph.nodes[i]['data']['proposal']['supporting_evidence_ids'],
                    'description':graph.nodes[i]['data']['proposal']['relation_description'],
                    'status':'REVIEW_only_layout_hint_not_verified_semantic_fact','proposal':graph.nodes[i]['data']['proposal'],
                    'llm_provenance':provenance} for i in admitted]
                write_json(folder/'evidence_pack.B.json',bpack)
                afacts,bfacts=writer_facts(pack),writer_facts(bpack)
                write_json(folder/'A_B_fairness.json',{'identical_writer_facts':afacts==bfacts,
                    'same_model_sampling':True,'only_semantic_relation_hints_differ':True,
                    'C_reuses_B_plan_and_original_draft':True})
                if afacts!=bfacts:
                    raise ValueError('A/B factual input pool changed')
                for group,p in [('A',pack),('B',bpack)]:
                    print(day+' '+group+' planning/generation',flush=True)
                    rejected=json.loads((args.repair_from/day/group/'plan.payload.json').read_text()) if args.repair_from else None
                    if args.repair_from:
                        old_pack=json.loads((args.repair_from/day/f'evidence_pack.{group}.json').read_text())
                        if writer_facts(old_pack)!=writer_facts(p):
                            raise ValueError('Plan repair must use identical factual input')
                    draft,status=run_writer(p,folder/group,policy,session,rejected_plan=rejected)
                    if draft:
                        status.update(render_case(package,samples,draft,folder/group,status,args.chrome))
                    day_summary['groups'][group]=status
                if (folder/'B/draft.before.json').exists() and (folder/'B/content_plan.json').exists():
                    draft,status=run_writer(bpack,folder/'C',policy,session,revise=True,reuse={
                        'source':str(folder/'B'),'plan':json.loads((folder/'B/content_plan.json').read_text()),
                        'draft':json.loads((folder/'B/draft.before.json').read_text())})
                    if draft:
                        status.update(render_case(package,samples,draft,folder/'C',status,args.chrome))
                    day_summary['groups']['C']=status
                else:
                    day_summary['groups']['C']={'status':'not_run_B_generation_unavailable','actual_llm_report':False}
                    write_json(folder/'C/status.json',day_summary['groups']['C'])
                day_summary['relations']={'proposed':len(checked['relations']),
                    'structurally_admitted':len(admitted),'semantic_truth':'pending_independent_review'}
            else:
                facts,pr,gr=writing_requests(pack,policy)
                write_json(folder/'offline_plan.request.NOT_SENT.json',pr)
                write_json(folder/'offline_generation.request.NOT_SENT.json',gr)
                plan=offline_plan(facts); draft=offline_draft(plan,facts)
                write_json(folder/'offline_preview.NOT_LLM.json',draft)
                day_summary['groups']={'offline':{'actual_llm_report':False,'API_calls':0}}
            # The new layer may add graph/fact objects, never rewrite the source package.
            day_summary['legacy_input_file_unchanged']=digest(folder/'legacy_report_facts.json')==legacy_package_digest
            write_json(folder/'daily_evidence_graph.json',daily_subgraph(graph,day).snapshot())
            write_json(folder/'summary.json',day_summary); summaries.append(day_summary)
            write_json(output/'summary.partial.json',summaries)
            print(json.dumps(day_summary,ensure_ascii=False),flush=True)
    finally:
        store.close()
        write_json(output/'model_calls.json',session.records)
        write_json(output/'summary.partial.json',summaries)
    save_csv(output/'coverage_comparison.csv',coverage)
    save_csv(output/'threshold_sensitivity.csv',sensitivity)
    save_csv(output/'human_relation_review.BLANK.csv',manual)
    write_json(output/'segment_comparisons.json',comparisons)
    after={p:digest(Path(p)) for p in protected}
    write_json(output/'protected_sources.after.json',after)
    summary={'mode':args.mode,'dates':summaries,'actual_new_model_calls':len(session.records),
        'input_tokens':sum(r.get('input_tokens') or 0 for r in session.records),
        'output_tokens':sum(r.get('output_tokens') or 0 for r in session.records),
        'failed_calls':sum(r['status']!='success' for r in session.records),
        'protected_sources_unchanged':protected==after,
        'human_semantic_accuracy':None,'controlled_performance_comparison':'not_established_unverified_control_fields',
        'billing_cost':'not_available_from_API_response_no_fabricated_currency_amount',
        'conclusion_scope':'small_development_pilot_program_consistency_not_engineering_accuracy_or_causal_inference'}
    write_json(output/'summary.json',summary)
    source=output/'source_code'
    for relative in ['scripts/run_response_relation_pilot.py','configs/plc_response_relations.json',
        'tbm_process/reporting_v2/response_features.py','tbm_process/reporting_v2/performance.py',
        'tbm_process/evidence_graph/analysis.py','tbm_process/evidence_graph/relations.py',
        'tbm_process/evidence_graph/joint_reporting.py','tests/test_response_relations.py']:
        target=source/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ROOT/relative,target)
    write_json(output/'MANIFEST.json',[{'path':str(p.relative_to(output)),'bytes':p.stat().st_size,'sha256':digest(p)}
        for p in sorted(output.rglob('*')) if p.is_file() and p.name!='MANIFEST.json'])
    print('FINAL_SUMMARY='+json.dumps(summary,ensure_ascii=False),flush=True)
    return output


def rerender_existing(root,chrome=None):
    """Fix display provenance without new API calls or replacing original artifacts."""
    from tbm_process.reporting_v2.performance import calculate_metrics
    root=Path(root); results=[]
    for facts in sorted(root.glob('*/legacy_report_facts.json')):
        package=json.loads(facts.read_text())
        samples,_=calculate_metrics(pd.read_csv(package['source']['samples'],low_memory=False),package['config'])
        for narrative in sorted(facts.parent.glob('[ABC]/narrative.json')):
            status=json.loads((narrative.parent/'status.json').read_text()); before=digest(narrative)
            folder=narrative.parent/'display_corrected'; folder.mkdir(exist_ok=False)
            result=render_case(package,samples,json.loads(narrative.read_text()),folder,status,chrome)
            record={'date':package['date'],'group':narrative.parent.name,'source_narrative_sha256':before,
                'source_narrative_unchanged':digest(narrative)==before,'reason':'correct_render_mode_label_and_visible_quality_status_only',**result}
            write_json(folder/'display_correction.json',record); results.append(record)
    write_json(root/'display_corrections.json',results)
    print(json.dumps(results,ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['offline','llm'],default='offline')
    p.add_argument('--dates',nargs='+',default=list(CASE_REASONS))
    p.add_argument('--analysis-dir',type=Path,default=ROOT/'experiments/plc_mapping_repair_v1/replay/after')
    p.add_argument('--database',type=Path,default=Path.home()/'Desktop/TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite')
    p.add_argument('--registry',type=Path,default=Path.home()/'Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json')
    p.add_argument('--source-root',type=Path,default=Path.home()/'Desktop/伯舒拉岭数据')
    p.add_argument('--config',type=Path,default=ROOT/'tbm_process/reporting_v2/defaults.json')
    p.add_argument('--policy',type=Path,default=ROOT/'configs/plc_response_relations.json')
    p.add_argument('--output-root',type=Path,default=ROOT/'outputs/plc_response_relations_v2')
    p.add_argument('--env-file',type=Path)
    p.add_argument('--prior-call-ledger',nargs='*',type=Path,default=[])
    p.add_argument('--repair-from',type=Path,help='Explicit one-off repair of saved rejected plans; reuse relations, never rerun that API call')
    p.add_argument('--chrome',type=Path)
    p.add_argument('--rerender-from',type=Path,help='Offline display-label correction into NEW child folders; never replace model text or originals')
    args=p.parse_args()
    if args.rerender_from:
        rerender_existing(args.rerender_from,args.chrome)
    else:
        run(args)


if __name__=='__main__':
    main()
