/* 主题：深/浅色解析与落盘
 * index.html 的内联脚本已做过一次预设（防 FOUC），
 * 这里负责运行期切换与监听系统主题变化。
 */
import { S } from "./store.js";

/* ======================================================================
 * 1. 主题
 * ==================================================================== */
function systemDark() {
  return (
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}
function applyTheme(pref) {
  var resolved = pref === "auto" ? (systemDark() ? "dark" : "light") : pref;
  document.documentElement.setAttribute("data-theme", resolved);
  document.documentElement.setAttribute("data-theme-pref", pref);
  try {
    localStorage.setItem("am.theme", pref);
  } catch (e) {
    /* 忽略 */
  }
  return resolved;
}
if (window.matchMedia) {
  try {
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", function () {
        if (S.themePref === "auto") S.theme = applyTheme("auto");
      });
  } catch (e) {
    /* 老浏览器 */
  }
}

export { systemDark, applyTheme };
