#!/usr/bin/env python3
"""B2 索引增量化回归测试 for ai-memory MCP server.

背景（2026-10-08）:
    旧实现只有「整库指纹」一层缓存：任何一个文件变动都会让 `_iter_entries()`
    重新解析**全库** frontmatter。而 frontmatter 解析正是写入延迟的主要构成
    （201 篇规模已可测量）⇒ 写一条笔记就要付一次全库解析，延迟随库线性上升。

    改为两层缓存：
      ① 整库指纹 `_DIR_FINGERPRINT` 未变 ⇒ 直接回放 `_ENTRY_SNAPSHOT`；
      ② 指纹变了 ⇒ 逐文件按 `(size, mtime_ns)` 判新鲜，**只重解析真变化的文件**。
    并把缓存键由 `title` 改为 `relpath`（修掉不同目录同名条目互相覆盖的遗留）。
    另给 `_refresh_index()` 加内容签名短路。

本脚本的断言口径:
    **用 `server._PARSE_COUNT` 计数证明「增量真的省了活」** —— 光「跑绿了」不等于
    「变快了」。核心判据是「改 1 个文件只新增 1 次解析」，旧实现会是「+ 全库」，
    因此 B2-3 对旧实现**必然变红**（能证伪，不是恒绿断言）。

运行:
    python tests/test_index_cache.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-test-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

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


def disk_md() -> list[Path]:
    return sorted(ROOT.rglob("*.md"))


def entries() -> list[tuple[Path, dict, str]]:
    return server._iter_entries()


def body_map() -> dict[str, str]:
    """relpath -> 正文，便于按路径断言（而非 title）。"""
    return {p.relative_to(ROOT).as_posix(): b for p, _m, b in entries()}


def reset_cache() -> int:
    """模拟冷启动：清空两层缓存并把解析计数归零。返回归零后的计数。"""
    server._ENTRY_CACHE.clear()
    server._ENTRY_STAT.clear()
    server._ENTRY_SNAPSHOT.clear()
    server._CACHE_VALID = False
    server._PARSE_COUNT = 0
    return server._PARSE_COUNT


def bump_mtime(p: Path) -> None:
    """显式把 mtime 推到 1 秒后，避免「同刻度内改写被判为未变」导致 flaky。

    NTFS 的 mtime_ns 粒度足够细，但测试不应依赖这一点 —— 判据要确定。
    """
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# =====================================================================
# 准备数据
for idx, name in enumerate(["缓存甲", "缓存乙", "缓存丙"]):
    server.memory_write(name, f"内容 {idx}")

# =====================================================================
# B2-1: 冷启动仍解析全部文件（增量不得把首次也省掉）
print("\n[B2-1] 冷启动全量解析")
n_files = len(disk_md())
reset_cache()
got = entries()
check("B2-1a 冷启动解析数 == 库内文件数",
      server._PARSE_COUNT == n_files,
      f"parses={server._PARSE_COUNT} files={n_files}")
check("B2-1b 条目数与磁盘文件数一致", len(got) == n_files,
      f"entries={len(got)} files={n_files}")

# =====================================================================
# B2-2: 无变更 ⇒ 走整库快路径，零解析
print("\n[B2-2] 无变更重复枚举")
base = server._PARSE_COUNT
entries()
entries()
check("B2-2a 重复枚举不新增解析", server._PARSE_COUNT == base,
      f"{base} -> {server._PARSE_COUNT}")

# =====================================================================
# B2-3: 单文件变更 ⇒ 只增量解析 1 篇（本改动的核心收益）
print("\n[B2-3] 单文件变更只增量解析")
target = ROOT / "缓存甲.md"
target.write_text(target.read_text(encoding="utf-8").replace("内容 0", "内容 0 已改"),
                  encoding="utf-8")
bump_mtime(target)
base = server._PARSE_COUNT
entries()
delta = server._PARSE_COUNT - base
check("B2-3a 只新增 1 次解析（旧实现为 +全库）",
      delta == 1,
      f"Δparses={delta}，旧实现应为 {n_files}")
check("B2-3b 改动后的内容被读到",
      "已改" in body_map().get("缓存甲.md", ""),
      body_map().get("缓存甲.md", "")[:60])

# =====================================================================
# B2-4: 新增文件 ⇒ 只解析新那篇
print("\n[B2-4] 新增文件只解析新篇")
newp = ROOT / "缓存丁.md"
n0 = len(entries())          # ⚠️ 基线必须在「写文件之前」取：写入后再取会把新条目算进基线
newp.write_text("---\ntitle: 缓存丁\ntags: [test]\n---\n\n新条目正文\n", encoding="utf-8")
base = server._PARSE_COUNT
n1 = len(entries())
check("B2-4a 只新增 1 次解析", server._PARSE_COUNT - base == 1,
      f"Δparses={server._PARSE_COUNT - base}")
check("B2-4b 条目数 +1", n1 == n0 + 1, f"{n0} -> {n1}")

# =====================================================================
# B2-5: 删除文件 ⇒ 零解析 + 条目回落 + 缓存剪枝
print("\n[B2-5] 删除文件剪枝缓存")
newp.unlink()
base = server._PARSE_COUNT
n2 = len(entries())
check("B2-5a 删除不新增解析", server._PARSE_COUNT - base == 0,
      f"Δparses={server._PARSE_COUNT - base}")
check("B2-5b 条目数回落", n2 == n0, f"{n0} -> {n2}")
check("B2-5c 缓存已剪枝（无残留键）",
      "缓存丁.md" not in server._ENTRY_CACHE,
      str(sorted(server._ENTRY_CACHE))[:200])

# =====================================================================
# B2-6: 同名不同目录不得互相污染（旧实现以 title 为键 ⇒ 必然覆盖）
print("\n[B2-6] 同名不同目录共存")
server.memory_write("同名条目", "根目录版本。")
server.memory_write("子目录A/同名条目", "子目录版本。")
reset_cache()
entries()
cache = server._ENTRY_CACHE
check("B2-6a 两个 relpath 各自成键",
      "同名条目.md" in cache and "子目录A/同名条目.md" in cache,
      str(sorted(cache))[:200])
bodies = {k: v[2] for k, v in cache.items()}
check("B2-6b 两者正文互不串味",
      "根目录版本" in bodies.get("同名条目.md", "")
      and "子目录版本" in bodies.get("子目录A/同名条目.md", ""),
      f"root={bodies.get('同名条目.md', '')[:20]!r} sub={bodies.get('子目录A/同名条目.md', '')[:20]!r}")

# =====================================================================
# B2-7: 返回的 meta 一律浅拷贝 ⇒ 调用方就地改不会污染缓存
print("\n[B2-7] meta 拷贝隔离")
e1 = entries()
e1[0][1]["title"] = "被篡改的标题"
e2 = entries()
check("B2-7a 篡改返回值不影响后续枚举",
      all(m.get("title") != "被篡改的标题" for _p, m, _b in e2),
      str([m.get("title") for _p, m, _b in e2][:5]))

# =====================================================================
# B2-8: _refresh_index 内容签名短路
print("\n[B2-8] 索引刷新签名短路")
idx = ROOT / ".记忆索引.md"     # B6：生成物自 2026-10-08 起带 `.` 前缀
server._refresh_index()
mt0 = idx.stat().st_mtime_ns
server._refresh_index()
server._refresh_index()
check("B2-8a 内容未变时不重写索引（mtime 不动）",
      idx.stat().st_mtime_ns == mt0,
      f"{mt0} -> {idx.stat().st_mtime_ns}")

# =====================================================================
# B2-9: 短路不得吃掉「索引被外部删除」这一必须重建的场景
print("\n[B2-9] 索引被删除仍重建")
idx.unlink()
server._refresh_index()
check("B2-9a 索引被删除后仍会重建", idx.exists())
check("B2-9b 重建内容含既有条目",
      "缓存甲" in idx.read_text(encoding="utf-8"),
      idx.read_text(encoding="utf-8")[:120] if idx.exists() else "<缺失>")

# =====================================================================
# B2-10: 签名变化 ⇒ 正常重建（短路不许把新条目挡在索引外）
print("\n[B2-10] 签名变化触发重建")
server.memory_write("缓存戊", "新增以改变索引签名。")
check("B2-10a 新条目出现在索引中",
      "缓存戊" in idx.read_text(encoding="utf-8"),
      [ln for ln in idx.read_text(encoding="utf-8").splitlines() if "缓存" in ln][:5])

# =====================================================================
# cleanup
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
