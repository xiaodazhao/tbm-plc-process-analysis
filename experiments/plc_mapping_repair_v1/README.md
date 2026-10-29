# PLC字段映射独立审计与候选修复

> 2026-10-27清理说明：以下文字描述当时的完整历史审计。当前工作目录保留修复程序、来源记录、修复后数据和`after`分析/匹配/日报事实；修复前分支、逐行对照明细、一次性迁移/打包脚本和30天模型复跑已移入项目外完整备份。`AUDIT_AND_IMPACT_REPORT.md`等仍作为历史研究记录保留，不是要求重新执行全部旧实验。恢复方法见项目根目录`docs/CLEANUP_2026-10-27.md`。保留的`replay.py`属于历史配对回放工具，原有`--resume`状态可能引用已归档的对照文件；不要在当前输入目录直接重跑，应从备份恢复完整工作副本后使用。

先读[AUDIT_AND_IMPACT_REPORT.md](AUDIT_AND_IMPACT_REPORT.md)，再读[EXPERIMENT_30DAY_REVIEW.md](EXPERIMENT_30DAY_REVIEW.md)。

核心限制：没有全字段身份认证；未解析值隔离；原始/冻结成果不覆盖；单位未知；不调用API。

repaired_raw含329天候选修复视图，repair_provenance含逐行原始索引，row_layouts是独立导航分类。replay保存329天两条算法路径的前后结果与归档比对。impact保存既有日报事实构建器和地质匹配器的真实重放。rerun_outputs保存同一30天三分支实验。unresolved_records.csv.gz保留未解析记录，all_fields_mapping_status.csv逐字段声明已修复范围与未认证透传。

审阅ZIP只含代码、统计、完整结构化JSON与3天逐点样例；全量逐点和候选修复数据仍完整保留本目录，不因压缩而删除。
