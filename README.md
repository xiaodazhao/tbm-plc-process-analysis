# TBM施工日报研究：当前冻结主线

冻结标识：`TBM-Daily-20261029-freeze1`。正式项目目录为本目录，桌面`tbm-report`不是源码仓库。

本次冻结的是可审查的工程实现，不是“事实准确率已经验证”或“论文正式实验已经完成”。

## 先看这四项

1. [详细研究摘要](docs/RESEARCH_SUMMARY_FROZEN.md)：数据、真实方法、代码职责、案例结果及限制。
2. [后续实验设计草案](docs/EXPERIMENT_PROTOCOL_DRAFT.md)：研究问题、对照与消融、公平性和人工评价。
3. [给GPT的项目交接](docs/GPT_HANDOFF.md)：防止外部讨论把历史原型当成当前版本。
4. [最新完整日报](outputs/report_quality_release_20261029_r2/2024-02-28/2024-02-28.integrated.pdf)：2024-02-28，三页，来源与历史照片另附。

## 唯一默认日报入口

```bash
conda activate LLMenv
cd /Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis
python scripts/run_integrated_report.py --date 2024-02-28 --dry-run --output-dir outputs/my_new_run
```

该命令准备全部事实和未发送的模型请求，不调用API。不要复用已有输出目录。
明确需要付费生成时，再对自己的准备目录执行：

```bash
python scripts/run_integrated_report.py --date 2024-02-28 --resume --output-dir outputs/my_new_run
```

密钥仅放环境变量`DEEPSEEK_API_KEY`或本机`.env`，不能写进代码或交付包。

**真实主流程：** 修复后PLC与既有阶段结果、地质库与最新素描、气体、原资料风险及设计参考
→ 统一事实包与可复算的运行现象
→ 整篇LLM内容规划
→ 整篇生成及编辑
→ 规则核验与有限修订
→ 一次HTML/PDF渲染与成品检查。

不是先写三章再追加气体或附录。详细顺序见[集成说明](docs/UNIFIED_ENGINEERING_REPORT.md)。

## 目录职责

| 位置 | 作用 |
|---|---|
| `tbm_process/engineering_reporting/` | 当前整篇日报主流程 |
| `tbm_process/reporting_v2/`、`evidence_graph/`、`geology_store/` | 当前流程复用的计算、证据关系及数据库功能 |
| `tbm_process/ingestion/`、`quality/`、`process/`、`dynamics/` | 保留的PLC处理与分段算法 |
| `tbm_process/final_reporting/` | 仍被调用的共享接口及兼容组件，不是第二条默认主线 |
| `experiments/plc_mapping_repair_v1/` | 修复程序、来源审计和当前329天输入；不能当中间产物删除 |
| `experiments/plc_standardized_export_20261025/` | 标准化导出工具 |
| `local_data/engineering_context/` | 本机设计PDF及本地检索索引，不能当缓存删除 |
| `datasets/portable_30/` | 历史兼容回归数据，不代表新版完整集成输入 |
| `outputs/report_quality_release_20261029_r2/` | 唯一当前接受的日报成果与全过程证据 |
| `release/20261029/` | 冻结清单、数据盘点、测试日志和无API重放记录 |

`run_three_chapter_report.py`等入口保留是为了历史回归与共享依赖，不用于新实验的默认日报生成。
`--profile portable`必须显式选择，不能把它的结果当本冻结版本。

## 清理与冻结边界

历史成果、旧实验及一次性工具已移出工作树：`/Users/zhaoxiaoda/TBM历史归档/20261029_冻结前/`。
路径迁移清单在`release/20261029/HISTORY_RELOCATION.csv`。
保留历史失败及模型原始记录，不通过删除失败来制造成功率。
原始资料、329天输入、冻结分段、地质SQLite和凭据未改动；未解封任何盲评密钥。

当前不再扩展功能。后续先完成独立标注和预注册实验，不在正式测试日期上修改提示词。

## 离线检查

```bash
python -B -m pytest tests -q -p no:cacheprovider
python -B scripts/audit_freeze_inventory.py --output release/my_readonly_inventory
```

2026-10-29清理后：443项测试通过；单日完整事实准备通过，新增API调用0次。
规则PASS、来源可追溯和PDF一致性均不等于独立工程事实准确率。语义仍标记`REVIEW`。
