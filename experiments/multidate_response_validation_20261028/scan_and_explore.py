"""Frozen-label replay, unique spatial membership, and non-inferential summaries."""
from __future__ import annotations

from collections import Counter
import json
import random

import numpy as np
import pandas as pd

from tbm_process.evidence_graph.literal_relations import overlap
from tbm_process.reporting_v2.facts import digest, write_json
from tbm_process.reporting_v2.performance import statistics
from tbm_process.reporting_v2.response_features import BASE, build_response_features, response_samples
from .common import ANALYSIS, HERE, PROJECT, gate_cache, read, source_units, write_csv


def select_dates(rows, protocol):
    rng = random.Random(protocol['seed'])
    pool = sorted([r for r in rows if r['coverage_ratio'] >= protocol['normal_minimum_coverage_ratio']
                   and r['net_change_m'] is not None and r['net_change_m'] > 0
                   and r['spatial_allowed'] and r['qualified_samples'] >= protocol['normal_minimum_stable_samples']
                   and r['date'] not in protocol['known_cases']], key=lambda r:r['date'])
    strata = [list(x) for x in np.array_split(np.array(pool, dtype=object), 3)]
    chosen, formal_normals = [], []
    for i, stratum in enumerate(strata):
        # In later periods prefer real forecast coverage if it is available; this
        # is a pre-generation data stratum, never a selection on report quality.
        rich = [r for r in stratum if r['eligible_forecast_units'] > 0]
        candidates = rich if len(rich) >= 2 else stratum
        selected = rng.sample(candidates, min(2, len(candidates)))
        chosen += [{**r, 'selection_reason': f'seeded_normal_period_stratum_{i+1}', 'selection_role': 'normal'} for r in selected]
        if selected:
            formal_normals.append(selected[0]['date'])
    index = {r['date']:r for r in rows}
    selected = [{**index[day], 'selection_reason': 'predeclared_known_branch_or_development_regression',
                 'selection_role': 'development_regression' if day == '2024-04-26' else 'known_negative_or_missing_evidence'}
                for day in protocol['known_cases'] if day in index] + chosen
    formal = formal_normals + [d for d in protocol['formal_special_cases'] if d in index]
    return {'seed': protocol['seed'], 'normal_pool_dates': [r['date'] for r in pool],
            'candidate_dates': [r['date'] for r in selected], 'formal_dates': formal,
            'selection_records': selected, 'stratum_ranges': [[s[0]['date'],s[-1]['date']] if s else [] for s in strata],
            'not_an_independent_random_test_of_entire_method': True,
            'reason': 'includes_known_development_and_negative_cases; seeded_new_dates_are_additional_functional_validation'}


def scan(output, protocol):
    docs, units, source_exclusions = source_units()
    days = sorted(p.stem for p in (ANALYSIS/'structured_full_days').glob('*.json'))
    gates = gate_cache(docs, days)
    config, phase = read(PROJECT/'tbm_process/reporting_v2/defaults.json'), read(PROJECT/'config.json')
    cache = output/'cache'; cache.mkdir(parents=True, exist_ok=True)
    features_folder = cache/'daily_features'; features_folder.mkdir(exist_ok=True)
    rows, qualified = [], []
    for index, day in enumerate(days):
        raw = read(ANALYSIS/'structured_full_days'/f'{day}.json'); o = raw['overall']
        path = ANALYSIS/'processed_samples'/f'{day}.csv.gz'
        sample = pd.read_csv(path); sample.time = pd.to_datetime(sample.time)
        frame, _ = response_samples(sample, config)
        good = frame.loc[frame.basic_response_eligible].copy()
        bounds = [o['chainage_start'],o['chainage_end']]
        spatial = None not in bounds and str(o['chainage_quality_status']).startswith('usable')
        usable = good.loc[good.chainage_quality.fillna('').str.startswith('usable') & good.chainage.notna()] if spatial else good.iloc[:0]
        # This reuses existing report-window computation over frozen labels. It
        # does not run state recognition, platform detection, or phase fitting.
        features = build_response_features(sample, config, phase, response_id='DAILY:'+day,
                                           bounds=bounds if spatial else None)
        write_json(features_folder/f'{day}.json', features)
        eligible = [u for u in units if spatial and overlap(u['range_m'], sorted(bounds))
                    and gates[(u['document_id'],day)]['status'] == 'eligible']
        row = {'date': day, 'records': len(sample), 'source_samples': str(path), 'samples_sha256': digest(path),
            'start_m': bounds[0], 'end_m': bounds[1], 'net_change_m': o['chainage_change_m'],
            'chainage_quality': o['chainage_quality_status'], 'spatial_allowed': spatial,
            'coverage_ratio': o['data_coverage']['observed_duration_seconds']/86400,
            'observed_seconds': o['data_coverage']['observed_duration_seconds'],
            'advancing_seconds': o['machine_state_duration_seconds'].get('advancing',0),
            'platform_count': len(raw['excavation_cycles']), 'qualified_samples': len(good),
            'qualified_support_seconds': float(good.sample_dt_seconds.fillna(0).sum()),
            'qualified_spatial_samples': len(usable), 'continuous_windows': len(features['windows']['basic']),
            'hard_gap_count': o['data_coverage']['hard_sampling_gap_count'],
            'quality_event_count': o['data_quality']['quality_event_count'],
            'negative_thrust_excluded_samples': int(frame.thrust.lt(0).sum()),
            'eligible_forecast_units': sum(u['role']=='forecast' for u in eligible),
            'eligible_observation_units': sum(u['role']=='observation' for u in eligible),
            'source_types': '|'.join(sorted({u['source_type'] for u in eligible})),
            'cache_features': str(features_folder/f'{day}.json')}
        rows.append(row)
        if len(usable):
            fields = ['time','source_file','source_row','chainage','sample_dt_seconds',*BASE,
                      'rpm_canonical','rpm_quality','experimental_platform_id','experimental_phase_id']
            b = usable[fields].copy(); b['date'] = day
            qualified.append(b)
        if index % 25 == 0 or index == len(days)-1:
            print(json.dumps({'stage':'scan','completed':index+1,'total':len(days),'date':day},ensure_ascii=False),flush=True)
    write_csv(output/'329day_screening.csv', rows)
    frame = pd.concat(qualified, ignore_index=True) if qualified else pd.DataFrame()
    frame.to_csv(cache/'qualified_spatial_samples.csv.gz', index=False, compression='gzip')
    write_json(output/'geology_source_units.json', units)
    write_json(output/'source_inventory.json', {'documents_by_type': dict(Counter(d['document_type'] for d in docs.values())),
        'source_units_by_type':dict(Counter(u['source_type'] for u in units)), 'source_fact_exclusions':source_exclusions,
        'PLC_days': len(days), 'reliable_samples': len(frame), 'not_independent_geological_observations':True})
    selection = select_dates(rows, protocol)
    write_json(output/'date_selection.frozen.json', selection)
    write_csv(output/'candidate_dates.csv', selection['selection_records'])
    write_csv(output/'formal_dates.csv', [{'date':d,'frozen_before_model_calls':True} for d in selection['formal_dates']])
    return rows, frame, docs, units, gates


def cell_index(values, boundaries):
    positions = np.searchsorted(boundaries, values, side='right')-1
    positions = np.where(np.asarray(values)==boundaries[-1], len(boundaries)-2, positions)
    return positions


def integrity_category(unit):
    values = [v['text'] for v in unit['attributes']['integrity'] if not v['locality']]
    if not values:
        return None
    cleaned = []
    for text in values:
        text = text.replace('岩体','').strip()
        for dash in ('～','至','—','~'):
            text = text.replace(dash,'-')
        cleaned.append(text)
    return '|'.join(sorted(set(cleaned)))


def explore(output, protocol, rows=None, frame=None, docs=None, units=None, gates=None):
    rows = rows if rows is not None else pd.read_csv(output/'329day_screening.csv').to_dict('records')
    if docs is None:
        docs, units, _ = source_units()
    frame = frame if frame is not None else pd.read_csv(output/'cache/qualified_spatial_samples.csv.gz')
    gates = gates if gates is not None else gate_cache(docs, sorted(frame.date.unique()))
    dest = output/'B_geology_response_v2'; dest.mkdir(exist_ok=False)
    write_json(dest/'source_document_index.json',docs)
    reliable_bounds = [v for r in rows if r['spatial_allowed'] for v in (r['start_m'],r['end_m']) if pd.notna(v)]
    lo, hi = min(reliable_bounds), max(reliable_bounds)
    candidates = [u for u in units if overlap(u['range_m'],[lo,hi])]
    boundaries = sorted({lo,hi} | {max(lo,min(hi,x)) for u in candidates for x in u['range_m']})
    frame = frame.copy(); frame['cell_id'] = cell_index(frame.chainage.to_numpy(), boundaries)
    assert frame.cell_id.between(0,len(boundaries)-2).all()
    assert not frame.duplicated(['date','source_file','source_row']).any()
    data_rows, source_rows, rejected_rows = [], [], []
    for i,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        block = frame.loc[frame.cell_id.eq(i)]
        sources = [u for u in candidates if overlap(u['range_m'],[a,b])]
        admissible = []
        for unit in sources:
            prior_days = [day for day in block.date.unique() if gates[(unit['document_id'],day)]['status']=='eligible']
            part = block.loc[block.date.isin(prior_days)]
            if not len(part):
                rejected_rows.append({'cell_id':i,'source_unit_id':unit['unit_id'],'reason':'no_PLC_samples_with_source_available_before_day',
                                      'source_type':unit['source_type'],'potential_samples':len(block)})
                continue
            admissible.append((unit,part))
            source_rows.append({'cell_id':i,'cell_start_m':a,'cell_end_m':b,'source_unit_id':unit['unit_id'],
                'document_id':unit['document_id'],'source_type':unit['source_type'],'source_range_m':unit['range_m'],
                'role':unit['role'],'fact_ids':unit['fact_ids'],'original_texts':unit['original_texts'],
                'integrity_category':integrity_category(unit),'matched_records':len(part),
                'supported_seconds':float(part.sample_dt_seconds.fillna(0).sum()),
                'matched_days':sorted(part.date.unique()),'sources':unit['sources'],
                'source_time_metadata':docs[unit['document_id']]['metadata'],
                'shared_PLC_membership_not_additive':True})
        metrics = {k:statistics(block[k]) for k in BASE} if len(block) else {}
        rpm = block.loc[block.rpm_quality.eq('usable_provisional_mapping'),'rpm_canonical'] if len(block) else pd.Series(dtype=float)
        record = {'cell_id':i,'start_m':a,'end_m':b,'width_m':b-a,'sample_count':len(block),
                  'supported_seconds':float(block.sample_dt_seconds.fillna(0).sum()) if len(block) else 0,
                  'days':sorted(block.date.unique()) if len(block) else [],
                  'platform_count':int(block[['date','experimental_platform_id']].drop_duplicates().shape[0]) if len(block) else 0,
                  'stable_phase_count_not_independent_geology':int(block[['date','experimental_phase_id']].drop_duplicates().shape[0]) if len(block) else 0,
                  'metrics':metrics,'rpm':statistics(rpm),'source_unit_ids':list(dict.fromkeys(u['unit_id'] for u,p in admissible)),
                  'source_types':sorted({u['source_type'] for u,p in admissible}),
                  'usable_descriptive':len(block)>=protocol['spatial_comparison_minimum_samples'] and
                                       (float(block.sample_dt_seconds.fillna(0).sum()) if len(block) else 0)>=protocol['spatial_comparison_minimum_support_seconds'],
                  'strict_control_comparability_established':False}
        data_rows.append(record)
    write_json(dest/'disjoint_spatial_cells.json',data_rows)
    write_json(dest/'source_cell_links.json',source_rows)
    write_csv(dest/'source_cell_links.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in source_rows])
    write_csv(dest/'time_or_coverage_rejections.csv',rejected_rows)
    # A separate per-source-type view avoids pooling overlapping TSP/HSP/sketch
    # evidence as if they created additional independent PLC observations.
    grouped = []
    for cell in data_rows:
        if not cell['usable_descriptive']:
            continue
        block = frame.loc[frame.cell_id.eq(cell['cell_id'])]
        for kind in ('TSP','HSP','SKETCH'):
            links = [r for r in source_rows if r['cell_id']==cell['cell_id'] and r['source_type']==kind]
            if not links:
                continue
            categories = sorted({r['integrity_category'] for r in links if r['integrity_category']})
            category = categories[0] if len(categories)==1 else '同类来源描述不同' if categories else '未明确完整性'
            days = {d for r in links for d in r['matched_days']}
            eligible = block.loc[block.date.isin(days)]
            if len(eligible)<protocol['spatial_comparison_minimum_samples'] or eligible.sample_dt_seconds.fillna(0).sum()<protocol['spatial_comparison_minimum_support_seconds']:
                continue
            grouped.append({'cell_id':cell['cell_id'],'start_m':cell['start_m'],'end_m':cell['end_m'],
                'source_type':kind,'integrity_category':category,'raw_categories':categories,
                'document_ids':sorted({r['document_id'] for r in links}), 'source_unit_ids':sorted({r['source_unit_id'] for r in links}),
                'sample_count':len(eligible),'support_seconds':float(eligible.sample_dt_seconds.fillna(0).sum()),
                **{k+'_median':float(eligible[k].median()) for k in BASE},
                **{k+'_p10':float(eligible[k].quantile(.1)) for k in BASE},
                **{k+'_p90':float(eligible[k].quantile(.9)) for k in BASE},
                'rpm_median':float(eligible.loc[eligible.rpm_quality.eq('usable_provisional_mapping'),'rpm_canonical'].median()),
                'not_independent_from_adjacent_cells_or_shared_source_document':True})
    write_json(dest/'attribute_response_rows.json',grouped)
    write_csv(dest/'attribute_response_rows.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in r.items()} for r in grouped])
    aggregate = []
    df = pd.DataFrame(grouped)
    for (kind,category),g in (df.groupby(['source_type','integrity_category']) if len(df) else []):
        aggregate.append({'source_type':kind,'integrity_category':category,'disjoint_cells':len(g),
            'source_documents':len({d for ds in g.document_ids for d in ds}),
            'source_units':len({d for ds in g.source_unit_ids for d in ds}),
            **{k+'_cell_median_distribution':statistics(g[k+'_median']) for k in BASE},
            'weighting':'one_cell_one_value_not_PLC_points','inferential_status':'not_established_no_verified_controls_or_independent_labels'})
    write_json(dest/'group_descriptive_statistics.json',aggregate)
    pairs = []
    by_id={r['cell_id']:r for r in data_rows}
    for i in range(1,len(boundaries)-1):
        left,right=by_id[i-1],by_id[i]
        if not left['usable_descriptive'] or not right['usable_descriptive']:
            continue
        linked={r['source_unit_id'] for r in source_rows if r['cell_id'] in (i-1,i)}
        boundary_units=[u for u in candidates if boundaries[i] in u['range_m'] and u['unit_id'] in linked]
        if not boundary_units:
            continue
        doc_ids={u['document_id'] for u in boundary_units}
        admitted=[]
        for cell in (left,right):
            block=frame.loc[frame.cell_id.eq(cell['cell_id'])]
            days=[d for d in block.date.unique() if any(gates[(did,d)]['status']=='eligible' for did in doc_ids)]
            part=block.loc[block.date.isin(days)]
            admitted.append(part)
        if any(len(b)<protocol['spatial_comparison_minimum_samples'] or b.sample_dt_seconds.fillna(0).sum()<protocol['spatial_comparison_minimum_support_seconds'] for b in admitted):
            continue
        rpm0,rpm1=[b.loc[b.rpm_quality.eq('usable_provisional_mapping'),'rpm_canonical'].median() for b in admitted]
        delta = abs(rpm1/rpm0-1) if rpm0 and rpm1 else None
        pairs.append({'boundary_m':boundaries[i],'left_cell_id':i-1,'right_cell_id':i,
            'source_boundary_units':[u['unit_id'] for u in boundary_units], 'source_document_ids':sorted(doc_ids),
            'left_admitted_records':len(admitted[0]),'right_admitted_records':len(admitted[1]),
            'time_policy':'boundary_sources_available_before_each_admitted_PLC_day',
            'rpm_relative_difference':delta,
            'rpm_diagnostic_passed':delta is not None and delta<=protocol['rpm_relative_tolerance_diagnostic_only'],
            **{k+'_percent':100*(admitted[1][k].median()/admitted[0][k].median()-1)
               if admitted[0][k].median() else None for k in BASE},
            'strict_control_comparability_established':False,'not_verified_geological_boundary':True})
    write_csv(dest/'boundary_descriptive_pairs.csv',pairs)
    counterexamples=[]
    for (kind,cat),g in (df.groupby(['source_type','integrity_category']) if len(df) else []):
        if len(g)<2 or cat in ('未明确完整性','同类来源描述不同'):
            continue
        for _,a in g.iterrows():
            for _,b in g.loc[g.cell_id.gt(a.cell_id)].iterrows():
                if set(a.source_unit_ids)&set(b.source_unit_ids):
                    continue
                if set(a.document_ids)&set(b.document_ids):
                    continue
                rpmrel=abs(b.rpm_median/a.rpm_median-1) if a.rpm_median and pd.notna(a.rpm_median) else None
                if rpmrel is None or rpmrel>protocol['rpm_relative_tolerance_diagnostic_only']:
                    continue
                counterexamples.append({'source_type':kind,'category':cat,'left_cell_id':int(a.cell_id),'right_cell_id':int(b.cell_id),
                    'left_range_m':[a.start_m,a.end_m],'right_range_m':[b.start_m,b.end_m],
                    'speed_medians':[a.advance_speed_median,b.advance_speed_median],
                    'speed_difference':b.advance_speed_median-a.advance_speed_median,'rpm_relative_difference':rpmrel,
                    'different_source_units':True,'strict_control_comparability_established':False})
    counterexamples.sort(key=lambda r:-abs(r['speed_difference']))
    write_json(dest/'similar_description_different_response_examples.json',counterexamples[:10])
    used_units={r['source_unit_id'] for r in source_rows}
    per_unit={uid:[r for r in source_rows if r['source_unit_id']==uid] for uid in used_units}
    sufficient_units={uid for uid,links in per_unit.items()
                      if sum(r['matched_records'] for r in links)>=protocol['spatial_comparison_minimum_samples']
                      and sum(r['supported_seconds'] for r in links)>=protocol['spatial_comparison_minimum_support_seconds']}
    spatial_keys={(r.date,r.source_file,int(r.source_row)) for r in frame.itertuples()}
    retained_windows=0
    for r in rows:
        features=read(output/'cache/daily_features'/f"{r['date']}.json")
        retained_windows+=sum(all((r['date'],s['source_file'],int(row)) in spatial_keys
                                 for s in w['source'] for row in s['source_rows']) for w in features['windows']['basic'])
    summary={'reliable_PLC_range_m':[lo,hi],'unique_PLC_records':len(frame),
        'all329_existing_daily_contiguous_windows':sum(r['continuous_windows'] for r in rows),
        'fully_supported_spatial_daily_windows':retained_windows,
        'original_source_intervals_in_range':len(candidates),
        'original_source_intervals_with_time_admissible_samples':len(used_units),
        'source_interval_counts_by_type':dict(Counter(u['source_type'] for u in candidates)),
        'time_admissible_source_interval_counts_by_type':dict(Counter(u['source_type'] for u in candidates if u['unit_id'] in used_units)),
        'source_intervals_meeting_sample_and_support_minimum_by_type':dict(Counter(u['source_type'] for u in candidates if u['unit_id'] in sufficient_units)),
        'original_documents_with_samples':len({r['document_id'] for r in source_rows}),
        'disjoint_spatial_cells_total':len(data_rows),'disjoint_cells_with_samples':sum(r['sample_count']>0 for r in data_rows),
        'disjoint_cells_with_sufficient_samples_and_support':sum(r['usable_descriptive'] for r in data_rows),
        'disjoint_cells_with_time_admissible_geology':sum(bool(r['source_unit_ids']) and r['usable_descriptive'] for r in data_rows),
        'strict_control_comparable_cells':0,'boundary_pairs':len(pairs),
        'boundary_pairs_passing_RPM_diagnostic':sum(r['rpm_diagnostic_passed'] for r in pairs),
        'counterexample_pairs_passing_RPM_diagnostic':len(counterexamples),
        'independent_geological_labels_confirmed':0,
        'independent_unit_status':'nonoverlapping_cells_are_not_statistically_independent_due_to_shared_sources_and_spatial_continuity',
        'inference_stopped':True,'stop_reason':'control_setpoints_and_face_alignment_unverified; source_shared_cells_not_independent_ground_truth',
        'no_future_source_admitted_to_primary_group_statistics':True,
        'unique_spatial_membership_verified':True,'dense_PLC_rows_not_geological_sample_size':True}
    write_json(dest/'summary.json',summary)
    return summary
