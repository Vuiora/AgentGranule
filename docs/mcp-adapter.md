# MCP stdio 接入

采用 [官方 MCP Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/)，依赖约束 `mcp>=1.28,<2`。本轮选择 MCP 实现用户允许的接入路径；不另外安装 Skill。

```sh
python -m pip install -e ".[mcp]"
python -m agentgranule.mcp_server --database .agentgranule/project.sqlite3
```

服务器以 stdio 等待 MCP 宿主通信，协议输出使用 stdout，诊断由 SDK 输出至 stderr。数据库也可通过 `AGENTGRANULE_DATABASE` 设置；命令行参数优先。宿主配置示例在 `examples/mcp-config.json`，请替换为实际 Python 与数据库绝对路径。该配置仅为样例，不自动修改用户的 MCP 设置。

## 工具与资源

工具：`create_session`、`add_problem`、`add_module`、`record_message`、`get_granularity`、`request_granularity`、`set_default_granularity`、`set_granularity`、`prepare_plan`、`submit_result`、`history`。

工具按原生类型参数公开。创建工具返回带 `session_id`／`problem_id` 的对象；`history` 返回 `events` 对象；其余操作使用核心结果。提供只读资源 `agentgranule://sessions/{session_id}/history`。

## 宿主调用流程

1. 创建 session，并将用户原文传给 `record_message`。
2. 按任务划分模块，不要求 A／B 固定结构或必须嵌套。
3. `request_granularity` 返回用户询问与建议来源；宿主呈现并记录真实回答。自定义时设置 direction、parameters、actor；选择默认时不伪造人工覆盖。
4. `prepare_plan` 返回参数、来源、uses_default 和版本。模块覆盖 > 项目方向默认 > 内置建议。优点／缺点可分别控制数目，说明方向可设置 detail_level／max_depth。
5. 宿主记录助手／工具消息，再提交实际 output（items 或 text）；兼容旧 categories。详细程度／深度由外部 Agent 执行，核心当前不判定语义质量。
6. 工具错误表示拒绝，不代表成功；通过 `history` 查询已保留的领域拒绝记录。

本地 stdio 接入不提供用户认证。`actor` 是审计标记，服务器不能凭它判断操作者是否为人，宿主必须确认人工设置并控制连接权限。类型错误可能在 MCP 参数解析阶段拒绝，尚未进入核心，所以不会生成核心事件；结果数目错误与过期计划会记录为被拒绝结果。

无法自动获取宿主未发送的外部聊天内容。只记录可见消息，不传入隐藏思考或凭据。实际模型推理仍由宿主承担，服务器只生成约束和校验结果。

## 验证

`python -m unittest discover -s tests -v` 会启动真实 stdio 子进程，使用官方 `ClientSession` 完成初始化、发现工具、设置与修改数目、提交结果、读历史和资源。无网络请求或具体模型依赖。
