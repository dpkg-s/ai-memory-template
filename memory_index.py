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
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_title ON entries(title);
CREATE INDEX IF NOT EXISTS idx_entries_stem ON entries(stem);
CREATE INDEX IF NOT EXISTS idx_entries_tier ON entries(tier);
"""
# ⚠️ permalink 的索引**刻意不写在这里**，见 _migrate 的说明。


def db_path(server_dir: Path, memory_dir: Path) -> Path:
    """Index db 放在 server.py 同目录(不污染记忆库), 文件名按 MEMORY_DIR 哈希隔离:
    同一 server 服务多个库/测试临时库时互不干扰."""
    h = hashlib.sha1(str(memory_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return server_dir / f"memory_index_{h}.db"


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
        "size": size,
        "mtime": mtime,
    }


def upsert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO entries(relpath, title, stem, tags, tier, created, updated,
                              source, version, access_count, summary, permalink, size, mtime)
           VALUES(:relpath,:title,:stem,:tags,:tier,:created,:updated,
                  :source,:version,:access_count,:summary,:permalink,:size,:mtime)
           ON CONFLICT(relpath) DO UPDATE SET
             title=excluded.title, stem=excluded.stem, tags=excluded.tags,
             tier=excluded.tier, created=excluded.created, updated=excluded.updated,
             source=excluded.source, version=excluded.version,
             access_count=excluded.access_count, summary=excluded.summary,
             permalink=excluded.permalink,
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
