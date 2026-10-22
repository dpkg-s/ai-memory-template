> 本页由仓库主页拆分而来，内容与拆分前逐字一致。返回 [主页](../README.md)。

# 自定义配置

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
| `AI_MEMORY_STATE_DIR` | 索引 db 与指标 json 的落点（容器镜像里指向独立卷 `/state`） | 与 `server.py` 同目录 |
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
