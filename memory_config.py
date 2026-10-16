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

## 配置面出口（`state()` / `write_values()`，2026-10-16）

本模块同时是**配置面的唯一事实源**：`state()` 给出「当前生效值 + 每键来源 + 文案 +
类型 + 默认值 + 配置文件路径 + 解析错误」的完整快照，`write_values()` 负责**整状态**
写回 `memory.toml`（校验 → 写前自解析 → 备份 → 原子替换 → 失效缓存）。

MCP 工具 `memory_config` 与 `memory_stats` 都从这里取数，**任何调用方都不得自己
拼装阈值文案、自己解析 `memory.toml`、自己写盘**。这条纪律的直接动因是 Web 控制台
（独立仓 `ai-memory-web`）曾经的跨仓 `import`：控制台与 MCP 服务端各持一份「如何读
写 memory.toml」的知识，任何一侧改动都可能让两边对不上。现在控制台只经 MCP 工具
读写，**不再 import 本模块** ⇒ 两个仓之间只剩「连接」一种关系。

⚠️ 本模块仍是最底层（不 import 本仓任何其它模块），故 `write_values()` **不加写锁**：
`os.replace()` 本身原子，且阈值写入是低频人工操作；为它引入 `locks` 会破坏分层铁律。
备份文件写在配置文件**同目录**（vault 根），命名 `memory.toml.bak.<时间戳>` ——
该形状命中记忆库自带 `.gitignore` 的 `*.bak.*`，不会污染版本库。

## 分层

本模块**不 import 本仓任何其它模块**（最底层）；`locks` / `text_utils` / `yaml_io` 等
工具层可依赖它，反之绝不允许（由 `tests/test_import_layers.py` 机器守卫）。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from datetime import datetime
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

# 键 -> (中文名, 作用说明)。**文案只此一份**：Web 控制台 `/api/thresholds` 与
# memory_stats 都从这里取，前端不硬编码任何阈值文案 —— 将来新增阈值时若漏写文案，
# `tests/test_memory_config.py` 的一致性断言会直接拦下（而不是界面上留个空洞）。
# 口径要求：写「改这个数会发生什么」，不写「这个数是什么」——后者默认值 pill 已经表达。
META: dict[str, tuple[str, str]] = {
    "dup_title_jaccard": (
        "标题重复阈值",
        "写入时把新标题与库内已有标题做 bigram Jaccard 比对；≥ 此值 ⇒ 返回"
        "「疑似重复标题」提示（纯提示：不合并、不覆盖、不改数据）。",
    ),
    "dup_body_jaccard": (
        "正文重复阈值",
        "写入时把新正文与库内已有正文做 bigram Jaccard 比对；≥ 此值 ⇒ 返回"
        "「疑似重复或冲突内容」提示（纯提示，同样不动数据）。",
    ),
    "body_truncate_chars": (
        "正文截断预算",
        "memory_read / memory_context 未显式传 max_chars 时，单条正文最多返回的"
        "字符数（超出即截断）；memory_context 传 0 表示不限量。",
    ),
    "lock_timeout": (
        "陈旧锁回收",
        "写锁 .memory.lock 的存在时间超过此秒数即视为陈旧锁并强制回收，"
        "避免进程异常退出后写入被永久卡住。",
    ),
    "heat_cold_max": (
        "降冷阈值",
        "热度低于此值、且当前分层不是 cold 的条目 ⇒ 判为应降为 cold"
        "（memory_heat_suggest 只报告，apply 时才写盘）。",
    ),
    "heat_demote_hot_max": (
        "hot 降级阈值",
        "热度低于此值、且当前分层是 hot 的条目 ⇒ 判为应降为 warm。",
    ),
    "heat_hot_min": (
        "升热阈值",
        "热度高于此值、且当前分层不是 hot 的条目 ⇒ 判为应升为 hot。",
    ),
    "heat_decay_lambda": (
        "热度衰减系数",
        "热度 = 访问次数 ÷ (1 + λ × 距今天数)。λ 越大，历史访问衰减越快，"
        "分层越依赖最近是否被读过。",
    ),
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


def meta(key: str) -> dict[str, str]:
    """取一个阈值的中文名与作用说明（未知键报错，与 get() 同口径）。

    返回 ``{"label": 中文名, "desc": 作用}``。**刻意不抛文案缺失异常**：界面少一行
    说明是小问题，为此让整个配置面板打不开是大问题 ⇒ 缺文案时回落空串。
    """
    if key not in _SPEC:
        raise KeyError(f"未知配置键：{key}（合法键：{', '.join(sorted(_SPEC))}）")
    label, desc = META.get(key, ("", ""))
    return {"label": label, "desc": desc}


def missing_meta() -> list[str]:
    """有默认值却没有文案的键（供测试断言；正常恒为空列表）。"""
    return sorted(set(DEFAULTS) - set(META))


# ---------------------------------------------------------------- 配置面快照
def source_of(key: str) -> str:
    """一个阈值「当前生效值」的来源：``env`` / ``file`` / ``default``。

    与 `get()` 的优先级链条**逐级对应**（环境变量 > 文件 > 默认），因此它回答的是
    「我屏幕上看到的这个数，是谁给的」——配置页上「来源」那一列就靠它。
    """
    if key not in _SPEC:
        raise KeyError(f"未知配置键：{key}（合法键：{', '.join(sorted(_SPEC))}）")
    if os.environ.get(ENV_PREFIX + key.upper()) is not None:
        return "env"
    values = _file_values()
    for candidate in (f"thresholds.{key}", key):
        if candidate in values:
            return "file"
    return "default"


def state() -> dict[str, Any]:
    """配置面完整快照 —— MCP 工具 `memory_config` 的取数口。

    返回 ``{vault, config_path, file_exists, writable, ttl, last_error, keys}``；
    ``keys`` 为「键 -> {value, default, type, source, env, label, desc}」。

    与 `memory_stats` 的「配置」节**同源**（都经 `get()` / `config_path()`），差别只在
    呈现：那边是给人读的文本摘要，这里是给机器消费的完整字段。

    ``last_error`` 在**取完所有值之后**才读：`get()` 命中合法环境变量时会清一次错误
    记录，先读会把「文件解析失败」这类真实问题提前定格成过期信息。
    """
    path = config_path()
    keys: dict[str, dict[str, Any]] = {}
    for k, dflt in DEFAULTS.items():
        info = meta(k)
        keys[k] = {
            "value": get(k),
            "default": dflt,
            "type": "int" if _SPEC[k] is int else "float",
            "source": source_of(k),
            "env": ENV_PREFIX + k.upper(),
            "label": info["label"],
            "desc": info["desc"],
        }
    return {
        "vault": str(_vault_dir) if _vault_dir is not None else None,
        "config_path": str(path) if path is not None else None,
        "file_exists": bool(path is not None and path.is_file()),
        "writable": path is not None,
        "ttl": CACHE_TTL,
        "last_error": _last_error,
        "keys": keys,
    }


# ---------------------------------------------------------------- 整状态写入
def _toml_scalar(key: str, value: Any) -> str:
    """把一个阈值渲染成 TOML 标量文本；类型不符直接报错（不静默强转）。"""
    want = _SPEC[key]
    if isinstance(value, bool):
        raise ValueError(f"配置 {key}：不接受布尔值")
    if want is int:
        if not isinstance(value, int):
            raise ValueError(f"配置 {key}：期望整数，得到 {value!r}")
        return str(value)
    if not isinstance(value, (int, float)):
        raise ValueError(f"配置 {key}：期望数值，得到 {value!r}")
    return repr(float(value))


def render_toml(values: dict[str, Any]) -> str:
    """把**一整套**阈值渲染成 `memory.toml` 文本（``[thresholds]`` 区，LF 行尾）。

    **整状态语义**：键集必须与 `DEFAULTS` 完全相等 —— 多一个键（拼错）或**少一个键**
    都直接拒绝，而不是补默认值后照写。理由是「漏提交的那个键」会被静默写回默认值，
    使用者只会看到「我明明没改它，它自己变回去了」，与本模块「宁可拒绝、不要惊喜」
    的既定口径相反。
    """
    if not isinstance(values, dict):
        raise ValueError("values 必须是「键 -> 数值」的字典")
    unknown = sorted(set(values) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"未知配置键：{', '.join(unknown)}")
    missing = sorted(set(DEFAULTS) - set(values))
    if missing:
        raise ValueError(
            f"缺少配置键：{', '.join(missing)}（整状态写，必须提交全部 {len(DEFAULTS)} 个键）")

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# ai-memory 运行时阈值（memory.toml）",
        f"# 由 MCP 工具 memory_config 写入：{stamp}",
        f"# 取值优先级：环境变量 {ENV_PREFIX}<KEY> > [thresholds] 区 > 顶层同名键 > 内置默认",
        f"# 改动约 {CACHE_TTL:g} 秒内生效，无需重启服务。",
        "",
        "[thresholds]",
    ]
    for k in DEFAULTS:
        lines.append(f"{k} = {_toml_scalar(k, values[k])}")
    lines.append("")
    return "\n".join(lines)


def _prune_backups(path: Path, keep: int) -> None:
    """只保留最近 `keep` 份 `memory.toml.bak.*`，其余删除（失败只告警，不影响写入）。"""
    if keep <= 0:
        return
    old = sorted(path.parent.glob(f"{CONFIG_NAME}.bak.*"))
    for p in old[:-keep]:
        try:
            p.unlink()
        except OSError as e:
            logger.warning("旧配置备份清理失败：%s（%s）", p.name, e)


def write_values(values: dict[str, Any], *, keep: int = 10) -> dict[str, Any]:
    """整状态写入 vault 根的 `memory.toml`，返回写入后的 `state()` 快照（附 ``backup``）。

    顺序刻意如此：**校验 → 渲染 → 写前自解析 → 备份 → 原子替换 → 失效缓存**。
    「写前自解析」是渲染器的自检 —— 渲染出的文本若连自家解析器都读不过，就绝不该
    落盘；这一步把「渲染器有 bug」变成写入失败，而不是一个坏配置文件。

    **只写 `memory.toml` 这一个文件，绝不触碰任何 `*.md`。**
    """
    path = config_path()
    if path is None:
        raise ValueError("未注入 vault 位置 ⇒ 无法定位 memory.toml。请设置 AI_MEMORY_DIR。")
    if not path.parent.is_dir():
        raise ValueError(f"vault 目录不存在：{path.parent}")

    text = render_toml(values)                      # 内含键集与类型校验
    parse_toml(text)                                # 写前自解析（fail-fast）

    backup: Path | None = None
    if path.is_file():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(f"{CONFIG_NAME}.bak.{stamp}")
        shutil.copyfile(str(path), str(backup))
        _prune_backups(path, keep)

    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(str(tmp), str(path))
    reload()                                        # 立刻生效，不等 TTL

    out = state()
    out["backup"] = str(backup) if backup is not None else None
    return out


__all__ = [
    "CONFIG_NAME", "ENV_PREFIX", "CACHE_TTL", "DEFAULTS", "META",
    "parse_toml", "set_vault", "config_path", "last_error", "reload",
    "get", "all_values", "meta", "missing_meta",
    "source_of", "state", "render_toml", "write_values",
]
