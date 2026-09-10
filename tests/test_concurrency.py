#!/usr/bin/env python3
"""跨进程并发一致性测试 for ai-memory MCP server (server.py)。

为什么必须用真实子进程而不是线程：
    server.py 的锁（locks.py）用 ``threading.local()`` 记录重入深度，进程内所有
    线程共享该状态；``_ENTRY_CACHE`` / ``_CACHE_VALID`` 同样是进程级缓存。只有
    真实子进程才能复现「多个 AI 工具（WorkBuddy / Claude Desktop / Codex）同时
    写同一个记忆库」的语义：锁文件跨进程互斥、乐观锁版本冲突、SQLite 索引最终
    一致。真实记忆库不能拿来做实验——本脚本把 AI_MEMORY_DIR 指向临时目录。

覆盖场景：
    A. 乐观锁竞争  10 进程以同一 expected_version 写同一 title → 恰好 1 个成功
    B. 无冲突并发  10 进程写 10 个不同 title → 全部成功且内容不串条
    C. 混合操作    写 / 归档 / 删除并发 → 无半截文件、无 .tmp 残留、无 0 字节
    D. 读写并发    读循环与写并发 → 每次读到的都是完整版本（原子写保证）
    E. 收尾对账    无 .memory.lock 残留、索引行与磁盘逐一对账且全部新鲜

运行:
    python tests/test_concurrency.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-conc-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import memory_index as midx  # noqa: E402
import server  # noqa: E402

_WORKER = Path(__file__).resolve().parent / "concurrency_worker.py"
_ART = Path(tempfile.mkdtemp(prefix="ai-memory-conc-art-"))
_ENV = {**os.environ, "AI_MEMORY_DIR": _TMP}

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


def spawn(op: str, *, tag: str, **kw) -> dict:
    """起一个真实子进程执行一次操作；结果由 result 文件回传（UTF-8 JSON）。"""
    pfile = _ART / f"p_{tag}.json"
    rfile = _ART / f"r_{tag}.json"
    rfile.unlink(missing_ok=True)
    pfile.write_text(json.dumps({"op": op, **kw}, ensure_ascii=False), encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(_WORKER), str(pfile), str(rfile)],
            capture_output=True, text=True, env=_ENV, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"op": op, "ok": False, "result": "", "error": "worker 超时", "duration": -1}
    if not rfile.exists():
        return {"op": op, "ok": False, "result": "", "error":
                f"worker 未产出结果 (rc={proc.returncode}) stderr={proc.stderr[-300:]}",
                "duration": -1}
    return json.loads(rfile.read_text(encoding="utf-8"))


def run_parallel(jobs: list[dict]) -> list[dict]:
    """真并发提交多个子进程（线程只负责等待，工作是独立进程）。"""
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as ex:
        futs = [ex.submit(spawn, **job) for job in jobs]
        return [f.result() for f in futs]


def load_meta(title: str):
    """直接读盘解析 frontmatter，不走进程内缓存。"""
    for f in server.MEMORY_DIR.glob("*.md"):
        try:
            meta, body = server._load_memory(f)
        except Exception:
            continue
        if server._entry_title(meta, f) == title or f.stem == title:
            return meta, body, f
    return None, None, None


def root_md() -> list[Path]:
    return sorted(server.MEMORY_DIR.glob("*.md"))


def all_md() -> list[Path]:
    files = list(server.MEMORY_DIR.glob("*.md"))
    arc = server.MEMORY_DIR / ".archive"
    if arc.exists():
        files += list(arc.glob("*.md"))
    return files


def index_relpaths() -> set[str]:
    conn = midx.connect(server._IDX_FILE)
    try:
        return {r["relpath"] for r in midx.iter_all(conn)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def disk_relpaths() -> set[str]:
    s = {p.name for p in server.MEMORY_DIR.glob("*.md")}
    arc = server.MEMORY_DIR / ".archive"
    if arc.exists():
        s |= {".archive/" + p.name for p in arc.glob("*.md")}
    return s


# =====================================================================
section("场景 A · 乐观锁竞争（10 进程争写同一 title，expected_version 相同）")
TITLE_A = "并发_A_乐观锁"
server.memory_write(TITLE_A, "初始正文 OLD", tags=["conc"])
_meta0, _body0, _ = load_meta(TITLE_A)
check("A0 预建条目 version=1", _meta0 is not None and _meta0.get("version") == 1,
      f"meta={_meta0}")

_a_jobs = [{"op": "write", "tag": f"a{i}", "title": TITLE_A,
            "content": f"WINNER-{i} 这是第 {i} 号进程写入的完整正文",
            "source": f"worker-a{i}", "expected_version": 1} for i in range(10)]
_a_res = run_parallel(_a_jobs)

_a_ok = [r for r in _a_res if r["ok"] and "已更新记忆" in r.get("result", "")]
_a_conflict = [r for r in _a_res if "版本冲突" in r.get("result", "")]
_a_err = [r for r in _a_res if not r["ok"]]

check("A1 无子进程异常退出", not _a_err, f"异常={[r.get('error') for r in _a_err]}")
check("A2 恰好 1 个子进程写入成功", len(_a_ok) == 1,
      f"成功数={len(_a_ok)} results={[r.get('result', '')[:40] for r in _a_res]}")
check("A3 其余 9 个子进程均被版本冲突拒绝", len(_a_conflict) == 9,
      f"冲突数={len(_a_conflict)}")
check("A4 冲突信息包含实际 version（可诊断）",
      all("实际 version=2" in r.get("result", "") for r in _a_conflict),
      f"{[r.get('result', '')[:60] for r in _a_conflict][:2]}")

_metaA, _bodyA, _pathA = load_meta(TITLE_A)
check("A5 最终 version 只递增一次（=2）", _metaA is not None and _metaA.get("version") == 2,
      f"version={_metaA and _metaA.get('version')}")
_winner_body = _bodyA.strip() if _bodyA else ""
check("A6 正文为单一胜者的完整内容（无混写/无拼接）",
      _winner_body.startswith("WINNER-") and len(_winner_body) == len("WINNER-0 这是第 0 号进程写入的完整正文"),
      f"body={_winner_body[:80]!r}")
check("A7 frontmatter 可解析且 title 正确",
      _metaA is not None and _metaA.get("title") == TITLE_A)
check("A8 胜者 source 已落盘（未被并发覆盖为其他 worker）",
      str(_metaA.get("source", "")).startswith("worker-a"), f"source={_metaA.get('source')}")

# =====================================================================
section("场景 B · 无冲突并发（10 进程写 10 个不同 title）")
TITLES_B = [f"并发_B_{i}" for i in range(10)]
_b_jobs = [{"op": "write", "tag": f"b{i}", "title": TITLES_B[i],
            "content": f"B-{i} 内容", "tags": ["conc"], "source": f"worker-b{i}"}
           for i in range(10)]
_b_res = run_parallel(_b_jobs)

check("B1 10 个子进程全部成功", all(r["ok"] and "已创建记忆" in r.get("result", "") for r in _b_res),
      f"{[(r['ok'], r.get('result', '')[:24]) for r in _b_res]}")
_b_missing = [t for t in TITLES_B if load_meta(t)[0] is None]
check("B2 10 个文件全部落盘", not _b_missing, f"缺失={_b_missing}")

_b_bad: list[str] = []
for i, t in enumerate(TITLES_B):
    meta, body, _ = load_meta(t)
    if meta is None or meta.get("title") != t or body.strip() != f"B-{i} 内容":
        _b_bad.append(f"{t}: meta={meta and meta.get('title')} body={body and body.strip()[:20]}")
check("B3 每个文件 title 与正文一一对应（无串条）", not _b_bad, f"{_b_bad[:3]}")
check("B4 每篇 version=1（新建不误增）",
      all((load_meta(t)[0] or {}).get("version") == 1 for t in TITLES_B))

# =====================================================================
section("场景 C · 混合操作（写 / 归档 / 删除 并发）")
TITLES_CW = [f"并发_C_写{i}" for i in range(3)]
TITLES_CA = [f"并发_C_归档{i}" for i in range(3)]
TITLES_CD = [f"并发_C_删除{i}" for i in range(2)]
for t in TITLES_CW + TITLES_CA + TITLES_CD:
    server.memory_write(t, f"{t} 原始正文", tags=["conc"])

_c_jobs: list[dict] = []
for i, t in enumerate(TITLES_CW):
    _c_jobs.append({"op": "write", "tag": f"cw{i}", "title": t,
                    "content": f"{t} 并发改写后的正文", "source": "worker-cw"})
for i, t in enumerate(TITLES_CA):
    _c_jobs.append({"op": "archive", "tag": f"ca{i}", "title": t})
for i, t in enumerate(TITLES_CD):
    _c_jobs.append({"op": "delete", "tag": f"cd{i}", "title": t})
_c_res = run_parallel(_c_jobs)

check("C1 8 个混合操作进程无异常退出", all(r["ok"] for r in _c_res),
      f"{[r.get('error') for r in _c_res if not r['ok']]}")
check("C2 写操作全部成功",
      all("已更新记忆" in r.get("result", "") for r in _c_res[:3]),
      f"{[r.get('result', '')[:30] for r in _c_res[:3]]}")
check("C3 归档操作全部成功",
      all("已归档" in r.get("result", "") for r in _c_res[3:6]),
      f"{[r.get('result', '')[:30] for r in _c_res[3:6]]}")
check("C4 删除操作全部成功",
      all("已永久删除" in r.get("result", "") for r in _c_res[6:]),
      f"{[r.get('result', '')[:30] for r in _c_res[6:]]}")

_cw_bad = [t for t in TITLES_CW if "并发改写后的正文" not in (load_meta(t)[1] or "")]
check("C5 并发改写的正文已完整落盘", not _cw_bad, f"{_cw_bad}")
_ca_bad = [t for t in TITLES_CA if "已归档" not in (load_meta(t)[1] or "")]
check("C6 被归档条目已变为归档 stub", not _ca_bad, f"{_ca_bad}")
_cd_alive = [t for t in TITLES_CD if load_meta(t)[0] is not None]
check("C7 被删除条目已从库内消失", not _cd_alive, f"{_cd_alive}")
check("C8 归档副本存在于 .archive/",
      len(list((server.MEMORY_DIR / ".archive").glob("*.md"))) >= 3,
      f"archive 文件数={len(list((server.MEMORY_DIR / '.archive').glob('*.md')))}")

_tmp_left = [str(p) for p in server.MEMORY_DIR.rglob("*.tmp")]
check("C9 无 .tmp 半成品残留（原子写未留残骸）", not _tmp_left, f"{_tmp_left[:3]}")
_zero = [p.name for p in all_md() if p.stat().st_size == 0]
check("C10 无 0 字节文件", not _zero, f"{_zero[:3]}")
_unparsable: list[str] = []
for p in all_md():
    try:
        server._parse_frontmatter(p.read_text(encoding="utf-8"))
    except Exception as e:
        _unparsable.append(f"{p.name}: {type(e).__name__}")
check("C11 全部 md 的 frontmatter 均可解析（无半截写入）", not _unparsable, f"{_unparsable[:3]}")

# =====================================================================
section("场景 D · 读写并发（读循环 vs 多进程写）")
TITLE_D = "并发_D_读写"
_FILLER = "x" * 2000
server.memory_write(TITLE_D, f"[BEGIN] 初始 {_FILLER} [END-0]", tags=["conc"])

_d_jobs: list[dict] = [{"op": "read_loop", "tag": "d_read", "title": TITLE_D,
                        "count": 300, "delay": 0.008}]
for i in range(3):
    _d_jobs.append({"op": "write", "tag": f"d_w{i}", "title": TITLE_D,
                    "content": f"[BEGIN] 第 {i} 轮 {_FILLER} [END-{i + 1}]",
                    "source": f"worker-d{i}"})
_d_res = run_parallel(_d_jobs)
_d_read = _d_res[0]
_d_writes = _d_res[1:]

check("D1 读循环子进程无异常", _d_read["ok"], f"{_d_read.get('error')}")
_d_w_err = [r.get("error") for r in _d_writes if not r["ok"]]
check("D2 并发写子进程无异常（Windows 原子替换未被读阻塞）", not _d_w_err, f"{_d_w_err}")
_reads = _d_read.get("reads", [])
check("D3 读循环返回 300 次结果", len(_reads) == 300, f"实际 {len(_reads)}")
check("D4 无任何空读取", all(bool(r.strip()) for r in _reads),
      f"空读取数={sum(1 for r in _reads if not r.strip())}")
check("D5 每次读取都含完整首尾标记（未读到半截文件）",
      all("[BEGIN" in r and "[END-" in r for r in _reads),
      f"残缺数={sum(1 for r in _reads if not ('[BEGIN' in r and '[END-' in r))}")
check("D6 每次读取的正文长度均完整（>=2000 字节填充）",
      all(len(r) >= 2000 for r in _reads), f"最小长度={min((len(r) for r in _reads), default=0)}")
_marks = {r.split("[END-")[-1].split("]")[0] for r in _reads if "[END-" in r}
check("D7 读到的版本号均为合法轮次（0~3）",
      _marks.issubset({"0", "1", "2", "3"}), f"marks={_marks}")

# =====================================================================
section("场景 E · 收尾对账（锁残留 / 索引一致性）")
check("E1 无 .memory.lock 残留（全部进程已释放）",
      not (server.MEMORY_DIR / ".memory.lock").exists())
check("E2 无 .tmp 残留（全场景）", not list(server.MEMORY_DIR.rglob("*.tmp")))
check("E3 无 .tmp 残留于 .archive", not list((server.MEMORY_DIR / ".archive").glob("*.tmp"))
      if (server.MEMORY_DIR / ".archive").exists() else True)

server._ensure_index()
_idx = index_relpaths()
_disk = disk_relpaths()
check("E4 索引行集合与磁盘文件集合一致", _idx == _disk,
      f"仅在索引={sorted(_idx - _disk)[:3]} 仅在磁盘={sorted(_disk - _idx)[:3]}")

_conn = midx.connect(server._IDX_FILE)
try:
    _rows = midx.iter_all(_conn)
    _stale = [r["relpath"] for r in _rows if not midx.fresh(_conn, server.MEMORY_DIR, r)]
finally:
    try:
        _conn.close()
    except Exception:
        pass
check("E5 索引所有行的 (size,mtime) 均与磁盘一致（无陈旧行）", not _stale, f"{_stale[:3]}")
check("E6 索引条目数 == 磁盘文件数", len(_rows) == len(_disk),
      f"索引={len(_rows)} 磁盘={len(_disk)}")

# =====================================================================
# cleanup：临时库 + 临时索引 db（运行时缓存，删掉可重建）
shutil.rmtree(_TMP, ignore_errors=True)
shutil.rmtree(_ART, ignore_errors=True)
for _p in (server._IDX_FILE, Path(str(server._IDX_FILE) + "-wal"),
           Path(str(server._IDX_FILE) + "-shm")):
    try:
        _p.unlink(missing_ok=True)
    except OSError:
        pass

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
