#!/usr/bin/env python3
"""memory_context（B1，第 23 个工具）行为测试。

与 test_tools_smoke.py 分工: 冒烟只验证「能调用」，本脚本验证「行为正确」。

覆盖:
    A. BFS 深度边界（depth=0/1/2/3）、死链与孤立笔记过滤、生成页排除
    B. 出链/反链双向可达 + 关系与来源标注 + 头部计数
    C. 排序权重（tier → access_count → updated）
    D. max_related 截断与提示；timeframe 过滤（起点恒保留 / 无法识别降级）
    E. max_chars 预算分摊（含「起点超长不得挤掉周边」的回归）
    F. 标题解析（缺失 / stem / 反斜杠归一化 / 生成页作起点）
    G. _parse_timeframe 单元

运行:
    <venv python> tests/test_context.py      # 退出码 0=全绿, 1=有失败
依赖:
    server.py import mcp ⇒ 必须用装了 mcp 的 venv 解释器跑。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-ctx-")
os.environ["AI_MEMORY_DIR"] = _TMP
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


def w(title: str, content: str, tier: str | None = None) -> None:
    server.memory_write(title, content, tags=["ctx"], tier=tier)


def set_fm(title: str, **fields) -> None:
    """直接改 frontmatter 字段 —— access_count / updated 没有工具入口。

    值请自带引号（如 updated='"2020-01-01T00:00:00+00:00"'），与 server 自身写法一致。
    """
    p = Path(_TMP) / server._safe_filename(title)
    raw = p.read_bytes().decode("utf-8")
    for k, v in fields.items():
        pat = r"(?m)^" + re.escape(k) + r":.*?(?=\r?\n)"
        raw, n = re.subn(pat, "{}: {}".format(k, v), raw, count=1)
        if n != 1:
            raise RuntimeError("frontmatter 字段 {} 未命中 ({})".format(k, p.name))
    p.write_bytes(raw.encode("utf-8"))
    # 立刻失效条目缓存，不依赖 mtime 精度
    server._CACHE_VALID = False
    server._ENTRY_CACHE.clear()


def order_of(out: str, *titles: str) -> tuple[bool, list[int]]:
    """这些标题是否全部出现、且出现次序与传入一致。"""
    idx = [out.find("## " + t) for t in titles]
    if any(i < 0 for i in idx):
        return False, idx
    return idx == sorted(idx), idx


def head_of(out: str) -> str:
    return out.split("\n", 2)[1] if out.count("\n") >= 2 else out


# =====================================================================
section("准备图数据")
w("CTX中心", "见 [[CTX一层A]] 与 [[CTX一层B]]；另有 [[CTX死链]] 未建页。")
w("CTX一层A", "指向 [[CTX二层X]]。")
w("CTX一层B", "指向 [[CTX二层Y]]。")
w("CTX二层X", "指向 [[CTX三层P]]。")
w("CTX二层Y", "叶子。")
w("CTX三层P", "最深层。")
w("CTX反向源", "引用 [[CTX中心]]。")
w("CTX孤立", "无链接。")

# =====================================================================
section("A. 深度边界与图过滤")
out0 = server.memory_context("CTX中心", depth=0)
check("depth=0 只有起点块", "## CTX一层A" not in out0 and "## CTX反向源" not in out0, out0[:160])
check("depth=0 头部标注周边 0 篇", "周边 0 篇（展开 0 篇）" in out0, head_of(out0))

out1 = server.memory_context("CTX中心", depth=1)
check("depth=1 含出链邻居", "## CTX一层A" in out1 and "## CTX一层B" in out1)
check("depth=1 含反链邻居", "## CTX反向源" in out1)
check("depth=1 不含二层", "## CTX二层X" not in out1)

out2 = server.memory_context("CTX中心", depth=2)
check("depth=2 含二层出链邻居", "## CTX二层X" in out2 and "## CTX二层Y" in out2)
check("depth=2 不含三层（深度边界）", "## CTX三层P" not in out2)

out3 = server.memory_context("CTX中心", depth=3)
check("depth=3 含三层", "## CTX三层P" in out3)
check("未建页的死链不展开成块（起点正文里仍可见其原文）",
      "## CTX死链" not in out2 and "CTX死链" in out2)
check("孤立笔记不出现", "## CTX孤立" not in out2)
check("生成页「记忆索引」不参与周边", "## 记忆索引" not in out2)
check("深度值为负按 0 处理", "## CTX一层A" not in server.memory_context("CTX中心", depth=-3))

# =====================================================================
section("B. 关系与来源标注")
check("出链标注带来源", "出链 ←「CTX中心」" in out2)
check("反链标注带来源", "反链 ←「CTX中心」" in out2 and "## CTX反向源" in out2)
check("头部出链/反链计数正确", "起点出链 2 条 / 反链 1 条" in out2, head_of(out2))
check("深度标注随层级变化", "深度 1" in out2 and "深度 2" in out2)
check("每个周边块都有 tier/reads 元信息", out2.count("tier=") >= 4, str(out2.count("tier=")))

# =====================================================================
section("C. 排序权重")
w("CTXR中心", "见 [[CTXR热]]、[[CTXR温]]、[[CTXR冷]]。")
w("CTXR热", "热。", tier="hot")
w("CTXR温", "温。", tier="warm")
w("CTXR冷", "冷。", tier="cold")
ok, idx = order_of(server.memory_context("CTXR中心", depth=1), "CTXR热", "CTXR温", "CTXR冷")
check("tier 权重 hot > warm > cold", ok, str(idx))

w("CTXC中心", "见 [[CTXC甲]]、[[CTXC乙]]、[[CTXC丙]]。")
w("CTXC甲", "甲。")
w("CTXC乙", "乙。")
w("CTXC丙", "丙。")
set_fm("CTXC甲", access_count=1)
set_fm("CTXC乙", access_count=5)
set_fm("CTXC丙", access_count=3)
ok, idx = order_of(server.memory_context("CTXC中心", depth=1), "CTXC乙", "CTXC丙", "CTXC甲")
check("同 tier 时 access_count 降序", ok, str(idx))

w("CTXT中心", "见 [[CTXT甲]]、[[CTXT乙]]、[[CTXT丙]]。")
w("CTXT甲", "甲。")
w("CTXT乙", "乙。")
w("CTXT丙", "丙。")
set_fm("CTXT甲", updated='"2020-01-01T00:00:00+00:00"')
set_fm("CTXT乙", updated='"2026-09-17T00:00:00+00:00"')
set_fm("CTXT丙", updated='"2025-01-01T00:00:00+00:00"')
ok, idx = order_of(server.memory_context("CTXT中心", depth=1), "CTXT乙", "CTXT丙", "CTXT甲")
check("同 tier 同 reads 时 updated 降序", ok, str(idx))

# =====================================================================
section("D. max_related 与 timeframe")
w("CTXM中心", "见 [[CTXM一]]、[[CTXM二]]、[[CTXM三]]、[[CTXM四]]。")
for _n in ("一", "二", "三", "四"):
    w("CTXM" + _n, "正文。")
outm = server.memory_context("CTXM中心", depth=1, max_related=2)
check("max_related=2 只展开 2 篇", "周边 4 篇（展开 2 篇）" in outm, head_of(outm))
check("超出 max_related 有提示", "未展开" in outm and "提高 max_related" in outm)
check("max_related=0 不展开周边", "周边 4 篇（展开 0 篇）" in server.memory_context(
    "CTXM中心", depth=1, max_related=0))

w("CTXF中心", "见 [[CTXF新]]、[[CTXF旧]]。")
w("CTXF新", "新。")
w("CTXF旧", "旧。")
set_fm("CTXF旧", updated='"2020-01-01T00:00:00+00:00"')
set_fm("CTXF中心", updated='"2020-01-01T00:00:00+00:00"')   # 起点本身很旧
outf = server.memory_context("CTXF中心", depth=1, timeframe="30d")
check("timeframe 过滤掉过期邻居", "## CTXF旧" not in outf and "## CTXF新" in outf)
check("timeframe 起点恒保留（即使起点 updated 很旧）", "## CTXF中心" in outf)
check("timeframe 略过数有提示", "超出 timeframe 已略过" in outf, head_of(outf))
outf2 = server.memory_context("CTXF中心", depth=1, timeframe="这不是时间")
check("无法识别的 timeframe 降级为不限并提示", "无法识别" in outf2 and "## CTXF旧" in outf2,
      head_of(outf2))
check("timeframe=all 视为不限", "## CTXF旧" in server.memory_context(
    "CTXF中心", depth=1, timeframe="all"))

# =====================================================================
section("E. max_chars 预算分摊")
_LONG = "长" * 9000
w("CTXL中心", _LONG + " [[CTXL邻居]]")
w("CTXL邻居", "邻居正文。")
outl = server.memory_context("CTXL中心", depth=1)
check("默认预算下起点超长不得挤掉周边（回归）", "## CTXL邻居" in outl, "len=%d" % len(outl))
check("默认预算总长受 BODY_TRUNCATE_CHARS 约束",
      len(outl) <= server.BODY_TRUNCATE_CHARS + 200, "len=%d" % len(outl))
check("起点块被截断有提示", "起点正文已按 max_chars 截断" in outl)
outl0 = server.memory_context("CTXL中心", depth=1, max_chars=0)
check("max_chars=0 不限长度", len(outl0) > 9000, "len=%d" % len(outl0))
check("max_chars=0 含完整长正文", _LONG in outl0)

outs = server.memory_context("CTXM中心", depth=1, max_related=4, max_chars=500)
check("预算耗尽后停止展开并有提示", "只展开了" in outs, outs[-160:])
check("停止展开时总长仍受限", len(outs) <= 500 + 200, "len=%d" % len(outs))

w("CTXS中心", "见 [[CTXS长邻居]]。")
w("CTXS长邻居", "甲" * 3000)
outt = server.memory_context("CTXS中心", depth=1, max_chars=600)
check("单块超预算时按块截断并有提示", "本块已按 max_chars 截断" in outt, "len=%d" % len(outt))
check("截断保留标注行（不切成半截）", "出链 ←「CTXS中心」· 深度 1" in outt, outt[-200:])
check("截断后总长仍受限", len(outt) <= 600 + 200, "len=%d" % len(outt))

# =====================================================================
section("F. 标题解析与错误处理")
check("不存在的标题返回友好提示",
      "未找到标题为" in server.memory_context("根本不存在的标题XYZ"))
w("CTX 空格 标题", "正文。")
check("文件名 stem（下划线形式）也能命中",
      "上下文：CTX 空格 标题" in server.memory_context("CTX_空格_标题"))
w("CTX反斜杠_笔记", "正文。")
check("反斜杠与空白归一化后命中",
      "上下文：CTX反斜杠_笔记" in server.memory_context("  CTX反\\斜杠_笔记 "))
check("显式把生成页作起点仍可用", "上下文：记忆索引" in server.memory_context("记忆索引"))

# =====================================================================
section("G. _parse_timeframe 单元")
_CASES = [("today", 0), ("今天", 0), ("yesterday", 1), ("昨天", 1),
          ("last week", 7), ("last month", 30), ("last year", 365),
          ("2 days ago", 2), ("3d", 3), ("24h", 1), ("2w", 14),
          ("all", None), ("", None), (None, None), ("5", None),
          ("这不是时间", None)]
for _tf, _want in _CASES:
    _got = server._parse_timeframe(_tf)
    check("_parse_timeframe(%r) == %r" % (_tf, _want), _got == _want, "实际 %r" % (_got,))
_dt = server._parse_timeframe("2020-01-01")
check("日期形式返回天数", isinstance(_dt, int) and _dt > 1000, repr(_dt))

# =====================================================================
# cleanup
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
sys.exit(1 if _failed else 0)
