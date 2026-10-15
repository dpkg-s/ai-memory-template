var XField = {
  props: {
    label: { default: "" },
    hint: { default: "" },
    req: { type: Boolean },
    flat: { type: Boolean },
  },
  template:
    '<div class="field" :style="flat ? \'margin:0\' : null">' +
    '  <label class="label" v-if="label || $slots.label">{{ label }}' +
    '    <span class="req" v-if="req">*</span><slot name="label"></slot></label>' +
    "  <slot></slot>" +
    '  <div class="hint" v-if="hint">{{ hint }}</div><slot name="hint"></slot>' +
    "</div>",
};

export { XField };
