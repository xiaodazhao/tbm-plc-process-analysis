#!/usr/bin/env python3
"""Compare a saved daily-report revision to its baseline without calling a model."""
import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tbm_process.engineering_reporting.common import load,save,sha,csv_file,compact
from tbm_process.engineering_reporting.organization import coverage_accounting,operating_comparison
import pandas as pd


def prose(folder):
    draft=load(folder/'final_narrative.json')
    text='\n'.join(p['text'] for s in draft['sections'] for p in s['paragraphs'])
    geology='\n'.join(p['text'] for s in draft['sections'] if s['section_id'].startswith('3') for p in s['paragraphs'])
    return {'characters':len(text),'geology_repeated_defensive_phrases':{term:geology.count(term) for term in ['不证明','不表示','不判定','不转换']},
        'by_section':{s['section_id']:sum(len(p['text']) for p in s['paragraphs']) for s in draft['sections']}}


def geology_signature(folder):
    return sorted(json.dumps({k:r[k] for k in ['document_type','original_range_m','original_quote','time','source']},
        ensure_ascii=False,sort_keys=True) for r in load(folder/'admissible_forecasts.json'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--additional-call-log',type=Path,action='append',default=[])
    args=parser.parse_args();folder=args.result;root=folder.parent
    package=load(folder/'report_facts.original.json');old=load(args.baseline/'report_facts.original.json')
    pack=load(folder/'unified_report_input.json');organization=load(folder/'report_organization.json')
    samples=pd.read_csv(folder/'processed_samples.csv.gz');samples.time=pd.to_datetime(samples.time)
    coverage=coverage_accounting(samples,package['daily_overview'],pack['date'])
    operation=operating_comparison(samples,load(folder/'activity_facts.json'),package,pack)
    artifact=load(folder/'artifact.validation.json');html=(folder/(pack['date']+'.integrated.html')).read_text()
    checks={
        'same_underlying_PLC_SHA256':old['source']['samples_sha256']==package['source']['samples_sha256'],
        'daily_statistics_unchanged':old['daily_overview']==package['daily_overview'],
        'same_admitted_forecast_sources_ranges_dates_and_quotes':geology_signature(args.baseline)==geology_signature(folder),
        'coverage_independently_replayed':coverage==organization['coverage'],
        'operating_comparison_replayed_from_saved_samples':operation==organization['operation'],
        'checked_body_is_complete_in_PDF':artifact['PDF_text_same_as_checked_model_text'],
        'no_out_of_page_text_blocks':not artifact['out_of_page_text_blocks'],
        'historical_photos_not_embedded_in_main_report':'src="figures/historical_' not in html,
        'source_index_attachment_exists':(folder/'source_index.html').is_file(),
        'historical_photo_attachment_exists':(folder/'historical_photos.html').is_file(),
        'quality_notice_rendered':'quality-panel' in html and compact('数据质量：有效状态支持') in compact(html),
        'body_rule_checks_passed':load(folder/'final_narrative.validation.json')['passed']}
    calls={}
    logs=[folder/'model_calls.json']+args.additional_call_log
    for path in logs:
        for row in load(path):calls.setdefault(row.get('response_id',str(path)+row['stage']),row)
    result={'checks':checks,'passed':all(checks.values()),'before':prose(args.baseline),'after':prose(folder),
        'before_pages':load(args.baseline/'artifact.validation.json')['pages'],'after_pages':artifact['pages'],
        'coverage':coverage,'comparison':operation['comparison'],
        'actual_API_calls_including_development_attempts':len(calls),
        'input_tokens':sum(r.get('input_tokens',0) for r in calls.values()),
        'output_tokens':sum(r.get('output_tokens',0) for r in calls.values()),
        'call_logs':[{'path':str(p.resolve()),'sha256':sha(p)} for p in logs],
        'not_stable_per_report_cost':True,'independent_expert_fact_labels_available':False,
        'not_engineering_fact_accuracy':True,'semantic_status':'REVIEW'}
    junit=root/'tests.xml'
    if junit.is_file():
        tests=list(ET.parse(junit).getroot().iter('testcase'))
        result['tests']={'count':len(tests),'failures':sum(t.find('failure') is not None or t.find('error') is not None for t in tests)}
    save(root/'QUALITY_REVIEW.json',result)
    csv_file(root/'MANIFEST.csv',[{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p)}
        for p in sorted(root.rglob('*')) if p.is_file() and p.name!='MANIFEST.csv'])
    print(json.dumps({k:v for k,v in result.items() if k not in ['comparison','coverage']},ensure_ascii=False))
    if not result['passed']:raise SystemExit('Saved report quality audit failed')


if __name__=='__main__':main()
