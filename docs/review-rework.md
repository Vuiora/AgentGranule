# PR 批注返工

日期：2026-09-30（Asia/Shanghai）。对应 [PR #4 的审阅](https://github.com/Vuiora/AgentGranule/pull/4#pullrequestreview-5363362179)。

## 批注与处理

| 反馈 | 返工结果 |
| --- | --- |
| A／子问题 B 是示意，不是特指结构 | 模块可独立或嵌套建立；新增 add_module，不要求 A／B |
| 粒度是处理问题的详细程度，各模块力度不同 | 核心按 `(module, direction)` 保存 parameters；支持 count、detail_level、max_depth 及宿主扩展参数 |
| 优缺列举数目是具体表现 | 默认设置和人工覆盖均按方向区分，新增事件优缺与说明示例 |
| 应询问用户列举数目 | request_granularity 返回用户问题、当前建议和来源；宿主负责真实呈现与记录回答 |
| 应设置默认粒度 | 可持久化的项目方向默认，无覆盖时使用；来源明确，未回答不伪造人工设置 |
| 粒度是抽象概念 | 蓝图、README、模块／YAML、MCP 和测试同步改为参数化处理计划 |

## 验证与边界

- 框架本地 21 项、模块／YAML 本地 33 项、MCP 本地 24 项测试通过。
- 测试覆盖独立方向、非数目型详细程度、询问、默认优先级和持久化、失效计划、旧 SQLite 消息与计划迁移及真实 MCP 调用。
- 内置列举方向初始建议为 3 项／standard，其他方向为 standard；这是可替换产品建议，不是用户指定值。
- 默认设置对象与模块覆盖均整体替换，模块覆盖 > 项目方向默认 > 内置建议。
- 数目、版本和结果基本格式可自动校验；其他参数交由外部 Agent 执行，未加入模型供应商、界面或语义质量判定。
- 未合并任何层级分支，联合集成验收仍待人工批准后进行。
- 原审阅批注保留，重新审批以各 PR 当前 head SHA 为准。

## CI 证据

返工代码在 Windows／Linux × Python 3.11／3.14 四组合均通过：

| 分支代码 | CI |
| --- | --- |
| 框架 4271a75 | [通过](https://github.com/Vuiora/AgentGranule/actions/runs/36696018845) |
| 集成 a486540 | [通过](https://github.com/Vuiora/AgentGranule/actions/runs/36696014137) |
| 模块／YAML 4c14a60 | [通过](https://github.com/Vuiora/AgentGranule/actions/runs/36696007352) |
| MCP d2b34de | [通过](https://github.com/Vuiora/AgentGranule/actions/runs/36696009169) |

四个 PR 已恢复 OPEN；#3、#4 保持 draft。当前没有任何层级合并，main 仍为 0028c7c。框架的后续原文／交付文档提交会改变 head，审批以实际 PR 当前 head 为准。
