#!/usr/bin/env python3
"""跨进程文件锁。

从 server.py 拆分而来（P3 模块化，2026-09-10）。本模块是**无状态工具层**：
锁文件路径由调用方传入，不依赖 MEMORY_DIR 等全局配置。

设计要点：
    - 跨进程：多个 AI 工具（WorkBuddy / Claude / Codex）可能同时写同一个
      记忆库，靠 lock 文件的存在性做互斥。
    - 可重入：同一线程内嵌套获取（如 memory_write -> _maybe_auto_archive
      -> memory_archive）只递增深度计数，不重复创建锁文件，否则自死锁。
      重入状态用 threading.local() 按线程隔离。
    - 抗陈旧：持有者进程崩溃会留下锁文件。超过 stale_timeout 秒的锁
      视为陈旧并强制回收，避免永久阻塞。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

__all__ = ["DEFAULT_LOCK_TIMEOUT", "acquire_lock", "release_lock", "reset_lock_state"]

# 锁文件最长持有时间（秒）：超过视为陈旧锁并回收
DEFAULT_LOCK_TIMEOUT = 15

# 每线程的重入深度（0 / 未设置为未持有）
_lock_state = threading.local()


def acquire_lock(lock_path: Path, stale_timeout: float = DEFAULT_LOCK_TIMEOUT) -> bool:
    """获取跨进程文件锁，返回是否成功。

    同线程可重入：已持有时仅递增深度计数并直接返回 True。
    等待超过 stale_timeout 秒仍未获取则返回 False（由调用方决定如何处理）。
    """
    if getattr(_lock_state, "depth", 0) > 0:
        _lock_state.depth += 1
        return True
    start = time.time()
    while True:
        try:
            with open(lock_path, "x", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.time()}, f)
            _lock_state.depth = 1
            return True
        except FileExistsError:
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if time.time() - data.get("time", 0) > stale_timeout:
                    lock_path.unlink(missing_ok=True)
                    continue
            except (json.JSONDecodeError, OSError):
                lock_path.unlink(missing_ok=True)
                continue
            time.sleep(0.2)
            if time.time() - start > stale_timeout:
                return False
    return False


def release_lock(lock_path: Path) -> None:
    """释放锁。重入深度 >1 时只递减，归零时才真正删除锁文件。"""
    depth = getattr(_lock_state, "depth", 0)
    if depth > 1:
        _lock_state.depth = depth - 1
        return
    _lock_state.depth = 0
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def reset_lock_state() -> None:
    """清空当前线程的重入深度（仅测试用）。"""
    _lock_state.depth = 0
