"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
import re
import json
import sqlite3
from pathlib import Path
from .common import *

CHINESE = re.compile(r'[\u3400-\u9fff]+')
LATIN = re.compile(r'[a-zA-Z][a-zA-Z0-9_]{1,}|\d+(?:\.\d+)?')
CHAIN = re.compile(r'(?i)(?<![A-Za-z])(DyK|DK)\s*(\d{3,5})\s*[+＋]\s*(\d{1,3}(?:\.\d+)?)')
HAZARDS = {
    '掉块': ['掉块', '落块', '落石'], '坍塌': ['坍塌', '塌方', '溜塌'],
    '出水': ['出水', '涌水', '突水', '突涌水'], '突泥': ['突泥', '涌泥'],
    '卡机': ['卡机', '卡盾'], '岩爆': ['岩爆'], '大变形': ['大变形', '膨胀', '挤压变形'],
    '有害气体': ['有害气体', '硫化氢', '二氧化碳', '二氧化硫', '二氧化氮', '甲烷', '瓦斯'],
    '高地温': ['高地温', '热害'], '破碎带': ['破碎带', '节理密集带', '软弱围岩'],
}
def _tokens(s):
    """中文2/3字gram + ASCII词 + 整段里程，供SQLite FTS5 unicode61使用。"""
    s = str(s or '').lower()
    seen = set()
    out = []
    for chain in CHAIN.finditer(s):
        prefix = chain.group(1).lower()
        token = f'{prefix}{chain.group(2)}p{chain.group(3).replace(".", "d")}'
        if token not in seen:
            out.append(token); seen.add(token)
    for word in LATIN.findall(s):
        if word not in seen:
            out.append(word); seen.add(word)
    for seg in CHINESE.findall(s):
        if len(seg) == 1:
            grams = [seg]
        elif len(seg) <= 3:
            grams = [seg] + [seg[i:i+2] for i in range(len(seg)-1)]
        else:
            grams = [seg[i:i+2] for i in range(len(seg)-1)]
            grams += [seg[i:i+3] for i in range(len(seg)-2)]
        for gram in grams:
            if gram not in seen:
                out.append(gram); seen.add(gram)
    return out
def mentions(text):
    """Literal source mentions only; preserve negation and never promote them to observed events."""
    found = []
    for category, words in HAZARDS.items():
        for word in words:
            for m in re.finditer(re.escape(word), text):
                before = text[max(0, m.start()-8):m.start()]
                polarity = 'negative' if re.search(r'(?:无|未见|未发现|不存在|未发生)\s*$', before) else 'mentioned'
                found.append({'category': category, 'literal': word, 'polarity': polarity,
                              'offset': [m.start(), m.end()], 'context': text[max(0,m.start()-30):m.end()+45]})
    return found

def local_locations(text):
    """Retain explicit full/short-form locations with local clause context, not a parent-span fault assignment."""
    records = []
    for clause in re.split(r'[。；;\n]', text):
        tokens = []; km = None; prefix = None
        pattern = r'(?i)(?:(DyK|DK)\s*(\d{3,5})\s*)?[+＋]\s*(\d{1,3}(?:\.\d+)?)'
        for match in re.finditer(pattern, clause):
            if match[1]: prefix = 'DyK' if match[1].lower()=='dyk' else 'DK'; km=int(match[2])
            if km is None: continue
            tokens.append({'value':km*1000+float(match[3]),'prefix':prefix,'span':match.span()})
        consumed = set()
        for i in range(len(tokens)-1):
            a,b=tokens[i:i+2]
            connector=clause[a['span'][1]:b['span'][0]].strip()
            if re.fullmatch(r'[～~—–−-]|至|到',connector) and a['prefix']==b['prefix']:
                records.append({'geometry':'interval','prefix':a['prefix'],'start_m':a['value'],'end_m':b['value'],
                                'clause':clause,'hazard_mentions':mentions(clause)})
                consumed.update([i,i+1])
        for i,t in enumerate(tokens):
            if i not in consumed: records.append({'geometry':'point','prefix':t['prefix'],'point_m':t['value'],
                'clause':clause,'hazard_mentions':mentions(clause)})
    return records

def search(database, query, top_k=6):
    terms=[t for t in _tokens(query) if len(t)>1][:40]
    if not terms:return []
    con=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    statement=' OR '.join('"'+t.replace('"','')+'"' for t in terms)
    rows=con.execute('''SELECT c.*,d.path,d.sha256,d.date_text,-bm25(chunks_fts) score
        FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.chunk_id JOIN documents d ON d.id=c.document_id
        WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 80''',(statement,)).fetchall()
    result=[]
    for row in rows:
        value=dict(row);value['bbox']=json.loads(value.pop('bbox_json'));value['semantic_status']='not_verified_by_keyword_retrieval'
        result.append(value)
    con.close()
    return result[:top_k]

def match_measures(hazards, controls, bounds=None, top_k=5):
    matches=[]
    for c in controls:
        cats={m['category'] for m in c['hazard_mentions'] if m['polarity']!='negative'}
        shared=set(hazards)&cats
        if not shared:continue
        all_locations=c['locations']
        if all_locations and not any(v['prefix']=='DyK' for v in all_locations):continue
        locations=[v for v in all_locations if v['prefix']=='DyK' and v['geometry']=='interval']
        spatial=[v for v in locations if bounds and overlap(bounds,[v['start_m'],v['end_m']])]
        if locations and bounds and not spatial:continue
        if c['reported_methods'] and 'TBM' not in c['reported_methods'] and '钻爆法' in c['reported_methods']:continue
        matches.append({**c,'matched_hazard_categories':sorted(shared),
            'scope_match':'source_mentioned_range_overlap_conditions_unverified' if spatial else 'generic_topic_only_no_verified_local_applicability',
            'candidate_priority':len(shared)+(2 if spatial else 0),
            'not_a_recommendation_to_execute':True,'required_review':['原文上下文及限定条件','TBM适用性','批准方案及设计变更','实际实施记录']})
    return sorted(matches,key=lambda r:(-r['candidate_priority'],r['source']['path'],r['source']['PDF_page']))[:top_k]
