"""Descriptive cohort counts from the real read-only database and locked input audit."""
import math
from common import *


def main():
    database=PATHS['database']
    con=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    documents=[dict(r) for r in con.execute('SELECT document_type,count(*) AS documents FROM documents GROUP BY document_type')]
    span=con.execute('SELECT min(date),max(date),count(*) FROM plc_days').fetchone()
    scope=con.execute('SELECT min(min(start_m,end_m)),max(max(start_m,end_m)),count(*) FROM plc_days WHERE spatial_allowed=1').fetchone()
    con.close()
    rows=csv_read(HERE/'CANDIDATE_POOL.csv');sample=load(HERE/'SAMPLING.json')
    result={'database':str(database),'database_SHA256':sha(database),
        'PLC_date_start':span[0],'PLC_date_end':span[1],'PLC_days':span[2],
        'spatial_allowed_days':scope[2],'spatial_allowed_chainage_envelope_m':[scope[0],scope[1]],
        'database_document_counts_not_new_PDF_reextraction':documents,
        'processed_sample_count_from_existing_verified_inventory':sum(int(r['sample_count']) for r in rows if r.get('sample_count')),
        'current_unexposed_available_pool':sample['pool_count'],'formal_sample_dates':len(sample['formal_dates']),
        'interface_development_dates':len(sample['interface_dates']),
        'current_sketch_JSON_refresh_may_differ_from_database_counts':True,
        'historical_interpretation':'data span and counts do not prove continuous monitoring or accurate stage recognition'}
    save(HERE/'DATASET_DESCRIPTION.json',result);print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
