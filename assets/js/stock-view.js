/* stock-view.js — 股票视图（C6d + C6e）
 *
 * window.StockView = { load, reload }
 * 消费 SignalAdapter，不直接 fetch /api/signals/*
 * 订阅 config:changed 自动刷新
 */
(function (global) {
  'use strict';

  const TDX_L1_NAMES = {
    T01: '能源', T02: '材料', T03: '日常消费', T04: '可选消费',
    T05: '商贸', T06: '社会服务', T07: '装备制造', T08: '公用事业',
    T09: '交通运输', T10: '金融', T11: '建筑地产', T12: '信息产业',
    T13: '综合类',
  };

  const state = {
    byStock: [],           // aggregateByStock 结果
    rawEvents: [],         // 所有 eventToView 对象（用于抽屉过滤）
    watchlistSet: new Set(),
    filterAction: 'all',   // all | follow | watch | skip
    filterIndustry: '',
    filterInstType: '',
    filterMinInst: 0,
    filterWatchlistOnly: false,
    loading: false,
    drawerStock: null,     // 当前打开的股票 code
    drawerTab: 'events',   // events | timeline | evidence
    freshnessDays: 90,
  };

  function el(id) { return document.getElementById(id); }
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
  function fmtDate(d) {
    if (!d) return '-';
    const s = String(d).replace(/[^0-9]/g, '').slice(0, 8);
    return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : String(d);
  }
  function daysAgo(dateStr) {
    if (!dateStr) return null;
    const s = String(dateStr).replace(/[^0-9]/g, '').slice(0, 8);
    if (s.length !== 8) return null;
    const dt = new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8));
    return Math.floor((Date.now() - dt.getTime()) / 86400000);
  }
  function actionBadge(action) {
    const map = {
      follow: '<span class="sig-badge sig-badge-follow">可跟</span>',
      watch:  '<span class="sig-badge sig-badge-watch">观察</span>',
      skip:   '<span class="sig-badge sig-badge-skip">不跟</span>',
    };
    return map[action] || `<span class="sig-badge sig-badge-skip">${esc(action)}</span>`;
  }

  // ── 胶囊筛选选项收集 ─────────────────────────────────────────────
  function collectOptions(byStock) {
    const industries = new Map();
    const instTypes = new Map();
    byStock.forEach(s => {
      const ind = s.industry;
      if (ind) industries.set(ind, (industries.get(ind) || 0) + 1);
      // instType from topEvent.ruleChecks
      const it = (s.topEvent?.ruleChecks || []).find(c => c.key === 'inst_type')?.raw;
      const itStr = it != null ? String(it) : null;
      if (itStr) instTypes.set(itStr, (instTypes.get(itStr) || 0) + 1);
    });
    return {
      industries: Array.from(industries.entries()).sort(([a], [b]) => a.localeCompare(b)),
      instTypes: Array.from(instTypes.entries()).sort((a, b) => b[1] - a[1]),
    };
  }

  // ── 过滤 ────────────────────────────────────────────────────────
  function applyFilters(byStock) {
    return byStock.filter(s => {
      if (state.filterAction !== 'all' && s.bestAction !== state.filterAction) return false;
      if (state.filterIndustry && s.industry !== state.filterIndustry) return false;
      if (state.filterInstType) {
        const it = (s.topEvent?.ruleChecks || []).find(c => c.key === 'inst_type')?.raw;
        if (it == null || String(it) !== state.filterInstType) return false;
      }
      if (state.filterMinInst > 0 && s.instCount < state.filterMinInst) return false;
      if (state.filterWatchlistOnly && !state.watchlistSet.has(s.stockCode)) return false;
      return true;
    });
  }

  // ── 共识进度条 ───────────────────────────────────────────────────
  function consensusBar(ac) {
    const total = (ac.follow || 0) + (ac.watch || 0) + (ac.skip || 0);
    if (!total) return '<span class="muted">—</span>';
    const f = Math.round(ac.follow / total * 100);
    const w = Math.round(ac.watch / total * 100);
    const sx = Math.max(0, 100 - f - w);
    return `<div class="sig-consensus-bar" title="Follow ${ac.follow} · Watch ${ac.watch} · Skip ${ac.skip}">
      <div class="sig-consensus-f" style="width:${f}%"></div>
      <div class="sig-consensus-w" style="width:${w}%"></div>
      <div class="sig-consensus-s" style="width:${sx}%"></div>
    </div>
    <div class="sig-consensus-legend muted">
      <span class="sig-pos">${ac.follow}</span>·<span>${ac.watch}</span>·<span class="muted">${ac.skip}</span>
    </div>`;
  }

  // ── 渲染列表行 ───────────────────────────────────────────────────
  function renderStockRow(s) {
    const inWl = state.watchlistSet.has(s.stockCode);
    const star = inWl
      ? `<button class="sig-star sig-star-on" data-sv-unwatch="${esc(s.stockCode)}" title="移出自选">★</button>`
      : `<button class="sig-star sig-star-off" data-sv-watch="${esc(s.stockCode)}" data-sv-name="${esc(s.stockName || '')}" title="加入自选">☆</button>`;
    const da = daysAgo(s.latestNotice);
    const daLabel = da == null ? fmtDate(s.latestNotice) : da === 0 ? '今天' : da + '天前';
    const indLabel = TDX_L1_NAMES[s.industry] || s.industry || '—';
    return `<tr class="sv-row" data-sv-code="${esc(s.stockCode)}">
      <td>
        <div class="sv-stock-name"><b>${esc(s.stockCode)}</b> ${esc(s.stockName || '')}</div>
        <div class="muted sv-industry">${esc(indLabel)}</div>
      </td>
      <td class="sv-signal-cell">
        ${actionBadge(s.bestAction)}
      </td>
      <td class="sv-consensus-cell">
        <div class="sv-inst-count">${s.instCount} 机构</div>
        ${consensusBar(s.actionCounts)}
      </td>
      <td class="sig-num">
        ${s.longEVBest != null ? fmtPct(s.longEVBest) : '-'}
        ${s.topEvent?.longEV?.n ? `<div class="muted sv-sub">n=${s.topEvent.longEV.n}</div>` : ''}
      </td>
      <td class="sig-num">${s.premiumAvg != null ? fmtPct(s.premiumAvg) : '-'}</td>
      <td class="sv-date-cell">
        <div>${esc(daLabel)}</div>
        <div class="muted sv-sub">${fmtDate(s.latestNotice)}</div>
      </td>
      <td class="sv-actions-cell">
        ${star}
        <button class="chip chip-outline chip-sm sv-detail-btn">详情</button>
      </td>
    </tr>`;
  }

  // ── 渲染筛选栏 ──────────────────────────────────────────────────
  function renderFilterBar(byStock) {
    const { industries, instTypes } = collectOptions(byStock);
    const actions = ['all', 'follow', 'watch', 'skip'];
    const actionLabels = { all: '全部', follow: '可跟', watch: '观察', skip: '不跟' };
    const actionBtns = actions.map(a =>
      `<button class="chip ${state.filterAction === a ? 'chip-primary' : 'chip-outline'} chip-sm sv-filter-action" data-action="${a}">${actionLabels[a]}</button>`
    ).join('');

    const indOptions = `<option value="">全部行业</option>` +
      industries.map(([code, n]) =>
        `<option value="${esc(code)}" ${state.filterIndustry === code ? 'selected' : ''}>${esc(TDX_L1_NAMES[code] || code)} (${n})</option>`
      ).join('');
    const itOptions = `<option value="">全部机构类型</option>` +
      instTypes.map(([it, n]) =>
        `<option value="${esc(it)}" ${state.filterInstType === it ? 'selected' : ''}>${esc(it)} (${n})</option>`
      ).join('');

    return `<div class="sv-filter-bar">
      <div class="chip-group">${actionBtns}</div>
      <select class="sv-select" id="svIndFilter">${indOptions}</select>
      <select class="sv-select" id="svItFilter">${itOptions}</select>
      <label class="sv-filter-wl">
        <input type="checkbox" id="svWlOnly" ${state.filterWatchlistOnly ? 'checked' : ''}> 仅自选
      </label>
      <span class="muted sv-count-hint" id="svCountHint"></span>
    </div>`;
  }

  // ── 渲染主列表 ──────────────────────────────────────────────────
  function renderList() {
    const root = el('sv-list-root');
    if (!root) return;
    if (state.loading) { root.innerHTML = '<div class="sig-empty">加载中…</div>'; return; }
    const filtered = applyFilters(state.byStock);
    const hint = el('svCountHint');
    if (hint) hint.textContent = `${filtered.length} / ${state.byStock.length} 支`;
    if (!filtered.length) {
      root.innerHTML = '<div class="sig-empty">无匹配股票</div>';
      return;
    }
    root.innerHTML = `<div class="sig-table-wrap">
      <table class="sig-table sv-table">
        <thead>
          <tr>
            <th>股票</th>
            <th style="width:70px">当期信号</th>
            <th style="width:160px">共识</th>
            <th class="sig-num" style="width:100px" title="该股最佳长期 EV">长期 EV</th>
            <th class="sig-num" style="width:80px">平均溢价</th>
            <th style="width:100px">最近事件</th>
            <th style="width:100px"></th>
          </tr>
        </thead>
        <tbody>${filtered.map(renderStockRow).join('')}</tbody>
      </table>
    </div>`;
    bindListEvents(root);
  }

  function bindListEvents(root) {
    root.querySelectorAll('.sv-row').forEach(row => {
      const code = row.dataset.svCode;
      row.querySelector('.sv-detail-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        openDrawer(code);
      });
      row.querySelector('[data-sv-watch]')?.addEventListener('click', async e => {
        e.stopPropagation();
        const name = e.currentTarget.dataset.svName;
        try {
          await fetch('/api/inst/watchlist', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: code, stock_name: name }),
          });
          state.watchlistSet.add(code);
          renderList();
        } catch (err) { alert('加入自选失败: ' + err.message); }
      });
      row.querySelector('[data-sv-unwatch]')?.addEventListener('click', e => {
        e.stopPropagation();
        state.watchlistSet.delete(code);
        renderList();
      });
    });
  }

  // ── 抽屉 ────────────────────────────────────────────────────────

  function openDrawer(stockCode) {
    state.drawerStock = stockCode;
    state.drawerTab = 'events';
    renderDrawer();
  }

  function renderDrawer() {
    const area = el('sv-drawer-area');
    if (!area) return;
    const code = state.drawerStock;
    if (!code) { area.innerHTML = ''; return; }
    const s = state.byStock.find(x => x.stockCode === code);
    if (!s) { area.innerHTML = ''; return; }
    const inWl = state.watchlistSet.has(code);
    const indLabel = TDX_L1_NAMES[s.industry] || s.industry || '—';
    const tabs = [
      { key: 'events', label: '机构持仓' },
      { key: 'timeline', label: '事件时间线' },
      { key: 'evidence', label: '信号证据链' },
    ];
    const tabBtns = tabs.map(t =>
      `<button class="sv-tab-btn ${state.drawerTab === t.key ? 'sv-tab-active' : ''}" data-svtab="${t.key}">${t.label}</button>`
    ).join('');

    area.innerHTML = `<div class="sig-drawer sv-drawer">
      <div class="sig-drawer-head">
        <div>
          <h3>${esc(code)} ${esc(s.stockName || '')} <span class="muted" style="font-weight:400">· ${esc(indLabel)}</span></h3>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="chip ${inWl ? 'chip-primary' : 'chip-outline'} chip-sm" id="svDrawerStar">
            ${inWl ? '★ 自选' : '☆ 自选'}
          </button>
          <button class="chip chip-ghost chip-sm" id="svDrawerClose">关闭</button>
        </div>
      </div>
      <div class="sv-drawer-kpis">
        <span><span class="muted">机构数</span> <b>${s.instCount}</b></span>
        <span><span class="muted">事件数</span> <b>${s.eventCount}</b></span>
        <span><span class="muted">共识</span> <b class="sig-pos">${s.actionCounts.follow}</b>F·${s.actionCounts.watch}W·<span class="muted">${s.actionCounts.skip}</span>S</span>
        <span><span class="muted">平均溢价</span> ${s.premiumAvg != null ? fmtPct(s.premiumAvg) : '-'}</span>
        <span><span class="muted">最佳长期EV</span> ${s.longEVBest != null ? fmtPct(s.longEVBest) : '-'}</span>
        <span><span class="muted">最近事件</span> ${fmtDate(s.latestNotice)}</span>
      </div>
      <div class="sv-drawer-tabs">${tabBtns}</div>
      <div id="sv-drawer-content" class="sv-drawer-content"></div>
    </div>`;

    area.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el('svDrawerClose').addEventListener('click', () => {
      state.drawerStock = null;
      area.innerHTML = '';
    });
    el('svDrawerStar').addEventListener('click', async () => {
      if (inWl) {
        state.watchlistSet.delete(code);
      } else {
        try {
          await fetch('/api/inst/watchlist', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: code, stock_name: s.stockName || '' }),
          });
          state.watchlistSet.add(code);
        } catch (err) { alert('加入自选失败: ' + err.message); return; }
      }
      renderDrawer();
      renderList();
    });
    area.querySelectorAll('[data-svtab]').forEach(btn => {
      btn.addEventListener('click', () => {
        state.drawerTab = btn.dataset.svtab;
        renderDrawerContent(s);
        area.querySelectorAll('[data-svtab]').forEach(b => b.classList.toggle('sv-tab-active', b.dataset.svtab === state.drawerTab));
      });
    });

    renderDrawerContent(s);
  }

  function renderDrawerContent(s) {
    const content = el('sv-drawer-content');
    if (!content) return;
    if (state.drawerTab === 'events') renderTabEvents(content, s);
    else if (state.drawerTab === 'timeline') renderTabTimeline(content, s);
    else if (state.drawerTab === 'evidence') renderTabEvidence(content, s);
  }

  // Tab1: 机构持仓（该股涉及的机构列表）
  function renderTabEvents(content, s) {
    const rows = s.events.map(ev => `<tr>
      <td>${actionBadge(ev.action)}</td>
      <td><b>${esc(ev.institutionName || ev.institutionId || '-')}</b>
        <div class="muted sv-sub">${esc(ev.institutionType || '')}</div>
      </td>
      <td class="sig-num">${ev.premiumPct != null ? fmtPct(ev.premiumPct) : '-'}</td>
      <td class="sig-num">${ev.longEV?.pct != null ? fmtPct(ev.longEV.pct) : '-'}
        ${ev.longEV?.n ? `<div class="muted sv-sub">n=${ev.longEV.n}</div>` : ''}
      </td>
      <td><span class="muted sv-sub">${fmtDate(ev.noticeDate)}</span></td>
    </tr>`).join('');
    content.innerHTML = `<div class="sig-table-wrap">
      <table class="sig-table sig-table-sm">
        <thead><tr>
          <th style="width:60px">档位</th>
          <th>机构</th>
          <th class="sig-num" style="width:80px">溢价</th>
          <th class="sig-num" style="width:100px">长期 EV</th>
          <th style="width:90px">公告日</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  // Tab2: 事件时间线（按公告日排序）
  function renderTabTimeline(content, s) {
    const events = [...s.events].sort((a, b) =>
      String(b.noticeDate || '').localeCompare(String(a.noticeDate || ''))
    );
    const rows = events.map(ev => `<tr>
      <td><span class="muted sv-sub">${fmtDate(ev.noticeDate)}</span></td>
      <td>${actionBadge(ev.action)}</td>
      <td><b>${esc(ev.institutionName || ev.institutionId || '-')}</b></td>
      <td class="sig-num">${ev.premiumPct != null ? fmtPct(ev.premiumPct) : '-'}</td>
      <td class="sig-num">${ev.shortEV?.pct != null ? fmtPct(ev.shortEV.pct) : '-'}</td>
      <td class="sig-num">${ev.longEV?.pct != null ? fmtPct(ev.longEV.pct) : '-'}</td>
    </tr>`).join('');
    content.innerHTML = `<div class="sig-table-wrap">
      <table class="sig-table sig-table-sm">
        <thead><tr>
          <th style="width:90px">公告日</th>
          <th style="width:60px">档位</th>
          <th>机构</th>
          <th class="sig-num" style="width:80px">溢价</th>
          <th class="sig-num" style="width:100px">近期 EV</th>
          <th class="sig-num" style="width:100px">长期 EV</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  // Tab3: 信号证据链（遍历 ruleChecks 数据驱动渲染，后端加 D9 自动出现）
  function renderTabEvidence(content, s) {
    const followEvents = s.events.filter(e => e.action === 'follow');
    const watchEvents = s.events.filter(e => e.action === 'watch');
    const candidates = [...followEvents, ...watchEvents];
    if (!candidates.length) {
      content.innerHTML = '<div class="sig-empty">无 follow/watch 事件，无法展示证据链</div>';
      return;
    }
    const statusIcon = { pass: '✓', fail: '✗', unknown: '—' };
    const statusWord = { pass: '通过', fail: '不通过', unknown: '未采集' };
    const blocks = candidates.map(ev => {
      const checks = ev.ruleChecks || [];
      if (!checks.length) return '';
      const chips = checks.map(c => {
        const tip = `${esc(c.label)}: ${statusWord[c.status] || '?'} · 原值 ${c.raw == null ? '—' : esc(String(c.raw))} · 阈值 ${esc(c.threshold_display || '')}`;
        return `<div class="sv-evidence-chip sv-evidence-${esc(c.status)}" title="${tip}">
          <div class="sv-evidence-label">${esc(c.label)}</div>
          <div class="sv-evidence-val">${c.raw == null ? '—' : esc(typeof c.raw === 'number' ? c.raw.toFixed(2) : String(c.raw))}</div>
          <div class="sv-evidence-badge">${statusIcon[c.status] || '—'}</div>
        </div>`;
      }).join('');
      return `<div class="sv-evidence-block">
        <div class="sv-evidence-head">
          ${actionBadge(ev.action)}
          <b>${esc(ev.institutionName || ev.institutionId || '-')}</b>
          <span class="muted sv-sub">${fmtDate(ev.noticeDate)}</span>
          ${ev.ruleTriggered ? `<span style="color:#b91c1c;font-size:11px">触发: ${esc(ev.ruleTriggered)}</span>` : ''}
        </div>
        <div class="sv-evidence-grid">${chips}</div>
      </div>`;
    }).join('');
    content.innerHTML = `<div class="sv-evidence-wrap">${blocks}</div>`;
  }

  // ── 主视图渲染 ───────────────────────────────────────────────────
  function renderRoot() {
    const root = el('view-stocks');
    if (!root) return;
    root.innerHTML = `<div class="sv-root">
      <div class="panel panel-hero workbench-hero" style="margin-bottom:14px">
        <div class="section-kicker">Stock Research</div>
        <h2 class="workbench-title">股票 · 信号汇总</h2>
        <p class="muted workbench-tagline">信号是股票的属性。以下为近 <span id="svFreshnessLabel">${state.freshnessDays}</span> 天内有机构 buy 事件的股票，按 follow 数排序。</p>
      </div>
      <div id="sv-filter-area">${renderFilterBar(state.byStock)}</div>
      <div id="sv-list-root"></div>
      <div id="sv-drawer-area"></div>
    </div>`;
    bindFilterEvents();
    renderList();
  }

  function bindFilterEvents() {
    document.querySelectorAll('.sv-filter-action').forEach(btn => {
      btn.addEventListener('click', () => {
        state.filterAction = btn.dataset.action;
        refreshFilter();
      });
    });
    const indSel = el('svIndFilter');
    if (indSel) indSel.addEventListener('change', () => { state.filterIndustry = indSel.value; renderList(); });
    const itSel = el('svItFilter');
    if (itSel) itSel.addEventListener('change', () => { state.filterInstType = itSel.value; renderList(); });
    const wlChk = el('svWlOnly');
    if (wlChk) wlChk.addEventListener('change', () => { state.filterWatchlistOnly = wlChk.checked; renderList(); });
  }

  function refreshFilter() {
    const area = el('sv-filter-area');
    if (area) {
      area.innerHTML = renderFilterBar(state.byStock);
      bindFilterEvents();
    }
    renderList();
  }

  // ── 数据加载 ─────────────────────────────────────────────────────
  async function reload() {
    state.loading = true;
    renderList();
    try {
      const [result, wl] = await Promise.all([
        global.SignalAdapter.fetchSignals(state.freshnessDays),
        fetch('/api/inst/watchlist').then(r => r.ok ? r.json() : null).catch(() => null),
      ]);
      state.byStock = result.byStock || [];
      state.rawEvents = result.events || [];
      state.watchlistSet = new Set(((wl && wl.data) || []).map(w => w.stock_code));
    } catch (e) {
      console.error('StockView reload failed', e);
      state.byStock = [];
      state.rawEvents = [];
    }
    state.loading = false;
    renderList();
    // 如果有打开的抽屉，数据已刷新，重新渲染抽屉内容
    if (state.drawerStock) renderDrawer();
  }

  async function load() {
    renderRoot();
    await reload();
  }

  // 订阅 config:changed → 自动刷新
  if (global.SignalAdapter) {
    global.SignalAdapter.on('config:changed', () => {
      if (el('view-stocks')?.classList.contains('active')) reload();
    });
  }

  global.StockView = { load, reload };
})(window);
