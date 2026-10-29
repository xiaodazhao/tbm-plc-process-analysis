"""Read-only diagnostics of observed failures, not a replacement truth scorer."""
from __future__ import annotations

import math
import json
from pathlib import Path
import re
import sqlite3

import pandas as pd

from tbm_process.evidence_graph.catalog import document_gate
from tbm_process.reporting_v2.facts import digest, write_json
from .common import DATABASE, read, write_csv
from .scan_and_explore import cell_index


def directed_percentage(text, name, percent):
    """Check the named direction and magnitude, not isolated number occurrence."""
    verb = '降低|下降|减少' if percent < 0 else '提高|上升|增加'
    pattern = re.escape(name)+r'.{0,8}?(?:'+verb+r')\s*([-+]?\d+(?:\.\d+)?)\s*%'
    return any(abs(float(m[1])-abs(percent)) <= .0051 for m in re.finditer(pattern,text))


def audit_reports(output):
    diagnostics=[]; auxiliary=[]
    with sqlite3.connect(DATABASE.as_uri()+'?mode=ro',uri=True) as connection:
        raw_facts={fid:json.loads(data) for fid,data in connection.execute('SELECT fact_id,data_json FROM facts')}
        documents={did:json.loads(data) for did,data in connection.execute('SELECT document_id,data_json FROM documents')}
    time_range_checks=[]
    names={'advance_speed':'推进速度','thrust':'总推力','torque':'刀盘扭矩'}
    for status in read(output/'A_generation_statuses.json'):
        day=status['date']; folder=output/'A_reports'/day
        if not (folder/'final/narrative.json').exists():
            continue
        draft=read(folder/'final/narrative.json'); analysis=read(folder/'response_analysis.json')
        pack=read(folder/'evidence_pack.json'); units={u['analysis_unit_id']:u for u in analysis['segment_analyses']}
        claims=[c for ch in draft['chapters'] for c in ch['claims']]
        fact_table={f['fact_id']:f for f in pack['facts']}
        used={i for c in claims for i in c.get('fact_ids',[])}
        used_geology={i for i in used if i.startswith('GF:')}
        used_geology.update(i for fid in used for i in fact_table[fid].get('graph_support_ids',[]) if i.startswith('GF:'))
        for fid in sorted(used_geology):
            f=raw_facts.get(fid)
            gate=document_gate(documents[f['document_id']],day) if f else {'status':'not_found'}
            time_range_checks.append({'date':day,'check':'used_geology_source_exists_and_is_prior_available','fact_id':fid,
                                      'passed':f is not None and gate['status']=='eligible','details':gate})
        ahead=fact_table.get('AHEAD_FORECAST_SUMMARY',{}).get('writer_data',{})
        for segment in ahead.get('segments',[]):
            f=raw_facts.get(segment['fact_id']);bounds=[f.get('start_m'),f.get('end_m')] if f else []
            intersection=segment['intersection_range_m'];window=ahead['ahead_range_m']
            passed=(bounds==segment['original_range_m'] and bounds[0]<=intersection[0]<intersection[1]<=bounds[1]
                    and window[0]<=intersection[0]<intersection[1]<=window[1])
            time_range_checks.append({'date':day,'check':'ahead_original_range_and_exact_clipping','fact_id':segment['fact_id'],
                                      'passed':passed,'details':{'SQLite_range':bounds,'reported':segment}})
        for error in status.get('quality_errors',[]):
            result='REVIEW'; reason='需要结合原文及完整语义复核，未自动改写原判定。'; evidence={}
            if error=='segment_claim_missing_valid_analysis_unit_id' and not units:
                result='程序条件错误：空集合仍被要求绑定区段ID'
                reason='response_analysis存在但analysis_units为空；正文说明无可比区段，并非遗漏一个真实存在的区段。'
                evidence={'analysis_unit_count':0,'claims':[c['text'] for c in claims if 'SEGMENT_RESPONSE_SYNTHESIS' in c.get('fact_ids',[])]}
            elif error.startswith('unsupported_value_unit_pair:') and error.endswith('%'):
                value=float(error.split(':',1)[1][:-1]); matches=[]
                for c in claims:
                    u=units.get(c.get('analysis_unit_id'))
                    if u:
                        for metric,change in u['changes'].items():
                            p=change.get('percent')
                            if p is not None and abs(abs(p)-value)<=.0051 and directed_percentage(c['text'],names[metric],p):
                                matches.append({'metric':metric,'signed_computed_percent':p,'text':c['text']})
                if matches:
                    result='程序误报：方向词与有符号变化的表示不兼容'
                    reason='原程序按有符号数值匹配；正文使用“降低+绝对幅度”，方向及两位小数幅度均有计算支持。'
                    evidence={'direction_and_magnitude_checks':matches}
            elif error.endswith('relative_geology_reference_not_preserved'):
                text=''.join(c['text'] for c in claims if '原报告以' in c['text'])
                if re.search(r'原报告以DyK\d+\+\d+至DyK\d+\+\d+为参照',text):
                    result='程序误报候选：明确参照不符合正则短语'
                    reason='正文给出原报告比较范围，但检查器只接受有限的“前一预报区段”等字面形式；范围是否与PDF一致仍待人工复核。'
                    evidence={'text':text}
            elif error=='report_availability_date_written_as_observation_date':
                pattern=r'\d{4}年\d+月\d+日(?:[^。；]{0,8})(?:观测|揭露|现场测得)'
                matches=[{'text':c['text'],'matched':m[0]} for c in claims for m in re.finditer(pattern,c['text'])]
                if matches and all('实际观测日期未记录' in r['text'] for r in matches):
                    result='程序误报候选：将否定观测日期的陈述当作肯定'
                    reason='实际匹配片段含“观测”，但整句明确说明观测日期未记录；正则不解析否定。'
                    evidence={'regex_matches':matches}
            elif error=='unsupported_value_unit_pair:0.0秒':
                result='REVIEW：复合时长的末尾秒数与累计秒数绑定不充分'
                reason='5/23实际支持时长为零，正文含0分00秒；保留原误报，不能据此认为整个验证器已校准。'
                evidence={'duration_audit':analysis['duration_audit']}
            diagnostics.append({'date':day,'original_error':error,'diagnostic':result,'reason':reason,
                                'evidence':evidence,'original_failure_status_unchanged':True,'human_truth_label':''})
        ambiguous=[c for c in claims if re.search(r'参考区段与.{0,50}当前区段相比，推进速度',c['text'])
                   and '当前区段推进速度' not in c['text']]
        auxiliary.append({'date':day,'scope':'Codex辅助阅读，不是独立专家标注',
            'time_analysis_present':bool(analysis['selected_time_windows']),
            'segment_analysis_present':bool(units),
            'possible_unflagged_comparison_lead_ambiguity':[c['text'] for c in ambiguous],
            'reading_note':('3.1比较句的语法主语可能将方向反转，需要改写后复核；本轮不修改模型结果。' if ambiguous else
                            '保留待人工判断；有限程序检查和辅助阅读不能确认全部语义正确。'),
            'engineering_value_human_label':'','readability_human_label':''})
    write_json(output/'A_failure_diagnostics.json',diagnostics)
    write_csv(output/'A_failure_diagnostics.csv',[{**r,'evidence':str(r['evidence'])} for r in diagnostics])
    write_json(output/'A_auxiliary_reading_NOT_HUMAN.json',auxiliary)
    write_json(output/'A_all_used_geology_time_range_audit.json',{
        'passed':all(c['passed'] for c in time_range_checks),'checks':time_range_checks,
        'scope':'all_bound_GF_IDs_and_ahead_summary_ranges; not_full_sentence_semantics'})
    return diagnostics,auxiliary


def audit_spatial(output):
    dest=output/'B_geology_response_v2'; docs=read(dest/'source_document_index.json')
    cells=read(dest/'disjoint_spatial_cells.json'); links=read(dest/'source_cell_links.json')
    frame=pd.read_csv(output/'cache/qualified_spatial_samples.csv.gz')
    boundaries=[c['start_m'] for c in cells]+[cells[-1]['end_m']]
    frame['cell_id']=cell_index(frame.chainage.to_numpy(),boundaries)
    checks=[]
    def add(name,passed,details):checks.append({'name':name,'passed':bool(passed),'details':details})
    add('unique_raw_row_membership',not frame.duplicated(['date','source_file','source_row']).any(),len(frame))
    add('cell_sample_counts',sum(c['sample_count'] for c in cells)==len(frame),len(frame))
    screened=pd.read_csv(output/'329day_screening.csv')
    changed=[r.date for r in screened.itertuples() if digest(Path(r.source_samples))!=r.samples_sha256]
    add('all329_processed_PLC_files_unchanged_since_scan',not changed,{'days':len(screened),'changed_dates':changed})
    bad_time=[]; bad_counts=[]
    for link in links:
        for day in link['matched_days']:
            if document_gate(docs[link['document_id']],day)['status']!='eligible':
                bad_time.append([link['source_unit_id'],day])
        block=frame.loc[frame.cell_id.eq(link['cell_id']) & frame.date.isin(link['matched_days'])]
        if len(block)!=link['matched_records'] or abs(block.sample_dt_seconds.fillna(0).sum()-link['supported_seconds'])>.001:
            bad_counts.append(link['source_unit_id'])
    add('source_availability_before_each_matched_day',not bad_time,bad_time)
    add('source_link_counts_and_real_support_seconds',not bad_counts,bad_counts)
    grouped=read(dest/'attribute_response_rows.json'); idx={(r['source_type'],r['cell_id']):r for r in grouped}
    examples=read(dest/'similar_description_different_response_examples.json'); bad_examples=[]
    for e in examples:
        a,b=[idx[(e['source_type'],e[k])] for k in ['left_cell_id','right_cell_id']]
        good=(not set(a['document_ids'])&set(b['document_ids']) and
              not set(a['source_unit_ids'])&set(b['source_unit_ids']) and
              math.isfinite(e['rpm_relative_difference']) and e['rpm_relative_difference']<=.15)
        if not good:bad_examples.append(e)
    add('counterexamples_distinct_documents_and_valid_RPM_diagnostic',not bad_examples,bad_examples)
    add('reported_group_RPM_is_finite',all(math.isfinite(r['rpm_median']) for r in grouped),len(grouped))
    used_documents=sorted({r['document_id'] for r in links}); originals=[]
    for did in used_documents:
        doc=docs[did];path=Path(doc.get('original_file_path') or doc['source']['path'])
        expected=doc.get('source_version') or doc.get('source',{}).get('sha256')
        exists=path.is_file(); actual=digest(path) if exists else None
        originals.append({'document_id':did,'source_file':doc['source_file'],'original_path':str(path),
                          'exists':exists,'expected_sha256':expected,'actual_sha256':actual,
                          'hash_matches':exists and bool(expected) and actual==expected})
    write_csv(dest/'original_source_file_verification.csv',originals)
    add('original_files_exist_and_match_recorded_hash',all(r['hash_matches'] for r in originals),
        {'documents':len(originals),'not_repeated_PDF_semantic_verification':True})
    units={u['unit_id']:u for u in read(output/'geology_source_units.json')}
    inventory=[]
    for uid in sorted({r['source_unit_id'] for r in links}):
        u=units[uid]
        for attribute,values in u['attributes'].items():
            for value in values:
                inventory.append({'source_unit_id':uid,'document_id':u['document_id'],'source_type':u['source_type'],
                    'attribute':attribute,'literal_value':value['text'],'locality':str(value.get('locality',[])),
                    'range_m':str(u['range_m']),'not_an_independent_ground_truth_label':True})
    write_csv(dest/'source_attribute_inventory.csv',inventory)
    result={'passed':all(r['passed'] for r in checks),'checks':checks,
        'scope':'membership_timestamps_counts_not_geological_truth_or_causal_validity',
        'prior_v1_is_superseded':'v2_corrects_temporal_subsets_source_independence_and_spatial_window_count',
        'CLI_serialization_failure':'summary_and_tables_written_before_stdout_failed; final_int64_stdout_adapter_fixed; full_B_not_rerun'}
    write_json(dest/'independent_consistency_audit.json',result)
    return result
