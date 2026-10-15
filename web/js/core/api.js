import { S } from "./store.js";

/* ======================================================================
 * 4. API 客户端
 * ==================================================================== */
var TOKEN_KEY = "am.token";
var api = {
  token: (function () {
    try {
      return sessionStorage.getItem(TOKEN_KEY) || "";
    } catch (e) {
      return "";
    }
  })(),
  setToken: function (t) {
    this.token = String(t || "");
    try {
      if (this.token) sessionStorage.setItem(TOKEN_KEY, this.token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) {
      /* 忽略 */
    }
  },
  req: function (path, opts) {
    opts = opts || {};
    var headers = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers["Authorization"] = "Bearer " + this.token;
    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
    }).then(function (res) {
      return res.text().then(function (txt) {
        var data = null;
        try {
          data = txt ? JSON.parse(txt) : null;
        } catch (e) {
          data = { error: txt.slice(0, 300) };
        }
        if (res.status === 401) {
          S.authRequired = true;
          throw Object.assign(new Error((data && data.error) || "未授权"), {
            status: 401,
          });
        }
        if (!res.ok) {
          throw Object.assign(
            new Error((data && data.error) || "HTTP " + res.status),
            { status: res.status, data: data },
          );
        }
        return data;
      });
    });
  },
  get: function (p) {
    return this.req(p);
  },
  post: function (p, body) {
    return this.req(p, { method: "POST", body: body || {} });
  },
};

export { TOKEN_KEY, api };
