"""Read-only TSP/HSP comparison pilot. No production integration or training."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tbm_process.evidence_graph.catalog import SourceAudit, compact, document_gate
from tbm_process.final_reporting.common import (
    CHAINAGE_RANGE_RE, format_chainage, load_deepseek_api_key, parse_chainage,
)
from tbm_process.final_reporting.integrated_llm import ModelResponseError, call_json_model

DATABASE = Path('/Users/zhaoxiaoda/Desktop/TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite')
INVENTORY = Path('/Users/zhaoxiaoda/Desktop/TBM_地质描述单元盘点_20261027/结果_20261027T200622')
OUTPUT_ROOT = Path('/Users/zhaoxiaoda/Desktop/TBM_TSP_HSP属性关系实验_20261027')
LABELS = dict(zip(
    'lithology stratigraphic_unit weathering rock_hardness fracture_development rock_fragmentation interlayer_bonding rock_mass_stability groundwater groundwater_change structure rock_fabric filling relative_geology_change recommended_grade risk_description reflection_anomaly reflection_interface_count reflection_polarity reflection_distribution geophysical_parameter_trend geophysical_fluctuation'.split(),
    '岩性及组合 地层时代 风化程度 岩质软硬 裂隙发育 破碎程度 层间结合 整体稳定性 地下水 出水变化 构造 岩石构造 充填 相对地质变化 建议围岩等级 风险描述 反射异常 反射界面数量 反射极性 反射分布 物探参数趋势 物探起伏'.split()))
FOCUS = ['lithology', 'rock_hardness', 'fracture_development', 'rock_fragmentation',
         'recommended_grade', 'relative_geology_change']
RELATIONS = {'literal_agreement', 'main_combination_agreement', 'main_combination_agreement_with_supplement',
             'degree_difference', 'range_overlap', 'explicit_difference',
             'information_supplement', 'insufficient_comparability', 'scope_limited'}
VERSION = 'tsp_hsp_attribute_relation_pilot_v1'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def csv_save(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else ['status'])
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k, '') for k in fields})


def intersection(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return [lo, hi] if lo < hi else None


def scope(unit, overlap):
    """Literal locality is not silently expanded to an entire parent interval."""
    raw = unit['raw_expression']
    ranges = [[parse_chainage(a), parse_chainage(b)] for a, b in CHAINAGE_RANGE_RE.findall(compact(raw))]
    if ranges:
        effective = [x for r in ranges if (x := intersection(r, overlap))]
        return {'status': 'local_range_overlap' if effective else 'outside_overlap',
                'explicit_ranges_m': ranges, 'effective_ranges_m': effective}
    if re.search(r'DyK\s*\d+\s*\+', raw):
        return {'status': 'review_unresolved_point_or_local_geometry', 'effective_ranges_m': []}
    if any(x in unit.get('qualifiers', []) for x in ('局部', '少量', '原岩', '掌子面右侧', '隧道右侧', '其中')):
        return {'status': 'local_qualifier_position_unresolved', 'effective_ranges_m': [overlap]}
    if unit.get('span_in_original_text'):
        preceding = unit.get('original_text', '')[:unit['span_in_original_text'][0]]
        preceding_clause = re.split(r'[，,。；;]', preceding.rstrip('，,'))[-1]
        if re.search(r'掌子面右侧|隧道右侧', preceding_clause):
            return {'status': 'review_inherited_locality', 'effective_ranges_m': [overlap],
                    'note': '前一子句含侧向限定，当前子句是否承接需人工核实。'}
    return {'status': 'parent_interval', 'effective_ranges_m': [overlap]}


def load_records(database, inventory):
    con = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        docs = {r['document_id']: json.loads(r['data_json']) for r in con.execute("SELECT * FROM documents WHERE document_type IN ('TSP','HSP')")}
        facts = [json.loads(r['data_json']) for r in con.execute("SELECT f.data_json FROM facts f JOIN documents d USING(document_id) WHERE d.document_type IN ('TSP','HSP') AND f.fact_type IN ('geological_description','geophysical_description','rock_grade')")]
    finally:
        con.close()
    units = json.loads((inventory / '全部候选单元与来源.json').read_text())
    by_record = defaultdict(list)
    for u in units:
        by_record[u['record_id']].append(u)
    records = {}
    aliases = {'geological_description': 'geology', 'geophysical_description': 'geophysical', 'rock_grade': 'grade'}
    for f in facts:
        rid = f['record_id']
        if rid not in records:
            records[rid] = {'record_id': rid, 'document_id': f['document_id'], 'document_type': f['document_type'],
                            'range_m': [f['start_m'], f['end_m']], 'fields': {}, 'units': by_record[rid]}
        name = aliases[f['fact_type']]
        text = f.get('value') if name == 'grade' else f.get('original_text')
        records[rid]['fields'][name] = {'fact_id': f['fact_id'], 'text': text or '', 'source': f['source'],
                                      'quality': f.get('extraction_quality', {}), 'information_role': f['information_role']}
    for r in records.values():
        prior = [q for q in records.values() if q['document_id'] == r['document_id'] and q['range_m'][1] == r['range_m'][0]]
        r['previous_segment_range_m'] = prior[0]['range_m'] if len(prior) == 1 else None
        d = docs[r['document_id']]
        r['document'] = d
        r['source_file'] = d['source_file']
        r['date_metadata'] = d['metadata']
    return list(records.values()), docs


def side_for_pair(record, overlap, day):
    return {**record, 'gate': document_gate(record['document'], day),
            'units': [{**u, 'comparison_scope': scope(u, overlap)} for u in record['units']]}


def values(side, kind):
    return [u for u in side['units'] if u['unit_type'] == kind and u['comparison_scope']['status'] != 'outside_overlap']


def literal_values(units):
    return list(dict.fromkeys(u['value'] for u in units))


def compare_attribute(kind, a, b, side_a, side_b):
    av, bv = literal_values(a), literal_values(b)
    note = ''
    if not av or not bv:
        relation = 'information_supplement' if av or bv else 'insufficient_comparability'
        note = '未提及不等于不存在；单方信息不能判定相互支持或冲突。'
    elif kind == 'relative_geology_change' or kind == 'geophysical_parameter_trend':
        relation = 'insufficient_comparability'
        note = '参照对象/前一段范围未证明相同，不直接比较变好变差或物探趋势。'
    elif kind == 'lithology':
        def main(us):
            return [re.sub(r'^石炭二叠系', '', u['value']) for u in us if not any(q in u.get('qualifiers', []) for q in ('局部', '少量', '原岩'))]
        am, bm = main(a), main(b)
        if av == bv:
            relation = 'literal_agreement'
        elif [re.sub(r'^石炭二叠系', '', v) for v in av] == [re.sub(r'^石炭二叠系', '', v) for v in bv]:
            relation = 'main_combination_agreement'
            note = '仅分离已盘点的地层时代词；岩性组合和附加成分一致，时代信息仍单独保留。'
        elif am and am == bm:
            relation = 'main_combination_agreement_with_supplement'
            note = '主体组合字面一致；额外岩性保留局部/少量等限定，不能扩展到整个交集。'
        else:
            relation = 'explicit_difference'
            note = '组合或主次表述不同；不判定真实矛盾或哪份预测更准。'
    elif av == bv:
        relation = 'literal_agreement'
    elif kind in ('rock_fragmentation', 'fracture_development'):
        ends = lambda vs: set(x for v in vs for x in re.split(r'[-~～—至]', v))
        relation = 'range_overlap' if ends(av) & ends(bv) else 'degree_difference'
        note = '仅比较原词及范围端点；保留复合值，不据此判定地质冲突。'
    else:
        relation = 'explicit_difference'
        note = '原文具体表述不同，不判定预测准确性。'
    unresolved = [u['unit_id'] for u in a + b if u['comparison_scope']['status'] in ('review_unresolved_point_or_local_geometry', 'local_qualifier_position_unresolved', 'review_inherited_locality')]
    if unresolved:
        note += ' 存在局部/原岩限定或未定点域，仅做文字对照，不能认定全交集均适用。'
    return {'attribute': kind, 'label': LABELS[kind], 'relation': relation, 'tsp_values': av, 'hsp_values': bv,
            'tsp_unit_ids': [u['unit_id'] for u in a], 'hsp_unit_ids': [u['unit_id'] for u in b],
            'scope_review_unit_ids': unresolved, 'note': note, 'semantic_status': 'REVIEW_not_human_gold'}


def rule_compare(pair):
    a, b = pair['tsp'], pair['hsp']
    kinds = FOCUS + [k for k in LABELS if k not in FOCUS and (values(a, k) or values(b, k))]
    return {'pair_id': pair['pair_id'], 'comparisons': [compare_attribute(k, values(a, k), values(b, k), a, b) for k in kinds]}


def profile(pair):
    r = rule_compare(pair)
    return {(x['attribute'], x['relation']) for x in r['comparisons']}


def enumerate_pairs(records):
    pairs, excluded = [], []
    for a in (r for r in records if r['document_type'] == 'TSP'):
        for b in (r for r in records if r['document_type'] == 'HSP'):
            overlap = intersection(a['range_m'], b['range_m'])
            if not overlap:
                continue
            ds = [x['date_metadata'].get('submission_date') or x['date_metadata'].get('cover_date') for x in (a, b)]
            if not all(ds):
                excluded.append({'tsp': a['record_id'], 'hsp': b['record_id'], 'reason': 'date_missing'})
                continue
            anchor = a['fields']['geology']['fact_id'] == 'GF:6b160df4b1aaffbb722a' and b['fields']['geology']['fact_id'] == 'GF:7647531824ffba4aa9da'
            day = '2024-04-26' if anchor else (date.fromisoformat(max(ds)) + timedelta(days=1)).isoformat()
            pair = {'pair_id': 'PAIR:' + hashlib.sha256((a['record_id'] + '|' + b['record_id']).encode()).hexdigest()[:12],
                    'intersection_m': overlap, 'intersection_text': format_chainage(overlap[0]) + '～' + format_chainage(overlap[1]),
                    'as_of_date': day, 'as_of_basis': 'specified_construction_day' if anchor else 'day_after_later_report_date_retrospective_not_PLC_day',
                    'tsp': side_for_pair(a, overlap, day), 'hsp': side_for_pair(b, overlap, day), 'is_anchor': anchor}
            if any(pair[s]['gate']['status'] != 'eligible' for s in ('tsp', 'hsp')):
                excluded.append({'pair_id': pair['pair_id'], 'reason': 'date_or_alignment_gate', 'gates': [pair[s]['gate'] for s in ('tsp', 'hsp')]})
                continue
            pairs.append(pair)
    return pairs, excluded


def select_pairs(pool, count=12):
    rng = random.Random(20261027)
    remaining = list(sorted(pool, key=lambda p: p['pair_id']))
    rng.shuffle(remaining)
    chosen = []
    def take(p, reason):
        p['selection_reason'] = reason
        chosen.append(p)
        remaining.remove(p)
    take(next(p for p in remaining if p['is_anchor']), '指定4月26日案例；检查等级差异及F36后继区段边界')
    boundary = [p for p in remaining if p['tsp']['document_id'] == chosen[0]['tsp']['document_id'] and p['hsp']['document_id'] == chosen[0]['hsp']['document_id'] and p['intersection_m'][1] == 1015822 and 'F36' in p['tsp']['fields']['geology']['text']]
    if boundary:
        take(max(boundary, key=lambda p: p['intersection_m'][0]), 'F36终点前的邻段对照；不向后继区段传播断层编号')
    while remaining and len(chosen) < count:
        tsp_seen = {p['tsp']['document_id'] for p in chosen}
        dp = Counter((p['tsp']['document_id'], p['hsp']['document_id']) for p in chosen)
        seen = set().union(*(profile(p) for p in chosen))
        eligible = [p for p in remaining if dp[(p['tsp']['document_id'], p['hsp']['document_id'])] < 2]
        if not eligible:
            break
        def score(p):
            return (p['tsp']['document_id'] not in tsp_seen,
                    dp[(p['tsp']['document_id'], p['hsp']['document_id'])] == 0,
                    len(profile(p) - seen),
                    len({u['value'] for s in ('tsp', 'hsp') for u in p[s]['units']} - {u['value'] for c in chosen for s in ('tsp', 'hsp') for u in c[s]['units']}))
        take(max(eligible, key=score), '固定种子打破并列；优先覆盖不同TSP及报告组合，再增加属性/字面表达差异')
    return chosen


def prepare(database, inventory, output_root):
    before = sha(database)
    output = output_root / datetime.now().strftime('%Y%m%dT%H%M%S')
    output.mkdir(parents=True, exist_ok=False)
    records, docs = load_records(database, inventory)
    pool, excluded = enumerate_pairs(records)
    pairs = select_pairs(pool)
    save(output / 'candidate_pool.json', pool)
    save(output / 'excluded_candidates.json', excluded)
    save(output / 'samples.json', pairs)
    save(output / 'rule_results.json', [rule_compare(p) for p in pairs])
    selected_docs = {p[s]['document_id'] for p in pairs for s in ('tsp', 'hsp')}
    save(output / 'source_documents.json', {k: docs[k] for k in sorted(selected_docs)})
    auditor, audits, page_texts = SourceAudit(), [], {}
    for p in pairs:
        for s in ('tsp', 'hsp'):
            side = p[s]
            for name, field in side['fields'].items():
                check, text = auditor.page(field['source'])
                key = field['fact_id']
                page_texts[key] = text
                audits.append({'pair_id': p['pair_id'], 'side': s, 'field': name, 'fact_id': key,
                               'path': field['source']['path'], 'page': field['source']['page'], **check,
                               'field_literal_on_pdf_page': bool(compact(field['text']) and compact(field['text']) in compact(text))})
    save(output / 'source_audit.json', audits)
    save(output / 'source_page_texts.json', page_texts)
    date_audits = []
    for did in sorted(selected_docs):
        d = docs[did]
        date_audits.append({'document_id': did, 'source_file': d['source_file'],
                            'check': auditor.metadata(d['metadata'], d['source']),
                            'stored_date_audit': d['date_audit']})
    save(output / 'availability_source_audit.json', date_audits)
    csv_save(output / 'sample_index.csv', [{'pair_id': p['pair_id'], '交集': p['intersection_text'], '核查日期': p['as_of_date'],
             'TSP': p['tsp']['source_file'], 'HSP': p['hsp']['source_file'], '抽样原因': p['selection_reason']} for p in pairs])
    config = {'version': VERSION, 'database': str(database), 'database_sha256_before': before,
              'inventory': str(inventory), 'inventory_sha256': sha(inventory / '全部候选单元与来源.json'),
              'seed': 20261027, 'sampling': 'purposive_diversity_not_random_representative_sample',
              'eligible_pairs': len(pool), 'excluded_pairs': len(excluded), 'selected_pairs': len(pairs),
              'attribute_categories': LABELS, 'max_new_api_calls': 2, 'sdk_retries': 0,
              'batch_size': 6, 'model': 'deepseek-chat', 'temperature': 0.1, 'max_tokens': 9000,
              'availability_limitation': '报告正文提交日期作为既有项目可用日期依据；不等于独立核实收文时刻。非指定案例用较晚提交日期次日作回溯核查截止。',
              'risk_limit': '上一轮risk_description未绑定单个区段，不能按母报告自动分配；本轮保留原文内风险和原有单位，不重新做风险抽取。',
              'source_audit': dict(Counter(x['status'] for x in audits))}
    config['database_sha256_after'] = sha(database)
    assert before == config['database_sha256_after']
    config['samples_sha256'] = sha(output / 'samples.json')
    save(output / 'config.json', config)
    print(json.dumps({'output': str(output), 'pairs': len(pairs), 'eligible': len(pool), 'source_checks': config['source_audit']}, ensure_ascii=False))
    return output


SYSTEM = '''你比较真实TSP/HSP预测文字，不作实际揭露判断、不评价谁准确、不联系PLC原因。输入双方原文、原有22类候选属性、真实空间交集及日期。
请具体指出同属性的相同、程度差异、范围重叠、单方补充及无法比较；不许仅说“可并列描述”。未提及不等于不存在；不能把不同物探特征直接看成冲突。
“板岩夹变质砂岩”保留主次组合；局部、少量、原岩不可删除；较破碎-破碎等范围不得降为单值。“较前段变差”的前段未对齐，不代表两报告共同观察到同一变化。
任何断层编号、局部里程只对原文明确的区段适用，断层角砾不能推成存在特定断层。地质预测也不是故障或因果证据。
以下是虚构的格式示例，不是实验记录：
1.A“板岩夹变质砂岩，岩体较破碎-破碎，Ⅳ”；B“板岩夹变质砂岩，局部夹压碎岩，岩体破碎，Ⅴ”：主体组合相符、B局部补充、破碎范围有重叠、建议等级不同；不能称整个地质冲突。
2.A“滴渗水”；B未提水：A提供出水描述，B不能认作无水。
3.F36只在+764～+822原段出现，比较+822～+827时另一段没有F36，不能把相邻段编号带入；两份“较前段变差”的参照段不同则暂不能比较。
只返回JSON对象 {"pairs":[{"pair_id":"输入ID","comparisons":[{"attribute":"22类之一","relation":"允许类型之一","summary":"具体比较，最多55字","tsp":{"field":"geology或grade或geophysical","quote":"逐字短引文，最多35字；未提及可为空"},"hsp":{"field":"同上","quote":"同上"},"limitations":"必要限定，最多50字"}],"overall_summary":"不超过90字"}]}。
每组覆盖输入六个重点属性，即使不足比较也明确列出；再选至多三个有信息的其他属性。不臆造类别、事实或引用；quote只从对应field原文逐字截取，围岩等级可以从grade字段引用。局部适用范围必须保留，无法定位说明不确定，不能断言全部交集均有该局部现象。
所有结论都需要人工判断，勿声称已核验为正确。'''


def llm_input(pair):
    def side(s):
        r = pair[s]
        return {'record_id': r['record_id'], 'source_file': r['source_file'], 'range_m': r['range_m'],
                'availability': r['gate'], 'previous_segment_range_m': r['previous_segment_range_m'],
                'fields': {k: {'fact_id': v['fact_id'], 'text': v['text'], 'page': v['source']['page']} for k, v in r['fields'].items()},
                'attributes': [{k: u.get(k) for k in ('unit_id', 'unit_type', 'value', 'raw_expression', 'qualifiers', 'comparison_reference', 'comparison_scope', 'structure_identifiers', 'parameter') if k in u} for u in r['units']]}
    return {k: pair[k] for k in ('pair_id', 'intersection_m', 'intersection_text', 'as_of_date')} | {'tsp': side('tsp'), 'hsp': side('hsp')}


def run_llm(output, env_file):
    config = json.loads((output / 'config.json').read_text())
    assert sha(output / 'samples.json') == config['samples_sha256'], 'frozen_samples_changed'
    pairs = json.loads((output / 'samples.json').read_text())
    ledger_path = output / 'api_calls.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    api_key = load_deepseek_api_key(env_file)
    if not api_key:
        raise RuntimeError('DeepSeek_key_unavailable_no_call_made')
    for offset in range(0, len(pairs), 6):
        batch = offset // 6 + 1
        if any(x['batch'] == batch for x in ledger):
            continue
        if len(ledger) >= 2:
            raise RuntimeError('two_call_hard_limit')
        request = {'model': config['model'], 'temperature': config['temperature'], 'max_tokens': config['max_tokens'],
                   'extra_body': {'thinking': {'type': 'disabled'}},
                   'response_format': {'type': 'json_object'}, 'messages': [
                       {'role': 'system', 'content': SYSTEM},
                       {'role': 'user', 'content': json.dumps({'allowed_relations': sorted(RELATIONS), 'attribute_categories': LABELS,
                        'focus_attributes': FOCUS, 'pairs': [llm_input(p) for p in pairs[offset:offset + 6]]}, ensure_ascii=False, separators=(',', ':'))}]}
        save(output / 'llm' / f'batch{batch}.request.json', request)
        entry = {'batch': batch, 'started_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'started',
                 'pair_ids': [p['pair_id'] for p in pairs[offset:offset + 6]], 'sdk_retries': 0,
                 'request_sha256': sha(output / 'llm' / f'batch{batch}.request.json')}
        ledger.append(entry)
        save(ledger_path, ledger)
        print(f'Starting authorized API call {len(ledger)}/2, batch {batch}', flush=True)
        try:
            payload, raw, meta = call_json_model(request, api_key=api_key, base_url='https://api.deepseek.com', timeout_seconds=240, max_retries=0)
            save(output / 'llm' / f'batch{batch}.payload.json', payload)
            save(output / 'llm' / f'batch{batch}.raw_response.json', raw)
            entry.update(meta, status='success')
        except ModelResponseError as e:
            save(output / 'llm' / f'batch{batch}.raw_response.json', e.response)
            entry.update(e.metadata, status='failed', error_type=type(e).__name__)
        except Exception as e:
            entry.update(status='failed', error_type=type(e).__name__, http_status=getattr(e, 'status_code', None))
        save(ledger_path, ledger)
        print(json.dumps(entry, ensure_ascii=False), flush=True)


def check_comparison(pair, item):
    issues, evidence = [], {}
    if item.get('attribute') not in LABELS:
        issues.append('unknown_attribute')
    if item.get('relation') not in RELATIONS:
        issues.append('unknown_relation')
    for s in ('tsp', 'hsp'):
        ref = item.get(s, {})
        field = pair[s]['fields'].get(ref.get('field'))
        quote = compact(ref.get('quote', ''))
        if not field:
            issues.append(s + '_unknown_field')
        elif quote and quote not in compact(field['text']):
            issues.append(s + '_quote_not_literal')
        if field:
            evidence[s] = {'fact_id': field['fact_id'], 'source': field['source'], 'quote_present': bool(quote),
                           'quote_literal': bool(quote and quote in compact(field['text']))}
    text = str(item.get('summary', '')) + str(item.get('limitations', ''))
    allowed = ''.join(v['text'] for s in ('tsp', 'hsp') for v in pair[s]['fields'].values())
    for fault in re.findall(r'F\d+', text):
        if fault not in allowed:
            issues.append('unsupported_fault_id:' + fault)
    if re.search(r'导致PLC|导致设备|确认.*(?:断层|故障)|证明.*更准确', text):
        issues.append('prohibited_causality_or_truth_claim')
    k = item.get('attribute')
    if k in FOCUS and k != 'relative_geology_change' and item.get('relation') in ('literal_agreement', 'main_combination_agreement', 'range_overlap', 'degree_difference', 'explicit_difference', 'main_combination_agreement_with_supplement'):
        if any(not compact(item.get(s, {}).get('quote', '')) for s in ('tsp', 'hsp')):
            issues.append('two_sided_relation_missing_quote')
    return {'program_status': 'FAIL' if issues else 'PASS', 'issues': issues,
            'resolved_evidence': evidence, 'semantic_status': 'REVIEW_requires_human_judgment'}


def summarize(output):
    pairs = json.loads((output / 'samples.json').read_text())
    rules = {r['pair_id']: r for r in json.loads((output / 'rule_results.json').read_text())}
    by_id = {p['pair_id']: p for p in pairs}
    results, checks = {}, []
    for path in sorted((output / 'llm').glob('*.payload.json')):
        for r in json.loads(path.read_text()).get('pairs', []):
            pid = r.get('pair_id')
            if pid not in by_id or pid in results:
                checks.append({'pair_id': pid, 'program_status': 'FAIL', 'issues': ['unknown_or_duplicate_pair_id']})
                continue
            results[pid] = r
            seen = Counter(i.get('attribute') for i in r.get('comparisons', []))
            missing = [k for k in FOCUS if not seen[k]]
            checks.append({'pair_id': pid, 'check': 'required_attribute_coverage', 'program_status': 'FAIL' if missing else 'PASS', 'missing': missing,
                           'semantic_status': 'REVIEW'})
            for i, item in enumerate(r.get('comparisons', [])):
                checks.append({'pair_id': pid, 'comparison_index': i, 'attribute': item.get('attribute'), **check_comparison(by_id[pid], item)})
    for pid in by_id.keys() - results.keys():
        checks.append({'pair_id': pid, 'program_status': 'FAIL', 'issues': ['missing_model_result']})
    save(output / 'llm_results.json', list(results.values()))
    save(output / 'model_program_checks.json', checks)
    rows, candidates = [], []
    for p in pairs:
        pid = p['pair_id']
        rule = rules[pid]
        model = results.get(pid, {})
        ri = {i['attribute']: i for i in rule['comparisons']}
        additions = []
        for n, item in enumerate(model.get('comparisons', [])):
            baseline = ri.get(item.get('attribute'), {})
            if item.get('relation') != baseline.get('relation') or item.get('attribute') not in FOCUS:
                check = check_comparison(p, item)
                candidate = {'pair_id': pid, 'comparison_index': n, 'attribute': item.get('attribute'),
                             'rule_relation': baseline.get('relation'), 'model_relation': item.get('relation'),
                             'model_summary': item.get('summary'), 'model_limitations': item.get('limitations'),
                             'tsp_quote': item.get('tsp', {}).get('quote'), 'hsp_quote': item.get('hsp', {}).get('quote'),
                             'program_status': check['program_status'],
                             'interpretation': 'candidate_difference_not_proven_incremental_value', 'human_judgment': ''}
                candidates.append(candidate)
                additions.append(candidate)
        lines = lambda r: '\n'.join(f"{LABELS.get(i['attribute'], i['attribute'])}: {i['relation']}；" + (i.get('summary') or str(i.get('tsp_values')) + ' / ' + str(i.get('hsp_values'))) for i in r.get('comparisons', []))
        rows.append({'样本ID': pid, '区段': p['intersection_text'], '核查截止日期': p['as_of_date'],
                     'TSP原文': p['tsp']['fields']['geology']['text'], 'HSP原文': p['hsp']['fields']['geology']['text'],
                     'TSP等级': p['tsp']['fields']['grade']['text'], 'HSP等级': p['hsp']['fields']['grade']['text'],
                     'TSP来源': p['tsp']['source_file'], 'HSP来源': p['hsp']['source_file'],
                     '规则结果': lines(rule), 'LLM结果': lines(model) if model else '未取得真实模型输出',
                     'LLM新增内容': additions, '人工判断': ''})
    csv_save(output / '人工核查.csv', rows)
    csv_save(output / '候选增量_待人工判断.csv', candidates)
    save(output / 'comparison_candidates.json', candidates)
    ledger = json.loads((output / 'api_calls.json').read_text()) if (output / 'api_calls.json').exists() else []
    summary = {'selected_pairs': len(pairs), 'model_pairs_returned': len(results),
               'model_comparisons': sum(len(r.get('comparisons', [])) for r in results.values()),
               'program_check_counts': dict(Counter(r['program_status'] for r in checks)),
               'calls': len(ledger), 'successful_calls': sum(r['status'] == 'success' for r in ledger),
               'input_tokens': sum(r.get('input_tokens') or 0 for r in ledger), 'output_tokens': sum(r.get('output_tokens') or 0 for r in ledger),
               'database_unchanged': sha(DATABASE) == json.loads((output / 'config.json').read_text())['database_sha256_before'],
               'warning': '程序检查通过不是语义正确率；候选增量数不是有效新增关系数；人工判断为空。'}
    save(output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'llm', 'summarize'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--database', type=Path, default=DATABASE)
    parser.add_argument('--inventory', type=Path, default=INVENTORY)
    parser.add_argument('--env-file', type=Path, default=Path('/Users/zhaoxiaoda/Desktop/tbm-bitemporal-twin/.env'))
    args = parser.parse_args()
    if args.stage == 'prepare':
        prepare(args.database, args.inventory, OUTPUT_ROOT)
    elif args.stage == 'llm':
        run_llm(args.output, args.env_file)
    else:
        summarize(args.output)


if __name__ == '__main__':
    main()
