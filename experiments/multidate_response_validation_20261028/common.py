"""Shared paths and read-only source projection for these two experiments."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3

import pandas as pd

from tbm_process.evidence_graph.catalog import document_gate
from tbm_process.evidence_graph.literal_relations import literal_units, overlap
from tbm_process.reporting_v2.facts import digest, write_json

PROJECT = Path(__file__).resolve().parents[2]
ANALYSIS = PROJECT/'experiments/plc_mapping_repair_v1/replay/after'
DATABASE = Path.home()/'Desktop/TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite'
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text())


def source_units():
    with sqlite3.connect(DATABASE.as_uri()+'?mode=ro', uri=True) as connection:
        docs = {r[0]: json.loads(r[1]) for r in connection.execute('SELECT document_id,data_json FROM documents')}
        facts = [json.loads(r[0]) for r in connection.execute("SELECT data_json FROM facts WHERE fact_type='geological_description' AND quality_status='PASS'")]
    grouped = {}
    exclusions = Counter()
    for fact in facts:
        role = fact['information_role']
        if role not in ('forecast','observation'):
            exclusions['not_forecast_or_observation'] += 1
            continue
        bounds = [fact.get('start_m'), fact.get('end_m')]
        if None in bounds or bounds[1] <= bounds[0]:
            exclusions['not_positive_interval'] += 1
            continue
        if fact.get('line') != 'right' or fact.get('chainage_system') != 'DyK':
            exclusions['wrong_or_unknown_line_or_chainage_system'] += 1
            continue
        key = (fact['document_id'], *bounds, role)
        unit = grouped.setdefault(key, {'unit_id': f'SU{len(grouped)+1:04d}', 'document_id': fact['document_id'],
            'source_type': docs[fact['document_id']]['document_type'], 'role': role, 'range_m': bounds, 'fact_ids': [],
            'original_texts': [], 'sources': []})
        unit['fact_ids'].append(fact['fact_id'])
        unit['original_texts'].append(fact['original_text'])
        unit['sources'].append(fact['source'])
    for unit in grouped.values():
        unit['original_texts'] = list(dict.fromkeys(unit['original_texts']))
        unit['attributes'] = literal_units('；'.join(unit['original_texts']))
    return docs, list(grouped.values()), dict(exclusions)


def gate_cache(docs, dates):
    return {(did, day): document_gate(doc, day) for did, doc in docs.items() for day in dates}


def write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')


def protection_snapshot():
    files = [PROJECT/p for p in ['config.json','tbm_process/quality/preprocessing.py',
             'tbm_process/process/platform_detection.py','tbm_process/process/segmentation.py',
             'tbm_process/evidence_graph/three_chapter.py','tbm_process/evidence_graph/response_analysis.py',
             'tbm_process/reporting_v2/response_features.py']]
    return {str(p.relative_to(PROJECT)): digest(p) for p in files} | {'geology_database': digest(DATABASE)}
