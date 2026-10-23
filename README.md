# ai-memory-template

**让不同 AI 工具共享同一份长期记忆。**

一个通过 [MCP](https://modelcontextprotocol.io/) 接入 AI 客户端的本地记忆库。内容以 Markdown 文件保存，可用 Obsidian 浏览；支持 WorkBuddy、Codex、Claude Desktop、Cursor 等 MCP 客户端，无需数据库或云服务。

[![Release](https://img.shields.io/github/v/release/dpkg-s/ai-memory-template?label=release&color=blue)](https://github.com/dpkg-s/ai-memory-template/releases/latest)
[![CI](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml/badge.svg)](https://github.com/dpkg-s/ai-memory-template/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.30--1.x-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 快速安装

### 直接启动 MCP Server（uv / PyPI）

发布后，安装 [uv](https://docs.astral.sh/uv/) 即可直接运行，无需克隆仓库：

```bash
uvx --from ai-memory-mcp-dpkg-s ai-memory-mcp
```

默认记忆库目录为 `~/ai-memory`。连接已有目录时传入 `--vault`，例如 `uvx --from ai-memory-mcp-dpkg-s ai-memory-mcp --vault "D:/Notes/ai-memory"`。也可通过 `AI_MEMORY_DIR` 环境变量设置路径。

DSH 用户可安装配套的 [DSH bundle](integrations/dsh/README.md)，它会用本机 stdio 启动同一 MCP Server。

需要 Python 3.10 或更高版本。运行安装脚本前，建议先阅读脚本内容。

```bash
# macOS / Linux
curl -fsSLO https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh
less install.sh
bash install.sh
```

```powershell
# Windows PowerShell
Invoke-WebRequest -Uri https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 -OutFile install.ps1
Get-Content -LiteralPath .\install.ps1 -Raw
.\install.ps1
```

Windows 也可使用图形安装器：从[最新 Release](https://github.com/dpkg-s/ai-memory-template/releases/latest)下载 `ai-memory-setup.exe`。不同版本提供的校验附件可能不同，请以对应 Release 页面为准；如同时提供 `ai-memory-setup.exe.sha256`，可在 macOS / Linux 使用 `sha256sum -c ai-memory-setup.exe.sha256` 校验。

安装器会安装 `mcp`（`mcp>=1.30,<2`）、创建 `~/ai-memory/` 并写入模板，以及复制 `server.py` 和所需模块。不修改 shell 配置或注册表，不需要管理员权限，也不会在后台常驻。

> 四种安装路径的说明（含 AI 助手部署、各平台 MCP 客户端配置与验证步骤）见
> **[docs/install.md](docs/install.md)**。

---

## 为什么需要它

多个 AI 客户端通常各自维护对话上下文，换一个工具，之前的项目背景和处理记录就不一定跟得上：

- 同一个偏好，需要在不同客户端重复说明
- 一个客户端刚排查过的问题，另一个客户端可能从头再查
- 对话结束后，项目决策和关键背景容易散落或遗失

**ai-memory-template** 将这些客户端连接到同一个记忆库。只要它们配置为访问同一份库，一个客户端写入的内容就能被其他客户端读取。

| 场景 | 没有共享记忆 | 有共享记忆 |
| --- | --- | --- |
| 个人偏好 | 在每个客户端重复说明 | 从共享记忆库读取 |
| 问题排查 | 其他客户端可能重复调查 | 复用已有的原因与处理记录 |
| 项目决策 | 随对话上下文一起丢失 | 作为 Markdown 笔记持续保存 |
| 多客户端协作 | 信息分散在各自的上下文里 | 访问同一份知识库 |

## 使用示例

在一个客户端记录问题处理过程，再从另一个客户端查阅：

**WorkBuddy**

> 帮我修一下爬虫的超时问题，并把原因和处理方式记下来。
>
> 已将问题记录到记忆库，并关联到对应的数据采集项目。

**之后在 Codex 中**

> 爬虫的超时问题之前是怎么处理的？
>
> 记忆库里记录的根因是连接池未复用；改用 `requests.Session` 并设置 `timeout=(3, 10)` 后恢复正常。

前提是两个客户端都已连接到同一记忆库，并且按配置读取相关记录。

## 三个仓的关系

项目由三个独立仓库组成，按需选择即可，不必全部安装。

| 仓 | 职责 | 适用场景 |
| --- | --- | --- |
| **ai-memory-template**（本仓） | MCP 服务端与安装器 | 安装服务端、配置客户端、使用工具 |
| [ai-memory-web](https://github.com/dpkg-s/ai-memory-web) | Web 控制台 | 在浏览器中查看和管理记忆库 |
| [ai-memory-docker](https://github.com/dpkg-s/ai-memory-docker) | Docker 镜像与 Compose 编排 | 通过 Docker 部署 MCP 和 Web 服务 |

## 版本对照

三个仓库独立发布，版本号各自演进；不要根据版本号大小推断兼容关系。

| 组件 | 当前版本 | 说明 |
| --- | --- | --- |
| ai-memory-template | [![v](https://img.shields.io/github/v/release/dpkg-s/ai-memory-template?label=&color=blue)](https://github.com/dpkg-s/ai-memory-template/releases/latest) | MCP 服务端与安装器 |
| ai-memory-web | [![v](https://img.shields.io/github/v/release/dpkg-s/ai-memory-web?label=&color=blue)](https://github.com/dpkg-s/ai-memory-web/releases/latest) | 连接 MCP 端点的浏览器控制台 |
| ai-memory-docker | [![v](https://img.shields.io/github/v/release/dpkg-s/ai-memory-docker?label=&color=blue)](https://github.com/dpkg-s/ai-memory-docker/releases/latest) | 提供 MCP 与 Web 服务的 Docker 部署方案 |

## 它能做什么

- **完整的记忆操作**：写入、读取、搜索、图谱、上下文组装、归档、批量维护与审计；支持乐观锁、可信度、作用域和生命周期字段。工具列表见 **[docs/tools.md](docs/tools.md)**。
- **Markdown 为数据源**：笔记以 Markdown 和 frontmatter 保存；SQLite 仅作可重建索引。
- **轻量运行**：需要 Python 3.10+ 和 `mcp` 包；无需数据库、容器或云账号。
- **状态可检查**：`memory_stats`、`memory_doctor`、`memory_schema_diff` 分别检查库状态、索引一致性和条目结构。
- **支持同步与版本控制**：记忆库是普通目录，可使用 Git 等方案在多设备间管理；配置前请阅读[同步说明](docs/obsidian-and-sync.md)。

更完整的能力清单与设计取舍见 **[docs/features.md](docs/features.md)**。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/install.md](docs/install.md) | 安装方式、客户端配置与验证步骤 |
| [integrations/dsh/README.md](integrations/dsh/README.md) | DSH 插件安装、数据目录与市场收录 |
| [docs/tools.md](docs/tools.md) | MCP 工具分类与调用签名 |
| [docs/configuration.md](docs/configuration.md) | `memory.toml` 选项、分层与归档策略 |
| [docs/obsidian-and-sync.md](docs/obsidian-and-sync.md) | Obsidian、双链、多设备同步与版本控制 |
| [docs/architecture.md](docs/architecture.md) | 模块职责与测试体系 |
| [docs/faq.md](docs/faq.md) | 常见问题与排错 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发、测试与发布流程 |
| [CHANGELOG.md](CHANGELOG.md) | 各版本变更记录 |
| [Releases](https://github.com/dpkg-s/ai-memory-template/releases) | 安装器及各版本提供的附件 |

## 许可

[MIT](LICENSE)
