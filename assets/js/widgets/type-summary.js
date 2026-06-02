/**
 * Type summary helper for enabled/non-blacklisted institution rows.
 *
 * Exported as window.TypeSummaryWidget = { collectEnabledTypeSummary }.
 */
(function (global) {
  'use strict';

  function collectEnabledTypeSummary(items, maxVisible) {
    var rows = Array.isArray(items) ? items : [];
    var counts = {};
    var active = 0;
    for (var i = 0; i < rows.length; i += 1) {
      var row = rows[i] || {};
      if (!row.enabled || row.blacklisted || !row.type) continue;
      active += 1;
      counts[row.type] = (counts[row.type] || 0) + 1;
    }
    var orderedTypes = Object.keys(counts).sort(function (left, right) {
      var diff = (counts[right] || 0) - (counts[left] || 0);
      return diff || String(left).localeCompare(String(right), 'zh-CN');
    });
    var visibleLimit = Number(maxVisible) > 0 ? Number(maxVisible) : 4;
    var visible = orderedTypes.slice(0, visibleLimit).map(function (type) {
      return type + counts[type];
    });
    var label = orderedTypes.length <= visibleLimit
      ? visible.join(' · ')
      : visible.join(' · ') + ' · +' + (orderedTypes.length - visibleLimit) + '类';
    var title = orderedTypes.map(function (type) {
      return type + ' ' + counts[type];
    }).join('\n');
    return {
      counts: counts,
      active: active,
      total: rows.length,
      orderedTypes: orderedTypes,
      label: label,
      title: title,
    };
  }

  global.TypeSummaryWidget = {
    collectEnabledTypeSummary: collectEnabledTypeSummary,
  };
})(typeof window !== 'undefined' ? window : globalThis);
