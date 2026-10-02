# 分支、人工审批与 milestone

## 分支树

```text
main
└── framework/blueprint                 蓝图与核心框架
    └── integration/extensions          两类接入的集成总分支
        ├── feature/module-yaml-api     模块 API 与 YAML 套件调用
        └── feature/mcp-adapter         MCP 接入
```

## 审批顺序

| 阶段 | 来源 → 目标 | 审批依据 |
| --- | --- | --- |
| 1a | feature/module-yaml-api → integration/extensions | 模块契约、YAML 样例、测试、范围 |
| 1b | feature/mcp-adapter → integration/extensions | 工具契约、stdio 客户端验证、记录边界 |
| 2 | integration/extensions → framework/blueprint | 两个功能 PR 的人工决定、合并后的联合验证 |
| 3 | framework/blueprint → main | 完整蓝图、框架、接入验收与 milestone |

两个功能 PR 可独立决定；人工可以接受、拒绝或要求修改。拒绝某个功能后，集成范围必须在 PR 中明确。后两层 PR 先标记为 draft，待下层完成后再更新最终描述与验收证据。

## 审批规则

[PR #12](https://github.com/Vuiora/AgentGranule/pull/12)曾被维护者关闭，要求修复低分辨率和真正3D展示，没有通过审批记录。本轮继续codex/proportional-module-height返工为原生Qt/OpenGL网格和完整物理像素，完成实际窗口、DPI、GPU隔离、独立进程退出和人工确认回归后重新打开同PR供审查；不创建重复PR，不合并或启用自动合并，审批必须关联新head SHA。

前轮PR #11开发时，用户要求紧凑同图100%占比，并明确仅归一化显示、保留原始力度；在codex/unrelated-module-layout完成并由维护者合并。其布局与渲染审批记录保留，不能代替本轮新代码的审批。

用户最新明确确认“3D 立体高度随占比增大”。交付前核对发现PR #11已由维护者于2026-10-01 20:05（Asia/Shanghai）合并，main为4d7d6848ffeb603f791c4516957a8bb31f735725且包含前分支head。本轮从该main创建codex/proportional-module-height，以[PR #12](https://github.com/Vuiora/AgentGranule/pull/12)追加真实长方体、所属片区裁剪及固定相机，不修改已合并PR的范围；底面归一化与原始力度保存契约不变。新PR继续关联v0.2 milestone，仍须人工审查当前head SHA，不执行合并。

用户最新要求无关联模块的粒度调整不能覆盖其他模块。远端 PR #10 已由维护者合并，main 为 f441101685e9aadfe0056f4b7cd8c52f79923048；本轮从该 main 创建 `codex/unrelated-module-layout`，只修复布局隔离并补充参数／结果隔离回归，以独立 PR 申请合入 main，关联 v0.2 milestone。新 PR 必须按当前 head SHA 重新人工审批，不执行合并或自动合并。

用户进一步要求“实现TODO任务”，在 `codex/module-design-3d` 开发模块分析、原生 3D 与完整人工力度分配。新分支最初基于小数粒度 #9；开发期间维护者已合并 #9（main 合并提交 eff6b6ce27827483e93c96bcdfca3d7329f3f597），已完成包含小数基础功能的 105 项联合验证。因此本轮新 PR 直接以 main 为目标，差异仅包含本轮实现，审批仍关联新 PR 当前 head SHA。Agent 不合并、不启用自动合并。

用户本轮要求新分支 `codex/decimal-granularity` 实现 0.01 小数设计力度，并将模块分析、3D 展示和人工分配写为下一步 TODO。分支创建时基于 `codex/granularity-slider`；开发期间维护者已合并[原生弹窗 PR #8](https://github.com/Vuiora/AgentGranule/pull/8)，并删除父分支。已核对 main 包含父分支提交，新 PR 直接以 main 为目标，差异仅包含本轮实现与规划；新 PR 仍须按当前 head SHA 人工审批。本轮 Agent 不执行任何合并。

Skill/MCP PR #6 已由维护者合并。本轮用户明确更正为分支开发，在 `codex/granularity-slider` 实现本地粒度滑块，以独立 PR 申请合入 main；界面范围仅限滑块设置，所有合并仍须明确人工批准。

算法 PR #5 已由维护者合并。本轮按用户最新要求，从已合并的 main 开 `codex/skill-mcp-workflow`，整体任务 Skill 与 MCP 的衔接作为一个联合验收 PR 申请合入 main，不重建已结束的历史分支树；其他分层开发仍按适用的下层先验收规则执行。

第一轮算法在 `feature/algorithm-round-1` 开发，基于人工合并后的 main，单独 PR 申请合入 main。前置 #1–#4 已由维护者合并；实际 #3 的目标被维护者调整为 main。既有分层流程保留为后续接入开发的规则，新算法分支同样须明确人工批准当前 head SHA。

- 本轮仅创建分支和 PR，不执行任何合并，不启用自动合并。
- 合并须有用户／人工维护者明确批准，可在 GitHub PR 上给出决定，或在项目对话中明确指定 PR 与同意合并；批准关联当前 head SHA，代码变化后重新审批。
- Agent 不以自己的测试结果、其他 Agent 的意见或超时替代人工批准。
- 合并结果和被拒决定写入项目记录；下层合并后重新验证上层，不提前声称集成成功。
- 本文为协作流程约束。GitHub 服务端分支保护是否可用取决于账号套餐；若不可用，须如实说明，不能声称技术上阻止了维护者直接推送。

## Milestone

`v0.2 — 模块分析与同图 3D 人工分配`（[milestone #2](https://github.com/Vuiora/AgentGranule/milestone/2)）关联已合并 PR #10 及本阶段的后续修复，不设置截止日期，本轮不关闭。

建立 `v0.1 — 框架与可审批接入`，关联蓝图／框架、两类接入、集成审批及 main 审批的任务与 PR。未设截止日期，所有审批项保持开放，直到人工决定并完成适用验收。Milestone 在本轮不提前关闭。
