# -*- coding: utf-8 -*-
"""Streamable HTTP 传输层装配（ai-memory v3 · 阶段 1，2026-10-13）。

用途
----
把 ai-memory 的 MCP 服务从 stdio 切到 Streamable HTTP，供远程客户端（局域网 /
隧道 / 反向代理之后）连接。**只做传输层装配，不改任何工具实现。**

三条设计约束（依据 vault 内 v3 评估笔记的实测结论）
----------------------------------------------------
1. **主程序零改动**。HTTP 入口是独立脚本 ``serve_http.py``；``server.py`` 保持
   原样，``python server.py`` 永远等于 stdio。DLC / 中心化方案的核心判据
   「不修改主程序」因此不被破坏。
2. **SDK 层刻意不开 auth**。``FastMCP(auth=...)`` 一旦开启，未认证请求会返回
   ``401 + WWW-Authenticate: Bearer ... resource_metadata=...``；mcp 1.30.0 上
   实测部分客户端会据此**强行走 OAuth 发现流程且不回落**，而我们没有授权服务器
   ⇒ 直接踩死。鉴权一律做在外层（反向代理 / Tailscale / Cloudflare Access）。
   本模块因此**不暴露任何 auth 相关配置**。
3. **Host 校验绝不静默关闭**。监听非回环地址时，SDK 里的 Host / Origin 校验极易
   被误配成「放行一切」（``host=0.0.0.0`` 是典型触发条件，mcp 1.30.0 实测其
   ``transport_security`` 会退化为空）。本模块在非回环监听上**强制要求显式
   allowed_hosts**，并**拒绝关闭** DNS rebinding 防护，违规即 fail-fast。

环境变量
--------
==================================  ===========  ==============================
变量                                默认值       说明
==================================  ===========  ==============================
``AI_MEMORY_TRANSPORT``             ``stdio``    stdio / http / streamable-http
``AI_MEMORY_HTTP_HOST``             ``127.0.0.1``  监听地址
``AI_MEMORY_HTTP_PORT``             ``8700``     监听端口
``AI_MEMORY_HTTP_PATH``             ``/mcp``     端点路径
``AI_MEMORY_HTTP_ALLOWED_HOSTS``    （空）        逗号分隔 Host 白名单；**非回环必填**
``AI_MEMORY_HTTP_ALLOWED_ORIGINS``  （空）        逗号分隔 Origin 白名单（浏览器场景）
``AI_MEMORY_HTTP_DNS_REBINDING``    ``1``        恒为 1；设 0 会被**拒绝**
``AI_MEMORY_HTTP_STATELESS``        ``0``        1 则无状态（多副本 / 无会话保持）
``AI_MEMORY_HTTP_SESSION_IDLE_TIMEOUT``  ``1800`` 会话空闲超时（秒）
``AI_MEMORY_HTTP_MAX_SESSIONS``     ``10000``    并发会话上限
==================================  ===========  ==============================

运行
----
本模块**不是**入口脚本，请用 ``serve_http.py``（它负责把传输方式默认成 http
并在远程模式下默认禁用热重启）。单元测试可直接 import 本模块的纯函数。
"""
from __future__ import annotations

import logging
import os

from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger("ai-memory")

MODE_ENV = "AI_MEMORY_TRANSPORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8700
DEFAULT_PATH = "/mcp"

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_STDIO_ALIASES = {"", "stdio", "local", "inproc"}
# SDK 原生叫 streamable-http；其余是给人用的简称。刻意不收 https —— 本模块不做
# TLS，TLS 一律在外层（反代 / 隧道）终结，写 https 会给出错误的安全感。
_HTTP_ALIASES = {"http", "streamable-http", "streamable_http", "remote", "sse-http"}


class TransportConfigError(RuntimeError):
    """传输层配置不合法 —— fail-fast，绝不带着危险默认值启动。"""


# ---------------------------------------------------------------------------
# 环境变量读取（容错，但危险组合一律显式报错而非静默兜底）
# ---------------------------------------------------------------------------
def _env_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else v.strip()


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in _TRUTHY


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v.strip())
    except (TypeError, ValueError):
        raise TransportConfigError(
            f"{name}={v!r} 不是整数。")


def _env_list(name: str) -> list[str]:
    raw = _env_str(name)
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _dedup(items: list[str]) -> list[str]:
    """去重且保序（Host 白名单的顺序无意义，但保序让日志可读、可复现）。"""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# 纯函数：模式判定 + 安全设置
# ---------------------------------------------------------------------------
def transport_mode() -> str:
    """把 ``AI_MEMORY_TRANSPORT`` 归一为 ``stdio`` 或 ``streamable-http``。

    非法值直接抛错（fail-fast）：静默回退到 stdio 会让「以为起了 HTTP、其实
    只在本机 stdio」这种最危险的误配变得无声无息。
    """
    raw = _env_str(MODE_ENV, "stdio").lower()
    if raw in _STDIO_ALIASES:
        return "stdio"
    if raw in _HTTP_ALIASES:
        return "streamable-http"
    raise TransportConfigError(
        f"{MODE_ENV}={raw!r} 不是已知传输方式。"
        f"可用：stdio（默认）/ http / streamable-http")


def is_loopback(host: str) -> bool:
    """该监听地址是否仅回环（不外露）。"""
    return host.strip().lower() in _LOOPBACK_HOSTS


def build_security(
    host: str,
    port: int,
    *,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    enable_dns_rebinding: bool = True,
) -> TransportSecuritySettings:
    """构造 ``TransportSecuritySettings``，并在危险组合上 fail-fast。

    规则（都可证伪，见 tests/test_transport.py）：

    * ``enable_dns_rebinding=False`` ⇒ 一律拒绝。校验是回环/局域网场景下唯一的
      浏览器侧防线；要放行更多主机请写白名单，而不是关掉校验。
    * 非回环监听且白名单为空 ⇒ 拒绝。SDK 在 ``transport_security`` 为 None 或
      白名单为空时不会替我们兜底（``0.0.0.0`` 下实测退化为不校验）。
    * 回环监听且白名单为空 ⇒ 用 SDK 等价默认（端口通配的回环三元组），
      即**不改变回环场景下原有的 SDK 语义**。
    """
    loopback = is_loopback(host)
    hosts = _dedup([h.strip() for h in (allowed_hosts or []) if h and h.strip()])
    origins = _dedup([h.strip() for h in (allowed_origins or []) if h and h.strip()])

    if not enable_dns_rebinding:
        raise TransportConfigError(
            "本模块不允许关闭 DNS rebinding 防护（AI_MEMORY_HTTP_DNS_REBINDING=0）。"
            "Host / Origin 校验是回环与局域网场景下唯一的浏览器侧防线；"
            "要放行更多主机名，请写进 AI_MEMORY_HTTP_ALLOWED_HOSTS，而不是关掉校验。")

    if not loopback and not hosts:
        raise TransportConfigError(
            f"监听非回环地址 {host!r} 时必须显式提供 AI_MEMORY_HTTP_ALLOWED_HOSTS，"
            "否则 Host 校验形同虚设（DNS rebinding 并不依赖公网暴露）。"
            f'示例：AI_MEMORY_HTTP_ALLOWED_HOSTS="192.168.1.10:{port},localhost:{port}"')

    if not hosts:
        # 与 FastMCP 自身默认一致（不偏离 → 回环下行为可预期）
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


# ---------------------------------------------------------------------------
# 配置解析 / 应用 / 启动
# ---------------------------------------------------------------------------
def config() -> dict:
    """从环境变量解析出完整配置（含全部校验）。stdio 模式返回最小字典。"""
    mode = transport_mode()
    if mode == "stdio":
        return {"mode": "stdio"}

    host = _env_str("AI_MEMORY_HTTP_HOST", DEFAULT_HOST) or DEFAULT_HOST
    port = _env_int("AI_MEMORY_HTTP_PORT", DEFAULT_PORT)
    if not (1 <= port <= 65535):
        raise TransportConfigError(f"AI_MEMORY_HTTP_PORT={port} 不在 1..65535。")

    path = _env_str("AI_MEMORY_HTTP_PATH", DEFAULT_PATH) or DEFAULT_PATH
    if not path.startswith("/"):
        path = "/" + path

    security = build_security(
        host,
        port,
        allowed_hosts=_env_list("AI_MEMORY_HTTP_ALLOWED_HOSTS"),
        allowed_origins=_env_list("AI_MEMORY_HTTP_ALLOWED_ORIGINS"),
        enable_dns_rebinding=_env_bool("AI_MEMORY_HTTP_DNS_REBINDING", True),
    )
    return {
        "mode": "streamable-http",
        "host": host,
        "port": port,
        "path": path,
        "stateless": _env_bool("AI_MEMORY_HTTP_STATELESS", False),
        "session_idle_timeout": _env_int("AI_MEMORY_HTTP_SESSION_IDLE_TIMEOUT", 1800),
        "max_sessions": _env_int("AI_MEMORY_HTTP_MAX_SESSIONS", 10000),
        "security": security,
    }


def describe(cfg: dict) -> str:
    """一行人类可读摘要，供启动日志使用。"""
    if cfg.get("mode") == "stdio":
        return "stdio（本机标准输入输出，默认）"
    sec = cfg["security"]
    return (
        f"streamable-http  http://{cfg['host']}:{cfg['port']}{cfg['path']}  "
        f"allowed_hosts={sec.allowed_hosts}  origins={sec.allowed_origins or '[]'}  "
        f"stateless={cfg['stateless']}")


def apply(mcp, cfg: dict | None = None) -> dict:
    """把配置写进 ``FastMCP.settings``。stdio 模式**不触碰任何属性**（零副作用）。"""
    cfg = cfg if cfg is not None else config()
    if cfg["mode"] == "stdio":
        return cfg
    mcp.settings.host = cfg["host"]
    mcp.settings.port = cfg["port"]
    mcp.settings.streamable_http_path = cfg["path"]
    mcp.settings.transport_security = cfg["security"]
    mcp.settings.stateless_http = cfg["stateless"]
    mcp.settings.session_idle_timeout = cfg["session_idle_timeout"]
    mcp.settings.max_sessions = cfg["max_sessions"]
    return cfg


def run(mcp) -> None:
    """按 ``AI_MEMORY_TRANSPORT`` 启动 FastMCP。

    * stdio（默认）⇒ ``mcp.run()``，与加本模块之前**逐字节等价**。
    * 其它 ⇒ 装配 HTTP 设置后 ``mcp.run(transport="streamable-http")``。
    """
    cfg = apply(mcp)
    if cfg["mode"] == "stdio":
        logger.info("ai-memory 传输层：%s", describe(cfg))
        mcp.run()
        return

    logger.info("ai-memory 传输层：%s", describe(cfg))
    logger.warning(
        "HTTP 模式未启用 SDK 层鉴权（这是刻意的）：请把鉴权做在外层 —— "
        "反向代理 / Tailscale / Cloudflare Access。切勿直接暴露到不可信网络。")
    mcp.run(transport="streamable-http")
