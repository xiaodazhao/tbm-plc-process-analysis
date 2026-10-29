"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
from pathlib import Path
import re
import fitz
from .common import *
from .knowledge import local_locations

def duration(seconds):
    s = round(seconds)
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return (f'{h}小时' if h else '') + (f'{m}分' if m else '') + f'{s}秒'

def span(bounds):
    if not bounds:
        return '无交集'
    if bounds[0] == bounds[1]:
        return format_chainage(bounds[0])
    return format_chainage(bounds[0]) + '至' + format_chainage(bounds[1])

def complete_measure(seed):
    """Extend a seed to its same-column numbered item; keep each original line and bounding box."""
    source = seed['source']
    with fitz.open(source['path']) as doc:
        page = doc[source['PDF_page'] - 1]
        box = fitz.Rect(source['bbox'])
        columns = page.rect.width > 900
        right = box.x0 > page.rect.width / 2 if columns else False
        lines = []
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(s['text'] for s in line['spans']).strip()
                b = fitz.Rect(line['bbox'])
                same_column = not columns or (b.x0 > page.rect.width / 2) == right
                if text and same_column and page.rect.height * .08 < b.y0 < page.rect.height * .95:
                    lines.append({'text': text, 'bbox': list(b)})
        lines.sort(key=lambda x: (x['bbox'][1], x['bbox'][0]))
        candidates = [i for i, line in enumerate(lines) if fitz.Rect(line['bbox']).intersects(box)]
        if not candidates:
            raise ValueError('Cannot locate measure source line: ' + seed['control_id'])
        first, last = min(candidates), max(candidates)
        boundary = re.compile(r'^(?:[①②③④⑤⑥⑦⑧⑨⑩]|[A-Z][.．、]|\d+[）)]|[（(]\d+[)）])')
        # Find the item start without crossing another numbered item or a large vertical gap.
        while first > 0 and not boundary.match(lines[first]['text']):
            if lines[first]['bbox'][1] - lines[first-1]['bbox'][3] > 24:
                break
            first -= 1
            if last - first >= 12:
                break
        while last + 1 < len(lines):
            nxt = lines[last + 1]
            if boundary.match(nxt['text']) or nxt['bbox'][1] - lines[last]['bbox'][3] > 24:
                break
            last += 1
            if last - first >= 15:
                break
        selected = lines[first:last + 1]
        heading = next((line['text'] for line in reversed(lines[:first])
                        if re.search(r'TBM.*节理密集带|\d+[）)].*(?:施工|排水|风险)', line['text'])), None)
        text = '\n'.join(line['text'] for line in selected)
    return {**seed, 'expanded_original_quote': text, 'expanded_lines': selected,
            'heading_context': heading, 'context_extension_method': 'same_PDF_page_same_column_numbered_item',
            'source_sha_verified': sha(source['path']) == source['sha256'],
            'not_a_qualified_site_instruction': True}
def spatial_relation(bounds, original_bounds=None, point=None):
    """Positive-length interval intersections, half-open point membership; never semantic similarity."""
    if point is not None:
        inside = bounds[0] <= point < bounds[1]
        return {'geometry':'point','point_m':point,'scope_m':list(bounds),
                'matches':inside,'intersection_m':[point,point] if inside else None}
    hit = overlap(bounds, original_bounds) if original_bounds else None
    return {'geometry':'interval','original_range_m':original_bounds,'scope_m':list(bounds),
            'matches':hit is not None,'intersection_m':hit}

def reference_condition(quote):
    first = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩A-Z\d（）).．、\s]+','',quote)
    first = re.sub(r'\s+','',first)
    match = re.search(r'^([^。；，,]{4,100}?(?:时|地段|情况下|条件下))[，,。]', first)
    return match[1] if match else None

def scoped_geology_text(row, bounds):
    """Do not use a point-only risk clause outside the current scope as a retrieval trigger."""
    kept, excluded = [], []
    for clause in re.split(r'[。；]',row['original_quote']):
        if not clause:
            continue
        locations = local_locations(clause)
        points = [r['point_m'] for r in locations if r['geometry']=='point' and r['prefix']=='DyK']
        if points and all(not spatial_relation(bounds,point=p)['matches'] for p in points):
            excluded.append(clause)
        else:
            kept.append(clause)
    return '。'.join(kept), excluded
