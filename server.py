"""
Local shared memory MCP server.

Storage:
- Markdown files under D:/ai记忆
- One file per memory entry
- Frontmatter may be JSON or YAML for backward compatibility

The server now normalizes reads so legacy files with a UTF-8 BOM or YAML
frontmatter remain readable while new writes always use JSON frontmatter.
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

logger = logging.getLogger("ai-memory")
_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)

WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]|]+)?\]\]")
HEAT_DECAY_LAMBDA = 0.2

from mcp.server.fastmcp import FastMCP

MEMORY_DIR = Path(os.environ.get("AI_MEMORY_DIR", "./memory")).resolve()
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_LOCK = MEMORY_DIR / ".memory.lock"
LOCK_TIMEOUT = 15


mcp = FastMCP("ai-memory", instructions="Shared Markdown memory vault for cross-AI tools")

def _safe_filename(title: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("._")
    safe = safe[:80] or "memory"
    return f"{safe}.md"

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

def _heat_score(reads: int, updated_str: str) -> float:
    """Heat score with time decay. Higher = more actively used."""
    if not updated_str:
        return float(reads)
    try:
        updated_dt = datetime.fromisoformat(str(updated_str))
        days_since = (datetime.now(timezone.utc) - updated_dt).days
    except (ValueError, TypeError):
        days_since = 999
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
    "deploy": "deploy", "部署": "deploy", "docker": "deploy",
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

def _acquire_lock() -> bool:
    """Cross-process file lock with staleness check."""
    start = time.time()
    while True:
        try:
            with open(MEMORY_LOCK, "x", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.time()}, f)
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
                new_fm = json.dumps(meta, ensure_ascii=False, indent=2)
                f.write_text(f"---\n{new_fm}\n---\n\n{body}", encoding="utf-8")
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
    """Detect known page titles in body and add as wiki links."""
    result = set(links)
    content_lower = content.lower()
    for t in _KNOWN_TITLES:
        if t.lower() in content_lower and t not in result:
            result.add(t)
    return sorted(result)

def _locked_write(fn):
    """Decorator to wrap write operations with lock."""
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
    global _CACHE_VALID, _ENTRY_CACHE
    if _CACHE_VALID and _ENTRY_CACHE:
        return list(_ENTRY_CACHE.values())
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

def _build_frontmatter(title: str, tags: list[str], source: str | None, created: str | None = None,
                       summary: str | None = None, tier: str | None = None, access_count: int = 0,
                       links: list[str] | None = None) -> str:
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
    return json.dumps(meta, ensure_ascii=False, indent=2)

def _write_memory(path: Path, title: str, tags: list[str], source: str | None, content: str,
                  created: str | None = None, summary: str | None = None,
                  tier: str | None = None, access_count: int = 0,
                  links: list[str] | None = None) -> None:
    # Merge cached access_count increments into the written count
    cached = _ACCESS_CACHE.pop(title, 0)
    if cached:
        access_count += cached
    frontmatter = _build_frontmatter(title, tags, source, created=created, summary=summary,
                                     tier=tier, access_count=access_count, links=links)
    path.write_text(f"---\n{frontmatter}\n---\n\n{content}", encoding="utf-8")

@mcp.tool()
def memory_write(title: str, content: str, tags: list[str] | None = None, source: str | None = None,
                 summary: str | None = None, tier: str | None = None) -> str:
    """Write a memory entry, updating an existing one with the same title if found."""
    tags = tags or []

    # auto-generate summary from first 100 chars of content if not provided
    if not summary:
        clean = content.replace("\n", " ").strip()
        summary = clean[:100] + ("..." if len(clean) > 100 else "")

    links = _extract_wiki_links(content)
    tags = _auto_suggest_tags(content, tags)
    links = _auto_link_titles(content, links)

    existing_path: Path | None = None
    existing_meta: dict[str, Any] = {}

    for f in list(MEMORY_DIR.glob("*.md")) + list((MEMORY_DIR / ".archive").glob("*.md")):
        meta, _ = _load_memory(f)
        if _entry_title(meta, f) == title:
            existing_path = f
            existing_meta = meta
            break

    _invalidate_cache()
    locked = _acquire_lock()
    try:
        if existing_path:
            old_count = existing_meta.get("access_count", 0)
            old_tier = tier or existing_meta.get("tier", "warm")
            _write_memory(
                existing_path,
                title=title,
                tags=tags,
                source=source or existing_meta.get("source"),
                content=content,
                created=str(existing_meta.get("created")) if existing_meta.get("created") else None,
                links=links,
            summary=summary,
            tier=old_tier,
            access_count=old_count if isinstance(old_count, int) else 0,
        )
            # Auto-archive: if tier is cold or very-low-heat, move to archive
            _maybe_auto_archive(title, existing_path)
            logger.info("UPDATE  title=%s tags=%s source=%s", title, tags, source)
            return f"已更新记忆: {title} ({existing_path.name})"

        filepath = MEMORY_DIR / _safe_filename(title)
        _write_memory(filepath, title=title, tags=tags, source=source, content=content,
                      summary=summary, tier=tier or "warm", links=links)
        logger.info("CREATE  title=%s tags=%s source=%s (new)", title, tags, source)
        return f"已创建记忆: {title} ({filepath.name})"
    finally:
        _flush_access_counts()
        _release_lock()

@mcp.tool()
def memory_read(title: str) -> str:
    """Read a memory entry by exact title, with filename fallback for legacy files."""
    for f in list(MEMORY_DIR.glob("*.md")) + list((MEMORY_DIR / ".archive").glob("*.md")):
        meta, body = _load_memory(f)
        if _entry_title(meta, f) == title:
            # increment access_count in memory cache (lazy write-back)
            _ACCESS_CACHE[title] = _ACCESS_CACHE.get(title, 0) + 1
            if len(_ACCESS_CACHE) >= _ACCESS_FLUSH_THRESHOLD:
                _flush_access_counts()

            meta_str = " | ".join(f"{k}={v}" for k, v in meta.items())
            return f"[{meta_str}]\n\n{body}"
    return f"未找到标题为 '{title}' 的记忆"
@mcp.tool()
def memory_search(keyword: str, tag: str | None = None) -> str:
    """Search memories by keyword across title, tags, and body."""
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
                    if start > 0:
                        ctx = "..." + ctx
                    if end < len(body):
                        ctx = ctx + "..."
                    context = f"\n  → {ctx}"

                source = meta.get("source", "")
                source_info = f" | 来源: {source}" if source else ""
                tags_display = ", ".join(entry_tags)
                summary_display = f"\n  📋 {summary}" if summary else ""

                results.append(
                    f"- [{title}] ({f.name}) | {tags_display} | tier={tier} | reads={count}{source_info}{summary_display}{context}"
                )

    if not results:
        return f"未找到与 '{keyword}' 相关的记忆"

    return f"找到 {len(results)} 条记忆:\n\n" + "\n\n".join(results[:20])

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
def memory_delete(title: str) -> str:
    _invalidate_cache()
    """Delete a memory entry by exact title, with filename fallback for legacy files."""
    for f in MEMORY_DIR.glob("*.md"):
        meta, _ = _load_memory(f)
        if _entry_title(meta, f) == title:
            f.unlink()
            return f"已删除记忆: {title} ({f.name})"
            logger.info("DELETE  title=%s", title)
    return f"未找到标题为 '{title}' 的记忆"

@mcp.tool()
def memory_audit() -> str:
    """Scan the vault and summarize what should be indexed or cleaned up manually."""
    entries = _iter_entries()
    if not entries:
        return "记忆库为空"

    bucket_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    attention: list[str] = []
    suggestions: list[str] = []

    for path, meta, _body in entries:
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
def memory_archive(title: str) -> str:
    """Archive a memory entry: move full content to .archive/, leave a summary stub."""
    archive_dir = MEMORY_DIR / ".archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    target_path: Path | None = None
    target_meta: dict[str, Any] = {}
    target_body = ""

    for f in MEMORY_DIR.glob("*.md"):
        meta, body = _load_memory(f)
        if _entry_title(meta, f) == title:
            target_path = f
            target_meta = meta
            target_body = body
            break

    if not target_path:
        return f"未找到标题为 '{title}' 的记忆"

    if target_meta.get("tier") == "cold" and target_meta.get("archived_to"):
        return f"记忆 '{title}' 已在归档中"

    archive_path = archive_dir / target_path.name
    meta_copy = dict(target_meta)
    meta_copy["tier"] = "cold"
    meta_copy["updated"] = datetime.now(timezone.utc).isoformat()
    archive_fm = json.dumps(meta_copy, ensure_ascii=False, indent=2)
    archive_path.write_text(f"---\n{archive_fm}\n---\n\n{target_body}", encoding="utf-8")

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
        "archived_to": ".archive/{0}".format(target_path.name),
        "created": str(target_meta.get("created", "")),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    if target_meta.get("source"):
        stub_meta["source"] = target_meta["source"]

    stub_fm = json.dumps(stub_meta, ensure_ascii=False, indent=2)
    stub_body = "> \u26a1 本条记忆已归档。完整内容在 `.archive/{0}`\n> 需要恢复的话跟我说一声就行。\n\n{1}".format(target_path.name, summary)
    target_path.write_text("---\n{0}\n---\n\n{1}".format(stub_fm, stub_body), encoding="utf-8")

    logger.info("ARCHIVE title=%s", title)
    return "已归档: {0} -> .archive/{1}".format(title, target_path.name)

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
        if isinstance(updated_raw, str) and updated_raw:
            try:
                updated_dt = datetime.fromisoformat(updated_raw)
                days_since_update = (now - updated_dt).days
            except (ValueError, TypeError):
                days_since_update = 999
        else:
            days_since_update = 999

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
        score = _heat_score(reads, str(meta.get("updated", "")))
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
        if _entry_title(meta, f) == title:
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
def memory_archive_old(days: int = 90) -> str:
    """Archive entries not updated in N days."""
    _invalidate_cache()
    core_pages = {"记忆索引", "近期工作动态", "用户画像", "AI身份档案", "Codex 身份档案",
                  "Claude Code 身份档案", "记忆库总规范", "共享记忆库规则", "记忆半自动整理流程",
                  "AI交互配置", "AI 对话自动归档提示词"}
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
        if isinstance(updated_raw, str) and updated_raw:
            try:
                updated_dt = datetime.fromisoformat(updated_raw)
                if (now - updated_dt).days < days:
                    continue
            except (ValueError, TypeError):
                pass
        else:
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

    keywords = [w.lower() for w in re.findall(r"[\w\u4e00-\u9fff]+", query)]
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
            try:
                updated_dt = datetime.fromisoformat(updated_str)
                days_since = (now - updated_dt).days
                score += max(0, 2.0 - days_since * 0.02)
            except (ValueError, TypeError):
                pass

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
        if updated_str:
            try:
                updated_dt = datetime.fromisoformat(updated_str)
                days = (now - updated_dt).days
                if days <= 7:
                    recent_7d += 1
                if days <= 30:
                    recent_30d += 1
                if days <= 90:
                    recent_90d += 1
            except (ValueError, TypeError):
                pass

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
