from __future__ import annotations

import json
import math
from typing import Any

from .fact_adapter import fact_map


DRAFT_SCHEMA = "tbm_supported_report_draft_v1"

SECTION_SPECS = (
    ("overview", "一、当日施工概况"),
    ("main_process", "二、主要施工过程"),
    ("responses", "三、值得关注的运行情况"),
)


def build_template_draft(
    pool: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Build the non-LLM baseline from the same facts and plan."""
    mapping = fact_map(pool)
    sections = []
    for section_id, heading in SECTION_SPECS:
        section_plan = next(
            section for section in plan["sections"] if section["section_id"] == section_id
        )
        ids = section_plan["required_fact_ids"] + section_plan["optional_fact_ids"]
        sentences = []
        for fact_id in ids:
            text = render_fact_sentence(mapping[fact_id])
            if text:
                sentences.append(
                    {"text": text, "supporting_fact_ids": [fact_id]}
                )
        if section_id == "responses" and not sentences:
            sentences.append(
                {
                    "text": "现有事实未提供需要单独写入日报的代表性运行响应。",
                    "supporting_fact_ids": [],
                }
            )
        sections.append(
            {"section_id": section_id, "heading": heading, "sentences": sentences}
        )
    draft = {
        "schema_version": DRAFT_SCHEMA,
        "date": pool["date"],
        "method_group": "A",
        "sections": sections,
    }
    validate_draft(draft, known_fact_ids=set(mapping))
    return draft


def render_fact_sentence(fact: dict[str, Any]) -> str:
    data = fact["data"]
    fact_type = fact["fact_type"]
    if fact_type == "data_coverage":
        return (
            f"PLC数据覆盖{_hm(data.get('start_time'))}—{_hm(data.get('end_time'))}，"
            f"共记录{int(data.get('sample_count') or 0)}条数据。"
        )
    if fact_type == "daily_advance":
        return (
            f"按导向盾首里程首末差计算，当日进尺为{_number(data.get('distance_m'))} m，"
            f"导向盾首里程由{_number(data.get('chainage_start'))} m变化至"
            f"{_number(data.get('chainage_end'))} m。"
        )
    if fact_type == "machine_state_duration":
        states = "、".join(
            f"{state['display_name']}{_duration(state.get('duration_seconds'))}"
            f"（{_number(state.get('percentage'))}%）"
            for state in data.get("states", [])
        )
        gap_note = "，硬采样缺口未计入停止时间" if data.get("hard_gaps_excluded") else ""
        return f"按原状态分类规则统计：{states}{gap_note}。"
    if fact_type == "coverage_limitation":
        return (
            f"当日识别到{int(data.get('hard_sampling_gap_count') or 0)}处硬采样缺口；"
            "缺口时段不计入停止时间。"
        )
    if fact_type == "daily_advancing_parameter_statistics":
        values = []
        for parameter in ("advance_speed", "thrust", "torque"):
            stats = data.get("parameters", {}).get(parameter)
            if stats and stats.get("median") is not None:
                values.append(
                    f"{stats['display_name']}中位数{_number(stats['median'])}"
                )
        if not values:
            return ""
        return (
            "推进状态质量控制记录的主要参数统计为："
            + "、".join(values)
            + "；以上均为原始PLC记录值，工程单位待核实，仅用于研究检查。"
        )
    if fact_type == "representative_construction_period":
        continuity = data.get("continuity_class")
        if continuity == "intermittent_activities":
            prefix = "时段内包含多段有间隔的运行活动"
        else:
            prefix = "时段内记录到单段连续运行活动"
        activity = {
            "confirmed_excavation": "，其中存在有进尺证据支持的掘进作业",
            "advance_related_operation": "，其性质为推进相关运行",
            "mixed_excavation_and_advance_related_operation": "，其中同时包含掘进作业和推进相关运行",
        }.get(data.get("activity_expression_class"), "")
        text = (
            f"{_hm(data.get('start_time'))}—{_hm(data.get('end_time'))}，{prefix}{activity}；"
            f"时间总跨度约{_duration(data.get('elapsed_span_seconds'))}，"
            f"累计活动时长约{_duration(data.get('total_activity_duration_seconds'))}"
        )
        if float(data.get("total_intervening_gap_seconds") or 0) > 0:
            text += (
                f"，中间间隔合计约{_duration(data.get('total_intervening_gap_seconds'))}，"
                "不得理解为全程连续运行"
            )
        levels = _level_text(data.get("relative_parameter_levels", {}))
        if levels:
            text += f"；按可比持续运行片段统计，{levels}"
        return text + "。"
    if fact_type == "within_day_parameter_comparison":
        left = str(data.get("left_period") or "左侧时段").replace("_", "—")
        right = str(data.get("right_period") or "右侧时段").replace("_", "—")
        differences = _comparison_text(data.get("relative_differences", {}))
        if not differences:
            return ""
        return f"与{left}相比，{right}的可比持续运行片段中，{differences}。"
    if fact_type == "observed_construction_response":
        changes = []
        fluctuating = []
        for item in data.get("parameters", {}).values():
            name = item.get("display_name")
            if item.get("trend") == "increasing":
                changes.append(f"{name}整体上升")
            elif item.get("trend") == "decreasing":
                changes.append(f"{name}整体下降")
            if item.get("fluctuation_level") == "high":
                fluctuating.append(str(name))
        text = (
            f"{_hm(data.get('start_time'))}—{_hm(data.get('end_time'))}，"
            + ("、".join(changes) if changes else "主要参数出现持续变化")
            + f"，持续约{_duration(data.get('duration_seconds'))}"
        )
        if fluctuating:
            text += f"，其中{'、'.join(fluctuating)}波动程度较高"
        return text + "；该描述仅表示观测响应，不解释其地质或设备原因。"
    if fact_type == "data_quality_limitation":
        subtype = {
            "unreliable_advance_speed": "推进速度不可信",
            "interval_speed_distance_mismatch": "区间速度与距离不匹配",
            "unreliable_canonical_rpm": "刀盘转速可疑",
            "unreliable_penetration": "贯入度可疑",
            "hard_sampling_gap": "硬采样缺口",
            "sampling_gap": "采样缺口",
        }.get(data.get("subtype"), str(data.get("subtype")))
        return (
            f"数据质量检查识别到{subtype}事件{int(data.get('event_count') or 0)}处，"
            f"涉及{int(data.get('affected_record_count') or 0)}条PLC记录；"
            "该信息仅作为数据使用限制，不解释为设备故障。"
        )
    return ""


def render_draft_markdown(draft: dict[str, Any]) -> str:
    validate_draft(draft)
    blocks = []
    for section in draft["sections"]:
        blocks.append(f"## {section['heading']}")
        sentences = [str(item["text"]).strip() for item in section["sentences"]]
        blocks.append("\n\n".join(sentence for sentence in sentences if sentence))
    return "\n\n".join(blocks).strip() + "\n"


def validate_draft(
    draft: dict[str, Any], *, known_fact_ids: set[str] | None = None
) -> None:
    if draft.get("schema_version") != DRAFT_SCHEMA:
        raise ValueError("Unsupported report draft schema")
    sections = draft.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise ValueError("Report draft must contain exactly three sections")
    actual = [(section.get("section_id"), section.get("heading")) for section in sections]
    if tuple(actual) != SECTION_SPECS:
        raise ValueError(f"Unexpected report section structure: {actual}")
    for section in sections:
        if not isinstance(section.get("sentences"), list):
            raise ValueError("Each report section must contain a sentence list")
        for item in section["sentences"]:
            if not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ValueError("Draft sentence text must be non-empty")
            ids = item.get("supporting_fact_ids")
            if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
                raise ValueError("supporting_fact_ids must be a string list")
            if known_fact_ids is not None:
                missing = set(ids) - known_fact_ids
                if missing:
                    raise ValueError(f"Draft references missing facts: {sorted(missing)}")


def parse_model_draft(text: str, *, date: str, group: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1]
        value = value.rsplit("```", 1)[0]
    payload = json.loads(value)
    if "schema_version" not in payload:
        payload["schema_version"] = DRAFT_SCHEMA
    payload["date"] = date
    payload["method_group"] = group
    return payload


def _hm(value: Any) -> str:
    if not value:
        return "时间未知"
    text = str(value)
    return text[11:16] if "T" in text and len(text) >= 16 else text


def _duration(value: Any) -> str:
    seconds = max(float(value or 0.0), 0.0)
    if 0 < seconds < 60:
        return "不足1分钟"
    # Use conventional half-up rounding so 45,750 seconds is 763 minutes,
    # instead of Python's banker's rounding to 762.
    minutes = int(seconds / 60.0 + 0.5)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}小时{remainder}分钟"
    if hours:
        return f"{hours}小时"
    return f"{minutes}分钟"


def _number(value: Any) -> str:
    if value is None:
        return "未知"
    number = float(value)
    if math.isclose(number, round(number), abs_tol=1e-9):
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _level_text(levels: dict[str, Any]) -> str:
    names = {
        "advance_speed": "推进速度",
        "thrust": "总推力",
        "torque": "刀盘扭矩",
        "rpm": "刀盘转速",
    }
    labels = {"higher": "较高", "middle": "居中", "lower": "较低"}
    return "、".join(
        f"{names.get(parameter, parameter)}{labels.get(level, level)}"
        for parameter, level in levels.items()
    )


def _comparison_text(values: dict[str, Any]) -> str:
    names = {
        "advance_speed": "推进速度",
        "thrust": "总推力",
        "torque": "刀盘扭矩",
        "rpm": "刀盘转速",
    }
    labels = {
        "higher": "整体较高",
        "lower": "整体较低",
        "similar": "整体接近",
        "middle": "整体接近",
    }
    return "、".join(
        f"{names.get(parameter, parameter)}{labels.get(level, level)}"
        for parameter, level in values.items()
    )
