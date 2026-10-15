var XKvRow = {
  props: {
    label: { default: "" },
    mono: { type: Boolean },
    value: { default: null },
  },
  template:
    '<div class="kv-row"><div class="kv-k">{{ label }}</div>' +
    '<div class="kv-v" :class="{ mono: mono }">' +
    "<slot>{{ value === null || value === '' ? '—' : value }}</slot></div></div>",
};

export { XKvRow };
