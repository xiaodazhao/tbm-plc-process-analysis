"""Presentation advice is not a factual error. Keep real evidence checks separate."""
from decimal import Decimal
import re

VERSION = 'practical_report_policy_20261030_v1.1'


def presentation_notes(text, facts, section):
    notes = []
    if any(f['section'] != section for f in facts):
        notes.append('cross_section_citation_check_relevance_not_section_number')
    if re.search('导致|因此|延迟更新|集中更新|通信故障|采集故障', text):
        notes.append('meaning_and_attribution_not_established_by_keyword_alone')
    if re.search('中等速度|中等扭矩|较高推力|效率低下|正常范围', text):
        notes.append('qualitative_comparison_reference_requires_semantic_review')
    if re.search('折算推进率|参数.*见表|参数表|水平见表', text):
        notes.append('terminology_or_figure_reference_editorial_advice_only')
    return notes


def activity_quantities(text, facts):
    """Bind to activity IDs, not an obligatory second-resolution prose prefix."""
    from .progress_investigation import reading_quantity_supported
    activities = [f for f in facts if f['fact_id'].startswith('ACTIVITY:')]
    errors, reviews = [], []
    for sentence in re.split('[。；\n]', text):
        for match in re.finditer(r'(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(m/h|m)(?![A-Za-z/])', sentence):
            if reading_quantity_supported(sentence, match[1], match[2], facts, paragraph_context=text):
                continue
            # Locations and daily totals may share the paragraph; only an explicit
            # activity quantity label establishes this parser's limited scope.
            prefix = sentence[max(0, match.start()-28):match.start()]
            if not re.search('进尺|推进效率|折算推进率|效率', prefix):
                continue
            if not activities:
                investigated = any(f.get('role') == 'data_consistency_investigation_not_confirmed_cause' for f in facts)
                if investigated and re.search('实际进尺|本段进尺|该段进尺|分段进尺|推进效率', prefix):
                    errors.append('unadopted_counter_delta_written_as_actual_advance')
                else:
                    reviews.append('activity_quantity_binding_not_resolved')
                continue
            field = 'rate_m_per_advancing_hour' if match[2] == 'm/h' else 'progress_m'
            values = [f['data'].get(field) for f in activities]
            allowed = {Decimal(f'{v:.2f}') for v in values if v is not None}
            if Decimal(match[1]) not in allowed:
                errors.append('interval_quantity_wrong_activity_scope')
            elif len(activities) > 1:
                reviews.append('multiple_activity_IDs_quantity_owner_requires_semantic_review')
    return sorted(set(errors)), sorted(set(reviews))
