---
name: agentgranule-workflow
description: "使用 AgentGranule 执行整体任务：划分模块与处理方向、询问人工粒度、通过 MCP 调度依赖任务、提交并校验结果、记录可见对话，以及设置变更后的局部重算。适用于需要人工控制详细程度且保留流程记录的任务。"
---

# AgentGranule 整体任务

读取 [接口契约](references/contract.md) 获取工具参数、安装与 CLI 后备入口。此 Skill 操作当前项目的本地数据库；真正的语义分析由宿主 Agent 执行。

## 从需求到任务图

1. 新任务调用 `create_session(title)`；续作使用已有 session_id / workflow_id，先读 `history` 和 `workflow_status`，不要重复建任务。将实际用户需求通过 `record_message` 原文保存。
2. 按实际需求划分模块，调用 `add_module`。为每个模块确定处理方向；创建明确的任务 id 和依赖边。不要把分类或 A/B 示例当作所有任务的固定结构。
3. 每个模块／方向调用 `request_granularity`，将问题、建议值和来源呈现给用户。询问数目、详细程度、展开深度等适用参数，允许用户一次设置多个方向。
4. 记录实际问答。仅在真实人工选择后调用 `set_granularity(..., actor=真实操作者标识)`；参数对象整体替换，需同时保留仍需要的参数。用户已明确授权使用默认值时可继续，并记录其真实授权；没有回答时暂停依赖该选择的任务，不将默认建议或超时写成人工决定。已获授权的设置无需再次询问。
5. 调用 `create_workflow`，传入模块 id、方向、依赖和任务事实／材料所在的 context。记录并保留返回的 workflow_id。context 和任务图固定；需求或材料变更时建立新 workflow，并记录关联原因。

## 执行与交付

循环调用 `next_task(workflow_id)`。按 request 中的 description、context、constraints 和直接依赖 inputs 处理，执行实际任务而非生成占位结果。没有足够材料时明确说明缺失，先补充信息，不能为满足 count 编造内容。

- 返回 count 时输出 `{"items":[非空字符串...]}`，项数必须恰好一致；否则输出 `{"text":"实际内容"}`。
- 需要结构化展开时附加 `details` 树，并遵守 max_depth。detail_level 与 custom 参数的语义由 Agent 执行，服务端不判断内容质量。
- 将实际可见助手输出、用户补充、工具调用与结果通过 `record_message` 保存；不保存隐藏思考或认证机密。宿主需回传全部可见消息，服务端不读取其他聊天。只能取得部分工具内容时明确标注执行摘要。
- 调用 `submit_task(workflow_id, request_id, output)`。校验拒绝后修正实际结果重试；过期或已消费的 request 不能再次提交，应重新调用 next_task。
- 每次 next_task 会返回当前有效 outputs 和进度；request=null 且 complete=true 时，交付有效结果。完成前再次调用 workflow_status，避免交付已过期内容，记录最终可见回复。

用户修改粒度时记录原文并调用 set_granularity，再读 workflow_status。受影响任务和依赖者会失效；无关任务保留。继续 next_task / submit_task 到 complete=true。失效历史仍留在事件记录中，不覆盖既有问答。

## 接入边界

优先使用已配置的 AgentGranule MCP 工具；工具未连接时按接口契约运行同名 CLI 操作。不能声称读过未取得的历史消息。此 Skill 不授予外发消息、合并、发布或新增供应商／界面的权限；项目代码变更遵守本仓库 AGENTS.md 和分支审批流程。
