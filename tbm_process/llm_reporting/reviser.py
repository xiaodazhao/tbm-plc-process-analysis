from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from .draft import render_fact_sentence, validate_draft
from .fact_adapter import fact_map
from .verifier import verify_report


REVISION_SCHEMA = "tbm_report_local_revision_log_v1"


def revise_draft(
    draft: dict[str, Any],
    pool: dict[str, Any],
    plan: dict[str, Any],
    *,
    verification_before: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply one deterministic, evidence-backed local revision pass to D."""
    validate_draft(draft)
    before = verification_before or verify_report(draft, pool, plan)
    revised = copy.deepcopy(draft)
    revised["method_group"] = "E"
    mapping = fact_map(pool)
    changes: list[dict[str, Any]] = []

    failed_positions = {
        (row["section_index"], row["sentence_index"]): row
        for row in before["sentences"]
        if row["status"] == "FAIL"
    }
    global_failed_ids = {
        fact_id
        for check in before.get("global_checks", [])
        if check.get("status") == "FAIL"
        for fact_id in check.get("fact_ids", [])
    }

    for section_index, section in enumerate(revised["sections"]):
        new_sentences = []
        for sentence_index, item in enumerate(section["sentences"]):
            failure = failed_positions.get((section_index, sentence_index))
            ids = [fact_id for fact_id in item["supporting_fact_ids"] if fact_id in mapping]
            if not failure and not (set(ids) & global_failed_ids):
                new_sentences.append(item)
                continue
            replacements = [
                {
                    "text": render_fact_sentence(mapping[fact_id]),
                    "supporting_fact_ids": [fact_id],
                }
                for fact_id in ids
                if render_fact_sentence(mapping[fact_id])
            ]
            changes.append(
                {
                    "action": "replace" if replacements else "delete",
                    "section_id": section["section_id"],
                    "before_text": item["text"],
                    "after_texts": [value["text"] for value in replacements],
                    "reason": (
                        failure.get("error_types", [])
                        if failure
                        else ["report_level_contradiction"]
                    ),
                    "fact_ids": ids,
                }
            )
            new_sentences.extend(replacements)
        section["sentences"] = _deduplicate(new_sentences)

    coverage = before.get("required_fact_coverage", {})
    missing = coverage.get(
        "missing_failed_or_review_fact_ids",
        coverage.get("missing_or_failed_fact_ids", []),
    )
    planned_section = {
        fact_id: section_plan["section_id"]
        for section_plan in plan["sections"]
        for fact_id in section_plan["required_fact_ids"]
    }
    existing = {
        fact_id
        for section in revised["sections"]
        for item in section["sentences"]
        for fact_id in item["supporting_fact_ids"]
    }
    for fact_id in missing:
        if fact_id in existing or fact_id not in mapping:
            continue
        text = render_fact_sentence(mapping[fact_id])
        if not text:
            continue
        target = next(
            section
            for section in revised["sections"]
            if section["section_id"] == planned_section[fact_id]
        )
        target["sentences"].append(
            {"text": text, "supporting_fact_ids": [fact_id]}
        )
        changes.append(
            {
                "action": "insert_missing_required_fact",
                "section_id": target["section_id"],
                "before_text": None,
                "after_texts": [text],
                "reason": ["required_fact_missing_or_failed"],
                "fact_ids": [fact_id],
            }
        )

    response_section = next(
        section for section in revised["sections"] if section["section_id"] == "responses"
    )
    original_order = [item["text"] for item in response_section["sentences"]]
    response_section["sentences"].sort(
        key=lambda item: _response_sort_key(item, mapping)
    )
    if original_order != [item["text"] for item in response_section["sentences"]]:
        changes.append(
            {
                "action": "reorder",
                "section_id": "responses",
                "before_text": original_order,
                "after_texts": [item["text"] for item in response_section["sentences"]],
                "reason": ["response_event_order_error"],
                "fact_ids": [
                    fact_id
                    for item in response_section["sentences"]
                    for fact_id in item["supporting_fact_ids"]
                ],
            }
        )

    validate_draft(revised, known_fact_ids=set(mapping))
    after = verify_report(revised, pool, plan)
    log = {
        "schema_version": REVISION_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": pool["date"],
        "source_group": draft.get("method_group"),
        "target_group": "E",
        "maximum_revision_passes": 1,
        "revision_passes_used": 1 if changes else 0,
        "change_count": len(changes),
        "changes": changes,
        "review_claims_left_unchanged": [
            row["text"] for row in before["sentences"] if row["status"] == "REVIEW"
        ],
        "note": "Only FAIL or missing required facts were changed; REVIEW was preserved for human review.",
    }
    return revised, log, after


def _deduplicate(sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in sentences:
        key = (item["text"], tuple(item["supporting_fact_ids"]))
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _response_sort_key(
    item: dict[str, Any], mapping: dict[str, dict[str, Any]]
) -> tuple[str, str]:
    starts = [
        str(mapping[fact_id]["data"].get("start_time") or "")
        for fact_id in item["supporting_fact_ids"]
        if mapping.get(fact_id, {}).get("fact_type")
        == "observed_construction_response"
    ]
    return (min(starts) if starts else "9999", item["text"])
