"""Report-only progress and rate facts over frozen platform memberships."""
from __future__ import annotations

from copy import deepcopy
import math

import pandas as pd

from .facts import duration
from .performance import row_evidence
from .response_features import BASE, metric_summary, response_samples


def numeric(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def _seconds(frame):
    dt = pd.to_numeric(frame.sample_dt_seconds, errors='coerce')
    if dt.lt(0).any() or dt.isin([float('inf'),float('-inf')]).any():
        raise ValueError('invalid_saved_sample_support')
    # The frozen reports give terminal/unsupported records zero support, never an invented nominal interval.
    return float(dt.fillna(0).sum())


def _row_ref(row):
    return {'source_file': str(row.source_file), 'source_row': int(row.source_row), 'time': row.time.isoformat()}


def platform_progress(platform, block, samples):
    """Replay existing endpoints; never turn a rejected value into an adopted one."""
    q = deepcopy(platform['interval_advance'])
    reasons = []
    if q.get('use_status') != 'adoptable':
        reasons.append('upstream_interval_progress_not_adoptable')
    if q.get('source_original_field') != '开累进尺' or q.get('unit') != 'm':
        reasons.append('unexpected_progress_source_or_unit')
    values = pd.to_numeric(block.cumulative_advance, errors='coerce')
    valid = block.loc[values.notna()]
    delta = numeric(q.get('net_change_m'))
    if len(valid) < 2 or delta is None:
        reasons.append('missing_endpoints_or_delta')
    else:
        actual = float(valid.cumulative_advance.iloc[-1])-float(valid.cumulative_advance.iloc[0])
        if (abs(actual-delta)>1e-7 or int(valid.source_row.iloc[0])!=q.get('first_source_row')
                or int(valid.source_row.iloc[-1])!=q.get('last_source_row')):
            reasons.append('saved_endpoint_value_or_row_mismatch')
        if delta < 0:
            reasons.append('negative_progress_not_reported_as_excavation')
    boundaries=[]
    positions=samples.index[samples.experimental_platform_id.eq(platform['platform_id'])].tolist()
    if positions:
        for left,right,kind in [(positions[0]-1,positions[0],'before'),(positions[-1],positions[-1]+1,'after')]:
            if 0 <= left < len(samples) and 0 <= right < len(samples):
                a,b=samples.iloc[left],samples.iloc[right]
                x,y=numeric(a.cumulative_advance),numeric(b.cumulative_advance)
                boundaries.append({'side':kind,'left':_row_ref(a),'right':_row_ref(b),
                                   'delta_m':y-x if x is not None and y is not None else None,
                                   'allocated_to_platform':False})
    return {'platform_id':platform['platform_id'],'progress_m':delta if not reasons else None,
            'status':'adoptable' if not reasons else 'review_required','reasons':reasons,
            'upstream_quality':q,'source':row_evidence(block),'boundary_changes':boundaries}


def build_activity_facts(package, pack, samples):
    """No new segmentation, no speed integration, and no coarse-chainage interval advance."""
    samples=samples.copy().sort_values('time').reset_index(drop=True)
    samples['time']=pd.to_datetime(samples.time)
    if samples[['source_file','source_row']].duplicated().any():
        raise ValueError('duplicate_original_PLC_record')
    platforms={p['platform_id']:p for p in package['platforms']}
    if len(platforms)!=len(package['platforms']):raise ValueError('duplicate_platform_ID')
    base,_=response_samples(samples,package['config'])
    groups=[];used=set()
    selected_ids={tuple(g['platform_ids']) for g in pack['process_summary']['selected']}
    for index,g in enumerate(pack['process_summary']['groups'],1):
        ids=g['platform_ids']
        if len(set(ids))!=len(ids) or used.intersection(ids):raise ValueError('overlapping_activity_platform_membership')
        if any(i not in platforms for i in ids):raise ValueError('unknown_activity_platform_ID')
        used.update(ids)
        block=samples.loc[samples.experimental_platform_id.isin(ids)]
        if block.empty:raise ValueError('missing_activity_samples')
        advancing=block.loc[block.machine_state.eq('advancing')]
        active=_seconds(advancing)
        if abs(active-g['advancing_seconds'])>1e-7:raise ValueError('saved_activity_advancing_duration_mismatch')
        components=[platform_progress(platforms[i],samples.loc[samples.experimental_platform_id.eq(i)],samples) for i in ids]
        good=all(c['status']=='adoptable' for c in components)
        progress=sum(c['progress_m'] for c in components) if good else None
        rate=progress/(active/3600) if progress is not None and active>0 else None
        base_block=base.loc[base.experimental_platform_id.isin(ids)]
        metrics={k:metric_summary(base_block,k,package['config']) for k in BASE}
        windows=[{'window_id':w['window_id'],'start_time':w['start_time'],'end_time':w['end_time'],
                  'pattern':w['pattern'],'source':w['source']} for w in pack['response_analysis']['selected_time_windows']
                 if w['platform_id'] in ids]
        groups.append({'activity_id':f'ACTIVITY:{index:03d}','selected':tuple(ids) in selected_ids,
                       'start_time':g['start_time'],'end_time':g['end_time'],'platform_ids':ids,
                       'elapsed_span_seconds':(pd.Timestamp(g['end_time'])-pd.Timestamp(g['start_time'])).total_seconds(),
                       'advancing_seconds':active,'advancing_display':duration(active),'progress_m':progress,
                       'progress_status':'adoptable' if good else 'review_required','rate_m_per_advancing_hour':rate,
                       'rate_status':'usable_descriptive' if rate is not None else 'not_available',
                       'rate_definition':'sum_adoptable_platform_internal_cumulative_deltas / classified_advancing_hours_in_same_platforms',
                       'rate_is_not_speed_median':True,'stable_metrics':metrics,'selected_windows':windows,
                       'components':components,'source':row_evidence(block),'advancing_source':row_evidence(advancing)})
    overview=package['daily_overview'];advance=overview['daily_advance'];t=numeric(overview['advancing_seconds'])
    length=numeric(advance['net_change_m']);observed=numeric(overview['effective_state_duration_seconds'])
    actual_advancing=_seconds(samples.loc[samples.machine_state.eq('advancing')])
    if t is None or abs(actual_advancing-t)>1e-7:raise ValueError('daily_advancing_duration_mismatch')
    if observed is None or abs(_seconds(samples)-observed)>1e-7:raise ValueError('daily_observed_duration_mismatch')
    daily_usable=bool(overview.get('daily_comparison_allowed')) and length is not None and length>=0 and advance['source_original_field']=='导向盾首里程'
    selected=[g for g in groups if g['selected']]
    selected_time=sum(g['advancing_seconds'] for g in selected)
    all_progress=sum(g['progress_m'] for g in groups) if all(g['progress_m'] is not None for g in groups) else None
    return {'version':'report_activity_facts_20261028','date':package['date'],
            'daily':{'advance_m':length if daily_usable else None,'advancing_seconds':t,'advancing_display':duration(t),
                     'observed_state_seconds':observed,'advancing_share_percent':100*t/observed if observed>0 else None,
                     'rate_m_per_advancing_hour':length/(t/3600) if daily_usable and t>0 else None,
                     'rate_definition':'guide_shield_daily_advance_m / classified_advancing_hours',
                     'unit':'m/h','rate_not_safety_or_equipment_efficiency_grade':True,
                     'stable_statistics':next(f['data']['statistics'] for f in pack['facts'] if f['fact_id']=='PARAMETER_ANALYSIS_SUMMARY')},
            'activities':groups,'selected_activities':selected,
            'coverage':{'all_activity_count':len(groups),'selected_count':len(selected),
                        'selected_advancing_seconds':selected_time,'other_advancing_seconds':t-selected_time,
                        'selected_share_of_daily_advancing_percent':100*selected_time/t if t>0 else None},
            'reconciliation':{'all_platform_internal_progress_sum_m':all_progress,'daily_guide_shield_advance_m':length,
                              'same_measurement_basis':False,'must_not_force_equality':True},
            'limits':['PLC_field_estimate_not_formal_engineering_survey','no_claim_of_strictly_comparable_operating_conditions',
                      'boundary_changes_remain_unassigned','no_causal_or_geological_efficiency_attribution'],
            'duration_audit':{'undefined_sample_support_count':int(samples.sample_dt_seconds.isna().sum()),
                              'undefined_support_contribution_seconds':0,'nominal_sampling_interval_imputed':False},
            'source':package['source']}
