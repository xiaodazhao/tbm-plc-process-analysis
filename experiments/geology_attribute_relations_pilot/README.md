# TSP/HSP属性关系小实验

本目录是独立实验，不由正式日报入口调用，不修改SQLite、PLC、地质提取或图谱模块。

## 方法与范围

- 只读读取既有数据库的地质、物探、围岩建议等级三个原始字段，复用上一轮22类候选单元，不重新提取全部PDF。
- 枚举正长度TSP/HSP交集；原文日期准入通过后，从96组中目的性选择12组。固定随机种子仅用于打破并列，不是随机总体抽样。
- 规则A保留组合、局部、原岩、程度范围和缺省信息；只分离已盘点的“石炭二叠系”时代词，不把岩性主次排列或同类岩块的列举次序一概视为相同。
- 模型B收到同一原文和单元，但看不到A的判断。使用一个固定Prompt、两批各6组，最多两次官方DeepSeek请求，SDK重试为0。
- 六个重点属性全部要求输出；其他属性最多三项。A的163项与B的107项不是同分母评分，不能比较数量得出优劣。
- 未绑定区段的旧风险单元不按整份报告广播。本轮保留原文风险，但没有实现新的风险归属提取。
- 日期只代表既有项目采用的正文提交日期依据，不声称已经独立确认实际收文时刻。
- 程序只验证来源、范围、日期、字面引用及少量明显非法输出，语义均保留REVIEW；人工CSV不预填。

## 使用

在原项目根目录、LLMenv环境下运行：

```bash
python experiments/geology_attribute_relations_pilot/pilot.py prepare
python experiments/geology_attribute_relations_pilot/pilot.py summarize --output /absolute/result/directory
python experiments/geology_attribute_relations_pilot/review_results.py /absolute/result/directory
python -m pytest experiments/geology_attribute_relations_pilot/test_pilot.py -q
```

以上命令不调用API。`prepare`新建时间戳目录，`summarize`及`review_results`只处理指定的实验输出。

唯一付费入口是：

```bash
python experiments/geology_attribute_relations_pilot/pilot.py llm --output /absolute/result/directory --env-file /private/path/to/.env
```

密钥沿用项目读取方式，环境变量优先。`api_calls.json`在发送前登记，每批已尝试后不会自动重试；超过两次拒绝发送。不要删除账本规避上限。交付结果已完成两次授权调用，无需再次运行该入口。

## 文件

`samples.json`是冻结样本，`candidate_pool.json`是完整合格候选池，`rule_results.json`是A，`llm/`是真实请求与原始响应，`llm_results.json`是B。`source_audit.json`、`source_page_texts.json`、`availability_source_audit.json`保存只读来源检查。

`model_program_checks.json`不是语义金标准；`independent_literal_diagnostics.json`记录额外离线诊断，不修正或删除模型输出。`人工核查.csv`按组阅读；`逐项人工核查_107项.csv`按项核查。

依赖直接复用原项目。`source_snapshot`保存相关源码证据，不是可以脱离完整项目独立运行的第三方软件包。源PDF未复制，文件名、原路径、SHA-256和页码保留；表格文字在原页出现不代表其全部语义或表格单元归属已经由专家确认。
