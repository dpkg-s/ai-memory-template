#!/usr/bin/env python3
"""作用域 / 生命周期 / 冲突字段测试 for ai-memory MCP server (server.py)。

对应《ai-memory-template 改进建议》：
    P0「scope 与项目级隔离」「定义冲突处理原则」
    P1「Lifecycle 与 Tier 分离」「stale / supersedes / conflicts」「强化 provenance」
    P2「Schema Version」

覆盖：
    S1  新建条目默认值     scope=global / status=active / schema_version=2 落盘
    S2  scope + project    字段层隔离（刻意不做 global/projects/temporary 目录分层）
    S3  非法取值拒绝        scope / status 非法值不落盘
    S4  更新继承           只改正文不会丢 scope/project/status/supersedes/conflicts
    S5  update_metadata    生命周期字段可单独修正；空串 / 空列表可清除
    S6  source_context     显式传入优先，其次回落环境变量 AI_MEMORY_SOURCE_CONTEXT
    S7  检索默认排除归档     search / list / smart_search + 隐藏计数提示
    S8  scope/project 过滤  检索支持按作用域与项目筛选
    S9  归档 / 恢复联动      archive→status=archived，restore→status=active
    S10 统计与审计          stats 出各维度分布；audit 检出字段不一致
    S11 向后兼容            旧笔记（无新字段）读取端注入默认值，schema 视为 v1

真实记忆库不能拿来做实验——本脚本把 AI_MEMORY_DIR 指向临时目录。

运行:
    python tests/test_scope_lifecycle.py     # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-scope-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ.pop("AI_MEMORY_SOURCE_CONTEXT", None)  # 由用例自行控制
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


def section(name: str) -> None:
    print(f"\n== {name} ==")


def load_meta(title: str) -> tuple[dict, str]:
    """直接读盘解析 frontmatter（不走进程内缓存）。"""
    for f in list(server.MEMORY_DIR.glob("*.md")) + list((server.MEMORY_DIR / ".archive").glob("*.md")):
        try:
            meta, body = server._load_memory(f)
        except Exception:
            continue
        if server._entry_title(meta, f) == title or f.stem == title:
            return meta, body
    return {}, ""


# =====================================================================
section("S1 · 新建条目的默认值")
server.memory_write("范围_默认值", "这是一条普通记忆。", tags=["scope"])
_m1, _b1 = load_meta("范围_默认值")
check("S1a scope 默认落盘为 global", _m1.get("scope") == "global", f"scope={_m1.get('scope')}")
check("S1b status 默认落盘为 active", _m1.get("status") == "active", f"status={_m1.get('status')}")
check("S1c schema_version 落盘为 2",
      _m1.get("schema_version") == 2, f"v={_m1.get('schema_version')}")
check("S1d project 未指定时不写出该字段", "project" not in _m1)
check("S1e supersedes / conflicts 未指定时不写出",
      "supersedes" not in _m1 and "conflicts" not in _m1)

# =====================================================================
section("S2 · scope + project（字段层隔离，非目录分层）")
server.memory_write("范围_项目记忆", "属于项目 A 的记忆。", tags=["scope"],
                    scope="project", project="项目A")
_m2, _ = load_meta("范围_项目记忆")
check("S2a scope=project 已落盘", _m2.get("scope") == "project")
check("S2b project 已落盘", _m2.get("project") == "项目A")
check("S2c 仍存放在库根目录（不做目录分层）",
      (server.MEMORY_DIR / "范围_项目记忆.md").exists(),
      f"根目录文件={[p.name for p in server.MEMORY_DIR.glob('*.md')]}")

server.memory_write("范围_临时记忆", "临时信息。", tags=["scope"], scope="temporary")
check("S2d scope=temporary 已落盘", load_meta("范围_临时记忆")[0].get("scope") == "temporary")

# =====================================================================
section("S3 · 非法取值拒绝（不落盘）")
_bad_scope = server.memory_write("范围_非法scope", "正文", scope="galaxy")
check("S3a 非法 scope 被拒绝", "错误" in _bad_scope and "非法" in _bad_scope,
      f"result={_bad_scope[:60]}")
check("S3b 非法 scope 未创建文件", load_meta("范围_非法scope")[0] == {})
_bad_status = server.memory_write("范围_非法status", "正文", status="deleted")
check("S3c 非法 status 被拒绝", "错误" in _bad_status and "合法取值" in _bad_status,
      f"result={_bad_status[:60]}")
check("S3d 非法 status 未创建文件", load_meta("范围_非法status")[0] == {})
_bad_upd = server.memory_update_metadata("范围_默认值", status="deleted")
check("S3e update_metadata 同样拒绝非法 status", "错误" in _bad_upd,
      f"result={_bad_upd[:60]}")

# =====================================================================
section("S4 · 更新继承（只改正文不丢语义字段）")
server.memory_write("范围_继承", "第一版正文。", tags=["scope"], scope="project",
                    project="项目B", status="stale", mem_type="decision",
                    confidence="high", verified=True, supersedes="范围_项目记忆")
_m4, _ = load_meta("范围_继承")
check("S4a 初始字段齐备",
      _m4.get("scope") == "project" and _m4.get("project") == "项目B"
      and _m4.get("status") == "stale" and _m4.get("supersedes") == "范围_项目记忆",
      f"{ {k: _m4.get(k) for k in ('scope', 'project', 'status', 'supersedes')} }")
server.memory_write("范围_继承", "第二版正文（只改正文）。", tags=["scope"])
_m4b, _b4b = load_meta("范围_继承")
check("S4b 更新后 scope/project 被继承", _m4b.get("scope") == "project"
      and _m4b.get("project") == "项目B", f"{_m4b.get('scope')}/{_m4b.get('project')}")
check("S4c 更新后 status 被继承（未被重置为 active）", _m4b.get("status") == "stale",
      f"status={_m4b.get('status')}")
check("S4d 更新后 supersedes 被继承", _m4b.get("supersedes") == "范围_项目记忆")
check("S4e 更新后 type/confidence/verified 仍被继承",
      _m4b.get("type") == "decision" and _m4b.get("confidence") == "high"
      and _m4b.get("verified") is True)
check("S4f 正文确已更新", "第二版正文" in _b4b)

# =====================================================================
section("S5 · memory_update_metadata 单独修正生命周期字段")
_r5 = server.memory_update_metadata("范围_默认值", status="deprecated",
                                    supersedes="范围_继承", conflicts=["范围_项目记忆"],
                                    scope="project", project="项目C",
                                    source_context="manual")
check("S5a update_metadata 返回成功", "已更新元数据" in _r5, f"result={_r5[:60]}")
_m5, _b5 = load_meta("范围_默认值")
check("S5b status 已改为 deprecated", _m5.get("status") == "deprecated")
check("S5c supersedes 已写入", _m5.get("supersedes") == "范围_继承")
check("S5d conflicts 列表已写入", _m5.get("conflicts") == ["范围_项目记忆"])
check("S5e scope/project 已改写", _m5.get("scope") == "project" and _m5.get("project") == "项目C")
check("S5f source_context 已写入", _m5.get("source_context") == "manual")
check("S5g 正文未被改写（只动元数据）", "普通记忆" in _b5, f"body={_b5[:40]!r}")
check("S5h schema_version 升为 2", _m5.get("schema_version") == 2)

server.memory_update_metadata("范围_默认值", supersedes="", conflicts=[],
                              project="", source_context="")
_m5c, _ = load_meta("范围_默认值")
check("S5i 空串 / 空列表可清除对应字段",
      "supersedes" not in _m5c and "conflicts" not in _m5c
      and "project" not in _m5c and "source_context" not in _m5c,
      f"{ {k: _m5c.get(k) for k in ('supersedes', 'conflicts', 'project', 'source_context')} }")

# =====================================================================
section("S6 · source_context：显式传入优先于环境变量")
server.memory_write("范围_上下文显式", "正文", source_context="codex")
check("S6a 显式 source_context 落盘",
      load_meta("范围_上下文显式")[0].get("source_context") == "codex")
os.environ["AI_MEMORY_SOURCE_CONTEXT"] = "workbuddy"
try:
    server.memory_write("范围_上下文环境变量", "正文")
    check("S6b 环境变量回落生效",
          load_meta("范围_上下文环境变量")[0].get("source_context") == "workbuddy",
          f"ctx={load_meta('范围_上下文环境变量')[0].get('source_context')}")
    server.memory_write("范围_上下文环境变量2", "正文", source_context="claude")
    check("S6c 显式值优先于环境变量",
          load_meta("范围_上下文环境变量2")[0].get("source_context") == "claude")
finally:
    os.environ.pop("AI_MEMORY_SOURCE_CONTEXT", None)
server.memory_write("范围_上下文无", "正文")
check("S6d 均未提供时不写出该字段", "source_context" not in load_meta("范围_上下文无")[0])

# =====================================================================
section("S7 · 检索默认排除 archived（含隐藏计数提示）")
server.memory_write("范围_待归档", "归档专用正文关键字 ZZARCHIVE。", tags=["scope"],
                    mem_type="fact")
server.memory_archive("范围_待归档")
_m7, _ = load_meta("范围_待归档")
check("S7a 归档后根目录 stub 的 status=archived", _m7.get("status") == "archived",
      f"status={_m7.get('status')}")

_search_default = server.memory_search("ZZARCHIVE")
check("S7b 默认搜索不返回 archived 条目", "范围_待归档" not in _search_default,
      f"result={_search_default[:120]}")
check("S7c 默认搜索给出隐藏提示（不让人误以为记忆不存在）",
      "被隐藏" in _search_default, f"result={_search_default[:200]}")
_search_any = server.memory_search("ZZARCHIVE", status="any")
check("S7d status=any 可看到归档条目", "范围_待归档" in _search_any,
      f"result={_search_any[:120]}")
_search_arch = server.memory_search("ZZARCHIVE", status="archived")
check("S7e status=archived 精确查询可看到", "范围_待归档" in _search_arch)

_list_default = server.memory_list(limit=200)
check("S7f 默认列表不含归档条目", "范围_待归档" not in _list_default)
_list_any = server.memory_list(limit=200, status="any")
check("S7g 列表 status=any 含归档条目", "范围_待归档" in _list_any)
_smart_default = server.memory_smart_search("ZZARCHIVE")
check("S7h 智能搜索默认排除归档", "范围_待归档" not in _smart_default,
      f"result={_smart_default[:120]}")
_smart_any = server.memory_smart_search("ZZARCHIVE", status="any")
check("S7i 智能搜索 status=any 可命中", "范围_待归档" in _smart_any)

# =====================================================================
section("S8 · scope / project 过滤")
# 用只在「范围_项目记忆」里出现的词组，避免被 limit 截断或命中同标题条目
_s_p = server.memory_search("属于项目", scope="project")
check("S8a scope=project 命中项目记忆", "范围_项目记忆" in _s_p, f"result={_s_p[:160]}")
_s_pn = server.memory_search("属于项目", scope="temporary")
check("S8b scope=temporary 排除项目记忆", "范围_项目记忆" not in _s_pn,
      f"result={_s_pn[:160]}")
_s_proj = server.memory_search("属于项目", project="项目A")
check("S8c project=项目A 精确筛选", "范围_项目记忆" in _s_proj, f"result={_s_proj[:160]}")
_s_proj_bad = server.memory_search("属于项目", project="项目Z")
check("S8d project=项目Z 无命中（隔离生效）", "范围_项目记忆" not in _s_proj_bad,
      f"result={_s_proj_bad[:160]}")
_s_tmp = server.memory_search("临时信息", scope="temporary")
check("S8e scope=temporary 可筛选", "范围_临时记忆" in _s_tmp, f"result={_s_tmp[:160]}")
_l_proj = server.memory_list(limit=200, project="项目A")
check("S8f 列表按 project 过滤", "范围_项目记忆" in _l_proj)
_l_any2 = server.memory_list(limit=200, scope="any")
check("S8g scope=any 表示不过滤", "范围_临时记忆" in _l_any2)

# =====================================================================
section("S9 · 归档 / 恢复与生命周期联动")
server.memory_write("范围_归档循环", "循环测试正文 CIRCLE。", tags=["scope"],
                    scope="project", project="项目D")
server.memory_archive("范围_归档循环")
_arc_files = list((server.MEMORY_DIR / ".archive").glob("范围_归档循环*.md"))
check("S9a 归档副本已生成", len(_arc_files) == 1, f"files={[p.name for p in _arc_files]}")
if _arc_files:
    _am, _ = server._load_memory(_arc_files[0])
    check("S9b 归档副本 status=archived 且 tier=cold",
          _am.get("status") == "archived" and _am.get("tier") == "cold",
          f"{_am.get('status')}/{_am.get('tier')}")
    check("S9c 归档副本保留 scope/project",
          _am.get("scope") == "project" and _am.get("project") == "项目D")
_stub, _ = load_meta("范围_归档循环")
check("S9d stub 保留 scope/project（恢复后不丢上下文）",
      _stub.get("scope") == "project" and _stub.get("project") == "项目D")
_r9 = server.memory_restore("范围_归档循环")
check("S9e 恢复成功", "已恢复记忆" in _r9, f"result={_r9[:60]}")
_res, _ = load_meta("范围_归档循环")
check("S9f 恢复后 status=active", _res.get("status") == "active", f"status={_res.get('status')}")
check("S9g 恢复后 tier=warm 且无 archived_to",
      _res.get("tier") == "warm" and "archived_to" not in _res)

# =====================================================================
section("S10 · 统计与审计")
_stats = server.memory_stats()
check("S10a stats 含作用域分布", "作用域分布" in _stats and "project:" in _stats)
check("S10b stats 含生命周期分布", "生命周期分布" in _stats and "archived:" in _stats)
check("S10c stats 含写入方上下文分布", "写入方上下文分布" in _stats and "workbuddy" in _stats)
check("S10d stats 含结构版本分布", "frontmatter 结构版本" in _stats and "v2:" in _stats)
check("S10e stats 含项目分布", "项目A" in _stats)

server.memory_write("范围_审计对象", "审计测试。", scope="project")  # 故意缺 project
server.memory_write("范围_审计死引用", "审计测试。", supersedes="根本不存在的记忆")
_audit = server.memory_audit()
check("S10f audit 报告生命周期/冲突/作用域章节",
      "生命周期 / 冲突 / 作用域一致性" in _audit)
check("S10g audit 检出 scope=project 但缺 project",
      "范围_审计对象" in _audit and "缺少 project 标识" in _audit)
check("S10h audit 检出 supersedes 死引用",
      "范围_审计死引用" in _audit and "指向不存在的记忆" in _audit)

# =====================================================================
section("S11 · 向后兼容（旧笔记无新字段）")
_legacy = server.MEMORY_DIR / "范围_旧格式笔记.md"
_legacy.write_text("---\ntitle: 范围_旧格式笔记\ntags: [legacy]\ntier: warm\n---\n\n旧格式正文 LEGACY。",
                   encoding="utf-8")
_m11, _b11 = load_meta("范围_旧格式笔记")
check("S11a 旧笔记读取端注入 scope=global", _m11.get("scope") == "global")
check("S11b 旧笔记读取端注入 status=active", _m11.get("status") == "active")
check("S11c 旧笔记 schema_version 视为 1", server._schema_version(_m11) == 1,
      f"v={_m11.get('schema_version')}")
check("S11d 旧笔记仍可正常读取", "旧格式正文" in server.memory_read("范围_旧格式笔记", max_chars=0))
check("S11e 旧笔记可被默认检索到（status 非 archived）",
      "范围_旧格式笔记" in server.memory_search("LEGACY"))
check("S11f memory_read 输出包含 scope/status 字段",
      "scope=" in server.memory_read("范围_旧格式笔记", max_chars=0)
      and "status=" in server.memory_read("范围_旧格式笔记", max_chars=0))

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
