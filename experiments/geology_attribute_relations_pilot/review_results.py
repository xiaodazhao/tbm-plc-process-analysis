"""Offline evidence checks and readable exports; no model calls or gold labels."""
from __future__ import annotations

from collections import Counter
import importlib.metadata
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys

from pilot import FOCUS, LABELS, ROOT, csv_save, llm_input, rule_compare, save, sha


def run(output):
    config = json.loads((output / 'config.json').read_text())
    pairs = json.loads((output / 'samples.json').read_text())
    rules = {r['pair_id']: r for r in json.loads((output / 'rule_results.json').read_text())}
    models = {r['pair_id']: r for r in json.loads((output / 'llm_results.json').read_text())}
    diagnostics, claims, paired_labels = [], [], []
    checks = []
    def check(name, passed, details=None):
        checks.append({'check': name, 'status': 'PASS' if passed else 'FAIL', 'details': details})
    check('source_database_sha_unchanged', sha(Path(config['database'])) == config['database_sha256_before'])
    check('frozen_samples_sha_unchanged', sha(output / 'samples.json') == config['samples_sha256'])
    check('rule_results_reproducible', [rule_compare(p) for p in pairs] == list(rules.values()))
    check('12_distinct_pairs', len(pairs) == 12 == len({p['pair_id'] for p in pairs}))
    check('all_selected_pairs_have_real_model_result', all(models.get(p['pair_id']) for p in pairs))
    requested_ids = []
    for f in sorted((output / 'llm').glob('*.request.json')):
        req = json.loads(f.read_text())
        body = json.loads(req['messages'][1]['content'])
        for r in body['pairs']:
            p = next(p for p in pairs if p['pair_id'] == r['pair_id'])
            requested_ids.append(r['pair_id'])
            check(f"same_input_object:{r['pair_id']}", r == llm_input(p),
                  '同一原文、原有单元和范围；未向LLM发送A的比较标签。')
            for side in ('tsp', 'hsp'):
                check(f"same_raw_fields:{r['pair_id']}:{side}",
                      all(r[side]['fields'][k]['text'] == v['text'] and r[side]['fields'][k]['fact_id'] == v['fact_id'] for k, v in p[side]['fields'].items()))
                check(f"prior_date:{r['pair_id']}:{side}",
                      p[side]['gate']['status'] == 'eligible' and
                      (p[side]['gate'].get('available_date') or p[side]['date_metadata']['submission_date']) < p['as_of_date'])
    check('all_12_requested_once', Counter(requested_ids) == Counter(p['pair_id'] for p in pairs))
    anchor = next(p for p in pairs if p['is_anchor'])
    check('anchor_exact_intersection', anchor['intersection_m'] == [1015822, 1015827])
    check('anchor_has_no_F36', all('F36' not in v['text'] for s in ('tsp', 'hsp') for v in anchor[s]['fields'].values()))
    check('anchor_model_did_not_import_F36', 'F36' not in json.dumps(models[anchor['pair_id']], ensure_ascii=False))
    ledger = json.loads((output / 'api_calls.json').read_text())
    check('at_most_two_new_calls_no_sdk_retry', len(ledger) <= 2 and all(x['sdk_retries'] == 0 for x in ledger))
    for p in pairs:
        pid = p['pair_id']
        ri = {i['attribute']: i for i in rules[pid]['comparisons']}
        mi = {i['attribute']: i for i in models.get(pid, {}).get('comparisons', [])}
        for kind in FOCUS:
            paired_labels.append({'pair_id': pid, 'attribute': kind, 'rule_relation': ri[kind]['relation'],
                                  'llm_relation': mi.get(kind, {}).get('relation'),
                                  'same_label': ri[kind]['relation'] == mi.get(kind, {}).get('relation'), 'human_judgment': ''})
        for i, item in enumerate(models.get(pid, {}).get('comparisons', [])):
            kind = item['attribute']
            rule = ri.get(kind, {})
            claims.append({'pair_id': pid, '区段': p['intersection_text'], '序号': i, '属性': LABELS[kind],
                           '规则标签': rule.get('relation'), 'LLM标签': item['relation'], 'LLM判断': item['summary'],
                           'LLM限定': item.get('limitations'), 'TSP原文': item['tsp'].get('quote'), 'HSP原文': item['hsp'].get('quote'),
                           'TSP事实ID': p['tsp']['fields'][item['tsp']['field']]['fact_id'],
                           'HSP事实ID': p['hsp']['fields'][item['hsp']['field']]['fact_id'],
                           'TSP页码': p['tsp']['fields'][item['tsp']['field']]['source']['page'],
                           'HSP页码': p['hsp']['fields'][item['hsp']['field']]['source']['page'], '人工判断': ''})
            # These diagnostics compare literal roles, not engineering truth.
            if rule.get('tsp_values') and rule.get('tsp_values') == rule.get('hsp_values') and item['relation'] == 'explicit_difference':
                diagnostics.append({'pair_id': pid, 'index': i, 'kind': 'same_literal_values_but_difference_label',
                                    'rule_values': rule['tsp_values'], 'actual_model_item': item,
                                    'status': 'program_detectable_internal_label_problem_not_expert_geology_label'})
            if item['relation'] in ('degree_difference', 'explicit_difference', 'range_overlap') and (not item['tsp'].get('quote') or not item['hsp'].get('quote')):
                diagnostics.append({'pair_id': pid, 'index': i, 'kind': 'two_sided_difference_with_one_source_unstated',
                                    'actual_model_item': item, 'status': 'needs_source_role_review_not_proven_geological_difference'})
    csv_save(output / '逐项人工核查_107项.csv', claims)
    csv_save(output / '重点属性标签对照.csv', paired_labels)
    save(output / 'offline_validation.json', checks)
    save(output / 'independent_literal_diagnostics.json', diagnostics)
    stats = {'focus_checks': len(paired_labels), 'focus_equal_label': sum(x['same_label'] for x in paired_labels),
             'focus_different_label': sum(not x['same_label'] for x in paired_labels),
             'diagnostic_flags': len(diagnostics), 'diagnostic_types': dict(Counter(x['kind'] for x in diagnostics)),
             'offline_checks': dict(Counter(x['status'] for x in checks)),
             'not_accuracy': '标签一致/不一致均不代表准确率，独立人工判断全部留空。'}
    save(output / 'offline_stats.json', stats)
    notes_path = output / 'analyst_observations.json'
    if notes_path.exists():
        notes = json.loads(notes_path.read_text())
        note_index = {x['pair_id']: [] for x in notes['items']}
        for item in notes['items']:
            note_index[item['pair_id']].append(item)
        with (output / '人工核查.csv').open(encoding='utf-8-sig', newline='') as f:
            human = list(csv.DictReader(f))
        for row in human:
            row['候选标签差异_不是新增有效关系'] = row.get('候选标签差异_不是新增有效关系') or row['LLM新增内容']
            row['LLM新增内容'] = note_index.get(row['样本ID'], '未确认超出规则的新增信息；不能仅凭标签不同判断有增量。')
            assert row['人工判断'] == ''
        csv_save(output / '人工核查.csv', human)
    lines = ['# 12组原文、规则与真实LLM对照', '', '所有人工判断均待填写；程序通过不代表语义正确。', '']
    for n, p in enumerate(pairs, 1):
        pid = p['pair_id']
        lines += [f"## {n}. {p['intersection_text']}", '', f"样本：`{pid}`；核查日期：{p['as_of_date']}；{p['as_of_basis']}。", '']
        for side, name in [('tsp', 'TSP'), ('hsp', 'HSP')]:
            s = p[side]
            lines += [f"**{name}**：{s['source_file']}", '',
                      f"事实：`{s['fields']['geology']['fact_id']}`；PDF第{s['fields']['geology']['source']['page']}页；提交日期{s['date_metadata']['submission_date']}；原区段{s['range_m']}。", '',
                      '> ' + s['fields']['geology']['text'], '', f"表中建议等级：{s['fields']['grade']['text']}。", '']
        lines += ['| 属性 | 规则结果（保留原值） | LLM原始判断及限定 |', '|---|---|---|']
        mi = {i['attribute']: i for i in models.get(pid, {}).get('comparisons', [])}
        for r in rules[pid]['comparisons']:
            m = mi.get(r['attribute'])
            left = f"{r['relation']}：TSP={r['tsp_values']}；HSP={r['hsp_values']}。{r['note']}"
            right = f"{m['relation']}：{m['summary']}。{m.get('limitations', '')}" if m else '本次模型未选择该补充属性；不能当作资料不存在'
            lines.append(f"| {r['label']} | {left.replace('|', '/')} | {right.replace('|', '/')} |")
        lines += ['', '人工判断：________', '']
    (output / '逐组对照.md').write_text('\n'.join(lines), encoding='utf-8')
    test = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=ROOT, capture_output=True, text=True)
    (output / 'tests.log').write_text(test.stdout + '\n' + test.stderr, encoding='utf-8')
    save(output / 'tests.json', {'command': [sys.executable, '-m', 'pytest', '-q'], 'cwd': str(ROOT), 'returncode': test.returncode})
    save(output / 'environment.json', {'python': sys.version, 'packages': {
        x: importlib.metadata.version(x) for x in ('openai', 'PyMuPDF', 'pytest', 'pandas', 'numpy')
    }})
    sources = [Path(__file__), Path(__file__).with_name('pilot.py'), Path(__file__).with_name('test_pilot.py'), Path(__file__).with_name('README.md'),
               ROOT / 'tbm_process/evidence_graph/catalog.py', ROOT / 'tbm_process/final_reporting/common.py',
               ROOT / 'tbm_process/final_reporting/integrated_llm.py']
    for source in sources:
        dest = output / 'source_snapshot' / source.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    assert all(x['status'] == 'PASS' for x in checks)
    assert test.returncode == 0
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    run(Path(sys.argv[1]))
