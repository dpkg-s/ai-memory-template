# ai-memory-template

**让所有 AI 工具共用同一个长期记忆。**

基于 [MCP](https://modelcontextprotocol.io/) 的跨工具共享记忆库：纯 Markdown 存储、零外部依赖、Obsidian 可视化。WorkBuddy / Codex CLI / Claude Desktop / Cursor 等任何支持 MCP 的客户端即插即用。

[![Release](https://img.shields.io/github/v/release/dpkg-s/ai-memory-template?label=release&color=blue)](https://github.com/dpkg-s/ai-memory-template/releases/latest)
[![CI](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml/badge.svg)](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.30--1.x-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 30 秒安装

```bash
# macOS / Linux
curl -sSLO https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh
less install.sh && bash install.sh
```

```powershell
# Windows PowerShell
Invoke-WebRequest -Uri https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 -OutFile install.ps1
Get-Content install.ps1; .\install.ps1
```

Windows 图形化安装：从 [最新 Release](https://github.com/dpkg-s/ai-memory-template/releases/latest) 下载
`ai-memory-setup.exe`，同页附 `ai-memory-setup.exe.sha256`，可 `sha256sum -c` 校验后再执行。

安装器只做三件事：`pip install mcp`（`mcp>=1.30,<2`）、创建 `~/ai-memory/` 并写入模板、复制 `server.py`
及其依赖模块。不改 shell 配置、不写注册表、不需要管理员权限、不常驻后台。

> 三种安装路径的完整说明（含「交给 AI 助手部署」提示词、各平台 MCP 客户端配置、验证步骤）见
> **[docs/install.md](docs/install.md)**。

---

## 为什么需要它

你同时用多个 AI 工具吗？它们各有独立的上下文窗口，互相不知道对方做过什么。于是：

- 同一个偏好，你要对每个 AI 重复交代一遍
- A 工具刚修好的坑，B 工具下一轮又从零排查
- 对话一结束，上下文清空，项目决策随之丢失

**ai-memory-template** 给所有 AI 接上同一个记忆库。一个 AI 写进去的知识，其余 AI 全部可读。

| 场景 | 没有共享记忆 | 有共享记忆 |
| --- | --- | --- |
| 你告诉 AI「我用 VS Code、偏好 Python」 | 下次对话又问一遍 | AI 从记忆库读到，直接记住 |
| WorkBuddy 修了一个 bug | Codex 不知道，可能重复排查 | Codex 一查记忆库就明白 |
| 项目架构决策 | 上下文一清就丢 | 永久留在项目页，跨对话保持 |
| 多 AI 协作 | 各自为政，信息割裂 | 共享同一份知识库 |

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

## 三个仓的关系

本产品拆成三仓，各自独立发版。你当前在 **ai-memory-template**。

| 仓 | 职责 | 何时来这个仓 |
| --- | --- | --- |
| **ai-memory-template**（本仓） | MCP 服务端本体、安装器、`VERSION` | 装服务端、调配置、查工具文档 |
| [ai-memory-web](https://github.com/dpkg-s/ai-memory-web) | Web 控制台（前端 + Starlette 后端） | 想在浏览器里看记忆库 |
| [ai-memory-docker](https://github.com/dpkg-s/ai-memory-docker) | 两个镜像与一栈式编排 | 想一条 `docker compose up` 跑全套 |

```
ai-memory-docker
  └── template/ (submodule) ── dpkg-s/ai-memory-template   ← 本仓
        └── web-console/ (submodule) ── dpkg-s/ai-memory-web
```

## 版本对照

三仓版本号**不同轨**，靠下表对照，不靠数字大小推断兼容性。

| 组件 | 当前版本 | 兼容说明 |
| --- | --- | --- |
| ai-memory-template | **3.1.2** | 只依赖 MCP 协议；任意 web 1.x / docker 1.x 均可连 |
| ai-memory-web | **1.11.3** | 需 MCP 端点可达；不 import 核心仓任何模块 |
| ai-memory-docker | **1.1.1** | `template` 与 `web-console` 指针各随上游 tag |

版本号规则（三仓一致）：major 位不动，**只叠加** —— 修 bug / 文档 / CI 记 `+0.0.1`，用户可见的新能力记 `+0.1`。
判断口径与语义化版本的差异见 [CHANGELOG.md](CHANGELOG.md) 顶部「版本号规则」。

发版顺序是硬约束：**本仓先 → ai-memory-web 后 → ai-memory-docker 跟随**，因为 web 的 Release 要从本仓
latest 转挂同一个安装器 exe。

> 历史版本 `2.1.0` 与 `1.0.0` / `1.1.0` **未单独发布**，只在 [CHANGELOG.md](CHANGELOG.md) 里留档。
> 不补发：GitHub 不允许修改 `published_at`，补发旧版会让 Releases 页面出现「旧版本比新版本更晚」的倒挂。

## 它能做什么

- **25 个 MCP 工具**：写入 / 读取 / 搜索 / 图谱 / 上下文组装 / 归档 / 批量维护 / 审计 / 热重启，
  含乐观锁、可信度、作用域与生命周期字段。完整签名见 **[docs/tools.md](docs/tools.md)**。
- **纯文件存储**：Markdown + frontmatter 是唯一事实源，SQLite 索引只是可删可重建的缓存。
- **零外部依赖**：Python 3.10+ 与 `mcp` 一个包，不需要数据库、容器或云账号。
- **可观测**：`memory_stats` / `memory_doctor` / `memory_schema_diff` 分别查健康度、索引一致性、结构偏差。
- **跨设备**：vault 就是一个目录，git 或任意同步盘即可多机共用（见 [docs/obsidian-and-sync.md](docs/obsidian-and-sync.md)）。

更完整的能力清单与设计取舍见 **[docs/features.md](docs/features.md)**。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/install.md](docs/install.md) | 三种安装方式、客户端配置、`mcp<2` 的版本上界原因、验证步骤 |
| [docs/tools.md](docs/tools.md) | 25 个工具的分类速查与签名 |
| [docs/configuration.md](docs/configuration.md) | `memory.toml` 全部选项、tier 与归档策略、JSON 输出 |
| [docs/obsidian-and-sync.md](docs/obsidian-and-sync.md) | Obsidian 图谱、双链约定、多设备同步与版本控制 |
| [docs/architecture.md](docs/architecture.md) | 分层结构、模块职责、测试体系与 CI |
| [docs/faq.md](docs/faq.md) | 常见问题与排错 |
| [CHANGELOG.md](CHANGELOG.md) | 逐版本变更 |
| [Releases](https://github.com/dpkg-s/ai-memory-template/releases) | 下载安装器与校验和 |

## 许可

[MIT](LICENSE)
