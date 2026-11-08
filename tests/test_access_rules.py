#!/usr/bin/env python3
"""访问档位自动判定回归测试（v3.1 · access_rules + memory_write + memory_access_suggest）。

覆盖:
    A. classify_access 的规则命中：凭据实值 / PII 实值 / 敏感标签 / 公开标签 / 默认
    B. merge_with_current 的「只收紧不放宽」
    C. memory_write 的自动分级：新建、显式优先、已有档位只提示、核心页豁免
    D. memory_access_suggest：默认预览不写盘 / apply=True 才写盘
    E. 环境变量总开关 AI_MEMORY_AUTO_ACCESS=off

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_access_rules.py       # 退出码 0=全绿, 1=有失败
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
_TMP = tempfile.mkdtemp(prefix="ai-memory-access-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_SOURCE_CONTEXT"] = "test-access"
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import access_rules  # noqa: E402
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


def read_meta(title: str) -> dict:
    for f in server._iter_md_files():
        meta, _ = server._load_memory(f)
        if server._entry_title(meta, f) == title:
            return meta
    return {}


# ============================================================
print("== A. classify_access 规则命中 ==")
# ============================================================
# 左侧用例刻意包含「有敏感**词**但没有实值」的样本（令牌签发 / 排期），
# 它们是判定的误报来源：只出现词就升档，半个库都会变成 sensitive。
_cases = [
    ("普通工程笔记", "把 uvicorn 的 reload 开关调通了，端口 8700。", [],
     ("internal", "reader"), "无实值证据 ⇒ 默认内部级"),
    ("令牌签发说明", "令牌签发面板三项：标准 / 只读共享 / 仅公开，正文不含真值。", ["mcp"],
     ("internal", "reader"), "只出现「令牌」这个词不算凭据"),
    ("联系人", "手机号 13812345678，只有内部知道。", [],
     ("sensitive", "member"), "手机号实值 ⇒ sensitive"),
    ("身份证", "身份证 11010519491231002X 已核验。", [],
     ("sensitive", "member"), "身份证号实值 ⇒ sensitive"),
    ("银行卡", "收款卡 6222021234567890123。", [],
     ("sensitive", "member"), "银行卡号实值 ⇒ sensitive"),
    ("私钥", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----", [],
     ("secret", "owner"), "PEM 私钥块 ⇒ secret"),
    ("真口令", "登录口令 password=Zx9kLm2Qw7", [],
     ("secret", "owner"), "赋值型真凭据 ⇒ secret"),
    ("占位口令", "口令写法：password=<YOUR_PASSWORD>", [],
     ("internal", "reader"), "占位符不是凭据"),
    ("敏感标签", "排期已定，正文没有实值。", ["privacy"],
     ("sensitive", "member"), "敏感标签 ⇒ sensitive"),
    ("触发词", "私钥放在 keys/age.key，收件人 age1abc。", [],
     ("sensitive", "member"), "明确指向敏感物的触发词 ⇒ sensitive"),
    ("策略讨论", "把安全与隐私提升到最高优先级，避免日志泄露正文，按周排期。", [],
     ("internal", "reader"), "讨论隐私/泄露/排期不是持有，只作提示"),
    ("公开标签", "这是一份演示数据。", ["demo"],
     ("public", "guest"), "公开标签 ⇒ public"),
    ("连通性测试", "随便写点东西。", [],
     ("public", "guest"), "标题含公开线索 ⇒ public"),
]
for title, body, tags, want, desc in _cases:
    verdict = access_rules.classify_access(title, body, tags)
    check(f"A {desc}", (verdict.security, verdict.clearance) == want,
          f"实际 {verdict.security}/{verdict.clearance}：{'；'.join(verdict.reasons)}")

_v_core = access_rules.classify_access("近期工作动态", "手机号 13812345678 出现在摘要里。", [],
                                       core_pages={"近期工作动态"})
check("A 核心页豁免：导航页即使命中 PII 也固定 internal/reader",
      (_v_core.security, _v_core.clearance) == ("internal", "reader"),
      f"{_v_core.security}/{_v_core.clearance}")

_v_weak = access_rules.classify_access("闲聊", "下周排期再说。", [])
check("A 提示词不触发（排期只进 reasons）", _v_weak.security == "internal",
      f"score={_v_weak.score} reasons={_v_weak.reasons}")

# ============================================================
print("\n== B. merge_with_current：只收紧不放宽 ==")
# ============================================================
_v_sens = access_rules.classify_access("联系人", "手机号 13812345678")
_v_int = access_rules.classify_access("普通", "没有敏感内容")

sec, clr, tight = access_rules.merge_with_current(_v_sens, "internal", "reader")
check("B1 现值 internal/reader + 建议 sensitive ⇒ 收紧并标记",
      (sec, clr, tight) == ("sensitive", "member", True), f"{sec}/{clr} tight={tight}")

sec, clr, tight = access_rules.merge_with_current(_v_int, "sensitive", "member")
check("B2 现值 sensitive + 建议 internal ⇒ 保持，不标记",
      (sec, clr, tight) == ("sensitive", "member", False), f"{sec}/{clr} tight={tight}")

sec, clr, tight = access_rules.merge_with_current(_v_int, None, None)
check("B3 缺字段 ⇒ 采用建议", (sec, clr) == ("internal", "reader"), f"{sec}/{clr}")

sec, clr, tight = access_rules.merge_with_current(_v_sens, "secret", "owner")
check("B4 现值更高（secret/owner）⇒ 原样保留",
      (sec, clr, tight) == ("secret", "owner", False), f"{sec}/{clr} tight={tight}")

# ============================================================
print("\n== C. memory_write 自动分级 ==")
# ============================================================
_r = server.memory_write("自动_手机号", "联系人 13812345678，只有内部知道。")
_m = read_meta("自动_手机号")
check("C1 新建命中 PII ⇒ sensitive/member 落盘",
      (_m.get("security"), _m.get("clearance")) == ("sensitive", "member"),
      f"{_m.get('security')}/{_m.get('clearance')}")
check("C2 返回值说明已自动分级", "已自动分级" in _r, _r[:120])

_r = server.memory_write("自动_普通", "把 uvicorn 的 reload 开关调通了。")
_m = read_meta("自动_普通")
check("C3 普通内容 ⇒ internal/reader", (_m.get("security"), _m.get("clearance")) == ("internal", "reader"),
      f"{_m.get('security')}/{_m.get('clearance')}")
check("C4 与默认档一致时不打扰调用方", "已自动分级" not in _r, _r[:120])

_r = server.memory_write("自动_显式", "手机号 13812345678", security="internal")
_m = read_meta("自动_显式")
check("C5 显式 security 优先于自动判定", _m.get("security") == "internal",
      f"{_m.get('security')}")
check("C6 显式优先时不得谎报「已自动分级」", "已自动分级" not in _r, _r[:120])

server.memory_write("自动_已标", "普通内容。", security="internal")
_r = server.memory_write("自动_已标", "补充：联系人 13812345678。")
_m = read_meta("自动_已标")
check("C7 已有显式档位 ⇒ 不自动改盘", _m.get("security") == "internal", f"{_m.get('security')}")
check("C8 但会提示可收紧（供人工采纳）", "建议收紧" in _r, _r[:200])

_r = server.memory_write("近期工作动态", "# 近期工作动态\n\n摘要里出现手机号 13812345678。")
_m = read_meta("近期工作动态")
check("C9 核心页豁免：导航页不被判成 sensitive",
      (_m.get("security"), _m.get("clearance")) == ("internal", "reader"),
      f"{_m.get('security')}/{_m.get('clearance')}")

# ============================================================
print("\n== D. memory_access_suggest：预览 / 执行 ==")
# ============================================================
_preview = server.memory_access_suggest()
check("D1 默认预览标注未改动数据", "未改动任何数据" in _preview, _preview[-200:])
check("D2 预览列出收紧建议", "收紧建议" in _preview, _preview[:200])
check("D3 预览未改盘（已标条目仍是 internal）",
      read_meta("自动_已标").get("security") == "internal")

_applied = server.memory_access_suggest(apply=True)
check("D4 apply=True 执行并回报", "已执行" in _applied, _applied[:200])
check("D5 执行后已标条目被收紧到 sensitive",
      read_meta("自动_已标").get("security") == "sensitive",
      str(read_meta("自动_已标").get("security")))

# ============================================================
print("\n== E. 总开关 AI_MEMORY_AUTO_ACCESS=off ==")
# ============================================================
os.environ["AI_MEMORY_AUTO_ACCESS"] = "off"
try:
    _r = server.memory_write("自动_开关关", "联系人 13812345678。")
    _m = read_meta("自动_开关关")
    check("E1 关闭后不自动分级 ⇒ internal/reader",
          (_m.get("security"), _m.get("clearance")) == ("internal", "reader"),
          f"{_m.get('security')}/{_m.get('clearance')}")
    check("E2 关闭后无自动分级提示", "已自动分级" not in _r, _r[:120])
finally:
    os.environ.pop("AI_MEMORY_AUTO_ACCESS", None)

# ============================================================
print()
if _failed:
    print(f"==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    print("失败项:")
    for item in _failed:
        print(f"  - {item}")
    sys.exit(1)
print(f"==== 结果: {len(_passed)} passed / 0 failed ====")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0)
