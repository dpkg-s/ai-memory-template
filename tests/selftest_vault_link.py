#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_vault / setup_link 自测（脚本式，不依赖 pytest）

    python tests/selftest_vault_link.py       # 退出码 0 = 全绿

全部用例都在临时目录里造夹具，**不触碰**任何真实 AI 客户端配置。
只读真实仓库的 template/ 与 rules/ 用于播种用例。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

try:  # tomllib 是 3.11+ 才进标准库，而本仓 CI 矩阵仍覆盖 3.10
    import tomllib
except ImportError:  # pragma: no cover - 仅在 3.10 上走这里
    tomllib = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import setup_link as L  # noqa: E402
import setup_vault as V  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL {name}" + (f"  <- {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    """环境缺能力（如 3.10 无 tomllib）时记跳过 —— 跳过不等于通过，汇总量分开报。"""
    global SKIP
    SKIP += 1
    print(f"  SKIP {name}  <- {why}")


def eq(name: str, got, want) -> None:
    check(name, got == want, f"got={got!r} want={want!r}")


TOML_SRC = """[mcp_servers]
[mcp_servers.ai-memory]
command = "C:/old/python.exe"
args = ["C:/old/ai-memory/server.py"]

[mcp_servers.ai-memory.tools.memory_search]
approval_mode = "approve"

[memories]
generate_memories = true
use_memories = true
"""

TOML_ENTRY = {
    "command": "C:/new/python.exe",
    "args": ["D:/vault/ai-memory/server.py"],
    "env": {"AI_MEMORY_DIR": "D:/vault", "AI_MEMORY_DIR_POLICY": "auto"},
}


def case_toml() -> None:
    print("\n[TOML 段落级写入]")
    out = L.write_toml_text(TOML_SRC, TOML_ENTRY)

    # 文本级断言：任何 Python 版本都跑（不依赖解析器）
    check("command 行已替换", 'command = "C:/new/python.exe"' in out, repr(out[:160]))
    check("旧 command 已消失", "C:/old/python.exe" not in out)
    check("env 段已写入", "[mcp_servers.ai-memory.env]" in out)
    check("tools 子段保留（文本）", "[mcp_servers.ai-memory.tools.memory_search]" in out)
    eq("重复写入幂等", L.write_toml_text(out, TOML_ENTRY), out)
    check("无重复空行", "\n\n\n" not in out, repr(out[:80]))

    # 解析级断言：需要 tomllib（3.11+）
    if tomllib is None:
        skip("TOML 解析级断言（4 项）", "Python 3.10 无 tomllib")
    else:
        data = tomllib.loads(out)
        ai = data["mcp_servers"]["ai-memory"]
        eq("command 已更新", ai["command"], "C:/new/python.exe")
        eq("args 已更新", ai["args"], ["D:/vault/ai-memory/server.py"])
        eq("env 已写入", ai["env"]["AI_MEMORY_DIR"], "D:/vault")
        eq("tools 子段保留", ai["tools"]["memory_search"]["approval_mode"], "approve")
        check("其他表保留", data["memories"]["use_memories"] is True)

    print("\n[TOML 无段时追加]")
    fresh = "[memories]\nuse_memories = true\n"
    out2 = L.write_toml_text(fresh, TOML_ENTRY)
    check("追加后出现段头", "[mcp_servers.ai-memory]" in out2, repr(out2[:120]))
    check("追加后原表仍在（文本）", "[memories]" in out2)
    eq("追加也幂等", L.write_toml_text(out2, TOML_ENTRY), out2)
    if tomllib is not None:
        d2 = tomllib.loads(out2)
        eq("追加后 command 正确", d2["mcp_servers"]["ai-memory"]["command"], "C:/new/python.exe")
        check("追加后原表仍在", d2["memories"]["use_memories"] is True)



CLIENT_JSON = L.Client(
    id="t_json", name="T", kind="json", layout="mcpServers",
    paths=(Path("x.json"),), probe=(Path("."),), requires_type=True,
)


def case_json(tmp: Path) -> None:
    print("\n[JSON merge 语义]")
    p = tmp / "mcp.json"
    old = {
        "mcpServers": {
            "ai-memory": {
                "type": "stdio",
                "command": "C:/old/python.exe",
                "args": ["C:/old/server.py"],
                "description": "本地共享记忆库",
                "env": {"AI_MEMORY_DIR_POLICY": "auto"},
            },
            "other": {"command": "keep-me"},
        },
        "unrelated": 1,
    }
    p.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")

    target = L.Target(python="C:/new/python.exe", server_py=Path("D:/vault/server.py"),
                      data_dir=Path("D:/vault"))
    entry = L.merge_entry(old["mcpServers"]["ai-memory"], target, CLIENT_JSON)
    eq("保留 type", entry.get("type"), "stdio")
    eq("保留 description", entry.get("description"), "本地共享记忆库")
    eq("保留 env 其他键", entry["env"].get("AI_MEMORY_DIR_POLICY"), "auto")
    eq("写入 AI_MEMORY_DIR", entry["env"].get("AI_MEMORY_DIR"), "D:/vault")
    eq("args 指向新 server", entry["args"], ["D:/vault/server.py"])
    eq("旧 command 路径失效则改用目标解释器", entry["command"], "C:/new/python.exe")

    L.write_json(p, CLIENT_JSON, entry)
    got = L.read_entry(CLIENT_JSON, p)
    eq("回读 args", got["args"], ["D:/vault/server.py"])
    eq("回读 env", got["env"].get("AI_MEMORY_DIR"), "D:/vault")
    raw = json.loads(p.read_text(encoding="utf-8"))
    check("其他服务未被动", raw["mcpServers"]["other"]["command"] == "keep-me")
    check("顶层其他键未被动", raw.get("unrelated") == 1)

    print("\n[CRLF 行尾保持]")
    p2 = tmp / "crlf.json"
    p2.write_text('{\r\n  "mcpServers": {}\r\n}\r\n', encoding="utf-8", newline="")
    L.write_json(p2, CLIENT_JSON, {"command": "C:/py", "args": ["D:/vault/server.py"]})
    data = p2.read_bytes()
    check("仍为 CRLF", b"\r\n" in data)
    check("无裸 LF", b"\n" not in data.replace(b"\r\n", b""))


def case_states(tmp: Path) -> None:
    print("\n[状态判定]")
    target = L.Target(python="C:/py.exe", server_py=tmp / "srv" / "server.py",
                      data_dir=tmp / "vault")
    (tmp / "srv").mkdir(parents=True, exist_ok=True)
    (tmp / "srv" / "server.py").write_text("#", encoding="utf-8")
    (tmp / "vault").mkdir(exist_ok=True)

    good = {"command": "C:/py.exe", "args": [str(target.server_py)],
            "env": {"AI_MEMORY_DIR": str(target.data_dir)}}
    eq("完整条目=已连接", L._judge(good, target, CLIENT_JSON)[0], L.ST_LINKED)

    amb = {k: v for k, v in good.items() if k != "env"}
    eq("缺 env=未指定库", L._judge(amb, target, CLIENT_JSON)[0], L.ST_AMBIGUOUS)

    # auto=False 只影响「能否代写」，不影响「是否已连好」——
    # 曾经这里恒报 MANUAL，导致 Cherry/VS Code 等 7 个客户端配得再对也显示「需手工」。
    manual_client = L.Client(
        id="t_manual", name="T", kind="json", layout="mcpServers",
        paths=(Path("x.json"),), probe=(Path("."),), auto=False,
    )
    eq("auto=False 但条目完整=已连接",
       L._judge(good, target, manual_client)[0], L.ST_LINKED)
    eq("auto=False 且缺 env=需手工",
       L._judge(amb, target, manual_client)[0], L.ST_MANUAL)
    check("auto=False 缺 env 时带手工提示",
          L._judge(amb, target, manual_client)[2] != [])

    drift = dict(good, args=[str(tmp / "other" / "server.py")])
    (tmp / "other").mkdir(exist_ok=True)
    (tmp / "other" / "server.py").write_text("#", encoding="utf-8")
    eq("server 不同=漂移", L._judge(drift, target, CLIENT_JSON)[0], L.ST_DRIFT)

    broken = dict(good, args=[str(tmp / "nope" / "server.py")])
    eq("server 不存在=失效", L._judge(broken, target, CLIENT_JSON)[0], L.ST_BROKEN)

    eq("解释器不同不算漂移", L.ST_LINKED,
       L._judge(dict(good, command="C:/other_py.exe"), target, CLIENT_JSON)[0])


def case_apply(tmp: Path) -> None:
    print("\n[apply：备份 / 预演 / 校验]")
    target = L.Target(python=sys.executable, server_py=tmp / "srv" / "server.py",
                      data_dir=tmp / "vault")
    c = L.Client(id="t2", name="T2", kind="json", layout="mcpServers",
                 paths=(tmp / "cfg" / "mcp.json",), probe=(tmp / "cfg",))
    st = L.ClientStatus(client=c, config_path=c.paths[0], exists=False,
                        installed=True, state=L.ST_NO_CONFIG)
    before = c.paths[0]
    res = L.apply_status(st, target, dry_run=True)
    eq("预演动作", res.action, "planned")
    check("预演不落盘", not before.exists())

    res = L.apply_status(st, target)
    eq("实写动作", res.action, "written")
    check("文件已生成", before.is_file())
    got = L.read_entry(c, before)
    eq("写入 args 正确", got["args"], [str(target.server_py).replace("\\", "/")])

    # 漂移 → 再 apply 应生成备份
    st2 = L.ClientStatus(client=c, config_path=before, exists=True,
                         installed=True, state=L.ST_DRIFT,
                         entry=dict(got, args=[str(tmp / "old.py")]))
    (tmp / "old.py").write_text("#", encoding="utf-8")
    res2 = L.apply_status(st2, target)
    eq("修正动作", res2.action, "written")
    check("生成了备份", res2.backup is not None and res2.backup.is_file())

    st3 = L.ClientStatus(client=c, config_path=before, exists=True,
                         installed=True, state=L.ST_LINKED,
                         entry=L.read_entry(c, before))
    res3 = L.apply_status(st3, target)
    eq("已连接默认跳过", res3.action, "skipped")
    res4 = L.apply_status(st3, target, force=True)
    eq("--force 强制重写", res4.action, "written")


def case_vault(tmp: Path) -> None:
    print("\n[vault：体检 / 打分 / 播种]")
    vault = tmp / "myvault"
    (vault / "sub").mkdir(parents=True)
    for name in ("a.md", "b.md", "记忆索引.md"):
        (vault / name).write_text("---\ntitle: t\n---\n", encoding="utf-8")
    (vault / "sub" / "c.md").write_text("x", encoding="utf-8")
    (vault / ".archive").mkdir()
    (vault / ".archive" / "old.md").write_text("x", encoding="utf-8")
    (vault / ".git").mkdir()

    info = V.inspect(vault, "test")
    eq("条目数含子目录、排除归档", info.md_entries, 4)
    check("识别索引 md", info.has_index)
    check("识别 git", info.has_git)
    check("空目录判定", V.inspect(tmp / "nope").empty)

    # 打分：像库的应高于空目录
    other = tmp / "empty"
    other.mkdir()
    check("打分排序正确", info.score > V.inspect(other).score)

    # 播种
    dst = tmp / "seedtest"
    r = V.init_vault(dst, ROOT)
    eq("播种 4 个 md", len([x for x in r["created"] if not x.startswith("rules/")]), 4)
    check("播种 rules", (dst / "rules").is_dir())
    check("记忆索引.md 存在", (dst / V.INDEX_MARK).is_file())

    # 同一个目录二次播种应拒绝
    try:
        V.init_vault(dst, ROOT)
        check("非空目录拒绝播种", False, "未抛异常")
    except FileExistsError:
        check("非空目录拒绝播种", True, "")
    # --force 允许
    r2 = V.init_vault(dst, ROOT, force=True)
    eq("force 时全部跳过", len(r2["created"]), 0)

    print("\n[vault：状态文件]")
    V.SETUP_HOME = tmp / "setup-home"
    V.STATE_FILE = V.SETUP_HOME / "vaults.json"
    res = V.set_current(vault, None)
    eq("set_current 路径", Path(res["path"]), vault.resolve())
    eq("get_current 回读", V.get_current(), vault.resolve())
    try:
        V.set_current(tmp / "not-here", None)
        check("非目录拒绝", False, "未抛异常")
    except NotADirectoryError:
        check("非目录拒绝", True)


def case_clients(tmp: Path) -> None:
    print("\n[客户端清单]")
    allc = L.clients()
    ids = [c.id for c in allc]
    eq("没有重复 id", len(ids), len(set(ids)))
    check("清单规模 >= 20", len(allc) >= 20, f"{len(allc)} 个")
    check("含 Gemini CLI", "gemini" in ids)
    check("含 Cline/Roo/Kilo", {"cline", "roo", "kilo"} <= set(ids))
    check("含 Zed/OpenCode/Continue", {"zed", "opencode", "continue"} <= set(ids))
    check("每条都有候选路径", all(c.paths for c in allc))
    check("路径变体客户端标了 unverified",
          {"trae", "qoder", "zed", "continue", "opencode"}
          <= {c.id for c in allc if c.unverified})

    target = L.Target(python=sys.executable, server_py=tmp / "srv" / "server.py",
                      data_dir=tmp / "vault")
    (tmp / "srv").mkdir(parents=True, exist_ok=True)
    (tmp / "srv" / "server.py").write_text("#", encoding="utf-8")
    (tmp / "vault").mkdir(exist_ok=True)

    print("\n[路径不确定时不新建配置]")
    probe = tmp / "fakeapp"
    probe.mkdir()
    unv = L.Client(id="unv", name="Unv", kind="json", layout="mcpServers",
                   paths=(probe / "mcp.json",), probe=(probe,), unverified=True)
    st = L.scan(target, [unv])[0]
    eq("unverified 且无配置 -> 需手工", st.state, L.ST_MANUAL)
    check("明确提示不自动创建", any("不自动创建" in n for n in st.notes))

    sure = L.Client(id="sure", name="Sure", kind="json", layout="mcpServers",
                    paths=(probe / "mcp.json",), probe=(probe,))
    eq("路径稳定且无配置 -> 待创建", L.scan(target, [sure])[0].state, L.ST_NO_CONFIG)

    print("\n[特殊结构片段]")
    entry = {"command": "C:/py.exe", "args": ["D:/v/ai-memory/server.py"],
             "env": {"AI_MEMORY_DIR": "D:/v"}}
    by_id = {c.id: c for c in allc}

    zed = json.loads(L.snippet_for(by_id["zed"], entry))
    eq("Zed 片段键", list(zed), ["context_servers"])
    eq("Zed command 是对象", zed["context_servers"]["ai-memory"]["command"]["path"], "C:/py.exe")

    oc = json.loads(L.snippet_for(by_id["opencode"], entry))
    eq("OpenCode type", oc["mcp"]["ai-memory"]["type"], "local")
    eq("OpenCode command 合并为数组", oc["mcp"]["ai-memory"]["command"][0], "C:/py.exe")
    eq("OpenCode environment", oc["mcp"]["ai-memory"]["environment"]["AI_MEMORY_DIR"], "D:/v")

    vs = json.loads(L.snippet_for(by_id["vscode"], entry))
    eq("VS Code 用 servers 键", list(vs), ["servers"])
    eq("VS Code type", vs["servers"]["ai-memory"]["type"], "stdio")

    ch = json.loads(L.snippet_for(by_id["cherry"], entry))
    eq("Cherry servers 是数组", isinstance(ch["mcp"]["servers"], list), True)
    eq("Cherry name 字段", ch["mcp"]["servers"][0]["name"], "ai-memory")

    eq("Continue 不给片段", L.snippet_for(by_id["continue"], entry), "")
    codex_snip = L.snippet_for(by_id["codex"], entry)
    check("Codex 片段含段头", "[mcp_servers.ai-memory]" in codex_snip, repr(codex_snip[:120]))
    if tomllib is None:
        skip("Codex 片段可解析（TOML）", "Python 3.10 无 tomllib")
    else:
        tool = tomllib.loads(codex_snip)
        eq("Codex 片段是 TOML", tool["mcp_servers"]["ai-memory"]["command"], "C:/py.exe")



def case_menu() -> None:
    """菜单冒烟：真起子进程喂输入，确认不崩、能退出。"""
    print("\n[菜单冒烟]")
    import subprocess

    def run(stdin_text: str):
        return subprocess.run(
            [sys.executable, str(ROOT / "ai_memory_setup.py")],
            input=stdin_text, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, cwd=str(ROOT),
        )

    r = run("0\n")
    out = (r.stdout or "") + (r.stderr or "")
    eq("主菜单退出码", r.returncode, 0)
    check("打印了仪表盘", "管家" in out, out[:200].replace("\n", "|"))
    check("四个分区都在", all(k in out for k in ("[1]", "[2]", "[3]", "[4]")), out[:300])

    r2 = run("2\n\n0\n")
    out2 = (r2.stdout or "") + (r2.stderr or "")
    eq("记忆库分区可返回", r2.returncode, 0)
    check("记忆库分区有内容", "记忆库目录" in out2, out2[:200])

    r3 = run("9\n0\n")
    eq("无效输入不崩", r3.returncode, 0)


def main() -> int:
    if tomllib is None:
        print("注意：当前 Python 无 tomllib（3.11+ 才有），TOML 解析级断言将记跳过。")
    tmp = Path(tempfile.mkdtemp(prefix="aim-selftest-"))
    print(f"临时目录：{tmp}")
    try:
        case_toml()
        case_json(tmp)
        case_states(tmp)
        case_clients(tmp)
        case_apply(tmp)
        case_vault(tmp)
        case_menu()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 46)
    print(f"  pass={PASS}  fail={FAIL}  skip={SKIP}")
    if FAILURES:
        print("  失败用例：")
        for f in FAILURES:
            print(f"    - {f}")
    print("=" * 46)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
