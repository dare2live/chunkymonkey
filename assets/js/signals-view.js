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
  const TDX_L1_NAMES = {
    T01: '能源', T02: '材料', T03: '日常消费', T04: '可选消费',
    T05: '商贸', T06: '社会服务', T07: '装备制造', T08: '公用事业',
    T09: '交通运输', T10: '金融', T11: '建筑地产', T12: '信息产业',
    T13: '综合类',
  };
  const state = {
    config: null,
    signals: [],
    summary: null,
    cohort: null,
    currentFilter: 'follow',
    currentFreshness: 90,
    filterIndustries: new Set(),   // Set<string> of TDX L1 codes; empty = all
    filterInstTypes: new Set(),    // Set<string>; empty = all
    viewMode: 'event',             // 'event' | 'stock'
    stockAggIndex: new Map(),      // stock_code -> agg（5c 抽屉用）
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
    const afterCaps = applyCapsuleFilters(state.signals);
    const capActive = state.filterIndustries.size > 0 || state.filterInstTypes.size > 0;
    const buckets = capActive
      ? afterCaps.reduce((acc, sig) => { acc[sig.action] = (acc[sig.action] || 0) + 1; return acc; }, {})
      : (s.by_action || {});
    const totalShown = capActive ? afterCaps.length : (s.total || 0);
    const tabClass = (k) => 'sig-tab' + (state.currentFilter === k ? ' sig-tab-active' : '');
    return `
      <div class="sig-summary">
        <div class="sig-summary-left">
          <div class="sig-tabs">
            <button class="${tabClass('follow')}" data-filter="follow">可跟 · ${buckets.follow || 0}</button>
            <button class="${tabClass('watch')}" data-filter="watch">观察 · ${buckets.watch || 0}</button>
            <button class="${tabClass('skip')}" data-filter="skip">不跟 · ${buckets.skip || 0}</button>
            <button class="${tabClass('all')}" data-filter="all">全部 · ${totalShown}</button>
          </div>
          <div class="sig-summary-hint muted">近 ${s.freshness_days || state.currentFreshness} 天 buy 事件；历史 EV、胜率基于该机构/该行业严格早于事件公告日的数据</div>
          <div class="sig-viewmode">
            <span class="muted">视角</span>
            <button class="sig-vm-btn${state.viewMode === 'event' ? ' sig-vm-active' : ''}" data-viewmode="event">事件</button>
            <button class="sig-vm-btn${state.viewMode === 'stock' ? ' sig-vm-active' : ''}" data-viewmode="stock">股票聚合</button>
          </div>
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

  const REASON_LABEL_TAG = {
    both_follow: { text: '双口径一致', tone: 'good' },
    short_follow_long_diverge: { text: '近期好·长期弱', tone: 'warn' },
    long_follow_short_diverge: { text: '长期好·近期弱', tone: 'warn' },
    both_watch: { text: '双口径边缘', tone: 'warn' },
    both_below: { text: '双口径偏弱', tone: 'bad' },
    long_follow_short_insufficient: { text: '短期样本不足', tone: 'warn' },
    short_follow_long_insufficient: { text: '长期样本不足', tone: 'warn' },
    long_only_watch: { text: '仅长期数据', tone: 'warn' },
    long_only_skip: { text: '仅长期数据', tone: 'warn' },
    short_only_watch: { text: '仅近期数据', tone: 'warn' },
    short_only_skip: { text: '仅近期数据', tone: 'warn' },
    both_insufficient: { text: '样本不足', tone: 'bad' },
  };

  function reasonBadge(reasonLabel) {
    const r = REASON_LABEL_TAG[reasonLabel];
    if (!r) return '';
    return `<span class="sig-reason-tag sig-reason-${r.tone}">${r.text}</span>`;
  }

  function signalIndustryCode(sig) {
    return (sig.industry || '').trim() || null;
  }
  function signalInstType(sig) {
    const bd = sig.rule_breakdown;
    if (!bd || !Array.isArray(bd.checks)) return null;
    const instCheck = bd.checks.find(c => c.key === 'inst_type');
    return instCheck && instCheck.raw ? String(instCheck.raw) : null;
  }

  function collectFilterOptions(signals) {
    const industries = new Map();   // code → count (only codes actually seen)
    const instTypes = new Map();
    (signals || []).forEach(sig => {
      const ind = signalIndustryCode(sig);
      if (ind) industries.set(ind, (industries.get(ind) || 0) + 1);
      const it = signalInstType(sig);
      if (it) instTypes.set(it, (instTypes.get(it) || 0) + 1);
    });
    return {
      industries: Array.from(industries.entries())
        .sort(([a], [b]) => a.localeCompare(b)),
      instTypes: Array.from(instTypes.entries())
        .sort((a, b) => b[1] - a[1]),
    };
  }

  function renderFilterCapsules(signals) {
    const { industries, instTypes } = collectFilterOptions(signals);
    const indActive = state.filterIndustries.size > 0;
    const itActive = state.filterInstTypes.size > 0;
    const indBtns = industries.map(([code, n]) => {
      const on = state.filterIndustries.has(code);
      const label = TDX_L1_NAMES[code] || code;
      return `<button class="sig-cap-btn${on ? ' sig-cap-active' : ''}" data-capind="${esc(code)}">${esc(label)} <span class="muted">${n}</span></button>`;
    }).join('');
    const itBtns = instTypes.map(([it, n]) => {
      const on = state.filterInstTypes.has(it);
      return `<button class="sig-cap-btn${on ? ' sig-cap-active' : ''}" data-capit="${esc(it)}">${esc(it)} <span class="muted">${n}</span></button>`;
    }).join('');
    const anyActive = indActive || itActive;
    return `
      <div class="sig-caps">
        <div class="sig-caps-row">
          <span class="sig-caps-label">行业</span>
          <div class="sig-caps-body">${indBtns || '<span class="muted">无数据</span>'}</div>
        </div>
        <div class="sig-caps-row">
          <span class="sig-caps-label">机构类型</span>
          <div class="sig-caps-body">${itBtns || '<span class="muted">无数据</span>'}</div>
        </div>
        ${anyActive ? '<button class="sig-btn sig-btn-ghost sig-caps-clear" id="sigClearFilters">清空筛选</button>' : ''}
      </div>
    `;
  }

  function applyCapsuleFilters(signals) {
    const fi = state.filterIndustries;
    const ft = state.filterInstTypes;
    if (fi.size === 0 && ft.size === 0) return signals;
    return signals.filter(s => {
      if (fi.size > 0 && !fi.has(signalIndustryCode(s))) return false;
      if (ft.size > 0 && !ft.has(signalInstType(s))) return false;
      return true;
    });
  }

  function ruleDots(breakdown) {
    if (!breakdown || !Array.isArray(breakdown.checks)) return '';
    const statusWord = { pass: '通过', fail: '不通过', unknown: '未采集' };
    const dots = breakdown.checks.map(c => {
      let rawDisp;
      if (c.raw == null) rawDisp = '—';
      else if (c.key === 'survey_count_90d') rawDisp = String(c.raw) + ' 次';
      else if (c.key === 'inst_type') rawDisp = String(c.raw);
      else if (typeof c.raw === 'number') rawDisp = c.raw.toFixed(2);
      else rawDisp = String(c.raw);
      const tip = `${c.label}: ${statusWord[c.status] || '?'} · 原始 ${rawDisp} · 阈值 ${c.threshold_display}`;
      return `<span class="sig-dot sig-dot-${esc(c.status)}" title="${esc(tip)}"></span>`;
    }).join('');
    return `<div class="sig-rule-dots" aria-label="7 维硬规则体检">${dots}</div>`;
  }

  function windowCell(win) {
    if (!win) return '<span class="muted">—</span>';
    const s = win.stats || {};
    if (!s.n) return '<span class="muted">n=0</span>';
    const evCls = (s.ev_pct || 0) >= 5 ? 'sig-pos' : (s.ev_pct || 0) < 0 ? 'sig-neg' : 'muted';
    return `<div class="sig-win-cell">
      <span class="${evCls}"><b>${s.ev_pct == null ? '-' : (s.ev_pct >= 0 ? '+' : '') + s.ev_pct.toFixed(1) + '%'}</b></span>
      <span class="muted">·${s.n}·${Math.round((s.win_rate || 0) * 100)}%</span>
    </div>`;
  }

  function renderSignalRow(sig) {
    const ev = sig.ev_stats || {};
    const realized = sig.realized_return_pct;
    const realizedCell = realized == null
      ? '<span class="muted">—</span>'
      : fmtPct(realized);

    return `
      <tr class="sig-row" data-event-id="${encodeURIComponent(sig.event_id)}" data-inst-id="${encodeURIComponent(sig.institution_id)}">
        <td>
          <div style="white-space:nowrap">${actionBadge(sig.action)} ${reasonBadge(sig.reason_label)}</div>
          ${ruleDots(sig.rule_breakdown)}
        </td>
        <td>
          <div class="sig-stock"><b>${esc(sig.stock_code)}</b> ${esc(sig.stock_name || '')}</div>
          <div class="muted sig-industry">${esc(sig.industry || '—')}</div>
        </td>
        <td>
          <div>${esc(sig.institution_name || sig.institution_id)}</div>
          <div class="muted sig-sub">${esc(sig.event_type)} · ${fmtDate(sig.notice_date)}</div>
        </td>
        <td class="sig-num">${windowCell(sig.short)}</td>
        <td class="sig-num">${windowCell(sig.long)}</td>
        <td class="sig-num">${sig.premium_pct == null ? '-' : fmtPct(sig.premium_pct)}</td>
        <td class="sig-num">${ev.avg_drawdown_pct == null ? '-' : fmtPctPlain(ev.avg_drawdown_pct, 1)}</td>
        <td class="sig-num">${realizedCell}</td>
        <td style="white-space:nowrap"><button class="sig-btn sig-btn-sm sig-detail-btn">详情</button></td>
      </tr>
    `;
  }

  function aggregateByStock(events) {
    const groups = new Map();
    events.forEach(ev => {
      const key = ev.stock_code;
      if (!groups.has(key)) {
        groups.set(key, {
          stock_code: ev.stock_code,
          stock_name: ev.stock_name,
          industry: ev.industry,
          events: [],
          institutions: new Set(),
          actions: { follow: 0, watch: 0, skip: 0 },
        });
      }
      const g = groups.get(key);
      g.events.push(ev);
      g.institutions.add(ev.institution_id);
      if (g.actions[ev.action] !== undefined) g.actions[ev.action] += 1;
    });
    const out = Array.from(groups.values()).map(g => {
      const premiums = g.events.map(e => e.premium_pct).filter(v => v != null);
      const shortEVs = g.events.map(e => e.short && e.short.stats && e.short.stats.ev_pct).filter(v => v != null);
      const longEVs = g.events.map(e => e.long && e.long.stats && e.long.stats.ev_pct).filter(v => v != null);
      const avg = (arr) => arr.length ? arr.reduce((a,b)=>a+b,0) / arr.length : null;
      const best = (arr) => arr.length ? Math.max(...arr) : null;
      // priority event: follow > watch > skip, then most recent notice_date
      const rank = { follow: 0, watch: 1, skip: 2 };
      const sorted = [...g.events].sort((a, b) => {
        const ra = rank[a.action] ?? 9; const rb = rank[b.action] ?? 9;
        if (ra !== rb) return ra - rb;
        return String(b.notice_date || '').localeCompare(String(a.notice_date || ''));
      });
      const topEvent = sorted[0];
      return {
        ...g,
        inst_count: g.institutions.size,
        ev_count: g.events.length,
        premium_avg: avg(premiums),
        short_ev_best: best(shortEVs),
        long_ev_best: best(longEVs),
        top_action: topEvent.action,
        top_event_id: topEvent.event_id,
        top_inst_id: topEvent.institution_id,
        latest_notice: sorted.reduce((m, e) => {
          const d = String(e.notice_date || '');
          return d > m ? d : m;
        }, ''),
      };
    });
    // sort: follow count desc, then inst_count desc, then latest_notice desc
    out.sort((a, b) => {
      if (b.actions.follow !== a.actions.follow) return b.actions.follow - a.actions.follow;
      if (b.inst_count !== a.inst_count) return b.inst_count - a.inst_count;
      return String(b.latest_notice).localeCompare(String(a.latest_notice));
    });
    return out;
  }

  function consensusBar(actions) {
    const total = actions.follow + actions.watch + actions.skip;
    if (!total) return '';
    const f = Math.round(actions.follow / total * 100);
    const w = Math.round(actions.watch / total * 100);
    const s = Math.max(0, 100 - f - w);
    return `
      <div class="sig-consensus-bar" title="Follow ${actions.follow} · Watch ${actions.watch} · Skip ${actions.skip}">
        <div class="sig-consensus-f" style="width:${f}%"></div>
        <div class="sig-consensus-w" style="width:${w}%"></div>
        <div class="sig-consensus-s" style="width:${s}%"></div>
      </div>
      <div class="sig-consensus-legend muted">
        <span class="sig-pos">${actions.follow}</span>·<span>${actions.watch}</span>·<span class="muted">${actions.skip}</span>
      </div>
    `;
  }

  function renderStockRow(agg) {
    const premCell = agg.premium_avg == null ? '-' : fmtPct(agg.premium_avg);
    const shortCell = agg.short_ev_best == null ? '-' : fmtPct(agg.short_ev_best);
    const longCell = agg.long_ev_best == null ? '-' : fmtPct(agg.long_ev_best);
    return `
      <tr class="sig-row sig-stock-row"
          data-stock-code="${esc(agg.stock_code)}"
          data-event-id="${encodeURIComponent(agg.top_event_id)}"
          data-inst-id="${encodeURIComponent(agg.top_inst_id)}">
        <td>
          <div class="sig-stock"><b>${esc(agg.stock_code)}</b> ${esc(agg.stock_name || '')}</div>
          <div class="muted sig-industry">${esc(TDX_L1_NAMES[agg.industry] || agg.industry || '—')}</div>
        </td>
        <td class="sig-num">${agg.inst_count}<div class="muted sig-sub">${agg.ev_count} 事件</div></td>
        <td style="width:140px">${consensusBar(agg.actions)}</td>
        <td class="sig-num">${premCell}</td>
        <td class="sig-num">${shortCell}</td>
        <td class="sig-num">${longCell}</td>
        <td><span class="muted sig-sub">${fmtDate(agg.latest_notice)}</span></td>
        <td style="white-space:nowrap"><button class="sig-btn sig-btn-sm sig-stock-detail-btn">查看事件</button></td>
      </tr>
    `;
  }

  function renderStockTable(events) {
    if (events.length === 0) {
      const hasCapFilter = state.filterIndustries.size > 0 || state.filterInstTypes.size > 0;
      return `<div class="sig-empty">${hasCapFilter ? '（当前胶囊筛选下）' : ''}无匹配股票</div>`;
    }
    const aggs = aggregateByStock(events);
    state.stockAggIndex = new Map(aggs.map(a => [a.stock_code, a]));
    return `
      <div class="sig-table-wrap">
        <table class="sig-table">
          <thead>
            <tr>
              <th>股票</th>
              <th class="sig-num" style="width:90px" title="去重机构数 · 总事件数">机构 / 事件</th>
              <th style="width:160px" title="Follow / Watch / Skip 事件数分布">共识</th>
              <th class="sig-num" style="width:80px">平均溢价</th>
              <th class="sig-num" style="width:100px">最佳近期 EV</th>
              <th class="sig-num" style="width:100px">最佳长期 EV</th>
              <th style="width:90px">最近事件</th>
              <th style="width:90px"></th>
            </tr>
          </thead>
          <tbody>
            ${aggs.map(renderStockRow).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderSignalsTable() {
    const afterCaps = applyCapsuleFilters(state.signals);
    const filtered = state.currentFilter === 'all'
      ? afterCaps
      : afterCaps.filter(s => s.action === state.currentFilter);

    if (state.viewMode === 'stock') {
      return renderStockTable(filtered);
    }

    if (filtered.length === 0) {
      const hasCapFilter = state.filterIndustries.size > 0 || state.filterInstTypes.size > 0;
      const hint = hasCapFilter ? '（当前胶囊筛选下）' : '';
      return `<div class="sig-empty">${hint}窗口内无 ${state.currentFilter === 'all' ? '' : '「' + state.currentFilter + '」档'} 信号</div>`;
    }

    return `
      <div class="sig-table-wrap">
        <table class="sig-table">
          <thead>
            <tr>
              <th style="width:180px;white-space:nowrap">建议 · 双口径</th>
              <th>股票</th>
              <th>机构 · 事件</th>
              <th class="sig-num" style="width:120px" title="近 365 天：EV · n · 胜率">近期 EV</th>
              <th class="sig-num" style="width:120px" title="全部历史：EV · n · 胜率（严谨 cooldown 过滤）">长期 EV</th>
              <th class="sig-num" style="width:68px">溢价</th>
              <th class="sig-num" style="width:68px" title="长期样本平均最大回撤">均回撤</th>
              <th class="sig-num" style="width:72px" title="本事件发生后实际 60d 收益（仅供复盘）">实际</th>
              <th style="width:70px"></th>
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
    // Phase B0 · 诚实披露：follow 档按季度拆分，揭示 alpha 是否集中在单季。
    // 不加 disclaimer 文字，直接给数据，让用户自己判断。
    const followQuarters = Array.isArray(f.quarterly) ? f.quarterly : [];
    const fTotal = f.n || 0;
    let concentrationPct = 0;
    let concentrationQ = null;
    followQuarters.forEach(q => {
      const pct = fTotal > 0 ? (q.n / fTotal) * 100 : 0;
      if (pct > concentrationPct) { concentrationPct = pct; concentrationQ = q.quarter; }
    });
    const isConcentrated = concentrationPct >= 60 && followQuarters.length > 1;
    const quartersHtml = followQuarters.length
      ? followQuarters.map(q => {
          const pct = fTotal > 0 ? Math.round((q.n / fTotal) * 100) : 0;
          const hot = pct >= 60 ? ' sig-q-hot' : '';
          return `<span class="sig-q-cell${hot}" title="${esc(q.quarter)} · n=${q.n} · EV ${fmtPctPlain(q.ev_pct, 1)} · 占 ${pct}%">${esc(q.quarter.slice(2))}:${q.n}</span>`;
        }).join('')
      : '';
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
            ${quartersHtml ? `<div class="sig-q-row" aria-label="Follow 按季度分布">${quartersHtml}</div>` : ''}
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
          ${isConcentrated ? ` · <b style="color:#b45309">Follow 样本 ${Math.round(concentrationPct)}% 集中于 ${esc(concentrationQ)}</b>` : ''}
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
        <div id="sigFilterArea">${renderFilterCapsules(state.signals)}</div>
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
        refreshFiltersAndTable();
      });
    });
    root.querySelectorAll('[data-viewmode]').forEach(btn => {
      btn.addEventListener('click', () => {
        state.viewMode = btn.dataset.viewmode;
        refreshFiltersAndTable();
      });
    });
    rebindFilters();
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
        refreshFiltersAndTable();
      });
    });
    document.querySelectorAll('[data-viewmode]').forEach(btn => {
      btn.addEventListener('click', () => {
        state.viewMode = btn.dataset.viewmode;
        refreshFiltersAndTable();
      });
    });
  }

  function refreshFiltersAndTable() {
    el('sigFilterArea').innerHTML = renderFilterCapsules(state.signals);
    el('sigSummaryArea').innerHTML = renderSummary();
    el('sigTableArea').innerHTML = renderSignalsTable();
    rebindSummary();
    rebindFilters();
    rebindTable();
  }

  function rebindFilters() {
    document.querySelectorAll('[data-capind]').forEach(btn => {
      btn.addEventListener('click', () => {
        const code = btn.dataset.capind;
        if (state.filterIndustries.has(code)) state.filterIndustries.delete(code);
        else state.filterIndustries.add(code);
        refreshFiltersAndTable();
      });
    });
    document.querySelectorAll('[data-capit]').forEach(btn => {
      btn.addEventListener('click', () => {
        const it = btn.dataset.capit;
        if (state.filterInstTypes.has(it)) state.filterInstTypes.delete(it);
        else state.filterInstTypes.add(it);
        refreshFiltersAndTable();
      });
    });
    const clr = el('sigClearFilters');
    if (clr) clr.addEventListener('click', () => {
      state.filterIndustries.clear();
      state.filterInstTypes.clear();
      refreshFiltersAndTable();
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
      row.querySelector('.sig-stock-detail-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const code = row.dataset.stockCode;
        if (code) showStockDetail(code);
      });
    });
  }

  // ─── 详情抽屉：相似历史 + 机构 track record ────────────────────

  async function showDetail(eventId, institutionId) {
    const area = el('sigDetailArea');
    area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>加载中...</h3><button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button></div></div>`;
    area.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');

    // 从已拉取的 signal 列表里找到当前事件，拿到 rule_breakdown（无需新 API）
    const signal = (state.signals || []).find(s => s.event_id === eventId) || null;

    try {
      const [similar, track] = await Promise.all([
        apiGet(`/api/signals/event/${encodeURIComponent(eventId)}/similar?limit=50`),
        apiGet(`/api/signals/institution/${encodeURIComponent(institutionId)}`),
      ]);
      renderDetail(area, eventId, similar, track, signal);
    } catch (e) {
      area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>加载失败</h3><button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button></div><div class="sig-empty">${esc(e.message)}</div></div>`;
      el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');
    }
  }

  // ─── 股票抽屉：聚合行展开查看该股所有事件（5c）─────────────────

  function showStockDetail(stockCode) {
    const agg = state.stockAggIndex.get(stockCode);
    const area = el('sigDetailArea');
    if (!agg) {
      area.innerHTML = `<div class="sig-drawer"><div class="sig-drawer-head"><h3>未找到股票</h3><button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button></div></div>`;
      el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');
      return;
    }

    const industry = TDX_L1_NAMES[agg.industry] || agg.industry || '—';
    const rows = [...agg.events].sort((a, b) => {
      const rank = { follow: 0, watch: 1, skip: 2 };
      const ra = rank[a.action] ?? 9;
      const rb = rank[b.action] ?? 9;
      if (ra !== rb) return ra - rb;
      return String(b.notice_date || '').localeCompare(String(a.notice_date || ''));
    });

    const eventRowsHtml = rows.map(ev => {
      const shortEV = ev.short && ev.short.stats ? ev.short.stats.ev_pct : null;
      const longEV = ev.long && ev.long.stats ? ev.long.stats.ev_pct : null;
      const prem = ev.premium_pct == null ? '-' : fmtPct(ev.premium_pct);
      return `
        <tr class="sig-stock-event-row"
            data-event-id="${encodeURIComponent(ev.event_id)}"
            data-inst-id="${encodeURIComponent(ev.institution_id)}">
          <td>${actionBadge(ev.action)}</td>
          <td><span class="muted sig-sub">${fmtDate(ev.notice_date)}</span></td>
          <td><b>${esc(ev.institution_name || ev.institution_id || '-')}</b><div class="muted sig-sub">${esc(ev.institution_type || '')}</div></td>
          <td class="sig-num">${prem}</td>
          <td class="sig-num">${shortEV == null ? '-' : fmtPct(shortEV)}</td>
          <td class="sig-num">${longEV == null ? '-' : fmtPct(longEV)}</td>
          <td><button class="sig-btn sig-btn-sm sig-stock-event-open">详情</button></td>
        </tr>
      `;
    }).join('');

    area.innerHTML = `
      <div class="sig-drawer">
        <div class="sig-drawer-head">
          <h3>${esc(agg.stock_code)} ${esc(agg.stock_name || '')} <span class="muted" style="font-weight:400">· ${esc(industry)} · ${agg.ev_count} 事件 / ${agg.inst_count} 机构</span></h3>
          <button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button>
        </div>
        <div style="padding:14px">
          <div class="sig-stock-drawer-summary">
            <div class="sig-stock-drawer-consensus">${consensusBar(agg.actions)}
              <div class="sig-consensus-legend muted"><span class="sig-pos">${agg.actions.follow}</span> follow · <span>${agg.actions.watch}</span> watch · <span class="muted">${agg.actions.skip}</span> skip</div>
            </div>
            <div class="sig-stock-drawer-kpis">
              <span><span class="muted">平均溢价</span> ${agg.premium_avg == null ? '-' : fmtPct(agg.premium_avg)}</span>
              <span><span class="muted">最佳近期 EV</span> ${agg.short_ev_best == null ? '-' : fmtPct(agg.short_ev_best)}</span>
              <span><span class="muted">最佳长期 EV</span> ${agg.long_ev_best == null ? '-' : fmtPct(agg.long_ev_best)}</span>
              <span><span class="muted">最近事件</span> ${fmtDate(agg.latest_notice)}</span>
            </div>
          </div>
          <div class="sig-table-wrap" style="margin-top:12px">
            <table class="sig-table sig-table-sm">
              <thead>
                <tr>
                  <th style="width:60px">档位</th>
                  <th style="width:90px">公告日</th>
                  <th>机构</th>
                  <th class="sig-num" style="width:80px">溢价</th>
                  <th class="sig-num" style="width:100px">近期 EV</th>
                  <th class="sig-num" style="width:100px">长期 EV</th>
                  <th style="width:70px"></th>
                </tr>
              </thead>
              <tbody>${eventRowsHtml}</tbody>
            </table>
          </div>
        </div>
      </div>
    `;
    area.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el('sigCloseDetail').addEventListener('click', () => area.innerHTML = '');
    area.querySelectorAll('.sig-stock-event-row').forEach(r => {
      r.querySelector('.sig-stock-event-open')?.addEventListener('click', (e) => {
        e.stopPropagation();
        showDetail(decodeURIComponent(r.dataset.eventId), decodeURIComponent(r.dataset.instId));
      });
    });
  }

  function fmtBreakdownRaw(raw, key) {
    if (raw === null || raw === undefined) return '<span class="muted">—</span>';
    if (key === 'inst_type') return esc(String(raw));
    // survey_count_90d 是整数次数
    if (key === 'survey_count_90d') return esc(String(raw)) + ' 次';
    // 数值字段保留 2 位小数
    return fmtPctPlain(raw, 2);
  }

  function renderRuleBreakdown(bd) {
    if (!bd || !Array.isArray(bd.checks)) return '';
    const statusIcon = { pass: '✓', fail: '✗', unknown: '—' };
    const triggerLabel = {
      inst_type_blacklisted: '机构类型黑名单',
      premium_too_high: '溢价过高',
      hold_ratio_too_low: '持仓占比过低',
      holder_dispersing: 'D1 股东散户化',
      forecast_too_weak: 'D3 预告偏弱',
      unlock_risk: 'D5 解禁风险',
      survey_too_quiet: 'D8 调研冷门',
    };
    const triggered = bd.triggered ? (triggerLabel[bd.triggered] || bd.triggered) : null;
    return `
      <div class="sig-breakdown-panel">
        <div class="sig-panel-head">
          <h4>硬规则检查
            <span class="muted" style="font-weight:400">
              ${triggered ? '· 触发：<b style="color:#b91c1c">' + esc(triggered) + '</b>' : '· 7 维全部通过或未评估'}
            </span>
          </h4>
        </div>
        <div class="sig-breakdown-grid">
          ${bd.checks.map(c => `
            <div class="sig-breakdown-cell sig-breakdown-${esc(c.status)}">
              <div class="sig-breakdown-label">${esc(c.label)}</div>
              <div class="sig-breakdown-raw">${fmtBreakdownRaw(c.raw, c.key)}</div>
              <div class="sig-breakdown-thresh muted">${esc(c.threshold_display)}</div>
              <div class="sig-breakdown-badge">${statusIcon[c.status] || '—'}</div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  function renderDetail(area, eventId, similar, track, signal) {
    const stats = similar.stats || {};
    const scope = similar.scope || '';
    const samples = similar.samples || [];
    const overall = track.overall || {};
    const byInd = track.by_industry || [];
    const breakdown = signal ? signal.rule_breakdown : null;

    area.innerHTML = `
      <div class="sig-drawer">
        <div class="sig-drawer-head">
          <h3>事件证据链 <span class="muted">${esc(eventId.split('|').slice(-2).join(' · '))}</span></h3>
          <button class="sig-btn sig-btn-ghost" id="sigCloseDetail">关闭</button>
        </div>
        ${breakdown ? `<div style="padding:14px 14px 0">${renderRuleBreakdown(breakdown)}</div>` : ''}
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

  function renderConfigCohortInline(cohort) {
    if (!cohort || !cohort.cohort_size) {
      return `
        <div class="sig-config-cohort">
          <div class="sig-config-cohort-title">当前参数下 cohort 预览</div>
          <div class="muted">暂无成熟样本</div>
        </div>
      `;
    }
    const f = cohort.by_bucket.follow || {};
    const b = cohort.by_bucket.blind || {};
    const s = cohort.by_bucket.skip || {};
    const edgeF = cohort.edge_vs_blind.follow || {};
    const edgeS = cohort.edge_vs_blind.skip || {};
    return `
      <div class="sig-config-cohort">
        <div class="sig-config-cohort-title">
          当前参数下 cohort 预览
          <span class="muted" style="font-weight:400">· ${esc(cohort.window.start)}~${esc(cohort.window.end)} · n=${cohort.cohort_size}</span>
        </div>
        <div class="sig-config-cohort-grid">
          <div class="sig-config-cohort-cell">
            <div class="muted">Follow</div>
            <b>${fmtPct(f.ev_pct)}</b>
            <div class="muted">n=${f.n} · 胜 ${fmtWinRate(f.win_rate)}</div>
            <div style="color:${(edgeF.ev_diff_pct||0)>0?'#16a34a':'#dc2626'}">vs Blind ${fmtPct(edgeF.ev_diff_pct)}</div>
          </div>
          <div class="sig-config-cohort-cell">
            <div class="muted">Blind</div>
            <b>${fmtPct(b.ev_pct)}</b>
            <div class="muted">n=${b.n} · 胜 ${fmtWinRate(b.win_rate)}</div>
            <div class="muted">基线</div>
          </div>
          <div class="sig-config-cohort-cell">
            <div class="muted">Skip</div>
            <b>${fmtPct(s.ev_pct)}</b>
            <div class="muted">n=${s.n} · 胜 ${fmtWinRate(s.win_rate)}</div>
            <div style="color:${(edgeS.ev_diff_pct||0)<0?'#16a34a':'#dc2626'}">vs Blind ${fmtPct(edgeS.ev_diff_pct)}</div>
          </div>
        </div>
      </div>
    `;
  }

  async function refreshConfigCohort() {
    const holder = el('sigConfigCohortHolder');
    if (!holder) return;
    holder.innerHTML = `<div class="sig-config-cohort"><div class="muted">重算中…</div></div>`;
    try {
      const cohort = await apiGet('/api/signals/cohort/recent?lookback_days=180');
      holder.innerHTML = renderConfigCohortInline(cohort);
    } catch (e) {
      holder.innerHTML = `<div class="sig-config-cohort"><div class="muted">cohort 加载失败: ${esc(e.message)}</div></div>`;
    }
  }

  function renderConfigPanel(area, payload) {
    const cur = payload.current || {};
    const def = payload.defaults || {};
    const desc = payload.descriptions || {};
    const fields = Object.keys(def);

    const fieldKind = (k) => {
      const v = def[k];
      if (typeof v === 'string') return 'string';
      if (Number.isInteger(v)) return 'int';
      return 'float';
    };
    const stepFor = (k) => fieldKind(k) === 'float' ? '0.1' : '1';

    // 首屏先用 state.cohort（已在 reload 拉过），保存后再主动刷新
    area.innerHTML = `
      <div class="sig-drawer">
        <div class="sig-drawer-head">
          <h3>信号参数</h3>
          <div>
            <button class="sig-btn sig-btn-ghost" id="sigResetConfig">恢复默认</button>
            <button class="sig-btn sig-btn-ghost" id="sigCloseConfig">关闭</button>
          </div>
        </div>
        <div id="sigConfigCohortHolder">${renderConfigCohortInline(state.cohort)}</div>
        <div class="sig-config-grid">
          ${fields.map(k => {
            const kind = fieldKind(k);
            const inputAttrs = kind === 'string'
              ? `type="text"`
              : `type="number" step="${stepFor(k)}"`;
            const curVal = cur[k] === null || cur[k] === undefined ? '' : cur[k];
            return `
              <label class="sig-config-field" data-kind="${kind}">
                <span>${esc(k)}</span>
                <input data-key="${esc(k)}" data-kind="${kind}" ${inputAttrs} value="${esc(String(curVal))}">
                <div class="muted sig-config-desc">${esc(desc[k] || '')} · 默认 ${esc(String(def[k]))}</div>
              </label>
            `;
          }).join('')}
        </div>
        <div class="sig-config-actions">
          <button class="sig-btn" id="sigSaveConfig">保存并重算 cohort</button>
          <button class="sig-btn sig-btn-ghost" id="sigRecomputeCohort" style="margin-left:8px">仅重算 cohort</button>
        </div>
      </div>
    `;
    el('sigCloseConfig').addEventListener('click', () => area.innerHTML = '');
    el('sigResetConfig').addEventListener('click', async () => {
      await apiPost('/api/signals/config/reset');
      await openConfig();
      await reload();
    });
    el('sigRecomputeCohort').addEventListener('click', async () => {
      await refreshConfigCohort();
    });
    el('sigSaveConfig').addEventListener('click', async () => {
      const patch = {};
      document.querySelectorAll('.sig-config-field input').forEach(input => {
        const k = input.dataset.key;
        const kind = input.dataset.kind;
        const raw = input.value;
        if (kind === 'string') {
          // 字符串字段：空值也允许（表示清空黑名单等）
          patch[k] = raw;
        } else {
          const v = parseFloat(raw);
          if (!isNaN(v)) patch[k] = kind === 'int' ? Math.round(v) : v;
        }
      });
      const btn = el('sigSaveConfig');
      btn.disabled = true; btn.textContent = '保存中…';
      try {
        await apiPost('/api/signals/config', patch);
        // 立即刷新内嵌 cohort（用户看 edge 变化）
        await refreshConfigCohort();
        btn.textContent = '已保存，刷新信号列表…';
        await reload();
        btn.textContent = '保存并重算 cohort';
        btn.disabled = false;
      } catch (e) {
        btn.textContent = '保存并重算 cohort';
        btn.disabled = false;
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
