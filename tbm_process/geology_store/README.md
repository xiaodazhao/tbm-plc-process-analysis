# 统一里程轴时空地质证据库

独立查询组件，不改变冻结PLC清洗、分段、指标或旧日报。正式论文方向不变，没有Agent、向量检索或付费API。

## 分层

- `database.py`：复用已有结构化提取和来源核对，导入唯一SQLite；查询连接默认只读。
- `spatial.py`：DyK数值轴、精确区间交集与并集；不建立固定网格。
- `time_policy.py`：全量历史/历史截止两种模式；提交日期、可用日期及观察日期分开。
- `applications.py`：当日预测与PLC、当天新增素描与此前预报、响应摘要关联。
- `reporting.py`：原六章日报适配，第一、二章分析仍用原程序；新第三至五章直接使用查询结果。
- `visualization.py`、`server.py`、`ui.html`：同一数据库的里程轴与本地只读检索界面。

SQLite表：`documents`、`document_aliases`、`time_evidence`、`facts`、`sources`、`plc_days`、`plc_responses`、`response_facts`、`metadata`。里程采用REAL64及起终点复合B-tree索引，不用存在单精度舍入的RTree作为精确判据。原始里程文字、原文、源文件哈希和PDF页码/JSON指针均保留。

## 运行

在项目根目录安装可选依赖：

```bash
python -m pip install -r requirements-geology-store.txt
python scripts/build_geology_store.py --output-root 新输出目录
python scripts/serve_geology_store.py --database /path/to/geology.sqlite --port 8766
python scripts/query_geology_store.py --database /path/to/geology.sqlite --start DyK1015+392 --end DyK1015+431 --as-of 2024-03-15 --coverage
python -m pytest tests/test_geology_store.py -q
```

界面仅监听127.0.0.1，不上传资料。更换电脑后查询数据库不要求再次解析PDF；打开原件仍需原件，交付包提供来源索引。生成完整日报需已有逐点PLC、平台/阶段表和项目来源资料。`--ahead`可指定任意正有限距离，不限20米。不会读取API密钥。

## API

```python
from tbm_process.geology_store import GeologyStore
db = GeologyStore('/path/to/geology.sqlite')
all_history = db.query_geology_by_chainage('DyK1015+392', 'DyK1015+431')
past = db.query_geology_by_chainage(1015392, 1015431, as_of='2024-03-15')
coverage = db.query_geological_coverage(1013211, 1017398)
gaps = db.query_geological_gaps(1013211, 1017398, as_of='2024-03-15')
ahead = db.query_forecasts_ahead('2024-04-26', 1015828, 13, 1)
sketches = db.daily_sketches('2024-04-22')
comparison = db.compare_forecast_with_sketch(sketches[0]['document_id'])
db.close()
```

数字及纯数字字符串按调用接口指定的体系以米解释。带前缀的字符串必须与指定体系一致；没有DK/DyK转换。反向推进须明确传入`direction=-1`，不从起终点大小擅自反转输入含义。

## 口径

1. 全量历史查询不承诺当时可用；默认历史日查询采用D日00:00的独占截止。
2. 只有日期的预报以经过原文核对的提交日期早于D准入，检测/封面日期不能替代提交日期。具有可靠带时区可用时刻时按实际时刻比较。
3. 当前素描全部来自桌面伯舒拉岭数据结构化JSON。图片路径日期依据用户明确确认仅作为可用日期，未变成实际观察日期。SHA相同副本保留全部路径但不重复计证据。
4. `daily_sketches`只找当天新增记录，不强制引用远处历史素描。当前真实观察日期仍缺失，故比较结果是原资料属性对照，不是预报准确率或已核实当日揭露。
5. 未注明位置的观察语句保留为`unresolved`，不扩大到表头范围；点不变成区间。原表施工等级的表头范围保留原义，同时注明并非独立验证的掌子面观察范围。
6. 覆盖按TSP/HSP实际预测分段并集计算；风险点和重叠不重复增加长度。设计转述和素描不作为超报覆盖。
7. 地质—PLC共用DyK参照，但盾首至掌子面偏移未知。只能并列描述对应参数和原预报，不能给出米级边界证明或致因判断。
8. 属性比较复用现有原文词项和等级规则；未记载不是没有，词项不同不自动等于工程矛盾。
9. 现有JSON素描没有可确认的原PDF页码，来源落到原JSON指针和JPG，不伪造PDF页码。TSP/HSP保留原PDF及实际页码。

## 交付与验证

每次新建时间戳目录，不覆盖旧结果。输出完整SQLite、文档/事实JSON和CSV、全线区间/空档、329天覆盖表、时间专项案例、自定义前方查询、三天应用及离线HTML/PDF。自动通过仅表示当前程序一致性，不是独立人工地质正确率。真实LLM规划和生成增益不在本轮验证范围。
