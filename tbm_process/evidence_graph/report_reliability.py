"""Typed, scoped reliability checks for the three-chapter report contract.

These checks establish limited program consistency, not engineering truth.
Unresolved language receives REVIEW; explicit evidence conflicts remain FAIL.
"""
from __future__ import annotations

import re
from copy import deepcopy

NAMES={'advance_speed':'推进速度','thrust':'总推力','torque':'刀盘扭矩'}
METRIC_RE=re.compile(r'推进速度|总推力|刀盘扭矩')
NEGATION=re.compile(r'并非|非(?=实际|当日|当天|今日|现场|新增)|不构成|不属于|不表示|不代表|不等于|不能|不得|不应|尚未|未能|未取得|未确认|没有|不是|未')
NUMBER=r'[-+−]?\d+(?:\.\d+)?'
INTERNAL_ID=re.compile(r'(?:DOC|GF|PLC|CP|LC|RF|GM|SC):[A-Za-z0-9_:.-]+')


def engineering_prose(text):
    return INTERNAL_ID.sub('',text)


def mask_report_cutoff(text,report_day):
    if not report_day:return text
    year,month,day=map(int,report_day.split('-'))
    return re.sub(r'截至'+str(year)+r'年0?'+str(month)+r'月0?'+str(day)+r'日','截至报告日',text)
DURATION=re.compile(r'(?:(\d+)小时)?(?:(\d+)分(?:钟)?)?(?:(\d+)秒)?')


def duration_values(text):
    return [(m.span(),sum(int(v or 0)*s for v,s in zip(m.groups(),(3600,60,1))))
            for m in DURATION.finditer(text) if m[0] and any(m.groups())]


def duration_check(text,pairs):
    """Check the whole compound duration, not its seconds suffix in isolation."""
    errors=[];masked=list(text)
    for (a,b),seconds in duration_values(text):
        if not any(u=='秒' and abs(seconds-v)<.0051 for v,u in pairs):
            errors.append('unsupported_compound_duration:'+text[a:b])
        masked[a:b]=' '*(b-a)
    return ''.join(masked),errors


def claim_facts(claim,facts,pack):
    """Use only the declared statistical scope of a synthesis claim."""
    available=deepcopy(facts)
    if not pack.get('claim_scope_contract'):return available
    for fact in available:
        if fact['fact_id']!='SEGMENT_RESPONSE_SYNTHESIS':continue
        data=fact['data'];scope=claim.get('statistic_scope')
        if scope=='daily_background':data=deepcopy(data.get('daily_background',{}))
        elif scope=='segment_comparison':
            data=next((deepcopy(u) for u in data.get('analysis_units',[]) if u['analysis_unit_id']==claim.get('analysis_unit_id')), {})
        elif scope=='single_segment':
            data=next((deepcopy(u) for u in data.get('single_segment_responses',[]) if u['response_id']==claim.get('response_id')), {})
        else:data={'absence_reason':data.get('absence_reason')}
        fact['data']=data;fact['canonical_text']=''
    return available


def historical_position(text,bounds,day_bounds):
    """Resolve an explicit outside relation rather than treating it as current geology."""
    m=re.search(r'历史(?:记录)?区段位于当日开挖范围(后方|前方|之外)',text)
    if not m:return None
    if not bounds or not day_bounds:return 'REVIEW'
    lo,hi=sorted(day_bounds);a,b=sorted(bounds)
    # Ahead/behind require verified direction, unavailable in this checker.
    if m[1] in ('后方','前方'):return 'REVIEW' if b<=lo or a>=hi else 'FAIL'
    return 'PASS' if b<=lo or a>=hi else 'FAIL'


def report_graph_view(graph):
    """Quarantine uncertain source ranges in a report-only view; retain full graph."""
    from .store import EvidenceGraph
    reasons=[];documents=set()
    pattern=re.compile(r'DyK(\d+)\+(\d+(?:\.\d+)?)\s*[~～—至-]\s*(?:DyK)?(\d+)\+(\d+(?:\.\d+)?)')
    for node in graph.typed('GeologyFact'):
        d=node['data']
        for m in pattern.finditer(d.get('original_description','')):
            a,b=1000*int(m[1])+float(m[2]),1000*int(m[3])+float(m[4])
            if a>b:
                documents.add(d['document_id'])
                reasons.append({'fact_id':node['id'],'document_id':d['document_id'],
                    'raw_range':m[0],'reason':'reversed_raw_range_requires_source_confirmation',
                    'source':d['source'],'no_endpoint_correction_attempted':True})
    if not documents:return graph,[]
    snapshot=graph.snapshot()
    excluded={n['id'] for n in snapshot['nodes'] if n['type']=='GeologyFact' and n['data']['document_id'] in documents}
    excluded.update(n['id'] for n in snapshot['nodes'] if n['type']=='EngineeringComparison' and
                    set(n['data'].get('source_fact_ids',[])) & excluded)
    view=EvidenceGraph.from_snapshot({'metadata':deepcopy(snapshot['metadata']),
        'nodes':[deepcopy(n) for n in snapshot['nodes'] if n['id'] not in excluded],
        'edges':[deepcopy(e) for e in snapshot['edges'] if e['source'] not in excluded and e['target'] not in excluded]})
    view.metadata['report_source_quarantine']=reasons
    return view,reasons


def affirmative_matches(text,pattern):
    result=[]
    for clause in re.split(r'[。；，,;\n]|但是|但',text):
        for m in re.finditer(pattern,clause):
            local=clause[max(0,m.start()-18):m.end()]
            if not NEGATION.search(local) and not re.match(r'日期(?:未记录|不明|未知)',clause[m.end():]):
                result.append(m[0])
    return result


def asserts_observation(text,*,current=False):
    pattern=(r'(?:今日|当日|当天).{0,10}(?:新增.{0,3})?(?:揭露|揭示|观察到|发现|现场测得)' if current else
             r'实际(?:揭露|揭示)|现场(?:已)?(?:揭露|揭示|证实)|(?:今日|当日|当天).{0,8}(?:揭露|揭示)')
    return bool(affirmative_matches(text,pattern))


def comparison_records(value):
    records=[]
    def walk(node):
        if isinstance(node,dict):
            for key,item in node.items():
                if key in NAMES and isinstance(item,dict) and all(k in item for k in ('percent','reference','current')):
                    if item['percent'] is not None:
                        records.append({'metric':key,**item})
                walk(item)
        elif isinstance(node,list):
            for item in node:walk(item)
    walk(value)
    return records


def percentage_check(text,available,pairs):
    """Bind change signs to the named metric; absolute percent alone is insufficient."""
    records=comparison_records(available);errors=[];reviews=[]
    normalized=text.replace('−','-')
    for m in re.finditer('('+NUMBER+r')\s*%',normalized):
        value=float(m[1]);prefix=re.split(r'[。；;]',normalized[:m.start()])[-1]
        names=list(METRIC_RE.finditer(prefix));tail=prefix[names[-1].end():] if names else ''
        directional=re.search(r'(增加|提高|上升|增长|降低|下降|减少|变化(?:量)?(?:为)?)\s*$',tail)
        if not directional or not names:
            if not any(unit=='%' and abs(value-v)<=.0051 for v,unit in pairs):
                errors.append('unsupported_value_unit_pair:'+str(value)+'%')
            elif records:
                reviews.append('percentage_metric_or_direction_not_resolved')
            continue
        metric=next(k for k,v in NAMES.items() if v==names[-1][0]);verb=directional[1]
        candidates=[r for r in records if r['metric']==metric]
        if not candidates:
            if not any(unit=='%' and abs(value-v)<=.0051 for v,unit in pairs):
                errors.append('unsupported_value_unit_pair:'+str(value)+'%')
            reviews.append('percentage_without_typed_comparison:'+metric)
            continue
        if verb in ('降低','下降','减少'):
            signed=-value
            if value<0:errors.append('double_negative_percentage:'+metric)
        elif verb in ('增加','提高','上升','增长'):
            signed=value
            if value<0:errors.append('negative_increase_percentage:'+metric)
        else:signed=value
        if not any(abs(signed-r['percent'])<=.0051 for r in candidates):
            errors.append('percentage_direction_or_value_conflict:'+metric)
    for metric,name in NAMES.items():
        for m in re.finditer(re.escape(name)+r'(?:中位数)?(?:由|从)\s*('+NUMBER+r')(?:mm/min|kN·m|kN)?\s*(下降|降低|上升|增加|提高)(?:至|到)\s*('+NUMBER+r')',normalized):
            initial,final=float(m[1]),float(m[3]);candidates=[r for r in records if r['metric']==metric]
            if candidates and not any(abs(initial-r['reference'])<=.0051 and abs(final-r['current'])<=.0051 for r in candidates):
                errors.append('comparison_endpoint_values_conflict:'+metric)
            if (m[2] in ('下降','降低') and final>=initial) or (m[2] in ('上升','增加','提高') and final<=initial):
                errors.append('comparison_endpoint_direction_conflict:'+metric)
    return sorted(set(errors)),sorted(set(reviews))


def scoped_facts(claim,pack):
    table={f['fact_id']:f for f in pack['facts']};ids=set(claim.get('fact_ids',[]))
    # Only declared graph support is followed; do not borrow unrelated facts
    # from the rest of the daily pool to rescue a wrong claim binding.
    for fid in list(ids):
        ids.update(table.get(fid,{}).get('graph_support_ids',[]))
    return [table[i] for i in ids if i in table]


def semantic_checks(draft,pack):
    errors=[];reviews=[];details=[]
    table={f['fact_id']:f for f in pack['facts']}
    overview=table.get('OVERVIEW',{}).get('writer_data',{})
    context=table.get('GEO_CONTEXT',{}).get('writer_data',{})
    day_bounds=context.get('day_range_m')
    if not day_bounds:
        bounds=re.findall(r'DyK(\d+)\+(\d+(?:\.\d+)?)',context.get('day_range',''))
        if len(bounds)==2:day_bounds=[1000*int(k)+float(m) for k,m in bounds]
    stable=table.get('PARAMETER_ANALYSIS_SUMMARY',{}).get('writer_data',{}).get('qualified_sample_count',0)
    summary=table.get('SEGMENT_RESPONSE_SYNTHESIS',{}).get('writer_data',{})
    units={u['analysis_unit_id']:u for u in summary.get('analysis_units',[]) if u.get('analysis_unit_id')}
    if pack.get('response_analysis') and len(units)!=len(summary.get('analysis_units',[])):
        errors.append('malformed_analysis_unit_contract')
    for ci,ch in enumerate(draft.get('chapters',[])):
        for i,claim in enumerate(ch.get('claims',[])):
            text=claim.get('text','');prefix=f'claim:{ci}:{i}:';local=[];pending=[]
            support=scoped_facts(claim,pack)
            for fact in support:
                d=fact.get('writer_data',{})
                day=d.get('source_date')
                if day and pack.get('date') and day[:10]>=pack['date']:
                    local.append('source_not_available_before_report_day')
                if d.get('information_role')=='forecast' and asserts_observation(text):
                    local.append('forecast_written_as_observation')
                if d.get('source_type')=='SKETCH':
                    if asserts_observation(text,current=True):local.append('historical_record_written_as_current_reveal')
                    bounds=d.get('range_m')
                    outside=(day_bounds and bounds and None not in bounds and
                             min(day_bounds[1],bounds[1])<=max(day_bounds[0],bounds[0]))
                    position=historical_position(text,bounds,day_bounds)
                    if position=='FAIL':local.append('historical_position_relation_conflict')
                    elif position=='REVIEW':pending.append('historical_position_direction_requires_confirmation')
                    if outside and position is None and affirmative_matches(text,r'(?:当日|当天|当前)(?:实际)?开挖(?:段落|区段|范围|段)'):
                        local.append('historical_range_written_as_current_segment')
                if d.get('source_date_role')=='report_availability_date' and not d.get('observation_date'):
                    if affirmative_matches(text,r'\d{4}年\d+月\d+日[^。；，]{0,8}(?:观测|揭露|现场测得)'):
                        local.append('availability_date_written_as_observation_date')
            if 'SEGMENT_RESPONSE_SYNTHESIS' in claim.get('fact_ids',[]):
                uid=claim.get('analysis_unit_id')
                if pack.get('claim_scope_contract'):
                    scope=claim.get('statistic_scope')
                    if scope not in ('daily_background','segment_comparison','single_segment','no_qualified_response'):
                        local.append('missing_or_invalid_statistic_scope')
                    if scope=='daily_background':
                        if not re.search(r'全天|全日',text):local.append('daily_background_scope_not_disclosed')
                        if re.search(r'该(?:地质|施工)?区段.{0,12}(?:中位数|推进速度|总推力|刀盘扭矩)',text):
                            local.append('daily_statistics_presented_as_segment_statistics')
                    if scope=='segment_comparison' and uid not in units:local.append('invalid_analysis_unit_id')
                    if scope=='single_segment' and claim.get('response_id') not in {r['response_id'] for r in summary.get('single_segment_responses',[])}:
                        local.append('invalid_single_response_id')
                if 'analysis_unit_id' in claim and (not uid or uid not in units):local.append('invalid_analysis_unit_id')
                if not units:
                    if claim.get('geology_assignments'):local.append('assignments_without_analysis_units')
                    if affirmative_matches(text,r'(?:当前区段|参考区段).{0,12}(?:相比|相较|对比)'):
                        local.append('comparison_asserted_without_analysis_units')
                    if stable>=30 and re.search(r'(?:现有|合格|PLC)样本不足|合格稳定推进样本不足',text):
                        local.append('PLC_sample_shortage_claim_conflicts_with_available_samples')
                elif uid in units:
                    unit=units[uid]
                    reverse=(re.escape(unit['reference_range_display'])+r'(?:参考区段)?(?:与|相较|相比于)'+
                             re.escape(unit['current_range_display'])+r'(?:当前区段)?相比')
                    lead=re.search(reverse,text) or re.search(r'参考区段(?:与|相较)当前区段相比',text)
                    if lead and not re.match(r'[，,]\s*当前区段',text[lead.end():]):
                        pending.append('comparison_subject_ambiguous_or_reversed')
                    if re.search(r'参考区段相较当前区段[，,].{0,12}(?:增加|降低|下降|提高)',text):
                        local.append('comparison_roles_reversed')
            selected_unit=units.get(claim.get('analysis_unit_id'))
            relatives=[g.get('relative_comparison') for g in selected_unit.get('geology',[])] if selected_unit else []
            ahead=table.get('AHEAD_FORECAST_SUMMARY',{}).get('writer_data',{})
            if 'AHEAD_FORECAST_SUMMARY' in claim.get('fact_ids',[]):
                relatives.extend(g.get('relative_comparison') for g in ahead.get('segments',[]))
            relatives=[r for r in relatives if r]
            if re.search(r'较(?:前一段|前段).{0,8}(?:变差|变好|改善)',text):
                explicit=re.findall(r'以(?:该报告前一预报区段)?(DyK\d+\+\d+(?:\.\d+)?至DyK\d+\+\d+(?:\.\d+)?)为参照',text)
                known={r.get('reference_range_display') for r in relatives if r.get('reference_range_display')}
                if explicit and known and any(x not in known for x in explicit):local.append('source_relative_reference_range_conflict')
                elif not explicit and not re.search(r'(?:该|其|原)报告.{0,12}(?:前一|前段)|前一预报区段|原预报区段',text):
                    pending.append('source_relative_comparison_reference_unresolved')
            if affirmative_matches(text,r'已(?:经)?排除(?:了)?操作因素|(?:严格)?相同(?:操作|控制)条件|确认.{0,8}地质原因'):
                local.append('unverified_control_or_causality_assertion')
            if 'AHEAD_CONTEXT' in claim.get('fact_ids',[]) and not table.get('AHEAD_CONTEXT',{}).get('writer_data',{}).get('range_m'):
                if affirmative_matches(text,r'已(?:经)?构造.{0,10}前方|前方.{0,10}(?:完整覆盖|全部覆盖)|窗口内(?:存在|分布|有).{0,12}(?:围岩|断层|预报)'):
                    local.append('ahead_coverage_asserted_without_constructed_window')
            errors.extend(prefix+x for x in local);reviews.extend(prefix+x for x in pending)
            details.append({'chapter_index':ci,'claim_index':i,'text':text,'errors':sorted(set(local)),
                            'review':sorted(set(pending)),'status':'FAIL' if local else 'REVIEW' if pending else 'PASS',
                            'scope':'specified_semantic_contract_only_not_complete_engineering_verification'})
    return sorted(set(errors)),sorted(set(reviews)),details


def finish_quality(result,pack,draft):
    extra,pending,items=semantic_checks(draft,pack)
    original=list(result['errors'])
    # A missing language reference is unresolved meaning, whereas an explicit
    # wrong reference is still a blocking evidence conflict above.
    unresolved=[r for r in original if r.endswith('relative_geology_reference_not_preserved')]
    result['raw_findings_before_severity_classification']=original
    result['severity_classification']=[{'finding':r,'state':'REVIEW','reason':'source_comparison_reference_not_resolved'} for r in unresolved]
    result['errors']=sorted(set([r for r in original if r not in unresolved]+extra))
    pending+=unresolved
    result['review_items']=sorted(set(result.get('review_items',[])+pending+['independent_engineering_review_pending']))
    result['reliability_checks']=items
    result['passed']=not result['errors']
    result['deterministic_status']='PASS' if result['passed'] else 'FAIL'
    result['status']='FAIL' if result['errors'] else 'REVIEW' if result['review_items'] else 'PASS'
    result['status_policy_version']='three_chapter_reliability_v1_20261028'
    result['passed_meaning']='deterministic_nonblocking_only_not_formal_engineering_acceptance'
    result['formal_engineering_acceptance']='NOT_COMPLETED'
    return result
