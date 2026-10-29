"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
from pathlib import Path
import math
import numpy as np
import pandas as pd
from .common import *

def finite(value):
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else None

def truth(series):
    return series.fillna(False).astype(str).str.lower().isin(['true','1'])

def supports(times, maximum_gap):
    delta=times.shift(-1).sub(times).dt.total_seconds()
    return delta.where(delta.gt(0)&delta.le(maximum_gap),0.).fillna(0.)

def time_weighted_windows(times, values, duration, window_seconds, minimum_coverage=1.):
    """Left-held measured support, no long-gap/NaN interpolation, exact clipping at each window start."""
    t=times.astype('int64').to_numpy()/1e9
    y=np.asarray(values,float);dt=np.asarray(duration,float)
    good=np.isfinite(y)&(dt>0);weights=np.where(good,dt,0.);areas=np.where(good,y*dt,0.)
    cp=np.r_[0.,np.cumsum(weights)];ca=np.r_[0.,np.cumsum(areas)]
    result=np.full(len(y),np.nan);coverage=np.zeros(len(y))
    for i,end in enumerate(t):
        begin=end-window_seconds;j=int(np.searchsorted(t,begin,side='left'))
        # Exclude the interval beginning at the endpoint: it is still future data at that endpoint.
        observed=cp[i]-cp[j];area=ca[i]-ca[j]
        if j>0 and good[j-1]:
            clipped=max(0.,min(t[j-1]+dt[j-1],end)-begin)
            observed+=clipped;area+=clipped*y[j-1]
        coverage[i]=observed/window_seconds
        if observed>=window_seconds*minimum_coverage-1e-6:result[i]=area/observed
    return result,coverage

def read_day(path,config):
    needed=['运行时间-time','mapping_source_row','mapping_gas_status','mapping_units_confirmed',
        'mapping_navigation_mode','导向盾首里程','quality_duplicate_timestamp','quality_backward_timestamp',
        'quality_chainage_short_gap_jump','quality_chainage_missing',*GASES]
    frame=pd.read_csv(path,usecols=lambda c:c in needed,low_memory=False)
    missing=[c for c in needed if c not in frame]
    if missing:raise ValueError('Missing standardized fields: '+str(missing))
    frame['time']=pd.to_datetime(frame['运行时间-time'],format='mixed',errors='coerce')
    if frame.time.isna().any() or frame.mapping_source_row.duplicated().any():raise ValueError('Invalid timestamp or source-row identity')
    if not frame.time.is_monotonic_increasing:raise ValueError('Backward timestamps: do not silently sort')
    expected=path.stem.replace('tbm_data_','');day=pd.to_datetime(expected,format='%Y%m%d').date().isoformat()
    if set(frame.time.dt.strftime('%Y-%m-%d'))!={day}:raise ValueError('Filename/date disagreement')
    saved=Path(config['frozen_samples_directory'])/(day+'.csv.gz')
    if saved.is_file():
        frozen=pd.read_csv(saved,usecols=['time','source_row','machine_state'])
        frozen['time']=pd.to_datetime(frozen.time)
        frame=frame.merge(frozen.rename(columns={'source_row':'mapping_source_row'}),on=['time','mapping_source_row'],how='left',validate='one_to_one')
        matched=int(frame.machine_state.notna().sum())
    else:frame['machine_state']='unknown';matched=0
    dt=supports(frame.time,config['gas']['maximum_supported_gap_seconds'])
    return day,frame,dt,{'saved_phase_state_source':str(saved),'matched_rows':matched,'total_rows':len(frame),
        'reclassified_states':False,'unmatched_state_rows_excluded_from_state_specific_statistics':int(frame.machine_state.isna().sum())}
def runs(mask,dt):
    spans=[];start=None
    for i,active in enumerate(mask):
        if start is not None and (not active or (i>0 and dt.iloc[i-1]<=0)):
            spans.append((start,i-1));start=None
        if active and start is None:start=i
    if start is not None:spans.append((start,len(mask)-1))
    return spans
def good_samples(frame, field):
    values = pd.to_numeric(frame[field], errors='coerce')
    valid = (values.notna() & np.isfinite(values) & values.ge(0)
             & frame.mapping_gas_status.eq('structural_and_step_supported_units_unconfirmed')
             & ~truth(frame.quality_duplicate_timestamp) & ~truth(frame.quality_backward_timestamp)
             & ~frame.time.duplicated(keep=False))
    return values.where(valid), valid

def attention_series(times, values, support, rule):
    custom=rule.get('user_attention_ppm')
    threshold = float(custom) if custom is not None else rule['basis_limit_mgm3'] * 24.05 / rule['molar_mass']
    if not np.isfinite(threshold) or threshold<=0:raise ValueError('Attention value must be a finite positive ppm number')
    if rule['window_seconds']:
        measured, coverage = time_weighted_windows(times, values, support, rule['window_seconds'])
    else:
        measured = np.asarray(values, dtype=float)
        coverage = np.where(np.isfinite(measured), 1., 0.)
    return np.asarray(measured), coverage, threshold


def build_gas(day, path, config, folder, samples_directory):
    """Compute from standardized rows, never read statistics from an old report."""
    from .plots import daily
    folder.mkdir(parents=True, exist_ok=False)
    reader = {'frozen_samples_directory': str(samples_directory),
              'gas': {'maximum_supported_gap_seconds': 60}}
    actual_day, frame, dt, state = read_day(path, reader)
    if actual_day != day:
        raise ValueError('gas_report_date_mismatch')
    source = {'source_file': str(path), 'source_sha256': sha(path)}
    statistics, events, evaluations = [], [], []
    for field in GASES:
        values, valid = good_samples(frame, field)
        supported = dt.where(valid, 0.)
        finite_values = values.dropna()
        statistics.append({
            'date': day, 'field': field, 'unit': 'ppm',
            'unit_source': 'user_designated_research_unit', 'numeric_conversion_factor': 1.,
            'valid_nonnegative_structurally_mapped_samples': int(valid.sum()),
            'excluded_samples': int((~valid).sum()),
            'monitored_supported_seconds': float(supported.sum()),
            'minimum': finite(finite_values.min()), 'maximum': finite(finite_values.max()),
            'time_weighted_mean': float((values.fillna(0)*supported).sum()/supported.sum()) if supported.sum() else None,
            'p10': finite(finite_values.quantile(.1)), 'median': finite(finite_values.median()),
            'p90': finite(finite_values.quantile(.9)),
            'time_at_maximum': frame.loc[values.idxmax(), 'time'].isoformat() if valid.any() else None,
            'source_row_at_maximum': int(frame.loc[values.idxmax(), 'mapping_source_row']) if valid.any() else None,
        })
    for rule in config['gas']['rules']:
        values, valid = good_samples(frame, rule['field'])
        measured, coverage, threshold = attention_series(frame.time, values, dt.where(valid, 0.), rule)
        over = np.isfinite(measured) & (measured >= threshold)
        for a, b in runs(over, dt):
            events.append({'event_id': ident('ATTENTION', day, rule['id'], a, b), 'field': rule['field'],
                'rule_id': rule['id'], 'evaluation_first_time': frame.time.iloc[a].isoformat(),
                'evaluation_last_time': frame.time.iloc[b].isoformat(),
                'window_seconds': rule['window_seconds'], 'attention_ppm': threshold,
                'maximum_statistic_ppm': float(np.nanmax(measured[a:b+1])),
                'source_first_row': int(frame.mapping_source_row.iloc[a]),
                'source_last_row': int(frame.mapping_source_row.iloc[b]),
                'not_exposure_or_formal_alarm': True})
        evaluations.append({**rule, 'attention_ppm': threshold, 'formal_alarm': False,
            'valid_evaluation_samples': int(np.isfinite(measured).sum()),
            'triggered_samples': int(over.sum()), 'incomplete_windows': int((coverage < 1-1e-9).sum())})
    daily(day, frame, dt, folder/'figures', unit_label='ppm')
    result = {'date': day, 'source': source, 'state_source': state, 'statistics': statistics,
              'attention_events': events, 'rule_evaluations': evaluations, 'policy': config['gas']}
    save(folder/'gas_facts.json', result)
    csv_file(folder/'gas_statistics.csv', statistics)
    csv_file(folder/'attention_events.csv', events)
    return result
