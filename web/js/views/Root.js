import { computed, nextTick, watch } from "../../vendor/vue.esm-browser.prod.js";
import { api } from "../core/api.js";
import { boot, loadTools } from "../core/data.js";
import { go, readHash, syncNavInd, syncTabInd } from "../core/router.js";
import { S, closeConfirm, dismiss } from "../core/store.js";
import { applyTheme } from "../core/theme.js";
import { loadThresholds } from "../features/settings.js";

/* ======================================================================
 * 14. 根组件
 * ==================================================================== */
var NAV = [
  { id: "dashboard", label: "概览", icon: "grid", key: "1" },
  { id: "console", label: "调试台", icon: "terminal", key: "2" },
  { id: "library", label: "记忆库", icon: "book", key: "3" },
  { id: "settings", label: "配置", icon: "sliders", key: "4" },
];
var PAGE_TITLES = {
  dashboard: "概览",
  console: "调试台",
  library: "记忆库",
  settings: "配置",
};
var PAGE_SUBS = {
  dashboard: "健康度与最近动态",
  console: "直接调用 MCP 工具",
  library: "浏览与检索条目",
  settings: "连接 · 阈值 · 同步",
};

/* 2026-10-13：VIEWS（标签条视图表）与 PANELS（底部面板页签表）已随两条横带整块摘除。
   原来的「专注模式」（概览只看最近更新 / 调试台只看终端输出·日志 / 记忆库只看节点信息）
   一并下线 —— 各页固定为默认视图，所有栏目常显。 */

/* 侧栏在窄屏是抽屉：给 body 加锁避免滚动穿透 */
function fixSidebarVar() {
  var narrow = window.innerWidth < 900;
  if (!narrow) S.sidebarOpen = false;
  document.body.style.overflow = narrow && S.sidebarOpen ? "hidden" : "";
  /* 视口变化后侧栏几何会变，指示条重新对位（不带动画，避免拖尾感） */
  syncNavInd(false);
}
watch(function () {
  return S.sidebarOpen;
}, fixSidebarVar);
/* 终端视图切换的滑动指示条 —— 挂在全局 watch 而不是改模板的 @click：
   视图还有程序化入口（执行完工具后会把 view 重置为 rendered，另见
   S.console.view 的赋值点），watch 一次覆盖两条路径，@click 只覆盖前者。
   签名里带上 result / foldResult：结果区是动态出现的，
   它出现的那一刻指示条需要首次落位（否则永远停在 opacity:0）。 */
watch(
  function () {
    return (
      S.console.view +
      "|" +
      (S.console.result ? 1 : 0) +
      "|" +
      (S.console.foldResult ? 1 : 0)
    );
  },
  function () {
    nextTick(function () {
      syncTabInd(document.querySelector(".term-tabs"));
    });
  },
);

/* 共享工具与组件统一挂到 app 上（Vue 全局构建没有 setup 作用域）。
   改造前这里是 14 行 `X.methods.foo = foo` 的逐组件打补丁 —— 同一个 svg/fmtNum
   在 5 个组件里各写一遍。现在一次注册，任何模板都能直接用；
   组件自己声明的同名方法优先级更高，不会互撞。 */

var Root = {
  data: function () {
    return {
      S: S,
      nav: NAV,
      titles: PAGE_TITLES,
      subs: PAGE_SUBS,
    };
  },
  computed: {
    pageTitle: function () {
      return this.titles[S.page] || "";
    },
    pageSub: function () {
      return this.subs[S.page] || "";
    },
    endpointShort: function () {
      var e = S.health.endpoint || S.about.resolved.mcp_endpoint || "";
      return e.replace(/^https?:\/\//, "");
    },
    /* 活动栏**单簇**（2026-10-15 主人令：「把配置从最下面移上来」）。
       原「主功能在上 + 配置落底簇」的 VS Code 式两簇结构已取消 —— 四项导航
       连续排在顶部，底部整块留给 .sidebar-foot（它自带 margin-top:auto）。
       模板直接遍历 data.nav，不再经 navTop / navBot 两个 computed 切片。 */
    /* 面包屑 = 根 › 页（视图切换已下线，不再有第三段）。 */
    crumbs: function () {
      return ["ai-memory", this.pageTitle];
    },
    numStat: function () {
      return ((S.overview || {}).parsed || {}).nums || {};
    },
    logs: function () {
      return S.console.logs || [];
    },
    lastLog: function () {
      return (S.console.logs || [])[0] || null;
    },
  },
  methods: {
    dismiss: dismiss,
    closeConfirm: closeConfirm,
    theme: function (p) {
      S.themePref = p;
      S.theme = applyTheme(p);
    },
    doConfirm: function () {
      if (S.confirm.phrase && S.confirm.typed.trim() !== S.confirm.phrase)
        return;
      var fn = S.confirm.onOk;
      closeConfirm();
      if (fn) fn();
    },
    submitToken: function () {
      api.setToken(S.tokenInput);
      S.tokenInput = "";
      S.authRequired = false;
      boot();
    },
    logout: function () {
      api.setToken("");
      S.tokenInput = "";
      S.authRequired = true;
    },
    onKey: function (e) {
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if (e.key === "Escape") {
        S.sidebarOpen = false;
        closeConfirm();
        return;
      }
      var hit = NAV.filter(function (n) {
        return n.key === e.key;
      })[0];
      if (hit) go(hit.id);
    },
  },
  mounted: function () {
    // 深链：#/settings?tab=th 直接落到指定页签（可分享、可测）。
    var hq = location.hash.split("?")[1] || "";
    var sp = hq || (location.search || "").replace(/^\?/, "");
    try {
      var tab = new URLSearchParams(sp).get("tab");
      if (tab && ["conn", "th", "fns", "about"].indexOf(tab) >= 0)
        S.settingsTab = tab;
    } catch (e) {
      /* 忽略 */
    }
    S.page = readHash();
    fixSidebarVar();
    window.addEventListener("resize", fixSidebarVar);
    window.addEventListener("keydown", this.onKey);
    boot();
    if (S.page === "console") loadTools();
    if (S.page === "settings") loadThresholds();
    /* 首帧落位不带动画（不走 hashchange 分支） */
    nextTick(function () {
      syncNavInd(false);
    });
  },
  template:
    '<div class="shell">' +
    '  <aside class="sidebar" :class="{open: S.sidebarOpen}">' +
    '    <span class="nav-ind" aria-hidden="true"></span>' +
    '    <div class="brand">' +
    '      <span class="brand-mark" v-html="svg(\'layers\')"></span>' +
    '      <span class="brand-text"><span class="brand-name">记忆库控制台</span>' +
    "        <span class=\"brand-sub\">ai-memory · v{{ S.about.resolved.version || '1.0.0' }}</span></span>" +
    "    </div>" +
    '    <div class="nav-sec">导航</div>' +
    '    <nav class="nav">' +
    '      <button class="nav-item" v-for="n in nav" :key="n.id" :class="{active: S.page===n.id}"' +
    "        :title=\"n.label + '（' + n.key + '）'\" :aria-label=\"n.label\" @click=\"go(n.id)\">" +
    '        <span class="nav-icon" v-html="svg(n.icon)"></span>' +
    '        <span class="nav-label">{{ n.label }}</span>' +
    '        <span class="nav-kbd">{{ n.key }}</span></button>' +
    "    </nav>" +
    '    <div class="sidebar-foot">' +
    '      <div class="foot-line"><span>端点</span><b class="mono">{{ endpointShort }}</b></div>' +
    '      <div class="foot-line mt-sm"><span>工具</span><b>{{ S.health.tools_count }}</b></div>' +
    '      <div class="foot-line mt-sm"><span>状态</span>' +
    "        <b :style=\"{color: S.health.ok ? 'var(--ok)' : 'var(--err)'}\">{{ S.health.ok ? '在线' : '离线' }}</b></div>" +
    "    </div>" +
    "  </aside>" +
    '  <div class="scrim" v-if="S.sidebarOpen" @click="S.sidebarOpen=false"></div>' +
    '  <div class="shell-main">' +
    '    <header class="topbar">' +
    '      <button class="icon-btn" @click="S.sidebarOpen=!S.sidebarOpen" :aria-label="\'菜单\'">' +
    '        <span v-html="svg(\'menu\')" style="width:20px;height:20px;display:block"></span></button>' +
    '      <div class="topbar-titles">' +
    '        <div class="topbar-title">记忆库控制台' +
    '          <span class="tb-ver mono">v{{ S.about.resolved.version || "1.0.0" }}</span></div>' +
    '        <div class="topbar-sub">{{ pageSub }}</div></div>' +
    '      <div class="spacer"></div>' +
    '      <div class="tabs" style="padding:3px" v-if="S.page===\'dashboard\'">' +
    '        <button class="tab" style="padding:5px 9px" @click="loadHealth(); loadOverview()" ' +
    "                :class=\"{ active: S.overviewLoading }\" title=\"刷新概览\" aria-label=\"刷新概览\">" +
    "          <span v-html=\"svg('refresh')\"></span></button></div>" +
    "      <div class=\"conn\" :class=\"S.health.ok ? 'ok' : (S.health.checking ? '' : 'err')\" " +
    '           @click="loadHealth(); loadTools(true)" :title="S.health.endpoint">' +
    "        <span class=\"dot\" :class=\"S.health.checking ? 'idle' : (S.health.ok ? 'ok' : 'err')\"></span>" +
    "        <span class=\"hide-mobile\">{{ S.health.checking ? '检查中…' : (S.health.ok ? '已连接' : '未连接') }}</span>" +
    '        <span class="mono small faint hide-mobile" v-if="S.health.latency_ms!=null">{{ S.health.latency_ms }}ms</span></div>' +
    '      <div class="tabs" style="padding:3px">' +
    '        <button class="tab" style="padding:5px 9px" :class="{active:S.themePref===\'light\'}" @click="theme(\'light\')" title="浅色">' +
    '          <span v-html="svg(\'sun\')" style="width:15px;height:15px;display:block"></span></button>' +
    '        <button class="tab" style="padding:5px 9px" :class="{active:S.themePref===\'auto\'}" @click="theme(\'auto\')" title="跟随系统">' +
    '          <span v-html="svg(\'auto\')" style="width:15px;height:15px;display:block"></span></button>' +
    '        <button class="tab" style="padding:5px 9px" :class="{active:S.themePref===\'dark\'}" @click="theme(\'dark\')" title="深色">' +
    '          <span v-html="svg(\'moon\')" style="width:15px;height:15px;display:block"></span></button>' +
    "      </div>" +
    "    </header>" +
    '    <nav class="crumbs" aria-label="面包屑">' +
    '      <template v-for="(c,i) in crumbs" :key="i">' +
    '        <span class="crumb-sep" v-if="i">›</span>' +
    '        <span class="crumb" :class="{last: i===crumbs.length-1}">{{ c }}</span>' +
    "      </template>" +
    "    </nav>" +
    "    <main class=\"main\" :style=\"{'--pdir': S.pageDir}\">" +
    '      <div class="banner err" v-if="S.authRequired" style="align-items:center">' +
    "        <span v-html=\"svg('key')\"></span><div><b>需要访问令牌</b>" +
    '        <div class="small">在下方输入 ADMIN token 后继续。</div></div></div>' +
    /* appear：Vue 的 transition 默认不播首帧，而这里撤掉了 .page 自带的
       pageIn animation（改用 §17c 的过渡类），不加 appear 首屏就没有入场。 */
    '      <transition name="page" mode="out-in" appear>' +
    "      <dashboard v-if=\"S.page==='dashboard'\"></dashboard>" +
    "      <console-page v-else-if=\"S.page==='console'\"></console-page>" +
    "      <library-page v-else-if=\"S.page==='library'\"></library-page>" +
    "      <settings-page v-else-if=\"S.page==='settings'\"></settings-page>" +
    '      </transition>' +
    "    </main>" +
    '    <footer class="statusbar">' +
    '      <div class="sb-grp">' +
    "        <span class=\"dot\" :class=\"S.health.checking ? 'idle' : (S.health.ok ? 'ok' : 'err')\"></span>" +
    "        <span>{{ S.health.checking ? '正在检查' : (S.health.ok ? '已连接' : '未连接') }}</span>" +
    '        <span class="mono" v-if="S.health.latency_ms!=null">{{ S.health.latency_ms }} ms</span></div>' +
    '      <span class="spacer"></span>' +
    '      <div class="sb-grp">' +
    '        <span>条目 <b>{{ fmtNum(numStat.total || 0) }}</b></span>' +
    '        <span>孤立 <b>{{ fmtNum(numStat.orphans || 0) }}</b></span></div>' +
    '    </footer>' +
    "  </div>" +
    '  <transition name="fade">' +
    '    <div class="modal-mask" v-if="S.authRequired">' +
    '      <div class="modal" style="max-width:420px">' +
    '        <div class="modal-head"><h3>输入访问令牌</h3>' +
    "          <p>该控制台启用了 token 鉴权。令牌只保存在本次浏览器会话（sessionStorage），不会进入 URL 与静态文件。</p></div>" +
    '        <div class="modal-body"><div class="field" style="margin:0">' +
    '          <input class="input mono" type="password" v-model="S.tokenInput" placeholder="Bearer token" @keyup.enter="submitToken"></div></div>' +
    '        <div class="modal-foot"><button class="btn btn-primary" @click="submitToken">进入</button></div>' +
    "      </div></div>" +
    "  </transition>" +
    '  <transition name="fade">' +
    '    <div class="modal-mask" v-if="S.confirm.open" @click.self="closeConfirm">' +
    '      <div class="modal">' +
    '        <div class="modal-head"><h3>{{ S.confirm.title }}</h3><p>{{ S.confirm.body }}</p></div>' +
    '        <div class="modal-body" v-if="S.confirm.phrase">' +
    '          <div class="field" style="margin:0"><label class="label">请输入 <code>{{ S.confirm.phrase }}</code> 以确认</label>' +
    '            <input class="input mono" v-model="S.confirm.typed" :placeholder="S.confirm.phrase" @keyup.enter="doConfirm"></div></div>' +
    '        <div class="modal-foot">' +
    '          <button class="btn" @click="closeConfirm">取消</button>' +
    "          <button class=\"btn\" :class=\"S.confirm.danger ? 'btn-danger' : 'btn-primary'\" " +
    '            :disabled="S.confirm.phrase && S.confirm.typed.trim() !== S.confirm.phrase" @click="doConfirm">确认</button>' +
    "        </div></div></div>" +
    "  </transition>" +
    '  <transition-group name="toast" tag="div" class="toast-wrap">' +
    '    <div class="toast" v-for="t in S.toasts" :key="t.id" :class="t.kind">' +
    '      <span class="grow"><span class="toast-title">{{ t.title }}</span>' +
    '        <span class="toast-body" v-if="t.body">{{ t.body }}</span></span>' +
    '      <button class="toast-close" @click="dismiss(t.id)">×</button></div>' +
    "  </transition-group>" +
    "</div>",
};

export { NAV, PAGE_TITLES, PAGE_SUBS, fixSidebarVar, Root };
