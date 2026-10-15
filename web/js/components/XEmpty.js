var XEmpty = {
  props: {
    icon: { default: "inbox" },
    title: { default: "" },
    text: { default: "" },
  },
  template:
    '<div class="empty"><div class="empty-ico" v-html="svg(icon)"></div>' +
    '<b v-if="title">{{ title }}</b><slot></slot>' +
    '<div class="small" v-if="text">{{ text }}</div></div>',
};

export { XEmpty };
