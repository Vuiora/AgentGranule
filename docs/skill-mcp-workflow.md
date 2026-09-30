# 整体任务 Skill 与 MCP

本轮基于维护者已合并的算法 PR #5（main `97e9d7c`），在 `codex/skill-mcp-workflow` 开发。用户已授权整体任务 Skill 封装、MCP 衔接和一轮测试；合并仍须人工明确批准。

## 启用

仓库 Skill 位于 [SKILL.md](../.agents/skills/agentgranule-workflow/SKILL.md)，适用于支持仓库 `.agents/skills` 的宿主。重新加载项目后可显式调用 `$agentgranule-workflow`。其他宿主可读取同一 Skill，或将该目录复制到其支持的 Skill 位置。未修改全局技能目录或用户客户端设置。

Python 3.11+ 环境执行：

```sh
python -m pip install -e ".[mcp]"
python -m agentgranule.mcp_server --database /absolute/path/project.sqlite3
```

也可使用安装后的 `agentgranule-mcp --database ...`。此命令供客户端通过 stdio 启动，不是 HTTP 服务。stdout 仅用于 MCP 协议，SDK 日志输出到 stderr。配置模板见 [examples/mcp-client.json](../examples/mcp-client.json)，需把 Python 与数据库占位符替换为真实绝对路径，Python 环境必须已安装本项目。未连接 MCP 时 Skill 可调用相同操作的 JSON CLI。

## 完整流程

1. create_session → record_message 保存实际需求原文。
2. add_module 划分问题；request_granularity 提供人工询问、建议值及其来源。
3. 宿主呈现问题，回传真实问答；真实人工选择后 set_granularity。用户明确授权使用默认值时无需伪造覆盖设置。
4. create_workflow 校验并保存任务图及 context。
5. next_task 获取当前可执行任务的 request_id、约束、模块描述、材料和依赖输出；宿主执行语义处理。
6. submit_task 校验并原子接受结果；重复领取返回相同请求，重复提交或已过期请求被拒绝。
7. workflow_status 查看当前有效输出。complete=true 后交付；可从 history 读取事件与传入的原文。
8. 修改粒度后重新调度，仅使受影响的任务和后代失效，无关结果保留。服务重启后按 workflow_id 继续。

工具详细契约见 [Skill 接口契约](../.agents/skills/agentgranule-workflow/references/contract.md)。12 个 MCP 工具覆盖会话、模块、记录、粒度与完整任务调度；CLI 保留原有核心操作并新增 4 个工作流操作。

## 实现与边界

`Workflow` 使用第一轮的 Task、topological_order、compile_constraints、validate_output。第一轮 AlgorithmRunner 继续服务本地已注册 Python handlers；新 Workflow 服务跨工具调用的宿主 Agent，持久化请求与结果，不接入模型供应商，不动态导入 JSON 中的代码。

任务图与 context 在创建后固定。需求／事实变更应创建新 workflow；粒度修改可在原 workflow 中重算。本轮按顺序执行，没有多 Agent 的独占领取租约。有效结果在同一个 workflow 中复用，不跨 workflow 缓存语义输出，也不自动判断 Agent／模型实现版本；需要换处理实现时创建新 workflow。机械校验无法证明内容真实性或 detail_level 的语义质量，宿主需实际处理并检查。

SQLite `BEGIN IMMEDIATE` 覆盖有效状态检查、请求生成、结果接受与事件写入；上游修订会拒绝已发出的下游旧请求。拒绝的有限 JSON 结果保留原提交内容，非 JSON／非有限值直接拒绝。失效结果保留在历史事件中，当前输出中移除；对话原文不覆盖。MCP 属于本地受信 stdio 接入，不提供网络服务或身份认证。

## 一轮测试

```sh
python -m unittest discover -s tests -v
python -X utf8 PATH_TO_SKILL_CREATOR/scripts/quick_validate.py .agents/skills/agentgranule-workflow
```

本地 Windows / Python 3.14：53 项通过（原有 41 项 + 工作流 9 项 + 接入 3 项），Skill 格式校验通过。新增覆盖：完整 DAG、稳定请求、校验失败可重试、数目／深度拒绝、上游修改拒绝下游旧结果、默认变更与覆盖优先级、局部重算、进程／连接重启恢复、并发重复提交仅接受一次、跨会话及非法图预检、CLI 后备完整闭环、真实官方 MCP 客户端与服务器子进程通信及 structuredContent。

Skill 操作契约按真实 MCP 与 CLI 流程走查；未声称完成外部宿主 UI 自动加载测试或模型语义质量评测。[代码提交 c6735a8 的 CI](https://github.com/Vuiora/AgentGranule/actions/runs/36715505637) 在 Windows/Linux × Python 3.11/3.14 四组合全部通过。安装 MCP 命令入口的隔离构建验证与 --help 通过。

[PR #6](https://github.com/Vuiora/AgentGranule/pull/6) 已创建并关联现有 milestone，等待人工评审当前 head SHA；Agent 不合并。
