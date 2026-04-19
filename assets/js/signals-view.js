/* signals-view.js — Signals v2 前端视图
 *
 * 独立模块，不依赖 app.js 的内部实现。
 * 只调 /api/signals/* 端点，不参与 legacy composite/pool 渲染。
 *
 * 结构：
 *   SignalsV2 = {
 *     load: 初始化，挂载事件
 *     reload: 刷新信号列表
 *     showDetail: 展开某条事件的抽屉（相似历史 + 机构 track record）
 *     saveConfig: 保存配置 + 重载
 *   }
 */

(function () {
  'use strict';

  const BASE = '';
  const state = {
    config: null,
    signals: [],
    summary: null,
    cohort: null,
    currentFilter: 'follow',
    currentFreshness: 90,
    loading: false,
  };

  // ─── utils ─────────────────────────────────────────────────────

  function el(id) { return document.getElementById(id); }
  function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function fmtPct(v, digits = 1) {
    if (v == null) return '-';
    const n = Number(v);
    const cls = n >= 0 ? 'sig-pos' : 'sig-neg';
    return `<span class="${cls}">${n >= 0 ? '+' : ''}${n.toFixed(digits)}%</span>`;
  }
  function fmtPctPlain(v, digits = 0) {
    if (v == null) return '-';
    return Number(v).toFixed(digits) + '%';
  }
  function fmtWinRate(wr) {
    if (wr == null) return '-';
    return Math.round(Number(wr) * 100) + '%';
  }
  function fmtDate(d) {
    if (!d) return '-';
    const s = String(d).replace(/[^0-9]/g, '').slice(0, 8);
    return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : d;
  }
  function actionBadge(action) {
    const map = {
      follow: { label: '可跟', cls: 'sig-badge-follow' },
      watch: { label: '观察', cls: 'sig-badge-watch' },
      skip: { label: '不跟', cls: 'sig-badge-skip' },
    };
    const m = map[action] || { label: action, cls: 'sig-badge-skip' };
    return `<span class="sig-badge ${m.cls}">${m.label}</span>`;
  }

  async function apiGet(path) {
    const r = await fetch(BASE + path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return await r.json();
  }

  async function apiPost(path, body) {
    const r = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body == null ? null : JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return await r.json();
  }

  // ─── 渲染：summary bar + config ─────────────────────────────────

  function renderSummary() {
    const s = state.summary || { by_action: {}, total: 0, freshness_days: state.currentFreshness };
    const cfg = state.config || {};
    const buckets = s.by_action || {};
    const tabClass = (k) => 'sig-tab' + (state.currentFilter === k ? ' sig-tab-active' : '');
    return `
      <div class="sig-summary">
        <div class="sig-summary-left">
          <div class="sig-tabs">
            <button class="${tabClass('follow')}" data-filter="follow">可跟 · ${buckets.follow || 0}</button>
            <button class="${tabClass('watch')}" data-filter="watch">观察 · ${buckets.watch || 0}</button>
            <button class="${tabClass('skip')}" data-filter="skip">不跟 · ${buckets.skip || 0}</button>
            <button class="${tabClass('all')}" data-filter="all">全部 · ${s.total || 0}</button>
          </div>
          <div class="sig-summary-hint muted">近 ${s.freshness_days || state.currentFreshness} 天 buy 事件；历史 EV、胜率基于该机构/该行业严格早于事件公告日的数据</div>
        </div>
        <div class="sig-summary-right">
          <label class="sig-inline">
            <span>窗口</span>
            <select id="sigFreshness" class="sig-select">
              <option value="30">30 天</option>
              <option value="60">60 天</option>
              <option value="90">90 天</option>
              <option value="180">180 天</option>
              <option value="365">1 年</option>
            </select>
          </label>
          <button class="sig-btn sig-btn-ghost" id="sigOpenConfig">参数</button>
          <button class="sig-btn sig-btn-ghost" id="sigOpenBacktest">回测</button>
          <button class="sig-btn" id="sigReload">刷新</button>
        </div>
      </div>
    `;
  }

  function renderSignalRow(sig) {
    const ev = sig.ev_stats || {};
    const realized = sig.realized_return_pct;
    const realizedCell = realized == null
      ? '<span class="muted">—</span>'
      : fmtPct(realized);
    const scopeLabel = {
      inst_industry: '同机构·同行业',
      inst_all: '同机构·全行业',
      insufficient: '样本不足',
    }[sig.scope] || sig.scope;

    return `
      <tr class="sig-row" data-event-id="${encodeURIComponent(sig.event_id)}" data-inst-id="${encodeURIComponent(sig.institution_id)}">
        <td>${actionBadge(sig.action)}</td>
        <td>
          <div class="sig-stock"><b>${esc(sig.stock_code)}</b> ${esc(sig.stock_name || '')}</div>
          <div class="muted sig-industry">${esc(sig.industry || '—')}</div>
        </td>
        <td>
          <div>${esc(sig.institution_name || sig.institution_id)}</div>
          <div class="muted sig-sub">${esc(sig.event_type)} · ${fmtDate(sig.notice_date)}</div>
        </td>
        <td class="sig-num">${ev.n || 0}</td>
        <td class="sig-num">${ev.ev_pct == null ? '-' : fmtPct(ev.ev_pct)}</td>
        <td class="sig-num">${fmtWinRate(ev.win_rate)}</td>
        <td class="sig-num">${sig.premium_pct == null ? '-' : fmtPct(sig.premium_pct)}</td>
        <td class="sig-num">${ev.avg_drawdown_pct == null ? '-' : fmtPctPlain(ev.avg_drawdown_pct, 1)}</td>
        <td class="sig-num">${realizedCell}</td>
        <td class="sig-scope muted">${scopeLabel}</td>
        <td><button class="sig-btn sig-btn-sm sig-detail-btn">详情</button></td>
      </tr>
    `;
  }

  function renderSignalsTable() {
    const filtered = state.currentFilter === 'all'
      ? state.signals
      : state.signals.filter(s => s.action === state.currentFilter);

    if (filtered.length === 0) {
      return `<div class="sig-empty">窗口内无 ${state.currentFilter === 'all' ? '' : '「' + state.currentFilter + '」档'} 信号</div>`;
    }

    return `
      <div class="sig-table-wrap">
        <table class="sig-table">
          <thead>
            <tr>
              <th style="width:60px">建议</th>
              <th>股票</th>
              <th>机构 · 事件</th>
              <th class="sig-num" style="width:52px">样本</th>
              <th class="sig-num" style="width:80px">历史EV</th>
              <th class="sig-num" style="width:60px">胜率</th>
              <th class="sig-num" style="width:68px">溢价</th>
              <th class="sig-num" style="width:68px" title="历史相似样本的平均最大回撤">均回撤</th>
              <th class="sig-num" style="width:72px" title="本事件发生后实际 60d 收益（仅供复盘）">实际</th>
              <th class="sig-scope">相似口径</th>
              <th style="width:60px"></th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(renderSignalRow).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderCohortCard() {
    const c = state.cohort;
    if (!c || !c.cohort_size) {
      return `
        <div class="sig-cohort-card sig-cohort-empty">
          <div class="sig-cohort-title">反馈闭环：已成熟 cohort</div>
          <div class="muted" style="font-size:12px">${c && c.note ? esc(c.note) : '暂无足够成熟数据'}</div>
        </div>`;
    }
    const f = c.by_bucket.follow || {};
    const s = c.by_bucket.skip || {};
    const b = c.by_bucket.blind || {};
    const edgeF = c.edge_vs_blind.follow || {};
    const edgeS = c.edge_vs_blind.skip || {};
    const followOk = (edgeF.ev_diff_pct || 0) > 0;
    const skipOk = (edgeS.ev_diff_pct || 0) < 0;
    return `
      <div class="sig-cohort-card">
        <div class="sig-cohort-title">
          反馈闭环：已成熟 cohort
          <span class="muted" style="font-weight:400">（${esc(c.window.start)} ~ ${esc(c.window.end)} · n=${c.cohort_size}）</span>
        </div>
        <div class="sig-cohort-grid">
          <div class="sig-cohort-cell ${followOk ? 'sig-cohort-good' : 'sig-cohort-bad'}">
            <div class="sig-cohort-bucket">Follow</div>
            <div class="sig-cohort-val">${fmtPct(f.ev_pct)}</div>
            <div class="sig-cohort-sub">n=${f.n} · 胜 ${fmtWinRate(f.win_rate)}</div>
            <div class="sig-cohort-edge">vs Blind ${fmtPct(edgeF.ev_diff_pct)}</div>
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
            <div class="sig-cohort-edge">vs Blind ${fmtPct(edgeS.ev_diff_pct)}</div>
          </div>
        </div>
        <div class="muted sig-cohort-hint">
          ${followOk && skipOk
            ? '✓ Follow 优于盲跟、Skip 劣于盲跟——筛选能力有效'
            : '⚠ 筛选方向与预期不一致，可能样本量不足或市场风格偏离'}
        </div>
      </div>
    `;
  }

  function renderRoot() {
    const root = el('view-signals-v2');
    if (!root) return;
    root.innerHTML = `
      <div class="sig-root">
        <div class="sig-hero">
          <div class="sig-hero-text">
            <h2>十大股东跟随信号</h2>
            <p class="muted">用历史同机构×同行业的 60 天跟随收益做 KNN 决策，只给三档建议：<b>可跟 / 观察 / 不跟</b>。没有评分合成，没有封顶规则，所有判断可回到同行业历史事件表里验证。</p>
          </div>
        </div>
        <div id="sigCohortArea">${renderCohortCard()}</div>
        <div id="sigSummaryArea">${renderSummary()}</div>
        <div id="sigTableArea">${state.loading ? '<div class="sig-empty">加载中...</div>' : renderSignalsTable()}</div>
        <div id="sigDetailArea"></div>
        <div id="sigConfigArea"></div>
        <div id="sigBacktestArea"></div>
      </div>
    `;
    // bind
    el('sigFreshness').value = String(state.currentFreshness);
    el('sigFreshness').addEventListener('change', (e) => {
      state.currentFreshness = parseInt(e.target.value, 10) || 90;
      reload();
    });
    el('sigReload').addEventListener('click', reload);
    el('sigOpenConfig').addEventListener('click', openConfig);
    el('sigOpenBacktest').addEventListener('click', openBacktest);
    root.querySelectorAll('.sig-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        state.currentFilter = btn.dataset.filter;
        el('sigSummaryArea').innerHTML = renderSummary();
        el('sigTableArea').innerHTML = renderSignalsTable();
        rebindTable();
        rebindSummary();
      });
    });
    rebindTable();
  }

  function rebindSummary() {
    el('sigFreshness').value = String(state.currentFreshness);
    el('sigFreshness').addEventListener('change', (e) => {
      state.currentFreshness = parseInt(e.target.value, 10) || 90;
      reload();
    });
    el('sigReload').addEventListener('click', reload);
    el('sigOpenConfig').addEventListener('click', openConfig);
    el('sigOpenBacktest').addEventListener('click', openBacktest);
    document.querySelectorAll('.sig-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        state.currentFilter = btn.dataset.filter;
        el('sigSummaryArea').innerHTML = renderSummary();
        el('sigTableArea').innerHTML = renderSignalsTable();
        rebindTable();
        rebindSummary();
      });
    });
  }

  function rebindTable() {
    document.querySelectorAll('.sig-row').forEach(row => {
      row.querySelector('.sig-detail-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const eid = decodeURIComponent(row.dataset.eventId);
        const iid = decodeURIComponent(row.dataset.instId);
        await showDetail(eid, iid);
      });
    });
  }

  // ─── 详情抽屉：相似历史 + 机构 track record ────────────────────

  async function showDetail(eventId, institutionId) {
    const area = el('sigDetailArea');
    area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>加载中...</h3><button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button></div></div>`;
    area.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');

    try {
      const [similar, track] = await Promise.all([
        apiGet(`/api/signals/event/${encodeURIComponent(eventId)}/similar?limit=50`),
        apiGet(`/api/signals/institution/${encodeURIComponent(institutionId)}`),
      ]);
      renderDetail(area, eventId, similar, track);
    } catch (e) {
      area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>加载失败</h3><button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button></div><div class="sig-empty">${esc(e.message)}</div></div>`;
      el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');
    }
  }

  function renderDetail(area, eventId, similar, track) {
    const stats = similar.stats || {};
    const scope = similar.scope || '';
    const samples = similar.samples || [];
    const overall = track.overall || {};
    const byInd = track.by_industry || [];

    area.innerHTML = `
      <div class="sig-drawer">
        <div class="sig-drawer-head">
          <h3>事件证据链 <span class="muted">${esc(eventId.split('|').slice(-2).join(' · '))}</span></h3>
          <button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button>
        </div>
        <div class="sig-drawer-grid">
          <div class="sig-drawer-panel">
            <div class="sig-panel-head">
              <h4>历史相似样本 <span class="muted">（${scope === 'inst_industry' ? '同机构·同行业' : scope === 'inst_all' ? '同机构·全行业' : scope} · n=${stats.n || 0}）</span></h4>
              <div class="sig-stats-inline">
                <span>EV ${stats.ev_pct == null ? '-' : fmtPct(stats.ev_pct)}</span>
                <span>胜率 ${fmtWinRate(stats.win_rate)}</span>
                <span>中位 ${stats.median_pct == null ? '-' : fmtPct(stats.median_pct)}</span>
                <span>P10 ${stats.p10_pct == null ? '-' : fmtPct(stats.p10_pct)}</span>
                <span>P90 ${stats.p90_pct == null ? '-' : fmtPct(stats.p90_pct)}</span>
              </div>
            </div>
            <div class="sig-table-wrap">
              <table class="sig-table sig-table-sm">
                <thead>
                  <tr>
                    <th>公告日</th>
                    <th>股票</th>
                    <th class="sig-num">溢价</th>
                    <th class="sig-num">60d 收益</th>
                    <th>行业</th>
                  </tr>
                </thead>
                <tbody>
                  ${samples.map(s => `
                    <tr>
                      <td>${fmtDate(s.notice_date)}</td>
                      <td><b>${esc(s.stock_code)}</b> ${esc(s.stock_name || '')}</td>
                      <td class="sig-num">${s.premium_pct == null ? '-' : fmtPct(s.premium_pct)}</td>
                      <td class="sig-num">${s.gain == null ? '-' : fmtPct(s.gain)}</td>
                      <td>${esc(s.industry || '—')}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>
          <div class="sig-drawer-panel">
            <div class="sig-panel-head">
              <h4>机构 track record <span class="muted">（${esc(track.institution_id)}）</span></h4>
            </div>
            <div class="sig-track-overall">
              <div class="sig-metric"><div class="sig-metric-val">${overall.n || 0}</div><div class="sig-metric-lbl">总事件</div></div>
              <div class="sig-metric"><div class="sig-metric-val">${overall.ev_pct == null ? '-' : fmtPct(overall.ev_pct)}</div><div class="sig-metric-lbl">全局 EV</div></div>
              <div class="sig-metric"><div class="sig-metric-val">${fmtWinRate(overall.win_rate)}</div><div class="sig-metric-lbl">全局胜率</div></div>
              <div class="sig-metric"><div class="sig-metric-val">${overall.avg_drawdown_pct == null ? '-' : fmtPctPlain(overall.avg_drawdown_pct, 1)}</div><div class="sig-metric-lbl">均回撤</div></div>
            </div>
            <div class="sig-panel-head" style="margin-top:14px">
              <h4>持有期对比 <span class="muted">（揭示 edge 是短线还是长线）</span></h4>
            </div>
            <div class="sig-table-wrap">
              <table class="sig-table sig-table-sm">
                <thead>
                  <tr>
                    <th>持有期</th>
                    <th class="sig-num">样本</th>
                    <th class="sig-num">EV</th>
                    <th class="sig-num">胜率</th>
                    <th class="sig-num">中位</th>
                  </tr>
                </thead>
                <tbody>
                  ${(track.by_horizon || []).map(h => `
                    <tr${h.horizon_days === (state.config && state.config.horizon_days || 60) ? ' class="sig-row-active"' : ''}>
                      <td>${h.horizon_days} 日${h.horizon_days === (state.config && state.config.horizon_days || 60) ? ' <span class="muted">(当前)</span>' : ''}</td>
                      <td class="sig-num">${h.n || 0}</td>
                      <td class="sig-num">${h.ev_pct == null ? '-' : fmtPct(h.ev_pct)}</td>
                      <td class="sig-num">${fmtWinRate(h.win_rate)}</td>
                      <td class="sig-num">${h.median_pct == null ? '-' : fmtPct(h.median_pct)}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
            <div class="sig-panel-head" style="margin-top:16px">
              <h4>按行业拆分 <span class="muted">（只展示样本≥门槛的行业）</span></h4>
            </div>
            <div class="sig-table-wrap">
              <table class="sig-table sig-table-sm">
                <thead>
                  <tr>
                    <th>行业</th>
                    <th class="sig-num">n</th>
                    <th class="sig-num">EV</th>
                    <th class="sig-num">胜率</th>
                    <th class="sig-num">均回撤</th>
                  </tr>
                </thead>
                <tbody>
                  ${byInd.map(r => `
                    <tr>
                      <td>${esc(r.industry || '—')}</td>
                      <td class="sig-num">${r.n}</td>
                      <td class="sig-num">${fmtPct(r.ev_pct)}</td>
                      <td class="sig-num">${fmtWinRate(r.win_rate)}</td>
                      <td class="sig-num">${r.avg_drawdown_pct == null ? '-' : fmtPctPlain(r.avg_drawdown_pct, 1)}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    `;
    el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');
  }

  // ─── 参数面板 ──────────────────────────────────────────────────

  async function openConfig() {
    const area = el('sigConfigArea');
    area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>加载参数...</h3><button class="sig-btn sig-btn-ghost" id="sigCloseConfig">关闭</button></div></div>`;
    el('sigCloseConfig').addEventListener('click', () => area.innerHTML = '');
    try {
      const r = await apiGet('/api/signals/config');
      renderConfigPanel(area, r);
    } catch (e) {
      area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>加载失败</h3><button class="sig-btn sig-btn-ghost" id="sigCloseConfig">关闭</button></div><div class="sig-empty">${esc(e.message)}</div></div>`;
      el('sigCloseConfig').addEventListener('click', () => area.innerHTML = '');
    }
  }

  function renderConfigPanel(area, payload) {
    const cur = payload.current || {};
    const def = payload.defaults || {};
    const desc = payload.descriptions || {};
    const fields = Object.keys(def);
    area.innerHTML = `
      <div class="sig-drawer">
        <div class="sig-drawer-head">
          <h3>信号参数</h3>
          <div>
            <button class="sig-btn sig-btn-ghost" id="sigResetConfig">恢复默认</button>
            <button class="sig-btn sig-btn-ghost" id="sigCloseConfig">关闭</button>
          </div>
        </div>
        <div class="sig-config-grid">
          ${fields.map(k => `
            <label class="sig-config-field">
              <span>${esc(k)}</span>
              <input data-key="${esc(k)}" type="number" step="${k.includes('threshold') ? '0.1' : '1'}" value="${cur[k]}">
              <div class="muted sig-config-desc">${esc(desc[k] || '')} · 默认 ${esc(String(def[k]))}</div>
            </label>
          `).join('')}
        </div>
        <div class="sig-config-actions">
          <button class="sig-btn" id="sigSaveConfig">保存并刷新信号</button>
        </div>
      </div>
    `;
    el('sigCloseConfig').addEventListener('click', () => area.innerHTML = '');
    el('sigResetConfig').addEventListener('click', async () => {
      await apiPost('/api/signals/config/reset');
      await openConfig();
      await reload();
    });
    el('sigSaveConfig').addEventListener('click', async () => {
      const patch = {};
      document.querySelectorAll('.sig-config-field input').forEach(input => {
        const k = input.dataset.key;
        const v = parseFloat(input.value);
        if (!isNaN(v)) patch[k] = v;
      });
      try {
        await apiPost('/api/signals/config', patch);
        area.innerHTML = '';
        await reload();
      } catch (e) {
        alert('保存失败: ' + e.message);
      }
    });
  }

  // ─── 回测面板 ──────────────────────────────────────────────────

  async function openBacktest() {
    const area = el('sigBacktestArea');
    area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>回测运行中…（约 10-15 秒）</h3><button class="sig-btn sig-btn-ghost" id="sigCloseBt">关闭</button></div></div>`;
    el('sigCloseBt').addEventListener('click', () => area.innerHTML = '');
    try {
      const r = await apiGet('/api/signals/backtest');
      renderBacktestPanel(area, r);
    } catch (e) {
      area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>回测失败</h3><button class="sig-btn sig-btn-ghost" id="sigCloseBt">关闭</button></div><div class="sig-empty">${esc(e.message)}</div></div>`;
      el('sigCloseBt').addEventListener('click', () => area.innerHTML = '');
    }
  }

  function renderBacktestPanel(area, r) {
    const cov = r.coverage || {};
    const fp = r.follow_policy || {};
    const wp = r.watch_policy || {};
    const sp = r.skip_policy || {};
    const bp = r.blind_buy || {};
    const trend = r.quarterly_trend || [];
    const tops = r.top_institutions_in_follow || [];
    area.innerHTML = `
      <div class="sig-drawer">
        <div class="sig-drawer-head">
          <h3>历史回测（当前参数）</h3>
          <button class="sig-btn sig-btn-ghost" id="sigCloseBt">关闭</button>
        </div>
        <div class="sig-bt-summary">
          <div class="sig-bt-card sig-bt-follow">
            <div class="sig-bt-lbl">Follow（筛选后）</div>
            <div class="sig-bt-val">${fmtPct(fp.ev_pct)}</div>
            <div class="sig-bt-sub">胜率 ${fmtWinRate(fp.win_rate)} · n=${fp.n || 0}（${((cov.follow / (cov.total_events || 1)) * 100).toFixed(1)}%）</div>
          </div>
          <div class="sig-bt-card">
            <div class="sig-bt-lbl">Blind（盲跟对照）</div>
            <div class="sig-bt-val">${fmtPct(bp.ev_pct)}</div>
            <div class="sig-bt-sub">胜率 ${fmtWinRate(bp.win_rate)} · n=${bp.n || 0}</div>
          </div>
          <div class="sig-bt-card">
            <div class="sig-bt-lbl">Watch 边缘</div>
            <div class="sig-bt-val">${fmtPct(wp.ev_pct)}</div>
            <div class="sig-bt-sub">胜率 ${fmtWinRate(wp.win_rate)} · n=${wp.n || 0}</div>
          </div>
          <div class="sig-bt-card sig-bt-skip">
            <div class="sig-bt-lbl">Skip 被过滤</div>
            <div class="sig-bt-val">${fmtPct(sp.ev_pct)}</div>
            <div class="sig-bt-sub">胜率 ${fmtWinRate(sp.win_rate)} · n=${sp.n || 0}</div>
          </div>
        </div>
        <div class="sig-bt-diff">
          <b>Follow vs Blind: EV 差 ${fmtPct((fp.ev_pct || 0) - (bp.ev_pct || 0))}，胜率差 ${((fp.win_rate || 0) - (bp.win_rate || 0)) * 100 >= 0 ? '+' : ''}${(((fp.win_rate || 0) - (bp.win_rate || 0)) * 100).toFixed(1)}pp</b>
          <span class="muted"> · 筛选有效则 Follow 应优于 Blind</span>
        </div>
        <div class="sig-panel-head" style="margin-top:14px"><h4>季度趋势</h4></div>
        <div class="sig-table-wrap">
          <table class="sig-table sig-table-sm">
            <thead>
              <tr>
                <th>季度</th>
                <th class="sig-num">Follow n</th>
                <th class="sig-num">F-EV</th>
                <th class="sig-num">F-胜率</th>
                <th class="sig-num">Blind n</th>
                <th class="sig-num">B-EV</th>
                <th class="sig-num">B-胜率</th>
                <th class="sig-num">EV差</th>
              </tr>
            </thead>
            <tbody>
              ${trend.map(q => {
                const diff = (q.follow_ev_pct || 0) - (q.blind_ev_pct || 0);
                return `
                  <tr>
                    <td>${esc(q.quarter)}</td>
                    <td class="sig-num">${q.follow_n || 0}</td>
                    <td class="sig-num">${q.follow_ev_pct == null ? '-' : fmtPct(q.follow_ev_pct)}</td>
                    <td class="sig-num">${fmtWinRate(q.follow_win_rate)}</td>
                    <td class="sig-num">${q.blind_n || 0}</td>
                    <td class="sig-num">${q.blind_ev_pct == null ? '-' : fmtPct(q.blind_ev_pct)}</td>
                    <td class="sig-num">${fmtWinRate(q.blind_win_rate)}</td>
                    <td class="sig-num">${fmtPct(diff)}</td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
        <div class="sig-panel-head" style="margin-top:14px"><h4>Top 20 机构（进入 follow 档的）</h4></div>
        <div class="sig-table-wrap">
          <table class="sig-table sig-table-sm">
            <thead>
              <tr>
                <th>机构</th>
                <th class="sig-num">n</th>
                <th class="sig-num">EV</th>
                <th class="sig-num">胜率</th>
              </tr>
            </thead>
            <tbody>
              ${tops.map(t => `
                <tr>
                  <td>${esc(t.institution_id)}</td>
                  <td class="sig-num">${t.n}</td>
                  <td class="sig-num">${fmtPct(t.ev_pct)}</td>
                  <td class="sig-num">${fmtWinRate(t.win_rate)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
    el('sigCloseBt').addEventListener('click', () => area.innerHTML = '');
  }

  // ─── 数据加载 ──────────────────────────────────────────────────

  async function reload() {
    state.loading = true;
    renderRoot();
    try {
      const [today, cohort] = await Promise.all([
        apiGet(`/api/signals/today?freshness_days=${state.currentFreshness}&limit=2000`),
        apiGet(`/api/signals/cohort/recent?lookback_days=180`).catch(() => null),
      ]);
      state.signals = today.signals || [];
      state.summary = today.summary || null;
      state.cohort = cohort;
    } catch (e) {
      console.error('signals load failed', e);
      state.signals = [];
      state.summary = null;
    }
    state.loading = false;
    renderRoot();
  }

  async function load() {
    try {
      const cfg = await apiGet('/api/signals/config');
      state.config = cfg.current;
    } catch (e) { /* ignore */ }
    await reload();
  }

  window.SignalsV2 = { load, reload };
})();
