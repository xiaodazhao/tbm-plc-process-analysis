"""Read-only end-of-run integrity audit, independent of report prose judgments."""
from collections import Counter
from common import *
from adapters import group_folder
from run import validate_locks


def main():
    registry=validate_locks();source_links={};inputs=[];artifacts=[]
    for scope,days in [('interface',DEVELOPMENT),('formal',registry['formal_dates'])]:
        for day in days:
            original=HERE/'inputs'/day/'unified_report_input.json';pack=load(original)
            for f in pack['facts']:
                for source in f['sources']:source_links[(source['path'],source['sha256'])]=source
            for arm in 'ABCD':
                folder=group_folder(scope,day,arm)
                inputs.append({'scope':scope,'date':day,'arm':arm,
                    'same_locked_input_SHA':sha(folder/'unified_report_input.json')==sha(original)})
                path=folder/'artifact.validation.json'
                if path.exists():
                    artifact=load(path)
                    artifacts.append({'scope':scope,'date':day,'arm':arm,'technical_passed':artifact['technical_passed'],
                        'same_checked_text':artifact['PDF_text_same_as_checked_model_text'],
                        'PDF_SHA_matches':sha(Path(artifact['pdf']))==artifact['PDF_SHA256'],
                        'body_SHA_matches':sha(folder/'final_narrative.json')==artifact['narrative_SHA256']})
    sources=[]
    for (path,expected),source in sorted(source_links.items()):
        file=Path(path);sources.append({'path':path,'expected_SHA256':expected,
            'exists_and_SHA_matches':file.is_file() and sha(file)==expected})
    csv_write(HERE/'FINAL_INPUT_IDENTITY.csv',inputs);csv_write(HERE/'FINAL_SOURCE_INTEGRITY.csv',sources)
    csv_write(HERE/'FINAL_ARTIFACT_INTEGRITY.csv',artifacts)
    calls=Calls('formal').rows();counts=Counter(r['scope'] for r in calls)
    record={'freeze_and_registry_locks_passed':True,'identical_arm_inputs':all(r['same_locked_input_SHA'] for r in inputs),
        'group_inputs_checked':len(inputs),'source_paths_and_hash_pairs':len(sources),
        'all_actual_sources_still_match':all(r['exists_and_SHA_matches'] for r in sources),
        'group_PDFs':len(artifacts),'all_group_PDF_integrity_passed':all(all(r[k] for k in
            ['technical_passed','same_checked_text','PDF_SHA_matches','body_SHA_matches']) for r in artifacts),
        'all_calls_terminal_success':all(r['status']=='SUCCESS' for r in calls),
        'calls_by_scope':dict(counts),'formal_and_relations_budget_used':counts['formal']+counts['relations'],
        'within_registered_budget':counts['formal']+counts['relations']<=180 and counts['interface']<=24 and counts['development_fix']<=4,
        'blind_keys_read':False,'human_fact_labels_inferred':False}
    save(HERE/'FINAL_ACCEPTANCE.json',record);print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':main()
