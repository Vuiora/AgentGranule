# v0.1 本轮交付状态

日期：2026-09-30（Asia/Shanghai）。本轮完成蓝图、框架和两个独立功能分支，未进行任何审批阶段的合并。

| 分支 | 已验证代码提交 | 本地测试 | CI（Windows／Linux × Python 3.11／3.14） |
| --- | --- | --- | --- |
| framework/blueprint | c556b6e | 11 项通过 | [四组合通过](https://github.com/Vuiora/AgentGranule/actions/runs/36686849859) |
| integration/extensions | ca4f413 | 继承相同核心 | [四组合通过](https://github.com/Vuiora/AgentGranule/actions/runs/36686919730) |
| feature/module-yaml-api | bfa71b7 | 21 项通过（11 核心 + 10 套件） | [四组合通过](https://github.com/Vuiora/AgentGranule/actions/runs/36686931326) |
| feature/mcp-adapter | 776dbf4 | 13 项通过（11 核心 + 2 真实 stdio 场景） | [四组合通过](https://github.com/Vuiora/AgentGranule/actions/runs/36686943031) |

表格记录已验证的代码提交；框架后续对话／交付文档提交会改变分支 head，审批时以 PR 当前 head SHA 为准。

## 人工审批

- 第一层：[PR #1](https://github.com/Vuiora/AgentGranule/pull/1) 与 [PR #2](https://github.com/Vuiora/AgentGranule/pull/2)，分别决定是否合入集成分支。
- 第二层：[PR #3](https://github.com/Vuiora/AgentGranule/pull/3)，draft。下层获批并合入后执行联合验收，再请求批准合入框架。
- 第三层：[PR #4](https://github.com/Vuiora/AgentGranule/pull/4)，draft。集成获批并完成后更新完整交付范围，再请求批准合入 main。
- 所有 PR 关联 [milestone #1](https://github.com/Vuiora/AgentGranule/milestone/1)，保持开放且无截止日期。
- `main` 仍为初始化提交 `0028c7c`，没有任何自动合并。
- 审批由 PR、用户明确决定和 AGENTS.md 协作规则执行；已确认 main 的 `protected` 为 false，当前没有 GitHub 服务端强制分支保护，不声称维护者无法直接推送。

## 适用边界

运行时能持久记录传入的原文和操作，宿主仍须将全部可见消息显式回传。人工设置通过 actor 标记审计，未实现用户认证。尚无实际模型供应商或界面。

两个功能尚未合并，联合验收未完成，测试通过不代替人工决定。
