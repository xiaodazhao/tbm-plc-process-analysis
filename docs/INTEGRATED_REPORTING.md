# PLC 与地质联合事实及全文规划

## 技术路线

本入口位于已完成的 PLC 分析和地质 V2 内容单元之后。它不重新读取原始 PDF 进行提取，也不改变 PLC 数据清洗、设备状态、运行平台、四阶段、参数事件或现有进尺口径。

1. PLC 分支读取当日结构化结果，形成日进尺、工作/停机时长、数据覆盖、主要参数、全部运行平台及动态事件事实。
2. 地质分支读取同日`03_v2_content_units.json`，保留 TSP/HSP 预测、风险点/区间及历史掌子面素描的原始来源与页码。仅接受有日期依据、且形成于施工日 00:00 之前的内容单元。
3. 双方在`integrated_facts.full.json`合并。每项事实保留`fact_id`、类型、可写章节、原始来源、数值或原文证据。`cross_source_links`记录运行平台导向盾首里程与预报适用里程的交集。
4. `integrated_facts.llm_input.json`把两类事实同时交给一次三章全文内容规划。计划确定选择哪些事实、放在哪章哪段，以及哪些可证实的空间关系需要同段表达。全部运行平台仍在确定性明细表中，无需都写进叙述。
5. 经过确定性计划校验，模型按该全局计划生成三章原子陈述。程序检查来源 ID、数值、必需事实、预测/观察混淆、跨源链接及一些禁止的因果断言。
6. 表格、PLC 曲线、运行时间轴、地质里程轴继续由现有确定性代码生成。验证后的文字放入相应三章，再导出 HTML/PDF。

PLC 与 PDF 并非在报告末尾简单拼接：合并发生在内容规划之前，同一个规划请求包含两类事实和空间关系。这个结构可供未来另建“机器响应与预报对照”分析，但当前空间重叠**不证明**预报准确，也不能据此推断机器参数变化的地质原因。导向盾首与掌子面之间的空间偏移未确认，不能擅自换算。

## 输入与运行

仓库附带30天便携数据，示例日期`2024-01-06`有两类输入。Windows或macOS直接运行：

```bash
python scripts/run_integrated_report.py --date 2024-01-06 --dry-run
```

`--dry-run`仅写事实包与全文规划请求，不调用模型。调用 DeepSeek 时，把密钥放在`DEEPSEEK_API_KEY`环境变量或主仓库内未提交的`.env`中，移除`--dry-run`。仓库的`.env.example`只含空占位，不保存真实密钥。可通过`--model`、`--base-url`、`--timeout-seconds`选择兼容接口；`--skip-pdf`只跳过 PDF 输出。

正常运行有两个逻辑模型阶段：一次规划、一次正文生成。规划或正文未通过确定性检查时，各允许一次带错误列表的定向修正；修订前后正文分别保存，修订后重新核验，且不得丢失原稿已引用的事实 ID。这里的定向修正仍由模型返回完整 JSON，并非程序保证只改一个字句。`--resume`可复用同日期已保存的模型输出，避免中断后重复付费。底层 SDK 每个阶段至多额外重试一次，实际 HTTP 请求次数可能更多。程序不会修改上游事实、无限重试或伪造报告。

模型请求显式关闭思考模式并提高 JSON 输出上限；若接口仍返回截断、空内容或无效 JSON，保存原始响应、`finish_reason`、Token 和失败原因后停止。不可把失败样本从统计中删除。历史素描的单元若缺少观测日期，仅在同一原始报告 ID、文件和 SHA 标识的其他单元有一致日期证据时继承；继承依据记录在`available_date_basis`。仍无法确定或日期冲突时停止，不猜测可用时间。

可用 `--analysis-dir` 与 `--geology-candidate-root` 覆盖便携数据默认路径，接入更多日期的同格式上游结果。便携包只含处理后的30天输入，未包含原始PLC/PDF；不能在Windows端独立复核原始地质PDF与逐点PLC的提取/清洗准确性。API密钥只应放在本机环境变量或被Git忽略的本仓库`.env`，不要写入事实包或提交到GitHub。

## 输出与校验边界

新结果保存在`outputs/integrated_reports/<日期>/`，不覆盖既有实验结果：

- `integrated_facts.full.json`：完整联合事实、来源和空间链接；
- `integrated_facts.llm_input.json`：进入全文规划的两类事实；
- `plan.request.json`、`plan.payload.json`、`plan.validation.json`：规划输入、实际输出和程序检查；
- `generation.request.json`、`generation.payload.json`、`generation.validation.json`：正文输入、实际输出和程序检查；
- `plan.response.json`、`generation.response.json`及元数据：原始 API 结果、模型版本、Token 与耗时；
- `plan_repair.*`、`generation_repair.*`：仅在相应校验失败时产生，保存修订请求、原始返回、修订稿、重新核验和引用事实保留检查；
- 图表、HTML、PDF、`render_validation.json`和`run_summary.json`：成品与排版/一致性检查。

程序校验**只能**确认可程序判断的合同：来源 ID、允许章节、日期、里程一致、数值是否有对应证据、必要事实是否保留等。`generation.validation.json`仍把每条自然语言陈述列为`REVIEW`；规则 PASS 不能写成“事实正确率”或“人工已核实”。对地质语义、来源原文支持、跨源关系和文字质量仍需独立审查。

无可用地质内容单元时，联合包会明确生成`GEO:availability`，不编造预报。地质报告同源日期冲突、晚于或等于施工日的资料、PLC/地质当日里程不一致时入口会停止，不会静默选一个版本继续。
