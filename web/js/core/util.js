/* ======================================================================
 * 3. 工具函数
 * ==================================================================== */
function fmtNum(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  return Number(n).toLocaleString("zh-CN");
}
function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  var u = ["B", "KB", "MB", "GB"],
    i = 0,
    v = Number(n);
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
}
function relTime(s) {
  if (!s) return "";
  var d = new Date(String(s).replace(" ", "T"));
  if (isNaN(d.getTime())) return String(s).slice(0, 16);
  var sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 60) return "刚刚";
  if (sec < 3600) return Math.floor(sec / 60) + " 分钟前";
  if (sec < 86400) return Math.floor(sec / 3600) + " 小时前";
  if (sec < 86400 * 30) return Math.floor(sec / 86400) + " 天前";
  return String(s).slice(0, 10);
}
/* ======================================================================
 * 调试台 · 展示层汉化表（2026-10-14）
 *
 * 只作用于 Web 控制台的**展示层**：MCP 侧的工具元数据（name / description /
 * inputSchema）保持英文原样不动 —— 因为那是**所有** MCP 客户端（WorkBuddy /
 * Claude / Codex / DSH）与 AI 共读的接口契约，改它会波及 AI 的工具选择上下文。
 *
 * 工具名仍然显示英文（终端日志、`$ memory_stats` 等要靠它定位真实调用），
 * 只在详情卡头另外挂一个中文名 pill；**描述、参数标签、类型标记**全部中文。
 * 表里没有的工具自动回退到服务端原文，不会显示空白。
 * ==================================================================== */
var TOOL_ZH = {
  memory_archive: ["归档记忆", "把整篇内容移入 .archive/，原位只留一条摘要存根。"],
  memory_archive_old: ["批量归档", "把超过 N 天未更新的条目移入归档。"],
  memory_audit: ["全库体检", "扫描全库，指出该建索引或该手动清理之处；含 frontmatter 统计看不到的文件系统级问题。"],
  memory_batch_tag: ["批量改标签", "把全库的某个标签统一改名。"],
  memory_batch_tier: ["批量改层级", "按热度分数区间批量设置 tier。"],
  memory_context: ["上下文组装", "围绕一条记忆组装可直接喂给模型的可读上下文块，并标注来源与关系。"],
  memory_delete: ["删除记忆", "按标题精确删除（旧文件名可回退匹配）；默认移入 .trash/ 而非直接抹除。"],
  memory_doctor: ["索引体检", "只检查 SQLite 索引与磁盘是否一致，不做任何修复。"],
  memory_graph: ["关系图谱", "列出某条记忆的出链，以及哪些页面反向链接到它。"],
  memory_heat_suggest: ["热度调层建议", "按访问次数与陈旧度建议 tier 调整；默认只预览、不改数据。"],
  memory_index_draft: ["索引草稿", "生成主索引的草稿，不自动写回。"],
  memory_list: ["列出记忆", "列出记忆条目并附简短预览，可按类型 / 作用域 / 项目过滤。"],
  memory_orphans: ["孤立笔记", "找出没有任何反向链接的孤立笔记，可输出 text 或 json。"],
  memory_read: ["读取记忆", "按标题精确读取（旧文件名可回退匹配），可按 max_chars 截断以控制 token。"],
  memory_rebuild_links: ["链接校验", "只读校验全库正文双链，报告死链与计数，不改任何文件。"],
  memory_recent: ["最近更新", "列出最近 N 天内更新的条目，最新的排在最前。"],
  memory_restart: ["重启服务", "就地重启 MCP 服务，让改过的代码立刻生效，无需重连客户端。"],
  memory_restore: ["从归档恢复", "把条目从 .archive/ 搬回库根，替换归档存根并把 tier 还原。"],
  memory_schema_diff: ["结构差异", "对比全库 frontmatter 的结构差异；只推断，绝不修复。"],
  memory_search: ["检索记忆", "按关键词在标题、标签、正文中检索，最近优先。"],
  memory_smart_search: ["智能检索", "对标题、标签、摘要、正文做多字段加权评分检索。"],
  memory_stats: ["库统计", "查看记忆库的健康度统计。"],
  memory_update_metadata: ["改元数据", "只改条目的 frontmatter 元数据，不重写正文。"],
  memory_write: ["写入记忆", "写入一条记忆；标题相同则就地更新。标题含 / 会落到分类子目录。"],
};
/* 参数字段名 → 中文标签（服务端 inputSchema 的 properties 没有 description，故只能在此映射）。 */
var ARG_ZH = {
  title: "标题",
  limit: "数量上限",
  mem_type: "记忆类型",
  scope: "作用域",
  project: "项目",
  status: "生命周期状态",
  output_format: "输出格式",
  tag: "标签",
  tier: "热度层级",
  source: "来源",
  days: "天数",
  max_chars: "最大字符数",
  tags: "标签列表",
  summary: "摘要",
  confidence: "可信度",
  verified: "是否已核验",
  supersedes: "取代的条目",
  conflicts: "冲突条目",
  source_context: "写入方上下文",
  old_tag: "原标签",
  new_tag: "新标签",
  target_tier: "目标层级",
  min_score: "分数下限",
  max_score: "分数上限",
  depth: "展开层数",
  max_related: "相关条目上限",
  timeframe: "时间范围",
  trash: "移入回收站",
  purge: "彻底删除",
  empty_trash: "清空回收站",
  include_all: "包含全部",
  apply: "实际执行",
  include_trash: "包含回收站",
  delay: "延迟秒数",
  keyword: "关键词",
  query: "检索词",
  content: "正文",
  expected_version: "期望版本",
};
var KIND_ZH = {
  select: "单选",
  bool: "开关",
  number: "数字",
  list: "列表",
  json: "JSON",
  textarea: "多行文本",
  text: "文本",
};
function toolZhName(n) {
  var z = TOOL_ZH[n];
  return z ? z[0] : "";
}
function toolZhDesc(n) {
  var z = TOOL_ZH[n];
  return z ? z[1] : "";
}
function argZh(k) {
  return ARG_ZH[k] || k;
}
function kindZh(k) {
  return KIND_ZH[k] || k;
}
/* 显示宽度：CJK / 全角记 2，其余记 1（用于按视觉宽度而非字符数截断）。 */
function dispWidth(s) {
  var w = 0;
  for (var i = 0; i < s.length; i++) w += s.charCodeAt(i) > 0x2e80 ? 2 : 1;
  return w;
}
function cutWidth(s, max) {
  var w = 0;
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i) > 0x2e80 ? 2 : 1;
    if (w + c > max) return s.slice(0, i) + "…";
    w += c;
  }
  return s;
}
function shortDesc(s) {
  if (!s) return "";
  var one = String(s)
    .split(/\n\s*\n/)[0]
    .replace(/\s+/g, " ")
    .trim();
  /* 按**显示宽度**截断：原来按 96 字符截，对中文等于放行 96 个汉字（近两行），
     列表项会被撑爆；统一 96 宽度后中文约 48 字、英文行为不变。 */
  return cutWidth(one, 96);
}
function tryJson(text) {
  if (typeof text !== "string") return null;
  var t = text.trim();
  if (!t || (t[0] !== "{" && t[0] !== "[")) return null;
  try {
    return JSON.parse(t);
  } catch (e) {
    return null;
  }
}
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Markdown → 安全 HTML（本地 marked + DOMPurify，并把 [[双链]] 变成可点链接） */
var mdReady = !!(window.marked && window.DOMPurify);
if (mdReady) {
  window.marked.setOptions({
    gfm: true,
    breaks: false,
    headerIds: false,
    mangle: false,
  });
}
function mdRender(src) {
  if (!src) return "";
  if (!mdReady) return '<pre class="code">' + escapeHtml(src) + "</pre>";
  var html;
  try {
    html = window.marked.parse(String(src));
  } catch (e) {
    return '<pre class="code">' + escapeHtml(src) + "</pre>";
  }
  html = html.replace(/\[\[([^\[\]<>]{1,120})\]\]/g, function (_, t) {
    return (
      '<span class="wikilink" data-title="' +
      escapeHtml(t.trim()) +
      '">' +
      t.trim() +
      "</span>"
    );
  });
  try {
    return window.DOMPurify.sanitize(html, {
      ADD_ATTR: ["data-title", "class"],
      FORBID_TAGS: ["style", "script", "iframe"],
    });
  } catch (e) {
    return '<pre class="code">' + escapeHtml(src) + "</pre>";
  }
}

/* memory_stats 文本 → 结构化数字（口径：只抓关键行，抓不到就显示 —） */
function grab(text, re) {
  var m = String(text || "").match(re);
  return m ? Number(m[1]) : null;
}
function parseStats(text) {
  var t = String(text || "");
  var sec = {},
    cur = null;
  t.split("\n").forEach(function (line) {
    var hd = line.match(/^##\s+(.*)$/);
    if (hd) {
      cur = hd[1].trim();
      sec[cur] = [];
      return;
    }
    if (cur && /^\s*-\s+/.test(line)) {
      var body = line.replace(/^\s*-\s+/, "").trim();
      var kv = body.match(/^(.*?)[:：]\s*(\d+)\s*条?$/);
      if (kv) sec[cur].push({ label: kv[1].trim(), value: Number(kv[2]) });
    }
  });
  var nums = {
    total: grab(t, /总条目数[:：]\s*(\d+)/),
    hot: grab(t, /活跃 hot[:：]\s*(\d+)/),
    warm: grab(t, /常温 warm[:：]\s*(\d+)/),
    cold: grab(t, /已归档 cold[:：]\s*(\d+)/),
    verified: grab(t, /已核验 verified[:：]\s*(\d+)/),
    orphans: grab(t, /孤立笔记[:：]\s*(\d+)/),
    links: grab(t, /总双向链接数[:：]\s*(\d+)/),
    neverRead: grab(t, /从未读取[:：]\s*(\d+)/),
    recent7: grab(t, /近 7 天更新[:：]\s*(\d+)/),
    recent30: grab(t, /近 30 天更新[:：]\s*(\d+)/),
    recent90: grab(t, /近 90 天更新[:：]\s*(\d+)/),
  };
  var readTop = [];
  var rm = t.match(/##\s*访问频率[\s\S]*?(?=\n##\s|$)/);
  if (rm) {
    (rm[0].match(/^\s*\d+\.\s*\[\[(.+?)\]\]\s*—\s*(\d+)/gm) || []).forEach(
      function (l) {
        var mm = l.match(/\[\[(.+?)\]\]\s*—\s*(\d+)/);
        if (mm) readTop.push({ title: mm[1], reads: Number(mm[2]) });
      },
    );
  }
  return { nums: nums, sections: sec, readTop: readTop, raw: t };
}
function sectionRows(parsed, prefix) {
  var out = [];
  Object.keys(parsed.sections || {}).forEach(function (k) {
    if (k.indexOf(prefix) === 0) out = out.concat(parsed.sections[k]);
  });
  return out.filter(function (r) {
    return r.value > 0;
  });
}

/* memory_search 文本 → 可点条目 */
function parseSearch(text) {
  var items = [];
  String(text || "")
    .split("\n")
    .forEach(function (line) {
      var m = line.match(/^-\s+\[(.+?)\]\s+\((.+?)\)\s*(.*)$/);
      if (m)
        items.push({
          title: m[1].trim(),
          file: m[2].trim(),
          meta: m[3].trim(),
        });
    });
  return items;
}

export { fmtNum, fmtBytes, relTime, TOOL_ZH, ARG_ZH, KIND_ZH, toolZhName, toolZhDesc, argZh, kindZh, dispWidth, cutWidth, shortDesc, tryJson, escapeHtml, mdReady, mdRender, grab, parseStats, sectionRows, parseSearch };
