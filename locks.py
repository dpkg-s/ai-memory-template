#!/usr/bin/env python3
"""跨进程文件锁。

从 server.py 拆分而来（P3 模块化，2026-09-10）。本模块是**无状态工具层**：
锁文件路径由调用方传入，不依赖 MEMORY_DIR 等全局配置。
唯一的例外是陈旧回收秒数 —— B5（2026-10-08）起取自 `memory_config`
（vault 根 `memory.toml` 的 `lock_timeout`）；`memory_config` 本身是最底层、
不反向依赖任何模块，故不破坏分层。

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

import memory_config

__all__ = [
    "DEFAULT_LOCK_TIMEOUT",
    "acquire_lock",
    "release_lock",
    "reset_lock_state",
    "current_depth",
]

# 锁文件最长持有时间（秒）：超过视为陈旧锁并回收。
# B5（2026-10-08）：真值已外置到 memory.toml 的 `lock_timeout`。本常量保留为
# **向后兼容别名 / 文档性默认值**，但**不再用作 acquire_lock 的默认参数** ——
# 默认参数在 `def` 期求值一次，那样改配置文件将永远不生效（B5 热重载的硬要求）。
DEFAULT_LOCK_TIMEOUT = memory_config.DEFAULTS["lock_timeout"]

# 每线程的重入深度（0 / 未设置为未持有）
_lock_state = threading.local()


def _unlink_quiet(path: Path) -> None:
    """尽力删除锁文件，失败静默（下一轮重试会接管）。

    为什么不能裸调 ``unlink``：Windows 下删除「正被其他进程打开」的文件会抛
    ``PermissionError``（WinError 5 / 删除共享冲突）。该异常原先会从
    ``acquire_lock`` 里**逃逸出去**，把并发写变成随机崩溃（而不是「没抢到锁」）。
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def acquire_lock(lock_path: Path, stale_timeout: float | None = None) -> bool:
    """获取跨进程文件锁，返回是否成功。

    同线程可重入：已持有时仅递增深度计数并直接返回 True。
    等待超过 stale_timeout 秒仍未获取则返回 False（由调用方决定如何处理）。

    Args:
        stale_timeout: 陈旧锁回收秒数。``None``（默认）= 取 ``memory.toml`` 的
            ``lock_timeout``（内置默认 15）。**每次调用只解析一次**（在循环外），
            免得在抢锁自旋里反复 stat 配置文件。

    Windows 陷阱（2026-10-12 修复）：
        ``open(path, "x")`` 落到 O_CREAT|O_EXCL。POSIX 下目标已存在 ⇒ EEXIST ⇒
        ``FileExistsError``；**Windows 下目标已存在且被其他进程持有时**返回
        ERROR_ACCESS_DENIED(5) ⇒ ``PermissionError``。只捕获 FileExistsError 会让
        后者逃逸 —— 调用方拿到的是异常而不是「抢锁失败」，并发写随即崩掉。
        故两者必须一并按「锁被占用」处理。同类问题也存在于 unlink 侧（见
        ``_unlink_quiet``）。
    """
    if stale_timeout is None:
        stale_timeout = memory_config.get("lock_timeout")
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
        except (FileExistsError, PermissionError):
            pass  # 锁被占用：交给下面的陈旧检测
        # 读内容判是否陈旧。读不到 / 解析不了 ⇒ 按损坏锁回收（R3c 口径）；
        # 但**回收动作本身**也可能因占用失败，必须静默，绝不让异常逃逸。
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stale = time.time() - data.get("time", 0) > stale_timeout
        except (json.JSONDecodeError, OSError):
            stale = True
        if stale:
            _unlink_quiet(lock_path)
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


def current_depth() -> int:
    """当前线程的锁重入深度（0 表示未持锁）。

    调用方据此区分「真实抢锁」与「同线程重入」——只有前者会产生等待、反映
    并发压力，后者是纯逻辑嵌套。用于运行时指标打点，避免 lock_calls 虚高。
    """
    return int(getattr(_lock_state, "depth", 0))
