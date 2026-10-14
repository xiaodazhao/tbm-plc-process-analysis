# 正式输出字段说明

## 两类日级事实包

### `structured_full_days/<date>.json`

完整事实层，不做数量截断。包含全天覆盖情况、状态时长、全部有效循环、每个阶段或未识别范围的参数整体特征、连续动态语义、趋势转折、多参数共同变化、施工响应事件和数据质量事件。

### `structured_days/<date>.json`

精简事实层，供后续受约束 LLM 输入。`selection_metadata` 明确记录：

- 全天循环总数与摘要循环数；
- 完整语义数与摘要语义数；
- 完整共同事件数与摘要事件数；
- 筛选规则；
- 未纳入摘要的数据所在位置。

因此，摘要中的 8 个循环不表示全天只有 8 个循环。

## 施工语义表达层

### `outputs/report_semantics/<date>.json`

该文件读取完整冻结事实包后生成，不参与四阶段识别，也不写回冻结结果。

- `daily_overview`：数据覆盖、导向盾首里程进尺和有数据支持的机器状态时长；
- `main_processes`：按可配置规则组织的代表性施工时段和同口径参数比较；
- `construction_responses`：经过持续性、多参数证据和重复合并筛选的运行响应；
- `data_quality`：与施工响应分开的数据质量限制；
- `traceability`：每条中文事实对应的冻结结果字段、内部事件及原始文件行号；
- `selection_metadata`：每个候选事实的保留、排除或合并原因及筛选前后数量；
- `parameter_unit_status`：参数单位和字段映射确认状态；
- `expression_limits`：当前语义层不能支持的解释范围。

“今日进尺”只采用导向盾首里程变化。施工响应和数据质量始终分开表达。

代表性过程事实进一步保存 `activity_expression_class`、`continuity_class`、每次 `activities`、`intervening_gaps`、时间跨度、累计活动时长、距离证据等级和参数比较基准。运行响应保存初末代表值（上游提供时）、代表值、实际变化量、斜率、内部归一化变化、单位状态及关联的主要过程事实。

同目录下的 `<date>.md` 是人类可读语义事实。交给 LLM 的正式版和研究版输入统一由日报量化层写入 `outputs/report_quantitative_v1/`。`outputs/validation/batch_validation.json` 和 `batch_validation_zh.md` 汇总多日结构、追溯和表达约束检查。

## 日报量化与 LLM 输入层

### `outputs/report_quantitative_v1/<date>.quantitative.json`

从冻结完整结果、冻结逐点结果和 V3 工程语义只读生成，包含日报级参数统计、代表过程统计、实际变化量、单位发布状态、追溯证据以及正式版和研究版输入。

- `<date>.llm_input.formal.json`：正式日报输入，不发布单位未确认的参数绝对值；
- `<date>.llm_input.research.json`：研究核查输入，原始记录值均显著标记单位待核实；
- `<date>.llm_request.formal.*` 与 `<date>.llm_request.research.*`：最新提示词和对应事实包组成的完整请求；
- `<date>.report.*.deterministic.md`：未调用 LLM 的确定性核查预览；
- `<date>.delivery_validation.json`：输入、报告、请求状态和冻结源码校验结果。

## 关键 CSV

| 文件 | 粒度 | 主要用途 |
|---|---|---|
| `processed_samples/*.csv` | PLC 逐点 | 最终追溯依据；含源文件、原始行号、质量、状态、循环、阶段和动态范围 |
| `daily_overview.csv` | 每日 | 数据覆盖、进尺证据、状态时长、主要参数和质量概览 |
| `advance_intervals.csv` | 循环 | 循环边界、持续时间、稳定识别状态、进尺一致性和阶段诊断 |
| `phases.csv` | 阶段 | 四阶段边界、持续时间、样本数、质量覆盖和参数统计 |
| `analysis_scopes.csv` | 分析范围 | 正式阶段、未分段循环和阶段间未识别区间 |
| `semantic_window_descriptors.csv` | 局部窗口×参数 | 阶段内 300 秒非重叠局部描述 |
| `merged_semantic_segments.csv` | 连续语义段×参数 | 相邻同类窗口合并后的趋势、波动、变化和复算统计 |
| `trend_transitions.csv` | 转折 | 相邻语义段之间的趋势或波动状态变化 |
| `coupled_parameter_events.csv` | 多参数事件 | 同一阶段内多参数共同变化，统计在事件精确时段复算 |
| `construction_events.csv` | 筛选施工响应 | 满足持续时间和变化证据的施工响应摘要 |
| `data_quality_events.csv` | 质量事件 | 速度、贯入度、转速、进尺和采样问题，与施工响应分开 |

## 追溯字段

所有正式阶段、语义段和事件至少包含：

- `date`
- `start_time` / `end_time`
- `source_file`
- `source_row_start` / `source_row_end`
- `interval_id`
- `phase_id` 或动态分析范围编号

可使用这些字段回到 `processed_samples/*.csv`，再回到原始 PLC 文件。

## 标签解释

- `machine_state`：运行状态层。
- `phase_label`：四阶段或动态分析范围标签。
- `unsegmented_cycle`：整个循环没有可靠稳定阶段，但仍开展动态分析。
- `unidentified_process`：循环内未被正式四阶段覆盖的施工记录。
- `construction_response`：客观机器响应，不表示因果诊断。
- `data_quality`：测量、字段、采样或一致性问题，不表示机器异常。
