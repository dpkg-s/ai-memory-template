import { api } from "../core/api.js";
import { S, askConfirm, toast } from "../core/store.js";

/* ======================================================================
 * 9. 更新面（2026-10-15）
 *
 * 「更新」= 从 git 远端把**白名单内**的文件取到当前工作区。
 *
 * 三个不在前端做决定的点：
 *   ① 白名单 —— 由服务端 `web_console.UPDATE_PATHS` 给出（后端运行时模块 + web/）。
 *      前端不维护第二份清单，否则每加一个模块就要改两处。
 *   ② 行尾转换 —— git 自己按 .gitattributes / core.autocrlf 写文件；前端与
 *      后端都不碰内容，只传递路径。
 *   ③ 重启时序 —— 控制台会在响应之后自退，由助手接手「停 → 起」。
 *      因此请求被切断属**预期路径**，不能当成失败。
 * ==================================================================== */

/* withFetch=false ⇒ 只读本地状态（进页面时用，不联网跑 git fetch）。 */
function loadUpdate(withFetch) {
  var u = S.update;
  u.loading = true;
  u.error = null;
  return api
    .get("/api/update" + (withFetch === false ? "?fetch=0" : ""))
    .then(function (d) {
      u.loading = false;
      u.data = d;
      /* 后端把「git 拉取失败」表达成 200 + ok:false + error/stderr，
         因为它同时还要把本地状态带回来。这里不要把它当请求失败。 */
      if (!d.ok && d.error) u.error = d.error;
      return d;
    })
    .catch(function (e) {
      u.loading = false;
      u.error = e.message;
      if (e.status !== 401) toast("err", "读取更新状态失败", e.message);
    });
}

function applyUpdate(force) {
  var u = S.update;
  var d = u.data || {};
  var body = [
    "将把白名单内的 " + (d.changed_count || 0) + " 个文件覆盖为远端版本。",
    "目标提交：" + (d.remote_short || "?") + (d.remote_version ? "（v" + d.remote_version + "）" : ""),
    "覆盖前会逐个备份到程序目录下的 " + (d.backup_dir || ".ai-memory-backup"),
  ];
  if (force && (d.conflicts || []).length) {
    body.push("");
    body.push("⚠ 强制覆盖会丢弃 " + d.conflicts.length + " 个文件的本地未提交改动。");
  }
  askConfirm(
    "确认执行更新",
    body.join("\n"),
    force ? "强制更新" : "更新",
    function () {
      u.applying = true;
      api
        .post("/api/update", {
          force: !!force,
          ref: d.ref || "",
          expected_sha: d.remote_sha || "",
        })
        .then(function (r) {
          u.applying = false;
          u.result = r;
          if (r.up_to_date) {
            toast("ok", "已是最新", r.message || "未改动任何文件。");
          } else {
            toast(
              "ok",
              "已更新 " + (r.written || []).length + " 个文件",
              r.restart_required
                ? "后端模块已变化，需重启后端才能生效。"
                : "前端刷新即生效。",
            );
          }
          loadUpdate(false);
        })
        .catch(function (e) {
          /* 业务失败走 409，详情在 e.data（后端把 conflicts 等一并带回来）。 */
          u.applying = false;
          var p = e.data || {};
          u.result = p;
          toast("err", "更新未完成", p.error || e.message);
          loadUpdate(false);
        });
    },
    force,
  );
}

function restartBackend() {
  var u = S.update;
  askConfirm(
    "重启后端",
    "将重启 MCP（8700）与 Web 控制台（8811）。本页面会断开数秒，随后自动重连。",
    "重启",
    function () {
      u.restarting = true;
      api
        .post("/api/update/restart", {})
        .then(function (d) {
          if (!d.ok) {
            u.restarting = false;
            toast("err", "无法重启", d.error || "");
            return;
          }
          toast("ok", "正在重启", "约 " + Math.round(d.delay || 3) + " 秒后自动重连…");
          watchReconnect(Math.round(d.delay || 3) + 3);
        })
        .catch(function (e) {
          var p = e.data || {};
          if (p.ok === false) {
            /* 真失败（未找到重启助手 / 解释器不可用）—— 控制台还活着。 */
            u.restarting = false;
            toast("err", "无法重启", p.error || e.message);
            return;
          }
          /* 连接被切断 ⇒ 控制台已在自退，属预期路径。 */
          toast("ok", "正在重启", "页面将在数秒后自动重连…");
          watchReconnect(6);
        });
    },
    false,
  );
}

/* 轮询 /api/health 直到后端回来。1 秒一格，前两秒不打扰（肯定还没起来）。 */
function watchReconnect(seconds) {
  var u = S.update;
  u.reconnecting = true;
  u.seconds = Math.max(1, seconds);
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    u.seconds = Math.max(0, u.seconds - 1);
    if (tries < 3) return;
    api
      .get("/api/health")
      .then(function (d) {
        if (!d || !d.ok) return;
        clearInterval(timer);
        u.reconnecting = false;
        u.restarting = false;
        toast("ok", "后端已恢复", "连接正常（延迟 " + d.latency_ms + " ms）。");
        loadUpdate(false);
      })
      .catch(function () {
        /* 还没起来，继续等下一次。 */
      });
  }, 1000);
}

export { loadUpdate, applyUpdate, restartBackend, watchReconnect };
