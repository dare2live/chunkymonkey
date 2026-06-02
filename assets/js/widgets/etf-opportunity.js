/* ============================================================
   etf-opportunity.js — ETF 机会发现 widget
   负责 /api/etf/list 的机会页渲染、挖掘建议和轮动面板挂载
   API: window.ETFOpportunityWidget.mountOpportunity(targetId, deps)
   ============================================================ */
(function (global) {
  'use strict';

  function pickFn(deps, name, fallback) {
    return deps && typeof deps[name] === 'function' ? deps[name] : fallback;
  }

  function escText(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function etfNum(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return '-';
    return Number(value).toFixed(digits == null ? 1 : digits);
  }

  function scoreNum(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toFixed(1);
  }

  function signedPct(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return (num >= 0 ? '+' : '') + num.toFixed(2) + '%';
  }

  function pct(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toFixed(2) + '%';
  }

  function etfOverviewTone(state) {
    if (state === 'panic') return { bg: 'var(--cm-brand-50)', fg: 'var(--cm-brand-500)', label: '恐慌待托底' };
    if (state === 'cooling') return { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)', label: '降温观察期' };
    if (state === 'heated') return { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)', label: '兑现降温期' };
    return { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)', label: '趋势恢复期' };
  }

  function etfWatchTags(list, tone, actionMode) {
    if (!list || !list.length) return '<span class="muted">-</span>';
    return list.map(function (item) {
      var meta = tone || { bg: 'var(--cm-brand-50)', fg: 'var(--cm-brand-500)' };
      var extra = [];
      if (item.rotation_score != null) extra.push('轮动 ' + etfNum(item.rotation_score, 1));
      if (item.setup_state) extra.push(item.setup_state);
      if (item.strategy_type) extra.push(item.strategy_type);
      if (item.grid_step_pct != null) extra.push('步长 ' + etfNum(item.grid_step_pct, 1) + '%');
      var clickable = actionMode === 'analyze' && !!item.code;
      var attr = clickable ? ' data-etf-analyze="' + escText(item.code) + '"' : '';
      return '<span' + attr + ' style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:' + meta.bg + ';color:' + meta.fg + ';font-size:11px;font-weight:600;margin:4px 6px 0 0' + (clickable ? ';cursor:pointer' : '') + '">' +
        escText((item.name || item.code || '-') + (extra.length ? ' · ' + extra.join(' · ') : '')) +
        '</span>';
    }).join('');
  }

  function resolveDeps(deps) {
    deps = deps || {};
    return {
      esc: pickFn(deps, 'esc', escText),
      api: pickFn(deps, 'api', null),
      state: deps.state || null,
      deepPanelId: deps.deepPanelId || 'opportunityDeepPanel',
      loadEtfDeepAnalysis: pickFn(deps, 'loadEtfDeepAnalysis', null),
      showEtfTab: pickFn(deps, 'showEtfTab', null),
      etfSectorRotationWidget: deps.ETFSectorRotationWidget || global.ETFSectorRotationWidget || null,
    };
  }

  function bindOpportunityActionLinks(root, deps) {
    if (!root) return;
    var d = resolveDeps(deps);
    root.querySelectorAll('[data-etf-analyze]').forEach(function (node) {
      node.addEventListener('click', function () {
        if (d.loadEtfDeepAnalysis) d.loadEtfDeepAnalysis(node.dataset.etfAnalyze, d.deepPanelId);
      });
    });
    root.querySelectorAll('[data-etf-category]').forEach(function (node) {
      node.addEventListener('click', function () {
        if (d.state) {
          d.state.categoryFilter = node.dataset.etfCategory || 'all';
          d.state.strategyFilter = 'all';
        }
        if (d.showEtfTab) d.showEtfTab('list');
      });
    });
    root.querySelectorAll('[data-etf-strategy]').forEach(function (node) {
      node.addEventListener('click', function () {
        if (d.state) {
          d.state.strategyFilter = node.dataset.etfStrategy || 'all';
          d.state.categoryFilter = 'all';
        }
        if (d.showEtfTab) d.showEtfTab('list');
      });
    });
    root.querySelectorAll('[data-etf-tab]').forEach(function (node) {
      node.addEventListener('click', function () {
        if (d.showEtfTab) d.showEtfTab(node.dataset.etfTab);
      });
    });
  }

  function buildOpportunityMiningHtml(data, deps) {
    var d = resolveDeps(deps);
    var safeData = data || {};
    var gridCards = (safeData.grid_candidates || []).map(function (item) {
      return '<div style="padding:8px 10px;border-radius:10px;background:var(--cm-brand-100);color:var(--cm-brand-500);margin-bottom:6px;cursor:pointer" data-etf-analyze="' + escText(item.code) + '">' +
        '<div style="font-weight:700;font-size:12px">' + escText((item.name || item.code) + ' · 步长 ' + scoreNum(item.best_step_pct) + '%') + '</div>' +
        '<div style="font-size:11px;line-height:1.6;margin-top:3px">' + escText('收益 ' + signedPct(item.backtest_return_pct) + ' · 超额 ' + signedPct(item.backtest_excess_pct) + ' · DD ' + pct(item.backtest_max_drawdown_pct)) + ' <span style="opacity:0.6;font-size:10px">▶ 深度分析</span></div>' +
        '</div>';
    }).join('');
    var trendCards = (safeData.trend_candidates || []).map(function (item) {
      return '<div style="padding:8px 10px;border-radius:10px;background:var(--cm-ok-100);color:var(--cm-ok-500);margin-bottom:6px;cursor:pointer" data-etf-analyze="' + escText(item.code) + '">' +
        '<div style="font-weight:700;font-size:12px">' + escText((item.name || item.code) + ' · ' + (item.action || '观察')) + '</div>' +
        '<div style="font-size:11px;line-height:1.6;margin-top:3px">' + escText('4w ' + signedPct(item.relative_strength_4w) + ' / 12w ' + signedPct(item.relative_strength_12w) + ' · 因子 ' + etfNum(item.factor_score, 1)) + ' <span style="opacity:0.6;font-size:10px">▶ 深度分析</span></div>' +
        '</div>';
    }).join('');

    return '<div class="finance-module-grid">' +
      '<div class="finance-module-card">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px"><div style="font-weight:700;font-size:13px">网格交易 Top 5</div><span class="finance-note">仅保留自动寻优后跑赢持有的可执行网格</span></div>' +
      (gridCards || '<div class="muted" style="font-size:12px">暂无</div>') +
      '</div>' +
      '<div class="finance-module-card">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px"><div style="font-weight:700;font-size:13px">买入持有 Top 5</div><span class="finance-note">趋势占优时直接展示持有结论，不再暗示网格应当取胜</span></div>' +
      (trendCards || '<div class="muted" style="font-size:12px">暂无</div>') +
      '</div>' +
      '</div>';
  }

  function buildOpportunityHtml(data, deps) {
    var d = resolveDeps(deps);
    var safeData = data || {};
    var ov = safeData.overview || safeData || {};
    var tone = etfOverviewTone(ov.market_state);
    var leadersHtml = etfWatchTags(ov.rotation_leaders, { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' }, 'analyze');
    var laggardsHtml = etfWatchTags(ov.rotation_laggards, { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' }, 'analyze');

    function stratBtn(label, count, stratType, color) {
      return '<div class="stat-card" style="cursor:pointer" data-etf-strategy="' + escText(stratType) + '" title="点击查看">' +
        '<div class="stat-value" style="color:' + color + '">' + escText(String(count || 0)) + '</div>' +
        '<div class="stat-label">' + escText(label) + '</div></div>';
    }

    return '<div class="panel" style="margin-bottom:0">' +
      '<div class="panel-head" style="align-items:flex-start;gap:14px;flex-wrap:wrap">' +
      '<div style="min-width:280px;flex:1">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">' +
      '<span style="font-weight:700;font-size:15px">ETF 机会发现</span>' +
      '<span style="padding:4px 10px;border-radius:999px;background:' + tone.bg + ';color:' + tone.fg + ';font-size:12px;font-weight:700">' + escText(ov.regime_label || tone.label) + '</span>' +
      '<span class="muted">温度 ' + escText(etfNum(ov.temperature_score, 1)) + '</span>' +
      '</div>' +
      '<div style="font-size:13px;line-height:1.7;color:var(--cm-ink-700)">' + escText(ov.regime_reason || '暂无整体判断。') + '</div>' +
      '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-900)"><strong>当前动作：</strong>' + escText(ov.action_hint || '-') + '</div>' +
      '<div style="margin-top:6px;font-size:12px;color:var(--cm-ink-500)"><strong>低频情景：</strong>' + escText(ov.macro_scenario || '-') + '。' + escText(ov.macro_note || '') + '</div>' +
      '<div style="margin-top:10px;font-size:12px;color:var(--cm-ink-900)"><strong>轮动规则：</strong>' + escText(ov.rotation_rule || '-') + '</div>' +
      '<div style="margin-top:10px;font-size:12px;color:var(--cm-ink-900)"><strong>关注名单：</strong>' + leadersHtml + '</div>' +
      '<div style="margin-top:6px;font-size:12px;color:var(--cm-ink-900)"><strong>回避名单：</strong>' + laggardsHtml + '</div>' +
      '</div>' +
      '<div style="min-width:240px;flex:1">' +
      '<div class="stats-row" style="grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:10px">' +
      '<div class="stat-card"><div class="stat-value">' + escText(etfNum(ov.positive_20d_ratio, 0)) + '%</div><div class="stat-label">宽基上涨占比</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + escText(etfNum(ov.avg_momentum_20d, 1)) + '%</div><div class="stat-label">平均20日动量</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + escText(etfNum(ov.avg_momentum_60d, 1)) + '%</div><div class="stat-label">平均60日动量</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + escText(etfNum(ov.avg_volatility_20d, 1)) + '%</div><div class="stat-label">平均20日波动</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + escText(etfNum(ov.avg_drawdown_60d, 1)) + '%</div><div class="stat-label">平均60日回撤</div></div>' +
      '</div>' +
      '<div class="stats-row" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:0">' +
      stratBtn('买入持有', ov.strategy_counts?.trend, '买入持有', 'var(--cm-ok-500)') +
      stratBtn('网格交易', ov.strategy_counts?.grid, '网格交易', 'var(--cm-brand-500)') +
      stratBtn('防守停泊', ov.strategy_counts?.defensive, '防守停泊', 'var(--cm-warn-500)') +
      stratBtn('暂不参与', ov.strategy_counts?.avoid, '暂不参与', 'var(--cm-bad-500)') +
      '</div>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '<div class="panel" style="margin-bottom:14px">' +
      '<div class="panel-head"><span style="font-weight:600">功能入口</span></div>' +
      '<div style="display:flex;gap:12px;flex-wrap:wrap">' +
      '<div data-etf-tab="opportunity" style="flex:1;min-width:220px;padding:14px;border:1px solid var(--cm-ink-100);border-radius:12px;background:var(--cm-bg);cursor:pointer">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px"><span style="font-weight:700;color:var(--cm-ink-900)">机会发现</span><span style="padding:3px 8px;border-radius:999px;background:var(--cm-ink-100);color:var(--cm-ink-700);font-size:11px;font-weight:700">判断</span></div>' +
      '<div style="font-size:12px;line-height:1.6;color:var(--cm-ink-700)">查看市场判断、网格候选、买入持有和下一轮动观察。</div>' +
      '</div>' +
      '<div data-etf-tab="list" style="flex:1;min-width:220px;padding:14px;border:1px solid var(--cm-ink-100);border-radius:12px;background:var(--cm-bg);cursor:pointer">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px"><span style="font-weight:700;color:var(--cm-ink-900)">全量筛选</span><span style="padding:3px 8px;border-radius:999px;background:var(--cm-ink-100);color:var(--cm-ink-700);font-size:11px;font-weight:700">筛选</span></div>' +
      '<div style="font-size:12px;line-height:1.6;color:var(--cm-ink-700)">按分类、策略、动量与回测结果筛选 ETF，并进入单只深度分析。</div>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '<div id="opportunityMiningSection" style="margin-top:14px"></div>' +
      '<div id="opportunityRotationSection" style="margin-top:14px"></div>' +
      '<div id="opportunityDeepPanel"></div>';
  }

  async function loadOpportunityMining(deps) {
    var d = resolveDeps(deps);
    var miningBox = document.getElementById('opportunityMiningSection');
    var rotationBox = document.getElementById('opportunityRotationSection');
    if (!miningBox) return;
    miningBox.innerHTML = '<div class="muted" style="padding:10px">加载挖掘建议...</div>';
    if (rotationBox) rotationBox.innerHTML = '';

    var r = await d.api('/api/etf/mining?grid_topn=5&trend_topn=5&rotation_topn=5');
    if (r?.status !== 'ok' || !r?.data) {
      miningBox.innerHTML = '<div class="muted">挖掘建议加载失败</div>';
      return;
    }
    var dset = r.data || {};
    miningBox.innerHTML = buildOpportunityMiningHtml(dset, d);
    bindOpportunityActionLinks(miningBox, d);
    if (d.etfSectorRotationWidget && typeof d.etfSectorRotationWidget.mount === 'function') {
      d.etfSectorRotationWidget.mount('opportunityRotationSection', {
        limit: 15,
        onPickETF: function (code) {
          if (d.loadEtfDeepAnalysis) d.loadEtfDeepAnalysis(code, d.deepPanelId);
        }
      });
    }
  }

  function mountOpportunity(targetId, deps) {
    var d = resolveDeps(deps);
    var container = typeof targetId === 'string' ? document.getElementById(targetId) : targetId;
    var data = d.state && d.state.dataCache;
    if (!container) return;
    if (!data?.data?.length) return;
    container.innerHTML = buildOpportunityHtml(data, d);
    bindOpportunityActionLinks(container, d);
    loadOpportunityMining(d);
  }

  global.ETFOpportunityWidget = {
    etfOverviewTone: etfOverviewTone,
    etfWatchTags: etfWatchTags,
    buildOpportunityHtml: buildOpportunityHtml,
    buildOpportunityMiningHtml: buildOpportunityMiningHtml,
    mountOpportunity: mountOpportunity,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.ETFOpportunityWidget = global.ETFOpportunityWidget;
  }
})(typeof window !== 'undefined' ? window : this);
