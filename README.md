# TBM PLC施工过程与日报事实生成

本项目当前只保留一条研究主线：

```text
原始PLC
  -> 字段映射与质量控制
  -> machine_state
  -> 平台优先施工过程识别
  -> startup / rising / stable / declining
  -> 阶段内动态语义
  -> 完整结构化事实
  -> 日报数值事实、明细表和图形
  -> 受约束LLM组织正文
  -> 事实一致性校验
  -> HTML / PDF日报
```

平台识别方法版本为`tbm_platform_first_experiment_v1`，四阶段方法版本为`tbm_four_phase_v1_2026-10-12`。版本字符串是方法标识，不代表项目中仍保留多套历史实现。

当前方法仍属于研究方法，尚未经过人工真值标注和跨TBM定量验证。算法平台不能直接解释为现场确认的真实掘进循环。A-E生成与验证代码作为论文对照实验保留，不是正式日报的必经路径。

## 目录

```text
tbm-plc-process-analysis/
├── tbm_process/
│   ├── ingestion/          # 原始字段读取与字段配置
│   ├── quality/            # 数据质量、采样连续性和暂定转速映射
│   ├── process/            # machine_state、平台识别和四阶段
│   ├── dynamics/           # 趋势、波动、转折和多参数共同变化
│   ├── facts/              # 完整及精简结构化事实
│   ├── reporting/          # 工程事实、日报正文约束、图表和HTML/PDF
│   ├── llm_reporting/      # A-E论文对照实验与事实验证器
│   ├── orchestration/      # 基础流程与当前平台优先流程
│   └── outputs/            # 确定性文件写出
├── configs/
├── scripts/
├── tests/
├── docs/
├── outputs/                # 本地生成，不提交Git
├── config.json
└── run_analysis.py
```

## 安装

```bash
conda create -n LLMenv python=3.11 -y
conda run -n LLMenv python -m pip install -r requirements.txt
conda run -n LLMenv python -m pytest -q
```

## 当前运行流程

### 1. 生成基础质量控制数据

```bash
conda run -n LLMenv python run_analysis.py \
  --plc-dir /path/to/TBM9_2023
```

输出到`outputs/base_analysis/`。这是当前平台分析的输入层，不是另一套对外方法。

### 2. 运行当前平台优先分析

```bash
conda run -n LLMenv python scripts/run_current_analysis.py
```

输出到`outputs/current_analysis/`，包括逐点结果、全部平台、四阶段、动态语义、完整事实、定量事实和LLM输入。

默认处理`configs/platform_analysis.json`中的10天研究样本，可通过`--dates`指定其他已生成日期。

### 3. 运行A-E受约束日报对照实验（可选）

只准备输入和Prompt，不调用API：

```bash
conda run -n LLMenv python scripts/run_llm_report_experiment.py \
  --date 2023-11-03 --groups A,B,C,D,E --dry-run
```

调用DeepSeek时只从环境变量读取密钥：

```bash
export DEEPSEEK_API_KEY="..."
conda run -n LLMenv python scripts/run_llm_report_experiment.py \
  --all --groups A,B,C,D,E --resume --max-api-calls 30
```

输出到`outputs/llm_experiment/`。该入口只用于论文对照实验，不是生成正式日报的前置步骤。当前严格验证器直接用于核验和E组局部修订；规则通过率不能称为工程事实准确率。

### 4. 生成施工日报前两章

先完成基础分析和当前平台优先分析，再运行：

```bash
conda run -n LLMenv python scripts/build_daily_report_front_sections.py \
  --date 2023-11-03 \
  --analysis-dir outputs/current_analysis \
  --output-dir outputs/daily_reports/2023-11-03 \
  --report-text-mode llm
```

该入口只读取现有逐点结果、平台表和四阶段表，不重新识别施工过程。输出包括日报事实JSON、全部平台明细CSV、短句证据JSON、两章正文JSON、时间轴、三参数曲线、HTML、PDF和一致性验证结果。表格“主要情况”始终由确定性事实规则生成；默认正文使用确定性回退文本，使用`--report-text-mode llm`时由LLM依据结构化事实组织两章正文。密钥从环境变量或被Git忽略的`.env`读取，请求、响应和核验元数据保存在`report_narrative_generation/`；调用失败或输出越界时自动回退。

### 5. 生成最终三章日报

先生成`outputs/current_analysis/`，再提供地质 V2 候选结果目录：

```bash
conda run -n LLMenv python scripts/run_final_reports.py \
  --date 2024-04-26 \
  --analysis-dir outputs/current_analysis \
  --geology-candidate-root /path/to/TBM9_geology_v2_candidate
```

该入口复用冻结 PLC 事实与已经通过规划检查的地质内容单元，确定性生成三章 HTML/PDF、地质里程轴、事实映射和成品审计，不调用 LLM。默认输出到`outputs/final_reports/`。Chrome/Chromium会自动发现，也可用`--chrome`指定；只有HTML时可加`--skip-pdf`。完整方法和外部数据目录要求见[最终三章日报](docs/FINAL_REPORTING.md)。

## 当前入口

- `run_analysis.py`：基础字段统一、质量控制、状态和基础结构化分析。
- `scripts/run_current_analysis.py`：唯一的平台优先施工过程、四阶段、动态语义和日报事实入口。
- `scripts/run_llm_report_experiment.py`：A-E受约束生成实验。
- `scripts/build_daily_report_front_sections.py`：基于现有分析结果生成日报前两章。
- `scripts/run_final_reports.py`：读取冻结 PLC 与地质 V2 证据，生成并审计最终三章日报。
- `tbm_process/llm_reporting/verifier.py`：当前类型化事实验证器。

## 配置

- `config.json`：质量控制、四阶段和动态分析参数。
- `configs/platform_analysis.json`：平台优先识别参数及研究日期。
- `configs/field_profiles/tbm9_2023.json`：TBM9原始字段映射。
- `configs/dynamic_parameters.json`：动态参数注册表。
- `configs/report_parameter_units.json`：工程单位和报告使用限制。
- `configs/llm_report_experiment.json`：A-E实验与模型配置。
- `configs/final_report.json`：最终三章日报的可移植路径、日期和地质轴配置。

## 研究边界

- 原始PLC表头未提供可靠工程单位；没有PLC点位表时不得猜测单位。
- 刀盘统一转速是暂定映射，仍需现场点位资料确认。
- 约20秒采样可能遗漏极短启动或下降过程。
- 参数响应不能自动解释为地质、刀具或设备故障。
- 当前10天样本属于方法开发和目视检查集，不是定量准确率验证集。
- 生成输出均位于`outputs/`并被Git忽略；新电脑克隆后应从原始PLC重新运行。

方法说明见[当前架构](docs/ARCHITECTURE.md)、[阶段方法](docs/PHASE_METHOD.md)、[输出结构](docs/OUTPUT_SCHEMA.md)、[受约束生成](docs/LLM_CONSTRAINED_REPORTING.md)、[事实验证器](docs/FACT_VERIFIER.md)和[最终三章日报](docs/FINAL_REPORTING.md)。本轮删除与保留内容见[项目清理记录](docs/CLEANUP_2026-10-16.md)。
