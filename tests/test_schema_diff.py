#!/usr/bin/env python3
"""frontmatter 结构差异视图回归测试 for ai-memory MCP server（B4, 2026-10-08）。

背景:
    库里有 SCHEMA_VERSION 与写入归一化，却**没有结构差异视图** —— 存量 v1 条目的
    迁移只能靠人肉翻，无法回答「到底差哪些键、有多少条、在哪一行」。

    B4 新增只读工具 `memory_schema_diff`：把「写入端承诺写出什么」与「磁盘上实际
    有什么」对账，输出核心键缺失 / v2 条目缺 v2 键 / v1 存量清单 / 未知键 / 可选键
    使用率 / 无 frontmatter 文件六类。

本脚本的断言口径:
    核心判据是 **分类必须正确且互不串味** —— 每类各造**恰好 1 条**命中，因此
    「某类多了/少了/串了」都会立刻反映成计数不符。另钉两条最易写错的口径：
      ① **「无 frontmatter」不等于「核心键缺失」**（混在 Vault 里的普通 Markdown
         不受结构契约约束，不能被算成缺陷）；
      ② 归档条目（.archive/）必须一并扫描 —— 它是「要迁移的存量」的大头。
    ⇒ 这两条在错误实现下必然变红，不是恒绿判据。

运行:
    python tests/test_schema_diff.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。
"""
from __future__ import annotations

import hashlib
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

# =====================================================================
# 造数据：每个分类恰好 1 条命中
# =====================================================================
CORE = ("title: {t}\n"
        "tags: []\n"
        "created: 2026-01-01T00:00:00+00:00\n"
        "updated: 2026-01-01T00:00:00+00:00\n"
        "tier: warm\n"
        "access_count: 0\n")
V2 = ("type: fact\n"
      "confidence: medium\n"
      "verified: false\n"
      "scope: global\n"
      "status: active\n"
      "schema_version: 2\n")


def write(rel: str, frontmatter: str | None, body: str = "正文。\n") -> None:
    """写一个条目；frontmatter 为 None 时写**不带** frontmatter 的普通 Markdown。"""
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    text = body if frontmatter is None else f"---\n{frontmatter}---\n\n{body}"
    p.write_text(text, encoding="utf-8")


# 根目录 6 个
write("合规.md", CORE.format(t="合规") + V2)                          # 完全合规
write("v1条目.md", CORE.format(t="v1条目"))                            # v1：缺全部 v2 键
write("缺核心键.md", CORE.format(t="缺核心键").replace("tier: warm\n", "")
      .replace("access_count: 0\n", "") + V2)                          # v2 但缺 core
write("v2缺键.md", CORE.format(t="v2缺键") + V2.replace("scope: global\n", "")
      .replace("status: active\n", ""))                                # v2 但缺 v2 键
write("未知键.md", "title: 未知键\naliases: [旧名]\nauthor: bob\n"
      + CORE.format(t="").replace("title: \n", "") + V2)               # 带未知键
write("无frontmatter.md", None)                                        # 普通 Markdown
# 分类子目录 1 个
write("项目/子目录条目.md", CORE.format(t="子目录条目") + V2)
# 归档 1 个
write(".archive/归档条目.md", CORE.format(t="归档条目") + V2)

EXPECT_FILES = 8
EXPECT_ROOT, EXPECT_CAT, EXPECT_ARCH = 6, 1, 1

# =====================================================================
# 辅助：按小节号取正文
# =====================================================================
def section(text: str, num: int) -> list[str]:
    """取小节正文；**首元素是该节标题行**（计数断言依赖它）。"""
    out, inside = [], False
    for ln in text.splitlines():
        if ln.startswith(f"## {num}."):
            inside = True
            out.append(ln)          # 标题行一并返回，便于断言「— N 条」
            continue
        if inside and ln.startswith("## "):
            break
        if inside:
            out.append(ln)
    return out


def head(text: str) -> list[str]:
    return text.splitlines()[:8]


def run(limit: int = 50) -> str:
    return server.memory_schema_diff(limit=limit)


# =====================================================================
print("\n[S1] 扫描范围与区域分布")
out = run()
h = head(out)
check("S1a 扫描文件总数正确",
      any(f"扫描文件: {EXPECT_FILES}" in ln for ln in h), repr(h[:3]))
check("S1b 区域分布正确（根/分类/归档）",
      any(f"根 {EXPECT_ROOT}" in ln and f"分类 {EXPECT_CAT}" in ln
          and f"归档 {EXPECT_ARCH}" in ln for ln in h), repr(h[:3]))

print("\n[S2] frontmatter 有无分离")
check("S2a 带 frontmatter 条目数正确",
      any("带 frontmatter 的条目: 7" in ln for ln in h), repr(h[:4]))
check("S2b 无 frontmatter 文件数正确",
      any("无 frontmatter: 1" in ln for ln in h), repr(h[:4]))

print("\n[S3] 结构版本分组")
check("S3a 版本分布 v2=6 · v1=1",
      any("v2 = 6" in ln and "v1 = 1" in ln for ln in h), repr(h[:5]))

print("\n[S4] 核心键缺失")
s1 = section(out, 1)
s1_body = "\n".join(s1)
check("S4a 恰 1 条命中", "— 1 条" in s1[0], repr(s1[:2]))
check("S4b 命中缺核心键.md", "缺核心键.md" in s1_body, s1_body[:300])
check("S4c 指出缺 tier 与 access_count",
      "tier" in s1_body and "access_count" in s1_body, s1_body[:300])
check("S4d **不含**无 frontmatter 的普通 Markdown",
      "无frontmatter.md" not in s1_body, s1_body[:300])
check("S4e 不含合规条目", "合规.md" not in s1_body, s1_body[:300])

print("\n[S5] v2 条目缺 v2 必需键")
s2 = section(out, 2)
s2_body = "\n".join(s2)
check("S5a 恰 1 条命中", "— 1 条" in s2[0], repr(s2[:2]))
check("S5b 命中 v2缺键.md 且指出 scope/status",
      "v2缺键.md" in s2_body and "scope" in s2_body and "status" in s2_body,
      s2_body[:300])
check("S5c 不含合规条目（合规 v2 不缺键）", "合规.md" not in s2_body, s2_body[:300])

print("\n[S6] v1 存量迁移清单")
s3 = section(out, 3)
s3_body = "\n".join(s3)
check("S6a 标注 1 条 v1 存量", "— 1 条" in s3[0], repr(s3[:2]))
check("S6b 按缺失键汇总（type 缺 1 条）",
      "缺 `type`：1 条" in s3_body, s3_body[:300])
check("S6c 列出 v1 条目样例", "v1条目.md" in s3_body, s3_body[:300])

print("\n[S7] 未知键")
s4 = section(out, 4)
s4_body = "\n".join(s4)
check("S7a 恰 1 条命中", "— 1 条" in s4[0], repr(s4[:2]))
check("S7b 命中未知键.md 且列出 aliases 与 author",
      "未知键.md" in s4_body and "aliases" in s4_body and "author" in s4_body,
      s4_body[:300])
check("S7c 附行号（aliases 在第 3 行）", "第 3 行" in s4_body, s4_body[:300])
check("S7d 不含 v2缺键.md", "v2缺键.md" not in s4_body, s4_body[:300])

print("\n[S8] 无 frontmatter 单列")
s6 = section(out, 6)
s6_body = "\n".join(s6)
check("S8a 恰 1 条命中", "— 1 条" in s6[0], repr(s6[:2]))
check("S8b 命中无frontmatter.md", "无frontmatter.md" in s6_body, s6_body[:300])

print("\n[S9] 可选键使用率")
s5 = section(out, 5)
s5_body = "\n".join(s5)
check("S9a 分母为「带 frontmatter 的条目数」而非文件数",
      "`source`: 0 / 7" in s5_body, s5_body[:400])

print("\n[S10] 结论口径")
concl = out[out.index("## 结论"):]
check("S10a 缺陷数 = 核心键缺失 1 + v2 缺失 1 + 读取失败 0 = 2",
      "发现 2 条结构缺陷" in concl, concl[:300])
check("S10b 无 frontmatter 不计入缺陷（未出现在缺陷明细）",
      "无 frontmatter 1" not in concl, concl[:300])
check("S10c 点出 v1 存量 1 条", "v1 存量 1 条" in concl, concl[:300])

print("\n[S11] 只读性与幂等")
before = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in ROOT.rglob("*.md")}
out2 = run()
after = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in ROOT.rglob("*.md")}
check("S11a 调用不新增/删除文件", set(before) == set(after),
      f"{sorted(set(before) ^ set(after))}")
check("S11b 调用不修改任何文件内容", before == after,
      f"{[k for k in before if before[k] != after.get(k)]}")
check("S11c 连续两次调用输出一致（纯读、无状态）", out == out2)

print("\n[S12] limit 生效（样例截断不影响计数）")
out3 = run(limit=1)
check("S12a limit=1 时计数仍为全量", "— 1 条" in section(out3, 1)[0])

print("\n[S13] 修复后应归零（判据可证伪：缺陷是真实可消的）")
write("缺核心键.md", CORE.format(t="缺核心键") + V2)
write("v2缺键.md", CORE.format(t="v2缺键") + V2)
out4 = run()
check("S13a 修复两条缺陷条目后，缺陷归零",
      "未发现结构缺陷" in out4, out4[out4.index("## 结论"):][:300])
check("S13b 但 v1 存量与未知键仍在（不因缺陷归零而消失）",
      "v1 存量 1 条" in out4 and "aliases" in out4)

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
