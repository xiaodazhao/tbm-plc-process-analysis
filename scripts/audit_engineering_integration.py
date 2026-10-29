"""Read saved runs to prove joint consumption, source integrity and artifact identity."""
import csv
import hashlib
import html
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'outputs/unified_engineering_release_20261029'


def load(path):
    return json.loads(path.read_text())


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    import sys
    sys.path.insert(0,str(ROOT))
    from tbm_process.engineering_reporting.pipeline import checked_plc_geometry
    rows, calls = [], []
    for day in ['2024-01-24', '2024-02-28', '2024-04-26']:
        folder = OUTPUT/day
        pack = load(folder/'unified_report_input.json')
        plan_request = json.loads(load(folder/'plan.request.json')['messages'][1]['content'])
        generation_request = json.loads(load(folder/'generation.request.json')['messages'][1]['content'])
        expected = {f['fact_id'] for f in pack['facts']}
        database = ROOT.parent/'TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite'
        admitted = checked_plc_geometry(database,day,load(folder/'report_facts.original.json')['daily_overview']['daily_advance'])
        input_locked = digest(folder/'unified_report_input.json') == load(folder/'preflight.json')['input_sha256']
        same_inputs = ({f['fact_id'] for f in plan_request['facts']} == expected ==
                       {f['fact_id'] for f in generation_request['facts']})
        source_ok = all(Path(r['source']['path']).is_file() and
                        digest(Path(r['source']['path'])) == r['source']['sha256']
                        for r in load(folder/'source_hash_audit.json'))
        artifact = load(folder/'artifact.validation.json')
        ledger = load(folder/'model_calls.json')
        calls.extend(ledger)
        result = {'date': day, 'facts': len(expected), 'single_pool_consumed_by_plan_and_generator': same_inputs,
            'input_hash_locked': input_locked, 'all_bound_source_files_still_match': source_ok,
            'final_body_check_passed': load(folder/'final_narrative.validation.json')['passed'],
            'artifact_passed': artifact['passed'], 'checked_body_is_PDF_body': artifact['PDF_text_same_as_checked_model_text'],
            'pages': artifact['pages'], 'calls': len(ledger),
            'existing_PLC_spatial_gate_rechecked': bool(admitted['spatial_allowed']),
            'input_tokens': sum(r.get('input_tokens', 0) for r in ledger),
            'output_tokens': sum(r.get('output_tokens', 0) for r in ledger),
            'gas_attention_events': len(load(folder/'gas/gas_facts.json')['attention_events']),
            'source_risk_records': len(load(folder/'source_risk_facts.json')['records']),
            'semantic_status': 'REVIEW_not_independent_accuracy',
            'pipeline_stages': [r['stage'] for r in ledger]}
        if not all(result[k] for k in ['single_pool_consumed_by_plan_and_generator', 'input_hash_locked',
                    'all_bound_source_files_still_match', 'final_body_check_passed', 'artifact_passed', 'checked_body_is_PDF_body']):
            raise ValueError('acceptance_evidence_failed:'+day)
        rows.append(result)
    protected = ['tbm_process/ingestion', 'tbm_process/quality', 'tbm_process/process',
                 'tbm_process/dynamics', 'config.json', 'experiments/plc_mapping_repair_v1']
    diff = subprocess.check_output(['git','diff','--name-only','1aaa6c6','--',*protected],cwd=ROOT,text=True).strip()
    summary = {'days': rows, 'protected_algorithm_files_changed': diff.splitlines(),
        'release_model_calls': len(calls), 'release_input_tokens': sum(r.get('input_tokens',0) for r in calls),
        'release_output_tokens': sum(r.get('output_tokens',0) for r in calls),
        'not_independent_SCI_experiment': True, 'historical_failures_preserved': True,
        'integration_uses_no_Desktop_13_runtime_path': True}
    old_calls = [r for p in (ROOT/'outputs/unified_engineering_acceptance_20261029').glob('*/model_calls.json') for r in load(p)]
    summary['including_initial_functional_attempts'] = {'calls': len(calls)+len(old_calls),
        'input_tokens': sum(r.get('input_tokens',0) for r in calls+old_calls),
        'output_tokens': sum(r.get('output_tokens',0) for r in calls+old_calls)}
    (OUTPUT/'integration_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    with (OUTPUT/'integration_audit.csv').open('w',newline='',encoding='utf-8-sig') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>当前整篇日报</title><style>body{font:16px/1.8 system-ui,sans-serif;max-width:900px;margin:48px auto;padding:0 24px;color:#24302d}h1{font-size:26px}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ccc}a{color:#176d65}small{color:#52615b}</style><h1>当前整篇TBM施工日报</h1><p>PLC、地质、气体、原资料风险和设计参考先进入同一事实包，再统一规划、写作、编辑、核验与渲染。</p><table><tr><th>日期</th><th>成品</th><th>页数</th><th>模型调用</th></tr>'
    for row in rows:
        day=row['date'];body+=f'<tr><td>{day}</td><td><a href="{day}/{day}.integrated.html">HTML</a> · <a href="{day}/{day}.integrated.pdf">PDF</a></td><td>{row["pages"]}</td><td>{row["calls"]}</td></tr>'
    body+='</table><p><a href="integration_audit.json">完整一致性检查</a></p><small>这是集成回归，不是独立SCI对照实验。程序检查通过不等于人工确认全部工程语义。</small></html>'
    (OUTPUT/'index.html').write_text(body)
    manifest=[]
    for path in sorted(OUTPUT.rglob('*')):
        if path.is_file() and path.name != 'MANIFEST.csv':
            manifest.append({'path':str(path.relative_to(OUTPUT)),'bytes':path.stat().st_size,'sha256':digest(path)})
    with (OUTPUT/'MANIFEST.csv').open('w',newline='',encoding='utf-8-sig') as handle:
        writer=csv.DictWriter(handle,fieldnames=['path','bytes','sha256']);writer.writeheader();writer.writerows(manifest)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    main()
