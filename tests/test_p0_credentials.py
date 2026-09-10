#!/usr/bin/env python3
"""P0 可信度字段专项测试 for ai-memory MCP server (server.py).

背景:
    2026-09-10 新增 type / confidence / verified / verified_at 四个 frontmatter
    字段, 用于区分「用户明说的事实」与「AI 自行推断」, 遏制 Memory Poisoning
    (AI 推测被写入后被后续会话当成事实复用)。

本脚本覆盖:
    - 写入落盘: 显式取值 / 缺省默认值
    - 读取输出: CORE 元数据带出四个字段
    - 更新语义: 未传字段时继承原值(不被重置), 显式传入时覆盖
    - 非法取值: 被拒绝且不落盘
    - 向后兼容: 无字段的历史笔记读取时注入默认值, 写入时渐进补齐
    - verified_at: 置 true 写入日期, 置 false 清除日期
    - 检索/统计: 按 type 过滤 与 类型分布统计

运行:
    python tests/test_p0_credentials.py     # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
    本脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-p0-test-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402   (此时 server.MEMORY_DIR == _TMP)
import yaml_io  # noqa: E402

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


def raw_meta(title: str) -> dict:
    """读磁盘原始 frontmatter —— 绕过 _apply_meta_defaults 的默认值注入。"""
    for f in list(server.MEMORY_DIR.glob("*.md")):
        if f.stem == title:
            meta, _ = server._parse_frontmatter(server._read_text(f))
            return meta
    return {}


def main() -> int:
    # ── K1-K4 写入落盘 ────────────────────────────────────────────────
    section("K. 写入落盘")

    server.memory_write(title="P0显式取值", content="正文甲", mem_type="decision",
                        confidence="high", verified=True)
    m = raw_meta("P0显式取值")
    check("K1 显式 mem_type 落盘为 type", m.get("type") == "decision", f"got={m.get('type')!r}")
    check("K2 显式 confidence 落盘", m.get("confidence") == "high", f"got={m.get('confidence')!r}")
    check("K3 verified=true 落盘且带 verified_at",
          m.get("verified") is True and bool(m.get("verified_at")),
          f"verified={m.get('verified')!r} at={m.get('verified_at')!r}")

    server.memory_write(title="P0缺省取值", content="正文乙")
    m2 = raw_meta("P0缺省取值")
    check("K4 缺省时回落 fact/medium/false",
          m2.get("type") == "fact" and m2.get("confidence") == "medium"
          and m2.get("verified") is False and "verified_at" not in m2,
          f"type={m2.get('type')!r} conf={m2.get('confidence')!r} ver={m2.get('verified')!r}")

    # ── K5 读取输出 ───────────────────────────────────────────────────
    section("K. 读取输出")

    out = server.memory_read("P0显式取值")
    check("K5 memory_read 输出含 type/confidence/verified",
          "type" in out and "decision" in out and "confidence" in out
          and "high" in out and "verified" in out,
          f"out[:200]={out[:200]!r}")

    # ── K6-K8 更新语义 ────────────────────────────────────────────────
    section("K. 更新语义")

    server.memory_write(title="P0显式取值", content="正文甲（改）")
    m3 = raw_meta("P0显式取值")
    check("K6 更新未传字段时继承原值（不重置为默认）",
          m3.get("type") == "decision" and m3.get("confidence") == "high"
          and m3.get("verified") is True,
          f"type={m3.get('type')!r} conf={m3.get('confidence')!r} ver={m3.get('verified')!r}")

    server.memory_write(title="P0显式取值", content="正文甲（再改）", confidence="low")
    m4 = raw_meta("P0显式取值")
    check("K7 显式传 confidence 时覆盖生效",
          m4.get("confidence") == "low" and m4.get("type") == "decision",
          f"conf={m4.get('confidence')!r} type={m4.get('type')!r}")

    r = server.memory_write(title="P0非法类型", content="正文丙", mem_type="banana")
    check("K8 非法 mem_type 被拒绝且不落盘",
          "错误" in r and not (server.MEMORY_DIR / "P0非法类型.md").exists(),
          f"resp={r[:80]!r}")

    r2 = server.memory_write(title="P0非法可信度", content="正文丁", confidence="very-high")
    check("K9 非法 confidence 被拒绝且不落盘",
          "错误" in r2 and not (server.MEMORY_DIR / "P0非法可信度.md").exists(),
          f"resp={r2[:80]!r}")

    # ── K10-K12 向后兼容 ──────────────────────────────────────────────
    section("K. 向后兼容（历史笔记无字段）")

    legacy = server.MEMORY_DIR / "P0历史笔记.md"
    legacy.write_text(
        "---\n"
        "title: P0历史笔记\n"
        "tags: [legacy]\n"
        "created: 2026-01-01T00:00:00+00:00\n"
        "updated: 2026-01-01T00:00:00+00:00\n"
        "tier: warm\n"
        "---\n\n"
        "这是 2026-01-01 写入的旧笔记，frontmatter 里没有任何 P0 字段。\n",
        encoding="utf-8",
    )
    raw = raw_meta("P0历史笔记")
    check("K10a 历史文件磁盘上确实无 P0 字段",
          "type" not in raw and "confidence" not in raw and "verified" not in raw,
          f"keys={sorted(raw)}")

    out_legacy = server.memory_read("P0历史笔记")
    check("K10b 读取历史笔记时注入默认值 fact/medium/false",
          "fact" in out_legacy and "medium" in out_legacy,
          f"out[:200]={out_legacy[:200]!r}")

    server.memory_update_metadata("P0历史笔记", tier="hot")
    raw_after = raw_meta("P0历史笔记")
    check("K11 历史笔记被写入后渐进补齐字段",
          raw_after.get("type") == "fact" and raw_after.get("confidence") == "medium"
          and raw_after.get("verified") is False,
          f"type={raw_after.get('type')!r} conf={raw_after.get('confidence')!r}")

    # ── K12 verified_at 生命周期 ──────────────────────────────────────
    section("K. verified_at 生命周期")

    server.memory_write(title="P0核验流转", content="正文戊", verified=True)
    has_at = bool(raw_meta("P0核验流转").get("verified_at"))
    server.memory_update_metadata("P0核验流转", verified=False)
    m5 = raw_meta("P0核验流转")
    check("K12a verified=true 时写入 verified_at", has_at)
    check("K12b 置 false 时 verified_at 被清除",
          m5.get("verified") is False and "verified_at" not in m5,
          f"ver={m5.get('verified')!r} at={m5.get('verified_at')!r}")

    server.memory_update_metadata("P0核验流转", verified=True)
    m6 = raw_meta("P0核验流转")
    check("K12c 置回 true 时重新写入 verified_at",
          m6.get("verified") is True and bool(m6.get("verified_at")),
          f"ver={m6.get('verified')!r} at={m6.get('verified_at')!r}")

    # ── K13-K14 检索过滤 ──────────────────────────────────────────────
    section("K. 检索与统计")

    server.memory_write(title="P0偏好样本", content="偏好样本正文", mem_type="preference")
    server.memory_write(title="P0事件样本", content="事件样本正文", mem_type="episodic")

    res = server.memory_search("P0", mem_type="preference", limit=50)
    check("K13a memory_search 按 mem_type 过滤（命中偏好样本）",
          "P0偏好样本" in res, f"res={res[:200]!r}")
    check("K13b memory_search 过滤排除非目标类型",
          "P0事件样本" not in res, f"res={res[:200]!r}")

    lst = server.memory_list(mem_type="episodic", limit=50)
    check("K13c memory_list 按 mem_type 过滤",
          "P0事件样本" in lst and "P0偏好样本" not in lst,
          f"lst={lst[:200]!r}")

    smart = server.memory_smart_search("样本正文", mem_type="preference", limit=20)
    check("K13d memory_smart_search 按 mem_type 过滤",
          "P0偏好样本" in smart and "P0事件样本" not in smart,
          f"smart={smart[:200]!r}")

    badge = server._credential_badge({"type": "decision", "confidence": "high", "verified": True})
    check("K13e 徽章正确反映可信度三态",
          "type=decision" in badge and "conf=high" in badge and "verified" in badge,
          f"badge={badge!r}")

    stats = server.memory_stats()
    check("K14a memory_stats 输出记忆类型分布",
          "记忆类型分布" in stats and "preference" in stats and "episodic" in stats,
          f"stats含类型分布={('记忆类型分布' in stats)}")
    check("K14b memory_stats 输出可信度分布与核验计数",
          "可信度分布" in stats and "已核验 verified" in stats,
          f"stats含可信度分布={('可信度分布' in stats)}")

    # ── K15 序列化层边界 ──────────────────────────────────────────────
    section("K. 序列化层边界")

    fm = yaml_io.build_frontmatter("T", [], "test", mem_type="not-a-type", confidence="ultra")
    check("K15 非法值在序列化层回落默认（防御性兜底）",
          "type: fact" in fm and "confidence: medium" in fm,
          f"fm={fm!r}")

    fm2 = yaml_io.build_frontmatter("T", [], "test", mem_type="workflow",
                                    confidence="low", verified=False)
    check("K15b verified=false 时序列化为 false 而非 null",
          "verified: false" in fm2 and "type: workflow" in fm2,
          f"fm2={fm2!r}")

    print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    if _failed:
        print("失败项:")
        for name in _failed:
            print(f"  - {name}")
    return 1 if _failed else 0


if __name__ == "__main__":
    try:
        _code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(_code)
