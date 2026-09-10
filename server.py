"""
Local shared memory MCP server.

Storage:
- Markdown files under a configurable vault directory (default: ~/ai-memory,
  override with the AI_MEMORY_DIR environment variable)
- One file per memory entry
- Frontmatter written as YAML (Obsidian-native), legacy JSON still readable

Features:
- 20 MCP tools: read/write/search/list/recent/graph/orphans/stats, batch tag &
  tier, heat-based tier suggestions, archive/restore, audit, rebuild_links,
  auto-generated MOC index (记忆索引.md)
- YAML frontmatter, Obsidian wikilinks, automatic link extraction,
  title-based dedup upsert, file locking, cross-process cache invalidation,
  SQLite metadata index for O(1) title lookup
"""

from __future__ import annotations

import atexit
import json
import re
import sqlite3
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

import memory_index as midx
import locks
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
    yaml_quote_scalar as _yaml_quote_scalar,
)

MEMORY_DIR = Path(os.environ.get("AI_MEMORY_DIR", "~/ai-memory")).expanduser().resolve()
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
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
                for f in d.glob("*.md"):
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
            rows = conn.execute(
                """SELECT * FROM entries WHERE title=? OR stem=?
                   ORDER BY (relpath LIKE '.archive/%') ASC, updated DESC""",
                (title, title),
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


def _resolve_unique_path(directory: Path, title: str) -> Path:
    """Pick a filename for a new entry, avoiding collisions with files that
    belong to a different title (P0-4).

    ``_safe_filename`` truncates at 80 chars, so two distinct titles can map to
    the same filename and silently overwrite each other. If the target already
    exists on disk AND its frontmatter title differs from ours, append _2/_3/...
    until free. If it exists and the title matches (e.g. a stale stub), reuse it.
    """
    base = _safe_filename(title)
    candidate = directory / base
    if not candidate.exists():
        return candidate
    try:
        meta, _ = _load_memory(candidate)
        if _entry_title(meta, candidate) == title or candidate.stem == title:
            return candidate
    except Exception:
        pass
    n = 2
    while True:
        alt = directory / f"{candidate.stem}_{n}{candidate.suffix}"
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
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
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
_ENTRY_CACHE: dict[str, tuple] = {}  # title -> (path, meta, body)
_CACHE_VALID = False
_DIR_MTIME = 0.0  # last seen MEMORY_DIR mtime, for cross-process cache invalidation
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
    for f in MEMORY_DIR.glob("*.md"):
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

def _entry_tags(meta: dict[str, Any]) -> list[str]:
    tags = meta.get("tags", [])
    if isinstance(tags, list):
        return [str(tag) for tag in tags if str(tag).strip()]
    if isinstance(tags, str) and tags.strip():
        return [tags.strip()]
    return []

def _entry_bucket(meta: dict[str, Any], path: Path) -> tuple[str, str]:
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


def _load_memory(path: Path) -> tuple[dict[str, Any], str]:
    meta, body = _parse_frontmatter(_read_text(path))
    return _apply_meta_defaults(meta), body

def _invalidate_cache() -> None:
    global _CACHE_VALID
    _CACHE_VALID = False

def _iter_entries() -> list[tuple[Path, dict[str, Any], str]]:
    global _CACHE_VALID, _ENTRY_CACHE, _DIR_MTIME
    # Cross-process invalidation: if another process (Obsidian edit, other MCP
    # client, git operation) changed the directory since we cached, rebuild.
    try:
        dir_mtime = MEMORY_DIR.stat().st_mtime
    except OSError:
        dir_mtime = 0.0
    if _CACHE_VALID and _ENTRY_CACHE and dir_mtime == _DIR_MTIME:
        return list(_ENTRY_CACHE.values())
    _DIR_MTIME = dir_mtime
    _ENTRY_CACHE.clear()
    entries: list[tuple[Path, dict[str, Any], str]] = []
    for f in sorted(MEMORY_DIR.glob("*.md"), reverse=True):
        try:
            meta, body = _load_memory(f)
        except Exception as e:
            logger.debug("_iter_entries: skipped %s: %s", f.name, e)
            continue
        t = _entry_title(meta, f)
        entries.append((f, meta, body))
        _ENTRY_CACHE[t] = (f, meta, body)
    _CACHE_VALID = True
    return entries


def _write_memory(path: Path, title: str, tags: list[str], source: str | None, content: str,
                  created: str | None = None, summary: str | None = None,
                  tier: str | None = None, access_count: int = 0,
                  links: list[str] | None = None, version: int | None = None,
                  mem_type: str | None = None, confidence: str | None = None,
                  verified: bool | None = None, verified_at: str | None = None,
                  scope: str | None = None, project: str | None = None,
                  status: str | None = None, supersedes: str | None = None,
                  conflicts: list[str] | None = None,
                  source_context: str | None = None) -> None:
    # Merge cached access_count increments into the written count
    cached = _ACCESS_CACHE.pop(title, 0)
    if cached:
        access_count += cached
    # 未显式传入的字段尝试从磁盘既有条目继承（version 防批量刷新清空，改进#10；
    # P0/P1 语义字段同理：更新正文不应把 type/confidence/verified/scope/status
    # 重置为默认值——否则「只改正文」会静默丢掉项目的隔离标记与生命周期状态）。
    if any(v is None for v in (version, mem_type, confidence, verified, scope, status,
                               project, supersedes, conflicts, source_context)):
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
        except Exception:
            pass
    # verified 为假时不应残留验证时间戳（避免"未验证却有 verified_at"的矛盾态）
    if not verified:
        verified_at = None
    frontmatter = _build_frontmatter(title, tags, source, created=created, summary=summary,
                                     tier=tier, access_count=access_count, links=links, version=version,
                                     mem_type=mem_type, confidence=confidence, verified=verified,
                                     verified_at=verified_at, scope=scope, project=project,
                                     status=status, supersedes=supersedes, conflicts=conflicts,
                                     source_context=source_context)
    _atomic_write_text(path, f"---\n{frontmatter}\n---\n\n{content}")


def _refresh_index() -> None:
    """Rebuild 记忆索引.md body from current entries (direct write, no recursion).

    Called after every successful memory_write so the index stays in sync
    without manual maintenance (improvement #7).
    """
    try:
        entries = _iter_entries()
        order = ["索引", "规则", "身份与配置", "项目", "工具", "运维", "修复",
                 "时间线", "模板", "AI相关", "其他"]
        groups: dict[str, list[str]] = {b: [] for b in order}
        for path, meta, _body in entries:
            title = _entry_title(meta, path)
            bucket, _reason = _entry_bucket(meta, path)
            groups.setdefault(bucket, []).append(title)
        lines = ["# 记忆索引（自动维护）", "",
                 "> 本索引由 ai-memory MCP 在每次写入后自动更新。如确需手工调整，请改各笔记自身而非此处。", ""]
        for bucket in order:
            items = groups.get(bucket, [])
            if not items:
                continue
            lines.append(f"## {bucket}")
            for title in sorted(set(items), key=lambda x: x.lower()):
                lines.append(f"- [[{title}]]")
            lines.append("")
        body = "\n".join(lines).rstrip()
        idx_path = MEMORY_DIR / "记忆索引.md"
        if idx_path.exists():
            meta, _ = _load_memory(idx_path)
        else:
            meta = {"title": "记忆索引", "tags": ["index", "moc", "navigation"],
                    "tier": "warm", "source": "ai-memory-server"}
        meta["updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_text(idx_path, f"---\n{_dump_yaml_frontmatter(meta)}\n---\n\n{body}")
    except Exception as e:
        logger.warning("_refresh_index failed: %s", e)


@mcp.tool()
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

    # 写入只匹配根目录条目；.archive 是只读归档（不参与写入匹配）。
    # 若根目录无此标题而归档中有，视为"归档后重建"——在根目录新建条目，
    # 归档保持原样。要修改归档内容请先 memory_restore。
    for f in list(MEMORY_DIR.glob("*.md")):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_title(meta, f) == title or f.stem == title:
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
                title=title,
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
            result_msg = f"已更新记忆: {title} ({existing_path.name})"
        else:
            filepath = _resolve_unique_path(MEMORY_DIR, title)
            _write_memory(filepath, title=title, tags=tags, source=effective_source, content=content,
                          summary=summary, tier=tier or "warm", version=new_version,
                          mem_type=mem_type, confidence=confidence, verified=verified,
                          verified_at=_today_utc() if verified else None,
                          scope=scope, project=project, status=status, supersedes=supersedes,
                          conflicts=conflicts, source_context=source_context)
            _maybe_auto_archive(title, filepath)
            logger.info("CREATE  title=%s tags=%s source=%s (new)", title, tags, source)
            result_msg = f"已创建记忆: {title} ({filepath.name})"
        # 改进#7：写入后自动维护索引（跳过索引自身，直接写文件防递归）
        if title != "记忆索引":
            _refresh_index()
        _m_inc("write_calls")
        _m_inc("write_updates" if existing_path else "write_creates")
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
BODY_TRUNCATE_CHARS = 8000  # memory_read 默认正文截断阈值，防超长笔记(如 近期工作动态 21KB)吃 token

@mcp.tool()
def memory_read(title: str, max_chars: int = BODY_TRUNCATE_CHARS) -> str:
    """Read a memory entry by exact title, with filename fallback for legacy files.

    Body is truncated to max_chars (default 8000) to bound token usage;
    pass max_chars=0 to get the full body.
    """
    _m_inc("read_calls")
    # 索引快速路径: title/stem 命中 + 文件新鲜 -> 直接读该文件, 免全库 glob+parse
    idx_hit = _resolve_title_via_index(title)
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
        if max_chars and max_chars > 0 and len(body) > max_chars:
            body = (body[:max_chars]
                    + f"\n\n... [正文共 {len(body)} 字符，已截断至前 {max_chars}。需要完整内容请以 max_chars=0 重读]")
        return f"[{meta_str}]\n\n{body}"

    # 回退: 全库 glob 扫描 (索引 miss / 文件被外部改动导致 stale)
    for f in list(MEMORY_DIR.glob("*.md")) + list((MEMORY_DIR / ".archive").glob("*.md")):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_title(meta, f) == title or f.stem == title:
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
            if max_chars and max_chars > 0 and len(body) > max_chars:
                body = (body[:max_chars]
                        + f"\n\n... [正文共 {len(body)} 字符，已截断至前 {max_chars}。需要完整内容请以 max_chars=0 重读]")
            return f"[{meta_str}]\n\n{body}"
    _m_inc("read_misses")
    return f"未找到标题为 '{title}' 的记忆"


@mcp.tool()
def memory_rebuild_links() -> str:
    """链接一致性校验（2026-09-06 起不再重写文件/写 links 字段）。

    links 已取消持久化（正文显式 [[双链]] 为唯一事实源），本工具改为只读
    校验：扫描全部根目录笔记正文，报告死链（指向不存在的标题）与计数，
    不修改任何文件、不刷新 mtime。
    """
    all_entries = _iter_entries()
    known = {_entry_title(meta, f) for f, meta, _ in all_entries}
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


@mcp.tool()
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
        for f in sorted(search_dir.glob("*.md"), reverse=True):
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
        _m_search("search", 0, _t0)
        return f"未找到与 '{keyword}' 相关的记忆" + _filter_hint(hidden)

    shown = results[:limit]
    total = len(results)
    _m_search("search", total, _t0)
    header = f"找到 {total} 条记忆" + (f"，显示前 {limit} 条" if total > limit else "") + ":\n\n"
    return header + "\n\n".join(shown) + _filter_hint(hidden)

@mcp.tool()
def memory_list(tag: str | None = None, limit: int = 20, tier: str | None = None,
                mem_type: str | None = None,
                scope: str | None = None, project: str | None = None,
                status: str | None = None) -> str:
    """List memory entries with a small preview.

    mem_type (optional): 仅列出该 type 的条目（fact/preference/decision/...）。
    scope / project (optional): 仅列出该作用域 / 项目的条目；传 "any" 表示不过滤。
    status (optional): 生命周期过滤。**省略时默认排除 archived**（归档条目不再出现
    在日常列表中）；传 "any" 查看全部，传具体值精确过滤。被隐藏的条目数会在末尾提示。
    """
    _t0 = time.perf_counter()
    entries: list[str] = []
    hidden = 0

    for f in sorted(MEMORY_DIR.glob("*.md"), reverse=True):
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

    if not entries:
        _m_search("list", 0, _t0)
        return "记忆库为空" + _filter_hint(hidden)

    total = len(entries)
    _m_search("list", total, _t0)
    entries = entries[:limit]
    result = "\n".join(entries)
    if total > limit:
        result += f"\n\n... 共 {total} 条，显示前 {limit} 条"
    return result + _filter_hint(hidden)

@mcp.tool()
@_locked_write
def memory_delete(title: str, trash: bool = True, purge: bool = False) -> str:
    """Delete a memory entry by exact title, with filename fallback for legacy files.

    trash=True (default) moves the file to .trash/ instead of unlinking, so a
    mistaken delete can be recovered. Pass purge=True (or trash=False) to
    permanently remove it (including a previously soft-deleted copy in .trash/).
    Archive entries (.archive/) are also matched.
    """
    search_dirs = [MEMORY_DIR]
    archive_dir = MEMORY_DIR / ".archive"
    if archive_dir.exists():
        search_dirs.append(archive_dir)
    if not trash or purge:
        trash_dir = MEMORY_DIR / ".trash"
        if trash_dir.exists():
            search_dirs.append(trash_dir)
    for f in [p for d in search_dirs for p in d.glob("*.md")]:
        try:
            meta, _ = _load_memory(f)
        except Exception:
            continue
        if _entry_title(meta, f) == title or f.stem == title:
            if trash and not purge:
                trash_dir = MEMORY_DIR / ".trash"
                trash_dir.mkdir(parents=True, exist_ok=True)
                dest = trash_dir / f.name
                n = 2
                while dest.exists():
                    dest = trash_dir / f"{f.stem}_{n}{f.suffix}"
                    n += 1
                f.replace(dest)
                _idx_remove_path(f)  # 软删移出库, 清索引行
                logger.info("DELETE(soft) title=%s -> %s", title, dest.name)
                _invalidate_cache()
                if title != "记忆索引":
                    _refresh_index()
                return f"已软删除记忆: {title} -> .trash/{dest.name}（如需彻底删除，用 purge=true）"
            f.unlink()
            _idx_remove_path(f)  # 永久删除, 清索引行
            logger.info("DELETE(purge) title=%s", title)
            _invalidate_cache()
            if title != "记忆索引":
                _refresh_index()
            return f"已永久删除记忆: {title} ({f.name})"
    return f"未找到标题为 '{title}' 的记忆"

@mcp.tool()
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
    for f in list(MEMORY_DIR.glob("*.md")) + list((MEMORY_DIR / ".archive").glob("*.md")):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_title(meta, f) == title or f.stem == title:
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

@mcp.tool()
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
        for f in d.glob("*.md"):
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

    for path, meta, body in entries:
        title = _entry_title(meta, path)
        bucket, reason = _entry_bucket(meta, path)
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


@mcp.tool()
def memory_index_draft() -> str:
    """Generate a draft of the main index without writing it back automatically."""
    entries = _iter_entries()
    if not entries:
        return "记忆库为空"

    groups: dict[str, list[tuple[str, str]]] = {
        "索引": [],
        "规则": [],
        "项目": [],
        "工具": [],
        "运维": [],
        "修复": [],
        "时间线": [],
        "身份与配置": [],
        "模板": [],
        "AI相关": [],
        "其他": [],
    }

    for path, meta, _body in entries:
        title = _entry_title(meta, path)
        bucket, _reason = _entry_bucket(meta, path)
        display = title if title == path.stem else f"{title}|{path.stem}"
        groups.setdefault(bucket, []).append((title, display))

    order = ["索引", "规则", "身份与配置", "项目", "工具", "运维", "修复", "时间线", "模板", "AI相关", "其他"]
    lines = ["# 记忆索引（半自动草稿）", "", "> 这个草稿由 MCP 自动扫描生成，建议人工确认后再回写到 `记忆索引.md`。", ""]

    for bucket in order:
        items = groups.get(bucket, [])
        if not items:
            continue
        lines.append(f"## {bucket}")
        for title, _display in sorted(items, key=lambda item: item[0].lower()):
            lines.append(f"- [[{title}]]")
        lines.append("")

    return "\n".join(lines).rstrip()

@mcp.tool()
@_locked_write
def memory_archive(title: str) -> str:
    """Archive a memory entry: move full content to .archive/, leave a summary stub."""
    archive_dir = MEMORY_DIR / ".archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""

    for f in MEMORY_DIR.glob("*.md"):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_title(meta, f) == title or f.stem == title:
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

@mcp.tool()
@_locked_write
def memory_restore(title: str) -> str:
    """Restore an archived entry back to the vault root (P1-7).

    Moves the full content from .archive/ back to MEMORY_DIR, replacing the stub
    left behind by memory_archive, and bumps tier back to warm.
    """
    archive_dir = MEMORY_DIR / ".archive"
    if not archive_dir.exists():
        return "归档目录不存在，无内容可恢复"

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""
    for f in archive_dir.glob("*.md"):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue
        if _entry_title(meta, f) == title or f.stem == title:
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
    for f in MEMORY_DIR.glob("*.md"):
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

@mcp.tool()
def memory_heat_suggest() -> str:
    """Scan entries and suggest tier changes based on access_count and staleness."""
    now = datetime.now(timezone.utc)
    suggestions: list[str] = []
    stats: list[tuple[str, str, str, int, str, str]] = []

    for f in sorted(MEMORY_DIR.glob("*.md"), reverse=True):
        try:
            meta, body = _load_memory(f)
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

        updated_raw = meta.get("updated", "")
        days_since_update = _days_since_utc(updated_raw)

        score = _heat_score(reads, str(meta.get("updated", "")))
        if score < 0.1 and tier_val != "cold":
            suggestions.append("- [{0}] {1}->cold | heat_score={2:.2f}, {3}d 未更新".format(title, tier_val, score, days_since_update))
        elif score < 0.5 and tier_val == "hot":
            suggestions.append("- [{0}] hot->warm | heat_score={1:.2f}, {2}d 未更新".format(title, score, days_since_update))
        elif score > 3.0 and tier_val != "hot":
            suggestions.append("- [{0}] {1}->hot | heat_score={2:.2f}, 访问频繁".format(title, tier_val, score))

    lines = ["# 热度分析建议", ""]
    lines.append("共 {0} 条记忆\n".format(len(stats)))

    lines.append("## 按访问次数排序")
    for title, tier_val, source, reads, updated_str, created_str in sorted(stats, key=lambda x: -x[3]):
        score = _heat_score(reads, updated_str)
        lines.append("- [{0}] {1} | {2} | score={3:.1f}, {4} 次读取 | {5}".format(created_str, title, tier_val, score, reads, source))

    lines.append("")
    if suggestions:
        lines.append("## 建议调整")
        lines.extend(suggestions[:30])
    else:
        lines.append("## 建议调整")
        lines.append("- 暂无需要调整的条目")

    return "\n".join(lines)

@mcp.tool()
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
        if _entry_title(meta, f) == title or f.stem == title:
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

@mcp.tool()
def memory_orphans() -> str:
    """Find entries with zero backlinks (isolated notes)."""
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()

    backlink_index: dict[str, list[str]] = {}
    title_map: dict[str, tuple[Path, dict[str, Any], str]] = {}

    for f, meta, body in all_entries:
        t = _entry_title(meta, f)
        title_map[t] = (f, meta, body)
        if t not in backlink_index:
            backlink_index[t] = []
        links = meta.get("links", [])
        if isinstance(links, list):
            for link in links:
                if link != t:
                    backlink_index.setdefault(link, [])
                    if t not in backlink_index[link]:
                        backlink_index[link].append(t)
        body_links = _extract_wiki_links(body)
        for link in body_links:
            if link != t:
                backlink_index.setdefault(link, [])
                if t not in backlink_index[link]:
                    backlink_index[link].append(t)

    core_pages = {"记忆索引", "近期工作动态", "用户画像", "AI身份档案",
                  "Codex 身份档案", "记忆库总规范", "共享记忆库规则"}
    orphans: list[str] = []
    for t in sorted(title_map.keys()):
        if t in core_pages:
            continue
        if not backlink_index.get(t):
            orphans.append(t)

    lines = ["# 孤立笔记 ({} 条)".format(len(orphans)), "",
             "以下笔记没有其他笔记引用它们：", ""]
    if orphans:
        for title in orphans:
            lines.append("- [[{}]]".format(title))
    else:
        lines.append("- (无孤立笔记)")

    return "\n".join(lines)

@mcp.tool()
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

@mcp.tool()
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

@mcp.tool()
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

@mcp.tool()
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

@mcp.tool()
def memory_recent(days: int = 7, limit: int = 20) -> str:
    """List entries updated within the last N days, most recent first."""
    now = datetime.now(timezone.utc)
    recent: list[tuple[str, str, str]] = []
    for f in MEMORY_DIR.glob("*.md"):
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
        return f"近 {days} 天无更新"
    recent.sort(reverse=True)
    lines = [f"# 近 {days} 天更新（{len(recent)} 条）", ""]
    for updated_str, title, summary in recent[:limit]:
        lines.append(f"- {updated_str[:10]} [[{title}]]")
        if summary:
            lines.append(f"  {summary}")
    if len(recent) > limit:
        lines.append(f"\n... 共 {len(recent)} 条，显示前 {limit} 条")
    return "\n".join(lines)

@mcp.tool()
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
    lines.extend(_metrics_report_lines())
    if zero_reads:
        lines.append(f"\n## 未读取条目（{len(zero_reads)} 条）")
        for title in zero_reads:
            lines.append(f"- [[{title}]]")

    return "\n".join(lines)
if __name__ == "__main__":
    mcp.run()
