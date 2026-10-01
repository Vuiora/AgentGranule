# AgentGranule

通过人工交互调节各模块处理问题的详细程度，实现项目客制化，并记录完整对话过程。

## 当前状态

第一轮算法 PR #5 已由维护者合并：依赖排序、粒度约束编译、执行调度与局部重算。算法接口与离线演示见 [docs/algorithms-round-1.md](docs/algorithms-round-1.md)。前置 #1–#4 已由人工合并；下表保留其历史审批层级。

[第一轮算法 PR #5](https://github.com/Vuiora/AgentGranule/pull/5) 已合并；其 41 项本地测试与四组合 CI 通过。

[整体任务 Skill](.agents/skills/agentgranule-workflow/SKILL.md) 与 MCP 衔接已通过 [PR #6](https://github.com/Vuiora/AgentGranule/pull/6) 由人工合并，支持人工粒度交互、持久化任务图、依赖调度、结果提交、重启恢复和局部重算；启用见 [接入说明](docs/skill-mcp-workflow.md)。按远端批注返工的原生小弹窗 [PR #8](https://github.com/Vuiora/AgentGranule/pull/8) 已由人工合并，其 61 项本地测试通过；原网页 PR #7 已关闭且未合并。

已由维护者合并的 [PR #9](https://github.com/Vuiora/AgentGranule/pull/9) 新增 `design_effort`：0.00–1.00、步长 0.01，保留原有详细程度选项。

`codex/module-design-3d` 的 [PR #10](https://github.com/Vuiora/AgentGranule/pull/10) 已实现“框架模块分析 → 原生 3D 模块展示 → 调用者人工分配设计力度”完整流程，114 项自动测试通过。静态分析真实源码生成待审核清单，调用者可编辑模块和依赖，在同一张图中旋转／缩放查看半透明模块薄片、逐项分配 0.01 小数力度、审核整份清单并原子保存，再继续工作流。见 [运行说明](docs/module-design-3d.md) 与 [TODO 实施状态](docs/module-design-3d-todo.md)，新功能仍待人工 PR 审批。

框架包含 SQLite 对话与事件存储、可独立或嵌套的处理模块、各方向粒度参数、默认值、人工覆盖、粒度询问、带版本的计划及结果校验；本地界面可通过滑块设置粒度。外部 Agent／模型负责按计划处理，宿主负责将可见对话传入记录。尚未实现模型供应商接入或身份认证。

蓝图见 [docs/blueprint.md](docs/blueprint.md)，分支与审批流程见 [docs/branch-workflow.md](docs/branch-workflow.md)。[Milestone：v0.1 — 框架与可审批接入](https://github.com/Vuiora/AgentGranule/milestone/1)。

| PR | 合并方向 | 状态 |
| --- | --- | --- |
| [#1 模块／YAML](https://github.com/Vuiora/AgentGranule/pull/1) | feature/module-yaml-api → integration/extensions | 已由人工合并 |
| [#2 MCP](https://github.com/Vuiora/AgentGranule/pull/2) | feature/mcp-adapter → integration/extensions | 已由人工合并 |
| [#3 集成](https://github.com/Vuiora/AgentGranule/pull/3) | integration/extensions → main（维护者调整） | 已由人工合并 |
| [#4 框架](https://github.com/Vuiora/AgentGranule/pull/4) | framework/blueprint → main | 已由人工合并 |

本轮概念修正、验证和边界见 [docs/review-rework.md](docs/review-rework.md)；首轮历史证据保存在 [docs/delivery-status.md](docs/delivery-status.md)。

## 运行框架

粒度小弹窗可通过 `python -m agentgranule.slider` 启动，用单个滑块选择简要、标准或详细；确认后接入现有人工设置与局部重算机制，没有网页或列举数目控件。Skill 可直接启动弹窗并读取人工确认结果，见 [弹窗说明](docs/granularity-slider.md)。

设计力度使用 `python -m agentgranule.slider --parameter design_effort`，滑块每步 0.01，始终显示两位小数；API／CLI／MCP 使用 `parameters={"design_effort": 0.37}`。每种模式仅修改所选参数，其他设置保留。数值超界、非数字或不在 0.01 网格上的输入会被拒绝，不自动舍入；JSON 数值的 `0.50` 和 `0.5` 等价。

完整的原生 3D 模块审核与力度分配可在项目目录启动：

所有模块在同一张可旋转的 3D 图中以半透明薄片区域显示，采用维恩图式层叠布局；不使用球体。拖动旋转、滚轮缩放、点击片区或标签选中，重叠处可选择各个模块；力度滑块改变片区面积，0.00 和待分配仍可见。片区重叠不推断模块职能交集。

```powershell
.venv\Scripts\python.exe -X utf8 examples\design_framework.py
```

示例分析当前真实 Python 框架并打开原生窗口，不自动批准模块或力度。已有分析用 `--analysis-id ID` 续作；Skill 也可调用 `python -m agentgranule.design_view --database ABS_DB --analysis-id ID --output-file UNIQUE_NEW_FILE`。核心接入增加 `Design` 分析服务，MCP 共 19 个工具。

需要 Python 3.11+：

```sh
python -m venv .venv
# 激活虚拟环境后执行
python -m pip install -e ".[mcp]"
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
    session = project.create_session("分析某事件的优缺")
    module = project.add_module(session, "事件评估")
    question = project.request_granularity(module, direction="advantages")
    # 宿主向用户呈现 question['question'] 并记录真实回答。
    # 以下数值与文本仅为调用示例：优点 2 项，缺点保留默认 3 项。
    project.set_granularity(module, actor="human", direction="advantages", parameters={"count": 2})
    project.set_granularity(module, actor="human", direction="explanation", parameters={"detail_level": "detailed"})
    plan = project.prepare_plan(module, direction="advantages")
    default_plan = project.prepare_plan(module, direction="disadvantages")
    project.submit_result(plan["plan_id"], output={"items": ["示例优点一", "示例优点二"]})
    events = project.history(session)
```

默认数据库 `.agentgranule/project.sqlite3` 不提交到仓库。原文可能包含项目敏感内容，调用者应在写入前去除认证机密。

## 项目目标

- **记录所有对话内容**：保留用户与 Agent 的交互、需求澄清、人工设置、调整过程及结果，让项目的形成过程可追溯。
- **人工控制处理方向粒度**：问题划分为模块后，用户分别控制每个模块各方向的处理力度，也可以使用可配置的默认值。
- **实现客制化**：Agent 的处理方式与输出细节由人工交互控制的粒度设置驱动。

## 什么是处理方向粒度

**粒度是处理问题的详细程度。** 将一个问题划分为多个模块，每个模块的处理力度可以不同；在一个模块内，不同处理方向也可以分别设置参数。

例如，分析某个事件的优缺时，询问用户希望分别列举多少个优点和缺点，就是把抽象粒度落实为具体数目。解释模块的粒度也可以是简要／详细程度或展开深度。

最初的 **A／子问题 B／分类数目** 是解释这一概念的例子，不规定真实项目必须采用这种任务结构，也不把粒度限定为分类。

实现中的参数对象为 `parameters`：`count` 是列举数目，`detail_level` 表示详细程度，`max_depth` 表示展开深度；可附加宿主理解的其他 JSON 参数。框架负责存储与传递，当前自动校验数目、版本和结果基本格式；其他参数的语义执行由外部 Agent 承担。

`design_effort` 是人工分配的相对设计力度，取值 0.00–1.00，并精确到 0.01；与详细程度标签独立，不要求各模块之和为 1，也不等同于实际工时或模型令牌预算。未分配时不补入隐含力度；弹窗的 0.50 仅供预览，真实确认才生效。0.00 表示最低力度，当前仍执行任务，未来 3D 中保留可见标记。算法和 MCP 请求在 `constraints.design_effort` 传递此值，由宿主处理其语义。

默认优先级为：模块人工覆盖 > 项目方向默认 > 内置建议。分类／列举／优点／缺点方向初始建议为 3 项、standard 详细程度；其他方向为 standard。默认值可通过 `set_default_granularity` 修改，初始建议不代表用户已批准或回答。`request_granularity` 向宿主提供询问及建议来源。

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

第一轮算法以独立 PR 申请合入 main，审批关联当前 head SHA；测试通过不代替人工批准。
