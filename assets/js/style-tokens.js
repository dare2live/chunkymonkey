/* ============================================================
   style-tokens.js — 从 CSS 变量读取设计令牌，暴露给 JS 使用

   目的: JS 里拼 SVG / inline style 时不再硬编码 hex；
         颜色在 main.css 一处定义，JS 自动同步。

   用法:
     var c = window.CMTokens.color('brand-500');
     var chart = window.CMTokens.chart();        // 8 色图表色板
     var sem = window.CMTokens.semantic();       // { ok, warn, bad, info, ...}

   规则:
     1. 所有 token 以 --cm-* 为源，读取失败时返回 ''，调用方可用 fallback
     2. 页面加载时缓存一次；需要刷新时调 window.CMTokens.refresh()
     3. 新增色值统一走 main.css :root，JS 不新增硬编码
   ============================================================ */
(function (global) {
  'use strict';

  var _cache = null;

  function readVar(name) {
    var v = getComputedStyle(document.documentElement).getPropertyValue('--' + name);
    return (v || '').trim();
  }

  function buildCache() {
    return {
      /* 品牌主色阶 */
      'brand-700': readVar('cm-brand-700'),
      'brand-500': readVar('cm-brand-500'),
      'brand-400': readVar('cm-brand-400'),
      'brand-300': readVar('cm-brand-300'),
      'brand-100': readVar('cm-brand-100'),
      'brand-50':  readVar('cm-brand-50'),
      /* 暖色 */
      'accent-warm':     readVar('cm-accent-warm'),
      'accent-warm-100': readVar('cm-accent-warm-100'),
      'accent-vivid':    readVar('cm-accent-vivid'),
      /* 自然 */
      'leaf-500': readVar('cm-leaf-500'),
      'leaf-300': readVar('cm-leaf-300'),
      'leaf-100': readVar('cm-leaf-100'),
      /* 表面与背景 */
      'surface':      readVar('cm-surface'),
      'surface-warm': readVar('cm-surface-warm'),
      'bg':           readVar('cm-bg'),
      /* 中性 */
      'ink-900': readVar('cm-ink-900'),
      'ink-700': readVar('cm-ink-700'),
      'ink-500': readVar('cm-ink-500'),
      'ink-300': readVar('cm-ink-300'),
      'ink-100': readVar('cm-ink-100'),
      'ink-50':  readVar('cm-ink-50'),
      /* 语义 */
      'ok-500':   readVar('cm-ok-500'),
      'ok-100':   readVar('cm-ok-100'),
      'warn-500': readVar('cm-warn-500'),
      'warn-100': readVar('cm-warn-100'),
      'bad-500':  readVar('cm-bad-500'),
      'bad-100':  readVar('cm-bad-100'),
      'info-500': readVar('cm-info-500'),
      'info-100': readVar('cm-info-100'),
      /* 图表 */
      'chart-1': readVar('cm-chart-1'),
      'chart-2': readVar('cm-chart-2'),
      'chart-3': readVar('cm-chart-3'),
      'chart-4': readVar('cm-chart-4'),
      'chart-5': readVar('cm-chart-5'),
      'chart-6': readVar('cm-chart-6'),
      'chart-7': readVar('cm-chart-7'),
      'chart-8': readVar('cm-chart-8'),
      /* 股票红涨绿跌 (中国习惯, 不走 palette) */
      'stock-up':   readVar('stock-up'),
      'stock-down': readVar('stock-down')
    };
  }

  function tokens() {
    if (_cache === null) _cache = buildCache();
    return _cache;
  }

  /* 公共 API */
  var CMTokens = {
    /** 单色读取: CMTokens.color('brand-500') → '#60569A' */
    color: function (key) {
      return tokens()[key] || '';
    },

    /** 8 色图表色板（序列 1~8） */
    chart: function () {
      var t = tokens();
      return [t['chart-1'], t['chart-2'], t['chart-3'], t['chart-4'],
              t['chart-5'], t['chart-6'], t['chart-7'], t['chart-8']];
    },

    /** 语义色集合: { ok, warn, bad, info, up, down } */
    semantic: function () {
      var t = tokens();
      return {
        ok:   t['ok-500'],   okLight:   t['ok-100'],
        warn: t['warn-500'], warnLight: t['warn-100'],
        bad:  t['bad-500'],  badLight:  t['bad-100'],
        info: t['info-500'], infoLight: t['info-100'],
        up:   t['stock-up'], down:      t['stock-down']
      };
    },

    /** 品牌色集合: { primary, primaryDark, accent, deep } */
    brand: function () {
      var t = tokens();
      return {
        primary:     t['brand-500'],
        primaryDark: t['brand-700'],
        accent:      t['brand-400'],
        warm:        t['accent-warm'],
        vivid:       t['accent-vivid'],
        leaf:        t['leaf-500']
      };
    },

    /** 所有 token 原始 map */
    all: function () {
      return tokens();
    },

    /** 主题切换 / CSS 重新注入后手动刷新 */
    refresh: function () {
      _cache = null;
      return tokens();
    }
  };

  global.CMTokens = CMTokens;
})(typeof window !== 'undefined' ? window : this);
