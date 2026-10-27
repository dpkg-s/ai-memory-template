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
   —— 2026-10-25 追加：**权级分档不走 SDK auth，而是路径分档**（见下节）。
3. **Host 校验绝不静默关闭**。监听非回环地址时，SDK 里的 Host / Origin 校验极易
   被误配成「放行一切」（``host=0.0.0.0`` 是典型触发条件，mcp 1.30.0 实测其
   ``transport_security`` 会退化为空）。本模块在非回环监听上**强制要求显式
   allowed_hosts**，并**拒绝关闭** DNS rebinding 防护，违规即 fail-fast。

权级分档（2026-10-25，2026-10-26 加只读变体）
---------------------------------------------
`/mcp` 是 owner 全权端点，`/mcp/member`、`/mcp/guest` 各挂一个更低的读权级；
`/mcp/reader`（2026-10-26 新增）只读共享档：能读 internal、不读 sensitive/secret、
完全禁写。

**2026-10-26 再加第二根轴：只读变体**。`/mcp/owner-ro` 与 `/mcp/member-ro` 表示
「视野照旧、禁止一切写入」—— 此前读写能力是焊死在档位上的（owner 必能写、
reader 必不能写），于是「全库可读、一字不许改」这个真实需求（别的设备上的
分析客户端 / 只读镜像）在档位表里没有坐标。拆成两轴之后判据是：

    实到写权 = 档位允许写 AND 本次请求允许写

路径里带 `-ro` 就打上后者为假。`guest` / `reader` **不生成** `-ro` 变体 ——
它们本来就只读，多一条等价路径只是多一个可绕过的口子。反过来 `owner-ro` 是
`/mcp` 的严格子集，多这一条不会放宽任何东西。

`ClearanceRouter` 把受限路径**改写**回 `streamable_http_path` 再转交 SDK，同时把
（档位, 是否允许写）二元组写进 ``server.set_clearance`` 的两个 contextvar —— 因此：

* 不需要改 mcp SDK、不需要改任何工具签名（已用 PoC 实测三条路径档位互不串味）；
* 不需要客户端支持自定义 header（AstrBot 的 ``mcp_server.json`` 只有 url 字段，
  改 URL 是它唯一确定支持的配置项）；
* 未登记的路径**一律 404**，防止有人用别名绕过档位。

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
``AI_MEMORY_HTTP_CLEARANCE_ROUTES`` ``member,guest,reader,member-ro,owner-ro``
                                               受限端点后缀（``-ro`` = 只读变体）；
                                               ``off`` 关闭分档，只剩 ``/mcp``
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
# 权级分档：路径 → 档位
# ---------------------------------------------------------------------------
CLEARANCE_ROUTES_ENV = "AI_MEMORY_HTTP_CLEARANCE_ROUTES"
#: 受限档位后缀的默认值。owner 是**路径本身**（`/mcp`），不需要也不允许后缀
#: （否则会出来 `/mcp/owner` 这种与 `/mcp` 等价却多一条绕过口的路由）。
#:
#: ``reader``（2026-10-26 新增）是「只读共享档」：够得着 internal 内容，
#: 够不着 sensitive / secret，且完全禁写。它排在 guest 与 member 之间，
#: 存在的理由见 ``yaml_io.SECURITY_MIN_CLEARANCE`` 上方那段注释。
DEFAULT_RESTRICTED_SUFFIXES = ("member", "guest", "reader", "member-ro", "owner-ro")
_CLEARANCE_LEVELS = ("guest", "reader", "member", "owner")
#: 关掉分档的写法。允许 "off" / "none" / "" 三种 —— 配置项写错时的默认必须是
#: **保持分档**（fail-safe），所以只有明确写 off/none 才关。
_ROUTES_OFF = {"off", "none", "0", "false"}
#: 只读后缀（2026-10-26）。`member-ro` / `owner-ro` 表示「视野照旧，禁止写入」。
_RO_SUFFIX = "-ro"
#: 本来就只读的档位：不给它们生成 `-ro` 变体。多一条等价路径 = 多一个可绕过的
#: 口子，而没有任何语义收益（reader-ro 与 reader 完全等价）。
_INHERENTLY_READONLY = frozenset({"guest", "reader"})


def clearance_routes(base_path: str, raw: str | None = None) -> dict[str, tuple[str, bool]]:
    """构造 ``{路径: (视野, 是否允许写)}`` 路由表。纯函数，供 ``ClearanceRouter`` 与测试共用。

    * ``base_path`` 恒等于 owner 可写档（``/mcp``）；
    * 其余按 ``AI_MEMORY_HTTP_CLEARANCE_ROUTES`` 生成 ``<base>/<档位>``；
      后缀带 ``-ro`` 表示只读。默认生成
      ``member / guest / reader / member-ro / owner-ro``；写 ``off`` 关闭分档
      （只剩 owner 端点）；
    * 非法档位名一律**丢弃并告警**，不猜测、不静默映射到 owner —— 拼错一个字母
      就变成全权端点，是这类配置最危险的失效模式；
    * ``owner``（可写）不生成别名，但 ``owner-ro`` **要**生成：它是 `/mcp` 的
      严格子集，多这一条不会放宽任何东西，而它正是「全库可读、一字不许改」
      唯一的挂载点。与之相对，``guest-ro`` / ``reader-ro`` 一律丢弃 —— 它们与
      基础档完全等价。

    返回值里的布尔位刻意用「是否允许写」而非「是否只读」：判据在服务端是
    ``if not writable: 拒绝``，与 ``server.current_writable()`` 的名词保持一致，
    少一层取反就少一处取反写错的机会。
    """
    base = base_path or DEFAULT_PATH
    if not base.startswith("/"):
        base = "/" + base
    routes: dict[str, tuple[str, bool]] = {base: ("owner", True)}

    if raw is None:
        raw = _env_str(CLEARANCE_ROUTES_ENV)
    items = [x.strip().lower() for x in raw.split(",") if x.strip()] if raw else []
    if not items:
        items = list(DEFAULT_RESTRICTED_SUFFIXES)
    if len(items) == 1 and items[0] in _ROUTES_OFF:
        return routes

    for suffix in items:
        if suffix in _ROUTES_OFF:
            continue
        writable = not suffix.endswith(_RO_SUFFIX)
        lvl = suffix[:-len(_RO_SUFFIX)] if not writable else suffix
        if lvl not in _CLEARANCE_LEVELS:
            logger.warning("忽略 %s 中无法识别的档位 %r（合法：guest / reader / member / owner，"
                           "可选 -ro 后缀）", CLEARANCE_ROUTES_ENV, suffix)
            continue
        if lvl == "owner" and writable:
            # owner 全权只能走 `/mcp` 本身，不额外开别名
            logger.warning("忽略 %s 中的 owner 档位：全权端点只能是 %s 本身",
                           CLEARANCE_ROUTES_ENV, base)
            continue
        if lvl in _INHERENTLY_READONLY and not writable:
            logger.warning("忽略 %s 中的 %r：%s 档本身就是只读，无需 -ro 变体",
                           CLEARANCE_ROUTES_ENV, suffix, lvl)
            continue
        routes[f"{base.rstrip('/')}/{suffix}"] = (lvl, writable)
    return routes


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
class ClearanceRouter:
    """ASGI 包装层：把 ``/mcp/<档>`` 改写回 ``/mcp`` 并设定该请求的读权级。

    为什么是 ASGI 中间件而不是 MCP 中间件：``streamable_http_app()`` 返回的是标准
    Starlette 应用，包一层即可，**完全不碰 SDK 内部**。工具实现那边只需要读一个
    contextvar（``server.current_clearance``），签名与 MCP schema 都不变。

    权级钩子**由入口注入**（``serve_http.py`` 传 ``server.set_clearance`` /
    ``reset_clearance``），本模块不 import server —— 分层守卫（I6a/I9b）要求装配
    层不得反向依赖主程序，而这条约束正是「server.py 零改动」的保障。没有注入钩
    子却声明了受限档位时，``run()`` 会 fail-fast 而不是默默当 owner 放行。

    三个必须守住的细节：

    1. **路径要改写两份**：``scope["path"]`` 与 ``scope["raw_path"]`` 都得改。
       Starlette 路由匹配用前者，但部分中间件会用后者，只改一个会出现「路由命中
       但日志/校验对不上」的错位。
    2. **contextvar 必须在 finally 里 reset**：泄漏一次的后果是「上一个请求的
       member 档位粘到下一个 owner 请求上」或反之 —— 两个方向都不可接受。
    3. **未登记路径返回 404 而不是 401/403**：401/403 会告诉扫描者「这里有个受保
       护的端点」，而 404 什么也不说。
    """

    def __init__(self, inner, base: str = DEFAULT_PATH,
                 routes: dict[str, tuple[str, bool]] | None = None,
                 setter=None, resetter=None):
        self.inner = inner
        self.base = base if base.startswith("/") else "/" + base
        self.routes = dict(routes or {self.base: ("owner", True)})
        self._set = setter
        self._reset = resetter

    @property
    def restricted(self) -> bool:
        """除「owner 可写」端点外是否还有别的档位。"""
        return any(not (lvl == "owner" and w) for lvl, w in self.routes.values())

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            spec = self.routes.get(path)
            if spec is None and path.startswith(self.base):
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": b"not found"})
                return
            if spec is not None:
                level, writable = spec
                rewritten = dict(scope)
                rewritten["path"] = self.base
                rewritten["raw_path"] = self.base.encode()
                if self._set is None:
                    # 无钩子只能是 owner：带受限档位时 run() 已拒绝启动，走到这里
                    # 说明有人手工构造了 router —— 保持不可被利用（不给低档位）。
                    return await self.inner(rewritten, receive, send)
                token = self._set(level, writable)
                try:
                    return await self.inner(rewritten, receive, send)
                finally:
                    self._reset(token)
        return await self.inner(scope, receive, send)


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
        "clearance_routes": clearance_routes(path),
    }


def _fmt_routes(routes: dict) -> str:
    """把路由表渲染成 ``/mcp→owner、/mcp/reader→reader(ro)`` 一行人读摘要。

    ``-ro`` 刻意显示成 ``(ro)`` 而不是把后缀原样重复一遍：路径里已经带了 ``-ro``，
    日志里再写 ``/mcp/owner-ro→owner-ro`` 会让「路径」与「档位」两个概念糊成一片。
    """
    if not routes:
        return "{}"
    return "、".join(f"{p}→{lvl}{'' if w else '(ro)'}"
                    for p, (lvl, w) in sorted(routes.items()))


def describe(cfg: dict) -> str:
    """一行人类可读摘要，供启动日志使用。"""
    if cfg.get("mode") == "stdio":
        return "stdio（本机标准输入输出，默认）"
    sec = cfg["security"]
    return (
        f"streamable-http  http://{cfg['host']}:{cfg['port']}{cfg['path']}  "
        f"clearance={_fmt_routes(cfg.get('clearance_routes') or {})}  "
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


def _serve(app, *, host: str, port: int, log_level: str) -> None:
    """起 uvicorn。抽成独立函数只为一件事：让测试能替换它。

    直接 `uvicorn.run(...)` 会让「装配是否正确的单元测试」变成「真的起一个服务
    并阻塞」—— 那既慢又不稳，还会占端口。测试注入替身即可断言装配结果。
    """
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level=log_level)


def run(mcp, clearance_hooks: tuple | None = None) -> None:
    """按 ``AI_MEMORY_TRANSPORT`` 启动 FastMCP。

    * stdio（默认）⇒ ``mcp.run()``，与加本模块之前**逐字节等价**。
    * 其它 ⇒ 装配 HTTP 设置，用 ``streamable_http_app()`` 拿到 Starlette 应用，
      外面包一层 ``ClearanceRouter`` 再交给 uvicorn。

    为什么不再用 ``mcp.run(transport="streamable-http")``：那条路会在内部直接
    构造 app 并起 uvicorn，没有插入中间件的钩子。改成自建 app 后，行为与原来
    等价（同一份 settings、同一个 session manager），只是多了路径分档。

    ``clearance_hooks`` 是 ``(set_clearance, reset_clearance)`` 二元组，由入口
    （``serve_http.py``）从 ``server`` 取来注入。**声明了受限档位却没给钩子 ⇒
    直接拒绝启动**：那意味着所有档位的请求都会以 owner 身份执行，比不设分档更
    危险 —— 表面上「已经隔离」，实际上全开放。
    """
    cfg = apply(mcp)
    if cfg["mode"] == "stdio":
        logger.info("ai-memory 传输层：%s", describe(cfg))
        mcp.run()
        return

    routes = cfg.get("clearance_routes") or {}
    has_restricted = any(not (lvl == "owner" and w) for lvl, w in routes.values())
    if has_restricted and not clearance_hooks:
        raise TransportConfigError(
            "已声明受限权级端点（"
            + "、".join(sorted(p for p, (lvl, w) in routes.items()
                              if not (lvl == "owner" and w)))
            + "）但没有注入权级钩子。请用 serve_http.py 启动（它会把 server 的 "
              "set_clearance / reset_clearance 传进来），或设 "
            f"{CLEARANCE_ROUTES_ENV}=off 关闭分档。拒绝以「表面隔离、实际全权」"
              "的方式启动。")

    logger.info("ai-memory 传输层：%s", describe(cfg))
    if has_restricted:
        logger.info("权级分档已启用：%s", _fmt_routes(routes))
    else:
        logger.info("权级分档未启用（只有 %s，owner 全权）", cfg["path"])
    logger.warning(
        "HTTP 模式未启用 SDK 层鉴权（这是刻意的）：请把鉴权做在外层 —— "
        "反向代理 / Tailscale / Cloudflare Access。切勿直接暴露到不可信网络。")

    setter, resetter = clearance_hooks if clearance_hooks else (None, None)
    app = ClearanceRouter(mcp.streamable_http_app(), base=cfg["path"], routes=routes,
                          setter=setter, resetter=resetter)
    _serve(app, host=cfg["host"], port=cfg["port"],
           log_level=str(getattr(mcp.settings, "log_level", "INFO")).lower())
