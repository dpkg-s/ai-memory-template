#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_memory_setup 自测（脚本式，不依赖 pytest）

    python tests/selftest_setup.py       # 退出码 0 = 全绿

覆盖四件事：
  1. 版本号解析/比较/排序（含不可解析时必须返回 None 而非 0）
  2. ``verify_install()`` 的四态判定与证据分级
  3. 可选模块（Streamable HTTP）是否装成「一整套」—— 启动脚本内容 / 换行符 /
     幂等 / 覆盖前备份
  4. Web 控制台（第 ③ 步）是否装成「一整棵子树」—— 源定位 / 噪音目录排除 /
     子目录落盘 / 源缺就如实报 found=False / 记录里的 web 段

全部夹具造在临时目录，**不联网**（``fetch_remote_tags`` 的真实网络路径不在此测，
只测它的纯函数部分 ``sort_tags``）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_memory_setup as S  # noqa: E402

PASS = 0
FAIL = 0
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


def eq(name: str, got, want) -> None:
    check(name, got == want, f"got={got!r} want={want!r}")


CHANGELOG = """# Changelog

## [Unreleased]

> 还没发版的东西

### Added

- 某功能

## [3.0.0] - 2026-10-16

### Added

- 正式版

## [2.5.0] - 2026-10-12

- 旧版
"""


# --------------------------------------------------------------------------
# 1. 版本号
# --------------------------------------------------------------------------

def case_version() -> None:
    print("\n[版本号解析 / 比较 / 排序]")
    eq("v2.5.0 → (2,5,0)", S.parse_version("v2.5.0"), (2, 5, 0))
    eq("3.0.0 → (3,0,0)", S.parse_version("3.0.0"), (3, 0, 0))
    eq("分支名不可解析 → ()", S.parse_version("main"), ())
    eq("空串不可解析 → ()", S.parse_version(""), ())
    eq("'release-2.4.2' 能掏出数字", S.parse_version("release-2.4.2"), (2, 4, 2))

    eq("cmp：小", S.version_cmp("2.4.2", "2.5.0"), -1)
    eq("cmp：等（v 前缀不影响）", S.version_cmp("v2.5.0", "2.5.0"), 0)
    eq("cmp：大", S.version_cmp("3.0.0", "2.5.0"), 1)
    # 这条是刻意设的：'unknown' 与 'unknown' 相等≠已知相同版本。
    # 若返回 0，安装器会把「版本不明」判成「已是最新」直接跳过更新。
    eq("cmp：都不可解析时返回 None", S.version_cmp("unknown", "unknown"), None)
    eq("cmp：一侧为 None 时返回 None", S.version_cmp("2.5.0", None), None)

    eq("排序：降序 + 去重 + 保留带 v 的写法",
       S.sort_tags(["v2.4.2", "v3.0.0", "2.5.0", "v2.5.0", "nightly", "v1.0.0"]),
       ["v3.0.0", "v2.5.0", "v2.4.2", "v1.0.0"])
    eq("排序：没有 v 前缀时原样保留", S.sort_tags(["2.5.0", "2.4.2"]), ["2.5.0", "2.4.2"])
    eq("排序：全是垃圾 → 空列表", S.sort_tags(["main", "dev", ""]), [])
    eq("双位版本号按数值比（不是字典序）",
       S.sort_tags(["v2.10.0", "v2.9.0"]), ["v2.10.0", "v2.9.0"])


# --------------------------------------------------------------------------
# 2. CHANGELOG 版本证据
# --------------------------------------------------------------------------

def case_changelog(tmp: Path) -> None:
    print("\n[CHANGELOG 版本证据]")
    d = tmp / "cl1"
    d.mkdir()
    (d / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    # Unreleased 小节没有数字版本号，必须被跳过
    eq("跳过 [Unreleased] 取到 3.0.0", S._changelog_version(d), "3.0.0")
    eq("_version_from_tree 与之一致", S._version_from_tree(d), "3.0.0")

    empty = tmp / "cl2"
    empty.mkdir()
    eq("没有 CHANGELOG → None（不是 'unknown'）", S._changelog_version(empty), None)
    eq("_version_from_tree 回退 'unknown'", S._version_from_tree(empty), "unknown")

    bad = tmp / "cl3"
    bad.mkdir()
    (bad / "CHANGELOG.md").write_text("# Changelog\n\n没有版本小节\n", encoding="utf-8")
    eq("CHANGELOG 无版本小节 → None", S._changelog_version(bad), None)


# --------------------------------------------------------------------------
# 3. verify_install 四态
# --------------------------------------------------------------------------

def _mk_install(root: Path, *, server="print('hi')\n", index="x = 1\n",
                record=True, version="3.0.0") -> Path:
    """造一个最小已装实例（只登记两个文件，够验状态机就行）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "server.py").write_text(server, encoding="utf-8")
    (root / "memory_index.py").write_text(index, encoding="utf-8")
    if record:
        S.write_meta(root, {
            "installed_version": version,
            "installed_at": "2026-10-16T10:00:00+08:00",
            "source": "remote:v3.0.0",
            "install_dir": str(root),
            "data_dir": str(root),
            "files": {
                "server.py": S.sha256_file(root / "server.py"),
                "memory_index.py": S.sha256_file(root / "memory_index.py"),
            },
        })
    return root


def case_verify(tmp: Path) -> None:
    print("\n[verify_install 四态]")

    none_dir = tmp / "v_none"
    none_dir.mkdir()
    v = S.verify_install(none_dir)
    eq("空目录 → none", v["state"], S.VERIFY_NONE)
    eq("空目录 → 无版本", v["version"], None)
    eq("空目录 → 证据 none", v["evidence"], "none")

    ok_dir = _mk_install(tmp / "v_ok")
    v = S.verify_install(ok_dir)
    eq("文件全对 → ok", v["state"], S.VERIFY_OK)
    eq("版本取自记录", v["version"], "3.0.0")
    eq("证据 = 仅安装记录", v["evidence"], "meta")
    eq("没有缺文件", v["missing"], [])
    eq("没有改动", v["changed"], [])
    eq("没有异常提示", v["notes"], [])

    # 记录与 CHANGELOG 一致时，证据升级
    (ok_dir / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    v = S.verify_install(ok_dir)
    eq("记录+CHANGELOG 一致 → 证据升级", v["evidence"], "meta+changelog")
    eq("一致时不产生告警", v["notes"], [])

    # 记录过期：CHANGELOG 说得更高 → 以 CHANGELOG 为准
    stale = _mk_install(tmp / "v_stale", version="2.5.0")
    (stale / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    v = S.verify_install(stale)
    eq("记录 v2.5.0 vs 文件 v3.0.0 → 取 3.0.0", v["version"], "3.0.0")
    eq("记录与文件不符 → 记录版本仍单独可见", v["recorded_version"], "2.5.0")
    check("记录与文件不符必须告警", bool(v["notes"]), str(v["notes"]))
    check("告警里点明以 CHANGELOG 为准",
          any("CHANGELOG" in n for n in v["notes"]), str(v["notes"]))
    eq("版本号不一致本身不改状态（文件是好的）", v["state"], S.VERIFY_OK)

    # 有程序无记录
    part = _mk_install(tmp / "v_part", record=False)
    v = S.verify_install(part)
    eq("有 server.py 无记录 → partial", v["state"], S.VERIFY_PARTIAL)
    eq("partial 且无 CHANGELOG → 版本未知", v["version"], None)
    (part / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    v = S.verify_install(part)
    eq("partial + CHANGELOG → 版本可定", v["version"], "3.0.0")
    eq("部分状态下的证据来源", v["evidence"], "changelog")

    # 漂移：内容被改
    drift = _mk_install(tmp / "v_drift")
    (drift / "server.py").write_text("print('tampered')\n", encoding="utf-8")
    v = S.verify_install(drift)
    eq("文件被改 → drift", v["state"], S.VERIFY_DRIFT)
    eq("改动清单", v["changed"], ["server.py"])
    check("改动必须告警", any("不符" in n for n in v["notes"]), str(v["notes"]))

    # 漂移：文件缺失
    gone = _mk_install(tmp / "v_gone")
    (gone / "memory_index.py").unlink()
    v = S.verify_install(gone)
    eq("文件缺失 → drift", v["state"], S.VERIFY_DRIFT)
    eq("缺失清单", v["missing"], ["memory_index.py"])
    check("缺失必须告警", any("缺少" in n for n in v["notes"]), str(v["notes"]))

    # 记录里没登记的核心模块（旧版记录）应被列出来但不判 drift
    legacy = _mk_install(tmp / "v_legacy", record=False)
    S.write_meta(legacy, {
        "installed_version": "2.0.0",
        "files": {"server.py": S.sha256_file(legacy / "server.py")},
    })
    v = S.verify_install(legacy)
    eq("旧记录缺登记不改状态", v["state"], S.VERIFY_OK)
    eq("未登记的核心模块被列出", v["unrecorded"], ["memory_index.py"])


def case_fetch() -> None:
    """``fetch_remote_tags`` 的两条通道与降级 —— 用打桩替代真网络。

    本仓 CI 无外网，而这条路径又必须验（否则「列不出可用版本」这种线上问题
    只能等用户撞上）。做法是替换 ``urllib.request.urlopen`` 与 ``_tags_from_git``，
    验完在 finally 里还原。
    """
    print("\n[fetch_remote_tags 通道与降级]")
    import urllib.request as urlreq

    real_urlopen = urlreq.urlopen
    real_git = S._tags_from_git

    class Resp:
        def __init__(self, body: bytes):
            self._b = body

        def read(self) -> bytes:
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = json.dumps([{"name": n} for n in
                          ("v2.4.2", "v3.0.0", "nightly", "v2.5.0")]).encode()
    calls = {"git": 0}

    def git_spy(quiet: bool = False):
        calls["git"] += 1
        return []

    try:
        urlreq.urlopen = lambda *a, **k: Resp(payload)          # type: ignore[assignment]
        S._tags_from_git = git_spy                              # type: ignore[assignment]
        eq("API 通道：降序 + 过滤垃圾",
           S.fetch_remote_tags(quiet=True), ["v3.0.0", "v2.5.0", "v2.4.2"])
        eq("API 通时不碰 git", calls["git"], 0)
        eq("limit 生效", S.fetch_remote_tags(limit=1, quiet=True), ["v3.0.0"])
        eq("limit=0 表示不限", len(S.fetch_remote_tags(limit=0, quiet=True)), 3)

        def boom(*a, **k):
            raise OSError("模拟不可达")

        urlreq.urlopen = boom                                  # type: ignore[assignment]
        S._tags_from_git = lambda quiet=False: ["v2.5.0", "v3.0.0"]  # type: ignore[assignment]
        eq("API 挂 → git 兜底并排序",
           S.fetch_remote_tags(quiet=True), ["v3.0.0", "v2.5.0"])

        S._tags_from_git = lambda quiet=False: []              # type: ignore[assignment]
        eq("两条都不通 → 空列表（不抛）", S.fetch_remote_tags(quiet=True), [])
        eq("_fetch_remote_tag 空列表时返回 None", S._fetch_remote_tag([]), None)
    finally:
        urlreq.urlopen = real_urlopen                          # type: ignore[assignment]
        S._tags_from_git = real_git                            # type: ignore[assignment]


def case_do_versions(tmp: Path) -> None:
    """``versions`` 动作的打印路径（把远端列表打桩，否则 CI 无外网跑不了）。"""
    print("\n[versions 动作]")
    import contextlib
    import io

    args = S.parse_setup_args(["versions", "-i", str(tmp / "v_ok")])
    real = S.fetch_remote_tags
    try:
        S.fetch_remote_tags = lambda limit=40, quiet=False: ["v3.0.0", "v2.6.0", "v2.5.0"]  # type: ignore[assignment]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = S.do_versions(args)
        out = buf.getvalue()
    finally:
        S.fetch_remote_tags = real                                    # type: ignore[assignment]

    eq("退出码 0", code, 0)
    check("列出了全部版本", all(t in out for t in ("v3.0.0", "v2.6.0", "v2.5.0")), out)
    check("标了「最新」", "最新" in out, out)
    check("标了「已装」", "已装" in out, out)
    check("同版本时提示已是最新", "已是最新" in out, out)
    check("给出 --ref 用法", "--ref" in out, out)
    check("打印了证据等级", "证据" in out, out)

    # 远端列不出来时：不能崩，也不能说成「远端没有版本」
    try:
        S.fetch_remote_tags = lambda limit=40, quiet=False: []          # type: ignore[assignment]
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            code2 = S.do_versions(args)
        out2 = buf2.getvalue()
    finally:
        S.fetch_remote_tags = real                                    # type: ignore[assignment]
    eq("列不出来也返回 0", code2, 0)
    check("提示改用 --ref", "--ref" in out2, out2)


def case_http_scripts(tmp: Path) -> None:
    """可选模块必须装成「一整套」：两个 .py + 能直接跑的启动脚本。"""
    print("\n[可选模块：HTTP 整套安装]")
    inst = tmp / "http_inst"
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "serve_http.py").write_text("x = 1\n", encoding="utf-8")

    cmd = S.http_script_text(inst, inst, kind="cmd", py="C:/py/python.exe")
    sh = S.http_script_text(inst, inst, kind="sh", py="/usr/bin/python3")

    check("cmd 指向 serve_http.py", "serve_http.py" in cmd, cmd)
    check("cmd 带默认端口", "AI_MEMORY_HTTP_PORT=8700" in cmd, cmd)
    check("cmd 透传额外参数", "%*" in cmd, cmd)
    check("cmd 写死记忆库目录", f"AI_MEMORY_DIR={inst}" in cmd, cmd)
    check("cmd 全用 CRLF",
          "\r\n" in cmd and "\n" not in cmd.replace("\r\n", ""), repr(cmd[:60]))
    check("cmd 提醒非回环要白名单", "ALLOWED_HOSTS" in cmd, cmd)
    # 双击运行时窗口会在进程退出瞬间消失 —— 失败必须停下来讲原因，否则等于没报错
    check("cmd 失败时捕获退出码", '"RC=%ERRORLEVEL%"' in cmd, cmd)
    check("cmd 失败时 pause 住窗口", "\r\n    pause\r\n" in cmd, cmd)
    check("cmd 失败时点明可能是缺 mcp", "mcp" in cmd, cmd)
    check("cmd 失败时给出补装命令", "-m pip install" in cmd, cmd)
    check("cmd 失败时把原退出码透出去", "exit /b %RC%" in cmd, cmd)
    # 文件是 UTF-8、控制台默认 GBK ⇒ 不切码页时 echo 的中文必乱码
    check("cmd 先切 UTF-8 码页再 echo 中文",
          cmd.index("chcp 65001") < cmd.index("[启动失败]"), cmd)
    check("chcp 的噪音被吞掉", "chcp 65001 >nul" in cmd, cmd)

    check("sh 用 exec 交替换进程", "exec " in sh, sh)
    check("sh 用 python3 而非写死 Windows 解释器", "exec python3 " in sh, sh)
    check("sh 路径为正斜杠（无反斜杠）", "\\" not in sh, sh)
    check("sh 透传额外参数", '"$@"' in sh, sh)
    check("sh 端口可被环境变量覆盖", ":-8700}" in sh, sh)
    check("sh 是 LF（无 CR）", "\r" not in sh, repr(sh[:60]))
    # exec 之后就没机会补救了，所以依赖探测必须在 exec 之前
    check("sh 先探 mcp 再 exec",
          sh.index("import mcp") < sh.index("exec python3 "), sh)
    check("sh 缺依赖时退出码非 0", "exit 1" in sh, sh)

    # 路径里带单引号时，POSIX 引号必须闭合，否则脚本会在意想不到的地方断掉
    weird = inst / "o'brien"
    s2 = S.http_script_text(weird, weird, kind="sh")
    check("含单引号的路径被正确转义", "'\\''" in s2, s2)

    stats = S.write_http_scripts(inst, inst, "C:/py/python.exe")
    eq("首次写出两个脚本", sorted(stats["written"]),
       sorted([S.HTTP_SCRIPT_WIN, S.HTTP_SCRIPT_POSIX]))
    check("cmd 落盘为 CRLF",
          (inst / S.HTTP_SCRIPT_WIN).read_bytes().count(b"\r\n") > 0)
    eq("sh 落盘为 LF", (inst / S.HTTP_SCRIPT_POSIX).read_bytes().count(b"\r"), 0)
    if os.name != "nt":
        check("sh 带执行位", os.access(inst / S.HTTP_SCRIPT_POSIX, os.X_OK))

    stats2 = S.write_http_scripts(inst, inst, "C:/py/python.exe")
    eq("幂等：第二次不重写", stats2["written"], [])
    eq("幂等：两个都记为未变", len(stats2["unchanged"]), 2)

    # 用户手改过脚本 ⇒ 重装要覆盖，但必须先备份（注释里已写明"重装会覆盖"）
    (inst / S.HTTP_SCRIPT_WIN).write_text("@echo off\r\nrem 我手改的\r\n",
                                          encoding="utf-8", newline="")
    stats3 = S.write_http_scripts(inst, inst, "C:/py/python.exe")
    check("手改过的脚本被重写", S.HTTP_SCRIPT_WIN in stats3["written"], str(stats3))
    eq("覆盖前有备份", stats3["backed_up"], 1)
    eq("备份文件真的存在",
       len(list((inst / S.BACKUP_DIR).rglob(S.HTTP_SCRIPT_WIN))), 1)

    fresh = tmp / "http_dry"
    fresh.mkdir()
    d = S.write_http_scripts(fresh, fresh, None, dry_run=True)
    eq("预演报告会写两个", len(d["written"]), 2)
    check("预演不落盘", not (fresh / S.HTTP_SCRIPT_WIN).exists())

    # 装好之后，安装记录要能说清这套 HTTP 是什么样（排查端口/路径不用去读脚本正文）
    real = tmp / "http_meta"
    real.mkdir()
    for n in list(S.CORE_FILES) + list(S.OPTIONAL_FILES):
        (real / n).write_text(f"# {n}\n", encoding="utf-8")
    S.write_http_scripts(real, real, None)
    S.record_meta(S.Source(real, "3.0.0", "local:test"), real, real, True,
                  {"written": 9}, "install")
    meta = S.read_meta(real)
    check("安装记录含 http 段", "http" in meta, str(list(meta)))
    eq("记录里的默认端口", meta["http"]["port"], "8700")
    eq("记录里的端点路径", meta["http"]["path"], "/mcp")
    eq("记录里列出两个启动脚本", len(meta["http"]["start"]), 2)
    eq("files 只登记 .py（脚本是生成物，不参与完整性校验）",
       sorted(meta["files"]), sorted(list(S.CORE_FILES) + list(S.OPTIONAL_FILES)))


def case_state_line(tmp: Path) -> None:
    print("\n[状态文案]")
    lines = set()
    for d, name in ((tmp / "v_none", "none"), (tmp / "v_ok", "ok"),
                    (tmp / "v_drift", "drift"), (tmp / "v_part", "partial")):
        line = S.verify_state_line(S.verify_install(d))
        check(f"{name} 有文案", bool(line.strip()), line)
        lines.add(line)
    eq("四态文案互不相同", len(lines), 4)
    check("drift 文案提示强制重装",
          "强制" in S.verify_state_line(S.verify_install(tmp / "v_drift")))


def case_web_install(tmp: Path) -> None:
    """第 ③ 步的产物：Web 控制台要装成一整棵子树，不是丢两个文件让人自己拼。"""
    print("\n[Web 控制台安装：整套]")

    # 造一个「像 ai-memory-web」的源：入口 + 装配层 + 前端目录 + 该被排除的东西
    src_root = tmp / "web_src"
    web = src_root / S.WEB_DIRNAME
    (web / "web").mkdir(parents=True)
    (web / S.WEB_ENTRY).write_text("print('x')\n", encoding="utf-8")
    (web / "web_console.py").write_text("y = 1\n", encoding="utf-8")
    (web / "web" / "index.html").write_text("<html></html>", encoding="utf-8")
    (web / "requirements.txt").write_text("mcp>=1.30,<2\n", encoding="utf-8")
    for junk in ("tests", "__pycache__", ".git", ".github"):
        (web / junk).mkdir()
        (web / junk / "junk.txt").write_text("noise", encoding="utf-8")

    eq("能在 web-console/ 子目录里定位", S.find_web_source(src_root), web)
    # 直接把 ai-memory-web 当安装源（根上就是入口）也要认
    eq("根上直接是控制台时也认", S.find_web_source(web), web)
    # 只有入口没有静态目录 ⇒ 不是控制台（防把别的仓同名入口认成它）
    bare = tmp / "bare"
    bare.mkdir()
    (bare / S.WEB_ENTRY).write_text("x", encoding="utf-8")
    eq("缺 web/ 静态目录则不认", S.find_web_source(bare), None)
    eq("什么都没有时返回 None", S.find_web_source(tmp / "empty_none"), None)

    # rel 是 Path，Windows 上 str() 会带反斜杠 —— 断言统一归一到正斜杠再比
    rels = {str(r).replace("\\", "/") for r in S._web_rels(web)}
    check("保留入口与装配层", {S.WEB_ENTRY, "web_console.py"} <= rels, str(sorted(rels)))
    check("保留前端静态目录", "web/index.html" in rels, str(sorted(rels)))
    check("保留 requirements.txt", "requirements.txt" in rels, str(sorted(rels)))
    check("排除 tests/__pycache__/.git/.github",
          not any(r.split("/")[0] in S.WEB_EXCLUDE_DIRS for r in rels), str(sorted(rels)))

    # --- 脚本正文 ---------------------------------------------------------
    cmd = S.web_script_text(tmp / "inst", tmp / "inst", kind="cmd", py="C:/py/python.exe")
    sh = S.web_script_text(tmp / "inst", tmp / "inst", kind="sh")
    check("cmd 指向 web-console 里的入口",
          f"{S.WEB_DIRNAME}/{S.WEB_ENTRY}".replace("/", "\\") in cmd
          or f"{S.WEB_DIRNAME}\\{S.WEB_ENTRY}" in cmd, cmd)
    check("cmd 全用 CRLF",
          "\r\n" in cmd and "\n" not in cmd.replace("\r\n", ""), repr(cmd[:60]))
    check("cmd 先切 UTF-8 码页", "chcp 65001" in cmd, cmd)
    check("cmd 失败时 pause 住窗口", "\r\n    pause\r\n" in cmd, cmd)
    check("cmd 失败时透出退出码", "exit /b %RC%" in cmd, cmd)
    check("cmd 带默认端口 8811", "AI_MEMORY_WEB_PORT=8811" in cmd, cmd)
    check("cmd 提醒非回环要 token", "AI_MEMORY_WEB_TOKEN" in cmd, cmd)
    check("sh 用 python3 且无 Windows 路径",
          "exec python3 " in sh and "\\" not in sh, sh)
    check("sh 是 LF（无 CR）", "\r" not in sh, repr(sh[:60]))
    check("sh 先探 mcp 再 exec", sh.index("import mcp") < sh.index("exec python3 "), sh)

    # --- 落盘 -------------------------------------------------------------
    inst = tmp / "web_inst"
    inst.mkdir()
    st = S.install_web(src_root, inst, inst, "C:/py/python.exe")
    check("报告 found=True", st["found"], str(st))
    eq("落地目录是 web-console/", st["dest"], inst / S.WEB_DIRNAME)
    eq("写入文件数 = 全部有效文件", st["written"], 4)
    check("入口真的落盘", (inst / S.WEB_DIRNAME / S.WEB_ENTRY).is_file())
    check("前端目录真的落盘（相对结构保住）",
          (inst / S.WEB_DIRNAME / "web" / "index.html").is_file())
    check("噪音目录没被搬过来",
          not (inst / S.WEB_DIRNAME / "tests").exists())
    check("启动脚本已生成",
          (inst / S.WEB_SCRIPT_WIN).is_file() and (inst / S.WEB_SCRIPT_POSIX).is_file())

    st2 = S.install_web(src_root, inst, inst, "C:/py/python.exe")
    eq("幂等：第二次 0 写入", st2["written"], 0)
    eq("幂等：4 个记为未变", st2["unchanged"], 4)
    eq("幂等：脚本也不重写", st2["scripts"]["written"], [])

    # 源里没有控制台时必须如实说，不能假装装好了
    nope = tmp / "web_none"
    nope.mkdir()
    st3 = S.install_web(nope, tmp / "web_none_inst", tmp / "web_none_inst", None)
    check("源里没有就报 found=False", st3["found"] is False, str(st3))
    check("并且不落任何东西", not (tmp / "web_none_inst" / S.WEB_DIRNAME).exists())

    # 预演不落盘
    dry = tmp / "web_dry"
    dry.mkdir()
    d = S.install_web(src_root, dry, dry, None, dry_run=True)
    eq("预演报告会写 4 个", d["written"], 4)
    check("预演不落盘", not (dry / S.WEB_DIRNAME).exists())
    check("预演不生成脚本", not (dry / S.WEB_SCRIPT_WIN).exists())

    # --- 安装记录 ---------------------------------------------------------
    for n in list(S.CORE_FILES) + list(S.OPTIONAL_FILES):
        (inst / n).write_text(f"# {n}\n", encoding="utf-8")
    S.record_meta(S.Source(inst, "3.0.0", "local:test"), inst, inst, True,
                  {"written": 19}, "install", include_web=True)
    meta = S.read_meta(inst)
    check("记录含 web 段", "web" in meta, str(list(meta)))
    eq("记录里的目录名", meta["web"]["dir"], S.WEB_DIRNAME)
    eq("记录里的入口相对路径", meta["web"]["entry"], f"{S.WEB_DIRNAME}/{S.WEB_ENTRY}")
    eq("记录里的默认端口", meta["web"]["port"], "8811")
    eq("记录里的文件数（按磁盘实况）", meta["web"]["files"], 4)
    eq("记录里列出两个启动脚本", len(meta["web"]["start"]), 2)
    check("记录里 with_web=True", meta.get("with_web") is True, str(meta.get("with_web")))
    check("token 只记有没有、不落值", isinstance(meta["web"]["token"], bool),
          str(meta["web"]["token"]))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aim-ver-selftest-"))
    print(f"临时目录：{tmp}")
    try:
        case_version()
        case_changelog(tmp)
        case_verify(tmp)
        case_fetch()
        case_do_versions(tmp)
        case_http_scripts(tmp)
        case_web_install(tmp)
        case_state_line(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 46)
    print(f"  pass={PASS}  fail={FAIL}")
    if FAILURES:
        print("  失败用例：")
        for f in FAILURES:
            print(f"    - {f}")
    print("=" * 46)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
