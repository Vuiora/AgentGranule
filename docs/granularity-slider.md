# 粒度小弹窗

按 [PR #7 的维护者批注](https://github.com/Vuiora/AgentGranule/pull/7#issuecomment-5912236674) 返工：取消网页界面，改为原生小弹窗，优化配色、留白、提示和按钮，只显示详细程度滑块，不展示列举数目模块。#7 已关闭且未合并；返工仍在 codex/granularity-slider 分支。

[返工 PR #8](https://github.com/Vuiora/AgentGranule/pull/8) 已提交，等待人工审批当前 head SHA。

本轮在 `codex/decimal-granularity` 新增设计力度模式，遵循用户确认的 0.00–1.00 范围、0.01 步长；基于仍待合并的 #8，单独审批。3D 模块展示属于 [下一轮 TODO](module-design-3d-todo.md)。

## 使用

Python 3.11+，运行环境须包含 Tkinter；本地验证所用 Python 已支持。执行：

```sh
python -m agentgranule.slider --database .agentgranule/project.sqlite3
```

打开居中的 460×438 小弹窗，选择已有模块与方向，滑动到“简要／标准／详细”，点击“确认粒度”保存并关闭。按 Esc、取消或关闭按钮不写入设置。没有模块时先用 Skill／CLI 创建任务模块。已安装时可用 agentgranule-slider 命令。

## Skill 调用

```sh
python -m agentgranule.slider --database ABSOLUTE_DATABASE_PATH --module-id ACTUAL_MODULE_ID --direction explanation --output-file ABSOLUTE_NEW_CHOICE_FILE
```

宿主保留进程等待真实用户操作：成功退出且 status=saved 才取得人工确认，control 为已经写入的设置，不重复提交；status=cancelled 表示未取得新选择。出错退出 2，不能当作默认授权。每次使用新结果文件，不读取上次留下的选择。详细契约见 [Skill 参考](../.agents/skills/agentgranule-workflow/references/contract.md#本地粒度弹窗)。远程或无显示环境明确说明弹窗不可用，改为对话询问，不启动浏览器或伪造选择。

默认模式仅修改 detail_level=brief/standard/detailed，其他已有参数原样保留。自定义详细程度显示原值，确认后才替换为所选级别。不提供数目、深度、新模块创建控件；核心旧接口保持兼容。

## 两位小数设计力度

调用时指定参数，仍为原生小弹窗与单个滑块：

```sh
python -m agentgranule.slider --database ABSOLUTE_DATABASE_PATH --module-id ACTUAL_MODULE_ID --direction design --parameter design_effort --output-file ABSOLUTE_NEW_CHOICE_FILE
```

从 0.00 到 1.00，每步 0.01，预览始终显示两位小数。滑块内部使用 0–100 整数刻度，保存时转换为 JSON 数字，避免连续拖动的浮点累积误差。仅修改 parameters.design_effort，已有 detail_level、count、max_depth 和自定义参数保留；默认模式仍仅修改 detail_level。

未分配时显示 0.50 预览及“未分配”提示，不自动保存或当作人工决定。0.00 是合法最低力度，当前仍执行任务。单模块力度独立，不要求总和为 1。关闭／取消或加载失败不产生新设置，旧快照须重新加载后确认。既有数据库中超界或精度非法的 design_effort 不自动舍入，需调用者通过对话／API 显式修正。

API／CLI／MCP 入口同样校验 design_effort 是有限数字，拒绝布尔、字符串、超界和不在 0.01 网格的值（例如 0.375）；count/max_depth 仍只接受正整数。JSON 的 0.50 与 0.5 等价，UI 负责两位显示。

## 一致性与记录

使用与 MCP 相同的数据库路径。保存 source/revision/完整快照检查与设置更新共用 SQLite 事务，旧弹窗不能覆盖其他入口的新选择；重新加载后再确认。操作者为 human:popup，保存真实选择及变更事件。相关任务与后代失效，无关结果保留；实际语义重算仍由宿主执行。

## 验证

70 项测试通过：原有 61 项回归及 9 项小数粒度测试，覆盖全部 101 个可选值的持久化／编译、宿主 Decimal 精度变化、API 与默认值校验、整数约束不变、参数保留、非法选项无写入、真实 MCP／CLI 小数往返、设置变化的局部重算、过期请求拒绝及旧版请求失效。

真实 Tk 窗口初始化检查：460×438、单个 Scale、无数目文本控件，控件布局未超出窗口；关闭返回 cancelled。验证使用独立验收数据库，不改变真实任务选择。已通过 Skill quick_validate 和命令 --help；没有声称通过原生鼠标拖动／人工确认的端到端测试。

小数模式同样完成真实 Tk 初始化与控件检查：460×438、一个整数刻度滑块，遍历 101 个刻度验证对应的两位小数预览，未分配提示可见且布局不越界；关闭返回 cancelled，验收数据库的设置与事件均无变化。这是程序驱动的隔离窗口检查，不代表已取得真实用户的设计力度分配。

此前 docs/assets/granularity-slider.jpg 是已拒绝的网页历史验收图，只供历史记录参考，不代表当前弹窗。
