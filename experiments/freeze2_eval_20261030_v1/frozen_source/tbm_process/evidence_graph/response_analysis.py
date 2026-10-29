"""Read-only analysis records over cached PLC features and source geology facts."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import math
import re

import pandas as pd

from tbm_process.final_reporting.common import format_chainage, parse_chainage
from tbm_process.reporting_v2.performance import clean, row_evidence
from tbm_process.reporting_v2.response_features import BASE, NAMES, rows_from_evidence, response_facts, response_samples
from .catalog import document_gate
from .literal_relations import literal_units, overlap


VERSION = 'report_response_analysis_v1_20261028'
SELECTION_POLICY = {
    'minimum_window_samples': 12,
    'maximum_examples': 3,
    'readable_joint_example_torque_cv_ceiling': .25,
    'small_speed_relative_change': .05,
    'meaning': 'report_description_selection_only_not_significance_or_safety_threshold',
    'ranking': 'coverage_of_activity_and_direction_patterns_then_chronological_first; no_percentage_ranking',
}


def range_text(bounds):
    return '至'.join(format_chainage(x) for x in bounds)


def parameter_pattern(changes):
    increasing = [NAMES[k] for k, v in changes.items() if v.get('trend') == 'increasing']
    decreasing = [NAMES[k] for k, v in changes.items() if v.get('trend') == 'decreasing']
    clauses = []
    if increasing:
        clauses.append('、'.join(increasing) + '上升')
    if decreasing:
        clauses.append('、'.join(decreasing) + '下降')
    if increasing and decreasing:
        kind = 'opposed_parameter_trends'
    elif len(increasing) + len(decreasing) >= 2:
        kind = 'co_directional_parameter_trends'
    elif increasing or decreasing:
        kind = 'single_parameter_trend'
    else:
        kind = 'no_directional_trend'
    return {'kind': kind, 'description': '，'.join(clauses) or '未达到现有升降趋势判据',
            'trend_fields': [k for k, v in changes.items() if v.get('trend') in {'increasing', 'decreasing'}]}


def time_window_analysis(features, process, samples, config):
    """Keep all computed windows, selecting examples without redefining trends."""
    rows = []
    for window in features.get('windows', {}).get('basic', []):
        block = rows_from_evidence(samples, window['source']).sort_values('time')
        count = len(block)
        quarter = max(1, count // 4)
        group_index = next((i for i, g in enumerate(process['selected'])
                            if window['platform_id'] in g['platform_ids']), None)
        group = process['selected'][group_index] if group_index is not None else None
        changes = deepcopy(window['parameter_changes'])
        checks = []
        for field, change in changes.items():
            change['unit'] = config['units'][field]
            before = float(block[field].iloc[:quarter].median()) if count else None
            after = float(block[field].iloc[-quarter:].median()) if count else None
            checks.append({'field': field, 'initial_recomputed': before, 'final_recomputed': after,
                           'passed': count > 0 and math.isclose(before, change['initial_median'], abs_tol=1e-8)
                           and math.isclose(after, change['final_median'], abs_tol=1e-8)})
        pattern = parameter_pattern(changes)
        reasons = []
        if count < SELECTION_POLICY['minimum_window_samples']:
            reasons.append('fewer_than_12_samples_for_narrative_example')
        if window['supported_duration_seconds'] < config['analysis']['minimum_duration_seconds']:
            reasons.append('insufficient_supported_duration')
        if not all(c['passed'] for c in checks) or count != window['sample_count']:
            reasons.append('cached_window_does_not_match_exact_rows')
        if not group:
            reasons.append('outside_selected_major_activity_groups_retained_in_full_audit')
        if not pattern['trend_fields']:
            reasons.append('no_existing_directional_trend_for_narrative')
        rms = block.loc[block.rpm_quality.eq('usable_provisional_mapping'), 'rpm_canonical']
        rows.append({
            'window_id': window['window_id'], 'phenomenon_window_id': 'JOINT_WINDOW:' + window['start_time'][11:19].replace(':', ''),
            'activity_group_index': group_index, 'activity_time_range': [group['start_time'], group['end_time']] if group else None,
            'platform_id': window['platform_id'], 'phase_id': window['phase_id'],
            'start_time': window['start_time'], 'end_time': window['end_time'],
            'sample_count': count, 'elapsed_span_seconds': window['elapsed_seconds'],
            'supported_duration_seconds': window['supported_duration_seconds'],
            'edge_statistic': 'median_of_first_and_last_quarter_of_same_contiguous_window_records',
            'quarter_sample_count': quarter, 'changes': changes, 'pattern': pattern,
            'torque_cv': window['torque_cv'], 'metric_statistics': window['metric_statistics'],
            'rpm_median': float(rms.median()) if len(rms) else None,
            'rpm_usable_count': len(rms), 'rpm_status': 'provisional_mapping_not_verified_control_setting',
            'source': window['source'], 'edge_recalculation': checks,
            'selected': False, 'selection_reasons': [], 'exclusion_reasons': reasons,
            'data_quality': {'all_existing_stable_advancing_quality_samples': True,
                             'exact_row_count_matches': count == window['sample_count'],
                             'quarter_statistics_verified': all(c['passed'] for c in checks),
                             'high_cv_is_variability_not_sensor_failure': True},
            'limits': ['within_window_description_not_whole_activity_trend',
                       'elapsed_span_differs_from_forward_sample_support',
                       'trend_relative_change_is_normalized_not_actual_percentage'],
        })
    eligible = [r for r in rows if not r['exclusion_reasons']]
    readable = [r for r in eligible if r['torque_cv'] is not None
                and r['torque_cv'] <= SELECTION_POLICY['readable_joint_example_torque_cv_ceiling']]
    selected = []
    # Retain the established chronological examples across different activities,
    # then admit an opposed-direction example that the previous selector missed.
    for group_index in sorted({r['activity_group_index'] for r in readable}):
        candidates = [r for r in readable if r['activity_group_index'] == group_index
                      and r['pattern']['kind'] == 'co_directional_parameter_trends']
        if candidates and len(selected) < 2:
            selected.append(min(candidates, key=lambda r: r['start_time']))
    opposed = [r for r in readable if r['pattern']['kind'] == 'opposed_parameter_trends']
    if opposed and len(selected) < SELECTION_POLICY['maximum_examples']:
        selected.append(min(opposed, key=lambda r: r['start_time']))
    if not selected:
        single = [r for r in readable if r['pattern']['kind'] == 'single_parameter_trend']
        if single:
            selected.append(min(single, key=lambda r: r['start_time']))
    for row in rows:
        if any(row is chosen for chosen in selected):
            row['selected'] = True
            row['selection_reasons'] = [row['pattern']['kind'], 'existing_minimum_support_and_exact_rows_verified',
                                        'chronological_example_with_activity_and_pattern_diversity_not_extreme_change']
        elif not row['exclusion_reasons']:
            row['exclusion_reasons'] = (['retained_high_variability_window_not_first_readable_example']
                                        if row['torque_cv'] is None or row['torque_cv'] > .25 else
                                        ['example_budget_or_repeated_activity_and_pattern_full_record_retained'])
    return rows


def geology_for_segment(graph, day, bounds, relative_resolver):
    result = []
    for node in graph.typed('GeologyFact'):
        data = node['data']
        if data.get('fact_type') != 'geological_description' or data.get('information_role') not in {'forecast', 'observed'}:
            continue
        intersection = overlap([data.get('start_m'), data.get('end_m')], bounds)
        if not intersection:
            continue
        doc = graph.nodes[data['document_id']]['data']
        gate = document_gate(doc, day)
        if gate['status'] != 'eligible' or data.get('extraction_quality', {}).get('status') != 'PASS':
            continue
        attributes = literal_units(data['original_description'])
        for values in attributes.values():
            for value in values:
                value['ranges_m'].extend([parse_chainage(a), parse_chainage(b)] for a, b in re.findall(
                    r'(DyK\d+\+\d+(?:\.\d+)?)\s*至\s*(DyK\d+\+\d+(?:\.\d+)?)', value['text']))
                value['applies_to_intersection'] = not value['ranges_m'] or any(overlap(r, intersection) for r in value['ranges_m'])
                value['local_qualifier_not_broadcast_to_whole_PLC_segment'] = bool(value['locality'])
        metadata = doc['metadata']
        result.append({'fact_id': node['id'], 'document_id': data['document_id'], 'source_type': doc['document_type'],
            'original_range_m': [data['start_m'], data['end_m']],
            'original_range_display': range_text([data['start_m'], data['end_m']]),
            'applies_to_PLC_range_m': bounds, 'intersection_range_m': intersection,
            'original_description': data['original_description'], 'literal_attributes': attributes,
            'relative_comparison': relative_resolver(graph, node['id']), 'information_role': data['information_role'],
            'available_date': metadata.get('available_date') or metadata.get('submission_date') or metadata.get('cover_date'),
            'observation_date': metadata.get('observation_date'), 'time_gate': gate, 'source': data['source'],
            'same_fact_id_reused_not_independent_observation': True})
    return sorted(result, key=lambda r: (r['source_type'], r['original_range_m'], r['fact_id']))


def segment_profile(graph, response_id, geology):
    response = graph.nodes[response_id]['data']
    features = response['features_v2']
    windows = features['windows']['basic']
    return {'response_id': response_id, 'range_m': [response['start_m'], response['end_m']],
            'range_display': range_text([response['start_m'], response['end_m']]),
            'sample_count': features['basic_count'], 'time_scope': features['time_scope'],
            'metrics': {k: features['metrics'][k] for k in (*BASE, 'rpm_canonical')},
            'platform_composition': features['platform_composition'], 'spatial_quality': features['spatial_quality'],
            'continuous_window_count': len(windows),
            'torque_cv_by_window': [{'window_id': w['window_id'], 'cv': w['torque_cv'], 'source': w['source']} for w in windows],
            'geology': geology, 'source': response['source_trace']}


def segment_analysis(graph, comparisons, day, config, relative_resolver):
    units = []
    for comparison in comparisons:
        if not comparison['usable']:
            continue
        sides = {}
        for role, rid, bounds in [('reference', comparison['reference_id'], comparison['reference_range_m']),
                                  ('current', comparison['response_id'], comparison['range_m'])]:
            sides[role] = segment_profile(graph, rid, geology_for_segment(graph, day, bounds, relative_resolver))
        changes = {k: deepcopy(v) for k, v in comparison['changes'].items() if k in BASE}
        speed = changes.get('advance_speed', {}).get('percent')
        descriptions = []
        for k, value in changes.items():
            descriptions.append(NAMES[k] + ('提高' if value['percent'] > 0 else '降低' if value['percent'] < 0 else '不变'))
        if speed is not None and abs(speed) <= 100 * SELECTION_POLICY['small_speed_relative_change']:
            descriptions[0] = '推进速度变化较小'
        description = '，'.join(descriptions)
        if changes.get('thrust', {}).get('percent', 0) * changes.get('torque', {}).get('percent', 0) < 0:
            description += '，推力与扭矩变化方向不同'
        shared = sorted({f['fact_id'] for f in sides['reference']['geology']} & {f['fact_id'] for f in sides['current']['geology']})
        width = comparison['range_m'][1] - comparison['range_m'][0]
        resolution = sides['current']['spatial_quality'].get('day_observed_minimum_positive_step_m')
        limits = list(comparison.get('limits', [])) + comparison.get('controlled_reasons', [])
        if resolution is not None and width <= resolution:
            limits.append('current_width_at_observed_chainage_step_only_short_nominal_scope_not_geological_replication')
        common_boundary = comparison['range_m'][0] if comparison['reference_range_m'][1] == comparison['range_m'][0] else None
        source_boundaries = [{'fact_id': f['fact_id'], 'source_type': f['source_type'], 'boundary_m': common_boundary}
                             for role in ('reference', 'current') for f in sides[role]['geology']
                             if common_boundary is not None and common_boundary in f['original_range_m']]
        units.append({'analysis_unit_id': 'SEGMENT_COMPARISON:' + comparison['response_id'],
                      'reference': sides['reference'], 'current': sides['current'], 'changes': changes,
                      'machine_observation': description, 'shared_geology_fact_ids': shared,
                      'forecast_boundaries_at_PLC_partition_boundary': source_boundaries,
                      'operating_comparability': {'status': comparison['controlled_status'],
                           'reasons': comparison.get('controlled_reasons', []),
                           'median_gates_are_diagnostics_only': True},
                      'supported_conclusions': [description, '同一来源原始地质区段与各PLC子区段的名义空间对应'],
                      'unsupported_explanations': ['地质导致机器变化', '预报边界是真实地质界线', '单日数据建立可重复地质影响规律'],
                      'evidence_level': 'levels_1_machine_observation_and_2_descriptive_spatial_correspondence',
                      'limits': sorted(set(limits)), 'width_m_is_not_measured_interval_advance': True})
    return units


def build_analysis(package, samples, graph, features, process, comparisons, relative_resolver):
    frame, _ = response_samples(samples, package['config'])
    qualified = frame.loc[frame.basic_response_eligible]
    dt = qualified.sample_dt_seconds.fillna(0)
    duration_audit = {
        'qualified_sample_count': len(qualified), 'supported_duration_seconds': float(dt.sum()),
        'sample_duration_histogram': [{'duration_seconds': float(v), 'count': int(n), 'subtotal_seconds': float(v*n)}
                                      for v, n in dt.value_counts().sort_index().items()],
        'duration_definition': 'sum_existing_forward_sample_dt_seconds_after_frozen_clipping_and_hard_gap_zeroing',
        'unweighted_sample_count_times_20_seconds': len(qualified)*20,
        'difference_from_count_times_20_seconds': float(dt.sum()-len(qualified)*20),
        'not_elapsed_span_or_independent_geology_sample_count': True, 'source': row_evidence(qualified),
    }
    windows = time_window_analysis(features, process, frame, package['config'])
    window_map = {w['window_id']: w for w in windows}
    fluctuation_comparisons = []
    for fact in response_facts(features, package['date'], package['config']):
        if fact['kind'] != 'adjacent_window_torque_cv_change':
            continue
        value = deepcopy(fact)
        current = window_map[fact['data']['current_window_id']]
        previous = window_map[fact['data']['previous_window_id']]
        gates = {}
        for name, threshold in [('rpm_median', 'comparison_rpm_relative_tolerance'),
                                ('thrust', 'comparison_thrust_relative_tolerance')]:
            a = previous['rpm_median'] if name == 'rpm_median' else previous['metric_statistics']['thrust']['median']
            b = current['rpm_median'] if name == 'rpm_median' else current['metric_statistics']['thrust']['median']
            ratio = abs(b/a-1) if a and b is not None else None
            gates[name] = {'relative_difference': ratio, 'tolerance': package['config']['analysis'][threshold],
                           'passed': ratio is not None and ratio <= package['config']['analysis'][threshold]}
        value['comparability'] = {'median_gates': gates, 'summary_gates_passed': all(g['passed'] for g in gates.values()),
                                  'controlled_settings_verified': False,
                                  'meaning': 'adjacent_same_platform_phase_and_rows; RPM_and_thrust_median_diagnostics_only'}
        value['selected_for_body'] = False
        value['omission_reason'] = 'full_adjacent_variability_audit_retained; limited_daily_examples_prefer_distinct_direction_patterns'
        fluctuation_comparisons.append(value)
    segments = segment_analysis(graph, comparisons, package['date'], package['config'], relative_resolver)
    sketches = []
    for doc in graph.typed('GeologyDocument'):
        d = doc['data']
        if d['document_type'] != 'SKETCH' or d['end_m'] is None or d['end_m'] > package['geology']['day_range_m'][0]:
            continue
        if document_gate(d, package['date'])['status'] == 'eligible':
            sketches.append({'document_id': doc['id'], 'range_m': [d['start_m'], d['end_m']],
                             'available_date': d['metadata'].get('available_date'),
                             'observation_date': d['metadata'].get('observation_date'),
                             'date_basis': d['date_audit'], 'source': d['source']})
    sketches.sort(key=lambda r: r['range_m'][1], reverse=True)
    return clean({'schema_version': VERSION, 'date': package['date'], 'day_range_m': package['geology']['day_range_m'],
                  'selection_policy': SELECTION_POLICY,
                  'duration_audit': duration_audit, 'time_windows': windows,
                  'selected_time_windows': sorted([r for r in windows if r['selected']], key=lambda r: r['start_time']),
                  'window_coverage': features['window_coverage']['basic'], 'segment_analyses': segments,
                  'fluctuation_comparisons': fluctuation_comparisons,
                  'nearest_historical_sketch': sketches[0] if sketches else None,
                  'source': package['source'], 'LLM_used': False,
                  'statistical_relationship_level_3': 'not_established_single_day_and_non_independent_source_scopes'})


def validate_analysis(analysis, samples, graph, *, sample_duration_clip_seconds):
    """Independently check cached arithmetic and exact source interval membership."""
    checks = []
    def add(name, passed, details=None):
        checks.append({'name': name, 'passed': bool(passed), 'details': details})
    audit = analysis['duration_audit']
    add('duration_histogram_conserves_count', sum(r['count'] for r in audit['sample_duration_histogram']) == audit['qualified_sample_count'])
    add('duration_histogram_conserves_seconds', math.isclose(sum(r['subtotal_seconds'] for r in audit['sample_duration_histogram']), audit['supported_duration_seconds']))
    exact = rows_from_evidence(samples, audit['source'])
    forward = samples.time.shift(-1).sub(samples.time).dt.total_seconds().clip(lower=0, upper=sample_duration_clip_seconds)
    forward = forward.mask(samples.hard_gap_before.shift(-1, fill_value=False).astype(bool), 0).fillna(0)
    add('sample_support_matches_frozen_timestamp_and_hard_gap_rule',
        (exact.sample_dt_seconds.fillna(0)-forward.loc[exact.index]).abs().lt(1e-8).all())
    for window in analysis['time_windows']:
        block = rows_from_evidence(samples, window['source']).sort_values('time')
        add('window_exact_rows:' + window['window_id'], len(block) == window['sample_count'])
        add('window_quarter_medians:' + window['window_id'], all(c['passed'] for c in window['edge_recalculation']))
        add('window_supported_duration:' + window['window_id'], math.isclose(float(block.sample_dt_seconds.fillna(0).sum()), window['supported_duration_seconds']))
    profiles = {}
    for unit in analysis['segment_analyses']:
        for role in ('reference', 'current'):
            profile = unit[role]
            profiles[profile['response_id']] = profile
            for entry in profile['geology']:
                bounds = overlap(profile['range_m'], entry['original_range_m'])
                add('positive_intersection:' + profile['response_id'] + ':' + entry['fact_id'], bounds == entry['intersection_range_m'] and bounds is not None)
                add('source_range_preserved:' + entry['fact_id'], entry['original_range_m'] == [graph.nodes[entry['fact_id']]['data'][k] for k in ('start_m', 'end_m')])
                add('time_admission:' + entry['fact_id'], entry['time_gate']['status'] == 'eligible')
            for metric in BASE:
                summary = profile['metrics'][metric]
                block = rows_from_evidence(samples, summary['source'])
                add('segment_metric:' + profile['response_id'] + ':' + metric,
                    len(block) == summary['count'] and math.isclose(float(block[metric].median()), summary['median'], abs_tol=1e-8))
                add('segment_source_rows_in_range:' + profile['response_id'] + ':' + metric,
                    (block.chainage.ge(profile['range_m'][0]) &
                     (block.chainage.le(profile['range_m'][1]) if profile['range_m'][1] == analysis['day_range_m'][1]
                      else block.chainage.lt(profile['range_m'][1]))).all())
        for metric, value in unit['changes'].items():
            before, after = unit['reference']['metrics'][metric]['median'], unit['current']['metrics'][metric]['median']
            add('segment_change_arithmetic:' + unit['analysis_unit_id'] + ':' + metric,
                math.isclose(value['percent'], 100*(after-before)/before, abs_tol=1e-8))
    return {'passed': all(c['passed'] for c in checks), 'checks': checks,
            'scope': 'arithmetic_exact_rows_positive_length_source_matching_and_time_admission',
            'semantic_status': 'REVIEW_not_independent_engineering_or_causal_confirmation', 'LLM_used': False}


def correct_comparison_order(draft, analysis):
    """Correct only a reversed comparison lead when all reported medians agree."""
    result, edits = deepcopy(draft), []
    units = {u['analysis_unit_id']: u for u in analysis['segment_analyses']}
    for chapter in result['chapters']:
        for claim in chapter['claims']:
            unit = units.get(claim.get('analysis_unit_id'))
            if not unit:
                continue
            reference, current = unit['reference']['range_display'], unit['current']['range_display']
            wrong = reference + '与' + current + '相比'
            text = claim['text']
            if not text.startswith(wrong):
                continue
            values = [(v['reference'], v['current'], v['unit']) for v in unit['changes'].values()]
            def displayed(value, unit_name):
                half_up = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                return f'{value:.2f}{unit_name}' in text or f'{half_up:.2f}{unit_name}' in text
            if not all(displayed(a, unit_name) and displayed(b, unit_name) for a, b, unit_name in values):
                raise ValueError('comparison_lead_reversed_but_median_roles_not_independently_supported')
            claim['text'] = current + '与' + reference + '相比' + text[len(wrong):]
            edits.append({'analysis_unit_id': unit['analysis_unit_id'], 'before': text, 'after': claim['text'],
                          'reason': 'reverse_comparison_lead_to_current_vs_reference_all_median_roles_verified',
                          'numeric_values_and_source_assignments_unchanged': True})
    return result, edits
