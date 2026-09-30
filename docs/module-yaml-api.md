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

## 套件契约 v1

顶层只有 `version: 1` 与 `steps`；每个 step 包含唯一 `id`、白名单 `operation`、`arguments`。最多 1000 步，嵌套深度最多 64，YAML 输入上限 1 MB。重复键、Python 对象标签、未知字段与未知操作均拒绝。

`{$ref: session}` 引用前序步骤完整返回值，`{$ref: plan.plan_id}` 引用其返回对象的字段。禁止向前引用、未知字段、动态导入和脚本执行。

| operation | 参数 | 返回值 |
| --- | --- | --- |
| create_session | title | session ID |
| add_problem | session_id, description, parent_id（可选） | problem ID |
| record_message | session_id, role, content | null |
| set_granularity | problem_id, count, actor | 设置及版本 |
| prepare_plan | problem_id | 分类计划 |
| submit_result | plan_id, categories | 接受的结果；错误时抛出异常并记录拒绝结果 |
| history | session_id | 有序事件列表 |

套件先做结构预检，再按顺序执行。每步事务独立：运行时错误停止后续步骤，前序成功操作保留；重试前读取历史，避免重复写入。结构预检失败不会执行任何步骤；领域参数类型／状态错误属于运行时失败。

人工数目必须由宿主确认，`actor` 不提供身份认证。对话消息通过 `record_message` 显式传入，套件不抓取外部聊天。
