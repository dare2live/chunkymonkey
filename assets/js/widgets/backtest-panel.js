/* widgets/backtest-panel.js — 历史回测 widget（工作台折叠区）
 *
 * window.BacktestPanelWidget = { mount(containerId) }
 * 依赖 SignalAdapter；手动触发，不自动运行
 */
(function (global) {
  'use strict';

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }
  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');
  function fmtPct(v, digits = 1) {
    const formatted = formatUtils.formatPercent(v, digits, false, true, '-');
    if (formatted === '-') return formatted;
    const n = Number(v);
    const cls = n >= 0 ? 'sig-pos' : 'sig-neg';
    return `<span class="${cls}">${formatted}</span>`;
  }

  function renderPanel(r) {
    const fp = r.follow_policy || {};
    const wp = r.watch_policy || {};
    const sp = r.skip_policy || {};
    const bp = r.blind_buy || {};
    const cov = r.coverage || {};
    const trend = r.quarterly_trend || [];
    const tops = r.top_institutions_in_follow || [];
    const evDiff = (fp.ev_pct || 0) - (bp.ev_pct || 0);
    const wrDiff = ((fp.win_rate || 0) - (bp.win_rate || 0)) * 100;
    return `
      <div class="sig-bt-summary">
        <div class="sig-bt-card sig-bt-follow">
          <div class="sig-bt-lbl">Follow（筛选后）</div>
          <div class="sig-bt-val">${fmtPct(fp.ev_pct)}</div>
          <div class="sig-bt-sub">胜率 ${formatUtils.formatWinRate(fp.win_rate, 0, '-')} · n=${fp.n || 0}（${((cov.follow / (cov.total_events || 1)) * 100).toFixed(1)}%）</div>
        </div>
        <div class="sig-bt-card">
          <div class="sig-bt-lbl">Blind（盲跟对照）</div>
          <div class="sig-bt-val">${fmtPct(bp.ev_pct)}</div>
          <div class="sig-bt-sub">胜率 ${formatUtils.formatWinRate(bp.win_rate, 0, '-')} · n=${bp.n || 0}</div>
        </div>
        <div class="sig-bt-card">
          <div class="sig-bt-lbl">Watch 边缘</div>
          <div class="sig-bt-val">${fmtPct(wp.ev_pct)}</div>
          <div class="sig-bt-sub">胜率 ${formatUtils.formatWinRate(wp.win_rate, 0, '-')} · n=${wp.n || 0}</div>
        </div>
        <div class="sig-bt-card sig-bt-skip">
          <div class="sig-bt-lbl">Skip 被过滤</div>
          <div class="sig-bt-val">${fmtPct(sp.ev_pct)}</div>
          <div class="sig-bt-sub">胜率 ${formatUtils.formatWinRate(sp.win_rate, 0, '-')} · n=${sp.n || 0}</div>
        </div>
      </div>
      <div class="sig-bt-diff">
        <b>Follow vs Blind: EV差 ${fmtPct(evDiff)}，胜率差 ${wrDiff >= 0 ? '+' : ''}${wrDiff.toFixed(1)}pp</b>
        <span class="muted"> · 筛选有效则 Follow 应优于 Blind</span>
      </div>
      <div class="sig-panel-head" style="margin-top:14px"><h4>季度趋势</h4></div>
      <div class="sig-table-wrap">
        <table class="sig-table sig-table-sm">
          <thead><tr>
            <th>季度</th><th class="sig-num">F-n</th><th class="sig-num">F-EV</th>
            <th class="sig-num">F-胜率</th><th class="sig-num">B-n</th>
            <th class="sig-num">B-EV</th><th class="sig-num">EV差</th>
          </tr></thead>
          <tbody>${trend.map(q => {
            const diff = (q.follow_ev_pct || 0) - (q.blind_ev_pct || 0);
            return `<tr>
              <td>${esc(q.quarter)}</td>
              <td class="sig-num">${q.follow_n || 0}</td>
              <td class="sig-num">${q.follow_ev_pct == null ? '-' : fmtPct(q.follow_ev_pct)}</td>
              <td class="sig-num">${formatUtils.formatWinRate(q.follow_win_rate, 0, '-')}</td>
              <td class="sig-num">${q.blind_n || 0}</td>
              <td class="sig-num">${q.blind_ev_pct == null ? '-' : fmtPct(q.blind_ev_pct)}</td>
              <td class="sig-num">${fmtPct(diff)}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>
      ${tops.length ? `
      <div class="sig-panel-head" style="margin-top:14px"><h4>Top 20 机构（进入 follow 档）</h4></div>
      <div class="sig-table-wrap">
        <table class="sig-table sig-table-sm">
          <thead><tr>
            <th>机构</th><th class="sig-num">n</th>
            <th class="sig-num">EV</th><th class="sig-num">胜率</th>
          </tr></thead>
          <tbody>${tops.map(t => `<tr>
            <td>${esc(t.institution_id)}</td>
            <td class="sig-num">${t.n}</td>
            <td class="sig-num">${fmtPct(t.ev_pct)}</td>
              <td class="sig-num">${formatUtils.formatWinRate(t.win_rate, 0, '-')}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>` : ''}
    `;
  }

  function mount(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = `<div style="padding:10px 0">
      <button class="chip chip-outline" id="${containerId}_run">运行回测（约 10-15 秒）</button>
      <div id="${containerId}_result" style="margin-top:12px"></div>
    </div>`;
    document.getElementById(containerId + '_run').addEventListener('click', async () => {
      const btn = document.getElementById(containerId + '_run');
      const result = document.getElementById(containerId + '_result');
      btn.disabled = true; btn.textContent = '回测运行中…';
      result.innerHTML = '<div class="muted">运行中，约 10-15 秒…</div>';
      try {
        const r = await global.SignalAdapter.fetchBacktest();
        result.innerHTML = renderPanel(r);
      } catch (e) {
        result.innerHTML = `<div class="sig-empty">回测失败: ${esc(e.message)}</div>`;
      }
      btn.disabled = false; btn.textContent = '重新运行';
    });
  }

  global.BacktestPanelWidget = { mount };
  if (typeof globalThis !== 'undefined') {
    globalThis.BacktestPanelWidget = global.BacktestPanelWidget;
  }
})(typeof window !== 'undefined' ? window : this);
