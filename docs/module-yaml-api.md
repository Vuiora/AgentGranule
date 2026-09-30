# 模块 API 与 YAML 套件

安装 `python -m pip install -e ".[yaml]"`。直接调用 `from agentgranule.api import Project, SuiteRunner, load_yaml`。

```python
from pathlib import Path
from agentgranule.api import Project, SuiteRunner, load_yaml

suite = load_yaml(Path("examples/classification.yaml").read_text(encoding="utf-8"))
with Project() as project:
    results = SuiteRunner(project).run(suite)
    print(results["plan"])
```

命令行：`python -m agentgranule run-suite examples/classification.yaml`。可用 `--database PATH` 指定数据库，默认数据保存在被 Git 忽略的 `.agentgranule/`。示例结果是固定演示数据，不是模型推理。

## 抽象粒度与默认值

粒度是各模块处理问题的详细程度，不限定为分类数目。新示例：`python -m agentgranule run-suite examples/event-analysis.yaml`，在事件评估模块中分别设置优点数目、缺点默认数目和原因说明详细程度。

- `add_module(session_id, description, parent_id=None)` 创建模块，不要求固定父子结构。
- `get_granularity(problem_id, direction)` 读取有效参数及来源。
- `request_granularity(problem_id, direction)` 生成宿主应呈现的询问；`question` 可引用并写入对话记录。
- `set_default_granularity(session_id, direction, parameters, actor)` 设置项目方向默认值。
- `set_granularity(problem_id, actor=..., direction=..., parameters=...)` 设置模块覆盖；兼容 count 简写。
- `prepare_plan(problem_id, direction)` 返回 parameters、source、uses_default。
- `submit_result(plan_id, output={items: [...]})` 提交列举结果，`output={text: ...}` 提交其他结果；兼容 categories。

模块覆盖 > 项目方向默认 > 内置建议。参数整体替换。列举方向初始为 3 项、standard；其他方向为 standard。详细程度／深度和自定义参数由宿主执行，核心不判定语义质量。示例回答与结果均为演示数据。

## 套件契约 v1

顶层只有 `version: 1` 与 `steps`；每个 step 包含唯一 `id`、白名单 `operation`、`arguments`。最多 1000 步，嵌套深度最多 64，YAML 输入上限 1 MB。重复键、Python 对象标签、未知字段与未知操作均拒绝。

`{$ref: session}` 引用前序步骤完整返回值，`{$ref: plan.plan_id}` 引用其返回对象的字段。禁止向前引用、未知字段、动态导入和脚本执行。

| operation | 参数 | 返回值 |
| --- | --- | --- |
| create_session | title | session ID |
| add_problem | session_id, description, parent_id（可选） | problem ID |
| add_module | session_id, description, parent_id（可选） | module ID（兼容 problem_id） |
| record_message | session_id, role, content | null |
| get_granularity | problem_id, direction（可选） | 当前参数、来源与版本 |
| request_granularity | problem_id, direction（可选） | 向用户询问的 question 及建议参数 |
| set_default_granularity | session_id, direction, parameters, actor | 项目方向默认及版本 |
| set_granularity | problem_id, actor, direction（可选）, parameters 或 count | 模块方向覆盖及版本 |
| prepare_plan | problem_id, direction（可选） | 参数化处理计划及来源 |
| submit_result | plan_id, output 或 categories | 接受的结果；领域校验拒绝时记录后抛异常 |
| history | session_id | 有序事件列表 |

套件先做结构预检，再按顺序执行。每步事务独立：运行时错误停止后续步骤，前序成功操作保留；重试前读取历史，避免重复写入。结构预检失败不会执行任何步骤；领域参数类型／状态错误属于运行时失败。

人工数目必须由宿主确认，`actor` 不提供身份认证。对话消息通过 `record_message` 显式传入，套件不抓取外部聊天。
