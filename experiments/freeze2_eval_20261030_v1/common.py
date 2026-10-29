"""Independent experiment utilities; production sources are read-only."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE/'frozen_source'))
os.environ.setdefault('HF_HUB_OFFLINE','1')
os.environ.setdefault('TRANSFORMERS_OFFLINE','1')
PYTHON = Path(sys.executable)
VERSION = 'TBM-Daily-20261030-freeze2'
SEED = 20261029
DEVELOPMENT = ['2024-01-24', '2024-02-28', '2024-03-15', '2024-04-26']
PATHS = {
    'analysis': ROOT/'experiments/plc_mapping_repair_v1/replay/after',
    'source_root': ROOT.parent/'伯舒拉岭数据',
    'database': ROOT.parent/'TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite',
    'registry': ROOT.parent/'TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json',
    'resources': ROOT/'local_data/engineering_context',
    'context_config': HERE/'frozen_source/configs/engineering_context.json',
}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str)+'\n', encoding='utf-8')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def csv_write(path, rows, fields=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()})


def csv_read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def now():
    return datetime.now(timezone.utc).isoformat()


def log(message):
    line=now()+' '+message
    print(line, flush=True)
    with (HERE/'execution.log').open('a',encoding='utf-8') as f:f.write(line+'\n')


def freeze_check():
    rows = csv_read(ROOT/'release/20261029/SOURCE_MANIFEST.csv')
    changed = [r['path'] for r in rows if not (ROOT/r['path']).is_file() or sha(ROOT/r['path'])!=r['sha256']]
    identity = load(ROOT/'release/20261029/FREEZE.json')
    candidate=HERE/'FREEZE.json'
    candidate_changes=[]
    if candidate.exists():
        for name,h in load(candidate)['source_SHA256'].items():
            path=HERE/'frozen_source'/name
            if not path.is_file() or sha(path)!=h:candidate_changes.append(name)
    return {'version':identity['version'], 'candidate_version':VERSION,'source_files':len(rows), 'changed':changed,
        'candidate_changes':candidate_changes,
        'accepted_PDF_unchanged':sha(ROOT/identity['accepted_report'])==identity['accepted_report_sha256'],
        'passed':not changed and not candidate_changes and sha(ROOT/identity['accepted_report'])==identity['accepted_report_sha256']}


class BudgetExceeded(RuntimeError):
    pass


class Calls:
    """Reserve before transport; failures and interrupted attempts consume budget."""
    def __init__(self, scope):
        self.scope=scope; self.path=HERE/'calls.sqlite'
        with sqlite3.connect(self.path, timeout=30) as c:
            c.execute('CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, scope TEXT, date TEXT, arm TEXT, stage TEXT, status TEXT, data TEXT)')

    def reserve(self, day, arm, stage, folder, request):
        if self.scope not in {'interface','formal','development_fix','relations'}:
            raise ValueError('unknown_budget_scope')
        c=sqlite3.connect(self.path, timeout=30)
        try:
            c.execute('BEGIN IMMEDIATE')
            n=c.execute('SELECT COUNT(*) FROM calls WHERE scope=?',(self.scope,)).fetchone()[0]
            limit={'interface':24,'formal':180,'development_fix':4,'relations':20}[self.scope]
            if n>=limit:raise BudgetExceeded(self.scope+'_hard_cap')
            if self.scope in {'formal','relations'}:
                total=c.execute("SELECT COUNT(*) FROM calls WHERE scope IN ('formal','relations')").fetchone()[0]
                if total>=180:raise BudgetExceeded('formal_and_relations_combined_hard_cap_180')
            data={'started_utc':now(),'folder':str(folder),'request_sha256':digest(request),
                  'model_requested':request['model'],'actual_http_retries':0}
            cursor=c.execute('INSERT INTO calls(scope,date,arm,stage,status,data) VALUES(?,?,?,?,?,?)',
                (self.scope,day,arm,stage,'RESERVED',json.dumps(data)))
            identity=cursor.lastrowid;c.commit();return identity,data
        finally:c.close()

    def finish(self, identity, status, data):
        with sqlite3.connect(self.path, timeout=30) as c:
            c.execute('UPDATE calls SET status=?,data=? WHERE id=?',(status,json.dumps(data,ensure_ascii=False),identity))

    def rows(self):
        with sqlite3.connect(self.path, timeout=30) as c:
            return [dict(zip(['call_id','scope','date','arm','stage','status'],r[:6]))|json.loads(r[6])
                    for r in c.execute('SELECT id,scope,date,arm,stage,status,data FROM calls ORDER BY id')]


def call(scope, day, arm, stage, request, folder):
    from tbm_process.final_reporting.common import load_deepseek_api_key
    from tbm_process.final_reporting.integrated_llm import call_json_model, ModelResponseError
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if (folder/(stage+'.request.json')).exists():
        raise RuntimeError('stage_already_attempted_no_automatic_retry')
    key=load_deepseek_api_key(ROOT/'.env')
    if not key:raise RuntimeError('API_KEY_NOT_CONFIGURED')
    budget=Calls(scope)
    identity,data=budget.reserve(day,arm,stage,folder,request)
    save(folder/(stage+'.request.json'),request)
    log(f'{scope} {day} {arm} {stage} call={identity} started')
    try:
        payload,response,metadata=call_json_model(request,api_key=key,base_url='https://api.deepseek.com',timeout_seconds=240,max_retries=0)
        save(folder/(stage+'.response.json'),response)
        save(folder/(stage+'.payload.json'),payload)
        metadata['usage_raw']=response.get('usage',{})
        save(folder/(stage+'.metadata.json'),metadata)
        data.update(metadata);data['finished_utc']=now()
        budget.finish(identity,'SUCCESS',data)
        log(f'{scope} {day} {arm} {stage} call={identity} success')
        return payload
    except Exception as exc:
        if isinstance(exc,ModelResponseError):
            save(folder/(stage+'.response.json'),exc.response)
            save(folder/(stage+'.metadata.json'),exc.metadata)
            data.update(exc.metadata);data['usage_raw']=exc.response.get('usage',{})
            data['response_parse_failure']=exc.reason
        data.update({'finished_utc':now(),'error_class':type(exc).__name__,
                     'http_status':getattr(exc,'status_code',None)})
        budget.finish(identity,'FAILED',data)
        save(folder/(stage+'.failure.json'),data)
        log(f'{scope} {day} {arm} {stage} FAILED {type(exc).__name__}')
        raise
