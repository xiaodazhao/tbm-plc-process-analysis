"""Remove superseded workspaces only after checking the retained evidence archives."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / 'experiments/freeze2_eval_20261030_v1'
RECORD = ROOT / 'release/20261030_cleanup'
OLD = [ROOT / 'experiments' / name for name in (
    'frozen_eval_20261029_v1', 'reliability_repair_20261029_v2',
    'stability_delivery_20261029_v3', 'stability_30day_20261029_v1',
    'practical_report_policy_20261030_v1',
)]
ARCHIVE = Path.home() / 'TBM历史归档/20261030_清理前审计证据.zip'
PRIVATE = Path.home() / 'TBM私有评价资料/20261030'
SENSITIVE = re.compile(r'sealed|unblind|blind.*key|group.*key|密钥', re.I)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(name, value):
    RECORD.mkdir(parents=True, exist_ok=True)
    (RECORD / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def walk(root):
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()
                   and d not in {'__pycache__', '.pytest_cache', '.cache', '.matplotlib'}
                   and not SENSITIVE.search(d)]
        for name in names:
            path = Path(directory) / name
            if (not path.is_symlink() and not SENSITIVE.search(name)
                    and not name.startswith('.env') and not name.endswith(('.pyc', '.pem', '.key'))
                    and name != '.DS_Store'):
                yield path


def main():
    if (RECORD / 'CLEANUP_RESULT.json').exists():
        raise RuntimeError('cleanup_already_recorded_do_not_repeat')
    verification = json.loads((LATEST / 'PACKAGE_VERIFICATION.json').read_text())
    delivery = Path(verification['archive'])
    if not verification['passed'] or digest(delivery) != verification['SHA256']:
        raise RuntimeError('latest_evidence_delivery_missing_or_changed')
    present = [p for p in OLD if p.is_dir()]
    if ARCHIVE.exists():
        raise RuntimeError('history_archive_already_exists_no_overwrite')
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    paths = [p for root in present for p in walk(root)]
    # Historical failures are evidence, not disposable successful-run caches.
    manifest = []
    with zipfile.ZipFile(ARCHIVE, 'x', zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
        for n, path in enumerate(paths):
            manifest.append({'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
                             'sha256': digest(path)})
            archive.write(path, str(path.relative_to(ROOT)))
            if n % 1000 == 0:
                print('archiving', n, 'of', len(paths), flush=True)
    with zipfile.ZipFile(ARCHIVE) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('history_archive_crc_failed_no_deletion')
        for row in manifest:
            h = hashlib.sha256()
            with archive.open(row['path']) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != row['sha256']:
                raise RuntimeError('history_archive_hash_failed_no_deletion')
    save('HISTORY_ARCHIVE.json', {'archive': str(ARCHIVE), 'sha256': digest(ARCHIVE),
         'files': len(manifest), 'bytes': ARCHIVE.stat().st_size,
         'CRC_and_all_member_SHA256_verified': True,
         'sealed_mapping_contents_not_read_or_archived': True})
    with (RECORD / 'HISTORY_ARCHIVE_MANIFEST.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['path', 'bytes', 'sha256'])
        writer.writeheader(); writer.writerows(manifest)
    # Move opaque private allocations, never open or hash their contents.
    moved = []
    for root in present:
        for directory, dirs, names in os.walk(root, followlinks=False):
            for name in list(dirs) + names:
                path = Path(directory) / name
                if SENSITIVE.search(name) or name.startswith('.env') or name.endswith(('.pem', '.key')):
                    destination = PRIVATE / root.name / path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        raise RuntimeError('private_destination_exists_no_overwrite')
                    shutil.move(str(path), str(destination))
                    moved.append(str(destination))
                    if name in dirs: dirs.remove(name)
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
    removed = []
    for root in present:
        shutil.rmtree(root)
        removed.append(str(root.relative_to(ROOT)))
    # Discard only per-arm inputs independently proven identical to retained input.
    duplicates = 0; reclaimed = 0
    with (LATEST / 'SHARED_INPUT_RESTORE.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            path = LATEST / row['omitted_group_file']
            source = LATEST / row['restore_from']
            if not path.exists(): continue
            if not path.is_relative_to(LATEST) or not source.is_relative_to(LATEST):
                raise RuntimeError('restore_path_outside_experiment')
            if digest(path) != row['same_SHA256'] or digest(source) != row['same_SHA256']:
                raise RuntimeError('duplicate_input_hash_mismatch')
            reclaimed += path.stat().st_size
            path.unlink()
            path.symlink_to(os.path.relpath(source, path.parent))
            duplicates += 1
    caches = 0
    for directory, dirs, names in os.walk(ROOT, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {'.git', 'local_data', 'datasets', 'repaired_raw',
                    'repair_provenance', 'replay', 'local_embedding_models', '_sealed'}
                   and not (Path(directory) / d).is_symlink()]
        for name in list(dirs):
            if name in {'__pycache__', '.pytest_cache', '.matplotlib', 'previews'}:
                shutil.rmtree(Path(directory) / name); dirs.remove(name); caches += 1
        for name in names:
            if name == '.DS_Store':
                (Path(directory) / name).unlink(); caches += 1
    save('CLEANUP_RESULT.json', {
        'removed_old_workspace_directories': removed,
        'history_preserved_only_in_verified_archive': str(ARCHIVE),
        'private_allocations_moved_without_reading_contents': moved,
        'latest_delivery_archive_unchanged': digest(delivery) == verification['SHA256'],
        'identical_group_input_copies_replaced_by_relative_links': duplicates,
        'logical_duplicate_bytes_removed_hardlinks_may_reduce_physical_savings': reclaimed,
        'cache_or_preview_entries_removed': caches,
        'production_algorithms_changed': False,
        'latest_experiment_methods_or_outcomes_changed': False,
    })
    print('cleanup completed', json.loads((RECORD / 'CLEANUP_RESULT.json').read_text()), flush=True)


if __name__ == '__main__':
    main()
