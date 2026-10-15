import { computed, h, nextTick } from "../../vendor/vue.esm-browser.prod.js";
import { loadHealth, loadTools } from "../core/data.js";
import { S, toast } from "../core/store.js";
import { mdRender, toolZhDesc, toolZhName, tryJson } from "../core/util.js";
import { ATTRS, CONFIRM_TOOLS, attrOf, attrPill, fieldsOf, runTool, selectTool } from "../features/console.js";
import { typeText } from "../core/anim.js";

/* ---------------------------------------------------------------------------
 * 终端输出的「逐字打印」（2026-10-15）
 *
 * 语义边界（先想清楚「谁该打、谁不该打」，否则一进页面 80 条日志会一起打字）：
 *   · 只有**本次页面会话新到达的那条日志**才逐字打印；
 *   · 其余（刷新 / 切页重挂载后已在库里的旧日志）一律视为「早就打完」，不重播；
 *   · 日志落盘时「命令与输出」区块默认是**折起**的（`runTool()` 里 `foldLog = true`）
 *     —— 打给 `display:none` 看等于白打，故折起态下**挂起**，等主人展开时再放。
 *
 * 标记全部落在 DOM 属性上（`data-tw` = 该行所属日志 id、`data-tw-done` = 已落定），
 * 不用额外的响应式状态：重挂载后属性随 DOM 重建而自动清空，无需手动复位。
 * ------------------------------------------------------------------------- */
/* 封盖一行：标记「已落定」，并**把可能停在半截的文本补齐**。
   🔴 这一步不是洁癖：连跑两条工具时，第一条可能正打到一半就被新头条取代。
      若只 kill 补间而不补齐文本，那一行会永久停在半截 —— 而 `data-tw-done`
      又告诉后来人「它已经打完了」，故障便静默固化。补齐是唯一的收敛点。 */
function twSeal(span) {
  var full = span.getAttribute("data-full");
  if (full != null && span.textContent !== full) span.textContent = full;
  var out = span.parentNode;
  if (out && out.classList && out.classList.contains("typing")) out.classList.remove("typing");
  span.setAttribute("data-tw-done", "1");
}

function termSpan(vm) {
  var logs = S.console.logs;
  if (!logs.length) return null;
  var host = vm.$el && vm.$el.querySelector(".term-log");
  if (!host) return null;
  var spans = host.querySelectorAll(".term-out .tw");
  var i;
  /* 非头条一律封盖：连跑两条时，第一条不该在第二次展开时才「补打」出来。 */
  for (i = 0; i < spans.length; i++) {
    if (spans[i].getAttribute("data-tw") !== logs[0].id) twSeal(spans[i]);
  }
  return host.querySelector('.term-out .tw[data-tw="' + logs[0].id + '"]');
}

/* 打字期间让视口跟随「打印头」—— 只在**已经溢出**且该行确实低于可视下沿时补滚，
   否则会对一个本来不需要滚动的容器做无意义赋值（内容不溢出时也就没有副作用）。 */
function termFollow(host, el) {
  if (host.scrollHeight - host.clientHeight <= 0) return;
  var delta = el.getBoundingClientRect().bottom - (host.getBoundingClientRect().bottom - 6);
  if (delta > 0) host.scrollTop += delta;
}

function termType(vm) {
  var host = vm.$el && vm.$el.querySelector(".term-log");
  if (!host) return;
  var el = termSpan(vm);
  if (!el) return;
  var id = el.getAttribute("data-tw");
  if (el.hasAttribute("data-tw-done")) return;
  /* 已在打同一条 ⇒ 不重开（重开会把 t0 重置，文本从半截处重新起步）。 */
  if (twTween && twTweenId === id) return;
  if (S.console.foldLog) return;      /* 折起态挂起，等展开 */
  if (host.offsetParent === null) return;  /* 整页不可见（非当前路由）时不打 */

  if (twTween) {
    twTween.kill();
    twTween = null;
    twTweenId = null;
  }
  var out = el.parentNode;
  var caret = out.querySelector(".tw-caret");
  out.classList.add("typing");
  var owner = host;
  twTweenId = id;
  twTween = typeText(el, el.getAttribute("data-full") || el.textContent, {
    caret: caret,
    delay: 120,                       /* 让前两行（命令 / 状态）的错峰淡入先走完 */
    onTick: function () {
      termFollow(owner, el);
    },
    onDone: function () {
      el.setAttribute("data-tw-done", "1");
      out.classList.remove("typing");
      twTween = null;
      twTweenId = null;
    },
  });
}

/* 跨重挂载存活：`data-tw-done` 在 DOM 上，这两个只在「正在打字」时有意义。 */
var twTween = null;
var twTweenId = null;

var ConsolePage = {
  data: function () {
    return { S: S, ATTRS: ATTRS };
  },
  computed: {
    filtered: function () {
      var q = S.console.filter.trim().toLowerCase();
      if (!q) return S.tools;
      return S.tools.filter(function (t) {
        return (
          (t.name + " " + (t.title || "") + " " + (t.description || "") +
            " " + toolZhName(t.name) + " " + toolZhDesc(t.name))
            .toLowerCase()
            .indexOf(q) >= 0
        );
      });
    },
    /* 属性切片计数：**基于全部工具**（不受搜索框影响），否则一边打字一边
       看到计数跳动，无法判断「这类一共几个」。 */
    attrCounts: function () {
      var c = { all: S.tools.length };
      for (var i = 0; i < ATTRS.length; i++) c[ATTRS[i].key] = 0;
      for (var j = 0; j < S.tools.length; j++) {
        var k = attrOf(S.tools[j]);
        if (c[k] !== undefined) c[k] += 1;
      }
      return c;
    },
    /* 分组后的可见列表（2026-10-15 方案 B 改造）。
       🔴 组与**组序**全部来自服务端：`S.toolGroups` ← `/api/tools` 的 `groups`
          ← `web_console.py` 的 `TOOL_GROUPS`。前端不建分类表 —— 那会随工具数
          增长而失配（2026-10-14 的注释里已立过这条原则）。
       两段过滤：先按危险等级切片（`attr`，顶部 chip 行），再交给 `filtered`（搜索）。
       空组不出现；搜索命中后组内只剩命中项。
       服务端未升级（响应里没有 groups）时退化为**单组平铺** —— 宁可少一层，
       也不让前端自己编组名假装分组。 */
    groups: function () {
      var want = S.console.attr;
      var list = this.filtered;
      if (want !== "all") {
        list = list.filter(function (t) {
          return attrOf(t) === want;
        });
      }
      var out = [];
      var metas = S.toolGroups && S.toolGroups.length ? S.toolGroups : null;
      /* 降级：服务端未升级（响应里没有 groups）⇒ **单组平铺**。
         🔴 这里绝不能用「伪组 key 去匹配 `t.group`」的写法：工具此时**照样
         带着 group 字段**（只是组定义没拿到），任何伪 key 都匹配不上 ⇒ 渲染
         出 0 组、左栏整片空白。宁可少一层分组，也不能让列表空掉。
         实测记录：2026-10-15 探针「无 groups 时退化为单组」一组三条全红，
         暴露的正是这个写法。 */
      if (!metas) {
        if (list.length) {
          out.push({
            key: "all",
            label: "全部工具",
            desc: "",
            count: list.length,
            items: list,
            open: !S.console.collapsed.all,
          });
        }
        return out;
      }
      var seen = {};
      for (var i = 0; i < metas.length; i++) {
        var g = metas[i];
        seen[g.key] = true;
        var items = list.filter(function (t) {
          return t.group === g.key;
        });
        if (!items.length) continue;
        out.push({
          key: g.key,
          label: g.label,
          desc: g.desc || "",
          count: items.length,
          items: items,
          open: !S.console.collapsed[g.key],
        });
      }
      /* 兜底：工具带了一个不在组定义里的 group ⇒ 收进「其他」而非凭空消失。
         服务端有 `_TOOL_GROUP_OF` + other 兜底组保证两者一致，这里是防御
         「响应不完整」（缓存错配、手工改过数据等）—— 条目静默丢失远比
         多出一个组难发现。 */
      var rest = list.filter(function (t) {
        return !seen[t.group];
      });
      if (rest.length) {
        out.push({
          key: "other",
          label: "其他",
          desc: "分组信息缺失的工具",
          count: rest.length,
          items: rest,
          open: !S.console.collapsed.other,
        });
      }
      return out;
    },
    fields: function () {
      return fieldsOf(S.console.selected);
    },
    argStore: function () {
      return S.console.selected
        ? S.console.args[S.console.selected.name] || {}
        : {};
    },
    resultJson: function () {
      var r = S.console.result;
      if (!r) return null;
      if (r.structured) return r.structured;
      return tryJson(r.text) || { text: r.text };
    },
    /* 日志检查器过滤：按 kind / 工具名 / 输出文本匹配（大小写不敏感）。 */
    filteredLogs: function () {
      var q = (S.console.logFilter || "").trim().toLowerCase();
      var all = S.console.logs;
      if (!q) return all;
      return all.filter(function (l) {
        return (
          l.kind +
          " " +
          l.tool +
          " " +
          (l.text || "") +
          " " +
          l.cmd
        )
          .toLowerCase()
          .indexOf(q) >= 0;
      });
    },
    resultMarkdown: function () {
      var r = S.console.result;
      if (!r) return "";
      return tryJson(r.text) ? "```json\n" + r.text + "\n```" : r.text;
    },
    /* 「是否处于折叠态」= 至少有一个区块被折起（两个 bool 求或）。
       模板与 CSS 都只认这一个派生量，避免散落判据各写一遍。 */
    folded: function () {
      return S.console.foldLog || S.console.foldResult;
    },
    /* 「两个区块都折起」—— 2026-10-14 方案 C：此时终端卡收缩为内容高（CSS 用
       `.all-folded` 判）。与 `folded` 的区别：只折一个区块时终端卡里还有内容，
       不该收缩。 */
    allFolded: function () {
      return S.console.foldLog && S.console.foldResult;
    },
    /* 卡头「破坏性」pill：只对**点下去会被拦**的工具亮。
       判据与 `runTool()` 的二次确认门槛同源（`CONFIRM_TOOLS`）⇒「看到 pill」
       与「会被拦」永远一致，pill 的意义正是预警那道确认。
       注意别改成 `attrOf(sel)==="destructive"`：那样 `memory_batch_tier`
       （服务端标幂等写、但会被拦）就会「弹了确认却没有预警」。 */
    needsConfirm: function () {
      return (
        !!S.console.selected &&
        CONFIRM_TOOLS.indexOf(S.console.selected.name) >= 0
      );
    },
    descHtml: function () {
      var sel = S.console.selected;
      if (!sel) return "";
      return mdRender(toolZhDesc(sel.name) || sel.description);
    },
    /* 终端块首行显示真实 vault 路径。
       注意：`endpointShort` 定义在**根组件**（App）上，ConsolePage 取不到，
       直接写会渲染成空；且它的语义是 MCP 端点、不是 vault ⇒ 这里另行取值。
       ⚠️ 必须放在 `computed`：放进 `methods` 时模板 `{{ vaultShort }}` 不会被自动
       调用，会把函数源码原样渲染出来（实测渲染成 `function () { [native code] }`）。 */
    vaultShort: function () {
      return (S.about.resolved || {}).vault || "（未设置）";
    },
    /* 头条日志的 id —— 「来了新日志」的判据用 id 而不是长度：
       日志有 80 条上限（`pushLog` 超限 `pop()`），满仓后长度恒定不变。 */
    headLogId: function () {
      return S.console.logs.length ? S.console.logs[0].id : "";
    },
  },
  watch: {
    /* 新日志到达 ⇒ 逐字打印（折起态下 `termType` 自己会挂起）。 */
    headLogId: function () {
      var vm = this;
      nextTick(function () {
        termType(vm);
      });
    },
    /* 展开「命令与输出」时，补放被挂起的打印。只在**展开**方向触发。 */
    "S.console.foldLog": function (v) {
      var vm = this;
      if (v) return;
      nextTick(function () {
        termType(vm);
      });
    },
  },
  mounted: function () {
    /* 挂载即把「已在库里的日志」全部封盖 —— 切回本页时不该重播历史输出。 */
    var vm = this;
    nextTick(function () {
      var host = vm.$el && vm.$el.querySelector(".term-log");
      if (!host) return;
      var spans = host.querySelectorAll(".term-out .tw");
      for (var i = 0; i < spans.length; i++) {
        twSeal(spans[i]);
      }
    });
  },
  methods: {
    /* 暴露给模板：工具行的等级徽章（返回 null 则不渲染，见 `attrPill` 注释）。 */
    attrPill: attrPill,
    /* 重载工具清单 —— 原页头那个内联表达式按钮，页头删除后提到左栏卡片头，
       表达式原样保留（清空 → 并发拉 health + tools → 收 loading 标志）。 */
    reloadTools: function () {
      S.tools = [];
      S.toolsLoading = true;
      S.health.checking = true;
      Promise.all([loadHealth(), loadTools(true)]).then(function () {
        S.toolsLoading = false;
      });
    },
    /* 属性切片（chip 行）—— 2026-10-15 主人新规：「点击上方标签，下方自动展开」。
       旧行为只改筛选条件、折叠状态各自保留 ⇒ 一旦用户先把组折起，再点 chip 就只
       看到一堆折叠着的组头，命中结果得手动逐个点开。
       现在点 chip 即把**本次筛选后仍可见的组**展开（`this.groups` 已经按新的
       `S.console.attr` 重算，Vue 3 的 computed 在依赖变更后同步重求值）；
       未命中的组不渲染、其折叠偏好原样保留，不做全量清空。 */
    setAttr: function (k) {
      S.console.attr = k;
      var gs = this.groups;
      for (var i = 0; i < gs.length; i++) S.console.collapsed[gs[i].key] = false;
    },
    toggleGroup: function (k) {
      S.console.collapsed[k] = !S.console.collapsed[k];
    },
    copy: function (text) {
      var t = typeof text === "string" ? text : JSON.stringify(text, null, 2);
      if (navigator.clipboard) {
        navigator.clipboard.writeText(t).then(
          function () {
            toast("ok", "已复制");
          },
          function () {
            toast("err", "复制失败");
          },
        );
      }
    },
    setArg: function (k, v) {
      S.console.args[S.console.selected.name][k] = v;
    },
    rerun: function (h) {
      var t = S.tools.filter(function (x) {
        return x.name === h.tool;
      })[0];
      if (!t) return;
      selectTool(t);
      S.console.args[h.tool] = Object.assign({}, h.args);
      nextTick(function () {
        runTool();
      });
    },
    /* 区块折叠（2026-10-14 主人改定为**各自独立**）：每个区块一个 bool，互不影响；
       两边都折也合法（终端只剩两行标题，用卡头三角箭头全展）。 */
    toggleFold: function (which) {
      if (which === "log") S.console.foldLog = !S.console.foldLog;
      else S.console.foldResult = !S.console.foldResult;
    },
    /* 卡头三角箭头：一键把两个折叠态清零（点区块内容区也行，见 restoreAuto）。 */
    expandAll: function () {
      S.console.foldLog = false;
      S.console.foldResult = false;
    },
    /* 点终端卡体（空白处）⇒ 恢复自适应。等价 expandAll，但只在**真的处于折叠态**
       时写状态，避免每次点空白都触发一次无谓的响应式更新。 */
    restoreAuto: function () {
      if (S.console.foldLog || S.console.foldResult) {
        S.console.foldLog = false;
        S.console.foldResult = false;
      }
    },
    /* 时间戳 → 本地 HH:MM:SS（终端块与日志检查器共用）。 */
    clock: function (ts) {
      var d = new Date(ts || Date.now());
      var p = function (n) {
        return (n < 10 ? "0" : "") + n;
      };
      return (
        p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds())
      );
    },
    /* 日志类型 → 中文。原始 kind（TOOL_CALL / ERROR）仍保留，过滤逻辑依赖原值。 */
    kindLabel: function (k) {
      return k === "ERROR" ? "错误" : k === "TOOL_CALL" ? "调用" : k;
    },
  },
  template:
    '<div class="page">' +
    '  <x-banner kind="err" icon="alert" v-if="S.toolsError">' +
    '    <div><b>拿不到工具清单</b><div class="small">{{ S.toolsError }}</div></div></x-banner>' +
    '  <div class="console-grid">' +
    '    <x-card bare class="con-rail">' +
    '      <div class="card-head"><h3>工具</h3>' +
    '        <span class="spacer"></span>' +
    '        <x-pill mono>{{ S.tools.length }}</x-pill>' +
    '        <button class="btn btn-sm" title="重新拉取工具清单" @click="reloadTools">' +
    "          <span v-html=\"svg('refresh')\"></span>重载</button></div>" +
    '      <div class="card-body tight">' +
    '        <div class="tool-search"><span v-html="svg(\'search\')"></span>' +
    '          <input class="input" v-model="S.console.filter" placeholder="过滤工具名 / 描述…"></div>' +
    '        <div class="attr-bar stagger-in">' +
    '          <button class="attr-chip" style="--i:0" :class="{ on: S.console.attr === \'all\' }" @click="setAttr(\'all\')">' +
    "            全部<b>{{ S.tools.length }}</b></button>" +
    '          <button class="attr-chip" v-for="(a, i) in ATTRS" :key="a.key" :style="\'--i:\' + (i + 1)" :title="a.desc"' +
    '                  :class="{ on: S.console.attr === a.key }" @click="setAttr(a.key)">' +
    "            {{ a.label }}<b>{{ attrCounts[a.key] }}</b></button>" +
    "        </div></div>" +
    '      <div class="tool-list">' +
    '        <x-skel v-if="S.toolsLoading" :n="6"></x-skel>' +
    '        <div class="tool-group" v-for="g in groups" :key="g.key">' +
    '          <div class="tool-group-head" @click="toggleGroup(g.key)">' +
    '            <span class="tg-caret" :class="{ open: g.open }"></span>' +
    '            <span class="tg-label">{{ g.label }}</span>' +
    '            <span class="spacer"></span>' +
    '            <span class="tg-count">{{ g.count }}</span></div>' +
    '          <div class="tool-group-body" v-show="g.open" :class="{ \'tg-in\': g.open }">' +
    '            <div class="tool-item" v-for="(t, i) in g.items" :key="t.name" :style="\'--i:\' + i" ' +
    '                 :class="{ active: S.console.selected && S.console.selected.name===t.name }" @click="selectTool(t)">' +
    '              <div class="tn">{{ t.name }}' +
    '                <x-pill v-if="attrPill(t)" :kind="attrPill(t).kind">{{ attrPill(t).text }}</x-pill></div>' +
    '              <div class="td">{{ toolZhDesc(t.name) || short(t.description) }}</div></div>' +
    "          </div></div>" +
    '        <x-empty v-if="!S.toolsLoading && !groups.length" icon="search">没有匹配的工具</x-empty>' +
    "      </div></x-card>" +
    // 方案 E：`param-open` = 「单击上面恢复工具最高高度」的手动展开态
    //（执行后参数段本会被收纳到 30%，点一下参数卡即回到内容高）。
    '    <div class="con-main" :class="{ \'param-open\': S.console.paramOpen }">' +
    '      <x-card v-if="!S.console.selected">' +
    '        <x-empty icon="terminal" title="选一个工具开始" text="左边列出全部工具；参数表单按各自的 JSON Schema 自动生成。"></x-empty></x-card>' +
    '      <div class="con-stack" v-else @click="S.console.paramOpen = true">' +
    '        <x-card>' +
    '          <template #head><h3 class="mono">{{ S.console.selected.name }}</h3>' +
    '            <x-pill v-if="toolZhName(S.console.selected.name)">{{ toolZhName(S.console.selected.name) }}</x-pill>' +
    '            <x-pill kind="err" v-if="needsConfirm">破坏性</x-pill></template>' +
    '          <template #actions><x-pill mono>{{ fields.length }} 个参数</x-pill></template>' +
    '          <div class="md small dim mb" v-html="descHtml"></div>' +
    '          <div class="row2">' +
    '            <x-field v-for="f in fields" :key="f.key" :label="f.label" :req="f.required" :hint="f.desc">' +
    "              <template #label><x-pill mono v-if=\"f.kind!=='text' && f.kind!=='textarea'\">{{ kindZh(f.kind) }}</x-pill></template>" +
    '              <select class="select" v-if="f.kind===\'select\'" :value="argStore[f.key]" @change="setArg(f.key, $event.target.value)">' +
    '                <option value="">（不指定）</option>' +
    '                <option v-for="o in f.enumValues" :key="o" :value="o">{{ o }}</option></select>' +
    '              <label class="switch" v-else-if="f.kind===\'bool\'">' +
    '                <input type="checkbox" :checked="argStore[f.key]===true" @change="setArg(f.key, $event.target.checked)">' +
    '                <span class="switch-track"></span>' +
    "                <span class=\"small dim\">{{ argStore[f.key] ? '开' : '关' }}</span></label>" +
    "              <textarea class=\"textarea\" v-else-if=\"f.kind==='textarea'||f.kind==='json'\" " +
    '                :value="argStore[f.key]" :placeholder="f.placeholder" ' +
    '                @input="setArg(f.key, $event.target.value)"></textarea>' +
    "              <input class=\"input\" :class=\"{ mono: f.kind==='number'||f.kind==='list' }\" v-else " +
    "                :type=\"f.kind==='number' ? 'number' : 'text'\" :value=\"argStore[f.key]\" " +
    "                :placeholder=\"f.placeholder || (f.kind==='list' ? '逗号或换行分隔' : '')\" " +
    '                @input="setArg(f.key, $event.target.value)">' +
    "            </x-field>" +
    "          </div>" +
"          <div class=\"run-bar\">" +
    '            <button class="btn btn-primary" :class="{ loading: S.console.running }" @click="runTool">' +
    "              <span v-html=\"svg('play')\"></span>{{ S.console.running ? '执行中…' : '执行' }}</button>" +
    '            <button class="btn btn-sm" @click="S.console.args[S.console.selected.name]={}">清空参数</button>' +
    '            <span class="small faint" v-if="S.console.elapsed!=null">耗时 {{ S.console.elapsed }} ms</span>' +
    "          </div></x-card>" +
    '        <x-card class="mt" v-if="S.console.error">' +
    '          <x-banner kind="err" icon="alert" flush>' +
    '            <div><b>调用失败</b><div class="small">{{ S.console.error }}</div></div></x-banner></x-card>' +
    "      </div>" +
    '        <x-card class="mt con-term">' +
    '          <template #head><h3 class="mono">终端输出</h3>' +
    "            <x-pill mono>{{ S.console.logs.length }} 条</x-pill>" +
    // 卡头的三角箭头（`.term-fold-all`）已按主人要求删除（2026-10-14）——折叠/展开
    // 统一走两个区块头（整行可点），不再需要额外的「一键全展」入口。
    '          </template>' +
    '          <template #actions>' +
    '            <button class="btn btn-sm" @click="S.console.logs=[]">清屏</button></template>' +
    '          <div class="term-wrap" :class="{ folded: folded, \'all-folded\': allFolded, \'is-running\': S.console.running }" @click="restoreAuto">' +
    '            <div class="term-sec">' +
    // 🔴 2026-10-14：折叠 handler 从内部 `<button>` **上移到整行容器** —— 原先只有
    // 精确点到三角/文字才折叠，点区块头的空白区会冒泡到 `.term-wrap` 触发
    // `restoreAuto`（两个区块一起展开），主人的体感就是「点一个、两个同时展开」。
    // 现在整行都可点、且 `@click.stop` 拦在这里；内部按钮不再绑事件（点它 ⇒ 冒泡到
    // 容器 ⇒ 触发一次），键盘 Enter 落在真 `<button>` 上同样冒泡上来 ⇒ 可达性不变。
    '              <div class="term-sec-head" @click.stop="toggleFold(\'log\')">' +
    '                <button class="term-sec-toggle">' +
    "                  <span class=\"term-caret\" :class=\"{ open: !S.console.foldLog }\"></span>" +
    '                  <span>命令与输出</span>' +
    "                  <x-pill mono>{{ S.console.logs.length }} 条</x-pill></button>" +
    "              </div>" +
    '              <div class="term term-log" v-show="!S.console.foldLog">' +
    '                <div class="term-line term-dim">Vault: <span :title="vaultShort">{{ vaultShort }}</span></div>' +
    '                <div class="term-line"><span class="term-dim">MCP: </span>' +
    "                  <span :class=\"S.health.ok ? 'term-ok' : 'term-err'\">{{ S.health.ok ? '已连接' : '已断开' }}</span>" +
    "                  <span class=\"term-dim\"> · {{ S.health.tools_count }} 个工具 · 协议 {{ S.health.protocol || '—' }}</span></div>" +
    '                <template v-for="lg in S.console.logs" :key="lg.id">' +
    '                  <div class="term-line" style="--i:0"><span class="term-prompt">$</span>' +
    '                    <span class="term-cmd">{{ lg.tool }}</span> <span class="term-dim">{{ lg.cmd }}</span></div>' +
    "                  <div class=\"term-line\" style=\"--i:1\"><span :class=\"lg.ok ? 'term-ok' : 'term-err'\">" +
    "                    [{{ lg.ok ? '成功' : '失败' }}]</span>" +
    "                    <span class=\"term-dim\"> {{ lg.ms != null ? lg.ms + ' ms' : '—' }} · {{ clock(lg.ts) }}</span></div>" +
    '                  <div class="term-line term-out" style="--i:2">' +
    '                    <span class="tw" :data-tw="lg.id"' +
    "                      :data-full=\"lg.text || '（无输出）'\">{{ lg.text || '（无输出）' }}</span>" +
    '                    <span class="tw-caret" aria-hidden="true">▍</span></div>' +
    '                </template>' +
    '                <div class="term-line term-dim" v-if="!S.console.logs.length">执行任意工具后，命令行与输出会打印在这里。</div>' +
    '                <div class="term-line" v-if="S.console.running"><span class="term-prompt">$</span>' +
    '                  <span class="term-dim">执行中…</span><span class="term-caret">▍</span></div>' +
    '              </div></div>' +
    '            <div class="term-sec" v-if="S.console.result">' +
    '              <div class="term-sec-head" @click.stop="toggleFold(\'result\')">' +
    '                <button class="term-sec-toggle">' +
    "                  <span class=\"term-caret\" :class=\"{ open: !S.console.foldResult }\"></span>" +
    '                  <span>结果</span>' +
    "                  <x-pill :kind=\"S.console.result.isError ? 'err' : 'ok'\">" +
    "                    {{ S.console.result.isError ? '工具返回错误' : '成功' }}</x-pill>" +
    "                  <x-pill mono>{{ fmtNum(S.console.result.chars) }} 字符</x-pill>" +
    "                </button>" +
    '                <div class="tabs term-tabs">' +
    '                  <span class="tab-ind" aria-hidden="true"></span>' +
    '                  <button class="tab" :class="{active:S.console.view===\'rendered\'}" @click.stop="S.console.view=\'rendered\'">渲染</button>' +
    '                  <button class="tab" :class="{active:S.console.view===\'json\'}" @click.stop="S.console.view=\'json\'">JSON</button>' +
    '                  <button class="tab" :class="{active:S.console.view===\'raw\'}" @click.stop="S.console.view=\'raw\'">原始</button>' +
    '                </div>' +
    "                <button class=\"btn btn-sm\" title=\"复制结果\" @click.stop=\"copy(S.console.result.text)\">" +
    "                  <span v-html=\"svg('copy')\"></span></button>" +
    "              </div>" +
    '              <div class="term-sec-body" v-show="!S.console.foldResult">' +
    '                <div v-if="S.console.view===\'rendered\'"><markdown-box :src="resultMarkdown"></markdown-box></div>' +
    "                <div v-else-if=\"S.console.view==='json'\"><json-tree :data=\"resultJson\"></json-tree></div>" +
    '                <pre class="code" v-else>{{ S.console.result.text }}</pre>' +
    '                <div class="small faint mt" v-if="S.console.result.truncated">（文本已按上限截断显示）</div>' +
    '              </div></div>' +
    '          </div></x-card>' +
    "    </div>" +
    '    <x-card bare class="con-side ide-panel">' +
    '      <div class="card-head ide-panel-head"><h3 class="mono">日志检查器</h3>' +
    '        <span class="spacer"></span><x-pill mono>{{ filteredLogs.length }}</x-pill>' +
    '        <button class="btn btn-sm" @click="S.console.logs=[]">清空</button></div>' +
    '      <div class="ide-panel-body">' +
    '      <div class="log-search"><span v-html="svg(\'search\')"></span>' +
    '        <input class="input" v-model="S.console.logFilter" placeholder="过滤日志…"></div>' +
    '      <div class="log-list stagger-in">' +
    "        <div class=\"log-item\" v-for=\"(lg, i) in filteredLogs\" :key=\"lg.id\" :style=\"'--i:' + i\" :class=\"lg.ok ? 'ok' : 'err'\">" +
    '          <div class="log-head"><span class="log-kind">{{ kindLabel(lg.kind) }}</span>' +
    '            <span class="log-time mono">{{ clock(lg.ts) }}</span></div>' +
    '          <div class="log-tool mono">{{ lg.tool }}</div>' +
    '          <div class="log-text mono">{{ lg.text || "（无输出）" }}</div>' +
    '          <div class="log-foot"><span class="mono">{{ lg.ms != null ? lg.ms + " ms" : "—" }}</span>' +
    '            <span class="spacer"></span>' +
    '            <button class="btn btn-sm" @click="rerun(lg)">重跑</button></div>' +
    "        </div>" +
    '        <x-empty v-if="!filteredLogs.length" icon="terminal" title="暂无日志"' +
    '          text="执行工具后，调用与错误会记录在这里；可过滤、可重跑。"></x-empty>' +
    "      </div></div></x-card>" +
    "  </div>" +
    "</div>",
};

export { ConsolePage };
