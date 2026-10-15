#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-memory 的 Streamable HTTP 入口（v3 阶段 1，2026-10-13）。

用法
----
::

    python serve_http.py                                    # 127.0.0.1:8700/mcp
    AI_MEMORY_HTTP_PORT=8800 python serve_http.py           # 换端口
    AI_MEMORY_HTTP_HOST=0.0.0.0 \\
      AI_MEMORY_HTTP_ALLOWED_HOSTS="192.168.1.10:8700,localhost:8700" \\
      python serve_http.py                                  # 局域网暴露（必须给白名单）

为什么是独立入口，而不是给 server.py 加一个分支
----------------------------------------------
v3 的「主程序零改动」判据要求 ``server.py`` 保持原样（``python server.py`` 永远
等于 stdio）。把 HTTP 装配全部收敛在这里，``server.py`` 一个字符都不用动 ——
这也是 DLC / 中心化方案「升级模板后能零改动替换」的前提。

两处本脚本特有的默认值
----------------------
1. **脚本名即意图**：未显式设置 ``AI_MEMORY_TRANSPORT`` 时按 ``http`` 起
   （仍可用环境变量覆盖回 stdio）。
2. **远程模式默认禁用热重启**：``memory_restart`` 走 ``os.execv`` 原地换进程，
   会让所有在途 HTTP 会话断连 —— 远程场景下这个代价远大于收益，重启应交给编排层
   （进程管理器 / systemd / 容器 restart）。必须在 ``import server`` **之前**
   写入环境变量，``server.py`` 才会读到它。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ① 未指定传输方式时按 http 起（可被 AI_MEMORY_TRANSPORT 覆盖）。
os.environ.setdefault("AI_MEMORY_TRANSPORT", "http")

# ② 保证本目录可 import（直接运行脚本时已在 sys.path，被当模块导入时不一定）。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import transport_http  # noqa: E402  （纯装配层，只依赖 mcp + 标准库）

# ③ 远程模式默认禁用热重启 —— 早于 import server，故 server 能读到。
#    显式设置 AI_MEMORY_RESTART 的用户意图优先（不改）。
if transport_http.transport_mode() != "stdio":
    if "AI_MEMORY_RESTART" not in os.environ:
        os.environ["AI_MEMORY_RESTART"] = "0"

import server  # noqa: E402  （聚合器，必须最后 import）


if __name__ == "__main__":
    transport_http.run(server.mcp)
