"""Score only actual reviewer entries; never infer labels or read allocation keys."""
from collections import Counter
from datetime import datetime,timezone
import csv
import hashlib
import json
import math
from pathlib import Path

LABELS={'SUPPORTED','CONTRADICTED','UNSUPPORTED','INSUFFICIENT_EVIDENCE','NON_FACTUAL'}


def rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def support_summary(data):
    values=[]
    for row in data:
        label=row.get('support_label','').strip()
        if label and label not in LABELS:raise ValueError('unknown_human_label')
        if label:values.append(label)
    count=Counter(values);s,c,u,i=(count[x] for x in ['SUPPORTED','CONTRADICTED','UNSUPPORTED','INSUFFICIENT_EVIDENCE'])
    judged=s+c+u;all_factual=judged+i
    return {'status':'PARTIAL' if values and len(values)<len(data) else 'EVALUATED' if values else 'NOT_EVALUATED',
        'assigned_rows':len(data),'labeled_rows':len(values),'counts':dict(count),
        'supported_among_determinable':s/judged if judged else None,
        'insufficient_evidence_ratio':i/all_factual if all_factual else None,
        'conservative_supported':s/all_factual if all_factual else None,
        'not_a_group_effect_without_authorized_unblinding':True}


def agreement(left,right):
    from sklearn.metrics import cohen_kappa_score
    a={r['candidate_claim_id']:r['support_label'].strip() for r in left if r.get('support_label','').strip()}
    b={r['candidate_claim_id']:r['support_label'].strip() for r in right if r.get('support_label','').strip()}
    common=sorted(set(a)&set(b))
    if not common:return {'status':'NOT_EVALUATED','paired_claims':0,'cohen_kappa':None}
    if any(x not in LABELS for x in list(a.values())+list(b.values())):raise ValueError('unknown_human_label')
    value=float(cohen_kappa_score([a[k] for k in common],[b[k] for k in common]))
    return {'status':'EVALUATED_EXACT_ID_MATCH_ONLY','paired_claims':len(common),
            'cohen_kappa':value if math.isfinite(value) else None,
            'unmatched_split_claims_require_manual_alignment':len(set(a)^set(b))}


def main(root):
    root=Path(root);r1=rows(root/'reviewer_1/claims.csv');r2=rows(root/'reviewer_2/claims.csv')
    result={'reviewer_1':support_summary(r1),'reviewer_2':support_summary(r2),'agreement':agreement(r1,r2),
        'actual_human_labels_only':True,'allocation_key_not_read':True,
        'adjudicated_gold':'NOT_PROVIDED','A_D_method_comparison':'NOT_UNBLINDED',
        'coverage_and_ratings':'retain_in_original_CSV_for_independent_applicability_review; do_not_infer_from_required_IDs'}
    out=root.parent/'human_scoring/results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(out),'labels':result['reviewer_1']['labeled_rows']+result['reviewer_2']['labeled_rows'],
                     'allocation_key_not_read':True}))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('blind_directory',type=Path);a=p.parse_args();main(a.blind_directory)
