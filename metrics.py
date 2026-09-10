#!/usr/bin/env python3
"""轻量运行时指标收集（仅标准库）。

用途（《改进建议》P1「完整 Observability」）：memory_stats / memory_audit 只能
回答「库里有什么」，回答不了「服务跑得怎么样」——检索是否经常零结果、锁等待是否
异常、索引是否在反复重建。本模块补齐这一层运行时视角。

记录维度（键名与 server.py 的打点一致）：
    检索    <kind>_calls / <kind>_zero_results / <kind>_results_total / <kind>_ms
            kind ∈ {search, smart_search, list}
    读写    read_calls / read_misses
            write_calls / write_creates / write_updates
    并发    lock_calls（不含同线程重入） / lock_wait_ms / lock_timeouts
    索引    index_rebuilds（首次加载或损坏丢弃后重建）

设计取舍（刻意保持克制）：
    - **绝不阻塞主流程**：全部操作 try/except 包裹，异常一律静默；不使用跨进程锁。
    - **允许并发下少量丢失**：多进程 read-merge-write 同一 JSON 并非原子操作，可能
      互相覆盖。指标用于观察趋势与发现异常，不需要精确到个位——用「不精确」换
      「零开销与零耦合」。这与 SQLite 索引的定位一致（都是可再生的辅助设施）。
    - 落盘位置由调用方传入（与索引 db 同目录、按 MEMORY_DIR 哈希隔离），不入 git。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

__all__ = ["Metrics", "FLUSH_EVERY", "path_for"]

# 每累积多少次事件落盘一次（进程常驻，不能只依赖退出时写盘）
FLUSH_EVERY = 50


def path_for(server_dir: Path, memory_dir: Path) -> Path:
    """指标文件路径：与索引 db 同目录，文件名按 MEMORY_DIR 哈希隔离。

    同一 server 服务多个库（或多份测试临时库）时互不干扰。
    """
    h = hashlib.sha1(str(memory_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return server_dir / f"memory_metrics_{h}.json"


class Metrics:
    """计数器集合：进程内累积 + 定期合并落盘。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._counters: dict[str, float] = {}
        self._persisted: dict[str, float] = {}
        self._pending = 0
        self._loaded = False
        self._lock = threading.Lock()  # 仅保护进程内字典（MCP 可能并发处理请求）

    # ---- 记录 ----------------------------------------------------------
    def inc(self, key: str, n: float = 1) -> None:
        """计数 +n（默认 1）。任何异常静默。"""
        try:
            with self._lock:
                self._counters[key] = self._counters.get(key, 0.0) + n
                self._pending += 1
                due = self._pending >= FLUSH_EVERY
            if due:
                self.flush()
        except Exception:
            pass

    def observe_ms(self, key: str, ms: float) -> None:
        """累积耗时（毫秒），配合 *_calls 可算出均值。"""
        self.inc(key, ms)

    # ---- 落盘 ----------------------------------------------------------
    def _load(self) -> None:
        if self._loaded or self.path is None:
            return
        self._loaded = True
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._persisted = {str(k): float(v) for k, v in raw.items()
                                       if isinstance(v, (int, float))}
        except Exception:
            self._persisted = {}

    def flush(self, force: bool = False) -> None:
        """把内存增量合并进磁盘（read-merge-write）。失败则把增量放回内存。"""
        if self.path is None:
            return
        delta: dict[str, float] = {}
        tmp: Path | None = None
        try:
            with self._lock:
                if not self._counters and not force:
                    return
                delta = dict(self._counters)
                self._counters.clear()
                self._pending = 0
            self._load()
            merged = dict(self._persisted)
            for k, v in delta.items():
                merged[k] = merged.get(k, 0.0) + v
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            self._persisted = merged
        except Exception:
            # 替换失败（Windows 上目标文件被占用是常见原因）会留下 .tmp 孤儿。
            # 这里顺手清掉——指标层不做重试：观测不值得为一次写盘去阻塞主流程，
            # 增量已回收到内存，下次 flush 会重写。
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                with self._lock:
                    for k, v in delta.items():
                        self._counters[k] = self._counters.get(k, 0.0) + v
            except Exception:
                pass

    def snapshot(self) -> dict[str, float]:
        """磁盘历史 + 内存增量（用于展示）。异常时返回空表。"""
        try:
            self._load()
            merged = dict(self._persisted)
            with self._lock:
                for k, v in self._counters.items():
                    merged[k] = merged.get(k, 0.0) + v
            return merged
        except Exception:
            return {}
