"""记忆条目访问等级自动判定（v3 · security / clearance）。

## 为什么需要这个模块

v3 给每条记忆加了两个正交字段：``security``（内容敏感度，后果维度）与
``clearance``（读取门槛，执行维度）。写入方（各端 AI）此前只能靠「记得填」——
不填就默认 ``internal / reader``。后果是两类错误：

* **该保护的没保护**：一条含手机号、私密排期、明文口令的记忆被默认成
  ``internal``，于是任何拿到 reader 只读共享令牌的设备（含非本人手机）都能读。
* **保护过度**：把工程文档里「令牌签发」「凭据盘点」这类**词**当成敏感内容，
  手工标成 sensitive，反而让 reader 端看不到正常的运维笔记。

本模块把判定收敛成一份**可执行的规则**（而不是散落在各端 prompt 里的口头约定）：
写入时由服务端统一判定，判定结果带 ``reasons`` 以便审计与人工推翻。

## 判定哲学：宁严勿宽，但只在有证据时收紧

* **不猜语义**：只有出现「实值证据」（真凭据串、手机号、身份证号、银行卡号）
  才自动升档；关键词只做加权，单独出现不触发敏感。
* **只收紧不放宽**：``server`` 侧合并时取 ``max(现值, 建议值)``。规则判 internal
  而条目已显式标 sensitive ⇒ **保持** sensitive，不会因为规则「没看出来」而降级。
* **核心页豁免**：``记忆索引`` / ``近期工作动态`` 等导航页内容天然混杂（项目摘要
  里会引用「密钥」「口令」等词），即使命中关键词也固定 internal，避免一条误判把
  整库导航对 reader 端藏掉。
* **可解释**：每条判定都带 reasons，``memory_access_suggest`` 直接展示，主人可以
  据此推翻单条而不必读代码。

## 与 yaml_io 的关系

``security`` 决定 ``clearance`` 的**下限**（``SECURITY_MIN_CLEARANCE``），这条
下限规则只存在于 ``yaml_io``。本模块不复制它，判定结果直接查那张表 —— 两处
各存一份规则，早晚会分叉。故本模块只负责「security 该怎么定」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from yaml_io import CLEARANCE_RANK, SECURITY_MIN_CLEARANCE, SECURITY_RANK

# 判定档次：拿不准就 internal —— 这是全库 99% 条目的合理归宿。
PUBLIC = "public"
INTERNAL = "internal"
SENSITIVE = "sensitive"
SECRET = "secret"

# ---- 一、凭据实值（命中即 secret） ---------------------------------------
# 这里只认**能直接拿去登录的串**。刻意不把「密钥」「token」这类**词**列进来：
# 库里 100+ 条运维笔记在讨论令牌签发/轮换，把词当证据会让半个库变成 secret。
_SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "OpenAI 风格密钥（sk-…）"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "GitHub PAT（ghp_…）"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub fine-grained PAT"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "Slack token（xox…）"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS Access Key（AKIA…）"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "Google API Key（AIza…）"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PEM 私钥块"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
     "JWT 令牌串"),
)

# 赋值型凭据：`password=xxx` / `token: xxx`。值必须**像真值**才算 —— 文档里
# 满屏 `password: <YOUR_PASSWORD>` / `token=xxx` / `sk-test`，那些是占位符。
_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b"
    r"\s*[:=：]\s*([\"']?)([^\s\"',;，。；]{6,})\2"
)

# 占位值指纹（小写比较）。命中即**不**算凭据实值。
_PLACEHOLDER_MARKERS = (
    "xxx", "***", "...", "…", "<", ">", "{", "}", "$", "%", "your", "example",
    "placeholder", "changeme", "change_me", "redacted", "sk-test", "sk-demo",
    "明文", "占位", "示例", "同上", "见上", "略", "空", "token", "password",
)

# ---- 二、PII 实值（命中即 sensitive） ------------------------------------
# 这些是「读出去就有现实后果」的串，且误报率极低 —— 手机号做了前后非数字
# 断言（避免从长数字串里截出 11 位），身份证/银行卡同理。
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "手机号"),
    (re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Za-z])"), "身份证号"),
    (re.compile(r"(?<!\d)(?:62\d{14,17}|4\d{15}|5[1-5]\d{14})(?!\d)"), "银行卡号"),
)

# ---- 三、关键词：分「触发」与「提示」两类 ---------------------------------
# 为什么不能只有一张词表：两者误报代价完全不同。
#
#   * ``_TRIGGER_KEYWORDS``：词本身就意味着**持有**敏感物（私钥、凭据盘点、明文口令）。
#     正常工程文档不会无指代地成篇出现，命中即 +2（达到阈值）。
#   * ``_HINT_KEYWORDS``：常见于**策略讨论**（「避免隐私泄露」「权限门禁」「排期」）。
#     早期把它们计分，实测 762 条里有 8 条纯工程文档被误升为 sensitive —— 代价是
#     只读端读不到正常运维笔记。现在它们只进 ``reasons`` 供人复核，**不计分**。
#
# 阈值：PII 实值本身 2 分；单个触发词 2 分；敏感标签 2 分。
_TRIGGER_KEYWORDS = ("明文密码", "明文口令", "凭据盘点", "弱口令", "账号密码清单",
                     "私钥", "IMEI")
_HINT_KEYWORDS = ("私密", "隐私", "不雅", "身份证", "护照", "银行卡", "病历",
                  "诊断", "体检", "工资", "薪资", "社保", "家庭住址", "门禁",
                  "行程", "排期", "泄露", "裸")
# 标签命中即 sensitive（写入方显式声明比任何正则都可靠）
_SENSITIVE_TAGS = frozenset({
    "privacy", "private", "pii", "sensitive", "secret", "私密", "隐私",
    "个人信息", "敏感",
})
# 标签命中即 public 候选（仍需无敏感证据）
_PUBLIC_TAGS = frozenset({
    "public", "open", "opensource", "demo", "template", "example", "sample",
    "模板", "演示", "公开",
})
# 标题里的公开线索：只对「连通性测试 / 示例 / 模板」这类确定物生效
_PUBLIC_TITLE_HINTS = ("连通性测试", "连通测试", "演示", "示例", "模板", "README")

# 阈值：弱信号凑够 2 分才算 sensitive；PII 实值本身 2 分。
_SENSITIVE_THRESHOLD = 2

# 核心页标题（与 server.CORE_PAGES 同源；由调用方传入，避免模块循环依赖）
_DEFAULT_CORE_PAGES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Verdict:
    """一次判定的结果。``reasons`` 面向人，``evidence`` 面向审计与回归测试。"""

    security: str
    clearance: str
    score: int
    reasons: tuple[str, ...]
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "security": self.security,
            "clearance": self.clearance,
            "score": self.score,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
        }


def _contains_placeholder(value: str) -> bool:
    low = value.lower()
    if any(marker in low for marker in _PLACEHOLDER_MARKERS):
        return True
    # 全角/中文占位、纯重复字符（xxxxxx）都不像真凭据
    if re.fullmatch(r"(.)\1{3,}", value):
        return True
    return False


def _scan_secret_values(text: str) -> list[str]:
    """返回命中的**凭据实值**描述；空列表表示没有硬证据。"""
    hits: list[str] = []
    for pattern, label in _SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            hits.append(label)
    for match in _ASSIGN_RE.finditer(text):
        value = match.group(3)
        if _contains_placeholder(value):
            continue
        # 值要足够「随机」：至少 8 字符且含字母或数字，纯中文短语不算
        if len(value) < 8 or not re.search(r"[A-Za-z0-9]", value):
            continue
        hits.append(f"赋值型凭据（{match.group(1)}）")
        break  # 同一类只报一次，避免理由刷屏
    return hits


def _scan_pii(text: str) -> list[str]:
    hits: list[str] = []
    for pattern, label in _PII_PATTERNS:
        if pattern.search(text):
            hits.append(f"{label}实值")
    return hits


def _scan_keywords(text: str) -> tuple[int, list[str], list[str]]:
    """返回 ``(触发分, 触发理由, 提示理由)``。

    提示词不进分数 —— 它们多半出现在「讨论敏感」而不是「持有敏感」的句子里。
    """
    score = 0
    triggers: list[str] = []
    hints: list[str] = []
    for word in _TRIGGER_KEYWORDS:
        if word in text:
            score += 2
            triggers.append(f"关键词「{word}」")
    for word in _HINT_KEYWORDS:
        if word in text:
            hints.append(f"提示词「{word}」")
    return score, triggers, hints


def _norm_tags(tags: Iterable[str] | None) -> set[str]:
    return {str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()}


def classify_access(title: str, content: str, tags: Sequence[str] | None = None, *,
                    core_pages: Iterable[str] | None = None) -> Verdict:
    """按标准给出 (security, clearance) 建议。

    判定的**唯一入口**：``server.memory_write``（新建/缺字段条目）与
    ``memory_access_suggest``（批量预览/回填）都调它，因此「预览所见」与
    「写入所得」天然一致 —— 与 heat_suggest 的 ``_heat_tier_decision`` 同源设计。

    Args:
        title: 条目标题（核心页豁免按它匹配）。
        content: 正文。**不含 frontmatter**（判定不该被旧档位自我强化）。
        tags: 已有标签；敏感/公开标签直接参与判定。
        core_pages: 核心页标题集合，命中者固定 internal/reader。

    规则顺序（先严后宽，命中即返回）：
        1. 核心页 → internal / reader
        2. 凭据实值 → secret / owner
        3. PII 实值 / 敏感标签 / 触发词（私钥、凭据盘点、明文口令…）→ sensitive / member
           提示词（私密、隐私、门禁、泄露、排期…）只进 reasons，**不计分**
        4. 公开标签 / 公开标题线索且无敏感证据 → public / guest
        5. 其余 → internal / reader
    """
    title = str(title or "")
    content = str(content or "")
    tag_set = _norm_tags(tags)
    core = set(core_pages) if core_pages is not None else set(_DEFAULT_CORE_PAGES)

    if title in core:
        return Verdict(INTERNAL, SECURITY_MIN_CLEARANCE[INTERNAL], 0,
                       ("核心页豁免（导航/身份页，内容天然混杂，不参与自动分级）",))

    text = f"{title}\n{content}"

    secret_hits = _scan_secret_values(text)
    if secret_hits:
        return Verdict(SECRET, SECURITY_MIN_CLEARANCE[SECRET], 10,
                       tuple(f"命中{hit}" for hit in secret_hits),
                       ("secret-value",))

    pii_hits = _scan_pii(text)
    kw_score, kw_hits, hint_hits = _scan_keywords(text)
    tag_hits = sorted(tag_set & _SENSITIVE_TAGS)

    score = 2 * len(pii_hits) + kw_score + (2 if tag_hits else 0)
    reasons: list[str] = list(pii_hits) + kw_hits
    if tag_hits:
        reasons.append("敏感标签：" + "、".join(tag_hits))
    if score >= _SENSITIVE_THRESHOLD:
        return Verdict(SENSITIVE, SECURITY_MIN_CLEARANCE[SENSITIVE], score,
                       tuple(reasons), ("pii" if pii_hits else "keyword/tag",))

    public_hits = sorted(tag_set & _PUBLIC_TAGS)
    title_open = any(hint in title for hint in _PUBLIC_TITLE_HINTS)
    if public_hits or title_open:
        lead = "公开标签：" + "、".join(public_hits) if public_hits else "标题含公开线索"
        return Verdict(PUBLIC, SECURITY_MIN_CLEARANCE[PUBLIC], score, (lead,),
                       ("public-tag/title",))

    # 提示词只在这里出现：判成 internal 的同时告诉审计者「这些词出现过，但不足以
    # 证明持有敏感物」。把它们计分会把「讨论隐私」误判成「持有隐私」。
    reasons = tuple(reasons + hint_hits) or ("无敏感实值证据，默认内部级",)
    return Verdict(INTERNAL, SECURITY_MIN_CLEARANCE[INTERNAL], score, reasons,
                   ("default",))


def merge_with_current(verdict: Verdict, current_security: str | None,
                       current_clearance: str | None) -> tuple[str, str, bool]:
    """把判定结果与条目**现值**合并，返回 ``(security, clearance, 是否收紧)``。

    规则：**只收紧不放宽**。

    * ``security`` 取现值与建议值中更严的一档；现值非法/缺失时才采用建议值。
    * ``clearance`` 取现值与「security 下限」中更严的一档 —— 复刻
      ``yaml_io.normalize_access`` 的下限语义，但**不**把现值往下压：
      现值更高（例如手工标了 owner）必须保留。

    「收紧」标记用于返回提示与审计清单：只有真正变严的写入才值得打扰主人。
    """
    sec_now = current_security if current_security in SECURITY_RANK else None
    clr_now = current_clearance if current_clearance in CLEARANCE_RANK else None

    if sec_now is None:
        sec = verdict.security
    else:
        sec = sec_now if SECURITY_RANK[sec_now] >= SECURITY_RANK[verdict.security] else verdict.security

    floor = SECURITY_MIN_CLEARANCE[sec]
    if clr_now is None:
        clr = floor
    else:
        clr = clr_now if CLEARANCE_RANK[clr_now] >= CLEARANCE_RANK[floor] else floor

    tightened = ((sec_now is not None and SECURITY_RANK[sec] > SECURITY_RANK[sec_now])
                 or (clr_now is not None and clr != clr_now))
    return sec, clr, tightened
