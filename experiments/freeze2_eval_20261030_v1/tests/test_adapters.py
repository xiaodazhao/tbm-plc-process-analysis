from copy import deepcopy
import importlib
from pathlib import Path
import sys

import pytest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
import common
import adapters
from tbm_process.engineering_reporting import writer
from tbm_process.engineering_reporting.common import save,sha,load
from tbm_process.engineering_reporting.schema import TITLES


def fixture():
    facts=[{'fact_id':sid,'section':sid,'canonical_text':'已有事实1。','data':{},
            'required':True,'role':'computed','sources':[]} for sid in TITLES]
    return {'date':'2024-01-01','facts':facts,'sections':TITLES,'policy':{},
            'coverage':{'ahead':{'forecast_union_m':1}}}


def test_A_B_C_receive_identical_fact_views_but_different_planning():
    pack=fixture();plan=adapters.fixed_plan(pack)
    a=adapters.direct_request(pack);b=writer.request('write',pack,plan=plan);c=writer.request('plan',pack)
    payload=[common.json.loads(r['messages'][1]['content']) for r in [a,b,c]]
    assert payload[0]['facts']==payload[1]['facts']==payload[2]['facts']
    assert 'approved_plan' not in payload[0]
    assert payload[1]['approved_plan']==plan
    assert writer.validate_plan(plan,pack)['passed']


def test_rule_plan_does_not_call_a_model_and_selects_fixed_optional_budget(monkeypatch):
    pack=fixture()
    for i in range(4):pack['facts'].append({'fact_id':'O'+str(i),'section':'1','canonical_text':'可选'+str(i),
        'data':{},'required':False,'role':'computed','sources':[]})
    monkeypatch.setattr(adapters,'call',lambda *args:pytest.fail('rule plan called API'))
    plan=adapters.fixed_plan(pack)
    ids={i for s in plan['sections'] for p in s['paragraphs'] for i in p['fact_ids']}
    assert {'O0','O1'}<=ids and 'O2' not in ids and 'O3' not in ids


def test_D_uses_identical_C_plan_and_draft_and_only_adds_editor(tmp_path,monkeypatch):
    day='2024-01-01';source=tmp_path/'inputs'/day;source.mkdir(parents=True)
    pack=fixture();plan=adapters.fixed_plan(pack)
    draft={'sections':[{'section_id':s,'paragraphs':[{'text':'已有事实1。','fact_ids':[s]}]} for s in TITLES]}
    save(source/'unified_report_input.json',pack)
    save(source/'preflight.json',{'passed':True,'input_sha256':sha(source/'unified_report_input.json')})
    save(source/'preparation.result.json',{'status':'PREPARED','input_sha256':sha(source/'unified_report_input.json')})
    save(tmp_path/'prepared_requests'/day/'request_status.json',{'status':'REQUESTS_PREPARED'})
    monkeypatch.setattr(adapters,'HERE',tmp_path)
    stages=[]
    def fake(scope,date,arm,stage,request,folder):
        stages.append((arm,stage))
        return deepcopy(plan if stage=='plan' else draft)
    monkeypatch.setattr(adapters,'call',fake)
    monkeypatch.setattr(adapters,'render',lambda folder:{'PDF_generated':False,'unit_test_skipped_real_renderer':True})
    adapters.run_date(day,'interface')
    c=tmp_path/'runs/interface/C'/day;d=tmp_path/'runs/interface/D'/day
    shared=load(d/'shared_C_provenance.json')
    assert shared['draft_sha256']==sha(c/'final_narrative.json')
    assert shared['plan_sha256']==sha(c/'content_plan.json')
    assert [stage for arm,stage in stages if arm=='D']==['editor']
    assert load(d/'narrative.initial.json')==load(c/'final_narrative.json')
    assert load(d/'result.json')['C_initial_draft_reused']


def test_wrong_source_or_quantity_still_not_silently_accepted():
    pack=fixture();plan=adapters.fixed_plan(pack)
    draft={'sections':[{'section_id':s,'paragraphs':[{'text':'已有事实999。','fact_ids':[s]}]} for s in TITLES]}
    assert not adapters.validate(draft,pack,plan)['passed']


def test_combined_budget_is_not_auto_increased(tmp_path,monkeypatch):
    monkeypatch.setattr(common,'HERE',tmp_path)
    budget=common.Calls('formal')
    for i in range(180):budget.reserve('day','A',str(i),tmp_path,{'model':'test'})
    with pytest.raises(common.BudgetExceeded):
        common.Calls('relations').reserve('day','REL+','extra',tmp_path,{'model':'test'})
