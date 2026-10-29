"""Verify an extracted review package without API calls or project dependencies."""
import csv
import hashlib
from pathlib import Path
import sys


def verify(root):
    root = root.resolve()
    with (root/'MANIFEST.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    errors = []
    for row in rows:
        path = root/row['path']
        if root not in path.resolve().parents:
            errors.append(row['path']+': invalid path')
            continue
        if not path.is_file():
            errors.append(row['path']+': missing')
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size != int(row['bytes']) or digest != row['sha256']:
            errors.append(row['path']+': size or SHA mismatch')
    print('Files:', len(rows), 'Errors:', len(errors))
    for error in errors:
        print(error)
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(verify(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parent))
