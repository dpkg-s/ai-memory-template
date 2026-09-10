# ai-memory-template

**让所有 AI 工具共用同一个长期记忆。**

基于 [MCP](https://modelcontextprotocol.io/)（Model Context Protocol）的跨工具共享记忆库：纯 Markdown 存储、零外部依赖、Obsidian 可视化。WorkBuddy / Codex CLI / Claude Desktop / Cursor 等任何支持 MCP 的客户端即插即用。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml/badge.svg)](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-2.1.0-blue)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.x-green)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-77%20assertions-brightgreen)](#测试与-ci)

---

## 目录

- [为什么需要它](#为什么需要它)
- [特性](#特性)
- [快速开始](#快速开始)
- [实际使用效果](#实际使用效果)
- [20 个工具速查](#20-个工具速查)
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

MCP 是开放协议，服务端只提供 stdio 传输，因此可接入：

- **WorkBuddy** — 国产 AI 助手
- **Codex CLI** — OpenAI 终端 AI 助手
- **Claude Desktop** — Anthropic 桌面客户端
- **Cursor** — AI 代码编辑器
- 任何支持 MCP stdio 的工具

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

### 显式双链，关系可追溯

链接关系用 Obsidian 风格的 `[[Wiki Link]]` 在正文里显式标注，**正文双链是链接关系的唯一事实源**。

自动补链已停用（避免制造大量冗余链接），`memory_rebuild_links` 改为只读校验：扫描全库双链、报告死链与计数，不改任何文件。配套工具 `memory_graph`（出入链图谱）与 `memory_orphans`（孤立笔记检测）。

### 跨进程并发安全

多个 AI 同时写入时，基于 PID + 时间戳的**文件锁**保证互斥，15 秒超时并自动接管陈旧锁。锁在**线程内可重入**，因此 `memory_write → 自动归档 → memory_archive` 这条嵌套链路不会自死锁。

### 工程化保障

v2.0.0 起，仓库自带测试与持续集成：

- **77 条断言**的三层测试（语义回归 28 + 全工具冒烟 25 + 记忆可信度 24）
- **GitHub Actions CI**：Python 3.10 / 3.11 / 3.12 / 3.13 矩阵跑测试 + ruff lint
- **模块化结构**：无状态工具层拆为 `yaml_io.py` / `text_utils.py` / `locks.py`

---

## 快速开始

### 环境要求

- **Python 3.10+**
- 运行时依赖：`pip install -r requirements.txt`（仅 `mcp`，约束为 `mcp>=1.27,<2`）
- 开发/测试额外依赖：`pip install -r requirements-dev.txt`

> **注意版本上界**：依赖锁定在 `mcp<2`。mcp 2.x 已将 `FastMCP` 更名为 `MCPServer`，装到 2.x 会直接 import 失败。

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

### 方式二：一键脚本

**macOS / Linux：**

```bash
curl -sSL https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh | bash
```

**Windows PowerShell：**

```powershell
irm https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 | iex
```

脚本会依次：安装 `mcp` 包 → 创建 `~/ai-memory/` → 复制初始模板文件 → 打印各平台的 MCP 配置片段。

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

## 20 个工具速查

### 读写

| 工具 | 签名 | 说明 |
|------|------|------|
| `memory_write` | `(title, content, tags?, source?, summary?, tier?, mem_type?, confidence?, verified?, expected_version?)` | 写入或更新。自动标签 + 自动归档；同标题 upsert；支持乐观锁与可信度标记 |
| `memory_read` | `(title, max_chars=8000)` | 按标题精确读取（含文件名回退，可命中 `.archive`）。默认截断正文 8000 字符，`max_chars=0` 取全文 |
| `memory_delete` | `(title, trash=True, purge=False)` | 删除。默认软删到 `.trash` 可恢复，`purge=True` 永久删除 |
| `memory_update_metadata` | `(title, tier?, tags?, summary?, source?, mem_type?, confidence?, verified?)` | 只改 frontmatter 元数据（含可信度字段），不重写正文 |
| `memory_recent` | `(days=7, limit=20)` | 列出近 N 天更新的记忆，最新优先 |

### 查询

| 工具 | 说明 |
|------|------|
| `memory_search(keyword, tag?, limit=20, mem_type?)` | 关键词搜索标题 / 标签 / 正文，命中处 `**` 高亮，可按标签或类型过滤 |
| `memory_smart_search(query, tag?, limit=10, mem_type?)` | 多字段加权搜索：标题 ×10、标签 ×4、摘要 ×3、正文 ×1，额外加时效性加分 |
| `memory_list(tag?, limit=20, tier?, mem_type?)` | 罗列记忆摘要 |
| `memory_graph(title, limit=10, include_all=False)` | 显示指定笔记的出链与反向链接图谱，默认截断 10 条 |
| `memory_orphans()` | 查找没有任何笔记引用的孤立笔记 |
| `memory_stats()` | 健康度统计：各 tier 数量、记忆类型分布、可信度分布、访问 TOP10、近 7/30/90 天更新量、热门标签 TOP10、孤立笔记数 |

### 维护

| 工具 | 说明 |
|------|------|
| `memory_archive(title)` | 归档：正文移入 `.archive/`，原位留摘要 stub |
| `memory_archive_old(days=90)` | 批量归档 N 天未更新的条目，自动跳过核心页 |
| `memory_restore(title)` | 从 `.archive/` 恢复，tier 重置为 warm |
| `memory_batch_tag(old_tag, new_tag)` | 全库标签重命名 |
| `memory_batch_tier(target_tier, min_score?, max_score?)` | 按热度分数批量调整 tier |
| `memory_heat_suggest()` | 按访问频次与陈旧度给出 tier 升降建议 |

### 审计

| 工具 | 说明 |
|------|------|
| `memory_audit()` | 全库扫描：空壳检测 / 死链检测（跳过代码块）/ 命名漂移 / 缺 source·tags 汇总 |
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

### 自定义自动标签规则

编辑 `server.py` 中的 `_TAG_AUTO_MAP` 字典（目前 68 条「正文关键词 → 标签」映射），按需增删即可。

### tier 体系与归档策略

每个条目有 `hot` / `warm` / `cold` 三档：

| tier | 含义 | 行为 |
|------|------|------|
| `hot` | 高频访问 | 常驻根目录 |
| `warm` | 一般 | 常驻根目录（默认） |
| `cold` | 冷数据 | **写入时自动归档**到 `.archive/`，原位留摘要 stub |

`memory_archive_old(days=90)` 可按时间批量归档，自动跳过 `CORE_PAGES` 保护名单（`记忆索引`、`近期工作动态`、`用户画像` 等 13 个核心页）。`memory_heat_suggest()` 会依据实际访问频次与陈旧度给出升降建议。

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
        ▼  MCP 协议 · stdio · JSON-RPC 2.0
  server.py —— 20 个 MCP 工具
  ├─ 读写：write / read / delete / update_metadata / recent
  ├─ 查询：search / smart_search / list / graph / orphans / stats
  ├─ 维护：archive / archive_old / restore / batch_tag / batch_tier / heat_suggest
  ├─ 审计：audit / index_draft / rebuild_links
  ├─ 无状态工具层（可独立测试）
  │    yaml_io.py · text_utils.py · locks.py
  └─ 基础设施：条目缓存 · 访问计数 · SQLite 索引 · 自动标签 · 自动归档
        │
        ▼
  存储层  ~/ai-memory/（纯 Markdown，也可直接用 Obsidian 打开）
  ├─ 用户画像.md · 近期工作动态.md · 项目_xxx.md · 记忆索引.md
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
├── server.py                # MCP 服务端：20 个工具注册 + 存储层 + 业务逻辑
├── memory_index.py          # SQLite 元数据索引（标题 O(1) 定位）
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
│
├── tests/
│   ├── test_roundtrip.py       # 28 断言：语义回归
│   ├── test_tools_smoke.py     # 25 断言：全工具冒烟 + 注册完整性
│   └── test_p0_credentials.py  # 24 断言：记忆可信度字段
├── .github/workflows/
│   └── ci.yml               # CI：Python 矩阵测试 + ruff lint
│
├── setup/                   # 各客户端 MCP 配置模板
│   ├── workbuddy.json
│   ├── claude.json
│   └── codex.toml
├── template/                # 初始记忆文件（开箱即用）
│   ├── 用户画像.md
│   ├── 近期工作动态.md
│   ├── 记忆索引.md
│   └── 记忆写入格式规范.md
└── rules/                   # AI 写入时遵循的规范
    ├── 记忆写入格式规范.md
    └── Git版本控制规范.md
```

---

## 测试与 CI

测试分三层：**语义正确性**（写读是否无损）、**覆盖面**（工具是否都能用）、**字段语义**（可信度标记是否可靠）。

### 语义回归 —— `tests/test_roundtrip.py`（28 断言）

- frontmatter 写 → 读无损 round-trip（中文标题 / 多行正文 / tags 特殊字符 / 类型保持）
- Windows 路径反斜杠不雪崩（连续 5 轮 update 回归）
- `memory_read` 不污染 `updated`（P0-1 修复的回归保护）
- `max_chars` 截断语义
- 文件名非法字符安全处理
- SQLite 索引层（写入命中 / read 走索引 / miss 回退 / 外部改动自愈 / delete 清理）

### 全工具冒烟 —— `tests/test_tools_smoke.py`（25 断言）

- 20 个 MCP 工具逐一调用，验证不抛异常且返回正常
- 工具注册完整性：数量为 20 且集合与预期完全一致（防重构时漏注册或误覆盖）

### 记忆可信度 —— `tests/test_p0_credentials.py`（24 断言）

- 写入落盘：显式取值正确写入，缺省时回落 `fact` / `medium` / `false`
- 更新语义：未传字段继承原值（不被重置），显式传入时覆盖生效
- 非法取值被拒绝且不落盘
- 向后兼容：无字段的历史笔记读取时注入默认值、被写入时渐进补齐
- `verified_at` 生命周期：置 `true` 写入日期，置 `false` 清除日期
- 检索过滤（`mem_type`）与统计分布输出
- 序列化层边界：非法值防御性兜底、`false` 不被序列化为 `null`

三个脚本都会把 `AI_MEMORY_DIR` 指向临时目录，**绝不触碰真实记忆库**。

```bash
pip install -r requirements-dev.txt
python tests/test_roundtrip.py
python tests/test_tools_smoke.py
python tests/test_p0_credentials.py
```

CI（`.github/workflows/ci.yml`）在 push / PR 时自动跑 Python 3.10–3.13 矩阵（三个脚本）+ ruff lint。

> 这三层测试是 `server.py` 能够安全模块化重构、并持续演进存储格式的前提 —— 先有测试护航，再动结构。

---

## 常见问题

**装完 AI 却不用记忆库？**
多数客户端需要**重启**才会加载新的 MCP 配置。若仍无效，检查配置文件里的 `command` 是否为可用的 Python 解释器绝对路径（推荐绝对路径，避免 PATH 差异）。

**`ImportError: cannot import name 'FastMCP'`？**
装到了 mcp 2.x。执行 `pip install "mcp>=1.27,<2"` 降回 1.x 即可。

**AI 记不住东西，或者写入报错？**
先让 AI 调用 `memory_stats` 确认连通。再确认记忆库目录有写权限（默认 `~/ai-memory`）。

**删掉 `memory_index_*.db` 会丢数据吗？**
不会。它只是运行时缓存，下次启动自动重建。**md 文件才是唯一事实源**。

**能在没有 Obsidian 的情况下用吗？**
完全可以。Obsidian 只是可选的浏览/可视化方式，所有功能都由 MCP 工具提供。

**多个 AI 同时写会冲突吗？**
不会。写入走跨进程文件锁互斥，15 秒超时自动接管陈旧锁。极端并发下还可用 `expected_version` 乐观锁进一步防覆盖。

**记忆库能同步到多台电脑吗？**
可以，用 git。见[多设备同步与版本控制](#多设备同步与版本控制)。

**记忆会不会越积越多？**
配合 tier 体系与 `memory_archive_old` 自动归档，冷数据移入 `.archive/` 且原位保留摘要 stub，检索仍然可达。`memory_stats` 与 `memory_heat_suggest` 可随时体检。

**调用不积极在ai个性化中加**

```
每次对话开始时，用 mcp__ai-memory__memory_search 搜索"近期工作动态"，了解最近做了什么。遇到不懂的项目或问题，直接搜相关记忆了解背景。
每次完成实质性工作后，必须通过 MCP 工具 mcp__ai-memory__memory_write 把关键信息同步到共享记忆库。写入前先用 memory_search 查重。触发时机：做了技术决策、修了 bug、项目进展、我分享了新偏好、我说"记住"。这条规则每次对话自动注入，不用我再提醒
写入具体项目笔记后须同步维护聚合笔记「近期工作动态」：先用 memory_read 读取它，在当日(YYYY-MM-DD)区块下追加一行 `- [x] 一句话摘要`；若当日区块不存在则新建 `## YYYY-MM-DD` 区块置于顶部（紧接开头说明之后），并同步更新「项目状态」表中相关行的状态。这样可避免聚合时间线滞后于各项目笔记而停更。
```

---

## License

[MIT](LICENSE)
