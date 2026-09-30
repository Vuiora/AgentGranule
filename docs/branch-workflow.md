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

- 本轮仅创建分支和 PR，不执行任何合并，不启用自动合并。
- 合并须有用户／人工维护者明确批准，可在 GitHub PR 上给出决定，或在项目对话中明确指定 PR 与同意合并；批准关联当前 head SHA，代码变化后重新审批。
- Agent 不以自己的测试结果、其他 Agent 的意见或超时替代人工批准。
- 合并结果和被拒决定写入项目记录；下层合并后重新验证上层，不提前声称集成成功。
- 本文为协作流程约束。GitHub 服务端分支保护是否可用取决于账号套餐；若不可用，须如实说明，不能声称技术上阻止了维护者直接推送。

## Milestone

建立 `v0.1 — 框架与可审批接入`，关联蓝图／框架、两类接入、集成审批及 main 审批的任务与 PR。未设截止日期，所有审批项保持开放，直到人工决定并完成适用验收。Milestone 在本轮不提前关闭。
