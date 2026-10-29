# TBM工程日报 V2

独立六章版本，不改旧版三章入口、PLC分段、阶段或历史结果。默认离线，不读取密钥或调用API。六章正文、数据表、图表、原始素描图和附录由同一事实包生成；离线PDF明确标记为程序模板核验稿，不能冒称真实LLM产物。

## 运行

在项目根目录：

```bash
python scripts/run_engineering_report_v2.py --date 2024-04-26 --mode offline
```

默认使用本机修复重放目录`experiments/plc_mapping_repair_v1/replay/after`、便携目录的223份地质结构化报告，以及本机地质证据包的原PDF SHA索引。换电脑时显式传入`--analysis-dir`、`--catalog`、`--registry`、`--source-root`和`--output-dir`。逐点文件支持`.csv`或`.csv.gz`，平台及阶段表仍用旧程序输出，程序不重做识别。

默认输出`outputs/engineering_reports_v2/日期/运行时间/`，每次新建，拒绝覆盖。Chrome/Chromium导出PDF，可用`--chrome`指定可执行文件。

## 模块

- `performance.py`：既有stable与advancing及质量交集；逐点PR、总推力口径FPI、机械SE；连续窗口CV及既有真实时间稳健趋势。
- `geology.py`：日期准入、DK/DyK检查、按真实来源边界切分、来源属性对照、前方可配置窗口、素描及名义PLC对应。不存在精确盾首—掌子面转换，不作地质因果判断。
- `facts.py`：复用旧版事实构建器，新增安全统计、完整源行集合、代表区间和关注事项；保留全部运行明细。
- `narrative.py`：完整PLC与地质事实共同进入六章计划/生成接口；默认确定性文字，保存请求但不发送。模型失败不会冒充LLM输出。
- `rendering.py`：真实曲线、状态时间轴、性能图、地质里程图、按SHA定位的原始素描整页图、HTML/PDF和一致性检查。
- `defaults.json`：用户确认的设备参数/单位与透明的报告级实验参数。

## 真实LLM接口

只有使用者明确运行`--mode llm`才读取`DEEPSEEK_API_KEY`或`--env-file`。复用旧版`call_json_model`；保存实际请求、响应及Token元数据。最多正常计划/生成各一次，不自动付费重试。验证失败明确转为离线稿，保存失败记录，不声明完整语义核验通过。保存了数字/来源检查与REVIEW，仍需工程审查。

## 计算和门禁

设备D=10.23m、N=70。单位为用户2026-10-27确认，并非本轮独立厂家核验。PR=v/n；FPI=F/(70PR)，使用PLC总推力而非净刀具法向力；SE先统一N、m、s，再以MJ/m³输出并核对功率等价式。SE不是实际电耗。原始贯入度列不用于代替PR。

报告级最小转速0.5、最小速度0.5、PR分母保护0.1、最小8样本/180秒、300秒连续窗口、比较相对变化20%、参考转速容差15%/推力容差25%均在配置中显式保存。其中PR保护与比较门限是首版实验约定，不是传感器标定、安全阈值或已经验证的最优值。历史参考只用早于日报的数据。工作时间和推进时间口径不同。

同日素描只有日期、无实际可用时刻时进入待核实清单；不会自动当作当日已揭露资料。允许有已核实可用时间戳的同日资料作为日终新增；预测不能解释形成前的施工。原始PDF缺失只跳过插图，不伪造图片。无设计分段时明确标记缺失。

设计接口可在配置`design_records`提供`record_id/start_m/end_m/chainage_prefix/available_date/source_file/source_sha256/source_page/source_quote/verified`及可选`reported_grade/attributes`。需来源文件存在且SHA一致、日期与里程系统通过；设计未核实不能靠OCR或文件名补造。

## 限制

本版是工程分析候选实现，不是新的冻结研究基线。相近文本的规则属性比较只报告原文差异与未知，不代表地质专家裁定；观察点不广播成范围；暂无经过核实的盾首/掌子面偏移。TSP/HSP与素描的比较是名义位置对照，不是预报准确率。耦合残差模型暂未建立，不输出假模型。

测试：`python -m pytest tests/test_engineering_report_v2.py -q`；旧版回归：`python -m pytest tests -q`。合成测试信号仅用于单位/接口回归，不进入真实日报。测试通过不等于工程识别准确率。
