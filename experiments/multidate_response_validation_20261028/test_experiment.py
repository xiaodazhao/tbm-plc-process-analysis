"""Protocol and boundary tests; these fixtures are not research observations."""
from copy import deepcopy
import numpy as np

from .report_validation import copy_guard
from .scan_and_explore import cell_index, integrity_category, select_dates
from .independent_audit import directed_percentage


def test_global_half_open_cells_have_unique_membership_and_last_endpoint():
    boundaries=[819.,822.,827.,828.]
    points=np.array([819.,821.,822.,826.,827.,828.])
    assert cell_index(points,boundaries).tolist()==[0,0,1,1,2,2]
    assert sum(np.bincount(cell_index(points,boundaries),minlength=3))==len(points)


def test_sampling_reproducible_and_not_replaced_after_generation():
    protocol={'seed':41,'normal_minimum_coverage_ratio':.8,'normal_minimum_stable_samples':30,
              'known_cases':['2024-04-26'],'formal_special_cases':[]}
    rows=[{'date':f'2024-01-{i:02d}','coverage_ratio':.9,'net_change_m':2,'spatial_allowed':True,
           'qualified_samples':100,'eligible_forecast_units':1} for i in range(1,28)]
    a=select_dates(rows,protocol);b=select_dates(rows,protocol)
    assert a==b and len(a['candidate_dates'])==6 and len(set(a['candidate_dates']))==6
    assert len(a['formal_dates'])==3
    assert max(a['candidate_dates'][:2])<min(a['candidate_dates'][2:4])


def test_empty_activity_guard_changes_only_copy_not_statistics():
    pack={'process_summary':{'selected':[]},'facts':[{'fact_id':'DAY_PATTERN_SUMMARY','canonical_text':'推进间歇分布',
           'data':{'seconds':0},'writer_data':{'seconds':0}}]}
    original=deepcopy(pack)
    result,edits=copy_guard(pack,{'daily_overview':{'advancing_seconds':0}})
    assert pack==original
    assert result['facts'][0]['data']['seconds']==0
    assert '未识别到推进状态' in result['facts'][0]['canonical_text']
    assert len(edits)==1


def test_existing_activity_does_not_receive_empty_activity_guard():
    pack={'process_summary':{'selected':[{'start':'x'}]},'facts':[]}
    assert copy_guard(pack,{'daily_overview':{'advancing_seconds':30}})==(pack,[])


def test_compound_fragmentation_is_preserved_and_local_label_not_globalized():
    assert integrity_category({'attributes':{'integrity':[{'text':'岩体较破碎-破碎','locality':[]}]}})=='较破碎-破碎'
    assert integrity_category({'attributes':{'integrity':[{'text':'局部极破碎','locality':['局部']}]}}) is None
    assert integrity_category({'attributes':{'integrity':[]}}) is None


def test_directional_percent_audit_checks_parameter_sign_and_rounding():
    assert directed_percentage('推进速度降低9.31%，总推力提高4.30%。','推进速度',-9.309)
    assert not directed_percentage('推进速度提高9.31%。','推进速度',-9.309)
    assert not directed_percentage('总推力降低9.31%。','推进速度',-9.309)
    assert not directed_percentage('推进速度降低19.31%。','推进速度',-9.309)


def test_sampling_rejects_bad_coverage_or_zero_advance_without_model_output():
    protocol={'seed':1,'normal_minimum_coverage_ratio':.8,'normal_minimum_stable_samples':30,
              'known_cases':[],'formal_special_cases':[]}
    rows=[{'date':'2024-01-01','coverage_ratio':.01,'net_change_m':7,'spatial_allowed':True,
           'qualified_samples':100,'eligible_forecast_units':1},
          {'date':'2024-01-02','coverage_ratio':1.,'net_change_m':0,'spatial_allowed':True,
           'qualified_samples':100,'eligible_forecast_units':1}]
    assert select_dates(rows,protocol)['normal_pool_dates']==[]
