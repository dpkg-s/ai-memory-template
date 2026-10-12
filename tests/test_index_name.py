#!/usr/bin/env python3
"""索引文件命名回归测试 for ai-memory MCP server。

沿革（为什么有这篇）:
    B6（2026-10-08）把生成物 `记忆索引` 改成**隐藏名** `.记忆索引.md`，理由是「Obsidian
    默认不显示点文件 ⇒ vault 根的人读区干净」。
    v2.4.2（2026-10-12）**撤销**该取舍，恢复可见名 `记忆索引.md` —— 隐藏换来的整洁远小于
    代价（三条均为实测，不是推断）:
      * 全库 **25 篇**用 `[[记忆索引]]` 引用索引；改名后 stem 变成 `.记忆索引`，
        这 25 条链接**全部变死链**；
      * **Obsidian 忽略点文件** ⇒ 索引在人侧根本点不开，MOC 的导航价值归零；
      * 人机可达性分叉：AI 靠 legacy 识别仍能 `memory_read("记忆索引")`，人却看不到。

本脚本的断言口径（都可证伪）:
    H0  常量面：生成物是**可见**名，legacy 常量为 B6 的隐藏名。
    H1  写入后**新名存在、隐藏名不存在**，且 vault 根只有一份索引 —— 否则库里会有两个
        「记忆索引」（一个生成、一个残留），而 `_entry_matches` 按 title 命中哪一个是
        **不确定**的。
    H2  生成物**仍须被枚举**。把它顺手排除出扫描是最容易犯的错：那时
        `memory_read("记忆索引")`、graph、doctor 会一起失联，而索引看起来「一切正常」。
    H3  一次性迁移**反向**：隐藏名残留被清理，人工写的同名笔记必须留下 ——
        那种删除不可逆，宁可保守。
    H4  **可点击性（本次改动的核心收益）**：索引渲染的 target 就是 `记忆索引`
        ⇒ 全库 `[[记忆索引]]` 这种**裸链不再死**（B6 期间它们正是 25 条死链）。
        本条在 v2.4.1 上**必然为红**，是这次改动的定向护栏。
    H5  文件名与 title 一致（stem == title == `记忆索引`）—— 这正是 H4 成立的原因。
    H6  **摘要**：条目行追加 frontmatter 的 `summary`，且换行被压平、超长被截断
        ——否则一条摘要会把一行 MOC 劈成两行，破坏 `_extract_wiki_links` 的逐行解析前提。
    H7  草稿提示与 doctor 一致性。

运行:
    python tests/test_index_name.py        # 退出码 0=全绿, 1=有失败
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
_TMP = tempfile.mkdtemp(prefix="ai-memory-indexname-")
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
NEW = ROOT / "记忆索引.md"          # v2.4.2 起的可见名
LEGACY = ROOT / ".记忆索引.md"       # B6 期间的隐藏名

# 与 `_refresh_index` 写出的生成物同构的 frontmatter（title + moc 标签）
GEN_FM = ("---\n"
          "title: 记忆索引\n"
          "tags: [index, moc, navigation]\n"
          "tier: warm\n"
          "source: ai-memory-server\n"
          "---\n\n# 记忆索引（自动维护）\n")


def all_index_files() -> list[str]:
    """vault 根下所有以「记忆索引.md」结尾的文件 —— **可见与隐藏都收**。

    ⚠️ 不能用 `ROOT.glob("*记忆索引.md")`：glob 的 `*` **不匹配前导点**（fnmatch 口径），
    隐藏名残留会被静默漏掉，判据随即退化成恒绿的空断言。这正是 H1/H3 要防的东西。
    """
    return sorted(p.name for p in ROOT.iterdir() if p.name.endswith("记忆索引.md"))


def dead_lines() -> list[str]:
    """`memory_rebuild_links` 的死链清单行（形如 `- {title} -> [[{link}]]`）。"""
    report = server.memory_rebuild_links()
    return [l for l in report.splitlines() if l.startswith("- ") and "->" in l]


def index_text() -> str:
    """索引正文；**不存在时返回空串**。

    ⚠️ 不能直接 `NEW.read_text()`：本套件要能跑到旧版（v2.4.1 及更早）上做**证伪** ——
    那时可见名索引根本不存在，直接读会抛 FileNotFoundError 把脚本中断，后续断言**没机会
    报红**，红数被系统性低估（首轮实测：只报出 5 项就崩了）。返回空串则每条断言各自变红。
    """
    return NEW.read_text(encoding="utf-8") if NEW.exists() else ""


def index_meta() -> dict:
    """索引条目的 frontmatter；不存在时返回空 dict（理由同上）。"""
    if not NEW.exists():
        return {}
    try:
        return server._load_memory(NEW)[0] or {}
    except Exception:                                        # noqa: BLE001
        return {}


# =====================================================================
# H0 常量面
# =====================================================================
print("\n[H0] 生成物文件名")
check("H0a 生成物名是**可见**名（不以 `.` 开头）",
      server._INDEX_FILENAME == "记忆索引.md" and not server._INDEX_FILENAME.startswith("."),
      server._INDEX_FILENAME)
check("H0b 落盘路径就在 vault 根", server._index_path() == ROOT / server._INDEX_FILENAME,
      str(server._index_path()))
check("H0c legacy 常量 = B6 的隐藏名（只用于迁移识别）",
      server._INDEX_LEGACY_FILENAME == ".记忆索引.md", server._INDEX_LEGACY_FILENAME)

# =====================================================================
# H1 第一次写入后：新名存在、隐藏名不存在
# =====================================================================
print("\n[H1] 写入后旧（隐藏）名必须消失")
server.memory_write("索引名测试条目甲", "这是用例的正文甲。", tags=["测试"])
check("H1a 生成物落在可见名 记忆索引.md", NEW.exists(), str(all_index_files()))
check("H1b 隐藏名 .记忆索引.md 不复存在", not LEGACY.exists(), str(all_index_files()))
check("H1c vault 根恰好只有一份索引（可见/隐藏一起数）",
      len(all_index_files()) == 1, str(all_index_files()))

# =====================================================================
# H2 生成物仍须被枚举（改名 ≠ 退出检索）
# =====================================================================
print("\n[H2] 生成物仍被扫描与检索")
names = [p.name for p in server._iter_md_files()]
check("H2a _iter_md_files 收录可见名", "记忆索引.md" in names, str(names))
titles = [server._entry_title(m, p) for p, m, _b in server._iter_entries()]
check("H2b _iter_entries 里有 title=记忆索引 的条目", "记忆索引" in titles,
      str([t for t in titles if "索引" in t]))
check("H2c memory_read 按 title 能读到",
      "记忆索引（自动维护）" in server.memory_read("记忆索引"),
      server.memory_read("记忆索引")[:120])
_idx_meta = index_meta()
# ⚠️ 入参是 **title / stem**，不是文件名：`memory_read("记忆索引.md")` 会经
# `safe_filename` 变成 `记忆索引.md.md`，必然落空 —— 那是调用方写法错误，不是缺陷。
# 本条要钉的是「stem 与 title 已合一」这个新事实（B6 期间两者相差一个前导点）。
check("H2d stem 与 title 合一 ⇒ 两种入参等价",
      NEW.stem == "记忆索引" and server._entry_matches(_idx_meta, NEW, "记忆索引")
      and server._entry_matches(_idx_meta, NEW, NEW.stem),
      f"stem={NEW.stem!r}")
check("H2e memory_read 走的就是这条匹配链（title 形态可用）",
      server._entry_matches(_idx_meta, NEW, "记忆索引"),
      str(_idx_meta.get("permalink")))
check("H2f memory_search 能命中生成页",
      "记忆索引" in server.memory_search("自动维护"), server.memory_search("自动维护")[:200])

# =====================================================================
# H3 一次性迁移（反向）
# =====================================================================
print("\n[H3] 隐藏名残留被迁移清理")
NEW.unlink(missing_ok=True)
LEGACY.write_text(GEN_FM, encoding="utf-8")
check("H3a 前置：隐藏名生成物已就位", LEGACY.exists() and not NEW.exists(),
      str(all_index_files()))

# 迁移必须**文件与索引行一起**清。只删文件不清行 → 库里留下一条指向不存在文件的
# 索引行（doctor 的「死索引」）。真实 vault 里隐藏名一定**有**索引行（历史全量重建
# 写入的），而本用例是新建空库、文件又是直接 write_text 落的盘（没走 `_atomic_write_text`
# 的 `_idx_sync_path`）⇒ 必须**强制全量重建**一次，才让「行存在」这个前提成立。
# 少了这一步，H3e 就成了恒绿的空断言。
server._IDX_NEEDS_REBUILD = True
server._ensure_index()


def _legacy_rows():
    with server.midx.connect(server._IDX_FILE) as conn:
        return conn.execute("SELECT relpath FROM entries WHERE relpath=?",
                            (".记忆索引.md",)).fetchall()


_rows_before = _legacy_rows()
check("H3b 前置：全量重建后隐藏名已入索引（否则 H3e 无意义）", len(_rows_before) == 1,
      str([dict(r) for r in _rows_before]))

server.memory_write("索引名测试条目乙", "这是用例的正文乙。", tags=["测试"])
check("H3c 迁移后可见名存在", NEW.exists(), str(all_index_files()))
check("H3d 迁移后隐藏名被删除", not LEGACY.exists(), str(all_index_files()))
_rows_after = _legacy_rows()
check("H3e 迁移同时清掉了隐藏名的索引行", not _rows_after,
      str([dict(r) for r in _rows_after]))

print("\n[H3d] 人工写的同名文件**不得**被删除（不可逆，宁可保守）")
NEW.unlink(missing_ok=True)
LEGACY.write_text("---\ntitle: 我这篇不是索引\ntags: [note]\n---\n\n手工笔记，别删我。\n",
                  encoding="utf-8")
server.memory_write("索引名测试条目丙", "这是用例的正文丙。", tags=["测试"])
check("H3d-1 title 不同的隐藏同名文件被保留",
      LEGACY.exists() and "别删我" in LEGACY.read_text(encoding="utf-8"),
      str(all_index_files()))
check("H3d-2 同时仍生成了可见名索引", NEW.exists(), str(all_index_files()))

# ⚠️ 一律用 missing_ok：若产品真的误删了，本行不该抛 FileNotFoundError 把整个脚本
# 中断（那样只会得到"崩了"，看不到后续断言到底是红是绿）。
LEGACY.unlink(missing_ok=True)
LEGACY.write_text("---\ntitle: 记忆索引\ntags: [note]\n---\n\n没有 moc 标签，视为人工。\n",
                  encoding="utf-8")
server.memory_write("索引名测试条目丁", "这是用例的正文丁。", tags=["测试"])
check("H3d-3 title 相同但缺 moc 标签的文件也被保留（双条件才删）",
      LEGACY.exists(), str(all_index_files()))
LEGACY.unlink(missing_ok=True)
server._idx_remove_path(LEGACY)
server._invalidate_cache()

# =====================================================================
# H4 可点击性（本次改动的核心收益）
# =====================================================================
print("\n[H4] 可点击性：`[[记忆索引]]` 能按**文件名**解析（Obsidian 口径）")
# ⚠️ 判据必须落在 **stem** 上，这是本节最关键的一点，也是首版写错的地方：
# `memory_rebuild_links` **不会**把 B6 期间那 25 条链接报成死链 —— 它的 `known`
# 集合同时收 title，而索引的 frontmatter title 一直是「记忆索引」。真正的断链只
# 发生在 **Obsidian 侧**：它按**文件名 stem** 解析，B6 之后 stem 变成 `.记忆索引`，
# 于是人点不开、AI 却能读（人机可达性分叉）。首版判据用了 `memory_rebuild_links`，
# 结果在 v2.4.1 上**恒绿** —— 造了个证明不了任何东西的假判据。


def obsidian_resolves(target: str) -> bool:
    """Obsidian 按**文件名 stem** 解析双链 —— 这才是「点得开」的判据。"""
    return any(p.stem == target for p in server._iter_md_files())


server._invalidate_cache()
server.memory_write("索引名测试条目戊", "这是用例的正文戊。", tags=["测试"])
# 复刻全库那 25 篇的**原始写法**：裸链、不带别名
server.memory_write("索引名测试引用者",
                    "索引入口见 [[记忆索引]]，这是 B6 期间在 Obsidian 里点不开的写法。",
                    tags=["测试"])

idx_text = index_text()
idx_links = server._extract_wiki_links(idx_text)
check("H4a 索引自指链接的 target 就是文件名 stem（记忆索引）",
      "记忆索引" in idx_links, str([t for t in idx_links if "索引" in t]))
check("H4b stem == title ⇒ 渲染成**裸链** `[[记忆索引]]`（与全库写法同形）",
      "[[记忆索引]]" in idx_text,
      str([l for l in idx_text.splitlines() if "记忆索引]]" in l])[:300])
check("H4c 该 target 在 Obsidian 口径下有同名文件（可点击）",
      obsidian_resolves("记忆索引"),
      str(sorted(p.stem for p in server._iter_md_files() if "索引" in p.stem)))

_note_ref = (ROOT / "索引名测试引用者.md").read_text(encoding="utf-8")
check("H4d 引用方的裸链 target 同样可解析（本条在 v2.4.1 上必红）",
      "[[记忆索引]]" in _note_ref and obsidian_resolves("记忆索引"),
      f"note={'[[记忆索引]]' in _note_ref} resolves={obsidian_resolves('记忆索引')}")

print("\n[H4e] 次要口径：memory_rebuild_links 无死链")
# 该口径**原本就不会红**（它认 title），保留它是防「修好文件名却把链接渲染改坏」的回归。
dead = dead_lines()
check("H4e-1 索引自身不被报成死链",
      not any("[[记忆索引]]" in l for l in dead), str([l for l in dead if "索引" in l]))
check("H4e-2 引用索引的裸链在服务端口径下也无死链",
      not any(l.startswith("- 索引名测试引用者") and "[[记忆索引]]" in l for l in dead),
      str([l for l in dead if "引用者" in l]))

print("\n[H4f] title != stem 的条目也不得误报死链（重命名场景，仍需 stem 口径）")
# 标题含空格 ⇒ safe_filename 归一化成下划线 ⇒ stem != title，索引里渲染成 `[[stem|title]]`。
# 这正是历史上被误报 36 条的那类；改回可见名并没有让这条口径变得多余。
server.memory_write("索引名 空格 标题 条目", "正文里有空格标题。", tags=["测试"])
idx_text2 = index_text()
check("H4f-1 别名形式出现在索引里", "|索引名 空格 标题 条目]]" in idx_text2,
      [l for l in idx_text2.splitlines() if "空格" in l])
dead2 = dead_lines()
check("H4f-2 该条目的 stem 形态不算死链",
      not any("索引名_空格_标题_条目" in l for l in dead2),
      str([l for l in dead2 if "空格" in l]))

# =====================================================================
# H5 文件名与逻辑标识一致
# =====================================================================
print("\n[H5] 文件名与 title 一致")
meta = index_meta()
check("H5a 生成物的 title 是「记忆索引」",
      meta.get("title") == "记忆索引", repr(meta.get("title")))
check("H5b 文件名 stem 也是「记忆索引」（不再带点）",
      NEW.stem == "记忆索引", repr(NEW.stem))
check("H5c 按 title 写入命中的是生成物本身（不另建一份）",
      index_meta().get("title") == "记忆索引")
check("H5d 生成物的 tags 仍带 moc/index/navigation（迁移判据依赖它）",
      {"moc", "index", "navigation"} <= {str(t).lower() for t in server._entry_tags(meta)},
      str(server._entry_tags(meta)))

# =====================================================================
# H6 索引行摘要（v2.4.2 新增）
# =====================================================================
print("\n[H6] 条目行追加 summary 摘要")
# 口径：**存储保真、渲染压平** —— 多行 summary 必须完整落盘（yaml_quote_scalar 的
# 换行转义修复），而在索引里被压成一行。两者缺一：前者丢失内容（静默），后者把一行
# MOC 条目劈成两行、破坏 `_extract_wiki_links` 的逐行解析前提。
server.memory_write("索引名测试摘要条目", "正文里没有这句特征串。",
                    summary="第一行摘要\n第二行摘要尾巴", tags=["测试"])
_meta_sum, _ = server._load_memory(ROOT / "索引名测试摘要条目.md")
check("H6a 多行 summary **完整**落盘（不被截到第一行）",
      _meta_sum.get("summary") == "第一行摘要\n第二行摘要尾巴",
      repr(_meta_sum.get("summary")))

idx3 = index_text()
_sumline = [l for l in idx3.splitlines() if l.startswith("- ") and "索引名测试摘要条目" in l]
check("H6b 摘要条目在索引中有且仅有一行", len(_sumline) == 1, str(_sumline))
check("H6c 换行在索引里被压平到同一行",
      bool(_sumline) and "— 第一行摘要 第二行摘要尾巴" in _sumline[0], str(_sumline))
check("H6d 换行未被写进索引（不会把一行劈成两行）",
      not any(l.strip() == "第二行摘要尾巴" for l in idx3.splitlines()),
      str([l for l in idx3.splitlines() if "第二行摘要" in l]))

server.memory_write("索引名测试长摘要条目", "正文。",
                    summary="长" * 100, tags=["测试"])
idx4 = index_text()
_longline = [l for l in idx4.splitlines() if l.startswith("- ") and "索引名测试长摘要条目" in l]
check("H6e 超长摘要被截断为 60 字符 + 省略号",
      bool(_longline) and ("— " + "长" * 60 + "…") in _longline[0],
      str(_longline)[:200])

# `memory_write` 未显式给 summary 时会**从正文自动派生**（content 前 100 字），
# 故「没有摘要的条目」在正常写入路径下并不存在 —— 这里钉住该事实，
# 免得后人以为索引里的破折号尾巴是「只在显式传 summary 时才出现」。
server.memory_write("索引名测试自动摘要条目", "自动摘要来自正文前 100 字。", tags=["测试"])
idx5 = index_text()
_autoline = [l for l in idx5.splitlines() if l.startswith("- ") and "索引名测试自动摘要条目" in l]
check("H6f 未显式给 summary 时，正文自动摘要也进索引",
      bool(_autoline) and "自动摘要来自正文前 100 字" in _autoline[0], str(_autoline))

print("\n[H6g] _index_summary 单元口径")
check("H6g-1 无 summary ⇒ 空串", server._index_summary({}) == "", repr(server._index_summary({})))
check("H6g-2 非字符串 ⇒ 空串（不抛 TypeError）",
      server._index_summary({"summary": 123}) == "",
      repr(server._index_summary({"summary": 123})))
check("H6g-3 压平空白（制表符/多空格/换行一并归一）",
      server._index_summary({"summary": "a\tb   c\nd"}) == "a b c d",
      repr(server._index_summary({"summary": "a\tb   c\nd"})))
check("H6g-4 恰好 60 字符不截断",
      server._index_summary({"summary": "x" * 60}) == "x" * 60)
check("H6g-5 61 字符 ⇒ 截 60 + 省略号",
      server._index_summary({"summary": "x" * 61}) == "x" * 60 + "…",
      repr(server._index_summary({"summary": "x" * 61})))

# =====================================================================
# H7 草稿提示与 doctor 一致性
# =====================================================================
print("\n[H7] 草稿提示与 doctor")
draft = server.memory_index_draft()
check("H7a index_draft 提示回写到可见名", "回写到 `记忆索引.md`" in draft, draft[:200])
check("H7b index_draft 不再提隐藏名", "回写到 `.记忆索引.md`" not in draft, draft[:200])

doctor = server.memory_doctor()
# 这三条合起来钉住「迁移把文件删干净**且**索引行也一起清掉」：
# 只删文件不清行 ⇒ 死索引 1 条；只清行不删文件 ⇒ 未入索引 1 条。
check("H7c doctor：无死索引行（迁移未留残行）",
      "死索引（索引有行、磁盘无文件）— 0 条" in doctor,
      "\n".join(l for l in doctor.splitlines() if "死索引" in l or "未入索引" in l))
check("H7d doctor：无未入索引条目",
      "未入索引（磁盘有文件、索引无行）— 0 条" in doctor,
      "\n".join(l for l in doctor.splitlines() if "死索引" in l or "未入索引" in l))
check("H7e doctor：结论为索引与磁盘一致", "索引与磁盘一致" in doctor,
      "\n".join(l for l in doctor.splitlines() if "结论" in l))
n_index = sum(1 for p in server._iter_md_files() if p.name == "记忆索引.md")
check("H7f 生成页恰好 1 份且被计入扫描", n_index == 1, f"n={n_index}")

# =====================================================================
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
