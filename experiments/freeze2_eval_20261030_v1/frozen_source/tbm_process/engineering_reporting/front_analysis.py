"""Report-only precision, compact window summaries and retrospective gas/state alignment."""
from copy import deepcopy
import math

import numpy as np
import pandas as pd

from .common import GASES, csv_file, save, sha
from .gas import good_samples, read_day
from .assembly import fact


POLICY = {
    'alignment_bin_seconds': 300, 'minimum_bin_coverage': .8, 'minimum_pairs': 12,
    'stop_minimum_seconds': 900, 'preceding_window_seconds': 300,
    'preceding_advancing_seconds': 60, 'recovery_bin_seconds': 60,
    'recovery_hold_seconds': 300, 'tail_reference_seconds': 300,
    'maximum_tail_spread_fraction_of_drop': .2,
    'meaning': 'retrospective_descriptive_selection_not_safety_or_significance_thresholds',
}


def _finite(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def progress_audit(samples, overview):
    fields = {}
    for field in ['chainage', 'cumulative_advance']:
        values = pd.to_numeric(samples[field], errors='coerce')
        valid = samples.loc[values.notna() & np.isfinite(values)]
        x = values.loc[valid.index]
        delta = x.diff()
        negative = valid.loc[delta.lt(-1e-7)]
        fields[field] = {
            'original_field': '导向盾首里程' if field == 'chainage' else '开累进尺',
            'first': _finite(x.iloc[0]) if len(x) else None,
            'last': _finite(x.iloc[-1]) if len(x) else None,
            'endpoint_difference_m': float(x.iloc[-1]-x.iloc[0]) if len(x)>1 else None,
            'valid_records': len(x), 'fractional_records': int((abs(x-x.round())>1e-7).sum()),
            'negative_steps': int(delta.lt(-1e-7).sum()), 'maximum_backstep_m': _finite(delta.min()),
            'largest_forward_step_m': _finite(delta.max()),
            'first_source_row': int(valid.iloc[0].source_row) if len(x) else None,
            'last_source_row': int(valid.iloc[-1].source_row) if len(x) else None,
            'backstep_rows': [{'time': r.time.isoformat(), 'source_row': int(r.source_row),
                               'delta_m': float(delta.loc[i])} for i,r in negative.iterrows()],
        }
    coarse = fields['chainage']['fractional_records'] == 0
    return {'fields': fields, 'chainage_integer_valued_in_this_day': coarse,
        'daily_advance_source_unchanged': overview['daily_advance']['source_original_field'],
        'daily_advance_display_m': f"约{overview['daily_advance']['net_change_m']:g} m" if coarse else f"{overview['daily_advance']['net_change_m']:.2f} m",
        'cumulative_endpoint_difference_is_alternative_not_verified_daily_advance': True,
        'no_speed_integration_or_positive_step_sum_substitution': True,
        'precision_note': '原始盾首里程仅见整米记录，里程差不能表达厘米精度' if coarse else '保留原字段精度',
    }


def window_statistics(windows):
    result = []
    for window in windows:
        metrics = {}
        for field, change in window['changes'].items():
            first, last = change['initial_median'], change['final_median']
            metrics[field] = {'initial_quarter_median': first, 'final_quarter_median': last,
                'change_percent': 100*(last-first)/abs(first) if first is not None and last is not None and first != 0 else None,
                'unit': change['unit'], 'trend': change['trend']}
        result.append({'window_id': window['window_id'], 'start_time': window['start_time'],
            'end_time': window['end_time'], 'metrics': metrics, 'source': window['source'],
            'sample_count': window['sample_count'], 'quarter_sample_count': window['quarter_sample_count'],
            'formula': '100 * (final_quarter_median - initial_quarter_median) / abs(initial_quarter_median)',
            'not_the_existing_IQR_normalized_relative_change': True,
            'not_instantaneous_endpoints_or_significance': True})
    return result


def weighted_bins(times, values, support, start, end, seconds):
    """Clip real left-held supports to non-overlapping bins, without gap filling."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    n = int(math.ceil((end-start).total_seconds()/seconds))
    area, covered = np.zeros(n), np.zeros(n)
    for time, value, dt in zip(times, values, support):
        if not np.isfinite(value) or not np.isfinite(dt) or dt <= 0:
            continue
        left, right = max(pd.Timestamp(time), start), min(pd.Timestamp(time)+pd.Timedelta(seconds=dt), end)
        if right <= left:
            continue
        while left < right:
            i = min(int((left-start).total_seconds()//seconds), n-1)
            edge = min(start+pd.Timedelta(seconds=(i+1)*seconds), right)
            weight = (edge-left).total_seconds()
            area[i] += value*weight; covered[i] += weight; left = edge
    widths = np.minimum(seconds, np.maximum(0., (end-start).total_seconds()-np.arange(n)*seconds))
    means = np.divide(area, covered, out=np.full(n, np.nan), where=covered >= widths*POLICY['minimum_bin_coverage']-1e-7)
    return pd.DataFrame({'time': [start+pd.Timedelta(seconds=i*seconds) for i in range(n)],
                         'mean': means, 'supported_seconds': covered, 'width_seconds': widths})


def _rho(a, b):
    good = pd.DataFrame({'a': a, 'b': b}).dropna()
    if len(good)<POLICY['minimum_pairs'] or good.a.nunique()<3 or good.b.nunique()<3:
        return None, len(good)
    return float(good.a.corr(good.b, method='spearman')), len(good)


def gas_operation_analysis(day, frame, dt, samples, gas):
    frame = frame.copy()
    speed = samples[['time','source_row','advance_speed']].rename(columns={'source_row':'mapping_source_row'})
    frame = frame.merge(speed, on=['time','mapping_source_row'], how='left', validate='one_to_one')
    triggered_fields = sorted({r['field'] for r in gas['attention_events']})
    fields = list(triggered_fields)
    reason = 'actual_research_attention_channel'
    if not fields:
        fields = [f for f in ['SO2检测','H2S检测','CO2检测'] if good_samples(frame,f)[0].nunique()>=4][:1]
        reason = 'varying_configured_channel_for_compact_display_not_a_risk_ranking'
    chosen = fields[:2]
    day_start, day_end = pd.Timestamp(day), pd.Timestamp(day)+pd.Timedelta(days=1)
    aligned, bins, recoveries = [], {}, []
    boundary = frame.machine_state.ne(frame.machine_state.shift()) | frame.time.diff().dt.total_seconds().gt(60)
    groups = [g for _,g in frame.groupby(boundary.cumsum(), sort=False)]
    for field in GASES:
        values, valid = good_samples(frame, field)
        known = frame.machine_state.notna() & frame.machine_state.ne('unknown')
        support = dt.where(valid & known,0.)
        gb = weighted_bins(frame.time, values, support, day_start, day_end, POLICY['alignment_bin_seconds'])
        ab = weighted_bins(frame.time, frame.machine_state.eq('advancing').astype(float), support,
                           day_start, day_end, POLICY['alignment_bin_seconds'])
        sb = weighted_bins(frame.time, pd.to_numeric(frame.advance_speed,errors='coerce'), support,
                           day_start, day_end, POLICY['alignment_bin_seconds'])
        r_level, count = _rho(gb['mean'], sb['mean'])
        r_delta, diff_count = _rho(gb['mean'].diff(), sb['mean'].diff())
        r_state, state_count = _rho(gb['mean'], ab['mean'])
        bins[field] = gb.assign(advance_speed=sb['mean'], advancing_fraction=ab['mean'])
        state_stats = []
        for state in ['advancing','stopped','free_rotation','thrusting_no_advance','standby']:
            mask = valid & frame.machine_state.eq(state)
            weights = dt.where(mask,0.)
            state_stats.append({'state':state, 'supported_seconds':float(weights.sum()),
                'time_weighted_mean_ppm':float((values.fillna(0)*weights).sum()/weights.sum()) if weights.sum() else None})
        aligned.append({'field':field, 'bin_seconds':POLICY['alignment_bin_seconds'],
            'gas_speed_level_spearman':r_level, 'paired_bins':count,
            'gas_speed_change_spearman':r_delta, 'paired_differences':diff_count,
            'gas_advancing_fraction_spearman':r_state, 'state_paired_bins':state_count,
            'state_statistics':state_stats, 'statistical_significance_or_causation_claimed':False})
        if field not in chosen:
            continue
        unique = np.sort(values.dropna().unique())
        differences = np.diff(unique)
        steps = differences[differences > max(float(np.max(abs(unique))) if len(unique) else 0.,1.)*1e-9]
        step = float(steps.min()) if len(steps) else None
        for group in groups:
            if group.machine_state.iloc[0] != 'stopped':
                continue
            first,last = group.index[0],group.index[-1]
            start = group.time.iloc[0]; end = group.time.iloc[-1]+pd.Timedelta(seconds=float(dt.iloc[last]))
            if (end-start).total_seconds()<POLICY['stop_minimum_seconds']:
                continue
            before_start = start-pd.Timedelta(seconds=POLICY['preceding_window_seconds'])
            previous = weighted_bins(frame.time, frame.machine_state.eq('advancing').astype(float), dt.where(known,0.),
                                     before_start,start,POLICY['preceding_window_seconds'])
            seconds_adv = float((previous['mean'].fillna(0)*previous.supported_seconds).sum())
            if seconds_adv<POLICY['preceding_advancing_seconds'] or first==0 or dt.iloc[first-1]<=0:
                continue
            b = weighted_bins(frame.time, values, dt.where(valid,0.), start,end,POLICY['recovery_bin_seconds'])
            complete = b.width_seconds.eq(60)
            b.loc[~complete,'mean'] = np.nan
            record = {'field':field, 'stop_start':start.isoformat(), 'stop_end':end.isoformat(),
                'source_first_row':int(frame.mapping_source_row.iloc[first]),
                'source_last_row':int(frame.mapping_source_row.iloc[last]),
                'preceding_advancing_seconds':seconds_adv, 'status':'insufficient_tail_or_gas_coverage',
                'bin_seconds':60, 'hold_seconds':300, 'stable_reference_is_not_normal_or_safe_level':True}
            tail_bins = b.loc[complete].tail(5)
            tail = tail_bins['mean']
            if len(tail)>=5 and tail.notna().all() and b['mean'].notna().any() and step is not None:
                center = float(tail.median()); spread = float(tail.quantile(.9)-tail.quantile(.1))
                peak_index = b['mean'].idxmax(); peak = float(b.loc[peak_index,'mean']); drop = peak-center
                record.update({'tail_reference_ppm':center,'tail_spread_ppm':spread,'observed_reading_step_ppm':step,
                    'tail_reference_start':tail_bins.time.iloc[0].isoformat(),
                    'tail_reference_end':(tail_bins.time.iloc[-1]+pd.Timedelta(seconds=60)).isoformat(),
                    'post_stop_peak_ppm':peak,'post_stop_peak_time':b.time.iloc[peak_index].isoformat(),
                    'post_stop_peak_delay_seconds':float((b.time.iloc[peak_index]-start).total_seconds())})
                if drop<=2*step:
                    record['status']='no_clear_post_stop_peak_to_tail_drop'
                elif spread>POLICY['maximum_tail_spread_fraction_of_drop']*drop:
                    record['status']='tail_not_sufficiently_flat_for_this_descriptive_rule'
                else:
                    band = max(spread,step)
                    inside = b['mean'].sub(center).abs().le(band+1e-12)
                    settled = next((i for i in range(peak_index+1,len(b)-4) if inside.iloc[i:i+5].all()),None)
                    record.update({'reference_band_halfwidth_ppm':band,
                                   'status':'recovery_not_observed_before_restart_or_day_end'})
                    if settled is not None:
                        record.update({'status':'observed_tail_band_return','band_entry_time':b.time.iloc[settled].isoformat(),
                            'confirmed_time':(b.time.iloc[settled]+pd.Timedelta(seconds=300)).isoformat(),
                            'seconds_since_stop':float((b.time.iloc[settled]-start).total_seconds()),
                            'seconds_since_peak':float((b.time.iloc[settled]-b.time.iloc[peak_index]).total_seconds())})
            recoveries.append(record)
    for row in recoveries:
        row['reportable_attention_channel'] = row['field'] in triggered_fields
    return {'date':day,'policy':deepcopy(POLICY),'selected_plot_fields':triggered_fields[:2],
        'analysis_fields':chosen,'selection_reason':reason,
        'plot_policy':'only_actual_attention_channels_other_channels_keep_statistics_and_alignment_audit',
        'aligned_statistics':aligned,'post_stop_records':recoveries,
        'time_reference':'existing_PLC_stopped_state_not_all_site_work_stopped',
        'recovery_reference':'same_stop_last_5_complete_one_minute_means_median_and_spread_retrospective_only',
        'no_ventilation_effect_or_gas_safety_claim':True}, bins, frame


def enrich_front(pack, package, activities, old_pack, samples, gas, source_root, analysis, folder):
    audit = progress_audit(samples,package['daily_overview'])
    from .progress_investigation import enrich_progress
    activities, _ = enrich_progress(pack, activities, samples, folder)
    windows = window_statistics(old_pack['response_analysis']['selected_time_windows'])
    day = pack['date']; raw = source_root/'TBM运行数据'/('tbm_data_'+day.replace('-','')+'.csv')
    _, frame, dt, _ = read_day(raw, {'frozen_samples_directory':str(analysis/'processed_samples'),
                                  'gas':{'maximum_supported_gap_seconds':60}})
    operation,bins,frame = gas_operation_analysis(day,frame,dt,samples,gas)
    source = [{'path':str(raw),'sha256':sha(raw)},
              {'path':package['source']['samples'],'sha256':package['source']['samples_sha256']}]
    by_id = {f['fact_id']:f for f in pack['facts']}
    if audit['chainage_integer_valued_in_this_day']:
        day_fact = by_id['DAY']
        day_fact['canonical_text'] = day_fact['canonical_text'].replace(
            f"当日进尺{package['daily_overview']['daily_advance']['net_change_m']:.2f}m",
            f"当日进尺约{package['daily_overview']['daily_advance']['net_change_m']:g}m（按整米记录的盾首里程首末差）")
    for f in pack['facts']:
        if f['section'].startswith('2'):
            f['canonical_text']=f['canonical_text'].replace('折算推进率','推进效率')
    rate = deepcopy(by_id['DAY_RATE'])
    rate.update(fact_id='OVERVIEW_EFFICIENCY',section='1')
    pack['facts'].append(rate)
    geology = [f for f in pack['facts'] if f['section']=='3.1' and f['role']=='prior_forecast']
    if not geology:
        geology = [f for f in pack['facts'] if f['section']=='3.1' and f['role']=='design_background']
    if geology:
        fact(pack['facts'],'OVERVIEW_GEOLOGY','1',
             '概况中仅简述这些当日范围资料的主要岩性、破碎或水文背景，保留预测/设计性质：'+
             '\n'.join(f['canonical_text'] for f in geology),
             {'original_fact_ids':[f['fact_id'] for f in geology]},
             [s for f in geology for s in f['sources']],True,'geological_background_not_today_observation')
    for row in windows:
        f=by_id[row['window_id']]
        f['data']['compact_statistics']=row
    text=[]
    for row in operation['aligned_statistics']:
        if row['field'] not in operation['analysis_fields']:
            continue
        states = {r['state']:r for r in row['state_statistics']}
        adv,stop = states['advancing']['time_weighted_mean_ppm'],states['stopped']['time_weighted_mean_ppm']
        if adv is not None and stop is not None:
            text.append(f"{row['field']}在合格推进记录内的时间加权均值为{adv:.4f}ppm，设备停止记录内为{stop:.4f}ppm；仅比较各自有效监测记录。")
        level=row['gas_speed_level_spearman']; change=row['gas_speed_change_spearman']
        text.append(f"{row['field']}按5分钟有效时间对齐推进速度，水平Spearman系数{level:.2f}，共同有效窗口{row['paired_bins']}个；相邻窗口变化的系数"+
                    (f"{change:.2f}，共同差分{row['paired_differences']}个。" if change is not None else '不可计算。') if level is not None else
                    f"{row['field']}变化或共同有效窗口不足，未取得可靠同步变化描述。")
    observed=[r for r in operation['post_stop_records'] if r['status']=='observed_tail_band_return' and r['reportable_attention_channel']]
    for row in sorted(observed,key=lambda r:-r['post_stop_peak_ppm'])[:2]:
        text.append(f"{row['field']}：设备停止状态{row['stop_start'][11:19]}至{row['stop_end'][11:19]}；"
            f"停止后峰值一分钟均值{row['post_stop_peak_ppm']:.4f}ppm出现在{row['post_stop_peak_time'][11:19]}，"
            f"于{row['band_entry_time'][11:19]}进入本次停止末段观测带并持续5分钟；距停止约{row['seconds_since_stop']/60:.1f}分钟，距峰值约{row['seconds_since_peak']/60:.1f}分钟。")
    if not observed:
        text.append('本日按既定覆盖和末段观测带规则，未取得可报告的停止后回落等待时间；这不等于气体没有变化。')
    unconfirmed=[r for r in operation['post_stop_records'] if r['reportable_attention_channel'] and
                 r.get('post_stop_peak_ppm',0)>0 and r['status']!='observed_tail_band_return']
    for row in sorted(unconfirmed,key=lambda r:-r['post_stop_peak_ppm'])[:2]:
        text.append(f"{row['field']}在设备停止{row['stop_start'][11:19]}至{row['stop_end'][11:19]}期间，"
            f"{row['post_stop_peak_time'][11:19]}出现一分钟均值峰值{row['post_stop_peak_ppm']:.4f}ppm，"
            f"距进入停止状态{row['post_stop_peak_delay_seconds']/60:.1f}分钟。停止末5个完整一分钟窗口中位数为{row['tail_reference_ppm']:.4f}ppm，"
            f"末段波动或回落幅度不满足当前观测带确认规则，未报告稳定回落时长。")
    text.append('上述均为历史回放时间对应，设备停止不等于现场停工；未测通风状态，不推断施工或通风造成气体变化，观测带不是安全限值。')
    fact(pack['facts'],'GAS_OPERATION_ALIGNMENT','2.3','\n'.join(text),operation,source,True,'descriptive_temporal_alignment_not_causation')
    pack['front_revision']={'version':'front_20261029_v2','daily_progress_formula_unchanged':True,
        'efficiency_display_name':'推进效率','efficiency_definition':'进尺 / 同范围累计推进小时，m/h，不是设备综合效率',
        'progress_precision':audit,'window_statistics':windows,'gas_operation':operation}
    pack['presentation_contract']={'2.2':{
        'fact_ids':[g['activity_id'] for g in activities['selected_activities']],
        'artifact':'activity_facts.json','artifact_sha256':sha(folder/'activity_facts.json'),
        'collection':'selected_activities','render_target':'major_activity_table'}}
    save(folder/'progress_precision_audit.json',audit)
    save(folder/'front_analysis.json',pack['front_revision'])
    save(folder/'gas/operation_analysis.json',operation)
    csv_file(folder/'gas/post_stop_review.csv',operation['post_stop_records'])
    for field,b in bins.items():
        b.to_csv(folder/'gas'/(field+'.aligned_5min.csv'),index=False,encoding='utf-8-sig')
    save(folder/'unified_report_input.json',pack)
    return pack
