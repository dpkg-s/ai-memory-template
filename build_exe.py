#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 ai-memory 安装器的两个 exe（GUI 版 / CLI 版）。

为什么需要这个脚本
------------------
conda 环境的 ``_tkinter.pyd`` 依赖 ``Library/bin`` 下的 ``tcl86t.dll`` /
``tk86t.dll``，而 PyInstaller 的 hook 只会收集 ``_tcl_data`` / ``_tk_data``
脚本库与 ``_tkinter.pyd`` 本体，**漏掉这两个核心 DLL**。后果是打包出的 exe
在干净机器上启动即崩：

    ImportError: DLL load failed while importing _tkinter: 找不到指定的模块。

⚠️ 这个缺陷在开发机上会被**掩盖**：如果同名 DLL 恰好能从 PATH 或环境目录
被搜到，exe 在本机跑得好好的，换台机器就崩。所以验证必须在**剥离 conda 的
干净 PATH** 下进行（见 verify_dlls / run_checks）。

本脚本的做法是先解析 ``_tkinter.pyd`` 的依赖树，把本环境内解析到的非系统
DLL 全部以 ``--add-binary`` 注入，因此将来 tcl 大版本升级（tcl86t → tcl90t）
也无需改代码。

用法：
    python build_exe.py            # 两个都打
    python build_exe.py gui        # 只打 GUI 版
    python build_exe.py cli        # 只打 CLI 版
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"

TARGETS = {
    "gui": {
        "entry": "ai_memory_gui.py",
        "name": "ai-memory-setup",
        "console": False,
        "work": BUILD / "pyi-gui",
        "needs_tk": True,
    },
    "cli": {
        "entry": "ai_memory_setup.py",
        "name": "ai-memory-setup-cli",
        "console": True,
        "work": BUILD / "pyi-cli",
        "needs_tk": False,
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


# Windows 控制台的默认代码页在**英文版系统上是 cp1252**，编不了本脚本里的中文日志
# （GitHub Actions 的 windows-latest 正是这种情况）。不处理的话第一条 log 就抛
# UnicodeEncodeError 把构建打断 —— 实测 Release 流水线报
# 「'charmap' codec can't encode characters in position 4-11」。
# 统一改 UTF-8，并对个别编不出的字符降级为替代符（宁可日志有瑕疵，不可中断构建）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 老解释器/被重定向时无此方法，忽略即可
        pass


# --------------------------------------------------------------------------
# 依赖树解析：找出 _tkinter.pyd 需要的、本环境内的 DLL
# --------------------------------------------------------------------------

def collect_tk_dlls() -> list[Path]:
    """挑出必须随包分发的 tcl/tk DLL。

    ⚠️ 不能只靠导入表：conda 的 ``_tkinter.pyd`` 走的是 Tcl 的 **stub 机制**
    （链接 ``tclstub86.lib``），真正的 ``tcl86t.dll`` / ``tk86t.dll`` 由运行时
    LoadLibrary 动态加载 —— 导入表里根本看不到它们，这正是 PyInstaller 会漏收
    这两个文件的原因。所以这里改为两步：
      1. 按文件名模式（tcl*/tk*/zlib*）在候选目录里找出种子 DLL；
      2. 对每个种子递归解析依赖（tcl86t 自身还依赖 zlib 等）。
    """
    try:
        import _tkinter  # noqa: F401
    except ImportError as e:
        raise SystemExit(f"当前解释器没有可用的 tkinter，无法打包 GUI 版：{e}")

    from PyInstaller.depend import bindepend

    pyd = Path(_tkinter.__file__).resolve()
    prefix = Path(sys.base_prefix).resolve()
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"

    def is_system_dll(p: Path) -> bool:
        """系统里已同名的（api-ms-win-*、ucrtbase、VC runtime 等）一律不带。

        conda 根目录下也躺着一批 UCRT API-set stub，它们本该由加载器解析到系统，
        打进包里反而会与系统实现冲突。
        """
        try:
            return (sys32 / p.name).is_file()
        except Exception:
            return False

    def inside_env(p: Path) -> bool:
        """只带走本环境内的 DLL，过滤 kernel32/user32 这类系统库。"""
        try:
            return p.is_file() and str(p).lower().startswith(str(prefix).lower())
        except Exception:
            return False

    cand_dirs = [
        pyd.parent,
        prefix / "Library" / "bin",
        prefix / "DLLs",
        prefix / "bin",
        prefix / "Library" / "lib",
    ]
    patterns = ("tcl*.dll", "tk*.dll", "zlib*.dll")

    seeds: list[Path] = []
    for d in cand_dirs:
        if not d.is_dir():
            continue
        for pat in patterns:
            seeds.extend(p for p in sorted(d.glob(pat)) if p.is_file())
    if seeds:
        log(f"    候选目录命中种子 DLL {len(seeds)} 个：" +
            ", ".join(sorted(s.name for s in seeds)))

    seen: set[str] = set()
    found: dict[str, Path] = {}
    stack: list[Path] = list(seeds) + [pyd]

    while stack:
        cur = stack.pop()
        key = cur.name.lower()
        if key in seen:
            continue
        seen.add(key)
        if cur.suffix.lower() == ".dll" and inside_env(cur) and not is_system_dll(cur):
            found[key] = cur
        try:
            imports = bindepend.get_imports(str(cur))
        except Exception:
            continue
        for _name, path in imports:
            if not path:
                continue
            pp = Path(path).resolve()
            if pp.name.lower() in seen:
                continue
            if pp.suffix.lower() == ".dll" and inside_env(pp) and not is_system_dll(pp):
                stack.append(pp)

    return sorted(found.values(), key=lambda x: x.name.lower())


# --------------------------------------------------------------------------
# 打包
# --------------------------------------------------------------------------

def build(key: str) -> Path:
    cfg = TARGETS[key]
    entry = ROOT / cfg["entry"]
    if not entry.is_file():
        raise SystemExit(f"缺少入口文件：{entry}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--console" if cfg["console"] else "--noconsole",
        "--name", cfg["name"],
        "--distpath", str(DIST),
        "--workpath", str(cfg["work"]),
        "--specpath", str(BUILD),
    ]

    injected: list[Path] = []
    if cfg["needs_tk"]:
        injected = collect_tk_dlls()
        log(f"[{key}] 注入 {len(injected)} 个 DLL：")
        for d in injected:
            log(f"        {d}")
            cmd += ["--add-binary", f"{d}{os.pathsep}."]
        if not any(d.name.lower().startswith("tcl") for d in injected):
            log(f"[{key}] !! 警告：未找到 tcl*.dll，打包结果可能在干净机器上启动失败")

    cmd.append(str(entry))
    log(f"[{key}] 开始打包 -> {cfg['name']}.exe")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"[{key}] PyInstaller 失败，退出码 {r.returncode}")

    exe = DIST / f"{cfg['name']}.exe"
    if not exe.is_file():
        raise SystemExit(f"[{key}] 未生成 {exe}")
    return exe


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def list_archive(exe: Path) -> list[str]:
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer", "-l", str(exe)],
        capture_output=True, text=True,
    )
    return r.stdout.splitlines()


def verify_dlls(exe: Path) -> bool:
    """确认产物内部确实含 tcl/tk 的核心 DLL（不只是脚本库数据）。"""
    lines = list_archive(exe)
    names = set()
    for line in lines:
        if ".dll" not in line.lower() and ".pyd" not in line.lower():
            continue
        # 文本形如:  1524271, 33886, 68424, 1, 'b', '_tkinter.pyd'
        tail = line.rsplit("'", 2)
        if len(tail) >= 2:
            names.add(tail[-2].strip().lower())

    need_tcl = any(n.startswith("tcl") and n.endswith(".dll") for n in names)
    need_tk = any(n.startswith("tk") and n.endswith(".dll") for n in names)
    has_pyd = "_tkinter.pyd" in names
    ok = need_tcl and need_tk and has_pyd

    log(f"    产物内 DLL: tcl={need_tcl} tk={need_tk} _tkinter.pyd={has_pyd}")
    missing_data = not any("_tcl_data" in l for l in lines)
    if missing_data:
        log("    注意：未见 _tcl_data（Tcl 脚本库），运行期可能报 init.tcl 找不到")
    return ok


def main() -> int:
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    keys = ["gui", "cli"] if which == "all" else [which]
    for k in keys:
        if k not in TARGETS:
            raise SystemExit(f"未知目标：{k}（可选 gui / cli / all）")

    ok_all = True
    for k in keys:
        exe = build(k)
        size = exe.stat().st_size / 1048576
        log(f"[{k}] 已生成 {exe.name}  ({size:.2f} MB)")
        if TARGETS[k]["needs_tk"]:
            good = verify_dlls(exe)
            log(f"[{k}] DLL 自检：{'PASS' if good else 'FAIL'}")
            ok_all = ok_all and good
        log("")

    log("全部完成" if ok_all else "完成，但存在自检未通过项")
    return 0 if ok_all else 2


if __name__ == "__main__":
    sys.exit(main())
