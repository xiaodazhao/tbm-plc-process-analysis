# 当前项目先看这里

唯一项目仓库是`tbm-plc-process-analysis`；桌面的`tbm-report`是空目录，不是另一套源码。

## 目前实际状态

- 生产冻结基线：`TBM-Daily-20261029-freeze1`，根目录源码及原有默认入口保留，不在此次清理中修改算法。
- 最新受试候选：`TBM-Daily-20261030-freeze2`，位于`experiments/freeze2_eval_20261030_v1`。
- `frozen_source`是实验SHA绑定的源码证据，不是另一条开发主线；不能随手删掉或编辑，否则实验版本核对失效。
- 尚未实施刚才讨论的结构化核验、来源修订和实质信息保留改进，不能把清理操作写成方法升级。

根README及旧冻结说明作为基线记录没有改写。最新研究进展以本文件和下面的实验结果为准，不要用旧README的“后续实验”描述推断实验尚未执行。

## 新实验结果与论文材料

最新实验：[`RESULTS.md`](experiments/freeze2_eval_20261030_v1/RESULTS.md)。

方法初稿：[`METHOD_CHAPTER_DRAFT.md`](experiments/freeze2_eval_20261030_v1/METHOD_CHAPTER_DRAFT.md)。

实验初稿：[`EXPERIMENTS_CHAPTER_DRAFT.md`](experiments/freeze2_eval_20261030_v1/EXPERIMENTS_CHAPTER_DRAFT.md)。

实际完成30天A-D共120份正式日报、16份接口日报、8对关系消融及80项受控测试。202次真实模型调用。全部PDF技术生成成功不代表全部事实正确；B的ID合同问题及D修订的实际局限均保留。真实人工盲评尚未完成。

## 哪些代码负责什么

|目录|职责|
|---|---|
|`tbm_process/engineering_reporting`|生产冻结的整篇事实组织、规划、生成、检查与渲染|
|`tbm_process/ingestion`、`quality`、`process`、`dynamics`|冻结PLC字段读取、质量处理、运行平台及阶段分析|
|`tbm_process/geology_store`、`evidence_graph`|地质资料查询、时空匹配、证据组织|
|`tbm_process/reporting_v2`、`final_reporting`|仍被调用的共享统计、图表和模型接口；不是可直接删除的旧代码|
|`experiments/plc_mapping_repair_v1`|在用的数据修复及329天重放输入，必须保留|
|`experiments/plc_standardized_export_20261025`|在用的标准化导出工具|
|`experiments/freeze2_eval_20261030_v1`|最新已锁定的正式实验、受试源码快照及结果|
|`release`|版本、来源清单和清理审计，不是第二套算法|

## 运行与数据边界

生产基线入口仍是`python scripts/run_integrated_report.py --help`。

最新候选入口是`python experiments/freeze2_eval_20261030_v1/generate_report.py --help`，默认只做离线准备，付费生成必须显式传`--llm`。不要复用本轮正式实验目录或重新抽样覆盖锁定清单；正式预算已经耗尽。

GitHub保存代码、轻量实验结果、配置、测试和数据索引。完整真实请求响应、原始PDF摘页、处理样本、120份PDF及匿名材料保留在本地桌面`TBM_冻结版本正式实验_20261030.zip`；SHA为`57ad82f804afc2b83b47bc28a47fde8623062a25473ecf1cc6f065b6bbcfba6a`。不能只克隆代码就声称有全量工程原始资料。

本地最新逐组重复输入按SHA去重，并以相对链接指向同一份输入，保留图表和检查程序的原路径可读性；便携ZIP仍使用已记录的共享输入恢复表。完整档案ZIP和真正API请求回复未改动。历史失败仅作为审计证据归入工作树外的压缩包，不再保留多套旧实验工作目录。盲评映射既不读取也不上传。

## 测试

```bash
python -B -m pytest tests -q -p no:cacheprovider
python -B -m pytest experiments/freeze2_eval_20261030_v1/tests experiments/freeze2_eval_20261030_v1/human_scoring/test_score.py -q -p no:cacheprovider
```

请显式指定测试目录，避免把受试源码快照再当一套项目重复收集。
