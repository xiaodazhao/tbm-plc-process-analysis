from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tbm_process.ingestion.dataset_view import build_dataset_view


FIELDS = ("运行时间-time", "掘进状态", "导向盾首里程", "推进速度")


def _write_day(path: Path, date: str, *, include_speed: bool = True) -> None:
    fields = list(FIELDS if include_speed else FIELDS[:-1])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for clock in ("00:00:10", "00:00:30"):
            row = {
                "运行时间-time": f"{date} {clock}",
                "掘进状态": "1",
                "导向盾首里程": "1013901",
                "推进速度": "20",
            }
            writer.writerow({key: row[key] for key in fields})


def test_build_dataset_view_uses_internal_dates_and_standardizes_names(tmp_path: Path) -> None:
    part1 = tmp_path / "part1"
    part2 = tmp_path / "part2"
    part1.mkdir()
    part2.mkdir()
    _write_day(part1 / "tbm_data (100).csv", "2024/04/10")
    _write_day(part2 / "export - arbitrary.csv", "2024/04/11")

    output = tmp_path / "view"
    result = build_dataset_view(
        (("part1", part1), ("part2", part2)),
        output_dir=output,
        time_field="运行时间-time",
        required_fields=FIELDS,
    )

    assert result["summary"]["file_count"] == 2
    assert result["summary"]["date_min"] == "2024-04-10"
    assert result["summary"]["date_max"] == "2024-04-11"
    assert (output / "tbm_data_20240410.csv").is_symlink()
    assert (output / "tbm_data_20240411.csv").is_symlink()


def test_build_dataset_view_rejects_duplicate_internal_dates(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_day(source / "one.csv", "2024/04/10")
    _write_day(source / "two.csv", "2024/04/10")

    with pytest.raises(ValueError, match="Duplicate PLC dates"):
        build_dataset_view(
            (("source", source),),
            output_dir=tmp_path / "view",
            time_field="运行时间-time",
            required_fields=FIELDS,
        )


def test_build_dataset_view_rejects_missing_required_fields(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_day(source / "one.csv", "2024/04/10", include_speed=False)

    with pytest.raises(ValueError, match="Missing required fields"):
        build_dataset_view(
            (("source", source),),
            output_dir=tmp_path / "view",
            time_field="运行时间-time",
            required_fields=FIELDS,
        )
