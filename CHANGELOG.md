# Changelog

本项目的所有重要变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.0.0] - 2026-09-10

引入 SQLite 元数据索引层，属结构性变更，故升主版本号。

### Added

- **SQLite 元数据索引层**（`memory_index.py`）：`memory_read` / `memory_list` 的标题定位从 O(n) 全库扫描降为 O(1)。仅使用标准库 `sqlite3`，不引入外部依赖。
  - md 文件仍是唯一事实源：索引只镜像 frontmatter 元数据，正文永不入库
  - 以 relpath 为主键，避免 `.archive/` 副本与根 stub 同标题互相覆盖
  - 通过 (mtime, size) 校验新鲜度，外部直接改文件后自动回退全库扫描并自愈
  - 索引损坏或缺失时静默回退，功能不降级；缓存文件可随时删除重建
- **回归测试**（`tests/test_roundtrip.py`）：28 条断言，覆盖 frontmatter 写读无损、反斜杠不雪崩、`read` 不污染 `updated`、`max_chars` 截断、文件名安全、SQLite 索引层（命中/miss/自愈/清理）
- **全工具冒烟测试**（`tests/test_tools_smoke.py`）：25 条断言，逐个调用 20 个 MCP 工具并校验工具注册完整性
- **CI 工作流**（`.github/workflows/ci.yml`）：Python 3.10 / 3.11 / 3.12 / 3.13 矩阵跑回归测试 + ruff lint
- **依赖声明**：`requirements.txt`（运行时）、`requirements-dev.txt`（开发/测试）
- **工程配置**：`ruff.toml`（lint 规则）、`.gitattributes`（强制 LF 换行）
- **`memory_read` 的 `max_chars` 参数**：默认截断正文至 8000 字符，`max_chars=0` 取全文
- **`memory_graph` 的 `limit` / `include_all` 参数**：默认截断至 10 条，`include_all=true` 取全量

### Changed

- **`server.py` 模块化拆分**：把无状态工具层抽为独立模块 —— `yaml_io.py`（frontmatter 解析/序列化）、`text_utils.py`（文件名/时间/热度工具）、`locks.py`（跨进程文件锁）。`server.py` 从约 1940 行降至约 1600 行，仅保留工具注册、存储层与业务逻辑。对外行为、工具签名与存储格式完全不变（由 28 + 25 条测试护航）
- `MEMORY_DIR` 默认值改为 `~/ai-memory`（保留 `AI_MEMORY_DIR` 环境变量覆盖）
- `memory_write` 不再写入 `links` 字段；`memory_rebuild_links` 改为只读校验报告，不再改写文件
- `memory_read` 只返回核心元数据（title / tags / summary / created / updated / tier / access_count / source）
- 反向链接改以正文扫描为唯一事实源

### Fixed

- **`read` 污染 `updated` 时间戳**：`_flush_access_counts` 不再改写 `updated`，读取操作不再影响 30 天归档语义
- **summary 反斜杠雪崩**：`_parse_scalar` 补充 YAML 双引号反转义，读写对称，含 Windows 路径的摘要不再逐轮翻倍
- `memory_write` 新建 `tier=cold` 条目时补触发自动归档
- `memory_delete(purge=True)` 现在也会搜索 `.trash` 目录
- `memory_archive_old` 复用全局 `CORE_PAGES` 保护名单（此前本地副本缺少 2 个页面）
- `memory_archive` 归档后补充调用 `_invalidate_cache()`
- 7 处扫描循环补充异常保护，单个损坏文件不再中断整轮扫描
- `_TAG_AUTO_MAP` 移除重复的 `docker` → `deploy` 映射
- 清理 5 个配置/脚本文件的 UTF-8 BOM（JSON/TOML 带 BOM 会导致部分解析器报错）
  - 注：`install.ps1` 有意保留 BOM，因 Windows PowerShell 5.1 依赖 BOM 识别 UTF-8 中文

### Removed

- `_rebuild_title_index` / `_KNOWN_TITLES` 死代码链路（自动补链停用后已无消费者）
- `memory_write` / `memory_rebuild_links` 的自动补链行为

### Dependencies

- 锁定 `mcp>=1.27,<2`。注意 mcp 2.x 已将 `FastMCP` 更名为 `MCPServer`，本项目基于 v1 API，不兼容 2.x

## [1.1.0] - 2026-08-26

### Added

- 新增 4 个工具：`memory_recent` / `memory_update_metadata` / `memory_restore` / `memory_rebuild_links`，工具总数达 20 个
- 自动生成 MOC 索引（`记忆索引.md`）
- 健康度增强：空条目 / 死链 / 命名漂移检测
- `read` / `delete` / `archive` / `graph` 支持「标题或文件名」双路匹配

### Changed

- 存储格式从 JSON frontmatter 迁移为 **YAML frontmatter**（Obsidian 原生可读），历史 JSON 格式仍可读
- `memory_write` 补充非空校验与 source 默认值

## [1.0.0] - 2026-07-22

首个公开版本。

### Added

- 16 个 MCP 工具：写入 / 读取 / 搜索 / 删除 / 关系图 / 孤儿检测 / 统计 / 归档 / 批量操作 / 智能搜索等
- 三项自动机制：写入时按关键词建议标签、按已知标题自动补链、冷条目自动归档
- 跨进程文件锁，支持多 AI 工具并发写入
- 内存级 access_count 缓存（批量落盘）与条目缓存（写入时失效）
- 全操作日志输出到 stderr
- `AI_MEMORY_DIR` 环境变量配置存储路径
- 跨平台安装脚本：Windows PowerShell + Linux/macOS bash
- 零外部依赖：仅需 Python 3.10+ 与 `mcp` 包

[Unreleased]: https://github.com/dpkg-s/ai-memory-template/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.0.0
[1.1.0]: https://github.com/dpkg-s/ai-memory-template/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v1.0.0
