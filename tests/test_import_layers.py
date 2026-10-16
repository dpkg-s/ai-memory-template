#!/usr/bin/env python3
"""模块分层守卫（A2, 2026-10-08）。

背景 / 为什么手写:
    A2 原计划用 import-linter，但那个工具要求 `pyproject.toml` / `setup.cfg` 里
    声明 contracts —— 本仓刻意**零配置、零构建步骤**（模板仓直接 cp 即用），
    为一条守卫引入打包元数据得不偿失。于是改成「手写 AST 守卫」：规则更少、
    更贴本仓，且**不引入任何依赖**。

本仓的形状（2026-10-13 实测）:
    server.py            —— 唯一聚合器（MCP 工具入口，被 mcp 装饰器注册）
    locks / memory_config / memory_index / metrics / text_utils / yaml_io
                         —— 叶子模块，只被 server 组装，**绝不反向依赖 server**
    serve_http / transport_http
                         —— 装配/入口层（v3 阶段 1）：**不被 server 依赖**，
                            由入口反向 import server（见 I9）
    边: server → 全部叶子；locks → memory_config；text_utils → memory_config；
        serve_http → server, transport_http

本脚本的断言口径（都可证伪）:
    I1  叶子模块**不得** import server（含函数体内延迟 import —— 那只是把环
        推迟到运行时，属于更隐蔽的违规）。
    I2  本地模块之间**无环**（拓扑上必须能排出层）。
    I3  叶子 → 叶子 的边只能在 `ALLOWED_LEAF_EDGES` 白名单里 —— 防止悄悄长出
        耦合成网的「私有小框架」。
    I4  白名单**不得腐烂**：白名单里的每条边都必须真实存在，否则是过期条目。
    I5  聚合器必须 import 全部叶子 —— 否则某个叶子成了无人引用的死代码
        （它不会再被任何测试的 import 链路带上）。
    I6  本地 import 必须都在**模块顶层**：延迟 import 会掩盖环，也让「启动即
        加载」的保证失效（本仓靠启动期确定性来暴露问题）。
    I7  不得用 `importlib.import_module` / `__import__` **指向本地模块**绕过守卫。
    I8  模块清单**与磁盘一致**：新增 .py 必须登记进 LEAF_MODULES / AGGREGATOR /
        ASSEMBLY_MODULES / TOOL_MODULES，否则它会从守卫视野里消失（漏检比误报危险）。
    I9  装配/入口层只能在「入口方向」被引导：叶子/聚合器**不得** import 装配层
        （否则 server 会经由 HTTP 入口反向依赖，破坏「主程序零改动」与
        「stdio 永远可用」两条前提）；装配层对本地模块的依赖**只许**指向 server，
        直连叶子必须在 `ALLOWED_ENTRY_LEAF_EDGES` 白名单里（I9d 保证白名单不腐烂）。

运行:
    python tests/test_import_layers.py       # 退出码 0=全绿, 1=有失败
依赖:
    纯标准库（AST 静态分析为主）；末尾 I5b 需要能 import server，故仍建议在
    装有 mcp 的 venv 里跑。脚本把 AI_MEMORY_DIR 指向临时目录，绝不触碰真实记忆库。
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AGGREGATOR = "server"
LEAF_MODULES = {"locks", "memory_config", "memory_index", "metrics",
                "text_utils", "yaml_io"}
# 装配/入口层（v3 阶段 1，2026-10-13）：**不被 server import**，而是反过来 import
# server（serve_http = HTTP 入口）或只 import mcp（transport_http = 纯装配）。
# 单列一类，是因为 I5a「server 必须 import 全部叶子」对它们不成立 —— 它们刻意
# 不进入主程序的导入图，这样 server.py 才能保持零改动、`python server.py` 永远
# 等于 stdio。改动它们不会波及任何 MCP 工具实现。
#
# 2026-10-16：Web 控制台（serve_web / web_console）已拆分为独立仓 ai-memory-web，
# 不在本仓的任何导入图里，故从本集合移除。
ASSEMBLY_MODULES = {"serve_http", "transport_http"}

# 自足工具/脚本（2026-10-16）：既不被 server import，也不 import 任何本地模块 ——
# 因而既不是叶子、也不是聚合器、更不是装配层。单列一类只为满足 I8「磁盘上的 .py
# 必须登记」：不登记它就会从守卫视野里消失（漏检比误报危险）。
#
# 2026-10-16：ai_memory_setup.py（面向用户的安装/更新器，仅用标准库、可 PyInstaller
# 打包）随 Web 控制台拆仓一并提交进本仓，但清单未同步 ⇒ CI 四个 Python 版本同报
# I0a「多=['ai_memory_setup']」。补登记即修。此类不参与 I2 无环、I5a 组装完整性、
# I9 依赖方向三组检查（它没有任何本地边）。
TOOL_MODULES = {"ai_memory_setup"}

# 叶子之间被允许的（且仅有的）依赖边。加边必须在这里显式登记并写明理由 ——
# 这条白名单是「架构没有被悄悄改写」的唯一证据。
ALLOWED_LEAF_EDGES = {
    ("locks", "memory_config"): "锁的陈旧超时可配（B5）",
    ("text_utils", "memory_config"): "热度衰减系数可配（B5）",
}

# 装配层被允许直连**叶子**的边（默认禁止，见 I9b）。同样必须显式登记并写明理由，
# 且登记了却未用到会被 I9d 判为陈旧 —— 白名单不允许随重构腐烂。
ALLOWED_ENTRY_LEAF_EDGES = {
    # 2026-10-16：原 ("web_console", "memory_config") 条目随 Web 控制台迁往独立仓
    # ai-memory-web 而移除 —— 该依赖边依然存在（控制台确实复用 memory_config 的
    # 默认值与 config_path），只是它已不属于本仓，本仓的白名单不再登记。
}

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


# =====================================================================
# 解析
# =====================================================================
def module_files() -> list[Path]:
    return sorted(ROOT.glob("*.py"))


def _import_targets(node: ast.Import | ast.ImportFrom) -> list[str]:
    """取出 import 语句里的顶层模块名（只关心本地模块，调用方自行过滤）。"""
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    return [(node.module or "").split(".")[0]] if node.module else []


def parse_all():
    """返回 {模块名: {'tree', 'src', 'top_edges', 'all_edges', 'inner', 'dyn'}}。

    top_edges: 模块级 import 产生的本地边（src → dst）
    inner:     函数体/类体里出现的本地 import（延迟 import，违规线索）
    dyn:       动态 import 调用里出现本地模块名的行号
    """
    info: dict[str, dict] = {}
    for f in module_files():
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(f))
        # ⚠️ 必须把 ASSEMBLY_MODULES 也纳入：否则装配层的 import 边提取不到，
        # 「叶子不得 import 装配层」会退化成**恒绿**的假判据（实测踩到）。
        local = LEAF_MODULES | {AGGREGATOR} | ASSEMBLY_MODULES

        top_edges: set[str] = set()
        # `import memory_index as midx` 会把 **midx** 绑进 server 的命名空间，
        # 而不是 `memory_index` ⇒ 运行期核属性必须按绑定名来（见 I5c）。
        top_binds: dict[str, list[str]] = {}
        for n in tree.body:                       # 只看模块级直接子节点
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for t in _import_targets(n):
                    if t in local:
                        top_edges.add(t)
                if isinstance(n, ast.Import):
                    for a in n.names:
                        _top = a.name.split(".")[0]
                        if _top in local:
                            top_binds.setdefault(_top, []).append(
                                a.asname or _top)

        inner: list[tuple[int, str]] = []
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for n in ast.walk(fn):
                    if isinstance(n, (ast.Import, ast.ImportFrom)):
                        for t in _import_targets(n):
                            if t in local:
                                inner.append((n.lineno, t))

        dyn: list[tuple[int, str]] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                callee = n.func
                nm = callee.id if isinstance(callee, ast.Name) else (
                    callee.attr if isinstance(callee, ast.Attribute) else None)
                if nm in ("__import__", "import_module"):
                    for a in ast.walk(n):
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            for t in local:
                                if a.value == t or a.value.startswith(t + "."):
                                    dyn.append((n.lineno, a.value))

        info[f.stem] = {"path": f, "src": src, "tree": tree,
                        "top_edges": top_edges, "top_binds": top_binds,
                        "inner": inner, "dyn": dyn}
    return info


INFO = parse_all()
EDGES = {(m, d) for m, v in INFO.items() for d in v["top_edges"]}

# =====================================================================
print("\n[I0] 口径自检")
_磁盘 = {p.stem for p in module_files()}
_预期 = LEAF_MODULES | {AGGREGATOR} | ASSEMBLY_MODULES | TOOL_MODULES
check("I0a 模块清单与磁盘一致（新增 .py 必须登记）", _磁盘 == _预期,
      f"多={sorted(_磁盘 - _预期)} 少={sorted(_预期 - _磁盘)}")
check("I0b 图非空（提取器本身没坏）", len(EDGES) >= 8,
      f"实际边={sorted(EDGES)}")
print(f"        （实测边：{sorted(EDGES)}）")

# =====================================================================
print("\n[I1] 叶子模块不得反向依赖聚合器")
for _m in sorted(LEAF_MODULES):
    check(f"I1a {_m} 不 import server",
          AGGREGATOR not in INFO[_m]["top_edges"],
          f"顶层边={sorted(INFO[_m]['top_edges'])}")
check("I1b 叶子模块里没有延迟/动态 import server 的手法",
      all(AGGREGATOR not in [t for _ln, t in INFO[_m]["inner"]]
          and not [x for x in INFO[_m]["dyn"] if AGGREGATOR in x[1]]
          for _m in LEAF_MODULES),
      str({m: (INFO[m]["inner"], INFO[m]["dyn"]) for m in sorted(LEAF_MODULES)
           if INFO[m]["inner"] or INFO[m]["dyn"]}))

# =====================================================================
print("\n[I2] 本地模块之间必须无环")
_LOCAL = LEAF_MODULES | {AGGREGATOR} | ASSEMBLY_MODULES
_WHITE, _GREY, _BLACK = 0, 1, 2
_state = dict.fromkeys(_LOCAL, _WHITE)
_cycle: list[str] = []


def _dfs(node: str, stack: list[str]) -> None:
    if _cycle:
        return
    _state[node] = _GREY
    stack.append(node)
    for dst in sorted(d for s, d in EDGES if s == node):
        if _state.get(dst) == _GREY:
            _cycle.append(" → ".join(stack[stack.index(dst):] + [dst]))
            return
        if _state.get(dst) == _WHITE:
            _dfs(dst, stack)
    stack.pop()
    _state[node] = _BLACK


for _m in sorted(_LOCAL):
    if _state[_m] == _WHITE:
        _dfs(_m, [])
check("I2a 无循环依赖", not _cycle, str(_cycle))

# =====================================================================
print("\n[I3] 叶子之间的依赖只能在白名单里")
_leaf_edges = {e for e in EDGES if e[0] in LEAF_MODULES and e[1] in LEAF_MODULES}
check("I3a 叶子→叶子的边全部已登记", _leaf_edges <= set(ALLOWED_LEAF_EDGES),
      f"未登记={sorted(_leaf_edges - set(ALLOWED_LEAF_EDGES))}")

print("\n[I4] 白名单不得腐烂（每条登记边都真实存在）")
for _e in sorted(ALLOWED_LEAF_EDGES):
    check(f"I4a {_e[0]} → {_e[1]} 真实存在", _e in EDGES,
          f"（{ALLOWED_LEAF_EDGES[_e]}）已过期，请删除该条目")

# =====================================================================
print("\n[I5] 聚合器必须 import 全部叶子")
for _m in sorted(LEAF_MODULES):
    check(f"I5a server import {_m}", _m in INFO[AGGREGATOR]["top_edges"])

# 静态之外再验一次「真的挂上了」：延迟/条件 import、拼错名字都会在这里露馅。
_TMP = tempfile.mkdtemp(prefix="ai-memory-layers-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_METRICS"] = "0"
sys.path.insert(0, str(ROOT))
try:
    _before = set(sys.modules)
    import server as _server  # noqa: E402
    _loaded = set(sys.modules) - _before
    # I5c：`import X as Y` 要按 Y 核（memory_index 是 as midx 进来的）。
    _badattr = {m: [b for b in INFO[AGGREGATOR]["top_binds"].get(m, []) if not hasattr(_server, b)]
                for m in sorted(LEAF_MODULES)}
    _badattr = {m: v for m, v in _badattr.items() if v}
    check("I5b 导入 server 时真的加载了全部叶子模块（sys.modules 佐证）",
          all(m in _loaded for m in LEAF_MODULES),
          str(sorted(m for m in LEAF_MODULES if m not in _loaded)))
    check("I5c 叶子模块都按声明时的绑定名挂在 server 上",
          not _badattr, str(_badattr))
    del sys.modules["server"]
except Exception as e:                                        # noqa: BLE001
    check("I5b 导入 server 时真的加载了全部叶子模块（sys.modules 佐证）",
          False, f"{type(e).__name__}: {e}")
    check("I5c 叶子模块都按声明时的绑定名挂在 server 上",
          False, f"{type(e).__name__}: {e}")
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

# =====================================================================
print("\n[I6] 本地 import 必须都在模块顶层")
for _m in sorted(_LOCAL):
    check(f"I6a {_m} 无函数体内本地 import", not INFO[_m]["inner"],
          str(INFO[_m]["inner"]))

# =====================================================================
print("\n[I7] 不得用动态 import 绕过守卫")
for _m in sorted(_LOCAL):
    check(f"I7a {_m} 无指向本地模块的动态 import", not INFO[_m]["dyn"],
          str(INFO[_m]["dyn"]))

# =====================================================================
print("\n[I9] 装配/入口层的依赖方向")
# ① 叶子/聚合器 → 装配层 的边必须为空。否则 server 会经由 HTTP 入口反向依赖，
#    「主程序零改动」与「stdio 永远可用」两条前提同时失效（且很隐蔽）。
_entry_inbound = {e for e in EDGES
                  if e[1] in ASSEMBLY_MODULES and e[0] not in ASSEMBLY_MODULES}
check("I9a 叶子/聚合器不得 import 装配层", not _entry_inbound,
      str(sorted(_entry_inbound)))

# ② 装配层对本地模块的依赖不得直连叶子（只许 server 与其它装配层）。直连叶子
#    说明它在绕过 server 自己拼装功能 —— 那部分逻辑应下沉到叶子或进入 server。
_entry_out = {e for e in EDGES if e[0] in ASSEMBLY_MODULES and e[1] in _LOCAL}
_entry_leaf = {e for e in _entry_out if e[1] in LEAF_MODULES}
_bad_entry_out = _entry_leaf - set(ALLOWED_ENTRY_LEAF_EDGES)
check("I9b 装配层不得直连叶子模块（白名单外；只许 server / 其它装配层）",
      not _bad_entry_out, str(sorted(_bad_entry_out)))
# 白名单自检：登记了却没出现 ⇒ 要么模块名写错，要么那条依赖已消失。两者都该报警，
# 否则白名单会随重构腐烂，悄悄把 I9b 放宽成恒绿。
_stale = set(ALLOWED_ENTRY_LEAF_EDGES) - _entry_leaf
check("I9d 装配→叶子白名单无陈旧项（登记的边确实存在）", not _stale,
      f"陈旧={sorted(_stale)} 实际装配→叶子边={sorted(_entry_leaf)}")

# ③ 提取器自检：I9a 依赖「装配层出现在 EDGES 里」。若提取器漏了它，I9a 会**恒绿**
#    —— 假判据比误报危险，故显式证明提取器确实看得见装配层。
check("I9c 提取器确实提取到装配层的边（否则 I9a 恒绿）",
      any(e[0] in ASSEMBLY_MODULES for e in EDGES),
      f"EDGES={sorted(EDGES)}")
print(f"        （装配层出边：{sorted(_entry_out)}）")

# =====================================================================
print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
