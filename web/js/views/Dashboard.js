import { computed, nextTick } from "../../vendor/vue.esm-browser.prod.js";
import { RECENT_DAYS, loadTools } from "../core/data.js";
import { go } from "../core/router.js";
import { S } from "../core/store.js";
import { sectionRows } from "../core/util.js";
import { selectTool } from "../features/console.js";
import { openNote } from "../features/library.js";

/* ======================================================================
 * 13-a. 「最近更新」热力日历 · 纯函数层（2026-10-15）
 *
 * 主人原话：「概览的最近更新改成〔日历卡片〕这种类型的，但是不需要这么多东西，
 * 然后表达那天的修改量不使用球，修改当天日期的背景色」。
 * 落地口径（已落地方案 A · 右栏换芯）：
 *   · 位置不动 —— 仍是 §17c 的右栏窄轨道，卡内由「竖列表」换成「月历网格」；
 *   · 修改量**不用圆点**，直接改当日格子的**背景色**（§17e-2 四档）；
 *   · 砍掉参考稿里的月度长摘要 / 分类 chips / 新增与更新两张分组表 / 工具调用行，
 *     只留「一行月度摘要 + 选中日条目」。
 *
 * 数据面：**后端已按天聚合**（`GET /api/activity`）⇒ 这里只做展示，不碰日期数学。
 * ==================================================================== */

/* 周一起始（与参考稿一致）。 */
var CAL_WEEK = ["一", "二", "三", "四", "五", "六", "日"];

/* 空态/占位用的骨架值：数据没到之前也保证 computed 不炸。 */
var CAL_EMPTY = {
  days: RECENT_DAYS,
  total: 0,
  today: "",
  counts: {},
  items: {},
};

function pad2(n) {
  return (n < 10 ? "0" : "") + n;
}
function dayKey(d) {
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
}
/* 修改量 → 热力档位。切点由 `heatCuts` 用**窗口内活跃天的四分位**给出
   （p25 / p50 / p75），不是写死的绝对阈值：
     · 绝对阈值在本库会饱和 —— 实测 120 天内 35 个活跃天，条数中位 5、p75 14、
       峰值 95；按「1 / 2 / 3~5 / ≥6」分档会有 **15 天全挤进最深档**，
       31 条与 6 条同色，日历失去区分度；
     · 改用「除以峰值取比例」又反着塌 —— 峰值 95 是离群点，32 天全落最浅档。
   四分位对离群点稳健，且切点是**条数的函数**（不是排名）⇒ 同一条数永远同色、
   翻月份不抖，口径也跨月一致（同一个全局基准，不是每月各算一遍）。
   颜色只表达**相对热度**；绝对量由摘要行「本月 N 条 · 峰值 X 日（Y 条）」给。 */
function heatLevel(n, cuts) {
  if (!n) return 0;
  if (!cuts) return 4;
  if (n <= cuts[0]) return 1;
  if (n <= cuts[1]) return 2;
  if (n <= cuts[2]) return 3;
  return 4;
}

/* 条目标题 → 「前缀 + 主干」两段（2026-10-15 第五轮）。
   本库条目标题形如「项目_记忆库v3.1概览最近更新改热力日历20261015」：类别前缀
   （项目_ / 经验_ / 运维_）在一屏 12 条里高度重复，既吃横向空间、又堆出视觉噪声
   —— 右栏收窄到 312px 后标题可用宽仅约 214px（≈31 个 11.5px 等宽字符），
   重复前缀直接决定了长标题会不会被截断。
   拆段交给模板分别渲染，前缀**淡化**显示：信息不丢，权重降下来。
   只认**第一个**下划线且前缀 ≤5 字符（项目_ / 经验_ / 运维_ 都是 3），
   超长的一律视为标题本身含下划线、不切 —— 宁可少切，不可切错。 */
function splitPrefix(t) {
  var s = String(t == null ? "" : t);
  var i = s.indexOf("_");
  if (i <= 0 || i > 4) return { p: "", n: s };
  return { p: s.slice(0, i + 1), n: s.slice(i + 1) };
}

/* ======================================================================
 * 13. 页面组件
 * ==================================================================== */
var Dashboard = {
  props: {},
  data: function () {
    return {
      S: S,
      week: CAL_WEEK,
      /* 日历视图态只属于本组件，不进全局 store（刷新回默认落点，不必持久化）。 */
      calY: 0,
      calM: 0, // 0-11
      calSel: "", // 选中日 YYYY-MM-DD
      calInit: false, // 默认落点是否已定过（数据是异步到的，只能定一次）
    };
  },
  computed: {
    nums: function () {
      return (S.overview && S.overview.parsed.nums) || {};
    },
    typeRows: function () {
      return S.overview ? sectionRows(S.overview.parsed, "记忆类型分布") : [];
    },
    confRows: function () {
      return S.overview ? sectionRows(S.overview.parsed, "可信度分布") : [];
    },
    scopeRows: function () {
      return S.overview ? sectionRows(S.overview.parsed, "作用域分布") : [];
    },
    tierRows: function () {
      var n = this.nums;
      return [
        { label: "活跃 hot", value: n.hot || 0 },
        { label: "常温 warm", value: n.warm || 0 },
        { label: "归档 cold", value: n.cold || 0 },
      ];
    },
    /* 概览页「热门标签」chips。数据源 = memory_stats 的 `## 热门标签 TOP 10`
       段落，交给 sectionRows 按前缀 "热门标签" 收行。
       ⚠️ 2026-10-15 事故复盘：本组件为「最近更新改热力日历」整体重写时，
       这个 computed 被连带删掉，而模板里的 `v-for="t in tagRows"` 没同步移除。
       Vue 对 undefined 做 v-for **不抛错**、只渲染空 ⇒ 卡片标题「热门标签」
       与右侧四个动作按钮都在，唯独 chips 全没了（"标签不见了"）。
       教训：重写组件要按**模板引用的标识符全集**回查 computed / methods，
       不能只盯着本次要改的那一块。 */
    tagRows: function () {
      return S.overview ? sectionRows(S.overview.parsed, "热门标签") : [];
    },
    act: function () {
      return S.recentActivity || CAL_EMPTY;
    },
    counts: function () {
      return this.act.counts;
    },
    /* 热力切点 = 窗口内**有更新的天**的 p25 / p50 / p75（见 heatLevel 的注释）。 */
    heatCuts: function () {
      var counts = this.counts;
      var vals = Object.keys(counts)
        .map(function (k) {
          return counts[k];
        })
        .sort(function (a, b) {
          return a - b;
        });
      if (!vals.length) return null;
      var q = function (p) {
        return vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))];
      };
      return [q(0.25), q(0.5), q(0.75)];
    },
    /* 「今天」以**后端**回传的为准：后端已把 UTC 换算成本机时区，
       前端若再算一次就可能出现两套口径（差一天的经典故障）。 */
    todayK: function () {
      return this.act.today || dayKey(new Date());
    },
    /* 数据实际覆盖的月份区间（只用来禁用越界的翻页，不让用户翻进空月份）。 */
    monthRange: function () {
      var keys = Object.keys(this.counts).sort();
      if (!keys.length) return null;
      var a = keys[0].split("-");
      var b = keys[keys.length - 1].split("-");
      return { min: +a[0] * 12 + (+a[1] - 1), max: +b[0] * 12 + (+b[1] - 1) };
    },
    calIdx: function () {
      return this.calY * 12 + this.calM;
    },
    calLabel: function () {
      return this.calY + "年" + (this.calM + 1) + "月";
    },
    calCanPrev: function () {
      var r = this.monthRange;
      return !r || this.calIdx > r.min;
    },
    calCanNext: function () {
      var r = this.monthRange;
      return !r || this.calIdx < r.max;
    },
    /* 恒为 42 格（6 行）—— 月份切换时卡片高度不跳。前后补格用相邻月日期灰显，
       不给热力、不可点。 */
    calCells: function () {
      var y = this.calY;
      var m = this.calM;
      var lead = (new Date(y, m, 1).getDay() + 6) % 7; // 周一为首列
      var counts = this.counts;
      var cuts = this.heatCuts;
      var todayK = this.todayK;
      var out = [];
      for (var i = 0; i < 42; i++) {
        var d = new Date(y, m, 1 - lead + i);
        var k = dayKey(d);
        var edge = d.getMonth() !== m;
        var n = edge ? 0 : counts[k] || 0;
        var cls = "";
        if (edge) cls += " out";
        else if (n) cls += " l" + heatLevel(n, cuts);
        if (!edge && k === todayK) cls += " today";
        if (k === this.calSel) cls += " on";
        out.push({
          key: k,
          day: d.getDate(),
          cls: cls,
          out: edge,
          tip: edge ? "" : m + 1 + "月" + d.getDate() + "日 · " + n + " 条更新",
        });
      }
      return out;
    },
    /* 月度一行摘要：总量 + 峰值。参考稿那段长摘要在方案 a 里被砍掉。 */
    calSummary: function () {
      var y = this.calY;
      var m = this.calM;
      var counts = this.counts;
      var total = 0;
      var peakK = "";
      var peak = 0;
      Object.keys(counts).forEach(function (k) {
        var p = k.split("-");
        if (+p[0] !== y || +p[1] - 1 !== m) return;
        var n = counts[k];
        total += n;
        if (n > peak) {
          peak = n;
          peakK = k;
        }
      });
      if (!total) return this.calLabel + "无更新";
      var pd = peakK.split("-");
      return (
        "本月 " + total + " 条 · 峰值 " + +pd[1] + "月" + +pd[2] + "日（" + peak + " 条）"
      );
    },
    /* 选中日的条目：[["HH:MM", "标题", "摘要"], …] */
    /* 选中日的条目。后端给的是 `[HH:MM, 标题, 摘要]` 三元组数组，这里就地拆出
       「前缀 / 主干」两段（splitPrefix）供模板分色渲染，避免在模板里重复调用。 */
    calList: function () {
      return (this.act.items[this.calSel] || []).map(function (r) {
        var s = splitPrefix(r[1]);
        return { t: r[0], title: r[1], sum: r[2], p: s.p, n: s.n };
      });
    },
  },
  watch: {
    /* 数据是异步到的：首帧 counts 还是空的，默认落点只能等数据到了再定一次。 */
    act: function () {
      if (!this.calInit && this.act.total) this.calReset();
    },
  },
  mounted: function () {
    if (this.act.total) this.calReset();
  },
  methods: {
    /* 默认落点：先看今天；今天没更新就落到**最近有更新的一天**（keys 升序取末位），
       并把那个月翻出来 —— 打开概览就该直接看到「最近发生了什么」。 */
    calReset: function () {
      var keys = Object.keys(this.counts).sort();
      var todayK = this.todayK;
      var k = this.counts[todayK] ? todayK : keys[keys.length - 1] || todayK;
      this.calSel = k;
      var p = k.split("-");
      this.calY = +p[0];
      this.calM = +p[1] - 1;
      this.calInit = true;
    },
    calStep: function (n) {
      var d = new Date(this.calY, this.calM + n, 1);
      this.calY = d.getFullYear();
      this.calM = d.getMonth();
    },
    calToday: function () {
      var k = this.todayK;
      this.calSel = k;
      var p = k.split("-");
      this.calY = +p[0];
      this.calM = +p[1] - 1;
      this.calInit = true;
    },
    calPick: function (c) {
      if (c.out) return;
      this.calSel = c.key;
    },
    jumpTool: function (name) {
      go("console");
      loadTools().then(function () {
        var t = S.tools.filter(function (x) {
          return x.name === name;
        })[0];
        if (t) selectTool(t);
      });
    },
    openNote: function (t) {
      go("library");
      nextTick(function () {
        openNote(t);
      });
    },
  },
  template:
    '<div class="page">' +
    '  <x-banner kind="err" icon="alert" v-if="S.overviewError">' +
    '    <div><b>无法加载概览</b><div class="small">{{ S.overviewError }}</div>' +
    '    <div class="small faint">确认 MCP 端点已启动：<code>python serve_http.py</code></div></div></x-banner>' +
    '  <div class="grid ov">' +
    '    <div class="ov-main">' +
    '      <div class="grid stats stagger" v-if="S.overview">' +
    '        <x-stat :i="0" icon="layers" label="总条目" :value="nums.total||0">' +
    '          <template #foot>近 30 天更新 {{ fmtNum(nums.recent30||0) }} 条</template></x-stat>' +
    '        <x-stat :i="1" icon="bolt" label="活跃 hot" :value="nums.hot||0">' +
    '          <template #foot>常温 {{ fmtNum(nums.warm||0) }} · 归档 {{ fmtNum(nums.cold||0) }}</template></x-stat>' +
    '        <x-stat :i="2" icon="check" label="已核验" :value="nums.verified||0">' +
    '          <template #foot>共 {{ fmtNum(nums.total||0) }} 条中</template></x-stat>' +
    '        <x-stat :i="3" icon="link" label="孤立笔记" :value="nums.orphans||0">' +
    '          <template #foot>从未读取 {{ fmtNum(nums.neverRead||0) }} 条</template></x-stat>' +
    "      </div>" +
    /* 2026-10-14「grid stats stagger 加载时保留骨架」：
      原条件 `!S.overviewLoading || S.overview` 有两个毛病 ——
        ① `overviewLoading` 初值 false，而 boot 是 about→health→overview **链式**请求，
           三段都回来前渲染的是「4 张 0 值的假卡」；
        ② overviewLoading 一转为 true（overview 仍为 null）v-if 即为假 ⇒ 整块**消失**，
           下方内容上跳，数据到达再跳回来。
      改为「有数据才画真卡，否则画 4 块等高骨架」；骨架高度与 `.card.stat` 对齐
      （见 style.css §17c-4），两种态不跳动。 */
    '      <div class="grid stats" v-else>' +
    '        <div class="skel stat-skel" v-for="i in 4" :key="i"></div>' +
    "      </div>" +
    '      <div class="grid two mt">' +
    '        <x-card title="热度分层" sub="tier 分布">' +
    '          <distribution-bars :rows="tierRows" :total="nums.total||0"></distribution-bars>' +
    '          <div class="divider"></div>' +
    '          <div class="legend"><span><i style="background:var(--accent)"></i>hot 高频</span>' +
    '            <span><i style="background:var(--info)"></i>warm 常温</span>' +
    '            <span><i style="background:var(--text-faint)"></i>cold 归档</span></div></x-card>' +
    '        <x-card title="记忆类型" sub="type 分布">' +
    '          <distribution-bars v-if="typeRows.length" :rows="typeRows" :total="nums.total||0"></distribution-bars>' +
    '          <x-empty v-else icon="layers">暂无数据</x-empty></x-card>' +
    "      </div>" +
    '      <x-card class="mt ov-tags" title="热门标签" sub="TOP" tight>' +
    '        <div class="tags-row">' +
    '          <div class="tags-scroll stagger-in">' +
    '            <span class="tag" v-for="(t, i) in tagRows" :key="t.label" :style="\'--i:\' + i" @click="S.library.tag=t.label; S.library.mode=\'browse\'; go(\'library\'); browseLibrary()">{{ t.label }} · {{ t.value }}</span>' +
    "          </div>" +
    '          <div class="tags-actions">' +
    '            <button class="btn btn-sm" v-for="q in [\'memory_stats\',\'memory_doctor\',\'memory_audit\',\'memory_orphans\']" :key="q" @click="jumpTool(q)">' +
    "              <span v-html=\"svg('play')\"></span>{{ toolZhName(q) || q }}</button></div>" +
    "        </div></x-card>" +
    "    </div>" +
    /* 右栏 = 热力日历（§17e-2）。原来的竖列表被换掉，但**位置、面板骨架、等高口径
       全部不动** —— `.ov-side` / `.ide-panel` / `.ide-panel-body` 原样沿用，
       当日条目也仍走 `.ide-rows`，所以页级铺满规则（§17c-2 / §17c-3）零回归。 */
    '    <x-card bare class="ov-side ide-panel">' +
    '      <div class="card-head ide-panel-head">' +
    '        <h3 class="mono">最近更新</h3>' +
    '        <span class="spacer"></span>' +
    '        <button class="btn btn-sm" @click="calToday">回到今天</button>' +
    "      </div>" +
    '      <div class="ide-panel-body">' +
    '        <x-skel v-if="S.recentLoading && !act.total" :n="4"></x-skel>' +
    '        <template v-else-if="act.total">' +
    '          <div class="cal-nav">' +
    '            <button class="cal-step" :disabled="!calCanPrev" @click="calStep(-1)" title="上个月">\u2039</button>' +
    '            <span class="cal-title mono">{{ calLabel }}</span>' +
    '            <button class="cal-step" :disabled="!calCanNext" @click="calStep(1)" title="下个月">\u203a</button>' +
    "          </div>" +
    '          <div class="cal-week"><span v-for="w in week" :key="w">{{ w }}</span></div>' +
    '          <div class="cal-grid">' +
    '            <button v-for="(c, i) in calCells" :key="c.key" class="cal-cell" :class="c.cls"' +
    '                    :style="\'--i:\' + i" :disabled="c.out" :title="c.tip" @click="calPick(c)">{{ c.day }}</button>' +
    "          </div>" +
    '          <div class="cal-sum">{{ calSummary }}</div>' +
    '          <div class="ide-rows cal-rows">' +
    '            <div class="ide-row" v-for="(r, i) in calList" :key="r.t + r.title" :style="\'--i:\' + i" @click="openNote(r.title)" :title="r.sum">' +
    '              <span class="ide-row-t">{{ r.t }}</span>' +
    '              <span class="ide-row-n"><span class="ide-row-p" v-if="r.p">{{ r.p }}</span>{{ r.n }}</span></div>' +
    "          </div>" +
    "        </template>" +
    '        <x-empty v-else icon="clock">近 {{ act.days }} 天没有更新</x-empty>' +
    "      </div></x-card>" +
    "  </div>" +
    "</div>",
};

export { Dashboard };
