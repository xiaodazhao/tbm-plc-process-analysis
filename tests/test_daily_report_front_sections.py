from __future__ import annotations

from pathlib import Path
import pandas as pd

from tbm_process.final_reporting.common import load_deepseek_api_key
from tbm_process.reporting.front_sections import (
    ReportSources,
    _interval_advance_quality,
    build_front_sections_facts,
    format_dk,
    interval_table_frame,
    load_report_config,
    validate_front_sections_facts,
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_report_config(PROJECT_ROOT / "configs" / "daily_report_front_sections.json")


def _fixture_sources() -> ReportSources:
    fixture = PROJECT_ROOT / "tests" / "fixtures" / "daily_report"
    return ReportSources(
        samples=fixture / "processed_samples.csv",
        platforms=fixture / "platforms.csv",
        phases=fixture / "phases.csv",
    )


def test_format_dk() -> None:
    assert format_dk(1013901) == "DK1013+901"


def test_interval_advance_rejects_reset() -> None:
    frame = pd.DataFrame(
        {
            "cumulative_advance": [10.0, 10.0625, 0.0, 0.0625],
            "source_row": [2, 3, 4, 5],
        }
    )
    result = _interval_advance_quality(frame, CONFIG["advance_quality"])
    assert result["use_status"] == "review_required"
    assert result["reset_count"] == 1
    assert 4 in result["evidence_rows"]


def test_front_sections_use_existing_assignments_and_exclude_stopped_time() -> None:
    facts, samples = build_front_sections_facts(
        date="2023-11-03", sources=_fixture_sources(), config=CONFIG
    )
    assert facts["platform_count"] == 2
    assert facts["daily_overview"]["daily_advance"]["net_change_m"] == 1.0
    assert facts["daily_overview"]["working_duration_seconds"] == 60.0
    assert facts["daily_overview"]["stopped_duration_seconds"] == 40.0
    assert facts["platforms"][0]["working_duration_seconds"] == 40.0
    assert facts["platforms"][0]["stopped_duration_seconds_inside_platform"] == 20.0
    assert facts["platforms"][0]["interval_advance"]["source_field"] == "cumulative_advance"
    assert interval_table_frame(facts).shape == (2, 4)
    validation = validate_front_sections_facts(facts, samples, CONFIG)
    assert validation["passed"]


def test_api_key_can_be_loaded_from_ignored_env_file(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY='test-key-from-file'\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert load_deepseek_api_key(env_file) == "test-key-from-file"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-from-environment")
    assert load_deepseek_api_key(env_file) == "test-key-from-environment"
