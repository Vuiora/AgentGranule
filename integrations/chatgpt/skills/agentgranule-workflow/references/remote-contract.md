# 远程接口契约

私有连接运行在用户电脑上，持续使用配置指定的绝对数据库路径。会话以 session_id 区分；两个对话明确使用同一 ID 才续作同一任务。固定数据库不等于能取得别的聊天原文。

以实际工具目录和 inputSchema 为准。MCP 成功读取 structuredContent：返回标量／列表的核心操作包装在 result 字段，工作流操作直接返回对象；错误使用 isError=true。不要用错误返回或缺少结果继续执行。

| 工具 | 主要参数 | 返回用途 |
| --- | --- | --- |
| create_session | title | result: session_id |
| add_module | session_id, description, parent_id? | result: module_id |
| record_message | session_id, role, content | 保存取得的可见内容 |
| request_granularity / get_granularity | problem_id=实际module_id, direction | parameters、source、revision；request会写请求事件 |
| set_granularity | problem_id, direction, parameters, actor | 整体替换参数对象后的新版本 |
| set_default_granularity | session_id, direction, parameters, actor | 项目方向默认设置 |
| create_workflow | session_id, tasks, context? | workflow_id、依赖顺序 |
| next_task | workflow_id | request、statuses、outputs、complete |
| submit_task | workflow_id, request_id, output | 当前有效进度与结果 |
| workflow_status | workflow_id | 刷新当前有效进度；可能写入失效事件 |
| history | session_id | result: 按顺序排列的事件 |
| analyze_framework | root_path | 实际服务端本地 Python 源码分析及 warnings |
| propose_analysis | session_id, analysis, previous_analysis_id? | 未批准图与修订 |
| update_analysis | analysis_id, expected_revision, analysis | 未批准图的新修订 |
| get_analysis | analysis_id | 图、module_ids、workflow_id及状态 |
| approve_analysis | analysis_id, expected_revision, actor | 真实确认后批准图 |
| allocation_snapshot | analysis_id | 全部图与控制修订快照 |
| save_allocation | snapshot, choices, actor | 原子保存完整分配、workflow_id和受影响任务 |

analysis包含summary、非空modules及可选context。每个模块包含id、name、description、expected_output、basis、directions、parent_id、depends_on；父关系和依赖分别无环，引用本提案内逻辑ID。approved图不能编辑；修改材料或图需propose_analysis(previous_analysis_id=旧ID)。真实模块UUID由module_ids映射取得，不能拿逻辑ID替代。

choices为全部模块／方向恰好各一次的列表，例如 `[{"module_id":"实际UUID","direction":"design","design_effort":0.37}]`。必须使用完整最新allocation_snapshot；任一图或控制修订过期则整批拒绝。保存只改变design_effort，其他参数保留。原始力度独立，不要求总和为1；0.00合法且当前仍执行任务。

其他参数：count/max_depth为正整数；detail_level为非空字符串；design_effort为有限JSON数字0.00–1.00且精确落在0.01网格上，拒绝布尔、字符串、超界与多余精度，不自动舍入。单项set_granularity整体替换parameters，先读取并保留仍需使用的其他参数。内置建议与默认source不构成这轮人工批准。

task对象为 `{"id":"summary","module_id":"实际UUID","direction":"explanation","depends_on":["source"]}`；context仅放实际事实与任务要求。next_task返回description、granularity、constraints、inputs、context。约束item_count非空时输出 `{"items":["有依据的非空项"]}` 且数量恰好一致，否则输出 `{"text":"实际内容"}`；可选details树遵守max_depth。接受后的request_id不能重复使用。相关力度变化使任务与后继失效，无关有效结果保留。
