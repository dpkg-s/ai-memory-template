#!/usr/bin/env python3
"""目录策略（阶段三）测试 —— memory_write 新建条目自动归类。

背景：阶段二（2026-09-20）放行了标题里的 `/`，写入端**能**写进子目录了；但
「能」不等于「会」—— 只要写入方忘了写目录前缀，新笔记就静静躺在 vault 根，
扁平格局照旧长回来。阶段三把既有的语义分桶 `_entry_bucket()`（原本只服务索引
草稿）接到写入路径上：新建条目按标题与 tags 自动推断规范目录。

本测试守八类风险：

    A. 自动归类落点 —— 各语义桶落到对应规范目录、目录不存在时自动创建
    B. **裸标题** —— frontmatter 的 title 绝不能带目录前缀（目录只体现在路径上）
    C. **更新不搬运** —— 已存在的根目录条目切到 auto 后仍就地更新，不被搬走
    D. 策略 warn / strict / off 的差异化行为
    E. 非法策略值回落默认档，大小写不敏感
    F. 桶 -> 目录映射表完备（新增桶而漏配会静默落根，是最隐蔽的回归）
    G. 与阶段二政策共存（保留目录 / 层级上限仍生效）
    H. 核心页豁免 —— CORE_PAGES 始终留根

脚本把 AI_MEMORY_DIR 指向临时目录，绝不触碰真实记忆库。

运行:
    python tests/test_dir_policy.py        # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑（server.py import mcp）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-dirpolicy-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_FSYNC"] = "0"          # 纯逻辑测试，不需要每次落盘同步
os.environ["AI_MEMORY_DIR_POLICY"] = "auto"  # 显式钉住默认档，防宿主环境干扰
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402

_ROOT = Path(_TMP)
_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def _set_policy(value: str | None) -> None:
    """改策略。注意 _dir_policy() 每次调用都实时读环境变量 ⇒ 无需 reload。"""
    if value is None:
        os.environ.pop("AI_MEMORY_DIR_POLICY", None)
    else:
        os.environ["AI_MEMORY_DIR_POLICY"] = value


def _exists(rel: str) -> bool:
    return (_ROOT / rel).exists()


def _all_md() -> list[str]:
    """全库 md 的相对路径（含子目录），用于「有没有多出一份」这类判据。"""
    return sorted(p.relative_to(_ROOT).as_posix() for p in _ROOT.rglob("*.md"))


def _count_stem(stem: str) -> int:
    return sum(1 for p in _ROOT.rglob("*.md") if p.stem == stem)


def main() -> int:
    # =================================================================
    # A. auto：语义桶自动归类
    print("\n[A] 策略 auto —— 新建条目自动归类")
    cases = [
        (["阶段三测试条目A"], ["project"], "项目"),
        (["阶段三测试条目B"], ["network"], "运维"),
        (["2026-09-20 阶段三测试日志"], [], "时间线"),
        (["阶段三测试条目C"], ["workflow"], "规则"),
        (["阶段三测试条目D"], ["tool"], "工具"),
        (["阶段三测试条目E"], ["fix"], "修复"),
        (["阶段三测试条目F"], ["template"], "模板"),
        (["阶段三测试条目G"], [], "其他"),
        (["阶段三测试条目H"], ["ai"], "技术"),
        (["阶段三测试条目I"], ["identity"], "身份"),
    ]
    for (titles, tags, want_dir) in cases:
        title = titles[0]
        r = server.memory_write(title, f"# {title}\n\n阶段三自动归类测试。", tags=tags)
        # ⚠️ 期望路径必须用 _safe_filename 算，不能拿标题硬拼：标题里的空格等
        # 会被归一化成 '_'（"2026-09-20 阶段三测试日志" → "2026-09-20_阶段三测试日志.md"）。
        # 硬编码标题会得到假失败 —— 归类逻辑其实是对的。
        rel = f"{want_dir}/{server._safe_filename(title)}"
        check(f"A 落点 {title} -> {want_dir}/",
              _exists(rel),
              f"返回={r!r} 全库={_all_md()}")

    # 模板/ 目录此前不存在，验证 mkdir(parents=True) 生效
    check("A11 不存在的规范目录被自动创建",
          (_ROOT / "模板").is_dir())

    # 返回消息给出落点说明
    r = server.memory_write("阶段三测试条目J", "测试返回消息。", tags=["tool"])
    check("A12 返回消息注明已自动归类", "已自动归类到 工具/" in r, repr(r))

    # 显式含 '/' 时完全尊重写入方
    r = server.memory_write("阶段三手工目录/手工指定条目", "显式路径优先。")
    check("A13 含 '/' 时尊重写入方",
          _exists("阶段三手工目录/手工指定条目.md") and "阶段三手工目录/" in r,
          f"返回={r!r}")
    check("A14 含 '/' 时不做二次推断（不落 其他/）",
          not _exists("其他/阶段三手工目录_手工指定条目.md"),
          str(_all_md()))

    # =================================================================
    # B. 裸标题 —— 目录只体现在路径，不写进 frontmatter
    print("\n[B] frontmatter 的 title 必须保持裸名")
    meta, _body = server._load_memory(_ROOT / "项目" / "阶段三测试条目A.md")
    check("B1 frontmatter title == 裸标题", meta.get("title") == "阶段三测试条目A",
          repr(meta.get("title")))
    check("B2 title 不含目录前缀", "/" not in str(meta.get("title")), repr(meta.get("title")))
    raw = (_ROOT / "项目" / "阶段三测试条目A.md").read_text(encoding="utf-8")
    check("B3 文件正文首行 frontmatter 无 '/'",
          "title: 阶段三测试条目A" in raw.split("---")[1],
          raw.split("---")[1][:120])
    check("B4 文件确实位于子目录",
          (_ROOT / "项目" / "阶段三测试条目A.md").exists())

    # =================================================================
    # C. 更新不搬运（本次最关键的回归点）
    print("\n[C] 已存在条目的更新绝不搬运")
    _set_policy("off")
    r = server.memory_write("阶段三测试_根目录存量", "第一版，off 策略。")
    check("C1 off 策略下新条目落根", _exists("阶段三测试_根目录存量.md"), repr(r))
    _set_policy("auto")
    r = server.memory_write("阶段三测试_根目录存量", "第二版，切到 auto。")
    check("C2 切 auto 后仍就地更新（未搬运）",
          _exists("阶段三测试_根目录存量.md") and "已更新记忆" in r, repr(r))
    check("C3 未被搬进子目录",
          not any(p.name == "阶段三测试_根目录存量.md" and p.parent != _ROOT
                  for p in _ROOT.rglob("*.md")),
          str(_all_md()))
    check("C4 全库仅一份", _count_stem("阶段三测试_根目录存量") == 1,
          f"count={_count_stem('阶段三测试_根目录存量')}")
    check("C5 正文已更新为第二版",
          "第二版" in (_ROOT / "阶段三测试_根目录存量.md").read_text(encoding="utf-8"))

    # 子目录里的既有条目同样就地更新
    r = server.memory_write("阶段三测试条目B", "第二版内容。", tags=["network"])
    check("C6 子目录内条目就地更新",
          "已更新记忆" in r and _exists("运维/阶段三测试条目B.md"), repr(r))
    check("C7 未在根目录另建一份", not _exists("阶段三测试条目B.md"))
    check("C8 全库仍仅一份", _count_stem("阶段三测试条目B") == 1)

    # =================================================================
    # D. warn / strict / off
    print("\n[D] 策略 warn")
    _set_policy("warn")
    r = server.memory_write("阶段三测试条目K", "warn 策略。", tags=["fix"])
    check("D1 warn 不改落点（仍落根）", _exists("阶段三测试条目K.md"), repr(r))
    check("D2 warn 返回建议归类", "建议归类" in r and "修复/" in r, repr(r))
    check("D3 warn 未自动建子目录副本", not _exists("修复/阶段三测试条目K.md"))

    print("\n[E] 策略 strict")
    _set_policy("strict")
    r = server.memory_write("阶段三测试条目L", "strict 策略应被拒。", tags=["fix"])
    check("E1 strict 拒绝写入", r.startswith("错误："), repr(r))
    check("E2 文件确实未创建",
          not _exists("阶段三测试条目L.md") and not _exists("修复/阶段三测试条目L.md"),
          str(_all_md()))
    r = server.memory_write("阶段三/显式路径放行", "strict 下显式目录应放行。")
    check("E3 strict 下显式含 '/' 放行", _exists("阶段三/显式路径放行.md"), repr(r))
    r = server.memory_write("记忆库总规范", "核心页在 strict 下也不该被拒。")
    check("E4 strict 下核心页放行", _exists("记忆库总规范.md"), repr(r))

    print("\n[F] 策略 off")
    _set_policy("off")
    r = server.memory_write("阶段三测试条目M", "off 策略。", tags=["fix"])
    check("F1 off 落根", _exists("阶段三测试条目M.md"), repr(r))
    check("F2 off 无任何归类提示",
          "建议归类" not in r and "自动归类" not in r, repr(r))

    # =================================================================
    # G. 映射表完备性
    print("\n[G] 桶 -> 目录 映射表完备性")
    expected_buckets = {"索引", "时间线", "项目", "工具", "运维", "修复",
                        "规则", "身份与配置", "模板", "AI相关", "其他"}
    check("G1 BUCKET_DIRS 覆盖全部桶（用 set 比对）",
          set(server.BUCKET_DIRS) == expected_buckets,
          f"缺={expected_buckets - set(server.BUCKET_DIRS)} "
          f"多={set(server.BUCKET_DIRS) - expected_buckets}")

    bucket_probe = [
        ("桶探测_索引入口", ["moc"], None),
        ("桶探测_时间线", ["timeline"], "时间线"),
        ("桶探测_项目", ["project"], "项目"),
        ("桶探测_工具", ["tool"], "工具"),
        ("桶探测_运维", ["network"], "运维"),
        ("桶探测_修复", ["fix"], "修复"),
        ("桶探测_规则", ["workflow"], "规则"),
        ("桶探测_身份", ["identity"], "身份"),
        ("桶探测_模板", ["template"], "模板"),
        ("桶探测_ai", ["ai"], "技术"),
        ("桶探测_其他", [], "其他"),
    ]
    for title, tags, want in bucket_probe:
        got_bucket, _reason = server._entry_bucket({"title": title, "tags": tags},
                                                   Path(server._safe_filename(title)))
        got = server._suggest_dir(title, tags)
        check(f"G 桶 {got_bucket} -> {want}（实际 {got}）", got == want,
              f"_entry_bucket={got_bucket} _suggest_dir={got}")

    check("G2 映射目标不含保留目录",
          not {d for d in server.BUCKET_DIRS.values() if d} & set(server.RESERVED_DIRS),
          str({d for d in server.BUCKET_DIRS.values() if d} & set(server.RESERVED_DIRS)))
    check("G3 索引桶映射为 None（留根）", server.BUCKET_DIRS["索引"] is None)
    check("G4 规范目录清单取自映射表",
          set(server.BUCKET_DIRS.values()) >= {"项目", "运维", "工具", "规则"})

    # =================================================================
    # H. 核心页豁免 + 默认档 + 非法策略值回落
    print("\n[H] 核心页豁免、默认档与非法策略值")
    check("H1 核心页不参与自动归类",
          server._suggest_dir("记忆索引", ["moc"]) is None
          and server._suggest_dir("用户画像", ["identity"]) is None,
          f"索引={server._suggest_dir('记忆索引', ['moc'])} "
          f"画像={server._suggest_dir('用户画像', ['identity'])}")
    # 默认档钉死为 warn，这是一条**护栏**：auto 一旦成为默认，所有客户端的新
    # 条目落点都会变，而既有代码/习惯/多套测试都建立在「新条目在根」之上。
    check("H2 默认档为 warn（保守，不改落点）",
          server.DEFAULT_DIR_POLICY == "warn", server.DEFAULT_DIR_POLICY)
    _set_policy("GARBAGE")
    check("H3 非法策略值回落默认档",
          server._dir_policy() == server.DEFAULT_DIR_POLICY, server._dir_policy())
    r = server.memory_write("阶段三测试条目N", "非法策略值应回落默认档。", tags=["fix"])
    check("H4 非法策略值下按默认档落根",
          _exists("阶段三测试条目N.md") and not _exists("修复/阶段三测试条目N.md"),
          repr(r))
    _set_policy("AUTO")
    check("H5 合法值大小写不敏感", server._dir_policy() == "auto", server._dir_policy())
    _set_policy(None)
    check("H6 未设置时取默认档", server._dir_policy() == server.DEFAULT_DIR_POLICY,
          server._dir_policy())
    r = server.memory_write("阶段三测试条目O", "默认档提示测试。", tags=["fix"])
    check("H7 默认档给出建议且不搬动落点",
          _exists("阶段三测试条目O.md") and "建议归类" in r, repr(r))
    r = server.memory_write("阶段三测试条目P", "默认档下的无标签条目。")
    check("H8 默认档下无标签条目也落根",
          _exists("阶段三测试条目P.md"), repr(r))
    _set_policy("auto")

    # =================================================================
    # I. 与阶段二政策共存
    print("\n[I] 与阶段二政策共存")
    for bad, label in ((  ".archive/偷偷归档", "保留目录 .archive"),
                       ("ai-memory/代码副本", "保留目录 ai-memory"),
                       ("a/b/c/d/太深了", "层级超限")):
        r = server.memory_write(bad, "不该被写入。")
        check(f"I 拒绝 {label}", r.startswith("错误："), repr(r))

    # =================================================================
    # J. title 与落点分离：frontmatter 只存叶子名
    print("\n[J] title 只存叶子名 + 显式路径按路径精确命中")
    r = server.memory_write("阶段三/J测试目录/叶子名条目", "第一版。")
    p_j = _ROOT / "阶段三" / "J测试目录" / "叶子名条目.md"
    check("J1 显式路径条目已创建", p_j.exists(), repr(r))
    meta_j, _bj = server._load_memory(p_j)
    check("J2 frontmatter title == 叶子名（无目录前缀）",
          meta_j.get("title") == "叶子名条目", repr(meta_j.get("title")))
    check("J3 title 不含 '/'", "/" not in str(meta_j.get("title")), repr(meta_j.get("title")))

    r = server.memory_write("阶段三/J测试目录/叶子名条目", "第二版。")
    check("J4 同标题再写 → 就地更新", "已更新记忆" in r, repr(r))
    check("J5 全库仅一份（未重复建档）", _count_stem("叶子名条目") == 1,
          f"count={_count_stem('叶子名条目')}")
    check("J6 正文已替换为第二版", "第二版" in p_j.read_text(encoding="utf-8"))

    # 不同目录下的同名叶子名必须互不串档
    server.memory_write("阶段三/另一目录/叶子名条目", "另一个目录。")
    check("J7 同名叶子名可分处不同目录",
          (_ROOT / "阶段三" / "另一目录" / "叶子名条目.md").exists()
          and _count_stem("叶子名条目") == 2,
          f"count={_count_stem('叶子名条目')}")
    r = server.memory_write("阶段三/另一目录/叶子名条目", "第二个目录的第二版。")
    check("J8 再次更新仍只 2 份（路径精确命中生效）",
          _count_stem("叶子名条目") == 2, f"count={_count_stem('叶子名条目')} 返回={r!r}")
    meta_j2, _bj2 = server._load_memory(_ROOT / "阶段三" / "另一目录" / "叶子名条目.md")
    check("J9 第二份的 title 同为叶子名",
          meta_j2.get("title") == "叶子名条目", repr(meta_j2.get("title")))
    check("J10 更新命中的是 target 目录那一份",
          "第二个目录的第二版" in
          (_ROOT / "阶段三" / "另一目录" / "叶子名条目.md").read_text(encoding="utf-8")
          and "第二个目录的第二版" not in p_j.read_text(encoding="utf-8"))

    # =================================================================
    # 汇总
    # ⚠️ 措辞刻意避开裸 "FAIL" 字样：run_all_tests.py 会 grep 各套件输出统计断言数，
    # 若这里打印 "FAIL=0" 会被它当成一条失败断言重复计入。
    print("\n== 汇总 ==")
    print(f"通过 {len(_passed)} / 失败 {len(_failed)}")
    if _failed:
        for n in _failed:
            print("  - " + n)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
