# Changelog

本项目的所有重要变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [3.0.0] - 2026-10-16

> **为什么是 major**：本版对**仓库结构**是破坏性变更 —— Web 控制台（前端 + 后端）整体迁往独立仓 `dpkg-s/ai-memory-web`，本仓改以 git submodule `web-console/` 引用。沿用 2.x 方式克隆/更新的人，会发现 `web/`、`serve_web.py`、`web_console.py`、`tests/test_web.py` 从本仓「凭空消失」（它们没被删除，只是搬了家）。
>
> **MCP 工具面只增不改**：新增第 **25** 个工具 `memory_config`（运行时阈值读写），其余 24 个的行为与 2.5.0 完全一致。`python server.py` 的 stdio 行为在「不碰新工具」时与 2.5.0 无差别。

三件事，按依赖顺序列出：

1. **传输层（v3「中心化 / 远程化」路线的阶段 1）** —— 服务端从「只有 stdio」扩展为「**可选** Streamable HTTP」：同一个 `server.py`、同一份记忆库，既能在本机走 stdio 给 WorkBuddy / Codex / Claude Desktop / Cursor 用，也能由 `serve_http.py` 起一个 HTTP 端点供局域网内多台机器共用。HTTP 装配全部收在两个新文件（`transport_http.py` / `serve_http.py`），主程序对**这一层**零改动 —— 这也是后续「中心化 DLC」分发的判据前提。
2. **Web 控制台（v3.1）拆分为独立仓** —— 控制台（前端 + 后端）整体迁往 `dpkg-s/ai-memory-web`，本仓改以 submodule 引用。理由：控制台可以独立演进、独立发版，不必牵动 MCP 服务端的节奏。
3. **配置面纳入 MCP：拆掉控制台的「第二条腿」** —— 控制台曾靠 `core_path.py` 上溯定位核心目录、注入 `sys.path`，再 `import memory_config` / `transport_http`；那是跨仓的第二条依赖腿，两侧各持一份「`memory.toml` 怎么读、怎么写、怎么校验」的知识。本版新增 MCP 工具 `memory_config`，把这件事收敛成一份实现，控制台**只经 MCP 读写** ⇒ 两个仓之间只剩「连接」一种关系。

### Added

- **`memory_config`（第 25 个 MCP 工具）** —— 运行时阈值（vault 根 `memory.toml`）在 MCP 上的**唯一通道**：
  - `action="get"`（默认）：当前生效值、每键来源（`env` / `file` / `default`）、中文名与作用说明、类型与默认值，外加配置文件路径、可写性与最近一次解析错误。
  - `action="set"`：**整状态写** —— 必须提交全部 8 个键，缺键即拒（漏提交的键被静默写回默认值，比报错更难查）。写前备份（同目录 `memory.toml.bak.<时间戳>`，保留最近 10 份）、写前自解析（fail-fast）、`os.replace` 原子替换。**只写 `memory.toml` 这一个文件，绝不触碰任何 `*.md`。**
  - 注解 `_WRITE`（与既有 `memory_heat_suggest(apply=)` 同型：默认只报、可显式落盘）；归 Web 控制台的「库维护」分组。
  - ⚠️ **注册名与 Python 函数名刻意不同**：函数叫 `_memory_config_tool`，对外注册名由 `@mcp.tool(name="memory_config")` 指定。若函数同名，模块级 `import memory_config` 会被这次 `def` **遮蔽**，本文件 20 余处 `memory_config.get(...)` / `.config_path()` 全部抛 `AttributeError`（实测：`memory_stats` 当场挂掉）。
  - `values` 的类型标注为 `dict[str, Any]` 而**不是** `dict[str, float]`：后者会让 SDK 入参校验把 `body_truncate_chars` 的 `8000` 强转成 `8000.0`，而该键期望 int ⇒ 一次正常的整状态写被判成类型错误（端到端测试当场 400）。类型校验由 `memory_config` 按 `_SPEC` 做，比 JSON Schema 的 `number` 更严（int / float 分开、拒 bool）。
- **`memory_config.py` 的配置面出口** —— `state()`（完整快照：值 / 来源 / 文案 / 类型 / 默认值 / 路径 / 错误）、`source_of(key)`、`render_toml(values)`（整状态渲染，缺键/多键/类型错一律拒）、`write_values(values, keep=10)`（校验 → 写前自解析 → 备份 → 原子替换 → 失效缓存）。`memory_stats` 的「配置」节与 MCP 工具同源，**调用方不得自己拼文案、自己解析、自己写盘**。`write_values()` **不加写锁**：`os.replace` 本身原子，且本模块是最底层（不 import `locks`，分层铁律）。
- **`transport_http.py`** —— HTTP 传输层装配（只依赖 `mcp` + 标准库，零新增依赖）。把 `AI_MEMORY_TRANSPORT` 归一为 `stdio` / `streamable-http`，解析 host / port / path 与会话治理参数，并在危险组合上 **fail-fast**：
  - **监听非回环地址（如 `0.0.0.0`）时必须显式提供 `AI_MEMORY_HTTP_ALLOWED_HOSTS`**，否则拒绝启动。依据是 mcp 1.30.0 实测：`host=0.0.0.0` 会让 SDK 的 `transport_security` 退化为「不校验」，而 DNS rebinding 防护**并不依赖公网暴露** —— 局域网下风险等级相同。
  - **不提供关闭 DNS rebinding 防护的开关**（`AI_MEMORY_HTTP_DNS_REBINDING=0` 直接拒绝）：Host / Origin 校验是回环与局域网场景下唯一的浏览器侧防线；要放行更多主机名应写白名单，而不是关掉校验。
  - **刻意不暴露 SDK 层 auth**。mcp 1.30.0 上实测：`auth` 一经开启，未认证请求返回 `401` + `WWW-Authenticate: Bearer … resource_metadata=…`，部分客户端会据此**强行走 OAuth 发现流程且不回落**；本仓没有授权服务器，只会卡死。鉴权因此留给外层（反向代理 / Tailscale / Cloudflare Access）。
- **`serve_http.py`** —— HTTP 入口。未显式指定传输方式时按 `http` 起（脚本名即意图），并在远程模式下**默认禁用热重启**（`os.execv` 会断开全部在途 HTTP 会话；重启交给进程管理器即可，`AI_MEMORY_RESTART=1` 可强行恢复）。
- **`tests/test_transport.py`（43 断言）** —— 模式归一 / 三条安全规则 / 替身 FastMCP 装配（含「stdio 分支零副作用」「刻意不设 auth」）/ **端到端**（真起 `serve_http.py`，客户端走 Streamable HTTP 完成 initialize·list_tools·write·read，并验证远程模式下 `memory_restart` 已自动禁用）/ **fail-fast 进程级验证**（非回环且无白名单 ⇒ 进程非零退出且提示明确）/ **零改动保证**（无 `AI_MEMORY_TRANSPORT` 时 `server.py` 仍是可用 stdio 服务且工具数正确）。
- **`Dockerfile` / `.dockerignore` / `compose.yaml`（容器化，可选）** —— 让「目标机器上不装 Python 与 mcp」也能跑：镜像里只有运行时（Python + `mcp` + 本仓 `.py`），**记忆库一律从卷/绑定挂载进来**，因此升级只换镜像 tag、数据不动。要点：
  - 以非 root（uid 10001）运行；健康检查**只探监听端口** —— MCP 端点是 POST + 会话语义，GET 不返回 2xx，拿它当探针会把「健康」误判成「故障」。
  - 容器内必须监听 `0.0.0.0`（否则宿主机连不上），这正好触发上面第 2 条约束 ⇒ 镜像预置 `AI_MEMORY_HTTP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,ai-memory:*`，要从别的主机名/地址访问**必须显式追加**，否则被 Host 校验拦下（有意的摩擦）。
  - compose 把端口绑到 `127.0.0.1`（局域网看不到），数据用具名卷；要用已有记忆库目录则换成 bind mount。
  - `.dockerignore` 把构建上下文收敛为 `*.py` + `requirements.txt`；`COPY *.py` 用通配而非逐项列举 —— 安装脚本里已有一份显式模块清单，不再复制第二份，新增模块（如本次的 `memory_config` 改动不新增文件）无需改 Dockerfile。
- **`web-console/`（submodule 引用）** —— Web 控制台（前端 + 后端）的唯一事实源在独立仓 [`dpkg-s/ai-memory-web`](https://github.com/dpkg-s/ai-memory-web)。本版引用其 `v1.1.0`：同源后端代理 + token 鉴权 + 阈值走 MCP 工具 + 更新面（git 白名单拉取 + 差异预览 + 一键重启）。要点（详细设计见该仓 README）：
  - **同源页面 + 后端代理**，而不是让浏览器直连 MCP：实测 `allowed_origins` 默认空 ⇒ 任何带 `Origin` 的请求一律 **403**；即便补白名单，MCP 端点也**不返回 CORS 头**且 `OPTIONS` 返 **405**。后端用 SDK 客户端连则不发送 `Origin` ⇒ 实测 `initialize` / 25 工具 / stats 全通，且 **MCP 端零配置改动**。（顺带印证健康检查口径：`GET /mcp` 无 `Accept` ⇒ **406**、带 SSE 无会话 ⇒ **400**，所以探针只探端口。）
  - 鉴权：`/api/*` 走 token（`Authorization: Bearer` 或 `X-Auth-Token`），**静态资源不校验**（里面没有秘密）；**监听非回环地址必须设 `AI_MEMORY_WEB_TOKEN`**，否则拒绝启动。
  - **控制台不 import 本仓任何模块**（含 `memory_config` / `transport_http`）——由该仓 `tests/test_web.py` 的 C 段机器守卫（`sys.modules` 里出现核心仓模块即红）。
- **`tests/test_memory_config.py` 新增 T10（30 断言）** —— 配置面出口的可证伪判据：快照字段齐备、键集与 `DEFAULTS` 严格相等、`source_of` 三级来源（含 env 覆盖与摘除回退）、`render_toml` 的四类拒绝、**被拒写入必须逐字节不碰盘**、备份名命中 vault 自带 `.gitignore` 的 `*.bak.*`、`keep` 限制备份份数、不留 `.tmp` 残骸、写入路径不产生 `*.md`、未注入 vault 时明确报错。
- **`tests/test_tools_smoke.py` 新增「配置面工具」段** —— `get`（text/json）/ 缺键拒绝 / 非法键值 / 非法 action / 全量写入落盘生效 / 二次写入留备份。

### Changed

- **`tests/test_import_layers.py`（35 → 47 → 47 断言）**：分层守卫第 9 组（I9）—— 装配/入口层（`serve_http` / `transport_http`）**不得被叶子或聚合器 import**（否则 `server` 会经由 HTTP 入口反向依赖，「主程序零改动」与「stdio 永远可用」两条前提同时失效），且它们对本地模块的依赖**只许**指向 `server`。附两条自检：**提取器自检**（I9c，本组第一版因模块清单未纳入装配层而**恒绿**）、**白名单不腐烂**（I9d）。本版随拆仓更新：原 `("web_console", "memory_config")` 白名单项移除 —— 该依赖边在控制台迁出后**已彻底不存在**（不是"搬走了"，是被本次第 3 件事消灭了）。
- **`tests/test_tools_smoke.py`（33 → 41 断言）/ `tests/test_transport.py`（43 断言）**：工具总数 24 → **25**，`EXPECTED_TOOLS` 与 stdio 端到端计数同步。
- **README**：新增「远程访问（可选 Streamable HTTP）」一节与 9 个 HTTP 环境变量；新增「用容器跑（可选）」小节与容器文件条目；新增「Web 控制台（v3.1，可选）」一节（指向独立仓）；工具速查 **24 → 25**；版本徽章与容器 tag 同步为 `3.0.0`；测试计数按实测重算。
- **`.github/workflows/release.yml`**：Release 标题改为取**带注释 tag 的 subject**（`git tag -a v3.0.0 -m "v3.0.0 — …"` ⇒ 标题即那句话），轻量 tag 回落为 tag 名。Checkout 补 `fetch-depth: 0`（读 tag 注释需要）。
  - ⚠️ **实况纠正（v3.0.0 发布当场踩到）**：只靠本地 `git tag -l --format='%(contents:subject)'` 不行 —— runner 上本地 tag 未必是带注释形态，该占位符会**静默退回被指向提交的主题**，于是 Release 标题变成 `docs: 更正 v3.0.0 的测试口径…` 而不是 tag 上写的那句话。现改为走 API：先看 `git/ref/tags/<tag>` 的 `object.type`，**确为 `tag`** 才读它的 `message` 首行，任何一步失败都回落为 tag 名（宁可为空，不可为错）。已核对本次 `object.type=tag`、标题取值正确，并手工订正了 v3.0.0 的 Release 标题。

### Removed

- **`SHA256SUMS.txt` 的生成与分发（release.yml）**：清单只覆盖 `install.sh` / `install.ps1` 与 7 个运行时模块中的 6 个（**漏 `memory_config.py`**），与实际安装集不一致；而安装/更新流程并不做校验 ⇒ 继续维护它的收益低于「清单与代码不同步」造成的误导风险（用户按过期哈希核对，只会得出「文件被篡改」的错误结论）。今后 Release 只发布代码本身。
- **本仓移除 Web 控制台相关文件**（迁往独立仓，submodule 引用）：`web/`（47 文件）、`serve_web.py`、`web_console.py`、`tests/test_web.py`。

### Fixed

- **窄屏（≤344px）概览页横向溢出 12px**（Web 控制台，随迁出）：`.grid.two` / `.grid.three` 的轨道下限是 `320px` / `260px`，`max-width:560px` 的分支只改了 `.grid.stats` ⇒ 视口小于该下限时轨道比内容盒还宽，直接顶破视口。现于窄屏分支把两者放成 `minmax(0, 1fr)`。
- **手机宽度下「调试台」页横向溢出最多 183px**（Web 控制台，随迁出）：`.tool-item .td` 是 `white-space: nowrap` 却**没有 `min-width: 0`** ⇒ 其 min-content 等于整句描述宽度（实测 ~455px），列向 flex 的交叉轴最小值默认 `auto` 压不下去，再经 `1fr` 轨道的 `auto` 最小尺寸把整张卡撑破视口。现补 `min-width: 0`（省略号这才真正生效），并显式允许网格条目收缩（`.console-grid > * { min-width: 0 }`），`.console-grid` / `.grid.lib` 的单列分支改用 `minmax(0, 1fr)`。
  - ⚠️ **这两处差点被漏掉也差点被改坏**：原先用 `chrome --headless --window-size=390,844 --screenshot` 取景，而 **Chrome 在 Windows 上会把窗口宽度钳到最小 500px**（实测 320/390 → **500**，1440 → 1424；三种 headless 模式一致，`--force-device-scale-factor` 也不改变布局宽度）。于是那张「390px 截图」其实是**500px 宽的布局被裁到 390**，看起来像「右侧溢出 / 卡片被切」——**纯取景假象**。若照此去改 CSS，反而会引入真回归。现改为把页面放进**同源 iframe**（iframe 的 CSS 宽度即布局视口，不经过 OS 窗口），截图后用 Pillow 裁到精确尺寸，并把「产出像素尺寸 == 请求尺寸」本身作为判据。同法实测 8 个视口 × 4 个页面 = **32 组零溢出**，统计卡列数随宽度单调不减（1→2→3→4）。

### Tests

- **本仓 26 套**（库存口径合计 **1092 断言**）。在**只含跟踪文件的干净检出**（等价 CI 的 checkout）上：**25 套全绿**，合计 **1060 断言**；唯一红灯是 `test_schema_diff`（32 断言）—— 本机安全策略拦截它往临时目录写入 `v2缺键.md`（`PermissionError`），CI 的 Ubuntu runner 无此策略。
- 本版**新增/改动**的套件：`tests/test_tools_smoke.py` **41/0**（+8：新增「配置面工具」段 + 工具数 24→25）、`tests/test_memory_config.py` **111/0**（+32：新增 T10 配置面出口）、`tests/test_transport.py` **43/0**（仅工具数计数同步）、`tests/test_import_layers.py` **43/0**。
- ⚠️ **本机工作区的额外红线不算数**：作者开发副本里 I0a 会额外报出 37 个 `_v3_*` 探针脚本与 `ai_memory_gui.py` / `build_exe.py`（前者命中 `.gitignore`、属一次性调试产物；后两者是尚未入库的本地脚本）。它们不在版本库里 ⇒ 不影响本版发布物，也不影响 CI。顺带记一次真实故障：899a776 把 `ai_memory_setup.py` 作为全新文件提交进本仓却漏同步模块清单，I0a 报 `多=['ai_memory_setup']`，CI 四个 Python 版本同时失败 37s；由 3e9dae9 新增 `TOOL_MODULES` 一类修复。
- 上版（2.5.0）基线为 25 套 / 1000 断言。
- Web 控制台的测试随其迁往 `ai-memory-web`：**72 断言全绿**（含真起 `serve_http` + `serve_web` 的阈值写读往返，以及「不 import 核心仓模块」的机器守卫 C2）。
- 另在**真 vault**（`D://ai记忆`）上以 Streamable HTTP 起服务端、经 MCP 协议调用 `memory_config` 做只读核对：**11/0**（工具数 25 / 注解 / 真实配置文件路径 / 8 键 / 文案齐备 / text 与 json 两形态）。
- Web 控制台三端适配另有**独立量测探针**：32 组（视口 × 页面）横向溢出全 0，列数单调；渲染验证 30 项全绿（各页 DOM 真实内容 + 14 张精确尺寸截图）。

## [2.5.0] - 2026-10-12

> 版本号说明：本版内容原计划随 `2.4.3` 发布，并入本版后改用 **minor** 号 —— 依赖下限从 `mcp>=1.27` 提到 `mcp>=1.30` 会**直接改变使用者的安装结果**（`mcp<1.30` 的环境将装不上），不属 patch 语义。

本版只有一处实质改动：**把运行时依赖下限从 `mcp>=1.27` 提到 `mcp>=1.30`**，并同步散落在安装脚本与文档里的版本引用。上游 **1.x 线已进入维护模式**（此后仅收安全修复），1.30.0 是其最后一版 —— 停在 1.27.2 等于停在维护线的更早位置。

### Changed

- **`requirements.txt`：`mcp>=1.27,<2` → `mcp>=1.30,<2`**。`install.sh` 4 处、`install.ps1` 4 处、`README.md` 3 处，共 **12 处**引用同步。⚠️ 上界 `<2` **保持不变** —— 2.x 的迁移成本不在那两行 import，而在 `memory_restart` 依赖的 `InitializationState` 已被删除（属重写自研补丁，非改名）。
- **README 测试计数校正为 1000**：正文原写 964、徽章原写 998，**三处互不一致**。本次按 25 套实测逐项重算（`recovery` 35→36、`frontmatter_norm` 41→49、`dir_policy` 71→82、`index_name` 33→49，其余不变，合计 1000），并把 `隐藏索引与迁移` 正名为 `索引命名与迁移`（v2.4.2 已把索引改回可见名）。徽章 Version 由滞后的 `2.4.1` 改为 `2.5.0`。
- README 版本上界说明补记**第二处破坏点**：2.x 除删除 `FastMCP` 外还删除了 `InitializationState`，`memory_restart` 的会话恢复补丁同样会失效。

### Dependencies

- `mcp>=1.30,<2`（下限 +0.3）。**升级依据（隔离 venv 实测，非推断）**：
  - `mcp.server.fastmcp` / `mcp.server.session.InitializationState` / `mcp.types.ToolAnnotations` 在 1.30.0 上**全部在位**；
  - `FastMCP.__init__` 参数 **27 → 30**，新增 `session_idle_timeout` / `max_sessions`（对将来的 HTTP 远程化是直接的 DoS 面收敛）；
  - 全量回归 **25 套 / 1000 断言全绿、退出码全 0**（含 `test_restart.py` 对会话恢复补丁的 26 项验证）。
- `requirements-dev.txt` 无变化（`pytest>=8.0` + `ruff>=0.5`）。

## [2.4.2] - 2026-10-12

本版是一次**回退式修正 + 两处「名不副实」清算**。v2.4.0 把自动生成的 MOC 索引用 `.` 前缀藏了起来（B6），换来「Obsidian 文件树干净」；实测这笔交易的代价远大于收益 —— 全库 25 篇笔记用 `[[记忆索引]]` 引用索引，而 Obsidian 按**文件名 stem** 解析双链，改名后这些链接在人侧**全部点不开**，索引本身也点不开。本版把索引改回可见名，并顺带修掉两处让工具「看起来能用、实际不好用」的缺陷：`memory_search` 的 `recent-first` 其实是路径字符串倒序，索引只有标题没有内容线索。另修一个**静默数据丢失**：含换行的标量写进 frontmatter 后，第二行起会被无声丢掉。

### Changed

- **索引回到可见名 `记忆索引.md`（撤销 B6 的隐藏化）**。B6 的代价有三条，均为实测：① 全库 **25 篇**笔记用 `[[记忆索引]]` 引用索引 —— 改名后文件名 stem 变成 `.记忆索引`，而 **Obsidian 按 stem 解析双链**，这 25 条链接在人侧全部点不开；② 索引自身**根本点不开**（Obsidian 忽略点文件），MOC 的导航价值归零；③ **人机可达性分叉** —— 服务端靠 legacy 识别仍能 `memory_read("记忆索引")`，连 `memory_rebuild_links` 都因 `known` 集合收 `title` 而不报死链，于是「工具全绿、人却什么都点不到」。生成物的身份改由 **frontmatter 的 title + tags** 标识，不再依赖文件名前缀。**升级后首次写入会自动删除隐藏名残留 `.记忆索引.md`**，但只有确认是生成物（`title` 恰为 `记忆索引` 且 tags 命中 `moc`/`index`/`navigation`）才删，疑似人工文件跳过并写日志。迁移**先于枚举**执行 —— 否则首轮重建会把即将被删的文件也渲染成一行，凭空造出一条死链。
- **索引条目行追加 `summary` 摘要**（上限 60 字符，换行压平）。索引此前只是**标题清单**：读的人看到 250+ 个标题仍不知哪一篇是目标，只能逐篇 `memory_read`。补上摘要后一次读入即可初筛，是「AI 在库里的索引路径」中「索引 MOC」这条路径从「能用」变「好用」的关键一步。`summary` 因此纳入 `_refresh_index` 的内容签名 —— 否则改摘要不触发重建，索引会停在旧内容上。
- **`memory_search` 的 `recent-first` 从名义变成事实**：旧实现是 `sorted(_iter_md_files(...), reverse=True)` —— 排的是**路径字符串**，与时间毫无关系，且结果**稳定**（稳定 ⇒ 看起来「确实有排序」，反而更难发现它是错的）。实测聚合页 `近期工作动态` 在 274 条里排第 **51** 位，子目录条目还因路径前缀整体压过根目录条目。现按 frontmatter 的 `updated` **真倒序**：缺失 `updated` 者排末尾，同刻按相对路径定序（保证跨次调用可复现、可断言）。

### Fixed

- **多行标量写进 frontmatter 后内容被静默丢弃**：`yaml_quote_scalar()` 的「需要加引号」判据不含换行 ⇒ 含 `\n` 的值被当作**裸多行标量**写出，而 `parse_yaml_frontmatter()` 遇到不含 `:` 的行会 `continue` —— 尾部内容**无声消失**（实测：显式传入两行 `summary`，落盘为 `summary: 第一行摘要`，读回只剩第一行）。现判据纳入控制空白，escape 侧同步把 `\n`/`\r`/`\t` 写成转义序列（解析端本就认这几个转义）⇒ 一读一写后逐字节稳定、内容零损失。该缺陷此前没被触发，是因为**没人真写过含换行的字段值**；本次把 `summary` 提升为索引里的可见字段后，它才第一次有了现实触发路径。

### Tests

- `tests/test_hidden_index.py` → **`tests/test_index_name.py`**（33 → **47** 断言）。原套件的断言口径（「点文件仍须被枚举」「迁移删旧名」）整体作废，按新模型重述为 H0~H7：常量面 / 旧名必消失 / 仍被枚举与检索 / 迁移**反向**清理隐藏名残留（含「人工同名文件不得误删」的保守双条件）/ **人侧可点击性** / 文件名与 title 一致 / 索引行摘要（换行压平、60 字截断）/ 草稿提示与 doctor 一致性。**证伪**：把新套件丢进 v2.4.1 的 `git archive HEAD` 快照（只换测试、不换产品代码，唯一变量）⇒ **47 项里 23 项确定性变红**。
  - 顺带修掉一处**假判据**：可点击性最初用 `memory_rebuild_links` 断言，而它在 v2.4.1 上**恒绿**（`known` 集合同时收 `title`，服务端从不认为那 25 条是死链）—— 真正的断链只在 Obsidian 侧，判据必须落在**文件名 stem** 上，否则这个断言证明不了任何东西。
  - 为保证旧版下「读不存在的索引文件」不会把脚本中断、使红数被系统性低估，索引读取统一走返回空串的 `index_text()` / `index_meta()`（首轮实测：只报出 5 项就崩了；修掉后报满 23 项）。
- `tests/test_frontmatter_norm.py`：断言 **41 → 49**（+8）。新增 L 段覆盖多行标量的转义往返：引号化 / `\n` 转义 / 解析还原 / 二次往返幂等 / 制表符 / 既有引号判据不回归 / `memory_write` 端到端完整落盘。**证伪时 6 项确定性变红**。
- 全库 **25 套 / 998 条断言**（v2.4.1 基线实测 976，本版 +22）。README 里的套件计数有三处长期未同步（`test_dir_policy` 71→82、`test_recovery` 35→36、`test_roundtrip` 28→29），本版一并按实测校正。

## [2.4.1] - 2026-10-12

本版是 2.4.0 目录化的一次**校正**：写入端自动归类当时只认 11 个写死的语义桶，遇到 vault 里**既有的**分类目录（`故障/` `核验/` `经验/` `调研/` `排坑/` …）一律兜底成「其他」，等于把标题前缀语义本已清晰的笔记扫进垃圾桶；索引渲染又只遍历固定桶序，前缀命中的「目录名桶」不在序里，会**静默丢条目**（索引少链接却不报错）。两处均已修正，且**旧调用形态的行为与改动前逐字一致**。另顺带修掉一个 **Windows 专属的并发崩溃**：抢锁撞上 `ERROR_ACCESS_DENIED` 时异常会逃逸出 `acquire_lock`，把「没抢到锁」变成调用方崩溃。

### Fixed

- **`acquire_lock` 在 Windows 下抛 `PermissionError` 逃逸（并发写随机崩）**：`open(path, "x")` 落到 `O_CREAT|O_EXCL` —— POSIX 下目标已存在返回 `EEXIST`（`FileExistsError`），而 **Windows 下目标已存在且被其他进程持有时返回 `ERROR_ACCESS_DENIED(5)`**，Python 抛的是 `PermissionError`。代码只 `except FileExistsError`，于是该异常**直接逃逸出 `acquire_lock`**：调用方（`memory_write` 等）拿到的是异常而非「抢锁失败」。同一陷阱也存在于回收侧 —— `unlink` 删除「正被其他进程打开」的文件同样抛 `PermissionError`，旧代码在 `except (JSONDecodeError, OSError)` 分支里**再删一次**，第二次照样逃逸。现改为：创建时一并捕获 `PermissionError`；删除统一走新增的 `_unlink_quiet()`（失败静默、下轮重试）。**实测**：10 进程并发写，脱链环境下 8 轮里 **2 轮**随机报 `.memory.lock` 的 `PermissionError`（`WinError 5` / `Errno 13`），修复后同一循环不再复现。⚠️ 该缺陷**只在 Windows 触发** —— POSIX 下 `open("x")` 恒为 `EEXIST`、`unlink` 不留占用，所以 ubuntu CI 上从未暴露。
- **自动归类漏判「既有分类目录」**：新增 `_dir_prefix_hit()` —— 标题**第一个 `_` 之前**那段若恰好是 vault 根下既有的一级目录，就直接以该目录为落点（`故障_H29K_dnsmasq崩溃循环` → `故障/`），不再兜底成「其他」。实施时实测：根目录 22 篇里 **8 篇**被兜底成「其他」，判准率仅 **11/22**。该判定**刻意排在 tags 判定之前** —— tags 可能由 `_auto_suggest_tags()` 自动补出，可靠性低于写入方手写的标题前缀。四档策略都受此影响（`warn` 下体现为**建议**落点变化，`auto` 下才是**真实**落点变化）；**不搬迁任何存量条目**，存量归位仍走一次性手工搬迁。
- **核心页被前缀规则误送进同名目录**：全库核心页文件名用 `_` 连接（`Codex_身份档案`），而 `CORE_PAGES` 里写的是空格形态（`Codex 身份档案`）⇒ 不归一化就漏判，`Codex_身份档案` 会被送进 `Codex/`。新增 `_core_variant()` 做 `_`／空格等价归一化，核心页（含两种变体）一律留根。
- **索引渲染静默丢条目**：渲染循环此前只遍历写死的 11 个桶序，而前缀命中会产生「目录名桶」（`故障` / `核验` / …），它们不在序里 ⇒ 这些桶下的条目**不会出现在 `.记忆索引.md` 中，且不报任何错**。改为由 `_bucket_order()` 生成渲染序：规范桶（仅有内容的）按固定序在前，其余桶按字典序附后。

### Changed

- `_entry_bucket()` 与 `_suggest_dir()` 增加**可选** `dirs` 参数：省略时内部取一次 `_known_dirs()`；传入 `None` 的旧调用形态**整体跳过前缀判定，行为与改动前逐字一致**。
- 三处批量调用点（索引重建 / `memory_audit` / `memory_index_draft`）改为**在循环外取一次目录集**复用 —— `_known_dirs()` 要扫一遍 vault 根目录，逐篇调用等于把 250 次目录枚举压进一次索引重建。

### Tests

- `tests/test_dir_policy.py`：断言 **51 → 62**（+11），覆盖前缀命中落点、核心页 `_`／空格变体豁免、`dirs=None` 旧形态等价性、渲染序含「目录名桶」四项。
- `tests/test_recovery.py`：新增 **R3f**（36 断言，原 35）—— 打桩 `open` 让锁文件的所有访问都抛 `PermissionError`，断言 `acquire_lock` **不逃逸**、按抢锁失败返回 `False`。该断言对旧实现**确定性变红**（实测输出 `acquired=RAISED:PermissionError`），故是真判据而非恒绿装饰。

## [2.4.0] - 2026-10-08

本版三条主线：**目录化**（记忆库不再只能是扁平一层 —— 可建子目录、写入端还会按语义自动归位）、**稳定标识与索引增量化**（改名不再静默失联，索引不再每次全量重解析）、**可脚本化与可配置**（四个只读工具可出 JSON，8 个运行时阈值外置到 `memory.toml` 并热重载）。

### Added

- **多级目录分类（递归枚举层）**：此前 21 处扫描都是非递归 `glob("*.md")`，把文件放进子目录就等同「脱离检索」—— 这也是 2.2.0 明确否决目录分层的原因。本版先把**枚举层**统一收口：
  - `_iter_md_files()` —— 唯一的 `.md` 枚举入口（`os.walk` + 保留目录剪枝 + 按「深度 → relpath」确定性排序）。21 处 `glob` 全部替换，只此一处可改枚举行为
  - `RESERVED_DIRS`（8 项：`.archive` / `.trash` / `.git` / `.obsidian` / `ai-memory` / `__pycache__` / `.ruff_cache` / `.pytest_cache`）—— 新增 `.md` 目录必须同步登记，否则会被当记忆收录
  - `_vault_fingerprint()` 的指纹键由文件名改为 **relpath**。⚠️ 任何枚举范围变化都必须同批改它，否则子目录内变更不触发缓存失效 ⇒ **静默返回陈旧数据且无报错**
  - 专项测试 `tests/test_folders.py`（43 断言）
- **`memory_write` 支持写入分类子目录**（阶段二）：`safe_filename()` 把 `/` 当**子目录分隔符**并逐段消毒（非法字符 → `_`、丢弃控制字符与空段、`strip("._")`、每段 80 字上限）；**不含 `/` 的标题行为与旧实现逐字节一致**。
  三条边界：① 旧口径保留为**独立的** `legacy_flat_filename()` —— 旧实现把 `/` 与其它非法字符放在**同一个正则字符类**里（连续非法串折叠成一个 `_`），拆成两步替换会破坏折叠语义（`a/ b` → `a__b` ≠ 旧值 `a_b`），历史标题的幂等匹配会失手；② `_title_path_error()` 是**政策层**而非消毒层：保留目录、`.` / `..` 段、盘符、绝对路径、层级 > 3 一律**显式拒绝并说明原因**（`strip("._")` 会把 `.archive/x` 静默改写成 `archive/x` —— 写进了不该写的地方却毫无报错）；③ `memory_write` 在 `"/" in title` 时**追加一次「旧口径 stem」匹配**，否则历史标题含 `/` 的笔记会被当成新路径、**凭空造重复件**。
  - 专项测试 `tests/test_title_path.py`（42 断言）
- **写入端自动归类 + 叶子名口径**（阶段三）：`memory_write` **新建**且标题不含 `/` 时，按 `_entry_bucket()` 的 11 个语义桶映射到规范目录。四档 `AI_MEMORY_DIR_POLICY`：**`warn`（默认：只提示不改落点）** / `auto` / `strict` / `off`。
  - ⚠️ **默认值为什么是保守的 `warn`**：auto 作默认时 **18 套里 11 套失败（50 断言）**，全是「`<根>/<标题>.md` 读不到」—— 既有辅助函数编码的正是「新条目落在根」这一心智模型，多客户端与既有习惯同样依赖它；且 `_auto_suggest_tags()` 与 `_entry_bucket` 两个启发式叠加会让落点对写入方**不可预测**
  - 三条边界：只作用于**新建**（更新绝不搬运，否则存量根核心页会被搅动）；落盘目录**不写进 frontmatter 的 `title`**（title 恒为裸叶子名，防「title 写路径 ⇒ 一切按 title 比对的判定失配」复发）；标题已含 `/` 时完全尊重写入方
  - 首版残留缺陷（同日修复）：只在 `auto` 分支改落点，显式 `title="目录/条目"` 分支仍把整串路径写进 frontmatter ⇒ 该条目在所有按 title 比对的判定里全部失配。现口径为「`leaf_title = title.rsplit("/", 1)[-1]` 写盘」，且按路径精确命中后**必须再核对 `title == leaf_title`**（防覆盖「同茎异主」= 不可逆丢失）；存量带前缀 title **被再次写入时自动回正**，无需迁移脚本
  - 专项测试 `tests/test_dir_policy.py`（71 断言）
- **`permalink` 稳定标识**（A3）：解决「改名即静默失联」。`permalink` 在**首次写入时冻结为当时的文件名 stem，此后永不覆盖**；查找链为 **`permalink → title → stem`**。
  要点：① `_entry_matches(meta, path, key)` 是**唯一入口**（9 处手写 `== k or stem == k` 全部替换）；② `_resolve_title_via_index` 的 SQL 与 `memory_index.find` 必须**同口径**加 `permalink=?`，否则同入参下命中/回退行为不同；③ `memory_rebuild_links` 的 `known` 集合收 `permalink` ⇒ 指向旧标识的双链不再误报死链；④ `_write_memory` 的继承块判 `is None` 而非 `or`（写成 `or path.stem` 会在改名时悄悄改掉冻结值）；⑤ `memory_index` 加列**只能靠 `_migrate()` 的 `ALTER TABLE`**，**`CREATE INDEX` 绝不能放进 SCHEMA** —— 老库无此列时会抛错并中止整个 `executescript`，`_migrate` 连执行机会都没有
  - 专项测试 `tests/test_permalink.py`（25 断言）
- **`memory_schema_diff` 结构对账（第 24 个工具）**（B4）：只读工具，把「写入端承诺写出什么」与「磁盘上实际有什么」对账。**契约来源就是代码**（`yaml_io.build_frontmatter` 是唯一写出源），**不另立 schema 文件**（两份真相必然分叉）。
  键分三层：**core**（`title`/`tags`/`created`/`updated`/`tier`/`access_count`，缺 = 真实缺陷）/ **v2**（`type`/`confidence`/`verified`/`scope`/`status`/`schema_version`，v1 缺属预期、声明 v2 却缺 = 写入端 bug）/ **opt**（按需写出）。⚠️ 必须把 `archived_to` 收为已知键 —— 漏掉它会把 15 条归档 stub 全报成「未知键」。
  输出六节：核心键缺失 / v2 缺键 / v1 存量迁移清单 / 未知键（附行号）/ 可选键使用率 / **无 frontmatter 文件**。⚠️ 关键设计是**区分「无 frontmatter」与「核心键缺失」**：`README.md` / `IDENTITY.md` 这类完全没有 frontmatter 的普通 Markdown 混在 Vault 里，本就不受结构契约约束，首版把它们算成缺陷 ⇒ 11 条里 2 条是噪声；现在单列一类且**不计入缺陷数**。
  - 专项测试 `tests/test_schema_diff.py`（32 断言）
- **`memory.toml` 运行时阈值外置 + 热重载**（B5）：新增 `memory_config.py`，把原先散落在 `server.py` / `locks.py` / `text_utils.py` 的 **8 个阈值**收口到 vault 根的可选 `memory.toml`：`dup_title_jaccard`、`dup_body_jaccard`、`body_truncate_chars`、`lock_timeout`、`heat_cold_max`、`heat_demote_hot_max`、`heat_hot_min`、`heat_decay_lambda`。优先级 **环境变量 `AI_MEMORY_CFG_<KEY>` > `[thresholds]` 区 > 顶层同名键 > 内置默认**；**默认值与改动前的硬编码值逐项相同** ⇒ 不建文件行为零变化。
  - **为什么不用 `tomllib`**：它是 3.11+ 才进标准库，而本仓 CI 矩阵仍覆盖 **Python 3.10** ⇒ 直接用会当场打穿那一格；把最低版本抬到 3.11 属破坏性变更、远超本项范围。改为**自写最小 TOML 子集解析器**（与既有手写 YAML 解析同一条路线）
  - 子集**窄且明确拒绝**：数组、内联表、多行字符串、日期、裸词、缺值、非法键名、未闭合小节、重复键一律 `ValueError`，并给出**行号与原文** —— 配置写错时静默取默认值会让「我明明改了却不生效」极难排查
  - 热重载 TTL 2 秒，且**变更检测用文件内容本身做签名**，不用 `(mtime_ns, size)`：实测本机 NTFS 的 mtime 粒度约 **0.51 ms**，同尺寸连续两次改写在 20 次里有 **10 次** `st_mtime_ns` 完全相同 ⇒ 用 (mtime, size) 会**漏掉**真改动
  - ⚠️ **阈值读取必须走函数、不能写成默认参数**：Python 的默认参数在 `def` 期只求值一次，写成字面量就等于把阈值钉在「进程启动那一刻」，之后改 `memory.toml` 永不生效（`memory_read` / `memory_context` 的 `max_chars` 已改为 `int | None = None`）。`locks.DEFAULT_LOCK_TIMEOUT` / `text_utils.HEAT_DECAY_LAMBDA` 保留为**向后兼容别名**
  - `memory_stats` 新增「运行时阈值配置」小节：打印配置文件路径、8 个生效值（覆写项标 `← 已覆写`）与**最近一次解析错误**（配置坏了要能看见，而不是悄悄退回默认值）
  - 安装脚本同步登记 `memory_config.py`
  - 专项测试 `tests/test_memory_config.py`（79 断言）
- **模块分层守卫**（A2）：原计划用 import-linter，但它要求在 `pyproject.toml` / `setup.cfg` 里声明 contracts —— 本仓刻意**零配置、零构建步骤**（模板仓直接 `cp` 即用），为一条守卫引入打包元数据得不偿失 ⇒ 改为**手写 AST 守卫** `tests/test_import_layers.py`（35 断言，纯标准库）。规则：叶子模块（`locks` / `memory_config` / `memory_index` / `metrics` / `text_utils` / `yaml_io`）**不得** import `server`（含函数体内延迟 import 与 `importlib` 动态拉起）；本地模块之间**无环**；叶子之间的依赖只能在显式白名单内，且白名单**不得腐烂**（登记的边必须真实存在）；`server` 必须 import 全部叶子且**真的加载**（`sys.modules` + 绑定名双重佐证）；本地 import 必须都在模块顶层；新增模块必须登记进清单（否则会从守卫视野里消失 —— 漏检比误报危险）。

### Changed

- **自动生成的 MOC 索引改名为隐藏文件 `.记忆索引.md`**（B6）：生成物混在用户笔记里（既污染 `memory_list`、又会被当成「一篇记忆」被引用/检索）是长期噪声源。改动含**一次性保守迁移**：仅当旧文件 `title` 恰为 `记忆索引` **且** tags 命中 `moc` / `index` / `navigation` 才判定为「确认的生成物」并删除（附带的索引行也一并清除，否则会留下死索引），疑似人工文件一律跳过并告警。
  - 两个连带修复：① `memory_rebuild_links` 的 `known` 集合补 **`f.stem`** —— MOC 用 `[[stem|title]]` 渲染，只认 title 会把 36 篇 `title ≠ stem` 的条目全报成死链；② 安装脚本原先用 shell glob 复制 `template/*`，**点文件会被漏掉** ⇒ 改为 `cp -r "$SCRIPT_DIR/template/." "$MEM_DIR/"`（bash）与 `Get-ChildItem -Force`（PowerShell）
  - 专项测试 `tests/test_hidden_index.py`（33 断言）
- **索引增量化**（B2）：`_iter_entries()` 由「每次全量 walk + 解析」改为两层缓存 —— ① 整库指纹 `_DIR_FINGERPRINT` 未变则直接回放 `_ENTRY_SNAPSHOT`（连 `os.walk`/`stat` 都省）；② 指纹变了则逐文件按 `(size, mtime_ns)` 判新鲜，**只重解析真变化的文件**。判据是「改 1 个文件只新增 1 次解析」（旧实现为 +全库）。
  - 配套：`_refresh_index()` 加**内容签名短路**（MOC 正文只由 `(bucket, stem, title)` 决定 ⇒ 三者未变则跳过渲染与写盘，但**必须同时要求文件存在**，否则索引被删时不会重建）；缓存键由 **title 改为 relpath**（顺带修掉「同名文件互相污染元数据」的遗留缺陷）；返回的 meta 一律**浅拷贝**。
  - ⚠️ **刻意不做「桶级增量渲染」**：MOC 是单文件、写盘量恒定，渲染成本比 frontmatter 解析低一个量级 —— **瓶颈在解析不在渲染**。目标是「写入延迟不随库增长」，不是检索性能
  - 专项测试 `tests/test_index_cache.py`（17 断言）
- **四个只读工具新增 `output_format`**（C1，默认 `"text"`）：`memory_read` / `memory_list` / `memory_recent` / `memory_orphans` 可传 `output_format="json"`，脚本化调用不必再用正则扒字段。
  - **范围即契约**：报告型工具（`stats` / `doctor` / `audit` / `schema_diff` / `context` / `graph`）**刻意不给** —— 它们的产出是「分节标题 + 计数说明 + 提示语」的叙事，整体转 JSON 等于另立一套真相；写工具的返回值是操作回执，结构化无意义。于是范围只覆盖「文本本身就是记录的渲染」的那几个，**JSON 是文本的同源投影而非第二份实现**
  - 非法取值**报错**而不静默回落 `text`：脚本化调用最怕「以为拿到 JSON、实际拿到一句中文」，那样 `json.loads` 会在很远的地方才炸；合法值宽容大小写与首尾空白
  - JSON 的 `body` **不含中文截断说明**（改由 `truncated` / `total_chars` 两个字段表达），否则脚本还得正则剥中文；截断说明仍**逐字节保留**在文本形态里
  - 专项测试 `tests/test_output_json.py`（76 断言）
- **`memory_orphans` 的反链判定改用标识映射**：正文里的 `[[X]]` 有三种合法写法（`title` / 文件名 stem / A3 的 `permalink`），原实现直接拿 target 当 key ⇒ 生成索引里的 `[[stem|title]]` 对「`title ≠ stem`」的条目**压根连不上**，那些条目全被算成孤立；同时 `{title}` / `{stem}` 之类的模板占位文本会被当成真实链接目标、凭空造出反链。现统一经 `id2title` 映射解析。
  另修复：生成页 `.记忆索引` 的链接只是**全库罗列**而非真实引用，把它算作引用方会让所有条目都「被引用过」⇒ **孤立数恒为 0、工具直接失去意义**，现与 `memory_context`（周边遍历排除生成页）、`memory_stats` 同源排除。

### Fixed

- **条目缓存按 title 为键导致同名文件互相污染元数据**（`_ENTRY_CACHE`）：缓存键改为 **relpath**，并拆出条目级缓存、`_ENTRY_STAT`（relpath → `(size, mtime_ns)`）与 `_ENTRY_SNAPSHOT`（整库回放）。此前同名文件会让后读到的 size/mtime 覆盖前一个，且只有重启 MCP 进程才能净化（删索引 db 无效）。
- **`cleanup_residue.py` 的「活索引可 open」与「真实库对账」两项判据从未生效**（工具侧，随本版同批修正）：脚本引用了 `sqlite3` 却**没有 import** ⇒ 两项最关键的验收判据恒报「索引不可读」，属**永远红的假判据**。修好后实测运行版 `entries=248 = 活跃 232 + 归档 16` **MATCH**。

### Dependencies

- 无变化（`requirements.txt` 仍为 `mcp>=1.27,<2`，`requirements-dev.txt` 仍为 `pytest>=8.0` + `ruff>=0.5`）。

### 工程化

- 测试资产由 **17 套 / 594 断言**（v2.3.0 之后）扩到 **25 套 / 964 断言**；本批新增 `test_folders.py`(43) / `test_title_path.py`(42) / `test_dir_policy.py`(71) / `test_index_cache.py`(17) / `test_permalink.py`(25) / `test_schema_diff.py`(32) / `test_memory_config.py`(79) / `test_hidden_index.py`(33) / `test_output_json.py`(76) / `test_import_layers.py`(35)。
- 每项新增判据都做了**可证伪验证**（把产品改回旧行为，断言必须变红）：B5 8/8 根针、B6 7/7、C1 7/7、A2 8/8。其中三次失败的证伪实验反而推动了设计修正：① 依赖时钟/调度的判据不算判据（`os.utime` 钉 mtime + 令测试正文大于默认预算）；② 同一语义若存在「索引快路径 + 全库兜底」两条实现，反证必须**两条一起禁**；③ `test_*` 是独立脚本，`-m pytest` 会 `No module named pytest` 且 rc=1 **无输出**，极易误读成「断言红了」。

## [2.3.0] - 2026-09-18

本版两条主线：**把「记忆怎么被取回来用」补成一个工具**（`memory_context` 上下文组装，跨会话延续从 3~5 次调用降到 1 次），以及**让改动立刻生效**（`memory_restart` 原地热重启，不必重连客户端）。同时把 Phase 3 的三项机制收成闭环 —— 回收站可进可出、重复写入主动提示、热度可预览可落地 —— 并修掉 4 个隐蔽的正确性问题（双 frontmatter 与摘要污染、重复防护漏掉核心页、索引自动生成死链、Windows 下跨进程缓存永不失效）。

### Added

- **`memory_context` —— 上下文组装（第 23 个工具）**：`memory_graph` 只列「出链 + 反链」，跨会话延续仍得把沿途正文一条条读出来（实测 3~5 次调用）。本工具**把沿途内容直接组装成一段可喂给模型的 Markdown**，一次调用拿到起点 + 周边。沿 `[[wikilink]]` 出链与反链 BFS 至 `depth`（`0` = 只要起点），周边按 `tier(hot > warm > cold) → access_count ↓ → updated ↓` 排序取前 `max_related` 条，每块标注来源与关系（`关系 · 深度 · tier · reads · updated`）；`timeframe` 支持自然语言（`last week` / `2 days ago` / `3d` / `2026-09-01`），**只过滤周边、起点恒在**，无法识别时按不限处理并在输出里提示。
  三个设计要点都是实测踩出来的：① `max_chars` 是**整段预算**而非单篇上限，必须分摊（起点至多一半，其余按篇数均分），否则长起点会独吞预算、周边一篇都展不开；② 自动生成的 `记忆索引.md` **必须排除出周边** —— 它的正文就是全库标题列表，会把每个条目都变成「强相关」；③ 截断要回退到行边界（`rsplit("\n", 1)[0]`），否则会把 `_关系 · 深度 · …_` 标注行切成半截。专项测试 `tests/test_context.py`（59 断言）。
- **`memory_restart` —— 原地热重启（第 22 个工具）**：改完 `server.py` 或任一模块后让新代码**立刻生效，不必重连客户端、不必重启应用**。实现为 `os.execv` 原地替换进程映像并保留 stdin/stdout，故 stdio 会话不断。
  稳妥性靠三道闸：① **预检** —— 先在子进程里试导入新代码、并确认「会话恢复补丁」装得上，任一失败即放弃重启，**绝不让起不来的代码顶掉正在服务的进程**；② 本次响应先经管道发出，真正的 `execv` 在延迟线程里执行；③ 新进程凭环境变量知道自己是「接力进程」，把客户端既有会话视为已初始化 —— 否则客户端不重发 `initialize`，新进程会以 "Received request before initialization was complete" 拒掉一切请求。
  细节：Windows 上 `execv` 内部是 `CreateProcess`，**pid 会变**；接力链上限 5 次防重启风暴；本机 Windows + Python 3.13 实测 `os.execve` 给新进程传 env 有约 1/3 概率崩在调用瞬间（压测 15 轮崩 5 轮），`os.execv` + `os.environ` 赋值 20/20 通过 ⇒ 统一走后者。文档另写明**宿主差异**：有客户端给 stdio MCP 套 Job Object 包装器，换进程时包装器随旧映像一起退出，客户端会报 `Not connected` —— 此时宿主会按需重新 spawn，**依旧不用重启应用**。专项测试 `tests/test_restart.py`。

- **热度驱动的 tier 自动升降级**（`memory_heat_suggest(apply=False)`）：此前它只**输出建议字符串**，改完还得手抄一遍 tool call，等于把闭环留给使用者，实际没人会做。现在支持 `apply=True` 真正写盘，同时保持**默认只预览**——不传参数绝不会动你的库。
  关键设计是**预览与执行共用同一个判据函数 `_heat_tier_decision`**：两套代码各写一份阈值判断，迟早漂移成「预览说升、执行说降」，这是这类工具最隐蔽的坑。判据三档：`score < 0.1` 且非 cold → `cold`；`score < 0.5` 且为 hot → `warm`（先降一档，避免从 hot 直接摔到 cold）；`score > 3.0` 且非 hot → `hot`；其余维持现状。`CORE_PAGES` 一律豁免。
  批量写盘走既有 `_write_memory`，v2 字段（`scope` / `status` / `confidence` / `supersedes` 等）由磁盘继承，**不会因为改 tier 而丢失**。专项测试 `tests/test_heat_apply.py`（29 断言）。
- **`memory_search` 中文长句兜底**（bigram 模糊匹配）：`memory_smart_search` 早已用 bigram 分词，但 `memory_search` 一直是**纯子串匹配** —— 短关键词（「路由器」）没问题，长句（「怎么在路由器上跑容器」）作为整体子串必然零命中，「查不到」与「不存在」被混为一谈。
  现改为**精确路径零命中时**才降级到 bigram 兜底，命中门槛为「共享中文 bigram ≥ 2 且 query 覆盖率 ≥ 0.15」——于是常态查询零额外开销，弱相关条目也不会被硬凑进来。兜底结果首行显式标注「以下为 bigram 模糊匹配结果」，**不伪装成精确命中**。长句召回实测 0.00 → 1.00；`tests/test_search_quality.py` 扩到 12 断言（新增 H 组 5 条：长句召回 ≥ 0.80、短关键词零回归、精确命中不得误标为模糊、负样本不得被兜底误召回）。
- **冲突 / 重复主动防护**（`memory_write`）：写入后自动做疑似重复/冲突检测，命中时在返回值末尾追加**非阻断提示**。相似度用字符 bigram 集合的 Jaccard 系数（零依赖、对中文天然友好，绕开 FTS5 分词坑）——标题相似度 ≥ 0.6 判「疑似重复标题」，否则正文 ≥ 0.5 判「疑似重复/冲突内容」，最多提示 3 条。**只提示、不改数据**：是否合并、是否给旧条目标注 `supersedes`/`conflicts` 由写入方（AI / 主人）决定；检测异常静默跳过，绝不影响正常写入。这补上了 2.2.0 落地 `supersedes`/`conflicts` 字段后「字段有了、机制空白」的缺口。专项测试 `tests/test_dup_guard.py`（15 断言）。
- **回收站闭环**（`.trash/`）：`memory_delete` 自 2.0 起默认软删除，但此前**只进不出** —— 文件移进 `.trash/` 后没有任何工具能列出来或取回去，「可恢复」一直停留在纸面。本次补齐三步：
  - `memory_list(include_trash=True)`：列出回收站内容（标题、移入时间、体积）
  - `memory_restore(title, source="trash")`：从回收站取回。**根目录已存在同标题条目时拒绝**并说明原因 —— 回收站里那份可能是被新版取代的旧内容，静默覆盖比不恢复更危险
  - `memory_delete(title, empty_trash=True)`：一键清空回收站（唯一会批量删除的路径，需显式传入，`title` 被忽略）
  - 软删除时把文件 `mtime` 置为移入时刻，回收站列表因而能显示真实的删除时间
  - `.trash/` 天然不参与检索（读取端 glob 非递归）；恢复后 `status` 回到 `active`、`schema_version` 升到当前版本
  - 与既有 `.archive/` 恢复路径互不干扰：`memory_restore` 的 `source` 默认 `"archive"`，行为完全向后兼容
  - 专项测试 `tests/test_trash.py`（34 断言）：软删除退出检索、恢复后索引与字段正确、同名冲突拒绝、同名两条并存、`purge` 与清空、既有归档路径无回归

### Changed

- **`memory_search` 长句检索策略**：此前是**纯子串匹配**，长句作为整体子串必然零命中（「查不到」与「不存在」被混为一谈）。现改为「精确路径零命中时才降级到 bigram 兜底」，命中门槛为共享中文 bigram ≥ 2 且 query 覆盖率 ≥ 0.15 —— 常态查询零额外开销，弱相关条目不会被硬凑进来；兜底结果首行显式标注「以下为 bigram 模糊匹配结果」，不伪装成精确命中。长句召回实测 0.00 → 1.00。
- **`memory_heat_suggest` 由「只预览」变为「可预览可执行」**：新增 `apply=True` 真正写盘，同时保持默认 `apply=False` 只预览 —— 不传参数绝不会动库。预览与执行共用同一个判据函数 `_heat_tier_decision`，避免两套阈值漂移成「预览说升、执行说降」。
- **回收站从「只进不出」变为闭环**：`memory_delete` 软删进的条目，现在可通过 `memory_list(include_trash=True)` 列出、`memory_restore(title, source="trash")` 取回、`memory_delete(title, empty_trash=True)` 清空。
- **`memory_rebuild_links` 改为只读校验**：不再改写文件，只扫描全库双链、报告死链与计数。
- **跨进程缓存失效键升级**：由「目录 `st_mtime`」改为「目录 mtime + 逐文件 `(name, size, mtime_ns)` 指纹」（`_vault_fingerprint()`），任何内容变更（含 Obsidian 保存、其它客户端写入、`git` 改文件）都会令缓存失效。147 篇规模下额外开销约 1~3 ms。

### Fixed

- **双 frontmatter 与摘要污染**（`memory_write` / `_write_memory`）：调用方把「带 frontmatter 的完整笔记」当 `content` 传入时，与写入端自行生成的权威 frontmatter 直接拼接，产出「双 frontmatter」—— 第二块连同 YAML 退化成正文深层垃圾，并固化进 `summary` 字段。全库体检实测命中 8 篇。新增 `yaml_io.strip_leading_frontmatter()` 在写入入口与收口各剥一次，只对「确实像 frontmatter」的块生效，正文开头合法的 `---` 水平线不被误伤；顺带获得自愈能力（已写坏的条目下次写入即恢复）。
- **冲突防护漏掉核心页**（`_find_duplicate_hints`）：上线时无条件跳过 `CORE_PAGES`，导致「新建『近期工作动态记录』而非更新原页」这类最典型的重复写入静默通过。现已让核心页同样参与比对，真正豁免的只有完全同名（属更新）。
- **索引自动生成死链**（`_refresh_index` / `memory_index_draft`）：直接输出 `- [[{title}]]`，而 Obsidian 以文件名 stem 解析双链，标题含空格即全成渲染死链，且每次 `memory_write` 后重建会把人工修复覆盖回去（148 篇库稳定复现 36 条）。新增 `_wiki_link(path, title)`：target 恒用 `path.stem`，可读标题放别名位（`[[stem|title]]`）。
- **跨进程缓存失效在 Windows 上不生效**（`_iter_entries`）：Windows/NTFS 改写已存在文件内容不更新父目录 mtime，外部编辑永远触发不了缓存重建，服务端持续返回陈旧条目。改用文件级指纹后已修复。

### Removed

- **`memory_index_draft` 里从未被使用的错误别名计算**：形如 `f"{title}|{path.stem}"` 的别名方向反了且从未被使用，随索引死链修复一并删除。

### Dependencies

- 无变化（`requirements.txt` 仍为 `mcp>=1.27,<2`，`requirements-dev.txt` 仍为 `pytest>=8.0` + `ruff>=0.5`）。

## [2.2.0] - 2026-09-10

三层补强：**作用域与生命周期**（记忆该在哪个范围生效、哪条还适用）、**工程韧性**（把「多 AI 工具同时写一个库」纳入自动化测试并修复其暴露的并发缺陷）、**可观测性**（服务跑得怎么样）。

### Added

- **作用域 / 生命周期字段**（frontmatter 升到 `schema_version: 2`）：
  - `scope`：`global` / `project` / `temporary`，配 `project` 标识用于项目级隔离
  - `status`：`candidate` / `active` / `stale` / `deprecated` / `archived`
  - `supersedes`：本条结论取代了哪条旧记忆；`conflicts`：与之冲突的条目
  - `source_context`：写入方上下文（如 `workbuddy` / `claude-desktop` / `codex`），可在各客户端 MCP 配置里用 `AI_MEMORY_SOURCE_CONTEXT` 环境变量设定
  - **只做字段层，不做目录分层**：单一根目录 + 字段过滤。`global/ projects/ temporary/` 目录分层被明确否决 —— 服务端有 15 处 glob 扫描是非递归的，搬文件即脱离检索，收益不抵风险
  - **默认排除 archived**：`memory_search` / `memory_list` / `memory_smart_search` 省略 `status` 时自动隐藏归档条目（`stale` / `deprecated` 仍保留 —— 「过时」不等于「不该看见」），被隐藏的命中数会在结果末尾提示，避免误判为「记忆不存在」
  - **零迁移向后兼容**：旧笔记读取端注入默认值（`global` / `active` / schema v1），仅在被显式写入时渐进补齐
  - `memory_archive` / `memory_restore` 自动联动 `status`（`archived` ⇄ `active`），并保留原 scope/project
  - `memory_stats` 新增作用域 / 生命周期 / 写入方上下文 / 结构版本 / 项目五项分布
  - `memory_audit` 新增「生命周期 / 冲突 / 作用域一致性」章节：检出 `supersedes` 与 `conflicts` 的死引用、`scope=project` 却缺 `project`、`status=archived` 却无 `archived_to`
  - **作用域与生命周期专项测试**（`tests/test_scope_lifecycle.py`，70 断言）
- **跨进程并发测试**（`tests/test_concurrency.py`，38 断言；配套子进程工人 `tests/concurrency_worker.py`）：
  覆盖五种真实竞争场景 —— 乐观锁竞争（10 进程同版本写入 → 恰好 1 成功 9 冲突）、无冲突并发写、
  写/归档/删除混合、读写并发（300 次读循环 vs 3 写）、收尾对账（无锁残留 + 索引与磁盘逐行核对）。
  用**真实 subprocess** 而非线程，才能复现多客户端同写一库时的进程级锁与缓存隔离语义。
- **异常恢复测试**（`tests/test_recovery.py`，35 断言）：索引删除→重建、索引垃圾字节→自愈、
  锁文件残留/损坏→陈旧回收、写入窗口内强杀进程→无损坏、外部改写正文→以磁盘为准、
  外部增删文件（如 `git` 切版本）→以磁盘为准、七类畸形 Markdown（0 字节 / 无 frontmatter /
  半截 `---` / 非法 YAML）→工具全部降级不崩、重复重建幂等。
- **检索质量评测**（`tests/test_search_quality.py`，7 断言）：15 篇 fixture（中文 / 英文 /
  代码标识符 / 专有名词）× 20 查询 + 3 负样本，以 recall@10 ≥ 0.95、MRR ≥ 0.80、
  零结果率为 0、负样本必须全空等指标作为**检索质量护栏**。后续任何检索改动都要过这道闸。
- **运行时指标层**（`metrics.py` + `tests/test_metrics.py`，44 断言）：纯标准库，记录检索调用数 /
  零结果率 / 命中数 / 平均耗时、读写次数与未命中率、锁获取与等待时长 / 超时、索引重建次数。
  设计上**绝不阻塞主流程**（全链路 try/except，指标自身崩溃也不外泄）、允许并发下少量丢失
  （多进程 read-merge-write 同一 JSON 非原子，用「不精确」换「零耦合」）、正常退出时自动落盘零头。
  指标摘要已并入 `memory_stats` 与 `memory_audit` 的「## 运行时指标」章节。
  可用环境变量 `AI_MEMORY_METRICS=0` 关闭。

### Fixed

- **索引损坏不自愈**：索引文件被截断或写入垃圾字节后，`_ensure_index` 只是静默返回，
  索引会永久失效。现改为**丢弃并重建一次**（优先截断为 0 字节 —— 部分受限环境会拦截删除动作），
  重建仍失败才保持脏标记并回退全库扫描。
- **把锁竞争误判为索引损坏**（并发测试实测）：SQLite 的 `database is locked` 属 `OperationalError`，
  原判定会在多进程同时访问时丢弃**他人正在使用**的索引文件，引发连锁失败。
  现严格区分「内容损坏」（`DatabaseError` 且非 `OperationalError`）与「锁竞争」。
- **读路径写盘未持锁**：`_flush_access_counts` 由读路径调用却不持锁，与写进程共用同一个
  `<文件名>.tmp` 互相删除（CI 在 Python 3.10/3.11 上偶发 `FileNotFoundError`）。
  现改为自持锁，并把临时文件名加上 pid 后缀。
- **Windows 下写盘替换偶发 `PermissionError(WinError 5)`**：只要有另一进程此刻正打开目标文件读取，
  `os.replace` 就会失败。新增退避重试（累计约 0.75 秒），把「替换瞬间」与「他人读取窗口」错开；
  仍失败则原样抛出，绝不降级为非原子写。POSIX 上该分支不会触发。
- **指标落盘失败会残留 `.tmp` 孤儿文件**：写临时文件成功、`os.replace` 失败（Windows 上目标被占用）
  时会留下 `<name>.<pid>.tmp`。现于失败分支顺手清理 —— 指标层刻意不做退避重试，观测不值得为
  一次写盘阻塞主流程，增量已回收到内存、下次 flush 重写。
- **`memory_smart_search` 过度召回**：时间新鲜度原本可**单独**决定入选，导致任意查询都会把
  「当天更新」的条目塞进结果（负样本用例实测：查询「量子纠缠退相干」返回 10 条完全无关的记忆）。
  现改为零词法命中即非候选，新鲜度只作加权项。

### Security

- **修复两个安装脚本的真实缺陷**：
  - `pip install mcp` **未带版本上界** —— 会装上 mcp 2.x，而 2.x 已把 `FastMCP` 更名为 `MCPServer`，装完启动即 `ImportError`。现固定为 `mcp>=1.27,<2`
  - **只复制 `server.py`、不复制依赖模块** —— `server.py` 依赖 `memory_index` / `yaml_io` / `text_utils` / `locks` / `metrics`，按原脚本装完必然启动失败。现改为整组复制
- **安装脚本顶部加安全声明**：明确列出脚本会做的三件事、不会做的事（不读写你的其他文件、不改 shell 配置或注册表、不请求 sudo/管理员权限、不常驻、除 pip 装依赖外不联网）
- **README 安装章节改为三种方式对比**：下载后审阅再执行（推荐）/ 一行管道执行 / clone 后本地运行，并明确提示管道执行看不到脚本内容
- **发布链路加入 SHA256 校验**：新增 `.github/workflows/release.yml`，打 `v*` tag 时**先跑完全部测试**再发布 Release，并把各文件的 SHA256 写进 Release 说明 + 作为附件上传（`scripts/make_checksums.py` 生成）。哈希取自 git blob 字节而非工作区 —— 仓库用 `.gitattributes` 把 `*.sh` / `*.py` 强制为 LF，而 Windows 工作区是 CRLF，直接哈希工作区文件会得出与用户下载内容不同的结果
- 新增环境变量 `AI_MEMORY_SKIP_PIP=1`（依赖已就绪时跳过脚本里的 pip），安装脚本支持 `AI_MEMORY_DIR` 指定记忆库目录

### Changed

- **CI 测试步骤改为遍历 `tests/test_*.py`**：新增回归测试无需再改 CI（子进程工人
  `tests/concurrency_worker.py` 不匹配该模式，不会被误执行）。CI 断言总数 77 → 271。
- **CI lint 目标加入 `metrics.py`**。
- `.gitignore` 用一条通配 `memory_index_*.db*` 覆盖索引全部派生物（`.db` / `-wal` / `-shm` / 测试用 `.db.orphan`），
  并新增 `memory_metrics_*.json`。

## [2.1.0] - 2026-09-10

引入**记忆可信度字段**，区分「用户明说的事实」与「AI 自行推断」，遏制 Memory Poisoning（AI 推测被写入后被后续会话当成事实复用）。

### Added

- **记忆可信度字段**：frontmatter 新增 `type` / `confidence` / `verified` / `verified_at`
  - `type`：9 种取值 —— `fact`（事实）/ `preference`（偏好）/ `decision`（决策）/ `experience`（经验）/ `episodic`（事件）/ `project`（项目）/ `constraint`（约束）/ `workflow`（工作流）/ `temporary`（临时）
  - `confidence`：`high` / `medium` / `low`
  - `verified` + `verified_at`：是否已由用户确认或事实核验。置 `true` 自动记录当日日期，置 `false` 自动清除日期（避免「未验证却有验证日期」的矛盾态）
  - **零迁移向后兼容**：读取端为不含新字段的历史笔记注入默认值（`fact` / `medium` / `false`），磁盘文件保持原样，仅在下次被显式写入时渐进补齐 —— 无需一次性全库迁移
- **`memory_write` / `memory_update_metadata` 新增参数**：`mem_type` / `confidence` / `verified`。非法取值直接拒绝并提示合法取值；更新未传字段时继承磁盘原值，不会把已有可信度重置为默认
- **`memory_search` / `memory_list` / `memory_smart_search` 新增 `mem_type` 过滤**
- **搜索结果行内可信度徽章**：形如 ` | type=decision conf=high ✓verified`，一行摘要即可判断来源与可信度，无需逐条读取
- **`memory_stats` 新增两节统计**：记忆类型分布、可信度分布（含已核验计数）
- **记忆可信度专项测试**（`tests/test_p0_credentials.py`）：24 条断言，覆盖写入落盘 / 更新继承 / 非法值拒绝 / 历史笔记兼容 / 检索过滤 / 统计 / 序列化边界

### Changed

- **README 全量重写**：改为以仓库名 `ai-memory-template` 为标题（原为「多平台AI融合记忆库」，与仓库名不一致）；新增目录导航与「实际使用效果」对话示例；补充 FAQ 与「写入即自动化」「工程化保障」特性说明；工具速查表补齐全部真实默认参数
- **修正 README 与实际不符的两处描述**：
  - 依赖约束原写 `mcp>=1.27`，实际已锁定为 `mcp>=1.27,<2`（并补充 mcp 2.x 更名 `MCPServer` 的说明）
  - 自动标签规则原写「约 30 条」，实际 `_TAG_AUTO_MAP` 为 **68 条**
- CI 徽章改用 GitHub Actions 原生 badge URL
- CI 测试步骤加入 `tests/test_p0_credentials.py`（测试断言总数 53 → 77）

### Fixed

- **清除 `rules/` 与 `template/` 下 7 个 Markdown 文件的 UTF-8 BOM**：BOM 会顶掉 YAML frontmatter 的起始 `---`，使文件被当作无元数据解析（`rules/记忆写入格式规范.md` 受影响最直接）。`install.ps1` 刻意保留 BOM —— 旧版 Windows 终端依赖它以正确识别中文
- **移除仓库内两处私有路径引用**：`rules/记忆写入格式规范.md` 的 vault 位置说明、`tests/test_roundtrip.py` 的注释，统一改为通用默认值（`~/ai-memory` / `AI_MEMORY_DIR` 环境变量）

## [2.0.0] - 2026-09-10

引入 SQLite 元数据索引层，属结构性变更，故升主版本号。

### Added

- **SQLite 元数据索引层**（`memory_index.py`）：`memory_read` / `memory_list` 的标题定位从 O(n) 全库扫描降为 O(1)。仅使用标准库 `sqlite3`，不引入外部依赖。
  - md 文件仍是唯一事实源：索引只镜像 frontmatter 元数据，正文永不入库
  - 以 relpath 为主键，避免 `.archive/` 副本与根 stub 同标题互相覆盖
  - 通过 (mtime, size) 校验新鲜度，外部直接改文件后自动回退全库扫描并自愈
  - 索引损坏或缺失时静默回退，功能不降级；缓存文件可随时删除重建
- **回归测试**（`tests/test_roundtrip.py`）：28 条断言，覆盖 frontmatter 写读无损、反斜杠不雪崩、`read` 不污染 `updated`、`max_chars` 截断、文件名安全、SQLite 索引层（命中/miss/自愈/清理）
- **全工具冒烟测试**（`tests/test_tools_smoke.py`）：25 条断言，逐个调用 20 个 MCP 工具并校验工具注册完整性
- **CI 工作流**（`.github/workflows/ci.yml`）：Python 3.10 / 3.11 / 3.12 / 3.13 矩阵跑回归测试 + ruff lint
- **依赖声明**：`requirements.txt`（运行时）、`requirements-dev.txt`（开发/测试）
- **工程配置**：`ruff.toml`（lint 规则）、`.gitattributes`（强制 LF 换行）
- **`memory_read` 的 `max_chars` 参数**：默认截断正文至 8000 字符，`max_chars=0` 取全文
- **`memory_graph` 的 `limit` / `include_all` 参数**：默认截断至 10 条，`include_all=true` 取全量

### Changed

- **`server.py` 模块化拆分**：把无状态工具层抽为独立模块 —— `yaml_io.py`（frontmatter 解析/序列化）、`text_utils.py`（文件名/时间/热度工具）、`locks.py`（跨进程文件锁）。`server.py` 从约 1940 行降至约 1600 行，仅保留工具注册、存储层与业务逻辑。对外行为、工具签名与存储格式完全不变（由 28 + 25 条测试护航）
- `MEMORY_DIR` 默认值改为 `~/ai-memory`（保留 `AI_MEMORY_DIR` 环境变量覆盖）
- `memory_write` 不再写入 `links` 字段；`memory_rebuild_links` 改为只读校验报告，不再改写文件
- `memory_read` 只返回核心元数据（title / tags / summary / created / updated / tier / access_count / source）
- 反向链接改以正文扫描为唯一事实源

### Fixed

- **`read` 污染 `updated` 时间戳**：`_flush_access_counts` 不再改写 `updated`，读取操作不再影响 30 天归档语义
- **summary 反斜杠雪崩**：`_parse_scalar` 补充 YAML 双引号反转义，读写对称，含 Windows 路径的摘要不再逐轮翻倍
- `memory_write` 新建 `tier=cold` 条目时补触发自动归档
- `memory_delete(purge=True)` 现在也会搜索 `.trash` 目录
- `memory_archive_old` 复用全局 `CORE_PAGES` 保护名单（此前本地副本缺少 2 个页面）
- `memory_archive` 归档后补充调用 `_invalidate_cache()`
- 7 处扫描循环补充异常保护，单个损坏文件不再中断整轮扫描
- `_TAG_AUTO_MAP` 移除重复的 `docker` → `deploy` 映射
- 清理 5 个配置/脚本文件的 UTF-8 BOM（JSON/TOML 带 BOM 会导致部分解析器报错）
  - 注：`install.ps1` 有意保留 BOM，因 Windows PowerShell 5.1 依赖 BOM 识别 UTF-8 中文

### Removed

- `_rebuild_title_index` / `_KNOWN_TITLES` 死代码链路（自动补链停用后已无消费者）
- `memory_write` / `memory_rebuild_links` 的自动补链行为

### Dependencies

- 锁定 `mcp>=1.27,<2`。注意 mcp 2.x 已将 `FastMCP` 更名为 `MCPServer`，本项目基于 v1 API，不兼容 2.x

## [1.1.0] - 2026-08-26

### Added

- 新增 4 个工具：`memory_recent` / `memory_update_metadata` / `memory_restore` / `memory_rebuild_links`，工具总数达 20 个
- 自动生成 MOC 索引（`记忆索引.md`）
- 健康度增强：空条目 / 死链 / 命名漂移检测
- `read` / `delete` / `archive` / `graph` 支持「标题或文件名」双路匹配

### Changed

- 存储格式从 JSON frontmatter 迁移为 **YAML frontmatter**（Obsidian 原生可读），历史 JSON 格式仍可读
- `memory_write` 补充非空校验与 source 默认值

## [1.0.0] - 2026-07-22

首个公开版本。

### Added

- 16 个 MCP 工具：写入 / 读取 / 搜索 / 删除 / 关系图 / 孤儿检测 / 统计 / 归档 / 批量操作 / 智能搜索等
- 三项自动机制：写入时按关键词建议标签、按已知标题自动补链、冷条目自动归档
- 跨进程文件锁，支持多 AI 工具并发写入
- 内存级 access_count 缓存（批量落盘）与条目缓存（写入时失效）
- 全操作日志输出到 stderr
- `AI_MEMORY_DIR` 环境变量配置存储路径
- 跨平台安装脚本：Windows PowerShell + Linux/macOS bash
- 零外部依赖：仅需 Python 3.10+ 与 `mcp` 包

[Unreleased]: https://github.com/dpkg-s/ai-memory-template/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/dpkg-s/ai-memory-template/compare/v2.5.0...v3.0.0
[2.5.0]: https://github.com/dpkg-s/ai-memory-template/compare/v2.4.2...v2.5.0
[2.4.2]: https://github.com/dpkg-s/ai-memory-template/compare/v2.4.1...v2.4.2
[2.4.1]: https://github.com/dpkg-s/ai-memory-template/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/dpkg-s/ai-memory-template/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/dpkg-s/ai-memory-template/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.2.0
[2.1.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.1.0
[2.0.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.0.0
[1.1.0]: https://github.com/dpkg-s/ai-memory-template/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v1.0.0
