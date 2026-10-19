from __future__ import annotations

from pathlib import Path

from tbm_process.final_reporting.common import (
    parse_chainage,
    redact_suspicious_chainage_ranges,
)
from tbm_process.final_reporting.geology_presentation import _merge_risks
from tbm_process.final_reporting.plc import _event_short_description
from tbm_process.final_reporting.rendering import _parameter_rows


def test_full_chainage_parser_and_suspicious_range_redaction() -> None:
    assert parse_chainage("DyK1017+347") == 1017347.0

    text, findings = redact_suspicious_chainage_ranges(
        "当前开挖段落DyK1017+347～DyK1016+357段为压碎岩。"
    )

    assert len(findings) == 1
    assert "DyK1016+357" not in text
    assert "未用于空间判断" in text


def test_final_reporting_imports_without_local_data() -> None:
    from tbm_process.final_reporting.audit import audit_report_artifacts
    from tbm_process.final_reporting.geology import build_geology_presentation
    from tbm_process.final_reporting.geology_axis import build_geology_axis_model
    from tbm_process.final_reporting.rendering import render_report_html

    assert callable(audit_report_artifacts)
    assert callable(build_geology_presentation)
    assert callable(build_geology_axis_model)
    assert callable(render_report_html)


def test_reader_description_translates_rpm_key() -> None:
    description = _event_short_description(
        {
            "parameter_changes": {
                "advance_speed": {"trend": "increasing"},
                "rpm": {"trend": "increasing"},
            }
        }
    )

    assert description == "推进速度、刀盘转速同步上升"


def test_empty_parameter_scope_is_explained() -> None:
    rendered = _parameter_rows({"scope_sample_count": 0, "parameters": {}})

    assert "未识别到可用于该统计口径的运行时段样本" in rendered
    assert "0/0" not in rendered


def test_merged_risks_preserve_all_sources() -> None:
    common = {
        "source_type": "hsp",
        "geometry": "point",
        "risk_type": "物探异常",
        "start_m": 1013.0,
        "end_m": 1013.0,
        "start_chainage": "DyK1+013",
        "end_chainage": "DyK1+013",
    }

    merged = _merge_risks(
        [
            {**common, "source_ref": "H1", "content_unit_ids": ["u1"]},
            {**common, "source_ref": "H2", "content_unit_ids": ["u2"]},
        ]
    )

    assert len(merged) == 1
    assert merged[0]["source_refs"] == ["H1", "H2"]
    assert merged[0]["content_unit_ids"] == ["u1", "u2"]


def test_final_report_config_contains_no_machine_specific_paths() -> None:
    config = Path(__file__).resolve().parents[1] / "configs/final_report.json"
    text = config.read_text(encoding="utf-8")

    assert "/Users/" not in text
    assert "C:\\Users\\" not in text
