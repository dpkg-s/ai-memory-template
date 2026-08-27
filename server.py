"""
Local shared memory MCP server (ai-memory).

Storage:
- Markdown files under a configurable vault directory (default: D:/ai记忆,
  override with the AI_MEMORY_DIR environment variable)
- One file per memory entry
- Frontmatter written as YAML (Obsidian-native), legacy JSON still readable

Features:
- 20 MCP tools: read/write/search/list/recent/graph/orphans/stats, batch tag &
  tier, heat-based tier suggestions, archive/restore, audit, rebuild_links,
  auto-generated MOC index (记忆索引.md)
- YAML frontmatter, Obsidian wikilinks, automatic link extraction,
  title-based dedup upsert, file locking, cross-process cache invalidation
"""

from __future__ import annotations

import json
import re
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

WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]|]+)?\]\]")
HEAT_DECAY_LAMBDA = 0.2

from mcp.server.fastmcp import FastMCP

# Vault location. Defaults to ~/ai-memory (as created by install.sh /
# install.ps1). Override with the AI_MEMORY_DIR env var to point at your own
# vault, e.g.  AI_MEMORY_DIR=D:/ai记忆 python server.py
MEMORY_DIR = Path(os.environ.get("AI_MEMORY_DIR", "~/ai-memory")).expanduser().resolve()
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_LOCK = MEMORY_DIR / ".memory.lock"
LOCK_TIMEOUT = 15


mcp = FastMCP("ai-memory", instructions="本地共享 Markdown 记忆库，支持多个 AI 工具读写")

def _safe_filename(title: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("._")
    safe = safe[:80] or "memory"
    return f"{safe}.md"

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

def _extract_wiki_links(body: str) -> list[str]:
    """Extract [[wiki link]] page names from body text, deduplicated."""
    links = WIKI_LINK_RE.findall(body)
    seen: set[str] = set()
    result: list[str] = []
    for link in links:
        normalized = link.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result

def _parse_dt_utc(value: Any) -> datetime | None:
    """Parse an ISO datetime string, treating naive (tzinfo-less) values as UTC.

    Legacy files may store ``updated``/``created`` without a timezone; comparing
    those against ``datetime.now(timezone.utc)`` raises TypeError and is silently
    swallowed by callers, making the entry appear stale or invisible. This helper
    normalizes naive timestamps to UTC so all downstream comparisons work.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _days_since_utc(value: Any) -> int:
    """Whole days between an ISO timestamp and now (UTC); 999 if unparseable."""
    dt = _parse_dt_utc(value)
    if dt is None:
        return 999
    return (datetime.now(timezone.utc) - dt).days

def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically: temp file in the same dir, then os.replace.

    Plain ``write_text`` truncates the target first; a crash or a concurrent
    reader can observe an empty or half-written file. Writing to a sibling
    temp file and atomically replacing avoids partial reads (fixes P0-1).
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

def _heat_score(reads: int, updated_str: str) -> float:
    """Heat score with time decay. Higher = more actively used."""
    if not updated_str:
        return float(reads)
    days_since = _days_since_utc(updated_str)
    return reads / (1 + HEAT_DECAY_LAMBDA * max(days_since, 0))

def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")

def _split_inline_list(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None

    for ch in value:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue

        if ch in {'"', "'"}:
            quote = ch
            current.append(ch)
            continue

        if ch == ",":
            items.append("".join(current).strip())
            current = []
            continue

        current.append(ch)

    if current:
        items.append("".join(current).strip())

    return items

def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""

    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in _split_inline_list(inner)]

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]

    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass

    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except ValueError:
            pass

    return value

def _parse_yaml_frontmatter(raw: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    lines = raw.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" not in line:
            i += 1
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        i += 1

        if not key:
            continue

        if value:
            meta[key] = _parse_scalar(value)
            continue

        if i < len(lines) and lines[i].lstrip().startswith("- "):
            items: list[Any] = []
            while i < len(lines):
                item_line = lines[i]
                item_stripped = item_line.strip()
                if not item_stripped:
                    i += 1
                    continue
                if not item_line.lstrip().startswith("- "):
                    break
                items.append(_parse_scalar(item_line.lstrip()[2:].strip()))
                i += 1
            meta[key] = items
            continue

        block: list[str] = []
        while i < len(lines):
            next_line = lines[i]
            if not next_line.strip():
                block.append("")
                i += 1
                continue
            if next_line.startswith(" ") or next_line.startswith("\t"):
                block.append(next_line.strip())
                i += 1
                continue
            break
        meta[key] = "\n".join(block).strip()

    return meta

def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    content = _strip_bom(content)
    if not content.startswith("---"):
        return {}, content.strip()

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content.strip()

    raw_meta = parts[1].strip()
    body = parts[2].lstrip("\r\n")

    if not raw_meta:
        return {}, body.strip()

    try:
        meta = json.loads(raw_meta)
        if isinstance(meta, dict):
            return meta, body.strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return _parse_yaml_frontmatter(raw_meta), body.strip()

_KNOWN_TITLES: list[str] = []
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

_lock_state = threading.local()  # re-entrancy depth per thread

def _acquire_lock() -> bool:
    """Cross-process file lock with staleness check.

    Re-entrant within the same thread: nested acquisitions (e.g. memory_write
    -> _maybe_auto_archive -> memory_archive) only bump a depth counter instead
    of re-creating the lock file, which would deadlock the current design.
    """
    if getattr(_lock_state, "depth", 0) > 0:
        _lock_state.depth += 1
        return True
    start = time.time()
    while True:
        try:
            with open(MEMORY_LOCK, "x", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.time()}, f)
            _lock_state.depth = 1
            return True
        except FileExistsError:
            try:
                with open(MEMORY_LOCK, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if time.time() - data.get("time", 0) > LOCK_TIMEOUT:
                    MEMORY_LOCK.unlink(missing_ok=True)
                    continue
            except (json.JSONDecodeError, OSError):
                MEMORY_LOCK.unlink(missing_ok=True)
                continue
            time.sleep(0.2)
            if time.time() - start > LOCK_TIMEOUT:
                return False
    return False

def _release_lock() -> None:
    depth = getattr(_lock_state, "depth", 0)
    if depth > 1:
        _lock_state.depth = depth - 1
        return
    _lock_state.depth = 0
    try:
        MEMORY_LOCK.unlink(missing_ok=True)
    except OSError:
        pass

def _rebuild_title_index() -> None:
    global _KNOWN_TITLES
    _KNOWN_TITLES = []
    for f in MEMORY_DIR.glob("*.md"):
        try:
            meta, _ = _load_memory(f)
            _KNOWN_TITLES.append(_entry_title(meta, f))
        except Exception:
            logger.debug("_rebuild_title_index: skipped %s", f.name)


def _flush_access_counts() -> int:
    """Flush pending access_count increments to disk. Returns number of entries flushed."""
    if not _ACCESS_CACHE:
        return 0
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
                meta["updated"] = datetime.now(timezone.utc).isoformat()
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
    global _KNOWN_TITLES
    if not _KNOWN_TITLES:
        _rebuild_title_index()
    """Detect known page titles in body and add as wiki links.

    Skip titles shorter than 3 chars (e.g. "AI", "X") to avoid polluting
    `links` with accidental substring matches in the body.
    """
    result = set(links)
    content_lower = content.lower()
    for t in _KNOWN_TITLES:
        if len(t.strip()) < 3:
            continue
        if t.lower() in content_lower and t not in result:
            result.add(t)
    return sorted(result)

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

def _load_memory(path: Path) -> tuple[dict[str, Any], str]:
    return _parse_frontmatter(_read_text(path))

def _invalidate_cache() -> None:
    global _CACHE_VALID
    _CACHE_VALID = False

def _iter_entries() -> list[tuple[Path, dict[str, Any], str]]:
    global _KNOWN_TITLES
    if not _KNOWN_TITLES:
        _rebuild_title_index()
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

def _yaml_quote_scalar(value: Any) -> str:
    """Quote a scalar for YAML safety (handles colons, #, brackets, quotes, leading/trailing space)."""
    s = str(value)
    needs_quote = (
        s == ""
        or s.strip() != s
        or re.search(r'[:#\[\]\{\},&*?|<>=!%@`"\']', s) is not None
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'
    return s


def _dump_yaml_frontmatter(meta: dict[str, Any]) -> str:
    """Serialize frontmatter as YAML (Obsidian-native) instead of JSON.

    Improves on the old JSON frontmatter so Obsidian property/tag/dataview
    panels recognize the metadata (improvement #1).
    """
    lines: list[str] = []
    for key, value in meta.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                items = [_yaml_quote_scalar(v) for v in value]
                lines.append(f"{key}: [{', '.join(items)}]")
        else:
            lines.append(f"{key}: {_yaml_quote_scalar(value)}")
    return "\n".join(lines)


def _build_frontmatter(title: str, tags: list[str], source: str | None, created: str | None = None,
                       summary: str | None = None, tier: str | None = None, access_count: int = 0,
                       links: list[str] | None = None, version: int | None = None) -> str:
    now = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "title": title,
        "tags": tags,
        "created": created or now,
        "updated": now,
        "tier": tier or "warm",
        "access_count": access_count,
    }
    if source:
        meta["source"] = source
    if summary:
        meta["summary"] = summary
    if links:
        meta["links"] = links
    if version is not None:
        meta["version"] = version
    return _dump_yaml_frontmatter(meta)

def _write_memory(path: Path, title: str, tags: list[str], source: str | None, content: str,
                  created: str | None = None, summary: str | None = None,
                  tier: str | None = None, access_count: int = 0,
                  links: list[str] | None = None, version: int | None = None) -> None:
    # Merge cached access_count increments into the written count
    cached = _ACCESS_CACHE.pop(title, 0)
    if cached:
        access_count += cached
    # 若未显式传入 version，尝试保留已有 version（避免批量/刷新操作清掉，改进#10）
    if version is None:
        try:
            em, _ = _load_memory(path)
            version = em.get("version")
        except Exception:
            version = None
    frontmatter = _build_frontmatter(title, tags, source, created=created, summary=summary,
                                     tier=tier, access_count=access_count, links=links, version=version)
    _atomic_write_text(path, f"---\n{frontmatter}\n---\n\n{content}")

def _clean_link_name(link: str) -> str:
    """Normalize a wiki link name: strip whitespace and stray backslashes (improvement #2)."""
    return link.strip().replace("\\", "")


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
                 expected_version: int | None = None) -> str:
    """Write a memory entry, updating an existing one with the same title if found.

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

    # auto-generate summary from first 100 chars of content if not provided
    if not summary:
        clean = content.replace("\n", " ").strip()
        summary = clean[:100] + ("..." if len(clean) > 100 else "")

    links = _extract_wiki_links(content)
    links = [_clean_link_name(l) for l in links]  # 改进#2
    tags = _auto_suggest_tags(content, tags)
    links = _auto_link_titles(content, links)

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
            continue  # 单个损坏/被占用的文件不应阻断写入
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
                links=links,
                summary=summary,
                tier=old_tier,
                access_count=old_count if isinstance(old_count, int) else 0,
                version=new_version,
            )
            # Auto-archive: if tier is cold or very-low-heat, move to archive
            _maybe_auto_archive(title, existing_path)
            logger.info("UPDATE  title=%s tags=%s source=%s", title, tags, source)
            result_msg = f"已更新记忆: {title} ({existing_path.name})"
        else:
            filepath = _resolve_unique_path(MEMORY_DIR, title)
            _write_memory(filepath, title=title, tags=tags, source=effective_source, content=content,
                          summary=summary, tier=tier or "warm", links=links, version=new_version)
            # 新建的 cold 条目同样自动归档（与更新分支行为一致）
            _maybe_auto_archive(title, filepath)
            logger.info("CREATE  title=%s tags=%s source=%s (new)", title, tags, source)
            result_msg = f"已创建记忆: {title} ({filepath.name})"
        # 改进#7：写入后自动维护索引（跳过索引自身，直接写文件防递归）
        if title != "记忆索引":
            _refresh_index()
        return result_msg
    finally:
        _flush_access_counts()
        _release_lock()

@mcp.tool()
def memory_read(title: str) -> str:
    """Read a memory entry by exact title, with filename fallback for legacy files."""
    for f in list(MEMORY_DIR.glob("*.md")) + list((MEMORY_DIR / ".archive").glob("*.md")):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue  # 单个损坏/被占用的文件不应阻断读取
        if _entry_title(meta, f) == title or f.stem == title:
            # increment access_count in memory cache (lazy write-back)
            _ACCESS_CACHE[title] = _ACCESS_CACHE.get(title, 0) + 1
            if len(_ACCESS_CACHE) >= _ACCESS_FLUSH_THRESHOLD:
                _flush_access_counts()

            meta_str = " | ".join(f"{k}={str(v).replace(chr(10), '\\\\n')}" for k, v in meta.items())
            return f"[{meta_str}]\n\n{body}"
    return f"未找到标题为 '{title}' 的记忆"
@mcp.tool()
@_locked_write
def memory_rebuild_links() -> str:
    """Rescan all root entries' bodies and refresh the `links` frontmatter field.

    Fixes legacy notes whose `links` field is out of sync with their actual
    wiki links (improvement #3).
    """
    _invalidate_cache()
    count = 0
    for f in sorted(MEMORY_DIR.glob("*.md")):
        meta, body = _load_memory(f)
        links = _extract_wiki_links(body)
        links = [_clean_link_name(l) for l in links]
        links = _auto_link_titles(body, links)
        meta["links"] = links
        meta["updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_text(f, f"---\n{_dump_yaml_frontmatter(meta)}\n---\n\n{body}")
        count += 1
    logger.info("REBUILD_LINKS count=%d", count)
    return f"已刷新 {count} 条笔记的 links 字段（改进#3）"


@mcp.tool()
def memory_search(keyword: str, tag: str | None = None, limit: int = 20) -> str:
    """Search memories by keyword across title, tags, and body (recent-first).

    limit caps the number of returned matches (default 20). Keyword occurrences
    in the body snippet are wrapped in ** for visibility.
    """
    if not keyword or not keyword.strip():
        return "错误：搜索关键词不能为空。"
    results: list[str] = []
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

            title = _entry_title(meta, f)
            tags_str = " ".join(entry_tags)
            searchable = f"{title} {tags_str} {body}".lower()

            if keyword_lower in searchable:
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
                tags_display = ", ".join(entry_tags)
                summary_display = f"\n  {summary}" if summary else ""

                results.append(
                    f"- [{title}] ({f.name}) | {tags_display} | tier={tier} | reads={count}{source_info}{summary_display}{context}"
                )

    if not results:
        return f"未找到与 '{keyword}' 相关的记忆"

    shown = results[:limit]
    total = len(results)
    header = f"找到 {total} 条记忆" + (f"，显示前 {limit} 条" if total > limit else "") + ":\n\n"
    return header + "\n\n".join(shown)

@mcp.tool()
def memory_list(tag: str | None = None, limit: int = 20, tier: str | None = None) -> str:
    """List memory entries with a small preview."""
    entries: list[str] = []

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

        title = _entry_title(meta, f)
        tags_display = ", ".join(entry_tags)
        created = str(meta.get("created", ""))[:10]
        source = meta.get("source", "")
        tier_val = meta.get("tier", "warm")
        reads = meta.get("access_count", 0)
        source_info = f" | {source}" if source else ""

        # prefer summary field, fallback to body[:40]
        summary = meta.get("summary", "")
        if summary:
            preview = summary[:80].replace("\n", " ").strip()
        else:
            preview = body[:40].replace("\n", " ").strip()
        preview += "..." if len(preview) >= (40 if not summary else 80) else ""

        entries.append(f"- [{created}] {title} [{tier_val}|{reads}]{source_info} | {tags_display}\n  {preview}")

    if not entries:
        return "记忆库为空"

    total = len(entries)
    entries = entries[:limit]
    result = "\n".join(entries)
    if total > limit:
        result += f"\n\n... 共 {total} 条，显示前 {limit} 条"
    return result

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
        # purge=True 也要搜索 .trash，否则软删副本永远无法连带清除（与 docstring 承诺一致）
        trash_dir = MEMORY_DIR / ".trash"
        if trash_dir.exists():
            search_dirs.append(trash_dir)
    for f in [p for d in search_dirs for p in d.glob("*.md")]:
        try:
            meta, _ = _load_memory(f)
        except Exception:
            continue  # 单个损坏/被占用的文件不应阻断删除
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
                logger.info("DELETE(soft) title=%s -> %s", title, dest.name)
                _invalidate_cache()
                if title != "记忆索引":
                    _refresh_index()
                return f"已软删除记忆: {title} -> .trash/{dest.name}（如需彻底删除，用 purge=true）"
            f.unlink()
            logger.info("DELETE(purge) title=%s", title)
            _invalidate_cache()
            if title != "记忆索引":
                _refresh_index()
            return f"已永久删除记忆: {title} ({f.name})"
    return f"未找到标题为 '{title}' 的记忆"

@mcp.tool()
@_locked_write
def memory_update_metadata(title: str, tier: str | None = None, tags: list[str] | None = None,
                           summary: str | None = None, source: str | None = None) -> str:
    """Update only the frontmatter metadata of an entry without rewriting its body.

    Useful for changing tier/tags/summary/source of an existing note while keeping
    its full content intact (avoids the full rewrite that memory_write requires).
    Pass None to leave a field unchanged; pass an empty list to clear tags.
    """
    for f in list(MEMORY_DIR.glob("*.md")) + list((MEMORY_DIR / ".archive").glob("*.md")):
        try:
            meta, body = _load_memory(f)
        except Exception:
            continue  # 单个损坏/被占用的文件不应阻断元数据更新
        if _entry_title(meta, f) == title or f.stem == title:
            if tier is not None:
                meta["tier"] = tier
            if tags is not None:
                meta["tags"] = tags
            if summary is not None:
                meta["summary"] = summary
            if source is not None:
                meta["source"] = source
            old_version = meta.get("version", 0)
            meta["version"] = (old_version + 1) if isinstance(old_version, int) else 1
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
    lines.append("## 需要人工补齐")
    lines.extend(attention[:60] if attention else ["- 暂无"])

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
            continue  # 单个损坏/被占用的文件不应阻断归档
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
        "access_count": target_meta.get("access_count", 0),
        "archived_to": ".archive/{0}".format(archive_path.name),
        "created": str(target_meta.get("created", "")),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    if target_meta.get("source"):
        stub_meta["source"] = target_meta["source"]

    stub_fm = _dump_yaml_frontmatter(stub_meta)
    stub_body = "> \u26a1 本条记忆已归档。完整内容在 `.archive/{0}`\n> 需要恢复的话跟我说一声就行。\n\n{1}".format(target_path.name, summary)
    _atomic_write_text(target_path, "---\n{0}\n---\n\n{1}".format(stub_fm, stub_body))

    _invalidate_cache()
    logger.info("ARCHIVE title=%s", title)
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
            continue  # 单个损坏/被占用的文件不应阻断恢复
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
            continue  # 单个损坏/被占用的文件不应阻断恢复
        if _entry_title(m, f) == target_title or f.stem == target_title:
            stub = f
            break

    restored_meta = dict(target_meta)
    restored_meta["tier"] = "warm"
    restored_meta.pop("archived_to", None)
    restored_meta["updated"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_text(stub, f"---\n{_dump_yaml_frontmatter(restored_meta)}\n---\n\n{target_body}")
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
def memory_graph(title: str) -> str:
    """Show which pages this entry links to and which pages link to it (backlinks)."""
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
        links_in_entry = meta.get("links", [])
        if isinstance(links_in_entry, list) and title in links_in_entry:
            backlinks.append(entry_title)
            continue
        if title in _extract_wiki_links(body):
            if entry_title not in backlinks:
                backlinks.append(entry_title)

    lines = ["# {} 的链接图谱".format(title), ""]
    lines.append("## 出链 ({} 条)".format(len(outlinks)))
    if outlinks:
        for link in sorted(outlinks):
            lines.append("- [[{}]]".format(link))
    else:
        lines.append("- (无)")
    lines.append("")
    lines.append("## 反向链接 ({} 条)".format(len(backlinks)))
    if backlinks:
        for link in sorted(backlinks):
            lines.append("- [[{}]]".format(link))
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
    # 直接复用全局 CORE_PAGES，与 _maybe_auto_archive 的保护范围保持一致
    # （此前本地副本少了 2 个页面，会被批量归档误伤）
    now = datetime.now(timezone.utc)
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    archived: list[str] = []

    for f, meta, body in all_entries:
        title = _entry_title(meta, f)
        if title in CORE_PAGES:
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
def memory_smart_search(query: str, tag: str | None = None, limit: int = 10) -> str:
    """Multi-field scored search across titles, tags, summaries, and bodies."""
    all_entries: list[tuple[Path, dict[str, Any], str]] = _iter_entries()
    now = datetime.now(timezone.utc)

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

    scored: list[tuple[float, str, str, str, str]] = []

    for f, meta, body in all_entries:
        title = _entry_title(meta, f)
        tags = _entry_tags(meta)
        summary = str(meta.get("summary", ""))
        tier = str(meta.get("tier", "warm"))
        source = str(meta.get("source", ""))
        updated_str = str(meta.get("updated", ""))

        if tag and tag not in tags:
            continue

        title_lower = title.lower()
        summary_lower = summary.lower()
        body_lower = body.lower()[:2000]
        tags_lower = {t.lower() for t in tags}

        score = 0.0

        for kw in keywords:
            if kw == title_lower:
                score += 10.0
            elif kw in title_lower:
                score += 5.0

            if kw in tags_lower:
                score += 4.0

            if kw in summary_lower:
                score += 3.0

            body_count = body_lower.count(kw)
            score += min(body_count, 5) * 1.0

        if updated_str:
            updated_dt = _parse_dt_utc(updated_str)
            if updated_dt is not None:
                days_since = (datetime.now(timezone.utc) - updated_dt).days
                score += max(0, 2.0 - days_since * 0.02)

        if score > 0:
            scored.append((score, title, tier, summary[:80], source))

    scored.sort(key=lambda x: -x[0])

    if not scored:
        return '未找到与 "{}" 相关的结果'.format(query)

    lines = ["# 搜索结果: {}".format(query), "共找到 {} 条相关记忆".format(len(scored)), ""]
    for score, title, tier, summary, source in scored[:limit]:
        lines.append("- [{}] **{}** (score={:.1f}, {})".format(tier, title, score, source))
        if summary:
            lines.append("  {}".format(summary))

    return "\n".join(lines)

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
    lines.append(f"- 孤立笔记: {orphan_count}（无反向链接）")
    lines.append(f"- 总双向链接数: {total_links}")
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
    if zero_reads:
        lines.append(f"\n## 未读取条目（{len(zero_reads)} 条）")
        for title in zero_reads:
            lines.append(f"- [[{title}]]")

    return "\n".join(lines)
if __name__ == "__main__":
    mcp.run()
