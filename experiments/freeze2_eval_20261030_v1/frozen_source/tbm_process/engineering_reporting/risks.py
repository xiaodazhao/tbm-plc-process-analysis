"""Admit audited original source labels before planning; never infer daily alarms."""
from copy import deepcopy
from pathlib import Path

from tbm_process.geology_store.time_policy import time_decision
from .assembly import fact, source_ok
from .common import ident, interval, load, overlap, save
from .risk_geometry import row_points
from .source_text import span


def locate(bounds, row):
    if bounds is None:return None
    point = row.get('point_m')
    if point is not None:
        return [point, point] if bounds[0] <= point < bounds[1] else None
    return overlap(bounds, row['range_m'])


def build_risks(day, evidence, resources):
    docs = load(resources/'source_documents.time_policy_input.json')['documents_by_PDF_SHA256']
    decisions = {digest: time_decision(doc, day+'T00:00:00+08:00') for digest, doc in docs.items()}
    records, excluded = [], []
    for kind, filename in [('risk_grade_cell', 'PDF_risk_grade_rows.refined.json'),
                           ('risk_point', 'PDF_explicit_risk_points.refined.json')]:
        for row in load(resources/filename):
            for scope in ['day', 'ahead']:
                hit = locate(evidence[scope+'_range_m'], row)
                if hit is None:
                    continue
                source = row['source']
                admission = decisions.get(source['sha256'], {'eligible': False, 'reason': 'unregistered_source'})
                if not admission['eligible'] or not source_ok(source):
                    excluded.append({'record_id': row['record_id'], 'scope': scope, 'time': admission,
                                     'source_ok': source_ok(source)})
                    continue
                records.append({**deepcopy(row), 'kind': kind, 'scope': scope, 'intersection_m': hit,
                    'admission': admission, 'is_current_alarm': False, 'is_actual_occurrence': False})
    for row in load(resources/'workbook_risk_labels.json')['records']:
        parsed = interval(row['raw_fields'].get('施工里程范围'))
        if not parsed or parsed['prefix'] != 'DyK':
            continue
        for scope in ['day', 'ahead']:
            original = [parsed['start_m'], parsed['end_m']]
            hit = overlap(original, evidence[scope+'_range_m'])
            if not hit:
                continue
            if not source_ok(row['source']):
                raise ValueError('design_workbook_source_changed')
            records.append({'record_id': ident('XLABEL', row['source']['sha256'], row['excel_row']),
                'kind': 'design_label', 'scope': scope, 'range_m': original, 'intersection_m': hit,
                'source': row['source'], 'original_label': row['label'],
                'source_colors': row['colors_in_literal_label'], 'risk_quote': row['raw_fields'].get('风险描述'),
                'admission': {'eligible': True, 'basis': 'user_confirmed_preconstruction_background'},
                'is_current_alarm': False, 'is_actual_occurrence': False})
    grouped = {}
    for row in records:
        key = ((row['scope'], row['source']['sha256'], row.get('point_m'), tuple(row['hazards']))
               if row['kind'] == 'risk_point' else (row['scope'], row['record_id']))
        if key not in grouped:
            grouped[key] = row
            row['duplicate_evidence_sources'] = [row['source']]
        else:
            grouped[key]['duplicate_evidence_sources'].append(row['source'])
    return {'date': day, 'records': list(grouped.values()), 'excluded': excluded,
            'color_semantics': 'source_original_not_new_daily_risk_rating'}


def add_to_pack(pack, risk, resources, folder):
    for row in risk['records']:
        section = '3.3' if row['scope'] == 'ahead' else '3.1'
        point = row.get('point_m')
        location = span([point, point]) if point is not None else span(row['intersection_m'])
        if row['kind'] == 'risk_point':
            text = f"原预报在{location}提示{'、'.join(row['hazards'])}；该关注位置与其原始页码绑定。"
        elif row['kind'] == 'risk_grade_cell':
            text = f"{location}相交原预报风险栏颜色为{row['source_color'] or '未可靠识别'}。"
            points, unresolved = row_points(row)
            if row['risk_quote'] and not points and not unresolved:
                text += '原风险栏：'+row['risk_quote']
            text += '颜色是来源原标记，不转换为当天安全等级。'
        else:
            text = f"施工前设计风险摘要在{location}的原表标签：{row['original_label'] or '未填写'}；不是现场事件台账或当天报警。"
        fact(pack['facts'], 'SOURCE_RISK:'+row['record_id']+':'+row['scope'], section, text,
             row, row['duplicate_evidence_sources'], row['scope'] == 'ahead', 'original_source_risk_not_event')
    texts = ' '.join(f['canonical_text'] for f in pack['facts'] if f['section'] == '3.3' and
                     f['role'] in ['prior_forecast', 'original_source_risk_not_event'])
    for row in load(resources/'conditional_design_references.json'):
        matched = [t for t in row['trigger_categories'] if t in texts]
        if not matched:
            continue
        if not source_ok(row['source']):
            raise ValueError('risk_reference_source_changed')
        data = {**row, 'matched_terms': matched, 'scope_m': pack['coverage']['ahead']['range_m']}
        fact(pack['facts'], 'RISK_REFERENCE:'+row['reference_id'], '3.3',
             '设计文件对此类条件的有条件参考：'+row['reference_quote']+'。适用条件：'+row['source_condition'],
             data, [row['source']], True, 'conditional_design_reference_not_implementation')
    ids = [f['fact_id'] for f in pack['facts']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_integrated_fact_id')
    pack['version'] = 'unified_engineering_report_20261029_v1'
    save(folder/'source_risk_facts.json', risk)
    save(folder/'unified_report_input.json', pack)
    return pack
