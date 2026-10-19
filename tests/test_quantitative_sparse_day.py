from __future__ import annotations

from tbm_process.reporting.quantitative import validate_quantitative_payload


def test_validator_accepts_explicitly_unavailable_state_duration() -> None:
    payload = {
        "date": "2023-10-06",
        "daily_overview": [
            {
                "fact_type": "data_coverage",
                "sample_count": 1,
            },
            {
                "fact_type": "daily_advance",
                "distance_source": "guidance_shield_head_chainage",
                "availability": "insufficient_records",
            },
        ],
        "parameter_registry": {"parameters": {}},
        "main_processes": [],
        "construction_responses": [],
        "data_quality": [],
        "llm_input_packages": {
            "formal": {},
            "research_preview": {
                "value_label": "原始PLC记录值，工程单位待核实"
            },
        },
    }

    validation = validate_quantitative_payload(payload)

    assert validation["passed"] is True
    assert any(
        row["name"]
        == "state_duration_checks_not_applicable_when_fact_unavailable"
        for row in validation["checks"]
    )
