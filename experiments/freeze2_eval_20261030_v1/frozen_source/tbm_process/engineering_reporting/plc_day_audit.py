"""Read saved PLC results; distinguish a zero endpoint difference from no activity."""
import math

import pandas as pd

from .common import save, sha
from .source_text import duration

VERSION = 'PLC_day_observation_audit_20261030_v1'


def audit(samples, overview, source):
    frame = samples.sort_values('time').copy()
    position = pd.to_numeric(frame.chainage, errors='coerce')
    if 'chainage_quality' in frame:
        position = position.where(frame.chainage_quality.astype(str).str.startswith('usable'))
    position = position.where(position.map(lambda v: pd.notna(v) and math.isfinite(v))).dropna()
    support = pd.to_numeric(frame.sample_dt_seconds, errors='coerce').fillna(0)
    advancing = frame.machine_state.eq('advancing')
    advancing_seconds = float(support.where(advancing, 0).sum())
    if abs(advancing_seconds-overview['advancing_seconds']) > 1e-6:
        raise ValueError('PLC_day_audit_disagrees_with_saved_state_duration')
    start, end = (float(position.iloc[0]), float(position.iloc[-1])) if len(position) else (None, None)
    net = end-start if start is not None else None
    if net is None:
        status = 'position_unavailable'
        conclusion = '未取得可用盾首里程读数，日里程变化缺测。'
    elif abs(net) > 1e-6:
        status = 'observed_position_change'
        conclusion = '盾首里程首末读数有变化，日进尺沿用已有有效首末差口径。'
    elif position.nunique() > 1:
        status = 'zero_net_with_internal_position_change'
        conclusion = '盾首里程首末相同，但日内读数有变化；首末差不能概括全部位移过程。'
    elif advancing.any():
        status = 'unchanged_position_with_advancing_records'
        conclusion = ('盾首里程读数未变，但PLC识别到推进记录，累计有效推进'
                      +duration(advancing_seconds)+'；不能按首末差认定全天没有推进。')
    elif float(support.sum()) > 0:
        status = 'no_advancing_in_observed_records'
        conclusion = '盾首里程读数未变，已取得记录中未识别到推进活动。'
    else:
        status = 'discrete_records_without_duration_support'
        conclusion = '仅有离散读数，没有可累计的有效状态时长，不能据此判断全天推进时长。'
    if overview['coverage_ratio'] < 1:
        conclusion += '未观测时段的施工情况未确认。'
    return {'version': VERSION, 'date': overview['date'], 'status': status,
        'position_source_field': '导向盾首里程', 'source': source,
        'record_count': len(frame), 'usable_position_count': len(position),
        'position_start_m': start, 'position_end_m': end, 'position_net_change_m': net,
        'position_min_m': float(position.min()) if len(position) else None,
        'position_max_m': float(position.max()) if len(position) else None,
        'position_unique_count': int(position.nunique()),
        'advancing_record_count': int(advancing.sum()), 'advancing_supported_seconds': advancing_seconds,
        'observed_supported_seconds': float(support.sum()), 'coverage_ratio': overview['coverage_ratio'],
        'hard_gap_count': overview['hard_gap_count'],
        'advancing_source_rows': frame.loc[advancing, 'source_row'].astype(int).tolist(),
        'conclusion': conclusion, 'fault_cause_confirmed': False,
        'missing_time_imputed_as_stopped': False, 'frozen_states_or_mapping_changed': False}


def enrich(pack, package, samples, folder):
    source = {'path': package['source']['samples'], 'sha256': package['source']['samples_sha256']}
    result = audit(samples, package['daily_overview'], source)
    path = folder/'PLC_day_observation_audit.json'
    save(path, result)
    day = next(f for f in pack['facts'] if f['fact_id'] == 'DAY')
    day['data']['PLC_day_observation_audit'] = result
    # A measured zero difference is not evidence of an entire day without excavation.
    if result['position_net_change_m'] is not None and abs(result['position_net_change_m']) < 1e-6:
        day['canonical_text'] = day['canonical_text'].replace('当日进尺约0m', '盾首里程首末差为0m')
    day['canonical_text'] += result['conclusion']
    day['sources'].append({'path': str(path.resolve()), 'sha256': sha(path)})
    pack['PLC_day_observation_audit'] = result
    save(folder/'unified_report_input.json', pack)
    return pack
