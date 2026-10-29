"""Controlled sentence mutations over real inputs; never edits PLC/PDF or verifier."""
from copy import deepcopy
from collections import defaultdict
import re

from common import *
from adapters import validate


CATEGORIES=['numeric_role_unit','time_support_span','chainage_local_scope','source_ID_page',
            'forecast_observation','relative_comparator','compound_unsupported_addition','important_information_deletion']


def one_draft(fact, text, ids=None):
    return {'sections':[{'section_id':fact['section'],'paragraphs':[{'text':text,'fact_ids':ids or [fact['fact_id']]}]}]}


def evaluate_case(case):
    from tbm_process.engineering_reporting.writer import validate_text
    pack=load(HERE/'inputs'/case['date']/'unified_report_input.json')
    plan={'sections':[{'section_id':case['section'],'paragraphs':[{'fact_ids':case['expected_source_IDs']}]}]}
    try:
        check=validate_text(case['draft'],pack,plan,section_ids=[case['section']])
    except Exception as exc:
        check={'passed':False,'errors':['validator_exception_'+type(exc).__name__],'review_required':['exception_not_semantic_confirmation']}
    blocked=not check['passed'];positive=case['expected_violation']
    return {**case,'blocked':blocked,'rule_errors':check['errors'],
        'confusion':'TP' if positive and blocked else 'FN' if positive else 'FP' if blocked else 'TN',
        'semantic_REVIEW':True,'not_natural_report_error_rate':True}


def build_cases():
    cases=[];packs=[load(HERE/'inputs'/day/'unified_report_input.json') for day in DEVELOPMENT]
    for category in CATEGORIES:
        for i in range(5):
            pack=packs[i%len(packs)];day=pack['date'];facts=pack['facts'];byid={f['fact_id']:f for f in facts}
            forecast=next((f for f in facts if f['role']=='prior_forecast' and f['section']=='3.1'),None)
            if forecast is None and category in {'chainage_local_scope','source_ID_page','forecast_observation','relative_comparator','compound_unsupported_addition'}:
                available=[(p,f) for p in packs for f in p['facts'] if f['role']=='prior_forecast' and f['section']=='3.1']
                pack,forecast=available[i%len(available)];day=pack['date'];facts=pack['facts'];byid={f['fact_id']:f for f in facts}
            fact=byid['DAY'];correct=fact['canonical_text'];bad=None;bad_ids=None;basis='explicit_source_field_or_claim_mutation'
            if category=='numeric_role_unit':
                fact=byid['PARAMETER_ANALYSIS_SUMMARY'];correct=fact['canonical_text']
                numbers=re.findall(r'\d+\.\d+',correct)
                if i%2==0 and len(numbers)>1:
                    bad=correct.replace(numbers[0],'@@X@@',1).replace(numbers[1],numbers[0],1).replace('@@X@@',numbers[1],1)
                elif 'mm/min' in correct:bad=correct.replace('mm/min','米/分钟',1)
                else:bad=correct+'推进速度为98765.4321米/分钟。'
            elif category=='time_support_span':
                fact=byid['COVERAGE_QUALITY'];correct=fact['canonical_text']
                bad=correct.replace('未覆盖','设备停止',1)
                basis='unobserved_support_complement_does_not_establish_stopped_state'
            elif category=='chainage_local_scope':
                fact=forecast;correct=fact['canonical_text']
                match=re.search(r'原预报范围([^。]+?)，本范围交集',correct)
                if not match:raise ValueError('pressure_forecast_range_missing')
                raw=match[1];ends=raw.split('至')
                bad=correct.replace(raw,'至'.join(reversed(ends)),1)
                basis='reverse_known_increasing_original_forecast_endpoints; not_new_geology_truth'
            elif category=='source_ID_page':
                fact=forecast;correct=fact['canonical_text']
                if i%2==0:bad=correct;bad_ids=['UNREGISTERED_SOURCE_'+str(i)]
                else:
                    page=fact['sources'][0].get('page',fact['sources'][0].get('PDF_page'))
                    correct+='本段依据原PDF第'+str(page)+'页。'
                    bad=correct.replace('PDF第'+str(page)+'页','PDF第'+str(page+1)+'页')
                basis='source_registry_ID_and_stored_PDF_physical_page; not_expert_geology_label'
            elif category=='forecast_observation':
                fact=forecast;correct='预报资料记载：'+fact['canonical_text']
                terms=['现场证实','已揭露','现场确认','当日揭示','实际已经揭露']
                bad=terms[i]+'：'+fact['canonical_text']
                basis='forecast_role_is_not_current_observation'
            elif category=='relative_comparator':
                matching=[(p,f) for p in packs for f in p['facts'] if f['role']=='prior_forecast' and f['section']=='3.1' and re.search('较前|前一段',f['canonical_text'])]
                if not matching:raise ValueError('no_real_relative_forecast_for_pressure')
                pack,fact=matching[i%len(matching)];day=pack['date']
                correct=fact['canonical_text']+'上述前段指原报告的前一预报段。'
                bad=correct.replace('原报告的前一预报段','程序切分的前一PLC统计子区段')
                basis='original_forecast_comparator_must_not_become_PLC_partition'
            elif category=='compound_unsupported_addition':
                fact=forecast;correct=fact['canonical_text']
                additions=['因此围岩导致推进速度降低。','本次现场已完成支护处置。','设备已经发生轴承故障。',
                           '可保证本段施工绝对安全。','据此可确定刀具发生严重磨损。']
                bad=correct+additions[i]
                basis='new_causal_implementation_or_safety_statement_absent_from_support'
            elif category=='important_information_deletion':
                targets=[f for f in facts if f['role']=='original_source_risk_not_event']
                fact=targets[i%len(targets)] if targets else byid['DAY']
                correct=fact['canonical_text'];bad='相关资料已收集，详细内容见来源。'
                basis='retain_support_ID_but_remove_original_information; controlled_omission_test_not_independent_importance_gold'
            if bad is None or (bad==correct and not bad_ids):raise ValueError('ineffective_mutation')
            for negative,text,ids in [(False,correct,None),(True,bad,bad_ids)]:
                cases.append({'case_id':f'{category}_{i+1}_'+('negative' if negative else 'positive'),
                    'category':category,'split':'development' if i<2 else 'sealed_test',
                    'date':day,'section':fact['section'],'expected_source_IDs':[fact['fact_id']],
                    'expected_violation':negative,'expectation_basis':basis,'original_support':fact['canonical_text'],
                    'sources':fact['sources'],'draft':one_draft(fact,text,ids),
                    'expected_label_is_controlled_mutation_not_human_annotation':True})
    return cases


def run_pressure():
    if (HERE/'pressure/results.json').exists():raise RuntimeError('pressure_results_already_exist')
    cases=build_cases();save(HERE/'pressure/cases.locked.json',cases)
    save(HERE/'pressure/case_commitment.json',{'cases_sha256':digest(cases),'locked_before_evaluation':True,
        'frozen_verifier_not_modified_or_tuned':True,'unit':'controlled_sentence_pair','count':len(cases)})
    results=[evaluate_case(c) for c in cases];save(HERE/'pressure/results.json',results)
    csv_write(HERE/'pressure/cases_and_results.csv',[{k:v for k,v in r.items() if k!='draft'}|
        {'text':r['draft']['sections'][0]['paragraphs'][0]['text']} for r in results])
    summary=[]
    for split in ['development','sealed_test','all']:
        for category in CATEGORIES+['ALL']:
            subset=[r for r in results if (split=='all' or r['split']==split) and (category=='ALL' or r['category']==category)]
            n={label:sum(r['confusion']==label for r in subset) for label in ['TP','FP','FN','TN']}
            tp,fp,fn,tn=(n[k] for k in ['TP','FP','FN','TN'])
            precision=tp/(tp+fp) if tp+fp else None;recall=tp/(tp+fn) if tp+fn else None
            summary.append({'split':split,'category':category,'n':len(subset),**n,
                'precision':precision,'recall':recall,'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                'false_positive_rate':fp/(fp+tn) if fp+tn else None,'semantic_REVIEW_ratio':1.,
                'not_engineering_fact_accuracy':True})
    csv_write(HERE/'pressure/summary.csv',summary)
    log('pressure test completed; n='+str(len(results)))


if __name__=='__main__':run_pressure()
