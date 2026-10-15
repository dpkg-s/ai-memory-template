import { computed, watch } from "../../vendor/vue.esm-browser.prod.js";
import { api } from "../core/api.js";
import { loadAbout, loadHealth, loadOverview, loadTools } from "../core/data.js";
import { S, askConfirm, toast } from "../core/store.js";
import { FNS_FIELD_LABEL, parseFnsPayload } from "../features/fnsImport.js";
import { loadThresholds, saveThresholds } from "../features/settings.js";

var SETTINGS_TABS = [
  { id: "conn", label: "连接", icon: "link" },
  { id: "th", label: "运行时阈值", icon: "sliders" },
  { id: "fns", label: "FNS 同步", icon: "inbox" },
  { id: "about", label: "关于", icon: "info" },
];

var SettingsPage = {
  data: function () {
    return {
      S: S,
      tabs: SETTINGS_TABS,
      endpointDraft: "",
      fnsDraft: null,
      showToken: false,
      importText: "",
      importReport: null,
    };
  },
  computed: {
    /* 配置页当前页签**直接派生**自 S.settingsTab —— 页面内 cfg-nav 是唯一入口。
       （2026-10-13：顶端标签条已整块摘除，原来与它共用的真值源只剩 cfg-nav。）
       早前用本地 data + watch("S.settingsTab") 做同步，
       实测那个 watcher **根本不触发**（S.settingsTab 变了、页面不动），
       改成 computed 后这类同步问题从根上消失。 */
    tab: function () {
      return S.settingsTab || "conn";
    },
    R: function () {
      return S.about.resolved || {};
    },
    th: function () {
      return S.thresholds;
    },
    thKeys: function () {
      return this.th && this.th.data ? Object.keys(this.th.data.keys) : [];
    },
  },
  methods: {
    pick: function (id) {
      S.settingsTab = id;
      if (id === "th") loadThresholds();
    },
    loadTh: function (f) {
      return loadThresholds(f);
    },
    saveTh: saveThresholds,
    connTest: function () {
      loadHealth();
    },
    saveEndpoint: function () {
      var self = this;
      askConfirm(
        "保存 MCP 端点",
        "把端点改为 " +
          (self.endpointDraft || "（清空，回退环境变量默认）") +
          "？",
        "保存",
        function () {
          api
            .post("/api/config", {
              mcp_endpoint: self.endpointDraft,
              vault_dir: self.R.vault || "",
              fns: (S.about.app || {}).fns || {},
              ui: (S.about.app || {}).ui || {},
            })
            .then(function () {
              return loadAbout();
            })
            .then(function () {
              toast("ok", "已保存", "正在重新探活…");
              return loadHealth();
            })
            .then(function () {
              loadTools(true);
              loadOverview();
            })
            .catch(function (e) {
              toast("err", "保存失败", e.message);
            });
        },
        false,
      );
    },
    saveFns: function () {
      var self = this;
      var fns = self.fnsDraft || {};
      api
        .post("/api/config", {
          mcp_endpoint: (S.about.app || {}).mcp_endpoint || "",
          vault_dir: (S.about.app || {}).vault_dir || "",
          fns: {
            server: fns.server,
            account: fns.account,
            vault: fns.vault,
            token: fns.token,
            exclude: fns.exclude,
          },
          ui: (S.about.app || {}).ui || {},
        })
        .then(function () {
          return loadAbout();
        })
        .then(function () {
          S.fns.dirty = false;
          toast(
            "ok",
            "FNS 配置已保存",
            "存储位置：" +
              ((S.about.resolved || {}).config_path || "（配置目录）") +
              "（在 vault 之外，不会被同步出去）",
          );
        })
        .catch(function (e) {
          toast("err", "保存失败", e.message);
        });
    },
    /* 粘贴 JSON → 识别 → 填表（不写盘）。识别结果落到 importReport 供回显。 */
    importJson: function () {
      var self = this;
      var res = parseFnsPayload(self.importText);
      if (!res.ok) {
        self.importReport = { error: res.error, unknown: res.unknown || [] };
        toast("err", "识别失败", res.error);
        return;
      }
      var d = self.fnsDraft || (self.fnsDraft = {});
      var filled = [];
      var overwritten = 0;
      ["server", "account", "vault", "token", "exclude"].forEach(function (f) {
        if (!(f in res.fields)) return;
        var v = res.fields[f];
        if (f === "exclude") {
          var arr = Array.isArray(v) ? v.slice() : [v];
          if (Array.isArray(d.exclude) && d.exclude.length) overwritten++;
          d.exclude = arr;
          d.excludeText = arr.join("\n");
        } else {
          if (String(d[f] || "").trim()) overwritten++;
          d[f] = v;
        }
        filled.push({ field: f, label: FNS_FIELD_LABEL[f], from: res.from[f] || f });
      });
      S.fns.dirty = true;
      self.importReport = {
        error: null,
        filled: filled,
        overwritten: overwritten,
        unknown: (res.unknown || []).slice(0, 12),
      };
      toast("ok", "识别到 " + filled.length + " 个字段",
        "仅填表、未写盘 —— 核对后点「保存 FNS 配置」。");
    },
    clearImport: function () {
      this.importText = "";
      this.importReport = null;
    },
    copy: function (t) {
      if (navigator.clipboard)
        navigator.clipboard.writeText(String(t)).then(function () {
          toast("ok", "已复制");
        });
    },
    resetTh: function () {
      var st = S.thresholds;
      if (!st.data) return;
      var d = {};
      Object.keys(st.data.keys).forEach(function (k) {
        d[k] = st.data.keys[k].default;
      });
      st.draft = d;
      st.dirty = true;
      toast("warn", "已填入内置默认值", "点「保存」才会写盘。");
    },
    watchTh: function () {
      S.thresholds.dirty = true;
    },
    srcPill: function (s) {
      return s === "file" ? "ok" : s === "env" ? "warn" : "";
    },
    srcLabel: function (s) {
      return s === "file" ? "文件" : s === "env" ? "环境变量" : "默认";
    },
    /* 阈值的中文名 + 作用：全部来自 GET /api/thresholds（服务端 memory_config.META）。
       前端**不硬编码**任何阈值文案 ⇒ 服务端加一个键，界面自动跟着长出说明。 */
    thDesc: function (k) {
      var m =
        S.thresholds.data && S.thresholds.data.keys
          ? S.thresholds.data.keys[k]
          : null;
      if (!m) return "";
      var label = m.label || "",
        desc = m.desc || "";
      if (label && desc) return label + "：" + desc;
      return desc || label;
    },
  },
  watch: {
    "S.about": {
      handler: function (v) {
        if (v && v.resolved) {
          this.endpointDraft = (v.app && v.app.mcp_endpoint) || "";
          var f = (v.app && v.app.fns) || {};
          this.fnsDraft = {
            server: f.server || "",
            account: f.account || "",
            vault: f.vault || "",
            token: f.token || "",
            exclude: Array.isArray(f.exclude) ? f.exclude.slice() : [],
            excludeText: (Array.isArray(f.exclude) ? f.exclude : []).join(
              "\n",
            ),
          };
        }
      },
      immediate: true,
      deep: true,
    },
  },
  template:
    '<div class="page">' +
    '  <div class="page-head"><h1 class="page-title">配置</h1>' +
    '    <p class="page-sub">连接、运行时阈值与 FNS 同步配置。FNS 凭据存放在 vault <b>之外</b>，不会被同步出去。</p></div>' +
    '  <div class="settings-grid">' +
    '    <nav class="cfg-nav">' +
    '      <div class="cfg-sec">CONFIGURATION</div>' +
    '      <button class="cfg-item" v-for="t in tabs" :key="t.id" :class="{active:tab===t.id}" @click="pick(t.id)">' +
    '        <span class="cfg-icon" v-html="svg(t.icon)"></span>' +
    '        <span class="cfg-label">{{ t.label }}</span>' +
    "        <span class=\"cfg-dot\" v-if=\"t.id==='th' && th.dirty\" title=\"有未保存修改\"></span></button>" +
    "    </nav>" +
    '    <div class="cfg-body">' +
    "  <div v-if=\"tab==='conn'\">" +
    '    <x-card title="MCP 端点">' +
    "      <template #actions><x-pill :kind=\"S.health.ok ? 'ok' : 'err'\">" +
    "        <span class=\"dot\" :class=\"S.health.ok ? 'ok' : 'err'\"></span>{{ S.health.ok ? '已连接' : '未连接' }}</x-pill></template>" +
    '      <x-field label="端点 URL">' +
    '        <template #hint>留空则用环境变量默认值 <code>{{ R.mcp_default }}</code>。保存后立即生效，无需重启控制台。</template>' +
    '        <div class="input-group"><input class="input mono" v-model="endpointDraft" :placeholder="R.mcp_default">' +
    '          <button class="btn" @click="connTest" :class="{loading:S.health.checking}">测试</button>' +
    "          <button class=\"btn btn-primary\" @click=\"saveEndpoint\"><span v-html=\"svg('save')\"></span>保存</button></div>" +
    '      </x-field>' +
    '      <x-kv>' +
    "        <x-kv-row label=\"server\" :value=\"S.health.server ? (S.health.server.name+' '+(S.health.server.version||'')) : ''\"></x-kv-row>" +
    '        <x-kv-row label="protocol" mono :value="S.health.protocol"></x-kv-row>' +
    '        <x-kv-row label="tools" :value="S.health.tools_count"></x-kv-row>' +
    "        <x-kv-row label=\"latency\" :value=\"S.health.latency_ms != null ? S.health.latency_ms+' ms' : ''\"></x-kv-row>" +
    '        <x-kv-row label="error" v-if="S.health.error"><span style="color:var(--err)">{{ S.health.error }}</span></x-kv-row>' +
    '      </x-kv>' +
    '      <div class="hint mt">若未连接，先在另一个终端启动端点：<code>python serve_http.py</code>，或用它一键起：<code>python serve_web.py --with-mcp</code>。</div>' +
    '    </x-card>' +
    '    <x-card class="mt" title="记忆库位置">' +
    '      <x-kv>' +
    "        <x-kv-row label=\"vault\" mono :value=\"R.vault || '（未设置）'\"></x-kv-row>" +
    "        <x-kv-row label=\"来源\" :value=\"R.vault_from_env ? '环境变量 AI_MEMORY_DIR' : '配置文件 vault_dir'\"></x-kv-row>" +
    "        <x-kv-row label=\"阈值写入\" :value=\"R.config_write ? '开启' : '只读'\"></x-kv-row>" +
    '      </x-kv></x-card>' +
    "  </div>" +
    "  <div v-if=\"tab==='th'\">" +
    '    <x-card title="运行时阈值">' +
    '      <template #head><x-pill mono>memory.toml</x-pill>' +
    "        <x-pill :kind=\"th.data && th.data.writable ? 'ok' : 'warn'\">{{ th.data && th.data.writable ? '可写' : '只读' }}</x-pill></template>" +
    '      <template #actions><button class="btn btn-sm" @click="loadTh(true)">重新读取</button></template>' +
    '      <x-skel v-if="th.loading" :n="6"></x-skel>' +
    '      <x-banner kind="warn" icon="alert" v-else-if="th.error">{{ th.error }}</x-banner>' +
    "      <div v-else>" +
    '        <x-banner kind="warn" icon="alert" v-if="th.data && th.data.last_error">' +
    '          <div><b>memory.toml 解析有问题</b><div class="small">{{ th.data.last_error }}</div>' +
    '          <div class="small faint">解析失败时服务会回落到内置默认值；修好再保存一次。</div></div></x-banner>' +
    '        <x-kv class="mb">' +
    "          <x-kv-row label=\"config_path\" mono :value=\"(th.data && th.data.config_path) || '（未定位到 vault）'\"></x-kv-row>" +
    "          <x-kv-row label=\"生效延迟\" :value=\"'约 ' + (th.data ? th.data.ttl : 2) + ' 秒（无需重启服务）'\"></x-kv-row>" +
    '        </x-kv>' +
    '        <div class="row2">' +
    '          <x-field v-for="k in thKeys" :key="k" :label="k">' +
    '            <template #label><x-pill :kind="srcPill(th.data.keys[k].source)">{{ srcLabel(th.data.keys[k].source) }}</x-pill>' +
    '              <x-pill mono>默认 {{ th.data.keys[k].default }}</x-pill></template>' +
    '            <input class="input mono" type="number" step="any" v-model="th.draft[k]" @input="watchTh()">' +
    '            <template #hint>' +
    '              <div class="hint" v-if="thDesc(k)">{{ thDesc(k) }}</div>' +
    "              <div class=\"hint mono\" v-if=\"th.data.keys[k].source==='env'\">被环境变量 {{ th.data.keys[k].env }} 覆盖 —— 保存进 memory.toml 也不会生效</div>" +
    '            </template>' +
    '          </x-field>' +
    '        </div>' +
    '        <div class="center wrap mt">' +
    "          <button class=\"btn btn-primary\" :class=\"{loading:th.saving}\" @click=\"saveTh\"><span v-html=\"svg('save')\"></span>保存到 memory.toml</button>" +
    '          <button class="btn btn-ghost" @click="loadTh(true)">放弃修改</button>' +
    '          <button class="btn btn-ghost" @click="resetTh">填内置默认值</button>' +
    '          <x-pill kind="warn" v-if="th.dirty">有未保存修改</x-pill>' +
    '        </div>' +
    '        <div class="hint mt">' +
    '          <div>热度类阈值（<span class="mono">heat_*</span>）只影响分层建议：核心页（记忆索引、近期工作动态等）一律豁免，不参与升降。</div>' +
    '          <div>写入前会先备份到 vault 之外，且只写这一个配置文件，绝不触碰任何 .md 笔记。</div>' +
    '        </div>' +
    '      </div></x-card>' +
    "  </div>" +
    "  <div v-if=\"tab==='fns' && fnsDraft\">" +
    '    <x-card title="快速导入" sub="粘贴 JSON，自动填表">' +
    '      <template #actions><x-pill mono>JSON</x-pill></template>' +
    '      <x-banner icon="file">' +
    '        <div>把 FNS 客户端的 <span class="mono">config.json</span> 整段粘进来，自动识别 <span class="mono">api / apiToken / vault / client</span> 等字段，填入下方表单。</div>' +
    '        <div class="small faint">只填表、不写盘；识别不了的键会列出来，不会被静默吞掉。</div></x-banner>' +
    '      <x-field label="JSON 内容">' +
    '        <textarea class="textarea" rows="5" v-model="importText" @input="importReport=null" placeholder=\'{"api":"https://obs.example.com:58889","apiToken":"eyJhbGci…","vault":"ai-memory","client":"workbuddy-netbook"}\'></textarea>' +
    '      </x-field>' +
    '      <div class="center wrap mt">' +
    '        <button class="btn btn-primary" @click="importJson"><span v-html="svg(\'download\')"></span>识别并填充</button>' +
    '        <button class="btn btn-ghost" @click="clearImport">清空</button>' +
    '      </div>' +
    '      <x-banner kind="ok" icon="check" v-if="importReport && !importReport.error">' +
    '        <div><b>识别到 {{ importReport.filled.length }} 个字段</b>：' +
    '          <span v-for="(it, i) in importReport.filled" :key="it.field"><span class="mono">{{ it.from }}</span> → {{ it.label }}<span v-if="i < importReport.filled.length - 1">、</span></span>' +
    '          <span v-if="importReport.overwritten">（其中 {{ importReport.overwritten }} 项覆盖了原有值）</span></div>' +
    '        <div class="small faint" v-if="importReport.unknown.length">未识别、已跳过：<span class="mono">{{ importReport.unknown.join(\'、\') }}</span></div>' +
    '        <div class="small faint">表单已填入，<b>核对无误后点下方「保存 FNS 配置」</b>才会写盘。</div></x-banner>' +
    '      <x-banner kind="err" icon="alert" v-else-if="importReport && importReport.error">' +
    '        <div><b>无法识别</b><div class="small">{{ importReport.error }}</div></div></x-banner>' +
    '    </x-card>' +
    '    <x-card class="mt" title="FNS 同步">' +
    '      <template #actions><x-pill mono>客户端配置</x-pill></template>' +
    '      <x-banner icon="info">' +
    '        <div>这些字段保存在 <b>vault 之外的配置文件</b>里（当前：<span class="mono">{{ R.config_path }}</span>），因此不会被同步出去。控制台负责「配置值的落账与导出」，实际同步由 FNS 客户端读取该配置执行。</div></x-banner>' +
    '      <div class="row2">' +
    '        <x-field label="服务器地址">' +
    '          <input class="input mono" v-model="fnsDraft.server" placeholder="https://obs.example.com:58889" @input="S.fns.dirty=true"></x-field>' +
    '        <x-field label="账号">' +
    '          <input class="input" v-model="fnsDraft.account" placeholder="登录账号" @input="S.fns.dirty=true"></x-field>' +
    '        <x-field label="Vault 名">' +
    '          <input class="input mono" v-model="fnsDraft.vault" placeholder="ai-memory-centralization" @input="S.fns.dirty=true"></x-field>' +
    '        <x-field label="口令 / Token">' +
    '          <div class="input-group"><input class="input mono" :type="showToken?\'text\':\'password\'" v-model="fnsDraft.token" placeholder="仅存本地，不进 vault" @input="S.fns.dirty=true">' +
    "            <button class=\"btn\" @click=\"showToken=!showToken\">{{ showToken ? '隐藏' : '显示' }}</button></div></x-field>" +
    '      </div>' +
    '      <x-field label="排除规则">' +
    '        <template #hint>一行一条。当前 {{ (fnsDraft.exclude||[]).length }} 条。</template>' +
    '        <textarea class="textarea" v-model="fnsDraft.excludeText" placeholder="每行一条，如：.obsidian/**&#10;*.tmp" @input="fnsDraft.exclude=fnsDraft.excludeText.split(/[\\r\\n,]+/).filter(Boolean); S.fns.dirty=true"></textarea>' +
    '      </x-field>' +
    '      <div class="center wrap mt">' +
    "        <button class=\"btn btn-primary\" @click=\"saveFns\"><span v-html=\"svg('save')\"></span>保存 FNS 配置</button>" +
    '        <x-pill kind="warn" v-if="S.fns.dirty">有未保存修改</x-pill>' +
    '      </div></x-card>' +
    "  </div>" +
    "  <div v-if=\"tab==='about'\">" +
    '    <x-card title="关于">' +
    '      <template #head><x-pill mono>v{{ R.version }}</x-pill></template>' +
    '      <x-kv>' +
    "        <x-kv-row label=\"控制台\" :value=\"'ai-memory Web 控制台 v' + R.version\"></x-kv-row>" +
    "        <x-kv-row label=\"监听\" mono :value=\"R.host + ':' + R.port\"></x-kv-row>" +
    '        <x-kv-row label="静态目录" mono :value="R.static"></x-kv-row>' +
    '        <x-kv-row label="配置存储" mono :value="R.config_path"></x-kv-row>' +
    "        <x-kv-row label=\"鉴权\" :value=\"R.auth_enabled ? '已启用（Bearer token）' : '关闭（仅回环可访问）'\"></x-kv-row>" +
    '      </x-kv>' +
    '      <div class="divider"></div>' +
    '      <x-banner icon="shield">' +
    '        <div><b>三道闸门</b><div class="small">① 破坏性工具执行前二次确认；② 删除走回收站（可 <span class="mono">memory_restore</span> 找回）；③ 审计记录写在 vault 之外。</div></div></x-banner>' +
    '      <div class="hint">控制台不直连 vault、不在进程内直调：对记忆库的一切读写都走完整 MCP 协议，因此与其它 AI 客户端共享同一把锁与同一套索引维护。</div>' +
    '    </x-card>' +
    "  </div>" +
    "    </div>" +
    "  </div>" +
    "</div>",
};
/* ⚠️ 不要在这里再给 SettingsPage.data 赋一次值。
   历史上这里存在一个**漏掉 `tabs: SETTINGS_TABS`** 的覆盖版本，把组件内联 data
   顶掉了 ⇒ 配置页的四个页签（以及现在的左侧 CONFIGURATION 分组导航）整块渲染
   不出来，只能靠深链 ?tab= 切到指定页。已删除该覆盖，data 以组件内联版本为准。 */

export { SETTINGS_TABS, SettingsPage };
