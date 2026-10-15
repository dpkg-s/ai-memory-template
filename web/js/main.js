import { createApp } from "../vendor/vue.esm-browser.prod.js";
import { AnimatedNumber } from "./components/AnimatedNumber.js";
import { DistributionBars } from "./components/DistributionBars.js";
import { JsonTree } from "./components/JsonTree.js";
import { MarkdownBox } from "./components/MarkdownBox.js";
import { XBanner } from "./components/XBanner.js";
import { XCard } from "./components/XCard.js";
import { XEmpty } from "./components/XEmpty.js";
import { XField } from "./components/XField.js";
import { XKv } from "./components/XKv.js";
import { XKvRow } from "./components/XKvRow.js";
import { XListItem } from "./components/XListItem.js";
import { XPill } from "./components/XPill.js";
import { XSkel } from "./components/XSkel.js";
import { XStat } from "./components/XStat.js";
import { loadHealth, loadOverview, loadTools } from "./core/data.js";
import { svg } from "./core/icons.js";
import { go } from "./core/router.js";
import { fmtBytes, fmtNum, kindZh, mdRender, relTime, shortDesc, toolZhDesc, toolZhName } from "./core/util.js";
import { runTool, selectTool } from "./features/console.js";
import { browseLibrary, openNote, searchLibrary } from "./features/library.js";
import { ConsolePage } from "./views/ConsolePage.js";
import { Dashboard } from "./views/Dashboard.js";
import { LibraryPage } from "./views/LibraryPage.js";
import { Root } from "./views/Root.js";
import { SettingsPage } from "./views/SettingsPage.js";


/* 原 `if (!window.Vue) { … return; }` 守卫已移除：ESM 模块顶层不允许 return。
   Vue 缺失时浏览器会直接报模块加载失败，交由 index.html 的 4 秒兜底提示呈现。 */
var app = createApp(Root);
Object.assign(app.config.globalProperties, {
  svg: svg,
  fmtNum: fmtNum,
  fmtBytes: fmtBytes,
  relTime: relTime,
  short: shortDesc,
  toolZhName: toolZhName,
  toolZhDesc: toolZhDesc,
  kindZh: kindZh,
  md: mdRender,
  go: go,
  openNote: openNote,
  browseLibrary: browseLibrary,
  searchLibrary: searchLibrary,
  selectTool: selectTool,
  runTool: runTool,
  loadHealth: loadHealth,
  loadTools: loadTools,
  loadOverview: loadOverview,
});

[
  ["animated-number", AnimatedNumber],
  ["markdown-box", MarkdownBox],
  ["json-tree", JsonTree],
  ["distribution-bars", DistributionBars],
  ["x-card", XCard],
  ["x-kv", XKv],
  ["x-kv-row", XKvRow],
  ["x-field", XField],
  ["x-empty", XEmpty],
  ["x-pill", XPill],
  ["x-skel", XSkel],
  ["x-banner", XBanner],
  ["x-stat", XStat],
  ["x-list-item", XListItem],
  ["dashboard", Dashboard],
  ["console-page", ConsolePage],
  ["library-page", LibraryPage],
  ["settings-page", SettingsPage],
].forEach(function (c) {
  app.component(c[0], c[1]);
});

app.mount("#app");


export { app };
