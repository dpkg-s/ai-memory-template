#!/usr/bin/env python3
"""工具输出可选 JSON 回归测试 for ai-memory MCP server（C1, 2026-10-08）。

背景:
    全部工具原本只返回格式化文本，脚本化调用要正则扒字段。C1 给**本质是「同构记录
    列表」的只读工具**加 `output_format`（默认 "text"）：memory_read / memory_list /
    memory_recent / memory_orphans。

本脚本的断言口径（都可证伪）:
    J1  四个工具在 json 形态下必须能被 `json.loads` 解析 —— 这是本项存在的全部理由。
    J2  **text 形态逐字节不变**：加参数绝不能顺手改了原来的文本（红一下就是回归）。
    J3  **范围即契约**：`output_format` 只允许出现在这四个工具上。报告型工具
        （stats / doctor / audit / schema_check …）**不得**有 —— 它们的产出是叙事，
        转 JSON 等于另立一套真相。J3 钉住「不要手滑扩大范围」。
    J4  非法取值**报错**而不是静默回落 text。脚本最怕「以为拿到 JSON、实际是一句
        中文」—— 那时 json.loads 会在很远的地方才炸。
    J5  **字段集合精确**：JSON 契约是被脚本依赖的东西，多/少一个键都算破坏性变更。
    J6  JSON 与文本**同源**（同一批记录）：count 必须与文本里的行数一致。
    J7  截断语义：JSON 的 `body` **不含**中文截断说明（信息由 truncated/total_chars
        承载）—— 否则脚本还得正则剥中文。
    J8  中文不转义（ensure_ascii=False）。

运行:
    python tests/test_output_json.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。
"""
from __future__ import annotations

import json
import os
import re
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

import memory_config  # noqa: E402
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
SRC = Path(server.__file__).read_text(encoding="utf-8")


def jload(text: str):
    """成功返回 (True, obj)，失败返回 (False, 异常文本)。"""
    try:
        return True, json.loads(text)
    except Exception as e:                                   # noqa: BLE001
        return False, f"{type(e).__name__}: {e} / {text[:120]!r}"


def defline(name: str) -> str:
    """从源码里取出 `def <name>(...)` 的参数串（装饰器会包住函数，故走源码）。"""
    m = re.search(rf"^def {name}\((.*?)\) -> str:", SRC, re.M | re.S)
    return m.group(1) if m else ""


# =====================================================================
# 造数据
# =====================================================================
print("\n[准备] 造三条测试条目")
# ⚠️ 甲的正文必须**明显长于** J7 里设的预算(20)，否则「截断语义」整组断言
# 会因为压根没发生截断而恒绿地证明不了任何东西（首版正文恰好 20 字，J7a/b/d 全红）。
server.memory_write("输出测试甲", "正文甲，含关键词 输出验证 与一段中文。"
                                "这是一段专为截断测试准备的更长正文，用来确认超出预算时"
                                "JSON 只给截断后的正文，而截断说明只出现在文本形态里。",
                    tags=["输出测试"])
server.memory_write("输出测试乙", "正文乙，含关键词 输出验证。", tags=["输出测试"])
server.memory_write("输出测试孤立", "没有人引用我。", tags=["输出测试"])

# =====================================================================
# J1 四个工具都能产出可解析的 JSON
# =====================================================================
print("\n[J1] json 形态必须可解析")
_ok, r_read = jload(server.memory_read("输出测试甲", output_format="json"))
check("J1a memory_read(json) 可解析", _ok, str(r_read)[:160])
_ok, r_list = jload(server.memory_list(output_format="json"))
check("J1b memory_list(json) 可解析", _ok, str(r_list)[:160])
_ok, r_recent = jload(server.memory_recent(days=3650, output_format="json"))
check("J1c memory_recent(json) 可解析", _ok, str(r_recent)[:160])
_ok, r_orphan = jload(server.memory_orphans(output_format="json"))
check("J1d memory_orphans(json) 可解析", _ok, str(r_orphan)[:160])

# =====================================================================
# J2 text 形态不得被改动
# =====================================================================
print("\n[J2] text 形态逐字节不变")
check("J2a read 默认形态仍是 `[meta]\\n\\n正文`",
      server.memory_read("输出测试甲").startswith("[title=输出测试甲")
      and "\n\n正文甲" in server.memory_read("输出测试甲"),
      server.memory_read("输出测试甲")[:80])
check("J2b read 未命中仍是中文提示",
      server.memory_read("压根不存在").startswith("未找到标题为"))
_l_txt = server.memory_list()
check("J2c list 仍是 `- [日期] 标题 [tier|reads]…` 行式文本",
      _l_txt.startswith("- [") and "输出测试甲" in _l_txt, _l_txt[:120])
_r_txt = server.memory_recent(days=3650)
check("J2d recent 仍是标题行 + 缩进摘要", _r_txt.startswith("# 近 3650 天更新（"),
      _r_txt[:80])
_o_txt = server.memory_orphans()
check("J2e orphans 仍是 `# 孤立笔记 (N 条)` 文本",
      _o_txt.startswith("# 孤立笔记 (") and "以下笔记没有其他笔记引用它们" in _o_txt
      and ("- [[" in _o_txt or "- (无孤立笔记)" in _o_txt), _o_txt[:120])
check("J2f `output_format=\"text\"` 与不传参完全一致",
      server.memory_list(output_format="text") == _l_txt
      and server.memory_orphans(output_format="text") == _o_txt)
check("J2g text 形态**不是** JSON（否则脚本会误判）",
      not jload(_l_txt)[0] and not jload(_o_txt)[0])

# =====================================================================
# J3 范围即契约
# =====================================================================
print("\n[J3] output_format 只允许出现在四个工具上")
_ALLOWED = {"memory_read", "memory_list", "memory_recent", "memory_orphans"}
_具有 = {m for m in re.findall(r"^def (memory_\w+)\(", SRC, re.M)
         if "output_format" in defline(m)}
check("J3a 开放 json 的恰好是这四个", _具有 == _ALLOWED,
      f"多={sorted(_具有 - _ALLOWED)} 少={sorted(_ALLOWED - _具有)}")
for _t in ("memory_stats", "memory_doctor", "memory_audit", "memory_schema_diff",
           "memory_context", "memory_graph", "memory_write", "memory_delete"):
    check(f"J3b {_t} 不得有 output_format", "output_format" not in defline(_t),
          defline(_t)[:120])

# =====================================================================
# J4 非法取值必须报错
# =====================================================================
print("\n[J4] 非法 output_format 必须报错（不静默回落）")
for _name, _call in (
        ("read", lambda v: server.memory_read("输出测试甲", output_format=v)),
        ("list", lambda v: server.memory_list(output_format=v)),
        ("recent", lambda v: server.memory_recent(output_format=v)),
        ("orphans", lambda v: server.memory_orphans(output_format=v))):
    for _bad in ("xml", "yaml", "TEXT2", "1"):
        _out = _call(_bad)
        check(f"J4a {_name}({_bad!r}) 报错", _out.startswith("错误：") and "output_format" in _out,
              _out[:90])
        check(f"J4b {_name}({_bad!r}) 不是 JSON", not jload(_out)[0])

print("\n[J4c] 合法取值应宽容大小写与空白")
check("J4c-1 ' JSON ' 被接受", jload(server.memory_list(output_format=" JSON "))[0])
check("J4c-2 'Text' 被接受", jload(server.memory_list(output_format="Text"))[0] is False
      and "output_format" not in server.memory_list(output_format="Text"))
check("J4c-3 '' 视同默认 text", server.memory_list(output_format="") == _l_txt)
check("J4c-4 None 视同默认 text", server.memory_list() == _l_txt)

# =====================================================================
# J5 字段集合精确
# =====================================================================
print("\n[J5] JSON 契约字段集合")
check("J5a read 字段集",
      set(r_read) == {"found", "title", "path", "meta", "body", "truncated", "total_chars"},
      str(sorted(r_read)))
check("J5b list 字段集",
      set(r_list) == {"trash", "count", "limit", "shown", "hidden", "entries"},
      str(sorted(r_list)))
check("J5c list 记录字段集",
      set(r_list["entries"][0]) == {"title", "created", "tier", "reads", "source",
                                    "tags", "preview", "path", "facets"},
      str(sorted(r_list["entries"][0])))
check("J5d recent 字段集",
      set(r_recent) == {"days", "count", "limit", "shown", "entries"}
      and set(r_recent["entries"][0]) == {"updated", "title", "summary"},
      str(sorted(r_recent)))
check("J5e orphans 字段集",
      set(r_orphan) == {"count", "orphans"}
      and set(r_orphan["orphans"][0]) == {"title", "path"},
      str(sorted(r_orphan)))
check("J5f trash 形态字段集",
      set(json.loads(server.memory_list(include_trash=True, output_format="json")))
      == {"trash", "count", "limit", "shown", "entries"})
check("J5g read 未命中字段集",
      set(json.loads(server.memory_read("压根不存在", output_format="json")))
      == {"found", "title"})

# =====================================================================
# J6 JSON 与文本同源
# =====================================================================
print("\n[J6] JSON 与文本必须同源（同一批记录）")
_text_lines = [l for l in _l_txt.splitlines() if l.startswith("- [")]
check("J6a list：count 与文本行数一致（未触 limit 时）",
      r_list["count"] == len(_text_lines), f"{r_list['count']} vs {len(_text_lines)}")
check("J6b list：entries 顺序与文本一致",
      [e["title"] for e in r_list["entries"]] ==
      [re.sub(r"^- \[[^\]]*\] ([^ ]*) .*$", r"\1", l) for l in _text_lines],
      str([e["title"] for e in r_list["entries"]]))
check("J6c orphans：count 与文本条目数一致",
      r_orphan["count"] == _o_txt.count("- [["), f"{r_orphan['count']} vs {_o_txt.count('- [[')}")
check("J6d read：JSON 正文与文本正文一致",
      r_read["body"] == server.memory_read("输出测试甲").split("\n\n", 1)[1])

print("\n[J6e] limit 生效但不改变 total")
_lim = json.loads(server.memory_list(limit=1, output_format="json"))
check("J6e-1 shown=1 且 entries 只有 1 条",
      _lim["shown"] == 1 and len(_lim["entries"]) == 1, str(_lim["shown"]))
check("J6e-2 count 仍是全量", _lim["count"] == r_list["count"],
      f"{_lim['count']} vs {r_list['count']}")

# =====================================================================
# J7 截断语义
# =====================================================================
print("\n[J7] 截断语义")
_old = memory_config.CACHE_TTL
memory_config.CACHE_TTL = -1.0
try:
    (ROOT / "memory.toml").write_text("body_truncate_chars = 20\n", encoding="utf-8")
    _r_trunc = json.loads(server.memory_read("输出测试甲", output_format="json"))
    _t_trunc = server.memory_read("输出测试甲")
    check("J7a JSON 报 truncated=True", _r_trunc["truncated"] is True, str(_r_trunc["truncated"]))
    check("J7b JSON 的 total_chars 是原长", _r_trunc["total_chars"] > len(_r_trunc["body"]),
          f"{_r_trunc['total_chars']} vs {len(_r_trunc['body'])}")
    check("J7c JSON 的 body **不含**中文截断说明", "已截断至前" not in _r_trunc["body"],
          _r_trunc["body"][-80:])
    check("J7d 文本形态仍带中文截断说明（原行为）", "已截断至前 20" in _t_trunc, _t_trunc[-120:])
    check("J7e body 长度即预算", len(_r_trunc["body"]) == 20, str(len(_r_trunc["body"])))
finally:
    (ROOT / "memory.toml").unlink(missing_ok=True)
    memory_config.CACHE_TTL = _old
    memory_config.reload()

# =====================================================================
# J8 中文不转义
# =====================================================================
print("\n[J8] 中文不转义")
check("J8a 输出里直接出现中文", "输出测试甲" in server.memory_read("输出测试甲",
                                                        output_format="json"))
check("J8b 没有 \\u 转义", "\\u" not in server.memory_orphans(output_format="json"))

# =====================================================================
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
