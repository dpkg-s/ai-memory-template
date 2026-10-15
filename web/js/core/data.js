import { api } from "./api.js";
import { S, toast } from "./store.js";
import { parseStats, tryJson } from "./util.js";

/* ======================================================================
 * 6. 数据加载
 * ==================================================================== */
function loadAbout() {
  return api
    .get("/api/config")
    .then(function (d) {
      S.about = d;
      S.authRequired = false;
      return d;
    })
    .catch(function (e) {
      if (e.status !== 401) toast("err", "读取配置失败", e.message);
    });
}
function loadHealth() {
  S.health.checking = true;
  return api
    .get("/api/health")
    .then(function (d) {
      S.health = Object.assign({ checking: false }, d);
      if (!d.ok) toast("warn", "MCP 端点不可达", d.error || "");
      return d;
    })
    .catch(function (e) {
      S.health = Object.assign({}, S.health, {
        checking: false,
        ok: false,
        error: e.message,
      });
      if (e.status !== 401) toast("err", "健康检查失败", e.message);
    });
}
function loadTools(force) {
  if (S.tools.length && !force) return Promise.resolve(S.tools);
  S.toolsLoading = true;
  S.toolsError = null;
  return api
    .get("/api/tools")
    .then(function (d) {
      S.tools = d.tools || [];
      /* 组定义同样来自服务端（2026-10-15 方案 B）。失败或旧版没有该字段时
         落空数组 ⇒ 左栏退化为单组平铺，而不是假装分组。 */
      S.toolGroups = d.groups || [];
      S.toolsLoading = false;
      S.health.tools_count = d.count;
      return S.tools;
    })
    .catch(function (e) {
      S.toolsLoading = false;
      S.toolsError = e.message;
      if (e.status !== 401) toast("err", "获取工具清单失败", e.message);
    });
}
function loadOverview() {
  S.overviewLoading = true;
  S.overviewError = null;
  return api
    .get("/api/overview")
    .then(function (d) {
      var stats = parseStats(d.stats && d.stats.text);
      S.overview = {
        parsed: stats,
        recent: (tryJson(d.recent && d.recent.text) || {}).entries || [],
        list: (tryJson(d.list && d.list.text) || {}).entries || [],
        raw: d,
      };
      S.overviewLoading = false;
      return S.overview;
    })
    .catch(function (e) {
      S.overviewLoading = false;
      S.overviewError = e.message;
      if (e.status !== 401) toast("err", "加载概览失败", e.message);
    });
}
/* 概览「最近更新」热力日历的数据面（2026-10-15）。
   数据**由后端按天聚合**（`GET /api/activity`），前端不做任何日期数学。
   🔴 为什么不走 `/api/call` 直接拉 `memory_recent`：任何回传浏览器的工具结果都要过
   后端的 `_clip`（`_THUMB` = 40000 字符），而 120 天的 JSON 约 **13 万字符**
   ⇒ 前端拿到的是**残缺 JSON**，`JSON.parse` 直接抛错（本轮实测踩到，页面只落到空态）。
   聚合放后端还顺带省掉了 content / text / structured 三份重复。
   窗口取 120 天：日历要按月翻，30 天铺不满四个月。 */
var RECENT_DAYS = 120;
function loadRecentActivity(days) {
  S.recentLoading = true;
  S.recentError = null;
  return api
    .get("/api/activity?days=" + (days || RECENT_DAYS))
    .then(function (d) {
      S.recentActivity = {
        days: d.days || days || RECENT_DAYS,
        total: d.total || 0,
        today: d.today || "",
        counts: d.counts || {},
        items: d.items || {},
      };
      S.recentLoading = false;
      return S.recentActivity;
    })
    .catch(function (e) {
      S.recentLoading = false;
      S.recentError = e.message;
      if (e.status !== 401) toast("err", "加载更新日历失败", e.message);
    });
}
function boot() {
  S.ready = true;
  if (S.authRequired) return;
  loadAbout().then(function () {
    loadHealth().then(function () {
      loadTools();
      loadOverview();
      loadRecentActivity();
    });
  });
}

export { loadAbout, loadHealth, loadTools, loadOverview, loadRecentActivity, RECENT_DAYS, boot };
