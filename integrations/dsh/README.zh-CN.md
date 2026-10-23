# DSH 共享记忆库

将 DeepSeek Harness 连接到本机 Markdown 记忆库。该服务也可供 Codex、Claude、WorkBuddy 等 MCP 客户端共用同一个目录。

## 环境要求

- DeepSeek Harness `0.2.0-rc.2` 或更新的 `0.2.x` 版本。
- 安装 `uv`，并确保 `uvx` 可从 DSH 进程的 `PATH` 找到。
- 首次启动需联网，以便 `uvx` 从 PyPI 获取固定版本的 `ai-memory-mcp-dpkg-s`。

## 安装

```sh
dsh plugin --profile web add dsh-ai-memory-vault
```

将 `web` 替换为你实际使用的 profile。DSH Desktop 用户可在应用的 Plugins 页面安装插件。

如果工具没有出现，重启对应的 DSH profile。工具名称类似 `mcp__ai-memory__memory_search`。

## 选择记忆库

默认目录为 `~/ai-memory`。如需连接现有目录，在启动 DSH 前设置 `AI_MEMORY_DIR`。例如 PowerShell：

```powershell
$env:AI_MEMORY_DIR = 'D:\Notes\ai-memory'
```

从桌面应用启动 DSH 时，可在 Windows 用户环境变量中设置后重启应用。不要把密钥放进该变量。

插件不会打包记忆库、用户配置或笔记内容。MCP 服务在本机通过 stdio 运行，不会把记忆库上传到托管服务。

**本插件会开放服务端的完整工具集**，包括新增、编辑、归档、恢复和删除笔记的工具。请只连接你愿意让 DSH 访问的记忆库，并为重要笔记保留备份。

## 更新和卸载

使用安装时相同的 DSH profile/plugin manager 更新或卸载 `dsh-ai-memory-vault`。卸载只断开服务，不会删除磁盘上的 Markdown 记忆库。

## 故障排查

- 没有看到 MCP 工具时，确认 DSH 启动环境中 `uvx --version` 可用，并检查 DSH 启动日志。
- 离线无法首次启动时，可先运行 `uv tool install ai-memory-mcp-dpkg-s==0.1.0`，然后重启 DSH。
- 确认插件安装在当前启动的 profile 中。

## 发布信息

DSH 插件包名为 `dsh-ai-memory-vault`，MCP 服务 PyPI 包名为 `ai-memory-mcp-dpkg-s`，命令为 `ai-memory-mcp`。DSH 插件目录投稿条目见同目录 `awesome-dsh-plugin.yml`；提交时应复制到目录仓库的 `data/plugins/dpkg-s__ai-memory-template--integrations-dsh.yml`。

许可证：MIT，见仓库根目录 `LICENSE`。
