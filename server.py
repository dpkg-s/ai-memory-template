"""
Local shared memory MCP server.

Storage:
- Markdown files under a configurable vault directory (default: ~/ai-memory,
  override with the AI_MEMORY_DIR environment variable)
- One file per memory entry
- Frontmatter written as YAML (Obsidian-native), legacy JSON still readable

Features:
- 24 MCP tools: read/write/search/smart_search/list/recent/graph/context/
  orphans/stats, audit/doctor/schema_diff/index_draft/rebuild_links, batch tag &
  tier, heat-based tier suggestions, archive/restore/delete, in-place hot restart
  (memory_restart), auto-generated MOC index (.记忆索引.md, hidden by B6)
- YAML frontmatter, Obsidian wikilinks, automatic link extraction,
  title-based dedup upsert, file locking, cross-process cache invalidation,
  SQLite metadata index for O(1) title lookup
- 运行时阈值可外置到 vault 根的 memory.toml（B5，热重载，见 memory_config）
- 零外部依赖：仅标准库 + mcp SDK
"""

from __future__ import annotations

import atexit
import json
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import time
import logging
import sys
import threading
import functools

logger = logging.getLogger("ai-memory")
_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import memory_index as midx
import locks
import memory_config
import metrics
import text_utils
import yaml_io

# ---- 拆分模块的别名导入 (P3 模块化, 2026-09-10) ----------------------
# 下列函数已移入独立模块; 保留原下划线命名并通过 as 别名导入, 使本文件
# 所有既有调用点零改动, 降低重构风险。
from text_utils import (
    clean_link_name as _clean_link_name,
    days_since_utc as _days_since_utc,
    extract_wiki_links as _extract_wiki_links,
    heat_score as _heat_score,
    legacy_flat_filename as _legacy_flat_filename,
    parse_dt_utc as _parse_dt_utc,
    safe_filename as _safe_filename,
)
from yaml_io import (
    CONFIDENCE_LEVELS,
    DEFAULT_CONFIDENCE,
    DEFAULT_MEMORY_TYPE,
    DEFAULT_SCOPE,
    DEFAULT_STATUS,
    DEFAULT_VERIFIED,
    LIFECYCLE_STATUSES,
    MEMORY_TYPES,
    SCHEMA_VERSION,
    SCOPES,
    build_frontmatter as _build_frontmatter,
    dump_yaml_frontmatter as _dump_yaml_frontmatter,
    parse_frontmatter as _parse_frontmatter,
    parse_yaml_frontmatter as _parse_yaml_frontmatter,
    read_text as _read_text,
    strip_bom as _strip_bom,
    strip_leading_frontmatter as _strip_leading_frontmatter,
    yaml_quote_scalar as _yaml_quote_scalar,
)

MEMORY_DIR = Path(os.environ.get("AI_MEMORY_DIR", "~/ai-memory")).expanduser().resolve()
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
# B5（2026-10-08）：把 vault 位置注入配置模块 —— 它是最底层，**不反向 import server**，
# 因此配置文件的位置只能由这里喂给它。必须在任何阈值读取之前调用。
memory_config.set_vault(MEMORY_DIR)
_SERVER_DIR = Path(__file__).resolve().parent
_IDX_FILE = midx.db_path(_SERVER_DIR, MEMORY_DIR)
_IDX_NEEDS_REBUILD = True

# ---- 运行时指标 (2026-09-10, 《改进建议》P1 Observability) -----------
# 只回答「服务跑得怎么样」，不回答「库里有什么」。全部异常静默、绝不阻塞主流程；
# 可设 AI_MEMORY_METRICS=0 关闭。落盘位置与索引 db 同目录、按 MEMORY_DIR 哈希隔离。
_METRICS_ENABLED = os.environ.get("AI_MEMORY_METRICS", "1") not in ("0", "false", "False")
_METRICS = metrics.Metrics(
    metrics.path_for(_SERVER_DIR, MEMORY_DIR) if _METRICS_ENABLED else None
)
# 正常退出时把内存里的零头也落盘（否则最后不足 FLUSH_EVERY 条事件会随进程消失）。
# 被强杀时自然拿不到，这正是「指标允许少量丢失」的取舍——不值得为它引入信号处理。
atexit.register(_METRICS.flush)


def _m_inc(key: str, n: float = 1) -> None:
    """指标计数。**永不抛异常** —— 观测绝不能成为主流程的新故障点。"""
    try:
        if _METRICS_ENABLED:
            _METRICS.inc(key, n)
    except Exception:
        pass


def _m_ms(key: str, ms: float) -> None:
    """指标耗时累积。**永不抛异常**。"""
    try:
        if _METRICS_ENABLED:
            _METRICS.observe_ms(key, ms)
    except Exception:
        pass


def _m_search(kind: str, n_results: int, t0: float) -> None:
    """检索类指标一次性记账：调用次数 / 耗时 / 结果数 / 零结果。

    零结果率是这一层最有价值的信号——它同时反映「库里的记忆覆盖不到这个问法」
    与「过滤条件把命中全挡掉了」两种情况，是判断检索是否退化的第一手依据。
    """
    try:
        if not _METRICS_ENABLED:
            return
        _METRICS.inc(f"{kind}_calls")
        _METRICS.observe_ms(f"{kind}_ms", (time.perf_counter() - t0) * 1000.0)
        if n_results <= 0:
            _METRICS.inc(f"{kind}_zero_results")
        else:
            _METRICS.inc(f"{kind}_results_total", n_results)
    except Exception:
        pass


def _metrics_report_lines() -> list[str]:
    """运行时指标摘要（供 memory_stats / memory_audit 复用）。永不抛异常。"""
    try:
        if not _METRICS_ENABLED:
            return ["## 运行时指标", "- 已通过 AI_MEMORY_METRICS=0 关闭"]
        snap = _METRICS.snapshot()
        if not snap:
            return ["## 运行时指标",
                    "- （暂无采样。指标是运行时累积的：检索/读写/锁等待/索引重建各打点，"
                    "每 50 次事件合并落盘一次）"]

        lines = ["## 运行时指标"]

        def _avg(sum_key: str, calls: int) -> float:
            return (snap.get(sum_key, 0.0) / calls) if calls else 0.0

        for kind, label in (("search", "memory_search"),
                            ("smart_search", "memory_smart_search"),
                            ("list", "memory_list")):
            calls = int(snap.get(f"{kind}_calls", 0))
            if not calls:
                continue
            zero = int(snap.get(f"{kind}_zero_results", 0))
            hits = int(snap.get(f"{kind}_results_total", 0))
            avg_ms = _avg(f"{kind}_ms", calls)
            lines.append(
                f"- {label}: 调用 {calls} 次 | 零结果 {zero} 次（{zero / calls:.0%}）"
                f" | 命中均值 {hits / calls:.1f} 条 | 平均耗时 {avg_ms:.1f} ms"
            )

        reads = int(snap.get("read_calls", 0))
        if reads:
            misses = int(snap.get("read_misses", 0))
            lines.append(f"- memory_read: 调用 {reads} 次 | 未命中 {misses} 次"
                         f"（{misses / reads:.0%}）")
        writes = int(snap.get("write_calls", 0))
        if writes:
            creates = int(snap.get("write_creates", 0))
            updates = int(snap.get("write_updates", 0))
            lines.append(f"- memory_write: 调用 {writes} 次（新建 {creates} / 覆盖 {updates}）")

        locks = int(snap.get("lock_calls", 0))
        if locks:
            timeouts = int(snap.get("lock_timeouts", 0))
            lines.append(f"- 互斥锁: 获取 {locks} 次（重入不计）"
                         f" | 超时 {timeouts} 次 | 平均等待 {_avg('lock_wait_ms', locks):.0f} ms"
                         "（跨进程竞争压力）")
        rebuilds = int(snap.get("index_rebuilds", 0))
        if rebuilds:
            lines.append(f"- 索引重建: {rebuilds} 次"
                         "（首次加载或损坏自愈；持续增长说明索引反复失效）")
        return lines
    except Exception:
        return ["## 运行时指标", "- （指标读取失败，已忽略）"]


# ---- SQLite 元数据索引接入 (2026-09-07) -----------------------------
# md 仍是唯一事实源; 索引只镜像元数据用于标题定位/过滤, 正文永远实时读盘。
# 全部索引操作都 try/except 包裹: 索引故障时静默回退原 glob 全扫, 功能不降级。

def _discard_index_file() -> None:
    """丢弃索引文件（含 WAL/SHM 旁文件）。

    索引定位为「运行时缓存，删掉可重建」，因此损坏时直接丢弃是最廉价且无风险的
    恢复手段——md 文件才是唯一事实源，丢弃索引不丢任何数据。

    实现上**优先截断为 0 字节**：0 字节文件在 SQLite 里等同于空数据库，重新连接
    会重新建表，效果与删除完全一致。之所以不首选 unlink，是因为部分受限环境
    （企业安全删除策略、DLP、只读挂载）会拦截删除动作——有的甚至直接拒绝或终止
    调用进程——而截断通常被允许。索引只需失效，不需要真的从磁盘移除。
    """
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_IDX_FILE) + suffix)
        if not p.exists():
            continue
        try:
            p.write_bytes(b"")
        except OSError:
            try:
                p.unlink()
            except OSError:
                pass


def _rebuild_index() -> None:
    """全量重建索引（覆盖根目录 + .archive），成功后清除脏标记。"""
    global _IDX_NEEDS_REBUILD
    _m_inc("index_rebuilds")
    with midx.connect(_IDX_FILE) as conn:
        dirs = [MEMORY_DIR]
        a = MEMORY_DIR / ".archive"
        if a.exists():
            dirs.append(a)

        def _loader():
            for d in dirs:
                for f in _iter_md_files(d):
                    try:
                        meta, _ = _load_memory(f)
                        yield f, meta
                    except Exception:
                        continue

        midx.rebuild(conn, MEMORY_DIR, _loader)
    _IDX_NEEDS_REBUILD = False
    logger.info("index rebuilt: %s", _IDX_FILE.name)


def _is_index_corrupted(e: BaseException) -> bool:
    """判断异常是否代表索引文件**内容损坏**，以区别于并发锁竞争。

    sqlite3 的异常层级：DatabaseError > OperationalError。诸如 "database is
    locked" / "database table is locked" 都是 OperationalError，属于多进程同时
    访问索引时的正常竞争，**绝不能**据此丢弃索引文件——那会把其他进程正在使用的
    索引删掉，引发连锁失败（2026-09-10 并发压力测试实测过这一误判）。
    真正的损坏（"file is not a database"、"malformed" 等）是 DatabaseError 本身
    或其它非 Operational 子类，此时丢弃重建才是正确处置。
    """
    return isinstance(e, sqlite3.DatabaseError) and not isinstance(e, sqlite3.OperationalError)


def _ensure_index() -> None:
    """惰性全量重建(首次访问索引时). 覆盖根目录 + .archive.

    索引文件可能被外部截断或写入垃圾字节（磁盘故障、误操作、未完成的复制）。
    此时 SQLite 连接会抛 DatabaseError —— 本函数丢弃损坏文件并重建一次，使索引
    在无人干预下自愈；若重建仍失败则保持脏标记，退回全库 glob 兜底（功能不降级）。
    锁竞争（OperationalError）不算损坏，保持脏标记下次再试即可。
    """
    global _IDX_NEEDS_REBUILD
    if not _IDX_NEEDS_REBUILD:
        return
    try:
        _rebuild_index()
        return
    except sqlite3.DatabaseError as e:
        if not _is_index_corrupted(e):
            logger.debug("_ensure_index: index busy, will retry later: %s", e)
            return
        logger.warning("index corrupted (%s), discarding & rebuilding: %s", _IDX_FILE.name, e)
    except Exception as e:
        logger.debug("_ensure_index failed: %s", e)
        return
    try:
        _discard_index_file()
        _rebuild_index()
    except Exception as e:
        logger.debug("_ensure_index: rebuild after discard failed: %s", e)


def _idx_sync_path(path: Path) -> None:
    """写盘成功后同步索引行(重新读 meta). 失败静默.

    索引损坏（DatabaseError）时直接丢弃并立即重建，使写路径首次触碰即可自愈；
    其余异常保持静默——索引是运行时缓存，故障绝不能影响主流程。
    """
    global _IDX_NEEDS_REBUILD
    try:
        with midx.connect(_IDX_FILE) as conn:
            meta, _ = _load_memory(path)
            midx.upsert(conn, midx.meta_to_row(MEMORY_DIR, path, meta))
            conn.commit()
    except sqlite3.DatabaseError as e:
        if not _is_index_corrupted(e):
            logger.debug("_idx_sync_path %s: index busy, skipped: %s", path.name, e)
            return
        logger.warning("index corrupted while syncing %s; discarding", path.name)
        _discard_index_file()
        _IDX_NEEDS_REBUILD = True
        _ensure_index()
    except Exception as e:
        logger.debug("_idx_sync_path %s: %s", path.name, e)


def _idx_remove_path(path: Path) -> None:
    """文件被删除/移出库后移除索引行."""
    global _IDX_NEEDS_REBUILD
    try:
        with midx.connect(_IDX_FILE) as conn:
            midx.remove(conn, path.relative_to(MEMORY_DIR).as_posix())
            conn.commit()
    except sqlite3.DatabaseError as e:
        if _is_index_corrupted(e):
            # 索引文件损坏：标记待重建即可（少删一行无伤大雅，下次重建会纠正）
            _IDX_NEEDS_REBUILD = True
        else:
            logger.debug("_idx_remove_path %s: index busy, skipped", path.name)
    except Exception:
        pass


def _resolve_title_via_index(title: str) -> Path | None:
    """索引快速定位: title/stem 命中且磁盘文件 (size,mtime) 新鲜才返回.
    根目录条目优先于 .archive(与原 glob 顺序一致). 未命中返回 None 由调用方回退全扫."""
    try:
        _ensure_index()
        with midx.connect(_IDX_FILE) as conn:
            # A3：permalink 也参与定位 —— 必须与 memory_index.find / _entry_matches
            # 同口径，否则「索引快路径」与「全库回退」对同一入参的判定会不一致。
            rows = conn.execute(
                """SELECT * FROM entries WHERE permalink=? OR title=? OR stem=?
                   ORDER BY (relpath LIKE '.archive/%') ASC, updated DESC""",
                (title, title, title),
            ).fetchall()
            for row in rows:
                p = MEMORY_DIR / row["relpath"]
                if p.exists() and midx.fresh(conn, MEMORY_DIR, row):
                    return p
    except Exception:
        pass
    return None

MEMORY_LOCK = MEMORY_DIR / ".memory.lock"


def _acquire_lock() -> bool:
    """获取跨进程文件锁（含同线程重入）。实现见 locks.py（P3 模块化，2026-09-10）。

    仅在**非重入**的真实抢锁路径上打点：重入是同一线程内的逻辑嵌套（如
    memory_write -> memory_archive），既不产生等待也不反映竞争压力。
    """
    if locks.current_depth() > 0:
        return locks.acquire_lock(MEMORY_LOCK)
    t0 = time.perf_counter()
    ok = locks.acquire_lock(MEMORY_LOCK)
    _m_inc("lock_calls")
    _m_ms("lock_wait_ms", (time.perf_counter() - t0) * 1000.0)
    if not ok:
        _m_inc("lock_timeouts")
    return ok


def _release_lock() -> None:
    """释放跨进程文件锁。实现见 locks.py（P3 模块化，2026-09-10）。"""
    locks.release_lock(MEMORY_LOCK)


mcp = FastMCP("ai-memory", instructions="本地共享 Markdown 记忆库，支持多个 AI 工具读写")


def _rel_display(path: Path) -> str:
    """写入回显用：优先给「相对 vault 根的 posix 路径」。

    阶段二后条目可能落在分类子目录里，只回 `path.name` 会让「到底写哪儿了」
    变得不可见 —— 而落点正是本阶段最容易出错的环节。不在 vault 内（理论上
    不可能，但回显路径绝不能成为抛异常的地方）时退回文件名。
    """
    try:
        return path.relative_to(MEMORY_DIR).as_posix()
    except ValueError:
        return path.name


def _resolve_unique_path(directory: Path, title: str) -> Path:
    """Pick a filename for a new entry, avoiding collisions with files that
    belong to a different title (P0-4).

    ``_safe_filename`` truncates at 80 chars, so two distinct titles can map to
    the same filename and silently overwrite each other. If the target already
    exists on disk AND its frontmatter title differs from ours, append _2/_3/...
    until free. If it exists and the title matches (e.g. a stale stub), reuse it.

    阶段二（2026-09-20）：``_safe_filename`` 的返回值现在可以是含 ``/`` 的
    **相对路径**（标题里写了存放目录）。路径拼接天然支持，但重名后缀必须落在
    相对路径的**父目录**里（见下方注释）。
    """
    base = _safe_filename(title)
    candidate = directory / base
    if not candidate.exists():
        return candidate
    try:
        meta, _ = _load_memory(candidate)
        if _entry_matches(meta, candidate, title):
            return candidate
    except Exception:
        pass
    n = 2
    while True:
        # ⚠️ 追加后缀必须留在**同一个目录**：base 现在可能含 '/'（阶段二），
        # 若写成 directory / f"{candidate.stem}_{n}" 会把重名条目错落到 vault 根，
        # 反而制造出「子目录一份 + 根目录一份」的重复。
        alt = candidate.parent / f"{candidate.stem}_{n}{candidate.suffix}"
        if not alt.exists():
            return alt
        n += 1


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 6,
                        base_delay: float = 0.05) -> None:
    """原子替换目标文件，遇「文件被占用」做有限次退避重试。

    Windows 的 MoveFileEx 要求目标文件具备 DELETE 共享权限，而 Python 的 open
    默认不含该权限——只要有另一个进程（典型：memory_write 在**取锁之前**的全库
    glob+parse 扫描，或另一端的 Obsidian/MCP 客户端）此刻正打开该文件读取，
    os.replace 就会抛 PermissionError(WinError 5)。读写窗口只有毫秒级，重试即可
    把「写盘的替换瞬间」与「他人的读取窗口」错开。
    POSIX 上 os.replace 是原子的、不检查占用，重试分支自然不会触发。

    累计退避约 0.05+0.10+0.15+0.20+0.25 = 0.75s，足够覆盖短暂的读取窗口；
    仍失败则原样抛出，由调用方（写工具）向用户报错，绝不降级为非原子写。
    """
    last_err: OSError | None = None
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except OSError as e:  # 逐次退避重试，需保留最后一次异常用于抛出
            last_err = e
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    assert last_err is not None
    raise last_err


# ---- 写盘持久化 (2026-09-17, B3) ------------------------------------
# 原子替换只保证「读者看不到半写文件」，不保证「内容已落盘」——os.replace 返回时
# 数据可能仍在页缓存里，断电或进程被强杀会留下「文件名对、内容为空」的条目。
# 在 replace 之前对 tmp 做 flush + fsync，把内容推到存储设备，构成完整的
# tmp → fsync → rename 三段式（EverOS 架构文档将此列为 Key guarantee）。
# 代价：每次写入多一次磁盘同步，毫秒级。本库写入频率是会话级而非高并发，
# 该代价换取崩溃场景的完整性是划算的；批量写入可设 AI_MEMORY_FSYNC=0 临时关闭。
_FSYNC_ENABLED = os.environ.get("AI_MEMORY_FSYNC", "1") not in ("0", "false", "False")


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically: temp file in the same dir, then os.replace.

    Plain ``write_text`` truncates the target first; a crash or a concurrent
    reader can observe an empty or half-written file. Writing to a sibling
    temp file and atomically replacing avoids partial reads (fixes P0-1).

    temp 名带上 pid（2026-09-10）：写操作虽已由跨进程锁串行化，但历史上存在未持锁
    的写盘路径（读路径的 access_count flush），两个进程共用同一个 ``<name>.tmp``
    时会互相 unlink/replace 而抛 FileNotFoundError。pid 后缀使各进程的 temp 名
    天然隔离，作为纵深防御，即使将来新增未持锁的写路径也不会互踩。
    替换动作走 _replace_with_retry，抵御 Windows 上「目标文件正被读取」的瞬时占用。

    阶段二（2026-09-20）：目标路径可以位于**尚不存在的分类子目录**里（标题写了
    ``目录/条目``），故先 ``mkdir(parents=True)`` 补齐父目录 —— 这是全库写盘的
    唯一收口，放在这里可让 memory_write / archive / restore / 索引重建一并受益。
    幂等，已存在时零成本。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    if _FSYNC_ENABLED:
        # 显式 open（而非 path.write_text）只为在 close 前拿到 fileno 做 fsync。
        # newline 保持默认 None，与 Path.write_text 语义一致（Windows 下 \n → \r\n）。
        # 切勿改成 newline=""：那会一次性改变全库行尾，属破坏性回归。
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    else:
        tmp.write_text(content, encoding="utf-8")
    try:
        _replace_with_retry(tmp, path)
    except OSError:
        # 替换始终失败：清掉自己的 temp，避免留下无人认领的残骸，再向上抛错
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # 写盘后同步 SQLite 索引(仅库内 md). 索引故障静默, 不影响主流程.
    if path.suffix == ".md":
        _idx_sync_path(path)


_ACCESS_CACHE: dict[str, int] = {}  # title -> pending access_count increments
_ACCESS_FLUSH_THRESHOLD = 10
# ---- B2 索引增量化（2026-10-08）--------------------------------------
# 旧实现只有「整库指纹」一层缓存：任何一个文件变动都会让 _iter_entries 重新
# 解析**全库** frontmatter。而 frontmatter 解析正是写入延迟的主要构成（201 篇
# 规模已可测量）—— 写一条笔记就要付一次全库解析，延迟随库增长线性上升。
#
# 现拆成两层，键一律用 **relpath**（相对 MEMORY_DIR 的 posix 路径）：
#   ① 整库指纹 _DIR_FINGERPRINT：未变 ⇒ 连 os.walk/stat 都省掉，直接回放快照；
#   ② 文件级 _ENTRY_STAT：指纹变了也只重解析 (size, mtime_ns) 真变化的那些文件。
# Windows/NTFS 改写已有文件内容不会 bump 父目录 mtime，故新鲜度判据必须落到
# 文件级 —— 与 _vault_fingerprint 存在的理由完全相同。
# ⚠️ 键由 title 改为 relpath 顺带修掉一处历史遗留：旧实现下不同目录的同名条目
# 会在缓存里互相覆盖元数据。（_ENTRY_CACHE 仅供 _iter_entries 使用，无外部读取方。）
_ENTRY_CACHE: dict[str, tuple] = {}   # relpath -> (path, meta, body)
_ENTRY_STAT: dict[str, tuple] = {}    # relpath -> (size, mtime_ns)：条目级新鲜度
_ENTRY_SNAPSHOT: list[tuple] = []     # 上次返回的条目序列（整库指纹未变时直接回放）
_CACHE_VALID = False
_DIR_FINGERPRINT: tuple = ()  # last seen vault fingerprint, for cross-process cache invalidation
_PARSE_COUNT = 0              # 累计 frontmatter 解析次数（B2 可观测性：增量是否真省了活）
_INDEX_SIG: tuple | None = None  # 上次渲染索引的内容签名（见 _refresh_index）

# ---- B6：生成物隐藏化（2026-10-08）-----------------------------------
# `记忆索引` 是库里**唯一**的纯生成物：每次写入后由 `_refresh_index` 整体重建，
# 正文不含任何人工内容。给它加 `.` 前缀后：
#   ① Obsidian 默认不显示点号开头的文件 ⇒ vault 根的人读区立刻干净；
#   ② 与 EverOS 那套「人看的 / 框架派生的」分层对齐（`.atomic_facts/` 等）。
#
# ⚠️ 三条必须同时成立，否则会造出「同名两条目」或「索引全变死链」：
#   ① **文件名硬编码只允许出现在这一行**，其余一律走 `_index_path()`；
#   ② 生成物**必须仍在 `_iter_entries()` 扫描范围内**（os.walk 不跳点文件）——
#      改名时若顺手把它排除，`memory_read("记忆索引")`、graph、doctor 会一起失联；
#   ③ `memory_rebuild_links` 的 `known` 必须同时收 `path.stem` —— MOC 用的是
#      `_wiki_link`（**渲染 stem**），改名后 stem 变成 `.记忆索引`，只认 title 的
#      判定会当场把新索引判成死链。
_INDEX_FILENAME = ".记忆索引.md"
_INDEX_LEGACY_FILENAME = "记忆索引.md"   # B6 之前的名字，仅供一次性迁移识别
_TAG_AUTO_MAP: dict[str, str] = {
    "python": "python", "py": "python", "flask": "python", "django": "python", "fastapi": "python",
    "javascript": "javascript", "js": "javascript", "typescript": "typescript", "ts": "typescript",
    "node": "nodejs", "node.js": "nodejs",
    "docker": "docker", "container": "docker", "compose": "docker",
    "android": "android", "hyperos": "android", "root": "android",
    "network": "network", "路由器": "network", "路由": "network", "dhcp": "network", "wifi": "network",
    "database": "database", "mysql": "database", "sqlite": "database", "redis": "database",
    "frontend": "frontend", "vue": "frontend", "react": "frontend", "html": "frontend", "css": "frontend",
    "backend": "backend", "api": "backend", "laravel": "backend",
    "tool": "tool", "cli": "tool", "gui": "tool",
    "project": "project",
    "fix": "fix", "bug": "fix", "修复": "fix", "排障": "fix",
    "tutorial": "tutorial", "教程": "tutorial", "guide": "tutorial",
    "linux": "linux", "ubuntu": "linux", "debian": "linux", "bash": "linux",
    "windows": "windows", "win": "windows",
    "security": "security", "渗透": "security", "pentest": "security", "hack": "security",
    "mcp": "mcp", "ai": "ai", "llm": "ai", "gpt": "ai",
    "git": "git", "github": "git",
    "obsidian": "obsidian", "markdown": "markdown",
    "deploy": "deploy", "部署": "deploy",
    "美化": "美化", "theme": "美化", "customization": "美化",
}

CORE_PAGES = {"记忆索引", "近期工作动态", "用户画像", "AI身份档案", "Codex 身份档案",
                  "Claude Code 身份档案", "记忆库总规范", "共享记忆库规则",
                  "记忆半自动整理流程", "AI交互配置", "AI 对话自动归档提示词",
                  "本地共享记忆库 MCP Server", "工具_记忆库MCP服务器"}

# ---- 回收站 (.trash) -------------------------------------------------
# memory_delete 默认软删除：文件移入 .trash/ 而非 unlink，误删可恢复。
# .trash/ 不参与任何检索（读取端统一走 _iter_md_files，它剪掉本目录），取回走
# memory_restore(source="trash")，查看走 memory_list(include_trash=True)。
TRASH_DIR_NAME = ".trash"


def _trash_dir() -> Path:
    return MEMORY_DIR / TRASH_DIR_NAME


# ---- 目录分层支持 (2026-09-19) ---------------------------------------
# 记忆库不再只平铺在 vault 根目录：分类子目录（如 项目/、运维/、技能/）
# 里的条目与根目录条目完全等同 —— 所有「读取端枚举」统一收敛到下面这个
# 函数。此前散落的 21 处手写 `MEMORY_DIR.glob("*.md")` 全是非递归的，
# 一旦有子目录就会集体漏读，故一律改走这里。
#
# RESERVED_DIRS 内的目录，其整棵子树都不进检索范围：
#   * .archive / .trash —— 独立功能区，各有专用工具（memory_restore 等）
#   * .git / .obsidian  —— 版本库与 Obsidian 配置
#   * ai-memory         —— vault 根下那份代码副本，自带 README 等 .md，
#                          不排除的话会被当成记忆条目混进检索
RESERVED_DIRS = frozenset({
    ".archive", ".trash", ".git", ".obsidian",
    "ai-memory", "__pycache__", ".ruff_cache", ".pytest_cache",
})


def _iter_md_files(base: Path | None = None) -> list[Path]:
    """递归枚举 base（默认 MEMORY_DIR）下全部 .md，跳过 RESERVED_DIRS。

    返回**已排序**结果，排序键为「层级深度 + posix 相对路径」⇒ 根目录条目
    先于子目录条目、同级按字典序。之所以强调确定性：调用方普遍依赖稳定次序
    （`_iter_entries` 的缓存填充序、`memory_search` 的同分 tie-break），
    而 `os.walk` 的目录序遍历顺序在不同文件系统上并无保证。

    ⚠️ 起点本身不受 RESERVED_DIRS 限制（只有它的**子目录**会被剪枝）——
    这正是 `_iter_md_files(MEMORY_DIR / ".archive")` 能取到归档条目的原因。
    """
    root = MEMORY_DIR if base is None else base
    if not root.exists():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 就地改写 dirnames 才能真正剪枝（os.walk 依据它决定是否下探）
        dirnames[:] = [d for d in dirnames if d not in RESERVED_DIRS]
        for fname in filenames:
            if fname.endswith(".md"):
                found.append(Path(dirpath) / fname)
    try:
        found.sort(key=lambda p: (p.relative_to(root).as_posix().count("/"),
                                  p.relative_to(root).as_posix()))
    except ValueError:
        found.sort()
    return found


# ---- 阶段二：memory_write 支持子目录 (2026-09-20) ---------------------
# 标题允许写成 `目录/条目`（如 `项目/记忆库MCP/条目`），落盘位置由
# `text_utils.safe_filename()` 逐段消毒后决定。这里补的是 **政策**校验：
# text_utils 只负责「什么字符非法」，而「哪些目录不允许写」是 vault 的约定。
#
# 为什么要单独校验而不是靠消毒兜住：
#   * 消毒会把 `.archive/x` 变成 `archive/x`（`strip("._")` 吃掉前导点），
#     于是「写进保留目录」会**静默变成写进同名普通目录**，用户看不出问题；
#   * `..` 段被消毒消解后同样无声 —— 显式报错才符合「宁可拒绝、不要惊喜」。
TITLE_PATH_MAX_DEPTH = 3   # 目录层数上限（`项目/记忆库MCP/条目` = 2 层目录，合规）


def _title_path_error(title: str) -> str | None:
    """标题含 ``/``（即指定了存放子目录）时的政策校验。

    返回错误文案；``None`` 表示放行。仅当标题含 ``/`` 时才检查，因此**不影响**
    存量扁平条目的读写 —— 含 ``/`` 的历史标题（如 ``轻装时代(QZSD)灯架/三脚架产品谱系``）
    首段不是保留目录、层级也浅，同样放行。
    """
    if "/" not in title:
        return None
    if title.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", title):
        return "错误：标题不能以分隔符开头，也不能包含盘符（会写到 vault 之外）。"
    raw_segs = [s.strip() for s in title.split("/")]
    if any(s in (".", "..") for s in raw_segs):
        return "错误：标题不能包含 '.' 或 '..' 路径段。"
    # 同时按**消毒后**的形态再看一遍（防「原始段不保留、消毒后恰好撞上保留目录」）
    raw_dirs = [s for s in raw_segs if s][:-1]
    safe_dirs = [s for s in os.path.dirname(_safe_filename(title)).split("/") if s]
    for dirs, tag in ((raw_dirs, "原始"), (safe_dirs, "归一化后")):
        if len(dirs) > TITLE_PATH_MAX_DEPTH:
            return (f"错误：目录层级过深（{tag} {len(dirs)} 层，最多 {TITLE_PATH_MAX_DEPTH} 层）。")
        hit = [d for d in dirs if d in RESERVED_DIRS]
        if hit:
            return (f"错误：'{hit[0]}' 是保留目录（{tag}名称命中），不能作为记忆条目的存放目录。"
                    f"保留目录：{', '.join(sorted(RESERVED_DIRS))}。")
    return None


# ---- 阶段三：写入端自动归类 (2026-09-20) ------------------------------
# 多级目录落地后，新条目仍靠写入方在 title 里手写 ``目录/条目``；一旦漏写，
# 笔记就静静躺在 vault 根 —— 扁平格局重新长回来。阶段二闭合的是「能不能写」，
# 本段闭合的是「会不会忘」。
#
# 做法：复用既有的语义分桶 ``_entry_bucket()``（原本只服务索引草稿），把它的
# 结果接到写入路径上。因此「索引里的分组」与「磁盘上的目录」同源，不会出现
# 索引归 A 桶、文件却躺在 B 目录这种自相矛盾。
#
# 四档策略，读环境变量 ``AI_MEMORY_DIR_POLICY``：
#   off    —— 不介入，落 vault 根（阶段二及之前的行为）
#   warn   —— 落点不变，但在返回消息里给出建议目录   ← **默认**
#   auto   —— 自动落 ``规范目录/条目.md``
#   strict —— 无规范目录可归时**拒绝写入**，要求写入方显式指定
#
# 默认取 warn 而非 auto，是**刻意保守**，依据是阶段三首轮的实测回归：把 auto
# 设为默认后，**11 个既有测试套件失败**（18 套里），失败主体是辅助函数按
# `<根>/<标题>.md` 读文件 —— 它们编码的正是「新条目落在根」这一心智模型，
# 而非实现细节。既然既有代码、既有习惯、多个客户端都依赖它，默认值就不该动。
# 而自动归类的判据（``_entry_bucket``）本身是**关键词启发式**：它读的是
# ``_auto_suggest_tags()`` 自动补出来的 tags，两个启发式叠加会让落点对写入方
# 不可预测（实测「测试_索引命中」因正文含 mcp 字样被自动补 tag，进而落到 工具/）。
# ⇒ 先用 warn 观察一段时间，确认误判率可接受，再按需切 auto。
# 要立刻启用自动归类：设 ``AI_MEMORY_DIR_POLICY=auto``。
#
# 2026-10-09 补：语义桶表只覆盖 11 个规范目录，而 vault 里另有十余个**语义同样
# 明确**的既有目录（故障 / 排坑 / 排查 / 经验 / 调研 / 核验 / 技能 / 移动端 /
# 素材 / 需求 / 偏好 / 技术）。实测 22 篇根目录笔记里 **8 篇**因此被兜底成「其他」
# 桶，判准率仅 11/22 —— 即「把语义清晰的笔记扫进垃圾桶」。故新增一条**前缀命中
# 既有目录**的判定（`_dir_prefix_hit`），插在语义桶表之前：`故障_XX` → `故障/`。
# 该规则不动 BUCKET_DIRS（规范目录清单不变），也不搬迁任何存量条目；传
# ``dirs=None`` 的旧调用形态行为与改动前逐字一致。存量归位走一次性搬迁。
DIR_POLICY_ENV = "AI_MEMORY_DIR_POLICY"
DIR_POLICIES = ("off", "warn", "auto", "strict")
DEFAULT_DIR_POLICY = "warn"

# ⚠️ 三条边界（均来自 2026-09-20 的实测教训）：
#   1. 只作用于**新建**。已存在标题的更新一律就地更新、绝不搬运 —— 否则存量
#      16 篇根目录核心页与 20 余个历史目录会被无谓搅动。
#   2. 落盘目录**不写进 frontmatter 的 title**。title 始终是裸条目名：早前有
#      4 篇 title 被写成 ``目录/条目`` 形式，导致其入链全部失效。
#   3. 标题已显式含 ``/`` 时**完全尊重写入方**，不做二次推断。

# 语义桶 -> 规范目录。只映射**语义无歧义**的目录：vault 里另有十余个历史遗留
# 目录（故障 / 排坑 / 排查 / 核验 / 经验 / 环境 / 技术 / 图片 / 调研 / 需求 /
# 偏好 / 素材 / 移动端 / Codex），彼此语义重叠（仅故障·排坑·排查 就有三个近义）。
# 本表刻意**不再向它们写新条目** ⇒ 旧目录只承接存量、自然冻结，新笔记一律进
# 规范目录，碎片随存量归档而收敛。要收编旧目录请走一次性搬迁，不要在这里做。
BUCKET_DIRS: dict[str, str | None] = {
    "时间线": "时间线",
    "项目": "项目",
    "工具": "工具",
    "运维": "运维",
    "修复": "修复",
    "规则": "规则",
    "身份与配置": "身份",
    "模板": "模板",
    "AI相关": "技术",
    "其他": "其他",
    "索引": None,          # 核心页（记忆索引），留在 vault 根
}



# ---- 归类目标目录集 (2026-10-09) --------------------------------------
# 前缀命中既有目录会产生「目录名桶」（故障 / 核验 / 经验 …），它们不在规范序里。
# 渲染循环只遍历序 ⇒ 不动态补充就会**静默丢条目**（索引里少链接却不报错），
# 故一律走 _bucket_order() 取序。
_BUCKET_ORDER = ("索引", "规则", "身份与配置", "项目", "工具", "运维", "修复",
                 "时间线", "模板", "AI相关", "其他")


def _bucket_order(groups: dict[str, list]) -> list[str]:
    """渲染顺序：规范桶（仅有内容的）按固定序在前，其余桶按字典序附后。"""
    extra = sorted(b for b in groups if b not in _BUCKET_ORDER)
    return [b for b in _BUCKET_ORDER if groups.get(b)] + extra


def _known_dirs() -> frozenset[str]:
    """vault 根下**可作归类目标**的一级目录名集（滤掉保留目录与点目录）。

    批量判定时应在循环外取一次复用 —— 本函数每次都扫一遍 vault 根目录。
    """
    try:
        entries = list(MEMORY_DIR.iterdir())
    except OSError:
        return frozenset()
    return frozenset(
        p.name for p in entries
        if p.is_dir() and not p.name.startswith(".") and p.name not in RESERVED_DIRS
    )


def _core_variant(title: str) -> str:
    """把标题里的 `_` 视作空格归一化，用于核心页豁免的宽松匹配。

    全库核心页文件名用 `_` 连接（`Codex_身份档案`），而 CORE_PAGES 里写的是
    空格形态（`Codex 身份档案`）—— 不归一化就会漏判，进而被前缀规则误送进
    同名目录（`Codex/`）。
    """
    return title.replace("_", " ").strip()


def _dir_prefix_hit(title: str, dirs: frozenset[str]) -> str | None:
    """标题前缀命中**同名既有目录**时返回该目录名，否则 ``None``。

    `故障_XX` → `故障/`、`核验_YY` → `核验/`。与 ``BUCKET_DIRS`` 的分工：后者是
    写死的规范目录表（11 个桶），本函数让 vault 里**既有**的分类目录自动成为
    归类目标，不必逐个登记；传空集 ``dirs`` 时恒不命中。

    三条边界：
      1. 只取第一个 `_` 之前的一段 —— 无 `_`、或该段不是目录名即不命中；
      2. 核心页（含 `_`/空格变体）一律豁免，避免 `Codex_身份档案` 被送进 `Codex/`；
      3. 目录存在性来自调用方传入的 ``dirs``，本函数**不做任何 IO**（便于测试）。
    """
    if title in CORE_PAGES or _core_variant(title) in CORE_PAGES:
        return None
    stem = title.split("_", 1)[0].strip()
    if not stem or stem == title or stem not in dirs:
        return None
    return stem



def _dir_policy() -> str:
    """当前目录策略；未设置或取值非法时回落 ``DEFAULT_DIR_POLICY``。

    非法值**不报错**（策略是运维开关，不该让写入失败），但也不做近似匹配：
    一律按默认档处理，且返回值上看不出差别 —— 故把这条明确写进文档。
    """
    raw = (os.environ.get(DIR_POLICY_ENV) or "").strip().lower()
    return raw if raw in DIR_POLICIES else DEFAULT_DIR_POLICY


def _suggest_dir(title: str, tags: list[str],
                 dirs: frozenset[str] | None = None) -> str | None:
    """推断条目的规范存放目录；``None`` 表示应留在 vault 根。

    核心页（``CORE_PAGES``）一律留根 —— 它们是全库导航入口，散进子目录会让
    「根目录 = 书架第一层」这个约定失效，而历史双链多以根路径书写。

    判定顺序（2026-10-09 起）：
      ① 核心页豁免（含 `_`/空格变体）；
      ② `_entry_bucket` 内的**标题前缀命中既有目录**（`故障_XX` → `故障/`）；
      ③ 语义桶表 ``BUCKET_DIRS`` 兜底。
    ``dirs`` 省略时内部取一次 ``_known_dirs()``；批量调用方应传入复用。
    """
    if title in CORE_PAGES or _core_variant(title) in CORE_PAGES:
        return None
    dirs = _known_dirs() if dirs is None else dirs
    bucket, _reason = _entry_bucket({"title": title, "tags": tags},
                                    Path(_safe_filename(title)), dirs)
    # 前缀命中时桶名**就是**目录名；否则按桶表映射（索引桶 -> None 即留根）。
    if bucket in dirs:
        return bucket
    return BUCKET_DIRS.get(bucket)


def _free_path_in(directory: Path, name: str) -> Path:
    """在 directory 内取一个**严格空闲**的路径（重名则追加 _2 / _3 ...）。

    与 `_resolve_unique_path` 的区别：后者在「已存在文件且 frontmatter title
    相同」时会复用该路径（覆盖语义，服务于 memory_write 的 upsert）；回收站
    场景下同名的两条软删除必须并存，所以这里一律避让，绝不覆盖。
    """
    dest = directory / name
    n = 2
    while dest.exists():
        dest = directory / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return dest


def _maybe_auto_archive(title: str, path: Path) -> None:
    """Auto-archive entries that are cold enough."""
    if title in CORE_PAGES:
        return
    try:
        meta, body = _load_memory(path)
        if meta.get("tier") != "cold":
            return
        # Only archive if tier is already cold
        memory_archive(title)
    except Exception as e:
        logger.warning("_maybe_auto_archive(%s): %s", title, e)


# ---- 冲突 / 重复主动防护（演进 #1） ---------------------------------
# memory_write 写入后据此做「疑似重复/冲突」提示（不阻断写入）。
# 相似度用字符 bigram 集合的 Jaccard 系数 —— 零依赖、对中文天然友好
# （FTS5 分词坑未解，但 bigram 不需要分词，直接滑窗取相邻字符对）。
# B5（2026-10-08）：两个相似度阈值已外置到 memory.toml（`dup_title_jaccard` /
# `dup_body_jaccard`）。下列常量只作**向后兼容别名与文档性默认值**，运行时真值
# 一律现取（见 `_find_duplicate_hints`），否则改配置不生效。
_DUP_TITLE_JACCARD = memory_config.DEFAULTS["dup_title_jaccard"]
_DUP_BODY_JACCARD = memory_config.DEFAULTS["dup_body_jaccard"]
_DUP_MAX_HINTS = 3          # 最多返回几条提示，避免刷屏（条数不属阈值类，仍内联）


def _bigrams(text: str) -> set[str]:
    """字符 bigram 集合（用于相似度）。空/过短文本返回空集。"""
    t = re.sub(r"\s+", "", text)
    if len(t) < 2:
        return set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度；空集时返回 0（避免除零）。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _find_duplicate_hints(title: str, content: str,
                          existing: list[tuple[Path, dict[str, Any], str]]) -> list[str]:
    """在现有条目里找疑似重复/冲突，返回最多 _DUP_MAX_HINTS 条提示。

    匹配逻辑（阈值取自 memory.toml，B5）：
      1. 标题 bigram Jaccard >= `dup_title_jaccard`（默认 0.6）→ 「疑似重复标题」
      2. 否则正文 bigram Jaccard >= `dup_body_jaccard`（默认 0.5）→ 「疑似重复/冲突内容」
    排除：标题完全相同（那就是 upsert 自己，同名写入属于更新而非重复）。
    纯提示，不改任何数据 —— 是否合并/标注 supersedes 由调用方（AI/主人）决定。
    """
    if not title or not content:
        return []
    t_bi = _bigrams(title)
    c_bi = _bigrams(content)
    if not t_bi and not c_bi:
        return []

    # 阈值在循环外取一次：memory_config 自带 TTL 缓存，但逐候选现取仍是白费开销。
    title_thr = memory_config.get("dup_title_jaccard")
    body_thr = memory_config.get("dup_body_jaccard")

    hints: list[tuple[float, str]] = []
    seen_titles: set[str] = set()
    for f, meta, body in existing:
        other = _entry_title(meta, f)
        if not other or other in seen_titles:
            continue
        if other == title:
            continue                      # upsert 自己（同名即更新，不算重复）
        # 2026-09-11 修正：核心页（CORE_PAGES）同样参与比对。
        # 原先无条件跳过核心页，结果「新建『近期工作动态记录』而不是更新原页」
        # 这类最典型的重复写入恰恰静默通过 —— 与防护目标正好背离。
        # 真正需要豁免的只有"完全同名"，已由上面的 other == title 分支处理。
        seen_titles.add(other)

        o_t_bi = _bigrams(other)
        title_sim = _jaccard(t_bi, o_t_bi)
        body_sim = _jaccard(c_bi, _bigrams(body))

        if title_sim >= title_thr:
            kind = "疑似重复标题"
            score = title_sim
        elif body_sim >= body_thr:
            kind = "疑似重复/冲突内容"
            score = body_sim
        else:
            continue
        hints.append((score, f"{kind}：[[{other}]]（相似度 {score:.0%}）"))

    hints.sort(key=lambda x: -x[0])
    return [h for _, h in hints[:_DUP_MAX_HINTS]]


def _flush_access_counts() -> int:
    """Flush pending access_count increments to disk. Returns number of entries flushed.

    P0-1 (2026-09-07): 只累加 access_count，绝不改写 updated。
    读取操作 ≠ 内容更新——若 flush 时刷新 updated，常被检索的旧笔记会被标记
    为"今天更新"，从而架空 30 天滚动归档 / heat_score / memory_recent 的语义。
    updated 只允许在真正修改内容或元数据时由写入端更新。

    P0-2 (2026-09-10, 由并发测试在 CI 上暴露): 本函数会**写盘**，因此必须与写
    操作互斥。它由读路径（memory_read 在 pending 达阈值时）调用，而读路径本身
    不持锁——若无锁保护，并发写同一文件会产生两种真实故障：
      ① 两个进程共用同一个 ``<name>.md.tmp`` → 互相 unlink/replace，
         抛 FileNotFoundError（CI 实测复现）；
      ② 读侧用「读盘快照」整体覆盖文件，可能吞掉写侧刚提交的正文（lost update）。
    写路径（memory_write 的 finally）调用本函数时已持锁，靠 locks 的可重入性
    零成本复用；读路径则在此真正获取锁——flush 每 10 次读才触发一次，开销可忽略。
    拿不到锁时保持 pending 返回 0，下次再 flush，绝不无锁写盘。
    """
    if not _ACCESS_CACHE:
        return 0
    if not _acquire_lock():
        return 0
    try:
        return _flush_access_counts_locked()
    finally:
        _release_lock()


def _flush_access_counts_locked() -> int:
    """写回 access_count（调用方必须已持有跨进程锁）。"""
    flushed = 0
    for f in _iter_md_files():
        try:
            meta, body = _load_memory(f)
            title = _entry_title(meta, f)
            if title in _ACCESS_CACHE:
                old_count = meta.get("access_count", 0)
                if not isinstance(old_count, int):
                    old_count = 0
                meta["access_count"] = old_count + _ACCESS_CACHE.pop(title)
                # 本路径写出的就是当前结构（含 scope/status 等 P1 字段），故标注版本
                meta["schema_version"] = SCHEMA_VERSION
                new_fm = _dump_yaml_frontmatter(meta)
                _atomic_write_text(f, f"---\n{new_fm}\n---\n\n{body}")
                flushed += 1
        except Exception as e:
            logger.debug("_flush_access_counts: skipped %s: %s", f.name, e)
    _ACCESS_CACHE.clear()
    return flushed

def _auto_suggest_tags(content: str, existing_tags: list[str]) -> list[str]:
    """Suggest tags from content when none are provided."""
    if existing_tags:
        return existing_tags
    content_lower = content.lower()
    suggested: set[str] = set()
    for keyword, tag in _TAG_AUTO_MAP.items():
        if keyword in content_lower:
            suggested.add(tag)
    return sorted(suggested)

def _auto_link_titles(content: str, links: list[str]) -> list[str]:
    """[DEPRECATED 2026-09-06] 自动补链已停用，见 commit c6b5419。

    曾经把正文中出现的已知标题自动补成 links，制造大量冗余（中心页背上
    30+ 条非显式链接）。links 唯一事实源改为正文显式 [[双链]]，本函数
    保留仅为兼容历史调用，直接原样返回，不做任何补链。
    """
    return list(links)

def _locked_write(fn):
    """Decorator to wrap write operations with lock.

    Uses functools.wraps so FastMCP registers the tool under the original
    function name (not "wrapper"); otherwise @mcp.tool() would register every
    locked tool as a single "wrapper" tool that overwrites the previous one.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        locked = _acquire_lock()
        try:
            return fn(*args, **kwargs)
        finally:
            if locked:
                _release_lock()
    return wrapper

def _entry_title(meta: dict[str, Any], path: Path) -> str:
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return path.stem


def _entry_matches(meta: dict[str, Any], path: Path, key: str) -> bool:
    """条目是否匹配 key：**permalink → title → stem** 三级（A3, 2026-10-08）。

    抽成单一入口的理由：全库原先有 9 处各自手写
    `_entry_title(meta, f) == key or f.stem == key`，任何一处漏改都会让「旧标识
    可定位」在不同工具上表现不一致 —— 那是最难排查的一类不一致（同一入参有时命中、
    有时落到全库回退）。

    permalink 是「首次写入时冻结的标识」，所以**文件被改名后**（stem 变）仍能用旧
    标识命中。此前条目的唯一身份是 title/stem，改名即失联；这是 A3 要解决的核心问题。

    ⚠️ 有意**不**用于「同名判定」场景（如 `_find_duplicate_hints` 里「同名即
    upsert 自己」）：那里判断的是「标题是否一样」，掺入 permalink 会让重复检测
    的语义变味。
    """
    if not key:
        return False
    pl = meta.get("permalink")
    if isinstance(pl, str) and pl and pl == key:
        return True
    return _entry_title(meta, path) == key or path.stem == key


def _wiki_link(path: Path, title: str | None = None) -> str:
    """渲染一条指向 path 的 Obsidian 双链。

    Obsidian 解析双链时以**文件名 stem** 为准，因此 link target 必须是
    path.stem，人类可读标题只能放别名位（`[[stem|title]]`）。早期实现直接输出
    `[[title]]`：凡标题含空格等会被 _safe_filename 归一化的字符 —— 例如标题
    "2026-07-06 记忆库半自动审计" 对应文件 2026-07-06_记忆库半自动审计.md ——
    链接一律变成渲染死链，而且每次 _refresh_index 都会把手工修复覆盖回去。
    """
    stem = path.stem
    if title and title != stem:
        return f"[[{stem}|{title}]]"
    return f"[[{stem}]]"

def _entry_tags(meta: dict[str, Any]) -> list[str]:
    tags = meta.get("tags", [])
    if isinstance(tags, list):
        return [str(tag) for tag in tags if str(tag).strip()]
    if isinstance(tags, str) and tags.strip():
        return [tags.strip()]
    return []

def _entry_bucket(meta: dict[str, Any], path: Path,
                  dirs: frozenset[str] | None = None) -> tuple[str, str]:
    title = _entry_title(meta, path)
    tags = {tag.lower() for tag in _entry_tags(meta)}
    title_lower = title.lower()

    if (
        title == "记忆索引"
        or "index" in tags
        or "moc" in tags
        or "navigation" in tags
        or title.endswith("索引")
    ):
        return "索引", "导航入口或总索引"

    # 前缀命中既有分类目录（2026-10-09）—— 位置刻意排在 tags 判定**之前**：
    # tags 可能被 `_auto_suggest_tags()` 自动补出来，可靠性低于写入方手写的标题
    # 前缀。`dirs` 为 None（旧调用形态）时整体跳过，行为与改动前一致。
    if dirs:
        hit = _dir_prefix_hit(title, dirs)
        if hit:
            return hit, f"标题前缀命中既有目录 {hit}/"

    if (
        title == "近期工作动态"
        or "timeline" in tags
        or "activity" in tags
        or "log" in tags
        or title.startswith("2026-")
    ):
        return "时间线", "按日期记录阶段性工作"

    if title.startswith("项目_") or "project" in tags:
        return "项目", "长期项目记忆"

    if title.startswith("工具_") or "tool" in tags or "mcp" in tags or "infra" in tags:
        return "工具", "工具、服务或基础设施"

    if title.startswith("运维_") or "network" in tags or "router" in tags or "docker" in tags:
        return "运维", "运维与环境配置"

    if title.startswith("修复_") or "fix" in tags or "issue" in tags:
        return "修复", "故障修复与排障"

    if (
        title.startswith("规则_")
        or title in {"共享记忆库规则", "记忆库总规范", "AI 对话自动归档提示词"}
        or "workflow" in tags
        or "standard" in tags
        or "structure" in tags
        or "memory" in tags
        or "prompt" in tags
    ):
        return "规则", "流程、规范与模板"

    if (
        title in {"用户画像", "AI身份档案", "Codex 身份档案", "Claude Code 身份档案", "AI交互配置"}
        or "identity" in tags
        or "persona" in tags
        or "preference" in tags
    ):
        return "身份与配置", "用户、AI 身份与交互设定"

    if title.startswith("模板_") or "template" in tags:
        return "模板", "可复用模板"

    if title_lower.startswith("ai") or "ai" in tags:
        return "AI相关", "与 AI 身份、配置或协作相关"

    return "其他", "暂未明确归类"

def _today_utc() -> str:
    """当前 UTC 日期（YYYY-MM-DD），用于 P0 的 verified_at 字段。"""
    return datetime.now(timezone.utc).date().isoformat()


def _apply_meta_defaults(meta: dict[str, Any]) -> dict[str, Any]:
    """为 frontmatter 缺失 P0/P1 字段的旧笔记注入默认值（内存态）。

    向后兼容的关键：全库 140+ 篇历史笔记不含 type/confidence/verified/
    scope/status/schema_version，若要求「先迁移再使用」则改动面过大。这里在
    **读取端**统一补默认值，使新逻辑对旧笔记立即生效，而磁盘文件保持原样；
    旧笔记仅在下次被显式写入时渐进补齐。

    默认语义：
      type=fact / confidence=medium / verified=false —— 未声明来源与验证状态的
        内容按「中等可信的事实」看待，既不轻信也不丢弃。
      scope=global —— 未声明作用域的按全局记忆对待（不会因默认 project 而漏检）。
      status=active；但**带 archived_to 的归档 stub 默认判为 archived**，否则归档
        条目会被误标为生效中，架空「默认不检索归档」的生命周期约定。

    schema_version 刻意**不在此注入**：它是「这份文件实际按哪版结构写出」的标注，
    由写入端（build_frontmatter / update_metadata / access_count flush）在真正写出
    新结构时置为 SCHEMA_VERSION。缺失即代表旧结构，读取方用 _schema_version() 取值。
    """
    if not isinstance(meta, dict):
        return meta
    if meta.get("type") not in MEMORY_TYPES:
        meta["type"] = DEFAULT_MEMORY_TYPE
    if meta.get("confidence") not in CONFIDENCE_LEVELS:
        meta["confidence"] = DEFAULT_CONFIDENCE
    if not isinstance(meta.get("verified"), bool):
        meta["verified"] = DEFAULT_VERIFIED
    if meta.get("scope") not in SCOPES:
        meta["scope"] = DEFAULT_SCOPE
    if meta.get("status") not in LIFECYCLE_STATUSES:
        meta["status"] = "archived" if meta.get("archived_to") else DEFAULT_STATUS
    conflicts = meta.get("conflicts")
    if conflicts is not None and not isinstance(conflicts, list):
        meta["conflicts"] = [str(conflicts)]
    return meta


def _schema_version(meta: dict[str, Any]) -> int:
    """返回条目的 frontmatter 结构版本；缺失（旧笔记）视为 1。"""
    v = meta.get("schema_version")
    return v if isinstance(v, int) else 1


# ---- B4（2026-10-08）：frontmatter 键分层 ---------------------------------
# 「写入端会产出什么」即本库的 schema 契约（`yaml_io.build_frontmatter` 是唯一
# 写出源）。据此把磁盘上出现的键分成三层，**不再另立一份 schema 文件** ——
# 两份真相必然分叉，而代码本身就是最准的那份。
#
#   core   任何版本都必须有（v1 时代就已存在）。缺 = 真实缺陷。
#   v2     仅 schema_version>=2 承诺写出。v1 条目缺它们属**预期**，是迁移清单。
#   opt    按需写出（有值才写）。缺失不是缺陷。
_SCHEMA_CORE_KEYS = ("title", "tags", "created", "updated", "tier", "access_count")
_SCHEMA_V2_KEYS = ("type", "confidence", "verified", "scope", "status", "schema_version")
_SCHEMA_OPTIONAL_KEYS = ("permalink", "verified_at", "project", "supersedes",
                         "conflicts", "source_context", "source", "summary",
                         "links", "version")
# `archived_to` 由 memory_archive 写入，不属 frontmatter 契约但属已知键 ——
# 漏掉它会把全部归档 stub 误报成「未知键」，把真信号淹没在噪声里。
_SCHEMA_KNOWN_EXTRA = ("archived_to",)
_SCHEMA_ALL_KNOWN = frozenset(
    _SCHEMA_CORE_KEYS + _SCHEMA_V2_KEYS + _SCHEMA_OPTIONAL_KEYS + _SCHEMA_KNOWN_EXTRA
)


def _frontmatter_key_lines(path: Path) -> dict[str, int]:
    """返回该文件 frontmatter 内的 `键 -> 行号(1-based)`，用于把差异定位到行。

    只扫首块 ``--- … ---``，正文里形如 ``key:`` 的行不会被误收。解析失败或文件
    没有 frontmatter 时返回空 dict（调用方据此退化为「不附行号」）。
    """
    marks: dict[str, int] = {}
    try:
        text = _read_text(path)
    except Exception:
        return marks
    if not text.startswith("---"):
        return marks
    for i, ln in enumerate(text.splitlines(), 1):
        if i == 1:
            continue
        if ln.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", ln)
        if m:
            marks.setdefault(m.group(1), i)
    return marks


def _credential_badge(meta: dict[str, Any]) -> str:
    """把可信度 / 生命周期字段压成紧凑标记，供搜索/列表行内展示。

    例：` | type=decision conf=high ✓verified | scope=project(记忆库MCP)
    status=deprecated ⇢supersedes ⚠conflicts(2)` —— 让调用方在一行摘要里就能看出
    「这是决策、高可信、已被用户确认、属于某项目、已被取代且与两条笔记冲突」，
    无需再逐条 memory_read。

    默认值（scope=global / status=active）刻意不显示，抑制绝大多数条目的噪声。
    """
    badge = (f" | type={meta.get('type', DEFAULT_MEMORY_TYPE)}"
             f" conf={meta.get('confidence', DEFAULT_CONFIDENCE)}")
    if meta.get("verified"):
        badge += " ✓verified"
    scope = meta.get("scope", DEFAULT_SCOPE)
    if scope != DEFAULT_SCOPE:
        project = meta.get("project")
        badge += f" | scope={scope}" + (f"({project})" if project else "")
    status = meta.get("status", DEFAULT_STATUS)
    if status != DEFAULT_STATUS:
        badge += f" status={status}"
    if meta.get("supersedes"):
        badge += " ⇢supersedes"
    conflicts = meta.get("conflicts")
    if conflicts:
        badge += f" ⚠conflicts({len(conflicts)})"
    return badge


def _passes_lifecycle_filters(meta: dict[str, Any], status: str | None = None,
                              scope: str | None = None, project: str | None = None) -> bool:
    """按生命周期 / 作用域判定条目是否参与本次检索。

    默认（status=None）**排除 archived**：归档的语义就是「退出日常检索」，否则
    归档条目会持续污染结果；stale / deprecated 仍保留可见（它们需要被看到，只是
    附带状态标记）。显式传 status="any" 可查看全部，传具体值则精确过滤。
    scope / project 传 "any" 表示不过滤。
    """
    if status is None:
        if meta.get("status", DEFAULT_STATUS) == "archived":
            return False
    elif status != "any":
        if meta.get("status", DEFAULT_STATUS) != status:
            return False
    if scope is not None and scope != "any":
        if meta.get("scope", DEFAULT_SCOPE) != scope:
            return False
    if project is not None and str(meta.get("project") or "") != project:
        return False
    return True


def _filter_hint(hidden: int) -> str:
    """被生命周期 / 作用域过滤隐藏了条目时的提示尾巴（避免用户误以为记忆不存在）。"""
    if not hidden:
        return ""
    return (f"\n\n（另有 {hidden} 条命中因生命周期或作用域过滤被隐藏；"
            f"可用 status=\"any\"、status=\"archived\" 或对应 scope 显式查看）")


# ---- C1：工具输出可选 JSON（2026-10-08）-------------------------------
# 只对**本质是「同构记录列表」的只读工具**开放 `output_format`（默认 "text"）：
#   memory_read / memory_search / memory_list / memory_recent / memory_orphans
#
# 为什么不给全部 24 个工具都加：
#   ① 报告型工具（stats / doctor / audit / schema_diff / context / graph …）的产出
#      是**叙事**：分节标题 + 计数说明 + 提示语。把它整体转 JSON 等于另立一套真相，
#      两份结构必然分叉 —— 这正是本仓反复踩过的坑（schema 文件 / 双链口径）。
#   ② 写工具（write / update / archive / delete …）返回的是**操作回执**（一句话
#      结果），结构化没有意义。
#   ③ 于是范围**刻意**只覆盖「文本本身就是记录的渲染」的那几个：JSON 是文本的
#      **同源投影**，而不是第二份实现。
#
# 口径：JSON 与文本由**同一批局部变量**组装（不是各写一遍）；非法取值**报错**而不
# 静默回落 text —— 脚本化调用最怕「以为拿到 JSON、实际拿到一句中文」，那样
# json.loads 会在很远的地方才炸，排查成本极高。
_OUTPUT_FORMATS = ("text", "json")


def _norm_output_format(value: str | None) -> str:
    """校验 output_format 取值；非法值抛 ValueError（调用方转成错误文本）。"""
    v = (value or "text").strip().lower()
    if v not in _OUTPUT_FORMATS:
        raise ValueError(f"output_format 只能是 {' / '.join(_OUTPUT_FORMATS)}，收到 {value!r}")
    return v


def _json_out(payload: dict[str, Any]) -> str:
    """统一 JSON 输出：中文不转义、缩进 2（便于人眼与脚本共用）。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _truncate_body(body: str, max_chars: int) -> tuple[str, bool, int]:
    """按预算截断正文。返回 `(截断后的正文, 是否截断, 原长)`。

    **文本与 JSON 共用**这一步，但截断说明只在文本侧拼（JSON 侧用 `truncated` /
    `total_chars` 两个字段表达）—— 否则脚本拿到正文后还得正则剥掉那句中文提示。
    """
    total = len(body)
    if max_chars and max_chars > 0 and total > max_chars:
        return body[:max_chars], True, total
    return body, False, total


def _body_notice(total: int, max_chars: int) -> str:
    """文本形态的截断说明尾巴（字节级与原实现一致）。"""
    return f"\n\n... [正文共 {total} 字符，已截断至前 {max_chars}。需要完整内容请以 max_chars=0 重读]"


def _meta_facets(meta: dict[str, Any]) -> dict[str, Any]:
    """JSON 输出用的元数据切面 —— 与 `_credential_badge` 展示的是**同一组字段**。

    只收「非默认值」的字段（scope=global / status=active 等默认值不出现），这样
    JSON 里不会塞满噪声，也不必让调用方区分「没设置」与「设成了默认值」。
    """
    facets: dict[str, Any] = {
        "type": meta.get("type", DEFAULT_MEMORY_TYPE),
        "confidence": meta.get("confidence", DEFAULT_CONFIDENCE),
        "verified": bool(meta.get("verified")),
    }
    for key, default in (("scope", DEFAULT_SCOPE), ("status", DEFAULT_STATUS)):
        val = meta.get(key, default)
        if val != default:
            facets[key] = val
    for key in ("project", "supersedes"):
        val = meta.get(key)
        if val:
            facets[key] = val
    conflicts = meta.get("conflicts")
    if isinstance(conflicts, list) and conflicts:
        facets["conflicts"] = conflicts
    return facets


def _load_memory(path: Path) -> tuple[dict[str, Any], str]:
    meta, body = _parse_frontmatter(_read_text(path))
    return _apply_meta_defaults(meta), body

def _invalidate_cache() -> None:
    global _CACHE_VALID
    _CACHE_VALID = False

def _vault_fingerprint(files: list[Path] | None = None) -> tuple:
    """Vault fingerprint: dir mtime + per-file (relpath, size, mtime_ns).

    ``files`` 允许调用方复用自己刚枚举好的文件列表（`_iter_entries` 会把
    `_iter_md_files()` 的结果直接传进来），省掉一次重复的 os.walk + stat。

    On Windows/NTFS, rewriting the *content* of an existing file does NOT bump
    the parent directory mtime — only create/rename/delete does. A dir-mtime-only
    invalidation key therefore misses external edits (Obsidian save, another MCP
    client writing, a git operation changing file contents) and the process keeps
    serving stale entries. Include a file-level fingerprint so any content change
    invalidates the cache.
    """
    try:
        dir_mtime = MEMORY_DIR.stat().st_mtime_ns
    except OSError:
        dir_mtime = 0
    parts: list[tuple] = []
    try:
        for f in (_iter_md_files() if files is None else files):
            try:
                st = f.stat()
            except OSError:
                continue
            parts.append((f.relative_to(MEMORY_DIR).as_posix(), st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    return (dir_mtime, tuple(parts))


def _iter_entries() -> list[tuple[Path, dict[str, Any], str]]:
    """全库条目枚举（唯一入口，14 处调用）。

    B2 两层缓存（2026-10-08）：

      ① 整库指纹未变 ⇒ 直接回放 `_ENTRY_SNAPSHOT`，连枚举与 stat 都省掉；
      ② 指纹变了 ⇒ 逐文件按 (size, mtime_ns) 判新鲜，**只重解析真变化的文件**。
         这是本改动的核心收益：改一条笔记不再触发全库 frontmatter 解析。

    返回的 meta 一律是浅拷贝 —— 调用方就地改 meta 不会污染缓存（旧实现直接
    回传缓存内的同一 dict，属共享可变对象隐患）。
    """
    global _CACHE_VALID, _DIR_FINGERPRINT, _ENTRY_SNAPSHOT, _PARSE_COUNT
    # Cross-process invalidation: rebuild when the vault fingerprint changed
    # (Obsidian edit, other MCP client, git operation). See _vault_fingerprint.
    files = _iter_md_files()
    fp = _vault_fingerprint(files)
    if _CACHE_VALID and _ENTRY_CACHE and _ENTRY_SNAPSHOT and fp == _DIR_FINGERPRINT:
        # 与慢路径一致地返回浅拷贝：否则调用方改 meta 会写进快照，污染后续调用。
        # 单个 dict 浅拷贝约 1µs 级，201 篇合计 <1ms，相对省下的解析开销可忽略。
        return [(p, dict(m), b) for p, m, b in _ENTRY_SNAPSHOT]
    _DIR_FINGERPRINT = fp

    order: list[str] = []
    seen: set[str] = set()
    for f in reversed(files):           # 原实现为 sorted(_iter_md_files(), reverse=True)
        try:
            rel = f.relative_to(MEMORY_DIR).as_posix()
        except ValueError:
            continue
        order.append(rel)
        seen.add(rel)
        try:
            st = f.stat()
        except OSError:
            continue
        sig = (st.st_size, st.st_mtime_ns)
        if _ENTRY_CACHE.get(rel) is not None and _ENTRY_STAT.get(rel) == sig:
            continue                    # ★ 增量：文件未变 ⇒ 复用，零 frontmatter 解析
        try:
            meta, body = _load_memory(f)
        except Exception as e:
            logger.debug("_iter_entries: skipped %s: %s", rel, e)
            _ENTRY_CACHE.pop(rel, None)
            _ENTRY_STAT.pop(rel, None)
            continue
        _ENTRY_CACHE[rel] = (f, meta, body)
        _ENTRY_STAT[rel] = sig
        _PARSE_COUNT += 1

    # 剪枝：磁盘上已消失的条目（删除 / 改名 / 移入保留目录），防缓存无界增长
    for gone in [r for r in _ENTRY_CACHE if r not in seen]:
        _ENTRY_CACHE.pop(gone, None)
        _ENTRY_STAT.pop(gone, None)

    snapshot: list[tuple[Path, dict[str, Any], str]] = []
    for rel in order:
        item = _ENTRY_CACHE.get(rel)
        if item is None:
            continue
        path, meta, body = item
        snapshot.append((path, dict(meta), body))
    _ENTRY_SNAPSHOT = snapshot
    _CACHE_VALID = True
    return list(snapshot)


def _write_memory(path: Path, title: str, tags: list[str], source: str | None, content: str,
                  created: str | None = None, summary: str | None = None,
                  tier: str | None = None, access_count: int = 0,
                  links: list[str] | None = None, version: int | None = None,
                  mem_type: str | None = None, confidence: str | None = None,
                  verified: bool | None = None, verified_at: str | None = None,
                  scope: str | None = None, project: str | None = None,
                  status: str | None = None,                   supersedes: str | None = None,
                  conflicts: list[str] | None = None,
                  source_context: str | None = None,
                  permalink: str | None = None) -> None:
    # Merge cached access_count increments into the written count
    cached = _ACCESS_CACHE.pop(title, 0)
    if cached:
        access_count += cached
    # 未显式传入的字段尝试从磁盘既有条目继承（version 防批量刷新清空，改进#10；
    # P0/P1 语义字段同理：更新正文不应把 type/confidence/verified/scope/status
    # 重置为默认值——否则「只改正文」会静默丢掉项目的隔离标记与生命周期状态）。
    if any(v is None for v in (version, mem_type, confidence, verified, scope, status,
                               project, supersedes, conflicts, source_context, permalink)):
        try:
            em, _ = _load_memory(path)
            if version is None:
                version = em.get("version")
            if mem_type is None:
                mem_type = em.get("type")
            if confidence is None:
                confidence = em.get("confidence")
            if verified is None:
                verified = em.get("verified")
            if verified_at is None:
                verified_at = em.get("verified_at")
            if scope is None:
                scope = em.get("scope")
            if project is None:
                project = em.get("project")
            if status is None:
                status = em.get("status")
            if supersedes is None:
                supersedes = em.get("supersedes")
            if conflicts is None:
                conflicts = em.get("conflicts")
            if source_context is None:
                source_context = em.get("source_context")
            # A3：permalink 一旦写过就永不重置 —— 若这里写成 `or path.stem`，
            # 文件改名时会把冻结值悄悄改掉，稳定标识就失去意义了。
            if permalink is None:
                permalink = em.get("permalink")
        except Exception:
            pass
    # verified 为假时不应残留验证时间戳（避免"未验证却有 verified_at"的矛盾态）
    if not verified:
        verified_at = None
    # 归一化正文：调用方常把「带 frontmatter 的整篇笔记」当作 content 传入，
    # 若直接拼接会产出双 frontmatter（Obsidian 只认第一块，第二块退化为正文垃圾，
    # 并污染检索/去重/链接抽取）。此处统一剥掉，顺带自愈历史受损条目。
    content = _strip_leading_frontmatter(content)
    # A3：permalink 的生成规则 —— 首次写入时冻结为**当时的文件名 stem**，此后永不覆盖
    # （上面的继承块已负责「有则沿用」）。取 stem 而非 title 的理由：全库双链按 stem
    # 解析（见 _wiki_link），stem 本就是链接体系的锚点，用它作 permalink 可保证
    # 「冻结值」与「实际链接目标」天然一致。
    if not permalink:
        permalink = path.stem
    frontmatter = _build_frontmatter(title, tags, source, created=created, summary=summary,
                                     tier=tier, access_count=access_count, links=links, version=version,
                                     mem_type=mem_type, confidence=confidence, verified=verified,
                                     verified_at=verified_at, scope=scope, project=project,
                                     status=status, supersedes=supersedes, conflicts=conflicts,
                                     source_context=source_context, permalink=permalink)
    _atomic_write_text(path, f"---\n{frontmatter}\n---\n\n{content}")


def _index_path() -> Path:
    """自动生成索引的落盘路径（B6 起带 `.` 前缀，见 `_INDEX_FILENAME`）。"""
    return MEMORY_DIR / _INDEX_FILENAME


def _drop_legacy_index() -> None:
    """一次性迁移（B6）：删掉旧名 `记忆索引.md`。

    只删「确认是生成物」的那份 —— frontmatter 的 title 必须是 `记忆索引`，且 tags
    命中 `moc`/`index`/`navigation`。若用户手工放了一份同名笔记（title 不同、
    或没有 moc 标签），**保留**：那是数据，不是生成物，删了就不可逆。
    """
    legacy = MEMORY_DIR / _INDEX_LEGACY_FILENAME
    if not legacy.exists():
        return
    try:
        meta, _ = _load_memory(legacy)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("B6 迁移跳过 %s（无法解析 frontmatter：%s）", legacy.name, e)
        return
    tags = {str(t).lower() for t in _entry_tags(meta)}
    if _entry_title(meta, legacy) != "记忆索引" or not (tags & {"moc", "index", "navigation"}):
        logger.warning("B6 迁移跳过 %s（疑似人工文件，不是生成物）", legacy.name)
        return
    try:
        legacy.unlink()
        _idx_remove_path(legacy)
        logger.info("B6 迁移：已删除旧名生成物 %s（生成物现为 %s）",
                    legacy.name, _INDEX_FILENAME)
    except OSError as e:
        logger.warning("B6 迁移失败 %s：%s", legacy.name, e)


def _refresh_index() -> None:
    """Rebuild 索引 body from current entries (direct write, no recursion).

    Called after every successful memory_write so the index stays in sync
    without manual maintenance (improvement #7).

    B2（2026-10-08）：加内容签名短路。索引正文只由 (bucket, stem, title) 三元组
    决定，三者全未变时重建只会产出逐字节相同的结果 ⇒ 跳过渲染与写盘。

    B6（2026-10-08）：落盘名改 `.记忆索引.md`（见 `_INDEX_FILENAME`）；写完顺带
    清掉旧名 —— 迁移机会只有这里，因为只有这里会（重）建生成物。
    """
    global _INDEX_SIG
    try:
        entries = _iter_entries()
        # `dirs` 在循环外取一次：`_known_dirs()` 要扫 vault 根目录，逐篇调用等于
        # 把 250 次目录枚举压进一次索引重建。
        dirs = _known_dirs()
        groups: dict[str, list[tuple[Path, str]]] = {}
        sig_parts: list[tuple[str, str, str]] = []
        for path, meta, _body in entries:
            title = _entry_title(meta, path)
            bucket, _reason = _entry_bucket(meta, path, dirs)
            groups.setdefault(bucket, []).append((path, title))
            sig_parts.append((bucket, path.stem, title))
        # tier / updated / 正文篇幅等变化都不改变索引正文，故一律不纳入签名。
        # ⚠️ 短路必须同时要求文件存在 —— 索引被外部删除时签名同样「未变」，
        #    而那时恰恰需要重建。
        idx_path = _index_path()
        index_sig = tuple(sorted(sig_parts))
        if index_sig == _INDEX_SIG and idx_path.exists():
            _m_inc("index_refresh_skipped")
            return
        _INDEX_SIG = index_sig
        lines = ["# 记忆索引（自动维护）", "",
                 "> 本索引由 ai-memory MCP 在每次写入后自动更新。如确需手工调整，请改各笔记自身而非此处。", ""]
        for bucket in _bucket_order(groups):
            items = groups.get(bucket, [])
            if not items:
                continue
            lines.append(f"## {bucket}")
            uniq: dict[str, tuple[Path, str]] = {}
            for path, title in items:
                uniq.setdefault(path.stem, (path, title))
            for stem in sorted(uniq, key=lambda s: uniq[s][1].lower()):
                path, title = uniq[stem]
                lines.append("- " + _wiki_link(path, title))
            lines.append("")
        body = "\n".join(lines).rstrip()
        if idx_path.exists():
            meta, _ = _load_memory(idx_path)
        else:
            meta = {"title": "记忆索引", "tags": ["index", "moc", "navigation"],
                    "tier": "warm", "source": "ai-memory-server"}
        meta["updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_text(idx_path, f"---\n{_dump_yaml_frontmatter(meta)}\n---\n\n{body}")
        _drop_legacy_index()
    except Exception as e:
        logger.warning("_refresh_index failed: %s", e)


# ---- MCP 工具行为标注 (2026-09-17, A1) ------------------------------
# 客户端依据这些 hint 决定是否向用户二次确认、能否并发或重试调用，并在渐进式
# 发现工具时据此排序。语义遵循 MCP 规范，两个默认值最容易踩：
#   * destructiveHint 默认是 **True** —— 非破坏性写工具必须显式设 False，
#     否则客户端会把普通 upsert 也当成破坏性操作要求确认。只读工具按规范
#     本可省略该字段（readOnlyHint 为真时它无意义），此处仍显式声明，
#     以防客户端忽略该前提条件而按默认值误判。
#   * openWorldHint 默认是 **True** —— 本库只操作本地 Vault，不触达外部世界。
# idempotentHint 表示「重复调用结果相同」：仅 memory_restore 例外 —— 从
# .archive 移回后再次调用语义不明确（目标位置可能已被新条目占用）。
_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                      idempotentHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)
_WRITE_NONIDEM = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                 idempotentHint=False, openWorldHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                               idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=_WRITE)
def memory_write(title: str, content: str, tags: list[str] | None = None, source: str | None = None,
                 summary: str | None = None, tier: str | None = None,
                 mem_type: str | None = None, confidence: str | None = None,
                 verified: bool | None = None,
                 scope: str | None = None, project: str | None = None,
                 status: str | None = None, supersedes: str | None = None,
                 conflicts: list[str] | None = None,
                 source_context: str | None = None,
                 expected_version: int | None = None) -> str:
    """Write a memory entry, updating an existing one with the same title if found.

    title 支持 ``目录/条目`` 写法（阶段二，2026-09-20）：标题含 ``/`` 时会落到 vault
    内的分类子目录，目录不存在则自动创建。例如
    ``memory_write("项目/记忆库MCP/新条目", ...)`` → ``项目/记忆库MCP/新条目.md``。
    保留目录（``.archive`` / ``.trash`` / ``.git`` / ``.obsidian`` / ``ai-memory`` 等）、
    ``.``/``..`` 路径段、盘符与超过 3 层的目录会被**拒绝并给出原因**。
    按标题更新时仍走全库匹配（含子目录），因此**不会**因为标题里出现路径而重复建档；
    阶段二之前 ``/`` 被拍成 ``_`` 的历史条目也会被正确命中。

    目录归属（阶段三，2026-09-20）：**新建**条目若标题未含 ``/``，会按标题与
    tags 推断规范目录，并在返回值末尾给出建议或落点。策略由环境变量
    ``AI_MEMORY_DIR_POLICY`` 控制：``warn``（默认，落点不变、只给建议）/
    ``auto``（自动归类落盘）/ ``strict``（无目录可归时拒绝写入）/ ``off``（关闭）。
    **已存在标题的更新一律就地更新、绝不搬运**（存量根目录条目不受影响）；
    核心页始终留在 vault 根，不参与自动归类。

    ``title`` 与落点分离：**frontmatter 里只存叶子名**（``项目/记忆库MCP/条目`` →
    ``条目``），目录仅由文件路径表达。早前把整串路径写进 title，会让该条目在所有
    「按 title 比对」的判定中失配（入链失效、MOC 渲染成死链）。

    mem_type (optional): 记忆类型，写入 frontmatter 的 type 字段。
        合法取值：fact（明确事实）/ preference（用户偏好）/ decision（决策）/
        experience（经验）/ episodic（事件记录）/ project（项目上下文）/
        constraint（约束）/ workflow（工作流）/ temporary（临时）。
        非法值将被拒绝。省略时：新建条目取默认 fact，更新条目保留原值。

    confidence (optional): 可信度，high / medium / low。
        省略时：新建取 medium，更新保留原值。

    verified (optional): 是否已由用户确认或事实核验，true / false。
        **AI 自行推断出的信息必须保持 false**，只有用户明确陈述或经核验才置 true。

    scope (optional): 记忆作用域 global / project / temporary。省略时新建取 global、
        更新保留原值。配合 project 把某项目的记忆与其他项目隔离开，检索时可用
        scope / project 过滤。注意这是**字段层**隔离，不改变文件存放位置。

    project (optional): 项目标识（建议用稳定的短名，如 "记忆库MCP"）。scope=project
        时用于区分项目；本工具不做强制校验，但 memory_audit 会提示 scope=project
        却缺 project 的条目。

    status (optional): 生命周期状态 candidate / active / stale / deprecated /
        archived，默认 active。与 verified（可信度）正交——verified 表示「内容是否
        被核实」，status 表示「这条记忆当前是否还适用」。archived 与 stale 默认不参与
        检索（可用 status 显式查询）。

    supersedes (optional): 本条记忆取代的那条记忆的标题。用于「同一件事有了新结论」
        的场景——写新结论时指向旧条目，并把旧条目改为 status=deprecated。

    conflicts (optional): 与哪些记忆标题存在冲突（字符串列表）。用于记录「两条记忆
        互相矛盾、尚未裁决」的状态，避免冲突被静默忽略。

    source_context (optional): 写入方上下文（如 workbuddy / codex / claude / manual）。
        省略时读环境变量 AI_MEMORY_SOURCE_CONTEXT。与 source 的区别：source 是数据
        来源标识（可含会话/设备信息），source_context 是「哪类客户端写入」的粗分类，
        便于按来源统计与追溯。

    expected_version (optional): optimistic concurrency check. If provided and the
    entry's current on-disk version does not match, the write is rejected to prevent
    one client silently overwriting another's concurrent edit. Omit for normal upsert.
    """
    tags = tags or []

    # 第三轮 P0-6：拒绝空壳与空标题
    if not title or not title.strip():
        return "错误：标题为空，已拒绝写入。"
    # 阶段二：标题里的 '/' 表示存放子目录 —— 位置政策先行校验，避免
    # 「消毒顺手改道」（`.archive/x` → `archive/x`）这类无声的落点偏移。
    path_err = _title_path_error(title)
    if path_err:
        return path_err
    # 归一化必须早于空壳校验与摘要派生：调用方（其他 AI 客户端 / 手工整篇粘贴 /
    # 跨工具导入）常把「带 frontmatter 的完整笔记」直接当 content 传入。若不先剥，
    # ① 正文会叠加出双 frontmatter（Obsidian 只认第一块，第二块沦为正文垃圾）；
    # ② 自动摘要会把那段 frontmatter 文本固化进 summary，污染检索结果与索引快照。
    # 仅含 frontmatter 而无正文的输入会在下一步被空壳校验拦下。
    content = _strip_leading_frontmatter(content)
    if not content or not content.strip():
        return "错误：内容为空，已拒绝创建空壳记忆（改进#5）。如为更新且需保留正文，请勿传空 content。"

    # P0 可信度字段：非法取值直接拒绝，避免脏数据进入记忆库
    if mem_type is not None and mem_type not in MEMORY_TYPES:
        return (f"错误：mem_type 取值非法（'{mem_type}'）。"
                f"合法取值：{', '.join(MEMORY_TYPES)}。")
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        return (f"错误：confidence 取值非法（'{confidence}'）。"
                f"合法取值：{', '.join(CONFIDENCE_LEVELS)}。")

    # P1 作用域 / 生命周期字段：同样拒绝非法值
    if scope is not None and scope not in SCOPES:
        return (f"错误：scope 取值非法（'{scope}'）。合法取值：{', '.join(SCOPES)}。")
    if status is not None and status not in LIFECYCLE_STATUSES:
        return (f"错误：status 取值非法（'{status}'）。"
                f"合法取值：{', '.join(LIFECYCLE_STATUSES)}。")
    # source_context 未显式给出时回落环境变量（各客户端可在自己的 MCP 配置里设定）
    if source_context is None:
        source_context = os.environ.get("AI_MEMORY_SOURCE_CONTEXT") or None

    # auto-generate summary from first 100 chars of content if not provided
    if not summary:
        clean = content.replace("\n", " ").strip()
        summary = clean[:100] + ("..." if len(clean) > 100 else "")

    tags = _auto_suggest_tags(content, tags)
    # 2026-09-06 links 优化（Step3）：不再把 links 持久化进 frontmatter。
    # 正文显式 [[双链]] 是唯一链接事实源；graph/orphans 动态扫描正文即可。
    # _write_memory 收到 links=None 时不会写出 links 字段（_build_frontmatter 兼容）。

    existing_path: Path | None = None
    existing_meta: dict[str, Any] = {}
    existing_body: str = ""

    # 阶段三（2026-09-20）：frontmatter 的 title 只存**叶子名**（目录由路径表达）。
    # 早前把整串 `目录/条目` 写进 title，导致该条目在所有「按 title 比对」的判定里
    # 全部失配（入链失效、MOC 渲染成死链）。存量 4 篇 + 1 篇属历史数据，需另行修正。
    leaf_title = title.rsplit("/", 1)[-1].strip() if "/" in title else title

    # 写入匹配全库条目（含分类子目录）；.archive 是只读归档（不参与写入匹配）。
    # 若根目录无此标题而归档中有，视为"归档后重建"——在根目录新建条目，
    # 归档保持原样。要修改归档内容请先 memory_restore。
    #
    # 阶段三：标题含 '/' 时**先按路径精确命中**。因为 frontmatter 现在只存叶子名，
    # 「按 title 全库比对」对显式路径标题必然失配 —— 不先按路径找，同一标题每次
    # 写入都会另建一份。
    explicit_path = MEMORY_DIR / _safe_filename(title) if "/" in title else None
    if explicit_path is not None and explicit_path.exists():
        # ⚠️ 必须核对 frontmatter 的 title：该路径上完全可能是「同名的别人的条目」
        # （重名场景）。不核对就会把它覆盖掉 —— 那是不可逆的数据丢失。核对不上
        # 就交给下面的 _resolve_unique_path 追加 _2 后缀，另建一份。
        try:
            _em, _eb = _load_memory(explicit_path)
            if _entry_title(_em, explicit_path) == leaf_title:
                existing_path = explicit_path
                existing_meta, existing_body = _em, _eb
        except Exception:
            pass
    #
    # 阶段二兼容：放行 '/' 之前，标题里的 '/' 会被拍成 '_' 才落盘，历史条目
    # （如 `轻装时代(QZSD)灯架/三脚架产品谱系` → `…灯架_三脚架产品谱系.md`）的文件名
    # 仍是旧口径。这里额外按旧口径的 stem 再匹配一次，否则该标题会被当成新路径，
    # 进而造出「同一标题两条记录」。
    legacy_stem = Path(_legacy_flat_filename(title)).stem if "/" in title else None
    if existing_path is None:
        for f in _iter_md_files():
            try:
                meta, body = _load_memory(f)
            except Exception:
                continue
            if (_entry_matches(meta, f, title)
                    or (legacy_stem is not None and f.stem == legacy_stem)):
                existing_path = f
                existing_meta = meta
                existing_body = body
                break

    _invalidate_cache()
    locked = _acquire_lock()
    try:
        # 第三轮 P0-2：乐观锁必须在锁内校验，且基于锁内重新读取的磁盘状态，
        # 避免"检查与写入非原子"的 TOCTOU 竞态（上一轮修复不彻底）。
        if existing_path is not None:
            fresh_meta, fresh_body = _load_memory(existing_path)
            existing_meta, existing_body = fresh_meta, fresh_body
            if expected_version is not None:
                current_version = existing_meta.get("version", 0)
                if isinstance(current_version, int) and current_version != expected_version:
                    return (f"错误：版本冲突（期望 version={expected_version}，"
                            f"实际 version={current_version}）。记忆可能已被其他端修改，"
                            f"请重新读取后再写入。")
        elif expected_version is not None:
            return f"错误：版本冲突（记忆 '{title}' 不存在，期望 version={expected_version}）。"

        # 改进#6：source 默认值；改进#10：version 递增（基于锁内最新值）
        effective_source = source or (existing_meta.get("source") if existing_path else None) or "unknown"
        old_version = existing_meta.get("version", 0)
        new_version = (old_version + 1) if isinstance(old_version, int) else 1

        if existing_path:
            old_count = existing_meta.get("access_count", 0)
            old_tier = tier or existing_meta.get("tier", "warm")
            _write_memory(
                existing_path,
                title=leaf_title,
                tags=tags,
                source=effective_source,
                content=content,
                created=str(existing_meta.get("created")) if existing_meta.get("created") else None,
                summary=summary,
                tier=old_tier,
                access_count=old_count if isinstance(old_count, int) else 0,
                version=new_version,
                mem_type=mem_type,
                confidence=confidence,
                verified=verified,
                verified_at=_today_utc() if verified else None,
                scope=scope,
                project=project,
                status=status,
                supersedes=supersedes,
                conflicts=conflicts,
                source_context=source_context,
            )
            # Auto-archive: if tier is cold or very-low-heat, move to archive
            _maybe_auto_archive(title, existing_path)
            logger.info("UPDATE  title=%s tags=%s source=%s", title, tags, source)
            result_msg = f"已更新记忆: {title} ({_rel_display(existing_path)})"
        else:
            # ---- 阶段三：新建条目的目录归属 ----
            # 标题含 '/' = 写入方已显式指定目录（阶段二政策已在入口放行），
            # 此时不介入；否则按语义桶推断规范目录，再按策略决定落点。
            policy = _dir_policy()
            write_title = title          # 仅用于算落盘路径，可带目录前缀
            dir_notice = ""
            if "/" not in title and policy != "off":
                suggest = _suggest_dir(title, tags)
                if suggest:
                    if policy == "auto":
                        write_title = f"{suggest}/{title}"
                        dir_notice = f"（已自动归类到 {suggest}/）"
                    elif policy == "warn":
                        dir_notice = (f"\n\n💡 建议归类：标题写作 '{suggest}/{title}' "
                                      f"即可落入 {suggest}/ 目录（本次仍落在 vault 根）。")
                    elif policy == "strict":
                        return (f"错误：标题未指定分类目录（策略 strict，"
                                f"{DIR_POLICY_ENV}=strict）。请改写为 "
                                f"'{suggest}/{title}'。规范目录："
                                f"{', '.join(sorted(d for d in set(BUCKET_DIRS.values()) if d))}。")
            filepath = _resolve_unique_path(MEMORY_DIR, write_title)
            _write_memory(filepath, title=leaf_title, tags=tags, source=effective_source, content=content,
                          summary=summary, tier=tier or "warm", version=new_version,
                          mem_type=mem_type, confidence=confidence, verified=verified,
                          verified_at=_today_utc() if verified else None,
                          scope=scope, project=project, status=status, supersedes=supersedes,
                          conflicts=conflicts, source_context=source_context)
            _maybe_auto_archive(title, filepath)
            logger.info("CREATE  title=%s tags=%s source=%s (new) path=%s",
                        title, tags, source, _rel_display(filepath))
            result_msg = f"已创建记忆: {title} ({_rel_display(filepath)}){dir_notice}"
        # 改进#7：写入后自动维护索引（跳过索引自身，直接写文件防递归）
        if title != "记忆索引":
            _refresh_index()
        _m_inc("write_calls")
        _m_inc("write_updates" if existing_path else "write_creates")

        # 演进 #1：冲突/重复主动防护 —— 纯提示，不阻断。让写入方（AI/主人）
        # 自己决定是否合并或标注 supersedes/conflicts。
        try:
            dup_hints = _find_duplicate_hints(title, content, _iter_entries())
        except Exception as e:  # 防护绝不干扰主流程
            logger.warning("_find_duplicate_hints failed: %s", e)
            dup_hints = []
        if dup_hints:
            result_msg += "\n\n⚠️ 疑似重复/冲突（未阻断，供你判断是否合并或标注 supersedes/conflicts）：\n- " + \
                          "\n- ".join(dup_hints)
        return result_msg
    finally:
        _flush_access_counts()
        _release_lock()

# 2026-09-06 links 优化（Step4）：memory_read 只回检索必需的核心元数据，
# 不再回传 links/version 等（links 已取消持久化，即使历史文件残留也不输出）。
# 2026-09-10 P0：新增 type/confidence/verified/verified_at —— 让读取方一眼判断
# 「这是用户明说的还是 AI 推断的、可信度如何」，遏制 Memory Poisoning。
CORE_META_KEYS = ["title", "tags", "summary", "created", "updated", "tier", "access_count", "source",
                  "type", "confidence", "verified", "verified_at",
                  "scope", "project", "status", "supersedes", "conflicts", "source_context"]
# B5（2026-10-08）：默认预算已外置到 memory.toml 的 `body_truncate_chars`。
# 本常量保留为**向后兼容别名**（tests/test_context.py 直接读它），运行时真值现取。
BODY_TRUNCATE_CHARS = memory_config.DEFAULTS["body_truncate_chars"]

@mcp.tool(annotations=_RO)
def memory_read(title: str, max_chars: int | None = None,
                output_format: str | None = None) -> str:
    """Read a memory entry by exact title, with filename fallback for legacy files.

    Body is truncated to max_chars to bound token usage; pass max_chars=0 to get
    the full body. ``None``/omitted = use ``memory.toml``'s ``body_truncate_chars``
    (built-in default 8000).

    output_format（C1）：`"text"`（默认）或 `"json"`。JSON 形态
    `{"found", "title", "path", "meta", "body", "truncated", "total_chars"}` ——
    `body` 是**已按预算截断**的正文（截断说明只在文本形态里拼），是否被截断由
    `truncated` / `total_chars` 表达。未命中时返回 `{"found": false, "title": …}`。

    ⚠️ 刻意**不把默认值写成字面量**：Python 的默认参数在 `def` 期求值一次，写死
    就等于把阈值钉在「进程启动那一刻」，之后改 memory.toml 永不生效（B5 热重载）。
    """
    try:
        fmt = _norm_output_format(output_format)
    except ValueError as e:
        return f"错误：{e}"
    if max_chars is None:
        max_chars = memory_config.get("body_truncate_chars")
    _m_inc("read_calls")
    # 索引快速路径: title/stem 命中 + 文件新鲜 -> 直接读该文件, 免全库 glob+parse
    #
    # 阶段三（2026-09-20）：入参可写成 `目录/条目`，与 memory_write 的写法对称。
    # frontmatter 的 title 只存叶子名，故按 title 比对命不中路径形式入参；
    # 含 '/' 时**先按路径直查**（最精确、也最省）。
    explicit_read = MEMORY_DIR / _safe_filename(title) if "/" in title else None
    idx_hit = explicit_read if (explicit_read is not None and explicit_read.exists()) \
        else _resolve_title_via_index(title)
    if idx_hit is not None:
        try:
            meta, body = _load_memory(idx_hit)
        except Exception:
            meta, body = {}, ""
        _ACCESS_CACHE[title] = _ACCESS_CACHE.get(title, 0) + 1
        if len(_ACCESS_CACHE) >= _ACCESS_FLUSH_THRESHOLD:
            _flush_access_counts()
        meta_str = " | ".join(
            f"{k}={str(meta[k]).replace(chr(10), ' ')}"
            for k in CORE_META_KEYS
            if k in meta
        )
        shown, truncated, total = _truncate_body(body, max_chars)
        if fmt == "json":
            return _json_out({"found": True, "title": _entry_title(meta, idx_hit),
                              "path": _rel_display(idx_hit),
                              "meta": {k: meta[k] for k in CORE_META_KEYS if k in meta},
                              "body": shown, "truncated": truncated, "total_chars": total})
        if truncated:
            shown += _body_notice(total, max_chars)
        return f"[{meta_str}]\n\n{shown}"

    # 回退: 全库递归扫描 (索引 miss / 文件被外部改动导致 stale)
    for f in _iter_md_files() + _iter_md_files(MEMORY_DIR / ".archive"):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_matches(meta, f, title):
            # increment access_count in memory cache (lazy write-back)
            _ACCESS_CACHE[title] = _ACCESS_CACHE.get(title, 0) + 1
            if len(_ACCESS_CACHE) >= _ACCESS_FLUSH_THRESHOLD:
                _flush_access_counts()
            _idx_sync_path(f)  # 回退命中后补索引, 下次走快速路径

            meta_str = " | ".join(
                f"{k}={str(meta[k]).replace(chr(10), ' ')}"
                for k in CORE_META_KEYS
                if k in meta
            )
            shown, truncated, total = _truncate_body(body, max_chars)
            if fmt == "json":
                return _json_out({"found": True, "title": _entry_title(meta, f),
                                  "path": _rel_display(f),
                                  "meta": {k: meta[k] for k in CORE_META_KEYS if k in meta},
                                  "body": shown, "truncated": truncated, "total_chars": total})
            if truncated:
                shown += _body_notice(total, max_chars)
            return f"[{meta_str}]\n\n{shown}"
    _m_inc("read_misses")
    if fmt == "json":
        return _json_out({"found": False, "title": title})
    return f"未找到标题为 '{title}' 的记忆"


@mcp.tool(annotations=_RO)
def memory_rebuild_links() -> str:
    """链接一致性校验（2026-09-06 起不再重写文件/写 links 字段）。

    links 已取消持久化（正文显式 [[双链]] 为唯一事实源），本工具改为只读
    校验：扫描全部根目录笔记正文，报告死链（指向不存在的标题）与计数，
    不修改任何文件、不刷新 mtime。
    """
    all_entries = _iter_entries()
    # A3：入库标识集合同时收 title 与 permalink —— 指向「改名前的旧标识」的双链
    # 因此不再被误报为死链（permalink 的稳定意义正在于此）。
    # B6（2026-10-08）：**必须**再收 path.stem —— 生成器渲染双链走 `_wiki_link`，
    # 而它按 Obsidian 口径**用 stem 当 target**（`[[stem|title]]`）。此前判定只认
    # title，于是「title ≠ stem」的条目在索引里一律被误报死链（36 篇），改名后的
    # `.记忆索引` 更会当场新增一条。此处的 `known` 因此与 `_entry_matches` 的
    # 「permalink → title → stem」查找链**保持同一口径**。
    known: set[str] = set()
    for f, meta, _b in all_entries:
        known.add(_entry_title(meta, f))
        known.add(f.stem)
        pl = meta.get("permalink")
        if isinstance(pl, str) and pl:
            known.add(pl)
    dead_links: list[str] = []
    total_links = 0
    notes_with_links = 0
    for f, meta, body in all_entries:
        title = _entry_title(meta, f)
        links = _extract_wiki_links(body)
        links = [_clean_link_name(l) for l in links if _clean_link_name(l) != title]
        if links:
            notes_with_links += 1
            total_links += len(links)
            for l in links:
                if l not in known:
                    dead_links.append(f"{title} -> [[{l}]]")
    lines = ["# 链接一致性校验报告", ""]
    lines.append(f"- 扫描笔记: {len(all_entries)} 条")
    lines.append(f"- 含显式双链的笔记: {notes_with_links} 条")
    lines.append(f"- 正文双链总数: {total_links} 条")
    lines.append(f"- 死链数: {len(dead_links)}")
    if dead_links:
        lines.append("")
        lines.append("## 死链清单")
        for d in sorted(set(dead_links)):
            lines.append(f"- {d}")
    lines.append("")
    lines.append("> 2026-09-06 起 links 字段已取消持久化，本工具不再写盘。")
    return "\n".join(lines)


# ---- 检索兜底用的字符 bigram（演进 #4） -----------------------------
# 与演进 #1 冲突检测的 `_bigrams`（全字符集合、用于 Jaccard 相似度）用途不同：
# 这里是**检索**用的，只取中文 2-gram 并另算英文整词，两者不可混用，故另起名字。
_RETRIEVAL_MIN_HITS = 2        # 共同 bigram 数下限
_RETRIEVAL_MIN_COVERAGE = 0.15  # query bigram 覆盖率下限
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-z0-9]{2,}")


def _cjk_bigrams(text: str) -> set[str]:
    """只取中文 2-gram。英文/数字不参与 —— 否则会把覆盖率分母撑大。"""
    out: set[str] = set()
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _retrieval_hits(query_lower: str, doc_lower: str) -> tuple[int, float]:
    """返回 (共同命中数, query 覆盖率)。

    命中数 = 共同中文 bigram 数；若英文/数字整词也有交集则额外 +1
    （英文标识符是强信号，不该被中文 bigram 的稀释效应吃掉）。
    """
    qb = _cjk_bigrams(query_lower)
    shared = len(qb & _cjk_bigrams(doc_lower))
    coverage = (shared / len(qb)) if qb else 0.0
    if set(_LATIN_RE.findall(query_lower)) & set(_LATIN_RE.findall(doc_lower)):
        shared += 1
    return shared, coverage


def _bigram_fallback(keyword_lower: str, tag: str | None, mem_type: str | None,
                     status: str | None, scope: str | None, project: str | None,
                     limit: int) -> tuple[list[str], int, int]:
    """整串子串零命中时的 bigram 兜底（#4 检索增强）。

    中文没有空格，自然语言长句（「怎么在路由器上跑容器」）作为一个**整体子串**
    永远匹配不上，而长句恰恰是真实提问最常见的形态 —— 这正是 memory_search
    此前的能力缺口（memory_smart_search 早已用 bigram 分词，两者不一致）。

    只在精确路径零命中时才被调用：常态查询不为这条罕见路径付出任何开销。
    判据「共同 bigram ≥ 2 且覆盖率 ≥ 0.15」刻意偏严 —— 兜底是**补漏**，
    不是宽泛召回；宁可漏一条，也不能让无关条目混进日常检索。
    """
    hidden = 0
    ranked: list[tuple[int, float, Path, dict[str, Any], str]] = []
    for f, meta, body in _iter_entries():
        entry_tags = _entry_tags(meta)
        if tag and tag.lower() not in [t.lower() for t in entry_tags]:
            continue
        if mem_type and meta.get("type", DEFAULT_MEMORY_TYPE) != mem_type:
            continue
        if not _passes_lifecycle_filters(meta, status=status, scope=scope, project=project):
            hidden += 1
            continue
        title = _entry_title(meta, f)
        searchable = "{} {} {}".format(title, " ".join(entry_tags), body).lower()
        hits, coverage = _retrieval_hits(keyword_lower, searchable)
        if hits < _RETRIEVAL_MIN_HITS or coverage < _RETRIEVAL_MIN_COVERAGE:
            continue
        ranked.append((hits, coverage, f, meta, title))

    ranked.sort(key=lambda x: (-x[0], -x[1]))
    lines: list[str] = []
    for hits, coverage, f, meta, title in ranked[:limit]:
        tier = meta.get("tier", "warm")
        count = meta.get("access_count", 0)
        source = meta.get("source", "")
        source_info = " | 来源: {}".format(source) if source else ""
        summary = meta.get("summary", "")
        summary_display = "\n  {}".format(summary) if summary else ""
        lines.append("- [{}] ({}) | tier={} | reads={}{}{}{}".format(
            title, f.name, tier, count, source_info,
            _credential_badge(meta), summary_display))
    return lines, len(ranked), hidden


@mcp.tool(annotations=_RO)
def memory_search(keyword: str, tag: str | None = None, limit: int = 20,
                  mem_type: str | None = None,
                  scope: str | None = None, project: str | None = None,
                  status: str | None = None) -> str:
    """Search memories by keyword across title, tags, and body (recent-first).

    limit caps the number of returned matches (default 20). Keyword occurrences
    in the body snippet are wrapped in ** for visibility.
    mem_type (optional): 仅返回该 type 的记忆（fact/preference/decision/experience/
    episodic/project/constraint/workflow/temporary）。旧笔记无该字段时按 fact 处理。
    scope / project (optional): 仅返回该作用域 / 项目的记忆；传 "any" 表示不过滤。
    status (optional): 生命周期过滤。**省略时默认排除 archived**（归档条目退出日常
    检索）；传 "any" 查看全部，传 candidate/active/stale/deprecated/archived 精确
    过滤。被隐藏的命中数会在结果末尾提示，避免误以为记忆不存在。
    """
    if not keyword or not keyword.strip():
        return "错误：搜索关键词不能为空。"
    _t0 = time.perf_counter()
    results: list[str] = []
    hidden = 0
    keyword_lower = keyword.lower()
    archive_dir = MEMORY_DIR / ".archive"

    search_dirs = [MEMORY_DIR]
    if archive_dir.exists():
        search_dirs.append(archive_dir)

    for search_dir in search_dirs:
        for f in sorted(_iter_md_files(search_dir), reverse=True):
            try:
                meta, body = _load_memory(f)
            except Exception:
                continue

            entry_tags = _entry_tags(meta)
            if tag and tag.lower() not in [t.lower() for t in entry_tags]:
                continue
            if mem_type and meta.get("type", DEFAULT_MEMORY_TYPE) != mem_type:
                continue

            title = _entry_title(meta, f)
            tags_str = " ".join(entry_tags)
            searchable = f"{title} {tags_str} {body}".lower()

            if keyword_lower in searchable:
                if not _passes_lifecycle_filters(meta, status=status, scope=scope,
                                                 project=project):
                    hidden += 1
                    continue
                summary = meta.get("summary", "")
                tier = meta.get("tier", "warm")
                count = meta.get("access_count", 0)

                # extract keyword context from body (20 chars before and after)
                idx = body.lower().find(keyword_lower)
                context = ""
                if idx >= 0:
                    start = max(0, idx - 20)
                    end = min(len(body), idx + len(keyword) + 20)
                    ctx = body[start:end].replace("\n", " ")
                    ctx = re.sub(re.escape(keyword), f"**{keyword}**", ctx, flags=re.IGNORECASE)
                    if start > 0:
                        ctx = "..." + ctx
                    if end < len(body):
                        ctx = ctx + "..."
                    context = f"\n  → {ctx}"

                source = meta.get("source", "")
                source_info = f" | 来源: {source}" if source else ""
                cred_info = _credential_badge(meta)
                tags_display = ", ".join(entry_tags)
                summary_display = f"\n  {summary}" if summary else ""

                results.append(
                    f"- [{title}] ({f.name}) | {tags_display} | tier={tier} | reads={count}{source_info}{cred_info}{summary_display}{context}"
                )

    if not results:
        # #4 检索增强：精确子串零命中 → 用字符 bigram 兜底（覆盖自然语言长句），
        # 兜底也零命中才算真的没有。常态查询走不到这里，故无额外开销。
        fb_lines, fb_total, fb_hidden = _bigram_fallback(
            keyword_lower, tag, mem_type, status, scope, project, limit)
        if fb_lines:
            _m_search("search", fb_total, _t0)
            header = ("未找到与 '{0}' **精确匹配**的记忆 —— 以下为 bigram 模糊匹配结果"
                      "（共 {1} 条）:\n\n").format(keyword, fb_total)
            return header + "\n\n".join(fb_lines) + _filter_hint(hidden + fb_hidden)
        _m_search("search", 0, _t0)
        return f"未找到与 '{keyword}' 相关的记忆" + _filter_hint(hidden)

    shown = results[:limit]
    total = len(results)
    _m_search("search", total, _t0)
    header = f"找到 {total} 条记忆" + (f"，显示前 {limit} 条" if total > limit else "") + ":\n\n"
    return header + "\n\n".join(shown) + _filter_hint(hidden)

def _trash_records(limit: int = 20) -> tuple[int, list[dict[str, Any]]]:
    """回收站记录（C1：文本与 JSON 共用同一批记录，避免两套实现分叉）。"""
    tdir = _trash_dir()
    if not tdir.exists():
        return 0, []
    rows = sorted(_iter_md_files(tdir), key=lambda p: p.stat().st_mtime, reverse=True)
    if not rows:
        return 0, []
    records: list[dict[str, Any]] = []
    for f in rows[:limit]:
        try:
            meta, _ = _load_memory(f)
            t = _entry_title(meta, f)
        except Exception:
            t = f.stem
        try:
            st = f.stat()
            when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            size = st.st_size
        except OSError:
            when, size = "?", 0
        records.append({"title": t, "moved_at": when, "size_bytes": size})
    return len(rows), records


def _list_trash(limit: int = 20) -> str:
    """列出回收站内容（memory_list(include_trash=True) 的文本渲染）。"""
    total, records = _trash_records(limit)
    if not total:
        return "回收站为空"
    lines = [f"- {r['title']}（{r['moved_at']} 移入, {r['size_bytes']} B）" for r in records]
    tail = f"\n\n... 共 {total} 条，显示前 {limit} 条" if total > limit else ""
    return (f"回收站共 {total} 条 —— 取回: memory_restore(source=\"trash\")；"
            f"清空: memory_delete(empty_trash=True)\n" + "\n".join(lines) + tail)


@mcp.tool(annotations=_RO)
def memory_list(tag: str | None = None, limit: int = 20, tier: str | None = None,
                mem_type: str | None = None,
                scope: str | None = None, project: str | None = None,
                status: str | None = None,
                include_trash: bool = False,
                output_format: str | None = None) -> str:
    """List memory entries with a small preview.

    mem_type (optional): 仅列出该 type 的条目（fact/preference/decision/...）。
    scope / project (optional): 仅列出该作用域 / 项目的条目；传 "any" 表示不过滤。
    status (optional): 生命周期过滤。**省略时默认排除 archived**（归档条目不再出现
    在日常列表中）；传 "any" 查看全部，传具体值精确过滤。被隐藏的条目数会在末尾提示。
    include_trash (optional): 改为列出**回收站**（.trash/）内容，此时其余过滤参数
    不生效。配合 memory_restore(source="trash") 取回、memory_delete(empty_trash=True) 清空。
    output_format（C1）: `"text"`（默认）或 `"json"`。回收站形态下 JSON 为
    `{"trash": true, "count", "limit", "shown", "entries": [{"title","moved_at","size_bytes"}]}`。
    """
    try:
        fmt = _norm_output_format(output_format)
    except ValueError as e:
        return f"错误：{e}"
    if include_trash:
        if fmt == "json":
            total, trash_records = _trash_records(limit)
            return _json_out({"trash": True, "count": total, "limit": limit,
                              "shown": len(trash_records), "entries": trash_records})
        return _list_trash(limit)

    _t0 = time.perf_counter()
    entries: list[str] = []
    records: list[dict[str, Any]] = []
    hidden = 0

    for f in sorted(_iter_md_files(), reverse=True):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue

        entry_tags = _entry_tags(meta)
        if tag and tag.lower() not in [t.lower() for t in entry_tags]:
            continue
        if tier and meta.get("tier", "warm") != tier:
            continue
        if mem_type and meta.get("type", DEFAULT_MEMORY_TYPE) != mem_type:
            continue
        if not _passes_lifecycle_filters(meta, status=status, scope=scope, project=project):
            hidden += 1
            continue

        title = _entry_title(meta, f)
        tags_display = ", ".join(entry_tags)
        created = str(meta.get("created", ""))[:10]
        source = meta.get("source", "")
        tier_val = meta.get("tier", "warm")
        reads = meta.get("access_count", 0)
        source_info = f" | {source}" if source else ""
        cred_info = _credential_badge(meta)

        # prefer summary field, fallback to body[:40]
        summary = meta.get("summary", "")
        if summary:
            preview = summary[:80].replace("\n", " ").strip()
        else:
            preview = body[:40].replace("\n", " ").strip()
        preview += "..." if len(preview) >= (40 if not summary else 80) else ""

        entries.append(f"- [{created}] {title} [{tier_val}|{reads}]{source_info}{cred_info} | {tags_display}\n  {preview}")
        records.append({"title": title, "created": created or None, "tier": tier_val,
                        "reads": reads if isinstance(reads, int) else 0,
                        "source": source or None, "tags": entry_tags,
                        "preview": preview,
                        "path": _rel_display(f), "facets": _meta_facets(meta)})

    if not entries:
        _m_search("list", 0, _t0)
        if fmt == "json":
            return _json_out({"trash": False, "count": 0, "limit": limit,
                              "shown": 0, "hidden": hidden, "entries": []})
        return "记忆库为空" + _filter_hint(hidden)

    total = len(entries)
    _m_search("list", total, _t0)
    if fmt == "json":
        return _json_out({"trash": False, "count": total, "limit": limit,
                          "shown": min(limit, total), "hidden": hidden,
                          "entries": records[:limit]})
    entries = entries[:limit]
    result = "\n".join(entries)
    if total > limit:
        result += f"\n\n... 共 {total} 条，显示前 {limit} 条"
    return result + _filter_hint(hidden)

@mcp.tool(annotations=_DESTRUCTIVE)
@_locked_write
def memory_delete(title: str, trash: bool = True, purge: bool = False,
                  empty_trash: bool = False) -> str:
    """Delete a memory entry by exact title, with filename fallback for legacy files.

    trash=True (default) moves the file to .trash/ instead of unlinking, so a
    mistaken delete can be recovered. Pass purge=True (or trash=False) to
    permanently remove it (including a previously soft-deleted copy in .trash/).
    Archive entries (.archive/) are also matched.

    empty_trash=True: 清空整个回收站（此时 title 被忽略）。这是唯一会一次性
    删除多条的路径，必须显式传入。查看回收站用 memory_list(include_trash=True)，
    取回用 memory_restore(source="trash")。
    """
    if empty_trash:
        tdir = _trash_dir()
        if not tdir.exists():
            return "回收站为空，无需清理"
        files = _iter_md_files(tdir)
        if not files:
            return "回收站为空，无需清理"
        removed = 0
        for f in files:
            try:
                f.unlink()
                removed += 1
            except OSError as e:
                logger.warning("empty_trash(%s): %s", f.name, e)
        _invalidate_cache()
        logger.info("DELETE(empty_trash) removed=%d", removed)
        return f"已清空回收站：永久删除 {removed} 条（不可恢复）"

    search_dirs = [MEMORY_DIR]
    archive_dir = MEMORY_DIR / ".archive"
    if archive_dir.exists():
        search_dirs.append(archive_dir)
    if not trash or purge:
        trash_dir = _trash_dir()
        if trash_dir.exists():
            search_dirs.append(trash_dir)
    for f in [p for d in search_dirs for p in _iter_md_files(d)]:
        try:
            meta, _ = _load_memory(f)
        except Exception:
            continue
        if _entry_matches(meta, f, title):
            if trash and not purge:
                trash_dir = _trash_dir()
                trash_dir.mkdir(parents=True, exist_ok=True)
                dest = _free_path_in(trash_dir, f.name)
                f.replace(dest)
                try:
                    os.utime(dest, None)  # mtime=进入回收站的时刻，供列表显示
                except OSError:
                    pass
                _idx_remove_path(f)  # 软删移出库, 清索引行
                logger.info("DELETE(soft) title=%s -> %s", title, dest.name)
                _invalidate_cache()
                if title != "记忆索引":
                    _refresh_index()
                return (f"已软删除记忆: {title} -> .trash/{dest.name}"
                        f"（用 memory_restore(source=\"trash\") 可恢复，purge=true 彻底删除）")
            f.unlink()
            _idx_remove_path(f)  # 永久删除, 清索引行
            logger.info("DELETE(purge) title=%s", title)
            _invalidate_cache()
            if title != "记忆索引":
                _refresh_index()
            return f"已永久删除记忆: {title} ({f.name})"
    return f"未找到标题为 '{title}' 的记忆"

@mcp.tool(annotations=_WRITE)
@_locked_write
def memory_update_metadata(title: str, tier: str | None = None, tags: list[str] | None = None,
                           summary: str | None = None, source: str | None = None,
                           mem_type: str | None = None, confidence: str | None = None,
                           verified: bool | None = None,
                           scope: str | None = None, project: str | None = None,
                           status: str | None = None, supersedes: str | None = None,
                           conflicts: list[str] | None = None,
                           source_context: str | None = None) -> str:
    """Update only the frontmatter metadata of an entry without rewriting its body.

    Useful for changing tier/tags/summary/source of an existing note while keeping
    its full content intact (avoids the full rewrite that memory_write requires).
    Pass None to leave a field unchanged; pass an empty list to clear tags.

    P0 可信度字段（mem_type / confidence / verified）同样支持在此单独修正 —— 典型
    用途是把 AI 早先推断写入的内容由 verified=false 提升为 true（用户事后确认），
    或修正 type 归类错误，而无需重写正文。

    P1 生命周期字段（scope / project / status / supersedes / conflicts /
    source_context）在此同样可单独修正，典型用途：
      - 把被取代的旧条目改为 status=deprecated（配合新条目声明 supersedes）
      - 把项目记忆标为 scope=project + project=xxx
      - 把长期未复核的条目标为 status=stale
    传空字符串 / 空列表可清除对应字段（如 supersedes="" 表示不再取代任何条目）。
    """
    if mem_type is not None and mem_type not in MEMORY_TYPES:
        return (f"错误：mem_type 取值非法（'{mem_type}'）。"
                f"合法取值：{', '.join(MEMORY_TYPES)}。")
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        return (f"错误：confidence 取值非法（'{confidence}'）。"
                f"合法取值：{', '.join(CONFIDENCE_LEVELS)}。")
    if scope is not None and scope not in SCOPES:
        return (f"错误：scope 取值非法（'{scope}'）。合法取值：{', '.join(SCOPES)}。")
    if status is not None and status not in LIFECYCLE_STATUSES:
        return (f"错误：status 取值非法（'{status}'）。"
                f"合法取值：{', '.join(LIFECYCLE_STATUSES)}。")
    for f in _iter_md_files() + _iter_md_files(MEMORY_DIR / ".archive"):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_matches(meta, f, title):
            if tier is not None:
                meta["tier"] = tier
            if tags is not None:
                meta["tags"] = tags
            if summary is not None:
                meta["summary"] = summary
            if source is not None:
                meta["source"] = source
            if mem_type is not None:
                meta["type"] = mem_type
            if confidence is not None:
                meta["confidence"] = confidence
            if verified is not None:
                meta["verified"] = bool(verified)
                if verified:
                    meta["verified_at"] = _today_utc()
                else:
                    meta.pop("verified_at", None)
            if scope is not None:
                meta["scope"] = scope
            if project is not None:
                if project.strip():
                    meta["project"] = project.strip()
                else:
                    meta.pop("project", None)
            if status is not None:
                meta["status"] = status
            if supersedes is not None:
                if supersedes.strip():
                    meta["supersedes"] = supersedes.strip()
                else:
                    meta.pop("supersedes", None)
            if conflicts is not None:
                cleaned = [str(c).strip() for c in conflicts if str(c).strip()]
                if cleaned:
                    meta["conflicts"] = cleaned
                else:
                    meta.pop("conflicts", None)
            if source_context is not None:
                if source_context.strip():
                    meta["source_context"] = source_context.strip()
                else:
                    meta.pop("source_context", None)
            old_version = meta.get("version", 0)
            meta["version"] = (old_version + 1) if isinstance(old_version, int) else 1
            meta["schema_version"] = SCHEMA_VERSION
            meta["updated"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_text(f, f"---\n{_dump_yaml_frontmatter(meta)}\n---\n\n{body}")
            _invalidate_cache()
            logger.info("UPDATE_META  title=%s", title)
            return f"已更新元数据: {title}"
    return f"未找到标题为 '{title}' 的记忆"

@mcp.tool(annotations=_RO)
def memory_audit() -> str:
    """Scan the vault and summarize what should be indexed or cleaned up manually.

    Now also covers filesystem-level issues that frontmatter-only stats miss
    (improvements #8/#9): empty shells, dead links, naming drift.
    """
    entries = _iter_entries()
    if not entries:
        return "记忆库为空"

    # 建立全量 title 集合（含 .archive）用于死链检测
    all_titles: set[str] = set()
    archive_dir = MEMORY_DIR / ".archive"
    for d in ([MEMORY_DIR, archive_dir] if archive_dir.exists() else [MEMORY_DIR]):
        for f in _iter_md_files(d):
            try:
                m, _ = _load_memory(f)
                all_titles.add(_entry_title(m, f))
                all_titles.add(f.stem)
            except Exception:
                pass

    bucket_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    attention: list[str] = []
    suggestions: list[str] = []
    dead_links: set[str] = set()
    empty_shells: list[str] = []
    p1_issues: list[str] = []      # P1：生命周期/作用域/冲突字段的一致性问题
    old_schema_count = 0           # P1：仍为旧 frontmatter 结构的条目数

    audit_dirs = _known_dirs()
    for path, meta, body in entries:
        title = _entry_title(meta, path)
        bucket, reason = _entry_bucket(meta, path, audit_dirs)
        bucket_counts[bucket] += 1

        source = str(meta.get("source", "")).strip()
        if source:
            source_counts[source] += 1
        else:
            attention.append(f"- {title}：缺少 source")

        tags = _entry_tags(meta)
        if not tags:
            attention.append(f"- {title}：缺少 tags")

        # 改进#9：空壳检测
        if not body or not body.strip():
            empty_shells.append(title)
            attention.append(f"- {title}：空壳（正文为空）")

        # 改进#9/#10：死链检测（links 字段 + 正文 wikilink）
        # 跳过代码块（```...``` 与 ~~~...~~~）中的示例链接，避免误报噪点
        body_no_code = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
        body_no_code = re.sub(r"~~~.*?~~~", "", body_no_code, flags=re.DOTALL)
        PLACEHOLDER_LINKS = {"X", "XX", "XXX", "笔记名", "示例", "example", "placeholder", "Placeholder"}
        for link in (meta.get("links", []) or []):
            if link in PLACEHOLDER_LINKS:
                continue
            if link != title and link not in all_titles:
                dead_links.add(f"- {title} → [[{link}]]（指向不存在的笔记）")
        for link in _extract_wiki_links(body_no_code):
            if link in PLACEHOLDER_LINKS:
                continue
            if link != title and link not in all_titles:
                dead_links.add(f"- {title} → [[{link}]]（正文指向不存在的笔记）")

        # P1（2026-09-10）：生命周期 / 作用域 / 冲突字段的一致性检测。
        # 这些字段是语义元数据，写错不会让任何功能报错，但会让「哪条还适用」
        # 判断失准（如 supersedes 指向已删除的记忆 → 读者以为有更权威的新结论）。
        _supersedes = str(meta.get("supersedes") or "").strip()
        if _supersedes:
            if _supersedes == title:
                p1_issues.append(f"- {title}：supersedes 指向自身")
            elif _supersedes not in all_titles:
                p1_issues.append(f"- {title}：supersedes 指向不存在的记忆 '{_supersedes}'")
        _conflicts = meta.get("conflicts")
        if isinstance(_conflicts, list):
            for _c in _conflicts:
                _cs = str(_c).strip()
                if _cs and _cs not in all_titles:
                    p1_issues.append(f"- {title}：conflicts 指向不存在的记忆 '{_cs}'")
        if meta.get("scope", DEFAULT_SCOPE) == "project" and not str(meta.get("project") or "").strip():
            p1_issues.append(f"- {title}：scope=project 但缺少 project 标识（无法按项目隔离检索）")
        if meta.get("status") == "archived" and not meta.get("archived_to"):
            p1_issues.append(f"- {title}：status=archived 但无 archived_to（疑似状态标注错误）")
        if _schema_version(meta) == 1:
            old_schema_count += 1

        # 改进#8：命名建议（游离的时间戳前缀）
        if re.match(r"^\d{4}-\d{2}-\d{2}_", title) and bucket == "其他":
            suggestions.append(f"- {title} -> 建议加分类前缀（如 项目_/修复_/工具_）")

        if bucket in {"项目", "工具", "运维", "修复", "规则", "身份与配置"}:
            suggestions.append(f"- {title} -> {bucket}（{reason}）")

    lines = ["# 记忆库审计报告", ""]
    lines.append("## 分类统计")
    for name, count in bucket_counts.most_common():
        lines.append(f"- {name}: {count}")

    lines.append("")
    lines.append("## 来源统计")
    for name, count in source_counts.most_common():
        lines.append(f"- {name}: {count}")

    lines.append("")
    lines.append("## 建议纳入索引")
    lines.extend(suggestions[:60] if suggestions else ["- 暂无"])

    lines.append("")
    lines.append("## 死链（指向不存在的笔记）")
    lines.extend(sorted(dead_links)[:60] if dead_links else ["- 暂无"])

    lines.append("")
    lines.append("## 空壳笔记")
    lines.extend([f"- {t}" for t in empty_shells] if empty_shells else ["- 暂无"])

    lines.append("")
    lines.append(f"## 生命周期 / 冲突 / 作用域一致性（P1，{len(p1_issues)} 条问题）")
    lines.append(f"- 仍为旧 frontmatter 结构（无 schema_version）: {old_schema_count} 条"
                 f"（被写入时会自动升到 v{SCHEMA_VERSION}）")
    lines.extend(sorted(set(p1_issues))[:60] if p1_issues else ["- 暂无字段一致性问题"])

    lines.append("")
    lines.append("## 需要人工补齐")
    lines.extend(attention[:60] if attention else ["- 暂无"])

    lines.append("")
    lines.extend(_metrics_report_lines())

    return "\n".join(lines)


@mcp.tool(annotations=_RO)
def memory_doctor(limit: int = 20) -> str:
    """Check SQLite index <-> disk consistency without repairing anything.

    ``_refresh_index`` rebuilds unconditionally, so drift stays invisible right
    up until it gets silently repaired. This tool answers "is the index
    trustworthy right now" by reporting three classes of divergence:

    1. **stale rows** — the index has a row, no file exists on disk
    2. **unindexed files** — a ``.md`` exists on disk with no index row
    3. **field drift** — ``title`` / ``(size, mtime)`` disagree between sides

    Deliberately skips the lazy rebuild: repairing first would make every report
    read "consistent". Scan scope matches ``_rebuild_index``'s loader (vault root
    + 分类子目录 + ``.archive``) so archive copies are not misreported as stale.
    """
    if not _IDX_FILE.exists():
        return ("# 索引一致性自检\n\n"
                f"索引文件尚未建立：{_IDX_FILE.name}\n"
                "> 首次写入或重启服务后会自动建立。")

    conn = None
    try:
        conn = midx.connect(_IDX_FILE)
        idx = {r["relpath"]: r for r in midx.iter_all(conn)}

        disk: dict[str, Path] = {}
        search_dirs = [MEMORY_DIR]
        archive = MEMORY_DIR / ".archive"
        if archive.exists():
            search_dirs.append(archive)
        for d in search_dirs:
            for f in _iter_md_files(d):
                try:
                    disk[f.relative_to(MEMORY_DIR).as_posix()] = f
                except ValueError:
                    continue
        n_root = sum(1 for rel in disk if "/" not in rel)
        n_arch = sum(1 for rel in disk if rel.startswith(".archive/"))
        n_cat = len(disk) - n_root - n_arch

        stale = sorted(set(idx) - set(disk))
        unindexed = sorted(set(disk) - set(idx))

        title_drift: list[tuple[str, str, str]] = []
        stat_drift: list[str] = []
        unreadable: list[str] = []
        for rel in sorted(set(idx) & set(disk)):
            row, path = idx[rel], disk[rel]
            try:
                meta, _ = _load_memory(path)
            except Exception as e:
                unreadable.append(f"- {rel}：{e}")
                continue
            title_now = _entry_title(meta, path)
            if title_now != row["title"]:
                title_drift.append((rel, row["title"], title_now))
            if not midx.fresh(conn, MEMORY_DIR, row):
                try:
                    st = path.stat()
                except OSError:
                    continue
                stat_drift.append(
                    f"- {rel}：索引 size={row['size']} mtime={row['mtime']:.3f}"
                    f" ↔ 磁盘 size={st.st_size} mtime={st.st_mtime:.3f}"
                )
    finally:
        if conn is not None:
            midx.close(conn)

    clean = not (stale or unindexed or title_drift or stat_drift or unreadable)

    out = ["# 索引一致性自检", "",
           f"- 索引行数: {len(idx)}",
           f"- 磁盘文件: {len(disk)}（根目录 {n_root}，分类目录 {n_cat}，.archive {n_arch}）",
           f"- 结论: {'索引与磁盘一致' if clean else '发现差异，索引当前不可完全信任'}",
           ""]

    def _section(head: str, items: list[str]) -> None:
        out.append(f"## {head}")
        out.extend(items[:limit] if items else ["- 无"])
        if len(items) > limit:
            out.append(f"- ... 共 {len(items)} 条，显示前 {limit} 条")
        out.append("")

    _section(f"1. 死索引（索引有行、磁盘无文件）— {len(stale)} 条",
             [f"- {rel}" for rel in stale])
    _section(f"2. 未入索引（磁盘有文件、索引无行）— {len(unindexed)} 条",
             [f"- {rel}" for rel in unindexed])

    out.append("## 3. 字段漂移")
    out.append(f"- title 不一致: {len(title_drift)} 条")
    out.extend(f"  - {rel}：索引「{old}」↔ 磁盘「{new}」"
               for rel, old, new in title_drift[:limit])
    out.append(f"- size/mtime 不一致: {len(stat_drift)} 条")
    out.extend(stat_drift[:limit])
    if len(stat_drift) > limit:
        out.append(f"- ... 共 {len(stat_drift)} 条，显示前 {limit} 条")
    out.append("")

    if unreadable:
        _section(f"4. 读取失败（跳过比对）— {len(unreadable)} 条", unreadable)

    if not clean:
        out.append("> 本工具只诊断不修复。索引定位为「运行时缓存，删掉可重建」，"
                   "重启 MCP 服务会在启动时全量重建；磁盘文件始终是唯一事实源。")
    return "\n".join(out)


@mcp.tool(annotations=_RO)
def memory_schema_diff(limit: int = 15) -> str:
    """Diff the frontmatter *structure* across the vault — infer, never repair.

    对标 basic-memory 的 `schema_infer` / `schema_validate` / `schema_diff` 三件套，
    本库只做其中的 **diff**：把「写入端承诺写出什么」与「磁盘上实际有什么」对账。

    契约来源是代码本身（`yaml_io.build_frontmatter` 是唯一写出源），键分三层：core
    任何版本都该有 / v2 仅 `schema_version>=2` 承诺 / opt 按需写出。据此输出：

    1. **核心键缺失** —— **有 frontmatter** 却缺任何版本都该有的键。属真实缺陷。
    2. **v2 条目缺 v2 键** —— 声明了 v2 却没写全，属**不一致**（写入端有 bug）。
    3. **v1 存量迁移清单** —— v1 条目缺 v2 键是**预期**，此处把它变成「按 diff 批量
       回填」的输入，而不是一条条盲改。
    4. **未知键** —— 既非本库写入端产出、也非归档标记的键（如第三方工具写的
       `aliases`）。可能是历史残留，也可能是外部工具在改你的 frontmatter。
    5. **可选键使用率** —— 仅作了解，缺失不是缺陷。
    6. **无 frontmatter 的文件** —— 混在 Vault 里的普通 Markdown（`README.md` /
       身份档案 / 日志导出）。它们不受结构契约约束，故单列且**不计入缺陷数** ——
       否则真实缺陷会淹没在这类噪声里（实测 11 条「核心键缺失」里 2 条属此类）。

    扫描范围与 `memory_doctor` 一致（根 + 分类子目录 + `.archive`），故归档 stub 也
    在账内。样例条目附**键在 frontmatter 中的行号**，便于直接跳到现场；`limit`
    控制每类显示的样例条数（计数始终是全量）。

    本工具**只读**：不写盘、不改索引、不修复任何条目。
    """
    scan: dict[str, Path] = {}
    for f in _iter_md_files(MEMORY_DIR):
        try:
            scan[f.relative_to(MEMORY_DIR).as_posix()] = f
        except ValueError:
            continue
    archive = MEMORY_DIR / ".archive"
    if archive.exists():
        for f in _iter_md_files(archive):
            try:
                scan[f.relative_to(MEMORY_DIR).as_posix()] = f
            except ValueError:
                continue

    versions: Counter = Counter()
    zones = Counter()
    core_missing: list[tuple[str, list[str]]] = []
    v2_missing: list[tuple[str, list[str]]] = []
    v1_entries: list[str] = []
    v1_gap_keys: Counter = Counter()
    unknown: list[tuple[str, list[str]]] = []
    unknown_keys: Counter = Counter()
    opt_used: Counter = Counter()
    no_frontmatter: list[str] = []
    unreadable: list[str] = []

    for rel in sorted(scan):
        path = scan[rel]
        zones["归档" if rel.startswith(".archive/") else
              ("分类" if "/" in rel else "根")] += 1
        try:
            raw = _read_text(path)
        except Exception as e:                      # 编码异常 / 读取失败
            unreadable.append(f"- {rel}：{e}")
            continue
        # 「没有 frontmatter」与「有 frontmatter 但缺键」是两回事：前者是混在 Vault
        # 里的普通 Markdown（README / 身份档案 / 日志导出），本就不受结构契约约束
        # —— 把它们算成「核心键缺失」会让真实缺陷淹没在噪声里（实测 11 条里 2 条
        # 属此类）。故单列一类，且**不计入缺陷数**。
        if not raw.startswith("---"):
            no_frontmatter.append(rel)
            continue
        try:
            meta, _ = _parse_frontmatter(raw)
        except Exception as e:
            unreadable.append(f"- {rel}：{e}")
            continue
        if not isinstance(meta, dict):
            unreadable.append(f"- {rel}：frontmatter 解析结果不是映射")
            continue

        keys = set(meta)
        ver = _schema_version(meta)
        versions[ver] += 1

        miss_core = [k for k in _SCHEMA_CORE_KEYS if k not in keys]
        if miss_core:
            core_missing.append((rel, miss_core))

        if ver >= SCHEMA_VERSION:
            miss_v2 = [k for k in _SCHEMA_V2_KEYS if k not in keys]
            if miss_v2:
                v2_missing.append((rel, miss_v2))
        else:
            v1_entries.append(rel)
            for k in _SCHEMA_V2_KEYS:
                if k not in keys:
                    v1_gap_keys[k] += 1

        extra = sorted(keys - _SCHEMA_ALL_KNOWN)
        if extra:
            unknown.append((rel, extra))
            unknown_keys.update(extra)

        for k in _SCHEMA_OPTIONAL_KEYS:
            if k in keys:
                opt_used[k] += 1

    def _with_lines(items: list[tuple[str, list[str]]]) -> list[str]:
        """把 `(rel, [键])` 渲染成带行号的样例行（只对本类样例读盘取行号）。"""
        rows = []
        for rel, ks in items:
            marks = _frontmatter_key_lines(scan[rel])
            label = "、".join(f"{k}（第 {marks[k]} 行）" if k in marks else k for k in ks)
            rows.append(f"- {rel}：{label}")
        return rows

    def _add_section(head: str, rows: list[str], total: int) -> None:
        out.append(f"## {head} — {total} 条")
        out.extend(rows[:limit] if rows else ["- 无"])
        if total > limit:
            out.append(f"- ... 共 {total} 条，显示前 {limit} 条")
        out.append("")

    total = sum(zones.values())
    n_fm = total - len(no_frontmatter)
    out = ["# frontmatter 结构差异（schema_diff）", "",
           f"- 扫描文件: {len(scan)}（根 {zones['根']} · 分类 {zones['分类']} · 归档 {zones['归档']}）",
           f"- 其中带 frontmatter 的条目: {n_fm}；无 frontmatter: {len(no_frontmatter)}"
           "（普通 Markdown，不参与结构判定）",
           "- 结构版本: " + " · ".join(
               f"v{v} = {versions[v]}" for v in sorted(versions, reverse=True)),
           f"- 判定基准: 当前写入端结构版本 v{SCHEMA_VERSION}",
           ""]

    _add_section("1. 核心键缺失（有 frontmatter 却缺任何版本都该有的键 —— 真实缺陷）",
                 [f"- {rel}：缺 " + "、".join(ks) for rel, ks in core_missing],
                 len(core_missing))
    _add_section(f"2. v2 条目缺 v2 必需键（声明了 v{SCHEMA_VERSION} 却没写全）",
                 _with_lines(v2_missing), len(v2_missing))

    out.append(f"## 3. v1 存量迁移清单 — {len(v1_entries)} 条")
    if v1_entries:
        out.append("以下条目缺 v2 键属**预期**（写入端会渐进补齐），此处汇总成回填输入：")
        for k in _SCHEMA_V2_KEYS:
            if v1_gap_keys[k]:
                out.append(f"- 缺 `{k}`：{v1_gap_keys[k]} 条")
        out.extend(f"- 样例：{rel}" for rel in v1_entries[:limit])
        if len(v1_entries) > limit:
            out.append(f"- ... 共 {len(v1_entries)} 条，显示前 {limit} 条")
    else:
        out.append("- 无")
    out.append("")

    _add_section("4. 未知键（非写入端产出、非归档标记）",
                 _with_lines(unknown), len(unknown))
    if unknown_keys:
        out.append("键频次：" + " · ".join(
            f"`{k}` {n}" for k, n in unknown_keys.most_common()))
        out.append("")

    out.append("## 5. 可选键使用率（按需写出，缺失不是缺陷）")
    for k in _SCHEMA_OPTIONAL_KEYS:
        out.append(f"- `{k}`: {opt_used[k]} / {n_fm}")
    out.append("")

    if no_frontmatter:
        _add_section("6. 无 frontmatter 的文件（非记忆条目，不在缺陷数内）",
                     [f"- {rel}" for rel in no_frontmatter], len(no_frontmatter))

    if unreadable:
        _add_section("7. 读取失败（未纳入统计）", unreadable, len(unreadable))

    defects = len(core_missing) + len(v2_missing) + len(unreadable)
    out.append("## 结论")
    if defects:
        out.append(f"- **发现 {defects} 条结构缺陷**"
                   f"（核心键缺失 {len(core_missing)} · v2 缺失 {len(v2_missing)}"
                   f" · 读取失败 {len(unreadable)}），建议逐条核对。")
    else:
        out.append("- 未发现结构缺陷：所有条目的核心键齐全，声明 v2 的条目亦写全 v2 键。")
    if v1_entries:
        out.append(f"- v1 存量 {len(v1_entries)} 条待渐进迁移 —— 可据第 3 节按缺失键"
                   "批量回填，或等其被再次写入时自动补齐。")
    out.append("> 本工具只诊断不修复；磁盘文件是唯一事实源，改结构走 `memory_write` / "
               "`memory_update_metadata`。")
    return "\n".join(out)


@mcp.tool(annotations=_RO)
def memory_index_draft() -> str:
    """Generate a draft of the main index without writing it back automatically."""
    entries = _iter_entries()
    if not entries:
        return "记忆库为空"

    dirs = _known_dirs()
    groups: dict[str, list[tuple[Path, str]]] = {}

    for path, meta, _body in entries:
        title = _entry_title(meta, path)
        bucket, _reason = _entry_bucket(meta, path, dirs)
        groups.setdefault(bucket, []).append((path, title))

    lines = ["# 记忆索引（半自动草稿）", "", "> 这个草稿由 MCP 自动扫描生成，建议人工确认后再回写到 `.记忆索引.md`。", ""]

    for bucket in _bucket_order(groups):
        items = groups.get(bucket, [])
        if not items:
            continue
        lines.append(f"## {bucket}")
        for path, title in sorted(items, key=lambda item: item[1].lower()):
            lines.append("- " + _wiki_link(path, title))
        lines.append("")

    return "\n".join(lines).rstrip()

@mcp.tool(annotations=_DESTRUCTIVE)
@_locked_write
def memory_archive(title: str) -> str:
    """Archive a memory entry: move full content to .archive/, leave a summary stub."""
    archive_dir = MEMORY_DIR / ".archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""

    for f in _iter_md_files():
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_matches(meta, f, title):
            target_path = f
            target_meta = meta
            target_body = body
            break

    if not target_path:
        return f"未找到标题为 '{title}' 的记忆"

    if target_meta.get("tier") == "cold" and target_meta.get("archived_to"):
        return f"记忆 '{title}' 已在归档中"

    archive_path = archive_dir / target_path.name
    if archive_path.exists():
        # 第三轮 P1-10：同名归档不覆盖，加时间戳唯一化
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_path = archive_dir / f"{target_path.stem}_{ts}{target_path.suffix}"
    meta_copy = dict(target_meta)
    meta_copy["tier"] = "cold"
    # 归档 = 生命周期进入 archived（与 tier=cold 的"冷热分层"是两个维度）
    meta_copy["status"] = "archived"
    meta_copy["schema_version"] = SCHEMA_VERSION
    meta_copy["updated"] = datetime.now(timezone.utc).isoformat()
    archive_fm = _dump_yaml_frontmatter(meta_copy)
    _atomic_write_text(archive_path, f"---\n{archive_fm}\n---\n\n{target_body}")

    summary = target_meta.get("summary", "")
    if not summary:
        clean = target_body.replace("\n", " ").strip()
        summary = clean[:100] + ("..." if len(clean) > 100 else "")

    stub_meta = {
        "title": target_meta.get("title", title),
        "tags": target_meta.get("tags", []),
        "summary": summary,
        "tier": "cold",
        "status": "archived",
        "schema_version": SCHEMA_VERSION,
        "access_count": target_meta.get("access_count", 0),
        "archived_to": ".archive/{0}".format(archive_path.name),
        "created": str(target_meta.get("created", "")),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    # stub 保留原条目的语义标签（类型/可信度/作用域），否则恢复与检索会丢失上下文
    for _key in ("type", "confidence", "scope", "project", "source_context", "supersedes"):
        if target_meta.get(_key):
            stub_meta[_key] = target_meta[_key]
    if target_meta.get("source"):
        stub_meta["source"] = target_meta["source"]

    stub_fm = _dump_yaml_frontmatter(stub_meta)
    stub_body = "> \u26a1 本条记忆已归档。完整内容在 `.archive/{0}`\n> 需要恢复的话跟我说一声就行。\n\n{1}".format(target_path.name, summary)
    _atomic_write_text(target_path, "---\n{0}\n---\n\n{1}".format(stub_fm, stub_body))

    logger.info("ARCHIVE title=%s", title)
    _invalidate_cache()
    return "已归档: {0} -> .archive/{1}".format(title, archive_path.name)

def _restore_from_trash(title: str) -> str:
    """从 .trash/ 恢复软删除的条目（memory_restore(source="trash") 的实现）。"""
    trash_dir = _trash_dir()
    if not trash_dir.exists():
        return "回收站为空，无内容可恢复"

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""
    for f in _iter_md_files(trash_dir):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_matches(meta, f, title):
            target_path, target_meta, target_body = f, meta, body
            break
    if not target_path:
        return (f"回收站中未找到标题为 '{title}' 的记忆"
                f"（用 memory_list(include_trash=True) 查看全部）")

    target_title = str(target_meta.get("title") or title)
    if target_title in CORE_PAGES:
        return f"错误：'{target_title}' 是核心页面，不允许恢复覆盖。"

    # 根目录已有同标题条目 => 拒绝，避免静默覆盖现存内容（回收站里的可能是旧版）
    for f in _iter_md_files():
        try:
            m, _ = _load_memory(f)
        except Exception:
            continue
        if _entry_title(m, f) == target_title or f.stem == target_title:
            return (f"未恢复：根目录已存在同标题记忆 '{target_title}'（{f.name}）。"
                    f"请先处理现存条目，再取回回收站中的这份。")

    dest = MEMORY_DIR / _safe_filename(target_title)
    restored_meta = dict(target_meta)
    restored_meta["status"] = "active"          # 取回 => 生命周期回到生效中
    restored_meta["schema_version"] = SCHEMA_VERSION
    restored_meta["updated"] = datetime.now(timezone.utc).isoformat()
    restored_meta.pop("deleted_at", None)
    _atomic_write_text(dest, f"---\n{_dump_yaml_frontmatter(restored_meta)}\n---\n\n{target_body}")
    _idx_remove_path(target_path)  # 清 .trash 行的索引（新行已由 atomic_write 同步）
    target_path.unlink(missing_ok=True)
    _invalidate_cache()
    if target_title != "记忆索引":
        _refresh_index()
    logger.info("RESTORE(trash) title=%s", target_title)
    return f"已恢复记忆: {target_title}（从 .trash/{target_path.name}）"


@mcp.tool(annotations=_WRITE_NONIDEM)
@_locked_write
def memory_restore(title: str, source: str = "archive") -> str:
    """Restore an entry back to the vault root (P1-7).

    source="archive"（默认）: 从 .archive/ 恢复 —— 把完整内容搬回 MEMORY_DIR，
    替换掉 memory_archive 留下的 stub，并把 tier 回到 warm。

    source="trash": 从 .trash/ 取回 memory_delete 软删除的条目。若根目录已存在
    同标题条目则**拒绝**（回收站里的可能是被新版替代的旧内容，静默覆盖更危险）。
    """
    src = (source or "archive").strip().lower()
    if src not in ("archive", "trash"):
        return f"错误：source 只能是 'archive' 或 'trash'（收到 {source!r}）"
    if src == "trash":
        return _restore_from_trash(title)

    archive_dir = MEMORY_DIR / ".archive"
    if not archive_dir.exists():
        return "归档目录不存在，无内容可恢复"

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""
    for f in _iter_md_files(archive_dir):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_matches(meta, f, title):
            target_path = f
            target_meta = meta
            target_body = body
            break
    if not target_path:
        return f"归档中未找到标题为 '{title}' 的记忆"

    target_title = str(target_meta.get("title") or title)
    if target_title in CORE_PAGES:
        return f"错误：'{target_title}' 是核心页面，不允许恢复覆盖。"

    # 定位根目录中的 stub（若有），否则用安全文件名新建
    stub = MEMORY_DIR / _safe_filename(target_title)
    for f in _iter_md_files():
        try:
            m, _ = _load_memory(f)
        except Exception:
            continue
        if _entry_title(m, f) == target_title or f.stem == target_title:
            stub = f
            break

    restored_meta = dict(target_meta)
    restored_meta["tier"] = "warm"
    restored_meta.pop("archived_to", None)
    # 从归档恢复 => 生命周期回到生效中；结构版本升到当前
    restored_meta["status"] = "active"
    restored_meta["schema_version"] = SCHEMA_VERSION
    restored_meta["updated"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_text(stub, f"---\n{_dump_yaml_frontmatter(restored_meta)}\n---\n\n{target_body}")
    _idx_remove_path(target_path)  # 原 .archive 文件删除, 清其索引行(新 stub 行已由 atomic_write 同步)
    target_path.unlink(missing_ok=True)
    _invalidate_cache()
    if target_title != "记忆索引":
        _refresh_index()
    logger.info("RESTORE title=%s", target_title)
    return f"已恢复记忆: {target_title}（从 .archive/{target_path.name}）"

# ---- 热度驱动自动升降级（演进 #3） --------------------------------
# 「预览」与「执行」必须共用同一判据：若两处各写一套阈值，dry-run 说的和
# 实际改的就会漂移，而这种漂移是静默的（预览说改 3 条、实改 5 条）——比
# 没有预览更危险。故判据抽成单一函数 _heat_tier_decision，两处都调它。
# B5（2026-10-08）：三个阈值已外置到 memory.toml（heat_cold_max /
# heat_demote_hot_max / heat_hot_min）。下列常量仅作向后兼容别名。
_HEAT_COLD_MAX = memory_config.DEFAULTS["heat_cold_max"]              # 低于此分且非 cold → 降为 cold
_HEAT_DEMOTE_HOT_MAX = memory_config.DEFAULTS["heat_demote_hot_max"]  # 低于此分且是 hot  → 降为 warm
_HEAT_HOT_MIN = memory_config.DEFAULTS["heat_hot_min"]                # 高于此分且非 hot  → 升为 hot


def _heat_tier_decision(title: str, current_tier: str, score: float) -> str | None:
    """按热度分数给出该条应有的 tier；返回 None 表示维持现状。

    核心页（CORE_PAGES）一律豁免：它们由人工或生成器维护（如 `.记忆索引.md`
    每次写入都会重建），自动改 tier 没有意义。这与 `_maybe_auto_archive`
    的核心页保护同源，也避免出现「预览清单里有核心页、执行时却被跳过」的
    不一致 —— 豁免发生在判据内部，两处行为天然一致。
    """
    if title in CORE_PAGES:
        return None
    # 阈值现取（B5）：预览与执行都走这一个函数，故两处天然用同一套阈值。
    cold_max = memory_config.get("heat_cold_max")
    demote_hot_max = memory_config.get("heat_demote_hot_max")
    hot_min = memory_config.get("heat_hot_min")
    if score < cold_max and current_tier != "cold":
        return "cold"
    if score < demote_hot_max and current_tier == "hot":
        return "warm"
    if score > hot_min and current_tier != "hot":
        return "hot"
    return None


def _heat_report(apply: bool) -> str:
    """热度分析主体；apply=True 时真正把 tier 写盘。"""
    _invalidate_cache()
    pending: list[tuple[Path, str, str, str, float, int]] = []
    stats: list[tuple[str, str, str, int, str, str]] = []
    core_skipped = 0

    for f in sorted(_iter_md_files(), reverse=True):
        try:
            meta, _body = _load_memory(f)
        except Exception:
            continue
        title = _entry_title(meta, f)
        tier_val = meta.get("tier", "warm")
        reads = meta.get("access_count", 0)
        if not isinstance(reads, int):
            reads = 0
        updated_str = str(meta.get("updated", ""))[:10]
        created_str = str(meta.get("created", ""))[:10]
        source = str(meta.get("source", "")) or "?"
        stats.append((title, tier_val, source, reads, updated_str, created_str))

        score = _heat_score(reads, str(meta.get("updated", "")))
        to_tier = _heat_tier_decision(title, tier_val, score)
        if to_tier is None:
            if title in CORE_PAGES:
                core_skipped += 1
            continue
        pending.append((f, title, tier_val, to_tier, score,
                        _days_since_utc(meta.get("updated", ""))))

    # 按 heat_score 升序：降级类（分数低）排在前面，先看更需要确认的部分
    pending.sort(key=lambda x: x[4])

    lines: list[str] = []
    lines.append("# 热度分析与自动升降级" if not apply else "# 热度分析与自动升降级（已执行）")
    lines.append("")
    lines.append("共 {0} 条记忆（核心页豁免 {1} 条）\n".format(len(stats), core_skipped))

    lines.append("## 按访问次数排序")
    for title, tier_val, source, reads, updated_str, created_str in sorted(stats, key=lambda x: -x[3]):
        score = _heat_score(reads, updated_str)
        lines.append("- [{0}] {1} | {2} | score={3:.1f}, {4} 次读取 | {5}".format(
            created_str, title, tier_val, score, reads, source))
    lines.append("")

    if not pending:
        lines.append("## 调整清单")
        lines.append("- 暂无需要调整的条目")
        return "\n".join(lines)

    if apply:
        applied: list[str] = []
        for path, title, from_tier, to_tier, _score, _days in pending:
            try:
                em, ebody = _load_memory(path)
            except Exception as e:      # 单条失败不影响整批
                logger.warning("heat_apply load failed title=%s: %s", title, e)
                continue
            _write_memory(
                path, title=title, tags=_entry_tags(em), source=em.get("source"),
                content=ebody, created=str(em.get("created", "")),
                summary=em.get("summary"), tier=to_tier,
                access_count=em.get("access_count", 0), links=em.get("links"),
            )
            applied.append("{}: {}->{}".format(title, from_tier, to_tier))
        if applied:
            _refresh_index()
        logger.info("HEAT_APPLY applied=%d skipped_core=%d", len(applied), core_skipped)
        lines.append("## 已执行（{0} 条）".format(len(applied)))
        lines.extend("- " + item for item in applied)
        lines.append("")
        lines.append("tier 已写盘，记忆索引已重建。")
        return "\n".join(lines)

    lines.append("## 待调整（未执行，共 {0} 条）".format(len(pending)))
    for _path, title, from_tier, to_tier, score, days in pending:
        tail = "访问频繁" if to_tier == "hot" else "{0}d 未更新".format(days)
        lines.append("- [{0}] {1}->{2} | heat_score={3:.2f}, {4}".format(
            title, from_tier, to_tier, score, tail))
    lines.append("")
    lines.append("以上仅为预览，**未改动任何数据**。确认无误后传 `apply=True` 执行。")
    return "\n".join(lines)


@mcp.tool(annotations=_WRITE)
def memory_heat_suggest(apply: bool = False) -> str:
    """Scan entries and suggest tier changes based on access_count and staleness.

    apply=False（默认）**只预览**，不改任何数据；apply=True 才真正把 tier
    写盘（并重建索引）。默认不执行是刻意的：自动升降级一次会改动全库 tier，
    误伤的代价远高于「多看一次预览」；预览结果也因此明确标注「未执行」，
    以免被误读为已完成。

    预览与执行共用 `_heat_tier_decision` 判据 —— 所见即所改。
    核心页（`CORE_PAGES`）一律豁免，不参与自动调整。
    """
    if not apply:
        return _heat_report(apply=False)
    # 仅在实际写盘时加锁：默认的预览是纯读，不该为它引入锁开销
    locked = _acquire_lock()
    try:
        return _heat_report(apply=True)
    finally:
        if locked:
            _release_lock()

@mcp.tool(annotations=_RO)
def memory_graph(title: str, limit: int = 10, include_all: bool = False) -> str:
    """Show which pages this entry links to and which pages link to it (backlinks).

    2026-09-06 links 优化（Step5）：默认最多展示 limit 条，避免高连接中心页
    输出过长；反向链接改为以「全库正文扫描」为唯一事实源（不读 frontmatter links）。
    """
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""
    for f, meta, body in all_entries:
        if _entry_matches(meta, f, title):
            target_meta = meta
            target_path = f
            target_body = body
            break

    if not target_path:
        return '未找到标题为 "{}" 的记忆'.format(title)

    outlinks = _extract_wiki_links(target_body)
    outlinks = [link for link in outlinks if link != title]

    backlinks: list[str] = []
    for f, meta, body in all_entries:
        if f == target_path:
            continue
        entry_title = _entry_title(meta, f)
        if title in _extract_wiki_links(body):
            if entry_title not in backlinks:
                backlinks.append(entry_title)

    def _cap(items: list[str]) -> list[str]:
        if include_all or len(items) <= limit:
            return items
        return items[:limit]

    lines = ["# {} 的链接图谱".format(title), ""]
    lines.append("## 出链 ({} 条)".format(len(outlinks)))
    if outlinks:
        shown = _cap(sorted(outlinks))
        for link in shown:
            lines.append("- [[{}]]".format(link))
        if len(outlinks) > len(shown):
            lines.append(f"- …共 {len(outlinks)} 条，用 include_all=true 查看全部")
    else:
        lines.append("- (无)")
    lines.append("")
    lines.append("## 反向链接 ({} 条)".format(len(backlinks)))
    if backlinks:
        shown = _cap(sorted(backlinks))
        for link in shown:
            lines.append("- [[{}]]".format(link))
        if len(backlinks) > len(shown):
            lines.append(f"- …共 {len(backlinks)} 条，用 include_all=true 查看全部")
    else:
        lines.append("- (无)")

    return "\n".join(lines)

# ---- B1 memory_context：上下文遍历与组装 (2026-09-18) --------------------
# 与 memory_graph 的分工：graph 只「列链接」，本工具「组装内容」——
# 一次调用即可拿到「某主题 + 其周边」的可读上下文，替代 3~5 次手工拼装。
_CONTEXT_TIER_RANK = {"hot": 0, "warm": 1, "cold": 2}
# 生成页：「记忆索引」由 _refresh_index 在每次写入后重建，正文就是**全库标题列表**。
# 若参与遍历，则任何查询的头号「邻居」都是它（warm + 刚更新 ⇒ 排位极高），
# 它的 [[链接]] 是「罗列全库」的产物、不是语义关联 ⇒ 只将它排除出**周边**；
# 显式把它当起点传入时仍可正常使用。
_CONTEXT_SKIP_TITLES = {"记忆索引"}
_CONTEXT_MIN_BLOCK_ROOM = 200   # 剩余预算低于此值就不再展开新条目
_CONTEXT_TF_UNITS = {"h": 1 / 24, "d": 1, "w": 7, "m": 30, "y": 365,
                     "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
_CONTEXT_TF_WORDS = (("last week", 7), ("last month", 30), ("last year", 365),
                     ("this week", 7), ("this month", 30))


def _parse_timeframe(timeframe: str | None) -> int | None:
    """把 timeframe 解析成「最多多少天」；无法识别返回 None（= 不限）。

    支持 "today" / "yesterday" / "last week" / "2 days ago" / "3d" / "24h" /
    "2w" / "2026-09-01"。刻意保持宽松：解析不出就按不限处理、由调用方在输出里
    提示，不抛异常 —— 防的是一个拼错的参数让整段上下文都取不到。
    """
    s = (timeframe or "").strip().lower()
    if not s or s in ("all", "any", "全部", "不限"):
        return None
    if s in ("today", "今天"):
        return 0
    if s in ("yesterday", "昨天"):
        return 1
    for word, days in _CONTEXT_TF_WORDS:
        if s.startswith(word):
            return days
    s = re.sub(r"\s*ago$", "", s)
    m = re.fullmatch(r"(\d+)\s*([a-z]+)", s)
    if m:
        factor = _CONTEXT_TF_UNITS.get(m.group(2).rstrip("s"))
        if factor is not None:
            return max(0, int(round(int(m.group(1)) * factor)))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          tzinfo=timezone.utc)
        except ValueError:
            return None
        return max(0, (datetime.now(timezone.utc) - dt).days)
    return None


@mcp.tool(annotations=_RO)
def memory_context(title: str, depth: int = 2, max_related: int = 10,
                   timeframe: str | None = None, max_chars: int | None = None) -> str:
    """Assemble a ready-to-read context block around one entry (B1).

    与 memory_graph 的区别：graph 只列「出链 + 反链」，本工具**把沿途正文组装成
    一段可直接喂给模型的内容**并标注来源与关系 —— 跨会话延续从「3~5 次调用」降到 1 次。

    Args:
        title: 起点条目的标题（或文件名 stem）。
        depth: 沿 [[wikilink]] 出链 / 反链扩展的层数。0 = 只要起点本身。
        max_related: 周边条目数上限（起点不计入），按 tier → access_count →
            updated 排序取前 N。
        timeframe: 只保留 updated 在范围内的**周边**条目（起点始终包含）。支持
            "today" / "yesterday" / "last week" / "2 days ago" / "3d" / "24h" /
            "2w" / "2026-09-01"；不传或 "all" = 不限。无法识别时按不限处理并在
            输出里提示。
        max_chars: 整段输出字符上限；0 = 不限。省略时用 ``memory.toml`` 的
            ``body_truncate_chars``（内置默认 8000）。
            预算在起点与周边间分摊（起点至多一半，其余按篇数均分），
            故周边不会因起点过长而被挤到一篇不剩；想要全文传 0。

    Returns:
        Markdown：起点块 + 周边块；每块以 `_关系 · 深度 · tier · reads · updated_`
        标注来源。受 max_chars 约束时会显式提示截断与未展开数量。

        自动生成的「记忆索引」不参与周边遍历（它的 [[链接]] 只是罗列全库，
        会挤掉真实关联）；但显式传它作为起点时仍可用。
    """
    all_entries = _iter_entries()

    by_name: dict[str, tuple[Path, dict[str, Any], str]] = {}
    for _f, _meta, _body in all_entries:
        for key in (_entry_title(_meta, _f), _f.stem):
            by_name.setdefault(key, (_f, _meta, _body))

    start = by_name.get(title) or by_name.get(_clean_link_name(title))
    if start is None:
        return '未找到标题为 "{}" 的记忆'.format(title)
    start_title = _entry_title(start[1], start[0])

    def _resolve(name: str) -> str | None:
        """把正文里的链接名解析为库内真实标题；解析不到返回 None（视为未建页）。"""
        ent = by_name.get(name) or by_name.get(_clean_link_name(name))
        return _entry_title(ent[1], ent[0]) if ent else None

    # 出链 / 反链都以「正文扫描」为唯一事实源，与 memory_graph 保持同一口径。
    outlinks: dict[str, list[str]] = {}
    backlinks: dict[str, list[str]] = {}
    for _f, _meta, _body in all_entries:
        _t = _entry_title(_meta, _f)
        if _t in _CONTEXT_SKIP_TITLES:
            continue
        _resolved: list[str] = []
        for _link in _extract_wiki_links(_body):
            _nxt = _resolve(_link)
            if _nxt and _nxt != _t and _nxt not in _resolved:
                _resolved.append(_nxt)
        outlinks[_t] = _resolved
        for _nxt in _resolved:
            backlinks.setdefault(_nxt, []).append(_t)

    # BFS：记下每个可达条目的最小深度，以及「从谁、以何种关系」抵达。
    found: dict[str, tuple[int, str, str]] = {}
    seen = {start_title}
    queue: list[tuple[str, int, str, str]] = [(start_title, 0, "起点", "")]
    limit_depth = max(int(depth), 0)
    while queue:
        cur, d, rel, via = queue.pop(0)
        if cur != start_title:
            found.setdefault(cur, (d, rel, via))
        if d >= limit_depth:
            continue
        for _nxt, _kind in ([(n, "出链") for n in outlinks.get(cur, [])] +
                            [(n, "反链") for n in backlinks.get(cur, [])]):
            if _nxt not in seen and _nxt != start_title:
                seen.add(_nxt)
                queue.append((_nxt, d + 1, _kind, cur))

    tf_days = _parse_timeframe(timeframe)
    tf_note = ""
    if timeframe and tf_days is None:
        tf_note = "（timeframe={!r} 无法识别，已按不限处理）".format(timeframe)

    selected: list[tuple[str, int, str, str, tuple[Path, dict[str, Any], str]]] = []
    skipped_tf = 0
    skipped_gen = 0
    for _name, (_d, _rel, _via) in found.items():
        if _name in _CONTEXT_SKIP_TITLES:
            skipped_gen += 1
            continue
        _ent = by_name.get(_name)
        if _ent is None:
            continue
        _upd = _ent[1].get("updated") or _ent[1].get("created") or ""
        if tf_days is not None and _days_since_utc(_upd) > tf_days:
            skipped_tf += 1
            continue
        selected.append((_name, _d, _rel, _via, _ent))

    def _rank(item) -> tuple:
        _meta = item[4][1]
        _tier = str(_meta.get("tier", "warm")).lower()
        try:
            _reads = int(_meta.get("access_count", 0) or 0)
        except (TypeError, ValueError):
            _reads = 0
        _dt = _parse_dt_utc(_meta.get("updated")) or datetime.min.replace(tzinfo=timezone.utc)
        return (_CONTEXT_TIER_RANK.get(_tier, 1), -_reads, -_dt.timestamp())

    selected.sort(key=_rank)
    total_related = len(selected)
    shown = selected[:max(int(max_related), 0)]

    budget = memory_config.get("body_truncate_chars") if max_chars is None else int(max_chars)
    unlimited = budget == 0

    def _meta_line(meta: dict[str, Any], extra: str) -> str:
        bits = [extra, "tier=" + str(meta.get("tier", "warm")),
                "reads=" + str(meta.get("access_count", 0) or 0)]
        _updated = str(meta.get("updated") or "")[:10]
        if _updated:
            bits.append("updated=" + _updated)
        return " · ".join(b for b in bits if b)

    def _render(name: str, ent, extra: str) -> str:
        return "## {}\n\n_{}_\n\n{}".format(name, _meta_line(ent[1], extra), ent[2].strip())

    def _cut_at_line(text: str, cap: int) -> str:
        """按字符上限截断并回退到最后一个换行 —— 避免把 `_关系 · 深度 · tier_` 标注行切断。"""
        cut = text[:max(cap, 0)]
        return cut.rsplit("\n", 1)[0] if "\n" in cut else cut

    head = ["# 上下文：{}".format(start_title),
            "起点 1 篇 + 周边 {} 篇（展开 {} 篇）".format(total_related, len(shown))
            + ("；{} 篇超出 timeframe 已略过".format(skipped_tf) if skipped_tf else "")
            + ("；生成页 {} 篇未计入".format(skipped_gen) if skipped_gen else "")
            + ("；{} 篇超出 max_related 未展开".format(total_related - len(shown))
               if total_related > len(shown) else "")
            + ((" " + tf_note) if tf_note else ""),
            "起点出链 {} 条 / 反链 {} 条".format(len(outlinks.get(start_title, [])),
                                                 len(backlinks.get(start_title, [])))]

    buf = ["\n".join(head)]
    used = len(buf[0])

    # 预算分摊：max_chars 约束的是**整段输出**，而单篇正文最长可达 20KB+（如「近期工作动态」）
    # —— 若让起点独占预算，周边一篇都展不开，那这工具就白搭。
    # 规则：起点至多占一半（无周边时可用满）；其余按「剩余 ÷ 未展开篇数」均分，
    # 且每篇至少预留 _CONTEXT_MIN_BLOCK_ROOM ⇒ 前篇截得短时后篇自动多分。
    truncated_any = False
    if unlimited:
        start_cap = 10 ** 9
    elif not shown:
        start_cap = max(budget - used, 0)
    else:
        start_cap = min(max(budget // 2, _CONTEXT_MIN_BLOCK_ROOM), max(budget - used, 0))

    start_block = _render(start_title, start, "起点 · 深度 0")
    if len(start_block) > start_cap:
        start_block = _cut_at_line(start_block, start_cap) + "\n\n…（起点正文已按 max_chars 截断）"
        truncated_any = True
    buf.append(start_block)
    used += len(start_block)

    merged = 0
    for _idx, (_name, _d, _rel, _via, _ent) in enumerate(shown):
        remaining = (budget - used) if not unlimited else 10 ** 9
        if remaining < _CONTEXT_MIN_BLOCK_ROOM:
            break
        left = len(shown) - _idx                      # 含本篇仍未展开的篇数
        reserve = _CONTEXT_MIN_BLOCK_ROOM * (left - 1)  # 为后面的篇保留最低额度
        share = min(remaining // left, max(remaining - reserve, _CONTEXT_MIN_BLOCK_ROOM))
        block = _render(_name, _ent, "{} ←「{}」· 深度 {}".format(_rel, _via, _d))
        if len(block) > share:
            block = _cut_at_line(block, share) + "\n\n…（本块已按 max_chars 截断）"
            truncated_any = True
        buf.append(block)
        used += len(block)
        merged += 1

    if merged < len(shown):
        buf.append("…共 {} 篇周边，因 max_chars 只展开了 {} 篇（可传 max_chars=0 取全部）"
                   .format(total_related, merged))
    elif total_related > len(shown):
        buf.append("…另有 {} 篇未展开（提高 max_related 可见）".format(total_related - len(shown)))
    if truncated_any:
        buf.append("…上方有块被截断（max_chars={}）；传 max_chars=0 可取全文"
                   .format(budget))

    return "\n\n".join(buf)


@mcp.tool(annotations=_RO)
def memory_orphans(output_format: str | None = None) -> str:
    """Find entries with zero backlinks (isolated notes).

    output_format（C1）：`"text"`（默认）或 `"json"`
    （`{"count", "orphans": [{"title", "path"}]}`）。
    """
    try:
        fmt = _norm_output_format(output_format)
    except ValueError as e:
        return f"错误：{e}"
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()

    backlink_index: dict[str, list[str]] = {}
    title_map: dict[str, tuple[Path, dict[str, Any], str]] = {}

    # ⚠️ 链接 target 必须**先解析回条目**再计入反链。正文里的 `[[X]]` 有三种合法写法：
    # title、文件名 stem（`_wiki_link` 生成器渲染的就是 stem）、以及 A3 的 permalink
    # （改名前的旧标识）。直接拿 target 当 key 会造成两类误报：
    #   ① 生成索引 `.记忆索引` 里的 `[[stem|title]]` 对「title≠stem」的条目**压根连不上**，
    #      于是那些条目全被算成孤立（与 B6 修掉的死链误报同型）；
    #   ② {title} / {stem} 之类的模板占位文本会被当成真实链接目标，凭空造出反链。
    id2title: dict[str, str] = {}
    for f, meta, _b in all_entries:
        t = _entry_title(meta, f)
        id2title.setdefault(t, t)
        id2title.setdefault(f.stem, t)
        _pl = meta.get("permalink")
        if isinstance(_pl, str) and _pl:
            id2title.setdefault(_pl, t)

    for f, meta, body in all_entries:
        t = _entry_title(meta, f)
        title_map[t] = (f, meta, body)
        if t not in backlink_index:
            backlink_index[t] = []
        if t in _CONTEXT_SKIP_TITLES:
            # 生成页（`.记忆索引`）的链接只是**全库罗列**，不是真实引用。把它算作
            # 引用方的话，所有条目都"被引用过" ⇒ 孤立数恒为 0，工具直接失去意义。
            # 这与 memory_context（周边遍历排除生成页）、memory_stats 的豁免同源。
            continue
        candidates: list[str] = []
        links = meta.get("links", [])
        if isinstance(links, list):
            candidates.extend(str(x) for x in links)
        candidates.extend(_extract_wiki_links(body))
        for link in candidates:
            target = id2title.get(link) or id2title.get(_clean_link_name(link))
            if target and target != t and t not in backlink_index.setdefault(target, []):
                backlink_index[target].append(t)

    core_pages = {"记忆索引", "近期工作动态", "用户画像", "AI身份档案",
                  "Codex 身份档案", "记忆库总规范", "共享记忆库规则"}
    orphans: list[str] = []
    for t in sorted(title_map.keys()):
        if t in core_pages:
            continue
        if not backlink_index.get(t):
            orphans.append(t)

    if fmt == "json":
        return _json_out({"count": len(orphans),
                          "orphans": [{"title": t,
                                       "path": _rel_display(title_map[t][0])} for t in orphans]})

    lines = ["# 孤立笔记 ({} 条)".format(len(orphans)), "",
             "以下笔记没有其他笔记引用它们：", ""]
    if orphans:
        for title in orphans:
            lines.append("- [[{}]]".format(title))
    else:
        lines.append("- (无孤立笔记)")

    return "\n".join(lines)

@mcp.tool(annotations=_DESTRUCTIVE)
@_locked_write
def memory_batch_tag(old_tag: str, new_tag: str) -> str:
    """Rename a tag across all entries."""
    _invalidate_cache()
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    updated: list[str] = []

    for f, meta, body in all_entries:
        tags = _entry_tags(meta)
        if old_tag not in tags:
            continue
        new_tags = [new_tag if t == old_tag else t for t in tags]
        title = _entry_title(meta, f)
        _write_memory(
            f,
            title=title,
            tags=new_tags,
            source=meta.get("source"),
            content=body,
            created=str(meta.get("created", "")),
            summary=meta.get("summary"),
            tier=meta.get("tier", "warm"),
            access_count=meta.get("access_count", 0),
            links=meta.get("links"),
        )
        updated.append(title)

    if updated:
        logger.info("BATCH_TAG %s->%s (%d entries)", old_tag, new_tag, len(updated))
        return "已更新 {} 条记忆的标签: {} -> {}\n- ".format(len(updated), old_tag, new_tag) + "\n- ".join(updated)
    return '未找到包含标签 "{}" 的记忆'.format(old_tag)

@mcp.tool(annotations=_WRITE)
@_locked_write
def memory_batch_tier(target_tier: str, min_score: float = 0.0, max_score: float | None = None) -> str:
    """Batch-set tier based on heat score range."""
    _invalidate_cache()
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    updated: list[str] = []

    for f, meta, body in all_entries:
        reads = meta.get("access_count", 0)
        if not isinstance(reads, int):
            reads = 0
        updated_str = str(meta.get("updated", ""))
        score = _heat_score(reads, updated_str)
        if min_score is not None and score < min_score:
            continue
        if max_score is not None and score > max_score:
            continue
        title = _entry_title(meta, f)
        _write_memory(
            f,
            title=title,
            tags=_entry_tags(meta),
            source=meta.get("source"),
            content=body,
            created=str(meta.get("created", "")),
            summary=meta.get("summary"),
            tier=target_tier,
            access_count=meta.get("access_count", 0),
            links=meta.get("links"),
        )
        updated.append("{} (score={:.1f})".format(title, score))

    if updated:
        logger.info("BATCH_TIER target=%s count=%d", target_tier, len(updated))
        return "已调整 {} 条记忆为 tier={}:\n- ".format(len(updated), target_tier) + "\n- ".join(updated)
    return "没有符合筛选条件的记忆"

@mcp.tool(annotations=_DESTRUCTIVE)
@_locked_write
def memory_archive_old(days: int = 90) -> str:
    """Archive entries not updated in N days."""
    _invalidate_cache()
    core_pages = CORE_PAGES
    now = datetime.now(timezone.utc)
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    archived: list[str] = []

    for f, meta, body in all_entries:
        title = _entry_title(meta, f)
        if title in core_pages:
            continue
        if meta.get("tier") == "cold":
            continue
        updated_raw = meta.get("updated", "")
        updated_dt = _parse_dt_utc(updated_raw)
        if updated_dt is None:
            continue
        if (datetime.now(timezone.utc) - updated_dt).days < days:
            continue
        result = memory_archive(title)
        archived.append("{} -> {}".format(title, result))

    if archived:
        logger.info("ARCHIVE_OLD days=%d count=%d", days, len(archived))
        return "已归档以下记忆:\n- " + "\n- ".join(archived)
    return "没有超过 {} 天未更新的条目需要归档".format(days)

@mcp.tool(annotations=_RO)
def memory_smart_search(query: str, tag: str | None = None, limit: int = 10,
                        mem_type: str | None = None,
                        scope: str | None = None, project: str | None = None,
                        status: str | None = None) -> str:
    """Multi-field scored search across titles, tags, summaries, and bodies.

    mem_type (optional): 仅检索该 type 的条目（fact/preference/decision/...）。
    scope / project (optional): 仅检索该作用域 / 项目的条目；传 "any" 表示不过滤。
    status (optional): 生命周期过滤。**省略时默认排除 archived**；传 "any" 查看全部。
    """
    _t0 = time.perf_counter()
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    now = datetime.now(timezone.utc)
    hidden = 0

    def _tokenize(q: str) -> list[str]:
        # English/number words stay whole; contiguous CJK runs are split into
        # 2-grams so a whole Chinese phrase matches relevant fragments (P2-11).
        out: list[str] = []
        for tok in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", q.lower()):
            if len(tok) > 2 and re.fullmatch(r"[\u4e00-\u9fff]+", tok):
                out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
            else:
                out.append(tok)
        return out

    keywords = _tokenize(query)
    if not keywords:
        return "查询词无效"

    scored: list[tuple[float, str, str, str, str, str]] = []

    for f, meta, body in all_entries:
        title = _entry_title(meta, f)
        tags = _entry_tags(meta)
        summary = str(meta.get("summary", ""))
        tier = str(meta.get("tier", "warm"))
        source = str(meta.get("source", ""))
        updated_str = str(meta.get("updated", ""))

        if tag and tag not in tags:
            continue
        if mem_type and meta.get("type", DEFAULT_MEMORY_TYPE) != mem_type:
            continue
        if not _passes_lifecycle_filters(meta, status=status, scope=scope, project=project):
            hidden += 1
            continue

        title_lower = title.lower()
        summary_lower = summary.lower()
        body_lower = body.lower()[:2000]
        tags_lower = {t.lower() for t in tags}

        lexical = 0.0

        for kw in keywords:
            if kw == title_lower:
                lexical += 10.0
            elif kw in title_lower:
                lexical += 5.0

            if kw in tags_lower:
                lexical += 4.0

            if kw in summary_lower:
                lexical += 3.0

            body_count = body_lower.count(kw)
            lexical += min(body_count, 5) * 1.0

        if lexical <= 0:
            # 零词法命中即非候选。时间新鲜度只能作为**加权项**，不能单独决定入选：
            # 否则任意查询都会把所有「当天更新」的条目塞进结果——搜索质量评测的
            # 负样本用例实测过（查询「量子纠缠退相干」曾返回 10 条完全无关的记忆）。
            continue

        score = lexical
        if updated_str:
            updated_dt = _parse_dt_utc(updated_str)
            if updated_dt is not None:
                days_since = (datetime.now(timezone.utc) - updated_dt).days
                score += max(0, 2.0 - days_since * 0.02)

        scored.append((score, title, tier, summary[:80], source, _credential_badge(meta)))

    scored.sort(key=lambda x: -x[0])

    if not scored:
        _m_search("smart_search", 0, _t0)
        return '未找到与 "{}" 相关的结果'.format(query) + _filter_hint(hidden)

    _m_search("smart_search", len(scored), _t0)
    lines = ["# 搜索结果: {}".format(query), "共找到 {} 条相关记忆".format(len(scored)), ""]
    for score, title, tier, summary, source, cred in scored[:limit]:
        lines.append("- [{}] **{}** (score={:.1f}, {}){}".format(tier, title, score, source, cred))
        if summary:
            lines.append("  {}".format(summary))

    return "\n".join(lines) + _filter_hint(hidden)

@mcp.tool(annotations=_RO)
def memory_recent(days: int = 7, limit: int = 20,
                  output_format: str | None = None) -> str:
    """List entries updated within the last N days, most recent first.

    output_format（C1）：`"text"`（默认）或 `"json"`（`{"days", "count", "limit",
    "shown", "entries": [{"updated", "title", "summary"}]}`）。
    """
    try:
        fmt = _norm_output_format(output_format)
    except ValueError as e:
        return f"错误：{e}"
    now = datetime.now(timezone.utc)
    recent: list[tuple[str, str, str]] = []
    for f in _iter_md_files():
        try:
            meta, _ = _load_memory(f)
        except Exception:
            continue
        updated_str = str(meta.get("updated", ""))
        updated_dt = _parse_dt_utc(updated_str)
        if updated_dt is None:
            continue
        if (now - updated_dt).days <= days:
            title = _entry_title(meta, f)
            summary = str(meta.get("summary", ""))
            recent.append((updated_str, title, summary[:80]))
    if not recent:
        if fmt == "json":
            return _json_out({"days": days, "count": 0, "limit": limit,
                              "shown": 0, "entries": []})
        return f"近 {days} 天无更新"
    recent.sort(reverse=True)
    if fmt == "json":
        return _json_out({"days": days, "count": len(recent), "limit": limit,
                          "shown": min(limit, len(recent)),
                          "entries": [{"updated": u, "title": t, "summary": s}
                                      for u, t, s in recent[:limit]]})
    lines = [f"# 近 {days} 天更新（{len(recent)} 条）", ""]
    for updated_str, title, summary in recent[:limit]:
        lines.append(f"- {updated_str[:10]} [[{title}]]")
        if summary:
            lines.append(f"  {summary}")
    if len(recent) > limit:
        lines.append(f"\n... 共 {len(recent)} 条，显示前 {limit} 条")
    return "\n".join(lines)

@mcp.tool(annotations=_RO)
def memory_stats() -> str:
    """Show memory vault health statistics."""
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    now = datetime.now(timezone.utc)

    total = len(all_entries)
    tier_count: dict[str, int] = {"hot": 0, "warm": 0, "cold": 0}
    tag_counter: Counter = Counter()
    type_counter: Counter = Counter()        # P0：记忆类型分布
    confidence_counter: Counter = Counter()  # P0：可信度分布
    verified_count = 0                       # P0：已核验条目数
    scope_counter: Counter = Counter()       # P1：作用域分布
    status_counter: Counter = Counter()      # P1：生命周期分布
    context_counter: Counter = Counter()     # P1：写入方上下文分布
    schema_counter: Counter = Counter()      # P1：frontmatter 结构版本分布
    project_counter: Counter = Counter()     # P1：项目分布
    reads_list: list[tuple[int, str]] = []
    zero_reads: list[str] = []
    orphan_count = 0
    total_links = 0
    recent_7d = 0
    recent_30d = 0
    recent_90d = 0

    for f, meta, body in all_entries:
        t = meta.get("tier", "warm")
        tier_count[t] = tier_count.get(t, 0) + 1
        type_counter[meta.get("type", DEFAULT_MEMORY_TYPE)] += 1
        confidence_counter[meta.get("confidence", DEFAULT_CONFIDENCE)] += 1
        if meta.get("verified"):
            verified_count += 1
        scope_counter[meta.get("scope", DEFAULT_SCOPE)] += 1
        status_counter[meta.get("status", DEFAULT_STATUS)] += 1
        schema_counter[_schema_version(meta)] += 1
        _ctx = str(meta.get("source_context") or "").strip()
        if _ctx:
            context_counter[_ctx] += 1
        _proj = str(meta.get("project") or "").strip()
        if _proj:
            project_counter[_proj] += 1
        tag_counter.update(_entry_tags(meta))
        reads = meta.get("access_count", 0)
        if not isinstance(reads, int):
            reads = 0
        reads_list.append((reads, _entry_title(meta, f)))
        if reads == 0:
            zero_reads.append(_entry_title(meta, f))

        links = meta.get("links", [])
        if isinstance(links, list):
            total_links += len(links)

        updated_str = str(meta.get("updated", ""))
        updated_dt = _parse_dt_utc(updated_str)
        if updated_dt is not None:
            days = (now - updated_dt).days
            if days <= 7:
                recent_7d += 1
            if days <= 30:
                recent_30d += 1
            if days <= 90:
                recent_90d += 1

    # Build backlink index for orphan count
    backlinks: dict[str, set[str]] = {}
    for f, meta, body in all_entries:
        t = _entry_title(meta, f)
        body_links = _extract_wiki_links(body)
        for link in body_links:
            if link != t:
                backlinks.setdefault(link, set()).add(t)

    for f, meta, body in all_entries:
        t = _entry_title(meta, f)
        if t not in CORE_PAGES and t not in backlinks:
            orphan_count += 1

    reads_list.sort(key=lambda x: -x[0])
    top_tags = tag_counter.most_common(10)

    lines = ["# 记忆库健康度统计", ""]
    lines.append(f"## 概览")
    lines.append(f"- 总条目数: {total}")
    lines.append(f"- 活跃 hot: {tier_count.get('hot', 0)}")
    lines.append(f"- 常温 warm: {tier_count.get('warm', 0)}")
    lines.append(f"- 已归档 cold: {tier_count.get('cold', 0)}")
    lines.append(f"- 已核验 verified: {verified_count} 条")
    lines.append(f"- 孤立笔记: {orphan_count}（无反向链接）")
    lines.append(f"- 总双向链接数: {total_links}")
    lines.append("")
    lines.append("## 记忆类型分布（P0）")
    for mtype in MEMORY_TYPES:
        c = type_counter.get(mtype, 0)
        if c:
            lines.append(f"- {mtype}: {c} 条")
    lines.append("")
    lines.append("## 可信度分布（P0）")
    for lvl in CONFIDENCE_LEVELS:
        lines.append(f"- {lvl}: {confidence_counter.get(lvl, 0)} 条")
    lines.append(f"- 未核验（含 AI 推断待确认）: {total - verified_count} 条")
    lines.append("")
    lines.append("## 作用域分布（P1）")
    for sc in SCOPES:
        lines.append(f"- {sc}: {scope_counter.get(sc, 0)} 条")
    if project_counter:
        lines.append("- 项目分布:")
        for proj, c in project_counter.most_common(10):
            lines.append(f"  - {proj}: {c} 条")
    lines.append("")
    lines.append("## 生命周期分布（P1）")
    for st in LIFECYCLE_STATUSES:
        lines.append(f"- {st}: {status_counter.get(st, 0)} 条")
    lines.append("")
    lines.append("## 写入方上下文分布（P1）")
    if context_counter:
        for ctx, c in context_counter.most_common(10):
            lines.append(f"- {ctx}: {c} 条")
    else:
        lines.append("- （均未标注 source_context；可在各客户端 MCP 配置里设 "
                     "AI_MEMORY_SOURCE_CONTEXT 环境变量）")
    lines.append("")
    lines.append("## frontmatter 结构版本（P1）")
    for ver in sorted(schema_counter):
        label = "旧结构（未标注 schema_version）" if ver == 1 else "当前结构"
        lines.append(f"- v{ver}: {schema_counter[ver]} 条（{label}）")
    lines.append("")
    lines.append(f"## 访问频率")
    lines.append(f"- 从未读取: {len(zero_reads)} 条")
    for i, (r, title) in enumerate(reads_list[:10]):
        lines.append(f"  {i+1}. [[{title}]] — {r} 次")
    lines.append("")
    lines.append(f"## 活跃度")
    lines.append(f"- 近 7 天更新: {recent_7d} 条")
    lines.append(f"- 近 30 天更新: {recent_30d} 条")
    lines.append(f"- 近 90 天更新: {recent_90d} 条")
    lines.append(f"- 超 90 天未更新: {total - recent_90d} 条")
    lines.append("")
    lines.append(f"## 热门标签 TOP 10")
    for tag, count in top_tags:
        lines.append(f"- {tag}: {count} 条")
    lines.append("")
    lines.append("## 运行时阈值配置（B5）")
    _cfg_path = memory_config.config_path()
    if _cfg_path is None:
        lines.append("- 未注入 vault 位置 ⇒ 全部使用内置默认值")
    elif _cfg_path.exists():
        lines.append(f"- 配置来源: {_cfg_path.name}（vault 根，改动约 {memory_config.CACHE_TTL:g} 秒内生效）")
    else:
        lines.append(f"- 配置来源: 无 {memory_config.CONFIG_NAME} ⇒ 全部使用内置默认值"
                     f"（可在 vault 根新建该文件覆写）")
    for _k, _v in memory_config.all_values().items():
        _mark = "  ← 已覆写" if _v != memory_config.DEFAULTS[_k] else ""
        lines.append(f"  - {_k} = {_v}{_mark}")
    _cfg_err = memory_config.last_error()
    lines.append(f"- 配置错误: {_cfg_err}" if _cfg_err else "- 配置错误: 无")
    lines.append("")
    lines.extend(_metrics_report_lines())
    if zero_reads:
        lines.append(f"\n## 未读取条目（{len(zero_reads)} 条）")
        for title in zero_reads:
            lines.append(f"- [[{title}]]")

    return "\n".join(lines)


# =====================================================================
# 热重启 (2026-09-17)
# =====================================================================
# 目标：改完本文件（或任一模块）后，**不重连客户端、不重启应用**即加载新代码。
#
# 机制：os.execv 用同一解释器重新执行本脚本 —— **原地替换进程映像**，且继承已打开
# 的 fd 0/1/2 ⇒ MCP 的 stdio 管道不中断，客户端感知不到换过进程。本次调用的响应
# 必须先经管道发出，故真正的 execv 交给延迟的 daemon 线程执行。
#
# 三条经实测确认的硬约束（2026-09-17，本机 Windows + Python 3.13.12）：
#   ① **只能用 os.execv，绝不能用 os.execve**。execve 约 1/3 概率以 0xC0000005
#      (ACCESS_VIOLATION) 崩在调用瞬间（15 轮压测崩 5 轮），而 execv 20/20、
#      execv+预置 env 20/20 全通过。要给新进程传标记，直接 `os.environ[k] = v`
#      改当前进程环境块即可 —— execv 由 CRT 传入同一环境块，实测新进程读得到。
#   ② 换进程后**客户端不会重发 initialize**（它以为会话还在），而 ServerSession
#      默认以 NotInitialized 起步 ⇒ 一切请求都被
#      「Received request before initialization was complete」拒掉。故接力进程
#      必须把既有会话视为已初始化（见 _install_session_resume_patch）。
#   ③ Windows 上 execv 内部是 CreateProcess + ExitProcess，**pid 会变**，与
#      POSIX「真替换、pid 不变」语义不同。实测 MCP 客户端不因此断开会话。

_RESTART_ENABLED = os.environ.get("AI_MEMORY_RESTART", "1") not in ("0", "false", "False")
_RESTART_CHAIN_MAX = 5            # 接力链上限，防重启风暴
_RESTARTING = threading.Event()   # 本进程内已安排重启，拒绝重复排队
_RESTART_CHAIN = 0
_RESUME_PATCH_STATUS = "未安装"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


_RESTART_DELAY = _env_float("AI_MEMORY_RESTART_DELAY", 1.5)


def _install_session_resume_patch() -> str:
    """把「接力进程」的会话直接视为已初始化，返回人类可读的状态说明。

    为何不用 SDK 自带的 stateless：`ServerSession.__init__` 确实是
    ``Initialized if stateless else NotInitialized``，但 FastMCP 的 stdio 路径
    **不透传**该开关（`run_stdio_async` 调 `_mcp_server.run(read, write,
    init_options)` 不带 stateless；构造参数 `stateless_http` 只被
    streamable_http_app 消费）。故按同一语义直接补 `__init__`，仅本进程生效。

    实现上做两件防守：用 `*args/**kwargs` 透传（SDK 改签名时不会 TypeError）；
    只在校验到 `InitializationState.Initialized` 成员存在时才安装。**不要**用
    `hasattr(ServerSession, "_initialization_state")` 当判据 —— SDK 里该名字只在
    `__init__` 中作为**实例属性**赋值，类属性另有其名（`_initialized`），类上取不到，
    会恒为 False 从而静默跳过安装。真正的「装上了且生效」由 memory_restart 的预检
    用真实构造来验证，不靠属性名猜测。
    """
    try:
        from mcp.server.session import InitializationState, ServerSession
    except Exception as exc:  # pragma: no cover - 依赖 SDK 内部路径
        return f"导入 mcp.server.session 失败：{type(exc).__name__}: {exc}"
    if not hasattr(InitializationState, "Initialized"):
        return "SDK 的 InitializationState 缺少 Initialized 成员，补丁不适用"
    original = ServerSession.__init__
    if getattr(original, "_ai_memory_resume_patch", False):
        return "已安装（幂等，跳过重复包装）"

    def patched(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self._initialization_state = InitializationState.Initialized

    patched._ai_memory_resume_patch = True  # type: ignore[attr-defined]
    ServerSession.__init__ = patched  # type: ignore[method-assign]
    return "已安装"


def _read_restart_chain() -> int:
    try:
        return max(0, int(os.environ.get("AI_MEMORY_RESTART_CHAIN", "0") or 0))
    except (TypeError, ValueError):
        return 0


def _restart_argv() -> list[str]:
    """重放当前命令行（解释器 + 本脚本 + 原有参数）。

    刻意不提供「自定义重启命令」这类配置面：`python server.py` 是本项目的标准
    启动方式，重放 argv 即精确。异常启动方式（`python -c`、被包装器吞掉 argv）
    由预检拦下并提示改用 UI 重连，不引入第二套配置。
    """
    return [sys.executable] + list(sys.argv)


_PREFLIGHT_SNIPPET = (
    "import sys;"
    "sys.path.insert(0, {server_dir!r});"
    "import server;"
    "from mcp.server.session import ServerSession, InitializationState;"
    "assert callable(server.memory_restart), 'memory_restart 未注册';"
    "assert getattr(ServerSession.__init__, '_ai_memory_resume_patch', False), '会话恢复补丁未装上';"
    "s = ServerSession(None, None, server.mcp._mcp_server.create_initialization_options());"
    "assert s._initialization_state is InitializationState.Initialized, '会话未处于已初始化状态';"
    "print('PREFLIGHT-OK')"
)


def _restart_preflight() -> str | None:
    """返回 None 表示可以重启，否则返回必须放弃重启的原因。

    最关键的一条：**绝不能把起不来的代码换上**。stdio MCP 一旦新进程启动失败，
    客户端看到的是连接断开 —— 那比「改了没生效」糟糕得多。故先在子进程里试导入
    新代码，并顺带确认会话恢复补丁装得上；失败则原地不动，把错误原文交回调用方。

    ⚠️ **必须显式 `stdin=subprocess.DEVNULL`**（2026-09-17 实测踩到，本函数最初
    漏了它，导致预检必然 90s 超时、热重启 100% 被自己的预检拦下）：stdio MCP server
    的 stdin 是客户端管道，且已被 asyncio Proactor 以**挂起的重叠读**绑定；子进程
    继承该句柄后，自己启动阶段对 stdin 的探测会与父进程的挂起读互锁 ⇒ 子进程永远
    到不了第一行代码，`subprocess.run` 只能干等到 timeout。对照实测：继承管道
    stdin 20s 超时（连子进程的第一条 print 都读不到）/ `DEVNULL` 0.46s 正常返回。
    **同一陷阱对任何「MCP server 内 spawn 子进程」的代码都成立。**
    """
    argv = _restart_argv()
    script = argv[1] if len(argv) > 1 else ""
    if not script or not Path(script).is_file():
        return (f"\n  命令行中没有可重放的脚本文件（argv={argv!r}）。\n"
                "  若本服务不是以 `python <server.py>` 方式启动，请改用 UI「停用再启用」加载新代码。")
    env = {k: v for k, v in os.environ.items() if k != "AI_MEMORY_RESTART_CHAIN"}
    env["AI_MEMORY_RESUME"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PREFLIGHT_SNIPPET.format(server_dir=str(_SERVER_DIR))],
            cwd=str(_SERVER_DIR), env=env, capture_output=True, text=True, timeout=90,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        return f"\n  预检子进程无法启动：{type(exc).__name__}: {exc}"
    if proc.returncode != 0 or "PREFLIGHT-OK" not in (proc.stdout or ""):
        detail = (proc.stderr or proc.stdout or "(无输出)").strip().splitlines()[-6:]
        return ("\n  新代码预检未通过，已放弃重启（当前进程继续用旧代码服务）：\n"
                + "\n".join("    " + ln for ln in detail))
    return None


def _restart_prepare() -> str:
    """换进程前的收尾。execv **不触发 atexit**，故内存里攒着的状态必须手动落盘，
    否则最后一批指标与访问计数会随旧进程一起消失。"""
    done: list[str] = []
    try:
        if _METRICS_ENABLED:
            _METRICS.flush()
            done.append("指标")
    except Exception as exc:
        logger.debug("restart: metrics flush failed: %s", exc)
    try:
        n = _flush_access_counts()
        if n:
            done.append(f"访问计数×{n}")
    except Exception as exc:
        logger.debug("restart: access flush failed: %s", exc)
    return "、".join(done) if done else "无待落盘状态"


def _deferred_restart(argv: list[str], delay: float) -> None:
    """延迟换进程。等的是「本次工具响应已写进 stdio 管道」这件事 —— 它是 execv
    唯一的时序前提，且无法从应用层观测，故用可调的等待时间覆盖。"""
    time.sleep(delay)
    flushed = _restart_prepare()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os.environ["AI_MEMORY_RESUME"] = "1"
    os.environ["AI_MEMORY_RESTART_CHAIN"] = str(_RESTART_CHAIN + 1)
    logger.info("热重启：execv %s（已落盘：%s）", argv, flushed)
    try:
        os.execv(argv[0], argv)
    except OSError as exc:
        _RESTARTING.clear()
        logger.error("热重启失败（%s）：%s —— 本进程继续运行，请改用 UI 重连该 MCP 服务。",
                     argv[0], exc)


@mcp.tool(annotations=_WRITE_NONIDEM)
def memory_restart(delay: float | None = None) -> str:
    """Restart this MCP server in place so edited server.py / module code takes effect at once.

    用途：改完记忆库代码（server.py 或任一模块）后立刻生效，**不必重连客户端、
    不必重启应用**。实现是 os.execv 原地替换进程映像，并保留 stdin/stdout，因此
    MCP 的 stdio 会话不中断。

    执行顺序（三件事都做了才敢换进程）：
      1. **预检**：在子进程里试导入新代码，并确认「会话恢复补丁」装得上。任一失败
         即放弃重启 —— 绝不让起不来的代码顶掉正在服务的进程。
      2. 本次响应先经 stdio 管道发出（真正 execv 在延迟线程里执行）。
      3. 新进程通过环境变量得知自己是「接力进程」，会把客户端既有会话视为已初始化
         （否则客户端不重发 initialize，新进程会用 "Received request before
         initialization was complete" 拒掉一切请求）。

    注意：
      - 换进程后 **pid 会变**（Windows 上 execv 内部是 CreateProcess）。
      - 接力链上限 5 次，用于防重启风暴；超限后请用 UI 重连本服务复位。
      - 设 AI_MEMORY_RESTART=0 可整体禁用本工具。

    delay (optional): 换进程前的等待秒数，为「响应写出」留时间。默认取
        AI_MEMORY_RESTART_DELAY（1.5s），显式传入时被夹在 [0.1, 30]。

    返回值是安排结果（含将执行的命令与接力链计数），**不代表新代码已生效**；
    新代码从下一次调用起生效。
    """
    if not _RESTART_ENABLED:
        return ("热重启已被 AI_MEMORY_RESTART=0 禁用。"
                "请改用 UI「停用再启用」该 MCP 服务来加载新代码。")
    if _RESTARTING.is_set():
        return "本进程已有一个热重启在排队，忽略本次重复调用。"
    if _RESTART_CHAIN >= _RESTART_CHAIN_MAX:
        return (f"接力链已达上限（{_RESTART_CHAIN}/{_RESTART_CHAIN_MAX}），拒绝继续热重启 —— "
                "这是防重启风暴的保险。请用 UI 重连该 MCP 服务以复位计数。")

    blocked = _restart_preflight()
    if blocked:
        return "已取消热重启。" + blocked

    if delay is None:
        d = _RESTART_DELAY
    else:
        try:
            d = max(0.1, min(float(delay), 30.0))
        except (TypeError, ValueError):
            d = _RESTART_DELAY

    argv = _restart_argv()
    _RESTARTING.set()
    threading.Thread(target=_deferred_restart, args=(argv, d),
                     name="ai-memory-restart", daemon=True).start()
    return (
        f"热重启已安排：约 {d}s 后原地换进程（pid 会变，stdio 会话不变）。\n"
        f"  · 命令：{' '.join(argv)}\n"
        f"  · 接力链：{_RESTART_CHAIN + 1}/{_RESTART_CHAIN_MAX}\n"
        f"  · 接力会话恢复：{_RESUME_PATCH_STATUS}\n"
        "本次响应会先发出，之后才换进程，客户端**无需重连**；新代码从下一次调用起生效。\n"
        "若数秒后所有调用都超时，说明新进程未能起来，请用 UI 重连该 MCP 服务"
        "（旧代码与记忆库数据均未受影响）。"
    )


# 接力进程：把客户端既有会话视为已初始化，否则它会拒绝一切请求（约束②）。
if _RESTART_ENABLED and os.environ.get("AI_MEMORY_RESUME") == "1":
    _RESTART_CHAIN = _read_restart_chain()
    _RESUME_PATCH_STATUS = _install_session_resume_patch()
    logger.info("热重启接力：pid=%s 接力链=%s 会话恢复=%s",
                os.getpid(), _RESTART_CHAIN, _RESUME_PATCH_STATUS)


if __name__ == "__main__":
    mcp.run()
