var XListItem = {
  props: {
    title: { default: "" },
    sub: { default: "" },
    mono: { type: Boolean },
  },
  template:
    '<div class="list-item"><div class="li-main">' +
    '  <div class="li-title" :class="{ mono: mono }">{{ title }}</div>' +
    '  <div class="li-sub" v-if="sub || $slots.sub"><slot name="sub">{{ sub }}</slot></div>' +
    "  <slot></slot></div>" +
    '<div class="li-right"><slot name="right"></slot></div></div>',
};

export { XListItem };
