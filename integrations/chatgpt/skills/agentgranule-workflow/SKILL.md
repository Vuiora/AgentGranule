---
name: agentgranule-workflow
description: "通过已连接的 AgentGranule MCP 分析任务模块，由调用者分配各方向的设计力度，调度依赖任务、记录可见对话并局部重算。适用于需要人工控制模块粒度的完整任务。"
---

# AgentGranule 个人 ChatGPT 工作流

使用前，在当前聊天的工具菜单中同时选择 **AgentGranule Personal** 连接，再调用此技能。技能安装本身不会自动连接或启动私有服务；没有实际可用的连接时先报告缺失，不能声称已绑定或调用成功。

读取 [远程接口契约](references/remote-contract.md) 获取工具与数据格式。只使用本对话实际连接的 AgentGranule 工具，服务前缀以当前工具目录为准。连接缺失、权限错误或隧道离线时报告实际状态；不能在云沙箱创建数据库或运行本地 CLI，冒充用户电脑上的服务。

新任务用 create_session 并以 record_message 保存真实需求。续作使用调用者提供或当前上下文已有的 session_id、analysis_id、workflow_id，读取最新 history、get_analysis 与工作流状态；不同对话可以使用同一已知会话 ID，但连接本身不会读取其他聊天。

根据取得的材料提出带依据的模块清单，包含稳定逻辑 ID、名称、职责、预期输出、处理方向及明确父关系和依赖。analyze_framework 的 root_path 指 MCP 服务端本地路径，不能把 ChatGPT 附件路径当成本机材料已扫描。由宿主进行真实语义分析，服务仅保存与机械校验。

propose_analysis 保存未批准提案后，呈现完整模块、方向和关系供调用者修改。只有取得对这份具体图及修订的真实确认，才 approve_analysis。执行任务的泛化请求、Agent 建议或默认值不是批准。

普通远程对话用 allocation_snapshot 呈现全部模块／方向的完整设置，让调用者分配 design_effort=0.00–1.00、步长0.01，再展示完整分配表与受影响任务。调用者明确确认后，以最新快照调用 save_allocation；用户已经给出的完整具体分配可沿用。原生3D与滑块需要明确可用的本地执行器、Python和同一数据库，隧道不会把桌面窗口传入网页。没有这个执行环境时采用上述对话交互，不声称已显示3D。

图确认和力度分配确认分别记录。未分配预览不构成设置，取消、失败、超时及缺少回答不是批准。快照任一修订冲突就重新加载与确认；不能用旧批准覆盖新设置。首次 save_allocation 原子创建工作流，继续返回的 workflow_id，不另建同图工作流。批准后修改图需提出关联的新图；只改力度则刷新快照并完整确认。

循环 next_task，按真实 description、context、constraints 和直接依赖 inputs 处理，再 submit_task。数目或结构失败修正；过期 request_id 重新取得。交付前 workflow_status 核对当前有效输出，complete=true 才声称工作流完成。机械校验不能证明语义真实性，材料不足时补充信息，不能凑数编造。

使用 record_message 回传能够取得的可见用户／助手原文和实际工具结果，部分工具内容明确标为执行摘要；不保存隐藏思考、密钥或认证凭据，缺失历史明确注明。记录交付原文，不声称自动读取全部聊天。

此 Skill 只指导现有工具；自动粒度策略和跨启动项目索引尚在设计，不能调用不存在的参数或宣称已实现。actor 用于记录而非身份认证。Skill 不授予合并、发布、外发消息或操作其他应用的权限。
