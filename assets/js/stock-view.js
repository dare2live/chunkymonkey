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
    byStock: [],           // SignalAdapter.byStock 结果
    rawEvents: [],         // 所有 eventToView 对象（用于抽屉过滤）
    watchlistSet: new Set(),
    filterAction: 'all',   // all | follow | watch | skip
    filterIndustry: '',
    filterInstType: '',
    filterMinInst: 0,
    filterAiTop: '',
    filterWatchlistOnly: false,
    filterScreening: new Set(),   // 多选：'f1' / 'f3' / 'f5'
    filterTurtle: new Set(),      // 多选：'breakout' / 'pre' / 'exit' / 'wait'
    topkItems: [],
    topkMap: new Map(),
    topkMeta: null,
    topkLoading: false,
    signalSummary: null,
    screeningMap: new Map(),      // stock_code → mart_stock_screening 行
    turtleMap: new Map(),         // stock_code → dim_stock_turtle_latest 行
    stockIndex: null,      // byStock 派生索引：筛选选项 / 计数 / 覆盖集合
    loading: false,
    drawerStock: null,     // 当前打开的股票 code
    drawerTab: 'conclusion',   // conclusion | data | model | timeline | rules
    freshnessDays: 90,
  };

  // 海龟 setup_state 中文 → 桶（用于多选过滤）
  function turtleBucket(setupState) {
    const s = String(setupState || '');
    if (s.includes('突破触发')) return 'breakout';
    if (s.includes('待突破')) return 'pre';
    if (s.includes('退出触发')) return 'exit';
    return 'wait';
  }

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }
  function securityIdentity(item, opts) {
    opts = opts || {};
    const payload = {
      stock_code: item?.stock_code || item?.stockCode || item?.code || '',
      stock_name: item?.stock_name || item?.stockName || item?.name || '',
      market: item?.market || '',
    };
    if (global.SecurityIdentity && typeof global.SecurityIdentity.renderSecurityIdentity === 'function') {
      return global.SecurityIdentity.renderSecurityIdentity(payload, opts);
    }
    const name = payload.stock_name || '名称待补';
    const code = payload.stock_code || '--';
    const prefix = code.startsWith('6') ? 'SH' : code.startsWith('8') || code.startsWith('4') ? 'BJ' : 'SZ';
    return `<div class="${esc(opts.className || 'cm-security-identity')}">
      <span class="cm-security-name">${esc(name)}</span>
      <span class="cm-security-meta"><a class="cm-security-code" href="https://xueqiu.com/S/${prefix}${esc(code)}" target="_blank" rel="noopener">${esc(prefix)}: ${esc(code)}</a></span>
    </div>`;
  }
  function stockIdentityPayload(s) {
    const topk = s ? state.topkMap.get(s.stockCode) : null;
    return {
      stock_code: s?.stockCode || topk?.stock_code || '',
      stock_name: s?.stockName || topk?.stock_name || '',
    };
  }
  function fmtPct(v, digits = 1) {
    if (v == null) return '-';
    const n = Number(v);
    const cls = n >= 0 ? 'sig-pos' : 'sig-neg';
    return `<span class="${cls}">${n >= 0 ? '+' : ''}${n.toFixed(digits)}%</span>`;
  }
  function fmtRatioPct(v, digits = 1) {
    if (v == null || isNaN(Number(v))) return '-';
    const n = Number(v) * 100;
    const cls = n >= 0 ? 'sig-pos' : 'sig-neg';
    return `<span class="${cls}">${n >= 0 ? '+' : ''}${n.toFixed(digits)}%</span>`;
  }
  function fmtRatioPlain(v, digits = 1) {
    if (v == null || isNaN(Number(v))) return '-';
    return (Number(v) * 100).toFixed(digits) + '%';
  }
  function fmtNum(v, digits = 4) {
    if (v == null || isNaN(Number(v))) return '-';
    return Number(v).toFixed(digits);
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
  function noticeSourceMeta(source) {
    const key = source || 'unknown';
    const map = {
      source_notice: { label: '公告日', cls: 'sig-source-true', title: '真实源公告日' },
      page_update_date: { label: 'F10更新', cls: 'sig-source-page', title: 'TDX/F10 页面更新日，可观测但不等同真实公告日' },
      fetched_at_observed: { label: '抓取可见', cls: 'sig-source-fetched', title: '本地抓取时已可见，保守晚于真实公告日' },
      regulatory_deadline: { label: '监管兜底', cls: 'sig-source-deadline', title: '监管披露期限兜底，不是真实公告日' },
      unknown: { label: '未知来源', cls: 'sig-source-unknown', title: '公告日来源未标记' },
    };
    return map[key] || map.unknown;
  }
  function noticeSourceBadge(source, opts = {}) {
    const meta = noticeSourceMeta(source);
    return `<span class="sig-source-badge ${meta.cls} ${opts.compact ? 'sig-source-badge-compact' : ''}" title="${esc(meta.title)}">${esc(meta.label)}</span>`;
  }
  function noticeSourceSummary(counts) {
    const c = counts || {};
    const bits = [];
    if (c.source_notice) bits.push(`${c.source_notice}源公告`);
    if (c.page_update_date) bits.push(`${c.page_update_date}F10更新`);
    if (c.fetched_at_observed) bits.push(`${c.fetched_at_observed}抓取可见`);
    if (c.regulatory_deadline) bits.push(`${c.regulatory_deadline}监管兜底`);
    if (c.unknown) bits.push(`${c.unknown}未知`);
    return bits.length ? bits.join(' · ') : '暂无来源标记';
  }
  function normalizeTopkItem(item, idx) {
    const code = item.stock_code || item.code || '';
    const rank = item.rank || item.rank_in_date || idx + 1;
    return {
      ...item,
      stock_code: code,
      rank: Number(rank),
      percentile: item.percentile == null ? null : Number(item.percentile),
    };
  }
  function parseTopkPayload(payload) {
    const items = ((payload && payload.items) || []).map(normalizeTopkItem);
    state.topkItems = items;
    state.topkMap = new Map(items.map(x => [x.stock_code, x]));
    state.topkMeta = payload ? {
      model_id: payload.model_id || null,
      model_role: payload.model_role || null,
      snapshot_date: payload.snapshot_date || null,
      selection_fallback: !!payload.selection_fallback,
      is_default_champion: !!payload.is_default_champion,
      run_mode: payload.run_mode || null,
      count: payload.count || items.length,
    } : null;
  }
  function topPercent(item) {
    if (!item || item.percentile == null || isNaN(item.percentile)) return null;
    return Math.max(0.1, (1 - Number(item.percentile)) * 100);
  }
  function horizonEvidence(item) {
    return item && item.horizon_evidence ? item.horizon_evidence : null;
  }
  function horizonLabel(item) {
    const h = horizonEvidence(item);
    const days = h ? h.selected_horizon_days : item?.selected_horizon_days;
    if (!days) return '--';
    return String(days) + 'd' + ((h && h.is_baseline) || Number(days) === 60 ? '基线' : '');
  }
  function horizonDetailText(item) {
    const h = horizonEvidence(item);
    if (!h) return '暂无个股周期证据';
    const bits = [];
    if (h.selected_horizon_confidence != null) bits.push('conf ' + (Number(h.selected_horizon_confidence) * 100).toFixed(0) + '%');
    if (h.avg_return_advantage != null) bits.push('收益差 ' + (Number(h.avg_return_advantage) * 100).toFixed(1) + '%');
    if (h.selected_max_drawdown != null) bits.push('DD ' + (Number(h.selected_max_drawdown) * 100).toFixed(1) + '%');
    return bits.join(' · ') || '60d基线';
  }
  function horizonStatusLabel(row) {
    if (!row) return '-';
    if (row.is_selected) return '选中';
    if (row.is_baseline) return '基线';
    const map = {
      candidate_pass: '通过',
      candidate_score_advantage_below_threshold: '分数不足',
      candidate_return_advantage_below_threshold: '收益不足',
      candidate_drawdown_blocked: '回撤阻断',
      candidate_confidence_low: '置信不足',
      candidate_low_observation: '样本不足',
      profile_only: '未入选',
    };
    return map[row.candidate_status] || row.candidate_status || '未入选';
  }
  function renderHorizonComparisonTable(horizon) {
    const rows = (horizon && horizon.horizon_comparison || []).slice().sort((a, b) =>
      Number(a.horizon_days || 0) - Number(b.horizon_days || 0)
    );
    if (!rows.length) {
      return '<div class="cm-muted-note" style="margin-top:10px">暂无 5/10/20/60/90d 周期对比明细。</div>';
    }
    const baseline = rows.find(row => row.is_baseline)
      || rows.find(row => Number(row.horizon_days) === Number(horizon?.baseline_horizon_days || 60))
      || null;
    const body = rows.map(row => {
      const avgDelta = baseline && row.avg_return != null && baseline.avg_return != null
        ? Number(row.avg_return) - Number(baseline.avg_return)
        : null;
      const drawdownDelta = baseline && row.max_drawdown != null && baseline.max_drawdown != null
        ? Number(row.max_drawdown) - Number(baseline.max_drawdown)
        : null;
      const tone = row.is_selected
        ? 'good'
        : row.candidate_status === 'candidate_drawdown_blocked'
          || row.candidate_status === 'candidate_low_observation'
          ? 'warn'
          : 'info';
      const badges = [
        row.is_baseline ? '<span class="cm-horizon-pill">60d 基线</span>' : '',
        row.is_selected ? '<span class="cm-horizon-pill cm-horizon-pill-selected">当前使用</span>' : '',
      ].filter(Boolean).join('');
      const delta = !row.is_baseline && baseline
        ? `<div class="cm-horizon-vs">vs 60d · 均收益 ${fmtRatioPct(avgDelta)} · 回撤差 ${fmtRatioPct(drawdownDelta)}</div>`
        : `<div class="cm-horizon-vs">60d 作为默认基线和比较锚点</div>`;
      return `<div class="stock-evidence-item stock-evidence-item--${tone} cm-horizon-node ${row.is_selected ? 'cm-horizon-node-selected' : ''}">
        <div class="stock-evidence-date">${esc(row.horizon_days || '-')}d</div>
        <div class="stock-evidence-content">
          <div class="cm-horizon-title-row">
            <div class="stock-evidence-title">${esc(horizonStatusLabel(row))}</div>
            <div>${badges}</div>
          </div>
          <div class="cm-horizon-metrics">
            <span><b>最高收益</b>${fmtRatioPlain(row.max_return)}</span>
            <span><b>均收益</b>${fmtRatioPlain(row.avg_return)}</span>
            <span><b>最大回撤</b>${fmtRatioPlain(row.max_drawdown)}</span>
            <span><b>胜率</b>${fmtRatioPlain(row.win_rate)}</span>
            <span><b>样本</b>${esc(row.obs_count || '-')}</span>
          </div>
          ${delta}
          <div class="cm-horizon-reason">${esc(row.reason_code || '-')}</div>
        </div>
      </div>`;
    }).join('');
    return `<div class="stock-evidence-timeline cm-horizon-timeline">${body}</div>`;
  }
  function renderContributionTable(topk) {
    const rows = (topk && topk.top_feature_contributions || []).slice(0, 8);
    if (!rows.length) {
      return '<div class="cm-muted-note" style="margin-top:10px">暂无精确贡献分解。</div>';
    }
    const body = rows.map(row => {
      const cls = Number(row.contribution || 0) >= 0 ? 'sig-pos' : 'sig-neg';
      return `<tr>
        <td><code>${esc(row.name || '-')}</code></td>
        <td class="sig-num ${cls}">${fmtNum(row.contribution, 5)}</td>
        <td class="sig-num">${fmtRatioPlain(row.contribution_pct, 1)}</td>
        <td class="sig-num">${fmtNum(row.model_value, 4)}</td>
        <td>${esc(row.direction || '-')}</td>
      </tr>`;
    }).join('');
    return `<div class="sig-table-wrap" style="margin-top:12px">
      <table class="sig-table sig-table-sm">
        <thead><tr>
          <th>特征</th>
          <th class="sig-num">贡献</th>
          <th class="sig-num">占比</th>
          <th class="sig-num">模型值</th>
          <th>方向</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
  }
  function renderAiCell(stockCode) {
    const item = state.topkMap.get(stockCode);
    if (!item) return '<span class="muted sv-sub">--</span>';
    const top = topPercent(item);
    return `<div class="sv-ai-cell">
      <b>#${esc(item.rank || '-')}</b>
      <span class="muted sv-sub">${top == null ? 'Top --' : 'Top ' + top.toFixed(top < 10 ? 1 : 0) + '%'}</span>
    </div>`;
  }
  function modelRoleText(meta) {
    if (!meta) return '未加载';
    if (meta.selection_fallback) return 'fallback';
    if (meta.is_default_champion || meta.model_role === 'champion') return 'champion';
    return meta.model_role || 'explicit';
  }

  // ── byStock 派生索引 ─────────────────────────────────────────────
  function buildStockIndex(byStock, screeningMap = state.screeningMap, turtleMap = state.turtleMap) {
    const items = Array.isArray(byStock) ? byStock : [];
    const screening = screeningMap || new Map();
    const turtle = turtleMap || new Map();
    const industries = new Map();
    const instTypes = new Map();
    const screeningHits = { f1: 0, f3: 0, f5: 0 };
    const turtleHits = { breakout: 0, pre: 0, exit: 0, wait: 0 };
    const stockMap = new Map();
    const stockCodes = new Set();
    for (const s of items) {
      stockMap.set(s.stockCode, s);
      stockCodes.add(s.stockCode);
      const ind = s.industry;
      if (ind) industries.set(ind, (industries.get(ind) || 0) + 1);
      const it = s.topEvent?.institutionType;
      const itStr = it != null ? String(it) : null;
      if (itStr) instTypes.set(itStr, (instTypes.get(itStr) || 0) + 1);
      const scr = screening.get(s.stockCode);
      if (scr) {
        if (scr.f1_hit) screeningHits.f1 += 1;
        if (scr.f3_hit) screeningHits.f3 += 1;
        if (scr.f5_hit) screeningHits.f5 += 1;
      }
      const tur = turtle.get(s.stockCode);
      if (tur && tur.turtle_setup_state) {
        turtleHits[turtleBucket(tur.turtle_setup_state)] += 1;
      }
    }
    return {
      industries: Array.from(industries.entries()).sort(([a], [b]) => a.localeCompare(b)),
      instTypes: Array.from(instTypes.entries()).sort((a, b) => b[1] - a[1]),
      screeningHits,
      turtleHits,
      stockCodes,
      stockMap,
    };
  }

  // ── 过滤 ────────────────────────────────────────────────────────
  function applyFilters(byStock) {
    return byStock.filter(s => {
      if (state.filterAction !== 'all' && s.bestAction !== state.filterAction) return false;
      if (state.filterIndustry && s.industry !== state.filterIndustry) return false;
      if (state.filterInstType) {
        const it = s.topEvent?.institutionType;
        if (it == null || String(it) !== state.filterInstType) return false;
      }
      if (state.filterAiTop) {
        const topk = state.topkMap.get(s.stockCode);
        if (!topk) return false;
        if (state.filterAiTop === 'top20' && Number(topk.rank) > 20) return false;
        if (state.filterAiTop === 'top50' && Number(topk.rank) > 50) return false;
      }
      if (state.filterMinInst > 0 && s.instCount < state.filterMinInst) return false;
      if (state.filterWatchlistOnly && !state.watchlistSet.has(s.stockCode)) return false;
      if (state.filterScreening.size > 0) {
        const scr = state.screeningMap.get(s.stockCode);
        if (!scr) return false;
        const hit = (state.filterScreening.has('f1') && scr.f1_hit)
                 || (state.filterScreening.has('f3') && scr.f3_hit)
                 || (state.filterScreening.has('f5') && scr.f5_hit);
        if (!hit) return false;
      }
      if (state.filterTurtle.size > 0) {
        const tur = state.turtleMap.get(s.stockCode);
        if (!tur) return false;
        if (!state.filterTurtle.has(turtleBucket(tur.turtle_setup_state))) return false;
      }
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

  // 选股命中徽章（F1/F3/F5）
  function renderScreeningChips(stockCode) {
    const scr = state.screeningMap.get(stockCode);
    if (!scr) return '<span class="muted sv-sub">—</span>';
    const chips = [];
    if (scr.f1_hit) chips.push('<span class="sv-tdx-chip sv-tdx-f1" title="F1 长期低位突破">F1</span>');
    if (scr.f3_hit) chips.push('<span class="sv-tdx-chip sv-tdx-f3" title="F3 多级回撤">F3</span>');
    if (scr.f5_hit) chips.push('<span class="sv-tdx-chip sv-tdx-f5" title="F5 连跌反转">F5</span>');
    if (!chips.length) return '<span class="muted sv-sub">—</span>';
    return chips.join(' ');
  }

  // 海龟状态徽章
  function renderTurtleBadge(stockCode) {
    const tur = state.turtleMap.get(stockCode);
    if (!tur) return '<span class="muted sv-sub">—</span>';
    const state_ = String(tur.turtle_setup_state || '');
    if (!state_) return '<span class="muted sv-sub">—</span>';
    const bucket = turtleBucket(state_);
    const score = tur.turtle_execution_score_v1;
    const scoreStr = score != null ? Number(score).toFixed(0) : '—';
    return `<span class="sv-turtle-badge sv-turtle-${bucket}" title="${esc(state_)} · 执行分 ${scoreStr}">${esc(state_)}</span>`;
  }

  // ── 渲染列表行 ───────────────────────────────────────────────────
  function renderStockRow(s) {
    const inWl = state.watchlistSet.has(s.stockCode);
    const identity = stockIdentityPayload(s);
    const star = inWl
      ? `<button class="sig-star sig-star-on" data-sv-unwatch="${esc(s.stockCode)}" title="移出自选">已选</button>`
      : `<button class="sig-star sig-star-off" data-sv-watch="${esc(s.stockCode)}" data-sv-name="${esc(identity.stock_name || '')}" title="加入自选">+自选</button>`;
    const da = daysAgo(s.latestNotice);
    const daLabel = da == null ? fmtDate(s.latestNotice) : da === 0 ? '今天' : da + '天前';
    const indLabel = TDX_L1_NAMES[s.industry] || s.industry || '—';
    return `<tr class="sv-row" data-sv-code="${esc(s.stockCode)}">
      <td>
        <div class="sv-stock-name">${securityIdentity(identity, { className: 'cm-security-identity cm-security-identity--stock-cell' })}</div>
        <div class="muted sv-industry">${esc(indLabel)}</div>
      </td>
      <td class="sv-ai-rank-cell">${renderAiCell(s.stockCode)}</td>
      <td class="sv-signal-cell">
        ${actionBadge(s.bestAction)}
      </td>
      <td class="sv-consensus-cell">
        <div class="sv-inst-count">${s.instCount} 机构</div>
        ${consensusBar(s.actionCounts)}
      </td>
      <td class="sv-tdx-cell">${renderScreeningChips(s.stockCode)}</td>
      <td class="sv-turtle-cell">${renderTurtleBadge(s.stockCode)}</td>
      <td class="sig-num">
        ${s.longEVBest != null ? fmtPct(s.longEVBest) : '-'}
        ${s.topEvent?.longEV?.n ? `<div class="muted sv-sub">n=${s.topEvent.longEV.n}</div>` : ''}
      </td>
      <td class="sig-num">${s.premiumAvg != null ? fmtPct(s.premiumAvg) : '-'}</td>
      <td class="sv-date-cell">
        <div>${esc(daLabel)}</div>
        <div class="muted sv-sub">${fmtDate(s.latestNotice)} ${noticeSourceBadge(s.topEvent?.noticeDateSource, { compact: true })}</div>
      </td>
      <td class="sv-actions-cell">
        ${star}
        <button class="chip chip-outline chip-sm sv-detail-btn">详情</button>
      </td>
    </tr>`;
  }

  // 多选 chip 计数：当前 byStock 中各桶/公式命中的股票数
  // ── 渲染筛选栏 ──────────────────────────────────────────────────
  function renderFilterBar(byStock) {
    const index = state.stockIndex || buildStockIndex(byStock);
    const { industries, instTypes, screeningHits: scrCount, turtleHits: tCount } = index;
    const actions = ['all', 'follow', 'watch', 'skip'];
    const actionLabels = { all: '全部', follow: '可跟', watch: '观察', skip: '不跟' };
    const actionBtns = actions.map(a =>
      `<button class="chip ${state.filterAction === a ? 'chip-primary' : 'chip-outline'} chip-sm sv-filter-action" data-action="${a}">${actionLabels[a]}</button>`
    ).join('');
    const aiBtns = [
      ['top20', 'AI Top20'],
      ['top50', 'AI Top50'],
    ].map(([k, label]) =>
      `<button class="chip ${state.filterAiTop === k ? 'chip-primary' : 'chip-outline'} chip-sm sv-filter-ai" data-ai-top="${k}">${label}</button>`
    ).join('');

    const indOptions = `<option value="">全部行业</option>` +
      industries.map(([code, n]) =>
        `<option value="${esc(code)}" ${state.filterIndustry === code ? 'selected' : ''}>${esc(TDX_L1_NAMES[code] || code)} (${n})</option>`
      ).join('');
    const itOptions = `<option value="">全部机构类型</option>` +
      instTypes.map(([it, n]) =>
        `<option value="${esc(it)}" ${state.filterInstType === it ? 'selected' : ''}>${esc(it)} (${n})</option>`
      ).join('');

    const scrChips = [
      ['f1', 'F1 长期低位'],
      ['f3', 'F3 多级回撤'],
      ['f5', 'F5 连跌反转'],
    ].map(([k, label]) => {
      const active = state.filterScreening.has(k);
      const n = scrCount[k] || 0;
      return `<button class="chip chip-sm sv-filter-tdx ${active ? 'chip-primary' : 'chip-outline'}" data-tdx="${k}">${label} (${n})</button>`;
    }).join('');

    const turtleChips = [
      ['breakout', '突破触发'],
      ['pre', '待突破'],
      ['exit', '退出触发'],
      ['wait', '等待形态'],
    ].map(([k, label]) => {
      const active = state.filterTurtle.has(k);
      const n = tCount[k] || 0;
      return `<button class="chip chip-sm sv-filter-turtle sv-filter-turtle-${k} ${active ? 'chip-primary' : 'chip-outline'}" data-turtle="${k}">${label} (${n})</button>`;
    }).join('');

    return `<div class="sv-filter-bar">
      <div class="chip-group">${actionBtns}</div>
      <div class="chip-group">${aiBtns}</div>
      <select class="sv-select" id="svIndFilter">${indOptions}</select>
      <select class="sv-select" id="svItFilter">${itOptions}</select>
      <label class="sv-filter-wl">
        <input type="checkbox" id="svWlOnly" ${state.filterWatchlistOnly ? 'checked' : ''}> 仅自选
      </label>
      <span class="muted sv-count-hint" id="svCountHint"></span>
    </div>
    <div class="sv-filter-bar sv-filter-bar-sub">
      <span class="muted sv-filter-label">TDX</span>
      <div class="chip-group">${scrChips}</div>
      <span class="muted sv-filter-label">海龟</span>
      <div class="chip-group">${turtleChips}</div>
      ${(state.filterScreening.size + state.filterTurtle.size) > 0 ? '<button class="chip chip-ghost chip-sm" id="svClearScreening">清除</button>' : ''}
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
            <th style="width:90px">AI</th>
            <th style="width:70px">当期信号</th>
            <th style="width:160px">共识</th>
            <th style="width:90px" title="TDX 选股命中：F1 长期低位 / F3 多级回撤 / F5 连跌反转">TDX</th>
            <th style="width:110px" title="海龟执行特征 setup_state">海龟</th>
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
    state.drawerTab = 'conclusion';
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
    const identity = stockIdentityPayload(s);
    const tabs = [
      { key: 'conclusion', label: '结论' },
      { key: 'data', label: '数据证据' },
      { key: 'model', label: '模型解释' },
      { key: 'timeline', label: '事件时间线' },
      { key: 'rules', label: '规则证据' },
    ];
    const tabBtns = tabs.map(t =>
      `<button class="sv-tab-btn ${state.drawerTab === t.key ? 'sv-tab-active' : ''}" data-svtab="${t.key}">${t.label}</button>`
    ).join('');

    area.innerHTML = `<div class="sig-drawer sv-drawer">
      <div class="sig-drawer-head">
        <div>
          <div class="sv-drawer-title">
            ${securityIdentity(identity, { className: 'cm-security-identity cm-security-identity--inline cm-security-identity--drawer' })}
            <span class="muted" style="font-weight:400">· ${esc(indLabel)}</span>
          </div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="chip ${inWl ? 'chip-primary' : 'chip-outline'} chip-sm" id="svDrawerStar">
            ${inWl ? '已选' : '+自选'}
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
        <span><span class="muted">日期来源</span> ${noticeSourceBadge(s.topEvent?.noticeDateSource)} ${esc(noticeSourceSummary(s.noticeSourceCounts))}</span>
        <span><span class="muted">AI</span> ${renderAiCell(code)}</span>
        <span><span class="muted">周期</span> ${esc(horizonLabel(state.topkMap.get(code)))}</span>
        <span id="sv-drawer-multidim-badge"></span>
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
            body: JSON.stringify({ stock_code: code, stock_name: identity.stock_name || '' }),
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
    // 多维量化评分徽章 (异步, 不阻塞主渲染)
    if (global.MultidimBadgeWidget) {
      global.MultidimBadgeWidget.mount('sv-drawer-multidim-badge', { stockCode: code });
    }

    renderDrawerContent(s);
  }

  function renderDrawerContent(s) {
    const content = el('sv-drawer-content');
    if (!content) return;
    if (state.drawerTab === 'conclusion') renderTabConclusion(content, s);
    else if (state.drawerTab === 'data') renderTabDataEvidence(content, s);
    else if (state.drawerTab === 'model') renderTabModelExplain(content, s);
    else if (state.drawerTab === 'timeline') renderTabTimeline(content, s);
    else if (state.drawerTab === 'rules') renderTabEvidence(content, s);
  }

  function renderTabConclusion(content, s) {
    const topk = state.topkMap.get(s.stockCode);
    const top = topPercent(topk);
    content.innerHTML = `<div class="cm-action-grid cm-action-grid-tight">
      <div class="cm-action-card">
        <div class="cm-action-title">当前结论</div>
        <p>${actionBadge(s.bestAction)}</p>
        <p class="muted">机构共识 <b class="sig-pos">${s.actionCounts.follow}</b> 可跟 · ${s.actionCounts.watch} 观察 · ${s.actionCounts.skip} 不跟。</p>
      </div>
      <div class="cm-action-card">
        <div class="cm-action-title">AI 分位</div>
        <p><b>${topk ? '#' + esc(topk.rank) : '无模型覆盖'}</b></p>
        <p class="muted">${top == null ? '当前 champion 推荐未覆盖该股。' : 'Top ' + top.toFixed(top < 10 ? 1 : 0) + '% · ' + esc(state.topkMeta?.model_id || '')}</p>
      </div>
      <div class="cm-action-card">
        <div class="cm-action-title">跟随周期</div>
        <p><b>${esc(horizonLabel(topk))}</b></p>
        <p class="muted">${esc(horizonDetailText(topk))}</p>
      </div>
      <div class="cm-action-card">
        <div class="cm-action-title">机构事件</div>
        <p><b>${s.instCount}</b> 机构 · <b>${s.eventCount}</b> 事件</p>
        <p class="muted">最近事件 ${fmtDate(s.latestNotice)} · 平均溢价 ${s.premiumAvg != null ? fmtPct(s.premiumAvg) : '-'}</p>
      </div>
    </div>`;
  }

  function renderTabDataEvidence(content, s) {
    const tdx = renderScreeningChips(s.stockCode);
    const turtle = renderTurtleBadge(s.stockCode);
    const eventHtml = document.createElement('div');
    renderTabEvents(eventHtml, s);
    content.innerHTML = `<div class="cm-status-strip" style="margin-bottom:12px">
      <div class="cm-status-item"><span>TDX 公式</span><b>${tdx}</b></div>
      <div class="cm-status-item"><span>海龟状态</span><b>${turtle}</b></div>
      <div class="cm-status-item"><span>日期来源</span><b>${esc(noticeSourceSummary(s.noticeSourceCounts))}</b></div>
    </div>${eventHtml.innerHTML}`;
  }

  function renderTabModelExplain(content, s) {
    const topk = state.topkMap.get(s.stockCode);
    const top = topPercent(topk);
    const horizon = horizonEvidence(topk);
    const topEffects = (horizon && horizon.top_feature_effects || []).slice(0, 3).map(row =>
      `<span><code>${esc(row.feature_name || '-')}</code> ${row.corr == null ? '' : Number(row.corr).toFixed(3)}</span>`
    ).join('');
    content.innerHTML = `<div class="cm-action-grid cm-action-grid-tight">
      <div class="cm-action-card">
        <div class="cm-action-title">Champion 推荐</div>
        <p><b>${topk ? '#' + esc(topk.rank) : '无覆盖'}</b></p>
        <p class="muted">${top == null ? '该股不在当前推荐快照中。' : 'AI 分位 Top ' + top.toFixed(top < 10 ? 1 : 0) + '%'}</p>
      </div>
      <div class="cm-action-card">
        <div class="cm-action-title">模型</div>
        <p><b>${esc(state.topkMeta?.model_id || '--')}</b></p>
        <p class="muted">角色 ${esc(modelRoleText(state.topkMeta))} · 日期 ${esc(state.topkMeta?.snapshot_date || '--')} · ${esc(topk?.explanation_status || 'no explanation')}</p>
      </div>
      <div class="cm-action-card">
        <div class="cm-action-title">个股持股周期</div>
        <p><b>${esc(horizonLabel(topk))}</b></p>
        <p class="muted">${horizon ? '收益优势 ' + fmtRatioPct(horizon.avg_return_advantage) + ' · 回撤 ' + fmtRatioPct(horizon.selected_max_drawdown) : '暂无个股周期证据'}</p>
      </div>
      <div class="cm-action-card">
        <div class="cm-action-title">加性校验</div>
        <p><b>${fmtNum(topk?.base_value, 5)}</b></p>
        <p class="muted">score ${fmtNum(topk?.pred_score, 5)} · error ${fmtNum(topk?.additivity_error, 8)}</p>
      </div>
    </div>
    ${topEffects ? '<div class="cm-muted-note" style="margin-top:10px">周期变量影响 ' + topEffects + '</div>' : ''}
    ${renderContributionTable(topk)}
    ${renderHorizonComparisonTable(horizon)}
    <div id="sv-drawer-model-badge" style="margin-top:12px"></div>`;
    if (global.MultidimBadgeWidget) {
      global.MultidimBadgeWidget.mount('sv-drawer-model-badge', { stockCode: s.stockCode });
    }
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
      <td><span class="muted sv-sub">${fmtDate(ev.noticeDate)}</span><div>${noticeSourceBadge(ev.noticeDateSource, { compact: true })}</div></td>
    </tr>`).join('');
    content.innerHTML = `<div class="sig-table-wrap">
      <table class="sig-table sig-table-sm">
        <thead><tr>
          <th style="width:60px">档位</th>
          <th>机构</th>
          <th class="sig-num" style="width:80px">溢价</th>
          <th class="sig-num" style="width:100px">长期 EV</th>
          <th style="width:120px">可用日</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  // Tab2: 事件时间线（按公告日排序）
  function renderTabTimeline(content, s) {
    const events = s.timelineEvents || s.events || [];
    const rows = events.map(ev => `<tr>
      <td><span class="muted sv-sub">${fmtDate(ev.noticeDate)}</span><div>${noticeSourceBadge(ev.noticeDateSource, { compact: true })}</div></td>
      <td>${actionBadge(ev.action)}</td>
      <td><b>${esc(ev.institutionName || ev.institutionId || '-')}</b></td>
      <td class="sig-num">${ev.premiumPct != null ? fmtPct(ev.premiumPct) : '-'}</td>
      <td class="sig-num">${ev.shortEV?.pct != null ? fmtPct(ev.shortEV.pct) : '-'}</td>
      <td class="sig-num">${ev.longEV?.pct != null ? fmtPct(ev.longEV.pct) : '-'}</td>
    </tr>`).join('');
    content.innerHTML = `<div class="sig-table-wrap">
      <table class="sig-table sig-table-sm">
        <thead><tr>
          <th style="width:120px">可用日</th>
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
    const candidates = [];
    for (const ev of s.events) {
      if (!ev) continue;
      if (ev.action === 'follow' || ev.action === 'watch') candidates.push(ev);
    }
    if (!candidates.length) {
      content.innerHTML = '<div class="sig-empty">无 follow/watch 事件，无法展示证据链</div>';
      return;
    }
    const statusIcon = { pass: 'OK', fail: 'X', unknown: '—' };
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
          ${noticeSourceBadge(ev.noticeDateSource, { compact: true })}
          ${ev.ruleTriggered ? `<span style="color:var(--cm-bad-500);font-size:11px">触发: ${esc(ev.ruleTriggered)}</span>` : ''}
        </div>
        <div class="sv-evidence-grid">${chips}</div>
      </div>`;
    }).join('');
    content.innerHTML = `<div class="sv-evidence-wrap">${blocks}</div>`;
  }

  function renderResearchStatus() {
    const root = el('sv-status-area');
    if (!root) return;
    const meta = state.topkMeta || {};
    const signalCache = state.signalSummary?.cache || {};
    const cacheStatus = signalCache.status || '--';
    const cacheTone = cacheStatus === 'hit' && !signalCache.stale ? 'ok'
      : cacheStatus === 'refreshed' ? 'ok'
      : 'warn';
    const cacheLabel = cacheStatus === 'miss' ? '未物化'
      : signalCache.stale ? '已过期'
      : cacheStatus === 'hit' ? '快照'
      : cacheStatus;
    const cacheTitle = signalCache.built_at
      ? `built ${String(signalCache.built_at).replace('T', ' ').slice(0, 19)}`
      : (signalCache.message || 'today-signal cache');
    const role = modelRoleText(meta);
    const healthTone = state.topkLoading ? 'warn' : state.topkItems.length ? 'ok' : 'warn';
    root.innerHTML = `<div class="cm-status-strip">
      <div class="cm-status-item">
        <span>推荐日期</span>
        <b>${esc(meta.snapshot_date || '--')}</b>
      </div>
      <div class="cm-status-item">
        <span>champion 模型</span>
        <b title="${esc(meta.model_id || '')}">${esc(meta.model_id || '--')}</b>
      </div>
      <div class="cm-status-item cm-status-${role === 'fallback' ? 'warn' : 'ok'}">
        <span>模型角色</span>
        <b>${esc(role)}</b>
      </div>
      <div class="cm-status-item cm-status-ok">
        <span>tdxhub 状态</span>
        <b>主供已接入</b>
      </div>
      <div class="cm-status-item cm-status-info">
        <span>TDX keep challenger</span>
        <b>Shadow 实验中</b>
      </div>
      <div class="cm-status-item cm-status-${cacheTone}">
        <span>信号快照</span>
        <b title="${esc(cacheTitle)}">${esc(cacheLabel)}</b>
      </div>
      <div class="cm-status-item cm-status-${healthTone}">
        <span>画像边界</span>
        <b>机构覆盖股票</b>
      </div>
    </div>`;
  }

  function renderTopkSummary() {
    const root = el('sv-topk-summary');
    if (!root) return;
    if (state.topkLoading) {
      root.innerHTML = '<div class="cm-muted-note">AI TopK 加载中...</div>';
      return;
    }
    if (!state.topkItems.length) {
      root.innerHTML = '<div class="cm-muted-note">暂无 AI TopK；机构事件列表仍可继续使用。</div>';
      return;
    }
    const index = state.stockIndex || buildStockIndex(state.byStock);
    const stockMap = index.stockMap;
    const coveredCodes = index.stockCodes;
    const rows = state.topkItems.slice(0, 10).map(item => {
      const s = stockMap.get(item.stock_code);
      const name = item.stock_name || s?.stockName || '';
      const identity = { stock_code: item.stock_code, stock_name: name };
      const industry = s ? (TDX_L1_NAMES[s.industry] || s.industry || '--') : (item.l2 || item.l1 || '--');
      const top = topPercent(item);
      const covered = coveredCodes.has(item.stock_code);
      return `<tr>
        <td>#${esc(item.rank || '-')}</td>
        <td>${securityIdentity(identity, { className: 'cm-security-identity cm-security-identity--inline' })}</td>
      <td>${top == null ? '--' : 'Top ' + top.toFixed(top < 10 ? 1 : 0) + '%'}</td>
      <td>${esc(horizonLabel(item))}</td>
      <td>${esc(industry)}</td>
      <td>${covered ? '是' : '否'}</td>
        <td><button class="chip chip-outline chip-sm" data-ai-pick="${esc(item.stock_code)}">${covered ? '打开' : '提示'}</button></td>
      </tr>`;
    }).join('');
    root.innerHTML = `<div class="cm-section-head">
      <div>
        <h3>AI 推荐摘要</h3>
        <p class="muted">默认正式 champion 推荐；用于和机构事件、TDX 公式、海龟信号交叉验证。</p>
      </div>
      <span class="cm-muted-note">${esc(state.topkMeta?.count || state.topkItems.length)} 条</span>
    </div>
    <div class="cm-table-scroll">
      <table class="cm-compact-table">
        <thead><tr><th>#</th><th>股票</th><th>AI分位</th><th>周期</th><th>行业</th><th>机构列表</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
    root.querySelectorAll('[data-ai-pick]').forEach(btn => {
      btn.addEventListener('click', () => {
        const code = btn.dataset.aiPick;
        if (coveredCodes.has(code)) openDrawer(code);
        else alert('该股在 AI TopK 中，但当前机构事件画像未覆盖。');
      });
    });
  }

  function refreshResearchPanels() {
    renderResearchStatus();
    renderTopkSummary();
  }

  // ── 主视图渲染 ───────────────────────────────────────────────────
  function renderRoot() {
    const root = el('view-stocks');
    if (!root) return;
    root.innerHTML = `<div class="sv-root">
      <div class="cm-page-head">
        <div class="section-kicker">Research Today</div>
        <h2>今日研究</h2>
        <p class="muted">Champion 推荐、机构共识、TDX 信号、海龟状态和数据边界集中在同一页。</p>
      </div>
      <div id="sv-status-area" style="margin-bottom:14px"></div>
      <div id="sv-topk-summary" style="margin-bottom:14px"></div>
      <div id="sv-filter-area">${renderFilterBar(state.byStock)}</div>
      <div id="sv-list-root"></div>
      <div id="sv-drawer-area"></div>
      <details class="workbench-section" id="sv-watchlist-section" style="margin-top:8px">
        <summary><span class="workbench-section-title">自选股</span><span class="muted" style="font-size:11px;font-weight:400">手工加入跟踪的股票</span></summary>
        <div id="watchlistContainer" style="padding:10px 0">加载中...</div>
      </details>
      <details class="workbench-section" id="sv-exclusion-section">
        <summary><span class="workbench-section-title">股票排除规则</span><span class="muted" style="font-size:11px;font-weight:400">被排除的股票不参与分析</span></summary>
        <div id="exclusionCategories" style="padding:10px 0">加载中...</div>
      </details>
    </div>`;
    bindFilterEvents();
    bindAuxSections();
    refreshResearchPanels();
    renderList();
  }

  function bindAuxSections() {
    const wlSec = el('sv-watchlist-section');
    if (wlSec) wlSec.addEventListener('toggle', () => {
      if (wlSec.open && global.App) global.App.loadWatchlist();
    }, { once: true });
    const exclSec = el('sv-exclusion-section');
    if (exclSec) exclSec.addEventListener('toggle', () => {
      if (exclSec.open && global.App) global.App.loadExclusions();
    }, { once: true });
  }

  function bindFilterEvents() {
    document.querySelectorAll('.sv-filter-action').forEach(btn => {
      btn.addEventListener('click', () => {
        state.filterAction = btn.dataset.action;
        refreshFilter();
      });
    });
    document.querySelectorAll('.sv-filter-ai').forEach(btn => {
      btn.addEventListener('click', () => {
        const k = btn.dataset.aiTop;
        state.filterAiTop = state.filterAiTop === k ? '' : k;
        refreshFilter();
      });
    });
    const indSel = el('svIndFilter');
    if (indSel) indSel.addEventListener('change', () => { state.filterIndustry = indSel.value; renderList(); });
    const itSel = el('svItFilter');
    if (itSel) itSel.addEventListener('change', () => { state.filterInstType = itSel.value; renderList(); });
    const wlChk = el('svWlOnly');
    if (wlChk) wlChk.addEventListener('change', () => { state.filterWatchlistOnly = wlChk.checked; renderList(); });
    document.querySelectorAll('.sv-filter-tdx').forEach(btn => {
      btn.addEventListener('click', () => {
        const k = btn.dataset.tdx;
        if (state.filterScreening.has(k)) state.filterScreening.delete(k);
        else state.filterScreening.add(k);
        refreshFilter();
      });
    });
    document.querySelectorAll('.sv-filter-turtle').forEach(btn => {
      btn.addEventListener('click', () => {
        const k = btn.dataset.turtle;
        if (state.filterTurtle.has(k)) state.filterTurtle.delete(k);
        else state.filterTurtle.add(k);
        refreshFilter();
      });
    });
    const clearBtn = el('svClearScreening');
    if (clearBtn) clearBtn.addEventListener('click', () => {
      state.filterScreening.clear();
      state.filterTurtle.clear();
      refreshFilter();
    });
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
    state.topkLoading = true;
    refreshResearchPanels();
    renderList();
    try {
      const [result, wl, enrich, topk] = await Promise.all([
        global.SignalAdapter.fetchSignals(state.freshnessDays),
        fetch('/api/inst/watchlist').then(r => r.ok ? r.json() : null).catch(() => null),
        global.SignalAdapter.fetchScreeningEnrichment().catch(() => null),
        fetch('/api/rec/daily-topk?limit=80').then(r => r.ok ? r.json() : null).catch(() => null),
      ]);
      state.signalSummary = result.summary || null;
      state.byStock = result.byStock || [];
      state.rawEvents = result.events || [];
      state.watchlistSet = new Set(((wl && wl.data) || []).map(w => w.stock_code));
      state.screeningMap = (enrich && enrich.screening) || new Map();
      state.turtleMap = (enrich && enrich.turtle) || new Map();
      state.stockIndex = buildStockIndex(state.byStock, state.screeningMap, state.turtleMap);
      parseTopkPayload(topk);
    } catch (e) {
      console.error('StockView reload failed', e);
      state.signalSummary = null;
      state.byStock = [];
      state.stockIndex = buildStockIndex([]);
      state.rawEvents = [];
      parseTopkPayload(null);
    }
    state.loading = false;
    state.topkLoading = false;
    refreshResearchPanels();
    refreshFilter();
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

  global.StockView = { load, reload, openDrawer, _buildStockIndex: buildStockIndex };
})(window);
