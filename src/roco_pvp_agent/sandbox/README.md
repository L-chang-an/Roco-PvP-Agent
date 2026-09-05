# Chat Mode 多环境沙箱查询工具

本文档记录 `sandbox_python_query` 首期实现范围、当前验证状态、尚未完成的工作，以及可直接执行的测试清单。

文档基于 2026-09-04 的代码状态。测试代码根目录为：

```text
/Users/liuzhanyu/workspace/MySelfPlayAgent/tests
```

## 1. 当前结论

首期已经完成公共协议、数据授权、Python 运行器、权限策略、脱敏审计、Dispatcher 取消控制、macOS 后端、Linux/WSL2 后端代码，以及 Chat Mode、Prompt、Skill、UI 健康状态集成。

当前平台状态如下：

| 范围 | 状态 | 说明 |
|---|---|---|
| 公共契约与运行时 | 已完成 | 八个数据集 ID、严格参数校验、统一输入输出协议、结果归一化和稳定错误码均已实现 |
| 权限策略与审计 | 已完成 | AST 前置检查、后端硬隔离、敏感参数脱敏、最小审计字段均已实现 |
| macOS arm64 | 已实现并完成真实 Seatbelt 测试 | 当前开发机真实集成测试为 `4 passed` |
| macOS x86_64 | 代码支持，待真实机器验证 | 不能用 arm64 结果替代 x86_64 安全验收 |
| Linux x86_64/arm64 | 已实现，待目标平台真实验证 | 单元测试覆盖命令构造、seccomp/cgroup 接口和降级逻辑 |
| WSL2 | 与 Linux 共用实现，待真实 WSL2 验证 | 已实现 WSL2/WSL1 识别和危险挂载排除，尚需 Windows 主机专项验收 |
| Docker | 未实现 | 目前只有显式 degraded 占位后端，不会退化成普通 subprocess |

当前已执行结果：

```text
完整默认测试：1195 passed, 5 skipped
macOS 真实沙箱测试：4 passed
本 README 完成后的沙箱专项复验（含 macOS 真实用例）：41 passed, 1 skipped
Linux/WSL2 真实测试：当前 macOS 主机未执行，按平台条件跳过
```

## 2. 首期已经完成的工作

### 2.1 工具契约与数据集最小授权

- 新增严格参数模型 `SandboxPythonQueryArgs`，只接受 `code` 和 `dataset_ids`。
- 数据集 ID 固定为 `full_spirits`、`full_skills`、`valid_skills`、`type_chart`、`families`、`evolution_chains`、`p1_skills`、`p2_skills`。
- 禁止 Agent 传入文件路径、命令、环境变量、Python 依赖、沙箱后端和资源额度。
- `dataset_ids` 不允许重复；未知 ID、额外字段、空代码、重复 ID 和超长代码都在 handler 之前拒绝。
- 数据文件由服务端注册表解析，校验真实路径、普通文件和符号链接，并通过 `O_NOFOLLOW` 读取快照，降低路径穿越、软链接和 TOCTOU 风险。
- 每次请求只为所选数据集创建临时只读快照，不授权整个数据目录。

主要代码：

- `src/roco_pvp_agent/sandbox/schema.py`
- `src/roco_pvp_agent/sandbox/catalog.py`
- `src/roco_pvp_agent/sandbox/models.py`

### 2.2 独立 Python 运行时与结果协议

- 新增独立运行时定义，要求 CPython 3.12、NumPy 2.4.2 和 Pandas 3.0.1。
- 运行器通过 stdin 接收单个 JSON 请求，通过 stdout 返回单个 JSON envelope。
- 用户代码只能通过一次 `emit_result(value)` 产生结果；缺少或多次调用均拒绝。
- 支持 JSON 基础类型、Pandas DataFrame/Series、NumPy ndarray 和 NumPy 标量。
- 最多返回 200 条记录，最大嵌套深度为 8；`NaN`/`Inf` 转为 `null`。
- 拒绝集合、任意 Python 对象等不可序列化结果。
- 用户代码产生的 stdout/stderr 和原始异常不会进入工具结果、SSE 或普通日志。
- 清空宿主环境，仅设置必要 locale、临时目录和单线程计算变量。
- 运行前设置 CPU、地址空间、进程数和文件大小等 `setrlimit`。

主要代码：

- `src/roco_pvp_agent/sandbox/runtime.py`
- `src/roco_pvp_agent/sandbox/runtime_runner.py`
- `sandbox-runtime/pyproject.toml`
- `sandbox-runtime/uv.lock`

### 2.3 权限策略、错误治理和审计脱敏

- 新增纯规则 `SandboxPermissionPolicy`，权限决定只有 `ALLOW` 或 `DENY`。
- AST 策略限制直接导入模块，禁止动态执行、文件 API、危险进程/网络模块和 dunder 属性访问。
- 每个策略决定包含稳定 `rule_id`，便于测试、审计和告警聚合。
- 策略检查只承担前置减面；macOS Seatbelt 或 Linux namespace/seccomp 才是最终安全边界。
- 新增统一错误码：策略拒绝、后端不可用、超时、资源限制、沙箱违规、执行错误、输出非法和输出超限。
- 只有普通语法/数据处理错误保留一次模型修复预算；策略拒绝、超时、资源违规和后端不可用不可重试。
- 审计只记录 run ID、平台、数据集 ID、代码长度和 SHA-256、规则 ID、数据指纹、执行指标及结果摘要哈希。
- UI、SSE、`ChatReply.tool_calls` 和普通日志均不保存或展示代码原文。

主要代码：

- `src/roco_pvp_agent/sandbox/policy.py`
- `src/roco_pvp_agent/sandbox/service.py`
- `src/roco_pvp_agent/tooling/dispatcher.py`
- `src/roco_pvp_agent/tooling/registry.py`

### 2.4 Dispatcher 取消和进程清理

- 为工具执行增加内部 `ToolExecutionControl`，传递有效截止时间与取消事件。
- 外层工具超时、Chat 总预算终止或客户端取消时，Dispatcher 会通知沙箱后端取消。
- 后端在独立进程组中启动沙箱，超时或取消时终止整个进程组，并等待清理宽限期。
- stdout/stderr 使用有界采集，避免用户代码通过超大输出消耗主进程内存。
- 全局信号量限制同时运行的沙箱请求数，默认最大并发为 2。

主要代码：

- `src/roco_pvp_agent/tooling/dispatcher.py`
- `src/roco_pvp_agent/sandbox/backends/base.py`
- `src/roco_pvp_agent/sandbox/service.py`

### 2.5 macOS Seatbelt 后端

- 使用 argv 直接调用 `/usr/bin/sandbox-exec`，没有 Shell 拼接。
- 每次请求生成独立、默认拒绝的 Seatbelt profile。
- 只读开放独立 Python 运行时、必要动态库和本次授权的数据快照。
- 只写开放随机临时目录，禁止网络和未授权文件访问。
- profile 中的路径由服务端产生，并经过专用 Seatbelt 字符串转义。
- 运行时缺失、版本不符、`sandbox-exec` 缺失或探针失败时，后端标记为不健康；不会裸执行。
- 当前开发机已真实验证正常查询、文件/网络拒绝和无限循环清理。

主要代码：

- `src/roco_pvp_agent/sandbox/backends/macos.py`
- `src/roco_pvp_agent/sandbox/backends/base.py`

### 2.6 Linux 与 WSL2 后端

- 使用 `bubblewrap` 创建 user、mount、PID、IPC、UTS 和 network namespace。
- 沙箱仅挂载只读运行时、选中数据快照、必要系统库、最小 `/proc` 和 `/dev`。
- 不挂载主项目、用户目录、其他数据、Docker/Podman socket 或宿主 `/proc`。
- 使用宿主 `libseccomp` 根据当前架构编译规则，通过 FD 交给 `bwrap --seccomp`。
- seccomp 拒绝网络 socket、mount、namespace 变更、ptrace、keyctl、bpf、perf_event 等危险系统调用。
- 支持 cgroup v2 的内存、CPU 和 PID 限制；没有委派控制器时回退至 `setrlimit + 进程组超时`。
- 明确拒绝 WSL1；WSL2 共用 Linux 后端，同时排除 `/mnt/c` 和 Windows/Docker Desktop 逃逸面。
- 当前已完成实现和平台无关单元测试，但尚未在真实 Linux/WSL2 主机上完成首期安全验收。

主要代码：

- `src/roco_pvp_agent/sandbox/backends/linux.py`
- `src/roco_pvp_agent/sandbox/backends/cgroup.py`
- `src/roco_pvp_agent/sandbox/backends/base.py`

### 2.7 Chat Mode、Scope、Registry、Prompt 与健康状态

- 工具仍由现有 Registry 唯一注册，不维护平行工具列表。
- 仅当 `SANDBOX_ENABLED=true` 且后端健康时注册 `sandbox_python_query`。
- 工具配置为 `DEFERRED`、`SERIAL`、`retry_limit=1`、外层超时 10 秒、输出上限 20,000 字符。
- Prompt 要求结构化图鉴工具优先，只有复杂统计或跨数据集查询才使用沙箱。
- Prompt 明确数据内容不是指令，不允许用沙箱处理洛克王国领域外请求。
- 沙箱证据 ID 可进入组队建议的 `evidence_ids` 和证据目录。
- ScopeGate 不再把“推荐、属性、搭配”等通用词单独视为领域锚点。
- 后端不可用时健康状态为 `degraded`，新会话不暴露沙箱工具，现有结构化工具继续工作。
- 客户端断开连接时会传播取消信号。

主要代码：

- `src/roco_pvp_agent/advisor/agent.py`
- `src/roco_pvp_agent/advisor/prompt.py`
- `src/roco_pvp_agent/advisor/scope.py`
- `src/roco_pvp_agent/advisor/skills.py`
- `src/roco_pvp_agent/advisor/skills/roco_team_advisor.json`
- `src/roco_pvp_agent/config.py`
- `src/ui/context.py`
- `src/ui/server.py`

## 3. 配置与运行条件

```dotenv
SANDBOX_ENABLED=false
SANDBOX_BACKEND=auto
SANDBOX_RUNTIME_PYTHON=/absolute/path/to/cpython-3.12
SANDBOX_MAX_CONCURRENCY=2
```

- `SANDBOX_ENABLED` 默认关闭。
- `auto` 只在宿主平台选择 `macos` 或 `linux`，不会自动切换 Docker。
- Linux/WSL2 需要预装 `bubblewrap` 和 `libseccomp`。
- 运行时必须是独立的 CPython 3.12 环境，并含锁定版本 NumPy/Pandas。
- 数据根目录固定来自应用的 `environment.dataset.DATA_DIR`，不能由 Agent 参数或生产环境变量覆盖。

开发环境可按 `sandbox-runtime/README.md` 创建独立运行时。完成后必须显式将该解释器绝对路径配置给 `SANDBOX_RUNTIME_PYTHON`。

## 4. 尚待完成的工作

### 4.1 首期发布前必须完成

1. 在 macOS x86_64 真实机器执行完整 conformance suite，确认 Seatbelt profile 和动态库最小授权没有架构差异。
2. 在 Linux x86_64 与 arm64 真实机器执行 bwrap、seccomp、cgroup/rlimit 和进程残留测试。
3. 在 WSL2 self-hosted Windows runner 执行 Linux conformance suite，并完成 `/mnt/c`、`cmd.exe`、`powershell.exe`、`wsl.exe`、Docker Desktop socket 专项逃逸验证。
4. 将上述平台任务加入 CI；安全验收任务不能用 fake backend 或 mock 替代。
5. 使用确定可连接的本地 TCP/UDP/Unix Socket 测试服务强化网络隔离测试。当前 macOS 网络用例能确认用户代码无法成功访问目标，但还需排除“目标本身未监听”造成的假阳性。
6. 增加父服务被强制终止后的 `die-with-parent`/孤儿进程检查，并在真实 Linux/WSL2 上记录 PID 清理证据。
7. 增加磁盘灌写、内存耗尽、fork bomb、DNS、UDP、Unix Socket、Docker/Podman socket 的逐平台真实攻击测试。
8. 完成生产部署说明，包括 Linux user namespace、seccomp、cgroup v2 delegation 和 WSL2 文件系统位置的检查与修复指引。
9. 完成小流量灰度、错误码/资源违规监控，以及检测到逃逸或残留进程时立即关闭后端的运维开关演练。
10. 增加真实 SSE 客户端中途断连测试，验证从 HTTP 连接、ChatContext、Dispatcher 到沙箱进程的整条取消链；当前已有 Dispatcher/Service 层取消测试，但没有端到端断连用例。

### 4.2 后续 Docker 里程碑

当前 `DockerSandboxBackend` 只返回明确的 `docker_backend_not_implemented` 健康原因。后续需要：

1. 构建 CPython 3.12、NumPy/Pandas 锁定版本的 amd64/arm64 镜像。
2. 以不可变镜像 digest 启动一次性容器，不复用执行过用户代码的容器。
3. 配置非 root、只读根文件系统、`network=none`、`cap-drop=ALL`、`no-new-privileges`。
4. 仅挂载选中数据快照且只读，工作目录使用限额 tmpfs。
5. 配置 CPU、512 MB 内存、16 PID、默认 seccomp 和 8 秒墙钟超时。
6. 禁止挂载 Docker socket、应用目录、凭据和宿主环境变量。
7. 通过与 macOS/Linux 完全相同的 conformance suite 后才将 Docker 标记为健康。

### 4.3 建议的持续加固项

- 为独立运行时产出 SBOM、依赖签名和部署时完整性校验。
- 对每个后端增加版本兼容矩阵，防止 macOS、bubblewrap 或 libseccomp 升级导致策略漂移。
- 对数据快照和临时目录增加故障注入测试，例如进程崩溃、磁盘满、权限突变和清理失败。
- 定期运行逃逸回归集，并将安全用例失败设为发布阻断条件。
- 评估将可信 `game_data` 辅助逻辑拆成独立安装包；当前功能由最小运行器内建，不依赖主应用包。

## 5. 测试执行方式

### 5.1 沙箱专项测试（默认不执行真实平台用例）

```bash
.venv/bin/python -m pytest \
  tests/test_sandbox_contract_policy.py \
  tests/test_sandbox_runtime.py \
  tests/test_sandbox_service.py \
  tests/test_sandbox_backends.py
```

### 5.2 macOS 真实 Seatbelt 测试

```bash
RUN_SANDBOX_INTEGRATION=1 \
.venv/bin/python -m pytest tests/platform/test_sandbox_macos.py -q
```

测试固定使用仓库内的 `sandbox-runtime/.venv/bin/python`。预期：macOS 且该运行时满足要求时全部通过；非 macOS 平台跳过。

### 5.3 Linux/WSL2 真实测试

```bash
RUN_SANDBOX_INTEGRATION=1 \
.venv/bin/python -m pytest tests/platform/test_sandbox_linux.py -q
```

测试固定使用仓库内的 `sandbox-runtime/.venv/bin/python`。预期：受支持 Linux/WSL2 且 bwrap、libseccomp、user namespace 可用时全部通过；能力缺失必须报告 skipped/degraded，绝不能裸执行。

### 5.4 完整回归测试

```bash
.venv/bin/python -m pytest
```

默认情况下真实平台测试由 `RUN_SANDBOX_INTEGRATION=1` 控制；未开启时应被显式跳过。

## 6. 详细测试清单

下面的“预期结果”描述断言目标，不以当前机器是否具备目标平台能力替代真实验收。

### 6.1 契约、数据注册表和权限策略

| ID | 测试代码完整路径与用例 | 测试目的 | 前置条件 | 预期结果 |
|---|---|---|---|---|
| CP-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_query_contract_is_strict_and_dataset_ids_are_unique` | 验证严格参数、未知字段拒绝和数据集唯一性 | 无 | 合法参数通过；未知数据集、额外字段、空/超长代码和重复 ID 均触发 Pydantic 校验错误 |
| CP-02 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_all_eight_registered_datasets_resolve_from_packaged_root` | 验证八个固定数据集都能由服务端注册表解析 | 仓库数据文件完整 | 八个 ID 均解析为数据根目录内的普通文件，不接受调用方路径 |
| CP-03 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_catalog_rejects_dataset_symlink` | 防止通过符号链接跳出授权数据根 | 测试文件系统支持 symlink | 注册表拒绝符号链接并返回沙箱违规，不读取链接目标 |
| CP-04 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：`import os`） | 禁止导入 OS 能力 | 无 | 决策为 DENY，规则 ID 为 `SBX-005` |
| CP-05 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：`open('/etc/passwd')`） | 禁止直接文件访问 | 无 | 决策为 DENY，规则 ID 为 `SBX-007` |
| CP-06 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：dunder 属性） | 禁止通过 dunder 属性探索对象或运行时 | 无 | 决策为 DENY，规则 ID 为 `SBX-006` |
| CP-07 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：`pd.read_csv`） | 禁止借助 Pandas 文件 API 绕过 `game_data` | 无 | `pd.read_csv` 被拒绝，规则 ID 为 `SBX-007` |
| CP-08 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：`eval`） | 禁止动态求值 | 无 | `eval` 被拒绝，规则 ID 为 `SBX-007` |
| CP-09 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：两次 `emit_result`） | 在执行前阻止明显的多次结果提交 | 无 | 两次直接调用 `emit_result` 被拒绝，规则 ID 为 `SBX-008` |
| CP-10 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：缺少 `emit_result`） | 要求用户代码明确返回结构化结果 | 无 | 缺少 `emit_result` 被拒绝，规则 ID 为 `SBX-008` |
| CP-11 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_denies_unsafe_or_invalid_code_with_stable_rule`（参数：语法错误） | 验证无法解析的 Python 代码不会执行 | 无 | 决策为 DENY，规则 ID 为 `SBX-003` |
| CP-12 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_policy_allows_readonly_numpy_pandas_query` | 验证合法只读统计代码不会被误拦截 | 无 | 只导入 `game_data`、NumPy、Pandas 的查询得到 ALLOW |
| CP-13 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_contract_policy.py)::`test_unhealthy_backend_is_a_hard_deny` | 后端不健康时禁止执行 | fake unhealthy backend | 决策为 DENY，返回稳定后端不可用规则，不调用执行器 |

### 6.2 可信运行器和输出归一化

| ID | 测试代码完整路径与用例 | 测试目的 | 前置条件 | 预期结果 |
|---|---|---|---|---|
| RT-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_normalizes_dataframe_numpy_nan_and_record_limit` | 验证 DataFrame/NumPy/NaN 归一化及 200 条上限 | 测试 Python 可启动运行器 | DataFrame 转 JSON 记录；NaN 转 `null`；只返回前 200 条且 `truncated=true` |
| RT-02 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_suppresses_user_stdout_and_exception_details` | 防止用户输出和原始异常泄露 | 同上 | stdout/stderr 不进入 envelope；只返回稳定的 `sandbox_execution_error` 和脱敏信息 |
| RT-03 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_requires_exactly_one_runtime_emit` | 运行时再次强制 exactly-once 结果提交 | 同上 | 条件分支导致零次或多次 emit 时返回 `sandbox_output_invalid` |
| RT-04 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_denies_ungranted_dataset_id` | 防止代码加载请求未授权的数据集 | 仅授权一个数据集 | 加载其他数据集失败，结果不包含目标文件内容 |
| RT-05 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_normalizes_series_array_and_numpy_scalar` | 覆盖 Series、ndarray 和 NumPy scalar | NumPy/Pandas 可用 | 三类值均转换为 JSON 兼容结构和基础数值类型 |
| RT-06 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_rejects_invalid_or_oversized_output`（参数：九层嵌套列表） | 限制结果嵌套深度 | 无 | 深度超过 8 时返回 `sandbox_output_invalid` |
| RT-07 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_rejects_invalid_or_oversized_output`（参数：set） | 拒绝未允许的 Python 对象 | 无 | set 等非 JSON/非受支持对象返回 `sandbox_output_invalid` |
| RT-08 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_rejects_invalid_or_oversized_output`（参数：21,000 字符结果） | 限制返回给模型的内容大小 | 无 | 归一化结果超过 20,000 字符时返回 `sandbox_output_too_large` |
| RT-09 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_runtime.py)::`test_runner_rejects_invalid_or_oversized_output`（参数：70,000 字符 stdout） | 限制原始输出，防止主进程内存消耗 | 无 | 原始输出超过 64 KB 时执行被终止并返回 `sandbox_output_too_large` |

### 6.3 Service、Registry、脱敏、重试、取消与并发

| ID | 测试代码完整路径与用例 | 测试目的 | 前置条件 | 预期结果 |
|---|---|---|---|---|
| SV-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_success_envelope_has_evidence_and_only_selected_snapshot` | 验证成功 envelope、证据 ID、数据指纹和最小快照 | fake backend | 返回 `ok=true`、`sandbox:<digest>:<run_id>`；仅传入所选数据；临时快照执行后被删除 |
| SV-02 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_advisor_registry_adds_only_healthy_service_as_deferred_serial_tool` | 验证 Registry 是唯一工具来源和健康注册条件 | fake healthy/unhealthy service | 健康时注册 DEFERRED/SERIAL 工具；不健康时工具不存在，结构化工具不受影响 |
| SV-03 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_dispatcher_redacts_code_from_log` | 防止普通 Dispatcher 日志泄露代码 | 注入日志捕获 | 日志只出现代码长度和 SHA-256，不出现代码原文 |
| SV-04 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_unknown_tool_with_code_argument_is_still_redacted` | 未知/伪造工具调用也不能借 `code` 字段写日志 | 无 | 未知工具错误保持正常；日志中不出现传入代码原文 |
| SV-05 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_chat_reply_and_sse_tool_event_never_contain_code` | 覆盖同步回复和 SSE 事件脱敏 | 测试 Chat/SSE 序列化 | `ChatReply.tool_calls` 和 SSE 工具事件均只含摘要，不含代码 |
| SV-06 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_policy_denial_does_not_invoke_backend_and_is_not_retryable` | 确保策略拒绝无审批/重试旁路 | 计数 fake backend | backend 调用次数为 0；结果为 `sandbox_policy_denied` 且不可重试 |
| SV-07 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_audit_log_contains_only_code_digest_and_length` | 验证最小审计字段 | 内存审计 sink | 审计包含 SHA-256/长度、规则/数据摘要；不包含代码、用户原文、宿主路径或原始异常 |
| SV-08 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_ordinary_execution_error_gets_one_repair_budget` | 验证普通执行错误只有一次修复机会 | 返回执行错误的 fake backend | 第一次结果允许重试；第二次耗尽预算；安全/资源类错误不获得该预算 |
| SV-09 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_dispatch_timeout_signals_backend_and_waits_for_cleanup` | 外层超时后必须协作取消并等待清理 | 可观察取消的 fake backend | Dispatcher 设置取消事件；后端退出；在清理宽限内返回且无后台残留任务 |
| SV-10 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_external_cancel_signals_backend_within_cleanup_grace` | Chat/客户端取消能传播到沙箱 | 外部 cancel event | 后端及时收到取消并停止，调用方在宽限期内完成 |
| SV-11 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_service.py)::`test_global_sandbox_concurrency_limit_is_enforced` | 防止多服务实例绕过全局并发限制 | 多线程 fake backend | 同时活跃沙箱数不超过配置值 2；后续请求在许可释放后运行 |

### 6.4 后端命令、profile、cgroup 和 Docker 降级

| ID | 测试代码完整路径与用例 | 测试目的 | 前置条件 | 预期结果 |
|---|---|---|---|---|
| BE-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py)::`test_linux_argv_has_isolated_namespaces_seccomp_and_selected_mount_only` | 静态验证 Linux bwrap 命令的隔离参数和最小挂载 | fake 平台探针 | argv 含 user/mount/PID/IPC/UTS/network namespace、seccomp 和 die-with-parent；只挂载选中数据；无 Shell 拼接和危险宿主路径 |
| BE-02 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py)::`test_macos_profile_quotes_paths_and_never_adds_unselected_data` | 验证 Seatbelt 路径转义和最小数据授权 | 构造含特殊字符路径 | profile 正确转义；只出现选中快照；未选数据与数据根不会被整体授权 |
| BE-03 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py)::`test_cgroup_without_delegated_controllers_falls_back_without_residue` | 验证无 cgroup 委派时安全回退和清理 | fake cgroup 文件系统 | 不留下 cgroup 目录/状态；回退到 rlimit/超时机制；不会误报已启用 cgroup |
| BE-04 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_sandbox_backends.py)::`test_docker_backend_is_explicitly_degraded_until_followup_milestone` | 防止未实现 Docker 后端被当作可用沙箱 | 无 | 健康状态为 false，原因是 `docker_backend_not_implemented`，服务不注册工具且不裸执行 |

### 6.5 macOS 真实 Seatbelt 集成测试

这些测试只有在 macOS 且设置 `RUN_SANDBOX_INTEGRATION=1` 时执行。

| ID | 测试代码完整路径与用例 | 测试目的 | 前置条件 | 预期结果 |
|---|---|---|---|---|
| MAC-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py)::`test_real_seatbelt_query_returns_structured_result` | 在真实 Seatbelt 中执行合法数据查询 | macOS、`sandbox-exec`、合格独立运行时 | 返回结构化成功结果、macOS backend 名称和正确数据统计，不泄露路径/代码 |
| MAC-02 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py)::`test_real_seatbelt_blocks_host_file_and_network`（参数：读取 `/etc/passwd`） | 验证真实沙箱无法读取未授权宿主文件 | 同上 | 绕过 AST 直接交给后端的文件读取失败，不返回宿主文件内容 |
| MAC-03 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py)::`test_real_seatbelt_blocks_host_file_and_network`（参数：HTTP 请求） | 验证真实沙箱无法建立网络连接 | 同上 | 网络操作不能成功，返回受控失败；发布前还需用确定监听目标强化该用例 |
| MAC-04 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_macos.py)::`test_real_seatbelt_kills_infinite_loop_without_residual_worker` | 验证无限循环能被墙钟/资源限制终止 | 同上 | 返回 `sandbox_timeout` 或 `sandbox_resource_limit`，总耗时小于 2 秒；独立的 PID 残留断言仍属于待补测试 |

### 6.6 Linux/WSL2 真实集成测试

该测试只有在 Linux/WSL2 且设置 `RUN_SANDBOX_INTEGRATION=1` 时执行。

| ID | 测试代码完整路径与用例 | 测试目的 | 前置条件 | 预期结果 |
|---|---|---|---|---|
| LNX-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_linux.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/platform/test_sandbox_linux.py)::`test_real_linux_or_wsl2_query_and_mount_isolation` | 验证真实 bwrap/seccomp 查询和 mount namespace 隔离 | Linux/WSL2、bwrap、libseccomp、user namespace、合格运行时 | 合法查询成功；未授权文件、`/mnt/c` 和 Docker socket 等路径均不可读；后端能力不足时标记 degraded/skip，绝不裸执行 |

### 6.7 关键集成与回归测试

下列测试不是沙箱专项文件中的全部用例，但属于发布前必须保留的集成回归项。

| ID | 测试代码完整路径与用例 | 测试目的 | 预期结果 |
|---|---|---|---|
| RG-01 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_tool_registry.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_tool_registry.py)::`test_sensitive_argument_must_exist_in_tool_schema` | 防止 Registry 的脱敏声明与参数 schema 漂移 | 声明的 `code` 必须真实存在于 schema；错误声明在注册阶段失败 |
| RG-02 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_tool_dispatcher.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_tool_dispatcher.py)::`test_tool_timeout_returns_without_exposing_late_result` | 保持工具超时的稳定结构化结果 | 超时立即返回稳定错误，且不暴露稍后到达的 handler 结果 |
| RG-03 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_tool_dispatcher.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_tool_dispatcher.py)::`test_one_serial_tool_forces_the_entire_batch_to_run_in_order` | 验证 SERIAL 工具所在批次不会并发执行 | 批次按调用顺序串行执行并保持结果顺序 |
| RG-04 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_advisor_scope.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_advisor_scope.py)::`test_classify_ambiguous` | 防止“推荐/属性/搭配”等通用词绕过领域门禁 | 没有洛克王国锚点时只判定为 AMBIGUOUS，不进入 Agent |
| RG-05 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_advisor_scope.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_advisor_scope.py)::`test_agent_scope_route_skips_llm` | 领域外、注入、模糊和问候请求不得进入 LLM 或沙箱 | 返回对应固定模板，LLM 调用次数为 0 |
| RG-06 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_agent_ui.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_agent_ui.py)::`test_health` | 验证健康接口基本契约 | 响应包含应用状态和脱敏 sandbox 状态 |
| RG-07 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_agent_ui.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_agent_ui.py)::`test_enabled_unavailable_backend_degrades_without_registering_tool` | 验证启用但不可用时安全降级 | `/api/health` 为 degraded；Registry 不包含沙箱工具；其他 Chat 功能可用 |
| RG-08 | [`/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_agent_ui.py`](/Users/liuzhanyu/workspace/MySelfPlayAgent/tests/test_agent_ui.py)::`test_stream_event_sequence_with_tool` | 验证 SSE 工具循环的公开事件顺序 | 事件为 meta、progress、tool、progress、reply、done，且不发送原始思维链 |

## 7. 发布验收清单

在某个平台将沙箱工具标记为可用之前，必须同时满足：

- [ ] 该平台和架构的真实 conformance suite 全部通过，不以 mock 结果代替。
- [ ] 选中数据集可读，未选数据集和宿主敏感文件不可读。
- [ ] TCP、UDP、DNS、HTTP、Unix Socket 和容器 socket 均不可用。
- [ ] Shell、子进程、fork bomb、无限循环、大内存、磁盘灌写和超大输出被拒绝或终止。
- [ ] 超时、取消和父进程退出后，1 秒清理宽限内无残留进程和临时文件。
- [ ] UI、SSE、同步响应和审计日志不含代码原文、宿主路径、环境变量和原始异常。
- [ ] 后端能力缺失或探针失败时状态为 degraded，工具不注册，且无普通 subprocess 回退。
- [ ] 完整 pytest 回归通过。
- [ ] 灰度期间的错误率、超时率、资源违规和残留进程指标达到发布阈值。
- [ ] 已验证紧急功能开关能立即阻止新沙箱请求。

## 8. 已知限制

- Windows 原生 Restricted Token/ACL 后端不在本方案内；Windows 只支持 WSL2。
- `sandbox-exec` 是 macOS 首期依赖；系统未来移除时只能 degraded，不能降级裸执行。
- Linux/WSL2 的真实安全强度依赖宿主是否启用 user namespace、libseccomp 和可用的 cgroup/rlimit 能力。
- Docker 后端尚未实现，配置为 Docker 时工具必须保持不可见。
- AST 策略不是安全边界，任何放宽 AST allowlist 的改动都必须同时通过真实 OS 沙箱逃逸测试。
- 当前开发机只能证明 macOS arm64 行为；不能据此宣称 Linux、WSL2、macOS x86_64 或 Docker 已完成安全验收。
