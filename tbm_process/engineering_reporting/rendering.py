"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
import html
import json
from pathlib import Path
import re
import shutil
import fitz
import numpy as np
import pandas as pd
from .common import *
from .source_text import span
from .schema import TITLES
from .plots import setup, separated
from tbm_process.reporting_v2.engineering_presentation import window_rows, FIELDS
from tbm_process.reporting_v2.publication import displayed_activity
from tbm_process.reporting_v2.rendering import make_pdf

CSS='''@page{size:A4;margin:14mm 14mm 16mm}*{box-sizing:border-box}body{font-family:"Arial Unicode MS","PingFang SC",sans-serif;color:#202b2d;font-size:10.3pt;line-height:1.65;margin:0;letter-spacing:0}h1{font-size:18pt;text-align:center;margin:0 0 2mm}h2{font-size:13.5pt;margin:5mm 0 2mm;border-bottom:1px solid #a7b9b3;padding-bottom:1mm;break-after:avoid}h3{font-size:11.5pt;margin:4mm 0 2mm;break-after:avoid}p{margin:1.6mm 0;text-align:justify;orphans:3;widows:3}.sub{text-align:center;color:#65716d;margin-bottom:4mm}.note,figcaption,.sources{font-size:8.3pt;color:#57625f;line-height:1.5}.note{margin:2mm 0}figure{margin:3mm 0;break-inside:avoid}figure img{display:block;width:100%;height:auto}figcaption{text-align:center;margin-top:1mm}table{border-collapse:collapse;width:100%;font-size:9pt;line-height:1.5;margin:2.5mm 0}th,td{border:1px solid #cbd4d0;padding:1.7mm 2mm;vertical-align:top}th{background:#edf2ef;font-weight:normal}tr{break-inside:avoid}thead{display:table-header-group}.window-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2mm;break-inside:avoid}.window-grid figure{margin:1mm 0;min-width:0}.daily img{max-height:63mm;object-fit:contain}.gas img{max-height:100mm;object-fit:contain}.photos{display:flex;gap:2mm;break-inside:avoid}.photos figure{width:33.33%;margin:1mm 0}.photos img{height:38mm;object-fit:contain}.badge{color:#a44734;font-size:9pt;text-align:center}.sources{margin-top:5mm;border-top:1px solid #cad4cf;padding-top:3mm}sup{font-size:7pt;color:#637c74}a{color:#276f68}@media screen and (max-width:640px){.window-grid{grid-template-columns:1fr}}'''
def table(headers,rows):
    return '<table><thead><tr>'+''.join('<th>'+html.escape(str(v))+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'

def draw_curves(folder):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    setup()
    package=load(folder/'report_facts.original.json');pack=load(folder/'evidence_pack.original.json')
    samples=pd.read_csv(folder/'processed_samples.csv.gz');samples.time=pd.to_datetime(samples.time)
    figures=folder/'figures';figures.mkdir(exist_ok=True)
    fig,ax=plt.subplots(figsize=(11,2.55),layout='constrained')
    support=pd.to_numeric(samples.sample_dt_seconds,errors='coerce').fillna(0)
    audit=[]
    for field,name,unit,color in FIELDS[:1]:
        values=pd.to_numeric(samples[field],errors='coerce').replace([np.inf,-np.inf],np.nan)
        good=values.ge(0)
        t,y=separated(samples.time,values.where(good),support)
        for p in package['platforms']:
            ax.axvspan(pd.Timestamp(p['start_time']),pd.Timestamp(p['end_time']),color='#d5e4dd',alpha=.45,zorder=0)
        ax.plot(t,y,color=color,lw=.75,zorder=3)
        rejected=values.notna()&~good
        if rejected.any():ax.scatter(samples.time[rejected],values[rejected],color='#b63a3a',s=4,zorder=4)
        for w in pack['response_analysis']['selected_time_windows']:
            ax.axvspan(pd.Timestamp(w['start_time']),pd.Timestamp(w['end_time']),color='#e0ba75',alpha=.38,zorder=1)
        ax.set_ylabel(name+'\n'+unit);ax.grid(alpha=.16)
    ax.set_xlim(pd.Timestamp(package['date']),pd.Timestamp(package['date'])+pd.Timedelta(days=1))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    fig.savefig(figures/'daily_parameters.png',dpi=200);plt.close(fig)
    audit.append({'figure':'figures/daily_parameters.png','source_SHA256':sha(folder/'processed_samples.csv.gz'),
                  'raw_processed_samples':len(samples),'platform_count':len(package['platforms']),
                  'interpolation':False,'axes':1,'parameters':FIELDS[:1]})
    windows=[]
    from .front_analysis import window_statistics
    summaries=window_statistics(pack['response_analysis']['selected_time_windows'])
    values=[abs(m['change_percent']) for w in summaries for m in w['metrics'].values() if m['change_percent'] is not None]
    extent=max(10., max(values,default=0.)*1.5)
    for n,w in enumerate(pack['response_analysis']['selected_time_windows'],1):
        block=window_rows(samples,w)
        block.to_csv(folder/f'window_{n}.exact_samples.csv',index=False,encoding='utf-8-sig',float_format='%.17g')
        fig,ax=plt.subplots(figsize=(3.5,2.3),layout='constrained')
        summary=summaries[n-1]
        for i,(field,name,unit,color) in enumerate(FIELDS):
            change=summary['metrics'][field]['change_percent']
            if change is not None:
                ax.barh(i,change,color='#398274' if change>=0 else '#ad624c',height=.54)
                ax.text(change+(extent*.04 if change>=0 else -extent*.04),i,f'{change:+.1f}%',
                        va='center',ha='left' if change>=0 else 'right',fontsize=10)
            else:ax.text(0,i,'基准为0，未换算',va='center',fontsize=8)
        ax.set_yticks(range(3),['推进速度','总推力','刀盘扭矩']);ax.invert_yaxis()
        ax.axvline(0,color='#777',lw=.7);ax.set_xlim(-extent,extent)
        ax.set_xlabel('首末四分之一中位数变化（%）');ax.grid(axis='x',alpha=.15)
        ax.set_xticks([-extent/2,0,extent/2]);ax.tick_params(axis='both',labelsize=9)
        title=w['start_time'][11:19]+'至'+w['end_time'][11:19]
        fig.savefig(figures/f'window_{n}.png',dpi=210);plt.close(fig)
        windows.append({'file':f'figures/window_{n}.png','caption':title})
        audit.append({'window_id':w['window_id'],'figure':windows[-1]['file'],'count':len(block),
                      'quartile_statistics_replayed_by_frozen_function':True,'source':w['source'],
                      'figure_SHA256':sha(figures/f'window_{n}.png'),'axes':1,'statistics':summary,
                      'unlike_units_not_mixed_on_a_raw_value_axis':True})
    save(folder/'figure_data_audit.json',audit)
    return windows


def draw_gas_context(folder):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from .gas import read_day,good_samples
    day=folder.name;gas=load(folder/'gas/gas_facts.json')
    operation=load(folder/'gas/operation_analysis.json');fields=operation['selected_plot_fields']
    if not fields:return None
    setup()
    _,frame,dt,_=read_day(Path(gas['source']['source_file']),{
        'frozen_samples_directory':str(Path(load(folder/'report_facts.original.json')['source']['analysis_directory'])/'processed_samples'),
        'gas':{'maximum_supported_gap_seconds':60}})
    compact=bool(load(folder/'unified_report_input.json').get('report_organization'))
    fig,axes=plt.subplots(len(fields),1,figsize=(11,(2.35 if compact else 2.8)*len(fields)),sharex=True,squeeze=False,layout='constrained')
    selected=sorted([r for r in operation['post_stop_records'] if r['status']=='observed_tail_band_return'],
                    key=lambda r:-r['post_stop_peak_ppm'])[:2]
    for ax,field in zip(axes[:,0],fields):
        values,valid=good_samples(frame,field)
        t,y=separated(frame.time,values,dt)
        ax.plot(t,y,color='#8ca79f',lw=.55,label='有效原始读数')
        bins=pd.read_csv(folder/'gas'/(field+'.aligned_5min.csv'));bins.time=pd.to_datetime(bins.time)
        ax.plot(bins.time+pd.Timedelta(seconds=150),bins['mean'],color='#277366',lw=1.1,label='5分钟时间加权均值')
        active=frame.machine_state.eq('advancing')&dt.gt(0)
        for _,g in frame.loc[active].groupby((~active).cumsum()):
            a,b=g.index[0],g.index[-1]
            ax.axvspan(frame.time.iloc[a],frame.time.iloc[b]+pd.Timedelta(seconds=float(dt.iloc[b])),color='#b7d4b0',alpha=.25,zorder=0)
        for r in selected:
            if r['field']==field:
                ax.axvline(pd.Timestamp(r['stop_start']),color='#a16a45',lw=.8,ls='--')
                ax.axvline(pd.Timestamp(r['band_entry_time']),color='#406c99',lw=.8,ls=':')
        ax.set_ylabel(field+'（ppm）');ax.grid(alpha=.15);ax.legend(fontsize=8,loc='upper right')
    axes[-1,0].set_xlim(pd.Timestamp(day),pd.Timestamp(day)+pd.Timedelta(days=1))
    axes[-1,0].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[-1,0].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    target=folder/'figures/gas_operation.png';fig.savefig(target,dpi=200);plt.close(fig)
    save(folder/'gas/figure_context_audit.json',{'fields':fields,'source_sha256':gas['source']['source_sha256'],
        'alignment':'real_PLC_advancing_intervals','raw_interpolation':False,'bin_seconds':300,
        'source_selection':operation['selection_reason'],'figure_sha256':sha(target)})
    return 'figures/gas_operation.png'

def photos(folder,pack):
    from PIL import Image,ImageOps
    candidates=[f for f in pack['facts'] if f['role']=='historical_observation_not_today']
    paths={s['path']:s for f in candidates for s in f['sources'] if Path(s['path']).suffix.lower()=='.json'}
    result=[]
    for path,s in list(paths.items())[:1]:
        source=Path(path)
        if not source.is_file() or sha(source)!=s['sha256']:
            raise ValueError('historical_photo_JSON_not_same_source')
        raw=load(source);entries=raw.get('现场照片',[])
        for n,entry in enumerate(entries,3):
            image=source.parent/f'图{n}.jpg'
            if not image.is_file():continue
            role,url=next(iter(entry.items()))
            destination=folder/'figures'/f'historical_{n}.jpg'
            with Image.open(image) as original:
                dimensions=original.size
                display=ImageOps.exif_transpose(original).convert('RGB')
                display.thumbnail((1800,1800))
                display.save(destination,format='JPEG',quality=90,optimize=True)
            result.append({'file':str(destination.relative_to(folder)),'role':role,'source':str(image),
                'sha256':sha(image),'JSON_source':s,'original_URL':url,'report_available_date':candidates[0]['data']['time']['available_date'],
                'observation_date_confirmed':False,'historical_not_today':True,
                'original_dimensions':dimensions,'display_dimensions':display.size,
                'display_SHA256':sha(destination),'presentation_only_resampling_no_original_edit':True})
    save(folder/'photo_provenance.json',result)
    return result

def mileage_figures(folder):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter,MaxNLocator
    setup()
    e=load(folder/'engineering_evidence.original.json');fs=load(folder/'admissible_forecasts.json')
    ctx=load(folder/'design_context.json');old=load(folder/'evidence_pack.original.json')
    palette={'design':'#9baba6','TSP':'#a5cdbb','HSP':'#aac4dd'}
    for label in ['day','ahead']:
        bounds=e[label+'_range_m'];base=int(bounds[0]//1000)*1000
        segments={}
        if label=='day':
            for a in old['response_analysis']['segment_analyses']:
                for role in ['reference','current']:
                    r=a[role];segments[tuple(r['range_m'])]=r
        count=4 if segments else 1
        compact=bool(load(folder/'unified_report_input.json').get('report_organization'))
        fig,axes=plt.subplots(count,1,figsize=(10.8,5.4 if segments else 1.65 if compact else 2.5),sharex=True,squeeze=False,layout='constrained')
        ax=axes[0,0]
        for n,kind in enumerate(['design','TSP','HSP']):
            rows=ctx['design_rows'] if kind=='design' else [r for r in fs if r['document_type']==kind]
            unique=set()
            for r in rows:
                hit=r[label+'_intersection_m']
                if hit and tuple(hit) not in unique:
                    unique.add(tuple(hit));ax.broken_barh([(hit[0]-base,hit[1]-hit[0])],(n-.22,.44),facecolors=palette[kind],edgecolors='white')
        ax.set_yticks([0,1,2],['设计背景','TSP预测','HSP预测']);ax.set_ylim(-.7,2.75)
        risks=[r for r in e['prior_geology'] if r['fact_type']=='risk' and r.get('point_m') is not None
               and bounds[0]<=r['point_m']<bounds[1]]
        for r in risks:
            n=1 if r['document_type']=='TSP' else 2
            ax.plot([r['point_m']-base],[n],marker='v',color='#a65a38',markersize=6)
            ax.annotate('+'+f"{r['point_m']-base:g}",(r['point_m']-base,n),xytext=(2,9),textcoords='offset points',fontsize=8)
        ax.grid(axis='x',alpha=.15)
        for ax,(field,name,unit,color) in zip(axes[1:,0],FIELDS):
            for (lo,hi),r in sorted(segments.items()):
                value=r['metrics'][field]['median']
                if value is not None:
                    ax.hlines(value,lo-base,hi-base,color=color,lw=2.3)
                    ax.plot([(lo+hi)/2-base],[value],marker='o',color=color,markersize=3)
            ax.set_ylabel(name+'\n'+unit);ax.grid(alpha=.15)
        axes[-1,0].set_xlim(bounds[0]-base,bounds[1]-base)
        axes[-1,0].xaxis.set_major_locator(MaxNLocator(nbins=7,min_n_ticks=3))
        axes[-1,0].xaxis.set_major_formatter(FuncFormatter(lambda x,pos:'+'+f'{x:g}'))
        axes[-1,0].set_xlabel('DyK'+str(base//1000)+'（m）')
        target=folder/'figures'/('geology_'+label+'.png');fig.savefig(target,dpi=210);plt.close(fig)
    save(folder/'mileage_figure_audit.json',{'exact_intersections':True,'risk_points':True,
        'PLC_source':'existing_response_analysis.segment_analyses_reference_and_current',
        'PLC_lines_meaning':'constant_segment_medians_not_interpolated_spatial_samples',
        'integer_chainage_precision_not_increased':True,'forecast_boundaries_are_not_confirmed_geology_boundaries':True})

def source_table(pack):
    lookup={};rows=[]
    for f in pack['facts']:
        for s in f['sources']:
            key=(s['path'],s.get('PDF_page',s.get('page')),s.get('excel_row'))
            if key not in lookup:
                index=len(rows)+1;lookup[key]=index
                locator='PDF第'+str(key[1])+'页' if key[1] else 'Excel第'+str(key[2])+'行' if key[2] else 'PLC/JSON结构化记录'
                rows.append({'index':index,'file':Path(s['path']).name,'locator':locator,'source':s})
    return lookup,rows

def comparative_rows(folder):
    fs=load(folder/'admissible_forecasts.json');ctx=load(folder/'design_context.json')
    records=[]
    for r in ctx['design_rows']:
        if r['day_intersection_m']:
            grade=r['reported_grade'] if r['grade_location_reliable'] else '本段等级边界待核'
            row=['设计背景',span(r['day_intersection_m']),grade,compact(r['original_quote'])]
            if row not in records:records.append(row)
    for r in fs:
        if r['day_intersection_m']:
            grades=r['explicit_grades_from_grade_field']
            if not grades:
                # TSP states the grade in a full conclusion, not a separate labelled field.
                grades=sorted(set(re.findall(r'按([ⅠⅡⅢⅣⅤⅥ]+)级围岩',compact(r['source'].get('quote','')))))
            records.append([r['document_type']+'预测',span(r['day_intersection_m']),
                '、'.join(grades) if len(grades)==1 else '见原文',compact(r['original_quote'])])
    csv_file(folder/'design_forecast_comparison.csv',[
        dict(zip(['role','day_intersection','grade','description'],row)) for row in records])
    return records

def render_report(folder):
    results=[]
    for day in [folder.name]:
        pack=load(folder/'unified_report_input.json');draft=load(folder/'final_narrative.json')
        check=load(folder/'final_narrative.validation.json');activities=load(folder/'activity_facts.json')
        organized=pack.get('report_organization')
        contract=pack.get('presentation_contract',{}).get('2.2')
        if contract:
            actual={g['activity_id'] for g in activities['selected_activities']}
            if actual!=set(contract['fact_ids']) or sha(folder/'activity_facts.json')!=contract['artifact_sha256']:
                raise ValueError('table_support_contract_changed')
            save(folder/'table_support.validation.json',{'passed':True,'fact_ids':sorted(actual),
                'source_sha256':contract['artifact_sha256'],'render_target':'major_activity_table'})
        windows=draw_curves(folder);gas_figure=draw_gas_context(folder);images=photos(folder,pack);mileage_figures(folder)
        lookup,sources=source_table(pack);facts={f['fact_id']:f for f in pack['facts']}
        if organized:
            source_body='<h1>完整来源索引</h1>'+''.join('<p id="src'+str(r['index'])+'">['+str(r['index'])+'] '+
                html.escape(r['file']+'；'+r['locator'])+'</p>' for r in sources)
            source_body+='<h2>口径说明</h2><p>地质资料分别保留设计、预测和历史记录性质；区段对比描述各自实际工况，操作给定与地质致因尚待核对。颜色为原资料标记。气体沿用用户指定ppm研究口径，仪器单位、量程和校准未独立核实；研究关注值不是现场报警或人员暴露结论。设计建议保留适用条件，未记录为实际实施。</p>'
            (folder/'source_index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+CSS+'</style>'+source_body+'</html>',encoding='utf-8')
            photo_body='<h1>历史照片</h1><p>照片为此前资料，不是当天新增揭示。</p>'
            for im in images:
                photo_body+='<figure><img src="'+im['file']+'"><figcaption>'+html.escape(im['role']+'；资料可用日期'+im['report_available_date'])+'</figcaption></figure>'
            (folder/'historical_photos.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+CSS+'</style>'+photo_body+'</html>',encoding='utf-8')
        body=f'<h1>{day} TBM施工日报</h1><div class="sub">伯舒拉岭隧道进口右线</div>'
        if not check['passed']:body+='<div class="badge">本稿未通过全部程序检查，仅供问题审阅</div>'
        rendered=[]
        for s in draft['sections']:
            sid=s['section_id']
            if sid=='2.1':body+='<h2>二、TBM掘进过程与运行情况</h2>'
            if sid=='3.1':body+='<h2>三、地质情况与施工响应分析</h2>'
            body+=('<h2>' if sid=='1' else '<h3>')+TITLES[sid]+('</h2>' if sid=='1' else '</h3>')
            for n,p in enumerate(s['paragraphs']):
                indices=sorted({lookup[(src['path'],src.get('PDF_page',src.get('page')),src.get('excel_row'))]
                    for i in p['fact_ids'] for src in facts[i]['sources']})
                label='<sup>'+','.join('['+str(i)+']' for i in indices)+'</sup>' if indices else ''
                for text in p['text'].split('\n'):
                    if text.strip():body+='<p data-claim="'+sid+':'+str(n)+'">'+html.escape(text)+label+'</p>'
                rendered.append({'section':sid,'paragraph_index':n,'text':p['text'],'fact_ids':p['fact_ids'],'citations':indices})
            if sid=='1' and organized:
                q=organized['coverage'];gap=q['largest_internal_gap']
                from .source_text import duration
                text='数据质量：有效状态支持'+duration(q['supported_seconds'])+'，未覆盖'+duration(q['unsupported_seconds'])+'。'
                if gap:text+='主要采样缺口 '+gap['start_time'][11:19]+'至'+gap['end_time'][11:19]+'（'+duration(gap['seconds'])+'）；'
                text+='日界两端未覆盖合计'+duration(q['day_edge_seconds'])+'。未观测时间单列。'
                body+='<aside class="quality-panel">'+html.escape(text)+'</aside>'
            if sid=='2.1':
                if not organized or any(re.search('P10|P90|速度.*(?:范围|之间)',p['text']) for p in s['paragraphs']):
                    body+='<div class="note">文中典型速度范围取稳定推进样本的P10至P90，即中间约80%的记录。</div>'
                body+='<figure class="daily"><img src="figures/daily_parameters.png"><figcaption>全天推进速度；浅绿背景为已有运行平台，浅黄为选中局部窗口。</figcaption></figure>'
            if sid=='2.2':
                rows=[]
                for g in activities['selected_activities']:
                    r=displayed_activity(g);rows.append([r['time'],r['advancing'],r['progress'],r['rate']])
                body+=table(['主要时段','累计推进时间','平台内进尺','推进效率'],rows)
                body+='<div class="note">推进效率＝对应进尺÷累计推进小时（m/h）。区间使用平台内开累进尺，全天使用整米盾首里程差，两种来源不强制求和相等。</div>'
                body+='<div class="window-grid">'
                for w in windows:body+='<figure><img src="'+w['file']+'"><figcaption>'+html.escape(w['caption'])+'</figcaption></figure>'
                body+='</div><div class="note">局部窗口采用统一百分比轴，显示首末四分之一中位数变化。</div>'
            if sid=='2.3':
                gas=load(folder/'gas/gas_facts.json')
                rows=[[r['field'],f"{r['time_weighted_mean']:.5g}" if r['time_weighted_mean'] is not None else '—',
                       f"{r['maximum']:.5g}" if r['maximum'] is not None else '—',r['time_at_maximum'][11:19] if r['time_at_maximum'] else '—'] for r in gas['statistics']]
                body+=table(['气体字段','有效时间加权均值 ppm','最大值 ppm','峰值时刻'],rows)
                if gas_figure:
                    operation=load(folder/'gas/operation_analysis.json')
                    caption='关注通道原始读数与5分钟均值；浅绿为PLC推进，缺口留白。'
                    if any(r['status']=='observed_tail_band_return' and r['field'] in operation['selected_plot_fields'] for r in operation['post_stop_records']):
                        caption+='虚线/点线为停止起点/观测带进入时间。'
                    if organized:caption+='ppm为研究口径，关注值非现场报警值。'
                    body+='<figure class="gas"><img src="'+gas_figure+'"><figcaption>'+caption+'</figcaption></figure>'
                if organized:
                    if not gas_figure:body+='<div class="note">ppm采用既定研究口径，关注值非现场报警值；监测与换算条件见附录。</div>'
                else:
                    body+='<div class="note">ppm采用用户指定研究口径，未改变原始数值。CO2、SO2以完整15分钟时间加权均值比较，H2S以采样值比较；GBZ 2.1—2019相应数值按20℃、101.3kPa参考条件换算，非现场报警值或人员暴露结论。NO、NO2、CH4仅作统计，仪器单位与校准尚待独立核实。</div>'
            if sid=='3.1':
                rows=comparative_rows(folder)
                comparison=organized['operation']['comparison'] if organized else None
                if comparison:
                    stats=[]
                    for p in [comparison['reference'],comparison['current']]:
                        stats.append([span(p['bounds_m']),p['activity_time_range'][0][11:16]+'至'+p['activity_time_range'][1][11:16]]+
                            [f"{p['metrics'][f]['median']:.2f}" if p['metrics'][f]['status']=='usable_descriptive' else '—'
                             for f in ['advance_speed','thrust','torque','rpm_canonical']])
                    body+=table(['稳定记录里程','所属活动','速度\nmm/min','推力\nkN','扭矩\nkN·m','转速\nr/min'],stats)
                    body+='<div class="note">表内为各自稳定推进记录的中位数；里程是样本覆盖位置，非新增地质边界。完整分布与操作条件检查另存。</div>'
                else:
                    body+=table(['资料性质','当天交集','围岩等级信息'],[r[:3] for r in rows])
                    if not organized:body+='<div class="note">各来源保留原始范围；表中是与当天施工的实际交集，不表示相互独立观测，也不裁定设计或预报哪一个正确。预报中的“前段”仍指原报告的前一预报段，不对应PLC子区段。完整描述与出处见追溯文件。</div>'
                    body+='<figure><img src="figures/geology_day.png"><figcaption>当天里程上的资料覆盖与已有区段统计。PLC横线为区段中位数。</figcaption></figure>'
            if sid=='3.2' and images:
                if organized:
                    body+='<div class="note"><a href="historical_photos.html">历史照片另附</a>，保留原始来源及日期。</div>'
                else:
                    body+='<div class="photos">'
                    for im in images:
                        body+='<figure><img src="'+im['file']+'"><figcaption>'+html.escape(im['role'])+' · 历史资料照片<br>资料可用日期'+im['report_available_date']+'</figcaption></figure>'
                    body+='</div>'
            if sid=='3.3':
                rows=[[r['document_type'],span(r['original_range_m']),span(r['ahead_intersection_m'])] for r in load(folder/'admissible_forecasts.json') if r['ahead_intersection_m']]
                if rows:body+=table(['前方预报来源','原预报范围','前方20m有效交集'],rows)
                risk = load(folder/'source_risk_facts.json')
                risk_rows = []
                for row in risk['records']:
                    if row['scope'] != 'ahead':continue
                    label = ('、'.join(row['hazards']) if row['kind']=='risk_point' else
                             row.get('original_label') if row['kind']=='design_label' else row.get('source_color'))
                    if label:
                        filename=Path(row['source']['path']).name
                        if organized:
                            filename='设计风险摘要' if filename.endswith('.xlsx') else 'HSP原报告' if '声波' in filename else 'TSP原报告' if '地震' in filename else '原资料'
                        risk_rows.append([span(row['intersection_m']), label, filename])
                if risk_rows:body+=table(['前方适用位置','原资料关注内容/颜色','来源'],risk_rows)
                body+='<figure><img src="figures/geology_ahead.png"><figcaption>前方20m的设计背景、TSP/HSP实际覆盖及局部预报关注点。空白仅表示该来源未覆盖。</figcaption></figure>'
        if organized:
            body+='<div class="sources">资料按设计、预测与历史记录标注；区段对比为实际工况描述。<a href="source_index.html">完整来源、口径与追溯记录另附</a>。</div>'
        else:
            body+='<div class="sources"><b>来源索引</b>'
            for r in sources:body+='<div>['+str(r['index'])+'] '+html.escape(r['file']+'；'+r['locator'])+'</div>'
            body+='<p>设计措施仅作有条件参考，原资料颜色不是本日报新设的安全等级。完整事实、规划、生成记录及来源校验值另存于追溯文件。</p></div>'
        css=CSS+('body{line-height:1.52}h2{margin-top:4mm}h3{margin-top:3mm}table{break-inside:avoid}th,td{padding:1.2mm 2mm}figure{margin:2mm 0;page-break-inside:avoid}figcaption{break-before:avoid;page-break-before:avoid}.quality-panel{font-size:9.2pt;border-left:3px solid #597c70;padding:2mm 3mm;margin:2mm 0;background:#f0f4f1;break-inside:avoid}.sources{margin-top:0;padding-top:1mm;border:0}.note{margin:1.4mm 0}' if organized else '')
        path=folder/(day+'.integrated.html');path.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>'+day+' TBM日报</title><style>'+css+'</style></head><body>'+body+'</body></html>',encoding='utf-8')
        pdf=path.with_suffix('.pdf');make_pdf(path,pdf)
        with fitz.open(pdf) as doc:
            text=compact(''.join(p.get_text() for p in doc));missing=[]
            for row in rendered:
                if compact(row['text']) not in text:missing.append(row['section']+':'+str(row['paragraph_index']))
            bounds=[]
            for n,page in enumerate(doc):
                for b in page.get_text('dict')['blocks']:
                    if 'lines' not in b:continue
                    if b['bbox'][0]<15 or b['bbox'][2]>page.rect.width-15 or b['bbox'][3]>page.rect.height-15:
                        bounds.append({'page':n+1,'bbox':b['bbox']})
            preview=folder/'previews';preview.mkdir(exist_ok=True)
            for n,page in enumerate(doc):page.get_pixmap(matrix=fitz.Matrix(1.25,1.25)).save(preview/f'page_{n+1}.png')
            expected_previews={f'page_{n+1}.png' for n in range(len(doc))}
            for previous in preview.glob('page_*.png'):
                if re.fullmatch(r'page_\d+\.png',previous.name) and previous.name not in expected_previews:
                    previous.unlink()
            record={'date':day,'pdf':str(pdf),'pages':len(doc),'model_paragraphs_missing_from_pdf':missing,
                'out_of_page_text_blocks':bounds,'image_count':sum(len(p.get_images()) for p in doc),
                'narrative_SHA256':sha(folder/'final_narrative.json'),'HTML_SHA256':sha(path),'PDF_SHA256':sha(pdf),
                'passed':not missing and not bounds and check['passed'],'semantic_status':'REVIEW',
                'PDF_text_same_as_checked_model_text':not missing}
        save(folder/'rendered_paragraphs.json',rendered);save(folder/'sources.display.json',sources)
        save(folder/'artifact.validation.json',record);results.append(record)
        print(json.dumps(record,ensure_ascii=False),flush=True)
    return results[0]
