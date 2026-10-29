"""Fixed engineering publication standard over an already analysed three-chapter day."""
from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
import re
import shutil

from .activity_facts import build_activity_facts
from .engineering_presentation import (
    CSS, FIELDS, ROOT, SECTION_TITLES, check_pdf, dump_csv, figures, numbers, read,
)
from .facts import digest, duration, write_json
from tbm_process.evidence_graph.joint_reporting import ModelSession
from tbm_process.evidence_graph.three_chapter import apply_window_choices
from tbm_process.final_reporting.common import load_deepseek_api_key


STANDARD = ROOT/'configs/engineering_report_standard.json'


def package_photos(package,pack,approved):
    """Use the exact historical sketch selected upstream, with the user's confirmed file convention."""
    from PIL import Image
    table={f['fact_id']:f for f in pack['facts']}
    p5=next(p for p in _paragraphs(approved) if p['paragraph_id']=='p5')
    ids=p5.get('fact_ids',p5.get('support_ids',[]))
    document_ids={table[i].get('data',{}).get('document_id') for i in ids if i in table}
    docs=package.get('current_sketch_documents',[])
    selected=[d for d in docs if d['document_id'] in document_ids]
    if not selected and len(docs)==1:selected=docs
    if docs and not selected:raise ValueError('ambiguous_historical_photo_document_do_not_guess')
    photos=[]
    for doc in selected:
        meta=doc['metadata'];path=Path(meta['structured_source_path'])
        if not path.is_file() or digest(path)!=meta['source_sha256']:raise ValueError('stale_sketch_source_refresh_preparation_first')
        report_date=meta.get('available_date')
        if not report_date or report_date>=package['date']:raise ValueError('historical_sketch_date_not_prior_day')
        raw=read(path);entries=raw.get('现场照片',[])
        if len(entries)!=3 or any(len(e)!=1 for e in entries):raise ValueError('historical_photo_roles_missing')
        for index,entry in enumerate(entries,3):
            role,url=next(iter(entry.items()));image=path.parent/f'图{index}.jpg'
            if not image.is_file():raise ValueError('original_site_photo_missing')
            with Image.open(image) as im:im.verify()
            photos.append({'role':role,'path':str(image),'sha256':digest(image),'report_date':report_date,
                           'range_m':[meta['coverage_start_m'],meta['coverage_end_m']],
                           'JSON':{'path':str(path),'sha256':meta['source_sha256'],'source_pointer':'/现场照片'},
                           'JSON_original_URL':url,'proof':'user_confirmed_2026-10-28_图3_图4_图5_in_JSON_photo_order'})
    return photos


def _paragraphs(narrative):
    if 'paragraphs' in narrative:
        return narrative['paragraphs']
    return [p for ch in narrative['chapters'] for p in ch['claims']]


def load_bundle(folder, facts_path=None, approved_path=None):
    """Two explicit saved formats; never search across dates or model groups."""
    if (folder/'original_case.json').is_file():
        case=read(folder/'original_case.json')
        approved=read(approved_path or folder/'narrative.revised.json')
        package_path=facts_path or Path(case['original_report']).parent/'report_facts.json'
        photos=read(folder/'historical_photo_provenance.json') if (folder/'historical_photo_provenance.json').exists() else []
    else:
        pack=read(folder/'evidence_pack.json');plan=read(folder/'content_plan.json');draft=read(folder/'narrative.json')
        case={'day':pack['date'],'pack':apply_window_choices(pack,plan),'plan':plan,'draft':draft,
              'assets':{'figure:parameters_focus':{'path':str(folder/'figures/parameters_focus.png')}},'original_report':str(folder)}
        approved=read(approved_path) if approved_path else draft
        package_path=facts_path or folder/'report_facts.json'
        photos=read(folder/'historical_photo_provenance.json') if (folder/'historical_photo_provenance.json').exists() else []
    package=read(package_path)
    if not photos:photos=package_photos(package,case['pack'],approved)
    if package['date']!=case['pack']['date'] or case['day']!=package['date']:
        raise ValueError('publication_day_mismatch')
    if Path(package['source']['samples']).resolve()!=Path(case['pack']['source']['samples']).resolve():
        raise ValueError('publication_PLC_sources_differ')
    if digest(Path(package['source']['samples']))!=package['source']['samples_sha256'] or package['source']['samples_sha256']!=case['pack']['source']['samples_sha256']:
        raise ValueError('stale_publication_PLC_hash')
    if [p['paragraph_id'] for p in _paragraphs(approved)]!=['p1','p2','p3','p4','p5','p6']:
        raise ValueError('expected_six_scoped_approved_paragraphs')
    for p in photos:
        if digest(Path(p['path']))!=p['sha256']:raise ValueError('historical_photo_SHA_changed')
        if not p.get('report_date') or p['report_date']>=package['date']:raise ValueError('historical_photo_not_prior_day')
        ref=p.get('JSON')
        if ref and digest(Path(ref['path']))!=ref['sha256']:raise ValueError('historical_sketch_JSON_changed_refresh_evidence_before_publication')
    return case,package,approved,photos,package_path


def displayed_activity(row):
    return {'activity_id':row['activity_id'], 'time':row['start_time'][11:19]+'至'+row['end_time'][11:19],
            'advancing':row['advancing_display'],
            'progress':f"{row['progress_m']:.2f}m" if row['progress_m'] is not None else '待核实',
            'rate':f"{row['rate_m_per_advancing_hour']:.2f}m/h" if row['rate_m_per_advancing_hour'] is not None else '—',
            'local_windows':[{'time':w['start_time'][11:19]+'至'+w['end_time'][11:19], 'pattern':w['pattern']} for w in row['selected_windows']]}


def build_input(case, package, approved, samples, standard):
    activities=build_activity_facts(package,case['pack'],samples)
    prior={p['paragraph_id']:p for p in _paragraphs(approved)}
    day=activities['daily']
    overall={'advance':f"{day['advance_m']:.2f}m" if day['advance_m'] is not None else '待核实',
             'advancing':day['advancing_display'],
             'advancing_share':f"{day['advancing_share_percent']:.1f}%" if day['advancing_share_percent'] is not None else None,
             'rate':f"{day['rate_m_per_advancing_hour']:.2f}m/h" if day['rate_m_per_advancing_hour'] is not None else None,
             'rate_name':'全天折算推进率','scope':'日进尺除以累计推进小时，不是稳定速度中位数'}
    chosen=[displayed_activity(a) for a in activities['selected_activities']]
    usable=[a for a in activities['selected_activities'] if a['progress_m'] is not None]
    main_contribution=displayed_activity(max(usable,key=lambda a:a['progress_m'])) if usable else None
    windows=[{'window_id':w['window_id'],'time':w['start_time'][11:19]+'至'+w['end_time'][11:19],
              'pattern':w['pattern'],'trends':{f:c['trend'] for f,c in w['changes'].items()}}
             for w in case['pack']['response_analysis']['selected_time_windows']]
    coverage=activities['coverage'];other=duration(coverage['other_advancing_seconds'])
    paragraphs=[{'paragraph_id':'p1','section':'1','locked_text':prior['p1']['text']},
        {'paragraph_id':'p2','section':'2.1','support_ids':['DAILY_OPERATION','PARAMETER_ANALYSIS_SUMMARY'],
         'daily':overall,'daily_stable_table':day['stable_statistics']},
        {'paragraph_id':'p3','section':'2.2','support_ids':['MAJOR_ACTIVITY_SUMMARY']+[a['activity_id'] for a in chosen]+[w['window_id'] for w in windows],
         'activities':chosen,'windows':windows,'other_advancing_display':other,
         'largest_progress_among_selected':main_contribution,
         'displayed_group_count':len(chosen),'all_group_count':coverage['all_activity_count'],
         'major_activities_are_not_all_activities':coverage['selected_count']<coverage['all_activity_count']}]
    paragraphs += [{'paragraph_id':i,'section':s,'locked_text':prior[i]['text'].replace('名义里程','里程'),
                    'retained_source_binding':{k:v for k,v in prior[i].items() if k!='text'}}
                   for i,s in [('p4','3.1'),('p5','3.2'),('p6','3.3')]]
    return {'date':package['date'],'version':standard['version'],'standard':standard,'paragraphs':paragraphs,
            'source_case':case['pack']['source'],'scope':'publication_over_existing_analysed_day; no_new_PLC_or_geology_algorithm'},activities


def request_for(data, model):
    system='''你是中文TBM工程日报编辑。只负责将程序已计算的事实写成自然的工程叙述，不计算、不增加事实或原因。
返回JSON {paragraphs:[{paragraph_id:"p2",text,support_ids},{paragraph_id:"p3",text,support_ids}],edit_notes:[]}。ID、support_ids必须原样返回。其余章节由程序保留，不要重写。
不写章节编号、章节标题或“图表另行插入”等编写过程说明，它们由程序渲染。
第二章固定先总体、再主要区间。
p2是“2.1 全天运行概况”：概括全天完成量、累计推进时间、推进时间占比、全天折算推进率，数字采用daily给定显示值。说明推进时间占比的分母为有效记录时间即可；不要评判效率高低。主要参数表和全天曲线由Python插入，只需一句自然的承接，不逐项复述中位数/P10/P90。
p3是“2.2 主要掘进区间”：围绕程序选出的主要时段，说明进尺贡献、实际推进时长和折算推进率，优先描述有信息量的活动及差异。不必在正文把表中每个数重复一遍。不能把起止跨度叫推进时长。若还有其他活动，用一句话交代未列时段仍有推进记录，不把代表性时段当作全天全部。
largest_progress_among_selected是程序给出的所选主要时段中进尺最多的一组，可据此说明其贡献；不要自行计算新比较。正文提到某段进尺或折算率时，必须在同一句先写它完整的time，再写该段给定数值，不把其他区间的数值移过来。单位、精度原样使用。
然后按真实时间说明已选局部窗口的机器表现。每个窗口均保留完整HH:MM:SS起止和参数方向，说明局部组合变化；不写样本数、支持分钟数、首末四分之一统计值，真实局部曲线另行插入。可以自然分段，不机械地一条事实一句话。
局部窗口只描述pattern.trend_fields列出的参数及其方向；trends中stable只表示未检出给定持续趋势，不证明参数波动小，不得写成“基本稳定”“保持稳定”。不额外补充未选参数的稳定性结论。
区间进尺是各运行平台内部PLC开累进尺净变化合计。全天进尺来自导向盾首里程，两个口径不得混用，不强行对账为同一量。折算推进率统一为m/h，与mm/min速度中位数不同，不称为设备效率或地质效率。无可靠进尺时写待核实，不算率、不补零。
只使用给定显示数字，不自行换算、排序后声称同工况优劣或求和。不要输出样本数、审计信息、内部ID、HTML或表格。不要写“程序已生成”“有证据的组合方向”“未获确认”等说明程序工作的句子。
不引入设备故障、地质原因、安全风险、施工建议、运行平稳、显著异常等未经给定事实支持的结论。限制集中用清晰的口径表述，不逐段重复“不能、不代表”。
参考文风：先说发生什么，再用少量准确数字支撑；使用自然衔接，少用数据库式的“字段为、范围为、资料类型为”。不改参数方向，不把局部趋势写成全天趋势。
只返回JSON。'''
    facts=[p for p in data['paragraphs'] if p['paragraph_id'] in ('p2','p3')]
    return {'model':model,'temperature':.1,'max_tokens':5500,'response_format':{'type':'json_object'},
            'extra_body':{'thinking':{'type':'disabled'}},'messages':[{'role':'system','content':system},
            {'role':'user','content':json.dumps({'date':data['date'],'version':data['version'],'facts':facts},ensure_ascii=False)}]}


def normalize_reply(payload):
    """Remove only known non-factual publishing scaffolding; raw model replies stay untouched."""
    result=deepcopy(payload);changes=[]
    if not isinstance(result,dict):return result,changes
    for p in result.get('paragraphs',[]):
        if not isinstance(p,dict) or not isinstance(p.get('text'),str):continue
        before=p['text'];text=before
        for section in ('2.1','2.2'):
            title=SECTION_TITLES[section]
            if text.strip().startswith(title):text=text.strip()[len(title):].lstrip()
        for sentence in ('各窗口真实局部曲线另行插入。','真实局部曲线另行插入。'):
            text=text.replace(sentence,'')
        p['text']=text
        if text!=before:changes.append({'paragraph_id':p.get('paragraph_id'),'before':before,'after':text,
            'method':'exact_allowlisted_heading_or_nonfactual_authoring_sentence_only','engineering_values_changed':False})
    return result,changes


def validate_reply(payload,data):
    errors=[]
    items=payload.get('paragraphs',[]) if isinstance(payload,dict) else []
    if not isinstance(items,list) or any(not isinstance(i,dict) for i in items) or [p.get('paragraph_id') for p in items]!=['p2','p3']:
        return {'passed':False,'errors':['publication_paragraph_schema_or_order']}
    facts={p['paragraph_id']:p for p in data['paragraphs']}
    for p in items:
        fid=p['paragraph_id'];f=facts[fid];text=p.get('text','')
        if not isinstance(text,str) or not text.strip():errors.append(fid+':empty');continue
        if p.get('support_ids')!=f['support_ids']:errors.append(fid+':source_binding_changed')
        # Only display-ready facts are numeric candidates. Internal IDs and raw samples are not evidence for prose numbers.
        numeric_facts={k:v for k,v in f.items() if k not in ('support_ids','paragraph_id','windows','daily_stable_table')}
        if fid=='p3':numeric_facts['windows']=[{'time':w['time']} for w in f['windows']]
        available=numbers(json.dumps(numeric_facts,ensure_ascii=False))
        for n in numbers(text):
            if n not in available:errors.append(fid+':unsupported_display_number:'+n)
        if re.search(r'名义|未获确认|未确认连续|\d+条(?:合格)?(?:样本|记录)|支持(?:约)?\d|程序已|有证据的组合方向',text):errors.append(fid+':unwanted_audit_prose')
        if re.search(r'(?:^|\n)\s*2\.[12]\s*|(?:曲线|图表|图片).*?另行插入',text):errors.append(fid+':duplicated_heading_or_authoring_instruction')
        if re.search(r'基本稳定|保持稳定|趋于稳定|运行稳定',text):errors.append(fid+':absence_of_trend_is_not_proven_stability')
        if re.search(r'地质导致|围岩导致|显著异常|运行平稳|效率低下|故障|设备效率|地质效率|连续掘进',text):errors.append(fid+':unsupported_interpretation')
        if re.search(r'\b(?:ACTIVITY|RW|DOC|GF):',text):errors.append(fid+':internal_ID_visible')
        if fid=='p2':
            for k in ('advance','advancing','advancing_share','rate'):
                value=f['daily'].get(k)
                if value and value!='待核实' and value not in text:errors.append(fid+':missing_daily_'+k)
            if f['daily'].get('rate') and '折算推进率' not in text:errors.append(fid+':rate_role_missing')
        else:
            # Bind interval quantities to the most recent explicit activity range within the same sentence.
            for sentence in re.split('[。；\n]',text):
                spans=sorted((m.start(),a) for a in f['activities'] for m in re.finditer(re.escape(a['time']),sentence))
                quantities=list(re.finditer(r'(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(m/h|m)(?![A-Za-z/])',sentence))
                if '其余主要时段' in sentence and not spans and f.get('largest_progress_among_selected'):
                    main=f['largest_progress_among_selected']['time']
                    others=[a for a in f['activities'] if a['time']!=main]
                    for unit,key in [('m','progress'),('m/h','rate')]:
                        actual=[float(m[1]) for m in quantities if m[2]==unit]
                        values=[float(a[key].removesuffix(unit)) for a in others if a[key] not in ('待核实','—')]
                        if actual and (not values or actual!=[min(values),max(values)]):errors.append(fid+':other_selected_range_not_verified')
                    continue
                for m in quantities:
                    owners=[a for pos,a in spans if pos<m.start()]
                    if not owners:errors.append(fid+':interval_quantity_missing_explicit_scope');continue
                    key='rate' if m[2]=='m/h' else 'progress'
                    expected=owners[-1][key]
                    if m[1]+m[2]!=expected:errors.append(fid+':interval_'+key+'_wrong_scope_or_value')
            for w in f['windows']:
                part=next((s for s in re.split('[。；\n]',text) if w['time'] in s),'')
                if not part:errors.append(fid+':missing_window:'+w['time']);continue
                expected={w['trends'][field] for field in w['pattern']['trend_fields']}
                for field in w['pattern']['trend_fields']:
                    label=next(name for ff,name,_,_ in FIELDS if ff==field)
                    if label not in part:errors.append(fid+':missing_window_metric:'+label)
                up=bool(re.search('上升|提高|增加',part));down=bool(re.search('下降|降低|减少',part))
                if ('increasing' in expected and not up) or ('decreasing' in expected and not down):errors.append(fid+':window_direction_missing')
                if expected=={'increasing'} and down or expected=={'decreasing'} and up:errors.append(fid+':window_direction_reversed')
            if f['major_activities_are_not_all_activities'] and not re.search(r'其他|其余|未列|此外|部分短时',text):errors.append(fid+':representative_selection_scope_missing')
    return {'passed':not errors,'errors':errors,'version':data['version'],
            'semantic_status':'REVIEW_not_independent_engineering_correctness',
            'limitations':['Numeric membership is not full sentence semantic verification','Qualitative engineering interpretation remains subject to human review']}


def combine(reply,data):
    model={p['paragraph_id']:p for p in reply['paragraphs']}
    return {'paragraphs':[model[p['paragraph_id']] if p['paragraph_id'] in model else
            {'paragraph_id':p['paragraph_id'],'text':p['locked_text'],'source_binding':p.get('retained_source_binding',{})}
            for p in data['paragraphs']]}


def render_standard(narrative,data,activities,photos,windows,output):
    paragraphs={p['paragraph_id']:'<p data-claim-id="'+p['paragraph_id']+'">'+html.escape(p['text']).replace('\n','</p><p>')+'</p>' for p in narrative['paragraphs']}
    img=lambda name,caption:'<figure><img src="figures/'+name+'"><figcaption>'+html.escape(caption)+'</figcaption></figure>'
    y,m,d=map(int,data['date'].split('-'))
    body=f'<h1>{y}年{m}月{d}日 TBM施工日报</h1><div class="sub">伯舒拉岭隧道进口右线</div>'
    body+='<section data-section="1"><h2>'+SECTION_TITLES['1']+'</h2>'+paragraphs['p1']+'</section>'
    body+='<h2>'+SECTION_TITLES['2']+'</h2><section data-section="2.1"><h3>'+SECTION_TITLES['2.1']+'</h3>'+paragraphs['p2']
    body+='<table data-fact-id="PARAMETER_ANALYSIS_SUMMARY"><thead><tr><th>参数</th><th>中位数</th><th>P10</th><th>P90</th></tr></thead><tbody>'
    for field,name,unit,_ in FIELDS:
        s=activities['daily']['stable_statistics'][field]
        body+='<tr><td>'+name+'（'+unit+'）</td>'+''.join('<td>'+ (f'{s[k]:.2f}' if s[k] is not None else '—')+'</td>' for k in ('median','p10','p90'))+'</tr>'
    body+='</tbody></table><div class="note">参数表采用合格稳定推进记录；P10、P90分别为第10、第90百分位数。</div>'
    body+=img('daily_parameters.png','图1  全天PLC参数与局部窗口位置')+'</section>'
    body+='<section class="major" data-section="2.2"><h3>'+SECTION_TITLES['2.2']+'</h3>'+paragraphs['p3']
    body+='<table id="major-activities"><colgroup><col style="width:34%"><col style="width:24%"><col style="width:21%"><col style="width:21%"></colgroup><thead><tr><th>主要时段</th><th>累计推进时长</th><th>平台内进尺<br>（PLC，m）</th><th>折算推进率<br>（m/h）</th></tr></thead><tbody>'
    row_texts=[]
    for a in activities['selected_activities']:
        display=displayed_activity(a)
        cells=[display['time'],display['advancing'],display['progress'].removesuffix('m'),display['rate'].removesuffix('m/h')]
        row_texts.append(''.join(cells));body+='<tr data-activity-id="'+a['activity_id']+'">'+''.join('<td>'+html.escape(v)+'</td>' for v in cells)+'</tr>'
    body+='</tbody></table><div class="note">区间进尺汇总各运行平台内部“开累进尺”净变化，边界外变化不分配；折算推进率＝该进尺÷累计推进小时，按未取整数值计算。全天进尺另按导向盾首里程统计。</div>'
    for i,w in enumerate(windows,1):body+=img(f'window_{i}.png',f'图{i+1}  '+w['start'][11:19]+'至'+w['end'][11:19]+'局部运行参数（各参数独立纵轴）')
    body+='</section><h2>'+SECTION_TITLES['3']+'</h2><section data-section="3.1"><h3>'+SECTION_TITLES['3.1']+'</h3>'+paragraphs['p4']+'</section>'
    body+='<section class="historical" data-section="3.2"><h3>'+SECTION_TITLES['3.2']+'</h3>'+paragraphs['p5']
    if photos:
        body+='<div class="photos">'
        for i,p in enumerate(photos,1):
            dest=output/'figures'/f'historical_photo_{i}.jpg';shutil.copy2(p['path'],dest)
            body+='<figure class="photo"><img src="figures/'+dest.name+'"><figcaption>'+html.escape(p['role'])+' · 历史资料照片</figcaption></figure>'
        body+='</div>'
    body+='</section><section data-section="3.3"><h3>'+SECTION_TITLES['3.3']+'</h3>'+paragraphs['p6']+'</section>'
    body+='<footer>统计口径及原始来源见随附追溯文件。PLC统计为工程记录描述，工程语义待人工审阅。</footer>'
    css=CSS+' .major{break-before:auto} .historical{break-inside:avoid} .major th,.major td{text-align:right;overflow-wrap:anywhere}.major th:first-child,.major td:first-child{text-align:left}.major figure{margin:3mm 0} .major table{break-inside:avoid}'
    path=output/(data['date']+'.standard.html');path.write_text('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>'+css+'</style></head><body>'+body+'</body></html>',encoding='utf-8')
    return path,row_texts


def publish(bundle,output,*,facts_path=None,approved_path=None,env_file=None,offline=False):
    import fitz
    import pandas as pd
    from .rendering import make_pdf
    output.mkdir(parents=True,exist_ok=False)
    case,package,approved,photos,package_path=load_bundle(bundle,facts_path,approved_path)
    standard=read(STANDARD)
    samples=pd.read_csv(package['source']['samples']);samples['time']=pd.to_datetime(samples.time)
    data,activities=build_input(case,package,approved,samples,standard)
    write_json(output/'publication_input.json',data);write_json(output/'activity_facts.json',activities)
    write_json(output/'report_facts.json',package);write_json(output/'original_case.json',case)
    write_json(output/'approved_before.json',approved);write_json(output/'standard.used.json',standard)
    write_json(output/'historical_photo_provenance.json',photos)
    protected=[STANDARD,Path(package_path),Path(package['source']['samples']),ROOT/'config.json',ROOT/'configs/platform_analysis.json']
    protected += [Path(p['path']) for p in photos]+[Path(p['JSON']['path']) for p in photos if p.get('JSON')]
    protected += [p for name in ('process','quality','ingestion','dynamics') for p in (ROOT/'tbm_process'/name).rglob('*.py')]
    write_json(output/'sources.before.json',{str(p):digest(p) for p in protected if p.is_file()})
    display=[displayed_activity(a) for a in activities['selected_activities']]
    dump_csv(output/'major_activities.csv',[{k:v for k,v in r.items() if k!='local_windows'} for r in display])
    write_json(output/'activity_summary.json',{'daily':activities['daily'],'coverage':activities['coverage'],
                                             'selected_activities':display,'reconciliation':activities['reconciliation']})
    request=request_for(data,read(ROOT/'configs/plc_response_relations.json')['llm']['model'])
    write_json(output/'publication.request.prepared.json',request)
    if offline:return {'status':'offline_ready_no_LLM_call','output':str(output),'major_activity_count':len(display)}
    policy=read(ROOT/'configs/plc_response_relations.json');policy['llm'].update(maximum_calls=standard['quality']['maximum_generation_calls_per_publication'],transport_retries=0)
    session=ModelSession(output,policy,load_deepseek_api_key(env_file or Path.home()/'Desktop/tbm-bitemporal-twin/.env'))
    payload=session.call(request,output,'standard_publication');payload,normalization=normalize_reply(payload);check=validate_reply(payload,data)
    write_json(output/'normalization.initial.json',normalization)
    write_json(output/'quality.initial.json',check)
    if not check['passed'] and payload:
        request['messages'] += [{'role':'assistant','content':json.dumps(payload,ensure_ascii=False)},
                                {'role':'user','content':json.dumps({'task':'只修复这些问题；正确事实和自然文风保留。返回完整JSON。','errors':check['errors']},ensure_ascii=False)}]
        payload=session.call(request,output,'one_publication_revision');payload,normalization=normalize_reply(payload);check=validate_reply(payload,data)
        write_json(output/'normalization.revision.json',normalization)
    write_json(output/'quality.final.json',check)
    if not check['passed']:return {'status':'failed_publication_checks','errors':check['errors'],'output':str(output)}
    narrative=combine(payload,data);write_json(output/'narrative.revised.json',narrative)
    windows=figures(case,output)
    path,table_rows=render_standard(narrative,data,activities,photos,windows,output)
    pdf=path.with_suffix('.pdf');make_pdf(path,pdf)
    audit=check_pdf(pdf,narrative,activities['daily']['stable_statistics'],photos,output)
    with fitz.open(pdf) as doc:pdf_text=re.sub(r'\s+','',''.join(p.get_text() for p in doc))
    audit['activity_rows_missing_from_PDF']=[r for r in table_rows if re.sub(r'\s+','',r) not in pdf_text]
    before=read(output/'sources.before.json');audit['protected_inputs_unchanged']=all(digest(Path(p))==sha for p,sha in before.items())
    audit['passed'] &= not audit['activity_rows_missing_from_PDF'] and audit['protected_inputs_unchanged']
    audit['fixed_standard_version']=standard['version'];write_json(output/'artifact.validation.json',audit)
    write_json(output/'paragraph_comparison.json',[{'paragraph_id':p['paragraph_id'],'before':old['text'],'after':p['text']} for p,old in zip(narrative['paragraphs'],_paragraphs(approved))])
    result={'status':'program_checks_passed_human_REVIEW' if audit['passed'] else 'failed_artifact_checks',
            'pdf':str(pdf),'output':str(output),'pages':audit['pages'],'calls':len(session.records),
            'input_tokens':sum(r.get('input_tokens',0) for r in session.records),'output_tokens':sum(r.get('output_tokens',0) for r in session.records)}
    write_json(output/'publication_summary.json',result)
    return result
