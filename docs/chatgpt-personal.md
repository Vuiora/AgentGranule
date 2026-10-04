# 在个人 ChatGPT 对话中调用 AgentGranule

目标是自己的多个 ChatGPT 对话复用一个私有 AgentGranule 服务。连接后，新对话从工具菜单选择 AgentGranule；继续旧任务时提供真实 session_id。连接和 Skill 加载分别验收，不会自动读取其他聊天。当前自动粒度与项目搜索缓存仍是设计方案。

## 接入与验收记录

2026-10-03：现有 stdio MCP 真实初始化并发现19工具，本轮3项MCP工作流测试通过。官方 Windows x64 tunnel-client v0.0.15 已下载至忽略的 `.agentgranule/chatgpt/tools/`，下载字节的SHA256与GitHub官方release声明一致。尚未取得账号中的真实 tunnel_id、注册的app ID，未通过ChatGPT端发现／调用，也未安装个人插件。

2026-10-04续作：源码边界与既有framework/MCP工作流共23项隔离回归，17项通过，6项真实symlink测试因Windows权限不足跳过；3项真实Windows junction场景均通过。真实stdio再次核对19工具及输入／输出schema与annotations。本地helper预检、固定模板打包与旧文件防覆盖检查、远程Skill格式校验通过；测试ID仅为隔离fixture，未创建真实profile或接入数据库。两个账号页面保持登录，个人组织显示尚无隧道；ChatGPT已准备名称为AgentGranule Personal的Tunnel连接草稿，尚未勾选授权或创建连接。

后续已获用户明确授权“同意创建私有连接并安装”：Platform真实创建AgentGranule Personal隧道并关联当前账号可用工作区，真实本地profile已生成。首次configure暴露官方客户端将Python `-X utf8`误识别为脚本的问题，已改为绝对Python与真实 `scripts/chatgpt_mcp.py` 入口；新增3项隔离回归通过，覆盖官方init预检、19工具及中文／emoji回读、源码边界与已有profile防覆盖。源码与入口回归合计26项，20通过、6项因Windows符号链接权限跳过。运行密钥仍需用户在自己的终端输入，尚未确认隧道在线、ChatGPT工具发现或Skill安装。

用户随后在自己的终端输入运行密钥。实际healthz／readyz均返回200，ChatGPT个人连接详情显示“已连接”；工具详情为18个Write、1个Read，与既有19工具及保守annotations相符。已使用该真实注册app生成完整Skill ZIP并选入网页导入表单，Skill安装和新对话调用继续验收中。

后续第一条新聊天已实际调用create_session、record_message与history，返回真实会话ID与中文／0.37测试消息；根Agent随后读取独立数据库并断言内容完全一致。测试未创建模块图、修改粒度或执行人工审批。完整Skill ZIP导入返回“无法添加插件。请重试”；补充非空longDescription并升级0.1.1的独立r2包仍失败，不能将此页面错误归因为已确认的清单字段问题。正准备官方兼容格式的候选包。第二条独立聊天通过“+”菜单实际选择AgentGranule Personal，但发送时返回cloudflare_challenge；已向用户交接人机验证，跨对话调用尚未验收。

该验证页随后自行恢复，未由Agent点击或绕过。第二条独立聊天实际history调用返回同一会话的2个事件（创建会话与测试消息），内容完全一致，跨对话工具调用已验证。0.1.2候选包采用官方支持的.codex-plugin兼容布局和方形图标，但导入仍返回相同页面错误；继续核对已有私有app引用是否适用此导入入口，不将猜测当作根因。

随后从实际独立[技能页面](https://chatgpt.com/skills)的“添加技能→从电脑上传”导入仅含根SKILL.md与references/remote-contract.md的ZIP。页面明确显示“技能已上传”及“已安装”；远程Skill安装已成功，无需完整插件ZIP导入。安装不会替代既有MCP权限或启动服务，聊天中需同时选择AgentGranule Personal。当前正在新普通聊天验证实际Skill读取和MCP调用。

普通“聊天”模式联合验收：选择app mention会替换先前Skill引用；即使只保留技能入口后切普通聊天，模型也明确无法读取已安装Skill及其references文件。因此不能声称普通聊天加载Skill成功。两种尝试的真实history调用均成功且仍返回2事件。正在保留Skill入口默认“工作”模式核对加载；普通聊天工具可用与Skill执行分别报告。

打包及启动最终联合回归12项全部通过，包含已校验官方客户端的隔离init预检。与源码边界、framework及既有MCP工作流的23项回归累计35项：29项通过，6项真实符号链接因Windows权限不足跳过；真实junction已覆盖。审查修复doctor/run静默忽略配置参数的问题，不重启现有client或修改真实profile。

最终保留技能入口默认“工作”模式，在编辑器的“插件”选择器中选择既有AgentGranule Personal，实际消息同时保留技能和插件引用。模型实际读取已安装SKILL.md及references/remote-contract.md，说明真实图确认、0.00–1.00/0.01力度、完整原文记录契约，并通过私有MCP读取同一测试会话的2事件。Work中Skill与MCP联合验收通过；没有创建图、保存力度、执行审批或新增消息。普通聊天MCP跨对话调用和Work技能执行均已实际验证，当前账号普通聊天读取独立Skill文件未通过，不能泛化为所有Skills或账号的产品限制。

本轮分支 `codex/chatgpt-personal-connection` 基于 `codex/api-performance` 的877a2efe5758df9c0cb25f81cdeed2e90327334f，未合并；现有未提交的API设计与历史记录保留。本轮准备既有服务的私有连接脚本、远程Skill和可选源码扫描边界，不实现新的HTTP服务或模型供应商。

## 连接私有服务

依据 [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)，stdio服务可经出站HTTPS私有隧道连接ChatGPT，不需要开放本机入站端口。隧道和ChatGPT开发者模式的权限分别检查；个人账户使用对应的个人Platform组织，并关联目标ChatGPT工作区。

1. 登录 [Platform隧道设置](https://platform.openai.com/settings/organization/tunnels)，检查可用隧道；新建／修改需要Tunnels Read + Manage，运行／选择需要Read + Use。获得真实 `tunnel_id`。运行时key由你在本地环境提供，不能写进仓库、聊天或配置样例。
2. 从设置页或 [OpenAI官方最新release](https://github.com/openai/tunnel-client/releases/latest) 下载适配系统的完整客户端，校验官方SHA256。现有已校验客户端路径保存在忽略的 `.agentgranule/chatgpt/client.json`；在其他电脑传 `--client` 指定自己校验的可执行文件。精简runtime版本不含init/doctor，不用于首次配置。
3. 在项目根目录执行下列命令。`check`只检查本地导入与版本，不调用云端，也不创建数据库；`configure`需要账号的真实隧道ID，只生成本地profile，不证明账号连接成功。

```powershell
.\.venv\Scripts\python.exe scripts/chatgpt_tunnel.py check
.\.venv\Scripts\python.exe scripts/chatgpt_tunnel.py configure --tunnel-id tunnel_实际账号ID
```

profile保存在 `.agentgranule/chatgpt/profiles/agentgranule-personal.yaml`，密钥字段只引用 `env:CONTROL_PLANE_API_KEY`。默认数据库是独立持久化的 `.agentgranule/chatgpt-personal.sqlite3`，不将现有研发对话数据库用于接入预览；同一配置重启后继续访问该数据库。MCP命令使用绝对路径与正斜杠，避免Windows路径被客户端的转义规则破坏；入口为真实 `scripts/chatgpt_mcp.py` 文件，在脚本内设置标准流UTF-8，兼容官方客户端的Python脚本预检；并传入 `--source-root` 将此连接的源码扫描限定在本项目内，拒绝出界目录与指向外部的文件／目录链接。原本不带该选项的本地MCP行为保持兼容。

源码边界通过解析真实路径检查后再遍历／读取，覆盖Windows junction；它假定链接不会在检查与读取之间被其他进程替换，不构成操作系统沙箱。静态分析返回模块名、公开符号、导入关系与依据摘要，服务不会执行被分析的源码。

4. 在自己的终端环境设置 `CONTROL_PLANE_API_KEY` 后运行下列命令，不将值粘贴到聊天。profile已存在时脚本拒绝覆盖；更换配置前先检查旧profile与真实目标。

```powershell
.\.venv\Scripts\python.exe scripts/chatgpt_tunnel.py doctor
.\.venv\Scripts\python.exe scripts/chatgpt_tunnel.py run
```

Windows也可以在自己的PowerShell执行以下脚本，由 `Read-Host -AsSecureString` 掩码输入对应个人组织的运行密钥。脚本只设置当前脚本进程及子进程环境，不写入用户环境、配置或磁盘；结束时恢复原进程环境。脚本先运行doctor，通过后继续前台run，不把本地profile存在当成在线。

`--python`与`--database`仅用于configure；check、doctor、run提供这些参数会立即报错，不会静默忽略。运行始终使用已有profile。check中的default_database/default_mcp_command只描述本地默认配置，不能代替已有profile的实际设置。

```powershell
& 'C:\Users\Lenovo\Desktop\OGv01\ProjectParticle\scripts\start_chatgpt_tunnel.ps1'
```

`run`在前台持续运行，Ctrl+C停止；需在ChatGPT发现与每次调用时保持运行。健康地址写入 `.agentgranule/chatgpt/health.url`，检查 `/healthz`、`/readyz`，本地 `/ui` 默认仅loopback监听。不要打开raw HTTP日志或远程管理UI来解决连接问题。

5. 在ChatGPT的设置→安全与登录中检查开发者模式，进入 [插件页面](https://chatgpt.com/plugins)，创建个人连接，选择Connection=Tunnel并指定同一真实隧道。检查它发现的是AgentGranule的19个工具。创建连接是实际权限授予，需你确认对独立数据库的读写及服务端源码扫描能力；不将开发模式开关当作审批粒度。

现有stdio服务没有独立OAuth端点，草稿的“身份验证”选择“无需身份验证”；这指MCP服务本身，隧道仍由OpenAI运行时凭据以及组织／工作区权限控制。需要绑定个人组织和目标个人ChatGPT工作区，实际可关联范围以创建页为准。若需要创建或输入新的认证凭据，由用户自行完成，不通过聊天收集密钥。

## 绑定 Skill

MCP工具连接不会自动加载本仓库的 `.agents/skills/agentgranule-workflow/SKILL.md`。远程版Skill位于 `integrations/chatgpt/skills/agentgranule-workflow/`，依赖实际连接；普通ChatGPT通过完整对话确认清单和力度，本地原生3D窗口不会通过隧道传到网页。

本账号已成功安装的路径是独立技能上传：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_chatgpt_plugin.py --skill-archive --output-name agentgranule-personal-skill
```

在[个人技能页](https://chatgpt.com/skills)选择“添加技能→从电脑上传”，上传生成的ZIP；页面当前接受.zip、.skill或SKILL.md，上限25MB。此ZIP仅含根SKILL.md及references/remote-contract.md，保留完整接口契约，不含账号ID、app引用或本地执行配置。检查“技能已上传”和“已安装”列表中的agentgranule workflow。生成脚本只准备文件，不自动安装或声称账号已连接，既有同名目录/ZIP均拒绝覆盖。

下面的完整插件包装供支持其导入或本地marketplace的宿主使用；本账号网页完整插件ZIP导入仍返回错误，未查明根因，当前使用上方已经验证的独立Skill入口。

ChatGPT创建连接后，地址中 `plugin_asdk_app_...` 对应实际app ID `asdk_app_...`。准备包时使用真实ID：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_chatgpt_plugin.py --app-id asdk_app_实际ID
```

脚本生成忽略目录中的 `agentgranule-personal/` 及ZIP，在根 `.app.json` 引用已注册连接；不包含密钥、数据库、Python客户端或本地MCP配置。`integrations/chatgpt/`是构建模板，缺少账号绑定文件，不能直接当已连接插件安装。

按 [官方个人插件打包与安装](https://developers.openai.com/plugins/build/plugins) 使用支持本地marketplace的桌面客户端安装。个人marketplace路径为 `~/.agents/plugins/marketplace.json`，包可放在个人目录，entry的source.path相对于marketplace根。安装到网页／其他surface的可用性必须实测，不能由本地目录存在推断已同步。账号或工作区若提供插件导入，也可导入生成的ZIP；这取决于实际权限，不承诺所有个人账户都有该入口。

本账号2026-10-04的插件页确实提供“上传插件压缩包”入口；已取得真实app ID并从此入口尝试安装完整包，当前导入返回错误。入口存在本身不代表安装成功。

包引用现有app，不声明 `mcp.json` 或 `.mcp.json`；官方说明声明MCP的导入插件会标为Desktop only，引用既有app不会自行创建连接或授予权限。[连接引用与边界](https://learn.chatgpt.com/docs/enterprise/plugin-management)

## 验收与调用

本账号已完成实际接入验收：隧道健康和ready、ChatGPT发现19工具、普通聊天跨对话读写，以及已安装独立Skill在Work新对话读取契约并调用私有MCP。完整图审批与力度保存另需真实人工确认，不能拿预检测试替代。

仅调用已连接工具时，在新聊天点击“+”，选择AgentGranule Personal，再发送任务；这一入口已在本账号实测可选。继续另一条聊天的任务需提供该任务真实session_id，并先调用history；新聊天不会自动取得其他聊天原文。保持本地启动脚本的终端运行，否则工具会离线。

使用已安装技能的实测步骤：

1. 打开[个人技能页](https://chatgpt.com/skills)，选择agentgranule workflow，点击“在聊天中试用”。
2. 保留默认“工作”模式；在编辑器底部“插件”中选择已连接的AgentGranule Personal。实际发送消息应同时显示技能和插件引用。
3. 替换预填示例为自己的真实任务。不要直接发送自动生成的虚构用户需求示例。保持本地隧道终端运行。

本账号当前切到普通聊天后无法读取该独立Skill，因此普通聊天仅将私有工具调用计作通过；需要完整Skill流程时使用以上Work入口。

新任务可写：

> 使用 AgentGranule 分析这个任务，先列出模块供我确认，再由我分配各模块设计力度。

续作可写：

> 使用 AgentGranule，继续 session_id=实际会话ID 的任务，先读取已有图、分配与状态。

若在其他账号仅有MCP连接而Skill未安装或未加载，明确要求按本仓库远程流程执行并提供流程材料；不能把工具发现说成Skill已加载。服务离线、登录失败、无开发者模式／隧道权限或安装入口缺失时记录具体失败，保持未完成状态。
