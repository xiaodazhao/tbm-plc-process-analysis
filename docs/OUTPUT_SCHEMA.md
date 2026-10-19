# 当前输出结构

所有生成结果位于`outputs/`，不提交Git。

## `outputs/base_analysis/`

由`run_analysis.py`生成，是当前平台优先方法的质量受控输入层：

- `processed_samples/<date>.csv`：逐点原始来源、质量标记、machine_state和基础阶段字段；
- `structured_full_days/<date>.json`：基础完整事实；
- `data_quality_events.csv`：数据质量事件；
- `method_manifest.json`：配置和源码校验信息。

基础结果是当前主线的中间数据，不是并行的旧报告方法。

## `outputs/current_analysis/`

由`scripts/run_current_analysis.py`生成：

- `platforms.csv`：全部算法运行平台；
- `phases.csv`：全部startup、rising、stable、declining阶段；
- `processed_samples/<date>.csv`：平台和阶段逐点归属；
- `analysis_scopes.csv`：阶段及未识别动态分析范围；
- `semantic_segments.csv`：连续趋势与波动区间；
- `coupled_events.csv`：多参数共同变化；
- `structured_full_days/<date>.json`：完整未筛选事实；
- `structured_days/<date>.json`：精简结构化事实；
- `construction_semantics/<date>.json`：工程语义、筛选和追溯；
- `quantitative/<date>.quantitative.json`：日报级定量事实；
- `llm_inputs/<date>.llm_input.formal.json`：正式模式输入；
- `llm_inputs/<date>.llm_input.research.json`：研究定量预览输入；
- `validation/<date>.json`：日级内部一致性检查。

算法平台不是现场确认的真实掘进循环。完整事实与摘要事实的数量必须通过`selection_metadata`区分。

## `outputs/llm_experiment/`

由`scripts/run_llm_report_experiment.py`生成：

- 类型化事实池和内容计划；
- A-E草稿及Markdown日报；
- 请求、原始响应和运行元数据；
- 当前事实验证器的逐句核验；
- E组局部修订日志；
- 汇总指标和人工复核表。

规则核验通过率不是工程事实准确率。

## `outputs/daily_reports/<date>/`

由`scripts/build_daily_report_front_sections.py`读取现有平台分析结果生成，不重新计算运行平台或四阶段：

- `<date>.daily_report_facts.json`：前两章的完整数值事实；
- `<date>.platform_descriptions.json`：确定性规则生成的表格短句及证据；
- `<date>.report_narrative.json`：确定性回退文本或通过核验的LLM正文；
- `<date>.platform_details.csv`：全部运行平台明细；
- `figures/`：全天时间轴和三参数曲线；
- `<date>.TBM施工日报_前两章.html/pdf`：最终可读报告；
- `<date>.validation.json`：数值、平台覆盖和渲染一致性检查；
- `report_narrative_generation/`：LLM请求、原始响应和调用元数据。

表格短句不调用LLM。LLM只在`--report-text-mode llm`时负责组织两章正文，数值、表格和图形始终由确定性程序产生。

## `outputs/final_reports/`

由`scripts/run_final_reports.py`读取`outputs/current_analysis/`与外部地质 V2 候选结果生成，不重新计算 PLC 或地质内容计划：

- `days/<date>/<date>.report_facts.json`：第一、二章确定性事实；
- `days/<date>/<date>.geology_source_table.json`：选中地质事实和来源；
- `days/<date>/<date>.geology_axis_model.json`：完整里程、预测分段、风险和历史观察图元；
- `days/<date>/<date>.geology_presentation.json`：第三章压缩表达与内容单元映射；
- `days/<date>/figures/`：时间轴、PLC参数曲线和地质里程轴；
- `days/<date>/<date>.TBM施工日报_最终版.html/pdf`：三章日报；
- `days/<date>/<date>.validation.json`：确定性事实一致性检查；
- `days/<date>/<date>.artifact_audit.json`：PDF及逐页预览成品审计；
- `audit/final_report_audit.csv/json`：批次汇总。

地质 V2 证据目录和原始 PLC 数据不提交 Git，须在新电脑通过命令行参数提供。自动审计通过不等于工程地质结论准确率。

## 追溯字段

平台、阶段、语义段和事件至少保留：

- `date`；
- `start_time`、`end_time`；
- `source_file`；
- `source_row_start`、`source_row_end`；
- `interval_id`或`platform_id`；
- `phase_id`。

这些字段可回到当前逐点结果，再回到原始PLC文件。
