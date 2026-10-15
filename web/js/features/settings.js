import { api } from "../core/api.js";
import { S, askConfirm, toast } from "../core/store.js";

/* ======================================================================
 * 8. 配置面
 * ==================================================================== */
function loadThresholds(force) {
  if (S.thresholds.data && !force) return Promise.resolve(S.thresholds.data);
  S.thresholds.loading = true;
  S.thresholds.error = null;
  return api
    .get("/api/thresholds")
    .then(function (d) {
      S.thresholds.data = d;
      S.thresholds.loading = false;
      var draft = {};
      Object.keys(d.keys || {}).forEach(function (k) {
        draft[k] = d.keys[k].value;
      });
      S.thresholds.draft = draft;
      S.thresholds.dirty = false;
      return d;
    })
    .catch(function (e) {
      S.thresholds.loading = false;
      S.thresholds.error = e.message;
      if (e.status !== 401) toast("err", "读取阈值失败", e.message);
    });
}
function saveThresholds() {
  var st = S.thresholds;
  if (!st.data || !st.data.writable) {
    toast(
      "warn",
      "不可写入",
      "阈值当前为只读（未配置 vault 或已关闭写入）。",
    );
    return;
  }
  var values = {};
  Object.keys(st.draft).forEach(function (k) {
    var isInt = st.data.keys[k].type === "int";
    values[k] = isInt ? parseInt(st.draft[k], 10) : parseFloat(st.draft[k]);
  });
  askConfirm(
    "确认写入 memory.toml",
    "将写入 " +
      (st.data.config_path || "memory.toml") +
      "（约 2 秒内生效）。仅写入该配置文件，不会触碰任何 .md 笔记；写入前会自动备份到 vault 之外。",
    "写入",
    function () {
      st.saving = true;
      api
        .post("/api/thresholds", { values: values })
        .then(function (d) {
          st.saving = false;
          st.data = d;
          st.dirty = false;
          var draft = {};
          Object.keys(d.keys || {}).forEach(function (k) {
            draft[k] = d.keys[k].value;
          });
          st.draft = draft;
          toast(
            "ok",
            "已写入 memory.toml",
            "备份：" + (d.backup || "（首次创建，无备份）"),
          );
        })
        .catch(function (e) {
          st.saving = false;
          toast("err", "写入失败", e.message);
        });
    },
    false,
  );
}

export { loadThresholds, saveThresholds };
