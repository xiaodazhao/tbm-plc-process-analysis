"""Synthetic interface/error injection fixtures, not engineering experiment evidence."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tbm_process.reporting_v2.performance import calculate_metrics, performance_windows
from tbm_process.reporting_v2.response_features import build_response_features, compare_responses, response_samples
from tbm_process.evidence_graph.joint_reporting import draft_quality, retention_check, ModelSession
from tbm_process.evidence_graph.relations import prohibited_assertions, check_relations
from tbm_process.evidence_graph.store import EvidenceGraph

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def config():
    return json.loads((ROOT/'tbm_process/reporting_v2/defaults.json').read_text())

@pytest.fixture
def policy():
    return json.loads((ROOT/'configs/plc_response_relations.json').read_text())

def signals(n=35):
    return pd.DataFrame({'time':pd.date_range('2024-04-26',periods=n,freq='20s'),
        'advance_speed':40.,'thrust':20000.,'torque':4000.,'rpm_canonical':5.,
        'experimental_phase_label':'stable','machine_state':'advancing',
        'advance_speed_quality':'usable','rpm_quality':'usable_provisional_mapping',
        'sample_dt_seconds':20.,'source_file':'fixture.csv','source_row':np.arange(n)+2,
        'experimental_platform_id':'X','experimental_phase_id':'XP','chainage':1015822.,'rpmsource':'resolved'})

def features(frame,config,identity='X'):
    return build_response_features(frame,config,json.loads((ROOT/'config.json').read_text()),
                                   response_id=identity,bounds=[1015822.,1015823.],day_resolution=1.)

def test_base_rpm_and_denominator_scopes(config):
    data=signals()
    data.loc[0,'rpm_canonical']=0
    data.loc[1,'rpmsource']='unresolved'
    data.loc[2,'advance_speed']=.6
    data.loc[2,'rpm_canonical']=8
    out,_=response_samples(data,config)
    assert out.basic_response_eligible.sum()==35
    assert out.rpm_response_eligible.sum()==33
    assert out.performance_eligible.sum()==32
    f=features(data,config)
    assert f['metrics']['thrust']['count']==35
    assert f['metrics']['pr']['count']==32
    assert f['metrics']['thrust']['supported_duration_seconds']==700
    assert len(f['metrics']['thrust']['source'][0]['source_rows'])==35

@pytest.mark.parametrize('column,value',[('mapping_core_positions_confirmed',False),('advance_speed_quality','bad'),('machine_state','stopped')])
def test_base_does_not_override_quality_or_existing_state(config,column,value):
    frame=signals()
    frame[column]=value
    result,_=response_samples(frame,config)
    assert not result.basic_response_eligible.any()

def test_default_windows_backward_compatible_and_no_file_bridge(config):
    frame,_=calculate_metrics(signals(),config)
    phase=json.loads((ROOT/'config.json').read_text())
    assert performance_windows(frame,config,phase)==performance_windows(frame,config,phase,
        eligibility_column='performance_eligible',parameters=('advance_speed','thrust','torque','pr'))
    frame.loc[9:,'source_file']='other_fixture.csv'
    f=features(frame,config)
    for w in f['windows']['basic']:
        assert len(w['source'])==1
    assert f['window_coverage']['basic']['undescribed_count']>0

def test_windows_never_bridge_platforms_and_short_no_invented_trend(config):
    frame=signals(18)
    frame.loc[9:,'experimental_platform_id']='Y'
    f=features(frame,config)
    assert f['windows']['basic']==[]
    assert f['window_coverage']['basic']['undescribed_count']==18
    assert not f['time_scope']['continuous']
    assert f['spatial_quality']['single_observed_position']

def test_descriptive_thrust_not_suppressed_by_matching_gate(config,policy):
    old=features(signals(),config,'old')
    frame=signals(); frame.time+=pd.Timedelta(hours=1); frame.thrust*=2
    current=features(frame,config,'new')
    comparison=compare_responses(current,old,config,policy)
    assert comparison['descriptive']['changes']['thrust']['percent']==100
    assert not comparison['controlled']['legacy_median_gates']['thrust']['passed']
    assert comparison['controlled']['status']=='not_established'
    assert len(comparison['sensitivity'])==3

def sample_pack():
    f={'fact_id':'x','chapter':1,'canonical_text':'今日进尺9.00米。','writer_data':{},'required':True,'nature':'PLC', 'graph_support_ids':[]}
    pack={'facts':[f],'writer_fact_ids':['x'],'program_relations':[],'semantic_relation_candidates':[]}
    plan={'chapters':[{'chapter_id':i,'fact_ids':['x'] if i==1 else []} for i in range(1,7)]}
    draft={'chapters':[{'chapter_id':i,'claims':[{'text':'今日进尺9.00米。','fact_ids':['x']}] if i==1 else []} for i in range(1,7)]}
    return pack,plan,draft

def test_chinese_metre_wrong_value_and_compound_cause_checked():
    pack,plan,draft=sample_pack()
    assert draft_quality(draft,plan,pack)['passed']
    draft['chapters'][0]['claims'][0]['text']='今日进尺90米，岩体破碎导致速度下降。'
    checked=draft_quality(draft,plan,pack)
    assert not checked['passed']
    assert any('unsupported_value_unit_pair' in e for e in checked['errors'])
    assert 'unsupported_causal_diagnostic_or_accuracy_assertion' in checked['errors']

def test_non_numeric_unverified_meaning_is_review_not_pass():
    pack,plan,draft=sample_pack()
    draft['chapters'][0]['claims'][0]['text']='今日进尺9.00米，参数十分优越。'
    q=draft_quality(draft,plan,pack)
    assert q['claim_checks'][0]['status']=='REVIEW'
    assert q['semantic_status']=='REVIEW_no_independent_human_gold_labels'

def test_only_explicit_percentage_not_normalized_change_is_reportable():
    pack,plan,draft=sample_pack()
    pack['facts'][0]['writer_data']={'comparison':{'unit':'mm/min','current':48.50659,
        'reference':36.655885,'percent':32.329610920592984},'relative_change':77.77}
    claim=draft['chapters'][0]['claims'][0]
    claim['text']='推进速度中位数相对参考值提高32.33%。'
    assert draft_quality(draft,plan,pack)['passed']
    claim['text']='推进速度提高77.77%。'
    assert not draft_quality(draft,plan,pack)['passed']

def test_local_revision_cannot_delete_supported_information():
    pack,_,before=sample_pack(); after=deepcopy(before)
    after['chapters'][0]['claims'][0]['text']='参数变化。'
    assert not retention_check(before,after,{(0,0)},pack)['passed']
    assert not retention_check(before,after,set(),pack)['passed']

@pytest.mark.parametrize('text,fail',[('岩体破碎导致速度下降',True),('不能据此认定岩体破碎导致速度下降',False),('未判断设备故障',False)])
def test_causal_expression_boundaries(text,fail):
    assert bool(prohibited_assertions(text))==fail

def test_offline_session_never_calls_api(tmp_path,policy,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('No API allowed in this test')
    monkeypatch.setattr('tbm_process.evidence_graph.joint_reporting.call_json_model',forbidden)
    session=ModelSession(tmp_path,policy)
    assert session.call({'model':'test'},tmp_path/'A','generation') is None
    assert not session.records

def test_hard_api_budget(tmp_path,policy,monkeypatch):
    policy['llm']['maximum_calls']=1
    calls=[]
    def fixture(*args,**kwargs):
        calls.append(kwargs)
        return {},{}, {'input_tokens':1,'output_tokens':1}
    monkeypatch.setattr('tbm_process.evidence_graph.joint_reporting.call_json_model',fixture)
    session=ModelSession(tmp_path,policy,'fixture_key_not_real')
    assert session.call({'model':'test'},tmp_path/'A','one')=={}
    assert session.call({'model':'test'},tmp_path/'A','two') is None
    assert len(calls)==1 and calls[0]['max_retries']==0

def test_relation_unknown_candidate_rejected(policy):
    payload={'relations':[{'candidate_id':'invented'}],'omitted':[]}
    result=check_relations(payload,{'candidates':[]},EvidenceGraph(),'2024-04-26',policy)
    assert not result['passed']

def test_negated_continuity_not_false_error():
    pack,plan,draft=sample_pack()
    pack['facts'][0]['writer_data']={'time_scope':{'continuous':False}}
    draft['chapters'][0]['claims'][0]['text']='今日进尺9.00米，不把时间跨度写成连续作业。'
    assert draft_quality(draft,plan,pack)['passed']
    draft['chapters'][0]['claims'][0]['text']='今日进尺9.00米，为连续作业。'
    assert not draft_quality(draft,plan,pack)['passed']

def relation_fixture():
    graph=EvidenceGraph()
    for index in (1,2):
        graph.add_node('GeologyDocument',f'd{index}',{'metadata':{
            'available_date_user_confirmed':True,'available_date':'2024-04-20',
            'chainage_prefix':'DyK','alignment':'right','availability_basis':'fixture'},
            'date_audit':{'status':'PASS'}})
        graph.add_node('GeologyFact',f'f{index}',{'document_id':f'd{index}',
            'information_role':'forecast','line':'right','chainage_system':'DyK','start_m':0.,'end_m':10.})
    candidate={'candidate_id':'c','source_fact_ids':['f1'],'target_fact_ids':['f2'],
        'supporting_evidence_ids':['f1','f2'],'semantic_kind':'geological_description_comparison',
        'evidence':[{'id':'f1','text':'岩体破碎'},{'id':'f2','text':'节理发育'}]}
    item={'candidate_id':'c','relation_type':'COMPARES','source_fact_ids':['f1'],'target_fact_ids':['f2'],
        'supporting_evidence_ids':['f1','f2'],'review_required':True,'report_relevance':'review',
        'limitations':['仅供比对，不能判断准确率'],'relation_description':'两份预测可并列描述',
        'semantic_basis':{'evidence_quotes':[{'evidence_id':'f1','quote':'岩体破碎'},
            {'evidence_id':'f2','quote':'节理发育'}],'explanation':'不同描述可供核查'}}
    return graph,{'candidates':[candidate]},{'relations':[item],'omitted':[]}

def test_relation_positive_is_review_not_geological_truth(policy):
    graph,pack,payload=relation_fixture()
    result=check_relations(payload,pack,graph,'2024-04-26',policy)
    assert result['passed']
    assert result['relations'][0]['status']=='STRUCTURE_PASS_SEMANTIC_REVIEW'

@pytest.mark.parametrize('mutation',['future','space','role','quote','cause_in_basis','unverified_truth','unknown_numeric','wrong_id'])
def test_relation_injections(policy,mutation):
    graph,pack,payload=relation_fixture()
    item=payload['relations'][0]
    if mutation=='future': graph.nodes['d1']['data']['metadata']['available_date']='2024-04-27'
    if mutation=='space': graph.nodes['f1']['data']['start_m']=20.
    if mutation=='role': graph.nodes['f1']['data']['information_role']='observed'
    if mutation=='quote': item['semantic_basis']['evidence_quotes'][0]['quote']='设备良好'
    if mutation=='cause_in_basis': item['semantic_basis']['explanation']='岩体破碎导致速度下降'
    if mutation=='unverified_truth': item['review_required']=False
    if mutation=='unknown_numeric': item['relation_description']='速度下降99米'
    if mutation=='wrong_id': item['target_fact_ids']=['f1']
    assert not check_relations(payload,pack,graph,'2024-04-26',policy)['passed']
