# 当前代码与执行路径

## 共用PLC分析层

`ingestion -> quality -> process -> dynamics -> facts -> orchestration -> outputs`。
`run_analysis.py`、`scripts/run_current_analysis.py`负责从原始PLC重建分析。
`reporting.front_sections`只读已有逐点数据和平台/阶段表，提供报告事实。
本次清理没有修改这一层的代码、阈值、字段映射或已处理结果。

## 当前证据库应用

入口 `scripts/build_geology_store.py`：

1. `geology_store.database.import_catalog`复用`evidence_graph.catalog/sketches`，读取TSP/HSP提取结果和桌面最新素描JSON，保存SQLite文档、事实、日期、里程及来源。
2. `geology_store.spatial/time_policy`执行区间交并和资料时间准入；`applications`构建当日范围、当日新增素描、前方预报三类查询结果。
3. `reporting_v2.facts.build_facts`复用修复后的PLC逐点数据和平台表，构建施工统计及性能事实，不重新分段。
4. `geology_store.reporting`复用`reporting_v2.narrative`的离线规划、离线文字和检查，再由`reporting_v2.rendering`绘图、输出HTML/PDF。
5. `query_geology_store.py`和`serve_geology_store.py`读取同一数据库；查询不需要再次解析原件。

这条新入口当前是**离线应用验证**，不调用真实LLM。脚本还执行全量来源和覆盖审计，因此只指定一天也会导入完整库，但只生成指定日期的日报。

## 仍在使用的独立入口

- `run_evidence_graph_report.py`：证据图、来源绑定和离线日报的独立验证路径。证据库复用其部分适配组件，但没有把所有图推理步骤接入数据库日报。
- `run_engineering_report_v2.py`：六章工程日报；默认离线，可显式调用LLM。其原始目录型地质输入与新数据库的当前素描输入不能混为一谈。
- `run_integrated_report.py`：保留的三章联合事实、LLM全文规划和生成路径。`final_reporting`里的部分工具仍被最新模块调用，不能按名称删除。

这些入口的研究任务和输入版本不同。真实模型输出、离线预览、最终成品应分别追溯，不能仅因共用部分函数就宣称已经完成统一真实模型端到端验证。

## 保留数据

全量处理结果仍位于 `experiments/plc_mapping_repair_v1/replay/after`；既有日报事实与地质匹配的`after`分支继续保留。当前代码显式引用这些路径，本次没有迁移。
历史修复前数据、对照明细、重复源码快照和独立探索已从工作目录清走，可在完整清理备份中恢复。
原始PLC、PDF、素描JSON、设计资料在项目外，不属于本次删除范围。

调试入口和断点见[DEBUGGING.md](DEBUGGING.md)，清理边界见[CLEANUP_2026-10-27.md](CLEANUP_2026-10-27.md)。
