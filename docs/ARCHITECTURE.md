# 当前代码与执行路径

版本：TBM-Daily-20261029-freeze1。

## 唯一默认报告路径

`scripts/run_integrated_report.py → engineering_reporting.cli.main → pipeline.run`。

准备阶段为`pipeline.prepare`：读取已修复PLC与既有阶段，读取SQLite并刷新当前素描，建立证据图与响应分析，组织活动进尺、气体、设计参考和原来源风险，执行进尺调查与覆盖对账，锁定统一事实及来源SHA。

生成阶段为`pipeline.generate`：整篇规划、提纲检查、整篇写作、常规编辑、正文规则检查及必要时一次定向修订。
渲染阶段为`engineering_reporting.rendering.render_report`：同一正文、真实图表、HTML/PDF与成品一致性核对。

没有成品后追加气体的第二轮日报流程。

## 复用组件

- `reporting_v2`：PLC事实、响应特征、活动进尺和底层绘图。
- `evidence_graph`：文档/事实/区段/响应/比较及来源约束，编译为报告证据；模型不直接执行图算法。
- `geology_store`：SQLite查询、时间与空间准入。
- `final_reporting`：共享模型接口及历史兼容组件。
- `ingestion → quality → process → dynamics`：既有PLC处理和分段，不在日报运行时重算全329天。

`run_analysis.py`及`run_current_analysis.py`负责重建分析，不是日常日报入口。
`build_geology_store.py`负责建库，`query/serve_geology_store.py`负责查询，不是新的整篇生成主线。

## 兼容入口

`run_engineering_report_v2.py`、`run_evidence_graph_report.py`、`run_three_chapter_report.py`和显式`--profile portable`供历史回放/回归保留。
它们的模型、章节及输入策略不能当作冻结版默认行为。保留兼容源码不等于同时维护多条活动研究主线。

## 数据和审计

当前329天结果仍在`experiments/plc_mapping_repair_v1/replay/after`及相关`impact/*/after`；本次不迁移这些被调用路径。
原始资料和当前素描在桌面`伯舒拉岭数据`；地质SQLite及来源注册索引仍是明确的外部依赖。
`local_data/engineering_context`及本地嵌入模型不是可删除的无关缓存。

清理后的唯一接受成果为`outputs/report_quality_release_20261029_r2`。
旧实验和中间输出移至`/Users/zhaoxiaoda/TBM历史归档/20261029_冻结前`。
完整迁移清单、源码身份及验证见`release/20261029`。

方法细节见[详细研究摘要](RESEARCH_SUMMARY_FROZEN.md)，断点思路见[调试说明](DEBUGGING.md)。
