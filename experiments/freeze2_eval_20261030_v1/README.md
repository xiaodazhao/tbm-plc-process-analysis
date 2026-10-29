# Freeze2：本轮唯一的新冻结版本及实验交付

先打开index.html。已知两处内容问题的修正版在development_fixes；新冻结源码只看frozen_source。它们与旧practical候选及旧freeze1是明确分开的历史版本，没有覆盖旧结果。

- 新冻结ID：TBM-Daily-20261030-freeze2，文件级SHA锁定，不冒充新的Git提交或已经人工验收的版本。
- 原主仓库：/Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis，冻结生产源码、PLC修复与四阶段算法未改。
- 单日入口：本目录generate_report.py，默认离线准备，--llm才调用DeepSeek。输出目录末级必须是YYYY-MM-DD。
- 正式实验入口：run.py interface，然后run.py formal，再run.py relations。不存在“run.py generate”子命令；FREEZE.json的entry摘要不是CLI帮助，实际命令以本README和EXPERIMENT_METHODS为准。
- frozen_source中的datasets、local_data、.cache链接指向原有只读数据/依赖，ZIP不包含这些链接或模型权重。

## 优先阅读

GPT_HANDOFF.md：外部审阅者的快速入口，包含实际完成量、主要缺陷和不能下的结论。

RESULTS.md：本轮实际结果及不能下的结论。

EXPERIMENTS_CHAPTER_DRAFT.md：实验章节初稿，人工指标未获得时明确标识。

METHOD_CHAPTER_DRAFT.md：依据实际代码写的方法章节初稿。

EXPERIMENT_METHODS.md、PRE_REGISTRATION.md：实验公平性、样本、预算、评价与复现。

POSTHOC_MECHANISM_CASE.md：正式实验中发现的规则误报与修订后来源仍不充分的探索性案例；没有据此调正式方法。

## 如何核查

inputs保存34天原始准备产物；runs/interface及runs/formal保存逐组输入、真实请求响应、计划、初稿、编辑、局部修订、检查与成品。C/D共享初稿的SHA在C_D_REUSE_AUDIT中逐日核对。MODEL_CALLS只按实际发送计数，不将各目录复制日志重复相加。

source_excerpts中是实际引用的原PDF物理页和小型原始JSON/表格；SOURCE_EXCERPT_INDEX区分原完整文件SHA与新摘页SHA。大规模原始PLC/PDF不全打包，但保存对应来源索引与完整处理样本。

程序规则、来源ID覆盖、PDF技术通过不等于工程事实准确。压力测试只检验指定扰动。所有人工标签空白；human_blind只给真实评价者，不给他们含A-D组信息的完整作者包。_sealed为新建分配表，未读取、不进ZIP，不能在盲评完成前解封。

human_scoring/score.py仅汇总真实填入的标签，不读取组别映射，不自动产生人工真值；未填时为NOT_EVALUATED。细分句子不一致时先人工对齐，再计算一致性。

## 数据准备的环境修复

首次准备因源码快照未链接本地嵌入模型缓存，23天失败。失败原样留在preparation_attempts，DEPENDENCY_REPAIR记录仅环境路径修复后的单次重试；不改日期、模型、算法或提示词。34天随后全部准备成功，这些失败没有消耗API。

## 交付完整性

MANIFEST列文件大小与SHA，PACKAGE_VERIFICATION记录真实解压复核。密钥、.env、模型缓存、盲评组别映射均不打包。每组的大型重复输入在ZIP中只保留inputs下的一份；恢复对应日期文件后可重新渲染。独立来源核对仍需按索引访问完整原始文件，不能声称ZIP替代了全量工程档案。

本轮实际使用package_release.py打包；EXPERIMENT_METHODS中保留的finish.py package是早期完整重复打包入口，不是本轮最终压缩包的制作命令。PACKAGE_VERIFICATION.json在压缩包完成后产生，位于本地实验目录，不参与自身ZIP的清单循环校验。
