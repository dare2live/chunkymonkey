/* ============================================================
   multidim-badge.js — 股票详情顶部多维量化评分徽章
   数据源: /api/rec/stock-prediction?code=xxx
   API: window.MultidimBadgeWidget.mount(containerId, { stockCode })
   ============================================================ */
(function (global) {
  'use strict';

  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function tone(percentile) {
    if (percentile == null) return 'neutral';
    if (percentile >= 0.9) return 'gold';
    if (percentile >= 0.7) return 'good';
    if (percentile >= 0.4) return 'neutral';
    return 'weak';
  }

  async function api(path) {
    var r = await fetch(path);
    if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
    return r.json();
  }

  function render(payload) {
    if (!payload || !payload.ok) {
      return '<div class="multidim-badge multidim-badge--empty">多维分不可用</div>';
    }
    if (!payload.has_prediction) {
      return '<div class="multidim-badge multidim-badge--empty" title="模型 ' + esc(payload.model_id || '-') + ' 不覆盖此股">多维分无覆盖</div>';
    }
    var pct = payload.percentile;
    var score = payload.pred_score;
    var t = tone(pct);
    return '<div class="multidim-badge multidim-badge--' + t + '" ' +
      'title="模型 ' + esc(payload.model_id || '-') + ' · 日期 ' + esc(payload.date || '-') +
      ' · IC ' + formatUtils.formatPercent(payload.model_ic, 2, true, false, '-') +
      ' · 模型 IC ~0.02 属研究辅助, 请结合其它维度">' +
      '<span class="multidim-badge-label">多维分</span>' +
      '<span class="multidim-badge-score">' + formatUtils.formatNumber(score != null ? score * 100 : null, 1, '-') + '</span>' +
      '<span class="multidim-badge-pct">Top ' + formatUtils.formatPercent(pct != null ? 1 - pct : null, 1, true, false, '-') + '</span>' +
      (payload.rank_in_date ? '<span class="multidim-badge-rank">#' + payload.rank_in_date + '</span>' : '') +
      '</div>';
  }

  async function mount(containerId, opts) {
    opts = opts || {};
    var container = document.getElementById(containerId);
    if (!container) return;
    var code = opts.stockCode;
    if (!code) { container.innerHTML = ''; return; }
    container.innerHTML = '<div class="multidim-badge multidim-badge--loading">...</div>';
    try {
      var r = await api('/api/rec/stock-prediction?code=' + encodeURIComponent(code));
      container.innerHTML = render(r);
    } catch (e) {
      container.innerHTML = '<div class="multidim-badge multidim-badge--empty">加载失败</div>';
    }
  }

  global.MultidimBadgeWidget = { mount: mount };
  if (typeof globalThis !== 'undefined') {
    globalThis.MultidimBadgeWidget = global.MultidimBadgeWidget;
  }
})(typeof window !== 'undefined' ? window : this);
