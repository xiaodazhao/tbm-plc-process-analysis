#!/usr/bin/env python3
"""Summarize existing pilot runs and package evidence; never call a model."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tbm_process.evidence_graph.joint_reporting import draft_quality
from tbm_process.final_reporting.common import load_deepseek_api_key


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def assemble(root,archive,env_file=None):
    root=Path(root); rows=[]; calls=[]; audits=[]; summaries=[]
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not (folder/'summary.json').is_file(): continue
        summary=json.loads((folder/'summary.json').read_text())
        if summary.get('mode')!='llm': continue
        ledger=json.loads((folder/'model_calls.json').read_text())
        calls.extend({**r,'run':folder.name} for r in ledger)
        summaries.append({'run':folder.name,**summary})
        if (folder/'independent_audit_summary.json').exists():
            audits.append({'run':folder.name,**json.loads((folder/'independent_audit_summary.json').read_text())})
        for day in summary['dates']:
            date=day['date']
            for group,status in day['groups'].items():
                f=folder/date/group
                row={'run':folder.name,'date':date,'group':group,'original_status':status['status'],
                     'actual_llm_text':bool(status.get('actual_llm_report')),
                     'original_program_quality_passed':status.get('program_checks_passed'),
                     'revision_accepted':status.get('revised',False),'changed_claims':status.get('changed_claims',0),
                     'current_program_recheck_passed':None,'semantic_truth':'REVIEW_not_expert_confirmed'}
                if (f/'narrative.json').exists():
                    narrative=json.loads((f/'narrative.json').read_text())
                    plan=json.loads((f/'content_plan.json').read_text())
                    pack=json.loads((folder/date/f"evidence_pack.{'A' if group=='A' else 'B'}.json").read_text())
                    recheck=draft_quality(narrative,plan,pack)
                    write(f/'postcheck_current'/'quality.json',recheck)
                    row.update(current_program_recheck_passed=recheck['passed'],
                        recheck_errors='|'.join(recheck['errors']),
                        claim_count=sum(len(c['claims']) for c in narrative['chapters']),
                        distinct_support_ids=len({i for ch in narrative['chapters'] for c in ch['claims'] for i in c['fact_ids']}),
                        relation_mentions=sum(len(c.get('relation_ids',[])) for ch in narrative['chapters'] for c in ch['claims']),
                        repeated_exact_sentences=sum(n-1 for n in Counter(c['text'] for ch in narrative['chapters'] for c in ch['claims']).values() if n>1))
                rows.append(row)
    if len(calls)>24: raise ValueError('Actual task call count exceeds24')
    aggregate={'actual_new_model_calls':len(calls),'call_stages':dict(Counter(c['stage'] for c in calls)),
        'input_tokens':sum(c.get('input_tokens') or 0 for c in calls),
        'output_tokens':sum(c.get('output_tokens') or 0 for c in calls),
        'returned_models':dict(Counter(c.get('model_returned') for c in calls)),
        'transport_failures':sum(c['status']!='success' for c in calls),
        'unique_model_generated_original_drafts':sum(c['stage']=='generation' and c['status']=='success' for c in calls),
        'expert_accuracy':None,'audits':audits,'runs':summaries,'group_records':rows,
        'comparison_warning':'Development repetitions and different interface revisions must not be pooled as a frozen fair benchmark.'}
    write(root/'aggregate_summary.json',aggregate)
    write(root/'all_model_calls.json',calls)
    fields=sorted({k for row in rows for k in row})
    with (root/'A_B_C_results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    quick=root/'先看这里'; quick.mkdir(exist_ok=True)
    for source,name in [
        (root/'20261027T181542/2024-04-25/C/display_corrected/2024-04-25.candidate.pdf','2024-04-25_程序核验候选_仍待工程语义审查.pdf'),
        (root/'20261027T182815/2024-04-26/C/display_corrected/2024-04-26.candidate.pdf','2024-04-26_未通过事实检查_研究失败稿.pdf')]:
        if source.is_file(): shutil.copy2(source,quick/name)
    # Complete current local code, separate from per-run snapshots of earlier versions.
    code=root/'source_code_current'
    for base in ['tbm_process','tests','configs']:
        for p in (ROOT/base).rglob('*'):
            if p.is_file() and p.suffix in {'.py','.json','.html','.css','.md'} and '__pycache__' not in p.parts:
                target=code/p.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target)
    for p in list((ROOT/'scripts').glob('*.py'))+list((ROOT/'experiments/plc_mapping_repair_v1').glob('*.py'))+list(ROOT.glob('requirements*.txt'))+[ROOT/'config.json',ROOT/'pytest.ini',ROOT/'run_analysis.py']:
        if p.is_file():
            target=code/p.relative_to(ROOT); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target)
    for p in [ROOT/'docs/PLC_RESPONSE_RELATIONS_V2.md',ROOT/'docs/EVIDENCE_GRAPH_JOINT_REPORTING.md']:
        target=root/'documentation'/p.name; target.parent.mkdir(exist_ok=True); shutil.copy2(p,target)
    frozen={}
    for run in summaries:
        p=root/run['run']; before=json.loads((p/'protected_sources.before.json').read_text()); after=json.loads((p/'protected_sources.after.json').read_text())
        frozen[run['run']]={'before_equals_after':before==after,'current_equals_before':all(Path(k).is_file() and sha(Path(k))==v for k,v in before.items()),'files':len(before)}
    write(root/'frozen_sources_check.json',frozen)
    candidates=[p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and not any(x.startswith('.pytest') for x in p.parts)
                and p.suffix!='.pyc' and p.name not in {'MANIFEST.csv','PACKAGE_CHECK.json','.DS_Store'}]
    actual_key=load_deepseek_api_key(env_file) if env_file else None
    findings=[]
    for p in candidates:
        data=p.read_bytes()
        if p.name=='.env' or p.suffix in {'.pem','.key'} or (actual_key and actual_key.encode() in data): findings.append(str(p.relative_to(root)))
        if p.suffix in {'.json','.py','.md','.txt','.log','.csv','.html'} and (re.search(rb'sk-[A-Za-z0-9]{24,}',data) or re.search(rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY',data)):
            findings.append(str(p.relative_to(root)))
    if findings:
        write(root/'security_failure.json',{'paths':sorted(set(findings))}); raise ValueError('Credential scan failed; no archive created')
    manifest=[]
    for p in sorted(candidates): manifest.append({'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p)})
    with (root/'MANIFEST.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['path','bytes','sha256']); w.writeheader(); w.writerows(manifest)
    archive=Path(archive)
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in candidates+[root/'MANIFEST.csv']: z.write(p,root.name+'/'+str(p.relative_to(root)))
    with zipfile.ZipFile(archive) as z:
        if z.testzip(): raise ValueError('ZIP CRC failed')
        for row in manifest:
            data=z.read(root.name+'/'+row['path'])
            if hashlib.sha256(data).hexdigest()!=row['sha256']: raise ValueError('ZIP hash mismatch')
        count=len(z.namelist())
    result={'zip':str(archive),'bytes':archive.stat().st_size,'files':count,'sha256':sha(archive),
        'CRC_and_manifest_SHA256_verified':True,'credential_scan_passed':True,
        'actual_API_calls':len(calls),'no_new_API_calls_in_packaging':True}
    write(root/'PACKAGE_CHECK.json',result)
    archive.with_suffix(archive.suffix+'.sha256').write_text(result['sha256']+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('directory',type=Path); p.add_argument('--zip',type=Path,required=True)
    p.add_argument('--secret-env-file',type=Path)
    args=p.parse_args(); assemble(args.directory,args.zip,args.secret_env_file)
