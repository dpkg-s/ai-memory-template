import { api } from "../core/api.js";
import { S, askConfirm, toast } from "../core/store.js";
import { argZh, shortDesc } from "../core/util.js";

/* ======================================================================
 * 9. 调试台
 * ==================================================================== */
/* ======================================================================
 * 9.1 工具危险等级（原「工具属性分组」，2026-10-15 方案 B 降级）
 *
 * 🔴 语义已变：这里**不再是分组轴**，只是**危险等级**。
 * 2026-10-14 曾拿它当左栏的一级分组，实测四组分布 14 : 4 : 4 : 2
 * （「只读」独占 58%，最大:最小 = 7:1）—— 分组几乎不降低查找成本，且用户找
 * 工具的心智是「我要干什么」而不是「这操作危不危险」。
 * 现改为：一级分组走**功能用途**（服务端 `web_console.py` 的 `TOOL_GROUPS`，
 * 随 `/api/tools` 的 `groups` 一起下发），等级降级为工具行内的一个小 pill
 * 加顶部筛选条。
 *
 * 等级仍由服务端 MCP `ToolAnnotations` 派生（`/api/tools` 原样透传，实测
 * 24/24 都带 annotations），严格对应 `server.py` 的四个注解常量：
 *   _RO（readOnlyHint）→ read        · _WRITE（幂等写）→ write
 *   _DESTRUCTIVE       → destructive · _WRITE_NONIDEM（非幂等）→ ops
 * 判序按 MCP 语义：只读优先，其次破坏性，再次「非幂等」，兜底归写入。
 * ==================================================================== */
var ATTRS = [
  { key: "read", label: "只读", desc: "只读：不改动任何文件" },
  { key: "write", label: "写入", desc: "幂等写：可重复调用、结果一致" },
  { key: "destructive", label: "破坏性", desc: "会删除或移动内容，调用前二次确认" },
  { key: "ops", label: "非幂等", desc: "非幂等：重复调用结果不同" },
];
function attrOf(tool) {
  var a = (tool && tool.annotations) || {};
  if (a.readOnlyHint) return "read";
  if (a.destructiveHint) return "destructive";
  if (a.idempotentHint === false) return "ops";
  return "write";
}
/* 行内等级徽章：等级不再占分组层级，降为工具名后一个小 pill。
   🔴「只读」返回 null **不显示** —— 它占 14/24，是默认预期，全标出来只是噪音；
      要专门挑只读工具，用顶部 `attr-chip` 筛选条。
   返回 null 时模板 `v-if` 不渲染该 pill（Vue 的 v-if 优先于属性绑定，
   不会去求值 `.kind`，所以不必额外判空）。 */
function attrPill(tool) {
  var k = attrOf(tool);
  if (k === "read") return null;
  if (k === "destructive") return { kind: "err", text: "破坏" };
  if (k === "ops") return { kind: "warn", text: "非幂等" };
  return { kind: "", text: "写" };
}

/* 需要**二次确认**的工具 —— 这是前端交互策略，与上面的等级是两回事。
   = 服务端 `_DESTRUCTIVE` 的 4 个 + `memory_batch_tier`。
   🔴 为什么多一个 `memory_batch_tier`：它按热度分数区间**批量改写全库 tier**
      （`server.py` 的 `memory_batch_tier` 会逐篇 `_write_memory` 落盘），影响面
      够大、误伤代价高，所以即便服务端把它标成「幂等写」，前端仍要拦一道。
      这是**有意为之的保守策略**，不是注解的镜像 —— 两者职责不同：
        ATTRS / attrOf   = 工具固有属性（回答「它是什么」）
        CONFIRM_TOOLS    = 前端确认门槛（回答「点下去要不要拦」）
      关系：服务端破坏性(4) ⊂ CONFIRM_TOOLS(5)，并不矛盾。
   ⚠️ 不要再让行内徽章或分组去读本数组 —— 那正是 2026-10-14 留下的双事实源
      （`memory_batch_tier` 在「写入」组里却挂红色「写」pill，自相矛盾）。 */
var CONFIRM_TOOLS = [
  "memory_delete",
  "memory_archive",
  "memory_archive_old",
  "memory_batch_tier",
  "memory_batch_tag",
];

function fieldsOf(tool) {
  if (!tool) return [];
  var sch = tool.inputSchema || {};
  var props = sch.properties || {};
  var required = sch.required || [];
  return Object.keys(props).map(function (key) {
    var p = props[key] || {};
    var kind = "text";
    if (p.enum && p.enum.length) kind = "select";
    else if (p.type === "boolean") kind = "bool";
    else if (p.type === "integer" || p.type === "number") kind = "number";
    else if (p.type === "array")
      kind = p.items && p.items.type === "string" ? "list" : "json";
    else if (p.type === "object") kind = "json";
    else if (key === "content" || key === "body" || key === "text")
      kind = "textarea";
    return {
      key: key,
      kind: kind,
      required: required.indexOf(key) >= 0,
      schema: p,
      label: argZh(key),
      desc: p.description ? shortDesc(p.description) : "",
      placeholder:
        p.examples && p.examples.length ? String(p.examples[0]) : "",
      enumValues: p.enum || [],
      def: p.default !== undefined ? p.default : kind === "bool" ? false : "",
    };
  });
}
function selectTool(tool) {
  S.console.selected = tool;
  S.console.error = null;
  S.console.result = null;
  if (!S.console.args[tool.name]) {
    var init = {};
    fieldsOf(tool).forEach(function (f) {
      init[f.key] = f.def;
    });
    S.console.args[tool.name] = init;
  }
  S.console.view = "rendered";
  // 换工具 = 回到「工具最高高度」的默认态（方案 E）：手动展开标记清零。
  S.console.paramOpen = false;
}
function buildArgs(tool) {
  var store = S.console.args[tool.name] || {};
  var out = {};
  var fields = fieldsOf(tool);
  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    var v = store[f.key];
    if (f.kind === "bool") {
      if (v === true || f.required) out[f.key] = !!v;
      continue;
    }
    if (v === "" || v === null || v === undefined) {
      if (f.required) out[f.key] = f.kind === "number" ? 0 : "";
      continue;
    }
    if (f.kind === "number") {
      var n = Number(v);
      if (isNaN(n)) throw new Error("参数 " + f.key + " 不是数字：" + v);
      out[f.key] = n;
    } else if (f.kind === "list") {
      var raw = String(v).trim();
      if (raw[0] === "[") out[f.key] = JSON.parse(raw);
      else
        out[f.key] = raw
          .split(/[\n,，]/)
          .map(function (s) {
            return s.trim();
          })
          .filter(Boolean);
    } else if (f.kind === "json") {
      out[f.key] = JSON.parse(String(v));
    } else {
      out[f.key] = v;
    }
  }
  return out;
}
/* 把参数拼成命令行回显（终端块与日志共用），**只做展示**、不参与请求构造。 */
function argLine(args) {
  return Object.keys(args || {})
    .map(function (k) {
      var v = args[k];
      if (v === "" || v === null || v === undefined) return "";
      if (typeof v === "boolean") return v ? "--" + k : "--no-" + k;
      if (Array.isArray(v) || typeof v === "object")
        return "--" + k + " '" + JSON.stringify(v) + "'";
      return "--" + k + ' "' + v + '"';
    })
    .filter(Boolean)
    .join(" ");
}
/* 记一条日志 —— 日志检查器与终端块共用的**唯一**入口。 */
function pushLog(kind, tool, args, ok, ms, text) {
  var c = S.console;
  c.logs.unshift({
    id: Math.random().toString(36).slice(2),
    ts: Date.now(),
    kind: kind,
    tool: tool,
    args: args || {},
    cmd: argLine(args),
    ok: !!ok,
    ms: ms,
    text: String(text || "")
      .replace(/\s+/g, " ")
      .slice(0, 160),
    /* 底部面板「调试控制台」页签要看原始返回，这里留一份较长的副本；
       仍设上限，避免 80 条 × 无限长把内存拖爆。 */
    full: String(text || "").slice(0, 4000),
  });
  if (c.logs.length > 80) c.logs.pop();
}
function runTool() {
  var tool = S.console.selected,
    c = S.console;
  if (!tool) return;
  var args;
  try {
    args = buildArgs(tool);
  } catch (e) {
    toast("err", "参数有误", e.message);
    return;
  }
  var fire = function () {
    c.running = true;
    c.error = null;
    c.result = null;
    /* 执行开始即切「执行后」形态：3 : 7 + 结果展开 / 日志流折起。
       失败也走这里（catch 里不再重复设）⇒ 三种结局（成功 / 工具返错 / 调用失败）
       的布局一致，不会出现「失败时比例没切」的分叉。
       注意：两个 bool **各自独立**（主人 2026-10-14 改定），这里只折日志、不动结果。 */
    c.foldLog = true;
    c.foldResult = false;
    // 「运行后收纳一点」（方案 E）：参数段回到被收纳的状态，清掉手动展开标记。
    c.paramOpen = false;
    var t0 = performance.now();
    api
      .post("/api/call", { tool: tool.name, arguments: args })
      .then(function (d) {
        c.running = false;
        c.result = d;
        c.elapsed = Math.round(performance.now() - t0);
        c.history.unshift({
          id: Math.random().toString(36).slice(2),
          tool: tool.name,
          args: args,
          ok: !d.isError,
          ms: c.elapsed,
          text: (d.text || "").slice(0, 300),
          at: Date.now(),
        });
        if (c.history.length > 10) c.history.pop();
        pushLog(
          d.isError ? "ERROR" : "TOOL_CALL",
          tool.name,
          args,
          !d.isError,
          c.elapsed,
          (d.text || "").split("\n")[0],
        );
        if (d.isError)
          toast("warn", tool.name + " 返回错误", "看结果面板的原始输出。");
      })
      .catch(function (e) {
        c.running = false;
        c.error = e.message;
        pushLog("ERROR", tool.name, args, false, null, e.message);
        if (e.status !== 401) toast("err", "调用失败", e.message);
      });
  };
  if (CONFIRM_TOOLS.indexOf(tool.name) >= 0) {
    askConfirm(
      "破坏性操作：" + tool.name,
      "这个工具会改动记忆库（删除 / 归档 / 批量改）。确认继续？",
      tool.name,
      fire,
      true,
    );
  } else {
    fire();
  }
}

export { CONFIRM_TOOLS, ATTRS, attrOf, attrPill, fieldsOf, selectTool, buildArgs, argLine, pushLog, runTool };
