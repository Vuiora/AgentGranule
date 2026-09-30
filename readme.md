# AgentGranule

通过人工交互调节 Agent 在各个处理方向上的粒度，实现项目客制化，并记录完整对话过程。

## 当前状态

本项目进入 v0.1 框架阶段。`main` 保留已审批版本，当前开发在 `framework/blueprint`；模块／YAML 和 MCP 分别在功能分支中实现，按层级等待人工审批。

框架包含 SQLite 对话与事件存储、问题／子问题、人工分类粒度、带版本的处理计划和结果数目校验。外部 Agent／模型负责实际分类，宿主负责将可见对话传入记录。尚未实现模型供应商接入、界面或身份认证。

蓝图见 [docs/blueprint.md](docs/blueprint.md)，分支与审批流程见 [docs/branch-workflow.md](docs/branch-workflow.md)。[Milestone：v0.1 — 框架与可审批接入](https://github.com/Vuiora/AgentGranule/milestone/1)。

## 运行框架

需要 Python 3.11+：

```sh
python -m venv .venv
# 激活虚拟环境后执行
python -m pip install -e .
python -m unittest discover -s tests -v
python -m agentgranule create_session '{"title":"问题 A"}'
```

命令行接收 JSON 对象，成功返回 `result`，输入错误返回 `error` 并以状态码 2 退出。Windows PowerShell 可通过 `--%` 传入 JSON，例如：

```powershell
.venv\Scripts\python.exe --% -m agentgranule create_session {\"title\":\"A\"}
```

```python
from agentgranule import Project

with Project() as project:
    session = project.create_session("问题 A")
    project.record_message(session, "user", "要求对子问题 B 分类，列举 3 个类别。")
    a = project.add_problem(session, "A")
    b = project.add_problem(session, "B", parent_id=a)
    project.set_granularity(b, count=3, actor="human")
    plan = project.prepare_plan(b)
    # 外部 Agent 使用 plan 进行分类，将实际结果和可见消息回传。
    project.record_message(session, "assistant", "类别：甲、乙、丙。")
    project.submit_result(plan["plan_id"], ["甲", "乙", "丙"])
    events = project.history(session)
```

默认数据库 `.agentgranule/project.sqlite3` 不提交到仓库。原文可能包含项目敏感内容，调用者应在写入前去除认证机密。

## 项目目标

- **记录所有对话内容**：保留用户与 Agent 的交互、需求澄清、人工设置、调整过程及结果，让项目的形成过程可追溯。
- **人工控制处理方向粒度**：用户可以针对具体问题、子问题及其处理方向设置粒度。
- **实现客制化**：Agent 的处理方式与输出细节由人工交互控制的粒度设置驱动。

## 什么是处理方向粒度

处理方向粒度，是人在指定问题及其处理方式下，对处理细节或规模所设置的控制参数。

例如：

1. 有一个问题 **A**，其中包含子问题 **B**。
2. 用户要求 Agent 对 **B** 使用**分类**的方式进行描述。
3. 用户人工设置该分类问题的**列举数目**。
4. 这个列举数目就是此处的**问题处理方向粒度**。

若用户将列举数目设为 3，Agent 应按该设置组织分类描述；当用户将其改为 5，输出应随设置调整。这里的数目仅用于说明概念，具体约束与交互机制留待后续设计。

粒度应关联具体的问题或子问题及其处理方向，而不只是整个会话的统一输出长度。其他处理方向及其粒度参数尚待讨论。

## 对话记录

- 研发阶段：在 `docs/conversations/` 中手工保存项目对话，保留原始表述，并明确区分原文与执行摘要。
- 运行阶段：核心服务自动记录收到的原文消息及状态变更。宿主需显式传入用户／助手／工具／系统消息，当前不自动抓取外部聊天。
- 对话记录与需求说明应同步维护，避免仅留下结论而丢失人工调整过程。
- 对话记录范围是可见的交互内容，不包含 Agent 的隐藏思考、认证凭据或其他机密运行数据。

## 仓库结构

```text
AgentGranule/
├── readme.md / AGENTS.md
├── pyproject.toml
├── src/agentgranule/       核心与 CLI
├── tests/                 领域与接入验证
├── .github/workflows/     跨平台 CI
└── docs/                  蓝图、审批流程与研发对话
```

## 后续讨论方向

1. 问题、子问题、处理方向与粒度设置之间的关系。
2. 人工交互设置、修改粒度的流程，以及 Agent 对设置的执行与反馈。
3. 对话与设置变更的记录方式。

模块／YAML 与 MCP 以独立功能 PR 交付；下层功能完成审批和集成验收后，再请求合入框架及 main。
