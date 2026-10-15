var XBanner = {
  props: {
    kind: { default: "info" },
    icon: { default: "info" },
    flush: { type: Boolean },
  },
  template:
    '<div class="banner" :class="kind" :style="flush ? \'margin:0\' : null">' +
    '<span v-html="svg(icon)"></span><slot></slot></div>',
};

export { XBanner };
