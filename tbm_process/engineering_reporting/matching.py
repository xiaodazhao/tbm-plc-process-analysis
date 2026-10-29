"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
from pathlib import Path
from .common import *
from .knowledge import mentions, match_measures

def fact_location(fact,bounds):
    if fact.get('chainage_system')!='DyK' or fact.get('line')!='right':return None
    if fact.get('geometry')=='interval' and fact.get('start_m') is not None and fact.get('end_m') is not None:
        return overlap(bounds,[fact['start_m'],fact['end_m']])
    p=fact.get('point_m')
    if p is not None and bounds[0]<=p<bounds[1]:return [p,p]
    return None

def match_day(day,geology,risks,design,controls,ahead_metres=20):
    original=geology['plc_days'].get(day)
    if not original:return {'date':day,'status':'PLC_geometry_unavailable'}
    if not original['spatial_allowed'] or original['start_m'] is None or original['end_m'] is None:
        return {'date':day,'status':'PLC_spatial_matching_blocked','original_PLC_context':original}
    start,end=original['start_m'],original['end_m'];direction=1 if end>start else -1 if end<start else None
    today=sorted([start,end]);ahead=sorted([end,end+(direction or 1)*ahead_metres]) if direction else None
    match={'date':day,'status':'ok','day_range_m':today,'shield_end_m':end,'direction':direction,'ahead_range_m':ahead,
        'position_reference':'guide_shield_not_confirmed_face','existing_geometry_source':original,
        'prior_geology':[],'excluded_time_or_source':[],'historical_sketch_context':[],
        'risk_register_retrospective_only':[],'design_context_review_only':[],'measure_candidates_review_only':[]}
    doc_checks={}
    for f in geology['facts']:
        loc=fact_location(f,today) if today[0]!=today[1] else None
        forward=fact_location(f,ahead) if ahead else None
        doc=geology['documents'][f['document_id']]
        # Historical observations behind the shield remain background, never today's revelation.
        historic=doc['document_type']=='SKETCH' and doc.get('end_m') is not None and doc['end_m']<=min(today)
        if not loc and not forward and not historic:continue
        if f['document_id'] not in doc_checks:
            gate=geology['time_decision'](doc,day)
            source=doc.get('source',{});path=Path(source.get('path',''))
            source_ok=path.is_file() and sha(path)==source.get('sha256')
            doc_checks[f['document_id']]={'time':gate,'source_exists_and_SHA_matches':source_ok}
        check=doc_checks[f['document_id']]
        row={'fact_id':f['fact_id'],'document_id':f['document_id'],'document_type':doc['document_type'],
            'information_role':f['information_role'],'fact_type':f['fact_type'],
            'original_range_m':[f.get('start_m'),f.get('end_m')],'point_m':f.get('point_m'),
            'day_intersection_m':loc,'ahead_intersection_m':forward,
            'original_quote':f.get('original_description',f.get('source',{}).get('quote','')),
            'source':f.get('source'),'date_evidence':doc.get('date_audit'),**check}
        if not check['time']['eligible'] or not check['source_exists_and_SHA_matches']:
            if loc or forward:match['excluded_time_or_source'].append(row)
        elif historic and not loc and not forward:
            match['historical_sketch_context'].append(row)
        else:match['prior_geology'].append(row)
    # Show only the closest prior historical sketch; preserve all matching current/ahead facts.
    old=match['historical_sketch_context']
    if old:
        closest=max(geology['documents'][r['document_id']]['end_m'] for r in old)
        match['historical_sketch_context']=[r for r in old if geology['documents'][r['document_id']]['end_m']==closest]
    relevant=[]
    for r in risks:
        bounds=r.get('chainage')
        if not bounds or bounds['prefix']!='DyK':continue
        a=overlap(today,[bounds['start_m'],bounds['end_m']]);b=overlap(ahead,[bounds['start_m'],bounds['end_m']]) if ahead else None
        if a or b:
            match['risk_register_retrospective_only'].append({**r,'day_intersection_m':a,'ahead_intersection_m':b,
                'allowed_in_historical_formal_report':False,'reason':'date_missing_no_future_leakage'})
            relevant.extend(m['category'] for m in r['hazard_mentions'] if m['polarity']!='negative')
    for r in design:
        a=overlap(today,[r['start_m'],r['end_m']]);b=overlap(ahead,[r['start_m'],r['end_m']]) if ahead else None
        if a or b:match['design_context_review_only'].append({**r,'day_intersection_m':a,'ahead_intersection_m':b,
            'allowed_in_historical_formal_report':False,'reason':'receipt_unverified_or_original_table_ambiguity'})
    eligible_hazards=set()
    for row in match['prior_geology']:
        eligible_hazards.update(m['category'] for m in mentions(row['original_quote']) if m['polarity']!='negative')
    for scope,hazards in [('prior_geology',eligible_hazards),('retrospective_undated_risk_register',set(relevant))]:
        for c in match_measures(hazards,controls,[today[0],ahead[1] if ahead else today[1]],top_k=5):
            match['measure_candidates_review_only'].append({**c,'trigger_evidence_scope':scope,
                'trigger_not_proof_conditions_satisfied':True})
    match['report_adapter']={'date':day,'schema_version':'engineering_addendum_candidates_v1',
        'prior_fact_ids':[r['fact_id'] for r in match['prior_geology']],
        'retrospective_only_risk_record_ids':[r['record_id'] for r in match['risk_register_retrospective_only']],
        'design_candidate_ids':[r['record_id'] for r in match['design_context_review_only']],
        'measure_candidate_ids':list(dict.fromkeys(r['control_id'] for r in match['measure_candidates_review_only'])),
        'no_current_report_code_consumption':True,'not_a_safety_or_support_instruction':True}
    return match
