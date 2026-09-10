#!/usr/bin/env python3
"""异常恢复 / 自愈测试 for ai-memory MCP server (server.py)。

对应《ai-memory-template 改进建议》P0「增强异常恢复测试」：
    R1 索引 db 被删除          → 新进程启动后自动重建，数据不丢
    R2 索引 db 被写垃圾字节    → 功能不降级 + 损坏文件被丢弃重建（自愈）
    R3 锁文件残留 / 内容损坏   → 陈旧检测接管，不永久卡死
    R4 进程被 kill（模拟崩溃） → 不留半截文件、不留锁、不阻塞后续写入
    R5 外部编辑器改写文件      → 读盘为准（正文永不入库），索引 (size,mtime) 自愈
    R6 外部增删文件（git 切版本）→ 检索以磁盘为准，重建后索引与磁盘一致
    R7 畸形 md（0 字节/无分隔符/半截 ---/非法 YAML）→ 全部工具优雅降级不崩
    R8 索引重建幂等

设计要点：
    - 「新进程启动」这类场景必须用**真实子进程**（server 的 _IDX_NEEDS_REBUILD
      是进程级标志位，同进程内无法模拟冷启动）。子进程由 concurrency_worker.py
      驱动。
    - 「删除文件」统一用 rename（移动到 .orphan）而非 unlink：一来更贴近
      git checkout / 外部删除的语义，二来规避 Windows 上文件被 sqlite 句柄持有时
      unlink 失败的问题。

运行:
    python tests/test_recovery.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-recovery-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import locks  # noqa: E402
import memory_index as midx  # noqa: E402
import server  # noqa: E402

_WORKER = Path(__file__).resolve().parent / "concurrency_worker.py"
_ART = Path(tempfile.mkdtemp(prefix="ai-memory-recovery-art-"))
_ENV = {**os.environ, "AI_MEMORY_DIR": _TMP}
_LOCK = server.MEMORY_DIR / ".memory.lock"

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
    pfile = _ART / f"p_{tag}.json"
    rfile = _ART / f"r_{tag}.json"
    rfile.unlink(missing_ok=True)
    pfile.write_text(json.dumps({"op": op, **kw}, ensure_ascii=False), encoding="utf-8")
    try:
        proc = subprocess.run([sys.executable, str(_WORKER), str(pfile), str(rfile)],
                              capture_output=True, text=True, env=_ENV, timeout=300)
    except subprocess.TimeoutExpired:
        return {"op": op, "ok": False, "result": "", "error": "worker 超时"}
    if not rfile.exists():
        return {"op": op, "ok": False, "result": "",
                "error": f"worker 未产出结果 (rc={proc.returncode}) stderr={proc.stderr[-300:]}"}
    return json.loads(rfile.read_text(encoding="utf-8"))


def load_meta(title: str):
    for f in server.MEMORY_DIR.glob("*.md"):
        try:
            meta, body = server._load_memory(f)
        except Exception:
            continue
        if server._entry_title(meta, f) == title or f.stem == title:
            return meta, body, f
    return None, None, None


def drop_idx() -> None:
    """把索引 db 及其 wal/shm 移出（模拟文件不存在 / 损坏），用 rename 规避占用。"""
    gc.collect()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(server._IDX_FILE) + suffix)
        if p.exists():
            try:
                p.rename(Path(str(p) + ".orphan"))
            except OSError:
                try:
                    p.write_bytes(b"")
                except OSError:
                    pass


def idx_rows() -> list:
    """读取索引行；索引损坏时返回空列表（让断言给出可读诊断而非中断整个测试）。"""
    try:
        conn = midx.connect(server._IDX_FILE)
    except Exception:
        return []
    try:
        return midx.iter_all(conn)
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def index_relpaths() -> set[str]:
    return {r["relpath"] for r in idx_rows()}


def disk_relpaths() -> set[str]:
    s = {p.name for p in server.MEMORY_DIR.glob("*.md")}
    arc = server.MEMORY_DIR / ".archive"
    if arc.exists():
        s |= {".archive/" + p.name for p in arc.glob("*.md")}
    return s


def idx_is_sqlite() -> bool:
    """索引文件是否是合法 SQLite（读 16 字节 header）。"""
    if not server._IDX_FILE.exists():
        return False
    with open(server._IDX_FILE, "rb") as f:
        return f.read(16).startswith(b"SQLite format 3")


# =====================================================================
section("准备基线数据")
server.memory_write("恢复_基线_a", "基线 A 正文", tags=["rec"])
server.memory_write("恢复_基线_b", "基线 B 正文", tags=["rec"])
server.memory_write("恢复_基线_c", "基线 C 正文", tags=["rec"])
server._ensure_index()
check("P0 基线条目与索引已建立", len(index_relpaths()) >= 3 and idx_is_sqlite(),
      f"rows={index_relpaths()}")

# =====================================================================
section("R1 · 索引文件被删除 → 新进程自动重建")
drop_idx()
check("R1a 索引文件已不存在", not server._IDX_FILE.exists(),
      f"exists={server._IDX_FILE.exists()}")
_r1_read = server.memory_read("恢复_基线_a", max_chars=0)
check("R1b 索引缺失时当前进程读取仍正常（回退全扫）", "基线 A 正文" in _r1_read)

_r1_res = spawn("read", tag="r1", title="恢复_基线_a")
check("R1c 新进程（冷启动）读取无异常", _r1_res["ok"], str(_r1_res.get("error")))
check("R1d 新进程读到正确内容", "基线 A 正文" in _r1_res.get("result", ""))
check("R1e 索引文件已被新进程重建为合法 SQLite", idx_is_sqlite())
_r1_rows = idx_rows()
check("R1f 重建后索引与磁盘文件集合一致（数据不丢）",
      {r["relpath"] for r in _r1_rows} == disk_relpaths(),
      f"索引={sorted({r['relpath'] for r in _r1_rows})} 磁盘={sorted(disk_relpaths())}")

# =====================================================================
section("R2 · 索引文件被写垃圾字节 → 优雅降级 + 自愈")
drop_idx()
server._IDX_FILE.write_bytes(b"\x00\xffNOT-A-SQLITE-DB " * 200)
check("R2a 索引文件已损坏（header 非 SQLite）", not idx_is_sqlite())

_r2_res = spawn("read", tag="r2", title="恢复_基线_b")
check("R2b 索引损坏时新进程读取无异常（功能不降级）", _r2_res["ok"],
      str(_r2_res.get("error")))
check("R2c 索引损坏时仍读到正确内容（回退全扫兜底）",
      "基线 B 正文" in _r2_res.get("result", ""))
check("R2d 损坏的索引文件已被自动丢弃并重建为合法 SQLite", idx_is_sqlite())
_r2_rows = idx_rows()
check("R2e 重建后索引覆盖全部磁盘文件（自愈无数据丢失）",
      {r["relpath"] for r in _r2_rows} == disk_relpaths(),
      f"索引={sorted({r['relpath'] for r in _r2_rows})} 磁盘={sorted(disk_relpaths())}")

# =====================================================================
section("R3 · 锁文件残留 / 内容损坏 → 陈旧检测接管")
_LOCK.write_text(json.dumps({"pid": 999999, "time": time.time() - 600}), encoding="utf-8")
_r3a = server.memory_write("恢复_R3_陈旧锁", "陈旧锁下的写入", tags=["rec"])
check("R3a 陈旧锁被回收，写入成功", "已创建记忆" in _r3a, f"result={_r3a[:60]}")
check("R3b 写入后锁文件已释放", not _LOCK.exists())

_LOCK.write_text("GARBAGE{{{not json", encoding="utf-8")
_r3c = server.memory_write("恢复_R3_垃圾锁", "非法锁内容下的写入", tags=["rec"])
check("R3c 非法 JSON 锁被回收，写入成功", "已创建记忆" in _r3c, f"result={_r3c[:60]}")
check("R3d 写入后锁文件已释放", not _LOCK.exists())

locks.reset_lock_state()
_LOCK.write_text(json.dumps({"pid": os.getpid(), "time": time.time()}), encoding="utf-8")
_t0 = time.time()
_r3e = locks.acquire_lock(_LOCK, stale_timeout=0.3)
_dt = time.time() - _t0
check("R3e 新鲜锁会阻塞并在超时后返回 False（不会误判为陈旧）",
      _r3e is False and _dt >= 0.2, f"acquired={_r3e} elapsed={_dt:.2f}s")
locks.reset_lock_state()
try:
    _LOCK.unlink(missing_ok=True)
except OSError:
    pass

# =====================================================================
section("R4 · 进程被 kill（模拟崩溃）→ 不留损坏、不阻塞后续")
_pf = _ART / "p_r4kill.json"
_pf.write_text(json.dumps({"op": "write", "title": "恢复_R4_崩溃残留",
                           "content": "这篇不应被写入", "sleep_before": 60},
                          ensure_ascii=False), encoding="utf-8")
_proc = subprocess.Popen([sys.executable, str(_WORKER), str(_pf), str(_ART / "r_r4kill.json")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_ENV)
time.sleep(3.0)  # 等它完成 import 并进入 sleep_before 窗口
_proc.kill()
try:
    _proc.wait(timeout=15)
except subprocess.TimeoutExpired:
    pass
check("R4a 子进程已被强制终止", _proc.poll() is not None and _proc.returncode != 0,
      f"rc={_proc.returncode}")
check("R4b 崩溃进程未遗留锁文件", not _LOCK.exists())
check("R4c 崩溃进程未写入半截条目", load_meta("恢复_R4_崩溃残留")[0] is None)
check("R4d 库内无 0 字节 md（原子写保证）",
      not [p.name for p in server.MEMORY_DIR.glob("*.md") if p.stat().st_size == 0])
_r4e = server.memory_write("恢复_R4_崩溃后续", "崩溃之后的正常写入", tags=["rec"])
check("R4e 崩溃后写入仍正常（无残留锁阻塞）", "已创建记忆" in _r4e, f"result={_r4e[:60]}")

# =====================================================================
section("R5 · 外部编辑器改写文件 → 读盘为准 + 索引自愈")
_T5 = "恢复_R5_外部改写"
server.memory_write(_T5, "旧正文 EXTERNAL-OLD", tags=["rec"])
server._ensure_index()
_meta5, _body5, _path5 = load_meta(_T5)
check("R5a 外部改写前索引行存在", _path5 is not None)
_path5.write_text(f"---\ntitle: {_T5}\ntags: [external]\n---\n\n新正文 EXTERNAL-NEW",
                  encoding="utf-8")  # 模拟 Obsidian / git 直接改写，绕过 server
_r5 = server.memory_read(_T5, max_chars=0)
check("R5b 读到外部改写后的内容（正文实时读盘，索引不缓存正文）",
      "EXTERNAL-NEW" in _r5 and "EXTERNAL-OLD" not in _r5, f"result={_r5[:120]!r}")
_conn = midx.connect(server._IDX_FILE)
try:
    _row5 = [r for r in midx.iter_all(_conn) if r["relpath"] == _path5.name]
    _fresh5 = bool(_row5) and midx.fresh(_conn, server.MEMORY_DIR, _row5[0])
finally:
    try:
        _conn.close()
    except Exception:
        pass
check("R5c 索引行 (size,mtime) 已自动刷新为磁盘现状（自愈）", _fresh5)

# =====================================================================
section("R6 · 外部增删文件（模拟 git 切换版本）")
_T6 = "恢复_R6_被外部删除"
server.memory_write(_T6, "即将被外部删除的正文", tags=["rec"])
server._ensure_index()
_meta6, _, _path6 = load_meta(_T6)
_path6.rename(_path6.with_suffix(".md.orphan"))  # 模拟 git checkout 把文件切走
_r6a = server.memory_read(_T6, max_chars=0)
check("R6a 文件被外部删除后读取返回未找到（不抛异常）", "未找到" in _r6a,
      f"result={_r6a[:80]}")
_conn = midx.connect(server._IDX_FILE)
try:
    _row6 = [r for r in midx.iter_all(_conn) if r["relpath"] == _path6.name]
    _stale6 = (not _row6) or (not midx.fresh(_conn, server.MEMORY_DIR, _row6[0]))
finally:
    try:
        _conn.close()
    except Exception:
        pass
check("R6b 已删文件的索引行被判为不新鲜（不会返回陈旧数据）", _stale6)

_newp = server.MEMORY_DIR / "恢复_R6_外部新增.md"
_newp.write_text("---\ntitle: 恢复_R6_外部新增\ntags: [external]\n---\n\n这是 git 拉入的笔记",
                 encoding="utf-8")
_r6c = server.memory_read("恢复_R6_外部新增", max_chars=0)
check("R6c 外部新增文件可被检索（索引 miss 时全扫兜底）", "git 拉入的笔记" in _r6c,
      f"result={_r6c[:80]}")
check("R6d 兜底命中后该条已补入索引",
      "恢复_R6_外部新增.md" in index_relpaths(), f"idx={sorted(index_relpaths())[:5]}")

server._IDX_NEEDS_REBUILD = True
server._ensure_index()
check("R6e 重建后索引与磁盘集合一致", index_relpaths() == disk_relpaths(),
      f"仅索引={sorted(index_relpaths() - disk_relpaths())[:3]} "
      f"仅磁盘={sorted(disk_relpaths() - index_relpaths())[:3]}")

# =====================================================================
section("R7 · 畸形 md 优雅降级（0 字节 / 无分隔符 / 半截 --- / 非法 YAML）")
_MALFORMED = {
    "恢复_R7_空文件.md": "",
    "恢复_R7_无分隔符.md": "只有正文，完全没有 frontmatter 分隔符",
    "恢复_R7_半截分隔符.md": "---\ntitle: 只开了头\n\n正文在这里但没有收尾分隔符",
    "恢复_R7_非法yaml.md": "---\n!!!: [[[ 这不是合法 yaml\n:\t\tbad\n---\n\n正文",
}
for _name, _content in _MALFORMED.items():
    (server.MEMORY_DIR / _name).write_text(_content, encoding="utf-8")

_tool_errors: list[str] = []
for _tname, _tfn, _targs in [
    ("memory_list", server.memory_list, ()),
    ("memory_search", server.memory_search, ("恢复",)),
    ("memory_smart_search", server.memory_smart_search, ("恢复",)),
    ("memory_audit", server.memory_audit, ()),
    ("memory_index_draft", server.memory_index_draft, ()),
    ("memory_stats", server.memory_stats, ()),
    ("memory_orphans", server.memory_orphans, ()),
    ("memory_heat_suggest", server.memory_heat_suggest, ()),
    ("memory_rebuild_links", server.memory_rebuild_links, ()),
    ("memory_recent", server.memory_recent, (30,)),
]:
    try:
        _out = _tfn(*_targs)
        if not isinstance(_out, str):
            _tool_errors.append(f"{_tname}: 返回值非字符串")
    except Exception as e:  # noqa: BLE001
        _tool_errors.append(f"{_tname}: {type(e).__name__}: {e}")
check("R7a 畸形文件共处时 10 个只读工具全部不抛异常", not _tool_errors, f"{_tool_errors[:3]}")
check("R7b _iter_entries 跳过/降级畸形文件不崩",
      isinstance(server._iter_entries(), list))
_r7 = server.memory_read("恢复_基线_c", max_chars=0)
check("R7c 畸形文件不影响正常条目检索", "基线 C 正文" in _r7, f"result={_r7[:80]}")
check("R7d 畸形文件未阻止索引重建",
      (server._IDX_NEEDS_REBUILD is False) and idx_is_sqlite())

# =====================================================================
section("R8 · 索引重建幂等")
server._IDX_NEEDS_REBUILD = True
server._ensure_index()
_n1 = len(index_relpaths())
server._IDX_NEEDS_REBUILD = True
server._ensure_index()
_n2 = len(index_relpaths())
check("R8 连续两次重建结果稳定（幂等且非空）", _n1 == _n2 and _n1 > 0, f"{_n1} vs {_n2}")

# =====================================================================
# cleanup
shutil.rmtree(_TMP, ignore_errors=True)
shutil.rmtree(_ART, ignore_errors=True)
gc.collect()
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
