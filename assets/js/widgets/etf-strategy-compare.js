/* ============================================================
   etf-strategy-compare.js — ETF grid vs buy-and-hold 对比 widget
   数据源: /api/etf/strategy-comparison/{code}
   API: window.ETFStrategyCompareWidget.mount(containerId, { code })

   UI 结构:
     - 顶部 3 tab: 1Y / 3Y / 5Y
     - 指标对比卡 (grid vs buy_hold)
     - SVG 价格归一化曲线 + grid 终点对照点
   ============================================================ */
(function (global) {
  'use strict';

  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function fmt(v, d) {
    if (v == null || isNaN(v)) return '-';
    return Number(v).toFixed(d == null ? 2 : d);
  }
  function fmtPct(v, d) {
    if (v == null || isNaN(v)) return '-';
    var n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(d == null ? 2 : d) + '%';
  }

  async function api(path) {
    var r = await fetch(path);
    if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
    return r.json();
  }

  function fmtDD(v) {
    // 回撤作为正值绝对量展示 (无 +/-)
    if (v == null || isNaN(v)) return '-';
    return Math.abs(Number(v)).toFixed(2) + '%';
  }
  function fmtWinRate(v) {
    // 后端 win_rate 可能是比例 (0-1) 或百分比 (0-100), 兼容两种
    if (v == null || isNaN(v)) return '-';
    var n = Number(v);
    if (n <= 1) n = n * 100;
    return n.toFixed(0) + '%';
  }

  function renderMetricCard(label, strat, tone) {
    if (!strat) return '<div class="esc-metric-card esc-metric-card--' + tone + '"><div class="esc-metric-head">' + label + '</div><div class="muted">无数据</div></div>';
    return '<div class="esc-metric-card esc-metric-card--' + tone + '">' +
      '<div class="esc-metric-head">' + label +
        (strat.best_step_pct != null ? ' · 步长 ' + fmt(strat.best_step_pct, 1) + '%' : '') +
      '</div>' +
      '<div class="esc-metric-grid">' +
        '<div><span class="esc-k">总收益</span><span class="esc-v">' + fmtPct(strat.return_pct) + '</span></div>' +
        '<div><span class="esc-k">年化</span><span class="esc-v">' + fmtPct(strat.annualized_return_pct) + '</span></div>' +
        '<div><span class="esc-k">最大回撤</span><span class="esc-v">' + fmtDD(strat.max_drawdown_pct) + '</span></div>' +
        '<div><span class="esc-k">夏普</span><span class="esc-v">' + fmt(strat.sharpe) + '</span></div>' +
        (strat.trade_count != null
          ? '<div><span class="esc-k">交易次数</span><span class="esc-v">' + strat.trade_count + '</span></div>'
          : '') +
        (strat.win_rate != null
          ? '<div><span class="esc-k">胜率</span><span class="esc-v">' + fmtWinRate(strat.win_rate) + '</span></div>'
          : '') +
      '</div>' +
      '</div>';
  }

  function renderCurve(priceSeries, lookbackDays, gridReturn, bhReturn) {
    var series = priceSeries.slice(-lookbackDays);
    if (!series.length) return '<div class="esc-empty">无价格数据</div>';
    var w = 640, h = 220, padL = 48, padR = 40, padT = 20, padB = 28;
    var initial = series[0].close;
    var points = series.map(function (p, i) {
      var pct = initial > 0 ? (p.close - initial) / initial * 100 : 0;
      return { i: i, date: p.date, pct: pct };
    });
    var pcts = points.map(function (p) { return p.pct; }).concat([gridReturn || 0]);
    var yMin = Math.min.apply(null, pcts);
    var yMax = Math.max.apply(null, pcts);
    var yPad = (yMax - yMin) * 0.08 || 5;
    yMin -= yPad; yMax += yPad;
    function X(i) { return padL + i / (points.length - 1 || 1) * (w - padL - padR); }
    function Y(v) { return padT + (1 - (v - yMin) / (yMax - yMin || 1)) * (h - padT - padB); }

    var pathD = points.map(function (p, i) {
      return (i === 0 ? 'M' : 'L') + X(i).toFixed(1) + ' ' + Y(p.pct).toFixed(1);
    }).join(' ');

    var zeroLine = '<line x1="' + padL + '" x2="' + (w - padR) + '" y1="' + Y(0).toFixed(1) +
      '" y2="' + Y(0).toFixed(1) + '" stroke="var(--cm-ink-100)" stroke-dasharray="3 3"/>';

    // Grid terminal line (水平虚线标记最终网格收益率)
    var gridLine = '';
    if (gridReturn != null) {
      gridLine = '<line x1="' + padL + '" x2="' + (w - padR) + '" y1="' + Y(gridReturn).toFixed(1) +
        '" y2="' + Y(gridReturn).toFixed(1) + '" stroke="var(--cm-ok-500)" stroke-dasharray="4 3" stroke-width="1.4"/>' +
        '<text x="' + (w - padR - 4) + '" y="' + (Y(gridReturn) - 5).toFixed(1) +
        '" text-anchor="end" font-size="10" fill="var(--cm-ok-500)" font-weight="700">网格 ' + fmtPct(gridReturn, 1) + '</text>';
    }

    var bhEnd = points.length ? points[points.length - 1].pct : 0;
    var bhEndMark = '<circle cx="' + X(points.length - 1).toFixed(1) + '" cy="' + Y(bhEnd).toFixed(1) +
      '" r="4" fill="var(--cm-accent-warm)" stroke="var(--cm-surface)" stroke-width="2"></circle>' +
      '<text x="' + (X(points.length - 1) - 6).toFixed(1) + '" y="' + (Y(bhEnd) - 8).toFixed(1) +
      '" text-anchor="end" font-size="10" fill="var(--cm-accent-warm)" font-weight="700">持有 ' + fmtPct(bhEnd, 1) + '</text>';

    var bhArea = '<path d="' + pathD +
      ' L ' + X(points.length - 1).toFixed(1) + ' ' + Y(yMin).toFixed(1) +
      ' L ' + X(0).toFixed(1) + ' ' + Y(yMin).toFixed(1) + ' Z' +
      '" fill="var(--cm-accent-warm-100)" opacity="0.35"></path>';

    var yTicks = [yMin, (yMin + yMax) / 2, yMax].map(function (t) {
      return '<text x="' + (padL - 6) + '" y="' + (Y(t) + 3).toFixed(1) +
        '" text-anchor="end" font-size="10" fill="var(--cm-ink-500)">' + fmtPct(t, 0) + '</text>';
    }).join('');
    var xTicks = [0, Math.floor(points.length / 2), points.length - 1].map(function (i) {
      return '<text x="' + X(i).toFixed(1) + '" y="' + (h - padB + 16) +
        '" text-anchor="middle" font-size="10" fill="var(--cm-ink-500)">' + esc(points[i].date) + '</text>';
    }).join('');

    return '<svg viewBox="0 0 ' + w + ' ' + h + '" class="esc-chart">' +
      '<rect x="' + padL + '" y="' + padT + '" width="' + (w - padL - padR) + '" height="' + (h - padT - padB) +
      '" fill="var(--cm-surface)" stroke="var(--cm-ink-100)"></rect>' +
      zeroLine +
      bhArea +
      '<path d="' + pathD + '" fill="none" stroke="var(--cm-accent-warm)" stroke-width="1.8"></path>' +
      gridLine +
      bhEndMark +
      yTicks + xTicks +
      '<text x="' + padL + '" y="14" font-size="11" font-weight="700" fill="var(--cm-brand-700)">价格曲线 (买入持有) vs 网格终点收益</text>' +
      '</svg>';
  }

  function renderForPeriod(data, periodObj) {
    var strategies = periodObj.strategies || {};
    var grid = strategies.grid;
    var bh = strategies.buy_hold;
    var gridRet = grid ? grid.return_pct : null;
    var bhRet = bh ? bh.return_pct : null;
    var edge = grid ? grid.edge_pct : null;
    var winner = (gridRet != null && bhRet != null)
      ? (gridRet > bhRet ? 'grid' : 'buy_hold')
      : null;

    var header = '<div class="esc-head">' +
      '<div class="esc-period-label">周期 ' + esc(periodObj.period) + ' · 窗口 ' + esc(periodObj.data_from || '-') + ' ~ ' + esc(periodObj.data_to || '-') + '</div>' +
      (edge != null
        ? '<div class="esc-edge esc-edge--' + (edge > 0 ? 'pos' : 'neg') + '">网格 ' + (edge > 0 ? '跑赢' : '跑输') + ' ' + fmtPct(Math.abs(edge), 1) + '</div>'
        : '') +
      '</div>';

    var cards = '<div class="esc-metric-row">' +
      renderMetricCard('网格', grid, winner === 'grid' ? 'win' : (grid ? 'neutral' : 'muted')) +
      renderMetricCard('买入持有', bh, winner === 'buy_hold' ? 'win' : (bh ? 'neutral' : 'muted')) +
      '</div>';

    var chart = renderCurve(data.price_series || [], periodObj.lookback_days || 252, gridRet, bhRet);
    return header + cards + chart;
  }

  async function mount(containerId, opts) {
    opts = opts || {};
    var container = document.getElementById(containerId);
    if (!container) return;
    var code = opts.code;
    if (!code) { container.innerHTML = '<div class="esc-empty">缺少 ETF 代码</div>'; return; }
    container.innerHTML = '<div class="esc-loading muted">加载策略对比...</div>';

    try {
      var r = await api('/api/etf/strategy-comparison/' + encodeURIComponent(code));
      if (r.status !== 'ok') {
        container.innerHTML = '<div class="esc-empty">' + esc(r.message || '数据不可用') + '</div>';
        return;
      }
      var periods = (r.periods || []).sort(function (a, b) { return a.lookback_days - b.lookback_days; });
      if (!periods.length) {
        container.innerHTML = '<div class="esc-empty">尚无回测结果</div>';
        return;
      }

      var tabs = periods.map(function (p, i) {
        return '<button class="esc-tab ' + (i === 0 ? 'esc-tab--active' : '') + '" data-esc-period="' + esc(p.period) + '">' + esc(p.period) + '</button>';
      }).join('');

      container.innerHTML =
        '<div class="esc-panel">' +
          '<div class="esc-panel-head">' +
            '<div class="esc-title">策略对比 · ' + esc(r.info.name || r.info.code) + '</div>' +
            '<div class="esc-meta">快照 ' + esc(r.snapshot_date) + '</div>' +
          '</div>' +
          '<div class="esc-tabs">' + tabs + '</div>' +
          '<div id="' + containerId + '-body" class="esc-body"></div>' +
        '</div>';

      var body = document.getElementById(containerId + '-body');
      function renderPeriod(period) {
        var pObj = periods.find(function (p) { return p.period === period; });
        if (pObj) body.innerHTML = renderForPeriod(r, pObj);
      }
      renderPeriod(periods[0].period);

      container.querySelectorAll('[data-esc-period]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          container.querySelectorAll('[data-esc-period]').forEach(function (b) {
            b.classList.toggle('esc-tab--active', b === btn);
          });
          renderPeriod(btn.getAttribute('data-esc-period'));
        });
      });
    } catch (e) {
      container.innerHTML = '<div class="esc-empty">加载失败: ' + esc(e.message || e) + '</div>';
    }
  }

  global.ETFStrategyCompareWidget = { mount: mount };
})(typeof window !== 'undefined' ? window : this);
