# 冻结证据说明

版本：`TBM-Daily-20261029-freeze1`。

- `FREEZE.json`：源码/成品身份及冻结范围，含真实Git状态说明。
- `SOURCE_MANIFEST.csv`：本次快照中的源码、配置、测试与说明SHA；不是原始数据全备份。
- `DATA_INVENTORY.json`：只读核对PLC表、SQLite、当前素描和接受日报的结果。
- `geology_source_inventory.csv`：SQLite原路径核对；素描旧路径与运行刷新来源分开。
- `current_sketch_source_checks.csv`：214个当前JSON逐项来源及SHA。
- `plc_day_inventory.csv`、`platform_phase_daily_counts.csv`：329天及平台/阶段数。
- `accepted_report_source_checks.csv`：接受日报所有直接事实来源检查。
- `protected_core_SHA256.csv`、`protected_core_diff.json`：冻结PLC代码及配置未变化。
- `tests.xml`、`tests.log`、`test_run.json`：清理后的443项回归测试。
- `OFFLINE_VALIDATION.json`：不调用模型的完整事实准备、分析复算及正文/PDF一致性检查。
- `offline_replay/`：本次断网准备的真实输入和未发送请求，不是新模型输出。
- `CLEANUP_RECORD.json`：旧实验、旧说明、试跑输出和项目副本的迁移原因。
- `HISTORY_RELOCATION.csv`：完整旧新路径及SHA，含46,196个历史文件；不建议当成正文阅读。
- `ARCHIVE_VERIFICATION.json`：对项目外归档重新计算SHA的检查。
- `PACKAGE_CHECK.json`：桌面交接ZIP的安全/解压/内部SHA验证。

本次不提交或推送Git；以源码快照和SHA冻结，保留真实未提交状态。未提交不等于未冻结，但后续应通过SHA确认没有改动。
本次新增的只是盘点脚本和交接文档，没有修改工程算法、来源数据或接受日报。

旧历史原始API请求和失败位于项目外归档，最新运行自身的原请求、原回复及前后对照仍在接受成果目录。不要把这些审计记录当成多个可选主线。

交接ZIP有独立`MANIFEST.csv`及`verify_package.py`。清单不包含自身，避免自引用；ZIP外另存SHA。它包含源码、当前样例、少量直接来源及摘要，不包含API密钥、Git历史或盲评密钥，也不是329天完整数据迁移包。
