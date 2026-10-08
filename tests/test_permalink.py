#!/usr/bin/env python3
"""permalink 稳定标识回归测试 for ai-memory MCP server（A3, 2026-10-08）。

背景:
    此前条目的**唯一身份是 title / 文件名 stem** —— 文件一旦被改名（手工重命名、
    搬迁、外部工具同步），所有指向它的引用就全部失联，且报错形态是「查不到」而非
    报错，属于静默失效。

    A3 引入 `permalink`：**首次写入时冻结为当时的文件名 stem，此后永不覆盖**，
    并把查找链升级为 `permalink → title → stem` 三级。于是改名之后：
      * 用旧标识仍能 memory_read 到；
      * 用旧标识仍能 memory_write 更新（而不是另建一份）；
      * 指向旧标识的双链不再被 memory_rebuild_links 误报为死链。

本脚本的断言口径:
    核心判据是 **「文件被改名后，旧标识仍可命中」** —— 该场景在旧实现下必然失败
    （旧实现只认 title/stem），因此这组断言能证伪，不是恒绿。

运行:
    python tests/test_permalink.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
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


ROOT = Path(_TMP)


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def fm_of(name: str) -> str:
    """取文件的 frontmatter 块（第一个 --- 与第二个 --- 之间）。"""
    txt = read(name)
    parts = txt.split("---")
    return parts[1] if len(parts) >= 3 else ""


def permalink_of(name: str) -> str:
    for line in fm_of(name).splitlines():
        if line.startswith("permalink:"):
            return line.split(":", 1)[1].strip()
    return ""


# =====================================================================
# P1: 新建条目写出 permalink，且紧跟 title
print("\n[P1] permalink 字段生成")
server.memory_write("旧名", "改名前写下的正文。")
fm = fm_of("旧名.md")
check("P1a 新建条目带 permalink", permalink_of("旧名.md") == "旧名", repr(fm[:200]))
lines = [l for l in fm.splitlines() if l.strip()]
check("P1b permalink 紧跟 title（字段顺序）",
      len(lines) >= 2 and lines[0].startswith("title:") and lines[1].startswith("permalink:"),
      repr(lines[:3]))

# =====================================================================
# P2: 再次写入不改变 permalink（永不覆盖）
print("\n[P2] 永不覆盖")
server.memory_write("旧名", "更新后的正文。")
check("P2a 更新后 permalink 不变", permalink_of("旧名.md") == "旧名", permalink_of("旧名.md"))
check("P2b 正文确实被更新", "更新后的正文" in read("旧名.md"))

# =====================================================================
# P3: 核心 —— 文件被改名后，旧标识仍可命中
print("\n[P3] 改名后旧标识仍可定位（核心）")
# 另造一条引用「旧名」的笔记，用于验证死链判定
server.memory_write("引用者", "这里引用了 [[旧名]] 这个条目。")
# 模拟外部改名：重命名文件 + 同步改 frontmatter title（permalink 原样保留）
old_p, new_p = ROOT / "旧名.md", ROOT / "新名.md"
renamed = read("旧名.md").replace("title: 旧名", "title: 新名")
old_p.unlink()
new_p.write_text(renamed, encoding="utf-8")

check("P3a 改名后 permalink 仍是冻结值", permalink_of("新名.md") == "旧名", permalink_of("新名.md"))
out_old = server.memory_read("旧名")
check("P3b 用旧标识仍能读到（经 permalink）",
      "更新后的正文" in out_old and "未找到" not in out_old, out_old[:120])
out_new = server.memory_read("新名")
check("P3c 用新标题也能读到（title 匹配）", "更新后的正文" in out_new, out_new[:120])
check("P3d 用新文件名 stem 也能读到", "更新后的正文" in server.memory_read("新名"), "")

# =====================================================================
# P4: 指向旧标识的双链不再被判为死链
print("\n[P4] 死链判定纳入 permalink")
report = server.memory_rebuild_links()
dead = [l for l in report.splitlines() if "旧名" in l and "->" in l]
check("P4a 指向旧标识的链接不算死链", not dead, str(dead[:3]))
check("P4b 报告可正常生成", "死链数" in report, report[:160])

# =====================================================================
# P5: upsert 判据纳入 permalink —— 用旧标识写入应更新而非新建
print("\n[P5] 用旧标识写入命中同一条目")
before = len(sorted(ROOT.rglob("*.md")))
server.memory_write("旧名", "经旧标识再次更新的正文。")
after = len(sorted(ROOT.rglob("*.md")))
check("P5a 未新建文件（仍更新原条目）", after == before, f"{before} -> {after}")
check("P5b 内容写进了改名后的那份",
      "经旧标识再次更新" in read("新名.md"), read("新名.md")[-160:])
check("P5c permalink 仍为冻结值", permalink_of("新名.md") == "旧名", permalink_of("新名.md"))

# =====================================================================
# P6: 索引层 —— 列存在 + 值与查询口径
print("\n[P6] 索引层 permalink")
with midx.connect(server._IDX_FILE) as conn:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
    check("P6a 索引表含 permalink 列", "permalink" in cols, str(sorted(cols)))
    # ⚠️ 用 relpath 定位而非 title：P5 已用旧标识写入，把该文件的 frontmatter
    # title 重新写成了「旧名」——按 title 查会因断言间的顺序耦合而假失败。
    rows = conn.execute("SELECT permalink, title FROM entries WHERE relpath=?",
                        ("新名.md",)).fetchall()
    check("P6b 索引行写入 permalink", len(rows) == 1 and rows[0]["permalink"] == "旧名",
          str([dict(r) for r in rows]))
    hit = midx.find(conn, "旧名")
    check("P6c midx.find 按 permalink 命中", len(hit) >= 1, str(len(hit)))

# =====================================================================
# P7: 旧 schema 的索引库被幂等补列（不重建、不报错）
print("\n[P7] 旧索引库补列迁移")
LEGACY = """
CREATE TABLE entries (
    relpath TEXT PRIMARY KEY, title TEXT NOT NULL, stem TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]', tier TEXT NOT NULL DEFAULT 'warm',
    created TEXT, updated TEXT, source TEXT, version INTEGER,
    access_count INTEGER NOT NULL DEFAULT 0, summary TEXT,
    size INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0
);
"""
legacy_db = ROOT / "legacy_probe.db"
c = sqlite3.connect(str(legacy_db))
c.executescript(LEGACY)
c.commit()
c.close()
conn = midx.connect(legacy_db)
lcols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
check("P7a 旧库 connect 后补上 permalink 列", "permalink" in lcols, str(sorted(lcols)))
conn.execute("INSERT INTO entries(relpath,title,stem,permalink) VALUES('z.md','Z','Z','Z-old')")
conn.commit()
check("P7b 补列后可写可查（依 permalink）", len(midx.find(conn, "Z-old")) == 1)
midx.close(conn)

# =====================================================================
# P8: 存量条目（无 permalink）零回归 + 渐进补齐
print("\n[P8] 存量条目兼容与渐进补齐")
legacy_md = ROOT / "存量条目.md"
legacy_md.write_text("---\ntitle: 存量条目\n---\n\n历史正文，没有 permalink。\n", encoding="utf-8")
check("P8a 无 permalink 时按 title 仍可读",
      "历史正文" in server.memory_read("存量条目"), server.memory_read("存量条目")[:100])
check("P8b 无 permalink 时按 stem 仍可读",
      "历史正文" in server.memory_read("存量条目"), "")
check("P8c 该条目此刻确实没有 permalink", permalink_of("存量条目.md") == "", repr(permalink_of("存量条目.md")))
server.memory_write("存量条目", "被写入后应当补齐 permalink。")
check("P8d 被写入后渐进补齐 permalink",
      permalink_of("存量条目.md") == "存量条目", permalink_of("存量条目.md"))

# =====================================================================
# P9: 边界 —— 空 key 不匹配；null/非法类型不崩
print("\n[P9] 边界")
check("P9a 空 key 一律不匹配", server._entry_matches({"permalink": "x"}, ROOT / "x.md", "") is False)
check("P9b permalink 为 None 时不崩且回退 title",
      server._entry_matches({"title": "T", "permalink": None}, ROOT / "T.md", "T") is True)
check("P9c 非字符串 permalink 不参与匹配（防类型污染）",
      server._entry_matches({"title": "T", "permalink": 123}, ROOT / "T.md", "123") is False)

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
