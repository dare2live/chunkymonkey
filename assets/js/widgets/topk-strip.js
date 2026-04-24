/* ============================================================
   topk-strip.js — 每日 multidim_v1 AI Top K 横向条带
   数据源: /api/rec/daily-topk?limit=N
   API: window.TopKStripWidget.mount(containerId, { limit, onPick })
   ============================================================ */
(function (global) {
  'use strict';

  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtScore(v) {
    if (v == null || isNaN(v)) return '—';
    return (Number(v) * 100).toFixed(1);
  }

  function gradeClass(pct) {
    if (pct == null) return '';
    if (pct >= 0.95) return 'topk-chip--gold';
    if (pct >= 0.85) return 'topk-chip--silver';
    return 'topk-chip--default';
  }

  async function api(path) {
    var r = await fetch(path);
    if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
    return r.json();
  }

  function renderHeader(model, meta, snapshot, count) {
    var grade = meta && meta.holdout_ic != null
      ? 'IC ' + (meta.holdout_ic * 100).toFixed(2) + '% · RankIC ' + ((meta.holdout_rank_ic || 0) * 100).toFixed(2) + '% · L-S ' + ((meta.holdout_long_short_spread || 0) * 100).toFixed(2) + '%'
      : '模型元数据未到';
    return '<div class="topk-strip-head">' +
      '<div class="topk-strip-title">多维量化评分 · 今日 Top ' + count + '</div>' +
      '<div class="topk-strip-meta">' +
        '<span class="topk-pill">' + esc(snapshot || '-') + '</span>' +
        '<span class="topk-pill topk-pill--muted">' + esc(model || '-') + '</span>' +
        '<span class="topk-pill topk-pill--muted">' + esc(grade) + '</span>' +
      '</div>' +
      '<div class="topk-strip-caveat">研究辅助池 · 非交易建议 · 模型 IC 偏弱 (~0.02), 需结合其它维度验证</div>' +
      '</div>';
  }

  function renderChip(item) {
    var pct = item.percentile;
    var name = item.stock_name || item.stock_code;
    var sector = item.l2 || item.l1 || '';
    return '<button class="topk-chip ' + gradeClass(pct) + '" ' +
      'data-topk-pick="' + esc(item.stock_code) + '" ' +
      'data-topk-name="' + esc(name) + '" ' +
      'title="score ' + fmtScore(item.pred_score) + ' · percentile ' + (pct != null ? (pct * 100).toFixed(1) : '-') + '%">' +
      '<span class="topk-chip-rank">#' + item.rank + '</span>' +
      '<span class="topk-chip-code">' + esc(item.stock_code) + '</span>' +
      '<span class="topk-chip-name">' + esc(name) + '</span>' +
      (sector ? '<span class="topk-chip-sector">' + esc(sector) + '</span>' : '') +
      '<span class="topk-chip-score">' + fmtScore(item.pred_score) + '</span>' +
      '</button>';
  }

  function renderEmpty(message) {
    return '<div class="topk-strip-empty">' + esc(message) + '</div>';
  }

  async function mount(containerId, opts) {
    opts = opts || {};
    var limit = opts.limit || 20;
    var container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<div class="topk-strip-loading muted">加载 AI Top K 中...</div>';

    try {
      var r = await api('/api/rec/daily-topk?limit=' + limit);
      if (!r.ok || !r.items || !r.items.length) {
        container.innerHTML = renderEmpty(r.message || '暂无推荐, 请先跑 run_daily_topk 脚本');
        return;
      }
      var chips = r.items.map(renderChip).join('');
      container.innerHTML =
        '<div class="topk-strip">' +
          renderHeader(r.model_id, r.model_meta, r.snapshot_date, r.items.length) +
          '<div class="topk-strip-scroll">' + chips + '</div>' +
        '</div>';

      container.querySelectorAll('[data-topk-pick]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var code = btn.getAttribute('data-topk-pick');
          var name = btn.getAttribute('data-topk-name');
          if (typeof opts.onPick === 'function') {
            opts.onPick(code, name);
          } else if (global.StockView && typeof global.StockView.openDrawer === 'function') {
            global.StockView.openDrawer(code);
          }
        });
      });
    } catch (e) {
      container.innerHTML = renderEmpty('加载失败: ' + (e.message || e));
    }
  }

  global.TopKStripWidget = { mount: mount };
})(typeof window !== 'undefined' ? window : this);
