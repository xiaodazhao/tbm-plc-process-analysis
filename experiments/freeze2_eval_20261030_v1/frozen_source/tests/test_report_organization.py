from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from tbm_process.engineering_reporting.common import ROOT, load, save
from tbm_process.engineering_reporting.organization import coverage_accounting, operating_comparison, enrich_organization, writing_view, style
from tbm_process.engineering_reporting.writer import request, model_facts, validate_text


def test_coverage_is_calendar_day_complement_not_stopped_time():
    frame = pd.DataFrame({'time': pd.to_datetime(['2024-01-01 00:00:10', '2024-01-01 01:00:10']),
        'sample_dt_seconds': [20., 0.]})
    result = coverage_accounting(frame, {'effective_state_duration_seconds': 20.}, '2024-01-01')
    assert result['supported_seconds'] == 20
    assert result['unsupported_seconds'] == 86380
    assert not result['unobserved_time_allocated_to_stopped']
    assert sum(r['seconds'] for r in result['uncovered_intervals']) == 86380


def test_hard_gap_and_edges_reconcile_exactly():
    frame = pd.DataFrame({'time': pd.to_datetime(['2024-01-01 00:00:10', '2024-01-01 12:00:00', '2024-01-01 13:00:00']),
        'sample_dt_seconds': [43190., 0., 39580.]})
    result = coverage_accounting(frame, {'effective_state_duration_seconds': 82770.}, '2024-01-01')
    assert result['largest_internal_gap']['seconds'] == 3600
    assert result['day_edge_seconds'] == 30
    assert result['unsupported_seconds'] == 3630


@pytest.mark.parametrize('mutation', ['overlap', 'wrong_total', 'wrong_day', 'negative'])
def test_inconsistent_coverage_is_not_published(mutation):
    frame = pd.DataFrame({'time': pd.to_datetime(['2024-01-01 00:00:00', '2024-01-01 00:00:20']), 'sample_dt_seconds': [20., 0.]})
    total = 20.
    if mutation=='overlap': frame.loc[0, 'sample_dt_seconds'] = 30.
    elif mutation=='wrong_total': total = 999.
    elif mutation=='wrong_day': frame.loc[0, 'time'] = pd.Timestamp('2023-12-31')
    else: frame.loc[0, 'sample_dt_seconds'] = -20.
    with pytest.raises(ValueError): coverage_accounting(frame, {'effective_state_duration_seconds': total}, '2024-01-01')


def operating_case():
    count = 20
    frame = pd.DataFrame({'time': list(pd.date_range('2024-01-01', periods=count, freq='20s'))+
        list(pd.date_range('2024-01-01 02:00', periods=count, freq='20s')),
        'source_file': 'unit.csv', 'source_row': np.arange(2*count)+2,
        'advance_speed': [40.]*count+[30.]*count, 'thrust': [1000.]*count+[1100.]*count,
        'torque': [100.]*count+[102.]*count, 'rpm_canonical': 5., 'rpm_quality': 'usable_provisional_mapping',
        'advance_speed_quality': 'usable', 'machine_state': 'advancing', 'experimental_phase_label': 'stable',
        'experimental_platform_id': ['a']*count+['b']*count, 'sample_dt_seconds': 20.,
        'chainage': list(np.linspace(100,102,count))+list(np.linspace(110,112,count)), 'chainage_quality': 'usable'})
    activities = {'selected_activities': [{'activity_id': pid, 'platform_ids': [pid],
        'start_time': str(g.time.iloc[0]), 'end_time': str(g.time.iloc[-1])} for pid,g in frame.groupby('experimental_platform_id')]}
    pack = {'facts': [{'fact_id': 'geo', 'section': '3.1', 'role': 'prior_forecast', 'data': {'original_range_m': [90.,120.]}}]}
    return frame, activities, {'config': load(ROOT/'tbm_process/reporting_v2/defaults.json')}, pack


def test_same_forecast_different_machine_response_is_descriptive_not_causal():
    frame, activities, package, pack = operating_case()
    before = frame.copy(deep=True)
    result = operating_comparison(frame, activities, package, pack)
    c = result['comparison']
    assert c and c['shared_geology_fact_ids'] == ['geo']
    assert c['changes']['advance_speed']['percent'] == -25
    assert c['changes']['thrust']['percent'] == 10
    assert c['changes']['torque']['percent'] == 2
    assert not c['same_conditions_confirmed'] and not result['geological_change_inferred']
    assert c['comparability']['status'] == 'not_established'
    pd.testing.assert_frame_equal(frame, before)


def test_insufficient_geology_or_spatial_support_does_not_invent_comparison():
    frame, activities, package, pack = operating_case()
    pack['facts'] = []
    assert operating_comparison(frame, activities, package, pack)['comparison'] is None
    frame.loc[frame.experimental_platform_id.eq('a'), 'chainage'] = 100.
    assert operating_comparison(frame, activities, package, pack)['comparison'] is None


def test_investigated_position_platform_is_excluded_from_spatial_comparison():
    frame, activities, package, pack = operating_case()
    pack['facts'].append({'section': '2.2', 'role': 'data_consistency_investigation_not_confirmed_cause', 'data': {'affected_platform_ids': ['a']}})
    result = operating_comparison(frame, activities, package, pack)
    assert result['comparison'] is None and result['excluded_investigated_platforms'] == ['a']


def test_overlapping_observed_positions_are_not_disjoint_comparison():
    frame, activities, package, pack = operating_case()
    frame.loc[frame.experimental_platform_id.eq('b'), 'chainage'] = np.linspace(101,103,20)
    assert operating_comparison(frame, activities, package, pack)['comparison'] is None


def test_organization_style_reaches_real_planning_request():
    pack = {'date': '2024-01-01', 'policy': {}, 'facts': [], 'report_organization': {'version': 'test'}}
    system = request('plan', pack)['messages'][0]['content']
    assert style() in system and 'COVERAGE_QUALITY' in system and 'WITHIN_BACKGROUND_RESPONSE' in system


@pytest.mark.parametrize('identical', [True, False])
def test_efficiency_is_required_once_only_when_facts_are_identical(tmp_path, identical):
    frame, activities, package, _ = operating_case()
    save(tmp_path/'activity_facts.json', activities)
    package.update(daily_overview={'effective_state_duration_seconds': 800.},
        source={'samples': 'unit_fixture_only.csv', 'samples_sha256': 'fixture'})
    rate = {'fact_id': 'DAY_RATE', 'section': '2.1', 'canonical_text': '推进效率2.5m/h',
        'data': {'rate': 2.5}, 'required': True, 'role': 'computed', 'sources': []}
    overview = deepcopy(rate); overview.update(fact_id='OVERVIEW_EFFICIENCY', section='1')
    if not identical: overview['data']['rate'] = 9.
    result = enrich_organization({'date': '2024-01-01', 'facts': [rate, overview]}, package, frame, tmp_path)
    assert rate['required'] == (not identical)
    assert overview['required']
    assert bool(result['report_organization']['redundant_required_facts']) == identical


def test_concise_view_keeps_real_risk_label_but_not_repeated_disclaimer():
    f={'fact_id':'risk', 'role':'original_source_risk_not_event', 'data':{},
       'canonical_text':'DyK1015+251原预报风险栏为绿色。颜色是来源原标记，不转换为当天安全等级。'}
    text=writing_view(f, {'facts':[f]})
    assert 'DyK1015+251' in text and '绿色' in text and '不转换' not in text
    assert '不转换' in f['canonical_text']


def test_writing_view_numeric_guard_rejects_new_value(monkeypatch):
    import tbm_process.engineering_reporting.organization as module
    f={'fact_id':'x','section':'1','canonical_text':'真实数值1','role':'computed','required':True,'data':{}}
    monkeypatch.setattr(module,'writing_view',lambda fact,pack:'新数值999')
    with pytest.raises(ValueError,match='unsupported_number'):
        model_facts({'facts':[f],'report_organization':{'version':'test'}})


def test_table_coverage_exception_is_in_the_real_model_instruction():
    p={'date':'2024-01-01','facts':[],'policy':{},'report_organization':{'version':'test'}}
    assert 'ACTIVITY' in request('write',p,plan={'sections':[]})['messages'][0]['content']
    assert '正文不必返回' in request('write',p,plan={'sections':[]})['messages'][0]['content']


def test_bare_median_qualifier_is_review_advice_not_release_failure():
    f={'fact_id':'x','section':'2.1','canonical_text':'推进速度中位数45.38mm/min',
       'role':'computed','required':True,'data':{}}
    p={'facts':[f],'report_organization':{'version':'test'}}
    plan={'sections':[{'section_id':'2.1','paragraphs':[{'fact_ids':['x']}]}]}
    draft={'sections':[{'section_id':'2.1','paragraphs':[{'fact_ids':['x'],'text':'推进速度45.38mm/min，为中等速度。'}]}]}
    result=validate_text(draft,p,plan,section_ids=['2.1'])
    assert result['passed']
    assert any('qualitative_comparison_reference' in r for r in result['review_required'])
