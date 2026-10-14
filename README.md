# TBM PLC 施工过程分析

本项目读取 91 份日 PLC 文件，完成数据质量控制、施工状态识别、掘进循环划分、循环内四阶段识别、阶段内动态语义提取和可追溯事实包生成。项目本身不调用 LLM；报告层可以生成定量事实包、完整模型请求和确定性日报核查预览。

当前正式方法：`tbm_four_phase_v1_2026-10-12`。

当前软件架构：`tbm_process_layered_v1_2026-10-13`。架构重组不改变冻结四阶段方法。

## 正式流程

```text
原始PLC -> 字段统一与质量控制 -> machine_state -> 掘进循环
       -> startup/rising/stable/declining -> 阶段内动态语义
       -> 完整事实包 -> 工程语义筛选与表达 -> 结构化LLM输入包
```

运行状态与四阶段是两个层级。`stopped`、`advancing`、`free_rotation` 等属于 `machine_state`；`startup`、`rising`、`stable`、`declining` 只用于已接受掘进循环内部。无法可靠分段的部分保留为 `unsegmented_cycle` 或 `unidentified_process`，不会强制归类。

## 环境与运行

```bash
cd /Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis
conda run -n LLMenv python -m pip install -r requirements.txt
conda run -n LLMenv python -m pytest -q
conda run -n LLMenv python scripts/run_frozen_regression.py
conda run -n LLMenv python run_analysis.py
conda run -n LLMenv python scripts/validate_analysis.py
conda run -n LLMenv python scripts/build_report_semantics.py
conda run -n LLMenv python scripts/validate_report_semantics.py
conda run -n LLMenv python scripts/build_quantitative_daily_report.py --date 2023-11-03
conda run -n LLMenv python scripts/validate_quantitative_report.py --date 2023-11-03
```

默认 PLC 目录在 [tbm_process/config.py](tbm_process/config.py) 中定义，也可通过 `--plc-dir` 指定。当前 TBM 字段映射位于 `configs/field_profiles/tbm9_2023.json`，可通过 `--field-profile` 替换。正式输出目录固定为 `outputs_frozen_v1/`。

在另一台电脑上使用时，克隆仓库并安装环境，然后通过 `--plc-dir` 指向该电脑上的 PLC 数据目录。原始 PLC、冻结逐点结果和其他大型生成文件不会上传 GitHub。

```bash
git clone git@github.com:xiaodazhao/tbm-plc-process-analysis.git
cd tbm-plc-process-analysis
conda create -n LLMenv python=3.11 -y
conda run -n LLMenv python -m pip install -r requirements.txt
conda run -n LLMenv python run_analysis.py --plc-dir /path/to/TBM9_2023
```

## 分层代码结构

- `tbm_process/contracts/`：层间 DataFrame 和 JSON 数据契约。
- `tbm_process/ingestion/`：读取文件，并按字段配置转换为统一 PLC 字段。
- `tbm_process/quality/`：速度、贯入度、转速质量控制与采样连续性。
- `tbm_process/process/`：运行状态、掘进循环、冻结四阶段和动态分析范围。
- `tbm_process/dynamics/`：阶段内趋势、波动、转折和多参数共同变化。
- `tbm_process/facts/`：生成完整及精简的可追溯 PLC 事实包。
- `tbm_process/reporting/`：将冻结事实确定性转换为读者可读的施工语义。
- `tbm_process/orchestration/`：只负责层级调用顺序和全数据集汇总。
- `tbm_process/outputs/`：CSV、JSON、方法清单和基础图表。
- `configs/field_profiles/`：不同 TBM 的原始字段映射；更换数据源时不修改分析层。
- `configs/dynamic_parameters.json`：动态分析参数注册表；增加新参数时不修改四阶段模块。
- `scripts/validate_analysis.py`：91 天独立一致性验证。
- `scripts/run_frozen_regression.py`：冻结版 10 天回归。
- `scripts/build_report_semantics.py`：批量生成完整工程语义事实及人类可读核查文本。
- `scripts/build_quantitative_daily_report.py`：从冻结结果只读生成日报级量化事实、正式版/研究版 LLM 输入和确定性预览。
- `scripts/validate_quantitative_report.py`：独立复核量化事实、日报重现性、请求状态和冻结源码校验值。
- `scripts/validate_report_semantics.py`：批量验证语义事实的口径、追溯和表达约束。
- `prompts/tbm_daily_report_system_v2_quantitative.txt`：支持正式版和研究定量预览版的日报提示词。

## 主要输出

- `processed_samples/*.csv`：逐点质量、状态、循环、阶段和分析范围，保留原始文件及行号。
- `advance_intervals.csv`：全部有效掘进循环。
- `phases.csv`：正式四阶段汇总，由最终逐点归属重新计算。
- `analysis_scopes.csv`：包括未分段循环与未识别施工区间的动态分析范围。
- `merged_semantic_segments.csv`：合并后的连续趋势区间，统计由合并范围原始记录复算。
- `coupled_parameter_events.csv`：多参数共同变化，统计由事件精确重叠时段复算。
- `structured_full_days/*.json`：每日完整、未筛选、可追溯事实包。
- `structured_days/*.json`：适合后续 LLM 读取的精简事实包，带筛选前后数量和未纳入范围说明。
- `outputs/report_semantics/*.json`：结构化工程事实、筛选记录、中文表达和内部证据。
- `outputs/report_semantics/*.md`：便于人工检查的中文语义事实。
- `outputs/report_quantitative_v1/*.quantitative.json`：日报级量化事实、来源、单位状态及两种 LLM 输入包。
- `outputs/report_quantitative_v1/*.llm_input.formal.json`：不包含未确认单位绝对值的正式版输入。
- `outputs/report_quantitative_v1/*.llm_input.research.json`：显著标记单位限制的研究定量预览输入。
- `outputs/report_quantitative_v1/*.report.*.deterministic.md`：未调用 LLM 的确定性日报核查预览。
- `outputs/report_quantitative_v1/*.delivery_validation.json`：量化交付的独立一致性和冻结源码校验报告。
- `outputs/validation/`：工程语义多日一致性与表达约束报告。
- `validation_report.json` 与 `验证说明.md`：正式验证结果。
- `outputs_frozen_v1/examples/2023-11-03/`：冻结动态语义事实示例。

完整分层与扩展规则见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，字段说明见 [docs/OUTPUT_SCHEMA.md](docs/OUTPUT_SCHEMA.md)，冻结方法见 [docs/FROZEN_METHOD.md](docs/FROZEN_METHOD.md)。施工语义表达层见 [docs/REPORT_SEMANTICS.md](docs/REPORT_SEMANTICS.md)，2023-11-03 的定量字段与单位核查见 [docs/QUANTITATIVE_REPORT_AUDIT_2023-11-03.md](docs/QUANTITATIVE_REPORT_AUDIT_2023-11-03.md)。这些报告层均不修改冻结分段结果，也不调用大语言模型。

## 已完成验证

- 91/91 份文件运行完成，共 328,217 条记录。
- 每条记录最多一个正式循环和一个正式阶段标签。
- 阶段表与逐点结果完全对应，阶段统计可独立复算。
- 循环、阶段和动态语义均不跨硬采样缺口。
- 硬缺口不计入运行或停机时长。
- 有效循环核心样本全部进入动态分析范围。
- 10 天冻结回归保持核心循环、稳定边界和核心逐点阶段标签不变。

这些检查证明程序可运行、规则可复现和内部数据一致，不代表阶段识别准确率达到 100%。正式定量准确率仍需人工标注后计算。

## 研究限制

- 刀盘转速统一字段是依据数据关系建立的暂定映射，仍待 PLC 点位表确认。
- 明显不可信的推进速度会被排除，但不能据此还原真实速度。
- 约 20 秒采样可能遗漏非常短的空转、空推和快速升降过程。
- 施工响应事件只描述参数事实，不能自动解释为地质恶化、刀具磨损、卡机或设备故障。
- 当前规则仅在这批数据和 10 天目视样本上检查，跨项目泛化尚未验证。
