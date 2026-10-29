"""Anonymous evidence-only review of completed relation pairs; no inferred judgments."""
import random
import secrets
from common import *


def main():
    out=HERE/'human_relation_review'
    if out.exists():raise RuntimeError('relation_review_not_reallocated')
    out.mkdir();seed=secrets.randbits(128);rng=random.Random(seed);rows=[];mapping=[]
    for index,day in enumerate(load(HERE/'EXPERIMENT_REGISTRY.json')['relation_dates'],1):
        files={arm:HERE/'relations'/day/arm/'result.json' for arm in ['REL+','REL-']}
        if not all(p.exists() and load(p)['status']=='GENERATED' for p in files.values()):continue
        arms=['REL+','REL-'];rng.shuffle(arms);left,right=[load(files[a])['payload'] for a in arms]
        task=load(HERE/'prepared_requests'/day/'relation.tasks.json')
        ident='PAIR-'+f'{index:03d}'+'-'+f'{rng.getrandbits(24):06x}'
        mapping.append({'pair_id':ident,'date':day,'left':arms[0],'right':arms[1]})
        rows.append({'pair_id':ident,'date':day,'same_original_components':task['components'],
            'left_text':left.get('text',json.dumps(left,ensure_ascii=False)),
            'right_text':right.get('text',json.dumps(right,ensure_ascii=False)),
            'left_source_IDs':left.get('source_ids',[]),'right_source_IDs':right.get('source_ids',[]),
            'left_supported':'','right_supported':'','left_important_omissions':'','right_important_omissions':'',
            'left_unsupported_inference':'','right_unsupported_inference':'','useful_extra_information':'',
            'preferred_for_report':'','reviewer_id':'','reason':''})
    for reviewer in ['reviewer_1','reviewer_2']:csv_write(out/reviewer/'pairs.csv',rows)
    save(out/'STATUS.json',{'completed_pairs':len(rows),'labels_received':0,'semantic_effect':'NOT_EVALUATED',
        'allocation_commitment':digest(seed),'compiled_relationship_not_used_as_human_truth':True})
    (out/'README.md').write_text('同一对段落使用完全相同的基础组件。请检查来源实质支持、局部里程、资料性质、重要信息和新增解释。左右顺序随机，不显示方法。不要因为关系多就判更好；所有标签空白。仅向评价者提供本目录，不给作者完整A-D/REL组结果。\n',encoding='utf-8')
    save(HERE/'_sealed/relation_allocation.json',{'seed':seed,'mapping':mapping})
    (HERE/'_sealed/relation_allocation.json').chmod(0o600)
    log('anonymous relation review created; actual pairs='+str(len(rows)))


if __name__=='__main__':main()
