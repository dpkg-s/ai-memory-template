#!/usr/bin/env python3
"""标题路径（阶段二）测试 —— memory_write 支持写入分类子目录。

背景：2026-09-19 把 178 篇笔记从 vault 根搬进 23 个分类目录，但**写入端没跟上**：
`text_utils.safe_filename()` 把标题里的 `/` 拍成 `_`，于是新笔记一律落回根目录，
扁平格局会重新长回来。阶段二（2026-09-20）放行 `/` 作为子目录分隔符。

本测试守六类风险：

    A. `safe_filename()` 单元语义 —— 无 `/` 的标题必须与旧实现**逐字节一致**
       （否则等于悄悄改了全库文件名规则）
    B. 落盘落点 —— 目录自动创建、正文可读、索引同步
    C. **幂等** —— 同标题重复写入必须更新同一份，绝不产生「子目录 + 根」两份
    D. 政策拒绝 —— 保留目录 / `..` / 盘符 / 绝对路径 / 过深层级
    E. **向后兼容** —— 阶段二前 `/` 被拍成 `_` 的历史条目仍能被命中更新
    F. 重名后缀留在**同一目录**（写错会直接制造重复，是本次最容易回归的一处）

脚本把 AI_MEMORY_DIR 指向临时目录，绝不触碰真实记忆库。

运行:
    python tests/test_title_path.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑（server.py import mcp）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-titlepath-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_FSYNC"] = "0"          # 纯逻辑测试，不需要每次落盘同步
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402
from text_utils import legacy_flat_filename, safe_filename  # noqa: E402

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


def _old_safe_filename(title: str) -> str:
    """阶段二之前的口径（照抄旧实现）：用于等价性回归。"""
    import re
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("._")
    safe = safe[:80] or "memory"
    return f"{safe}.md"


def _exists(rel: str) -> bool:
    return (_ROOT / rel).exists()


def main() -> int:
    # =================================================================
    # A. safe_filename 单元语义
    print("\n[A] safe_filename 单元语义")
    plain = [
        "2026-07-06 记忆库半自动审计",
        "AI交互配置",
        "标题含  多空格",
        ".hidden",
        "尾部点.",
        "带*星号?和:冒号",
        "x" * 200,
        "",
    ]
    diffs = [t for t in plain if safe_filename(t) != _old_safe_filename(t)]
    check("A1 无 '/' 标题与旧实现逐字节一致", not diffs, f"不一致={diffs}")
    check("A2 '/' 作为子目录分隔符",
          safe_filename("项目/记忆库MCP/条目") == "项目/记忆库MCP/条目.md",
          safe_filename("项目/记忆库MCP/条目"))
    check("A3 重复 '/' 折叠",
          safe_filename("项目//双斜杠///条目") == "项目/双斜杠/条目.md",
          safe_filename("项目//双斜杠///条目"))
    check("A4 '..' 段被消解（不产生穿越）",
          ".." not in safe_filename("a/../../etc/passwd"),
          safe_filename("a/../../etc/passwd"))
    check("A5 盘符被消解（冒号→下划线，不可能出现 C:）",
          safe_filename("C:/Windows/system32") == "C/Windows/system32.md",
          safe_filename("C:/Windows/system32"))
    check("A6 空段/尾部斜杠退化为根目录条目",
          safe_filename("尾部斜杠/") == "尾部斜杠.md", safe_filename("尾部斜杠/"))
    check("A7 全空标题退化", safe_filename("") == "memory.md", safe_filename(""))
    check("A8 逐段截断上限仍为 80",
          len(safe_filename("目录/" + "y" * 200).split("/")[-1]) == 83,   # 80 + '.md'
          safe_filename("目录/" + "y" * 200))
    check("A9 legacy_flat_filename 与旧实现一致",
          all(legacy_flat_filename(t) == _old_safe_filename(t) for t in plain + ["a/ b", "x/y/z"]),
          legacy_flat_filename("a/ b"))

    # =================================================================
    # B. 落盘落点
    print("\n[B] 落盘到子目录")
    msg = server.memory_write("测试项目/子目录A/第一条", "正文内容 B1", tags=["tp"])
    check("B1 写入返回成功", msg.startswith("已创建记忆"), msg)
    check("B2 自动创建父目录并落盘", _exists("测试项目/子目录A/第一条.md"),
          str(sorted(str(p.relative_to(_ROOT)) for p in _ROOT.rglob("*.md"))))
    check("B3 回显给出相对路径（看得见落点）",
          "测试项目/子目录A/第一条.md" in msg, msg)
    body = server.memory_read("测试项目/子目录A/第一条")
    check("B4 memory_read 能按标题读到", "正文内容 B1" in body, body[:200])
    check("B5 _iter_md_files 递归可见",
          any(p.name == "第一条.md" for p in server._iter_md_files()),
          str([p.name for p in server._iter_md_files()]))
    found = server.memory_search("正文内容 B1")
    check("B6 memory_search 能检索到子目录条目", "第一条" in found, found[:200])
    idx = (_ROOT / ".记忆索引.md").read_text(encoding="utf-8")   # B6：生成物带 `.` 前缀
    check("B7 索引收录了该条目", "第一条" in idx, idx[:200])

    # =================================================================
    # C. 幂等（同标题重复写入不得产生第二份）
    print("\n[C] 幂等")
    server.memory_write("测试项目/子目录A/第一条", "正文内容 B1 改")
    hits = [p for p in _ROOT.rglob("第一条*.md")]
    check("C1 同标题重复写入仍只有一份", len(hits) == 1, str(hits))
    check("C2 且仍在原父目录内", hits and hits[0].parent.name == "子目录A", str(hits))
    check("C3 根目录没有孳生副本", not _exists("第一条.md") and not _exists("第一条_2.md"),
          str(sorted(str(p.relative_to(_ROOT)) for p in _ROOT.rglob("*.md"))))
    check("C4 正文确实被更新",
          "正文内容 B1 改" in server.memory_read("测试项目/子目录A/第一条"))

    # =================================================================
    # D. 政策拒绝
    print("\n[D] 政策拒绝")
    rejects = [
        ("D1 保留目录 .archive", ".archive/偷偷归档"),
        ("D2 保留目录 .trash", ".trash/偷偷删除"),
        ("D3 保留目录 ai-memory", "ai-memory/代码副本"),
        ("D4 保留目录 .git", ".git/config"),
        ("D5 父级穿越", "../逃逸"),
        ("D6 路径段 '..'", "项目/../逃逸"),
        ("D7 绝对路径", "/绝对路径"),
        ("D8 盘符", "C:/Windows/system32"),
        ("D9 层级过深", "a/b/c/d/e/条目"),
    ]
    for name, title in rejects:
        r = server.memory_write(title, "不应被写入")
        check(f"{name} 被拒绝并说明原因", r.startswith("错误"), r)

    leaked = [p for p in _ROOT.rglob("*.md")
              if p.name in {"偷偷归档.md", "偷偷删除.md", "代码副本.md", "config.md",
                            "逃逸.md", "system32.md", "条目.md"}]
    check("D10 被拒标题未在磁盘留下任何文件", not leaked, str(leaked))
    check("D11 vault 之外未被动过", not Path(_TMP).parent.joinpath("逃逸.md").exists())
    check("D12 同名普通目录 archive 仍可用（只是前导点被消毒吃掉）",
          server.memory_write("myarchive/正常条目", "x").startswith("已创建")
          and _exists("myarchive/正常条目.md"),
          str(sorted(str(p.relative_to(_ROOT)) for p in _ROOT.rglob("*.md"))))

    # =================================================================
    # E. 向后兼容：阶段二前 '/' 被拍成 '_' 的历史条目
    print("\n[E] 向后兼容（历史扁平条目）")
    # E-A：**frontmatter 里没有 title** 的历史文件 —— 此时 _entry_title 只能退回
    # stem，`f.stem == title` 也不成立，**只有 legacy_stem 那条兜底能命中**。
    # 这是真正会退化出重复的场景，必须单独守。
    ta = "轻装时代(QZSD)灯架/三脚架产品谱系"
    flat_a = legacy_flat_filename(ta)
    (_ROOT / flat_a).write_text("---\ntags: []\nversion: 1\n---\n\n历史正文 A\n", encoding="utf-8")
    # 先证明「这条命中只可能来自 legacy_stem 兜底」：
    #   * frontmatter 无 title ⇒ _entry_title 只能退回 stem
    #   * stem(…灯架_三脚架产品谱系) ≠ 标题(…灯架/三脚架产品谱系)
    #   ⇒ 唯一可能的匹配路径就是 legacy_stem。缺了它，E1 必然变成 CREATE。
    check("E0 该场景的命中只可能来自旧口径兜底",
          Path(flat_a).stem != ta and "title:" not in (_ROOT / flat_a).read_text(encoding="utf-8"),
          f"stem={Path(flat_a).stem!r} vs title={ta!r}")
    server._invalidate_cache()
    r = server.memory_write(ta, "历史正文 A 已更新")
    check("E1 无 title 字段的历史文件也被命中（走旧口径 stem）", r.startswith("已更新记忆"), r)
    check("E2 更新写入原扁平文件",
          "历史正文 A 已更新" in (_ROOT / flat_a).read_text(encoding="utf-8"))
    check("E3 未创建同名子目录", not (_ROOT / "轻装时代(QZSD)灯架").exists(),
          str(sorted(str(p.relative_to(_ROOT)) for p in _ROOT.rglob("*.md"))))

    # E-B：frontmatter 带 title 的历史文件 —— 走正常的标题匹配
    tb = "旧式标题/带frontmatter"
    flat_b = legacy_flat_filename(tb)
    (_ROOT / flat_b).write_text(f"---\ntitle: {tb}\ntags: []\nversion: 1\n---\n\n历史正文 B\n",
                                encoding="utf-8")
    server._invalidate_cache()
    r = server.memory_write(tb, "历史正文 B 已更新")
    check("E4 带 title 的历史文件命中并原地更新",
          r.startswith("已更新记忆") and "历史正文 B 已更新" in (_ROOT / flat_b).read_text(encoding="utf-8"), r)
    check("E5 两份历史标题合计仍只有 2 个文件",
          len([p for p in _ROOT.rglob("*.md") if p.stem.startswith("轻装时代") or p.stem.startswith("旧式标题")]) == 2,
          str([p.name for p in _ROOT.rglob("*.md")]))

    # =================================================================
    # F. 重名后缀必须留在同一目录
    print("\n[F] 重名后缀落在同一目录")
    (_ROOT / "测试项目" / "子目录A").mkdir(parents=True, exist_ok=True)
    (_ROOT / "测试项目" / "子目录A" / "占位.md").write_text(
        "---\ntitle: 别人的标题\ntags: []\nversion: 1\n---\n\n占位\n", encoding="utf-8")
    server._invalidate_cache()
    r = server.memory_write("测试项目/子目录A/占位", "新条目")
    check("F1 同名不同标题时追加后缀", r.startswith("已创建记忆"), r)
    check("F2 后缀文件在**子目录**内",
          _exists("测试项目/子目录A/占位_2.md"),
          str(sorted(str(p.relative_to(_ROOT)) for p in _ROOT.rglob("*.md"))))
    check("F3 根目录未被写成 占位_2.md", not _exists("占位_2.md"))
    check("F4 原占位文件未被覆盖",
          "占位" in (_ROOT / "测试项目" / "子目录A" / "占位.md").read_text(encoding="utf-8"))

    # =================================================================
    # 汇总
    # ⚠️ 措辞刻意避开裸 "FAIL" 字样：run_all_tests.py 会 grep 各套件输出统计断言数，
    # 若这里打印 "FAIL=0" 会被它当成一条失败断言重复计入。
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
