# LLM受约束施工日报对照实验

> 本文档记录A-E论文对照实验，不是正式前两章日报的运行手册。正式日报使用`scripts/build_daily_report_front_sections.py`。

## 研究边界

该模块位于当前平台优先分析和工程语义之后，只读取既有日报事实包。它不读取原始PLC、不重新计算运行平台、不修改四阶段，也不把自动核验写成工程准确率。

处理链为：

```text
当前结构化事实
  -> 精简类型化事实池
  -> 确定性内容选择与排序
  -> DeepSeek结构化草稿
  -> 独立字段角色与事实支持核验
  -> 一次有证据的局部修订
  -> 三部分Markdown日报
```

方法借鉴了数据到文本研究中的内容选择与规划、受控表到文本生成、逻辑一致性、原子事实评价和证据驱动修订思想。当前代码是这些思想在TBM施工日报中的工程化组合，不宣称完整复现原论文，也不把组合本身夸大为新的通用生成算法。

## 五组对照

| 组别 | 输入与处理 |
|---|---|
| A | 同一事实池上的确定性模板，无LLM |
| B | 精简事实池直接输入DeepSeek |
| C | B加预先确定的内容计划 |
| D | C加字段角色、单位、连续性和禁用推断约束 |
| E | 从D实际草稿出发，独立核验后做至多一次局部修订 |

B、C、D使用同一模型、温度和最大输出长度。E不重新生成随机初稿。所有LLM句子必须声明`supporting_fact_ids`，但声明本身不等于核验通过。

## 独立核验

确定性核验覆盖：

- 今日进尺、里程、记录数；
- 状态时长和占比；
- 总跨度、累计活动时长和间隔时长的字段角色；
- 参数统计、响应初末值和实际百分比；
- 质量事件数、受影响记录数和事件跨度记录数；
- 工程单位限制；
- 间歇活动不得写成全程连续；
- 运行响应时间顺序；
- 必需事实覆盖；
- 地质、故障、刀具和人员原因等无依据推断。

复杂语义不能由结构化字段可靠裁决时保留`REVIEW`。`PASS`、`FAIL`、`REVIEW`和`N/A`分开统计。程序不会调用同一个模型给自己的正文打一个笼统分数。

## 局部修订

E组只处理明确`FAIL`和缺失的必需事实：

- 数值或字段角色错误时，用对应结构化事实的确定性表达替换；
- 无事实编号的错误句删除；
- 缺失的必需事实补入原计划章节；
- 运行响应按来源时间重新排序；
- `REVIEW`保持原样交给人工复核。

每次修改保存原句、新句、原因和事实编号，修订后再次运行同一验证器。最大修订轮数为1。

## 复现

```bash
conda run -n LLMenv python -m pytest -q

# 只准备事实和请求，不调用API
conda run -n LLMenv python scripts/run_llm_report_experiment.py \
  --date 2023-11-03 --dry-run

# 单日三次LLM调用
export DEEPSEEK_API_KEY="..."
conda run -n LLMenv python scripts/run_llm_report_experiment.py \
  --date 2023-11-03 --groups A,B,C,D,E --max-api-calls 3

# 十天断点续跑
conda run -n LLMenv python scripts/run_llm_report_experiment.py \
  --all --groups A,B,C,D,E --resume --max-api-calls 27
```

配置位于`configs/llm_report_experiment.json`。API密钥只从环境变量或隐藏输入读取，不写入任何文件。若调用失败，保存失败记录，不以模板冒充模型结果。

## 输出

`outputs/llm_experiment/<date>/`保存：

- `input_facts.json`与`evidence_index.json`；
- `content_plan.json`与`required_facts.json`；
- A—E各组的草稿JSON、Markdown、逐句核验JSON和人工复核CSV；
- B—D完整请求、原始API响应和运行元数据；
- E组修订日志。

根目录的`summary.csv`、`summary.md`和`experiment_report.md`给出十天总表、Token、耗时、逐日覆盖率及修订案例。未配置可靠模型价格时，费用保持空值。

## 解释限制

十天是方案开发集，每组每个日期只有一次模型调用。结果可以比较这次运行中的错误和覆盖情况，但不能替代多随机种子实验、人工语言质量评价或跨TBM验证。自动可判定项目达到100%也不等于报告工程真实性达到100%。
