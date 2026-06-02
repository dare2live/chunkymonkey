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

  // Step 5d：机构 sub-tabs 已合并为单列表 + 抽屉（见 renderInstList）。
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
  function stockCompositeSummary(s) {
    return [
      '发' + scoreNum(s.discovery_score),
      '质' + scoreNum(s.company_quality_score),
      '阶' + scoreNum(s.stage_score)
    ].join(' / ');
  }
  function stockCompositeCell(s) {
    var composite = s.composite_priority_score != null ? Number(s.composite_priority_score).toFixed(1) : '-';
    var poolHtml = priorityPoolTag(s.priority_pool);
    var tooltip = [stockCompositeSummary(s), s.stock_archetype || '待分类'].filter(Boolean).join(' · ');
    return '<div class="stock-composite-cell" title="' + esc(tooltip) + '">' +
      '<div class="stock-composite-main">' + poolHtml + '<span class="stock-composite-score">' + composite + '</span></div>' +
      '</div>';
  }
  function stockResearchCell(s) {
    var gate = stockGateInfo(s);
    var tone = gate.key === 'follow' ? 'good' : gate.key === 'watch' ? 'accent' : gate.key === 'observe' ? 'warn' : 'neutral';
    var label = gate.label || '待验证';
    var tooltip = gate.reason || s.setup_reason || s.priority_pool_reason || s.stage_reason || '展开详情查看完整研报';
    return '<div class="stock-research-cell">' +
      '<span class="stock-research-status stock-research-status--' + tone + '" title="' + esc(tooltip) + '">' + esc(label) + '</span>' +
      '</div>';
  }
  function stockDateSummaryCell(dateText, subtext) {
    if (!dateText) return '<div class="stock-date-cell"><div class="stock-date-main">-</div></div>';
    return '<div class="stock-date-cell"><div class="stock-date-main">' + fmtDate(dateText) + ' ' + daysAgoPill(dateText) + '</div></div>';
  }
  function stockHolderCoverageCell(s) {
    var holderTotal = (s && s.holder_total != null) ? Number(s.holder_total || 0) : Number((s && s.inst_count_t0) || 0);
    var follow = Number((s && s.holder_follow_count) || 0);
    var watch = Number((s && s.holder_watch_count) || 0);
    var observe = Number((s && s.holder_observe_count) || 0);
    var avoid = Number((s && s.holder_avoid_count) || 0);
    var parts = [];
    if (follow) parts.push('可跟 ' + follow);
    if (watch) parts.push('关注 ' + watch);
    if (observe) parts.push('观察 ' + observe);
    if (avoid) parts.push('回避 ' + avoid);
    var lead = follow ? ('可跟 ' + follow) : (watch ? ('关注 ' + watch) : (observe ? ('观察 ' + observe) : (avoid ? ('回避 ' + avoid) : '未分层')));
    return '<div class="stock-holder-cell" title="' + esc(parts.join(' · ') || '暂无执行分层') + '"><div class="stock-holder-main">' + esc(String(holderTotal)) + '</div><div class="stock-holder-sub">' + esc(lead) + '</div></div>';
  }
  function stockDimensionCell(s, dimension) {
    var score = stockScoreValue(s, dimension);
    var band = stockScoreBandMeta(dimension, score);
    var sub = stockScoreSubtext(s, dimension);
    return '<div class="stock-score-cell" title="' + esc(sub || '') + '"><div class="stock-score-head"><span class="stock-score-value">' + esc(scoreNum(score)) + '</span><span class="stock-score-pill stock-score-pill--' + band.tone + '">' + esc(band.label) + '</span></div>' +
      '</div>';
  }
  function stockAttentionVerdictCell(s, opts) {
    opts = opts || {};
    if (!s) return '';
    var tag = attentionSignalTag(s.external_attention_signal);
    var metrics = [];
    if (s.external_attention_score != null) metrics.push('确 ' + scoreNum(s.external_attention_score));
    if (s.external_crowding_penalty != null && Number(s.external_crowding_penalty) > 0) metrics.push('挤 ' + scoreNum(s.external_crowding_penalty));
    if (s.raw_composite_priority_score != null && s.composite_priority_score != null && Number(s.raw_composite_priority_score) !== Number(s.composite_priority_score)) {
      metrics.push(scoreNum(s.raw_composite_priority_score) + ' → ' + scoreNum(s.composite_priority_score));
    }
    var reason = opts.withReason ? (s.priority_pool_reason || s.composite_cap_reason || '') : '';
    if (!tag && !metrics.length && !reason) return '';
    var lead = tag || '<span class="stock-attention-pill stock-attention-pill--neutral">外部覆盖</span>';
    return '<div style="margin-top:6px;line-height:1.45">' +
      lead +
      (metrics.length ? '<div class="muted" style="font-size:10px;margin-top:4px">' + esc(metrics.join(' · ')) + '</div>' : '') +
      (reason ? '<div class="muted" style="font-size:10px;margin-top:4px">' + esc(reason) + '</div>' : '') +
      '</div>';
  }
  function stockSignalCell(s) {
    var scoreText = s.score_highlights ? '加分：' + s.score_highlights : (s.discovery_score != null ? '发现层 ' + scoreNum(s.discovery_score) : '发现层 -');
    if (s.score_risks) scoreText += ' · 风险：' + s.score_risks;
    if (!s || !s.setup_tag) {
      return '<div class="stock-signal-cell"><div class="stock-signal-title">暂无当前核心信号</div><div class="stock-signal-sub">' + esc(s.path_state || s.price_trend || '等待新的机构动作') + '</div><div class="stock-signal-meta">' + esc(scoreText) + '</div></div>';
    }
    return '<div class="stock-signal-cell">' +
      '<div>' + setupBadge(s.setup_tag, s.setup_priority, s.setup_confidence) + '</div>' +
      '<div class="stock-signal-title">' + esc(stockSignalHeadline(s)) + '</div>' +
      '<div class="stock-signal-sub">' + esc(stockSignalNarrative(s)) + '</div>' +
      '<div class="stock-signal-meta">' + esc(scoreText) + '</div>' +
      '</div>';
  }
  function stockExecutionCell(s) {
    // 与表格列、筛选共用同一份 stockGateInfo（可跟/关注/观察/回避）
    var info = stockGateInfo(s);
    var main = stockGateTag(s);
    var sub = info.reason || s.setup_execution_reason || s.priority_pool_reason || s.stage_reason || '';
    return '<div class="stock-exec-cell">' + main + (sub ? '<div class="stock-exec-sub">' + esc(sub) + '</div>' : '') + '</div>';
  }
  function sourceInstitutionCell(s) {
    var name = stockSourceName(s);
    var sub = s.setup_industry_name || s.tdx_l3 || s.tdx_l2 || s.tdx_l1 || '-';
    return '<div class="stock-source-cell"><div class="stock-source-main">' + esc(name) + '</div><div class="stock-source-sub">' + esc(sub) + '</div></div>';
  }
  function stockReportCell(s) {
    return '<div class="stock-source-cell"><div class="stock-source-main">' + fmtDate(s.latest_report_date) + '</div><div class="stock-source-sub">' + esc(daysBetweenDates(s.latest_report_date, s.latest_notice_date)) + ' 披露</div></div>';
  }
  function instLink(id, name, type) { return '<span class="type-tag clickable-name" data-type="' + esc(type || 'other') + '" onclick="App.toggleInstDetail(\'' + esc(id) + '\',this)" style="cursor:pointer;font-size:11px">' + esc(name || '') + '</span>' }
  function typeTag(type, label) { return '<span class="type-tag" data-type="' + esc(type || 'other') + '">' + (label || esc(type || 'other')) + '</span>' }
  function evTag(type, label) { var cls = { new_entry: 'new', increase: 'up', decrease: 'down', exit: 'exit', unchanged: 'unchanged' }[type] || 'unchanged'; return '<span class="event-tag event-' + (cls) + '">' + esc(label || { new_entry: '新进', increase: '增持', decrease: '减持', exit: '退出', unchanged: '不变' }[type] || type) + '</span>' }

  // ============================================================
  // Table Sorting
  // ============================================================
  function sortableCellMeta(cell) {
    if (!cell) return { kind: 'text', raw: '', value: '' };
    var datasetValue = cell.dataset.sortValue || cell.querySelector('[data-sort-value]')?.dataset.sortValue || '';
    var raw = String(datasetValue || cell.textContent || '').trim();
    if (!raw) return { kind: 'text', raw: '', value: '' };
    var dateMatch = raw.match(/(\d{4})[-\/]?(\d{2})[-\/]?(\d{2})/);
    if (dateMatch) {
      return {
        kind: 'date',
        raw: raw,
        value: Number(dateMatch[1] + dateMatch[2] + dateMatch[3])
      };
    }
    var numericRaw = raw.replace(/[,%+]/g, '');
    var numeric = parseFloat(numericRaw);
    if (!Number.isNaN(numeric) && /^[-+]?\d/.test(numericRaw)) {
      return { kind: 'number', raw: raw, value: numeric };
    }
    return { kind: 'text', raw: raw, value: raw };
  }

  function makeSortable(tableEl) {
    if (!tableEl || tableEl.dataset.sortableReady === '1') return;
    tableEl.dataset.sortableReady = '1';
    var heads = tableEl.querySelectorAll('thead th');
    heads.forEach(function (th, idx) {
      // 跳过包含复选框或宽度很小的列（如全选列）
      if (th.querySelector('input[type="checkbox"]') || th.style.width === '30px') return;
      th.style.cursor = 'pointer';
      th.addEventListener('click', function () {
        var tbody = tableEl.querySelector('tbody');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        var asc = th.dataset.sortDir !== 'asc';
        heads.forEach(h => { h.dataset.sortDir = ''; h.textContent = h.textContent.replace(/ [▲▼]/, ''); });
        th.dataset.sortDir = asc ? 'asc' : 'desc';
        th.textContent += asc ? ' ▲' : ' ▼';
        rows.sort(function (a, b) {
          var ca = a.children[idx], cb = b.children[idx];
          var ma = sortableCellMeta(ca), mb = sortableCellMeta(cb);
          if (ma.kind === 'date' && mb.kind === 'date') return asc ? ma.value - mb.value : mb.value - ma.value;
          if (ma.kind === 'number' && mb.kind === 'number') return asc ? ma.value - mb.value : mb.value - ma.value;
          return asc ? ma.raw.localeCompare(mb.raw, 'zh') : mb.raw.localeCompare(ma.raw, 'zh');
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  }

  function scheduleSortableTables(root, selector) {
    var container = typeof root === 'string' ? el(root) : root;
    if (!container) return;
    var tables = Array.from(container.querySelectorAll(selector || '.data-table'));
    if (!tables.length) return;
    var idx = 0;
    function runBatch() {
      var startedAt = (window.performance && performance.now) ? performance.now() : Date.now();
      while (idx < tables.length) {
        makeSortable(tables[idx]);
        idx++;
        var now = (window.performance && performance.now) ? performance.now() : Date.now();
        if ((now - startedAt) > 8) break;
      }
      if (idx < tables.length) {
        if (typeof window.requestIdleCallback === 'function') {
          window.requestIdleCallback(runBatch, { timeout: 300 });
        } else {
          setTimeout(runBatch, 16);
        }
      }
    }
    if (typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(runBatch, { timeout: 150 });
    } else {
      setTimeout(runBatch, 16);
    }
  }

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
  function renderScoreParamCard(containerId, framework, config, defaults) {
    var items = framework?.editable_factors || [];
    return '<div class="score-rule-card" id="' + esc(containerId) + '">' +
      '<div class="score-rule-title">' + esc(framework?.title || '评分参数') + '</div>' +
      '<div class="score-param-list">' + items.map(function (item) {
        var current = config && config[item.key] != null ? config[item.key] : (defaults && defaults[item.key] != null ? defaults[item.key] : 0);
        var def = defaults && defaults[item.key] != null ? defaults[item.key] : 0;
        return '<div class="score-param-item">' +
          '<div class="score-param-head">' +
          '<div class="score-param-title">' + esc(item.label || item.key) + '</div>' +
          '<input type="number" class="score-input" data-key="' + esc(item.key) + '" value="' + esc(String(current)) + '" min="0" max="100">' +
          '</div>' +
          '<div class="score-param-desc">' + esc(item.description || '-') + '</div>' +
          '<div class="score-param-sub">默认 ' + esc(String(def)) + (item.source ? ' · 来源 ' + esc(item.source) : '') + '</div>' +
          '</div>';
      }).join('') + '</div>' +
      '</div>';
  }

  function renderInstFrameworkRules(framework) {
    return '<div class="score-rule-grid">' +
      '<div class="score-rule-card">' +
      '<div class="score-rule-title">固定口径</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item"><b>评分公式</b>：' + esc(framework?.formula || '-') + '</div>' +
      '<div class="score-rule-item"><b>置信因子</b>：' + esc(framework?.confidence || '-') + '</div>' +
      '</div>' +
      '</div>' +
      '<div class="score-rule-card">' +
      '<div class="score-rule-title">当前定位</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item">' + esc(framework?.summary || '-') + '</div>' +
      '</div>' +
      '</div>' +
      '</div>';
  }

  function renderInstScorecardStats(stats) {
    if (!stats) return '';
    var summary = stats.summary || {};
    var typeTop = Array.isArray(stats.type_top) ? stats.type_top : [];
    var hintTop = Array.isArray(stats.hint_top) ? stats.hint_top : [];
    var confidence = stats.confidence || {};

    var summaryCards = '<div class="scorecard-stats-grid">' +
      renderScorecardMiniCard('机构样本', fmt(summary.total || 0), '当前已生成画像的机构数') +
      renderScorecardMiniCard('买入口径', fmt(summary.buy_basis_count || 0), 'fallback ' + fmt(summary.fallback_basis_count || 0)) +
      renderScorecardMiniCard('高置信机构分', fmt(summary.quality_high_conf_count || 0), '均分 ' + scoreNum(summary.avg_quality_score)) +
      renderScorecardMiniCard('高置信可跟分', fmt(summary.follow_high_conf_count || 0), '均分 ' + scoreNum(summary.avg_followability_score)) +
      renderScorecardMiniCard('高分机构', fmt(summary.quality_strong_count || 0), 'quality ≥ 65') +
      renderScorecardMiniCard('高可跟机构', fmt(summary.followability_strong_count || 0), 'followability ≥ 65') +
      renderScorecardMiniCard('安全跟随机构', fmt(summary.safe_follow_inst_count || 0), '均安全样本 ' + fmt(summary.avg_safe_follow_event_count || 0)) +
      renderScorecardMiniCard('平均溢价', fmtGain(summary.avg_premium_pct), '均买入样本 ' + fmt(summary.avg_buy_event_count || 0)) +
      '</div>';

    var typeTable = typeTop.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">机构类型分布</div>' +
      '<table class="score-pool-table"><thead><tr><th>类型</th><th>机构数</th><th>均机构分</th><th>均可跟分</th></tr></thead><tbody>' +
      typeTop.map(function (item) {
        return '<tr><td>' + esc(item.inst_type || '未分类') + '</td><td>' + fmt(item.total) + '</td><td>' + scoreNum(item.avg_quality_score) + '</td><td>' + scoreNum(item.avg_followability_score) + '</td></tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    var confidenceCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">置信分层</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item"><b>机构分</b>：' + renderConfidenceSummary(confidence.quality || []) + '</div>' +
      '<div class="score-rule-item"><b>可跟分</b>：' + renderConfidenceSummary(confidence.followability || []) + '</div>' +
      '</div>' +
      '</div>';

    var hintCard = hintTop.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">可跟性提示分布</div>' +
      '<div class="score-rule-list">' + hintTop.map(function (item) {
        return '<div class="score-rule-item">' + esc(item.followability_hint || '未标注') + ' · ' + fmt(item.total) + ' 家</div>';
      }).join('') + '</div>' +
      '</div>'
      : '';

    return '<div class="score-rule-card" style="margin-bottom:12px">' +
      '<div class="score-rule-title">当前样本摘要</div>' +
      '<div class="scorecard-note">机构评分卡现在会直接展示真实机构画像分布，帮助判断这套机构评分与可跟性评分在当前样本中的覆盖、置信和主流提示结构。</div>' +
      '</div>' +
      summaryCards +
      '<div class="score-rule-grid" style="margin-top:12px">' + typeTable + confidenceCard + '</div>' +
      hintCard;
  }

  function renderConfidenceSummary(items) {
    if (!items || !items.length) return '-';
    return items.map(function (item) {
      return (item.confidence || '未标注') + ' ' + fmt(item.total || 0);
    }).join(' · ');
  }

  function renderStockFrameworkLayer(layer) {
    return '<div class="score-framework-card">' +
      '<div class="score-framework-head">' +
      '<div class="score-framework-title">' + esc(layer.label || '-') + '</div>' +
      '<span class="score-framework-weight">' + esc(String(layer.weight || 0)) + '%</span>' +
      '</div>' +
      '<div class="score-framework-role">' + esc(layer.role || '-') + '</div>' +
      '<div class="score-framework-summary">' + esc(layer.summary || '-') + '</div>' +
      '<div class="score-framework-list">' + (layer.items || []).map(function (item) {
        return '<div class="score-framework-item">' + esc(item) + '</div>';
      }).join('') + '</div>' +
      '</div>';
  }

  function renderStockFrameworkRules(framework) {
    var formulaCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">固定口径</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item"><b>综合优先分</b>：' + esc(framework.formula || '-') + '</div>' +
      '<div class="score-rule-item"><b>' + esc((framework.effective_forecast || {}).label || '生效预测分') + '</b>：' + esc((framework.effective_forecast || {}).formula || '-') + '</div>' +
      '<div class="score-rule-item">' + esc((framework.effective_forecast || {}).meaning || '-') + '</div>' +
      '</div>' +
      '</div>';
    var capsCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">封顶与门槛</div>' +
      '<div class="score-rule-list">' + (framework.caps || []).map(function (item) {
        return '<div class="score-rule-item">' + esc(item) + '</div>';
      }).join('') + '</div>' +
      '</div>';
    var overlay = framework.external_overlay || {};
    var overlayCard = (overlay.summary || (overlay.items || []).length)
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">' + esc(overlay.label || '外部关注叠加层') + '</div>' +
      '<div class="score-rule-list">' +
      (overlay.summary ? '<div class="score-rule-item">' + esc(overlay.summary) + '</div>' : '') +
      (overlay.items || []).map(function (item) {
        return '<div class="score-rule-item">' + esc(item) + '</div>';
      }).join('') +
      '</div>' +
      '</div>'
      : '';
    var poolRows = (framework.pools || []).map(function (item) {
      return '<tr><td>' + priorityPoolTag(item.label) + '</td><td>' + esc(item.gate || '-') + '</td><td>' + esc(item.meaning || '-') + '</td></tr>';
    }).join('');
    var poolCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">池子规则</div>' +
      '<table class="score-pool-table"><thead><tr><th>池子</th><th>门槛</th><th>含义</th></tr></thead><tbody>' + poolRows + '</tbody></table>' +
      '</div>';
    return '<div class="score-rule-grid">' + formulaCard + capsCard + overlayCard + '</div>' + poolCard;
  }

  function renderStockAttentionCalibration(calibration) {
    if (!calibration || !calibration.summary) return '';
    var summary = calibration.summary || {};
    var hints = Array.isArray(calibration.hints) ? calibration.hints : [];
    var signals = Array.isArray(calibration.signal_distribution) ? calibration.signal_distribution : [];
    var scoreBands = Array.isArray(calibration.score_bands) ? calibration.score_bands : [];
    var penaltyBands = Array.isArray(calibration.penalty_bands) ? calibration.penalty_bands : [];

    var summaryCards = '<div class="scorecard-stats-grid">' +
      renderScorecardMiniCard('外部覆盖', fmt(summary.covered_stock_count || 0), '覆盖率 ' + pct(summary.coverage_ratio)) +
      renderScorecardMiniCard('触发信号', fmt(summary.signaled_stock_count || 0), '增强 ' + fmt(summary.boosted_signal_count || 0) + ' · 拥挤 ' + fmt(summary.crowded_signal_count || 0)) +
      renderScorecardMiniCard('加分股票', fmt(summary.score_up_count || 0), '均分差 ' + signedScore(summary.avg_score_delta)) +
      renderScorecardMiniCard('降分股票', fmt(summary.score_down_count || 0), '被热度和拥挤压制') +
      renderScorecardMiniCard('晋升A池', fmt(summary.promoted_to_a_count || 0), '晋升B池 ' + fmt(summary.promoted_to_b_count || 0)) +
      renderScorecardMiniCard('拥挤降级', fmt(summary.demoted_by_crowding_count || 0), '均折扣 ' + scoreNum(summary.avg_crowding_penalty)) +
      '</div>';

    var readinessNote = summary.replay_ready_20d
      ? 'attention 快照历史已达到 ' + fmt(summary.snapshot_days || 0) + ' 天，后续可以继续扩成 20/60 日严格前瞻回放。'
      : 'attention 快照历史当前只有 ' + fmt(summary.snapshot_days || 0) + ' 天（最新 ' + esc(fmtDate(summary.latest_snapshot_date)) + '），本阶段先做横截面校验，不把它伪装成前瞻回放。';

    var hintCard = '<div class="score-rule-card" style="margin-top:12px">' +
      '<div class="score-rule-title">校准结论</div>' +
      '<div class="scorecard-note">' + esc(calibration.methodology || '-') + '</div>' +
      '<div class="score-rule-list" style="margin-top:10px">' +
      '<div class="score-rule-item">' + esc(readinessNote) + '</div>' +
      hints.map(function (item) { return '<div class="score-rule-item">' + esc(item) + '</div>'; }).join('') +
      '</div>' +
      '</div>';

    var signalTable = signals.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">当前信号横截面</div>' +
      '<table class="score-pool-table"><thead><tr><th>信号</th><th>股票数</th><th>均确分</th><th>均折扣</th><th>均分差</th><th>近20日</th><th>胜率</th><th>晋A / 拥挤降级</th></tr></thead><tbody>' +
      signals.map(function (item) {
        return '<tr>' +
          '<td>' + (attentionSignalTag(item.signal_label) || esc(item.signal_label || '未触发')) + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_attention_score) + '</td>' +
          '<td>' + scoreNum(item.avg_crowding_penalty) + '</td>' +
          '<td>' + signedScore(item.avg_score_delta) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '<td>' + pct(item.win_rate_20d) + '</td>' +
          '<td>' + fmt(item.promoted_to_a_count || 0) + ' / ' + fmt(item.demoted_by_crowding_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    var scoreBandTable = scoreBands.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">Attention 分层校验</div>' +
      '<table class="score-pool-table"><thead><tr><th>分层</th><th>股票数</th><th>均折扣</th><th>均分差</th><th>近20日</th><th>胜率</th><th>晋A / 拥挤降级</th></tr></thead><tbody>' +
      scoreBands.map(function (item) {
        return '<tr>' +
          '<td>' + esc(item.band_label || '-') + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_crowding_penalty) + '</td>' +
          '<td>' + signedScore(item.avg_score_delta) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '<td>' + pct(item.win_rate_20d) + '</td>' +
          '<td>' + fmt(item.promoted_to_a_count || 0) + ' / ' + fmt(item.demoted_by_crowding_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    var penaltyBandTable = penaltyBands.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">拥挤折扣分层</div>' +
      '<table class="score-pool-table"><thead><tr><th>折扣层</th><th>股票数</th><th>均确分</th><th>均分差</th><th>近20日</th><th>胜率</th><th>加分 / 降分</th></tr></thead><tbody>' +
      penaltyBands.map(function (item) {
        return '<tr>' +
          '<td>' + esc(item.band_label || '-') + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_attention_score) + '</td>' +
          '<td>' + signedScore(item.avg_score_delta) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '<td>' + pct(item.win_rate_20d) + '</td>' +
          '<td>' + fmt(item.score_up_count || 0) + ' / ' + fmt(item.score_down_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    return '<div class="score-rule-card" style="margin-top:12px">' +
      '<div class="score-rule-title">外部关注校准</div>' +
      '<div class="scorecard-note">现在先校验阈值是否把强弱和拥挤层次分开，并明确说明当前还缺多少 attention 历史，避免把横截面结果误当成前瞻回放。</div>' +
      '</div>' +
      summaryCards +
      hintCard +
      '<div class="score-rule-grid" style="margin-top:12px">' + signalTable + scoreBandTable + '</div>' +
      '<div class="score-rule-grid" style="margin-top:12px">' + penaltyBandTable + '</div>';
  }

  function renderStockScorecardStats(stats) {
    if (!stats) return '';
    var summary = stats.summary || {};
    var currentPools = Array.isArray(stats.current_pools) ? stats.current_pools : [];
    var archetypes = Array.isArray(stats.archetypes) ? stats.archetypes : [];
    var attentionCalibration = stats.attention_calibration || null;
    var qualitySourceSummary = stats.quality_source_summary || {};
    var replay = stats.snapshot_replay || {};
    var coverage = replay.coverage || {};
    var baseline = replay.baseline || {};
    var byPool = Array.isArray(replay.by_pool) ? replay.by_pool : [];
    var snapshotPendingText = snapshotMaturityPendingText(
      {
        scored_snapshot_dates: summary.snapshot_scored_dates,
        scored_rows: summary.snapshot_scored_rows
      },
      {
        matured_10d_count: baseline.matured_10d_count,
        matured_30d_count: baseline.matured_30d_count,
        matured_60d_count: baseline.matured_60d_count
      }
    );
    var qualitySourceNoteParts = [];
    if ((qualitySourceSummary.quality_feature_v1_count || 0) > 0 || (qualitySourceSummary.stock_scoring_v2_count || 0) > 0) {
      qualitySourceNoteParts.push('质量快照 ' + fmt(qualitySourceSummary.quality_feature_v1_count || 0) + ' 只');
      qualitySourceNoteParts.push('评分兜底 ' + fmt(qualitySourceSummary.stock_scoring_v2_count || 0) + ' 只');
    }
    if ((qualitySourceSummary.other_source_count || 0) > 0) {
      qualitySourceNoteParts.push('其他来源 ' + fmt(qualitySourceSummary.other_source_count || 0) + ' 只');
    }
    if (qualitySourceSummary.latest_snapshot_date) {
      qualitySourceNoteParts.push('最近质量快照 ' + fmtDate(qualitySourceSummary.latest_snapshot_date));
    }
    var qualitySourceNote = qualitySourceNoteParts.length
      ? ('质量分来源已显式拆开：' + qualitySourceNoteParts.join(' · '))
      : '';
    var qualitySnapshotSubtext = (qualitySourceSummary.quality_feature_v1_count || 0) > 0
      ? (qualitySourceSummary.latest_snapshot_date ? ('最近 ' + fmtDate(qualitySourceSummary.latest_snapshot_date)) : '已有对齐快照')
      : '当前没有对齐快照';

    var summaryCards = '<div class="scorecard-stats-grid">' +
      renderScorecardMiniCard('当前覆盖', fmt(summary.stock_count || 0), '已进入四层评分的股票') +
      renderScorecardMiniCard('Setup候选', fmt(summary.setup_count || 0), '当前带 Setup 标签的股票') +
      renderScorecardMiniCard('封顶样本', fmt(summary.capped_count || 0), '触发阶段/质量封顶') +
      renderScorecardMiniCard('A池股票', fmt(summary.a_pool_count || 0), '当前重点优先池') +
      renderScorecardMiniCard('质量快照', fmt(qualitySourceSummary.quality_feature_v1_count || 0), qualitySnapshotSubtext) +
      renderScorecardMiniCard('评分兜底', fmt(qualitySourceSummary.stock_scoring_v2_count || 0), '未命中对齐快照时回退 stock_scoring_v2') +
      renderScorecardMiniCard('快照样本', fmt(summary.snapshot_scored_rows || 0), '已写入四层主分的快照行') +
      renderScorecardMiniCard('快照日', fmt(summary.snapshot_scored_dates || 0), esc((summary.first_scored_snapshot_date || '-') + ' ~ ' + (summary.last_scored_snapshot_date || '-'))) +
      '</div>';

    var poolTable = currentPools.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">当前池子分布</div>' +
      '<table class="score-pool-table"><thead><tr><th>池子</th><th>股票数</th><th>Setup</th><th>封顶</th><th>均综合</th><th>均质量</th><th>均阶段</th><th>近20日反馈</th></tr></thead><tbody>' +
      currentPools.map(function (item) {
        return '<tr>' +
          '<td>' + priorityPoolTag(item.priority_pool) + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + fmt(item.setup_count) + '</td>' +
          '<td>' + fmt(item.capped_count) + '</td>' +
          '<td>' + scoreNum(item.avg_composite_score) + '</td>' +
          '<td>' + scoreNum(item.avg_quality_score) + '</td>' +
          '<td>' + scoreNum(item.avg_stage_score) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    var archetypeTable = archetypes.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">股票类型分布</div>' +
      '<table class="score-pool-table"><thead><tr><th>类型</th><th>股票数</th><th>A池</th><th>均质量</th><th>均阶段</th><th>均综合</th></tr></thead><tbody>' +
      archetypes.map(function (item) {
        return '<tr>' +
          '<td>' + esc(item.stock_archetype || '待分类') + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + fmt(item.a_pool_count) + '</td>' +
          '<td>' + scoreNum(item.avg_quality_score) + '</td>' +
          '<td>' + scoreNum(item.avg_stage_score) + '</td>' +
          '<td>' + scoreNum(item.avg_composite_score) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    var replayTable = byPool.length
      ? '<div class="score-rule-card">' +
      '<div class="score-rule-title">快照回放摘要</div>' +
      '<div class="scorecard-note">全量快照基线：10日 ' + fmtGain(baseline.avg_gain_10d) + ' · 30日 ' + fmtGain(baseline.avg_gain_30d) + ' · 60日 ' + fmtGain(baseline.avg_gain_60d) + '</div>' +
      (snapshotPendingText ? '<div class="scorecard-note">' + esc(snapshotPendingText) + '</div>' : '') +
      '<table class="score-pool-table"><thead><tr><th>池子</th><th>快照日</th><th>10日</th><th>30日</th><th>60日</th></tr></thead><tbody>' +
      byPool.map(function (item) {
        return '<tr>' +
          '<td>' + priorityPoolTag(item.priority_pool) + '</td>' +
          '<td>' + fmt(item.snapshot_days) + '</td>' +
          '<td><div>' + fmtGain(item.avg_gain_10d) + '</div><div class="muted" style="font-size:10px">样本 ' + fmt(item.matured_10d_count || 0) + ' · 胜率 ' + pct(item.win_rate_10d) + '</div></td>' +
          '<td><div>' + fmtGain(item.avg_gain_30d) + '</div><div class="muted" style="font-size:10px">样本 ' + fmt(item.matured_30d_count || 0) + ' · 胜率 ' + pct(item.win_rate_30d) + '</div></td>' +
          '<td><div>' + fmtGain(item.avg_gain_60d) + '</div><div class="muted" style="font-size:10px">样本 ' + fmt(item.matured_60d_count || 0) + ' · 胜率 ' + pct(item.win_rate_60d) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>' +
      '</div>'
      : '';

    return '<div class="score-rule-card" style="margin-bottom:12px">' +
      '<div class="score-rule-title">当前样本与回放摘要</div>' +
      '<div class="scorecard-note">评分卡现在不只解释公式，也直接展示当前分池结构、股票类型分布和快照后验摘要，帮助判断这套口径在当前样本上的真实形态。</div>' +
      (qualitySourceNote ? '<div class="scorecard-note">' + esc(qualitySourceNote) + '</div>' : '') +
      '</div>' +
      summaryCards +
      (attentionCalibration ? renderStockAttentionCalibration(attentionCalibration) : '') +
      '<div class="score-rule-grid" style="margin-top:12px">' + poolTable + archetypeTable + '</div>' +
      replayTable;
  }

  function renderScorecardMiniCard(label, value, sub) {
    return '<div class="scorecard-mini-card">' +
      '<div class="scorecard-mini-label">' + esc(label || '-') + '</div>' +
      '<div class="scorecard-mini-value">' + value + '</div>' +
      '<div class="scorecard-mini-sub">' + esc(sub || '-') + '</div>' +
      '</div>';
  }

  async function loadInstScorecard() {
    var rs = await Promise.all([
      api('/api/inst/scoring/framework/institution'),
      api('/api/inst/scoring/config/institution'),
      api('/api/inst/scoring/framework/followability'),
      api('/api/inst/scoring/config/followability')
    ]);
    var instFw = rs[0]?.ok ? (rs[0].data || {}) : {};
    var instStats = rs[0]?.ok ? (rs[0].stats || {}) : {};
    var instCfg = rs[1]?.ok ? rs[1] : {};
    var followFw = rs[2]?.ok ? (rs[2].data || {}) : {};
    var followCfg = rs[3]?.ok ? rs[3] : {};

    el('instScorecardFramework').innerHTML =
      '<div style="background:var(--cm-bg);border:1px solid var(--cm-ink-100);border-radius:12px;padding:14px 16px;margin-bottom:14px;font-size:12px;color:var(--cm-ink-700);line-height:1.7">' +
      '<div style="font-size:14px;font-weight:700;color:var(--cm-ink-900);margin-bottom:6px">机构评分双框架</div>' +
      '机构页当前同时维护“机构实力评分”和“可跟性评分”。前者回答机构信号本身好不好，后者回答普通跟随者是否容易复现。' +
      '</div>' +
      '<div class="score-framework-grid">' + (instFw.layers || []).map(renderStockFrameworkLayer).join('') + '</div>' +
      renderInstFrameworkRules(instFw) +
      '<div class="score-framework-grid" style="margin-top:14px">' + (followFw.layers || []).map(renderStockFrameworkLayer).join('') + '</div>' +
      renderInstFrameworkRules(followFw);

    el('instScorecardStats').innerHTML = renderInstScorecardStats(instStats);

    el('instScorecardParams').innerHTML =
      '<div class="score-rule-grid">' +
      renderScoreParamCard('instInstitutionParams', instFw, instCfg.config || {}, instCfg.defaults || {}) +
      renderScoreParamCard('instFollowabilityParams', followFw, followCfg.config || {}, followCfg.defaults || {}) +
      '</div>';
  }

  // Step 5 任务 4：scorecard 入口（loadStockScorecard / calcInstScore /
  // resetInstScore / calcStockScore / resetStockScore）已随机构/股票评分卡 UI 删除而下线。

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
    // 机构维度切换（overview / returns / risk）
    document.querySelectorAll('.inst-dim-switch .dim-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { switchInstDim(btn.dataset.dim); });
    });
    // 机构页面胶囊标签：列表 | 管理
    var instMgmtLoaded = false;
    document.querySelectorAll('#view-research .inst-page-tabs .chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tab = btn.dataset.itab;
        document.querySelectorAll('#view-research .inst-page-tabs .chip').forEach(function (b) {
          b.classList.toggle('chip-primary', b.dataset.itab === tab);
          b.classList.toggle('chip-outline', b.dataset.itab !== tab);
        });
        el('itab-list').style.display = tab === 'list' ? '' : 'none';
        el('itab-manage').style.display = tab === 'manage' ? '' : 'none';
        if (tab === 'manage' && !instMgmtLoaded) {
          instMgmtLoaded = true;
          loadInstMgmt && loadInstMgmt();
        }
      });
    });
    el('btnReset')?.addEventListener('click', resetDerivedData);
    el('btnSearchInst')?.addEventListener('click', searchInst);
    el('btnImportChecked')?.addEventListener('click', importChecked);
    el('mgmtSearch')?.addEventListener('keydown', function (e) { if (e.key === 'Enter') searchInst(); });
    el('btnBatchAlias')?.addEventListener('click', batchAlias);
    el('btnBatchType')?.addEventListener('click', batchType);
    el('btnBatchMerge')?.addEventListener('click', batchMerge);
    el('btnBatchBlack')?.addEventListener('click', batchBlack);
    el('btnBatchDelete')?.addEventListener('click', batchDelete);
    el('stockSearch')?.addEventListener('input', handleStockSearchInput);
    el('instSearch')?.addEventListener('input', filterInstList);
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
  function etfPctCell(v, invert) {
    if (v == null || Number.isNaN(Number(v))) return '<span class="muted">-</span>';
    var n = Number(v);
    var positive = invert ? n <= 0 : n >= 0;
    var cls = positive ? 'gain-pos' : 'gain-neg';
    var sign = n > 0 ? '+' : '';
    return '<span class="' + cls + '">' + sign + n.toFixed(2) + '%</span>';
  }
  function etfOverviewTone(state) {
    if (state === 'panic') return { bg: 'var(--cm-brand-50)', fg: 'var(--cm-brand-500)', label: '恐慌待托底' };
    if (state === 'cooling') return { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)', label: '降温观察期' };
    if (state === 'heated') return { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)', label: '兑现降温期' };
    return { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)', label: '趋势恢复期' };
  }
  function etfStrategyTone(kind) {
    if (kind === '买入持有' || kind === '趋势持有') return { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' };
    if (kind === '网格交易' || kind === '网格候选') return { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' };
    if (kind === '防守停泊') return { bg: 'var(--cm-bg)', fg: 'var(--cm-ink-700)' };
    if (kind === '暂不参与') return { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' };
    return { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' };
  }
  function etfSetupTone(state) {
    if (state === '收敛待发') return { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' };
    if (state === '趋势跟随') return { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' };
    if (state === '低波防守') return { bg: 'var(--cm-bg)', fg: 'var(--cm-ink-700)' };
    if (state === '结构松散') return { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' };
    return { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' };
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
      var attr = clickable ? ' data-etf-analyze="' + esc(item.code) + '"' : '';
      return '<span' + attr + ' style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:' + meta.bg + ';color:' + meta.fg + ';font-size:11px;font-weight:600;margin:4px 6px 0 0' + (clickable ? ';cursor:pointer' : '') + '">' +
        esc((item.name || item.code || '-') + (extra.length ? ' · ' + extra.join(' · ') : '')) +
        '</span>';
    }).join('');
  }
  function etfCatColor(cat) {
    var map = {
      '宽基': 'var(--cm-brand-500)', '跨境': 'var(--cm-accent-vivid)', '商品': 'var(--cm-warn-500)', '债券': 'var(--cm-ink-500)', '货币': 'var(--cm-ink-300)',
      '医疗健康': 'var(--cm-ok-500)', '半导体': 'var(--cm-brand-400)', '新能源': 'var(--cm-ok-500)', '消费': 'var(--cm-warn-500)',
      '金融': 'var(--cm-bad-500)', '军工': 'var(--cm-ink-700)', '地产建筑': 'var(--cm-ink-500)', '周期资源': 'var(--cm-warn-500)',
      '数字科技': 'var(--cm-brand-500)', '交通物流': 'var(--cm-brand-700)', '电力公用': 'var(--cm-brand-500)', '汽车': 'var(--cm-warn-500)',
      '高端制造': 'var(--cm-brand-500)', '红利策略': 'var(--cm-accent-vivid)'
    };
    return map[cat] || 'var(--cm-brand-700)';
  }

  function bindEtfActionLinks(root, deepPanelId) {
    if (!root) return;
    root.querySelectorAll('[data-etf-analyze]').forEach(function (node) {
      node.addEventListener('click', function () {
        loadEtfDeepAnalysis(node.dataset.etfAnalyze, deepPanelId || 'etfDeepAnalysisPanel');
      });
    });
    root.querySelectorAll('[data-etf-category]').forEach(function (node) {
      node.addEventListener('click', function () {
        etfState.categoryFilter = node.dataset.etfCategory || 'all';
        etfState.strategyFilter = 'all';
        showEtfTab('list');
      });
    });
    root.querySelectorAll('[data-etf-strategy]').forEach(function (node) {
      node.addEventListener('click', function () {
        etfState.strategyFilter = node.dataset.etfStrategy || 'all';
        etfState.categoryFilter = 'all';
        showEtfTab('list');
      });
    });
    root.querySelectorAll('[data-etf-tab]').forEach(function (node) {
      node.addEventListener('click', function () {
        showEtfTab(node.dataset.etfTab);
      });
    });
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

    bindEtfActionLinks(box);
  }

  function loadEtfOpportunity() {
    var r = etfState.dataCache;
    if (!r?.data?.length) return;
    var opportunityBox = el('etfOpportunityContainer');
    if (!opportunityBox) return;
    var ov = r.overview || {};
    var tone = etfOverviewTone(ov.market_state);
    var leadersHtml = etfWatchTags(ov.rotation_leaders, { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' }, 'analyze');
    var laggardsHtml = etfWatchTags(ov.rotation_laggards, { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' }, 'analyze');

    // 策略计数卡 — 可点击跳转到 ETF 列表并过滤
    function stratBtn(label, count, stratType, color) {
      return '<div class="stat-card" style="cursor:pointer" data-etf-strategy="' + esc(stratType) + '" title="点击查看">' +
        '<div class="stat-value" style="color:' + color + '">' + esc(fmt(count || 0)) + '</div>' +
        '<div class="stat-label">' + esc(label) + '</div></div>';
    }

    opportunityBox.innerHTML =
      '<div class="panel" style="margin-bottom:0">' +
      '<div class="panel-head" style="align-items:flex-start;gap:14px;flex-wrap:wrap">' +
      '<div style="min-width:280px;flex:1">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">' +
      '<span style="font-weight:700;font-size:15px">ETF 机会发现</span>' +
      '<span style="padding:4px 10px;border-radius:999px;background:' + tone.bg + ';color:' + tone.fg + ';font-size:12px;font-weight:700">' + esc(ov.regime_label || tone.label) + '</span>' +
      '<span class="muted">温度 ' + esc(etfNum(ov.temperature_score, 1)) + '</span>' +
      '</div>' +
      '<div style="font-size:13px;line-height:1.7;color:var(--cm-ink-700)">' + esc(ov.regime_reason || '暂无整体判断。') + '</div>' +
      '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-900)"><strong>当前动作：</strong>' + esc(ov.action_hint || '-') + '</div>' +
      '<div style="margin-top:6px;font-size:12px;color:var(--cm-ink-500)"><strong>低频情景：</strong>' + esc(ov.macro_scenario || '-') + '。' + esc(ov.macro_note || '') + '</div>' +
      '<div style="margin-top:10px;font-size:12px;color:var(--cm-ink-900)"><strong>轮动规则：</strong>' + esc(ov.rotation_rule || '-') + '</div>' +
      '<div style="margin-top:10px;font-size:12px;color:var(--cm-ink-900)"><strong>关注名单：</strong>' + leadersHtml + '</div>' +
      '<div style="margin-top:6px;font-size:12px;color:var(--cm-ink-900)"><strong>回避名单：</strong>' + laggardsHtml + '</div>' +
      '</div>' +
      '<div style="min-width:240px;flex:1">' +
      '<div class="stats-row" style="grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:10px">' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(ov.positive_20d_ratio, 0)) + '%</div><div class="stat-label">宽基上涨占比</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(ov.avg_momentum_20d, 1)) + '%</div><div class="stat-label">平均20日动量</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(ov.avg_momentum_60d, 1)) + '%</div><div class="stat-label">平均60日动量</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(ov.avg_volatility_20d, 1)) + '%</div><div class="stat-label">平均20日波动</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(ov.avg_drawdown_60d, 1)) + '%</div><div class="stat-label">平均60日回撤</div></div>' +
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
      '<div id="opportunityMiningSection" style="margin-top:14px"></div>' +
      '<div id="opportunityRotationSection" style="margin-top:14px"></div>' +
      '<div id="opportunityDeepPanel"></div>';

    bindEtfActionLinks(opportunityBox, 'opportunityDeepPanel');

    // 加载挖掘建议 (grid / trend top5)
    _loadEtfOpportunityMining();
    // 加载板块轮动 widget (新 mart_etf_sector_rotation 数据源)
    if (window.ETFSectorRotationWidget) {
      window.ETFSectorRotationWidget.mount('opportunityRotationSection', {
        limit: 15,
        onPickETF: function (code) { loadEtfDeepAnalysis(code, 'opportunityDeepPanel'); }
      });
    }
  }

  // 机会发现页 — 挖掘建议 + 轮动预测
  async function _loadEtfOpportunityMining() {
    var miningBox = el('opportunityMiningSection');
    var rotationBox = el('opportunityRotationSection');
    if (!miningBox) return;
    miningBox.innerHTML = '<div class="muted" style="padding:10px">加载挖掘建议...</div>';
    if (rotationBox) rotationBox.innerHTML = '';

    var r = await api('/api/etf/mining?grid_topn=5&trend_topn=5&rotation_topn=5');
    if (r?.status !== 'ok' || !r?.data) {
      miningBox.innerHTML = '<div class="muted">挖掘建议加载失败</div>';
      return;
    }
    var d = r.data || {};

    // --- 网格交易 + 买入持有 ---
    function miningRow(title, sub, palette, extra) {
      return '<div style="padding:8px 10px;border-radius:10px;background:' + palette.bg + ';color:' + palette.fg + ';margin-bottom:6px;' + (extra?.cursor ? 'cursor:pointer' : '') + '"' + (extra?.attr || '') + '>' +
        '<div style="font-weight:700;font-size:12px">' + esc(title) + '</div>' +
        (sub ? '<div style="font-size:11px;line-height:1.6;margin-top:3px">' + sub + '</div>' : '') +
        '</div>';
    }

    var gridCards = (d.grid_candidates || []).map(function (item) {
      return miningRow(
        (item.name || item.code) + ' · 步长 ' + scoreNum(item.best_step_pct) + '%',
        esc('收益 ' + signedPct(item.backtest_return_pct) + ' · 超额 ' + signedPct(item.backtest_excess_pct) + ' · DD ' + pct(item.backtest_max_drawdown_pct)) +
        ' <span style="opacity:0.6;font-size:10px">▶ 深度分析</span>',
        { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' },
        { cursor: true, attr: ' data-etf-analyze="' + esc(item.code) + '"' }
      );
    }).join('');

    var trendCards = (d.trend_candidates || []).map(function (item) {
      return miningRow(
        (item.name || item.code) + ' · ' + (item.action || '观察'),
        esc('4w ' + signedPct(item.relative_strength_4w) + ' / 12w ' + signedPct(item.relative_strength_12w) + ' · 因子 ' + etfNum(item.factor_score, 1)) +
        ' <span style="opacity:0.6;font-size:10px">▶ 深度分析</span>',
        { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' },
        { cursor: true, attr: ' data-etf-analyze="' + esc(item.code) + '"' }
      );
    }).join('');

    miningBox.innerHTML =
      '<div class="finance-module-grid">' +
      '<div class="finance-module-card">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px"><div style="font-weight:700;font-size:13px">网格交易 Top 5</div><span class="finance-note">仅保留自动寻优后跑赢持有的可执行网格</span></div>' +
      (gridCards || '<div class="muted" style="font-size:12px">暂无</div>') +
      '</div>' +
      '<div class="finance-module-card">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px"><div style="font-weight:700;font-size:13px">买入持有 Top 5</div><span class="finance-note">趋势占优时直接展示持有结论，不再暗示网格应当取胜</span></div>' +
      (trendCards || '<div class="muted" style="font-size:12px">暂无</div>') +
      '</div>' +
      '</div>';

    // 绑定深度分析点击
    miningBox.querySelectorAll('[data-etf-analyze]').forEach(function (card) {
      card.addEventListener('click', function () {
        loadEtfDeepAnalysis(card.dataset.etfAnalyze, 'opportunityDeepPanel');
      });
    });
    // 轮动预测已迁到 ETFSectorRotationWidget (mart_etf_sector_rotation 数据源)
    // 在 loadEtfOpportunity 里另行挂载, 本函数不再渲染 rotationBox
  }

  function loadEtfList() {
    var r = etfState.dataCache;
    if (!r?.data?.length) return;
    var c = el('etfTableContainer');
    var filterBox = el('etfCategoryFilter');
    if (!c) return;

    // 提取所有分类并构建胶囊标签
    var categories = [];
    var catSet = {};
    r.data.forEach(function (e) {
      var cat = e.category || '其他';
      if (!catSet[cat]) { catSet[cat] = 0; }
      catSet[cat]++;
    });
    categories = Object.keys(catSet).sort(function (a, b) {
      var order = ['宽基', '医疗健康', '半导体', '新能源', '消费', '金融', '军工', '数字科技', '高端制造', '汽车', '电力公用', '地产建筑', '周期资源', '交通物流', '红利策略', '行业·其他', '跨境', '商品', '债券', '货币'];
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

    if (filterBox) {
      // 分类过滤行
      var filterHtml = '<div class="type-filter">';
      filterHtml += '<span class="type-tag' + (etfState.categoryFilter === 'all' ? ' active' : '') + '" data-etfcat="all">全部 (' + r.data.length + ')</span>';
      categories.forEach(function (cat) {
        var color = etfCatColor(cat);
        filterHtml += '<span class="type-tag' + (etfState.categoryFilter === cat ? ' active' : '') + '" data-etfcat="' + esc(cat) + '" style="--tc:' + color + '">' + esc(cat) + ' (' + catSet[cat] + ')</span>';
      });
      filterHtml += '</div>';
      // 策略过滤行
      var stratTypes = ['买入持有', '网格交易', '防守停泊', '暂不参与'];
      filterHtml += '<div class="type-filter" style="margin-top:4px">';
      filterHtml += '<span class="type-tag' + (etfState.strategyFilter === 'all' ? ' active' : '') + '" data-etfstrat="all" style="font-size:11px">策略:全部</span>';
      stratTypes.forEach(function (s) {
        var st = etfStrategyTone(s);
        filterHtml += '<span class="type-tag' + (etfState.strategyFilter === s ? ' active' : '') + '" data-etfstrat="' + esc(s) + '" style="font-size:11px;--tc:' + st.fg + '">' + esc(s) + '</span>';
      });
      filterHtml += '</div>';
      filterBox.innerHTML = filterHtml;
      filterBox.querySelectorAll('[data-etfcat]').forEach(function (tag) {
        tag.addEventListener('click', function () {
          etfState.categoryFilter = tag.dataset.etfcat;
          loadEtfList();
        });
      });
      filterBox.querySelectorAll('[data-etfstrat]').forEach(function (tag) {
        tag.addEventListener('click', function () {
          etfState.strategyFilter = tag.dataset.etfstrat;
          loadEtfList();
        });
      });
    }

    // 过滤数据
    var filtered = r.data;
    if (etfState.categoryFilter !== 'all') filtered = filtered.filter(function (e) { return e.category === etfState.categoryFilter; });
    if (etfState.strategyFilter !== 'all') filtered = filtered.filter(function (e) { return e.strategy_type === etfState.strategyFilter; });

    var head = '<table class="data-table"><thead><tr><th>名称</th><th>代码</th><th>分类</th><th>4周相强</th><th>12周相强</th><th>轮动分</th><th>网格收益</th><th>持有收益</th><th>超额</th><th>日线结构</th><th>策略类型</th><th>参考步长</th><th>趋势</th></tr></thead><tbody>';
    var body = filtered.map(function (e) {
      var catColor = etfCatColor(e.category);
      var trendColor = e.trend_status === '多头' ? 'var(--stock-up)' : (e.trend_status === '空头' ? 'var(--stock-down)' : 'var(--cm-ink-500)');
      var strategyTone = etfStrategyTone(e.strategy_type);
      var setupTone = etfSetupTone(e.setup_state);
      var rotationText = e.rotation_score != null ? etfNum(e.rotation_score, 1) + (e.rotation_bucket === 'leader' ? ' · 前排' : e.rotation_bucket === 'blacklist' ? ' · 回避' : '') : '—';
      var excessColor = e.backtest_excess_pct == null ? 'var(--cm-ink-500)' : (e.backtest_excess_pct >= 0 ? 'var(--cm-ok-500)' : 'var(--cm-bad-500)');
      return '<tr style="cursor:pointer" data-etf-code="' + esc(e.code) + '">' +
        '<td style="font-weight:600">' + esc(e.name) + '</td>' +
        '<td>' + xueqiuPillLink(e.code, e.code, true) + '</td>' +
        '<td><span style="padding:2px 8px;border-radius:999px;background:' + catColor + '14;color:' + catColor + ';font-size:11px;font-weight:600">' + esc(e.category) + '</span></td>' +
        '<td>' + etfPctCell(e.relative_strength_4w, false) + '</td>' +
        '<td>' + etfPctCell(e.relative_strength_12w, false) + '</td>' +
        '<td>' + esc(rotationText) + '</td>' +
        '<td>' + (e.backtest_return_pct != null ? signedPct(e.backtest_return_pct) : '<span class="muted">-</span>') + '</td>' +
        '<td>' + (e.buy_hold_return_pct != null ? signedPct(e.buy_hold_return_pct) : '<span class="muted">-</span>') + '</td>' +
        '<td style="color:' + excessColor + ';font-weight:700">' + (e.backtest_excess_pct != null ? signedPct(e.backtest_excess_pct) : '<span class="muted">-</span>') + '</td>' +
        '<td><span style="padding:2px 8px;border-radius:999px;background:' + setupTone.bg + ';color:' + setupTone.fg + ';font-size:11px;font-weight:600">' + esc(e.setup_state || '-') + '</span></td>' +
        '<td><span style="padding:2px 8px;border-radius:999px;background:' + strategyTone.bg + ';color:' + strategyTone.fg + ';font-size:11px;font-weight:600" title="' + esc(e.strategy_reason || '') + '">' + esc(e.strategy_type || '-') + '</span></td>' +
        '<td>' + (e.grid_step_pct != null ? esc(etfNum(e.grid_step_pct, 1) + '%') : '<span class="muted">-</span>') + '</td>' +
        '<td style="color:' + trendColor + '">' + esc(e.trend_status) + '</td>' +
        '</tr>';
    }).join('');
    c.innerHTML = head + body + '</tbody></table>';
    scheduleSortableTables('etfTableContainer');
    // 点击行 → 在该行下方插入深度分析面板
    c.querySelectorAll('tr[data-etf-code]').forEach(function (row) {
      row.addEventListener('click', function () {
        var code = row.dataset.etfCode;
        // 移除已有的分析行
        var prev = c.querySelector('.etf-analysis-row');
        if (prev) prev.remove();
        // 在点击行后插入新的分析行
        var analysisRow = document.createElement('tr');
        analysisRow.className = 'etf-analysis-row';
        var td = document.createElement('td');
        td.colSpan = 13;
        td.id = 'etfListAnalysisPanel';
        td.style.padding = '0';
        td.style.background = 'var(--bg-subtle)';
        analysisRow.appendChild(td);
        row.parentNode.insertBefore(analysisRow, row.nextSibling);
        loadEtfDeepAnalysis(code, 'etfListAnalysisPanel');
      });
    });
  }

  // ============================================================
  // ETF 深度量化分析面板
  // ============================================================
  async function loadEtfDeepAnalysis(code, panelId) {
    panelId = panelId || 'etfDeepAnalysisPanel';
    var panel = el(panelId);
    if (!panel) return;
    panel.innerHTML = '<div class="panel" style="margin-top:14px"><div class="muted" style="padding:20px;text-align:center">加载 ' + esc(code) + ' 深度分析中...</div></div>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    var r = await api('/api/etf/analysis/' + encodeURIComponent(code));
    if (r?.status !== 'ok' || !r?.data) {
      panel.innerHTML = '<div class="panel" style="margin-top:14px"><div class="muted" style="padding:20px;text-align:center">分析失败: ' + esc(r?.message || r?.detail || '未知错误') + '</div></div>';
      return;
    }
    var d = r.data;
    var info = d.info || {};
    var optimizerSummary = d.optimizer_summary || {};
    var verdict = d.verdict || {};
    var best = d.best_step || {};
    var bh = d.buy_hold || {};
    var tradeabilityOk = !info.tradeability_status || info.tradeability_status === 'ok';
    var issueHtml = '';
    if (!tradeabilityOk) {
      issueHtml = '<div style="margin-bottom:14px;padding:14px;border:1px solid var(--cm-bad-100);border-radius:14px;background:var(--cm-bad-100);color:var(--cm-bad-500);font-size:12px;line-height:1.8">' +
        '<div style="font-weight:700;margin-bottom:6px">该产品已从 ETF 可交易池中剔除</div>' +
        '<div>' + esc(info.tradeability_reason || '该产品未通过 ETF 基础交易性检查。') + '</div>' +
        '</div>';
    }

    // --- 1. 头部信息卡 ---
    var ratingColors = { '强烈推荐': { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' }, '推荐': { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' }, '中性': { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' }, '谨慎': { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' } };
    var rc = ratingColors[verdict.rating] || ratingColors['中性'];
    var recStrategy = d.recommended_strategy || '';
    var recStrategyTone = etfStrategyTone(recStrategy);
    var headerHtml =
      '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">' +
      securityIdentityBlock(code, info.name || code, { wrapperClass: 'security-identity security-identity--hero', includeMarketLink: true, includeXueqiuPill: true }) +
      '<span style="padding:3px 12px;border-radius:var(--radius-pill);background:' + rc.bg + ';color:' + rc.fg + ';font-size:12px;font-weight:700">' + esc(verdict.rating || '分析中') + '</span>' +
      (recStrategy ? '<span style="padding:3px 10px;border-radius:var(--radius-pill);background:' + recStrategyTone.bg + ';color:' + recStrategyTone.fg + ';font-size:11px;font-weight:700">推荐: ' + esc(recStrategy) + '</span>' : '') +
      (info.strategy_type ? '<span style="padding:2px 8px;border-radius:var(--radius-pill);background:var(--primary-light);color:var(--primary);font-size:11px;font-weight:600">' + esc(info.strategy_type) + '</span>' : '') +
      (info.setup_state ? '<span style="padding:2px 8px;border-radius:var(--radius-pill);background:var(--line-light);color:var(--text-2);font-size:11px;font-weight:600">' + esc(info.setup_state) + '</span>' : '') +
      '</div>';

    // --- 2. 核心指标对比卡 ---
    function metricCard(label, gridVal, bhVal, unit, better) {
      var gv = gridVal != null ? (typeof gridVal === 'number' ? gridVal.toFixed(2) : gridVal) : '-';
      var bv = bhVal != null ? (typeof bhVal === 'number' ? bhVal.toFixed(2) : bhVal) : '-';
      var gColor = 'var(--text)';
      var bColor = 'var(--text)';
      if (better === 'higher' && gridVal != null && bhVal != null) {
        gColor = gridVal >= bhVal ? 'var(--success)' : 'var(--muted)';
        bColor = bhVal >= gridVal ? 'var(--success)' : 'var(--muted)';
      } else if (better === 'lower' && gridVal != null && bhVal != null) {
        gColor = gridVal <= bhVal ? 'var(--success)' : 'var(--danger)';
        bColor = bhVal <= gridVal ? 'var(--success)' : 'var(--danger)';
      }
      return '<div style="text-align:center;min-width:100px">' +
        '<div class="muted" style="font-size:10px;margin-bottom:4px">' + esc(label) + '</div>' +
        '<div style="font-size:14px;font-weight:700;color:' + gColor + '">' + gv + (unit || '') + '</div>' +
        '<div style="font-size:11px;color:' + bColor + '">' + bv + (unit || '') + '</div>' +
        '</div>';
    }

    function statusPill(label, passed, title) {
      var bg = passed ? 'var(--cm-ok-100)' : 'var(--cm-bad-100)';
      var fg = passed ? 'var(--cm-ok-500)' : 'var(--cm-bad-500)';
      return '<span title="' + esc(title || '') + '" style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:' + bg + ';color:' + fg + ';font-size:11px;font-weight:700">' + esc(label) + '</span>';
    }

    function ledgerMetric(label, value, note) {
      return '<div style="min-width:110px;flex:1 1 110px">' +
        '<div class="muted" style="font-size:10px;margin-bottom:4px">' + esc(label) + '</div>' +
        '<div style="font-size:13px;font-weight:700;color:var(--text)">' + value + '</div>' +
        (note ? '<div class="muted" style="font-size:10px;margin-top:3px">' + esc(note) + '</div>' : '') +
        '</div>';
    }

    function ledgerCard(title, target, isGrid) {
      if (!target || !Object.keys(target).length) return '';
      var audit = target.audit || {};
      var failures = audit.failures || [];
      var gate = isGrid
        ? statusPill(target.hard_gate_passed ? '可执行' : '已淘汰', !!target.hard_gate_passed, target.hard_gate_reason || '')
        : statusPill(audit.audit_passed ? '基准通过' : '基准异常', !!audit.audit_passed, failures.join('；'));
      var auditPill = statusPill(audit.audit_passed ? '账本通过' : '账本异常', !!audit.audit_passed, failures.join('；'));
      var subtitle = [];
      if (target.initial_position_ratio_pct != null) subtitle.push('初始底仓 ' + Number(target.initial_position_ratio_pct).toFixed(1) + '%');
      if (target.lot_size != null) subtitle.push('整手 ' + fmt(target.lot_size));
      if (target.tranche_count != null) subtitle.push('分仓 ' + fmt(target.tranche_count));
      return '<div style="flex:1 1 320px;padding:12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg-subtle)">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px">' +
        '<div><div style="font-size:13px;font-weight:700">' + esc(title) + '</div>' +
        (subtitle.length ? '<div class="muted" style="font-size:11px;margin-top:3px">' + esc(subtitle.join(' · ')) + '</div>' : '') +
        '</div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">' + gate + auditPill + '</div>' +
        '</div>' +
        '<div style="display:flex;gap:12px;flex-wrap:wrap">' +
        ledgerMetric('买入总投入', fmtCurrency(target.buy_cash_total), '含手续费') +
        ledgerMetric('卖出回笼', fmtCurrency(target.sell_net_total), '卖出后净回款') +
        ledgerMetric('期末现金', fmtCurrency(target.final_cash), '现金账本') +
        ledgerMetric('期末持仓市值', fmtCurrency(target.final_market_value), '剩余仓位') +
        ledgerMetric('已实现盈亏', fmtCurrency(target.realized_pnl), '已平仓部分') +
        ledgerMetric('未实现盈亏', fmtCurrency(target.unrealized_pnl), '未平仓部分') +
        ledgerMetric('最大资金占用', pct(target.peak_deployed_pct), fmtCurrency(target.peak_deployed_capital)) +
        '</div>' +
        (failures.length ? '<div style="margin-top:10px;font-size:11px;color:var(--danger);line-height:1.6">淘汰/异常原因：' + esc(failures.join('；')) + '</div>' : '') +
        (isGrid && target.hard_gate_passed === false && target.hard_gate_reason ? '<div style="margin-top:8px;font-size:11px;color:var(--danger);line-height:1.6">硬约束结论：' + esc(target.hard_gate_reason) + '</div>' : '') +
        '</div>';
    }

    var comparisonHtml = '';
    if (tradeabilityOk && (Object.keys(best).length || Object.keys(bh).length)) {
      comparisonHtml =
        '<div style="margin-bottom:14px">' +
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">' +
        '<span style="font-weight:600;font-size:13px">核心指标对比</span>' +
        '<span style="padding:2px 8px;border-radius:var(--radius-pill);background:var(--cm-brand-100);color:var(--cm-brand-500);font-size:10px;font-weight:600">网格 ' + (best.step_pct || '-') + '%</span>' +
        '<span style="padding:2px 8px;border-radius:var(--radius-pill);background:var(--line-light);color:var(--text-2);font-size:10px;font-weight:600">买入持有</span>' +
        '</div>' +
        '<div style="display:flex;gap:4px;flex-wrap:wrap;padding:12px;background:var(--bg-subtle);border-radius:var(--radius);border:1px solid var(--line)">' +
        metricCard('总收益', best.return_pct, bh.return_pct, '%', 'higher') +
        metricCard('年化收益', best.annual_return_pct, bh.annual_return_pct, '%', 'higher') +
        metricCard('最大回撤', best.max_drawdown_pct, bh.max_drawdown_pct, '%', 'lower') +
        metricCard('Sharpe', best.sharpe, bh.sharpe, '', 'higher') +
        metricCard('Calmar', best.calmar, bh.calmar, '', 'higher') +
        metricCard('胜率', best.win_rate, null, '%', null) +
        metricCard('交易次数', best.trade_count, null, '', null) +
        metricCard('回测天数', best.days, bh.days, '天', null) +
        '</div></div>';
    }

    var optimizerHtml = '';
    if (optimizerSummary.candidate_step_count != null) {
      optimizerHtml = '<div style="margin-bottom:14px;padding:12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--cm-macaron-cream)">' +
        '<div style="font-weight:700;font-size:13px;margin-bottom:8px">寻优模型约束</div>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">' +
        '<span style="padding:2px 8px;border-radius:999px;background:var(--cm-brand-100);color:var(--cm-brand-700);font-size:11px;font-weight:700">候选步长 ' + fmt(optimizerSummary.candidate_step_count || 0) + '</span>' +
        '<span style="padding:2px 8px;border-radius:999px;background:var(--cm-ok-100);color:var(--cm-ok-500);font-size:11px;font-weight:700">通过硬约束 ' + fmt(optimizerSummary.valid_step_count || 0) + '</span>' +
        '<span style="padding:2px 8px;border-radius:999px;background:var(--cm-bad-100);color:var(--cm-bad-500);font-size:11px;font-weight:700">淘汰 ' + fmt(optimizerSummary.rejected_step_count || 0) + '</span>' +
        '<span style="padding:2px 8px;border-radius:999px;background:' + (optimizerSummary.grid_available ? 'var(--cm-ok-100)' : 'var(--cm-warn-100)') + ';color:' + (optimizerSummary.grid_available ? 'var(--cm-ok-500)' : 'var(--cm-warn-500)') + ';font-size:11px;font-weight:700">' + (optimizerSummary.grid_available ? '存在可执行网格' : '无可执行网格') + '</span>' +
        '</div>' +
        '<div class="muted" style="font-size:11px;line-height:1.7">' + (optimizerSummary.model_rules || []).map(function (rule) { return '· ' + esc(rule); }).join('<br>') + '</div>' +
        '</div>';
    }

    var ledgerHtml = '';
    if (tradeabilityOk && (Object.keys(best).length || Object.keys(bh).length)) {
      ledgerHtml = '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">实盘账本检验</div>' +
        '<div style="display:flex;gap:12px;flex-wrap:wrap">' +
        ledgerCard('网格策略账本', best, true) +
        ledgerCard('买入持有账本', bh, false) +
        '</div></div>';
    }

    // --- 3. 全步长对比表 ---
    var stepsHtml = '';
    if (tradeabilityOk && d.all_steps && d.all_steps.length) {
      var bestStep = best.step_pct;
      stepsHtml = '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">全步长回测对比</div>' +
        '<div style="overflow-x:auto"><table class="data-table" style="font-size:11px"><thead><tr>' +
        '<th>步长</th><th>可执行</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>Sharpe</th><th>Calmar</th><th>胜率</th><th>买卖次数</th><th>淘汰/说明</th>' +
        '</tr></thead><tbody>';
      d.all_steps.forEach(function (s) {
        var isBest = s.step_pct === bestStep;
        var rowStyle = isBest ? 'background:var(--primary-light);font-weight:600' : (!s.hard_gate_passed ? 'background:var(--cm-bad-100)' : '');
        var gateHtml = statusPill(s.hard_gate_passed ? '通过' : '淘汰', !!s.hard_gate_passed, s.hard_gate_reason || '');
        stepsHtml += '<tr style="' + rowStyle + '">' +
          '<td>' + s.step_pct + '%' + (isBest ? ' (best)' : '') + '</td>' +
          '<td>' + gateHtml + '</td>' +
          '<td style="color:' + (s.return_pct >= 0 ? 'var(--danger)' : 'var(--success)') + '">' + signedPct(s.return_pct) + '</td>' +
          '<td>' + (s.annual_return_pct != null ? s.annual_return_pct.toFixed(1) + '%' : '-') + '</td>' +
          '<td>' + (s.max_drawdown_pct != null ? s.max_drawdown_pct.toFixed(2) + '%' : '-') + '</td>' +
          '<td>' + (s.sharpe != null ? s.sharpe.toFixed(2) : '-') + '</td>' +
          '<td>' + (s.calmar != null ? s.calmar.toFixed(1) : '-') + '</td>' +
          '<td>' + (s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '-') + '</td>' +
          '<td>' + (s.buy_count || 0) + '买 / ' + (s.sell_count || 0) + '卖</td>' +
          '<td style="min-width:220px">' + esc(s.hard_gate_reason || '通过实盘硬约束') + '</td>' +
          '</tr>';
      });
      stepsHtml += '</tbody></table></div></div>';
    }

    // --- 4. 净值曲线 SVG ---
    var curveHtml = '';
    if (tradeabilityOk && best.curve && best.curve.length > 2 && bh.curve && bh.curve.length > 2) {
      curveHtml = '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">净值走势对比</div>' +
        _buildNavCurveSvg(best.curve, bh.curve, best.step_pct) +
        '</div>';
    }

    var tradeTimelineHtml = tradeabilityOk
      ? _buildEtfTradeTimelineHtml(d.daily_prices || [], best.trades || [], best.step_pct)
      : '';

    // --- 5. 多周期稳定性 ---
    var periodHtml = '';
    if (tradeabilityOk && d.multi_period && d.multi_period.length) {
      periodHtml = '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">多周期稳定性检验</div>' +
        '<div style="overflow-x:auto"><table class="data-table" style="font-size:11px"><thead><tr>' +
        '<th>窗口</th><th>天数</th><th>网格收益</th><th>持有收益</th><th>超额</th><th>网格DD</th><th>持有DD</th><th>最优步长</th>' +
        '</tr></thead><tbody>';
      d.multi_period.forEach(function (p) {
        var gb = p.best;
        var pbh = p.buy_hold;
        var gridR = gb ? gb.return_pct : null;
        var bhR = pbh ? pbh.return_pct : null;
        var excess = (gridR != null && bhR != null) ? (gridR - bhR).toFixed(2) : '-';
        var excessColor = excess !== '-' ? (parseFloat(excess) >= 0 ? 'var(--danger)' : 'var(--success)') : 'var(--muted)';
        periodHtml += '<tr>' +
          '<td style="font-weight:600">' + esc(p.window) + '</td>' +
          '<td>' + p.days + '</td>' +
          '<td style="color:' + ((gridR || 0) >= 0 ? 'var(--danger)' : 'var(--success)') + '">' + (gridR != null ? signedPct(gridR) : '-') + '</td>' +
          '<td style="color:' + ((bhR || 0) >= 0 ? 'var(--danger)' : 'var(--success)') + '">' + (bhR != null ? signedPct(bhR) : '-') + '</td>' +
          '<td style="color:' + excessColor + ';font-weight:600">' + (excess !== '-' ? (parseFloat(excess) >= 0 ? '+' : '') + excess + '%' : '-') + '</td>' +
          '<td>' + (gb ? (gb.max_drawdown_pct != null ? gb.max_drawdown_pct.toFixed(2) + '%' : '-') : '-') + '</td>' +
          '<td>' + (pbh ? (pbh.max_drawdown_pct != null ? pbh.max_drawdown_pct.toFixed(2) + '%' : '-') : '-') + '</td>' +
          '<td>' + (gb ? gb.step_pct + '%' : '-') + '</td>' +
          '</tr>';
      });
      periodHtml += '</tbody></table></div></div>';
    }

    // --- 6. 量化结论 ---
    var verdictHtml = '';
    if (verdict.lines && verdict.lines.length) {
      verdictHtml = '<div style="padding:12px 14px;background:' + rc.bg + ';border-radius:var(--radius);border:1px solid ' + rc.fg + '22">' +
        '<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:' + rc.fg + '">量化基金经理结论 · ' + esc(verdict.rating) + '</div>' +
        verdict.lines.map(function (line) {
          return '<div style="font-size:12px;line-height:1.7;color:' + rc.fg + '">· ' + esc(line) + '</div>';
        }).join('') +
        '</div>';
    }

    var strategyCompareMountId = panelId + '-strategy-compare';
    panel.innerHTML = '<div class="panel" style="margin-top:14px">' +
      '<div class="panel-head" style="justify-content:space-between">' +
      '<span style="font-weight:600">深度量化分析</span>' +
      '<button class="chip chip-ghost chip-sm" onclick="document.getElementById(\'' + panelId + '\').innerHTML=\'\'">关闭</button>' +
      '</div>' +
      headerHtml + issueHtml +
      '<div id="' + strategyCompareMountId + '" style="margin-bottom:14px"></div>' +
      optimizerHtml + comparisonHtml + ledgerHtml + curveHtml + tradeTimelineHtml + stepsHtml + periodHtml + verdictHtml +
      '</div>';
    scheduleSortableTables(panel);
    // 挂载 1Y/3Y/5Y 策略对比 widget
    if (window.ETFStrategyCompareWidget) {
      window.ETFStrategyCompareWidget.mount(strategyCompareMountId, { code: code });
    }
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function _buildEtfTradeTimelineHtml(dailyPrices, trades, stepPct) {
    var prices = (dailyPrices || []).filter(function (item) {
      return item && item.date && item.close != null && !isNaN(Number(item.close));
    }).map(function (item) {
      return { date: item.date, close: Number(item.close) };
    });
    if (prices.length < 2 || !trades || !trades.length) return '';

    var minPrice = Math.min.apply(null, prices.map(function (item) { return item.close; }));
    var maxPrice = Math.max.apply(null, prices.map(function (item) { return item.close; }));
    if (!isFinite(minPrice) || !isFinite(maxPrice)) return '';
    if (maxPrice <= minPrice) maxPrice = minPrice + 1;

    var W = Math.max(960, prices.length * 4);
    var H = 360;
    var padL = 64, padR = 24, padT = 20, padB = 36;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    function px(index) {
      return padL + (index / Math.max(prices.length - 1, 1)) * plotW;
    }

    function py(value) {
      return padT + plotH - ((Number(value) - minPrice) / (maxPrice - minPrice)) * plotH;
    }

    var dateIndex = {};
    prices.forEach(function (item, index) {
      dateIndex[item.date] = index;
    });

    var pricePath = prices.map(function (item, index) {
      return (index === 0 ? 'M' : 'L') + px(index).toFixed(1) + ',' + py(item.close).toFixed(1);
    }).join(' ');

    var yTicks = 5;
    var gridHtml = '';
    for (var i = 0; i <= yTicks; i++) {
      var val = minPrice + (maxPrice - minPrice) * (i / yTicks);
      var y = padT + plotH - (i / yTicks) * plotH;
      gridHtml += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '" stroke="var(--line)" stroke-width="0.5" stroke-dasharray="3,3"/>';
      gridHtml += '<text x="' + (padL - 6) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" font-size="10" fill="var(--muted)">' + val.toFixed(2) + '</text>';
    }

    var xLabelHtml = '';
    [0, 0.25, 0.5, 0.75, 1].forEach(function (ratio) {
      var index = Math.min(prices.length - 1, Math.round((prices.length - 1) * ratio));
      var x = px(index);
      xLabelHtml += '<text x="' + x.toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle" font-size="10" fill="var(--muted)">' + esc((prices[index].date || '').slice(0, 10)) + '</text>';
    });

    var dayStack = {};
    var tradeDots = (trades || []).map(function (trade) {
      if (dateIndex[trade.date] == null || trade.price == null) return '';
      var stackKey = trade.date + '_' + trade.side;
      var stackIndex = dayStack[stackKey] || 0;
      dayStack[stackKey] = stackIndex + 1;
      var baseY = py(trade.price);
      var offset = (trade.side === 'buy' ? 1 : -1) * (8 + (stackIndex % 3) * 7);
      var x = px(dateIndex[trade.date]);
      var y = baseY + offset;
      var color = trade.side === 'buy' ? 'var(--cm-bad-500)' : 'var(--cm-ok-500)';
      var title = [
        (trade.side === 'buy' ? '买入' : '卖出') + ' #' + trade.seq,
        trade.date,
        '价格 ' + etfNum(trade.price, 2),
        '份额 ' + fmt(trade.units || 0),
        '金额 ' + fmtCurrency(trade.notional),
        trade.realized_pnl_pct != null ? ('盈亏 ' + signedPct(trade.realized_pnl_pct)) : '',
        trade.note || ''
      ].filter(Boolean).join(' | ');
      return '<g><circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="5" fill="' + color + '" stroke="var(--cm-surface)" stroke-width="1.5"></circle><title>' + esc(title) + '</title></g>';
    }).join('');

    var realizedTotal = (trades || []).reduce(function (sum, trade) {
      return sum + (trade.realized_pnl || 0);
    }, 0);
    var buyCount = (trades || []).filter(function (trade) { return trade.side === 'buy'; }).length;
    var sellCount = (trades || []).filter(function (trade) { return trade.side === 'sell'; }).length;

    var tradeRows = (trades || []).slice().reverse().map(function (trade) {
      var tone = trade.side === 'buy' ? { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)', label: '买入' } : { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)', label: '卖出' };
      return '<tr>' +
        '<td>' + esc((trade.date || '').slice(0, 10)) + '</td>' +
        '<td><span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:' + tone.bg + ';color:' + tone.fg + ';font-size:11px;font-weight:700">' + tone.label + '</span></td>' +
        '<td>' + etfNum(trade.price, 2) + '</td>' +
        '<td>' + fmt(trade.units || 0) + '</td>' +
        '<td>' + fmtCurrency(trade.notional) + '</td>' +
        '<td>' + fmtCurrency(trade.fee) + '</td>' +
        '<td>' + (trade.realized_pnl != null ? fmtCurrency(trade.realized_pnl) : '<span class="muted">-</span>') + '</td>' +
        '<td>' + (trade.realized_pnl_pct != null ? signedPct(trade.realized_pnl_pct) : '<span class="muted">-</span>') + '</td>' +
        '<td>' + esc(trade.note || '') + '</td>' +
        '</tr>';
    }).join('');

    return '<div style="margin-bottom:14px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:8px">' +
      '<div style="font-weight:600;font-size:13px">日线买卖点时间轴 <span class="muted" style="font-size:11px">红买绿卖 · 步长 ' + esc(stepPct != null ? etfNum(stepPct, 1) + '%' : '-') + '</span></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-bad-100);color:var(--cm-bad-500);font-size:11px;font-weight:700">买入 ' + fmt(buyCount) + '</span>' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-ok-100);color:var(--cm-ok-500);font-size:11px;font-weight:700">卖出 ' + fmt(sellCount) + '</span>' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-brand-50);color:var(--cm-brand-500);font-size:11px;font-weight:700">已实现盈亏 ' + fmtCurrency(realizedTotal) + '</span>' +
      '</div>' +
      '</div>' +
      '<div style="padding:12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg-subtle)">' +
      '<div class="muted" style="font-size:11px;line-height:1.7;margin-bottom:8px">如果买卖点较多，图表会自动拉宽并支持横向滚动，以完整保留每个日线交易点。</div>' +
      '<div style="overflow-x:auto;padding-bottom:6px"><svg viewBox="0 0 ' + W + ' ' + H + '" style="width:' + W + 'px;max-width:none;height:auto;display:block">' +
      gridHtml +
      '<path d="' + pricePath + '" fill="none" stroke="var(--cm-ink-500)" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"></path>' +
      tradeDots +
      xLabelHtml +
      '<text x="' + (padL + 8) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-ink-500)" font-weight="700">灰线=日线收盘价</text>' +
      '<text x="' + (padL + 120) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-bad-500)" font-weight="700">红点=买点</text>' +
      '<text x="' + (padL + 210) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-ok-500)" font-weight="700">绿点=卖点</text>' +
      '</svg></div>' +
      '<div style="margin-top:10px;max-height:360px;overflow:auto"><table class="data-table" style="font-size:11px;margin-bottom:0"><thead><tr><th>日期</th><th>方向</th><th>价格</th><th>份额</th><th>成交金额</th><th>手续费</th><th>已实现盈亏</th><th>盈亏比例</th><th>备注</th></tr></thead><tbody>' + tradeRows + '</tbody></table></div>' +
      '</div>' +
      '</div>';
  }

  // 净值曲线 SVG 生成（网格 vs 买入持有）
  function _buildNavCurveSvg(gridCurve, bhCurve, stepPct) {
    var W = 580, H = 200, padL = 50, padR = 20, padT = 20, padB = 30;
    var plotW = W - padL - padR, plotH = H - padT - padB;

    // 合并日期，找公共范围
    var allNavs = gridCurve.map(function (p) { return p.nav; }).concat(bhCurve.map(function (p) { return p.nav; }));
    var minNav = Math.min.apply(null, allNavs) * 0.98;
    var maxNav = Math.max.apply(null, allNavs) * 1.02;
    if (maxNav <= minNav) maxNav = minNav + 0.01;

    function toPath(curve, color) {
      if (!curve.length) return '';
      var pts = curve.map(function (p, i) {
        var x = padL + (i / (curve.length - 1)) * plotW;
        var y = padT + plotH - ((p.nav - minNav) / (maxNav - minNav)) * plotH;
        return (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
      }).join(' ');
      return '<path d="' + pts + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round"/>';
    }

    // Y 轴刻度
    var yTicks = 5;
    var yLines = '';
    for (var i = 0; i <= yTicks; i++) {
      var val = minNav + (maxNav - minNav) * (i / yTicks);
      var y = padT + plotH - (i / yTicks) * plotH;
      yLines += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '" stroke="var(--line)" stroke-width="0.5" stroke-dasharray="3,3"/>';
      yLines += '<text x="' + (padL - 4) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end" font-size="10" fill="var(--muted)">' + val.toFixed(2) + '</text>';
    }

    // X 轴日期标签（首尾 + 中间）
    var xLabels = '';
    [gridCurve[0], gridCurve[Math.floor(gridCurve.length / 2)], gridCurve[gridCurve.length - 1]].forEach(function (p, i) {
      if (!p) return;
      var x = padL + ([0, 0.5, 1][i]) * plotW;
      xLabels += '<text x="' + x.toFixed(1) + '" y="' + (H - 4) + '" text-anchor="middle" font-size="10" fill="var(--muted)">' + (p.date || '').slice(0, 10) + '</text>';
    });

    // 图例
    var legend =
      '<text x="' + (padL + 8) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-brand-400)" font-weight="600">— 网格 ' + stepPct + '%</text>' +
      '<text x="' + (padL + 120) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-ink-300)" font-weight="600">— 买入持有</text>';

    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;max-width:' + W + 'px;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-sm)">' +
      yLines + xLabels +
      toPath(bhCurve, 'var(--cm-ink-300)') +
      toPath(gridCurve, 'var(--cm-brand-400)') +
      legend +
      '</svg>';
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
