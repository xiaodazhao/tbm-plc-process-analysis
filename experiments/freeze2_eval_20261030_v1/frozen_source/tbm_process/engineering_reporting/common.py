from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
GASES = ['CO2检测', 'H2S检测', 'SO2检测', 'NO2检测', 'NO检测', 'CH4检测']


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + '\n', encoding='utf-8')


def csv_file(path, rows, fields=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='raise')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def ident(prefix, *values):
    text = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return prefix + ':' + hashlib.sha256(text.encode()).hexdigest()[:16]


def compact(text):
    return re.sub(r'\s+', '', str(text or ''))


CHAIN = re.compile(r'(?i)(?<![A-Za-z])(DyK|DK)\s*(\d{3,5})\s*[+＋]\s*(\d{1,3}(?:\.\d+)?)')


def chainage(text):
    match = CHAIN.fullmatch(str(text).strip())
    if not match: return None
    return {'prefix': 'DyK' if match[1].lower() == 'dyk' else 'DK', 'metres': float(match[2])*1000+float(match[3])}


def interval(text):
    hits = list(CHAIN.finditer(str(text)))
    if len(hits) != 2: return None
    a, b = [chainage(m.group()) for m in hits]
    if a['prefix'] != b['prefix'] or a['metres'] >= b['metres']: return None
    return {'prefix': a['prefix'], 'start_m': a['metres'], 'end_m': b['metres']}


def overlap(a, b):
    if a is None or b is None:return None
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return [lo, hi] if lo < hi else None


def format_chainage(value):
    return f'DyK{int(value)//1000}+{value%1000:06.2f}'


def iso_day(value):
    try: return datetime.fromisoformat(str(value)[:10]).date().isoformat()
    except ValueError: return None
