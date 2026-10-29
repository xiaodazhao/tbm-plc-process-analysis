from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from tbm_process.engineering_reporting.common import load
from tbm_process.engineering_reporting.progress_investigation import (
    apply_report_decisions, case_text, enrich_progress, investigate_progress,
    investigation_claim_errors, reading_quantity_supported,
)
from tbm_process.engineering_reporting.writer import request, validate_text


def signal(values=None, state='advancing'):
    n = len(values) if values is not None else 82
    v = values if values is not None else [100.]*61+[102.]*21
    return pd.DataFrame({'time': pd.date_range('2024-01-01', periods=n, freq='20s'),
        'source_file': 'test.csv', 'source_row': np.arange(n)+2, 'cumulative_advance': v,
        'chainage': np.where(np.arange(n)<61, 1000., 1002.),
        'chainage_quality': 'usable',
        'advance_speed': 50., 'advance_speed_quality': 'usable', 'machine_state': state,
        'experimental_platform_id': ['p1' if i<61 else 'p2' for i in range(n)],
        'sample_dt_seconds': [20.]*(n-1)+[0.], 'hard_gap_before': False})


def analyze(frame):
    return investigate_progress(frame, day='2024-01-01')


def activity_bundle():
    groups = [{'activity_id': 'ACTIVITY:001', 'platform_ids': ['p1'], 'selected': True,
        'start_time': '2024-01-01T00:00:00', 'end_time': '2024-01-01T00:20:00', 'advancing_display': '20分00秒',
        'progress_m': 0., 'progress_status': 'adoptable', 'rate_m_per_advancing_hour': 0., 'rate_status': 'usable_descriptive'}]
    return {'daily': {'advance_m': 2., 'advancing_seconds': 1200}, 'activities': groups,
            'selected_activities': deepcopy(groups), 'reconciliation': {'all_platform_internal_progress_sum_m': 0.}}


def fact(case):
    return {'fact_id': case['investigation_id'], 'section': '2.2', 'canonical_text': case_text(case),
        'data': case, 'role': 'data_consistency_investigation_not_confirmed_cause', 'required': True, 'sources': []}


def test_plateau_plus_cojump_is_candidate_not_confirmed_cause():
    result = analyze(signal())
    assert len(result['cases']) == 1
    c = result['cases'][0]
    assert c['plateau']['advancing_supported_seconds'] == 1200
    assert c['transition']['delta_m'] == 2
    assert c['hypotheses'][0]['status'] == 'candidate_with_temporal_evidence'
    assert c['review_status'] == 'cause_requires_external_confirmation'
    assert not c['decision']['repair_or_allocate_distance']
    assert 'not independent' in c['evidence_dependence']


def test_no_jump_plateau_is_still_found_and_zero_progress_is_withheld():
    frame = signal([100.]*82)
    result = analyze(frame)
    assert len(result['cases']) == 1 and result['cases'][0]['kind']=='unchanged_reading_during_advancing'
    original = activity_bundle(); before = deepcopy(original)
    revised = apply_report_decisions(original, result)
    assert original == before
    assert revised['activities'][0]['progress_m'] is None
    assert revised['activities'][0]['pre_investigation']['progress_m'] == 0.
    assert revised['daily'] == before['daily']


def test_normal_stopping_with_constant_counter_is_not_flagged():
    assert not analyze(signal([100.]*82, state='stopped'))['cases']


def test_gradual_valid_progress_has_no_candidate():
    assert not analyze(signal(np.arange(82)*.01+100))['cases']


def test_short_quantized_plateaus_are_not_flagged():
    frame = signal(np.repeat(np.arange(21), 4)*.0625+100)
    assert not analyze(frame)['cases']


def test_speed_support_and_quality_are_required():
    frame = signal([100.]*82)
    frame['advance_speed_quality'] = 'suspect'
    assert not analyze(frame)['cases']
    frame['advance_speed_quality'] = 'usable'; frame['advance_speed'] = np.nan
    assert not analyze(frame)['cases']
    frame['advance_speed'] = 0.
    assert not analyze(frame)['cases']


def test_missing_values_do_not_bridge_a_plateau_or_jump():
    frame = signal([100.]*20+[np.nan]+[102.]*20)
    assert not analyze(frame)['cases']


def test_hard_gap_prevents_delayed_update_explanation():
    frame = signal()
    frame.loc[61:, 'time'] += pd.Timedelta(minutes=5)
    frame.loc[60, 'sample_dt_seconds'] = 0.
    frame.loc[61, 'hard_gap_before'] = True
    result = analyze(frame)
    jump = next(c for c in result['cases'] if c['kind']=='forward_jump')
    assert not jump['transition']['continuous_supported_pair']
    assert jump['hypotheses'][0]['status']=='insufficient_evidence'
    assert '未观测时间' in case_text(jump)


def test_source_boundary_never_becomes_corroborating_continuous_update():
    frame = signal(); frame.loc[61:, 'source_file'] = 'other.csv'
    c = next(c for c in analyze(frame)['cases'] if c['kind']=='forward_jump')
    assert not c['transition']['continuous_supported_pair']


def test_no_coshift_means_delay_not_established():
    frame = signal(); frame['chainage'] = 1000.
    c = analyze(frame)['cases'][0]
    assert c['hypotheses'][0]['status']=='insufficient_evidence'


def test_unusable_or_missing_position_quality_cannot_support_delay_candidate():
    frame = signal(); frame['chainage_quality'] = 'suspect'
    assert analyze(frame)['cases'][0]['hypotheses'][0]['status']=='insufficient_evidence'
    assert analyze(frame.drop(columns='chainage_quality'))['cases'][0]['hypotheses'][0]['status']=='insufficient_evidence'


def test_short_tail_in_previous_platform_does_not_withhold_that_platform():
    frame = signal()
    frame.loc[:1, 'experimental_platform_id'] = 'previous'
    c = analyze(frame)['cases'][0]
    assert 'previous' in c['platform_ids']
    assert 'previous' not in c['affected_platform_ids']
    assert 'p1' in c['affected_platform_ids']


def test_jump_across_platform_boundary_is_not_assigned_to_either_platform():
    frame = signal([100.]*20+[102.]*20)
    frame.loc[:19, 'experimental_platform_id'] = 'p1'
    frame.loc[20:, 'experimental_platform_id'] = 'p2'
    c = analyze(frame)['cases'][0]
    assert c['affected_platform_ids'] == []


def test_actual_support_not_nominal_twenty_seconds():
    frame = signal([100.]*82)
    frame['time'] = pd.date_range('2024-01-01', periods=len(frame), freq='10s')
    frame['sample_dt_seconds'] = [10.]*(len(frame)-1)+[0.]
    assert analyze(frame)['cases'][0]['plateau']['advancing_supported_seconds'] == 810


@pytest.mark.parametrize('mutation', ['duplicate_time', 'reverse_time', 'duplicate_row', 'negative_support'])
def test_bad_input_fails_explicitly(mutation):
    frame = signal()
    if mutation=='duplicate_time': frame.loc[1, 'time'] = frame.loc[0, 'time']
    elif mutation=='reverse_time': frame = frame.iloc[::-1]
    elif mutation=='duplicate_row': frame.loc[1, 'source_row'] = frame.loc[0, 'source_row']
    else: frame.loc[1, 'sample_dt_seconds'] = -20.
    with pytest.raises(ValueError): analyze(frame)


def test_negative_counter_step_is_preserved_as_measurement_issue():
    frame = signal([100.]*20+[98.]*20)
    c = analyze(frame)['cases'][0]
    assert c['kind']=='backstep_or_reset' and c['transition']['delta_m']==-2.
    assert c['hypotheses'][0]['status']=='insufficient_evidence'


def test_daily_source_platforms_and_baseline_are_not_rewritten(tmp_path):
    frame = signal(); original = activity_bundle(); before = deepcopy(original)
    pack = {'date': '2024-01-01', 'policy': {}, 'facts': [{'fact_id': 'ACTIVITY:001', 'section': '2.2',
        'data': original['activities'][0], 'canonical_text': '原事实', 'sources': [], 'required': True, 'role': 'computed'}]}
    revised, result = enrich_progress(pack, original, frame, tmp_path)
    assert original == before
    assert load(tmp_path/'activity_facts.before_investigation.json') == original
    assert revised['activities'][0]['progress_m'] is None
    assert result['cases'][0]['source_records'][0]['source_row']==2
    assert pack['facts'][-1]['required']
    assert pack['facts'][-1]['sources'][0]['sha256']
    payload = json.loads(request('plan', pack)['messages'][1]['content'])
    assert any(f['role']=='data_consistency_investigation_not_confirmed_cause' for f in payload['facts'])


@pytest.mark.parametrize('text,valid', [
    ('这些读数可能延迟更新，原因待核实。', True),
    ('这些读数存在延迟更新。', False),
    ('这些读数可能由通信故障造成。', False),
    ('尚不能确认通信故障。', True),
    ('已经核查，已分摊突跳距离。', False),
])
def test_explanation_error_injection_guards(text, valid):
    supported = [fact(analyze(signal())['cases'][0])]
    assert (not investigation_claim_errors(text, supported, True)) == valid


def test_optional_hypothesis_cannot_be_added_without_evidence():
    assert investigation_claim_errors('可能延迟更新。', [], True)
    c = analyze(signal([100.]*82))['cases'][0]
    assert not investigation_claim_errors('尚不足以确认延迟更新。', [fact(c)], True)


def test_counter_quantity_is_not_misclassified_as_adopted_interval_progress():
    f = fact(analyze(signal())['cases'][0])
    pack = {'progress_investigation': {'version': 'v1'}, 'facts': [f]}
    plan = {'sections': [{'section_id': '2.2', 'paragraphs': [{'fact_ids': [f['fact_id']]}]}]}
    draft = {'sections': [{'section_id': '2.2', 'paragraphs': [{'fact_ids': [f['fact_id']],
        'text': '开累进尺读数由100.000m变为102.000m，突跳2.000m，可能延迟更新。'}]}]}
    assert validate_text(draft, pack, plan, section_ids=['2.2'])['passed']
    draft['sections'][0]['paragraphs'][0]['text'] = '该段实际进尺2.000m。'
    assert not validate_text(draft, pack, plan, section_ids=['2.2'])['passed']
    assert not reading_quantity_supported('开累进尺读数变化', 999, 'm', [f])
    assert not reading_quantity_supported('开累进尺读数变化', 2, 'm/h', [f])


def test_investigation_is_deterministic_and_does_not_modify_samples():
    frame = signal(); before = frame.copy(deep=True)
    assert analyze(frame) == analyze(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_future_or_wrong_date_records_are_not_silently_admitted():
    with pytest.raises(ValueError, match='outside_report_date'):
        investigate_progress(signal(), day='2023-12-31')
