#!/usr/bin/env python3
"""访问控制闭环测试（v3 · security / clearance 两字段硬限制）。

覆盖:
    yaml_io.normalize_access 的收紧规则
    server._can_read / _owner_gate 的判定
    memory_read / search / list / recent / smart_search / graph / orphans /
    stats / index_draft 的条目级过滤
    memory_write 的越权覆盖拦截与「受限方不得抬高敏感度」
    owner 专有工具的拒绝

验证要点:
    - 越权读取与「条目不存在」返回**逐字节一致**的结果（不泄露存在性）
    - 各检索通道口径一致：一处过滤、处处过滤，不能只堵 read 而漏 search
    - secret 条目在 member 视角下连**标题**都不出现（图谱 / 索引 / 孤立清单）
    - 受限方不能靠覆盖写降级别人的条目，也不能凭空造 secret

脚本把 AI_MEMORY_DIR 指向临时目录, 绝不触碰真实记忆库。

运行:
    python tests/test_clearance.py       # 退出码 0=全绿, 1=有失败
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
_TMP = tempfile.mkdtemp(prefix="ai-memory-clearance-")
os.environ["AI_MEMORY_DIR"] = _TMP
os.environ["AI_MEMORY_SOURCE_CONTEXT"] = "test-clearance"
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402
import yaml_io  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def as_level(level: str):
    """把当前权级临时切到 level，返回退出时用于恢复的 token。"""
    return server.set_clearance(level)


def read_meta(title: str) -> dict:
    path = server._resolve_title_via_index(title)
    if path is None:
        for f in server._iter_md_files():
            m, _ = server._load_memory(f)
            if server._entry_title(m, f) == title:
                return m
        return {}
    meta, _ = server._load_memory(path)
    return meta


# ============================================================
print("== A. normalize_access 收紧规则 ==")
# ============================================================
cases = [
    (("secret", "guest"), ("secret", "owner"), "绝密 + 访客 ⇒ 抬到 owner"),
    (("public", None), ("public", "guest"), "公开 ⇒ 门槛 guest"),
    ((None, None), ("internal", "reader"), "全缺 ⇒ internal / reader"),
    (("internal", "owner"), ("internal", "owner"), "内部但抬到 owner ⇒ 保持"),
    (("bogus", "bogus"), ("internal", "reader"), "非法值 ⇒ 全默认"),
]
for args, want, desc in cases:
    check(f"A normalize: {desc}", yaml_io.normalize_access(*args) == want,
          str(yaml_io.normalize_access(*args)))
check("A clearance_allows: member 不够 owner",
      not yaml_io.clearance_allows("member", "owner"))
check("A clearance_allows: 脏数据按最严（None 读 None）",
      not yaml_io.clearance_allows(None, None))

# ============================================================
print("\n== B. 建档（owner 视角） ==")
# ============================================================
server.memory_write("权限_公开条", "这是一条公开内容，任何人都能读。", security="public")
server.memory_write("权限_内部条", "这是一条内部内容，member 及以上可读。",
                    security="internal", tags=["内部"])
server.memory_write("权限_绝密条",
                    "绝密内容：内网 192.168.1.10，密钥 sk-test，路径 D:\\secret。"
                    "\n\n关联：[[权限_内部条]]",
                    security="secret", tags=["绝密"])
# sensitive 是「较敏感的 internal」，门槛仍为 member。它存在的意义是给 reader
# 一档留出「读得到 internal、读不到这条」的判别样本 —— 否则 reader 与 guest
# 在测试里无法区分（两者都读不到 secret）。
server.memory_write("权限_敏感条", "较敏感内容：内网跳板机 10.0.0.8。",
                    security="sensitive", tags=["敏感"])

_m_pub = read_meta("权限_公开条")
_m_sec = read_meta("权限_绝密条")
check("B1 落盘写出 security=public", _m_pub.get("security") == "public",
      str(_m_pub.get("security")))
check("B2 落盘写出 clearance=guest", _m_pub.get("clearance") == "guest",
      str(_m_pub.get("clearance")))
check("B3 secret 自动抬 clearance 到 owner", _m_sec.get("clearance") == "owner",
      str(_m_sec.get("clearance")))
check("B4 schema_version 升到 3", _m_sec.get("schema_version") == server.SCHEMA_VERSION,
      str(_m_sec.get("schema_version")))

# ============================================================
print("\n== C. owner：全部可见 ==")
# ============================================================
check("C1 owner 读绝密命中", "绝密内容" in server.memory_read("权限_绝密条"))
check("C2 owner 搜索命中绝密", "权限_绝密条" in server.memory_search("绝密"))
check("C3 owner 列表含绝密", "权限_绝密条" in server.memory_list(limit=50))
check("C4 owner stats 含安全等级分布", "安全等级分布" in server.memory_stats())

# ============================================================
print("\n== D. member：读不到 secret ==")
# ============================================================
_tok = as_level("member")
try:
    deny = server.memory_read("权限_绝密条")
    missing = server.memory_read("根本不存在的条目_xyz")
    check("D1 member 读绝密 ⇒ 未找到", "未找到" in deny and "绝密内容" not in deny, deny[:60])
    _norm = lambda s, t: s.replace(t, "<TITLE>")  # noqa: E731
    check("D2 越权与不存在同构（句式逐字一致）",
          _norm(deny, "权限_绝密条") == _norm(missing, "根本不存在的条目_xyz"),
          f"{deny[:40]!r} vs {missing[:40]!r}")

    _search = server.memory_search("绝密")
    check("D3 member 搜索不含绝密标题", "权限_绝密条" not in _search, _search[:120])
    check("D4 member 搜索不泄露关键词上下文", "192.168" not in _search)

    _list = server.memory_list(limit=50)
    check("D5 member 列表不含绝密", "权限_绝密条" not in _list)
    check("D6 member 列表仍含内部条", "权限_内部条" in _list)

    _recent = server.memory_recent(days=7, limit=50)
    check("D7 member recent 不含绝密", "权限_绝密条" not in _recent)

    _smart = server.memory_smart_search("绝密 密钥 内网", limit=10)
    check("D8 member smart_search 不含绝密", "权限_绝密条" not in _smart)

    _graph = server.memory_graph("权限_内部条", include_all=True)
    check("D9 member 图谱反链不含绝密标题", "权限_绝密条" not in _graph, _graph[:160])

    _orphans = server.memory_orphans()
    check("D10 member 孤立清单不含绝密", "权限_绝密条" not in str(_orphans))

    _idx = server.memory_index_draft()
    check("D11 member 索引草稿不含绝密", "权限_绝密条" not in _idx)

    _stats = server.memory_stats()
    check("D12 member stats 不含安全等级分布", "安全等级分布" not in _stats)
    check("D13 member stats 不含绝密标题", "权限_绝密条" not in _stats)

    # 写侧：不得覆盖、不得抬高
    _overwrite = server.memory_write("权限_绝密条", "被覆盖成普通内容")
    check("D14 member 覆盖 secret 条目被拒", "不可写" in _overwrite, _overwrite[:80])

    _escalate = server.memory_write("权限_伪造绝密", "试图自造绝密。", security="secret")
    check("D15 member 不能写 secret", "不能写入" in _escalate, _escalate[:80])

    _del = server.memory_delete("权限_绝密条")
    check("D16 member 不能删除条目", "不可用" in _del, _del[:60])

    _doctor = server.memory_doctor()
    check("D17 member 不能跑底层诊断", "不可用" in _doctor, _doctor[:60])

    _cfg = server._memory_config_tool(action="get")
    check("D18 member 不能读改运行时配置", "不可用" in _cfg, _cfg[:60])

    # 语义回归保护：新增 reader 档不得改变 member 的既有能力 —— member 仍应
    # 读得到 sensitive（它的门槛就是 member），否则等于无意中收紧了 AstrBot。
    check("D19 member 仍可读 sensitive（reader 档未收紧 member）",
          "较敏感内容" in server.memory_read("权限_敏感条"))
finally:
    server.reset_clearance(_tok)

# ============================================================
print("\n== E. guest：只能读 public ==")
# ============================================================
_tok = as_level("guest")
try:
    check("E1 guest 读不到 internal",
          "未找到" in server.memory_read("权限_内部条"))
    check("E2 guest 可读 public", "公开内容" in server.memory_read("权限_公开条"))
    _glist = server.memory_list(limit=50)
    check("E3 guest 列表不含 internal", "权限_内部条" not in _glist)
    check("E4 guest 列表含 public", "权限_公开条" in _glist)

    # 写侧：guest 是「面向非本人设备」的共享端点，必须纯读。
    # 注意这与 member 不同 —— member 可写普通条目（D14/D15 只拦 secret 与越权覆盖），
    # 所以这里断言的是 guest 专属语义，不要合并进 D 组。
    _gwrite = server.memory_write("权限_guest越权写", "guest 不该写进来的内容。")
    check("E5 guest 不能写普通条目", "不可用" in _gwrite, _gwrite[:80])

    _gcommit = server.memory_commit("权限_guest越权commit", "guest 不该提交的内容。")
    check("E6 guest 不能 commit", "不可用" in _gcommit, _gcommit[:80])

    # 反向保护：写拦截不得溢出到读路径。
    check("E7 guest 写被拒后仍可读 public", "公开内容" in server.memory_read("权限_公开条"))
finally:
    server.reset_clearance(_tok)

# ============================================================
print("\n== H. reader：只读共享档（2026-10-26 新增） ==")
# ============================================================
# 这一档的存在理由：给「非本人设备只读共享」一个能读 internal、读不到
# sensitive/secret、且完全禁写的档位。它与 guest 的差别只在读（guest 只够
# public），写上一律全禁。
_tok = as_level("reader")
try:
    check("H1 reader 可读 internal", "内部内容" in server.memory_read("权限_内部条"))
    check("H2 reader 可读 public", "公开内容" in server.memory_read("权限_公开条"))
    check("H3 reader 读不到 sensitive", "较敏感内容" not in server.memory_read("权限_敏感条"))
    check("H4 reader 读不到 secret", "绝密内容" not in server.memory_read("权限_绝密条"))

    _hlist = server.memory_list(limit=50)
    check("H5 reader 列表含 internal", "权限_内部条" in _hlist)
    check("H6 reader 列表不含 sensitive", "权限_敏感条" not in _hlist)
    check("H7 reader 列表不含 secret", "权限_绝密条" not in _hlist)

    # 与 guest 的判别点：同一条 internal，guest 读不到而 reader 读得到。
    _rg = server.memory_read("权限_内部条")

    _hwrite = server.memory_write("权限_reader越权写", "reader 不该写进来的内容。")
    check("H8 reader 不能写普通条目", "不可用" in _hwrite, _hwrite[:80])
    _hcommit = server.memory_commit("权限_reader越权commit", "reader 不该提交的内容。")
    check("H9 reader 不能 commit", "不可用" in _hcommit, _hcommit[:80])
    _hdel = server.memory_delete("权限_内部条")
    check("H10 reader 不能删除条目", "不可用" in _hdel, _hdel[:60])
    _hsec = server.memory_write("权限_reader自造敏感", "试图自造。", security="sensitive")
    check("H11 reader 写被拦优先于敏感度校验", "不可用" in _hsec, _hsec[:80])
finally:
    server.reset_clearance(_tok)

# guest 与 reader 的档位关系：同一条 internal，guest 不可读、reader 可读。
_tok = as_level("guest")
try:
    check("H12 判别：同一条 internal guest 读不到",
          "未找到" in server.memory_read("权限_内部条"))
    check("H13 判别：reader 那条记录确实有内容", "内部内容" in _rg)
finally:
    server.reset_clearance(_tok)

# ============================================================
print("\n== F. owner 写侧：越权覆盖与一致写回 ==")
# ============================================================
_ok = server.memory_write("权限_提级测试", "普通内容。", security="internal")
check("F1 owner 建普通条目成功", "已创建" in _ok, _ok[:60])
_up = server.memory_update_metadata("权限_提级测试", security="secret")
check("F2 update_metadata 可提级", "已更新" in _up, _up[:60])
_m_up = read_meta("权限_提级测试")
check("F3 提级后 clearance 同步抬到 owner", _m_up.get("clearance") == "owner",
      str(_m_up.get("clearance")))

# 只改正文不应把档位重置
server.memory_write("权限_提级测试", "只改正文，不动档位。")
_m_keep = read_meta("权限_提级测试")
check("F4 只改正文时 security 保留", _m_keep.get("security") == "secret",
      str(_m_keep.get("security")))
check("F5 只改正文时 clearance 保留", _m_keep.get("clearance") == "owner",
      str(_m_keep.get("clearance")))

_bad = server.memory_write("权限_非法档", "内容。", security="top-secret")
check("F6 非法 security 被拒", "取值非法" in _bad, _bad[:60])

# 默认档（不传参数）应是 internal / member
server.memory_write("权限_默认档", "默认档内容。")
_m_def = read_meta("权限_默认档")
check("F7 不传参 ⇒ security=internal", _m_def.get("security") == "internal",
      str(_m_def.get("security")))
check("F8 不传参 ⇒ clearance=reader", _m_def.get("clearance") == "reader",
      str(_m_def.get("clearance")))

# ============================================================
print("\n== G. 传输层路由表（纯函数，不启服务） ==")
# ============================================================
import transport_http  # noqa: E402

_routes = transport_http.clearance_routes("/mcp")
check("G1 默认 /mcp ⇒ owner 可写", _routes.get("/mcp") == ("owner", True), str(_routes))
check("G2 /mcp/member 已登记且可写", _routes.get("/mcp/member") == ("member", True), str(_routes))
check("G3 /mcp/guest 已登记", _routes.get("/mcp/guest") == ("guest", True), str(_routes))
check("G4 /mcp/reader 已登记", _routes.get("/mcp/reader") == ("reader", True), str(_routes))
check("G5 路由表共 6 档", len(_routes) == 6, str(sorted(_routes)))
# 2026-10-26：第二根轴（只读变体）。owner-ro 是「全库可读、一字不许改」的挂载点，
# 它比 /mcp 严格更弱，因此允许存在；guest-ro / reader-ro 与基础档完全等价，
# 必须**不**生成 —— 多一条等价路径就是多一个可绕过的口子。
check("G6 /mcp/owner-ro 存在且只读",
      _routes.get("/mcp/owner-ro") == ("owner", False), str(_routes))
check("G7 /mcp/member-ro 存在且只读",
      _routes.get("/mcp/member-ro") == ("member", False), str(_routes))
check("G8 不给 guest/reader 生成 -ro 等价路径",
      "/mcp/guest-ro" not in _routes and "/mcp/reader-ro" not in _routes, str(sorted(_routes)))
_off = transport_http.clearance_routes("/mcp", "off")
check("G9 off ⇒ 只剩 owner 端点", _off == {"/mcp": ("owner", True)}, str(_off))
_bad = transport_http.clearance_routes("/mcp", "member,bogus,owner,owner-ro")
check("G10 非法档位与 owner 别名被丢弃、owner-ro 保留",
      sorted(_bad) == ["/mcp", "/mcp/member", "/mcp/owner-ro"], str(sorted(_bad)))

# ============================================================
print("\n== I. 只读视野：读得全，写不了（2026-10-26 新增） ==")
# ============================================================
# 这一档不是新档位，而是「档位 × 写开关」的组合：视野 owner、writable=False。
# 存在的理由：别的设备上要「能读完、不能改」—— 此前读写焊死在档位上，没有坐标。
_tok = server.set_clearance("owner", writable=False)
try:
    check("I1 owner-ro 读绝密命中（内容可见）", "绝密内容" in server.memory_read("权限_绝密条"))
    check("I2 owner-ro 搜索命中绝密", "权限_绝密条" in server.memory_search("绝密"))
    check("I3 owner-ro 列表含绝密", "权限_绝密条" in server.memory_list(limit=50))
    check("I4 owner-ro stats 仍含安全等级分布", "安全等级分布" in server.memory_stats())
    check("I5 owner-ro 仍可读 internal", "内部内容" in server.memory_read("权限_内部条"))

    check("I6 owner-ro 不能写普通条目",
          "只读" in server.memory_write("权限_只读越权写", "不该写进来的内容。"))
    check("I7 owner-ro 不能 commit", "只读" in server.memory_commit("权限_只读越权commit", "x"))
    check("I8 owner-ro 不能删除", "不可用" in server.memory_delete("权限_内部条"))
    check("I9 owner-ro 不能改元数据",
          "不可用" in server.memory_update_metadata("权限_内部条", tier="cold"))
    check("I10 owner-ro 不能跑诊断", "不可用" in server.memory_doctor())
    check("I11 owner-ro 不能读改配置", "不可用" in server._memory_config_tool(action="get"))
    # 拒绝语必须区分「档位只读」与「端点只读」，否则排障时分不清选错身份还是选错端点。
    _w = server.memory_write("权限_只读越权写2", "x")
    check("I12 拒绝语点名「端点只读」而非「权限不足」",
          "端点" in _w and "权限不足" not in _w, _w[:80])
finally:
    server.reset_clearance(_tok)

# 反向保护：写开关必须随请求结束还原。泄漏一次的后果是「一个只读请求把后续所有
# 请求都变成只读」，而那是极难复现的一类故障（下一个调用方会以为自己坏了）。
check("I13 reset 后 owner 恢复可写",
      "已创建" in server.memory_write("权限_只读还原测试", "还原后应能写。"))

# member 视野 + 只读：介于两者之间 —— 能读 sensitive，但写被拒。
_tok = server.set_clearance("member", writable=False)
try:
    check("J1 member-ro 可读 sensitive", "较敏感内容" in server.memory_read("权限_敏感条"))
    check("J2 member-ro 读不到 secret", "绝密内容" not in server.memory_read("权限_绝密条"))
    check("J3 member-ro 不能写普通条目",
          "只读" in server.memory_write("权限_memberro越权写", "x"))
finally:
    server.reset_clearance(_tok)
check("J4 reset 后 member 恢复可写",
      "已创建" in server.memory_write("权限_member还原测试", "还原后应能写。"))

# guest / reader 即使 writable=True 也不能写：档位语义与写开关是**合取**，
# 写开关不能把只读档位「解锁」。
_tok = server.set_clearance("reader", writable=True)
try:
    check("K1 reader + writable=True 仍然不能写（合取，非并列）",
          "只读" in server.memory_write("权限_reader合取测试", "x"))
finally:
    server.reset_clearance(_tok)


# ============================================================
print()
if _failed:
    print(f"==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
print(f"==== 结果: {len(_passed)} passed / 0 failed ====")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0)
