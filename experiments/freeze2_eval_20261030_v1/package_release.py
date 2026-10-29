"""Compact evidence delivery: immutable group inputs stored once, requests never omitted."""
import gzip
import os
import re
import tempfile
import zipfile
from common import *


def selection():
    files=[];omissions=[];skip={'_sealed','__pycache__','.pytest_cache','.cache','.matplotlib','previews'}
    for folder,dirs,names in os.walk(HERE,followlinks=False):
        dirs[:]=[d for d in dirs if d not in skip and not Path(folder,d).is_symlink()]
        for name in names:
            path=Path(folder)/name;relative=path.relative_to(HERE);parts=relative.parts
            if path.is_symlink() or name.startswith('.env') or name.endswith(('.pyc','-wal','-shm')):continue
            if name in {'MANIFEST.csv','PACKAGE_VERIFICATION.json'}:continue
            if 'preparation_attempts' in parts and name not in {'preparation.result.json','run_configuration.json'}:
                continue
            if len(parts)>=5 and parts[0]=='runs' and parts[1] in {'formal','interface'}:
                day=parts[3];base=HERE/'inputs'/day
                same=base/Path(*parts[4:])
                # Never elide genuine API evidence or a changed artifact.
                evidence=any(tag in name for tag in ['request','response','payload','metadata','validation','result','narrative','plan','revision','comparison'])
                if not evidence and same.is_file() and sha(path)==sha(same):
                    omissions.append({'omitted_group_file':str(relative),'restore_from':str(same.relative_to(HERE)),
                        'same_SHA256':sha(path),'reason':'identical_locked_input_no_information_removed'})
                    continue
            files.append(path)
    return sorted(files),omissions


def main():
    target=ROOT.parent/'TBM_冻结版本正式实验_20261030.zip'
    if target.exists():raise RuntimeError('existing_archive_not_overwritten')
    if not freeze_check()['passed']:raise RuntimeError('frozen_source_changed')
    files,omitted=selection();csv_write(HERE/'SHARED_INPUT_RESTORE.csv',omitted)
    files.append(HERE/'SHARED_INPUT_RESTORE.csv')
    suspects=[];scanned=0
    pattern=re.compile(rb'\bsk-[A-Za-z0-9]{24,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    for path in files:
        if path.suffix in {'.py','.json','.csv','.md','.html','.txt','.log'}:
            data=path.read_bytes();scanned+=1
            if pattern.search(data):suspects.append(str(path.relative_to(HERE)))
        elif path.name.endswith('.csv.gz'):
            scanned+=1
            with gzip.open(path,'rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):
                    if pattern.search(block):suspects.append(str(path.relative_to(HERE)));break
    save(HERE/'SECURITY_SCAN.json',{'passed':not suspects,'scanned_text_or_decompressed_CSV':scanned,
        'suspect_files':suspects,'env_or_credentials_packaged':False,
        'old_and_new_blind_mapping_not_read_or_packaged':True,'symlinked_data_or_model_caches_not_followed':True})
    if suspects:raise RuntimeError('sensitive_material_found_no_archive_created')
    files=[p for p in files if p.name!='SECURITY_SCAN.json']+[HERE/'SECURITY_SCAN.json']
    files=sorted(set(files))
    manifest=[{'path':str(p.relative_to(HERE)),'bytes':p.stat().st_size,'SHA256':sha(p)} for p in files]
    csv_write(HERE/'MANIFEST.csv',manifest);files.append(HERE/'MANIFEST.csv')
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
        for p in files:archive.write(p,HERE.name+'/'+str(p.relative_to(HERE)))
    errors=[]
    with tempfile.TemporaryDirectory(prefix='tbm_freeze2_actual_extract_') as tmp:
        with zipfile.ZipFile(target) as archive:
            crc=archive.testzip();archive.extractall(tmp)
        root=Path(tmp)/HERE.name
        for row in manifest:
            if sha(root/row['path'])!=row['SHA256']:errors.append(row['path'])
        # Every elided per-arm file must resolve to an actual same-hash input.
        for row in omitted:
            if sha(root/row['restore_from'])!=row['same_SHA256']:errors.append(row['restore_from'])
    verification={'archive':str(target),'bytes':target.stat().st_size,'files':len(files),'SHA256':sha(target),
        'CRC_error':crc,'extracted_SHA_mismatches':errors,'shared_input_restorations_checked':len(omitted),
        'passed':crc is None and not errors,'QA_page_previews_omitted_but_original_PDFs_and_figure_data_retained':True,
        'blind_mapping_packaged':False}
    save(HERE/'PACKAGE_VERIFICATION.json',verification)
    (target.parent/(target.name+'.sha256')).write_text(verification['SHA256']+'  '+target.name+'\n',encoding='utf-8')
    print(json.dumps(verification,ensure_ascii=False))


if __name__=='__main__':main()
