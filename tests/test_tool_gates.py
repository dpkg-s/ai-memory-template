#!/usr/bin/env python3
"""工具权限声明门禁 + 间接泄露面测试（A/B 两项）。

覆盖:
    A. 声明表自检：26 只工具全部合规、豁免表不腐化、注入假工具能被拦住
       （含 strict 抛错 / warn 放行两种模式）
    B. 门禁真实生效：受限视野下 12 只 owner 专有工具整只不可用，且拒绝后
       不产生副作用（不是「先执行再报错」）
    C. 间接泄露面：stats / orphans / graph 按权级收窄，heat_suggest 整只关闭

设计意图：这道自检的全部价值在于「新增写工具忘了挂 @_owner_gate 时会失败」。
故 B 段用 `_audit_tool_gates(tools=...)` 的注入点真的构造一只漏挂的写工具，
而不是只断言现有代码碰巧合规 —— 后者在任何人删掉自检后依然会绿。

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_tool_gates.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑（server.py import mcp）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-gates-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_SOURCE_CONTEXT"] = "test-gates"
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


def fake_tool(name: str, fn, annotations) -> types.SimpleNamespace:
    """构造一只「假工具」，形状与 FastMCP Tool 一致（name / fn / annotations）。"""
    return types.SimpleNamespace(name=name, fn=fn, annotations=annotations)


# ---- 供注入段使用的假工具函数体 -------------------------------------------
def _fake_write_plain() -> str:
    """未挂任何门的写工具（应被自检拦下）。"""
    return "ok"


def _fake_tiered_unguarded() -> str:
    """分级写工具但没自己校验写权（应被拦下）。"""
    return "ok"


def _fake_tiered_guarded() -> str:
    """分级写工具，函数体里真的校验了写权（合规）。"""
    denied = server._readonly_endpoint("fake_tiered")
    return denied or "ok"


def _fake_readonly() -> str:
    """普通只读工具：不加额外约束（合规）。"""
    return "ok"


def _fake_tiered_fake_call() -> str:
    """假装校验了写权：只在**字符串**里写了 _readonly_endpoint(x)。

    这是「按源码文本判定」的典型翻车样本 —— 源码里有那个子串，字节码里没有
    这个名字。判据必须认字节码，否则一个手滑写成注释的调用就能骗过自检。
    """
    return "文本: _readonly_endpoint(x)"


# ============================================================
print("== A. 现有注册表的声明自检 ==")
# ============================================================
_tools = server._registered_tools()
_names = {getattr(t, "name", None) for t in _tools}
check("A1 注册表可枚举且非空", len(_tools) >= 26, f"实际 {len(_tools)} 只")
check("A2 全部 26 只工具声明自检通过（0 违规）",
      server._audit_tool_gates() == [], str(server._audit_tool_gates()))
check("A3 每只工具都带 annotations（否则分类无从谈起）",
      all(getattr(t, "annotations", None) is not None for t in _tools))
check("A4 豁免表里的工具名都真实存在（防止改名后表腐化）",
      server._TIERED_WRITE_TOOLS <= _names and server._OWNER_ONLY_READ_TOOLS <= _names,
      f"tiered 缺 {sorted(server._TIERED_WRITE_TOOLS - _names)}；"
      f"owner-only 缺 {sorted(server._OWNER_ONLY_READ_TOOLS - _names)}")
_write_tools = [t for t in _tools
                if getattr(getattr(t, "annotations", None), "readOnlyHint", None) is False]
check("A5 写/破坏性工具共 12 只，且逐只通过门禁判定",
      len(_write_tools) == 12 and
      all(server._gate_violation(getattr(t, "name", None),
                                 getattr(t, "annotations", None),
                                 getattr(t, "fn", None)) is None for t in _write_tools),
      str(sorted(getattr(t, "name", None) for t in _write_tools)))

# ============================================================
print("\n== B. 注入假工具：漏挂门禁必须被拦下 ==")
# ============================================================
_v = server._gate_violation("memory_new_write", server._WRITE, _fake_write_plain)
check("B1 新增写工具漏挂 @_owner_gate -> 违规", _v is not None and "未挂" in str(_v), str(_v))

_gated = server._owner_gate(_fake_write_plain)
check("B2 写工具挂了 @_owner_gate -> 合规",
      server._gate_violation("memory_new_write", server._WRITE, _gated) is None)

_v = server._gate_violation("memory_write", server._WRITE, _fake_tiered_unguarded)
check("B3 分级写工具不校验写权 -> 违规", _v is not None and "_readonly_endpoint" in str(_v), str(_v))

check("B4 分级写工具调用 _readonly_endpoint -> 合规",
      server._gate_violation("memory_write", server._WRITE, _fake_tiered_guarded) is None)

_v = server._gate_violation("memory_doctor", server._RO, _fake_readonly)
check("B5 底层诊断工具漏挂 @_owner_gate -> 违规", _v is not None and "诊断" in str(_v), str(_v))
check("B6 底层诊断工具挂了门 -> 合规",
      server._gate_violation("memory_doctor", server._RO,
                             server._owner_gate(_fake_readonly)) is None)

check("B7 普通只读工具不加额外约束（不过度约束）",
      server._gate_violation("memory_read", server._RO, _fake_readonly) is None)

_bad = [fake_tool("memory_new_write", _fake_write_plain, server._WRITE)]
try:
    server._audit_tool_gates(strict=True, tools=_bad)
    check("B8 strict 模式注入违规 -> 抛 RuntimeError 使服务起不来", False, "未抛错")
except RuntimeError as exc:
    check("B8 strict 模式注入违规 -> 抛 RuntimeError 使服务起不来",
          "自检未通过" in str(exc), str(exc)[:100])
_warned = server._audit_tool_gates(strict=False, tools=_bad)
check("B9 warn 模式注入违规 -> 只返回清单不抛错", len(_warned) == 1, str(_warned))
check("B10 注入空清单 -> 空表（自检不会无事生非）",
      server._audit_tool_gates(strict=True, tools=[]) == [])

# B11~B13：判据必须在**拿不到源码**的场合仍然成立。
# exec 定义的函数没有可读源文件，inspect.getsource 必然失败 —— 正好模拟冻结 /
# 打包 / zipimport 的代码。若判定依赖源码文本，这两种情形会被判反：真挂了守卫的
# 写工具被拒启、真没挂的反而放行。故判据走字节码名字表。
_ns_ok: dict = {"_readonly_endpoint": lambda _t: None}
exec("def _frozen_guarded():\n    return _readonly_endpoint('x')\n", _ns_ok)
check("B11 无源码（exec 定义）时仍能识别已调用守卫",
      server._gate_violation("memory_write", server._WRITE,
                             _ns_ok["_frozen_guarded"]) is None)
_ns_bad: dict = {}
exec("def _frozen_plain():\n    return 'ok'\n", _ns_bad)
check("B12 无源码且未调用守卫 -> 仍判违规（不因读不到源码就放行）",
      server._gate_violation("memory_write", server._WRITE,
                             _ns_bad["_frozen_plain"]) is not None)
check("B13 只在字符串里写 _readonly_endpoint(x) 骗不过判据",
      server._gate_violation("memory_write", server._WRITE,
                             _fake_tiered_fake_call) is not None,
      "源码文本里有子串但字节码没有该名字，不该算作校验")

# ============================================================
print("\n== C. 建档（owner 视角） ==")
# ============================================================
server.memory_write("门禁_公开条", "公开内容，任何人可读。", security="public")
server.memory_write("门禁_内部条", "内部内容，关联 [[门禁_绝密条]]。", security="internal")
server.memory_write("门禁_敏感条", "较敏感内容：跳板机 10.0.0.8。", security="sensitive")
server.memory_write("门禁_绝密条", "绝密内容：密钥 sk-gate-test。", security="secret")
_owner_stats = server.memory_stats()
check("C1 owner 视野统计正常", "总条目数:" in _owner_stats)

# ============================================================
print("\n== D. 受限视野：owner 专有工具整只不可用 ==")
# ============================================================
_owner_only_calls = [
    ("D01 memory_delete", lambda: server.memory_delete("门禁_内部条")),
    ("D02 memory_archive", lambda: server.memory_archive("门禁_内部条")),
    ("D03 memory_restore", lambda: server.memory_restore("门禁_内部条")),
    ("D04 memory_batch_tag", lambda: server.memory_batch_tag("internal", "x")),
    ("D05 memory_batch_tier", lambda: server.memory_batch_tier("cold")),
    ("D06 memory_archive_old", lambda: server.memory_archive_old(90)),
    ("D07 memory_update_metadata", lambda: server.memory_update_metadata("门禁_内部条", tier="cold")),
    ("D08 memory_heat_suggest", lambda: server.memory_heat_suggest(apply=True)),
    ("D09 memory_doctor", lambda: server.memory_doctor()),
    ("D10 memory_schema_diff", lambda: server.memory_schema_diff()),
    ("D11 memory_config", lambda: server._memory_config_tool(action="get")),
    ("D12 memory_restart", lambda: server.memory_restart()),
]
_tok = server.set_clearance("reader", writable=False)
try:
    for label, fn in _owner_only_calls:
        try:
            out = fn()
            ok = isinstance(out, str) and "不可用" in out
            detail = (out if isinstance(out, str) else str(out))[:90]
        except Exception as exc:  # noqa: BLE001 - 任何异常都算未按预期拒绝
            ok, detail = False, f"抛出 {type(exc).__name__}: {exc}"
        check(f"{label} 在受限视野下被拒", ok, detail)
finally:
    server.reset_clearance(_tok)

# 拒绝必须是「没执行」，不是「执行了再报错」—— 后者在 owner 视角下会看到脏数据。
check("D13 被拒的 delete 真的没删（内部条仍在）",
      "内部内容" in server.memory_read("门禁_内部条"))
check("D14 owner 视野恢复后仍然可写",
      "已更新" in server.memory_write("门禁_内部条", "内部内容，关联 [[门禁_绝密条]]。（已改）"))

# ============================================================
print("\n== E. 间接泄露面：统计/孤立/图谱按权级收窄 ==")
# ============================================================
_tok = server.set_clearance("reader", writable=False)
try:
    _reader_stats = server.memory_stats()
    _orphans = server.memory_orphans()
    _graph = server.memory_graph("门禁_内部条")

    def _total(text: str) -> int:
        for line in text.splitlines():
            if line.startswith("- 总条目数:"):
                return int(line.split(":")[1].strip())
        return -1

    _t_owner, _t_reader = _total(_owner_stats), _total(_reader_stats)
    check("E1 stats 总数随权级收窄（不是全库规模）",
          0 < _t_reader < _t_owner, f"owner={_t_owner} reader={_t_reader}")
    check("E2 reader 统计里不出现绝密条标题", "门禁_绝密条" not in _reader_stats)
    check("E3 reader 孤立清单里不出现绝密条标题", "门禁_绝密条" not in _orphans)
    check("E4 reader 图谱出链里不出现绝密条标题", "门禁_绝密条" not in _graph)
    check("E5 reader 图谱不含绝密正文片段", "sk-gate-test" not in _graph)
    check("E6 heat_suggest 整只关闭（不做过滤后仍可用的假象）",
          "不可用" in server.memory_heat_suggest())
finally:
    server.reset_clearance(_tok)

_owner_graph = server.memory_graph("门禁_内部条")
check("E7 owner 视角图谱里能看到绝密条（对照，证明 E4 不是恒真）",
      "门禁_绝密条" in _owner_graph)

# ============================================================
print()
if _failed:
    print(f"==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
print(f"==== 结果: {len(_passed)} passed / 0 failed ====")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0)
