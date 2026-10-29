#!/usr/bin/env python3
"""Read-only numerical/source audit independent of the report-generation validators."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

import fitz
import numpy as np
import pandas as pd


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def audit(root):
    root=Path(root); config=json.loads((root/'method_config.json').read_text())['legacy_report_config']
    records=[]; sources=[]; membership=[]
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',folder.name): continue
        package=json.loads((folder/'legacy_report_facts.json').read_text())
        source=Path(package['source']['samples']); raw=pd.read_csv(source,low_memory=False)
        source_sha=sha(source)
        snapshot=json.loads((folder/'daily_evidence_graph.json').read_text())
        nodes={n['id']:n for n in snapshot['nodes']}
        responses=[json.loads((folder/'daily_response_features.json').read_text())]
        responses.extend(n['data']['features_v2'] for n in nodes.values() if n['type']=='PLCResponse')
        for response in responses:
            for metric,stats in response['metrics'].items():
                mask=pd.Series(False,index=raw.index); listed=[]
                for evidence in stats['source']:
                    mask |= raw.source_file.eq(evidence['source_file']) & raw.source_row.isin(evidence['source_rows'])
                    listed.extend((evidence['source_file'],r) for r in evidence['source_rows'])
                data=raw.loc[mask]; eligible=(data.experimental_phase_label.eq('stable') & data.machine_state.eq('advancing')
                    & data.advance_speed_quality.eq('usable') & data.advance_speed.gt(config['analysis']['minimum_speed_mm_min'])
                    & data.thrust.gt(0) & data.torque.gt(0))
                if metric not in ('advance_speed','thrust','torque'):
                    eligible &= data.rpm_quality.eq('usable_provisional_mapping') & data.rpm_canonical.gt(config['analysis']['minimum_rpm'])
                    eligible &= ~data.rpmsource.fillna('unresolved').astype(str).str.contains('unresolved|unavailable',regex=True)
                pr=data.advance_speed/data.rpm_canonical.replace(0,np.nan)
                if metric in ('pr','fpi','se'):
                    eligible &= pr.ge(config['analysis']['minimum_pr_mm_rev'])
                if metric in raw:
                    values=pd.to_numeric(data[metric],errors='coerce')
                elif metric=='pr': values=pr
                elif metric=='fpi': values=data.thrust/(config['equipment']['cutter_count']*pr)
                else:
                    area=math.pi*config['equipment']['diameter_m']**2/4
                    values=(1000*data.thrust/area+2*math.pi*1000*data.torque/(area*pr/1000))/1e6
                values=values.replace([np.inf,-np.inf],np.nan).dropna().to_numpy()
                expected={'count':len(values),'median':np.median(values) if len(values) else None,
                    'p10':np.quantile(values,.1) if len(values) else None,'p90':np.quantile(values,.9) if len(values) else None,
                    'supported_duration_seconds':float(data.sample_dt_seconds.fillna(0).sum())}
                bad=[]
                for name,value in expected.items():
                    observed=stats[name]
                    if value is None and observed is None: continue
                    if value is None or observed is None or not np.isclose(value,observed,rtol=1e-10,atol=1e-8): bad.append(name)
                if len(listed)!=len(set(listed)) or len(listed)!=len(data): bad.append('missing_duplicate_rows')
                if not eligible.all(): bad.append('ineligible_source_rows')
                if source_sha!=package['source']['samples_sha256']: bad.append('input_sha_mismatch')
                records.append({'date':folder.name,'response_id':response['response_id'],'metric':metric,
                    'rows':len(data),'support_seconds':expected['supported_duration_seconds'],
                    'result':'PROGRAM_NUMERICAL_PASS' if not bad else 'FAIL','errors':'|'.join(bad),
                    'scope':'fresh_read_of_repaired_PLC_exact_rows_not_human_engineering_truth'})
        relation_path=folder/'relations/relations.payload.json'
        candidates={r['candidate_id']:r for r in json.loads((folder/'relation_candidates.json').read_text())['candidates']}
        if relation_path.exists():
            payload=json.loads(relation_path.read_text())
            for relation in payload.get('relations',[]):
                candidate=candidates.get(relation.get('candidate_id'))
                exact=bool(candidate and all(set(relation.get(k,[]))==set(candidate[k]) for k in
                    ('source_fact_ids','target_fact_ids','supporting_evidence_ids')))
                membership.append({'date':folder.name,'candidate_id':relation.get('candidate_id'),
                    'result':'PROGRAM_ID_MEMBERSHIP_PASS' if exact else 'FAIL',
                    'semantic_support':'REVIEW_requires_human_judgment'})
                for quote in relation.get('semantic_basis',{}).get('evidence_quotes',[]):
                    node=nodes.get(quote.get('evidence_id'))
                    if not node or node['type']!='GeologyFact': continue
                    ref=node['data']['source']; path=Path(ref.get('path','')); state='REVIEW_source_missing'; original=''
                    if path.is_file():
                        if sha(path)!=ref.get('sha256'): state='FAIL_SHA'
                        elif path.suffix=='.pdf':
                            page=ref.get('page')
                            with fitz.open(path) as pdf:
                                if isinstance(page,int) and 1<=page<=len(pdf):
                                    original=pdf[page-1].get_text(); state='PAGE_SHA_VERIFIED'
                                else: state='REVIEW_page_unknown'
                        elif path.suffix=='.json':
                            original=path.read_text(encoding='utf-8-sig'); state='JSON_SHA_VERIFIED'
                    normalize=lambda x: re.sub(r'\s+','',str(x))
                    exact_quote=bool(original and normalize(quote.get('quote')) in normalize(original))
                    sources.append({'date':folder.name,'candidate_id':relation['candidate_id'],
                        'evidence_id':node['id'],'source':str(path),'page':ref.get('page'),
                        'source_status':state,'quote_exact_in_original':exact_quote,'quote':quote.get('quote'),
                        'meaning':'exact_quote_does_not_establish_relation_semantics' if exact_quote else 'REVIEW_literal_or_normalization_difference_not_automatic_extraction_error'})
    pd.DataFrame(records).to_csv(root/'independent_numeric_audit.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(sources).to_csv(root/'original_quote_audit.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(membership).to_csv(root/'independent_relation_id_audit.csv',index=False,encoding='utf-8-sig')
    result={'numeric_metric_checks':len(records),'numeric_failures':sum(r['result']=='FAIL' for r in records),
        'relation_id_checks':len(membership),'relation_id_failures':sum(r['result']=='FAIL' for r in membership),
        'original_quote_checks':len(sources),'exact_quote_matches':sum(r['quote_exact_in_original'] for r in sources),
        'source_states':dict(Counter(r['source_status'] for r in sources)),
        'expert_relation_accuracy':None,'note':'This is a separate deterministic audit, not independent human accuracy.'}
    write(root/'independent_audit_summary.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('run_directory',type=Path)
    audit(p.parse_args().run_directory)
