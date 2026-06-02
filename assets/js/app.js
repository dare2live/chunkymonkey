/**
 * Chunky Monkey v2 — 前端主逻辑
 */
(function () {
  'use strict';
  if (!window.AppCache || !window.AppNav || !window.AppListState) {
    throw new Error('Frontend state modules failed to initialize');
  }

  var _initPromise = null;
  const BASE = location.origin;
  const SHORT_CACHE_TTL_MS = window.AppCache.SHORT_CACHE_TTL_MS;
  const INDUSTRY_OVERVIEW_SUMMARY_URL = '/api/screening/industry-overview?topn=4&summary=v2';
  var AppCache = window.AppCache;
  var AppNav = window.AppNav;
  var AppListState = window.AppListState;
  var StockListControlsWidget = window.StockListControlsWidget || null;
  var StockListRowsWidget = window.StockListRowsWidget || null;
  var StockSummaryWidget = window.StockSummaryWidget || null;
  var StockReportWidget = window.StockReportWidget || null;
  var ReturnsChartWidget = window.ReturnsChartWidget || null;
  var TypeSummaryWidget = window.TypeSummaryWidget || null;
  var InstitutionScorecardWidget = window.InstitutionScorecardWidget || null;
  var ETFListWidget = window.ETFListWidget || null;
  var ETFOpportunityWidget = window.ETFOpportunityWidget || null;
  var etfState = AppNav.getEtfState();
  var instListState = AppListState.inst;
  var stockListState = AppListState.stock;
  var industryViewState = AppListState.industry;

  // ─── Toast 通知 ───────────────────────────────────────────
  var _toastContainer = null;
  function showToast(msg, type) {
    if (!_toastContainer) {
      _toastContainer = document.createElement('div');
      _toastContainer.id = 'toast-container';
      _toastContainer.style.cssText = 'position:fixed;top:16px;right:16px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(_toastContainer);
    }
    var el = document.createElement('div');
    var bg = type === 'error' ? 'var(--stock-up)' : type === 'warn' ? 'var(--cm-warn-500)' : 'var(--stock-down)';
    el.style.cssText = 'padding:10px 18px;border-radius:8px;color:var(--cm-surface);font-size:13px;background:' + bg + ';opacity:0;transition:opacity .3s;pointer-events:auto;max-width:360px;box-shadow:0 4px 12px rgba(0,0,0,.15);';
    el.textContent = msg;
    _toastContainer.appendChild(el);
    requestAnimationFrame(function () { el.style.opacity = '1'; });
    setTimeout(function () {
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 350);
    }, 4000);
  }

  // ─── API 层（带重试 + 用户提示）────────────────────────────
  async function api(path, opts) {
    var maxRetries = 1;
    for (var attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        var r = await fetch(BASE + path, {
          cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          ...opts
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
      } catch (e) {
        if (attempt < maxRetries) {
          await new Promise(function (resolve) { setTimeout(resolve, 1000 * (attempt + 1)); });
          continue;
        }
        showToast('请求失败: ' + path.split('?')[0] + ' (' + e.message + ')', 'error');
        return null;
      }
    }
  }

  async function apiCached(path, ttlMs, opts) {
    return AppCache.apiCached(api, path, ttlMs, opts);
  }

  async function loadIndustryOverviewSummary() {
    var cached = await apiCached(INDUSTRY_OVERVIEW_SUMMARY_URL, SHORT_CACHE_TTL_MS).catch(function () { return null; });
    if (cached?.summary && Array.isArray(cached.summary.sector_focus)) return cached;
    return api(INDUSTRY_OVERVIEW_SUMMARY_URL);
  }

  // ============================================================
  // Navigation
  // ============================================================
  // ============================================================
  // Navigation — 两级：股东挖掘 / ETF研究
  // ============================================================
  function showGroup(name) {
    AppNav.setCurrentGroup(name);
    document.querySelectorAll('.nav-group-btn').forEach(b => b.classList.toggle('active', b.dataset.group === name));
    var subHolder = el('nav-sub-holder');
    var subEtf = el('nav-sub-etf');
    if (subHolder) subHolder.style.display = name === 'holder' ? '' : 'none';
    if (subEtf) subEtf.style.display = name === 'etf' ? '' : 'none';
    if (name === 'holder') {
      showView('workbench');
    } else if (name === 'etf') {
      showView('etf');
    }
  }

  function showView(name) {
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
    // 更新子导航按钮的 active 状态（仅更新当前板块的子导航）
    var subBar = AppNav.getCurrentGroup() === 'etf' ? el('nav-sub-etf') : el('nav-sub-holder');
    if (subBar) {
      subBar.querySelectorAll('.nav-btn').forEach(b => {
        if (name === 'etf') {
          // ETF 子导航 active 状态由 etftab 控制
          b.classList.toggle('active', b.dataset.etftab === etfState.currentTab);
        } else {
          b.classList.toggle('active', b.dataset.view === name);
        }
      });
    }
    // C6g: stocks 为主入口，signals-v2 tab 下线
    // P2-P5: 新增 data / strategy / settings 三个独立页, 通过 window.XxxView.show() 接入
    ({
      stocks: loadStocks,
      workbench: function () { window.WorkbenchView && window.WorkbenchView.show(); },
      dashboard: loadWorkbench,
      research: loadResearch,
      etf: loadEtf,
      'model-monitor': loadModelMonitor,
      data: function () { window.CMDataView && window.CMDataView.show(); },
      strategy: function () { window.StrategyView && window.StrategyView.show(); },
      settings: function () { window.SettingsView && window.SettingsView.show(); },
    })[name]?.();
  }

  function showWorkbenchTab(tab) {
    if (window.WorkbenchView && window.WorkbenchView.setTab) {
      window.WorkbenchView.setTab(tab);
    }
    showView('workbench');
  }

  function showEtfTab(tabName) {
    AppNav.setEtfTab(tabName);
    document.querySelectorAll('.etf-tab-content').forEach(c => c.classList.toggle('active', c.id === 'etftab-' + tabName));
    // Update ETF sub-nav active state
    var subEtf = el('nav-sub-etf');
    if (subEtf) {
      subEtf.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.etftab === tabName));
    }
    if (tabName === 'workbench') {
      loadEtfWorkbench();
      return;
    }
    // Lazy-load content for the tab (if cache missing, fetch first)
    if (!etfState.dataCache && (tabName === 'opportunity' || tabName === 'list')) {
      loadEtf();
      return;
    }
    if (tabName === 'opportunity') loadEtfOpportunity();
    if (tabName === 'list') loadEtfList();
  }

  // 顶层板块切换
  document.querySelectorAll('.nav-group-btn').forEach(b => b.addEventListener('click', () => showGroup(b.dataset.group)));

  // 子导航按钮
  document.querySelectorAll('.nav-sub-bar .nav-btn').forEach(b => {
    b.addEventListener('click', () => {
      if (b.dataset.etftab) {
        // ETF 子标签
        showView('etf');
        showEtfTab(b.dataset.etftab);
      } else {
        showView(b.dataset.view);
      }
    });
  });

  // Dashboard sub-tabs
  // 工作台不再有 tabs，排除规则和网络检测在页面加载时一起执行

  // Step 5d：机构页已收束为 scorecard + 研究说明，不再保留列表/管理子面板。
  // 批量管理挪至工作台的折叠区，首次展开时延迟加载。

  // Stock sub-tabs
  document.querySelectorAll('.stock-tabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.stock-tabs .tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.stab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      var t = document.getElementById('stab-' + btn.dataset.stab);
      if (t) t.classList.add('active');
      setStockSearchContext(btn.dataset.stab);
      loadActiveStockSubtab();
    });
  });

  // ============================================================
  // Health
  // ============================================================
  async function checkHealth() {
    var badge = el('statusBadge'), r = await api('/health');
    if (r && r.status === 'ok') {
      badge.textContent = 'Online'; badge.className = 'logo-status online';

      // Update dynamic modules nav（仅保留 ETF group 动态显示）
      var modules = r.enabled_modules || [];
      var navGroupEtf = el('nav-group-etf');
      if (navGroupEtf) navGroupEtf.style.display = modules.includes('etf') ? '' : 'none';
    }
    else { badge.textContent = 'Offline'; badge.className = 'logo-status'; }
  }

  // ============================================================
  // Workbench — 运维控制台（Step 5d 重塑）
  // 聚焦：健康带 + 数据管线 + 日志 + 高级折叠区
  // ============================================================

  // ── 股票视图（C6g 主入口）─────────────────────────────────────
  var _stocksLoaded = false;
  function loadStocks() {
    // TopK 条带由 stock-view 自己挂 TopKStripWidget, app.js 不重复注入
    if (window.StockView) {
      if (!_stocksLoaded) { _stocksLoaded = true; window.StockView.load(); }
      else { window.StockView.reload(); }
    }
  }

  async function loadWorkbench() {
    await refreshDashboardStatus(true, true);
    refreshWorkbenchHealthBar();
    _mountWorkbenchWidgets();
    var shortcut = el('btnUpdateAllShortcut');
    var mainBtn = el('btnUpdateAll');
    if (shortcut && mainBtn && !shortcut.dataset.bound) {
      shortcut.dataset.bound = '1';
      shortcut.addEventListener('click', function () { mainBtn.click(); });
    }
  }

  // 工作台 widget 懒挂载（只挂一次）
  var _widgetsMounted = false;
  function _mountWorkbenchWidgets() {
    if (_widgetsMounted) return;
    _widgetsMounted = true;
    // 信号参数 widget — 展开时挂载
    var paramsSection = document.getElementById('wb-signal-params-section');
    if (paramsSection) {
      paramsSection.addEventListener('toggle', function () {
        if (paramsSection.open && window.SignalParamsWidget) {
          window.SignalParamsWidget.mount('wb-signal-params-container');
        }
      }, { once: true });
    }
    // Cohort widget — 展开时挂载
    var cohortSection = document.getElementById('wb-cohort-section');
    if (cohortSection) {
      cohortSection.addEventListener('toggle', function () {
        if (cohortSection.open && window.CohortCardWidget) {
          window.CohortCardWidget.mount('wb-cohort-container');
        }
      }, { once: true });
    }
    // 回测 widget — 展开时挂载
    var btSection = document.getElementById('wb-backtest-section');
    if (btSection) {
      btSection.addEventListener('toggle', function () {
        if (btSection.open && window.BacktestPanelWidget) {
          window.BacktestPanelWidget.mount('wb-backtest-container');
        }
      }, { once: true });
    }
    // 选股扫描 widget — 展开时挂载（Phase 2）
    var screenSection = document.getElementById('wb-screening-section');
    if (screenSection) {
      screenSection.addEventListener('toggle', function () {
        if (screenSection.open && window.ScreeningPanelWidget) {
          window.ScreeningPanelWidget.mount('wb-screening-container');
        }
      }, { once: true });
    }
    // ETF 网格自寻优 widget — 展开时挂载
    var gridOptSection = document.getElementById('wb-grid-opt-section');
    if (gridOptSection) {
      gridOptSection.addEventListener('toggle', function () {
        if (gridOptSection.open && window.GridOptimizerWidget) {
          window.GridOptimizerWidget.mount('wb-grid-opt-container');
        }
      }, { once: true });
    }
  }

  async function refreshWorkbenchHealthBar() {
    // 并行拉 5 个数据源拼装 5 张健康卡
    try {
      var [status, inst, evStats, up, sig] = await Promise.all([
        api('/api/inst/market/status').catch(() => null),
        api('/api/inst/institutions').catch(() => null),
        api('/api/signals/events/stats').catch(() => null),
        api('/api/inst/update/status').catch(() => null),
        api('/api/signals/today?freshness_days=90&limit=2000').catch(() => null),
      ]);
      renderHealthFreshness(status);
      renderHealthEvents(evStats, status);
      renderHealthInst(inst);
      renderHealthSignals(sig);
      renderHealthPipeline(up);
    } catch (e) { /* 不阻塞 */ }
  }

  function setText(id, text) { var n = el(id); if (n) n.textContent = text == null ? '—' : String(text); }
  function setHtml(id, html) { var n = el(id); if (n) n.innerHTML = html || ''; }
  function setChip(id, text, tone) {
    var n = el(id); if (!n) return;
    n.textContent = text || '';
    n.classList.remove('chip-ok', 'chip-warn', 'chip-bad');
    if (tone) n.classList.add('chip-' + tone);
  }
  function daysAgoFromYmd(ymd) {
    if (!ymd) return null;
    var s = String(ymd).replace(/[^0-9]/g, '').slice(0, 8);
    if (s.length !== 8) return null;
    var dt = new Date(+s.slice(0,4), +s.slice(4,6)-1, +s.slice(6,8));
    return Math.floor((Date.now() - dt.getTime()) / 86400000);
  }

  function renderHealthFreshness(status) {
    if (!status) { setText('wbFreshDate', '—'); return; }
    setText('wbFreshDate', fmtDate(status.latest_notice_date));
    var days = daysAgoFromYmd(status.latest_notice_date);
    setText('wbFreshAge', days == null ? '' : '距今 ' + days + ' 天');
    if (days == null) setChip('wbFreshChip', '');
    else if (days <= 3) setChip('wbFreshChip', '新鲜', 'ok');
    else if (days <= 7) setChip('wbFreshChip', '稍旧', 'warn');
    else setChip('wbFreshChip', '已过时', 'bad');
  }

  function renderHealthEvents(evStats, status) {
    // 任务 3：事件成熟卡直接显示 /api/signals/events/stats 的真实数字
    // buy_total 为所有 new_entry/increase 事件，buy_matured 为其中 gain_60d 已观察到的
    if (!evStats) {
      var fallback = (status?.total_records) || 0;
      setText('wbEvMatured', fmt(fallback));
      setText('wbEvTotal', '事件总数');
      setText('wbEvPct', '');
      return;
    }
    var total = evStats.buy_total || 0;
    var matured = evStats.buy_matured || 0;
    var pct = total ? Math.round(matured / total * 100) : 0;
    setText('wbEvMatured', fmt(matured));
    setText('wbEvTotal', '成熟 / 总 ' + fmt(total));
    setText('wbEvPct', '成熟率 ' + pct + '%');
  }

  function renderHealthInst(inst) {
    var arr = (inst && inst.data) ? inst.data : [];
    var summary = TypeSummaryWidget && TypeSummaryWidget.collectEnabledTypeSummary
      ? TypeSummaryWidget.collectEnabledTypeSummary(arr, 4)
      : null;
    var active = summary ? summary.active : arr.filter(function (i) { return i.enabled && !i.blacklisted; }).length;
    var total = summary ? summary.total : arr.length;
    setText('wbInstActive', fmt(active));
    setText('wbInstTotal', '总 ' + fmt(total) + ' · 黑名单 ' + fmt(total - active));
    // 展示全部类别，按数量降序；卡片位置有限，类别数多时展示 Top 4 + "+N类"
    var chipLabel = summary ? summary.label : '';
    var wbInstTypesEl = el('wbInstTypes');
    if (wbInstTypesEl) {
      wbInstTypesEl.textContent = chipLabel;
      wbInstTypesEl.title = summary ? summary.title : '';
    }
  }

  function renderHealthSignals(sig) {
    if (!sig) { setText('wbSigFollow', '—'); return; }
    var by = (sig.summary && sig.summary.by_action) || {};
    setText('wbSigFollow', fmt(by.follow || 0) + ' 可跟');
    setText('wbSigWatchSkip', fmt(by.watch || 0) + ' 观察 · ' + fmt(by.skip || 0) + ' 不跟');
  }

  function renderHealthPipeline(up) {
    if (!up) { setText('wbPipeStatus', '—'); return; }
    var running = up.running === true;
    var summary = up.summary || {};
    var counts = summary.counts || {};
    var failedCount = counts.failed || 0;
    var completedCount = counts.completed || 0;
    var skippedCount = counts.skipped || 0;
    var latestAt = counts.latest_at || summary.latest_status_at || null;
    if (running) {
      setText('wbPipeStatus', '运行中');
      setChip('wbPipeChip', '进行中', 'warn');
    } else if (failedCount > 0) {
      setText('wbPipeStatus', failedCount + ' 步失败');
      setChip('wbPipeChip', '需排查', 'bad');
    } else {
      setText('wbPipeStatus', '就绪');
      setChip('wbPipeChip', (completedCount + '/' + (completedCount + skippedCount + failedCount)) + ' 步', 'ok');
    }
    if (latestAt) {
      var d = latestAt.slice(0, 16).replace('T', ' ');
      setText('wbPipeLast', '上次运行 ' + d);
    } else {
      setText('wbPipeLast', '');
    }
  }

  function topCountEntries(counts, limit) {
    var map = counts || {};
    var keys = Object.keys(map).sort(function (left, right) {
      var diff = (map[right] || 0) - (map[left] || 0);
      return diff || String(left).localeCompare(String(right), 'zh-CN');
    });
    var cap = Number(limit) > 0 ? Number(limit) : keys.length;
    return keys.slice(0, cap).map(function (key) {
      return { name: key, count: map[key] };
    });
  }

  // Step 5d 补齐：HEAD 里 resolveStockSummary 调用点有 3 处但从未定义，导致
  // renderStockResearchSummary 在 loadStockView 中一直静默抛 ReferenceError。
  // 本函数合并「后端 summary」与「本地算出的 summary」两种来源：
  //   - 后端 stockSummary 非空：优先用（缺字段回退到本地）
  //   - 后端 stockSummary 为空：完全用本地计算
  function resolveStockSummary(stocks, stockSummary) {
    if (StockSummaryWidget && StockSummaryWidget.mergeStockSummary) {
      return StockSummaryWidget.mergeStockSummary(stocks || [], stockSummary || null, {
        stockGateInfo: stockGateInfo,
        stockSourceName: stockSourceName,
      });
    }
    var local = {
      total: (stocks || []).length,
      abTotal: 0,
      followTotal: 0,
      dualConfirm: 0,
      setupTotal: 0,
      pools: {},
      gates: { follow: 0, watch: 0, observe: 0, avoid: 0 },
      signals: {},
      industries: {},
      sources: {},
      attentionCovered: 0,
      attentionBoosted: 0,
      attentionCrowded: 0,
      attentionSignals: {},
      turtleCovered: 0,
      turtleBreakout: 0,
      turtleWatch: 0,
      turtleExit: 0,
      topIndustries: [],
      topSignals: [],
      topSources: [],
      topAttentionSignals: []
    };
    (stocks || []).forEach(function (s) {
      var pool = s.priority_pool || '未分池';
      var gate = (stockGateInfo && stockGateInfo(s) || {}).key || '';
      var source = (typeof stockSourceName === 'function') ? stockSourceName(s) : '';
      var industry = s.setup_industry_name || s.tdx_l2 || s.tdx_l1 || '';
      local.pools[pool] = (local.pools[pool] || 0) + 1;
      if (pool === 'A池' || pool === 'B池') local.abTotal += 1;
      if (gate && local.gates[gate] != null) local.gates[gate] += 1;
      if (gate === 'follow') local.followTotal += 1;
      if (s.setup_tag) {
        local.setupTotal += 1;
        var signalKey = 'A' + (s.setup_priority != null ? s.setup_priority : '?');
        local.signals[signalKey] = (local.signals[signalKey] || 0) + 1;
      }
      if (industry) local.industries[industry] = (local.industries[industry] || 0) + 1;
      if (source && source !== '-') local.sources[source] = (local.sources[source] || 0) + 1;
      if (s._dual_confirm) local.dualConfirm += 1;
    });
    local.topIndustries = topCountEntries(local.industries, 4);
    local.topSignals = topCountEntries(local.signals, 4);
    local.topSources = topCountEntries(local.sources, 3);
    if (!stockSummary || typeof stockSummary !== 'object') return local;
    return Object.assign({}, local, stockSummary, {
      pools: Object.assign({}, local.pools, stockSummary.pools || {}),
      gates: Object.assign({}, local.gates, stockSummary.gates || {}),
      signals: Object.assign({}, local.signals, stockSummary.signals || {}),
      industries: Object.assign({}, local.industries, stockSummary.industries || {}),
      sources: Object.assign({}, local.sources, stockSummary.sources || {}),
      attentionSignals: Object.assign({}, local.attentionSignals, stockSummary.attentionSignals || {}),
      topIndustries: stockSummary.topIndustries || local.topIndustries,
      topSignals: stockSummary.topSignals || local.topSignals,
      topSources: stockSummary.topSources || local.topSources,
      topAttentionSignals: stockSummary.topAttentionSignals || local.topAttentionSignals,
    });
  }

  function summaryChip(label, count, tone) {
    return '<span class="summary-chip summary-chip--' + tone + '">' + esc(label) + ' ' + fmt(count) + '</span>';
  }

  function hasAttentionCoverage(s) {
    return !!(s && (
      s.external_attention_signal ||
      s.external_attention_score != null ||
      s.attention_focus_index != null ||
      s.attention_comment_trade_date ||
      s.attention_composite_score != null
    ));
  }

  function hasTurtleCoverage(s) {
    return !!(s && (
      s.turtle_setup_state ||
      s.turtle_execution_score != null ||
      s.turtle_breakout_score != null ||
      s.turtle_risk_score != null ||
      s.turtle_score_delta != null ||
      s.turtle_reason ||
      s.turtle_preferred_system
    ));
  }

  function turtleSystemLabel(system) {
    if (system === 'S1') return 'S1 系统';
    if (system === 'S2') return 'S2 系统';
    if (system === '观察') return '观察';
    return system || '-';
  }

  function turtleStateMeta(state) {
    return {
      'S2突破触发': { label: 'S2突破触发', shortLabel: 'S2突破', tone: 'good', group: 'breakout' },
      'S1突破触发': { label: 'S1突破触发', shortLabel: 'S1突破', tone: 'good', group: 'breakout' },
      'S2待突破': { label: 'S2待突破', shortLabel: 'S2待突', tone: 'accent', group: 'watch' },
      'S1待突破': { label: 'S1待突破', shortLabel: 'S1待突', tone: 'accent', group: 'watch' },
      '20日退出触发': { label: '20日退出触发', shortLabel: '20日退出', tone: 'warn', group: 'exit' },
      '10日退出触发': { label: '10日退出触发', shortLabel: '10日退出', tone: 'warn', group: 'exit' },
      '等待形态': { label: '等待形态', shortLabel: '等待形态', tone: 'neutral', group: 'waiting' }
    }[state || ''] || (state ? { label: String(state), shortLabel: String(state), tone: 'neutral', group: 'covered' } : null);
  }

  function turtleStateTag(state, useShortLabel) {
    var meta = turtleStateMeta(state);
    if (!meta) return '';
    return '<span class="stock-attention-pill stock-attention-pill--' + meta.tone + '">' + esc(useShortLabel ? (meta.shortLabel || meta.label) : meta.label) + '</span>';
  }

  function attentionSummaryTone(signal) {
    var meta = attentionSignalMeta(signal);
    if (!meta) return 'neutral';
    return meta.tone === 'warn' ? 'warning' : 'attention';
  }


  function stockScoreValue(s, dimension) {
    if (!s) return null;
    if (dimension === 'discovery') return s.discovery_score;
    if (dimension === 'quality') return s.company_quality_score;
    if (dimension === 'stage') return s.stage_score;
    return null;
  }

  function stockScoreBandMeta(dimension, rawScore) {
    if (rawScore == null || rawScore === '') return { key: 'missing', label: '缺失', tone: 'neutral' };
    var score = Number(rawScore);
    if (Number.isNaN(score)) return { key: 'missing', label: '缺失', tone: 'neutral' };
    var strong = 75;
    var mid = 60;
    var watch = 45;
    if (dimension === 'stage') {
      strong = 70;
      mid = 55;
      watch = 40;
    }
    if (score >= strong) return { key: 'strong', label: '强', tone: 'good' };
    if (score >= mid) return { key: 'mid', label: '中', tone: 'accent' };
    if (score >= watch) return { key: 'watch', label: '观察', tone: 'warn' };
    return { key: 'weak', label: '弱', tone: 'neutral' };
  }

  function stockScoreSubtext(s, dimension) {
    if (!s) return '';
    if (dimension === 'discovery') return s.display_inst_name || s.setup_inst_name || preferredIndustryLabel(s) || '机构发现';
    if (dimension === 'quality') return s.stock_archetype || '公司质量';
    if (dimension === 'stage') return s.path_state || s.stage_reason || '阶段过滤';
    return '';
  }

  function screeningHitCount(screen) {
    if (!screen) return 0;
    if (screen.hit_count != null) return Number(screen.hit_count || 0);
    return (screen.f1_hit ? 1 : 0) + (screen.f3_hit ? 1 : 0) + (screen.f5_hit ? 1 : 0);
  }

  function stockScreeningInline(s) {
    var screen = s && s._screen;
    if (!screen) return '<span class="muted">待筛选</span>';
    var tags = [];
    if (screen.f1_hit) tags.push('<span class="tag tag-sm" style="background:var(--cm-brand-400);color:var(--cm-surface)" title="MA突破">F1</span>');
    if (screen.f3_hit) tags.push('<span class="tag tag-sm" style="background:var(--cm-accent-vivid);color:var(--cm-surface)" title="趋势跟踪">F3</span>');
    if (screen.f5_hit) tags.push('<span class="tag tag-sm" style="background:var(--cm-warn-500);color:var(--cm-surface)" title="MACD金叉">F5</span>');
    if (!tags.length) return '<span class="muted">未命中</span>';
    return '<div style="display:flex;flex-direction:column;gap:4px;align-items:flex-start">' +
      '<div style="display:flex;flex-wrap:wrap;gap:4px">' + tags.join('') + '</div>' +
      '<div class="muted" style="font-size:11px">' + screeningHitCount(screen) + ' 式命中</div>' +
      '</div>';
  }

  function stockSortRows(stocks) {
    if (StockListControlsWidget && StockListControlsWidget.sortStockRows) {
      return StockListControlsWidget.sortStockRows(stocks || [], stockListState.getSortMode());
    }
    var mode = stockListState.getSortMode();
    return (stocks || []).slice().sort(function (left, right) {
      var diff = 0;
      if (mode === 'notice') {
        diff = String(right.latest_notice_date || '').localeCompare(String(left.latest_notice_date || ''));
      } else {
        diff = Number(right.composite_priority_score || -1) - Number(left.composite_priority_score || -1);
      }
      if (!diff) diff = Number(right.discovery_score || -1) - Number(left.discovery_score || -1);
      if (!diff) diff = String(left.stock_code || '').localeCompare(String(right.stock_code || ''), 'zh-CN');
      return diff;
    });
  }

  function summaryRow(label, count, tone, total) {
    var pct = total > 0 ? Math.max(8, Math.round((count / total) * 100)) : 0;
    return '<div class="summary-row">' +
      '<div class="summary-row-main"><span class="summary-dot summary-dot--' + tone + '"></span><span>' + esc(label) + '</span></div>' +
      '<div class="summary-row-value">' + fmt(count) + '</div>' +
      '<div class="summary-row-track"><span style="width:' + pct + '%"></span></div>' +
      '</div>';
  }

  function heroMetricCard(label, value, sub) {
    return '<div class="hero-metric-card">' +
      '<span class="hero-metric-label">' + esc(label) + '</span>' +
      '<strong>' + esc(String(value == null ? '-' : value)) + '</strong>' +
      '<small>' + esc(sub || '-') + '</small>' +
      '</div>';
  }

  // renderDashboardOverview 已随 Step 5d 工作台重塑移除。
  // 候选池/评分框架从「工作台」撤下，主入口统一为信号 v2。

  function stockReportDeps() {
    return {
      esc: esc,
      fmt: fmt,
      fmtDate: fmtDate,
      fmtDateTime: fmtDateTime,
      fmtGain: fmtGain,
      pct: pct,
      compactNum: compactNum,
      fmtCurrency: fmtCurrency,
      scoreNum: scoreNum,
      signedScore: signedScore,
      signedPct: signedPct,
      signedCountText: signedCountText,
      daysFromDateDigits: daysFromDateDigits,
      daysBetweenDates: daysBetweenDates,
      resolveStockSummary: resolveStockSummary,
      stockGateInfo: stockGateInfo,
      stockGateTag: stockGateTag,
      stockSignalNarrative: stockSignalNarrative,
      stockSignalHeadline: stockSignalHeadline,
      stockSourceName: stockSourceName,
      preferredIndustryLabel: preferredIndustryLabel,
      turtleSystemLabel: turtleSystemLabel,
      turtleStateTag: turtleStateTag,
      setupEventText: setupEventText,
      followGateTag: followGateTag,
      costMethodText: costMethodText,
      instLink: instLink,
      evTag: evTag,
      attentionSignalTag: attentionSignalTag,
      attentionSignalMeta: attentionSignalMeta,
    };
  }

  function renderStockResearchSummary(stocks, sectorSummary, stockSummary) {
    if (!StockReportWidget || !StockReportWidget.renderStockResearchSummary) {
      throw new Error('StockReportWidget failed to initialize');
    }
    return StockReportWidget.renderStockResearchSummary(stocks, sectorSummary, stockSummary, stockReportDeps());
  }

  function renderStockInstitutionCoverageSection(base, institutions) {
    if (!StockReportWidget || !StockReportWidget.renderStockInstitutionCoverageSection) {
      throw new Error('StockReportWidget failed to initialize');
    }
    return StockReportWidget.renderStockInstitutionCoverageSection(base, institutions, stockReportDeps());
  }

  function renderStockReportHero(base, attention) {
    if (!StockReportWidget || !StockReportWidget.renderStockReportHero) {
      throw new Error('StockReportWidget failed to initialize');
    }
    return StockReportWidget.renderStockReportHero(base, attention, stockReportDeps());
  }

  function renderStockEvidenceTimeline(base) {
    if (!StockReportWidget || !StockReportWidget.renderStockEvidenceTimeline) {
      throw new Error('StockReportWidget failed to initialize');
    }
    return StockReportWidget.renderStockEvidenceTimeline(base, stockReportDeps());
  }

  function renderStockDetailCardGrid(base) {
    if (!StockReportWidget || !StockReportWidget.renderStockDetailCardGrid) {
      throw new Error('StockReportWidget failed to initialize');
    }
    return StockReportWidget.renderStockDetailCardGrid(base, stockReportDeps());
  }

  function priorityPoolTag(pool) {
    var meta = {
      'A池': { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' },
      'B池': { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' },
      'C池': { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' },
      'D池': { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' }
    }[pool || ''] || { bg: 'var(--cm-ink-50)', fg: 'var(--cm-ink-500)' };
    return '<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:' + meta.bg + ';color:' + meta.fg + ';font-size:11px;font-weight:700">' + esc(pool || '未分池') + '</span>';
  }
  function instLink(id, name, type) { return '<span class="type-tag clickable-name" data-type="' + esc(type || 'other') + '" onclick="App.toggleInstDetail(\'' + esc(id) + '\',this)" style="cursor:pointer;font-size:11px">' + esc(name || '') + '</span>' }
  function typeTag(type, label) { return '<span class="type-tag" data-type="' + esc(type || 'other') + '">' + (label || esc(type || 'other')) + '</span>' }
  function evTag(type, label) { var cls = { new_entry: 'new', increase: 'up', decrease: 'down', exit: 'exit', unchanged: 'unchanged' }[type] || 'unchanged'; return '<span class="event-tag event-' + (cls) + '">' + esc(label || { new_entry: '新进', increase: '增持', decrease: '减持', exit: '退出', unchanged: '不变' }[type] || type) + '</span>' }

  // ============================================================
  // Network Connectivity Check
  // ============================================================
  function normalizeSourceName(detail, fallback) {
    if (!detail) return fallback;
    var s = String(detail).trim();
    // 抽取实际源名，去掉 HTTP 状态码 / IP / 端口 / 括号尾注
    if (/^HTTP\s*\d+$/i.test(s)) return fallback;
    s = s.split(/[\s(（]/)[0];                 // 去掉括号说明
    s = s.replace(/_[\d.]+:\d+$/, '');         // tdxhub_218.6.170.47:7709 → tdxhub
    s = s.replace(/:\d+$/, '');                // host:port → host
    // 项目自维护 tdxhub 源统一展示为 tdxhub（底层 python 包名仍为 tdxhub）
    if (/^tdxhub$/i.test(s)) s = 'tdxhub';
    return s || fallback;
  }
  function setSourcePill(id, name, online, detail, pending) {
    var pill = el(id);
    if (!pill) return;
    pill.classList.remove('online', 'offline', 'pending');
    pill.classList.add(pending ? 'pending' : (online ? 'online' : 'offline'));
    var textEl = pill.querySelector('.source-pill-text');
    if (!textEl) return;
    var statusText = pending ? '检测中' : (online ? '在线' : '离线');
    var text = name + ' · ' + statusText;
    if (!pending && online && detail) text += ' · ' + detail;
    textEl.textContent = text;
  }
  function primeNetworkPills() {
    setSourcePill('sourcePillHoldings', '股东源', false, '', false);
    setSourcePill('sourcePillKline', 'K线源', false, '', false);
    setSourcePill('sourcePillIndustry', '行业源', false, '', false);
  }
  async function checkNetwork() {
    setSourcePill('sourcePillHoldings', '股东源', false, '', true);
    setSourcePill('sourcePillKline', 'K线源', false, '', true);
    setSourcePill('sourcePillIndustry', '行业源', false, '', true);
    var r = await apiCached('/api/inst/update/connectivity', 5 * 60 * 1000);
    if (!r) {
      primeNetworkPills();
      return;
    }
    setSourcePill('sourcePillHoldings', '股东源', !!r.holdings_source,
      normalizeSourceName(r.holdings_source_detail, 'akshare'), false);
    setSourcePill('sourcePillKline', 'K线源', !!r.kline_source,
      normalizeSourceName(r.kline_source_detail, 'tdxhub'), false);
    setSourcePill('sourcePillIndustry', '行业源', !!r.industry_source,
      normalizeSourceName(r.industry_source_detail, 'tdxhub'), false);
  }

  // 手动强制刷新连通性（绕过前后端 5 分钟缓存）
  async function refreshNetwork() {
    setSourcePill('sourcePillHoldings', '股东源', false, '', true);
    setSourcePill('sourcePillKline', 'K线源', false, '', true);
    setSourcePill('sourcePillIndustry', '行业源', false, '', true);
    try {
      // 清前端 apiCached 缓存
      if (window.App && typeof window.App._api === 'function') {
        // 直接 fetch force=1 绕过后端缓存
        const r = await fetch('/api/inst/update/connectivity?force=1').then(x => x.json());
        setSourcePill('sourcePillHoldings', '股东源', !!r.holdings_source,
          normalizeSourceName(r.holdings_source_detail, 'akshare'), false);
        setSourcePill('sourcePillKline', 'K线源', !!r.kline_source,
          normalizeSourceName(r.kline_source_detail, 'tdxhub'), false);
        setSourcePill('sourcePillIndustry', '行业源', !!r.industry_source,
          normalizeSourceName(r.industry_source_detail, 'tdxhub'), false);
      }
    } catch (e) {
      primeNetworkPills();
    }
  }

  // ============================================================
  // Scorecard
  // ============================================================
  function loadInstScorecard() {
    if (InstitutionScorecardWidget && typeof InstitutionScorecardWidget.mountScorecard === 'function') {
      return InstitutionScorecardWidget.mountScorecard({
        frameworkId: 'instScorecardFramework',
        statsId: 'instScorecardStats',
        paramsId: 'instScorecardParams',
      }, {
        api: api,
        esc: esc,
        fmt: fmt,
        fmtGain: fmtGain,
        scoreNum: scoreNum,
        priorityPoolTag: priorityPoolTag,
      });
    }
    setHtml('instScorecardFramework', '<div class="score-rule-card"><div class="score-rule-title">机构评分双框架</div><div class="scorecard-note">机构评分卡暂不可用</div></div>');
    setHtml('instScorecardStats', '');
    setHtml('instScorecardParams', '');
  }

  function loadResearch() {
    loadInstScorecard();
  }

  // Step 5 任务 4：scorecard 入口现在只负责挂载机构评分卡 widget，不再保留旧的股票/机构评分卡双入口。

  // ============================================================
  // Init
  // ============================================================
  async function init() {
    renderIdleStepGrid();
    await checkHealth(); // ensure it awaits first to initialize module toggles
    showView('workbench');
    el('btnUpdateAll').addEventListener('click', startUpdate);
    el('btnRefreshStatus')?.addEventListener('click', refreshWorkbenchStatus);
    el('btnStop').addEventListener('click', async () => {
      el('btnStop').disabled = true;
      el('btnStop').textContent = '停止中...';
      await api('/api/inst/update/stop', { method: 'POST' });
      addLog('已请求停止');
    });
    el('btnClearLog')?.addEventListener('click', () => el('updateLog').innerHTML = '');
    el('btnCopyLog')?.addEventListener('click', copyLogs);
    // Step 5d：管线折叠改由 <details> 原生处理，系统操作面板删除。
    el('btnReset')?.addEventListener('click', resetDerivedData);
    el('stockSearch')?.addEventListener('input', handleStockSearchInput);
    el('btnLifeboat')?.addEventListener('click', runLifeboat);
    el('btnEtfSync')?.addEventListener('click', async function () {
      var btn = this;
      btn.disabled = true; btn.textContent = '同步中...';
      el('etfMsg').textContent = '正在启动 ETF 同步任务...';
      var r = await api('/api/etf/sync', { method: 'POST' });
      if (!r || r.status !== 'ok') {
        btn.disabled = false; btn.textContent = '同步 ETF 数据';
        el('etfMsg').textContent = '启动失败: ' + (r?.message || '未知错误');
        return;
      }
      // 启动后台轮询，实时显示进度与日志
      pollEtfStatus(btn);
    });

    function pollEtfStatus(btn) {
      var msgEl = el('etfMsg');
      var box = el('etfLogBox');
      var poll = setInterval(async function () {
        var s = await api('/api/etf/status');
        var d = s && s.data;
        if (!d) return;
        var pct = (d.total > 0) ? Math.round((d.current || 0) / d.total * 100) : 0;
        var progressTxt = d.total > 0 ? (d.current + ' / ' + d.total + '  (' + pct + '%)') : '';
        var stageLabel = ({
          'idle': '等待', 'starting': '启动中', 'fetch_list': '拉取 ETF 列表',
          'write_universe': '写入资产池', 'sync_kline': '同步 K 线', 'build_snapshot': '重建最新快照',
          'done': '完成', 'error': '失败'
        })[d.stage] || d.stage;
        var listSource = d.result && d.result.list_source ? d.result.list_source : '';
        var klineBreakdown = d.result && Array.isArray(d.result.kline_source_breakdown)
          ? d.result.kline_source_breakdown.map(function (item) {
              return (item.source || '未知') + ' · ' + fmt(item.count || 0);
            }).join(' / ')
          : '';
        var extraSummary = '';
        if (!d.running && d.result) {
          extraSummary = '<div style="margin-top:8px;font-size:12px;line-height:1.7;color:var(--cm-ink-700)">' +
            '<div><strong>资产池来源：</strong>' + esc(listSource || '暂无') + '</div>' +
            '<div><strong>K线来源分布：</strong>' + esc(klineBreakdown || '暂无') + '</div>' +
            '<div><strong>快照：</strong>' + esc(d.result.snapshot_id || '-') + ' · <strong>覆盖ETF：</strong>' + esc(fmt(d.result.etf_count || 0)) + ' · <strong>日线ETF：</strong>' + esc(fmt(d.result.kline_etf_count || 0)) + '</div>' +
            '</div>';
        }
        if (msgEl) {
          msgEl.innerHTML = '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
            '<span style="font-weight:600">' + esc(stageLabel) + '</span>' +
            (progressTxt ? '<span class="muted">' + esc(progressTxt) + '</span>' : '') +
            '<span>' + esc(d.message || '') + '</span>' +
            '</div>' +
            (d.total > 0 ? '<div style="margin-top:6px;height:6px;background:var(--cm-ink-100);border-radius:3px;overflow:hidden">' +
              '<div style="height:100%;width:' + pct + '%;background:' + (d.stage === 'error' ? 'var(--stock-up)' : 'var(--cm-brand-400)') + ';transition:width .3s"></div></div>' : '') +
            extraSummary;
        }
        if (box && d.logs && d.logs.length) {
          box.innerHTML = d.logs.slice(-30).map(function (line) {
            var color = line.level === 'error' ? 'var(--stock-up)' : (line.level === 'warning' ? 'var(--cm-warn-500)' : 'var(--cm-ink-700)');
            return '<div style="color:' + color + '">[' + (line.ts || '').slice(11, 19) + '] ' + esc(line.message) + '</div>';
          }).join('');
          box.scrollTop = box.scrollHeight;
        }
        if (!d.running) {
          clearInterval(poll);
          if (btn) { btn.disabled = false; btn.textContent = '同步 ETF 数据'; }
          if (d.stage === 'done') {
            loadEtf();
          }
        }
      }, 1500);
    }
    setInterval(checkHealth, 30000);
  }

  function startInit() {
    if (_initPromise) return _initPromise;
    _initPromise = init().catch(function (error) {
      console.error('App init failed', error);
      throw error;
    });
    return _initPromise;
  }

  // ============================================================
  // ETF 研究板块
  // ============================================================
  // 通用 ETF helper 函数
  function etfNum(v, digits) {
    if (v == null || Number.isNaN(Number(v))) return '-';
    return Number(v).toFixed(digits == null ? 1 : digits);
  }
  // 主入口：加载 ETF 数据并分别渲染各子标签
  async function loadEtf(forceRefresh) {
    if (etfState.currentTab === 'workbench') return loadEtfWorkbench(forceRefresh);

    var path = '/api/etf/list';
    if (forceRefresh) path += '?force_refresh=true';
    var r = await api(path);
    if (!r?.data?.length) {
      etfState.dataCache = null;
      var opportunityBox = el('etfOpportunityContainer');
      if (opportunityBox) opportunityBox.innerHTML = '';
      var c = el('etfTableContainer');
      if (c) c.innerHTML = '<div class="muted" style="padding:20px;text-align:center">暂无 ETF 数据，请点击上方同步按钮。</div>';
      return;
    }
    etfState.dataCache = r;
    // 渲染当前活跃子标签
    if (etfState.currentTab === 'opportunity') loadEtfOpportunity();
    if (etfState.currentTab === 'list') loadEtfList();
  }

  async function loadEtfWorkbench(forceRefresh) {
    var box = el('etfWorkbenchContainer');
    if (!box) return;
    box.innerHTML = '<div class="panel"><div class="muted" style="padding:28px;text-align:center">加载 ETF 工作台...</div></div>';

    var path = '/api/etf/workbench';
    if (forceRefresh) path += '?force_refresh=true';
    var r = await api(path);
    if (r?.status !== 'ok' || !r?.data) {
      box.innerHTML = '<div class="panel"><div class="muted" style="padding:28px;text-align:center">工作台加载失败: ' + esc(r?.message || '未知错误') + '</div></div>';
      return;
    }

    var d = r.data || {};
    var snapshot = d.snapshot || {};
    var source = d.source_status || {};
    var overview = d.overview || {};
    var syncState = d.sync_state || {};
    var connectivity = source.connectivity || {};
    var syncTone = syncState.running ? { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)', label: '同步进行中' } : { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)', label: '快照已就绪' };
    var staleNote = snapshot.is_stale
      ? '当前展示的是最近一次缓存结果，快照早于最近一次行情同步。'
      : '页面默认直接展示最近一次快照，不会因为刷新页面再次全量回测。';
    var sourceBreakdown = (source.source_breakdown || []).map(function (item) {
      return '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-brand-50);color:var(--cm-brand-500);font-size:11px;font-weight:600">' + esc((item.source || '未知') + ' · ' + fmt(item.count || 0)) + '</span>';
    }).join('') || '<span class="muted">暂无来源明细</span>';
    var universeSourceText = source.universe_source || '暂无';
    var universeSourceUpdatedText = fmtDateTime(source.universe_source_updated_at);
    var missingExamples = (source.no_kline_examples || []).length
      ? esc((source.no_kline_examples || []).join('、'))
      : '暂无';
    var strategyCounts = overview.strategy_counts || {};

    function statusPill(label, ok, detail) {
      var unknown = ok == null;
      var bg = unknown ? 'var(--cm-ink-100)' : (ok ? 'var(--cm-ok-100)' : 'var(--cm-bad-100)');
      var fg = unknown ? 'var(--cm-ink-700)' : (ok ? 'var(--cm-ok-500)' : 'var(--cm-bad-500)');
      var text = label + ' · ' + (unknown ? '未检测' : (ok ? '在线' : '离线'));
      if (detail) text += ' · ' + detail;
      return '<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:' + bg + ';color:' + fg + ';font-size:11px;font-weight:700">' + esc(text) + '</span>';
    }

    function workbenchLinkCard(title, desc, tag, tabName) {
      return '<div data-etf-tab="' + esc(tabName) + '" style="flex:1;min-width:220px;padding:14px;border:1px solid var(--cm-ink-100);border-radius:12px;background:var(--cm-bg);cursor:pointer">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">' +
        '<span style="font-weight:700;color:var(--cm-ink-900)">' + esc(title) + '</span>' +
        '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-ink-100);color:var(--cm-ink-700);font-size:11px;font-weight:700">' + esc(tag) + '</span>' +
        '</div>' +
        '<div style="font-size:12px;line-height:1.6;color:var(--cm-ink-700)">' + esc(desc) + '</div>' +
        '</div>';
    }

    function strategyShortcut(label, count, strategyType, tone) {
      return '<span data-etf-strategy="' + esc(strategyType) + '" style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:' + tone.bg + ';color:' + tone.fg + ';font-size:11px;font-weight:700;margin:4px 8px 0 0;cursor:pointer">' +
        esc(label + ' ' + fmt(count || 0)) +
        '</span>';
    }

    box.innerHTML =
      '<div class="panel" style="margin-bottom:14px">' +
      '<div class="panel-head" style="align-items:flex-start;gap:12px;flex-wrap:wrap">' +
      '<div style="min-width:280px;flex:1">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">' +
      '<span style="font-weight:700;font-size:15px">ETF 工作台</span>' +
      '<span style="padding:4px 10px;border-radius:999px;background:' + syncTone.bg + ';color:' + syncTone.fg + ';font-size:12px;font-weight:700">' + esc(syncTone.label) + '</span>' +
      '<span style="padding:4px 10px;border-radius:999px;background:' + (snapshot.is_stale ? 'var(--cm-warn-100)' : 'var(--cm-ink-100)') + ';color:' + (snapshot.is_stale ? 'var(--cm-warn-500)' : 'var(--cm-ink-700)') + ';font-size:12px;font-weight:700">' + esc(snapshot.is_stale ? '缓存偏旧' : '缓存最新') + '</span>' +
      '</div>' +
      '<div style="font-size:13px;line-height:1.7;color:var(--cm-ink-700)">' + esc(staleNote) + '</div>' +
      '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-900)"><strong>快照：</strong>' + esc(snapshot.snapshot_id || '-') + '</div>' +
      '<div style="margin-top:4px;font-size:12px;color:var(--cm-ink-500)"><strong>计算时间：</strong>' + esc(fmtDateTime(snapshot.computed_at)) + ' · <strong>覆盖ETF：</strong>' + esc(fmt(snapshot.etf_count)) + '</div>' +
      (syncState.message ? '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-500)"><strong>最近任务：</strong>' + esc(syncState.message) + '</div>' : '') +
      '</div>' +
      '<div style="min-width:280px;flex:1">' +
      '<div class="stats-row" style="grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:0">' +
      '<div class="stat-card"><div class="stat-value">' + esc(fmt(source.universe_count)) + '</div><div class="stat-label">ETF池</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(fmt(source.kline_etf_count)) + '</div><div class="stat-label">有日线</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(fmt(source.coverage_2023_count)) + '</div><div class="stat-label">覆盖到2023</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(fmt(source.recent_only_count)) + '</div><div class="stat-label">仅近期开盘</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(fmt(source.no_kline_count)) + '</div><div class="stat-label">暂无日线</div></div>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '</div>' +

      '<div class="panel" style="margin-bottom:14px">' +
      '<div class="panel-head"><span style="font-weight:600">数据源与覆盖范围</span></div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">' +
      statusPill('股东源', !!connectivity.holdings_source, connectivity.holdings_source_detail) +
      statusPill('K线源', !!connectivity.kline_source, connectivity.kline_source_detail) +
      statusPill('行业源', !!connectivity.industry_source, connectivity.industry_source_detail) +
      '</div>' +
      '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;line-height:1.8;color:var(--cm-ink-700)">' +
      '<div style="min-width:260px;flex:1"><strong>资产池来源：</strong>' + esc(universeSourceText) + '<br><strong>来源记录时间：</strong>' + esc(universeSourceUpdatedText) + '<br><strong>ETF池更新时间：</strong>' + esc(fmtDateTime(source.universe_updated_at)) + '</div>' +
      '<div style="min-width:260px;flex:1"><strong>全局历史区间：</strong>' + esc((source.history_start || '-') + ' ~ ' + (source.history_end || '-')) + '<br><strong>日线覆盖率：</strong>' + esc(source.kline_coverage_ratio != null ? Number(source.kline_coverage_ratio).toFixed(2) + '%' : '-') + '<br><strong>最近成功：</strong>' + esc(fmtDateTime(source.latest_kline_success_at)) + '</div>' +
      '<div style="min-width:260px;flex:1"><strong>最近尝试：</strong>' + esc(fmtDateTime(source.latest_kline_attempt_at)) + '<br><strong>快照滞后：</strong>' + esc(source.snapshot_lag_minutes != null ? source.snapshot_lag_minutes + ' 分钟' : '-') + '<br><strong>同步错误数：</strong>' + esc(fmt(source.last_error_count || 0)) + '</div>' +
      '</div>' +
      '<div style="margin-top:10px;font-size:12px;color:var(--cm-ink-900)"><strong>来源分布：</strong> ' + sourceBreakdown + '</div>' +
      '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-500)"><strong>当前无日线样例：</strong>' + missingExamples + '</div>' +
      '</div>' +

      '<div class="panel" style="margin-bottom:14px">' +
      '<div class="panel-head"><span style="font-weight:600">当前 ETF 结构</span></div>' +
      '<div class="stats-row" style="grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:10px">' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(overview.temperature_score, 1)) + '</div><div class="stat-label">市场温度</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(overview.positive_20d_ratio, 0)) + '%</div><div class="stat-label">宽基上涨占比</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(overview.avg_momentum_20d, 1)) + '%</div><div class="stat-label">平均20日动量</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(overview.avg_volatility_20d, 1)) + '%</div><div class="stat-label">平均20日波动</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(overview.avg_drawdown_60d, 1)) + '%</div><div class="stat-label">平均60日回撤</div></div>' +
      '</div>' +
      '<div style="font-size:12px;line-height:1.8;color:var(--cm-ink-700)"><strong>' + esc(overview.regime_label || '整体判断') + '：</strong>' + esc(overview.regime_reason || '-') + '</div>' +
      '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-900)"><strong>策略分布：</strong>' +
      strategyShortcut('买入持有', strategyCounts.trend, '买入持有', { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' }) +
      strategyShortcut('网格交易', strategyCounts.grid, '网格交易', { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' }) +
      strategyShortcut('防守停泊', strategyCounts.defensive, '防守停泊', { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' }) +
      strategyShortcut('暂不参与', strategyCounts.avoid, '暂不参与', { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' }) +
      '</div>' +
      '</div>' +

      '<div class="panel" style="margin-bottom:14px">' +
      '<div class="panel-head"><span style="font-weight:600">功能入口</span></div>' +
      '<div style="display:flex;gap:12px;flex-wrap:wrap">' +
      workbenchLinkCard('机会发现', '查看市场判断、网格候选、买入持有和下一轮动观察。', '判断', 'opportunity') +
      workbenchLinkCard('全量筛选', '按分类、策略、动量与回测结果筛选 ETF，并进入单只深度分析。', '筛选', 'list') +
      '</div>' +
      '</div>';

    box.querySelectorAll('[data-etf-tab]').forEach(function (node) {
      node.addEventListener('click', function () {
        showEtfTab(node.dataset.etfTab);
      });
    });
  }

  function loadEtfOpportunity() {
    var r = etfState.dataCache;
    if (!r?.data?.length) return;
    var opportunityBox = el('etfOpportunityContainer');
    if (!opportunityBox) return;
    if (!ETFOpportunityWidget || typeof ETFOpportunityWidget.mountOpportunity !== 'function') {
      opportunityBox.innerHTML = '<div class="muted" style="padding:20px;text-align:center">ETF 机会页 widget 暂不可用</div>';
      return;
    }
    ETFOpportunityWidget.mountOpportunity('etfOpportunityContainer', {
      state: etfState,
      deepPanelId: 'opportunityDeepPanel',
      api: api,
      loadEtfDeepAnalysis: loadEtfDeepAnalysis,
      showEtfTab: showEtfTab,
      ETFSectorRotationWidget: window.ETFSectorRotationWidget,
    });
  }

  function loadEtfList() {
    var r = etfState.dataCache;
    if (!r?.data?.length) return;
    var c = el('etfTableContainer');
    if (!c) return;
    if (!ETFListWidget || typeof ETFListWidget.mountEtfList !== 'function') {
      c.innerHTML = '<div class="muted" style="padding:20px;text-align:center">ETF 列表 widget 暂不可用</div>';
      return;
    }
    ETFListWidget.mountEtfList({
      tableId: 'etfTableContainer',
      filterId: 'etfCategoryFilter',
      state: etfState,
      rows: r.data,
      deepPanelId: 'etfListAnalysisPanel',
      onAnalyze: function (code, panelId) {
        loadEtfDeepAnalysis(code, panelId || 'etfListAnalysisPanel');
      },
    });
  }

  // ============================================================
  // ETF 深度量化分析面板
  // ============================================================
  async function loadEtfDeepAnalysis(code, panelId) {
    panelId = panelId || 'etfDeepAnalysisPanel';
    if (window.ETFAnalysisWidget && typeof window.ETFAnalysisWidget.mountDeepAnalysis === 'function') {
      return window.ETFAnalysisWidget.mountDeepAnalysis(code, panelId, {
        securityIdentityBlock: securityIdentityBlock,
      });
    }
    var panel = el(panelId);
    if (panel) {
      panel.innerHTML = '<div class="panel" style="margin-top:14px"><div class="muted" style="padding:20px;text-align:center">ETF 深度分析暂不可用</div></div>';
    }
  }

  // ============================================================
  // Lifeboat
  // ============================================================
  async function runLifeboat() {
    var btn = el('btnLifeboat');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '运行中...';
    var r = await api('/api/inst/lifeboat/run', { method: 'POST' });
    if (!r?.ok) {
      showModal('救生艇', r?.message || '启动失败');
      btn.disabled = false;
      btn.textContent = '救生艇';
      return;
    }
    // 轮询等待完成
    var polls = 0;
    var timer = setInterval(async function () {
      polls++;
      var s = await api('/api/inst/lifeboat/status');
      if (s && !s.running) {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = '救生艇';
        if (s.result?.ok) {
          showModal('救生艇', '报告已生成！<br><br><a href="/api/inst/lifeboat/report" target="_blank" style="color:var(--cm-brand-400);font-weight:600">点击查看报告</a><br><br><span style="font-size:12px;color:var(--cm-ink-300)">也可双击 lifeboat/run.command 独立运行</span>');
        } else {
          showModal('救生艇', '运行失败：' + (s.result?.message || '未知错误'));
        }
      }
      if (polls > 120) { clearInterval(timer); btn.disabled = false; btn.textContent = '救生艇'; }
    }, 3000);
  }

  // ============================================================
  // 模型监控页面 (Phase 5)
  // ============================================================

  // 全局 labels 缓存（英文 → 中文）
  window.FeatureLabels = window.FeatureLabels || { features: {}, models: {}, loaded: false };

  async function ensureFeatureLabels() {
    if (window.FeatureLabels.loaded) return window.FeatureLabels;
    try {
      var res = await api('/api/rec/labels');
      if (res && res.ok) {
        window.FeatureLabels.features = res.features || {};
        window.FeatureLabels.models = res.models || {};
        window.FeatureLabels.loaded = true;
      }
    } catch (e) {}
    return window.FeatureLabels;
  }

  function labelFeature(name) {
    var zh = window.FeatureLabels.features[name];
    return zh ? name + '（' + zh + '）' : name;
  }

  function labelModelId(mid) {
    if (!mid) return '-';
    for (var prefix in window.FeatureLabels.models) {
      if (mid.indexOf(prefix + '_') === 0) {
        var tail = mid.slice(prefix.length + 1);
        if (tail.length >= 13 && /^\d{8}/.test(tail)) {
          return window.FeatureLabels.models[prefix] + ' · ' +
            tail.slice(0, 4) + '-' + tail.slice(4, 6) + '-' + tail.slice(6, 8) + ' ' +
            tail.slice(9, 11) + ':' + tail.slice(11, 13);
        }
        return window.FeatureLabels.models[prefix] + ' · ' + tail;
      }
    }
    return mid;
  }

  var modelMonitorState = { modelId: null, regime: '' };

  async function loadModelMonitor() {
    await ensureFeatureLabels();
    if (modelMonitorState._bound) return renderModelMonitor();
    modelMonitorState._bound = true;
    var sel = el('mm-model-select'), regSel = el('mm-regime-select'), btn = el('mm-refresh');
    if (sel) sel.addEventListener('change', function () { modelMonitorState.modelId = sel.value; renderModelMonitor(); });
    if (regSel) regSel.addEventListener('change', function () { modelMonitorState.regime = regSel.value; });
    if (btn) btn.addEventListener('click', renderModelMonitor);

    // 加载模型历史填下拉 (中文名 + 综合评级标签)
    try {
      var res = await api('/api/rec/model-history?limit=20');
      if (res && res.ok) {
        sel.innerHTML = (res.items || []).map(function (m) {
          var cname = m.model_name_cn || m.model_id;
          var grade = m.composite_grade && m.composite_grade.grade || '-';
          return '<option value="' + m.model_id + '">' + cname + '  · 综合：' + grade + '</option>';
        }).join('');
        if (res.items && res.items[0]) modelMonitorState.modelId = res.items[0].model_id;
        try {
          var cmp = await api('/api/rec/model-comparison');
          if (cmp && cmp.ok && cmp.champion && cmp.champion.model_id) {
            modelMonitorState.modelId = cmp.champion.model_id;
            sel.value = modelMonitorState.modelId;
          }
        } catch (e) {}
      }
    } catch (e) {
      sel.innerHTML = '<option value="">(加载失败)</option>';
    }
    renderModelMonitor();
  }

  async function renderModelMonitor() {
    await Promise.all([
      renderModelComparison(),
      renderPromotionGate(),
      renderTdxValidation(),
      renderMetricsCards(),
      renderDailyChart(),
      renderRegimeChart(),
      renderFeatureImportance()
    ]);
  }

  async function renderModelComparison() {
    var box = el('mm-comparison'); if (!box) return;
    try {
      var res = await api('/api/rec/model-comparison');
      if (!res || !res.ok) { box.innerHTML = ''; return; }
      function fmt(v, d) { return v == null ? '-' : Number(v).toFixed(d == null ? 3 : d); }
      function pct(v) { return v == null ? '-' : (Number(v) * 100).toFixed(2) + '%'; }
      var c = res.champion || {}, ch = res.challenger || {};
      var shadow = res.shadow_topk || {};
      var evidence = res.evidence_bundle || {};
      box.innerHTML =
        '<div class="cm-action-grid cm-action-grid-tight">' +
        '<div class="cm-action-card"><div class="cm-action-title">Champion · 正式推荐</div><div style="font-weight:700;font-size:13px">' + esc(labelModelId(c.model_id || '-')) + '</div>' +
        '<div class="muted" style="font-size:11px;margin-top:4px">默认推荐只取 lifecycle champion · RankIC ' + fmt(c.holdout_rank_ic) + ' · L/S ' + pct(c.holdout_long_short_spread) + '</div></div>' +
        '<div class="cm-action-card"><div class="cm-action-title">最新 Challenger · Shadow 实验</div><div style="font-weight:700;font-size:13px">' + esc(labelModelId(ch.model_id || '尚未训练 challenger')) + '</div>' +
        '<div class="muted" style="font-size:11px;margin-top:4px">Not promoted · RankIC ' + fmt(ch.holdout_rank_ic) + ' · L/S ' + pct(ch.holdout_long_short_spread) + '</div>' +
        '<div class="muted" style="font-size:11px;margin-top:2px">shadow topK ' + (shadow.rows || shadow.row_count || 0) + ' rows · ' + (shadow.snapshot_date || shadow.latest_snapshot || '-') + '</div>' +
        (evidence.evidence_run_id ? '<div class="muted" style="font-size:11px;margin-top:2px">evidence ' + esc(evidence.status || '-') + ' · ' + esc(evidence.evidence_run_id) + '</div>' : '') + '</div>' +
        '</div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">model comparison error: ' + esc(e.message) + '</div>';
    }
  }

  async function renderPromotionGate() {
    var box = el('mm-promotion-gate'); if (!box) return;
    try {
      var res = await api('/api/rec/model-comparison');
      if (!res || !res.ok) { box.innerHTML = ''; return; }
      var gate = res.promotion_gate || {};
      var status = gate.promotion_status || 'WAIT';
      var tone = status === 'PASS' ? 'ok' : status === 'FAIL' ? 'bad' : 'warn';
      var blockers = gate.blockers || gate.reasons || gate.reason || '';
      if (Array.isArray(blockers)) {
        blockers = blockers.map(function (b) {
          if (typeof b === 'string') return b;
          return [b.gate, b.status, b.reason].filter(Boolean).join(': ');
        }).join('；');
      }
      box.innerHTML =
        '<div class="cm-status-strip">' +
        '<div class="cm-status-item cm-status-' + tone + '"><span>Promotion Gate</span><b>' + esc(status) + '</b></div>' +
        '<div class="cm-status-item"><span>Decision</span><b>' + esc(gate.decision || 'keep_shadow') + '</b></div>' +
        '<div class="cm-status-item"><span>默认推荐</span><b>' + (res.selection_fallback ? 'fallback' : 'champion-only') + '</b></div>' +
        '<div class="cm-status-item"><span>发布规则</span><b>PASS 也只标记 promote-ready</b></div>' +
        (blockers ? '<div class="cm-status-item cm-status-warn"><span>阻塞项</span><b>' + esc(String(blockers).slice(0, 120)) + '</b></div>' : '') +
        '</div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">promotion gate error: ' + esc(e.message) + '</div>';
    }
  }

  async function renderTdxValidation() {
    var box = el('mm-tdx-validation'); if (!box) return;
    try {
      var res = await api('/api/rec/tdx-feature-validation');
      if (!res || !res.ok) { box.innerHTML = ''; return; }
      function fmt(v) { return v == null ? '-' : Number(v).toFixed(3); }
      var keep = (res.manual || []).filter(function (r) { return r.decision === 'keep'; });
      var watchDrop = (res.manual || []).filter(function (r) { return r.decision !== 'keep'; }).slice(0, 8);
      var sources = (res.sources || []).map(function (s) {
        return '<span style="display:inline-block;margin:2px 6px 2px 0;padding:3px 7px;border:1px solid var(--cm-ink-100);border-radius:4px;font-size:11px">' +
          esc(s.data_domain) + ': <b>' + esc(s.preferred_source) + '</b>' +
          (s.fallback_1 ? ' / ' + esc(s.fallback_1) : '') +
          (s.fallback_2 ? ' / ' + esc(s.fallback_2) : '') + '</span>';
      }).join('');
      var keepRows = keep.map(function (r) {
        return '<tr><td>' + esc(labelFeature(r.feature_name)) + '</td><td>' + fmt(r.mean_rank_ic) + '</td><td>' + fmt(r.fold_same_sign_rate) + '</td><td>' + fmt(r.coverage_pct) + '%</td><td>' + (r.pit_violation_rows || 0) + '</td></tr>';
      }).join('');
      var wdRows = watchDrop.map(function (r) {
        return '<tr><td>' + esc(r.decision) + '</td><td>' + esc(labelFeature(r.feature_name)) + '</td><td>' + esc(r.primary_reason || '-') + '</td></tr>';
      }).join('');
      box.innerHTML =
        '<div class="panel" style="padding:12px">' +
        '<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-bottom:8px">' +
        '<h4 style="margin:0;font-size:13px">TDX keep 特征验证与数据源切换</h4>' +
        '<span class="muted" style="font-size:11px">PIT violations: manual ' + ((res.pit && res.pit.tdx_f10_gpcw_v1 && res.pit.tdx_f10_gpcw_v1.violation_rows) || 0) + '</span></div>' +
        '<div style="margin-bottom:8px">' + sources + '</div>' +
        '<div class="mm-validation-grid">' +
        '<div class="cm-table-scroll"><table class="mini-table"><thead><tr><th>keep feature</th><th>RankIC</th><th>same sign</th><th>coverage</th><th>PIT</th></tr></thead><tbody>' + keepRows + '</tbody></table></div>' +
        '<div class="cm-table-scroll"><table class="mini-table"><thead><tr><th>decision</th><th>feature</th><th>reason</th></tr></thead><tbody>' + wdRows + '</tbody></table></div>' +
        '</div></div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">tdx validation error: ' + esc(e.message) + '</div>';
    }
  }

  async function renderMetricsCards() {
    var box = el('mm-metrics');
    var cbox = el('mm-composite');
    if (!box) return;
    var mid = modelMonitorState.modelId;
    if (!mid) { box.innerHTML = '<div class="muted" style="padding:20px">无已训练模型</div>'; if (cbox) cbox.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">' + (res && res.message || 'load err') + '</div>'; return; }
      var m = res.meta || {};
      function fmt(v, d) { return v == null ? '-' : Number(v).toFixed(d == null ? 3 : d); }
      function pct(v) { return v == null ? '-' : (Number(v) * 100).toFixed(2) + '%'; }

      // 综合评级卡 (置顶大卡)
      var comp = m.composite_grade || {};
      if (cbox) {
        cbox.innerHTML =
          '<div class="panel" style="padding:14px;display:flex;gap:18px;align-items:center;background:var(--cm-macaron-cream);border-left:4px solid ' +
          (comp.color || 'var(--cm-ink-300)') + '">' +
          '<div style="font-size:28px;font-weight:700;color:' + (comp.color || 'var(--cm-ink-300)') + ';min-width:120px">' +
          (comp.grade || '-') + '</div>' +
          '<div style="flex:1">' +
          '<div style="font-size:12px;color:var(--cm-ink-500)">综合评级（5 档：差/较差/一般/良好/优秀）· ' +
          (m.model_name_cn || mid) + '</div>' +
          '<div style="font-size:11px;color:var(--cm-ink-500);margin-top:2px">平均档位 ' + (comp.avg_index == null ? '-' : comp.avg_index.toFixed(2)) + ' / 4 · 特征数 ' + (m.n_features || '-') + ' · 创建 ' + (m.created_at ? m.created_at.slice(0, 19) : '-') + '</div>' +
          '</div>' +
          '</div>';
      }

      // 5 个指标卡 (各自带档位 chip)
      var mg = m.metric_grades || {};
      function gradeChip(g) {
        if (!g || g.index < 0) return '<div class="wb-card-chip" style="background:var(--cm-ink-50);color:var(--cm-ink-500)">-</div>';
        return '<div class="wb-card-chip" style="background:' + g.color + '22;color:' + g.color + ';font-weight:600">' + g.grade + '</div>';
      }
      var portfolioItems = (res.portfolio && res.portfolio.items) || [];
      var p30 = portfolioItems.find(function (x) { return x.curve_type === 'model_top20' && Number(x.cost_bps) === 30; }) ||
        portfolioItems.find(function (x) { return x.curve_type === 'model_top20'; }) || {};
      var wf = (res.walkforward && res.walkforward.summary) || {};
      var dq = res.data_quality || {};
      var liveCards =
        '<div class="wb-card"><div class="wb-card-label">net top20（组合·扣成本）</div><div class="wb-card-value">' + pct(p30.annualized_return) + '</div><div class="wb-card-sub">MaxDD ' + pct(p30.max_drawdown) + ' · Sharpe ' + fmt(p30.sharpe, 2) + '</div><div class="wb-card-chip">30bps</div></div>' +
        '<div class="wb-card"><div class="wb-card-label">walk-forward RankIC</div><div class="wb-card-value">' + fmt(wf.rank_ic_mean, 3) + '</div><div class="wb-card-sub">正折率 ' + pct(wf.rank_ic_positive_ratio) + ' · 折数 ' + (wf.fold_count || '-') + '</div><div class="wb-card-chip">稳定性</div></div>' +
        '<div class="wb-card"><div class="wb-card-label">feature schema</div><div class="wb-card-value">' + (m.feature_schema_version || '-') + '</div><div class="wb-card-sub">label ' + (m.label_name || '-') + ' · ' + ((m.feature_cols || []).length || m.n_features || '-') + ' 列</div><div class="wb-card-chip">schema</div></div>' +
        '<div class="wb-card"><div class="wb-card-label">panel freshness</div><div class="wb-card-value">' + (dq.latest_panel_date || '-') + '</div><div class="wb-card-sub">' + (dq.codes || '-') + ' 股 · ' + (dq.dates || '-') + ' 日 · label ' + (dq.label_rows || '-') + '</div><div class="wb-card-chip">data</div></div>';
      box.innerHTML =
        liveCards +
        '<div class="wb-card"><div class="wb-card-label">holdout_ic（持出期·IC）</div><div class="wb-card-value">' + fmt(m.holdout_ic, 3) + '</div><div class="wb-card-sub">门槛 优秀>0.05 良好>0.03</div>' + gradeChip(mg.holdout_ic) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_rank_ic（持出期·Rank IC）</div><div class="wb-card-value">' + fmt(m.holdout_rank_ic, 3) + '</div><div class="wb-card-sub">门槛 优秀>0.08 良好>0.06</div>' + gradeChip(mg.holdout_rank_ic) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_top_decile_avg（Top 10% 平均20日收益）</div><div class="wb-card-value">' + pct(m.holdout_top_decile_avg) + '</div><div class="wb-card-sub">门槛 优秀>3% 良好>2%</div>' + gradeChip(mg.holdout_top_decile_avg) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_long_short_spread（多空价差）</div><div class="wb-card-value">' + pct(m.holdout_long_short_spread) + '</div><div class="wb-card-sub">门槛 优秀>4% 良好>2%</div>' + gradeChip(mg.holdout_long_short_spread) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_winrate_top（Top 10% 胜率）</div><div class="wb-card-value">' + pct(m.holdout_winrate_top) + '</div><div class="wb-card-sub">门槛 优秀>60% 良好>56%</div>' + gradeChip(mg.holdout_winrate_top) + '</div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + e.message + '</div>';
    }
  }

  function svgLine(series, w, h, pad) {
    pad = pad || 30;
    if (!series.length) return '<text x="' + (w / 2) + '" y="' + (h / 2) + '" text-anchor="middle" fill="var(--cm-ink-300)" font-size="12">无数据</text>';
    var vals = series.map(function (d) { return d.v; }).filter(function (v) { return v != null && !isNaN(v); });
    if (!vals.length) return '<text x="' + (w / 2) + '" y="' + (h / 2) + '" text-anchor="middle" fill="var(--cm-ink-300)" font-size="12">无数值</text>';
    var minV = Math.min.apply(null, vals), maxV = Math.max.apply(null, vals);
    var range = maxV - minV || 0.01;
    var W = w - pad * 2, H = h - pad * 2;
    var pts = series.map(function (d, i) {
      var x = pad + (series.length === 1 ? W / 2 : i / (series.length - 1) * W);
      var y = pad + H - (d.v == null || isNaN(d.v) ? 0 : (d.v - minV) / range * H);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var zeroY = pad + H - (0 - minV) / range * H;
    var zeroLine = minV < 0 && maxV > 0 ? '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + zeroY + '" y2="' + zeroY + '" stroke="var(--cm-ink-100)" stroke-dasharray="3,3"/>' : '';
    return zeroLine +
      '<polyline points="' + pts + '" fill="none" stroke="var(--cm-brand-400)" stroke-width="1.5"/>' +
      '<text x="' + pad + '" y="' + (pad - 6) + '" fill="var(--cm-ink-500)" font-size="10">max ' + maxV.toFixed(3) + '</text>' +
      '<text x="' + pad + '" y="' + (h - 8) + '" fill="var(--cm-ink-500)" font-size="10">min ' + minV.toFixed(3) + '</text>' +
      '<text x="' + (w - pad) + '" y="' + (h - 8) + '" fill="var(--cm-ink-500)" font-size="10" text-anchor="end">n=' + series.length + '</text>';
  }

  async function renderDailyChart() {
    var box = el('mm-chart-daily'); if (!box) return;
    var mid = modelMonitorState.modelId; if (!mid) { box.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">load err</div>'; return; }
      var series = (res.daily_series || []).map(function (d) { return { t: d.date, v: d.spread }; });
      box.innerHTML = '<svg viewBox="0 0 600 220" style="width:100%;height:100%">' + svgLine(series, 600, 220, 30) + '</svg>';
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + e.message + '</div>';
    }
  }

  async function renderRegimeChart() {
    var box = el('mm-chart-regime'); if (!box) return;
    var mid = modelMonitorState.modelId; if (!mid) { box.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">load err</div>'; return; }
      var rows = (res.regime_breakdown || []).filter(function (r) { return r.regime_flag; });
      if (!rows.length) { box.innerHTML = '<div class="muted" style="text-align:center;padding:40px">holdout 期无分组数据</div>'; return; }
      var w = 600, h = 220, pad = 40;
      var barW = (w - pad * 2) / (rows.length * 2 + rows.length);
      var allV = rows.flatMap(function (r) { return [r.top_avg || 0, r.bot_avg || 0]; });
      var maxAbs = Math.max.apply(null, allV.map(Math.abs)) || 0.01;
      var midY = h / 2;
      var svg = '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + midY + '" y2="' + midY + '" stroke="var(--cm-ink-100)"/>';
      rows.forEach(function (r, i) {
        var x = pad + i * barW * 3;
        var hTop = Math.abs(r.top_avg || 0) / maxAbs * (h / 2 - pad);
        var hBot = Math.abs(r.bot_avg || 0) / maxAbs * (h / 2 - pad);
        var topY = (r.top_avg || 0) >= 0 ? midY - hTop : midY;
        var botY = (r.bot_avg || 0) >= 0 ? midY - hBot : midY;
        svg += '<rect x="' + x + '" y="' + topY + '" width="' + barW + '" height="' + hTop + '" fill="var(--stock-down)"/>';
        svg += '<rect x="' + (x + barW) + '" y="' + botY + '" width="' + barW + '" height="' + hBot + '" fill="var(--stock-up)"/>';
        svg += '<text x="' + (x + barW) + '" y="' + (h - pad / 2) + '" text-anchor="middle" font-size="11" fill="var(--cm-ink-700)">' + r.regime_flag + '</text>';
        svg += '<text x="' + (x + barW / 2) + '" y="' + (topY - 2) + '" text-anchor="middle" font-size="9" fill="var(--cm-ok-500)">' + ((r.top_avg || 0) * 100).toFixed(1) + '%</text>';
        svg += '<text x="' + (x + barW * 1.5) + '" y="' + (botY + hBot + 10) + '" text-anchor="middle" font-size="9" fill="var(--cm-bad-500)">' + ((r.bot_avg || 0) * 100).toFixed(1) + '%</text>';
      });
      svg += '<text x="' + pad + '" y="' + (pad / 2) + '" font-size="10" fill="var(--cm-ok-500)">■ top-decile</text>';
      svg += '<text x="' + (pad + 80) + '" y="' + (pad / 2) + '" font-size="10" fill="var(--cm-bad-500)">■ bot-decile</text>';
      box.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:100%">' + svg + '</svg>';
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + e.message + '</div>';
    }
  }

  async function renderFeatureImportance() {
    var box = el('mm-chart-fi'); if (!box) return;
    var mid = modelMonitorState.modelId; if (!mid) { box.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">load err</div>'; return; }
      var fi = (res.meta && res.meta.feature_importance) || [];
      if (!fi.length) { box.innerHTML = '<div class="muted">无 feature importance 数据</div>'; return; }
      var maxV = Math.max.apply(null, fi.map(function (x) { return x.importance || 0; })) || 1;
      var html = '<div style="display:grid;grid-template-columns:auto 1fr auto;gap:4px 10px;font-size:12px;align-items:center">';
      fi.forEach(function (x, i) {
        var pct = (x.importance / maxV) * 100;
        var zh = x.label_cn || window.FeatureLabels.features[x.name] || '';
        html += '<div class="muted">#' + (i + 1) + '</div>';
        html += '<div style="display:flex;align-items:center;gap:8px">' +
          '<div style="font-weight:500;white-space:nowrap;min-width:240px"><span style="color:var(--cm-ink-900)">' + x.name + '</span>' +
          (zh ? '<span style="color:var(--cm-ink-500);font-weight:400;margin-left:6px">（' + zh + '）</span>' : '') + '</div>' +
          '<div style="flex:1;background:var(--cm-ink-50);height:14px;border-radius:3px;overflow:hidden">' +
          '<div style="width:' + pct.toFixed(1) + '%;height:100%;background:var(--cm-brand-500)"></div>' +
          '</div></div>';
        html += '<div style="color:var(--cm-ink-500);font-family:monospace">' + Math.round(x.importance) + '</div>';
      });
      html += '</div>';
      box.innerHTML = html;
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + e.message + '</div>';
    }
  }

  window.App = { showView, showWorkbenchTab, setAlias, setType, toggleBlack, deleteInst, restoreInst, toggleInstDetail, toggleInstBreakdown, showL2Profile, toggleStockDetail, switchInstDim, switchStockDim, runSingleStep, loadWatchlist, loadExclusions, refreshNetwork, _api: api };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startInit, { once: true });
  } else {
    startInit();
  }
})();
