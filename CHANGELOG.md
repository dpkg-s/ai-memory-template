# Changelog

本项目的所有重要变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [2.3.0] - 2026-09-18

本版两条主线：**把「记忆怎么被取回来用」补成一个工具**（`memory_context` 上下文组装，跨会话延续从 3~5 次调用降到 1 次），以及**让改动立刻生效**（`memory_restart` 原地热重启，不必重连客户端）。同时把 Phase 3 的三项机制收成闭环 —— 回收站可进可出、重复写入主动提示、热度可预览可落地 —— 并修掉 4 个隐蔽的正确性问题（双 frontmatter 与摘要污染、重复防护漏掉核心页、索引自动生成死链、Windows 下跨进程缓存永不失效）。

### Added

- **`memory_context` —— 上下文组装（第 23 个工具）**：`memory_graph` 只列「出链 + 反链」，跨会话延续仍得把沿途正文一条条读出来（实测 3~5 次调用）。本工具**把沿途内容直接组装成一段可喂给模型的 Markdown**，一次调用拿到起点 + 周边。沿 `[[wikilink]]` 出链与反链 BFS 至 `depth`（`0` = 只要起点），周边按 `tier(hot > warm > cold) → access_count ↓ → updated ↓` 排序取前 `max_related` 条，每块标注来源与关系（`关系 · 深度 · tier · reads · updated`）；`timeframe` 支持自然语言（`last week` / `2 days ago` / `3d` / `2026-09-01`），**只过滤周边、起点恒在**，无法识别时按不限处理并在输出里提示。
  三个设计要点都是实测踩出来的：① `max_chars` 是**整段预算**而非单篇上限，必须分摊（起点至多一半，其余按篇数均分），否则长起点会独吞预算、周边一篇都展不开；② 自动生成的 `记忆索引.md` **必须排除出周边** —— 它的正文就是全库标题列表，会把每个条目都变成「强相关」；③ 截断要回退到行边界（`rsplit("\n", 1)[0]`），否则会把 `_关系 · 深度 · …_` 标注行切成半截。专项测试 `tests/test_context.py`（59 断言）。
- **`memory_restart` —— 原地热重启（第 22 个工具）**：改完 `server.py` 或任一模块后让新代码**立刻生效，不必重连客户端、不必重启应用**。实现为 `os.execv` 原地替换进程映像并保留 stdin/stdout，故 stdio 会话不断。
  稳妥性靠三道闸：① **预检** —— 先在子进程里试导入新代码、并确认「会话恢复补丁」装得上，任一失败即放弃重启，**绝不让起不来的代码顶掉正在服务的进程**；② 本次响应先经管道发出，真正的 `execv` 在延迟线程里执行；③ 新进程凭环境变量知道自己是「接力进程」，把客户端既有会话视为已初始化 —— 否则客户端不重发 `initialize`，新进程会以 "Received request before initialization was complete" 拒掉一切请求。
  细节：Windows 上 `execv` 内部是 `CreateProcess`，**pid 会变**；接力链上限 5 次防重启风暴；本机 Windows + Python 3.13 实测 `os.execve` 给新进程传 env 有约 1/3 概率崩在调用瞬间（压测 15 轮崩 5 轮），`os.execv` + `os.environ` 赋值 20/20 通过 ⇒ 统一走后者。文档另写明**宿主差异**：有客户端给 stdio MCP 套 Job Object 包装器，换进程时包装器随旧映像一起退出，客户端会报 `Not connected` —— 此时宿主会按需重新 spawn，**依旧不用重启应用**。专项测试 `tests/test_restart.py`。

- **热度驱动的 tier 自动升降级**（`memory_heat_suggest(apply=False)`）：此前它只**输出建议字符串**，改完还得手抄一遍 tool call，等于把闭环留给使用者，实际没人会做。现在支持 `apply=True` 真正写盘，同时保持**默认只预览**——不传参数绝不会动你的库。
  关键设计是**预览与执行共用同一个判据函数 `_heat_tier_decision`**：两套代码各写一份阈值判断，迟早漂移成「预览说升、执行说降」，这是这类工具最隐蔽的坑。判据三档：`score < 0.1` 且非 cold → `cold`；`score < 0.5` 且为 hot → `warm`（先降一档，避免从 hot 直接摔到 cold）；`score > 3.0` 且非 hot → `hot`；其余维持现状。`CORE_PAGES` 一律豁免。
  批量写盘走既有 `_write_memory`，v2 字段（`scope` / `status` / `confidence` / `supersedes` 等）由磁盘继承，**不会因为改 tier 而丢失**。专项测试 `tests/test_heat_apply.py`（29 断言）。
- **`memory_search` 中文长句兜底**（bigram 模糊匹配）：`memory_smart_search` 早已用 bigram 分词，但 `memory_search` 一直是**纯子串匹配** —— 短关键词（「路由器」）没问题，长句（「怎么在路由器上跑容器」）作为整体子串必然零命中，「查不到」与「不存在」被混为一谈。
  现改为**精确路径零命中时**才降级到 bigram 兜底，命中门槛为「共享中文 bigram ≥ 2 且 query 覆盖率 ≥ 0.15」——于是常态查询零额外开销，弱相关条目也不会被硬凑进来。兜底结果首行显式标注「以下为 bigram 模糊匹配结果」，**不伪装成精确命中**。长句召回实测 0.00 → 1.00；`tests/test_search_quality.py` 扩到 12 断言（新增 H 组 5 条：长句召回 ≥ 0.80、短关键词零回归、精确命中不得误标为模糊、负样本不得被兜底误召回）。
- **冲突 / 重复主动防护**（`memory_write`）：写入后自动做疑似重复/冲突检测，命中时在返回值末尾追加**非阻断提示**。相似度用字符 bigram 集合的 Jaccard 系数（零依赖、对中文天然友好，绕开 FTS5 分词坑）——标题相似度 ≥ 0.6 判「疑似重复标题」，否则正文 ≥ 0.5 判「疑似重复/冲突内容」，最多提示 3 条。**只提示、不改数据**：是否合并、是否给旧条目标注 `supersedes`/`conflicts` 由写入方（AI / 主人）决定；检测异常静默跳过，绝不影响正常写入。这补上了 2.2.0 落地 `supersedes`/`conflicts` 字段后「字段有了、机制空白」的缺口。专项测试 `tests/test_dup_guard.py`（15 断言）。
- **回收站闭环**（`.trash/`）：`memory_delete` 自 2.0 起默认软删除，但此前**只进不出** —— 文件移进 `.trash/` 后没有任何工具能列出来或取回去，「可恢复」一直停留在纸面。本次补齐三步：
  - `memory_list(include_trash=True)`：列出回收站内容（标题、移入时间、体积）
  - `memory_restore(title, source="trash")`：从回收站取回。**根目录已存在同标题条目时拒绝**并说明原因 —— 回收站里那份可能是被新版取代的旧内容，静默覆盖比不恢复更危险
  - `memory_delete(title, empty_trash=True)`：一键清空回收站（唯一会批量删除的路径，需显式传入，`title` 被忽略）
  - 软删除时把文件 `mtime` 置为移入时刻，回收站列表因而能显示真实的删除时间
  - `.trash/` 天然不参与检索（读取端 glob 非递归）；恢复后 `status` 回到 `active`、`schema_version` 升到当前版本
  - 与既有 `.archive/` 恢复路径互不干扰：`memory_restore` 的 `source` 默认 `"archive"`，行为完全向后兼容
  - 专项测试 `tests/test_trash.py`（34 断言）：软删除退出检索、恢复后索引与字段正确、同名冲突拒绝、同名两条并存、`purge` 与清空、既有归档路径无回归

### Changed

- **`memory_search` 长句检索策略**：此前是**纯子串匹配**，长句作为整体子串必然零命中（「查不到」与「不存在」被混为一谈）。现改为「精确路径零命中时才降级到 bigram 兜底」，命中门槛为共享中文 bigram ≥ 2 且 query 覆盖率 ≥ 0.15 —— 常态查询零额外开销，弱相关条目不会被硬凑进来；兜底结果首行显式标注「以下为 bigram 模糊匹配结果」，不伪装成精确命中。长句召回实测 0.00 → 1.00。
- **`memory_heat_suggest` 由「只预览」变为「可预览可执行」**：新增 `apply=True` 真正写盘，同时保持默认 `apply=False` 只预览 —— 不传参数绝不会动库。预览与执行共用同一个判据函数 `_heat_tier_decision`，避免两套阈值漂移成「预览说升、执行说降」。
- **回收站从「只进不出」变为闭环**：`memory_delete` 软删进的条目，现在可通过 `memory_list(include_trash=True)` 列出、`memory_restore(title, source="trash")` 取回、`memory_delete(title, empty_trash=True)` 清空。
- **`memory_rebuild_links` 改为只读校验**：不再改写文件，只扫描全库双链、报告死链与计数。
- **跨进程缓存失效键升级**：由「目录 `st_mtime`」改为「目录 mtime + 逐文件 `(name, size, mtime_ns)` 指纹」（`_vault_fingerprint()`），任何内容变更（含 Obsidian 保存、其它客户端写入、`git` 改文件）都会令缓存失效。147 篇规模下额外开销约 1~3 ms。

### Fixed

- **双 frontmatter 与摘要污染**（`memory_write` / `_write_memory`）：调用方把「带 frontmatter 的完整笔记」当 `content` 传入时，与写入端自行生成的权威 frontmatter 直接拼接，产出「双 frontmatter」—— 第二块连同 YAML 退化成正文深层垃圾，并固化进 `summary` 字段。全库体检实测命中 8 篇。新增 `yaml_io.strip_leading_frontmatter()` 在写入入口与收口各剥一次，只对「确实像 frontmatter」的块生效，正文开头合法的 `---` 水平线不被误伤；顺带获得自愈能力（已写坏的条目下次写入即恢复）。
- **冲突防护漏掉核心页**（`_find_duplicate_hints`）：上线时无条件跳过 `CORE_PAGES`，导致「新建『近期工作动态记录』而非更新原页」这类最典型的重复写入静默通过。现已让核心页同样参与比对，真正豁免的只有完全同名（属更新）。
- **索引自动生成死链**（`_refresh_index` / `memory_index_draft`）：直接输出 `- [[{title}]]`，而 Obsidian 以文件名 stem 解析双链，标题含空格即全成渲染死链，且每次 `memory_write` 后重建会把人工修复覆盖回去（148 篇库稳定复现 36 条）。新增 `_wiki_link(path, title)`：target 恒用 `path.stem`，可读标题放别名位（`[[stem|title]]`）。
- **跨进程缓存失效在 Windows 上不生效**（`_iter_entries`）：Windows/NTFS 改写已存在文件内容不更新父目录 mtime，外部编辑永远触发不了缓存重建，服务端持续返回陈旧条目。改用文件级指纹后已修复。

### Removed

- **`memory_index_draft` 里从未被使用的错误别名计算**：形如 `f"{title}|{path.stem}"` 的别名方向反了且从未被使用，随索引死链修复一并删除。

### Dependencies

- 无变化（`requirements.txt` 仍为 `mcp>=1.27,<2`，`requirements-dev.txt` 仍为 `pytest>=8.0` + `ruff>=0.5`）。

## [2.2.0] - 2026-09-10

三层补强：**作用域与生命周期**（记忆该在哪个范围生效、哪条还适用）、**工程韧性**（把「多 AI 工具同时写一个库」纳入自动化测试并修复其暴露的并发缺陷）、**可观测性**（服务跑得怎么样）。

### Added

- **作用域 / 生命周期字段**（frontmatter 升到 `schema_version: 2`）：
  - `scope`：`global` / `project` / `temporary`，配 `project` 标识用于项目级隔离
  - `status`：`candidate` / `active` / `stale` / `deprecated` / `archived`
  - `supersedes`：本条结论取代了哪条旧记忆；`conflicts`：与之冲突的条目
  - `source_context`：写入方上下文（如 `workbuddy` / `claude-desktop` / `codex`），可在各客户端 MCP 配置里用 `AI_MEMORY_SOURCE_CONTEXT` 环境变量设定
  - **只做字段层，不做目录分层**：单一根目录 + 字段过滤。`global/ projects/ temporary/` 目录分层被明确否决 —— 服务端有 15 处 glob 扫描是非递归的，搬文件即脱离检索，收益不抵风险
  - **默认排除 archived**：`memory_search` / `memory_list` / `memory_smart_search` 省略 `status` 时自动隐藏归档条目（`stale` / `deprecated` 仍保留 —— 「过时」不等于「不该看见」），被隐藏的命中数会在结果末尾提示，避免误判为「记忆不存在」
  - **零迁移向后兼容**：旧笔记读取端注入默认值（`global` / `active` / schema v1），仅在被显式写入时渐进补齐
  - `memory_archive` / `memory_restore` 自动联动 `status`（`archived` ⇄ `active`），并保留原 scope/project
  - `memory_stats` 新增作用域 / 生命周期 / 写入方上下文 / 结构版本 / 项目五项分布
  - `memory_audit` 新增「生命周期 / 冲突 / 作用域一致性」章节：检出 `supersedes` 与 `conflicts` 的死引用、`scope=project` 却缺 `project`、`status=archived` 却无 `archived_to`
  - **作用域与生命周期专项测试**（`tests/test_scope_lifecycle.py`，70 断言）
- **跨进程并发测试**（`tests/test_concurrency.py`，38 断言；配套子进程工人 `tests/concurrency_worker.py`）：
  覆盖五种真实竞争场景 —— 乐观锁竞争（10 进程同版本写入 → 恰好 1 成功 9 冲突）、无冲突并发写、
  写/归档/删除混合、读写并发（300 次读循环 vs 3 写）、收尾对账（无锁残留 + 索引与磁盘逐行核对）。
  用**真实 subprocess** 而非线程，才能复现多客户端同写一库时的进程级锁与缓存隔离语义。
- **异常恢复测试**（`tests/test_recovery.py`，35 断言）：索引删除→重建、索引垃圾字节→自愈、
  锁文件残留/损坏→陈旧回收、写入窗口内强杀进程→无损坏、外部改写正文→以磁盘为准、
  外部增删文件（如 `git` 切版本）→以磁盘为准、七类畸形 Markdown（0 字节 / 无 frontmatter /
  半截 `---` / 非法 YAML）→工具全部降级不崩、重复重建幂等。
- **检索质量评测**（`tests/test_search_quality.py`，7 断言）：15 篇 fixture（中文 / 英文 /
  代码标识符 / 专有名词）× 20 查询 + 3 负样本，以 recall@10 ≥ 0.95、MRR ≥ 0.80、
  零结果率为 0、负样本必须全空等指标作为**检索质量护栏**。后续任何检索改动都要过这道闸。
- **运行时指标层**（`metrics.py` + `tests/test_metrics.py`，44 断言）：纯标准库，记录检索调用数 /
  零结果率 / 命中数 / 平均耗时、读写次数与未命中率、锁获取与等待时长 / 超时、索引重建次数。
  设计上**绝不阻塞主流程**（全链路 try/except，指标自身崩溃也不外泄）、允许并发下少量丢失
  （多进程 read-merge-write 同一 JSON 非原子，用「不精确」换「零耦合」）、正常退出时自动落盘零头。
  指标摘要已并入 `memory_stats` 与 `memory_audit` 的「## 运行时指标」章节。
  可用环境变量 `AI_MEMORY_METRICS=0` 关闭。

### Fixed

- **索引损坏不自愈**：索引文件被截断或写入垃圾字节后，`_ensure_index` 只是静默返回，
  索引会永久失效。现改为**丢弃并重建一次**（优先截断为 0 字节 —— 部分受限环境会拦截删除动作），
  重建仍失败才保持脏标记并回退全库扫描。
- **把锁竞争误判为索引损坏**（并发测试实测）：SQLite 的 `database is locked` 属 `OperationalError`，
  原判定会在多进程同时访问时丢弃**他人正在使用**的索引文件，引发连锁失败。
  现严格区分「内容损坏」（`DatabaseError` 且非 `OperationalError`）与「锁竞争」。
- **读路径写盘未持锁**：`_flush_access_counts` 由读路径调用却不持锁，与写进程共用同一个
  `<文件名>.tmp` 互相删除（CI 在 Python 3.10/3.11 上偶发 `FileNotFoundError`）。
  现改为自持锁，并把临时文件名加上 pid 后缀。
- **Windows 下写盘替换偶发 `PermissionError(WinError 5)`**：只要有另一进程此刻正打开目标文件读取，
  `os.replace` 就会失败。新增退避重试（累计约 0.75 秒），把「替换瞬间」与「他人读取窗口」错开；
  仍失败则原样抛出，绝不降级为非原子写。POSIX 上该分支不会触发。
- **指标落盘失败会残留 `.tmp` 孤儿文件**：写临时文件成功、`os.replace` 失败（Windows 上目标被占用）
  时会留下 `<name>.<pid>.tmp`。现于失败分支顺手清理 —— 指标层刻意不做退避重试，观测不值得为
  一次写盘阻塞主流程，增量已回收到内存、下次 flush 重写。
- **`memory_smart_search` 过度召回**：时间新鲜度原本可**单独**决定入选，导致任意查询都会把
  「当天更新」的条目塞进结果（负样本用例实测：查询「量子纠缠退相干」返回 10 条完全无关的记忆）。
  现改为零词法命中即非候选，新鲜度只作加权项。

### Security

- **修复两个安装脚本的真实缺陷**：
  - `pip install mcp` **未带版本上界** —— 会装上 mcp 2.x，而 2.x 已把 `FastMCP` 更名为 `MCPServer`，装完启动即 `ImportError`。现固定为 `mcp>=1.27,<2`
  - **只复制 `server.py`、不复制依赖模块** —— `server.py` 依赖 `memory_index` / `yaml_io` / `text_utils` / `locks` / `metrics`，按原脚本装完必然启动失败。现改为整组复制
- **安装脚本顶部加安全声明**：明确列出脚本会做的三件事、不会做的事（不读写你的其他文件、不改 shell 配置或注册表、不请求 sudo/管理员权限、不常驻、除 pip 装依赖外不联网）
- **README 安装章节改为三种方式对比**：下载后审阅再执行（推荐）/ 一行管道执行 / clone 后本地运行，并明确提示管道执行看不到脚本内容
- **发布链路加入 SHA256 校验**：新增 `.github/workflows/release.yml`，打 `v*` tag 时**先跑完全部测试**再发布 Release，并把各文件的 SHA256 写进 Release 说明 + 作为附件上传（`scripts/make_checksums.py` 生成）。哈希取自 git blob 字节而非工作区 —— 仓库用 `.gitattributes` 把 `*.sh` / `*.py` 强制为 LF，而 Windows 工作区是 CRLF，直接哈希工作区文件会得出与用户下载内容不同的结果
- 新增环境变量 `AI_MEMORY_SKIP_PIP=1`（依赖已就绪时跳过脚本里的 pip），安装脚本支持 `AI_MEMORY_DIR` 指定记忆库目录

### Changed

- **CI 测试步骤改为遍历 `tests/test_*.py`**：新增回归测试无需再改 CI（子进程工人
  `tests/concurrency_worker.py` 不匹配该模式，不会被误执行）。CI 断言总数 77 → 271。
- **CI lint 目标加入 `metrics.py`**。
- `.gitignore` 用一条通配 `memory_index_*.db*` 覆盖索引全部派生物（`.db` / `-wal` / `-shm` / 测试用 `.db.orphan`），
  并新增 `memory_metrics_*.json`。

## [2.1.0] - 2026-09-10

引入**记忆可信度字段**，区分「用户明说的事实」与「AI 自行推断」，遏制 Memory Poisoning（AI 推测被写入后被后续会话当成事实复用）。

### Added

- **记忆可信度字段**：frontmatter 新增 `type` / `confidence` / `verified` / `verified_at`
  - `type`：9 种取值 —— `fact`（事实）/ `preference`（偏好）/ `decision`（决策）/ `experience`（经验）/ `episodic`（事件）/ `project`（项目）/ `constraint`（约束）/ `workflow`（工作流）/ `temporary`（临时）
  - `confidence`：`high` / `medium` / `low`
  - `verified` + `verified_at`：是否已由用户确认或事实核验。置 `true` 自动记录当日日期，置 `false` 自动清除日期（避免「未验证却有验证日期」的矛盾态）
  - **零迁移向后兼容**：读取端为不含新字段的历史笔记注入默认值（`fact` / `medium` / `false`），磁盘文件保持原样，仅在下次被显式写入时渐进补齐 —— 无需一次性全库迁移
- **`memory_write` / `memory_update_metadata` 新增参数**：`mem_type` / `confidence` / `verified`。非法取值直接拒绝并提示合法取值；更新未传字段时继承磁盘原值，不会把已有可信度重置为默认
- **`memory_search` / `memory_list` / `memory_smart_search` 新增 `mem_type` 过滤**
- **搜索结果行内可信度徽章**：形如 ` | type=decision conf=high ✓verified`，一行摘要即可判断来源与可信度，无需逐条读取
- **`memory_stats` 新增两节统计**：记忆类型分布、可信度分布（含已核验计数）
- **记忆可信度专项测试**（`tests/test_p0_credentials.py`）：24 条断言，覆盖写入落盘 / 更新继承 / 非法值拒绝 / 历史笔记兼容 / 检索过滤 / 统计 / 序列化边界

### Changed

- **README 全量重写**：改为以仓库名 `ai-memory-template` 为标题（原为「多平台AI融合记忆库」，与仓库名不一致）；新增目录导航与「实际使用效果」对话示例；补充 FAQ 与「写入即自动化」「工程化保障」特性说明；工具速查表补齐全部真实默认参数
- **修正 README 与实际不符的两处描述**：
  - 依赖约束原写 `mcp>=1.27`，实际已锁定为 `mcp>=1.27,<2`（并补充 mcp 2.x 更名 `MCPServer` 的说明）
  - 自动标签规则原写「约 30 条」，实际 `_TAG_AUTO_MAP` 为 **68 条**
- CI 徽章改用 GitHub Actions 原生 badge URL
- CI 测试步骤加入 `tests/test_p0_credentials.py`（测试断言总数 53 → 77）

### Fixed

- **清除 `rules/` 与 `template/` 下 7 个 Markdown 文件的 UTF-8 BOM**：BOM 会顶掉 YAML frontmatter 的起始 `---`，使文件被当作无元数据解析（`rules/记忆写入格式规范.md` 受影响最直接）。`install.ps1` 刻意保留 BOM —— 旧版 Windows 终端依赖它以正确识别中文
- **移除仓库内两处私有路径引用**：`rules/记忆写入格式规范.md` 的 vault 位置说明、`tests/test_roundtrip.py` 的注释，统一改为通用默认值（`~/ai-memory` / `AI_MEMORY_DIR` 环境变量）

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

[Unreleased]: https://github.com/dpkg-s/ai-memory-template/compare/v2.3.0...HEAD
[2.3.0]: https://github.com/dpkg-s/ai-memory-template/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.2.0
[2.1.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.1.0
[2.0.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v2.0.0
[1.1.0]: https://github.com/dpkg-s/ai-memory-template/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dpkg-s/ai-memory-template/releases/tag/v1.0.0
