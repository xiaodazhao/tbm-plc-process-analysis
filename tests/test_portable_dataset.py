from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/portable_30"


def test_portable_dataset_manifest_and_inputs() -> None:
    manifest = json.loads((DATASET / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["day_count"] == 30
    assert len(manifest["days"]) == 30
    assert manifest["raw_plc_or_pdf_included"] is False
    for day in manifest["days"]:
        assert (DATASET / "analysis/processed_samples" / f"{day}.csv").is_file()
        assert (DATASET / "analysis/structured_full_days" / f"{day}.json").is_file()
        assert (DATASET / "geology/evidence/prepared_days" / day / "03_v2_content_units.json").is_file()
    for row in manifest["files"]:
        path = DATASET / row["path"]
        assert path.stat().st_size == row["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]


def test_integrated_dry_run_uses_packaged_data_without_api(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_integrated_report.py"),
            "--date", "2024-01-06",
            "--dry-run",
            "--output-dir", str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((tmp_path / "2024-01-06/run_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "dry_run_no_llm_called"
    assert summary["plc_platform_count"] > 0
    assert summary["geology_fact_count"] > 0
