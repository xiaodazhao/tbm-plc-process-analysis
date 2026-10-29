"""Stage cost accounting and a separate reviewer-only ZIP; no allocation access."""
import zipfile
from common import *


def main():
    calls=Calls('formal').rows();cost=[]
    for arm in 'ABCD':
        own=[r for r in calls if r['scope']=='formal' and r['arm']==arm]
        equivalent=own+[r for r in calls if r['scope']=='formal' and r['arm']=='C'] if arm=='D' else own
        cost.append({'arm':arm,'formal_reports':30,'actual_marginal_calls':len(own),
            'actual_marginal_input_tokens':sum(r.get('input_tokens') or 0 for r in own),
            'actual_marginal_output_tokens':sum(r.get('output_tokens') or 0 for r in own),
            'model_API_seconds_marginal':sum(r.get('duration_seconds') or 0 for r in own),
            'standalone_equivalent_calls':len(equivalent),
            'standalone_equivalent_total_tokens':sum((r.get('input_tokens') or 0)+(r.get('output_tokens') or 0) for r in equivalent),
            'standalone_equivalent_model_seconds':sum(r.get('duration_seconds') or 0 for r in equivalent),
            'local_plotting_and_PDF_time_not_measured_per_arm':True,
            'standalone_is_equivalent_not_additional_actual_HTTP':True})
    csv_write(HERE/'TIMING_AND_COST.csv',cost)
    csv_write(HERE/'WHOLE_DATE_EXECUTION_TIME.csv',[load(p) for p in sorted((HERE/'executions/formal').glob('*/result.json'))])
    target=HERE/'只发给评价者_匿名材料.zip'
    if target.exists():raise RuntimeError('reviewer_zip_not_overwritten')
    files=[]
    for name in ['human_blind','human_relation_review']:
        for p in (HERE/name).rglob('*'):
            if p.is_file() and not p.is_symlink():files.append(p)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(files):z.write(p,str(p.relative_to(HERE)))
    with zipfile.ZipFile(target) as z:crc=z.testzip()
    save(HERE/'REVIEWER_PACKAGE.json',{'path':str(target),'SHA256':sha(target),'bytes':target.stat().st_size,
        'files':len(files),'CRC_error':crc,'sealed_keys_read_or_included':False,
        'author_group_results_included':False,'human_labels_received':0})


if __name__=='__main__':main()
