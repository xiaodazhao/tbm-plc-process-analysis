#!/usr/bin/env python3
"""Read-only inventory for the 2026-10-29 research freeze; never calls an API."""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tbm_process.engineering_reporting.common import csv_file, load, save, sha


def inventory(output):
    output.mkdir(parents=True, exist_ok=True)
    database = ROOT.parent / 'TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite'
    con = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        documents = [dict(r) for r in con.execute('SELECT * FROM documents ORDER BY document_type,document_id')]
        days = [dict(r) for r in con.execute('SELECT * FROM plc_days ORDER BY date')]
        fact_counts = [dict(r) for r in con.execute('SELECT fact_type,information_role,COUNT(*) AS count FROM facts GROUP BY 1,2')]
        response_count = con.execute('SELECT COUNT(*) FROM plc_responses').fetchone()[0]
    finally:
        con.close()
    doc_index = []
    for doc in documents:
        p = Path(doc['original_path']) if doc['original_path'] else None
        exists = p is not None and p.is_file()
        doc_index.append({k: doc[k] for k in ['document_id','document_type','start_m','end_m',
            'available_at','available_date','observation_date','original_path','file_hash']} | {
            'source_exists': exists, 'source_sha_matches': sha(p) == doc['file_hash'] if exists and doc['file_hash'] else None})
    csv_file(output/'geology_source_inventory.csv', doc_index)
    csv_file(output/'plc_day_inventory.csv', days)
    csv_file(output/'geology_fact_type_counts.csv', fact_counts)
    analysis = ROOT/'experiments/plc_mapping_repair_v1/replay/after'
    platforms = pd.read_csv(analysis/'tables/实验平台明细.csv')
    phases = pd.read_csv(analysis/'tables/实验四阶段明细.csv')
    base_rows = []
    for day in days:
        base_rows.append({'date': day['date'], 'platforms': int(platforms.date.eq(day['date']).sum()),
            'stable_phases': int((phases.date.eq(day['date']) & phases.phase_label.eq('stable')).sum()),
            'spatial_allowed': bool(day['spatial_allowed'])})
    csv_file(output/'platform_phase_daily_counts.csv', base_rows)
    result_folder = ROOT/'outputs/report_quality_release_20261029_r2/2024-02-28'
    pack = load(result_folder/'unified_report_input.json')
    graph = load(result_folder/'daily_evidence_graph.json')
    source_index = [s for f in pack['facts'] for s in f['sources']]
    unique = {s['path']:s for s in source_index}
    source_checks = [{'path':p, 'exists':Path(p).is_file(), 'expected_sha256':s.get('sha256'),
                     'sha_matches':Path(p).is_file() and sha(Path(p))==s.get('sha256')} for p,s in sorted(unique.items())]
    csv_file(output/'accepted_report_source_checks.csv', source_checks)
    refresh = load(result_folder/'current_sketch_refresh.json')
    refreshed = [{'record_id':r['record_id'], 'path':r['path'], 'sha256':r['sha256'],
        'exists':Path(r['path']).is_file(),
        'sha_matches':Path(r['path']).is_file() and sha(Path(r['path']))==r['sha256'],
        'confirmed_available_date':r.get('confirmed_available_date'),
        'observation_date':r.get('explicit_date'), 'report_date':r.get('report_date')}
        for r in refresh['entries']]
    csv_file(output/'current_sketch_source_checks.csv', refreshed)
    versions = {}
    for name in ['pandas','numpy','scipy','scikit-learn','matplotlib','PyMuPDF','openai',
                 'sentence-transformers','torch','playwright','pytest']:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'not_installed'
    save(output/'environment.json', {'python':sys.version,'executable':sys.executable,'versions':versions})
    core = [ROOT/'config.json']
    for name in ['ingestion','quality','process','dynamics']:
        core += sorted((ROOT/'tbm_process'/name).glob('*.py'))
    protected = [{'path':str(p.relative_to(ROOT)),'sha256':sha(p)} for p in core]
    csv_file(output/'protected_core_SHA256.csv', protected)
    summary = {
        'inventory_version':'20261029_freeze_read_only_v1', 'new_API_calls':0,
        'PLC': {'days':len(days), 'first_date':min(d['date'] for d in days), 'last_date':max(d['date'] for d in days),
            'spatial_allowed_days':sum(bool(d['spatial_allowed']) for d in days),
            'blocked_dates':[d['date'] for d in days if not d['spatial_allowed']],
            'admitted_start_m':min(d['start_m'] for d in days if d['spatial_allowed']),
            'admitted_end_m':max(d['end_m'] for d in days if d['spatial_allowed']),
            'processed_day_files':len(list((analysis/'processed_samples').glob('*.csv.gz'))),
            'platforms':len(platforms),'phases':len(phases), 'phase_labels':dict(Counter(phases.phase_label)),
            'days_without_platforms':sum(r['platforms']==0 for r in base_rows),
            'days_without_stable_phases':sum(r['stable_phases']==0 for r in base_rows),
            'historical_mapping_audit':load(ROOT/'experiments/plc_mapping_repair_v1/FINAL_SUMMARY.json')},
        'geology_database':{'path':str(database),'sha256':sha(database),'documents':len(documents),
            'document_types':dict(Counter(d['document_type'] for d in documents)),
            'fact_records':sum(r['count'] for r in fact_counts), 'fact_counts':fact_counts,
            'available_timestamp_nonempty':sum(bool(d['available_at']) for d in documents),
            'observation_date_nonempty':sum(bool(d['observation_date']) for d in documents),
            'source_exists':sum(r['source_exists'] for r in doc_index),
            'source_SHA_matches':sum(r['source_sha_matches'] is True for r in doc_index),
            'cached_PLC_response_rows':response_count,
            'current_refreshed_sketch_JSONs':len(refreshed),
            'current_refreshed_sketch_JSONs_SHA_match':sum(r['sha_matches'] for r in refreshed),
            'stored_SKETCH_original_paths_are_legacy_not_current_runtime_sources':True,
            'not_independent_extraction_accuracy':True},
        'accepted_report':{'date':pack['date'],'folder':str(result_folder), 'facts':len(pack['facts']),
            'facts_by_section':dict(Counter(f['section'] for f in pack['facts'])),
            'required_fact_count':sum(bool(f['required']) for f in pack['facts']),
            'nodes':dict(Counter(n['type'] for n in graph['nodes'])),
            'edges':dict(Counter(e['type'] for e in graph['edges'])),
            'all_fact_sources_exist_and_match':all(r['sha_matches'] for r in source_checks),
            'artifact_validation':load(result_folder/'artifact.validation.json'),
            'current_sketch_refresh_counts':{k:v for k,v in load(result_folder/'current_sketch_refresh.json').items() if k!='entries'},
            'not_independent_effectiveness_experiment':True},
        'no_human_labels_inferred':True,
    }
    save(output/'DATA_INVENTORY.json', summary)
    result = subprocess.run(['git','diff','8e3bac2','--','config.json','tbm_process/ingestion',
                             'tbm_process/quality','tbm_process/process','tbm_process/dynamics'],
                            cwd=ROOT,capture_output=True,text=True,check=True)
    save(output/'protected_core_diff.json', {'reference_commit':'8e3bac2','diff_empty':not result.stdout,
        'core_tree_hash':hashlib.sha256(json.dumps(protected,sort_keys=True).encode()).hexdigest()})
    print(json.dumps({'PLC':summary['PLC']|{'historical_mapping_audit':'see file'},
        'geology_documents':summary['geology_database']['document_types'],
        'fact_records':summary['geology_database']['fact_records'],
        'source_exists':summary['geology_database']['source_exists'],
        'source_SHA_matches':summary['geology_database']['source_SHA_matches'],
        'all_accepted_report_sources_match':summary['accepted_report']['all_fact_sources_exist_and_match'],
        'protected_core_unchanged':not result.stdout},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'release/20261029')
    inventory(parser.parse_args().output.resolve())
