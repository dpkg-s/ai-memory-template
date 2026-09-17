#!/usr/bin/env python3
"""热重启（memory_restart）行为测试 —— 14 套中的「换进程」专项。

**本脚本绝不真的换掉自己**：execv 会终止测试进程。所有涉及换进程的路径都用替身
记录调用参数，只验证「参数对了、时机对了」。

覆盖：
  1. 会话恢复补丁真的把 ServerSession 起始状态改成 Initialized，且重复安装幂等
  2. 预检能拦下「新代码起不来」（真实的坏语法样本）与「argv 不可重放」，
     且在**真实 stdio 会话**里不挂死（stdin=DEVNULL 判据，防回归）
  3. 三道拒绝闸门：AI_MEMORY_RESTART=0 / 已有重启排队 / 接力链超限
  4. 延迟线程确实按 argv 调用了 execv，且换进程前先把标记写进环境
  5. 辅助纯函数（argv 重放、接力链计数、浮点 env 解析）

运行:
    python tests/test_restart.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-restart-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402
from mcp.server.session import InitializationState, ServerSession  # noqa: E402

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


# =====================================================================
section("会话恢复补丁（约束②：客户端不重发 initialize）")
status = server._install_session_resume_patch()
check("首次安装返回「已安装」", status == "已安装", f"实际 {status!r}")
status2 = server._install_session_resume_patch()
check("重复安装幂等（不重复包装）", "幂等" in status2, f"实际 {status2!r}")
check("SDK 的 InitializationState 有 Initialized 成员（补丁前提）",
      hasattr(InitializationState, "Initialized"))

try:
    opts = server.mcp._mcp_server.create_initialization_options()
except Exception as e:  # noqa: BLE001
    opts = None
    check("可取到 InitializationOptions", False, f"{type(e).__name__}: {e}")
else:
    check("可取到 InitializationOptions", True)

if opts is not None:
    try:
        # read/write stream 在 __init__ 里只被保存、不立即使用，故可用 None 占位
        sess = ServerSession(None, None, opts)  # type: ignore[arg-type]
        check("新会话起始即为 Initialized（补丁真正生效）",
              sess._initialization_state is InitializationState.Initialized,
              f"实际 {sess._initialization_state}")
    except Exception as e:  # noqa: BLE001
        check("新会话起始即为 Initialized（补丁真正生效）", False,
              f"构造 ServerSession 失败 {type(e).__name__}: {e}")

# =====================================================================
section("预检拦截（绝不把起不来的代码换上）")
_saved_dir = server._SERVER_DIR
_saved_argv_fn = server._restart_argv

bad_dir = Path(_TMP) / "bad_repo"
bad_dir.mkdir(parents=True, exist_ok=True)
(bad_dir / "server.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
server._SERVER_DIR = bad_dir
server._restart_argv = lambda: [sys.executable, str(bad_dir / "server.py")]  # type: ignore[assignment]
reason_bad = server._restart_preflight()
check("坏语法样本被拦下", bool(reason_bad) and "预检未通过" in (reason_bad or ""),
      f"{reason_bad!r}")

server._restart_argv = lambda: [sys.executable, "-c", "pass"]  # type: ignore[assignment]
reason_argv = server._restart_preflight()
check("不可重放的 argv 被拦下",
      bool(reason_argv) and "没有可重放的脚本" in (reason_argv or ""), f"{reason_argv!r}")

server._SERVER_DIR = _saved_dir
server._restart_argv = _saved_argv_fn  # type: ignore[assignment]
_t0 = time.time()
reason_ok = server._restart_preflight()
check("真实代码预检通过（不误杀）", reason_ok is None, f"{reason_ok!r}")
print(f"        （真实预检耗时 {time.time() - _t0:.2f}s）")

# 防回归：MCP server 的 stdin 是客户端管道，该形态下子进程若继承它会挂死
# （实测：继承管道 stdin 20s 超时、连第一条 print 都读不到；DEVNULL 下 0.46s 正常）
# ⇒ 预检**必须**显式 stdin=DEVNULL。
#
# ⚠️ 这个故障**只在真正的 MCP server 进程里复现**。以下三种简化形态实测都复现不了
# （均 1.0~1.3s 正常返回），所以别拿它们替代下面的真实 stdio 会话：
#   ① 同步父进程 + Popen(stdin=PIPE)，孙进程继承 stdin
#   ② 父进程先把 stdin 挂到 asyncio Proactor（connect_read_pipe）再跑预检
#   ③ 用 asyncio.create_subprocess_exec(stdin=PIPE) 起父进程
# 等价条件是「进程里真跑着 FastMCP 的 stdio 服务循环」⇒ 直接起真实 stdio 会话。
section("真实 stdio 会话端到端：真换进程 + 会话不重连 + 预检不挂死")


async def _e2e_restart() -> tuple[bool, float, str]:
    env = dict(os.environ)
    env["AI_MEMORY_DIR"] = _TMP          # 隔离：绝不碰真实记忆库
    env.pop("AI_MEMORY_RESUME", None)    # 以「首次启动」身份起，走完整握手
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(_SERVER_DIR / "server.py")], env=env)
    with open(os.devnull, "w", encoding="utf-8") as _null:
        async with stdio_client(params, errlog=_null) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                t0 = time.time()
                r = await session.call_tool("memory_restart", {"delay": 1.0})
                dt = time.time() - t0
                txt = r.content[0].text
                if "热重启已安排" not in txt:
                    return False, dt, txt
                await asyncio.sleep(4.0)
                r2 = await session.call_tool("memory_stats", {})
                return bool(r2.content[0].text), dt, "ok"


try:
    import asyncio  # noqa: E402

    from mcp import ClientSession, StdioServerParameters  # noqa: E402
    from mcp.client.stdio import stdio_client  # noqa: E402
except Exception as _imp_e:  # noqa: BLE001
    check("真实 stdio 会话 e2e（预检不挂死 + 换进程后会话可用）",
          False, f"导入失败 {type(_imp_e).__name__}: {_imp_e}")
else:
    try:
        _ok, _dt, _txt = asyncio.run(_e2e_restart())
    except Exception as _run_e:  # noqa: BLE001
        _ok, _dt, _txt = False, -1.0, f"抛异常 {type(_run_e).__name__}: {_run_e}"
    check("真实 stdio 会话 e2e（预检不挂死 + 换进程后会话可用）",
          _ok and _dt < 15.0,
          f"耗时 {_dt:.1f}s（须 <15s；若漏了 DEVNULL 会约 90s 并返回「预检子进程无法启动」）"
          f"；详情：{'ok' if _txt == 'ok' else _txt[:200]}")

# =====================================================================
section("三道拒绝闸门")
_saved_enabled = server._RESTART_ENABLED
server._RESTART_ENABLED = False
out_disabled = server.memory_restart()
check("AI_MEMORY_RESTART=0 时整体禁用", "禁用" in out_disabled, out_disabled[:100])
server._RESTART_ENABLED = _saved_enabled

server._RESTARTING.set()
try:
    out_dup = server.memory_restart()
    check("已有重启排队时拒绝重复调用", "排队" in out_dup, out_dup[:100])
finally:
    server._RESTARTING.clear()

_saved_chain = server._RESTART_CHAIN
server._RESTART_CHAIN = server._RESTART_CHAIN_MAX
out_chain = server.memory_restart()
check("接力链超限时拒绝（防重启风暴）", "已达上限" in out_chain, out_chain[:100])
server._RESTART_CHAIN = _saved_chain

# =====================================================================
section("换进程路径（execv 替身，绝不真换）")
_calls: list[tuple[str, list[str]]] = []
_saved_execv = server.os.execv


def _fake_execv(path: str, argv: list[str]) -> None:
    """替身：只记录参数，不换进程。"""
    _calls.append((path, list(argv)))


server.os.execv = _fake_execv  # type: ignore[assignment]
try:
    out_sched = server.memory_restart(delay=0.1)
    check("返回「已安排」并含命令与接力链", "热重启已安排" in out_sched and "接力链" in out_sched,
          out_sched[:120])
    for _ in range(80):
        if _calls:
            break
        time.sleep(0.1)
    check("延迟线程确实调用了 execv", len(_calls) == 1, f"实际调用 {len(_calls)} 次")
    if _calls:
        path, argv = _calls[0]
        check("execv 第一个参数是解释器", path == sys.executable, path)
        check("execv argv 精确重放当前命令行",
              argv == [sys.executable] + list(sys.argv), f"{argv}")
    check("换进程前已写入接力标记 AI_MEMORY_RESUME=1",
          os.environ.get("AI_MEMORY_RESUME") == "1",
          f"实际 {os.environ.get('AI_MEMORY_RESUME')!r}")
    check("换进程前已写入接力链计数",
          os.environ.get("AI_MEMORY_RESTART_CHAIN") == str(_saved_chain + 1),
          f"实际 {os.environ.get('AI_MEMORY_RESTART_CHAIN')!r}")
finally:
    server.os.execv = _saved_execv  # type: ignore[assignment]
    server._RESTARTING.clear()

# =====================================================================
section("辅助纯函数")
check("_restart_argv 重放命令行",
      server._restart_argv() == [sys.executable] + list(sys.argv))

os.environ["AI_MEMORY_RESTART_CHAIN"] = "3"
check("_read_restart_chain 读环境变量", server._read_restart_chain() == 3)
os.environ["AI_MEMORY_RESTART_CHAIN"] = "garbage"
check("非法计数回退 0", server._read_restart_chain() == 0)
os.environ["AI_MEMORY_RESTART_CHAIN"] = "-5"
check("负数计数被夹到 0", server._read_restart_chain() == 0)
os.environ.pop("AI_MEMORY_RESTART_CHAIN", None)

os.environ["XI_T"] = "2.5"
check("_env_float 正常解析", server._env_float("XI_T", 1.0) == 2.5)
os.environ["XI_T"] = ""
check("_env_float 空值取默认", server._env_float("XI_T", 1.0) == 1.0)
os.environ["XI_T"] = "abc"
check("_env_float 非法取默认", server._env_float("XI_T", 1.0) == 1.0)
os.environ.pop("XI_T", None)

detail = server._restart_prepare()
check("_restart_prepare 不抛异常且返回说明", isinstance(detail, str) and bool(detail), repr(detail))

# =====================================================================
# cleanup
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
