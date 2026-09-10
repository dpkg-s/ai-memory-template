#!/usr/bin/env python3
"""并发 / 恢复测试的工人进程（非测试用例，由 tests/test_*.py 以 subprocess 调用）。

为什么单独放一个文件：
    tests/test_concurrency.py 与 tests/test_recovery.py 都需要**真实子进程**才能
    验证跨进程语义 —— 线程共享进程内状态（locks.py 的 threading.local 重入计数、
    server 的 _ENTRY_CACHE / _CACHE_VALID），既造不出真实的「锁文件残留」，也模拟
    不了「写入中途进程被 kill」。把子进程逻辑集中在这里，避免测试文件里拼长串
    的 ``python -c`` 代码。

契约：
    python tests/concurrency_worker.py <payload.json> <result.json>

    payload（UTF-8 JSON）: {"op": <见下>, ...}
        write        title, content, tags?, source?, expected_version?
        update_meta  title, kwargs?（透传给 memory_update_metadata）
        archive      title
        restore      title
        delete       title, trash?, purge?
        read         title            -> 完整正文（max_chars=0）
        read_loop    title, count?, delay?  -> 连续读取 count 次，返回 reads 列表
        stats        -
    所有 op 均可附加 ``sleep_before``（秒）：进入 op 前先休眠，供恢复测试在
    「导入完成但尚未写入」的窗口内 kill 进程，验证崩溃不留损坏。
    result（UTF-8 JSON）: {"op", "ok", "result", "error", "duration", "reads"?}

    用**文件**而非命令行参数传递数据，规避 Windows 命令行编码差异；子进程的
    ``AI_MEMORY_DIR`` 由调用方通过环境变量传入，保证不触碰真实记忆库。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))


def _run(payload: dict) -> dict:
    import server  # 延迟导入：确保 AI_MEMORY_DIR 已由父进程设置好

    wait = float(payload.get("sleep_before", 0) or 0)
    if wait:
        time.sleep(wait)  # 恢复测试在此时窗口内 kill 本进程，验证崩溃不留损坏

    op = payload.get("op", "")
    title = payload.get("title", "")

    if op == "write":
        return {"result": server.memory_write(
            title,
            payload.get("content", ""),
            tags=payload.get("tags"),
            source=payload.get("source"),
            expected_version=payload.get("expected_version"),
        )}
    if op == "update_meta":
        return {"result": server.memory_update_metadata(title, **payload.get("kwargs", {}))}
    if op == "archive":
        return {"result": server.memory_archive(title)}
    if op == "restore":
        return {"result": server.memory_restore(title)}
    if op == "delete":
        return {"result": server.memory_delete(
            title,
            trash=bool(payload.get("trash", False)),
            purge=bool(payload.get("purge", True)),
        )}
    if op == "read":
        return {"result": server.memory_read(title, max_chars=0)}
    if op == "read_loop":
        reads: list[str] = []
        delay = float(payload.get("delay", 0))
        for _ in range(int(payload.get("count", 20))):
            reads.append(server.memory_read(title, max_chars=0))
            if delay:
                time.sleep(delay)
        return {"result": f"read {len(reads)} times", "reads": reads}
    if op == "stats":
        return {"result": server.memory_stats()}
    raise ValueError(f"未知 op: {op!r}")


def main() -> int:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    started = time.time()
    out: dict = {"op": payload.get("op"), "ok": True, "result": "", "error": None,
                 "duration": 0.0}
    try:
        out.update(_run(payload))
    except Exception as e:  # noqa: BLE001 - 测试目的就是捕捉一切异常并回报给父进程
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    out["duration"] = round(time.time() - started, 4)
    Path(sys.argv[2]).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
