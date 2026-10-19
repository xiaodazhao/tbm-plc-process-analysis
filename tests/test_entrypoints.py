from __future__ import annotations

import json
import os
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
        "scripts/run_llm_report_experiment.py",
        "scripts/build_daily_report_front_sections.py",
        "scripts/run_final_reports.py",
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


def test_llm_experiment_call_limit_never_substitutes_template_for_llm(
    tmp_path: Path,
) -> None:
    config = json.loads(
        (PROJECT_ROOT / "configs/llm_report_experiment.json").read_text()
    )
    config["source_root"] = str(PROJECT_ROOT / "tests/fixtures/current_llm_inputs")
    config["input_pattern"] = "{date}.llm_input.research.json"
    config["quantitative_pattern"] = "missing/{date}.json"
    config["output_root"] = str(tmp_path / "experiment")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/run_llm_report_experiment.py"),
            "--config",
            str(config_path),
            "--date",
            "2023-11-03",
            "--groups",
            "B",
            "--max-api-calls",
            "0",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", "")},
    )

    date_dir = tmp_path / "experiment/2023-11-03"
    assert completed.returncode == 1
    assert not (date_dir / "B_direct.report.md").exists()
    failure = json.loads((date_dir / "B_direct.run.json").read_text())
    assert failure["status"] == "failed"
    assert failure["note"] == "No template output was substituted for a failed LLM result."
