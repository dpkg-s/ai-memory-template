# ai-memory-template

**让所有 AI 工具共用同一个长期记忆。**

基于 [MCP](https://modelcontextprotocol.io/)（Model Context Protocol）的跨工具共享记忆库：纯 Markdown 存储、零外部依赖、Obsidian 可视化。WorkBuddy / Codex CLI / Claude Desktop / Cursor 等任何支持 MCP 的客户端即插即用。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml/badge.svg)](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-3.0.0-blue)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.x-green)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-1092%20assertions-brightgreen)](#测试与-ci)

---

## 目录

- [为什么需要它](#为什么需要它)
- [特性](#特性)
- [快速开始](#快速开始)
- [实际使用效果](#实际使用效果)
- [25 个工具速查](#25-个工具速查)
- [自定义配置](#自定义配置)
- [用 Obsidian 可视化记忆网络](#用-obsidian-可视化记忆网络)
- [多设备同步与版本控制](#多设备同步与版本控制)
- [技术栈与架构](#技术栈与架构)
- [项目结构](#项目结构)
- [测试与 CI](#测试与-ci)
- [常见问题](#常见问题)

---

## 为什么需要它

你同时用多个 AI 工具吗？它们各有独立的上下文窗口，互相不知道对方做过什么。于是：

- 同一个偏好，你要对每个 AI 重复交代一遍
- A 工具刚修好的坑，B 工具下一轮又从零排查
- 对话一结束，上下文清空，项目决策随之丢失

**ai-memory-template** 给所有 AI 接上同一个记忆库。一个 AI 写进去的知识，其余 AI 全部可读。

| 场景 | 没有共享记忆 | 有共享记忆 |
|------|------------|-----------|
| 你告诉 AI「我用 VS Code、偏好 Python」 | 下次对话又问一遍 | AI 从记忆库读到，直接记住 |
| WorkBuddy 修了一个 bug | Codex 不知道，可能重复排查 | Codex 一查记忆库就明白 |
| 项目架构决策 | 上下文一清就丢 | 永久留在项目页，跨对话保持 |
| 多 AI 协作 | 各自为政，信息割裂 | 共享同一份知识库 |

---

## 特性

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

需要**远程 / 多机共用**同一个记忆库时，另有可选的 Streamable HTTP 入口（见 [远程访问（可选 Streamable HTTP）](#远程访问可选-streamable-http)）；想在浏览器里直接观察与调试记忆库，另有可选的 [Web 控制台（v3.1，可选）](#web-控制台v31可选)。两者都默认关闭，不影响上面任何一种用法。

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

### 用容器跑（可选）

不想在目标机器上准备 Python 与 `mcp` 依赖时，可以直接跑容器。仓库自带 `Dockerfile` 与 `compose.yaml`，镜像里**只有运行时**（Python + `mcp` + 本仓 `.py`），记忆库一律从卷/绑定挂载进来，所以升级只换镜像 tag、数据不动。

```bash
# 构建（或 docker load 一个别人给的镜像 tar）
docker build -t ai-memory:3.0.0 .

# 方式一：具名卷（数据在 Docker 管理的卷里）
docker run -d --name ai-memory -p 127.0.0.1:8700:8700 \
    -v ai-memory-vault:/vault ai-memory:3.0.0

# 方式二：直接挂已有的记忆库目录（注意容器会写索引与 .memory.lock）
#   把下面的路径换成你自己的绝对路径（Windows 形如 D:/memory/ai-memory）
docker run -d --name ai-memory -p 127.0.0.1:8700:8700 \
    -v "/path/to/your/ai-memory:/vault" ai-memory:3.0.0

# 或用 compose（在仓库根目录）
docker compose up -d --build
```

容器内默认 `AI_MEMORY_DIR=/vault`、监听 `0.0.0.0:8700`。**注意容器必须监听 `0.0.0.0`**（否则宿主机连不上），这恰好触发上面第 2 条约束 ⇒ 镜像里预置了 `AI_MEMORY_HTTP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,ai-memory:*`。要从别的主机名/地址访问，**必须显式追加**（例：`192.168.1.20:8700`），否则请求会被 Host 校验拦下 —— 这是有意的摩擦。

容器以非 root（uid 10001）运行；健康检查只探监听端口（MCP 端点是 POST + 会话语义，GET 不返回 2xx，拿它当探针会把「健康」误判成「故障」）。

### Web 控制台（v3.1，可选）

想在浏览器里**看着**记忆库（而不是全靠 AI 转述），可以起一个可选的本地控制台。

> 🔴 **2026-10-16 起，Web 控制台已拆分为独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)**（前端 + 后端一体），在本仓作为 submodule 挂在 `web-console/`。下面的命令都在该目录下执行：

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

四个页面：**概览**（条目数 / 热度分层 / 类型分布 / 最近更新 / 热门标签）、**调试台**（列出全部 25 个工具、按 JSON Schema 生成参数表单、直接调用并查看原始返回，破坏性工具要求二次确认）、**记忆库**（关键词搜索 + 浏览 + Markdown 渲染）、**配置**（MCP 端点 / 运行时阈值 / FNS 同步 / 关于）。支持深浅主题、三端适配（手机 / 平板 / 桌面）与前端动画。

**架构上只有一条要紧的事**：页面与 API **同源**，浏览器**不直连 MCP** —— 由后端用 SDK 客户端去连。依据是实测：MCP 端点的 `allowed_origins` 默认空 ⇒ 任何带 `Origin` 的请求一律 **403**；即便补上白名单，端点也**不返回 CORS 头**且 `OPTIONS` 返 **405**。后端连则不发送 `Origin` ⇒ **MCP 端零配置改动**。

> **仍然零改动**：控制台是**独立进程**，既不 import `server`，也不 import 本仓任何核心模块，对记忆库的一切读写都走 MCP 协议 —— 天然满足 v3 的两条判死项（不直连 vault、不进程内直调）。`python server.py` 依旧逐字节等于 stdio。该分层断言现由独立仓的 `tests/test_web.py` C 段钉住（`sys.modules` 里出现 `server` / `memory_config` / `transport_http` 任一即红）。

> 前端是 **Vue 3 + 本地 vendor**：**零 CDN、零构建、零 node**，随该仓一起分发。阈值写入是**整状态写**（须提交全部键，缺键即拒），由本仓的 `memory_config` 工具执行、**只写 vault 根的 `memory.toml`**，绝不触碰 `*.md`；写前自动备份为同目录 `memory.toml.bak.<时间戳>`（保留最近 10 份）。

### 改完即生效（原地热重启）

改了 `server.py` 或任一模块后，调用 `memory_restart` 即可让新代码生效，**不必重连客户端、不必重启应用**：进程映像被原地替换，stdin/stdout 保留，stdio 会话因此不断。

换进程前先在子进程里**预检** —— 新代码能否导入、会话恢复补丁能否装上，任一失败就放弃重启，绝不让起不来的代码顶掉正在服务的进程。重启后的新进程凭环境变量把客户端既有会话视为已初始化，否则客户端不重发 `initialize`，新进程会以「初始化未完成」拒掉一切请求。接力链上限 5 次，用于防重启风暴。

### 工程化保障

v2.0.0 起，仓库自带测试与持续集成：

- **1092 条断言的二十六层测试**：语义回归 29 + 全工具冒烟 41 + 记忆可信度 24 + 作用域与生命周期 70 + 跨进程并发 38 + 异常恢复 36 + 检索质量 12 + 索引链接可解析性 22 + 运行时可观测性 45 + 回收站闭环 34 + 冲突重复防护 15 + 热度自动升降级 29 + frontmatter 归一化 49 + 原地热重启 26 + 上下文组装 59 + 多级目录枚举 43 + 子目录写入 42 + 写入端归类策略 82 + 索引增量化 17 + permalink 稳定标识 25 + 结构对账 32 + 阈值配置 111 + 索引命名与迁移 49 + JSON 输出 76 + 模块分层守卫 43 + HTTP 传输层 43
- **GitHub Actions CI**：Python 3.10 / 3.11 / 3.12 / 3.13 矩阵跑测试 + ruff lint
- **模块化结构**：无状态工具层拆为 `yaml_io.py` / `text_utils.py` / `locks.py` / `memory_index.py` / `memory_config.py`，可观测层拆为 `metrics.py`；`tests/test_import_layers.py` 用手写 AST 守卫钉住分层（叶子模块不得反向依赖 `server.py`、本地模块之间不得成环），**不引入任何打包元数据**

---

## 快速开始

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

**校验完整性**：每次打 tag 发布时，Release 说明里会自动附上各文件的 SHA256（由 [`.github/workflows/release.yml`](.github/workflows/release.yml) 在发布前跑完全部测试后生成）。把下载到的文件哈希与之比对：

```bash
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

> 完整模板（含 `AI_MEMORY_DIR` 可选配置）见 [`setup/`](setup/) 目录。

### 验证是否正常

在 AI 对话里说「把今天的工作记到记忆库里」，观察 AI 是否调用了 `memory_write`。也可以直接让 AI 执行：

```
调用 memory_stats 看看记忆库状态
```

返回 tier 分布、访问排行、更新量等统计即表示服务正常。

---

## 实际使用效果

记忆库的价值在于**你不需要手动管理它**。典型的一天：

**上午 · 在 WorkBuddy 里**

> 主人：帮我修一下这个爬虫的超时问题，记到记忆库
>
> AI：已写入 `项目_爬虫超时修复`，标签 `[python, fix]`，关联到 `[[项目_数据采集]]`

**下午 · 换到 Codex CLI**

> 主人：爬虫那个超时问题之前怎么修的？
>
> AI：记忆库里有记录 —— `[[项目_爬虫超时修复]]`：根因是连接池未复用，改为 `requests.Session` 并加 `timeout=(3, 10)` 后恢复正常。

**关键点**：第二次查询发生在**另一个 AI 工具**里，且**完全不需要你复述任何背景**。

---

## 25 个工具速查

> 带 `output_format` 的四个只读工具（`memory_read` / `memory_list` / `memory_recent` / `memory_orphans`）可传 `output_format="json"` 拿到结构化输出，详见[脚本化调用](#脚本化调用可选-json-输出)。

### 读写

| 工具 | 签名 | 说明 |
|------|------|------|
| `memory_write` | `(title, content, tags?, source?, summary?, tier?, mem_type?, confidence?, verified?, scope?, project?, status?, supersedes?, conflicts?, source_context?, expected_version?)` | 写入或更新。自动标签 + 自动归档；同标题 upsert；支持乐观锁、可信度与作用域/生命周期标记 |
| `memory_read` | `(title, max_chars=None, output_format=None)` | 按标题精确读取（含文件名回退，含 `permalink` 与文件名 stem 回退，可命中 `.archive`）。`max_chars` 省略时取 `memory.toml` 的 `body_truncate_chars`（内置 8000），`0` = 取全文；`output_format="json"` 可拿结构化输出 |
| `memory_delete` | `(title, trash=True, purge=False, empty_trash=False)` | 删除。默认软删到 `.trash/` 可恢复，`purge=True` 永久删除，`empty_trash=True` 清空回收站 |
| `memory_update_metadata` | `(title, tier?, tags?, summary?, source?, mem_type?, confidence?, verified?, scope?, project?, status?, supersedes?, conflicts?, source_context?)` | 只改 frontmatter 元数据（含可信度、作用域、生命周期字段），不重写正文。传空串 / 空列表可清除 |
| `memory_recent` | `(days=7, limit=20, output_format=None)` | 列出近 N 天更新的记忆，最新优先 |

### 查询

| 工具 | 说明 |
|------|------|
| `memory_search(keyword, tag?, limit=20, mem_type?, scope?, project?, status?)` | 关键词搜索标题 / 标签 / 正文，命中处 `**` 高亮；可按标签 / 类型 / 作用域 / 项目过滤。**省略 `status` 时默认隐藏 `archived`**，隐藏条数会在末尾提示。精确零命中时自动降级为中文 bigram 模糊匹配（结果会显式标注） |
| `memory_smart_search(query, tag?, limit=10, mem_type?, scope?, project?, status?)` | 多字段加权搜索：标题 ×10、标签 ×4、摘要 ×3、正文 ×1，另加时效性加权。**零词法命中即非候选**，时效性不能单独决定入选 |
| `memory_list(tag?, limit=20, tier?, mem_type?, scope?, project?, status?, include_trash?, output_format?)` | 罗列记忆摘要，过滤语义同上；`include_trash=True` 改为列出回收站内容 |
| `memory_graph(title, limit=10, include_all=False)` | 显示指定笔记的出链与反向链接图谱，默认截断 10 条 |
| `memory_context(title, depth=2, max_related=10, timeframe?, max_chars?)` | **上下文组装**：沿 `[[wikilink]]` 出链与反链 BFS 到 `depth` 层（`0` = 只要起点），把沿途正文拼成一段可直接喂给模型的 Markdown，每块标注关系 / 深度 / tier / 读取次数 / 更新时间。周边按 `tier → access_count ↓ → updated ↓` 排序取前 `max_related` 条；`timeframe` 支持自然语言（`last week` / `2 days ago` / `3d` / `2026-09-01`）且只过滤周边；`max_chars` 为**整段预算**（在起点与周边间分摊，`0` = 不限） |
| `memory_orphans(output_format?)` | 查找没有任何笔记引用的孤立笔记。反链按**标识映射**解析（`title` / 文件名 stem / `permalink` 三种写法都认），生成页 `记忆索引` 不计为引用方 |
| `memory_stats()` | 健康度统计：各 tier 数量、记忆类型 / 可信度 / 作用域 / 生命周期 / 写入方上下文 / 结构版本分布、访问 TOP10、近 7/30/90 天更新量、热门标签 TOP10、孤立笔记数，以及**运行时指标**（检索零结果率 / 锁等待 / 索引重建） |

### 维护

| 工具 | 说明 |
|------|------|
| `memory_archive(title)` | 归档：正文移入 `.archive/`，原位留摘要 stub |
| `memory_archive_old(days=90)` | 批量归档 N 天未更新的条目，自动跳过核心页 |
| `memory_restore(title, source="archive")` | 恢复。默认从 `.archive/` 取回并把 tier 重置为 warm；`source="trash"` 从回收站取回（根目录已有同名条目则拒绝，不静默覆盖） |
| `memory_batch_tag(old_tag, new_tag)` | 全库标签重命名 |
| `memory_batch_tier(target_tier, min_score?, max_score?)` | 按热度分数批量调整 tier |
| `memory_config(action="get", values?, output_format?)` | **运行时阈值的 MCP 通道**（第 25 个工具，2026-10-16 起）。`get` 返回当前生效值 / 每键来源（`env`/`file`/`default`）/ 中文名与作用说明 / 类型 / 默认值 / 配置文件路径 / 解析错误；`set` 是**整状态写**（须提交全部 8 个键，缺键即拒），写前备份为同目录 `memory.toml.bak.<时间戳>`（保留最近 10 份）、写前自解析、原子替换。**只写 `memory.toml`，绝不触碰任何 `*.md`**。Web 控制台的阈值面板走的就是它 |
| `memory_heat_suggest(apply=False)` | 按访问频次与陈旧度给出 tier 升降建议。默认 `apply=False` **只预览不写盘**；`apply=True` 才真正批量落地（核心页豁免，预览与执行共用同一判据函数，不会漂移） |
| `memory_restart(delay?)` | **原地热重启**：改完 `server.py` 或任一模块后让新代码立刻生效，不必重连客户端、不必重启应用。`os.execv` 替换进程映像并保留 stdin/stdout，会话不断；重启前先在子进程**预检**新代码可导入、会话恢复补丁装得上，失败即放弃。⚠️ 换进程后 **pid 会变**；接力链上限 5 次防重启风暴 |

### 审计

| 工具 | 说明 |
|------|------|
| `memory_audit()` | 全库扫描：空壳检测 / 死链检测（跳过代码块）/ 命名漂移 / 缺 source·tags 汇总 |
| `memory_doctor(limit=20)` | **索引一致性自检（只读不修）**：报告索引与磁盘的三类偏差 —— 索引有行但文件已不在（stale rows）、磁盘有 `.md` 却无索引行（unindexed）、`title` 与 `(size, mtime)` 两侧不一致（field drift）。刻意**跳过惰性重建**，否则修完再报永远「一致」；扫描范围与索引加载器一致（库根 + `.archive`），归档副本不会被误报为 stale |
| `memory_schema_diff()` | **结构对账（只读）**：把「写入端承诺写出什么」与「磁盘上实际有什么」对照。契约来源就是代码（`yaml_io.build_frontmatter` 是唯一写出源），不另立 schema 文件。键分三层 —— core（`title`/`tags`/`created`/`updated`/`tier`/`access_count`，缺失即真实缺陷）/ v2（`type`/`confidence`/`verified`/`scope`/`status`/`schema_version`，v1 存量缺属预期）/ opt（按需写出）。输出六节：核心键缺失 / v2 缺键 / v1 迁移清单 / 未知键（附行号）/ 可选键使用率 / **无 frontmatter 文件**（普通 Markdown 不受结构契约约束，单列且不计入缺陷数） |
| `memory_index_draft()` | 自动生成记忆索引草稿（不回写，需人工确认） |
| `memory_rebuild_links()` | 只读校验：扫描正文双链，报告死链与计数，不修改文件 |

---

## 自定义配置

### 修改记忆库路径

默认目录是 `~/ai-memory`（安装脚本会自动创建）。用环境变量 `AI_MEMORY_DIR` 覆盖：

**macOS / Linux：**

```bash
export AI_MEMORY_DIR=/path/to/your/memory
python server.py
```

**Windows PowerShell：**

```powershell
$env:AI_MEMORY_DIR = "D:\my-memory"
python server.py
```

配置到 MCP 客户端里（以 Claude Desktop 为例）：

```json
{
  "mcpServers": {
    "ai-memory": {
      "command": "python",
      "args": ["/path/to/ai-memory/server.py"],
      "env": { "AI_MEMORY_DIR": "D:/my-memory" }
    }
  }
}
```

### 可选环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `AI_MEMORY_DIR` | 记忆库（vault）目录 | `~/ai-memory` |
| `AI_MEMORY_METRICS` | 运行时指标；设 `0` 完全关闭 | 开启 |
| `AI_MEMORY_SOURCE_CONTEXT` | 本条记忆的写入方上下文，写入 `source_context` 字段（如 `workbuddy` / `claude-desktop` / `codex`），便于日后分辨「谁写的」 | 不设置 |
| `AI_MEMORY_DIR_POLICY` | 写入端目录归类档位：`warn` / `auto` / `strict` / `off` | `warn` |
| `AI_MEMORY_CFG_<KEY>` | 覆盖任一 `memory.toml` 阈值（如 `AI_MEMORY_CFG_LOCK_TIMEOUT=30`），优先级最高 | 不设置 |
| `AI_MEMORY_FSYNC` | 写入后强制 `fsync`（掉电/强杀场景下的持久化保证；代价是写入变慢） | 关闭 |
| `AI_MEMORY_TRANSPORT` | 传输方式：`stdio`（默认）/ `http` / `streamable-http` | `stdio` |
| `AI_MEMORY_HTTP_HOST` | HTTP 监听地址（非回环须配 Host 白名单） | `127.0.0.1` |
| `AI_MEMORY_HTTP_PORT` | HTTP 监听端口 | `8700` |
| `AI_MEMORY_HTTP_PATH` | HTTP 端点路径 | `/mcp` |
| `AI_MEMORY_HTTP_ALLOWED_HOSTS` | Host 白名单（逗号分隔）；**非回环监听必填** | 不设置 |
| `AI_MEMORY_HTTP_ALLOWED_ORIGINS` | Origin 白名单（逗号分隔，浏览器场景） | 不设置 |
| `AI_MEMORY_HTTP_STATELESS` | 无状态模式（多副本 / 无会话保持） | `0` |
| `AI_MEMORY_HTTP_SESSION_IDLE_TIMEOUT` | HTTP 会话空闲超时（秒） | `1800` |
| `AI_MEMORY_HTTP_MAX_SESSIONS` | HTTP 并发会话上限 | `10000` |
| `AI_MEMORY_WEB_HOST` | 控制台监听地址（非回环须配 token） | `127.0.0.1` |
| `AI_MEMORY_WEB_PORT` | 控制台监听端口 | `8811` |
| `AI_MEMORY_WEB_MCP` | 上游 MCP 端点 URL | `http://127.0.0.1:8700/mcp` |
| `AI_MEMORY_WEB_TOKEN` | 访问令牌（`/api/*` 校验）；**非回环监听必填** | 不设置 |
| `AI_MEMORY_WEB_ALLOWED_HOSTS` | 控制台 Host 白名单（逗号分隔，启用 TrustedHost） | 不设置 |
| `AI_MEMORY_WEB_CONFIG` | 控制台配置面存储（JSON，**在 vault 之外**） | `~/.ai-memory-web/config.json` |
| `AI_MEMORY_WEB_CONFIG_WRITE` | 设 `0` 则阈值页转只读 | `1` |
| `AI_MEMORY_WEB_STATIC` | 前端静态资源目录 | `<仓库>/web` |

HTTP 相关变量的用途与安全约束见 [远程访问（可选 Streamable HTTP）](#远程访问可选-streamable-http)；控制台相关变量见 [Web 控制台（v3.1，可选）](#web-控制台v31可选)。

指标与索引一样，是**运行时生成的缓存文件**（`memory_metrics_<哈希>.json`，位于 `server.py` 同目录，不入库、可随时删除）。想从哪里看服务健康状况，直接调用 `memory_stats` 或 `memory_audit` 即可。

### 记忆库目录结构

```
~/ai-memory/
├── 用户画像.md · 近期工作动态.md · …     # 根条目（任意篇）
├── 记忆索引.md                           # 自动生成的 MOC 索引（勿手工维护）
├── 项目/                                 # 分类子目录，任意层级（≤ 3 级）
│   ├── 记忆库/
│   └── 训练模块B/
├── memory.toml                           # 可选：阈值覆盖（热重载）
├── .archive/                             # 归档正文（cold 条目），不参与日常检索
└── .trash/                               # 软删除，可列出 / 可恢复 / 可清空
```

- **保留目录**（8 个）不可作为写入落点，也不参与检索：`.archive` `.trash` `.git` `.obsidian` `ai-memory` `__pycache__` `.ruff_cache` `.pytest_cache`
- 子目录里的条目**参与全部检索与统计**（枚举是递归的，且带保留目录剪枝）
- 生成物 `记忆索引.md` 会被排除出「周边组装」等遍历，避免它的全库标题清单冒充强相关

### 自定义自动标签规则

编辑 `server.py` 中的 `_TAG_AUTO_MAP` 字典（目前 68 条「正文关键词 → 标签」映射），按需增删即可。

### tier 体系与归档策略

每个条目有 `hot` / `warm` / `cold` 三档：

| tier | 含义 | 行为 |
|------|------|------|
| `hot` | 高频访问 | 常驻根目录 |
| `warm` | 一般 | 常驻根目录（默认） |
| `cold` | 冷数据 | **写入时自动归档**到 `.archive/`，原位留摘要 stub |

`memory_archive_old(days=90)` 可按时间批量归档，自动跳过 `CORE_PAGES` 保护名单（`记忆索引`、`近期工作动态`、`用户画像` 等 13 个核心页）。`memory_heat_suggest()` 会依据实际访问频次与陈旧度给出升降建议；加 `apply=True` 即可让它**直接落地** —— 先跑一次预览确认名单，再带 `apply=True` 执行，两个模式共用同一判据函数，不会出现「预览说升、执行说降」。

---

## 用 Obsidian 可视化记忆网络

因为记忆是 **Markdown + 双链**，直接用 Obsidian 打开记忆库目录，就能得到完整的知识图谱：

- **图谱视图（Graph View）** —— 所有笔记的链接网络一目了然，哪些项目关联最紧密、哪些笔记是枢纽节点，一眼可见
- **局部图谱（Local Graph）** —— 选中一条笔记，只看它的出链与反向链接
- **孤岛高亮** —— Obsidian 自动标记没有链接的笔记，与 `memory_orphans` 工具互补

```
记忆网络示意：

              运维实录
                 ↑
    ┌──────── 项目_A ────────┐
    │            ↑           │
    │      用户画像 ←── 记忆索引
    │            ↓           │
    └──────── 项目_B ────────┘
                 ↓
           近期工作动态
```

不装 Obsidian 也没关系 —— `memory_graph` 与 `memory_orphans` 在 AI 对话里就能直接查询链接关系。

---

## 多设备同步与版本控制

记忆库就是一堆 Markdown 文件，天然适合 git。给它接上远程仓库，即可实现**多设备无缝同步 + 完整历史记录**。

安装脚本会把 `rules/Git版本控制规范.md` 复制进记忆库目录。**AI 打开记忆库后搜索「git」就能发现并自动遵守这套规范**，无需人工提醒。

### 给 AI 的规范

**每次 `memory_write` 完成后自动执行：**

```bash
cd ~/ai-memory
git add -A
git commit -m "{source}: {动作} - {简要说明}"
git push
```

| 约定 | 说明 |
|------|------|
| commit 格式 | `{source}: {动作} - {简要说明}` |
| 示例 | `codex: 更新项目状态 - 完成数据采集工具 v4` |
| source 标识 | 每个 AI 用自己的标识：`workbuddy`、`codex`、`claude` |
| push | commit 后立即 push，保证远程最新 |

**每次对话开始时先拉取：**

```bash
cd ~/ai-memory && git pull
```

### 首次设置

```bash
# 1. 初始化本地 git
cd ~/ai-memory
git init
git add -A
git commit -m "chore: 初始化记忆库"

# 2. 在 GitHub 创建私有仓库（不要勾选 README）

# 3. 关联远程并推送
git remote add origin https://github.com/你的用户名/ai-memory.git
git branch -M master
git push -u origin master
```

> 记忆库常含个人工作记录，**建议使用私有仓库**。

### 第二台电脑

```bash
git clone https://github.com/你的用户名/ai-memory.git ~/ai-memory
```

clone 完即可使用，所有 AI 的记忆自动同步。

---

## 技术栈与架构

| 层级 | 技术 | 说明 |
|------|------|------|
| 协议 | **MCP (Model Context Protocol)** | 开放标准，stdio 传输，JSON-RPC 2.0 |
| 框架 | **FastMCP (Python SDK)** | MCP 服务端框架，自动处理协议层 |
| 存储 | **Markdown + YAML Frontmatter** | 一条记忆一个 `.md`，元数据存 frontmatter（Obsidian 原生识别，兼容旧 JSON） |
| 索引 | **SQLite（内置 sqlite3，运行时缓存）** | 镜像 frontmatter 用于标题 O(1) 定位；可随时删除重建，故障自动回退全库扫描 |
| 缓存 | **Python dict（内存）** | 条目缓存 + 访问计数缓存，写入时失效 |
| 可观测性 | **JSON 快照（内置 json，运行时缓存）** | 检索/读写/锁/索引四类指标的进程内累积 + 定期合并落盘；不加跨进程锁，允许少量丢失 |
| 锁 | **文件锁（PID + 时间戳）** | 跨进程互斥，线程内可重入，15 秒超时接管 |
| 搜索 | **关键词匹配 + 多字段加权** | 标题 / 标签 / 摘要 / 正文分级打分 |
| 图谱 | **正则解析 `[[Wiki Link]]`** | Obsidian 兼容双链语法 |
| 审计 | **全库扫描 + 热度分析** | 健康度统计与冷热数据识别 |

### 架构

```
  AI 客户端层
  ├─ WorkBuddy
  ├─ Codex CLI
  ├─ Claude Desktop
  └─ Cursor / 任何支持 MCP stdio 的客户端
        │
        ▼  MCP 协议 · stdio（默认）· JSON-RPC 2.0
  server.py —— 24 个 MCP 工具
  ↑ 可选远程入口：serve_http.py → transport_http.py（同一份 server，不改主程序）
  ├─ 读写：write / read / delete / update_metadata / recent
  ├─ 查询：search / smart_search / list / graph / context / orphans / stats
  ├─ 维护：archive / archive_old / restore / batch_tag / batch_tier / heat_suggest / restart
  ├─ 审计：audit / doctor / schema_diff / index_draft / rebuild_links
  ├─ 无状态工具层（可独立测试，分层由 tests/test_import_layers.py 守卫）
  │    yaml_io.py · text_utils.py · locks.py · memory_index.py · memory_config.py
  └─ 基础设施：条目缓存 · 访问计数 · SQLite 索引 · 运行时指标 · 自动标签 · 自动归档 · 阈值热重载
        │
        ▼
  存储层  ~/ai-memory/（纯 Markdown，也可直接用 Obsidian 打开）
  ├─ 用户画像.md · 近期工作动态.md · 项目_xxx.md · 记忆索引.md（生成物）
  ├─ <分类子目录>/（任意层级，上限 3 级）
  ├─ memory.toml（可选：阈值覆盖，热重载）
  └─ .archive/（归档正文）· .trash/（软删除）
```

---

## 项目结构

```
ai-memory-template/
├── README.md                # 本文档
├── CHANGELOG.md             # 版本变更记录
├── LICENSE                  # MIT
│
├── server.py                # MCP 服务端：25 个工具注册 + 存储层 + 业务逻辑（stdio 入口）
├── serve_http.py            # 可选：Streamable HTTP 入口（主程序零改动的 HTTP 装配）
├── transport_http.py        # HTTP 传输层：环境变量解析 + Host 校验 fail-fast（只依赖 mcp）
├── web-console/             # 🔗 submodule → ai-memory-web：Web 控制台（前端 + 后端一体）
├── memory_index.py          # SQLite 元数据索引（标题 O(1) 定位，增量重解析）
├── memory_config.py         # 运行时阈值（vault 根 memory.toml + 环境变量 + 热重载）
├── metrics.py               # 运行时指标（检索/读写/锁/索引；仅标准库）
├── yaml_io.py               # YAML frontmatter 解析 / 序列化（无状态）
├── text_utils.py            # 文件名 / 链接 / 时间 / 热度工具（无状态）
├── locks.py                 # 跨进程文件锁（可重入 + 抗陈旧）
│
├── install.sh               # macOS / Linux 一键安装
├── install.ps1              # Windows 一键安装
├── requirements.txt         # 运行时依赖（mcp）
├── requirements-dev.txt     # 开发依赖（pytest + ruff）
├── ruff.toml                # lint 配置
├── .gitattributes           # 强制 LF 换行，避免 CRLF 污染
├── Dockerfile               # 容器镜像（只装运行时；记忆库从卷挂载）
├── .dockerignore            # 构建上下文白名单（*.py + requirements.txt）
├── compose.yaml             # 本机 compose（只绑回环，具名卷存数据）
│
├── tests/
│   ├── test_roundtrip.py         # 29 断言：语义回归
│   ├── test_tools_smoke.py       # 32 断言：全工具冒烟 + 注册完整性
│   ├── test_p0_credentials.py    # 24 断言：记忆可信度字段
│   ├── test_scope_lifecycle.py   # 70 断言：作用域 / 生命周期 / 冲突字段
│   ├── test_concurrency.py       # 38 断言：跨进程并发（乐观锁 / 混合操作 / 读写并发）
│   ├── concurrency_worker.py     # 并发测试的子进程工人（非测试脚本，不被 CI 执行）
│   ├── test_recovery.py          # 36 断言：索引损坏 / 锁残留 / 进程强杀 / 畸形文件
│   ├── test_search_quality.py    # 12 断言：检索质量护栏（recall@10 / MRR / 零结果率 / 长句兜底）
│   ├── test_index_links.py       # 22 断言：索引链接可解析性（防自动生成死链）
│   ├── test_trash.py             # 34 断言：回收站闭环（列出 / 取回 / 清空 / 同名拒绝）
│   ├── test_dup_guard.py         # 15 断言：冲突重复防护（标题 / 正文 / 核心页 / 容错）
│   ├── test_heat_apply.py        # 29 断言：热度自动升降级（判据 / 预览 / 执行 / 字段不丢）
│   ├── test_metrics.py           # 45 断言：运行时可观测性
│   ├── test_frontmatter_norm.py  # 49 断言：frontmatter 归一化（双 frontmatter / 摘要污染 / 自愈 / 多行标量转义）
│   ├── test_restart.py           # 26 断言：原地热重启（预检失败即放弃 / 接力标记 / 上限 5 次 / pid 变化）
│   ├── test_context.py           # 59 断言：上下文组装（深度边界 / 出链反链 / 预算分摊 / timeframe / 生成页排除）
│   ├── test_folders.py           # 43 断言：多级目录枚举（递归 / 剪枝 / 排序 / 指纹键）
│   ├── test_title_path.py        # 42 断言：子目录写入（逐段消毒 / 政策拒绝 / 旧口径兜底 / 叶子名）
│   ├── test_dir_policy.py        # 82 断言：写入端归类策略（四档 / 只作用于新建 / title 不夹带目录）
│   ├── test_index_cache.py       # 17 断言：索引增量化（改 1 个文件只新增 1 次解析）
│   ├── test_permalink.py         # 25 断言：稳定标识 permalink（冻结 / 查找链 / 改名不失联）
│   ├── test_schema_diff.py       # 32 断言：结构对账（三层键 / 无 frontmatter 单列 / 未知键行号）
│   ├── test_memory_config.py     # 79 断言：阈值配置（默认值一致 / 子集拒绝 / 优先级 / 热重载 / 坏配置）
│   ├── test_index_name.py        # 47 断言：索引文件名与迁移（可见名 / 反向迁移 / 人侧可点击 / 摘要行）
│   ├── test_output_json.py       # 76 断言：JSON 输出（范围即契约 / text 逐字节不变 / 非法值报错）
│   ├── test_import_layers.py     # 47 断言：模块分层守卫（叶子不反向依赖 / 无环 / 白名单不腐烂 / 装配层依赖方向）
│   └── test_transport.py         # 43 断言：HTTP 传输层（模式归一 / 安全规则 / 装配 / 端到端 / 零改动保证）
├── scripts/
│   └── make_checksums.py    # 生成 SHA256SUMS.txt（打 tag 发布时由 release.yml 调用）
├── .github/workflows/
│   ├── ci.yml               # CI：Python 矩阵测试 + ruff lint
│   └── release.yml          # 打 tag 时先跑全量测试，再发布 Release 并附 SHA256
│
├── setup/                   # 各客户端 MCP 配置模板
│   ├── workbuddy.json
│   ├── claude.json
│   └── codex.toml
├── template/                # 初始记忆文件（开箱即用）
│   ├── 用户画像.md
│   ├── 近期工作动态.md
│   ├── 记忆索引.md           # 开箱索引占位（生成物；首次写入后自动重建）
│   └── 记忆写入格式规范.md
└── rules/                   # AI 写入时遵循的规范
    ├── 记忆写入格式规范.md
    ├── 记忆库结构规范.md
    └── Git版本控制规范.md
```

---

## 测试与 CI

测试分四类：**正确性**（写读是否无损）、**覆盖面**（工具是否都能用）、**语义与质量**（字段标记是否可靠、检索是否退化）、**韧性**（并发、崩溃、损坏文件下是否还站得住）。共 **26 套 / 1092 条断言**（另有 Web 控制台一套已随控制台迁至独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)）。

**每条新判据都要先证明它「会红」**：新增断言时会把产品改回旧行为跑一遍，若断言不变红就说明它证明不了任何东西。本批的证伪结果：阈值配置 8/8、索引命名 **23/47**、多行标量转义 **6/8**、JSON 输出 7/7、模块分层 8/8 根针全部生效。

### 语义回归 —— `tests/test_roundtrip.py`（28 断言）

- frontmatter 写 → 读无损 round-trip（中文标题 / 多行正文 / tags 特殊字符 / 类型保持）
- Windows 路径反斜杠不雪崩（连续 5 轮 update 回归）
- `memory_read` 不污染 `updated`（P0-1 修复的回归保护）
- `max_chars` 截断语义
- 文件名非法字符安全处理
- SQLite 索引层（写入命中 / read 走索引 / miss 回退 / 外部改动自愈 / delete 清理）

### 全工具冒烟 —— `tests/test_tools_smoke.py`（32 断言）

- 24 个 MCP 工具逐一调用，验证不抛异常且返回正常
- 工具注册完整性：数量为 20 且集合与预期完全一致（防重构时漏注册或误覆盖）

### 记忆可信度 —— `tests/test_p0_credentials.py`（24 断言）

- 写入落盘：显式取值正确写入，缺省时回落 `fact` / `medium` / `false`
- 更新语义：未传字段继承原值（不被重置），显式传入时覆盖生效
- 非法取值被拒绝且不落盘
- 向后兼容：无字段的历史笔记读取时注入默认值、被写入时渐进补齐
- `verified_at` 生命周期：置 `true` 写入日期，置 `false` 清除日期
- 检索过滤（`mem_type`）与统计分布输出
- 序列化层边界：非法值防御性兜底、`false` 不被序列化为 `null`

### 作用域与生命周期 —— `tests/test_scope_lifecycle.py`（70 断言）

- 字段默认值与写入落盘、`scope` / `status` 非法取值拒绝
- 更新时继承磁盘原值（只改正文不丢语义字段），`memory_update_metadata` 可修正与清空
- `AI_MEMORY_SOURCE_CONTEXT` 环境变量回落
- 检索默认排除 `archived`（`stale` / `deprecated` 保留）、`scope` / `project` 过滤
- 归档 / 恢复与 `status` 联动；`memory_stats` / `memory_audit` 新章节
- 旧笔记（无新字段）向后兼容

### 跨进程并发 —— `tests/test_concurrency.py`（38 断言）

用**真实子进程**（`tests/concurrency_worker.py`）而非线程，才能复现多个 AI 客户端同写一个库时的进程级锁与缓存隔离语义：

- 乐观锁竞争：10 进程同 `expected_version` 写同标题 → 恰好 1 成功 9 冲突
- 无冲突并发：10 进程写 10 个标题互不干扰
- 混合操作：写入 / 归档 / 删除交错
- 读写并发：300 次读循环 vs 3 个写进程
- 收尾对账：无锁文件残留、索引与磁盘逐行核对无差异、无 `.tmp` 残留

### 异常恢复 —— `tests/test_recovery.py`（35 断言）

- 索引被删除 → 自动重建；索引被写入垃圾字节 → 自愈
- 锁文件残留 / 内容损坏 → 陈旧检测回收
- 写入窗口内强杀进程 → 库无损坏
- 外部直接改写正文 → 以磁盘为准；外部增删文件（如 `git` 切版本）→ 以磁盘为准
- 七类畸形 Markdown（0 字节 / 无 frontmatter / 半截 `---` / 非法 YAML 等）→ 10 个工具全部降级不崩
- 重复重建幂等

### 检索质量 —— `tests/test_search_quality.py`（12 断言）

15 篇 fixture（中文 / 英文 / 代码标识符 / 专有名词）× 20 条查询 + 3 条负样本，以 **recall@10 ≥ 0.95、MRR ≥ 0.80、零结果率为 0、负样本必须全空** 作为护栏。检索相关改动必须过这道闸。

后半段（H 组 5 断言）专测 `memory_search` 的**长句兜底**：长句召回 ≥ 0.80（改造前恒为 0.00）、短关键词零回归、精确命中不得被误标为模糊、负样本不得被兜底误召回。

### 索引链接可解析性 —— `tests/test_index_links.py`（22 断言）

`记忆索引.md` 由 `_refresh_index()` 自动重建。Obsidian 解析双链以**文件名 stem** 为准（`permalink` 是服务端侧的稳定标识，不在渲染口径里），而 `safe_filename()` 会把标题里的空格等字符归一化成下划线 —— 若生成器直接输出 `[[title]]`，凡标题含空格的笔记在索引里全是渲染死链，且每次写入都会把人工修复覆盖回去。

判定口径是一句话：**索引与索引草稿里的每一条链接，其 target 都必须能解析到库中真实存在的文件**。覆盖：

- 标题==stem 时输出裸链接、标题!=stem 时 target 用 stem 且标题进别名位
- 标题含空格（原缺陷场景）与含 `/ : * ?` 等归一化字符的标题均须可解析
- 连续三次 `_refresh_index()` 后链接集合不变（幂等，防覆盖）
- `memory_index_draft` 走同一口径；索引条目数不缺不漏

### 回收站闭环 —— `tests/test_trash.py`（34 断言）

`memory_delete` 默认软删到 `.trash/`，但「可恢复」要真的能恢复才算数。覆盖：

- 软删除后条目立即退出检索与列表，且**仍可被 `include_trash=True` 列出**
- 从回收站取回后：文件回到根目录、字段（tier / status / schema_version）正确、索引重新可见
- **根目录已有同名条目时拒绝覆盖**（回收站里那份可能是被新版取代的旧内容，静默覆盖比不恢复更危险）
- 回收站内同名两条并存时不误取
- `purge=True` 永久删除、`empty_trash=True` 清空回收站
- 既有 `.archive/` 恢复路径零回归

### 冲突 / 重复防护 —— `tests/test_dup_guard.py`（15 断言）

- 标题相近（≥ 0.6）判「疑似重复标题」；标题不相似但正文相近（≥ 0.5）判「疑似重复/冲突内容」
- **无关内容不误报**（假阳性会迅速让人无视提示）
- `upsert` 同一标题不把自己当成重复项
- **核心页变体同样触发**（「新建『近期工作动态记录』而不是更新原页」正是最高发的重复写入）
- 检测内部异常时静默跳过，写入本身照常成功

### 热度自动升降级 —— `tests/test_heat_apply.py`（29 断言）

`memory_heat_suggest(apply=True)` 会把预览里的建议**真正写盘**。整组测试围绕「预览与执行绝不能漂移」展开：

- 判据纯函数 `_heat_tier_decision` 逐档取值：冷 → `cold`、hot 且未达阈值 → `warm`、过热 → `hot`、其余维持现状、**核心页一律豁免**
- **预览不改盘**：`apply=False` 前后全库文件的哈希与 mtime 不变
- **执行写盘且所见即所改**：预览列出的每一条，执行后 tier 与之一致
- **幂等**：连续两次 `apply=True`，第二次无任何改动
- **v2 字段不丢**：批量改 tier 不会清掉 `scope` / `status` / `confidence` / `supersedes` 等字段
- 空库边界不崩

### frontmatter 归一化 —— `tests/test_frontmatter_norm.py`（41 断言）

写入端会自行生成权威 frontmatter，而调用方（其他 AI 客户端、手工整篇粘贴、跨工具导入）**常把「带 frontmatter 的完整笔记」直接当作 `content` 传入**。两者直接拼接会产出「双 frontmatter」：Obsidian 只认第一块，第二块退化为正文里的深层垃圾；更隐蔽的是**自动摘要取自未剥离的原始 `content`**，会把那段 frontmatter 文本固化进 `summary` 字段，持续污染检索结果与索引快照。

修复分两道纵深：`yaml_io.strip_leading_frontmatter()` 纯函数负责剥离，`memory_write()` 入口调用它（**早于空壳校验与摘要/标签派生**），`_write_memory()` 收口再剥一次（覆盖其余写盘路径）：

- 传入单块 / 连续多块 frontmatter → 落盘仍只有 1 块，正文干净
- **摘要污染回归**：`summary` 不得含 `---` / `title:` 等传入块文本
- **权威性**：服务器生成的 `title` / `tags` 为准，传入块不夺权
- **更新路径**（历史重灾区）同样只留 1 块，版本号正常自增
- **自愈**：磁盘上已被写坏的条目，再次写入即恢复
- **误伤防护**：正文开头恰为 `---` 水平线 + 非 `key:` 文本时**不剥**
- 幂等（连写两次不堆叠）、纯函数边界（空串 / 无 frontmatter / 空块 / BOM）

### 运行时可观测性 —— `tests/test_metrics.py`（45 断言）

- 计数 / 耗时累积、跨实例 read-merge-write 合并、达到阈值自动落盘
- 落盘失败静默且增量不丢；`path=None` 时全操作安全
- 各工具确实打点（写入新建 vs 覆盖、读命中 vs 未命中、检索零结果、锁获取）
- 指标摘要确实出现在 `memory_stats` / `memory_audit`
- `AI_MEMORY_METRICS=0` 可关闭（子进程验证）
- **容错**：指标设施自身崩塌时，检索与统计仍然正常返回

### 原地热重启 —— `tests/test_restart.py`（26 断言）

- **预检失败即放弃**：新代码导入不通过、或会话恢复补丁装不上时**不换进程**，原进程继续服务
- **接力标记**：新进程能识别自己由重启而来，不因客户端未重发 `initialize` 而拒掉请求
- 接力链上限 5 次防重启风暴，到顶后给出可操作的复位指引
- 换进程后 **pid 变化**（Windows 上 `execv` 内部即 `CreateProcess`），旧 pid 不再响应
- 响应先经管道发出、`execv` 在延迟线程执行 ⇒ 调用方拿到返回值，而非连接被掐断
- 环境变量传递走 `os.environ` 赋值而非 `os.execve`（本机实测 1/3 概率崩溃的回归护栏）

### 上下文组装 —— `tests/test_context.py`（59 断言）

- 深度边界：`depth=0` 只要起点 / `1` 只取直接邻居 / `2` 展开两层 / 超过图深度不报错
- **出链与反链都纳入**（只取出链会漏掉「谁引用了我」）
- 排序权重：`tier(hot > warm > cold)` 优先，其次 `access_count`，最后按 `updated`
- `max_related` 截断，且**起点不占用周边配额**
- `timeframe` 只过滤周边、起点恒在；无法识别的表达式按不限处理并在输出里提示；`_parse_timeframe` 有单元级覆盖
- **预算分摊回归**：起点很长时周边仍须分到配额（缺陷形态是长起点独吞整段预算，周边一篇都展不开）
- **生成页排除**：`记忆索引.md` 正文即全库标题列表，必须排除出周边，否则会冒充「头号邻居」
- 截断回退到行边界，不把 `_关系 · 深度 · …_` 标注行切成半截
- 标题解析：不存在时报错清晰、按文件名 stem 命中、反斜杠路径归一化

### 多级目录枚举 —— `tests/test_folders.py`（43 断言）

- 递归枚举与剪枝：`RESERVED_DIRS` 内一律不计，深度与排序确定（同一库两次枚举同序）
- 子目录条目的可见性：`memory_list` / `memory_search` / `memory_stats` 都能看到它们
- **指纹键必须是 relpath 而非文件名**（跨目录同名会撞键 ⇒ 变更不触发缓存失效、静默返回陈旧数据）
- 历史扁平库与目录化库的枚举结果对账

### 子目录写入 —— `tests/test_title_path.py`（42 断言）

- 逐段消毒：非法字符 / 控制字符 / 空段 / 首尾 `._` / 段长上限
- 政策层拒绝：保留目录、`.` / `..`、盘符、绝对路径、层级 > 3（**拒绝而不是消毒后放行**）
- 旧口径兜底：`legacy_flat_filename()` 对历史标题的幂等匹配（含 `a/ b` → `a_b` 的字符类折叠语义）
- **叶子名口径**：frontmatter 的 `title` 恒为裸叶子名，绝不夹带目录前缀

### 写入端归类策略 —— `tests/test_dir_policy.py`（71 断言）

- 四档 `warn` / `auto` / `strict` / `off` 的行为边界
- **只作用于新建**：更新绝不搬运（存量笔记不被搅动）
- **title 不夹带目录**：`auto` 归类后 frontmatter 里仍只有叶子名（防「按 title 比对」全线失配）
- 标题已含 `/` 时完全尊重写入方
- 语义桶映射与「无规范目录可归」时的四档差异

### 索引增量化 —— `tests/test_index_cache.py`（17 断言）

- **核心判据：改 1 个文件只新增 1 次解析**（旧实现是 +全库）。验证手法是把增量分支临时改成假，断言必须变红
- 整库指纹未变时连 `os.walk`/`stat` 都省（回放快照）
- `_refresh_index` 的内容签名短路：正文未变则跳过渲染与写盘，但**文件不存在时必须重建**
- 缓存键为 relpath（同名不同目录不互相覆盖），返回的 meta 是浅拷贝

### permalink 稳定标识 —— `tests/test_permalink.py`（25 断言）

- 首次写入冻结、后续写入**永不覆盖**（`is None` 判据，写成 `or path.stem` 会在改名时悄悄改掉）
- 查找链 `permalink → title → stem`，且索引快路径与全库兜底**两条实现同口径**
- 改名后 `[[旧名]]` 仍能解析；`memory_rebuild_links` 不误报死链
- 索引加列只经 `_migrate()` 的 `ALTER TABLE`（`CREATE INDEX` 放进 SCHEMA 会打断 `executescript`）

### 结构对账 —— `tests/test_schema_diff.py`（32 断言）

- core / v2 / opt 三层键的分类与计数
- **「无 frontmatter」与「核心键缺失」必须分开**：普通 Markdown 不受结构契约约束，单列且不计入缺陷数
- `archived_to` 属已知键（漏掉会把归档 stub 全报成未知键）
- 未知键附行号；可选键使用率统计

### 阈值配置 —— `tests/test_memory_config.py`（79 断言）

- **默认值与改动前的硬编码值逐项相同**（含用 `inspect.signature` 钉住两个默认参数必须是 `None`，防「钉死在 def 期」的退化）
- 解析子集：能接受的写法与**必须报错**的写法（数组 / 内联表 / 多行字符串 / 裸词 / 缺值 / 重复键…），错误须带行号
- 优先级链：环境变量 > `[thresholds]` 区 > 顶层键 > 默认
- 热重载：改文件后无需重启即生效；T6 用「坏配置必须退回默认值而不是沿用旧值」证明失败处理真的走了默认分支
- 四个读取点确实被接上（改配置能改变实际行为）

### 索引文件名与迁移 —— `tests/test_index_name.py`（47 断言）

- 生成物为**可见名** `记忆索引.md`；仍能被枚举 / 检索（可见 ≠ 更好维护，故仍走生成器）
- 一次性迁移**反向**：B6 期间的隐藏名 `.记忆索引.md` 残留被清理；**仅当 `title == 记忆索引` 且 tags 命中 `moc`/`index`/`navigation`** 才删，疑似人工文件跳过并告警
- 迁移同时清掉索引行（否则留死索引）；迁移**先于枚举**执行，避免把即将被删的文件也渲染成一行
- **人侧可点击**：判据落在**文件名 stem** 上（Obsidian 的解析口径），而不是 `memory_rebuild_links` —— 后者认 `title`，在旧版上恒绿，是个证明不了任何东西的假判据
- 索引条目行带 `summary` 摘要：换行压平（否则会把一行劈成两行）、60 字符截断

### JSON 输出 —— `tests/test_output_json.py`（76 断言）

- 四个工具的 JSON 必须可 `json.loads`（本项存在的全部理由）
- **text 形态逐字节不变**：加参数绝不能顺手改了原来的文本
- **范围即契约**：`output_format` 只允许出现在这四个工具上，报告型工具不得有
- 非法取值**报错**且返回的不是 JSON（防「以为拿到 JSON、实际是一句中文」）
- 字段集合精确（多一个键也算破坏性变更）；JSON 与文本**同源**（同一批记录、同样顺序）
- 截断语义：JSON 的 `body` 不含中文截断说明，改由 `truncated` / `total_chars` 承载
- 中文不转义（`ensure_ascii=False`）

### 模块分层守卫 —— `tests/test_import_layers.py`（43 断言）

- 叶子模块不得 import `server`（含函数体内延迟 import 与 `importlib` 动态拉起）
- 本地模块之间**无环**；叶子之间的依赖只能在显式白名单内，且白名单**不得腐烂**
- `server` 必须 import 全部叶子，且运行期**真的加载**（`sys.modules` + 绑定名双重佐证 —— `import memory_index as midx` 要按 `midx` 核）
- 本地 import 必须都在模块顶层；新增模块必须登记进清单（否则从守卫视野里消失）
- **装配/入口层**（`serve_http.py` / `transport_http.py`）依赖方向单向：叶子与聚合器**不得** import 它们，它们也只许 import `server`（装配层若确需直连叶子，必须在白名单里显式登记，且白名单会因陈旧条目被判红）—— 「主程序零改动」不被反向依赖破坏。原 `serve_web` / `web_console` 已随 Web 控制台迁至独立仓，不在本仓的导入图内

### HTTP 传输层 —— `tests/test_transport.py`（43 断言）

- **模式归一**：`stdio` 为默认；`http` / `streamable-http` 等多写法归一；非法值 fail-fast（绝不静默回退 stdio）
- **安全规则**：非回环监听 + 无 Host 白名单 ⇒ 拒绝；关闭 DNS rebinding 防护 ⇒ 拒绝；回环默认与 SDK 语义一致
- **装配（替身 FastMCP）**：stdio 分支调用 `mcp.run()` 且不触碰 settings；http 分支写对 `host/port/path/transport_security`，且**刻意不设 auth**
- **端到端**：真起 `serve_http.py`，客户端走 Streamable HTTP 完成 `initialize` / `list_tools` / `write` / `read`，并验证远程模式下 `memory_restart` 已被自动禁用
- **fail-fast 进程级**：非回环且无白名单时，进程必须非零退出并给出明确提示（而不是静默起一个不校验 Host 的服务）
- **零改动保证**：不带 `AI_MEMORY_TRANSPORT` 时，`server.py` 仍是可用的 stdio 服务且工具数不变

### Web 控制台 —— 已迁至独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)（2026-10-16）

原 `tests/test_web.py` 随控制台一并迁出，现由该仓的 `tests/test_web.py` 承载（覆盖配置 fail-fast / 前端资源完整性 / 分层 / 鉴权 / 只读保护 / 端到端 / 工具分组 / 更新面）。本仓不再含控制台的任何代码与测试 —— 它与本仓的全部关系只有**两条**：① 一个 submodule 引用（`web-console/`）；② 运行期一条 MCP 连接。控制台**不 import 本仓任何模块** —— 阈值读写走 MCP 工具 `memory_config`，端点默认地址是本仓外的字面常量；该仓 `tests/test_web.py` 的 C 段机器守卫此事（`sys.modules` 里出现 `server` / `memory_config` / `transport_http` 任一即红）。唯一例外是 `serve_web.py --with-mcp`（要让控制台替你把 MCP 端点作为子进程拉起），此时须显式给 `--core-dir` / `AI_MEMORY_CORE_DIR` 指定本仓目录。

所有脚本都会把 `AI_MEMORY_DIR` 指向临时目录，**绝不触碰真实记忆库**。

> ⚠️ `tests/*.py` 是**独立脚本**（`python tests/test_xxx.py`，退出码 0 = 全绿），不是 pytest 用例。CI 直接遍历执行，故**新增测试无需改 CI**。

```bash
pip install -r requirements-dev.txt
for f in tests/test_*.py; do python "$f"; done   # 或逐个运行
```

CI（`.github/workflows/ci.yml`）在 push / PR 时自动遍历 `tests/test_*.py`（Python 3.10 / 3.11 / 3.12 / 3.13 矩阵）+ ruff lint。**新增测试无需改 CI**。

> 这二十六层测试是 `server.py` 能够安全模块化重构、并持续演进存储格式的前提 —— 先有测试护航，再动结构。并发与恢复测试上线后立刻暴露了 3 个真实并发缺陷（读路径写盘未持锁、Windows 下 `os.replace` 被读占用、把锁竞争误判为索引损坏），检索质量测试也暴露了 `memory_smart_search` 的过度召回 —— 这正是它们存在的意义。frontmatter 归一化测试则来自一次全库体检：8 篇笔记被同一处写入缺陷写坏，**先复现、再修生成器、最后回填数据**，而不是逐篇手改。

---

## 常见问题

**装完 AI 却不用记忆库？**
多数客户端需要**重启**才会加载新的 MCP 配置。若仍无效，检查配置文件里的 `command` 是否为可用的 Python 解释器绝对路径（推荐绝对路径，避免 PATH 差异）。

**`ImportError: cannot import name 'FastMCP'`？**
装到了 mcp 2.x。执行 `pip install "mcp>=1.30,<2"` 降回 1.x 即可。

**AI 记不住东西，或者写入报错？**
先让 AI 调用 `memory_stats` 确认连通。再确认记忆库目录有写权限（默认 `~/ai-memory`）。

**删掉 `memory_index_*.db` 会丢数据吗？**
不会。它只是运行时缓存，下次启动自动重建。**md 文件才是唯一事实源**。

**`memory_metrics_*.json` 是什么？**
运行时指标快照（检索零结果率、锁等待、索引重建次数等），与索引同目录。纯观测数据，删掉即从零重新累积；设 `AI_MEMORY_METRICS=0` 可不再生成。

**怎么知道记忆库是不是「检索不出来」了？**
调 `memory_stats`，看末尾「## 运行时指标」里的**零结果率**与 `memory_read` 未命中率。零结果率持续偏高通常意味着新问题落在已有记忆的覆盖范围之外，该补笔记了；索引重建次数持续增长则说明索引在反复失效，值得排查。

**能在没有 Obsidian 的情况下用吗？**
完全可以。Obsidian 只是可选的浏览/可视化方式，所有功能都由 MCP 工具提供。

**多个 AI 同时写会冲突吗？**
不会。写入走跨进程文件锁互斥，15 秒超时自动接管陈旧锁。极端并发下还可用 `expected_version` 乐观锁进一步防覆盖。

**记忆库能同步到多台电脑吗？**
可以，用 git。见[多设备同步与版本控制](#多设备同步与版本控制)。

**记忆会不会越积越多？**
配合 tier 体系与 `memory_archive_old` 自动归档，冷数据移入 `.archive/` 且原位保留摘要 stub，检索仍然可达。`memory_stats` 与 `memory_heat_suggest` 可随时体检。

**标题里带 `/` 为什么报错？**
标题里的 `/` 被当作**子目录分隔符**（`memory_write("项目/索引层定案", …)` → `项目/索引层定案.md`）。如果路径指向保留目录（`.archive/` `.trash/` 等）、含 `.` / `..` 段、是盘符/绝对路径，或层级超过 3 级，会被**显式拒绝并说明原因** —— 而不是消毒后悄悄写到别处。把 `/` 换成其它分隔符，或改成合法层级即可。

**为什么索引文件名从 `.记忆索引.md` 又变回了 `记忆索引.md`？**
v2.4.0 曾把生成的 MOC 索引改为**隐藏文件**（`.记忆索引.md`），以免它混在用户笔记里被当成「一篇记忆」。v2.4.2 **撤销**了这个取舍 —— 实测代价太大：全库 25 篇笔记用 `[[记忆索引]]` 引用索引，而 **Obsidian 按文件名 stem 解析双链**，改名后这些链接在人侧全部点不开；索引本身也**根本点不开**（Obsidian 忽略点文件），MOC 的导航价值归零。升级后首次重建时会**自动删除隐藏名残留 `.记忆索引.md`**，但只有确认是生成物（`title` 恰为 `记忆索引` 且带 `moc`/`index`/`navigation` 标签）才删，疑似人工文件会跳过并写日志。你自己的文件不会被碰。

**改了 `memory.toml` 为什么没生效？**
三种可能：① 文件不在 **vault 根目录**（不是 `server.py` 旁边）；② TTL 为 2 秒，稍等片刻再试；③ 内容写错了 —— 此时不会静默退回默认值，调 `memory_stats` 看「运行时阈值配置」小节的 `last_error` 即可看到**行号与原文**。环境变量 `AI_MEMORY_CFG_<KEY>` 优先级高于配置文件。

**`memory_schema_diff` 报了「核心键缺失」，要紧吗？**
那是**真实缺陷** —— core 层（`title`/`tags`/`created`/`updated`/`tier`/`access_count`）缺失意味着写入端没写全。但注意它单列的「无 frontmatter 文件」不算缺陷：`README.md` 这类普通 Markdown 混在 vault 里本就不受结构契约约束。

**调用不积极在ai个性化中加**

```
每次对话开始时，用 mcp__ai-memory__memory_search 搜索"近期工作动态"，了解最近做了什么。遇到不懂的项目或问题，直接搜相关记忆了解背景。
每次完成实质性工作后，必须通过 MCP 工具 mcp__ai-memory__memory_write 把关键信息同步到共享记忆库。写入前先用 memory_search 查重。触发时机：做了技术决策、修了 bug、项目进展、我分享了新偏好、我说"记住"。这条规则每次对话自动注入，不用我再提醒
写入具体项目笔记后须同步维护聚合笔记「近期工作动态」：先用 memory_read 读取它，在当日(YYYY-MM-DD)区块下追加一行 `- [x] 一句话摘要`；若当日区块不存在则新建 `## YYYY-MM-DD` 区块置于顶部（紧接开头说明之后），并同步更新「项目状态」表中相关行的状态。这样可避免聚合时间线滞后于各项目笔记而停更。
```

---

## License

[MIT](LICENSE)
