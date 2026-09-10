#!/usr/bin/env python3
"""运行时指标（可观测性）测试。

用途：
    验证 metrics.py 这一层「观察服务健康状况」的设施本身可靠且**永不拖累主流程**。
    指标是辅助设施，它的第一条铁律不是「记得准」，而是「绝不能让记忆读写失败」——
    因此本脚本除了验算术，还专门验容错（路径不可写 / 无路径 / 高并发下不抛异常）。

    分两部分：
      1. 单元：Metrics 计数、合并落盘、snapshot、失败回滚、path_for 隔离、自动 flush
      2. 集成：server.py 各工具确实打点、memory_stats 确实展示、AI_MEMORY_METRICS=0 可关闭

    脚本把 AI_MEMORY_DIR 指向临时目录，绝不触碰真实记忆库。

运行:
    python tests/test_metrics.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-metrics-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ.pop("AI_MEMORY_METRICS", None)  # 确保默认开启
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import metrics  # noqa: E402
import server  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def section(name: str) -> None:
    print(f"\n== {name} ==")


_sandbox = Path(tempfile.mkdtemp(prefix="ai-memory-metrics-sandbox-"))

# =====================================================================
section("M1 · 计数与落盘")
_m1_path = _sandbox / "m1.json"
_m1 = metrics.Metrics(_m1_path)
_m1.inc("a", 3)
_m1.inc("b")
_m1.flush()
check("M1a flush 后文件存在", _m1_path.exists())
_d1 = json.loads(_m1_path.read_text(encoding="utf-8"))
check("M1b 计数正确落盘", _d1.get("a") == 3 and _d1.get("b") == 1, f"{_d1}")

# =====================================================================
section("M2 · 跨实例 read-merge-write 累加")
_m2 = metrics.Metrics(_m1_path)
_m2.inc("a", 2)
_m2.flush()
_d2 = json.loads(_m1_path.read_text(encoding="utf-8"))
check("M2a 同键累加而非覆盖", _d2.get("a") == 5, f"a={_d2.get('a')}")
check("M2b 他键保留", _d2.get("b") == 1)

# =====================================================================
section("M3 · 耗时累积与均值")
_m3_path = _sandbox / "m3.json"
_m3 = metrics.Metrics(_m3_path)
_m3.inc("search_calls", 2)
_m3.observe_ms("search_ms", 30.0)
_m3.observe_ms("search_ms", 10.0)
_m3.flush()
_d3 = json.loads(_m3_path.read_text(encoding="utf-8"))
check("M3a 耗时累加正确", _d3.get("search_ms") == 40.0, f"{_d3.get('search_ms')}")
check("M3b 可算出平均耗时", _d3["search_ms"] / _d3["search_calls"] == 20.0)

# =====================================================================
section("M4 · snapshot 合并盘上与内存、且不改动磁盘")
_m4_path = _sandbox / "m4.json"
_m4 = metrics.Metrics(_m4_path)
_m4.inc("x", 1)
_m4.flush()
_m4.inc("x", 4)  # 留在内存，未落盘
_snap4 = _m4.snapshot()
_d4 = json.loads(_m4_path.read_text(encoding="utf-8"))
check("M4a snapshot 含内存增量", _snap4.get("x") == 5, f"{_snap4}")
check("M4b snapshot 不改动磁盘", _d4.get("x") == 1, f"{_d4}")
check("M4c path=None 时 snapshot 安全", metrics.Metrics(None).snapshot() == {})

# =====================================================================
section("M5 · 落盘失败静默且不丢增量")
_bad = _sandbox / "not_a_file_dir"
_bad.mkdir(exist_ok=True)
_m5 = metrics.Metrics(_bad)  # 路径是个目录 → 写盘必然失败
try:
    _m5.inc("y", 7)
    _m5.flush()
    _ok5 = True
except Exception as e:  # pragma: no cover - 失败即测试失败
    _ok5 = False
    print(f"     unexpected: {e!r}")
check("M5a 落盘失败不抛异常", _ok5)
check("M5b 失败后增量仍在内存（下次可重试）", _m5.snapshot().get("y") == 7)
_orphans = list(_sandbox.glob(f"{_bad.name}.*.tmp"))
check("M5c 落盘失败不残留 .tmp 孤儿文件", not _orphans,
      f"残留: {[p.name for p in _orphans]}")

# =====================================================================
section("M6 · path_for 按记忆库隔离")
_pa = metrics.path_for(_sandbox, _sandbox / "vault-a")
_pb = metrics.path_for(_sandbox, _sandbox / "vault-b")
_pa2 = metrics.path_for(_sandbox, _sandbox / "vault-a")
check("M6a 不同库 → 不同文件", _pa != _pb, f"{_pa.name} vs {_pb.name}")
check("M6b 同库 → 稳定同名", _pa == _pa2)
check("M6c 文件名带 metrics 前缀且为 json", _pa.name.startswith("memory_metrics_")
      and _pa.suffix == ".json")

# =====================================================================
section("M7 · 达到 FLUSH_EVERY 自动落盘")
_m7_path = _sandbox / "m7.json"
_orig_every = metrics.FLUSH_EVERY
metrics.FLUSH_EVERY = 3
try:
    _m7 = metrics.Metrics(_m7_path)
    for _ in range(3):
        _m7.inc("z")
    check("M7a 累积到阈值自动落盘（无需显式 flush）", _m7_path.exists())
finally:
    metrics.FLUSH_EVERY = _orig_every

# =====================================================================
section("M8 · server 各工具确实打点")


def _snap() -> dict:
    return server._METRICS.snapshot()


def _delta(before: dict, key: str) -> float:
    return _snap().get(key, 0.0) - before.get(key, 0.0)


check("M8a 指标默认启用", server._METRICS_ENABLED is True)
check("M8b 指标文件路径已绑定", server._METRICS.path is not None)

_b = _snap()
server.memory_write("指标_新建", "正文 METRICTOKEN。", tags=["metrics"])
check("M8c memory_write 新建计数 +1", _delta(_b, "write_creates") == 1)
check("M8d memory_write 总调用 +1", _delta(_b, "write_calls") == 1)

_b = _snap()
server.memory_write("指标_新建", "正文第二版 METRICTOKEN。", tags=["metrics"])
check("M8e 同标题重写计入 write_updates", _delta(_b, "write_updates") == 1)
check("M8f 重写不再计入 write_creates", _delta(_b, "write_creates") == 0)

_b = _snap()
server.memory_read("指标_新建", max_chars=0)
check("M8g memory_read 命中计数 +1", _delta(_b, "read_calls") == 1)
check("M8h memory_read 命中不计 miss", _delta(_b, "read_misses") == 0)

_b = _snap()
server.memory_read("指标_根本不存在的标题", max_chars=0)
check("M8i memory_read 未命中计数 +1", _delta(_b, "read_misses") == 1)

_b = _snap()
server.memory_search("METRICTOKEN")
check("M8j memory_search 调用计数 +1", _delta(_b, "search_calls") == 1)
check("M8k memory_search 有命中不计零结果", _delta(_b, "search_zero_results") == 0)
check("M8l memory_search 记录命中数", _delta(_b, "search_results_total") >= 1)
check("M8m memory_search 记录耗时", _delta(_b, "search_ms") > 0)

_b = _snap()
server.memory_search("ZZZ_绝无此关键词_ZZZ")
check("M8n memory_search 零结果计数 +1", _delta(_b, "search_zero_results") == 1)

_b = _snap()
server.memory_list(limit=100)
check("M8o memory_list 调用计数 +1", _delta(_b, "list_calls") == 1)

_b = _snap()
server.memory_smart_search("METRICTOKEN")
check("M8p memory_smart_search 调用计数 +1", _delta(_b, "smart_search_calls") == 1)

_b = _snap()
server.memory_smart_search("ZZZ_绝无此关键词_ZZZ")
check("M8q smart_search 零结果计数 +1", _delta(_b, "smart_search_zero_results") == 1)

_b = _snap()
server.memory_archive("指标_新建") if False else None
_lk = _snap().get("lock_calls", 0.0)
check("M8r 写工具触发了真实抢锁打点", _lk >= 1, f"lock_calls={_lk}")

# =====================================================================
section("M9 · memory_stats / memory_audit 展示运行时指标")
_stats = server.memory_stats()
check("M9a memory_stats 含运行时指标章节", "## 运行时指标" in _stats)
check("M9b memory_stats 展示 memory_search 一行", "memory_search: 调用" in _stats)
check("M9c memory_stats 展示锁竞争一行", "互斥锁" in _stats)
check("M9d memory_stats 零结果率以百分比呈现", "%" in _stats)
_audit = server.memory_audit()
check("M9e memory_audit 同样含运行时指标章节", "## 运行时指标" in _audit)

# =====================================================================
section("M10 · AI_MEMORY_METRICS=0 可关闭（子进程验证）")
_probe = (
    "import os, sys, tempfile;"
    "sys.path.insert(0, r'" + str(_SERVER_DIR) + "');"
    "import server;"
    "print('ENABLED=%r PATH=%r' % (server._METRICS_ENABLED, server._METRICS.path))"
)
_env = dict(os.environ)
_env["AI_MEMORY_METRICS"] = "0"
_env["AI_MEMORY_DIR"] = _TMP
try:
    _out = subprocess.run([sys.executable, "-c", _probe], env=_env,
                          capture_output=True, text=True, timeout=120)
    _txt = (_out.stdout or "") + (_out.stderr or "")
    if "ENABLED=False" not in _txt:  # pragma: no cover
        print("     probe output:", _txt.strip()[-400:])
    check("M10a 开关关闭时 _METRICS_ENABLED=False", "ENABLED=False" in _txt)
    check("M10b 关闭时不创建指标文件（path=None）", "PATH=None" in _txt)

    _env2 = dict(os.environ)
    _env2.pop("AI_MEMORY_METRICS", None)
    _env2["AI_MEMORY_DIR"] = _TMP
    _out2 = subprocess.run([sys.executable, "-c", _probe], env=_env2,
                           capture_output=True, text=True, timeout=120)
    _txt2 = (_out2.stdout or "") + (_out2.stderr or "")
    check("M10c 默认开启（对照组）", "ENABLED=True" in _txt2)
except Exception as e:  # pragma: no cover - 子进程启动失败
    check("M10a 开关关闭时 _METRICS_ENABLED=False", False, repr(e))
    check("M10b 关闭时不创建指标文件（path=None）", False, repr(e))
    check("M10c 默认开启（对照组）", False, repr(e))

# =====================================================================
section("M11 · 指标层绝不拖累主流程（容错）")


class _BoomMetrics:
    """模拟「指标设施自身崩塌」——打点路径上的每个方法都抛异常。"""

    def inc(self, *a, **k):
        raise RuntimeError("boom")

    def observe_ms(self, *a, **k):
        raise RuntimeError("boom")

    def snapshot(self):
        raise RuntimeError("boom")


_orig_metrics = server._METRICS
server._METRICS = _BoomMetrics()
try:
    _r11 = server.memory_search("METRICTOKEN")
    _hit11 = "指标_新建" in _r11
except Exception as e:  # pragma: no cover - 冒泡即为缺陷
    print(f"     unexpected: {e!r}")
    _hit11 = False
try:
    _stats11 = server.memory_stats()
    _stats11_ok = "## 运行时指标" in _stats11
except Exception as e:  # pragma: no cover
    print(f"     unexpected: {e!r}")
    _stats11_ok = False
server._METRICS = _orig_metrics

check("M11a 指标内部异常不影响检索结果返回", _hit11)
check("M11b 指标内部异常不影响 memory_stats 输出结构", _stats11_ok)
check("M11c 恢复后指标仍可用", server._METRICS.snapshot().get("search_calls", 0) > 0)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
