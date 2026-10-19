from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pandas as pd

from tbm_process.reporting.front_sections import (
    ReportSources,
    _interval_advance_quality,
    build_front_sections_facts,
    format_dk,
    interval_table_frame,
    load_report_config,
    validate_front_sections_facts,
)
from tbm_process.reporting.report_narrative import (
    build_default_narrative,
    build_report_narrative_request,
    load_deepseek_api_key,
    validate_report_narrative,
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


def test_report_narrative_requires_complete_overview_and_multiple_process_paragraphs() -> None:
    facts, _ = build_front_sections_facts(
        date="2023-11-03", sources=_fixture_sources(), config=CONFIG
    )
    request, compact = build_report_narrative_request(facts, model="test-model")
    assert request["body"]["response_format"] == {"type": "json_object"}
    default = build_default_narrative(facts)
    payload = {
        "chapter1": default["chapter1"],
        "chapter2": {
            "paragraphs": [
                "当日运行活动分布在00:00至00:01。",
                "各段工作时长和进尺见表，期间存在停止转换。",
            ],
            "supporting_platform_ids": [row["platform_id"] for row in facts["platforms"]],
        },
    }
    result = validate_report_narrative(payload, facts, compact)
    assert result["passed"], result["errors"]
    payload["chapter2"]["paragraphs"] = ["设备故障导致停止。"]
    result = validate_report_narrative(payload, facts, compact)
    assert not result["passed"]
    assert "chapter2_requires_two_to_four_paragraphs" in result["errors"]
    assert "prohibited_inference_term" in result["errors"]


def test_report_narrative_accepts_chinese_metre_alias() -> None:
    facts, _ = build_front_sections_facts(
        date="2023-11-03", sources=_fixture_sources(), config=CONFIG
    )
    _, compact = build_report_narrative_request(facts, model="test-model")
    default = build_default_narrative(facts)
    paragraph = default["chapter1"]["paragraphs"][0].replace("1.00 m", "1.00米")
    payload = {
        "chapter1": {
            "paragraphs": [paragraph],
            "supporting_fields": ["daily_overview"],
        },
        "chapter2": {
            "paragraphs": [
                "当日运行活动主要分布于00:00至00:01。",
                "各段参数表现和工作时长见明细表。",
            ],
            "supporting_platform_ids": [
                row["platform_id"] for row in facts["platforms"]
            ],
        },
    }
    result = validate_report_narrative(payload, facts, compact)
    assert result["passed"], result["errors"]


def test_report_narrative_rejects_unsupported_gap_claim() -> None:
    facts, _ = build_front_sections_facts(
        date="2023-11-03", sources=_fixture_sources(), config=CONFIG
    )
    _, compact = build_report_narrative_request(facts, model="test-model")
    default = build_default_narrative(facts)
    payload = {
        "chapter1": default["chapter1"],
        "chapter2": {
            "paragraphs": [
                "00:00至00:01运行，此后无运行记录。",
                "其他时段的参数表现见明细表。",
            ],
            "supporting_platform_ids": [
                row["platform_id"] for row in facts["platforms"]
            ],
        },
    }
    result = validate_report_narrative(payload, facts, compact)
    assert not result["passed"]
    assert any(
        error.startswith("unsupported_gap_or_continuity_claim")
        for error in result["errors"]
    )


def test_default_narrative_handles_day_without_reportable_platforms() -> None:
    facts, _ = build_front_sections_facts(
        date="2023-11-03", sources=_fixture_sources(), config=CONFIG
    )
    empty = deepcopy(facts)
    empty["platforms"] = []
    empty["platform_count"] = 0
    narrative = build_default_narrative(empty)
    assert narrative["chapter2"]["supporting_platform_ids"] == []
    assert "未形成可单列的运行时段" in narrative["chapter2"]["paragraphs"][0]
