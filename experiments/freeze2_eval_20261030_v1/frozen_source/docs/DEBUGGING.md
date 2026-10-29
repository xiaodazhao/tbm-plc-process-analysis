# 冻结版本只读调试

项目根目录为`/Users/zhaoxiaoda/Desktop/tbm-plc-process-analysis`，本机解释器为`/Users/zhaoxiaoda/miniconda3/envs/LLMenv/bin/python`。

先阅读`engineering_reporting/pipeline.py`的`prepare → generate → render_report`，再沿调用关系下断点。
不要将构建地质库、历史便携报告或旧局部改写脚本误认为新版日报入口。

```bash
python scripts/run_integrated_report.py --date 2024-02-28 --dry-run --output-dir outputs/my_debug_run
```

这会创建新目录，准备真实事实、图关系、气体统计和检索记录，不发起模型请求，也不会生成假模型正文。
查看`unified_report_input.json`、`report_organization.json`、`progress_investigations.json`、`plan.NOT_SENT.json`。

事实来源、完整数值在`data/sources`中。LLM实际收到的是`writer.model_facts`形成的精简展示视图，必须同时查看真实`.request.json`，不能认为整个大JSON原封不动送入模型。

如需复核已生成报告，逐一对照：`content_plan.json → final_narrative.json → final_narrative.validation.json → rendered_paragraphs.json → artifact.validation.json`。
复算统计时使用保存的逐点文件及原始行号，不按固定20秒乘样本数。

运行`python -B -m pytest tests -q -p no:cacheprovider`进行回归。正式新实验开始后，发现方法不足应记录，不在锁定测试日反复调参。
本次冻结没有修改数据修复、平台检测或四阶段算法。
