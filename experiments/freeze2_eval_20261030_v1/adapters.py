"""Pre-registered A-D adapters around the frozen planner, writer and editor."""
from copy import deepcopy
import difflib
import json
from pathlib import Path
import re
import shutil

from common import *
from tbm_process.engineering_reporting import writer, editor
from tbm_process.engineering_reporting.schema import TITLES

MODEL='deepseek-v4-flash'
FIXED_PLAN_RULE={
    'selection':'all_required_plus_first_two_nonduplicate_optional_per_section_in_input_order',
    'optional_limit_per_section':2,
    'grouping':'conditional_references_separate; activities_then_windows_then_other; remaining_same_section_combined',
    'source_of_plan':'Python_only_no_LLM',
}


def fixed_plan(pack):
    sections=[];omissions=[]
    for sid in TITLES:
        fs=[f for f in pack['facts'] if f['section']==sid]
        selected=[f for f in fs if f['required']]
        texts={f['canonical_text'] for f in selected};n=0
        for f in fs:
            if f['required']:continue
            if f['canonical_text'] not in texts and n<FIXED_PLAN_RULE['optional_limit_per_section']:
                selected.append(f);texts.add(f['canonical_text']);n+=1
            else:omissions.append({'fact_id':f['fact_id'],'reason':'fixed_input_order_optional_budget_or_identical_text'})
        groups={}
        for f in selected:
            group=('conditional_reference' if f['role']=='conditional_design_reference_not_implementation' else
                   'activity' if f['fact_id'].startswith('ACTIVITY:') else
                   'window' if f['fact_id'].startswith('JOINT_WINDOW:') else 'section_evidence')
            groups.setdefault(group,[]).append(f['fact_id'])
        sections.append({'section_id':sid,'paragraphs':[{'purpose':TITLES[sid]+'：'+g,'fact_ids':ids} for g,ids in groups.items()]})
    return {'sections':sections,'omissions':omissions}


def direct_request(pack):
    req=writer.request('write',pack,plan=None)
    old='严格使用已核查内容规划，返回JSON'
    assert old in req['messages'][0]['content']
    req['messages'][0]['content']=req['messages'][0]['content'].replace(old,'根据同一事实集合直接组织完整日报，不先输出提纲；返回JSON',1)
    req['messages'][0]['content']=req['messages'][0]['content'].replace('全篇保留规划采用的必要证据，可跨节组织，段落绑定实际使用的来源ID。',
        '每段绑定实际采用的事实ID，required信息必须保留，允许跨节组织。',1)
    req['messages'][0]['content']=req['messages'][0]['content'].replace('其余被规划的支持ID仍需保留','其余必选信息仍需保留')
    req['messages'][0]['content']=req['messages'][0]['content'].replace('其余被规划的支持ID全篇保留即可','其余必选信息全篇保留即可')
    payload=json.loads(req['messages'][1]['content']);payload.pop('approved_plan')
    req['messages'][1]['content']=json.dumps(payload,ensure_ascii=False)
    return req


def materialized_plan(draft, pack):
    """Evaluation-only binding for A, not a hidden plan supplied to its model."""
    sections=[];seen=set()
    for s in draft.get('sections',[]):
        ps=[]
        for p in s.get('paragraphs',[]):
            ids=p.get('fact_ids',[]);seen.update(ids)
            ps.append({'purpose':'posthoc_output_binding_not_pre_generation_planning','fact_ids':ids})
        sections.append({'section_id':s.get('section_id'),'paragraphs':ps})
    return {'sections':sections,'omissions':[{'fact_id':f['fact_id'],'reason':'not_bound_in_direct_output_evaluation_only'} for f in pack['facts'] if f['fact_id'] not in seen]}


def validate(draft,pack,plan):
    try:return writer.validate_text(draft,pack,plan)
    except Exception as exc:
        return {'passed':False,'errors':['validator_interface_'+type(exc).__name__],
                'not_engineering_fact_accuracy':True,'review_required':['semantic_human_review_required']}


def input_folder(day):
    return HERE/'inputs'/day


def group_folder(scope, day, arm):
    # The renderer uses folder.name as the date.
    return HERE/'runs'/scope/arm/day


def create_bundle(day, scope, arm):
    source=input_folder(day);folder=group_folder(scope,day,arm)
    if folder.exists():raise RuntimeError('group_folder_exists_no_overwrite')
    folder.mkdir(parents=True)
    for p in source.iterdir():
        if p.is_dir():shutil.copytree(p,folder/p.name)
        elif p.name!='preparation.result.json':
            if p.name in {'processed_samples.csv.gz','local_reference_vectors.npz'}:
                os.link(p,folder/p.name)
            else:shutil.copy2(p,folder/p.name)
    save(folder/'input_reference.json',{'input_folder':str(source),'input_sha256':sha(source/'unified_report_input.json'),
        'large_samples_are_read_only_hardlinks':True})
    return folder


def render(folder):
    from tbm_process.engineering_reporting.rendering import render_report
    try:
        result=render_report(folder)
        save(folder/'render.result.json',result)
        return {'PDF_generated':True,'artifact_check_passed':result['technical_passed'],
            'PDF_text_same_as_checked_model_text':result['PDF_text_same_as_checked_model_text'],
            'pages':result['pages'],'pdf':result['pdf']}
    except Exception as exc:
        failure={'PDF_generated':False,'render_error_class':type(exc).__name__}
        save(folder/'render.failure.json',failure);return failure


def ensure_input(day):
    p=input_folder(day);result=load(p/'preparation.result.json')
    if result['status']!='PREPARED':raise RuntimeError('PREPARATION_FAILED')
    if sha(p/'unified_report_input.json')!=result['input_sha256']:raise RuntimeError('locked_input_changed')
    return load(p/'unified_report_input.json')


def finish(folder, draft, pack, plan, extra=None):
    check=validate(draft,pack,plan)
    save(folder/'final_narrative.json',draft);save(folder/'final_narrative.validation.json',check)
    save(folder/'content_plan.json',plan)
    from tbm_process.engineering_reporting.delivery_status import decide
    decide(folder,check)
    has_text=any(p.get('text','').strip() for s in draft.get('sections',[]) for p in s.get('paragraphs',[]))
    result={'status':'GENERATED' if has_text else 'EMPTY_STRUCTURED_OUTPUT','body_rules_passed':check['passed'],
        'body_errors':check['errors'],'semantic_status':'NOT_EVALUATED',**(extra or {})}
    result.update(render(folder) if has_text else {'PDF_generated':False})
    save(folder/'result.json',result);return result


def run_date(day, scope):
    request_status=load(HERE/'prepared_requests'/day/'request_status.json')
    if request_status['status']!='REQUESTS_PREPARED':
        for arm in 'ABCD':
            folder=group_folder(scope,day,arm);folder.mkdir(parents=True,exist_ok=True)
            if not (folder/'result.json').exists():
                save(folder/'result.json',{'status':request_status['status'],'reason':request_status,
                    'semantic_status':'NOT_EVALUATED','date_not_replaced':True})
        return
    try:pack=ensure_input(day)
    except Exception as exc:
        for arm in 'ABCD':
            folder=group_folder(scope,day,arm);folder.mkdir(parents=True,exist_ok=True)
            if not (folder/'result.json').exists():save(folder/'result.json',{'status':'INPUT_UNAVAILABLE','reason':type(exc).__name__,'semantic_status':'NOT_EVALUATED'})
        return
    for arm in ['A','B','C']:
        folder=group_folder(scope,day,arm)
        if (folder/'result.json').exists():continue
        if folder.exists():
            # Interrupted API attempts are retained rather than repeated silently.
            save(folder/'result.json',{'status':'INTERRUPTED_REQUIRES_AUDIT','semantic_status':'NOT_EVALUATED'});continue
        folder=create_bundle(day,scope,arm)
        try:
            if arm=='A':
                draft=call(scope,day,arm,'generation',direct_request(pack),folder)
                plan=materialized_plan(draft,pack)
                save(folder/'A_plan_binding_is_posthoc.json',{'not_model_planning':True,'not_supplied_to_A_model':True})
            else:
                if arm=='B':plan=fixed_plan(pack);save(folder/'fixed_rule_definition.json',FIXED_PLAN_RULE)
                else:
                    plan=call(scope,day,arm,'plan',writer.request('plan',pack),folder)
                    initial=writer.validate_plan(plan,pack)
                    save(folder/'plan.initial.json',plan);save(folder/'plan.initial.validation.json',initial)
                    if not initial['passed']:
                        req=writer.request('plan',pack)
                        req['messages'].append({'role':'assistant','content':json.dumps(plan,ensure_ascii=False)})
                        req['messages'].append({'role':'user','content':'仅修复以下提纲检查问题，仍返回完整规划JSON：'+json.dumps(initial['errors'],ensure_ascii=False)})
                        plan=call(scope,day,arm,'plan_repair',req,folder)
                pc=writer.validate_plan(plan,pack);save(folder/'content_plan.json',plan);save(folder/'content_plan.validation.json',pc)
                if not pc['passed'] and arm=='C':
                    from tbm_process.engineering_reporting.delivery_status import rule_plan
                    save(folder/'plan.failed.json',{'plan':plan,'validation':pc})
                    plan=rule_plan(pack);pc=writer.validate_plan(plan,pack)
                    save(folder/'plan.fallback.json',{'LLM_plan_succeeded':False,'method':'frozen_same_pool_rule_fallback'})
                    save(folder/'content_plan.json',plan);save(folder/'content_plan.validation.json',pc)
                if not pc['passed']:
                    save(folder/'result.json',{'status':'PLAN_FAILED','plan_errors':pc['errors'],'semantic_status':'NOT_EVALUATED'});continue
                draft=call(scope,day,arm,'generation',writer.request('write',pack,plan=plan),folder)
            finish(folder,draft,pack,plan)
        except Exception as exc:
            save(folder/'result.json',{'status':'FAILED','error_class':type(exc).__name__,'semantic_status':'NOT_EVALUATED'})
    d=group_folder(scope,day,'D')
    if (d/'result.json').exists():return
    if d.exists():
        save(d/'result.json',{'status':'INTERRUPTED_REQUIRES_AUDIT','semantic_status':'NOT_EVALUATED'});return
    d=create_bundle(day,scope,'D');c=group_folder(scope,day,'C')
    if not (c/'final_narrative.json').exists():
        save(d/'result.json',{'status':'BLOCKED_BY_C','semantic_status':'NOT_EVALUATED'});return
    try:
        draft=load(c/'final_narrative.json');plan=load(c/'content_plan.json')
        save(d/'content_plan.initial.json',plan);save(d/'narrative.initial.json',draft)
        save(d/'shared_C_provenance.json',{'plan_path':str(c/'content_plan.json'),'plan_sha256':sha(c/'content_plan.json'),
            'draft_path':str(c/'final_narrative.json'),'draft_sha256':sha(c/'final_narrative.json'),'no_new_planning_or_initial_generation':True})
        check=validate(draft,pack,plan);save(d/'narrative.initial.validation.json',check)
        from tbm_process.engineering_reporting import pipeline,model
        original=model.model_stage
        def transport(stage,request,folder,key,ledger):
            try:
                value=call(scope,day,'D',stage,request,folder)
            except Exception:
                ledger.append({'stage':stage,'success':False,'see_SQLite_ledger':True})
                return None
            ledger.append({'stage':stage,'success':True,'see_SQLite_ledger':True})
            return value
        model.model_stage=transport
        try:
            pipeline.generate(d,'credential_is_read_by_transport_not_passed_here',MODEL,
                              seed_plan=deepcopy(plan),seed_draft=deepcopy(draft))
        finally:model.model_stage=original
        draft=load(d/'final_narrative.json');plan=load(d/'content_plan.json')
        if (d/'editor.comparison.json').exists():
            save(d/'narrative.after_editor.json',load(d/'editor.comparison.json')['after'])
        repaired=(d/'targeted_revision.request.json').exists()
        finish(d,draft,pack,plan,{'revision_triggered':repaired,'C_initial_draft_reused':True,
            'failed_body_PDF_is_review_artifact_not_production_release':True})
    except Exception as exc:
        save(d/'result.json',{'status':'FAILED','error_class':type(exc).__name__,'semantic_status':'NOT_EVALUATED'})


def relation_pair_tasks(pack):
    """Focused ablation: identical component evidence, relation obligations withheld."""
    base=writer.model_facts(pack)
    byid={f['fact_id']:f for f in pack['facts']}
    relations=[]
    for f in pack['facts']:
        if f['fact_id']=='WITHIN_BACKGROUND_RESPONSE':
            d=f['data'];ids=d['shared_geology_fact_ids']
            raw=[]
            for label in ['reference','current']:
                p=d[label];raw.append({'record_id':p['response_id'],'bounds_m':p['bounds_m'],
                    'time_range':p['activity_time_range'],'sample_count':p['sample_count'],
                    'supported_seconds':p['supported_seconds'],
                    'metrics':{k:{key:v[key] for key in ['median','p10','p90','unit','count']} for k,v in p['metrics'].items()}})
            numerical_changes={'record_id':'precomputed_differences','changes':{
                k:{key:v[key] for key in ['current','reference','difference','percent','unit']}
                for k,v in d['changes'].items()}}
            relations.append({'id':f['fact_id'],'components':raw+[numerical_changes]+[{'record_id':i,'text':byid[i]['canonical_text']} for i in ids],
                              'compiled_relation':writer.model_facts({'facts':[f],'report_organization':pack.get('report_organization')})[0]['canonical_text']})
    # Fall back to a real two-source overlapping forecast pair; all original scopes remain visible.
    if not relations:
        forecasts=[f for f in pack['facts'] if f['role']=='prior_forecast' and f['section'] in ['3.1','3.3']]
        for i,left in enumerate(forecasts):
            for right in forecasts[i+1:]:
                a,b=left['data'],right['data'];ar=a.get('original_range_m');br=b.get('original_range_m')
                if not ar or not br or left['fact_id']==right['fact_id']:continue
                lo,hi=max(ar[0],br[0]),min(ar[1],br[1])
                if lo>=hi:continue
                relations.append({'id':'source_overlap','components':[
                    {'record_id':left['fact_id'],'text':left['canonical_text'],'range_m':ar},
                    {'record_id':right['fact_id'],'text':right['canonical_text'],'range_m':br}],
                    'compiled_relation':{'positive_overlap_m':[lo,hi],
                        'same_source_not_independent_observation':a.get('document_id')==b.get('document_id'),
                        'both_predictions_not_today_observations':True}})
                break
            if relations:break
    if not relations:return None
    # This is a narrowly scoped relation-to-paragraph test, not an extra full A-D report.
    components=relations[0]['components']
    system=('依据给定真实组件写一段地质与机器资料综合说明。只使用提供数字和来源，保留资料性质及局部范围，不推断地质因果。'
            '返回JSON {text,source_ids:[],limitations:[]}。约150至250个汉字，不输出提纲。')
    minus={'components':components,'compiled_relationships':[]}
    plus={'components':components,'compiled_relationships':[relations[0]['compiled_relation']]}
    def req(payload):
        return {'model':MODEL,'temperature':.1,'max_tokens':8000,'response_format':{'type':'json_object'},
            'extra_body':{'thinking':{'type':'disabled'}},'messages':[{'role':'system','content':system},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
    return {'REL+':req(plus),'REL-':req(minus),'components_sha256':digest(components),
        'scope':'focused_paragraph_relation_compilation_ablation_not_full_report_not_graph_storage_comparison',
        'components':components}
