#!/usr/bin/env python3
"""Audit cached response analysis first; optionally generate with at most two API calls."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import html

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from tbm_process.evidence_graph.catalog import SourceAudit, compact
from tbm_process.evidence_graph.joint_reporting import ModelSession, draft_quality, plan_quality, run_writer
from tbm_process.evidence_graph.response_analysis import correct_comparison_order, validate_analysis
from tbm_process.evidence_graph.store import EvidenceGraph
from tbm_process.evidence_graph.three_chapter import build_pack, offline_preview, requests
from tbm_process.final_reporting.common import load_deepseek_api_key
from tbm_process.reporting_v2.facts import digest, duration, write_json
from tbm_process.reporting_v2.performance import calculate_metrics
from scripts.run_three_chapter_report import render


def cell(value):
    return str(value).replace('|', '/').replace('\n', ' ')


def write_tables(output, analysis):
    rows = []
    for w in analysis['time_windows']:
        rows.append({'窗口ID': w['window_id'], '开始': w['start_time'], '结束': w['end_time'],
                     '推进活动': cell(w['activity_time_range']), '平台': w['platform_id'], '样本数': w['sample_count'],
                     '支持秒数': w['supported_duration_seconds'], '首末跨度秒数': w['elapsed_span_seconds'],
                     '首末四分之一条数': w['quarter_sample_count'], '参数趋势': w['pattern']['description'],
                     '实际变化': json.dumps({k: {x: v[x] for x in ('initial_median', 'final_median', 'absolute_change', 'unit')}
                                              for k, v in w['changes'].items()}, ensure_ascii=False),
                     '扭矩CV': w['torque_cv'], '入选': w['selected'],
                     '选择理由': '|'.join(w['selection_reasons']), '未选理由': '|'.join(w['exclusion_reasons']),
                     '原始行': json.dumps(w['source'], ensure_ascii=False), '限制': '|'.join(w['limits'])})
    pd.DataFrame(rows).to_csv(output/'表A_全部时间窗口与选择理由.csv', index=False, encoding='utf-8-sig')
    write_json(output/'相邻窗口波动比较.json', analysis['fluctuation_comparisons'])
    b_rows = []
    for unit in analysis['segment_analyses']:
        for role in ('reference', 'current'):
            p = unit[role]
            for geology in p['geology'] or [{}]:
                b_rows.append({'分析ID': unit['analysis_unit_id'], '角色': role, 'PLC区段': p['range_display'],
                    '样本数': p['sample_count'], '支持秒数': p['time_scope']['supported_duration_seconds'],
                    '速度中位数_mm_min': p['metrics']['advance_speed']['median'],
                    '推力中位数_kN': p['metrics']['thrust']['median'], '扭矩中位数_kNm': p['metrics']['torque']['median'],
                    '转速中位数_r_min': p['metrics']['rpm_canonical']['median'],
                    '转速合格样本数': p['metrics']['rpm_canonical']['count'], '地质事实ID': geology.get('fact_id'),
                    '资料类型': geology.get('source_type'), '原始地质范围': geology.get('original_range_display'),
                    '真实交集': cell(geology.get('intersection_range_m')), '原文': geology.get('original_description'),
                    '原报告参照': json.dumps(geology.get('relative_comparison'), ensure_ascii=False),
                    '可用日期': geology.get('available_date'), '原始观测日期': geology.get('observation_date'),
                    '来源': json.dumps(geology.get('source'), ensure_ascii=False),
                    '变化幅度': json.dumps(unit['changes'], ensure_ascii=False),
                    '确定性分析': unit['machine_observation'], '可比性': json.dumps(unit['operating_comparability'], ensure_ascii=False),
                    '限制': '|'.join(unit['limits']), '同一来源不独立': True})
    pd.DataFrame(b_rows).to_csv(output/'表B_地质区段与机器响应.csv', index=False, encoding='utf-8-sig')
    lines = [f"# {analysis['date']} 独立运行现象与地质对应分析", '', '本文件在任何模型调用之前由程序生成。', '',
             '## 表A 代表性时间窗口', '', '| 时间 | 样本 | 支持时长 | 首末跨度 | 已计算趋势 | 选择理由 |',
             '|---|---:|---|---|---|---|']
    for w in analysis['selected_time_windows']:
        lines.append('| ' + ' | '.join(map(cell, [w['start_time'][11:]+'至'+w['end_time'][11:], w['sample_count'],
                     duration(w['supported_duration_seconds']), duration(w['elapsed_span_seconds']),
                     w['pattern']['description'], '活动与方向类型覆盖、按时间选取，原始行和四分之一统计复核通过'])) + ' |')
    lines += ['', '所有已计算窗口（含未选窗口及理由）见表A CSV。首末数值均取同一窗口首末各 floor(N/4) 条记录的中位数。', '',
              '## 表B 地质区段与机器响应', '', '| 角色/PLC范围 | 样本/支持时长 | 速度/推力/扭矩中位数 | 适用地质原始范围 | 属性原文 |',
              '|---|---|---|---|---|']
    for unit in analysis['segment_analyses']:
        for role in ('reference', 'current'):
            p = unit[role]
            for geo in p['geology']:
                values = '/'.join(f"{p['metrics'][k]['median']:.2f}" for k in ('advance_speed','thrust','torque'))
                lines.append('| '+' | '.join(map(cell, [role+' '+p['range_display'], str(p['sample_count'])+'/'+duration(p['time_scope']['supported_duration_seconds']),
                             values, geo['source_type']+' '+geo['original_range_display'], geo['original_description']]))+' |')
        lines += ['', '**程序归纳：** '+unit['machine_observation']+'。',
                  '同一地质来源重复覆盖：'+str(unit['shared_geology_fact_ids'])+'。',
                  '工况可比性：'+unit['operating_comparability']['status']+'；'+str(unit['operating_comparability']['reasons'])+'。',
                  '范围限制：'+str(unit['limits'])+'。', '']
    d = analysis['duration_audit']
    lines += ['## 稳定样本支持时长', '', f"{d['qualified_sample_count']}条合格记录的支持时长为{d['supported_duration_seconds']:g}秒（{duration(d['supported_duration_seconds'])}）。", '',
              '| 每条记录支持秒数 | 条数 | 小计秒数 |', '|---:|---:|---:|']
    for part in d['sample_duration_histogram']:
        lines.append(f"| {part['duration_seconds']:g} | {part['count']} | {part['subtotal_seconds']:g} |")
    lines += ['', '支持时长沿用冻结预处理：当前时间戳至下一时间戳的间隔，按既有最大支持时间裁剪；跨硬缺口置零。末条无后继的记录无额外推算。选中记录的支持时长可能延伸到其下一采样点，因此它与窗口首末跨度也不同。',
              f"若误用固定20秒则为{d['unweighted_sample_count_times_20_seconds']}秒，相差{d['difference_from_count_times_20_seconds']:g}秒。", '',
              '## 历史素描日期', '', json.dumps(analysis['nearest_historical_sketch'], ensure_ascii=False, indent=2), '',
              '可用日期只表示资料当时已可使用；observation_date为空时不推断原始观测日期。', '',
              '## 解释边界', '', '本日形成运行现象与名义里程对应两层结果。PLC采样点、同一预报来源的多个子区段均不构成相互独立的地质样本。严格控制给定值未核实，单日结果不建立可重复地质影响规律。']
    (output/'独立分析结果.md').write_text('\n'.join(lines), encoding='utf-8')


def prepare(cached, output):
    output.mkdir(parents=True, exist_ok=False)
    package = json.loads((cached/'report_facts.json').read_text())
    if digest(Path(package['source']['samples'])) != package['source']['samples_sha256']:
        raise ValueError('cached_processed_samples_hash_mismatch')
    samples = pd.read_csv(package['source']['samples']); samples.time = pd.to_datetime(samples.time)
    graph = EvidenceGraph.from_snapshot(json.loads((cached/'daily_evidence_graph.json').read_text()))
    features = json.loads((cached/'daily_response_features.json').read_text())
    for name in ('report_facts.json', 'daily_evidence_graph.json', 'daily_response_features.json', 'time_admissible_catalog.json'):
        shutil.copy2(cached/name, output/name)
    write_json(output/'inputs.sha256.json', {name: digest(cached/name) for name in ('report_facts.json', 'daily_evidence_graph.json', 'daily_response_features.json')})
    pack = build_pack(package, samples, graph, features)
    analysis = pack['response_analysis']
    write_json(output/'response_analysis.json', analysis)
    write_tables(output, analysis)
    frozen_config = json.loads((ROOT/'config.json').read_text())
    check = validate_analysis(analysis, samples, graph, sample_duration_clip_seconds=frozen_config['maximum_sample_duration_seconds'])
    write_json(output/'analysis.validation.json', check)
    if not check['passed']:
        raise ValueError('independent_analysis_failed_no_model_allowed')
    source_audit = SourceAudit(); sources = {}
    for unit in analysis['segment_analyses']:
        for role in ('reference', 'current'):
            for entry in unit[role]['geology']:
                if entry['fact_id'] not in sources:
                    verified, page_text = source_audit.page(entry['source'])
                    verified['description_literal_after_whitespace'] = compact(entry['original_description']) in compact(page_text)
                    verified['source_quote_is_reconstructed_table_row_not_verbatim_paragraph'] = True
                    sources[entry['fact_id']] = {'source': entry['source'], 'check': verified,
                                                 'description': entry['original_description'], 'page_text': page_text}
    write_json(output/'original_source.validation.json', sources)
    if any(v['check']['status'] == 'FAIL' for v in sources.values()):
        raise ValueError('original_source_hash_or_pointer_failed')
    write_json(output/'evidence_pack.json', pack)
    # Reuse the accepted LLM paragraph topics, with the new analysis behind the same
    # fact IDs. No planning API call is needed for this narrowly scoped correction.
    old_plan = json.loads((cached/'content_revision/content_plan.json').read_text())
    check = plan_quality(old_plan, pack)
    origin = 'reused_previous_real_LLM_plan_same_section_topics_new_validated_analysis'
    if not check['passed']:
        write_json(output/'previous_plan.validation.json', check)
        old_plan, _ = offline_preview(pack)
        origin = 'existing_deterministic_phenomenon_planner_after_previous_plan_no_longer_compatible'
    write_json(output/'content_plan.json', old_plan)
    write_json(output/'plan.provenance.json', {'origin': origin, 'new_planning_calls': 0})
    check = plan_quality(old_plan, pack); write_json(output/'plan.validation.json', check)
    if not check['passed']:
        raise ValueError('plan_preflight_failed_no_model_allowed')
    return package, samples, graph, pack, old_plan


def audit_final(output):
    final = output/'report_final'
    draft = json.loads((final/'narrative.json').read_text())
    pack = json.loads((output/'evidence_pack.json').read_text())
    analysis = json.loads((output/'response_analysis.json').read_text())
    html_text = html.unescape((final/f"{analysis['date']}.three_chapter.html").read_text())
    with fitz.open(final/f"{analysis['date']}.three_chapter.pdf") as doc:
        pdf_text = ''.join(page.get_text() for page in doc)
    traces = []
    for chapter in draft['chapters']:
        for claim in chapter['claims']:
            traces.append({'chapter_id': chapter['chapter_id'], **claim,
                'source_records': [{'fact_id': f['fact_id'], 'source': f['source'], 'graph_support_ids': f['graph_support_ids']}
                                   for f in pack['facts'] if f['fact_id'] in claim['fact_ids']],
                'analysis_record': next((u for u in analysis['segment_analyses'] if u['analysis_unit_id'] == claim.get('analysis_unit_id')), None),
                'exact_text_present_in_HTML': claim['text'] in html_text,
                'exact_text_present_in_PDF_after_whitespace': compact(claim['text']) in compact(pdf_text)})
    write_json(final/'source_traces.json', traces)
    write_json(final/'analysis_tables.provenance.json', {
        'type': 'deterministic_verified_analysis_tables_not_LLM_output',
        'analysis_file': '../response_analysis.json', 'analysis_sha256': digest(output/'response_analysis.json'),
        'time_window_ids': [w['window_id'] for w in analysis['selected_time_windows']],
        'geology_assignments': [{'PLC_range_m': u[role]['range_m'], 'fact_id': g['fact_id'],
                                'source_range_m': g['original_range_m'], 'source': g['source']}
                               for u in analysis['segment_analyses'] for role in ('reference','current') for g in u[role]['geology']]})
    checks = {'final_narrative_equals_HTML': all(t['exact_text_present_in_HTML'] for t in traces),
              'final_narrative_equals_PDF_after_whitespace': all(t['exact_text_present_in_PDF_after_whitespace'] for t in traces),
              'PLC_source_hash_unchanged': digest(Path(pack['source']['samples'])) == pack['source']['samples_sha256'],
              'API_calls_within_two': len(json.loads((output/'model_calls.json').read_text())) <= 2}
    write_json(final/'final_product.validation.json', {'passed': all(checks.values()), 'checks': checks,
               'semantic_status': 'REVIEW_not_independent_human_confirmation'})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached-day', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--finalize', action='store_true', help='Recheck saved model text and correct only verified reversed comparison leads; no API.')
    parser.add_argument('--audit-final', action='store_true', help='Trace final narrative and deterministic tables to their evidence; no API.')
    parser.add_argument('--env-file', type=Path, default=Path.home()/'Desktop/tbm-bitemporal-twin/.env')
    args = parser.parse_args()
    if args.audit_final:
        print(json.dumps(audit_final(args.output), ensure_ascii=False))
    elif args.finalize:
        package = json.loads((args.output/'report_facts.json').read_text())
        pack = json.loads((args.output/'evidence_pack.json').read_text())
        plan = json.loads((args.output/'content_plan.json').read_text())
        original = json.loads((args.output/'report/narrative.json').read_text())
        draft, edits = correct_comparison_order(original, pack['response_analysis'])
        quality = draft_quality(draft, plan, pack)
        destination = args.output/'report_final'; destination.mkdir(exist_ok=False)
        write_json(destination/'narrative.original_model.json', original)
        write_json(destination/'narrative.json', draft)
        write_json(destination/'deterministic_corrections.json', {'new_API_calls': 0, 'edits': edits,
            'purpose': 'comparison_role_correction_only_no_new_numeric_or_geological_content',
            'unchanged_source_assignments': all(a.get('geology_assignments') == b.get('geology_assignments')
                for x,y in zip(original['chapters'],draft['chapters']) for a,b in zip(x['claims'],y['claims']))})
        write_json(destination/'quality.final.json', quality)
        if not quality['passed']:
            raise ValueError('finalization_failed_original_output_retained')
        samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), package['config'])
        samples.time = pd.to_datetime(samples.time)
        graph = EvidenceGraph.from_snapshot(json.loads((args.output/'daily_evidence_graph.json').read_text()))
        status = {'actual_llm_report': True, 'program_checks_passed': True, 'semantic_status': 'REVIEW',
                  'deterministic_comparison_lead_corrections': len(edits), 'new_API_calls': 0,
                  'tables_origin': 'validated_deterministic_analysis_not_LLM'}
        status.update(render(package, samples, graph, pack, plan, draft, destination, status))
        write_json(destination/'status.json', status)
        audit_final(args.output)
        print(json.dumps(status, ensure_ascii=False))
    elif args.generate:
        if not json.loads((args.output/'analysis.validation.json').read_text())['passed']:
            raise ValueError('analysis_must_pass_before_API')
        if (args.output/'model_calls.json').exists():
            raise ValueError('existing_call_ledger_no_automatic_replay')
        package = json.loads((args.output/'report_facts.json').read_text())
        pack = json.loads((args.output/'evidence_pack.json').read_text())
        plan = json.loads((args.output/'content_plan.json').read_text())
        if not plan_quality(plan, pack)['passed']:
            raise ValueError('invalid_plan_no_API')
        policy = json.loads((ROOT/'configs/plc_response_relations.json').read_text())
        policy['llm'].update(maximum_calls=2, transport_retries=0, maximum_local_revision_calls_per_report=1)
        session = ModelSession(args.output, policy, load_deepseek_api_key(args.env_file))
        draft, status = run_writer(pack, args.output/'report', policy, session, revise=True, supplied_plan=plan, request_builder=requests)
        if draft:
            samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), package['config'])
            samples.time = pd.to_datetime(samples.time)
            graph = EvidenceGraph.from_snapshot(json.loads((args.output/'daily_evidence_graph.json').read_text()))
            status.update(render(package, samples, graph, pack, plan, draft, args.output/'report', status))
        write_json(args.output/'status.json', status)
        print(json.dumps(status, ensure_ascii=False))
    else:
        prepare(args.cached_day, args.output)
        print(json.dumps({'analysis_passed': True, 'model_calls': 0, 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
