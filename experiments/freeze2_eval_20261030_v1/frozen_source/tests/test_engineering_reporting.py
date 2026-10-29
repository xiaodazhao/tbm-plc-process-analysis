from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tbm_process.engineering_reporting.common import save, sha
from tbm_process.engineering_reporting.gas import supports, time_weighted_windows, good_samples, runs
from tbm_process.engineering_reporting.risks import locate
from tbm_process.engineering_reporting.schema import TITLES
from tbm_process.engineering_reporting.writer import request, validate_plan, validate_text


def small_pack():
    facts = [{'fact_id': 'F:'+sid, 'section': sid, 'canonical_text': '真实数值10，研究参考。',
              'data': {}, 'sources': [], 'required': True, 'role': 'computed'} for sid in TITLES]
    return {'date': '2024-01-01', 'facts': facts, 'policy': {},
            'coverage': {'ahead': {'forecast_union_m': 0, 'forecast_uncovered_m': 20}}}


def small_plan(pack):
    return {'sections': [{'section_id': sid, 'paragraphs': [{'purpose': '说明', 'fact_ids': ['F:'+sid]}]}
                         for sid in TITLES], 'omissions': []}


def small_draft(pack):
    return {'sections': [{'section_id': sid, 'paragraphs': [{'text': '真实数值10。', 'fact_ids': ['F:'+sid]}]}
                         for sid in TITLES]}


def test_support_preserves_real_gap_and_last_zero():
    t = pd.Series(pd.to_datetime(['2024-01-01 00:00:00', '2024-01-01 00:00:20', '2024-01-01 00:03:00']))
    assert supports(t, 60).tolist() == [20, 0, 0]


def test_window_does_not_use_future_endpoint():
    t = pd.Series(pd.date_range('2024-01-01', periods=5, freq='10s'))
    mean, coverage = time_weighted_windows(t, [1, 1, 1, 1, 999], [10, 10, 10, 10, 0], 40)
    assert mean[-1] == 1
    assert coverage[-1] == 1
    assert np.isnan(mean[0])


def test_window_rejects_missing_support():
    t = pd.Series(pd.date_range('2024-01-01', periods=5, freq='10s'))
    mean, _ = time_weighted_windows(t, [1, np.nan, 1, 1, 1], [10, 10, 10, 10, 0], 40)
    assert np.isnan(mean[-1])


def test_zero_retained_unresolved_and_negative_rejected():
    frame = pd.DataFrame({'X': [0, 3, -1], 'time': pd.date_range('2024-01-01', periods=3, freq='20s'),
        'mapping_gas_status': ['structural_and_step_supported_units_unconfirmed', 'unresolved', 'structural_and_step_supported_units_unconfirmed'],
        'quality_duplicate_timestamp': False, 'quality_backward_timestamp': False})
    _, valid = good_samples(frame, 'X')
    assert valid.tolist() == [True, False, False]


def test_events_split_at_hard_gap():
    assert runs([True, True, True], pd.Series([20, 0, 0])) == [(0, 1), (2, 2)]


@pytest.mark.parametrize('point,expected', [(10, [10, 10]), (20, None), (9, None)])
def test_risk_point_half_open(point, expected):
    assert locate([10, 20], {'point_m': point}) == expected


def test_endpoint_touch_does_not_assign_parent_risk():
    assert locate([20, 30], {'point_m': None, 'range_m': [10, 20]}) is None


def test_plan_checks_missing_and_invented_ids():
    pack = small_pack(); plan = small_plan(pack)
    assert validate_plan(plan, pack)['passed']
    plan['sections'][0]['paragraphs'][0]['fact_ids'] = ['invented']
    assert not validate_plan(plan, pack)['passed']


def test_plan_cannot_omit_required_gas_fact():
    pack = small_pack(); plan = small_plan(pack)
    next(s for s in plan['sections'] if s['section_id'] == '2.3')['paragraphs'] = []
    plan['omissions'] = [{'fact_id': 'F:2.3', 'reason': 'too long'}]
    assert not validate_plan(plan, pack)['passed']


def test_body_numeric_injection_is_rejected():
    pack = small_pack(); plan = small_plan(pack); draft = small_draft(pack)
    assert validate_text(draft, pack, plan)['passed']
    draft['sections'][0]['paragraphs'][0]['text'] = '真实数值999。'
    assert not validate_text(draft, pack, plan)['passed']


def test_body_pass_is_never_semantic_certification():
    pack = small_pack(); result = validate_text(small_draft(pack), pack, small_plan(pack))
    assert result['passed'] and result['not_engineering_fact_accuracy']
    assert all(r['rule_status'] == 'checks_passed_semantics_REVIEW' for r in result['paragraphs'])


def test_one_request_contains_all_sections_and_new_sources():
    pack = small_pack()
    pack['facts'] += [{'fact_id': key, 'section': sid, 'canonical_text': '证据', 'required': True, 'role': 'reference'}
                     for key, sid in [('GAS_EVENT:1', '2.3'), ('SOURCE_RISK:1', '3.3'), ('RISK_REFERENCE:1', '3.3')]]
    payload = json.loads(request('plan', pack)['messages'][1]['content'])
    assert {f['fact_id'] for f in payload['facts']} >= {'GAS_EVENT:1', 'SOURCE_RISK:1', 'RISK_REFERENCE:1'}
    assert list(payload['sections']) == list(TITLES)
    assert '3.4' not in request('plan', pack)['messages'][0]['content']


def test_generation_uses_same_unified_pool_and_plan():
    pack = small_pack(); plan = small_plan(pack)
    data = json.loads(request('write', pack, plan)['messages'][1]['content'])
    assert data['approved_plan'] == plan
    assert len(data['facts']) == len(pack['facts'])


def test_resume_blocks_input_mutation_before_any_API(tmp_path):
    from tbm_process.engineering_reporting.pipeline import generate
    save(tmp_path/'unified_report_input.json', small_pack())
    save(tmp_path/'preflight.json', {'passed': True, 'input_sha256': 'different'})
    with pytest.raises(ValueError, match='input_changed'):
        generate(tmp_path, 'unit-test-not-a-real-key', 'test-model')
    assert not (tmp_path/'model_calls.json').exists()


def test_resume_does_not_overwrite_existing_model_run(tmp_path):
    from tbm_process.engineering_reporting.pipeline import generate
    save(tmp_path/'unified_report_input.json', small_pack())
    save(tmp_path/'preflight.json', {'passed': True, 'input_sha256': sha(tmp_path/'unified_report_input.json')})
    save(tmp_path/'model_calls.json', [])
    with pytest.raises(ValueError, match='existing_model_run'):
        generate(tmp_path, 'unit-test-not-a-real-key', 'test-model')


def test_production_modules_have_no_pilot_or_finished_report_dependency():
    root = Path(__file__).resolve().parents[1]/'tbm_process/engineering_reporting'
    for path in root.glob('*.py'):
        source = path.read_text()
        assert 'Desktop/13' not in source
        assert 'from integration import' not in source
        assert 'tbm_risk_tools' not in source
        assert 'content_convergence_20261029' not in source


@pytest.mark.parametrize('allowed,start,end,expected', [
    (1,10,20,True), (0,10,20,False), (1,None,None,False), (1,10,21,False),
])
def test_existing_PLC_spatial_gate_is_not_bypassed(tmp_path, allowed, start, end, expected):
    import sqlite3
    from tbm_process.engineering_reporting.pipeline import checked_plc_geometry
    path = tmp_path/'geology.sqlite'; con=sqlite3.connect(path)
    con.execute('CREATE TABLE plc_days(date TEXT,start_m REAL,end_m REAL,spatial_allowed INTEGER)')
    con.execute('INSERT INTO plc_days VALUES(?,?,?,?)', ('2024-01-01',start,end,allowed))
    con.commit();con.close()
    advance={'start_value_m':10.,'end_value_m':20.}
    if expected:
        assert checked_plc_geometry(path,'2024-01-01',advance)['spatial_allowed']
    else:
        with pytest.raises(ValueError):checked_plc_geometry(path,'2024-01-01',advance)
