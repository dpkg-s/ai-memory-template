#!/usr/bin/env python3
"""回收站 (.trash) 闭环测试：软删除 → 列出 → 恢复 → 清理。

覆盖:
    memory_delete(trash / purge / empty_trash)
    memory_list(include_trash=True)
    memory_restore(source="trash")

验证要点:
    - 软删除后条目退出根目录与检索，但文件仍在 .trash/ 可恢复
    - 恢复后检索 / 索引 / 生命周期字段(status) 正确，tier 不被改动
    - 根目录已存在同标题时**拒绝**恢复，绝不静默覆盖现存内容
    - 同名的两条软删除在回收站内并存（不互相覆盖）
    - 不影响既有的 .archive 恢复路径（source 默认值向后兼容）

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_trash.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-trash-")
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


def trash_files() -> list[Path]:
    d = Path(_TMP) / ".trash"
    return sorted(d.glob("*.md")) if d.exists() else []


def root_files() -> list[Path]:
    return sorted(Path(_TMP).glob("*.md"))


def root_has(stem: str) -> bool:
    return any(f.stem == stem for f in root_files())


def meta_of(stem: str) -> dict:
    for f in root_files():
        if f.stem == stem:
            return server._load_memory(f)[0]
    return {}


def main() -> int:
    print("== A. 软删除基础 ==")
    server.memory_write("回收站_A", "回收站测试条目的正文内容。", tags=["trash-test"])
    check("A1 写入后进入根目录", root_has("回收站_A"))
    out = server.memory_delete("回收站_A")
    check("A2 软删除返回提到 .trash", ".trash" in out, out)
    check("A3 根目录已移除", not root_has("回收站_A"))
    check("A4 文件出现在回收站", len(trash_files()) == 1, str([f.name for f in trash_files()]))
    _s_after = server.memory_search("回收站_A")
    check("A5 已退出检索", "[回收站_A] (" not in _s_after, _s_after[:160])
    check("A6 已退出列表", "回收站_A" not in server.memory_list(limit=100))

    print("\n== B. 列出回收站 ==")
    o = server.memory_list(include_trash=True)
    check("B1 回收站视图含该条目", "回收站_A" in o, o[:140])
    check("B2 视图含取回指引", "memory_restore" in o, o[:140])
    check("B3 视图含清空指引", "empty_trash" in o, o[:140])

    print("\n== C. 从回收站恢复 ==")
    o = server.memory_restore("回收站_A", source="trash")
    check("C1 恢复成功", "已恢复" in o, o)
    check("C2 文件回到根目录", root_has("回收站_A"))
    check("C3 回收站中已移除", len(trash_files()) == 0, str([f.name for f in trash_files()]))
    check("C4 恢复后可读且正文完整", "正文内容" in server.memory_read("回收站_A"))
    check("C5 恢复后可检索", "回收站_A" in server.memory_search("回收站_A"))
    m = meta_of("回收站_A")
    check("C6 status 回到 active", m.get("status") == "active", str(m.get("status")))
    check("C7 schema_version 升到当前", m.get("schema_version") == server.SCHEMA_VERSION,
          str(m.get("schema_version")))
    check("C8 tags 保留", "trash-test" in (m.get("tags") or []), str(m.get("tags")))

    print("\n== D. 根目录同名冲突保护 ==")
    server.memory_write("回收站_D", "第一版内容。")
    server.memory_delete("回收站_D")            # 第一版进回收站
    server.memory_write("回收站_D", "第二版内容。")  # 根目录重建同名条目
    o = server.memory_restore("回收站_D", source="trash")
    check("D1 拒绝恢复并给出原因", "未恢复" in o, o)
    check("D2 现存条目未被覆盖", "第二版内容" in server.memory_read("回收站_D"))
    check("D3 回收站内那份仍在", any(f.stem == "回收站_D" for f in trash_files()))

    print("\n== E. 未找到 / 空回收站 ==")
    o = server.memory_restore("不存在的条目_ZZZ", source="trash")
    check("E1 未找到给出提示", "未找到" in o, o)

    print("\n== F. purge 与 empty_trash ==")
    server.memory_delete("回收站_D", purge=True)          # 删根目录那份
    server.memory_delete("回收站_D", purge=True)          # 再删回收站那份
    check("F1 purge 不进回收站", not any(f.stem == "回收站_D" for f in trash_files()),
          str([f.name for f in trash_files()]))
    check("F2 purge 后根目录也没有", not root_has("回收站_D"))

    server.memory_write("回收站_F1", "F1 内容")
    server.memory_write("回收站_F2", "F2 内容")
    server.memory_delete("回收站_F1")
    server.memory_delete("回收站_F2")
    n_before = len(trash_files())
    o = server.memory_delete("__ignored__", empty_trash=True)
    check("F3 清空返回被删数量", "已清空回收站" in o and str(n_before) in o, f"{o} / n={n_before}")
    check("F4 回收站已空", len(trash_files()) == 0)
    o2 = server.memory_delete("__ignored__", empty_trash=True)
    check("F5 对空回收站再清空给出提示", "为空" in o2, o2)
    check("F6 列表显示回收站为空", "回收站为空" in server.memory_list(include_trash=True))

    print("\n== G. 同名多次软删除并存 ==")
    server.memory_write("回收站_G", "G 第一次")
    server.memory_delete("回收站_G")
    server.memory_write("回收站_G", "G 第二次")
    server.memory_delete("回收站_G")
    same = [f.name for f in trash_files() if f.stem.startswith("回收站_G")]
    check("G1 两条并存未被覆盖", len(same) == 2, str(same))

    print("\n== H. source 参数校验 ==")
    o = server.memory_restore("任意标题", source="bogus")
    check("H1 非法 source 被拒绝", "错误" in o, o)

    print("\n== I. 不影响既有 .archive 恢复路径 ==")
    server.memory_write("回收站_I", "I 的正文内容。", tags=["keep-me"])
    _o_arch = server.memory_archive("回收站_I")
    check("I1 归档动作成功并指向 .archive", ".archive" in _o_arch, _o_arch)
    check("I1b 根目录只剩归档 stub（含归档提示）",
          "已归档" in server.memory_read("回收站_I"),
          server.memory_read("回收站_I")[:120])
    o = server.memory_restore("回收站_I")      # 默认 source="archive"
    check("I2 默认仍从 archive 恢复", "已恢复" in o and "archive" in o, o)
    m = meta_of("回收站_I")
    check("I3 archive 恢复后 tier=warm", m.get("tier") == "warm", str(m.get("tier")))
    check("I4 archive 恢复后正文完整", "I 的正文内容" in server.memory_read("回收站_I"))

    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
