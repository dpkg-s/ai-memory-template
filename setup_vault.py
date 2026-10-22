#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆库（vault）目录管理 —— ai-memory-setup 的 vault 子模块（v1.1.0）

职责边界
--------
本模块**只认目录**，不碰安装、不碰客户端配置。它回答三个问题：

1. 这台机器上，哪些目录**像**记忆库？（``scan``）
2. 用户想用哪一个当作"当前库"？（``set_current``）
3. 空目录怎么变成一个能用的库？（``init_vault``）

为什么"像"要打分而不是判一个标志位
----------------------------------
记忆库没有注册表项，也没有固定的文件名清单（用户会自建子目录、加 .git、
用 Obsidian 打开）。所以判定用加权打分：``记忆索引.md`` 是模板播种的固定
产物，权重最高；md 条目数、.git 存在、索引库存在依次递减。打分同时用于
排序，让最像的那一个排在最前面，而不是按路径字母序。

候选来源（按可信度排序，去重后统一打分）
----------------------------------------
    1. 进程环境变量 ``AI_MEMORY_DIR``      —— 用户显式指定，最可信
    2. ``<install-dir>/.ai-memory-install.json`` 的 data_dir —— 安装器记录
    3. 已连接 AI 客户端配置里的 AI_MEMORY_DIR / server.py 父目录 —— 实际在用
    4. 本模块上次记录的 last_used
    5. ``~/ai-memory``                     —— server.py 的默认值
    6. 各盘符根下的 ``ai记忆`` / ``ai-memory`` —— 中文用户常见习惯

关于"当前库"为什么单独存一份 state
----------------------------------
安装器的 ``.ai-memory-install.json`` 只有"装过"才存在。但用户完全可能先
用 vault 指定目录、再决定装程序（甚至只想让已有客户端换库）。因此当前库
记在 ``~/.ai-memory-setup/vaults.json``，**装不装都能用**；若安装器元数据
存在，``set_current`` 会顺手把 data_dir 同步进去，避免两处打架。

仅使用标准库。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

VAULT_VERSION = "1.2.0"

# 模板播种时落在记忆库根、且被 install.ps1 / install.sh 视为"已安装"的标志文件
INDEX_MARK = "记忆索引.md"

# 统计条目时要剪掉的目录（不是用户记忆正文）
SKIP_DIRS = {
    ".archive",
    ".trash",
    ".git",
    ".obsidian",
    ".ai-memory-backup",
    "node_modules",
    "__pycache__",
}

# 状态文件位置（与安装目录解耦）
SETUP_HOME = Path.home() / ".ai-memory-setup"
STATE_FILE = SETUP_HOME / "vaults.json"

META_NAME = ".ai-memory-install.json"

# 盘符浅扫时匹配的目录名（大小写不敏感比较）
COMMON_VAULT_NAMES = ("ai记忆", "ai-memory", "ai_memory", "AI记忆", "aimemory")

# 浅扫盘符范围：C..H 覆盖绝大多数机器，避免枚举光驱/网络盘卡住
DRIVE_LETTERS = "CDEFGH"


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class VaultInfo:
    """一个候选记忆库的体检结果。"""

    path: Path
    source: str = ""
    md_entries: int = 0
    has_index: bool = False
    has_git: bool = False
    index_dbs: int = 0
    last_write: float = 0.0
    program: Path | None = None       # 同目录下的 ai-memory/server.py（若有）
    empty: bool = False

    @property
    def score(self) -> int:
        """像记忆库的程度。排序与推荐都用它。"""
        s = 0
        if self.has_index:
            s += 40
        s += min(self.md_entries, 60)
        if self.has_git:
            s += 10
        if self.index_dbs:
            s += 10
        if self.program is not None:
            s += 15
        return s

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "source": self.source,
            "md_entries": self.md_entries,
            "has_index": self.has_index,
            "has_git": self.has_git,
            "index_dbs": self.index_dbs,
            "last_write": self.last_write,
            "program": str(self.program) if self.program else None,
            "score": self.score,
        }


# --------------------------------------------------------------------------
# 状态文件
# --------------------------------------------------------------------------

def read_state() -> dict:
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict) -> None:
    SETUP_HOME.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, STATE_FILE)


def get_current() -> Path | None:
    """返回上次指定的当前库（不校验是否还存在）。"""
    p = read_state().get("last_used")
    if not p:
        return None
    try:
        return Path(p)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 候选收集
# --------------------------------------------------------------------------

def _norm(path: Path) -> str:
    try:
        return str(path.resolve()).replace("\\", "/").rstrip("/").lower()
    except OSError:
        return str(path).replace("\\", "/").rstrip("/").lower()


def _meta_data_dir(install_dir: Path | None) -> Path | None:
    if not install_dir:
        return None
    p = Path(install_dir) / META_NAME
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        dd = data.get("data_dir")
        return Path(dd) if dd else None
    except (OSError, ValueError):
        return None


def _shallow_drive_scan() -> list[Path]:
    """各盘符根下找常见命名的记忆库目录。只看一层，不做递归。"""
    found: list[Path] = []
    wanted = {n.lower() for n in COMMON_VAULT_NAMES}
    for letter in DRIVE_LETTERS:
        root = Path(f"{letter}:/")
        try:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                try:
                    if child.is_dir() and child.name.lower() in wanted:
                        found.append(child)
                except OSError:
                    continue
        except OSError:
            continue
    return found


def collect_candidates(install_dir: Path | None = None) -> list[tuple[Path, str]]:
    """按可信度返回 (路径, 来源) 列表，未去重。"""
    out: list[tuple[Path, str]] = []

    env_dir = os.environ.get("AI_MEMORY_DIR")
    if env_dir:
        out.append((Path(env_dir), "env:AI_MEMORY_DIR"))

    md = _meta_data_dir(install_dir)
    if md:
        out.append((md, "安装元数据"))

    # 已连接客户端反推 —— 延迟导入，避免与 setup_link 形成循环
    try:
        from setup_link import discover_hints  # noqa: WPS433

        for p in discover_hints():
            out.append((p, "客户端配置"))
    except Exception:
        pass

    last = get_current()
    if last:
        out.append((last, "上次使用"))

    out.append((Path.home() / "ai-memory", "默认路径"))

    # install_dir 的父目录（程序常放在库内的 ai-memory/ 子目录）
    if install_dir:
        try:
            out.append((Path(install_dir).resolve().parent, "程序父目录"))
        except OSError:
            pass

    for p in _shallow_drive_scan():
        out.append((p, "盘符扫描"))

    return out


# --------------------------------------------------------------------------
# 体检
# --------------------------------------------------------------------------

def inspect(path: Path, source: str = "") -> VaultInfo:
    """读取一个目录的记忆库特征。不存在时返回空 info（empty=True）。"""
    info = VaultInfo(path=Path(path), source=source)
    try:
        if not info.path.is_dir():
            info.empty = True
            return info
    except OSError:
        info.empty = True
        return info

    try:
        info.has_index = (info.path / INDEX_MARK).is_file()
        info.has_git = (info.path / ".git").exists()
    except OSError:
        pass

    # 索引库：可能在库根，也可能在库内的 ai-memory/ 程序目录
    try:
        candidates = list(info.path.glob("memory_index_*.db"))
        prog = info.path / "ai-memory" / "server.py"
        if prog.is_file():
            info.program = prog
            candidates += list((info.path / "ai-memory").glob("memory_index_*.db"))
        elif (info.path / "server.py").is_file():
            info.program = info.path / "server.py"
            candidates += list(info.path.glob("memory_index_*.db"))
        info.index_dbs = len(candidates)
    except OSError:
        pass

    count = 0
    newest = 0.0
    try:
        for dirpath, dirnames, filenames in os.walk(info.path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if not fn.lower().endswith(".md"):
                    continue
                count += 1
                try:
                    mt = os.stat(os.path.join(dirpath, fn)).st_mtime
                    if mt > newest:
                        newest = mt
                except OSError:
                    continue
    except OSError:
        pass

    info.md_entries = count
    info.last_write = newest
    info.empty = not count and not info.has_index
    return info


def scan(install_dir: Path | None = None) -> list[VaultInfo]:
    """扫描并去重，按打分降序返回。"""
    seen: dict[str, VaultInfo] = {}
    for path, source in collect_candidates(install_dir):
        try:
            key = _norm(Path(path))
        except (TypeError, ValueError):
            continue
        if key in seen:
            # 同一目录被多个来源命中：保留更可信的来源标签（先到先得）
            continue
        info = inspect(Path(path), source)
        seen[key] = info

    current = get_current()
    cur_key = _norm(current) if current else None
    items = list(seen.values())

    def sort_key(v: VaultInfo):
        return (0 if cur_key and _norm(v.path) == cur_key else 1, -v.score, _norm(v.path))

    items.sort(key=sort_key)
    return items


def pick_best(install_dir: Path | None = None) -> VaultInfo | None:
    """挑一个最像记忆库的目录；没有 md 也没有索引 md 的目录不算。"""
    for v in scan(install_dir):
        if v.has_index or v.md_entries > 0:
            return v
    return None


# --------------------------------------------------------------------------
# 切换当前库
# --------------------------------------------------------------------------

def set_current(path: Path, install_dir: Path | None = None) -> dict:
    """把 path 设为当前库。同步安装元数据（若存在），返回变更摘要。

    不做目录内容校验之外的动作：不复制、不删除、不改环境变量。
    环境变量需用户自己设或用 ``link apply`` 写进客户端配置。
    """
    target = Path(path).expanduser()
    if not target.is_dir():
        raise NotADirectoryError(f"不是目录或不存在：{target}")
    target = target.resolve()

    state = read_state()
    state["last_used"] = str(target)
    known = set(state.get("known") or [])
    known.add(str(target))
    state["known"] = sorted(known)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_state(state)

    result = {"path": str(target), "meta_updated": False, "meta_path": None}

    # 同步安装元数据，避免 install/update 与实际使用目录打架
    if install_dir:
        mp = Path(install_dir) / META_NAME
        if mp.is_file():
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                if data.get("data_dir") != str(target):
                    data["data_dir"] = str(target)
                    mp.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    result["meta_updated"] = True
                result["meta_path"] = str(mp)

    return result


# --------------------------------------------------------------------------
# 初始化空库
# --------------------------------------------------------------------------

def init_vault(
    path: Path,
    install_dir: Path | None = None,
    *,
    force: bool = False,
) -> dict:
    """把一个空目录播种成可用记忆库。

    播种内容与安装器一致：``template/*.md`` 平铺到库根、``rules/`` 保留子目录。
    目标目录若已有 md 或索引 md，默认拒绝（避免把用户的库当空目录塞模板）。
    """
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    target = target.resolve()

    existing_md = [p for p in target.glob("*.md") if p.is_file()]
    if (existing_md or (target / INDEX_MARK).exists()) and not force:
        raise FileExistsError(
            f"目标目录已有 {len(existing_md)} 个 md 文件，看起来不是空库；如确认要播种请加 --force"
        )

    sources: list[Path] = []
    if install_dir:
        sources.append(Path(install_dir))
    sources.append(Path(__file__).resolve().parent)

    src_root = next((s for s in sources if (s / "template").is_dir()), None)
    if src_root is None:
        raise FileNotFoundError(
            "找不到 template/ 目录：无法播种。请用 -i 指定包含 template/ 的程序目录，"
            "或先执行 install。"
        )

    created: list[str] = []
    skipped: list[str] = []

    tpl = src_root / "template"
    for f in sorted(tpl.glob("*.md")):
        dst = target / f.name
        if dst.exists():
            skipped.append(f.name)
            continue
        shutil.copy2(f, dst)
        created.append(f.name)

    rules_src = src_root / "rules"
    if rules_src.is_dir():
        rules_dst = target / "rules"
        rules_dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(rules_src.glob("*.md")):
            dst = rules_dst / f.name
            if dst.exists():
                skipped.append(f"rules/{f.name}")
                continue
            shutil.copy2(f, dst)
            created.append(f"rules/{f.name}")

    return {
        "path": str(target),
        "source": str(src_root),
        "created": created,
        "skipped": skipped,
    }


# --------------------------------------------------------------------------
# 人读格式化（CLI 层可直接用；B 阶段菜单复用同一份）
# --------------------------------------------------------------------------

def format_size_ago(ts: float) -> str:
    if not ts:
        return "—"
    delta = max(0, time.time() - ts)
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def format_lines(items: list[VaultInfo], current: Path | None = None) -> list[str]:
    if not items:
        return ["  （没有发现任何候选目录）"]
    cur_key = _norm(current) if current else None
    lines = []
    for i, v in enumerate(items, 1):
        mark = "  <- 当前" if cur_key and _norm(v.path) == cur_key else ""
        if v.empty:
            status = "空目录"
        else:
            bits = [f"{v.md_entries} 条"]
            if v.has_index:
                bits.append("索引 md")
            if v.has_git:
                bits.append("git")
            if v.index_dbs:
                bits.append(f"{v.index_dbs} 索引库")
            bits.append(format_size_ago(v.last_write))
            status = " · ".join(bits)
        prog = f"  程序：{v.program}" if v.program else ""
        lines.append(f"  {i}) {v.path}{mark}")
        lines.append(f"     {status}   [来源：{v.source or '未知'}]{prog}")
    return lines
