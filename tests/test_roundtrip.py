#!/usr/bin/env python3
"""round-trip / 一致性回归测试 for ai-memory MCP server (server.py).

用途:
    每次改动 server.py 后运行一次, 确保 frontmatter 写→读无损、
    反斜杠不雪崩、read 不污染 updated 等关键语义不被破坏。
    库本身是主人的真实记忆, 不能拿真库做实验——本脚本把
    AI_MEMORY_DIR 指向临时目录, 绝不触碰 D:/ai记忆。

运行:
    python tests/test_roundtrip.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import re
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
import memory_index as midx  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def load_meta(title: str) -> dict:
    """按标题找到磁盘文件并解析 frontmatter(真实读盘, 不走缓存/不走 read 截断)。"""
    for f in list(server.MEMORY_DIR.glob("*.md")):
        try:
            meta, _ = server._load_memory(f)
        except Exception:
            continue
        if server._entry_title(meta, f) == title or f.stem == title:
            return meta
    return {}


def section(name: str) -> None:
    print(f"\n== {name} ==")


# =====================================================================
section("A. 基础 round-trip: 中文标题/正文/多行")
title_a = "测试_回归基础"
content_a = "这是正文第一行, 讲 python 脚本。\n\n第二段: 含 [[双链]] 与 `代码` 与 ```代码块```。"
server.memory_write(title_a, content_a)
meta_a = load_meta(title_a)
check("A1 文件已建且 title 正确", meta_a.get("title") == title_a)
check("A2 created/updated 为 ISO", bool(re.match(r"\d{4}-\d{2}-\d{2}T", str(meta_a.get("created", "")))),
      f"created={meta_a.get('created')}")
raw_a = server.memory_read(title_a, max_chars=0)
check("A3 正文读回无损", content_a in raw_a)
check("A4 tags 自动建议非空(python 关键词)", "python" in meta_a.get("tags", []),
      f"tags={meta_a.get('tags')}")

# =====================================================================
section("B. 反斜杠雪崩回归: Windows 路径 summary 连续 5 轮 update 不翻倍")
WIN_PATH = r"C:\tmp\ai-memory-backup-20260907\sub\dir"  # 单反斜杠
title_b = "测试_反斜杠路径"
for i in range(5):
    server.memory_write(title_b, f"第 {i+1} 轮正文 body-{i}", summary=WIN_PATH)
    m = load_meta(title_b)
    if m.get("summary") != WIN_PATH:
        check(f"B 第{i+1}轮 summary 未雪崩", False,
              f"期望单反斜杠, 实际: {m.get('summary')!r}")
        break
else:
    check("B 连续 5 轮 update 后 summary 仍为单反斜杠", True)
check("B2 version 递增到 5", load_meta(title_b).get("version") == 5,
      f"version={load_meta(title_b).get('version')}")

# =====================================================================
section("C. tags 特殊字符 round-trip")
special_tags = ['project', '工具:测试', '含"双引号"', "a,b", "尾随空格 "]
server.memory_write("测试_特殊标签", "tags 特殊字符正文", tags=special_tags, summary="s")
back_tags = load_meta("测试_特殊标签").get("tags", [])
check("C1 tags 元素无损", sorted(str(x).strip() for x in back_tags) == sorted(x.strip() for x in special_tags),
      f"写={special_tags} 读={back_tags}")

# =====================================================================
section("D. frontmatter dump→parse 幂等 3 轮")
meta_d = {
    "title": "幂等测试",
    "tags": ["python", '带"引号"', r"含\反斜杠", "a,逗号"],
    "summary": r"D:\一些\Windows\路径 与 \"引号\"",
    "tier": "warm",
    "access_count": 7,
    "source": "workbuddy-netbook",
    "created": "2026-09-07T02:00:00.000000+00:00",
    "updated": "2026-09-07T02:00:00.000000+00:00",
    "version": 3,
    "flag": True,
    "nothing": None,
}
cur = dict(meta_d)
ok_idem = True
for rnd in range(3):
    fm = server._dump_yaml_frontmatter(cur)
    cur = server._parse_yaml_frontmatter(fm)
    if not (cur.get("summary") == meta_d["summary"] and cur.get("tags") == meta_d["tags"]):
        ok_idem = False
        break
check("D 3 轮 dump→parse 后 summary/tags 稳定", ok_idem,
      f"末轮 summary={cur.get('summary')!r} tags={cur.get('tags')!r}")
check("D2 int/bool 类型保持", cur.get("version") == 3 and cur.get("flag") is True,
      f"version={cur.get('version')!r} flag={cur.get('flag')!r}")

# =====================================================================
section("E. 空标题/空正文拒绝")
r_e1 = server.memory_write("   ", "正文")
r_e2 = server.memory_write("测试_空正文", "   ")
check("E1 空标题被拒", "标题为空" in r_e1, r_e1)
check("E2 空正文被拒(不建空壳)", "内容为空" in r_e2, r_e2)
check("E3 空正文确实未落盘", load_meta("测试_空正文") == {})

# =====================================================================
section("F. P0-1: read 不污染 updated, 只累加 access_count")
title_f = "测试_updated不被read污染"
server.memory_write(title_f, "P0-1 正文")
meta_before = load_meta(title_f)
upd_before = meta_before.get("updated")
count_before = meta_before.get("access_count", 0)
for _ in range(12):  # 超过 flush 阈值 10, 触发自动 flush
    server.memory_read(title_f, max_chars=0)
server._flush_access_counts()  # 显式兜底一次
meta_after = load_meta(title_f)
check("F1 updated 未被读取操作改写", meta_after.get("updated") == upd_before,
      f"before={upd_before} after={meta_after.get('updated')}")
check("F2 access_count 已累加 12", meta_after.get("access_count", 0) == count_before + 12,
      f"before={count_before} after={meta_after.get('access_count')}")

# =====================================================================
section("G. memory_read 截断语义")
big = "长" * 12_000
server.memory_write("测试_超长正文", big)
r_g1 = server.memory_read("测试_超长正文")  # 默认 8000
r_g2 = server.memory_read("测试_超长正文", max_chars=0)
check("G1 默认截断", "已截断" in r_g1 and len(r_g1) < 9000)
check("G2 max_chars=0 全文", "长" * 12_000 in r_g2)

# =====================================================================
section("H. 文件名安全: 非法字符标题")
title_h = 'a/b:c*d?e"f<g>h|i j'
server.memory_write(title_h, "文件名安全正文")
check("H1 含非法字符标题可写可读", server.memory_read(title_h, max_chars=0).startswith("["),
      "memory_read 未命中")
safe_name = server._safe_filename(title_h)
check("H2 安全文件名不含非法字符", not re.search(r'[\\/:*?"<>|]', safe_name), safe_name)

# =====================================================================
section("I. wiki 链接提取(单元)")
links = server._extract_wiki_links("看 [[笔记A]] 和 [[笔记B|别名]] 重复 [[笔记A]]")
check("I1 提取去重 + 别名剥离", sorted(links) == ["笔记A", "笔记B"], str(links))

# =====================================================================
section("J. SQLite 索引层")
# J1: 写入后索引行存在且 fresh
server.memory_write("测试_索引命中", "索引正文", tags=["mcp"])
server._ensure_index()
with midx.connect(server._IDX_FILE) as conn:
    rows = midx.find(conn, "测试_索引命中")
    check("J1 写入后索引可定位", len(rows) >= 1 and rows[0]["relpath"].endswith("测试_索引命中.md"),
          f"rows={[r['relpath'] for r in rows]}")
    check("J1b 索引行 fresh", all(midx.fresh(conn, server.MEMORY_DIR, r) for r in rows))

# J2: memory_read 走索引路径命中(不依赖全扫) — 通过 read 返回验证
r_j2 = server.memory_read("测试_索引命中", max_chars=0)
check("J2 索引路径 read 命中", "索引正文" in r_j2)

# J3: 索引 miss(title 不存在) 正确返回未找到
r_j3 = server.memory_read("测试_绝不存在的标题_xyz", max_chars=0)
check("J3 索引 miss 返回未找到", "未找到" in r_j3)

# J4: 归档后 根 stub 优先于 .archive 副本 (同 title 两行)
server.memory_write("测试_索引归档", "归档前正文")
server.memory_archive("测试_索引归档")
with midx.connect(server._IDX_FILE) as conn:
    rows = midx.find(conn, "测试_索引归档")
    rels = [r["relpath"] for r in rows]
check("J4 根 stub 与 archive 副本并存", any(r.startswith(".archive/") for r in rels)
      and any(not r.startswith(".archive/") for r in rels), f"rels={rels}")
r_j4 = server.memory_read("测试_索引归档", max_chars=0)
check("J4b read 优先返回根 stub(归档提示)", "已归档" in r_j4)

# J5: 外部直接改文件(绕 server)后, mtime 变化 -> 索引 stale -> read 回退全扫自愈
p_j5 = server.MEMORY_DIR / "测试_索引命中.md"
orig = p_j5.read_text(encoding="utf-8")
new_body = "索引正文-外部修改版"
import re as _re
new_text = _re.sub(r"(?<=---\n\n).*", new_body, orig, count=1, flags=_re.DOTALL)
p_j5.write_text(new_text, encoding="utf-8")
# 强制改 mtime 使索引 stale
import os as _os
_os.utime(p_j5, None)
r_j5 = server.memory_read("测试_索引命中", max_chars=0)
check("J5 外部改动后 read 读到新正文(回退自愈)", new_body in r_j5, r_j5[:120])
# 自愈后索引行应已刷新
server._ensure_index()
with midx.connect(server._IDX_FILE) as conn:
    fresh_j5 = any(midx.fresh(conn, server.MEMORY_DIR, r) for r in midx.find(conn, "测试_索引命中"))
check("J5b 回退后索引已刷新", fresh_j5)

# J6: memory_delete 清理索引行
server.memory_delete("测试_索引命中", trash=False, purge=True)
with midx.connect(server._IDX_FILE) as conn:
    rows = midx.find(conn, "测试_索引命中")
check("J6 delete 后索引行清除", len(rows) == 0, f"rows={[r['relpath'] for r in rows]}")

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
