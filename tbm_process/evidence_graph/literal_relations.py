"""Report-layer literal comparisons. Preserve qualifiers; never rewrite stored facts."""
from __future__ import annotations

from itertools import combinations
import re

from tbm_process.final_reporting.common import CHAINAGE_RANGE_RE, parse_chainage
from .catalog import document_gate
from .store import identity


LABELS = {'lithology': '岩性组合', 'hardness': '岩质软硬', 'joints': '节理裂隙',
          'integrity': '岩体破碎程度', 'weathering': '风化程度', 'water': '出水描述', 'structure': '构造'}
PATTERNS = {
    'lithology': r'(?:地层)?岩性(?:主要)?(?:为|以)|^原岩(?:主要)?为|(?:局部|夹少量).*?(?:压碎岩|断层泥|断层角砾)',
    'hardness': r'岩质(?:软硬不均|较软|较硬|软|硬)',
    'joints': r'(?:节理裂隙|节理|裂隙).*(?:发育|密集)',
    'integrity': r'^(?:岩体|局部)(?:较完整|完整|较破碎|相对破碎|极破碎|破碎)',
    'weathering': r'(?:全|强|中等|弱|微|未)风化',
    'water': r'滴渗水(?:[-~～至]线状出水)?|渗滴水|线[-~～至]股状出水|线状出水|股状出水|未见(?:明显)?(?:出水|地下水)|无水',
    'structure': r'断层(?!角砾|泥)(?:破碎带|影响带)?',
}


def overlap(a, b):
    if None in list(a) + list(b):
        return None
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return [lo, hi] if lo < hi else None


def literal_units(text):
    units = {k: [] for k in LABELS}
    previous = ''
    for match in re.finditer(r'[^，,。；;\n]+', text):
        raw = match.group().strip()
        compact = re.sub(r'\s+', '', raw)
        for kind, pattern in PATTERNS.items():
            if not re.search(pattern, compact) or (kind == 'water' and '风险' in compact):
                continue
            unit = {'text': raw, 'span': [match.start(), match.end()],
                    'locality': [q for q in ('局部', '少量', '原岩', '掌子面右侧', '隧道右侧') if q in compact],
                    'ranges_m': [[parse_chainage(a), parse_chainage(b)] for a, b in CHAINAGE_RANGE_RE.findall(compact)]}
            if kind == 'integrity' and re.search(r'掌子面右侧|隧道右侧', previous):
                unit['locality'].append('前句侧向限定是否承接待核实')
            if kind == 'structure':
                unit['fault_ids'] = re.findall(r'F\s*\d+', compact)
            units[kind].append(unit)
        previous = compact
    return units


def compare_units(kind, a, b):
    av, bv = [x['text'] for x in a], [x['text'] for x in b]
    if not av or not bv:
        return 'source_only_or_unknown', '仅一方明确记载；未提及不代表不存在。'
    normalize = lambda x: re.sub(r'\s+', '', x).replace('石炭二叠系', '')
    na, nb = [normalize(x) for x in av], [normalize(x) for x in bv]
    if na == nb:
        return 'same_literal_attributes', '保留各自原文及限定。'
    if kind == 'lithology':
        main = lambda rows: [re.sub(r'^(?:推测)?(?:该段)?(?:地层)?岩性(?:主要)?(?:为|以)', '', normalize(x['text']))
                             for x in rows if not x['locality']]
        if main(a) and main(a) == main(b):
            return 'main_combination_with_supplement', '主体组合相符；附加成分保留局部、少量或原岩限定，未定位其在交集内的具体位置。'
    if kind in ('joints', 'integrity'):
        prefix = r'^.*?(?:节理裂隙|节理|裂隙)' if kind == 'joints' else r'^岩体'
        ends = lambda vs: {part for v in vs for part in re.split(r'[-~～—至]', re.sub(prefix, '', v))}
        if ends(na) & ends(nb):
            return 'range_overlap', '原文程度范围存在端点重叠，不判定地质矛盾。'
    return 'different_descriptions', '具体描述不同，不选择正确一方，不自动判定冲突。'


def add_literal_relations(graph, day):
    context = graph.metadata['daily_contexts'][day]
    targets = [context['day_range_m']] + ([context['ahead_range_m']] if context['ahead_range_m'] else [])
    descriptions = [n for n in graph.typed('GeologyFact') if n['data']['fact_type'] == 'geological_description'
                    and n['data']['information_role'] in ('forecast', 'observed')
                    and document_gate(graph.nodes[n['data']['document_id']]['data'], day)['status'] == 'eligible'
                    and n['data']['extraction_quality']['status'] == 'PASS']
    added = []
    for left, right in combinations(descriptions, 2):
        a, b = left['data'], right['data']
        if a['document_id'] == b['document_id']:
            continue
        common = overlap([a['start_m'], a['end_m']], [b['start_m'], b['end_m']])
        if not common:
            continue
        roles = {a['information_role'], b['information_role']}
        if roles == {'forecast'}:
            portions = [x for target in targets if (x := overlap(common, target))]
            kind = 'geological_forecast_description_comparison'
        elif roles == {'forecast', 'observed'}:
            obs, forecast = (a, b) if a['information_role'] == 'observed' else (b, a)
            od = graph.nodes[obs['document_id']]['data']['metadata']
            asof = od.get('observation_date') or od.get('available_date')
            if not asof or document_gate(graph.nodes[forecast['document_id']]['data'], asof).get('role') != 'prior_document':
                continue
            if obs['document_id'] not in context['observation_document_ids']:
                continue
            portions, kind = [common], 'forecast_observation_description_comparison'
        else:
            continue
        ua, ub = literal_units(a['original_description']), literal_units(b['original_description'])
        for portion in portions:
            comparisons = []
            for attribute in LABELS:
                def effective(rows):
                    return [x for x in rows if not x['ranges_m'] or any(overlap(r, portion) for r in x['ranges_m'])]
                aa, bb = effective(ua[attribute]), effective(ub[attribute])
                if not aa and not bb:
                    continue
                result, explanation = compare_units(attribute, aa, bb)
                comparisons.append({'attribute': attribute, 'label': LABELS[attribute], 'left': aa, 'right': bb,
                                    'result': result, 'explanation': explanation})
            if not comparisons:
                continue
            key = identity('LC', day, left['id'], right['id'], portion)
            data = {'report_date': day, 'comparison_type': kind, 'chainage_range_m': portion, 'time_range': None,
                    'source_fact_ids': [left['id'], right['id']], 'source_response_ids': [], 'attribute': 'literal_attribute_bundle',
                    'result': 'literal_source_comparison_requires_review', 'measured_difference': comparisons,
                    'comparison_method': 'source_bound_clauses_preserve_degree_ranges_locality_and_rock_combination_v1',
                    'limitations': ['prediction_not_observation', 'local_terms_not_broadcast', 'nominal_chainage_only',
                                    'report_available_date_not_verified_observation_time', 'not_causal_or_accuracy_evidence']}
            graph.add_node('EngineeringComparison', key, data)
            for support in data['source_fact_ids']:
                graph.add_edge('COMPARES', key, support, {'method': data['comparison_method']})
                graph.add_edge('DERIVED_FROM', key, support, {'method': data['comparison_method']})
            added.append(key)
    return added
