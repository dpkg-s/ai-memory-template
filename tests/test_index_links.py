#!/usr/bin/env python3
"""索引链接可解析性回归测试 for ai-memory MCP server.

背景（2026-09-11）:
    `记忆索引.md` 由 `_refresh_index()` 在每次 memory_write 后自动重建，早期
    实现输出 `- [[{title}]]`。Obsidian 解析双链以**文件名 stem** 为准，而
    `safe_filename()` 会把标题里的空格等字符归一化成下划线 —— 于是凡标题含
    空格的笔记（如 "2026-07-06 记忆库半自动审计" -> 2026-07-06_记忆库半自动审计.md）
    在索引里全是**渲染死链**，更糟的是每次写入都会把人工修复覆盖回去。实测
    148 篇的库里有 36 条这类死链。

    修复：新增 `_wiki_link(path, title)`，target 恒用 `path.stem`，标题只作
    别名（`[[stem|title]]`）；`_refresh_index` 与 `memory_index_draft` 统一走它。

本脚本的断言口径：**索引与草稿里的每一条链接，其 target 都必须能解析到
库中真实存在的文件**。这是能自动抓住该类回归的最直接判据。

运行:
    python tests/test_index_links.py        # 退出码 0=全绿, 1=有失败
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
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402   (此时 server.MEMORY_DIR == _TMP)
from text_utils import extract_wiki_links, safe_filename  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def index_targets() -> list[str]:
    """取出 记忆索引.md 中所有双链的 target（别名被忽略）。"""
    p = Path(_TMP) / "记忆索引.md"
    if not p.exists():
        return []
    return extract_wiki_links(p.read_text(encoding="utf-8"))


def resolves(target: str) -> bool:
    return (Path(_TMP) / f"{target}.md").exists()


def unreachable(targets: list[str]) -> list[str]:
    return [t for t in targets if not resolves(t)]


# =====================================================================
# IL1: _wiki_link 单元语义
print("\n[IL1] _wiki_link 单元语义")
l_same = server._wiki_link(Path(_TMP) / "AI关联.md", "AI关联")
check("IL1a 标题==stem 时输出裸链接", l_same == "[[AI关联]]", l_same)
l_alias = server._wiki_link(Path(_TMP) / "A_B.md", "A B")
check("IL1b 标题!=stem 时 target 用 stem、标题进别名位", l_alias == "[[A_B|A B]]", l_alias)
check("IL1c 别名形式 target 不含空格", "[[A B" not in l_alias, l_alias)
check("IL1d 无标题时退化为裸链接", server._wiki_link(Path(_TMP) / "X.md") == "[[X]]")

# =====================================================================
# IL2: 标题含空格 —— 真实缺陷场景
print("\n[IL2] 标题含空格（原缺陷场景）")
t_space = "2026-07-06 记忆库半自动审计"
server.memory_write(t_space, "标题含空格，文件名会被归一化为下划线。")
check("IL2a safe_filename 确实把空格换成下划线", safe_filename(t_space) == "2026-07-06_记忆库半自动审计.md",
      safe_filename(t_space))
targets = index_targets()
check("IL2b 索引中该条 target 使用 stem", "2026-07-06_记忆库半自动审计" in targets, str(targets))
check("IL2c 索引中不含空格形态的死链 target", t_space not in targets, str(targets))
bad = unreachable(targets)
check("IL2d 索引全部链接可解析", not bad, f"死链={bad}")

# =====================================================================
# IL3: 其它非法字符 + 别名保真
print("\n[IL3] 其它归一化字符")
t_junk = '测试/非法:字符*案例?x'
stem_junk = Path(safe_filename(t_junk)).stem
server.memory_write(t_junk, "标题含 / : * ? 等字符。")
targets = index_targets()
check("IL3a 含非法字符的标题可解析", stem_junk in targets and resolves(stem_junk), f"{stem_junk} in {targets}")
idx_text = (Path(_TMP) / "记忆索引.md").read_text(encoding="utf-8")
check("IL3b 原始标题保留在别名位", f"[[{stem_junk}|{t_junk}]]" in idx_text,
      f"期望 [[{stem_junk}|{t_junk}]]")
bad = unreachable(targets)
check("IL3c 索引全部链接可解析", not bad, f"死链={bad}")

# =====================================================================
# IL4: 幂等 —— 反复重建索引不得退化
print("\n[IL4] 反复重建（防覆盖回归）")
server.memory_write("测试_普通条目", "标题无空格，应输出裸链接。")
before = sorted(index_targets())
for _ in range(3):
    server._refresh_index()
after = sorted(index_targets())
check("IL4a 三次重建后链接集合不变（幂等）", before == after, f"{before[:3]} vs {after[:3]}")
bad = unreachable(after)
check("IL4b 重建后仍全部可解析", not bad, f"死链={bad}")
check("IL4c 标题==stem 时输出裸链接（无多余别名）", "测试_普通条目" in after, str(after))

# =====================================================================
# IL5: 索引草稿（memory_index_draft）同一口径
print("\n[IL5] memory_index_draft")
draft = server.memory_index_draft()
d_targets = extract_wiki_links(draft)
check("IL5a 草稿非空", len(d_targets) > 0, f"n={len(d_targets)}")
bad_d = unreachable(d_targets)
check("IL5b 草稿全部链接可解析", not bad_d, f"死链={bad_d}")

# =====================================================================
# IL6: 全库覆盖 —— 索引条目数不缺不漏
print("\n[IL6] 条目完整性")
entries = [p.stem for p, _m, _b in server._iter_entries()]
check("IL6a 索引条目数 == 库内文件数", len(set(after)) == len(set(entries)),
      f"index={len(set(after))} entries={len(set(entries))}")
check("IL6b 索引覆盖全部笔记", all(s in set(after) for s in entries),
      f"missing={[s for s in entries if s not in set(after)]}")

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
