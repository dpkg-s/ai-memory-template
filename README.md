# 多平台AI融合记忆库

> 跨 AI 工具共享长期记忆 — 让你的ai工具无需复杂的环境共用同一个大脑 — 可视化记忆

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/dpkg-s/ai-memory-template/workflows/CI/badge.svg)](https://github.com/dpkg-s/ai-memory-template/actions)
![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-1.x-green)

> 版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

---

## 这是什么

你有多个 AI 工具同时用吗？它们各自有独立的上下文窗口，互相不知道对方做过什么。

**ai-memory-template** 通过 **MCP（Model Context Protocol，模型上下文协议）** 给所有 AI 搭了一个共享记忆库。一个 AI 写入的知识，所有 AI 都能读到。

### 对比：有记忆 vs 没有记忆

| 场景 | 没有 shared memory | 有 shared memory |
|------|------------------|------------------|
| 你告诉 AI 你用 VS Code、偏好 Python | 下次对话 AI 又问一遍 | AI 自动从记忆库读到，直接记住 |
| WorkBuddy 修了一个 bug | Codex 不知道，可能重复排查 | Codex 一查记忆库就知道 |
| 项目架构决策 | 下一轮对话上下文一清就丢了 | 永久记录在项目页，跨对话保持 |
| 多 AI 协作 | 各自为政，信息割裂 | 共享同一份知识库 |

---

## 核心优势

### 1. 零外部依赖

只需要 Python 3.10+ 和一个 `pip install mcp`。不需要装数据库服务、不需要容器、不需要云服务。

> **性能说明（SQLite 元数据索引）**：`memory_read` / `memory_list` 的标题定位走 Python 内置的 `sqlite3`（标准库自带，非外部依赖、无需安装），把 O(n) 全库扫描降为 O(1)。索引只是运行时自动生成的**缓存文件**，仅镜像 frontmatter 元数据——正文永不入库、实时读盘，md 文件仍是唯一事实源；索引损坏或缺失时自动回退全库扫描，功能不降级。

### 2. 纯文件存储

所有记忆是**纯 Markdown 文件**（数据库只是运行时生成的缓存，删掉随时重建，记忆永不丢失）。这意味着：
- 你可以用 **Obsidian** 直接打开浏览编辑
- 用 **git** 做版本管理
- 用 **grep** 做全文搜索
- 用你喜欢的任何文本编辑器修改
- 备份就是复制一个文件夹

### 3. 可视化记忆网络

因为所有记忆是 **Markdown + 双链 ([[Wiki Link]])** 格式，你可以直接用 **Obsidian** 打开记忆库目录，获得完整的知识图谱可视化：

- **图谱视图 (Graph View)** — 所有笔记的链接网络一目了然，看到哪些项目关联最紧密、哪些笔记是枢纽节点
- **局部图谱 (Local Graph)** — 选中一条笔记，只看它的出链和反向链接
- **孤岛检测** — Obsidian 自动高亮没有链接的笔记，与 `memory_orphans` 工具互补

```
Obsidian 图谱视图效果（文本示意）:

               运维a
                ↑
    ┌──—————— 项目a  ———————──┐
    │         ↑              │
    │    用户画像 ←──── 记忆索引
    │         ↓              │
    └──———————— 项目b ———————─┘
                ↓
          近期工作动态
```

同时，`memory_graph` 和 `memory_orphans` 两个 MCP 工具在 AI 对话中也能直接查询链接关系，不依赖 Obsidian。

### 4. 跨平台 + 跨 AI

MCP 是开放协议。这套服务可以接入任何支持 MCP 的客户端：
- **Codex CLI** - OpenAI 终端 AI 助手
- **Claude Desktop** - Anthropic 桌面客户端
- **WorkBuddy** - 国产 AI 助手
- **Cursor** - AI 代码编辑器
- 任何支持 MCP stdio 传输的工具

### 5. 2 个内置自动机制 + 显式双链

AI 写入时自动处理，同时支持手动标注关联：

**自动标签** — 正文关键词自动推断标签，30+ 条映射规则覆盖常见领域

```
输入: "用 Python 写了一个 Docker 部署脚本"
输出标签: [deploy, python]
```

**显式双链** — 记忆用 Obsidian 风格 `[[Wiki Link]]` 手动标注关联，正文显式双链是链接关系的唯一事实源（自动补链已停用以避免冗余，`memory_rebuild_links` 只做只读校验）

**自动归档** — cold tier 条目在写入时自动移入 `.archive/` 目录，原位保留摘要

### 6. 跨进程并发安全

多 AI 同时写入时，文件锁防止数据覆盖。15 秒超时自动接管过期锁。

---
## 快速开始

### 环境要求

- Python 3.10+
- 安装依赖：`pip install -r requirements.txt`（仅 `mcp>=1.27`）
- 开发/测试额外依赖：`pip install -r requirements-dev.txt`（pytest + ruff）

### 一键安装


ai助手-快速上手：
```bash
请帮我部署 ai-memory-template 这个 MCP 项目。项目地址是 https://github.com/dpkg-s/ai-memory-template
步骤：
1、克隆或下载这个项目到我本地
2、安装依赖（pip install mcp）
3、帮我创建一个记忆库目录
4、根据我的操作系统（Windows / Mac / Linux），帮我配置好 MCP 设置，让它开机自启
5、最后告诉我怎么验证它是否正常工作
```


macOS / Linux：
```bash
curl -sSL https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh | bash
```


Windows PowerShell：
```bash
irm https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 | iex
```

安装脚本会：
1. 安装 `mcp` Python 包
2. 在 `~/ai-memory/` 创建记忆库目录
3. 复制初始模板文件
4. 打印各平台的 MCP 配置

### 手动安装

```bash
git clone https://github.com/dpkg-s/ai-memory-template.git
cd ai-memory-template
pip install -r requirements.txt
bash install.sh    # Linux/macOS
.\install.ps1     # Windows
```

### 配置 AI 工具

将安装脚本输出的配置添加到对应 AI 工具的 MCP 配置中：

**Codex CLI** — `~/.codex/config.toml`
```toml
[mcp_servers]
[mcp_servers.ai-memory]
command = "/usr/bin/python3"
args = ["/home/you/ai-memory/server.py"]
```

**Claude Desktop** — `claude_desktop_config.json`
```json
{
  "mcpServers": {
    "ai-memory": {
      "command": "/usr/bin/python3",
      "args": ["/home/you/ai-memory/server.py"]
    }
  }
}
```

**WorkBuddy** — `~/.workbuddy/mcp.json`
```json
{
  "mcpServers": {
    "ai-memory": {
      "type": "stdio",
      "command": "/usr/bin/python3",
      "args": ["/home/you/ai-memory/server.py"]
    }
  }
}
```

> 完整的配置模板见 [`setup/`](setup/) 目录。

---

## 自定义

### 修改记忆库路径

默认记忆库目录为 `~/ai-memory`（`install.sh` / `install.ps1` 自动创建）。如需自定义，设置环境变量即可：

```bash
export AI_MEMORY_DIR=/path/to/your/memory
python server.py
```

Windows PowerShell 下：

```powershell
$env:AI_MEMORY_DIR = "D:\my-memory"
python server.py
```

### 自定义自动标签规则

编辑 `server.py` 中的 `_TAG_AUTO_MAP` 字典，约 30 条关键词 → 标签映射。按需增删。


---
## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 协议 | **MCP (Model Context Protocol)** | 开放标准，stdio 传输，JSON-RPC 2.0 |
| 框架 | **FastMCP (Python SDK)** | MCP 服务器框架，自动处理协议层 |
| 存储 | **Markdown + YAML Frontmatter** | 每个记忆一个 .md 文件，元数据存 frontmatter（Obsidian 原生识别，兼容旧 JSON） |
| 索引 | **SQLite（内置 sqlite3，运行时缓存）** | 镜像 frontmatter 元数据用于标题 O(1) 定位，正文实时读盘；可随时删除重建，故障自动回退全库扫描 |
| 缓存 | **Python dict（内存）** | 条目缓存 + 访问计数缓存，写入时失效 |
| 锁 | **文件锁（PID + 时间戳）** | 跨进程互斥，15 秒超时 |
| 搜索 | **关键词匹配 + 多字段打分** | 标题/标签/摘要/正文加权排序 |
| 图形 | **正则解析 [[Wiki Link]]** | Obsidian 兼容的双链语法，配合 Obsidian 图谱视图可直接可视化 |
| 审计 | **全库扫描 + 热度分析** | 健康度统计 + 冷热数据识别 |

### 架构图

```
┌─────────────────────────────────────────────────────┐
│                    AI 客户端层                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Codex    │  │ Claude   │  │ WorkBuddy        │  │
│  │ CLI      │  │ Desktop  │  │ (and any MCP     │  │
│  │          │  │          │  │  compatible tool)│  │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│       │             │                 │             │
├───────┴─────────────┴─────────────────┴─────────────┤
│                  MCP 协议层 (stdio)                   │
│              JSON-RPC 2.0 over stdin/stdout           │
├──────────────────────┬──────────────────────────────┤
│               server.py (MCP Server)                  │
│  ┌────────┬────────┬────────┬────────┬───────────┐  │
│  │ 读写   │ 搜索   │ 图谱   │ 统计   │ 维护     │  │
│  │ write  │ search │ graph  │ stats  │ archive   │  │
│  │ read   │  smart │orphans │  heat  │ batch_tag │  │
│  │ delete │_search │        │_suggest│ batch_tier│  │
│  │        │  list  │        │ audit  │_old       │  │
│  └────────┴────────┴────────┴────────┴───────────┘  │
│  ┌──────────────────────────────────────────────┐   │
│  │ 基础设施                                      │   │
│  │ 文件锁 │ 条目缓存 │ 访问计数缓存 │ 日志        │   │
│  │ SQLite 索引 │ 自动标签 │ 自动归档             │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────┐
│                  存储层 (文件系统)                    │
│  ┌──────────────────────────────────────────────┐   │
│  │ ~/ai-memory/  (或 AI_MEMORY_DIR 指定目录)     │   │
│  │  ├── 用户画像.md                               │   │
│  │  ├── 近期工作动态.md                            │   │
│  │  ├── 项目_xxx.md                               │   │
│  │  ├── 记忆索引.md                               │   │
│  │  └── .archive/                                │   │
│  │       └── 已归档条目.md (摘要 stub 留在原地)     │   │
│  └──────────────────────────────────────────────┘   │
│      ↑ 你也可以直接用 Obsidian 打开这个目录          │
└─────────────────────────────────────────────────────┘
```

---

## 20 个工具完整说明

### 读写操作

| 工具 | 签名 | 功能 |
|------|------|------|
| `memory_write` | `(title, content, tags?, source?, summary?, tier?, expected_version?)` | 写入或更新记忆。自动标签 + 自动归档；拒绝空内容；同标题自动 upsert 覆盖；支持乐观锁 |
| `memory_read` | `(title, max_chars=8000)` | 按标题精确读取（含文件名回退，命中 `.archive`）。访问计数缓存在内存，攒够 10 次批量写回；正文默认截断 8000 字符，`max_chars=0` 取全文 |
| `memory_search` | `(keyword, tag?, limit?)` | 关键词搜索（标题/标签/正文），命中处 `**` 高亮，可指定标签过滤 |
| `memory_delete` | `(title, trash=True, purge=False)` | 删除记忆（默认软删到 `.trash` 可恢复；`purge=True` 永久删） |
| `memory_update_metadata` | `(title, tier?, tags?, summary?, source?)` | 仅更新 frontmatter 元数据，不重写正文 |
| `memory_recent` | `(days=7, limit?)` | 列出近 N 天更新的记忆（最新优先） |

### 高级查询

| 工具 | 功能 |
|------|------|
| `memory_graph(title, limit=10, include_all=False)` | 显示指定笔记的出链和反向链接图谱 |
| `memory_orphans()` | 查找**孤立笔记**（没有任何其他笔记引用它） |
| `memory_smart_search(query, tag?, limit?)` | **多字段加权搜索**：标题 ×10、标签 ×4、摘要 ×3、正文 ×1，额外加时效性加分 |
| `memory_list(tag?, limit?, tier?)` | 罗列所有记忆摘要 |
| `memory_stats()` | **健康度统计**：各 tier 数量、访问频率 TOP10、近 7/30/90 天更新量、热门标签 TOP10、孤立笔记数 |

### 维护操作

| 工具 | 功能 |
|------|------|
| `memory_archive(title)` | 归档：正文移入 `.archive/`，原位置留摘要 stub |
| `memory_archive_old(days=90)` | 批量归档 N 天未更新的条目（跳过核心页） |
| `memory_restore(title)` | 从 `.archive/` 恢复归档笔记回根目录，tier 回 warm |
| `memory_batch_tag(old_tag, new_tag)` | 全库标签重命名 |
| `memory_batch_tier(target_tier, min_score?, max_score?)` | 按热度分数批量调整 tier |
| `memory_heat_suggest()` | 扫描全库，按访问频次与陈旧度给出 tier 升降建议 |
| `memory_rebuild_links()` | 只读校验：扫描正文 `[[双链]]`，报告死链（指向不存在标题）与计数，不修改文件 |

### 审计工具

| 工具 | 功能 |
|------|------|
| `memory_audit()` | 全库扫描：空壳检测 / 死链检测（跳过代码块）/ 命名漂移 / 缺 source·tags 汇总，建议整理 |
| `memory_index_draft()` | 自动生成记忆索引草稿（不回写，需人工确认） |

---


## Git 版本控制 + 多设备同步（推荐）

> 给记忆库加上 git 版本控制，每次写入自动 commit + push，多台电脑无缝同步。

安装脚本会自动将 `rules/Git版本控制规范.md` 复制到记忆库目录。**AI 助手打开记忆库后，搜索"git"即可自动发现并遵守此规范**，无需人工提醒。

### 给 AI 助手的规范

> ⚠️ 以下规则应写入你的记忆库规则中，让所有 AI 自动遵守。

**每次 `memory_write` 完成后，必须自动执行：**

```bash
cd ~/ai-memory
git add -A
git commit -m "{source}: {动作} - {简要说明}"
git push
```

| 约定 | 说明 |
|------|------|
| commit 格式 | `{source}: {动作} - {简要说明}`，例如 `codex: 更新项目状态 - 完成TS合并工具v4` |
| source 标识 | 每个 AI 用自己的标识：`codex`、`claude`、`workbuddy` |
| push | commit 后立即 push，确保远程始终最新 |

**每次对话开始时，先拉取最新：**

```bash
cd ~/ai-memory && git pull
```

### 手动设置（首次）

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

### 多台电脑使用

在其他电脑上：

```bash
git clone https://github.com/你的用户名/ai-memory.git ~/ai-memory
```

clone 完成即可使用，所有 AI 的记忆自动同步。

---
## 项目结构

```
ai-memory-template/
├── README.md               # 本文档
├── CHANGELOG.md            # 版本变更记录
├── server.py               # MCP 服务器：20 个工具注册 + 存储层 + 业务逻辑（~1600 行）
├── memory_index.py         # SQLite 元数据索引模块（标题 O(1) 定位）
├── yaml_io.py              # YAML frontmatter 解析/序列化（无状态纯函数层）
├── text_utils.py           # 文本/时间/热度工具（无状态纯函数层）
├── locks.py                # 跨进程文件锁（线程内可重入 + 抗陈旧锁）
├── install.ps1             # Windows 一键安装脚本
├── install.sh              # Linux/macOS 一键安装脚本
├── requirements.txt        # 运行时依赖（mcp）
├── requirements-dev.txt    # 开发/测试依赖（pytest + ruff）
├── ruff.toml               # ruff lint 配置（聚焦真实错误 F 系列）
├── LICENSE                 # MIT 许可证
├── tests/                  # 测试（语义回归 + 全工具冒烟）
│   ├── test_roundtrip.py   # 28 断言：写读无损 / 反斜杠 / 截断 / 索引层
│   └── test_tools_smoke.py # 25 断言：20 个工具全量可调用 + 注册完整性
├── .github/workflows/      # CI（Python 矩阵跑测试 + ruff lint）
│   └── ci.yml
├── setup/                  # 各平台的 MCP 配置模板
│   ├── codex.toml
│   ├── claude.json
│   └── workbuddy.json
├── template/               # 初始记忆文件（开箱即用）
│   ├── 近期工作动态.md
│   ├── 用户画像.md
│   ├── 记忆索引.md
│   └── 记忆写入格式规范.md
└── rules/                  # AI 写入时遵循的规范
    ├── 记忆写入格式规范.md
    └── Git版本控制规范.md
```

---

## 测试

测试分两层，互为补充：**语义正确性**（写读是否无损）与**覆盖面**（工具是否都能用）。

### 1. 语义回归 — `tests/test_roundtrip.py`（28 断言）

- frontmatter 写→读无损 round-trip（中文标题 / 多行 / tags 特殊字符 / 类型保持）
- Windows 路径反斜杠不雪崩（连续 5 轮 update 回归）
- `memory_read` 不污染 `updated`（P0-1 修复）
- `max_chars` 截断语义
- 文件名非法字符安全
- SQLite 索引层（写入命中 / read 走索引 / miss 回退 / 外部改动自愈 / delete 清理）

### 2. 全工具冒烟 — `tests/test_tools_smoke.py`（25 断言）

- 20 个 MCP 工具逐一调用，验证不抛异常且返回正常
- 工具注册完整性：数量为 20、集合与预期完全一致（防重构时漏注册或误覆盖）

两个脚本都会把 `AI_MEMORY_DIR` 指向临时目录，**绝不触碰真实的记忆库**。运行：

```bash
pip install -r requirements-dev.txt
python tests/test_roundtrip.py
python tests/test_tools_smoke.py
```

CI（`.github/workflows/ci.yml`）在 push / PR 时自动跑 Python 3.10–3.13 矩阵（两个脚本）+ ruff lint。

> 这两层测试是 `server.py` 能够安全模块化重构（P3）的前提：先有测试护航，再做结构调整。

---

## License

MIT




