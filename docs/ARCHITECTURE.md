# 当前架构

正式三章日报只有一个入口：`scripts/run_integrated_report.py`。上游PLC分析是独立的一条链，地质PDF提取和V2内容单元准备目前在外部项目完成；本仓库附带30天已处理地质输入以便跨电脑开发。

1. `ingestion -> quality -> process -> dynamics -> facts -> orchestration -> outputs`：原始PLC到冻结分析结果。`run_analysis.py`及`scripts/run_current_analysis.py`负责上游重建；便携数据可跳过重建。
2. `reporting.front_sections`与`final_reporting.plc`：只读处理后的逐点CSV、平台/阶段表和结构化日结果，构建日报PLC事实。
3. `final_reporting.integrated_facts`：将PLC事实和同日地质内容单元合入一个带来源、时间、里程、用途约束的事实包，建立空间交集链接；链接本身不说明因果或预报准确性。
4. `final_reporting.integrated_llm`：一个联合事实包进入全文内容规划；模型按计划写三章文字。程序检查计划、正文与引用，必要时各允许一次修订。
5. `final_reporting.plots`、`geology_axis`、`rendering`：确定性生成平台表、参数图、地质轴、HTML和PDF。`validation`检查结构与呈现完整性。

没有“先写两章再补一章”的默认路径。`outputs/`仅存本地生成物，GitHub附带的`datasets/portable_30/`是跨电脑实验输入，不是新算法版本。模型文本检查通过仍表示候选报告，需要独立语义审查。完整运行细节见[联合流程](INTEGRATED_REPORTING.md)。
