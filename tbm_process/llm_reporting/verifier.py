from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import re
from typing import Any

from .draft import SECTION_SPECS, validate_draft
from .fact_adapter import fact_map


VERIFICATION_SCHEMA = "tbm_report_verification_v2"
STATUS_RANK = {"N/A": 0, "PASS": 1, "REVIEW": 2, "FAIL": 3}

FORBIDDEN_ENGINEERING_UNITS = re.compile(
    r"(?<![A-Za-z])(kN\s*[·*]?\s*m|kN·m|kN|mm/min|m/min|r/min|rpm)(?![A-Za-z])",
    flags=re.IGNORECASE,
)
UNSUPPORTED_CAUSES = re.compile(
    r"围岩(?:良好|稳定|变硬|变软|恶化|破碎|坚硬|软弱)|"
    r"地质(?:良好|稳定|恶化|变化|原因)|刀具(?:磨损|损坏)|"
    r"刀盘卡(?:滞|死)|设备故障|机械故障|人员操作(?:不当)?|"
    r"施工效率(?:低|高|下降|提高)"
)
NEGATION_WINDOW = re.compile(
    r"不(?:能|得|可|应)?(?:据此)?(?:解释|判断|推断|认定)|未确认|不代表"
)
UNSUPPORTED_ASSERTION = re.compile(
    r"设备(?:运行)?(?:完全)?(?:正常|良好|稳定)|"
    r"现场(?:施工)?(?:正常|顺利|良好)|施工(?:正常|顺利)|"
    r"未发生(?:设备|机械)?(?:故障|异常)|无(?:设备|机械)?(?:故障|异常)|"
    r"风险(?:较低|可控)|安全(?:状态)?(?:良好|可控)"
)
INTERNAL_TERMS = re.compile(
    r"\b(?:startup|rising|stable|declining|fact_id|relative_change)\b|"
    r"\b[APQR]\d{2,4}\b|四阶段(?:标签|分类)?|算法评分|平台编号",
    flags=re.IGNORECASE,
)
PARAMETER_NAMES = {
    "advance_speed": "推进速度",
    "thrust": "总推力",
    "torque": "刀盘扭矩",
    "rpm": "刀盘转速",
}
PARAMETER_ALIASES = {
    "advance_speed": ("推进速度",),
    "thrust": ("总推力", "推进力"),
    "torque": ("刀盘扭矩", "扭矩"),
    "rpm": ("刀盘转速", "转速"),
}
QUALITY_LABELS = (
    "区间速度与距离不匹配",
    "速度距离不匹配",
    "推进速度",
    "刀盘转速",
    "规范转速",
    "转速数据",
    "贯入度",
    "采样缺口",
)


def verify_report(
    draft: dict[str, Any],
    pool: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Conservatively verify independent claims against typed report facts.

    PASS requires at least one explicitly checked field role. Matching only a fact
    identifier, keyword, or untyped number is insufficient. Unsupported but not
    deterministically false content remains REVIEW.
    """
    validate_draft(draft)
    mapping = fact_map(pool)
    sentence_rows: list[dict[str, Any]] = []
    verified_fact_ids: set[str] = set()
    failed_fact_ids: set[str] = set()
    review_fact_ids: set[str] = set()
    numeric_checks: list[dict[str, Any]] = []

    for section_index, section in enumerate(draft["sections"]):
        for sentence_index, item in enumerate(section["sentences"]):
            row = _verify_sentence(
                item,
                mapping,
                pool_date=str(pool["date"]),
                section_id=section["section_id"],
                section_index=section_index,
                sentence_index=sentence_index,
            )
            sentence_rows.append(row)
            verified_fact_ids.update(row["verified_fact_ids"])
            failed_fact_ids.update(row["failed_fact_ids"])
            review_fact_ids.update(row["review_fact_ids"])
            numeric_checks.extend(row["numeric_checks"])

    global_checks = _check_response_order(sentence_rows, mapping)
    global_checks.extend(_check_internal_contradictions(sentence_rows))
    for check in global_checks:
        if check["status"] == "FAIL":
            failed_fact_ids.update(check.get("fact_ids", []))

    required_ids = list(plan.get("required_fact_ids", []))
    covered_ids = [
        fact_id
        for fact_id in required_ids
        if fact_id in verified_fact_ids
        and fact_id not in failed_fact_ids
        and fact_id not in review_fact_ids
    ]
    missing_ids = [fact_id for fact_id in required_ids if fact_id not in covered_ids]
    structure = _check_structure(draft)
    metrics = _build_metrics(
        sentence_rows,
        global_checks,
        numeric_checks,
        required_ids,
        covered_ids,
        structure,
    )
    manual_review = [
        {
            "section": row["section_id"],
            "sentence": row["text"],
            "supporting_fact_ids": row["supporting_fact_ids"],
            "automatic_result": row["status"],
            "error_types": row["error_types"],
            "claims": [
                {
                    "claim": claim["claim"],
                    "status": claim["status"],
                    "error_types": claim["error_types"],
                    "checked_roles": claim["checked_roles"],
                }
                for claim in row["claims"]
                if claim["status"] in {"FAIL", "REVIEW"}
            ],
        }
        for row in sentence_rows
        if row["status"] in {"FAIL", "REVIEW"}
    ]
    return {
        "schema_version": VERIFICATION_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": pool["date"],
        "method_group": draft.get("method_group"),
        "verification_scope": {
            "method": "deterministic_role_aware_claim_verification",
            "claim_splitting": "Chinese sentence/semicolon/comma plus selected conjunctions",
            "pass_rule": "PASS requires explicit typed-field checks; keyword or fact-id match alone is insufficient.",
            "review_rule": "Unresolved or only partially checked factual content is REVIEW.",
            "llm_semantic_judge_used": False,
            "not_engineering_fact_accuracy": True,
        },
        "structure_check": structure,
        "sentences": sentence_rows,
        "global_checks": global_checks,
        "required_fact_coverage": {
            "required_fact_ids": required_ids,
            "covered_fact_ids": covered_ids,
            "missing_failed_or_review_fact_ids": missing_ids,
            "review_fact_ids": sorted(review_fact_ids & set(required_ids)),
        },
        "numeric_checks": numeric_checks,
        "metrics": metrics,
        "error_type_counts": dict(sorted(_error_counts(sentence_rows, global_checks).items())),
        "manual_review_table": manual_review,
    }


def split_independent_claims(text: str) -> list[str]:
    """Split report prose conservatively while preserving commas inside numbers."""
    coarse = re.split(r"[。；;！？!?]+", text)
    raw_parts: list[str] = []
    for value in coarse:
        value = value.strip()
        if not value:
            continue
        comma_parts = _split_top_level_commas(value)
        for comma_part in comma_parts:
            cleaned = comma_part.strip(" ，,：:")
            if cleaned:
                raw_parts.append(cleaned)
    parts: list[str] = []
    for part in raw_parts:
        if parts and re.match(r"^(?:占比|比例)(?:约|为|：|:)?", part):
            parts[-1] += "，" + part
        elif (
            parts
            and any(name in parts[-1] for name in PARAMETER_NAMES.values())
            and re.match(
                r"^(?:(?:由|从)\s*[-+]?\d|(?:但)?波动|(?:变化(?:幅度)?|差异|增幅|降幅))",
                part,
            )
        ):
            parts[-1] += "，" + part
        elif (
            parts
            and re.match(r"^(?:涉及|影响|受影响)", part)
            and re.search(r"事件|不可靠|不可信|可疑|不匹配|缺口|数据片段", parts[-1])
        ):
            parts[-1] += "，" + part
        elif (
            parts
            and "分别为" in parts[-1]
            and re.fullmatch(r"(?:高|中|低|较高|较低|中等|适中)", part)
        ):
            parts[-1] += "，" + part
        else:
            parts.append(part)
    return parts or [text.strip()]


def _split_top_level_commas(text: str) -> list[str]:
    """Split clause separators, but keep commas inside paired parentheses."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for index, character in enumerate(text):
        if character in "（(":
            depth += 1
        elif character in "）)" and depth:
            depth -= 1
        is_numeric_comma = (
            character == ","
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        )
        if character in "，," and depth == 0 and not is_numeric_comma:
            if current:
                parts.append("".join(current))
                current = []
            continue
        current.append(character)
    if current:
        parts.append("".join(current))
    return parts


def compare_draft_information(
    before_draft: dict[str, Any],
    after_draft: dict[str, Any],
    before_verification: dict[str, Any],
    after_verification: dict[str, Any],
) -> dict[str, Any]:
    """Measure D-to-E information retention without treating deletion as success."""
    before_ids = _declared_fact_ids(before_draft)
    after_ids = _declared_fact_ids(after_draft)
    before_roles = _role_signatures(before_verification, numeric_only=False)
    after_roles = _role_signatures(after_verification, numeric_only=False)
    before_numeric = _role_signatures(before_verification, numeric_only=True)
    after_numeric = _role_signatures(after_verification, numeric_only=True)
    before_claims = _claim_counts(before_verification)
    after_claims = _claim_counts(after_verification)
    before_chars = _draft_characters(before_draft)
    after_chars = _draft_characters(after_draft)
    return {
        "schema_version": "tbm_report_information_retention_v2",
        "date": before_draft.get("date"),
        "source_group": before_draft.get("method_group"),
        "target_group": after_draft.get("method_group"),
        "declared_fact_ids": _retention_block(before_ids, after_ids),
        "checked_role_signatures": _retention_block(before_roles, after_roles),
        "numeric_role_signatures": _retention_block(before_numeric, after_numeric),
        "claim_counts": {"before": before_claims, "after": after_claims},
        "character_counts": {
            "before": before_chars,
            "after": after_chars,
            "retention_rate": _ratio(after_chars, before_chars),
        },
        "possible_information_loss": bool(
            (before_ids - after_ids) or (before_roles - after_roles) or (before_numeric - after_numeric)
        ),
        "interpretation": (
            "Retention metrics are reported separately from verifier pass rates. "
            "Removal of failed or hard-to-check content must not be counted as factual improvement by itself."
        ),
    }


def _verify_sentence(
    item: dict[str, Any],
    mapping: dict[str, dict[str, Any]],
    *,
    pool_date: str,
    section_id: str,
    section_index: int,
    sentence_index: int,
) -> dict[str, Any]:
    text = str(item["text"]).strip()
    ids = list(item.get("supporting_fact_ids", []))
    missing = [fact_id for fact_id in ids if fact_id not in mapping]
    facts = [mapping[fact_id] for fact_id in ids if fact_id in mapping]
    claims = [
        _verify_claim(
            claim,
            full_sentence=text,
            declared_facts=facts,
            all_facts=mapping,
            pool_date=pool_date,
        )
        for claim in split_independent_claims(text)
    ]
    if missing:
        claims.append(
            _claim_result(
                text,
                "FAIL",
                ["unknown_supporting_fact_id"],
                [],
                [],
                set(),
                set(missing),
                set(),
                {"missing_fact_ids": missing},
            )
        )
    status = _worst_status([claim["status"] for claim in claims])
    return {
        "section_id": section_id,
        "section_index": section_index,
        "sentence_index": sentence_index,
        "text": text,
        "supporting_fact_ids": ids,
        "status": status,
        "error_types": sorted({error for claim in claims for error in claim["error_types"]}),
        "verified_fact_ids": sorted({value for claim in claims for value in claim["verified_fact_ids"]}),
        "failed_fact_ids": sorted({value for claim in claims for value in claim["failed_fact_ids"]}),
        "review_fact_ids": sorted({value for claim in claims for value in claim["review_fact_ids"]}),
        "numeric_checks": [check for claim in claims for check in claim["numeric_checks"]],
        "checked_roles": sorted({role for claim in claims for role in claim["checked_roles"]}),
        "claims": claims,
    }


def _verify_claim(
    claim: str,
    *,
    full_sentence: str,
    declared_facts: list[dict[str, Any]],
    all_facts: dict[str, dict[str, Any]],
    pool_date: str,
) -> dict[str, Any]:
    errors: list[str] = []
    review_reasons: list[str] = []
    checks: list[dict[str, Any]] = []
    checked_roles: set[str] = set()
    matched_fact_ids: set[str] = set()

    if INTERNAL_TERMS.search(claim):
        errors.append("internal_identifier_or_algorithm_term_exposed")
    cause = UNSUPPORTED_CAUSES.search(claim)
    if cause and not NEGATION_WINDOW.search(claim[: cause.start()]):
        errors.append("unsupported_engineering_or_geological_inference")
    if FORBIDDEN_ENGINEERING_UNITS.search(claim):
        errors.append("unconfirmed_engineering_unit")
    if UNSUPPORTED_ASSERTION.search(claim):
        review_reasons.append("unsupported_operational_assertion_requires_review")

    evidence: dict[str, Any] = {}
    for fact in declared_facts:
        outcome = _check_fact(
            claim,
            fact,
            full_sentence=full_sentence,
            all_facts=all_facts,
            pool_date=pool_date,
        )
        if not outcome["matched"]:
            continue
        fact_id = str(fact["fact_id"])
        matched_fact_ids.add(fact_id)
        checks.extend(outcome["checks"])
        errors.extend(outcome["errors"])
        review_reasons.extend(outcome["review_reasons"])
        checked_roles.update(f"{fact_id}:{role}" for role in outcome["checked_roles"])
        evidence[fact_id] = outcome["evidence"]

    failed_checks = [check for check in checks if check["status"] == "FAIL"]
    errors.extend(check["error_type"] for check in failed_checks if check.get("error_type"))
    required_roles = _required_roles(claim, full_sentence)
    role_names = {role.split(":", 1)[1] for role in checked_roles if ":" in role}
    missing_roles = sorted(role for role in required_roles if not _role_satisfied(role, role_names))
    if missing_roles:
        review_reasons.extend(f"unverified_claim_role:{role}" for role in missing_roles)

    if not matched_fact_ids and declared_facts and not _is_nonfactual_guardrail(claim):
        candidates = _find_candidate_support(
            claim,
            full_sentence=full_sentence,
            all_facts=all_facts,
            pool_date=pool_date,
        )
        if candidates:
            review_reasons.append("supporting_fact_id_mismatch")
            evidence["candidate_supporting_fact_ids"] = candidates

    if errors:
        status = "FAIL"
    elif review_reasons:
        status = "REVIEW"
    elif matched_fact_ids and checked_roles:
        status = "PASS"
    elif _is_nonfactual_guardrail(claim):
        status = "N/A"
    else:
        status = "REVIEW"
        review_reasons.append(
            "no_declared_support" if not declared_facts else "semantic_support_not_explicitly_verified"
        )

    declared_ids = {str(fact["fact_id"]) for fact in declared_facts}
    verified = matched_fact_ids if status == "PASS" else set()
    failed = declared_ids if status == "FAIL" else set()
    reviewed = declared_ids if status == "REVIEW" else set()
    return _claim_result(
        claim,
        status,
        errors + review_reasons,
        checks,
        sorted(checked_roles),
        verified,
        failed,
        reviewed,
        evidence,
    )


def _check_fact(
    claim: str,
    fact: dict[str, Any],
    *,
    full_sentence: str,
    all_facts: dict[str, dict[str, Any]],
    pool_date: str,
) -> dict[str, Any]:
    fact_type = str(fact["fact_type"])
    if fact_type == "data_coverage":
        return _check_data_coverage(claim, fact, pool_date, full_sentence)
    if fact_type == "daily_advance":
        return _check_daily_advance(claim, fact)
    if fact_type == "machine_state_duration":
        return _check_machine_states(claim, fact)
    if fact_type == "coverage_limitation":
        return _check_coverage_limitation(claim, fact)
    if fact_type == "daily_advancing_parameter_statistics":
        return _check_daily_parameters(claim, fact, full_sentence)
    if fact_type == "representative_construction_period":
        return _check_process(claim, fact, full_sentence)
    if fact_type == "within_day_parameter_comparison":
        return _check_comparison(claim, fact, full_sentence)
    if fact_type == "observed_construction_response":
        return _check_response(claim, fact, full_sentence, all_facts)
    if fact_type == "data_quality_limitation":
        return _check_quality(claim, fact)
    return _outcome(False)


def _check_data_coverage(
    claim: str,
    fact: dict[str, Any],
    pool_date: str,
    full_sentence: str,
) -> dict[str, Any]:
    coverage_context = bool(
        re.search(r"PLC数据|数据覆盖|共(?:采集|记录)|采集到|记录了?\s*[\d,]+\s*条", full_sentence)
    )
    if not coverage_context or not (
        re.search(r"PLC数据|数据覆盖|共(?:采集|记录)|采集到|记录了?\s*[\d,]+\s*条|共\s*[\d,]+\s*条记录", claim)
        or _clock_values(claim)
        or re.search(r"\d{4}年\d{1,2}月\d{1,2}日", claim)
    ):
        return _outcome(False)
    data = fact["data"]
    checks = _time_checks(claim, [data.get("start_time"), data.get("end_time")], fact)
    roles = {check["role"] for check in checks}
    sample = re.search(r"([\d,]+)\s*条(?:PLC)?(?:数据|记录)?", claim)
    if sample:
        checks.append(_numeric_check("sample_count", _number(sample.group(1)), data.get("sample_count"), fact))
        roles.add("sample_count")
    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", claim)
    if date_match:
        observed = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        checks.append(_value_check("calendar_date", observed, pool_date, fact))
        roles.add("calendar_date")
    return _outcome(True, checks=checks, roles=roles)


def _check_daily_advance(claim: str, fact: dict[str, Any]) -> dict[str, Any]:
    if "进尺" not in claim and "导向盾首里程" not in claim:
        return _outcome(False)
    data = fact["data"]
    checks: list[dict[str, Any]] = []
    roles: set[str] = set()
    distance_patterns = [
        r"(?:当日|今日)(?:完成)?进尺(?:为|约|：|:)?\s*([\d,]+(?:\.\d+)?)\s*(?:m|米)",
        r"进尺(?:为|约|：|:)?\s*([\d,]+(?:\.\d+)?)\s*(?:m|米)",
        r"([\d,]+(?:\.\d+)?)\s*(?:m|米)(?:的)?(?:当日|今日)进尺",
    ]
    distance_match = next((re.search(pattern, claim, flags=re.IGNORECASE) for pattern in distance_patterns if re.search(pattern, claim, flags=re.IGNORECASE)), None)
    if distance_match:
        checks.append(_numeric_check("daily_advance", _number(distance_match.group(1)), data.get("distance_m"), fact, 0.011))
        roles.add("daily_advance")
    chainage_match = re.search(
        r"导向盾首里程.*?([\d,]+(?:\.\d+)?)\s*(?:m|米).*?(?:至|到|变化至|推进至).*?([\d,]+(?:\.\d+)?)\s*(?:m|米)",
        claim,
        flags=re.IGNORECASE,
    )
    if chainage_match:
        checks.append(_numeric_check("chainage_start", _number(chainage_match.group(1)), data.get("chainage_start"), fact, 0.011))
        checks.append(_numeric_check("chainage_end", _number(chainage_match.group(2)), data.get("chainage_end"), fact, 0.011))
        roles.update({"chainage_start", "chainage_end"})
    if "导向盾首里程首末差" in claim:
        roles.add("daily_advance_source")
    return _outcome(True, checks=checks, roles=roles)


def _check_machine_states(claim: str, fact: dict[str, Any]) -> dict[str, Any]:
    data = fact["data"]
    labels = [str(state.get("display_name") or state.get("state")) for state in data.get("states", [])]
    if not ("运行状态" in claim or any(re.search(re.escape(label) + r"(?:状态)?", claim) for label in labels) or "硬采样缺口未计入停止" in claim):
        return _outcome(False)
    checks: list[dict[str, Any]] = []
    roles: set[str] = set()
    for state in data.get("states", []):
        label = str(state.get("display_name") or state.get("state"))
        # Require the state label to introduce a duration expression.  This
        # prevents the short label "推进" from matching inside the distinct
        # state name "加压但未满足推进条件".
        match = re.search(
            re.escape(label) + r"(?:状态)?(?=\s*(?:累计|时长|约|为|：|:|\d))",
            claim,
        )
        if not match:
            continue
        tail = claim[match.end():]
        duration = _parse_duration(tail)
        if duration is not None:
            role = f"machine_state_duration:{state['state']}"
            checks.append(
                _numeric_check(
                    role,
                    duration,
                    state.get("duration_seconds"),
                    fact,
                    _duration_tolerance(tail),
                )
            )
            roles.add(role)
        percent = re.search(r"(?:占比)?(?:约|为|：|:)?\s*([\d.]+)\s*%", tail)
        if percent:
            role = f"machine_state_percentage:{state['state']}"
            checks.append(_numeric_check(role, float(percent.group(1)), state.get("percentage"), fact, 0.11))
            roles.add(role)
    if "硬采样缺口未计入停止" in claim or "缺口时段不计入停止" in claim:
        checks.append(_value_check("hard_gaps_excluded", True, bool(data.get("hard_gaps_excluded")), fact))
        roles.add("hard_gaps_excluded")
    return _outcome(True, checks=checks, roles=roles)


def _check_coverage_limitation(claim: str, fact: dict[str, Any]) -> dict[str, Any]:
    if "采样缺口" not in claim and "缺口时段不计入停止" not in claim:
        return _outcome(False)
    checks: list[dict[str, Any]] = []
    roles: set[str] = set()
    count = re.search(r"([\d,]+)\s*(?:处|次|个|起)(?:硬)?采样缺口|(?:硬)?采样缺口(?:共|为|约)?\s*([\d,]+)\s*(?:处|次|个|起)", claim)
    if count:
        raw = next(value for value in count.groups() if value is not None)
        checks.append(_numeric_check("hard_sampling_gap_episode_count", _number(raw), fact["data"].get("hard_sampling_gap_count"), fact))
        roles.add("hard_sampling_gap_episode_count")
    if "缺口时段不计入停止" in claim or "缺口未计入停止" in claim:
        roles.add("gap_exclusion_rule")
    return _outcome(True, checks=checks, roles=roles)


def _check_daily_parameters(claim: str, fact: dict[str, Any], full_sentence: str) -> dict[str, Any]:
    parameters = fact["data"].get("parameters", {})
    matched = any(str(item.get("display_name") or "") in claim for item in parameters.values())
    if not matched and not re.search(r"推进状态.*(?:样本|记录)", claim):
        return _outcome(False)
    result = _parameter_checks(claim, parameters, fact, full_sentence, mode="distribution")
    scope_count = re.search(r"(?:样本|有效记录)(?:数)?(?:为|约|：|:)?\s*([\d,]+)\s*条", claim)
    if scope_count:
        expected = fact["data"].get("statistical_scope", {}).get("sample_count")
        result["checks"].append(_numeric_check("statistical_scope_sample_count", _number(scope_count.group(1)), expected, fact))
        result["checked_roles"].add("statistical_scope_sample_count")
    result["matched"] = True
    return result


def _check_process(claim: str, fact: dict[str, Any], full_sentence: str) -> dict[str, Any]:
    data = fact["data"]
    allowed_times = _process_times(data)
    has_time = bool(_clock_values(claim))
    has_process_term = bool(
        re.search(
            r"时段|活动|作业|过程|总跨度|累计.*时长|间隔|确认(?:掘进|开挖)|推进相关|相对偏|居中",
            claim,
        )
    ) or any(name in claim for name in PARAMETER_NAMES.values())
    if not has_process_term and re.match(r"^持续(?:时间)?(?:约|为|：|:)?\s*\d", claim):
        has_process_term = bool(re.search(r"活动|作业|掘进时段|运行时段", full_sentence))
    if not has_process_term and not (
        has_time
        and any(
            _time_matches(value, allowed_times) for value in _clock_values(claim)
        )
    ):
        return _outcome(False)
    checks = _time_checks(claim, allowed_times, fact)
    roles = {check["role"] for check in checks}
    errors: list[str] = []
    review: list[str] = []
    role_patterns = {
        "elapsed_timestamp_span": (r"(?:时间)?总?跨度(?:约|为|：|:)?\s*(.+)", data.get("elapsed_span_seconds")),
        "cumulative_activity_duration": (
            r"(?:(?:累计|实际)(?:活动|作业|运行)?(?:时长)?|持续(?:时间)?)(?:约|为|：|:)?\s*(.+)",
            data.get("total_activity_duration_seconds"),
        ),
        "intervening_gap_duration": (r"(?:中间)?间隔(?:合计|总时长)?(?:约|为|：|:)?\s*(.+)", data.get("total_intervening_gap_seconds")),
    }
    for role, (pattern, expected) in role_patterns.items():
        found = re.search(pattern, claim)
        if found:
            duration = _parse_duration(found.group(1))
            if duration is not None:
                checks.append(
                    _numeric_check(
                        role,
                        duration,
                        expected,
                        fact,
                        _duration_tolerance(found.group(1)),
                    )
                )
                roles.add(role)
    continuity = data.get("continuity_class")
    if re.search(r"间歇|存在间隔|有间隔|多段活动", claim):
        roles.add("continuity_class")
        if continuity != "intermittent_activities":
            errors.append("continuity_class_mismatch")
    if re.search(r"单(?:一|段)连续|连续(?:确认)?掘进时段|单段连续运行", claim):
        roles.add("continuity_class")
        if continuity == "intermittent_activities":
            errors.append("intermittent_process_misreported_as_continuous")
    if re.search(r"代表性运行过程", claim):
        roles.add("representative_process_presence")
    activity_class = str(data.get("activity_expression_class") or "")
    supports_confirmed = activity_class in {
        "confirmed_excavation",
        "mixed_excavation_and_advance_related_operation",
    }
    supports_related = activity_class in {
        "advance_related_operation",
        "mixed_excavation_and_advance_related_operation",
    }
    if re.search(r"确认(?:掘进|开挖)|掘进作业", claim):
        roles.add("activity_expression_class:confirmed_excavation")
        if not supports_confirmed:
            errors.append("activity_expression_class_mismatch")
    if "推进相关" in claim:
        roles.add("activity_expression_class:advance_related_operation")
        if not supports_related:
            errors.append("activity_expression_class_mismatch")
    activity_counts = []
    if "包含" in claim:
        activity_counts = re.findall(
            r"(?<![\d:])(\d+|[一二两三四五六七八九十])(?:段|个)(?:子)?"
            r"(?:推进相关(?:活动|作业|操作)|确认(?:掘进|开挖)(?:活动)?|活动)",
            claim.split("包含", 1)[1],
        )
    if activity_counts:
        observed = sum(_small_integer(value) for value in activity_counts)
        checks.append(_numeric_check("activity_count", observed, len(data.get("activities", [])), fact))
        roles.add("activity_count")
    for index, activity in enumerate(data.get("activities", []), start=1):
        start = _clock_text(activity.get("start_time"))
        end = _clock_text(activity.get("end_time"))
        if not start or not end:
            continue
        activity_duration = re.search(
            rf"{re.escape(start)}\s*(?:至|到|—|–|-)\s*{re.escape(end)}"
            rf"[^；;。]*?[（(][^）)]*?([\d.]+)\s*秒[）)]",
            claim,
        )
        if activity_duration:
            role = f"activity_duration:{index}"
            checks.append(
                _numeric_check(
                    role,
                    _number(activity_duration.group(1)),
                    activity.get("duration_seconds"),
                    fact,
                    1.0,
                )
            )
            roles.add(role)
    if re.search(r"无(?:中间)?间隔", claim):
        checks.append(
            _numeric_check(
                "intervening_gap_duration",
                0.0,
                data.get("total_intervening_gap_seconds"),
                fact,
                1.0,
            )
        )
        roles.add("intervening_gap_duration")
    relative_errors, relative_roles = _relative_label_checks(claim, data.get("relative_parameter_levels", {}))
    errors.extend(relative_errors)
    roles.update(relative_roles)
    parameter_result = _parameter_checks(claim, data.get("parameters", {}), fact, full_sentence, mode="distribution")
    checks.extend(parameter_result["checks"])
    errors.extend(parameter_result["errors"])
    review.extend(parameter_result["review_reasons"])
    roles.update(parameter_result["checked_roles"])
    return _outcome(True, checks=checks, roles=roles, errors=errors, review=review)


def _check_comparison(claim: str, fact: dict[str, Any], full_sentence: str) -> dict[str, Any]:
    data = fact["data"]
    parameter_names = [str(item.get("display_name") or "") for item in data.get("parameters", {}).values()]
    if (
        not re.search(r"相比|对比|较高|较低|更高|更低|接近|差异|增幅|降幅|可比持续运行片段", claim)
        and not any(name and name in claim for name in parameter_names)
    ):
        return _outcome(False)
    checks: list[dict[str, Any]] = []
    period_roles: set[str] = set()
    observed_periods = _period_ranges(claim)
    for role, field in (("comparison_left_period", "left_period"), ("comparison_right_period", "right_period")):
        period = _normalize_period(str(data.get(field) or ""))
        if period and period in observed_periods:
            checks.append(_value_check(role, period, period, fact))
            period_roles.add(role)
    if "可比持续运行片段" in claim:
        expected_scope = data.get("comparison_basis", {}).get("scope")
        checks.append(_value_check("comparison_scope", "comparable_sustained_operation_only", expected_scope, fact))
        period_roles.add("comparison_scope")
    errors, roles = _relative_label_checks(claim, data.get("relative_differences", {}))
    result = _parameter_checks(claim, data.get("parameters", {}), fact, full_sentence, mode="comparison")
    return _outcome(
        True,
        checks=checks + result["checks"],
        roles=period_roles | roles | result["checked_roles"],
        errors=errors + result["errors"],
        review=result["review_reasons"],
    )


def _check_response(
    claim: str,
    fact: dict[str, Any],
    full_sentence: str,
    all_facts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    data = fact["data"]
    names = [str(item.get("display_name") or "") for item in data.get("parameters", {}).values()]
    if not re.search(r"响应|持续|上升|下降|增加|降低|稳定|波动|变化", claim) and not any(name and name in claim for name in names) and not _clock_values(claim):
        return _outcome(False)
    allowed_times = [data.get("start_time"), data.get("end_time")]
    linked_id = str(data.get("related_main_process_fact_id") or "")
    linked = all_facts.get(linked_id)
    if linked and linked.get("fact_type") == "representative_construction_period":
        allowed_times.extend(_process_times(linked["data"]))
    checks = _time_checks(claim, allowed_times, fact)
    roles = {check["role"] for check in checks}
    if re.search(r"主要参数出现持续变化|观测到.*响应|出现.*响应", claim):
        roles.add("observed_response_presence")
    duration = re.search(r"持续(?:时间)?(?:约|为|：|:)?\s*(.+)", claim)
    if duration:
        parsed = _parse_duration(duration.group(1))
        if parsed is not None:
            checks.append(
                _numeric_check(
                    "response_duration",
                    parsed,
                    data.get("duration_seconds"),
                    fact,
                    _duration_tolerance(duration.group(1)),
                )
            )
            roles.add("response_duration")
    trend_source = (
        full_sentence
        if re.search(r"(?:三者|三项|各参数)均", claim) and not any(name in claim for name in names)
        else claim
    )
    errors, trend_roles = _response_trend_checks(trend_source, data.get("parameters", {}))
    roles.update(trend_roles)
    fluctuation_source = (
        full_sentence
        if re.search(r"波动(?:水平|幅度)?分别为", claim) and not any(name in claim for name in names)
        else claim
    )
    fluctuation_errors, fluctuation_roles = _fluctuation_checks(
        fluctuation_source,
        data.get("parameters", {}),
    )
    errors.extend(fluctuation_errors)
    roles.update(fluctuation_roles)
    parameter_result = _parameter_checks(claim, data.get("parameters", {}), fact, full_sentence, mode="response")
    checks.extend(parameter_result["checks"])
    errors.extend(parameter_result["errors"])
    roles.update(parameter_result["checked_roles"])
    return _outcome(True, checks=checks, roles=roles, errors=errors, review=parameter_result["review_reasons"])


def _check_quality(claim: str, fact: dict[str, Any]) -> dict[str, Any]:
    data = fact["data"]
    aliases = _quality_aliases(data)
    has_alias = any(alias in claim for alias in aliases)
    has_other_quality_label = any(label in claim for label in QUALITY_LABELS)
    has_role_phrase = bool(
        re.search(
            r"(?:涉及|影响).*条(?:PLC)?记录|受影响(?:PLC)?记录(?:数)?|记录跨度",
            claim,
        )
    )
    if has_other_quality_label and not has_alias:
        return _outcome(False)
    if not has_alias and not has_role_phrase:
        return _outcome(False)
    checks: list[dict[str, Any]] = []
    roles: set[str] = set()
    event_patterns = [
        r"事件(?:共|为|约)?\s*([\d,]+)\s*(?:处|次|个|起)",
        r"(?:存在|识别到)\s*([\d,]+)\s*(?:处|次|个|起).{0,15}(?:事件|不可靠|可疑|不匹配|缺口)",
        r"([\d,]+)\s*(?:处|次|个|起).{0,20}(?:事件|不可靠|可疑|不匹配|缺口)",
    ]
    for pattern in event_patterns:
        found = re.search(pattern, claim)
        if found:
            checks.append(_numeric_check("quality_episode_count", _number(found.group(1)), data.get("event_count"), fact))
            roles.add("quality_episode_count")
            break
    affected_patterns = [
        r"(?:涉及|影响)\s*([\d,]+)\s*条(?:PLC)?(?:受影响)?记录(?!跨度)",
        r"受影响(?:PLC)?记录(?:数)?(?:为|约|：|:)?\s*([\d,]+)\s*条?",
    ]
    for pattern in affected_patterns:
        found = re.search(pattern, claim)
        if found:
            checks.append(_numeric_check("affected_plc_record_count", _number(found.group(1)), data.get("affected_record_count"), fact))
            roles.add("affected_plc_record_count")
            break
    if re.search(r"未影响(?:PLC)?记录", claim):
        checks.append(_numeric_check("affected_plc_record_count", 0, data.get("affected_record_count"), fact))
        roles.add("affected_plc_record_count")
    span = re.search(r"([\d,]+)\s*条(?:PLC)?记录跨度|记录跨度(?:为|约|：|:)?\s*([\d,]+)\s*条", claim)
    if span:
        raw = next(value for value in span.groups() if value is not None)
        checks.append(_numeric_check("episode_span_record_count", _number(raw), data.get("episode_span_record_count"), fact))
        roles.add("episode_span_record_count")
    if has_alias and re.search(r"不可靠|不可信|可疑|不匹配|质量事件|缺口", claim):
        roles.add("quality_limitation_presence")
    return _outcome(True, checks=checks, roles=roles)


def _parameter_alias_in_text(key: str, display_name: str, text: str) -> str | None:
    aliases = set(PARAMETER_ALIASES.get(key, ()))
    aliases.add(display_name)
    present = [alias for alias in aliases if alias and alias in text]
    if not present:
        return None
    return min(present, key=lambda alias: (text.find(alias), -len(alias)))


def _parameter_split_pattern() -> str:
    aliases = sorted(
        {alias for values in PARAMETER_ALIASES.values() for alias in values},
        key=len,
        reverse=True,
    )
    return "|".join(re.escape(alias) for alias in aliases)


def _parameter_checks(
    claim: str,
    parameters: dict[str, Any],
    fact: dict[str, Any],
    full_sentence: str,
    *,
    mode: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    roles: set[str] = set()
    errors: list[str] = []
    review: list[str] = []
    for key, item in parameters.items():
        name = str(item.get("display_name") or PARAMETER_NAMES.get(key, key))
        mentioned_name = _parameter_alias_in_text(key, name, claim)
        if not mentioned_name:
            continue
        tail = claim.split(mentioned_name, 1)[1]
        tail = re.split(_parameter_split_pattern(), tail, maxsplit=1)[0]
        found_numeric_role = False
        requires_unit_caveat = False
        if mode == "distribution":
            for label, field in (("P10", "p10"), ("中位数", "median"), ("P90", "p90")):
                found = re.search(
                    re.escape(label)
                    + r"(?:约)?(?:为)?(?:：|:)?\s*([-+]?[\d,]+(?:\.\d+)?)",
                    tail,
                    flags=re.IGNORECASE,
                )
                if found:
                    role = f"parameter:{key}:{field}"
                    checks.append(_numeric_check(role, _number(found.group(1)), item.get(field), fact, 0.02))
                    roles.add(role)
                    found_numeric_role = True
                    requires_unit_caveat = True
            valid = re.search(r"(?:有效记录|有效样本)(?:数)?(?:为|约|：|:)?\s*([\d,]+)\s*条", tail)
            if valid:
                role = f"parameter:{key}:valid_count"
                checks.append(_numeric_check(role, _number(valid.group(1)), item.get("valid_count"), fact))
                roles.add(role)
                found_numeric_role = True
        elif mode == "comparison":
            pair = re.search(
                r"(?:从|由)?\s*([-+]?[\d,]+(?:\.\d+)?)\s*"
                r"(?:vs\.?|与|对比|→|(?:变化|上升|下降|增加|降低|升|降)?至)\s*"
                r"([-+]?[\d,]+(?:\.\d+)?)",
                tail,
                flags=re.IGNORECASE,
            )
            if pair:
                for field, raw in (("left_value", pair.group(1)), ("right_value", pair.group(2))):
                    role = f"parameter:{key}:{field}"
                    checks.append(_numeric_check(role, _number(raw), item.get(field), fact, 0.02))
                    roles.add(role)
                found_numeric_role = True
                requires_unit_caveat = True
            percent = re.search(r"(?:差异|变化|增幅|降幅)?(?:约|为|：|:)?\s*([-+]?\d+(?:\.\d+)?)\s*%", tail)
            if percent:
                role = f"parameter:{key}:percent_difference"
                expected = item.get("percent_difference")
                observed_percent = float(percent.group(1))
                if (
                    observed_percent >= 0
                    and re.search(r"降幅|降低|下降|偏低|较低", tail)
                    and expected is not None
                ):
                    expected = abs(float(expected))
                checks.append(
                    _numeric_check(
                        role,
                        observed_percent,
                        expected,
                        fact,
                        _reported_number_tolerance(percent.group(1)),
                    )
                )
                roles.add(role)
                found_numeric_role = True
        elif mode == "response":
            pair = re.search(
                r"(?:从|由)\s*([-+]?[\d,]+(?:\.\d+)?)\s*"
                r"(?:变化|上升|下降|增加|降低|微升|微降|升|降)?至\s*"
                r"([-+]?[\d,]+(?:\.\d+)?)",
                tail,
            )
            if pair:
                for field, raw in (("initial_value", pair.group(1)), ("final_value", pair.group(2))):
                    role = f"parameter:{key}:{field}"
                    checks.append(_numeric_check(role, _number(raw), item.get(field), fact, 0.02))
                    roles.add(role)
                found_numeric_role = True
                requires_unit_caveat = True
            percent = re.search(r"\(?\s*([-+]?\d+(?:\.\d+)?)\s*%\s*\)?", tail)
            if percent:
                role = f"parameter:{key}:percent_change"
                checks.append(
                    _numeric_check(
                        role,
                        float(percent.group(1)),
                        item.get("percent_change"),
                        fact,
                        _reported_number_tolerance(percent.group(1)),
                    )
                )
                roles.add(role)
                found_numeric_role = True
        if re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", tail) and not found_numeric_role:
            review.append(f"untyped_parameter_number:{key}")
        if found_numeric_role and requires_unit_caveat:
            unit_status = str(item.get("unit_status") or fact.get("unit_status") or "")
            if "unconfirmed" in unit_status and not re.search(r"原始PLC记录值|单位待核实|单位未确认", full_sentence):
                errors.append("unconfirmed_parameter_value_without_caveat")
    return {
        "matched": bool(roles or checks or errors or review),
        "checks": checks,
        "checked_roles": roles,
        "errors": errors,
        "review_reasons": review,
        "evidence": {},
    }


def _relative_label_checks(claim: str, values: dict[str, Any]) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    roles: set[str] = set()
    group_observed = None
    if re.search(r"均(?:为|呈|相对水平)?(?:相对)?(?:较|偏)?高", claim):
        group_observed = "higher"
    elif re.search(r"均(?:为|呈|相对水平)?(?:相对)?(?:较|偏)?低", claim):
        group_observed = "lower"
    elif re.search(r"均(?:为|呈|相对水平)?(?:居中|接近|中等)", claim):
        group_observed = "similar"
    for parameter, expected in values.items():
        name = PARAMETER_NAMES.get(parameter, parameter)
        mentioned_name = _parameter_alias_in_text(parameter, name, claim)
        if not mentioned_name:
            continue
        tail = re.split(
            _parameter_split_pattern(),
            claim.split(mentioned_name, 1)[1],
            maxsplit=1,
        )[0]
        observed = (
            "higher" if re.search(r"较高|更高|偏高", tail)
            else "lower" if re.search(r"较低|更低|偏低", tail)
            else "similar" if re.search(r"接近|居中|中等", tail)
            else None
        )
        observed = observed or group_observed
        if observed:
            roles.add(f"relative_label:{parameter}")
            normalized = "similar" if expected == "middle" else expected
            if observed != normalized:
                errors.append(f"relative_comparison_mismatch:{parameter}")
    return errors, roles


def _response_trend_checks(claim: str, parameters: dict[str, Any]) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    roles: set[str] = set()
    group_observed = (
        "increasing" if re.search(r"均呈(?:整体)?(?:上升|增加)", claim)
        else "decreasing" if re.search(r"均呈(?:整体)?(?:下降|降低)", claim)
        else "stable" if re.search(r"均呈(?:整体)?稳定", claim)
        else None
    )
    for parameter, item in parameters.items():
        name = str(item.get("display_name") or PARAMETER_NAMES.get(parameter, parameter))
        mentioned_name = _parameter_alias_in_text(parameter, name, claim)
        if not mentioned_name:
            continue
        tail = re.split(
            _parameter_split_pattern(),
            claim.split(mentioned_name, 1)[1],
            maxsplit=1,
        )[0]
        observed = (
            "increasing" if re.search(r"上升|增加", tail)
            else "decreasing" if re.search(r"下降|降低", tail)
            else "stable" if "稳定" in tail
            else None
        )
        observed = observed or group_observed
        if observed:
            roles.add(f"response_trend:{parameter}")
            if observed != item.get("trend"):
                errors.append(f"response_trend_mismatch:{parameter}")
    return errors, roles


def _fluctuation_checks(claim: str, parameters: dict[str, Any]) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    roles: set[str] = set()
    standalone = re.fullmatch(
        r"(?:但)?波动(?:程度|幅度|水平)?(?:为|呈)?(?:较|偏)?(高|大|明显|中等|适中|低|小)",
        claim.strip(),
    )
    if standalone:
        observed = {
            "高": "high", "大": "high", "明显": "high",
            "中等": "moderate", "适中": "moderate",
            "低": "low", "小": "low",
        }[standalone.group(1)]
        for parameter, item in parameters.items():
            roles.add(f"fluctuation_level:{parameter}")
            if observed != item.get("fluctuation_level"):
                errors.append(f"fluctuation_level_mismatch:{parameter}")
        return errors, roles
    listed_levels: dict[str, str] = {}
    listed = re.search(r"波动(?:水平|幅度)?分别为(.+)", claim)
    if listed:
        raw_levels = [value.strip(" ，,。；;）)") for value in re.split(r"[、，,]", listed.group(1))]
        present_keys = sorted(
            (
                key
                for key, item in parameters.items()
                if _parameter_alias_in_text(
                    key,
                    str(item.get("display_name") or PARAMETER_NAMES.get(key, key)),
                    claim,
                )
            ),
            key=lambda key: claim.find(
                _parameter_alias_in_text(
                    key,
                    str(parameters[key].get("display_name") or PARAMETER_NAMES.get(key, key)),
                    claim,
                )
                or str(parameters[key].get("display_name") or PARAMETER_NAMES.get(key, key))
            ),
        )
        if len(raw_levels) == len(present_keys):
            normalized = {
                "高": "high", "较高": "high", "中": "moderate",
                "中等": "moderate", "适中": "moderate", "低": "low", "较低": "low",
            }
            listed_levels = {
                key: normalized.get(level, level)
                for key, level in zip(present_keys, raw_levels)
            }
    shared_levels: dict[str, str] = {}
    shared = re.search(
        r"((?:推进速度|总推力|刀盘扭矩|刀盘转速)(?:(?:和|、)(?:推进速度|总推力|刀盘扭矩|刀盘转速))+).*?"
        r"波动(?:程度|幅度|水平)?(?:为|呈|相对)?(?:较|偏)?(高|大|明显|中等|适中|低|小)",
        claim,
    )
    if shared:
        shared_value = {
            "高": "high", "大": "high", "明显": "high",
            "中等": "moderate", "适中": "moderate",
            "低": "low", "小": "low",
        }[shared.group(2)]
        for key, item in parameters.items():
            name = str(item.get("display_name") or PARAMETER_NAMES.get(key, key))
            if name in shared.group(1):
                shared_levels[key] = shared_value
    for parameter, item in parameters.items():
        name = str(item.get("display_name") or PARAMETER_NAMES.get(parameter, parameter))
        mentioned_name = _parameter_alias_in_text(parameter, name, claim)
        if not mentioned_name:
            continue
        tail = re.split(
            _parameter_split_pattern(),
            claim.split(mentioned_name, 1)[1],
            maxsplit=1,
        )[0]
        observed = listed_levels.get(parameter) or shared_levels.get(parameter) or (
            "high" if re.search(r"波动(?:程度|幅度|水平)?(?:较高|偏高|高|较大|大|明显)", tail)
            else "moderate" if re.search(r"波动(?:程度|幅度|水平)?(?:中等|适中)", tail)
            else "low" if re.search(r"波动(?:程度|幅度|水平)?(?:较低|偏低|低|较小|小)", tail)
            else None
        )
        if observed:
            roles.add(f"fluctuation_level:{parameter}")
            if observed != item.get("fluctuation_level"):
                errors.append(f"fluctuation_level_mismatch:{parameter}")
    return errors, roles


def _required_roles(claim: str, full_sentence: str) -> set[str]:
    roles: set[str] = set()
    if re.search(r"(?:当日|今日)(?:完成)?进尺|进尺(?:为|约|：|:)", claim) and re.search(r"\d", claim):
        roles.add("daily_advance")
    if "导向盾首里程" in claim and len(re.findall(r"[\d,]+(?:\.\d+)?\s*(?:m|米)", claim, flags=re.IGNORECASE)) >= 2:
        roles.update({"chainage_start", "chainage_end"})
    if re.search(r"总?跨度", claim) and _parse_duration(claim) is not None:
        roles.add("elapsed_timestamp_span")
    if re.search(
        r"(?:累计(?:活动|作业|运行)(?:时长)?|实际(?:活动|作业|运行)?时长)",
        claim,
    ) and _parse_duration(claim) is not None:
        roles.add("cumulative_activity_duration")
    if re.search(r"(?:中间)?间隔(?:合计)?", claim) and _parse_duration(claim) is not None:
        roles.add("intervening_gap_duration")
    if re.search(r"(?:推进|空转|停止)(?:状态)?(?:累计|约|为)", claim) and _parse_duration(claim) is not None:
        roles.add("machine_state_duration")
    if re.search(r"事件.*\d+\s*(?:处|次|个|起)|\d+\s*(?:处|次|个|起).*事件", claim):
        roles.add("quality_episode_count")
    if re.search(r"(?:涉及|影响|受影响).*\d+\s*条(?:PLC)?记录|受影响(?:PLC)?记录数", claim):
        roles.add("affected_plc_record_count")
    if "记录跨度" in claim and re.search(r"\d", claim):
        roles.add("episode_span_record_count")
    if "持续" in claim and _parse_duration(claim) is not None:
        if re.search(r"(?:活动|作业|掘进时段|运行时段)", full_sentence) and not re.search(
            r"(?:参数|响应|趋势|波动|变化)", full_sentence
        ):
            roles.add("cumulative_activity_duration")
        else:
            roles.add("response_duration")
    for key, name in PARAMETER_NAMES.items():
        if name in claim and "中位数" in claim and re.search(r"\d", claim):
            roles.add(f"parameter:{key}:median")
    return roles


def _role_satisfied(required: str, actual: set[str]) -> bool:
    if required == "machine_state_duration":
        return any(role.startswith("machine_state_duration:") for role in actual)
    return required in actual


def _find_candidate_support(
    claim: str,
    *,
    full_sentence: str,
    all_facts: dict[str, dict[str, Any]],
    pool_date: str,
) -> list[str]:
    candidates = []
    for fact_id, fact in all_facts.items():
        outcome = _check_fact(
            claim,
            fact,
            full_sentence=full_sentence,
            all_facts=all_facts,
            pool_date=pool_date,
        )
        if not outcome["matched"] or outcome["errors"] or outcome["review_reasons"]:
            continue
        if outcome["checked_roles"] and all(check["status"] == "PASS" for check in outcome["checks"]):
            candidates.append(fact_id)
    return sorted(candidates)


def _time_checks(claim: str, expected_values: list[Any], fact: dict[str, Any]) -> list[dict[str, Any]]:
    found = _clock_values(claim)
    if not found:
        return []
    expected = [_clock_text(value) for value in expected_values if value]
    checks = []
    for value in found:
        allowed = [candidate for candidate in expected if _clock_equal(value, candidate)]
        checks.append(
            {
                "kind": "numeric",
                "fact_id": fact["fact_id"],
                "role": "clock_time",
                "observed": value,
                "expected": expected,
                "tolerance": "exact_to_reported_precision",
                "status": "PASS" if allowed else "FAIL",
                "error_type": None if allowed else "time_boundary_mismatch",
            }
        )
    return checks


def _numeric_check(
    role: str,
    observed: Any,
    expected: Any,
    fact: dict[str, Any],
    tolerance: float = 0.001,
) -> dict[str, Any]:
    valid = observed is not None and expected is not None and math.isclose(
        float(observed), float(expected), abs_tol=tolerance, rel_tol=0.0
    )
    return {
        "kind": "numeric",
        "fact_id": fact["fact_id"],
        "role": role,
        "observed": observed,
        "expected": expected,
        "tolerance": tolerance,
        "status": "PASS" if valid else "FAIL",
        "error_type": None if valid else f"numeric_role_mismatch:{role}",
    }


def _value_check(role: str, observed: Any, expected: Any, fact: dict[str, Any]) -> dict[str, Any]:
    valid = observed == expected
    return {
        "kind": "structured",
        "fact_id": fact["fact_id"],
        "role": role,
        "observed": observed,
        "expected": expected,
        "tolerance": "exact",
        "status": "PASS" if valid else "FAIL",
        "error_type": None if valid else f"structured_role_mismatch:{role}",
    }


def _outcome(
    matched: bool,
    *,
    checks: list[dict[str, Any]] | None = None,
    roles: set[str] | None = None,
    errors: list[str] | None = None,
    review: list[str] | None = None,
) -> dict[str, Any]:
    checks = checks or []
    return {
        "matched": matched,
        "checks": checks,
        "checked_roles": roles or set(),
        "errors": errors or [],
        "review_reasons": review or [],
        "evidence": {
            "checked_roles": sorted(roles or set()),
            "check_count": len(checks),
        },
    }


def _claim_result(
    claim: str,
    status: str,
    errors: list[str],
    checks: list[dict[str, Any]],
    checked_roles: list[str],
    verified: set[str],
    failed: set[str],
    reviewed: set[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "claim": claim,
        "status": status,
        "error_types": sorted(set(errors)),
        "checked_roles": sorted(checked_roles),
        "verified_fact_ids": sorted(verified),
        "failed_fact_ids": sorted(failed),
        "review_fact_ids": sorted(reviewed),
        "numeric_checks": checks,
        "evidence": evidence,
    }


def _check_response_order(rows: list[dict[str, Any]], mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = []
    for row in rows:
        if row["section_id"] != "responses":
            continue
        response_ids = [
            fact_id
            for fact_id in row["supporting_fact_ids"]
            if mapping.get(fact_id, {}).get("fact_type") == "observed_construction_response"
        ]
        starts = [mapping[fact_id]["data"].get("start_time") for fact_id in response_ids]
        if starts:
            ordered.append((min(starts), row["sentence_index"], response_ids))
    actual = [value[0] for value in ordered]
    status = "PASS" if actual == sorted(actual) else "FAIL"
    return [{
        "check": "response_chronological_order",
        "status": status,
        "error_type": None if status == "PASS" else "response_event_order_error",
        "observed_start_times": actual,
        "expected_start_times": sorted(actual),
        "fact_ids": [fact_id for _, _, ids in ordered for fact_id in ids],
    }]


def _check_internal_contradictions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parameter_number_reported = any(
        re.search(r"推进速度|总推力|刀盘扭矩|刀盘转速", row["text"])
        and re.search(r"\d", row["text"])
        for row in rows
    )
    contradiction_rows = [
        row
        for row in rows
        if re.search(r"未(?:写入|用于|纳入).{0,12}参数(?:结论|数值|报告)", row["text"])
    ]
    status = "FAIL" if parameter_number_reported and contradiction_rows else "PASS"
    return [{
        "check": "parameter_reporting_quality_statement_consistency",
        "status": status,
        "error_type": None if status == "PASS" else "parameter_value_quality_statement_contradiction",
        "fact_ids": [fact_id for row in contradiction_rows for fact_id in row["supporting_fact_ids"]],
        "sentences": [row["text"] for row in contradiction_rows],
    }]


def _check_structure(draft: dict[str, Any]) -> dict[str, Any]:
    actual = [(section.get("section_id"), section.get("heading")) for section in draft.get("sections", [])]
    expected = list(SECTION_SPECS)
    return {"status": "PASS" if actual == expected else "FAIL", "actual": actual, "expected": expected}


def _build_metrics(
    rows: list[dict[str, Any]],
    global_checks: list[dict[str, Any]],
    numeric_checks: list[dict[str, Any]],
    required: list[str],
    covered: list[str],
    structure: dict[str, Any],
) -> dict[str, Any]:
    value_checks = [check for check in numeric_checks if check.get("kind") == "numeric"]
    numeric_pass = sum(check["status"] == "PASS" for check in value_checks)
    numeric_fail = sum(check["status"] == "FAIL" for check in value_checks)
    claims = [claim for row in rows for claim in row["claims"] if claim["status"] != "N/A"]
    pass_count = sum(claim["status"] == "PASS" for claim in claims)
    fail_count = sum(claim["status"] == "FAIL" for claim in claims)
    review_count = sum(claim["status"] == "REVIEW" for claim in claims)
    global_fail = sum(check["status"] == "FAIL" for check in global_checks)
    decision_denominator = len(claims) + len(global_checks)
    metrics = {
        "deterministic_numeric_check_pass_rate": _ratio(numeric_pass, numeric_pass + numeric_fail),
        "numeric_check_pass_count": numeric_pass,
        "numeric_check_fail_count": numeric_fail,
        "required_fact_verified_coverage": _ratio(len(covered), len(required), empty=1.0),
        "required_fact_count": len(required),
        "covered_required_fact_count": len(covered),
        "fully_verified_claim_rate": _ratio(pass_count, len(claims)),
        "explicit_failure_rate": _ratio(fail_count + global_fail, decision_denominator),
        "review_rate": _ratio(review_count, decision_denominator),
        "deterministic_decision_rate": _ratio(
            pass_count + fail_count + len(global_checks),
            decision_denominator,
        ),
        "pass_claim_count": pass_count,
        "fail_claim_count": fail_count + global_fail,
        "review_claim_count": review_count,
        "factual_claim_count": len(claims),
        "global_check_count": len(global_checks),
        "structure_ok": structure["status"] == "PASS",
        "sentence_count": len(rows),
        "character_count": sum(len(row["text"]) for row in rows),
        "metric_warning": "These are deterministic rule-verifier rates, not engineering fact accuracy.",
    }
    # Compatibility names used by the report experiment summary. They point to
    # the stricter current metrics and do not restore the retired verifier.
    metrics.update(
        {
            "numeric_fact_accuracy": metrics["deterministic_numeric_check_pass_rate"],
            "required_fact_coverage": metrics["required_fact_verified_coverage"],
            "unsupported_fail_rate": metrics["explicit_failure_rate"],
        }
    )
    return metrics


def _error_counts(rows: list[dict[str, Any]], global_checks: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        for claim in row["claims"]:
            counts.update(claim["error_types"])
    counts.update(
        check["error_type"]
        for check in global_checks
        if check.get("status") == "FAIL" and check.get("error_type")
    )
    return counts


def _quality_aliases(data: dict[str, Any]) -> list[str]:
    return {
        "interval_speed_distance_mismatch": ["区间速度与距离不匹配", "速度距离不匹配"],
        "unreliable_canonical_rpm": ["刀盘转速", "规范转速", "转速数据"],
        "unreliable_advance_speed": ["推进速度"],
        "unreliable_penetration": ["贯入度"],
        "hard_sampling_gap": ["采样缺口"],
        "sampling_gap": ["采样缺口"],
    }.get(str(data.get("subtype")), [str(data.get("subtype") or "")])


def _process_times(data: dict[str, Any]) -> list[Any]:
    values = [data.get("start_time"), data.get("end_time")]
    for activity in data.get("activities", []):
        values.extend([activity.get("start_time"), activity.get("end_time")])
    return values


def _clock_values(text: str) -> list[str]:
    return [match.group(0) for match in re.finditer(r"(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?!\d)", text)]


def _period_ranges(text: str) -> set[str]:
    values = set()
    for match in re.finditer(
        r"((?:[01]\d|2[0-4]):[0-5]\d)\s*(?:—|–|-|至|到)\s*((?:[01]\d|2[0-4]):[0-5]\d)",
        text,
    ):
        values.add(f"{match.group(1)}—{match.group(2)}")
    return values


def _normalize_period(value: str) -> str:
    values = _period_ranges(value)
    return next(iter(values), value.strip())


def _clock_text(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.split("T", 1)[1]
    return text[:8] if len(text) >= 8 else text[:5]


def _clock_equal(observed: str, expected: str) -> bool:
    return observed == expected if len(observed) == 8 else observed[:5] == expected[:5]


def _time_matches(observed: str, expected_values: list[Any]) -> bool:
    return any(_clock_equal(observed, _clock_text(value)) for value in expected_values if value)


def _parse_duration(text: str) -> float | None:
    pattern = re.compile(
        r"(?:(\d+(?:\.\d+)?)\s*小时)?\s*(?:(\d+(?:\.\d+)?)\s*分钟)?\s*(?:(\d+(?:\.\d+)?)\s*秒)?"
    )
    for match in pattern.finditer(text):
        if any(match.groups()):
            hours, minutes, seconds = (
                float(value or 0.0) for value in match.groups()
            )
            return hours * 3600.0 + minutes * 60.0 + seconds
    found = re.search(r"(\d+(?:\.\d+)?)\s*(?:min|分钟)", text, flags=re.IGNORECASE)
    return float(found.group(1)) * 60.0 if found else None


def _duration_tolerance(text: str) -> float:
    """Match tolerance to the precision explicitly used in report prose."""
    if "秒" in text and "分钟" not in text and "小时" not in text:
        return 1.0
    return 60.0


def _reported_number_tolerance(value: str) -> float:
    """Allow ordinary rounding at the precision shown in report prose."""
    decimals = len(value.split(".", 1)[1]) if "." in value else 0
    return 0.5 * (10 ** -decimals) + 1e-9


def _number(value: Any) -> float:
    return float(str(value).replace(",", ""))


def _small_integer(value: str) -> int:
    if value.isdigit():
        return int(value)
    values = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return values[value]


def _is_nonfactual_guardrail(text: str) -> bool:
    if UNSUPPORTED_ASSERTION.search(text):
        return False
    return bool(
        re.search(
            r"不(?:解释|代表|等同|推断|认定)|仅表示|单位待核实|单位未确认|"
            r"不得(?:理解|视为)|不能确认|数据使用限制|研究检查|原始PLC记录值|"
            r"信息不足|^按.+统计(?:显示)?$|^运行状态时长统计显示$|"
            r"^数据质量方面$|^在.+记录中$|^(?:当日)?推进状态参数统计中$|"
            r"^同日参数对比显示$|^当日可比持续运行时段对比显示$|^其中$",
            text,
        )
    )


def _worst_status(statuses: list[str]) -> str:
    return max(statuses or ["N/A"], key=lambda status: STATUS_RANK[status])


def _ratio(numerator: float, denominator: float, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _declared_fact_ids(draft: dict[str, Any]) -> set[str]:
    return {
        fact_id
        for section in draft["sections"]
        for sentence in section["sentences"]
        for fact_id in sentence.get("supporting_fact_ids", [])
    }


def _role_signatures(verification: dict[str, Any], *, numeric_only: bool) -> set[str]:
    signatures: set[str] = set()
    for row in verification["sentences"]:
        for claim in row["claims"]:
            for role in claim["checked_roles"]:
                if not numeric_only or _numeric_role_name(role):
                    signatures.add(role)
    return signatures


def _numeric_role_name(role: str) -> bool:
    return any(
        token in role
        for token in (
            "daily_advance", "chainage_", "duration", "percentage", "count",
            "clock_time", "p10", "median", "p90", "left_value", "right_value",
            "difference", "initial_value", "final_value", "percent_change",
        )
    )


def _retention_block(before: set[str], after: set[str]) -> dict[str, Any]:
    return {
        "before_count": len(before),
        "after_count": len(after),
        "retained_count": len(before & after),
        "retention_rate": _ratio(len(before & after), len(before), empty=1.0),
        "removed": sorted(before - after),
        "added": sorted(after - before),
    }


def _claim_counts(verification: dict[str, Any]) -> dict[str, int]:
    counts = Counter(
        claim["status"]
        for row in verification["sentences"]
        for claim in row["claims"]
    )
    return dict(sorted(counts.items()))


def _draft_characters(draft: dict[str, Any]) -> int:
    return sum(
        len(str(sentence["text"]))
        for section in draft["sections"]
        for sentence in section["sentences"]
    )
