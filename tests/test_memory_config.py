#!/usr/bin/env python3
"""运行时阈值外置 + 热重载回归测试 for ai-memory MCP server（B5, 2026-10-08）。

背景:
    8 个阈值原先硬编码在各模块里（`_DUP_TITLE_JACCARD` / `BODY_TRUNCATE_CHARS` /
    `DEFAULT_LOCK_TIMEOUT` / 三个 heat 阈值 / `HEAT_DECAY_LAMBDA`），既无法按 vault
    调参，也把两处**默认参数**钉死在 `def` 期（改配置永远不生效）。

    B5 新增 `memory_config`：vault 根 `memory.toml` + 环境变量 + 2 秒 TTL 热重载。

本脚本的断言口径（都可证伪）:
    T1  **无配置时行为零变化** —— 8 个默认值与 B5 之前的硬编码值**逐项相同**。
        这是本改动的安全底：若谁偷偷改了某个默认值，T1 当场变红。
    T2  **解析器只认它声明的子集** —— 数组 / 内联表 / 多行字符串 / 裸词一律报错，
        且错误信息**带行号**。用「静默取默认值」来兜配置写错，会让「我明明改了却
        不生效」变成无法排查的怪现象，故本条是本模块的核心设计断言。
    T3  **优先级链**：环境变量 > `[thresholds]` 区 > 顶层键 > 默认。
    T4  **类型严格**：int 与 float 分开（`8000.0` 不算合法的 `body_truncate_chars`），
        bool 不得冒充数值。
    T5  **热重载**：改文件后无需 reload，TTL 过期即取新值。
    T6  **配置写坏不打挂服务**：回落到**默认值**（不是上次的旧值），并记进
        `last_error()`。
    T7  **真的接线了** —— 光有模块没用。4 个读取点（锁超时 / heat 升降级 /
        正文截断 / 去重阈值）必须随配置变化，否则 B5 就是摆设。其中
        「正文截断」与「锁超时」正是那两个**默认参数**：字面量写死时 T7 必红。

运行:
    python tests/test_memory_config.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-test-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_METRICS"] = "0"
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import memory_config as mc  # noqa: E402
import locks  # noqa: E402
import text_utils  # noqa: E402
import server  # noqa: E402   (此时 server.MEMORY_DIR == _TMP)

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


ROOT = Path(_TMP)
CFG = ROOT / "memory.toml"


def put(text: str | None) -> None:
    """写 (或删除) vault 根的 memory.toml，并强制丢弃缓存。"""
    if text is None:
        CFG.unlink(missing_ok=True)
    else:
        CFG.write_text(text, encoding="utf-8")
    mc.reload()


def snapshot() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(ROOT))] = p.read_text(encoding="utf-8", errors="replace")
    return out


# =====================================================================
# T1 无配置时行为零变化
# =====================================================================
print("\n[T1] 默认值必须与 B5 之前的硬编码值逐项相同")
_PRE_B5 = {
    "dup_title_jaccard": 0.6,
    "dup_body_jaccard": 0.5,
    "body_truncate_chars": 8000,
    "lock_timeout": 15.0,
    "heat_cold_max": 0.1,
    "heat_demote_hot_max": 0.5,
    "heat_hot_min": 3.0,
    "heat_decay_lambda": 0.2,
}
put(None)
check("T1a DEFAULTS 与改动前硬编码逐一相等", mc.DEFAULTS == _PRE_B5,
      f"{ {k: (mc.DEFAULTS.get(k), v) for k, v in _PRE_B5.items() if mc.DEFAULTS.get(k) != v} }")
check("T1b 无配置文件时 all_values() 全为默认", mc.all_values() == _PRE_B5)
check("T1c 无配置文件不算错误", mc.last_error() is None, repr(mc.last_error()))
check("T1d config_path 指向 vault 根", mc.config_path() == CFG, repr(mc.config_path()))

print("\n[T1e] 向后兼容别名仍在（有测试直接 import 它们）")
check("T1e-1 server.BODY_TRUNCATE_CHARS", server.BODY_TRUNCATE_CHARS == 8000)
check("T1e-2 locks.DEFAULT_LOCK_TIMEOUT", locks.DEFAULT_LOCK_TIMEOUT == 15.0)
check("T1e-3 text_utils.HEAT_DECAY_LAMBDA", text_utils.HEAT_DECAY_LAMBDA == 0.2)

print("\n[T1f] 两处默认参数**不得**写死字面量（写死则热重载恒失效）")
check("T1f-1 acquire_lock(stale_timeout) 默认值为 None",
      inspect.signature(locks.acquire_lock).parameters["stale_timeout"].default is None,
      repr(inspect.signature(locks.acquire_lock)))
check("T1f-2 acquire_lock 仍接受位置参数调用（签名兼容）",
      list(inspect.signature(locks.acquire_lock).parameters) == ["lock_path", "stale_timeout"])

# =====================================================================
# T2 解析器子集边界
# =====================================================================
print("\n[T2] 不支持的结构必须报错，且带行号")
_BAD = [
    ("数组", "heat_hot_min = [1, 2]\n"),
    ("内联表", "heat_hot_min = {a = 1}\n"),
    ("未闭合的多行字符串", 'heat_hot_min = """abc\n'),
    ("单行三引号（TOML 里是多行串语义）", 'heat_hot_min = """abc"""\n'),
    ("裸词", "heat_hot_min = abc\n"),
    ("缺值", "heat_hot_min =\n"),
    ("非法键名", "3heat = 1\n"),
    ("小节未闭合", "[thresholds\nheat_hot_min = 1\n"),
    ("非 key=value 行", "heat_hot_min 1\n"),
]
for i, (label, text) in enumerate(_BAD, 1):
    try:
        mc.parse_toml(text)
        ok, detail = False, "未报错（被静默接受）"
    except ValueError as e:
        ok, detail = "第 1 行" in str(e), str(e)
    except Exception as e:                       # noqa: BLE001
        ok, detail = False, f"抛了非 ValueError: {type(e).__name__}: {e}"
    check(f"T2a-{i} 拒绝{label}", ok, detail)

print("\n[T2b] 非法位置须给**正确**行号（不是永远第 1 行）")
try:
    mc.parse_toml("# c1\n\nheat_hot_min = 1.0\nheat_cold_max = [9]\n")
    check("T2b-1 第 4 行报错", False, "未报错")
except ValueError as e:
    check("T2b-1 第 4 行报错", "第 4 行" in str(e), str(e))
try:
    mc.parse_toml("heat_hot_min = 1.0\nheat_hot_min = 2.0\n")
    check("T2b-2 重复键报错", False, "未报错")
except ValueError as e:
    check("T2b-2 重复键报错", "第 2 行" in str(e) and "重复" in str(e), str(e))

print("\n[T2c] 合法子集必须解析正确（别把该收的也拒了）")
parsed = mc.parse_toml(
    "# 注释\n"
    "a = 42        # 行内注释\n"
    "b = -0.5\n"
    "c = .75\n"
    "d = 1e-3\n"
    "e = true\n"
    "f = false\n"
    "g = \"带#号的字符串\"\n"
    "h = 'x'\n"
    "\n"
    "[thresholds]\n"
    "a = 7\n"
)
check("T2c-1 整数", parsed.get("a") == 42 and isinstance(parsed.get("a"), int))
check("T2c-2 负浮点", parsed.get("b") == -0.5)
check("T2c-3 省略整数部分的浮点", parsed.get("c") == 0.75)
check("T2c-4 科学计数法", parsed.get("d") == 1e-3)
check("T2c-5 布尔", parsed.get("e") is True and parsed.get("f") is False)
check("T2c-6 引号内的 # 不被当注释", parsed.get("g") == "带#号的字符串", repr(parsed.get("g")))
check("T2c-7 单引号字符串", parsed.get("h") == "x")
check("T2c-8 小节键写作 section.key", parsed.get("thresholds.a") == 7)

# =====================================================================
# T3 取值优先级
# =====================================================================
print("\n[T3] 优先级：环境变量 > [thresholds] > 顶层 > 默认")
put("heat_cold_max = 0.9\nheat_hot_min = 8.0\n[thresholds]\nheat_hot_min = 0.01\n")
check("T3a 顶层键覆写生效", mc.get("heat_cold_max") == 0.9, repr(mc.get("heat_cold_max")))
check("T3b [thresholds] 优先于顶层同名键", mc.get("heat_hot_min") == 0.01,
      repr(mc.get("heat_hot_min")))
check("T3c 未提及的键仍回落默认", mc.get("dup_body_jaccard") == 0.5)

os.environ["AI_MEMORY_CFG_HEAT_COLD_MAX"] = "0.123"
mc.reload()
check("T3d 环境变量优先于一切", mc.get("heat_cold_max") == 0.123, repr(mc.get("heat_cold_max")))
os.environ["AI_MEMORY_CFG_HEAT_COLD_MAX"] = "not-a-number"
mc.reload()
check("T3e 非法环境变量不生效、回落文件值（此处为顶层 0.9）",
      mc.get("heat_cold_max") == 0.9, repr(mc.get("heat_cold_max")))
check("T3f 非法环境变量被记进 last_error", (mc.last_error() or "") != "", repr(mc.last_error()))
del os.environ["AI_MEMORY_CFG_HEAT_COLD_MAX"]
mc.reload()

# =====================================================================
# T4 类型严格
# =====================================================================
print("\n[T4] 类型必须严格（int 与 float 不通用；bool 不得冒充数值）")
put("body_truncate_chars = 8000.0\n")
check("T4a 整型键收到浮点 → 不采纳", mc.get("body_truncate_chars") == 8000
      and isinstance(mc.get("body_truncate_chars"), int), repr(mc.get("body_truncate_chars")))
check("T4b 并报出类型错误", "期望整数" in (mc.last_error() or ""), repr(mc.last_error()))

put("body_truncate_chars = true\n")
check("T4c 整型键收到布尔 → 不采纳", mc.get("body_truncate_chars") == 8000)
check("T4d 布尔错误信息明确", "布尔" in (mc.last_error() or ""), repr(mc.last_error()))

put("heat_hot_min = 4\n")
check("T4e 浮点键收到整数 → 归一化为 float", mc.get("heat_hot_min") == 4.0
      and isinstance(mc.get("heat_hot_min"), float), repr(mc.get("heat_hot_min")))

put("heat_hot_min = \"4\"\n")
check("T4f 数值键收到字符串 → 不采纳", mc.get("heat_hot_min") == 3.0)

print("\n[T4g] 未知键必须报错（防拼错键名后静默拿默认值）")
try:
    mc.get("heat_hot_mim")
    check("T4g-1 未知键 KeyError", False, "未报错")
except KeyError as e:
    msg = str(e)
    check("T4g-1 未知键 KeyError", "未知配置键" in msg, msg)
    check("T4g-2 报错信息列出合法键名", "heat_hot_min" in msg and "body_truncate_chars" in msg, msg)
except Exception as e:                                   # noqa: BLE001
    check("T4g-1 未知键 KeyError", False, f"抛了 {type(e).__name__}: {e}")

# =====================================================================
# T5 热重载
# =====================================================================
print("\n[T5] 改文件后无需 reload，TTL 过期即生效")
put("dup_body_jaccard = 0.11\n")
check("T5a 写入后 reload 立即生效", mc.get("dup_body_jaccard") == 0.11)
check("T5b TTL 内回缓存（不重读文件）", mc.get("dup_body_jaccard") == 0.11)

# 让缓存**必定**过期：用 -1 而不是 0 —— `time.monotonic()` 在本机分辨率虽然约 100ns，
# 但「恰好等于 0」这条边界不值得赌，-1 让判定与时钟无关。
_orig_ttl = mc.CACHE_TTL
mc.CACHE_TTL = -1.0
try:
    # 这里刻意写**不等长**的值：本节只验「TTL 过期 → 重读」，不该依赖变更检测机制。
    CFG.write_text("dup_body_jaccard = 0.987654321\n", encoding="utf-8")
    check("T5c TTL 过期后**不调 reload** 也拿到新值", mc.get("dup_body_jaccard") == 0.987654321,
          repr(mc.get("dup_body_jaccard")))
    CFG.unlink()
    check("T5d 配置文件被删 → 回落默认（不是沿用旧值）", mc.get("dup_body_jaccard") == 0.5,
          repr(mc.get("dup_body_jaccard")))
finally:
    mc.CACHE_TTL = _orig_ttl
put(None)

print("\n[T5e] TTL 内不重读文件（缓存真的挡住了）")
put("dup_body_jaccard = 0.22\n")
_ = mc.get("dup_body_jaccard")          # 预热
CFG.write_text("dup_body_jaccard = 0.33\n", encoding="utf-8")
check("T5e-1 TTL 内仍返回旧值（证明缓存生效）", mc.get("dup_body_jaccard") == 0.22,
      repr(mc.get("dup_body_jaccard")))
mc.reload()
check("T5e-2 reload 后取到新值", mc.get("dup_body_jaccard") == 0.33)

print("\n[T5f] **同尺寸 + 同 mtime** 改写必须能被察觉（回归护栏）")
# 背景：实测本机 NTFS 的 mtime 粒度约 0.51 ms，同尺寸连续两次改写在 20 次里有 10 次
# mtime_ns 完全相同。若变更检测用 (mtime_ns, size)，这种改写会被**漏掉**。
# ⚠️ 但「恰好撞上」只有约 50% 概率 —— 靠概率的判据不算判据，故这里用 os.utime
# **人为把 mtime 钉回原值**，把「同签名的两次改写」变成**确定**场景：只有按内容
# 判变更的实现才能察觉，按 (mtime_ns, size) 的实现必然漏判。
put("dup_body_jaccard = 0.11\n")       # 与下一版**等长**（都是 22 字节）
_ = mc.get("dup_body_jaccard")
_st_before = CFG.stat()
mc.CACHE_TTL = -1.0
try:
    CFG.write_text("dup_body_jaccard = 0.99\n", encoding="utf-8")
    os.utime(CFG, ns=(_st_before.st_atime_ns, _st_before.st_mtime_ns))
    assert CFG.stat().st_size == _st_before.st_size, "前置：两版必须等长"
    assert CFG.stat().st_mtime_ns == _st_before.st_mtime_ns, "前置：mtime 必须被钉住"
    check("T5f-1 等长且 mtime 未变，仍被察觉（内容签名，不是 mtime+size）",
          mc.get("dup_body_jaccard") == 0.99, repr(mc.get("dup_body_jaccard")))
finally:
    mc.CACHE_TTL = _orig_ttl
put(None)

# =====================================================================
# T6 配置写坏不打挂服务
# =====================================================================
print("\n[T6] 配置写坏 → 回落默认 + last_error 可见，绝不抛异常")
# ⚠️ 这一段**刻意不走 put()**（put 会 reload 清缓存）：要让「回落默认」与「沿用旧值」
# 两种实现真正分得开，必须先让缓存里**存着一个合法旧值**，再把文件写坏。
put("heat_hot_min = 7.5\n")
check("T6a-0 基线：正常读到 7.5", mc.get("heat_hot_min") == 7.5,
      repr(mc.get("heat_hot_min")))
_ttl6 = mc.CACHE_TTL
mc.CACHE_TTL = -1.0
try:
    CFG.write_text("heat_hot_min = [1, 2]\n", encoding="utf-8")   # 写坏，且不清缓存
    _v6 = mc.get("heat_hot_min")
    check("T6a 解析失败不抛异常，回落**默认值**（不是上次的旧值 7.5）", _v6 == 3.0,
          f"got={_v6!r}（若得 7.5 说明沿用了旧值）")
    _e6a = mc.last_error()
    check("T6b last_error 记下并含行号与文件名",
          "第 1 行" in (_e6a or "") and "memory.toml" in (_e6a or ""), repr(_e6a))
    check("T6c 其它键仍可用（一个键写坏不牵连全局）", mc.get("dup_title_jaccard") == 0.6)

    CFG.write_text("heat_hot_min = 2.5\n", encoding="utf-8")
    _v6b = mc.get("heat_hot_min")            # 先取一次：错误是在**成功加载后**被清掉的
    _e6b = mc.last_error()
    check("T6d 修好后错误自动清除", _v6b == 2.5 and _e6b is None,
          f"val={_v6b!r} last_error={_e6b!r}")
finally:
    mc.CACHE_TTL = _ttl6
put(None)

# =====================================================================
# T7 真的接线了 —— 4 个读取点随配置变化
# =====================================================================
print("\n[T7] 读取点必须真的走 memory_config（否则 B5 是摆设）")

print("[T7a] 锁陈旧回收超时")
put("lock_timeout = 4.0\n")
cfg_lock = ROOT / ".memory.lock"
cfg_lock.write_text(json.dumps({"pid": 999999, "time": time.time() - 10.0}), encoding="utf-8")
t0 = time.perf_counter()
got = locks.acquire_lock(cfg_lock)         # 10s > 4s ⇒ 视为陈旧，立即回收
elapsed = time.perf_counter() - t0
check("T7a-1 10 秒前的锁在 lock_timeout=4 下被立即回收", got is True and elapsed < 2.0,
      f"got={got} elapsed={elapsed:.2f}s（写死默认 15 时这里会卡 15 秒后返回 False）")
locks.release_lock(cfg_lock)
put(None)

print("[T7b] 热度升降级阈值")
check("T7b-1 默认下 5.0 分升为 hot", server._heat_tier_decision("某条", "warm", 5.0) == "hot")
put("heat_hot_min = 9.5\n")
check("T7b-2 heat_hot_min=9.5 后 5.0 分不再升 hot（且不降级）",
      server._heat_tier_decision("某条", "warm", 5.0) is None,
      repr(server._heat_tier_decision("某条", "warm", 5.0)))
check("T7b-3 9.6 分仍升 hot", server._heat_tier_decision("某条", "warm", 9.6) == "hot")
put("heat_cold_max = 8.0\n")
check("T7b-4 heat_cold_max=8.0 后 5.0 分降为 cold",
      server._heat_tier_decision("某条", "warm", 5.0) == "cold")
put(None)
check("T7b-5 默认下 0.5 分不会让 hot 降级",
      server._heat_tier_decision("某条", "hot", 0.5) is None,
      repr(server._heat_tier_decision("某条", "hot", 0.5)))
put("heat_demote_hot_max = 0.9\n")
check("T7b-6 heat_demote_hot_max=0.9 后 0.5 分的 hot 降为 warm",
      server._heat_tier_decision("某条", "hot", 0.5) == "warm",
      repr(server._heat_tier_decision("某条", "hot", 0.5)))
check("T7b-7 核心页始终豁免（不受阈值影响）",
      server._heat_tier_decision("记忆索引", "hot", 0.01) is None)
put(None)

print("[T7c] 去重提示阈值")
_ent = [(ROOT / "已存在.md",
         {"title": "记忆库索引优化记录", "tags": []},
         "这是一段关于记忆库索引优化的说明文字，用来和下面的输入比相似度。" * 3)]
put("dup_title_jaccard = 0.99\ndup_body_jaccard = 0.99\n")
hint_hi = server._find_duplicate_hints("记忆库索引优化记录2",
                                       "这是一段关于记忆库索引优化的说明文字。", _ent)
put("dup_title_jaccard = 0.1\ndup_body_jaccard = 0.1\n")
hint_lo = server._find_duplicate_hints("记忆库索引优化记录2",
                                       "这是一段关于记忆库索引优化的说明文字。", _ent)
check("T7c-1 阈值 0.99 时不出提示", len(hint_hi) == 0, repr(hint_hi))
check("T7c-2 阈值 0.1 时出提示", len(hint_lo) == 1, repr(hint_lo))
check("T7c-3 提示里带出目标标题", "记忆库索引优化记录" in (hint_lo[0] if hint_lo else ""))
put(None)

print("[T7d] 正文截断预算（默认参数写死时本条必红）")
# 正文刻意造到 **10000 字符 > 内置默认 8000** —— 只有这样「配置 0 = 不限」与
# 「写死 8000」才分得开；正文太短会让两条实现给出同样的结果（判据恒绿）。
_BIG_BODY = "甲乙" * 5000
server.memory_write(title="B5截断用条目", content=_BIG_BODY, tags=["测试"])


def _read_default() -> str:
    return server.memory_read("B5截断用条目")          # 不传 max_chars ⇒ 走配置


put("body_truncate_chars = 100\n")
out_small = _read_default()
put("body_truncate_chars = 0\n")                       # 0 = 不限
out_full = _read_default()
put(None)
out_default = _read_default()
check("T7d-1 配置 100 ⇒ 截到 100", "已截断至前 100" in out_small, out_small[-160:])
check("T7d-2 配置 0 ⇒ 完全不截断（正文 {0} 字符）".format(len(_BIG_BODY)),
      "已截断" not in out_full, out_full[-160:])
check("T7d-3 无配置 ⇒ 内置默认 8000（可证伪：写死别的值即红）",
      "已截断至前 8000" in out_default, out_default[-160:])

print("[T7e] memory_context 的整段预算同样走配置")
server.memory_write(title="B5上下文用条目", content="丙丁" * 500, tags=["测试"])
put("body_truncate_chars = 120\n")
ctx_small = server.memory_context("B5上下文用条目", depth=0)
put(None)
check("T7e-1 memory_context 受配置约束", len(ctx_small) <= 120 + 300,
      f"len={len(ctx_small)}")

print("[T7f] 热度衰减系数 λ")
_OLD = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
put("heat_decay_lambda = 1.0\n")
_h_lam1 = text_utils.heat_score(10, _OLD)          # 10 / (1 + 1.0*9) = 1.0
put(None)
_h_lam_def = text_utils.heat_score(10, _OLD)       # 10 / (1 + 0.2*9) ≈ 3.57
check("T7f-1 λ 取自配置（λ=1.0, 9 天 ⇒ ≈1.0）", abs(_h_lam1 - 1.0) < 0.05,
      f"got={_h_lam1:.3f}")
check("T7f-2 无配置时用内置 λ=0.2（≈3.57）", abs(_h_lam_def - 10 / 2.8) < 0.05,
      f"got={_h_lam_def:.3f}")
check("T7f-3 两个 λ 下结果确实不同（说明配置真的生效）", _h_lam1 < _h_lam_def - 1.0)

# =====================================================================
# T8 切换 vault / 只读
# =====================================================================
print("\n[T8] set_vault 换目录须清缓存；读取不得写盘")
_other = Path(tempfile.mkdtemp(prefix="ai-memory-test-vaultB-"))
try:
    put("dup_body_jaccard = 0.44\n")
    check("T8a 当前 vault 读到 0.44", mc.get("dup_body_jaccard") == 0.44)
    (_other / "memory.toml").write_text("dup_body_jaccard = 0.77\n", encoding="utf-8")
    mc.set_vault(_other)
    check("T8b 切到 vault B 后取 B 的值（未串味）", mc.get("dup_body_jaccard") == 0.77,
          repr(mc.get("dup_body_jaccard")))
    mc.set_vault(ROOT)
    check("T8c 切回 vault A 取回 A 的值", mc.get("dup_body_jaccard") == 0.44,
          repr(mc.get("dup_body_jaccard")))
    mc.set_vault(ROOT)     # 同路径重复设置不应清缓存（无副作用）
    check("T8d 同路径重复 set_vault 无害", mc.get("dup_body_jaccard") == 0.44)
finally:
    mc.set_vault(ROOT)
    shutil.rmtree(_other, ignore_errors=True)

put("heat_hot_min = 1.25\nbody_truncate_chars = 4096\n")
before = snapshot()
_ = mc.all_values()
_ = mc.get("heat_hot_min")
_ = mc.last_error()
_ = mc.config_path()
after = snapshot()
check("T8e 取配置不新增/删除文件", set(before) == set(after),
      f"{sorted(set(before) ^ set(after))}")
check("T8f 取配置不修改任何文件内容", before == after,
      f"{[k for k in before if before[k] != after.get(k)]}")
check("T8g 取到的值与文件一致", mc.get("heat_hot_min") == 1.25
      and mc.get("body_truncate_chars") == 4096)

put(None)

# =====================================================================
# T9 阈值文案（中文名 + 作用）—— 单一来源，缺一个即红
# =====================================================================
print("\n[T9] 每个阈值都必须带中文名与作用说明（Web 控制台直接渲染它，缺了就成一片空白）")

check("T9a 8 个键**逐个**都有文案（missing_meta 为空）", mc.missing_meta() == [],
      repr(mc.missing_meta()))
check("T9b 文案表键集与默认值键集**完全相等**（多一个少一个都算漂移）",
      set(mc.META) == set(mc.DEFAULTS),
      f"多={sorted(set(mc.META) - set(mc.DEFAULTS))} "
      f"少={sorted(set(mc.DEFAULTS) - set(mc.META))}")

_bad_label = [k for k in mc.DEFAULTS if len(mc.meta(k)["label"].strip()) < 2]
_bad_desc = [k for k in mc.DEFAULTS if len(mc.meta(k)["desc"].strip()) < 20]
check("T9c 中文名非空且不像占位符（≥2 字）", not _bad_label, repr(_bad_label))
check("T9d 作用说明**够具体**（≥20 字；写「见文档」这类空话必红）",
      not _bad_desc, repr(_bad_desc))

# 反证：meta() 必须返回**副本**。返回 META 内部对象的话，任何调用方（含前端请求
# 处理路径）就地改写一下，全站文案就跟着错 —— 而且是那种"重启才好"的错。
_probe = mc.meta("heat_hot_min")
_probe["label"] = "被改坏了"
_probe["desc"] = "被改坏了"
check("T9e meta() 返回副本，调用方改写不污染全局 META",
      mc.meta("heat_hot_min")["label"] != "被改坏了"
      and mc.meta("heat_hot_min")["desc"] != "被改坏了",
      repr(mc.meta("heat_hot_min")))

try:
    mc.meta("no_such_key")
    _keyerr = False
except KeyError:
    _keyerr = True
check("T9f 未知键报错（与 get() 同口径，防拼错键名后静默拿空文案）", _keyerr)

# =====================================================================
# T10 配置面出口：state() / render_toml() / write_values()（2026-10-16）
#
# 动因：Web 控制台此前**自己 import memory_config、自己拼 memory.toml、自己写盘**
# ⇒ 「如何读写阈值」有两份实现，任一侧改动都可能让两边对不上。现在控制台只经 MCP
# 工具 memory_config 读写，读侧落到 state()、写侧落到 render_toml()/write_values()，
# 所以这两条路径的正确性就是「控制台能不能用」的同义词。
# =====================================================================
print("\n[T10] 配置面出口：快照 / 渲染 / 整状态写入")

from pathlib import Path  # noqa: E402

_ws = Path(tempfile.mkdtemp(prefix="cfg-write-"))
_prev_vault = ROOT
try:
    mc.set_vault(_ws)
    cfg_file = _ws / "memory.toml"

    # ---- state() 形状
    _st = mc.state()
    check("T10a state 顶层字段齐备",
          set(_st) == {"vault", "config_path", "file_exists", "writable",
                       "ttl", "last_error", "keys"}, sorted(_st))
    check("T10b state 键集 == DEFAULTS（不多不少）", set(_st["keys"]) == set(mc.DEFAULTS),
          sorted(set(_st["keys"]) ^ set(mc.DEFAULTS)))
    check("T10c 每键都带 value/default/type/source/env/label/desc",
          all(set(v) == {"value", "default", "type", "source", "env", "label", "desc"}
              for v in _st["keys"].values()),
          repr(next(iter(_st["keys"].values()))))
    check("T10d 无配置文件时 file_exists=False 且能写",
          _st["file_exists"] is False and _st["writable"] is True)
    check("T10e int 与 float 分得开",
          _st["keys"]["body_truncate_chars"]["type"] == "int"
          and _st["keys"]["lock_timeout"]["type"] == "float")
    check("T10f 默认来源标 default", _st["keys"]["lock_timeout"]["source"] == "default")

    # ---- source_of 三级
    mc.write_values(dict(mc.DEFAULTS))
    check("T10g 文件写入后来源变 file", mc.source_of("lock_timeout") == "file")
    os.environ["AI_MEMORY_CFG_LOCK_TIMEOUT"] = "7.5"
    check("T10h 环境变量优先级最高（来源 env）", mc.source_of("lock_timeout") == "env")
    check("T10i env 值真的生效", mc.get("lock_timeout") == 7.5)
    os.environ.pop("AI_MEMORY_CFG_LOCK_TIMEOUT")
    check("T10j 摘掉 env 后回到 file", mc.source_of("lock_timeout") == "file")
    try:
        mc.source_of("no_such_key")
        _kerr = False
    except KeyError:
        _kerr = True
    check("T10k source_of 未知键报错", _kerr)

    # ---- render_toml 守卫
    try:
        mc.render_toml({k: v for k, v in list(mc.DEFAULTS.items())[:-1]})
        _miss = False
    except ValueError:
        _miss = True
    check("T10l 缺键拒绝（整状态写：不补默认值）", _miss)
    try:
        mc.render_toml({**mc.DEFAULTS, "nope": 1})
        _unk = False
    except ValueError:
        _unk = True
    check("T10m 未知键拒绝", _unk)
    try:
        mc.render_toml({**mc.DEFAULTS, "body_truncate_chars": 8000.5})
        _typ = False
    except ValueError:
        _typ = True
    check("T10n 类型不符拒绝（int 键不接受 8000.5）", _typ)
    _txt = mc.render_toml(dict(mc.DEFAULTS))
    check("T10o 渲染结果 LF 行尾且含 [thresholds]", "[thresholds]" in _txt and "\r" not in _txt)
    check("T10p 渲染结果可被自家解析器读回",
          mc.parse_toml(_txt)["thresholds.lock_timeout"] == mc.DEFAULTS["lock_timeout"])

    # ---- write_values 行为
    _before = cfg_file.read_text(encoding="utf-8")
    _r1 = mc.write_values({**mc.DEFAULTS, "heat_hot_min": 9.0})
    check("T10q 写入后立即生效（不等 TTL）", mc.get("heat_hot_min") == 9.0)
    check("T10r 首次覆盖前有原文件 ⇒ 产生备份", _r1["backup"] is not None)
    check("T10s 备份名命中 vault 的 .gitignore（*.bak.*）",
          Path(_r1["backup"]).match("*.bak.*"), Path(_r1["backup"]).name)
    check("T10t 返回的是写入**后**的快照", _r1["keys"]["heat_hot_min"]["value"] == 9.0
          and _r1["file_exists"] is True)
    check("T10u 不留 .tmp 残骸", not list(_ws.glob("memory.toml.tmp*")))

    # 被拒绝的写入**不得**碰盘 —— 这是「宁可拒绝、不要惊喜」的可证伪形式
    _snap = cfg_file.read_text(encoding="utf-8")
    try:
        mc.write_values({k: v for k, v in list(mc.DEFAULTS.items())[:-1]})
        _rej = False
    except ValueError:
        _rej = True
    check("T10v 非法写入被拒且文件**逐字节未动**",
          _rej and cfg_file.read_text(encoding="utf-8") == _snap)

    # 备份滚动保留
    for _i in range(4):
        mc.write_values({**mc.DEFAULTS, "heat_hot_min": 1.0 + _i}, keep=2)
    check("T10w keep 参数限制备份份数", len(list(_ws.glob("memory.toml.bak.*"))) <= 2,
          repr(sorted(p.name for p in _ws.glob("memory.toml.bak.*"))))
    check("T10x 不产生除 memory.toml / 备份之外的任何文件",
          {p.name.split(".bak.")[0] for p in _ws.iterdir()} <= {"memory.toml"},
          repr(sorted(p.name for p in _ws.iterdir())))

    # 记忆库正文绝不被触碰
    check("T10y 写入路径绝不创建/改动 *.md", not list(_ws.glob("*.md")))

    # 未注入 vault ⇒ 明确报错，而不是写到一个猜出来的位置
    mc.set_vault(None)
    try:
        mc.write_values(dict(mc.DEFAULTS))
        _novault = False
    except ValueError as e:
        _novault = "无法定位 memory.toml" in str(e)
    check("T10z 未注入 vault 时写入明确报错", _novault)
finally:
    mc.set_vault(_prev_vault)
    shutil.rmtree(_ws, ignore_errors=True)

# =====================================================================
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
