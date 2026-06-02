/**
 * Stock list controls helper for the research overview.
 *
 * Exported as window.StockListControlsWidget = {
 *   sortStockRows,
 *   buildStockFilterBar,
 *   buildStockFilterMetaByCode,
 *   applyStockFilters,
 * }.
 */
(function (global) {
  'use strict';

  function sortStockRows(stocks, sortMode) {
    var rows = Array.isArray(stocks) ? stocks : [];
    var mode = sortMode || 'composite';
    return rows.slice().sort(function (left, right) {
      var diff = 0;
      if (mode === 'notice') {
        diff = String(right.latest_notice_date || '').localeCompare(String(left.latest_notice_date || ''));
      } else {
        diff = Number(right.composite_priority_score || -1) - Number(left.composite_priority_score || -1);
      }
      if (!diff) diff = Number(right.discovery_score || -1) - Number(left.discovery_score || -1);
      if (!diff) diff = String(left.stock_code || '').localeCompare(String(right.stock_code || ''), 'zh-CN');
      return diff;
    });
  }

  function collectTdxL1Counts(stocks) {
    var counts = {};
    (Array.isArray(stocks) ? stocks : []).forEach(function (s) {
      var code = String(s && s.tdx_l1 || '').trim();
      if (code) counts[code] = (counts[code] || 0) + 1;
    });
    return counts;
  }

  function buildStockFilterBar(options) {
    options = options || {};
    var stocks = Array.isArray(options.stocks) ? options.stocks : [];
    var activeGate = options.activeGate || 'all';
    var activeIndustry = options.activeIndustry || 'all';
    var activeSortMode = options.activeSortMode || 'composite';
    var tdxL1Names = options.tdxL1Names || {};
    var gates = [
      { key: 'all', label: '全部' },
      { key: 'follow', label: '可跟' },
      { key: 'watch', label: '关注' },
      { key: 'observe', label: '观察' },
      { key: 'avoid', label: '回避' }
    ];
    var sorts = [
      { key: 'composite', label: '综合优先' },
      { key: 'notice', label: '公告最近' },
    ];
    var tdxL1Counts = collectTdxL1Counts(stocks);
    var industries = [{ key: 'all', label: '全部' }].concat(
      Object.keys(tdxL1Counts).sort().map(function (code) {
        return { key: code, label: (tdxL1Names[code] || code) + ' ' + tdxL1Counts[code] };
      })
    );

    function chip(group, key, label, active) {
      return '<span class="type-tag stock-filter-chip' + (active ? ' active' : '') + '" data-filter-group="' + group + '" data-filter-key="' + key + '">' + label + '</span>';
    }

    return '<div class="stock-filter-bar">' +
      '<div class="stock-filter-group"><span class="stock-filter-label-pill">执行</span>' + gates.map(function (f) { return chip('gate', f.key, f.label, f.key === activeGate); }).join('') + '</div>' +
      '<div class="stock-filter-group"><span class="stock-filter-label-pill">行业</span>' + industries.map(function (f) { return chip('industry', f.key, f.label, f.key === activeIndustry); }).join('') + '</div>' +
      '<div class="stock-filter-group"><span class="stock-filter-label-pill stock-filter-label-pill--sort">排序</span>' + sorts.map(function (f) { return chip('sort', f.key, f.label, f.key === activeSortMode); }).join('') + '</div>' +
      '</div>';
  }

  function buildStockFilterMetaByCode(stocks, deps) {
    deps = deps || {};
    var stockGateInfo = typeof deps.stockGateInfo === 'function' ? deps.stockGateInfo : function () { return { key: '' }; };
    var metaByCode = Object.create(null);
    (Array.isArray(stocks) ? stocks : []).forEach(function (s) {
      if (!s || !s.stock_code) return;
      var gate = stockGateInfo(s).key || '';
      metaByCode[s.stock_code] = {
        gate: gate,
        industry: String(s.tdx_l1 || '').trim(),
        search: String(s._search_blob || '').toLowerCase(),
      };
    });
    return metaByCode;
  }

  function applyStockFilters(rows, metaByCode, filterState, keyword) {
    var rowList = rows && typeof rows.forEach === 'function' ? rows : [];
    var state = filterState || {};
    var filterGate = state.gate || 'all';
    var filterIndustry = state.industry || 'all';
    var searchKeyword = String(keyword || '').trim().toLowerCase();
    rowList.forEach(function (tr) {
      var code = tr && tr.dataset ? tr.dataset.stockCode : '';
      var meta = code ? (metaByCode && metaByCode[code]) : null;
      if (!meta) { tr.style.display = 'none'; return; }
      var show = (filterGate === 'all' || meta.gate === filterGate) &&
        (filterIndustry === 'all' || meta.industry === filterIndustry);
      if (show && searchKeyword) show = meta.search.includes(searchKeyword);
      tr.style.display = show ? '' : 'none';
    });
  }

  global.StockListControlsWidget = {
    sortStockRows: sortStockRows,
    buildStockFilterBar: buildStockFilterBar,
    buildStockFilterMetaByCode: buildStockFilterMetaByCode,
    applyStockFilters: applyStockFilters,
  };
})(typeof window !== 'undefined' ? window : globalThis);
