#!/usr/bin/env python3
"""冲突 / 重复主动防护测试（演进 #1）。

覆盖 memory_write 的疑似重复/冲突提示（非阻断）：
    - 标题相近 → 「疑似重复标题」
    - 正文相近（不同结论，模拟冲突）→ 「疑似重复/冲突内容」
    - 完全无关 → 不触发
    - 同名 upsert（更新自己）→ 不触发
    - 防护逻辑异常时不影响正常写入（容错）

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_dup_guard.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ai-memory-dup-")
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


def main() -> int:
    print("== A. 标题相近 ==")
    server.memory_write("项目A_技术栈", "项目 A 使用 Vue 2 作为前端框架。", tags=["project"])
    out = server.memory_write("项目A_技术栈记录", "项目 A 目前用 Vue 3 进行开发。", tags=["project"])
    check("A1 触发疑似重复标题提示", "疑似重复标题" in out, out[:160])
    check("A2 提示指向旧条目", "项目A_技术栈" in out, out[:160])
    check("A3 提示标注非阻断", "未阻断" in out, out[:160])
    check("A4 写入本身仍成功", "已创建记忆" in out, out[:80])

    print("\n== B. 正文相近（模拟冲突）==")
    out = server.memory_write("项目B_部署方式",
                              "项目 B 的数据库采用 PostgreSQL，部署在本地服务器上。",
                              tags=["project"])
    check("B1 首条不触发提示", "疑似" not in out, out[:160])
    out = server.memory_write("项目B_部署方式v2",
                              "项目 B 的数据库已经迁移到 MySQL，部署在云端容器上。",
                              tags=["project"])
    # 标题「项目B_部署方式」vs「项目B_部署方式v2」高度相似（>标题阈值），
    # 会优先命中「疑似重复标题」分支；正文相似只作次级判定。
    check("B2 触发疑似重复提示（标题优先）", "疑似重复" in out, out[:200])
    check("B3 提示指向旧条目", "项目B_部署方式" in out, out[:200])

    # 标题完全不同、但正文高度相似（大量共享句式，仅关键词不同）→ 命中「冲突内容」
    server.memory_write("开发语言偏好",
                        "用户的主开发语言是 Python，用于日常脚本与数据分析。", tags=["fact"])
    out = server.memory_write("编程语言使用记录",
                              "用户的主开发语言是 JavaScript，用于日常脚本与数据分析。",
                              tags=["fact"])
    check("B4 正文高度相似命中冲突内容", "疑似重复/冲突内容" in out, out[:200])

    print("\n== C. 完全无关不误报 ==")
    out = server.memory_write("备忘_买牛奶", "明天记得买牛奶。")
    check("C1 无关内容不触发", "疑似" not in out, out[:160])
    out = server.memory_write("备忘_订机票", "下周三飞北京的航班记得提前值机。")
    check("C2 另一条无关内容不触发", "疑似" not in out, out[:160])

    print("\n== D. 同名 upsert 不触发自身重复 ==")
    out = server.memory_write("项目A_技术栈", "项目 A 已经迁移到 Vue 3，并升级了构建工具。",
                              tags=["project"])
    # upsert 自己不触发「疑似重复标题」；但因库里还有「项目A_技术栈记录」，
    # 相似条目提醒仍然会出现（这是合理的），故只断言不指向自身。
    check("D1 更新自己不触发「指向自身」的重复", "[[项目A_技术栈]]" not in out, out[:240])

    print("\n== E. 容错：防护逻辑异常不影响写入 ==")
    _orig = server._find_duplicate_hints

    def _boom(*a, **k):
        raise RuntimeError("boom")

    server._find_duplicate_hints = _boom
    try:
        out = server.memory_write("备忘_容错测试", "这条记忆用于验证防护异常时仍能写入。")
        check("E1 防护异常时写入仍成功", "已创建记忆" in out, out[:160])
    finally:
        server._find_duplicate_hints = _orig

    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
