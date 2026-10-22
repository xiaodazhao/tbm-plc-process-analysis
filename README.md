# TBM施工日报：PLC与地质联合生成

本仓库的正式报告入口是 `scripts/run_integrated_report.py`。PLC和地质资料分别处理为结构化事实，**先合并为同一事实包，再由模型规划并撰写完整三章**。不存在“先生成两章、再补第一章/第三章”的正式运行路径。

```text
PLC原始记录 -> 质量控制 -> 运行平台/四阶段/动态分析 -> PLC事实 --+
                                                            +-> 联合事实JSON
TSP/HSP/素描PDF -> 既有提取与时空筛选 -> 地质内容单元 -----------+
联合事实JSON -> 全文内容计划 -> 三章文字 -> 程序核验 -> HTML/PDF
                         表格、PLC曲线和地质里程轴由程序绘制 ----+
```

本次代码整理未改动冻结的PLC清洗、状态识别、平台识别、四阶段和动态分析规则。处理后的30天实验材料位于 `datasets/portable_30/`，可以在没有原始PLC和PDF的Windows电脑上继续开发联合日报。它不是329天全量数据，也不能替代原始资料核验。

## Windows快速开始

在PowerShell中，从GitHub克隆仓库后执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/run_integrated_report.py --date 2024-01-06 --dry-run
```

`--dry-run` 不调用API，只在 `outputs/integrated_reports/2024-01-06/` 写入联合事实包和规划请求。30个可用日期见 [`DAYS.txt`](datasets/portable_30/DAYS.txt)。切换日期只需改 `--date`。

需要实际调用模型时，把自己的 `DEEPSEEK_API_KEY` 设为环境变量，或者从 `.env.example` 创建本地 `.env` 并填写；`.env` 已被Git忽略。然后去掉 `--dry-run`：

```powershell
python scripts/run_integrated_report.py --date 2024-01-06
```

需要Chrome/Chromium导出PDF；自动查找失败时传入 `--chrome "C:\Program Files\Google\Chrome\Application\chrome.exe"`。只调试事实与HTML时可用 `--skip-pdf`。模型失败会保存请求、原始响应、Token与错误；不会用模板冒充模型结果。`--resume` 可复用同日期已经保存的模型输出。**实际API调用会产生费用**，默认测试不调用。

## 便携数据

`datasets/portable_30/analysis/processed_samples/`：30天质量控制后的逐点PLC CSV，保留时间、来源行号、状态、参数、质量标记与平台/阶段标签。不是原始PLC文件。

`datasets/portable_30/analysis/tables/`：同30天的平台与四阶段明细，从329天既有结果按日期筛出；只用于读取，不重新分段。

`datasets/portable_30/analysis/structured_full_days/`：同30天完整结构化PLC分析结果，供施工响应选择使用。

`datasets/portable_30/geology/evidence/prepared_days/`：同30天已准备好的地质V2内容单元，是联合日报的直接地质输入。

`datasets/portable_30/geology/extracted/`：已提取的右线地质目录与223份结构化报告JSON，供追溯与后续开发；**原始PDF未随仓库提供**。部分来源字段仍记录Mac原路径，它们是出处线索，不保证Windows端能打开原PDF。

`datasets/portable_30/MANIFEST.json` 保存日期清单、文件大小与SHA-256。仓库**不包含**原始PLC、原始PDF、API密钥、329天逐点全量结果或旧模型实验输出。需要更新便携数据时，使用 `scripts/prepare_portable_dataset.py --help`，从已验证的上游结果重新导出，不要手工改写冻结值。

## 当前代码路径

- `run_analysis.py`、`scripts/run_current_analysis.py`：从原始PLC重建上游分析，只有取得原始数据时才需要运行。
- `tbm_process/ingestion/`、`quality/`、`process/`、`dynamics/`、`facts/`、`orchestration/`、`outputs/`：冻结PLC分析链。
- `tbm_process/reporting/front_sections.py`：读取处理后的逐点数据和平台表，构建日报PLC事实。它是辅助模块，不单独生成两章日报。
- `tbm_process/final_reporting/`：联合事实、全文规划与生成、地质展示、绘图、HTML/PDF、自动检查。
- `scripts/run_integrated_report.py`：唯一正式三章日报入口。

程序检查可验证结构、数字候选、引用和部分表达禁令；所有自由文本陈述仍须语义审阅。自动通过率不是工程事实准确率。数据来源、方法及限制详见[联合流程](docs/INTEGRATED_REPORTING.md)、[阶段方法](docs/PHASE_METHOD.md)、[清理与数据记录](docs/CLEANUP_2026-10-22.md)。
