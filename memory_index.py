"""SQLite 元数据索引层 for ai-memory MCP server (server.py).

设计原则 (2026-09-07, 克制版):
- md 文件仍是唯一事实源 (Obsidian 可读、git 可管、多端可写)。
- SQLite 只镜像 frontmatter 元数据 + title/stem -> 相对路径 的定位表,
  让高频 memory_read / memory_list 从 O(n) 全库 glob+parse 降为 O(1) 定位。
- 正文永不入库: 读取时实时从磁盘读文件, 天然与 Obsidian/外部改动一致。
- 一致性策略:
  * 写路径 (write/update_metadata/delete/archive/restore/batch_*/flush)
    在文件写盘成功后增量 upsert/remove 本索引 (均在 server 文件锁内)。
  * 读路径按文件 (size, mtime) 懒校验: 命中行但 mtime 对不上 -> 该行失效,
    回退全库扫描并刷新该条 (自愈外部改动)。
  * lookup 未命中 (外部新建文件) -> 回退全库扫描兜底, 找到则补入索引。
- 数据库位置: 与 server.py 同目录 memory_index.db (多端若指向同一 server.py
  则共享; 不入 git)。
- 并发: WAL 模式 + busy_timeout; 写操作全部发生在 server 的 _acquire_lock 内,
  天然串行。读操作走 WAL 快照, 无需锁。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DB_NAME = "memory_index.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    relpath      TEXT PRIMARY KEY,   -- 相对 MEMORY_DIR 的 posix 路径 (一个文件一行)
    title        TEXT NOT NULL,      -- frontmatter title (归档 stub 与 .archive 副本可同 title)
    stem         TEXT NOT NULL,      -- 文件名 stem (无扩展名), 用于旧文件回退匹配
    tags         TEXT NOT NULL DEFAULT '[]',  -- JSON 数组
    tier         TEXT NOT NULL DEFAULT 'warm',
    created      TEXT,
    updated      TEXT,
    source       TEXT,
    version      INTEGER,
    access_count INTEGER NOT NULL DEFAULT 0,
    summary      TEXT,
    permalink    TEXT,               -- A3: 稳定标识（首次写入冻结），文件改名后仍可定位
    type         TEXT,               -- P0: 记忆类型（缺省视为 fact）
    scope        TEXT,               -- P0: 作用域 global / project
    project      TEXT,               -- P1: 项目名（仅 scope=project 时有值）
    status       TEXT,               -- P1: 生命周期 active / stale / ...
    confidence   TEXT,               -- P0: 可信度 high / medium / low
    verified     INTEGER,            -- P0: 是否核验 0/1（布尔存整数）
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_title ON entries(title);
CREATE INDEX IF NOT EXISTS idx_entries_stem ON entries(stem);
CREATE INDEX IF NOT EXISTS idx_entries_tier ON entries(tier);
"""
# ⚠️ permalink 与下面 _RETRIEVAL_COLS 里各列的索引**一律刻意不写在本 SCHEMA 中**，
#    理由见 _migrate 的说明：旧库表已存在且缺列，SCHEMA 内的 CREATE INDEX 会抛
#    `no such column` 并打断整个 executescript，连补列的机会都没有。

# 检索过滤列（2026-10-23 新增）。`memory_search` / `memory_list` 的
# mem_type / scope / project / status 过滤原先只能「读完正文再判断」——
# 索引表根本没有这些列。补进表后，检索可以先用元数据把候选池缩小，
# 再对剩余候选读正文，避免每次全库 2.84 MB 的扇入扫描。
_RETRIEVAL_COLS: tuple[tuple[str, str], ...] = (
    ("type", "TEXT"),
    ("scope", "TEXT"),
    ("project", "TEXT"),
    ("status", "TEXT"),
    ("confidence", "TEXT"),
    ("verified", "INTEGER"),
)

# 只有会被 WHERE 命中的列才建索引。confidence / verified 选择度过低，建了反而
# 增加写放大，故不建。
_RETRIEVAL_IDX: frozenset[str] = frozenset({"type", "scope", "status", "project"})

# 与 yaml_io.DEFAULT_MEMORY_TYPE 保持一致。此处内联而非 import，是为了让索引层
# 保持「零业务依赖」——它只镜像字段，不参与 frontmatter 语义判断。
_DEFAULT_TYPE = "fact"


def db_path(state_dir: Path, memory_dir: Path) -> Path:
    """Index db 放在调用方给的**状态目录**（不污染记忆库），文件名按 MEMORY_DIR
    哈希隔离：同一 server 服务多个库/测试临时库时互不干扰。

    状态目录由 server 决定（`AI_MEMORY_STATE_DIR`，默认与 server.py 同目录；
    容器里指向独立卷 `/state`），本模块不感知部署形态。
    """
    h = hashlib.sha1(str(memory_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return state_dir / f"memory_index_{h}.db"


def connect(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_file), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """幂等补列（A3, 2026-10-08）。

    `CREATE TABLE IF NOT EXISTS` **不会**给已存在的表加列，所以 A3 之前建的索引库
    缺 `permalink` 列。而缺列的报错 `no such column: permalink` 属
    `OperationalError`，会被 `server._is_index_corrupted` 判为「锁竞争」⇒ **不会**
    触发丢弃重建。因此若不在连接层显式补齐，索引定位会持续报错（只剩全库回退），
    而且症状是「静默降级」而非报错。

    索引定位为「可重建缓存」，补列是最轻的处置；失败也不阻塞连接 —— 查询报错后
    调用方自会回退全库扫描，功能不降级。

    ⚠️ permalink 的**索引也一并放在这里**，绝不能写进 SCHEMA：SCHEMA 是在
    `CREATE TABLE IF NOT EXISTS` 之后立即执行的，而旧库的表**已存在且没有该列**，
    于是 `CREATE INDEX ... ON entries(permalink)` 会抛 `no such column: permalink`
    并把整个 `executescript(SCHEMA)` 打断 —— 连补列的机会都不会有（实测踩到）。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
        if "permalink" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN permalink TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_permalink ON entries(permalink)")
    except sqlite3.Error:
        pass

    # 检索过滤列（2026-10-23）：逐列独立 try —— 某一列补失败不应拖累其余列，
    # 也不能让异常冒到 connect() 外面（否则整个索引层降级成「只剩全库回退」）。
    # 补完一列立刻建该列索引，理由同 permalink：索引必须在列存在之后才能建。
    for name, decl in _RETRIEVAL_COLS:
        try:
            if name not in cols:
                conn.execute(f"ALTER TABLE entries ADD COLUMN {name} {decl}")
            if name in _RETRIEVAL_IDX:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_entries_{name} ON entries({name})")
        except sqlite3.Error:
            continue


def meta_to_row(memory_dir: Path, path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """把 frontmatter meta + 文件状态合成一行 (不读正文)."""
    rel = path.relative_to(memory_dir).as_posix()
    title = str(meta.get("title") or path.stem).strip()
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    tags = [str(t) for t in tags if str(t).strip()]
    try:
        st = path.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    return {
        "title": title,
        "stem": path.stem,
        "relpath": rel,
        "tags": json.dumps(tags, ensure_ascii=False),
        "tier": str(meta.get("tier") or "warm"),
        "created": str(meta.get("created") or ""),
        "updated": str(meta.get("updated") or ""),
        "source": str(meta.get("source") or ""),
        "version": meta.get("version") if isinstance(meta.get("version"), int) else None,
        "access_count": meta.get("access_count") if isinstance(meta.get("access_count"), int) else 0,
        "summary": str(meta.get("summary") or ""),
        "permalink": str(meta.get("permalink") or ""),
        # 检索过滤列。`_load_memory` 经 `_apply_meta_defaults` 后通常已带默认值，
        # 但 meta_to_row 也会被测试直接调用，故这里仍兜一层。
        "type": str(meta.get("type") or _DEFAULT_TYPE),
        "scope": str(meta.get("scope") or ""),
        "project": str(meta.get("project") or ""),
        "status": str(meta.get("status") or ""),
        "confidence": str(meta.get("confidence") or ""),
        "verified": 1 if meta.get("verified") else 0,
        "size": size,
        "mtime": mtime,
    }


def upsert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO entries(relpath, title, stem, tags, tier, created, updated,
                              source, version, access_count, summary, permalink,
                              type, scope, project, status, confidence, verified,
                              size, mtime)
           VALUES(:relpath,:title,:stem,:tags,:tier,:created,:updated,
                  :source,:version,:access_count,:summary,:permalink,
                  :type,:scope,:project,:status,:confidence,:verified,
                  :size,:mtime)
           ON CONFLICT(relpath) DO UPDATE SET
             title=excluded.title, stem=excluded.stem, tags=excluded.tags,
             tier=excluded.tier, created=excluded.created, updated=excluded.updated,
             source=excluded.source, version=excluded.version,
             access_count=excluded.access_count, summary=excluded.summary,
             permalink=excluded.permalink,
             type=excluded.type, scope=excluded.scope, project=excluded.project,
             status=excluded.status, confidence=excluded.confidence,
             verified=excluded.verified,
             size=excluded.size, mtime=excluded.mtime""",
        row,
    )


def remove(conn: sqlite3.Connection, relpath: str) -> None:
    conn.execute("DELETE FROM entries WHERE relpath=?", (relpath,))


def find(conn: sqlite3.Connection, title_or_stem: str) -> list[sqlite3.Row]:
    """按 permalink / title / stem 匹配的所有行 (同 title 可能有根 stub + .archive 副本).
    根目录条目在前, 同目录内 updated 新者在前——与 server 原 glob 语义一致.

    A3：permalink 也参与匹配 ⇒ 文件被改名（stem 变）后，用旧标识仍能定位到它。
    """
    return conn.execute(
        """SELECT * FROM entries WHERE permalink=? OR title=? OR stem=?
           ORDER BY (relpath LIKE '.archive/%') ASC, updated DESC""",
        (title_or_stem, title_or_stem, title_or_stem),
    ).fetchall()


def iter_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM entries ORDER BY updated DESC").fetchall()


def candidates(conn: sqlite3.Connection, *, mem_type: str | None = None,
               scope: str | None = None, project: str | None = None,
               status: str | None = None, tier: str | None = None,
               tag: str | None = None,
               include_archived: bool = False,
               null_is_candidate: bool = True) -> list[sqlite3.Row]:
    """按 frontmatter 元数据预筛候选行 —— 供检索做「过滤前置」，全程不碰磁盘。

    这是「先筛后读」的入口：调用方拿到 relpath 列表后，只对这批候选读正文，
    不再全库扫。相比现状（先 `_load_memory` 全库、再判断 tag/scope/project），
    带任一过滤条件的查询可把 IO 砍掉大半。

    ⚠️ 三处刻意保守，调用方必须知悉：

    1. `null_is_candidate=True`（默认）时，**该列为 NULL 或空串的行一律保留为
       候选**。索引是缓存，旧行可能尚未回填；若据「缺失」做排除，会静默吞掉
       真实命中（比慢更糟）。索引全部回填后可传 False 收紧。
    2. `tag` 用 `LIKE '%"tag"%'` 粗筛。tags 列是 JSON 数组，这个模式能命中
       带引号的元素，但也可能命中子串（`"mcp"` 会匹配 `"mcp-extra"`）——
       调用方**仍须用真实 tag 列表做一次精确判定**。
    3. 未指定 `status` 时默认排除 `archived`（与 `memory_search` 默认语义一致），
       但 NULL 状态不在此列 —— `COALESCE(status,'') != 'archived'` 会把未知
       状态保留下来。

    排序为 `updated DESC`，与 `memory_search` 的 recent-first 同向；调用方若要
    再排序（如按 tier / access_count），自行重排即可。
    """
    sql = ["SELECT * FROM entries WHERE 1=1"]
    args: list[Any] = []

    def _eq(col: str, value: str) -> None:
        """精确匹配；保守模式下把 NULL/空串也算作候选（未知≠不匹配）。"""
        if null_is_candidate:
            sql.append(f"AND ({col} = ? OR {col} IS NULL OR {col} = '')")
        else:
            sql.append(f"AND {col} = ?")
        args.append(value)

    if mem_type:
        # type 缺省按 fact 处理（与 server 的 DEFAULT_MEMORY_TYPE 一致），
        # 故用 COALESCE 补齐后再比，不需要"未知即保留"。
        sql.append("AND COALESCE(type, ?) = ?")
        args.extend([_DEFAULT_TYPE, mem_type])
    if scope:
        _eq("scope", scope)
    if project:
        _eq("project", project)
    if tier:
        _eq("tier", tier)
    if status and status != "any":
        _eq("status", status)
    elif not include_archived:
        sql.append("AND COALESCE(status, '') != 'archived'")
    if tag:
        sql.append("AND tags LIKE ?")
        args.append(f'%"{tag}"%')
    sql.append("ORDER BY updated DESC")
    return conn.execute(" ".join(sql), args).fetchall()


def rebuild(conn: sqlite3.Connection, memory_dir: Path, loader) -> int:
    """全量重建: loader(memory_dir) 需产出 (path, meta) 迭代器.
    返回索引条目数."""
    conn.execute("DELETE FROM entries")
    n = 0
    for path, meta in loader():
        try:
            row = meta_to_row(memory_dir, path, meta)
            upsert(conn, row)
            n += 1
        except Exception:
            continue
    conn.commit()
    return n


def fresh(conn: sqlite3.Connection, memory_dir: Path, row: sqlite3.Row) -> bool:
    """校验索引行对应的磁盘文件 (size, mtime) 是否仍一致."""
    p = memory_dir / row["relpath"]
    try:
        st = p.stat()
        return st.st_size == row["size"] and abs(st.st_mtime - row["mtime"]) < 1e-6
    except OSError:
        return False


def close(conn: sqlite3.Connection) -> None:
    try:
        conn.commit()
        conn.close()
    except Exception:
        pass
