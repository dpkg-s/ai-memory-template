#!/usr/bin/env python3
"""目录分层（多级分类）支持测试。

背景：记忆库原先把 193 篇笔记**全部平铺**在 vault 根目录，分类只体现在
文件名前缀（`项目_xxx.md`）。2026-09-19 起改为「目录分层 + 文件名前缀并存」，
文件名 stem 完全不变（⇒ 全库 1096 条 `[[链接]]` 零断链）。

这要求所有「读取端枚举」从 `MEMORY_DIR.glob("*.md")`（非递归）改为递归。
本测试专门守这条改造，覆盖四类风险：

    A. `_iter_md_files()` 单元语义（排序 / 剪枝 / 起点不自排除）
    B. 递归可见性 —— 子目录条目能否被 search / list / read 看到
    C. **缓存失效** —— 子目录内文件增删改后，工具是否立即感知
       （⚠️ 这是唯一会「静默返回陈旧数据、无任何报错」的失败模式）
    D. 保留目录剪枝 —— .archive/.trash/.git/.obsidian/ai-memory 不得被收录
    E. 写入 / 归档 / 回收站链路对子目录条目的处理
    F. 零断链 —— 双链按 stem 解析，与所在目录无关

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_folders.py        # 退出码 0=全绿, 1=有失败
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
_TMP = tempfile.mkdtemp(prefix="ai-memory-folders-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402

_ROOT = Path(_TMP)
_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def _fresh() -> None:
    """清空沙箱并让缓存失效(模拟外部改动后的下一次调用)。"""
    for child in _ROOT.iterdir():
        if child.name == ".memory.lock":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    server._invalidate_cache()


def _put(rel: str, title: str, body: str = "正文。") -> Path:
    """在沙箱内任意相对路径写一条带 frontmatter 的记忆。"""
    p = _ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntitle: {title}\ntags: [folder-test]\ntier: warm\n"
        f"source: test\nupdated: \"2026-09-19T00:00:00+00:00\"\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def main() -> int:
    # ============ A. _iter_md_files 单元语义 ============
    print("== A. _iter_md_files() 单元语义 ==")
    _fresh()
    _put("根条目.md", "根条目")
    _put("项目/子条目.md", "子条目")
    _put("项目/子项目/深条目.md", "深条目")
    _put(".archive/归档条目.md", "归档条目")
    _put(".trash/回收条目.md", "回收条目")
    _put(".git/不该出现.md", "不该出现git")
    _put(".obsidian/不该出现2.md", "不该出现obsidian")
    _put("ai-memory/README.md", "代码副本README")

    files = server._iter_md_files()
    rels = {f.relative_to(_ROOT).as_posix() for f in files}
    check("A1 根目录条目被收录", "根条目.md" in rels, str(sorted(rels)))
    check("A2 一级子目录条目被收录", "项目/子条目.md" in rels, str(sorted(rels)))
    check("A3 二级子目录条目被收录", "项目/子项目/深条目.md" in rels, str(sorted(rels)))
    check("A4 .archive 被剪枝", not any(r.startswith(".archive/") for r in rels), str(sorted(rels)))
    check("A5 .trash 被剪枝", not any(r.startswith(".trash/") for r in rels), str(sorted(rels)))
    check("A6 .git 被剪枝", not any(r.startswith(".git/") for r in rels), str(sorted(rels)))
    check("A7 .obsidian 被剪枝", not any(r.startswith(".obsidian/") for r in rels), str(sorted(rels)))
    check("A8 ai-memory/ 代码副本被剪枝", not any(r.startswith("ai-memory/") for r in rels),
          str(sorted(rels)))
    check("A9 根目录条目排在子目录条目之前",
          [f.name for f in files][0] == "根条目.md", str([f.name for f in files]))

    arc = server._iter_md_files(_ROOT / ".archive")
    check("A10 起点为 .archive 时其内部条目被收录（起点不自排除）",
          any(f.name == "归档条目.md" for f in arc), str([f.name for f in arc]))
    check("A11 起点不存在时返回空列表",
          server._iter_md_files(_ROOT / "不存在目录") == [])

    # ============ B. 递归可见性 ============
    print("\n== B. 子目录条目的检索可见性 ==")
    server._invalidate_cache()
    s = server.memory_search("子条目")
    check("B1 子目录条目可被 search 命中", "[子条目]" in s, s[:200])
    check("B2 子目录条目可被 list 列出", "子条目" in server.memory_list(limit=200))
    r = server.memory_read("子条目")
    check("B3 子目录条目可被 read 读取", "正文" in r, r[:200])
    check("B4 深层子目录条目同样可见", "深条目" in server.memory_list(limit=200))
    st = server.memory_stats()
    check("B5 stats 的条目总数把子目录算进去", "总条目数: 3" in st or "总条目数: 4" in st,
          [l for l in st.splitlines() if "总条目数" in l][:1].__str__())

    # ============ C. 缓存失效（最关键：失败时静默返陈旧） ============
    print("\n== C. 缓存失效（子目录变更必须被立即感知） ==")
    server._invalidate_cache()
    server.memory_list(limit=200)              # 先填充缓存
    _put("新增目录/事后新增.md", "事后新增")     # 直接落盘，不走 memory_write
    o = server.memory_list(limit=200)
    check("C1 子目录内新建 .md 后 list 立即可见", "事后新增" in o, o[:200])
    check("C2 新条目可被 search 命中", "[事后新增]" in server.memory_search("事后新增"))

    p = _ROOT / "项目" / "子条目.md"
    t = p.read_text(encoding="utf-8").replace("正文。", "正文已被外部改写。")
    p.write_text(t, encoding="utf-8")
    check("C3 子目录内改内容后 read 立即反映",
          "已被外部改写" in server.memory_read("子条目"), server.memory_read("子条目")[:200])

    p.unlink()
    check("C4 子目录内删除后 list 立即消失",
          "子条目" not in server.memory_list(limit=200))
    check("C5 删除后 search 也不应命中",
          "[子条目]" not in server.memory_search("子条目"))

    # ============ D. 写入匹配 ============
    print("\n== D. memory_write 对子目录条目的匹配 ==")
    _fresh()
    p_sub = _put("项目/待更新条目.md", "待更新条目", "第一版内容。")
    server._invalidate_cache()
    out = server.memory_write("待更新条目", "第二版内容。")
    check("D1 返回提到更新而非新建", "已更新" in out, out)
    check("D2 子目录里那份被就地更新（未在根目录新建）",
          "第二版内容" in p_sub.read_text(encoding="utf-8"), out)
    check("D3 根目录未出现同名文件",
          not (_ROOT / "待更新条目.md").exists())
    server._invalidate_cache()
    out2 = server.memory_write("全新建条目", "全新内容。")
    check("D4 新条目仍落在根目录（保持既有行为）",
          (_ROOT / "全新建条目.md").exists() and "已创建" in out2, out2)

    # ============ E. 归档 / 回收站链路 ============
    print("\n== E. 归档与回收站链路 ==")
    _fresh()
    _put("项目/待归档条目.md", "待归档条目", "归档前正文内容。")
    server._invalidate_cache()
    o = server.memory_archive("待归档条目")
    check("E1 归档成功", "已归档" in o, o)
    arc_files = list((_ROOT / ".archive").glob("*.md"))
    check("E2 正文进了 .archive", any(f.name == "待归档条目.md" for f in arc_files),
          str([f.name for f in arc_files]))
    check("E3 stub 留在原子目录（不搬回根）",
          (_ROOT / "项目" / "待归档条目.md").exists(),
          str([str(p.relative_to(_ROOT)) for p in server._iter_md_files()]))
    check("E4 归档条目退出日常检索",
          "[待归档条目]" not in server.memory_search("待归档条目"))

    server._invalidate_cache()
    _put("项目/待删除条目.md", "待删除条目", "软删除前正文。")
    server._invalidate_cache()
    server.memory_delete("待删除条目")
    check("E5 子目录条目可被软删除（进 .trash）",
          any(f.name == "待删除条目.md" for f in (_ROOT / ".trash").glob("*.md")),
          str([f.name for f in (_ROOT / ".trash").glob("*.md")]))
    check("E6 删除后退出检索",
          "[待删除条目]" not in server.memory_search("待删除条目"))

    # ============ F. 保留目录内容绝不进入检索 ============
    print("\n== F. 保留目录内容不进检索 ==")
    _fresh()
    _put("ai-memory/README.md", "代码副本README", "这是代码副本里的 README。")
    _put(".git/说明.md", "git说明")
    _put(".obsidian/配置.md", "obsidian配置")
    _put("正常条目.md", "正常条目")
    server._invalidate_cache()
    o = server.memory_list(limit=200)
    check("F1 ai-memory/ 下的 .md 不出现在 list", "代码副本README" not in o, o[:200])
    check("F2 .git 下的 .md 不出现在 list", "git说明" not in o, o[:200])
    check("F3 .obsidian 下的 .md 不出现在 list", "obsidian配置" not in o, o[:200])
    check("F4 正常条目不受影响", "正常条目" in o, o[:200])
    check("F5 search 也搜不到保留目录内容",
          "[代码副本README]" not in server.memory_search("代码副本README"))

    # ============ G. 零断链（双链与路径解耦） ============
    print("\n== G. 零断链：双链按 stem 解析 ==")
    _fresh()
    _put("项目/被链接条目.md", "被链接条目", "我是被链接的目标。")
    _put("其他/引用者.md", "引用者", "参见 [[被链接条目]] 与 [[项目/被链接条目]]。")
    server._invalidate_cache()
    r = server.memory_read("引用者")
    check("G1 跨目录的 [[stem]] 链接可读", "参见" in r, r[:200])
    o = server.memory_orphans()
    check("G2 被链接条目不被判为孤立（反链已建立）", "被链接条目" not in o, o[:300])
    g = server.memory_graph("被链接条目")
    check("G3 图谱能跨目录呈现反链", "引用者" in g, g[:300])

    # ============ H. 一致性工具不误报 ============
    print("\n== H. doctor / audit 对子目录的处理 ==")
    _fresh()
    _put("项目/索引内条目.md", "索引内条目")
    _put("其他/索引内条目2.md", "索引内条目2")
    server._invalidate_cache()
    # doctor 刻意不做惰性重建（见其 docstring），故先显式让索引与磁盘对齐 ——
    # 这一步同时验证「索引重建会递归收录子目录条目」（计划书验收 A6 索引自愈）。
    server._rebuild_index()
    d = server.memory_doctor()
    check("H1 doctor 不把子目录条目误报为未入索引",
          "未入索引（磁盘有文件、索引无行）— 0 条" in d,
          "\n".join([l for l in d.splitlines() if "未入索引" in l]))
    check("H2 doctor 的磁盘侧把子目录条目计入「分类目录」",
          "分类目录 2" in d,
          "\n".join([l for l in d.splitlines() if "磁盘文件" in l]))
    check("H3 doctor 结论为一致",
          "索引与磁盘一致" in d,
          "\n".join([l for l in d.splitlines() if "结论" in l]))
    a = server.memory_audit()
    check("H4 audit 可正常执行", "审计" in a or "#" in a, a[:160])

    # ============ 汇总 ============
    # ⚠️ 措辞刻意避开裸 "FAIL" 字样：run_all_tests.py 会 grep 各套件输出统计
    # 断言数，若这里打印 "FAIL=0" 会被它当成一条失败断言重复计入。
    print("\n== 汇总 ==")
    print(f"通过 {len(_passed)} / 失败 {len(_failed)}")
    if _failed:
        for n in _failed:
            print("  - " + n)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
