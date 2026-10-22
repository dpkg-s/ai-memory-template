> 本页由仓库主页拆分而来，内容与拆分前逐字一致。返回 [主页](../README.md)。

# 快速开始

### 环境要求

- **Python 3.10+**
- 运行时依赖：`pip install -r requirements.txt`（仅 `mcp`，约束为 `mcp>=1.30,<2`）
- 开发/测试额外依赖：`pip install -r requirements-dev.txt`

> **注意版本上界**：依赖锁定在 `mcp<2`。mcp 2.x 已将 `FastMCP` 更名为 `MCPServer`（`mcp.server.fastmcp` 模块整个被删），装到 2.x 会直接 import 失败；若使用了 `memory_restart`，2.x 还移除了 `InitializationState`，其会话恢复补丁同样会失效。
>
> **版本下限 1.30**：`mcp.server.fastmcp` / `mcp.server.session.InitializationState` / `mcp.types.ToolAnnotations` 在 1.30.0 上均完好（2.5.0 时实测 26 套 / 1055 断言，本版已增至 1092），并新增 `session_idle_timeout` / `max_sessions` 两个 HTTP 会话治理参数。注意 **1.x 线已进入维护模式**，上游此后仅收安全修复。

### 方式一：交给 AI 助手自动部署（推荐）

把下面这段直接发给你的 AI 助手：

```
请帮我部署 ai-memory-template 这个 MCP 项目，地址：
https://github.com/dpkg-s/ai-memory-template

步骤：
1. 克隆或下载项目到本地
2. 安装依赖（pip install -r requirements.txt）
3. 创建记忆库目录
4. 根据我的操作系统（Windows / macOS / Linux）配置好 MCP 客户端
5. 告诉我怎么验证它是否正常工作
```

### 方式二：安装脚本

脚本会依次：安装 `mcp` 包（`mcp>=1.30,<2`）→ 创建 `~/ai-memory/` → 复制初始模板与规则 → 复制 `server.py` 及其依赖模块 → 打印各平台的 MCP 配置片段。

**安装脚本会改动你的系统状态，请选择你愿意承担的方式：**

| 方式 | 命令 | 适用 |
|------|------|------|
| 下载后审阅再执行（**推荐**） | 见下方 | 想先看清脚本做了什么 |
| 一行管道执行（最省事） | `curl -sSL ... \| bash` / `irm ... \| iex` | 已信任本仓库，或只是快速试一下 |
| clone 后本地运行 | `git clone ... && bash install.sh` | 想同时保留完整的源码与测试 |

**macOS / Linux：**

```bash
# 推荐：先下载、读一遍，确认无误再执行
curl -sSLO https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh
less install.sh
bash install.sh

# 或者：一行管道执行（看不到脚本内容，谨慎使用）
curl -sSL https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh | bash
```

**Windows PowerShell：**

```powershell
# 推荐：先下载、读一遍，确认无误再执行
Invoke-WebRequest -Uri https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 -OutFile install.ps1
Get-Content install.ps1
.\install.ps1

# 或者：一行管道执行（看不到脚本内容，谨慎使用）
irm https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 | iex
```

**脚本行为声明**（两个脚本顶部都有同样的注释块，可自行核对）：只做三件事 —— 用 pip 装 `mcp`、创建记忆库目录并写入模板、把 `server.py` 与依赖模块复制过去。**不会**读写你的其他文件、不会修改 shell 配置或注册表、不请求 sudo/管理员权限、不在后台常驻、除 pip 装依赖外不联网。

**校验完整性**：二进制附件（`ai-memory-setup.exe`）在 Release 里同附 `<文件名>.sha256`，下载后可直接校验：

```bash
sha256sum -c ai-memory-setup.exe.sha256
```

`install.sh` / `install.ps1` 与各 `.py` 模块**不再提供官方校验和** —— 2026-10-16 起弃用了合并式的
`SHA256SUMS.txt`，因为那份清单与实际安装集不同步（曾漏掉 `memory_config.py`），继续维护反而误导。
需要核对脚本时，请用「下载后读一遍再执行」的方式，或直接与仓库中对应 tag 的文件比对：

```bash
git show v3.1.2:install.sh | sha256sum
sha256sum install.sh                       # macOS: shasum -a 256 install.sh
```

```powershell
Get-FileHash install.ps1 -Algorithm SHA256
```

可用环境变量：`AI_MEMORY_DIR`（指定记忆库目录）、`AI_MEMORY_SKIP_PIP=1`（依赖已就绪、跳过 pip）。

### 方式三：手动安装

```bash
git clone https://github.com/dpkg-s/ai-memory-template.git
cd ai-memory-template
pip install -r requirements.txt

bash install.sh      # macOS / Linux
.\install.ps1        # Windows PowerShell
```

### 配置 AI 客户端

把下面的配置片段加入对应客户端的 MCP 配置文件，路径按你的实际情况替换。

**WorkBuddy** — `~/.workbuddy/mcp.json`

```json
{
  "mcpServers": {
    "ai-memory": {
      "type": "stdio",
      "command": "python",
      "args": ["/path/to/ai-memory/server.py"]
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ai-memory": {
      "command": "python",
      "args": ["/path/to/ai-memory/server.py"]
    }
  }
}
```

**Codex CLI** — `~/.codex/config.toml`

```toml
[mcp_servers]
[mcp_servers.ai-memory]
command = "python"
args = ["/path/to/ai-memory/server.py"]
```

> 完整模板（含 `AI_MEMORY_DIR` 可选配置）见 [`setup/`](../setup/) 目录。

### 方式四：交给「记忆库管家」自动完成（推荐）

上面两步（装程序、配客户端）可以交给 `ai_memory_setup.py` 一条命令做完。它不只安装，还管两件事：**记忆库目录**和**AI 工具连接**。

```bash
# 交互式管家菜单（四分区：安装 / 记忆库 / AI 连接 / 自检）
python ai_memory_setup.py

# 或者直接下子命令，适合脚本化 / 批量
python ai_memory_setup.py install          # 装或更新
python ai_memory_setup.py versions         # 核验已装版本 + 列出远端可选版本（只读）
python ai_memory_setup.py check            # 环境自检
python ai_memory_setup.py vault list       # 记忆库目录：扫描候选
python ai_memory_setup.py link scan        # AI 工具连接：看状态
python ai_memory_setup.py link apply       # 把扫描到的工具接上
```

> 也提供打包好的单文件 exe，无需预装 Python：
> - `ai-memory-setup-cli.exe` —— **控制台版，包含上面的全部管家功能**（`vault` / `link` / 交互菜单）
> - `ai-memory-setup.exe` —— 图形版，**三步引导式安装**（见下），逻辑仍走同一份 `ai_memory_setup.py`

#### 图形版：五步引导

图形版把「装在哪」「装什么」「接到哪」拆成一条引导链：

| 步骤 | 你做的事 | 它会做的事 |
|---|---|---|
| ① 记忆库目录 | 从扫描结果里挑一个，或选空目录新建 | 把候选库按「像不像记忆库」排序列出 |
| ② 程序本体 | 一般不用动 | 自动填成 `<库目录>/ai-memory`，随第 ① 步联动；想分开放就点「浏览…」，之后可用「恢复自动」回到派生值 |
| ③ 附加组件 | **一个勾选框**：想多开入口就勾上（顺便定 Web 端口），不用就留空 | 一次装齐两样：HTTP 的两个 `.py` + `start-http` 脚本，加上 Web 的整棵 `web-console/` + `start-web` 脚本 |
| ④ 确认安装 | 看一眼摘要，选安装源与版本，按「开始安装」 | 摘要列出库目录 / 程序本体 / **已装实例的核验结论** / 附加组件；版本下拉标出「最新 / 已装」；安装源、执行模式、高级选项都在这一步 |
| ⑤ 接入 AI 工具 | 勾选要接的客户端，按「接入选中的」 | 安装成功后**自动跳到这一步并扫描本机**，列出 21 个客户端里哪些已装、哪些能自动写；写前备份、写后回读，失败即回滚 |

**为什么 HTTP 与 Web 是同一个勾选框**：两者回答的是同一个问题 ——「要不要多开一个入口」，而且**天然互斥不了**：控制台是 MCP 的前端代理，读写全走 HTTP 端点，没有 HTTP 的 Web 就是个打得开、列表全空的壳。分成两个框，「勾了 Web 没勾 HTTP」这个**看起来装好了、实际读不到数据**的状态就会一直存在，只能靠警告文案去兜；合成一个勾选框，这个半残状态在界面上根本无法被表达出来。勾选框下面的两行说明仍然把两块内容分开讲 —— 一个开关，不等于一句话糊过去。

**为什么装完要进第 ⑤ 步**：装好不等于能用。21 个客户端各有自己的配置文件位置和格式（JSON / TOML / 有的还要手工改结构），用户不可能自己去翻。这一步复用 `setup_link` 的扫描与写入 —— 与命令行的 `link scan` / `link apply` **是同一份代码**，所以「界面上接上的」和「命令行接上的」结果必然一致。默认只勾「能自动改且确实需要改」的，已连上的不动、需手工的只提示不硬写。

第 ④ 步自上而下的区块顺序是：**安装源（含版本下拉）→ 执行模式 → 高级选项**（附加组件已整体上移到第 ③ 步，确认页不再重复出现同一个开关）。

三点设计取舍：**高级选项常驻展开**（不再做成 `▸` 折叠，剩余参数一眼可见）；**取消「与程序安装目录相同」勾选框** —— 新旧模型里程序位置由库目录派生，那个开关已经没有意义；**窗口高度按内容实测**，不再写死 —— Windows DPI 125% 下同样的内容会多要约 60px，写死的高度必然把最后一个区块切掉。

#### 第 ③ 步：附加组件装成什么样

```bash
python ai_memory_setup.py install --with-optional                 # 只装 HTTP 传输层
python ai_memory_setup.py install --with-optional --with-web      # 传输层 + Web 控制台（图形版这一勾装的就是这套）
python ai_memory_setup.py install --with-optional --with-web --web-port 8812   # 顺便换 Web 端口
```

图形版的第 ③ 步把上面第 2 条包成一个勾选框；命令行仍保留两个独立开关 —— CLI 的调用方往往是被脚本编排的，「只装 HTTP」在多机共用场景下确实有意义，限制它是没必要的。**只勾 `--with-web` 不带 `--with-optional` 时预览阶段会直接警告**（控制台能开、数据全空），而不是装上再说。

| 组件 | 落点 | 内容 |
|---|---|---|
| HTTP 传输层 | `<程序目录>/` | `transport_http.py` / `serve_http.py` + `start-http.cmd` / `.sh` |
| Web 控制台 | `<程序目录>/web-console/` | `serve_web.py` + `web_console.py` + 整个 `web/` 前端目录 + `requirements.txt`（约 55 个文件，**保持相对结构** —— 控制台按「与本文件同级的 `web/`」找静态资源，平铺就散架）+ `start-web.cmd` / `.sh` |

`--web-port` 等价于设 `AI_MEMORY_WEB_PORT`（端口只有一个事实源，记录 / 脚本 / 提示文案全从 `_web_defaults()` 读）。安装记录新增 `web` 块：`host` / `port` / `token`（**只记有没有，不落值**）/ `mcp` / `dir` / `entry` / `files` / `start`。

三条刻意的行为：

- **源里没有控制台就如实说**。远端 zip 不含 `web-console/` 子模块是正常情况，此时报「安装源里没有 Web 控制台 —— 本次跳过」并给出补救办法，而不是假装装好了。
- **不装 HTTP 就不装 Web 的沉默陷阱**。控制台是 MCP 的前端代理，读写全走 HTTP 端点；只装 Web 不装 HTTP 时，控制台能打开、列表却是空的。图形版用一个勾选框从根上消掉了这个组合，命令行则在预览阶段直接警告。
- **不勾选时不动旧文件**。目录里已有 `start-web.cmd` / `start-http.cmd` 却本次没勾，只提示「未改动它们」——不做静默清理。

`web-console/` 目录不参与完整性校验（不在 `files` 指纹里）：它是整棵子树、文件数多，且升级替换频繁，逐文件指纹只会制造噪音。

#### 版本核验与版本选择

「已装版本」在旧版是个**声称**：本工具在程序目录写 `.ai-memory-install.json` 记下版本号，之后就一直转述这个数字。但它可能过期（有人手工拷了新文件覆盖）、也可能整个文件就不在（用 `git clone` 或 `install.ps1` 装的）。**声称不等于事实**，所以有了核验：

```bash
python ai_memory_setup.py versions             # 核验 + 列出全部可选版本
python ai_memory_setup.py versions --ref v2.5.0  # 顺便标出指定版本在列表里的位置
python ai_memory_setup.py install --ref v2.5.0   # 装指定版本（回退也走这条路）
```

核验靠三条证据交叉：

| 证据 | 说明 |
|---|---|
| `.ai-memory-install.json` 的 `installed_version` | 我们自己写的，可能过期 |
| 目录里的 `CHANGELOG.md` | 随源码发下来的独立证据（安装器不装它，故只在源码目录就地安装时存在） |
| 记录里各文件的 `sha256` | 能查出「文件被改过 / 被截断 / 莫名缺失」 |

结论落在四态上：`未安装` / `有程序文件、无安装记录` / `记录与文件不一致` / `完好`，且版本号**必须带证据等级**一起报 —— 否则「v3.0.0」看起来像结论，实际只是一句转述。两者打架时**以 CHANGELOG 为准**：磁盘上的文件才是真跑起来的东西。

两个刻意的保守：

- 记录里没有文件指纹时**不报「完好」**，改报「完整性未核验」—— 没查过和查过是两件事。
- **不**拿当前 `CORE_FILES` 去要求旧版本。否则「老版本少一个后来才拆出来的模块」会被误判成文件损坏，然后劝你去强制重装。

版本列表走两条通道：GitHub API（`?per_page=100`）→ `git ls-remote --tags`（API 匿名限流 60 次/小时，这条不限）。都拿不到时返回空列表并提示 `--ref` 手填，**不当成「远端没有版本」**。回退会在执行前二次确认 —— 装低版本后若客户端用到新版才有的能力，报错看起来跟安装器毫无关系，事后极难归因。

#### `vault` —— 记忆库目录管理

本仓一个容易踩的坑：程序目录和记忆库目录**可以分开**，而 `server.py` 在拿不到 `AI_MEMORY_DIR` 时会回落到 `~/ai-memory`。如果你的记忆库在 `D:\ai记忆`，而 `~/ai-memory` 是个空目录，AI 就会连上一个空库，还一切「正常」。

`vault` 解决这件事：

```bash
python ai_memory_setup.py vault list           # 扫描候选目录并按「像不像记忆库」打分
python ai_memory_setup.py vault use D:\ai记忆   # 指定当前库（同时写给 .ai-memory-install.json）
python ai_memory_setup.py vault init D:\new    # 从零建库（播种 template/ 与 rules/）
python ai_memory_setup.py vault path           # 只打印当前生效路径
```

打分权重：`记忆索引.md` +40、md 条目数（上限 +60）、`.git` +10、索引库 +10、同目录有 `server.py` +15。候选来源按可信度排序：`AI_MEMORY_DIR` 环境变量 → 安装元数据 → 客户端配置里的引用 → 上次选择 → 默认路径。

#### `link` —— 扫描并接入 AI 工具

```bash
python ai_memory_setup.py link clients          # 打印支持的 21 个客户端与三级路径可信度
python ai_memory_setup.py link scan             # 逐个判定状态
python ai_memory_setup.py link apply            # 真写配置（写前备份、写后回读，失败回滚）
python ai_memory_setup.py link apply --dry-run  # 只预演：打印将改什么 + 手工片段
python ai_memory_setup.py link apply --client codex,workbuddy   # 限定客户端
```

状态分七类：`已连接` / `待创建` / `未连接` / `需确认`（env 缺失但程序指向对）/ `待修正`（漂移）/ `需手工`（**确实要你动手、但本工具不能代写**）/ `未安装`。注意 `需手工` 只表示「我们写不了」，**已配好的照样报 `已连接`** —— 两者是两件事。写入要点：

- **合并而非替换** —— 只增删 `ai-memory` 这一个条目，`description`、`type`、其它 `env` 键原样保留；Codex 的 `[mcp_servers.ai-memory.tools.*]` 子段也整段留住。
- **写前备份、写后回读** —— 同目录 `.bak-<时间戳>`，`os.replace` 原子替换，回读解析失败即回滚。
- **保留 CRLF** —— 按字节读再解码，不做通用换行转换，否则一次写入会把整个配置文件的行尾翻掉。
- **路径分级** —— 已验证（本机实测）/ 可新建（文档稳定，可直接创建）/ 仅修改（路径随版本变，只在文件已存在时改，绝不新建）。文件是否存在本身就是这个猜测路径的校验器。

> **Python 3.10 限制**：读 TOML（Codex 配置）依赖 `tomllib`（3.11+）。在 3.10 上 JSON 客户端不受影响，TOML 客户端会明确报错而不是静默改坏。建议用 3.11+。

### 验证是否正常

在 AI 对话里说「把今天的工作记到记忆库里」，观察 AI 是否调用了 `memory_write`。也可以直接让 AI 执行：

```
调用 memory_stats 看看记忆库状态
```

返回 tier 分布、访问排行、更新量等统计即表示服务正常。

---
