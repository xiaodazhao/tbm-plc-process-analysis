"""Enrich the new, still-unrated blind packet; never access allocation files."""
import shutil
from common import *


def main():
    root=HERE/'human_blind'
    if not (root/'STATUS.json').exists():raise RuntimeError('create_new_blind_packet_first')
    if (root/'ASSET_PROVENANCE.json').exists():raise RuntimeError('review_assets_not_overwritten')
    days=set(load(HERE/'SAMPLING.json')['human_dates'])
    excerpts=csv_read(HERE/'SOURCE_EXCERPT_INDEX.csv');selected=[]
    for r in excerpts:
        if not days.intersection(load_list(r['referencing_dates'])):continue
        if r.get('excerpt'):
            source=HERE/r['excerpt'];target=root/'evidence_pages'/source.name
            target.parent.mkdir(exist_ok=True)
            if not target.exists():shutil.copy2(source,target)
            r={**r,'blind_packet_excerpt':'evidence_pages/'+source.name}
        selected.append(r)
    csv_write(root/'SOURCE_EXCERPT_INDEX.csv',selected)
    stripped=[]
    for day in sorted(days):
        evidence=root/'evidence'/(day+'.json')
        if not evidence.exists():continue
        value=load(evidence)
        for fact in value['facts']:
            if 'required' in fact:fact.pop('required');stripped.append({'date':day,'fact_id':fact['fact_id']})
        save(evidence,value)
        source=HERE/'inputs'/day/'processed_samples.csv.gz'
        if source.exists():shutil.copy2(source,root/'evidence'/(day+'.processed_samples.csv.gz'))
    save(root/'ASSET_PROVENANCE.json',{'actual_original_source_pages':sum(bool(r.get('excerpt')) for r in selected),
        'date_count':len(days),'generator_required_flags_removed':len(stripped),
        'purpose':'keep independent importance review distinct from generator priorities',
        'original_experiment_inputs_changed':False,'allocation_key_accessed':False,
        'human_labels_autofilled':False})


def load_list(text):
    return json.loads(text)


if __name__=='__main__':main()
