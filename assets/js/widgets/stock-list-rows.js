/**
 * Stock list row renderer for the research overview.
 *
 * Exported as window.StockListRowsWidget = {
 *   buildStockListRowHtml,
 * }.
 */
(function (global) {
  'use strict';

  function buildStockListRowHtml(stock, idx, deps) {
    deps = deps || {};
    var esc = typeof deps.esc === 'function' ? deps.esc : function (value) { return String(value == null ? '' : value); };
    var fmtDate = typeof deps.fmtDate === 'function' ? deps.fmtDate : function (value) { return String(value || ''); };
    var stockCell = typeof deps.stockCell === 'function' ? deps.stockCell : function (code, name) { return esc(code) + ' ' + esc(name || ''); };
    var stockCompositeCell = typeof deps.stockCompositeCell === 'function' ? deps.stockCompositeCell : function (s) { return esc(s && s.composite_priority_score != null ? s.composite_priority_score : '-'); };
    var stockDateSummaryCell = typeof deps.stockDateSummaryCell === 'function' ? deps.stockDateSummaryCell : function (dateText) { return esc(dateText || '-'); };
    var stockHolderCoverageCell = typeof deps.stockHolderCoverageCell === 'function' ? deps.stockHolderCoverageCell : function (s) { return esc(s && (s.holder_total != null ? s.holder_total : (s.inst_count_t0 || 0))); };
    var tdxL1Names = deps.tdxL1Names || {};

    function noticeSourceBadge(source) {
      var map = {
        source_notice: { text: '公告日', cls: 'sig-source-true', title: '真实源公告日' },
        page_update_date: { text: 'F10更新', cls: 'sig-source-page', title: 'TDX/F10 页面更新日，可观测但不等同真实公告日' },
        fetched_at_observed: { text: '抓取可见', cls: 'sig-source-fetched', title: '本地抓取时已可见，保守晚于真实公告日' },
        regulatory_deadline: { text: '监管兜底', cls: 'sig-source-deadline', title: '监管披露期限兜底，不是真实公告日' },
        unknown: { text: '未知来源', cls: 'sig-source-unknown', title: '公告日来源未标记' },
      };
      var meta = map[source || 'unknown'] || map.unknown;
      return '<span class="sig-source-badge sig-source-badge-compact ' + meta.cls + '" title="' + esc(meta.title) + '">' + esc(meta.text) + '</span>';
    }

    function signalV2Cell(s) {
      var ev = s && s._sig_v2;
      if (!ev) return '<span class="muted" style="font-size:11px">无当期信号</span>';
      var action = ev.action || 'skip';
      var badgeMap = {
        follow: { text: '可跟', bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' },
        watch:  { text: '观察', bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' },
        skip:   { text: '不跟', bg: 'var(--cm-ink-50)', fg: 'var(--cm-ink-500)' },
      };
      var bd = badgeMap[action] || badgeMap.skip;
      var badge = '<span style="background:' + bd.bg + ';color:' + bd.fg + ';font-size:10px;font-weight:600;padding:2px 8px;border-radius:3px">' + bd.text + '</span>';
      var evLong = ev.long && ev.long.stats;
      var evLine = '';
      if (evLong && evLong.ev_pct != null) {
        var sign = evLong.ev_pct >= 0 ? '+' : '';
        var color = evLong.ev_pct >= 5 ? 'var(--cm-ok-500)' : evLong.ev_pct < 0 ? 'var(--cm-bad-500)' : 'var(--cm-ink-500)';
        evLine = '<span style="font-size:11px;color:' + color + ';margin-left:6px">' + sign + evLong.ev_pct.toFixed(1) + '% · n=' + (evLong.n || 0) + '</span>';
      }
      return '<div style="line-height:1.4">' + badge + evLine + '<div class="muted" style="font-size:10px">' +
        esc(ev.institution_name || ev.institution_id || '') + ' · ' + fmtDate(ev.notice_date) + ' ' +
        noticeSourceBadge(ev.notice_date_source) + '</div></div>';
    }

    function industryCell(s) {
      var name = tdxL1Names[(s.tdx_l1 || '').trim()] || s.tdx_l2 || s.tdx_l1 || '—';
      var l3Text = s.tdx_l3 ? s.tdx_l3 : (s.tdx_l2 ? 'L3未分类' : '');
      var subText = l3Text || s.tdx_l2 || '';
      var sub = subText ? ('<div class="muted" style="font-size:10px">' + esc(subText) + '</div>') : '';
      return '<div style="line-height:1.4"><div style="font-size:12px">' + esc(name) + '</div>' + sub + '</div>';
    }

    function watchlistButton(s) {
      var inList = !!s._in_watchlist;
      if (inList) return '<span class="muted" style="font-size:11px">已在自选</span>';
      return '<button type="button" class="chip chip-ghost chip-sm stock-watch-btn" data-stock-code="' + esc(s.stock_code) + '" data-stock-name="' + esc(s.stock_name || '') + '">+ 加自选</button>';
    }

    return '<tr data-stock-idx="' + idx + '" data-stock-code="' + esc(stock.stock_code) + '">' +
      '<td>' + stockCell(stock.stock_code, stock.stock_name) + '</td>' +
      '<td>' + signalV2Cell(stock) + '</td>' +
      '<td data-sort-value="' + esc((stock.tdx_l1 || '')) + '">' + industryCell(stock) + '</td>' +
      '<td data-sort-value="' + esc(String(stock.composite_priority_score != null ? stock.composite_priority_score : -1)) + '">' + stockCompositeCell(stock) + '</td>' +
      '<td data-sort-value="' + esc(String(stock.latest_notice_date || '')) + '">' + stockDateSummaryCell(stock.latest_notice_date) + '</td>' +
      '<td data-sort-value="' + esc(String(stock.holder_total != null ? stock.holder_total : (stock.inst_count_t0 || 0))) + '">' + stockHolderCoverageCell(stock) + '</td>' +
      '<td>' + watchlistButton(stock) + '</td>' +
      '</tr>';
  }

  global.StockListRowsWidget = {
    buildStockListRowHtml: buildStockListRowHtml,
  };
})(typeof window !== 'undefined' ? window : globalThis);
