"""Scoped positive and injected-error fixtures, never field observations."""
from copy import deepcopy
import pytest

from tbm_process.evidence_graph.report_reliability import (asserts_observation,percentage_check,semantic_checks)
from tbm_process.evidence_graph.joint_reporting import draft_quality,plan_quality
from tbm_process.evidence_graph.three_chapter import check_relation_planning
from tests.test_three_chapter_report import fixture


def values():
    return [{'data':{'changes':{'advance_speed':{'reference':100.,'current':90.69,'percent':-9.31},
                              'thrust':{'reference':100.,'current':109.31,'percent':9.31}}}}]


@pytest.mark.parametrize('text',['推进速度下降9.31%。','推进速度降低9.31%。','推进速度变化量为−9.31%。',
                                '当前区段相较参考区段，推进速度下降9.31%。','推进速度从100下降至90.69。'])
def test_equivalent_decreases(text):
    assert not percentage_check(text,values(),[(-9.31,'%'),(9.31,'%')])[0]


@pytest.mark.parametrize('text',['推进速度上升9.31%。','推进速度下降19.31%。','推进速度下降-9.31%。',
                                '推进速度从90.69下降至100。','总推力下降9.31%。'])
def test_wrong_parameter_direction_value_or_endpoints_blocked(text):
    assert percentage_check(text,values(),[(-9.31,'%'),(9.31,'%')])[0]


@pytest.mark.parametrize('text',['并非当日新增揭示。','不构成当天实际揭露。','该记录不属于当日新增揭示。',
                                '未取得可确认属于当日新增揭示的资料。','不等于实际揭露。'])
def test_negated_reveals_not_asserted(text):
    assert not asserts_observation(text,current=True)
    assert not asserts_observation(text)


@pytest.mark.parametrize('text',['当日新增揭示Ⅳ级围岩。','今日实际揭露破碎岩体。',
                                '并非当日新增揭示，但当日实际揭露了断层。'])
def test_positive_reveal_or_positive_second_clause_is_detected(text):
    assert asserts_observation(text,current=True)


def segment_fixture():
    pack,plan,draft=fixture()
    f=pack['facts'][2];f['fact_id']='SEGMENT_RESPONSE_SYNTHESIS'
    f['writer_data']={'analysis_units':[]};f['data']=f['writer_data']
    pack['writer_fact_ids'][2]=f['fact_id']
    plan['chapters'][2]['fact_ids']=[f['fact_id']];plan['chapters'][2]['paragraphs'][0]['fact_ids']=[f['fact_id']]
    draft['chapters'][2]['claims'][0].update(fact_ids=[f['fact_id']],text='缺少可匹配地质资料的相邻区段，未开展比较。')
    pack['response_analysis']={'exists':True}
    return pack,plan,draft


def test_no_analysis_units_no_id_is_legal_but_fake_ids_are_not():
    pack,plan,draft=segment_fixture()
    q=draft_quality(draft,plan,pack)
    assert q['passed'] and q['status']=='REVIEW' and q['formal_engineering_acceptance']=='NOT_COMPLETED'
    for value in ('','invented'):
        bad=deepcopy(draft);bad['chapters'][2]['claims'][0]['analysis_unit_id']=value
        assert any('invalid_analysis_unit_id' in e for e in draft_quality(bad,plan,pack)['errors'])


def test_no_units_does_not_allow_invented_comparison():
    pack,_,draft=segment_fixture();draft['chapters'][2]['claims'][0]['text']='当前区段相较参考区段，推进速度下降9.31%。'
    assert any('comparison_asserted_without' in e for e in semantic_checks(draft,pack)[0])


def test_positive_PLC_support_must_not_be_described_as_global_shortage():
    pack,_,draft=segment_fixture()
    pack['facts'].append({'fact_id':'PARAMETER_ANALYSIS_SUMMARY','writer_data':{'qualified_sample_count':100}})
    draft['chapters'][2]['claims'][0]['text']='现有合格样本不足。'
    assert any('sample_shortage' in e for e in semantic_checks(draft,pack)[0])


def test_historical_nonoverlap_cannot_be_current_excavation():
    pack,_,draft=fixture();f=pack['facts'][2]
    f['writer_data'].update(source_type='SKETCH',information_role='observed',source_date='2024-04-22',range_m=[100.,110.])
    pack['facts'].append({'fact_id':'GEO_CONTEXT','writer_data':{'day_range_m':[120.,130.]}})
    draft['chapters'][2]['claims'][0]['text']='此前素描显示，当前开挖段落岩体破碎。'
    assert any('historical_range_written' in e for e in semantic_checks(draft,pack)[0])
    draft['chapters'][2]['claims'][0]['text']='历史记录区段岩体破碎，仅作为此前背景。'
    assert not semantic_checks(draft,pack)[0]


def test_future_source_detected_even_without_explicit_date_in_text():
    pack,_,draft=fixture();pack['facts'][2]['writer_data'].update(source_date='2024-04-27')
    assert any('source_not_available' in e for e in semantic_checks(draft,pack)[0])


def test_availability_negation_is_not_observation_assignment():
    pack,_,draft=fixture();pack['facts'][2]['writer_data'].update(source_date_role='report_availability_date',observation_date=None)
    draft['chapters'][2]['claims'][0]['text']='截至2024年4月22日已可用，实际观测日期未记录。'
    assert not semantic_checks(draft,pack)[0]
    draft['chapters'][2]['claims'][0]['text']='2024年4月22日现场测得破碎岩体。'
    assert any('availability_date_written' in e for e in semantic_checks(draft,pack)[0])


def test_unestablished_controls_not_promoted_to_equal_controls():
    pack,_,draft=fixture();draft['chapters'][2]['claims'][0]['text']='已经排除了操作因素，处于严格相同控制条件。'
    assert any('unverified_control' in e for e in semantic_checks(draft,pack)[0])
    draft['chapters'][2]['claims'][0]['text']='尚未核实严格相同控制条件。'
    assert not semantic_checks(draft,pack)[0]


def test_ambiguous_comparison_is_REVIEW_not_unconditional_pass():
    pack,_,draft=segment_fixture()
    pack['facts'][2]['writer_data']['analysis_units']=[{'analysis_unit_id':'U','reference_range_display':'DyK1+000至DyK1+010',
        'current_range_display':'DyK1+010至DyK1+020','geology':[]}]
    c=draft['chapters'][2]['claims'][0];c.update(analysis_unit_id='U',text='参考区段与当前区段相比，推进速度下降9.31%。')
    assert any('comparison_subject' in r for r in semantic_checks(draft,pack)[1])
    c['text']='当前区段相较参考区段，推进速度下降9.31%。'
    assert not semantic_checks(draft,pack)[1]


def test_unknown_relations_and_missing_endpoints_still_fail():
    pack,plan,_=fixture();pack['program_relations']=[{'relation_id':'R','evidence_fact_ids':['2','3']}]
    plan['chapters'][2]['paragraphs'][0]['relation_ids']=['R']
    assert 'planning_relation_missing_endpoint_facts' in plan_quality(plan,pack)['errors']


def test_wrong_source_relative_reference_is_blocked():
    pack,_,draft=segment_fixture()
    pack['facts'][2]['writer_data']['analysis_units']=[{'analysis_unit_id':'U','reference_range_display':'DyK1+000至DyK1+010',
        'current_range_display':'DyK1+010至DyK1+020','geology':[{'relative_comparison':{'reference_range_display':'DyK1+000至DyK1+050'}}]}]
    draft['chapters'][2]['claims'][0].update(analysis_unit_id='U',text='原报告以DyK1+010至DyK1+020为参照，围岩较前段变差。')
    assert any('source_relative_reference_range_conflict' in e for e in semantic_checks(draft,pack)[0])


def test_grade_and_new_fault_errors_still_block():
    pack,plan,draft=fixture();draft['chapters'][2]['claims'][0]['text']='原报告建议Ⅲ级，进入F999断层。'
    q=draft_quality(draft,plan,pack)
    assert q['status']=='FAIL' and any('unsupported_rock_grade' in e for e in q['errors'])
    assert any('unsupported_fault_id' in e for e in q['errors'])


@pytest.mark.parametrize('text,seconds', [('3小时03分40秒',11020),('0秒',0),('15分20秒',920),('2小时',7200)])
def test_compound_duration_is_one_typed_quantity(text,seconds):
    from tbm_process.evidence_graph.report_reliability import duration_check
    masked,errors=duration_check(text,[(seconds,'秒')])
    assert not errors and not masked.strip()
    assert duration_check(text,[(seconds+20,'秒')])[1]


def test_history_relative_position_is_not_current_reveal():
    pack,_,draft=fixture()
    pack['facts'][2]['writer_data'].update(source_type='SKETCH',range_m=[100,110])
    pack['facts'].append({'fact_id':'GEO_CONTEXT','writer_data':{'day_range_m':[120,130]}})
    c=draft['chapters'][2]['claims'][0]
    c['text']='该历史区段位于当日开挖范围后方，仅作历史背景。'
    assert not semantic_checks(draft,pack)[0]
    assert semantic_checks(draft,pack)[1]
    pack['facts'][2]['writer_data']['range_m']=[122,125]
    assert any('position_relation_conflict' in e for e in semantic_checks(draft,pack)[0])


def test_daily_background_is_explicit_and_not_a_segment_measurement():
    from tbm_process.evidence_graph.report_reliability import claim_facts
    pack,plan,draft=segment_fixture();pack['claim_scope_contract']='v1'
    f=pack['facts'][2]
    f['writer_data']['daily_background']={'statistics':{'thrust':{'median':2000,'unit':'kN'}}}
    c=draft['chapters'][2]['claims'][0]
    c.update(statistic_scope='daily_background',text='全天稳定推进背景的总推力中位数2000kN。')
    available=claim_facts(c,[{'fact_id':f['fact_id'],'data':f['writer_data'],'canonical_text':'9000kN'}],pack)
    assert 'analysis_units' not in available[0]['data'] and available[0]['canonical_text']==''
    assert not semantic_checks(draft,pack)[0]
    c['text']='该地质区段总推力中位数2000kN。'
    assert any('daily_' in e for e in semantic_checks(draft,pack)[0])


def test_unit_scope_does_not_borrow_another_units_values():
    from tbm_process.evidence_graph.report_reliability import claim_facts
    facts=[{'fact_id':'SEGMENT_RESPONSE_SYNTHESIS','canonical_text':'2000kN 9000kN', 'data':{
        'analysis_units':[{'analysis_unit_id':'U1','thrust':2000},{'analysis_unit_id':'U2','thrust':9000}]}}]
    allowed=claim_facts({'statistic_scope':'segment_comparison','analysis_unit_id':'U1'},facts,{'claim_scope_contract':'v1'})
    assert allowed[0]['data']=={'analysis_unit_id':'U1','thrust':2000}
    assert '9000' not in str(allowed)


def test_reversed_raw_geology_quarantined_without_changing_original_graph():
    from tbm_process.evidence_graph.store import EvidenceGraph
    from tbm_process.evidence_graph.report_reliability import report_graph_view
    graph=EvidenceGraph()
    graph.add_node('GeologyFact','GF:a',{'document_id':'D','source':{},
        'original_description':'当前开挖段落DyK1017+176~DyK1016+208段岩体破碎。'})
    graph.add_node('GeologyFact','GF:b',{'document_id':'D','source':{},'original_description':'围岩等级Ⅳ。'})
    view,audit=report_graph_view(graph)
    assert len(audit)==1 and len(graph.nodes)==2 and not view.nodes


def test_window_choice_accounting_and_assignment():
    pack,plan,_=fixture()
    pack.update(window_selection_contract='v1',planning_candidates=[{'window_id':'W1'},{'window_id':'W2'}])
    plan['window_decisions']=[{'window_id':'W1','selected':True,'reason':'持续多参数变化'},
                              {'window_id':'W2','selected':False,'reason':'与已选窗口重复'}]
    p=plan['chapters'][1]['paragraphs'][0];p.update(section='2.1',window_ids=['W1'])
    assert not check_relation_planning(plan,pack)
    p['window_ids']=['W1','W2']
    assert 'selected_windows_not_assigned_once' in check_relation_planning(plan,pack)


def test_internal_identifiers_are_not_engineering_numbers_to_preserve():
    from tbm_process.evidence_graph.joint_reporting import retention_check
    pack,_,draft=fixture();pack['facts'][2]['writer_data']['numeric']=25
    old=deepcopy(draft);old['chapters'][2]['claims'][0]['text']='记录包括DOC:ac25ef，原记录数值25。'
    new=deepcopy(old);new['chapters'][2]['claims'][0]['text']='原记录数值25。'
    assert retention_check(old,new,{(2,0)},pack)['passed']
    new['chapters'][2]['claims'][0]['text']='原记录。'
    assert not retention_check(old,new,{(2,0)},pack)['passed']


def test_report_cutoff_context_is_not_a_source_observation_date():
    from tbm_process.evidence_graph.report_reliability import mask_report_cutoff
    assert mask_report_cutoff('截至2024年4月26日未取得资料。','2024-04-26')=='截至报告日未取得资料。'
    assert mask_report_cutoff('截至2024年4月27日','2024-04-26')=='截至2024年4月27日'
    assert mask_report_cutoff('2024年4月26日现场揭露','2024-04-26')=='2024年4月26日现场揭露'
    pack,plan,draft=fixture();draft['chapters'][2]['claims'][0]['text']='截至2024年4月26日未取得新增资料。'
    assert draft_quality(draft,plan,pack)['passed']
    draft['chapters'][2]['claims'][0]['text']='截至2024年4月27日未取得新增资料。'
    assert not draft_quality(draft,plan,pack)['passed']
