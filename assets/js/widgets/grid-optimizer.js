/* ============================================================
   grid-optimizer.js — ETF 网格自寻优 widget (工作台)
   数据源: /api/etf/grid/optimize?code=XXX&lookback_days=N
   API: window.GridOptimizerWidget.mount(containerId)
   ============================================================ */
(function (global) {
  'use strict';

  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
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

  function renderBestCard(best, buyHold) {
    if (!best) return '<div class="grid-opt-empty">无可行步长 (所有候选未通过硬门槛)</div>';
    var bhRet = buyHold ? (buyHold.return_pct || 0) : 0;
    return '<div class="grid-opt-best">' +
      '<div class="grid-opt-best-head">推荐步长</div>' +
      '<div class="grid-opt-best-grid">' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">步长</span><span class="grid-opt-metric-value">' + fmt(best.step_pct, 1) + '%</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">收益</span><span class="grid-opt-metric-value">' + fmtPct(best.return_pct) + '</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">超额</span><span class="grid-opt-metric-value">' + fmtPct(best.backtest_excess_pct) + '</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">最大回撤</span><span class="grid-opt-metric-value">' + fmtPct(best.max_drawdown_pct) + '</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">夏普</span><span class="grid-opt-metric-value">' + fmt(best.sharpe) + '</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">交易次数</span><span class="grid-opt-metric-value">' + (best.trade_count || 0) + '</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">完整卖出</span><span class="grid-opt-metric-value">' + (best.sell_count || 0) + '</span></div>' +
        '<div class="grid-opt-metric"><span class="grid-opt-metric-label">胜率</span><span class="grid-opt-metric-value">' + (best.win_rate != null ? (best.win_rate * 100).toFixed(1) + '%' : '-') + '</span></div>' +
      '</div>' +
      '<div class="grid-opt-baseline">基准 buy-and-hold 收益: ' + fmtPct(bhRet) + '</div>' +
      '</div>';
  }

  function renderCandidateTable(candidates) {
    if (!candidates || !candidates.length) return '';
    var rows = candidates.slice().sort(function (a, b) { return a.step_pct - b.step_pct; });
    var rowsHtml = rows.map(function (c) {
      var cls = c.hard_gate_passed ? 'grid-opt-row--pass' : 'grid-opt-row--fail';
      return '<tr class="' + cls + '">' +
        '<td>' + fmt(c.step_pct, 1) + '%</td>' +
        '<td class="mono">' + fmtPct(c.return_pct) + '</td>' +
        '<td class="mono">' + fmtPct(c.excess_pct) + '</td>' +
        '<td class="mono">' + fmtPct(c.max_drawdown_pct) + '</td>' +
        '<td class="mono">' + fmt(c.sharpe) + '</td>' +
        '<td>' + (c.trade_count || 0) + ' / ' + (c.sell_count || 0) + '</td>' +
        '<td>' + (c.win_rate != null ? (c.win_rate * 100).toFixed(0) + '%' : '-') + '</td>' +
        '<td>' + (c.candidate_score != null ? c.candidate_score.toFixed(1) : '-') + '</td>' +
        '<td>' + (c.hard_gate_passed ? '通过' : '不通过') + '</td>' +
      '</tr>';
    }).join('');
    return '<div class="grid-opt-table-wrap"><table class="data-table data-table-compact grid-opt-table"><thead><tr>' +
      '<th>步长</th><th>收益</th><th>超额</th><th>回撤</th><th>夏普</th><th>交易/卖出</th><th>胜率</th><th>评分</th><th>门槛</th>' +
      '</tr></thead><tbody>' + rowsHtml + '</tbody></table></div>';
  }

  function renderStepReturnChart(candidates, buyHold) {
    if (!candidates || !candidates.length) return '';
    var w = 640, h = 180, padL = 40, padR = 20, padT = 16, padB = 30;
    var rows = candidates.slice().sort(function (a, b) { return a.step_pct - b.step_pct; });
    var xs = rows.map(function (c) { return c.step_pct; });
    var ys = rows.map(function (c) { return c.return_pct || 0; });
    var bhRet = buyHold ? (buyHold.return_pct || 0) : 0;
    var xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
    var yMin = Math.min.apply(null, ys.concat([bhRet])), yMax = Math.max.apply(null, ys.concat([bhRet]));
    var yPad = (yMax - yMin) * 0.08 || 5;
    yMin -= yPad; yMax += yPad;
    function X(x) { return padL + (x - xMin) / (xMax - xMin || 1) * (w - padL - padR); }
    function Y(y) { return padT + (1 - (y - yMin) / (yMax - yMin || 1)) * (h - padT - padB); }

    var path = rows.map(function (c, i) {
      return (i === 0 ? 'M' : 'L') + X(c.step_pct).toFixed(1) + ' ' + Y(c.return_pct || 0).toFixed(1);
    }).join(' ');
    var dots = rows.map(function (c) {
      var fillVar = c.hard_gate_passed ? 'var(--cm-ok-500)' : 'var(--cm-ink-300)';
      return '<circle cx="' + X(c.step_pct).toFixed(1) + '" cy="' + Y(c.return_pct || 0).toFixed(1) +
        '" r="3" fill="' + fillVar + '"></circle>';
    }).join('');
    var bhLine = '<line x1="' + padL + '" y1="' + Y(bhRet).toFixed(1) + '" x2="' + (w - padR) + '" y2="' + Y(bhRet).toFixed(1) +
      '" stroke="var(--cm-accent-warm)" stroke-dasharray="4 3" stroke-width="1.2"></line>' +
      '<text x="' + (w - padR - 4) + '" y="' + (Y(bhRet) - 4).toFixed(1) +
      '" text-anchor="end" font-size="10" fill="var(--cm-accent-warm)">持有基准 ' + fmtPct(bhRet, 1) + '</text>';

    // axis labels
    var xTicks = [xMin, (xMin + xMax) / 2, xMax].map(function (t) {
      return '<text x="' + X(t).toFixed(1) + '" y="' + (h - padB + 16) +
        '" text-anchor="middle" font-size="10" fill="var(--cm-ink-500)">' + t.toFixed(1) + '%</text>';
    }).join('');
    var yTicks = [yMin, (yMin + yMax) / 2, yMax].map(function (t) {
      return '<text x="' + (padL - 4) + '" y="' + (Y(t) + 3).toFixed(1) +
        '" text-anchor="end" font-size="10" fill="var(--cm-ink-500)">' + fmtPct(t, 0) + '</text>';
    }).join('');

    return '<svg viewBox="0 0 ' + w + ' ' + h + '" class="grid-opt-chart">' +
      '<rect x="' + padL + '" y="' + padT + '" width="' + (w - padL - padR) + '" height="' + (h - padT - padB) +
      '" fill="var(--cm-surface)" stroke="var(--cm-ink-100)"></rect>' +
      bhLine +
      '<path d="' + path + '" fill="none" stroke="var(--cm-brand-500)" stroke-width="1.8"></path>' +
      dots +
      xTicks + yTicks +
      '<text x="' + (padL) + '" y="12" font-size="11" font-weight="700" fill="var(--cm-brand-700)">Step → Return 曲线 (绿点=通过硬门槛, 灰点=未通过)</text>' +
      '</svg>';
  }

  function renderForm(defaults) {
    defaults = defaults || {};
    return '<div class="grid-opt-form">' +
      '<label><span>ETF 代码</span>' +
        '<input type="text" class="grid-opt-input" id="grid-opt-code" value="' + esc(defaults.code || '') + '" placeholder="如 515050" maxlength="6"></label>' +
      '<label><span>回测窗口 (交易日)</span>' +
        '<input type="number" class="grid-opt-input" id="grid-opt-lookback" value="' + (defaults.lookback || 240) + '" min="60" max="1200" step="20"></label>' +
      '<button class="chip chip-primary" id="grid-opt-submit">开始寻优</button>' +
      '<span class="grid-opt-note" id="grid-opt-note">参数空间 0.5%~4.5% 每 0.1% 扫描</span>' +
      '</div>';
  }

  function mount(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = renderForm();

    var noteEl = container.querySelector('#grid-opt-note');
    var resultContainerId = containerId + '-result';
    container.insertAdjacentHTML('beforeend',
      '<div id="' + resultContainerId + '" class="grid-opt-result"></div>'
    );

    async function run() {
      var code = (container.querySelector('#grid-opt-code').value || '').trim();
      var lookback = Number(container.querySelector('#grid-opt-lookback').value || 240);
      var resBox = document.getElementById(resultContainerId);
      if (!/^\d{6}$/.test(code)) {
        noteEl.textContent = 'ETF 代码需要 6 位数字';
        noteEl.style.color = 'var(--cm-bad-500)';
        return;
      }
      noteEl.textContent = '寻优中... (约 5-10 秒)';
      noteEl.style.color = 'var(--cm-brand-500)';
      resBox.innerHTML = '';
      try {
        var r = await api('/api/etf/grid/optimize?code=' + encodeURIComponent(code) + '&lookback_days=' + lookback);
        if (r.status === 'unsupported') {
          resBox.innerHTML = '<div class="grid-opt-empty">' + esc(r.message || '不支持网格') + '</div>';
          noteEl.textContent = '完成';
          noteEl.style.color = 'var(--cm-ink-500)';
          return;
        }
        if (r.status !== 'ok') {
          resBox.innerHTML = '<div class="grid-opt-empty">' + esc(r.message || '计算失败') + '</div>';
          noteEl.textContent = '失败';
          noteEl.style.color = 'var(--cm-bad-500)';
          return;
        }
        var info = r.info || {};
        var header =
          '<div class="grid-opt-header">' +
            '<div class="grid-opt-etf-title">' + esc(info.name || info.code || '-') +
              ' <span class="grid-opt-etf-code">' + esc(info.code || '') + '</span>' +
              (info.category ? '<span class="grid-opt-etf-cat">' + esc(info.category) + '</span>' : '') +
            '</div>' +
            '<div class="grid-opt-etf-meta">窗口 ' + esc(r.price_window.from) + ' ~ ' + esc(r.price_window.to) +
              ' · 可行 ' + r.feasible_count + ' / ' + r.total_count + '</div>' +
          '</div>';
        resBox.innerHTML =
          header +
          renderBestCard(r.best, r.buy_hold) +
          renderStepReturnChart(r.candidates, r.buy_hold) +
          renderCandidateTable(r.candidates);
        noteEl.textContent = '完成 ' + r.total_count + ' 个候选, 可行 ' + r.feasible_count + ' 个';
        noteEl.style.color = 'var(--cm-ok-500)';
      } catch (e) {
        resBox.innerHTML = '<div class="grid-opt-empty">请求失败: ' + esc(e.message || e) + '</div>';
        noteEl.textContent = '请求异常';
        noteEl.style.color = 'var(--cm-bad-500)';
      }
    }

    container.querySelector('#grid-opt-submit').addEventListener('click', run);
    container.querySelector('#grid-opt-code').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') run();
    });
  }

  global.GridOptimizerWidget = { mount: mount };
})(typeof window !== 'undefined' ? window : this);
