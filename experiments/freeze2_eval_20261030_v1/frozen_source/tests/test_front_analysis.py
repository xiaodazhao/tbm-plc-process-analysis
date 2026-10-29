import numpy as np
import pandas as pd
import pytest

from tbm_process.engineering_reporting.front_analysis import progress_audit,window_statistics,weighted_bins,gas_operation_analysis
from tbm_process.engineering_reporting.common import GASES
from tbm_process.engineering_reporting.writer import request,validate_plan,validate_text


def test_integer_chainage_is_not_falsely_formatted_with_centimeters():
    samples=pd.DataFrame({'chainage':[100,100,101], 'cumulative_advance':[1.25,1.20,2.5],
                          'source_row':[1,2,3],'time':pd.date_range('2024-01-01',periods=3,freq='20s')})
    result=progress_audit(samples,{'daily_advance':{'net_change_m':1,'source_original_field':'导向盾首里程'}})
    assert result['daily_advance_display_m']=='约1 m'
    assert result['fields']['cumulative_advance']['negative_steps']==1
    assert result['fields']['cumulative_advance']['endpoint_difference_m']==1.25
    assert result['cumulative_endpoint_difference_is_alternative_not_verified_daily_advance']


def test_window_percent_does_not_use_IQR_normalized_change():
    w={'window_id':'w','start_time':'2024-01-01','end_time':'2024-01-01','source':[],
       'sample_count':20,'quarter_sample_count':5,'changes':{'advance_speed':{
           'initial_median':20.,'final_median':30.,'relative_change':3.8,'unit':'mm/min','trend':'increasing'}}}
    assert window_statistics([w])[0]['metrics']['advance_speed']['change_percent']==50
    w['changes']['advance_speed']['initial_median']=0
    assert window_statistics([w])[0]['metrics']['advance_speed']['change_percent'] is None


def test_bins_use_actual_durations_and_break_missing_coverage():
    t=pd.date_range('2024-01-01',periods=3,freq='20s')
    b=weighted_bins(t,[10.,20.,99.],[20.,20.,0.],t[0],t[0]+pd.Timedelta(seconds=120),60)
    assert b['mean'].isna().all()
    b=weighted_bins(t,[10.,20.,30.],[20.,20.,20.],t[0],t[0]+pd.Timedelta(seconds=60),60)
    assert b['mean'].iloc[0]==20


def test_split_support_is_clipped_at_bin_edges():
    t=[pd.Timestamp('2024-01-01 00:00:40')]
    b=weighted_bins(t,[12.],[40.],pd.Timestamp('2024-01-01'),pd.Timestamp('2024-01-01 00:02'),60)
    assert b.supported_seconds.tolist()==[20.,20.]
    assert b['mean'].isna().all()


def signal_case(with_gap=False):
    times=pd.date_range('2024-01-01',periods=100,freq='20s')
    n=len(times);states=np.array(['advancing']*15+['stopped']*(n-15))
    gas=np.r_[np.full(15,10.),np.linspace(10,1,30),np.ones(n-45)]
    frame=pd.DataFrame({'time':times,'mapping_source_row':np.arange(n),'machine_state':states,
        'mapping_gas_status':'structural_and_step_supported_units_unconfirmed',
        'quality_duplicate_timestamp':False,'quality_backward_timestamp':False})
    for f in GASES:frame[f]=gas if f=='SO2检测' else 0.
    samples=pd.DataFrame({'time':times,'source_row':np.arange(n),'advance_speed':np.where(states=='advancing',30.,0.)})
    dt=pd.Series([20.]*(n-1)+[0.])
    if with_gap:dt.iloc[-16:-1]=0.
    return frame,dt,samples


def test_retrospective_recovery_does_not_mean_return_to_safety():
    frame,dt,samples=signal_case()
    result,_,_=gas_operation_analysis('2024-01-01',frame,dt,samples,{'attention_events':[]})
    recovered=[r for r in result['post_stop_records'] if r['status']=='observed_tail_band_return']
    assert recovered
    assert all(r['seconds_since_stop']>=0 and r['hold_seconds']==300 for r in recovered)
    assert all(r['stable_reference_is_not_normal_or_safe_level'] for r in recovered)
    assert result['no_ventilation_effect_or_gas_safety_claim']


def test_tail_missing_data_does_not_produce_recovery_time():
    frame,dt,samples=signal_case(with_gap=True)
    result,_,_=gas_operation_analysis('2024-01-01',frame,dt,samples,{'attention_events':[]})
    assert not any(r['status']=='observed_tail_band_return' for r in result['post_stop_records'])


def test_constant_gases_have_no_invented_correlation_or_chart():
    frame,dt,samples=signal_case()
    for f in GASES:frame[f]=0.
    result,_,_=gas_operation_analysis('2024-01-01',frame,dt,samples,{'attention_events':[]})
    assert result['selected_plot_fields']==[]
    assert all(r['gas_speed_level_spearman'] is None for r in result['aligned_statistics'])


def test_front_scoped_plan_and_text_do_not_require_rewriting_geology():
    scope=['1','2.1','2.2','2.3']
    pack={'date':'2024-01-01','policy':{},'sections':{s:s for s in scope},'generation_scope':scope,
          'facts':[{'fact_id':s,'section':s,'canonical_text':'真实记录','data':{},'sources':[],
                    'required':True,'role':'computed'} for s in scope]}
    plan={'sections':[{'section_id':s,'paragraphs':[{'fact_ids':[s]}]} for s in scope],'omissions':[]}
    draft={'sections':[{'section_id':s,'paragraphs':[{'fact_ids':[s],'text':'真实记录'}]} for s in scope]}
    assert validate_plan(plan,pack,section_ids=scope)['passed']
    assert validate_text(draft,pack,plan,section_ids=scope)['passed']
    assert '其他章节程序将原样保留' in request('plan',pack)['messages'][0]['content']


def test_only_real_activity_table_can_cover_required_activity_without_repetition():
    pack={'facts':[{'fact_id':'ACTIVITY:001','section':'2.2','canonical_text':'真实活动',
                   'data':{},'sources':[],'required':True,'role':'computed'}],
          'presentation_contract':{'2.2':{'fact_ids':['ACTIVITY:001']}}}
    plan={'sections':[{'section_id':'2.2','paragraphs':[{'fact_ids':['ACTIVITY:001']}]}]}
    draft={'sections':[{'section_id':'2.2','paragraphs':[]}]}
    assert validate_text(draft,pack,plan,section_ids=['2.2'])['passed']
    pack['facts'][0]['fact_id']='RISK_REQUIRED';plan['sections'][0]['paragraphs'][0]['fact_ids']=['RISK_REQUIRED']
    pack['presentation_contract']['2.2']['fact_ids']=['RISK_REQUIRED']
    assert not validate_text(draft,pack,plan,section_ids=['2.2'])['passed']


def test_optional_reference_retirement_is_recorded_not_added_to_text():
    from tbm_process.engineering_reporting.writer import retire_unused_optional_front_references
    pack={'facts':[{'fact_id':'reference','section':'2.3','required':False,
                   'role':'conditional_design_reference_not_implementation'}]}
    plan={'sections':[{'section_id':'2.3','paragraphs':[{'fact_ids':['reference']}]}],'omissions':[]}
    draft={'sections':[{'section_id':'2.3','paragraphs':[]}]}
    retired=retire_unused_optional_front_references(plan,draft,pack)
    assert retired and plan['omissions'][0]['fact_id']=='reference'
    assert draft['sections'][0]['paragraphs']==[]
    pack['facts'][0]['required']=True
    plan={'sections':[{'section_id':'2.3','paragraphs':[{'fact_ids':['reference']}]}],'omissions':[]}
    assert not retire_unused_optional_front_references(plan,draft,pack)


def test_overview_relative_geology_keeps_the_original_report_comparator():
    from tbm_process.engineering_reporting.writer import qualify_overview_forecast_reference
    pack={'facts':[{'fact_id':'OVERVIEW_GEOLOGY','data':{'original_fact_ids':['G']}},
                   {'fact_id':'G','canonical_text':'HSP围岩较前段变好','data':{
                    'relative_description_reference':'original_report_preceding_forecast_segment_not_PLC_subsegment'}}]}
    draft={'sections':[{'section_id':'1','paragraphs':[{'text':'围岩较前段稍变好','fact_ids':['OVERVIEW_GEOLOGY']}]}]}
    result=qualify_overview_forecast_reference(draft,pack)
    assert result and '原报告的前一预报段' in draft['sections'][0]['paragraphs'][0]['text']
