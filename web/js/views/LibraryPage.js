import { computed } from "../../vendor/vue.esm-browser.prod.js";
import { S, toast } from "../core/store.js";
import { fmtNum, mdRender } from "../core/util.js";
import { browseLibrary, onMdClick, openNote } from "../features/library.js";

var LibraryPage = {
  data: function () {
    return { S: S };
  },
  computed: {
    L: function () {
      return S.library;
    },
    tagsOf: function () {
      return function (it) {
        return (it.tags || []).slice(0, 4);
      };
    },
    rendered: function () {
      return this.L.note ? mdRender(this.L.note.body) : "";
    },
    /* 节点信息 · 元数据：只挑「节点身份」字段；summary / source_context 这类
       长文本不进面板（否则右栏会被一句话撑爆）。
       2026-10-14 汉化：键名走中文（口径 = 概览页既有译法：热度 / 读取 / 置信度），
       **值保持原文**（type=project、tier=warm 等是数据值，汉化需另建 TYPE_ZH/TIER_ZH）。 */
    nodeMeta: function () {
      var n = this.L.note || {};
      var m = n.meta || {};
      var rows = [
        ["路径", n.path || n.title],
        ["类型", m.type],
        ["大小", n.total_chars ? fmtNum(n.total_chars) + " 字符" : ""],
        ["创建", this.dateOnly(m.created)],
        ["更新", this.dateOnly(m.updated)],
        ["热度", m.tier],
        ["读取", m.access_count],
        ["版本", m.version],
        ["状态", m.status],
        ["置信度", m.confidence],
      ];
      return rows
        .filter(function (r) {
          return r[1] !== undefined && r[1] !== null && r[1] !== "";
        })
        .map(function (r) {
          return { k: r[0], v: r[1] };
        });
    },
    nodeTags: function () {
      var t =
        (this.L.note && this.L.note.meta && this.L.note.meta.tags) || [];
      if (!Array.isArray(t)) t = [t];
      return t.filter(Boolean);
    },
    /* 节点信息 · 关联节点：从正文抽 [[双链]]，按出现次数当 references。 */
    nodeLinks: function () {
      var b = (this.L.note && this.L.note.body) || "";
      var hits = b.match(/\[\[[^\]]+\]\]/g) || [];
      var count = {};
      var order = [];
      hits.forEach(function (x) {
        var t = x
          .replace(/^\[\[/, "")
          .replace(/\]\]$/, "")
          .split("|")[0]
          .trim();
        if (!t) return;
        if (!count[t]) {
          count[t] = 0;
          order.push(t);
        }
        count[t] += 1;
      });
      return order.map(function (t) {
        return { title: t, n: count[t] };
      });
    },
  },
  methods: {
    onMd: function (ev) {
      onMdClick(ev);
    },
    dateOnly: function (s) {
      return s ? String(s).slice(0, 10) : "";
    },
    openLink: function (t) {
      openNote(t);
    },
    /* 节点信息 · 标签 点击（2026-10-14 修）
       原实现 `@click="openLink(t)"` → `openNote(t)` → `memory_read(title=标签名)`：
       `memory_read` 按**条目标题**精确查找，而标签名从来不是标题 ⇒ 必然 not found，
       中间栏渲染「（未找到）」、右栏「节点信息」被清空。这就是「点 TAGS 列不出该标签
       的笔记」的根因 —— **接线接错对象**，不是标签过滤能力缺失。

       标签过滤早已具备：`memory_list(tag=…)` 在服务端按 frontmatter tags 过滤
       （server.py `memory_list`，大小写不敏感），页面顶部本就有「标签过滤」输入框。
       故复用概览页「热门标签」的既定接法：写回 L.tag → 切 browse → 重新列举。
       同标签再点是**取消**（进得去也出得来）。 */
    openTag: function (t) {
      var L = this.L;
      var off = L.tag === t;
      L.tag = off ? "" : t;
      L.keyword = "";
      L.mode = "browse";
      browseLibrary().then(function () {
        if (L.error) return; /* 失败已有 err toast，不再叠加成功提示 */
        if (off) toast("ok", "已取消标签过滤", "共 " + L.count + " 条");
        else toast("ok", "已按标签过滤", "「" + t + "」共 " + L.count + " 条");
      });
    },
    niTagHint: function (t) {
      return this.L.tag === t ? "取消该标签过滤" : "列出带有「" + t + "」的条目";
    },
    tierPill: function (t) {
      return t === "hot" ? "err" : t === "cold" ? "" : "info";
    },
  },
  template:
    '<div class="page">' +
    '  <x-card class="mb">' +
    '    <div class="input-group">' +
    '      <input class="input" v-model="L.keyword" placeholder="关键词搜索（标题 / 标签 / 正文）…" ' +
    '             @keyup.enter="searchLibrary()">' +
    '      <button class="btn btn-primary" @click="searchLibrary()" :class="{loading:L.loading}">' +
    "        <span v-html=\"svg('search')\"></span>搜索</button>" +
    "      <button class=\"btn\" @click=\"L.mode='browse'; L.keyword=''; browseLibrary()\">浏览</button>" +
    "    </div>" +
    '    <div class="row2 mt">' +
    '      <x-field flat label="标签过滤">' +
    '        <input class="input" v-model="L.tag" placeholder="如 mcp / v3" @keyup.enter="L.mode===\'search\'?searchLibrary():browseLibrary()"></x-field>' +
    '      <x-field flat label="热度">' +
    '        <select class="select" v-model="L.tier" @change="browseLibrary()">' +
    '          <option value="">全部</option><option value="hot">hot</option>' +
    '          <option value="warm">warm</option><option value="cold">cold</option></select></x-field>' +
    "    </div></x-card>" +
    '  <div class="grid lib">' +
    '    <x-card bare>' +
    "      <div class=\"card-head\"><h3>{{ L.mode==='search' ? '搜索结果' : '全部条目' }}</h3>" +
    '        <span class="spacer"></span>' +
    "        <x-pill>{{ L.mode==='search' ? L.searchItems.length : (L.count||L.items.length) }}</x-pill></div>" +
    '      <div class="card-body tight">' +
    '        <x-skel v-if="L.loading" :n="6"></x-skel>' +
    '        <x-empty v-else-if="L.error" icon="alert">{{ L.error }}</x-empty>' +
    "        <transition-group tag=\"div\" name=\"liblist\" class=\"list\" appear v-else-if=\"L.mode==='search' && L.searchItems.length\">" +
    '          <x-list-item v-for="(it, i) in L.searchItems" :key="it.file" :style="\'--i:\' + i" :title="it.title" @click="openNote(it.title)">' +
    '            <template #sub><span class="mono">{{ it.file }}</span></template>' +
    '            <template #right><x-pill kind="accent">打开</x-pill></template>' +
    "          </x-list-item></transition-group>" +
    "        <transition-group tag=\"div\" name=\"liblist\" class=\"list\" appear v-else-if=\"L.mode==='browse' && L.items.length\">" +
    '          <x-list-item v-for="(it, i) in L.items" :key="it.path || it.title" :style="\'--i:\' + i" :title="it.title" :sub="short(it.preview)" @click="openNote(it.title)">' +
    '            <div class="tags mt-sm"><span class="tag" v-for="tg in tagsOf(it)" :key="tg">{{ tg }}</span></div>' +
    '            <template #right><x-pill :kind="tierPill(it.tier)">{{ it.tier }}</x-pill>' +
    '              <span class="small faint nowrap">{{ it.reads }} 次</span></template>' +
    "          </x-list-item></transition-group>" +
    '        <x-empty v-else-if="L.mode===\'search\' && L.searchRaw" icon="search" title="没有命中">' +
    '          <pre class="code mt" style="text-align:left">{{ L.searchRaw }}</pre></x-empty>' +
    '        <x-empty v-else icon="book" title="还没有加载内容" text="点上面「浏览」看全部条目，或输入关键词搜索。"></x-empty>' +
    "      </div></x-card>" +
    '    <x-card sticky bare>' +
    '      <div class="card-head" v-if="L.note"><h3>{{ L.note.title }}</h3>' +
    '        <span class="spacer"></span>' +
    '        <x-pill mono v-if="L.note.total_chars">{{ fmtNum(L.note.total_chars) }} 字符</x-pill></div>' +
    '      <div class="card-body">' +
    '        <transition name="reader" mode="out-in">' +
    '        <div v-if="L.noteLoading" key="skel"><x-skel :n="1" w="w40"></x-skel><x-skel :n="1" block mt></x-skel></div>' +
    "        <div v-else-if=\"L.note\" :key=\"L.note.title || 'note'\">" +
    '          <x-banner v-if="L.note.truncated" icon="info">' +
    "            <div>正文较长，已截断显示。</div></x-banner>" +
    '          <div class="md" v-html="rendered" @click="onMd"></div>' +
    "        </div>" +
    '        <x-empty v-else key="empty" icon="file" title="未选中条目" text="左边点任意条目即可在此查看渲染结果。"></x-empty>' +
    "        </transition>" +
    "      </div></x-card>" +
    '    <x-card bare class="sticky-side">' +
    '      <div class="card-head"><h3 class="mono">节点信息</h3>' +
    '        <span class="spacer"></span>' +
    '        <x-pill mono v-if="L.note && L.note.meta && L.note.meta.tier">{{ L.note.meta.tier }}</x-pill></div>' +
    '      <div class="card-body">' +
    '        <x-empty v-if="!L.note" icon="file" title="未选中条目" text="点开任意条目查看节点信息。"></x-empty>' +
    "        <template v-else>" +
    '          <div class="ni-sec">元数据</div>' +
    '          <x-kv class="ni-kv stagger-in">' +
    '            <x-kv-row v-for="(r, i) in nodeMeta" :key="r.k" :style="\'--i:\' + i" :label="r.k" :value="r.v"></x-kv-row>' +
    "          </x-kv>" +
    '          <div class="ni-sec">标签</div>' +
    '          <div class="tags stagger-in" v-if="nodeTags.length">' +
    '            <button class="tag tg-btn" v-for="(t, i) in nodeTags" :key="t" :style="\'--i:\' + i" @click="openTag(t)" :title="niTagHint(t)">{{ t }}</button></div>' +
    '          <div class="small faint" v-else>（无标签）</div>' +
    '          <div class="ni-sec">关联节点</div>' +
    '          <div class="ni-links stagger-in" v-if="nodeLinks.length">' +
    '            <button class="ni-link" v-for="(l, i) in nodeLinks" :key="l.title" :style="\'--i:\' + i" @click="openLink(l.title)">' +
    '              <span class="mono ni-link-t">{{ l.title }}</span>' +
    '              <span class="small faint">{{ l.n }} 处引用</span></button></div>' +
    '          <div class="small faint" v-else>（无关联节点）</div>' +
    "        </template>" +
    "      </div></x-card>" +
    "  </div>" +
    "</div>",
};

export { LibraryPage };
