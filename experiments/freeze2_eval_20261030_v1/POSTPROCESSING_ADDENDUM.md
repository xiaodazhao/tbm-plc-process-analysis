# 后处理范围说明

正式输入、抽样、生成代码、提示词、主评价定义和预算均受EXPERIMENT_REGISTRY及FREEZE约束，没有在正式结果出现后更改。

以下文件是新增的导出或次级诊断工具，不反馈给Planner/Generator，也不作为事后挑选的主要假设指标：

- export_source_pages.py、prepare_review_assets.py：导出实际原PDF页与小型原始资料，为匿名评价者补齐相同证据；移除生成器required标记，避免将它冒充独立重要性真值。
- dataset_description.py：只读统计真实SQLite与已锁定样本清单。
- diagnostics.py：统计计划修复/回退、冻结规则问题类型、段落补丁结构保持和实际模型阶段，不把它们计作工程事实准确率。
- posthoc_cases.py：如实记录正式日期2023-11-22暴露的机制问题。该案例是事后探索性审读，未用来调正式方法。
- human_scoring/score.py：仅实现预登记的支持比例公式及真实双评价者标签一致性；没有读取分配表或填人工标签。实现SHA在标签收集前另存，缺标签时NOT_EVALUATED。
- relation_review.py：将已实际完成的关系段落对做左右匿名展示，只显示共同原始组件，语义判断留空，不以关系编译结果作为人工真值。

没有把这些后处理命名成新的工程算法，也没有修改正式测试集、源资料或主指标。现有失败和假阳性/漏检例子不删除。
