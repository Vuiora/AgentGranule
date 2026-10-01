# 第一轮算法开发

分支：`feature/algorithm-round-1`，基线为已由人工合并的 `main`（be6a35b）。用户确认本轮范围：模块依赖排序、粒度约束编译、执行调度、设置变化后的局部重算。

## 输入与规划

任务用 `Task(id, module_id, direction, depends_on=())` 表示。模块划分和依赖由宿主提供，不将问题限定为 A／B 或分类。每个任务使用自己模块／方向的有效粒度，沿用模块覆盖 > 项目方向默认 > 内置建议。

`topological_order` 使用 Kahn 算法；就绪任务按 id 的字典顺序执行，同一图输入顺序不同也得到相同计划。拒绝重复 id、重复依赖、未知依赖和循环。排序复杂度为 O((V+E) log V)，存储为 O(V+E)。

开始运行前检查所有模块属于同一会话，且每个方向已有显式注册的处理函数。未通过预检不执行处理函数，也不创建领域计划。

## 粒度编译与执行

`compile_constraints` 将参数编译成 item_count、detail_level、max_depth、design_effort、output_kind 和 custom，保留非数目型详细程度及自定义参数。参数对象整体替换，编译时不补入隐含数目或改写自定义详细级别。design_effort 为 0.00–1.00、步长 0.01 的人工相对力度；未分配时编译为 null，独立于详细程度标签，服务端不衡量语义质量。

小数参数版本将 AlgorithmRunner 缓存版本升级为 round-1-v2、Workflow 请求版本升级为 2；旧缓存与旧版请求／结果在下次运行时失效，重新派发时提供新的 constraints 字段。历史事件仍保留，过期请求不能提交。

```python
from agentgranule import AlgorithmRunner, Project, Task

with Project() as project:
    session = project.create_session("模块分析")
    module = project.add_module(session, "说明")
    project.set_granularity(module, actor="human", direction="explanation",
                            parameters={"detail_level": "detailed", "max_depth": 2})
    runner = AlgorithmRunner(project)
    # 示例函数；实际宿主应根据请求提供真实处理逻辑。
    runner.register("explanation", lambda request: {"text": "示例说明"}, version="v1")
    result = runner.run(session, [Task("explain", module, "explanation")],
                        context={"question": "当前用户任务"})
```

处理函数收到 `{task, plan, constraints, inputs, context}`，其中 inputs 为直接依赖的实际输出。函数必须在声明版本、上下文、参数及依赖输入不变时具有稳定行为；外部数据变化应放入 context 或修改版本。替换同一 runner 中的函数必须修改版本。

结果支持 `{items: [字符串, ...]}` 或 `{text: 字符串}`；可附加 `details: [{text, children?}, ...]` 的显式层级。根节点深度为 1，max_depth 是上限，不要求创造节点。算法校验数目、JSON 格式、详情节点和深度，核心同时确认计划版本仍有效。detail_level 与自定义参数的语义由宿主处理函数实现，不声称自动衡量文本质量。

调度为顺序执行；失败任务不会进入缓存，其后继标记 blocked，无关任务继续。任务开始／完成／失败／缓存复用／失效及运行结束均记入核心事件存储，保留实际输入和输出。不可序列化的返回值用明确标记记录；不调用 repr 记录未知对象或隐藏数据。宿主仍负责显式传入用户／助手的可见消息。

## 局部重算

成功结果持久存入 SQLite algorithm_cache。SHA-256 指纹包含算法版本、任务定义、模块／方向的参数、来源和版本、处理函数版本、外部 context、直接依赖指纹。

- 修改模块粒度或实际使用的默认值：该任务及其后继重算。
- 修改默认值，但模块已人工覆盖：保留该任务缓存。
- 修改无关模块／方向：保留不相关任务缓存。
- 修改依赖关系或处理函数版本：该任务及后继重算。
- 修改全局 context：所有任务重算，因为它属于所有处理函数的输入。
- 数据库重新打开后仍能复用成功缓存。

运行期间重新检查依赖及其祖先的设置。人工修改使输入失效时阻止后续处理或拒绝结果；交付前再次检查全部输出。`tasks` 保留诊断与失败内容，只有 `outputs` 中 completed／cached 的有效结果可供使用，`complete=false` 表示不能当作完整工作流成功交付。交付后的设置变化在下一次运行重新检测。

初版新鲜度检查遍历依赖祖先；总执行成本还包含处理函数与校验成本，不声称整个运行器都达到拓扑排序的复杂度。缓存针对确定输入，不替代外部工具的副作用确认；有外部副作用的函数不应直接使用此结果缓存作为执行凭据。

## 验证与演示

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python examples/algorithm_round_1.py
```

离线演示从明确给定的示例事实选择优缺并生成汇总：首次三个任务执行，再次三个均缓存；人工把优点从 2 改为 3 后，优点与汇总重算，缺点复用。事实不足会报错，不凑数。示例不接入模型或外部服务。

本轮使用 Python 模块接入。读取基线时 main 的实际文件树只有核心服务，未包含此前功能 PR 的 YAML／MCP 适配器文件；本轮没有自动恢复那些文件，算法交付不假定它们存在。

合并通过单独算法 PR 申请进入 main，仍待人工审批；不执行自动合并。

交付 PR：[算法第一轮 #5](https://github.com/Vuiora/AgentGranule/pull/5)。代码提交 5a40499 的 41 项测试在 Windows／Linux × Python 3.11／3.14 全部通过：[CI 证据](https://github.com/Vuiora/AgentGranule/actions/runs/36701400117)。后续对话及交付文档提交仅更新文档，审批以 PR 当前 head SHA 为准。
