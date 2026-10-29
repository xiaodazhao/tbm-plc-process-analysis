"""Each geological source keeps its own scope; summaries do not establish causality."""
from __future__ import annotations

from tbm_process.reporting_v2.response_features import build_response_features
from .catalog import document_gate
from .literal_relations import literal_units, overlap
from .store import identity


def add_source_responses(graph, package, samples, phase_config):
    day, day_range = package['date'], package['geology']['day_range_m']
    records = []
    for node in list(graph.typed('GeologyFact')):
        d = node['data']
        if d['fact_type'] != 'geological_description' or d['information_role'] not in ('forecast', 'observed'):
            continue
        doc = graph.nodes[d['document_id']]['data']
        if document_gate(doc, day)['status'] != 'eligible':
            continue
        bounds = overlap([d['start_m'], d['end_m']], day_range)
        if not bounds:
            continue
        lo, hi = bounds
        existing = next((n for n in graph.typed('PLCResponse') if n['data']['report_date'] == day and [n['data']['start_m'], n['data']['end_m']] == bounds), None)
        if existing:
            rid = existing['id']
        else:
            mask = samples.chainage.ge(lo) & samples.chainage.lt(hi)
            if hi == day_range[1]:
                mask |= samples.chainage.eq(hi)
            scope = samples.loc[mask].copy()
            if 'chainage_quality' in scope:
                scope = scope.loc[scope.chainage_quality.fillna('').str.startswith('usable')]
            rid = identity('PLC', day, 'source_scope', bounds)
            features = build_response_features(scope, package['config'], phase_config, response_id=rid, bounds=bounds)
            segment = identity('SEG', day, 'source_scope', bounds)
            graph.add_node('ConstructionSegment', segment, {'report_date': day, 'segment_role': 'source_specific_geology_PLC_scope',
                'start_m': lo, 'end_m': hi, 'geometry': 'interval', 'line': 'right', 'chainage_system': 'DyK',
                'spatial_quality': 'nominal_shield_reference_offset_to_face_unknown'})
            graph.add_node('PLCResponse', rid, {'response_id': rid, 'report_date': day, 'date': day, 'start_m': lo, 'end_m': hi,
                'start_time': features['time_scope']['first'], 'end_time': features['time_scope']['last'],
                'features_v2': features, 'effective_sample_count': features['basic_count'],
                'stable_duration_seconds': features['time_scope']['supported_duration_seconds'],
                'summary': {'sample_count': features['basic_count'], 'statistics': features['metrics']},
                'source_trace': {'samples_path': package['source']['samples'], 'samples_sha256': package['source']['samples_sha256'],
                    'exact_rows': features['metrics']['advance_speed']['source'], 'scope': 'own_geology_interval_intersect_day_not_shared_grid'},
                'baseline_reference': {'status': 'not_selected_for_source_specific_scope'},
                'comparisons_v2': {'controlled': {'status': 'not_established', 'reasons': ['source_scope_descriptive_only']}},
                'response_facts_v2': [], 'data_quality': {'fine_fault_boundary_analysis_allowed': False}, 'baseline_response_id': None})
            graph.add_edge('MEASURED_OVER', rid, segment, {'method': 'existing_stable_features_in_source_specific_bounds'})
        response = graph.nodes[rid]['data']; features = response['features_v2']; windows = features['windows']['basic']
        cv = [w['torque_cv'] for w in windows if w.get('torque_cv') is not None]
        co_trends = []
        for w in windows:
            for trend in ('increasing', 'decreasing'):
                fields = [k for k, v in w['parameter_changes'].items() if k in ('advance_speed', 'thrust', 'torque') and v['trend'] == trend]
                if len(fields) >= 2:
                    co_trends.append({'time_range': [w['start_time'], w['end_time']], 'trend': trend, 'fields': fields,
                                      'basis': 'existing_trend_flags_same_contiguous_window', 'source': w['source']})
        key = identity('GM', day, node['id'], rid)
        usable = all(features['metrics'][k]['status'] == 'usable_descriptive' for k in ('advance_speed', 'thrust', 'torque'))
        description = {'source_type': doc['document_type'], 'original_geology_range_m': [d['start_m'], d['end_m']],
            'geology_text': d['original_description'], 'literal_attributes': literal_units(d['original_description']),
            'sample_count': features['basic_count'], 'metrics': {k: features['metrics'][k] for k in ('advance_speed', 'thrust', 'torque', 'rpm_canonical', 'pr')},
            'time_scope': features['time_scope'], 'platform_composition': features['platform_composition'],
            'stability': {'continuous_windows': len(windows), 'torque_cv_min': min(cv) if cv else None,
                          'torque_cv_max': max(cv) if cv else None, 'co_trends': co_trends[:3]},
            'evidence_level': 'single_segment_spatial_description_not_statistical_association',
            'usable': usable, 'not_independent_from_overlapping_source_scopes': True}
        graph.add_node('EngineeringComparison', key, {'report_date': day, 'comparison_type': 'geology_machine_segment_description',
            'chainage_range_m': bounds, 'time_range': [response['start_time'], response['end_time']],
            'source_fact_ids': [node['id']], 'source_response_ids': [rid], 'attribute': 'performance_load_stability',
            'result': 'source_specific_descriptive_response' if usable else 'insufficient_qualified_response',
            'measured_difference': description, 'comparison_method': 'source_geology_interval_existing_PLC_features',
            'limitations': ['nominal_spatial_correspondence_only', 'not_causal_or_confirmed_statistical_association',
                            'overlapping_source_scopes_not_independent_geological_samples']})
        for support in (node['id'], rid):
            graph.add_edge('COMPARES', key, support, {'method': 'geology_source_scope_and_exact_PLC_rows'})
            graph.add_edge('DERIVED_FROM', key, support, {'method': 'source_specific_response_not_geological_diagnosis'})
        records.append({'relation_id': key, 'geology_fact_id': node['id'], 'response_id': rid, 'range_m': bounds,
                        'document_id': d['document_id'], 'document_type': doc['document_type'], 'date': day,
                        'description': description, 'source': d['source']})
    graph.metadata.setdefault('source_specific_response_analysis', {})[day] = records
    return records
