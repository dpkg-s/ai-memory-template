# -*- coding: utf-8 -*-
"""ai-memory Web 控制台后端（v3.1，2026-10-13）。

定位
----
给「人」用的管理界面，两件事：

1. **调试台** —— 像 API 调试一样直接调用 24 个 MCP 工具（查询/检索/看图）。
2. **配置面** —— FNS 同步配置 + 运行时阈值（``memory.toml``），不再靠手改文件。

本模块**不是** MCP 服务端，而是 MCP 的**外部客户端**：它通过 Streamable HTTP
连到 ai-memory 端点（默认 ``127.0.0.1:8700/mcp``）。因此它天然满足 v3 的两条判死项
——不直连 vault、不进程内 import 直调 —— 所有对记忆库的读写都走完整 MCP 协议。

🔴 为什么必须「同源页面 + 后端代理」，而不是浏览器直连 MCP
----------------------------------------------------------
三条路都真起服务端实测过（`_v3_web_browser_probe.py` / `_v3_web_proxy_probe.py`）：

* **后端代理**（本模块采用）：后端用 SDK 客户端连 MCP（服务端→服务端，**不发
  ``Origin`` 头**）⇒ 实测 initialize / 24 工具 / memory_stats 全通，**MCP 端零配置
  改动**。
* **浏览器直连**（不可行）：``TransportSecuritySettings.allowed_origins`` 默认是空
  列表 ⇒ 任何带 ``Origin`` 头的请求一律 **403 Invalid Origin header**（这是刻意
  的 fail-closed 默认，不是缺陷）。即便把 Origin 写进白名单，SDK 也**不返回 CORS
  头**、``OPTIONS`` 预检返 **405** ⇒ 仍要自写 CORS 中间件才可用。
* **同进程 mount**：``streamable_http_app()`` 返回 Starlette，可以 mount，但会破坏
  「Web 台 = 独立一层」的分层，且把 MCP 端点与 Web 面绑成一个进程。

⇒ 定稿：浏览器只与 Web 台**同源**通信（普通 ``fetch`` + JSON，无 CORS），MCP 调用
一律在后端完成。

安全（回环默认 + 非回环必须给 token）
------------------------------------
* 监听地址默认 ``127.0.0.1``（宿主机局域网看不到）。
* 一旦监听非回环地址且**未配置 token** ⇒ **拒绝启动**（fail-fast）。理由是
  「全放行调试台」= 对记忆库具备完整破坏能力的写入口，裸奔暴露在局域网上不可接受。
* 配了 token 后，``/api/*`` 必须带 ``Authorization: Bearer <token>``（或
  ``X-Auth-Token``）；静态资源不校验（里面没有秘密）。前端把 token 存在
  ``sessionStorage``，**不进 URL、不进 bundle**。
* ``AI_MEMORY_WEB_ALLOWED_HOSTS``（可选）启用 Starlette ``TrustedHostMiddleware``
  —— DNS rebinding 并不依赖公网暴露，局域网下同样值得挡。

配置面口径
----------
* **FNS 凭据/配置**写进 **vault 之外**的 JSON（``AI_MEMORY_WEB_CONFIG``）——
  写进 vault 会被 fns-sync 同步出去（v3 红线 1）。
* **运行时阈值**写 vault 根的 ``memory.toml`` —— 它是**配置文件不是笔记**（不进
  索引、无 frontmatter、不被 MCP 工具管理），且写入前先经 ``memory_config.parse_toml``
  校验、写时原子替换、写前备份到 vault 之外。可用
  ``AI_MEMORY_WEB_CONFIG_WRITE=0`` 关闭为只读。
* **绝不触碰 ``*.md``**。

环境变量
--------
========================================  =====================  ========================
变量                                       默认值                 说明
========================================  =====================  ========================
``AI_MEMORY_WEB_HOST``                    ``127.0.0.1``          监听地址
``AI_MEMORY_WEB_PORT``                    ``8811``               监听端口
``AI_MEMORY_WEB_MCP``                     ``http://127.0.0.1:8700/mcp``  上游 MCP 端点
``AI_MEMORY_WEB_TOKEN``                   （空）                 非回环必填；空=不鉴权
``AI_MEMORY_WEB_ALLOWED_HOSTS``           （空）                 TrustedHost 白名单
``AI_MEMORY_WEB_CONFIG``                  ``~/.ai-memory-web/config.json``  配置面存储
``AI_MEMORY_WEB_CONFIG_WRITE``            ``1``                  0=阈值只读
``AI_MEMORY_WEB_STATIC``                  ``<本文件同级>/web``    前端静态资源目录
``AI_MEMORY_DIR``                         （空）                 vault 位置（阈值写入需要）
========================================  =====================  ========================
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import memory_config
import transport_http

logger = logging.getLogger("ai-memory.web")

WEB_VERSION = "1.0.0"
DEFAULT_WEB_HOST = "127.0.0.1"
# ⚠️ 本机实测（2026-10-13，Windows）：8800/8801/8802 均被系统保留（bind 报
# WinError 10013），故默认改用 8811 段。可选：8811-8820。
DEFAULT_WEB_PORT = 8811
DEFAULT_MCP_URL = (f"http://{transport_http.DEFAULT_HOST}:"
                   f"{transport_http.DEFAULT_PORT}{transport_http.DEFAULT_PATH}")
DEFAULT_CONFIG_PATH = Path.home() / ".ai-memory-web" / "config.json"
DEFAULT_CONFIG_WRITE = True
MAX_BODY_BYTES = 2 * 1024 * 1024          # 请求体上限 2 MiB（拒绝无脑大包）
MCP_TIMEOUT = 90.0                        # 单次会话总超时（秒）
MCP_HEALTH_TIMEOUT = 20.0
# 回传浏览器的文本封顶。**不能太小**：`memory_list` / `memory_recent` 支持的
# `output_format="json"` 是一条 JSON 字符串，截断会让它直接解析失败（探针踩过）。
# 40k 字符足够容纳「列表 50 条 + 笔记正文」，又能挡住病态大包。
_THUMB = 40000

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

# FNS 配置允许的字段（写盘时按此白名单过滤 —— 不落未知字段，避免手滑把秘密写歪）
_FNS_FIELDS = ("server", "account", "vault", "token", "exclude")
_EXCLUDE_MAX = 200


class WebConfigError(RuntimeError):
    """Web 控制台配置不合法 —— fail-fast，绝不带着危险默认值启动。"""


# ---------------------------------------------------------------------------
# 环境变量
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
        raise WebConfigError(f"{name}={v!r} 不是整数。")


def is_loopback(host: str) -> bool:
    return host.strip().lower() in _LOOPBACK_HOSTS


def static_dir() -> Path:
    """前端静态资源目录（``AI_MEMORY_WEB_STATIC`` 可覆盖）。"""
    raw = _env_str("AI_MEMORY_WEB_STATIC")
    return Path(raw) if raw else (Path(__file__).resolve().parent / "web")


def config_path() -> Path:
    raw = _env_str("AI_MEMORY_WEB_CONFIG")
    return Path(raw).expanduser() if raw else DEFAULT_CONFIG_PATH


def config() -> dict:
    """解析运行时配置（含全部校验）。危险组合一律抛错，不静默兜底。"""
    host = _env_str("AI_MEMORY_WEB_HOST", DEFAULT_WEB_HOST) or DEFAULT_WEB_HOST
    port = _env_int("AI_MEMORY_WEB_PORT", DEFAULT_WEB_PORT)
    if not (1 <= port <= 65535):
        raise WebConfigError(f"AI_MEMORY_WEB_PORT={port} 不在 1..65535。")

    token = _env_str("AI_MEMORY_WEB_TOKEN")
    if not is_loopback(host) and not token:
        raise WebConfigError(
            f"监听非回环地址 {host!r} 时必须设置 AI_MEMORY_WEB_TOKEN，"
            "否则局域网内任何人都能打开调试台并对记忆库执行破坏性操作。"
            '示例：AI_MEMORY_WEB_TOKEN="$(openssl rand -hex 16)"')

    mcp = _env_str("AI_MEMORY_WEB_MCP", DEFAULT_MCP_URL) or DEFAULT_MCP_URL
    if not re.match(r"^https?://", mcp):
        raise WebConfigError(f"AI_MEMORY_WEB_MCP={mcp!r} 必须是 http(s):// URL。")

    allowed_hosts = [x.strip() for x in _env_str("AI_MEMORY_WEB_ALLOWED_HOSTS").split(",")
                     if x.strip()]
    static = static_dir()
    if not (static / "index.html").is_file():
        raise WebConfigError(
            f"前端静态资源缺失：{static / 'index.html'} 不存在。"
            "请确认 web/ 目录已随仓库就位（2026-10-15 起为模块化结构："
            "index.html + js/ （ESM 模块树，入口 js/main.js）"
            " + css/ （按层 1..8 共 8 片，引入顺序即层叠顺序）"
            " + vendor/ （vue/marked/purify/gsap/Flip）），"
            "或用 AI_MEMORY_WEB_STATIC 指定目录。")

    return {
        "host": host,
        "port": port,
        "token": token,
        "auth_enabled": bool(token),
        "mcp": mcp,
        "allowed_hosts": allowed_hosts,
        "static": static,
        "config_path": config_path(),
        "config_write": _env_bool("AI_MEMORY_WEB_CONFIG_WRITE", DEFAULT_CONFIG_WRITE),
    }


def describe(cfg: dict | None = None) -> str:
    cfg = cfg or config()
    return (f"http://{cfg['host']}:{cfg['port']}/  →  MCP {cfg['mcp']}  "
            f"auth={'token' if cfg['auth_enabled'] else '关（仅回环）'}  "
            f"config={cfg['config_path']}")


# ---------------------------------------------------------------------------
# 配置面：vault 之外的 JSON（FNS 凭据等）
# ---------------------------------------------------------------------------
def _blank_app_config() -> dict:
    return {
        "mcp_endpoint": "",
        "vault_dir": "",
        "fns": {k: "" for k in _FNS_FIELDS},
        "ui": {"theme": "auto"},
        "updated": "",
    }


def _sanitize_app_config(raw: Any) -> dict:
    """把任意输入收敛成合法结构（不信任前端来的东西）。"""
    out = _blank_app_config()
    if not isinstance(raw, dict):
        return out
    out["mcp_endpoint"] = str(raw.get("mcp_endpoint") or "").strip()[:500]
    out["vault_dir"] = str(raw.get("vault_dir") or "").strip()[:500]
    fns = raw.get("fns")
    if isinstance(fns, dict):
        for k in _FNS_FIELDS:
            v = fns.get(k, "")
            if k == "exclude":
                if isinstance(v, str):
                    items = [x.strip() for x in re.split(r"[\r\n,]+", v) if x.strip()]
                elif isinstance(v, list):
                    items = [str(x).strip() for x in v if str(x).strip()]
                else:
                    items = []
                out["fns"][k] = items[:_EXCLUDE_MAX]
            else:
                out["fns"][k] = str(v or "").strip()[:2000]
    ui = raw.get("ui")
    if isinstance(ui, dict):
        theme = str(ui.get("theme") or "auto")
        out["ui"]["theme"] = theme if theme in ("auto", "light", "dark") else "auto"
    out["updated"] = str(raw.get("updated") or "")
    return out


def load_app_config() -> dict:
    p = config_path()
    if not p.is_file():
        return _blank_app_config()
    try:
        return _sanitize_app_config(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        logger.warning("读取 %s 失败（用空配置）：%s", p, e)
        return _blank_app_config()


def save_app_config(raw: dict) -> dict:
    """原子写（同目录 tmp + replace）。配置目录不在 vault 内，故 fns token 不会被同步出去。"""
    cfg = _sanitize_app_config(raw)
    cfg["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(str(tmp), str(p))
    return cfg


def resolve_mcp(cfg: dict | None = None) -> str:
    """MCP 端点：JSON 配置里的值优先（前端可改），否则用环境变量默认。"""
    cfg = cfg or config()
    app_cfg = load_app_config()
    return app_cfg.get("mcp_endpoint") or cfg["mcp"]


def resolve_vault() -> Path | None:
    """vault 位置：环境变量 AI_MEMORY_DIR > JSON 配置 vault_dir > 无。"""
    raw = _env_str("AI_MEMORY_DIR")
    if raw:
        return Path(raw).expanduser()
    v = load_app_config().get("vault_dir") or ""
    return Path(v).expanduser() if v else None


# ---------------------------------------------------------------------------
# 配置面：运行时阈值（vault 根的 memory.toml）
# ---------------------------------------------------------------------------
def _toml_scalar(key: str, value: Any) -> str:
    want = type(memory_config.DEFAULTS[key])
    if isinstance(value, bool):
        raise WebConfigError(f"配置 {key} 不接受布尔值。")
    if want is int:
        if not isinstance(value, int):
            raise WebConfigError(f"配置 {key} 期望整数，得到 {value!r}。")
        return str(value)
    if not isinstance(value, (int, float)):
        raise WebConfigError(f"配置 {key} 期望数值，得到 {value!r}。")
    return repr(float(value))


def render_toml(values: dict[str, Any]) -> str:
    """把 8 个阈值渲染成 memory.toml 文本（``[thresholds]`` 区，LF 行尾）。"""
    unknown = sorted(set(values) - set(memory_config.DEFAULTS))
    if unknown:
        raise WebConfigError(f"未知配置键：{', '.join(unknown)}")
    missing = sorted(set(memory_config.DEFAULTS) - set(values))
    if missing:
        raise WebConfigError(f"缺少配置键：{', '.join(missing)}")
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# ai-memory 运行时阈值（memory.toml）",
        f"# 由 ai-memory Web 控制台写入：{stamp}",
        f"# 取值优先级：环境变量 {memory_config.ENV_PREFIX}<KEY> > "
        "[thresholds] 区 > 顶层同名键 > 内置默认",
        f"# 改动约 {memory_config.CACHE_TTL:g} 秒内生效，无需重启服务。",
        "",
        "[thresholds]",
    ]
    for k in memory_config.DEFAULTS:
        lines.append(f"{k} = {_toml_scalar(k, values[k])}")
    lines.append("")
    return "\n".join(lines)


def _threshold_source(key: str, file_values: dict[str, Any]) -> str:
    if _env_str(memory_config.ENV_PREFIX + key.upper()):
        return "env"
    if f"thresholds.{key}" in file_values or key in file_values:
        return "file"
    return "default"


def thresholds_state(cfg: dict | None = None) -> dict:
    """当前生效值 + 每个键的来源 + 可否写入。"""
    cfg = cfg or config()
    vault = resolve_vault()
    memory_config.set_vault(vault)
    memory_config.reload()

    path = memory_config.config_path()
    file_values: dict[str, Any] = {}
    parse_error: str | None = None
    if path is not None and path.is_file():
        try:
            file_values = memory_config.parse_toml(path.read_text(encoding="utf-8-sig"))
        except Exception as e:                                    # noqa: BLE001
            parse_error = str(e)

    keys: dict[str, dict] = {}
    for k, dflt in memory_config.DEFAULTS.items():
        # 文案随值一起下发（唯一来源 = memory_config.META）：前端因此不必硬编码
        # 任何阈值含义，新增阈值时界面自动跟着长出来。
        info = memory_config.meta(k)
        keys[k] = {
            "value": memory_config.get(k),
            "default": dflt,
            "type": "int" if isinstance(dflt, int) else "float",
            "source": _threshold_source(k, file_values),
            "env": memory_config.ENV_PREFIX + k.upper(),
            "label": info["label"],
            "desc": info["desc"],
        }
    return {
        "vault": str(vault) if vault else None,
        "config_path": str(path) if path is not None else None,
        "file_exists": bool(path is not None and path.is_file()),
        "writable": bool(cfg["config_write"] and path is not None),
        "keys": keys,
        "last_error": parse_error or memory_config.last_error(),
        "ttl": memory_config.CACHE_TTL,
    }


def write_thresholds(values: dict[str, Any], cfg: dict | None = None) -> dict:
    """校验 → 备份 → 原子写 memory.toml（**只写这个文件，绝不碰 *.md**）。"""
    cfg = cfg or config()
    if not cfg["config_write"]:
        raise WebConfigError("阈值写入已被 AI_MEMORY_WEB_CONFIG_WRITE=0 关闭（只读模式）。")
    vault = resolve_vault()
    memory_config.set_vault(vault)
    path = memory_config.config_path()
    if path is None:
        raise WebConfigError(
            "未知 vault 位置 ⇒ 无法定位 memory.toml。请设置环境变量 AI_MEMORY_DIR，"
            "或在「配置 → 记忆库」里填写 vault 目录。")

    text = render_toml(values)                     # 内含类型校验
    memory_config.parse_toml(text)                 # 写前自解析一遍（fail-fast）

    backup = None
    if path.is_file():
        bak_dir = config_path().parent / "backups"
        bak_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = bak_dir / f"memory.toml.{stamp}.bak"
        shutil.copyfile(str(path), str(backup))

    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(str(tmp), str(path))
    memory_config.reload()
    state = thresholds_state(cfg)
    state["backup"] = str(backup) if backup else None
    return state


# ---------------------------------------------------------------------------
# MCP 客户端（后端代理）
# ---------------------------------------------------------------------------
def _serialize_result(res: Any) -> dict:
    content: list[dict] = []
    for item in (getattr(res, "content", None) or []):
        if hasattr(item, "model_dump"):
            content.append({k: v for k, v in item.model_dump().items() if v is not None})
        elif isinstance(item, dict):
            content.append(item)
        else:
            content.append({"type": "unknown", "text": str(item)})
    text = "\n".join(str(c.get("text", "")) for c in content
                     if isinstance(c.get("text"), str))
    structured = getattr(res, "structuredContent", None)
    return {
        "isError": bool(getattr(res, "isError", False)),
        "content": content,
        "text": text,
        "structured": structured,
        "chars": len(text),
    }


def _clip(res: dict) -> dict:
    """给浏览器的文本封顶（MCP 端单项可达数万字符，不宜整包回传）。"""
    if len(res.get("text", "")) > _THUMB:
        res = dict(res)
        res["text"] = res["text"][:_THUMB] + f"\n…（已截断，原文 {res['chars']} 字符）"
        res["truncated"] = True
    return res


def _bounded_int(raw: Any, default: int, lo: int, hi: int) -> int:
    """查询串里的整数参数：解析失败回落默认值，并夹在 [lo, hi] 内。"""
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _local_stamp(iso: Any) -> tuple[str, str]:
    """ISO 时间戳 → **本机时区**下的 ``("YYYY-MM-DD", "HH:MM")``。

    ``memory_recent`` 给的是 ``2026-10-15T08:31:07+00:00``。直接切前 10 位会在
    UTC+8 下把「本地凌晨写的」整条挪到前一天 —— 日历格子与用户挂钟就会差一整天。
    换算只在这里做一次（前端不再做日期数学），双份口径的隐患随之消失。
    解析失败返回空串，调用方跳过该条（**不猜**）。
    """
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return "", ""
    dt = dt.astimezone()          # 无 tz 的裸时间按本机时区解释
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


async def mcp_session_calls(endpoint: str, calls: list[tuple[str, dict]],
                            timeout: float = MCP_TIMEOUT,
                            clip: bool = True) -> list[dict]:
    """一次会话里顺序执行多次工具调用（省掉 N 次握手）。

    后端→MCP 是服务端到服务端调用，**不带 Origin 头** ⇒ 不受 allowed_origins 限制。

    ``clip=False`` 取消 ``_THUMB`` 文本封顶 —— 只给**服务端自己消费**的调用用
    （如 ``/api/activity`` 要在后端解析 memory_recent 的整包 JSON）。
    给浏览器的调用**必须**保持默认 ``clip=True``，否则大包会整坨回传到前端。
    """
    out: list[dict] = []
    with anyio.fail_after(timeout):
        async with streamablehttp_client(endpoint) as (read, write, _cb):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for tool, arguments in calls:
                    res = await session.call_tool(tool, arguments or {})
                    item = _serialize_result(res)
                    out.append(_clip(item) if clip else item)
    return out


async def mcp_health(endpoint: str, timeout: float = MCP_HEALTH_TIMEOUT) -> dict:
    """连通性探针：握手 + 工具清单。失败返回 ``ok=False`` 而不是抛错。"""
    t0 = time.perf_counter()
    try:
        with anyio.fail_after(timeout):
            async with streamablehttp_client(endpoint) as (read, write, _cb):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
        info = getattr(init, "serverInfo", None)
        return {
            "ok": True,
            "endpoint": endpoint,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "server": {
                "name": getattr(info, "name", None),
                "version": getattr(info, "version", None),
            },
            "protocol": getattr(init, "protocolVersion", None),
            "tools_count": len(getattr(tools, "tools", []) or []),
            "error": None,
        }
    except Exception as e:                                        # noqa: BLE001
        return {
            "ok": False,
            "endpoint": endpoint,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "server": None,
            "protocol": None,
            "tools_count": 0,
            "error": f"{type(e).__name__}: {e}",
        }


# ---------------------------------------------------------------------------
# 工具功能分组（2026-10-15）
#
# 🔴 为什么必须显式声明、且必须声明在服务端：
#   · MCP 的 `ToolAnnotations` 只有四个标准字段（readOnlyHint / destructiveHint /
#     idempotentHint / openWorldHint），描述的是**操作危险等级**、不是**功能用途**。
#     按危险等级分组时「只读」一组吃到 24 个里的 14 个（58%），而「非幂等」只有 2 个
#     （最大:最小 = 7:1）—— 分组这一层几乎不产生收益；且用户找工具的心智是
#     「我要干什么」而不是「这操作危不危险」。
#   · 归属无法从注解自动派生（需要人的判断），所以必须显式声明。
#   · **不能放前端**：`tests/test_web.py` 有断言钉住控制台进程 `sys.modules` 里没有
#     `server`，`/api/tools` 是走 MCP 协议取的清单 ⇒ 前端拿不到工具定义处的上下文；
#     把表放前端就会变成「随工具数增长而失配的第二事实源」。
#   · 「不重、不漏、无陈旧键」由 `tests/test_web.py` 的守卫断言钉死。
#
# 注意：本表**不是**危险等级的替代 —— 危险等级仍由 `ToolAnnotations` 派生，
# 在前端降级为工具行内的小徽章与顶部筛选条（见 `web/js/features/console.js`）。
# ---------------------------------------------------------------------------
TOOL_GROUPS = [
    {"key": "content", "label": "内容读写", "desc": "取用与落盘单条记忆"},
    {"key": "search", "label": "检索", "desc": "按关键词、相似度或时间找条目"},
    {"key": "graph", "label": "图谱结构", "desc": "双向链接拓扑与孤立条目"},
    {"key": "health", "label": "体检诊断", "desc": "全库体检、对账与统计"},
    {"key": "maintain", "label": "库维护", "desc": "索引重建、批量标签与热度调整"},
    {"key": "lifecycle", "label": "生命周期与系统", "desc": "归档、恢复、删除与热重启"},
]
# 兜底组：新增工具若忘记登记，会落到这里并在左栏显式出现 —— 静默失配比报错更难发现。
TOOL_GROUP_OTHER = {"key": "other", "label": "未归类", "desc": "尚未登记分组的新工具"}

_TOOL_GROUP_OF = {
    # 内容读写
    "memory_read": "content",
    "memory_write": "content",
    "memory_update_metadata": "content",
    # 检索
    "memory_search": "search",
    "memory_smart_search": "search",
    "memory_list": "search",
    "memory_recent": "search",
    "memory_context": "search",
    # 图谱结构
    "memory_graph": "graph",
    "memory_orphans": "graph",
    # 体检诊断
    "memory_audit": "health",
    "memory_doctor": "health",
    "memory_schema_diff": "health",
    "memory_stats": "health",
    # 库维护
    "memory_index_draft": "maintain",
    "memory_rebuild_links": "maintain",
    "memory_heat_suggest": "maintain",
    "memory_batch_tier": "maintain",
    "memory_batch_tag": "maintain",
    # 生命周期与系统
    "memory_archive": "lifecycle",
    "memory_archive_old": "lifecycle",
    "memory_restore": "lifecycle",
    "memory_delete": "lifecycle",
    "memory_restart": "lifecycle",
}


async def mcp_tools(endpoint: str, timeout: float = MCP_TIMEOUT) -> list[dict]:
    with anyio.fail_after(timeout):
        async with streamablehttp_client(endpoint) as (read, write, _cb):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.list_tools()
    out = []
    ungrouped = []
    for t in (getattr(res, "tools", None) or []):
        ann = getattr(t, "annotations", None)
        name = getattr(t, "name", "")
        group = _TOOL_GROUP_OF.get(name)
        if group is None:
            group = TOOL_GROUP_OTHER["key"]
            ungrouped.append(name)
        out.append({
            "name": name,
            "title": getattr(t, "title", None),
            "description": getattr(t, "description", "") or "",
            "inputSchema": getattr(t, "inputSchema", None) or {"type": "object"},
            "annotations": (ann.model_dump() if hasattr(ann, "model_dump")
                            else (ann if isinstance(ann, dict) else None)),
            "group": group,
        })
    out.sort(key=lambda x: x["name"])
    if ungrouped:
        logger.warning("工具未登记功能分组，已归入 %r：%s",
                       TOOL_GROUP_OTHER["key"], ", ".join(ungrouped))
    return out


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------
def _err(status: int, message: str, **extra: Any) -> JSONResponse:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return JSONResponse(payload, status_code=status)


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise WebConfigError(f"请求体超过 {MAX_BODY_BYTES // 1024} KiB。")
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise WebConfigError(f"请求体不是合法 JSON：{e}")
    if not isinstance(data, dict):
        raise WebConfigError("请求体必须是 JSON 对象。")
    return data


class WebConsole:
    """把配置与处理器收在一起，便于测试注入 stub。"""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config()

    # -------- 只读接口 --------
    async def health(self, request: Request) -> Response:
        endpoint = resolve_mcp(self.cfg)
        return JSONResponse(await mcp_health(endpoint))

    async def tools(self, request: Request) -> Response:
        endpoint = resolve_mcp(self.cfg)
        try:
            items = await mcp_tools(endpoint)
        except Exception as e:                                    # noqa: BLE001
            return _err(502, f"无法获取工具清单：{type(e).__name__}: {e}",
                        endpoint=endpoint)
        groups = list(TOOL_GROUPS)
        if any(i.get("group") == TOOL_GROUP_OTHER["key"] for i in items):
            groups.append(TOOL_GROUP_OTHER)
        return JSONResponse({"ok": True, "endpoint": endpoint,
                             "count": len(items), "groups": groups,
                             "tools": items})

    async def overview(self, request: Request) -> Response:
        """一次会话取齐概览所需数据（避免 N 次握手）。"""
        endpoint = resolve_mcp(self.cfg)
        try:
            stats, recent, listing = await mcp_session_calls(endpoint, [
                ("memory_stats", {}),
                ("memory_recent", {"days": 30, "limit": 12, "output_format": "json"}),
                ("memory_list", {"limit": 14, "output_format": "json"}),
            ])
        except Exception as e:                                    # noqa: BLE001
            return _err(502, f"无法获取概览：{type(e).__name__}: {e}",
                        endpoint=endpoint)
        return JSONResponse({"ok": True, "endpoint": endpoint,
                             "stats": stats, "recent": recent, "list": listing})

    async def activity(self, request: Request) -> Response:
        """概览右栏「最近更新」热力日历的数据面：**服务端按天聚合**（2026-10-15）。

        为什么不复用 ``overview`` 里那条 ``memory_recent``：
          ① 那条是 30 天 / 12 条口径，只够铺一两天，撑不起按月着色的日历；
          ② 任何回传浏览器的工具结果都要过 ``_clip``（``_THUMB`` = 40000 字符），
             而 120 天的 ``memory_recent`` JSON 约 **13 万字符** ⇒ 前端拿到的是
             **残缺 JSON**，``JSON.parse`` 必然抛错（本轮先按「前端走 /api/call」
             实现时实测踩到，页面只落到空态）。⇒ 聚合只能在后端做，
             并且这一步要显式 ``clip=False``。

        回传的是紧凑结构，不是原始条目列表：
        ``{"ok","days","total","today","counts":{"YYYY-MM-DD":n},
        "items":{"YYYY-MM-DD":[["HH:MM","标题","摘要"], …]}}``
        —— 日期由 key 给出，行里只留时刻，摘要已在 ``memory_recent`` 侧截到 80 字。
        """
        days = _bounded_int(request.query_params.get("days"), 120, 1, 365)
        endpoint = resolve_mcp(self.cfg)
        try:
            results = await mcp_session_calls(
                endpoint,
                # limit 要给足：memory_recent 用 entries[:limit] 截取，传 0 会得到空表。
                [("memory_recent", {"days": days, "limit": 5000,
                                    "output_format": "json"})],
                clip=False,
            )
        except Exception as e:                                    # noqa: BLE001
            return _err(502, f"无法读取更新日历：{type(e).__name__}: {e}")
        try:
            payload = json.loads(results[0].get("text") or "{}")
        except Exception as e:                                    # noqa: BLE001
            return _err(502, f"更新日历数据不是合法 JSON：{type(e).__name__}: {e}")

        counts: dict[str, int] = {}
        items: dict[str, list[list[str]]] = {}
        for entry in (payload.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            day, hhmm = _local_stamp(entry.get("updated"))
            if not day:
                continue
            counts[day] = counts.get(day, 0) + 1
            items.setdefault(day, []).append([
                hhmm, str(entry.get("title") or ""), str(entry.get("summary") or ""),
            ])
        for rows in items.values():       # 组内新的在上（外层已是倒序，这里只稳一次）
            rows.sort(key=lambda r: r[0], reverse=True)
        return JSONResponse({
            "ok": True,
            "days": days,
            "total": sum(counts.values()),
            "today": _local_stamp(datetime.now())[0],
            "counts": counts,
            "items": items,
        })

    async def config_get(self, request: Request) -> Response:
        app_cfg = load_app_config()
        vault = resolve_vault()
        return JSONResponse({
            "ok": True,
            "app": app_cfg,
            "resolved": {
                "mcp_endpoint": resolve_mcp(self.cfg),
                "mcp_default": self.cfg["mcp"],
                "vault": str(vault) if vault else None,
                "vault_from_env": bool(_env_str("AI_MEMORY_DIR")),
                "config_path": str(config_path()),
                "config_write": self.cfg["config_write"],
                "auth_enabled": self.cfg["auth_enabled"],
                "static": str(self.cfg["static"]),
                "version": WEB_VERSION,
                "host": self.cfg["host"],
                "port": self.cfg["port"],
            },
        })

    async def config_post(self, request: Request) -> Response:
        try:
            body = await _json_body(request)
        except WebConfigError as e:
            return _err(400, str(e))
        saved = save_app_config(body)
        return JSONResponse({"ok": True, "app": saved,
                             "resolved": {"mcp_endpoint": resolve_mcp(self.cfg)}})

    async def thresholds_get(self, request: Request) -> Response:
        return JSONResponse({"ok": True, **thresholds_state(self.cfg)})

    async def thresholds_post(self, request: Request) -> Response:
        try:
            body = await _json_body(request)
            values = body.get("values")
            if not isinstance(values, dict):
                raise WebConfigError("需要 {'values': {键: 值}}。")
            state = write_thresholds(values, self.cfg)
        except WebConfigError as e:
            return _err(400, str(e))
        except Exception as e:                                    # noqa: BLE001
            return _err(500, f"写入失败：{type(e).__name__}: {e}")
        return JSONResponse({"ok": True, **state})

    # -------- 调试台 --------
    async def call(self, request: Request) -> Response:
        try:
            body = await _json_body(request)
        except WebConfigError as e:
            return _err(400, str(e))
        tool = str(body.get("tool") or "").strip()
        if not tool:
            return _err(400, "缺少 tool 字段。")
        arguments = body.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _err(400, "arguments 必须是 JSON 对象。")

        endpoint = resolve_mcp(self.cfg)
        t0 = time.perf_counter()
        try:
            results = await mcp_session_calls(endpoint, [(tool, arguments)])
        except Exception as e:                                    # noqa: BLE001
            return _err(502, f"调用失败：{type(e).__name__}: {e}")
        res = dict(results[0])
        res["tool"] = tool
        res["arguments"] = arguments
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        res["ok"] = not res["isError"]
        return JSONResponse(res)

    # -------- 静态页面 --------
    async def index(self, request: Request) -> Response:
        return FileResponse(self.cfg["static"] / "index.html",
                            headers={"Cache-Control": "no-store"})


class AuthMiddleware(BaseHTTPMiddleware):
    """``/api/*`` 的 token 校验（静态资源不校验：里面没有秘密）。"""

    def __init__(self, app, token: str, header: str = "Authorization"):
        super().__init__(app)
        self.token = token
        self.header = header

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return Response(status_code=204)
        supplied = ""
        raw = request.headers.get(self.header, "")
        if raw.lower().startswith("bearer "):
            supplied = raw[7:].strip()
        if not supplied:
            supplied = request.headers.get("X-Auth-Token", "").strip()
        if not supplied or not hmac.compare_digest(supplied, self.token):
            return _err(401, "未授权：请在页面上填入访问令牌。",
                        auth_required=True)
        return await call_next(request)


def create_app(cfg: dict | None = None) -> Starlette:
    cfg = cfg or config()
    console = WebConsole(cfg)

    routes = [
        Route("/api/health", console.health, methods=["GET"]),
        Route("/api/tools", console.tools, methods=["GET"]),
        Route("/api/overview", console.overview, methods=["GET"]),
        Route("/api/activity", console.activity, methods=["GET"]),
        Route("/api/call", console.call, methods=["POST"]),
        Route("/api/config", console.config_get, methods=["GET"]),
        Route("/api/config", console.config_post, methods=["POST"]),
        Route("/api/thresholds", console.thresholds_get, methods=["GET"]),
        Route("/api/thresholds", console.thresholds_post, methods=["POST"]),
        Mount("/assets", app=StaticFiles(directory=str(cfg["static"])), name="assets"),
        Route("/", console.index),
        Route("/{path:path}", console.index),      # 前端用 hash 路由，任何路径都回 index
    ]

    middleware: list[Middleware] = []
    if cfg["allowed_hosts"]:
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        middleware.append(Middleware(TrustedHostMiddleware,
                                     allowed_hosts=cfg["allowed_hosts"]))
    if cfg["auth_enabled"]:
        middleware.append(Middleware(AuthMiddleware, token=cfg["token"]))

    return Starlette(routes=routes, middleware=middleware, debug=False)


__all__ = [
    "WEB_VERSION", "DEFAULT_WEB_HOST", "DEFAULT_WEB_PORT", "DEFAULT_MCP_URL",
    "WebConfigError", "config", "describe", "create_app", "WebConsole",
    "is_loopback", "static_dir", "config_path",
    "load_app_config", "save_app_config", "resolve_mcp", "resolve_vault",
    "render_toml", "thresholds_state", "write_thresholds",
    "mcp_health", "mcp_tools", "mcp_session_calls",
    "TOOL_GROUPS", "TOOL_GROUP_OTHER",
]
