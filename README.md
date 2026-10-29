# TBM施工日报研究项目

当前实际开发目录：`/Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis`。
`/Users/zhaoxiaoda/Desktop/tbm-report` 不是本项目源码目录；调试代码请使用上述实际开发目录。

## 当前三章日报发布标准

正式展示统一通过 `scripts/run_three_chapter_report.py publish`，不再每次临时设计润色提示词和HTML。
第二章固定为“2.1 全天运行概况 → 2.2 主要掘进区间”：前者展示全天进尺、推进时间占比、折算推进率和总体曲线，后者展示现有主要活动的实际推进时长、平台内PLC进尺、折算推进率及局部三参数曲线。

规则在 `configs/engineering_report_standard.json`，维护代码在 `reporting_v2/activity_facts.py`、`reporting_v2/publication.py`和共享展示模块中。原润色实验入口只保留复现兼容，不再作为新的发布入口。

```bash
python scripts/run_three_chapter_report.py publish --bundle /path/to/one_completed_day --output /new/publication --offline
python scripts/run_three_chapter_report.py publish --bundle /path/to/one_completed_day --output /another/new/publication
```

`publish`读取已经完成分析和核验的单日材料；不重新分段或生成地质结论。默认会调用DeepSeek，`--offline`只准备事实及请求。第二条命令保留既有第一、三章，按当前要求改写第二章并统一渲染；不能把发布层当作原始PLC到日报的完整分析器。不合格回复保留并阻止发布为通过，不以删掉重要内容换取通过。详细口径、输入材料格式及复用边界见 [日报统一标准](docs/ENGINEERING_REPORT_STANDARD.md)。

## 从哪里开始调试

目前最新的是**统一里程轴地质证据库及其日报应用**，入口为 `scripts/build_geology_store.py`。
它读取已修复PLC结果、已有TSP/HSP提取结果及桌面最新素描JSON，构建SQLite，输出查询结果和离线六章日报。
默认不调用LLM，不重新计算PLC分段。

```bash
conda activate LLMenv
cd /Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis
python -m pip install -r requirements-geology-store.txt
python -m pytest -q
python scripts/build_geology_store.py --dates 2024-04-26 --output-root outputs/debug_geology_store
```

运行会新建时间戳目录，包含数据库、事实JSON、规划/文字检查、HTML、PDF及来源记录。
需要本机桌面的来源资料，以及Chrome/Chromium导出PDF。
已有数据库的只读查询无需重新导入原始PDF：

```bash
python scripts/query_geology_store.py --database "/Users/zhaoxiaoda/Desktop/TBM_时空地质证据库_v1_20261027/20261027T144445/geology.sqlite" --start DyK1015+392 --end DyK1015+431 --as-of 2024-03-15 --coverage
```

VS Code已提供本机 `LLMenv` 调试配置。具体断点和文件阅读顺序见 [调试指南](docs/DEBUGGING.md)。

## 保留的入口

| 入口 | 实际作用 | 模型调用 |
|---|---|---|
| `scripts/build_geology_store.py` | 最新SQLite证据库、查询及离线日报应用 | 不调用 |
| `scripts/serve_geology_store.py`、`query_geology_store.py` | 同一数据库的只读界面/查询 | 不调用 |
| `scripts/run_evidence_graph_report.py` | 独立证据图与日报验证入口 | 当前离线 |
| `scripts/run_engineering_report_v2.py` | 六章工程日报V2，证据库应用复用其事实和渲染模块 | 默认offline；`--mode llm`才调用 |
| `scripts/run_integrated_report.py` | 既有三章联合生成流程，保留复现入口和共享依赖 | `--dry-run`不调用；普通运行会调用 |
| `run_analysis.py`、`scripts/run_current_analysis.py` | 原始PLC到分析结果，不是调试日报的必要步骤 | 不调用 |
| `scripts/export_stable_platform_samples.py` | 独立导出稳定阶段样本 | 不调用 |

**不要把“文件名有v2”或“目录名是experiments”当作废弃依据。**
`final_reporting`仍提供当前V2使用的格式化、LLM接口和绘图/PDF工具；`evidence_graph`也是SQLite导入的实际依赖。
上述模块不能直接删除。

证据库应用、证据图应用和原三章真实LLM路径目前仍有独立入口。
不能将它们描述成已经通过真实LLM验证的同一条“SQLite → 证据图 → 联合生成”端到端主流程。
离线文字是确定性预览，不是真实模型生成结果。

## 文件结构

```text
tbm_process/
  ingestion/ quality/ process/ dynamics/  PLC输入、质量、平台/阶段和动态分析
  contracts/ facts/ orchestration/ outputs/  数据契约、事实、调度及输出
  reporting/ final_reporting/           事实构建和保留的联合报告公共模块
  reporting_v2/                         六章事实、指标、规划/文字检查、渲染
  evidence_graph/                       来源关系、查询及证据图检查
  geology_store/                        SQLite、时空查询、应用和只读界面
scripts/                               命令入口
configs/                               字段、报告及平台配置
tests/                                 当前自动测试
datasets/portable_30/                   既有30天便携材料，不等于当前全量库
experiments/plc_mapping_repair_v1/       当前修复数据、来源记录及修复工具
experiments/plc_standardized_export_20261025/  标准化PLC导出工具
outputs/                               本地新生成结果
```

## 数据与版本边界

- 当前全量PLC读取 `experiments/plc_mapping_repair_v1/replay/after/`；历史日报事实和地质匹配保留在同目录下 `impact/` 的 `after/` 分支。路径未迁移，避免影响既有读取和历史比较。
- 原始资料位于桌面 `伯舒拉岭数据/`。最新证据库素描使用其 `右线地质资料/` 中的结构化JSON；旧便携目录仍保留原提取内容供历史流程复现。
- 便携30天支持在没有全量原始资料的电脑上调试既有三章流程，例如 `python scripts/run_integrated_report.py --date 2024-01-06 --dry-run`。最新数据库重建不保证仅凭该便携目录即可完成。
- 不直接编辑生成的PLC CSV/JSON来调试算法。先在独立输出目录重放；清洗、运行状态、平台/四阶段和动态分析规则本次没有修改。
- `.env`、原始数据、大型输出和本机编辑器设置不应提交。不要将API密钥写入源码或调试配置。
- 自动检查通过只表示相应规则/一致性检查通过，不代表工程事实准确率或独立人工验证。

## 本次清理

历史CAD、波形、建模、异常检测和机器学习试验及其重复快照已移出当前工作目录，完整留在项目外备份。
原始资料、当前修复数据、必要来源记录、现行算法和报告共享模块均保留。
清单、备份位置和验证结果见 [清理记录](docs/CLEANUP_2026-10-27.md)。
旧文档如 `INTEGRATED_REPORTING.md` 说明的是原三章路径，不代表最新证据库应用。
