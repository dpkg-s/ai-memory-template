# ai-memory-template

> 跨 AI 工具共享记忆库 — 让你的 AI 助手们共享长期记忆

## 这是什么

你有多个 AI 工具在同时用吗？WorkBuddy、Claude Code、Codex CLI……每个都有自己的上下文窗口，互相不知道对方做了什么。

**ai-memory-template** 给它们搭了一个共享记忆库：

- 一个 AI 修了 bug → 其他 AI 都知道
- 你分享的偏好 → 所有 AI 都记住
- 项目状态 → 跨对话保持一致

## 工作原理

```
WorkBuddy ──┐
Claude Code ─┤── MCP 协议 ──→ server.py ──→ memory/*.md
Codex ──────┘                              │
                                           Obsidian（直接查看）
```

- 记忆存为 Markdown 文件，可以用 Obsidian 直接看
- AI 通过 16 个 MCP 工具读写记忆
- 自动标签、自动链接、热度管理、文件锁

## 30 秒快速开始

### 一键安装

```bash
# macOS / Linux
curl -sSL https://raw.githubusercontent.com/dpkg-s/ai-memory-template/main/install.sh | bash

# Windows PowerShell
irm https://raw.githubusercontent.com/dpkg-s/ai-memory-template/main/install.ps1 | iex
```

### 手动安装

```bash
git clone https://github.com/dpkg-s/ai-memory-template.git
cd ai-memory-template
pip install mcp
bash install.sh    # Linux/macOS
.\install.ps1     # Windows
```

## 可用工具（16 个）

| 工具 | 功能 |
|------|------|
| memory_write | 写入/更新（自动标签+自动链接） |
| memory_read | 读取（内存缓存，批量写回） |
| memory_search | 关键词搜索 |
| memory_delete | 删除 |
| memory_graph | 链接图谱（出链+反向链接） |
| memory_orphans | 查找孤立笔记 |
| memory_smart_search | 多字段打分搜索 |
| memory_list | 罗列记忆 |
| memory_stats | 健康度统计 |
| memory_archive | 归档 |
| memory_archive_old | 批量归档 |
| memory_batch_tag | 批量重命名标签 |
| memory_batch_tier | 批量调级 |
| memory_heat_suggest | 热度分析 |
| memory_audit | 全库扫描 |
| memory_index_draft | 索引草稿 |

## 内置自动行为

1. 自动标签：正文关键词自动推断标签
2. 自动链接：检测已知笔记标题，补入 links
3. 自动归档：cold tier 条目自动移入 .archive/

## 项目结构

```
ai-memory-template/
├── README.md
├── server.py          # MCP 服务器（核心）
├── install.ps1        # Windows 安装
├── install.sh         # Linux/macOS 安装
├── setup/
│   ├── codex.toml
│   ├── claude.json
│   └── workbuddy.json
├── template/          # 初始记忆文件
│   ├── 近期工作动态.md
│   ├── 用户画像.md
│   ├── 记忆索引.md
│   └── 记忆写入格式规范.md
└── rules/
    └── 记忆写入格式规范.md
```

## 依赖

- Python 3.10+
- `pip install mcp`

## License

MIT

