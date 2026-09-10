#!/usr/bin/env python3
"""20 个 MCP 工具全量冒烟测试。

用途:
    确认 server.py 的**全部** MCP 工具都能被正常调用且不抛异常。与
    test_roundtrip.py 分工互补:
      - test_roundtrip.py 验证「语义正确性」(frontmatter 写读无损、反斜杠
        不雪崩、read 不污染 updated、SQLite 索引自愈等)
      - 本脚本验证「覆盖面」(20 个工具一个不漏、全部可调用)
    两者结合才能支撑 server.py 的安全重构（P3 模块化即以此护航）。

    脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_tools_smoke.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-smoke-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

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


def call(name: str, fn, *args, expect: str = "", **kwargs) -> str:
    """调用一个工具, 断言不抛异常且返回值为字符串。"""
    try:
        out = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - 冒烟测试需要捕获一切异常
        check(name, False, f"抛出异常 {type(e).__name__}: {e}")
        return ""
    if not isinstance(out, str):
        check(name, False, f"返回值非字符串: {type(out).__name__}")
        return ""
    if expect and expect not in out:
        check(name, False, f"返回未含期望文本 {expect!r}: {out[:120]}")
        return out
    check(name, True)
    return out


def section(name: str) -> None:
    print(f"\n== {name} ==")


# =====================================================================
section("准备测试数据")
call("write 主笔记", server.memory_write, "冒烟_主笔记", "正文含 [[冒烟_关联笔记]] 链接, 讲 python 与 mcp。",
     tags=["smoke", "mcp"], summary="冒烟测试主笔记")
call("write 关联笔记", server.memory_write, "冒烟_关联笔记", "被主笔记引用的笔记, 讲 sqlite。", tags=["smoke"])
call("write 冷笔记", server.memory_write, "冒烟_冷笔记", "用于测试归档与批量分层。", tags=["smoke"], tier="cold")

# =====================================================================
section("读取类工具")
call("memory_read", server.memory_read, "冒烟_主笔记", expect="正文含")
call("memory_list", server.memory_list, expect="冒烟_")
call("memory_search", server.memory_search, "python", expect="冒烟_")
call("memory_recent", server.memory_recent, 7, expect="冒烟_")
call("memory_smart_search", server.memory_smart_search, "sqlite", expect="冒烟_")

# =====================================================================
section("统计与审计类工具")
call("memory_stats", server.memory_stats, expect="")
call("memory_audit", server.memory_audit, expect="")
call("memory_index_draft", server.memory_index_draft, expect="")
call("memory_heat_suggest", server.memory_heat_suggest, expect="")
call("memory_orphans", server.memory_orphans, expect="")

# =====================================================================
section("关系图与链接工具")
call("memory_graph", server.memory_graph, "冒烟_主笔记", expect="冒烟_")
call("memory_rebuild_links", server.memory_rebuild_links, expect="")

# =====================================================================
section("元数据修改类工具")
call("memory_update_metadata", server.memory_update_metadata, "冒烟_关联笔记", tier="cold", expect="")
call("memory_batch_tag", server.memory_batch_tag, "smoke", "smoke2", expect="")
call("memory_batch_tier", server.memory_batch_tier, "warm", expect="")

# =====================================================================
section("归档类工具")
call("memory_archive", server.memory_archive, "冒烟_冷笔记", expect="")
call("memory_restore", server.memory_restore, "冒烟_冷笔记", expect="")
call("memory_archive_old", server.memory_archive_old, 90, expect="")

# =====================================================================
section("删除类工具")
call("memory_delete(trash)", server.memory_delete, "冒烟_关联笔记", expect="")
call("memory_delete(purge)", server.memory_delete, "冒烟_主笔记", trash=False, purge=True, expect="")

# =====================================================================
section("MCP 工具注册完整性")
import asyncio  # noqa: E402

EXPECTED_TOOLS = {
    "memory_write", "memory_read", "memory_rebuild_links", "memory_search",
    "memory_list", "memory_delete", "memory_update_metadata", "memory_audit",
    "memory_index_draft", "memory_archive", "memory_restore", "memory_heat_suggest",
    "memory_graph", "memory_orphans", "memory_batch_tag", "memory_batch_tier",
    "memory_archive_old", "memory_smart_search", "memory_recent", "memory_stats",
}
try:
    registered = {t.name for t in asyncio.run(server.mcp.list_tools())}
    check("工具总数 = 20", len(registered) == 20, f"实际 {len(registered)}: {sorted(registered)}")
    check("工具集合与预期一致", registered == EXPECTED_TOOLS,
          f"缺失={sorted(EXPECTED_TOOLS - registered)} 多余={sorted(registered - EXPECTED_TOOLS)}")
except Exception as e:  # noqa: BLE001
    check("列出注册工具", False, f"{type(e).__name__}: {e}")

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
