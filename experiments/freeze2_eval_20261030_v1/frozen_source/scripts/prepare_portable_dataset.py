#!/usr/bin/env python3
"""Export existing processed facts for offline report development."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TABLE_NAMES = ("实验平台明细.csv", "实验四阶段明细.csv")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filtered_table(source: Path, target: Path, days: set[str]) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8-sig", newline="") as input_stream:
        reader = csv.DictReader(input_stream)
        if not reader.fieldnames or "date" not in reader.fieldnames:
            raise ValueError(f"Missing date column: {source}")
        with target.open("w", encoding="utf-8-sig", newline="") as output_stream:
            writer = csv.DictWriter(output_stream, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            count = 0
            for row in reader:
                if row["date"] in days:
                    writer.writerow(row)
                    count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--geology-candidate-root", type=Path, required=True)
    parser.add_argument("--geology-catalog-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "datasets/portable_30")
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing dataset: {output}")
    prepared = args.geology_candidate_root / "evidence/prepared_days"
    days = sorted(path.name for path in prepared.iterdir() if path.is_dir())
    if not days:
        raise ValueError(f"No prepared geology days in {prepared}")

    for day in days:
        sources = (
            (args.analysis_dir / "processed_samples" / f"{day}.csv", output / "analysis/processed_samples" / f"{day}.csv"),
            (args.analysis_dir / "structured_full_days" / f"{day}.json", output / "analysis/structured_full_days" / f"{day}.json"),
            (prepared / day / "03_v2_content_units.json", output / "geology/evidence/prepared_days" / day / "03_v2_content_units.json"),
        )
        for source, target in sources:
            if not source.is_file():
                raise FileNotFoundError(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    table_counts = {
        name: filtered_table(args.analysis_dir / "tables" / name, output / "analysis/tables" / name, set(days))
        for name in TABLE_NAMES
    }
    catalog = args.geology_catalog_root / "geology_catalog_right_line.json"
    if not catalog.is_file():
        raise FileNotFoundError(catalog)
    target = output / "geology/extracted/geology_catalog_right_line.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(catalog, target)
    report_root = args.geology_catalog_root / "reports"
    report_files = sorted(report_root.glob("*.json"))
    for source in report_files:
        target = output / "geology/extracted/reports" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    (output / "DAYS.txt").write_text("\n".join(days) + "\n", encoding="utf-8")
    files = sorted(path for path in output.rglob("*") if path.is_file())
    manifest = {
        "schema_version": "tbm_portable_report_dataset_v1",
        "day_count": len(days),
        "days": days,
        "table_row_counts": table_counts,
        "extracted_geology_report_count": len(report_files),
        "raw_plc_or_pdf_included": False,
        "files": [
            {"path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        ],
    }
    (output / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "days": len(days), "files": len(files) + 1, "bytes": sum(row["size_bytes"] for row in manifest["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
