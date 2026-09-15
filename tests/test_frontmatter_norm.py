#!/usr/bin/env python3
"""frontmatter 归一化测试（2026-09-15 双 frontmatter 缺陷修复回归）。

背景：调用方常把「带 frontmatter 的完整笔记」直接当 content 传入 memory_write，
写入端会再生成权威 frontmatter 拼接，两者叠加产出「双 frontmatter」——
Obsidian 只认第一块，第二块退化为正文垃圾。更隐蔽的是**自动摘要取自未剥离的
原始 content**，把那段 frontmatter 文本固化进 summary 字段，污染检索结果与索引
快照。全库排查出 8 篇此型损坏（详见记忆库修复笔记）。

修复（两处，纵深两道）：
    1. yaml_io.strip_leading_frontmatter() —— 纯函数，剥掉开头多余的 frontmatter 块
    2. server.memory_write() 入口归一化（早于空壳校验与摘要/标签派生）
       + _write_memory() 收口再剥一次（覆盖非 memory_write 的写盘路径）

覆盖：
    A. 新增：content 自带单块 frontmatter → 落盘仅 1 块、正文干净
    B. 新增：content 自带连续多块 → 全部剥掉
    C. 对照组：正常 content 逐字保留
    D. 误伤防护：正文开头 `---` + 非 key 文本不剥
    E. 更新既有条目时传自带 frontmatter 的 content → 仍 1 块（历史重灾区）
    F. 权威性：服务器生成的 frontmatter 为准（传入块的 title/tags 不夺权）
    G. 幂等：连写两次仍 1 块且正文不堆叠
    H. 自愈：磁盘上已被写坏（双块）的条目，再次写入后恢复正常
    I. 纯函数边界：空串 / 无 frontmatter / 空块 / BOM
    J. 正文中部 `---` 分隔线不受影响
    K. 摘要污染回归：summary 不得含传入块的 frontmatter 文本

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_frontmatter_norm.py     # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ai-memory-fmnorm-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402
from yaml_io import strip_leading_frontmatter  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def fm_blocks(text: str) -> int:
    """统计文件开头的连续 frontmatter 块数量。"""
    lines = text.replace("\r\n", "\n").split("\n")
    n, i = 0, 0
    while i < len(lines) and lines[i].strip() == "---":
        j = i + 1
        end = None
        while j < len(lines):
            if lines[j].strip() == "---":
                end = j
                break
            j += 1
        if end is None:
            break
        n += 1
        i = end + 1
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        if i < len(lines) and lines[i].strip() != "---":
            break
    return n


def read(title: str) -> str:
    return (Path(_TMP) / f"{title}.md").read_text(encoding="utf-8")


def fm_of(title: str) -> str:
    """取第一个 frontmatter 块（不含 --- 分隔符）。"""
    t = read(title).replace("\r\n", "\n")
    parts = t.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def body_of(title: str) -> str:
    """取正文（第一块 frontmatter 之后的内容）。"""
    t = read(title).replace("\r\n", "\n")
    parts = t.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else t


def main() -> int:
    print("== A. 新增：content 自带单块 frontmatter ==")
    hand = ("---\ntitle: Norm_A\ntags: [exp]\ncreated: \"2026-09-15T01:40:00.000000+00:00\"\n"
            "source: workbuddy-netbook\nsummary: \"手写 frontmatter\"\ntier: warm\n---\n\n"
            "# Norm_A\n\n正文甲。\n")
    server.memory_write("Norm_A", hand, tags=["exp"], source="workbuddy-netbook")
    check("A1 落盘仅 1 个 frontmatter 块", fm_blocks(read("Norm_A")) == 1, f"实际 {fm_blocks(read('Norm_A'))} 块")
    check("A2 正文不含传入块字段（tier/summary）",
          "tier: warm" not in body_of("Norm_A") and "手写 frontmatter" not in body_of("Norm_A"),
          repr(body_of("Norm_A")[:160]))
    check("A3 正文内容保留", body_of("Norm_A").startswith("# Norm_A") and "正文甲。" in body_of("Norm_A"),
          repr(body_of("Norm_A")[:120]))
    check("A4 结构正常（frontmatter 后无残留 ---）", "\n---\n\n# Norm_A" in read("Norm_A"), repr(read("Norm_A")[:120]))

    print("\n== B. 新增：content 自带连续多块 ==")
    two = ("---\ntitle: Norm_B\nsummary: \"第一块\"\n---\n\n"
           "---\ntitle: Norm_B\ntags: [a]\n---\n\n# Norm_B\n\n正文乙。\n")
    server.memory_write("Norm_B", two, tags=["exp"])
    check("B1 多块全部剥掉，仅 1 块", fm_blocks(read("Norm_B")) == 1, f"实际 {fm_blocks(read('Norm_B'))} 块")
    check("B2 第一块标记未进正文", "第一块" not in body_of("Norm_B"), repr(body_of("Norm_B")[:160]))
    check("B3 第二块标记未进正文", "tags: [a]" not in body_of("Norm_B"), repr(body_of("Norm_B")[:160]))
    check("B4 正文乙保留", body_of("Norm_B").rstrip().endswith("正文乙。"), repr(body_of("Norm_B")[-60:]))

    print("\n== C. 对照组：正常 content 逐字保留 ==")
    body = "# Norm_C\n\n正常正文，含 [[双链]] 与 `代码`。\n\n- 列表项\n"
    server.memory_write("Norm_C", body, tags=["exp"])
    check("C1 仅 1 块", fm_blocks(read("Norm_C")) == 1, f"实际 {fm_blocks(read('Norm_C'))} 块")
    check("C2 正文逐字保留", body.rstrip() in read("Norm_C"), repr(body_of("Norm_C")[-120:]))

    print("\n== D. 误伤防护：正文开头 --- + 非 key 文本 ==")
    hr_body = "---\n\n上面是水平线，这行不是 frontmatter。\n"
    server.memory_write("Norm_D", hr_body, tags=["exp"])
    check("D1 未把水平线当 frontmatter 剥掉", "上面是水平线" in read("Norm_D"), repr(read("Norm_D")[-120:]))
    check("D2 文件本身仍只有 1 个规范块", fm_blocks(read("Norm_D")) == 1, f"实际 {fm_blocks(read('Norm_D'))} 块")
    check("D3 纯函数层同样不误剥", strip_leading_frontmatter(hr_body).startswith("---"), "")

    print("\n== E. 更新既有条目时传自带 frontmatter 的 content ==")
    server.memory_write("Norm_E", "# Norm_E\n\n初版正文。\n", tags=["exp"])
    server.memory_write("Norm_E", "---\ntitle: Norm_E\nsummary: \"旧式块\"\n---\n\n# Norm_E\n\n改版正文。\n")
    check("E1 更新路径仍仅 1 块", fm_blocks(read("Norm_E")) == 1, f"实际 {fm_blocks(read('Norm_E'))} 块")
    check("E2 正文已更新", "改版正文。" in body_of("Norm_E"), repr(body_of("Norm_E")[:120]))
    check("E3 旧正文不残留", "初版正文。" not in read("Norm_E"), repr(read("Norm_E")[-160:]))
    check("E4 传入块标记不残留于正文", "旧式块" not in body_of("Norm_E"), repr(body_of("Norm_E")[:160]))
    check("E5 版本号正常自增", "version: 2" in fm_of("Norm_E"), repr(fm_of("Norm_E")[:200]))

    print("\n== F. 权威性：服务器生成的 frontmatter 为准 ==")
    server.memory_write("Norm_F",
                        "---\ntitle: 伪造标题\ntags: [伪造]\nsummary: \"伪造摘要\"\n---\n\n正文。\n",
                        tags=["真实标签"], source="src-x")
    fm = fm_of("Norm_F")
    check("F1 标题以参数为准，未被传入块夺权", "title: Norm_F" in fm and "伪造标题" not in fm, repr(fm[:200]))
    check("F2 参数 tags 生效，传入 tags 未夺权", "真实标签" in fm and "伪造" not in fm, repr(fm[:220]))
    check("F3 落盘仅 1 块", fm_blocks(read("Norm_F")) == 1, f"实际 {fm_blocks(read('Norm_F'))} 块")
    check("F4 正文只剩实质内容", body_of("Norm_F").strip() == "正文。", repr(body_of("Norm_F")[:80]))

    print("\n== G. 幂等：连写两次 ==")
    fm_body = "---\ntitle: Norm_G\nsummary: \"重复传入\"\n---\n\n# Norm_G\n\n幂等正文。\n"
    server.memory_write("Norm_G", fm_body)
    server.memory_write("Norm_G", fm_body)
    check("G1 两次写后仍 1 块", fm_blocks(read("Norm_G")) == 1, f"实际 {fm_blocks(read('Norm_G'))} 块")
    check("G2 正文不重复堆叠", body_of("Norm_G").count("幂等正文。") == 1, f"正文出现 {body_of('Norm_G').count('幂等正文。')} 次")

    print("\n== H. 自愈：磁盘上已被写坏（双块）的条目 ==")
    p = Path(_TMP) / "Norm_H.md"
    p.write_text("---\ntitle: Norm_H\ntier: warm\n---\n\n---\ntitle: Norm_H\ntier: warm\n---\n\n# Norm_H\n\n坏掉的正文。\n",
                 encoding="utf-8")
    check("H0 构造出双块文件", fm_blocks(p.read_text(encoding="utf-8")) == 2, "")
    server.memory_write("Norm_H", p.read_text(encoding="utf-8"))
    check("H1 再写一次即自愈为 1 块", fm_blocks(read("Norm_H")) == 1, f"实际 {fm_blocks(read('Norm_H'))} 块")
    check("H2 正文未丢", "坏掉的正文。" in body_of("Norm_H"), repr(body_of("Norm_H")[:120]))

    print("\n== I. 纯函数边界 ==")
    check("I1 空串返回空串", strip_leading_frontmatter("") == "", repr(strip_leading_frontmatter("")))
    plain = "# 无 frontmatter\n\n正文。\n"
    check("I2 无 frontmatter 原样返回", strip_leading_frontmatter(plain) == plain, repr(strip_leading_frontmatter(plain)))
    check("I3 保留 BOM 剥离能力", strip_leading_frontmatter("\ufeff" + plain) == plain, "")
    check("I4 纯 frontmatter 剥完为空", strip_leading_frontmatter("---\ntitle: X\n---\n") == "",
          repr(strip_leading_frontmatter("---\ntitle: X\n---\n")))
    check("I5 空块（无 key）不剥", strip_leading_frontmatter("---\n---\n正文") == "---\n---\n正文", "")
    check("I6 仅 frontmatter 的输入被空壳校验拦下",
          "内容为空" in server.memory_write("Norm_I", "---\ntitle: Norm_I\n---\n"),
          server.memory_write("Norm_I", "---\ntitle: Norm_I\n---\n")[:80])

    print("\n== J. 正文中部 --- 分隔线不受影响 ==")
    mid = "# Norm_J\n\n上半段。\n\n---\n\n下半段。\n"
    server.memory_write("Norm_J", mid)
    check("J1 中部 --- 保留", "\n---\n\n下半段。" in read("Norm_J"), repr(read("Norm_J")[-140:]))
    check("J2 仍仅 1 个 frontmatter 块", fm_blocks(read("Norm_J")) == 1, f"实际 {fm_blocks(read('Norm_J'))} 块")

    print("\n== K. 摘要污染回归（summary 不得含传入块文本）==")
    for t in ("Norm_A", "Norm_B", "Norm_E", "Norm_F", "Norm_G", "Norm_H"):
        f = fm_of(t)
        line = [l for l in f.splitlines() if l.startswith("summary:")]
        s = line[0] if line else ""
        check(f"K-{t} 摘要不含 frontmatter 残留",
              "---" not in s.replace('summary: "', "", 1) and "title:" not in s,
              repr(s[:160]))

    print("\n" + "=" * 54)
    print(f"==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    if _failed:
        print("失败项:")
        for f in _failed:
            print("  -", f)
        return 1
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
