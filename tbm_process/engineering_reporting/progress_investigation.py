"""Evidence-first progress investigations, without changing frozen PLC results."""
from copy import deepcopy
import re

import numpy as np
import pandas as pd

from .common import ROOT, csv_file, ident, load, save, sha


def _number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _reference(row):
    return {'source_file': str(row.source_file), 'source_row': int(row.source_row),
            'time': row.time.isoformat()}


def investigate_progress(samples, *, day, policy=None, quality_policy=None):
    policy = deepcopy(policy or load(ROOT/'configs/progress_investigation.json'))
    quality_policy = deepcopy(quality_policy or load(ROOT/'configs/daily_report_front_sections.json')['advance_quality'])
    frame = samples.copy()
    frame['time'] = pd.to_datetime(frame.time, errors='raise')
    if frame.time.isna().any() or not frame.time.is_monotonic_increasing or frame.time.duplicated().any():
        raise ValueError('investigation_requires_unique_ordered_timestamps')
    if not frame.time.dt.strftime('%Y-%m-%d').eq(day).all():
        raise ValueError('investigation_records_outside_report_date')
    if frame[['source_file', 'source_row']].duplicated().any():
        raise ValueError('investigation_requires_unique_source_rows')
    frame = frame.reset_index(drop=True)
    values = pd.to_numeric(frame.cumulative_advance, errors='coerce').replace([np.inf, -np.inf], np.nan)
    chainage = pd.to_numeric(frame.chainage, errors='coerce').replace([np.inf, -np.inf], np.nan)
    speed = pd.to_numeric(frame.advance_speed, errors='coerce').replace([np.inf, -np.inf], np.nan)
    support = pd.to_numeric(frame.sample_dt_seconds, errors='coerce').fillna(0.)
    if not np.isfinite(support).all() or support.lt(0).any():
        raise ValueError('invalid_investigation_sample_support')
    gap = frame.time.diff().dt.total_seconds()
    # A sample supports the following interval only when it reaches that next record.
    continuous = gap.gt(0) & gap.le(policy['maximum_adjacent_gap_seconds'])
    continuous &= support.shift().ge(gap-1e-7)
    if 'hard_gap_before' in frame:
        continuous &= ~frame.hard_gap_before.fillna(False).astype(bool)
    continuous &= frame.source_file.eq(frame.source_file.shift())
    delta = values.diff()
    jump = delta.gt(quality_policy['positive_jump_threshold_m'])
    reset = delta.le(-quality_policy['reset_drop_threshold_m'])
    cases = []
    for i in frame.index[jump | reset]:
        left, right = frame.iloc[i-1], frame.iloc[i]
        cases.append({'kind': 'forward_jump' if jump.iloc[i] else 'backstep_or_reset',
            'left_index': int(i-1), 'right_index': int(i),
            'transition': {'left': _reference(left), 'right': _reference(right),
                'elapsed_seconds': float(gap.iloc[i]), 'left_value_m': float(values.iloc[i-1]),
                'right_value_m': float(values.iloc[i]), 'delta_m': float(delta.iloc[i]),
                'continuous_supported_pair': bool(continuous.iloc[i]),
                'guide_shield_quality_usable': bool('chainage_quality' in frame and
                    frame.chainage_quality.iloc[i-1:i+1].astype(str).str.startswith('usable').all()),
                'chainage_delta_m': _number(chainage.iloc[i]-chainage.iloc[i-1]),
                'left_state': str(left.machine_state), 'right_state': str(right.machine_state)}})
    tolerance = policy['unchanged_tolerance_m']
    run_start = None
    runs = []
    for i in frame.index:
        if pd.isna(values.iloc[i]):
            if run_start is not None:
                runs.append((run_start, i-1))
            run_start = None
        elif run_start is None:
            run_start = i
        elif not continuous.iloc[i] or abs(values.iloc[i]-values.iloc[run_start]) > tolerance:
            runs.append((run_start, i-1))
            run_start = i
    if run_start is not None:
        runs.append((run_start, len(frame)-1))
    for start, end in runs:
        if end <= start:
            continue
        block = frame.iloc[start:end+1]
        # End-record support belongs after the unchanged run, so do not count it.
        weights = support.iloc[start:end+1].copy()
        weights.iloc[-1] = 0.
        active = block.machine_state.eq('advancing')
        active_seconds = float(weights.where(active, 0.).sum())
        if active_seconds < policy['minimum_plateau_advancing_seconds']:
            continue
        good_speed = speed.iloc[start:end+1].notna() & speed.iloc[start:end+1].ge(0)
        if 'advance_speed_quality' in block:
            good_speed &= block.advance_speed_quality.eq('usable')
        valid_seconds = float(weights.where(active & good_speed, 0.).sum())
        ratio = valid_seconds/active_seconds
        median = _number(speed.iloc[start:end+1].loc[active & good_speed].median())
        if ratio < policy['minimum_valid_speed_support_ratio'] or median is None or median <= policy['minimum_advancing_speed_median_mm_min']:
            continue
        active_rows = block.loc[active & good_speed]
        plateau = {'start': _reference(block.iloc[0]), 'end': _reference(block.iloc[-1]),
            'sample_count': len(block), 'constant_value_m': float(values.iloc[start]),
            'advancing_supported_seconds': active_seconds, 'valid_speed_supported_seconds': valid_seconds,
            'speed_support_ratio': ratio, 'advancing_speed_median_mm_min': median,
            'first_advancing_record': _reference(active_rows.iloc[0]),
            'last_advancing_record': _reference(active_rows.iloc[-1]),
            'chainage_also_constant': bool(chainage.iloc[start:end+1].notna().all() and
                chainage.iloc[start:end+1].max()-chainage.iloc[start:end+1].min() <= tolerance),
            'guide_shield_quality_usable': bool('chainage_quality' in block and
                block.chainage_quality.astype(str).str.startswith('usable').all()),
            'platform_advancing_support_seconds': {
                str(pid): float(weights.loc[g.index].where(active.loc[g.index] & good_speed.loc[g.index], 0.).sum())
                for pid, g in block.groupby('experimental_platform_id')},
            'end_support_not_counted': True}
        linked = next((c for c in cases if c['right_index'] == end+1 and c['transition']['continuous_supported_pair']), None)
        if linked:
            linked['plateau'] = plateau
            linked['left_index'] = start
        else:
            cases.append({'kind': 'unchanged_reading_during_advancing', 'left_index': start,
                          'right_index': end, 'plateau': plateau})
    for case in cases:
        left, right = case.pop('left_index'), case.pop('right_index')
        block = frame.iloc[left:right+1]
        case['investigation_id'] = ident('PROGRESS', day, _reference(block.iloc[0]), _reference(block.iloc[-1]), case['kind'])
        case['start_time'], case['end_time'] = block.time.iloc[0].isoformat(), block.time.iloc[-1].isoformat()
        case['source_records'] = [_reference(r) for _, r in block.iterrows()]
        case['platform_ids'] = sorted(block.experimental_platform_id.dropna().unique().tolist())
        case['confirmed_observations'] = []
        tr, plateau = case.get('transition'), case.get('plateau')
        affected = {pid for pid, seconds in (plateau or {}).get('platform_advancing_support_seconds', {}).items()
                    if seconds >= policy['minimum_plateau_advancing_seconds']}
        if tr:
            pair = frame.loc[frame.time.isin(pd.to_datetime([tr['left']['time'], tr['right']['time']]))]
            owners = pair.experimental_platform_id
            if len(owners)==2 and owners.notna().all() and owners.nunique()==1:
                affected.add(str(owners.iloc[0]))
        case['affected_platform_ids'] = sorted(affected)
        if plateau:
            case['confirmed_observations'].append('unchanged_cumulative_reading_with_classified_advancing_and_positive_speed')
            if plateau['chainage_also_constant']:
                case['confirmed_observations'].append('guide_shield_reading_unchanged_in_same_records')
        if tr:
            case['confirmed_observations'].append('adjacent_cumulative_forward_jump' if case['kind']=='forward_jump' else 'adjacent_cumulative_backstep')
            if tr['chainage_delta_m'] is not None and abs(tr['chainage_delta_m']) > tolerance:
                case['confirmed_observations'].append('guide_shield_also_changes_in_same_record_pair')
            if not tr['continuous_supported_pair']:
                case['confirmed_observations'].append('record_pair_has_unobserved_time_or_source_boundary')
        delay_supported = bool(plateau and tr and case['kind']=='forward_jump' and
            tr['continuous_supported_pair'] and plateau['chainage_also_constant'] and
            plateau['guide_shield_quality_usable'] and tr['guide_shield_quality_usable'] and
            tr['chainage_delta_m'] is not None and tr['chainage_delta_m'] > 0)
        case['hypotheses'] = [
            {'name': 'delayed_or_batched_position_update',
             'status': 'candidate_with_temporal_evidence' if delay_supported else 'insufficient_evidence',
             'support': list(case['confirmed_observations']) if delay_supported else [],
             'unresolved': 'Cannot distinguish acquisition/export delay, manual counter update, sensor problem or other causes without independent records.'}]
        case['evidence_dependence'] = 'Speed and advancing classification overlap; cumulative advance and guide-shield chainage may share a navigation subsystem. These are not independent ground truth.'
        case['decision'] = {'status': 'withhold_affected_interval_progress_and_rate',
                            'repair_or_allocate_distance': False, 'daily_progress_formula_changed': False}
        case['review_status'] = 'cause_requires_external_confirmation'
    return {'version': policy['version'], 'date': day, 'policy': policy,
        'inherited_progress_quality_policy': quality_policy, 'cases': sorted(cases, key=lambda c:(c['start_time'], c['investigation_id'])),
        'limitations': ['retrospective_daily_context_not_online_detection', 'no_confirmed_cause_or_equipment_fault',
                       'no_speed_integral_replacement_or_distance_allocation', 'no_independent_truth_labels'],
        'field_availability': {'missing_cumulative_records': int(values.isna().sum()),
                              'missing_speed_records': int(speed.isna().sum())}}


def apply_report_decisions(activities, investigation):
    """Withhold suspect report quantities, retaining every upstream value for audit."""
    result = deepcopy(activities)
    changes = []
    for activity in result['activities']:
        hits = [c for c in investigation['cases'] if set(c['affected_platform_ids']) & set(activity['platform_ids'])]
        if not hits:
            continue
        activity['pre_investigation'] = {k: activity[k] for k in (
            'progress_m', 'progress_status', 'rate_m_per_advancing_hour', 'rate_status')}
        activity.update(progress_m=None, progress_status='review_required', rate_m_per_advancing_hour=None,
                        rate_status='not_available', investigation_ids=[c['investigation_id'] for c in hits])
        for component in activity.get('components', []):
            component_hits = [c['investigation_id'] for c in hits if component['platform_id'] in c['affected_platform_ids']]
            if component_hits:
                component['pre_investigation'] = {k: deepcopy(component[k]) for k in ('progress_m', 'status', 'reasons')}
                component.update(progress_m=None, status='review_required', investigation_ids=component_hits)
                component['reasons'] = component['reasons']+['progress_reading_consistency_investigation']
        changes.append({'activity_id': activity['activity_id'], 'before': activity['pre_investigation'],
                        'after': {'progress_m': None, 'rate_m_per_advancing_hour': None},
                        'investigation_ids': activity['investigation_ids']})
    result['selected_activities'] = [deepcopy(a) for a in result['activities'] if a['selected']]
    result['reconciliation']['all_platform_internal_progress_sum_m'] = (
        sum(a['progress_m'] for a in result['activities'])
        if all(a['progress_m'] is not None for a in result['activities']) else None)
    result['progress_investigation'] = {'version': investigation['version'], 'changes': changes,
        'daily_values_unchanged': result['daily'] == activities['daily']}
    return result


def case_text(case):
    parts = []
    if 'plateau' in case:
        p = case['plateau']
        parts.append(f"{p['start']['time'][11:19]}至{p['end']['time'][11:19]}开累进尺读数保持{p['constant_value_m']:.3f}m，"
            f"其中识别推进累计{p['advancing_supported_seconds']/60:.2f}分钟，推进记录速度中位数{p['advancing_speed_median_mm_min']:.2f}mm/min。")
    if 'transition' in case:
        t = case['transition']
        parts.append(f"{t['left']['time'][11:19]}至{t['right']['time'][11:19]}开累进尺由{t['left_value_m']:.3f}m变为{t['right_value_m']:.3f}m，"
                     f"相差{t['delta_m']:+.3f}m，记录间隔{t['elapsed_seconds']:g}秒。")
        if t['chainage_delta_m'] is not None:
            parts.append(f"同一记录对盾首里程变化{t['chainage_delta_m']:+.3f}m。")
        if not t['continuous_supported_pair']:
            parts.append('该记录对存在未观测时间或来源边界，不将跨度内的累计变化认定为瞬时推进。')
    if case['hypotheses'][0]['status'] == 'candidate_with_temporal_evidence':
        parts.append('这些记录支持“位置读数可能延迟或集中更新”的候选解释，具体原因待独立记录确认。')
    else:
        parts.append('现有证据只支持读数一致性疑点，尚不足以确认延迟更新或其他具体原因。')
    parts.append('涉及运行平台的分段进尺和推进效率暂不采用，不分摊突跳距离；全天原统计口径保留。')
    return ''.join(parts)


def enrich_progress(pack, activities, samples, folder):
    """Run before planning and bind investigation facts to their saved evidence."""
    result = investigate_progress(samples, day=pack['date'])
    save(folder/'activity_facts.before_investigation.json', activities)
    revised = apply_report_decisions(activities, result)
    result['report_decisions'] = revised['progress_investigation']
    evidence_file = folder/'progress_investigations.json'
    save(evidence_file, result)
    save(folder/'activity_facts.json', revised)
    source = {'path': str(evidence_file.resolve()), 'sha256': sha(evidence_file)}
    activity_lookup = {a['activity_id']: a for a in revised['activities']}
    for fact in pack['facts']:
        if fact['fact_id'] not in activity_lookup:
            continue
        a = activity_lookup[fact['fact_id']]
        if not a.get('investigation_ids'):
            continue
        fact['data'] = deepcopy(a)
        fact['canonical_text'] = (f"{a['start_time'][11:19]}至{a['end_time'][11:19]}累计推进{a['advancing_display']}，"
            '进尺记录存在一致性疑点，分段进尺及推进效率暂不采用；具体前后记录见对应进尺调查事实。')
        fact['sources'].append(source)
    selected_platforms = {p for a in revised['selected_activities'] for p in a['platform_ids']}
    for case in result['cases']:
        pack['facts'].append({'fact_id': case['investigation_id'], 'section': '2.2', 'canonical_text': case_text(case),
            'data': case, 'sources': [source], 'required': bool(selected_platforms & set(case['affected_platform_ids'])),
            'role': 'data_consistency_investigation_not_confirmed_cause'})
    pack['progress_investigation'] = {'version': result['version'], 'case_count': len(result['cases']),
        'evidence': source, 'numeric_repair_performed': False}
    records = [{**{k:c[k] for k in ('investigation_id', 'kind', 'start_time', 'end_time', 'platform_ids', 'confirmed_observations', 'hypotheses')},
                'summary': case_text(c), 'human_judgment': ''} for c in result['cases']]
    csv_file(folder/'progress_investigations.csv', records, fields=[
        'investigation_id', 'kind', 'start_time', 'end_time', 'platform_ids', 'confirmed_observations', 'hypotheses', 'summary', 'human_judgment'])
    for case in result['cases']:
        time = pd.to_datetime(samples.time)
        start = pd.Timestamp(case['start_time'])-pd.Timedelta(seconds=result['policy']['context_before_seconds'])
        end = pd.Timestamp(case['end_time'])+pd.Timedelta(seconds=result['policy']['context_after_seconds'])
        columns = [c for c in ('time', 'source_file', 'source_row', 'cumulative_advance', 'chainage', 'advance_speed',
            'advance_speed_quality', 'chainage_quality', 'chainage_step_quality', 'machine_state',
            'experimental_platform_id', 'sample_dt_seconds', 'hard_gap_before') if c in samples]
        block = samples.loc[time.between(start, end), columns]
        block.to_csv(folder/(case['investigation_id'].replace(':', '_')+'.evidence.csv'), index=False, encoding='utf-8-sig', float_format='%.17g')
    return revised, result


def investigation_claim_errors(text, supported_facts, enabled):
    """Bounded lexical guards; untested paraphrases remain semantic REVIEW."""
    if not enabled:
        return []
    errors = []
    related = [f for f in supported_facts if f['role'] == 'data_consistency_investigation_not_confirmed_cause']
    candidates = [f for f in related if any(h['status']=='candidate_with_temporal_evidence' for h in f['data']['hypotheses'])]
    for clause in re.split('[。；\n]', text):
        if not re.search('延迟更新|集中更新|集中补记|延后更新|通信故障|采集故障', clause):
            continue
        if re.search('不足以确认|不能确认|无法确认|尚未确认|无法确定|尚不能确定', clause):
            continue
        if not candidates:
            errors.append('update_delay_explanation_without_supporting_investigation')
        if re.search('通信故障|采集故障', clause):
            errors.append('specific_progress_fault_cause_not_established')
        if not re.search('可能|疑似|待核实|待确认|候选', clause):
            errors.append('unconfirmed_progress_cause_stated_as_fact')
    if related and re.search('已修复|已校正|已分摊|已补齐|确定.*故障|证实.*故障', text):
        errors.append('unsupported_progress_repair_or_fault_diagnosis')
    return sorted(set(errors))


def reading_quantity_supported(sentence, value, unit, supported_facts):
    """Permit only measured reading quantities, never an adopted interval rate."""
    if unit != 'm' or not re.search('开累进尺|读数|突跳|跳变', sentence):
        return False
    allowed = []
    for fact in supported_facts:
        if fact['role'] != 'data_consistency_investigation_not_confirmed_cause':
            continue
        case = fact['data']
        if 'plateau' in case:
            allowed.append(case['plateau']['constant_value_m'])
        if 'transition' in case:
            t = case['transition']
            allowed.extend([t['left_value_m'], t['right_value_m'], abs(t['delta_m'])])
            if '盾首' in sentence and t['chainage_delta_m'] is not None:
                allowed.append(abs(t['chainage_delta_m']))
    return any(abs(float(value)-float(f'{x:.3f}'))<1e-7 for x in allowed)
