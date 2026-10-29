#!/usr/bin/env python3
"""Prepare analysed days, retain historical experiments, or publish the fixed engineering standard."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tbm_process.evidence_graph.analysis import add_daily_analysis
from tbm_process.evidence_graph.catalog import build_catalog_graph
from tbm_process.evidence_graph.geology_response import add_source_responses
from tbm_process.evidence_graph.joint_reporting import ModelSession, draft_quality, plan_quality, run_writer, writer_facts
from tbm_process.evidence_graph.literal_relations import add_literal_relations
from tbm_process.evidence_graph.sketches import load_current_catalog
from tbm_process.evidence_graph.queries import daily_subgraph
from tbm_process.evidence_graph.three_chapter import TITLES, build_pack, offline_preview, requests
from tbm_process.evidence_graph.validation import validate_graph
from tbm_process.final_reporting.common import load_deepseek_api_key
from tbm_process.geology_store.database import GeologyStore
from tbm_process.reporting_v2.facts import build_facts, digest, write_json
from tbm_process.reporting_v2.rendering import make_pdf, render_html, validate_artifacts
from tbm_process.reporting_v2.response_features import build_response_features
from tbm_process.reporting_v2.three_chapter_rendering import render_three_figures


DEFAULT_OUTPUT = Path.home() / 'Desktop/TBM_新版三章日报开发验证_20261027'
DATABASE = Path('/Users/zhaoxiaoda/Desktop/TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite')


def render(package, samples, graph, pack, plan, draft, output, status):
    from tbm_process.evidence_graph.three_chapter import apply_window_choices
    if pack.get('window_selection_contract') and 'window_decisions' in plan:
        pack=apply_window_choices(pack,plan)
    figures = render_three_figures(package, samples, graph, pack, output)
    html = render_html(package, draft, {'pack': pack, 'plan': plan}, output, status, layout='three')
    pdf = output / (package['date'] + '.three_chapter.pdf')
    make_pdf(html, pdf)
    validation = validate_artifacts(package, draft, figures, pdf, chapter_titles=TITLES)
    write_json(output / 'artifact.validation.json', validation)
    return {'pdf': str(pdf), 'html': str(html), 'artifact_passed': validation['passed'], 'pages': validation['pdf_pages']}


def prepare(output, dates, database=DATABASE, analysis=None):
    analysis = Path(analysis) if analysis else ROOT / 'experiments/plc_mapping_repair_v1/replay/after'
    config = json.loads((ROOT / 'tbm_process/reporting_v2/defaults.json').read_text())
    policy = json.loads((ROOT / 'configs/plc_response_relations.json').read_text())
    policy['llm'].update(maximum_calls=6, transport_retries=0, max_tokens=7000)
    phase = json.loads((ROOT / 'config.json').read_text())
    registry_path = Path.home() / 'Desktop/TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json'
    registry = json.loads(registry_path.read_text())
    store = GeologyStore(database)
    summaries = []
    all_sources = []
    try:
        for day in dates:
            folder = output / day
            folder.mkdir(parents=True, exist_ok=False)
            try:
                catalog = store.export_catalog(day)
                sketch_root = Path.home()/'Desktop/伯舒拉岭数据/右线地质资料'
                if (sketch_root/'伯舒拉岭右线洞身素描').is_dir():
                    catalog, refresh = load_current_catalog(catalog, sketch_root)
                    write_json(folder/'current_sketch_refresh.audit.json', refresh)
                cp = folder / 'time_admissible_catalog.json'; write_json(cp, catalog)
                graph = build_catalog_graph(catalog, registry, Path.home() / 'Desktop/伯舒拉岭数据')
                package, samples = build_facts(report_day=day, project=ROOT, analysis=analysis, catalog_path=cp,
                    registry_path=registry_path, source_root=Path.home() / 'Desktop/伯舒拉岭数据', config=config)
                add_daily_analysis(graph, package, samples, phase, response_policy=policy, literal_relations=True)
                add_literal_relations(graph, day)
                source_rows = add_source_responses(graph, package, samples, phase)
                all_sources.extend(source_rows)
                validation = validate_graph(graph, day)
                write_json(folder / 'graph.validation.json', validation)
                if not validation['passed']:
                    raise ValueError('graph_validation_failed')
                daily = build_response_features(samples, config, phase, response_id='DAILY:' + day, bounds=package['geology']['day_range_m'])
                pack = build_pack(package, samples, graph, daily)
                write_json(folder / 'report_facts.json', package)
                write_json(folder / 'evidence_pack.B.json', pack)
                baseline = deepcopy(pack); baseline['program_relations'] = []
                write_json(folder / 'evidence_pack.A.json', baseline)
                assert writer_facts(pack) == writer_facts(baseline)
                write_json(folder / 'A_B_fairness.json', {'identical_writer_facts': True, 'only_explicit_relations_differ': True,
                    'shared_model_sampling_and_prompt': True, 'not_independent_quality_benchmark': True})
                write_json(folder / 'daily_evidence_graph.json', daily_subgraph(graph, day).snapshot())
                write_json(folder / 'source_specific_geology_responses.json', source_rows)
                write_json(folder / 'process_summary.json', pack['process_summary'])
                write_json(folder / 'segment_comparisons.json', pack['segment_comparisons'])
                write_json(folder / 'daily_response_features.json', daily)
                columns = ['time', 'source_file', 'source_row', 'chainage', 'machine_state', 'experimental_platform_id',
                           'experimental_phase_label', 'advance_speed', 'thrust', 'torque', 'rpm_canonical', 'pr', 'performance_eligible']
                samples[columns].to_csv(folder / 'plot_samples.csv.gz', index=False, compression='gzip')
                plan, draft = offline_preview(pack)
                write_json(folder / 'offline_plan.json', plan); write_json(folder / 'offline_narrative.NOT_LLM.json', draft)
                pq, dq = plan_quality(plan, pack), draft_quality(draft, plan, pack)
                write_json(folder / 'offline_quality.json', {'plan': pq, 'draft': dq})
                from tbm_process.evidence_graph.response_analysis import validate_analysis
                aq=validate_analysis(pack['response_analysis'],samples,graph,
                    sample_duration_clip_seconds=phase['maximum_sample_duration_seconds'])
                write_json(folder/'analysis.validation.json',aq)
                for group, p in [('A', baseline), ('B', pack)]:
                    _, request, _ = requests(p, policy)
                    write_json(folder / f'{group}.plan.NOT_SENT.json', request)
                preview = folder / 'offline'; preview.mkdir()
                products = render(package, samples, graph, pack, plan, draft, preview, {'actual_llm_report': False})
                summaries.append({'date': day, 'status': 'offline_complete', 'platform_count': package['platform_count'],
                    'source_scopes': len(source_rows), 'usable_source_scopes': sum(x['description']['usable'] for x in source_rows),
                    'facts': len(pack['facts']), 'relations': len(pack['program_relations']),
                    'coverage_ratio': package['daily_overview']['coverage_ratio'],
                    'offline_program_checks': validation['passed'] and aq['passed'] and pq['passed'],
                    'offline_draft_checks':dq['passed'],
                    'preflight_scope':'source_graph_calculated_analysis_and_plan; deterministic_preview_is_not_the_LLM_body',**products})
            except Exception as exc:
                write_json(folder / 'preparation.failure.json', {'error_type': type(exc).__name__, 'reason': str(exc), 'no_API_called': True})
                summaries.append({'date': day, 'status': 'offline_preparation_failed', 'error': str(exc)})
            write_json(output / 'offline_summary.json', summaries)
            print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
    finally:
        store.close()
    write_json(output / 'method_config.json', {'policy': policy, 'report_config': config, 'database': str(database), 'dates': dates,
        'main_case': '2024-04-26', 'model_budget': 6, 'same_source_scopes_not_independent': True})
    # Feasibility only: one row per source/interval/day, not one independent sample per PLC point.
    import pandas as pd
    rows = []
    for r in all_sources:
        d = r['description']; m = d['metrics']
        rows.append({'date': r['date'], 'document_id': r['document_id'], 'geology_fact_id': r['geology_fact_id'],
            'source_type': r['document_type'], 'range_m': r['range_m'], 'usable': d['usable'],
            'lithology': [x['text'] for x in d['literal_attributes']['lithology']],
            'integrity': [x['text'] for x in d['literal_attributes']['integrity']], 'sample_count': d['sample_count'],
            **{k + '_median': m[k]['median'] for k in ('advance_speed', 'thrust', 'torque')},
            **d['stability'], 'not_independent_when_overlapping': True})
    write_json(output / 'multisegment_feasibility_rows.json', rows)
    pd.DataFrame(rows).to_csv(output / 'multisegment_feasibility_rows.csv', index=False, encoding='utf-8-sig')
    return summaries


def llm(output, day, env_file):
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.reporting_v2.performance import calculate_metrics
    import pandas as pd
    cfg = json.loads((output / 'method_config.json').read_text()); policy = cfg['policy']
    if policy['llm']['maximum_calls'] > 6 or policy['llm']['transport_retries'] != 0:
        raise ValueError('six_call_consent_boundary')
    offline = json.loads((output / 'offline_summary.json').read_text())
    if not next((x for x in offline if x['date'] == day and x['status'] == 'offline_complete' and x['artifact_passed'] and x['offline_program_checks']), None):
        raise ValueError('offline_preflight_not_passed')
    key = load_deepseek_api_key(env_file)
    if not key:
        write_json(output / 'LLM_NOT_RUN.json', {'reason': 'configured_key_unavailable', 'actual_calls': 0}); return
    ledger = output / 'model_calls.json'
    if ledger.exists():
        raise ValueError('existing_call_ledger_no_automatic_second_run')
    folder = output / day
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    package = json.loads((folder / 'report_facts.json').read_text())
    samples = pd.read_csv(package['source']['samples']); samples.time = pd.to_datetime(samples.time)
    samples, _ = calculate_metrics(samples, cfg['report_config'])
    session = ModelSession(output, policy, key)
    freeze = {'dates': [day], 'mode': 'small_sample_functional_A_B_not_statistical_efficacy', 'group_order': ['B', 'A'],
              'model': policy['llm']['model'], 'temperature': .1, 'maximum_calls': 6,
              'source_code_sha256': {str(p.relative_to(ROOT)): digest(p) for p in [ROOT / 'tbm_process/evidence_graph/three_chapter.py',
                 ROOT / 'tbm_process/evidence_graph/joint_reporting.py', ROOT / 'tbm_process/evidence_graph/literal_relations.py']},
              'input_sha256': {g: digest(folder / f'evidence_pack.{g}.json') for g in ['A', 'B']}}
    write_json(output / 'experiment_freeze.before_calls.json', freeze)
    results = []
    for group in ('B', 'A'):
        pack = json.loads((folder / f'evidence_pack.{group}.json').read_text())
        dest = folder / group; dest.mkdir(exist_ok=False)
        # Reserve at least two calls for A; no quality-driven unbounded retries.
        budget = deepcopy(policy)
        budget['llm']['maximum_calls'] = 4 if group == 'B' else 6
        budget['llm']['maximum_local_revision_calls_per_report'] = 1
        original = session.policy; session.policy = budget
        draft, status = run_writer(pack, dest, budget, session, revise=True, request_builder=requests)
        session.policy = original
        if draft:
            plan = json.loads((dest / 'content_plan.json').read_text())
            status.update(render(package, samples, graph, pack, plan, draft, dest, status))
            status['planned_relation_ids'] = sorted({i for c in plan['chapters'] for p in c['paragraphs'] for i in p.get('relation_ids', [])})
            status['expressed_relation_ids'] = sorted({i for c in draft['chapters'] for claim in c['claims'] for i in claim.get('relation_ids', [])})
        results.append({'date': day, 'group': group, **status})
        write_json(output / 'llm_AB_results.json', results)
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


def repair_plans(output, day, env_file):
    """One feedback repair per failed pilot plan; exactly four remaining calls at most."""
    import pandas as pd
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.reporting_v2.performance import calculate_metrics
    cfg = json.loads((output / 'method_config.json').read_text())
    ledger = json.loads((output / 'model_calls.json').read_text())
    if len(ledger) != 2 or any(r['stage'] != 'plan' for r in ledger):
        raise ValueError('requires_exactly_two_initial_plan_calls_no_other_retries')
    folder = output / day
    freeze = json.loads((output / 'experiment_freeze.before_calls.json').read_text())
    for group in ('A', 'B'):
        if digest(folder / f'evidence_pack.{group}.json') != freeze['input_sha256'][group]:
            raise ValueError('facts_changed_since_original_calls')
        if json.loads((folder / group / 'status.json').read_text())['status'] != 'failed_plan':
            raise ValueError('not_a_failed_plan')
    policy = deepcopy(cfg['policy'])
    policy['llm'].update(maximum_calls=6, transport_retries=0)
    session = ModelSession(output, policy, load_deepseek_api_key(env_file))
    session.records = ledger
    write_json(output / 'interface_recovery_protocol.json', {
        'initial_calls': 2, 'maximum_total_calls': 6, 'unchanged_input_hashes': True,
        'allowed_changes': ['chapter_id_string_to_integer_only'],
        'plan_feedback_repairs_per_group': 1, 'generation_calls_per_group': 1,
        'local_revision_calls': 0, 'not_a_clean_no_retry_efficacy_experiment': True,
        'writer_sha256_after_interface_fix': digest(ROOT / 'tbm_process/evidence_graph/joint_reporting.py')})
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    package = json.loads((folder / 'report_facts.json').read_text())
    samples = pd.read_csv(package['source']['samples']); samples.time = pd.to_datetime(samples.time)
    samples, _ = calculate_metrics(samples, cfg['report_config'])
    results = []
    for group in ('B', 'A'):
        pack = json.loads((folder / f'evidence_pack.{group}.json').read_text())
        rejected = json.loads((folder / group / 'plan.payload.json').read_text())
        dest = folder / group / 'recovery'; dest.mkdir(exist_ok=False)
        draft, status = run_writer(pack, dest, policy, session, revise=False, rejected_plan=rejected, request_builder=requests)
        if draft:
            plan = json.loads((dest / 'content_plan.json').read_text())
            status.update(render(package, samples, graph, pack, plan, draft, dest, status))
            status['planned_relation_ids'] = sorted({i for c in plan['chapters'] for p in c['paragraphs'] for i in p.get('relation_ids', [])})
            status['expressed_relation_ids'] = sorted({i for c in draft['chapters'] for claim in c['claims'] for i in claim.get('relation_ids', [])})
        results.append({'date': day, 'group': group, **status})
        write_json(output / 'llm_AB_recovery_results.json', results)
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


def generate_compiled_plans(output, day, env_file):
    import pandas as pd
    from tbm_process.evidence_graph.three_chapter import compile_plan_evidence
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.reporting_v2.performance import calculate_metrics
    cfg = json.loads((output / 'method_config.json').read_text())
    ledger = json.loads((output / 'model_calls.json').read_text())
    if len(ledger) != 4 or any(r['stage'] not in ('plan', 'plan_repair') for r in ledger):
        raise ValueError('requires_exactly_four_recorded_planning_attempts')
    folder = output / day
    policy = deepcopy(cfg['policy']); policy['llm'].update(maximum_calls=6, transport_retries=0)
    session = ModelSession(output, policy, load_deepseek_api_key(env_file)); session.records = ledger
    package = json.loads((folder / 'report_facts.json').read_text())
    samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), cfg['report_config'])
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    results = []
    write_json(output / 'evidence_closure_protocol.before_generation.json', {
        'prior_calls': len(ledger), 'remaining_calls': 2, 'maximum_total_calls': 6,
        'same_compilation_procedure_for_A_and_B': True, 'no_new_topic_selection': True,
        'unexplained_omissions_remain_semantic_REVIEW': True,
        'not_a_preregistered_frozen_efficacy_experiment': True,
        'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in [
            ROOT / 'tbm_process/evidence_graph/three_chapter.py',
            ROOT / 'tbm_process/evidence_graph/joint_reporting.py',
            ROOT / 'tbm_process/reporting_v2/three_chapter_rendering.py']}})
    for group in ('B', 'A'):
        pack = json.loads((folder / f'evidence_pack.{group}.json').read_text())
        frozen = json.loads((output / 'experiment_freeze.before_calls.json').read_text())
        if digest(folder / f'evidence_pack.{group}.json') != frozen['input_sha256'][group]:
            raise ValueError('changed_facts_not_allowed')
        plan = json.loads((folder / group / 'recovery/plan_repair.payload.json').read_text())
        compiled, audit = compile_plan_evidence(plan, pack)
        dest = folder / group / 'compiled_generation'; dest.mkdir(exist_ok=False)
        write_json(dest / 'plan.compilation.audit.json', audit)
        write_json(dest / 'original_model_plan.json', plan)
        write_json(dest / 'program_compiled_plan.json', compiled)
        draft, status = run_writer(pack, dest, policy, session, revise=False, supplied_plan=compiled, request_builder=requests)
        status['plan_origin'] = 'LLM_topics_plus_explicit_program_evidence_closure_semantic_REVIEW'
        if draft:
            status.update(render(package, samples, graph, pack, compiled, draft, dest, status))
            status['planned_relation_ids'] = sorted({i for c in compiled['chapters'] for p in c['paragraphs'] for i in p.get('relation_ids', [])})
            status['expressed_relation_ids'] = sorted({i for c in draft['chapters'] for claim in c['claims'] for i in claim.get('relation_ids', [])})
        results.append({'date': day, 'group': group, **status})
        write_json(output / 'llm_AB_compiled_results.json', results)
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


def revise_existing(output, day, env_file, *, second_attempt=False):
    """User-authorized extra budget: preserve original A/B and revise located claims once each."""
    import pandas as pd
    from tbm_process.evidence_graph.three_chapter import clarify_pack_for_quality
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.reporting_v2.performance import calculate_metrics
    cfg = json.loads((output / 'method_config.json').read_text())
    ledger = json.loads((output / 'model_calls.json').read_text())
    if len(ledger) != (8 if second_attempt else 6):
        raise ValueError('requires_exact_recorded_budget_boundary')
    folder = output / day
    policy = deepcopy(cfg['policy'])
    policy['llm'].update(maximum_calls=10, transport_retries=0, maximum_local_revision_calls_per_report=1)
    session = ModelSession(output, policy, load_deepseek_api_key(env_file)); session.records = ledger
    write_json(output / ('second_local_revision.protocol.json' if second_attempt else 'additional_budget.authorized.json'), {
        'authorization': 'User: 不一定只要6次，有问题你继续做', 'previous_cap': 6, 'new_total_cap': 10,
        'automatic_transport_retries': 0, 'targeted_revision_per_group': 2 if second_attempt else 1,
        'original_experiment_preserved': True, 'post_review_development_not_fair_frozen_AB_score': True,
        'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in [ROOT / 'tbm_process/evidence_graph/three_chapter.py', ROOT / 'tbm_process/evidence_graph/joint_reporting.py']}})
    package = json.loads((folder / 'report_facts.json').read_text())
    samples, _ = calculate_metrics(pd.read_csv(package['source']['samples']), cfg['report_config'])
    graph = EvidenceGraph.from_snapshot(json.loads((folder / 'daily_evidence_graph.json').read_text()))
    results = []
    for group in ('B', 'A'):
        original_pack = json.loads((folder / f'evidence_pack.{group}.json').read_text())
        pack = clarify_pack_for_quality(original_pack)
        previous = folder / group / 'compiled_generation'
        plan = json.loads((previous / 'content_plan.json').read_text())
        draft = json.loads((previous / 'narrative.json').read_text())
        dest = folder / group / ('reviewed_revision2' if second_attempt else 'reviewed_revision'); dest.mkdir(exist_ok=False)
        if second_attempt:
            write_json(dest / 'previous_rejected_patch.json', json.loads((folder / group / 'reviewed_revision/revision.log.json').read_text()))
        write_json(dest / 'original_pack.sha256.json', {'path': str(folder / f'evidence_pack.{group}.json'), 'sha256': digest(folder / f'evidence_pack.{group}.json'),
            'clarification': 'existing Python display rounding and explicit 20m bound difference; no original observation changed'})
        draft, status = run_writer(pack, dest, policy, session, revise=True,
            reuse={'plan': plan, 'draft': draft, 'source': str(previous)}, request_builder=requests)
        if draft:
            status.update(render(package, samples, graph, pack, plan, draft, dest, status))
            status['planned_relation_ids'] = sorted({i for c in plan['chapters'] for p in c['paragraphs'] for i in p.get('relation_ids', [])})
            status['expressed_relation_ids'] = sorted({i for c in draft['chapters'] for claim in c['claims'] for i in claim.get('relation_ids', [])})
        results.append({'date': day, 'group': group, **status})
        write_json(output / ('post_review_revision2_results.json' if second_attempt else 'post_review_revision_results.json'), results)
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['prepare', 'llm', 'repair-plans', 'generate-compiled-plans', 'revise-existing', 'retry-revision', 'publish'])
    p.add_argument('--output', type=Path)
    p.add_argument('--dates', nargs='+', default=['2024-04-26', '2023-11-18', '2024-05-23'])
    p.add_argument('--database', type=Path, default=DATABASE)
    p.add_argument('--analysis-dir', type=Path)
    p.add_argument('--env-file', type=Path, default=Path('/Users/zhaoxiaoda/Desktop/tbm-bitemporal-twin/.env'))
    p.add_argument('--bundle',type=Path,help='publish: one completed day bundle, not a parent containing several dates')
    p.add_argument('--facts',type=Path,help='publish: optional explicit report_facts.json path')
    p.add_argument('--approved',type=Path,help='publish: optional approved narrative JSON')
    p.add_argument('--offline',action='store_true',help='publish: build facts and request without reading credentials or calling an API')
    args = p.parse_args()
    if args.stage == 'publish':
        if not args.bundle or not args.output:p.error('publish requires --bundle and a new --output directory')
        from tbm_process.reporting_v2.publication import publish
        result=publish(args.bundle,args.output,facts_path=args.facts,approved_path=args.approved,env_file=args.env_file,offline=args.offline)
        print(json.dumps(result,ensure_ascii=False))
        if result['status'].startswith('failed'):raise SystemExit(1)
        return
    if args.output is None:
        if args.stage != 'prepare':
            p.error('llm requires --output pointing to an existing prepared run')
        args.output = DEFAULT_OUTPUT / datetime.now().strftime('%Y%m%dT%H%M%S')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage == 'prepare':
        prepare(args.output, args.dates, args.database, args.analysis_dir)
    elif args.stage == 'llm':
        llm(args.output, args.dates[0], args.env_file)
    elif args.stage == 'repair-plans':
        repair_plans(args.output, args.dates[0], args.env_file)
    elif args.stage == 'generate-compiled-plans':
        generate_compiled_plans(args.output, args.dates[0], args.env_file)
    elif args.stage == 'revise-existing':
        revise_existing(args.output, args.dates[0], args.env_file)
    else:
        revise_existing(args.output, args.dates[0], args.env_file, second_attempt=True)


if __name__ == '__main__':
    main()
