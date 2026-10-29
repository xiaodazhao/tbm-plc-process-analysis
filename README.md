# TBM施工日报研究项目

当前开发目录：`/Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis`。
`tbm-report`不是本项目源码目录。

## 当前唯一日报主入口

```bash
conda activate LLMenv
cd /Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis
python -m pip install -r requirements-engineering-reporting.txt
python scripts/run_integrated_report.py --date 2024-04-26 --dry-run --output-dir outputs/my_run
python scripts/run_integrated_report.py --date 2024-04-26 --resume --output-dir outputs/my_run
```

第一条报告命令只计算事实和保存未发送请求，不调用API、不生成假模型正文。
第二条使用锁定的同一事实包调用DeepSeek，完成整篇规划、生成、编辑、核验及HTML/PDF。
也可直接省略`--dry-run`一次完成全流程。输出目录不能覆盖既有实验。
密钥只从环境变量`DEEPSEEK_API_KEY`或项目`.env`读取，不写入Git。

**当前流程不是先生成旧三章再追加附录：**

已有修复PLC及四阶段结果 + 地质SQLite和最新素描 + 气体逐时刻记录 + 原PDF风险提示 + 设计条文检索
→ 一个统一事实包 → 一个整篇内容规划 → 一次整篇写作和编辑 → 核验 → 一次最终渲染。

日报结构：

- 一、当日施工总体情况。
- 二、TBM掘进过程与运行情况：全天运行、主要掘进区间、气体监测。
- 三、地质情况与施工响应分析：地质与机器响应、现场记录、前方地质信息与设计参考。

设计参考保留原文条件及来源，不记为已执行措施；原始风险颜色不转换为新安全等级。
气体沿用用户指定ppm研究口径；参考值不是现场报警或人员暴露判断。
地质建模不在本流程中。详细执行顺序见[集成说明](docs/UNIFIED_ENGINEERING_REPORT.md)。

## 阅读代码

| 目录 | 当前作用 |
|---|---|
| `tbm_process/engineering_reporting/` | 当前整篇调度、气体统计、设计检索、风险准入、规划写作和成品检查 |
| `tbm_process/reporting_v2/` | 复用的施工事实、响应特征、活动进尺及绘图工具 |
| `tbm_process/evidence_graph/` | 来源、里程区段、响应分析及支持关系 |
| `tbm_process/geology_store/` | 地质SQLite、时间准入及只读查询 |
| `tbm_process/ingestion/ quality/ process/ dynamics/` | 既有PLC处理和阶段识别，本次未修改 |
| `tbm_process/final_reporting/` | 共享DeepSeek接口与历史便携流程等公共实现，不可直接删除 |
| `local_data/engineering_context/` | 本机设计PDF、只读检索索引、风险提取快照，不提交Git |
| `outputs/` | 事实、请求回复、核验及最终日报，默认不提交Git |

## 数据与历史版本

PLC使用`experiments/plc_mapping_repair_v1/replay/after/`中的已修复分析结果。
原始资料在桌面`伯舒拉岭数据/`。地质库、来源索引及设计资源路径可通过CLI参数指定。
本次没有重算全329天、没有修改字段修复规则和四阶段算法。

`datasets/portable_30/`只支持历史便携基线，不包含新气体与设计检索的全部输入。
若需复现旧方法，必须显式选择：

```bash
python scripts/run_integrated_report.py --profile portable --date 2024-01-06 --dry-run
```

`run_three_chapter_report.py publish`等旧入口仅供历史实验复现，不是新日报默认流程。
地质库查询、稳定样本导出等独立工具继续保留，不重复实现或删除共享模块。

## 检查

```bash
python -B -m pytest tests -q -p no:cacheprovider
```

检查通过表示相应程序规则和一致性通过，不是独立人工工程事实准确率。
