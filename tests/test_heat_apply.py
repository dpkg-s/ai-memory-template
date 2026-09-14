#!/usr/bin/env python3
"""热度驱动自动升降级测试（演进 #3）。

覆盖 memory_heat_suggest(apply=...) 的预览 / 执行双模式：
    - 判据 _heat_tier_decision 的各分支与核心页豁免
    - **预览不写盘**（最关键的安全属性）
    - apply=True 真正写盘且 tier 改变
    - 幂等：已调整过的条目不再重复调整
    - **v2 字段不丢**（type/confidence/scope/project/status 批量改 tier 后仍在）
    - 核心页不被自动降级
    - 所见即所改：预览条数 == 实际执行条数

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_heat_apply.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ai-memory-heat-")
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


def _path_of(title: str) -> Path:
    # _safe_filename 已带 .md 扩展名
    return server.MEMORY_DIR / server._safe_filename(title)


def set_heat(title: str, reads: int, days_ago: int, tier: str | None = None) -> Path:
    """把条目改造成指定热度：直接改写 frontmatter 的 updated / access_count / tier。

    memory_write 不接受 updated 参数（总写当前时间），所以先用它建出**格式合法**
    的条目，再定向改写这三行 —— 比手写整段 frontmatter 更不易格式错。
    """
    p = _path_of(title)
    txt = p.read_text(encoding="utf-8")
    updated = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.000000+00:00"
    )
    txt = re.sub(r"^updated: .*$", f'updated: "{updated}"', txt, count=1, flags=re.M)
    txt = re.sub(r"^access_count: .*$", f"access_count: {reads}", txt, count=1, flags=re.M)
    if tier is not None:
        if re.search(r"^tier: .*$", txt, flags=re.M):
            txt = re.sub(r"^tier: .*$", f"tier: {tier}", txt, count=1, flags=re.M)
        else:
            txt = txt.replace("---\n\n", f"tier: {tier}\n---\n\n", 1)
    p.write_text(txt, encoding="utf-8")
    server._invalidate_cache()
    return p


def read_fm(title: str) -> dict:
    meta, _body = server._load_memory(_path_of(title))
    return meta


def main() -> int:
    print("== A. 判据 _heat_tier_decision（纯函数）==")
    d = server._heat_tier_decision
    check("A1 低分非 cold → cold", d("普通笔记A", "warm", 0.0) == "cold", repr(d("普通笔记A", "warm", 0.0)))
    check("A2 高分非 hot → hot", d("普通笔记B", "warm", 5.0) == "hot", repr(d("普通笔记B", "warm", 5.0)))
    check("A3 中低分且 hot → warm", d("普通笔记C", "hot", 0.3) == "warm", repr(d("普通笔记C", "hot", 0.3)))
    check("A4 中间分 → 维持现状", d("普通笔记D", "warm", 1.5) is None, repr(d("普通笔记D", "warm", 1.5)))
    check("A5 已是 cold 且低分 → 幂等", d("普通笔记E", "cold", 0.0) is None, repr(d("普通笔记E", "cold", 0.0)))
    check("A6 已是 hot 且高分 → 幂等", d("普通笔记F", "hot", 5.0) is None, repr(d("普通笔记F", "hot", 5.0)))
    check("A7 核心页低分仍豁免", d("用户画像", "hot", 0.0) is None, repr(d("用户画像", "hot", 0.0)))
    check("A8 核心页高分仍豁免", d("记忆索引", "warm", 99.0) is None, repr(d("记忆索引", "warm", 99.0)))

    print("\n== B. 预览（apply=False，默认）不写盘 ==")
    server.memory_write("热度测试_冷", "这条会被判定为冷。", tags=["test"])
    set_heat("热度测试_冷", reads=0, days_ago=30, tier="warm")
    out = server.memory_heat_suggest()          # 不传参 = 默认预览
    check("B1 默认调用即预览", "未执行" in out, out[-260:])
    check("B2 预览列出待调整条目", "热度测试_冷" in out, out[-400:])
    check("B3 预览明确标注未改动数据", "未改动任何数据" in out, out[-260:])
    check("B4 预览后磁盘 tier 未变", read_fm("热度测试_冷").get("tier") == "warm",
          str(read_fm("热度测试_冷").get("tier")))

    print("\n== C. 执行（apply=True）真正写盘 ==")
    n_preview = len(re.findall(r"^- \[", out.split("## 待调整")[-1], flags=re.M)) if "## 待调整" in out else 0
    out_apply = server.memory_heat_suggest(apply=True)
    check("C1 输出含已执行标记", "已执行" in out_apply, out_apply[:200])
    check("C2 磁盘 tier 已降为 cold", read_fm("热度测试_冷").get("tier") == "cold",
          str(read_fm("热度测试_冷").get("tier")))
    check("C3 已执行清单含该条", "热度测试_冷" in out_apply, out_apply[:400])
    n_apply = int(re.search(r"## 已执行（(\d+) 条）", out_apply).group(1))
    check("C4 所见即所改：预览条数 == 执行条数", n_preview == n_apply,
          f"preview={n_preview} apply={n_apply}")

    print("\n== D. 幂等：已调整过的条目不再重复调整 ==")
    out2 = server.memory_heat_suggest(apply=True)
    check("D1 二次执行不再重复调整该条", "热度测试_冷:" not in out2, out2[:400])
    check("D2 tier 保持 cold", read_fm("热度测试_冷").get("tier") == "cold",
          str(read_fm("热度测试_冷").get("tier")))

    print("\n== E. v2 语义字段在批量改 tier 后不丢 ==")
    server.memory_write("热度测试_字段", "带完整 v2 字段的条目。", tags=["test"],
                        mem_type="preference", confidence="high",
                        scope="project", project="测试项目", status="active")
    set_heat("热度测试_字段", reads=0, days_ago=30, tier="warm")
    server.memory_heat_suggest(apply=True)
    fm = read_fm("热度测试_字段")
    check("E1 tier 已降为 cold", fm.get("tier") == "cold", str(fm.get("tier")))
    check("E2 type 保留", fm.get("type") == "preference", str(fm.get("type")))
    check("E3 confidence 保留", fm.get("confidence") == "high", str(fm.get("confidence")))
    check("E4 scope 保留", fm.get("scope") == "project", str(fm.get("scope")))
    check("E5 project 保留", fm.get("project") == "测试项目", str(fm.get("project")))
    check("E6 status 保留", fm.get("status") == "active", str(fm.get("status")))
    check("E7 schema_version 保留为 2", str(fm.get("schema_version")) == "2",
          str(fm.get("schema_version")))

    print("\n== F. 核心页不被自动降级 ==")
    server.memory_write("用户画像", "核心页，不该被自动改 tier。", tags=["core"])
    set_heat("用户画像", reads=0, days_ago=60, tier="hot")
    server.memory_heat_suggest(apply=True)
    check("F1 核心页 tier 未被自动降级", read_fm("用户画像").get("tier") == "hot",
          str(read_fm("用户画像").get("tier")))
    out3 = server.memory_heat_suggest()
    check("F2 报告中说明了核心页豁免数", "核心页豁免" in out3, out3[:200])

    print("\n== G. 边界：库为空时不应报错 ==")
    empty = tempfile.mkdtemp(prefix="ai-memory-heat-empty-")
    saved = server.MEMORY_DIR
    try:
        server.MEMORY_DIR = Path(empty)
        server._invalidate_cache()
        o = server.memory_heat_suggest()
        check("G1 空库预览不报错", "暂无需要调整" in o, o[:200])
        o2 = server.memory_heat_suggest(apply=True)
        check("G2 空库执行不报错", "暂无需要调整" in o2, o2[:200])
    finally:
        server.MEMORY_DIR = saved
        server._invalidate_cache()
        shutil.rmtree(empty, ignore_errors=True)

    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
