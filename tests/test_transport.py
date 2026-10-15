#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Streamable HTTP 传输层测试（v3 阶段 1，2026-10-13）。

覆盖：
  A  transport_mode 归一化（含非法值 fail-fast）
  B  is_loopback 判定
  C  build_security 的三条安全规则（非回环必须给白名单 / 禁止关防护 / 回环默认）
  D  config() 各环境变量组合
  E  装配（替身 FastMCP）：stdio 不带参数、http 写 settings —— 不真起服务
  F  端到端 HTTP：真起 serve_http.py，客户端 initialize / list_tools / write /
     read，并验证远程模式下 memory_restart 被自动禁用
  G  fail-fast 的**进程级**验证：非回环监听且未给白名单时，进程必须非零退出
     且输出明确提示（而不是静默起来一个不校验 Host 的服务）
  H  主程序零改动：不带 AI_MEMORY_TRANSPORT 时，server.py 仍是可用的 stdio 服务

运行:
    python tests/test_transport.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑（server.py import mcp；HTTP 客户端需要 httpx）。
    全程把 AI_MEMORY_DIR 指向临时目录，绝不触碰真实记忆库。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import 被测模块 -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-transport-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import transport_http  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def section(name: str) -> None:
    print(f"\n== {name} ==")


# ---------------------------------------------------------------------------
# 环境变量隔离（transport_http 的函数每次调用都读 env —— 与 server 的
# 「导入时读一次」不同，故可直接改 os.environ 测试）
# ---------------------------------------------------------------------------
_ENV_KEYS = [
    "AI_MEMORY_TRANSPORT", "AI_MEMORY_HTTP_HOST", "AI_MEMORY_HTTP_PORT",
    "AI_MEMORY_HTTP_PATH", "AI_MEMORY_HTTP_ALLOWED_HOSTS",
    "AI_MEMORY_HTTP_ALLOWED_ORIGINS", "AI_MEMORY_HTTP_DNS_REBINDING",
    "AI_MEMORY_HTTP_STATELESS", "AI_MEMORY_HTTP_SESSION_IDLE_TIMEOUT",
    "AI_MEMORY_HTTP_MAX_SESSIONS",
]
_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}


@contextmanager
def env(**kw):
    """临时设置传输层环境变量：进入时清空全部相关键，退出时原样还原。"""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = str(v)
    try:
        yield
    finally:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _raises(fn, exc_type) -> tuple[bool, str]:
    try:
        fn()
    except exc_type as e:  # noqa: BLE001
        return True, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"抛了 {type(e).__name__}（期望 {exc_type.__name__}）: {e}"
    return False, "没有抛异常"


CfgErr = transport_http.TransportConfigError

# =====================================================================
section("A transport_mode 归一化")
with env():
    check("A1 未设置 ⇒ stdio", transport_http.transport_mode() == "stdio")
with env(AI_MEMORY_TRANSPORT=""):
    check("A2 空串 ⇒ stdio", transport_http.transport_mode() == "stdio")
with env(AI_MEMORY_TRANSPORT="local"):
    check("A3 local 别名 ⇒ stdio", transport_http.transport_mode() == "stdio")
with env(AI_MEMORY_TRANSPORT="http"):
    check("A4 http ⇒ streamable-http", transport_http.transport_mode() == "streamable-http")
with env(AI_MEMORY_TRANSPORT="streamable-http"):
    check("A5 原生名 ⇒ streamable-http", transport_http.transport_mode() == "streamable-http")
with env(AI_MEMORY_TRANSPORT="  HTTP  "):
    check("A6 大小写/空白归一", transport_http.transport_mode() == "streamable-http")
with env(AI_MEMORY_TRANSPORT="https"):
    ok, msg = _raises(transport_http.transport_mode, CfgErr)
    check("A7 https 不是合法别名（不给出虚假的 TLS 安全感）", ok, msg)
with env(AI_MEMORY_TRANSPORT="bogus"):
    ok, msg = _raises(transport_http.transport_mode, CfgErr)
    check("A8 非法值 fail-fast（不静默回退 stdio）", ok, msg)

# =====================================================================
section("B is_loopback")
for h, want in [("127.0.0.1", True), ("localhost", True), ("::1", True),
                ("[::1]", True), ("0.0.0.0", False), ("192.168.1.10", False),
                ("example.com", False)]:
    check(f"B {h!r} ⇒ {want}", transport_http.is_loopback(h) is want)

# =====================================================================
section("C build_security（三条安全规则）")
sec = transport_http.build_security("127.0.0.1", 8700)
check("C1 回环+空白名单 ⇒ 用回环三元组默认",
      sec.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"], str(sec.allowed_hosts))
check("C1b 回环默认启用 DNS rebinding 防护",
      sec.enable_dns_rebinding_protection is True)

ok, msg = _raises(lambda: transport_http.build_security("0.0.0.0", 8700), CfgErr)
check("C2 非回环+空白名单 ⇒ 拒绝启动", ok, msg)
check("C2b 拒绝信息里给出了变量名与示例",
      ok and "AI_MEMORY_HTTP_ALLOWED_HOSTS" in msg and "192.168.1.10" in msg, msg[:160])

sec3 = transport_http.build_security(
    "0.0.0.0", 8700, allowed_hosts=["192.168.1.10:8700", "localhost:8700"])
check("C3 非回环+显式白名单 ⇒ 通过且原样保留",
      sec3.allowed_hosts == ["192.168.1.10:8700", "localhost:8700"], str(sec3.allowed_hosts))

ok, msg = _raises(
    lambda: transport_http.build_security("127.0.0.1", 8700, enable_dns_rebinding=False),
    CfgErr)
check("C4 关闭 DNS rebinding 防护 ⇒ 一律拒绝（回环也不行）", ok, msg)

sec5 = transport_http.build_security(
    "0.0.0.0", 9000,
    allowed_hosts=["a:9000", "a:9000", "b:9000", "", "  "],
    allowed_origins=["http://a", "http://a"])
check("C5 白名单去重保序 + 丢空项",
      sec5.allowed_hosts == ["a:9000", "b:9000"], str(sec5.allowed_hosts))
check("C6 origins 去重", sec5.allowed_origins == ["http://a"], str(sec5.allowed_origins))

# =====================================================================
section("D config() 环境变量解析")
with env():
    check("D1 未设置 ⇒ mode=stdio 且不解析其余字段",
          transport_http.config() == {"mode": "stdio"})

with env(AI_MEMORY_TRANSPORT="http"):
    c = transport_http.config()
    check("D2 默认 host/port/path", (c["host"], c["port"], c["path"]) == ("127.0.0.1", 8700, "/mcp"),
          str({k: c[k] for k in ("host", "port", "path")}))
    check("D2b 默认 stateless=False / idle=1800 / max=10000",
          (c["stateless"], c["session_idle_timeout"], c["max_sessions"]) == (False, 1800, 10000),
          str({k: c[k] for k in ("stateless", "session_idle_timeout", "max_sessions")}))

with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_HOST="0.0.0.0"):
    ok, msg = _raises(transport_http.config, CfgErr)
    check("D3 非回环+无白名单 ⇒ config 拒绝", ok, msg)

with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_HOST="0.0.0.0",
         AI_MEMORY_HTTP_ALLOWED_HOSTS="10.0.0.5:8700"):
    c = transport_http.config()
    check("D4 非回环+白名单 ⇒ 通过", c["security"].allowed_hosts == ["10.0.0.5:8700"],
          str(c["security"].allowed_hosts))

with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_PATH="mcp"):
    check("D5 path 无前导 / ⇒ 自动补上", transport_http.config()["path"] == "/mcp")

with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_PORT="0"):
    ok, msg = _raises(transport_http.config, CfgErr)
    check("D6 port=0 非法 ⇒ 拒绝", ok, msg)
with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_PORT="70000"):
    ok, msg = _raises(transport_http.config, CfgErr)
    check("D6b port=70000 非法 ⇒ 拒绝", ok, msg)
with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_PORT="abc"):
    ok, msg = _raises(transport_http.config, CfgErr)
    check("D6c port 非整数 ⇒ 拒绝", ok, msg)

with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_STATELESS="1",
         AI_MEMORY_HTTP_SESSION_IDLE_TIMEOUT="60", AI_MEMORY_HTTP_MAX_SESSIONS="8"):
    c = transport_http.config()
    check("D7 stateless/超时/上限可配",
          (c["stateless"], c["session_idle_timeout"], c["max_sessions"]) == (True, 60, 8),
          str({k: c[k] for k in ("stateless", "session_idle_timeout", "max_sessions")}))

# =====================================================================
section("E 装配（替身 FastMCP，不真起服务）")


class _FakeSettings:
    """允许任意属性赋值（FastMCP.settings 是 pydantic BaseSettings，可写）。"""


class _FakeMCP:
    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.calls: list[tuple[tuple, dict]] = []

    def run(self, *a, **kw):
        self.calls.append((a, kw))


with env():
    f1 = _FakeMCP()
    transport_http.run(f1)
    check("E1 stdio 分支调用 mcp.run() 且不带参数", f1.calls == [((), {})], str(f1.calls))
    check("E1b stdio 分支不触碰 settings（零副作用）", not hasattr(f1.settings, "host"))

with env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_PORT="8791"):
    f2 = _FakeMCP()
    transport_http.run(f2)
    check("E2 http 分支传 transport=streamable-http",
          f2.calls == [((), {"transport": "streamable-http"})], str(f2.calls))
    check("E2b settings.host/port 已写入",
          (f2.settings.host, f2.settings.port) == ("127.0.0.1", 8791),
          f"{getattr(f2.settings, 'host', None)}:{getattr(f2.settings, 'port', None)}")
    check("E2c settings.transport_security 已写入且防护开启",
          getattr(f2.settings.transport_security, "enable_dns_rebinding_protection", False) is True)
    check("E2d streamable_http_path 已写入",
          f2.settings.streamable_http_path == "/mcp", getattr(f2.settings, "streamable_http_path", None))
    check("E2e 刻意不设 auth（避免 401→OAuth 发现流程陷阱）",
          getattr(f2.settings, "auth", None) is None)


# ---------------------------------------------------------------------------
# 子进程辅助
# ---------------------------------------------------------------------------
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _child_env(**extra) -> dict:
    e = dict(os.environ)
    e["AI_MEMORY_DIR"] = _TMP
    e["AI_MEMORY_METRICS"] = "0"
    for k in ("AI_MEMORY_TRANSPORT", "AI_MEMORY_HTTP_HOST", "AI_MEMORY_HTTP_PORT",
              "AI_MEMORY_HTTP_ALLOWED_HOSTS", "AI_MEMORY_HTTP_DNS_REBINDING",
              "AI_MEMORY_RESTART", "AI_MEMORY_RESUME", "AI_MEMORY_RESTART_CHAIN"):
        e.pop(k, None)
    e.update({k: str(v) for k, v in extra.items()})
    return e


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass


async def _wait_ready(proc: subprocess.Popen, port: int, timeout: float = 30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False, f"进程提前退出 rc={proc.returncode}"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True, "ok"
        except OSError:
            await asyncio.sleep(0.2)
    return False, f"等待 {timeout:.0f}s 仍未就绪"


# =====================================================================
section("F 端到端 HTTP（真起 serve_http.py）")


async def _http_e2e() -> tuple[bool, str]:
    port = _free_port()
    logpath = os.path.join(_TMP, "serve_http.log")
    logf = open(logpath, "wb")
    proc = subprocess.Popen(
        [sys.executable, str(_SERVER_DIR / "serve_http.py")],
        env=_child_env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_PORT=port),
        cwd=str(_SERVER_DIR), stdout=logf, stderr=logf)
    try:
        ok, why = await _wait_ready(proc, port)
        if not ok:
            logf.flush()
            return False, f"{why} | 子进程日志尾部：{_decode(Path(logpath).read_bytes())[-400:]}"

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url = f"http://127.0.0.1:{port}/mcp"
        last = ""
        for _attempt in range(4):
            try:
                async with streamablehttp_client(url) as (read, write, _get_sid):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = {t.name for t in tools.tools}
                        if "memory_read" not in names or "memory_write" not in names:
                            return False, f"工具列表缺关键工具；共 {len(names)} 个"
                        w = await session.call_tool(
                            "memory_write",
                            {"title": "传输层e2e", "content": "HTTP 通道连通性测试正文"})
                        if "传输层e2e" not in w.content[0].text:
                            return False, f"write 回显异常：{w.content[0].text[:160]}"
                        r = await session.call_tool("memory_read", {"title": "传输层e2e"})
                        if "HTTP 通道连通性测试正文" not in r.content[0].text:
                            return False, f"read 未取回正文：{r.content[0].text[:160]}"
                        mr = await session.call_tool("memory_restart", {})
                        if "禁用" not in mr.content[0].text:
                            return False, ("HTTP 模式下 memory_restart 未被自动禁用："
                                           + mr.content[0].text[:160])
                return True, f"工具 {len(names)} 个；initialize/list/write/read 全通；restart 已禁用"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                await asyncio.sleep(1.0)
        return False, f"连接重试耗尽：{last}"
    finally:
        _terminate(proc)
        logf.close()


try:
    _ok_f, _detail_f = asyncio.run(_http_e2e())
except Exception as _e:  # noqa: BLE001
    _ok_f, _detail_f = False, f"抛异常 {type(_e).__name__}: {_e}"
check("F1 HTTP 通道端到端（initialize/list_tools/write/read）", _ok_f, _detail_f)

# =====================================================================
section("G fail-fast 进程级验证（非回环 + 无白名单必须拒绝启动）")


def _failfast_e2e() -> tuple[bool, str]:
    env = _child_env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_HOST="0.0.0.0",
                     AI_MEMORY_HTTP_PORT=_free_port())
    p = subprocess.run([sys.executable, str(_SERVER_DIR / "serve_http.py")],
                       env=env, cwd=str(_SERVER_DIR),
                       capture_output=True, timeout=60)
    txt = _decode((p.stdout or b"") + (p.stderr or b""))
    hit = "AI_MEMORY_HTTP_ALLOWED_HOSTS" in txt
    ok = p.returncode != 0 and hit
    return ok, f"rc={p.returncode} 含白名单提示={hit} | {txt[-220:]}"


try:
    _ok_g, _detail_g = _failfast_e2e()
except Exception as _e:  # noqa: BLE001
    _ok_g, _detail_g = False, f"抛异常 {type(_e).__name__}: {_e}"
check("G1 非回环+无白名单 ⇒ 进程非零退出且提示明确", _ok_g, _detail_g)

# =====================================================================
section("H 主程序零改动（不带 TRANSPORT 时 server.py 仍是 stdio 服务）")


async def _stdio_e2e() -> tuple[bool, str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=[str(_SERVER_DIR / "server.py")], env=_child_env())
    with open(os.devnull, "w", encoding="utf-8") as null:
        async with stdio_client(params, errlog=null) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                n = len(tools.tools)
                return n == 24, f"stdio 工具数={n}（期望 24）"


try:
    _ok_h, _detail_h = asyncio.run(_stdio_e2e())
except Exception as _e:  # noqa: BLE001
    _ok_h, _detail_h = False, f"抛异常 {type(_e).__name__}: {_e}"
check("H1 server.py 无 env 时仍可 stdio 握手且工具数不变", _ok_h, _detail_h)

# =====================================================================
# cleanup
for k, v in _BASELINE.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
