import { h, onBeforeUnmount, onMounted, ref, watch } from "../../vendor/vue.esm-browser.prod.js";
import { fmtNum } from "../core/util.js";
import { countTo, kill } from "../core/anim.js";

/* ======================================================================
 * 11. 组件
 * ==================================================================== */
var AnimatedNumber = {
  props: { value: { default: 0 } },
  setup: function (props) {
    var shown = ref(0);
    var tw = null;
    function run(to) {
      /* 先 kill 再起新的：数字在 720ms 内被反复刷新时（概览页连续 loadOverview），
         两个 tween 会同时往 shown 上写，读数是两段插值交替 —— 看着像卡顿。
         原实现用 `cancelAnimationFrame(raf)` 做过同一件事，改 GSAP 后等价物是
         tween.kill()。 */
      kill(tw);
      tw = countTo({
        from: shown.value,
        to: to,
        apply: function (v) {
          shown.value = v;
        },
      });
    }
    onMounted(function () {
      run(Number(props.value) || 0);
    });
    watch(
      function () {
        return props.value;
      },
      function (v) {
        run(Number(v) || 0);
      },
    );
    /* 原实现漏了这一步：组件卸载时 rAF 仍在跑（虽无 DOM 可写，但会持有闭包）。
       GSAP 的 tween 由全局时间线托管，不 kill 会一直留在列表里。 */
    onBeforeUnmount(function () {
      kill(tw);
      tw = null;
    });
    return function () {
      return h("span", { class: "count-up" }, fmtNum(shown.value));
    };
  },
};

export { AnimatedNumber };
