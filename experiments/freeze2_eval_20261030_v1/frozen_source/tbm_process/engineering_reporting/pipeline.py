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
    if evidence['status'] != 'ok':
        save(folder/'engineering_evidence.original.json', evidence)
        raise ValueError('geometry_or_direction_not_confirmed_no_forward_range_invented')
    if evidence.get('ahead_range_m') is None:
        evidence['degraded_mode']={'status':'partial_report_possible','reason':'direction_not_confirmed',
            'ahead_window_invented':False,'prior_spatial_admission_preserved':True}
        save(folder/'input_limitations.json',evidence['degraded_mode'])
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
    from .front_analysis import enrich_front
    unified = enrich_front(unified, package, activities, pack, samples, gas,
                           paths['source_root'], paths['analysis'], folder)
    from .organization import enrich_organization
    unified = enrich_organization(unified, package, samples, folder)
    from .plc_day_audit import enrich as enrich_plc_day
    unified = enrich_plc_day(unified, package, samples, folder)
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


def generate(folder, key, model, *, section_ids=None, baseline_folder=None, seed_plan=None, seed_draft=None):
    from copy import deepcopy
    from .model import model_stage
    from .writer import request, validate_plan, validate_text, retire_unused_optional_front_references, qualify_overview_forecast_reference
    from .delivery_status import rule_plan, structure_errors, decide
    full_pack = load(folder/'unified_report_input.json')
    pack = full_pack
    preflight = load(folder/'preflight.json')
    if not preflight['passed'] or sha(folder/'unified_report_input.json') != preflight['input_sha256']:
        raise ValueError('input_changed_since_preflight')
    if (folder/'model_calls.json').exists():
        raise ValueError('existing_model_run_not_overwritten_use_new_output_directory')
    if section_ids is not None:
        from copy import deepcopy
        pack = deepcopy(full_pack)
        pack['facts'] = [f for f in pack['facts'] if f['section'] in section_ids]
        pack['sections'] = {s:t for s,t in pack['sections'].items() if s in section_ids}
        pack['generation_scope'] = list(section_ids)
        if baseline_folder is None:
            raise ValueError('partial_revision_requires_original_checked_report')
    ledger, warnings = [], []
    def call(stage, req, *, optional=False):
        req['model'] = model
        result = model_stage(stage, req, folder, key, ledger)
        save(folder/'model_calls.json', ledger)
        if result is None:
            if not optional:
                raise RuntimeError('model_call_failed:'+stage)
            warnings.append({'stage': stage, 'reason': 'model_call_failed', 'fallback': 'retain_available_evidence_and_text'})
        return result
    plan = seed_plan if seed_plan is not None else call('plan', request('plan', pack), optional=True)
    plan_check = validate_plan(plan, pack, section_ids=section_ids)
    save(folder/'plan.initial.json', plan); save(folder/'plan.initial.validation.json', plan_check)
    if not plan_check['passed']:
        req = request('plan', pack)
        req['messages'].append({'role':'assistant', 'content':json.dumps(plan, ensure_ascii=False)})
        req['messages'].append({'role':'user', 'content':'仅修复以下提纲检查问题，仍返回完整规划JSON：'+json.dumps(plan_check['errors'], ensure_ascii=False)})
        plan = call('plan_repair', req, optional=True)
        plan_check = validate_plan(plan, pack, section_ids=section_ids)
    save(folder/'content_plan.json', plan); save(folder/'content_plan.validation.json', plan_check)
    if not plan_check['passed']:
        save(folder/'plan.failed.json', {'plan': plan, 'validation': plan_check})
        plan = rule_plan(pack, section_ids)
        plan_check = validate_plan(plan, pack, section_ids=section_ids)
        save(folder/'plan.fallback.json', {'method': 'deterministic_all_same_section_facts',
             'LLM_plan_succeeded': False, 'no_new_fact_IDs': True, 'validation': plan_check})
        warnings.append({'stage': 'plan', 'reason': 'LLM_plan_not_accepted', 'fallback': 'deterministic_same_fact_pool_plan'})
        save(folder/'content_plan.json', plan); save(folder/'content_plan.validation.json', plan_check)
        if not plan_check['passed']:
            raise ValueError('deterministic_plan_failed_input_requires_review')
    draft = seed_draft if seed_draft is not None else call('generation', request('write', pack, plan=plan))
    check = validate_text(draft, pack, plan, section_ids=section_ids)
    save(folder/'narrative.initial.json', draft); save(folder/'narrative.initial.validation.json', check)
    checked_backup = (deepcopy(draft), deepcopy(plan)) if check['passed'] else None
    from .editor import request as editor_request
    edited = call('editor', editor_request(pack, draft, check['errors'], model), optional=True)
    save(folder/'editor.comparison.json', {'before': draft, 'after': edited})
    if edited is not None:
        edited_check = validate_text(edited, pack, plan, section_ids=section_ids)
        save(folder/'editor.validation.json', edited_check)
        import re
        category = lambda e: re.sub(r'^\d(?:\.\d)?:\d+:', '', e).split(':')[0]
        new_issue_types = {category(e) for e in edited_check['errors']} - {category(e) for e in check['errors']}
        if check['passed'] and not edited_check['passed']:
            warnings.append({'stage': 'editor', 'reason': 'edited_text_failed_checks',
                             'fallback': 'one_local_patch_else_initial_checked_draft', 'errors': edited_check['errors']})
        if structure_errors(edited):
            warnings.append({'stage': 'editor', 'reason': 'malformed_edited_text', 'fallback': 'retain_initial_draft'})
        elif not check['passed'] and new_issue_types:
            warnings.append({'stage': 'editor', 'reason': 'new_known_issue_types_in_edit',
                             'new_issue_types': sorted(new_issue_types), 'fallback': 'patch_original_draft_not_regressed_edit'})
        else:
            draft = edited
    if 'front_revision' in pack and not structure_errors(draft):
        # Qualifiers and optional-reference reconciliation must not erase the last available draft.
        from copy import deepcopy
        candidate_draft, candidate_plan = deepcopy(draft), deepcopy(plan)
        try:
            changes = qualify_overview_forecast_reference(candidate_draft, full_pack)
            retired = retire_unused_optional_front_references(candidate_plan, candidate_draft, pack)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            warnings.append({'stage': 'source_qualification', 'reason': type(exc).__name__,
                             'fallback': 'retain_unmodified_text_for_checks'})
        else:
            draft, plan = candidate_draft, candidate_plan
            save(folder/'source_comparator_qualification.json', changes)
            save(folder/'final_plan_reconciliation.json', retired)
    check = validate_text(draft, pack, plan, section_ids=section_ids)
    if not check['passed']:
        from .local_revision import request as patch_request, apply as apply_patch
        try:
            req = patch_request(pack, draft, check['errors'], model)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            save(folder/'revision.unresolved.json', {'status': 'REVIEW_REQUIRED',
                'reason': 'not_safely_localizable', 'error_class': type(exc).__name__, 'issues': check['errors']})
        else:
            payload = call('targeted_revision', req, optional=True)
            if payload is not None:
                try:
                    revised, preservation = apply_patch(draft, payload, check['errors'])
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    save(folder/'revision.rejected.json', {'reason': 'unsafe_or_invalid_patch',
                        'error_class': type(exc).__name__, 'payload': payload, 'original_text_retained': True})
                else:
                    save(folder/'revision.diff.json', {'before': draft, 'after': revised, 'reasons': check['errors'],
                         'non_target_preservation': preservation, 'patch_payload': payload})
                    # Never replace a checked draft by a failed patch; original failed drafts remain reviewable.
                    draft = revised
                    check = validate_text(draft, pack, plan, section_ids=section_ids)
    if not check['passed'] and checked_backup is not None:
        save(folder/'editor_and_revision.not_accepted.json', {'draft': draft, 'validation': check})
        draft, plan = checked_backup
        check = validate_text(draft, pack, plan, section_ids=section_ids)
        warnings.append({'stage': 'final_selection', 'reason': 'edited_or_revised_text_failed_checks',
                         'fallback': 'initial_checked_draft_restored_without_text_changes'})
    if section_ids is not None:
        from copy import deepcopy
        old_plan = load(baseline_folder/'content_plan.json')
        old_draft = load(baseline_folder/'final_narrative.json')
        save(folder/'front_plan.json',plan);save(folder/'front_narrative.json',draft)
        save(folder/'front_narrative.validation.json',check)
        plan['sections'] += deepcopy([s for s in old_plan['sections'] if s['section_id'] not in section_ids])
        plan['omissions'] += [r for r in old_plan.get('omissions',[]) if
                              next(f for f in full_pack['facts'] if f['fact_id']==r['fact_id'])['section'] not in section_ids]
        draft['sections'] += deepcopy([s for s in old_draft['sections'] if s['section_id'] not in section_ids])
        untouched = [s for s in old_draft['sections'] if s['section_id'] not in section_ids]
        if [s for s in draft['sections'] if s['section_id'] not in section_ids] != untouched:
            raise ValueError('out_of_scope_chapter_changed')
        save(folder/'unchanged_chapters.validation.json',{'passed':True,
             'baseline_narrative_sha256':sha(baseline_folder/'final_narrative.json'),
             'unchanged_sections':[s['section_id'] for s in untouched]})
        save(folder/'content_plan.json',plan)
        save(folder/'content_plan.validation.json',validate_plan(plan,full_pack))
        full_check = validate_text(draft,full_pack,plan)
        full_check['passed'] = full_check['passed'] and check['passed']
        check = full_check
    else:
        save(folder/'content_plan.json',plan)
        save(folder/'content_plan.validation.json',validate_plan(plan,pack))
    save(folder/'final_narrative.json', draft); save(folder/'final_narrative.validation.json', check)
    decide(folder, check, warnings)
    return draft


def _run(day, folder, paths, *, dry_run=False, resume=False, env_file=None, model='deepseek-v4-flash', skip_pdf=False):
    from tbm_process.final_reporting.common import load_deepseek_api_key
    started = datetime.now(timezone.utc).isoformat()
    if resume:
        pack = load(folder/'unified_report_input.json')
        if pack['date'] != day:
            raise ValueError('resume_date_mismatch')
        from .assembly import source_ok
        sources = {(s['path'], s['sha256']): s for f in pack['facts'] for s in f['sources']}
        source_check = [{'source': s, 'sha_matches': source_ok(s)} for s in sources.values()]
        save(folder/'resume.source_hash_audit.json', source_check)
        if not all(r['sha_matches'] for r in source_check):
            raise ValueError('unified_source_path_or_SHA_failure')
        if sha(folder/'processed_samples.csv.gz') != sha(Path(pack['source']['samples'])):
            raise ValueError('PLC_source_changed')
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
    decision = load(folder/'delivery_decision.json')
    if skip_pdf:
        return {'date': day, **decision, 'PDF_generated': False}
    from .delivery_status import review_view, failed
    draft = load(folder/'final_narrative.json')
    view = review_view(draft)
    if view is None:
        return failed(folder, 'SYSTEM_FAILURE', 'generation', 'no_renderable_model_text',
                      date=day, raw_response_preserved=True)
    if decision['status'] == 'REVIEW_REQUIRED':
        save(folder/'narrative.review_view.json', view)
    from .rendering import render_report
    result = render_report(folder)
    technical = result.get('technical_passed', False)
    summary = {'started_utc': started, **result, **decision,
               'PDF_generated': True, 'execution_completed': technical,
               'automated_eligible_report': technical and decision['status'] == 'AUTO_CHECKED',
               'limited_report': technical and decision['status'] == 'LIMITED_INPUT',
               'review_draft': technical and decision['status'] == 'REVIEW_REQUIRED'}
    if not technical:
        summary.update(status='SYSTEM_FAILURE', label='PDF成品检查未通过；未作为合格报告交付',
                       formal_release_blocked=True, reason='artifact_integrity_failed')
    save(folder/'run.result.json', summary)
    return summary


def run(day, folder, paths, **kwargs):
    """Persist per-day terminal outcomes; a failed day must not abort a batch."""
    from .delivery_status import failed
    try:
        return _run(day, folder, paths, **kwargs)
    except Exception as exc:
        # Preserve old runs and stage records. Never persist arbitrary service exception text.
        if isinstance(exc, FileExistsError) or (folder/'run.result.json').exists():
            raise
        stage = 'generation' if (folder/'preflight.json').exists() else 'input'
        input_codes = {'existing_PLC_spatial_admission_missing_or_blocked',
            'current_PLC_geometry_disagrees_with_admitted_database_range',
            'existing_graph_validation_failed', 'existing_response_analysis_validation_failed',
            'PLC_source_changed', 'geometry_or_direction_not_confirmed_no_forward_range_invented',
            'unified_source_path_or_SHA_failure', 'input_changed_since_preflight',
            'deterministic_plan_failed_input_requires_review'}
        blocked = isinstance(exc, FileNotFoundError) or isinstance(exc, ValueError) and str(exc) in input_codes
        status = 'INPUT_BLOCKED' if blocked else 'SYSTEM_FAILURE'
        safe = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return failed(folder, status, stage, safe, date=day, error_class=type(exc).__name__,
                      raw_stage_records_preserved=True)
