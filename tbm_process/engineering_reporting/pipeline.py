"""One facts/planning/writing/verification/rendering lifecycle for the entire report."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import sqlite3
import gzip

from .common import ROOT, load, save, sha


def checked_plc_geometry(database, day, advance):
    """Respect the existing PLC spatial-admission decision, never default to allowed."""
    import math
    con = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute('SELECT * FROM plc_days WHERE date=?', (day,)).fetchone()
    finally:
        con.close()
    if row is None or not row['spatial_allowed'] or row['start_m'] is None or row['end_m'] is None:
        raise ValueError('existing_PLC_spatial_admission_missing_or_blocked')
    original = dict(row)
    for field, value in [('start_m', advance['start_value_m']), ('end_m', advance['end_value_m'])]:
        if value is None or not math.isfinite(value) or not math.isclose(value, original[field], rel_tol=0., abs_tol=1e-6):
            raise ValueError('current_PLC_geometry_disagrees_with_admitted_database_range')
    return original


def prepare(day, folder, paths):
    from tbm_process.geology_store.database import GeologyStore
    from tbm_process.geology_store.time_policy import time_decision
    from tbm_process.evidence_graph.sketches import load_current_catalog
    from tbm_process.evidence_graph.catalog import build_catalog_graph
    from tbm_process.evidence_graph.analysis import add_daily_analysis
    from tbm_process.evidence_graph.literal_relations import add_literal_relations
    from tbm_process.evidence_graph.geology_response import add_source_responses
    from tbm_process.evidence_graph.validation import validate_graph
    from tbm_process.evidence_graph.response_analysis import validate_analysis
    from tbm_process.evidence_graph.three_chapter import build_pack
    from tbm_process.reporting_v2.facts import build_facts
    from tbm_process.reporting_v2.response_features import build_response_features
    from tbm_process.reporting_v2.activity_facts import build_activity_facts
    from .assembly import assemble, coverage, design_context, forecasts, reference_contexts, source_ok
    from .gas import build_gas
    from .matching import match_day
    from .references import HybridReferences, choose_references
    from .risks import build_risks, add_to_pack

    folder.mkdir(parents=True, exist_ok=False)
    config = load(ROOT/'tbm_process/reporting_v2/defaults.json')
    phase = load(ROOT/'config.json')
    policy = load(ROOT/'configs/plc_response_relations.json')
    context_config = load(paths['context_config'])
    resources = paths['resources']
    save(folder/'run_configuration.json', {'paths': paths, 'report_config': config,
        'context_config': context_config, 'frozen_phase_config_sha256': sha(ROOT/'config.json'),
        'no_PLC_algorithm_reexecution': True})
    store = GeologyStore(paths['database'])
    try:
        catalog = store.export_catalog(day)
    finally:
        store.close()
    catalog, refresh = load_current_catalog(catalog, paths['source_root']/'右线地质资料')
    cp = folder/'current_catalog.json'
    save(cp, catalog); save(folder/'current_sketch_refresh.json', refresh)
    registry = load(paths['registry'])
    package, samples = build_facts(report_day=day, project=ROOT, analysis=paths['analysis'],
        catalog_path=cp, registry_path=paths['registry'], source_root=paths['source_root'], config=config)
    original = checked_plc_geometry(paths['database'], day, package['daily_overview']['daily_advance'])
    save(folder/'PLC_spatial_admission.json', original)
    graph = build_catalog_graph(catalog, registry, paths['source_root'])
    add_daily_analysis(graph, package, samples, phase, response_policy=policy, literal_relations=True)
    add_literal_relations(graph, day)
    add_source_responses(graph, package, samples, phase)
    graph_check = validate_graph(graph, day)
    save(folder/'graph.validation.json', graph_check)
    if not graph_check['passed']:
        raise ValueError('existing_graph_validation_failed')
    daily = build_response_features(samples, config, phase, response_id='DAILY:'+day,
                                    bounds=package['geology']['day_range_m'])
    pack = build_pack(package, samples, graph, daily)
    analysis_check = validate_analysis(pack['response_analysis'], samples, graph,
        sample_duration_clip_seconds=phase['maximum_sample_duration_seconds'])
    save(folder/'analysis.validation.json', analysis_check)
    if not analysis_check['passed']:
        raise ValueError('existing_response_analysis_validation_failed')
    activities = build_activity_facts(package, pack, samples)
    save(folder/'report_facts.original.json', package)
    save(folder/'evidence_pack.original.json', pack)
    save(folder/'activity_facts.json', activities)
    save(folder/'daily_evidence_graph.json', graph.snapshot())
    source = Path(package['source']['samples'])
    if sha(source) != package['source']['samples_sha256']:
        raise ValueError('PLC_source_changed')
    copied = folder/'processed_samples.csv.gz'
    if source.suffix == '.gz':
        shutil.copy2(source, copied)
    else:
        with source.open('rb') as reader, gzip.open(copied, 'wb') as writer:
            shutil.copyfileobj(reader, writer)
    geology = {'documents': {n['id']: n['data'] for n in graph.typed('GeologyDocument')},
        'facts': [n['data'] for n in graph.typed('GeologyFact')], 'plc_days': {day: original}, 'time_decision': time_decision}
    evidence = match_day(day, geology, [], [], [])
    evidence.pop('report_adapter', None)
    if evidence['status'] != 'ok' or not evidence.get('ahead_range_m'):
        save(folder/'engineering_evidence.original.json', evidence)
        raise ValueError('geometry_or_direction_not_confirmed_no_forward_range_invented')
    ctx = design_context(evidence, load(resources/'design_intervals.json'), load(resources/'risk_records.json'))
    fs = forecasts(evidence)
    gas_file = paths['source_root']/'TBM运行数据'/('tbm_data_'+day.replace('-', '')+'.csv')
    gas = build_gas(day, gas_file, context_config, folder/'gas', paths['analysis']/'processed_samples')
    for name in ['local_reference_vectors.npz', 'embedding_metadata.json', 'reference_index_records.json']:
        shutil.copy2(resources/name, folder/name)
    retriever = HybridReferences(context_config, folder, resources)
    refs = [(context, choose_references(retriever, context, folder))
            for context in reference_contexts(evidence, ctx, fs, gas)]
    unified = assemble(day, folder, package, pack, activities, evidence, ctx, fs, gas, refs)
    risk = build_risks(day, evidence, resources)
    unified = add_to_pack(unified, risk, resources, folder)
    for name, value in [('engineering_evidence.original.json', evidence), ('design_context.json', ctx),
                        ('admissible_forecasts.json', fs), ('selected_references.json', [{'context':c, 'references':r} for c,r in refs])]:
        save(folder/name, value)
    audits = [{'source': s, 'sha_matches': source_ok(s)} for f in unified['facts'] for s in f['sources']]
    save(folder/'source_hash_audit.json', audits)
    if not all(r['sha_matches'] for r in audits):
        raise ValueError('unified_source_path_or_SHA_failure')
    save(folder/'preflight.json', {'passed': True, 'facts': len(unified['facts']),
        'PLC_and_geology_and_gas_and_risk_and_references_precede_planning': True,
        'input_sha256': sha(folder/'unified_report_input.json'), 'API_calls': 0})
    return unified


def generate(folder, key, model):
    from .model import model_stage
    from .writer import request, validate_plan, validate_text
    pack = load(folder/'unified_report_input.json')
    preflight = load(folder/'preflight.json')
    if not preflight['passed'] or sha(folder/'unified_report_input.json') != preflight['input_sha256']:
        raise ValueError('input_changed_since_preflight')
    if (folder/'model_calls.json').exists():
        raise ValueError('existing_model_run_not_overwritten_use_new_output_directory')
    ledger = []
    def call(stage, req):
        req['model'] = model
        result = model_stage(stage, req, folder, key, ledger)
        save(folder/'model_calls.json', ledger)
        if result is None:
            raise RuntimeError('model_call_failed:'+stage)
        return result
    plan = call('plan', request('plan', pack))
    plan_check = validate_plan(plan, pack)
    save(folder/'plan.initial.json', plan); save(folder/'plan.initial.validation.json', plan_check)
    if not plan_check['passed']:
        req = request('plan', pack)
        req['messages'].append({'role':'assistant', 'content':json.dumps(plan, ensure_ascii=False)})
        req['messages'].append({'role':'user', 'content':'仅修复以下提纲检查问题，仍返回完整规划JSON：'+json.dumps(plan_check['errors'], ensure_ascii=False)})
        plan = call('plan_repair', req)
        plan_check = validate_plan(plan, pack)
    save(folder/'content_plan.json', plan); save(folder/'content_plan.validation.json', plan_check)
    if not plan_check['passed']:
        raise ValueError('plan_failed_after_one_bounded_repair')
    draft = call('generation', request('write', pack, plan=plan))
    check = validate_text(draft, pack, plan)
    save(folder/'narrative.initial.json', draft); save(folder/'narrative.initial.validation.json', check)
    from .editor import request as editor_request
    edited = call('editor', editor_request(pack, draft, check['errors'], model))
    save(folder/'editor.comparison.json', {'before': draft, 'after': edited})
    draft = edited
    check = validate_text(draft, pack, plan)
    save(folder/'editor.validation.json', check)
    if not check['passed']:
        req = editor_request(pack, draft, check['errors'], model)
        req['messages'].append({'role':'assistant', 'content':json.dumps(draft, ensure_ascii=False)})
        req['messages'].append({'role':'user', 'content':'只改确有问题的段落，其余原样保留；返回完整JSON。问题：'+json.dumps(check['errors'], ensure_ascii=False)})
        revised = call('targeted_revision', req)
        save(folder/'revision.diff.json', {'before': draft, 'after': revised, 'reasons': check['errors']})
        draft = revised; check = validate_text(draft, pack, plan)
    save(folder/'final_narrative.json', draft); save(folder/'final_narrative.validation.json', check)
    if not check['passed']:
        raise ValueError('body_failed_after_one_bounded_revision')
    return draft


def run(day, folder, paths, *, dry_run=False, resume=False, env_file=None, model='deepseek-v4-flash', skip_pdf=False):
    from tbm_process.final_reporting.common import load_deepseek_api_key
    started = datetime.now(timezone.utc).isoformat()
    if resume:
        pack = load(folder/'unified_report_input.json')
        if pack['date'] != day:
            raise ValueError('resume_date_mismatch')
    else:
        pack = prepare(day, folder, paths)
    if dry_run:
        from .writer import request
        req = request('plan', pack); req['model'] = model
        save(folder/'plan.NOT_SENT.json', req)
        return {'date': day, 'status': 'offline_prepared', 'facts': len(pack['facts']), 'API_calls': 0}
    key = load_deepseek_api_key(env_file or ROOT/'.env')
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY_not_configured_in_environment_or_env_file')
    generate(folder, key, model)
    if skip_pdf:
        return {'date': day, 'status': 'narrative_checked', 'PDF_generated': False}
    from .rendering import render_report
    result = render_report(folder)
    save(folder/'run.result.json', {'started_utc': started, **result})
    return result
