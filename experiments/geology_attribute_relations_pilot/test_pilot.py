from copy import deepcopy
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('attribute_relation_pilot', Path(__file__).with_name('pilot.py'))
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def unit(value, kind='rock_fragmentation', **kwargs):
    return {'unit_id': value, 'unit_type': kind, 'value': value, 'raw_expression': value,
            'comparison_scope': {'status': 'parent_interval'}, **kwargs}


def compare(kind, a, b):
    return pilot.compare_attribute(kind, a, b, {}, {})


def pair():
    side = {'fields': {'geology': {'fact_id': 'GF:real', 'text': '板岩夹变质砂岩，局部夹压碎岩，岩体较破碎-破碎。',
                                  'source': {'page': 3}}, 'grade': {'fact_id': 'GF:grade', 'text': 'Ⅳ', 'source': {'page': 3}}}}
    return {'tsp': deepcopy(side), 'hsp': deepcopy(side)}


def test_compound_range_retained_and_overlap():
    r = compare('rock_fragmentation', [unit('较破碎-破碎')], [unit('破碎')])
    assert r['relation'] == 'range_overlap'
    assert r['tsp_values'] == ['较破碎-破碎']


def test_missing_not_negative():
    r = compare('groundwater', [unit('滴渗水', 'groundwater')], [])
    assert r['relation'] == 'information_supplement'
    assert '不存在' in r['note']


def test_grade_difference_not_truth():
    assert compare('recommended_grade', [unit('Ⅳ')], [unit('Ⅴ')])['relation'] == 'explicit_difference'


def test_lithology_qualification_preserved():
    a = [unit('石炭二叠系板岩夹变质砂岩', 'lithology')]
    b = [unit('板岩夹变质砂岩', 'lithology'), unit('局部夹压碎岩', 'lithology', qualifiers=['局部'])]
    b[-1]['comparison_scope']['status'] = 'local_qualifier_position_unresolved'
    r = compare('lithology', a, b)
    assert r['relation'] == 'main_combination_agreement_with_supplement'
    assert '局部夹压碎岩' in r['hsp_values']
    assert r['scope_review_unit_ids']


def test_same_rocks_not_same_combination():
    assert compare('lithology', [unit('板岩夹变质砂岩')], [unit('变质砂岩夹板岩')])['relation'] == 'explicit_difference'


def test_stratigraphy_only_not_extra_rock():
    assert compare('lithology', [unit('石炭二叠系板岩夹变质砂岩')], [unit('板岩夹变质砂岩')])['relation'] == 'main_combination_agreement'


def test_locality_from_preceding_clause_is_review():
    text = '掌子面右侧节理裂隙发育密集，岩体破碎'
    u = unit('岩体破碎', original_text=text, span_in_original_text=[text.index('岩体'), len(text)])
    assert pilot.scope(u, [0, 1])['status'] == 'review_inherited_locality'


def test_reference_not_aligned():
    assert compare('relative_geology_change', [unit('较前段变差')], [unit('较前段变差')])['relation'] == 'insufficient_comparability'


def test_touching_boundary_not_overlap():
    assert pilot.intersection([1015764, 1015822], [1015822, 1015840]) is None


def test_local_range_outside_omitted():
    u = unit('其中DyK1013+130~DyK1013+137段有出水')
    assert pilot.scope(u, [1013120, 1013128])['status'] == 'outside_overlap'
    assert pilot.scope(u, [1013134, 1013140])['effective_ranges_m'] == [[1013134, 1013137]]


def test_unresolved_point_not_broadcast():
    assert pilot.scope(unit('DyK1015+822附近有出水风险'), [1015822, 1015827])['status'] == 'review_unresolved_point_or_local_geometry'


def test_injected_fault_and_bad_quote_detected():
    item = {'attribute': 'structure', 'relation': 'information_supplement', 'summary': '存在F36断层',
            'tsp': {'field': 'geology', 'quote': 'F36断层'}, 'hsp': {'field': 'geology', 'quote': ''}}
    result = pilot.check_comparison(pair(), item)
    assert result['program_status'] == 'FAIL'
    assert 'unsupported_fault_id:F36' in result['issues']


def test_source_quote_pass_not_semantic_gold():
    item = {'attribute': 'lithology', 'relation': 'literal_agreement', 'summary': '主体岩性相同',
            'tsp': {'field': 'geology', 'quote': '板岩夹变质砂岩'}, 'hsp': {'field': 'geology', 'quote': '板岩夹变质砂岩'}}
    r = pilot.check_comparison(pair(), item)
    assert r['program_status'] == 'PASS'
    assert r['semantic_status'].startswith('REVIEW')


def test_false_agreement_without_evidence_rejected():
    item = {'attribute': 'rock_hardness', 'relation': 'literal_agreement', 'summary': '相同',
            'tsp': {'field': 'geology', 'quote': ''}, 'hsp': {'field': 'geology', 'quote': ''}}
    assert 'two_sided_relation_missing_quote' in pilot.check_comparison(pair(), item)['issues']


def test_offline_prompt_has_no_rule_result():
    assert '虚构' in pilot.SYSTEM and '未提及不等于不存在' in pilot.SYSTEM
    assert 'rule_result' not in pilot.SYSTEM


def test_call_budget_uses_recorded_attempts(tmp_path, monkeypatch):
    pilot.save(tmp_path / 'samples.json', [{'pair_id': str(i)} for i in range(13)])
    pilot.save(tmp_path / 'config.json', {'samples_sha256': pilot.sha(tmp_path / 'samples.json')})
    pilot.save(tmp_path / 'api_calls.json', [{'batch': 1}, {'batch': 2}])
    monkeypatch.setattr(pilot, 'load_deepseek_api_key', lambda _: 'not-a-real-secret')
    monkeypatch.setattr(pilot, 'call_json_model', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must_not_call')))
    import pytest
    with pytest.raises(RuntimeError, match='two_call_hard_limit'):
        pilot.run_llm(tmp_path, tmp_path / '.env')


def test_literal_quotes_do_not_check_relation_meaning():
    item = {'attribute': 'rock_fragmentation', 'relation': 'explicit_difference', 'summary': '双方相同',
            'tsp': {'field': 'geology', 'quote': '岩体较破碎-破碎'}, 'hsp': {'field': 'geology', 'quote': '岩体较破碎-破碎'}}
    r = pilot.check_comparison(pair(), item)
    assert r['program_status'] == 'PASS'
    assert r['semantic_status'] == 'REVIEW_requires_human_judgment'
