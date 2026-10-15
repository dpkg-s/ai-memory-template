import { api } from "../core/api.js";
import { S, toast } from "../core/store.js";
import { parseSearch, tryJson } from "../core/util.js";

/* ======================================================================
 * 10. 记忆库
 * ==================================================================== */
function browseLibrary() {
  var L = S.library;
  L.loading = true;
  L.error = null;
  var args = { limit: 60, output_format: "json" };
  if (L.tag) args.tag = L.tag;
  if (L.tier) args.tier = L.tier;
  /* 2026-10-14：改为 **返回 Promise**（原为 fire-and-forget）。调用方需要它做
     「按标签过滤后拿到真实命中数再提示」；模板里 `@click="browseLibrary()"` 拿到
     一个被忽略的 Promise，行为不变。 */
  return api
    .post("/api/call", { tool: "memory_list", arguments: args })
    .then(function (d) {
      var j = tryJson(d.text);
      L.items = (j && j.entries) || [];
      L.hidden = (j && j.hidden) || 0;
      L.count = (j && j.count) || L.items.length;
      L.loading = false;
    })
    .catch(function (e) {
      L.loading = false;
      L.error = e.message;
      if (e.status !== 401) toast("err", "列举失败", e.message);
    });
}
function searchLibrary() {
  var L = S.library;
  if (!L.keyword.trim()) {
    L.mode = "browse";
    return browseLibrary();
  }
  L.mode = "search";
  L.loading = true;
  L.error = null;
  var args = { keyword: L.keyword.trim(), limit: 40 };
  if (L.tag) args.tag = L.tag;
  api
    .post("/api/call", { tool: "memory_search", arguments: args })
    .then(function (d) {
      L.searchRaw = d.text || "";
      L.searchItems = parseSearch(L.searchRaw);
      L.loading = false;
    })
    .catch(function (e) {
      L.loading = false;
      L.error = e.message;
      if (e.status !== 401) toast("err", "搜索失败", e.message);
    });
}
function openNote(title, maxChars) {
  var L = S.library;
  L.noteLoading = true;
  L.note = { title: title, body: "", meta: {} };
  api
    .post("/api/call", {
      tool: "memory_read",
      arguments: {
        title: title,
        max_chars: maxChars || 20000,
        output_format: "json",
      },
    })
    .then(function (d) {
      var j = tryJson(d.text);
      L.noteLoading = false;
      if (j && j.found)
        L.note = {
          title: j.title,
          path: j.path,
          meta: j.meta || {},
          body: j.body || "",
          truncated: j.truncated,
          total_chars: j.total_chars,
        };
      else
        L.note = {
          title: title,
          meta: {},
          body: d.text || "（未找到）",
          notFound: true,
        };
    })
    .catch(function (e) {
      L.noteLoading = false;
      L.error = e.message;
      if (e.status !== 401) toast("err", "读取失败", e.message);
    });
}
function onMdClick(ev) {
  var el = ev.target.closest ? ev.target.closest(".wikilink") : null;
  if (!el) return;
  ev.preventDefault();
  openNote(el.getAttribute("data-title"));
}

export { browseLibrary, searchLibrary, openNote, onMdClick };
