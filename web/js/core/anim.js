/* ============================================================================
 * core/anim.js —— GSAP 封装层
 *
 * 🔴 职责边界（改动画前必须先读这段）
 *
 *   本文件只接管**需要 JS 参与编排**的动画：
 *     · 时序串联（A 结束再 B，且中途可打断）
 *     · 目标数量运行时才知道（stagger）
 *     · 需要先测量再动画（FLIP / 高度塌陷）
 *
 *   单元素状态切换继续留在 css/6-anim.css，**不要搬到这边**：
 *     · 导航条与终端 tab 的滑动指示条、阅读器 crossfade、卡片 hover、
 *       列表项按压反馈 —— 这些走 CSS 声明式 + 合成层，零 JS 更稳也更省。
 *
 *   🔴 同一条动效只能有一处定义。两边都写会互相打架，典型症状是
 *      「JS 写的 inline transform 被 CSS 的 !important 吃掉」或
 *      「CSS transition 与 GSAP tween 同时驱动同一属性 ⇒ 抖动」。
 *
 * ---------------------------------------------------------------------------
 * 已接管
 *   · 数字滚动（components/AnimatedNumber.js）—— 原先是 30 行手写
 *     requestAnimationFrame + 手算 easeOutCubic。
 *
 * 未接管，以及为什么（都是权衡后的选择，不是遗漏）
 *   · 列表 FLIP 重排：Vue 的 <transition-group> **自带 FLIP**（.liblist-move），
 *     已正确工作。GSAP Flip 插件干的也是 FLIP，两套并存只会互相覆盖；
 *     要换必须整块替换 transition-group，收益为零、风险不小。
 *     Flip 已随 vendor 就位，将来做「跨容器共享元素迁移」时再启用。
 *   · 页面方向感知转场 / 模态回弹 / Toast 塌陷 / 折叠展开：现行 CSS 实现
 *     带完整踩坑取证（见 6-anim.css 注释，含一次 .term-sec 被撑到 8981px 的
 *     事故记录）。迁移属于「用 JS 重写已经对的 CSS」⇒ 风险 > 收益。
 *
 * ---------------------------------------------------------------------------
 * ease 对齐说明
 *   CSS 用 cubic-bezier()，GSAP 用命名 ease，两套语法不通用，下表是**近似**对齐：
 *
 *     CSS token                         本节 EASE        说明
 *     --ease      (0.22,1,0.36,1)  →   power3.out    强出场
 *     --ease-expo (0.16,1,0.30,1)  →   expo.out      同形
 *     --ease-spring (0.34,1.56,…)  →   back.out(1.56) 过冲量对齐
 *     --ease-in-out (0.65,0,0.35,1)→   power1.inOut
 *
 *   ⚠️ 要逐帧一致需引入 CustomEase（GSAP 3.13 起免费，本项目未随 vendor 引入）。
 * ========================================================================== */

var gsap = (typeof window !== "undefined" && window.gsap) || null;

/* 与 css/1-tokens.css 的时长 token 同值（单位：秒 —— GSAP 用秒，CSS 用毫秒） */
export var T = { fast: 0.16, base: 0.26, slow: 0.42, page: 0.34 };

export var EASE = {
  out: "power3.out",
  expo: "expo.out",
  spring: "back.out(1.56)",
  inOut: "power1.inOut",
  cubic: "power2.out",   /* = CSS 的 ease-out-cubic，数字滚动原用曲线 */
};

/** GSAP 是否可用（vendor 缺失时所有函数退化为「直接落终值」）。 */
export function available() {
  return !!gsap;
}

/** 用户是否偏好减弱动效。 */
export function reduced() {
  return !!(
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * 数字滚动补间。
 *
 * 为什么封装成「proxy + apply」而不是直接动 DOM：调用方是 Vue 的 ref，
 * 数值必须回到响应式系统里再由模板渲染，直接写 textContent 会被下一次
 * 渲染覆盖，也会绕过 Vue 的调度。
 *
 * @param {{from:number,to:number,apply:function(number):void,
 *          duration?:number,ease?:string}} o
 * @returns {object|null} GSAP tween；未启动（无 GSAP / reduced / 起止相同）时返回 null
 */
export function countTo(o) {
  var apply = o.apply,
    to = Number(o.to) || 0,
    from = Number(o.from) || 0;

  /* ⚠️ 退化路径要**一帧到位**，而不是把 duration 压到 0：
     后者仍会跑一轮 onUpdate 与帧调度，在 reduced 场景下是纯开销，
     也让「是否真的动过」变得不可判。 */
  if (!gsap || reduced() || from === to) {
    apply(to);
    return null;
  }

  var proxy = { v: from };
  return gsap.to(proxy, {
    v: to,
    duration: o.duration || 0.72,
    ease: o.ease || EASE.cubic,
    onUpdate: function () {
      apply(Math.round(proxy.v));
    },
    onComplete: function () {
      apply(to);
    },
  });
}

/** 停掉一个 tween（组件卸载或重新起补间前调用，避免两个补间抢同一属性）。 */
export function kill(tw) {
  if (tw && typeof tw.kill === "function") tw.kill();
}

/**
 * 逐字打印（打字机）—— 终端输出的「正在被打印」观感。
 *
 * 为什么是 JS 而不是 CSS：CSS 能做逐字只有 `width: 0 → 100%` + `steps()` 一条路，
 * 前提是 `white-space: nowrap`（否则 `steps()` 的步数 ≠ 可见字符数）。
 * 终端输出行是 `pre-wrap` **会折行**的多行文本 ⇒ 纯 CSS 方案要么强制不折行
 * （160 字预览必然横向溢出），要么退化成「整块从左到右拉开帘子」，都不是打字。
 * 逐字 = 每帧改一个字符数 ⇒ 天然属于「需要 JS 编排」的那一类（见本文件顶部职责边界）。
 *
 * ⚠️ 只改**文本节点**，不碰响应式：日志对象落盘后不再变化，Vue 的 text patch 只在
 *    vnode 的 children 字符串变化时才写 DOM ⇒ JS 打好的内容不会被下一次渲染冲掉。
 *    调用方仍需把**原文**放在 `data-full` 上，重放时以它为准（而不是读当前 textContent）。
 *
 * @param {Element} el      承载文本的元素（其 textContent 会被逐字覆盖）
 * @param {string}  text    完整文本
 * @param {object} [opts]
 * @param {Element} [opts.caret]  尾随光标元素，打字期间显示、落定后隐藏
 * @param {number}  [opts.cps]    每秒字符数（默认 300）
 * @param {number}  [opts.min]    最短时长（秒，默认 0.24）—— 短文本也要「打得出来」
 * @param {number}  [opts.delay]  起步延迟（毫秒，默认 0）
 * @param {number}  [opts.max]    超过该长度直接整段显示（默认 260）—— 防长输出拖沓
 * @param {function} [opts.onTick] 每帧回调（调用方用来让视口跟随光标）
 * @param {function} [opts.onDone] 落定回调
 * @returns {{kill:function():void}|null} 退化（未启动）时返回 null
 */
export function typeText(el, text, opts) {
  opts = opts || {};
  var full = String(text == null ? "" : text);
  var caret = opts.caret || null;
  var onTick = opts.onTick || null;
  var onDone = opts.onDone || null;
  var cps = opts.cps || 300;
  var minDur = opts.min == null ? 0.24 : opts.min;
  var maxLen = opts.max == null ? 260 : opts.max;

  var finish = function () {
    el.textContent = full;
    if (caret) caret.style.display = "none";
    if (onTick) onTick();
    if (onDone) onDone();
  };

  /* 退化路径一律**一帧到位**（与 countTo 同口径）：
     空文本没什么可打的；超长文本逐字打要好几秒、像卡住；减弱动效下必须立即完整。 */
  if (!full || reduced() || full.length > maxLen) {
    finish();
    return null;
  }

  el.textContent = "";
  if (caret) caret.style.display = "";

  var dur = Math.max(full.length / cps, minDur) * 1000;
  var t0 = null,
    raf = 0,
    timer = 0,
    dead = false;

  var step = function (ts) {
    if (dead) return;
    if (t0 === null) t0 = ts;
    var p = dur > 0 ? (ts - t0) / dur : 1;
    var n = p >= 1 ? full.length : Math.max(1, Math.floor(full.length * p));
    if (el.textContent.length !== n) el.textContent = full.slice(0, n);
    if (onTick) onTick();
    if (p >= 1) {
      raf = 0;
      finish();
      return;
    }
    raf = requestAnimationFrame(step);
  };

  timer = setTimeout(function () {
    timer = 0;
    raf = requestAnimationFrame(step);
  }, opts.delay || 0);

  return {
    kill: function () {
      dead = true;
      if (raf) cancelAnimationFrame(raf);
      if (timer) clearTimeout(timer);
      raf = 0;
      timer = 0;
    },
  };
}
