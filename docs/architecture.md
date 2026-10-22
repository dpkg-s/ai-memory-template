> 本页由仓库主页拆分而来，内容与拆分前逐字一致。返回 [主页](../README.md)。

# 技术栈与架构

| 层级 | 技术 | 说明 |
|------|------|------|
| 协议 | **MCP (Model Context Protocol)** | 开放标准，stdio 传输，JSON-RPC 2.0 |
| 框架 | **FastMCP (Python SDK)** | MCP 服务端框架，自动处理协议层 |
| 存储 | **Markdown + YAML Frontmatter** | 一条记忆一个 `.md`，元数据存 frontmatter（Obsidian 原生识别，兼容旧 JSON） |
| 索引 | **SQLite（内置 sqlite3，运行时缓存）** | 镜像 frontmatter 用于标题 O(1) 定位；可随时删除重建，故障自动回退全库扫描 |
| 缓存 | **Python dict（内存）** | 条目缓存 + 访问计数缓存，写入时失效 |
| 可观测性 | **JSON 快照（内置 json，运行时缓存）** | 检索/读写/锁/索引四类指标的进程内累积 + 定期合并落盘；不加跨进程锁，允许少量丢失 |
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
        ▼  MCP 协议 · stdio（默认）· JSON-RPC 2.0
  server.py —— 24 个 MCP 工具
  ↑ 可选远程入口：serve_http.py → transport_http.py（同一份 server，不改主程序）
  ├─ 读写：write / read / delete / update_metadata / recent
  ├─ 查询：search / smart_search / list / graph / context / orphans / stats
  ├─ 维护：archive / archive_old / restore / batch_tag / batch_tier / heat_suggest / restart
  ├─ 审计：audit / doctor / schema_diff / index_draft / rebuild_links
  ├─ 无状态工具层（可独立测试，分层由 tests/test_import_layers.py 守卫）
  │    yaml_io.py · text_utils.py · locks.py · memory_index.py · memory_config.py
  └─ 基础设施：条目缓存 · 访问计数 · SQLite 索引 · 运行时指标 · 自动标签 · 自动归档 · 阈值热重载
        │
        ▼
  存储层  ~/ai-memory/（纯 Markdown，也可直接用 Obsidian 打开）
  ├─ 用户画像.md · 近期工作动态.md · 项目_xxx.md · 记忆索引.md（生成物）
  ├─ <分类子目录>/（任意层级，上限 3 级）
  ├─ memory.toml（可选：阈值覆盖，热重载）
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
├── server.py                # MCP 服务端：25 个工具注册 + 存储层 + 业务逻辑（stdio 入口）
├── serve_http.py            # 可选：Streamable HTTP 入口（主程序零改动的 HTTP 装配）
├── transport_http.py        # HTTP 传输层：环境变量解析 + Host 校验 fail-fast（只依赖 mcp）
├── web-console/             # 🔗 submodule → ai-memory-web：Web 控制台（前端 + 后端一体）
├── memory_index.py          # SQLite 元数据索引（标题 O(1) 定位，增量重解析）
├── memory_config.py         # 运行时阈值（vault 根 memory.toml + 环境变量 + 热重载）
├── metrics.py               # 运行时指标（检索/读写/锁/索引；仅标准库）
├── yaml_io.py               # YAML frontmatter 解析 / 序列化（无状态）
├── text_utils.py            # 文件名 / 链接 / 时间 / 热度工具（无状态）
├── locks.py                 # 跨进程文件锁（可重入 + 抗陈旧）
│
├── install.sh               # macOS / Linux 一键安装
├── install.ps1              # Windows 一键安装
│
├── ai_memory_setup.py       # 记忆库管家：安装 / 更新 / 版本核验 / 自检 + 四分区交互菜单（v1.4）
├── setup_vault.py           # 记忆库目录管理：扫描候选 / 打分 / 指定当前 / 从零建库
├── setup_link.py            # AI 工具连接：21 个客户端的发现 / 判定 / 写入 / 回滚
├── ai_memory_gui.py         # 管家图形界面（Tkinter 五步引导：库目录 → 程序本体 → 附加组件 → 确认安装 → 接入 AI 工具；逻辑仍走 ai_memory_setup.py）
├── build_exe.py             # 打包两个 exe（GUI / CLI），补 PyInstaller 漏收的 tcl/tk DLL
├── requirements.txt         # 运行时依赖（mcp）
├── requirements-dev.txt     # 开发依赖（pytest + ruff）
├── ruff.toml                # lint 配置
├── .gitattributes           # 强制 LF 换行，避免 CRLF 污染
│                            # （容器化定义 Dockerfile / compose.yaml / .dockerignore
│                            #   已拆到独立仓 dpkg-s/ai-memory-docker，本仓不再持有）
│
├── tests/
│   ├── test_roundtrip.py         # 29 断言：语义回归
│   ├── test_tools_smoke.py       # 32 断言：全工具冒烟 + 注册完整性
│   ├── test_p0_credentials.py    # 24 断言：记忆可信度字段
│   ├── test_scope_lifecycle.py   # 70 断言：作用域 / 生命周期 / 冲突字段
│   ├── test_concurrency.py       # 38 断言：跨进程并发（乐观锁 / 混合操作 / 读写并发）
│   ├── concurrency_worker.py     # 并发测试的子进程工人（非测试脚本，不被 CI 执行）
│   ├── test_recovery.py          # 36 断言：索引损坏 / 锁残留 / 进程强杀 / 畸形文件
│   ├── test_search_quality.py    # 12 断言：检索质量护栏（recall@10 / MRR / 零结果率 / 长句兜底）
│   ├── test_index_links.py       # 22 断言：索引链接可解析性（防自动生成死链）
│   ├── test_trash.py             # 34 断言：回收站闭环（列出 / 取回 / 清空 / 同名拒绝）
│   ├── test_dup_guard.py         # 15 断言：冲突重复防护（标题 / 正文 / 核心页 / 容错）
│   ├── test_heat_apply.py        # 29 断言：热度自动升降级（判据 / 预览 / 执行 / 字段不丢）
│   ├── test_metrics.py           # 45 断言：运行时可观测性
│   ├── test_frontmatter_norm.py  # 49 断言：frontmatter 归一化（双 frontmatter / 摘要污染 / 自愈 / 多行标量转义）
│   ├── test_restart.py           # 26 断言：原地热重启（预检失败即放弃 / 接力标记 / 上限 5 次 / pid 变化）
│   ├── test_context.py           # 59 断言：上下文组装（深度边界 / 出链反链 / 预算分摊 / timeframe / 生成页排除）
│   ├── test_folders.py           # 43 断言：多级目录枚举（递归 / 剪枝 / 排序 / 指纹键）
│   ├── test_title_path.py        # 42 断言：子目录写入（逐段消毒 / 政策拒绝 / 旧口径兜底 / 叶子名）
│   ├── test_dir_policy.py        # 82 断言：写入端归类策略（四档 / 只作用于新建 / title 不夹带目录）
│   ├── test_index_cache.py       # 17 断言：索引增量化（改 1 个文件只新增 1 次解析）
│   ├── test_permalink.py         # 25 断言：稳定标识 permalink（冻结 / 查找链 / 改名不失联）
│   ├── test_schema_diff.py       # 32 断言：结构对账（三层键 / 无 frontmatter 单列 / 未知键行号）
│   ├── test_memory_config.py     # 79 断言：阈值配置（默认值一致 / 子集拒绝 / 优先级 / 热重载 / 坏配置）
│   ├── test_index_name.py        # 47 断言：索引文件名与迁移（可见名 / 反向迁移 / 人侧可点击 / 摘要行）
│   ├── test_output_json.py       # 76 断言：JSON 输出（范围即契约 / text 逐字节不变 / 非法值报错）
│   ├── test_import_layers.py     # 47 断言：模块分层守卫（叶子不反向依赖 / 无环 / 白名单不腐烂 / 装配层依赖方向）
│   ├── test_transport.py         # 43 断言：HTTP 传输层（模式归一 / 安全规则 / 装配 / 端到端 / 零改动保证）
│   ├── selftest_vault_link.py    # 86 断言：管家自检（TOML 段级写入 / 状态判定 / 路径分级 / 片段结构 / 菜单冒烟）
│   └── selftest_setup.py         # 147 断言：安装器自检（版本核验 / 四态证据 / 远端降级 / HTTP 与 Web 随装）
│
├── scripts/
│   └── make_checksums.py    # 生成 SHA256SUMS.txt（打 tag 发布时由 release.yml 调用）
├── .github/workflows/
│   ├── ci.yml               # CI：Python 矩阵测试 + ruff lint
│   └── release.yml          # 打 tag 时先跑全量测试，再发布 Release 并附 SHA256
│
├── setup/                   # 各客户端 MCP 配置模板
│   ├── workbuddy.json
│   ├── claude.json
│   └── codex.toml
├── template/                # 初始记忆文件（开箱即用）
│   ├── 用户画像.md
│   ├── 近期工作动态.md
│   ├── 记忆索引.md           # 开箱索引占位（生成物；首次写入后自动重建）
│   └── 记忆写入格式规范.md
└── rules/                   # AI 写入时遵循的规范
    ├── 记忆写入格式规范.md
    ├── 记忆库结构规范.md
    └── Git版本控制规范.md
```

---

## 测试与 CI

测试分四类：**正确性**（写读是否无损）、**覆盖面**（工具是否都能用）、**语义与质量**（字段标记是否可靠、检索是否退化）、**韧性**（并发、崩溃、损坏文件下是否还站得住）。共 **28 套 / 1325 条断言**（另有 Web 控制台一套已随控制台迁至独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)）。其中 26 套是 `tests/test_*.py` 的 pytest 风格回归；另 2 套是**脚本式**自检（退出码 0 = 全绿，CI 单独一步执行）：`tests/selftest_vault_link.py`（管家）与 `tests/selftest_setup.py`（安装器）—— 后者覆盖版本核验与 HTTP、Web 两套随装脚本，在 CI 无外网环境下靠打桩验证远端列举逻辑，不依赖真网络；前者在 Python 3.10 上会把依赖 `tomllib` 的解析级断言记为 `skip` 而非 `fail`。

**每条新判据都要先证明它「会红」**：新增断言时会把产品改回旧行为跑一遍，若断言不变红就说明它证明不了任何东西。本批的证伪结果：阈值配置 8/8、索引命名 **23/47**、多行标量转义 **6/8**、JSON 输出 7/7、模块分层 8/8 根针全部生效。

### 语义回归 —— `tests/test_roundtrip.py`（28 断言）

- frontmatter 写 → 读无损 round-trip（中文标题 / 多行正文 / tags 特殊字符 / 类型保持）
- Windows 路径反斜杠不雪崩（连续 5 轮 update 回归）
- `memory_read` 不污染 `updated`（P0-1 修复的回归保护）
- `max_chars` 截断语义
- 文件名非法字符安全处理
- SQLite 索引层（写入命中 / read 走索引 / miss 回退 / 外部改动自愈 / delete 清理）

### 全工具冒烟 —— `tests/test_tools_smoke.py`（32 断言）

- 24 个 MCP 工具逐一调用，验证不抛异常且返回正常
- 工具注册完整性：数量为 20 且集合与预期完全一致（防重构时漏注册或误覆盖）

### 记忆可信度 —— `tests/test_p0_credentials.py`（24 断言）

- 写入落盘：显式取值正确写入，缺省时回落 `fact` / `medium` / `false`
- 更新语义：未传字段继承原值（不被重置），显式传入时覆盖生效
- 非法取值被拒绝且不落盘
- 向后兼容：无字段的历史笔记读取时注入默认值、被写入时渐进补齐
- `verified_at` 生命周期：置 `true` 写入日期，置 `false` 清除日期
- 检索过滤（`mem_type`）与统计分布输出
- 序列化层边界：非法值防御性兜底、`false` 不被序列化为 `null`

### 作用域与生命周期 —— `tests/test_scope_lifecycle.py`（70 断言）

- 字段默认值与写入落盘、`scope` / `status` 非法取值拒绝
- 更新时继承磁盘原值（只改正文不丢语义字段），`memory_update_metadata` 可修正与清空
- `AI_MEMORY_SOURCE_CONTEXT` 环境变量回落
- 检索默认排除 `archived`（`stale` / `deprecated` 保留）、`scope` / `project` 过滤
- 归档 / 恢复与 `status` 联动；`memory_stats` / `memory_audit` 新章节
- 旧笔记（无新字段）向后兼容

### 跨进程并发 —— `tests/test_concurrency.py`（38 断言）

用**真实子进程**（`tests/concurrency_worker.py`）而非线程，才能复现多个 AI 客户端同写一个库时的进程级锁与缓存隔离语义：

- 乐观锁竞争：10 进程同 `expected_version` 写同标题 → 恰好 1 成功 9 冲突
- 无冲突并发：10 进程写 10 个标题互不干扰
- 混合操作：写入 / 归档 / 删除交错
- 读写并发：300 次读循环 vs 3 个写进程
- 收尾对账：无锁文件残留、索引与磁盘逐行核对无差异、无 `.tmp` 残留

### 异常恢复 —— `tests/test_recovery.py`（35 断言）

- 索引被删除 → 自动重建；索引被写入垃圾字节 → 自愈
- 锁文件残留 / 内容损坏 → 陈旧检测回收
- 写入窗口内强杀进程 → 库无损坏
- 外部直接改写正文 → 以磁盘为准；外部增删文件（如 `git` 切版本）→ 以磁盘为准
- 七类畸形 Markdown（0 字节 / 无 frontmatter / 半截 `---` / 非法 YAML 等）→ 10 个工具全部降级不崩
- 重复重建幂等

### 检索质量 —— `tests/test_search_quality.py`（12 断言）

15 篇 fixture（中文 / 英文 / 代码标识符 / 专有名词）× 20 条查询 + 3 条负样本，以 **recall@10 ≥ 0.95、MRR ≥ 0.80、零结果率为 0、负样本必须全空** 作为护栏。检索相关改动必须过这道闸。

后半段（H 组 5 断言）专测 `memory_search` 的**长句兜底**：长句召回 ≥ 0.80（改造前恒为 0.00）、短关键词零回归、精确命中不得被误标为模糊、负样本不得被兜底误召回。

### 索引链接可解析性 —— `tests/test_index_links.py`（22 断言）

`记忆索引.md` 由 `_refresh_index()` 自动重建。Obsidian 解析双链以**文件名 stem** 为准（`permalink` 是服务端侧的稳定标识，不在渲染口径里），而 `safe_filename()` 会把标题里的空格等字符归一化成下划线 —— 若生成器直接输出 `[[title]]`，凡标题含空格的笔记在索引里全是渲染死链，且每次写入都会把人工修复覆盖回去。

判定口径是一句话：**索引与索引草稿里的每一条链接，其 target 都必须能解析到库中真实存在的文件**。覆盖：

- 标题==stem 时输出裸链接、标题!=stem 时 target 用 stem 且标题进别名位
- 标题含空格（原缺陷场景）与含 `/ : * ?` 等归一化字符的标题均须可解析
- 连续三次 `_refresh_index()` 后链接集合不变（幂等，防覆盖）
- `memory_index_draft` 走同一口径；索引条目数不缺不漏

### 回收站闭环 —— `tests/test_trash.py`（34 断言）

`memory_delete` 默认软删到 `.trash/`，但「可恢复」要真的能恢复才算数。覆盖：

- 软删除后条目立即退出检索与列表，且**仍可被 `include_trash=True` 列出**
- 从回收站取回后：文件回到根目录、字段（tier / status / schema_version）正确、索引重新可见
- **根目录已有同名条目时拒绝覆盖**（回收站里那份可能是被新版取代的旧内容，静默覆盖比不恢复更危险）
- 回收站内同名两条并存时不误取
- `purge=True` 永久删除、`empty_trash=True` 清空回收站
- 既有 `.archive/` 恢复路径零回归

### 冲突 / 重复防护 —— `tests/test_dup_guard.py`（15 断言）

- 标题相近（≥ 0.6）判「疑似重复标题」；标题不相似但正文相近（≥ 0.5）判「疑似重复/冲突内容」
- **无关内容不误报**（假阳性会迅速让人无视提示）
- `upsert` 同一标题不把自己当成重复项
- **核心页变体同样触发**（「新建『近期工作动态记录』而不是更新原页」正是最高发的重复写入）
- 检测内部异常时静默跳过，写入本身照常成功

### 热度自动升降级 —— `tests/test_heat_apply.py`（29 断言）

`memory_heat_suggest(apply=True)` 会把预览里的建议**真正写盘**。整组测试围绕「预览与执行绝不能漂移」展开：

- 判据纯函数 `_heat_tier_decision` 逐档取值：冷 → `cold`、hot 且未达阈值 → `warm`、过热 → `hot`、其余维持现状、**核心页一律豁免**
- **预览不改盘**：`apply=False` 前后全库文件的哈希与 mtime 不变
- **执行写盘且所见即所改**：预览列出的每一条，执行后 tier 与之一致
- **幂等**：连续两次 `apply=True`，第二次无任何改动
- **v2 字段不丢**：批量改 tier 不会清掉 `scope` / `status` / `confidence` / `supersedes` 等字段
- 空库边界不崩

### frontmatter 归一化 —— `tests/test_frontmatter_norm.py`（41 断言）

写入端会自行生成权威 frontmatter，而调用方（其他 AI 客户端、手工整篇粘贴、跨工具导入）**常把「带 frontmatter 的完整笔记」直接当作 `content` 传入**。两者直接拼接会产出「双 frontmatter」：Obsidian 只认第一块，第二块退化为正文里的深层垃圾；更隐蔽的是**自动摘要取自未剥离的原始 `content`**，会把那段 frontmatter 文本固化进 `summary` 字段，持续污染检索结果与索引快照。

修复分两道纵深：`yaml_io.strip_leading_frontmatter()` 纯函数负责剥离，`memory_write()` 入口调用它（**早于空壳校验与摘要/标签派生**），`_write_memory()` 收口再剥一次（覆盖其余写盘路径）：

- 传入单块 / 连续多块 frontmatter → 落盘仍只有 1 块，正文干净
- **摘要污染回归**：`summary` 不得含 `---` / `title:` 等传入块文本
- **权威性**：服务器生成的 `title` / `tags` 为准，传入块不夺权
- **更新路径**（历史重灾区）同样只留 1 块，版本号正常自增
- **自愈**：磁盘上已被写坏的条目，再次写入即恢复
- **误伤防护**：正文开头恰为 `---` 水平线 + 非 `key:` 文本时**不剥**
- 幂等（连写两次不堆叠）、纯函数边界（空串 / 无 frontmatter / 空块 / BOM）

### 运行时可观测性 —— `tests/test_metrics.py`（45 断言）

- 计数 / 耗时累积、跨实例 read-merge-write 合并、达到阈值自动落盘
- 落盘失败静默且增量不丢；`path=None` 时全操作安全
- 各工具确实打点（写入新建 vs 覆盖、读命中 vs 未命中、检索零结果、锁获取）
- 指标摘要确实出现在 `memory_stats` / `memory_audit`
- `AI_MEMORY_METRICS=0` 可关闭（子进程验证）
- **容错**：指标设施自身崩塌时，检索与统计仍然正常返回

### 原地热重启 —— `tests/test_restart.py`（26 断言）

- **预检失败即放弃**：新代码导入不通过、或会话恢复补丁装不上时**不换进程**，原进程继续服务
- **接力标记**：新进程能识别自己由重启而来，不因客户端未重发 `initialize` 而拒掉请求
- 接力链上限 5 次防重启风暴，到顶后给出可操作的复位指引
- 换进程后 **pid 变化**（Windows 上 `execv` 内部即 `CreateProcess`），旧 pid 不再响应
- 响应先经管道发出、`execv` 在延迟线程执行 ⇒ 调用方拿到返回值，而非连接被掐断
- 环境变量传递走 `os.environ` 赋值而非 `os.execve`（本机实测 1/3 概率崩溃的回归护栏）

### 上下文组装 —— `tests/test_context.py`（59 断言）

- 深度边界：`depth=0` 只要起点 / `1` 只取直接邻居 / `2` 展开两层 / 超过图深度不报错
- **出链与反链都纳入**（只取出链会漏掉「谁引用了我」）
- 排序权重：`tier(hot > warm > cold)` 优先，其次 `access_count`，最后按 `updated`
- `max_related` 截断，且**起点不占用周边配额**
- `timeframe` 只过滤周边、起点恒在；无法识别的表达式按不限处理并在输出里提示；`_parse_timeframe` 有单元级覆盖
- **预算分摊回归**：起点很长时周边仍须分到配额（缺陷形态是长起点独吞整段预算，周边一篇都展不开）
- **生成页排除**：`记忆索引.md` 正文即全库标题列表，必须排除出周边，否则会冒充「头号邻居」
- 截断回退到行边界，不把 `_关系 · 深度 · …_` 标注行切成半截
- 标题解析：不存在时报错清晰、按文件名 stem 命中、反斜杠路径归一化

### 多级目录枚举 —— `tests/test_folders.py`（43 断言）

- 递归枚举与剪枝：`RESERVED_DIRS` 内一律不计，深度与排序确定（同一库两次枚举同序）
- 子目录条目的可见性：`memory_list` / `memory_search` / `memory_stats` 都能看到它们
- **指纹键必须是 relpath 而非文件名**（跨目录同名会撞键 ⇒ 变更不触发缓存失效、静默返回陈旧数据）
- 历史扁平库与目录化库的枚举结果对账

### 子目录写入 —— `tests/test_title_path.py`（42 断言）

- 逐段消毒：非法字符 / 控制字符 / 空段 / 首尾 `._` / 段长上限
- 政策层拒绝：保留目录、`.` / `..`、盘符、绝对路径、层级 > 3（**拒绝而不是消毒后放行**）
- 旧口径兜底：`legacy_flat_filename()` 对历史标题的幂等匹配（含 `a/ b` → `a_b` 的字符类折叠语义）
- **叶子名口径**：frontmatter 的 `title` 恒为裸叶子名，绝不夹带目录前缀

### 写入端归类策略 —— `tests/test_dir_policy.py`（71 断言）

- 四档 `warn` / `auto` / `strict` / `off` 的行为边界
- **只作用于新建**：更新绝不搬运（存量笔记不被搅动）
- **title 不夹带目录**：`auto` 归类后 frontmatter 里仍只有叶子名（防「按 title 比对」全线失配）
- 标题已含 `/` 时完全尊重写入方
- 语义桶映射与「无规范目录可归」时的四档差异

### 索引增量化 —— `tests/test_index_cache.py`（17 断言）

- **核心判据：改 1 个文件只新增 1 次解析**（旧实现是 +全库）。验证手法是把增量分支临时改成假，断言必须变红
- 整库指纹未变时连 `os.walk`/`stat` 都省（回放快照）
- `_refresh_index` 的内容签名短路：正文未变则跳过渲染与写盘，但**文件不存在时必须重建**
- 缓存键为 relpath（同名不同目录不互相覆盖），返回的 meta 是浅拷贝

### permalink 稳定标识 —— `tests/test_permalink.py`（25 断言）

- 首次写入冻结、后续写入**永不覆盖**（`is None` 判据，写成 `or path.stem` 会在改名时悄悄改掉）
- 查找链 `permalink → title → stem`，且索引快路径与全库兜底**两条实现同口径**
- 改名后 `[[旧名]]` 仍能解析；`memory_rebuild_links` 不误报死链
- 索引加列只经 `_migrate()` 的 `ALTER TABLE`（`CREATE INDEX` 放进 SCHEMA 会打断 `executescript`）

### 结构对账 —— `tests/test_schema_diff.py`（32 断言）

- core / v2 / opt 三层键的分类与计数
- **「无 frontmatter」与「核心键缺失」必须分开**：普通 Markdown 不受结构契约约束，单列且不计入缺陷数
- `archived_to` 属已知键（漏掉会把归档 stub 全报成未知键）
- 未知键附行号；可选键使用率统计

### 阈值配置 —— `tests/test_memory_config.py`（79 断言）

- **默认值与改动前的硬编码值逐项相同**（含用 `inspect.signature` 钉住两个默认参数必须是 `None`，防「钉死在 def 期」的退化）
- 解析子集：能接受的写法与**必须报错**的写法（数组 / 内联表 / 多行字符串 / 裸词 / 缺值 / 重复键…），错误须带行号
- 优先级链：环境变量 > `[thresholds]` 区 > 顶层键 > 默认
- 热重载：改文件后无需重启即生效；T6 用「坏配置必须退回默认值而不是沿用旧值」证明失败处理真的走了默认分支
- 四个读取点确实被接上（改配置能改变实际行为）

### 索引文件名与迁移 —— `tests/test_index_name.py`（47 断言）

- 生成物为**可见名** `记忆索引.md`；仍能被枚举 / 检索（可见 ≠ 更好维护，故仍走生成器）
- 一次性迁移**反向**：B6 期间的隐藏名 `.记忆索引.md` 残留被清理；**仅当 `title == 记忆索引` 且 tags 命中 `moc`/`index`/`navigation`** 才删，疑似人工文件跳过并告警
- 迁移同时清掉索引行（否则留死索引）；迁移**先于枚举**执行，避免把即将被删的文件也渲染成一行
- **人侧可点击**：判据落在**文件名 stem** 上（Obsidian 的解析口径），而不是 `memory_rebuild_links` —— 后者认 `title`，在旧版上恒绿，是个证明不了任何东西的假判据
- 索引条目行带 `summary` 摘要：换行压平（否则会把一行劈成两行）、60 字符截断

### JSON 输出 —— `tests/test_output_json.py`（76 断言）

- 四个工具的 JSON 必须可 `json.loads`（本项存在的全部理由）
- **text 形态逐字节不变**：加参数绝不能顺手改了原来的文本
- **范围即契约**：`output_format` 只允许出现在这四个工具上，报告型工具不得有
- 非法取值**报错**且返回的不是 JSON（防「以为拿到 JSON、实际是一句中文」）
- 字段集合精确（多一个键也算破坏性变更）；JSON 与文本**同源**（同一批记录、同样顺序）
- 截断语义：JSON 的 `body` 不含中文截断说明，改由 `truncated` / `total_chars` 承载
- 中文不转义（`ensure_ascii=False`）

### 模块分层守卫 —— `tests/test_import_layers.py`（43 断言）

- 叶子模块不得 import `server`（含函数体内延迟 import 与 `importlib` 动态拉起）
- 本地模块之间**无环**；叶子之间的依赖只能在显式白名单内，且白名单**不得腐烂**
- `server` 必须 import 全部叶子，且运行期**真的加载**（`sys.modules` + 绑定名双重佐证 —— `import memory_index as midx` 要按 `midx` 核）
- 本地 import 必须都在模块顶层；新增模块必须登记进清单（否则从守卫视野里消失）
- **装配/入口层**（`serve_http.py` / `transport_http.py`）依赖方向单向：叶子与聚合器**不得** import 它们，它们也只许 import `server`（装配层若确需直连叶子，必须在白名单里显式登记，且白名单会因陈旧条目被判红）—— 「主程序零改动」不被反向依赖破坏。原 `serve_web` / `web_console` 已随 Web 控制台迁至独立仓，不在本仓的导入图内

### HTTP 传输层 —— `tests/test_transport.py`（43 断言）

- **模式归一**：`stdio` 为默认；`http` / `streamable-http` 等多写法归一；非法值 fail-fast（绝不静默回退 stdio）
- **安全规则**：非回环监听 + 无 Host 白名单 ⇒ 拒绝；关闭 DNS rebinding 防护 ⇒ 拒绝；回环默认与 SDK 语义一致
- **装配（替身 FastMCP）**：stdio 分支调用 `mcp.run()` 且不触碰 settings；http 分支写对 `host/port/path/transport_security`，且**刻意不设 auth**
- **端到端**：真起 `serve_http.py`，客户端走 Streamable HTTP 完成 `initialize` / `list_tools` / `write` / `read`，并验证远程模式下 `memory_restart` 已被自动禁用
- **fail-fast 进程级**：非回环且无白名单时，进程必须非零退出并给出明确提示（而不是静默起一个不校验 Host 的服务）
- **零改动保证**：不带 `AI_MEMORY_TRANSPORT` 时，`server.py` 仍是可用的 stdio 服务且工具数不变

### Web 控制台 —— 已迁至独立仓 [ai-memory-web](https://github.com/dpkg-s/ai-memory-web)（2026-10-16）

原 `tests/test_web.py` 随控制台一并迁出，现由该仓的 `tests/test_web.py` 承载（覆盖配置 fail-fast / 前端资源完整性 / 分层 / 鉴权 / 只读保护 / 端到端 / 工具分组 / 更新面）。本仓不再含控制台的任何代码与测试 —— 它与本仓的全部关系只有**两条**：① 一个 submodule 引用（`web-console/`）；② 运行期一条 MCP 连接。控制台**不 import 本仓任何模块** —— 阈值读写走 MCP 工具 `memory_config`，端点默认地址是本仓外的字面常量；该仓 `tests/test_web.py` 的 C 段机器守卫此事（`sys.modules` 里出现 `server` / `memory_config` / `transport_http` 任一即红）。唯一例外是 `serve_web.py --with-mcp`（要让控制台替你把 MCP 端点作为子进程拉起），此时须显式给 `--core-dir` / `AI_MEMORY_CORE_DIR` 指定本仓目录。

所有脚本都会把 `AI_MEMORY_DIR` 指向临时目录，**绝不触碰真实记忆库**。

> ⚠️ `tests/*.py` 是**独立脚本**（`python tests/test_xxx.py`，退出码 0 = 全绿），不是 pytest 用例。CI 直接遍历执行，故**新增测试无需改 CI**。

```bash
pip install -r requirements-dev.txt
for f in tests/test_*.py; do python "$f"; done   # 或逐个运行
```

CI（`.github/workflows/ci.yml`）在 push / PR 时自动遍历 `tests/test_*.py`，再单独跑一遍 `tests/selftest_*.py`（Python 3.10 / 3.11 / 3.12 / 3.13 矩阵）+ ruff lint。**新增测试无需改 CI** —— 两类文件都按通配匹配，加文件即自动纳入。

> 这二十七层测试是 `server.py` 能够安全模块化重构、并持续演进存储格式的前提 —— 先有测试护航，再动结构。并发与恢复测试上线后立刻暴露了 3 个真实并发缺陷（读路径写盘未持锁、Windows 下 `os.replace` 被读占用、把锁竞争误判为索引损坏），检索质量测试也暴露了 `memory_smart_search` 的过度召回 —— 这正是它们存在的意义。frontmatter 归一化测试则来自一次全库体检：8 篇笔记被同一处写入缺陷写坏，**先复现、再修生成器、最后回填数据**，而不是逐篇手改。

---
