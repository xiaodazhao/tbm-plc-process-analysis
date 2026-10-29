"""Secondary descriptive audits; no scoring truth and no feedback to generation."""
from collections import Counter
from common import *
from adapters import group_folder


def main():
    plans=[];findings=[];patches=[];completion=[]
    calls=Calls('formal').rows()
    for scope,days in [('interface',DEVELOPMENT),('formal',load(HERE/'SAMPLING.json')['formal_dates'])]:
        for day in days:
            input_file=HERE/'inputs'/day/'unified_report_input.json'
            if not input_file.exists():continue
            pool={f['fact_id']:f for f in load(input_file)['facts']}
            for arm in 'ABCD':
                folder=group_folder(scope,day,arm);p=folder/'content_plan.json'
                own=[r for r in calls if r['scope']==scope and r['date']==day and r['arm']==arm]
                completed=[r for r in own if r['status']=='SUCCESS']
                completion.append({'scope':scope,'date':day,'arm':arm,
                    'actual_successful_stages':[r['stage'] for r in completed],
                    'generation_transport_succeeded':any(r['stage']=='generation' for r in completed) if arm!='D' else None,
                    'D_editor_succeeded':any(r['stage']=='editor' for r in completed) if arm=='D' else None,
                    'D_reused_C_initial_text':(folder/'shared_C_provenance.json').exists() if arm=='D' else None,
                    'no_engineering_quality_truth_inferred':True})
                if p.exists():
                    plan=load(p);selected={i for s in plan['sections'] for para in s.get('paragraphs',[]) for i in para['fact_ids']}
                    initial=folder/'plan.initial.validation.json'
                    plans.append({'scope':scope,'date':day,'arm':arm,'selection_is_posthoc_only':arm=='A',
                        'selected_IDs':len(selected),'optional_selected':sum(i in pool and not pool[i]['required'] for i in selected),
                        'paragraph_groups':sum(len(s.get('paragraphs',[])) for s in plan['sections']),
                        'omissions':plan.get('omissions',[]),'LLM_plan_initial_pass':load(initial)['passed'] if initial.exists() else None,
                        'plan_repair_attempted':(folder/'plan_repair.request.json').exists(),
                        'LLM_plan_fallback':(folder/'plan.fallback.json').exists(),
                        'not_semantic_coverage_or_planning_quality':True})
                for stage,name in [('initial','narrative.initial.validation.json'),('editor','editor.validation.json'),('final','final_narrative.validation.json')]:
                    f=folder/name
                    if not f.exists():continue
                    for issue in load(f)['errors']:
                        kind=('ID_contract_not_proven_information_omission' if 'support_ID' in issue or 'unknown_support' in issue else
                              'numeric_membership_not_confirmed_value_error' if 'unsupported_numeric_literal' in issue else
                              'typed_quantity_or_role' if 'quantity' in issue or 'duration_role' in issue else
                              'other_frozen_rule_not_human_fact_truth')
                        findings.append({'scope':scope,'date':day,'arm':arm,'stage':stage,'error':issue,
                            'diagnostic_category':kind,'independent_human_judgment':''})
                diff=folder/'revision.diff.json'
                if diff.exists():
                    data=load(diff);before=data['before'];after=data['after']
                    changed=[];ids_same=True;structure_same=[s['section_id'] for s in before['sections']]==[s['section_id'] for s in after['sections']]
                    for left,right in zip(before['sections'],after['sections']):
                        structure_same &= len(left['paragraphs'])==len(right['paragraphs'])
                        for n,(a,b) in enumerate(zip(left['paragraphs'],right['paragraphs'])):
                            ids_same &= a['fact_ids']==b['fact_ids']
                            if a!=b:changed.append((left['section_id'],n))
                    target={(r['section_id'],r['paragraph_index']) for r in data.get('patch_payload',{}).get('replacements',[])}
                    patches.append({'scope':scope,'date':day,'arm':arm,'changed_paragraphs':changed,
                        'structure_unchanged':bool(structure_same),'source_IDs_unchanged':bool(ids_same),
                        'all_changes_inside_returned_patch_targets':all(x in target for x in changed),
                        'rule_errors_before':data['reasons'],
                        'rule_errors_final':load(folder/'final_narrative.validation.json')['errors'],
                        'true_errors_corrected':'NOT_EVALUATED','correct_information_retained':'NOT_EVALUATED',
                        'false_alarm_deletions_possible':True})
    csv_write(HERE/'PLAN_SELECTION.csv',plans);csv_write(HERE/'RULE_FINDINGS_DIAGNOSTIC.csv',findings)
    csv_write(HERE/'LOCAL_PATCH_INDEPENDENT_STRUCTURE_CHECK.csv',patches)
    csv_write(HERE/'METHOD_COMPLETION.csv',completion)
    save(HERE/'SECONDARY_DIAGNOSTIC_SUMMARY.json',{
        'purpose':'descriptive_mechanism_audit_not_additional_primary_hypothesis_or_truth_labels',
        'formal_C_initial_plan_pass':sum(r['LLM_plan_initial_pass'] is True for r in plans if r['scope']=='formal' and r['arm']=='C'),
        'formal_C_plan_repairs':sum(r['plan_repair_attempted'] for r in plans if r['scope']=='formal' and r['arm']=='C'),
        'formal_C_rule_fallbacks':sum(r['LLM_plan_fallback'] for r in plans if r['scope']=='formal' and r['arm']=='C'),
        'formal_D_local_patch_records':sum(r['scope']=='formal' for r in patches),
        'all_recorded_patches_preserve_other_structure_and_IDs':all(r['structure_unchanged'] and r['source_IDs_unchanged'] and r['all_changes_inside_returned_patch_targets'] for r in patches),
        'formal_final_rule_types':dict(Counter(r['diagnostic_category'] for r in findings if r['scope']=='formal' and r['stage']=='final'))})


if __name__=='__main__':main()
