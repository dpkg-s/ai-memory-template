#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-memory Web 控制台测试（v3.1，2026-10-13）。

覆盖：
  A  配置校验 / fail-fast（`serve_web.py --check` 的**进程级退出码**，不是看文案）
  B  前端静态资源完整性（挂载点 + 本地 vendor 三件 + app/style 非空）
  C  分层：导入控制台**不得**把 server 拉进 sys.modules（v3 判死项「不进程内直调」）
  D  鉴权中间件（httpx ASGI transport，无需端口）：无 token 401 / Bearer /
     X-Auth-Token / 静态资源不校验 / OPTIONS 204
  E  写保护：AI_MEMORY_WEB_CONFIG_WRITE=0 ⇒ POST /api/thresholds 被拒（400）
  F  端到端（真起 serve_http + serve_web）：health / tools(24) / overview /
     thresholds(8 键) / index 回落 / assets 可达 / /api/call 真往返 / 阈值写读往返
  H  更新面（2026-10-15）：白名单安全边界 / 行首状态位回归 / 三点 diff 语义 /
     restarter 显式失败（H1~H5 静态，F11 走真服务）

运行:
    python tests/test_web.py       # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑（starlette / uvicorn / httpx / anyio 全是 mcp 的
    传递依赖，零新增）。全程把 AI_MEMORY_DIR 指向临时目录，绝不触碰真实记忆库。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import 被测模块 -------------------
_TMP = Path(tempfile.mkdtemp(prefix="ai-memory-web-"))
os.environ["AI_MEMORY_DIR"] = str(_TMP / "vault")
(_TMP / "vault").mkdir(parents=True, exist_ok=True)
_VAULT = _TMP / "vault"
for _n in ["笔记A", "笔记B", "笔记C"]:
    (_VAULT / f"{_n}.md").write_text(
        "---\n"
        f"title: {_n}\ntags: [test]\ntype: project\ntier: warm\n"
        "created: 2026-10-13 10:00\nupdated: 2026-10-13 10:00\n"
        "confidence: high\nverified: true\n---\n\n"
        f"# {_n}\n\n这是 {_n}。\n\n- 列表项\n- **加粗**\n",
        encoding="utf-8", newline="\n")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import web_console  # noqa: E402

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


_ENV_KEYS = ["AI_MEMORY_WEB_HOST", "AI_MEMORY_WEB_PORT", "AI_MEMORY_WEB_MCP",
             "AI_MEMORY_WEB_TOKEN", "AI_MEMORY_WEB_ALLOWED_HOSTS",
             "AI_MEMORY_WEB_CONFIG", "AI_MEMORY_WEB_CONFIG_WRITE",
             "AI_MEMORY_WEB_STATIC"]
_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}


def _clean_env(**kw) -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)          # 净化：沙箱链会注入 shim 猴补丁
    for k in _ENV_KEYS:
        env.pop(k, None)
    env["AI_MEMORY_DIR"] = str(_VAULT)
    env["PYTHONUNBUFFERED"] = "1"
    env.update({k: str(v) for k, v in kw.items()})
    return env


def _check_rc(env: dict) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(_ROOT / "serve_web.py"), "--check"],
                       cwd=str(_ROOT), env=env, capture_output=True, timeout=90)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_port(port, secs=45) -> bool:
    t0 = time.time()
    while time.time() - t0 < secs:
        s = socket.socket()
        s.settimeout(2)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        finally:
            s.close()
        time.sleep(0.4)
    return False


# =====================================================================
section("A 配置校验 / fail-fast（进程级退出码）")
check("A1 is_loopback 判定",
      web_console.is_loopback("127.0.0.1") and web_console.is_loopback("localhost")
      and web_console.is_loopback("::1")
      and not web_console.is_loopback("0.0.0.0")
      and not web_console.is_loopback("192.168.1.5"), "")

rc, out = _check_rc(_clean_env())
check("A2 合法配置 --check 退出 0", rc == 0, f"rc={rc} out={out[-200:]}")

rc, out = _check_rc(_clean_env(AI_MEMORY_WEB_HOST="0.0.0.0"))
check("A3 非回环且无 token ⇒ 拒绝启动（rc=2）", rc == 2, f"rc={rc} out={out[-200:]}")

rc, out = _check_rc(_clean_env(AI_MEMORY_WEB_HOST="0.0.0.0",
                               AI_MEMORY_WEB_TOKEN="s3cret"))
check("A4 非回环 + token ⇒ 放行（rc=0）", rc == 0, f"rc={rc} out={out[-200:]}")

rc, out = _check_rc(_clean_env(AI_MEMORY_WEB_PORT="70000"))
check("A5 端口越界 ⇒ 拒绝启动（rc=2）", rc == 2, f"rc={rc}")

rc, out = _check_rc(_clean_env(AI_MEMORY_WEB_MCP="not-a-url"))
check("A6 MCP 端点非 http(s) ⇒ 拒绝启动（rc=2）", rc == 2, f"rc={rc}")

_empty = _TMP / "empty_static"
_empty.mkdir(exist_ok=True)
rc, out = _check_rc(_clean_env(AI_MEMORY_WEB_STATIC=str(_empty)))
check("A7 前端静态目录缺 index.html ⇒ 拒绝启动（rc=2）", rc == 2, f"rc={rc}")

# =====================================================================
section("B 前端静态资源完整性（本地化、零 CDN、零构建）")
_static = _ROOT / "web"
# ⚠️ 2026-10-15 前端重构后判据**重述**（不是放宽）：
#   · 入口由单文件 app.js 变为 js/main.js（import 图 30+ 模块）
#   · 样式由单文件 style.css 变为 css/1..8 共 8 片（引入顺序 = 层叠顺序）
#   · Vue 由 global 构建换为 ESM 构建（150,058 B，比 global 版小 126 KB）
#   · 新增 GSAP 3.13 + Flip 插件（UMD，供 core/anim.js 取用）
#   旧判据钉的是「三件套文件名」，重构后必红 —— 故改为「入口存在 +
#   模块树非空 + 分片齐备 + vendor 齐备」，这组更能抓到真问题
#   （入口在但 import 图断链、CSS 片漏引）。
for _f, _min in [("index.html", 800),
                 ("js/main.js", 200),
                 ("js/core/anim.js", 1500),
                 ("css/1-tokens.css", 2000),
                 ("css/8-responsive.css", 2000),
                 ("vendor/vue.esm-browser.prod.js", 50000),
                 ("vendor/gsap.min.js", 30000),
                 ("vendor/Flip.min.js", 10000),
                 ("vendor/marked.min.js", 10000),
                 ("vendor/purify.min.js", 8000)]:
    _p = _static / _f
    check(f"B {_f} 存在且非空(>{_min}B)",
          _p.is_file() and _p.stat().st_size > _min,
          f"size={_p.stat().st_size if _p.is_file() else '缺失'}")

# 模块树：core / components / views 三类都要有料（防「入口在、图断了」）
for _d, _n in [("js/core", 8), ("js/components", 12), ("js/views", 5)]:
    _got = len(list((_static / _d).glob("*.js")))
    check(f"B {_d}/ 模块数 ≥ {_n}", _got >= _n, f"实际 {_got}")

# CSS 分片必须齐 8 片且编号连续 —— 漏一片会静默丢一层样式
_css = sorted(p.name for p in (_static / "css").glob("*.css"))
check("B css/ 恰好 8 片且编号 1..8 连续",
      len(_css) == 8 and [n[0] for n in _css] == list("12345678"),
      f"实际 {_css}")

_html = (_static / "index.html").read_text(encoding="utf-8")
check("B index.html 有 Vue 挂载点 #app", 'id="app"' in _html, "")
check("B index.html 按序引用 8 片 CSS",
      all(f"/assets/css/{n}" in _html for n in _css), "")
check("B index.html 走 ESM 入口 /assets/js/main.js",
      "/assets/js/main.js" in _html and 'type="module"' in _html, "")
check("B index.html 引用本地 vendor（无 CDN）",
      all(x in _html for x in ["/assets/vendor/marked.min.js",
                               "/assets/vendor/purify.min.js",
                               "/assets/vendor/gsap.min.js",
                               "/assets/vendor/Flip.min.js"])
      and "http://cdn" not in _html and "https://unpkg" not in _html
      and "jsdelivr" not in _html, "")
check("B index.html 已不再引用旧单文件 app.js / style.css",
      "/assets/app.js" not in _html and "/assets/style.css" not in _html, "")
_main = (_static / "js" / "main.js").read_text(encoding="utf-8")
check("B main.js 以 ESM 具名导入 Vue（非 global 构建）",
      "../vendor/vue.esm-browser.prod.js" in _main
      and "{ createApp" in _main, "")

# =====================================================================
section("C 分层：控制台不得把 server 拉进内存")
check("C1 import web_console 后 sys.modules 里没有 server",
      "server" not in sys.modules,
      f"被拉入 ⇒ 控制台有进程内直调风险；modules={'server' in sys.modules}")


# =====================================================================
section("D 鉴权中间件（ASGI，无需端口）")
def _asgi(cfg: dict):
    import httpx
    return httpx.ASGITransport(app=web_console.create_app(cfg))


async def _req(cfg, method, path, headers=None, json_body=None):
    import httpx
    async with httpx.AsyncClient(transport=_asgi(cfg), base_url="http://t") as c:
        return await c.request(method, path, headers=headers or {}, json=json_body)


_cfg_auth = None
os.environ["AI_MEMORY_WEB_TOKEN"] = "s3cret"
_cfg_auth = web_console.config()
check("D0 设 token 后 auth_enabled=True", _cfg_auth["auth_enabled"], "")

_r = asyncio.run(_req(_cfg_auth, "GET", "/api/config"))
_body = json.loads(_r.text) if _r.text else {}
check("D1 无 token ⇒ 401 且标记 auth_required",
      _r.status_code == 401 and _body.get("auth_required") is True,
      f"status={_r.status_code} body={_r.text[:120]}")

_r = asyncio.run(_req(_cfg_auth, "GET", "/api/config",
                      {"Authorization": "Bearer s3cret"}))
check("D2 Authorization: Bearer 正确 token ⇒ 200", _r.status_code == 200,
      f"status={_r.status_code}")

_r = asyncio.run(_req(_cfg_auth, "GET", "/api/config",
                      {"X-Auth-Token": "s3cret"}))
check("D3 X-Auth-Token 亦被接受 ⇒ 200", _r.status_code == 200,
      f"status={_r.status_code}")

_r = asyncio.run(_req(_cfg_auth, "GET", "/api/config",
                      {"Authorization": "Bearer wrong"}))
check("D4 token 错误 ⇒ 401", _r.status_code == 401, f"status={_r.status_code}")

_r = asyncio.run(_req(_cfg_auth, "GET", "/"))
check("D5 静态资源不校验 token（GET / ⇒ 200）", _r.status_code == 200,
      f"status={_r.status_code}")

_r = asyncio.run(_req(_cfg_auth, "OPTIONS", "/api/config"))
check("D6 OPTIONS 预检 ⇒ 204", _r.status_code == 204, f"status={_r.status_code}")

# =====================================================================
section("E 写保护（AI_MEMORY_WEB_CONFIG_WRITE=0）")
_cfg_ro = web_console.config()
_cfg_ro = dict(_cfg_ro, config_write=False)
_r = asyncio.run(_req(_cfg_ro, "POST", "/api/thresholds",
                      {"Authorization": "Bearer s3cret"},
                      json_body={"values": {"heat_hot_min": 5}}))
_ok = _r.status_code == 400 and "只读" in _r.text
check("E1 只读模式下写阈值 ⇒ 400 且提示只读", _ok,
      f"status={_r.status_code} body={_r.text[:140]}")

# =====================================================================
section("F 端到端（真起 serve_http + serve_web）")
mcp_port, web_port = free_port(), free_port()
env = _clean_env(AI_MEMORY_TRANSPORT="http", AI_MEMORY_HTTP_HOST="127.0.0.1",
                 AI_MEMORY_HTTP_PORT=str(mcp_port), AI_MEMORY_HTTP_PATH="/mcp",
                 AI_MEMORY_RESTART="0", AI_MEMORY_METRICS="0")
lf = open(_TMP / "mcp.log", "wb")
p_mcp = subprocess.Popen([sys.executable, str(_ROOT / "serve_http.py")],
                         cwd=str(_ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
wenv = _clean_env(AI_MEMORY_WEB_HOST="127.0.0.1", AI_MEMORY_WEB_PORT=str(web_port),
                  AI_MEMORY_WEB_MCP=f"http://127.0.0.1:{mcp_port}/mcp",
                  AI_MEMORY_WEB_CONFIG=str(_TMP / "webcfg" / "config.json"))
lf2 = open(_TMP / "web.log", "wb")
p_web = subprocess.Popen([sys.executable, str(_ROOT / "serve_web.py")],
                         cwd=str(_ROOT), env=wenv, stdout=lf2, stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{web_port}"


# 时间/日期形状判据（F3b 用）——写在模块级，避免每次都重新编译
_HHMM_RE = re.compile(r"\d{2}:\d{2}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _http(path, method="GET", body=None, timeout=60):
    req = urllib.request.Request(base + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                # noqa: PERF203
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                             # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
section("G 工具功能分组（方案 B，2026-10-15）")
# G1~G5 是**静态守卫**：`server.py` 里 `@mcp.tool` 注册的工具名 ↔
# `web_console._TOOL_GROUP_OF` 双向一致 —— 不重、不漏、无陈旧键、无空组。
# 🔴 为什么必须有：把分组表放服务端，正是为了不让前端变成「会随工具数增长而
# 失配的第二事实源」；但表本身仍会与工具定义脱节（新增工具忘登记 / 删了工具
# 留下陈旧键）。两个方向的失配都靠这里钉住，而不是靠人记得同步改。
import ast as _ast  # noqa: E402

_srv_py = Path(__file__).resolve().parent.parent / "server.py"
_registered: set[str] = set()
for _node in _ast.walk(_ast.parse(_srv_py.read_text(encoding="utf-8"))):
    if not isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
        continue
    for _dec in _node.decorator_list:
        _f = _dec.func if isinstance(_dec, _ast.Call) else _dec
        if getattr(_f, "attr", None) == "tool":
            _registered.add(_node.name)

_mapped = set(web_console._TOOL_GROUP_OF)
_group_keys = {g["key"] for g in web_console.TOOL_GROUPS}
_used_keys = set(web_console._TOOL_GROUP_OF.values())

check("G1 server.py 注册了 24 个 @mcp.tool 工具",
      len(_registered) == 24, f"实际 {len(_registered)} 个")
check("G2 分组表无遗漏（每个已注册工具都有归属）",
      not (_registered - _mapped), ", ".join(sorted(_registered - _mapped)))
check("G3 分组表无陈旧键（没有已不存在的工具）",
      not (_mapped - _registered), ", ".join(sorted(_mapped - _registered)))
check("G4 归属值都落在 TOOL_GROUPS 的 key 内",
      _used_keys <= _group_keys,
      ", ".join(sorted(_used_keys - _group_keys)))
check("G5 每个分组都至少有 1 个工具（无空组）",
      _group_keys <= _used_keys, ", ".join(sorted(_group_keys - _used_keys)))


# ---------------------------------------------------------------------------
section("H 更新面（2026-10-15）")
# H1/H2 钉的是**安全边界**：白名单决定「更新」能覆盖哪些文件。少了控制台后端或
# 前端目录，功能就名不副实；混进 *.md / *.db，就会碰到 vault 数据。
_wl = set(web_console.UPDATE_PATHS)
check("H1 白名单覆盖控制台后端四件套 + 前端目录 + 核心模块",
      {"web_console.py", "serve_web.py", "serve_http.py", "transport_http.py",
       "server.py", "memory_config.py", web_console.UPDATE_FRONTEND_DIR} <= _wl,
      f"缺={sorted({'web_console.py', 'serve_web.py'} - _wl)}")
check("H2 白名单不含数据类文件（.md / .db / .sqlite）",
      not any(str(p).endswith((".md", ".db", ".sqlite", ".sqlite3")) for p in _wl),
      ", ".join(sorted(_wl)))


def _mk_repo(path: Path, files: dict) -> None:
    """造一个最小真仓库并提交一轮 —— H3/H4 要在真 git 输出上验证解析，而非打桩。"""
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                ["config", "user.name", "t"], ["config", "core.autocrlf", "false"]):
        subprocess.run(["git", *cmd], cwd=str(path), capture_output=True, timeout=30)
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(path),
                   capture_output=True, timeout=30)


# H3 回归保护：`_whitelist_dirty` 曾因 `_git` 整包 strip 吞掉**行首状态位**，
#    把 `memory_config.py` 读成 `emory_config.py`（少一个字符）。这里刻意只改
#    工作区、不 add，让 porcelain 首行是 ` M 路径`。
_repo = _TMP / "h_repo"
_mk_repo(_repo, {"first.py": "x = 1\n", "second.py": "y = 1\n"})
(_repo / "first.py").write_text("x = 2\n", encoding="utf-8", newline="\n")
_dirt = web_console._whitelist_dirty(_repo, ("first.py", "second.py"))
check("H3 _whitelist_dirty 保留首行完整路径（行首状态位不被吞）",
      _dirt == ["first.py"], f"得到 {_dirt!r}")

# H4 三点语义：本地领先时 `HEAD..ref` 会把**本地已提交的新文件**列成待删除。
#    造「本地新增 third.py 并提交、ref 指上一提交」的局面 —— 两点会报 D，
#    三点（merge-base..ref）必须为空。
(_repo / "third.py").write_text("z = 1\n", encoding="utf-8", newline="\n")
subprocess.run(["git", "add", "."], cwd=str(_repo), capture_output=True, timeout=30)
subprocess.run(["git", "commit", "-qm", "second"], cwd=str(_repo),
               capture_output=True, timeout=30)
_two_point = subprocess.run(["git", "diff", "--name-status", "HEAD..HEAD~1",
                             "--", "third.py"], cwd=str(_repo),
                            capture_output=True, timeout=30).stdout.decode()
_three_point, _ = web_console._diff_head_to(_repo, "HEAD~1", ("third.py",))
check("H4 _diff_head_to 用三点语义（本地新增不被误列成待删除）",
      _three_point == [] and _two_point.strip().startswith("D"),
      f"三点={_three_point!r} 两点={_two_point.strip()!r}")

# H5 restarter 的配置失败必须显式：设了不存在的路径就返回空串，不静默回落桌面
#    —— 否则「为什么不能重启」会变得不可复现。
_keep_env = os.environ.get("AI_MEMORY_RESTARTER")
os.environ["AI_MEMORY_RESTARTER"] = str(_TMP / "no-such-helper.py")
_got_restarter = web_console.restarter_path()
if _keep_env is None:
    os.environ.pop("AI_MEMORY_RESTARTER", None)
else:
    os.environ["AI_MEMORY_RESTARTER"] = _keep_env
check("H5 AI_MEMORY_RESTARTER 指向不存在文件时返回空串（不静默回落）",
      _got_restarter == "", f"得到 {_got_restarter!r}")


try:
    check("F0 MCP 端点起来", wait_port(mcp_port), "")
    check("F0 控制台起来", wait_port(web_port), "")

    st, txt = _http("/api/health")
    j = json.loads(txt) if st == 200 else {}
    check("F1 /api/health 200 且 ok",
          st == 200 and j.get("ok") is True and j.get("server"),
          f"status={st} body={txt[:160]}")

    st, txt = _http("/api/tools")
    j = json.loads(txt) if st == 200 else {}
    names = {t.get("name") for t in j.get("tools", [])} if j else set()
    check("F2 /api/tools 返回 24 个工具且含关键项",
          st == 200 and j.get("count") == 24 and {"memory_batch_tag",
                                                  "memory_orphans"} <= names,
          f"status={st} count={j.get('count')}")

    # F2b~F2d 工具功能分组（方案 B，2026-10-15）。
    # G 段已静态证明「表本身自洽」，这里证明它**真的经 MCP 协议 + /api/tools
    # 走到了前端** —— 控制台不 import server，两段缺一不可。
    _gkeys = [g.get("key") for g in j.get("groups", [])] if j else []
    _gtools = j.get("tools", []) if j else []
    check("F2b /api/tools 下发 6 个功能组且首组为 content",
          st == 200 and len(_gkeys) == 6 and _gkeys[0] == "content",
          f"groups={_gkeys}")
    check("F2c 24 项工具全部带 group，且值都在组定义内",
          st == 200 and len(_gtools) == 24
          and all(t.get("group") in _gkeys for t in _gtools),
          ", ".join(sorted({str(t.get("group")) for t in _gtools} - set(_gkeys))))
    check("F2d 没有工具落入 other 兜底组（映射表无遗漏）",
          st == 200 and all(t.get("group") != "other" for t in _gtools), "")

    st, txt = _http("/api/overview")
    j = json.loads(txt) if st == 200 else {}
    check("F3 /api/overview 200 且带 stats/recent/list",
          st == 200 and j.get("ok") is True and isinstance(j.get("stats"), dict)
          and "recent" in j and "list" in j, f"status={st} keys={list(j)[:6]}")

    # F3b /api/activity —— 概览「最近更新」热力日历的数据面（2026-10-15 新增）。
    #     这一条的存在理由：该端点**必须**是「按天聚合的紧凑结构」而不是原始条目列表。
    #     走 /api/call 直接拿 memory_recent 的写法已被实测否决 —— `_clip`
    #     （_THUMB = 40000 字符）会把 120 天的 JSON（约 13 万字符）截断成非法 JSON，
    #     前端 JSON.parse 必然抛错。所以这里钉住聚合契约：
    #       ① total == sum(counts)；
    #       ② items 的键必须都在 counts 里（不许出现「有明细没计数」的天）；
    #       ③ 每行必须是 [HH:MM, 标题, 摘要]，且时刻是**本地**格式 ——
    #          回归保护：曾按 UTC 直接切前 10 位，会让本地凌晨写的条目整体差一天；
    #       ④ today 必须是合法日期串（前端拿它标「今天」，不再自己算日期）。
    st, txt = _http("/api/activity?days=120")
    j = json.loads(txt) if st == 200 else {}
    counts = j.get("counts") or {}
    items = j.get("items") or {}
    orphan_keys = [k for k in items if k not in counts]
    bad_rows = [k for k, rows in items.items() for r in rows
                if not (isinstance(r, list) and len(r) == 3
                        and _HHMM_RE.fullmatch(str(r[0]))
                        and str(r[1]).strip())]
    check("F3b /api/activity 按天聚合且 counts/items/total 自洽",
          st == 200 and j.get("ok") is True
          and isinstance(counts, dict) and isinstance(items, dict)
          and j.get("total") == sum(counts.values())
          and not orphan_keys and not bad_rows
          and bool(_DATE_RE.fullmatch(str(j.get("today") or ""))),
          f"status={st} total={j.get('total')} "
          f"sum(counts)={sum(counts.values()) if counts else 0} "
          f"孤儿键={orphan_keys[:3]} 坏行={bad_rows[:3]} today={j.get('today')!r}")

    st2, txt2 = _http("/api/activity?days=abc")
    j2 = json.loads(txt2) if st2 == 200 else {}
    st3, txt3 = _http("/api/activity?days=99999")
    j3 = json.loads(txt3) if st3 == 200 else {}
    st4, txt4 = _http("/api/activity?days=0")
    j4 = json.loads(txt4) if st4 == 200 else {}
    check("F3c days 参数非法/越界会被夹住（非法→120，上限 365，下限 1）",
          st2 == 200 and j2.get("days") == 120
          and st3 == 200 and j3.get("days") == 365
          and st4 == 200 and j4.get("days") == 1,
          f"abc->{j2.get('days')} 99999->{j3.get('days')} 0->{j4.get('days')}")

    st, txt = _http("/api/thresholds")
    j = json.loads(txt) if st == 200 else {}
    keys = set((j.get("keys") or {}).keys())
    want = {"dup_title_jaccard", "dup_body_jaccard", "body_truncate_chars",
            "lock_timeout", "heat_cold_max", "heat_demote_hot_max",
            "heat_hot_min", "heat_decay_lambda"}
    check("F4 /api/thresholds 列出 8 个内置阈值键且可写",
          st == 200 and want <= keys and j.get("writable") is True,
          f"status={st} missing={sorted(want - keys)} writable={j.get('writable')}")

    # 接线断言：文案只有 memory_config.META 一处来源，接口必须把它带出来。
    # 断言口径是「**每个**键都非空」，而不是「至少有一个」—— 后者在 7 个键都空时照样绿。
    _no_meta = [k for k, v in (j.get("keys") or {}).items()
                if not str(v.get("label") or "").strip()
                or not str(v.get("desc") or "").strip()]
    check("F4b 每个阈值键都随接口下发 label/desc（界面据此渲染作用说明）",
          st == 200 and not _no_meta, f"缺文案的键={_no_meta}")

    # F11 更新面端点（2026-10-15）。只读本地状态这条必须是**真不联网**：
    #     `?fetch=0` 是打开配置页「关于」时的默认路径，它若能联网，就会在每次
    #     进页面时静默跑一次可能耗时几十秒的 git fetch。
    st, txt = _http("/api/update?fetch=0")
    j = json.loads(txt) if st == 200 else {}
    check("F11 GET /api/update?fetch=0 返回本地状态且 checked=false",
          st == 200 and j.get("checked") is False and j.get("is_repo") is True
          and bool(j.get("local_sha")) and isinstance(j.get("whitelist"), list)
          and len(j.get("whitelist") or []) >= 10,
          f"status={st} checked={j.get('checked')} is_repo={j.get('is_repo')}")

    st, txt = _http("/")
    check("F5 GET / 返回 index.html（含 #app）", st == 200 and 'id="app"' in txt,
          f"status={st} len={len(txt)}")

    st, txt = _http("/assets/js/main.js")
    check("F6 /assets/js/main.js 可达且非空", st == 200 and len(txt) > 200,
          f"status={st} len={len(txt)}")

    # ESM 拆分后必须有这一条：入口可达 ≠ import 图完整。任一模块取不到，
    # 整棵树都不会执行（页面停在 4 秒兜底），而只测入口的判据照样绿。
    _mods = ["/assets/js/core/anim.js", "/assets/js/core/store.js",
             "/assets/js/components/AnimatedNumber.js",
             "/assets/js/views/Root.js", "/assets/js/core/router.js"]
    _bad = []
    for _m in _mods:
        st_m, txt_m = _http(_m)
        if st_m != 200 or len(txt_m) < 100:
            _bad.append("%s(%s)" % (_m, st_m))
    check("F6b import 图内 5 个代表模块均可取",
          not _bad, f"取不到: {_bad}")

    _css_bad = []
    for _i in range(1, 9):
        _p = sorted((_ROOT / "web" / "css").glob(f"{_i}-*.css"))
        if not _p:
            _css_bad.append(f"{_i}-*")
            continue
        st_c, txt_c = _http("/assets/css/" + _p[0].name)
        if st_c != 200 or len(txt_c) < 500:
            _css_bad.append("%s(%s)" % (_p[0].name, st_c))
    check("F6c 8 片 CSS 均可取且非空", not _css_bad, f"异常: {_css_bad}")

    st, txt = _http("/some/deep/hash/route")
    check("F7 未知路径回落 index.html（hash 路由）",
          st == 200 and 'id="app"' in txt, f"status={st}")

    st, txt = _http("/api/call", "POST", {"tool": "memory_stats", "arguments": {}})
    j = json.loads(txt) if st == 200 else {}
    check("F8 POST /api/call 真往返 memory_stats",
          st == 200 and j.get("ok") is True and j.get("tool") == "memory_stats",
          f"status={st} body={txt[:160]}")

    # 契约：阈值是**整状态写**（前端 saveThresholds 也把 draft 的全部键一起提交）。
    # 因此「只给一个键」必须被明确拒绝，而不是静默写半份配置。
    st, txt = _http("/api/thresholds")
    all_keys = {k: v["value"] for k, v in json.loads(txt)["keys"].items()}
    st_p, txt_p = _http("/api/thresholds", "POST",
                        {"values": {"heat_hot_min": 7}})
    check("F9a 不完整键集 ⇒ 400 且明确列出缺失键（整状态写契约）",
          st_p == 400 and "缺少配置键" in txt_p,
          f"status={st_p} body={txt_p[:160]}")

    st, txt = _http("/api/thresholds", "POST",
                    {"values": dict(all_keys, heat_hot_min=7, lock_timeout=12)})
    j = json.loads(txt) if st == 200 else {}
    st2, txt2 = _http("/api/thresholds")
    kk = (json.loads(txt2).get("keys") or {}) if st2 == 200 else {}
    got, got2 = kk.get("heat_hot_min", {}).get("value"), kk.get("lock_timeout", {}).get("value")
    check("F9b 阈值写→读往返（heat_hot_min=7 / lock_timeout=12）",
          st == 200 and j.get("ok") is True and got == 7 and got2 == 12
          and (_TMP / "vault" / "memory.toml").is_file(),
          f"status={st} read_back=({got},{got2}) body={txt[:140]}")
    check("F10 阈值只写 memory.toml，vault 里 *.md 未被改动",
          len(list(_VAULT.glob("*.md"))) == 3
          and all("heat_hot_min" not in p.read_text(encoding="utf-8")
                  for p in _VAULT.glob("*.md")), "")
finally:
    for p in (p_web, p_mcp):
        try:
            p.terminate(); p.wait(timeout=8)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    try:
        lf.close(); lf2.close()
    except Exception:
        pass

# =====================================================================
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
