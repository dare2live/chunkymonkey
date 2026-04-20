/* widgets/cohort-card.js — Cohort 反馈闭环 widget（工作台折叠区）
 *
 * window.CohortCardWidget = { mount(containerId) }
 * 依赖 SignalAdapter；订阅 config:changed 自动刷新
 */
(function (global) {
  'use strict';

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }
  function fmtPct(v, digits = 1) {
    if (v == null) return '-';
    const n = Number(v);
    const cls = n >= 0 ? 'sig-pos' : 'sig-neg';
    return `<span class="${cls}">${n >= 0 ? '+' : ''}${n.toFixed(digits)}%</span>`;
  }
  function fmtPctPlain(v, digits = 1) {
    if (v == null) return '-';
    return Number(v).toFixed(digits) + '%';
  }
  function fmtWinRate(wr) {
    if (wr == null) return '-';
    return Math.round(Number(wr) * 100) + '%';
  }

  function renderCard(c) {
    if (!c || !c.cohort_size) {
      return `<div class="sig-cohort-card sig-cohort-empty">
        <div class="sig-cohort-title">反馈闭环：已成熟 cohort</div>
        <div class="muted" style="font-size:12px">${c && c.note ? esc(c.note) : '暂无足够成熟数据'}</div>
      </div>`;
    }
    const f = c.by_bucket.follow || {};
    const s = c.by_bucket.skip || {};
    const b = c.by_bucket.blind || {};
    const ef = c.edge_vs_blind.follow || {};
    const es = c.edge_vs_blind.skip || {};
    const followOk = (ef.ev_diff_pct || 0) > 0;
    const skipOk = (es.ev_diff_pct || 0) < 0;
    const followQuarters = Array.isArray(f.quarterly) ? f.quarterly : [];
    const fTotal = f.n || 0;
    let concentrationPct = 0, concentrationQ = null;
    followQuarters.forEach(q => {
      const pct = fTotal > 0 ? (q.n / fTotal) * 100 : 0;
      if (pct > concentrationPct) { concentrationPct = pct; concentrationQ = q.quarter; }
    });
    const isConcentrated = concentrationPct >= 60 && followQuarters.length > 1;
    const quartersHtml = followQuarters.length
      ? followQuarters.map(q => {
          const pct = fTotal > 0 ? Math.round((q.n / fTotal) * 100) : 0;
          const hot = pct >= 60 ? ' sig-q-hot' : '';
          return `<span class="sig-q-cell${hot}" title="${esc(q.quarter)} · n=${q.n} · EV ${fmtPctPlain(q.ev_pct)} · 占 ${pct}%">${esc(q.quarter.slice(2))}:${q.n}</span>`;
        }).join('')
      : '';
    return `<div class="sig-cohort-card">
      <div class="sig-cohort-title">反馈闭环：已成熟 cohort
        <span class="muted" style="font-weight:400">（${esc(c.window.start)} ~ ${esc(c.window.end)} · n=${c.cohort_size}）</span>
      </div>
      <div class="sig-cohort-grid">
        <div class="sig-cohort-cell ${followOk ? 'sig-cohort-good' : 'sig-cohort-bad'}">
          <div class="sig-cohort-bucket">Follow</div>
          <div class="sig-cohort-val">${fmtPct(f.ev_pct)}</div>
          <div class="sig-cohort-sub">n=${f.n} · 胜 ${fmtWinRate(f.win_rate)}</div>
          <div class="sig-cohort-edge">vs Blind ${fmtPct(ef.ev_diff_pct)}</div>
          ${quartersHtml ? `<div class="sig-q-row">${quartersHtml}</div>` : ''}
        </div>
        <div class="sig-cohort-cell">
          <div class="sig-cohort-bucket">Blind 对照</div>
          <div class="sig-cohort-val">${fmtPct(b.ev_pct)}</div>
          <div class="sig-cohort-sub">n=${b.n} · 胜 ${fmtWinRate(b.win_rate)}</div>
          <div class="sig-cohort-edge muted">基线</div>
        </div>
        <div class="sig-cohort-cell ${skipOk ? 'sig-cohort-good' : 'sig-cohort-bad'}">
          <div class="sig-cohort-bucket">Skip（负向筛选）</div>
          <div class="sig-cohort-val">${fmtPct(s.ev_pct)}</div>
          <div class="sig-cohort-sub">n=${s.n} · 胜 ${fmtWinRate(s.win_rate)}</div>
          <div class="sig-cohort-edge">vs Blind ${fmtPct(es.ev_diff_pct)}</div>
        </div>
      </div>
      <div class="muted sig-cohort-hint">
        ${followOk && skipOk
          ? '✓ Follow 优于盲跟、Skip 劣于盲跟——筛选能力有效'
          : '⚠ 筛选方向与预期不一致，可能样本量不足或市场风格偏离'}
        ${isConcentrated ? ` · <b style="color:#b45309">Follow 样本 ${Math.round(concentrationPct)}% 集中于 ${esc(concentrationQ)}</b>` : ''}
      </div>
    </div>`;
  }

  async function mount(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<div class="muted" style="padding:10px">加载中…</div>';
    try {
      const c = await global.SignalAdapter.fetchCohort(180);
      container.innerHTML = renderCard(c) +
        `<div style="margin-top:8px"><button class="chip chip-ghost chip-sm" id="${containerId}_refresh">刷新 cohort</button></div>`;
      document.getElementById(containerId + '_refresh')?.addEventListener('click', () => mount(containerId));
    } catch (e) {
      container.innerHTML = `<div class="sig-empty">加载失败: ${esc(e.message)}</div>`;
    }
  }

  // 订阅 config:changed 自动刷新（如果容器在 DOM 中）
  if (global.SignalAdapter) {
    global.SignalAdapter.on('config:changed', () => {
      // 如果容器存在则刷新
      document.querySelectorAll('[data-cohort-widget]').forEach(el => mount(el.id));
    });
  }

  global.CohortCardWidget = { mount, renderCard };
})(window);
