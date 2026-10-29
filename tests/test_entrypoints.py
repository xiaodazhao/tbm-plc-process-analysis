from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_output_writer_is_importable() -> None:
    from tbm_process.outputs.writer import write_outputs

    assert callable(write_outputs)


@pytest.mark.parametrize(
    "relative_path",
    [
        "run_analysis.py",
        "scripts/run_current_analysis.py",
        "scripts/run_integrated_report.py",
        "scripts/prepare_portable_dataset.py",
        "scripts/run_engineering_report_v2.py",
        "scripts/run_three_chapter_report.py",
        "scripts/verify_engineering_report_v2.py",
        "scripts/run_evidence_graph_report.py",
        "scripts/run_response_relation_pilot.py",
        "scripts/audit_response_relation_pilot.py",
        "scripts/build_geology_store.py",
        "scripts/query_geology_store.py",
        "scripts/serve_geology_store.py",
        "scripts/audit_geology_store.py",
        "scripts/export_stable_platform_samples.py",
        "scripts/audit_guidance_chainage.py",
        "scripts/build_plc_dataset_view.py",
    ],
)
def test_formal_cli_help_imports_are_complete(relative_path: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / relative_path), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
