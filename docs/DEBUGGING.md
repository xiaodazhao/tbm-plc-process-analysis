# 亲自调试指南

## 1. 打开正确项目

VS Code打开 `/Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis` 整个目录，而不是单个脚本。
本机解释器已配置为 `/Users/zhaoxiaoda/miniconda3/envs/LLMenv/bin/python`。
如果编辑器此前已经选过别的环境，需要执行一次“Python: Select Interpreter”选择LLMenv；默认设置不会强制覆盖编辑器已有选择。
F5配置也显式指定该解释器。未删除任何其他Python环境。

安装/检查依赖：

```bash
conda activate LLMenv
python -m pip install -r requirements-geology-store.txt
python -m pytest -q
```

## 2. 首先只看一个已有查询

F5选择“01 查询已有地质库（只读）”，运行2024-03-15对应里程的资料覆盖查询。
入口：`scripts/query_geology_store.py`。
可在 `tbm_process/geology_store/database.py` 的 `query_geological_coverage` 或 `query_geology_by_chainage` 下断点。
观察输入里程、`as_of`、准入资料、实际预测分段、覆盖并集和空档；未覆盖不能理解成没有地质情况。
默认数据库路径是本机已有交付结果；换路径时只改F5的`--database`，不要改数据库内容。

## 3. 再走单日完整离线应用

F5选择“02 单日证据库日报（离线，不调用API）”。或在项目根目录运行：

```bash
python scripts/build_geology_store.py --dates 2024-04-26 --output-root outputs/debug_geology_store
```

读取路径已经在入口的参数默认值中列出。需要桌面的原始地质来源索引和已结构化素描JSON；PDF导出需要Chrome/Chromium。
导入和全线覆盖审计仍针对完整库；`--dates`只限制输出哪一天的报告，不是只导入一天。
脚本不重新分段，不读API密钥，不发起付费模型请求。

建议按此顺序下断点：

| 文件/函数 | 看什么 |
|---|---|
| `scripts/build_geology_store.py:run` | 总调用顺序和实际输入路径 |
| `geology_store/database.py:import_catalog` | 原始结构化资料如何进库 |
| `reporting_v2/facts.py:build_facts` | 一天PLC事实、全部平台、参数统计 |
| `geology_store/applications.py:construction_analysis` | 当日范围与地质事实的对应 |
| `geology_store/reporting.py:make_narrative` | 应用事实、离线计划和文字检查 |
| `geology_store/reporting.py:render_report` | 最终HTML如何使用事实与文字 |

以上模块文件均在 `tbm_process/` 下。F10逐行，F11进入函数，在Variables查看字典或DataFrame；先理解一条记录，不要一次修改多个层。

输出在 `outputs/debug_geology_store/<运行时间>/`。优先看：

1. `summary.json`：实际运行状态及数据规模。
2. `reports/2024-04-26/report_facts.json`：真实事实包。
3. `reports/2024-04-26/applications.json`：三类应用的查询内容。
4. 同日期目录的规划、文字、核验、HTML和PDF：逐步对照代码产生的内容。

离线文字不是DeepSeek输出。当前新证据库入口与真实LLM统一路径仍需单独验证，不在本次清理中新增该功能。

## 4. 其他入口不要混跑

- 调试模型接口本身可读 `reporting_v2/narrative.py` 和 `final_reporting/integrated_llm.py`，但本轮F5配置不会调用API。
- `run_engineering_report_v2.py --mode llm`和不带`--dry-run`的`run_integrated_report.py`会产生付费调用。只在明确要做模型实验时使用。
- 原三章便携流程可用 `python scripts/run_integrated_report.py --date 2024-01-06 --dry-run` 检查联合输入；它不等于最新SQLite应用。
- 只想看稳定阶段算法时读 `process/platform_detection.py` 和 `process/segmentation.py`，不要先重跑329天。

## 5. 修改与验证的边界

调试应先观察变量，再在独立输出目录运行；不要直接编辑冻结数据、历史报告或原始来源。
这次清理没有改变任何 `tbm_process/` 源码或算法配置。
旧异常检测/建模试验及其专属测试已随完整备份移出，不再参与当前测试；并不是通过删掉当前失败测试来修复算法。

```bash
python -m pytest tests/test_geology_store.py -q
python -m pytest tests/test_engineering_report_v2.py tests/test_evidence_graph.py -q
python -m pytest -q
```

自动测试不需要付费API。备份、删除清单及测试前后变化见[CLEANUP_2026-10-27.md](CLEANUP_2026-10-27.md)。
