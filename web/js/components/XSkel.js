import { computed } from "../../vendor/vue.esm-browser.prod.js";

var XSkel = {
  props: {
    n: { default: 3 },
    w: { default: "" },
    block: { type: Boolean },
    mt: { type: Boolean },
  },
  computed: {
    cls: function () {
      return this.block
        ? ["block"].concat(this.mt ? ["mt"] : [])
        : ["line"].concat(this.w ? [this.w] : []);
    },
  },
  template:
    '<div><div v-for="i in n" :key="i" class="skel" :class="cls"></div></div>',
};

export { XSkel };
