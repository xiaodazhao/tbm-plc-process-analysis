# 实验方法与复现

## 版本及唯一入口

本轮使用TBM-Daily-20261030-freeze2，源码快照在frozen_source，SHA见FREEZE.json。它是独立冻结的后继候选，未替换主项目旧freeze1的生产源码。新增源码变化只有混合点位列表裁剪和无比较时的写作约束；沿用已接受的实用检查策略，资料缺失只作报告说明。

生成单份新日报：在项目根目录执行下面的入口。默认不调用API，传--llm才生成模型正文及PDF。输出必须是新目录，末级使用施工日期，沿用现有渲染器以日期命名目录的约定。

```bash
/Users/zhaoxiaoda/miniconda3/envs/LLMenv/bin/python -B \
  experiments/freeze2_eval_20261030_v1/generate_report.py \
  --date 2024-04-26 --output-dir outputs/freeze2_new_report/2024-04-26 --llm
```

## 真实输入链路

从既有修复后的PLC样本和冻结平台/四阶段结果构建当日统计、活动、稳定样本、变化窗口及其原始行号；没有重跑329天PLC。地质SQLite导出符合日期准入的事实，再加载桌面已更新素描，保留原报告日期证据、PDF页码、SHA及实际范围。既有图谱、PLCResponse和区段分析结果进入统一事实包。设计背景及气体研究统计按原有流程在规划前进入同一对象，不是写完三章后再追加。

PLC日观察核对将首末读数、日内变化、推进状态及实际支持时长分别记录。相同首末里程不能自动解释为全天未掘进；缺失记录不补成停机。原日期空间准入仍保留，无可靠方向时不凭空设前方窗口，但其他报告内容继续生成。气体ppm是用户指定研究口径，不是独立完成仪器标定或现场报警验证。

## 公平对照

同日A-D共享同一事实视图、来源、时间与里程限制、章节结构、图表、同一请求模型及采样参数。A没有显式计划；B由Python选取全部required和最多2条不重复可选事实；C调用冻结Planner，最多一次计划修复，失败则按已冻结方法回退并单独统计；B/C使用相同Generator。D逐字复用C的计划和正文，仅在冻结pipeline.generate中继续执行整篇编辑、检查及必要的哈希约束局部补丁。其他段落和来源ID不得被局部补丁修改。

所有组都运行相同只读检查。A/B/C的失败不反馈给模型改写；D可以按冻结策略保留已检查初稿，失败编辑和补丁不删除。来源章节、正文秒级时间、关键词和格式偏好不是重大事实错误；未解析语义留REVIEW，不声称自动全部核实。

## 抽样与独立性

源候选池329天，按现有来源文件、空间准入及既有输入结构审查，再排除已找到的开发生成、模型调用及评价定义日期。本轮可用池238天，固定种子20261029简单随机选择30天，4个开发接口日期另列；12个盲评日期在输出前锁定。完整日期与排除理由见SAMPLING、CANDIDATE_POOL和HISTORY_EXPOSURE_EVIDENCE。未发现历史使用不等于证明已删除历史从未使用；共享同一TSP/HSP的日期不视为独立地质观测。

## 评价及人工作业

来源独立审查程序用文件SHA、PDF物理页及原文片段核对实际来源，并从保存PLC记录独立重算样本数与状态支持时长。这是部分直接验证，不是专家工程语义确认。原子句模板由标点粗分，人工需要进一步拆复合事实。

重要信息要求先按原始输入与工程阅读需要建立独立草案，标注PROVISIONAL，必须由真实评价者确认适用性；生成器required只是另列的内部代理指标，不能冒充独立金标准。

两名评价者使用human_blind内匿名HTML、同日共享证据及空白CSV；先独立标注再合议。未经填写的支持标签、覆盖和评分保持空白，汇总填NOT_EVALUATED。新的盲分配表只创建封存，不查看已有或新分配表，不打入公开ZIP。完整作者包含A-D方法信息，不能发给盲评者。

受控错误注入80项、8类，只评定指定错误敏感性；报告TP/FP/FN/TN和未核查语义。模板已有开发暴露，不声称真正未见封存测试，也不按结果修改规则。不能拿它推算自然生成的工程事实准确率。

日期配对bootstrap1000次，报告差值和95%区间；任何共享预报来源的日期形成连通簇进行保守敏感性分析。仅有一个簇时不估计该聚类区间。人工未评价的主要终点不填数、不做显著性结论。

## 执行、预算及完整性

run.py lock在所有模型输出之前锁定请求模板、输入、代码、配置和评价定义。interface阶段4日先验证接口，通过后formal阶段30日不替换；有方法错误保留。common.py使用SQLite在发送前原子预留调用；主实验和关系消融合计180次、接口24次、已知开发案例修订4次，各自有限且不自动扩增。HTTP重试0，计划修复1次，局部补丁1次，单日子进程1500秒硬超时，账号或连续服务失败触发停止。

成本以本轮真实usage和response ID为准，D复用C不重复计费。D独立运行的等效成本另列。模型返回别名必须报告，不保证API随机输出逐字可复现。

```bash
# 已锁定阶段不重建、不覆盖；以下是首次运行顺序。
python setup_experiment.py freeze
python setup_experiment.py sample
python setup_experiment.py prepare
python -m pytest -q tests
python run.py lock
python run.py interface
python run.py formal
python run.py relations
python evaluation.py inputs
python pressure.py
python finish.py summary
python finish.py blind
python finish.py figures
python finish.py package
```

以上短命令在本实验目录中执行。模型密钥只读主项目.env，不打印、不复制。当前机型依赖LLMenv、既有模型缓存、处理数据和源PDF。frozen_source中的datasets、local_data、.cache链接为已有依赖，不随ZIP复制。正式输出写入新目录，旧结果及失败日志不覆盖。

## 打包边界

完整ZIP包含源码、锁定输入、全部请求响应、运行结果、空白盲评包、指标和图表。大输入在inputs中只保留一份，per-arm重复处理样本与向量不重复打包，可按日期复制回对应组目录再渲染。事实sources仍保留原绝对路径及SHA；外部机器需按来源索引恢复PDF和原始数据，才能重新完成原始文件核验。源PDF的完整目录和模型缓存不是本ZIP的分发范围，不能声称无原始资料也能重做全部提取。
