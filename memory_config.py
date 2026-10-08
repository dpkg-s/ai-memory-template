# -*- coding: utf-8 -*-
"""运行时阈值配置（B5, 2026-10-08）—— vault 根 ``memory.toml`` + 环境变量 + 热重载。

## 为什么不用 `tomllib`

清单原文建议用 `tomllib`（标准库，3.11+），理由是「不违背零外部依赖」。但本仓 CI
矩阵**仍覆盖 Python 3.10**，而 `tomllib` 是 3.11 才进标准库 ⇒ 直接用它当场打穿那
一格。两条出路：把最低版本抬到 3.11（属**破坏性变更**，远超 B5 的范围），或**自写
最小解析器**。选后者 —— 这与本仓既有的「手写 YAML 解析」（`yaml_io`）是同一条路线。

## 支持的最小子集（**窄，且不支持的一律报错**）

    # 注释
    body_truncate_chars = 8000      # 整数
    dup_title_jaccard   = 0.6       # 浮点
    heat_verified       = true      # 布尔
    note                = "文本"     # 单/双引号字符串

    [thresholds]                    # 可选小节；与顶层键同义（小节优先）
    lock_timeout = 15

**数组、内联表、多行字符串、日期、裸词** 等一律 `ValueError`，并给出**行号与原文**
—— 宁可拒绝、不要惊喜：配置写错时静默取默认值，会让「我明明改了却不生效」极难排查。

## 取值优先级

    环境变量 AI_MEMORY_CFG_<KEY>  >  [thresholds] 区  >  顶层同名键  >  内置默认

## 热重载

TTL 2 秒：TTL 内直接回内存缓存；过期则**重读文件内容**，内容与上次不同才**重解析**。
⇒ 改完 `memory.toml` 约 2 秒内生效，无需重启 MCP 进程。TTL 不是懒，而是给阈值读取
**封顶一次读盘**的开销 —— 去重阈值在配对循环里会被反复读。

⚠️ 变更检测**用内容签名，不用 `(mtime_ns, size)`**：实测（本机 NTFS）mtime 粒度约
**0.51 ms**，同尺寸的连续两次改写在 20 次里 **10 次** mtime_ns 完全相同 ⇒ 会**漏掉**
真改动。配置文件仅几百字节，直接比内容成本可忽略、语义可靠。

## 错误处理口径

配置出错**绝不打挂服务**：解析失败时 `get()` 回落到**默认值**（而不是 crash 或静默用
上次的旧值），并把错误记进 `last_error()`，由 `memory_stats` 的「配置」节对外暴露。
⇒ 写错配置 = 有可见提示 + 行为可预期，而不是一个查不出来的怪现象。

## 分层

本模块**不 import 本仓任何其它模块**（最底层）；`locks` / `text_utils` / `yaml_io` 等
工具层可依赖它，反之绝不允许（由 `tests/test_import_layers.py` 机器守卫）。
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-memory.config")

CONFIG_NAME = "memory.toml"
ENV_PREFIX = "AI_MEMORY_CFG_"
CACHE_TTL = 2.0                      # 秒；改配置后最迟这么久生效

# 内置默认值 —— 与 B5 之前硬编码在各模块里的值**逐个相同** ⇒ 没有 memory.toml 时行为零变化。
DEFAULTS: dict[str, Any] = {
    "dup_title_jaccard": 0.6,        # 标题 bigram Jaccard ≥ 此值 → 「疑似重复标题」
    "dup_body_jaccard": 0.5,         # 正文 bigram Jaccard ≥ 此值 → 「疑似重复/冲突内容」
    "body_truncate_chars": 8000,     # memory_read/memory_context 的默认整段字符预算
    "lock_timeout": 15.0,            # .memory.lock 的陈旧回收秒数
    "heat_cold_max": 0.1,            # 热度 < 此值且非 cold → 降为 cold
    "heat_demote_hot_max": 0.5,      # 热度 < 此值且是 hot → 降为 warm
    "heat_hot_min": 3.0,             # 热度 > 此值且非 hot → 升为 hot
    "heat_decay_lambda": 0.2,        # 热度时间衰减系数（越大衰减越快）
}

# 键 -> 期望类型（int 与 float 分开，防「阈值写成 8000.0」这类类型漂移）
_SPEC: dict[str, type] = {
    "dup_title_jaccard": float,
    "dup_body_jaccard": float,
    "body_truncate_chars": int,
    "lock_timeout": float,
    "heat_cold_max": float,
    "heat_demote_hot_max": float,
    "heat_hot_min": float,
    "heat_decay_lambda": float,
}

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_STR_RE = re.compile(r"""^(?P<q>["'])(?P<s>.*)(?P=q)$""")

_vault_dir: Path | None = None
_last_error: str | None = None
_cache: dict[str, Any] = {"at": -1e9, "sig": None, "values": {}}


# ---------------------------------------------------------------- 解析
def _strip_comment(line: str) -> str:
    """去掉行内注释，但**尊重引号内的 `#`**（`note = "a#b"` 不能被切掉）。"""
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _parse_value(raw: str, where: str) -> Any:
    raw = raw.strip()
    if not raw:
        raise ValueError(f"{where}：`=` 后缺少值")
    # 三引号必须在普通引号分支**之前**判：单行的 `"""a"""` 也能被普通字符串正则匹配上，
    # 那样会把它当成 `""a""` 静默接受 —— 而 TOML 里它是「多行字符串」的语法，语义不同。
    if raw.startswith('"""') or raw.startswith("'''"):
        raise ValueError(f"{where}：不支持多行字符串 -> {raw!r}")
    if raw[0] in "\"'":
        m = _STR_RE.match(raw)
        if not m:
            raise ValueError(f"{where}：字符串未正确闭合 -> {raw!r}")
        return m.group("s")
    if raw == "true":
        return True
    if raw == "false":
        return False
    if _INT_RE.match(raw):
        return int(raw)
    if _FLOAT_RE.match(raw):
        return float(raw)
    if raw.startswith("[") or raw.startswith("{"):
        raise ValueError(f"{where}：不支持数组/内联表（本模块只外置标量阈值）-> {raw!r}")
    raise ValueError(f"{where}：无法解析的值 -> {raw!r}"
                     "（只支持 整数 / 浮点 / true·false / 带引号字符串）")


def parse_toml(text: str) -> dict[str, Any]:
    """把最小 TOML 子集解析成**扁平**字典；小节内的键写作 ``section.key``。

    独立成公开函数（不读盘）是为了可单测 —— 解析器的边界情况远多于「读一个文件」，
    必须能在不造目录的前提下逐条验。
    """
    out: dict[str, Any] = {}
    section = ""
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        where = f"memory.toml 第 {lineno} 行"
        if line.startswith("["):
            if not line.endswith("]"):
                raise ValueError(f"{where}：小节未闭合 -> {raw_line.strip()!r}")
            name = line[1:-1].strip()
            if not name or not all(_KEY_RE.match(p) for p in name.split(".")):
                raise ValueError(f"{where}：非法小节名 -> {raw_line.strip()!r}")
            section = name
            continue
        if "=" not in line:
            raise ValueError(f"{where}：既不是小节，也不是 `key = value` -> {raw_line.strip()!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            raise ValueError(f"{where}：非法键名 -> {raw_line.strip()!r}")
        full = f"{section}.{key}" if section else key
        if full in out:
            raise ValueError(f"{where}：键重复 -> {full}")
        out[full] = _parse_value(value, where)
    return out


def _coerce(key: str, value: Any) -> Any:
    """按 `_SPEC` 校验并归一化类型。int 与 float 严格区分（bool 是 int 子类，须先挡）。"""
    want = _SPEC[key]
    if isinstance(value, bool):
        raise ValueError(f"配置 {key}：期望 {want.__name__}，得到布尔值")
    if want is int:
        if not isinstance(value, int):
            raise ValueError(f"配置 {key}：期望整数，得到 {type(value).__name__}（{value!r}）")
        return value
    if not isinstance(value, (int, float)):
        raise ValueError(f"配置 {key}：期望数值，得到 {type(value).__name__}（{value!r}）")
    return float(value)


# ---------------------------------------------------------------- 装载
def set_vault(path: str | os.PathLike[str] | None) -> None:
    """由 `server.py` 在解析出 `MEMORY_DIR` 后调用。

    本模块**不反向 import server**（分层铁律），故 vault 位置由调用方注入。
    换目录会同时清缓存，避免把上一个 vault 的配置带过去。
    """
    global _vault_dir
    new = None if path is None else Path(path)
    if new != _vault_dir:
        _vault_dir = new
        _cache.update(at=-1e9, sig=None, values={})


def config_path() -> Path | None:
    """当前生效的配置文件路径；未注入 vault 时返回 None（纯默认值模式）。"""
    return None if _vault_dir is None else _vault_dir / CONFIG_NAME


def last_error() -> str | None:
    """最近一次配置错误（解析失败 / 取值失败）；无错返回 None。由 memory_stats 暴露。"""
    return _last_error


def reload() -> None:
    """强制丢弃缓存（测试与「立刻生效」场景用）。"""
    _cache.update(at=-1e9, sig=None, values={})


def _set_error(msg: str | None) -> None:
    global _last_error
    if msg and msg != _last_error:
        logger.warning("配置问题：%s", msg)
    _last_error = msg


def _file_values() -> dict[str, Any]:
    p = config_path()
    if p is None:
        return {}
    now = time.monotonic()
    if _cache["values"] is not None and (now - _cache["at"]) <= CACHE_TTL:
        return _cache["values"]        # TTL 内：零读盘直接回缓存
    # ⚠️ 变更检测用**文件内容本身**做签名，不用 (mtime_ns, size)。
    # 实测（2026-10-08，本机 NTFS）：mtime 粒度约 **0.51 ms**，且同尺寸连续两次
    # 改写在 20 次里有 **10 次** mtime_ns 完全相同 ⇒ (mtime_ns, size) 会**漏掉**
    # 真改动。配置文件只有几百字节，直接比内容成本可忽略、语义绝对可靠。
    try:
        raw = p.read_text(encoding="utf-8-sig")
        sig: str | None = raw
    except OSError:
        sig = None                     # 没有配置文件是**正常态**，不是错误
    if sig != _cache["sig"]:
        if sig is None:
            _cache.update(sig=None, values={})
            _set_error(None)
        else:
            try:
                _cache.update(sig=sig, values=parse_toml(sig))
                _set_error(None)
            except Exception as e:
                # 解析失败 → 用**默认值**（不是上次的旧值：旧值来自已被改坏的配置，
                # 继续沿用会让人以为「改动生效了」）。错误留给 last_error() 对外报。
                _cache.update(sig=sig, values={})
                _set_error(str(e))
    _cache["at"] = now
    return _cache["values"]


def get(key: str) -> Any:
    """取一个阈值的当前生效值。未知键直接报错（防拼错键名后静默拿默认值）。"""
    if key not in _SPEC:
        raise KeyError(f"未知配置键：{key}（合法键：{', '.join(sorted(_SPEC))}）")

    env_name = ENV_PREFIX + key.upper()
    raw = os.environ.get(env_name)
    if raw is not None:
        try:
            value = _coerce(key, _parse_value(raw, f"环境变量 {env_name}"))
            _set_error(None)
            return value
        except ValueError as e:
            _set_error(str(e))          # 环境变量写错：记下来并回落到文件/默认
            logger.warning("忽略非法的 %s：%s", env_name, e)

    values = _file_values()
    for candidate in (f"thresholds.{key}", key):
        if candidate in values:
            try:
                return _coerce(key, values[candidate])
            except ValueError as e:
                _set_error(str(e))
                logger.warning("忽略非法的 %s：%s", candidate, e)
                break
    return DEFAULTS[key]


def all_values() -> dict[str, Any]:
    """全部阈值的当前生效值（供 memory_stats 展示）。"""
    return {k: get(k) for k in DEFAULTS}


__all__ = [
    "CONFIG_NAME", "ENV_PREFIX", "CACHE_TTL", "DEFAULTS",
    "parse_toml", "set_vault", "config_path", "last_error", "reload",
    "get", "all_values",
]
