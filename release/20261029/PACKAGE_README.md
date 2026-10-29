# TBM日报冻结交接包

版本：`TBM-Daily-20261029-freeze1`，2026-10-29。

## 推荐顺序

1. `documentation/GPT_HANDOFF.md`：快速了解现状及不能越过的证据边界。
2. `documentation/RESEARCH_SUMMARY_FROZEN.md`：约1.9万字符的详细数据、方法、实际效果及局限。
3. `documentation/EXPERIMENT_PROTOCOL_DRAFT.md`：约7000字符的后续对照、标注和统计设计，尚未执行。
4. `accepted_report/2024-02-28/2024-02-28.integrated.pdf`：最新接受的三页日报。
5. 同日事实、规划、真实模型输入输出、修改记录和PDF检查。
6. `freeze/`：源码身份、数据盘点、443项测试及无API重放。

## 包内材料

- `source_code/`是当前实际工作树的源码快照，不是只取旧Git提交。主入口仍为`source_code/scripts/run_integrated_report.py`。
- `accepted_report/`保留同一次接受运行的完整证据，`before_*.pdf`和中间模型回复是审计记录，不是多个活动版本。
- `evidence/prior_plan_attempts/`保留本次质量开发前两次提纲调用，最终稿有明确复用关系。总开发调用8次，不能当稳定单报成本。
- `evidence/baseline/`保存比较用旧稿及少量关键输入，明确为旧开发版本，不能和当前结果混算成功率。
- `evidence/direct_sources/`包含当前日报直接用到的少量真实来源，原路径与包内位置见`PATH_MAP.csv`。
- `evidence/geology.sqlite`是只读数据库快照；`current_sketch_JSONs/`是214份当前素描JSON。数据库旧素描路径和运行新JSON已分开说明。
- `source_code/datasets/portable_30/`仅为既有兼容回归材料；不是冻结版新对照实验数据集。
- 不包含全部329天原始与分析数据，不包含全部地质PDF和照片，不承诺仅用此包可对任意日期重新生成。当前目录快照与直接来源足以审查本日关键计算、模型请求和交付正文。

## 路径与复现

原始请求、事实中的本机绝对路径未被改写，以免改变历史证据和SHA。包内副本位置在`PATH_MAP.csv`。外部电脑需依据此表重新配置来源根，而不是认为原机路径仍可直接访问。

可以首先运行不依赖工程环境的完整性检查：

```bash
python verify_package.py
```

源码的回归命令为`python -B -m pytest tests -q -p no:cacheprovider`；环境版本见`freeze/environment.json`和`DEPENDENCY_VERSIONS.txt`。这份包保留常用测试材料，但某些测试/来源审计仍可能需要原机索引和外部资料，不能无条件承诺换机全量复现。

`--dry-run`不调用API；`--resume`会调用付费API，审阅时不要误执行。没有携带密钥；浏览器PDF依赖原机Chrome/Chromium，模型嵌入缓存未打包，可按固定官方版本另行获取。

## 校验与研究边界

`MANIFEST.csv`列出包内除清单自身之外的所有文件大小和SHA。ZIP已执行CRC检查、实际解压和逐文件SHA对照；ZIP外另附整体SHA。

冻结期新增API调用0次；443项回归通过；单日45事实完整离线准备和统计重放通过。工程语义仍是`REVIEW`，没有预填人工标签或解封盲评。

本包不含API密钥、Cookie、私钥、Git历史或盲评组别密钥；保存来源路径不等于携带访问凭证。
完整历史科学证据在原机项目外归档，未纳入这份交接包；清理摘要在`freeze/CLEANUP_RECORD.json`。
