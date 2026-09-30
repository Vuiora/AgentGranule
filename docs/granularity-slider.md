# 粒度小弹窗

按 [PR #7 的维护者批注](https://github.com/Vuiora/AgentGranule/pull/7#issuecomment-5912236674) 返工：取消网页界面，改为原生小弹窗，优化配色、留白、提示和按钮，只显示详细程度滑块，不展示列举数目模块。#7 已关闭且未合并；返工仍在 codex/granularity-slider 分支。

[返工 PR #8](https://github.com/Vuiora/AgentGranule/pull/8) 已提交，等待人工审批当前 head SHA。

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

弹窗仅修改 detail_level=brief/standard/detailed，其他已有参数原样保留。自定义详细程度显示原值，确认后才替换为所选级别。不提供数目、深度、新模块创建控件；核心旧接口保持兼容。

## 一致性与记录

使用与 MCP 相同的数据库路径。保存 source/revision/完整快照检查与设置更新共用 SQLite 事务，旧弹窗不能覆盖其他入口的新选择；重新加载后再确认。操作者为 human:popup，保存真实选择及变更事件。相关任务与后代失效，无关结果保留；实际语义重算仍由宿主执行。

## 验证

61 项测试通过，替换网页测试为原生弹窗服务的 8 项回归：参数保留／不添加 count、旧弹窗拒绝覆盖、并发仅接受一次、非法程度无写入、默认变化使快照过期、工作流局部失效与记录、取消向 Skill 回传且无写入、GUI 错误不产生人工选择。

真实 Tk 窗口初始化检查：460×438、单个 Scale、无数目文本控件，控件布局未超出窗口；关闭返回 cancelled。验证使用独立验收数据库，不改变真实任务选择。已通过 Skill quick_validate 和命令 --help；没有声称通过原生鼠标拖动／人工确认的端到端测试。

此前 docs/assets/granularity-slider.jpg 是已拒绝的网页历史验收图，只供历史记录参考，不代表当前弹窗。
