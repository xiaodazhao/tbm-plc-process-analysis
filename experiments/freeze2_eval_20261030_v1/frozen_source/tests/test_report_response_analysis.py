"""Regression tests for report-only evidence scopes and deterministic descriptions."""
from copy import deepcopy

import pandas as pd

from tbm_process.evidence_graph.response_analysis import correct_comparison_order, geology_for_segment, time_window_analysis
from tbm_process.evidence_graph.joint_reporting import _numeric_unit_pairs
from tbm_process.evidence_graph.store import EvidenceGraph
from tbm_process.evidence_graph.three_chapter import check_three_draft
from tests.test_three_chapter_report import fixture


def geology_graph():
    g = EvidenceGraph()
    g.add_node('GeologyDocument', 'D', {
        'document_type': 'TSP', 'metadata': {'available_date_user_confirmed': True, 'available_date': '2024-04-10',
            'chainage_prefix': 'DyK', 'alignment': 'right', 'availability_basis': 'test'},
        'date_audit': {'status': 'PASS'}})
    for fid, bounds, text in [('G1', [814, 822], 'F36断层破碎带'), ('G2', [822, 840], '岩体较破碎-破碎')]:
        g.add_node('GeologyFact', fid, {'document_id': 'D', 'start_m': bounds[0], 'end_m': bounds[1],
            'fact_type': 'geological_description', 'information_role': 'forecast',
            'original_description': text, 'extraction_quality': {'status': 'PASS'}, 'source': {'page': 14}})
    return g


def test_touching_geology_is_not_inherited_from_comparison_reference():
    graph = geology_graph()
    reference = geology_for_segment(graph, '2024-04-26', [819, 822], lambda *args: None)
    current = geology_for_segment(graph, '2024-04-26', [822, 827], lambda *args: None)
    assert [f['fact_id'] for f in reference] == ['G1']
    assert [f['fact_id'] for f in current] == ['G2']
    assert current[0]['original_range_m'] == [822, 840]
    assert current[0]['intersection_range_m'] == [822, 827]


def test_shared_geology_keeps_one_source_id_and_local_scope():
    graph = geology_graph()
    graph.nodes['G2']['data']['original_description'] = '岩性为板岩，局部DyK1+830至DyK1+832为F10断层破碎带'
    a = geology_for_segment(graph, '2024-04-26', [822, 827], lambda *args: None)[0]
    b = geology_for_segment(graph, '2024-04-26', [827, 828], lambda *args: None)[0]
    assert a['fact_id'] == b['fact_id']
    assert a['same_fact_id_reused_not_independent_observation']
    assert not a['literal_attributes']['structure'][0]['applies_to_intersection']


def test_future_geology_is_excluded():
    graph = geology_graph()
    assert not geology_for_segment(graph, '2024-04-01', [819, 822], lambda *args: None)


def window_fixture():
    count = 12
    frame = pd.DataFrame({'time': pd.date_range('2024-01-01', periods=count, freq='20s'),
                          'source_file': ['s']*count, 'source_row': range(count),
                          'advance_speed': range(10, 22), 'thrust': range(100, 88, -1), 'torque': [30]*count,
                          'sample_dt_seconds': [10, 20, 30]*4,
                          'rpm_quality': ['usable_provisional_mapping']*count, 'rpm_canonical': [5]*count})
    changes = {k: {'trend': direction, 'initial_median': float(frame[k].iloc[:3].median()),
                   'final_median': float(frame[k].iloc[-3:].median()), 'absolute_change': float(frame[k].iloc[-3:].median()-frame[k].iloc[:3].median())}
               for k, direction in [('advance_speed', 'increasing'), ('thrust', 'decreasing'), ('torque', 'stable')]}
    window = {'window_id': 'W', 'platform_id': 'P', 'phase_id': 'S', 'start_time': frame.time.iloc[0].isoformat(),
              'end_time': frame.time.iloc[-1].isoformat(), 'elapsed_seconds': 220, 'sample_count': count,
              'supported_duration_seconds': 240, 'torque_cv': .1, 'parameter_changes': changes,
              'metric_statistics': {}, 'source': [{'source_file': 's', 'source_rows': list(range(count))}]}
    features = {'windows': {'basic': [window]}}
    process = {'selected': [{'platform_ids': ['P'], 'start_time': window['start_time'], 'end_time': window['end_time']}]}
    config = {'units': {'advance_speed': 'mm/min', 'thrust': 'kN', 'torque': 'kN·m'}, 'analysis': {'minimum_duration_seconds': 180}}
    return frame, features, process, config


def test_opposed_trends_are_selected_and_quarter_medians_checked():
    frame, features, process, config = window_fixture()
    row = time_window_analysis(features, process, frame, config)[0]
    assert row['selected'] and row['pattern']['kind'] == 'opposed_parameter_trends'
    assert row['quarter_sample_count'] == 3
    assert row['supported_duration_seconds'] == 240
    assert row['elapsed_span_seconds'] == 220
    features['windows']['basic'][0]['parameter_changes']['advance_speed']['initial_median'] += 10
    bad = time_window_analysis(features, process, frame, config)[0]
    assert not bad['selected']
    assert 'cached_window_does_not_match_exact_rows' in bad['exclusion_reasons']


def test_empty_or_no_trend_data_does_not_force_examples():
    assert time_window_analysis({'windows': {'basic': []}}, {'selected': []}, pd.DataFrame(), {}) == []
    frame, features, process, config = window_fixture()
    for value in features['windows']['basic'][0]['parameter_changes'].values():
        value['trend'] = 'stable'
    assert not time_window_analysis(features, process, frame, config)[0]['selected']


def test_high_variability_is_retained_without_being_called_bad_sensor_data():
    frame, features, process, config = window_fixture()
    features['windows']['basic'][0]['torque_cv'] = .6
    row = time_window_analysis(features, process, frame, config)[0]
    assert not row['selected']
    assert row['data_quality']['high_cv_is_variability_not_sensor_failure']
    assert row['exclusion_reasons'] == ['retained_high_variability_window_not_first_readable_example']


def test_structure_assignments_reject_reference_fault_on_current_segment():
    pack, plan, draft = fixture()
    pack['response_analysis'] = {'schema_version': 'test'}
    fact = pack['facts'][2]
    fact['fact_id'] = 'SEGMENT_RESPONSE_SYNTHESIS'
    fact['writer_data'] = {'analysis_units': [{'analysis_unit_id': 'U', 'parameter_pattern': {'directions': {}, 'magnitude_text': None},
          'geology': [{'fact_id': 'G1', 'applies_to_PLC_range_m': [1015819, 1015822],
                       'original_range_m': [1015814, 1015822], 'original_description': 'F36断层破碎带'}]}]}
    claim = draft['chapters'][2]['claims'][0]
    claim.update(fact_ids=['SEGMENT_RESPONSE_SYNTHESIS'], analysis_unit_id='U',
                 geology_assignments=[{'fact_id': 'G1', 'plc_range_m': [1015822, 1015827]}],
                 text='DyK1015+822至DyK1015+827对应F36断层破碎带。')
    plan['chapters'][2]['paragraphs'][0]['fact_ids'] = ['SEGMENT_RESPONSE_SYNTHESIS']
    errors = check_three_draft(draft, plan, pack)
    assert 'geology_fact_assigned_to_wrong_PLC_segment' in errors
    assert 'fault_description_broadcast_beyond_source_range' in errors
    claim['geology_assignments'][0]['plc_range_m'] = [1015819, 1015822]
    claim['text'] = '此前DyK1015+819至DyK1015+822对应TSP原段DyK1015+814至DyK1015+822的F36断层破碎带。'
    assert not any('wrong_PLC_segment' in e or 'broadcast' in e for e in check_three_draft(draft, plan, pack))


def test_availability_date_cannot_become_observation_date():
    pack, plan, draft = fixture()
    pack['facts'][2]['writer_data'].update(source_date_role='report_availability_date', observation_date=None)
    draft['chapters'][2]['claims'][0]['text'] = '2024年4月22日观测为V级围岩。'
    assert 'report_availability_date_written_as_observation_date' in check_three_draft(draft, plan, pack)
    draft['chapters'][2]['claims'][0]['text'] = '截至2024年4月22日已可用的历史资料记载V级，原始观测日期未记录。'
    assert 'report_availability_date_written_as_observation_date' not in check_three_draft(draft, plan, pack)


def test_explicit_nested_sample_count_is_checked_with_record_unit():
    values = _numeric_unit_pairs([{'canonical_text': '', 'data': {'current_profile': {'sample_count': 55}}}])
    assert (55.0, '条') in values
    assert (56.0, '条') not in values


def test_comparison_lead_correction_requires_correct_median_roles_and_preserves_membership():
    analysis = {'segment_analyses': [{'analysis_unit_id': 'U', 'reference': {'range_display': 'A'},
                'current': {'range_display': 'B'}, 'changes': {'advance_speed': {'reference': 10, 'current': 15, 'unit': 'mm/min'}}}]}
    draft = {'chapters': [{'claims': [{'text': 'A与B相比，速度由10.00mm/min变为15.00mm/min。',
             'analysis_unit_id': 'U', 'geology_assignments': [{'fact_id': 'G', 'plc_range_m': [1,2]}]}]}]}
    original = deepcopy(draft)
    corrected, edits = correct_comparison_order(draft, analysis)
    assert corrected['chapters'][0]['claims'][0]['text'].startswith('B与A相比')
    assert corrected['chapters'][0]['claims'][0]['geology_assignments'] == draft['chapters'][0]['claims'][0]['geology_assignments']
    assert len(edits) == 1 and original == draft
    assert correct_comparison_order(corrected, analysis)[1] == []


def test_analysis_table_preserves_each_original_geology_scope():
    from tbm_process.reporting_v2.three_chapter_rendering import analysis_table
    geo = {'source_type': 'TSP', 'original_range_display': 'DyK1015+822至DyK1015+840',
           'literal_attributes': {k: [] for k in ('structure','lithology','integrity','water')}}
    p = {'response_id': 'P', 'range_m': [1015827,1015828], 'range_display': 'DyK1015+827至DyK1015+828',
         'sample_count': 55, 'time_scope': {'supported_duration_seconds': 1010}, 'metrics': {'rpm_canonical': {'median': 5.39}}, 'geology': [geo]}
    pack = {'response_analysis': {'segment_analyses': [{'reference': p, 'current': p}]}}
    html, md = analysis_table('3.1', pack)
    assert 'DyK1015+822至DyK1015+840' in html
    assert 'DyK1015+827至DyK1015+828' in html
    assert '55条' in html and '16分50秒' in html
    assert len([row for row in md if '55条' in row]) == 1
