from __future__ import annotations

import json
from pathlib import Path

from tbm_process.llm_reporting.content_planner import build_content_plan
from tbm_process.llm_reporting.draft import build_template_draft, render_draft_markdown
from tbm_process.llm_reporting.fact_adapter import build_typed_fact_pool
from tbm_process.llm_reporting.generator import build_generation_request
from tbm_process.llm_reporting.verifier import verify_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "tests/fixtures/current_llm_inputs"


def load_day(date: str = "2023-11-03"):
    return build_typed_fact_pool(
        SOURCE_ROOT / f"{date}.llm_input.research.json",
    )


def test_compact_fact_pool_preserves_ids_and_excludes_prebuilt_semantics() -> None:
    source_path = SOURCE_ROOT / "2023-11-03.llm_input.research.json"
    source = json.loads(source_path.read_text())
    pool, evidence = load_day()

    source_ids = {
        row["fact_id"]
        for rows in source["structured_facts"].values()
        for row in rows
    }
    assert {fact["fact_id"] for fact in pool["facts"]} == source_ids
    assert pool["source"]["semantic_expressions_in_source"] is True
    assert pool["source"]["semantic_expressions_in_llm_pool"] is False
    assert all("semantic_expressions" not in fact for fact in pool["facts"])
    assert set(evidence["facts"]) == source_ids


def test_groups_share_fact_pool_and_add_only_plan_or_constraints() -> None:
    pool, _ = load_day()
    plan = build_content_plan(pool)
    requests = {
        group: build_generation_request(
            pool, plan, group=group, model="deepseek-v4-flash", temperature=0.0, max_tokens=1000
        )
        for group in ("B", "C", "D")
    }

    for request in requests.values():
        prompt = request["body"]["messages"][1]["content"]
        assert json.dumps(pool, ensure_ascii=False, separators=(",", ":")) in prompt
    assert "content_plan" not in requests["B"]["body"]["messages"][1]["content"]
    assert "content_plan" in requests["C"]["body"]["messages"][1]["content"]
    assert "event_count是质量事件数" in requests["D"]["body"]["messages"][0]["content"]


def test_content_plan_is_predeclared_and_chronological() -> None:
    pool, _ = load_day()
    plan = build_content_plan(pool, max_response_facts=3)
    response_section = next(section for section in plan["sections"] if section["section_id"] == "responses")
    mapping = {fact["fact_id"]: fact for fact in pool["facts"]}
    times = [mapping[fact_id]["data"]["start_time"] for fact_id in response_section["required_fact_ids"]]

    assert len(response_section["required_fact_ids"]) == 3
    assert times == sorted(times)
    assert set(plan["required_fact_ids"]).issubset(mapping)


def test_template_baseline_passes_deterministic_verification() -> None:
    pool, _ = load_day()
    plan = build_content_plan(pool)
    draft = build_template_draft(pool, plan)
    verification = verify_report(draft, pool, plan)

    assert verification["metrics"]["numeric_fact_accuracy"] == 1.0
    assert verification["metrics"]["required_fact_coverage"] == 1.0
    assert verification["metrics"]["fail_claim_count"] == 0
    assert len(render_draft_markdown(draft)) > 500


def test_every_fixed_day_builds_fact_pool_and_plan() -> None:
    for input_path in sorted(SOURCE_ROOT.glob("*.research.json")):
        date = input_path.name[:10]
        pool, _ = load_day(date)
        plan = build_content_plan(pool)
        assert pool["date"] == date
        assert plan["required_fact_count"] > 0
