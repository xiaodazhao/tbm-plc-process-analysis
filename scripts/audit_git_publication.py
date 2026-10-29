"""Read-only staged-tree credential, size and frozen-version checks before push."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    entries = git('ls-files', '--stage', '-z').split(b'\0')
    suspects = []; bad_paths = []; large = []; count = 0; size = 0
    pattern = re.compile(rb'\bsk-[A-Za-z0-9]{24,}\b|\bgh[pousr]_[A-Za-z0-9]{30,}\b|'
                         rb'github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    for entry in entries:
        if not entry: continue
        meta, raw_name = entry.split(b'\t', 1)
        mode, blob, stage = meta.decode().split()
        name = raw_name.decode(); parts = Path(name).parts
        if (any(p in {'_sealed', 'local_data', '.cache'} for p in parts)
                or (Path(name).name.startswith('.env') and Path(name).name != '.env.example')
                or re.search(r'(?:allocation|unblind|blind.*key)\.(?:json|csv)$', name, re.I)):
            bad_paths.append(name); continue
        data = git('cat-file', 'blob', blob); count += 1; size += len(data)
        if len(data) >= 100 * 1024 * 1024: large.append(name)
        if pattern.search(data): suspects.append(name)
    release = ROOT / 'release/20261029'
    with (release / 'SOURCE_MANIFEST.csv').open(encoding='utf-8-sig', newline='') as stream:
        original = list(csv.DictReader(stream))
    changed = [row['path'] for row in original
               if not (ROOT / row['path']).is_file() or sha(ROOT / row['path']) != row['sha256']]
    study = ROOT / 'experiments/freeze2_eval_20261030_v1'
    frozen = json.loads((study / 'FREEZE.json').read_text())['source_SHA256']
    changed_snapshot = [name for name, checksum in frozen.items()
                        if sha(study / 'frozen_source' / name) != checksum]
    registry = json.loads((study / 'EXPERIMENT_REGISTRY.json').read_text())
    changed_study = [row['path'] for row in registry['locks']
                     if sha(study / row['path']) != row['sha256']]
    result = {'passed': not (suspects or bad_paths or large or changed or changed_snapshot or changed_study),
              'staged_files_scanned': count, 'staged_total_bytes': size,
              'credential_pattern_findings_filenames_only': suspects,
              'forbidden_paths': bad_paths, 'files_at_or_above_GitHub_100MiB_limit': large,
              'production_freeze_files_checked': len(original), 'production_freeze_changed': changed,
              'candidate_snapshot_files_checked': len(frozen), 'candidate_snapshot_changed': changed_snapshot,
              'registered_study_changed': changed_study,
              'secret_values_printed': False, 'sealed_mapping_contents_read': False,
              'archive_and_private_data_publication': False}
    path = ROOT / 'release/20261030_cleanup/PUBLICATION_AUDIT.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    if not result['passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()
