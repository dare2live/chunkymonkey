/**
 * Stock summary helper for the research overview.
 *
 * Exported as window.StockSummaryWidget = {
 *   collectStockSummary,
 *   mergeStockSummary,
 * }.
 */
(function (global) {
  'use strict';

  function topCountEntries(counts, limit) {
    var map = counts || {};
    var keys = Object.keys(map).sort(function (left, right) {
      var diff = (map[right] || 0) - (map[left] || 0);
      return diff || String(left).localeCompare(String(right), 'zh-CN');
    });
    var cap = Number(limit) > 0 ? Number(limit) : keys.length;
    return keys.slice(0, cap).map(function (key) {
      return { name: key, count: map[key] };
    });
  }

  function collectStockSummary(stocks, deps) {
    deps = deps || {};
    var stockGateInfo = typeof deps.stockGateInfo === 'function' ? deps.stockGateInfo : function () { return {}; };
    var stockSourceName = typeof deps.stockSourceName === 'function' ? deps.stockSourceName : function () { return ''; };
    var rows = Array.isArray(stocks) ? stocks : [];
    var summary = {
      total: rows.length,
      abTotal: 0,
      followTotal: 0,
      dualConfirm: 0,
      setupTotal: 0,
      pools: {},
      gates: { follow: 0, watch: 0, observe: 0, avoid: 0 },
      signals: {},
      industries: {},
      sources: {},
      attentionCovered: 0,
      attentionBoosted: 0,
      attentionCrowded: 0,
      attentionSignals: {},
      turtleCovered: 0,
      turtleBreakout: 0,
      turtleWatch: 0,
      turtleExit: 0,
      topIndustries: [],
      topSignals: [],
      topSources: [],
      topAttentionSignals: []
    };

    for (var i = 0; i < rows.length; i += 1) {
      var s = rows[i] || {};
      var pool = s.priority_pool || '未分池';
      var gate = (stockGateInfo(s) || {}).key || '';
      var source = stockSourceName(s) || '';
      var industry = s.setup_industry_name || s.tdx_l2 || s.tdx_l1 || '';
      summary.pools[pool] = (summary.pools[pool] || 0) + 1;
      if (pool === 'A池' || pool === 'B池') summary.abTotal += 1;
      if (gate && summary.gates[gate] != null) summary.gates[gate] += 1;
      if (gate === 'follow') summary.followTotal += 1;
      if (s.setup_tag) {
        summary.setupTotal += 1;
        var signalKey = 'A' + (s.setup_priority != null ? s.setup_priority : '?');
        summary.signals[signalKey] = (summary.signals[signalKey] || 0) + 1;
      }
      if (industry) summary.industries[industry] = (summary.industries[industry] || 0) + 1;
      if (source && source !== '-') summary.sources[source] = (summary.sources[source] || 0) + 1;
      if (s._dual_confirm) summary.dualConfirm += 1;
    }

    summary.topIndustries = topCountEntries(summary.industries, 4);
    summary.topSignals = topCountEntries(summary.signals, 4);
    summary.topSources = topCountEntries(summary.sources, 3);
    return summary;
  }

  function mergeStockSummary(stocks, stockSummary, deps) {
    var local = collectStockSummary(stocks || [], deps);
    if (!stockSummary || typeof stockSummary !== 'object') return local;
    return Object.assign({}, local, stockSummary, {
      pools: Object.assign({}, local.pools, stockSummary.pools || {}),
      gates: Object.assign({}, local.gates, stockSummary.gates || {}),
      signals: Object.assign({}, local.signals, stockSummary.signals || {}),
      industries: Object.assign({}, local.industries, stockSummary.industries || {}),
      sources: Object.assign({}, local.sources, stockSummary.sources || {}),
      attentionSignals: Object.assign({}, local.attentionSignals, stockSummary.attentionSignals || {}),
      topIndustries: stockSummary.topIndustries || local.topIndustries,
      topSignals: stockSummary.topSignals || local.topSignals,
      topSources: stockSummary.topSources || local.topSources,
      topAttentionSignals: stockSummary.topAttentionSignals || local.topAttentionSignals,
    });
  }

  global.StockSummaryWidget = {
    collectStockSummary: collectStockSummary,
    mergeStockSummary: mergeStockSummary,
  };
})(typeof window !== 'undefined' ? window : globalThis);
