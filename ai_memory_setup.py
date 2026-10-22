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
    ai-memory-setup                       # 无参数：管家菜单（四分区）
    ai-memory-setup check                 # 环境自检（Python / mcp / GitHub 连通）
    ai-memory-setup versions              # 核验已装版本 + 列出远端可选版本（只读）
    ai-memory-setup install               # 安装到默认目录
    ai-memory-setup update                # 更新已装实例
    ai-memory-setup install --ref v2.5.0  # 安装指定版本（回退也走这条路）
    ai-memory-setup install -s D:\\ai-memory-template-work
    ai-memory-setup install -i D:\\app\\ai-memory -d D:\\ai记忆
    ai-memory-setup update --dry-run      # 只预演，不落盘

    ai-memory-setup vault                 # 扫描本机上的记忆库候选目录
    ai-memory-setup vault use D:\\ai记忆    # 把某个目录指定为当前库
    ai-memory-setup vault init D:\\新库      # 把空目录播种成可用记忆库
    ai-memory-setup link                  # 扫描 AI 工具，显示各自连接状态
    ai-memory-setup link apply            # 写入 / 修正各客户端配置（先备份）
    ai-memory-setup link apply --dry-run  # 只预演要动哪些文件

v1.1.0 起本工具不再只是"安装器"，而是"记忆库管家"：
  - ``vault`` 子命令管记忆库目录（找、选、建），见 setup_vault.py
  - ``link`` 子命令管 AI 工具连接（扫描、判定漂移、批量写入），见 setup_link.py
两个子模块都只依赖标准库，且都是"先返回结构化结果、由 CLI 层负责打印"，
以便后续换成图形界面时不必重写逻辑。

v1.2.0 把无参数启动改成四分区管家菜单：进菜单先算一遍状态（程序 / 版本 /
记忆库条目数 / AI 工具已配与待修数），再按 [1] 安装更新 [2] 记忆库目录
[3] AI 工具连接 [4] 环境自检 进入分区；任何写入都要二次确认，Ctrl+C 退回
上一级，EOF 退出。

v1.3.0 增加「已装版本核验」与「版本选择」：
  - ``verify_install()`` 把「已装版本」从**声称**还原成**结论** —— 交叉比对
    安装记录 / 目录里的 CHANGELOG / 各文件 sha256，并显式给出证据等级。
    定位状态的是四态：未安装 / 有程序无记录 / 记录与文件不一致 / 完好。
  - ``versions`` 动作 + ``--ref <tag>``：列举远端可选版本，装哪个由用户定，
    回退不再需要手抄 tag。``--ref`` 本就存在，此前只是没有「列出来供选」的入口。

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

SETUP_VERSION = "1.4.0"
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
        description="ai-memory 记忆库管家（安装 / 更新 / 记忆库目录 / AI 工具连接）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("action", nargs="?", default=None,
                   choices=["install", "update", "check", "versions", "vault", "link"],
                   help="install=安装  update=更新  check=环境自检  versions=核验已装并列出可选版本  "
                        "vault=记忆库目录  link=AI 工具连接（缺省则进交互菜单）")
    p.add_argument("sub", nargs="?", default=None,
                   help="vault: list|use|init|path   link: scan|apply|clients")
    p.add_argument("value", nargs="?", default=None,
                   help="vault use/init 的目标目录")
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
                   help="同时安装 HTTP 传输层（transport_http.py / serve_http.py）")
    p.add_argument("--with-web", action="store_true",
                   help="同时安装 Web 控制台（整棵子树落 web-console/ + start-web 脚本）")
    p.add_argument("--web-port", default=None, metavar="PORT",
                   help="Web 控制台监听端口（默认 8811；等价于 AI_MEMORY_WEB_PORT）")
    p.add_argument("--no-pip", action="store_true", help="跳过 pip 安装 mcp 依赖")
    p.add_argument("--dry-run", action="store_true", help="只预演，不写任何文件")
    p.add_argument("--force", action="store_true", help="版本相同也强制执行")
    p.add_argument("--client", default=None,
                   help="link apply 只处理指定客户端，逗号分隔（如 codex,workbuddy）")
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


def _changelog_version(root: Path) -> str | None:
    """从 CHANGELOG.md 首个小节标题取版本；没有该文件或解析不出则返回 None。

    这是**独立于安装记录**的版本证据：``.ai-memory-install.json`` 是我们自己写的
    「声称」，而 CHANGELOG 是随源码一起发下来的「事实」。两者打架时以 CHANGELOG
    为准 —— 磁盘上的文件才是真正跑起来的东西。

    注意：安装器只覆盖白名单内的 ``.py``，**不安装** CHANGELOG.md，所以这份证据
    只在「源码目录就地安装」时存在。拿到 None 时只能说明「没有独立证据」，
    不能据此判定版本缺失或文件有问题。
    """
    for name in ("CHANGELOG.md", "changelog.md"):
        changelog = root / name
        if not changelog.is_file():
            continue
        try:
            text = changelog.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Unreleased 小节没有数字版本号，正则天然跳过它
        for line in text.splitlines()[:120]:
            m = re.match(r"^##\s*\[?(\d+\.\d+\.\d+)", line.strip())
            if m:
                return m.group(1)
    return None


def _version_from_tree(root: Path) -> str:
    """从 CHANGELOG.md 取版本；取不到则回退 'unknown'（下载源的默认值）。"""
    return _changelog_version(root) or "unknown"


def version_cmp(a: str | None, b: str | None) -> int | None:
    """比较两个版本串：-1 小于 / 0 相同 / 1 大于；任一不可解析返回 None。

    不可解析时返回 None 而不是 0 —— 「不知道」和「相等」必须分开，
    否则 'unknown' 与 'unknown' 会被判成「已是最新」，直接跳过更新。
    """
    ka, kb = parse_version(a or ""), parse_version(b or "")
    if not ka or not kb:
        return None
    return (ka > kb) - (ka < kb)


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


def sort_tags(names: list[str]) -> list[str]:
    """过滤不可解析项，按版本**从新到旧**去重。

    同一个版本可能同时存在 ``v2.5.0`` 与 ``2.5.0`` 两个 tag。去重时保留带 v 的那个：
    codeload 拼 URL 时要 ``refs/tags/v2.5.0``，而 ``git clone --branch`` 用两种写法
    都能命中，所以留带 v 的更通用。
    """
    best: dict[tuple, str] = {}
    for raw in names:
        name = (raw or "").strip()
        key = parse_version(name)
        if not key:
            continue
        cur = best.get(key)
        if cur is None or (name.lower().startswith("v") and not cur.lower().startswith("v")):
            best[key] = name
    return [best[k] for k in sorted(best, reverse=True)]


def _tags_from_git(quiet: bool = False) -> list[str]:
    """``git ls-remote --tags`` 兜底通道。GitHub API 匿名限流 60 次/小时，这条不限。"""
    if shutil.which("git") is None:
        return []
    try:
        r = subprocess.run(["git", "ls-remote", "--tags", "--refs", f"{REPO_URL}.git"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        return [ln.split("refs/tags/", 1)[1].strip()
                for ln in r.stdout.splitlines() if "refs/tags/" in ln]
    except Exception as e:
        if not quiet:
            note(f"git ls-remote 不可用（{type(e).__name__}）")
        return []


def fetch_remote_tags(limit: int = 40, quiet: bool = False) -> list[str]:
    """远端可选的版本 tag，按版本从新到旧；都拿不到返回空列表（不抛）。

    两条通道：GitHub API（快、不依赖 git）→ ``git ls-remote``。返回空列表时调用方
    应提示「无法列举，请用 --ref 手动指定」，而不是当成「远端没有版本」。
    """
    tags: list[str] = []
    try:
        api = f"https://api.github.com/repos/{REPO_SLUG}/tags?per_page=100"
        req = urllib.request.Request(api, headers={"User-Agent": UA,
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list):
            tags = [str(it.get("name", "")) for it in data if isinstance(it, dict)]
    except Exception as e:
        if not quiet:
            note(f"GitHub API 不可达（{type(e).__name__}），改用 git ls-remote")
    if not tags:
        tags = _tags_from_git(quiet=quiet)
    out = sort_tags(tags)
    return out[:limit] if limit and limit > 0 else out


def _fetch_remote_tag(mirrors: list[str]) -> str | None:
    """取最新 tag；失败返回 None（不抛，交由上层降级）。"""
    tags = fetch_remote_tags(limit=1)
    return tags[0] if tags else None


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
        # 安装记录里的版本号统一不带 v 前缀（remote 通道也是 lstrip('v')）。
        # 不统一的话同一版本在两条通道下会写成两个字符串，所有比较处都得各自兼容。
        if ref and ref[:1] in ("v", "V") and parse_version(ref):
            ver = ref[1:]
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
# 已装版本核验
# --------------------------------------------------------------------------

VERIFY_NONE = "none"        # 目录里既无 server.py 也无安装记录
VERIFY_PARTIAL = "partial"  # 有 server.py，但无安装记录（手工装过 / 记录丢了）
VERIFY_DRIFT = "drift"      # 有记录，但文件与记录对不上（缺文件 / 被改过）
VERIFY_OK = "ok"            # 有记录且文件全部核对通过

VERIFY_STATE_LABEL = {
    VERIFY_NONE: "未安装",
    VERIFY_PARTIAL: "有程序文件、无安装记录",
    VERIFY_DRIFT: "记录与文件不一致",
    VERIFY_OK: "完好",
}

# 「已装版本」这个数字的可信度 —— 必须随版本一起报给用户，
# 否则「v3.0.0」看起来像结论，实际只是一句转述。
VERIFY_EVIDENCE_LABEL = {
    "none": "无（无法确定已装版本）",
    "meta": "仅安装记录，无独立证据",
    "changelog": "CHANGELOG（无安装记录）",
    "meta+changelog": "安装记录与 CHANGELOG 一致",
}


def verify_state_label(v: dict) -> str:
    """状态标签。``ok`` 但没做过 sha256 核验时不能写「完好」—— 那是没查过，不是查过了。"""
    st = v.get("state", VERIFY_NONE)
    if st == VERIFY_OK and not v.get("integrity_checked", True):
        return "已安装（完整性未核验）"
    return VERIFY_STATE_LABEL.get(st, st)


def verify_install(install_dir: Path) -> dict:
    """核验已装实例，返回结构化结论。**纯离线、不打印、不写盘。**

    立场：「已装版本」是一个**声称**，不是一个**事实**。三条证据交叉才敢下结论 ——

    1. ``.ai-memory-install.json`` 里的 ``installed_version`` —— 我们自己写的，可能
       因「拷文件覆盖」而过期，或整个文件丢了
    2. 目录里的 ``CHANGELOG.md`` —— 随源码发下来的独立证据（安装器不装它，故只在
       源码目录就地安装时存在；拿不到不等于文件有问题）
    3. 记录里的各文件 ``sha256`` —— 能查出「文件被改过 / 被截断 / 莫名缺失」

    两者版本打架时**以 CHANGELOG 为准**：磁盘上的文件才是真跑起来的东西。
    """
    install_dir = Path(install_dir)
    meta = read_meta(install_dir)
    has_server = (install_dir / "server.py").is_file()

    res: dict = {
        "path": str(install_dir),
        "state": VERIFY_NONE,
        "version": None,             # 综合后的可信版本
        "evidence": "none",          # 上述版本的证据等级
        "recorded_version": meta.get("installed_version"),
        "changelog_version": None,
        "installed_at": meta.get("installed_at"),
        "source": meta.get("source"),
        "data_dir": meta.get("data_dir"),
        "has_program": has_server,
        "has_record": bool(meta),
        "missing": [],               # 记录里有、磁盘上没有
        "changed": [],               # 记录里的 sha256 与磁盘不符
        "unrecorded": [],            # 是核心模块但记录里没登记（旧版记录）
        "integrity_checked": True,   # 是否真的做了 sha256 核验（无指纹时为 False）
        "notes": [],
    }
    if not has_server and not meta:
        return res

    # --- 证据 2：CHANGELOG ---
    cv = _changelog_version(install_dir)
    res["changelog_version"] = cv

    # --- 证据 3：逐文件 sha256 ---
    files = meta.get("files") or {}
    if isinstance(files, dict) and files:
        for name, expect in files.items():
            p = install_dir / name
            if not p.is_file():
                res["missing"].append(name)
                continue
            try:
                if sha256_file(p) != expect:
                    res["changed"].append(name)
            except OSError:
                # 读不了就当被改过 —— 核验的目的是「不敢保证」，不是「尽量通过」
                res["changed"].append(name)
        # 「未登记但存在」只在有记录时才是有效信息；没有记录时列出全部核心文件
        # 纯属噪音（每条都必然命中，用户看了也不知道该干什么）
        for name in CORE_FILES:
            if name not in files and (install_dir / name).is_file():
                res["unrecorded"].append(name)
    else:
        # 记录里没有文件指纹（极旧版本 / 记录被截断）→ 完整性无从核验，
        # 只能如实说「验不了」。这里刻意**不**拿当前 CORE_FILES 去要求旧版本 ——
        # 那会把「老版本少一个后来才拆出来的模块」误判成「文件损坏」。
        res["integrity_checked"] = False
        if meta:
            # 无记录时「没有指纹」是显然的，再报一遍只会和「无安装记录」重复
            res["notes"].append("安装记录里没有文件指纹，完整性无法核验（只能确认文件在不在）")

    # --- 状态 ---
    if meta:
        res["state"] = VERIFY_DRIFT if (res["missing"] or res["changed"]) else VERIFY_OK
    else:
        res["state"] = VERIFY_PARTIAL

    # --- 证据 1 vs 2：定「可信版本」 ---
    rv = res["recorded_version"]
    if rv and cv:
        same = version_cmp(rv, cv) == 0
        res["version"] = cv if not same else rv
        res["evidence"] = "meta+changelog"
        if not same:
            res["notes"].append(
                f"安装记录写的是 v{rv}，但目录里的 CHANGELOG 是 v{cv} —— 以 CHANGELOG 为准")
    elif rv:
        res["version"] = rv
        res["evidence"] = "meta"
    elif cv:
        res["version"] = cv
        res["evidence"] = "changelog"
        res["notes"].append("无安装记录，版本取自目录里的 CHANGELOG")

    if res["missing"]:
        res["notes"].append("缺少文件：" + "、".join(res["missing"]))
    if res["changed"]:
        res["notes"].append("内容与安装记录不符：" + "、".join(res["changed"]))
    if res["unrecorded"]:
        res["notes"].append("未登记但存在：" + "、".join(res["unrecorded"]))
    return res


def verify_state_line(v: dict) -> str:
    """把核验结果压成一行给状态栏用（GUI 与 CLI 共用，避免两处文案跑偏）。"""
    state = v.get("state", VERIFY_NONE)
    ver_txt = f"v{v['version']}" if v.get("version") else "版本未知"
    if state == VERIFY_NONE:
        return "未检测到已安装实例（将执行全新安装）"
    if state == VERIFY_PARTIAL:
        return f"发现程序文件但无安装记录（按安装处理，不动记忆库笔记）· {ver_txt}"
    if state == VERIFY_DRIFT:
        bits = []
        if v.get("missing"):
            bits.append(f"缺 {len(v['missing'])} 个文件")
        if v.get("changed"):
            bits.append(f"{len(v['changed'])} 个文件被改过")
        detail = f"（{'、'.join(bits)}）" if bits else ""
        return f"检测到已装 {ver_txt}，但核验不通过{detail}，建议强制重装"
    if not v.get("integrity_checked", True):
        return f"检测到已装 {ver_txt}（记录无文件指纹，完整性未核验）"
    return f"检测到已装 {ver_txt}（核验通过，将执行更新）"


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


def find_mcp_python() -> tuple[str | None, bool]:
    """找一个**已装 mcp** 的解释器，返回 (路径, 是否确认有 mcp)。

    为什么不直接用 ``find_python()``：那只是"PATH 里第一个 python"，很可能
    没装 mcp，写进客户端配置后 MCP 会启动失败、报 ImportError，而错误要到
    用户下一次开客户端才暴露。优先信任"已经连着的客户端用的那个解释器"。
    """
    cands: list[str] = []
    env_py = os.environ.get("AI_MEMORY_PYTHON")
    if env_py:
        cands.append(env_py)
    if not is_frozen():
        cands.append(sys.executable)

    try:
        import setup_link as link

        for c in link.clients():
            p, exists = link.resolve_config_path(c)
            if not exists:
                continue
            try:
                entry = link.read_entry(c, p)
            except Exception:
                continue
            cmd = (entry or {}).get("command")
            if cmd:
                cands.append(str(cmd))
    except Exception:
        pass

    for name in ("python.exe", "python3.exe", "python"):
        found = shutil.which(name)
        if found:
            cands.append(found)

    seen: set[str] = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            if Path(c).is_file() and python_has_mcp(c):
                return c, True
        except OSError:
            continue
    return find_python(), False


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
                include_optional: bool, stats: dict, action: str,
                include_web: bool = False) -> None:
    files = {}
    for name in list(CORE_FILES) + (OPTIONAL_FILES if include_optional else []):
        p = install_dir / name
        if p.is_file():
            files[name] = sha256_file(p)
    meta = {
        "setup_version": SETUP_VERSION,
        "installed_version": src.version,
        "installed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "action": action,
        "source": src.origin,
        "install_dir": str(install_dir),
        "data_dir": str(data_dir),
        "with_optional": include_optional,
        "with_web": include_web,
        "files": files,
        "last_run": {k: v for k, v in stats.items()},
    }
    if include_optional:
        hd = _http_defaults()
        # 记下「这套 HTTP 服务是什么样」—— 以后排查端口/路径不用去读脚本正文
        meta["http"] = {
            "host": hd["host"],
            "port": hd["port"],
            "path": hd["path"],
            "start": [n for n in (HTTP_SCRIPT_WIN, HTTP_SCRIPT_POSIX)
                      if (install_dir / n).is_file()],
        }
    if include_web:
        wd = _web_defaults()
        web_files = 0
        if (install_dir / WEB_DIRNAME).is_dir():
            web_files = sum(1 for p in (install_dir / WEB_DIRNAME).rglob("*") if p.is_file())
        meta["web"] = {
            "host": wd["host"],
            "port": wd["port"],
            "token": wd["token"],       # 只记"有没有"，不落值
            "mcp": wd["mcp"],
            "dir": WEB_DIRNAME,
            "entry": f"{WEB_DIRNAME}/{WEB_ENTRY}",
            "files": web_files,
            "start": [n for n in (WEB_SCRIPT_WIN, WEB_SCRIPT_POSIX)
                      if (install_dir / n).is_file()],
        }
    write_meta(install_dir, meta)



# --------------------------------------------------------------------------
# 可选模块（Streamable HTTP）：随主程序一起装成一整套
# --------------------------------------------------------------------------

# 启动脚本由安装器**生成**，不从源拷贝。
# 理由：如果「安装 HTTP 传输层」只是往目录里丢两个 .py，用户还得自己去翻环境变量
# 才知道怎么起服务 —— 那只能算「零件备齐了」，不算「装好了一套」。
# 脚本可安全覆盖（重装即重写），因此端口 / 监听地址一律走环境变量：
# 用户不必改脚本，也就不会出现「我改的东西被升级覆盖掉」。
HTTP_SCRIPT_WIN = "start-http.cmd"
HTTP_SCRIPT_POSIX = "start-http.sh"


def _http_defaults() -> dict:
    """HTTP 那套的默认参数。与 transport_http.DEFAULT_* 对齐，但**不 import** 它 ——
    安装器要能在没装 mcp 依赖、甚至没装 Python 的环境里照常跑。"""
    return {
        "host": os.environ.get("AI_MEMORY_HTTP_HOST") or "127.0.0.1",
        "port": os.environ.get("AI_MEMORY_HTTP_PORT") or "8700",
        "path": "/mcp",
    }


def _sh_quote(text: str) -> str:
    """POSIX 单引号转义：路径里若出现单引号，直接包会截断。"""
    return "'" + str(text).replace("'", "'\\''") + "'"


def http_script_text(install_dir: Path, data_dir: Path, *, kind: str,
                     py: str | None = None) -> str:
    """生成 HTTP 启动脚本正文。``kind`` = ``cmd`` | ``sh``（换行符由调用方定）。

    两个平台写法刻意不同：
    * ``.cmd`` 写死 Windows 解释器绝对路径 —— 双击就要能跑，不能依赖 PATH。
    * ``.sh`` 用 ``python3`` + 正斜杠 —— 把 Windows 绝对路径塞进 POSIX 脚本毫无意义，
      何况反斜杠在单引号里是字面量，拷到 Linux 上是 100% 跑不起来。
    """
    serve = install_dir / "serve_http.py"
    py_s = py or "python"
    port = _http_defaults()["port"]
    # 反斜杠转换在 f-string 里写会被 3.12 之前的语法拒绝，故先算好
    serve_posix = str(serve).replace("\\", "/")
    data_posix = str(data_dir).replace("\\", "/")
    if kind == "cmd":
        # 失败兜底：双击运行时窗口会在进程退出的瞬间消失，用户看不到任何原因。
        # 所以非 0 退出一定停下来把「最可能的三条」摆出来再 pause。
        lines = [
            "@echo off",
            # 文件是 UTF-8 写的，而中文 Windows 控制台默认 GBK(936) —— 不切码页时
            # 下面 echo 的中文全是乱码（实测「这�?Python」）。切之前别 echo 任何中文。
            "chcp 65001 >nul",
            "rem ai-memory — Streamable HTTP 入口（安装器生成，重装会覆盖本文件）",
            "rem 按当前安装路径生成；搬到别的机器请重装，别手改路径。",
            "rem 改端口 / 监听地址请用环境变量，不要改这里：",
            f"rem   set AI_MEMORY_HTTP_PORT={port}",
            "rem   set AI_MEMORY_HTTP_HOST=0.0.0.0",
            f"rem   set AI_MEMORY_HTTP_ALLOWED_HOSTS=192.168.1.10:{port},localhost:{port}",
            "rem   （非回环监听必须给白名单，否则程序会拒绝启动）",
            "setlocal",
            f'set "AI_MEMORY_DIR={data_dir}"',
            f'if not defined AI_MEMORY_HTTP_PORT set "AI_MEMORY_HTTP_PORT={port}"',
            f'"{py_s}" "{serve}" %*',
            'set "RC=%ERRORLEVEL%"',
            'if not "%RC%"=="0" (',
            "    echo.",
            f"    echo [启动失败] 退出码 %RC%",
            "    echo   最常见原因：这个 Python 没装 mcp 依赖。补装：",
            f'    echo     "{py_s}" -m pip install "{MCP_REQUIREMENT}"',
            "    echo   其次：端口 %AI_MEMORY_HTTP_PORT% 已被占用；或监听非回环地址却没设 AI_MEMORY_HTTP_ALLOWED_HOSTS。",
            "    pause",
            ")",
            "exit /b %RC%",
            "",
        ]
        return "\r\n".join(lines)
    lines = [
        "#!/bin/sh",
        "# ai-memory — Streamable HTTP 入口（安装器生成，重装会覆盖本文件）",
        "# 按当前安装路径生成；搬到别的机器请重装，别手改路径。",
        "# 改端口 / 监听地址请用环境变量，不要改这里：",
        f"#   AI_MEMORY_HTTP_PORT=8800 ./{HTTP_SCRIPT_POSIX}",
        "#   AI_MEMORY_HTTP_HOST=0.0.0.0 AI_MEMORY_HTTP_ALLOWED_HOSTS=ip:port ./start-http.sh",
        f"export AI_MEMORY_DIR={_sh_quote(data_posix)}",
        f'AI_MEMORY_HTTP_PORT="${{AI_MEMORY_HTTP_PORT:-{port}}}"',
        "export AI_MEMORY_HTTP_PORT",
        "# 先探一下依赖：exec 之后就没机会补救了，缺 mcp 时报错会是一屏看不出重点的 traceback",
        "if ! python3 -c 'import mcp' 2>/dev/null; then",
        '    echo "[启动失败] python3 没装 mcp 依赖。补装：" >&2',
        f"    echo \"    python3 -m pip install '{MCP_REQUIREMENT}'\" >&2",
        "    exit 1",
        "fi",
        f'exec python3 {_sh_quote(serve_posix)} "$@"',
        "",
    ]
    return "\n".join(lines)


def write_http_scripts(install_dir: Path, data_dir: Path, py: str | None,
                       *, dry_run: bool = False) -> dict:
    """写出 HTTP 那套的启动脚本。幂等；内容变了先备份再覆盖。

    两个平台都写（成本极低，且安装目录常被共享 / 挂进容器）。
    注意 ``.cmd`` 必须 CRLF、``.sh`` 必须 LF —— 用 ``write_bytes`` 精确控制，
    走文本模式会按平台自动转换，在 Windows 上生成的 ``.sh`` 会带 CRLF 而跑不起来。
    """
    stats = {"written": [], "unchanged": [], "backed_up": 0}
    backup_root = install_dir / BACKUP_DIR / stamp()
    for name, kind in ((HTTP_SCRIPT_WIN, "cmd"), (HTTP_SCRIPT_POSIX, "sh")):
        p = install_dir / name
        body = http_script_text(install_dir, data_dir, kind=kind, py=py).encode("utf-8")
        if p.is_file():
            try:
                if p.read_bytes() == body:
                    stats["unchanged"].append(name)
                    continue
            except OSError:
                pass
            if not dry_run:
                try:
                    backup_root.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, backup_root / name)
                    stats["backed_up"] += 1
                except OSError:
                    pass
        stats["written"].append(name)
        if dry_run:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        if kind == "sh":
            try:
                p.chmod(p.stat().st_mode | 0o111)
            except OSError:
                pass
    return stats


# --------------------------------------------------------------------------
# Web 控制台（独立仓 ai-memory-web）：同样装成一整套
# --------------------------------------------------------------------------

# 这一套的落法与 HTTP 不同：HTTP 是两个 .py 平铺在安装目录，Web 控制台是
# 「入口 + 装配层 + 整个前端静态目录」，必须整棵子树落到 <install>/web-console/。
# 落成子目录而不是平铺，是因为 web/ 这个静态目录名会和别的东西撞，
# 而且 web_console 默认按「与本文件同级的 web/」去找静态资源 —— 保持相对结构才不用改配置。
WEB_DIRNAME = "web-console"
WEB_ENTRY = "serve_web.py"
WEB_STATIC_DIR = "web"
WEB_SCRIPT_WIN = "start-web.cmd"
WEB_SCRIPT_POSIX = "start-web.sh"

# 运行用不到、装过去只是噪音的东西
WEB_EXCLUDE_DIRS = {"tests", "__pycache__", ".git", ".github", ".pytest_cache", ".idea"}


def _web_defaults() -> dict:
    """Web 控制台的默认参数。与 web_console.py 的默认值对齐，但**不 import** 它 ——
    安装器要能在依赖没装、甚至没装 Python 的环境里照常跑。"""
    return {
        "host": os.environ.get("AI_MEMORY_WEB_HOST") or "127.0.0.1",
        "port": os.environ.get("AI_MEMORY_WEB_PORT") or "8811",
        "token": bool(os.environ.get("AI_MEMORY_WEB_TOKEN")),
        "mcp": os.environ.get("AI_MEMORY_WEB_MCP") or "http://127.0.0.1:8700/mcp",
    }


def find_web_source(root: Path) -> Path | None:
    """在安装源里定位 Web 控制台。

    两种摆法都认：本仓的 ``web-console/`` 子模块，或直接拿 ai-memory-web 当安装源
    （根上就是 serve_web.py）。判据是「入口文件 + 静态目录同时在」—— 只看
    ``serve_web.py`` 会把别的仓的同名入口误认成控制台。
    """
    for cand in (root / WEB_DIRNAME, root):
        if (cand / WEB_ENTRY).is_file() and (cand / WEB_STATIC_DIR).is_dir():
            return cand
    return None


def _web_rels(web_src: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(web_src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(web_src)
        if rel.parts and rel.parts[0] in WEB_EXCLUDE_DIRS:
            continue
        out.append(rel)
    return out


def web_script_text(install_dir: Path, data_dir: Path, *, kind: str,
                    py: str | None = None) -> str:
    """生成 Web 控制台的启动脚本。与 HTTP 那套同款加固，理由完全一样。"""
    entry = install_dir / WEB_DIRNAME / WEB_ENTRY
    py_s = py or "python"
    wd = _web_defaults()
    port = wd["port"]
    entry_posix = str(entry).replace("\\", "/")
    data_posix = str(data_dir).replace("\\", "/")
    if kind == "cmd":
        lines = [
            "@echo off",
            "chcp 65001 >nul",
            "rem ai-memory Web 控制台（安装器生成，重装会覆盖本文件）",
            "rem 按当前安装路径生成；搬到别的机器请重装，别手改路径。",
            "rem 改端口 / 监听地址请用环境变量，不要改这里：",
            f"rem   set AI_MEMORY_WEB_PORT={port}",
            "rem   set AI_MEMORY_WEB_HOST=0.0.0.0",
            "rem   非回环监听必须同时设 AI_MEMORY_WEB_TOKEN，否则程序会拒绝启动",
            "setlocal",
            f'set "AI_MEMORY_DIR={data_dir}"',
            f'if not defined AI_MEMORY_WEB_PORT set "AI_MEMORY_WEB_PORT={port}"',
            f'"{py_s}" "{entry}" %*',
            'set "RC=%ERRORLEVEL%"',
            'if not "%RC%"=="0" (',
            "    echo.",
            f"    echo [启动失败] 退出码 %RC%",
            "    echo   最常见原因：这个 Python 没装 mcp 依赖。补装：",
            f'    echo     "{py_s}" -m pip install "{MCP_REQUIREMENT}"',
            "    echo   其次：端口 %AI_MEMORY_WEB_PORT% 已被占用；或监听非回环地址却没设 AI_MEMORY_WEB_TOKEN。",
            "    pause",
            ")",
            "exit /b %RC%",
            "",
        ]
        return "\r\n".join(lines)
    lines = [
        "#!/bin/sh",
        "# ai-memory Web 控制台（安装器生成，重装会覆盖本文件）",
        "# 按当前安装路径生成；搬到别的机器请重装，别手改路径。",
        "# 改端口 / 监听地址请用环境变量，不要改这里：",
        f"#   AI_MEMORY_WEB_PORT={port} ./{WEB_SCRIPT_POSIX}",
        "#   非回环监听必须同时设 AI_MEMORY_WEB_TOKEN，否则程序会拒绝启动",
        f"export AI_MEMORY_DIR={_sh_quote(data_posix)}",
        f'AI_MEMORY_WEB_PORT="${{AI_MEMORY_WEB_PORT:-{port}}}"',
        "export AI_MEMORY_WEB_PORT",
        "if ! python3 -c 'import mcp' 2>/dev/null; then",
        '    echo "[启动失败] python3 没装 mcp 依赖。补装：" >&2',
        f"    echo \"    python3 -m pip install '{MCP_REQUIREMENT}'\" >&2",
        "    exit 1",
        "fi",
        f'exec python3 {_sh_quote(entry_posix)} "$@"',
        "",
    ]
    return "\n".join(lines)


def write_web_scripts(install_dir: Path, data_dir: Path, py: str | None,
                      *, dry_run: bool = False) -> dict:
    stats = {"written": [], "unchanged": [], "backed_up": 0}
    backup_root = install_dir / BACKUP_DIR / stamp()
    for name, kind in ((WEB_SCRIPT_WIN, "cmd"), (WEB_SCRIPT_POSIX, "sh")):
        p = install_dir / name
        body = web_script_text(install_dir, data_dir, kind=kind, py=py).encode("utf-8")
        if p.is_file():
            try:
                if p.read_bytes() == body:
                    stats["unchanged"].append(name)
                    continue
            except OSError:
                pass
            if not dry_run:
                try:
                    backup_root.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, backup_root / name)
                    stats["backed_up"] += 1
                except OSError:
                    pass
        stats["written"].append(name)
        if dry_run:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        if kind == "sh":
            try:
                p.chmod(p.stat().st_mode | 0o111)
            except OSError:
                pass
    return stats


def install_web(src_root: Path, install_dir: Path, data_dir: Path,
                py: str | None, *, dry_run: bool = False) -> dict:
    """把 Web 控制台整棵子树装到 ``<install>/web-console/``，再生成启动脚本。

    源里没有控制台时**不报错**（远端 zip 不含 submodule 是正常情况）——
    返回 ``found=False``，由调用方如实说明，而不是假装装好了。
    """
    res: dict = {"found": False, "src": None, "dest": None,
                 "written": 0, "unchanged": 0, "scripts": None}
    web_src = find_web_source(src_root)
    if web_src is None:
        return res
    res["found"] = True
    res["src"] = web_src
    dest = install_dir / WEB_DIRNAME
    res["dest"] = dest

    for rel in _web_rels(web_src):
        s = web_src / rel
        d = dest / rel
        try:
            body = s.read_bytes()
        except OSError:
            continue
        if d.is_file():
            try:
                if d.read_bytes() == body:
                    res["unchanged"] += 1
                    continue
            except OSError:
                pass
        res["written"] += 1
        if dry_run:
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_bytes(body)

    if not dry_run:
        res["scripts"] = write_web_scripts(install_dir, data_dir, py)
    return res


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


def do_versions(args) -> int:
    """核验已装实例 + 列举远端可选版本。只读，不写盘。"""
    install_dir = Path(args.install_dir).resolve() if args.install_dir else default_install_dir()

    hr("已装实例核验")
    v = verify_install(install_dir)
    print(f"  程序目录 : {v['path']}")
    print(f"  状态     : {verify_state_label(v)}")
    if v["version"]:
        ev = VERIFY_EVIDENCE_LABEL.get(v["evidence"], v["evidence"])
        print(f"  已装版本 : v{v['version']}    （证据：{ev}）")
    elif v["state"] != VERIFY_NONE:
        print("  已装版本 : 无法确定（既无安装记录，也无 CHANGELOG）")
    if v["installed_at"]:
        print(f"  安装时间 : {v['installed_at']}")
    if v["source"]:
        print(f"  安装来源 : {v['source']}")
    if v["data_dir"]:
        dd = Path(v["data_dir"])
        print(f"  记忆库   : {v['data_dir']}    {count_entries(dd)} 条 md")
    for nt in v["notes"]:
        warn(nt)
    if v["state"] == VERIFY_OK:
        ok("核心文件与安装记录逐一对齐")

    hr("远端可用版本")
    tags = fetch_remote_tags()
    if not tags:
        warn("无法列举远端版本（GitHub API 与 git ls-remote 均不可用）")
        note("不影响安装：可用 --ref 手动指定版本，例如 install --ref v2.5.0")
        hr()
        return 0

    cur = v["version"]
    want = args.ref.lstrip("v") if args.ref else None
    print(f"  共 {len(tags)} 个版本（新 → 旧）：")
    for i, t in enumerate(tags):
        marks = []
        if i == 0:
            marks.append("最新")
        if cur and version_cmp(t, cur) == 0:
            marks.append("已装")
        if want and version_cmp(t, want) == 0:
            marks.append("指定")
        tail = f"    ← {'、'.join(marks)}" if marks else ""
        print(f"    {t}{tail}")

    latest = tags[0]
    if cur:
        c = version_cmp(cur, latest)
        if c == 0:
            ok(f"已是最新（v{cur}）")
        elif c is not None and c < 0:
            note(f"有新版本：v{cur} → {latest}")
        elif c is not None:
            warn(f"本地 v{cur} 高于远端最新 {latest}（本地改动，或该版本尚未打 tag）")
    print()
    note("安装指定版本：install --ref v2.4.2   /   update --ref v2.4.2")
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

    # --web-port 落到环境变量再往下走：端口这件事只有一个事实源（AI_MEMORY_WEB_PORT），
    # 记录、启动脚本、提示文案全从 _web_defaults() 读，避免三处各存一份数字。
    if getattr(args, "web_port", None):
        os.environ["AI_MEMORY_WEB_PORT"] = str(args.web_port).strip()

    hr(f"ai-memory {'安装' if action == 'install' else '更新'}  v{SETUP_VERSION}")
    note(f"程序目录：{install_dir}")
    note(f"记忆库　：{data_dir}")

    meta = read_meta(install_dir)
    # 已装版本先过一遍核验：记录里的数字可能是过期的（比如有人手工拷了新文件覆盖）
    vf = verify_install(install_dir)
    local_ver = vf["version"] or meta.get("installed_version")
    if action == "install" and local_ver:
        note(f"目标目录已有安装记录（v{local_ver}），本次将按「更新」语义执行")
    if vf["state"] == VERIFY_DRIFT:
        warn(f"核验不通过（{VERIFY_STATE_LABEL[VERIFY_DRIFT]}）：")
        for nt in vf["notes"]:
            print(f"      {nt}")
        note("本次将把这些文件按源版本重写；记忆库笔记不受影响")
    elif vf["state"] == VERIFY_PARTIAL:
        note("有程序文件但无安装记录（手工装过或记录丢失）—— 本次会顺带补齐安装记录")

    # 源
    hr("获取安装源")
    try:
        src = resolve_source(args)
    except Exception as e:
        err(str(e))
        return 2
    try:
        ok(f"源版本：{src.version}   来源：{src.origin}")
        cmp = version_cmp(src.version, local_ver) if local_ver else None
        if cmp == 0:
            if not args.force:
                ok(f"已是最新（v{local_ver}）。如需强制重装请加 --force")
                return 0
            warn("版本相同，因 --force 继续执行")
        elif cmp == -1:
            warn(f"源版本 v{src.version} 低于已装 v{local_ver} —— 这是一次**回退**操作")
            note("回退后若客户端用的是新版本才有的能力，会直接报错；确认再继续")

        # 安全检查：目标目录不能被解析到系统根
        if install_dir == install_dir.parent:
            err("拒绝把盘符根目录作为安装目录")
            return 2

        seed = not (data_dir / "记忆索引.md").exists()
        plan = build_plan(src, install_dir, data_dir, args.with_optional, seed, args.force)
        describe_plan(plan, install_dir, data_dir)
        if args.with_optional:
            note(f"将一并生成 HTTP 启动脚本：{HTTP_SCRIPT_WIN} / {HTTP_SCRIPT_POSIX}")
        if args.with_web:
            ws_src = find_web_source(src.root)
            if ws_src is None:
                warn("安装源里没有 Web 控制台（远端 zip 不含 web-console/ 子模块）—— 本次会跳过")
            else:
                n = len(_web_rels(ws_src))
                note(f"将一并安装 Web 控制台：{install_dir / WEB_DIRNAME}"
                     f"（{n} 个文件 + {WEB_SCRIPT_WIN} / {WEB_SCRIPT_POSIX}）")
                if not args.with_optional:
                    warn("Web 控制台需要 HTTP 端点（默认 8700）作为读写通道，本次未勾选 HTTP 传输层")

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

        # 可选模块要装成「一整套」，而不是只多两个 .py 让人自己猜怎么启动
        py = find_python()
        if args.with_optional:
            hs = write_http_scripts(install_dir, data_dir, py)
            if hs["unchanged"] == [HTTP_SCRIPT_WIN, HTTP_SCRIPT_POSIX]:
                ok("HTTP 启动脚本已是最新")
            else:
                ok(f"HTTP 传输层已装好：{'、'.join(hs['written'])}")
            if hs["backed_up"]:
                note(f"旧脚本已备份 {hs['backed_up']} 个（{install_dir / BACKUP_DIR}）")
        else:
            stale = [n for n in (HTTP_SCRIPT_WIN, HTTP_SCRIPT_POSIX)
                     if (install_dir / n).is_file()]
            if stale:
                note(f"目录里已有 {'、'.join(stale)}，本次未勾选可选模块 —— 未改动它们")

        _ensure_pip(py, args)

        # Web 控制台是第 ③ 步的产物：整棵子树 + 启动脚本，同样装成一整套。
        # 必须先落盘再 record_meta —— 记录里的 web.start / web.files 是按磁盘实况
        # 统计的，顺序反了就会写进一份"装好了但一件没落地"的假记录。
        if args.with_web:
            ws = install_web(src.root, install_dir, data_dir, py)
            if not ws["found"]:
                # 远端 zip 不含 submodule 属正常情况 —— 如实说，别假装装好了
                warn("安装源里没有 Web 控制台（远端 zip 不含 web-console/ 子模块）")
                note("换成本地目录源即可一并装上；或用 --source 指向 ai-memory-web 下载的目录")
            else:
                ok(f"Web 控制台已装好：{ws['dest']}"
                   f"（{ws['written']} 个文件写入，{ws['unchanged']} 个未变）")
                st = ws["scripts"] or {}
                if st.get("written"):
                    ok(f"启动脚本：{'、'.join(st['written'])}")
                elif st.get("unchanged"):
                    ok("Web 启动脚本已是最新")
                if st.get("backed_up"):
                    note(f"旧脚本已备份 {st['backed_up']} 个（{install_dir / BACKUP_DIR}）")
                if not args.with_optional:
                    warn("Web 控制台是 MCP 的代理前端 —— 没装 HTTP 传输层时它打得开、但读不到数据")
                    note("补装：重跑 install 并加 --with-optional")
        else:
            stale = [n for n in (WEB_SCRIPT_WIN, WEB_SCRIPT_POSIX)
                     if (install_dir / n).is_file()]
            if stale:
                note(f"目录里已有 {'、'.join(stale)}，本次未勾选 Web 控制台 —— 未改动它们")

        record_meta(src, install_dir, data_dir, args.with_optional, stats, action,
                    include_web=args.with_web)

        if seed and data_dir.is_dir():
            ok(f"记忆库已播种（{count_entries(data_dir)} 个 md）")

        print_config_hint(py, install_dir, data_dir, with_http=args.with_optional,
                          with_web=args.with_web)

        hr()
        ok("完成")
        return 0
    finally:
        src.close()


def print_config_hint(py: str | None, install_dir: Path, data_dir: Path,
                      with_http: bool = False, with_web: bool = False) -> None:
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
    print("  接上本机已装的 AI 工具（写前备份、写后回读校验）：")
    print(f"    {py_s} ai_memory_setup.py link scan            # 先看状态")
    print(f"    {py_s} ai_memory_setup.py link apply --dry-run # 预演要改什么")
    print()
    print("  完整样例见 setup/ 目录")

    if with_http:
        hd = _http_defaults()
        url = f"http://{hd['host']}:{hd['port']}{hd['path']}"
        hr("Streamable HTTP（已随主程序一起安装）")
        print(f"  启动 : {install_dir / HTTP_SCRIPT_WIN}")
        print(f"         （POSIX 用同目录的 {HTTP_SCRIPT_POSIX}）")
        print(f"  地址 : {url}")
        print("  换端口 : set AI_MEMORY_HTTP_PORT=8800")
        print("  局域网 : set AI_MEMORY_HTTP_HOST=0.0.0.0")
        print(f"           并设 AI_MEMORY_HTTP_ALLOWED_HOSTS=192.168.1.10:{hd['port']},"
              f"localhost:{hd['port']}")
        print("           非回环监听**必须**给白名单，否则程序会拒绝启动（这是安全底线）")
        print("  验证 : 启动后访问该地址，能连上即可 —— 缺 MCP 握手头时返回 4xx 属正常")
        note("HTTP 与 stdio 是两个独立入口，同一份记忆库：http 用来多机共用，stdio 给本机客户端")

    if with_web:
        wd = _web_defaults()
        url = f"http://{wd['host']}:{wd['port']}/"
        hr("Web 控制台（已随主程序一起安装）")
        print(f"  启动 : {install_dir / WEB_SCRIPT_WIN}")
        print(f"         （POSIX 用同目录的 {WEB_SCRIPT_POSIX}）")
        print(f"  地址 : {url}")
        print(f"  程序 : {install_dir / WEB_DIRNAME / WEB_ENTRY}")
        print("  换端口 : set AI_MEMORY_WEB_PORT=8812")
        print("  局域网 : set AI_MEMORY_WEB_HOST=0.0.0.0")
        print("           并设 AI_MEMORY_WEB_TOKEN=<随机串>")
        print("           非回环监听**必须**给 token，否则程序会拒绝启动")
        print(f"  上游 : {wd['mcp']}（可用 AI_MEMORY_WEB_MCP 改）")
        note("控制台是 MCP 的前端代理 —— 读写都走上面的 HTTP 端点，"
             "所以它需要 HTTP 传输层在跑；纯 stdio 场景下控制台打得开、但读不到数据")
        if not with_http:
            warn("本次没有一起装 HTTP 传输层，控制台需要单独启动一个 serve_http.py 才可用")


# --------------------------------------------------------------------------
# vault / link：v1.1.0 新增的两个子命令
# --------------------------------------------------------------------------

def _guess_install_dir(args) -> Path:
    """定位程序目录：显式 -i > 默认目录（若真有 server.py）> 从记忆库反推。"""
    if args.install_dir:
        return Path(args.install_dir).expanduser().resolve()
    d = default_install_dir()
    if (d / "server.py").is_file():
        return d
    try:
        import setup_vault as vault

        best = vault.pick_best(None)
        if best and best.program and best.program.is_file():
            return best.program.parent
    except Exception:
        pass
    return d


def _guess_data_dir(args, install_dir: Path) -> Path | None:
    """定位记忆库：显式 -d > 上次指定的当前库 > 安装元数据 > 合并布局。"""
    if args.data_dir:
        return Path(args.data_dir).expanduser().resolve()
    try:
        import setup_vault as vault

        cur = vault.get_current()
        if cur and cur.is_dir():
            return cur
    except Exception:
        pass
    meta = read_meta(install_dir)
    dd = meta.get("data_dir")
    if dd:
        return Path(dd)
    # 没有元数据记录时，用"最像记忆库"的目录，而不是程序目录本身 ——
    # 否则 <vault>/ai-memory/ 这种布局会把库指到程序目录上。
    try:
        import setup_vault as vault

        best = vault.pick_best(install_dir if install_dir.is_dir() else None)
        if best is not None:
            return best.path
    except Exception:
        pass
    return install_dir


def do_vault(args) -> int:
    try:
        import setup_vault as vault
    except ImportError as e:
        err(f"缺少 setup_vault.py：{e}")
        return 1

    sub = (args.sub or "list").lower()
    install_dir = _guess_install_dir(args)

    if sub == "path":
        cur = vault.get_current()
        if not cur:
            warn("尚未指定当前记忆库（运行 vault list 看看有哪些候选）")
            return 1
        print(str(cur))
        return 0

    if sub in ("list", "ls", "scan"):
        hr(f"记忆库扫描  v{vault.VAULT_VERSION}")
        items = vault.scan(install_dir if install_dir.is_dir() else None)
        for line in vault.format_lines(items, vault.get_current()):
            print(line)
        print()
        if items:
            note(f"共 {len(items)} 个候选，最像记忆库的是：{items[0].path}")
            print("  指定当前库：ai-memory-setup vault use <路径>")
            print("  从零建库  ：ai-memory-setup vault init <空目录>")
        return 0

    if sub == "use":
        if not args.value:
            err("用法：ai-memory-setup vault use <目录>")
            return 1
        target = Path(args.value).expanduser()
        try:
            r = vault.set_current(target, install_dir if install_dir.is_dir() else None)
        except Exception as e:  # noqa: BLE001
            err(f"{type(e).__name__}: {e}")
            return 1
        ok(f"当前记忆库：{r['path']}")
        if r.get("meta_updated"):
            note(f"已同步安装元数据：{r['meta_path']}")
        info = vault.inspect(Path(r["path"]), "当前")
        if info.empty:
            warn(f"这个目录看起来是空的（{info.md_entries} 条 md）"
                 "；若要从零开始，先跑 vault init 播种模板")
        else:
            print(f"  条目 {info.md_entries} 条"
                  + ("  · 含索引 md" if info.has_index else "")
                  + ("  · git" if info.has_git else ""))
        print()
        note("下一步：ai-memory-setup link apply —— 让各 AI 工具都指向这个库")
        return 0

    if sub == "init":
        if not args.value:
            err("用法：ai-memory-setup vault init <目录>")
            return 1
        target = Path(args.value).expanduser()
        try:
            r = vault.init_vault(
                target,
                install_dir if install_dir.is_dir() else None,
                force=args.force,
            )
        except Exception as e:  # noqa: BLE001
            err(f"{type(e).__name__}: {e}")
            return 1
        ok(f"已播种记忆库：{r['path']}")
        for n in r["created"]:
            print(f"    + {n}")
        for n in r["skipped"]:
            print(f"    = {n}（已存在，跳过）")
        print()
        note("下一步：ai-memory-setup vault use <该目录>，再 link apply")
        return 0

    err(f"未知的 vault 子命令：{sub}（可用：list / use / init / path）")
    return 1


def do_link(args) -> int:
    try:
        import setup_link as link
        import setup_vault as vault
    except ImportError as e:
        err(f"缺少 setup_link.py / setup_vault.py：{e}")
        return 1

    sub = (args.sub or "scan").lower()

    # 纯清单，不依赖本机是否装了任何客户端
    if sub in ("clients", "list-clients", "ls-clients"):
        allc = link.clients()
        hr(f"支持的 AI 客户端（{len(allc)} 个）")
        print(f"  {'客户端':<20}{'写入':<6}{'配置':<8}配置文件")
        for c in allc:
            mode = "自动" if c.auto else "手工"
            cre = "仅修改" if c.unverified else "可新建"
            print(f"  {c.name:<20}{mode:<6}{cre:<8}{c.paths[0]}")
        print()
        note("自动=可直接改配置（写前备份、写后回读）；手工=只给可粘贴片段")
        note("可新建=路径稳定，客户端装了但没配置时可直接生成；仅修改=路径有变体风险，只改已存在的文件")
        return 0

    install_dir = _guess_install_dir(args)
    data_dir = _guess_data_dir(args, install_dir)
    py, has_mcp = find_mcp_python()

    server = install_dir / "server.py"
    if not server.is_file():
        err(f"找不到 {server}")
        note("请先 install，或用 -i 指定程序目录")
        return 1
    if not py:
        warn("未找到 Python 解释器：写入的 command 可能不可用")
    elif not has_mcp:
        warn("所选解释器未检测到 mcp 模块 —— 写进配置后客户端会启动失败；"
             "请先 pip install \"mcp>=1.30,<2\"，或用 AI_MEMORY_PYTHON 指定解释器")

    target = link.build_target(install_dir, data_dir, py)

    hr(f"AI 工具连接状态  v{link.LINK_VERSION}")
    print(f"  目标程序  : {target.server_py}")
    print(f"  目标记忆库: {target.data_dir}")
    if data_dir is not None and data_dir.is_dir():
        info = vault.inspect(data_dir, "目标")
        print(f"               {info.md_entries} 条 md"
              + ("（空库）" if info.empty else ""))
        if info.empty:
            warn("目标记忆库是空的 —— 连上去会读到 0 条记忆")
    else:
        warn(f"目标记忆库目录不存在：{data_dir}")
    print(f"  Python    : {target.python or '未找到'}"
          + ("" if has_mcp else "  （未见 mcp 模块）"))
    print()

    statuses = link.scan(target)
    for line in link.format_status_lines(statuses):
        print(line)
    print()

    if sub in ("scan", "list", "ls"):
        todo = [s for s in statuses if s.actionable]
        if todo:
            note(f"{len(todo)} 个客户端可自动修正："
                 + "、".join(s.client.name for s in todo))
            print("  预演：ai-memory-setup link apply --dry-run")
            print("  执行：ai-memory-setup link apply")
        else:
            ok("没有需要修正的客户端")
        return 0

    if sub != "apply":
        err(f"未知的 link 子命令：{sub}（可用：scan / apply）")
        return 1

    only = [x for x in (args.client or "").split(",") if x.strip()] or None
    results = link.apply_all(
        statuses, target, only=only, dry_run=args.dry_run, force=args.force
    )
    hr("结果")
    for line in link.format_apply_lines(results):
        print(line)
    print()

    if any(r.action == "written" for r in results):
        note("配置已改：重启对应客户端（或重载 MCP）后生效")
    if any(r.action == "failed" for r in results):
        warn("有客户端写入失败，详情见上；原配置已回滚")

    manual = [s for s in statuses if not s.client.auto and s.installed]
    if manual:
        note("以下客户端结构特殊，需手工粘贴：")
        for s in manual:
            entry = link.merge_entry(s.entry, target, s.client)
            print(f"\n  --- {s.client.name}（{s.config_path}）---")
            body = link.snippet_for(s.client, entry)
            if body:
                print(body)
            if s.client.note:
                print(f"  说明：{s.client.note}")

    if args.dry_run:
        note("这是预演（--dry-run），没有写入任何文件")
    return 0


# --------------------------------------------------------------------------
# 管家菜单（B 方案：进菜单先给状态仪表盘，再进分区）
# --------------------------------------------------------------------------

_PY_CACHE: dict = {}


def _clone_args(args, **kw):
    ns = argparse.Namespace(**vars(args))
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _ask(prompt: str) -> str | None:
    """读一行。EOF 返回 None（=退出整个程序），Ctrl+C 返回 ''（=返回上一级）。"""
    try:
        return input(prompt).strip()
    except EOFError:
        return None
    except KeyboardInterrupt:
        print()
        return ""


def _clear_screen() -> None:
    if os.environ.get("AI_MEMORY_SETUP_NO_CLEAR"):
        return
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        pass


def _cached_mcp_python() -> tuple[str | None, bool]:
    """解释器探测要跑子进程，进过菜单就缓存，避免反复付出 0.5 秒。"""
    if "v" not in _PY_CACHE:
        _PY_CACHE["v"] = find_mcp_python()
    return _PY_CACHE["v"]


def _dashboard(args) -> dict:
    install_dir = _guess_install_dir(args)
    meta = read_meta(install_dir)
    data_dir = _guess_data_dir(args, install_dir)
    has_program = (install_dir / "server.py").is_file()
    # 别直接拿 meta 里的版本号当结论 —— 过一遍核验，才知道这个数字有几成可信
    vf = verify_install(install_dir)

    entries = 0
    try:
        import setup_vault as vault

        if data_dir is not None and data_dir.is_dir():
            entries = vault.inspect(data_dir, "dashboard").md_entries
    except Exception:
        pass

    linked = pending = configured = 0
    if has_program:
        try:
            import setup_link as link

            target = link.build_target(install_dir, data_dir, None)
            for s in link.scan(target):
                if s.state == link.ST_LINKED:
                    linked += 1
                if s.entry is not None:
                    configured += 1
                if s.actionable:
                    pending += 1
        except Exception:
            pass

    return {
        "install_dir": install_dir,
        "version": vf["version"],
        "installed_at": meta.get("installed_at") if meta else None,
        "data_dir": data_dir,
        "entries": entries,
        "has_program": has_program,
        "linked": linked,
        "configured": configured,
        "pending": pending,
        "verify": vf,
    }


def _render_dashboard(args) -> dict:
    d = _dashboard(args)
    _clear_screen()
    hr(f"ai-memory 管家  v{SETUP_VERSION}")
    print(f"  程序目录 : {d['install_dir']}"
          + ("" if d["has_program"] else "   <- 未见 server.py"))
    if d["version"]:
        tag = "" if d["verify"]["state"] == VERIFY_OK else \
            f"   <- {verify_state_label(d['verify'])}"
        print(f"  已装版本 : v{d['version']}  （{d['installed_at'] or '安装时间未登记'}）{tag}")
    elif d["has_program"]:
        print("  已装版本 : 无法确定（server.py 在，但无安装记录也无 CHANGELOG）")
    else:
        print("  已装版本 : 未安装")
    if d["data_dir"]:
        tail = f"{d['entries']} 条 md"
        if d["entries"] == 0:
            tail += "   <- 空库"
        print(f"  记忆库   : {d['data_dir']}    {tail}")
    else:
        print("  记忆库   : 未指定")
    if d["has_program"]:
        print(f"  AI 工具  : 已配 {d['configured']} 个，其中 {d['pending']} 个待修正")
    else:
        print("  AI 工具  : 装好程序后才能连接")
    print()
    print("  [1] 安装 / 更新          [2] 记忆库目录")
    print("  [3] AI 工具连接          [4] 环境自检")
    print("  [?] 帮助                 [0] 退出")
    print()
    return d


def _menu_install(args, d) -> None:
    _clear_screen()
    hr("安装 / 更新")
    print(f"  程序目录 : {d['install_dir']}")
    print(f"  记忆库   : {d['data_dir']}")
    ver = f"v{d['version']}" if d["version"] else (
        "无法确定（有 server.py 但无记录）" if d["has_program"] else "未安装"
    )
    print(f"  已装版本 : {ver}")
    st = d["verify"]["state"]
    if st in (VERIFY_DRIFT, VERIFY_PARTIAL):
        print(f"  核验结论 : {verify_state_label(d['verify'])}")
        for nt in d["verify"]["notes"][:3]:
            print(f"             {nt}")
    print()
    print("  [i] 安装          [u] 更新")
    print("  [p] 预演更新      [d] 预演安装        （预演不写任何文件）")
    print("  [v] 查看可用版本  （核验已装 + 列出远端 tag，不写盘）")
    pfx = "开" if getattr(args, "with_optional", False) else "关"
    wfx = "开" if getattr(args, "with_web", False) else "关"
    print(f"  [o] HTTP 传输层：{pfx}    [w] Web 控制台：{wfx}    （按一下切换，然后回本页）")
    print("  [回车] 返回")
    print()
    print("  提示：GitHub 被阻断时，命令行加 -s <本地目录> 走离线源")
    c = _ask("  请选择：")
    if not c:
        return

    kw = {"install_dir": str(d["install_dir"])}
    if d["data_dir"]:
        kw["data_dir"] = str(d["data_dir"])
    act = "update" if d["version"] else "install"
    key = c.lower()
    if key in ("o", "w"):
        field = "with_optional" if key == "o" else "with_web"
        new = not bool(getattr(args, field, False))
        setattr(args, field, new)
        if key == "w" and new and not getattr(args, "with_optional", False):
            # 控制台没有 HTTP 端点就是个空壳 —— 顺手把 HTTP 打开，用户仍可按 [o] 关掉
            args.with_optional = True
        _menu_install(args, d)
        return
    if key == "v":
        do_versions(_clone_args(args, install_dir=str(d["install_dir"]),
                               data_dir=str(d["data_dir"]) if d["data_dir"] else None))
        _ask("\n  按回车返回…")
        return
    if key == "i":
        do_install_or_update(_clone_args(args, dry_run=False, **kw), "install")
    elif key == "u":
        do_install_or_update(_clone_args(args, dry_run=False, **kw), "update")
    elif key == "p":
        do_install_or_update(_clone_args(args, dry_run=True, **kw), act)
    elif key == "d":
        do_install_or_update(_clone_args(args, dry_run=True, **kw), "install")
    else:
        return
    _ask("\n  按回车返回…")


def _menu_vault(args, d) -> None:
    while True:
        try:
            import setup_vault as vault
        except ImportError as e:
            err(f"缺少 setup_vault.py：{e}")
            _ask("\n  按回车返回…")
            return

        _clear_screen()
        hr("记忆库目录")
        cur = vault.get_current()
        items = vault.scan(d["install_dir"] if d["install_dir"].is_dir() else None)
        for line in vault.format_lines(items, cur):
            print(line)
        print()
        print("  [数字] 设为当前库     [i] 在空目录新建库")
        print("  [r] 重新扫描          [回车] 返回")
        c = _ask("  请选择：")
        if c is None or c == "":
            return
        key = c.lower()

        if key == "r":
            continue

        if key == "i":
            p = _ask("  新库路径（例如 D:/新记忆库）：")
            if not p:
                continue
            try:
                r = vault.init_vault(Path(p), d["install_dir"], force=False)
            except Exception as e:  # noqa: BLE001
                err(f"{type(e).__name__}: {e}")
            else:
                ok(f"已播种：{r['path']}（新增 {len(r['created'])} 个文件）")
            _ask("\n  按回车继续…")
            continue

        if c.isdigit():
            idx = int(c)
            if not 1 <= idx <= len(items):
                warn("序号超出范围")
                _ask("\n  按回车继续…")
                continue
            target_vault = items[idx - 1]
            try:
                r = vault.set_current(target_vault.path, d["install_dir"])
            except Exception as e:  # noqa: BLE001
                err(f"{type(e).__name__}: {e}")
                _ask("\n  按回车继续…")
                continue
            ok(f"当前库：{r['path']}")
            if target_vault.empty:
                warn("这个目录是空的，客户端连上去会读到 0 条记忆")
            a = _ask("  要让已连接的 AI 工具同步指向这个库吗？[y/N] ")
            if a and a.lower().startswith("y"):
                _apply_from_menu(args, d, only=None, dry_run=False)
            _ask("\n  按回车继续…")
            continue

        warn("无效输入")
        _ask("\n  按回车继续…")


def _apply_from_menu(args, d, *, only=None, dry_run: bool, ask: bool = True) -> None:
    """菜单里执行写入：算目标 → 确认 → 写 → 打印结果。"""
    try:
        import setup_link as link
    except ImportError as e:
        err(f"缺少 setup_link.py：{e}")
        return

    py, has_mcp = _cached_mcp_python()
    if not py:
        warn("未找到 Python 解释器")
    elif not has_mcp:
        warn("所选解释器未见 mcp 模块 —— 客户端可能启动失败")

    target = link.build_target(d["install_dir"], d["data_dir"], py)
    statuses = link.scan(target)

    picked = statuses
    if only is not None:
        picked = [s for s in statuses if s.client.id in only]
    picked = [s for s in picked if s.actionable]
    if not picked:
        note("没有需要修正的客户端")
        return

    if not dry_run and ask:
        print(f"\n  将修改 {len(picked)} 个配置文件（改前自动备份）：")
        for s in picked:
            print(f"    - {s.client.name:<16} {s.config_path}")
        a = _ask("  确认？[y/N] ")
        if not (a and a.lower().startswith("y")):
            note("已取消")
            return

    results = link.apply_all(picked, target, dry_run=dry_run)
    hr("结果")
    for line in link.format_apply_lines(results):
        print(line)
    if dry_run:
        note("这是预演，没有写入任何文件")
    elif any(r.action == "written" for r in results):
        note("请重启对应客户端后生效")


def _menu_link(args, d) -> None:
    try:
        import setup_link as link
    except ImportError as e:
        err(f"缺少 setup_link.py：{e}")
        _ask("\n  按回车返回…")
        return

    while True:
        _clear_screen()
        hr("AI 工具连接")
        py, has_mcp = _cached_mcp_python()
        target = link.build_target(d["install_dir"], d["data_dir"], py)
        print(f"  目标程序  : {target.server_py}")
        print(f"  目标记忆库: {target.data_dir}")
        print(f"  解释器    : {target.python or '未找到'}"
              + ("" if has_mcp else "   <- 未见 mcp 模块"))
        print()

        statuses = link.scan(target)
        live = [s for s in statuses if s.state != link.ST_NOT_INSTALLED]
        gone = [s for s in statuses if s.state == link.ST_NOT_INSTALLED]
        for i, s in enumerate(live, 1):
            tag = "  [路径未验证]" if s.client.unverified else ""
            print(f"  {i}. {s.client.name:<20}{s.label:<10} {s.config_path}{tag}")
            for df in s.diffs:
                print(f"       差异：{df}")
            for nt in s.notes:
                print(f"       提示：{nt}")
            if s.state == link.ST_ERROR:
                print(f"       错误：{s.error}")
        if gone:
            print("\n  " + "、".join(s.client.name for s in gone) + " —— 未安装")
        print()
        print("  [a] 全部写入      [p] 预演（不落盘）")
        print("  [数字] 只处理某个  [回车] 返回")
        c = _ask("  请选择：")
        if c is None or c == "":
            return
        key = c.lower()

        if key == "a":
            _apply_from_menu(args, d, dry_run=False)
        elif key == "p":
            _apply_from_menu(args, d, dry_run=True)
        elif c.isdigit():
            idx = int(c)
            if not 1 <= idx <= len(live):
                warn("序号超出范围")
            else:
                s = live[idx - 1]
                if not s.client.auto:
                    # 结构特殊的客户端不自动写，直接给可粘贴的片段
                    entry = link.merge_entry(s.entry, target, s.client)
                    print(f"\n  --- {s.client.name} 需手工粘贴（{s.config_path}）---")
                    body = link.snippet_for(s.client, entry)
                    if body:
                        print(body)
                    if s.client.note:
                        note(s.client.note)
                else:
                    _apply_from_menu(args, d, only=[s.client.id], dry_run=False)
        else:
            warn("无效输入")
        _ask("\n  按回车继续…")


def menu(args) -> int:
    """无参数启动时的管家菜单。返回退出码。"""
    while True:
        d = _render_dashboard(args)
        c = _ask("  请选择：")
        if c is None:
            print()
            return 0
        key = c.lower()

        if key in ("", "0", "q", "quit", "exit"):
            return 0
        if key == "1":
            _menu_install(args, d)
        elif key == "2":
            _menu_vault(args, d)
        elif key == "3":
            _menu_link(args, d)
        elif key == "4":
            _clear_screen()
            do_check(_clone_args(
                args,
                install_dir=str(d["install_dir"]),
                data_dir=str(d["data_dir"]) if d["data_dir"] else None,
            ))
            _ask("\n  按回车返回…")
        elif key in ("?", "h", "help"):
            # argparse 打印帮助后会 SystemExit，菜单里必须拦下，否则直接退程序
            try:
                parse_setup_args(["--help"])
            except SystemExit:
                pass
            _ask("\n  按回车返回…")
        else:
            warn(f"无效输入：{c}")


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
        if args.action == "versions":
            return do_versions(args)
        if args.action == "vault":
            return do_vault(args)
        if args.action == "link":
            return do_link(args)
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
