"""Separate execution, automated content checks, and release eligibility."""
from copy import deepcopy
import re

from .common import load, save, sha
from .schema import TITLES


LABELS = {
    'AUTO_CHECKED': '已完成自动检查；工程语义仍待独立审查',
    'LIMITED_INPUT': '资料受限版；未提供无法可靠判断的部分',
    'REVIEW_REQUIRED': '待审稿；存在未解决检查项，不可作为正式施工依据',
    'INPUT_BLOCKED': '输入完整性或准入检查不通过，未生成报告',
    'SYSTEM_FAILURE': '运行未完成；见失败记录',
}


def structure_errors(value, *, plan=False):
    if not isinstance(value, dict) or not isinstance(value.get('sections'), list):
        return ['invalid_sections_schema']
    errors = []
    seen = set()
    for section in value['sections']:
        if not isinstance(section, dict) or not isinstance(section.get('section_id'), str):
            errors.append('invalid_section_schema')
            continue
        sid = section['section_id']
        if sid not in TITLES or sid in seen:
            errors.append('unknown_or_duplicate_section:' + sid)
        seen.add(sid)
        if not isinstance(section.get('paragraphs'), list):
            errors.append('invalid_paragraph_list:' + sid)
            continue
        for index, paragraph in enumerate(section['paragraphs'], 1):
            prefix = f'{sid}:{index}:'
            if not isinstance(paragraph, dict):
                errors.append(prefix + 'invalid_paragraph_schema')
                continue
            ids = paragraph.get('fact_ids')
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                errors.append(prefix + 'invalid_fact_ID_list')
            if not plan and (not isinstance(paragraph.get('text'), str) or not paragraph['text'].strip()):
                errors.append(prefix + 'missing_paragraph_text')
    if plan:
        omissions = value.get('omissions')
        if not isinstance(omissions, list) or any(not isinstance(r, dict) for r in omissions):
            errors.append('invalid_omission_schema')
    return errors


def rule_plan(pack, section_ids=None):
    """Bounded fallback over the same fact pool; never invent or omit a fact."""
    sections = list(section_ids or TITLES)
    return {
        'sections': [
            {'section_id': sid, 'paragraphs': [
                {'purpose': '依据同节现有事实组织正文；本次使用程序保守提纲',
                 'fact_ids': [f['fact_id'] for f in pack['facts'] if f['section'] == sid]}
            ] if any(f['section'] == sid for f in pack['facts']) else []}
            for sid in sections
        ],
        'omissions': [],
    }


def review_view(draft):
    """Display available text without fabricating missing sections or source IDs."""
    if not isinstance(draft, dict) or not isinstance(draft.get('sections'), list):
        return None
    result, changes = [], []
    for section in draft['sections']:
        if not isinstance(section, dict) or not isinstance(section.get('section_id'), str):
            continue
        paragraphs = []
        rows = section.get('paragraphs', [])
        if not isinstance(rows, list):
            continue
        for index, paragraph in enumerate(rows):
            if not isinstance(paragraph, dict) or not isinstance(paragraph.get('text'), str) or not paragraph['text'].strip():
                continue
            row = deepcopy(paragraph)
            ids = row.get('fact_ids')
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                row['fact_ids'] = []
                changes.append({'section_id': section['section_id'], 'paragraph_index': index,
                                'reason': 'invalid_source_binding_not_invented'})
            paragraphs.append(row)
        if paragraphs:
            result.append({'section_id': section['section_id'], 'paragraphs': paragraphs})
    if not result:
        return None
    return {'draft': {'sections': result}, 'display_changes': changes,
            'semantic_status': 'REVIEW', 'not_a_repaired_or_accepted_narrative': True}


def issue_kind(error):
    name = re.sub(r'^\d(?:\.\d)?:\d+:', '', str(error))
    if any(t in name for t in ['unsupported_numeric', 'typed_quantity', 'unsupported_grade',
            'duration_role', 'wrong_activity', 'causal', 'safety_conclusion', 'unsupported_added',
            'promoted', 'comparator', 'wrong_source', 'unknown_support', 'coverage_contradicted']):
        return 'factual_or_source_issue'
    if any(t in name for t in ['missing_required', 'omitted', 'section', 'support_ID', 'schema']):
        return 'structure_or_coverage_issue'
    return 'unresolved_check_item'


def decide(folder, check, warnings=None):
    limits = load(folder / 'input_limitations.json') if (folder / 'input_limitations.json').exists() else None
    limitations = []
    if limits:
        limitations.append({'reason': limits.get('reason', 'existing_input_limitation'),
            'message': '前方方向或窗口无法可靠确定，前方覆盖记为未知，未当作无预报。'})
    pack_path = folder / 'unified_report_input.json'
    if pack_path.exists():
        day_fact = next((f for f in load(pack_path)['facts'] if f['fact_id'] == 'DAY'), None)
        if day_fact and day_fact.get('data', {}).get('daily_comparison_allowed') is False:
            limitations.append({'reason': 'frozen_PLC_daily_comparison_not_allowed',
                'message': '统计范围为实际有效记录，缺失时段单独列示；不影响生成日报。',
                'source_fact_id': 'DAY', 'new_quality_threshold_introduced': False})
    if limitations:
        limits = {**(limits or {}), 'limitations': limitations}
    errors = list(check.get('errors', []))
    # Missing observation is reported, not treated as a whole-report failure.
    status = 'REVIEW_REQUIRED' if not check.get('passed') or errors else 'AUTO_CHECKED'
    record = {
        'status': status, 'label': LABELS[status],
        'automated_body_checks_passed': bool(check.get('passed')) and not errors,
        'formal_release_blocked': status == 'REVIEW_REQUIRED',
        'issues': [{'error': e, 'kind': issue_kind(e)} for e in errors],
        'input_limitations': limits, 'execution_warnings': list(warnings or []),
        'input_quality_is_not_a_whole_report_release_gate': True,
        'presentation_advisories': check.get('review_required', []),
        'independent_engineering_fact_accuracy': 'NOT_MEASURED',
        'human_approval': 'NOT_PROVIDED',
        'checked_narrative_SHA256': sha(folder / 'final_narrative.json'),
    }
    save(folder / 'delivery_decision.json', record)
    return record


def failed(folder, status, stage, reason, **extra):
    record = {'status': status, 'label': LABELS[status], 'stage': stage, 'reason': reason,
              'formal_release_blocked': True, 'PDF_generated': False, **extra}
    save(folder / 'run.result.json', record)
    return record
