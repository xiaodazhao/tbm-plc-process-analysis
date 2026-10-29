# TBM多源证据图 V1

> 本文件是组件早期独立验证说明。当前图组件已被整篇日报`engineering_reporting.pipeline.prepare`复用；不是另一条默认生成主线。完整链路与真实下游消费方式见`docs/RESEARCH_SUMMARY_FROZEN.md`。

这是独立增量组件，不修改PLC清洗、平台、四阶段、性能公式或原六章入口。只把可对应里程的机器摘要、地质来源与可核查对照组织成关系。没有Neo4j、向量库、Agent、故障诊断或付费调用。

## 真实入口

```bash
python scripts/run_evidence_graph_report.py --dates 2024-04-26 2023-11-18 2024-04-25 2024-03-15 --output-root /path/to/new/output
python -m pytest tests/test_evidence_graph.py -q
```

输入路径可用`--analysis-dir`、`--catalog`、`--registry`、`--source-root`、`--sketch-root`、`--config`显式指定。每次新建时间戳目录，拒绝覆盖；SQLite也拒绝覆盖。

## 数据源约定

- PLC复用`experiments/plc_mapping_repair_v1/replay/after`逐点数据、平台表和阶段表。
- TSP/HSP复用已有结构化目录，按SHA索引定位原PDF，核对日期证据页。
- 素描只用`伯舒拉岭数据/右线地质资料/伯舒拉岭右线洞身素描1、2/**/数据.json`。旧便携目录的167份素描不作为本组件输入。
- 本轮目录有244份JSON文件，按内容SHA为214份唯一记录；完全相同的30份副本只建一次文档节点，全部原路径保留，不删除原文件，不合并内容不同的记录。
- 2026-10-27用户明确确认：本批JSON图片URL路径日期就是报告可用日期。程序保存`availability_basis`及日精度，不把它改称原报告签发日、实际观察日或具体时刻。若同一JSON出现不同日期则待核实；同日缺具体时刻不用于解释此前运行。
- 设计仅接收既有V2接口的可靠分段及原文件/SHA/页码/日期原文依据；本轮没有独立、核定的真实设计分段。素描中转述的设计分级仍是素描属性，不自动变成独立设计文档。

## 模块

- `store.py`：六种节点、九种关系、端点约束、索引遍历、JSON/SQLite读写及来源追溯。
- `sketches.py`：当前结构化素描适配，保留原JSON、明确分句里程、日期依据、图片SHA；不修改原文件。
- `catalog.py`：已有字段转事实、PDF/JSON来源定位、日期审计和查询时间准入。事实既有原文也有保守规则属性；不是新的全文重提取。
- `analysis.py`：复用V2区间统计及连续窗口；保存比较基准和原始行集合；组织多源差异、资料对照及机器响应。
- `queries.py`：A当日地质、B区段PLC、C素描、D前方、E报告信息；普通字段检索和图检索共用事实池及时间门禁。
- `claims.py`：确定性候选陈述及支持ID、选择/省略原因；相同来源、等级、相邻范围才允许合并。
- `validation.py`：不只检查路径存在；重新核对几何、字段角色、支持成员、基准时间、预报/观察资料时间。
- `reporting.py`：把实际图查询陈述送入V2六章事实、计划、正文、最终PDF；插入当前素描原图及证据附录。
- `visualization.py`：实际陈述追溯子图的PNG/SVG；不是分析的替代。

## 存储及核验

SQLite含`metadata/nodes/edges`三表，按节点类型及边端点建立索引。节点属性保存在JSON字段内，无逐采样点节点。单个PLCResponse保留摘要、连续窗口及精确源行列表，原采样数不等于节点数。

关系白名单：`HAS_FACT/COVERS/OBSERVED_AT/MEASURED_OVER/OVERLAPS_WITH/COMPARES/DIFFERS_FROM/DERIVED_FROM/SUPPORTED_BY`。没有`CAUSES/PROVES/CONFIRMS`。同一空间交集只说明名义对应；掌子面偏移未知，禁止米级断层定位推断。

`PROGRAM_SUPPORTED`只表示此确定性陈述通过类型、来源、时间和计算链一致性检查；不代表独立工程事实准确率。改写过的文字不会因ID或路径存在自动通过：新增无支持数值、无依据诊断为FAIL，其他未证明语义为REVIEW。JSON指针与PDF页码明确区分，缺页码不补造。

LLM写作接口为`writing_facts.json`、检查过的`content_plan.json`和完整`*.request.NOT_SENT.json`，可与现有JSON模型调用封装连接。本轮程序无付费调用开关，不自动读取密钥；不能把离线成品称为真实模型生成，也不能声称本轮证明图谱提升了LLM效果。

完整设计、时间政策、复现范围及比较限制见`docs/EVIDENCE_GRAPH_JOINT_REPORTING.md`。
