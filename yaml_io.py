#!/usr/bin/env python3
"""YAML frontmatter 解析与序列化。

从 server.py 拆分而来（P3 模块化，2026-09-10）。本模块是**无状态纯函数层**：
不依赖 MEMORY_DIR、缓存、索引或任何全局状态，可独立测试与替换。

设计要点：
    - 解析端 (parse_*) 与序列化端 (dump_*) 必须保持转义对称，
      否则含反斜杠的标量（如 Windows 路径）会在每轮「读-改-写」中翻倍。
      对应修复见 _yaml_unescape_double 的 docstring。
    - 兼容两种 frontmatter：YAML（当前格式，Obsidian 原生可读）
      与 JSON（历史格式，仍可读）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "strip_bom",
    "read_text",
    "split_inline_list",
    "yaml_unescape_double",
    "parse_scalar",
    "parse_yaml_frontmatter",
    "parse_frontmatter",
    "yaml_quote_scalar",
    "dump_yaml_frontmatter",
    "build_frontmatter",
    "MEMORY_TYPES",
    "CONFIDENCE_LEVELS",
    "DEFAULT_MEMORY_TYPE",
    "DEFAULT_CONFIDENCE",
    "DEFAULT_VERIFIED",
    "SCOPES",
    "DEFAULT_SCOPE",
    "LIFECYCLE_STATUSES",
    "DEFAULT_STATUS",
    "SCHEMA_VERSION",
]

# ── P0 可信度字段取值域（2026-09-10） ─────────────────────────────────────
# 目的：区分「事实 / 偏好 / 决策 / 经验」并标注可信度与验证状态，遏制 AI
# 自身推断被后续会话当成事实复用（Memory Poisoning / Stale Memory）。
# 取值域集中定义于此，解析端与序列化端共用，避免两边漂移。
MEMORY_TYPES = (
    "fact",        # 明确事实
    "preference",  # 用户偏好
    "decision",    # 项目/技术决策
    "experience",  # 经验总结
    "episodic",    # 事件记录
    "project",     # 项目上下文
    "constraint",  # 约束条件
    "workflow",    # 工作流/习惯
    "temporary",   # 临时信息
)
CONFIDENCE_LEVELS = ("high", "medium", "low")
DEFAULT_MEMORY_TYPE = "fact"
DEFAULT_CONFIDENCE = "medium"
DEFAULT_VERIFIED = False

# ── P1 作用域 / 生命周期 / 冲突字段取值域（2026-09-10） ───────────────────
# scope 解决「项目记忆污染其他项目」。**只做字段层，不做目录分层**：
# 目录分层（global/ projects/ temporary/）会打散既有 143+ 篇笔记的组织方式，
# 且 server 的 15 处 glob 全为非递归，搬文件即脱离检索；字段层同样能满足
# 「项目记忆不干扰其他项目」，且零迁移、可随时回退。
SCOPES = ("global", "project", "temporary")
DEFAULT_SCOPE = "global"

# status 是**生命周期**维度，与 verified（可信度）正交，不可互相替代：
#   candidate  候选 / 草稿，尚未确认
#   active     生效中（默认）
#   stale      可能过期，待复核（仍参与检索，但排序靠后）
#   deprecated 已被取代（通常由 supersedes 指向它的条目出现）
#   archived   已归档（冷存；默认不参与检索，需显式查询）
LIFECYCLE_STATUSES = ("candidate", "active", "stale", "deprecated", "archived")
DEFAULT_STATUS = "active"

# frontmatter 结构版本：旧笔记缺失该字段时按 1 对待，被写入时升到 2。
# 目的是给将来的结构迁移留下判定依据，而不是现在就要求全库迁移。
SCHEMA_VERSION = 2


def strip_bom(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


def read_text(path) -> str:
    """读文本并按 UTF-8 BOM 自动去标记（utf-8-sig）。"""
    return path.read_text(encoding="utf-8-sig")


def split_inline_list(value: str) -> list[str]:
    """按逗号切分 YAML 行内列表，尊重引号内的逗号。"""
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


_YAML_DOUBLE_ESCAPES = {
    "\\": "\\",
    '"': '"',
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "e": "\x1b",
    " ": " ",
}


def yaml_unescape_double(inner: str) -> str:
    """Decode YAML double-quoted string escapes.

    Counterpart of _yaml_quote_scalar (which escapes only \\ and ").
    Handles the common YAML escape set so round-tripping a scalar that
    contains backslashes (e.g. Windows paths) no longer doubles them on
    every read-modify-write cycle. Fixes backslash snowball in summaries.
    """
    out: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch == "\\" and i + 1 < n:
            nxt = inner[i + 1]
            if nxt in _YAML_DOUBLE_ESCAPES:
                out.append(_YAML_DOUBLE_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "x" and i + 3 < n:
                try:
                    out.append(chr(int(inner[i + 2:i + 4], 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(inner[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if nxt == "U" and i + 9 < n:
                try:
                    out.append(chr(int(inner[i + 2:i + 10], 16)))
                    i += 10
                    continue
                except ValueError:
                    pass
        out.append(ch)
        i += 1
    return "".join(out)


def parse_scalar(value: str) -> Any:
    """解析单个 YAML 标量：null/bool/int/float/引号字符串/行内列表。"""
    value = value.strip()
    if not value:
        return ""

    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item) for item in split_inline_list(inner)]

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        if value[0] == '"':
            return yaml_unescape_double(value[1:-1])
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


def parse_yaml_frontmatter(raw: str) -> dict[str, Any]:
    """解析 YAML frontmatter 文本（不含 --- 分隔符）。

    支持：标量、行内列表 [a, b]、块列表 (- item)、多行块标量（缩进行）。
    """
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
            meta[key] = parse_scalar(value)
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
                items.append(parse_scalar(item_line.lstrip()[2:].strip()))
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


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """切分 frontmatter 与正文。

    优先按 YAML 解析；若 frontmatter 是合法 JSON（历史格式），按 JSON 解析。
    返回 (meta, body)。
    """
    content = strip_bom(content)
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

    return parse_yaml_frontmatter(raw_meta), body.strip()


def yaml_quote_scalar(value: Any) -> str:
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


def dump_yaml_frontmatter(meta: dict[str, Any]) -> str:
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
                items = [yaml_quote_scalar(v) for v in value]
                lines.append(f"{key}: [{', '.join(items)}]")
        else:
            lines.append(f"{key}: {yaml_quote_scalar(value)}")
    return "\n".join(lines)


def build_frontmatter(title: str, tags: list[str], source: str | None, created: str | None = None,
                      summary: str | None = None, tier: str | None = None, access_count: int = 0,
                      links: list[str] | None = None, version: int | None = None,
                      mem_type: str | None = None, confidence: str | None = None,
                      verified: bool | None = None, verified_at: str | None = None,
                      scope: str | None = None, project: str | None = None,
                      status: str | None = None, supersedes: str | None = None,
                      conflicts: list[str] | None = None,
                      source_context: str | None = None) -> str:
    """构造 frontmatter 文本（含 title/tags/created/updated/tier/access_count 等）。

    P0 可信度字段（type/confidence/verified）与 P1 生命周期字段
    （scope/status/schema_version）**总是写出**，非法或缺失值回落到模块级默认，
    使新笔记自带完整语义标签；旧笔记则在下次被写入时渐进补齐（读取端的默认值
    注入见 server._apply_meta_defaults）。

    按需写出的字段（project / supersedes / conflicts / source_context）只在有值时
    出现，避免给绝大多数条目增加无意义的空行。
    """
    now = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "title": title,
        "tags": tags,
        "created": created or now,
        "updated": now,
        "tier": tier or "warm",
        "access_count": access_count,
        "type": mem_type if mem_type in MEMORY_TYPES else DEFAULT_MEMORY_TYPE,
        "confidence": confidence if confidence in CONFIDENCE_LEVELS else DEFAULT_CONFIDENCE,
        "verified": bool(verified) if verified is not None else DEFAULT_VERIFIED,
    }
    if verified_at:
        meta["verified_at"] = verified_at
    meta["scope"] = scope if scope in SCOPES else DEFAULT_SCOPE
    if project:
        meta["project"] = project
    meta["status"] = status if status in LIFECYCLE_STATUSES else DEFAULT_STATUS
    meta["schema_version"] = SCHEMA_VERSION
    if supersedes:
        meta["supersedes"] = supersedes
    if conflicts:
        meta["conflicts"] = conflicts
    if source_context:
        meta["source_context"] = source_context
    if source:
        meta["source"] = source
    if summary:
        meta["summary"] = summary
    if links:
        meta["links"] = links
    if version is not None:
        meta["version"] = version
    return dump_yaml_frontmatter(meta)
