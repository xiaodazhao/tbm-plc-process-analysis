"""Evidence-preserving acceptance report; never promotes a failed run to PASS."""
from __future__ import annotations

from collections import Counter
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

from .run import DB,MODIFIED,OLD,OUT,PROTECTED,ROOT,csv,protection,read
from .validation import code_hashes
from tbm_process.evidence_graph.joint_reporting import _numeric_unit_pairs,draft_quality,writer_facts
from tbm_process.final_reporting.integrated_llm import _numbers,_number_is_supported
from tbm_process.reporting_v2.facts import digest,write_json


def markdown(name,text):(OUT/name).write_text(text.strip()+'\n',encoding='utf-8')


def final_historical_replay():
    rows=[]
    checks=[]
    for old in read(OLD/'A_generation_statuses.json'):
        day=old['date'];source=OLD/'A_reports'/day/'generated/draft.before.json'
        if not source.exists():continue
        d=read(source);folder=OUT/'history_replay_v2'/day
        oldcheck=draft_quality(d,read(OLD/'A_reports'/day/'content_plan.json'),read(OLD/'A_reports'/day/'evidence_pack.json'))
        rebuilt=draft_quality(d,read(folder/'content_plan.json'),read(folder/'evidence_pack.json'))
        write_json(folder/'quality.same_old_input.final.json',oldcheck)
        write_json(folder/'quality.rebuilt_input.final.json',rebuilt)
        rows.append({'date':day,'raw_model_sha256':digest(source),'old_errors':old['quality_errors'],
            'same_old_input_status':oldcheck['status'],'same_old_input_errors':oldcheck['errors'],
            'same_old_input_reviews':oldcheck['review_items'],'rebuilt_input_status':rebuilt['status'],
            'rebuilt_input_errors':rebuilt['errors'],
            'not_new_model_output':True,'existing_raw_report_not_rewritten':True})
        for error in old['quality_errors']:
            remains=error in oldcheck['errors']
            checks.append({'date':day,'old_check':error,'old_state':'FAIL',
                'new_same_input_check_state':'FAIL' if remains else 'old_flag_not_reproduced',
                'new_report_state':oldcheck['status'],'new_report_blockers':oldcheck['errors'],
                'rebuilt_input_blockers':rebuilt['errors'],
                'interpretation':('原绑定事实未提供该时长，补齐事实后再检查；不是放宽数值。' if error=='unsupported_value_unit_pair:0.0秒' else
                                  '原空ID/方向/否定表达误报已消除；新语义阻断与原旗标分开保存。'),
                'independent_human_correctness_label':''})
    write_json(OUT/'HISTORICAL_FINAL_COMPARISON.json',rows);csv(OUT/'HISTORICAL_FINAL_COMPARISON.csv',rows)
    write_json(OUT/'RULE_REPLAY_COMPARISON.json',checks);csv(OUT/'RULE_REPLAY_COMPARISON.csv',checks)
    return rows


def independent_diagnostics():
    rows=[];checks=[];human=[];source_problems=[]
    for status in read(OUT/'independent_results.json'):
        day=status['date'];folder=OUT/'independent_validation'/day
        draft=read(folder/'report/narrative.json');pack=read(folder/'evidence_pack.json')
        plan=read(folder/'content_plan.json');quality=read(folder/'report/quality.json')
        allfacts=writer_facts(pack);table={f['fact_id']:f for f in allfacts};allpairs=_numeric_unit_pairs(allfacts)
        for item in quality['claim_checks']:
            for error in item['errors']:
                support=[table[i] for i in item['fact_ids'] if i in table];pairs=_numeric_unit_pairs(support)
                diagnostic='待人工核查';detail={}
                if error.startswith('unsupported_value_unit_pair:'):
                    m=re.fullmatch(r'unsupported_value_unit_pair:([-+]?\d+(?:\.\d+)?)(.+)',error)
                    value=float(m[1]);unit=m[2]
                    elsewhere=any(u==unit and abs(v-value)<=.0051 for v,u in allpairs)
                    if elsewhere:
                        diagnostic='全日其他事实有支持，但本段绑定事实未包含该数值/单位；不能直接称数值算错'
                    if unit=='秒':
                        durations=[]
                        for dm in re.finditer(r'(?:(\d+)小时)?(\d+)分(\d+)秒',item['text']):
                            total=3600*int(dm[1] or 0)+60*int(dm[2])+int(dm[3])
                            if int(dm[3])==value and any(u=='秒' and abs(v-total)<=.0051 for v,u in pairs):durations.append({'display':dm[0],'total_seconds':total})
                        if durations:
                            diagnostic='复合时长尾部秒数被误当作独立累计秒数；整段时长有绑定证据'
                            detail['supported_compound_durations']=durations
                    if unit=='m' and '未构造前方预测窗口' in item['text']:
                        diagnostic='否定句中的配置窗口长度被当作测量事实；无已构造前方范围，需区分配置与结果'
                    detail.update(value=value,unit=unit,supported_elsewhere_in_writer_pool=elsewhere)
                elif error=='unsupported_numeric_content':
                    scoped=_numbers(json.dumps(support,ensure_ascii=False));global_numbers=_numbers(json.dumps(allfacts,ensure_ascii=False))
                    missing=[str(n) for n in _numbers(item['text']) if not _number_is_supported(n,scoped)]
                    absent_everywhere=[str(n) for n in _numbers(item['text']) if not _number_is_supported(n,global_numbers)]
                    detail={'missing_in_bound_facts':missing,'missing_in_entire_writer_pool':absent_everywhere}
                    if not absent_everywhere:diagnostic='数值存在于其他事实，当前段落支持绑定不足（非独立数值真伪标签）'
                rows.append({'date':day,'check':error,'text':item['text'],'fact_ids':item['fact_ids'],
                             'diagnosis':diagnostic,'evidence':detail,'original_FAIL_preserved':True,'human_label':''})
        for check in quality.get('reliability_checks',[]):
            for error in check['errors']:
                diagnosis='明确阻断或待核查'
                if error=='historical_range_written_as_current_segment' and '位于当日开挖范围后方' in check['text']:
                    diagnosis='误报：句子明确说历史区段位于当天范围后方，不是把历史区段当作当天区段'
                rows.append({'date':day,'check':error,'text':check['text'],'diagnosis':diagnosis,
                             'original_FAIL_preserved':True,'human_label':''})
        for f in pack['facts']:
            data=f.get('writer_data',{});text=data.get('text','')
            ranges=re.findall(r'DyK(\d+)\+(\d+(?:\.\d+)?)\s*[~～至]\s*DyK(\d+)\+(\d+(?:\.\d+)?)',text)
            for k,a,l,b in ranges:
                first,second=1000*int(k)+float(a),1000*int(l)+float(b)
                if first<=second:continue
                source=f.get('source',{});p=Path(source.get('path',''))
                preserved=None;literal_substring=None;file_hash_matches=None
                if p.is_file():
                    original=read(p);node=original
                    for token in source['source_pointer'].lstrip('/').split('/'):
                        node=node[int(token)] if isinstance(node,list) else node[token]
                    preserved=(node==source.get('quote'))
                    literal_substring=isinstance(node,str) and source.get('quote','') in node
                    file_hash_matches=digest(p)==source.get('sha256')
                    dest=OUT/'source_issue_evidence'/day;dest.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(p,dest/'original_structured_source.json')
                source_problems.append({'date':day,'fact_id':f['fact_id'],'original_text':text,'original_order_range':[first,second],
                    'normalized_range_in_database':data.get('range_m'),'source':source,'source_pointer_value_equals_stored_quote':preserved,
                    'source_quote_is_literal_substring_of_pointer_value':literal_substring,
                    'source_file_hash_matches_recorded':file_hash_matches,
                    'meaning':'源结构化文字中已含跨千米反向范围；不能自动认定正确端点，更不能悄悄改源数据',
                    'requires_human_original_drawing_check':True,'not_detected_as_blocking_by_current_validator':True})
        pdf=folder/'report'/f'{day}.three_chapter.pdf'
        with fitz.open(pdf) as doc:
            text=''.join(p.get_text() for p in doc);normalized=re.sub(r'\s+','',text)
            for page_id,page in enumerate(doc):
                dest=OUT/'PDF_previews'/day;dest.mkdir(parents=True,exist_ok=True)
                page.get_pixmap(matrix=fitz.Matrix(1.1,1.1)).save(dest/f'page{page_id+1}.png')
        claimtexts=[c['text'] for ch in draft['chapters'] for c in ch['claims']]
        checks.append({'date':day,'all_model_paragraphs_in_final_PDF':all(re.sub(r'\s+','',t) in normalized for t in claimtexts),
            'artifact_passed':status.get('artifact_passed'),'quality_status':quality['status'],
            'model_to_PDF_text_check_not_semantic_truth':True})
        criteria=['进尺、推进时间和施工范围','主要施工过程','PLC现象工程信息量','地质区段对应',
                  '预测/历史/当日揭示区分','分析范围与因果限制','文字自然性','图文一致性']
        for criterion in criteria:
            human.append({'date':day,'criterion':criterion,'pdf':str(pdf),'facts':str(folder/'evidence_pack.json'),
                          '人工结论':'','证据及问题':'','审阅人':'','审阅日期':''})
    write_json(OUT/'INDEPENDENT_FAILURE_DIAGNOSTICS.json',rows);csv(OUT/'INDEPENDENT_FAILURE_DIAGNOSTICS.csv',rows)
    write_json(OUT/'NEW_SOURCE_ISSUES.json',source_problems)
    write_json(OUT/'FINAL_ARTIFACT_CHECKS.json',checks);csv(OUT/'HUMAN_ACCEPTANCE_BLANK.csv',human)
    return rows,source_problems,checks


def documentation(history,diagnostics,source_issues,artifacts):
    results=read(OUT/'independent_results.json');calls=read(OUT/'model_calls.json')
    usage={'calls':len(calls),'input_tokens':sum(r.get('input_tokens',0) for r in calls),
        'output_tokens':sum(r.get('output_tokens',0) for r in calls),'retries':sum(r.get('transport_retries',0) for r in calls),
        'requested_model':sorted({r['model_requested'] for r in calls}),'returned_model':sorted({r['model_returned'] for r in calls}),
        'new_LLM_planning_calls':0,'new_LLM_revision_calls':0,'billing_cost':'未核实实时账单费率，未估造费用'}
    write_json(OUT/'MODEL_USAGE.json',usage);csv(OUT/'MODEL_CALLS.csv',calls)
    compare='\n'.join(f"|{r['date']}|FAIL|{r['same_old_input_status']}|{'；'.join(r['same_old_input_errors']) or '无明确阻断，工程语义待审'}|" for r in history)
    validation='\n'.join(f"|{r['date']}|{r['category']}|{r.get('quality_status','未生成')}|{r.get('pages','')}|{r.get('artifact_passed',False)}|" for r in results)
    markdown('RESULTS.md',f'''# 最终可靠性修复与冻结验收

## 结论：暂不冻结

本轮修复了已确认的通用错误，但独立日期复验仍暴露重复的证据绑定问题和核验误报，另发现一条尚未阻断的源里程疑点。不能因364项测试通过、14个预设探针符合预期，就宣布生成系统已达到最终可靠性要求。保留本次可复现实验快照，不发布为无人审阅正式工程日报版本。

## 已完成的修复

1. 单区段地质—机器关系不再依赖“存在相邻区段比较”才携带端点，关系与原始全量事实仍保留。
2. analysis_units为空时不强制填写ID；虚构、空字符串ID和无区段硬比较仍阻断。
3. 下降百分比按指标、方向及幅度核对，支持带负号的变化量和起止值；错误方向仍阻断。
4. 日期/揭示检查处理明确否定，保留对未来资料和历史观察冒充当日揭示的阻断。
5. current/reference结构、原值与变化量显式保留；主语含糊进入REVIEW，不按数字相同即确认正确。
6. 将PLC稳定样本不足、可匹配地质相邻区段不足和可比条件不足分开；零推进日不再自动写间歇推进。
7. PASS/REVIEW/FAIL分层：passed只表示确定性检查无明确阻断；工程验收未完成。原始规则发现及严重度转换记录保留。

## 历史回放

12候选中11天图、分析及提纲检查通过；2023-11-05的异常里程继续正确阻断。原来5个提纲闭包失败日期全部恢复离线提纲检查，未删除其真实关系。

以下使用原始模型正文、不改写历史报告，并以完全相同的旧提纲/事实包重放检查器：

|日期|旧判定|新判定|仍存在的阻断|
|---|---|---|---|
{compare}

旧5/23的0秒并非简单证明“数字错”：该段原绑定事实没提供该时长。重建事实包补齐真正已有的推进时长后，这项消失，但历史区段措辞仍被阻断。旧11/18的全局样本不足措辞也仍是FAIL。不能把消除误报变成原失败报告全通过。

## 冻结选择后的三天验证

随机种子2026102802；排除本轮12个修复开发日及两个已知开发例，保存完整候选池、分层条件、选择结果与调用前哈希。每个日期只调用一次正文生成；使用既有确定性现象提纲，不是LLM提纲收益实验。

|日期|预选类型|质量状态|PDF页数|成品有限检查|
|---|---|---|---:|---|
{validation}

三份PDF均真实生成，但原运行判定仍为FAIL。没有换日期、重试、局部修订或验证后改提示词。三份均没有可用的相邻区段比较单元：该分支本轮只由历史回放及注入测试验证，尚无新日期生成验证。正常组实际有预报交集，不等于资料支持充分的多区段对比，属于本次选择覆盖的限制。

## 新失败究竟是什么

- 2/04和6/19：3.1引用了全天速度、推力、扭矩统计，却只绑定GEO_CONTEXT与SEGMENT_RESPONSE_SYNTHESIS，未绑定PARAMETER_ANALYSIS_SUMMARY。数值可在全日另一事实中找到，不应直接称算错；但来源绑定合同未满足，不能静默借用全池事实来救正文。
- 2/04：3小时03分40秒等完整时长有证据，但40秒尾数被当作独立累计秒数检查，产生误报。
- 6/19：原文明确说“该历史区段位于当日开挖范围后方”，仍触发当前开挖范围误配检查，属于关系措辞误报。
- 8/10：没有可靠推进方向、未构造前方窗口，否定说明中的“20m”仍触发数值检查；配置量与测量结果尚未正确区分。
- 8/10：历史结构化素描原文已包含DyK1017+176至DyK1016+208的反向里程，数据库排序成968m范围后仍入选为最近历史背景，最终被复述。存储引文逐字包含在原JSON指针指向的较长字段内，文件哈希一致；并非整个字段与子句完全相等，也不是模型凭空生成。无法仅靠排序确定正确端点，当前检查器未把它作为阻断。这是源证据适用性待核实，不改原资料、不擅自把1016改成1017。

因此这次不足不只是“缺人工评价”：生成后的陈述支持闭包仍需一般化处理，日期/空间措辞与数值角色校验还不可靠。没有在新日期上继续修规则后宣称独立通过。

## 验证与资源

全项目364项测试通过；14个基于真实正文构造的预设正向/错误注入案例全部满足预期，其中2个正向例和12个错误例。它们只验证指定扰动，不代表真实语义准确率。新增DeepSeek {usage['calls']}次，输入{usage['input_tokens']}、输出{usage['output_tokens']} Token，总计{usage['input_tokens']+usage['output_tokens']}；零重试、零修订，预算已停止。请求deepseek-chat，返回deepseek-flash。

旧实验全部文件校验值、受保护PLC算法/修复代码/统计特征/SQLite及329个处理后PLC文件校验值不变。仅修改提纲适配、检查与研究稿状态显示，未重跑329天分段。完整独立人工工程审阅尚未完成；HUMAN_ACCEPTANCE_BLANK.csv全部留空，辅助诊断不冒充专家结果。

## 下一步边界

只剩少量明确的可靠性问题应作为下一次定点修复：最终陈述的支持ID闭包（不能借全池事实盲目放行）、复合时长/配置量角色，以及历史里程疑点的准入与待核查处理。完成后必须另留新日期验证；本轮三天已暴露问题，后续不得再冒充未见测试集。暂不增加算法、Agent、指标或大规模实验。本轮结果本身可用于论文的失败分析和可靠性局限，不能写稳定无人审核出报或真实事实准确率。
''')
    markdown('METHOD_AND_LIMITS.md','''# 候选版本实际方法与评价边界

PLC沿用修复后样本、冻结平台/四阶段标签及既有趋势/区段统计；地质沿用现有SQLite原文与时空准入。Evidence Pack保留事实、来源、关系和区段。Python按现象形成固定三章内容计划，DeepSeek一次性写正文。检查器校验格式/事实ID/计划归属/已算数值/若干角色及空间规则。最后使用同一JSON渲染HTML/PDF，逐段复核PDF是否含原文字。

本轮确定性程序：样本/窗口/区段统计、关系支持闭包、简化原因、提纲、规定范围内的数值/日期/ID/适用范围核查、图表/PDF渲染。LLM：按已有提纲组织正文，不能计算或改变事实。本轮未新增LLM规划、未执行局部修订，也没有对局部修订有效性作新结论。

检查状态合同：

- 确定性条目PASS仅说明该已执行规则无已识别冲突，不说明句子全部语义正确。
- REVIEW保留无法确定的比较主语、未解析的原预报参照及工程人工审阅缺失。
- FAIL保留明确数据/ID/来源/角色冲突；尚有误报则原判定仍保留，辅助诊断单独记录。
- report.passed为历史接口的“无确定性阻断”布尔值；report.status才是分级状态。只要未独立人工验收，整份无阻断报告也属于REVIEW，不声称正式工程验收。

质量结果保存claim_checks、reliability_checks、原发现、严重度转换、errors、review_items，不能只读passed。FAIL PDF明确标识为失败研究稿。

研究局限：规则语义检查有限，不是自然语言推理模型；没有独立人工事实金标准；3日仅为功能复验，不能证明泛化；本轮未覆盖新的相邻比较分支；结构化地质中的原文疑点不因程序validated而变真值。引用ID完整与原始事实支持、最终正文可靠，必须分别评价。
''')
    markdown('REPRODUCE.md',f'''# 复核说明

先读RESULTS.md，再看FAILURE_AUDIT.full.json、HISTORICAL_FINAL_COMPARISON.json和INDEPENDENT_FAILURE_DIAGNOSTICS.json。
before_source保存修复前实际源码；source_code保存本轮最终源码；CODE_CHANGES.patch可对照。baseline_evidence保留上轮12日事实、5份原始模型/报告及失败记录。history_replay_v2是新的回放，不覆盖旧结果。独立3日所有请求/响应/提纲/事实/质量/PDF在independent_validation。

本机解释器：{sys.executable}。

```bash
python -m pytest -q
```

输入真实路径在experiments/report_reliability_freeze_20261028/run.py中。audit、replay、select、generate、deliver依次运行；本次输出已经存在，重新执行需显式使用新目录。generate会付费并拒绝重复ledger；不要为了复核调用它。可直接加载存储JSON调用draft_quality进行不付费回放。模型正文不能保证逐字再现，因此保存原始响应。

原始大型PLC与地质库不随包复制；12日及3日事实/分析、源行索引和一条新地质疑点的原始JSON保留。source_file_manifest中提供全部329日处理后PLC哈希；DB与受保护文件哈希在保护记录。SHA-256只校验内容身份，不证明工程准确性。

没有人工金标准、没有专家签认、没有新的地质影响建模，没有改PLC算法和源数据。三天新样本验证后未再修改核心方法。
''')
    markdown('FREEZE_DECISION.md','''# 开发冻结建议：暂不冻结

支持暂停最终冻结的直接证据：

1. 3个未参与本轮修复调试的预选日期全部仍为FAIL，不能认为原通用问题消除就达到跨日稳定性。
2. 生成器跨段取用了全日统计但未给对应支持事实ID，表明输入/计划/最终陈述之间的证据合同仍不稳定。
3. 检查器出现新的复合时长和空间否定/后方关系误报，且8/10的源反向范围未被可靠阻断。
4. 人工工程内容验收未完成，独立3日也没有覆盖可用相邻比较单元分支。

已经完成：5个原提纲失败的闭包恢复、空单元不造ID、下降方向检查、否定揭示表达的基本处理、比较主语REVIEW、不同不足原因区分。原反向里程日继续拒绝；旧失败正文没有改成成功。

可保存本轮候选源码与完整证据快照供论文研究复现，但这不等于“冻结为最终可靠版本”。不建议扩大新功能；下轮只修复本报告列出的几类可靠性问题，再留新日期验收。若最终只用于有人工审阅的研究系统，也必须明确人工责任与未核查范围。
''')


def finish():
    history=final_historical_replay();diagnostics,source_issues,artifacts=independent_diagnostics()
    documentation(history,diagnostics,source_issues,artifacts)
    unchanged=read(OUT/'protected.before.json')==protection()
    changed_history=[str(p) for rel,sha in read(OUT/'history.before.json').items()
                     if not (p:=OLD/rel).is_file() or digest(p)!=sha]
    screened=pd.read_csv(OLD/'329day_screening.csv')
    changed_PLC=[r.date for r in screened.itertuples() if digest(Path(r.source_samples))!=r.samples_sha256]
    write_json(OUT/'PROTECTION_VALIDATION.json',{'passed':unchanged and not changed_history and not changed_PLC,
        'protected_core_and_database_unchanged':unchanged,'modified_old_experiment_files':changed_history,
        'modified_processed_PLC_dates':changed_PLC,'days_checked':len(screened),
        'method_unchanged_after_independent_date_freeze':code_hashes()==read(OUT/'pre_API_freeze.json')['code_sha256']})
    csv(OUT/'source_file_manifest.csv',screened[['date','source_samples','samples_sha256']].to_dict('records'))
    patch=[]
    for name in MODIFIED:
        before=(OUT/'before_source'/name).read_text().splitlines(keepends=True)
        after=(ROOT/name).read_text().splitlines(keepends=True)
        patch.extend(difflib.unified_diff(before,after,fromfile='before/'+name,tofile='after/'+name))
    (OUT/'CODE_CHANGES.patch').write_text(''.join(patch),encoding='utf-8')
    src=OUT/'source_code';src.mkdir(exist_ok=False)
    for folder in ['tbm_process','scripts','tests','configs']:
        shutil.copytree(ROOT/folder,src/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(Path(__file__).parent,src/'experiments'/Path(__file__).parent.name,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'experiments/multidate_response_validation_20261028',
        src/'experiments/multidate_response_validation_20261028',ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['config.json','requirements.txt','requirements-geology-store.txt','pytest.ini']:
        if (ROOT/name).exists():shutil.copy2(ROOT/name,src/name)
    baseline=OUT/'baseline_evidence';baseline.mkdir(exist_ok=False)
    shutil.copytree(OLD/'A_reports',baseline/'A_reports',ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['RESULTS.md','A_offline_statuses.json','A_generation_statuses.json','date_selection.frozen.json','model_calls.json']:
        shutil.copy2(OLD/name,baseline/name)
    write_json(OUT/'ENVIRONMENT.json',{'python':sys.version,'executable':sys.executable,
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
        'dependencies':subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True),
        'candidate_code_sha256':code_hashes(),'freeze_recommendation':'暂不冻结'})
    from experiments.multidate_response_validation_20261028.delivery import package
    package(OUT)
