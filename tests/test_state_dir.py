#!/usr/bin/env python3
"""运行时状态目录（AI_MEMORY_STATE_DIR）回归测试 for ai-memory MCP server。

沿革（为什么有这篇）:
    2026-10-21：索引 db 与指标 json 原先固定落在 `server.py` 同目录。裸跑没问题，
    但在容器里那是**镜像层 /app** —— 容器一重建就归零。索引本身是纯派生缓存
    （`access_count` 的真身写在 md 的 frontmatter 里，随 vault 走），丢了只是下次
    首访多一次全量扫描；真正补不回来的是**指标 json 的累积趋势**（检索零结果率、
    锁等待、索引重建次数）。于是引入 `AI_MEMORY_STATE_DIR` 指向独立卷 `/state`。

    这条改动的危险点不在「新功能不生效」，而在**静默回落**：
      * 目录不可写时不阻塞启动 ⇒ 一切看起来正常，只是数据悄悄落回镜像层；
      * 卷挂载点存在但属主不对时 `mkdir` 会成功，直到写 sqlite 才失败；
      * 而 sqlite 失败的处置路径是「降级为全库扫描」—— 连报错都没有。
    所以本脚本断言的重点是**落点**与**回落时必须吭声**，不是「跑得通」。

本脚本的断言口径（都可证伪）:
    S1  未设变量 ⇒ 落点仍是源码目录（裸跑行为与 v3.0 逐字节一致，不能借这次
        改动悄悄搬家）。
    S2  设了可写目录 ⇒ 索引 db 与指标 json **都**落那里，且 db 真的能建表、
        真的能写进条目（只比字符串等于没测）。
    S3  设了不可用目录 ⇒ **回落源码目录 + stderr 必须有一句告警**。
        只回落不吭声等于把「卷没挂上」伪装成「一切正常」，这是本条的主体。
    S4  叶子模块 `db_path` / `path_for` 保持**纯函数**（不做 IO、不建目录）——
        一旦它们偷偷 mkdir，S3 的告警就失去意义（目录由模块建了，回落分支永远
        走不到），将来没人再能定位「到底谁创建了这个目录」。

运行:
    python tests/test_state_dir.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑（server.py import mcp）。
脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库; S1/S3 只断言落点、
**不建任何文件到源码目录**（建立与否由 S2 在临时目录里验证）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 源码目录（本文件的上一级）。S1/S3 的期望落点就是它。
_REPO = Path(__file__).resolve().parent.parent

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


# ---------------------------------------------------------------------------
# 子进程模式：在**独立的解释器**里按给定 env 观察落点。
# 必须隔离进程 —— 这些值在 server import 时算一次并成为模块常量，同进程内改
# 环境变量不会重算，那会写出「测试全绿但什么都没测」的假绿。
# ---------------------------------------------------------------------------
def child(case: str) -> int:
    tmp = Path(tempfile.mkdtemp(prefix=f"ai-memory-state-{case}-"))
    vault = tmp / "vault"
    vault.mkdir()
    (vault / "2026-10-21-entry.md").write_text(
        "---\ntitle: 状态目录探针\ntier: warm\n---\n\n正文。\n", encoding="utf-8")

    os.environ["AI_MEMORY_DIR"] = str(vault)
    os.environ["AI_MEMORY_METRICS"] = "1"

    if case == "s1":
        os.environ.pop("AI_MEMORY_STATE_DIR", None)
    elif case == "s2":
        os.environ["AI_MEMORY_STATE_DIR"] = str(tmp / "state")
    elif case == "s3":
        # 用一个**文件**当目录：mkdir 必然失败，模拟「卷没挂上 / 属主不对」
        blocker = tmp / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        os.environ["AI_MEMORY_STATE_DIR"] = str(blocker / "state")
    else:
        raise SystemExit(f"未知 case: {case}")

    sys.path.insert(0, str(_REPO))
    import server  # noqa: E402

    out = {
        "state_dir": str(server._STATE_DIR),
        "server_dir": str(server._SERVER_DIR),
        "idx_file": str(server._IDX_FILE),
        "metrics_path": str(server._METRICS.path) if server._METRICS.path else "",
        "tmp": str(tmp),
        "vault": str(vault),
    }

    # 只在 S2 真写：其余情形只验落点，不往源码目录拉屎（测试自己也不该制造垃圾）
    if case == "s2":
        server._rebuild_index()
        import sqlite3
        conn = sqlite3.connect(str(server._IDX_FILE))
        out["rows"] = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        conn.close()
        server._METRICS.inc("probe_state_dir")
        server._METRICS.flush()
        out["metrics_exists"] = server._METRICS.path.exists()
    else:
        out["rows"] = -1
        out["metrics_exists"] = None

    print("__JSON__" + json.dumps(out))
    return 0


# ---------------------------------------------------------------------------
# 父进程模式：跑三个子进程 + 纯函数断言
# ---------------------------------------------------------------------------
def run_child(case: str) -> tuple[int, dict, str]:
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), f"--case={case}"],
                       capture_output=True, text=True, timeout=120)
    payload: dict = {}
    for line in p.stdout.splitlines():
        if line.startswith("__JSON__"):
            payload = json.loads(line[len("__JSON__"):])
            break
    # stderr 里既有日志也有告警，统一交回父进程判断
    return p.returncode, payload, p.stderr


def repo_state_files() -> set[str]:
    """源码目录里所有「运行时状态产物」的文件名 —— 用来证明测试没污染仓库。"""
    pats = ("memory_index_*.db", "memory_index_*.db-wal", "memory_index_*.db-shm",
            "memory_metrics_*.json")
    out: set[str] = set()
    for pat in pats:
        out |= {p.name for p in _REPO.glob(pat)}
    return out


def main() -> int:
    baseline = repo_state_files()

    # ---- S1 未设 --------------------------------------------------------
    print("[S1] 未设 AI_MEMORY_STATE_DIR ⇒ 落回源码目录（裸跑行为不变）")
    rc, j, _err = run_child("s1")
    check("S1a 子进程正常退出", rc == 0, f"rc={rc}")
    check("S1b _STATE_DIR == 源码目录",
          j.get("state_dir") == j.get("server_dir"), f"{j.get('state_dir')}")
    check("S1c 索引落在源码目录",
          Path(j.get("idx_file", "/x")).parent == _REPO, j.get("idx_file"))
    check("S1d 指标落在源码目录",
          Path(j.get("metrics_path", "/x")).parent == _REPO, j.get("metrics_path"))

    # ---- S2 可写 --------------------------------------------------------
    print("\n[S2] 设了可写目录 ⇒ 索引 db 与指标 json 都落那里，且真能写")
    rc, j, err2 = run_child("s2")
    tmp = Path(j.get("tmp", "/nonexistent"))
    state = tmp / "state"
    check("S2a 子进程正常退出", rc == 0, f"rc={rc}")
    check("S2b _STATE_DIR == 指定目录", j.get("state_dir") == str(state), j.get("state_dir"))
    check("S2c 索引 db 在状态目录", Path(j.get("idx_file", "/x")).parent == state,
          j.get("idx_file"))
    check("S2d 指标 json 在状态目录",
          Path(j.get("metrics_path", "/x")).parent == state, j.get("metrics_path"))
    check("S2e 索引真写进了条目（不是只换了个字符串）", j.get("rows") == 1,
          f"rows={j.get('rows')}")
    check("S2f 指标文件真落盘", j.get("metrics_exists") is True, str(j.get("metrics_exists")))
    check("S2g 可写情形不该有回落告警", "不可用" not in err2,
          [l for l in err2.splitlines() if "STATE_DIR" in l])

    # ---- S3 不可用 ------------------------------------------------------
    print("\n[S3] 设了不可用目录 ⇒ 回落源码目录，且必须告警")
    rc, j, err3 = run_child("s3")
    check("S3a 子进程不崩（索引与指标都不该成为启动故障点）", rc == 0, f"rc={rc}")
    check("S3b 回落到源码目录", j.get("state_dir") == j.get("server_dir"),
          j.get("state_dir"))
    check("S3c 索引跟着回落到源码目录",
          Path(j.get("idx_file", "/x")).parent == _REPO, j.get("idx_file"))
    warned = [l for l in err3.splitlines() if "AI_MEMORY_STATE_DIR" in l and "不可用" in l]
    check("S3d stderr 有明确的回落告警（不吭声才是真 bug）", bool(warned),
          f"stderr 尾部：{err3.strip()[-160:]}")

    # ---- S4 叶子模块保持纯函数 ------------------------------------------
    print("\n[S4] db_path / path_for 必须是纯函数（不建目录、不做 IO）")
    sys.path.insert(0, str(_REPO))
    import memory_index as midx
    import metrics as mx
    s4 = Path(tempfile.mkdtemp(prefix="ai-memory-state-s4-"))
    want_state = s4 / "never-created"
    p_idx = midx.db_path(want_state, s4 / "vault")
    p_met = mx.path_for(want_state, s4 / "vault")
    check("S4a db_path 落在传入目录", p_idx.parent == want_state, str(p_idx))
    check("S4b path_for 落在传入目录", p_met.parent == want_state, str(p_met))
    check("S4c 两者都不擅自 mkdir", not want_state.exists(), f"存在：{want_state}")
    check("S4d 文件名仍按 MEMORY_DIR 哈希隔离且前缀不变",
          p_idx.name.startswith("memory_index_") and p_met.name.startswith("memory_metrics_")
          and p_idx.stem.split("_")[-1] == p_met.stem.split("_")[-1],
          f"{p_idx.name} / {p_met.name}")

    # ---- S5 仓库未被污染 -------------------------------------------------
    print("\n[S5] 测试自身不得往源码目录写运行时产物")
    now = repo_state_files()
    check("S5a 源码目录没有新增 memory_index_* / memory_metrics_*",
          now == baseline, f"新增：{sorted(now - baseline)} 消失：{sorted(baseline - now)}")

    shutil.rmtree(s4, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    if _failed:
        print("失败项:")
        for f in _failed:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("--case="):
        raise SystemExit(child(sys.argv[1].split("=", 1)[1]))
    raise SystemExit(main())
