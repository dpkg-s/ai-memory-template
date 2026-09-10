#!/usr/bin/env python3
"""搜索质量评测 for ai-memory MCP server (server.py)。

目的（《改进建议》P1「Search 质量评测」）：
    当前检索是「子串匹配 + 字段加权 + 时间衰减」的纯词法方案。将来若引入中文
    分词、FTS5 或语义检索，需要有**客观对照口径**才能判断是变好还是变坏。本脚本
    用固定的小型标注集（query → 期望命中的标题集合）计算三项指标并断言不低于
    基线：

      recall@10   期望命中的条目有多少比例出现在前 10 条结果里
      MRR         第一个正确结果排名倒数的均值（衡量排序质量）
      零结果率    完全无命中的查询占比（用户最直观的失败体验）

    指标只用于**回归护栏**（不得显著劣化），不是绝对质量宣称——fixture 规模有限，
    数值会随实现变化；基线取「实测值留出余量」，故意不贴着实测值写死。

覆盖的检索难点：
    - 中文短语（无空格）与中英混排
    - 代码标识符 / 全大写缩写（WAL、JWT、HAR、OpenWrt）
    - 专有名词（PyInstaller、ffmpeg、ESP32）
    - 同义不同词（"跨主机通信" vs 正文的 "跨主机通信"）

运行:
    python tests/test_search_quality.py      # 退出码 0=全绿, 1=有失败
依赖:
    需在装有 mcp 的 venv 里跑(server.py import mcp)。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

# ---- 先把 MEMORY_DIR 指到临时目录, 再 import server -------------------
_TMP = tempfile.mkdtemp(prefix="ai-memory-searchq-")
os.environ["AI_MEMORY_DIR"] = _TMP
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

import server  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []
_TOPK = 10

# ── 基线（实测后留余量；低于此值即视为检索质量劣化） ────────────────────
BASELINE_RECALL = 0.95
BASELINE_MRR = 0.80
BASELINE_ZERO_RATE = 0.0

# ── fixture 库：15 篇，覆盖中文/英文/代码标识符/专有名词 ────────────────
_FIXTURES: list[tuple[str, str, list[str]]] = [
    ("检索_Flask部署", "使用 Flask + gunicorn 部署 Python 服务，配置 systemd 守护进程与优雅重启。", ["python", "deploy"]),
    ("检索_Docker网络", "Docker 容器跨主机通信需要 overlay 网络，或改用 host 模式简化拓扑。", ["docker", "network"]),
    ("检索_SQLite并发", "SQLite 的 WAL 模式允许多进程读写并发，busy_timeout 控制等待时长。", ["database"]),
    ("检索_PyQt打包", "PyQt5 程序用 PyInstaller 打包成单目录便携版，并隐藏控制台窗口。", ["python", "tool"]),
    ("检索_Vue路由守卫", "Vue Router 的 beforeEach 守卫用于登录态校验与权限拦截。", ["frontend"]),
    ("检索_Laravel中间件", "Laravel 中间件通过 handle 方法实现请求过滤与 JWT 鉴权。", ["backend"]),
    ("检索_ESP32传感器", "ESP32 通过 I2C 读取土壤湿度传感器，异常时返回错误码 -11。", ["tool"]),
    ("检索_路由器Docker", "OpenWrt 路由器上运行 Docker 需要先挂载 overlay 并扩容存储。", ["network", "docker"]),
    ("检索_微信小程序支付", "微信小程序支付需要统一下单、签名校验与回调验签三段流程。", ["backend"]),
    ("检索_Obsidian双链", "Obsidian 的 wikilink 双链 [[笔记名]] 构成可导航的知识图谱。", ["obsidian"]),
    ("检索_MCP服务器", "MCP server 通过 stdio 或 SSE 与 AI 客户端通信，暴露 tools 列表。", ["mcp", "ai"]),
    ("检索_ffmpeg合并", "ffmpeg 用 concat demuxer 合并多个 TS 分片为单个 MP4 文件。", ["tool"]),
    ("检索_爬虫逆向", "分析 HAR 日志定位加密参数，还原 JS 中的签名算法实现。", ["python", "security"]),
    ("检索_MySQL索引", "MySQL 联合索引遵循最左前缀原则，覆盖索引可避免回表查询。", ["database"]),
    ("检索_正则回溯", "正则表达式灾难性回溯会导致 CPU 飙升，需要限定重复次数上限。", ["python"]),
]

# ── 标注查询：query → 期望命中的标题集合 ────────────────────────────────
_QUERIES: list[tuple[str, set[str]]] = [
    ("Flask 部署", {"检索_Flask部署"}),
    ("gunicorn", {"检索_Flask部署"}),
    ("跨主机通信", {"检索_Docker网络"}),
    ("overlay", {"检索_Docker网络", "检索_路由器Docker"}),
    ("WAL", {"检索_SQLite并发"}),
    ("busy_timeout", {"检索_SQLite并发"}),
    ("PyInstaller", {"检索_PyQt打包"}),
    ("路由守卫", {"检索_Vue路由守卫"}),
    ("beforeEach", {"检索_Vue路由守卫"}),
    ("JWT 鉴权", {"检索_Laravel中间件"}),
    ("土壤湿度", {"检索_ESP32传感器"}),
    ("错误码 -11", {"检索_ESP32传感器"}),
    ("OpenWrt", {"检索_路由器Docker"}),
    ("统一下单", {"检索_微信小程序支付"}),
    ("wikilink", {"检索_Obsidian双链"}),
    ("stdio", {"检索_MCP服务器"}),
    ("concat demuxer", {"检索_ffmpeg合并"}),
    ("HAR 日志", {"检索_爬虫逆向"}),
    ("最左前缀", {"检索_MySQL索引"}),
    ("灾难性回溯", {"检索_正则回溯"}),
    # ── 自然语言长句：考验中文 2-gram 分词的鲁棒性（真实用户的提问形态）──
    ("怎么在路由器上跑容器", {"检索_路由器Docker"}),
    ("多进程同时读写数据库", {"检索_SQLite并发"}),
    ("把多个分片合成一个视频", {"检索_ffmpeg合并"}),
    ("小程序的支付回调怎么验签", {"检索_微信小程序支付"}),
    ("正则把 CPU 打满", {"检索_正则回溯"}),
]

# ── 负样本：主题在库里完全不存在，应返回空（衡量精确率，防止过度召回）──
_NEGATIVE_QUERIES: list[str] = [
    "量子纠缠退相干",
    "古法造纸工序",
    "NBA季后赛赛程",
]


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}   {detail}")


def section(name: str) -> None:
    print(f"\n== {name} ==")


_RANK_RE = re.compile(r"^-\s+\[[^\]]*\]\s+\*\*(.+?)\*\*", re.MULTILINE)


def ranked_titles(query: str) -> list[str]:
    """调用 memory_smart_search 并按返回顺序解析出标题列表（top-K）。"""
    out = server.memory_smart_search(query, limit=_TOPK, status="any")
    return _RANK_RE.findall(out)


# =====================================================================
section("准备 fixture 库（15 篇标注笔记）")
for _t, _body, _tags in _FIXTURES:
    server.memory_write(_t, _body, tags=_tags, mem_type="fact")
_written = sum(1 for t, _, _ in _FIXTURES if (server.MEMORY_DIR / f"{t}.md").exists())
check("Q0 fixture 全部落盘", _written == len(_FIXTURES), f"{_written}/{len(_FIXTURES)}")

# =====================================================================
section("逐条查询诊断")
_recalls: list[float] = []
_rrs: list[float] = []
_zero = 0
for _q, _expected in _QUERIES:
    _got = ranked_titles(_q)
    _hit = [t for t in _got if t in _expected]
    _recall = len(set(_hit)) / len(_expected)
    _recalls.append(_recall)
    if _hit:
        _rank = _got.index(_hit[0]) + 1
        _rrs.append(1.0 / _rank)
    else:
        _rrs.append(0.0)
    if not _got:
        _zero += 1
    _flag = "OK " if _recall >= 1.0 else "MISS"
    print(f"  {_flag} {_q:<18} top1={(_got[0] if _got else '—'):<20} "
          f"recall={_recall:.2f} hits={_hit}")

_recall_mean = sum(_recalls) / len(_recalls)
_mrr = sum(_rrs) / len(_rrs)
_zero_rate = _zero / len(_QUERIES)

# =====================================================================
section("指标与基线对比")
print(f"  recall@{_TOPK} = {_recall_mean:.3f}    MRR = {_mrr:.3f}    "
      f"零结果率 = {_zero_rate:.3f}    （基线: ≥{BASELINE_RECALL} / ≥{BASELINE_MRR} / "
      f"≤{BASELINE_ZERO_RATE}）")

check(f"Q1 零结果率不高于基线（{_zero_rate:.3f} ≤ {BASELINE_ZERO_RATE}）",
      _zero_rate <= BASELINE_ZERO_RATE,
      f"共 {_zero} 条查询无任何命中")
check(f"Q2 recall@{_TOPK} 不低于基线（{_recall_mean:.3f} ≥ {BASELINE_RECALL}）",
      _recall_mean >= BASELINE_RECALL,
      f"低召回查询={[_QUERIES[i][0] for i, r in enumerate(_recalls) if r < 1.0][:5]}")
check(f"Q3 MRR 不低于基线（{_mrr:.3f} ≥ {BASELINE_MRR}）",
      _mrr >= BASELINE_MRR,
      f"MRR 贡献低的查询={[_QUERIES[i][0] for i, r in enumerate(_rrs) if r < 0.5][:5]}")

# 结果条数上限与去重：单独断言，避免指标掩盖这类结构性问题
_dup_query = "overlay"
_dup_titles = ranked_titles(_dup_query)
check("Q4 返回结果无重复标题", len(_dup_titles) == len(set(_dup_titles)),
      f"{_dup_titles}")
check(f"Q5 返回条数不超过 limit", len(_dup_titles) <= _TOPK, f"{len(_dup_titles)}")

# 负样本：主题不存在时必须返回空，否则说明检索在「过度召回」
_false_positives = {q: ranked_titles(q) for q in _NEGATIVE_QUERIES}
_fp_bad = {q: t for q, t in _false_positives.items() if t}
check("Q6 负样本查询全部返回空（不过度召回）", not _fp_bad,
      f"误召回={_fp_bad}")

print(f"\n==== 结果: {len(_passed)} passed / {len(_failed)} failed ====")
if _failed:
    print("失败项:")
    for f in _failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
