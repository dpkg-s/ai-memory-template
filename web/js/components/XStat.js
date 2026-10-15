var XStat = {
  props: {
    icon: { default: "layers" },
    label: { default: "" },
    value: { default: 0 },
    foot: { default: "" },
    i: { default: 0 },
  },
  template:
    '<div class="card stat raise" :style="{ \'--i\': i }">' +
    '  <div class="stat-top"><span class="stat-ico" v-html="svg(icon)"></span>{{ label }}</div>' +
    '  <div class="stat-value"><animated-number :value="value"></animated-number></div>' +
    '  <div class="stat-foot" v-if="foot || $slots.foot"><slot name="foot">{{ foot }}</slot></div></div>',
};

export { XStat };
