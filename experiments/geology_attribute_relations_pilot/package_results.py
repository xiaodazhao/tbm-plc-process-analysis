"""Package only this experiment; scan credentials and verify every ZIP member."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

from pilot import ROOT, save, sha


def run(output):
    source = Path(__file__)
    dest = output / 'source_snapshot' / source.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    patterns = {
        'api_key': re.compile(r'\bsk-[A-Za-z0-9]{16,}\b'),
        'bearer_value': re.compile(r'(?i)Bearer\s+[A-Za-z0-9_.-]{20,}'),
        'private_key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    }
    violations = []
    for path in output.rglob('*'):
        if not path.is_file():
            continue
        if path.name == '.env' or path.suffix in {'.pem', '.key'}:
            violations.append({'file': str(path.relative_to(output)), 'type': 'credential_file'})
        try:
            text = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            continue
        for name, regex in patterns.items():
            if regex.search(text):
                violations.append({'file': str(path.relative_to(output)), 'type': name})
    save(output / 'security_scan.json', {'status': 'FAIL' if violations else 'PASS', 'violations': violations,
         'scope': '本实验全部交付文件；只检查凭据特征，不输出命中值；不复制.env或盲评文件。'})
    if violations:
        raise RuntimeError('credential_scan_failed_no_zip_created')
    files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'MANIFEST.csv')
    with (output / 'MANIFEST.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['relative_path', 'size_bytes', 'sha256'])
        w.writeheader()
        w.writerows({'relative_path': str(p.relative_to(output)), 'size_bytes': p.stat().st_size, 'sha256': sha(p)} for p in files)
    files.append(output / 'MANIFEST.csv')
    archive = output.parent.parent / 'TBM_TSP_HSP属性关系实验_20261027_交付.zip'
    if archive.exists():
        raise RuntimeError('archive_already_exists_no_overwrite')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            z.write(path, 'TBM_TSP_HSP属性关系实验/' + str(path.relative_to(output)))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for path in files:
            assert hashlib.sha256(z.read('TBM_TSP_HSP属性关系实验/' + str(path.relative_to(output)))).hexdigest() == sha(path)
    archive.with_suffix('.zip.sha256').write_text(sha(archive) + '  ' + archive.name + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'files': len(files),
                      'sha256': sha(archive), 'security': 'PASS', 'zip_member_sha_verification': 'PASS'}, ensure_ascii=False))


if __name__ == '__main__':
    run(Path(sys.argv[1]))
