#!/usr/bin/env python3
"""文本 / 时间 / 热度通用工具。

从 server.py 拆分而来（P3 模块化，2026-09-10）。本模块是**无状态纯函数层**：
不依赖 MEMORY_DIR、缓存、索引或任何全局状态，可独立测试。

包含三类：
    - 文件名：safe_filename（标题 -> 安全文件名）
    - wiki 链接：WIKI_LINK_RE / extract_wiki_links / clean_link_name
    - 时间与热度：parse_dt_utc / days_since_utc / heat_score
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "WIKI_LINK_RE",
    "HEAT_DECAY_LAMBDA",
    "safe_filename",
    "extract_wiki_links",
    "clean_link_name",
    "parse_dt_utc",
    "days_since_utc",
    "heat_score",
]

# [[目标]] 或 [[目标|别名]] —— 捕获目标名，忽略别名
WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]|]+)?\]\]")

# 热度随时间衰减的系数：heat = reads / (1 + λ * days)
HEAT_DECAY_LAMBDA = 0.2


def safe_filename(title: str) -> str:
    """把标题转换为安全文件名：替换非法字符、截断至 80 字符、补 .md 后缀。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("._")
    safe = safe[:80] or "memory"
    return f"{safe}.md"


def extract_wiki_links(body: str) -> list[str]:
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


def clean_link_name(link: str) -> str:
    """Normalize a wiki link name: strip whitespace and stray backslashes (improvement #2)."""
    return link.strip().replace("\\", "")


def parse_dt_utc(value: Any) -> datetime | None:
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


def days_since_utc(value: Any) -> int:
    """Whole days between an ISO timestamp and now (UTC); 999 if unparseable."""
    dt = parse_dt_utc(value)
    if dt is None:
        return 999
    return (datetime.now(timezone.utc) - dt).days


def heat_score(reads: int, updated_str: str) -> float:
    """Heat score with time decay. Higher = more actively used."""
    if not updated_str:
        return float(reads)
    days_since = days_since_utc(updated_str)
    return reads / (1 + HEAT_DECAY_LAMBDA * max(days_since, 0))
