#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描本机 AI 工具并把它们连到同一份记忆库 —— ai-memory-setup 的 link 子模块

为什么需要它
------------
记忆库装好之后，真正的麻烦不是"装"，而是"每个 AI 客户端各配一遍"。手工配
的典型后果是：三份配置文件指向两份不同的 server.py 副本，其中一份还忘了写
``AI_MEMORY_DIR`` —— 于是两个客户端读写的是两个库，而症状是"单端正常、跨端
读不到"，极难排查。本模块把这件事收敛成「扫描 → 看清状态 → 批量写入」。

设计取舍
--------
1. **只改该改的**：判定"漂移"只看两个字段 —— ``args[0]``（server.py 路径）
   与 ``env.AI_MEMORY_DIR``（记忆库路径）。解释器（command）不同只提示、
   不参与漂移判定，因为换 Python 未必是错，强改会覆盖用户的选择。

2. **merge 而非 replace**：条目里的 ``description``、``env`` 中的其他键
   （例如 ``AI_MEMORY_DIR_POLICY=auto``）一律保留。只增改上面两个字段，
   外加必要时补 ``type``。否则会把用户辛苦调的配置抹平。

3. **结构特殊的客户端不强写**：Cherry Studio 用数组、VS Code 用 ``servers``
   键，与主流 ``mcpServers`` 不同。本版只检测 + 给粘贴片段，自动写入留到
   结构验证之后。宁可少做，不要写坏别人的配置文件。

4. **TOML 只做段落级原地改**：Codex 的 config.toml 里还有
   ``[mcp_servers.ai-memory.tools.*]`` 子段（工具审批模式），整文件重写会
   丢。故按表头切段：删掉主段与 env 段、在原位置插回新块，其余原样。

5. **写前必备份、写后必回读**：备份落在同目录 ``<文件名>.bak-<时间戳>``，
   写完用 ``json.loads`` / ``tomllib.loads`` 回读校验，校验失败即回滚。

仅使用标准库（``tomllib`` 需要 Python 3.11+；仅用于读，写是文本操作）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib  # type: ignore
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore

LINK_VERSION = "1.2.0"
SERVER_NAME = "ai-memory"
ENV_VAULT = "AI_MEMORY_DIR"

# 状态码
ST_NOT_INSTALLED = "not_installed"
ST_NO_CONFIG = "no_config"
ST_NOT_LINKED = "not_linked"
ST_LINKED = "linked"
ST_AMBIGUOUS = "ambiguous"
ST_DRIFT = "drift"
ST_BROKEN = "broken"
ST_ERROR = "error"
ST_MANUAL = "manual"

STATE_LABEL = {
    ST_NOT_INSTALLED: "未安装",
    ST_NO_CONFIG: "待创建配置",
    ST_NOT_LINKED: "已装未连",
    ST_LINKED: "已连接",
    ST_AMBIGUOUS: "未指定库",
    ST_DRIFT: "路径漂移",
    ST_BROKEN: "指向失效",
    ST_ERROR: "读取失败",
    ST_MANUAL: "需手工",
}

MANUAL_NOTE = "结构非 mcpServers，需手工粘贴"


# --------------------------------------------------------------------------
# 客户端清单
# --------------------------------------------------------------------------

def _home() -> Path:
    return Path.home()


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))


def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    kind: str                                   # json | toml
    layout: str                                 # mcpServers | servers | cherry
    paths: tuple[Path, ...]                     # 候选配置路径（按优先级）
    probe: tuple[Path, ...]                     # 判断"是否装了"的目录
    auto: bool = True                           # 是否支持自动写入
    requires_type: bool = False                 # 条目是否需要 "type": "stdio"
    note: str = ""
    unverified: bool = False                    # 路径未在本机验证：只改已存在的文件，绝不新建
    snippet: str = "json"                       # 手工片段风格：json / toml / zed / opencode / none


def clients() -> list[Client]:
    """客户端清单。

    排序原则：本机/社区实测过的在前，官方文档可查但未在本机验证的在后，结构特殊的最后。
    ``unverified=True`` 的条目**只在配置文件已存在时写入**（文件存在本身就是路径正确的
    证据），路径猜错时它只会显示为「未安装」，不会凭空造出一个客户端不认识的文件。
    """
    h, ad = _home(), _appdata()
    gs = ad / "Code" / "User" / "globalStorage"
    return [
        # ---- 本机实测过 ----
        Client(
            id="workbuddy", name="WorkBuddy", kind="json", layout="mcpServers",
            paths=(h / ".workbuddy" / "mcp.json",),
            probe=(h / ".workbuddy",),
            requires_type=True,
        ),
        Client(
            id="claude_code", name="Claude Code", kind="json", layout="mcpServers",
            paths=(h / ".claude.json",),
            probe=(h / ".claude", h / ".claude.json"),
        ),
        Client(
            id="claude_desktop", name="Claude Desktop", kind="json", layout="mcpServers",
            paths=(ad / "Claude" / "claude_desktop_config.json",),
            probe=(ad / "Claude",),
        ),
        Client(
            id="codex", name="Codex", kind="toml", layout="mcp_servers",
            paths=(h / ".codex" / "config.toml",),
            probe=(h / ".codex",),
        ),
        # ---- 路径按官方文档约定（未在本机实测，但路径稳定，允许创建配置）----
        Client(
            id="cursor", name="Cursor", kind="json", layout="mcpServers",
            paths=(h / ".cursor" / "mcp.json", ad / "Cursor" / "User" / "mcp.json"),
            probe=(h / ".cursor", ad / "Cursor"),
        ),
        Client(
            id="windsurf", name="Windsurf", kind="json", layout="mcpServers",
            paths=(h / ".codeium" / "windsurf" / "mcp_config.json",),
            probe=(h / ".codeium" / "windsurf",),
        ),
        Client(
            id="gemini", name="Gemini CLI", kind="json", layout="mcpServers",
            paths=(h / ".gemini" / "settings.json",),
            probe=(h / ".gemini",),
        ),
        Client(
            id="qwen", name="Qwen Code", kind="json", layout="mcpServers",
            paths=(h / ".qwen" / "settings.json",),
            probe=(h / ".qwen",),
        ),
        Client(
            id="cline", name="Cline", kind="json", layout="mcpServers",
            paths=(gs / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",),
            probe=(gs / "saoudrizwan.claude-dev",),
        ),
        Client(
            id="roo", name="Roo Code", kind="json", layout="mcpServers",
            paths=(gs / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json",),
            probe=(gs / "rooveterinaryinc.roo-cline",),
        ),
        Client(
            id="kilo", name="Kilo Code", kind="json", layout="mcpServers",
            paths=(gs / "kilocode.kilo-code" / "settings" / "cline_mcp_settings.json",),
            probe=(gs / "kilocode.kilo-code",),
        ),
        Client(
            id="kiro", name="Kiro", kind="json", layout="mcpServers",
            paths=(h / ".kiro" / "settings" / "mcp.json",),
            probe=(h / ".kiro",),
        ),
        Client(
            id="lmstudio", name="LM Studio", kind="json", layout="mcpServers",
            paths=(h / ".lmstudio" / "mcp.json",),
            probe=(h / ".lmstudio",),
        ),
        Client(
            id="5ire", name="5ire", kind="json", layout="mcpServers",
            paths=(ad / "5ire" / "mcp.json",),
            probe=(ad / "5ire",),
        ),
        # ---- 结构特殊：只给片段，不自动写 ----
        Client(
            id="trae", name="Trae", kind="json", layout="mcpServers",
            paths=(ad / "Trae" / "User" / "mcp.json",),
            probe=(ad / "Trae",),
            auto=False, unverified=True,
            note="路径与结构未在本机验证，建议手工粘贴",
        ),
        Client(
            id="qoder", name="Qoder", kind="json", layout="mcpServers",
            paths=(ad / "Qoder" / "User" / "mcp.json",),
            probe=(ad / "Qoder",),
            auto=False, unverified=True,
            note="路径与结构未在本机验证，建议手工粘贴",
        ),
        Client(
            id="cherry", name="Cherry Studio", kind="json", layout="cherry",
            paths=(ad / "CherryStudio" / "config.json",),
            probe=(ad / "CherryStudio",),
            auto=False, snippet="cherry", note=MANUAL_NOTE + "（mcp.servers 数组）",
        ),
        Client(
            id="vscode", name="VS Code / Copilot", kind="json", layout="servers",
            paths=(ad / "Code" / "User" / "mcp.json",),
            probe=(ad / "Code" / "User", ad / "Code" / "User" / "globalStorage" / "github.copilot-chat"),
            auto=False, snippet="vscode", note=MANUAL_NOTE + "（servers 键）",
        ),
        Client(
            id="zed", name="Zed", kind="json", layout="context_servers",
            paths=(ad / "Zed" / "settings.json",),
            probe=(ad / "Zed",),
            auto=False, unverified=True, snippet="zed",
            note="Zed 用 context_servers，且 command 是对象结构，需手工粘贴",
        ),
        Client(
            id="continue", name="Continue", kind="json", layout="mcpServers",
            paths=(h / ".continue" / "config.json",),
            probe=(h / ".continue",),
            auto=False, unverified=True, snippet="none",
            note="新版 Continue 用 config.yaml（本工具不解析 YAML），请按片段手工添加",
        ),
        Client(
            id="opencode", name="OpenCode", kind="json", layout="opencode",
            paths=(h / ".config" / "opencode" / "opencode.json",),
            probe=(h / ".config" / "opencode",),
            auto=False, unverified=True, snippet="opencode",
            note="OpenCode 的 mcp 段结构不同（type=local、command 为数组），需手工粘贴",
        ),
    ]


# --------------------------------------------------------------------------
# 目标（要把客户端连到哪里）
# --------------------------------------------------------------------------

@dataclass
class Target:
    python: str | None
    server_py: Path
    data_dir: Path | None = None

    def as_dict(self) -> dict:
        return {
            "python": self.python,
            "server_py": str(self.server_py),
            "data_dir": str(self.data_dir) if self.data_dir else None,
        }


def build_target(
    install_dir: Path,
    data_dir: Path | None = None,
    python: str | None = None,
) -> Target:
    return Target(
        python=python,
        server_py=Path(install_dir) / "server.py",
        data_dir=Path(data_dir) if data_dir else None,
    )


# --------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------

def _norm_path(value: object) -> str:
    if value is None:
        return ""
    s = str(value).strip().strip('"').strip("'")
    s = s.replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    return s.rstrip("/").lower()


@dataclass
class ClientStatus:
    client: Client
    config_path: Path | None = None
    exists: bool = False
    installed: bool = False
    state: str = ST_NOT_INSTALLED
    entry: dict | None = None
    diffs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def label(self) -> str:
        return STATE_LABEL.get(self.state, self.state)

    @property
    def actionable(self) -> bool:
        return self.client.auto and self.state in (
            ST_NO_CONFIG, ST_NOT_LINKED, ST_AMBIGUOUS, ST_DRIFT, ST_BROKEN
        )


def resolve_config_path(client: Client) -> tuple[Path, bool]:
    """返回 (要用的配置文件路径, 是否已存在)。都不存在时返回第一个候选。"""
    for p in client.paths:
        if p.is_file():
            return p, True
    return client.paths[0], False


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------

def _read_text_raw(path: Path) -> str:
    """按字节读再解码 —— 不用 read_text 是为了保留 CRLF（它会被通用换行吞掉）。"""
    return path.read_bytes().decode("utf-8-sig")


def _posix(p: object) -> str:
    """统一写正斜杠：与既有配置一致，且免去 JSON/TOML 里的反斜杠转义噪音。"""
    return str(p).replace("\\", "/")


def _read_json(path: Path) -> dict:
    raw = _read_text_raw(path)
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层不是对象")
    return data


def _read_toml(path: Path) -> dict:
    if tomllib is None:
        raise RuntimeError("当前 Python 不支持 tomllib（需 3.11+），无法读取 TOML 配置")
    raw = _read_text_raw(path)
    if not raw.strip():
        return {}
    return tomllib.loads(raw)


def read_entry(client: Client, path: Path) -> dict | None:
    """从客户端配置里取出 ai-memory 条目；不存在返回 None。"""
    if client.kind == "toml":
        data = _read_toml(path)
        servers = data.get("mcp_servers") or {}
        entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    else:
        data = _read_json(path)
        if client.layout == "cherry":
            servers = ((data.get("mcp") or {}).get("servers")) or []
            entry = None
            if isinstance(servers, list):
                entry = next(
                    (s for s in servers if isinstance(s, dict) and s.get("name") == SERVER_NAME),
                    None,
                )
        else:
            servers = data.get(client.layout) or {}
            entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    if entry is None:
        return None
    if not isinstance(entry, dict):
        return None
    out = dict(entry)
    out["args"] = list(entry.get("args") or [])
    env = entry.get("env")
    out["env"] = dict(env) if isinstance(env, dict) else {}
    return out


def _dir_has_md(path: Path) -> bool:
    """顶层有 md 才算记忆库候选 —— 程序目录本身不该被当成库。"""
    try:
        if not path.is_dir():
            return False
        for p in path.glob("*.md"):
            if p.is_file():
                return True
    except OSError:
        return False
    return False


def discover_hints() -> list[Path]:
    """从所有能找到的客户端配置里反推"可能正在用的记忆库目录"。

    供 vault 模块做候选来源；本函数绝不抛异常（宁可少给一个候选）。
    """
    hints: list[Path] = []
    for c in clients():
        try:
            path, exists = resolve_config_path(c)
            if not exists:
                continue
            entry = read_entry(c, path)
            if not entry:
                continue
            env_dir = entry.get("env", {}).get(ENV_VAULT)
            if env_dir:
                hints.append(Path(str(env_dir)))
            for a in entry.get("args") or []:
                p = Path(str(a))
                if p.name.lower() != "server.py":
                    continue
                # 两种常见布局：<vault>/ai-memory/server.py 与 <vault>/server.py
                for cand in (p.parent.parent, p.parent):
                    if _dir_has_md(cand):
                        hints.append(cand)
                        break
                break
        except Exception:
            continue
    # 去重保序
    seen: set[str] = set()
    out: list[Path] = []
    for p in hints:
        k = _norm_path(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


# --------------------------------------------------------------------------
# 扫描与状态判定
# --------------------------------------------------------------------------

def scan(target: Target, client_list: list[Client] | None = None) -> list[ClientStatus]:
    """扫描全部客户端，返回状态列表（只读，不写任何文件）。

    ``client_list`` 仅用于测试注入；生产调用留空即用内置清单。
    """
    results: list[ClientStatus] = []
    for c in (client_list if client_list is not None else clients()):
        st = ClientStatus(client=c)
        path, exists = resolve_config_path(c)
        st.config_path = path
        st.exists = exists
        st.installed = any(_safe_exists(p) for p in c.probe)

        if not exists:
            if not st.installed:
                st.state = ST_NOT_INSTALLED
            elif c.auto and not c.unverified:
                st.state = ST_NO_CONFIG
            else:
                st.state = ST_MANUAL
                if c.unverified:
                    st.notes.append("配置不存在，路径未在本机验证 —— 不自动创建")
            results.append(st)
            continue

        try:
            entry = read_entry(c, path)
        except Exception as e:  # noqa: BLE001
            st.state = ST_ERROR
            st.error = f"{type(e).__name__}: {e}"
            results.append(st)
            continue

        if entry is None:
            st.state = ST_NOT_LINKED if c.auto else ST_MANUAL
            results.append(st)
            continue

        st.entry = entry
        st.state, st.diffs, st.notes = _judge(entry, target, c)
        results.append(st)
    return results


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False


def _judge(entry: dict, target: Target, client: Client) -> tuple[str, list[str], list[str]]:
    """返回 (状态, 差异列表, 提示列表)。

    差异分三类，因为严重程度不同：
      - server 路径不一致 → 真的连错程序，属漂移
      - AI_MEMORY_DIR 有值但不一致 → 真的连错库，属漂移
      - AI_MEMORY_DIR 缺失 → 只是"没显式指定"，可能由外层环境变量供上，
        降级为「未指定库」，仍可一键补写，但不算错误。

    ``auto``（能否代写）只在「确实需要动手」时才影响结论：能代写的报「待确认」，
    不能代写的报「需手工」。**配置完全正确时一律报已连接** —— 否则 auto=False 的
    客户端配得再对也永远显示「需手工」。
    """
    diffs: list[str] = []
    notes: list[str] = []
    server_diff = False
    env_diff = False
    env_missing = False

    args = entry.get("args") or []
    server_arg = args[0] if args else None
    if server_arg is None:
        server_diff = True
        diffs.append("缺少 server.py 参数")
    elif not _safe_exists(Path(str(server_arg))):
        return ST_BROKEN, [f"server.py 不存在：{server_arg}"], notes
    elif _norm_path(server_arg) != _norm_path(target.server_py):
        server_diff = True
        diffs.append(f"server → {target.server_py}")

    if target.data_dir is not None:
        cur = entry.get("env", {}).get(ENV_VAULT)
        if not cur:
            env_missing = True
            diffs.append(f"未显式指定 {ENV_VAULT}（缺省会落到 ~/ai-memory）")
        elif _norm_path(cur) != _norm_path(target.data_dir):
            env_diff = True
            diffs.append(f"{ENV_VAULT} → {target.data_dir}")

    cmd = entry.get("command")
    if cmd and target.python and _norm_path(cmd) != _norm_path(target.python):
        notes.append(f"解释器不同：{cmd}")

    if server_diff or env_diff:
        return ST_DRIFT, diffs, notes
    # ``auto`` 回答的是「**我们能不能代写**」，不是「**人家连好没有**」。
    # 所以它只能出现在「确实需要动手」的分支里：配置已完全正确时一律报已连接，
    # 否则 auto=False 的客户端再怎么配得一丝不差也永远显示「需手工」，
    # 用户照着提示再粘一遍就变成重复条目。
    if env_missing:
        return (ST_AMBIGUOUS if client.auto else ST_MANUAL), diffs, notes + (
            [] if client.auto else [client.note or MANUAL_NOTE]
        )
    return ST_LINKED, diffs, notes


# --------------------------------------------------------------------------
# 生成条目
# --------------------------------------------------------------------------

def merge_entry(old: dict | None, target: Target, client: Client) -> dict:
    """在旧条目上做最小改动，返回新条目。"""
    old = dict(old or {})
    new: dict = dict(old)

    if client.requires_type or "type" in old:
        new["type"] = old.get("type") or "stdio"

    py = old.get("command")
    if not py or not _safe_exists(Path(str(py))):
        py = target.python or py
    if py:
        new["command"] = str(py)

    new["args"] = [_posix(target.server_py)]

    env = dict(old.get("env") or {})
    if target.data_dir is not None:
        env[ENV_VAULT] = _posix(target.data_dir)
    if env:
        new["env"] = env
    else:
        new.pop("env", None)
    return new


def snippet_json(entry: dict, indent: int = 2) -> str:
    return json.dumps({SERVER_NAME: entry}, ensure_ascii=False, indent=indent)


def snippet_toml(entry: dict) -> str:
    return "\n".join(_toml_block(entry)).rstrip("\n")


def snippet_for(client: Client, entry: dict) -> str:
    """按客户端真实结构生成可粘贴片段；返回空串表示本工具给不出可靠片段。"""
    style = client.snippet
    if style == "none":
        return ""
    if client.kind == "toml":
        return snippet_toml(entry)
    if style == "zed":
        # Zed：context_servers 里 command 是一个对象，不是字符串
        cmd: dict = {"path": entry.get("command", ""), "args": list(entry.get("args") or [])}
        if entry.get("env"):
            cmd["env"] = dict(entry["env"])
        return json.dumps({"context_servers": {SERVER_NAME: {"command": cmd}}},
                          ensure_ascii=False, indent=2)
    if style == "opencode":
        # OpenCode：type=local，command 是「解释器 + 参数」的合并数组，env 叫 environment
        payload: dict = {
            "type": "local",
            "command": [entry.get("command", "")] + list(entry.get("args") or []),
            "enabled": True,
        }
        if entry.get("env"):
            payload["environment"] = dict(entry["env"])
        return json.dumps({"mcp": {SERVER_NAME: payload}}, ensure_ascii=False, indent=2)
    if style == "vscode":
        item = dict(entry)
        item.setdefault("type", "stdio")
        return json.dumps({"servers": {SERVER_NAME: item}}, ensure_ascii=False, indent=2)
    if style == "cherry":
        return json.dumps({"mcp": {"servers": [{"name": SERVER_NAME, **entry}]}},
                          ensure_ascii=False, indent=2)
    return snippet_json(entry)


# --------------------------------------------------------------------------
# 写入：JSON
# --------------------------------------------------------------------------

def _detect_newline(raw: str) -> str:
    return "\r\n" if "\r\n" in raw else "\n"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def write_json(path: Path, client: Client, entry: dict) -> None:
    raw = _read_text_raw(path) if path.is_file() else ""
    nl = _detect_newline(raw)
    data = _read_json(path) if raw.strip() else {}

    if client.layout == "cherry":
        mcp = data.setdefault("mcp", {})
        if not isinstance(mcp, dict):
            raise ValueError("配置里的 mcp 不是对象，放弃写入")
        servers = mcp.setdefault("servers", [])
        if not isinstance(servers, list):
            raise ValueError("配置里的 mcp.servers 不是数组，放弃写入")
        payload = {"name": SERVER_NAME, **entry}
        for i, s in enumerate(servers):
            if isinstance(s, dict) and s.get("name") == SERVER_NAME:
                servers[i] = {**s, **payload}
                break
        else:
            servers.append(payload)
    elif client.layout == "servers":
        servers = data.setdefault("servers", {})
        if not isinstance(servers, dict):
            raise ValueError("配置里的 servers 不是对象，放弃写入")
        servers[SERVER_NAME] = entry
    else:
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("配置里的 mcpServers 不是对象，放弃写入")
        servers[SERVER_NAME] = entry

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if raw.endswith("\n"):
        text += "\n"
    if nl == "\r\n":
        text = text.replace("\n", "\r\n")
    _write_text_atomic(path, text)


# --------------------------------------------------------------------------
# 写入：TOML（段落级原地改）
# --------------------------------------------------------------------------

_TOML_BARE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _toml_quote(value: str) -> str:
    """生成 TOML 基本字符串。用 JSON 转义（\\ 与 \" 规则一致），中文直出。"""
    return json.dumps(str(value), ensure_ascii=False)


def _toml_key(key: str) -> str:
    return key if _TOML_BARE.match(key) else _toml_quote(key)


def _toml_block(entry: dict) -> list[str]:
    lines = ["[mcp_servers.ai-memory]"]
    if entry.get("command"):
        lines.append(f"command = {_toml_quote(entry['command'])}")
    args = entry.get("args") or []
    lines.append("args = [" + ", ".join(_toml_quote(a) for a in args) + "]")
    env = entry.get("env") or {}
    if env:
        lines.append("")
        lines.append("[mcp_servers.ai-memory.env]")
        for k, v in env.items():
            lines.append(f"{_toml_key(str(k))} = {_toml_quote(str(v))}")
    lines.append("")
    return lines


def _table_end(lines: list[str], start: int) -> int:
    j = start + 1
    while j < len(lines) and not lines[j].lstrip().startswith("["):
        j += 1
    return j


def _collapse_blank(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(line)
    while out and out[-1].strip() == "":
        out.pop()
    return out + [""]


def write_toml_text(text: str, entry: dict) -> str:
    """删掉旧的 ai-memory 主段与 env 段，在原位置插回新块；其余表原样保留。"""
    lines = text.splitlines()
    out: list[str] = []
    insert_at: int | None = None
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "[mcp_servers.ai-memory]":
            if insert_at is None:
                insert_at = len(out)
            i = _table_end(lines, i)
            continue
        if s == "[mcp_servers.ai-memory.env]":
            i = _table_end(lines, i)
            continue
        out.append(lines[i])
        i += 1

    block = _toml_block(entry)
    if insert_at is None:
        if out and out[-1].strip():
            out.append("")
        out.extend(block)
    else:
        out[insert_at:insert_at] = block

    merged = _collapse_blank(out)
    return "\n".join(merged)


def write_toml(path: Path, entry: dict) -> None:
    raw = _read_text_raw(path) if path.is_file() else ""
    nl = _detect_newline(raw)
    text = write_toml_text(raw, entry)
    if nl == "\r\n":
        text = text.replace("\n", "\r\n")
    _write_text_atomic(path, text)


# --------------------------------------------------------------------------
# 备份与回读校验
# --------------------------------------------------------------------------

def backup_config(path: Path) -> Path | None:
    if not path.is_file():
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = path.with_name(f"{path.name}.bak-{ts}")
    n = 1
    while dst.exists():
        dst = path.with_name(f"{path.name}.bak-{ts}-{n}")
        n += 1
    shutil.copy2(path, dst)
    return dst


def verify_written(client: Client, path: Path, entry: dict) -> tuple[bool, str]:
    try:
        got = read_entry(client, path)
    except Exception as e:  # noqa: BLE001
        return False, f"回读失败：{type(e).__name__}: {e}"
    if not got:
        return False, "回读不到 ai-memory 条目"
    if _norm_path((got.get("args") or [""])[0]) != _norm_path(entry["args"][0]):
        return False, "回读的 server 路径与预期不符"
    want = entry.get("env", {}).get(ENV_VAULT)
    if want and _norm_path(got.get("env", {}).get(ENV_VAULT)) != _norm_path(want):
        return False, f"回读的 {ENV_VAULT} 与预期不符"
    return True, ""


@dataclass
class ApplyResult:
    client_id: str
    name: str
    action: str          # written | skipped | manual | failed | planned
    detail: str = ""
    backup: Path | None = None


def apply_status(
    st: ClientStatus,
    target: Target,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> ApplyResult:
    """对单个客户端执行写入。dry_run 只报告将要做什么。"""
    c = st.client
    if st.state == ST_NOT_INSTALLED:
        return ApplyResult(c.id, c.name, "skipped", "未检测到该客户端")
    if st.state == ST_ERROR:
        return ApplyResult(c.id, c.name, "failed", st.error or "配置读取失败")
    if st.state == ST_MANUAL or not c.auto:
        return ApplyResult(c.id, c.name, "manual", c.note or MANUAL_NOTE)
    if st.state == ST_LINKED and not force:
        return ApplyResult(c.id, c.name, "skipped", "已是最新")

    path = st.config_path or c.paths[0]
    entry = merge_entry(st.entry, target, c)

    if dry_run:
        what = "创建配置并写入" if st.state == ST_NO_CONFIG else "修正条目"
        return ApplyResult(c.id, c.name, "planned", f"{what}：{path}")

    backup = backup_config(path)
    try:
        if c.kind == "toml":
            write_toml(path, entry)
        else:
            write_json(path, c, entry)
    except Exception as e:  # noqa: BLE001
        if backup and backup.is_file():
            try:
                shutil.copy2(backup, path)
            except OSError:
                pass
        return ApplyResult(c.id, c.name, "failed", f"{type(e).__name__}: {e}", backup)

    okv, why = verify_written(c, path, entry)
    if not okv:
        if backup and backup.is_file():
            try:
                shutil.copy2(backup, path)
            except OSError:
                pass
        return ApplyResult(c.id, c.name, "failed", f"{why}（已回滚）", backup)

    action = "written" if st.state != ST_NO_CONFIG else "written"
    return ApplyResult(c.id, c.name, action, str(path), backup)


def apply_all(
    statuses: list[ClientStatus],
    target: Target,
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[ApplyResult]:
    picked = statuses
    if only:
        want = {s.strip().lower() for s in only if s.strip()}
        picked = [s for s in statuses if s.client.id.lower() in want]
    return [apply_status(s, target, dry_run=dry_run, force=force) for s in picked]


# --------------------------------------------------------------------------
# 人读格式化
# --------------------------------------------------------------------------

def format_status_lines(statuses: list[ClientStatus]) -> list[str]:
    live = [s for s in statuses if s.state != ST_NOT_INSTALLED]
    gone = [s for s in statuses if s.state == ST_NOT_INSTALLED]
    lines: list[str] = []
    if not live:
        lines.append("  （没有检测到任何已安装的 AI 工具）")
    for s in live:
        tag = "  [路径未验证]" if s.client.unverified else ""
        lines.append(f"  {s.client.name:<20}{s.label:<10} {s.config_path}{tag}")
        for d in s.diffs:
            lines.append(f"      差异：{d}")
        for n in s.notes:
            lines.append(f"      提示：{n}")
        if s.state == ST_ERROR:
            lines.append(f"      错误：{s.error}")
    if gone:
        lines.append("  " + "、".join(s.client.name for s in gone) + " —— 未安装")
    return lines


def format_apply_lines(results: list[ApplyResult]) -> list[str]:
    action_label = {
        "written": "已写入", "planned": "将写入", "skipped": "跳过",
        "manual": "需手工", "failed": "失败",
    }
    lines = []
    for r in results:
        lines.append(f"  {r.name:<16} {action_label.get(r.action, r.action):<8} {r.detail}")
        if r.backup:
            lines.append(f"      备份：{r.backup}")
    return lines
