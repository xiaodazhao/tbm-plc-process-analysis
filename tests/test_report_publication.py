from copy import deepcopy
from pathlib import Path
import json

import pandas as pd
import pytest

from tbm_process.reporting_v2.activity_facts import build_activity_facts
from tbm_process.reporting_v2.publication import build_input, displayed_activity, validate_reply, combine, normalize_reply


ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def activity_case():
    config=json.loads((ROOT/'tbm_process/reporting_v2/defaults.json').read_text())
    frame=pd.DataFrame({'time':pd.date_range('2024-01-01',periods=8,freq='20s'),
        'source_file':['day.csv']*8,'source_row':range(1,9),'cumulative_advance':[10,10,10.1,10.6,20.6,20.6,20.6,21.1],
        'experimental_platform_id':['','','P1','P1','','','P2','P2'],
        'sample_dt_seconds':[10,20,10,30,20,30,15,25],
        'machine_state':['stopped','stopped','advancing','advancing','stopped','stopped','advancing','advancing'],
        'experimental_phase_label':['stable']*8,'advance_speed':[20.]*8,'advance_speed_quality':['usable']*8,
        'rpm_canonical':[5.]*8,'rpm_quality':['usable_provisional_mapping']*8,'thrust':[20000.]*8,'torque':[3000.]*8})
    platforms=[];groups=[]
    for pid,start,stop in [('P1',2,3),('P2',6,7)]:
        q={'source_original_field':'开累进尺','unit':'m','use_status':'adoptable','net_change_m':.5,
           'first_source_row':start+1,'last_source_row':stop+1,'quality_reasons':[]}
        platforms.append({'platform_id':pid,'interval_advance':q})
        groups.append({'platform_ids':[pid],'start_time':frame.time[start].isoformat(),'end_time':frame.time[stop].isoformat(),'advancing_seconds':40.})
    overview={'advancing_seconds':80.,'effective_state_duration_seconds':160.,'daily_comparison_allowed':True,
              'daily_advance':{'net_change_m':2.,'source_original_field':'导向盾首里程'}}
    stats={k:{'median':1.,'p10':.5,'p90':2.,'unit':unit} for k,unit in [('advance_speed','mm/min'),('thrust','kN'),('torque','kN·m')]}
    package={'date':'2024-01-01','config':config,'platforms':platforms,'daily_overview':overview,'source':{}}
    pack={'process_summary':{'groups':groups,'selected':groups},'response_analysis':{'selected_time_windows':[]},
          'facts':[{'fact_id':'PARAMETER_ANALYSIS_SUMMARY','data':{'statistics':stats}}]}
    return package,pack,frame


def test_rates_reuse_membership_and_observed_durations_not_elapsed(activity_case):
    package,pack,frame=activity_case;a=build_activity_facts(package,pack,frame)
    assert a['daily']['rate_m_per_advancing_hour']==90.
    assert a['daily']['advancing_share_percent']==50.
    assert a['activities'][0]['rate_m_per_advancing_hour']==45.
    assert a['activities'][0]['advancing_seconds']==40.
    assert a['activities'][0]['elapsed_span_seconds']==20.
    assert a['reconciliation']['all_platform_internal_progress_sum_m']==1.
    assert a['reconciliation']['daily_guide_shield_advance_m']==2.
    assert a['reconciliation']['must_not_force_equality']


def test_boundary_jump_is_evidence_only_not_assigned(activity_case):
    a=build_activity_facts(*activity_case)
    component=a['activities'][0]['components'][0]
    assert component['progress_m']==.5
    assert component['boundary_changes'][-1]['delta_m']==pytest.approx(10.)
    assert not component['boundary_changes'][-1]['allocated_to_platform']


@pytest.mark.parametrize('mutation',[
    lambda q:q.update(use_status='review_required'),
    lambda q:q.update(unit='unknown'),
    lambda q:q.update(first_source_row=999),
    lambda q:q.update(net_change_m=-.01),
])
def test_unreliable_progress_is_not_zero_and_has_no_rate(activity_case,mutation):
    package,pack,frame=activity_case;mutation(package['platforms'][0]['interval_advance'])
    a=build_activity_facts(package,pack,frame)
    assert a['activities'][0]['progress_m'] is None
    assert a['activities'][0]['rate_m_per_advancing_hour'] is None
    assert displayed_activity(a['activities'][0])['progress']=='待核实'


def test_one_rejected_component_suppresses_whole_group_not_partial_total(activity_case):
    package,pack,frame=activity_case
    package['platforms'][1]['interval_advance']['use_status']='review_required'
    group={'platform_ids':['P1','P2'],'start_time':pack['process_summary']['groups'][0]['start_time'],
           'end_time':pack['process_summary']['groups'][1]['end_time'],'advancing_seconds':80.}
    pack['process_summary']={'groups':[group],'selected':[group]}
    a=build_activity_facts(package,pack,frame)
    assert a['activities'][0]['progress_m'] is None


def test_group_overlap_rejected(activity_case):
    package,pack,frame=activity_case
    pack['process_summary']['groups'].append(deepcopy(pack['process_summary']['groups'][0]))
    with pytest.raises(ValueError,match='overlapping'):build_activity_facts(package,pack,frame)


def test_wrong_time_denominator_rejected(activity_case):
    package,pack,frame=activity_case;pack['process_summary']['groups'][0]['advancing_seconds']=20.
    with pytest.raises(ValueError,match='duration_mismatch'):build_activity_facts(package,pack,frame)


def test_duplicate_original_rows_rejected(activity_case):
    package,pack,frame=activity_case;frame.loc[1,'source_row']=1
    with pytest.raises(ValueError,match='duplicate_original'):build_activity_facts(package,pack,frame)


def test_zero_progress_is_not_missing_and_zero_advance_time_no_division(activity_case):
    package,pack,frame=activity_case
    frame.loc[3,'cumulative_advance']=frame.loc[2,'cumulative_advance']
    package['platforms'][0]['interval_advance']['net_change_m']=0.
    a=build_activity_facts(package,pack,frame)
    assert a['activities'][0]['progress_m']==0.
    assert a['activities'][0]['rate_m_per_advancing_hour']==0.
    frame['machine_state']='stopped';package['daily_overview']['advancing_seconds']=0.
    for g in pack['process_summary']['groups']:g['advancing_seconds']=0.
    a=build_activity_facts(package,pack,frame)
    assert a['daily']['rate_m_per_advancing_hour'] is None
    assert a['activities'][0]['rate_m_per_advancing_hour'] is None


def reply_fixture():
    data={'version':'test','paragraphs':[
        {'paragraph_id':'p1','locked_text':'原概况'},
        {'paragraph_id':'p2','support_ids':['D'],'daily':{'advance':'8.00m','advancing':'4小时11分10秒','advancing_share':'17.4%','rate':'1.91m/h'}},
        {'paragraph_id':'p3','support_ids':['A'],'activities':[],'windows':[{'time':'02:15:00至02:19:50',
            'pattern':{'trend_fields':['advance_speed','thrust']},'trends':{'advance_speed':'increasing','thrust':'increasing'}}],
         'major_activities_are_not_all_activities':True},
        {'paragraph_id':'p4','locked_text':'地质原文','retained_source_binding':{'fact_ids':['G']}}]}
    reply={'paragraphs':[{'paragraph_id':'p2','support_ids':['D'],'text':'全天进尺8.00m，累计推进4小时11分10秒，推进时间占比17.4%，全天折算推进率1.91m/h。'},
                         {'paragraph_id':'p3','support_ids':['A'],'text':'此外有其他推进活动。02:15:00至02:19:50推进速度、总推力同向上升。'}]}
    return data,reply


def test_valid_standard_reply_and_locked_chapters():
    data,reply=reply_fixture();assert validate_reply(reply,data)['passed']
    n=combine(reply,data)
    assert n['paragraphs'][0]['text']=='原概况'
    assert n['paragraphs'][3]['source_binding']=={'fact_ids':['G']}


@pytest.mark.parametrize('index,change',[
    (0,lambda p:p.update(text=p['text'].replace('1.91','9.91'))),
    (0,lambda p:p.update(text=p['text'].replace('1.91m/h','1.91mm/min'))),
    (0,lambda p:p.update(text=p['text'].replace('折算推进率','设备效率'))),
    (1,lambda p:p.update(text=p['text'].replace('上升','下降'))),
    (1,lambda p:p.update(text=p['text'].replace('此外有其他推进活动。',''))),
    (1,lambda p:p.update(support_ids=['INVENTED'])),
    (1,lambda p:p.update(text=p['text']+'（19条样本，支持约5分钟）')),
    (0,lambda p:p.update(text='2.1 全天运行概况\n'+p['text'])),
    (1,lambda p:p.update(text=p['text']+'刀盘扭矩基本稳定。')),
    (1,lambda p:p.update(text=p['text']+'真实局部曲线另行插入。')),
])
def test_standard_error_injections(index,change):
    data,reply=reply_fixture();change(reply['paragraphs'][index])
    assert not validate_reply(reply,data)['passed']


def test_interval_value_must_belong_to_the_stated_time_not_another_row():
    data,reply=reply_fixture();f=data['paragraphs'][2]
    f['activities']=[{'time':'02:01:20至03:37:20','progress':'1.88m','rate':'1.96m/h'},
                     {'time':'10:42:10至14:22:40','progress':'2.69m','rate':'2.30m/h'}]
    reply['paragraphs'][1]['text']='02:01:20至03:37:20进尺1.88m，折算推进率1.96m/h。'+reply['paragraphs'][1]['text']
    assert validate_reply(reply,data)['passed']
    reply['paragraphs'][1]['text']=reply['paragraphs'][1]['text'].replace('1.88m','2.69m')
    assert any('wrong_scope_or_value' in e for e in validate_reply(reply,data)['errors'])


def test_only_allowlisted_nonfactual_scaffolding_is_normalized():
    data,reply=reply_fixture();original=deepcopy(reply)
    reply['paragraphs'][0]['text']='2.1 全天运行概况\n'+reply['paragraphs'][0]['text']
    reply['paragraphs'][1]['text']+='各窗口真实局部曲线另行插入。'
    clean,log=normalize_reply(reply)
    assert clean==original and len(log)==2
    assert reply['paragraphs'][0]['text'].startswith('2.1')
    reply['paragraphs'][1]['text']+='总推力基本稳定。'
    clean,_=normalize_reply(reply)
    assert '基本稳定' in clean['paragraphs'][1]['text']
    assert not validate_reply(clean,data)['passed']


def test_pdf_check_handles_unavailable_stable_statistics(tmp_path):
    import fitz
    from tbm_process.reporting_v2.engineering_presentation import check_pdf

    pdf=tmp_path/'missing_statistics.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((40,40),'No qualified stable samples',fontsize=12)
        doc.save(pdf)
    payload={'paragraphs':[{'paragraph_id':'p1','text':'No qualified stable samples'}]}
    stats={'advance_speed':{'median':None,'p10':None,'p90':None}}
    result=check_pdf(pdf,payload,stats,[],tmp_path)
    assert result['passed']
    assert result['missing_table_values']==[]


@pytest.mark.parametrize('day',['2023-12-20','2024-01-24','2024-04-19'])
def test_saved_real_days_replay_without_algorithms_or_API(day):
    root=Path.home()/'Desktop/TBM_论文研究候选冻结版_20261028/run1/selection_preflight'/day
    if not (root/'report_facts.json').exists():pytest.skip('Local research evidence not distributed with unit tests')
    package=json.loads((root/'report_facts.json').read_text());pack=json.loads((root/'evidence_pack.json').read_text())
    frame=pd.read_csv(package['source']['samples']);frame.time=pd.to_datetime(frame.time)
    a=build_activity_facts(package,pack,frame)
    assert a['date']==day
    assert a['coverage']['selected_count']==len(pack['process_summary']['selected'])
    assert a['coverage']['other_advancing_seconds']>=0
    if day=='2024-01-24':
        assert a['daily']['rate_m_per_advancing_hour']==pytest.approx(1.9110816191108162)
        assert [r['progress_m'] for r in a['selected_activities']]==pytest.approx([1.875,1.563,2.687,1.875])
