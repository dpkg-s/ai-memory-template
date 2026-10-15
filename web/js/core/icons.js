/* ======================================================================
 * 2. 图标（内联 SVG，零依赖）
 * ==================================================================== */
var P =
  'stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
var ICONS = {
  grid:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>',
  terminal:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>',
  book:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 5.5V20.5"/></svg>',
  sliders:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
  clock:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  check:
    '<svg viewBox="0 0 24 24" ' + P + '><path d="M4 12.5l5 5L20 6.5"/></svg>',
  alert:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M12 3l9 16H3z"/><path d="M12 9v4M12 16.5v.5"/></svg>',
  layers:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/></svg>',
  hash:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M9 3L7 21M17 3l-2 18M3.5 9h17M3 15h17"/></svg>',
  search:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/></svg>',
  play:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M7 4.5l12 7.5-12 7.5z"/></svg>',
  chevron:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M9 5l7 7-7 7"/></svg>',
  refresh:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>',
  sun:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/></svg>',
  moon:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/></svg>',
  auto:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 12a9 9 0 0 0 6.4 8.5"/></svg>',
  menu:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  copy:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 5.5A2.5 2.5 0 0 0 12.5 3H6.5A3.5 3.5 0 0 0 3 6.5v6A2.5 2.5 0 0 0 5.5 15"/></svg>',
  link:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
  save:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M5 3h11l3 3v15H5z"/><path d="M8 3v6h7V3M8 21v-6h8v6"/></svg>',
  shield:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z"/></svg>',
  inbox:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1 2h6l1-2h5"/></svg>',
  bolt:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M13 2L5 14h6l-1 8 8-12h-6z"/></svg>',
  file:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>',
  key:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M17 6l2 2M14 9l2 2"/></svg>',
  close:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M6 6l12 12M18 6L6 18"/></svg>',
  trash:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>',
  info:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.5"/></svg>',
  download:
    '<svg viewBox="0 0 24 24" ' +
    P +
    '><path d="M12 3v11"/><path d="M8 10l4 4 4-4"/><path d="M4 19h16"/></svg>',
};
function svg(name) {
  return ICONS[name] || ICONS.info;
}

export { P, ICONS, svg };
