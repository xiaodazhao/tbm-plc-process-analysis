"""Objective summaries, empty independent instruments, manuscript-ready data, package."""
from collections import Counter
from copy import deepcopy
import html
import gzip
import random
import re
import secrets
import shutil
import tempfile
import zipfile

import numpy as np

from common import *
from adapters import group_folder
from evaluation import collect_metrics,paragraphs,sentence_rows,requirements


def audit_requests():
    rows=[];shared=[]
    for scope,days in [('interface',DEVELOPMENT),('formal',load(HERE/'SAMPLING.json')['formal_dates'])]:
        for day in days:
            original=HERE/'inputs'/day/'unified_report_input.json'
            expected=load(HERE/'prepared_requests'/day/'request_status.json')
            for arm in 'ABCD':
                folder=group_folder(scope,day,arm)
                for p in sorted(folder.glob('*.request.json')):
                    req=load(p);payload=json.loads(req['messages'][1]['content'])
                    ids=[f['fact_id'] for f in payload.get('facts',[])]
                    row={'scope':scope,'date':day,'arm':arm,'stage':p.stem,
                        'model':req['model'],'temperature':req['temperature'],
                        'fact_view_hash':digest(payload.get('facts',[])),
                        'all_known_IDs':set(ids)<=set(f['fact_id'] for f in load(original)['facts']),
                        'same_full_fact_view':digest(payload.get('facts',[]))==expected.get('identical_fact_view_SHA256'),
                        'local_patch_has_target_subset':p.name=='targeted_revision.request.json',
                        'request_SHA256':sha(p),'request_path':str(p.relative_to(HERE))}
                    rows.append(row)
                prov=folder/'shared_C_provenance.json'
                if arm=='D' and prov.exists():
                    data=load(prov);c=group_folder(scope,day,'C')
                    shared.append({'scope':scope,'date':day,'same_C_plan_SHA':data['plan_sha256']==sha(c/'content_plan.json'),
                        'same_C_draft_SHA':data['draft_sha256']==sha(c/'final_narrative.json'),
                        'no_new_D_plan_or_generation':not (folder/'plan.request.json').exists() and not (folder/'generation.request.json').exists()})
    csv_write(HERE/'REQUEST_AUDIT.csv',rows);csv_write(HERE/'C_D_REUSE_AUDIT.csv',shared)
    return rows,shared


def blind():
    root=HERE/'human_blind'
    if root.exists():raise RuntimeError('new_blind_package_not_reallocated_or_read')
    root.mkdir();sample=load(HERE/'SAMPLING.json');seed=secrets.randbits(128);rng=random.Random(seed)
    tasks=[(d,a) for d in sample['human_dates'] for a in 'ABCD'];rng.shuffle(tasks)
    assignments=[];manifest=[];claims=[];ratings=[];coverage=[]
    provisional=csv_read(HERE/'PROVISIONAL_INFORMATION_REQUIREMENTS.csv')
    # Neutral HTML is used for every arm: no technical verdict, model stage or group name.
    for index,(day,arm) in enumerate(tasks,1):
        code='REPORT-'+f'{index:03d}'+'-'+f'{rng.getrandbits(32):08x}'
        folder=group_folder('formal',day,arm)
        available=(folder/'final_narrative.json').is_file()
        assignments.append({'anonymous_id':code,'date':day,'arm':arm,'source_folder':str(folder)})
        manifest.append({'anonymous_id':code,'date':day,'text_available':available})
        if available:
            draft=load(folder/'final_narrative.json')
            body='<html lang="zh-CN"><meta charset="utf-8"><style>body{font:16px/1.8 system-ui;max-width:960px;margin:30px auto;padding:16px}p{margin:12px 0}</style><h1>'+html.escape(day)+' TBM施工日报</h1>'
            for section in draft['sections']:
                body+='<h2>'+html.escape(section['section_id'])+'</h2>'
                for p in section['paragraphs']:body+='<p>'+html.escape(p['text'])+'</p>'
            (root/'reports').mkdir(exist_ok=True)
            (root/'reports'/(code+'.html')).write_text(body+'</html>',encoding='utf-8')
            save(root/'reports'/(code+'.json'),draft)
            for sid,n,p in paragraphs(draft):
                for i,sentence in enumerate(sentence_rows(p['text']),1):
                    claims.append({'anonymous_id':code,'date':day,'candidate_claim_id':f'{code}:{sid}:{n}:{i}',
                        'section':sid,'claim_text':sentence,'declared_fact_IDs':p['fact_ids'],
                        'atomization':'REQUIRES_HUMAN_SPLIT_IF_COMPOUND','support_label':'','reviewer_id':'',
                        'source_ID_reachable':'','page_and_chainage_correct':'','source_substantively_supports':'',
                        'error_type':'','judgment_reason':'','adjudication':''})
        ratings.append({'anonymous_id':code,'date':day,'text_available':available,
            'readability_1_to_5':'','conciseness_1_to_5':'','engineering_specificity_1_to_5':'','reviewer_id':'','reason':''})
        for req in provisional:
            if req['date']==day:
                coverage.append({'anonymous_id':code,**req,'present_in_body':'','present_in_common_table_or_figure':'',
                    'correctly_expressed':'','reviewer_id':'','judgment_reason':''})
    for reviewer in ['reviewer_1','reviewer_2']:
        csv_write(root/reviewer/'claims.csv',claims);csv_write(root/reviewer/'ratings.csv',ratings)
        csv_write(root/reviewer/'coverage.csv',coverage)
        csv_write(root/reviewer/'adjudication.csv',[],['candidate_claim_id','label_1','label_2','final_label','reason','adjudicator'])
    for day in sample['human_dates']:
        folder=HERE/'inputs'/day
        if (folder/'unified_report_input.json').exists():
            pack=load(folder/'unified_report_input.json')
            save(root/'evidence'/(day+'.json'),{'date':day,'facts':pack['facts'],
                'note':'same_evidence_for_all_arms; generator_required_not_independent_importance_gold'})
            # Shared figures/tables are evidence aids, not a rated method-specific layout.
            for arm in 'ABCD':
                figures=group_folder('formal',day,arm)/'figures'
                if figures.is_dir():
                    shutil.copytree(figures,root/'common_figures'/day);break
            for name in ['activity_facts.json','report_organization.json','PLC_day_observation_audit.json']:
                if (folder/name).exists():shutil.copy2(folder/name,root/'evidence'/(day+'.'+name))
    csv_write(root/'DOCUMENT_MANIFEST.csv',manifest)
    shutil.copy2(HERE/'ANNOTATION_GUIDE.md',root/'ANNOTATION_GUIDE.md')
    (root/'README.md').write_text('本包用于两名真实评价者独立盲评。只向评价者提供本目录，不提供包含A-D组结果的完整作者交付包。正文统一为匿名HTML，隐藏方法与内部PASS/FAIL；共享图表作为同日相同输入的证据辅助，不评定组间版式优劣。原子句候选需人工检查是否还含多个事实。所有标签和评分空白，不得照抄自动检查。先独立填写，再合议。\n',encoding='utf-8')
    save(root/'STATUS.json',{'requested_reports':len(tasks),'text_reports':sum(r['text_available'] for r in manifest),
        'reviewers':2,'labels_received':0,'human_metrics':'NOT_EVALUATED','allocation_commitment':digest(seed),
        'all_human_fields_blank':True,'presentation':'neutral_text_HTML_and_same_day_common_evidence_figures'})
    sealed=HERE/'_sealed';sealed.mkdir(mode=0o700,exist_ok=True)
    save(sealed/'allocation.json',{'seed':seed,'assignments':assignments});(sealed/'allocation.json').chmod(0o600)
    log('new anonymous materials created; allocation written once, not read back')


def figures():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows=[r for r in csv_read(HERE/'ARM_SUMMARY.csv') if r['scope']=='formal']
    (HERE/'figures').mkdir(exist_ok=True)
    arms=[r['arm'] for r in rows];n=[int(r['locked_dates']) for r in rows]
    f,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    axes[0].bar(arms,[int(r['PDF_generated']) for r in rows],color=['#537e8c','#ab8c5d','#609478','#9b6e87'])
    axes[0].set(title='Complete PDFs (technical outcome)',ylabel='Reports');axes[0].set_ylim(0,max(n or [1])+2)
    axes[1].bar(arms,[int(r['body_rules_passed']) for r in rows],color=['#537e8c','#ab8c5d','#609478','#9b6e87'])
    axes[1].set(title='Frozen rule checks (NOT factual accuracy)',ylabel='Reports');axes[1].set_ylim(0,max(n or [1])+2)
    f.savefig(HERE/'figures/operational_results.png',dpi=180);plt.close(f)
    f,ax=plt.subplots(figsize=(7,4),layout='constrained')
    ax.bar(arms,[(int(r['input_tokens_marginal'])+int(r['output_tokens_marginal']))/1000 for r in rows],color='#608b7b')
    ax.set(title='Actual marginal tokens; D reuses C initial draft',ylabel='Tokens (thousands)')
    f.savefig(HERE/'figures/token_cost.png',dpi=180);plt.close(f)
    data=csv_read(HERE/'pressure/summary.csv');allrows=[r for r in data if r['split']=='all' and r['category']!='ALL']
    f,ax=plt.subplots(figsize=(10,5),layout='constrained')
    ax.barh([r['category'] for r in allrows],[int(r['TP']) for r in allrows],color='#5e8a78',label='Detected injected errors')
    ax.barh([r['category'] for r in allrows],[int(r['FN']) for r in allrows],left=[int(r['TP']) for r in allrows],color='#b57e65',label='Missed injected errors')
    ax.set(xlabel='Controlled mutations',title='Injection sensitivity; NOT natural fault/accuracy rate');ax.legend()
    f.savefig(HERE/'figures/injection_results.png',dpi=180);plt.close(f)


def summary():
    rows,arms=collect_metrics();requests,reuse=audit_requests()
    calls=Calls('formal').rows();pressure=next(r for r in csv_read(HERE/'pressure/summary.csv') if r['split']=='all' and r['category']=='ALL')
    source=load(HERE/'SOURCE_AUDIT_SUMMARY.json')
    record={'version':VERSION,'freeze_integrity':freeze_check(),'formal_dates':len(load(HERE/'SAMPLING.json')['formal_dates']),
        'interface':load(HERE/'INTERFACE_ACCEPTANCE.json'),'arms':arms,'calls_by_scope':dict(Counter(r['scope'] for r in calls)),
        'actual_calls':len(calls),'successful_calls':sum(r['status']=='SUCCESS' for r in calls),
        'input_tokens':sum(r.get('input_tokens') or 0 for r in calls),'output_tokens':sum(r.get('output_tokens') or 0 for r in calls),
        'model_returned':dict(Counter(r.get('model_returned','UNAVAILABLE') for r in calls)),
        'injection':pressure,'source_audit':source,'all_D_share_C':all(r['same_C_plan_SHA'] and r['same_C_draft_SHA'] and r['no_new_D_plan_or_generation'] for r in reuse),
        'human_factual_accuracy':'NOT_EVALUATED','human_coverage':'NOT_EVALUATED','human_readability':'NOT_EVALUATED'}
    save(HERE/'FINAL_SUMMARY.json',record)
    failures=[r for r in rows if not r['generated'] or not r['body_rules_passed'] or not r['artifact_check_passed']]
    csv_write(HERE/'FAILURE_CASES.csv',failures)
    relrows=[]
    for day in load(HERE/'EXPERIMENT_REGISTRY.json')['relation_dates']:
        for arm in ['REL+','REL-']:
            p=HERE/'relations'/day/arm/'result.json'
            relrows.append({'date':day,'arm':arm,**(load(p) if p.exists() else {'status':'NOT_RUN'}),
                           'independent_semantic_judgment':'NOT_EVALUATED'})
    csv_write(HERE/'RELATION_RESULTS.csv',relrows)
    table='|组别|日期数|正文生成|PDF技术通过|规则通过|边际调用|\n|---|---:|---:|---:|---:|---:|\n'
    for r in arms:
        if r['scope']=='formal':table+=f"|{r['arm']}|{r['locked_dates']}|{r['generated']}|{r['artifact_check_passed']}|{r['body_rules_passed']}|{r['actual_calls_marginal']}|\n"
    text=f'''# 冻结版本实验结果与分析报告

## 实际完成

版本{VERSION}；源代码锁定后不修改方法。4个开发日期先验证接口；从238个未发现既往报告开发记录的日期简单随机锁定30天，不根据结果替换日期。源资料、时间准入和PLC算法沿用既有实现。初次准备的缓存路径失败已原样归档，补齐同版本本地缓存后仅重试输入准备，未消耗API。

{table}

表中规则通过不是工程事实准确率；PDF技术通过仅说明实际被检查文字已进入可读取成品，不说明工程解释正确。

## 调用与成本

全部新增实际HTTP请求{record['actual_calls']}次，成功{record['successful_calls']}次；输入{record['input_tokens']:,} Token，输出{record['output_tokens']:,} Token。分阶段、失败、usage、返回模型别名及response ID见MODEL_CALLS。D逐字复用C计划和初稿，边际调用不重复计费；独立完整运行的等效Token另列D_COST_ACCOUNTING。未以历史开发调用数冒充正式成本，未按未经核实的现价编造人民币费用。

## 核验器能查什么、漏什么

80个正反配对受控案例中，TP={pressure['TP']}、FP={pressure['FP']}、FN={pressure['FN']}、TN={pressure['TN']}。指定扰动检出率{float(pressure['recall']):.1%}；这不是实际日报事实准确率。错误样例按类别完整保留，未为了提高结果修规则。旧模板已被阅读，不声称这些封存命名案例是真正未见测试。未解析语义保持REVIEW。

## 五个问题的诚实回答

1. LLM规划是否优于规则规划：目前只能比较自动运行及组织代理指标；真实事实支持、重要信息覆盖和可读性尚无人类盲评标签，不能宣布优于。
2. 工程关系约束是否有用：保存同组件REL+/REL-真实生成及差异；其语义增量待独立评价，不能仅凭关系数或来源ID数宣称有效。
3. 编辑核验是否减少错误且保留信息：规则变化、编辑及局部修订次数、原稿保留和来源ID保留已记录；实际正确内容是否被删或新增错误仍待逐条人工审阅。未触发局部修订的日期不算修订成功。
4. 日报是否准确、完整、可读：生成与成品技术状态已实际统计；来源文件及数值重算能验证一部分，不能替代全部工程事实金标准。48份匿名文本材料和两名评价者空表供后续填写。
5. 代价是多少：以上是本轮真实调用及Token；D共享C的实际边际成本和单独运行等效成本分开报告。

## 仍未完成的部分

没有真实工程评价者填写的事实支持、覆盖和可读性标签；没有独立核实仪器ppm单位及现场安全报警能力；没有跨TBM外部验证。日期级独立只限可访问历史中未发现生成开发记录，不能排除已删除历史。共享地质来源的日期不是独立地质观测，已按来源连通分组提供敏感性分析。上述限制不能用自动PASS或测试数代替。
'''
    (HERE/'RESULTS.md').write_text(text,encoding='utf-8')
    chapter='''# 论文实验章节初稿

## 4.1 工程项目与数据

研究对象是完整三章TBM施工日报。PLC经过冻结的数据修复和施工过程分析形成事实；地质资料按其形成日期、来源性质和适用里程进入统一输入，另保留设计背景和气体研究统计。正式日来自同一工程，未构成跨工程外部验证。候选池、日期、源文件SHA及共享预报来源见数据清单。

## 4.2 实验设置与对照

四组同日共享事实视图、可用日期限制、模型配置、章节目标和渲染器。A直接生成，B由Python固定规划，C采用LLM规划，D复用C原稿后编辑、核验及必要局部修订。对照的差别不是排版。数据及方法在输出前锁定，所有失败保留。

## 4.3 评价标准

自动评价包括生成完成、PDF文字一致性、有限规则检查、明确的来源ID及信息覆盖代理、成本与失败。工程事实支持、独立必要信息覆盖、实质来源归属及可读性等待两名真实工程相关评价者匿名独立标注，当前NOT_EVALUATED。规则通过和源文件可达率不得称为事实准确率。

## 4.4 对照结果

'''+table+'''

逐日数据与日期配对bootstrap区间见METRICS_RAW和PAIRED_COMPARISONS。本表只呈现技术与规则状态，人工主要终点暂缺，因此不据此宣布方法在工程事实质量上优于基线。

## 4.5 消融与核验压力测试

REL+与REL-保留相同基础组件，仅关系编译信息不同；这是关系组织增量实验，不是图数据库存储优越性实验。受控错误注入分8类，完整混淆矩阵及漏检见pressure目录，不代替自然报告幻觉率。正确样例与错误样例成对保留，未用测试结果调规则。

## 4.6 案例分析

保留两类已知开发案例的局部修正记录：无计算比较却套用比较结论；同一句风险位置列表跨越当前窗口。它们用于解释机制边界，已永久排除于正式样本。正式样本的全部失败与编辑差异均保留，典型案例应在人工核查后确定，不凭漂亮正文选取。

## 4.7 成本与局限

真实调用按规划、正文、编辑、局部修订和关系实验分别计数。D复用C，不将共享调用重复计为实际费用。研究仅单工程；历史可访问性、资料日期不等于独立确认收到时刻、仪器单位未独立核实、盾首与掌子面配准及地质因果均构成限制。人工评价完成前，不填写支持率、独立覆盖率及读者评分。
'''
    (HERE/'EXPERIMENTS_CHAPTER_DRAFT.md').write_text(chapter,encoding='utf-8')
    index='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Freeze2实验交付</title><style>body{font:16px/1.65 system-ui;max-width:1160px;margin:30px auto;padding:15px;color:#222}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left}th{background:#edf3f1}a{color:#176c61}h1{font-size:26px}.note{padding:14px;border-left:3px solid #a07743;background:#f5f2ec}</style><h1>冻结版本正式实验</h1>'
    index+='<p>本页区分自动结果和人工未评价事项。<a href="RESULTS.md">中文结果与分析</a> · <a href="EXPERIMENTS_CHAPTER_DRAFT.md">论文实验初稿</a> · <a href="EXPERIMENT_METHODS.md">方法与复现</a> · <a href="human_blind/README.md">盲评材料说明</a></p>'
    index+='<div class="note">事实准确率、独立信息覆盖与工程可读性尚待真实盲评，不能由程序通过率代替。旧freeze1及此前30天结果未覆盖。</div>'
    index+='<h2>已知问题修正样例</h2><p>'
    for day in ['2024-08-21','2024-09-17']:index+='<a href="development_fixes/'+day+'/'+day+'.integrated.pdf">'+day+'修正版</a>　'
    index+='</p><h2>正式30日逐组结果</h2><table><tr><th>日期</th><th>A直接生成</th><th>B规则规划</th><th>C模型规划</th><th>D完整流程</th></tr>'
    for day in load(HERE/'SAMPLING.json')['formal_dates']:
        index+='<tr><td>'+day+'</td>'
        for arm in 'ABCD':
            folder=group_folder('formal',day,arm);p=folder/'result.json';result=load(p) if p.exists() else {'status':'NOT_RUN'}
            pdf=Path(result['pdf']) if result.get('pdf') else None
            label=result['status']+'；规则'+('通过' if result.get('body_rules_passed') else '未通过/未运行')
            index+='<td>'+(f'<a href="{pdf.relative_to(HERE)}">PDF</a> ' if pdf else '')+html.escape(label)+'</td>'
        index+='</tr>'
    index+='</table><h2>图表与原表</h2><p><a href="METRICS_RAW.csv">逐日指标</a> · <a href="PAIRED_COMPARISONS.csv">日期配对区间</a> · <a href="MODEL_CALLS.csv">调用日志</a> · <a href="pressure/summary.csv">错误注入</a> · <a href="FAILURE_CASES.csv">失败与规则问题</a></p>'
    for img in ['operational_results.png','token_cost.png','injection_results.png']:index+='<img style="max-width:100%" src="figures/'+img+'">'
    (HERE/'index.html').write_text(index+'</html>',encoding='utf-8')
    log('statistics and author report generated; human results remain NOT_EVALUATED')


def package():
    root=HERE;desktop=ROOT.parent;archive=desktop/'TBM_冻结版本正式实验_20261030.zip'
    if archive.exists():raise RuntimeError('delivery_archive_not_overwritten')
    excluded={'_sealed','__pycache__','.pytest_cache','.cache','.matplotlib','entry_smoke'}
    # Shared input data are included once; per-arm copies are recoverable by date.
    shared_names={'processed_samples.csv.gz','local_reference_vectors.npz','daily_evidence_graph.json',
        'report_facts.original.json','evidence_pack.original.json','reference_index_records.json'}
    files=[]
    for folder,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if d not in excluded and not Path(folder,d).is_symlink()]
        for name in names:
            p=Path(folder)/name;rel=p.relative_to(root)
            if p.is_symlink() or name.startswith('.env') or name in {'MANIFEST.csv','PACKAGE_VERIFICATION.json'}:continue
            if 'preparation_attempts' in rel.parts and name not in {'preparation.result.json','run_configuration.json'}:continue
            if 'runs' in rel.parts and name in shared_names:continue
            if name.endswith('.pyc'):continue
            files.append(p)
    suspicious=[]
    for p in files:
        if p.suffix in {'.json','.md','.py','.csv','.html','.log','.txt'}:
            data=p.read_bytes()
            if re.search(rb'\bsk-[A-Za-z0-9]{24,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data):suspicious.append(str(p.relative_to(root)))
    save(root/'SECURITY_SCAN.json',{'passed':not suspicious,'suspected_credential_files':suspicious,
        'env_files_excluded':True,'sealed_allocation_not_read_or_packaged':True,'external_symlinks_not_followed':True})
    if suspicious:raise RuntimeError('potential_credential_do_not_package')
    files.append(root/'SECURITY_SCAN.json');files=sorted(set(files))
    manifest=[{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files]
    csv_write(root/'MANIFEST.csv',manifest)
    files.append(root/'MANIFEST.csv')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5,allowZip64=True) as z:
        for p in files:z.write(p,root.name+'/'+str(p.relative_to(root)))
    bad=[]
    with tempfile.TemporaryDirectory(prefix='tbm_freeze2_zip_verify_') as tmp:
        with zipfile.ZipFile(archive) as z:
            crc=z.testzip();z.extractall(tmp)
        restored=Path(tmp)/root.name
        for r in manifest:
            if sha(restored/r['path'])!=r['sha256']:bad.append(r['path'])
    report={'archive':str(archive),'bytes':archive.stat().st_size,'file_count':len(files),
        'sha256':sha(archive),'CRC_error':crc,'extracted_SHA_mismatches':bad,'passed':not bad and crc is None,
        'sealed_mapping_included':False,'per_arm_shared_inputs_omitted_but_present_once_in_inputs':sorted(shared_names)}
    save(root/'PACKAGE_VERIFICATION.json',report)
    (desktop/(archive.name+'.sha256')).write_text(report['sha256']+'  '+archive.name+'\n',encoding='ascii')
    log('ZIP verified; bytes='+str(report['bytes']))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['summary','blind','figures','package']);a=p.parse_args()
    {'summary':summary,'blind':blind,'figures':figures,'package':package}[a.stage]()
