"""Exploratory mechanism diagnostics, not new primary metrics or production rules."""
from common import *


def main():
    day='2023-11-22';folder=HERE/'runs/formal/D'/day
    if not (folder/'revision.diff.json').exists():raise RuntimeError('specified_real_case_missing')
    diff=load(folder/'revision.diff.json');pack=load(folder/'unified_report_input.json')
    coverage=load(folder/'report_organization.json')['coverage']
    facts={f['fact_id']:f for f in pack['facts']}
    before=next(s for s in diff['before']['sections'] if s['section_id']=='1')['paragraphs'][2]
    after=next(s for s in diff['after']['sections'] if s['section_id']=='1')['paragraphs'][2]
    geo_before=next(s for s in diff['before']['sections'] if s['section_id']=='3.3')['paragraphs'][1]
    geo_after=next(s for s in diff['after']['sections'] if s['section_id']=='3.3')['paragraphs'][1]
    result={'status':'EXPLORATORY_POSTHOC_MECHANISM_CASE_NOT_A_NEW_PRIMARY_ENDPOINT','date':day,
        'coverage':{'before':before,'after':after,'actual_uncovered_intervals':coverage['uncovered_intervals'],
            'bound_fact':facts['COVERAGE_QUALITY'],
            'interpretation':'14:56:20 is present in structured gap data but absent from canonical text. Membership checker flagged a supported timestamp; patch removed the exact time rather than correcting an engineering value.'},
        'source_binding':{'before':geo_before,'after':geo_after,'ahead_window':pack['coverage']['ahead']['range_m'],
            'bound_sources':[{k:facts[i][k] for k in ['fact_id','role','canonical_text','sources']} for i in geo_after['fact_ids']],
            'source_115':[f for f in pack['facts'] if f['role']=='forecast_risk_not_event' and '115.00' in f['canonical_text']],
            'interpretation':'115 is outside current forward window. Removing its number cleared a numeric rule, but the paragraph still mentions HSP while binding only design-risk labels. Immutable IDs cannot add a missing source.'},
        'human_labels_not_filled':True,'no_production_rule_or_prompt_changed_after_test':True}
    save(HERE/'POSTHOC_MECHANISM_CASE.json',result)
    (HERE/'POSTHOC_MECHANISM_CASE.md').write_text('''# 正式样本中的机制局限案例

这是实验开始后的探索性案例审读，不是新增主要指标，没有据此修改冻结方法、提示词或样本。

## 2023-11-22：正确时间被规则提示

C稿和编辑稿写“14:56:20之后至日终的未观测时段”。独立读取结构化缺口表可确认，该时间是真实缺口起点；但COVERAGE_QUALITY的canonical_text没有该秒数，只有data里保留完整区间。因此数字字面检查报unsupported_numeric_literal:20。局部补丁把精确时间删掉，程序通过，但这不能算纠正了错误工程事实，也显示有删除正确细节的风险。

## 同日：删去错误位置后仍可能漏绑来源

前方窗口为DyK1014+117至+137，原稿在前方段落带入HSP的+115掉块位置。补丁删掉了115数值，规则通过，但段落仍称HSP提示掉块，支持ID仍全部是设计风险标签，没有绑定相应HSP事实。程序保证ID不变可以防止局部补丁漂移，也会使“原稿漏绑来源”无法仅靠改文字得到修复。

上述第一项可用结构化时间直接验证；第二项来源类型和窗口关系也可直接检查，剩余语义及信息完整性仍须独立审阅。本案例不能外推自然错误比例，但足以说明规则通过、ID保持及补丁完成不等于事实正确或正确信息完整保留。
''',encoding='utf-8')


if __name__=='__main__':main()
