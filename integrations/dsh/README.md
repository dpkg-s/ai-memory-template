# DSH AI Memory

[简体中文](README.zh-CN.md)

Connect DeepSeek Harness to an existing local Markdown memory vault through the MCP server. The same vault can be used by other MCP clients such as Codex, Claude, and WorkBuddy.

## Requirements

- DeepSeek Harness `0.2.0-rc.2` or newer in the `0.2.x` line.
- `uv` installed and available as `uvx` on `PATH`.
- Network access on the first start so `uvx` can obtain the pinned `ai-memory-mcp-dpkg-s` release from PyPI.

## Install

For the DSH CLI profile:

```sh
dsh plugin --profile web add dsh-ai-memory-vault
```

Replace `web` with the profile you actually run. On DSH Desktop, install the bundle through the Desktop app's Plugins page so it is added to the profile managed by the app.

After installation, restart the selected DSH profile if it does not hot-reload bundles. The MCP tools appear with names such as `mcp__ai-memory__memory_search`.

## Choose a vault

Without an override, the server uses `~/ai-memory` and creates it if needed. To connect an existing vault, set `AI_MEMORY_DIR` in the environment inherited by DSH before launching it. The bundle passes that path to the server. For example, in PowerShell:

```powershell
$env:AI_MEMORY_DIR = 'D:\Notes\ai-memory'
```

If DSH is launched from the Desktop app, set the variable in the Windows user environment and restart the app. Do not put secrets in this variable.

The package does not include a vault, user profile, or memory data. The server runs locally over stdio; it does not send vault contents to a hosted service.

This connects DSH to the server's full tool set, including tools that create, edit, archive, restore, or delete notes. Point it only at a vault you intend DSH to access, and keep a backup of important notes.

## Update and uninstall

Update the bundle through the same DSH profile/plugin manager used for installation. Remove `dsh-ai-memory-vault` from that profile to disconnect the server. The Markdown vault remains on disk and is not removed by uninstalling the bundle.

## Troubleshooting

- If no MCP tools appear, confirm `uvx --version` works in the environment that launches DSH, then inspect the DSH startup log.
- If the server cannot start while offline, install the Python package first with `uv tool install ai-memory-mcp-dpkg-s==0.1.0`, then restart DSH.
- Confirm the selected DSH profile is the one where the bundle was installed.

## Development and distribution

The DSH bundle is an npm package with a `dsh.bundle` patch layer. The Python MCP server is distributed separately as `ai-memory-mcp-dpkg-s` on PyPI; its executable remains `ai-memory-mcp`.

To list the project in a community directory, add the GitHub topics `dsh-plugin` and `deepseek-harness`. To enter a curated market, submit the repository/package to that market's registry. DSH's experimental Custom plugin markets feature does not include a default index source; users add a trusted index URL in Plugins → Discover.

## License

MIT. See the repository's `LICENSE` file.
