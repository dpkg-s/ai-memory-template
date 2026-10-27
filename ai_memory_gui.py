#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-memory 安装器 · 图形界面（骨架：方案 B「单页全览」，Tkinter）

设计要点
--------
1. **单页全览**：路径区 + 源与选项 + 「预览／日志」共用区 + 底部按钮，一屏放完。
   预演时那块区域显示变更清单，执行时原地变成流式日志 —— 先看再动，天然成序。
2. **界面与逻辑解耦**：本文件只负责交互；真正的安装/更新逻辑仍在 ai_memory_setup.py。
   界面通过替换 sys.stdout 把逻辑层的输出接进日志区，逻辑层零改动。
3. **不卡界面**：探测与执行都跑在后台线程，主线程只用 after() 轮询队列刷日志。
4. **CLI 仍可用**：带参数启动时原样转交命令行入口；打包成 --noconsole 时自动
   挂一个控制台，保证脚本调用同样有输出。
5. **安全底线沿用逻辑层**：绝不覆写记忆库的 md / db，覆盖前必备份。

打包：
    pyinstaller --noconsole --onefile --name ai-memory-setup ai_memory_gui.py
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

import ai_memory_setup as core

APP_TITLE = f"ai-memory 安装 / 更新器  v{core.SETUP_VERSION}"
WIN_W, WIN_H = 800, 860

C_OK = "#1a7f37"
C_WARN = "#b26a00"
C_FAIL = "#b42318"
C_DIM = "#6b7280"
C_ACTIVE = "#0b5cad"

# 版本下拉的三个占位态。它们必须与真实 tag 长得不一样，
# 否则用户在远端不可达时会把占位文案当成一个可选版本选走。
VER_LOADING = "（正在获取版本列表…）"
VER_NO_NET = "最新（远端不可达，无法选版本）"

# 底栏那个「跳过安装、直接接入」按钮的文案。检测中会临时换成别的字，
# 所以放在模块级：两处（常态 / 复位）必须用同一份，不能各写一遍。
# 文案刻意与第 ⑤ 步同名 —— 它做的正是"跳到那一步"，不加"跳过安装"之类的解释性
# 前缀（那是按钮状态的职责，见 _refresh_skip_btn），否则一行字塞两件事。
SKIP_LABEL = "接入 AI 工具"
SKIP_BUSY_LABEL = "检测中…"


# --------------------------------------------------------------------------
# 关于「带参数启动」
# --------------------------------------------------------------------------
# 打包为 --noconsole 后进程没有 stdout，靠 AllocConsole 临时挂控制台并不可靠
# （实测在受限环境下会失败并挂住）。因此这里不做控制台回退，而是把命令行参数
# 交给窗口自己在日志区执行 —— 既不静默失败，用户也能看到完整输出。
# 真正需要 stdout 的脚本场景请用控制台版 ai-memory-setup-cli.exe。


# --------------------------------------------------------------------------
# 输出桥：逻辑层的 print → 队列 → 日志区
# --------------------------------------------------------------------------

class _QueueStream:
    """替代 sys.stdout。逻辑层的每个 print 都会变成一条队列消息。"""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, text: str) -> int:
        if text:
            self.q.put(("log", text))
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def reconfigure(self, **kwargs) -> None:
        pass

    def fileno(self):
        raise OSError("no fileno")


# --------------------------------------------------------------------------
# 「已经装过」的识别 —— 给第 ① 步的「已装过？直接接入 AI」用
# --------------------------------------------------------------------------
# 目的：本机明明已经装好了程序，用户却被逼着重走一遍「选库 → 定本体 → 附加组件
# → 确认」。这四步的产物在磁盘上早就存在，正确的动作只有第 ⑤ 步：把已有的
# 程序写进 AI 工具的 MCP 配置。
#
# 判据用逻辑层现成的 ``core.verify_install``（纯离线、不打印、不写盘），
# 不另造一套"看起来装了"的启发式 —— 两套判据必然会在某天给出互相矛盾的答案。

# 可跳过性排序：完好 > 记录与文件不一致 > 有程序无安装记录
_SKIP_RANK = {core.VERIFY_OK: 3, core.VERIFY_DRIFT: 2, core.VERIFY_PARTIAL: 1}


def _path_key(p) -> str:
    """路径去重键。Windows 上大小写不敏感，全小写归一后再比。"""
    try:
        return str(Path(p)).replace("\\", "/").rstrip("/").lower()
    except (TypeError, ValueError):
        return ""


def installed_candidates(seed: Path | None = None) -> list[Path]:
    """收集「可能装着程序本体」的目录（含 server.py 或安装记录的那个）。

    来源与 ``setup_vault`` 的候选一致（环境变量 / 安装元数据 / 客户端配置反推 /
    上次使用 / 默认路径 / 盘符浅扫），再各加两种常见落法：``<库>/ai-memory``
    与库根本身 —— 两者都出现过（有人把程序直接装在库根）。
    """
    seen: dict[str, None] = {}
    order: list[Path] = []

    def add(p) -> None:
        if not p:
            return
        try:
            pp = Path(p)
        except (TypeError, ValueError):
            return
        k = _path_key(pp)
        if k and k not in seen:
            seen[k] = None
            order.append(pp)

    add(seed)
    try:
        import setup_vault as vault

        cur = vault.get_current()
        if cur:
            add(cur)
            add(Path(cur) / "ai-memory")
        for v in vault.scan(seed):
            add(v.path / "ai-memory")
            add(v.path)
    except Exception:
        # 候选收集失败不该让按钮整体失效：至少 seed 与默认路径还在
        pass
    add(core.default_install_dir())
    return order


def _data_dir_for(vf: dict, install: Path) -> Path:
    """反推库目录：安装记录 > 往上找带「记忆索引.md」的那一层 > 本体目录。

    为什么不能拿「目录里有 md 文件」当判据：程序目录本身就带 README.md /
    CHANGELOG.md / rules/*.md，实测把 ``D:\\ai记忆\\ai-memory`` 自己认成了库
    （真库是它的父目录）。``记忆索引.md`` 是模板播种的固定产物，才是硬信号。
    """
    dd = vf.get("data_dir")
    if dd:
        try:
            if Path(dd).is_dir():
                return Path(dd)
        except (TypeError, ValueError):
            pass

    try:
        import setup_vault as vault

        mark = vault.INDEX_MARK
        up: Path | None = install
        for _ in range(3):          # 本体自己 / 父目录 / 祖父目录，再往上就是瞎猜
            if up is None:
                break
            try:
                if (up / mark).is_file():
                    return up
            except OSError:
                pass
            up = up.parent if up.parent != up else None
    except Exception:
        pass
    return install


def find_installed(seed: Path | None = None) -> dict:
    """找本机「已装好、可以直接接入 AI」的实例。纯离线、不弹窗、不写盘。

    只认**程序文件真的在**的实例：``server.py`` 都不在了还把它写进客户端配置，
    只会让 MCP 启动失败，而错误要拖到用户下次开客户端才暴露 —— 那种情况
    必须回第 ④ 步重装，不能跳过。这类"有记录没程序"的目录单独报给界面。

    返回 ``{"found": {install, data, vf} | {}, "broken": [路径…], "scanned": n}``。
    """
    cands = installed_candidates(seed)
    best: dict = {}
    best_key: tuple = ()
    broken: list[str] = []
    for c in cands:
        try:
            vf = core.verify_install(c)
        except Exception:
            continue
        state = vf.get("state", core.VERIFY_NONE)
        if state == core.VERIFY_NONE:
            continue
        if not vf.get("has_program"):
            broken.append(str(c))
            continue
        key = (
            _SKIP_RANK.get(state, 0),
            1 if vf.get("integrity_checked", True) else 0,
            core.parse_version(vf.get("version") or ""),
        )
        if not best or key > best_key:
            best_key = key
            best = {"install": str(c), "data": str(_data_dir_for(vf, c)), "vf": vf}
    return {"found": best, "broken": broken, "scanned": len(cands)}



# --------------------------------------------------------------------------
# 主界面
# --------------------------------------------------------------------------

class SetupApp:
    def __init__(self, root, auto_argv: list[str] | None = None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.env: dict = {}
        self.auto_argv = list(auto_argv) if auto_argv else None
        self._closing = False
        self.detect_busy = False
        self.busy = False

        root.title(APP_TITLE)
        root.minsize(760, 720)
        self._center()
        self._setup_fonts()
        self._build()
        # 内容建完才知道真实高度需求 —— 硬编码高度在非 100% 缩放下必然裁内容
        self._fit_height()
        self._install_stdout_bridge()
        root.after(80, self._drain)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_probe()
        if self.auto_argv:
            root.after(200, lambda: self._run_cli(self.auto_argv))

    # ---------------------------------------------------------------- 基础

    def _center(self) -> None:
        r = self.root
        r.update_idletasks()
        x = max(0, (r.winfo_screenwidth() - WIN_W) // 2)
        y = max(0, (r.winfo_screenheight() - WIN_H) // 3)
        r.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    def _fit_height(self) -> None:
        """按内容实际需要的高度重设窗口高度。

        两个坑叠在一起，缺一个都量不准：
        1. **缩放**：Windows 125% 时同一段内容的 reqheight 从 485 涨到 545，
           写死 860 会让第 ④ 步最后一块「高级选项」被日志区顶出可视区。
        2. **grid_remove 的页面不参与布局**：三页叠在同一格，启动停在第 1 步时
           量到的只是第 1 步的高度。必须逐页显示量一遍取最大值。
        上限取屏幕可用高度的 92%，避免小屏上高过任务栏；下限交给 minsize。
        """
        r = self.root
        frames = list(self.step_frames.items())
        best = 0
        for i, _fr in frames:
            for j, other in frames:
                if j == i:
                    other.grid()
                else:
                    other.grid_remove()
            r.update_idletasks()
            best = max(best, r.winfo_reqheight())
        # 量完把显示状态还原成当前步（这里不走 _goto，避免触发扫描/刷新等副作用）
        for j, other in frames:
            if j == self.step:
                other.grid()
            else:
                other.grid_remove()
        r.update_idletasks()

        avail = int(r.winfo_screenheight() * 0.92)
        h = max(min(best, avail), 720)
        x = max(0, (r.winfo_screenwidth() - WIN_W) // 2)
        r.geometry(f"{WIN_W}x{h}+{x}+{max(0, (r.winfo_screenheight() - h) // 3)}")
        self.fit_height = h
        self.fit_needed = best

    def _setup_fonts(self) -> None:
        import tkinter.font as tkfont

        try:
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
                tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
            tkfont.nametofont("TkFixedFont").configure(family="Consolas", size=9)
        except Exception:
            pass

    def _install_stdout_bridge(self) -> None:
        self._real_stdout = sys.stdout
        sys.stdout = _QueueStream(self.q)

    def _drain(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "env":
                    self._apply_env(payload)
                elif kind == "vaults":
                    self._on_vaults(payload)
                elif kind == "tags":
                    self._on_tags(payload)
                elif kind == "done":
                    self._on_task_done(payload)
                elif kind == "link":
                    self._on_link_scan(payload)
                elif kind == "link_apply":
                    self._on_link_applied(payload)
                elif kind == "installed":
                    self._on_installed(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    # ---------------------------------------------------------------- 构建

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk

        outer = ttk.Frame(self.root, padding=(14, 12, 14, 12))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        # --- 标题 + 环境状态条 ---------------------------------------------
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=APP_TITLE, font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0, column=0, sticky="w")

        self.lbl_state = ttk.Label(head, text="", foreground=C_DIM)
        # 三个环境指示灯横排：**必须以横排容器为父级**才能正确布局。
        # 若把父级留在 head 上再用 grid(in_=...) 挂过去，实测会被排到容器可视区之外
        # （控件有几何但看不见）——这种隐性问题不如直接建对父级。
        envrow = ttk.Frame(head)
        envrow.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.lbl_py = ttk.Label(envrow, text="● Python  检测中…", foreground=C_DIM)
        self.lbl_mcp = ttk.Label(envrow, text="● mcp 依赖  检测中…", foreground=C_DIM)
        self.lbl_net = ttk.Label(envrow, text="● GitHub  检测中…", foreground=C_DIM)
        for i, w in enumerate((self.lbl_py, self.lbl_mcp, self.lbl_net)):
            w.grid(row=0, column=i, sticky="w", padx=(0, 16))
        self.lbl_state.grid(row=2, column=0, sticky="w", pady=(2, 0))

        ttk.Separator(outer, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=(10, 10))

        # --- 步骤条 ---------------------------------------------------------
        self.ribbon = ttk.Frame(outer)
        self.ribbon.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.step_names = ("记忆库目录", "程序本体", "附加组件", "确认安装", "接入 AI 工具")
        self.lbl_steps: list = []
        col = 0
        for i, name in enumerate(self.step_names, 1):
            if i > 1:
                ttk.Label(self.ribbon, text="→", foreground=C_DIM).grid(
                    row=0, column=col, padx=10)
                col += 1
            lb = ttk.Label(self.ribbon, text=f"{'①②③④⑤'[i - 1]} {name}")
            lb.grid(row=0, column=col, sticky="w")
            col += 1
            self.lbl_steps.append(lb)

        # --- 步骤内容 -------------------------------------------------------
        host = ttk.Frame(outer)
        host.grid(row=3, column=0, sticky="nsew")
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        # 四页叠在同一格，只显示当前那页（比 destroy/重建更省心，输入不丢）
        self.step_frames = {
            1: self._build_step1(host),
            2: self._build_step2(host),
            3: self._build_step3(host),
            4: self._build_step4(host),
            5: self._build_step5(host),
        }
        for fr in self.step_frames.values():
            fr.grid(row=0, column=0, sticky="nsew")

        # --- 预览 / 日志共用区 ---------------------------------------------
        logbox = ttk.LabelFrame(outer, text="预览 · 日志", padding=(8, 6, 8, 8))
        logbox.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        logbox.columnconfigure(0, weight=1)
        logbox.rowconfigure(0, weight=1)

        self.txt = tk.Text(logbox, height=7, wrap="none", state="disabled",
                           background="#ffffff", relief="flat",
                           font=("Consolas", 9), padx=6, pady=4)
        self.txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logbox, orient="vertical", command=self.txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.tag_configure("err", foreground=C_FAIL)
        self.txt.tag_configure("warn", foreground=C_WARN)
        self.txt.tag_configure("ok", foreground=C_OK)

        # --- 底部导航 -------------------------------------------------------
        bar = ttk.Frame(outer)
        bar.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        bar.columnconfigure(1, weight=1)
        self.btn_prev = ttk.Button(bar, text="‹ 上一步", width=10, command=self._prev_step)
        self.btn_prev.grid(row=0, column=0, sticky="w")
        self.btn_next = ttk.Button(bar, text="下一步 ›", width=10, command=self._next_step)
        self.btn_next.grid(row=0, column=2)
        self.btn_dry = ttk.Button(bar, text="预演（不改动）", command=lambda: self.run_task(dry=True))
        self.btn_dry.grid(row=0, column=3, padx=(8, 8))
        self.btn_run = ttk.Button(bar, text="开始安装", command=lambda: self.run_task(dry=False))
        self.btn_run.grid(row=0, column=4, padx=(0, 8))
        # 第 ⑤ 步的主操作放在底栏最右下、与「关闭」同排：它是这一页唯一"往前推"的动作，
        # 埋在列表下面的按钮排里会被当成次要选项，而它其实等同于其他页的「下一步」。
        self.btn_link_apply = ttk.Button(bar, text="接入选中的", command=self._apply_links)
        self.btn_link_apply.grid(row=0, column=4, padx=(0, 8))
        self.btn_link_apply.grid_remove()
        self.btn_link_apply.configure(state="disabled")
        # 快捷入口：已经装好的人不必把前四步再走一遍。
        # 常态 disabled —— 没装的时候它无事可做，一个能点却只能弹"没找到"的按钮
        # 比灰着更糟（用户会怀疑是按钮坏了）。启用条件见 _refresh_skip_btn。
        self.btn_skip = ttk.Button(bar, text=SKIP_LABEL, command=self._detect_installed)
        self.btn_skip.grid(row=0, column=5, padx=(0, 8))
        self.btn_skip.configure(state="disabled")
        self.btn_quit = ttk.Button(bar, text="关闭", command=self.on_close)
        self.btn_quit.grid(row=0, column=6)

        self._goto(1)

    # ---------------------------------------------------------------- 步骤页

    def _build_step1(self, parent):
        """第 1 步：选记忆库目录（引导的起点，后面全部由它派生）。"""
        tk, ttk = self.tk, self.ttk
        f = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)

        ttk.Label(f, text="记忆库就是笔记存放的地方。下面列出扫到的候选库，"
                          "都不合适就选个空目录新建。",
                  foreground=C_DIM, wraplength=730).grid(row=0, column=0, sticky="w")

        rowdir = ttk.Frame(f)
        rowdir.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        rowdir.columnconfigure(1, weight=1)
        ttk.Label(rowdir, text="记忆库目录").grid(row=0, column=0, padx=(0, 8))
        self.var_data = tk.StringVar(value=str(self._default_vault()))
        ttk.Entry(rowdir, textvariable=self.var_data).grid(row=0, column=1, sticky="ew")
        ttk.Button(rowdir, text="浏览…", width=8,
                   command=lambda: self._pick_dir(self.var_data, is_vault=True)).grid(
            row=0, column=2, padx=(8, 0))

        ttk.Label(f, text="扫描到的候选（双击某一项填入上面的目录）",
                  foreground=C_DIM).grid(row=2, column=0, sticky="w", pady=(10, 4))

        lbwrap = ttk.Frame(f)
        lbwrap.grid(row=3, column=0, sticky="nsew")
        lbwrap.columnconfigure(0, weight=1)
        lbwrap.rowconfigure(0, weight=1)
        self.lb_vault = tk.Listbox(lbwrap, height=6, activestyle="none", exportselection=False)
        self.lb_vault.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lbwrap, orient="vertical", command=self.lb_vault.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.lb_vault.configure(yscrollcommand=sb.set)
        self.lb_vault.bind("<Double-Button-1>", self._on_vault_pick)
        self.lb_vault.bind("<Return>", self._on_vault_pick)

        brow = ttk.Frame(f)
        brow.grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(brow, text="重新扫描", command=self._start_vault_scan).grid(row=0, column=0)
        ttk.Button(brow, text="在这个目录新建空库", command=self._on_vault_init).grid(
            row=0, column=1, padx=(8, 0))

        self.lbl_vault_msg = ttk.Label(f, text="正在扫描…", foreground=C_DIM, wraplength=730)
        self.lbl_vault_msg.grid(row=5, column=0, sticky="w", pady=(6, 0))
        self._vault_items: list = []
        return f

    def _build_step2(self, parent):
        """第 2 步：程序本体位置 —— 默认由第 1 步派生，仍可改。"""
        tk, ttk = self.tk, self.ttk
        f = ttk.Frame(parent)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="程序文件（server.py 等）会自动放到记忆库目录下的 ai-memory 子目录，"
                          "也就是「库目录 / ai-memory」。需要把程序和记忆库分开存放时可以改。",
                  foreground=C_DIM, wraplength=730).grid(
            row=0, column=0, columnspan=4, sticky="w")

        ttk.Label(f, text="程序本体").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(14, 0))
        self.var_install = tk.StringVar(value=str(self._auto_install()))
        ttk.Entry(f, textvariable=self.var_install).grid(
            row=1, column=1, sticky="ew", pady=(14, 0))
        ttk.Button(f, text="浏览…", width=8,
                   command=lambda: self._pick_dir(self.var_install, is_vault=False)).grid(
            row=1, column=2, padx=(8, 0), pady=(14, 0))
        ttk.Button(f, text="恢复自动", width=10, command=self._restore_auto_install).grid(
            row=1, column=3, padx=(8, 0), pady=(14, 0))

        self.lbl_derive = ttk.Label(f, text="", foreground=C_DIM)
        self.lbl_derive.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        why = ttk.LabelFrame(f, text="为什么这样放", padding=(10, 8))
        why.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        for i, line in enumerate((
            "· 程序与记忆库同在一个父目录下，备份或整体迁移时只需搬一个文件夹。",
            "· 想把程序放到别的盘也没问题：点「浏览…」选别处即可，之后不再被自动覆盖。",
            "· 若本机已装过实例，会按原位置识别并只做更新，不会挪动已有文件。",
        )):
            ttk.Label(why, text=line, foreground=C_DIM).grid(row=i, column=0, sticky="w", pady=1)

        self.var_install_manual = False
        self.var_data.trace_add("write", lambda *_: self._on_data_changed())
        self.var_install.trace_add("write", lambda *_: self._on_install_changed())
        return f

    def _on_install_changed(self) -> None:
        """本体目录一变，三处都得跟着重算。

        只刷派生提示是不够的：顶部状态行与第 ④ 步的核验结论都是按**旧目录**
        算出来的，留在屏幕上就是自相矛盾（截图里抓到过：顶部说有 v2.6.0、
        下面说未检测到已安装实例）。
        """
        self._refresh_derive()
        self._refresh_state_label()
        self._refresh_summary()

    def _build_step3(self, parent):
        """第 3 步：附加组件（HTTP 传输层 + Web 控制台），一个勾选框装两样。

        为什么合成一个开关：Web 控制台是 MCP 的**前端代理**，它的读写全走 HTTP 端点。
        分成两个框就会持续存在「勾了 Web 没勾 HTTP」这个**看起来装好、实际读不到数据**
        的状态 —— 靠警告文案去兜这种半残状态，不如让它根本无法被表达出来。
        """
        tk, ttk = self.tk, self.ttk
        f = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text="附加组件是可选的，不装完全不影响 MCP 侧的任何用法。",
                  foreground=C_DIM, wraplength=730).grid(row=0, column=0, sticky="w")

        box = ttk.LabelFrame(f, text="附加组件（可选）", padding=(10, 8))
        box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)

        self.var_extras = tk.BooleanVar(value=False)
        self.cb_extras = ttk.Checkbutton(
            box, text="安装附加组件（HTTP 传输层 + Web 控制台）",
            variable=self.var_extras, command=self._refresh_summary)
        self.cb_extras.grid(row=0, column=0, columnspan=3, sticky="w")

        # wraplength：Tk 的 Label 不会自动换行，超宽会被父容器直接裁掉（截图抓到过）
        ttk.Label(box, text="两块内容：① Streamable HTTP 入口 —— transport_http.py + "
                            "serve_http.py + start-http.cmd（.sh），默认 127.0.0.1:8700/mcp；",
                  foreground=C_DIM, wraplength=700).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(box, text="② Web 控制台 —— serve_web.py + web_console.py + 整个 web/ 前端目录"
                            "（约 55 个文件），落到 <程序目录>/web-console/，"
                            "配 start-web.cmd（.sh）。",
                  foreground=C_DIM, wraplength=700).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

        row = ttk.Frame(box)
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(row, text="Web 端口").grid(row=0, column=0, padx=(0, 8))
        self.var_webport = tk.StringVar(value="8811")
        ttk.Entry(row, textvariable=self.var_webport, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(row, text="默认 http://127.0.0.1:8811/；两块共用一个 Python 依赖（mcp），"
                            "没有额外 pip 包。", foreground=C_DIM).grid(
            row=0, column=2, padx=(10, 0))

        # 「装完怎么起」写在这一步，而不是等安装日志刷过去才看到 —— 这一屏本来就空，
        # 顺便解决大面积留白（空着的区域不传达任何信息，还不如换成启动说明）。
        use = ttk.LabelFrame(f, text="装好之后怎么用", padding=(10, 8))
        use.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for i, line in enumerate((
            "HTTP：双击 start-http.cmd → http://127.0.0.1:8700/mcp（客户端里填这个地址）",
            "Web ：双击 start-web.cmd → http://127.0.0.1:8811/（它靠 HTTP 端点读写，两个一起开）",
            "换端口：用上面的输入框，或 set AI_MEMORY_HTTP_PORT / AI_MEMORY_WEB_PORT",
            "局域网：HTTP 设 AI_MEMORY_HTTP_ALLOWED_HOSTS；Web 设 AI_MEMORY_WEB_TOKEN，"
            "缺了都会被拒绝启动",
        )):
            ttk.Label(use, text="· " + line, foreground=C_DIM, wraplength=730).grid(
                row=i, column=0, sticky="w", pady=(0, 2))
        return f

    def _build_step4(self, parent):
        """第 4 步：确认 + 其余选项（高级选项常驻展开，不折叠）。"""
        tk, ttk = self.tk, self.ttk
        f = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)

        box = ttk.LabelFrame(f, text="将要执行", padding=(10, 8))
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        for r, (name, attr) in enumerate((("记忆库目录", "lbl_sum_vault"),
                                          ("程序本体", "lbl_sum_prog"),
                                          ("当前状态", "lbl_sum_state"),
                                          ("附加组件", "lbl_sum_extra"))):
            ttk.Label(box, text=name).grid(row=r, column=0, sticky="w", pady=1)
            lb = ttk.Label(box, text="—")
            lb.grid(row=r, column=1, sticky="w", padx=(10, 0), pady=1)
            setattr(self, attr, lb)

        src = ttk.LabelFrame(f, text="安装源", padding=(10, 8))
        src.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        src.columnconfigure(3, weight=1)
        ttk.Label(src, text="来源").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.var_srcmode = tk.StringVar(value="GitHub 自动下载")
        cb = ttk.Combobox(src, textvariable=self.var_srcmode, state="readonly", width=17,
                          values=["GitHub 自动下载", "本地目录"])
        cb.grid(row=0, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_srcmode())

        # 版本与「来源」同一行：第 ④ 步本来就挤，多加一行会把「高级选项」顶出可视区。
        # 下拉里带「最新 / 已装」标记 —— 否则「只选版本看不见已装哪个」是硬伤。
        ttk.Label(src, text="版本").grid(row=0, column=2, sticky="w", padx=(14, 8))
        self.var_ver = tk.StringVar(value=VER_LOADING)
        self.ver_map: dict[str, str | None] = {}
        self.ver_tags: list[str] = []
        self.tags_busy = False
        self.cb_ver = ttk.Combobox(src, textvariable=self.var_ver, state="readonly", width=26,
                                   values=[VER_LOADING])
        self.cb_ver.grid(row=0, column=3, sticky="ew")
        self.btn_ver = ttk.Button(src, text="刷新", width=6, command=self._reload_tags)
        self.btn_ver.grid(row=0, column=4, sticky="w", padx=(8, 0))

        self.frm_local = ttk.Frame(src)
        self.frm_local.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(6, 0))

        self.frm_local.columnconfigure(1, weight=1)
        ttk.Label(self.frm_local, text="本地目录").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.var_local = tk.StringVar(value="")
        ttk.Entry(self.frm_local, textvariable=self.var_local).grid(row=0, column=1, sticky="ew")
        ttk.Button(self.frm_local, text="浏览…", width=8,
                   command=lambda: self._pick_dir(self.var_local)).grid(
            row=0, column=2, sticky="w", padx=(8, 0))
        self.frm_local.grid_remove()

        # 附加组件已整体上移到第 ③ 步（HTTP 与 Web 并列），这一步不再重复出现 ——
        # 同一个开关出现在两页，迟早会出现「这页关了、那页还开着」的困惑。
        mode = ttk.LabelFrame(f, text="执行模式", padding=(10, 8))
        mode.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.var_mode = tk.StringVar(value="auto")
        ttk.Radiobutton(mode, text="自动（未装则安装，已装则更新）", value="auto",
                        variable=self.var_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(mode, text="强制重装", value="force",
                        variable=self.var_mode).grid(row=0, column=1, sticky="w", padx=(16, 0))

        adv = ttk.LabelFrame(f, text="高级选项（常驻展开）", padding=(10, 8))
        adv.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        adv.columnconfigure(1, weight=1)
        self.var_nopip = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv, text="跳过 pip 依赖安装（已就绪时勾选）",
                        variable=self.var_nopip).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(adv, text="镜像前缀（GitHub 不可达时留空，或填 https://ghfast.top/）").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.var_mirror = tk.StringVar(value="")
        ttk.Entry(adv, textvariable=self.var_mirror, width=28).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        return f

    def _build_step5(self, parent):
        """第 5 步：扫描本机 AI 工具，把刚装好的记忆库写进它们的 MCP 配置。

        装完不等于能用 —— 21 个客户端各有一套配置格式，用户不可能自己去翻。
        这一步复用 setup_link 的扫描/写入（与 CLI 的 link scan / link apply 同一份代码），
        所以「界面上接上的」和「命令行接上的」结果必然一致。
        """
        tk, ttk = self.tk, self.ttk
        f = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        ttk.Label(f, text="装好了，但 AI 工具还不知道它 —— 下面扫描本机已装的客户端，"
                          "把 ai-memory 写进各自的 MCP 配置。",
                  foreground=C_DIM, wraplength=730).grid(row=0, column=0, sticky="w")

        box = ttk.LabelFrame(f, text="扫描结果（可多选，未勾的不会动）", padding=(10, 8))
        box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.lb_link = tk.Listbox(box, height=7, activestyle="none",
                                  selectmode="extended", exportselection=False)
        self.lb_link.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.lb_link.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.lb_link.configure(yscrollcommand=sb.set)

        brow = ttk.Frame(f)
        brow.grid(row=2, column=0, sticky="w", pady=(10, 0))
        # 「接入选中的」不在这里 —— 它放在底部导航栏最右下、与「关闭」同排（见 _build）。
        # 这一页只留「怎么选」相关的按钮。
        self.btn_scan = ttk.Button(brow, text="重新扫描", command=self._scan_clients)
        self.btn_scan.grid(row=0, column=0)
        ttk.Button(brow, text="全选可接入", command=self._select_all_link).grid(
            row=0, column=1, padx=(8, 0))
        ttk.Button(brow, text="全部不选", command=lambda: self.lb_link.selection_clear(0, "end")).grid(
            row=0, column=2, padx=(8, 0))

        self.lbl_link = ttk.Label(f, text="", foreground=C_DIM, wraplength=730)
        self.lbl_link.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.link_statuses: list = []
        self.link_rows: list = []
        self.link_scan_busy = False
        return f


    # ------------------------------------------------------------ 接入 AI 工具

    def _link_target(self):
        """构造 setup_link 的 Target —— 与 CLI 的 link 子命令同一套参数。"""
        import setup_link as link

        inst = Path(self.var_install.get().strip() or core.default_install_dir())
        data = Path(self.var_data.get().strip() or inst)
        # 解释器优先用「确认装了 mcp」的那个：写进客户端配置后要能直接跑起来
        py = core.find_python()
        try:
            py2, has = core.find_mcp_python()
            if py2 and has:
                py = py2
        except Exception:
            pass
        return link, link.build_target(inst, data, py)

    def _scan_clients(self) -> None:
        if self.link_scan_busy:
            return
        self.link_scan_busy = True
        self.btn_scan.configure(state="disabled")
        self.btn_link_apply.configure(state="disabled")
        self.lbl_link.configure(text="扫描中…")
        self.lb_link.delete(0, "end")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            link, target = self._link_target()
            statuses = link.scan(target)
            self.q.put(("link", {"statuses": statuses, "error": ""}))
        except Exception as e:
            self.q.put(("link", {"statuses": [], "error": f"{type(e).__name__}: {e}"}))

    def _on_link_scan(self, info: dict) -> None:
        import setup_link as link

        self.link_scan_busy = False
        self.btn_scan.configure(state="normal")
        self.lb_link.delete(0, "end")
        self.link_rows = []
        if info.get("error"):
            self.btn_link_apply.configure(state="disabled")
            self.lbl_link.configure(text=f"扫描失败：{info['error']}")
            self._append(f"[FAIL] 扫描 AI 工具失败：{info['error']}\n")
            return
        statuses = list(info.get("statuses") or [])
        self.link_statuses = statuses
        # 只列「装了」的：没装的那些列出来只是噪音，也没法接入
        self.link_rows = [s for s in statuses if s.state != link.ST_NOT_INSTALLED]
        for s in self.link_rows:
            self.lb_link.insert("end", f"{s.client.name}　{s.label}")
        # 默认只勾「能自动改且确实需要改」的：已连上的不用动，需手工的勾了也写不了
        for i, s in enumerate(self.link_rows):
            if s.actionable:
                self.lb_link.selection_set(i)
        n_act = sum(1 for s in self.link_rows if s.actionable)
        if not self.link_rows:
            self.lbl_link.configure(text="没有检测到任何已安装的 AI 工具。")
        else:
            self.lbl_link.configure(
                text=f"共 {len(self.link_rows)} 个已安装客户端，"
                     f"其中 {n_act} 个可自动接入（已默认勾选）。")
        self.btn_link_apply.configure(state="normal" if n_act else "disabled")
        self._append(f"[..  ] 扫描到 {len(self.link_rows)} 个已安装客户端，"
                     f"{n_act} 个可自动接入\n")

    def _select_all_link(self) -> None:
        for i, s in enumerate(getattr(self, "link_rows", [])):
            if s.actionable:
                self.lb_link.selection_set(i)

    def _apply_links(self) -> None:
        idxs = list(self.lb_link.curselection())
        if not idxs:
            self.lbl_link.configure(text="没有选中任何客户端。")
            return
        rows = [self.link_rows[i] for i in idxs if i < len(self.link_rows)]
        self.btn_link_apply.configure(state="disabled")
        self.btn_scan.configure(state="disabled")
        self.lbl_link.configure(text=f"写入中…（{len(rows)} 个）")

        def job():
            try:
                link, target = self._link_target()
                res = link.apply_all(rows, target)
                self.q.put(("link_apply", {"results": res, "error": ""}))
            except Exception as e:
                self.q.put(("link_apply", {"results": [],
                                           "error": f"{type(e).__name__}: {e}"}))

        threading.Thread(target=job, daemon=True).start()

    def _on_link_applied(self, info: dict) -> None:
        import setup_link as link

        self.btn_scan.configure(state="normal")
        if info.get("error"):
            self.lbl_link.configure(text=f"写入失败：{info['error']}")
            self._append(f"[FAIL] 接入失败：{info['error']}\n")
            return
        results = list(info.get("results") or [])
        self._append("\n=== 接入结果 ===\n")
        for line in link.format_apply_lines(results):
            self._append(line + "\n")
        n_ok = sum(1 for r in results if r.action == "written")
        n_fail = sum(1 for r in results if r.action == "failed")
        n_manual = sum(1 for r in results if r.action == "manual")
        parts = [f"写入 {n_ok} 个"]
        if n_fail:
            parts.append(f"失败 {n_fail} 个")
        if n_manual:
            parts.append(f"需手工 {n_manual} 个")
        self.lbl_link.configure(text="接入完成：" + "，".join(parts) + "。")
        self._refresh_state_label()
        # 写完立刻重扫：界面上必须显示真实状态，而不是「我刚做过什么」的推测
        self._scan_clients()

    # ---------------------------------------------------------------- 推导

    def _default_vault(self) -> Path:
        """默认库目录：优先用已登记的当前库，否则回到默认路径。"""
        try:
            import setup_vault as vault

            cur = vault.get_current()
            if cur:
                return Path(cur)
        except Exception:
            pass
        return core.default_install_dir()

    def _auto_install(self) -> Path:
        """程序本体 = 库目录下的 ai-memory 子目录（第 1 步选定即确定）。"""
        base = self.var_data.get().strip()
        root = Path(base) if base else core.default_install_dir()
        return root / "ai-memory"

    def _on_data_changed(self) -> None:
        # 库目录一变，本体跟着派生 —— 除非用户手动改过本体
        if not self.var_install_manual:
            auto = str(self._auto_install())
            if self.var_install.get() != auto:
                self.var_install.set(auto)      # 会经 trace 走到 _on_install_changed
        self._refresh_derive()
        self._refresh_state_label()
        self._refresh_summary()

    def _refresh_derive(self) -> None:
        if not hasattr(self, "lbl_derive"):
            return
        base = self.var_data.get().strip() or "(未选)"
        cur = self.var_install.get().strip()
        if self.var_install_manual:
            self.lbl_derive.configure(
                text=f"手动指定：{cur}　（已与库目录分开，不再自动跟随）",
                foreground=C_WARN)
        else:
            self.lbl_derive.configure(
                text=f"自动派生：{base} → {cur}", foreground=C_DIM)

    def _restore_auto_install(self) -> None:
        self.var_install_manual = False
        self.var_install.set(str(self._auto_install()))
        self._refresh_derive()

    # ---------------------------------------------------------------- 导航

    def _goto(self, step: int) -> None:
        self.step = step
        for i, fr in self.step_frames.items():
            if i == step:
                fr.grid()
            else:
                fr.grid_remove()
        for i, lb in enumerate(self.lbl_steps, 1):
            if i == step:
                lb.configure(foreground=C_ACTIVE, font=("Microsoft YaHei UI", 10, "bold"))
            elif i < step:
                lb.configure(foreground=C_DIM, font=("Microsoft YaHei UI", 10))
            else:
                lb.configure(foreground=C_DIM, font=("Microsoft YaHei UI", 10))

        if step == 1:
            self._start_vault_scan()
        if step == 2:
            # 首屏就要显示派生提示：var_data 的初值是在挂 trace 之前设的，
            # 不主动刷一次会留白。
            self._refresh_derive()
        if step == 3:
            self._refresh_summary()
        if step == 4:
            self._refresh_summary()
            # 版本列表进确认页才拉：远端请求要几秒，没必要在开局就付这个延迟
            if not self.ver_tags and not self.tags_busy:
                self._reload_tags()
            elif self.ver_tags:
                # 已装版本可能刚变过（换了本体目录）——重算「已装」标记但保住用户的选中
                self._render_tags(self.ver_tags, keep=True)
        if step == 5:
            self._refresh_summary()
            # 进这一页就自动扫一次：安装刚成功时用户最想知道「AI 能不能用了」，
            # 让他再点一次「扫描」纯属多余。
            if not getattr(self, "link_rows", None) and not self.link_scan_busy:
                self._scan_clients()

        self.btn_prev.configure(state="normal" if step > 1 else "disabled")
        last = len(self.step_frames)
        if step == 4:
            # 只有确认页有「预演 / 开始安装」
            self.btn_next.grid_remove()
            self.btn_dry.grid()
            self.btn_run.grid()
            self.btn_run.configure(text="开始更新" if self._installed() else "开始安装")
        elif step == last:
            self.btn_next.grid_remove()
            self.btn_dry.grid_remove()
            self.btn_run.grid_remove()
        else:
            self.btn_next.grid()
            self.btn_dry.grid_remove()
            self.btn_run.grid_remove()
        # 「接入选中的」只在第 ⑤ 步出现，占的是「开始安装」那一格（两者不会同时在场）
        if step == last:
            self.btn_link_apply.grid()
        else:
            self.btn_link_apply.grid_remove()

        # 「接入 AI 工具」这个捷径只在还没到接入页时有意义 —— 到了第 ⑤ 步，
        # 跳过这件事已经发生了，留着按钮等于给用户一个"点了没反应"的控件。
        if step == last:
            self.btn_skip.grid_remove()
        else:
            self.btn_skip.grid()
        self._refresh_skip_btn()

    def _next_step(self) -> None:
        from tkinter import messagebox

        if self.step == 1:
            if not self.var_data.get().strip():
                messagebox.showwarning(APP_TITLE, "请先选择记忆库目录")
                return
            self._goto(2)
        elif self.step == 2:
            if not self.var_install.get().strip():
                messagebox.showwarning(APP_TITLE, "请先指定程序本体位置")
                return
            self._goto(3)
        elif self.step == 3:
            port = self.var_webport.get().strip() or "8811"
            # 提前挡住非法端口：留到安装时报错，用户会以为是安装器坏了
            if self.var_extras.get() and not (port.isdigit() and 1 <= int(port) <= 65535):
                messagebox.showwarning(APP_TITLE, f"Web 控制台端口不合法：{port}\n应为 1–65535 的数字")
                return
            self._goto(4)

    def _prev_step(self) -> None:
        if self.step > 1:
            self._goto(self.step - 1)

    # ---------------------------------------------------------- 跳过安装

    def _detect_installed(self) -> None:
        """「已装过？」—— 找到本机已有实例就直跳第 ⑤ 步。

        探测必须走后台线程：``setup_vault.scan`` 会按盘符浅扫并逐个体检候选库
        （要 walk 每个目录数 md 条目），在机械盘或网络盘上足以让窗口白屏两秒。
        """
        if self.detect_busy:
            return
        self.detect_busy = True
        self.btn_skip.configure(state="disabled", text=SKIP_BUSY_LABEL)
        raw = self.var_install.get().strip()
        self._append("\n[..  ] 正在检测本机是否已装过记忆库…\n")
        threading.Thread(target=self._detect_worker,
                         args=(Path(raw) if raw else None,), daemon=True).start()

    def _detect_worker(self, seed: Path | None) -> None:
        try:
            info = find_installed(seed)
            err = ""
        except Exception as e:      # 探测炸了也只是"没找到"，不该带走窗口
            info, err = {}, f"{type(e).__name__}: {e}"
        self.q.put(("installed", {"info": info, "error": err}))

    def _on_installed(self, payload: dict) -> None:
        from tkinter import messagebox

        self.detect_busy = False
        self.btn_skip.configure(text=SKIP_LABEL)
        self._refresh_skip_btn()

        err = payload.get("error") or ""
        if err:
            self._append(f"[FAIL] 检测已装实例失败：{err}\n")
            messagebox.showerror(APP_TITLE, f"检测已装实例时出错：\n{err}")
            return

        info = payload.get("info") or {}
        found = info.get("found") or {}
        if not found:
            n = info.get("scanned", 0)
            self._append(f"[WARN] 扫了 {n} 个候选目录，没有可以直接接入的已装实例\n")
            for p in info.get("broken") or []:
                self._append(f"[WARN]   {p} 有安装记录但 server.py 已不在（需重装）\n")
            messagebox.showinfo(
                APP_TITLE,
                "没有找到可以直接接入的已装实例。\n\n"
                "请先走完前四步完成安装 —— 装好后会自动进入第 ⑤ 步。")
            return

        install = Path(found["install"])
        data = Path(found["data"])
        vf = found["vf"]

        # 先定库目录（会派生一次本体），再覆盖成实际本体并按需标「手动」：
        # 反过来的话 _on_data_changed 会把本体重新派生回 库/ai-memory，
        # 已经分开放置的实例会被悄悄指到不存在的路径。
        self.var_install_manual = (install != (data / "ai-memory"))
        self.var_data.set(str(data))
        self.var_install.set(str(install))
        self._refresh_derive()
        self._refresh_state_label()
        self._refresh_summary()

        self._append(f"[OK  ] 找到已装实例：{install}\n")
        self._append(f"       {core.verify_state_line(vf)}\n")
        self._append(f"       记忆库：{data}\n")
        for note in vf.get("notes") or []:
            self._append(f"       · {note}\n")
        if vf.get("state") == core.VERIFY_DRIFT:
            self._append("[WARN] 文件核验不通过：接入后可能启动失败，"
                         "建议回第 ④ 步强制重装\n")

        # 第 ⑤ 步必须重扫：现有列表可能是上一个本体目录的结论
        self.link_rows = []
        self.link_statuses = []
        self._goto(5)

    # ---------------------------------------------------------------- 交互

    def _pick_dir(self, var, is_vault: bool | None = None) -> None:
        from tkinter import filedialog

        cur = var.get().strip()
        init = cur if cur and Path(cur).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(title="选择目录", initialdir=init)
        if not chosen:
            return
        var.set(str(Path(chosen)))
        # 手动给「程序本体」选目录 ⇒ 后续不再被库目录覆盖
        if is_vault is False:
            self.var_install_manual = True
            self._refresh_derive()

    def _on_vault_pick(self, _event=None) -> None:
        sel = self.lb_vault.curselection()
        if not sel or not self._vault_items:
            return
        idx = sel[0]
        if 0 <= idx < len(self._vault_items):
            self.var_data.set(str(self._vault_items[idx].path))
            self.lbl_vault_msg.configure(
                text=f"已选用：{self._vault_items[idx].path}", foreground=C_OK)

    def _on_vault_init(self) -> None:
        from tkinter import messagebox

        target = self.var_data.get().strip()
        if not target:
            messagebox.showwarning(APP_TITLE, "请先填写或选择一个目录")
            return
        try:
            import setup_vault as vault

            res = vault.init_vault(Path(target))
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"新建记忆库失败：{type(e).__name__}: {e}")
            return
        created = res.get("created") if isinstance(res, dict) else None
        msg = f"已在该目录播种初始笔记{f'（{created} 个文件）' if created else ''}：{target}"
        self.lbl_vault_msg.configure(text=msg, foreground=C_OK)
        self._start_vault_scan()

    def _start_vault_scan(self) -> None:
        if getattr(self, "_scanning", False):
            return
        self._scanning = True
        self.lbl_vault_msg.configure(text="正在扫描…", foreground=C_DIM)
        threading.Thread(target=self._scan_vaults, daemon=True).start()

    def _scan_vaults(self) -> None:
        # 扫描会摸多个盘符根，放后台线程，结果经队列回主线程
        try:
            import setup_vault as vault

            items = vault.scan()
        except Exception as e:  # noqa: BLE001
            self.q.put(("vaults", {"items": [], "error": f"{type(e).__name__}: {e}"}))
            return
        self.q.put(("vaults", {"items": items, "error": ""}))

    def _on_vaults(self, info: dict) -> None:
        self._scanning = False
        items = info.get("items") or []
        self._vault_items = items
        self.lb_vault.delete(0, "end")
        if info.get("error"):
            self.lbl_vault_msg.configure(text=f"扫描失败：{info['error']}", foreground=C_FAIL)
            return
        if not items:
            self.lbl_vault_msg.configure(
                text="没扫到已有库。选一个空目录后点「在这个目录新建空库」即可。",
                foreground=C_DIM)
            return
        for v in items:
            if v.empty:
                desc = "空目录"
            else:
                bits = [f"{v.md_entries} 条"]
                if v.has_index:
                    bits.append("有索引")
                if v.program:
                    bits.append("含程序")
                desc = " · ".join(bits)
            self.lb_vault.insert("end", f"{v.path}    —— {desc}")

        best = max(items, key=lambda x: x.score)
        if len(items) == 1:
            self.lbl_vault_msg.configure(
                text=f"扫到 1 个候选，双击即可选用。", foreground=C_DIM)
        else:
            self.lbl_vault_msg.configure(
                text=f"扫到 {len(items)} 个候选，最像记忆库的是：{best.path}（双击选用）",
                foreground=C_DIM)

    def _refresh_summary(self) -> None:
        if not hasattr(self, "lbl_sum_vault"):
            return
        self.lbl_sum_vault.configure(text=self.var_data.get().strip() or "—")
        self.lbl_sum_prog.configure(text=self.var_install.get().strip() or "—")
        vf = self._verify()
        # 状态行直接复用逻辑层的文案生成器 —— 界面上说的和命令行里说的必须是同一句话，
        # 否则「日志说核验不通过、界面说一切正常」这种自相矛盾迟早会来一次。
        self.lbl_sum_state.configure(text=core.verify_state_line(vf))
        # 附加组件写清楚，别让人到安装完才发现少装/多装了东西
        # 一个开关装两样，摘要就把两样都写出来 —— 用户勾之前得知道到底会多出什么
        if hasattr(self, "var_extras") and self.var_extras.get():
            port = self.var_webport.get().strip() if hasattr(self, "var_webport") else "8811"
            self.lbl_sum_extra.configure(
                text=f"HTTP 传输层 + Web 控制台（端口 {port or '8811'}）")
        else:
            self.lbl_sum_extra.configure(text="无（只装 MCP 本体）")
        if vf.get("state") in (core.VERIFY_DRIFT,):
            self.lbl_sum_state.configure(foreground=C_FAIL)
        elif vf.get("state") == core.VERIFY_PARTIAL:
            self.lbl_sum_state.configure(foreground=C_WARN)
        else:
            self.lbl_sum_state.configure(foreground=C_DIM)

    # ------------------------------------------------------------ 版本列表

    def _reload_tags(self) -> None:
        if self.tags_busy:
            return
        self.tags_busy = True
        self.ver_tags = []
        self.ver_map = {}
        self.var_ver.set(VER_LOADING)
        self.cb_ver.configure(values=[VER_LOADING], state="readonly")
        self.btn_ver.configure(state="disabled")
        threading.Thread(target=self._fetch_tags, daemon=True).start()

    def _fetch_tags(self) -> None:
        try:
            tags = core.fetch_remote_tags(limit=25, quiet=True)
        except Exception:
            tags = []
        self.q.put(("tags", {"tags": tags}))

    def _on_tags(self, info: dict) -> None:
        self.tags_busy = False
        if self.var_srcmode.get() != "GitHub 自动下载":
            self.btn_ver.configure(state="disabled")
            return
        self.btn_ver.configure(state="normal")
        tags = list(info.get("tags") or [])
        if not tags:
            self.ver_tags = []
            self.ver_map = {VER_NO_NET: None}
            self.var_ver.set(VER_NO_NET)
            self.cb_ver.configure(values=[VER_NO_NET], state="readonly")
            self._append("[WARN] 远端版本列举失败（GitHub API 与 git 均不可达）\n"
                         "       默认仍装最新版；要指定版本请用命令行 --ref，或改选本地目录源\n")
            return
        self._render_tags(tags)

    def _render_tags(self, tags: list[str], keep: bool = False) -> None:
        """把 tag 列表渲染成下拉项，并标出「最新 / 已装」。

        ``keep=True`` 时尽量保住用户已经选中的那一项 —— 重进第 ④ 步或按刷新时
        不该把人的选择默默重置回最新版。
        """
        prev = self._picked_ref() if (keep and self.ver_map) else None
        self.ver_tags = list(tags)
        cur = self._verify().get("version")
        labels: list[str] = []
        self.ver_map = {}
        chosen = None
        for i, t in enumerate(tags):
            marks = []
            if i == 0:
                marks.append("最新")
            if cur and core.version_cmp(t, cur) == 0:
                marks.append("已装")
            txt = f"{t}  ← {'、'.join(marks)}" if marks else t
            labels.append(txt)
            # 最新项的 ref 故意留空：让解析器自己再查一次最新，
            # 而不是把「此刻看到的最新」写死进参数里
            self.ver_map[txt] = None if i == 0 else t
            if prev and t == prev:
                chosen = txt
        self.cb_ver.configure(values=labels, state="readonly")
        self.var_ver.set(chosen or labels[0])

    def _picked_ref(self) -> str | None:
        """把下拉的显示文本翻回 tag。None 表示「要最新版」。"""
        if self.var_srcmode.get() != "GitHub 自动下载":
            return None
        txt = self.var_ver.get().strip()
        if txt in self.ver_map:
            return self.ver_map[txt]
        # 正常路径走不到这里（下拉是 readonly）。留着是为了万一 var_ver 被外部改写 ——
        # 用 core.parse_version 判断而不是另写一个正则，事实源只留一份。
        if core.parse_version(txt):
            return txt
        return None

    def _on_srcmode(self) -> None:
        remote = self.var_srcmode.get() == "GitHub 自动下载"
        if remote:
            self.frm_local.grid_remove()
            self.btn_ver.configure(state="disabled" if self.tags_busy else "normal")
            # 从「本地目录」切回来时必须把下拉一并解禁。只恢复按钮状态会留下
            # 一个死角：下拉永久变灰，用户只能靠「刷新」救回来（探针实测抓到过）。
            self.cb_ver.configure(state="readonly")
            if not self.ver_tags and not self.tags_busy:
                self._reload_tags()
        else:
            self.frm_local.grid()
            # 本地目录装什么版本由目录内容决定，这里给选择是假的
            self.cb_ver.configure(state="disabled")
            self.btn_ver.configure(state="disabled")

    def _append(self, text: str) -> None:
        tag = None
        if "[FAIL" in text:
            tag = "err"
        elif "[WARN" in text:
            tag = "warn"
        elif "[OK" in text:
            tag = "ok"
        self.txt.configure(state="normal")
        if tag:
            self.txt.insert("end", text, tag)
        else:
            self.txt.insert("end", text)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _clear_log(self) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _busy(self, busy: bool, label: str = "") -> None:
        self.busy = busy
        st = "disabled" if busy else "normal"
        self.btn_dry.configure(state=st)
        self.btn_run.configure(state=st)
        self.btn_next.configure(state=st)
        self.btn_prev.configure(state="disabled" if busy
                                else ("normal" if self.step > 1 else "disabled"))
        self.btn_run.configure(text=label or ("开始更新" if self._installed() else "开始安装"))
        # 捷径按钮的可点与否由「已装 + 不忙」两个条件共同决定，不能跟着 st 走：
        # 安装结束后 st 会回到 normal，但没装成时它仍然不该可点。
        self._refresh_skip_btn()

    def _refresh_skip_btn(self, vf: dict | None = None) -> None:
        """底栏「接入 AI 工具」的启用闸门：**本机确有已装实例**才允许点。

        为什么不做成"永远可点、点了再告诉你没装"：那个按钮的语义是"跳过前四步"，
        而没装的时候前四步无可跳过 —— 可点却只能弹一句"没找到"，用户会先怀疑
        按钮坏了。灰着 + 顶部状态行那句「未检测到已安装实例，将执行全新安装」
        已经把理由说清楚了。

        ``vf`` 可由调用方传入（``_refresh_state_label`` 刚算过一份），避免为了一次
        状态刷新重复 hash 七个核心文件。
        """
        if not hasattr(self, "btn_skip"):
            return
        v = vf if vf is not None else self._verify()
        ok = (not self.busy) and v.get("state", core.VERIFY_NONE) != core.VERIFY_NONE
        self.btn_skip.configure(state="normal" if ok else "disabled")

    def _verify(self) -> dict:
        """核验当前本体目录。**每次现读盘**，只读一个小 json + 几个小文件，
        代价可忽略，却避免了「异步探测的到达次序」这类时序坑。"""
        try:
            p = Path(self.var_install.get().strip() or core.default_install_dir())
            return core.verify_install(p)
        except Exception:
            return {"state": core.VERIFY_NONE, "version": None, "notes": []}

    def _installed(self) -> bool:
        return self._verify().get("state", core.VERIFY_NONE) != core.VERIFY_NONE

    # ---------------------------------------------------------------- 探测

    def _start_probe(self) -> None:
        # Tcl 不是线程安全的：任何 Tk 对象（含 StringVar）都只能在主线程碰。
        # 因此先把要用的值在主线程取出来，再交给后台线程。
        raw = self.var_install.get().strip()
        install_dir = Path(raw) if raw else core.default_install_dir()
        threading.Thread(target=self._probe, args=(install_dir,), daemon=True).start()

    def _probe(self, install_dir: Path) -> None:
        # 分两批上报：本地项（快）先亮，网络项（慢）后亮。
        # 否则 GitHub 不可达时要等满超时，状态条会长时间停在「检测中…」。
        quick: dict = {"install_dir": install_dir}
        quick["python"] = core.find_python()
        quick["mcp"] = core.python_has_mcp(quick["python"]) if quick["python"] else False
        try:
            quick["meta"] = core.read_meta(install_dir)
        except Exception:
            quick["meta"] = {}
        self.q.put(("env", quick))

        net = False
        try:
            core._http_get("https://api.github.com/repos/" + core.REPO_SLUG, timeout=6)
            net = True
        except Exception:
            pass
        self.q.put(("env", {"net": net}))

    def _apply_env(self, info: dict) -> None:
        self.env.update(info)

        if "python" in info:
            py = info["python"]
            if py:
                self._set_dot(self.lbl_py, True, "● Python 已就绪")
            else:
                self._set_dot(self.lbl_py, False, "● 未找到 Python")
        if "mcp" in info:
            self._set_dot(self.lbl_mcp, bool(info["mcp"]),
                          "● mcp 已就绪" if info["mcp"]
                          else "● mcp 未装（将自动安装）")
        if "net" in info:
            self._set_dot(self.lbl_net, bool(info["net"]),
                          "● GitHub 可达" if info["net"]
                          else "● GitHub 不可达（用本地源）")
        if "meta" in info:
            meta = info["meta"] or {}
            if meta:
                # 先定库目录（会顺带派生一次本体），再按元数据覆盖本体；
                # 若元数据里的本体与派生值不同，说明这个实例是分开放置的 ⇒ 标记为手动
                dd = meta.get("data_dir")
                if dd:
                    self.var_data.set(str(dd))
                ii = meta.get("install_dir")
                if ii:
                    self.var_install_manual = (Path(ii) != self._auto_install())
                    self.var_install.set(str(ii))
                    self._refresh_derive()
            self._refresh_state_label()
            self._refresh_summary()
            self._busy(False)

    def _refresh_state_label(self) -> None:
        """概要行始终反映磁盘真实状态 —— 不去依赖异步探测的到达次序。"""
        vf = self._verify()
        try:
            p = Path(self.var_install.get().strip() or core.default_install_dir())
            dd = Path(vf.get("data_dir") or p)
            n = core.count_entries(dd)
        except Exception:
            n = 0
        if vf.get("state") == core.VERIFY_NONE:
            self.lbl_state.configure(text="未检测到已安装实例，将执行全新安装",
                                     foreground=C_DIM)
            self._refresh_skip_btn(vf)
            return
        ver = f"v{vf['version']}" if vf.get("version") else "版本未知"
        ev = core.VERIFY_EVIDENCE_LABEL.get(vf.get("evidence", "none"), "")
        line = f"{core.verify_state_label(vf)}：{ver}"
        if vf.get("state") == core.VERIFY_OK:
            line += f"（{ev}），记忆库 {n} 个条目"
        self.lbl_state.configure(
            text=line,
            foreground=C_FAIL if vf.get("state") == core.VERIFY_DRIFT else C_DIM)
        # 状态行与捷径按钮读的是同一份核验结论 —— 两处各算一次会出现
        # 「上面说有、下面按钮灰着」这种自相矛盾
        self._refresh_skip_btn(vf)

    def _set_dot(self, label, good: bool, text: str) -> None:
        label.configure(text=text, foreground=C_OK if good else C_WARN)

    # ---------------------------------------------------------------- 执行

    def _build_args(self, dry: bool):
        action = "update" if self._installed() else "install"
        args = core.parse_setup_args([action])
        # 新模型：库目录是主体，程序本体默认是它的 ai-memory 子目录（可手动改）。
        # 两者不再强制相同 —— 旧版那个「合并布局」勾选框已随之取消。
        args.install_dir = self.var_install.get().strip() or None
        args.data_dir = self.var_data.get().strip() or None
        if self.var_srcmode.get() == "本地目录":
            args.source = self.var_local.get().strip() or None
        args.mirror = self.var_mirror.get().strip() or None
        # 版本：下拉选中项 → --ref；None = 交给解析器取最新
        args.ref = self._picked_ref()

        # 一个勾选框驱动两个开关：只装其中一个的状态在界面上无法被表达出来
        args.with_optional = bool(self.var_extras.get())
        args.with_web = bool(self.var_extras.get())
        args.web_port = (self.var_webport.get().strip() or None) if args.with_web else None
        args.no_pip = bool(self.var_nopip.get())
        args.force = (self.var_mode.get() == "force")
        args.dry_run = dry
        args.yes = True
        return args, action

    def run_task(self, dry: bool) -> None:
        if self.worker and self.worker.is_alive():
            return
        args, action = self._build_args(dry)

        if not args.install_dir:
            from tkinter import messagebox
            messagebox.showwarning(APP_TITLE, "请先指定程序安装目录")
            return
        if self.var_srcmode.get() == "本地目录" and not args.source:
            from tkinter import messagebox
            messagebox.showwarning(APP_TITLE, "已选择「本地目录」作为安装源，请填写或浏览该目录")
            return

        # 回退要二次确认：装低版本会让「只有新版才有的能力」在客户端里直接报错，
        # 而这类报错看起来跟安装器毫无关系，事后极难归因。
        cur_ver = self._verify().get("version")
        if not dry and args.ref and cur_ver and core.version_cmp(args.ref, cur_ver) == -1:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"这是一次回退：v{cur_ver} → {args.ref}\n\n"
                    "回退后，若某个客户端用到了新版本才有的能力，会直接报错。\n"
                    "记忆库里的笔记不会被改动。\n\n确定要回退吗？"):
                self._append("[..  ] 已取消回退\n")
                return

        self._clear_log()
        self._busy(True, "执行中…")
        verb = "预演" if dry else ("更新" if action == "update" else "安装")
        self._append(f"=== {verb}开始 ===\n")

        def job():
            code = 1
            try:
                code = core.do_install_or_update(args, action)
            except SystemExit as e:
                code = int(e.code or 0)
            except Exception as e:
                self._append(f"[FAIL] {type(e).__name__}: {e}\n")
                code = 1
            self.q.put(("done", {"code": code, "dry": dry, "action": action}))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def _run_cli(self, argv: list[str]) -> None:
        """带参数启动时：在窗口内执行命令行入口，输出原样进日志区。"""
        if self.worker and self.worker.is_alive():
            return
        self._clear_log()
        self._busy(True, "执行中…")
        self._append(f"$ ai-memory-setup {' '.join(argv)}\n\n")

        # 避免逻辑层在 frozen 下去等一个并不存在的回车
        os.environ["AI_MEMORY_SETUP_NO_PAUSE"] = "1"

        def job():
            code = 0
            try:
                code = core.main(list(argv))
            except SystemExit as e:
                code = int(e.code or 0)
            except Exception as e:
                self._append(f"[FAIL] {type(e).__name__}: {e}\n")
                code = 1
            self.q.put(("done", {"code": code, "dry": False, "action": "cli"}))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def _on_task_done(self, info: dict) -> None:
        self._refresh_state_label()
        self._busy(False)
        from tkinter import messagebox

        code = info.get("code", 1)
        if info.get("dry"):
            self._append("\n=== 预演结束（未改动任何文件）===\n")
            return
        if code == 0:
            self._append("\n=== 完成 ===\n")
            if info.get("action") == "cli":
                messagebox.showinfo(APP_TITLE, "操作完成。\n\n详情见上方日志。")
                return
            # 装完直接进第 ⑤ 步：扫描本机 AI 工具并把配置写进去。
            # 不弹「要不要继续」——「装好了但 AI 用不上」是一个没有价值的状态。
            self._append("\n=== 接着让 AI 工具认到这份记忆库 ===\n")
            self.link_rows = []
            self.link_statuses = []
            self._goto(5)
            return
        self._append("\n=== 失败，请查看日志 ===\n")
        messagebox.showerror(APP_TITLE, f"操作未成功（退出码 {code}）。\n\n详情见日志区。")

    # ---------------------------------------------------------------- 收尾

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            from tkinter import messagebox

            if not messagebox.askyesno(APP_TITLE, "任务仍在执行，确定退出？"):
                return
        self._closing = True
        try:
            sys.stdout = self._real_stdout
        except Exception:
            pass
        self.root.destroy()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main() -> int:
    argv = sys.argv[1:]

    # 直接用 python 运行本文件时（非打包），参数照常走控制台，不做窗口包装
    if argv and not core.is_frozen():
        return core.main(argv)

    # Windows 高分屏：必须在创建 Tk 之前声明 DPI 感知
    if sys.platform == "win32":
        try:
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    try:
        # 打包态带参数：窗口照常打开，参数交给窗口在日志区执行
        SetupApp(root, auto_argv=argv or None)
    except Exception as e:
        messagebox.showerror(APP_TITLE, f"界面初始化失败：{type(e).__name__}: {e}")
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
