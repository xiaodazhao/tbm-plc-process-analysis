"""Generate descriptive figures, candid results, and a verified evidence archive."""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tbm_process.reporting_v2.facts import digest, write_json
from .common import HERE, PROJECT, read, write_csv
from .independent_audit import audit_reports, audit_spatial


def text_file(path,text):
    path.write_text(text.strip()+'\n',encoding='utf-8')


def figures(output):
    dest=output/'figures';dest.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['PingFang SC','Arial Unicode MS','DejaVu Sans'],
                         'axes.unicode_minus':False,'font.size':10})
    frame=pd.DataFrame(read(output/'B_geology_response_v2/attribute_response_rows.json'))
    names={'advance_speed':'推进速度 (mm/min)','thrust':'总推力 (kN)','torque':'刀盘扭矩 (kN·m)'}
    fig,axes=plt.subplots(2,3,figsize=(14,8))
    for row,kind in enumerate(['TSP','HSP']):
        part=frame.loc[frame.source_type.eq(kind)]
        categories=sorted(part.integrity_category.unique())
        for col,(key,name) in enumerate(names.items()):
            ax=axes[row,col]
            ax.boxplot([part.loc[part.integrity_category.eq(c),key+'_median'] for c in categories],
                       tick_labels=[c+'\nn='+str(sum(part.integrity_category.eq(c))) for c in categories],widths=.5)
            ax.set_title(kind+' / '+name);ax.tick_params(axis='x',labelrotation=25,labelsize=8);ax.grid(axis='y',alpha=.2)
    fig.suptitle('原始地质描述类别下的区段中位数分布：仅描述性比较，区段共享报告且未控制全部操作条件')
    fig.tight_layout(rect=(0,0,1,.95));fig.savefig(dest/'B_attribute_cell_distributions.png',dpi=180);plt.close(fig)
    pairs=pd.read_csv(output/'B_geology_response_v2/boundary_descriptive_pairs.csv')
    fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True)
    for ax,(key,name) in zip(axes,names.items()):
        ok=pairs.rpm_diagnostic_passed
        ax.scatter(pairs.loc[~ok,'boundary_m']-1013000,pairs.loc[~ok,key+'_percent'],s=12,c='#9b9b9b',label='未通过转速诊断')
        ax.scatter(pairs.loc[ok,'boundary_m']-1013000,pairs.loc[ok,key+'_percent'],s=14,c='#237a78',label='转速差≤15%（非严格同工况）')
        ax.axhline(0,color='#555555',linewidth=.7);ax.set_ylabel(name+'\n右/左变化 (%)');ax.grid(alpha=.15)
    axes[0].legend(fontsize=9);axes[-1].set_xlabel('名义盾首里程 - DyK1013+000 (m)')
    fig.suptitle('原始预报范围边界附近的描述性差异；边界不等于真实地质界线')
    fig.tight_layout(rect=(0,0,1,.95));fig.savefig(dest/'B_boundary_changes.png',dpi=180);plt.close(fig)
    examples=read(output/'B_geology_response_v2/similar_description_different_response_examples.json')
    if examples:
        e=examples[0];rows=[frame.loc[frame.source_type.eq(e['source_type'])&frame.cell_id.eq(e[k])].iloc[0] for k in ['left_cell_id','right_cell_id']]
        fig,axes=plt.subplots(1,3,figsize=(12,4))
        for ax,(key,name) in zip(axes,names.items()):
            ax.bar(['区段A','区段B'],[r[key+'_median'] for r in rows],color=['#237a78','#a97250']);ax.set_title(name)
        fig.suptitle(f"反例：同为{e['source_type']}“{e['category']}”，来自不同报告；仍非严格同工况")
        fig.tight_layout(rect=(0,0,1,.90));fig.savefig(dest/'B_counterexample.png',dpi=180);plt.close(fig)
    for path in output.glob('A_reports/*/final/*.three_chapter.pdf'):
        import fitz
        with fitz.open(path) as doc:
            for index,page in enumerate(doc):
                page.get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(dest/f'{path.name[:10]}.page{index+1}.png')


def documentation(output,diagnostics,auxiliary):
    screening=pd.read_csv(output/'329day_screening.csv')
    offline=read(output/'A_offline_statuses.json');generated=read(output/'A_generation_statuses.json')
    selection=read(output/'date_selection.frozen.json'); b=read(output/'B_geology_response_v2/summary.json')
    calls=read(output/'model_calls.json');checks=read(output/'A_quality_evaluation.json')
    usage={'calls':len(calls),'successful_API_responses':sum(c['status']=='success' for c in calls),
        'input_tokens':sum(c.get('input_tokens',0) for c in calls),'output_tokens':sum(c.get('output_tokens',0) for c in calls),
        'API_seconds_sum':sum(c.get('duration_seconds',0) for c in calls),'transport_retries':sum(c.get('transport_retries',0) for c in calls),
        'requested_models':sorted({c['model_requested'] for c in calls}),'returned_models':sorted({c.get('model_returned') for c in calls}),
        'planning_calls':0,'revision_calls':0,'cost_currency':'not_computed_no_verified_current_billing_rate',
        'remaining_call_budget_not_used_for_posthoc_prompt_tuning':6-len(calls)}
    write_json(output/'model_usage_summary.json',usage)
    write_csv(output/'model_usage.csv',[{**c,'experiment':'A_multidate_report',
                                       'group_field_note':'original_interface_label_not_A-D_baseline_group'} for c in calls])
    rows=[]
    for s in offline:
        reason=s.get('reason','')
        p=output/'A_reports'/s['date']/'plan.validation.json'
        if p.exists() and not read(p)['passed']:reason=';'.join(read(p)['errors'])
        rows.append({'date':s['date'],'offline_status':s['status'],'reason':reason,
            'stable_samples':s.get('stable_samples'),'stable_support_seconds':s.get('stable_support_seconds'),
            'windows':s.get('windows'),'analysis_units':s.get('segment_comparisons'),
            'generated_status':next((g['status'] for g in generated if g['date']==s['date']),'not_in_formal_generation_set')})
    write_csv(output/'A_all_date_results.csv',rows)
    table='\n'.join(f"|{r['date']}|{r['offline_status']}|{r['stable_samples']}|{r['windows']}|{r['analysis_units']}|{r['generated_status']}|" for r in rows)
    text_file(output/'CAPABILITY_AUDIT.md',f'''# 实际能力审计

本轮只新增 `experiments/{HERE.name}/`，现有核心代码、PLC缓存及SQLite未改写。完整源码快照和环境信息另附。

|能力|真实复用位置|本轮验证及限制|
|---|---|---|
|329天数据与质量|plc_mapping_repair_v1/replay/after|{len(screening)}天可读，326天允许空间匹配；70天无合格稳定样本|
|稳定样本与连续窗口|reporting_v2/response_features.py: response_samples, build_response_features|复用冻结标签及统计函数，不重新识别平台或阶段；全量11799窗口，空间样本完整支持11650窗口|
|趋势与参数共同变化|reporting_v2/response_features.py: response_facts；evidence_graph/response_analysis.py|按原首末四分之一中位数，保留精确行号与支持时长；非相关系数、非异常诊断|
|地质原文/时间/里程|geology_store/database.py；evidence_graph/catalog.py: document_gate|SQLite只读；当前实际7 TSP、49 HSP、214素描；原始时间角色继续保留|
|区段比较与原范围绑定|evidence_graph/response_analysis.py: segment_analysis, geology_for_segment|12候选均使用原函数，11/05里程异常拒绝；其余11天分析检查通过|
|代表性选择|response_analysis.py: time_window_analysis；three_chapter.py: build_pack|沿用既有选择，不针对4/26重排；3个现象是上限，不是每天必须存在|
|提纲与正文|three_chapter.py: offline_preview, requests；joint_reporting.py: run_writer|本轮用既有确定性现象提纲，每日仅调用一次正文生成，不评价LLM规划增益|
|规划核查|joint_reporting.py: plan_quality|5日期出现端点事实闭包不足；未修复核心、未换样本|
|核验/PDF|joint_reporting.py: draft_quality；scripts/run_three_chapter_report.py: render|5份真实PDF；原检查0/5通过，独立有限一致性5/5通过，两者不是事实准确率|

必要最小适配：无主要推进活动时，实验副本原有“间歇推进”概况缺乏支持，copy_guard仅改该句并保存前后差异。5/23及9/21使用，原包保留。故本轮这两日不冒充原始未适配方法的直接成功。

未发现本轮重跑329天PLC分段、重提PDF或修改数据库。已发现跨日期通用性缺陷，并非只缺人工标签。4/26仅做离线回归，未新增该日模型调用。
''')
    text_file(output/'A_RESULTS.md',f'''# 实验A：多日期验证

## 预先冻结的样本

种子20261028；正常候选池187天，按日期三等分，各抽2天；若该层至少2天有可用预报，先在该资料层内抽取。另加6个预声明开发/负面日期，共12天。各层第一个抽中日期加3个特殊日期组成正式6天：{', '.join(selection['formal_dates'])}。已知案例及新样本合用，所以这是跨日期功能验证，不是完全独立泛化测试。

|日期|离线状态|合格稳定记录|窗口|比较单元|正式生成状态|
|---|---|---:|---:|---:|---|
{table}

## 真实结果与分母

- 12候选中6天通过全部离线检查；5天图/分析通过但提纲失败；1天因异常里程被拒绝。
- 正式6天中3/30离线失败保留、不换日期。其余5天各调用一次，5次均返回真实JSON，生成5份2至3页PDF。
- 原质量检查通过0/5（正式全6日期口径0/6）。不能把它写成“真实事实准确率0%”。
- 5/5 PDF通过既有成品检查；5/5通过另行执行的有限日期/范围/最终文字检查。这同样不是语义正确率，也不能抵消质量失败。
- 未进行LLM修订、未调提示词重试；原始结果、失败状态与原核验全部保留。
- 当前请求模型deepseek-chat，API实际返回deepseek-flash；必须记录真实返回，不能描述为已锁定不可变的具体模型版本。

## 具体发现

1. 5个日期重复出现planning_relation_missing_endpoint_facts：没有可用相邻比较时，综合事实缺少部分PLCResponse端点支持，提纲仍引用其关系。这是现有证据闭包/编排的接口缺陷，不是PLC统计失败。
2. 所有3个无可比区段的已生成日期，被要求填写一个不存在的analysis_unit_id；这是空集合分支的检查器条件问题。
3. 两份正常报告的4个负百分比被写成“降低+绝对幅度”，原检查按有符号数比较造成误报；方向和幅度的单独复核见A_failure_diagnostics。
4. 6/20“原报告以DyK1016+476至+510为参照”的形式未被有限正则接受；5/23“实际观测日期未记录”又被日期正则标记，均须区分字面检查与语义。
5. 12/08“参考区段与当前区段相比，推进速度降低...”有比较主语含糊甚至方向误读风险，而原程序未报此项。保留原文，标为辅助审阅待核查，不伪装专家标签。
6. 无推进日期原概况仍套用“间歇推进”，本轮仅在输入副本加了预声明防护；冻结核心尚未修复。

正常日期12/08及6/20有真实趋势/负载组合和相邻区段分析；11/18有2245条合格稳定记录与3个代表窗口，但没有合格相邻比较。5/23有效覆盖仅520秒/86400≈0.60%，7m只能称已观测首末差；9/21零进尺、无合格稳定样本，二者只适合简化日报。不能凭2个正常生成例认定全329天稳定可用。

## 辅助阅读与人工评价

A_auxiliary_reading_NOT_HUMAN.json是Codex辅助阅读，不是独立工程专家确认。文字仍重复总体分布和代表窗口，3.1的证据与解释限制比旧机械罗列清楚，但工程分析价值和自然性尚待人工评价。A_human_review_blank.csv的人工栏全部留空。

没有构造旧/新生成优劣百分比：历史模型、输入及提示词条件不完全相同。4/26只作为开发回归，不作独立提升证据。
''')
    example=read(output/'B_geology_response_v2/similar_description_different_response_examples.json')[0]
    text_file(output/'B_RESULTS.md',f'''# 实验B：跨区段描述性探索

## 数量与真正的独立性

可靠PLC名义里程DyK1013+211至DyK1017+398，跨度4187m。空间质量合格且符合既有稳定推进筛选的原始记录{b['unique_PLC_records']}条，原每日窗口中{b['fully_supported_spatial_daily_windows']}个完全由这些记录支持。原始记录逐行只属于一个半开空间单元，末端全局端点属于最后一格。

|口径|TSP|HSP|素描|
|---|---:|---:|---:|
|与可靠PLC全程相交的原始正长度区段|47|203|105|
|具有该施工日前已可用资料及稳定样本|34|132|0|
|再满足至少30样本及300秒支持|34|131|0|

355是原始来源区段数量，不是355个独立真值；有时效样本的166区段来自44份报告。414个不重叠空间单元中256有样本、255满足基本支持、237同时有事前地质；相邻单元共享报告且机器/地质连续，不独立。确认的独立工程地质标签数为0，严格同工况区段数为0。

素描有资料不代表当前严格事前时效、正长度区间和稳定样本能相交。本轮零个合格素描匹配，不把点素描外推成区段，不把事后观察并入历史日报证据。被排除原文与原因仍保留。

## 描述性结果

按TSP/HSP分别统计原文完整性组合值，保留“较破碎-破碎”等范围及局部限定，未把每个采样点当观测。图中一格一值，类别原始文本、来源报告数、样本支持另存；所有图均未计算显著性。岩性、软硬、节理、风化、水和构造等已有字面属性在source_attribute_inventory及完整source_units保留；本轮没有把相近岩性组合强行归并为新的工程分类，没有对每个属性批量显著性检验。统计探索目前主要以完整性字面类别为示例，不宣称已经穷尽所有属性关系。

实际使用的44份原始PDF均存在且SHA-256与来源索引一致，逐文件记录见original_source_file_verification.csv；本轮未重新提取、也未逐句专家核实原始地质含义。

原始预报边界两侧满足时效/样本支持的比较共{b['boundary_pairs']}对，其中{b['boundary_pairs_passing_RPM_diagnostic']}对的可用转速中位数相差不超过15%。该15%仅为预声明诊断筛选，转速映射仍带provisional标记；不是安全限值、不是严格工况控制、不是证明真实地质界面。

反例：两份不同HSP均有“破碎”描述，DyK1014+299至+317的速度中位数21.68mm/min，DyK1015+198至+203为68.53mm/min，转速中位数相对差约7.78%。原始来源和真实样本在source_cell_links及attribute_response_rows中可查。它说明“破碎”文字标签不能唯一决定机器响应，不证明地质没有影响，也不证明差异来自操作。

同类文字的参数分布广泛重叠；不同类别也可能存在分布差异，但原始报告数不均、部分类别仅1份报告、跨期操作与位置误差未控制。因此本轮只确认数据组织及描述性差异，尚不能确认可重复的地质—机器统计规律。

## 停止进一步统计推断

没有可靠的操作给定值统一控制、严格掌子面位置配准、独立现场地质真值及来源分组的独立重复，故不计算p值、不拟合围岩分类模型、不声称因果。TSP与HSP分开呈现，不叠加为更多样本；B结果没有送入A事实包或日报。

v2为最终分析版本。保留B_geology_response旧v1供审计，但其边界对与窗口计数未充分约束时效/空间支持，不得用于论文结论。v2在文件写完后仅stdout NumPy整数打印报错，独立表格校验另行完成；未将该报错冒充整个实验未运行或悄悄删除失败记录。
''')
    text_file(output/'RESULTS.md',f'''# 本轮最终结论

## 实验A与B必须分开

A：已完成329天筛查、12候选离线验证、冻结6日中的5次真实模型调用及5份PDF。原质量判定0/5通过，包含明确的跨日期程序误报及未捕获的表达问题；不能宣称稳定批量正式交付。B：已完成跨区段描述性数据集和图表，但未获得可确认的地质影响规律。B的不足不等于A失败，A的PDF生成也不证明B规律。

## 回答八个问题

1. 能否换日期？能够读取并计算，已真实生成5日；但5个候选提纲闭包失败及核验分支缺陷表明尚不能宣称稳定跨日期正式生成。
2. 哪些有分析？12/08、6/20具备窗口及区段分析，11/18有时间窗口但不适合强做空间比较；5/23严重缺测和9/21零推进应简化。其余候选的离线分析虽在，提纲失败须保留。
3. 重复问题？无比较时的关系端点支持闭包、空analysis_units仍强制绑定ID、下降百分比符号、有限日期/相对描述正则误报；另发现比较句主语含糊的潜在漏检。
4. 可靠PLC特征？既有质量筛选下的速度/推力/扭矩分布、真实支持时长、连续窗口首末四分之一中位数趋势、多参数同/反向变化、CV及相邻区段描述。转速仅按既有provisional状态作诊断，不强算严格FPI/TPI/比能耗。
5. 地质区段多少？355原始来源范围、166有事前可用稳定样本，165满足基本支持；414不重叠空间单元中237有足量样本和时效地质。它们不是独立地质真值，确认独立标签仍为0。
6. 有统计联系吗？观察到描述性参数分布差异、边界两侧差异和同地质文字不同响应的反例；未确认跨区段可重复联系。
7. 主要限制？未知操作给定值/磨损/操作者等混杂、暂定转速映射、盾首与掌子面配准、同源报告与空间连续性、素描时效及位置相交不足、类别来源不均。
8. 哪些可写论文？可写真实实现、选择协议、失败分支、核验器误报/漏检和可追溯描述性案例；不能写真实事实准确率、方法显著优于基线、地质致因、独立泛化、LLM规划增益或全329天稳定正式成功。

## 调用与保护

实际新增{usage['calls']}次正文调用，输入{usage['input_tokens']}Token、输出{usage['output_tokens']}Token，总计{usage['input_tokens']+usage['output_tokens']}；0次重试、0次LLM规划/修订。请求deepseek-chat，返回deepseek-flash。未核实账单费率，未伪造费用。剩余1次额度未用来调参。

核验所列核心/配置/SQLite校验值不变，329个处理后PLC文件均与本轮扫描时的SHA-256一致；全项目自动测试335项通过，见full_regression_tests.log（另有302项指定范围检查日志）。所有核心文件的脏工作区是任务前已有，源码快照包含实际当前版本，不把未提交代码冒充某个干净commit。

## 应优先做什么

后续先针对本轮暴露的通用接口和检查器错误单独修复，并在预留新日期上验证；本轮不改核心、不反复调提示词。人工应审阅比较方向、地质语义及工程信息量，空白表已提供。大规模地质响应建模及追加Agent功能暂不优先。
''')
    text_file(output/'STATISTICAL_LIMITATIONS.md','''# 统计推断审查

本轮没有p值、显著性、准确率或故障标签，也没有人工结果。以下风险逐项保留：

1. 伪重复：20秒记录及共享报告的相邻格不是独立地质观测。
2. 自相关：时间连续、空间相邻，常规独立样本检验不适用。
3. 选择偏差：稳定阶段质量筛选会排除低速/启动等状态，结论只适用于所筛样本。
4. 来源不均：类别可能仅来自1份原始报告。
5. 混杂：控制给定值、磨损、操作习惯和施工时期未充分控制。
6. 标签误差：预报不是实际揭示，字面类别不能等同真值。
7. 空间误差：整数盾首里程且掌子面偏移未核实。
8. 时间泄漏：每条source_cell_link逐日核对可用时间；未知日期不准入。
9. 多重比较：穷举对仅用于选反例，不计算推断性显著结果。
10. 结果选择：正式日期事前冻结，失败不替换；已知案例不是独立测试集。
11. 构念混淆：内部检查通过、PDF完整、语义正确、工程价值分别记录。

可报告分布和反例，暂不支持严格同工况联系或地质因果；没有关系得到证实不等于证实无关系。
''')
    text_file(output/'REPRODUCE.md',f'''# 复现与文件导航

先读RESULTS.md，再看A_RESULTS.md/B_RESULTS.md及对应CSV/JSON。正式A PDF均为质量失败候选，不是可直接发布的工程成品；原状态保留。

独立实验源码在source_code/experiments/{HERE.name}，实际既有实现快照在source_code/tbm_process及scripts。数据库和大型原始PLC不随包分发，来源路径/原始文件名/页码/行号/哈希保留；qualified_spatial_samples及全部每日特征缓存随包提供供离线核对。

本机解释器：{sys.executable}。从实际仓库根目录运行：

```bash
python -m pytest -q tests experiments/{HERE.name}/test_experiment.py
python -m experiments.{HERE.name}.run evaluate --output <已运行的输出目录>
```

完整重跑需将common.py中数据位置配置为拥有的真实缓存/SQLite；scan、B、offline、generate依次运行在全新输出目录。generate会访问付费API，不属于默认复核命令；遇已有ledger主动拒绝重跑。本轮预算上限6，已用5，不无限重试。B旧v1保留但v2才是主结果。若仅重做图表可调用delivery.figures；deliver最后创建ZIP且禁止覆盖已有ZIP。

模型输出不保证逐字复现；保留实际请求、返回模型、采样参数、响应及Token。本轮计划为确定性现象规划，不能推断LLM规划有效性；没有新的A-D对照。

本轮新增：实验协议、扫描/描述性分析、只读复核、绘图/打包、7个测试；不改现有tbm_process、scripts、config或SQLite。CLI stdout序列化和B时效/独立性修复只在实验代码内，旧产物未覆盖。
''')


def snapshot(output):
    target=output/'source_code';target.mkdir(exist_ok=False)
    shutil.copytree(PROJECT/'tbm_process',target/'tbm_process',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(HERE,target/'experiments'/HERE.name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for sub in ['configs','tests','scripts']:
        shutil.copytree(PROJECT/sub,target/sub,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['config.json','pyproject.toml','requirements.txt','requirements-geology-store.txt','pytest.ini']:
        if (PROJECT/name).is_file():shutil.copy2(PROJECT/name,target/name)
    environment={'python':sys.version,'executable':sys.executable,
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip(),
        'git_branch':subprocess.check_output(['git','branch','--show-current'],cwd=PROJECT,text=True).strip(),
        'git_status':subprocess.check_output(['git','status','--short'],cwd=PROJECT,text=True),
        'pip_freeze':subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True)}
    write_json(output/'environment.json',environment)


def package(output):
    from tbm_process.final_reporting.common import load_deepseek_api_key
    key=load_deepseek_api_key(Path.home()/'Desktop/tbm-bitemporal-twin/.env')
    violations=[];scanned=0
    for p in output.rglob('*'):
        if not p.is_file() or p.suffix.lower() in {'.gz','.png','.pdf','.zip','.pyc'}:continue
        data=p.read_bytes();scanned+=1
        if key and key.encode() in data:violations.append(str(p.relative_to(output)))
        if re.search(rb'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----',data):violations.append(str(p.relative_to(output)))
        # Literal long access-key candidates are not the regex patterns naming them.
        for token in re.findall(rb'\bsk-[A-Za-z0-9]{24,}\b',data):
            if b'example' not in token.lower() and not re.fullmatch(rb'sk-0+',token):violations.append(str(p.relative_to(output)))
    write_json(output/'SECURITY_SCAN.json',{'passed':not violations,'scanned_text_files':scanned,
        'violations_file_paths_only':sorted(set(violations)),
        'scope':'configured_key_long_sk_tokens_private_keys; no_env_or_headers_copied; not_a_formal_secret_scanner'})
    if violations:raise ValueError('secret_candidate_detected_do_not_package')
    manifest=[]
    for p in sorted(output.rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts or p.suffix=='.pyc' or p.name in {'MANIFEST.csv','ZIP_VERIFICATION.json'}:continue
        rel=str(p.relative_to(output));day=re.search(r'20\d{2}-\d{2}-\d{2}',rel)
        manifest.append({'path':rel,'bytes':p.stat().st_size,'sha256':digest(p),'date':day[0] if day else '',
                         'category':rel.split('/')[0] if '/' in rel else 'audit_or_summary'})
    write_csv(output/'MANIFEST.csv',manifest)
    archive=output.parent/(output.name+'_交付.zip')
    if archive.exists():raise ValueError('archive_already_exists_no_overwrite')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for r in manifest:z.write(output/r['path'],arcname=output.name+'/'+r['path'])
        z.write(output/'MANIFEST.csv',arcname=output.name+'/MANIFEST.csv')
    failures=[]
    with zipfile.ZipFile(archive) as z:
        if z.testzip():failures.append('CRC_failure')
        for r in manifest:
            if hashlib.sha256(z.read(output.name+'/'+r['path'])).hexdigest()!=r['sha256']:failures.append(r['path'])
        count=len(z.infolist())
    write_json(output/'ZIP_VERIFICATION.json',{'passed':not failures,'sha256':digest(archive),'archive':str(archive),
        'bytes':archive.stat().st_size,'files':count,'hash_verified_files':len(manifest),'failures':failures,
        'verification_record_external_to_archive_to_avoid_self_reference':True})
    print(json.dumps(read(output/'ZIP_VERIFICATION.json'),ensure_ascii=False),flush=True)


def deliver(output,protocol):
    diagnostics,auxiliary=audit_reports(output)
    audit_spatial(output)
    figures(output)
    documentation(output,diagnostics,auxiliary)
    snapshot(output)
    package(output)
