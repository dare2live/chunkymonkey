/* ============================================================
   etf-sector-rotation.js — ETF 板块轮动雷达 widget
   数据源: /api/etf/sector-rotation?limit=N
   API: window.ETFSectorRotationWidget.mount(containerId, { limit, onPickETF })

   展示:
     - 标题: 板块轮动 Top N (基于 ETF 自身动量 · snapshot_date)
     - 每行: rank · sector · score bar · ret20d · rel4w · 龙头 ETF (可点)
   ============================================================ */
(function (global) {
  'use strict';

  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function toneClass(label) {
    if (label === 'leader') return 'esr-row--leader';
    if (label === 'laggard') return 'esr-row--laggard';
    return 'esr-row--observer';
  }

  async function api(path) {
    var r = await fetch(path);
    if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
    return r.json();
  }

  function renderRow(it, maxScore) {
    var score = it.rotation_score || 0;
    var widthPct = maxScore > 0 ? Math.max(3, (score / maxScore) * 100) : 3;
    var leadName = it.leading_etf_name || it.leading_etf_code || '—';
    var leadCode = it.leading_etf_code || '';
    return '<div class="esr-row ' + toneClass(it.rotation_label) + '">' +
      '<span class="esr-rank">#' + (it.rotation_rank || '-') + '</span>' +
      '<span class="esr-sector">' + esc(it.sector || '-') + '</span>' +
      '<span class="esr-count">' + (it.etf_count || 0) + ' 只</span>' +
      '<div class="esr-bar-wrap">' +
        '<div class="esr-bar" style="width:' + widthPct.toFixed(1) + '%"></div>' +
        '<span class="esr-bar-value">' + score.toFixed(1) + '</span>' +
      '</div>' +
      '<span class="esr-metric">' + formatUtils.formatPercent(it.avg_ret_20d, 1, false, true) + '</span>' +
      '<span class="esr-metric esr-metric--muted">rel ' + formatUtils.formatPercent(it.rel_strength_4w, 1, false, true) + '</span>' +
      (leadCode
        ? '<button class="esr-leader" data-etf-pick="' + esc(leadCode) + '" title="深度分析 ' + esc(leadCode) + '">龙头 ' + esc(leadName) + '</button>'
        : '<span class="esr-leader esr-leader--none">无数据</span>') +
      '</div>';
  }

  async function mount(containerId, opts) {
    opts = opts || {};
    var limit = opts.limit || 20;
    var container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<div class="esr-loading muted">加载板块轮动...</div>';

    try {
      var r = await api('/api/etf/sector-rotation?limit=' + limit);
      if (r.status !== 'ok' || !r.items || !r.items.length) {
        container.innerHTML = '<div class="esr-empty">' + esc(r.message || '暂无板块轮动数据') + '</div>';
        return;
      }
      var maxScore = Math.max.apply(null, r.items.map(function (x) { return x.rotation_score || 0; }));
      var rows = r.items.map(function (it) { return renderRow(it, maxScore); }).join('');
      container.innerHTML =
        '<div class="esr-panel">' +
          '<div class="esr-head">' +
            '<div class="esr-title">ETF 板块轮动 · Top ' + r.items.length + '</div>' +
            '<div class="esr-meta">快照 ' + esc(r.snapshot_date) + ' · 基于 ETF 自身动量 + 资金 + 相对强度</div>' +
          '</div>' +
          '<div class="esr-legend">' +
            '<span class="esr-legend-chip esr-legend-chip--leader">leader 领涨</span>' +
            '<span class="esr-legend-chip esr-legend-chip--observer">observer 观察</span>' +
            '<span class="esr-legend-chip esr-legend-chip--laggard">laggard 滞后</span>' +
          '</div>' +
          '<div class="esr-list">' + rows + '</div>' +
        '</div>';
      container.querySelectorAll('[data-etf-pick]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var code = btn.getAttribute('data-etf-pick');
          if (typeof opts.onPickETF === 'function') opts.onPickETF(code);
          else if (global.loadEtfDeepAnalysis) global.loadEtfDeepAnalysis(code, 'opportunityDeepPanel');
        });
      });
    } catch (e) {
      container.innerHTML = '<div class="esr-empty">加载失败: ' + esc(e.message || e) + '</div>';
    }
  }

  global.ETFSectorRotationWidget = { mount: mount };
  if (typeof globalThis !== 'undefined') {
    globalThis.ETFSectorRotationWidget = global.ETFSectorRotationWidget;
  }
})(typeof window !== 'undefined' ? window : this);
