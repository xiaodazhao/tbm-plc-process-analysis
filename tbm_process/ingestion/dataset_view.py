from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


TIME_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
SUPPORTED_SUFFIXES = frozenset({".csv"})


@dataclass(frozen=True)
class SourceFileRecord:
    source_group: str
    source_path: str
    source_name: str
    analysis_date: str
    first_timestamp: str
    last_timestamp: str
    row_count: int
    column_count: int
    header_sha256: str
    file_sha256: str
    missing_required_fields: tuple[str, ...]
    standardized_name: str


def inspect_source_file(
    path: Path,
    *,
    source_group: str,
    time_field: str,
    required_fields: Iterable[str],
) -> SourceFileRecord:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported source file: {path}")

    digest = hashlib.sha256()
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    row_count = 0
    with path.open("rb") as raw_stream:
        while chunk := raw_stream.read(1024 * 1024):
            digest.update(chunk)

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = tuple(reader.fieldnames or ())
        if time_field not in fields:
            raise ValueError(f"Missing time field {time_field!r}: {path}")
        for row_number, row in enumerate(reader, start=2):
            value = str(row.get(time_field, "")).strip()
            timestamp = _parse_timestamp(value)
            if timestamp is None:
                raise ValueError(
                    f"Invalid timestamp at source row {row_number}: {path}: {value!r}"
                )
            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp
            row_count += 1

    if first_timestamp is None or last_timestamp is None:
        raise ValueError(f"Source file has no data rows: {path}")
    if first_timestamp.date() != last_timestamp.date():
        raise ValueError(
            "One source file spans multiple dates: "
            f"{path}: {first_timestamp.isoformat()} to {last_timestamp.isoformat()}"
        )

    missing = tuple(field for field in required_fields if field not in fields)
    header_sha256 = hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()
    date = first_timestamp.date().isoformat()
    return SourceFileRecord(
        source_group=source_group,
        source_path=str(path.resolve()),
        source_name=path.name,
        analysis_date=date,
        first_timestamp=first_timestamp.isoformat(sep=" "),
        last_timestamp=last_timestamp.isoformat(sep=" "),
        row_count=row_count,
        column_count=len(fields),
        header_sha256=header_sha256,
        file_sha256=digest.hexdigest(),
        missing_required_fields=missing,
        standardized_name=f"tbm_data_{first_timestamp:%Y%m%d}.csv",
    )


def build_dataset_view(
    sources: Iterable[tuple[str, Path]],
    *,
    output_dir: Path,
    time_field: str,
    required_fields: Iterable[str],
) -> dict[str, object]:
    required = tuple(required_fields)
    records: list[SourceFileRecord] = []
    for source_group, source_dir in sources:
        if not source_dir.is_dir():
            raise FileNotFoundError(source_dir)
        candidates = sorted(
            path
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
        for path in candidates:
            records.append(
                inspect_source_file(
                    path,
                    source_group=source_group,
                    time_field=time_field,
                    required_fields=required,
                )
            )

    records.sort(key=lambda row: (row.analysis_date, row.first_timestamp, row.source_path))
    _validate_records(records)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    for record in records:
        link = output_dir / record.standardized_name
        link.symlink_to(record.source_path)

    manifest_rows = [_record_payload(record) for record in records]
    summary = _build_summary(records)
    manifest = {
        "schema_version": "tbm_plc_dataset_view_v1",
        "read_only_source": True,
        "source_files_modified": False,
        "date_source": f"first valid value of {time_field!r} inside each CSV",
        "required_fields": list(required),
        "summary": summary,
        "files": manifest_rows,
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_manifest_csv(output_dir / "dataset_manifest.csv", manifest_rows)
    return manifest


def _record_payload(record: SourceFileRecord) -> dict[str, object]:
    payload = asdict(record)
    payload["missing_required_fields"] = list(record.missing_required_fields)
    return payload


def _validate_records(records: list[SourceFileRecord]) -> None:
    if not records:
        raise ValueError("No PLC CSV files were found")
    by_date: dict[str, list[SourceFileRecord]] = {}
    for record in records:
        by_date.setdefault(record.analysis_date, []).append(record)
        if record.missing_required_fields:
            missing = ", ".join(record.missing_required_fields)
            raise ValueError(f"Missing required fields in {record.source_path}: {missing}")
    duplicates = {date: rows for date, rows in by_date.items() if len(rows) > 1}
    if duplicates:
        details = "; ".join(
            f"{date}: {', '.join(row.source_path for row in rows)}"
            for date, rows in sorted(duplicates.items())
        )
        raise ValueError(f"Duplicate PLC dates: {details}")


def _build_summary(records: list[SourceFileRecord]) -> dict[str, object]:
    dates = [datetime.strptime(row.analysis_date, "%Y-%m-%d").date() for row in records]
    present = set(dates)
    missing_dates: list[str] = []
    cursor = min(dates)
    while cursor <= max(dates):
        if cursor not in present:
            missing_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    source_counts: dict[str, int] = {}
    for row in records:
        source_counts[row.source_group] = source_counts.get(row.source_group, 0) + 1
    return {
        "file_count": len(records),
        "unique_date_count": len(present),
        "date_min": min(dates).isoformat(),
        "date_max": max(dates).isoformat(),
        "missing_calendar_date_count": len(missing_dates),
        "missing_calendar_dates": missing_dates,
        "source_file_counts": source_counts,
        "total_rows": sum(row.row_count for row in records),
        "all_required_fields_present": True,
    }


def _write_manifest_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["missing_required_fields"] = "|".join(
                str(item) for item in out["missing_required_fields"]
            )
            writer.writerow(out)


def _parse_timestamp(value: str) -> datetime | None:
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
