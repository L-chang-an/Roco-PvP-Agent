# Windows 原生 Python 沙箱

状态（2026-09-06 复测）：**当前 120 秒预算下，Windows 隔离、runner、单元测试及两组各 100 次持续查询共 46 项全部通过，本轮未复现历史崩溃或超时。** 本轮使用 `tmp/windows-runtime-current/python.exe`；历史异常根因尚未确定，不能据此声称已完成根因修复。`SANDBOX_ENABLED` 默认仍为 `false`。

## 执行边界

- Windows x64 下 `auto` 选择 `windows`。运行时校验、启动探针或 OS 隔离配置失败即关闭后端，不回退普通子进程；advisor 健康状态动态反映后端故障。
- 每次请求生成独立 AppContainer SID，不注册持久 profile。原生 LowBox token 配合 LPAC opt-out 排除 `ALL APPLICATION PACKAGES` 的隐式授权。
- 仅授予该运行时的随机 capability 和系统 `registryRead` capability，不授予网络 capability。用户私有注册表、宿主数据及其他请求快照不授权。
- 进程以挂起状态创建，通过 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 原子加入 Job；父进程与 runner 都验证 LPAC、禁止子进程策略及 Job UI 限制。
- Job 限制单进程、4 秒用户态 CPU 时间、512 MiB 提交内存，禁止 breakaway；关闭最后一个 Job 句柄即终止工作进程。父进程另按内核 CPU 记账检查预算，执行 120 秒墙钟限制并响应取消。工具调度上限同步为 120 秒；会话剩余时间更短或发生取消时仍会提前结束。
- 每请求创建不可见的独立桌面，配置低完整性标签及最小 DACL；启用全部 Job UI 限制。使用 `DETACHED_PROCESS`，避免启动控制台宿主。
- 显式继承 stdin/stdout/stderr 三个管道句柄；固定最小环境，不继承 API key 等宿主变量。stdout/stderr 分别限制为 64 KiB，返回 JSON 最多 20,000 字符、200 条记录、8 层深度。
- 独立运行时和所选 JSON 快照仅可读。DACL 显式拒绝修改数据、删除、修改所有者和 ACL；临时路径同样不可写，不提供落盘工作区。
- 数据源通过句柄校验并复制，拒绝 UNC、ADS、reparse point 和多硬链接；复制期间持有文件及祖先目录句柄，阻止写入和路径替换。
- 运行时逐文件摘要与 manifest 校验，并将 runner/Win32 helper 摘要绑定到当前应用版本。部署目录、源码及宿主账户属于信任边界；manifest 不是发行者签名，不能防御已控制宿主账户的攻击者。服务期间不得修改运行时。

## 已批准的兼容调整

标准 CPython 的 `_ctypes` 依赖 USER32，pandas 又依赖 ctypes。创建时封锁 Win32k 会导致 DLL 初始化失败，初始化后再设置封锁也在本机返回拒绝访问。用户于 2026-09-06 批准以下调整，生产实现已采用：

1. 保留 LPAC、只读 ACL、Job、禁止子进程和禁网。
2. 增加系统 `registryRead` capability，以兼容 Windows DLL 加载器；不修改宿主注册表 ACL。
3. 移除 Win32k lockdown，改用独立桌面和 Job UI 限制控制桌面对象访问。
4. 禁用 Python 可选的 WMI 探测，采用标准库已有回退；这只是兼容处理，不作为安全边界。

独立桌面不能等价替代 Win32k lockdown 的攻击面缩减。本实现允许加载 GUI DLL，仍可触达更多 GUI 内核接口；它依赖 Windows 内核和对象访问控制，不提供虚拟机边界。

`NtCreateLowBoxToken` 及 LPAC token security attribute 存在系统版本兼容风险。当前实测平台为 Windows 11 x64 build 26200、普通非提权用户；其他 Windows 版本必须运行下述真实验收，API 不可用时保持关闭。

## 准备与验收

在仓库根目录执行。主应用环境应已安装；独立运行时使用 CPython 3.12 和 `sandbox-runtime/uv.lock` 锁定的 NumPy 2.4.2、pandas 3.0.1 及其依赖。

```powershell
uv sync --project sandbox-runtime --python 3.12 --frozen
.venv/Scripts/python.exe -m roco_pvp_agent.sandbox.windows_runtime --source sandbox-runtime/.venv/Scripts/python.exe --destination sandbox-runtime/windows-runtime
```

目标目录必须不存在；准备失败仅清理本次创建的 staging。已有运行时升级时，停止使用旧运行时，准备到新目录，再更新配置；不要覆盖正在服务的目录。应用更新 runner 或 Win32 helper 后必须重建。首次准备和摘要验证会受文件扫描影响，需要等待命令完整结束。

不能将 Windows venv 的 `Scripts/python.exe` redirector 直接配置为沙箱运行时。默认位置为仓库下 `sandbox-runtime/windows-runtime/python.exe`；自定义 `SANDBOX_RUNTIME_PYTHON` 必须使用准备产物的绝对路径。

```powershell
$env:RUN_SANDBOX_INTEGRATION = '1'
.venv/Scripts/python.exe -m pytest tests/platform/test_sandbox_windows.py tests/test_sandbox_runtime.py tests/test_sandbox_windows_unit.py -q
Remove-Item Env:RUN_SANDBOX_INTEGRATION
```

持续运行验收另执行 100 次正常查询，遇到首次失败立即停止：

```powershell
$env:RUN_SANDBOX_INTEGRATION = '1'
$env:RUN_SANDBOX_STABILITY = '1'
.venv/Scripts/python.exe -m pytest tests/platform/test_sandbox_windows_stability.py -q
Remove-Item Env:RUN_SANDBOX_INTEGRATION, Env:RUN_SANDBOX_STABILITY
```

自定义验收路径可设置 `SANDBOX_WINDOWS_TEST_RUNTIME` 为准备产物中 `python.exe` 的绝对路径。此变量只供测试使用。真实测试需要普通本地交互桌面、IPv4/IPv6，以及可绑定的 `127.0.0.2:53`；DNS 测试有宿主解析正向对照。缺少条件会明确失败，不能将跳过算作隔离通过。

只有目标机器完成隔离与稳定性验收后，才在部署环境设置 `SANDBOX_ENABLED=true`、`SANDBOX_BACKEND=windows`。初始化会先执行真实查询及 13 项拒绝访问探针，任一失败都不注册工具。

## 本机验收记录

2026-09-06 在当前代码和 120 秒墙钟预算下复测，未调整 CPU/内存限制、查询内容、次数或失败判断：

| 测试 | 本轮结果 |
|---|---|
| Windows 真实隔离测试 | 15 项通过 |
| runner 契约测试 | 9 项通过 |
| Windows 配置、协议、ABI 和路径检查 | 20 项通过 |
| 连续 100 次正常查询（无诊断插件） | 1 项通过，执行完整 100 次 |
| 原始崩溃诊断（`tmp/test_windows_crash_trace.py`，启用故障栈插件） | 1 项通过，执行完整 100 次 |

表中前四组共 45 项，耗时 332.31 秒，无失败、无跳过。使用的工作进程为 `tmp/windows-runtime-current/python.exe`（CPython 3.12.14 的 20260901 构建），测试宿主为主应用 `.venv/Scripts/python.exe`。原始证据保存在本机 `tmp/windows-recheck-1788663189767/`：`windows-suite.log`、`windows-suite.xml` 和包含运行时/测试文件摘要的 `environment.json`。该目录属于本机诊断产物，不随仓库分发。

原始崩溃诊断随后单独执行，耗时 271.00 秒，1 项通过；命令与用户提供的一致，另添加 JUnit 报告输出。对应日志为同目录下 `crash-trace.log` 和 `crash-trace.xml`。两次运行合计 46 项通过，覆盖 200 次正常沙箱查询；未通过重试过滤失败。

随后执行 `.venv/Scripts/python.exe tmp/runtime_import_control.py tmp/windows-runtime-current/python.exe`，100 个独立的沙箱外进程全部成功导入 NumPy/pandas 并完成 DataFrame 检查，退出码 0，日志为 `dependency-control.log`。此前在沙箱外出现的标准库语法错误在这次对照中也未复现。

真实测试绕过 AST 和 safe builtins，直接调用操作系统接口；覆盖：

| 类别 | 已验证行为 |
|---|---|
| 查询契约 | DataFrame/Series/NumPy 归一化、中文路径、数据摘要和 evidence ID、输出大小与深度限制 |
| 文件与对象 | 宿主私有读写、运行时/快照修改、ACL 修改、宿主命名管道、进程注入和非白名单句柄被拒绝 |
| 网络与注册表 | IPv4/IPv6 TCP/UDP 拒绝；原生 DNS 有本地服务器正向对照；私有 HKCU 读写拒绝 |
| 桌面 | 工作进程处于独立桌面；宿主 Default 桌面、剪贴板及宿主自建窗口消息访问被拒绝 |
| 生命周期 | CPU、内存、墙钟、输出限额；执行中取消；SSE 断开；宿主在挂起/运行阶段退出后 Job 清理 |
| 并发与路径 | 独立 SID、同时存在的请求快照互读拒绝、junction/硬链接拒绝、持有句柄期间文件和祖先替换失败 |

以下是本轮复测之前的历史记录，不代表当前仍能复现。早期 23 项 Windows/runner 用例曾整组通过；随后新增的真实窗口消息用例与路径/契约回归为 33 通过、1 跳过（本机无 symlink 创建权限，另有无需该权限的 junction 实测）。早期还观察到：

- 多轮相关回归曾出现 CPython 原生退出 `0xC0000005`；正常查询压力检查在第 22 次失败，`0xC0000409`，stderr 为 `Fatal Python error: tok_backup: tokenizer beginning of buffer`，发生在 pandas 导入阶段，尚未执行查询代码。
- 仓库内正式的 100 次持续运行测试在第 41 次触发当时的 8 秒墙钟超时并终止工作进程。该记录属于旧预算；之后用户要求将墙钟与工具调度预算调整为 120 秒，原生崩溃记录仍需独立审计。
- 对照了 CPython 3.12.14 的 20260814 与 20260901 两个构建，更新构建未消除异常。新版只安装到仓库 `tmp` 的独立目录，没有替换主应用 `.venv` 或系统 Python。
- 完全不加载沙箱代码、不使用 LowBox/Job/桌面限制的受信任依赖导入对照，也在第二次进程启动时对标准库 `inspect.py` 报出语法错误；固定标准库字节连续编译 1,000 次则通过。已证实异常不只出现在沙箱内，但尚不能确定是解释器构建、依赖还是本机运行环境原因。
- 项目全量回归还在环境模拟路径出现原生崩溃；另有已有 `test_stream_event_sequence_final` 断言期待 4 轮、当前应用默认 100 轮的失败。未为本任务修改该默认值或断言。

此前常规回归为 116 通过、1 跳过、1 未选择（上述已确认的 UI 轮数断言）；本轮未重跑全项目回归。没有改动主应用 `.env`，也没有提交 Git commit。

当前结论是**所列 Windows 验收项目在本轮通过，历史异常未复现，根因尚未确定**。保留历史失败证据，但不再将旧的 8 秒超时或未复现的崩溃表述为本轮仍失败。其他运行时构建及全项目回归不在这份通过结论内；生产启用仍由部署配置显式决定。

## 自行审计崩溃

120 秒预算调整后最初进行了 55 项相关回归；随后本节上方记录的复测已补齐完整 100 次持续查询。以下入口用于继续审计，不能据历史失败认定每次运行必然崩溃。

正式入口为 [`test_real_repeated_normal_queries`](../tests/platform/test_sandbox_windows_stability.py)：`examples` 保存三条正常查询及预期输出，循环每次创建新 Python 进程，首次失败即停止。原始 tokenizer 崩溃来自本机保留的 `tmp/test_windows_crash_trace.py` 的同类循环；第 22 次对应 DataFrame 查询，不代表第 22 次必然触发。

使用本机已准备的对照运行时，并加载只用于测试的故障栈插件：

```powershell
$env:RUN_SANDBOX_INTEGRATION = '1'
$env:RUN_SANDBOX_STABILITY = '1'
$env:SANDBOX_WINDOWS_TEST_RUNTIME = [IO.Path]::GetFullPath('tmp/windows-runtime-current/python.exe')
.venv/Scripts/python.exe -m pytest tests/platform/test_sandbox_windows_stability.py -p tmp.win_pipe_plugin -q --tb=short
```

`tmp/win_pipe_plugin.py` 给子进程添加 `-X faulthandler`，并在非零退出时打印退出码和有界管道内容；该文件及下述对照脚本是本机诊断产物，不随仓库分发。建议保留 pytest 默认捕获，使失败报告包含诊断。

按以下顺序每次只改一个变量，记录查询序号、退出码和 stderr：

1. 将正式测试的 `examples` 缩为一项，例如 `[("emit_result(1)", 1)]`，或只保留 DataFrame/Series 查询。循环次数在 `range(100)` 修改。runner 在执行查询前始终导入 NumPy/pandas，所以标量查询不能排除依赖导入。
2. 在本机 `tmp/runtime_import_control.py` 的 `program` 中保留 `sys.path.insert(...)`，将后续代码依次换成 `print('ok')`、`import numpy; print('ok')`、`import pandas; print('ok')`、原来的 DataFrame 操作。标准输出须仍只有 `ok`。该脚本只运行这些明确编写的受信任代码，不使用沙箱 runner、令牌或 Job，单进程超时也已改为 120 秒：

   ```powershell
   .venv/Scripts/python.exe tmp/runtime_import_control.py tmp/windows-runtime-current/python.exe
   ```

3. 对比旧构建时，将运行时路径改为 `sandbox-runtime/windows-runtime/python.exe`；比较构建应保持查询、依赖版本、预算和轮数不变。
4. 只有返回 `sandbox_resource_limit` 时，再在正式测试构造请求的 `SandboxLimits()` 处单独调整 CPU 或内存，例如 `SandboxLimits(cpu_seconds=120)` 或 `SandboxLimits(memory_bytes=1024*1024*1024)`。这些改动仅限诊断测试，不能把资源终止和 `0xC0000005`/tokenizer 崩溃混为一类。
5. 如需观察内存分配异常，可在本机插件 `traced()` 的启动参数中加 `'-X', 'dev'`。`-I` 会忽略 `PYTHON*` 环境变量，单设 `PYTHONMALLOC=debug` 不会生效。调试选项也会改变时序；一次未复现不能证明修复。

实际执行顺序位于 [`runtime_runner.py`](../src/roco_pvp_agent/sandbox/runtime_runner.py) 的 `_run()`：先设置限制、导入 NumPy/pandas，再 `compile` 和 `exec` 查询。原始故障栈在导入阶段，不能仅凭外层查询断言失败认定是 DataFrame 操作导致。

修改测试的查询、轮数和请求预算不需要重建运行时。修改受信任 `runtime_runner.py` 或 `backends/win32.py` 后必须重新准备到新目录，并更新 `SANDBOX_WINDOWS_TEST_RUNTIME`；直接改已准备的副本会触发完整性校验失败。墙钟预算改为 120 秒不影响运行时文件摘要，本次无需重建。

参考：[Microsoft capability SID 内存管理](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-derivecapabilitysidsfromname)、[Chromium LPAC capability 定义](https://raw.githubusercontent.com/chromium/chromium/main/sandbox/policy/win/lpac_capability.h)、[Chromium Windows LPAC 策略](https://raw.githubusercontent.com/chromium/chromium/main/sandbox/policy/win/sandbox_win.cc)。
