# M6 — 网页白名单搜索（P1，条件触发，详细实施计划）

> 上级：`chatmode_plan_v2.md` §5 M6　|　依赖：仅 M1（`get_catalog_version`，其返回含 `data_digest`）；独立于 M2–M5
> 状态：**P1 条件触发**，默认关闭。触发条件 = 负责人批准新依赖 + 提供至少一个可信站点白名单。

## 1. 目标

让顾问在“本地资料不足或用户明确要求当前环境资料”时，从**精确白名单内的站点**抓取规范化事实（URL + 采集时间 + 可信等级），并带硬 SSRF/注入防护。**默认白名单为空 = 工具关闭**，不给“能搜全网”的通用能力。

## 2. 前置与依赖

- 新增依赖 `httpx`（**需负责人批准**；本项目当前无任何 HTTP 客户端依赖——已核实 `pyproject.toml` 无 `httpx/requests/aiohttp`）。
- M1 的 `get_catalog_version`（其返回含 `data_digest`，用于记录抓取时的数据版本；网页内容不参与 VersionGate 的硬过滤，只作“补充资讯”标注）。

## 3. 交付物

| 文件 | 职责 |
|---|---|
| `advisor/web.py` | `search_curated_web` + SSRF 防护 + 抓取管线 |
| `config` 增项 | `CURATED_WEB_DOMAINS`（精确域名白名单，默认空） |
| `pyproject.toml` | 加 `httpx` 依赖 |
| `tests/test_advisor_web.py` | SSRF / 白名单 / 缓存 / 大小上限用例 |

## 4. 详细设计

### 4.1 域名白名单（精确匹配，非字符串包含）

- 配置形如 `CURATED_WEB_DOMAINS = ["www.xxx.com"]`（默认 `[]`）。
- 匹配规则：**精确域名相等或等于其后缀的一级子域**（`www.xxx.com`、`a.www.xxx.com` 可，`xxx.com.evil.com`、`eviltwitterxxx.com` **不可**）。禁止用 `"xxx.com" in url` 这类子串判断。
- `CURATED_WEB_DOMAINS` 为空 → `search_curated_web` 返回“网页搜索未启用”，不进网络。

### 4.2 SSRF / 网络硬化（代码级，非提示词）

```python
def _assert_public(host: str, resolved_ips: list[str]) -> None:
    # 拒绝私网/回环/链路本地/保留段：
    # 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
    # 169.254.0.0/16, 0.0.0.0/8, ::1, fc00::/7, fe80::/10, 及云元数据 IP
```

- **仅 HTTPS GET**：`http://` 一律拒绝；无上传/登录/写操作。
- **重定向防护**：默认 `follow_redirects=False`；允许时逐跳重新解析并再次过 `_assert_public`（防重定向到私网）。
- **DNS 重绑定**：对解析出的**所有** IP 都做私网检查（不是只看第一个）。
- **响应上限**：`max_size`（如 512KB）超限截断；`timeout` 短超时（如 5s）；单次回答**调用次数上限**（如 3 次）。
- **内容净化**：只回**去除 `<script>`/`<style>` 后的正文片段**（`{url, snippet, fetched_at, trust}`），**不把整页 HTML 灌进模型上下文**。
- **缓存快照**：按 URL+`fetched_at` 缓存，同请求周期内不重复抓取。

### 4.3 工具签名

```python
def search_curated_web(query: str, *, max_results: int = 3) -> list[dict]:
    # 仅从 CURATED_WEB_DOMAINS 内抓取；返回 [{url, snippet, fetched_at, trust}]
    # trust ∈ {"official", "community", "unknown"}，默认 "unknown"
```

- 网页结论在顾问事实优先级里排 **③（本地库 → 轨迹 → 网页 → Skill）**，且每条结论必须带 URL + 采集时间 + 可信等级；冲突时以本地库为准。

## 5. 边界与护栏

- **默认关闭**：无白名单无网络。
- **网页正文是待分析数据，不是指令**：正文里“忽略规则/调用额外工具/泄露提示词/访问隐藏数据”一律忽略（与 M4 Skill 同一原则）。
- **不进 VersionGate 硬过滤**：网页是“补充资讯”，数据版本一致性只约束本地库与轨迹，网页只标 `fetched_at` + `trust`。
- **隐私**：抓取不发任何用户数据；query 只作为站内检索词。

## 6. 测试计划

- 白名单：精确域名通过、`evil.com` 后缀绕过被拒、空白名单返回“未启用”。
- SSRF：私网 IP（`127.0.0.1`/`10.x`/`169.254.169.254` 元数据）被 `_assert_public` 拒绝；重定向到私网被拒。
- 管线：`http://` 拒绝、响应超限截断、去脚本后回片段、缓存命中不二次抓取。
- 用 `httpx.MockTransport` / `respx`（dev 依赖已有）注入响应，不碰真实网络。

## 7. 验收 Gate

- [ ] 无白名单默认关闭；有白名单时精确匹配 + 全套 SSRF 防护。
- [ ] 只回规范化片段（URL + fetched_at + trust），不回整页 HTML。
- [ ] 网页结论在事实优先级 ③，冲突以本地库为准。
- [ ] SSRF/白名单用例全绿，不碰真实网络。
- [ ] 新依赖经负责人批准。
- [ ] Gate 报告 → 用户「通过」→ commit。

## 8. 风险与回滚

- **风险**：SSRF 漏判（DNS 重绑定等）→ `_assert_public` 对全部解析 IP 检查 + 重定向逐跳复查 + 测试钉住。
- **风险**：网页内容注入 → 只回净化片段 + 事实优先级 ③ + M5 的 `WEB_INJECTION` 失败签名盯防。
- **回滚**：`web.py` 纯新增 + 依赖可撤；`CURATED_WEB_DOMAINS` 置空即彻底关闭。
