#!/usr/bin/env python3
"""文本 / 时间 / 热度通用工具。

从 server.py 拆分而来（P3 模块化，2026-09-10）。本模块是**无状态纯函数层**：
不依赖 MEMORY_DIR、缓存、索引或任何全局状态，可独立测试。
（B5, 2026-10-08：`heat_score` 的衰减系数改从 `memory_config` 取；后者是最底层、
自身不反向依赖任何模块，故本层仍是「无 vault 全局状态」的。）

包含三类：
    - 文件名：safe_filename（标题 -> 安全文件名，标题中的 `/` 表示子目录）
              legacy_flat_filename（阶段二之前的扁平规则，仅供向后兼容查找）
    - wiki 链接：WIKI_LINK_RE / extract_wiki_links / clean_link_name
    - 时间与热度：parse_dt_utc / days_since_utc / heat_score
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import memory_config

__all__ = [
    "WIKI_LINK_RE",
    "HEAT_DECAY_LAMBDA",
    "safe_filename",
    "legacy_flat_filename",
    "extract_wiki_links",
    "clean_link_name",
    "parse_dt_utc",
    "days_since_utc",
    "heat_score",
]

# [[目标]] 或 [[目标|别名]] —— 捕获目标名，忽略别名
WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]|]+)?\]\]")

# 热度随时间衰减的系数：heat = reads / (1 + λ * days)
# B5（2026-10-08）：真值已外置到 memory.toml 的 `heat_decay_lambda`；本常量
# 保留为向后兼容别名，运行时的真值由 heat_score 内的 memory_config.get() 取。
HEAT_DECAY_LAMBDA = memory_config.DEFAULTS["heat_decay_lambda"]

# 单个路径段的最大字符数（阶段二：整条标题的 80 字上限改为**逐段**施加）
MAX_SEGMENT_CHARS = 80
# 文件名中必须替换掉的字符（Windows 保留字符 + 空白；`/` 不在其列 ⇒ 用作分隔符）
_ILLEGAL_CHARS_RE = re.compile(r'[\\:*?"<>|\s]+')
# 阶段二之前的口径：`/` 与其它非法字符**同属一个字符类**，因此「`a/ b`」这类
# 连续非法字符会被折叠成单个 `_`。兼容查找必须沿用这个折叠语义，不能拆成两步替换。
_LEGACY_FLAT_RE = re.compile(r'[\\/:*?"<>|\s]+')
# 控制字符直接丢弃（比替换成 `_` 更贴近原意，且不制造无意义下划线）
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(title: str) -> str:
    """把标题转换为安全文件名；标题中的 ``/`` 表示**子目录**（阶段二，2026-09-20）。

    例：``safe_filename("项目/记忆库MCP/条目")`` → ``"项目/记忆库MCP/条目.md"``。
    返回的是**相对路径**（不含盘符、不以分隔符开头），调用方自行与基准目录拼接，
    即 ``MEMORY_DIR / safe_filename(title)``。

    逐段消毒（对 ``/`` 切分出的每一段**独立**施加，规则彼此不串味）：

    * ``\\ : * ? " < > |`` 与空白 → ``_``；控制字符丢弃
    * 丢弃空段（顺带折叠重复的 ``/``）以及 ``.`` / ``..``（它们在 ``strip("._")``
      后必为空 ⇒ 天然消解，杜绝目录穿越）
    * 每段去掉首尾的 ``.`` 与 ``_``（Windows 不允许尾随点/空格）
    * 每段截断至 ``MAX_SEGMENT_CHARS``

    **无 ``/`` 的标题行为与阶段二之前逐字节一致**（单段即全串，便于回归比对）；
    所有段都被丢弃时退化为 ``memory.md``。

    ⚠️ 本函数只做**语法**消毒，不判断「哪些目录不允许写入」—— 那是 vault 的
    **政策**，见 ``server._title_path_error()``（保留目录 / 盘符 / 层级上限）。
    """
    parts: list[str] = []
    for raw in str(title).split("/"):
        seg = _CONTROL_CHARS_RE.sub("", raw)
        seg = _ILLEGAL_CHARS_RE.sub("_", seg).strip("._")
        if not seg:
            continue
        parts.append(seg[:MAX_SEGMENT_CHARS])
    return ("/".join(parts) + ".md") if parts else "memory.md"


def legacy_flat_filename(title: str) -> str:
    """阶段二（2026-09-20）之前的文件名规则：``/`` 与其它非法字符一样拍成 ``_``。

    **仅供向后兼容查找，不用于新建。** 历史确有标题含 ``/`` 的笔记
    （``轻装时代(QZSD)灯架/三脚架产品谱系`` 实存为 ``轻装时代(QZSD)灯架_三脚架产品谱系.md``）。
    放行 ``/`` 之后，若不按旧规则再找一遍，这类标题会被当成新路径，
    从而造出「同一标题两条记录」的重复 —— ``memory_write`` 的匹配循环会同时
    比对 ``f.stem == Path(legacy_flat_filename(title)).stem`` 来兜住它。
    """
    safe = _LEGACY_FLAT_RE.sub("_", str(title)).strip("._")
    safe = safe[:MAX_SEGMENT_CHARS] or "memory"
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
    """Heat score with time decay. Higher = more actively used.

    衰减系数 λ 取自 ``memory.toml`` 的 ``heat_decay_lambda``（B5，内置默认 0.2）。
    每次调用都现取 —— ``memory_config`` 内部有 2 秒 TTL 缓存，读取开销可忽略，
    而这样改配置无需重启 MCP 进程。
    """
    if not updated_str:
        return float(reads)
    days_since = days_since_utc(updated_str)
    return reads / (1 + memory_config.get("heat_decay_lambda") * max(days_since, 0))
