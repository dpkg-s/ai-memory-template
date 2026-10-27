> 本页由仓库主页拆分而来，内容与拆分前逐字一致。返回 [主页](../README.md)。

# 特性

### 零外部依赖

只要 Python 3.10+ 和一个 `pip install mcp`。不需要数据库服务、不需要容器、不需要云账号。

> **关于 SQLite 索引**：`memory_read` / `memory_list` 的标题定位走 Python 内置的 `sqlite3`（标准库自带，不是外部依赖），把 O(n) 全库扫描降为 O(1)。索引只是运行时自动生成的**缓存文件**，仅镜像 frontmatter 元数据 —— 正文永不入库、始终实时读盘，**md 文件是唯一事实源**。索引损坏或缺失时自动回退全库扫描，功能不降级；文件删掉即可重建。

### 纯文件存储

所有记忆都是**纯 Markdown 文件**。这意味着：

- 用 **Obsidian** 打开浏览、编辑、看图谱
- 用 **git** 做版本管理、多设备同步
- 用 **grep** 全文搜索，用任何文本编辑器改
- **备份 = 复制一个文件夹**

### 跨平台 · 跨 AI 客户端

MCP 是开放协议，服务端默认以 stdio 传输，因此可接入：

- **WorkBuddy** — 国产 AI 助手
- **Codex CLI** — OpenAI 终端 AI 助手
- **Claude Desktop** — Anthropic 桌面客户端
- **Cursor** — AI 代码编辑器
- 任何支持 MCP stdio 的工具

需要**远程 / 多机共用**同一个记忆库时，另有可选的 Streamable HTTP 入口（见 [远程访问（可选 Streamable HTTP）](#远程访问可选-streamable-http)）；想在浏览器里直接观察与调试记忆库，另有可选的 [Web 控制台（v3.1，可选）](#web-控制台v31可选) —— 它可以在**安装引导的第 ③ 步一并装好**，不必自己去 clone 独立仓。两者都默认关闭，不影响上面任何一种用法。

### 写入即自动化

AI 调用 `memory_write` 时，服务端自动完成：

- **自动标签** —— 正文关键词推断标签，内置 68 条映射规则，覆盖常见技术领域

  ```
  输入正文: "用 Python 写了一个 Docker 部署脚本"
  自动标签: [deploy, python]
  ```

- **自动归档** —— 写入 `tier=cold` 的条目自动移入 `.archive/`，原位只留摘要 stub
- **安全防护** —— 拒绝空内容；同标题自动 upsert；`expected_version` 乐观锁防并发覆盖

### 记忆可信度

AI 会写记忆，也会**推断**。若两者混在一起、默认可信，AI 的猜测迟早会被后续会话当成事实复用 —— 这就是 Memory Poisoning。因此每条记忆都带可信度标记：

| 字段 | 取值 | 说明 |
|------|------|------|
| `type` | `fact` / `preference` / `decision` / `experience` / `episodic` / `project` / `constraint` / `workflow` / `temporary` | 这条记忆是什么性质 |
| `confidence` | `high` / `medium` / `low` | 可信度 |
| `verified` | `true` / `false` | 是否已由你确认或经事实核验 |
| `verified_at` | 日期 | 核验日期（`verified=false` 时自动清除） |

```yaml
---
title: 部署方式定案
type: decision          # 这是一条决策，不是随口一提
confidence: high
verified: true          # 用户明确拍板过
verified_at: 2026-09-10
---
```

检索时可按 `mem_type` 过滤，命中结果行内直接带徽章：

```
- [warm] **部署方式定案** (score=12.0, workbuddy) | type=decision conf=high ✓verified
```

**两种来源不混淆**：AI 自行推断写入的内容应保持 `verified: false`，只有用户明确陈述或经核验才置 `true`。旧笔记无需迁移 —— 读取时自动按 `fact` / `medium` / 未核验处理，被写入时才渐进补齐。

### 作用域与生命周期

一条记忆除了「可信吗」，还要回答「在什么范围内生效」「现在还作不作数」：

| 字段 | 取值 | 说明 |
|------|------|------|
| `scope` | `global` / `project` / `temporary` | 生效范围；配 `project` 标识可做项目级隔离 |
| `status` | `candidate` / `active` / `stale` / `deprecated` / `archived` | 生命周期状态 |
| `supersedes` | 标题 | 本条结论取代了哪条旧记忆 |
| `conflicts` | 标题列表 | 与之矛盾的条目 |
| `source_context` | 字符串 | 写入方上下文（哪个客户端写的） |

```yaml
---
title: 项目_xxx_索引层定案
scope: project
project: xxx
status: active
supersedes: 项目_xxx_旧索引方案
---
```

**检索默认隐藏 `archived`**，但 `stale` / `deprecated` 仍会返回 —— 「过时」不等于「不该看见」，读者需要知道旧结论存在、以及什么取代了它。`memory_archive` / `memory_restore` 会自动联动 `status`。

**字段与目录并存**：`scope` 是**字段层**语义（过滤视角），目录分层是**组织层**手段（人怎么看）。v2.4.0 起两者并存 —— 见下一节。

### 目录分层与写入端自动归类

笔记多了之后，「全堆在根目录」和「全丢进子目录」都不好。v2.4.0 起支持**任意层级的子目录分类**（上限 3 级），并且写入端会**按语义自动归位**：

- **标题里带 `/` 就是路径** —— `memory_write("项目/记忆库/索引层定案", ...)` 会写到 `项目/记忆库/索引层定案.md`。每一段都会被消毒（非法字符 → `_`、丢弃控制字符与空段、去首尾 `._`、每段 80 字上限）；**不含 `/` 的标题行为与旧版逐字节一致**，历史笔记无需搬家
- **保留目录显式拒绝**：`.archive/` `.trash/` `.git/` `.obsidian/` 等 8 个目录、`.` / `..` 段、盘符、绝对路径、层级 > 3 一律**报错并说明原因**。消毒不能兜住这些 —— 静默改写会把文件写进不该写的地方却毫无提示
- **写入端自动归类**由环境变量 `AI_MEMORY_DIR_POLICY` 控制四档：

  | 档位 | 行为 |
  |------|------|
  | `warn`（**默认**） | 只提示「这条更适合放到 `工具/`」，**不改你的落点** |
  | `auto` | 先按**标题前缀**命中 vault 里既有的分类目录，否则按语义桶（11 个）落到规范目录 |
  | `strict` | 无可归的规范目录时**拒写** |
  | `off` | 完全关闭 |

  默认保守是有意的：目录落点会改变「笔记在哪」这一心智模型，而多客户端与既有脚本都依赖它，所以先只提示、由你决定要不要开。
- **落点有两级判据**（v2.4.1 起）：① **标题前缀命中既有目录** —— 标题第一个 `_` 之前那段若恰好是 vault 根下已有的一级目录（`故障/` `核验/` `经验/` …），就以它为准（`故障_XXX` → `故障/`）；② 否则退回**语义桶表**（11 个规范目录）。核心页（含 `_`／空格两种写法）一律留根、不参与归类。四档策略都受此影响（`warn` 只改**建议**、`auto` 才改**落点**）。
- **两条硬边界**：① 自动归类**只作用于新建** —— 更新绝不搬运，否则存量笔记会被搅动；② 落地目录**不写进 frontmatter 的 `title`** —— `title` 恒为裸叶子名。若把路径写进 `title`，一切「按标题比对」的判定（双链、更新、去重）都会失配，这是本项目踩过的真实坑。

> 存量笔记**一律不改名、不搬迁**。想收编旧目录可以手工搬，风险自担；服务端不会替你做迁移。

### 冲突 / 重复主动防护

`memory_write` 写入后会自动做一次**疑似重复/冲突检测**（基于字符 bigram 的 Jaccard 相似度，零依赖、对中文天然友好），命中时在返回值末尾追加提示：

- **标题相似度 ≥ 0.6** → 「疑似重复标题」
- **正文相似度 ≥ 0.5**（且标题不相似）→ 「疑似重复/冲突内容」

提示**不阻断写入** —— 是否合并、是否给旧条目标注 `supersedes`/`conflicts` 由写入方决定。检测异常时静默跳过，绝不影响正常写入。

### 长句检索兜底

`memory_smart_search` 一直用 bigram 分词，但 `memory_search` 是**纯子串匹配** —— 短关键词（「路由器」）没问题，长句（「怎么在路由器上跑容器」）作为整体子串必然零命中，「查不到」和「不存在」就被混为一谈。

现在 `memory_search` 在**精确路径零命中时**才降级到 bigram 兜底，命中门槛为「共享中文 bigram ≥ 2 且 query 覆盖率 ≥ 0.15」。两个好处：

- **常态零开销**：只在精确匹配颗粒无收时才做兜底扫描，日常查询不多花一毫秒
- **不硬凑**：弱相关条目达不到门槛就不进结果，避免「搜什么都有一堆似是而非的命中」

兜底结果会在首行显式标注「以下为 bigram 模糊匹配结果」，**不伪装成精确命中**。长句召回实测 0.00 → 1.00。

### 显式双链，关系可追溯

链接关系用 Obsidian 风格的 `[[Wiki Link]]` 在正文里显式标注，**正文双链是链接关系的唯一事实源**。

自动补链已停用（避免制造大量冗余链接），`memory_rebuild_links` 改为只读校验：扫描全库双链、报告死链与计数，不改任何文件。配套工具 `memory_graph`（出入链图谱）与 `memory_orphans`（孤立笔记检测）。

### 改名不失联（稳定标识 `permalink`）

双链、更新、去重都靠「标识」找条目。旧实现只有 `title` 和文件名 stem 两个标识 —— 一旦改名，**所有指向旧名的链接会静默变成死链**，而且没有任何报错。

v2.4.0 起每条记忆在**首次写入时冻结一个 `permalink`**（值为当时的文件名 stem），**此后永不覆盖**；查找链是 **`permalink` → `title` → 文件名 stem**。因此：

- 改名后，`[[旧名]]` 仍然指得到
- `memory_rebuild_links` 不再把指向旧标识的链接误报成死链
- 新版 `memory_schema_diff` 会告诉你全库有多少条目已回填 `permalink`（渐进回填，不强制迁移）

> 注意：**别名位不是标识位**。生成的索引里写的是 `[[stem|可读标题]]` —— `stem` 才是 Obsidian 的解析依据。`memory_orphans`（孤立笔记检测）也按同样口径解析反链，否则凡标题 ≠ 文件名的条目都会被误判成孤立。

### 一次调用拿到上下文

`memory_graph` 只列「出链 + 反链」，跨会话延续仍得把沿途正文一条条读出来 —— 实测要 3~5 次调用。`memory_context` 把这件事压成一次：以某条笔记为起点，沿双链向两个方向 BFS 到指定层数，按 `tier → 读取次数 → 更新时间` 挑出最该看的周边条目，连同各自正文与关系标注**拼成一段可直接喂给模型的 Markdown**。

两个容易踩的点已经在实现里兜住：输出是**整段字符预算**，在起点与周边之间分摊（起点至多占一半），否则一条长笔记会把周边全挤掉；自动生成的 `记忆索引.md` 被排除在周边之外 —— 它的正文就是全库标题清单，否则会冒充「头号邻居」。`timeframe` 支持自然语言（`last week` / `2 days ago` / `3d`），只筛周边、**起点始终保留**。

### 阈值可配置（`memory.toml`）

去重相似度阈值、正文截断预算、锁超时、热度升降档位等 **8 个运行时阈值**原先硬编码在代码里，改一个数就得动源码。v2.4.0 起可放到 vault 根的 `memory.toml`：

```toml
# ~/ai-memory/memory.toml —— 全部为可选，不建此文件则行为与旧版完全一致
body_truncate_chars = 8000    # memory_read / memory_context 的默认正文预算
lock_timeout        = 15      # 跨进程文件锁的陈旧回收秒数
dup_title_jaccard   = 0.6     # 疑似重复标题的相似度门槛
dup_body_jaccard    = 0.5     # 疑似重复正文的相似度门槛

[thresholds]                  # 小节写法亦可，与顶层同名键等价（小节优先）
heat_hot_min = 3.0            # 升为 hot 的热度下限
```

取值优先级：**环境变量 `AI_MEMORY_CFG_<KEY>` > `[thresholds]` 区 > 顶层同名键 > 内置默认**。改完约 **2 秒内生效**（TTL 热重载），不必重启服务。

不想手改文件时，阈值也可由 MCP 工具 **`memory_config`** 读改（Web 控制台的配置页走的就是它）：`memory_config(action="set", values={…})` 是**整状态写**（须提交全部 8 个键），写前备份、写前自解析、原子替换，只写这一个文件。

写错**不会静默退回默认值** —— 数组、内联表、多行字符串、裸词、缺值、重复键等不支持的写法一律报错并给出**行号与原文**；解析失败时 `memory_stats` 的「运行时阈值配置」小节会显示 `last_error`。理由很直接：静默取默认值会让「我明明改了却不生效」变成一场漫长的排查。

### 脚本化调用（可选 JSON 输出）

`memory_read` / `memory_list` / `memory_recent` / `memory_orphans` 支持 `output_format="json"`（默认 `"text"`），供脚本直接 `json.loads`：

```jsonc
// memory_read(title="部署方式定案", output_format="json")
{
  "found": true,
  "title": "部署方式定案",
  "path": "决策/部署方式定案.md",
  "meta": { "type": "decision", "confidence": "high", "verified": true },
  "body": "……",
  "truncated": false,
  "total_chars": 412
}
```

三点刻意的设计：

- **范围即契约**：报告型工具（`memory_stats` / `memory_doctor` / `memory_audit` / `memory_schema_diff` / `memory_context` / `memory_graph`）**不给** `output_format`。它们的产出是「分节标题 + 计数说明 + 提示语」的叙事，整体转 JSON 等于另立一套真相，两份结构迟早分叉。JSON 只覆盖「文本本身就是记录的渲染」的那几个工具 —— 它是文本的**同源投影**，不是第二份实现
- **非法取值报错**而非静默回落 `text`。脚本最怕「以为拿到 JSON、实际拿到一句中文」，那样 `json.loads` 会在很远的地方才炸；合法值则宽容大小写与首尾空白
- **`body` 不含中文截断说明**，是否被截断由 `truncated` / `total_chars` 表达。文本形态里的那句「已截断至前 N」**逐字节保留**（老用户不受影响），但脚本不该为了拿正文去正则剥中文

### 跨进程并发安全

多个 AI 同时写入时，基于 PID + 时间戳的**文件锁**保证互斥，15 秒超时并自动接管陈旧锁。锁在**线程内可重入**，因此 `memory_write → 自动归档 → memory_archive` 这条嵌套链路不会自死锁。

### 运行时可观测性

`memory_stats` / `memory_audit` 回答的是「库里有什么」，回答不了「服务跑得怎么样」。`metrics.py` 补齐后者：检索调用数与**零结果率**、命中条数、平均耗时，读写次数与未命中率，锁获取与等待时长、超时次数，索引重建次数 —— 全部并入两个工具输出末尾的「## 运行时指标」章节。

设计上刻意克制：纯标准库、全链路 `try/except`（观测绝不能成为主流程的新故障点）、**不加跨进程锁**（多进程 read-merge-write 同一 JSON 非原子，允许少量丢失 —— 指标看趋势，不需要精确到个位）。正常退出时自动落盘零头；被强杀则丢最后不足 50 次事件，这正是这层取舍的一部分。设 `AI_MEMORY_METRICS=0` 可完全关闭。

### 远程访问（可选 Streamable HTTP）

默认传输是 stdio（本机、每个客户端各起一个进程）。若要让**局域网内的多台机器 / 多个客户端共用同一个记忆库**，用可选入口 `serve_http.py` 把同一份服务暴露成 Streamable HTTP：

```bash
# 本机（仅回环可访问）
python serve_http.py                                    # → http://127.0.0.1:8700/mcp

# 局域网暴露：必须显式声明允许的 Host，否则拒绝启动
AI_MEMORY_HTTP_HOST=0.0.0.0 \
AI_MEMORY_HTTP_ALLOWED_HOSTS="192.168.1.10:8700,localhost:8700" \
python serve_http.py
```

客户端侧把原本的 `command` + `args` 换成该 URL 即可（这类客户端用 streamable HTTP 型 MCP 服务）。

**三条刻意的安全约束**（`transport_http.py`）：

1. **不开 SDK 层鉴权**。一旦开启，未认证请求会返回 `401` + `WWW-Authenticate: Bearer … resource_metadata=…`，而部分客户端会据此**强行走 OAuth 发现流程且不回落** —— 我们没有授权服务器，只会卡死。**鉴权请做在外层**（反向代理 / Tailscale / Cloudflare Access 等）。
2. **Host / Origin 校验绝不静默关闭**。监听非回环地址时必须显式给出 `AI_MEMORY_HTTP_ALLOWED_HOSTS`，否则**直接拒绝启动**；本模块也不提供关闭 DNS rebinding 防护的开关。理由是这类「监听地址一改、校验静默失效」的误配在网上极其常见。
3. **远程模式自动禁用热重启**。`memory_restart` 走 `os.execv` 原地换进程，会断开全部在途 HTTP 会话 —— 远程场景下重启交给进程管理器更合适（确有需要时用 `AI_MEMORY_RESTART=1` 强行恢复）。

> **主程序零改动**：HTTP 装配全部收在 `transport_http.py` / `serve_http.py` 两个文件里，`server.py` 一个字符都不动 —— `python server.py` 永远等于 stdio，两种入口互不影响，`tests/test_transport.py` 有专项断言钉住这一点。Web 控制台自 2026-10-16 起拆分为独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)，不在本仓的导入图内。

#### 装的时候就连启动器一起装好

只放两个 `.py` 是不完整的 —— 拿到手的人得自己找解释器、自己设 `AI_MEMORY_DIR`、自己想怎么起。安装器勾上「可选模块」时给的是**一套能直接跑的东西**：

| 生成物 | 平台 | 内容要点 |
|---|---|---|
| `start-http.cmd` | Windows | CRLF 行尾；写死安装时那个 `python.exe` 的**绝对路径**；`%*` 透传参数 |
| `start-http.sh` | Linux / macOS | LF 行尾；`exec python3 <正斜杠路径> "$@"`（不写死 Windows 解释器） |

两个脚本**按当前安装位置生成**，所以搬到别的机器请重装、不要手改路径。它们还会自己 `export AI_MEMORY_DIR`，双击即用，无需先开终端设变量。

**起不来时不会一闪而过**：双击 `.cmd` 时窗口会在进程退出的瞬间消失，所以非 0 退出会打印退出码、三条最可能的原因（缺 `mcp` 依赖 / 端口被占 / 监听非回环却没给白名单）以及可直接复制的补装命令，再 `pause` 住窗口。`.sh` 侧则在 `exec` 之前先探 `import mcp` —— 一旦 `exec` 就再无补救机会。`.cmd` 首行还带 `chcp 65001`：文件是 UTF-8 写的，中文 Windows 控制台默认 GBK，不切码页时 `echo` 出来的中文全是乱码。

```bat
:: 换个端口跑（脚本里留了 if not defined 的默认值，环境变量优先）
set AI_MEMORY_HTTP_PORT=8800 && start-http.cmd
```

可调的就三个环境变量，都已在脚本里给了默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_MEMORY_HTTP_HOST` | `127.0.0.1` | 改 `0.0.0.0` 才能局域网访问，**且必须同时配下面的白名单** |
| `AI_MEMORY_HTTP_PORT` | `8700` | 端口被占就换一个 |
| `AI_MEMORY_HTTP_ALLOWED_HOSTS` | （空） | 监听非回环地址时**必填**，否则拒绝启动 |

安装记录里会多一个 `http` 块（`host` / `port` / `path` / 实际生成的脚本名单），`ai_memory_setup.py check` 和安装完成的提示里都会把上面这段打印出来。

**幂等**：内容逐字节相同就不重写；被手改过的脚本**先备份到 `<安装目录>/.ai-memory-backup/<时间戳>/` 再覆盖**；`--dry-run` 一律不落盘。启动器不进 `files` 指纹（它们是生成物），所以完整性校验不会因为你改了端口就把安装判成「被篡改」。**未勾选该模块时，目录里上次留下的脚本不会被删，也不会被改** —— 安装器会明确提示这一点，而不是让人以为清干净了。

### 用容器跑（可选）

容器化定义已拆到独立仓 **[dpkg-s/ai-memory-docker](https://github.com/dpkg-s/ai-memory-docker)** —— 本仓不再自带 `Dockerfile` / `compose.yaml`。

划分理由：镜像里**只有运行时**（Python + `mcp` + 本仓 `.py`），记忆库一律从卷挂载，所以「换镜像 tag = 升级」；这套东西的变更节奏与本仓业务代码无关，混在一起会让每次业务提交都捎上一份容器配置的 diff。

```bash
git clone --recurse-submodules https://github.com/dpkg-s/ai-memory-docker.git
cd ai-memory-docker
docker compose up -d --build          # 端点 http://127.0.0.1:8700/mcp
```

那个仓以 submodule 引用本仓，依赖链是 `ai-memory-docker → ai-memory-template → ai-memory-web`。镜像的行为约定（`/vault` 与 `/state` 双卷、非 root uid 10001、端口只绑回环、Host 白名单）详见该仓 README。

容器内默认 `AI_MEMORY_DIR=/vault`、监听 `0.0.0.0:8700`。**注意容器必须监听 `0.0.0.0`**（否则宿主机连不上），这恰好触发上面第 2 条约束 ⇒ 镜像里预置了 `AI_MEMORY_HTTP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,ai-memory:*`。要从别的主机名/地址访问，**必须显式追加**（例：`192.168.1.20:8700`），否则请求会被 Host 校验拦下 —— 这是有意的摩擦。

容器以非 root（uid 10001）运行；健康检查只探监听端口（MCP 端点是 POST + 会话语义，GET 不返回 2xx，拿它当探针会把「健康」误判成「故障」）。

索引 db 与指标 json 落在 `AI_MEMORY_STATE_DIR`，镜像里预置为 `/state`（独立卷）。**别让它落回 `/app`** —— 那是镜像层，容器一重建就归零：索引本身可自动重建，但指标里的累积趋势（检索零结果率、锁等待、索引重建次数）补不回来。裸跑时不设这个变量，仍与 `server.py` 同目录，行为不变。

### Web 控制台（v3.1，可选）

想在浏览器里**看着**记忆库（而不是全靠 AI 转述），可以起一个可选的本地控制台。

> 🔴 **2026-10-16 起，Web 控制台已拆分为独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)**（前端 + 后端一体），在本仓作为 submodule 挂在 `web-console/`。

**想省事就直接让安装器装**：图形版第 ③ 步勾上，或命令行 `python ai_memory_setup.py install --with-web` —— 它会把整棵子树落到 `<程序目录>/web-console/` 并生成 `start-web.cmd` / `start-web.sh`，顺带把端口写进安装记录。下面是在源码目录里手工起服务的方式：

```bash
git clone --recursive git@github.com:dpkg-s/ai-memory-template.git
cd ai-memory-template/web-console

python serve_web.py                 # → http://127.0.0.1:8811/
python serve_web.py --with-mcp --core-dir <本仓目录>
                                    # 顺带把 MCP 端点也作为子进程拉起来（须显式给本仓目录）
python serve_web.py --check         # 只校验配置并打印摘要，不起服务（自检 / CI）

# 暴露到局域网：必须设 token，否则拒绝启动
AI_MEMORY_WEB_HOST=0.0.0.0 \
AI_MEMORY_WEB_TOKEN="$(openssl rand -hex 16)" \
python serve_web.py
```

控制台**不 import 本仓的任何模块**（2026-10-16 收敛）：阈值读写走 MCP 工具 `memory_config`（本仓 `memory_config.py` 是唯一实现），MCP 端点默认地址是它自己的字面常量。曾经那条「`core_path.py` 上溯定位核心目录 + 注入 `sys.path` 再 import `memory_config` / `transport_http`」的路径已整体拆除。⇒ 本仓与控制台的关系只剩**一个 submodule 引用**，运行期只剩**一条 MCP 连接**。唯一例外：`serve_web.py --with-mcp`（让控制台替你把 MCP 端点作为子进程拉起）需要知道本仓目录，此时须显式给 `--core-dir` 或 `AI_MEMORY_CORE_DIR`。

**首次打开会要凭证 —— 控制台不设默认口令。** 两种方式任选，也可以同时配：

```bash
# ① 访问令牌（最省事，适合脚本）：上面那种配法，登录框里粘 token
# ② 账号密码（推荐给人用）：先生成哈希，再把两行一起写进环境
python web-console/make_password.py
#     AI_MEMORY_WEB_USER=admin
#     AI_MEMORY_WEB_PASSWORD_HASH=scrypt:16384:8:1:<salt>:<hash>
```

两种都给时，登录框底部可以「改用访问令牌 / 改用账号密码」互相切换。**半配会被拒绝启动**（给了用户名却没给哈希），不会静默放行 —— 半配最容易让人误以为「门已经锁上了」。容器部署改完凭证必须**重建容器**：`docker restart` 不生效，`-e` 在创建那一刻就固化了。会话语义（默认 12 小时、HttpOnly Cookie、**改口令不会踢掉已登录会话**）详见控制台仓 README 的「鉴权与首次登录」一节。

四个页面：**概览**（条目数 / 热度分层 / 类型分布 / 最近更新 / 热门标签）、**调试台**（列出全部 26 个工具、按 JSON Schema 生成参数表单、直接调用并查看原始返回，破坏性工具要求二次确认）、**记忆库**（关键词搜索 + 浏览 + Markdown 渲染）、**配置**（MCP 端点 / 运行时阈值 / FNS 同步 / 关于）。支持深浅主题、三端适配（手机 / 平板 / 桌面）与前端动画。

**架构上只有一条要紧的事**：页面与 API **同源**，浏览器**不直连 MCP** —— 由后端用 SDK 客户端去连。依据是实测：MCP 端点的 `allowed_origins` 默认空 ⇒ 任何带 `Origin` 的请求一律 **403**；即便补上白名单，端点也**不返回 CORS 头**且 `OPTIONS` 返 **405**。后端连则不发送 `Origin` ⇒ **MCP 端零配置改动**。

> **仍然零改动**：控制台是**独立进程**，既不 import `server`，也不 import 本仓任何核心模块，对记忆库的一切读写都走 MCP 协议 —— 天然满足 v3 的两条判死项（不直连 vault、不进程内直调）。`python server.py` 依旧逐字节等于 stdio。该分层断言现由独立仓的 `tests/test_web.py` C 段钉住（`sys.modules` 里出现 `server` / `memory_config` / `transport_http` 任一即红）。

> 前端是 **Vue 3 + 本地 vendor**：**零 CDN、零构建、零 node**，随该仓一起分发。阈值写入是**整状态写**（须提交全部键，缺键即拒），由本仓的 `memory_config` 工具执行、**只写 vault 根的 `memory.toml`**，绝不触碰 `*.md`；写前自动备份为同目录 `memory.toml.bak.<时间戳>`（保留最近 10 份）。

### 改完即生效（原地热重启）

改了 `server.py` 或任一模块后，调用 `memory_restart` 即可让新代码生效，**不必重连客户端、不必重启应用**：进程映像被原地替换，stdin/stdout 保留，stdio 会话因此不断。

换进程前先在子进程里**预检** —— 新代码能否导入、会话恢复补丁能否装上，任一失败就放弃重启，绝不让起不来的代码顶掉正在服务的进程。重启后的新进程凭环境变量把客户端既有会话视为已初始化，否则客户端不重发 `initialize`，新进程会以「初始化未完成」拒掉一切请求。接力链上限 5 次，用于防重启风暴。

### 工程化保障

v2.0.0 起，仓库自带测试与持续集成：

- **1325 条断言的二十八层测试**：语义回归 29 + 全工具冒烟 41 + 记忆可信度 24 + 作用域与生命周期 70 + 跨进程并发 38 + 异常恢复 36 + 检索质量 12 + 索引链接可解析性 22 + 运行时可观测性 45 + 回收站闭环 34 + 冲突重复防护 15 + 热度自动升降级 29 + frontmatter 归一化 49 + 原地热重启 26 + 上下文组装 59 + 多级目录枚举 43 + 子目录写入 42 + 写入端归类策略 82 + 索引增量化 17 + permalink 稳定标识 25 + 结构对账 32 + 阈值配置 111 + 索引命名与迁移 49 + JSON 输出 76 + 模块分层守卫 43 + HTTP 传输层 43 + 管家自检 86 + 安装器自检 147
- **GitHub Actions CI**：Python 3.10 / 3.11 / 3.12 / 3.13 矩阵跑测试 + ruff lint
- **模块化结构**：无状态工具层拆为 `yaml_io.py` / `text_utils.py` / `locks.py` / `memory_index.py` / `memory_config.py`，可观测层拆为 `metrics.py`；`tests/test_import_layers.py` 用手写 AST 守卫钉住分层（叶子模块不得反向依赖 `server.py`、本地模块之间不得成环），**不引入任何打包元数据**

---
