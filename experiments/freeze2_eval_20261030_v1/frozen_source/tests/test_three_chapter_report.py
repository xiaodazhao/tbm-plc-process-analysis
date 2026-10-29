"""Synthetic regression fixtures only, never used to produce report values."""
from copy import deepcopy

import pandas as pd

from tbm_process.evidence_graph.literal_relations import compare_units, literal_units, overlap
from tbm_process.evidence_graph.three_chapter import (absolute_geology_description, check_relation_planning,
                                                     check_three_draft, comparison_pattern, process_summary, requests)
from tbm_process.evidence_graph.joint_reporting import plan_quality, draft_quality, normalize_three_chapter_ids
from tbm_process.reporting_v2.narrative import check_plan


def fixture():
    facts = []
    for i in range(1, 4):
        facts.append({'fact_id': str(i), 'chapter': i, 'section': str(i), 'canonical_text': '原报告建议Ⅳ级',
                      'data': {'text': '原报告建议Ⅳ级'}, 'writer_data': {'text': '原报告建议Ⅳ级'},
                      'required': True, 'nature': 'forecast', 'graph_support_ids': [], 'source': {}})
    pack = {'schema_version': 'three_chapter_test', 'date': '2024-04-26', 'chapter_ids': [1, 2, 3], 'facts': facts,
            'writer_fact_ids': ['1', '2', '3'], 'program_relations': [], 'semantic_relation_candidates': []}
    plan = {'chapters': [{'chapter_id': i, 'fact_ids': [str(i)], 'paragraphs': [{'paragraph_id': 'p'+str(i),
              'section': str(i), 'intent': 'describe', 'purpose': '工程主题', 'fact_ids': [str(i)], 'relation_ids': []}]} for i in range(1, 4)], 'omitted': []}
    draft = {'chapters': [{'chapter_id': i, 'claims': [{'paragraph_id': 'p'+str(i), 'text': '原报告建议Ⅳ级', 'fact_ids': [str(i)], 'relation_ids': []}]} for i in range(1, 4)]}
    return pack, plan, draft


def test_compound_degree_is_literal_not_worst_case():
    a = literal_units('岩体较破碎-破碎')['integrity']
    b = literal_units('岩体破碎')['integrity']
    assert a[0]['text'] == '岩体较破碎-破碎'
    assert compare_units('integrity', a, b)[0] == 'range_overlap'


def test_local_supplement_kept():
    a = literal_units('岩性为板岩夹变质砂岩')['lithology']
    b = literal_units('岩性为板岩夹变质砂岩，局部夹压碎岩')['lithology']
    assert compare_units('lithology', a, b)[0] == 'main_combination_with_supplement'
    assert b[-1]['locality'] == ['局部']


def test_fault_breccia_not_numbered_fault():
    assert not literal_units('岩性为断层角砾、压碎岩')['structure']
    assert literal_units('推测该段进入F36断层破碎带')['structure'][0]['fault_ids'] == ['F36']


def test_fault_endpoint_touch_not_shared_interval():
    assert overlap([1015764, 1015822], [1015822, 1015840]) is None
    assert overlap([None, None], [1015822, 1015840]) is None


def test_explicit_local_range_preserved():
    x = literal_units('其中DyK1015+764～DyK1015+822段为F36断层破碎带')['structure'][0]
    assert x['ranges_m'] == [[1015764, 1015822]]
    assert overlap(x['ranges_m'][0], [1015822, 1015827]) is None


def test_missing_not_dry_or_agreement():
    assert compare_units('water', literal_units('掌子面存在线-股状出水')['water'], [])[0] == 'source_only_or_unknown'


def test_rock_main_order_not_lost():
    a = literal_units('岩性为板岩夹变质砂岩')['lithology']
    b = literal_units('岩性为变质砂岩夹板岩')['lithology']
    assert compare_units('lithology', a, b)[0] == 'different_descriptions'


def test_three_is_opt_in_six_default_stays():
    pack, plan, _ = fixture()
    assert check_plan(plan, pack['facts'], chapter_ids=[1, 2, 3])['passed']
    assert not check_plan(plan, pack['facts'])['passed']
    assert plan_quality(plan, pack)['passed']


def test_not_forcing_relation_in_descriptive_paragraph():
    pack, plan, _ = fixture()
    pack['program_relations'] = [{'relation_id': 'R', 'evidence_fact_ids': ['3']}]
    assert not check_relation_planning(plan, pack)
    plan['chapters'][2]['paragraphs'][0]['intent'] = 'compare'
    assert check_relation_planning(plan, pack)


def test_unknown_and_missing_relation_endpoints_fail():
    pack, plan, _ = fixture()
    pack['program_relations'] = [{'relation_id': 'R', 'evidence_fact_ids': ['2', '3']}]
    plan['chapters'][2]['paragraphs'][0]['relation_ids'] = ['R']
    assert 'planning_relation_missing_endpoint_facts' in plan_quality(plan, pack)['errors']
    plan['chapters'][2]['paragraphs'][0]['relation_ids'] = ['invented']
    assert 'unknown_planning_relation' in plan_quality(plan, pack)['errors']


def test_grade_injection_detected():
    pack, plan, draft = fixture()
    assert draft_quality(draft, plan, pack)['passed']
    draft['chapters'][2]['claims'][0]['text'] = '原报告建议Ⅲ级'
    assert any('unsupported_rock_grade' in e for e in draft_quality(draft, plan, pack)['errors'])


def test_fault_id_and_strict_index_not_invented():
    pack, plan, draft = fixture()
    draft['chapters'][2]['claims'][0]['text'] = '进入F36断层，FPI升高'
    errors = check_three_draft(draft, plan, pack)
    assert any('unsupported_fault_id' in e for e in errors)
    assert any('unapproved_index' in e for e in errors)


def test_no_causality_and_future_reveal():
    pack, plan, draft = fixture()
    draft['chapters'][2]['claims'][0]['text'] = '岩体破碎导致设备故障，实际揭露Ⅳ级围岩'
    assert not draft_quality(draft, plan, pack)['passed']


def test_process_duration_not_span():
    package = {'platforms': [{'platform_id': 'P', 'start_time': '2024-04-26T00:00:00', 'end_time': '2024-04-26T00:10:00'}], 'source': {}}
    frame = pd.DataFrame({'experimental_platform_id': ['P', 'P', 'P'], 'machine_state': ['advancing', 'stopped', 'advancing'],
                          'sample_dt_seconds': [10, 200, 20], 'source_file': ['test']*3, 'source_row': [2, 3, 4]})
    s = process_summary(package, frame)
    assert s['groups'][0]['advancing_seconds'] == 30
    assert s['groups'][0]['continuous_excavation_confirmed'] is False


def test_empty_process_no_invented_windows():
    frame = pd.DataFrame(columns=['experimental_platform_id', 'machine_state', 'sample_dt_seconds'])
    assert process_summary({'platforms': [], 'source': {}}, frame)['selected'] == []


def test_prompt_same_facts_AB_no_relations_on_A():
    pack, _, _ = fixture()
    policy = {'llm': {'model': 'deepseek-chat'}}
    a = deepcopy(pack); a['program_relations'] = []
    pack['program_relations'] = [{'relation_id': 'R', 'type': 'geology_machine_segment_description', 'evidence_fact_ids': ['3']}]
    fa, ra, _ = requests(a, policy); fb, rb, _ = requests(pack, policy)
    assert fa == fb
    assert ra['messages'][0] == rb['messages'][0]
    assert json_load(ra)['program_relations'] == []
    assert len(json_load(rb)['program_relations']) == 1


def json_load(req):
    import json
    return json.loads(req['messages'][1]['content'])


def test_coverage_does_not_become_full_day_progress():
    pack, plan, draft = fixture()
    pack['facts'][0]['writer_data']['day_scope_complete'] = False
    draft['chapters'][0]['claims'][0]['text'] = '全天完成进尺7.00m'
    assert any('incomplete_coverage' in e for e in check_three_draft(draft, plan, pack))


def test_interface_normalization_does_not_change_content_or_membership():
    _, plan, _ = fixture()
    original = deepcopy(plan)
    plan['chapters'][0]['chapter_id'] = '1'
    normalized, audit = normalize_three_chapter_ids(plan)
    assert normalized == original
    assert plan['chapters'][0]['chapter_id'] == '1'
    assert audit == [{'chapter_index': 0, 'before': '1', 'after': 1}]
    plan['chapters'][0]['chapter_id'] = '4'
    assert normalize_three_chapter_ids(plan)[0]['chapters'][0]['chapter_id'] == '4'


def test_reader_style_replaces_disclaimer_without_changing_support_or_numbers():
    from scripts.run_three_chapter_content_revision import _apply_reader_style
    draft = {'chapters': [{'chapter_id': 1, 'claims': [{
        'text': '07:36至07:41速度由18.56变为33.99mm/min。该窗口趋势不代表整个活动时段。',
        'fact_ids': ['F1'], 'relation_ids': ['R1'], 'phenomenon_ids': ['P1'],
    }]}]}
    styled, edits = _apply_reader_style(draft)
    assert styled['chapters'][0]['claims'][0]['text'].endswith('该变化范围限于上述局部窗口。')
    assert styled['chapters'][0]['claims'][0]['fact_ids'] == ['F1']
    assert styled['chapters'][0]['claims'][0]['relation_ids'] == ['R1']
    assert '18.56' in styled['chapters'][0]['claims'][0]['text']
    assert draft['chapters'][0]['claims'][0]['text'].endswith('不代表整个活动时段。')
    assert len(edits) == 1


def test_association_requires_available_relation_only_for_joint_evidence():
    pack, plan, draft = fixture()
    pack['program_relations'] = [{'relation_id': 'R', 'type': 'geology_machine_segment_description', 'evidence_fact_ids': ['2', '3']}]
    draft['chapters'][2]['claims'][0]['fact_ids'] = ['2', '3']
    assert any('joint_geology_response_missing' in e for e in check_three_draft(draft, plan, pack))
    draft['chapters'][2]['claims'][0]['fact_ids'] = ['3']
    assert not any('joint_geology_response_missing' in e for e in check_three_draft(draft, plan, pack))


def test_plan_evidence_closure_only_resolves_chosen_known_relations():
    from tbm_process.evidence_graph.three_chapter import compile_plan_evidence
    pack, plan, _ = fixture()
    extra = deepcopy(pack['facts'][-1]); extra['fact_id'] = 'extra'
    pack['facts'].append(extra); pack['writer_fact_ids'].append('extra')
    pack['program_relations'] = [{'relation_id': 'R', 'evidence_fact_ids': ['3', 'extra']}]
    plan['chapters'][2]['paragraphs'][0]['relation_ids'] = ['R']
    compiled, audit = compile_plan_evidence(plan, pack)
    assert compiled['chapters'][2]['paragraphs'][0]['fact_ids'] == ['3', 'extra']
    assert plan['chapters'][2]['paragraphs'][0]['fact_ids'] == ['3']
    assert audit['semantic_review_required']
    pack['program_relations'] = []
    compiled, audit = compile_plan_evidence(plan, pack)
    assert compiled['omitted'][0]['reason_author'] == 'program_not_LLM'
    assert compiled['chapters'][2]['paragraphs'][0]['relation_ids'] == ['R']
    assert not plan_quality(compiled, pack)['passed']


def test_grade_only_value_without_suffix_is_supported():
    pack, plan, draft = fixture()
    pack['facts'][2]['writer_data'] = {'value': 'Ⅳ'}
    assert not any('unsupported_rock_grade' in e for e in check_three_draft(draft, plan, pack))
    draft['chapters'][2]['claims'][0]['text'] = '建议Ⅴ级'
    assert any('unsupported_rock_grade' in e for e in check_three_draft(draft, plan, pack))


def test_window_length_materialized_from_actual_bounds():
    from tbm_process.evidence_graph.three_chapter import clarify_pack_for_quality
    pack, _, _ = fixture()
    pack['facts'].append({'fact_id': 'AHEAD_CONTEXT', 'writer_data': {'ahead_range_display': 'DyK1015+828至DyK1015+848'}})
    actual = clarify_pack_for_quality(pack)['facts'][-1]['writer_data']
    assert actual['range_m'] == [1015828, 1015848]
    assert actual['window_length_display'] == '20.00m'
    from tbm_process.evidence_graph.joint_reporting import _numeric_unit_pairs
    assert (20.0, 'm') in _numeric_unit_pairs([{'canonical_text': '', 'data': actual}])
    assert (30.0, 'm') not in _numeric_unit_pairs([{'canonical_text': '', 'data': actual}])
    assert 'range_m' not in pack['facts'][-1]['writer_data']


def test_display_rounding_is_explicit_not_relaxed_numeric_tolerance():
    from tbm_process.evidence_graph.three_chapter import clarify_pack_for_quality
    pack, _, _ = fixture()
    pack['facts'][2]['writer_data']['metrics'] = {'thrust': {'median': 25434.035}}
    value = clarify_pack_for_quality(pack)['facts'][2]['writer_data']['metrics']['thrust']
    assert value['display_values']['median'] == f'{25434.035:.2f}'


def test_nonoverlapping_sketch_not_current_PLC_geology():
    pack, plan, draft = fixture()
    pack['facts'][1]['nature'] = 'PLC_response'
    pack['facts'][1]['writer_data']['range_m'] = [1015822, 1015827]
    pack['facts'][2]['writer_data'].update(source_type='SKETCH', range_m=[1015789, 1015819])
    draft['chapters'][2]['claims'][0].update(text='该区段此前素描为Ⅳ级，推进速度较高。', fact_ids=['2', '3'])
    assert any('no_spatial_overlap' in e for e in check_three_draft(draft, plan, pack))
    draft['chapters'][2]['claims'][0]['text'] = '该素描与机器统计区段不重叠，仅作历史背景。'
    assert not any('no_spatial_overlap' in e for e in check_three_draft(draft, plan, pack))


def test_ahead_point_risk_cannot_be_imported_from_parent_interval():
    pack, plan, draft = fixture()
    pack['facts'].append({'fact_id': 'AHEAD_CONTEXT', 'writer_data': {'range_m': [1015828, 1015848]}})
    plan['chapters'][2]['paragraphs'][0]['section'] = '3.3'
    draft['chapters'][2]['claims'][0]['text'] = '原报告预测DyK1015+822附近存在出水风险。'
    assert any('risk_point_outside' in e for e in check_three_draft(draft, plan, pack))
    draft['chapters'][2]['claims'][0]['text'] = '原报告提及的DyK1015+822附近出水风险位于窗口外。'
    assert not any('risk_point_outside' in e for e in check_three_draft(draft, plan, pack))


def test_historical_context_is_limited_to_one_paragraph():
    pack, plan, _ = fixture()
    plan['chapters'][2]['paragraphs'][0]['section'] = '3.2'
    duplicate = deepcopy(plan['chapters'][2]['paragraphs'][0])
    duplicate['paragraph_id'] = 'p3b'
    plan['chapters'][2]['paragraphs'].append(duplicate)
    assert 'historical_context_overexpanded' in check_relation_planning(plan, pack)


def test_final_validation_correction_is_presentation_only():
    from scripts.run_three_chapter_content_revision import _apply_final_validation_corrections
    draft = {
        'chapters': [
            {'chapter_id': 1, 'claims': [{'text': 'keep-1'}]},
            {'chapter_id': 2, 'claims': [{'text': 'keep-2'}]},
            {'chapter_id': 3, 'claims': [
                {'text': 'DyK1015+819至+822段；DyK1015+822至+827段；DyK1015+827至+828段'},
                {'text': 'keep-3'},
                {'text': 'keep-4'},
                {'text': 'DyK1015+822附近存在出水风险'},
            ]},
        ]
    }
    corrected, edits = _apply_final_validation_corrections(draft)
    assert draft['chapters'][2]['claims'][3]['text'] == 'DyK1015+822附近存在出水风险'
    assert corrected['chapters'][2]['claims'][3]['text'] == 'DyK1015+822附近存在出水风险'
    assert 'DyK1015+819至DyK1015+822' in corrected['chapters'][2]['claims'][0]['text']
    assert corrected['chapters'][0] == draft['chapters'][0]
    assert corrected['chapters'][1] == draft['chapters'][1]
    assert len(edits) == 1


def test_relative_geology_phrase_is_removed_only_from_absolute_description():
    text = '该段围岩较前一段明显变差，岩性为板岩夹变质砂岩，局部夹压碎岩，岩体破碎'
    assert absolute_geology_description(text) == '岩性为板岩夹变质砂岩，局部夹压碎岩，岩体破碎'
    assert absolute_geology_description('岩性为板岩夹变质砂岩，岩体破碎') == '岩性为板岩夹变质砂岩，岩体破碎'


def test_relative_geology_claim_must_preserve_source_reference():
    pack, plan, draft = fixture()
    draft['chapters'][2]['claims'][0]['text'] = '对应HSP描述围岩较前段明显变差。'
    assert any('relative_geology_reference_not_preserved' in error for error in check_three_draft(draft, plan, pack))
    draft['chapters'][2]['claims'][0]['text'] = 'HSP原报告称该区段较该报告前一预报区段明显变差。'
    assert not any('relative_geology_reference_not_preserved' in error for error in check_three_draft(draft, plan, pack))


def test_window_change_requires_quarter_median_definition():
    pack, plan, draft = fixture()
    pack['facts'][1]['fact_id'] = 'PROCESS_ANALYSIS_SUMMARY'
    pack['writer_fact_ids'][1] = 'PROCESS_ANALYSIS_SUMMARY'
    plan['chapters'][1]['fact_ids'] = ['PROCESS_ANALYSIS_SUMMARY']
    plan['chapters'][1]['paragraphs'][0]['fact_ids'] = ['PROCESS_ANALYSIS_SUMMARY']
    draft['chapters'][1]['claims'][0]['fact_ids'] = ['PROCESS_ANALYSIS_SUMMARY']
    draft['chapters'][1]['claims'][0]['text'] = '窗口内推进速度由18.56变为33.99mm/min。'
    assert any('window_edge_statistic_not_disclosed' in error for error in check_three_draft(draft, plan, pack))
    draft['chapters'][1]['claims'][0]['text'] = '窗口首末四分之一记录的中位数由18.56变为33.99mm/min。'
    assert not any('window_edge_statistic_not_disclosed' in error for error in check_three_draft(draft, plan, pack))
    draft['chapters'][1]['claims'][0]['text'] = '窗口首末各3条记录的中位数由18.56变为33.99mm/min。'
    assert not any('window_edge_statistic_not_disclosed' in error for error in check_three_draft(draft, plan, pack))


def test_comparison_pattern_reports_mixed_directions_and_relative_magnitude():
    row = {'changes': {
        'advance_speed': {'percent': 2.57},
        'thrust': {'percent': 9.79},
        'torque': {'percent': 8.62},
    }}
    pattern = comparison_pattern(row)
    assert pattern['magnitude_text'] == '推进速度变化幅度低于总推力和刀盘扭矩'
    row['changes']['thrust']['percent'] = -14.17
    pattern = comparison_pattern(row)
    assert '参数变化方向并不一致' in pattern['direction_text']


def test_required_ahead_segments_cannot_be_silently_omitted():
    pack, plan, draft = fixture()
    pack['facts'][2]['fact_id'] = 'AHEAD_FORECAST_SUMMARY'
    pack['facts'][2]['required_text_anchors'] = ['DyK1015+822至DyK1015+840', 'DyK1015+833']
    pack['writer_fact_ids'][2] = 'AHEAD_FORECAST_SUMMARY'
    plan['chapters'][2]['fact_ids'] = ['AHEAD_FORECAST_SUMMARY']
    plan['chapters'][2]['paragraphs'][0]['fact_ids'] = ['AHEAD_FORECAST_SUMMARY']
    draft['chapters'][2]['claims'][0].update(fact_ids=['AHEAD_FORECAST_SUMMARY'], text='前方资料仅覆盖DyK1015+833。')
    assert any('required_display_missing:AHEAD_FORECAST_SUMMARY:DyK1015+822至DyK1015+840' in error
               for error in draft_quality(draft, plan, pack)['errors'])


def test_same_evidence_allows_prose_reorganisation_but_protects_values_and_sources():
    from scripts.run_three_chapter_content_revision import _same_evidence
    base = {
        'source': {'samples_sha256': 'same'},
        'facts': [
            {'fact_id': 'PROCESS', 'nature': 'deterministic', 'canonical_text': '旧表达',
             'data': {'summary': '旧表达', 'seconds': 120},
             'source': {'catalog': '/old/run/catalog.json', 'catalog_sha256': 'catalog-hash'}},
            {'fact_id': 'GF:1', 'nature': 'forecast', 'canonical_text': '岩体破碎',
             'data': {'range_m': [1, 2]}, 'source': {'page': 10}},
        ],
    }
    revised = deepcopy(base)
    revised['facts'][0]['canonical_text'] = '新表达'
    revised['facts'][0]['data']['summary'] = '新表达'
    revised['facts'][0]['source']['catalog'] = '/new/run/catalog.json'
    revised['facts'].append({'fact_id': 'PROCESS_ANALYSIS_SUMMARY', 'nature': 'deterministic',
                             'canonical_text': '现象摘要', 'data': {'seconds': 120}, 'source': None})
    audit = _same_evidence(base, revised)
    assert audit['same_underlying_evidence_confirmed']
    assert audit['presentation_only_changes']

    changed_value = deepcopy(revised)
    changed_value['facts'][0]['data']['seconds'] = 121
    assert not _same_evidence(base, changed_value)['same_underlying_evidence_confirmed']

    changed_source = deepcopy(revised)
    changed_source['facts'][1]['source']['page'] = 11
    assert not _same_evidence(base, changed_source)['same_underlying_evidence_confirmed']


def test_nested_summary_supports_fault_id_and_second_unit():
    pack, plan, draft = fixture()
    pack['facts'][2]['fact_id'] = 'SEGMENT_RESPONSE_SYNTHESIS'
    pack['facts'][2]['writer_data'] = {
        'analysis_units': [{
            'geology': [{'absolute_description': 'F36断层破碎带'}],
            'parameter_pattern': {
                'directions': {'advance_speed': 'increase', 'thrust': 'increase', 'torque': 'increase'},
                'magnitude_text': None,
            },
        }],
        'supported_duration_seconds': 10370,
    }
    pack['facts'][2]['data'] = deepcopy(pack['facts'][2]['writer_data'])
    pack['writer_fact_ids'][2] = 'SEGMENT_RESPONSE_SYNTHESIS'
    plan['chapters'][2]['fact_ids'] = ['SEGMENT_RESPONSE_SYNTHESIS']
    plan['chapters'][2]['paragraphs'][0]['fact_ids'] = ['SEGMENT_RESPONSE_SYNTHESIS']
    draft['chapters'][2]['claims'][0].update(
        text='F36断层破碎带对应样本支持时长10370秒。',
        fact_ids=['SEGMENT_RESPONSE_SYNTHESIS'],
    )
    result = draft_quality(draft, plan, pack)
    assert not any('unsupported_fault_id' in error for error in result['errors'])
    assert not any('unsupported_value_unit_pair:10370.0秒' in error for error in result['errors'])
