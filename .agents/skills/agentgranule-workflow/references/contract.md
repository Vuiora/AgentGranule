# 接口契约

在项目根目录使用 Python 3.11+ 安装 `python -m pip install -e ".[mcp]"`。本地 MCP 启动命令为 `python -m agentgranule.mcp_server --database ABSOLUTE_DATABASE_PATH`，通信为 stdio。客户端配置模板见仓库 `examples/mcp-client.json`；替换所有绝对路径后加载。不自动修改用户的客户端配置。

工具名在不同宿主中可能有服务前缀，以工具目录实际名称为准。MCP 成功结果使用 structuredContent：返回标量／列表的核心操作包装在 result 字段；工作流操作直接返回对象。错误走 MCP isError=true；CLI 返回 error 并退出 2。

| 操作 | 参数 | 返回 |
| --- | --- | --- |
| create_session | title | result: session_id |
| add_module | session_id, description, parent_id? | result: module_id |
| record_message | session_id, role, content | result: null |
| request_granularity / get_granularity | problem_id=module_id, direction | parameters, source, revision；request 另有 question |
| set_granularity | problem_id, direction, parameters, actor | 新版本与前值 |
| set_default_granularity | session_id, direction, parameters, actor | 项目默认的新版本 |
| create_workflow | session_id, tasks, context? | workflow_id, order |
| next_task | workflow_id | request / null, statuses, outputs, complete |
| submit_task | workflow_id, request_id, output | statuses, outputs, complete |
| workflow_status | workflow_id | 当前有效进度与输出 |
| history | session_id | result: 按顺序排列的事件 |

任务对象为 `{"id":"summary","module_id":"实际模块ID","direction":"explanation","depends_on":["advantages","disadvantages"]}`。tasks 非空、id 唯一、依赖已存在且无环，所有模块属于同一会话。方向为非空自定义字符串。context 是有限 JSON 对象，作为事实材料和任务要求传给每项任务；不得放入认证机密。

parameters 是非空对象；count/max_depth 为正整数，detail_level 非空字符串；可有其他有限 JSON 参数。优先级：模块覆盖 > 项目方向默认 > 内置建议。列举、分类、优缺方向建议 count=3；其他方向不强制数目，建议 detail_level=standard。默认建议不构成人工回答。

request 包含 request_id、task、description、granularity、constraints、inputs、context。相同有效请求重复 next_task 返回相同 request_id。接受后的 request_id 不可复用。任意上游粒度修订都会使其后代失效；同一参数再次设置也产生新版本。状态有 ready、blocked、dispatched、completed；blocked 表示依赖未完成，不代表项目失败。没有并行领取／独占租约，本轮顺序执行。

输出示例：`{"items":["有依据的第一项","有依据的第二项"],"details":[{"text":"根","children":[{"text":"子层"}]}]}`。details 根深度为 1；max_depth 为上界，不要求人为凑层。服务端机械校验数目、非空格式、深度和版本；不评估语义。

CLI 后备：`python -m agentgranule --database ABSOLUTE_DATABASE_PATH OPERATION JSON_OBJECT`，操作名／参数与上表一致。CLI 所有成功结果统一包在 result 内。为避免 shell 转义问题，可用 Python `subprocess.run([sys.executable, '-m', 'agentgranule', '--database', db, op, json.dumps(arguments, ensure_ascii=False)], check=True, capture_output=True, text=True, encoding='utf-8')`；Windows 设置 PYTHONIOENCODING=utf-8。数据库路径应在工作区或用户授权位置；不依赖宿主工作目录确定 MCP 数据库。


## 本地粒度弹窗

宿主通过当前项目已安装的 Python 启动：

```sh
python -m agentgranule.slider --database ABSOLUTE_DATABASE_PATH --module-id ACTUAL_MODULE_ID --direction explanation --output-file ABSOLUTE_CHOICE_JSON_PATH
```

此命令打开 460×438 的原生 Tk 小弹窗，无网页／HTTP 服务。仅设置 detail_level=brief/standard/detailed，不显示数目或深度模块，保留数据库原有的其他参数。实际确认后保存人工选择并关闭；取消／关闭返回 `{"status":"cancelled"}`，不写设置。成功为 `{"status":"saved","control":{...},"workflows":[...]}`。结果同时输出 stdout，并可保存到指定文件。

每次调用使用新的输出文件，防止读取上次已确认的文件；启动后保留进程会话，等真实用户操作，不由 Agent 点击确认替代人工选择。只有进程成功退出且 status=saved 才作为本次人工选择；控制已写入，不重复提交。取消后可询问用户是否改用对话设置；GUI 不可用明确说明并使用对话入口，禁止自行填写答案。Tkinter 须由 Python 运行环境提供，未安装时会报告错误。

MCP 继续负责记录和任务调度；弹窗由本地宿主启动，不能在无桌面的远程 MCP 服务端伪称向用户显示弹窗。宿主应使用与 MCP 相同的数据库绝对路径。启动前记录呈现给用户的模块、方向及建议，确认结果由核心记录；不得声称它能读取其他聊天。
