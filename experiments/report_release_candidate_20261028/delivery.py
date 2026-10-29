"""Package a candid candidate release, not a certificate of engineering truth."""
from __future__ import annotations

from copy import deepcopy
import difflib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import fitz
import pandas as pd

from .run import OUT,OLD,PREVIOUS,ROOT,FILES,DB,PROTECTED,read
from tbm_process.reporting_v2.facts import digest,write_json


def text(path,value):path.write_text(value.strip()+'\n',encoding='utf-8')


def current_cases():
    rows=[]
    for day,model,report in [('2023-12-20','accepted_saved_patch','report_final'),('2024-01-24','model','report')]:
        folder=OUT/'selection_preflight'/day
        rows.append((day,folder,read(folder/'evidence_pack.json'),read(folder/model/'content_plan.json'),
                     read(folder/model/'narrative.json'),folder/report/(day+'.three_chapter.pdf')))
    return rows


def pressure(cases):
    from tbm_process.evidence_graph.joint_reporting import draft_quality
    rows=[]
    def check(name,base,change=None,expected='NONBLOCKING',required=None):
        day,folder,p,plan,d,pdf=base;p,d=deepcopy(p),deepcopy(d)
        if change:change(p,d)
        q=draft_quality(d,plan,p)
        good=(not q['errors']) if expected=='NONBLOCKING' else bool(q['errors'])
        if required:good=good and any(required in e for e in q['errors'])
        record={'case':name,'expected':expected,'actual':q['status'],'met_expectation':good,
                'required_finding':required,'errors':q['errors'],'not_engineering_truth_label':True}
        rows.append(record);target=OUT/'pressure_tests'/name;target.mkdir(parents=True,exist_ok=True)
        for file,value in [('draft',d),('quality',q)]:write_json(target/(file+'.json'),value)
    def replace(d,a,b):
        changed=0
        for ch in d['chapters']:
            for c in ch['claims']:
                if a in c['text']:c['text']=c['text'].replace(a,b);changed+=1
        if not changed:raise ValueError('probe_literal_not_found:'+a)
    a,b=cases
    check('actual_repaired_comparison_report',a)
    check('actual_sparse_evidence_report',b)
    check('equivalent_compound_duration',b,lambda p,d:replace(d,'2小时31分10秒','151分10秒'))
    check('wrong_speed',b,lambda p,d:replace(d,'41.16mm/min','141.16mm/min'),'FAIL','unsupported_value_unit_pair')
    check('wrong_sample_count',b,lambda p,d:replace(d,'562条','1562条'),'FAIL','unsupported_value_unit_pair')
    check('wrong_duration',b,lambda p,d:replace(d,'2小时31分10秒','2小时31分20秒'),'FAIL','unsupported_compound_duration')
    check('wrong_percentage_sign',a,lambda p,d:replace(d,'提高28.20%','提高-28.20%'),'FAIL','percentage')
    check('invalid_comparison_id',a,lambda p,d:d['chapters'][2]['claims'][0].update(analysis_unit_id='invented'),'FAIL','invalid_analysis_unit_id')
    def assignment(p,d):d['chapters'][2]['claims'][0]['geology_assignments'][0]['plc_range_m']=[1,2]
    check('wrong_geology_assignment',a,assignment,'FAIL','geology_fact_assigned_to_wrong')
    check('daily_statistics_as_local',b,lambda p,d:d['chapters'][2]['claims'][0].update(
        text='该地质区段推进速度中位数41.16mm/min。'),'FAIL','daily_statistics_presented_as_segment')
    check('invented_fault_in_compound_sentence',a,lambda p,d:d['chapters'][2]['claims'][0].update(
        text=d['chapters'][2]['claims'][0]['text']+'当前存在F999断层。'),'FAIL','unsupported_fault_id')
    check('invented_cause_in_compound_sentence',b,lambda p,d:d['chapters'][1]['claims'][1].update(
        text=d['chapters'][1]['claims'][1]['text']+'岩体破碎导致设备故障。'),'FAIL','unsupported_causal')
    check('historical_record_as_today',b,lambda p,d:d['chapters'][2]['claims'][1].update(
        text='当日实际揭露IV级围岩。'),'FAIL','historical_record_written_as_current')
    write_json(OUT/'pressure_results.json',rows)
    pd.DataFrame(rows).to_csv(OUT/'pressure_results.csv',index=False,encoding='utf-8-sig')
    if not all(r['met_expectation'] for r in rows):raise ValueError('pressure_regression_failed')
    return rows


def evidence(cases):
    sources={};final=[]
    def walk(value):
        if isinstance(value,dict):
            if value.get('path') and value.get('sha256'):sources[value['path']]=value
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    for day,folder,pack,plan,draft,pdf in cases:
        used={fid for ch in draft['chapters'] for c in ch['claims'] for fid in c['fact_ids']}
        for f in pack['facts']:
            if f['fact_id'] in used:walk(f.get('source',{}))
        with fitz.open(pdf) as doc:
            full=''.join(p.get_text() for p in doc);norm=lambda s:re.sub(r'\s+','',s)
            checks=[{'text_sha256':__import__('hashlib').sha256(c['text'].encode()).hexdigest(),
                     'present_in_PDF':norm(c['text']) in norm(full)} for ch in draft['chapters'] for c in ch['claims']]
            preview=OUT/'pdf_previews'/day;preview.mkdir(parents=True,exist_ok=True)
            for i,page in enumerate(doc):page.get_pixmap(matrix=fitz.Matrix(1,1)).save(preview/f'page{i+1}.png')
            pages=len(doc)
        final.append({'date':day,'pdf':str(pdf),'sha256':digest(pdf),'pages':pages,'claim_count':len(checks),
                      'all_final_claims_present_in_PDF':all(x['present_in_PDF'] for x in checks),'claims':checks})
        cache=OUT/'sample_data'/day;cache.mkdir(parents=True,exist_ok=True)
        package=read(folder/'report_facts.json');path=Path(package['source']['samples'])
        shutil.copy2(path,cache/path.name)
        write_json(cache/'sample_provenance.json',{'original_path':str(path),'sha256':digest(path),
                    'equals_fact_source_hash':digest(path)==package['source']['samples_sha256']})
    index=[];target=OUT/'original_evidence';target.mkdir(exist_ok=True)
    for i,(raw,source) in enumerate(sorted(sources.items()),1):
        path=Path(raw);present=path.exists();matches=present and digest(path)==source['sha256']
        copy=None
        if matches:
            copy=target/(f'{i:03d}_'+path.name);shutil.copy2(path,copy)
        index.append({'original_path':raw,'expected_sha256':source['sha256'],'exists':present,'hash_matches':matches,
                      'package_copy':str(copy.relative_to(OUT)) if copy else None})
    write_json(OUT/'original_evidence_index.json',index);write_json(OUT/'PDF_TEXT_CONSISTENCY.json',final)
    if not all(x['all_final_claims_present_in_PDF'] for x in final):raise ValueError('PDF_body_differs')
    return final,index


def snapshot():
    target=OUT/'source_code';target.mkdir(exist_ok=True)
    for folder in ('tbm_process','scripts','tests','configs'):
        shutil.copytree(ROOT/folder,target/folder,dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('__pycache__','*.pyc','.env','*.env'))
    for name in ('requirements.txt','requirements-geology-store.txt','pyproject.toml','pytest.ini','config.json'):
        if (ROOT/name).exists():shutil.copy2(ROOT/name,target/name)
    for folder in ('report_release_candidate_20261028','report_reliability_freeze_20261028','multidate_response_validation_20261028'):
        shutil.copytree(ROOT/'experiments'/folder,target/'experiments'/folder,dirs_exist_ok=True,
                       ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    text(target/'experiments/__init__.py','')
    changes=[]
    for f in FILES:
        before=OUT/'before_source'/f;after=ROOT/f
        changes+=list(difflib.unified_diff(before.read_text().splitlines(True),after.read_text().splitlines(True),
                                         fromfile='before/'+f,tofile='after/'+f))
    text(OUT/'core_changes.patch',''.join(changes))
    environment=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True)
    text(OUT/'environment.txt',environment.stdout)
    git=subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True)
    text(OUT/'git_status.txt',git.stdout)


def deliver():
    cases=current_cases();probes=pressure(cases);pdfs,sources=evidence(cases)
    smoke=subprocess.run([sys.executable,str(ROOT/'scripts/run_three_chapter_report.py'),'prepare',
        '--output',str(OUT/'cli_input_smoke'),'--dates','2024-01-24'],cwd=ROOT,capture_output=True,text=True)
    text(OUT/'cli_input_smoke.log',smoke.stdout+smoke.stderr)
    smoke_summary=read(OUT/'cli_input_smoke/offline_summary.json')
    write_json(OUT/'cli_input_smoke.result.json',{'exit_code':smoke.returncode,'results':smoke_summary,
        'no_API_called':True,'preview_is_not_a_validated_LLM_report':True})
    if smoke.returncode or not all(r.get('offline_program_checks') for r in smoke_summary):raise ValueError('CLI_input_preflight_failed')
    protection={}
    baseline=read(OUT/'protected.before.json')
    for f in PROTECTED:
        p=ROOT/f;protection[str(p)]=digest(p)==baseline[str(p)]
    protection['database_identical_to_preceding_audit']=digest(DB)==read(PREVIOUS/'protected.before.json')[str(DB)]
    history=read(OUT/'previous_results.before.json')
    altered=[p for p,h in history.items() if not Path(p).exists() or digest(Path(p))!=h]
    write_json(OUT/'PROTECTION.final.json',{'protected_unchanged':protection,'altered_previous_outputs':altered,
        'original_database_temporarily_missing_then_restored':True})
    if not all(protection.values()) or altered:raise ValueError('protected_or_history_changed')
    calls=read(OUT/'model_calls.json');initial=read(OUT/'generation_results.json')
    replay=read(OUT/'development_replay.json');patched=read(OUT/'saved_patch_final_result.json')
    usage={'calls':len(calls),'input_tokens':sum(c.get('input_tokens',0) for c in calls),
        'output_tokens':sum(c.get('output_tokens',0) for c in calls),
        'planning_calls':sum(c['stage']=='plan' for c in calls),'generation_calls':sum(c['stage']=='generation' for c in calls),
        'revision_calls':sum(c['stage']=='local_revision' for c in calls),
        'seconds':sum(c.get('duration_seconds',0) for c in calls),
        'requested_models':sorted({c['model_requested'] for c in calls}),
        'returned_models':sorted({c.get('model_returned','unknown') for c in calls}),
        'currency_cost':'not_computed_without_verified_billing_rate'}
    write_json(OUT/'MODEL_USAGE.json',usage)
    pd.DataFrame(calls).to_csv(OUT/'model_calls.csv',index=False,encoding='utf-8-sig')
    human=[]
    for day,folder,pack,plan,draft,pdf in cases:
        for ch in draft['chapters']:
            for c in ch['claims']:
                human.append({'date':day,'chapter':ch['chapter_id'],'paragraph_id':c['paragraph_id'],'text':c['text'],
                    'fact_ids':'|'.join(c['fact_ids']),'human_factual_support':'','human_coverage':'','human_readability':'','reviewer':'','notes':''})
    pd.DataFrame(human).to_csv(OUT/'HUMAN_REVIEW_BLANK.csv',index=False,encoding='utf-8-sig')
    snapshot()
    current={str(f.relative_to(ROOT)):digest(f) for f in [*[ROOT/x for x in FILES],ROOT/'scripts/run_three_chapter_report.py']}
    write_json(OUT/'CANDIDATE_FREEZE.json',{'state':'research_candidate_code_frozen_not_engineering_release',
        'code_sha256':current,'initial_fresh_generation_nonblocking':sum(r.get('program_checks_passed',False) for r in initial),
        'initial_fresh_generation_total':len(initial),'post_development_replay_nonblocking':2,
        'human_acceptance':'pending','no_claim_of_planner_superiority':True,'protected_data_and_PLC_algorithms_unchanged':True})
    text(OUT/'RESULTS.md',f'''# 候选版收尾结果

## 核心结论

当前代码冻结为研究候选版，不是无人审查的正式工程发布。两份最终样例均通过当前程序一致性检查，状态为REVIEW，工程语义人工评价尚未完成。

**不能把首轮结果写成2/2独立成功。** 预先锁定2023-12-20、2024-01-24，首次生成1/2非阻断；12/20因正文出现内部DOC编号失败，首次修订未改变文本。后续只做展示/保留检查接口修复，新增一次局部修订，模型实际删除编号；保存的修订又经离线检查后接受。初始结果、两次API修订、检查器修复及失败PDF全部保留。日期未替换，工程事实和规划未重选。

## 完成内容

- 15个开发日期回放：14个图/分析/提纲通过，2023-11-05的真实异常里程继续拒绝；这不是14份日报成功率。
- 合成事实声明显式区分全天背景、单区段、区段比较和无可用响应。数值仅按声明范围核查，避免用其他区段或全天值为局部错误兜底。
- 复合时长按总秒数核查；“历史区段位于当日范围后方”与“历史记录就是当日揭露”分别处理。没有方向确认时保留REVIEW。
- 反向原文里程在报告输入视图隔离，同一来源整份暂缓自动使用；原JSON、SQLite及原图不改写，隔离不代表已经证实原资料错误。
- 真实LLM Planner对候选窗口逐一给出采用/省略及理由，选中窗口经检查后才供生成及图表使用。两天均选择全部3个候选，**未证明相对规则规划有增益**。
- 修订只允许目标段落，其他段落不变，支持ID和正确工程数值保留；内部编号的数字不属于工程数值。此次接受修订改变1段，未重生成全篇。
- {len(probes)}项真实输出正向/错误注入回归全部满足预设判断；它们不是工程正确率、故障检出率或人工金标准。
- 完整测试见tests.final.log；入口实际离线prepare通过输入图、分析、计划及成品检查。确定性拼接预览的正文检查失败单独保留，不把预览当LLM成品，也不拿它替代输入分析校验。
- 受保护算法、配置、数据库及前两轮实验文件哈希未改变。

## 真实API量

{usage['calls']}次：规划{usage['planning_calls']}、正文{usage['generation_calls']}、局部修订{usage['revision_calls']}；输入{usage['input_tokens']} Token，输出{usage['output_tokens']} Token。请求{','.join(usage['requested_models'])}，返回{','.join(usage['returned_models'])}。没有传输自动重试，未估算未核实价格。请求量偏大，属于当前效率限制，未为降低Token改动方法。

## 仍需如实面对

1. LLM可以规划，但两例都选全部候选，不能据此声称内容选择更优；需要冻结条件下的规则/LLM对照与人工评价。
2. 语言仍有重复参数、集中罗列和防御性表述；不以无限重写追求零文字问题。
3. 数字和绑定通过不等于每个复合句语义均得到核实，未知语义仍REVIEW。模型输出也可能漏写JSON之外的含义。
4. 可用转速仍带provisional标记，控制给定值和盾首—掌子面偏移尚未充分确认；无单日地质因果或可重复规律结论。
5. 疑点素描仍需人核实原始里程。数据库未被自动修复，报告输入只采取保守隔离。

## 阅读顺序

先看最终报告两份、METHOD_ACTUAL.md、HUMAN_REVIEW_BLANK.csv；需要审计时再看原始模型请求/回复、content_plan、quality、retention和PDF_TEXT_CONSISTENCY。MANIFEST.csv列出包内文件及SHA-256。
''')
    text(OUT/'METHOD_ACTUAL.md',METHOD)
    text(OUT/'NEXT_FOR_PAPER.md',NEXT)
    text(OUT/'REPRODUCE.md',f'''# 复现与边界

当前实际仓库：{ROOT}。桌面tbm-report为空工作目录，不是本轮源码仓库。

离线测试（不会读密钥或调用API）：
```sh
python -m pytest -q
```
入口的离线事实准备（必须使用全新输出目录）：
```sh
python scripts/run_three_chapter_report.py prepare --output /new/output --dates 2024-01-24 --database /local/geology.sqlite --analysis-dir /local/frozen/analysis
```
旧脚本llm阶段包含历史A/B实验分支，不是本轮单方法的公平性对照；不要用它的组名推导本轮结果。

本轮真实执行脚本在experiments/report_release_candidate_20261028，run.py保存初始化、回放、抽样、生成、定向修复和交付阶段；已有目录/调用账本会阻止隐式覆盖和重跑。不要把generate当离线命令。既有API请求和响应可直接检查，无需再次付费。

代码快照包含依赖文件及实际pip环境。sample_data含两天真实已处理PLC样本；原始PLC未打包，未重新分段。original_evidence保存本轮采用来源的实际副本，索引保留原路径和哈希。完整数据库仍在本机原路径，未把全部源数据塞入包。历史15日期回放未附全量PLC，因此包外重跑完整15天仍需原缓存。

脱离本机重放时，应显式映射原绝对数据路径，不要更改工程事实数值。包提供现成输入、提纲、原始回复、质量记录和PDF，可进行无需API的外部审阅。没有人工标签、盲评密钥或凭据。
''')
    text(OUT/'START_HERE.md','''# 只认这个候选版

本目录是2026-10-28的研究候选交付，不覆盖过去版本。

1. RESULTS.md：真实完成情况及失败边界。
2. METHOD_ACTUAL.md：实际方法与代码对应。
3. FINAL_REPORTS/：两份最终研究审核稿；非独立成功率证明。
4. HUMAN_REVIEW_BLANK.csv：需要你或专家实际填写，当前为空。
5. NEXT_FOR_PAPER.md：冻结后只做必要论文评价，不继续堆功能。

初始失败与接口修复仍在selection_preflight中，不能拿修复后的开发回放覆盖初次验收分母。
''')
    report_folder=OUT/'FINAL_REPORTS';report_folder.mkdir(exist_ok=True)
    for item in pdfs:shutil.copy2(item['pdf'],report_folder/Path(item['pdf']).name)
    from experiments.multidate_response_validation_20261028.delivery import package
    package(OUT)


METHOD='''# 实际方法说明：三章日报研究候选版

## 1. 双线输入与统一事实

PLC先经既有逐行映射修复、清洗、状态/平台/阶段识别，读取缓存而不重新计算阶段。reporting_v2/facts.py:build_facts组织当天概况、平台、时间、进尺、质量及来源；response_features.py复用合格稳定且推进的记录，计算分布、连续窗口、趋势等。原始字段单位来自既有配置和用户确认，未等同独立厂家校验。

地质SQLite提供按历史日期准入的TSP/HSP/素描原文。catalog.py:build_catalog_graph、document_gate核查文件/字段来源及时间；日期精度资料严格早于施工日才准入；原文预测、历史观察、推测保持性质，不把资料可用日期当观测日期。没有重提PDF。

两路进入three_chapter.py:build_pack，完整facts保存PLC和地质；writer_fact_ids是报告候选摘要，不是全部原始采样。统一输入的摘要可通过graph_support_ids回到原始事实和PLCResponse。两天的真实plan.request和generation.request证明Planner/Generator均消费两路事实，而不是只给第三章位置背景。

## 2. 程序负责什么分析

response_analysis.py使用现有稳定连续窗口，首末各floor(n/4)条记录中位数描述变化，n少时至少1条。支持时长来自真实sample_dt_seconds累计，不把记录数乘固定20秒，也不把首末跨度当累计推进时间。

代表性候选至少12样本、满足既有支持时长、确切行号复核及趋势条件；最多3例，优先覆盖活动与方向模式，不按变化百分比最大排序。可读性选择使用扭矩CV≤0.25，这只是展示选择，不是设备安全/异常阈值。

相邻不重叠名义里程区段用既有样本/时长和转速等可比诊断。变化为(current/reference−1)×100，规则归纳同向/反向负载组合及变化幅度。示例小速度变化描述阈值5%属于预先说明的文字筛选，不是显著性。严格给定值控制与位置偏移未确认，故只作描述性对照。

地质以正长度交集匹配，各PLC区段保留各自原始地质区段与参考/当前角色。端点接触不把前段F36带入后段；一份地质资料被两段引用仍是同一来源。相对“较前段”只指原预报文件的前一段。report_graph_view仅在报告视图隔离反向原文里程，不猜正确端点，不更改源库。

## 3. 图的作用

GeologyFact→ConstructionSegment←PLCResponse及EngineeringComparison连接来源、名义范围和计算。关系按确定性空间/比较条件建立并带端点，Evidence Pack只传合法摘要及支持闭包。图提供组织和核查所需的范围、端点、来源，不证明地质导致机器变化，也不是经过训练的地质因果模型。

## 4. 规划是规则与LLM联合

规则确定三章、六个必要主题、合格事实、前方覆盖/风险必选信息、最多3个既有窗口候选；这些不是LLM发现。

LLM真正返回结构化段落计划：paragraph_id、section、intent、purpose、phenomenon_ids、fact_ids、relation_ids，以及window_decisions。模型逐个选择/省略候选窗口并给理由，可决定段落组织和表达重点；已选窗口只安排在2.1或2.2一次。程序检查合法ID、章节、全部候选去向、必要覆盖及关系端点。apply_window_choices把未选窗口移出实际写作和重点图表输入，完整原包另存。

这仍是受限规划：六个主题和大部分摘要必选，模型没有无限事实选择自由。此次两天都选3/3，不构成LLM规划优于固定规则的证据，也没有复现任何论文的完整规划算法。

## 5. 受约束自然语言生成

DeepSeek按事实与验证后的提纲返回三章claims JSON，每个自然段带支持ID、关系ID与窗口ID。3.1另外声明statistic_scope：全天背景、区段比较、单区段或缺少合格响应；比较必须有有效analysis_unit_id及逐地质事实的plc_range_m归属。全天背景可以在缺乏局部比较时使用，但必须明确是全天，不能称地质区段实测值。

全部三章自然语言在本轮均由真实模型产生；统计、图表、分析表和HTML/PDF由Python生成。Generator不计算参数，不修改里程边界，不诊断故障或给地质因果。模型请求、版本别名实际返回、输入输出Token、耗时均保存。

## 6. 核验不等于工程真值

joint_reporting.py:draft_quality、three_chapter.py:check_three_draft和report_reliability.py核查结构、计划归属、必要信息锚点、数值/单位、复合时长、带方向百分比、比较角色、原范围、日期属性、来源绑定、局部风险、未支持的因果/诊断及内部ID。

旧通用数字检查不再与三章的逐claim类型化检查重复计数；原发现仍记录legacy_unscoped_numeric_findings。时间完整表达按总秒比较；“截至报告日”只允许精确匹配报告日期，其他日期仍核查。原始观测日期与资料可用日期继续由性质规则约束。

FAIL表示发现确定性冲突或不合法输入/输出；没有阻断项仍为REVIEW，因为未核查完所有语义，也没有独立人工金标准。程序通过不能称实际工程事实准确率；有限正则未涵盖所有中文改写与复合语义，不能宣称已解决全部幻觉。

## 7. 局部修订与保留

验证器定位chapter_index/claim_index，将反馈、原文与证据送模型，只允许修改目标。候选修改需通过retention_check及再次完整draft_quality：未目标段落逐对象不变、段数不增删、支持ID保留、先前受支持工程数值保留。内部DOC等编号在正文禁止，其数字不当作工程信息保留；结构化来源ID仍在JSON与追溯中。

该保留检查不是完整语义等价性证明。此次真实例中只删除一段的内部编号，日期与资料性质保留，其他5段不变。初次修订无效，额外开发修订/离线接口修复后才接受，不能冒充原始冻结条件下的独立成功。

## 8. 成品与研究边界

render_three_figures依真实逐点数据绘图，重点窗口与LLM已选窗口一致；地质空间图用区段汇总而非伪造高精度曲线。正文以HTML转PDF，本轮逐段检查模型最终文本实际进入PDF；输入图与分析、正文质量、成品检查分别记录，不相互替代。

当前仅完成候选方法的运行及有限程序验证。未获得独立专家事实标注、PLC/地质人工识别准确率、跨TBM外部验证或LLM规划优势。单日空间对应不是地质作用规律；不能将此方法写成故障预测、安全预警或围岩自动判别系统。
'''

NEXT='''# 接下来只做论文所需的评价

## 立即停止继续堆功能

候选代码及样例已经留档。正文有普通重复、免责声明等问题记入人工表，不再用无限调用追求完美。暂不加入Agent、额外异常模型、更多图节点或地质因果分类。

## 三项必要研究工作

1. 完成真实人工标注：优先核查本包两份报告的施工数值、原始地质范围与关键句，填写空白表。再按已经准备的PLC/PDF样本与盲评协议开展，不预填答案。
2. 冻结条件做公平的小规模对照：相同事实池下比较直接生成、规则规划、LLM规划以及核验/局部修正。开发日期排除出独立测试；保留失败；不要拿本轮1/2后修成2份的结果当独立提升。
3. 对修订有真实错误的例子单独评估：错误是否减少、信息是否丢失、是否引入新错。自然生成若C/D文本没变化，就不能声称修订提升；错误注入仅证明指定敏感性。

## 现在可以写什么

方法章节可据METHOD_ACTUAL.md及源码写双线处理、统一事实/证据、确定性现象摘要、受限LLM规划、受约束生成、分项一致性核验和局部修订。实验章节可以写真实数据规模、流程、失败类别、调用量及有限回归结果。

暂时不能写真实事实准确率、显著优于基线、已证实地质致因、跨项目泛化。不要把内部测试或来源追溯通过率换个名字当事实正确率。
'''
