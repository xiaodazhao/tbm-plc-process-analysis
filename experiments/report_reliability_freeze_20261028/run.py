"""Preserve baseline evidence, replay repairs, and validate three preselected days."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import random
import shutil
import sys
import traceback
import zipfile

import pandas as pd

from tbm_process.reporting_v2.facts import digest, write_json

ROOT=Path(__file__).resolve().parents[2]
OLD=Path.home()/'Desktop/TBM_多日期验证与跨区段探索_20261028/20261028_run1'
OUT=Path.home()/'Desktop/TBM_可靠性修复与冻结验收_20261028/run1'
ANALYSIS=ROOT/'experiments/plc_mapping_repair_v1/replay/after'
DB=Path.home()/'Desktop/TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite'
MODIFIED=['tbm_process/evidence_graph/three_chapter.py','tbm_process/evidence_graph/joint_reporting.py',
          'tbm_process/reporting_v2/three_chapter_rendering.py']
PROTECTED=['config.json','tbm_process/quality/preprocessing.py','tbm_process/process/platform_detection.py',
           'tbm_process/process/segmentation.py','experiments/plc_mapping_repair_v1/repair.py',
           'tbm_process/evidence_graph/response_analysis.py','tbm_process/reporting_v2/response_features.py']


def read(path):return json.loads(Path(path).read_text())


def csv(path,rows):pd.DataFrame(rows).to_csv(path,index=False,encoding='utf-8-sig')


def protection():
    return {str(p):digest(p) for p in [DB,*[ROOT/f for f in PROTECTED]]}


def audit():
    OUT.mkdir(parents=True,exist_ok=False)
    archive=OLD.parent/'20261028_run1_交付.zip'
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise ValueError('old_archive_CRC_failure')
        for p in OLD.glob('A_reports/*/final/*.pdf'):
            if z.read(OLD.name+'/'+str(p.relative_to(OLD)))!=p.read_bytes():raise ValueError('old_loose_PDF_differs_from_archive')
    for name in MODIFIED:
        dest=OUT/'before_source'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
    protected=protection();write_json(OUT/'protected.before.json',protected)
    history={str(p.relative_to(OLD)):digest(p) for p in OLD.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    write_json(OUT/'history.before.json',history)
    write_json(OUT/'baseline_archive.json',{'path':str(archive),'sha256':digest(archive),'CRC_passed':True,
        'five_loose_PDFs_equal_archived_PDFs':True})
    prior=read(OLD/'A_failure_diagnostics.json');rows=[]
    for item in prior:
        day=item['date'];folder=OLD/'A_reports'/day
        draft=read(folder/'final/narrative.json');pack=read(folder/'evidence_pack.json')
        quality=read(folder/'final/quality.final.json')
        claims=[c for ch in draft['chapters'] for c in ch['claims']]
        implicated=[c for c in claims if any(item['original_error'] in e for v in quality.get('claim_checks',[]) if v.get('text')==c['text'] for e in v['errors'])]
        if not implicated:implicated=[c for c in claims if c['paragraph_id'].startswith('offline_3')]
        category='误报' if '程序误报' in item['diagnostic'] or '程序条件错误' in item['diagnostic'] else '暂不能确定'
        source='joint_reporting.py:draft_quality' if item['original_error'].startswith('unsupported_value') else 'three_chapter.py:check_three_draft'
        rows.append({'date':day,'report_id':day+'.three_chapter','rule':item['original_error'],
            'classification':category,'actual_claims':implicated,'expected_evidence':item['evidence'],
            'source':source,'cross_date':True,'minimal_repair':item['reason'],
            'facts':[{k:f.get(k) for k in ['fact_id','writer_data','canonical_text']}
                     for f in pack['facts'] if f['fact_id'] in {i for c in implicated for i in c['fact_ids']}],
            'independent_human_label':''})
    for s in read(OLD/'A_offline_statuses.json'):
        if s['can_generate']:continue
        path=OLD/'A_reports'/s['date']/'plan.validation.json'
        error=read(path)['errors'] if path.exists() else s.get('reason')
        rows.append({'date':s['date'],'report_id':s['date'],'rule':error,
            'classification':'真实接口错误' if path.exists() else '资料质量阻断（正确拒绝）',
            'source':'three_chapter.py:build_pack / plan_quality' if path.exists() else 'reporting_v2/facts.py:build_facts',
            'cross_date':True,'minimal_repair':'完整保留合法关系及支持闭包；里程异常继续阻断。'})
    additions=[('2023-12-08','comparison_lead_ambiguous','漏检','current/reference主语含糊，应REVIEW，不暗改历史正文'),
        ('2023-11-18','insufficient_sample_scope_ambiguous','真实事实层表达错误','2245条稳定样本并不少；缺少的是合格相邻地质匹配比较'),
        ('2024-05-23','historical_range_written_as_current','真实适用范围表达错误','历史素描DyK1016+119～149不是当日DyK1016+178～185'),
        ('2024-05-23','empty_process_intermittent_template','真实事实层表达错误','无推进不能套用间歇推进；旧实验临时copy_guard已保留'),
        ('2024-09-21','empty_process_intermittent_template','真实事实层表达错误','零推进日原模板仍称间歇推进，需通用修复')]
    for day,rule,category,reason in additions:
        rows.append({'date':day,'report_id':day,'rule':rule,'classification':category,
            'actual_claims':read(OLD/'A_reports'/day/'final/narrative.json'),
            'source':'three_chapter.py:build_pack/check_three_draft','cross_date':True,'minimal_repair':reason})
    write_json(OUT/'FAILURE_AUDIT.full.json',rows)
    csv(OUT/'FAILURE_AUDIT.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows])
    print(json.dumps({'audit_items':len(rows),'paid_calls':0,'old_archive_verified':True}),flush=True)


def rebuild(day,folder,*,prior=False):
    from tbm_process.evidence_graph.analysis import add_daily_analysis
    from tbm_process.evidence_graph.catalog import build_catalog_graph
    from tbm_process.evidence_graph.geology_response import add_source_responses
    from tbm_process.evidence_graph.literal_relations import add_literal_relations
    from tbm_process.evidence_graph.queries import daily_subgraph
    from tbm_process.evidence_graph.three_chapter import build_pack,offline_preview
    from tbm_process.evidence_graph.joint_reporting import plan_quality,draft_quality
    from tbm_process.evidence_graph.response_analysis import validate_analysis
    from tbm_process.evidence_graph.validation import validate_graph
    from tbm_process.geology_store.database import GeologyStore
    from tbm_process.reporting_v2.facts import build_facts
    from tbm_process.reporting_v2.performance import calculate_metrics
    from tbm_process.reporting_v2.response_features import build_response_features
    folder.mkdir(parents=True,exist_ok=False)
    phase=read(ROOT/'config.json');policy=read(ROOT/'configs/plc_response_relations.json')
    registry_path=Path.home()/'Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json'
    config=read(ROOT/'tbm_process/reporting_v2/defaults.json')
    if prior and (OLD/'A_reports'/day/'report_facts.json').exists():
        package=read(OLD/'A_reports'/day/'report_facts.json')
        samples,_=calculate_metrics(pd.read_csv(package['source']['samples']),config)
        catalog=read(OLD/'A_reports'/day/'time_admissible_catalog.json')
        features=read(OLD/'cache/daily_features'/f'{day}.json')
    else:
        store=GeologyStore(DB)
        try:catalog=store.export_catalog(day)
        finally:store.close()
        cp=folder/'time_admissible_catalog.json';write_json(cp,catalog)
        package,samples=build_facts(report_day=day,project=ROOT,analysis=ANALYSIS,catalog_path=cp,
            registry_path=registry_path,source_root=Path.home()/'Desktop/伯舒拉岭数据',config=config)
        features=build_response_features(samples,config,phase,response_id='DAILY:'+day,bounds=package['geology']['day_range_m'])
    samples.time=pd.to_datetime(samples.time)
    graph=build_catalog_graph(catalog,read(registry_path),Path.home()/'Desktop/伯舒拉岭数据')
    add_daily_analysis(graph,package,samples,phase,response_policy=policy,literal_relations=True)
    add_literal_relations(graph,day);add_source_responses(graph,package,samples,phase)
    pack=build_pack(package,samples,graph,features);plan,preview=offline_preview(pack)
    graphcheck=validate_graph(graph,day);analysischeck=validate_analysis(pack['response_analysis'],samples,graph,
        sample_duration_clip_seconds=phase['maximum_sample_duration_seconds'])
    pq=plan_quality(plan,pack)
    for name,value in [('evidence_pack',pack),('report_facts',package),('content_plan',plan),('daily_response_features',features),
        ('daily_evidence_graph',daily_subgraph(graph,day).snapshot()),('graph.validation',graphcheck),
        ('analysis.validation',analysischeck),('plan.validation',pq)]:write_json(folder/(name+'.json'),value)
    return package,samples,graph,pack,plan,{'graph':graphcheck['passed'],'analysis':analysischeck['passed'],'plan':pq['passed'],
                                          'plan_errors':pq['errors']}


def replay():
    from tbm_process.evidence_graph.joint_reporting import draft_quality
    rows=[]
    for oldstatus in read(OLD/'A_offline_statuses.json'):
        day=oldstatus['date'];folder=OUT/'history_replay_v2'/day
        try:
            package,samples,graph,pack,plan,checks=rebuild(day,folder,prior=True)
            status={'date':day,'old_offline_status':oldstatus['status'],**checks,'status':'offline_passed' if all(checks[k] for k in ('graph','analysis','plan')) else 'offline_failed'}
            original=OLD/'A_reports'/day/'generated/draft.before.json'
            if original.exists():
                # Validate the unchanged raw model output, never a hand-edited final.
                draft=read(original);oldpack=read(OLD/'A_reports'/day/'evidence_pack.json')
                oldplan=read(OLD/'A_reports'/day/'content_plan.json')
                exact=draft_quality(draft,oldplan,oldpack)
                current=draft_quality(draft,plan,pack)
                write_json(folder/'historical.raw_model.json',draft)
                write_json(folder/'quality.same_old_input.json',exact);write_json(folder/'quality.rebuilt_input.json',current)
                status.update(old_errors=read(OLD/'A_reports'/day/'final/quality.final.json')['errors'],
                    replay_same_old_input=exact.get('status'),replay_errors=exact['errors'],
                    rebuilt_input_status=current.get('status'),rebuilt_errors=current['errors'])
            rows.append(status)
        except Exception as exc:
            folder.mkdir(parents=True,exist_ok=True)
            failure={'date':day,'status':'blocked','type':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc()}
            write_json(folder/'execution.failure.json',failure);rows.append(failure)
        write_json(OUT/'historical_replay.v2.json',rows);print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    csv(OUT/'historical_replay.v2.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in r.items()} for r in rows])


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['audit','replay','select','generate','deliver']);args=p.parse_args()
    if args.stage=='audit':audit()
    elif args.stage=='replay':replay()
    else:
        from .validation import select,generate,deliver
        {'select':select,'generate':generate,'deliver':deliver}[args.stage]()


if __name__=='__main__':main()
