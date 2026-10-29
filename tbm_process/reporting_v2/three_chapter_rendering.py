"""Three-chapter template and additional real-data figures using the shared renderer."""
from __future__ import annotations

import html

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from tbm_process.evidence_graph.three_chapter import TITLES
from .facts import duration, write_json
from .response_features import NAMES
from .rendering import engineering_styles, render_figures


def render_three_figures(package, samples, graph, pack, output):
    from tbm_process.evidence_graph.report_reliability import report_graph_view
    graph,_=report_graph_view(graph)
    evidence = render_figures(package, samples, output, basic_only=True)
    folder = output / 'figures'
    plot_data = {}
    focus = [f['writer_data'] for f in pack['facts'] if f.get('nature') == 'construction_phenomenon']
    if focus:
        fields = [('advance_speed', '推进速度', 'mm/min', '#28779b'), ('thrust', '总推力', 'kN', '#81629a'),
                  ('torque', '刀盘扭矩', 'kN·m', '#bd6846'), ('rpm_canonical', '刀盘实际转速', 'r/min', '#408779')]
        fig, axes = plt.subplots(4, 1, figsize=(10.5, 5.5), sharex=True, constrained_layout=True)
        gap = samples.time.diff().dt.total_seconds().gt(package['config']['analysis']['hard_gap_seconds'])
        highlight_colors = ['#e7b65b', '#c8735d']
        for ax, (column, name, unit, color) in zip(axes, fields):
            for p in package['platforms']:
                ax.axvspan(pd.Timestamp(p['start_time']), pd.Timestamp(p['end_time']), color='#dce7e3', alpha=.32, lw=0, zorder=0)
            for index, window in enumerate(focus):
                ax.axvspan(pd.Timestamp(window['start_time']), pd.Timestamp(window['end_time']),
                           color=highlight_colors[index % len(highlight_colors)], alpha=.28, lw=0, zorder=1)
            values = pd.to_numeric(samples[column], errors='coerce').where(lambda s: s.ge(0))
            if column == 'advance_speed':
                values = values.where(samples.advance_speed_quality.eq('usable'))
            if column == 'rpm_canonical':
                values = values.where(samples.rpm_quality.eq('usable_provisional_mapping'))
            ax.plot(samples.time, values.where(~gap), color=color, lw=.65, zorder=2)
            ax.set_ylabel(f'{name}\n{unit}', fontsize=8)
            ax.grid(color='#dde3e4', lw=.45)
            ax.set_xlim(pd.Timestamp(package['date']), pd.Timestamp(package['date']) + pd.Timedelta(days=1))
        axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        labels = '；'.join(chr(65 + i) + ' ' + row['start_time'][11:16] + '至' + row['end_time'][11:16] for i, row in enumerate(focus))
        axes[0].set_title('当日主要PLC参数与代表性局部窗口（橙色/红色；' + labels + '）', fontsize=10)
        fig.savefig(folder / 'parameters_focus.png', dpi=220, facecolor='white')
        plt.close(fig)
        plot_data['representative_windows'] = focus
    comparisons = [r for r in pack['segment_comparisons'] if r['usable']]
    if comparisons:
        fig, axes = plt.subplots(len(comparisons), 3, figsize=(10.5, 2.2 * len(comparisons)), squeeze=False, constrained_layout=True)
        comparison_base = int(min(min(min(item['reference_range_m']), min(item['range_m'])) for item in comparisons)) // 1000 * 1000

        def short_range(bounds):
            return f'{bounds[0] - comparison_base:g}–{bounds[1] - comparison_base:g}'

        for row, item in enumerate(comparisons):
            for ax, k, label in zip(axes[row], ['advance_speed', 'thrust', 'torque'], ['推进速度', '总推力', '刀盘扭矩']):
                a, b = item['reference_metrics'][k], item['current_metrics'][k]
                ax.bar([0, 1], [a['median'], b['median']], color=['#7896aa', '#598d7b'], width=.55)
                ax.errorbar([0, 1], [a['median'], b['median']],
                            yerr=[[a['median'] - a['p10'], b['median'] - b['p10']], [a['p90'] - a['median'], b['p90'] - b['median']]], fmt='none', color='#333', capsize=3)
                ax.set_xticks([0, 1], [short_range(item['reference_range_m']), short_range(item['range_m'])], fontsize=8)
                ax.set_title(label + ' / ' + a['unit'], fontsize=10)
                ax.tick_params(axis='y', labelsize=8)
        fig.suptitle(f'区段中位数与P10—P90（DyK{comparison_base // 1000}+相对米数；描述性对比）', fontsize=10)
        fig.savefig(folder / 'segment_comparison.png', dpi=220, facecolor='white'); plt.close(fig)
        plot_data['segment_comparison'] = comparisons
    context = graph.metadata['daily_contexts'][package['date']]
    facts = [n['data'] for n in graph.typed('GeologyFact') if n['data']['fact_type'] == 'rock_grade']
    def bands(ax, bounds, rows, *, observation=False):
        base = int(bounds[0] // 1000) * 1000
        types = ['TSP', 'HSP', 'SKETCH'] if observation else ['TSP', 'HSP']
        colors = {'TSP': '#7794ad', 'HSP': '#b09d63', 'SKETCH': '#6e9a81'}
        drawn = []
        for y, kind in enumerate(types):
            ax.broken_barh([(bounds[0] - base, bounds[1] - bounds[0])], (y - .18, .36), color='#edf0f0')
            for d in rows:
                doc = graph.nodes[d['document_id']]['data']
                if doc['document_type'] != kind or d['start_m'] is None or d['end_m'] is None:
                    continue
                if kind == 'SKETCH' and d['information_role'] != 'observed':
                    continue
                lo, hi = max(bounds[0], d['start_m']), min(bounds[1], d['end_m'])
                if lo >= hi:
                    continue
                ax.broken_barh([(lo - base, hi - lo)], (y - .18, .36), color=colors[kind], edgecolor='white', lw=.7)
                if hi - lo >= (bounds[1] - bounds[0]) / 18:
                    ax.text((lo + hi) / 2 - base, y, str(d['value']), ha='center', va='center', fontsize=8)
                drawn.append({'document_id': d['document_id'], 'fact_id': d['fact_id'], 'original_range_m': [d['start_m'], d['end_m']], 'display_range_m': [lo, hi], 'grade': d['value']})
        ax.set_yticks(range(len(types)), types)
        ax.set_ylim(-.5, len(types) - .5)
        ax.set_xlim(bounds[0] - base, bounds[1] - base)
        ax.set_xlabel(f'DyK{base // 1000}+ 相对米数；浅灰表示该来源未覆盖')
        ax.grid(axis='x', color='#ddd', lw=.5)
        return base, drawn
    bounds = context['day_range_m']
    current_geo = [d for d in facts if d['information_role'] == 'forecast' and d['start_m'] is not None and d['start_m'] < bounds[1] and d['end_m'] > bounds[0]]
    partitions = {}
    for row in comparisons:
        partitions[tuple(row['reference_range_m'])] = row['reference_metrics']
        partitions[tuple(row['range_m'])] = row['current_metrics']
    if current_geo and bounds[0] < bounds[1] and partitions:
        fig, axes = plt.subplots(4, 1, figsize=(10.5, 5.7), constrained_layout=True)
        base, drawn = bands(axes[0], bounds, current_geo)
        rows = []
        for ax, k, label, unit in zip(axes[1:], ['advance_speed', 'thrust', 'torque'], ['推进速度', '总推力', '刀盘扭矩'], ['mm/min', 'kN', 'kN·m']):
            for (lo, hi), metrics in sorted(partitions.items()):
                value = metrics[k]['median']
                ax.hlines(value, lo - base, hi - base, color='#467d92', lw=4, alpha=.78)
                ax.scatter((lo + hi) / 2 - base, value, s=20, color='#315e70', zorder=3)
                ax.annotate(f'{value:.1f}', ((lo + hi) / 2 - base, value), xytext=(0, 4), textcoords='offset points',
                            ha='center', fontsize=7)
                rows.append({'range_start_m': lo, 'range_end_m': hi, 'metric': k, 'median': value, 'unit': unit,
                             'display_geometry': 'horizontal_interval_summary_not_pointwise_continuous_curve'})
            ax.set_xlim(bounds[0] - base, bounds[1] - base); ax.set_ylabel(label + '\n' + unit, fontsize=8)
            ax.grid(color='#ddd', lw=.4)
        axes[0].set_title('地质预测范围与稳定推进分区统计（按区间汇总）', fontsize=10)
        axes[-1].set_xlabel(f'DyK{base // 1000}+ 相对米数；横线为相应区段统计中位数')
        fig.savefig(folder / 'geology_response_intervals.png', dpi=220, facecolor='white'); plt.close(fig)
        pd.DataFrame(rows).to_csv(folder / 'geology_response_intervals.csv', index=False)
        plot_data['geology_response_bands'] = drawn
        plot_data['geology_response_interval_summaries'] = rows
    ahead = context['ahead_range_m']
    ahead_rows = [d for d in facts if d['information_role'] == 'forecast' and d['start_m'] is not None and ahead and d['start_m'] < ahead[1] and d['end_m'] > ahead[0]]
    if ahead_rows:
        fig, ax = plt.subplots(figsize=(10.5, 1.9), constrained_layout=True)
        base, drawn = bands(ax, ahead, ahead_rows)
        risks = [n['data'] for n in graph.typed('GeologyFact')
                 if n['data']['fact_type'] == 'risk' and n['data']['information_role'] == 'forecast'
                 and n['data'].get('point_m') is not None and min(ahead) <= n['data']['point_m'] <= max(ahead)]
        source_y = {'TSP': 0, 'HSP': 1}
        risk_points = []
        for risk in risks:
            doc = graph.nodes[risk['document_id']]['data']
            kind = doc['document_type']
            if kind not in source_y:
                continue
            x, y = risk['point_m'] - base, source_y[kind]
            ax.scatter([x], [y], marker='v', s=42, color='#a94e42', edgecolor='white', linewidth=.5, zorder=5)
            ax.annotate(f"+{risk['point_m'] - base:g} {risk['value']}", (x, y), xytext=(0, 10),
                        textcoords='offset points', ha='center', fontsize=7, color='#7f342d')
            risk_points.append({'fact_id': risk['fact_id'], 'source_type': kind,
                                'point_m': risk['point_m'], 'risk': risk['value']})
        ax.set_title('盾首参考前方20m预报等级覆盖与局部风险点', fontsize=10)
        fig.savefig(folder / 'ahead.png', dpi=220, facecolor='white'); plt.close(fig)
        plot_data['ahead_bands'] = drawn
        plot_data['ahead_risk_points'] = risk_points
    write_json(output / 'three_figures.data.json', plot_data)
    evidence['additional_figures'] = sorted(p.name for p in folder.glob('*.png'))
    write_json(output / 'figures.provenance.json', evidence)
    return evidence


def analysis_table(section, pack):
    """Render verified analysis records, preserving machine and source scope roles."""
    analysis = pack.get('response_analysis')
    if not analysis:
        return '', []
    rows = []
    if section == '2.1':
        headers = ['局部窗口', '样本/支持时长', '首末四分之一中位数变化']
        for window in analysis['selected_time_windows']:
            changes = '；'.join(f"{NAMES[k]}{window['changes'][k]['initial_median']:.2f}→{window['changes'][k]['final_median']:.2f}{window['changes'][k]['unit']}"
                               for k in window['pattern']['trend_fields'])
            rows.append([window['start_time'][11:]+'至'+window['end_time'][11:],
                         f"{window['sample_count']}条 / {duration(window['supported_duration_seconds'])}", changes])
    elif section == '3.1':
        headers = ['PLC名义里程', '样本/支持时长/转速中位数', 'TSP原始区段及属性', 'HSP原始区段及属性']
        profiles = {u[role]['response_id']: u[role] for u in analysis['segment_analyses'] for role in ('reference','current')}
        for p in sorted(profiles.values(), key=lambda p: p['range_m']):
            descriptions = {}
            for kind in ('TSP', 'HSP'):
                entries = []
                for geo in p['geology']:
                    if geo['source_type'] != kind:
                        continue
                    attrs = geo['literal_attributes']
                    texts = [v['text'] for field in ('structure','lithology','integrity','water') for v in attrs[field]
                             if v['applies_to_intersection']]
                    entries.append(geo['original_range_display']+'：'+'；'.join(dict.fromkeys(texts)))
                descriptions[kind] = ' / '.join(entries) or '无合格覆盖'
            rpm = p['metrics']['rpm_canonical']['median']
            rows.append([p['range_display'], f"{p['sample_count']}条 / {duration(p['time_scope']['supported_duration_seconds'])} / "
                         + (f'{rpm:.2f}r/min' if rpm is not None else '转速不足'), descriptions['TSP'], descriptions['HSP']])
    else:
        return '', []
    if not rows:
        return '', []
    body = '<table><thead><tr>'+''.join('<th>'+html.escape(x)+'</th>' for x in headers)+'</tr></thead><tbody>'
    body += ''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
    markdown = ['| '+' | '.join(headers)+' |', '| '+' | '.join('---' for _ in headers)+' |']
    markdown += ['| '+' | '.join(str(x).replace('|','/') for x in row)+' |' for row in rows]
    if section == '3.1':
        note = '表内转速沿用暂可用映射；尚未核实控制给定值的严格一致性。重复出现的相同原始地质区段属于同一份来源，预报分段边界仅作为资料范围。'
    else:
        note = '支持时长为合格记录各自的有效采样间隔累计；窗口时间为记录首末跨度。'
    return body+'<p class="note">'+html.escape(note)+'</p>', markdown+['', note, '']


def render_three_html(package, narrative, context, output, provenance):
    pack, plan = context['pack'], context['plan']
    mode = 'LLM辅助生成｜程序一致性检查通过｜研究审核稿' if provenance.get('actual_llm_report') else '确定性事实预览｜未调用LLM'
    if provenance.get('actual_llm_report') and not provenance.get('program_checks_passed'):
        mode = '真实LLM研究失败稿：未通过事实检查，不可作为正式工程日报'
    elif provenance.get('actual_llm_report') and provenance.get('quality_status') == 'REVIEW':
        mode = '程序一致性检查通过｜工程语义待人工审阅｜研究候选稿'
    paragraph_map = {p['paragraph_id']: p for c in plan['chapters'] for p in c.get('paragraphs', [])}
    labels = {'2.1': '2.1 主要掘进过程', '2.2': '2.2 主要参数及性能表现', '3.1': '3.1 相关地质条件与当日机器响应',
              '3.2': '3.2 现场记录与既有预报对照', '3.3': '3.3 前方地质预报'}
    figures = {'2.1': [('parameters_focus.png', '当日实际PLC参数；浅色背景为既有运行区间，橙/红色为正文采用的代表性局部窗口，缺失不插值')],
               '2.2': [],
               '3.1': [('geology_response_intervals.png', '地质范围与PLC分区统计，横线对应区段汇总')],
               '3.2': [],
               '3.3': [('ahead.png', '前方20m内各来源预测等级覆盖；红色三角为原报告局部风险点，具体地质描述见正文')]}
    body = f"<h1>{package['date']} TBM施工日报</h1><div class='mode'>{html.escape(mode)}</div>"
    markdown = [f"# {package['date']} TBM施工日报", '', mode, '']
    for ch in narrative['chapters']:
        cid = ch['chapter_id']; title = ['一、', '二、', '三、'][cid - 1] + TITLES[cid - 1]
        body += '<h2>' + title + '</h2>'; markdown += ['## ' + title, '']
        sections = ['1'] if cid == 1 else ['2.1', '2.2'] if cid == 2 else ['3.1', '3.2', '3.3']
        for section in sections:
            if section != '1':
                body += '<h3>' + labels[section] + '</h3>'; markdown += ['### ' + labels[section], '']
            claims = [c for c in ch['claims'] if paragraph_map.get(c.get('paragraph_id'), {}).get('section', str(cid)) == section]
            if not claims:
                text = '现有可用资料不足，未补写具体结论。'
                body += '<p>' + text + '</p>'; markdown += [text, '']
            for claim in claims:
                body += '<p>' + html.escape(claim['text']) + '</p>'; markdown += [claim['text'], '']
            table, table_markdown = analysis_table(section, pack)
            body += table
            markdown += table_markdown
            for filename, caption in figures.get(section, []):
                image = output / 'figures' / filename
                if image.is_file():
                    body += f"<figure><img src='{image.as_uri()}'><figcaption>{html.escape(caption)}</figcaption></figure>"
                    markdown += [f'![{caption}](figures/{filename})', '']
    used_fact_ids = {fact_id for chapter in narrative['chapters'] for claim in chapter['claims'] for fact_id in claim.get('fact_ids', [])}
    sources = {}
    def collect_sources(value):
        if isinstance(value, dict):
            if value.get('path'):
                sources[(value['path'], value.get('page'), value.get('source_pointer'))] = value
            for nested in value.values():
                collect_sources(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_sources(nested)
    for f in pack['facts']:
        if f['fact_id'] not in used_fact_ids:
            continue
        collect_sources(f.get('source', {}))
    source_note = (
        '统计口径：推进与非推进时间按有效PLC记录累计；地质资料与机器响应按盾首名义里程关联，'
        '并保留施工前预测或历史记录属性。正文引用的文件、页码和字段位置见独立追溯清单。'
    )
    body += "<div class='sources'><h3>统计口径与资料来源</h3><p>" + html.escape(source_note) + '</p></div>'
    markdown += ['## 统计口径与资料来源', '', source_note, '']
    path = output / (package['date'] + '.three_chapter.html')
    path.write_text("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>TBM施工日报</title><style>" + engineering_styles() + 'p{break-inside:avoid;orphans:2;widows:2}</style></head><body>' + body + '</body></html>', encoding='utf-8')
    (output / (package['date'] + '.three_chapter.md')).write_text('\n'.join(markdown), encoding='utf-8')
    write_json(output / 'report_source_index.json', list(sources.values()))
    return path
