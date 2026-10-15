/* ----------------------------------------------------------------------
 * 配置页 · FNS「粘贴 JSON 自动识别」（2026-10-14）
 *
 * 为什么需要：FNS 客户端 config.json 的键名（api / apiToken / vault / client）
 * 与本页表单字段（server / account / vault / token）**不一致**，而且 apiToken
 * 是一长串 JWT —— 手抄四个字段极易出错。这里做一次**宽松识别**：键名归一化
 * （小写、去 _ - . 空格）后按别名表匹配，兼容嵌套对象与 ```json 围栏，
 * 认不出的键原样列出，绝不静默吞掉。
 *
 * 只填表、不写盘：结果写进 fnsDraft，仍要点「保存 FNS 配置」才落账。
 * -------------------------------------------------------------------- */
var FNS_FIELD_LABEL = {
  server: "服务器地址",
  account: "账号",
  vault: "Vault 名",
  token: "口令 / Token",
  exclude: "排除规则",
};
var FNS_IMPORT_ALIAS = {
  server: ["server", "api", "url", "baseurl", "base_url", "endpoint", "apiurl",
           "api_url", "address", "addr", "host", "origin", "domain"],
  account: ["account", "user", "username", "userid", "login", "loginname",
            "email", "mail", "client", "clientname", "client_name", "device",
            "devicename", "device_name", "uid", "nickname"],
  vault: ["vault", "vaultname", "vault_name", "bucket", "space", "spacename",
          "repo", "repository", "notebook", "collection"],
  token: ["token", "apitoken", "api_token", "accesstoken", "access_token",
          "authtoken", "auth_token", "bearer", "password", "passwd", "pwd",
          "key", "apikey", "api_key", "secret", "secretkey", "secret_key",
          "credential"],
  exclude: ["exclude", "excludes", "excluded", "ignore", "ignores",
            "ignorepatterns", "ignore_patterns", "excludepatterns",
            "exclude_patterns", "filter", "filters", "skips"],
};
function _normKey(k) {
  return String(k).toLowerCase().replace(/[\s_\-.]/g, "");
}
/* 归一化别名 → 字段名（反查表，只建一次） */
var FNS_ALIAS_LOOKUP = (function () {
  var m = {};
  Object.keys(FNS_IMPORT_ALIAS).forEach(function (f) {
    FNS_IMPORT_ALIAS[f].forEach(function (a) {
      m[_normKey(a)] = f;
    });
  });
  return m;
})();
/* 剥掉 ```json … ``` 围栏与首尾空白 */
function _stripFence(s) {
  var t = String(s == null ? "" : s).trim();
  var m = t.match(/^```[a-zA-Z]*\s*\n?([\s\S]*?)\n?```$/);
  return m ? m[1].trim() : t;
}
/* 宽松解析。返回 {ok, fields, from, unknown, error}
   · 遍历深度 ≤4、浅层优先；同一字段「先命中先赢」，不被深层覆盖。
   · 标量取字符串去空白；数组仅用于 exclude 之类多值字段。 */
function parseFnsPayload(text) {
  var res = { ok: false, fields: {}, from: {}, unknown: [], error: "" };
  var body = _stripFence(text);
  if (!body) {
    res.error = "内容为空 —— 请先粘贴 JSON。";
    return res;
  }
  var data;
  try {
    data = JSON.parse(body);
  } catch (e) {
    res.error = "不是合法 JSON：" + (e && e.message ? e.message : "解析失败");
    return res;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    res.error = '顶层不是 JSON 对象（应形如 { "api": "…" }）。';
    return res;
  }
  var queue = [{ o: data, d: 0 }];
  while (queue.length) {
    var cur = queue.shift();
    Object.keys(cur.o).forEach(function (k) {
      var v = cur.o[k];
      if (v === null || v === undefined) return;
      var f = FNS_ALIAS_LOOKUP[_normKey(k)];
      if (f !== undefined) {
        if (Array.isArray(v)) {
          if (!(f in res.fields)) {
            res.fields[f] = v
              .map(String)
              .map(function (x) { return x.trim(); })
              .filter(Boolean);
            res.from[f] = k;
          }
        } else if (typeof v !== "object") {
          if (!(f in res.fields)) {
            res.fields[f] = String(v).trim();
            res.from[f] = k;
          }
        }
        return;
      }
      if (typeof v === "object" && !Array.isArray(v)) {
        if (cur.d < 4) queue.push({ o: v, d: cur.d + 1 });
        else res.unknown.push(k);
        return;
      }
      res.unknown.push(k);
    });
  }
  res.ok = Object.keys(res.from).length > 0;
  if (!res.ok) {
    res.error =
      "JSON 解析成功，但没认出任何 FNS 字段（可识别的键：api / apiToken / vault / client / server / token …）。";
  }
  return res;
}

export { FNS_FIELD_LABEL, FNS_IMPORT_ALIAS, _normKey, FNS_ALIAS_LOOKUP, _stripFence, parseFnsPayload };
