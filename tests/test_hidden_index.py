#!/usr/bin/env python3
"""生成物隐藏化回归测试 for ai-memory MCP server（B6, 2026-10-08）。

背景:
    vault 根把「人看的笔记」与「框架派生的生成物」混在同一层。`记忆索引` 是库里
    **唯一**的纯生成物（每次写入后被 `_refresh_index` 整体重建，正文无人工内容），
    故给它加 `.` 前缀 —— Obsidian 默认不显示点号开头的文件，人读区立刻干净。

本脚本的断言口径（都可证伪）:
    H1  改名后**旧名必须消失** —— 否则库里会有两个「记忆索引」（一个生成、一个
        残留），而 `_entry_matches` 按 title 命中哪一个是**不确定**的。
    H2  生成物**仍须被枚举**（os.walk 不跳点文件）。把它顺手排除出扫描是最容易
        犯的错：那时 `memory_read("记忆索引")`、graph、doctor 会一起失联，而
        索引本身看起来「一切正常」。
    H3  一次性迁移只删**确认是生成物**的那份（title + moc 标签双条件）。人工写的
        同名笔记必须留下 —— 那种删除不可逆。
    H4  `memory_rebuild_links` 的 `known` 必须收 `path.stem`：生成器渲染双链走
        `_wiki_link`（**按 Obsidian 口径用 stem 当 target**），改名后 stem 变成
        `.记忆索引`；只认 title 的判定会把新索引当场判成死链，并且凡 title≠stem
        的条目（实测 36 篇）也会被误报。
    H5  frontmatter 的 title **不随文件名改变**（仍是 `记忆索引`）—— 文件名与
        「逻辑标识」解耦，这正是改名不破坏检索的原因。

运行:
    python tests/test_hidden_index.py        # 退出码 0=全绿, 1=有失败
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
os.environ["AI_MEMORY_METRICS"] = "0"
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
HIDDEN = ROOT / ".记忆索引.md"
LEGACY = ROOT / "记忆索引.md"

# 与 `_refresh_index` 写出的生成物同构的 frontmatter（title + moc 标签）
GEN_FM = ("---\n"
          "title: 记忆索引\n"
          "tags: [index, moc, navigation]\n"
          "tier: warm\n"
          "source: ai-memory-server\n"
          "---\n\n# 记忆索引（自动维护）\n")


def links_in_index(text: str) -> list[str]:
    return server._extract_wiki_links(text)


def dead_lines() -> list[str]:
    report = server.memory_rebuild_links()
    return [l for l in report.splitlines() if l.startswith("- ") and "->" in l]


# =====================================================================
# H0 常量面
# =====================================================================
print("\n[H0] 生成物文件名")
check("H0a 生成物名带 `.` 前缀", server._INDEX_FILENAME.startswith("."),
      server._INDEX_FILENAME)
check("H0b 落盘路径就在 vault 根", server._index_path() == ROOT / server._INDEX_FILENAME,
      str(server._index_path()))
check("H0c 旧名常量只用于迁移识别", server._INDEX_LEGACY_FILENAME == "记忆索引.md")

# =====================================================================
# H1 第一次写入后：新名存在、旧名不存在
# =====================================================================
print("\n[H1] 改名后旧名必须消失")
server.memory_write("B6测试条目甲", "这是 B6 用例的正文甲。", tags=["测试"])
check("H1a 生成物落在 .记忆索引.md", HIDDEN.exists(), str(sorted(p.name for p in ROOT.glob("*.md"))))
check("H1b 旧名 记忆索引.md 不复存在", not LEGACY.exists(),
      str(sorted(p.name for p in ROOT.iterdir())))
check("H1c vault 根恰好只有一份索引", len(list(ROOT.glob("*记忆索引.md"))) == 1,
      str(sorted(p.name for p in ROOT.glob("*记忆索引.md"))))

# =====================================================================
# H2 生成物仍须被枚举（改名 ≠ 退出检索）
# =====================================================================
print("\n[H2] 改名后仍被扫描与检索")
names = [p.name for p in server._iter_md_files()]
check("H2a _iter_md_files 收录点文件", ".记忆索引.md" in names, str(names))
titles = [server._entry_title(m, p) for p, m, _b in server._iter_entries()]
check("H2b _iter_entries 里有 title=记忆索引 的条目", "记忆索引" in titles,
      str([t for t in titles if "索引" in t]))
check("H2c memory_read 按 title 能读到",
      "记忆索引（自动维护）" in server.memory_read("记忆索引"),
      server.memory_read("记忆索引")[:120])
check("H2d memory_read 按文件名 stem 也能读到（.记忆索引）",
      "未找到" not in server.memory_read(".记忆索引"),
      server.memory_read(".记忆索引")[:120])
check("H2e memory_search 能命中生成页",
      "记忆索引" in server.memory_search("自动维护"), server.memory_search("自动维护")[:200])

# =====================================================================
# H3 一次性迁移
# =====================================================================
print("\n[H3] 旧名生成物被迁移清理")
HIDDEN.unlink(missing_ok=True)
LEGACY.write_text(GEN_FM, encoding="utf-8")
check("H3a 前置：旧名生成物已就位", LEGACY.exists() and not HIDDEN.exists())

# 迁移必须**文件与索引行一起**清。只删文件不清行 → 库里留下一条指向不存在文件的
# 索引行（doctor 的「死索引」）。真实 vault 里旧名一定**有**索引行（历史全量重建
# 写入的），而本用例是新建空库、文件又是直接 write_text 落的盘（没走 _atomic_write_text
# 的 _idx_sync_path）⇒ 必须**强制全量重建**一次，才让「行存在」这个前提成立。
# 少了这一步，H3e 就成了恒绿的空断言。
server._IDX_NEEDS_REBUILD = True
server._ensure_index()


def _legacy_rows():
    with server.midx.connect(server._IDX_FILE) as conn:
        return conn.execute("SELECT relpath FROM entries WHERE relpath=?",
                            ("记忆索引.md",)).fetchall()


_rows_before = _legacy_rows()
check("H3b 前置：全量重建后旧名已入索引（否则 H3e 无意义）", len(_rows_before) == 1,
      str([dict(r) for r in _rows_before]))

server.memory_write("B6测试条目乙", "这是 B6 用例的正文乙。", tags=["测试"])
check("H3c 迁移后新名存在", HIDDEN.exists())
check("H3d 迁移后旧名被删除", not LEGACY.exists(),
      str(sorted(p.name for p in ROOT.glob("*记忆索引*"))))
_rows_after = _legacy_rows()
check("H3e 迁移同时清掉了旧名的索引行", not _rows_after,
      str([dict(r) for r in _rows_after]))

print("\n[H3d] 人工写的同名笔记**不得**被删除（不可逆，宁可保守）")
HIDDEN.unlink(missing_ok=True)
LEGACY.write_text("---\ntitle: 我这篇不是索引\ntags: [note]\n---\n\n手工笔记，别删我。\n",
                  encoding="utf-8")
server.memory_write("B6测试条目丙", "这是 B6 用例的正文丙。", tags=["测试"])
check("H3d-1 title 不同的同名文件被保留",
      LEGACY.exists() and "别删我" in LEGACY.read_text(encoding="utf-8"),
      str(sorted(p.name for p in ROOT.glob("*记忆索引*"))))
check("H3d-2 同时仍生成了新名索引", HIDDEN.exists())

# ⚠️ 一律用 missing_ok：若产品真的误删了，本行不该抛 FileNotFoundError 把整个脚本
# 中断（那样只会得到"崩了"，看不到后续断言到底是红是绿）。
LEGACY.unlink(missing_ok=True)
LEGACY.write_text("---\ntitle: 记忆索引\ntags: [note]\n---\n\n没有 moc 标签，视为人工。\n",
                  encoding="utf-8")
server.memory_write("B6测试条目丁", "这是 B6 用例的正文丁。", tags=["测试"])
check("H3d-3 title 相同但缺 moc 标签的文件也被保留（双条件才删）",
      LEGACY.exists(), str(sorted(p.name for p in ROOT.glob("*记忆索引*"))))
LEGACY.unlink(missing_ok=True)
server._idx_remove_path(LEGACY)
server._invalidate_cache()

# =====================================================================
# H4 死链判定必须认 stem（改名后索引自身不能变成死链）
# =====================================================================
print("\n[H4] memory_rebuild_links 的口径")
server._invalidate_cache()
server.memory_write("B6测试条目戊", "这是 B6 用例的正文戊。", tags=["测试"])
idx_links = links_in_index(HIDDEN.read_text(encoding="utf-8"))
check("H4a 索引里渲染的 target 用的是 stem（.记忆索引）",
      ".记忆索引" in idx_links, str([t for t in idx_links if "索引" in t]))
check("H4b 别名位保留人类可读 title",
      "[[.记忆索引|记忆索引]]" in HIDDEN.read_text(encoding="utf-8"),
      HIDDEN.read_text(encoding="utf-8")[:400])

dead = dead_lines()
check("H4c 索引自身不被报成死链",
      not any(".记忆索引" in l for l in dead), str([l for l in dead if "索引" in l]))

print("\n[H4d] title != stem 的条目也不得误报死链（重命名场景）")
# 标题含空格 ⇒ safe_filename 归一化成下划线 ⇒ stem != title，索引里渲染成
# `[[stem|title]]`。这正是历史上被误报 36 条的那类。
server.memory_write("B6 空格 标题 条目", "正文里有空格标题。", tags=["测试"])
idx_text = HIDDEN.read_text(encoding="utf-8")
check("H4d-1 别名形式出现在索引里", "|B6 空格 标题 条目]]" in idx_text,
      [l for l in idx_text.splitlines() if "空格" in l])
dead2 = dead_lines()
check("H4d-2 该条目的 stem 形态不算死链",
      not any("B6_空格_标题_条目" in l for l in dead2),
      str([l for l in dead2 if "空格" in l]))

# =====================================================================
# H5 文件名与逻辑标识解耦
# =====================================================================
print("\n[H5] frontmatter 的 title 不随文件名改变")
meta, _ = server._load_memory(HIDDEN)
check("H5a 生成物的 title 仍是「记忆索引」（不带点）",
      meta.get("title") == "记忆索引", repr(meta.get("title")))
check("H5b 而文件名 stem 是「.记忆索引」",
      HIDDEN.stem == ".记忆索引", repr(HIDDEN.stem))
check("H5c 按 title 写入命中的是生成物本身（不另建一份）",
      server._load_memory(HIDDEN)[0].get("title") == "记忆索引")

# =====================================================================
# H6 草稿提示与 doctor 一致性
# =====================================================================
print("\n[H6] 周边口径")
draft = server.memory_index_draft()
check("H6a index_draft 提示回写到新名", ".记忆索引.md" in draft, draft[:200])
check("H6b index_draft 不再提旧名",
      "回写到 `记忆索引.md`" not in draft, draft[:200])

doctor = server.memory_doctor()
# 这三条合起来钉住「迁移把文件删干净**且**索引行也一起清掉」：
# 只删文件不清行 ⇒ 死索引 1 条；只清行不删文件 ⇒ 未入索引 1 条。
check("H6c doctor：无死索引行（迁移未留残行）",
      "死索引（索引有行、磁盘无文件）— 0 条" in doctor,
      "\n".join(l for l in doctor.splitlines() if "死索引" in l or "未入索引" in l))
check("H6d doctor：无未入索引条目",
      "未入索引（磁盘有文件、索引无行）— 0 条" in doctor,
      "\n".join(l for l in doctor.splitlines() if "死索引" in l or "未入索引" in l))
check("H6e doctor：结论为索引与磁盘一致", "索引与磁盘一致" in doctor,
      "\n".join(l for l in doctor.splitlines() if "结论" in l))
n_hidden = sum(1 for p in server._iter_md_files() if p.name == ".记忆索引.md")
check("H6f 生成页恰好 1 份且被计入扫描", n_hidden == 1, f"n={n_hidden}")

# =====================================================================
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
