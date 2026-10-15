/* ======================================================================
 * 12. 基础组件
 *
 * 这一层是本文件里唯一「复用」的落点。改造前，卡片头/键值行/表单字段/空态/
 * 胶囊/骨架/横幅/统计块这些标记是**各自内联**在四个页面里的 ——
 * kv-row ×21、card-head ×15、card-body ×14、field ×12、label ×11、empty ×8。
 * 现在定义一次，页面里只剩组合。
 *
 * 约定：不声明 `emits`。根元素是普通 div/span，父级写的 @click / class / style
 * 由 Vue 的 attribute fallthrough 自动落到根元素上，省掉一整套样板。
 * ==================================================================== */
var XCard = {
  props: {
    title: { default: "" },
    sub: { default: "" },
    tight: { type: Boolean },
    bare: { type: Boolean },
    sticky: { type: Boolean },
  },
  template:
    '<div class="card" :class="{ \'sticky-side\': sticky }">' +
    '  <div class="card-head" v-if="title || sub || $slots.head || $slots.actions">' +
    '    <h3 v-if="title">{{ title }}</h3><span class="sub" v-if="sub">{{ sub }}</span>' +
    '    <slot name="head"></slot><span class="spacer"></span><slot name="actions"></slot>' +
    "  </div>" +
    '  <slot v-if="bare"></slot>' +
    '  <div class="card-body" v-else :class="{ tight: tight }"><slot></slot></div>' +
    "</div>",
};

export { XCard };
