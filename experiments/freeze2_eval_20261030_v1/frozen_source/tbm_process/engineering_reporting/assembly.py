"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import re
from .common import *
from .source_text import duration, span, scoped_geology_text
from .schema import TITLES

def source_ok(source):
    path = Path(source['path'])
    return path.is_file() and sha(path) == source['sha256']

def covered_length(intervals):
    merged = []
    for lo, hi in sorted(v for v in intervals if v and v[0] < v[1]):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(hi, merged[-1][1])
        else:
            merged.append([lo, hi])
    return sum(hi-lo for lo, hi in merged)

def design_context(evidence, designs, risks):
    """Full-row deduplication, positive-length intersections, no invented grade boundaries."""
    grouped = {}
    for row in designs:
        if row['prefix'] != 'DyK':
            continue
        original = [row['start_m'], row['end_m']]
        a = overlap(evidence['day_range_m'], original)
        b = overlap(evidence['ahead_range_m'], original)
        if not a and not b:
            continue
        key = tuple(compact(c) for c in row['original_cells'])
        if key in grouped:
            grouped[key]['duplicate_sources'].append(row['source'])
            continue
        grouped[key] = {**deepcopy(row), 'day_intersection_m': a, 'ahead_intersection_m': b,
                        'duplicate_sources': [], 'role': 'preconstruction_design_not_observed',
                        'time_admission': 'user_confirmed_preconstruction_background_not_exact_receipt_date'}
    selected = list(grouped.values())
    for row in selected:
        same = [r for r in selected if (r['start_m'], r['end_m']) == (row['start_m'], row['end_m'])]
        length_mismatch = abs(row['reported_length_m']-(row['end_m']-row['start_m'])) > .1
        row['grade_location_reliable'] = not length_mismatch and len({r['reported_grade'] for r in same}) == 1
        row['grade_limitation'] = None if row['grade_location_reliable'] else 'same_original_range_has_multiple_grades_or_inconsistent_length_no_sub_boundaries'
    risk_selected = []
    for row in risks:
        bounds = row['chainage']
        if not bounds or bounds['prefix'] != 'DyK':
            continue
        a = overlap(evidence['day_range_m'], [bounds['start_m'], bounds['end_m']])
        b = overlap(evidence['ahead_range_m'], [bounds['start_m'], bounds['end_m']])
        if a or b:
            risk_selected.append({**row, 'day_intersection_m': a, 'ahead_intersection_m': b,
                'role': 'user_confirmed_preconstruction_design_summary_not_site_ledger',
                'time_admission': 'user_confirmation_bound_to_original_SHA',
                'negative_or_ended_structure': bool(re.search('断层已结束|无断层|未见断层', row['original_quote'])),
                'warning_label_is_current_alert': False})
    return {'design_rows': selected, 'design_risk_rows': risk_selected}

def forecasts(evidence):
    groups = {}
    for row in evidence['prior_geology']:
        if row['document_type'] not in ['TSP', 'HSP'] or row['fact_type'] != 'geological_description':
            continue
        if not row['time']['eligible'] or row['time']['available_date'] >= evidence['date']:
            raise ValueError('future_or_undated_forecast_in_prior_input')
        key = (row['document_id'], tuple(row['original_range_m']))
        x = deepcopy(row)
        x['day_intersection_m'] = overlap(evidence['day_range_m'], row['original_range_m'])
        x['ahead_intersection_m'] = overlap(evidence['ahead_range_m'], row['original_range_m'])
        grade_rows = [r for r in evidence['prior_geology'] if r['document_id'] == row['document_id']
                      and r['original_range_m'] == row['original_range_m'] and r['fact_type'] == 'rock_grade']
        grades = []
        for g in grade_rows:
            quote = g['source'].get('quote', '')
            match = re.search(r'(?:等级|分级)[：: ]*([ⅠⅡⅢⅣⅤⅥIVX]+)', quote)
            if match:
                grades.append(match[1])
        x['explicit_grades_from_grade_field'] = sorted(set(grades))
        x['relative_description_reference'] = 'original_report_preceding_forecast_segment_not_PLC_subsegment'
        groups[key] = x
    return sorted(groups.values(), key=lambda r: (r['original_range_m'][0], r['document_type']))

def coverage(evidence, context, fs):
    result = {}
    for label in ['day', 'ahead']:
        bounds = evidence[label+'_range_m']
        if bounds is None:
            result[label]={'range_m':None,'length_m':None,'forecast_union_m':None,'design_union_m':None,
                'forecast_uncovered_m':None,'status':'NOT_EVALUABLE_direction_unconfirmed'}
            continue
        result[label] = {'range_m': bounds, 'length_m': bounds[1]-bounds[0],
            'forecast_union_m': covered_length([r[label+'_intersection_m'] for r in fs]),
            'design_union_m': covered_length([r[label+'_intersection_m'] for r in context['design_rows']])}
        result[label]['forecast_uncovered_m'] = result[label]['length_m']-result[label]['forecast_union_m']
    return result

def aligned_sources(e,ctx,fs):
    """Partition for descriptive source comparison, never infer finer geological truth."""
    rows=[]
    for label in ['day','ahead']:
        scope=e[label+'_range_m']
        if scope is None:continue
        bounds=set(scope)
        candidates=[]
        for r in ctx['design_rows']:
            hit=r[label+'_intersection_m']
            if hit:
                bounds.update(hit)
                candidates.append({'id':r['record_id'],'role':'design_background','range_m':[r['start_m'],r['end_m']],
                    'intersection_m':hit,'grades':[r['reported_grade']] if r['grade_location_reliable'] else [],
                    'grade_reliable':r['grade_location_reliable'],'quote':r['original_quote'],'source':r['source']})
        for r in fs:
            hit=r[label+'_intersection_m']
            if hit:
                bounds.update(hit)
                candidates.append({'id':r['fact_id'],'role':r['document_type']+'_forecast','range_m':r['original_range_m'],
                    'intersection_m':hit,'grades':r['explicit_grades_from_grade_field'],
                    'grade_reliable':bool(r['explicit_grades_from_grade_field']),'quote':r['original_quote'],'source':r['source']})
        ordered=sorted(bounds)
        for left,right in zip(ordered,ordered[1:]):
            records=[r for r in candidates if overlap([left,right],r['intersection_m'])]
            grade_set={g for r in records for g in r['grades']}
            roles={r['role'] for r in records}
            status=('source_grades_differ_not_truth_judgment' if len(grade_set)>1 else
                    'reported_grades_agree_not_accuracy_validation' if len(roles)>1 and grade_set and all(r['grade_reliable'] for r in records) else
                    'insufficient_or_ambiguous_grade_comparison')
            rows.append({'scope':label,'range_m':[left,right],'sources':records,'comparison_status':status,
                         'local_geological_truth_inferred':False,'different_resolutions_retained':True})
    return rows

def fact(facts, identity, section, text, data=None, sources=None, required=False, role='computed'):
    facts.append({'fact_id': identity, 'section': section, 'canonical_text': text, 'data': data or {},
                  'sources': sources or [], 'required': required, 'role': role})

def assemble(day, folder, package, old_pack, activities, e, ctx, fs, gas, references):
    facts = []
    o = package['daily_overview']; a = o['daily_advance']; d = activities['daily']
    text = (f"{day}，导向盾首里程由{a['start_chainage']}至{a['end_chainage']}，当日进尺{a['net_change_m']:.2f}m。"
            f"PLC共{o['record_count']}条，覆盖{o['coverage_start'][11:19]}至{o['coverage_end'][11:19]}。"
            f"累计推进{duration(o['advancing_seconds'])}，非推进{duration(o['nonadvancing_observed_seconds'])}。"
            '非推进包括停止、调整及其他非推进状态。')
    fact(facts, 'DAY', '1', text, o, [{'path': package['source']['samples'], 'sha256': package['source']['samples_sha256']}], True)
    old = {f['fact_id']: f for f in old_pack['facts']}
    for identity, section in [('DAY_PATTERN_SUMMARY','1'), ('PARAMETER_ANALYSIS_SUMMARY','2.1'), ('SEGMENT_RESPONSE_SYNTHESIS','3.1')]:
        if identity in old:
            r = old[identity]
            fact(facts, identity, section, r['canonical_text'].replace('名义里程','里程'), r.get('data'), r.get('sources'), identity != 'SEGMENT_RESPONSE_SYNTHESIS')
    rate = f"{d['rate_m_per_advancing_hour']:.2f}m/h" if d['rate_m_per_advancing_hour'] is not None else '不可计算'
    share=f"{d['advancing_share_percent']:.1f}%" if d['advancing_share_percent'] is not None else '不可计算（有效状态支持不足）'
    fact(facts,'DAY_RATE','2.1',f"累计推进时间占有效状态记录时间{share}；全天折算推进率{rate}，为导向盾首日进尺除以累计推进小时。",d, required=True)
    for g in activities['selected_activities']:
        progress = f"{g['progress_m']:.2f}m" if g['progress_m'] is not None else '待核实'
        rate = f"{g['rate_m_per_advancing_hour']:.2f}m/h" if g['rate_m_per_advancing_hour'] is not None else '不可计算'
        fact(facts,g['activity_id'],'2.2',f"{g['start_time'][11:19]}至{g['end_time'][11:19]}累计推进{g['advancing_display']}，平台内部开累进尺变化合计{progress}，折算推进率{rate}。",g,required=True)
    for w in old_pack['response_analysis']['selected_time_windows']:
        labels = {'advance_speed':'推进速度','thrust':'总推力','torque':'刀盘扭矩'}
        directions = {'increasing':'上升','decreasing':'下降'}
        changes = '、'.join(labels[k]+directions[v['trend']] for k,v in w['changes'].items() if k in labels and v['trend'] in directions)
        fact(facts,w['window_id'],'2.2',f"{w['start_time'][11:19]}至{w['end_time'][11:19]}的连续稳定推进窗口内，{changes}。方向来自既有趋势检测；首末摘要采用首末四分之一记录的中位数。",w,required=True)
    c = coverage(e, ctx, fs)
    summary=activities['daily']['stable_statistics']
    measures=[]
    for field,name,unit in [('advance_speed','推进速度','mm/min'),('thrust','总推力','kN'),('torque','刀盘扭矩','kN·m')]:
        value=summary.get(field,{}).get('median')
        if value is not None:measures.append(f'{name}中位数{value:.2f}{unit}')
    if measures:
        fact(facts,'PLC_RESPONSE_CONTEXT','3.1',f"当天{span(e['day_range_m'])}范围合格稳定推进记录表现为"+'、'.join(measures)+
             '。这些为当天实际工况统计，空间对应不证明地质致因，也不表示与其他区段严格同工况。',
             {'statistics':summary,'range_m':e['day_range_m']},[{'path':package['source']['samples'],'sha256':package['source']['samples_sha256']}],
             not bool(old_pack['response_analysis']['segment_analyses']))
    fact(facts,'COVERAGE_DAY','3.1',f"当日施工里程{span(e['day_range_m'])}，合格TSP/HSP覆盖{c['day']['forecast_union_m']:.2f}m，设计背景覆盖{c['day']['design_union_m']:.2f}m。",c['day'],required=True)
    ahead_text=(f"以前方{span(e['ahead_range_m'])}作为20m展示窗口，合格TSP/HSP覆盖{c['ahead']['forecast_union_m']:.2f}m，未覆盖{c['ahead']['forecast_uncovered_m']:.2f}m；设计背景覆盖{c['ahead']['design_union_m']:.2f}m。"
        if e['ahead_range_m'] is not None else '当日有效起终里程未显示可确认的推进方向，未定义前方20m窗口；前方资料覆盖状态待核实，不等于无预报。')
    fact(facts,'COVERAGE_AHEAD','3.3',ahead_text,c['ahead'],required=True)
    for label, section in [('day','3.1'),('ahead','3.3')]:
        for r in ctx['design_rows']:
            if not r[label+'_intersection_m']:
                continue
            grade = f"设计围岩等级{r['reported_grade']}。" if r['grade_location_reliable'] else '同一原始范围列有不同等级和分段长度，局部等级边界未明确，不能指定本段唯一设计等级。'
            fact(facts, r['record_id']+':'+label, section,
                f"施工前设计表原范围{span([r['start_m'],r['end_m']])}，与本范围交集{span(r[label+'_intersection_m'])}。设计描述：{compact(r['original_quote'])}。{grade}",r,[r['source']],True,'design_background')
        for r in fs:
            if not r[label+'_intersection_m']:
                continue
            scoped_quote, excluded = scoped_geology_text(r,e[label+'_range_m'])
            text = (f"{r['document_type']}资料可用日期{r['time']['available_date']}，原预报范围{span(r['original_range_m'])}，"
                f"本范围交集{span(r[label+'_intersection_m'])}。适用原文：{compact(scoped_quote)}。")
            if r['explicit_grades_from_grade_field']:
                text += '建议围岩等级'+'、'.join(r['explicit_grades_from_grade_field'])+'。'
            fact(facts, r['fact_id']+':'+label, section,text,r,[r['source']],True,'prior_forecast')
    alignment=aligned_sources(e,ctx,fs)
    save(folder/'design_forecast_alignment.json',alignment)
    for n,row in enumerate(alignment,1):
        if row['comparison_status']!='source_grades_differ_not_truth_judgment':continue
        labels=['设计等级'+','.join(r['grades']) if r['role']=='design_background' else r['role'].replace('_forecast','')+'建议等级'+','.join(r['grades']) for r in row['sources'] if r['grades']]
        fact(facts,'SOURCE_GRADE_DIFFERENCE:'+str(n),'3.1' if row['scope']=='day' else '3.3',
             f"{span(row['range_m'])}的不同来源等级表述为："+'；'.join(dict.fromkeys(labels))+'。这是来源表述差异，不判定哪份更准确。',
             row,[r['source'] for r in row['sources']],False,'different_source_assessments_not_ground_truth')
    for r in e['prior_geology']:
        if r['fact_type'] != 'risk' or r['document_type'] not in ['TSP','HSP']:
            continue
        for label, section in [('day','3.1'),('ahead','3.3')]:
            if not r.get(label+'_intersection_m'):
                continue
            # Point facts bind to their own point only; a multi-point original quote is evidence, not scope expansion.
            location = span([r['point_m'],r['point_m']]) if r['point_m'] is not None else span(r['original_range_m'])
            risk_terms = [t for t in ['掉块','溜坍','出水','涌水','突泥','破碎带'] if t in compact(r['original_quote'])]
            scoped_text = '、'.join(risk_terms) if risk_terms else '原文关注内容待结合来源核查'
            fact(facts,r['fact_id']+':risk:'+label,section,
                f"{r['document_type']}原预报在{location}提示{scoped_text}；本事实只适用于所绑定位置，未标注当日已发生。",r,[r['source']],True,'forecast_risk_not_event')
    historical = [r for r in e.get('historical_sketch_context',[]) if r['time']['eligible'] and r['time']['available_date']<day
                  and r.get('original_range_m') and all(isinstance(v,(int,float)) for v in r['original_range_m'])]
    if historical:
        chosen = max(historical,key=lambda r:(r['original_range_m'][1],r['time']['available_date']))['document_id']
        relevant = [r for r in historical if r['document_id']==chosen and r['information_role']=='observed']
        for r in relevant:
            fact(facts,r['fact_id'],'3.2',f"历史素描资料可用日期{r['time']['available_date']}，原范围{span(r['original_range_m'])}；记录内容：{compact(r['original_quote'])}。",r,[r['source']],False,'historical_observation_not_today')
    fact(facts,'SKETCH_STATUS','3.2','当前未取得可确认属于当天新增现场揭示的资料。此前素描仅作历史背景，其资料可用日期不是已核实的现场观测时刻。',required=True,role='availability_limit')
    for r in ctx['design_risk_rows']:
        # A spreadsheet row can summarize a wider report and contain unrelated local risks.
        from .knowledge import local_locations
        local = local_locations(r['original_quote'])
        row_range = [r['chainage']['start_m'],r['chainage']['end_m']]
        mixed = any(v['geometry']=='point' or v.get('start_m',row_range[0])<row_range[0]
                    or v.get('end_m',row_range[1])>row_range[1] for v in local)
        description = ('原文同时包含母区段和其他局部位置，需逐项核对适用范围；本版保留原始摘要供追溯，不把其中局部风险移入本范围。'
                       if mixed else r['original_quote'])
        fact(facts,r['record_id'],'3.1' if r['day_intersection_m'] else '3.3',
            f"施工前风险资料在{span(row_range)}记述：{description}。这是设计背景，原表颜色不是当天报警，未包含实际实施措施。",r,[r['source']],False,'preconstruction_summary')
    gas_event_fields = sorted({r['field'] for r in gas['attention_events']})
    fact(facts,'GAS_STATUS','2.3',f"六种气体采用用户指定的ppm统计口径；{len(gas['attention_events'])}段达到已配置研究参考值的连续评价时段。只按配置比较CO2、SO2、H2S，NO2、NO、CH4本轮仅列统计。", {'trigger_fields':gas_event_fields},required=True)
    for n, r in enumerate(gas['attention_events'],1):
        stat = '完整15分钟时间加权均值' if r['window_seconds'] else '采样读数'
        fact(facts,'GAS_EVENT:'+str(n),'2.3',f"{r['field']}在{r['evaluation_first_time'][11:19]}至{r['evaluation_last_time'][11:19]}的{stat}达到研究参考值{r['attention_ppm']:.4f}ppm，该统计量最大{r['maximum_statistic_ppm']:.4f}ppm。",r,[{'path':gas['source']['source_file'],'sha256':gas['source']['source_sha256']}],True,'research_attention_not_formal_alarm')
    for context, rows in references:
        for r in rows:
            section = '2.3' if context['kind']=='gas' else '3.3'
            fact(facts,r['control_id']+':'+context['context_id'],section,
                f"设计文件参考（{Path(r['source']['path']).name}，PDF第{r['source']['PDF_page']}页）：{compact(r['reference_quote'])}",
                {'context':context,'reference':r},[r['source']],False,'conditional_design_reference_not_implementation')
    ids = [r['fact_id'] for r in facts]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate_fact_ID')
    result = {'version':'integration_experiment_20261029_v1','date':day,'facts':facts,'coverage':c,
        'sections':TITLES,'policy':{'preserve_original_forecast_ranges':True,'no_invented_design_grade_boundaries':True,
            'no_causal_or_safety_diagnosis':True,'all_gas_values_user_assigned_ppm_no_rescaling':True,
            'not_formal_alarm_or_exposure_assessment':True,'design_source_availability':'user_confirmed_preconstruction_background',
            'forecast_availability':'strictly_before_report_day_00:00','semantic_certification':'REVIEW_not_automatic'},
        'source':package['source']}
    save(folder/'unified_report_input.json',result)
    return result

def reference_contexts(e, ctx, fs, gas):
    texts = [scoped_geology_text(r, e['ahead_range_m'])[0] for r in fs if r['ahead_intersection_m']]
    texts += [r['original_quote'] for r in ctx['design_rows'] if r['ahead_intersection_m']]
    joined = '。'.join(texts)
    contexts = []
    terms = [t for t in ['破碎','节理','出水','富水','软弱'] if t in joined]
    if terms:
        contexts.append({'context_id':'GEO_REFERENCE','kind':'geology','scope_m':e['ahead_range_m'],
            'query':'TBM '+' '.join(terms)+' 支护 超前预报 探孔 注浆',
            'trigger_evidence':texts,'condition_status':'text_topic_not_all_site_applicability_confirmed'})
    fields = sorted({r['field'] for r in gas['attention_events']})
    if fields:
        names = {'SO2检测':'二氧化硫','CO2检测':'二氧化碳','H2S检测':'硫化氢'}
        contexts.append({'context_id':'GAS_REFERENCE','kind':'gas','scope_m':e['day_range_m'],
            'query':'施工 隧道 有害气体 '+' '.join(names.get(f,f) for f in fields)+' 通风 监测 检测',
            'trigger_fields':fields,'trigger_event_ids':[r['event_id'] for r in gas['attention_events']]})
    return contexts
