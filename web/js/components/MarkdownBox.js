import { mdRender } from "../core/util.js";

var MarkdownBox = {
  props: { src: { default: "" } },
  methods: {
    html: function () {
      return mdRender(this.src);
    },
  },
  template:
    '<div class="md" v-html="html()" @click="$emit(\'wikilink\', $event)"></div>',
};

export { MarkdownBox };
