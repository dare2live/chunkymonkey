/* ============================================================
   format-utils.js — widget 数字格式化共享 helper
   负责统一 number / percent 的展示格式，避免各 widget 各写一套
   API: window.WidgetFormatUtils.formatNumber / formatPercent
   ============================================================ */
(function (global) {
  'use strict';

  function toFiniteNumber(value) {
    if (value == null || value === '') return null;
    var num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function formatNumber(value, digits, empty) {
    var num = toFiniteNumber(value);
    if (num == null) return empty == null ? '-' : empty;
    return num.toFixed(digits == null ? 2 : digits);
  }

  function formatPercent(value, digits, isRatio, signed, empty) {
    var num = toFiniteNumber(value);
    if (num == null) return empty == null ? '-' : empty;
    if (isRatio) num *= 100;
    return (signed && num > 0 ? '+' : '') + num.toFixed(digits == null ? 2 : digits) + '%';
  }

  global.WidgetFormatUtils = {
    formatNumber: formatNumber,
    formatPercent: formatPercent,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.WidgetFormatUtils = global.WidgetFormatUtils;
  }
})(typeof window !== 'undefined' ? window : this);
