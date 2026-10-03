# 2026-10-02 回归测试结果

日期按 Asia/Shanghai 记录。测试对象为当前工作区 `codex/proportional-module-height`，提交 `d0fa7b6da92687bdc71d9821279960e4ec96a221`；本轮未修改实现。

环境：Windows、Python 3.14.7、PySide6 6.11.2、MCP 1.30.0、PyYAML 6.0.3。

| 测试范围 | 通过 | 失败／错误 | 跳过 | 耗时 | 退出码 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 完整回归，默认硬件 OpenGL | 189 | 0 | 0 | 28.911 秒 | 0 |
| 软件 OpenGL：原生 GL | 4 | 0 | 0 | 4.746 秒 | 0 |
| 软件 OpenGL：Qt 交互及独立退出 | 11 | 0 | 0 | 13.106 秒 | 0 |

软件专项复测完整回归中的 15 项，不能把两轮数量相加作为独立用例数。

完整回归覆盖对话／事件存储、模块依赖排序、粒度编译、调度与局部重算、CLI／YAML、实际 MCP stdio 与重启恢复、模块分析、原子保存和修订冲突、0.01 小数粒度及原生界面。

图形专项真实创建 OpenGL 上下文并读取 framebuffer，检查完整物理像素、偶／奇窗口尺寸、旋转、零占比选择和邻模块 GPU 像素隔离；Qt 测试检查当前方向的待提交力度、模块审核、整批复核、取消、加载／GL 失败、过期快照及 python／pythonw 独立进程的正常退出。

两轮均设置 `AGENTGRANULE_REQUIRE_OPENGL=1`，缺少 Qt／可用 GL 会失败，不能跳过后当作图形通过。软件专项另设置 `QT_OPENGL=software`，与硬件回归顺序运行以避免窗口焦点互相干扰。测试使用临时数据库或隔离服务，未自动操作真实项目设置与人工批准。

本轮日志验证 GL ≥ 3.0、depth ≥ 16 bit、stencil ≥ 1 bit、shader 已链接、产生真实帧及窗口×DPR尺寸一致；未输出精确厂商／版本、DPR或实际像素尺寸，不据此声明固定数值或所有后端保证抗锯齿。

完整回归命令：

```powershell
$env:AGENTGRANULE_REQUIRE_OPENGL = '1'
Remove-Item Env:QT_OPENGL -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

软件专项在 `QT_OPENGL=software` 下分别运行 `test_native_gl.py` 和 `test_qt_design_ui.py`。原始日志保存在工作区 `.agentgranule/2026-10-02-full-tests.log` 与 `.agentgranule/2026-10-02-software-ui-tests.log`。
