var XPill = {
  props: { kind: { default: "" }, mono: { type: Boolean } },
  template:
    '<span class="pill" :class="[kind, { mono: mono }]"><slot></slot></span>',
};

export { XPill };
