import { h, nextTick } from "../../vendor/vue.esm-browser.prod.js";
import { loadTools } from "./data.js";
import { S } from "./store.js";
import { loadThresholds } from "../features/settings.js";

/* ======================================================================
 * 7. hash 路由
 * ==================================================================== */
var PAGES = ["dashboard", "console", "library", "settings"];
function readHash() {
  var m = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
  return PAGES.indexOf(m) >= 0 ? m : "dashboard";
}
function go(page) {
  location.hash = "#/" + page;
}

/* 导航指示条定位：把 .nav-ind 滑到当前 active 条目的垂直位置。
   用两侧 getBoundingClientRect 求**差**，而不是 offsetTop ——
   .sidebar 是 position:fixed，窄屏还是带 transform 的抽屉，offsetParent
   语义在那种状态下不可靠；求差则天然免疫整体平移。
   animate=false 时先摘掉 transition、写完值强制重排再挂回，否则首次进入会
   从 (0,0) 滑到目标位，像是「指示条自己飞过来」。 */
function syncNavInd(animate) {
  var sb = document.querySelector(".sidebar");
  var ind = sb && sb.querySelector(".nav-ind");
  if (!sb || !ind) return;
  var item = sb.querySelector(".nav-item.active");
  if (!item) {
    ind.classList.remove("on");
    return;
  }
  var ir = item.getBoundingClientRect();
  var sr = sb.getBoundingClientRect();
  /* 指示条固定 20px 并垂直居中于条目；条目本身更矮时以条目高度为准，
     免得在短条目上探出上下两端。 */
  var h = Math.min(20, Math.round(ir.height));
  var y = Math.round(ir.top - sr.top + (ir.height - h) / 2);
  if (!animate) ind.style.transition = "none";
  ind.style.setProperty("--ind-y", y + "px");
  ind.style.setProperty("--ind-h", h + "px");
  if (!animate) {
    void ind.offsetWidth;
    ind.style.transition = "";
  }
  ind.classList.add("on");
}

/* 终端视图 tab 的滑动指示条定位（横向版 syncNavInd）。
   同样用 rect 求差：.term-tabs 是 flex 子项，宽度随中栏档位变化，
   写死像素值在缩放窗口后立刻错位。 */
function syncTabInd(root) {
  if (!root) return;
  var ind = root.querySelector(".tab-ind");
  if (!ind) return;
  var act = root.querySelector(".tab.active");
  if (!act) {
    ind.style.opacity = "0";
    return;
  }
  var ar = act.getBoundingClientRect();
  var rr = root.getBoundingClientRect();
  var x = Math.round(ar.left - rr.left + root.scrollLeft);
  /* 下划线两侧各内缩 8px，免得贴着 tab 的圆角 */
  ind.style.width = Math.max(0, Math.round(ar.width - 16)) + "px";
  ind.style.transform = "translateX(" + (x + 8) + "px)";
  ind.style.opacity = "1";
}

window.addEventListener("hashchange", function () {
  var prev = S.page;
  S.page = readHash();
  var a = PAGES.indexOf(prev),
    b = PAGES.indexOf(S.page);
  if (b !== a && b >= 0) S.pageDir = b > a ? 1 : -1;
  S.sidebarOpen = false;
  if (S.page === "console") loadTools();
  if (S.page === "settings") loadThresholds();
  window.scrollTo({ top: 0, behavior: "smooth" });
  /* 指示条必须等 Vue 把 .active 换到新条目上之后再量 */
  nextTick(function () {
    syncNavInd(true);
  });
});

export { PAGES, readHash, go, syncNavInd, syncTabInd };
