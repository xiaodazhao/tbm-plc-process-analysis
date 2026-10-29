# 整篇工程日报集成说明

## 版本和备份

2026-10-29集成前源码快照为Git提交`1aaa6c6`，已推送到
`origin/codex/three-chapter-evidence-20261027`。原始PLC、密钥及大型本机结果未加入该提交。
本轮迁移的是`13`中已被审阅的计算与表达能力，不是另建PLC算法。
来源函数与原文件SHA见`local_data/engineering_context/migration_manifest.json`。
`scripts/migrate_engineering_context.py`仅为一次性迁移工具，不是日报运行依赖。

## 实际顺序

1. `engineering_reporting.pipeline.prepare`调用既有`build_facts`读取修复数据及平台/阶段表，复用参数计算。
2. 从现有地质SQLite导出目录，`load_current_catalog`读取最新素描JSON及可用时间。
3. 复用`add_daily_analysis/add_source_responses/build_response_features/build_pack`生成图、响应及代表性现象，并复核样本支持。
4. `build_activity_facts`计算实际推进时长、平台内开累进尺及折算推进率，不把跨度当推进时长，不把平台进尺强行加成日进尺。
5. `gas.build_gas`直接读取标准化PLC气体。使用映射、数值及时间合格记录，保留零值，不插值硬缺口，末条不默认支持20秒，另存缺失/排除数。
6. `matching.match_day`沿用时间准入和实际里程交集；`assembly`保留设计、预测、历史观察性质及原范围。
7. 本地BGE向量和SQLite FTS通过既有RRF检索设计条文；复核PDF SHA、同页同栏条款、条件及无关位置，不把相似度当适用概率。
8. `risks.build_risks`读取已有原PDF风险/颜色快照，再按当前日期、SHA、正长度相交或点的左闭右开归属准入。Excel是用户确认的施工前设计摘要，不是台账。
9. `unified_report_input.json`汇总所有事实，`preflight.json`锁定SHA。此时尚未调用LLM。
10. 同一事实池进入整篇Planner，检查ID、章节、必选覆盖及省略原因，最多一次提纲定向修复。
11. Generator整篇写作，Editor使用同一事实及支持ID编辑整篇。编辑在主流程内，不是在成品后追加章节。
12. `validate_text`检查正文；必要时最多一次定向修订，保留前后及原因。仍失败则停止，不伪装成功。
13. `render_report`渲染核验的同一正文，用真实数据绘图、插入来源照片、生成表格及PDF。成品文本再逐段对照。

## 事实与模型

每条事实含`fact_id/section/canonical_text/data/sources/required/role`。
模型消费精简事实、支持ID、写作约束与通过检查的规划，不计算进尺、阈值或参数百分比。
完整统计、行号、原路径、页码、质量说明保存在事实和图追溯文件。
3.3接收前方预报、关注点、来源颜色与条件措施；2.3接收气体统计、关注时段和通风/监测参考。
不读取历史PDF作为新日报内容，不依赖`13`旧成稿或实验入口。

## 输入与环境

- 分析：项目内`experiments/plc_mapping_repair_v1/replay/after`。
- 地质库：桌面`TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite`。
- 来源根：桌面`伯舒拉岭数据`。
- 地质索引：桌面`TBM9_地质证据与PLC对照_v1_20261025/地质证据包.json`。
- 本机参考资源：`local_data/engineering_context`，配置：`configs/engineering_context.json`。

均可通过CLI参数覆盖。复制设计PDF时核对SHA，只重定位副本索引路径，不改原文。
向量模型缓存于项目`.cache/local_embedding_models`，不发送设计文件到嵌入服务。
新增依赖安装到既有LLMenv，不要求`13`的临时虚拟环境。

## 能力边界

时间、空间、SHA、样本支持、统计、ID和章节覆盖可程序核验；数字字面集合不等于完整原子语义核验，角色错配和复合句推断仍可能漏检。
正文保留`semantic_status=REVIEW`，不能称实际工程事实准确率。
风险颜色保留来源含义，不把绿色等同无风险，不把预测当事件。
ppm沿用用户指定研究口径。CO2/SO2用完整15分钟时间加权均值，H2S用采样值；NO/NO2/CH4没有另设未经确认阈值，只统计绘图。
职业接触参考不等于现场报警设定。措施保留条件、出处、页码，不新增规格、已实施或效果保证，气体不写除尘。
未确认方向、关键输入缺失或核验失败时留存失败，不生成合格空报告。
单日空间对应不等于地质因果规律；本轮不做建模和新诊断。

## 输出与复现

`--dry-run`保存全部事实及`plan.NOT_SENT.json`，不调用API。
`--resume`只接受SHA未变、尚未运行API的准备目录，不覆盖失败或历史模型运行。
各阶段实际请求、原回复、Token、耗时与失败记录随日保存。
最终HTML/PDF、正文核验、精确窗口CSV、图片来源和SHA同时保留。
更换`--output-dir`进行新运行，不覆盖旧目录。
旧便携路径必须显式使用`--profile portable`，不代表当前完整集成能力。
