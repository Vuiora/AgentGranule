# 2026-10-03 接口性能回归

日期按Asia/Shanghai。测试对象为`codex/api-performance`的完整性能优化工作区，基于main `ffe339d216e1259020543cc762cedf813ab95378`。

完整198项unittest通过，34.175秒，exit0，无失败／错误／跳过。主代理同时保留进程结果文件，确认正常退出；前日198项／29.425秒的OK日志因中断丢失退出码，不作为这次进程正常退出的依据。

本轮新增9项性能回归，使用查询次数而非毫秒阈值，涵盖1653模块／方向对在连接明确设为999变量限制后的分块读取、1001任务轮询、嵌套参数独立、跨连接最新默认值／覆盖、过期完整快照拒绝、原子回滚、失效与新派发持久化、同锁重用状态后的依赖指纹、事件顺序、旧v2库索引升级和对话原文排序。既有核心、CLI、MCP、原生GL与Qt退出测试均继续通过。

```powershell
$env:AGENTGRANULE_REQUIRE_OPENGL = '1'
Remove-Item Env:QT_OPENGL -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

原始日志与退出结果在`.agentgranule/2026-10-03-api-full-tests.log`、`.agentgranule/2026-10-03-api-full-tests-result.json`。所有测试与基准模拟批准使用隔离数据，不代表实际用户批准或力度保存。基准方法、前后数据与性能适用范围见[接口性能报告](../api-performance.md)。
