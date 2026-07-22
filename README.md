# 多平台AI融合记忆库

> 跨 AI 工具共享长期记忆 — 让你的ai工具共用同一个大脑

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-1.27-green)

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

只需要 Python 3.10+ 和一个 `pip install mcp`。不需要数据库、不需要容器、不需要云服务。

### 2. 纯文件存储

所有记忆是**纯 Markdown 文件**。这意味着：
- 你可以用 **Obsidian** 直接打开浏览编辑
- 用 **git** 做版本管理
- 用 **grep** 做全文搜索
- 用你喜欢的任何文本编辑器修改
- 备份就是复制一个文件夹

### 3. 跨平台 + 跨 AI

MCP 是开放协议。这套服务可以接入任何支持 MCP 的客户端：
- **Codex CLI** - 当前对话
- **Claude Desktop** - Anthropic 桌面客户端
- **WorkBuddy** - 国产 AI 助手
- **Cursor** - AI 代码编辑器
- 任何支持 MCP stdio 传输的工具

### 4. 3 个内置自动机制

减少手写工作量，AI 写入时自动处理：

**自动标签** — 正文关键词自动推断标签，30+ 条映射规则覆盖常见领域

```
输入: "用 Python 写了一个 Docker 部署脚本"
输出标签: [deploy, python]
```

**自动链接** — 检测正文中的已知笔记标题，自动补入双向链接

```
输入: "参考了用户画像和记忆索引的内容"
输出 links: [用户画像, 记忆索引]
```

**自动归档** — cold tier 条目在写入时自动移入 `.archive/` 目录，原位保留摘要

### 5. 跨进程并发安全

多 AI 同时写入时，文件锁防止数据覆盖。15 秒超时自动接管过期锁。

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 协议 | **MCP (Model Context Protocol)** | 开放标准，stdio 传输，JSON-RPC 2.0 |
| 框架 | **FastMCP (Python SDK)** | MCP 服务器框架，自动处理协议层 |
| 存储 | **Markdown + JSON Frontmatter** | 每个记忆一个 .md 文件，元数据存 frontmatter |
| 缓存 | **Python dict（内存）** | 条目缓存 + 访问计数缓存，写入时失效 |
| 锁 | **文件锁（PID + 时间戳）** | 跨进程互斥，15 秒超时 |
| 搜索 | **关键词匹配 + 多字段打分** | 标题/标签/摘要/正文加权排序 |
| 图形 | **正则解析 [[Wiki Link]]** | Obsidian 兼容的双链语法 |
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
│  │ 自动标签 │ 自动链接 │ 自动归档                 │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────┐
│                  存储层 (文件系统)                    │
│  ┌──────────────────────────────────────────────┐   │
│  │ D:/ai-memory/  (或任意目录)                    │   │
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

## 16 个工具完整说明

### 读写操作

| 工具 | 签名 | 功能 |
|------|------|------|
| `memory_write` | `(title, content, tags?, source?, summary?, tier?)` | 写入或更新记忆。自动标签 + 自动链接 + 自动归档 |
| `memory_read` | `(title)` | 按标题读取。访问计数缓存在内存，攒够 10 次批量写回 |
| `memory_search` | `(keyword, tag?)` | 关键词搜索，可指定标签过滤 |
| `memory_delete` | `(title)` | 删除记忆（硬删除） |

### 高级查询

| 工具 | 功能 |
|------|------|
| `memory_graph(title)` | 显示指定笔记的出链和反向链接图谱 |
| `memory_orphans()` | 查找**孤立笔记**（没有任何其他笔记引用它） |
| `memory_smart_search(query, tag?, limit?)` | **多字段加权搜索**：标题 ×10、标签 ×4、摘要 ×3、正文 ×1，额外加时效性加分 |
| `memory_list(tag?, limit?, tier?)` | 罗列所有记忆摘要 |
| `memory_stats()` | **健康度统计**：各 tier 数量、访问频率 TOP10、近 7/30/90 天更新量、热门标签 TOP10、孤立笔记数 |

### 维护操作

| 工具 | 功能 |
|------|------|
| `memory_archive(title)` | 归档：正文移入 `.archive/`，原位置留摘要 stub |
| `memory_archive_old(days=90)` | 批量归档 N 天未更新的条目（跳过核心页） |
| `memory_batch_tag(old_tag, new_tag)` | 全库标签重命名 |
| `memory_batch_tier(target_tier, min_score?, max_score?)` | 按热度分数批量调整 tier |
| `memory_heat_suggest()` | 扫描全库，给出 tier 升降建议 |

### 审计工具

| 工具 | 功能 |
|------|------|
| `memory_audit()` | 全库扫描，按类型分组汇总，建议整理 |
| `memory_index_draft()` | 自动生成记忆索引草稿 |

---

## 快速开始

### 环境要求

- Python 3.10+
- 安装 `mcp` 包：`pip install mcp`

### 一键安装

```bash
# macOS / Linux
curl -sSL https://raw.githubusercontent.com/dpkg-s/ai-memory-template/main/install.sh | bash

# Windows PowerShell
irm https://raw.githubusercontent.com/dpkg-s/ai-memory-template/main/install.ps1 | iex
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
pip install mcp
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

设置环境变量即可：

```bash
export AI_MEMORY_DIR=/path/to/your/memory
python server.py
```

### 自定义自动标签规则

编辑 `server.py` 中的 `_TAG_AUTO_MAP` 字典，约 30 条关键词 → 标签映射。按需增删。

---

## 项目结构

```
ai-memory-template/
├── README.md               # 本文档
├── server.py               # MCP 服务器（单文件，~1300 行）
├── install.ps1             # Windows 一键安装脚本
├── install.sh              # Linux/macOS 一键安装脚本
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
    └── 记忆写入格式规范.md
```

---

## License

MIT
