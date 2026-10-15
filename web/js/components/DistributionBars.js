var DistributionBars = {
  props: {
    rows: {
      default: function () {
        return [];
      },
    },
    total: { default: 0 },
  },
  methods: {
    pct: function (v) {
      return this.total > 0
        ? Math.max(2, Math.round((v / this.total) * 100))
        : 0;
    },
  },
  template:
    '<div class="tier-row stagger-in">' +
    '  <div class="tier-line" v-for="(r,i) in rows" :key="r.label" :style="\'--i:\' + i">' +
    '    <span class="nm">{{ r.label }}</span>' +
    '    <span class="bar"><span class="bar-fill" :style="{ width: pct(r.value)+\'%\', transitionDelay: (i*40)+\'ms\' }"></span></span>' +
    '    <span class="vl">{{ r.value }}</span>' +
    "  </div>" +
    "</div>",
};

export { DistributionBars };
