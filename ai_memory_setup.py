#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-memory 安装 / 更新器（setup v1.0.0）

用途
----
把 ai-memory-template 的「安装」与「更新」收敛为一个可双击运行、可打包成
Windows 单文件 exe 分发的程序。目标用户是没有 Python 环境准备经验的人。

设计要点（对应一次可行性评估发现的四个缺口）
--------------------------------------------
1. **程序文件与用户数据严格分离**
   只覆盖白名单内的运行时模块；``*.md`` / ``*.db`` / ``*.db-shm`` / ``*.db-wal``
   等数据文件永不写入。并支持 ``--install-dir`` 与 ``--data-dir`` 分离布局
   （主程序与记忆库各放一处），而合并布局（两者同目录）仍兼容旧 install.ps1。

2. **版本旁路标记**
   主程序本身不自报版本号，故本工具在程序目录写 ``.ai-memory-install.json``，
   记录已装版本与各文件 sha256，作为「是否有新版」的判定依据。不入侵 server.py。

3. **拉取通道分级降级**
   本地目录 → git → GitHub API + codeload → 镜像前缀。任一可用即可工作，
   因此在 GitHub 被网络策略阻断的机器上仍能以 ``--source`` 走离线安装。

4. **覆盖必有退路**
   任何被覆盖的文件先备份到 ``<install-dir>/.ai-memory-backup/<时间戳>/``，
   并支持 ``--dry-run`` 预演，先看清动哪些文件再决定。

用法
----
    ai-memory-setup                       # 无参数：交互菜单
    ai-memory-setup check                 # 环境自检（Python / mcp / GitHub 连通）
    ai-memory-setup install               # 安装到默认目录
    ai-memory-setup update                # 更新已装实例
    ai-memory-setup install -s D:\\ai-memory-template-work
    ai-memory-setup install -i D:\\app\\ai-memory -d D:\\ai记忆
    ai-memory-setup update --dry-run      # 只预演，不落盘

本程序只使用标准库，便于 PyInstaller 打包为无依赖单文件 exe。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

SETUP_VERSION = "1.0.0"
REPO_SLUG = "dpkg-s/ai-memory-template"
REPO_URL = f"https://github.com/{REPO_SLUG}"
MCP_REQUIREMENT = "mcp>=1.30,<2"

# server.py 直接 import 的模块 + 自身 —— 缺一个即 ImportError
CORE_FILES = [
    "server.py",
    "memory_index.py",
    "yaml_io.py",
    "text_utils.py",
    "locks.py",
    "metrics.py",
    "memory_config.py",
]

# 可选模块：Streamable HTTP 传输（v2.6.0 起）
# 2026-10-16：Web 控制台（serve_web.py / web_console.py + web/）已拆分为独立仓
# ai-memory-web，不再随本仓分发 —— 安装器只管 MCP 侧文件。
OPTIONAL_FILES = [
    "transport_http.py",
    "serve_http.py",
]

# 只读资产：仅首次安装时播种；一旦存在即视为用户资产，更新时绝不覆盖
#   SEED_AS_DIR  —— 保留目录层级，落 <data_dir>/<名字>/...
#   SEED_AS_FLAT —— 内容平铺到记忆库根，落 <data_dir>/<文件名>
# 两种落法与 install.ps1 / install.sh 的行为保持一致。
SEED_AS_DIR = ["rules"]
SEED_AS_FLAT = ["template"]
SEED_ROOT_FILES = ["setup/claude.json", "setup/codex.toml", "setup/workbuddy.json"]

META_NAME = ".ai-memory-install.json"
BACKUP_DIR = ".ai-memory-backup"

# 绝不允许被本工具写入的数据文件特征
PROTECTED_SUFFIXES = (".md", ".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite3")

# 镜像前缀（ghproxy 风格），用户可用 --mirror 覆盖
DEFAULT_MIRRORS = [
    "",  # 直连优先
    "https://ghfast.top/",
    "https://gh-proxy.com/",
]

UA = f"ai-memory-setup/{SETUP_VERSION}"


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def _redirect_safe() -> None:
    """统一输出通道的行为。

    - 真实控制台：交给 Python 默认处理 —— Windows 下走 WriteConsoleW，中文正常。
    - 被重定向 / 管道 / 被脚本调用：改用 UTF-8，避免退化成 GBK 导致乱码或
      grep 之类的工具把它当成二进制。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None or not hasattr(stream, "reconfigure"):
                continue
            try:
                is_tty = stream.isatty()
            except Exception:
                is_tty = False
            if not is_tty:
                stream.reconfigure(encoding="utf-8")
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def log(tag: str, msg: str) -> None:
    print(f"[{tag:<4}] {msg}")


def ok(msg: str) -> None:
    log("OK", msg)


def err(msg: str) -> None:
    log("FAIL", msg)


def warn(msg: str) -> None:
    log("WARN", msg)


def note(msg: str) -> None:
    log("..", msg)


def hr(title: str = "") -> None:
    if title:
        print(f"\n=== {title} ===")
    else:
        print("-" * 66)


# --------------------------------------------------------------------------
# 杂项工具
# --------------------------------------------------------------------------

def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file(a: Path, b: Path) -> bool:
    """两个文件内容是否逐字节相同（大小先比，省一次哈希）。"""
    try:
        if not (a.is_file() and b.is_file()):
            return False
        if a.stat().st_size != b.stat().st_size:
            return False
        return sha256_file(a) == sha256_file(b)
    except OSError:
        return False


def is_protected(name: str) -> bool:
    low = name.lower()
    return low.endswith(PROTECTED_SUFFIXES)


def safe_target(root: Path, rel: str) -> Path:
    """把相对路径解析到 root 内，阻止 ../ 逃逸（zip slip 防护）。"""
    candidate = (root / rel).resolve()
    root_r = root.resolve()
    if root_r != candidate and root_r not in candidate.parents:
        raise ValueError(f"路径逃逸，已拒绝：{rel}")
    return candidate


def _in_tree(path: Path, root: Path) -> bool:
    """path 是否位于 root 树内（含 root 自身）。"""
    try:
        return path == root or root in path.parents
    except Exception:
        return False


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def parse_version(text: str) -> tuple:
    """把 'v2.5.0' / '2.6.0' 解析为可比较元组；不可解析返回空元组（视为最小）。"""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return ()
    return tuple(int(x) for x in m.groups())


def parse_setup_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ai-memory-setup",
        description="ai-memory 安装 / 更新器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("action", nargs="?", default=None,
                   choices=["install", "update", "check"],
                   help="install=安装  update=更新  check=环境自检（缺省则进交互菜单）")
    p.add_argument("-i", "--install-dir", default=None,
                   help="程序安装目录（默认 ~/ai-memory）")
    p.add_argument("-d", "--data-dir", default=None,
                   help="记忆库目录（默认与程序目录相同，即合并布局）")
    p.add_argument("-s", "--source", default=None,
                   help="安装源：本地目录路径，或指向 zip 的 URL")
    p.add_argument("--ref", default=None, help="指定 tag / 分支（远端或 git 拉取时用）")
    p.add_argument("--from-git", action="store_true", help="强制用 git 拉取")
    p.add_argument("--mirror", default=None, help="GitHub 镜像前缀，如 https://ghfast.top/")
    p.add_argument("--with-optional", action="store_true",
                   help="同时安装可选模块（HTTP 传输 / Web 控制台）")
    p.add_argument("--no-pip", action="store_true", help="跳过 pip 安装 mcp 依赖")
    p.add_argument("--dry-run", action="store_true", help="只预演，不写任何文件")
    p.add_argument("--force", action="store_true", help="版本相同也强制执行")
    p.add_argument("-y", "--yes", action="store_true", help="全部默认确认，不交互")
    p.add_argument("--version", action="version", version=f"ai-memory-setup {SETUP_VERSION}")
    return p.parse_args(argv)


# --------------------------------------------------------------------------
# 源（Source）：本地目录 / git / 远端 zip
# --------------------------------------------------------------------------

class Source:
    """已解析好的安装源：一个含 server.py 的目录树 + 版本字符串。"""

    def __init__(self, root: Path, version: str, origin: str, cleanup: bool = False):
        self.root = root
        self.version = version
        self.origin = origin
        self._cleanup = cleanup

    def file(self, rel: str) -> Path | None:
        p = self.root / rel
        return p if p.is_file() else None

    def has(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    def close(self) -> None:
        if self._cleanup:
            shutil.rmtree(self.root, ignore_errors=True)


def _version_from_tree(root: Path) -> str:
    """从 CHANGELOG.md 首个小节标题取版本；取不到则回退 'unknown'。"""
    changelog = root / "CHANGELOG.md"
    if changelog.is_file():
        try:
            for line in changelog.read_text(encoding="utf-8", errors="replace").splitlines()[:80]:
                m = re.match(r"^##\s*\[?(\d+\.\d+\.\d+)", line.strip())
                if m:
                    return m.group(1)
        except OSError:
            pass
    return "unknown"


def source_from_local(path: Path) -> Source:
    if not (path / "server.py").is_file():
        raise FileNotFoundError(f"本地源缺少 server.py：{path}")
    return Source(path.resolve(), _version_from_tree(path), f"local:{path}")


def _unpack_zip(zip_path: Path, dest: Path) -> Path:
    """解压 zip，剥掉 codeload 归档最外层的 <repo>-<ref>/ 目录。"""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("zip 内容为空")
        top = names[0].split("/", 1)[0]
        for info in zf.infolist():
            rel = info.filename
            if rel.startswith(top + "/"):
                rel = rel[len(top) + 1:]
            if not rel:
                continue
            target = safe_target(dest, rel)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return dest


def _http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_remote_tag(mirrors: list[str]) -> str | None:
    """查 GitHub API 拿最新 tag；失败返回 None（不抛，交由上层降级）。"""
    api = f"https://api.github.com/repos/{REPO_SLUG}/tags"
    req = urllib.request.Request(api, headers={"User-Agent": UA,
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data:
            name = item.get("name", "")
            if parse_version(name):
                return name
    except Exception as e:
        note(f"GitHub API 不可达（{type(e).__name__}），尝试下载归档")
    return None


def source_from_remote(mirror: str | None, ref: str | None,
                       known_version: str | None = None) -> Source:
    """下载 tag 归档并解压到临时目录。mirror 为空表示直连。"""
    mirrors = [mirror] if mirror is not None else DEFAULT_MIRRORS
    tag = ref or known_version or _fetch_remote_tag(mirrors)
    if not tag:
        raise RuntimeError(
            "无法确定远端版本号（GitHub API 不可达且未指定 --ref）。\n"
            "      可改用：--ref v2.5.0 指定版本，或用 --source 指向本地目录。"
        )
    tag_norm = tag if tag.startswith("v") else f"v{tag}"

    last_err: Exception | None = None
    for prefix in mirrors:
        url = f"{prefix}https://codeload.github.com/{REPO_SLUG}/zip/refs/tags/{tag_norm}"
        note(f"下载 {tag_norm} … {'（镜像 ' + prefix + '）' if prefix else '（直连）'}")
        tmpdir = Path(tempfile.mkdtemp(prefix="ai-memory-src-"))
        try:
            blob = _http_get(url)
            zpath = tmpdir / "src.zip"
            zpath.write_bytes(blob)
            note(f"已下载 {human_size(len(blob))}，解压中…")
            tree = tmpdir / "tree"
            tree.mkdir()
            _unpack_zip(zpath, tree)
            if not (tree / "server.py").is_file():
                raise ValueError("归档中未找到 server.py")
            zpath.unlink(missing_ok=True)
            return Source(tree, tag_norm.lstrip("v"), f"remote:{tag_norm}", cleanup=True)
        except Exception as e:
            last_err = e
            shutil.rmtree(tmpdir, ignore_errors=True)
            warn(f"该通道失败：{type(e).__name__}: {e}")
    raise RuntimeError(
        f"所有下载通道均失败（最后错误：{last_err}）。\n"
        "      常见原因：GitHub 域名被 hosts 指向 127.0.0.1、或代理未放行。\n"
        "      解决：清理 hosts 中的 github 条目 / 让代理放行 / 或改用 --source 本地目录。"
    )


def source_from_git(url: str, ref: str | None) -> Source:
    if shutil.which("git") is None:
        raise RuntimeError("未找到 git 可执行文件")
    tmpdir = Path(tempfile.mkdtemp(prefix="ai-memory-git-"))
    try:
        note(f"git clone {url} …")
        cmd = ["git", "clone", "--depth", "1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [url, str(tmpdir / "repo")]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr else "git clone 失败")
        repo = tmpdir / "repo"
        if not (repo / "server.py").is_file():
            raise ValueError("clone 结果中没有 server.py")
        ver = ref or "unknown"
        try:
            d = subprocess.run(["git", "-C", str(repo), "describe", "--tags", "--abbrev=0"],
                               capture_output=True, text=True, timeout=30)
            if d.returncode == 0 and d.stdout.strip():
                ver = d.stdout.strip().lstrip("v")
        except Exception:
            pass
        return Source(repo, ver, f"git:{url}", cleanup=True)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def resolve_source(args) -> Source:
    if args.source:
        p = Path(args.source)
        if p.is_dir():
            return source_from_local(p)
        if re.match(r"^https?://", args.source, re.I):
            tmpdir = Path(tempfile.mkdtemp(prefix="ai-memory-src-"))
            try:
                blob = _http_get(args.source)
                zpath = tmpdir / "src.zip"
                zpath.write_bytes(blob)
                tree = tmpdir / "tree"
                tree.mkdir()
                _unpack_zip(zpath, tree)
                if not (tree / "server.py").is_file():
                    raise ValueError("归档中未找到 server.py")
                return Source(tree, _version_from_tree(tree), f"url:{args.source}", cleanup=True)
            except Exception:
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise
        raise FileNotFoundError(f"--source 既不是目录也不是 http(s) URL：{args.source}")

    if args.from_git:
        return source_from_git(f"{REPO_URL}.git", args.ref)

    # 无显式源：优先 git，失败退回远端归档
    try:
        return source_from_git(f"{REPO_URL}.git", args.ref)
    except Exception as e:
        note(f"git 通道不可用（{e}），改用归档下载")
    return source_from_remote(args.mirror, args.ref)


# --------------------------------------------------------------------------
# 安装元数据
# --------------------------------------------------------------------------

def meta_path(install_dir: Path) -> Path:
    return install_dir / META_NAME


def read_meta(install_dir: Path) -> dict:
    p = meta_path(install_dir)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_meta(install_dir: Path, data: dict) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    meta_path(install_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# 环境探测
# --------------------------------------------------------------------------

def find_python() -> str | None:
    """找可用的 Python 解释器。exe 运行时不能用 sys.executable（那是 exe 自身）。"""
    if not is_frozen():
        return sys.executable
    cands = []
    env_py = os.environ.get("AI_MEMORY_PYTHON")
    if env_py:
        cands.append(env_py)
    cands += ["python.exe", "python3.exe", "python"]
    for c in cands:
        found = shutil.which(c)
        if found:
            return found
    return None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def python_has_mcp(python: str) -> bool:
    try:
        r = subprocess.run([python, "-c", "import mcp, sys; sys.exit(0)"],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def count_entries(data_dir: Path) -> int:
    if not data_dir.is_dir():
        return 0
    try:
        return sum(1 for p in data_dir.rglob("*.md") if p.is_file())
    except OSError:
        return 0


def default_install_dir() -> Path:
    return Path.home() / "ai-memory"


# --------------------------------------------------------------------------
# 规划（plan）
# --------------------------------------------------------------------------

class Plan:
    def __init__(self):
        self.writes: list[tuple[Path, Path]] = []   # (源文件, 目标文件)
        self.skips: list[tuple[str, str]] = []      # (相对名, 原因)
        self.removed: list[str] = []                # 旧版本有、新版本无（仅提示）

    @property
    def n_writes(self) -> int:
        return len(self.writes)


def build_plan(src: Source, install_dir: Path, data_dir: Path,
               include_optional: bool, seed: bool, force: bool) -> Plan:
    plan = Plan()
    wanted = list(CORE_FILES) + (OPTIONAL_FILES if include_optional else [])

    for name in wanted:
        s = src.file(name)
        if s is None:
            if name in CORE_FILES:
                plan.skips.append((name, "源中缺失（核心模块）"))
            continue
        plan.writes.append((s, install_dir / name))

    # 播种资产只在「首次安装」写入：
    #   rules/    → 保留目录层级，落 data_dir
    #   template/ → 内容平铺到 data_dir 根（记忆库的 md 必须位于根目录）
    #   setup/    → 配置样例，属程序侧文档，落 install_dir
    if seed:
        for d in SEED_AS_DIR:
            srcdir = src.root / d
            if not srcdir.is_dir():
                continue
            for f in sorted(srcdir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(src.root)
                    plan.writes.append((f, data_dir / rel))
        for d in SEED_AS_FLAT:
            srcdir = src.root / d
            if not srcdir.is_dir():
                continue
            for f in sorted(srcdir.rglob("*")):
                if f.is_file():
                    plan.writes.append((f, data_dir / f.name))
        for rel in SEED_ROOT_FILES:
            f = src.file(rel)
            if f:
                plan.writes.append((f, install_dir / rel))

    # 目标目录里已有的 .py 但新版本不再提供 —— 仅提示，绝不代删
    if install_dir.is_dir():
        for p in sorted(install_dir.glob("*.py")):
            if p.name not in wanted and p.name != "ai_memory_setup.py":
                plan.removed.append(p.name)
    return plan


def skip_reason(d: Path, data_dir: Path, install_dir: Path) -> str | None:
    """该目标是否必须跳过写入。

    保护的是「覆盖」而不是「创建」：数据文件在首次安装时可正常播种，
    但只要它已经存在，就视为用户资产，任何情况下都不再被本工具改写。
    """
    if _in_tree(d, data_dir) and not _in_tree(d, install_dir) and d.exists():
        return "记忆库资产已存在，跳过覆盖"
    if is_protected(d.name) and d.exists():
        return "数据文件已存在，跳过覆盖"
    return None


def describe_plan(plan: Plan, install_dir: Path, data_dir: Path) -> None:
    hr("变更预览")
    new_cnt = upd_cnt = same_cnt = keep_cnt = 0
    for s, d in plan.writes:
        why = skip_reason(d, data_dir, install_dir)
        if why:
            keep_cnt += 1
            continue
        if not d.exists():
            new_cnt += 1
            print(f"   新增  {d.name}")
        elif _same_file(d, s):
            same_cnt += 1
        else:
            upd_cnt += 1
            print(f"   覆盖  {d.name}")
    note(f"新增 {new_cnt} / 覆盖 {upd_cnt} / 内容相同 {same_cnt} / 保留 {keep_cnt}")
    for name, why in plan.skips:
        warn(f"跳过 {name}：{why}")
    if plan.removed:
        warn("以下文件存在于目标目录但新版未提供，本工具不会删除："
             + ", ".join(plan.removed))
    if upd_cnt == 0 and new_cnt == 0:
        ok("没有需要写入的文件")


# --------------------------------------------------------------------------
# 执行（apply）
# --------------------------------------------------------------------------

def apply_plan(plan: Plan, install_dir: Path, data_dir: Path,
               dry_run: bool, force: bool = False, backup: bool = True) -> dict:
    stats = {"written": 0, "backed_up": 0, "skipped_protected": 0, "unchanged": 0}
    backup_root = install_dir / BACKUP_DIR / stamp() if backup else None

    for s, d in plan.writes:
        why = skip_reason(d, data_dir, install_dir)
        if why:
            stats["skipped_protected"] += 1
            continue

        # 内容没变就不动它：既避免无谓备份，也让「预览」与「执行」结果一致
        if d.exists() and not force and _same_file(d, s):
            stats["unchanged"] += 1
            continue

        if dry_run:
            continue

        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists() and backup_root is not None:
            try:
                rel_b = d.relative_to(install_dir)
            except ValueError:
                rel_b = Path("data") / d.name
            b = backup_root / rel_b
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(d, b)
            stats["backed_up"] += 1
        shutil.copy2(s, d)
        stats["written"] += 1

    return stats


def record_meta(src: Source, install_dir: Path, data_dir: Path,
                include_optional: bool, stats: dict, action: str) -> None:
    files = {}
    for name in list(CORE_FILES) + (OPTIONAL_FILES if include_optional else []):
        p = install_dir / name
        if p.is_file():
            files[name] = sha256_file(p)
    write_meta(install_dir, {
        "setup_version": SETUP_VERSION,
        "installed_version": src.version,
        "installed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "action": action,
        "source": src.origin,
        "install_dir": str(install_dir),
        "data_dir": str(data_dir),
        "with_optional": include_optional,
        "files": files,
        "last_run": {k: v for k, v in stats.items()},
    })


# --------------------------------------------------------------------------
# 各动作
# --------------------------------------------------------------------------

def do_check(args) -> int:
    hr("环境自检")
    py = find_python()
    if py:
        try:
            v = subprocess.run([py, "--version"], capture_output=True, text=True, timeout=30)
            ok(f"Python: {(v.stdout or v.stderr).strip()}  ({py})")
        except Exception as e:
            warn(f"Python 调用异常：{e}")
        if python_has_mcp(py):
            ok("mcp 包：已安装")
        else:
            warn(f"mcp 包：未安装（需 {MCP_REQUIREMENT}）")
    else:
        err("Python：未找到。MCP 服务器运行需要 Python 3.10+")

    install_dir = Path(args.install_dir).resolve() if args.install_dir else default_install_dir()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else install_dir
    meta = read_meta(install_dir)
    if meta:
        ok(f"已装版本：{meta.get('installed_version')}   （安装于 {meta.get('installed_at')}）")
        ok(f"程序目录：{install_dir}")
        ok(f"记忆库　：{meta.get('data_dir', data_dir)}   共 {count_entries(Path(meta.get('data_dir', data_dir)))} 个 md")
    else:
        note(f"未检测到已安装实例（{install_dir} 无 {META_NAME}）")

    hr("网络通道")
    try:
        _http_get("https://api.github.com/repos/dpkg-s/ai-memory-template", timeout=15)
        ok("GitHub API：可达")
    except Exception as e:
        warn(f"GitHub API 不可达（{type(e).__name__}）—— 仍可用 --source 本地目录安装")
    for m in ("https://www.baidu.com",):
        try:
            _http_get(m, timeout=10)
            ok(f"通用外网（{m}）：可达")
            break
        except Exception:
            warn(f"通用外网（{m}）：不可达")
    hr()
    return 0


def _ensure_pip(py: str | None, args) -> None:
    if args.no_pip:
        warn(f"按要求跳过 mcp 安装（--no-pip）；请确保已有 {MCP_REQUIREMENT}")
        return
    if not py:
        warn("未找到 Python，跳过 pip 安装")
        return
    if python_has_mcp(py):
        ok(f"mcp 包已就绪（{MCP_REQUIREMENT}）")
        return
    note(f"安装依赖 {MCP_REQUIREMENT} …")
    r = subprocess.run([py, "-m", "pip", "install", MCP_REQUIREMENT],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ok("mcp 安装完成")
    else:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        err(f"pip 安装失败：{tail[-1] if tail else '未知错误'}")
        warn(f"可手动执行：{py} -m pip install \"{MCP_REQUIREMENT}\"")


def do_install_or_update(args, action: str) -> int:
    install_dir = Path(args.install_dir).resolve() if args.install_dir else default_install_dir()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else install_dir

    hr(f"ai-memory {'安装' if action == 'install' else '更新'}  v{SETUP_VERSION}")
    note(f"程序目录：{install_dir}")
    note(f"记忆库　：{data_dir}")

    meta = read_meta(install_dir)
    local_ver = meta.get("installed_version")
    if action == "install" and local_ver:
        note(f"目标目录已有安装记录（v{local_ver}），本次将按「更新」语义执行")

    # 源
    hr("获取安装源")
    try:
        src = resolve_source(args)
    except Exception as e:
        err(str(e))
        return 2
    try:
        ok(f"源版本：{src.version}   来源：{src.origin}")
        if local_ver and parse_version(src.version) and parse_version(src.version) == parse_version(local_ver):
            if not args.force:
                ok(f"已是最新（v{local_ver}）。如需强制重装请加 --force")
                return 0
            warn("版本相同，因 --force 继续执行")
        if local_ver and parse_version(src.version) and parse_version(local_ver) \
                and parse_version(src.version) < parse_version(local_ver):
            warn(f"源版本 v{src.version} 低于已装 v{local_ver}，这可能是回退操作")

        # 安全检查：目标目录不能被解析到系统根
        if install_dir == install_dir.parent:
            err("拒绝把盘符根目录作为安装目录")
            return 2

        seed = not (data_dir / "记忆索引.md").exists()
        plan = build_plan(src, install_dir, data_dir, args.with_optional, seed, args.force)
        describe_plan(plan, install_dir, data_dir)

        if args.dry_run:
            hr()
            ok("--dry-run：未写入任何文件")
            return 0

        # 确认
        if not args.yes and sys.stdin is not None and sys.stdin.isatty():
            try:
                ans = input("\n确认执行？[Y/n] ").strip().lower()
            except EOFError:
                ans = "y"
            if ans not in ("", "y", "yes"):
                warn("已取消")
                return 0

        hr("执行")
        stats = apply_plan(plan, install_dir, data_dir, dry_run=False, force=args.force)
        ok(f"写入 {stats['written']} 个文件，备份 {stats['backed_up']} 个"
           + (f"，内容未变 {stats['unchanged']} 个" if stats.get("unchanged") else ""))
        if stats["backed_up"]:
            note(f"备份位置：{install_dir / BACKUP_DIR}")

        _ensure_pip(find_python(), args)
        record_meta(src, install_dir, data_dir, args.with_optional, stats, action)

        if seed and data_dir.is_dir():
            ok(f"记忆库已播种（{count_entries(data_dir)} 个 md）")

        print_config_hint(find_python(), install_dir, data_dir)
        hr()
        ok("完成")
        return 0
    finally:
        src.close()


def print_config_hint(py: str | None, install_dir: Path, data_dir: Path) -> None:
    hr("配置各 AI 客户端")
    py_s = py or "python"
    server = install_dir / "server.py"
    env_line = "" if data_dir == install_dir else f'  AI_MEMORY_DIR = "{data_dir}"'
    print(f"  Python : {py_s}")
    print(f"  Server : {server}")
    if env_line:
        print(f"  记忆库 : {data_dir}（需设置环境变量 AI_MEMORY_DIR 指向它）")
    print()
    print("  --- Codex CLI (~/.codex/config.toml) ---")
    print("    [mcp_servers.ai-memory]")
    print(f'    command = "{py_s}"')
    print(f'    args = ["{server}"]')
    if env_line:
        print("    [mcp_servers.ai-memory.env]")
        print(f'    AI_MEMORY_DIR = "{data_dir}"')
    print()
    print("  --- Claude Desktop (claude_desktop_config.json) ---")
    print("    {")
    print('      "mcpServers": {')
    print('        "ai-memory": {')
    print(f'          "command": "{py_s.replace(chr(92), chr(92)*2)}",')
    print(f'          "args": ["{str(server).replace(chr(92), chr(92)*2)}"]')
    print("        }")
    print("      }")
    print("    }")
    print()
    print("  完整样例见 setup/ 目录")


def menu(args) -> int:
    hr(f"ai-memory 安装 / 更新器  v{SETUP_VERSION}")
    install_dir = Path(args.install_dir).resolve() if args.install_dir else default_install_dir()
    meta = read_meta(install_dir)
    py = find_python()

    print("  当前探测：")
    print(f"    程序目录 : {install_dir}")
    if meta:
        print(f"    已装版本 : v{meta.get('installed_version')}  （{meta.get('installed_at')}）")
        dd = Path(meta.get("data_dir", install_dir))
        print(f"    记忆库   : {dd}   {count_entries(dd)} 个 md")
    else:
        print("    已装版本 : （未安装）")
    print(f"    Python   : {py or '未找到 —— 运行 MCP 服务器前必须先装 Python'}")
    print()
    print("  请选择：")
    print("    [1] 安装 / 更新（推荐）")
    print("    [2] 仅做环境自检")
    print("    [3] 查看帮助后退出")
    print("    [0] 退出")
    print()
    try:
        c = input("  输入序号后回车：").strip()
    except EOFError:
        return 1

    if c == "1":
        act = "update" if meta else "install"
        return do_install_or_update(args, act)
    if c == "2":
        return do_check(args)
    if c == "3":
        parse_setup_args(["--help"])
        return 0
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    _redirect_safe()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_setup_args(argv)

    try:
        if args.action == "check":
            return do_check(args)
        if args.action in ("install", "update"):
            return do_install_or_update(args, args.action)
        return menu(args)
    except KeyboardInterrupt:
        print()
        warn("已被用户中断")
        return 130
    except Exception as e:
        print()
        err(f"未预期的错误：{type(e).__name__}: {e}")
        if os.environ.get("AI_MEMORY_SETUP_TRACE"):
            import traceback
            traceback.print_exc()
        return 1
    finally:
        # 双击 exe 场景：留住窗口让用户看清结果
        if is_frozen() and not os.environ.get("AI_MEMORY_SETUP_NO_PAUSE"):
            try:
                if sys.stdin is not None and sys.stdin.isatty():
                    input("\n按回车键退出…")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
