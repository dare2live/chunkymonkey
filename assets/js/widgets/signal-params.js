/* widgets/signal-params.js — 信号参数 widget（工作台折叠区）
 *
 * window.SignalParamsWidget = { mount(containerId) }
 * 依赖 SignalAdapter；保存后 emit config:changed
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
  function fmtWinRate(wr) {
    if (wr == null) return '-';
    return Math.round(Number(wr) * 100) + '%';
  }

  function el(id) { return document.getElementById(id); }

  function renderCohortInline(cohort) {
    if (!cohort || !cohort.cohort_size) {
      return `<div class="sig-config-cohort"><div class="muted" style="font-size:12px">暂无成熟 cohort 样本</div></div>`;
    }
    const f = cohort.by_bucket.follow || {};
    const b = cohort.by_bucket.blind || {};
    const s = cohort.by_bucket.skip || {};
    const ef = cohort.edge_vs_blind.follow || {};
    const es = cohort.edge_vs_blind.skip || {};
    return `<div class="sig-config-cohort">
      <div class="sig-config-cohort-title">当前参数下 cohort
        <span class="muted" style="font-weight:400">· ${esc(cohort.window.start)}~${esc(cohort.window.end)} · n=${cohort.cohort_size}</span>
      </div>
      <div class="sig-config-cohort-grid">
        <div class="sig-config-cohort-cell">
          <div class="muted">Follow</div><b>${fmtPct(f.ev_pct)}</b>
          <div class="muted">n=${f.n} · 胜 ${fmtWinRate(f.win_rate)}</div>
          <div style="color:${(ef.ev_diff_pct||0)>0?'var(--cm-ok-500)':'var(--cm-bad-500)'}">vs Blind ${fmtPct(ef.ev_diff_pct)}</div>
        </div>
        <div class="sig-config-cohort-cell">
          <div class="muted">Blind</div><b>${fmtPct(b.ev_pct)}</b>
          <div class="muted">n=${b.n} · 胜 ${fmtWinRate(b.win_rate)}</div>
          <div class="muted">基线</div>
        </div>
        <div class="sig-config-cohort-cell">
          <div class="muted">Skip</div><b>${fmtPct(s.ev_pct)}</b>
          <div class="muted">n=${s.n} · 胜 ${fmtWinRate(s.win_rate)}</div>
          <div style="color:${(es.ev_diff_pct||0)<0?'var(--cm-ok-500)':'var(--cm-bad-500)'}">vs Blind ${fmtPct(es.ev_diff_pct)}</div>
        </div>
      </div>
    </div>`;
  }

  async function refreshCohortHolder(holderId) {
    const holder = el(holderId);
    if (!holder) return;
    holder.innerHTML = `<div class="sig-config-cohort"><div class="muted">重算中…</div></div>`;
    try {
      const cohort = await global.SignalAdapter.fetchCohort(180);
      holder.innerHTML = renderCohortInline(cohort);
    } catch (e) {
      holder.innerHTML = `<div class="sig-config-cohort"><div class="muted">cohort 加载失败: ${esc(e.message)}</div></div>`;
    }
  }

  async function mount(containerId) {
    const container = el(containerId);
    if (!container) return;
    container.innerHTML = '<div class="muted" style="padding:10px">加载参数…</div>';
    let payload;
    try {
      payload = await global.SignalAdapter.fetchConfig();
    } catch (e) {
      container.innerHTML = `<div class="sig-empty">加载失败: ${esc(e.message)}</div>`;
      return;
    }
    const cur = payload.current || {};
    const def = payload.defaults || {};
    const desc = payload.descriptions || {};
    const fields = Object.keys(def);
    const fieldKind = k => {
      const v = def[k];
      if (typeof v === 'string') return 'string';
      if (Number.isInteger(v)) return 'int';
      return 'float';
    };
    const cohortHolderId = containerId + '_cohort';

    container.innerHTML = `
      <div style="padding:10px 0">
        <div id="${cohortHolderId}">${renderCohortInline(null)}</div>
        <div class="sig-config-grid" style="margin-top:12px">
          ${fields.map(k => {
            const kind = fieldKind(k);
            const inputAttrs = kind === 'string' ? `type="text"` : `type="number" step="${kind === 'float' ? '0.1' : '1'}"`;
            const curVal = cur[k] == null ? '' : cur[k];
            return `<label class="sig-config-field" data-kind="${kind}">
              <span>${esc(k)}</span>
              <input class="sp-field-input" data-key="${esc(k)}" data-kind="${kind}" ${inputAttrs} value="${esc(String(curVal))}">
              <div class="muted sig-config-desc">${esc(desc[k] || '')} · 默认 ${esc(String(def[k]))}</div>
            </label>`;
          }).join('')}
        </div>
        <div class="sig-config-actions" style="margin-top:12px">
          <button class="chip chip-primary" id="${containerId}_save">保存并刷新</button>
          <button class="chip chip-outline" id="${containerId}_reset" style="margin-left:8px">恢复默认</button>
          <button class="chip chip-ghost" id="${containerId}_recohort" style="margin-left:8px">仅重算 cohort</button>
        </div>
        <div id="${containerId}_historyArea" style="margin-top:14px"></div>
      </div>
    `;

    // 加载当前 cohort
    refreshCohortHolder(cohortHolderId);

    el(containerId + '_recohort').addEventListener('click', () => refreshCohortHolder(cohortHolderId));

    el(containerId + '_reset').addEventListener('click', async () => {
      try {
        await global.SignalAdapter.resetConfig();
        await mount(containerId);
      } catch (e) { alert('重置失败: ' + e.message); }
    });

    el(containerId + '_save').addEventListener('click', async () => {
      const patch = {};
      container.querySelectorAll('.sp-field-input').forEach(input => {
        const k = input.dataset.key;
        const kind = input.dataset.kind;
        if (kind === 'string') {
          patch[k] = input.value;
        } else {
          const v = parseFloat(input.value);
          if (!isNaN(v)) patch[k] = kind === 'int' ? Math.round(v) : v;
        }
      });
      const btn = el(containerId + '_save');
      btn.disabled = true; btn.textContent = '保存中…';
      try {
        await global.SignalAdapter.updateConfig(patch);
        await refreshCohortHolder(cohortHolderId);
        renderHistoryArea(el(containerId + '_historyArea'));
        btn.textContent = '保存并刷新';
        btn.disabled = false;
      } catch (e) {
        btn.textContent = '保存并刷新';
        btn.disabled = false;
        alert('保存失败: ' + e.message);
      }
    });

    renderHistoryArea(el(containerId + '_historyArea'));
  }

  function renderHistoryArea(area) {
    if (!area) return;
    const history = global.SignalAdapter.getConfigHistory();
    if (!history.length) { area.innerHTML = ''; return; }
    const rows = history.slice(0, 10).map(h => {
      const ts = new Date(h.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
      const keys = Object.keys(h.patch || {});
      return `<div class="sv-history-row">
        <span class="muted" style="font-size:11px;min-width:90px">${esc(ts)}</span>
        <span style="font-size:12px">${keys.map(k => `${esc(k)}=${esc(String(h.patch[k]))}`).join(', ')}</span>
      </div>`;
    }).join('');
    area.innerHTML = `<details style="font-size:12px">
      <summary class="muted" style="cursor:pointer;padding:4px 0">参数变更历史（最近 ${history.length} 次）</summary>
      <div style="padding:8px 0;border-top:1px solid var(--cm-ink-100);margin-top:6px">${rows}</div>
    </details>`;
  }

  global.SignalParamsWidget = { mount };
})(window);
