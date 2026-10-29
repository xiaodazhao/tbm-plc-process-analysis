"""Text-only paragraph patches with immutable hashes and non-target preservation."""
from copy import deepcopy
import hashlib
import json
import re

from .writer import model_facts


def fingerprint(paragraph):
    return hashlib.sha256(json.dumps(paragraph,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def targets(draft,errors):
    locations={}
    for error in errors:
        m=re.match(r'^(\d(?:\.\d)?):(\d+):',error)
        if not m:raise ValueError('nonlocal_issue_requires_review_not_whole_report_rewrite')
        key=(m[1],int(m[2])-1)
        locations.setdefault(key,[]).append(error)
    result=[]
    for (sid,index),issues in sorted(locations.items()):
        section=next(s for s in draft['sections'] if s['section_id']==sid)
        paragraph=section['paragraphs'][index]
        if not paragraph.get('fact_ids'):raise ValueError('missing_source_binding_requires_review')
        result.append({'section_id':sid,'paragraph_index':index,'expected_sha256':fingerprint(paragraph),
            'text':paragraph['text'],'fact_ids':paragraph['fact_ids'],'issues':issues})
    return result


def request(pack,draft,errors,model):
    selected=targets(draft,errors);ids={i for p in selected for i in p['fact_ids']}
    facts=[f for f in model_facts(pack) if f['fact_id'] in ids]
    return {'model':model,'temperature':.1,'max_tokens':4000,'response_format':{'type':'json_object'},
        'extra_body':{'thinking':{'type':'disabled'}},'messages':[
            {'role':'system','content':'仅修改给定的问题段落，不返回整篇，不增加数值、原因、措施或来源。未证实问题允许说明无法修订。返回JSON {replacements:[{section_id,paragraph_index,expected_sha256,text}],unresolved:[]}。只允许这些段落和原哈希；不得改其他段落或fact_ids。'},
            {'role':'user','content':json.dumps({'targets':selected,'facts':facts},ensure_ascii=False)}]}


def apply(draft,payload,errors):
    approved={(r['section_id'],r['paragraph_index']):r for r in targets(draft,errors)}
    revised=deepcopy(draft);seen=set()
    if not isinstance(payload,dict):raise ValueError('invalid_patch_schema')
    if not isinstance(payload.get('replacements',[]),list) or not isinstance(payload.get('unresolved',[]),list):
        raise ValueError('invalid_patch_schema')
    if set(payload)-{'replacements','unresolved'}:raise ValueError('unexpected_patch_fields')
    for row in payload.get('replacements',[]):
        if not isinstance(row,dict):raise ValueError('invalid_patch_schema')
        if set(row)!={'section_id','paragraph_index','expected_sha256','text'}:raise ValueError('invalid_patch_schema')
        key=(row['section_id'],row['paragraph_index'])
        if key not in approved or key in seen:raise ValueError('unauthorized_or_duplicate_patch_target')
        if row['expected_sha256']!=approved[key]['expected_sha256']:raise ValueError('stale_patch_hash')
        if not isinstance(row['text'],str) or not row['text'].strip():raise ValueError('empty_patch_deletion_rejected')
        p=next(s for s in revised['sections'] if s['section_id']==key[0])['paragraphs'][key[1]]
        p['text']=row['text'];seen.add(key)
    unchanged=[]
    for before,after in zip(draft['sections'],revised['sections']):
        for n,(left,right) in enumerate(zip(before['paragraphs'],after['paragraphs'])):
            key=(before['section_id'],n)
            if key not in seen and left!=right:raise ValueError('out_of_target_mutation')
            if left.get('fact_ids')!=right.get('fact_ids'):raise ValueError('source_binding_changed')
            if key not in seen:unchanged.append({'section_id':key[0],'paragraph_index':n,'sha256':fingerprint(left)})
    return revised,{'changed_targets':[{'section_id':s,'paragraph_index':n} for s,n in sorted(seen)],
        'untouched_paragraphs':unchanged,'source_IDs_unchanged':True,
        'correct_information_retention':'REVIEW_required_not_guaranteed_by_text_or_ID_hash'}
