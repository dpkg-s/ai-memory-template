import { computed } from "../../vendor/vue.esm-browser.prod.js";

var JsonTree = {
  name: "JsonTree",
  props: { data: {}, name: { default: "" }, depth: { default: 0 } },
  data: function () {
    return { open: this.depth < 2 };
  },
  computed: {
    kind: function () {
      var d = this.data;
      if (d === null) return "null";
      if (Array.isArray(d)) return "array";
      return typeof d;
    },
    entries: function () {
      var d = this.data;
      if (Array.isArray(d))
        return d.map(function (v, i) {
          return [String(i), v];
        });
      if (d && typeof d === "object")
        return Object.keys(d).map(function (k) {
          return [k, d[k]];
        });
      return [];
    },
  },
  methods: {
    fmtPrim: function (v) {
      return typeof v === "string" ? '"' + v + '"' : String(v);
    },
  },
  template:
    '<div class="json">' +
    "  <template v-if=\"kind==='object'||kind==='array'\">" +
    '    <div class="json-row"><span class="json-toggle" @click="open=!open">{{ open ? \'−\' : \'+\' }}</span>' +
    '      <span class="json-key" v-if="name">{{ name }}</span>' +
    "      <span class=\"dim\">{{ kind==='array' ? '[' : '{' }}{{ entries.length }}{{ kind==='array' ? ']' : '}' }}</span></div>" +
    '    <div class="json-children" v-show="open">' +
    '      <json-tree v-for="e in entries" :key="e[0]" :data="e[1]" :name="e[0]" :depth="depth+1"></json-tree>' +
    "    </div>" +
    "  </template>" +
    '  <div class="json-row" v-else>' +
    '    <span class="json-toggle"></span>' +
    '    <span class="json-key" v-if="name">{{ name }}:</span>' +
    "    <span :class=\"'json-'+(kind==='number'?'num':kind==='boolean'?'bool':kind==='string'?'str':'null')\">{{ fmtPrim(data) }}</span>" +
    "  </div>" +
    "</div>",
};

export { JsonTree };
