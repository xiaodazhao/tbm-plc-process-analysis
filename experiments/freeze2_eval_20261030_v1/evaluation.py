"""Independent source/field audits, descriptive metrics and empty human instruments."""
from collections import Counter
from copy import deepcopy
import difflib
import json
import math
from pathlib import Path
import random
import re
import secrets
import shutil

import numpy as np
import pandas as pd

from common import *
from adapters import group_folder, materialized_plan, validate


HUMAN_FIELDS=['support_label','reviewer_id','judgment_reason','source_ID_reachable','page_and_chainage_correct',
              'source_substantively_supports','error_type','adjudication']


def requirements(day,pack):
    """Input-based draft requirements; a real reviewer must confirm applicability."""
    folder=HERE/'inputs'/day;o=load(folder/'report_facts.original.json')['daily_overview']
    rows=[]
    def add(kind,text,basis):
        rows.append({'date':day,'requirement_id':f'{day}:REQ:{len(rows)+1:03d}',
            'requirement_type':kind,'requirement':text,'evidence':basis,
            'definition_status':'PROVISIONAL_REQUIRES_HUMAN_CONFIRMATION',
            'human_applicable':'','human_reason':'','not_copied_from_generator_required':True})
    for kind,text in [('overview','日期、起终盾首里程和按既定口径的日进尺'),('time','累计推进及观测非推进时间、正确有效支持分母'),
                      ('quality','主要未覆盖时间及不能将其算入停机'),('operation','有依据概括主要施工活动及参数组合')]:
        add(kind,text,{'overview':o,'source':load(folder/'report_facts.original.json')['source']})
    fs=load(folder/'admissible_forecasts.json')
    for f in fs:
        if f['ahead_intersection_m']:
            add('ahead_forecast','前方窗口内该来源的有效覆盖及主要地质条件；来源重叠可合并但性质保留',f)
    risk=load(folder/'source_risk_facts.json')
    for f in risk['records']:
        if f['scope']=='ahead' and (f['kind']=='risk_point' or f.get('hazards')):
            add('local_attention','保留有原始位置及内容的前方局部关注信息',f)
    add('coverage','准确交代前方20m的预测覆盖及空档，不与设计背景混淆',pack['coverage']['ahead'])
    add('evidence_role','正确区分设计、预测和历史记录，不将其写成当日揭示',
        [{'role':f['role'],'sources':f['sources']} for f in pack['facts'] if f['section'].startswith('3')])
    if load(folder/'report_organization.json')['operation']['comparison']:
        add('operation_comparison','所选前后活动比较应说明参数组合及共同原预报背景，避免地质致因',
            load(folder/'report_organization.json')['operation']['comparison'])
    return rows


def input_audit():
    import fitz
    entries=[];checks=[];reqs=[];page_cache={};hash_cache={}
    days=DEVELOPMENT+load(HERE/'SAMPLING.json')['formal_dates']
    norm=lambda s:re.sub(r'\s+','',str(s))
    for day in days:
        folder=HERE/'inputs'/day
        if load(folder/'preparation.result.json')['status']!='PREPARED':continue
        pack=load(folder/'unified_report_input.json');reqs+=requirements(day,pack)
        samples=pd.read_csv(folder/'processed_samples.csv.gz')
        dt=pd.to_numeric(samples.sample_dt_seconds,errors='coerce').fillna(0)
        o=load(folder/'report_facts.original.json')['daily_overview']
        for name,value,expected in [('record_count',len(samples),o['record_count']),
            ('effective_support_seconds',float(dt.sum()),o['effective_state_duration_seconds']),
            ('advancing_seconds',float(dt.where(samples.machine_state.eq('advancing'),0).sum()),o['advancing_seconds'])]:
            checks.append({'date':day,'check':name,'independent_value':value,'stored_value':expected,
                           'passed':math.isclose(value,expected,abs_tol=1e-6),'method':'independent_pandas_sum_over_saved_rows_not_production_verifier'})
        for f in pack['facts']:
            for src in f['sources']:
                p=Path(src['path']);exists=p.is_file()
                if exists and str(p) not in hash_cache:hash_cache[str(p)]=sha(p)
                matches=exists and hash_cache[str(p)]==src.get('sha256')
                page=src.get('PDF_page',src.get('page'));quote=src.get('quote')
                page_status='NOT_APPLICABLE';quote_status='NOT_EVALUATED';excerpt=''
                if p.suffix.lower()=='.pdf' and page is not None and exists:
                    key=(str(p),int(page))
                    if key not in page_cache:
                        with fitz.open(p) as pdf:
                            page_cache[key]=pdf[int(page)-1].get_text() if 1<=int(page)<=len(pdf) else None
                    text=page_cache[key];page_status='PAGE_EXISTS' if text is not None else 'PAGE_OUT_OF_RANGE'
                    excerpt=(text or '')[:3000]
                    if quote and text is not None:
                        quote_status='EXACT_WHITESPACE_NORMALIZED_TEXT_FOUND' if norm(quote) in norm(text) else 'NOT_EXACTLY_FOUND_REQUIRES_REVIEW'
                entries.append({'date':day,'fact_id':f['fact_id'],'section':f['section'],'role':f['role'],
                    'original_file':str(p),'sha256':src.get('sha256'),'file_exists':exists,'SHA_matches':matches,
                    'PDF_physical_page':page,'page_check':page_status,'quote_check':quote_status,
                    'original_quote':quote,'page_text_excerpt':excerpt,'structured_fact':f['canonical_text'],
                    'actual_scope_data':{k:v for k,v in f['data'].items() if k in
                        ['original_range_m','day_intersection_m','ahead_intersection_m','time','bounds_m','comparison_status']},
                    'complete_fact_data_in_locked_input':str(folder/'unified_report_input.json'),
                    'check_method':'independent_file_hash_and_PyMuPDF_text_not_generation_validator',
                    'semantic_result':'REQUIRES_HUMAN_REVIEW','human_judgment':''})
    csv_write(HERE/'SOURCE_EVIDENCE_AUDIT.csv',entries)
    csv_write(HERE/'INDEPENDENT_NUMERICAL_REPLAY.csv',checks)
    csv_write(HERE/'PROVISIONAL_INFORMATION_REQUIREMENTS.csv',reqs)
    save(HERE/'SOURCE_AUDIT_SUMMARY.json',{'source_links':len(entries),'all_file_hash_links_match':all(r['SHA_matches'] for r in entries),
        'source_link_is_not_independent_fact':True,'quote_checks':dict(Counter(r['quote_check'] for r in entries)),
        'numerical_checks':len(checks),'numerical_passes':sum(r['passed'] for r in checks),
        'human_metrics':'NOT_EVALUATED','no_expert_labels_inferred':True})


def paragraphs(draft):
    return [(s.get('section_id'),i,p) for s in draft.get('sections',[]) for i,p in enumerate(s.get('paragraphs',[]))]


def text_of(draft):
    return '\n'.join(p['text'] for _,_,p in paragraphs(draft))


def sentence_rows(text):
    return [s.strip() for s in re.split('[。；\n]+',text) if s.strip()]


def metrics(scope,day,arm):
    folder=group_folder(scope,day,arm)
    result=load(folder/'result.json') if (folder/'result.json').exists() else {'status':'NOT_RUN'}
    row={'scope':scope,'date':day,'arm':arm,'status':result['status'],
         'input_prepared':load(HERE/'inputs'/day/'preparation.result.json')['status']=='PREPARED',
         'generated':(folder/'final_narrative.json').exists(),'PDF_generated':result.get('PDF_generated',False),
         'body_rules_passed':result.get('body_rules_passed',False),
         'artifact_check_passed':result.get('artifact_check_passed',False),
         'revision_triggered':result.get('revision_triggered',False),
         'human_fact_support':'NOT_EVALUATED','human_required_coverage':'NOT_EVALUATED',
         'human_readability':'NOT_EVALUATED','human_substantive_attribution':'NOT_EVALUATED'}
    if row['generated']:
        draft=load(folder/'final_narrative.json');pack=load(folder/'unified_report_input.json')
        row['generated']=any(p.get('text','').strip() for _,_,p in paragraphs(draft))
        plan=load(folder/'content_plan.json');check=validate(draft,pack,plan);text=text_of(draft)
        sentences=sentence_rows(text);bound=[i for _,_,p in paragraphs(draft) for i in p.get('fact_ids',[])]
        known={f['fact_id'] for f in pack['facts']};required={f['fact_id'] for f in pack['facts'] if f['required']}
        visual=set()
        if (folder/'table_support.validation.json').exists():
            t=load(folder/'table_support.validation.json')
            if t['passed']:visual=set(t['fact_ids'])
        seen=set(bound)|visual
        row.update({'characters':len(text),'paragraphs':len(paragraphs(draft)),'sentences':len(sentences),
            'exact_sentence_duplicate_ratio':1-len(set(sentences))/len(sentences) if sentences else None,
            'rule_error_count':len(check['errors']),'rule_errors':check['errors'],
            'declared_fact_ID_count':len(bound),'known_fact_ID_count':sum(i in known for i in bound),
            'known_fact_ID_ratio':sum(i in known for i in bound)/len(bound) if bound else None,
            'generator_required_ID_numerator':len(required&seen),'generator_required_ID_denominator':len(required),
            'generator_required_ID_coverage_NOT_independent_coverage':len(required&seen)/len(required) if required else None})
    return row


def collect_metrics():
    sampling=load(HERE/'SAMPLING.json');rows=[];changes=[]
    for scope,days in [('interface',DEVELOPMENT),('formal',sampling['formal_dates'])]:
        for day in days:
            rows += [metrics(scope,day,arm) for arm in 'ABCD']
            d=group_folder(scope,day,'D');c=group_folder(scope,day,'C')
            if not (c/'final_narrative.json').exists():continue
            before=load(c/'final_narrative.json')
            for stage,path in [('editor',d/'narrative.after_editor.json'),('final',d/'final_narrative.json')]:
                if not path.exists():continue
                after=load(path)
                if not isinstance(after,dict):continue
                ids=lambda x:{i for _,_,p in paragraphs(x) for i in p.get('fact_ids',[])}
                bi,ai=ids(before),ids(after);bs,ass=set(sentence_rows(text_of(before))),set(sentence_rows(text_of(after)))
                changes.append({'scope':scope,'date':day,'stage':stage,'before_IDs':len(bi),'after_IDs':len(ai),
                    'retained_bound_IDs':len(bi&ai),'deleted_bound_IDs':sorted(bi-ai),'added_bound_IDs':sorted(ai-bi),
                    'unchanged_exact_sentences':len(bs&ass),'before_sentences':len(bs),'after_sentences':len(ass),
                    'text_similarity_not_factual_retention':difflib.SequenceMatcher(None,text_of(before),text_of(after)).ratio(),
                    'errors_fixed':'NOT_EVALUATED','new_errors':'NOT_EVALUATED','correct_information_retained':'NOT_EVALUATED',
                    'correct_information_deleted':'NOT_EVALUATED','not_actual_information_retention_rate':True})
    csv_write(HERE/'METRICS_RAW.csv',rows);csv_write(HERE/'EDIT_RETENTION_PROXY.csv',changes)
    allcalls=Calls('formal').rows();csv_write(HERE/'MODEL_CALLS.csv',allcalls)
    save(HERE/'MODEL_CALLS.json',allcalls)
    summaries=[]
    for scope,days in [('interface',DEVELOPMENT),('formal',sampling['formal_dates'])]:
        for arm in 'ABCD':
            rr=[r for r in rows if r['scope']==scope and r['arm']==arm]
            cc=[r for r in allcalls if r['scope']==scope and r['arm']==arm]
            summaries.append({'scope':scope,'arm':arm,'locked_dates':len(days),
                'input_prepared':sum(r['input_prepared'] for r in rr),'generated':sum(r['generated'] for r in rr),
                'PDF_generated':sum(r['PDF_generated'] for r in rr),'body_rules_passed':sum(r['body_rules_passed'] for r in rr),
                'artifact_check_passed':sum(r['artifact_check_passed'] for r in rr),'revisions_triggered':sum(r['revision_triggered'] for r in rr),
                'actual_calls_marginal':len(cc),'input_tokens_marginal':sum(r.get('input_tokens') or 0 for r in cc),
                'output_tokens_marginal':sum(r.get('output_tokens') or 0 for r in cc),
                'HTTP_or_parse_failures':sum(r['status']!='SUCCESS' for r in cc),
                'human_fact_support':'NOT_EVALUATED','human_coverage':'NOT_EVALUATED','human_readability':'NOT_EVALUATED'})
    csv_write(HERE/'ARM_SUMMARY.csv',summaries)
    cost=[]
    for scope,days in [('interface',DEVELOPMENT),('formal',sampling['formal_dates'])]:
        for day in days:
            own=[r for r in allcalls if r['scope']==scope and r['date']==day and r['arm']=='D']
            shared=[r for r in allcalls if r['scope']==scope and r['date']==day and r['arm']=='C']
            cost.append({'scope':scope,'date':day,'D_marginal_calls':len(own),'D_standalone_call_equivalent':len(own+shared),
                'D_standalone_input_token_equivalent':sum(r.get('input_tokens') or 0 for r in own+shared),
                'D_standalone_output_token_equivalent':sum(r.get('output_tokens') or 0 for r in own+shared),
                'D_standalone_is_equivalent_accounting_not_additional_HTTP':True})
    csv_write(HERE/'D_COST_ACCOUNTING.csv',cost)
    paired_statistics(rows,sampling)
    return rows,summaries


def paired_statistics(rows,sampling):
    output=[];lookup={(r['date'],r['arm']):r for r in rows if r['scope']=='formal'}
    sharing=csv_read(HERE/'DATE_SOURCE_SHARING.csv')
    parent={d:d for d in sampling['formal_dates']};owners={}
    def find(day):
        while parent[day]!=day:
            parent[day]=parent[parent[day]];day=parent[day]
        return day
    for r in sharing:
        day=r['date']
        if day not in parent:continue
        doc=r['forecast_document_id']
        if doc in owners:parent[find(day)]=find(owners[doc])
        else:owners[doc]=day
    cluster={d:find(d) for d in parent}
    for control,treatment in [('B','C'),('C','D'),('A','D')]:
        for metric in ['generated','PDF_generated','body_rules_passed','artifact_check_passed','characters','exact_sentence_duplicate_ratio']:
            pairs=[]
            for day in sampling['formal_dates']:
                a,b=lookup[(day,control)].get(metric),lookup[(day,treatment)].get(metric)
                if a is None or b is None:continue
                pairs.append((day,float(b)-float(a)))
            values=np.array([p[1] for p in pairs]);rng=np.random.default_rng(SEED)
            if len(values):
                samples=rng.choice(values,size=(1000,len(values)),replace=True).mean(axis=1)
                lo,hi=np.quantile(samples,[.025,.975]);mean=float(values.mean())
                groups={}
                for day,value in pairs:groups.setdefault(cluster.get(day,'DAY:'+day),[]).append(value)
                keys=sorted(groups);sampled=[]
                if len(keys)>=2:
                    for _ in range(1000):
                        picked=rng.choice(keys,size=len(keys),replace=True)
                        sampled.append(np.mean([x for k in picked for x in groups[k]]))
                    blo,bhi=np.quantile(sampled,[.025,.975])
                else:blo=bhi=None
            else:mean=lo=hi=blo=bhi=None;groups={}
            output.append({'comparison':treatment+'-'+control,'metric':metric,'paired_days':len(values),
                'locked_day_denominator':len(sampling['formal_dates']),'mean_difference':mean,'CI95_low':lo,'CI95_high':hi,
                'source_cluster_count':len(groups),'cluster_sensitivity_CI95_low':blo,'cluster_sensitivity_CI95_high':bhi,
                'cluster_definition':'connected_date_components_sharing_any_forecast_ID; fewer_than_two_clusters_not_estimable',
                'bootstrap_repeats':1000,'seed':SEED,'not_engineering_fact_effect':True})
        for metric in ['human_fact_support','human_required_coverage','human_readability']:
            output.append({'comparison':treatment+'-'+control,'metric':metric,'paired_days':0,'status':'NOT_EVALUATED'})
    csv_write(HERE/'PAIRED_COMPARISONS.csv',output)


def blind_package():
    """Write a new sealed allocation, never read an existing allocation key."""
    sampling=load(HERE/'SAMPLING.json');root=HERE/'human_blind'
    if root.exists():raise RuntimeError('blind_material_already_created_no_key_read_or_reallocation')
    root.mkdir();seed=secrets.randbits(128);rng=random.Random(seed)
    assignments=[];manifest=[];claims=[];ratings=[];coverage=[];edits=[]
    provisional=csv_read(HERE/'PROVISIONAL_INFORMATION_REQUIREMENTS.csv')
    tasks=[(day,arm) for day in sampling['human_dates'] for arm in 'ABCD'];rng.shuffle(tasks)
    for index,(day,arm) in enumerate(tasks,1):
        code='REPORT-'+format(index,'03d')+'-'+format(rng.getrandbits(32),'08x')
        folder=group_folder('formal',day,arm);r=load(folder/'result.json') if (folder/'result.json').exists() else {'status':'NOT_RUN'}
        assignments.append({'anonymous_id':code,'date':day,'arm':arm,'result_folder':str(folder)})
        entry={'anonymous_id':code,'date':day,'document_status':'AVAILABLE' if (folder/'final_narrative.json').exists() else 'NOT_GENERATED'}
        manifest.append(entry)
        if entry['document_status']=='AVAILABLE':
            draft=load(folder/'final_narrative.json');save(root/'reports'/(code+'.json'),draft)
            # Neutral prose view excludes internal PASS/FAIL and method labels.
            import html
            body='<html lang="zh-CN"><meta charset="utf-8"><style>body{font:16px/1.8 system-ui;max-width:900px;margin:30px auto}p{margin:12px 0}</style><h1>'+day+' TBM施工日报</h1>'
            for section in draft['sections']:
                body+='<h2>'+html.escape(section['section_id'])+'</h2>'
                for p in section['paragraphs']:body+='<p>'+html.escape(p['text'])+'</p>'
            (root/'reports'/(code+'.html')).write_text(body+'</html>',encoding='utf-8')
            if (folder/(day+'.integrated.pdf')).exists():
                # The original review badge is not shown to blind raters; original PDFs remain unchanged.
                from tbm_process.reporting_v2.rendering import make_pdf
                original=(folder/(day+'.integrated.html')).read_text()
                original=original.replace('<div class="badge">本稿未通过全部程序检查，仅供问题审阅</div>','')
                neutral=folder/'blind_presentation.html';neutral.write_text(original,encoding='utf-8')
                make_pdf(neutral,root/'reports'/(code+'.pdf'))
            for sid,paragraph_index,p in paragraphs(draft):
                for n,sentence in enumerate(sentence_rows(p['text']),1):
                    claims.append({'anonymous_id':code,'date':day,'candidate_claim_id':f'{code}:{sid}:{paragraph_index}:{n}',
                        'section':sid,'paragraph_index':paragraph_index,'claim_text':sentence,'declared_fact_IDs':p['fact_ids'],
                        'atomization_status':'REQUIRES_HUMAN_SPLIT_IF_COMPOUND',**{k:'' for k in HUMAN_FIELDS}})
        ratings.append({'anonymous_id':code,'date':day,'document_status':entry['document_status'],
            'readability_1_to_5':'','conciseness_1_to_5':'','engineering_specificity_1_to_5':'','reviewer_id':'','reason':''})
        for req in provisional:
            if req['date']==day:coverage.append({'anonymous_id':code,**req,'present_in_body':'','present_in_table_or_figure':'',
                'correctly_expressed':'','reviewer_id':'','judgment_reason':''})
    for reviewer in ['reviewer_1','reviewer_2']:
        csv_write(root/reviewer/'claims.csv',claims)
        csv_write(root/reviewer/'coverage.csv',coverage)
        csv_write(root/reviewer/'ratings.csv',ratings)
        csv_write(root/reviewer/'adjudication_template.csv',[],['candidate_claim_id','reviewer_1_label','reviewer_2_label','adjudicated_label','reason','adjudicator'])
    for day in sampling['human_dates']:
        p=HERE/'inputs'/day/'unified_report_input.json'
        if p.exists():
            pack=load(p);save(root/'evidence'/(day+'.json'),{'date':day,'facts':[
                {k:f[k] for k in ['fact_id','section','canonical_text','data','sources','role']} for f in pack['facts']]})
    csv_write(root/'DOCUMENT_MANIFEST.csv',manifest)
    shutil.copy2(HERE/'ANNOTATION_GUIDE.md',root/'ANNOTATION_GUIDE.md')
    save(root/'STATUS.json',{'human_reports_requested':len(tasks),'actual_text_reports':sum(r['document_status']=='AVAILABLE' for r in manifest),
        'reviewers':2,'labels_received':0,'human_metrics':'NOT_EVALUATED','internal_PASS_FAIL_hidden':True,
        'allocation_seed_commitment':digest(seed),'no_expert_labels_autofilled':True,
        'source_evidence_paths_must_be_resolved_or_reviewed_from_original_material':True})
    sealed=HERE/'_sealed';sealed.mkdir(mode=0o700,exist_ok=True)
    key=sealed/'blind_allocation.json';save(key,{'seed':seed,'assignments':assignments});key.chmod(0o600)
    log('blind materials created; allocation key sealed, not read')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['inputs','metrics','blind']);stage=p.parse_args().stage
    if stage=='inputs':input_audit()
    elif stage=='metrics':collect_metrics()
    else:blind_package()
