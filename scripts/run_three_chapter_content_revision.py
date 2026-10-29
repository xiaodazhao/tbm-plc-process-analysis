#!/usr/bin/env python3
"""Run one bounded phenomenon-centred rewrite against the same 2024-04-26 evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import json
from pathlib import Path
import re
import shutil
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tbm_process.evidence_graph.joint_reporting import ModelSession, draft_quality, run_writer
from tbm_process.evidence_graph.store import EvidenceGraph
from tbm_process.evidence_graph.three_chapter import requests
from tbm_process.final_reporting.common import load_deepseek_api_key
from tbm_process.reporting_v2.facts import digest, write_json
from tbm_process.reporting_v2.performance import calculate_metrics
from scripts.run_three_chapter_report import render


DEFAULT_BEFORE = Path('/Users/zhaoxiaoda/Desktop/TBM_新版三章日报开发验证_20261027/20261027T214820/final_validation/2024-04-26/B/final_delivery')


READER_STYLE_REPLACEMENTS = (
    ('非推进时段包含停止、调整及其他非推进状态，不能全部视为设备停机。',
     '非推进时段包括停止、调整及其他非推进状态。'),
    ('时段内含非推进记录，不表示首末之间一直连续推进。',
     '各时段均按累计有效推进时间统计，期间存在推进间隔。'),
    ('该窗口趋势不代表整个活动时段。', '该变化范围限于上述局部窗口。'),
    ('上述为描述性对应，未建立控制条件对比。',
     '上述结果反映各区段实际施工状态下的参数组合。'),
    ('当日地质资料按导向盾首名义里程对应，预测不等于实际揭露，不能据机器参数变化判定地质原因。',
     '当日地质资料按导向盾首名义里程与机器响应对应，资料性质保留为施工前预测。'),
    ('上述内容为前方预测，不是现场揭露。', '上述内容均属前方预测。'),
)


def _claims_by_chapter(narrative):
    return {int(ch['chapter_id']): [c['text'] for c in ch.get('claims', [])] for ch in narrative['chapters']}


def _same_evidence(before_pack, after_pack):
    before = {f['fact_id']: f for f in before_pack['facts']}
    after = {f['fact_id']: f for f in after_pack['facts']}
    common = sorted(set(before) & set(after))
    changed_source_evidence = []
    changed_derived_values = []
    presentation_only_changes = []

    def comparable_data(value):
        """Remove prose-only summary fields while retaining every computed value."""
        if isinstance(value, dict):
            return {
                key: comparable_data(item)
                for key, item in value.items()
                if key not in {'summary'}
            }
        if isinstance(value, list):
            return [comparable_data(item) for item in value]
        return value

    def comparable_derived_source(value):
        """Ignore only the run-local catalog copy path; preserve hashes and raw rows."""
        if isinstance(value, dict):
            return {
                key: comparable_derived_source(item)
                for key, item in value.items()
                if key != 'catalog'
            }
        if isinstance(value, list):
            return [comparable_derived_source(item) for item in value]
        return value

    for fid in common:
        old, new = before[fid], after[fid]
        is_source_fact = fid.startswith('GF:')
        protected_fields = ('canonical_text', 'data', 'nature', 'source') if is_source_fact else ('nature',)
        for field in protected_fields:
            if old.get(field) != new.get(field):
                changed_source_evidence.append({'fact_id': fid, 'field': field})
        if not is_source_fact and comparable_derived_source(old.get('source')) != comparable_derived_source(new.get('source')):
            changed_source_evidence.append({'fact_id': fid, 'field': 'source'})
        if not is_source_fact and comparable_data(old.get('data')) != comparable_data(new.get('data')):
            changed_derived_values.append({'fact_id': fid, 'field': 'data'})
        if not is_source_fact and old.get('canonical_text') != new.get('canonical_text'):
            presentation_only_changes.append({'fact_id': fid, 'field': 'canonical_text'})
        if not is_source_fact and old.get('data', {}).get('summary') != new.get('data', {}).get('summary'):
            presentation_only_changes.append({'fact_id': fid, 'field': 'data.summary'})

    removed = set(before) - set(after)
    removed_source = sorted(fid for fid in removed if fid.startswith('GF:'))
    same_samples = before_pack['source']['samples_sha256'] == after_pack['source']['samples_sha256']
    return {
        'same_processed_samples_sha256': same_samples,
        'before_processed_samples_sha256': before_pack['source']['samples_sha256'],
        'after_processed_samples_sha256': after_pack['source']['samples_sha256'],
        'common_fact_count': len(common),
        'changed_source_evidence_fields': changed_source_evidence,
        'changed_derived_numeric_or_structural_values': changed_derived_values,
        'presentation_only_changes': presentation_only_changes,
        'new_deterministic_summary_fact_ids': sorted(set(after) - set(before)),
        'removed_fact_ids': sorted(removed),
        'removed_source_fact_ids': removed_source,
        'same_underlying_evidence_confirmed': (
            same_samples
            and not changed_source_evidence
            and not changed_derived_values
            and not removed_source
        ),
        'meaning': (
            'Raw geology facts, processed PLC samples and derived numeric/structural values are protected separately. '
            'Prose-only summaries may change during the content-organisation revision.'
        ),
    }


def _write_review_materials(output, before_dir, after_dir, pack, plan, narrative, status):
    before_target = output / 'before'
    before_target.mkdir(exist_ok=True)
    for name in ('2024-04-26.three_chapter.md', '2024-04-26.three_chapter.pdf', 'narrative.json', 'content_plan.json', 'evidence_pack.json', 'quality.final.json'):
        shutil.copy2(before_dir / name, before_target / name)

    before_pack = json.loads((before_dir / 'evidence_pack.json').read_text())
    write_json(output / 'same_evidence.audit.json', _same_evidence(before_pack, pack))
    before_narrative = json.loads((before_dir / 'narrative.json').read_text())
    old, new = _claims_by_chapter(before_narrative), _claims_by_chapter(narrative)
    lines = ['# 2024-04-26 日报逐章修订对照', '',
             '本次只改变内容组织、生成提示和图表呈现；PLC、地质事实、图谱来源及里程范围未扩大。', '']
    for cid, title in enumerate(pack['titles'], 1):
        lines += [f'## {cid}. {title}', '', '### 修订前', '']
        lines += [f'- {text}' for text in old.get(cid, [])]
        lines += ['', '### 修订后', '']
        lines += [f'- {text}' for text in new.get(cid, [])]
        lines.append('')
    (output / '逐章对照差异.md').write_text('\n'.join(lines), encoding='utf-8')

    phenomena = {p['phenomenon_id']: p for p in pack['construction_phenomena']}
    paragraph_map = {p['paragraph_id']: p for ch in plan['chapters'] for p in ch['paragraphs']}
    rows = []
    for chapter in narrative['chapters']:
        for claim in chapter['claims']:
            pids = claim.get('phenomenon_ids', paragraph_map[claim['paragraph_id']].get('phenomenon_ids', []))
            rows.append({
                'chapter_id': chapter['chapter_id'], 'paragraph_id': claim['paragraph_id'],
                'phenomenon_ids': '|'.join(pids),
                'phenomenon_titles': '|'.join(phenomena[p]['title'] for p in pids),
                'text': claim['text'], 'fact_ids': '|'.join(claim['fact_ids']),
                'relation_ids': '|'.join(claim.get('relation_ids', [])),
                'reliability': '|'.join(phenomena[p]['reliability'] for p in pids),
            })
    write_json(output / '段落证据映射.json', rows)
    with (output / '段落证据映射.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)

    used = {fid for row in rows for fid in row['fact_ids'].split('|') if fid}
    decisions = {
        'entered_body_fact_ids': sorted(used),
        'retained_but_not_writer_selected': pack['writer_omissions'],
        'feature_level_decisions': [
            {'feature': '推进速度/总推力/刀盘扭矩全天中位数及P10-P90', 'decision': 'entered', 'reason': '构成全天性能与负载背景'},
            {'feature': '07:36与13:40局部多参数同向变化', 'decision': 'entered', 'reason': '来自不同主要活动组，具有已有稳定窗口与原始行证据'},
            {'feature': '三个不重叠里程分区的参数组合变化', 'decision': 'entered', 'reason': '直接服务3.1施工现象主线，避免重叠来源样本重复计数'},
            {'feature': '其余已计算局部趋势窗口', 'decision': 'not_entered', 'reason': '仅选两个不同活动组的代表性窗口，避免日报堆砌普通变化'},
            {'feature': '逐来源重复的PLC中位数', 'decision': 'support_only', 'reason': '保留追溯，但正文改用不重叠分区组合，避免按TSP/HSP逐条罗列'},
            {'feature': '刀盘转速与每转贯入量', 'decision': 'not_entered', 'reason': '转速映射仍为临时可用状态，且不是本日现象主线'},
            {'feature': '远离当日起点的历史素描及多来源等级对照', 'decision': 'not_entered', 'reason': '当日无新增素描，仅保留紧邻起点的历史背景'},
            {'feature': 'FPI/TPI/SE', 'decision': 'prohibited', 'reason': '本报告事实包未批准这些指标进入正文'},
        ],
    }
    write_json(output / '特征采用与省略说明.json', decisions)
    write_json(output / '核验与追溯摘要.json', {
        'status': status, 'quality': json.loads((after_dir / 'quality.final.json').read_text()),
        'source_trace_file': str(after_dir / 'source_traces.json'),
        'causal_constraints': pack['generation_constraints'],
        'semantic_status': 'REVIEW_no_independent_engineering_human_confirmation',
    })


def _write_source_traces(path, draft, pack):
    facts = {fact['fact_id']: fact for fact in pack['facts']}
    rows = []
    for chapter in draft.get('chapters', []):
        for claim in chapter.get('claims', []):
            rows.append({
                'text': claim['text'],
                'phenomenon_ids': claim.get('phenomenon_ids', []),
                'fact_ids': claim.get('fact_ids', []),
                'relation_ids': claim.get('relation_ids', []),
                'source_records': [
                    {
                        'fact_id': fact_id,
                        'source': facts[fact_id].get('source'),
                        'graph_support_ids': facts[fact_id].get('graph_support_ids', []),
                    }
                    for fact_id in claim.get('fact_ids', [])
                    if fact_id in facts
                ],
            })
    write_json(path, rows)


def _apply_reader_style(draft):
    """Replace repeated disclaimers with concise scope statements without changing evidence."""
    corrected = deepcopy(draft)
    edits = []
    for chapter_index, chapter in enumerate(corrected.get('chapters', [])):
        for claim_index, claim in enumerate(chapter.get('claims', [])):
            before = claim['text']
            after = before
            for old, new in READER_STYLE_REPLACEMENTS:
                after = after.replace(old, new)
            if after != before:
                claim['text'] = after
                edits.append({
                    'chapter_index': chapter_index,
                    'claim_index': claim_index,
                    'before': before,
                    'after': after,
                    'reason': 'replace_repeated_defensive_disclaimer_with_scope_statement',
                })
    return corrected, edits


def _apply_final_validation_corrections(draft):
    """Apply presentation-only corrections; factual content stays evidence-driven."""
    corrected = deepcopy(draft)
    edits = []
    pattern = re.compile(r'DyK(?P<km>\d+)\+(?P<start>\d+(?:\.\d+)?)至\+(?P<end>\d+(?:\.\d+)?)')
    for chapter_index, chapter in enumerate(corrected.get('chapters', [])):
        for claim_index, claim in enumerate(chapter.get('claims', [])):
            before = claim['text']
            after = pattern.sub(lambda match: (
                f"DyK{match.group('km')}+{match.group('start')}至"
                f"DyK{match.group('km')}+{match.group('end')}"), before)
            if after != before:
                claim['text'] = after
                edits.append({'chapter_index': chapter_index, 'claim_index': claim_index,
                              'before': before, 'after': after, 'reason': 'expand_chainage_display_only'})
    corrected, style_edits = _apply_reader_style(corrected)
    edits.extend(style_edits)
    return corrected, edits


def _restyle_existing(args, cfg, folder):
    """Create a reader-facing copy with identical evidence and no new model call."""
    source = args.restyle_from
    destination = folder / 'content_revision_reader_v2'
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination)
    pack = json.loads((source / 'evidence_pack.json').read_text())
    plan = json.loads((source / 'content_plan.json').read_text())
    original = json.loads((source / 'narrative.json').read_text())
    draft, edits = _apply_reader_style(original)
    quality = draft_quality(draft, plan, pack)
    if not quality['passed']:
        write_json(destination / 'reader_style.failure.json', {'edits': edits, 'quality': quality})
        raise ValueError('reader_style_failed_quality')
    before_claims = [claim for chapter in original['chapters'] for claim in chapter['claims']]
    after_claims = [claim for chapter in draft['chapters'] for claim in chapter['claims']]
    preservation = {
        'claim_count_unchanged': len(before_claims) == len(after_claims),
        'all_fact_relation_and_phenomenon_memberships_unchanged': all(
            before.get('fact_ids') == after.get('fact_ids')
            and before.get('relation_ids', []) == after.get('relation_ids', [])
            and before.get('phenomenon_ids', []) == after.get('phenomenon_ids', [])
            for before, after in zip(before_claims, after_claims)
        ),
        'numeric_tokens_unchanged': all(
            re.findall(r'[-+]?\d+(?:\.\d+)?', before.get('text', ''))
            == re.findall(r'[-+]?\d+(?:\.\d+)?', after.get('text', ''))
            for before, after in zip(before_claims, after_claims)
        ),
    }
    preservation['passed'] = all(preservation.values())
    if not preservation['passed']:
        raise ValueError('reader_style_failed_preservation')
    write_json(destination / 'narrative.json', draft)
    write_json(destination / 'quality.final.json', quality)
    write_json(destination / 'reader_style_revision.json', {
        'mode': 'deterministic_presentation_edit_only',
        'new_model_calls': 0,
        'edits': edits,
        'information_preservation': preservation,
        'policy': 'constraints_remain_in_program_checks; shared_scope_is_stated_once in_report_footer',
    })
    _write_source_traces(destination / 'source_traces.json', draft, pack)
    package = json.loads((folder / 'report_facts.json').read_text())
    samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), cfg['report_config'])
    samples.time = pd.to_datetime(samples.time)
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    status = {
        'status': 'real_llm_program_checked_reader_candidate',
        'actual_llm_report': True,
        'program_checks_passed': True,
        'semantic_status': 'REVIEW',
        'reader_style_revision': True,
        'new_model_calls': 0,
    }
    status.update(render(package, samples, graph, pack, plan, draft, destination, status))
    write_json(destination / 'status.json', status)
    comparison = ['# 防御性表达修订对照', '', '本次仅调整呈现语言；数值、事实、关系、段落数量和来源绑定保持不变。', '']
    for edit in edits:
        comparison.extend([f"## 第{edit['chapter_index'] + 1}章第{edit['claim_index'] + 1}段", '',
                           '**修订前**', '', edit['before'], '', '**修订后**', '', edit['after'], ''])
    (args.prepared_output / '防御性表达修订对照.md').write_text('\n'.join(comparison), encoding='utf-8')
    print(json.dumps({'destination': str(destination), 'edits': len(edits), 'new_calls': 0}, ensure_ascii=False))


def _finalize_without_model(args, cfg, folder):
    source = args.finalize_from
    destination = folder / 'content_revision_final'
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination)
    pack = json.loads((source / 'evidence_pack.json').read_text())
    plan = json.loads((source / 'content_plan.json').read_text())
    original = json.loads((source / 'narrative.json').read_text())
    draft, edits = _apply_final_validation_corrections(original)
    quality = draft_quality(draft, plan, pack)
    if not quality['passed']:
        write_json(destination / 'deterministic_finalization.failure.json', {'edits': edits, 'quality': quality})
        raise ValueError('deterministic_finalization_failed_quality')
    original_claims = [claim for chapter in original['chapters'] for claim in chapter['claims']]
    final_claims = [claim for chapter in draft['chapters'] for claim in chapter['claims']]
    preservation = {
        'passed': len(original_claims) == len(final_claims),
        'claim_count_unchanged': len(original_claims) == len(final_claims),
        'all_fact_memberships_unchanged': all(
            before.get('fact_ids') == after.get('fact_ids') and before.get('relation_ids', []) == after.get('relation_ids', [])
            for before, after in zip(original_claims, final_claims)
        ),
        'presentation_edits': edits,
        'factual_replacement_disabled': True,
        'ahead_content_source': 'AHEAD_FORECAST_SUMMARY_from_current_evidence_pack',
    }
    if not all((preservation['passed'], preservation['claim_count_unchanged'], preservation['all_fact_memberships_unchanged'])):
        raise ValueError('deterministic_finalization_failed_preservation')
    write_json(destination / 'narrative.json', draft)
    write_json(destination / 'quality.final.json', quality)
    write_json(destination / 'deterministic_finalization.json', {
        'mode': 'program_validation_correction_after_bounded_LLM_generation',
        'new_model_calls': 0,
        'edits': edits,
        'information_preservation': preservation,
        'semantic_status': 'REVIEW_no_independent_engineering_human_confirmation',
    })
    _write_source_traces(destination / 'source_traces.json', draft, pack)
    package = json.loads((folder / 'report_facts.json').read_text())
    samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), cfg['report_config'])
    samples.time = pd.to_datetime(samples.time)
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    status = {
        'status': 'real_llm_program_checked_candidate',
        'actual_llm_report': True,
        'program_checks_passed': True,
        'semantic_status': 'REVIEW',
        'revised': True,
        'deterministic_validation_correction': True,
        'new_model_calls': 0,
    }
    status.update(render(package, samples, graph, pack, plan, draft, destination, status))
    write_json(destination / 'status.json', status)
    _write_review_materials(args.prepared_output, args.before, destination, pack, plan, draft, status)
    ledger = args.prepared_output / 'model_calls.json'
    call_count = len(json.loads(ledger.read_text())) if ledger.exists() else None
    write_json(args.prepared_output / 'content_revision.status.json', {
        **status,
        'destination': str(destination),
        'total_prior_model_calls': call_count,
        'finalization_model_calls': 0,
        'input_pack_sha256': digest(folder / 'evidence_pack.B.json'),
        'before_pack_sha256': digest(args.before / 'evidence_pack.json'),
    })
    print(json.dumps({'destination': str(destination), 'status': status, 'new_calls': 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-output', type=Path, required=True)
    parser.add_argument('--before', type=Path, default=DEFAULT_BEFORE)
    parser.add_argument('--env-file', type=Path, default=Path('/Users/zhaoxiaoda/Desktop/tbm-bitemporal-twin/.env'))
    parser.add_argument('--day', default='2024-04-26')
    parser.add_argument('--retry-from', type=Path, help='Reuse a failed generated draft and spend at most one remaining local-revision call.')
    parser.add_argument('--finalize-from', type=Path, help='Apply deterministic validator corrections without any model call.')
    parser.add_argument('--restyle-from', type=Path, help='Reduce repeated defensive prose without changing facts or calling the model.')
    args = parser.parse_args()
    output, folder = args.prepared_output, args.prepared_output / args.day
    cfg = json.loads((output / 'method_config.json').read_text())
    if args.restyle_from:
        _restyle_existing(args, cfg, folder)
        return
    if args.finalize_from:
        _finalize_without_model(args, cfg, folder)
        return
    offline = json.loads((output / 'offline_summary.json').read_text())
    row = next(x for x in offline if x['date'] == args.day)
    if not (row['status'] == 'offline_complete' and row['offline_program_checks'] and row['artifact_passed']):
        raise ValueError('offline_preflight_not_passed')
    pack = json.loads((folder / 'evidence_pack.B.json').read_text())
    old_pack = json.loads((args.before / 'evidence_pack.json').read_text())
    evidence_check = _same_evidence(old_pack, pack)
    if not evidence_check['same_underlying_evidence_confirmed']:
        write_json(output / 'same_evidence.failure.json', evidence_check)
        raise ValueError('underlying_evidence_changed')
    policy = deepcopy(cfg['policy'])
    policy['llm'].update(maximum_calls=4, transport_retries=0, maximum_local_revision_calls_per_report=1)
    session = ModelSession(output, policy, load_deepseek_api_key(args.env_file))
    if not session.available:
        raise ValueError('configured_key_unavailable')
    if args.retry_from:
        ledger = json.loads((output / 'model_calls.json').read_text())
        if len(ledger) != 3:
            raise ValueError('retry_requires_exactly_three_prior_calls')
        session.records = ledger
        destination = folder / 'content_revision_retry'
        reused_plan = json.loads((args.retry_from / 'content_plan.json').read_text())
        reused_draft = json.loads((args.retry_from / 'narrative.json').read_text())
        draft, status = run_writer(pack, destination, policy, session, revise=True,
                                   reuse={'plan': reused_plan, 'draft': reused_draft, 'source': str(args.retry_from)},
                                   request_builder=requests)
    else:
        destination = folder / 'content_revision'
        draft, status = run_writer(pack, destination, policy, session, revise=True, request_builder=requests)
        if draft is None and status['status'] == 'failed_plan' and len(session.records) == 1:
            rejected = json.loads((destination / 'plan.payload.json').read_text())
            destination = folder / 'content_revision_plan_repair'
            draft, status = run_writer(pack, destination, policy, session, revise=True, rejected_plan=rejected, request_builder=requests)
    if draft is None:
        write_json(output / 'content_revision.status.json', status)
        raise ValueError('content_revision_not_generated')
    plan = json.loads((destination / 'content_plan.json').read_text())
    package = json.loads((folder / 'report_facts.json').read_text())
    samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), cfg['report_config'])
    samples.time = pd.to_datetime(samples.time)
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    status.update(render(package, samples, graph, pack, plan, draft, destination, status))
    write_json(destination / 'status.json', status)
    _write_review_materials(output, args.before, destination, pack, plan, draft, status)
    write_json(output / 'content_revision.status.json', {
        **status, 'destination': str(destination), 'calls': len(session.records),
        'input_pack_sha256': digest(folder / 'evidence_pack.B.json'),
        'before_pack_sha256': digest(args.before / 'evidence_pack.json'),
    })
    print(json.dumps({'destination': str(destination), 'status': status, 'calls': len(session.records)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
