"""Distribute actual cited PDF pages and small source tables, without modifying originals."""
from collections import defaultdict
import shutil
import fitz
from common import *


def main():
    root=HERE/'source_excerpts';root.mkdir(exist_ok=False)
    references=csv_read(HERE/'SOURCE_EVIDENCE_AUDIT.csv');groups=defaultdict(list)
    for row in references:
        groups[(row['original_file'],row['sha256'],row['PDF_physical_page'])].append(row)
    entries=[]
    for (name,expected,page),links in sorted(groups.items()):
        path=Path(name);record={'original_file':name,'original_SHA256':expected,'original_PDF_physical_page':page,
            'referencing_dates':sorted({r['date'] for r in links}),
            'referencing_fact_IDs':sorted({r['fact_id'] for r in links}),
            'source_link_count':len(links),'new_excerpt_is_not_the_original_full_file':True}
        if not path.is_file() or sha(path)!=expected:
            record['status']='SOURCE_NOT_AVAILABLE_OR_CHANGED'
        elif path.suffix.lower()=='.pdf' and page:
            with fitz.open(path) as source:
                n=int(page)-1
                if n<0 or n>=len(source):record['status']='PAGE_OUT_OF_RANGE'
                else:
                    target=root/(expected[:16]+'_p'+f'{n+1:03d}'+'.pdf')
                    with fitz.open() as excerpt:
                        excerpt.insert_pdf(source,from_page=n,to_page=n)
                        excerpt.set_metadata({'title':path.name+' - original PDF physical page '+str(n+1),
                            'subject':'Unmodified source-page content; full original SHA256: '+expected})
                        excerpt.save(target,garbage=4,deflate=True)
                    record.update(status='ACTUAL_SOURCE_PAGE_EXPORTED',excerpt=str(target.relative_to(HERE)),
                                  excerpt_SHA256=sha(target))
        elif path.suffix.lower() in {'.json','.xlsx'} and path.stat().st_size<5*1024*1024:
            target=root/(expected[:16]+path.suffix.lower())
            if not target.exists():shutil.copy2(path,target)
            record.update(status='SMALL_ORIGINAL_SOURCE_COPIED',excerpt=str(target.relative_to(HERE)),excerpt_SHA256=sha(target))
        else:record['status']='INDEX_ONLY_large_or_already_in_processed_inputs'
        entries.append(record)
    csv_write(HERE/'SOURCE_EXCERPT_INDEX.csv',entries)
    save(root/'STATUS.json',{'items':len(entries),
        'PDF_pages':sum(r['status']=='ACTUAL_SOURCE_PAGE_EXPORTED' for r in entries),
        'small_original_sources':sum(r['status']=='SMALL_ORIGINAL_SOURCE_COPIED' for r in entries),
        'original_sources_overwritten':False,'excerpt_SHA_not_conflated_with_full_original_SHA':True})
    log('real source pages and source index exported')


if __name__=='__main__':main()
