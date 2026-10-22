> 本页由仓库主页拆分而来，内容与拆分前逐字一致。返回 [主页](../README.md)。

# 25 个工具速查

> 带 `output_format` 的四个只读工具（`memory_read` / `memory_list` / `memory_recent` / `memory_orphans`）可传 `output_format="json"` 拿到结构化输出，详见[脚本化调用](#脚本化调用可选-json-输出)。

### 读写

| 工具 | 签名 | 说明 |
|------|------|------|
| `memory_write` | `(title, content, tags?, source?, summary?, tier?, mem_type?, confidence?, verified?, scope?, project?, status?, supersedes?, conflicts?, source_context?, expected_version?)` | 写入或更新。自动标签 + 自动归档；同标题 upsert；支持乐观锁、可信度与作用域/生命周期标记 |
| `memory_read` | `(title, max_chars=None, output_format=None)` | 按标题精确读取（含文件名回退，含 `permalink` 与文件名 stem 回退，可命中 `.archive`）。`max_chars` 省略时取 `memory.toml` 的 `body_truncate_chars`（内置 8000），`0` = 取全文；`output_format="json"` 可拿结构化输出 |
| `memory_delete` | `(title, trash=True, purge=False, empty_trash=False)` | 删除。默认软删到 `.trash/` 可恢复，`purge=True` 永久删除，`empty_trash=True` 清空回收站 |
| `memory_update_metadata` | `(title, tier?, tags?, summary?, source?, mem_type?, confidence?, verified?, scope?, project?, status?, supersedes?, conflicts?, source_context?)` | 只改 frontmatter 元数据（含可信度、作用域、生命周期字段），不重写正文。传空串 / 空列表可清除 |
| `memory_recent` | `(days=7, limit=20, output_format=None)` | 列出近 N 天更新的记忆，最新优先 |

### 查询

| 工具 | 说明 |
|------|------|
| `memory_search(keyword, tag?, limit=20, mem_type?, scope?, project?, status?)` | 关键词搜索标题 / 标签 / 正文，命中处 `**` 高亮；可按标签 / 类型 / 作用域 / 项目过滤。**省略 `status` 时默认隐藏 `archived`**，隐藏条数会在末尾提示。精确零命中时自动降级为中文 bigram 模糊匹配（结果会显式标注） |
| `memory_smart_search(query, tag?, limit=10, mem_type?, scope?, project?, status?)` | 多字段加权搜索：标题 ×10、标签 ×4、摘要 ×3、正文 ×1，另加时效性加权。**零词法命中即非候选**，时效性不能单独决定入选 |
| `memory_list(tag?, limit=20, tier?, mem_type?, scope?, project?, status?, include_trash?, output_format?)` | 罗列记忆摘要，过滤语义同上；`include_trash=True` 改为列出回收站内容 |
| `memory_graph(title, limit=10, include_all=False)` | 显示指定笔记的出链与反向链接图谱，默认截断 10 条 |
| `memory_context(title, depth=2, max_related=10, timeframe?, max_chars?)` | **上下文组装**：沿 `[[wikilink]]` 出链与反链 BFS 到 `depth` 层（`0` = 只要起点），把沿途正文拼成一段可直接喂给模型的 Markdown，每块标注关系 / 深度 / tier / 读取次数 / 更新时间。周边按 `tier → access_count ↓ → updated ↓` 排序取前 `max_related` 条；`timeframe` 支持自然语言（`last week` / `2 days ago` / `3d` / `2026-09-01`）且只过滤周边；`max_chars` 为**整段预算**（在起点与周边间分摊，`0` = 不限） |
| `memory_orphans(output_format?)` | 查找没有任何笔记引用的孤立笔记。反链按**标识映射**解析（`title` / 文件名 stem / `permalink` 三种写法都认），生成页 `记忆索引` 不计为引用方 |
| `memory_stats()` | 健康度统计：各 tier 数量、记忆类型 / 可信度 / 作用域 / 生命周期 / 写入方上下文 / 结构版本分布、访问 TOP10、近 7/30/90 天更新量、热门标签 TOP10、孤立笔记数，以及**运行时指标**（检索零结果率 / 锁等待 / 索引重建） |

### 维护

| 工具 | 说明 |
|------|------|
| `memory_archive(title)` | 归档：正文移入 `.archive/`，原位留摘要 stub |
| `memory_archive_old(days=90)` | 批量归档 N 天未更新的条目，自动跳过核心页 |
| `memory_restore(title, source="archive")` | 恢复。默认从 `.archive/` 取回并把 tier 重置为 warm；`source="trash"` 从回收站取回（根目录已有同名条目则拒绝，不静默覆盖） |
| `memory_batch_tag(old_tag, new_tag)` | 全库标签重命名 |
| `memory_batch_tier(target_tier, min_score?, max_score?)` | 按热度分数批量调整 tier |
| `memory_config(action="get", values?, output_format?)` | **运行时阈值的 MCP 通道**（第 25 个工具，2026-10-16 起）。`get` 返回当前生效值 / 每键来源（`env`/`file`/`default`）/ 中文名与作用说明 / 类型 / 默认值 / 配置文件路径 / 解析错误；`set` 是**整状态写**（须提交全部 8 个键，缺键即拒），写前备份为同目录 `memory.toml.bak.<时间戳>`（保留最近 10 份）、写前自解析、原子替换。**只写 `memory.toml`，绝不触碰任何 `*.md`**。Web 控制台的阈值面板走的就是它 |
| `memory_heat_suggest(apply=False)` | 按访问频次与陈旧度给出 tier 升降建议。默认 `apply=False` **只预览不写盘**；`apply=True` 才真正批量落地（核心页豁免，预览与执行共用同一判据函数，不会漂移） |
| `memory_restart(delay?)` | **原地热重启**：改完 `server.py` 或任一模块后让新代码立刻生效，不必重连客户端、不必重启应用。`os.execv` 替换进程映像并保留 stdin/stdout，会话不断；重启前先在子进程**预检**新代码可导入、会话恢复补丁装得上，失败即放弃。⚠️ 换进程后 **pid 会变**；接力链上限 5 次防重启风暴 |

### 审计

| 工具 | 说明 |
|------|------|
| `memory_audit()` | 全库扫描：空壳检测 / 死链检测（跳过代码块）/ 命名漂移 / 缺 source·tags 汇总 |
| `memory_doctor(limit=20)` | **索引一致性自检（只读不修）**：报告索引与磁盘的三类偏差 —— 索引有行但文件已不在（stale rows）、磁盘有 `.md` 却无索引行（unindexed）、`title` 与 `(size, mtime)` 两侧不一致（field drift）。刻意**跳过惰性重建**，否则修完再报永远「一致」；扫描范围与索引加载器一致（库根 + `.archive`），归档副本不会被误报为 stale |
| `memory_schema_diff()` | **结构对账（只读）**：把「写入端承诺写出什么」与「磁盘上实际有什么」对照。契约来源就是代码（`yaml_io.build_frontmatter` 是唯一写出源），不另立 schema 文件。键分三层 —— core（`title`/`tags`/`created`/`updated`/`tier`/`access_count`，缺失即真实缺陷）/ v2（`type`/`confidence`/`verified`/`scope`/`status`/`schema_version`，v1 存量缺属预期）/ opt（按需写出）。输出六节：核心键缺失 / v2 缺键 / v1 迁移清单 / 未知键（附行号）/ 可选键使用率 / **无 frontmatter 文件**（普通 Markdown 不受结构契约约束，单列且不计入缺陷数） |
| `memory_index_draft()` | 自动生成记忆索引草稿（不回写，需人工确认） |
| `memory_rebuild_links()` | 只读校验：扫描正文双链，报告死链与计数，不修改文件 |

---
