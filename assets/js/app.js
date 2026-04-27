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
      // C6g: 进入股东挖掘默认显示股票视图
      showView('stocks');
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
      dashboard: loadWorkbench,
      research: loadResearch,
      etf: loadEtf,
      'model-monitor': loadModelMonitor,
      data: function () { window.DataView && window.DataView.show(); },
      strategy: function () { window.StrategyView && window.StrategyView.show(); },
      settings: function () { window.SettingsView && window.SettingsView.show(); },
    })[name]?.();
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
    var active = arr.filter(i => i.enabled && !i.blacklisted).length;
    var total = arr.length;
    setText('wbInstActive', fmt(active));
    setText('wbInstTotal', '总 ' + fmt(total) + ' · 黑名单 ' + fmt(total - active));
    // 展示全部类别，按数量降序；卡片位置有限，类别数多时展示 Top 4 + "+N类"
    var types = {};
    arr.forEach(i => { if (i.enabled && !i.blacklisted && i.type) types[i.type] = (types[i.type]||0) + 1; });
    var sorted = Object.entries(types).sort((a,b) => b[1]-a[1]);
    var chipLabel;
    if (sorted.length <= 4) {
      chipLabel = sorted.map(([t,n]) => t + n).join(' · ');
    } else {
      chipLabel = sorted.slice(0, 4).map(([t,n]) => t + n).join(' · ') + ' · +' + (sorted.length - 4) + '类';
    }
    var wbInstTypesEl = el('wbInstTypes');
    if (wbInstTypesEl) {
      wbInstTypesEl.textContent = chipLabel;
      wbInstTypesEl.title = sorted.map(([t,n]) => t + ' ' + n).join('\n');
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

  function emptyStockSummary(stocks) {
    var summary = {
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
      summary.pools[pool] = (summary.pools[pool] || 0) + 1;
      if (pool === 'A池' || pool === 'B池') summary.abTotal += 1;
      if (gate && summary.gates[gate] != null) summary.gates[gate] += 1;
      if (gate === 'follow') summary.followTotal += 1;
      if (s.setup_tag) {
        summary.setupTotal += 1;
        var signalKey = 'A' + (s.setup_priority != null ? s.setup_priority : '?');
        summary.signals[signalKey] = (summary.signals[signalKey] || 0) + 1;
      }
      if (industry) summary.industries[industry] = (summary.industries[industry] || 0) + 1;
      if (source && source !== '-') summary.sources[source] = (summary.sources[source] || 0) + 1;
      if (s._dual_confirm) summary.dualConfirm += 1;
    });
    if (typeof topCountEntries === 'function') {
      summary.topIndustries = topCountEntries(summary.industries, 4);
      summary.topSignals = topCountEntries(summary.signals, 4);
      summary.topSources = topCountEntries(summary.sources, 3);
    }
    return summary;
  }

  // Step 5d 补齐：HEAD 里 resolveStockSummary 调用点有 3 处但从未定义，导致
  // renderStockResearchSummary 在 loadStockView 中一直静默抛 ReferenceError。
  // 本函数合并「后端 summary」与「本地算出的 summary」两种来源：
  //   - 后端 stockSummary 非空：优先用（缺字段回退到本地）
  //   - 后端 stockSummary 为空：完全用本地计算
  function resolveStockSummary(stocks, stockSummary) {
    var local = emptyStockSummary(stocks || []);
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
    // Step 5 任务 2：排序从 4 种（composite/turtle/attention/crowding）简化为 2 种
    // （composite/notice）。turtle/attention/crowding 旧 mode 值仍 fall-through 到
    // composite，不会报错，但 UI 不再暴露这些选项。
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

  function renderStockResearchSummary(stocks, sectorSummary, stockSummary) {
    // Step 5e 重塑：股票视图不再服务"候选池筛选"，改为"股票深挖 + 自选管理"。
    // 汇总胶囊从 6 个 legacy 指标（A/B池 / 海龟 / 外部关注 / Setup）简化为 3 个 signals_v2 口径的事实数字。
    var summary = resolveStockSummary(stocks, stockSummary);
    var stockSummaryBar = el('stockSummaryBar');
    var stockListMeta = el('stockListMeta');
    if (stockSummaryBar) {
      stockSummaryBar.innerHTML = [
        { label: '覆盖股票', value: fmt(summary.total), sub: '被机构持仓的 A 股', tone: 'neutral' },
        { label: '可跟执行', value: fmt(summary.followTotal), sub: '当前 gate=follow 的持仓', tone: 'success' },
        { label: '已纳入自选', value: fmt(summary.watchlistTotal || 0), sub: '去「自选股」tab 管理', tone: 'accent' },
      ].map(function (item) {
        return '<div class="stock-summary-chip stock-summary-chip--' + item.tone + '">' +
          '<span class="stock-summary-label">' + esc(item.label) + '</span>' +
          '<strong>' + esc(item.value) + '</strong>' +
          '<small>' + esc(item.sub) + '</small>' +
          '</div>';
      }).join('');
    }
    if (stockListMeta) {
      stockListMeta.innerHTML =
        '<div class="table-meta-bar">' +
          '<div class="table-meta-copy">' +
            '<div class="table-meta-sub">共 ' + fmt(summary.total) + ' 只股票 · 可跟 ' + fmt(summary.followTotal) + '。点击股票行可展开机构持仓明细。</div>' +
          '</div>' +
        '</div>';
    }
  }

  // openStockFromCandidate / renderCandidatePool 已移除（Step 5d）。
  // 股票研究主入口从「候选池卡片」迁至信号 v2 的"股票聚合"视图。

  async function refreshDashboardStatus(includeConnectivity) {
    // Step 5d 重塑：工作台 5 张健康卡由 refreshWorkbenchHealthBar 直接拉 API 渲染。
    // 此函数现只负责"管线步骤网格 + 审计快照 + 网络源 pill"这三件事。
    var forceAudit = arguments.length > 1 ? !!arguments[1] : false;
    var auditPromise = forceAudit ? refreshAuditSnapshot(true) : Promise.resolve(_lastAuditSnapshot);
    var up = await api('/api/inst/update/status').catch(() => null);
    await auditPromise;
    await renderUpdatePanel(up, { forceAudit: forceAudit });
    if (includeConnectivity !== false) checkNetwork();
  }

  async function refreshWorkbenchStatus() {
    var btn = el('btnRefreshStatus');
    if (!btn) return;
    var originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '刷新中...';
    try {
      await refreshDashboardStatus(false, true);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  // ============================================================
  // Research — Cards & List
  // ============================================================
  async function loadResearch() {
    var [profiles, inst] = await Promise.all([api('/api/inst/profiles'), api('/api/inst/institutions')]);

    // 按类型统计数量（仅计启用且未拉黑），按数量降序排列
    var typeCounts = {};
    (inst?.data || []).forEach(function (i) {
      if (!i || !i.type || !i.enabled || i.blacklisted) return;
      typeCounts[i.type] = (typeCounts[i.type] || 0) + 1;
    });
    var orderedTypes = Object.entries(typeCounts)
      .sort(function (a, b) { return b[1] - a[1]; })
      .map(function (kv) { return kv[0]; });
    var totalActive = Object.values(typeCounts).reduce(function (a, b) { return a + b; }, 0);

    var filterEl = el('instTypeFilter');
    filterEl.innerHTML =
      typeTag('all', '全部 ' + totalActive) +
      orderedTypes.map(function (t) { return typeTag(t, t + ' ' + typeCounts[t]); }).join('');
    filterEl.querySelectorAll('.type-tag').forEach(tag => {
      tag.addEventListener('click', () => {
        filterEl.querySelectorAll('.type-tag').forEach(t => t.classList.remove('active'));
        tag.classList.add('active');
        renderInstList(profiles?.data || [], tag.dataset.type);
      });
    });
    filterEl.querySelector('.type-tag')?.classList.add('active');
    renderInstList(profiles?.data || [], 'all');
  }

  function buildReturnsSvg(gains, width, height) {
    if (!gains || gains.length < 2) return '<div class="muted" style="height:' + height + 'px;display:flex;align-items:center;justify-content:center;font-size:11px">数据不足</div>';
    var vals = gains.map(function (g) { return g.gain_30d || 0; });
    // 数据点过多时采样，保持曲线平滑
    if (vals.length > 60) {
      var step = Math.ceil(vals.length / 60), sampled = [];
      for (var si = 0; si < vals.length; si += step) {
        var chunk = vals.slice(si, Math.min(si + step, vals.length));
        sampled.push(chunk.reduce(function (a, b) { return a + b }, 0) / chunk.length);
      }
      vals = sampled;
    }
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    if (mx === mn) { mx = mn + 1; }
    var pad = 4, w = width - pad * 2, h = height - pad * 2;
    // 生成坐标点
    var coords = vals.map(function (v, i) {
      return { x: pad + i / (vals.length - 1) * w, y: pad + (1 - (v - mn) / (mx - mn)) * h };
    });
    // 贝塞尔平滑曲线
    var pathD = 'M ' + coords[0].x.toFixed(1) + ' ' + coords[0].y.toFixed(1);
    for (var ci = 1; ci < coords.length; ci++) {
      var prev = coords[ci - 1], curr = coords[ci];
      var cpx = (prev.x + curr.x) / 2;
      pathD += ' C ' + cpx.toFixed(1) + ' ' + prev.y.toFixed(1) + ', ' + cpx.toFixed(1) + ' ' + curr.y.toFixed(1) + ', ' + curr.x.toFixed(1) + ' ' + curr.y.toFixed(1);
    }
    // 零线
    var zeroY = (pad + (1 - (0 - mn) / (mx - mn)) * h).toFixed(1);
    // 最大最小点
    var maxIdx = 0, minIdx = 0;
    vals.forEach(function (v, i) { if (v > vals[maxIdx]) maxIdx = i; if (v < vals[minIdx]) minIdx = i; });

    return '<svg viewBox="0 0 ' + width + ' ' + height + '" style="width:100%;height:' + height + 'px">' +
      '<line x1="' + pad + '" y1="' + zeroY + '" x2="' + (width - pad) + '" y2="' + zeroY + '" stroke="var(--cm-ink-100)" stroke-dasharray="3"/>' +
      '<path d="' + pathD + '" fill="none" stroke="var(--cm-brand-400)" stroke-width="1.5"/>' +
      '<circle cx="' + coords[maxIdx].x.toFixed(1) + '" cy="' + coords[maxIdx].y.toFixed(1) + '" r="3" fill="var(--stock-down)"/>' +
      '<text x="' + (coords[maxIdx].x + 4).toFixed(1) + '" y="' + (coords[maxIdx].y - 3).toFixed(1) + '" font-size="9" fill="var(--stock-down)">+' + vals[maxIdx].toFixed(1) + '%</text>' +
      '<circle cx="' + coords[minIdx].x.toFixed(1) + '" cy="' + coords[minIdx].y.toFixed(1) + '" r="3" fill="var(--stock-up)"/>' +
      '<text x="' + (coords[minIdx].x + 4).toFixed(1) + '" y="' + (coords[minIdx].y + 10).toFixed(1) + '" font-size="9" fill="var(--stock-up)">' + vals[minIdx].toFixed(1) + '%</text>' +
      '</svg>';
  }


  // Phase 3: 机构列表维度切换（Step 5d：按钮组移到 HTML 的 .inst-dim-switch）
  function syncDimSwitchActive() {
    var dim = instListState.getDim();
    document.querySelectorAll('.inst-dim-switch .dim-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.dim === dim);
    });
  }

  function renderInstList(data, tf) {
    instListState.setData(tf === 'all' ? data : data.filter(function (p) { return p.inst_type === tf }));
    var instData = instListState.getData();
    var instDim = instListState.getDim();
    var c = el('instListContainer');
    syncDimSwitchActive();

    var head, row;
    if (instDim === 'overview') {
      head = '<tr><th>机构</th><th>类型</th><th>实力分</th><th>可跟分</th><th>置信</th><th>胜率</th><th>持仓</th><th>资金</th><th>公告日</th><th>距今</th></tr>';
      row = function (p) {
        var confBadge = p.score_confidence === 'high' ? '<span style="color:var(--stock-down);font-size:10px">高</span>' :
          p.score_confidence === 'medium' ? '<span style="color:var(--cm-warn-500);font-size:10px">中</span>' :
            p.score_confidence === 'low' ? '<span style="color:var(--stock-up);font-size:10px">低</span>' : '-';
        return '<td><b class="clickable-name" onclick="App.toggleInstDetail(\'' + esc(p.institution_id) + '\',this)">' + esc(p.display_name || p.institution_name || '') + '</b></td><td>' + typeTag(p.inst_type) + '</td><td>' + (p.quality_score != null ? Number(p.quality_score).toFixed(1) : '-') + '</td><td>' + (p.followability_score != null ? Number(p.followability_score).toFixed(1) : '-') + '</td><td>' + confBadge + '</td><td>' + pct(p.win_rate_30d) + '</td><td>' + (p.current_stock_count || 0) + '</td><td>' + compactNum(p.current_total_cap) + '</td><td>' + fmtDate(p.latest_notice_date) + '</td><td>' + (p.latest_notice_date ? daysAgo(p.latest_notice_date) : '-') + '</td>';
      };
    } else if (instDim === 'returns') {
      // 最小可信样本阈值，与 signals.v2.min_sample 一致（10 条）
      var MIN_BUY_SAMPLES = 10;
      head = '<tr><th>机构</th><th title="买入事件数 < ' + MIN_BUY_SAMPLES + ' 条时后续列统计值灰化，样本不足不足以作真信号">买入事件<span class="muted" style="font-weight:400;font-size:10px">（<' + MIN_BUY_SAMPLES + ' 灰显）</span></th><th>30日胜率</th><th>60日胜率</th><th>120日胜率</th><th>30日均收</th><th>60日均收</th><th>120日均收</th><th>依据</th></tr>';
      row = function (p) {
        var basisTag = p.score_basis === 'buy' ? '<span style="color:var(--cm-brand-400);font-size:10px">买入</span>' : '<span style="color:var(--cm-ink-300);font-size:10px">全事件</span>';
        var n = p.buy_event_count || p.total_events || 0;
        var lowSample = n < MIN_BUY_SAMPLES;
        var wrap = function (html) {
          if (!lowSample) return html;
          return '<span class="low-sample" title="样本仅 ' + n + ' 条，低于 ' + MIN_BUY_SAMPLES + ' 条可信门槛">' + html + '</span>';
        };
        var nCell = lowSample
          ? '<span class="low-sample" title="样本仅 ' + n + ' 条，低于 ' + MIN_BUY_SAMPLES + ' 条可信门槛">' + n + ' *</span>'
          : String(n);
        return '<td><b class="clickable-name" onclick="App.toggleInstDetail(\'' + esc(p.institution_id) + '\',this)">' + esc(p.display_name || p.institution_name || '') + '</b></td><td>' + nCell + '</td><td>' + wrap(pct(p.buy_win_rate_30d || p.win_rate_30d)) + '</td><td>' + wrap(pct(p.buy_win_rate_60d || p.win_rate_60d)) + '</td><td>' + wrap(pct(p.buy_win_rate_120d || p.win_rate_90d)) + '</td><td>' + wrap(fmtGain(p.buy_avg_gain_30d || p.avg_gain_30d)) + '</td><td>' + wrap(fmtGain(p.buy_avg_gain_60d || p.avg_gain_60d)) + '</td><td>' + wrap(fmtGain(p.buy_avg_gain_120d || p.avg_gain_120d)) + '</td><td>' + basisTag + '</td>';
      };
    } else if (instDim === 'exits') {
      // 退出表现：卖出后 N 日股价走势 -> 避损率(gain≤0 占比) 越高越会卖 / 后续均涨跌越负越会卖
      var MIN_EXIT_SAMPLES = 10;
      head = '<tr><th>机构</th><th title="退出/减持事件数 < ' + MIN_EXIT_SAMPLES + ' 条时后续列统计值灰化">退出事件<span class="muted" style="font-weight:400;font-size:10px">（<' + MIN_EXIT_SAMPLES + ' 灰显）</span></th><th title="卖出后 30 日跌或平占比，越高说明卖点越准">30日避损</th><th>60日避损</th><th>120日避损</th><th title="卖出后 30 日平均涨跌，越负说明卖得越对">30日后均涨</th><th>60日后均涨</th><th>120日后均涨</th></tr>';
      row = function (p) {
        var n = p.exit_event_count || 0;
        var lowSample = n > 0 && n < MIN_EXIT_SAMPLES;
        var wrap = function (html) {
          if (!lowSample) return html;
          return '<span class="low-sample" title="样本仅 ' + n + ' 条，低于 ' + MIN_EXIT_SAMPLES + ' 条可信门槛">' + html + '</span>';
        };
        var nCell = n === 0
          ? '<span class="muted">0</span>'
          : (lowSample
              ? '<span class="low-sample" title="样本仅 ' + n + ' 条，低于 ' + MIN_EXIT_SAMPLES + ' 条可信门槛">' + n + ' *</span>'
              : String(n));
        return '<td><b class="clickable-name" onclick="App.toggleInstDetail(\'' + esc(p.institution_id) + '\',this)">' + esc(p.display_name || p.institution_name || '') + '</b></td><td>' + nCell + '</td>' +
          '<td>' + wrap(pct(p.exit_avoid_loss_rate_30d)) + '</td>' +
          '<td>' + wrap(pct(p.exit_avoid_loss_rate_60d)) + '</td>' +
          '<td>' + wrap(pct(p.exit_avoid_loss_rate_120d)) + '</td>' +
          '<td>' + wrap(fmtGain(p.exit_post_avg_gain_30d)) + '</td>' +
          '<td>' + wrap(fmtGain(p.exit_post_avg_gain_60d)) + '</td>' +
          '<td>' + wrap(fmtGain(p.exit_post_avg_gain_120d)) + '</td>';
      };
    } else {
      head = '<tr><th>机构</th><th>回撤30d</th><th>回撤60d</th><th>主要行业</th><th>优势行业</th><th>集中度</th><th>完整性</th></tr>';
      row = function (p) {
        var dcTag = p.data_completeness === 'partial' ? '<span style="color:var(--cm-warn-500);font-size:10px" title="收益或行业数据不完整">部分</span>' : '<span style="color:var(--stock-down);font-size:10px">完整</span>';
        return '<td><b class="clickable-name" onclick="App.toggleInstDetail(\'' + esc(p.institution_id) + '\',this)">' + esc(p.display_name || p.institution_name || '') + '</b></td><td>' + (p.buy_median_max_drawdown_30d != null ? '-' + Number(p.buy_median_max_drawdown_30d).toFixed(1) + '%' : (p.median_max_drawdown_30d != null ? '-' + p.median_max_drawdown_30d.toFixed(1) + '%' : '-')) + '</td><td>' + (p.buy_median_max_drawdown_60d != null ? '-' + Number(p.buy_median_max_drawdown_60d).toFixed(1) + '%' : (p.median_max_drawdown_60d != null ? '-' + p.median_max_drawdown_60d.toFixed(1) + '%' : '-')) + '</td><td>' + esc(p.main_industry_1 || '-') + '</td><td>' + esc(p.best_industry_1 || '-') + '</td><td>' + (p.concentration != null ? p.concentration + '%' : '-') + '</td><td>' + dcTag + '</td>';
      };
    }

    c.innerHTML = '<table class="data-table data-table-cards"><thead>' + head + '</thead><tbody>' +
      (instData.length ? instData.map(function (p, i) { return '<tr data-inst-idx="' + i + '">' + row(p) + '</tr>' }).join('') : '<tr><td class="empty-row" colspan="11">暂无数据</td></tr>') +
      '</tbody></table>';
    scheduleSortableTables('instListContainer');
  }
  function switchInstDim(dim) { instListState.setDim(dim); renderInstList(instListState.getData(), 'all'); }
  function filterInstList() {
    var keyword = (el('instSearch')?.value || '').trim().toLowerCase();
    if (!keyword) { renderInstList(instListState.getData(), 'all'); return; }
    var filtered = instListState.getData().filter(function (p) {
      return [p.institution_name, p.display_name, p.inst_type, p.institution_id]
        .some(function (v) { return String(v || '').toLowerCase().includes(keyword); });
    });
    var c = el('instListContainer');
    var rows = c?.querySelectorAll('tbody tr');
    if (!rows) return;
    rows.forEach(function (tr) {
      var text = tr.textContent.toLowerCase();
      tr.style.display = text.includes(keyword) ? '' : 'none';
    });
  }
  function daysAgo(dateStr) {
    if (!dateStr) return '-';
    var s = String(dateStr).replace(/[^0-9]/g, '');
    if (s.length !== 8) return '-';
    var d = new Date(s.substring(0, 4) + '-' + s.substring(4, 6) + '-' + s.substring(6, 8));
    return isNaN(d) ? '-' : Math.floor((new Date() - d) / 86400000) + '天';
  }

  // ============================================================
  // Stocks — Phase 4: 维度切换
  // ============================================================
  var _stockListLoadedAt = 0;
  var _stockListLoadingPromise = null;
  var _stockListRenderSeq = 0;
  var _stockSearchTimer = null;
  // _stockValidationSector 已删除（stock-validation 视图下线）
  function activeStockSubtab() {
    return document.querySelector('.stock-tabs .tab-btn.active')?.dataset.stab || 'list';
  }
  function setStockSearchContext(stab) {
    var search = el('stockSearch');
    var filterArea = el('stockFilterArea');
    if (!search) return;
    var mode = stab || 'list';
    search.placeholder = mode === 'industry'
      ? '搜索行业名称/代码/候选股'
      : mode === 'list'
        ? '搜索股票代码/名称/行业'
        : '当前子页不支持顶部搜索';
    if (filterArea) {
      if (mode === 'industry') filterArea.innerHTML = '<div class="muted" style="font-size:12px">行业背景加载中...</div>';
      else if (mode === 'list') filterArea.innerHTML = '';
      else filterArea.innerHTML = '';
    }
  }
  async function loadActiveStockSubtab() {
    var stab = activeStockSubtab();
    setStockSearchContext(stab);
    // Step 5e：股票视图 6 tab → 3 tab（list / watchlist / exclusions）
    if (stab === 'watchlist') return loadWatchlist();
    if (stab === 'exclusions') return loadExclusions();
    return loadStockList();
  }
  async function loadStockView() {
    await loadActiveStockSubtab();
  }
  async function loadStockList(force) {
    var now = Date.now();
    if (!force && stockListState.getData().length && (now - _stockListLoadedAt) < 15000) {
      renderStockList();
      return;
    }
    if (_stockListLoadingPromise) {
      await _stockListLoadingPromise;
      renderStockList();
      return;
    }
    _stockListLoadingPromise = (async function () {
      // Step 5 任务 C：股票列表并行拉 signals_v2/today，按 stock_code 索引注入每行，
      // 让"信号"列直接展示 signals_v2 当期 action 而非 legacy setup_tag。
      var results = await Promise.all([
        api('/api/inst/stock-trends'),
        loadIndustryOverviewSummary().catch(function () { return null; }),
        api('/api/signals/today?freshness_days=90&limit=2000').catch(function () { return null; }),
        api('/api/inst/watchlist').catch(function () { return null; }),
      ]);
      var r = results[0];
      var industry = results[1];
      var sig = results[2];
      var watchlist = results[3];
      stockListState.setData(r?.data || []);
      stockListState.setSummary(r?.summary || null);
      var stockData = stockListState.getData();
      // 按 stock_code 取"最近一条 follow > watch > skip"的 signal 合并
      var sigByStock = {};
      if (sig && Array.isArray(sig.signals)) {
        var rank = { follow: 0, watch: 1, skip: 2 };
        sig.signals.forEach(function (ev) {
          var code = ev.stock_code;
          if (!code) return;
          var prev = sigByStock[code];
          var prevRank = prev ? (rank[prev.action] != null ? rank[prev.action] : 9) : 9;
          var currRank = rank[ev.action] != null ? rank[ev.action] : 9;
          if (currRank < prevRank) {
            sigByStock[code] = ev;
          } else if (currRank === prevRank && prev && String(ev.notice_date || '') > String(prev.notice_date || '')) {
            sigByStock[code] = ev;
          } else if (!prev) {
            sigByStock[code] = ev;
          }
        });
      }
      var wlSet = new Set((watchlist?.data || []).map(function (w) { return w.stock_code; }));
      stockData.forEach(function (s) {
        s._sig_v2 = sigByStock[s.stock_code] || null;
        s._in_watchlist = wlSet.has(s.stock_code);
      });
      stockData.forEach(decorateStockSearchBlob);
      if (industry?.data) industryViewState.setData(industry.data || []);
      if (industry?.summary) industryViewState.setSummary(industry.summary || null);
      _stockListLoadedAt = Date.now();
    })();
    try {
      await _stockListLoadingPromise;
    } finally {
      _stockListLoadingPromise = null;
    }
    renderStockList();
  }

  function renderStockFilters() {
    var signals = [
      { key: 'all', label: '全部' },
      { key: 'a1', label: 'A1' },
      { key: 'a2', label: 'A2' },
      { key: 'a3', label: 'A3' },
      { key: 'a45', label: 'A4/A5' },
      { key: 'none', label: '无信号' }
    ];
    var gates = [
      { key: 'all', label: '全部' },
      { key: 'follow', label: '可跟' },
      { key: 'watch', label: '关注' },
      { key: 'observe', label: '观察' },
      { key: 'avoid', label: '回避' }
    ];
    // Step 5 任务 2：筛选条瘦身。删除 8 组 legacy 分类（信号 setup / 发现 / 质量 / 阶段 / 预测 /
    // TDX / 海龟 / 外部），保留与 signals_v2 决策流程对齐的 2 组（执行 / 排序）+ 1 组新增行业。
    // 理由：stocks 视图定位为"深挖单只股票"，发现新股票应回到信号 v2 主视图；多维评分 legacy
    // 筛选对 signals_v2 使用者噪音大。
    var sorts = [
      { key: 'composite', label: '综合优先' },
      { key: 'notice', label: '公告最近' },
    ];
    // 从列表数据里实时提取 TDX L1 分布作为行业筛选胶囊
    var tdxL1Counts = {};
    (stockListState.getData() || []).forEach(function (s) {
      var code = (s.tdx_l1 || '').trim();
      if (code) tdxL1Counts[code] = (tdxL1Counts[code] || 0) + 1;
    });
    var tdxL1Names = {
      T01: '能源', T02: '材料', T03: '日常消费', T04: '可选消费',
      T05: '商贸', T06: '社会服务', T07: '装备制造', T08: '公用事业',
      T09: '交通运输', T10: '金融', T11: '建筑地产', T12: '信息产业',
      T13: '综合类'
    };
    var industries = [{ key: 'all', label: '全部' }].concat(
      Object.keys(tdxL1Counts).sort().map(function (code) {
        return { key: code, label: (tdxL1Names[code] || code) + ' ' + tdxL1Counts[code] };
      })
    );
    function chip(group, key, label, active) {
      return '<span class="type-tag stock-filter-chip' + (active ? ' active' : '') + '" data-filter-group="' + group + '" data-filter-key="' + key + '">' + label + '</span>';
    }
    return '<div class="stock-filter-bar">' +
      '<div class="stock-filter-group"><span class="stock-filter-label-pill">执行</span>' + gates.map(function (f) { return chip('gate', f.key, f.label, f.key === stockListState.getFilterGate()) }).join('') + '</div>' +
      '<div class="stock-filter-group"><span class="stock-filter-label-pill">行业</span>' + industries.map(function (f) { return chip('industry', f.key, f.label, f.key === stockListState.getFilterIndustry()) }).join('') + '</div>' +
      '<div class="stock-filter-group"><span class="stock-filter-label-pill stock-filter-label-pill--sort">排序</span>' + sorts.map(function (f) { return chip('sort', f.key, f.label, f.key === stockListState.getSortMode()) }).join('') + '</div>' +
      '</div>';
  }

  // Step 5 任务 2：筛选条瘦身后，本函数只处理 gate / industry / sort 三组。
  // 旧 setup / discovery / quality / stageScore / forecast / screening / turtle / attention
  // 的 state 仍保留在 AppListState 中（下轮删除），但不再绑定 UI。
  function bindStockFilters() {
    var area = el('stockFilterArea');
    if (!area) return;
    area.querySelectorAll('.stock-filter-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var group = chip.dataset.filterGroup;
        var key = chip.dataset.filterKey;
        if (group === 'gate') stockListState.setFilterGate(key);
        if (group === 'industry') stockListState.setFilterIndustry(key);
        if (group === 'sort') {
          stockListState.setSortMode(key);
          renderStockList();
          return;
        }
        chip.closest('.stock-filter-group').querySelectorAll('.stock-filter-chip').forEach(function (c) { c.classList.remove('active') });
        chip.classList.add('active');
        applyStockFilters();
      });
    });
  }

  // Step 5 任务 4：matchSignalFilter / matchAttentionFilter / matchScoreBandFilter /
  // matchScreeningFilter / matchTurtleFilter 已随筛选条瘦身一并下线，不再有调用路径。

  function matchGateFilter(s) {
    // 与显示列共用同一份 stockGateInfo 解析，杜绝「各算各的」
    var filterGate = stockListState.getFilterGate();
    if (filterGate === 'all') return true;
    return stockGateInfo(s).key === filterGate;
  }

  function decorateStockSearchBlob(s) {
    if (!s) return s;
    var screeningTerms = [];
    if (s._screen) {
      if (s._screen.f1_hit) screeningTerms.push('f1', 'ma突破');
      if (s._screen.f3_hit) screeningTerms.push('f3', '趋势跟踪');
      if (s._screen.f5_hit) screeningTerms.push('f5', 'macd金叉');
      if (!screeningTerms.length) screeningTerms.push('未命中');
    }
    s._search_blob = [
      s.stock_code,
      s.stock_name,
      s.tdx_l1,
      s.tdx_l2,
      s.tdx_l3,
      s.setup_industry_name,
      s.stock_archetype,
      s.priority_pool,
      s.display_inst_name,
      s.setup_inst_name,
      s.external_attention_signal,
      s.turtle_setup_state,
      s.turtle_preferred_system,
      s.turtle_reason,
      screeningTerms.join(' ')
    ].map(function (v) {
      return String(v || '').toLowerCase();
    }).join(' ');
    return s;
  }

  function matchIndustryFilter(s) {
    var filter = stockListState.getFilterIndustry();
    if (filter === 'all') return true;
    return (s.tdx_l1 || '').trim() === filter;
  }

  function applyStockFilters() {
    // Step 5 任务 2：filter 链从 9 个（signal/gate/4 scoreBand/screening/turtle/attention）
    // 简化为 2 个（gate/industry）+ keyword。
    var keyword = ((el('stockSearch')?.value) || '').trim().toLowerCase();
    var rows = el('stockListContainer')?.querySelectorAll('tbody tr[data-stock-code]');
    if (!rows) return;
    var data = stockListState.getData() || [];
    var stockMap = {};
    data.forEach(function (item) { stockMap[item.stock_code] = item; });
    rows.forEach(function (tr) {
      var s = stockMap[tr.dataset.stockCode];
      if (!s) { tr.style.display = 'none'; return; }
      var show = matchGateFilter(s) && matchIndustryFilter(s);
      if (show && keyword) show = String(s._search_blob || '').includes(keyword);
      tr.style.display = show ? '' : 'none';
    });
  }

  // Step 5 任务 4：industry 视图整块已下线（trendStateMeta / renderIndustryView /
  // loadIndustryView / applyIndustryFilters 等 ~380 行），入口随 stab-industry 删除。
  // loadIndustryOverviewSummary 保留（loadStockList 仍需要 join 行业汇总）。


  function handleStockSearchInput() {
    if (_stockSearchTimer) clearTimeout(_stockSearchTimer);
    _stockSearchTimer = setTimeout(function () { applyStockFilters(); }, 120);
  }

  function renderStockList() {
    // 筛选栏放到 banner 区
    var filterArea = el('stockFilterArea');
    if (filterArea) { filterArea.innerHTML = renderStockFilters(); bindStockFilters(); }

    var c = el('stockListContainer');
    // Step 5 任务 C + F：head/row 严格对齐为 7 列，信号列从 legacy setup_tag 换成 signals_v2 action。
    // 删除 4 个单独 score 维度 / 标签列 / 来源机构列 / 报告期列（冗余或 legacy）。
    var emptyCols = 7;
    var colgroup = '<colgroup><col style="width:140px"><col style="width:160px"><col style="width:130px"><col style="width:100px"><col style="width:120px"><col style="width:80px"><col style="width:90px"></colgroup>';
    var head = '<tr><th>股票</th><th>当期信号</th><th>行业</th><th>综合评分</th><th>最近公告</th><th title="当前持仓机构总数 (其中可跟家数)">机构</th><th></th></tr>';
    var TDX_L1_NAMES_TBL = {
      T01: '能源', T02: '材料', T03: '日常消费', T04: '可选消费',
      T05: '商贸', T06: '社会服务', T07: '装备制造', T08: '公用事业',
      T09: '交通运输', T10: '金融', T11: '建筑地产', T12: '信息产业', T13: '综合类'
    };
    function signalV2Cell(s) {
      var ev = s && s._sig_v2;
      if (!ev) return '<span class="muted" style="font-size:11px">无当期信号</span>';
      var action = ev.action || 'skip';
      var badgeMap = {
        follow: { text: '可跟', bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' },
        watch:  { text: '观察', bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' },
        skip:   { text: '不跟', bg: 'var(--cm-ink-50)', fg: 'var(--cm-ink-500)' },
      };
      var bd = badgeMap[action] || badgeMap.skip;
      var badge = '<span style="background:' + bd.bg + ';color:' + bd.fg + ';font-size:10px;font-weight:600;padding:2px 8px;border-radius:3px">' + bd.text + '</span>';
      var evLong = ev.long && ev.long.stats;
      var evShort = ev.short && ev.short.stats;
      var evLine = '';
      if (evLong && evLong.ev_pct != null) {
        var sign = evLong.ev_pct >= 0 ? '+' : '';
        var color = evLong.ev_pct >= 5 ? 'var(--cm-ok-500)' : evLong.ev_pct < 0 ? 'var(--cm-bad-500)' : 'var(--cm-ink-500)';
        evLine = '<span style="font-size:11px;color:' + color + ';margin-left:6px">' + sign + evLong.ev_pct.toFixed(1) + '% · n=' + (evLong.n || 0) + '</span>';
      }
      return '<div style="line-height:1.4">' + badge + evLine + '<div class="muted" style="font-size:10px">' +
        esc(ev.institution_name || ev.institution_id || '') + ' · ' + fmtDate(ev.notice_date) + '</div></div>';
    }
    function industryCell(s) {
      var name = TDX_L1_NAMES_TBL[(s.tdx_l1 || '').trim()] || s.tdx_l2 || s.tdx_l1 || '—';
      // L3 缺失（约一半股票 TDX 源无 L3）时明确标注"L3未分类"，避免误读为 L2 就是最细层
      var l3Text = s.tdx_l3 ? s.tdx_l3 : (s.tdx_l2 ? 'L3未分类' : '');
      var subText = l3Text || s.tdx_l2 || '';
      var sub = subText ? ('<div class="muted" style="font-size:10px">' + esc(subText) + '</div>') : '';
      return '<div style="line-height:1.4"><div style="font-size:12px">' + esc(name) + '</div>' + sub + '</div>';
    }
    function watchlistButton(s) {
      var inList = !!s._in_watchlist;
      if (inList) return '<span class="muted" style="font-size:11px">已在自选</span>';
      return '<button type="button" class="chip chip-ghost chip-sm stock-watch-btn" data-stock-code="' + esc(s.stock_code) + '" data-stock-name="' + esc(s.stock_name || '') + '">+ 加自选</button>';
    }
    var row = function (s, idx) {
      return '<tr data-stock-idx="' + idx + '" data-stock-code="' + esc(s.stock_code) + '">' +
        '<td>' + stockCell(s.stock_code, s.stock_name) + '</td>' +
        '<td>' + signalV2Cell(s) + '</td>' +
        '<td data-sort-value="' + esc((s.tdx_l1 || '')) + '">' + industryCell(s) + '</td>' +
        '<td data-sort-value="' + esc(String(s.composite_priority_score != null ? s.composite_priority_score : -1)) + '">' + stockCompositeCell(s) + '</td>' +
        '<td data-sort-value="' + esc(String(s.latest_notice_date || '')) + '">' + stockDateSummaryCell(s.latest_notice_date) + '</td>' +
        '<td data-sort-value="' + esc(String(s.holder_total != null ? s.holder_total : (s.inst_count_t0 || 0))) + '">' + stockHolderCoverageCell(s) + '</td>' +
        '<td>' + watchlistButton(s) + '</td>' +
        '</tr>';
    };
    var sourceData = stockListState.getData() || [];
    var data = stockSortRows(sourceData);
    renderStockResearchSummary(sourceData, industryViewState.getSummary(), stockListState.getSummary());
    if (!sourceData.length) {
      c.innerHTML = '<div class="data-shell"><div class="table-scroll-wrap"><table class="data-table data-table-compact data-table-cards">' + colgroup + '<thead>' + head + '</thead><tbody><tr><td class="empty-row" colspan="' + emptyCols + '">暂无数据</td></tr></tbody></table></div></div>';
      bindStockListInteractions();
      return;
    }
    var renderSeq = ++_stockListRenderSeq;
    c.innerHTML =
      '<div class="data-shell">' +
      '<div id="stockListRenderHint" class="table-render-hint">正在渲染 0 / ' + data.length + ' ...</div>' +
      '<div class="table-scroll-wrap"><table class="data-table data-table-compact data-table-cards">' +
      colgroup + '<thead>' + head + '</thead><tbody></tbody></table></div>' +
      '</div>';
    var tbody = c.querySelector('tbody');
    var hint = el('stockListRenderHint');
    var chunkSize = 180;
    var offset = 0;

    function flushChunk() {
      if (renderSeq !== _stockListRenderSeq || !tbody) return;
      var slice = data.slice(offset, Math.min(offset + chunkSize, data.length));
      tbody.insertAdjacentHTML('beforeend', slice.map(function (s, i) {
        return row(s, offset + i);
      }).join(''));
      offset += slice.length;
      if (hint) {
        hint.textContent = offset < data.length
          ? ('正在渲染 ' + offset + ' / ' + data.length + ' ...')
          : ('共 ' + data.length + ' 只股票，已完成渲染');
      }
      if (offset < data.length) {
        requestAnimationFrame(flushChunk);
        return;
      }
      applyStockFilters();
      bindStockListInteractions();
      scheduleSortableTables('stockListContainer');
    }

    requestAnimationFrame(flushChunk);
  }

  function bindStockListInteractions() {
    var container = el('stockListContainer');
    if (!container || container.dataset.detailBound === '1') return;
    container.dataset.detailBound = '1';
    container.addEventListener('click', async function (event) {
      // Step 5 任务 C：拦截"+ 加自选"按钮点击
      var watchBtn = event.target.closest('.stock-watch-btn');
      if (watchBtn) {
        event.stopPropagation();
        var code = watchBtn.dataset.stockCode;
        var name = watchBtn.dataset.stockName;
        try {
          await api('/api/inst/watchlist', { method: 'POST', body: JSON.stringify({ stock_code: code, stock_name: name }) });
          watchBtn.outerHTML = '<span class="muted" style="font-size:11px">已在自选</span>';
          // 同步更新 stockListState 里对应 row 的 _in_watchlist
          stockListState.getData().forEach(function (s) { if (s.stock_code === code) s._in_watchlist = true; });
        } catch (e) {
          showToast('加入自选失败: ' + e.message, 'error');
        }
        return;
      }
      if (event.target.closest('[onclick],a,button,input,textarea,select,label')) return;
      var row = event.target.closest('tr[data-stock-code]');
      if (!row || !container.contains(row)) return;
      toggleStockDetail(row.dataset.stockCode, row);
    });
  }

  function switchStockDim() { renderStockList(); }

  // ============================================================
  // Watchlist 自选股（C1 紧急精简：去掉 setup-validation/tracking/candidates 等 legacy 段）
  // ============================================================
  async function loadWatchlist() {
    var r = await apiCached('/api/inst/watchlist', SHORT_CACHE_TTL_MS);
    var c = el('watchlistContainer');
    var rows = (r && r.data) || [];
    var head = '<table class="data-table"><thead><tr>' +
      '<th>股票</th><th>入池日期</th><th>入池价</th><th>理由</th>' +
      '<th>至今涨跌</th><th>最大涨</th><th>最大撤</th><th>状态</th>' +
      '</tr></thead><tbody>';
    if (!rows.length) {
      c.innerHTML = '<div class="muted" style="font-size:12px;padding:8px 0">' +
        '自选股列表为空。去「信号」tab → 股票聚合视角 → 点股票行添加（C6b 即将实装）。' +
        '</div>';
      return;
    }
    c.innerHTML = '<div class="panel">' +
      head + rows.map(function (w) {
        return '<tr>' +
          '<td>' + stockCell(w.stock_code, w.stock_name) + '</td>' +
          '<td>' + fmtDate(w.added_date) + '</td>' +
          '<td>' + (w.added_price || '-') + '</td>' +
          '<td>' + esc(w.added_reason || '') + '</td>' +
          '<td>' + fmtGain(w.gain_since_added) + '</td>' +
          '<td>' + fmtGain(w.max_gain) + '</td>' +
          '<td>' + (w.max_drawdown != null ? '-' + Number(w.max_drawdown).toFixed(1) + '%' : '-') + '</td>' +
          '<td>' + esc(w.status || '') + '</td>' +
          '</tr>';
      }).join('') + '</tbody></table></div>';
    scheduleSortableTables('watchlistContainer');
  }

  function validationMetricCard(label, value, sub) {
    return '<div class="validation-kpi-card">' +
      '<div class="validation-kpi-label">' + esc(label) + '</div>' +
      '<div class="validation-kpi-value">' + value + '</div>' +
      (sub ? '<div class="validation-kpi-sub">' + esc(sub) + '</div>' : '') +
      '</div>';
  }

  function qualityScoreSourceMeta(item) {
    if (!item) return '';
    var source = item.company_quality_score_source === 'quality_feature_v1' ? '质量快照' : '评分兜底';
    var parts = [source];
    if (item.company_quality_score_source === 'quality_feature_v1' && item.quality_feature_snapshot_date) {
      parts.push('快照 ' + fmtDate(item.quality_feature_snapshot_date));
    }
    return parts.join(' · ');
  }

  function validationStockTable(rows, reasonField) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:12px">暂无样本。</div>';
    return '<table class="data-table data-table-compact"><thead><tr><th>股票</th><th>池子</th><th>综合 / 原始</th><th>质量 / 阶段</th><th>原因</th></tr></thead><tbody>' +
      rows.map(function (item) {
        var reason = item[reasonField] || item.priority_pool_reason || item.composite_cap_reason || '-';
        var qualityMeta = [item.stock_archetype || '待分类'];
        var qualitySourceMeta = qualityScoreSourceMeta(item);
        if (qualitySourceMeta) qualityMeta.push(qualitySourceMeta);
        return '<tr>' +
          '<td>' + stockCell(item.stock_code, item.stock_name) + '</td>' +
          '<td>' + priorityPoolTag(item.priority_pool) + '</td>' +
          '<td><div style="font-size:12px;color:var(--cm-ink-900);font-weight:600">综合 ' + scoreNum(item.composite_priority_score) + '</div><div class="muted" style="font-size:10px">原始 ' + scoreNum(item.raw_composite_priority_score) + '</div></td>' +
          '<td><div style="font-size:12px;color:var(--cm-ink-900)">质 ' + scoreNum(item.company_quality_score) + ' · 阶 ' + scoreNum(item.stage_score) + '</div><div class="muted" style="font-size:10px">' + esc(qualityMeta.join(' · ')) + '</div></td>' +
          '<td><div style="font-size:11px;color:var(--cm-ink-700);line-height:1.5">' + esc(reason) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }

  function validationReplayCell(item, horizon) {
    var matured = Number(item['matured_' + horizon + 'd_count'] || 0);
    var gain = item['avg_gain_' + horizon + 'd'];
    var winRate = item['win_rate_' + horizon + 'd'];
    var drawdown = item['avg_drawdown_' + horizon + 'd'];
    if (!matured) return '<span class="muted">待成熟</span>';
    return '<div style="line-height:1.45"><div>' + fmtGain(gain) + '</div><div class="muted" style="font-size:10px">样本 ' + fmt(matured) + ' · 胜率 ' + pct(winRate) + (drawdown != null ? ' · 回撤 -' + Number(drawdown).toFixed(1) + '%' : '') + '</div></div>';
  }

  function validationSnapshotHistoryTable(rows) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:12px">暂无快照池历史。</div>';
    return '<table class="data-table data-table-compact"><thead><tr><th>快照日</th><th>池子</th><th>样本</th><th>均综合</th><th>成熟30日</th><th>30日均收益</th><th>30日胜率</th></tr></thead><tbody>' +
      rows.map(function (item) {
        return '<tr>' +
          '<td>' + esc(fmtDate(item.snapshot_date)) + '</td>' +
          '<td>' + priorityPoolTag(item.priority_pool) + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_composite_score) + '</td>' +
          '<td>' + fmt(item.matured_30d_count || 0) + '</td>' +
          '<td>' + fmtGain(item.avg_gain_30d) + '</td>' +
          '<td>' + pct(item.win_rate_30d) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }

  function validationAttentionPoolTable(rows) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:12px">暂无池子 attention 数据。</div>';
    return '<table class="data-table data-table-compact"><thead><tr><th>池子</th><th>覆盖</th><th>增强</th><th>拥挤</th><th>均确分</th><th>均折扣</th><th>均分差</th><th>近20日</th></tr></thead><tbody>' +
      rows.map(function (item) {
        return '<tr>' +
          '<td>' + priorityPoolTag(item.priority_pool) + '</td>' +
          '<td>' + fmt(item.attention_covered_count || 0) + '</td>' +
          '<td>' + fmt(item.attention_boosted_count || 0) + '</td>' +
          '<td>' + fmt(item.attention_crowded_count || 0) + '</td>' +
          '<td>' + scoreNum(item.avg_attention_score) + '</td>' +
          '<td>' + scoreNum(item.avg_crowding_penalty) + '</td>' +
          '<td>' + signedScore(item.avg_score_delta) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }

  function validationAttentionImpactTable(rows) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:12px">暂无样本。</div>';
    return '<table class="data-table data-table-compact"><thead><tr><th>股票</th><th>当前池子</th><th>外部裁决</th><th>Raw → 综合</th><th>近20日</th><th>说明</th></tr></thead><tbody>' +
      rows.map(function (item) {
        var signalHtml = attentionSignalTag(item.external_attention_signal) || '<span class="stock-attention-pill stock-attention-pill--neutral">外部覆盖</span>';
        var signalMeta = [];
        if (item.external_attention_score != null) signalMeta.push('确 ' + scoreNum(item.external_attention_score));
        if (item.external_crowding_penalty != null && Number(item.external_crowding_penalty) > 0) signalMeta.push('挤 ' + scoreNum(item.external_crowding_penalty));
        var reason = item.priority_pool_reason || item.composite_cap_reason || '-';
        var delta = (item.composite_priority_score != null && item.raw_composite_priority_score != null)
          ? (Number(item.composite_priority_score) - Number(item.raw_composite_priority_score))
          : null;
        return '<tr>' +
          '<td>' + stockCell(item.stock_code, item.stock_name) + '</td>' +
          '<td><div style="line-height:1.45">' + priorityPoolTag(item.priority_pool) + '<div class="muted" style="font-size:10px;margin-top:3px">' + esc(item.stock_archetype || '待分类') + '</div></div></td>' +
          '<td><div style="line-height:1.45">' + signalHtml + (signalMeta.length ? '<div class="muted" style="font-size:10px;margin-top:4px">' + esc(signalMeta.join(' · ')) + '</div>' : '') + '</div></td>' +
          '<td><div style="font-size:12px;font-weight:600;color:var(--cm-ink-900)">' + scoreNum(item.raw_composite_priority_score) + ' → ' + scoreNum(item.composite_priority_score) + '</div><div class="muted" style="font-size:10px;margin-top:3px">Δ ' + signedScore(delta) + '</div></td>' +
          '<td>' + fmtGain(item.price_20d_pct) + '</td>' +
          '<td><div style="font-size:11px;color:var(--cm-ink-700);line-height:1.5">' + esc(reason) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }

  function validationAttentionMiniStat(label, valueHtml, tone) {
    return '<div class="validation-attention-mini-stat' + (tone ? ' validation-attention-mini-stat--' + tone : '') + '">' +
      '<div class="validation-attention-mini-label">' + esc(label) + '</div>' +
      '<div class="validation-attention-mini-value">' + (valueHtml || '-') + '</div>' +
      '</div>';
  }

  function validationAttentionMeter(segments) {
    var total = 0;
    (segments || []).forEach(function (segment) {
      total += Math.max(Number(segment && segment.value || 0), 0);
    });
    if (total <= 0) {
      return '<div class="validation-attention-meter validation-attention-meter--empty"><span class="validation-attention-meter-segment validation-attention-meter-segment--empty" style="width:100%"></span></div>';
    }
    return '<div class="validation-attention-meter">' +
      segments.map(function (segment) {
        var value = Math.max(Number(segment && segment.value || 0), 0);
        if (!value) return '';
        return '<span class="validation-attention-meter-segment validation-attention-meter-segment--' + (segment.tone || 'neutral') + '" style="width:' + (value / total * 100).toFixed(1) + '%"></span>';
      }).join('') +
      '</div>';
  }

  function validationAttentionChip(label, valueHtml, tone) {
    return '<span class="validation-attention-chip validation-attention-chip--' + (tone || 'neutral') + '">' +
      '<span class="validation-attention-chip-label">' + esc(label) + '</span>' +
      '<span class="validation-attention-chip-value">' + (valueHtml || '-') + '</span>' +
      '</span>';
  }

  function validationAttentionSummaryCard(label, valueHtml, subtext, bodyHtml, tone) {
    return '<div class="validation-attention-summary-card validation-attention-summary-card--' + (tone || 'neutral') + '">' +
      '<div class="validation-attention-summary-label">' + esc(label) + '</div>' +
      '<div class="validation-attention-summary-value">' + (valueHtml || '-') + '</div>' +
      (subtext ? '<div class="validation-attention-summary-sub">' + esc(subtext) + '</div>' : '') +
      (bodyHtml ? '<div class="validation-attention-summary-body">' + bodyHtml + '</div>' : '') +
      '</div>';
  }

  function validationAttentionDeltaMeter(value, maxAbs) {
    if (value == null) return '<div class="validation-attention-delta-meter"></div>';
    var safeMax = Math.max(Number(maxAbs || 0), 1);
    var width = Math.min(Math.abs(Number(value)) / safeMax * 100, 100);
    var tone = Number(value) >= 0 ? 'good' : 'warn';
    return '<div class="validation-attention-delta-meter"><span class="validation-attention-delta-meter-fill validation-attention-delta-meter-fill--' + tone + '" style="width:' + width.toFixed(1) + '%"></span></div>';
  }

  function validationAttentionDeltaRow(label, value, maxAbs) {
    return '<div class="validation-attention-delta-row">' +
      '<div class="validation-attention-delta-label">' + esc(label) + '</div>' +
      validationAttentionDeltaMeter(value, maxAbs) +
      '<div class="validation-attention-delta-value ' + (value != null && Number(value) >= 0 ? 'positive' : 'negative') + '">' + signedScore(value) + '</div>' +
      '</div>';
  }

  function validationAttentionPoolVisual(rows) {
    if (!rows || !rows.length) return '<div class="muted" style="font-size:12px">暂无池子 attention 数据。</div>';
    return '<div class="validation-attention-pool-grid">' +
      rows.map(function (item) {
        var total = Math.max(Number(item.total || 0), 0);
        var covered = Math.max(Number(item.attention_covered_count || 0), 0);
        var boosted = Math.max(Number(item.attention_boosted_count || 0), 0);
        var crowded = Math.max(Number(item.attention_crowded_count || 0), 0);
        var coveredBase = Math.max(covered, boosted + crowded, 0);
        var neutral = Math.max(coveredBase - boosted - crowded, 0);
        var coverPct = total > 0 ? covered / total * 100 : 0;
        var tone = item.priority_pool === 'A池' ? 'focus'
          : item.priority_pool === 'B池' ? 'good'
            : item.priority_pool === 'C池' ? 'accent'
              : 'neutral';
        return '<div class="validation-attention-pool-card validation-attention-pool-card--' + tone + '">' +
          '<div class="validation-attention-pool-head">' +
          '<div>' +
          '<div class="validation-attention-pool-title">' + priorityPoolTag(item.priority_pool) + '</div>' +
          '<div class="validation-attention-pool-sub">覆盖 ' + fmt(covered) + ' / ' + fmt(total) + ' · 均确分 ' + scoreNum(item.avg_attention_score) + '</div>' +
          '</div>' +
          '<div class="validation-attention-pool-cover">' + (total > 0 ? coverPct.toFixed(0) + '%' : '-') + '</div>' +
          '</div>' +
          '<div class="validation-attention-meter-wrap">' +
          validationAttentionMeter([
            { value: boosted, tone: 'good' },
            { value: neutral, tone: 'neutral' },
            { value: crowded, tone: 'warn' }
          ]) +
          '<div class="validation-attention-meter-legend">' +
          validationAttentionChip('增强', fmt(boosted), 'good') +
          validationAttentionChip('中性', fmt(neutral), 'neutral') +
          validationAttentionChip('拥挤', fmt(crowded), 'warn') +
          '</div>' +
          '</div>' +
          '<div class="validation-attention-pool-stats">' +
          validationAttentionMiniStat('均确分', scoreNum(item.avg_attention_score), 'accent') +
          validationAttentionMiniStat('均折扣', scoreNum(item.avg_crowding_penalty), 'warn') +
          validationAttentionMiniStat('均分差', signedScore(item.avg_score_delta), item.avg_score_delta != null && Number(item.avg_score_delta) >= 0 ? 'good' : 'warn') +
          validationAttentionMiniStat('近20日', fmtGain(item.avg_price_20d_pct)) +
          '</div>' +
          '</div>';
      }).join('') +
      '</div>';
  }

  function validationAttentionSummaryVisual(summary, attentionSummary) {
    summary = summary || {};
    attentionSummary = attentionSummary || {};
    var signaled = Number(attentionSummary.attention_signaled_count || 0);
    var boosted = Number(attentionSummary.boosted_signal_count || 0);
    var crowded = Number(attentionSummary.crowded_signal_count || 0);
    var promotedToA = Number(attentionSummary.promoted_to_a_count || 0);
    var promotedToB = Number(attentionSummary.promoted_to_b_count || 0);
    var promoted = promotedToA + promotedToB;
    var demoted = Number(attentionSummary.demoted_by_crowding_count || 0);
    var crowdedAB = Number(summary.ab_pool_crowded_count || 0);
    var crowdedBase = Math.max(crowdedAB, demoted, 0);
    var maxAbsDelta = Math.max(Math.abs(Number(attentionSummary.boosted_avg_delta || 0)), Math.abs(Number(attentionSummary.crowded_avg_delta || 0)), 1);
    return '<div class="validation-attention-summary-grid">' +
      validationAttentionSummaryCard(
        '当前信号',
        fmt(signaled),
        '增强 ' + fmt(boosted) + ' · 拥挤 ' + fmt(crowded),
        validationAttentionMeter([
          { value: boosted, tone: 'good' },
          { value: crowded, tone: 'warn' }
        ]) +
        '<div class="validation-attention-chip-row">' +
        validationAttentionChip('增强', fmt(boosted), 'good') +
        validationAttentionChip('拥挤', fmt(crowded), 'warn') +
        '</div>',
        'accent'
      ) +
      validationAttentionSummaryCard(
        '晋池确认',
        fmt(promoted),
        'A池 ' + fmt(promotedToA) + ' · B池 ' + fmt(promotedToB),
        '<div class="validation-attention-chip-row">' +
        validationAttentionChip('A池', fmt(promotedToA), 'focus') +
        validationAttentionChip('B池', fmt(promotedToB), 'accent') +
        validationAttentionChip('A池现存确认', fmt(summary.a_pool_confirm_count || 0), 'good') +
        '</div>',
        'focus'
      ) +
      validationAttentionSummaryCard(
        '拥挤压制',
        fmt(demoted),
        'A/B池拥挤 ' + fmt(crowdedAB),
        validationAttentionMeter([
          { value: demoted, tone: 'warn' },
          { value: Math.max(crowdedBase - demoted, 0), tone: 'neutral' }
        ]) +
        '<div class="validation-attention-chip-row">' +
        validationAttentionChip('压回B池', fmt(demoted), 'warn') +
        validationAttentionChip('池内拥挤', fmt(crowdedAB), 'neutral') +
        '</div>',
        'warn'
      ) +
      validationAttentionSummaryCard(
        '均分差',
        signedScore(attentionSummary.boosted_avg_delta),
        '增强组对比拥挤组',
        validationAttentionDeltaRow('增强组', attentionSummary.boosted_avg_delta, maxAbsDelta) +
        validationAttentionDeltaRow('拥挤组', attentionSummary.crowded_avg_delta, maxAbsDelta),
        'neutral'
      ) +
      '</div>';
  }

  function renderTurtleValidationPanel(turtle) {
    var summary = turtle && turtle.summary || {};
    var states = turtle && turtle.state_distribution || [];
    var systems = turtle && turtle.system_distribution || [];
    var bands = turtle && turtle.score_bands || [];
    var hints = turtle && turtle.hints || [];
    var methodology = turtle && turtle.methodology || '';
    var stateTable = '<table class="data-table data-table-compact"><thead><tr><th>状态</th><th>样本</th><th>执行分</th><th>突破分</th><th>风险分</th><th>阶段分</th><th>近20日</th><th>A池</th><th>D池</th></tr></thead><tbody>' +
      (states.length ? states.map(function (item) {
        return '<tr>' +
          '<td>' + turtleStateTag(item.turtle_setup_state) + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_execution_score) + '</td>' +
          '<td>' + scoreNum(item.avg_breakout_score) + '</td>' +
          '<td>' + scoreNum(item.avg_risk_score) + '</td>' +
          '<td>' + scoreNum(item.avg_stage_score) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '<td>' + fmt(item.a_pool_count) + '</td>' +
          '<td>' + fmt(item.d_pool_count) + '</td>' +
          '</tr>';
      }).join('') : '<tr><td class="empty-row" colspan="9">暂无海龟状态样本</td></tr>') +
      '</tbody></table>';
    var systemTable = '<table class="data-table data-table-compact"><thead><tr><th>系统</th><th>样本</th><th>执行分</th><th>突破分</th><th>风险分</th><th>近20日</th></tr></thead><tbody>' +
      (systems.length ? systems.map(function (item) {
        return '<tr>' +
          '<td>' + esc(turtleSystemLabel(item.preferred_system)) + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_execution_score) + '</td>' +
          '<td>' + scoreNum(item.avg_breakout_score) + '</td>' +
          '<td>' + scoreNum(item.avg_risk_score) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '</tr>';
      }).join('') : '<tr><td class="empty-row" colspan="6">暂无海龟系统样本</td></tr>') +
      '</tbody></table>';
    var bandTable = '<table class="data-table data-table-compact"><thead><tr><th>执行分带</th><th>样本</th><th>均执行分</th><th>突破触发</th><th>退出触发</th><th>近20日</th></tr></thead><tbody>' +
      (bands.length ? bands.map(function (item) {
        return '<tr>' +
          '<td>' + esc(item.band_label || '-') + '</td>' +
          '<td>' + fmt(item.total) + '</td>' +
          '<td>' + scoreNum(item.avg_execution_score) + '</td>' +
          '<td>' + fmt(item.breakout_trigger_count) + '</td>' +
          '<td>' + fmt(item.exit_trigger_count) + '</td>' +
          '<td>' + fmtGain(item.avg_price_20d_pct) + '</td>' +
          '</tr>';
      }).join('') : '<tr><td class="empty-row" colspan="6">暂无海龟分带样本</td></tr>') +
      '</tbody></table>';
    return '<div class="validation-section-grid" style="margin-top:14px">' +
      '<div class="panel validation-table-card">' +
      '<div class="validation-section-head"><div><div class="validation-section-title">海龟执行验证</div><div class="validation-section-sub">' + esc(methodology || '用海龟执行状态观察突破、待突破和退出是否能把强弱股票分开。') + '</div></div></div>' +
      '<div class="validation-kpi-grid">' +
      validationMetricCard('海龟覆盖', fmt(summary.covered_stock_count || 0), '覆盖率 ' + pct(summary.coverage_ratio)) +
      validationMetricCard('突破触发', fmt(summary.breakout_trigger_count || 0), '待突破 ' + fmt(summary.watchlist_count || 0)) +
      validationMetricCard('退出触发', fmt(summary.exit_trigger_count || 0), '执行风险过滤') +
      validationMetricCard('平均执行分', scoreNum(summary.avg_execution_score), '突破 ' + scoreNum(summary.avg_breakout_score) + ' · 风险 ' + scoreNum(summary.avg_risk_score)) +
      validationMetricCard('近20日反馈', fmtGain(summary.avg_price_20d_pct), '海龟覆盖股票横截面') +
      '</div>' +
      (hints.length ? '<div class="validation-overlap-note">' + esc(hints.join(' · ')) + '</div>' : '') +
      stateTable +
      '</div>' +
      '<div class="panel validation-table-card">' +
      '<div class="validation-section-head"><div><div class="validation-section-title">系统 / 分带分布</div><div class="validation-section-sub">把 S1 / S2 和执行分带拆开，观察当前执行层是偏进攻还是偏风险过滤。</div></div></div>' +
      systemTable +
      '<div style="height:12px"></div>' +
      bandTable +
      '</div>' +
      '</div>';
  }

  function renderValidationScopeBanner(scope) {
    if (!scope?.sector) return '';
    return '<div class="panel validation-scope-banner">' +
      '<div class="validation-scope-head">' +
      '<div>' +
      '<div class="validation-scope-title">行业验证视角：' + esc(scope.sector) + '</div>' +
      '<div class="validation-scope-sub">当前分池反馈、快照回放、新旧排序对比和异常样本已按该行业过滤。审计摘要仍保持全市场口径。</div>' +
      '</div>' +
      '<button class="chip chip-outline chip-sm" type="button" onclick="App.clearStockValidationFilter()">查看全市场</button>' +
      '</div>' +
      '</div>';
  }

  function snapshotMaturityPendingText(coverage, baseline, extra) {
    coverage = coverage || {};
    baseline = baseline || {};
    var matured10 = Number(baseline.matured_10d_count || 0);
    var matured30 = Number(baseline.matured_30d_count || 0);
    var matured60 = Number(baseline.matured_60d_count || 0);
    var snapshotDays = Number(
      coverage.scored_snapshot_dates != null ? coverage.scored_snapshot_dates
        : coverage.snapshot_dates != null ? coverage.snapshot_dates
          : coverage.total_snapshot_days != null ? coverage.total_snapshot_days
            : 0
    );
    var totalRows = Number(
      coverage.scored_rows != null ? coverage.scored_rows
        : coverage.total_rows != null ? coverage.total_rows
          : coverage.latest_snapshot_total != null ? coverage.latest_snapshot_total
            : 0
    );
    if ((matured10 + matured30 + matured60) > 0 || (!snapshotDays && !totalRows)) return '';
    var parts = ['快照样本仍在积累中'];
    if (snapshotDays > 0) parts.push('快照日 ' + fmt(snapshotDays));
    if (totalRows > 0) parts.push('样本 ' + fmt(totalRows));
    if (extra) parts.push(extra);
    return parts.join(' · ');
  }

  // Step 5 任务 4：stock-validation 整块已下线（renderStockValidation /
  // loadStockValidation / openSectorValidation / clearStockValidationFilter），
  // 对应后端 /api/inst/stock-validation 路由已在子步 1 删除。




  // ============================================================
  // Update Pipeline
  // ============================================================
  var STATUS_COLORS = {
    completed: 'var(--stock-down)',
    partial: 'var(--cm-warn-500)',
    failed: 'var(--stock-up)',
    blocked: 'var(--cm-warn-500)',
    running: 'var(--cm-brand-400)',
    pending: 'var(--cm-ink-300)',
    skipped: 'var(--cm-ink-300)',
    stopped: 'var(--cm-warn-500)',
    idle: 'var(--cm-ink-100)'
  };
  var AUDIT_SNAPSHOT_STORAGE_KEY = 'cm.audit.snapshot.v1';
  function loadStoredAuditSnapshot() {
    try {
      var raw = window.localStorage ? window.localStorage.getItem(AUDIT_SNAPSHOT_STORAGE_KEY) : null;
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.layers ? parsed : null;
    } catch (e) {
      return null;
    }
  }
  function persistAuditSnapshot(audit) {
    try {
      if (!window.localStorage) return;
      if (audit && audit.layers) window.localStorage.setItem(AUDIT_SNAPSHOT_STORAGE_KEY, JSON.stringify(audit));
    } catch (e) {}
  }
  var _uiRunning = false;
  var _lastAuditSnapshot = loadStoredAuditSnapshot();
  var _auditRefreshPromise = null;
  var _activeRunContext = null;
  var _lastRunContext = null;
  var _stopRequestedUi = false;

  var DOWNSTREAM_LABELS = {
    sync_market_data: ['计算收益', '构建当前关系', '机构画像', '行业统计', '生成股票列表', 'TDX选股筛选', '板块动量分析', '机构评分', '股票评分'],
    sync_financial: ['计算财务指标', 'TDX选股筛选'],
    gen_events: ['计算收益', '构建当前关系', '机构画像', '行业统计', '生成股票列表', '机构评分', '股票评分'],
    calc_returns: ['构建当前关系', '机构画像', '行业统计', '生成股票列表', '机构评分', '股票评分'],
    sync_industry: ['构建当前关系', '机构画像', '行业统计', '生成股票列表', '板块动量分析', '机构评分', '股票评分'],
    calc_financial_derived: ['TDX选股筛选'],
    build_current_rel: ['机构画像', '行业统计', '生成股票列表', '机构评分', '股票评分'],
    build_profiles: ['机构评分', '股票评分'],
    build_industry_stat: ['机构评分', '股票评分'],
    build_trends: ['阶段特征构建', '股票评分'],
    calc_screening: ['股票评分'],
    calc_sector_momentum: ['阶段特征构建', '股票评分'],
    build_external_attention: ['股票评分'],
    build_stage_features: ['股票评分'],
    calc_inst_scores: ['股票评分'],
  };
  var STEP_DEFAULTS = [
    { step_id: 'sync_raw', step_name: '下载十大股东', status: 'idle', desc: '从全市场拉取最新十大股东入驻数据' },
    { step_id: 'match_inst', step_name: '匹配跟踪机构', status: 'idle', desc: '将原始数据与跟踪名单匹配' },
    { step_id: 'sync_market_data', step_name: '同步行情数据', status: 'idle', desc: '补齐持仓股月K/日K数据' },
    { step_id: 'sync_financial', step_name: '同步财务数据', status: 'idle', desc: '从通达信同步 gpcw 财务数据' },
    { step_id: 'gen_events', step_name: '生成事件', status: 'idle', desc: '比对持仓变动，生成新进/增持/减持事件' },
    { step_id: 'calc_returns', step_name: '计算收益', status: 'idle', desc: '计算每个事件公告后的收益与回撤' },
    { step_id: 'sync_surveys', step_name: '机构调研', status: 'idle', desc: '同步调研事件供外部关注信号' },
    { step_id: 'sync_qfii', step_name: 'QFII 季报', status: 'idle', desc: '季度末 +30 天后同步 QFII 十大股东持仓（外资维度）' },
    { step_id: 'sync_margin', step_name: '融资融券', status: 'idle', desc: '每日同步 SH+SZ 融资买入/余额/融券数据' },
    { step_id: 'sync_lhb', step_name: '龙虎榜', status: 'idle', desc: '每日同步龙虎榜上榜明细（机构/游资短线痕迹）' },
    { step_id: 'sync_industry', step_name: '通达信行业', status: 'idle', desc: '给持仓股补充通达信三级行业分类' },
    { step_id: 'calc_financial_derived', step_name: '计算财务指标', status: 'idle', desc: '计算 ROE、毛利率等财务派生指标' },
    { step_id: 'build_current_rel', step_name: '构建当前关系', status: 'idle', desc: '构建“机构→股票”当前持仓关系' },
    { step_id: 'build_profiles', step_name: '机构画像', status: 'idle', desc: '生成机构历史胜率、收益、行业画像' },
    { step_id: 'build_industry_stat', step_name: '行业统计', status: 'idle', desc: '汇总机构在各行业的历史表现' },
    { step_id: 'build_trends', step_name: '生成股票列表', status: 'idle', desc: '汇总当前持仓股的信号与趋势' },
    { step_id: 'calc_screening', step_name: 'TDX选股筛选', status: 'idle', desc: '运行通达信 F1/F3/F5 选股公式' },
    { step_id: 'calc_sector_momentum', step_name: '板块动量分析', status: 'idle', desc: '计算板块技术态势与双重确认信号' },
    { step_id: 'build_external_attention', step_name: '外部关注快照', status: 'idle', desc: '同步千股千评与调研统计，更新外部关注快照' },
    { step_id: 'build_stage_features', step_name: '阶段特征构建', status: 'idle', desc: '汇总趋势、行业与财务上下文，生成阶段特征' },
    { step_id: 'build_turtle_features', step_name: '海龟执行特征', status: 'idle', desc: '基于阶段特征构建海龟执行因子' },
    { step_id: 'calc_inst_scores', step_name: '机构评分', status: 'idle', desc: '多维度评分机构实力、胜率、稳定性' },
    { step_id: 'calc_stock_scores', step_name: '股票评分', status: 'idle', desc: '综合评分每只股票的行动价值' }
  ];

  function cloneStepDefinitions() {
    return STEP_DEFAULTS.map(function (step) { return Object.assign({}, step); });
  }

  function mergeStepDefinitions(steps) {
    var known = {};
    cloneStepDefinitions().forEach(function (step) {
      known[step.step_id] = true;
    });
    var byId = {};
    (steps || []).forEach(function (step) {
      if (step && step.step_id) byId[step.step_id] = Object.assign({}, step);
    });
    var merged = cloneStepDefinitions().map(function (step) {
      return Object.assign({}, step, byId[step.step_id] || {});
    });
    (steps || []).forEach(function (step) {
      if (step && step.step_id && !known[step.step_id]) merged.push(Object.assign({}, step));
    });
    return merged;
  }

  function makePlannedSteps(stepIds, skipReasons) {
    var selected = new Set(stepIds || []);
    var reasons = skipReasons || {};
    return cloneStepDefinitions().map(function (step) {
      if (selected.has(step.step_id)) {
        return Object.assign(step, { status: 'pending' });
      }
      return Object.assign(step, {
        status: 'skipped',
        error: reasons[step.step_id] || '数据已是最新，无需更新'
      });
    });
  }

  function fmtTime(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d)) return '';
    return (d.getMonth() + 1) + '-' + d.getDate() + ' ' + d.getHours() + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  function gapStatusLabel(status) {
    return {
      blocked: '阻断',
      retrying: '重试中',
      pending: '待补齐',
      resolved: '已补齐'
    }[status] || (status || '');
  }

  function renderGapItems(items, maxItems) {
    var list = Array.isArray(items) ? items.slice(0, maxItems || items.length) : [];
    if (!list.length) return '';
    return '<div class="gap-queue-list">' + list.map(function (item) {
      var title = item.stock_name ? (item.stock_name + ' · ' + item.stock_code) : (item.stock_code || '-');
      var meta = [];
      if (item.source_attempts > 0) meta.push('尝试' + fmt(item.source_attempts) + '次');
      var ts = item.last_attempt_at || item.updated_at || item.first_seen_at;
      if (ts) meta.push(fmtTime(ts));
      return '<div class="gap-queue-item">' +
        '<div class="gap-queue-item-head">' +
        '<span>' + esc(title) + '</span>' +
        '<span class="gap-chip ' + esc(item.status || 'pending') + '">' + esc(gapStatusLabel(item.status)) + '</span>' +
        '</div>' +
        (item.reason ? '<div class="gap-queue-item-reason">' + esc(item.reason) + '</div>' : '') +
        (meta.length ? '<div class="gap-queue-item-reason">' + esc(meta.join(' · ')) + '</div>' : '') +
        '</div>';
    }).join('') + '</div>';
  }

  function renderGapSummaryInline(summary, maxItems) {
    if (!summary || typeof summary.unresolved !== 'number') return '';
    var meta = '未补齐 ' + fmt(summary.unresolved);
    if (summary.blocked) meta += ' · 阻断 ' + fmt(summary.blocked);
    if (summary.retrying) meta += ' · 重试中 ' + fmt(summary.retrying);
    if (summary.pending) meta += ' · 待补齐 ' + fmt(summary.pending);
    var html = '<div class="gap-queue-meta" style="margin-top:4px">' + esc(meta) + '</div>';
    if ((summary.unresolved || 0) > 0) html += renderGapItems(summary.items, maxItems || 3);
    return html;
  }

  function renderMarketSyncDetail(step) {
    var detail = step.detail;
    if (!detail) return '';
    var lines = [];
    var progressObj = null;
    function stageLine(label, obj) {
      if (!obj) return;
      var text = label + ' ';
      if (typeof obj.done_codes === 'number' && typeof obj.total_codes === 'number') {
        text += obj.done_codes + '/' + obj.total_codes;
      } else if (typeof obj.total_codes === 'number') {
        text += '0/' + obj.total_codes;
      } else {
        text += obj.status || '';
      }
      if (typeof obj.rows === 'number') text += ' · ' + fmt(obj.rows) + '行';
      if (typeof obj.failed_count === 'number' && obj.failed_count > 0) text += ' · 失败' + obj.failed_count;
      if (typeof obj.before_missing === 'number' && typeof obj.after_missing === 'number') {
        text += ' · 缺口' + fmt(obj.before_missing) + '→' + fmt(obj.after_missing);
      }
      lines.push('<div style="font-size:10px;color:var(--cm-ink-500);margin-top:2px">' + esc(text) + '</div>');
      if (obj.reason) {
        lines.push('<div style="font-size:10px;color:var(--cm-warn-500);margin-top:2px">' + esc(obj.reason) + '</div>');
      }
      if (obj.gap_summary && obj.gap_summary.unresolved > 0) {
        lines.push(renderGapSummaryInline(obj.gap_summary, 2));
      }
      if (typeof obj.total_codes === 'number' && obj.total_codes > 0 && obj.status === 'running') {
        progressObj = obj;
      }
    }
    stageLine('月K', detail.monthly_sync);
    stageLine('日K', detail.daily_sync);
    if (_stopRequestedUi && step && step.status === 'running') {
      lines.push('<div style="font-size:10px;color:var(--cm-warn-500);margin-top:2px">正在等待当前请求结束后停止</div>');
    }
    if (!progressObj) return lines.join('');
    var done = typeof progressObj.done_codes === 'number' ? progressObj.done_codes : 0;
    var total = Math.max(progressObj.total_codes || 0, 1);
    var pct = Math.max(0, Math.min(100, Math.round(done / total * 100)));
    return lines.join('') + '<div class="step-mini-progress"><div class="step-mini-progress-fill" style="width:' + pct + '%"></div></div>';
  }

  function renderIndustrySyncDetail(step) {
    var detail = step.detail;
    var obj = detail && detail.industry_sync;
    if (!obj) return '';
    var text = '通达信三级';
    if (typeof obj.updated_rows === 'number') text += ' · 更新' + fmt(obj.updated_rows) + '只';
    if (typeof obj.before_missing === 'number' && typeof obj.after_missing === 'number') {
      text += ' · 缺口' + fmt(obj.before_missing) + '→' + fmt(obj.after_missing);
    } else if (obj.gap_summary && typeof obj.gap_summary.unresolved === 'number') {
      text += ' · 未补齐' + fmt(obj.gap_summary.unresolved);
    }
    var lines = [
      '<div style="font-size:10px;color:var(--cm-ink-500);margin-top:2px">' + esc(text) + '</div>'
    ];
    if (obj.reason) {
      lines.push('<div style="font-size:10px;color:var(--cm-warn-500);margin-top:2px">' + esc(obj.reason) + '</div>');
    }
    if (obj.gap_summary && obj.gap_summary.unresolved > 0) {
      lines.push(renderGapSummaryInline(obj.gap_summary, 2));
    }
    return lines.join('');
  }

  function renderFinancialSyncDetail(step) {
    var detail = step.detail;
    if (!detail) return '';
    var lines = [];
    var progressObj = null;
    var summary = detail.summary;
    if (summary && typeof summary.records === 'number') {
      lines.push('<div style="font-size:10px;color:var(--cm-ink-500);margin-top:2px">新增记录 ' + fmt(summary.records) + ' 条</div>');
    }
    function stageLine(label, obj, stockLabel) {
      if (!obj) return;
      var text = label + ' ';
      if (typeof obj.done_codes === 'number' && typeof obj.candidate_codes === 'number') {
        text += obj.done_codes + '/' + obj.candidate_codes;
      } else if (typeof obj.candidate_codes === 'number') {
        text += '0/' + obj.candidate_codes;
      } else {
        text += obj.status || '';
      }
      if (typeof obj.rows === 'number') text += ' · ' + fmt(obj.rows) + '条';
      if (typeof obj.success_codes === 'number' && obj.success_codes > 0) text += ' · 成功' + fmt(obj.success_codes);
      if (typeof obj.partial_codes === 'number' && obj.partial_codes > 0) text += ' · 未满目标' + fmt(obj.partial_codes);
      if (typeof obj.failed_codes === 'number' && obj.failed_codes > 0) text += ' · 失败' + fmt(obj.failed_codes);
      if (typeof obj.skipped_recent === 'number' && obj.skipped_recent > 0) text += ' · 跳过' + fmt(obj.skipped_recent);
      if (typeof obj.stock_count === 'number') text += ' · ' + fmt(obj.stock_count) + (stockLabel || '只');
      lines.push('<div style="font-size:10px;color:var(--cm-ink-500);margin-top:2px">' + esc(text) + '</div>');
      if (obj.error) {
        lines.push('<div style="font-size:10px;color:var(--cm-warn-500);margin-top:2px">' + esc(obj.error) + '</div>');
      }
      if (typeof obj.candidate_codes === 'number' && obj.candidate_codes > 0 && obj.status === 'running') {
        progressObj = obj;
      }
    }
    stageLine('历史', detail.history_backfill);
    stageLine('最新', detail.snapshot_sync);
    stageLine('资本', detail.capital_behavior);
    stageLine('指标', detail.financial_indicator);
    stageLine('质量', detail.quality_features);
    stageLine('类型', detail.stock_archetypes);
    if (_stopRequestedUi && step && step.status === 'running') {
      lines.push('<div style="font-size:10px;color:var(--cm-warn-500);margin-top:2px">正在等待当前请求结束后停止</div>');
    }
    if (!progressObj) return lines.join('');
    var done = typeof progressObj.done_codes === 'number' ? progressObj.done_codes : 0;
    var total = Math.max(progressObj.candidate_codes || 0, 1);
    var pct = Math.max(0, Math.min(100, Math.round(done / total * 100)));
    return lines.join('') + '<div class="step-mini-progress"><div class="step-mini-progress-fill" style="width:' + pct + '%"></div></div>';
  }


  function normalizeStepReason(step) {
    var msg = step && step.error ? String(step.error) : '';
    if (!msg) return '';
    if (msg === '原始数据已保留，恢复时未重拉') return '已复用现有原始数据';
    if (msg === '持仓数据已保留，恢复时未重匹配') return '已复用现有持仓数据';
    if (msg === '现有K线保留，恢复时未补拉') return '已复用现有K线数据';
    if (msg === '现有行业数据保留，恢复时未补拉') return '已复用现有行业数据';
    return msg;
  }


  function hasStepHistory(steps) {
    return !!(steps && steps.some(function (s) {
      return !!(s && ((s.status && s.status !== 'idle') || s.started_at || s.finished_at || s.records));
    }));
  }

  function buildAuditMeta(step, audit) {
    var layers = audit?.layers || {};
    var raw = layers.raw || {};
    var institutions = layers.institutions || {};
    var holdings = layers.holdings || {};
    var events = layers.events || {};
    var kline = layers.kline || {};
    var returns = layers.returns || {};
    var industry = layers.industry || {};
    var currentRel = layers.current_relationship || {};
    var profiles = layers.profiles || {};
    var industryStat = layers.industry_stat || {};
    var trends = layers.trends || {};
    var sectorMom = layers.sector_momentum || {};
    var externalAttention = layers.external_attention || {};
    var notes = [];
    var actionable = false;
    var actionLabel = '';
    var issueCount = 0;
    var hasData = false;
    var stepId = step.step_id;
    var status = step.status;
    function addNote(text, tone) {
      if (!text) return;
      notes.push({ text: text, tone: tone || 'info' });
    }
    function addIssue(text) {
      addNote(text, 'issue');
    }

    if (stepId === 'sync_raw') {
      if (typeof raw.count === 'number') addNote('审计 ' + fmt(raw.count) + ' 条原始记录 · 覆盖 ' + fmt(raw.stocks || 0) + ' 只全市场股票');
      if (raw.latest_notice) addNote('最新公告 ' + fmtDate(raw.latest_notice));
      hasData = (raw.count || 0) > 0;
      actionable = ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独补拉';
    } else if (stepId === 'match_inst') {
      if (typeof holdings.institutions === 'number') {
        addNote('审计 ' + fmt(holdings.institutions) + '/' + fmt(holdings.tracked_institutions || 0) + ' 家跟踪机构匹配到持仓 · ' + fmt(holdings.stocks || 0) + ' 只历史持仓股');
      }
      if ((holdings.missing_institutions || 0) > 0) {
        addNote('另有 ' + fmt(holdings.missing_institutions) + ' 家跟踪机构当前未见持仓披露，可能为空仓或未进前十大');
      }
      hasData = (holdings.count || 0) > 0 || (holdings.institutions || 0) > 0;
      actionable = ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独重匹配';
    } else if (stepId === 'sync_market_data') {
      if (typeof kline.expected_stocks === 'number') {
        addNote('审计 ' + fmt(kline.covered_stocks || 0) + '/' + fmt(kline.expected_stocks) + ' 只匹配持仓股有日K · 覆盖' + (kline.coverage != null ? kline.coverage : 0) + '%');
        if ((kline.missing || 0) > 0) addIssue('仍缺 ' + fmt(kline.missing) + ' 只无任何日K');
        if ((kline.stale_stocks || 0) > 0) {
          var parts = [];
          if (kline.suspended_stocks) parts.push(fmt(kline.suspended_stocks) + ' 只停牌');
          if (kline.delisted_stocks) parts.push(fmt(kline.delisted_stocks) + ' 只退市');
          addNote(fmt(kline.stale_stocks) + ' 只日K未更新到最新交易日（' + parts.join('、') + '）' + (kline.latest_trade_date ? ' · ' + fmtDate(kline.latest_trade_date) : ''));
        }
      }
      hasData = (kline.expected_stocks || 0) === 0 || (kline.covered_stocks || 0) > 0;
      issueCount = kline.missing || 0;
      actionable = (kline.missing || 0) > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = (kline.missing || 0) > 0 ? '补齐缺失K线' : '单独补跑';
    } else if (stepId === 'gen_events') {
      if (typeof events.count === 'number') addNote('审计 ' + fmt(events.count) + ' 条事件 · 来源 ' + fmt(holdings.count || 0) + ' 条匹配持仓');
      if (typeof events.expected_stocks === 'number') addNote('覆盖 ' + fmt(events.stocks || 0) + '/' + fmt(events.expected_stocks) + ' 只匹配持仓股');
      hasData = (events.expected_stocks || 0) === 0 || (events.count || 0) > 0;
      actionable = ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '重新生成事件';
    } else if (stepId === 'calc_returns') {
      if (typeof returns.total === 'number') {
        addNote('审计 ' + fmt(returns.count || 0) + '/' + fmt(returns.total) + ' 条事件有收益 · 覆盖' + (returns.coverage != null ? returns.coverage : 0) + '%');
        if (typeof returns.expected_institutions === 'number') addNote(fmt(returns.institutions || 0) + '/' + fmt(returns.expected_institutions) + ' 家有事件机构具备收益样本');
        if ((returns.ineligible_events || 0) > 0) addNote('另有 ' + fmt(returns.ineligible_events) + ' 条退出事件不参与收益计算');
        if ((returns.not_ready_events || 0) > 0) {
          addNote(
            '另有 ' + fmt(returns.not_ready_events || 0) + ' 条事件尚待成熟'
            + '（锚点未到 ' + fmt(returns.not_ready_future_events || 0)
            + ' · 路径待成熟 ' + fmt(returns.not_ready_path_events || 0) + '）'
          );
        }
        // 停牌事件是正常现象，灰色提示不算缺口
        if ((returns.suspended_waiting_events || 0) > 0) {
          addNote(fmt(returns.suspended_waiting_events) + ' 只股票停牌中，恢复交易后自动补算');
        }
        // 真正的数据缺失才标红
        var realMissing = Math.max((returns.missing_entry_price_events || 0) - (returns.suspended_waiting_events || 0), 0);
        if (realMissing > 0) {
          addIssue('仍有 ' + fmt(realMissing) + ' 条成熟事件缺K线入口价');
        }
        if ((returns.other_missing_events || 0) > 0) {
          addIssue('仍有 ' + fmt(returns.other_missing_events) + ' 条收益路径未补齐');
        }
        // issueCount 不计入停牌事件
        issueCount = Math.max(realMissing, returns.other_missing_events || 0, returns.missing_institutions || 0);
      }
      hasData = (returns.total || 0) === 0 || (returns.count || 0) > 0 || (returns.not_ready_events || 0) > 0;
      actionable = ((returns.actionable_missing_events || 0) > 0) || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = ((returns.actionable_missing_events || 0) > 0) ? '补算缺失收益' : '单独补跑';
    } else if (stepId === 'sync_surveys') {
      var surveys = layers.surveys || {};
      if (typeof surveys.count === 'number') addNote('审计 ' + fmt(surveys.count) + ' 条机构调研记录');
      if (surveys.latest_date) addNote('最近调研日 ' + fmtDate(surveys.latest_date));
      hasData = (surveys.count || 0) > 0;
      actionable = ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独补跑';
    } else if (stepId === 'sync_qfii') {
      var qfii = layers.qfii || {};
      addNote('数据源：东方财富 stock_gdfx_holding_detail_em（季频，QFII 外资十大股东）');
      if (typeof qfii.rows === 'number') addNote('审计 ' + fmt(qfii.rows) + ' 条季度持仓记录');
      if (qfii.latest_report_date) addNote('最新报告期 ' + qfii.latest_report_date);
      hasData = (qfii.rows || 0) > 0;
      actionable = ['failed'].includes(status);
      actionLabel = '单独补跑';
    } else if (stepId === 'sync_margin') {
      var margin = layers.margin || {};
      addNote('数据源：上交所 + 深交所融资融券日度明细');
      if (typeof margin.rows === 'number') addNote('审计 ' + fmt(margin.rows) + ' 条两融记录');
      if (margin.latest_trade_date) addNote('最新交易日 ' + margin.latest_trade_date);
      hasData = (margin.rows || 0) > 0;
      actionable = ['failed'].includes(status);
      actionLabel = '单独补跑';
    } else if (stepId === 'sync_lhb') {
      var lhb = layers.lhb || {};
      addNote('数据源：东方财富龙虎榜明细（上榜原因多条合并入库）');
      if (typeof lhb.rows === 'number') addNote('审计 ' + fmt(lhb.rows) + ' 条龙虎榜记录');
      if (lhb.latest_trade_date) addNote('最新上榜日 ' + lhb.latest_trade_date);
      hasData = (lhb.rows || 0) > 0;
      actionable = ['failed'].includes(status);
      actionLabel = '单独补跑';
    } else if (stepId === 'sync_industry') {
      if (typeof industry.expected_stocks === 'number') {
        addNote('股票维度：给匹配持仓股补通达信行业分类（L1+L2 为必备，L3 为 TDX 可选字段）');
        addNote('审计 ' + fmt(industry.complete_three_level_stocks || 0) + '/' + fmt(industry.expected_stocks) + ' 只匹配持仓股有完整行业分类 · 覆盖' + (industry.coverage != null ? industry.coverage : 0) + '%');
        addNote('分级覆盖：L1 ' + fmt(industry.level1_stocks || 0) + ' / L2 ' + fmt(industry.level2_stocks || 0) + ' / L3 ' + fmt(industry.level3_stocks || 0) + '（L3 约半数股票 TDX 源无数据）');
        if ((industry.missing || 0) > 0) addIssue('仍缺 ' + fmt(industry.missing) + ' 只，可单独补齐');
      }
      hasData = (industry.expected_stocks || 0) === 0 || (industry.complete_three_level_stocks || 0) > 0;
      issueCount = industry.missing || 0;
      actionable = (industry.missing || 0) > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = (industry.missing || 0) > 0 ? '补齐缺失行业' : '单独补跑';
    } else if (stepId === 'build_current_rel') {
      if (typeof currentRel.expected_count === 'number') addNote('审计 ' + fmt(currentRel.count || 0) + '/' + fmt(currentRel.expected_count) + ' 条当前关系已对齐');
      addNote('口径：每只股票取全市场最新报告期中的跟踪机构');
      addNote('当前集合：' + fmt(currentRel.institutions || 0) + '/' + fmt(currentRel.expected_institutions || 0) + ' 家机构 · ' + fmt(currentRel.stocks || 0) + '/' + fmt(currentRel.expected_stocks || 0) + ' 只股票');
      if (typeof currentRel.industry_stocks === 'number' && typeof currentRel.stocks === 'number') addNote('其中 ' + fmt(currentRel.industry_stocks) + '/' + fmt(currentRel.stocks) + ' 只当前股已带行业');
      if (Math.abs(currentRel.row_gap || 0) > 0) addIssue('当前关系条数仍差 ' + fmt(Math.abs(currentRel.row_gap || 0)) + ' 条');
      if (Math.abs(currentRel.institution_gap || 0) > 0) addIssue('当前关系机构口径仍差 ' + fmt(Math.abs(currentRel.institution_gap || 0)) + ' 家');
      if (Math.abs(currentRel.stock_gap || 0) > 0) addIssue('当前关系股票口径仍差 ' + fmt(Math.abs(currentRel.stock_gap || 0)) + ' 只');
      if ((currentRel.industry_missing_stocks || 0) > 0) addIssue('仍有 ' + fmt(currentRel.industry_missing_stocks) + ' 只当前股缺行业');
      hasData = (currentRel.expected_count || 0) === 0 || (currentRel.count || 0) > 0;
      issueCount = Math.max(
        Math.abs(currentRel.row_gap || 0),
        Math.abs(currentRel.institution_gap || 0),
        Math.abs(currentRel.stock_gap || 0),
        currentRel.industry_missing_stocks || 0
      );
      actionable = issueCount > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '重建当前关系';
    } else if (stepId === 'build_profiles') {
      if (typeof profiles.count === 'number') addNote('审计 ' + fmt(profiles.count) + '/' + fmt(profiles.expected_institutions || institutions.tracked || profiles.count) + ' 家跟踪机构画像已生成');
      if (typeof profiles.current_institutions === 'number') addNote('其中 ' + fmt(profiles.current_institutions) + ' 家当前有持仓 · ' + fmt(profiles.tracked_without_current || 0) + ' 家当前空仓');
      if (Math.max((profiles.expected_institutions || 0) - (profiles.count || 0), 0) > 0) {
        addIssue('仍缺 ' + fmt(Math.max((profiles.expected_institutions || 0) - (profiles.count || 0), 0)) + ' 家机构画像');
      }
      hasData = (profiles.expected_institutions || institutions.tracked || 0) === 0 || (profiles.count || 0) > 0;
      issueCount = Math.max((profiles.expected_institutions || 0) - (profiles.count || 0), 0);
      actionable = (profiles.count || 0) < (institutions.tracked || profiles.count || 0) || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = (profiles.count || 0) < (institutions.tracked || profiles.count || 0) ? '补齐机构画像' : '重算机构画像';
    } else if (stepId === 'build_industry_stat') {
      if (typeof industryStat.expected_institutions === 'number') addNote('审计 ' + fmt(industryStat.institutions || 0) + '/' + fmt(industryStat.expected_institutions) + ' 家有收益样本的机构已生成行业统计');
      addNote('机构维度：按行业汇总历史收益表现，不等同于股票行业映射');
      addNote('L1+L2 统计齐全 ' + fmt(industryStat.complete_three_level_institutions || 0) + '/' + fmt(industryStat.expected_institutions || 0) + ' 家');
      if ((industryStat.tracked_without_holdings || 0) > 0 || (industryStat.matched_without_returns || 0) > 0) {
        addNote('另有 ' + fmt(industryStat.tracked_without_holdings || 0) + ' 家跟踪机构暂无持仓，' + fmt(industryStat.matched_without_returns || 0) + ' 家匹配机构暂无收益样本');
      }
      if ((industryStat.inactive_or_merged_institutions || 0) > 0) {
        addNote('另有 ' + fmt(industryStat.inactive_or_merged_institutions) + ' 家已合并/停用别名机构保留历史收益，不计入行业统计');
      }
      if ((industryStat.missing_institutions || 0) > 0) addIssue('仍缺 ' + fmt(industryStat.missing_institutions) + ' 家机构行业统计');
      var incompleteIndustryStat = Math.max((industryStat.expected_institutions || 0) - (industryStat.complete_three_level_institutions || 0), 0);
      if (incompleteIndustryStat > 0) addIssue('仍有 ' + fmt(incompleteIndustryStat) + ' 家机构 L1/L2 层级未补齐');
      hasData = (industryStat.expected_institutions || 0) === 0 || (industryStat.institutions || 0) > 0;
      issueCount = Math.max(
        industryStat.missing_institutions || 0,
        incompleteIndustryStat
      );
      actionable = issueCount > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '重算行业统计';
    } else if (stepId === 'build_trends') {
      if (typeof trends.expected_stocks === 'number') addNote('审计 ' + fmt(trends.count || 0) + '/' + fmt(trends.expected_stocks) + ' 只当前持仓股已入榜');
      if (typeof trends.scored === 'number') addNote('其中 ' + fmt(trends.scored) + '/' + fmt(trends.count || 0) + ' 只已有评分');
      if ((trends.missing_stocks || 0) > 0) addIssue('仍缺 ' + fmt(trends.missing_stocks) + ' 只当前股未入榜');
      if ((trends.extra_stocks || 0) > 0) addIssue('仍有 ' + fmt(trends.extra_stocks) + ' 只股票未对齐当前关系');
      if (Math.max((trends.count || 0) - (trends.scored || 0), 0) > 0) addIssue('仍有 ' + fmt(Math.max((trends.count || 0) - (trends.scored || 0), 0)) + ' 只入榜股票未评分');
      hasData = (trends.expected_stocks || 0) === 0 || (trends.count || 0) > 0;
      issueCount = Math.max(trends.missing_stocks || 0, trends.extra_stocks || 0, Math.max((trends.count || 0) - (trends.scored || 0), 0));
      actionable = issueCount > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '重生成股票列表';
    } else if (stepId === 'sync_financial') {
      var financial = layers.financial || {};
      if (typeof financial.raw_count === 'number') addNote('审计 ' + fmt(financial.raw_count) + ' 条原始财务记录');
      if (typeof financial.latest_count === 'number') addNote(fmt(financial.latest_count) + '/' + fmt(financial.expected_stocks || 0) + ' 只A股主数据股票有最新财务快照');
      if (typeof financial.tracked_latest_count === 'number') addNote(fmt(financial.tracked_latest_count) + '/' + fmt(financial.tracked_expected_stocks || 0) + ' 只持仓股票已有财务覆盖');
      hasData = (financial.raw_count || 0) > 0 || (financial.latest_count || 0) > 0;
      actionable = ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独补拉';
    } else if (stepId === 'calc_financial_derived') {
      var financial = layers.financial || {};
      if (typeof financial.derived_count === 'number') addNote('审计 ' + fmt(financial.derived_count) + ' 条派生财务指标');
      if (typeof financial.latest_count === 'number') addNote(fmt(financial.latest_count) + ' 只股票有最新快照');
      hasData = (financial.derived_count || 0) > 0;
      actionable = ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独补算';
    } else if (stepId === 'calc_screening') {
      var screening = layers.screening || {};
      if (typeof screening.count === 'number') addNote('审计 ' + fmt(screening.count) + ' 只股票已筛选');
      if (typeof screening.hits === 'number' && screening.count > 0) addNote('命中 ' + fmt(screening.hits) + ' 只（F1/F3/F5 任一命中）');
      if (screening.screen_date) addNote('最近筛选：' + fmtDate(screening.screen_date));
      hasData = (screening.count || 0) > 0;
      actionable = (screening.count || 0) === 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独补跑';
    } else if (stepId === 'calc_sector_momentum') {
      if (typeof sectorMom.count === 'number') addNote('审计 ' + fmt(sectorMom.count) + ' 个板块动量已计算');
      if (typeof sectorMom.dual_confirm_count === 'number' && sectorMom.count > 0) addNote('双重确认信号 ' + fmt(sectorMom.dual_confirm_count) + ' 个');
      hasData = (sectorMom.count || 0) > 0;
      actionable = (sectorMom.count || 0) === 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = '单独补跑';
    } else if (stepId === 'build_external_attention') {
      if (externalAttention.latest_snapshot_date) {
        var freshnessBits = ['最新快照 ' + fmtDate(externalAttention.latest_snapshot_date)];
        if (externalAttention.comment_trade_date) freshnessBits.push('评论日 ' + fmtDate(externalAttention.comment_trade_date));
        addNote(freshnessBits.join(' · '));
      }
      if (typeof externalAttention.expected_stocks === 'number' && externalAttention.expected_stocks > 0) {
        addNote(
          '审计 ' + fmt(externalAttention.covered_stocks || 0) + '/' + fmt(externalAttention.expected_stocks) +
          ' 只当前股票有外部覆盖 · 覆盖' + (externalAttention.coverage != null ? externalAttention.coverage : 0) + '%'
        );
      } else if (typeof externalAttention.snapshot_rows === 'number' && externalAttention.snapshot_rows > 0) {
        addNote('审计 ' + fmt(externalAttention.snapshot_rows) + ' 条外部关注最新记录');
      }
      var sourceBits = [];
      if (typeof externalAttention.comment_covered === 'number') sourceBits.push('千评 ' + fmt(externalAttention.comment_covered));
      if (typeof externalAttention.survey_covered === 'number') sourceBits.push('调研 ' + fmt(externalAttention.survey_covered));
      if (sourceBits.length) addNote(sourceBits.join(' · '));
      if (typeof externalAttention.trend_scope === 'number' && externalAttention.trend_scope > 0) {
        addNote(
          '其中 ' + fmt(externalAttention.trend_scored_stocks || 0) + '/' + fmt(externalAttention.trend_scope) +
          ' 只研究股票已回流 attention 评分 · 信号 ' + fmt(externalAttention.trend_signal_count || 0) + ' 只'
        );
      }
      if ((externalAttention.snapshot_lag_days || 0) > 0) addIssue('快照滞后 ' + fmt(externalAttention.snapshot_lag_days) + ' 天');
      if ((externalAttention.missing_stocks || 0) > 0) addIssue('仍缺 ' + fmt(externalAttention.missing_stocks) + ' 只当前股票外部覆盖');
      hasData = (externalAttention.snapshot_rows || 0) > 0;
      issueCount = Math.max(externalAttention.missing_stocks || 0, (externalAttention.snapshot_lag_days || 0) > 0 ? 1 : 0);
      actionable = !hasData || issueCount > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = hasData ? '刷新外部关注' : '补拉外部关注';
    } else if (stepId === 'build_stage_features') {
      var stageExpected = trends.count || 0;
      if (typeof sectorMom.stage_feature_count === 'number') addNote('审计 ' + fmt(sectorMom.stage_feature_count) + '/' + fmt(stageExpected) + ' 只入榜股票已生成阶段特征');
      if (typeof sectorMom.industry_context_count === 'number' && sectorMom.industry_context_count > 0) {
        addNote('行业上下文 ' + fmt(sectorMom.industry_context_count) + ' 只 · 板块动量 ' + fmt(sectorMom.count || 0) + ' 个');
      }
      var stageGap = Math.max(stageExpected - (sectorMom.stage_feature_count || 0), 0);
      if (stageGap > 0) addIssue('仍缺 ' + fmt(stageGap) + ' 只股票阶段特征');
      hasData = stageExpected === 0 || (sectorMom.stage_feature_count || 0) > 0;
      issueCount = stageGap;
      actionable = stageGap > 0 || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = stageGap > 0 ? '补齐阶段特征' : '重算阶段特征';
    } else if (stepId === 'calc_inst_scores') {
      if (typeof profiles.scored === 'number') addNote('审计 ' + fmt(profiles.scored) + '/' + fmt(profiles.count || institutions.tracked || 0) + ' 家机构已评分');
      if (Math.max((profiles.count || 0) - (profiles.scored || 0), 0) > 0) addIssue('仍缺 ' + fmt(Math.max((profiles.count || 0) - (profiles.scored || 0), 0)) + ' 家机构评分');
      hasData = (profiles.count || institutions.tracked || 0) === 0 || (profiles.scored || 0) > 0;
      issueCount = Math.max((profiles.count || 0) - (profiles.scored || 0), 0);
      actionable = (profiles.scored || 0) < (profiles.count || institutions.tracked || 0) || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = (profiles.scored || 0) < (profiles.count || institutions.tracked || 0) ? '补算剩余机构评分' : '重算机构评分';
    } else if (stepId === 'calc_stock_scores') {
      if (typeof trends.scored === 'number') addNote('审计 ' + fmt(trends.scored) + '/' + fmt(trends.count || 0) + ' 只入榜股票已评分');
      if (Math.max((trends.count || 0) - (trends.scored || 0), 0) > 0) addIssue('仍缺 ' + fmt(Math.max((trends.count || 0) - (trends.scored || 0), 0)) + ' 只股票评分');
      hasData = (trends.count || 0) === 0 || (trends.scored || 0) > 0;
      issueCount = Math.max((trends.count || 0) - (trends.scored || 0), 0);
      actionable = (trends.scored || 0) < (trends.count || 0) || ['failed', 'skipped', 'stopped'].includes(status);
      actionLabel = (trends.scored || 0) < (trends.count || 0) ? '补算剩余股票评分' : '重算股票评分';
    }

    if (!actionable && ['failed', 'skipped', 'stopped'].includes(status)) {
      actionable = true;
      actionLabel = '单独补跑';
    }

    var downstream = DOWNSTREAM_LABELS[stepId] || [];
    if (actionable && downstream.length) {
      addNote('执行后将自动续跑：' + downstream.slice(0, 3).join('、') + (downstream.length > 3 ? ' 等' : ''));
    }

    return { audit_notes: notes, actionable: actionable, action_label: actionLabel, issue_count: issueCount, has_data: hasData };
  }

  function isBlockedStep(step) {
    var reason = normalizeStepReason(step) || '';
    return /不可用|阻断|中断|连接失败/.test(reason);
  }

  function deriveDisplayStatus(step, meta) {
    var rawStatus = step.status || 'idle';
    var issueCount = meta.issue_count || 0;
    var hasData = !!meta.has_data;
    var blocked = isBlockedStep(step);
    var recordNum = Number(step.records);
    var hasStepResult = !!step.finished_at || !!step.detail || (Number.isFinite(recordNum) && recordNum > 0);

    if (_uiRunning) {
      if (rawStatus === 'running' || rawStatus === 'pending' || rawStatus === 'failed' || rawStatus === 'blocked' || rawStatus === 'stopped') return rawStatus;
      if (rawStatus === 'skipped' && blocked) return 'blocked';
      if (rawStatus === 'partial') return 'partial';
      if (rawStatus === 'completed' && issueCount > 0) return 'partial';
      return rawStatus;
    }

    if (rawStatus === 'failed' && !hasData) return 'failed';
    if (rawStatus === 'blocked') return 'blocked';
    if (rawStatus === 'stopped' && !hasData) return 'stopped';
    if (blocked && !hasData) return 'blocked';
    if (rawStatus === 'partial') return 'partial';
    // 已经跑完且后端明确返回 skipped (跳过 / 已最新), 保留 skipped 状态;
    // 否则会被下面 hasData/idle 分支吞成 idle, UI 不显示图标和文案语义.
    if (rawStatus === 'skipped' && (step.finished_at || step.detail)) return 'skipped';
    if (issueCount > 0) return 'partial';
    if (rawStatus === 'completed' && hasStepResult) return 'completed';
    if (hasData) return 'completed';
    return 'idle';
  }

  function formatAuditNote(note) {
    var text = typeof note === 'string' ? note : (note?.text || '');
    var tone = typeof note === 'string' ? 'info' : (note?.tone || 'info');
    var html = esc(text);
    if (tone === 'issue') {
      html = html.replace(/(\d+(?:,\d{3})*(?:\.\d+)?)/g, '<span class="audit-issue-num">$1</span>');
    }
    return { html: html, tone: tone };
  }

  function withAuditNotes(steps, audit) {
    return (steps || []).map(function (s) {
      var item = Object.assign({}, s);
      var meta = buildAuditMeta(item, audit);
      item.audit_notes = meta.audit_notes || [];
      item.actionable = !!meta.actionable;
      item.action_label = meta.action_label || '';
      item.audit_issue = !!meta.issue_count;
      item.audit_issue_count = meta.issue_count || 0;
      item.display_status = deriveDisplayStatus(item, meta);
      return item;
    });
  }

  function renderWorkbenchSummary(summary) {
    var progressBar = document.querySelector('#progressArea .progress-bar');
    var pipelineSummary = el('pipelineSummary');
    if (!summary || summary.kind === 'idle') {
      el('progressArea').style.display = 'none';
      el('progressFill').style.width = '0%';
      el('progressText').textContent = '';
      if (pipelineSummary) pipelineSummary.textContent = '默认折叠，只有在更新或排查时再展开';
      if (progressBar) progressBar.style.display = '';
      return;
    }
    var pctVal = Math.max(0, Math.min(100, Number(summary.pct || 0)));
    var latestAt = fmtTime(summary?.counts?.latest_at);
    var message = summary.message || '';
    if ((summary.kind === 'last' || summary.kind === 'noop') && latestAt) {
      message += ' · ' + latestAt;
    }
    el('progressArea').style.display = '';
    if (progressBar) progressBar.style.display = summary.show_progress === false ? 'none' : '';
    el('progressFill').style.width = (summary.kind === 'noop' ? '0%' : pctVal + '%');
    el('progressText').textContent = message;
    if (pipelineSummary) pipelineSummary.textContent = message;
  }

  function stepGridSignature(steps) {
    if (!steps || !steps.length) return '';
    return JSON.stringify(steps.map(function (s) {
      return [
        s.step_id,
        s.display_status || s.status || '',
        Number.isFinite(Number(s.records)) ? Number(s.records) : 0,
        s.detail?.message || '',
        s.started_at || '',
        s.finished_at || '',
        s.error || '',
        s.actionable ? 1 : 0,
        s.audit_issue_count || 0
      ];
    }));
  }

  function workbenchSummarySignature(summary) {
    if (!summary) return '';
    return JSON.stringify({
      kind: summary.kind || '',
      mode: summary.mode || '',
      pct: summary.pct || 0,
      message: summary.message || '',
      show_progress: summary.show_progress !== false,
      latest_at: summary?.counts?.latest_at || '',
      running: summary?.counts?.running || 0,
      pending: summary?.counts?.pending || 0,
      active: (summary.active_step_ids || []).join(',')
    });
  }

  function maybeRenderStepGrid(steps) {
    if (!steps || !steps.length) {
      _lastStepGridSig = '';
      renderStepGrid(steps);
      return;
    }
    var sig = stepGridSignature(steps);
    if (sig && sig === _lastStepGridSig) return;
    _lastStepGridSig = sig;
    renderStepGrid(steps);
  }

  function maybeRenderWorkbenchSummary(summary) {
    if (!summary) {
      _lastWorkbenchSummarySig = '';
      renderWorkbenchSummary(summary);
      return;
    }
    var sig = workbenchSummarySignature(summary);
    if (sig && sig === _lastWorkbenchSummarySig) return;
    _lastWorkbenchSummarySig = sig;
    renderWorkbenchSummary(summary);
  }

  async function refreshAuditSnapshot() {
    var forceRefresh = arguments.length > 0 ? !!arguments[0] : false;
    if (!forceRefresh && _lastAuditSnapshot) return _lastAuditSnapshot;
    if (_auditRefreshPromise) return _auditRefreshPromise;
    _auditRefreshPromise = (async function () {
      var audit = await api('/api/inst/update/audit');
      if (audit && audit.layers) {
        _lastAuditSnapshot = audit;
        persistAuditSnapshot(audit);
      }
      return _lastAuditSnapshot;
    })();
    try {
      return await _auditRefreshPromise;
    } finally {
      _auditRefreshPromise = null;
    }
  }

  async function renderUpdatePanel(up, options) {
    var forceAudit = !!(options && options.forceAudit);
    if (up?.run_context) _activeRunContext = up.run_context;
    if (up?.last_run_context) _lastRunContext = up.last_run_context;
    if (up?.running) {
      _uiRunning = true;
      _stopRequestedUi = !!up.stop_requested;
      var runningSteps = withAuditNotes(mergeStepDefinitions(up.steps || STEP_DEFAULTS), _lastAuditSnapshot || {});
      maybeRenderStepGrid(runningSteps);
      maybeRenderWorkbenchSummary(up.summary);
      syncServerLogs(up.logs || [], true);
      el('btnUpdateAll').disabled = true;
      el('btnUpdateAll').textContent = up.stop_requested ? '停止中...' : '重算中...';
      el('btnStop').disabled = !!up.stop_requested;
      el('btnStop').textContent = up.stop_requested ? '停止中...' : '停止';
      el('btnStop').style.display = '';
      if (!_poll) _poll = setInterval(pollStatus, 1000);
      return;
    }

    _uiRunning = false;
    _stopRequestedUi = false;
    _activeRunContext = null;
    var baseSteps = hasStepHistory(up?.steps) ? mergeStepDefinitions(up.steps) : STEP_DEFAULTS;
    if (forceAudit) await refreshAuditSnapshot(true);
    var enrichedSteps = withAuditNotes(baseSteps, _lastAuditSnapshot);
    maybeRenderStepGrid(enrichedSteps);
    maybeRenderWorkbenchSummary(up?.summary);
    syncServerLogs(up?.logs || [], true);

    el('btnUpdateAll').disabled = false;
    el('btnUpdateAll').textContent = '重算派生层';
    el('btnStop').disabled = false;
    el('btnStop').textContent = '停止';
    el('btnStop').style.display = 'none';
  }

  // Step 5 任务 B：从"3 列 × 19 卡片密铺"改为"3 组折叠行表"，
  // 默认折叠到三行概览，有 failed/partial/running 的组自动展开。
  /* 用首字母英文缩写做占位, 未来图标替换时改 step-icon--X 的 CSS background / mask 即可 */
  var STEP_STATUS_ICON = {
    completed: 'OK',  partial: '!',  failed: 'X',  blocked: '-',
    running:   '...', pending: '·',  skipped: 'OK', stopped: '■', idle: '·'
  };
  var STEP_STATUS_LABEL = {
    completed: '完成', partial: '有缺口', failed: '失败', blocked: '阻断',
    running: '运行中', pending: '等待执行', skipped: '已最新', stopped: '已停止', idle: ''
  };

  function renderStepGrid(steps) {
    if (!steps || !steps.length) { el('stepGrid').innerHTML = ''; return; }

    // M9.5: 优先用后端 step.group_name (新增 step 自动归组), 旧 step_id 没 group_name 时回退硬编码映射.
    var GROUP_MAP = {
      sync_raw: 'data', match_inst: 'data', sync_market_data: 'data', sync_financial: 'data', sync_surveys: 'data', sync_qfii: 'data', sync_margin: 'data', sync_lhb: 'data', sync_industry: 'data',
      build_turtle_features: 'mart',
      gen_events: 'calc', calc_returns: 'calc', calc_financial_derived: 'calc',
      build_current_rel: 'mart', build_profiles: 'mart', build_industry_stat: 'mart', build_trends: 'mart', calc_screening: 'mart', calc_sector_momentum: 'mart', build_external_attention: 'mart', build_stage_features: 'mart', calc_inst_scores: 'mart', calc_stock_scores: 'mart'
    };
    var GROUP_DEF = {
      data: { name: '数据获取', verb: '重新同步', badge: '①' },
      calc: { name: '事实计算', verb: '全量计算', badge: '②' },
      mart: { name: '集市构建', verb: '重构集市', badge: '③' }
    };
    var grouped = { data: [], calc: [], mart: [] };
    steps.forEach(function (s) {
      var g = s.group_name || GROUP_MAP[s.step_id] || 'mart';
      if (!grouped[g]) grouped[g] = [];
      grouped[g].push(s);
    });
    // M9.5: count 改为动态 (按实际 grouped 长度), 避免硬编码与新 step 不一致
    GROUP_DEF.data.count = grouped.data.length;
    GROUP_DEF.calc.count = grouped.calc.length;
    GROUP_DEF.mart.count = grouped.mart.length;

    // 保留用户手动展开状态（每次重渲染时读取 <details open>）
    var prevOpen = {};
    el('stepGrid').querySelectorAll('details[data-group]').forEach(function (d) {
      prevOpen[d.dataset.group] = d.open;
    });

    // P3 (2026-04-27): 工作台只展示 calc + mart, 数据获取(data)迁去数据页
    var html = '';
    ['calc', 'mart'].forEach(function (gId) {
      var groupSteps = grouped[gId];
      var def = GROUP_DEF[gId];
      var counts = { completed: 0, failed: 0, partial: 0, running: 0, skipped: 0, stopped: 0, blocked: 0, pending: 0, idle: 0 };
      groupSteps.forEach(function (s) {
        var ds = s.display_status || s.status || 'idle';
        counts[ds] = (counts[ds] || 0) + 1;
      });
      var total = def.count;
      var completedN = counts.completed + counts.skipped;
      var failedN = counts.failed + counts.partial + counts.blocked;
      var runningN = counts.running;

      // 折叠策略：有失败/运行中 → 强制展开；否则保留上次状态，首次渲染默认折叠
      var shouldOpen = (failedN > 0 || runningN > 0);
      if (!shouldOpen && prevOpen[gId] !== undefined) shouldOpen = prevOpen[gId];

      // 组摘要状态色
      var groupTone = failedN > 0 ? 'bad' : runningN > 0 ? 'running' : completedN === total ? 'ok' : 'pending';
      var summaryBits = [];
      summaryBits.push(completedN + '/' + total);
      if (runningN > 0) summaryBits.push(runningN + ' 运行中');
      if (failedN > 0) summaryBits.push(failedN + ' 需关注');
      if (counts.skipped > 0) summaryBits.push(counts.skipped + ' 已最新');

      var rowsHtml = groupSteps.map(function (s) {
        var st = s.display_status || s.status || 'idle';
        var color = STATUS_COLORS[st] || 'var(--cm-ink-300)';
        var icon = STEP_STATUS_ICON[st] || '○';
        var label = STEP_STATUS_LABEL[st] || '';
        if (_stopRequestedUi && st === 'running') label = '停止中';
        var timeStr = fmtTime(s.finished_at || s.started_at);
        // 行展示文案优先级: detail.message > '写入 N 条' > '' (idle)
        var recordStr = '';
        var recordNum = Number(s.records);
        if (s.detail && s.detail.message) {
          recordStr = String(s.detail.message);
        } else if (Number.isFinite(recordNum) && recordNum > 0) {
          recordStr = fmt(recordNum) + ' 条';
        } else if (st === 'skipped') {
          // skipped 但无 message 时, 至少给个 "已最新" 占位, 避免空行
          recordStr = '已最新';
        }
        var detailHtml = s.step_id === 'sync_market_data'
          ? renderMarketSyncDetail(s)
          : (s.step_id === 'sync_industry' ? renderIndustrySyncDetail(s)
            : (s.step_id === 'sync_financial' ? renderFinancialSyncDetail(s) : ''));
        var auditInline = (s.audit_notes || []).map(function (note) {
          var formatted = formatAuditNote(note);
          var nc = formatted.tone === 'issue' ? 'var(--stock-up)' : 'var(--cm-ink-500)';
          return '<span class="step-row-audit" style="color:' + nc + '">' + formatted.html + '</span>';
        }).join('');
        var reasonStr = '';
        if (s.error && !s.detail && !['completed', 'partial', 'idle'].includes(st)) {
          reasonStr = '<span class="step-row-error" style="color:' + (st === 'failed' ? 'var(--stock-up)' : 'var(--cm-warn-500)') + '">' + esc(normalizeStepReason(s)).substring(0, 60) + '</span>';
        }
        var canShowAction = !!s.actionable && !_uiRunning;
        var actionHtml = canShowAction
          ? '<button type="button" class="chip chip-ghost chip-sm step-row-action" data-step-id="' + esc(s.step_id) + '" data-step-name="' + esc(s.step_name || s.step_id) + '">补跑</button>'
          : '<span class="step-row-action-placeholder"></span>';

        var rowCls = 'step-row step-row-' + esc(st);
        return '<div class="' + rowCls + '">' +
          '<span class="step-row-icon" style="color:' + color + '">' + icon + '</span>' +
          '<span class="step-row-name">' + esc(s.step_name || s.step_id) + '</span>' +
          '<span class="step-row-label" style="color:' + color + '">' + esc(label) + '</span>' +
          '<span class="step-row-meta">' + (recordStr ? esc(recordStr) + ' · ' : '') + (timeStr ? esc(timeStr) : '') + '</span>' +
          '<span class="step-row-notes">' + auditInline + reasonStr + '</span>' +
          actionHtml +
          '</div>' +
          (detailHtml ? '<div class="step-row-detail">' + detailHtml + '</div>' : '');
      }).join('');

      html += '<details class="step-group step-group-' + gId + ' step-group-tone-' + groupTone + '" data-group="' + gId + '"' + (shouldOpen ? ' open' : '') + '>' +
        '<summary class="step-group-summary">' +
          '<span class="step-group-badge">' + def.badge + '</span>' +
          '<span class="step-group-name">' + def.name + '</span>' +
          '<span class="step-group-stats">' + summaryBits.join(' · ') + '</span>' +
          '<button type="button" class="chip chip-outline chip-sm step-group-run-btn" data-group="' + gId + '"' + (_uiRunning ? ' disabled' : '') + '>' + def.verb + '</button>' +
        '</summary>' +
        '<div class="step-group-body">' + rowsHtml + '</div>' +
      '</details>';
    });

    el('stepGrid').innerHTML = html;

    el('stepGrid').querySelectorAll('.step-group-run-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault(); e.stopPropagation();
        if (_uiRunning) return;
        runGroup(btn.dataset.group, btn.textContent);
      });
    });
    el('stepGrid').querySelectorAll('.step-row-action').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        runSingleStep(btn.dataset.stepId, btn.dataset.stepName);
      });
    });
  }
  function renderIdleStepGrid() {
    renderStepGrid(STEP_DEFAULTS);
  }

  async function runGroup(groupId, groupName) {
    if (!groupId) return;
    if (_uiRunning) {
      await modalAlert('当前已有更新任务在运行，请稍候。');
      return;
    }

    // groupId is one of 'data' (sync), 'calc' (calc), 'mart' (mart)
    var endpoint = groupId === 'data' ? '/api/inst/update/sync' : '/api/inst/update/' + groupId;
    var r = await api(endpoint, { method: 'POST' });
    if (!r || !r.ok) {
      addLog('启动失败: ' + (r ? r.message || r.error : '未知错误'), true);
      return;
    }

    el('btnUpdateAll').disabled = true; el('btnUpdateAll').textContent = '更新中...';
    el('btnStop').disabled = false; el('btnStop').textContent = '停止';
    el('btnStop').style.display = ''; el('progressArea').style.display = '';

    _activeRunContext = { mode: groupId, step_ids: r.step_ids || [] };
    _uiRunning = true;
    _stopRequestedUi = false;

    if (_poll) clearInterval(_poll);
    _poll = setInterval(pollStatus, 1000);
    pollStatus();
  }

  var _poll = null;
  var _lastServerLogId = 0;
  var _pollRequestSeq = 0;
  var _pollAppliedSeq = 0;
  var _pollInFlight = false;
  var _lastStepGridSig = '';
  var _lastWorkbenchSummarySig = '';
  async function startUpdate() {
    _activeRunContext = null;
    _lastRunContext = null;
    _uiRunning = true;
    _stopRequestedUi = false;
    el('btnUpdateAll').disabled = true; el('btnUpdateAll').textContent = '分析中...';
    el('btnStop').disabled = false; el('btnStop').textContent = '停止';
    el('btnStop').style.display = ''; el('progressArea').style.display = '';
    el('updateLog').innerHTML = ''; _lastServerLogId = 0; addLog('正在分析派生层状态...');
    // P3 (2026-04-27): 工作台跑 calc + mart 链路, 不再触发 data 组 (那里是数据页职责)
    // /api/inst/update/calc 跑事实计算 → 自动续跑 mart 链路
    var r = await api('/api/inst/update/calc', { method: 'POST' });
    if (!r || !r.ok) {
      if (r && r.message) addLog(r.message);
      else addLog('启动失败', true);
      el('btnUpdateAll').disabled = false; el('btnUpdateAll').textContent = '智能更新';
      el('btnStop').style.display = 'none';
      return;
    }
    if (r.noop || !r.steps) {
      _uiRunning = false;
      _activeRunContext = null;
      el('btnUpdateAll').disabled = false;
      el('btnUpdateAll').textContent = '重算派生层';
      el('btnStop').style.display = 'none';
      await refreshDashboardStatus(true);
      addLog(r.message || '数据已是最新，无需更新');
      return;
    }
    _activeRunContext = { mode: 'smart', step_ids: Array.isArray(r.step_ids) ? r.step_ids : [] };
    // 显示计划摘要
    if (r.plan) {
      var reasons = r.plan.reason || [];
      reasons.forEach(function (reason) { addLog('计划: ' + reason); });
      var skipReasons = r.plan.skip_reasons || {};
      Object.keys(skipReasons).forEach(function (stepId) {
        var step = STEP_DEFAULTS.find(function (item) { return item.step_id === stepId; });
        var name = step ? step.step_name : stepId;
        addLog('跳过: ' + name + ' · ' + skipReasons[stepId]);
      });
      addLog('开始执行 ' + (r.steps || 0) + ' 个步骤...');
    }
    el('btnUpdateAll').textContent = '更新中...';
    renderStepGrid(makePlannedSteps(r.step_ids, r.plan?.skip_reasons));
    renderWorkbenchSummary({
      kind: 'running',
      pct: 0,
      message: '智能更新 · 0/' + (r.steps || 0) + ' · 0%',
      counts: { total: r.steps || 0, done: 0 },
      show_progress: true
    });
    if (_poll) clearInterval(_poll);
    await pollStatus();
    if (_uiRunning) _poll = setInterval(pollStatus, 1000);
  }
  async function pollStatus() {
    if (_pollInFlight) return;
    var requestSeq = ++_pollRequestSeq;
    _pollInFlight = true;
    var r = await api('/api/inst/update/status');
    _pollInFlight = false;
    if (!r) return;
    if (requestSeq < _pollAppliedSeq) return;
    _pollAppliedSeq = requestSeq;
    if (r.run_context) _activeRunContext = r.run_context;
    if (r.last_run_context) _lastRunContext = r.last_run_context;
    _stopRequestedUi = !!r.stop_requested;
    var effectiveRunning = !!r.running;
    if (
      effectiveRunning &&
      r.summary?.counts?.total > 0 &&
      r.summary?.counts?.done >= r.summary?.counts?.total &&
      (!r.summary?.active_step_ids || !r.summary.active_step_ids.length)
    ) {
      effectiveRunning = false;
    }
    if (r.steps) {
      var steps = withAuditNotes(mergeStepDefinitions(r.steps), _lastAuditSnapshot || {});
      maybeRenderStepGrid(steps);
      maybeRenderWorkbenchSummary(r.summary);
      syncServerLogs(r.logs || [], false);
      if (effectiveRunning) {
        el('btnUpdateAll').textContent = r.stop_requested ? '停止中...' : '更新中...';
        el('btnStop').disabled = !!r.stop_requested;
        el('btnStop').textContent = r.stop_requested ? '停止中...' : '停止';
        return;
      }
      clearInterval(_poll);
      _poll = null;
      _uiRunning = false;
      _lastRunContext = r.last_run_context || _activeRunContext || _lastRunContext;
      _activeRunContext = null;
      el('btnUpdateAll').disabled = false; el('btnUpdateAll').textContent = '智能更新';
      el('btnStop').style.display = 'none';
      await renderUpdatePanel(r, { forceAudit: true });
    }
  }

  async function runSingleStep(stepId, stepName) {
    if (!stepId) return;
    if (_uiRunning) {
      await modalAlert('当前已有更新任务在运行，请稍候。');
      return;
    }
    var r = await api('/api/inst/update/step/' + encodeURIComponent(stepId), { method: 'POST' });
    if (!r || !r.ok) {
      await modalAlert((r && (r.message || r.error)) || '单步执行启动失败');
      return;
    }
    _activeRunContext = { mode: 'single', step_id: stepId, step_name: stepName || stepId };
    if (Array.isArray(r.steps) && r.steps.length) _activeRunContext.step_ids = r.steps;
    _lastRunContext = null;
    _uiRunning = true;
    el('btnUpdateAll').disabled = true;
    el('btnUpdateAll').textContent = '更新中...';
    el('btnStop').disabled = false;
    el('btnStop').textContent = '停止';
    el('btnStop').style.display = '';
    el('progressArea').style.display = '';
    el('updateLog').innerHTML = '';
    _lastServerLogId = 0;
    addLog('开始单独执行: ' + (stepName || stepId));
    if (Array.isArray(r.steps) && r.steps.length > 1) {
      var names = r.steps.slice(1).map(function (id) {
        var step = STEP_DEFAULTS.find(function (s) { return s.step_id === id; });
        return step ? step.step_name : id;
      });
      if (names.length) addLog('将自动续跑下游: ' + names.join(' -> '));
    }
    var status = await api('/api/inst/update/status');
    if (status?.steps) {
      if (!_lastAuditSnapshot && !_auditRefreshPromise) refreshAuditSnapshot();
      var steps = withAuditNotes(status.steps, _lastAuditSnapshot || {});
      maybeRenderStepGrid(steps);
      maybeRenderWorkbenchSummary(status.summary);
      syncServerLogs(status.logs || [], Array.isArray(status.logs) && status.logs.length > 0);
    }
    if (_poll) clearInterval(_poll);
    _poll = setInterval(pollStatus, 1000);
  }

  function logClass(level) {
    if (level === 'error') return 'log-err';
    if (level === 'warning' || level === 'warn') return 'log-warn';
    return 'log-ok';
  }

  function appendLogEntry(entry) {
    var box = el('updateLog');
    var t = entry.ts
      ? new Date(entry.ts).toLocaleTimeString('zh-CN', { hour12: false })
      : new Date().toLocaleTimeString('zh-CN', { hour12: false });
    box.innerHTML += '<div class="log-line"><span class="log-time">' + esc(t) + '</span><span class="' + logClass(entry.level) + '">' + esc(entry.message) + '</span></div>';
    box.scrollTop = box.scrollHeight;
  }

  function syncServerLogs(logs, replace) {
    if (!Array.isArray(logs)) return;
    if (replace) {
      if (!logs.length && el('updateLog').innerHTML.trim()) return;
      el('updateLog').innerHTML = '';
      _lastServerLogId = 0;
    }
    logs.forEach(function (log) {
      if (!replace && typeof log.id === 'number' && log.id <= _lastServerLogId) return;
      appendLogEntry(log);
      if (typeof log.id === 'number' && log.id > _lastServerLogId) _lastServerLogId = log.id;
    });
  }

  function addLog(msg, isErr) {
    appendLogEntry({
      ts: new Date().toISOString(),
      level: isErr ? 'error' : 'info',
      message: msg
    });
  }

  // ============================================================
  // Institution Management (inside Research)
  // ============================================================
  var _mgmtData = [];
  var _mgmtActiveType = 'all';  // 记住当前选中的分类
  var _chunkTableRenderSeq = 0;

  function renderChunkedTable(containerEl, options) {
    if (!containerEl) return;
    var rows = options.rows || [];
    var head = options.head || '';
    var emptyColspan = options.emptyColspan || 1;
    var emptyText = options.emptyText || '暂无数据';
    var chunkSize = options.chunkSize || 120;
    var progressLabel = options.progressLabel || '正在渲染';
    if (!rows.length) {
      containerEl.innerHTML = head + '<tr><td class="empty-row" colspan="' + emptyColspan + '">' + emptyText + '</td></tr></tbody></table>';
      if (typeof options.afterRender === 'function') options.afterRender(containerEl, true);
      return;
    }
    var seq = ++_chunkTableRenderSeq;
    containerEl.innerHTML =
      '<div class="muted" style="font-size:11px;margin-bottom:8px">' + progressLabel + ' 0 / ' + rows.length + ' ...</div>' +
      head + '</tbody></table>';
    var hint = containerEl.querySelector('.muted');
    var tbody = containerEl.querySelector('tbody');
    var offset = 0;

    function flushChunk() {
      if (seq !== _chunkTableRenderSeq || !tbody) return;
      var slice = rows.slice(offset, Math.min(offset + chunkSize, rows.length));
      tbody.insertAdjacentHTML('beforeend', slice.join(''));
      offset += slice.length;
      if (hint) {
        hint.textContent = offset < rows.length
          ? (progressLabel + ' ' + offset + ' / ' + rows.length + ' ...')
          : ('共 ' + rows.length + ' 行，已完成渲染');
      }
      if (offset < rows.length) {
        requestAnimationFrame(flushChunk);
        return;
      }
      if (typeof options.afterRender === 'function') options.afterRender(containerEl, false);
    }

    requestAnimationFrame(flushChunk);
  }

  async function loadInstMgmt() {
    var r = await api('/api/inst/institutions?show=all');
    if (!r?.data) return;
    _mgmtData = r.data;
    var types = [...new Set(r.data.map(i => i.type).filter(Boolean))];
    var filterEl = el('mgmtTypeFilter');
    filterEl.innerHTML = typeTag('all', '全部 ' + r.data.length) + types.map(t => typeTag(t, t + ' ' + r.data.filter(i => i.type === t).length)).join('');
    filterEl.querySelectorAll('.type-tag').forEach(tag => {
      tag.addEventListener('click', () => {
        filterEl.querySelectorAll('.type-tag').forEach(t => t.classList.remove('active'));
        tag.classList.add('active');
        _mgmtActiveType = tag.dataset.type;
        renderMgmtTable(r.data, tag.dataset.type);
      });
    });
    // 恢复之前选中的分类
    var activeTag = filterEl.querySelector('.type-tag[data-type="' + _mgmtActiveType + '"]') || filterEl.querySelector('.type-tag');
    if (activeTag) activeTag.classList.add('active');
    renderMgmtTable(r.data, _mgmtActiveType);
  }

  function renderMgmtTable(data, tf) {
    var d = tf === 'all' ? data : data.filter(i => i.type === tf);
    var head = '<table class="data-table"><thead><tr><th style="width:30px"><input type="checkbox" class="row-check" id="checkAll"></th><th>机构名称</th><th>简称</th><th>类型</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    renderChunkedTable(el('instMgmtTable'), {
      head: head,
      rows: d.map(function (i) {
        var st = i.blacklisted ? evTag('exit', '已拉黑') : i.merged_into ? evTag('unchanged', '已合并') : !i.enabled ? evTag('unchanged', '禁用') : evTag('new_entry', '正常');
        var ops = '';
        if (i.merged_into || i.blacklisted || !i.enabled) {
          ops = '<button class="chip chip-ok chip-sm" onclick="App.restoreInst(\'' + esc(i.id) + '\')">恢复</button> ' +
            '<button class="chip chip-danger chip-sm" onclick="App.deleteInst(\'' + esc(i.id) + '\')">删除</button>';
        } else {
          ops = '<button class="chip chip-outline chip-sm" onclick="App.setAlias(\'' + esc(i.id) + '\')">简称</button> ' +
            '<button class="chip chip-outline chip-sm" onclick="App.setType(\'' + esc(i.id) + '\')">类型</button> ' +
            '<button class="chip chip-warn chip-sm" onclick="App.toggleBlack(\'' + esc(i.id) + '\',1)">拉黑</button> ' +
            '<button class="chip chip-danger chip-sm" onclick="App.deleteInst(\'' + esc(i.id) + '\')">删除</button>';
        }
        return '<tr' + (i.blacklisted || i.merged_into ? ' style="opacity:0.6"' : '') + '>' +
          '<td><input type="checkbox" class="row-check" data-id="' + esc(i.id) + '"></td>' +
          '<td><span class="clickable-name" onclick="App.toggleInstDetail(\'' + esc(i.id) + '\',this)">' + esc(i.name || '') + '</span></td><td>' + esc(i.display_name || '') + '</td>' +
          '<td>' + typeTag(i.type) + '</td><td>' + st + '</td>' +
          '<td style="white-space:nowrap">' + ops + '</td></tr>';
      }),
      emptyColspan: 6,
      emptyText: '暂无机构',
      chunkSize: 80,
      progressLabel: '正在渲染机构',
      afterRender: function () {
        document.getElementById('checkAll')?.addEventListener('change', function () {
          document.querySelectorAll('#instMgmtTable .row-check[data-id]').forEach(cb => cb.checked = this.checked);
        });
        scheduleSortableTables('instMgmtTable');
      }
    });
  }

  function getCheckedIds() {
    return [...document.querySelectorAll('#instMgmtTable .row-check[data-id]:checked')].map(cb => cb.dataset.id);
  }

  async function restoreInst(id) {
    if (!await modalConfirm('恢复该机构为正常状态？')) return;
    await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ blacklisted: 0, enabled: 1, merged_into: null }) });
    loadInstMgmt();
  }
  async function setAlias(id) { var v = await modalPrompt('设置简称'); if (v === null) return; await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ display_name: v }) }); loadInstMgmt(); }
  async function setType(id) { var v = await modalTypeSelect('设置类型'); if (!v) return; await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ type: v }) }); loadInstMgmt(); }
  async function toggleBlack(id, val) { await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ blacklisted: val }) }); loadInstMgmt(); }
  async function deleteInst(id) { if (!await modalConfirm('确定删除？')) return; await api('/api/inst/institutions/' + id, { method: 'DELETE' }); loadInstMgmt(); }
  async function batchAlias() {
    var ids = getCheckedIds(); if (!ids.length) { await modalAlert('请先勾选'); return; }
    var fr = await modalFindReplace('批量简称 — 查找替换');
    if (!fr) return;
    var changed = 0;
    for (var id of ids) {
      var inst = _mgmtData.find(function (i) { return i.id === id });
      if (!inst) continue;
      // 从当前简称（有则用）或原名开始替换
      var name = inst.display_name || inst.name || '';
      var newName = name;
      // 逐个替换每个查找词
      for (var fi = 0; fi < fr.finds.length; fi++) {
        newName = newName.split(fr.finds[fi]).join(fr.replace);
      }
      newName = newName.trim();
      if (newName && newName !== (inst.display_name || '')) {
        await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ display_name: newName }) });
        changed++;
      }
    }
    await modalAlert('已更新 ' + changed + ' 个机构的简称');
    loadInstMgmt();
  }
  async function batchBlack() {
    var ids = getCheckedIds(); if (!ids.length) { await modalAlert('请先勾选'); return; }
    if (!await modalConfirm('确定拉黑选中的 ' + ids.length + ' 个机构？')) return;
    for (var id of ids) await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ blacklisted: 1 }) });
    loadInstMgmt();
  }
  async function searchInst() {
    var kw = el('mgmtSearch').value.trim();
    if (!kw) { modalAlert('请输入关键词'); return; }
    var holderType = el('searchHolderType')?.value || '';
    el('searchResultArea').style.display = '';
    el('searchResults').innerHTML = '<div class="muted">搜索中...</div>';
    var url = '/api/inst/institutions/search?keywords=' + encodeURIComponent(kw);
    if (holderType) url += '&holder_type=' + encodeURIComponent(holderType);
    var r = await api(url);
    if (!r?.ok || !r.data) { el('searchResults').innerHTML = '<div class="muted">搜索失败</div>'; return; }
    el('searchResultCount').textContent = '共 ' + r.total + ' 个结果' + (r.keywords ? '（关键词: ' + r.keywords.join(' + ') + '）' : '');
    if (!r.data.length) { el('searchResults').innerHTML = '<div class="muted">未找到匹配的机构</div>'; return; }
    var head = '<table class="data-table"><thead><tr><th style="width:30px"><input type="checkbox" id="searchCheckAll"></th><th>机构名称</th><th>东财分类</th><th>当前持仓数</th><th>最新公告</th><th>状态</th></tr></thead><tbody>';
    renderChunkedTable(el('searchResults'), {
      head: head,
      rows: r.data.map(function (item) {
        var st = item.tracked ? '<span style="color:var(--stock-down)">已跟踪</span>' : '<span style="color:var(--cm-ink-300)">未跟踪</span>';
        var disabled = item.tracked ? ' disabled' : '';
        return '<tr><td><input type="checkbox" class="search-check" data-name="' + esc(item.holder_name) + '"' + disabled + '></td>' +
          '<td>' + esc(item.holder_name) + '</td><td>' + esc(item.holder_type || '') + '</td><td>' + item.stock_count + '</td>' +
          '<td>' + fmtDate(item.latest_notice) + '</td><td>' + st + '</td></tr>';
      }),
      emptyColspan: 6,
      emptyText: '未找到匹配的机构',
      chunkSize: 80,
      progressLabel: '正在渲染搜索结果',
      afterRender: function () {
        document.getElementById('searchCheckAll')?.addEventListener('change', function () {
          document.querySelectorAll('.search-check:not(:disabled)').forEach(function (cb) { cb.checked = this.checked; }.bind(this));
        });
        scheduleSortableTables('searchResults');
      }
    });
  }

  async function importChecked() {
    // 获取标签
    var typeVal = el('addInstType').value;
    if (!typeVal) { await modalAlert('请选择标签类型（必填）'); return; }
    // 获取选中的机构
    var names = [];
    document.querySelectorAll('.search-check:checked:not(:disabled)').forEach(function (cb) { names.push(cb.dataset.name); });
    if (!names.length) { modalAlert('请勾选要导入的机构'); return; }
    if (!await modalConfirm('确认导入 ' + names.length + ' 个机构，标签: ' + typeVal + '？')) return;
    // 批量添加
    var items = names.map(function (n) { return { name: n, type: typeVal }; });
    var r = await api('/api/inst/institutions/batch', { method: 'POST', body: JSON.stringify({ institutions: items }) });
    if (r?.ok) {
      modalAlert('已导入 ' + (r.created || names.length) + ' 个机构，系统正在后台自动匹配持仓和计算数据...');
      el('searchResultArea').style.display = 'none';
      el('mgmtSearch').value = '';
      loadInstMgmt();
    } else { modalAlert('导入失败'); }
  }
  async function batchType() {
    var ids = getCheckedIds(); if (!ids.length) { await modalAlert('请先勾选'); return; }
    var type = await modalTypeSelect('批量设类型'); if (!type) return;
    for (var id of ids) await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ type: type }) });
    loadInstMgmt();
  }
  async function batchMerge() {
    var ids = getCheckedIds(); if (ids.length < 2) { modalAlert('至少勾选2个'); return; }
    var target = prompt('合并到哪个？输入目标机构ID（第一个勾选的ID: ' + ids[0] + '）:', ids[0]); if (!target) return;
    for (var id of ids) { if (id !== target) await api('/api/inst/institutions/' + id, { method: 'PUT', body: JSON.stringify({ merged_into: target, enabled: 0 }) }); }
    loadInstMgmt();
  }
  async function batchDelete() {
    var ids = getCheckedIds(); if (!ids.length) { modalAlert('请先勾选'); return; }
    if (!await modalConfirm('确定删除 ' + ids.length + ' 个机构？')) return;
    for (var id of ids) await api('/api/inst/institutions/' + id, { method: 'DELETE' });
    loadInstMgmt();
  }

  // ============================================================
  // Lightweight Panels
  // ============================================================
  function togglePanel(buttonId, panelId, openText, closeText) {
    var btn = el(buttonId), panel = el(panelId);
    if (!btn || !panel) return;
    var isOpen = panel.style.display !== 'none';
    var nextOpen = !isOpen;
    panel.style.display = nextOpen ? '' : 'none';
    btn.textContent = nextOpen ? closeText : openText;
    btn.classList.toggle('active', nextOpen);
    if (!btn.classList.contains('pill-tab-btn')) {
      btn.style.borderColor = nextOpen ? 'var(--cm-brand-400)' : '';
      btn.style.color = nextOpen ? 'var(--cm-brand-500)' : '';
      btn.style.background = nextOpen ? 'var(--cm-brand-50)' : '';
    }
  }

  async function resetDerivedData() {
    if (!await modalConfirm('确认清空事件、收益、画像、关系和股票列表等派生层，并在之后重新计算吗？')) return;
    var r = await api('/api/inst/update/reset-derived', { method: 'POST' });
    if (r?.ok) {
      await modalAlert(r.message || '已重置派生数据');
      loadWorkbench();
    } else {
      await modalAlert((r && (r.message || r.error)) || '重置失败');
    }
  }

  async function copyLogs() {
    var lines = Array.from(document.querySelectorAll('#updateLog .log-line')).map(function (line) {
      return line.innerText.trim();
    }).filter(Boolean);
    if (!lines.length) {
      await modalAlert('当前没有可复制的日志');
      return;
    }
    var text = lines.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      await modalAlert('运行日志已复制到剪贴板');
    } catch (e) {
      var textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      await modalAlert('运行日志已复制到剪贴板');
    }
  }

  // ============================================================
  // Exclusions
  // ============================================================
  async function loadExclusions() {
    var r = await api('/api/inst/exclusions/categories');
    var cats = r?.data?.length ? r.data : [
      { category: 'ST', label: 'ST/*ST — 退市风险警示', enabled: 1 }, { category: 'DELISTED', label: '退市股', enabled: 1 },
      { category: 'OLD_OTC', label: '老三板 (400xxx)', enabled: 1 }, { category: 'BSE', label: '北交所', enabled: 1 },
      { category: 'NEEQ', label: '新三板', enabled: 1 }, { category: 'B_SHARE', label: 'B股', enabled: 1 },
      { category: 'CDR', label: 'CDR存托凭证 (689xxx)', enabled: 1 },
      { category: 'SCIENCE', label: '科创板 (688xxx)', enabled: 0 }, { category: 'GEM', label: '创业板 (300xxx)', enabled: 0 },
    ];
    el('exclusionCategories').innerHTML = cats.map(c =>
      '<div class="excl-category"><button class="excl-toggle' + (c.enabled ? ' on' : '') + '" data-cat="' + esc(c.category) + '"></button><span class="excl-label">' + esc(c.label || c.category) + '</span></div>'
    ).join('');
    el('exclusionCategories').querySelectorAll('.excl-toggle').forEach(btn => btn.addEventListener('click', () => btn.classList.toggle('on')));
  }

  // ============================================================
  // Detail Panels (click to expand)
  // ============================================================
  function removeDetailPanel(panelEl) {
    if (panelEl && panelEl.parentNode) panelEl.parentNode.removeChild(panelEl);
  }

  function toggleInstDetail(instId, clickedEl) {
    // If already open, close it
    var existing = document.querySelector('.detail-panel[data-detail-inst="' + instId + '"]');
    if (existing) { removeDetailPanel(existing.closest('tr.detail-row') || existing); clickedEl.removeAttribute('data-detail-open'); return; }
    // Close any other open inst detail
    document.querySelectorAll('.detail-panel[data-detail-inst]').forEach(function (p) { removeDetailPanel(p.closest('tr.detail-row') || p); });
    document.querySelectorAll('[data-detail-open="inst"]').forEach(function (e) { e.removeAttribute('data-detail-open'); });
    clickedEl.setAttribute('data-detail-open', 'inst');

    // Determine context: inside a table row or inside a card grid
    var tr = clickedEl.closest('tr');
    var cardEl = clickedEl.closest('.inst-card');
    var panel = document.createElement('div');
    panel.className = 'detail-panel';
    panel.setAttribute('data-detail-inst', instId);
    panel.innerHTML = '<div class="detail-loading">加载中...</div>';

    if (tr) {
      // Table context: insert a new tr below
      var colCount = tr.children.length;
      var detailTr = document.createElement('tr');
      detailTr.className = 'detail-row';
      var td = document.createElement('td');
      td.setAttribute('colspan', colCount);
      td.appendChild(panel);
      detailTr.appendChild(td);
      tr.parentNode.insertBefore(detailTr, tr.nextSibling);
    } else if (cardEl) {
      // Card grid context: insert after the card, spanning full width
      cardEl.parentNode.insertBefore(panel, cardEl.nextSibling);
    } else {
      // Fallback: insert after clicked element
      clickedEl.parentNode.insertBefore(panel, clickedEl.nextSibling);
    }

    Promise.all([
      api('/api/inst/profiles/detail/' + encodeURIComponent(instId)),
      api('/api/inst/profiles'),
      api('/api/inst/profiles/returns-history/' + encodeURIComponent(instId)),
      api('/api/signals/institution/' + encodeURIComponent(instId)).catch(function () { return null; }),
    ]).then(function (results) {
      var r = results[0], profilesResp = results[1], chartResp = results[2];
      var sv2 = results[3];
      if (!r || !r.ok) { panel.innerHTML = '<div class="detail-loading">加载失败</div>'; return; }
      var holdings = r.data || [];

      // 从 profiles 中找到该机构的画像
      var p = (profilesResp?.data || []).find(function (x) { return x.institution_id === instId }) || {};

      // 画像摘要 + 收益曲线
      var chartSvg = (chartResp?.ok && chartResp.data?.length) ? buildReturnsSvg(chartResp.data, 400, 60) : '<span class="muted">暂无收益数据</span>';
      var html = '<div style="margin-bottom:6px;font-size:12px;color:var(--cm-ink-700);background:var(--cm-ink-50);padding:6px 10px;border-radius:4px">' +
        '<strong>研究层画像</strong>&nbsp;—&nbsp;描述性指标，不直接决定是否跟投；执行主口径见下方"跟随决策 track record"。' +
        '</div>' +
        '<div style="display:flex;gap:16px;margin-bottom:12px;flex-wrap:wrap;align-items:flex-start">' +
        '<div style="flex:1;min-width:280px">' +
        '<div style="display:flex;flex-wrap:wrap;gap:12px;font-size:12px">' +
        metric('实力分', p.quality_score != null ? Number(p.quality_score).toFixed(1) : '-') +
        metric('可跟分', p.followability_score != null ? Number(p.followability_score).toFixed(1) : '-') +
        metric('历史胜率', pct(p.total_win_rate)) + metric('30日胜率', pct(p.win_rate_30d)) +
        metric('60日胜率', pct(p.win_rate_60d)) + metric('最大回撤', p.median_max_drawdown_30d != null ? '-' + p.median_max_drawdown_30d.toFixed(1) + '%' : '-') +
        metric('30日均', fmtGain(p.avg_gain_30d)) + metric('60日均', fmtGain(p.avg_gain_60d)) + metric('120日均', fmtGain(p.avg_gain_120d)) +
        metric('持仓', (p.current_stock_count || 0) + '只') + metric('资金', compactNum(p.current_total_cap)) +
        '</div>' +
        '<div style="display:flex;flex-wrap:wrap;gap:12px;font-size:12px;margin-top:12px">' +
        metric('平均跟随溢价', premiumText(p.avg_premium_pct)) +
        metric('安全跟随样本', fmt(p.safe_follow_event_count || 0)) +
        metric('安全跟随30日胜率', pct(p.safe_follow_win_rate_30d)) +
        metric('安全跟随30日均', fmtGain(p.safe_follow_avg_gain_30d)) +
        metric('安全跟随平均回撤', p.safe_follow_avg_drawdown_30d != null ? '-' + Number(p.safe_follow_avg_drawdown_30d).toFixed(1) + '%' : '-') +
        metric('信号传递效率', p.signal_transfer_efficiency_30d != null ? Number(p.signal_transfer_efficiency_30d).toFixed(0) + '%' : '-') +
        metric('可跟性提示', esc(p.followability_hint || '-')) +
        '</div>' +
        '</div>' +
        '<div style="flex:0 0 420px;border:1px solid var(--cm-ink-50);border-radius:6px;padding:6px;background:var(--cm-surface-warm)">' + chartSvg + '</div>' +
        '</div>';

      // Phase 4: 评分拆解入口
      html += '<div style="margin:8px 0"><button class="chip chip-outline chip-sm" onclick="App.toggleInstBreakdown(\'' + esc(instId) + '\')">评分拆解</button></div>' +
        '<div id="breakdown-' + esc(instId) + '" style="display:none"></div>';

      // Layer B 擅长 L2 评分卡片（§29 顶层设计 W1 接入）
      var lbSummary = r.layer_b_summary;
      if (lbSummary && lbSummary.stable_l2_count > 0) {
        html += '<div style="margin-bottom:14px;border:1px solid var(--cm-brand-100);border-radius:6px;padding:10px;background:var(--cm-brand-50)">' +
          '<div style="background:var(--cm-warn-100);border:1px solid var(--cm-warn-500);color:var(--cm-warn-500);padding:4px 8px;border-radius:3px;font-size:11px;margin-bottom:6px">' +
          '研究参考：历史 walk-forward 结果含 PIT 污染；portfolio 回测未显 excess edge（§2 2026-04-23 收口段）' +
          '</div>' +
          '<div style="font-size:12px;color:var(--cm-brand-700);margin-bottom:6px"><b>Layer B 擅长 L2 评分（研究参考）</b> ' +
          '<span style="color:var(--cm-ink-500);font-size:11px">(' + lbSummary.stable_l2_count + ' 稳定 / ' +
          lbSummary.total_l2_with_score + ' 有评分 L2)</span></div>' +
          '<div style="font-size:12px">';
        (lbSummary.top_stable_l2 || []).forEach(function (l2) {
          var sl = l2.stop_loss != null ? (Number(l2.stop_loss) * 100).toFixed(0) + '%' : '无';
          var tp = l2.take_profit != null ? '+' + (Number(l2.take_profit) * 100).toFixed(0) + '%' : '无';
          var score = l2.stable_score != null ? Number(l2.stable_score).toFixed(1) : '-';
          var sharpe = l2.ho_sharpe != null ? Number(l2.ho_sharpe).toFixed(2) : '-';
          html += '<div style="margin:4px 0;display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
            '<a href="javascript:void(0)" onclick="App.showL2Profile(\'' + esc(l2.l2_name) + '\')" ' +
               'style="color:var(--cm-brand-500);font-weight:600;cursor:pointer;text-decoration:none">' + esc(l2.l2_name) + '</a>' +
            '<span style="background:var(--stock-down);color:white;padding:1px 8px;border-radius:3px;font-weight:600;font-size:11px">' + score + '</span>' +
            '<span style="color:var(--cm-ink-500);font-size:11px">train=' + l2.train_n + ' / ho=' + l2.ho_n + ' / Sharpe=' + sharpe + '</span>' +
            '<span style="color:var(--cm-ink-700);font-size:11px">推荐 持仓' + l2.max_hold_days + 'd / 止损' + sl + ' / 止盈' + tp + '</span>' +
            '</div>';
        });
        html += '</div></div>';
      }

      // 行业分布 + 业绩表现
      var indSummary = r.industry_summary || [];
      if (indSummary.length) {
        html += '<div style="margin-bottom:14px">' +
          '<div style="font-size:11px;color:var(--cm-ink-500);margin-bottom:4px">注意 · 当前行业口径：股票被重分类时，历史事件会被映射到最新行业</div>' +
          '<table class="data-table" style="font-size:12px"><thead><tr>' +
          '<th style="text-align:left">行业</th><th>持仓</th><th>占比</th><th>胜率</th><th>30日均</th>' +
          '</tr></thead><tbody>';
        indSummary.forEach(function (l1) {
          // 一级行业 = 分组行
          var barW = Math.min(l1.pct, 100);
          html += '<tr style="background:var(--cm-bg);cursor:pointer" onclick="var s=this.nextElementSibling;while(s&&!s.classList.contains(\'ind-l1\')){s.style.display=s.style.display===\'none\'?\'table-row\':\'none\';s=s.nextElementSibling;}">' +
            '<td style="font-weight:600;padding-left:8px">' + esc(l1.level1) + ' <div style="background:var(--cm-ink-100);height:4px;border-radius:2px;width:100px;display:inline-block;vertical-align:middle;margin-left:6px"><div style="background:var(--cm-brand-400);height:100%;border-radius:2px;width:' + barW + '%"></div></div></td>' +
            '<td style="text-align:center">' + l1.stock_count + '只</td>' +
            '<td style="text-align:center">' + l1.pct + '%</td>' +
            '<td style="text-align:center">' + (l1.win_rate_30d != null ? ('<span style="color:' + (Number(l1.win_rate_30d) >= 60 ? 'var(--stock-down)' : Number(l1.win_rate_30d) >= 50 ? 'var(--cm-warn-500)' : 'var(--stock-up)') + '">' + Number(l1.win_rate_30d).toFixed(0) + '%</span>') : '-') + '</td>' +
            '<td style="text-align:center">' + (l1.avg_gain_30d != null ? fmtGain(l1.avg_gain_30d) : '-') + '</td></tr>';
          // 二级行业 = 数据行（默认隐藏）
          (l1.children || []).forEach(function (l2) {
            var wrHtml = '-', gainHtml = '-';
            if (l2.win_rate_30d != null) {
              var wr = Number(l2.win_rate_30d);
              var wrColor = wr >= 60 ? 'var(--stock-down)' : wr >= 50 ? 'var(--cm-warn-500)' : 'var(--stock-up)';
              wrHtml = '<span style="color:' + wrColor + ';font-weight:600">' + wr.toFixed(0) + '%</span>';
            }
            if (l2.avg_gain_30d != null) gainHtml = fmtGain(l2.avg_gain_30d);
            // 三级子行业（紧凑展示）
            var l3Str = (l2.children || []).map(function (l3) { return l3.level3 + '(' + l3.stock_count + ')'; }).join(' ');
            html += '<tr style="display:none">' +
              '<td style="padding-left:24px">' + esc(l2.level2) +
              (l3Str ? '<div style="font-size:10px;color:var(--cm-ink-300);margin-top:1px">' + esc(l3Str) + '</div>' : '') +
              '</td>' +
              '<td style="text-align:center">' + l2.stock_count + '只</td><td></td>' +
              '<td style="text-align:center">' + wrHtml + '</td>' +
              '<td style="text-align:center">' + gainHtml + '</td></tr>';
          });
        });
        html += '</tbody></table></div>';
      }

      // 分隔线
      html += '<hr style="border:none;border-top:1px solid var(--cm-ink-100);margin:14px 0">';

      // Signals v2 track record（与 signals-view 抽屉同源，Step 5e 补足任务 1）
      if (sv2 && sv2.overall) {
        html += renderInstSignalsTrackRecord(sv2);
        html += '<hr style="border:none;border-top:1px solid var(--cm-ink-100);margin:14px 0">';
      }

      // 持仓明细表
      if (!holdings.length) { html += '<div class="muted">暂无持仓明细</div>'; panel.innerHTML = html; return; }
      html += '<table class="data-table"><thead><tr><th>股票</th><th>行业</th><th>报告期</th><th>事件</th><th>机构成本</th><th>跟随溢价</th><th>门槛</th><th>持仓市值</th><th>其他机构</th></tr></thead><tbody>';
      html += holdings.map(function (h) {
        var others = (h.other_institutions || []).map(function (o) { return instLink(o.id, o.name, o.type); }).join(' ') || '-';
        // L3 缺失时明确标注"L3未分类"（约一半股票 TDX 源无 L3）
        var indFull = (h.tdx_l1 || '') + (h.tdx_l2 ? ' > ' + h.tdx_l2 : '') + (h.tdx_l2 ? ' > ' + (h.tdx_l3 || 'L3未分类') : '') || '-';
        var cost = h.inst_ref_cost != null ? Number(h.inst_ref_cost).toFixed(2) + '<div class="muted" style="font-size:10px">' + esc(costMethodText(h.inst_cost_method)) + '</div>' : '-';
        var premium = h.premium_pct != null ? premiumText(h.premium_pct) + '<div class="muted" style="font-size:10px">' + esc(h.premium_bucket || '') + '</div>' : '-';
        return '<tr><td>' + stockCell(h.stock_code, h.stock_name) + '</td><td style="font-size:11px;color:var(--cm-ink-500)">' + esc(indFull) + '</td><td>' + fmtDate(h.report_date) + '</td><td>' + (h.event_type ? evTag(h.event_type) : '-') + '</td><td>' + cost + '</td><td>' + premium + '</td><td>' + followGateTag(h.follow_gate, h.follow_gate_reason) + '</td><td>' + compactNum(h.hold_market_cap) + '</td><td>' + others + '</td></tr>';
      }).join('');
      html += '</tbody></table>';
      panel.innerHTML = html;
    });
  }

  // Step 5 任务 1：在机构抽屉末尾追加 signals_v2 track record 块，
  // 与 signals-view.js 的 institution_track_record 组件风格对齐。
  function renderInstSignalsTrackRecord(sv2) {
    var overall = sv2.overall || {};
    var byHorizon = Array.isArray(sv2.by_horizon) ? sv2.by_horizon : [];
    var byIndustry = Array.isArray(sv2.by_industry) ? sv2.by_industry : [];
    var horizonDays = sv2.horizon_days || 60;

    function fmtGainSigned(v) {
      if (v == null) return '-';
      var n = Number(v);
      var cls = n >= 0 ? 'color:var(--cm-ok-500)' : 'color:var(--cm-bad-500)';
      return '<span style="' + cls + '">' + (n >= 0 ? '+' : '') + n.toFixed(2) + '%</span>';
    }
    function fmtWr(wr) {
      if (wr == null) return '-';
      return Math.round(Number(wr) * 100) + '%';
    }
    var metricsHtml =
      '<div style="display:flex;flex-wrap:wrap;gap:12px;font-size:12px;margin-bottom:10px">' +
      metric('总事件', overall.n || 0) +
      metric('全局 EV', fmtGainSigned(overall.ev_pct)) +
      metric('全局胜率', fmtWr(overall.win_rate)) +
      metric('均回撤', overall.avg_drawdown_pct != null ? '-' + Number(overall.avg_drawdown_pct).toFixed(1) + '%' : '-') +
      metric('中位', fmtGainSigned(overall.median_pct)) +
      '</div>';

    var horizonHtml = byHorizon.length
      ? '<div style="margin-bottom:10px"><div style="font-size:12px;font-weight:600;margin-bottom:4px;color:var(--cm-ink-900)">持有期对比（揭示 edge 是短线还是长线）</div>' +
        '<table class="data-table" style="font-size:12px"><thead><tr>' +
        '<th>持有期</th><th class="num">样本</th><th class="num">EV</th><th class="num">胜率</th><th class="num">中位</th>' +
        '</tr></thead><tbody>' +
        byHorizon.map(function (h) {
          var isCurrent = h.horizon_days === horizonDays;
          return '<tr' + (isCurrent ? ' style="background:var(--cm-warn-100)"' : '') + '>' +
            '<td>' + (h.horizon_days || 0) + ' 日' + (isCurrent ? ' <span class="muted" style="font-size:10px">(当前)</span>' : '') + '</td>' +
            '<td class="num">' + (h.n || 0) + '</td>' +
            '<td class="num">' + fmtGainSigned(h.ev_pct) + '</td>' +
            '<td class="num">' + fmtWr(h.win_rate) + '</td>' +
            '<td class="num">' + fmtGainSigned(h.median_pct) + '</td>' +
            '</tr>';
        }).join('') +
        '</tbody></table></div>'
      : '';

    var industryHtml = byIndustry.length
      ? '<div><div style="font-size:12px;font-weight:600;margin-bottom:4px;color:var(--cm-ink-900)">按行业拆分（只展示样本 ≥ 门槛的行业）</div>' +
        '<table class="data-table" style="font-size:12px"><thead><tr>' +
        '<th>行业</th><th class="num">n</th><th class="num">EV</th><th class="num">胜率</th><th class="num">均回撤</th>' +
        '</tr></thead><tbody>' +
        byIndustry.slice(0, 15).map(function (r) {
          return '<tr>' +
            '<td>' + esc(r.industry || '—') + '</td>' +
            '<td class="num">' + (r.n || 0) + '</td>' +
            '<td class="num">' + fmtGainSigned(r.ev_pct) + '</td>' +
            '<td class="num">' + fmtWr(r.win_rate) + '</td>' +
            '<td class="num">' + (r.avg_drawdown_pct != null ? '-' + Number(r.avg_drawdown_pct).toFixed(1) + '%' : '-') + '</td>' +
            '</tr>';
        }).join('') +
        '</tbody></table></div>'
      : '';

    return '<div class="sv2-track-record" style="background:var(--cm-warn-100);padding:12px 14px;border-radius:8px;border:1px solid var(--cm-warn-500)">' +
      '<div style="font-size:13px;font-weight:600;color:var(--cm-ink-900);margin-bottom:8px">' +
      '<span style="background:var(--cm-warn-500);color:var(--cm-surface);font-size:10px;padding:2px 8px;border-radius:3px;margin-right:6px">执行主口径</span>' +
      'signals v2 · track record（' + horizonDays + '日跟随收益口径）' +
      '<span style="font-weight:400;color:var(--cm-ink-500);font-size:11px;margin-left:6px">与上方研究画像冲突时，以此为准</span>' +
      '</div>' +
      metricsHtml + horizonHtml + industryHtml +
      '</div>';
  }

  async function showL2Profile(l2Name) {
    // §29 W1：L2 行业画像弹窗（从机构详情页 Layer B 卡片点击触发）
    var existing = document.getElementById('l2-profile-modal');
    if (existing) existing.remove();
    try {
      var r = await api('/api/inst/industry/l2/' + encodeURIComponent(l2Name));
      if (!r.ok) { alert('加载失败：' + (r.message || '')); return; }
      var s = r.summary || {};
      var html = '<div id="l2-profile-modal" style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)this.remove()">' +
        '<div style="background:white;border-radius:8px;padding:16px 18px;max-width:860px;width:92%;max-height:85vh;overflow:auto;position:relative;box-shadow:0 10px 40px rgba(0,0,0,0.2)">' +
        '<button onclick="this.closest(\'#l2-profile-modal\').remove()" style="position:absolute;top:8px;right:12px;background:none;border:none;font-size:22px;cursor:pointer;color:var(--cm-ink-500);line-height:1">×</button>' +
        '<h3 style="margin:0 0 10px;color:var(--cm-ink-900);font-size:16px">' + esc(l2Name) + ' <span style="font-size:12px;color:var(--cm-ink-500);font-weight:normal">L2 行业画像</span></h3>' +
        '<div style="font-size:12px;color:var(--cm-ink-700);margin-bottom:14px;padding:8px 10px;background:var(--cm-bg);border-radius:4px">' +
          '<span style="margin-right:16px">股票数 <b>' + (s.n_stocks != null ? s.n_stocks : '-') + '</b></span>' +
          '<span style="margin-right:16px">有评分机构 <b>' + (s.n_insts_with_score != null ? s.n_insts_with_score : '-') + '</b></span>' +
          '<span style="margin-right:16px;color:var(--stock-down)">stable <b>' + (s.n_stable || 0) + '</b></span>' +
          '<span style="margin-right:16px;color:var(--cm-warn-500)">weak <b>' + (s.n_weak_positive || 0) + '</b></span>' +
          '<span style="color:var(--stock-up)">overfit <b>' + (s.n_overfit || 0) + '</b></span>' +
          (s.top_score != null ? '<span style="margin-left:16px">最高评分 <b>' + s.top_score + '</b></span>' : '') +
          (s.avg_stable_score != null ? '<span style="margin-left:16px">稳定平均 <b>' + s.avg_stable_score + '</b></span>' : '') +
          '</div>';
      html += '<h4 style="margin:10px 0 6px;font-size:13px;color:var(--cm-ink-700)">稳定 / 弱正机构（按评分降序）</h4>';
      if (r.institutions && r.institutions.length) {
        html += '<table class="data-table" style="font-size:12px;width:100%"><thead><tr>' +
          '<th style="text-align:left">机构</th><th>类型</th><th>评分</th><th>判定</th>' +
          '<th>train n</th><th>ho n</th><th>ho Sharpe</th><th>推荐参数</th></tr></thead><tbody>';
        r.institutions.forEach(function (i) {
          var sl = i.stop_loss != null ? (Number(i.stop_loss) * 100).toFixed(0) + '%' : '无';
          var tp = i.take_profit != null ? '+' + (Number(i.take_profit) * 100).toFixed(0) + '%' : '无';
          var vColor = i.verdict === 'stable' ? 'var(--stock-down)' : 'var(--cm-warn-500)';
          html += '<tr>' +
            '<td><a href="javascript:void(0)" onclick="document.getElementById(\'l2-profile-modal\').remove();App.toggleInstDetail(\'' +
              esc(i.institution_id) + '\')" style="color:var(--cm-brand-500);cursor:pointer">' + esc(i.inst_name || i.institution_id) + '</a></td>' +
            '<td>' + esc(i.inst_type || '-') + '</td>' +
            '<td style="font-weight:600;text-align:center">' + (i.stable_score != null ? Number(i.stable_score).toFixed(1) : '-') + '</td>' +
            '<td style="text-align:center"><span style="color:' + vColor + ';font-weight:600">' + esc(i.verdict || '') + '</span></td>' +
            '<td style="text-align:center">' + (i.train_n != null ? i.train_n : '-') + '</td>' +
            '<td style="text-align:center">' + (i.ho_n != null ? i.ho_n : '-') + '</td>' +
            '<td style="text-align:center">' + (i.ho_sharpe != null ? Number(i.ho_sharpe).toFixed(2) : '-') + '</td>' +
            '<td style="font-size:11px;color:var(--cm-ink-700)">持仓' + (i.max_hold_days || '-') + 'd / 止损' + sl + ' / 止盈' + tp + '</td></tr>';
        });
        html += '</tbody></table>';
      } else {
        html += '<div style="color:var(--cm-ink-300);font-size:12px">暂无稳定机构</div>';
      }
      html += '<h4 style="margin:14px 0 6px;font-size:13px;color:var(--cm-ink-700)">在仓股票（按 follow 机构数降序，Top 50）</h4>';
      if (r.stocks && r.stocks.length) {
        html += '<table class="data-table" style="font-size:12px;width:100%"><thead><tr>' +
          '<th style="text-align:left">代码</th><th style="text-align:left">名称</th>' +
          '<th>持仓机构</th><th>follow 机构</th></tr></thead><tbody>';
        r.stocks.forEach(function (st) {
          html += '<tr>' +
            '<td>' + esc(st.stock_code) + '</td>' +
            '<td>' + esc(st.stock_name || '-') + '</td>' +
            '<td style="text-align:center">' + (st.n_holders || 0) + '</td>' +
            '<td style="text-align:center">' + ((st.n_follow_holders || 0) > 0 ?
              '<b style="color:var(--stock-down)">' + st.n_follow_holders + '</b>' : '0') + '</td></tr>';
        });
        html += '</tbody></table>';
      }
      html += '</div></div>';
      document.body.insertAdjacentHTML('beforeend', html);
    } catch (e) {
      alert('加载失败：' + (e && e.message ? e.message : e));
    }
  }

  async function toggleInstBreakdown(instId) {
    var el = document.getElementById('breakdown-' + instId);
    if (!el) return;
    if (el.style.display !== 'none') {
      el.style.display = 'none';
      return;
    }
    var bd = await App._api('/api/inst/scoring/breakdown/institution/' + encodeURIComponent(instId));
    if (!bd || !bd.ok) return;
    var h = '<div style="padding:10px;background:var(--cm-bg);border-radius:6px;font-size:12px">';
    h += '<b>实力分公式</b>: ' + esc(bd.formula) + '<br>';
    h += '<b>实力分</b>: ' + (bd.quality_score != null ? Number(bd.quality_score).toFixed(1) : '-') + ' | ';
    h += '<b>可跟分</b>: ' + (bd.followability_score != null ? Number(bd.followability_score).toFixed(1) : '-') + '<br>';
    h += '<b>实力置信</b>: ' + esc(bd.score_confidence || '-') + ' | ';
    h += '<b>可跟置信</b>: ' + esc(bd.followability_confidence || '-') + '<br>';
    h += '<b>评分依据</b>: ' + (bd.score_basis === 'buy' ? '买入类事件' : '全事件回退') + '<br><br>';
    h += '<table style="width:100%;font-size:11px"><tr><th>因子</th><th>原始值</th><th>权重</th><th>来源</th></tr>';
    (bd.factors || []).forEach(function (f) {
      h += '<tr><td>' + esc(f.label || '-') + '</td><td>' + (f.raw_value != null ? esc(String(f.raw_value)) : '-') + '</td><td>' + esc(String(f.weight || 0)) + '</td><td style="color:var(--cm-ink-500)">' + esc(f.source || '-') + '</td></tr>';
    });
    h += '</table></div>';
    if (bd.followability) {
      h += '<div style="margin-top:8px;padding:10px;background:var(--cm-surface);border:1px solid var(--cm-ink-100);border-radius:6px;font-size:12px">';
      h += '<b>可跟性画像</b>';
      h += '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px">';
      h += '<span>平均溢价: ' + esc(premiumText(bd.followability.avg_premium_pct)) + '</span>';
      h += '<span>安全样本: ' + esc(String(bd.followability.safe_follow_event_count || 0)) + '</span>';
      h += '<span>安全胜率: ' + esc(pct(bd.followability.safe_follow_win_rate_30d)) + '</span>';
      h += '<span>安全30日均: ' + (bd.followability.safe_follow_avg_gain_30d != null ? fmtGain(bd.followability.safe_follow_avg_gain_30d) : '-') + '</span>';
      h += '<span>安全平均回撤: ' + (bd.followability.safe_follow_avg_drawdown_30d != null ? '-' + Number(bd.followability.safe_follow_avg_drawdown_30d).toFixed(1) + '%' : '-') + '</span>';
      h += '<span>传递效率: ' + (bd.followability.signal_transfer_efficiency_30d != null ? esc(Number(bd.followability.signal_transfer_efficiency_30d).toFixed(0) + '%') : '-') + '</span>';
      h += '<span>提示: ' + esc(bd.followability.followability_hint || '-') + '</span>';
      h += '</div></div>';
    }
    el.innerHTML = h;
    el.style.display = 'block';
  }

  function renderMultidimScoreCard(m) {
    // §29.4 W2：股票三维画像评分卡片
    if (!m || !m.n_dimensions_available) return '';
    var c = m.components || {};
    function detail(key) {
      if (key === 'resonance' && c.resonance) {
        return c.resonance.n_holders + ' 家持仓 · ' + (c.resonance.n_stable_l2_matched || 0) + ' 家 L2 擅长';
      }
      if (key === 'margin' && c.margin && c.margin.rz_balance_yuan != null) {
        return '融资余额 ' + (c.margin.rz_balance_yuan / 1e8).toFixed(2) + ' 亿 · 市场分位 ' + (c.margin.market_percentile != null ? c.margin.market_percentile.toFixed(0) : '-');
      }
      if (key === 'survey' && c.survey) {
        return '近 60 天 ' + (c.survey.inst_count_60d || 0) + ' 家调研';
      }
      return '-';
    }
    var dims = [
      { key: 'resonance', score: m.resonance_score, label: '机构共振' },
      { key: 'margin',    score: m.margin_score,    label: '两融情绪' },
      { key: 'survey',    score: m.survey_score,    label: '调研热度' },
    ];
    var overall = m.overall_score;
    var ovColor = overall == null ? 'var(--cm-ink-300)' : overall >= 60 ? 'var(--stock-down)' : overall >= 40 ? 'var(--cm-warn-500)' : 'var(--stock-up)';
    var html = '<div style="margin:10px 0;border:1px solid var(--cm-accent-warm);border-radius:6px;padding:12px;background:var(--cm-macaron-lilac)">' +
      '<div style="background:var(--cm-warn-100);border:1px solid var(--cm-warn-500);color:var(--cm-warn-500);padding:6px 10px;border-radius:4px;font-size:12px;margin-bottom:10px;font-weight:600">' +
      '研究参考画像，非可交易评分（§2 2026-04-23 收口段）' +
      '</div>' +
      '<div style="display:flex;align-items:center;margin-bottom:10px">' +
      '<b style="font-size:13px;color:var(--cm-accent-vivid)">画像评分（研究参考）</b>' +
      '<span style="margin-left:10px;font-size:11px;color:var(--cm-ink-500)">三维平均，未经 PIT 校准</span>';
    if (overall != null) {
      html += '<span style="margin-left:auto;background:' + ovColor + ';color:white;padding:4px 14px;border-radius:4px;font-weight:600;font-size:13px">综合 ' + overall.toFixed(1) + '</span>';
    }
    html += '</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;font-size:12px">';
    dims.forEach(function (d) {
      var s = d.score;
      var display = s != null ? Number(s).toFixed(1) : '-';
      var color = s == null ? 'var(--cm-ink-300)' : s >= 60 ? 'var(--stock-down)' : s >= 40 ? 'var(--cm-warn-500)' : 'var(--stock-up)';
      var barW = s != null ? Math.max(0, Math.min(100, Number(s))) : 0;
      html += '<div style="background:white;border-radius:4px;padding:8px;border:1px solid var(--cm-ink-50)">' +
        '<div style="font-size:11px;color:var(--cm-ink-500);margin-bottom:2px">' + d.label + '</div>' +
        '<div style="font-size:18px;font-weight:600;color:' + color + '">' + display + '</div>' +
        '<div style="background:var(--cm-ink-100);height:3px;border-radius:2px;margin:4px 0;overflow:hidden">' +
        '<div style="background:' + color + ';height:100%;border-radius:2px;width:' + barW + '%"></div></div>' +
        '<div style="font-size:10px;color:var(--cm-ink-700);line-height:1.35">' + esc(detail(d.key)) + '</div>' +
        '</div>';
    });
    html += '</div></div>';
    return html;
  }

  function toggleStockDetail(stockCode, clickedEl) {
    var existing = document.querySelector('.detail-panel[data-detail-stock="' + stockCode + '"]');
    if (existing) { removeDetailPanel(existing.closest('tr.detail-row') || existing); clickedEl.removeAttribute('data-detail-open'); return; }
    // Close any other open stock detail
    document.querySelectorAll('.detail-panel[data-detail-stock]').forEach(function (p) { removeDetailPanel(p.closest('tr.detail-row') || p); });
    document.querySelectorAll('[data-detail-open="stock"]').forEach(function (e) { e.removeAttribute('data-detail-open'); });
    clickedEl.setAttribute('data-detail-open', 'stock');

    var tr = clickedEl.closest('tr');
    var panel = document.createElement('div');
    panel.className = 'detail-panel';
    panel.setAttribute('data-detail-stock', stockCode);
    panel.innerHTML = '<div class="detail-loading">加载中...</div>';

    if (tr) {
      var colCount = tr.children.length;
      var detailTr = document.createElement('tr');
      detailTr.className = 'detail-row';
      var td = document.createElement('td');
      td.setAttribute('colspan', colCount);
      td.appendChild(panel);
      detailTr.appendChild(td);
      tr.parentNode.insertBefore(detailTr, tr.nextSibling);
    } else {
      clickedEl.parentNode.insertBefore(panel, clickedEl.nextSibling);
    }

    Promise.all([
      api('/api/inst/stocks/detail/' + encodeURIComponent(stockCode)),
      api('/api/inst/stocks/multidim/' + encodeURIComponent(stockCode)).catch(function () { return null; }),
    ]).then(function (results) {
      var r = results[0], md = results[1];
      if (!r || !r.ok) { panel.innerHTML = '<div class="detail-loading">加载失败: ' + esc(r && r.detail || '未知') + '</div>'; return; }
      try {
      var insts = r.institutions || [];
      var indStr = r.industry ? (r.industry.tdx_l1 || '') + (r.industry.tdx_l2 ? ' > ' + r.industry.tdx_l2 : '') + (r.industry.tdx_l2 ? ' > ' + (r.industry.tdx_l3 || 'L3未分类') : '') : '';
      var html = '';
      // 兼容原代码 bug：原代码引用了未定义的 detailPayload；此处把 r 作为 payload 兜底
      var detailPayload = r;
      html += renderStockReportHero(detailPayload);
      // §29.4 W2 五维画像评分（在 hero 之后立即展示，视觉突出）
      html += renderMultidimScoreCard(md && md.multidim_score);
      html += renderStockInstitutionCoverageSection(detailPayload, insts);
      html += renderStockEvidenceTimeline(detailPayload);
      html += renderSetupBlock(r.setup, insts, detailPayload);
      html += renderStockDetailCardGrid(detailPayload);
      panel.innerHTML = html;
      } catch (e) { panel.innerHTML = '<div class="detail-loading">渲染出错: ' + esc(String(e)) + '</div>'; }
    }).catch(function (e) { panel.innerHTML = '<div class="detail-loading">请求出错: ' + esc(String(e)) + '</div>'; });
  }

  // ============================================================
  // 自定义模态框（替代 prompt/confirm/alert）
  // ============================================================
  var INST_TYPES = ['QFII', '社保', '保险', '国家队', '北向', '牛散', '基金', '券商'];

  function showModal(title, bodyHtml) {
    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      overlay.innerHTML = '<div class="modal-box"><div class="modal-title">' + title + '</div><div class="modal-body">' + bodyHtml + '</div><div class="modal-actions"><button class="modal-cancel">取消</button><button class="primary modal-ok">确定</button></div></div>';
      document.body.appendChild(overlay);
      overlay.querySelector('.modal-cancel').onclick = function () { document.body.removeChild(overlay); resolve(null); };
      overlay.querySelector('.modal-ok').onclick = function () { resolve(overlay); };
      overlay.addEventListener('click', function (e) { if (e.target === overlay) { document.body.removeChild(overlay); resolve(null); } });
      var firstInput = overlay.querySelector('input,select');
      if (firstInput) firstInput.focus();
    });
  }
  function closeModal(overlay) { if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay); }

  async function modalPrompt(title, defaultVal) {
    var ov = await showModal(title, '<input type="text" id="modalInput" value="' + (defaultVal || '') + '">');
    if (!ov) return null;
    var val = ov.querySelector('#modalInput').value;
    closeModal(ov);
    return val;
  }
  async function modalConfirm(msg) {
    var ov = await showModal('确认', '<p style="font-size:13px;color:var(--cm-ink-700)">' + msg + '</p>');
    if (!ov) return false;
    closeModal(ov);
    return true;
  }
  async function modalAlert(msg) {
    var ov = await showModal('提示', '<p style="font-size:13px;color:var(--cm-ink-700)">' + msg + '</p>');
    if (ov) closeModal(ov);
  }
  async function modalTypeSelect(title) {
    var opts = INST_TYPES.map(function (t) { return '<option value="' + t + '">' + t + '</option>'; }).join('');
    var ov = await showModal(title || '选择类型', '<select id="modalSelect">' + opts + '</select>');
    if (!ov) return null;
    var val = ov.querySelector('#modalSelect').value;
    closeModal(ov);
    return val;
  }
  async function modalFindReplace(title) {
    var body = '<label>查找（多个词用逗号或空格分隔，将逐个替换）</label><input type="text" id="modalFind" placeholder="例如：有限责任公司,股份有限公司,-自有资金">' +
      '<label style="margin-top:10px">替换为（留空则删除匹配的文字）</label><input type="text" id="modalReplace" placeholder="替换后的文字，留空=删除">';
    var ov = await showModal(title || '查找替换', body);
    if (!ov) return null;
    var findStr = ov.querySelector('#modalFind').value;
    var replace = ov.querySelector('#modalReplace').value;
    closeModal(ov);
    if (!findStr) return null;
    // 拆分多个查找词（逗号、顿号、空格）
    var finds = findStr.split(/[,，、\s]+/).filter(function (s) { return s.trim(); });
    if (!finds.length) return null;
    return { finds: finds, replace: replace };
  }

  function el(id) { return document.getElementById(id) }
  function esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML }
  function fmt(n) {
    if (n == null) return '-';
    var num = Number(n);
    if (!Number.isFinite(num)) return '-';   // NaN / Infinity 防御 (avoid 'NaN 条')
    return num.toLocaleString();
  }
  function fmtCurrency(n) {
    if (n == null) return '-';
    return '￥' + Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  function pct(v) { return v != null ? Number(v).toFixed(1) + '%' : '-' }
  function fmtDateTime(d) {
    if (!d) return '-';
    var dt = new Date(d);
    if (!Number.isNaN(dt.getTime())) return dt.toLocaleString('zh-CN', { hour12: false });
    return String(d).replace('T', ' ').slice(0, 19);
  }
  function premiumText(v) { if (v == null) return '-'; var n = Number(v); return (n > 0 ? '+' : '') + n.toFixed(1) + '%' }
  function fmtDate(d) { if (!d) return '-'; var s = String(d).replace(/[^0-9]/g, '').slice(0, 8); return s.length === 8 ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) : String(d) }
  function fmtGain(v) { if (v == null) return '-'; var n = Number(v); return '<span class="' + (n >= 0 ? 'gain-pos' : 'gain-neg') + '">' + (n >= 0 ? '+' : '') + n.toFixed(1) + '%</span>' }
  function compactNum(v) { if (v == null) return '-'; var n = Number(v); return n >= 1e8 ? (n / 1e8).toFixed(1) + '亿' : n >= 1e4 ? (n / 1e4).toFixed(0) + '万' : n.toFixed(0) }
  function metric(l, v) { return '<div class="metric"><div class="metric-value">' + v + '</div><div class="metric-label">' + l + '</div></div>' }
  function setupPriorityMeta(priority) {
    var p = Number(priority || 0);
    if (p === 1) return { label: 'A1', bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' };
    if (p === 2) return { label: 'A2', bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' };
    if (p === 3) return { label: 'A3', bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' };
    if (p === 4) return { label: 'A4', bg: 'var(--cm-ink-50)', fg: 'var(--cm-ink-700)' };
    if (p === 5) return { label: 'A5', bg: 'var(--cm-bg)', fg: 'var(--cm-ink-500)' };
    return { label: '-', bg: 'var(--cm-bg)', fg: 'var(--cm-ink-300)' };
  }
  function setupBadge(tag, priority, confidence) {
    if (!tag) return '<span class="muted">-</span>';
    var meta = setupPriorityMeta(priority);
    var conf = confidence ? '<span style="font-size:10px;color:var(--cm-ink-300);margin-left:4px">' + esc(confidence) + '</span>' : '';
    return '<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:' + meta.bg + ';color:' + meta.fg + ';font-size:11px;font-weight:700">Setup ' + meta.label + '</span>' + conf;
  }
  function setupSummaryCell(s) {
    if (!s || !s.setup_tag) return '<span class="muted">-</span>';
    var badge = setupBadge(s.setup_tag, s.setup_priority, s.setup_confidence);
    var sub = [];
    var scoreLine = [];
    if (s.priority_pool) scoreLine.push(s.priority_pool);
    if (s.composite_priority_score != null) scoreLine.push('综合 ' + scoreNum(s.composite_priority_score));
    else if (s.discovery_score != null) scoreLine.push('发现 ' + scoreNum(s.discovery_score));
    if (scoreLine.length) sub.push(scoreLine.join(' · '));
    if (s.company_quality_score != null || s.company_quality_score_source || s.quality_feature_snapshot_date) {
      sub.push(qualityScoreSourceMeta(s));
    }
    var name = stockSourceName(s);
    if (name && name !== '-') sub.push(name);
    if (s.score_highlights) sub.push(s.score_highlights);
    else if (s.setup_reason) sub.push(s.setup_reason);
    return badge + (sub.length ? '<div class="muted" style="font-size:10px;line-height:1.4;margin-top:4px">' + esc(sub.join(' · ')) + '</div>' : '');
  }
  function renderSetupBlock(setup, institutions, payload) {
    var scoreTarget = Object.assign({}, payload || {}, (payload && payload.stage) || {}, (payload && payload.forecast) || {}, setup || {});
    var insts = Array.isArray(institutions) ? institutions : [];
    var gate = stockGateInfo(scoreTarget);
    var source = stockSourceName(scoreTarget);
    var leader = scoreTarget.leader_inst ? shortInstName(scoreTarget.leader_inst) : (insts[0] && insts[0].inst_name ? insts[0].inst_name : '');
    var industry = preferredIndustryLabel(scoreTarget);
    var latestReport = scoreTarget.latest_report_date || '';
    var latestNotice = scoreTarget.latest_notice_date || '';
    var thesis = [];
    if (setup && setup.setup_tag) thesis.push((gate.key ? (gate.label + '，') : '') + stockSignalNarrative(scoreTarget));
    else thesis.push((gate.key ? (gate.label + '，') : '') + (scoreTarget.path_state || '等待新的机构动作'));
    if (scoreTarget.score_highlights) thesis.push('亮点：' + scoreTarget.score_highlights);
    if (scoreTarget.score_risks) thesis.push('风险：' + scoreTarget.score_risks);
    var summaryRows = [
      stockReportHeroMetric('当前结论', gate.label || '待验证', gate.reason || '列表区仅保留状态，详情页展开完整判断', gate.key === 'follow' ? 'good' : (gate.key === 'watch' ? 'accent' : 'neutral')),
      stockReportHeroMetric('核心信号', setup && setup.setup_tag ? stockSignalHeadline(scoreTarget) : '暂无当前核心信号', setup && setup.setup_confidence ? ('置信 ' + setup.setup_confidence) : (setup && setup.setup_reason ? setup.setup_reason : '等待下一次明确触发')),
      stockReportHeroMetric('来源机构', source || '-', leader ? ('领头 ' + leader) : (insts.length ? ('覆盖 ' + fmt(insts.length) + ' 家') : '暂无当前机构覆盖')),
      stockReportHeroMetric('行业定位', industry || '-', scoreTarget.stock_archetype || '未分类'),
      stockReportHeroMetric('研究池', scoreTarget.priority_pool || '未分池', scoreTarget.priority_pool_reason || scoreTarget.composite_cap_reason || '等待更多证据'),
      stockReportHeroMetric('外部关注', scoreTarget.external_attention_signal || (scoreTarget.external_attention_score != null ? '外部覆盖' : '中性'), [scoreTarget.external_attention_score != null ? ('确认 ' + scoreNum(scoreTarget.external_attention_score)) : '', scoreTarget.external_crowding_penalty != null ? ('折扣 ' + scoreNum(scoreTarget.external_crowding_penalty)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('海龟执行', scoreTarget.turtle_setup_state || '未覆盖', [scoreTarget.turtle_preferred_system ? turtleSystemLabel(scoreTarget.turtle_preferred_system) : '', scoreTarget.turtle_score_delta != null ? ('修正 ' + signedScore(scoreTarget.turtle_score_delta)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('最新报告', latestReport ? fmtDate(latestReport) : '-', latestReport ? daysFromDateDigits(latestReport) : '暂无'),
      stockReportHeroMetric('最新公告', latestNotice ? fmtDate(latestNotice) : '-', [latestNotice ? daysFromDateDigits(latestNotice) : '', latestReport && latestNotice ? ('披露滞后 ' + daysBetweenDates(latestReport, latestNotice)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('当前机构数', fmt(insts.length), insts.length ? ('可跟 ' + fmt(insts.filter(function (item) { return item.follow_gate === 'follow'; }).length) + ' 家') : '暂无持仓机构')
    ];
    var callouts = [
      scoreTarget.score_highlights ? { label: '加分主因', text: scoreTarget.score_highlights, tone: 'positive' } : null,
      scoreTarget.score_risks ? { label: '核心风险', text: scoreTarget.score_risks, tone: 'warning' } : null,
      renderFocusConstraintLine(scoreTarget) ? { label: '约束条件', text: renderFocusConstraintLine(scoreTarget), tone: 'neutral' } : null
    ].filter(Boolean);
    return renderStockReportSection(
      '投资摘要',
      '把列表里省掉的来源、约束和亮点收回到详情页，先看摘要，再看底稿。',
      '<div class="stock-report-thesis"><div class="stock-report-thesis-label">当前摘要</div><div class="stock-report-thesis-body">' + esc(thesis.join(' ')) + '</div></div>' +
      renderStockReportKeyTable(summaryRows, 2) +
      renderStockReportCallouts(callouts),
      'stock-report-section--summary'
    );
  }
  function followGateTag(gate, reason) {
    // 用于「机构-股票对」级 follow_gate（loose：基于 event_type + premium_pct 两输入）
    if (!gate) return '<span class="muted">-</span>';
    var color = gate === 'follow' ? 'var(--cm-ok-500)' : gate === 'watch' ? 'var(--stock-down)' : gate === 'observe' ? 'var(--cm-warn-500)' : 'var(--stock-up)';
    var label = gate === 'follow' ? '可跟' : gate === 'watch' ? '关注' : gate === 'observe' ? '观察' : gate === 'avoid' ? '回避' : gate;
    return '<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:' + color + '14;color:' + color + ';font-size:11px;font-weight:600" title="' + esc(reason || '') + '">' + label + '</span>';
  }
  // 单一真相源：股票级 gate 解析器
  // 只消费后端回传的 stock_gate / stock_gate_reason，前端不再重算业务门槛
  function stockGateInfo(s) {
    if (!s) return { key: null, label: '-', color: 'var(--cm-ink-300)', reason: '' };
    var gate = s.stock_gate || null;
    var reason = s.stock_gate_reason || '';
    var meta = {
      follow: { label: '可跟', color: 'var(--cm-ok-500)' },
      watch: { label: '关注', color: 'var(--stock-down)' },
      observe: { label: '观察', color: 'var(--cm-warn-500)' },
      avoid: { label: '回避', color: 'var(--stock-up)' }
    }[gate];
    if (!meta) return { key: null, label: '-', color: 'var(--cm-ink-300)', reason: reason };
    return { key: gate, label: meta.label, color: meta.color, reason: reason };
  }
  // 单一真相源：股票级 gate 渲染器（display 列与详情共用同一份样式）
  function stockGateTag(s) {
    var info = stockGateInfo(s);
    if (!info.key) return '<span class="muted" title="' + esc(info.reason) + '">-</span>';
    return '<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:' + info.color + '14;color:' + info.color + ';font-size:11px;font-weight:600" title="' + esc(info.reason) + '">' + info.label + '</span>';
  }
  // 单一真相源：来源机构简称（后端解析后的 display_inst_name 优先；前端不再 fallback 到 leader_inst 全名）
  function stockSourceName(s) {
    if (!s) return '-';
    return s.display_inst_name || s.setup_inst_name || '-';
  }
  function industryLevels(item) {
    if (!item) return [];
    return [
      item.industry_level1,
      item.industry_level2,
      item.industry_level3
    ].filter(Boolean);
  }
  function industryChain(item) {
    return industryLevels(item).join(' > ');
  }
  function preferredIndustryLabel(item) {
    if (!item) return '';
    return item.setup_industry_name || industryLevels(item).slice(-1)[0] || '';
  }
  function costMethodText(v) {
    if (!v) return '-';
    return String(v)
      .replace('holding_chain_weighted_avg', '持仓链加权均价')
      .replace('holding_chain_carry_forward', '沿用上次持仓成本')
      .replace('holding_chain_new_entry', '本轮新进成本')
      .replace('holding_chain_event_fallback', '回退到本次事件成本')
      .replace('daily_vwap_qfq_volume_hand_adjusted', '日线VWAP(手数修正)')
      .replace('daily_vwap_qfq', '日线VWAP')
      .replace('daily_close_mean_qfq', '日线均价')
      .replace('monthly_vwap_qfq', '月线VWAP')
      .replace('monthly_close_mean_qfq', '月线均价')
      .replace(/_/g, ' ');
  }
  function securityMarketPrefix(code) { if (!code) return ''; return code.startsWith('6') ? 'SH' : 'SZ'; }
  function xqLink(code) { if (!code) return ''; var p = securityMarketPrefix(code); return '<a class="xq-link" href="https://xueqiu.com/S/' + p + code + '" target="_blank">' + p + ':' + esc(code) + '</a>' }
  function xueqiuPillLink(code, label, stopPropagation) {
    if (!code) return '';
    var prefix = code.startsWith('1') || code.startsWith('0') || code.startsWith('3') ? 'SZ' : securityMarketPrefix(code);
    var text = label || code;
    var clickAttr = stopPropagation ? ' onclick="event.stopPropagation()"' : '';
    return '<a href="https://xueqiu.com/S/' + prefix + esc(code) + '" target="_blank" rel="noopener"' + clickAttr + ' style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;background:var(--cm-brand-50);color:var(--cm-brand-500);font-size:11px;font-weight:700;border:1px solid var(--cm-brand-100);text-decoration:none">' + esc(text) + '</a>';
  }
  function securityIdentityBlock(code, name, opts) {
    opts = opts || {};
    var wrapperClass = opts.wrapperClass || 'security-identity';
    var metaClass = opts.metaClass || '';
    var label = name || code || '-';
    var nameClasses = ['security-identity-name'];
    if (opts.nameClass) nameClasses.push(opts.nameClass);
    if (opts.clickAction) nameClasses.push('clickable-name');
    var nameHtml = opts.clickAction
      ? '<span class="' + nameClasses.join(' ') + '" onclick="' + opts.clickAction + '">' + esc(label) + '</span>'
      : '<span class="' + nameClasses.join(' ') + '">' + esc(label) + '</span>';
    var metaParts = [];
    if (opts.includeCodeTag && code) metaParts.push('<span class="security-code-tag">' + esc(opts.codeLabel || code) + '</span>');
    if (opts.includeMarketLink && code) metaParts.push(xqLink(code));
    if (opts.includeXueqiuPill && code) metaParts.push(xueqiuPillLink(code, opts.xueqiuLabel || '雪球', true));
    return '<div class="' + wrapperClass + '"><div class="security-identity-main">' + nameHtml + '</div>' +
      (metaParts.length ? '<div class="security-identity-meta' + (metaClass ? (' ' + metaClass) : '') + '">' + metaParts.join('') + '</div>' : '') +
      '</div>';
  }
  function recommendedStrategySummary(item) {
    if (!item) {
      return {
        label: '未定',
        tone: { bg: 'var(--cm-ink-50)', fg: 'var(--cm-ink-700)' },
        recommendedReturn: null,
        recommendedLabel: null,
        comparisonLabel: null,
        comparisonReturn: null,
        edge: null
      };
    }
    var strategyType = item.strategy_type || item.recommended_strategy_label || '买入持有';
    return {
      label: item.recommended_strategy_label || strategyType,
      tone: etfStrategyTone(strategyType),
      recommendedReturn: item.recommended_strategy_return_pct != null ? item.recommended_strategy_return_pct : (strategyType === '网格交易' ? item.backtest_return_pct : item.buy_hold_return_pct),
      recommendedLabel: item.recommended_strategy_label || (strategyType === '网格交易' ? '最优网格' : strategyType),
      comparisonLabel: item.comparison_strategy_label || (strategyType === '网格交易' ? '买入持有' : (item.backtest_return_pct != null ? '最优网格' : null)),
      comparisonReturn: item.comparison_strategy_return_pct != null ? item.comparison_strategy_return_pct : (strategyType === '网格交易' ? item.buy_hold_return_pct : item.backtest_return_pct),
      edge: item.strategy_edge_pct != null ? item.strategy_edge_pct : (item.backtest_excess_pct != null ? (strategyType === '网格交易' ? item.backtest_excess_pct : -item.backtest_excess_pct) : null)
    };
  }
  function stockCell(code, name) {
    return '<div class="stock-name-cell">' + securityIdentityBlock(code, name, {
      wrapperClass: 'security-identity security-identity--stock-cell',
      clickAction: 'App.toggleStockDetail(\'' + esc(code) + '\',this)',
      includeMarketLink: true
    }) + '</div>';
  }
  function shortInstName(v) {
    if (!v) return '';
    return String(v).replace(/^inst_/, '').replace(/_/g, ' ').trim();
  }
  function parseDateDigits(v) {
    var s = String(v || '').replace(/[^0-9]/g, '').slice(0, 8);
    if (s.length !== 8) return null;
    var d = new Date(s.substring(0, 4) + '-' + s.substring(4, 6) + '-' + s.substring(6, 8));
    return isNaN(d) ? null : d;
  }
  function normalizeTimelineDateText(v) {
    var s = String(v || '').replace(/[^0-9]/g, '').slice(0, 8);
    if (s.length !== 8) return '';
    return s.substring(0, 4) + '-' + s.substring(4, 6) + '-' + s.substring(6, 8);
  }
  function daysFromDateDigits(v) {
    var d = parseDateDigits(v);
    if (!d) return '-';
    return Math.floor((new Date() - d) / 86400000) + '天';
  }
  function daysAgoPill(v) {
    var d = parseDateDigits(v);
    if (!d) return '';
    var days = Math.floor((new Date() - d) / 86400000);
    var color = days <= 7 ? 'var(--cm-ok-500)' : days <= 30 ? 'var(--cm-warn-500)' : 'var(--cm-ink-300)';
    var bg = days <= 7 ? 'var(--cm-ok-100)' : days <= 30 ? 'var(--cm-warn-100)' : 'var(--cm-bg)';
    return '<span style="display:inline-flex;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:600;background:' + bg + ';color:' + color + ';border:1px solid ' + color + '22;margin-left:4px;white-space:nowrap">' + days + '天</span>';
  }
  function daysBetweenDates(v1, v2) {
    var d1 = parseDateDigits(v1), d2 = parseDateDigits(v2);
    if (!d1 || !d2) return '-';
    return Math.floor((d2 - d1) / 86400000) + '天';
  }
  function setupLevelText(level) {
    if (level === 'level3') return '细分行业';
    if (level === 'level2') return '二级行业';
    if (level === 'level1') return '一级行业';
    return '行业';
  }
  function setupEventText(eventType) {
    return {
      new_entry: '新进',
      increase: '增持',
      unchanged: '持仓不变',
      decrease: '减持'
    }[eventType] || '动作';
  }
  function setupTimelinessText(grade) {
    return {
      1: '时效佳',
      2: '披露较快',
      3: '披露适中',
      4: '披露偏慢',
      5: '披露过慢'
    }[Number(grade)] || '披露适中';
  }
  function stockSignalHeadline(s) {
    if (!s || !s.setup_tag) return '暂无当前核心信号';
    return setupLevelText(s.setup_level) + '高手' + setupEventText(s.setup_event_type);
  }
  function stockSignalNarrative(s) {
    if (!s || !s.setup_tag) return s && (s.path_state || s.price_trend) ? String(s.path_state || s.price_trend) : '等待新的机构动作';
    var parts = [];
    var name = stockSourceName(s);
    var industry = preferredIndustryLabel(s);
    if (name && name !== '-') parts.push(name);
    if (industry) parts.push(industry);
    if (s.report_recency_grade != null && Number(s.report_recency_grade) <= 2) parts.push(setupTimelinessText(s.report_recency_grade));
    if (s.premium_grade === 1) parts.push('低溢价');
    return parts.join(' · ') || (s.setup_reason || '-');
  }
  function scoreNum(v) {
    return v != null ? Number(v).toFixed(1) : '-';
  }
  function fmtScore(v, digits) {
    if (v == null) return '-';
    var n = Number(v);
    if (Number.isNaN(n)) return '-';
    return n.toFixed(digits == null ? 1 : digits);
  }
  function signedScore(v) {
    if (v == null) return '-';
    var n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(1);
  }
  function signedPct(v) {
    if (v == null) return '-';
    var n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
  }
  function signedCountText(v, suffix) {
    if (v == null) return '-';
    var n = Number(v);
    return (n >= 0 ? '+' : '') + fmt(Math.round(n)) + (suffix || '');
  }
  function renderFocusConstraintLine(s) {
    if (!s) return '';
    var parts = [];
    if (s.setup_execution_reason) parts.push(s.setup_execution_reason);
    else if (s.priority_pool_reason) parts.push(s.priority_pool_reason);
    if (s.turtle_setup_state) parts.push('海龟 ' + s.turtle_setup_state);
    if (s.turtle_score_delta != null && Number(s.turtle_score_delta) !== 0) parts.push('海龟修正 ' + signedScore(s.turtle_score_delta));
    if (s.composite_cap_reason) parts.push(s.composite_cap_reason);
    if (s.external_attention_signal) parts.push('外部信号 ' + s.external_attention_signal);
    else if (s.external_attention_score != null && Number(s.external_attention_score) >= 65) parts.push('外部确认 ' + scoreNum(s.external_attention_score));
    if (s.external_crowding_penalty != null && Number(s.external_crowding_penalty) >= 4) parts.push('热度折扣 ' + scoreNum(s.external_crowding_penalty));
    if (s.raw_composite_priority_score != null && s.composite_priority_score != null && Number(s.raw_composite_priority_score) !== Number(s.composite_priority_score)) {
      parts.push('综合裁决 ' + scoreNum(s.raw_composite_priority_score) + ' -> ' + scoreNum(s.composite_priority_score));
    }
    return parts.join(' · ');
  }
  function renderStockReportSection(title, subtitle, body, extraClass) {
    if (!body) return '';
    return '<section class="stock-report-section' + (extraClass ? (' ' + extraClass) : '') + '">' +
      '<div class="stock-report-section-head"><div class="stock-report-section-title">' + esc(title || '-') + '</div>' + (subtitle ? '<div class="stock-report-section-sub">' + esc(subtitle) + '</div>' : '') + '</div>' +
      body +
      '</section>';
  }
  function renderStockReportKeyTable(items, pairsPerRow) {
    var rows = (items || []).filter(Boolean);
    if (!rows.length) return '';
    var pairCount = Math.max(1, pairsPerRow || 2);
    var html = '<table class="stock-report-key-table"><tbody>';
    for (var i = 0; i < rows.length; i += pairCount) {
      var chunk = rows.slice(i, i + pairCount);
      html += '<tr>';
      chunk.forEach(function (item) {
        var valueHtml = item.valueHtml != null ? item.valueHtml : esc(item.value == null ? '-' : String(item.value));
        var noteHtml = item.noteHtml != null ? item.noteHtml : (item.note ? esc(String(item.note)) : '');
        html += '<th>' + esc(item.label || '-') + '</th>' +
          '<td class="stock-report-key-cell' + (item.tone ? (' stock-report-key-cell--' + item.tone) : '') + '">' +
          '<div class="stock-report-key-value">' + valueHtml + '</div>' +
          (noteHtml ? '<div class="stock-report-key-note">' + noteHtml + '</div>' : '') +
          '</td>';
      });
      for (var j = chunk.length; j < pairCount; j += 1) {
        html += '<th class="stock-report-key-spacer"></th><td class="stock-report-key-spacer"></td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
  }
  function renderStockReportSubtable(title, rows, pairsPerRow, note, extraClass) {
    if (!rows || !rows.filter(Boolean).length) return '';
    return '<div class="stock-report-subtable' + (extraClass ? (' ' + extraClass) : '') + '">' +
      '<div class="stock-report-subtable-head"><div class="stock-report-subtable-title">' + esc(title || '-') + '</div>' + (note ? '<div class="stock-report-subtable-note">' + esc(note) + '</div>' : '') + '</div>' +
      renderStockReportKeyTable(rows, pairsPerRow) +
      '</div>';
  }
  function renderStockReportModule(title, note, body, extraClass) {
    if (!body) return '';
    return '<div class="stock-report-subtable stock-report-module' + (extraClass ? (' ' + extraClass) : '') + '">' +
      '<div class="stock-report-subtable-head"><div class="stock-report-subtable-title">' + esc(title || '-') + '</div>' + (note ? '<div class="stock-report-subtable-note">' + esc(note) + '</div>' : '') + '</div>' +
      '<div class="stock-report-module-body">' + body + '</div>' +
      '</div>';
  }
  function renderStockReportMatrixTable(rows) {
    var items = (rows || []).filter(Boolean);
    if (!items.length) return '';
    return '<table class="data-table data-table-compact stock-report-matrix-table"><thead><tr><th>维度</th><th>分数</th><th>当前状态</th><th>关键信号</th><th>风险 / 约束</th></tr></thead><tbody>' +
      items.map(function (item) {
        var statusHtml = item.statusHtml != null ? item.statusHtml : esc(item.status == null ? '-' : String(item.status));
        return '<tr>' +
          '<td class="stock-report-matrix-dim">' + esc(item.dim || '-') + '</td>' +
          '<td>' + esc(item.score == null ? '-' : String(item.score)) + '</td>' +
          '<td>' + statusHtml + '</td>' +
          '<td>' + esc(item.signal || '-') + '</td>' +
          '<td>' + esc(item.risk || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }
  function renderStockReportCallouts(items) {
    var rows = (items || []).filter(Boolean);
    if (!rows.length) return '';
    return '<div class="stock-report-callout-row">' + rows.map(function (item) {
      return '<div class="stock-report-callout stock-report-callout--' + esc(item.tone || 'neutral') + '">' +
        '<div class="stock-report-callout-label">' + esc(item.label || '-') + '</div>' +
        '<div class="stock-report-callout-text">' + esc(item.text || '-') + '</div>' +
        '</div>';
    }).join('') + '</div>';
  }
    function renderStockInstitutionCoverageSection(base, institutions) {
      var rows = Array.isArray(institutions) ? institutions : [];
      var followCount = rows.filter(function (item) { return item.follow_gate === 'follow'; }).length;
      var watchCount = rows.filter(function (item) { return item.follow_gate === 'watch'; }).length;
      var observeCount = rows.filter(function (item) { return item.follow_gate === 'observe'; }).length;
      var avoidCount = rows.filter(function (item) { return item.follow_gate === 'avoid'; }).length;
      var summaryRows = [
        stockReportHeroMetric('覆盖机构', fmt(rows.length), followCount ? ('可跟 ' + fmt(followCount) + ' 家') : '暂无可跟席位'),
        stockReportHeroMetric('执行分层', [followCount ? ('可跟 ' + fmt(followCount)) : '', watchCount ? ('关注 ' + fmt(watchCount)) : '', observeCount ? ('观察 ' + fmt(observeCount)) : '', avoidCount ? ('回避 ' + fmt(avoidCount)) : ''].filter(Boolean).join(' · ') || '-'),
        stockReportHeroMetric('最新报告', base.latest_report_date ? fmtDate(base.latest_report_date) : '-', base.latest_report_date ? daysFromDateDigits(base.latest_report_date) : '暂无'),
        stockReportHeroMetric('最新公告', base.latest_notice_date ? fmtDate(base.latest_notice_date) : '-', base.latest_notice_date ? daysFromDateDigits(base.latest_notice_date) : '暂无'),
        stockReportHeroMetric('最新收盘', base.price_timeline && base.price_timeline.end_close != null ? Number(base.price_timeline.end_close).toFixed(2) : '-', base.latest_close_date ? fmtDate(base.latest_close_date) : '待更新'),
        stockReportHeroMetric('覆盖结构', rows.length ? ('前3家 ' + rows.slice(0, 3).map(function (item) { return item.inst_name; }).filter(Boolean).join(' / ')) : '-', rows.length ? '按当前仍在持仓的跟踪机构排序' : '暂无机构覆盖')
      ];
      var tableRows = rows.map(function (inst) {
        var instMeta = [inst.holder_rank != null ? ('席位 #' + fmt(inst.holder_rank)) : '', inst.current_held_days != null ? (fmt(Math.round(inst.current_held_days)) + '天') : ''].filter(Boolean).join(' · ');
        var costText = inst.inst_ref_cost != null ? Number(inst.inst_ref_cost).toFixed(2) : '-';
        var costNote = inst.inst_cost_method ? costMethodText(inst.inst_cost_method) : '';
        return '<tr>' +
          '<td><div class="stock-source-cell"><div class="stock-source-main">' + instLink(inst.institution_id, inst.inst_name || '-', inst.inst_type) + '</div>' + (instMeta ? '<div class="stock-source-sub">' + esc(instMeta) + '</div>' : '') + '</div></td>' +
          '<td>' + (inst.event_type ? evTag(inst.event_type) : '<span class="muted">-</span>') + '</td>' +
          '<td>' + followGateTag(inst.follow_gate, inst.follow_gate_reason) + '</td>' +
          '<td>' + fmtDate(inst.report_date) + '</td>' +
          '<td>' + fmtDate(inst.notice_date) + '</td>' +
          '<td>' + fmtGain(inst.report_return_to_now) + '</td>' +
          '<td>' + fmtGain(inst.notice_return_to_now) + '</td>' +
          '<td>' + premiumText(inst.premium_pct) + '</td>' +
          '<td>' + pct(inst.hold_ratio) + '</td>' +
          '<td><div>' + esc(costText) + '</div>' + (costNote ? '<div class="muted" style="font-size:10px;margin-top:3px">' + esc(costNote) + '</div>' : '') + '</td>' +
          '<td>' + compactNum(inst.hold_market_cap) + '</td>' +
          '</tr>';
      }).join('') || '<tr><td colspan="11" class="muted">暂无机构覆盖</td></tr>';
      var tableHtml = '<div class="stock-report-table-wrap"><table class="data-table data-table-compact stock-report-inst-table"><thead><tr><th>机构</th><th>动作</th><th>执行</th><th>报告期</th><th>公告日</th><th>报告后</th><th>公告后</th><th>溢价</th><th>持股比</th><th>参考成本</th><th>持仓市值</th></tr></thead><tbody>' + tableRows + '</tbody></table></div>';
      return renderStockReportSection(
        '机构覆盖明细',
        '把谁在里面、谁可跟、谁在减持放到最前面，先看席位结构，再看轨迹和判断。',
        renderStockReportKeyTable(summaryRows, 3) + renderStockReportModule('当前机构覆盖表', '报告后 / 公告后收益统一对齐到当前最新收盘。', tableHtml, 'stock-report-module--table'),
        'stock-report-section--coverage'
      );
    }
    function stockHouseholdCountText(value) {
      if (value == null) return '-';
      var n = Number(value);
      if (Math.abs(n) >= 10000) return (n / 10000).toFixed(1) + '万户';
      return fmt(Math.round(n)) + '户';
    }
    function stockInstitutionCountText(value) {
      if (value == null) return '-';
      return fmt(Math.round(Number(value))) + '家';
    }
    function stockMoneyText(value) {
      if (value == null) return '-';
      return compactNum(Number(value));
    }
    function renderStockTrajectoryOverlayModule(overlay) {
      var series = overlay && Array.isArray(overlay.series) ? overlay.series.slice().reverse() : [];
      if (!series.length) return '';
      var headerNote = [
        overlay.quarters_loaded != null ? ('已载入 ' + fmt(overlay.quarters_loaded) + ' 季') : '',
        overlay.latest_complete_report_date ? ('完整季度 ' + fmtDate(overlay.latest_complete_report_date)) : '',
        overlay.latest_available_report_date && overlay.latest_available_report_date !== overlay.latest_complete_report_date ? ('最新落库 ' + fmtDate(overlay.latest_available_report_date)) : ''
      ].filter(Boolean).join(' · ');
      var rows = series.slice(0, 8).map(function (item) {
        var deltaParts = [];
        if (item.holder_count_delta_pct != null) deltaParts.push('股东 ' + signedPct(item.holder_count_delta_pct));
        if (item.inst_total_count_delta != null && Number(item.inst_total_count_delta) !== 0) deltaParts.push('机构 ' + signedCountText(item.inst_total_count_delta, '家'));
        if (item.fund_count_delta != null && Number(item.fund_count_delta) !== 0) deltaParts.push('基金 ' + signedCountText(item.fund_count_delta, '家'));
        if (item.national_team_shares_wan_delta != null && Number(item.national_team_shares_wan_delta) !== 0) deltaParts.push('国家队 ' + signedCountText(item.national_team_shares_wan_delta, '万股'));
        return '<tr>' +
          '<td>' + esc(fmtDate(item.report_date)) + '</td>' +
          '<td>' + esc(stockHouseholdCountText(item.holder_count)) + '</td>' +
          '<td>' + esc(stockInstitutionCountText(item.inst_total_count)) + '</td>' +
          '<td>' + esc(stockInstitutionCountText(item.fund_count)) + '</td>' +
          '<td>' + esc(stockInstitutionCountText(item.insurance_count)) + ' / ' + esc(stockInstitutionCountText(item.qfii_count)) + '</td>' +
          '<td>' + esc(item.national_team_shares_wan != null ? (Number(item.national_team_shares_wan) >= 10000 ? (Number(item.national_team_shares_wan) / 10000).toFixed(2) + '亿股' : fmt(Math.round(Number(item.national_team_shares_wan))) + '万股') : '-') + '</td>' +
          '<td>' + esc(deltaParts.join(' · ') || '首个落库季度') + '</td>' +
          '</tr>';
      }).join('');
      var body = '<div class="stock-report-inline-note">' + esc(overlay.capability_note || 'TDXHub 季度结构已纳入时间轴。') + '</div>' +
        '<div class="stock-report-inline-note stock-report-inline-note--muted">上图信号区已把股东人数与机构总量入图，这里保留原始季度值与环比变化，便于复核。</div>' +
        '<div class="stock-report-table-wrap"><table class="data-table data-table-compact stock-report-trajectory-table"><thead><tr><th>季度</th><th>股东人数</th><th>机构总量</th><th>基金</th><th>保险 / QFII</th><th>国家队</th><th>环比变化</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
      return renderStockReportModule('TDXHub 季度结构明细', headerNote, body, 'stock-report-module--table');
    }
    function renderStockMarketSignalModule(marginOverlay, changeSummary) {
      var margin = marginOverlay || {};
      var summary = changeSummary || {};
      var hasMargin = Array.isArray(margin.points) && margin.points.length;
      var hasChange = summary.event_count != null || summary.increase_count != null || summary.decrease_count != null || summary.latest_notice_date;
      if (!hasMargin && !hasChange) return '';
      var note = [
        hasMargin && margin.latest_trade_date ? ('两融 ' + fmtDate(margin.latest_trade_date)) : '',
        hasChange && summary.latest_notice_date ? ('最新公告 ' + fmtDate(summary.latest_notice_date)) : '',
        summary.window_days ? ('窗口 ' + fmt(summary.window_days) + ' 天') : ''
      ].filter(Boolean).join(' · ');
      var rows = [
        {
          label: '融资余额',
          value: hasMargin && margin.latest_fin_balance != null ? stockMoneyText(margin.latest_fin_balance) : '-',
          delta: hasMargin ? [
            margin.fin_balance_change_20d_pct != null ? ('20日 ' + signedPct(margin.fin_balance_change_20d_pct)) : '',
            margin.fin_balance_change_60d_pct != null ? ('60日 ' + signedPct(margin.fin_balance_change_60d_pct)) : ''
          ].filter(Boolean).join(' · ') || '-' : '-',
          note: hasMargin && margin.latest_fin_balance_ratio != null ? ('占比 ' + Number(margin.latest_fin_balance_ratio).toFixed(2) + '%') : '东财日频两融'
        },
        {
          label: '融券余额',
          value: hasMargin && margin.latest_loan_balance != null ? stockMoneyText(margin.latest_loan_balance) : '-',
          delta: hasMargin ? [
            margin.loan_balance_change_20d_pct != null ? ('20日 ' + signedPct(margin.loan_balance_change_20d_pct)) : '',
            margin.loan_balance_change_60d_pct != null ? ('60日 ' + signedPct(margin.loan_balance_change_60d_pct)) : ''
          ].filter(Boolean).join(' · ') || '-' : '-',
          note: hasMargin && margin.latest_loan_balance_ratio != null ? ('占比 ' + Number(margin.latest_loan_balance_ratio).toFixed(3) + '%') : '东财日频两融'
        },
        {
          label: '高管/股东增减持',
          value: hasChange ? ('增持 ' + fmt(summary.increase_count || 0) + ' / 减持 ' + fmt(summary.decrease_count || 0)) : '-',
          delta: hasChange && summary.net_event_count != null ? ('净方向 ' + signedCountText(summary.net_event_count, '次')) : '-',
          note: hasChange ? (summary.latest_notice_date ? ('最新公告 ' + fmtDate(summary.latest_notice_date)) : '最近180天事件汇总') : '-'
        }
      ].filter(function (item) {
        return item.value !== '-' || item.delta !== '-' || item.note !== '-';
      });
      if (!rows.length) return '';
      var tableHtml = '<div class="stock-report-inline-note">融资/融券在图上按日频曲线展示，增减持在图上按事件带展示；这里保留最新原始口径。</div>' +
        '<div class="stock-report-table-wrap"><table class="data-table data-table-compact stock-report-trajectory-table"><thead><tr><th>信号</th><th>当前值</th><th>变化</th><th>说明</th></tr></thead><tbody>' + rows.map(function (item) {
          return '<tr>' +
            '<td>' + esc(item.label) + '</td>' +
            '<td>' + esc(item.value) + '</td>' +
            '<td>' + esc(item.delta) + '</td>' +
            '<td>' + esc(item.note) + '</td>' +
            '</tr>';
        }).join('') + '</tbody></table></div>';
      return renderStockReportModule('两融与增减持摘要', note, tableHtml, 'stock-report-module--table');
    }
    function renderStockTdxBlockModule(blockPayload) {
      var payload = blockPayload || {};
      var categories = Array.isArray(payload.categories) ? payload.categories.filter(function (item) {
        return item && Array.isArray(item.blocks) && item.blocks.length;
      }) : [];
      if (!categories.length) return '';
      var note = [
        payload.total_blocks != null ? ('归属 ' + fmt(payload.total_blocks) + ' 个板块') : '',
        payload.updated_at ? ('同步 ' + fmtDateTime(payload.updated_at)) : '',
        payload.source ? ('来源 ' + payload.source) : ''
      ].filter(Boolean).join(' · ');
      var body = '<div class="stock-report-inline-note">把 sync_industry 落下来的 TDX 概念 / 风格 / 指数归属挂回详情页，成员数越小通常代表篮子更窄。</div>' + categories.map(function (category) {
        var blocks = (category.blocks || []).slice(0, 8);
        var hiddenCount = Math.max(0, Number(category.count || blocks.length) - blocks.length);
        return '<div class="stock-report-inline-note"><strong>' + esc(category.label || category.category || '-') + '</strong>' +
          (category.count != null ? ' · ' + esc(fmt(category.count)) + ' 个' : '') +
          '</div>' +
          '<div class="stock-price-summary-row">' + blocks.map(function (block) {
            var title = block.member_count != null ? ('成员 ' + fmt(block.member_count)) : '成员数未知';
            var label = block.member_count != null ? ((block.name || '-') + ' · ' + fmt(block.member_count)) : (block.name || '-');
            return '<span class="stock-price-summary-pill" title="' + esc(title) + '">' + esc(label) + '</span>';
          }).join('') +
          (hiddenCount > 0 ? '<span class="stock-price-summary-pill">+' + esc(fmt(hiddenCount)) + ' 个更多</span>' : '') +
          '</div>';
      }).join('');
      return renderStockReportModule('TDX 板块归属', note, body, 'stock-report-module--table');
    }
  function attentionSignalMeta(signal) {
    return {
      '外部确认增强': { label: '外部确认增强', tone: 'good' },
      '关注度抬升': { label: '关注度抬升', tone: 'accent' },
      '调研活跃': { label: '调研活跃', tone: 'info' },
      '热度拥挤': { label: '热度拥挤', tone: 'warn' }
    }[signal || ''] || null;
  }
  function attentionSignalTag(signal) {
    var meta = attentionSignalMeta(signal);
    if (!meta) return '';
    return '<span class="stock-attention-pill stock-attention-pill--' + meta.tone + '">' + esc(meta.label) + '</span>';
  }
  function stockReportHeroMetric(label, value, subtext, tone, valueHtml, noteHtml) {
    return {
      label: label,
      value: value,
      note: subtext,
      tone: tone || '',
      valueHtml: valueHtml,
      noteHtml: noteHtml
    };
  }
  function renderStockReportHero(base, attention) {
    if (!base) return '';
    var insts = Array.isArray(base.institutions) ? base.institutions : [];
    var timeline = base.price_timeline || {};
    var latestReport = base.latest_report_date || (base.setup && base.setup.latest_report_date) || (insts[0] && insts[0].report_date);
    var latestNotice = base.latest_notice_date || (base.setup && base.setup.latest_notice_date) || '';
    var followCount = insts.filter(function (item) { return item.follow_gate === 'follow'; }).length;
    var industry = '';
    if (base.industry) {
      industry = industryChain(base.industry);
    }
    if (!industry) industry = preferredIndustryLabel(base);
    var chips = [priorityPoolTag(base.priority_pool)];
    if (stockGateInfo(base).key) chips.push(stockGateTag(base));
    if (base.external_attention_signal) chips.push(attentionSignalTag(base.external_attention_signal));
    if (base.turtle_setup_state) chips.push(turtleStateTag(base.turtle_setup_state, true));
    if (base.stock_archetype) chips.push('<span class="stock-attention-pill stock-attention-pill--neutral">' + esc(base.stock_archetype) + '</span>');
    var summaryParts = [];
    if (industry) summaryParts.push(industry);
    var narrative = stockSignalNarrative(base);
    if (narrative && narrative !== '-') summaryParts.push(narrative);
    if (base.score_risks) summaryParts.push('风险：' + base.score_risks);
    var heroRows = [
      stockReportHeroMetric('综合优先', scoreNum(base.composite_priority_score), base.priority_pool || '未分池', Number(base.composite_priority_score || 0) >= 75 ? 'good' : ''),
      stockReportHeroMetric('最新价', timeline.end_close != null ? Number(timeline.end_close).toFixed(2) : '-', timeline.end_date ? fmtDate(timeline.end_date) : '待K线'),
      stockReportHeroMetric('三年涨跌', timeline.change_pct != null ? signedPct(timeline.change_pct) : '-', timeline.start_date && timeline.end_date ? (fmtDate(timeline.start_date) + ' -> ' + fmtDate(timeline.end_date)) : '近三年价格', timeline.change_pct != null && Number(timeline.change_pct) >= 0 ? 'good' : ''),
      stockReportHeroMetric('最新报告', latestReport ? fmtDate(latestReport) : '-', latestReport ? daysFromDateDigits(latestReport) : '暂无'),
      stockReportHeroMetric('公告时点', latestNotice ? fmtDate(latestNotice) : '-', latestNotice ? daysFromDateDigits(latestNotice) : '暂无'),
      stockReportHeroMetric('当前机构', fmt(insts.length), followCount ? ('可跟 ' + followCount + ' 家') : '待执行分层')
    ];
    return '<div class="stock-report-hero">' +
      '<div class="stock-report-hero-head">' +
      '<div>' +
      '<div class="stock-report-hero-title">' + securityIdentityBlock(base.stock_code, base.stock_name, { wrapperClass: 'security-identity security-identity--hero', nameClass: 'stock-report-hero-name', includeMarketLink: true, includeXueqiuPill: true }) + '</div>' +
      (summaryParts.length ? '<div class="stock-report-hero-sub">' + esc(summaryParts.join(' · ')) + '</div>' : '') +
      '<div class="stock-report-hero-chip-row">' + chips.join('') + '</div>' +
      '</div>' +
      '</div>' +
      '<div class="stock-report-hero-metrics">' + renderStockReportKeyTable(heroRows, 3) + '</div>' +
      '</div>';
  }
  function stockTrajectorySeriesMeta(key) {
    return {
      holder_count: { label: '股东人数', stroke: 'var(--cm-brand-700)', fill: 'rgba(15, 118, 110, 0.12)', text: 'var(--cm-brand-700)' },
      inst_total_count: { label: '机构总量', stroke: 'var(--cm-warn-500)', fill: 'rgba(194, 65, 12, 0.12)', text: 'var(--cm-warn-500)' },
      fin_balance: { label: '融资余额', stroke: 'var(--cm-accent-vivid)', fill: 'rgba(109, 40, 217, 0.12)', text: 'var(--cm-accent-vivid)' },
      loan_balance: { label: '融券余额', stroke: 'var(--cm-bad-500)', fill: 'rgba(220, 38, 38, 0.12)', text: 'var(--cm-bad-500)' }
    }[key] || { label: key || '信号', stroke: 'var(--cm-ink-500)', fill: 'rgba(100, 116, 139, 0.12)', text: 'var(--cm-ink-700)' };
  }
  function normalizeStockTimelineEventKey(event) {
    var lane = event && event.lane ? String(event.lane) : '';
    var tone = event && event.tone ? String(event.tone) : '';
    if (lane === 'tdx') return 'tdx';
    if (lane === 'change') {
      if (tone === 'increase') return 'increase';
      if (tone === 'decrease') return 'decrease';
      return 'change';
    }
    if (lane === 'survey') return 'survey';
    if (lane === 'research') return 'research';
    if (lane === 'news') return 'news';
    if (lane === 'signal') return 'signal';
    if (lane === 'capital') return 'capital';
    if (lane === 'report') return 'report';
    if (lane === 'notice') return 'notice';
    if (tone === 'increase') return 'increase';
    if (tone === 'decrease') return 'decrease';
    return lane || tone || 'notice';
  }
  function stockTimelineEventMeta(input) {
    var key = typeof input === 'string' ? input : normalizeStockTimelineEventKey(input);
    return {
      report: { label: '报告期', stroke: 'var(--cm-brand-500)', fill: 'var(--cm-brand-50)', text: 'var(--cm-brand-700)' },
      notice: { label: '公告披露', stroke: 'var(--cm-brand-500)', fill: 'var(--cm-brand-50)', text: 'var(--cm-brand-700)' },
      capital: { label: '资本事项', stroke: 'var(--cm-warn-500)', fill: 'var(--cm-warn-100)', text: 'var(--cm-warn-500)' },
      change: { label: '股东/高管', stroke: 'var(--cm-bad-500)', fill: 'var(--cm-bad-100)', text: 'var(--cm-bad-500)' },
      increase: { label: '增持', stroke: 'var(--cm-ok-500)', fill: 'var(--cm-ok-100)', text: 'var(--cm-ok-500)' },
      decrease: { label: '减持', stroke: 'var(--cm-bad-500)', fill: 'var(--cm-bad-100)', text: 'var(--cm-bad-500)' },
      signal: { label: '外部关注', stroke: 'var(--cm-accent-vivid)', fill: 'var(--cm-accent-warm-100)', text: 'var(--cm-accent-vivid)' },
      survey: { label: '机构调研', stroke: 'var(--cm-brand-700)', fill: 'var(--cm-brand-50)', text: 'var(--cm-brand-700)' },
      research: { label: '个股研报', stroke: 'var(--cm-ok-500)', fill: 'var(--cm-ok-100)', text: 'var(--cm-ok-500)' },
      news: { label: '新闻脉冲', stroke: 'var(--cm-warn-500)', fill: 'var(--cm-warn-100)', text: 'var(--cm-warn-500)' },
      tdx: { label: 'TDX季度', stroke: 'var(--cm-brand-700)', fill: 'var(--cm-brand-50)', text: 'var(--cm-brand-700)' }
    }[key] || { label: key || '事件', stroke: 'var(--cm-ink-300)', fill: 'var(--cm-bg)', text: 'var(--cm-ink-700)' };
  }
  function stockTimelineLaneKey(event) {
    var key = normalizeStockTimelineEventKey(event);
    if (key === 'increase' || key === 'decrease') return 'change';
    return key;
  }
  function stockTimelineLaneOrder() {
    return ['report', 'notice', 'capital', 'change', 'signal', 'survey', 'research', 'news'];
  }
  function stockTrajectorySeriesByKey(seriesList, key) {
    var items = Array.isArray(seriesList) ? seriesList : [];
    for (var i = 0; i < items.length; i += 1) {
      if (items[i] && items[i].key === key) return items[i];
    }
    return null;
  }
  function buildStockTrajectorySeries(base) {
    var signalSeries = [];
    var quarterlySeries = base && base.tdx_quarterly_overlay && Array.isArray(base.tdx_quarterly_overlay.series)
      ? base.tdx_quarterly_overlay.series
      : [];
    var marginPoints = base && base.margin_balance_overlay && Array.isArray(base.margin_balance_overlay.points)
      ? base.margin_balance_overlay.points
      : [];
    function appendSignalSeries(key, rows, valueKey, formatValue) {
      var points = (rows || []).map(function (item) {
        var dateText = normalizeTimelineDateText(item.date || item.report_date);
        var value = item && item[valueKey] != null ? Number(item[valueKey]) : null;
        if (!dateText || value == null || isNaN(value)) return null;
        return { date: dateText, value: value, text: formatValue(value) };
      }).filter(Boolean);
      if (!points.length) return;
      signalSeries.push({
        key: key,
        points: points,
        latestText: points[points.length - 1].text,
        latestDate: points[points.length - 1].date
      });
    }
    appendSignalSeries('holder_count', quarterlySeries, 'holder_count', stockHouseholdCountText);
    appendSignalSeries('inst_total_count', quarterlySeries, 'inst_total_count', stockInstitutionCountText);
    appendSignalSeries('fin_balance', marginPoints, 'fin_balance', stockMoneyText);
    appendSignalSeries('loan_balance', marginPoints, 'loan_balance', stockMoneyText);
    return signalSeries;
  }
  function collectStockTimelineEvents(base) {
    return Array.isArray(base && base.timeline_events) ? base.timeline_events.slice() : [];
  }
  function timelineEventSortValue(dateText) {
    var parsed = parseDateDigits(dateText);
    return parsed ? parsed.getTime() : 0;
  }
  function condenseStockTimelineEvents(events) {
    var grouped = {};
    (Array.isArray(events) ? events : []).forEach(function (item) {
      if (!item) return;
      var dateText = normalizeTimelineDateText(item.date);
      var typeKey = normalizeStockTimelineEventKey(item);
      if (!dateText || typeKey === 'tdx') return;
      var laneKey = stockTimelineLaneKey(item);
      var groupKey = [dateText, laneKey, typeKey].join('|');
      if (!grouped[groupKey]) {
        grouped[groupKey] = {
          date: dateText,
          lane: laneKey,
          tone: typeKey,
          title: item.title,
          body: item.body,
          baseBody: item.body,
          shortLabel: item.shortLabel || item.title,
          count: 1
        };
        return;
      }
      grouped[groupKey].count += 1;
      if (grouped[groupKey].title !== item.title) grouped[groupKey].title = stockTimelineEventMeta(typeKey).label;
      grouped[groupKey].body = `${grouped[groupKey].baseBody}；另有 ${grouped[groupKey].count - 1} 条同日事件`;
    });
    return Object.keys(grouped).map(function (key) {
      var item = grouped[key];
      return {
        date: item.date,
        lane: item.lane,
        tone: item.tone,
        title: item.title,
        body: item.body,
        shortLabel: item.shortLabel,
        count: item.count
      };
    }).sort(function (a, b) {
      return timelineEventSortValue(a.date) - timelineEventSortValue(b.date);
    });
  }
  function renderStockTrajectorySignalLegend(signalSeries) {
    var items = (Array.isArray(signalSeries) ? signalSeries : []).map(function (series) {
      var meta = stockTrajectorySeriesMeta(series.key);
      var latestDate = series.latestDate ? fmtDate(series.latestDate) : '';
      return `<span class='stock-price-legend-item stock-price-legend-item--curve'><span class='stock-price-legend-line' style='background:${meta.stroke}'></span><span class='stock-price-legend-label'>${esc(meta.label)}</span><span class='stock-price-legend-value'>${esc(series.latestText || '-')}</span>${latestDate ? `<span class='stock-price-legend-note'>${esc(latestDate)}</span>` : ''}</span>`;
    }).join('');
    return items ? `<div class='stock-price-legend stock-price-legend--curves'>${items}</div>` : '';
  }
  function renderStockTimelineEventLegend(events) {
    var counts = {};
    (Array.isArray(events) ? events : []).forEach(function (item) {
      var key = normalizeStockTimelineEventKey(item);
      if (key === 'tdx') return;
      counts[key] = (counts[key] || 0) + 1;
    });
    var ordered = ['report', 'notice', 'capital', 'increase', 'decrease', 'signal', 'survey', 'research', 'news'];
    var items = ordered.filter(function (key) {
      return counts[key];
    }).map(function (key) {
      var meta = stockTimelineEventMeta(key);
      return `<span class='stock-price-legend-item'><span class='stock-price-legend-dot' style='background:${meta.stroke}'></span><span class='stock-price-legend-label'>${esc(meta.label)}</span><span class='stock-price-legend-note'>${esc(fmt(counts[key]))}</span></span>`;
    }).join('');
    return items ? `<div class='stock-price-legend stock-price-legend--events'>${items}</div>` : '';
  }
  function renderStockTimelineEventDigest(events) {
    var rows = (Array.isArray(events) ? events : []).slice().sort(function (a, b) {
      return timelineEventSortValue(b.date) - timelineEventSortValue(a.date);
    }).slice(0, 12);
    if (!rows.length) return '';
    var body = `<div class='stock-report-table-wrap'><table class='data-table data-table-compact stock-price-event-table'><thead><tr><th>日期</th><th>类别</th><th>事项</th><th>明细</th></tr></thead><tbody>${rows.map(function (event) {
      var meta = stockTimelineEventMeta(event);
      var badge = `<span class='stock-price-event-badge' style='background:${meta.fill};color:${meta.text};border-color:${meta.stroke}'>${esc(meta.label)}</span>`;
      return `<tr><td>${esc(fmtDate(event.date))}</td><td>${badge}</td><td>${esc(event.title || '-')}</td><td>${esc(event.body || '-')}</td></tr>`;
    }).join('')}</tbody></table></div>`;
    return renderStockReportModule('事件明细', '与图上事件带一一对应，按时间倒序展示最近 12 条。', body, 'stock-report-module--table');
  }
  function buildStockTimelineSvg(points, signalSeries, events) {
    if (!Array.isArray(points) || !points.length) return `<div class='muted' style='padding:26px 0;text-align:center;font-size:12px'>暂无近三年价格数据。</div>`;
    var validPoints = points.filter(function (point) { return parseDateDigits(point.date); });
    if (!validPoints.length) return `<div class='muted' style='padding:26px 0;text-align:center;font-size:12px'>价格日期无效。</div>`;
    var width = 1160;
    var left = 58;
    var right = 28;
    var top = 22;
    var priceBottom = 178;
    var signalTop = 214;
    var signalBottom = 296;
    var eventTop = 332;
    var laneGap = 28;
    var minTs = parseDateDigits(validPoints[0].date).getTime();
    var maxTs = parseDateDigits(validPoints[validPoints.length - 1].date).getTime();
    if (minTs === maxTs) maxTs += 86400000;
    var closes = validPoints.map(function (point) { return Number(point.close || 0); });
    var minClose = Math.min.apply(null, closes);
    var maxClose = Math.max.apply(null, closes);
    if (minClose === maxClose) {
      minClose -= 1;
      maxClose += 1;
    }
    var padding = (maxClose - minClose) * 0.08;
    var low = minClose - padding;
    var high = maxClose + padding;
    function xFor(dateText) {
      var parsed = parseDateDigits(dateText);
      if (!parsed) return left;
      var ratio = (parsed.getTime() - minTs) / (maxTs - minTs);
      ratio = Math.max(0, Math.min(1, ratio));
      return left + ratio * (width - left - right);
    }
    function priceYFor(close) {
      return top + (1 - (Number(close || 0) - low) / (high - low)) * (priceBottom - top);
    }
    function signalYFor(ratio) {
      return signalBottom - ratio * (signalBottom - signalTop);
    }
    var condensedEvents = Array.isArray(events) ? events : [];
    var laneKeys = stockTimelineLaneOrder().filter(function (laneKey) {
      return condensedEvents.some(function (event) { return stockTimelineLaneKey(event) === laneKey; });
    });
    var laneYMap = {};
    laneKeys.forEach(function (laneKey, index) {
      laneYMap[laneKey] = eventTop + index * laneGap;
    });
    var axisLineY = laneKeys.length ? laneYMap[laneKeys[laneKeys.length - 1]] + 14 : signalBottom + 22;
    var axisLabelY = axisLineY + 18;
    var height = axisLabelY + 24;
    var pricePath = validPoints.map(function (point, index) {
      return `${index ? 'L' : 'M'} ${xFor(point.date).toFixed(1)} ${priceYFor(point.close).toFixed(1)}`;
    }).join(' ');
    var priceArea = `${pricePath} L ${xFor(validPoints[validPoints.length - 1].date).toFixed(1)} ${priceBottom} L ${xFor(validPoints[0].date).toFixed(1)} ${priceBottom} Z`;
    var priceGrid = [0, 0.25, 0.5, 0.75, 1].map(function (ratio) {
      var y = top + (priceBottom - top) * ratio;
      var value = (high - (high - low) * ratio).toFixed(2);
      return `<line x1='${left}' y1='${y.toFixed(1)}' x2='${width - right}' y2='${y.toFixed(1)}' stroke='var(--cm-ink-100)' stroke-dasharray='4 4'></line><text x='${left - 10}' y='${(y + 4).toFixed(1)}' text-anchor='end' fill='var(--cm-ink-300)' font-size='10'>${esc(value)}</text>`;
    }).join('');
    var tickIndexes = [0, 0.25, 0.5, 0.75, 1].map(function (ratio) {
      return Math.min(validPoints.length - 1, Math.round((validPoints.length - 1) * ratio));
    }).filter(function (value, index, arr) {
      return arr.indexOf(value) === index;
    });
    var ticks = tickIndexes.map(function (idx) {
      var point = validPoints[idx];
      var x = xFor(point.date);
      return `<line x1='${x.toFixed(1)}' y1='${axisLineY}' x2='${x.toFixed(1)}' y2='${axisLineY + 8}' stroke='var(--cm-ink-300)'></line><text x='${x.toFixed(1)}' y='${axisLabelY}' text-anchor='middle' fill='var(--cm-ink-300)' font-size='10'>${esc(fmtDate(point.date))}</text>`;
    }).join('');
    var normalizedSeries = (Array.isArray(signalSeries) ? signalSeries : []).map(function (series) {
      var cleanPoints = (series.points || []).filter(function (point) {
        return parseDateDigits(point.date) && point.value != null;
      });
      if (!cleanPoints.length) return null;
      var values = cleanPoints.map(function (point) { return Number(point.value); });
      var minValue = Math.min.apply(null, values);
      var maxValue = Math.max.apply(null, values);
      var span = maxValue - minValue;
      return {
        key: series.key,
        points: cleanPoints.map(function (point) {
          return {
            date: point.date,
            value: point.value,
            text: point.text,
            ratio: span <= 0 ? 0.5 : (Number(point.value) - minValue) / span
          };
        })
      };
    }).filter(Boolean);
    var signalGrid = [0, 0.5, 1].map(function (ratio) {
      var y = signalYFor(ratio);
      return `<line x1='${left}' y1='${y.toFixed(1)}' x2='${width - right}' y2='${y.toFixed(1)}' stroke='var(--cm-brand-100)'></line><text x='${width - right + 4}' y='${(y + 4).toFixed(1)}' fill='var(--cm-ink-300)' font-size='10'>${Math.round(ratio * 100)}</text>`;
    }).join('');
    var signalPaths = normalizedSeries.map(function (series) {
      var meta = stockTrajectorySeriesMeta(series.key);
      var path = series.points.map(function (point, index) {
        return `${index ? 'L' : 'M'} ${xFor(point.date).toFixed(1)} ${signalYFor(point.ratio).toFixed(1)}`;
      }).join(' ');
      var lastPoint = series.points[series.points.length - 1];
      var markers = series.points.length <= 16
        ? series.points.map(function (point) {
          return `<circle cx='${xFor(point.date).toFixed(1)}' cy='${signalYFor(point.ratio).toFixed(1)}' r='2.8' fill='${meta.stroke}'></circle>`;
        }).join('')
        : '';
      return `<path d='${path}' fill='none' stroke='${meta.stroke}' stroke-width='${series.key.indexOf('balance') >= 0 ? '1.9' : '2.2'}' stroke-linecap='round' stroke-linejoin='round'></path>${markers}<circle cx='${xFor(lastPoint.date).toFixed(1)}' cy='${signalYFor(lastPoint.ratio).toFixed(1)}' r='4.1' fill='var(--cm-surface)' stroke='${meta.stroke}' stroke-width='2'></circle>`;
    }).join('');
    var laneLabels = laneKeys.map(function (laneKey) {
      var meta = stockTimelineEventMeta(laneKey === 'change' ? 'change' : laneKey);
      return `<text x='12' y='${laneYMap[laneKey] + 4}' fill='${meta.text}' font-size='11' font-weight='700'>${esc(meta.label)}</text><line x1='${left}' y1='${laneYMap[laneKey]}' x2='${width - right}' y2='${laneYMap[laneKey]}' stroke='var(--cm-ink-100)'></line>`;
    }).join('');
    var eventMarks = condensedEvents.map(function (event) {
      var laneKey = stockTimelineLaneKey(event);
      var laneY = laneYMap[laneKey];
      if (laneY == null) return '';
      var meta = stockTimelineEventMeta(event);
      var x = xFor(event.date);
      var shadow = event.count > 1
        ? `<rect x='${(x - 5.5 + 2).toFixed(1)}' y='${(laneY - 5.5 - 2).toFixed(1)}' width='11' height='11' rx='4' fill='${meta.fill}' stroke='${meta.stroke}' stroke-width='1.2' opacity='0.42'></rect>`
        : '';
      var tooltip = [fmtDate(event.date), meta.label, event.title, event.body].filter(Boolean).join(' · ');
      return `<g><title>${esc(tooltip)}</title>${shadow}<rect x='${(x - 5.5).toFixed(1)}' y='${(laneY - 5.5).toFixed(1)}' width='11' height='11' rx='4' fill='${meta.fill}' stroke='${meta.stroke}' stroke-width='1.6'></rect></g>`;
    }).join('');
    var lastPricePoint = validPoints[validPoints.length - 1];
    return `<svg class='stock-price-chart' viewBox='0 0 ${width} ${height}' aria-label='三年价格与多信号时间线'><defs><linearGradient id='stockPriceAreaGradientV237' x1='0' x2='0' y1='0' y2='1'><stop offset='0%' stop-color='var(--cm-brand-400)' stop-opacity='0.30'></stop><stop offset='100%' stop-color='var(--cm-surface)' stop-opacity='0'></stop></linearGradient></defs><text x='12' y='18' fill='var(--cm-ink-700)' font-size='11' font-weight='700'>价格</text><text x='12' y='206' fill='var(--cm-ink-700)' font-size='11' font-weight='700'>结构 / 资金信号</text><text x='${width - right}' y='206' text-anchor='end' fill='var(--cm-ink-300)' font-size='10'>各曲线按近三年各自区间归一化</text>${priceGrid}<rect x='${left}' y='${signalTop}' width='${width - left - right}' height='${signalBottom - signalTop}' rx='14' fill='var(--cm-brand-50)' stroke='var(--cm-brand-100)'></rect>${signalGrid}<path d='${priceArea}' fill='url(#stockPriceAreaGradientV237)'></path><path d='${pricePath}' fill='none' stroke='var(--cm-brand-500)' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'></path><circle cx='${xFor(lastPricePoint.date).toFixed(1)}' cy='${priceYFor(lastPricePoint.close).toFixed(1)}' r='4.6' fill='var(--cm-surface)' stroke='var(--cm-brand-500)' stroke-width='2'></circle>${normalizedSeries.length ? signalPaths : `<text x='${left + 12}' y='${signalTop + 34}' fill='var(--cm-ink-300)' font-size='11'>暂无可叠加的结构 / 资金曲线</text>`}<line x1='${left}' y1='${axisLineY}' x2='${width - right}' y2='${axisLineY}' stroke='var(--cm-ink-300)'></line>${laneLabels}${eventMarks}${ticks}</svg>`;
  }
  function renderStockEvidenceTimeline(base) {
    var timeline = base.price_timeline || {};
    var points = timeline.points || [];
    var rawEvents = collectStockTimelineEvents(base);
    var condensedEvents = condenseStockTimelineEvents(rawEvents);
    var signalSeries = buildStockTrajectorySeries(base);
    var overlayModule = renderStockTrajectoryOverlayModule(base.tdx_quarterly_overlay || null);
    var marketSignalModule = renderStockMarketSignalModule(base.margin_balance_overlay || null, base.shareholder_change_summary || null);
    var blockModule = renderStockTdxBlockModule(base.tdx_blocks || null);
    var moduleGrid = [overlayModule, marketSignalModule, blockModule].filter(Boolean).join('');
    if (!points.length && !signalSeries.length && !condensedEvents.length && !moduleGrid) return '';
    var holderSeries = stockTrajectorySeriesByKey(signalSeries, 'holder_count');
    var instSeries = stockTrajectorySeriesByKey(signalSeries, 'inst_total_count');
    var finSeries = stockTrajectorySeriesByKey(signalSeries, 'fin_balance');
    var loanSeries = stockTrajectorySeriesByKey(signalSeries, 'loan_balance');
    var shareholderSummary = base.shareholder_change_summary || {};
    var summaryPills = [
      timeline.start_date && timeline.end_date ? `<span class='stock-price-summary-pill'>区间 ${esc(fmtDate(timeline.start_date) + ' - ' + fmtDate(timeline.end_date))}</span>` : '',
      timeline.change_pct != null ? `<span class='stock-price-summary-pill'>三年涨跌 ${esc(signedPct(timeline.change_pct))}</span>` : '',
      timeline.end_close != null ? `<span class='stock-price-summary-pill'>最新价 ${esc(Number(timeline.end_close).toFixed(2))}</span>` : '',
      holderSeries ? `<span class='stock-price-summary-pill'>股东 ${esc(holderSeries.latestText || '-')}</span>` : '',
      instSeries ? `<span class='stock-price-summary-pill'>机构 ${esc(instSeries.latestText || '-')}</span>` : '',
      finSeries ? `<span class='stock-price-summary-pill'>融资 ${esc(finSeries.latestText || '-')}</span>` : '',
      loanSeries ? `<span class='stock-price-summary-pill'>融券 ${esc(loanSeries.latestText || '-')}</span>` : '',
      shareholderSummary.event_count != null ? `<span class='stock-price-summary-pill'>180天增/减 ${esc(fmt(shareholderSummary.increase_count || 0) + '/' + fmt(shareholderSummary.decrease_count || 0))}</span>` : '',
      condensedEvents.length ? `<span class='stock-price-summary-pill'>事件带 ${esc(String(condensedEvents.length))} 条</span>` : ''
    ].filter(Boolean).join('');
    var chartNote = `<div class='stock-price-chart-note'>价格区使用前复权收盘；中段信号区把股东人数、机构总量、融资余额、融券余额按各自近三年区间归一化叠加；下方事件带与明细表按同一分类口径展示。</div>`;
    var signalLegend = renderStockTrajectorySignalLegend(signalSeries);
    var eventLegend = renderStockTimelineEventLegend(condensedEvents);
    var eventDigest = renderStockTimelineEventDigest(condensedEvents);
    return renderStockReportSection(
      '价格与事件轨迹',
      '把价格、结构资金曲线和事件带压到同一条横轴上，先看共振，再下钻到明细。',
      (summaryPills ? `<div class='stock-price-summary-row'>${summaryPills}</div>` : '') +
      chartNote +
      `<div class='stock-price-chart-wrap'>${buildStockTimelineSvg(points, signalSeries, condensedEvents)}</div>` +
      signalLegend +
      eventLegend +
      eventDigest +
      (moduleGrid ? `<div class='stock-report-trajectory-grid'>${moduleGrid}</div>` : ''),
      'stock-report-section--trajectory'
    );
  }
  function renderStockReportScoreSection(base) {
    var attention = base.attention || {};
    var snapshot = attention.snapshot || {};
    var research = attention.research || {};
    var news = attention.news || {};
    var survey30 = base.attention_survey_count_30d != null ? Number(base.attention_survey_count_30d) : Number(snapshot.survey_count_30d || 0);
    var survey90 = base.attention_survey_count_90d != null ? Number(base.attention_survey_count_90d) : Number(snapshot.survey_count_90d || 0);
    var qualitySourceLabel = base.company_quality_score_source === 'quality_feature_v1' ? '质量特征快照' : '评分引擎兜底';
    var qualitySourceHint = base.quality_snapshot_date ? ('快照 ' + fmtDate(base.quality_snapshot_date)) : (base.quality_latest_financial_report_date ? ('财报 ' + fmtDate(base.quality_latest_financial_report_date)) : '');
    var rows = [
      {
        dim: '发现',
        score: scoreNum(base.discovery_score),
        statusHtml: (base.setup_tag ? setupBadge(base.setup_tag, base.setup_priority, base.setup_confidence) + ' ' : '') + stockGateTag(base),
        signal: [stockSourceName(base), base.setup_event_type ? setupEventText(base.setup_event_type) : '', base.consensus_count != null ? ('共识 ' + fmt(base.consensus_count)) : ''].filter(Boolean).join(' · ') || '等待新的机构动作',
        risk: base.setup_execution_reason || base.priority_pool_reason || '等待下一次明确触发'
      },
      {
        dim: '质量',
        score: scoreNum(base.company_quality_score),
        status: base.stock_archetype || '待分类',
        signal: [base.roe != null ? ('ROE ' + pct(base.roe)) : '', base.gross_margin != null ? ('毛利 ' + pct(base.gross_margin)) : '', base.net_profit_positive_8q != null ? ('8期净利正 ' + fmt(base.net_profit_positive_8q) + '/8') : '', qualitySourceLabel].filter(Boolean).join(' · ') || '等待更多财务覆盖',
        risk: base.score_risks || qualitySourceHint || '财报覆盖仍需补厚'
      },
      {
        dim: '阶段',
        score: scoreNum(base.stage_score),
        status: base.path_state || '待判断',
        signal: [base.return_3m != null ? ('3月 ' + signedPct(base.return_3m)) : '', base.return_12m != null ? ('12月 ' + signedPct(base.return_12m)) : '', base.amount_ratio_20_120 != null ? ('量能 ' + scoreNum(base.amount_ratio_20_120)) : ''].filter(Boolean).join(' · ') || '等待路径确认',
        risk: base.stage_reason || base.composite_cap_reason || '观察阶段约束'
      },
      {
        dim: '外部',
        score: scoreNum(base.external_attention_score),
        statusHtml: attentionSignalTag(base.external_attention_signal) || '<span class="stock-attention-pill stock-attention-pill--neutral">中性</span>',
        signal: [survey30 ? ('30天调研 ' + fmt(survey30)) : '', survey90 ? ('90天调研 ' + fmt(survey90)) : '', Number(research.count_90d || 0) ? ('90天研报 ' + fmt(research.count_90d || 0)) : '', Number(news.count_30d || 0) ? ('30天新闻 ' + fmt(news.count_30d || 0)) : ''].filter(Boolean).join(' · ') || '外部关注仍偏安静',
        risk: base.external_crowding_penalty != null ? ('热度折扣 ' + scoreNum(base.external_crowding_penalty)) : '暂无拥挤惩罚'
      },
      {
        dim: '海龟',
        score: scoreNum(base.turtle_execution_score),
        statusHtml: base.turtle_setup_state ? turtleStateTag(base.turtle_setup_state, true) : '<span class="stock-attention-pill stock-attention-pill--neutral">未覆盖</span>',
        signal: [base.turtle_preferred_system ? turtleSystemLabel(base.turtle_preferred_system) : '', base.breakout_dist_20_pct != null ? ('距20日位 ' + signedPct(base.breakout_dist_20_pct)) : '', base.exit_dist_10_pct != null ? ('距10日位 ' + signedPct(base.exit_dist_10_pct)) : ''].filter(Boolean).join(' · ') || '等待突破位与退出位收敛',
        risk: base.turtle_reason || '执行层暂未给出额外说明'
      }
    ];
    return renderStockReportSection('评分与判断框架', '把发现、质量、阶段、外部和海龟放回同一张研究判断表。', renderStockReportMatrixTable(rows));
  }
  function renderStockReportDataSection(base) {
    var attention = base.attention || {};
    var snapshot = attention.snapshot || {};
    var research = attention.research || {};
    var news = attention.news || {};
    var industry = preferredIndustryLabel(base);
    var survey30 = base.attention_survey_count_30d != null ? Number(base.attention_survey_count_30d) : Number(snapshot.survey_count_30d || 0);
    var survey90 = base.attention_survey_count_90d != null ? Number(base.attention_survey_count_90d) : Number(snapshot.survey_count_90d || 0);
    var qualitySourceLabel = base.company_quality_score_source === 'quality_feature_v1' ? '质量特征快照' : '评分引擎兜底';
    var qualitySourceHint = [base.quality_snapshot_date ? ('快照 ' + fmtDate(base.quality_snapshot_date)) : '', base.quality_latest_financial_report_date ? ('财报 ' + fmtDate(base.quality_latest_financial_report_date)) : ''].filter(Boolean).join(' · ');
    var qualityRows = [
      stockReportHeroMetric('质量来源', qualitySourceLabel, qualitySourceHint),
      stockReportHeroMetric('股票类型', base.stock_archetype || '待分类', industry || ''),
      stockReportHeroMetric('财务口径', base.quality_latest_financial_report_date ? fmtDate(base.quality_latest_financial_report_date) : '-', [base.quality_latest_indicator_report_date ? ('指标 ' + fmtDate(base.quality_latest_indicator_report_date)) : '', base.quality_snapshot_date ? ('快照 ' + fmtDate(base.quality_snapshot_date)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('ROE', base.roe != null ? pct(base.roe) : '-', '', base.roe != null && Number(base.roe) >= 15 ? 'good' : ''),
      stockReportHeroMetric('ROA', base.roa_ak != null ? pct(base.roa_ak) : '-'),
      stockReportHeroMetric('毛利率', base.gross_margin != null ? pct(base.gross_margin) : '-'),
      stockReportHeroMetric('现利比', base.ocf_to_profit != null ? scoreNum(base.ocf_to_profit) : '-'),
      stockReportHeroMetric('负债率', base.debt_ratio != null ? pct(base.debt_ratio) : '-'),
      stockReportHeroMetric('流动比率', base.current_ratio != null ? scoreNum(base.current_ratio) : '-'),
      stockReportHeroMetric('8期净利为正', base.net_profit_positive_8q != null ? (fmt(base.net_profit_positive_8q) + '/8') : '-'),
      stockReportHeroMetric('8期现金为正', base.operating_cashflow_positive_8q != null ? (fmt(base.operating_cashflow_positive_8q) + '/8') : '-'),
      stockReportHeroMetric('4期营收同比为正', base.revenue_yoy_positive_4q != null ? (fmt(base.revenue_yoy_positive_4q) + '/4') : '-'),
      stockReportHeroMetric('4期利润同比为正', base.profit_yoy_positive_4q != null ? (fmt(base.profit_yoy_positive_4q) + '/4') : '-'),
      stockReportHeroMetric('3年股本变动', base.total_shares_growth_3y != null ? signedPct(base.total_shares_growth_3y) : '-'),
      stockReportHeroMetric('股东人数变动', base.holder_count_change_pct != null ? signedPct(base.holder_count_change_pct) : '-')
    ];
    var stageRows = [
      stockReportHeroMetric('路径状态', base.path_state || '待判断', base.stage_reason || ''),
      stockReportHeroMetric('执行门槛', stockGateInfo(base).label || '未分层', stockGateInfo(base).reason || ''),
      stockReportHeroMetric('1月收益', base.return_1m != null ? signedPct(base.return_1m) : '-'),
      stockReportHeroMetric('3月收益', base.return_3m != null ? signedPct(base.return_3m) : '-'),
      stockReportHeroMetric('6月收益', base.return_6m != null ? signedPct(base.return_6m) : '-'),
      stockReportHeroMetric('12月收益', base.return_12m != null ? signedPct(base.return_12m) : '-'),
      stockReportHeroMetric('60日回撤', base.max_drawdown_60d != null ? pct(base.max_drawdown_60d) : '-'),
      stockReportHeroMetric('距250日线', base.dist_ma250_pct != null ? signedPct(base.dist_ma250_pct) : '-'),
      stockReportHeroMetric('站上250日线', base.above_ma250 != null ? (base.above_ma250 ? '是' : '否') : '-'),
      stockReportHeroMetric('20/120量能', base.amount_ratio_20_120 != null ? scoreNum(base.amount_ratio_20_120) : '-'),
      stockReportHeroMetric('20日波动', base.volatility_20d != null ? pct(base.volatility_20d) : '-'),
      stockReportHeroMetric('20日振幅', base.amplitude_20d != null ? pct(base.amplitude_20d) : '-'),
      stockReportHeroMetric('通用阶段', base.generic_stage_raw != null ? scoreNum(base.generic_stage_raw) : '-'),
      stockReportHeroMetric('类型修正', base.stage_type_adjust_raw != null ? signedScore(base.stage_type_adjust_raw) : '-')
    ];
    var attentionRows = [
      stockReportHeroMetric('关注指数', base.attention_focus_index != null ? scoreNum(base.attention_focus_index) : (snapshot.focus_index != null ? scoreNum(snapshot.focus_index) : '-')),
      stockReportHeroMetric('综合关注分', base.attention_composite_score != null ? scoreNum(base.attention_composite_score) : (snapshot.composite_score != null ? scoreNum(snapshot.composite_score) : '-')),
      stockReportHeroMetric('机构参与', base.attention_institution_participation != null ? pct(base.attention_institution_participation) : (snapshot.institution_participation != null ? pct(snapshot.institution_participation) : '-')),
      stockReportHeroMetric('30/90天调研', fmt(survey30) + ' / ' + fmt(survey90), snapshot.last_survey_date ? ('最新 ' + fmtDate(snapshot.last_survey_date)) : ''),
      stockReportHeroMetric('90天研报 / 30天新闻', fmt(research.count_90d || 0) + ' / ' + fmt(news.count_30d || 0), research.latest_date ? ('研报 ' + fmtDate(research.latest_date)) : (news.latest_time ? ('新闻 ' + fmtDate(news.latest_time)) : ''))
    ];
    var turtleRows = [
      stockReportHeroMetric('系统 / 状态', [base.turtle_preferred_system ? turtleSystemLabel(base.turtle_preferred_system) : '', base.turtle_setup_state || '未覆盖'].filter(Boolean).join(' / ') || '未覆盖', base.turtle_reason || ''),
      stockReportHeroMetric('ATR% / 收盘', [base.atr_14_pct != null ? pct(base.atr_14_pct) : '-', base.close_price != null ? Number(base.close_price).toFixed(2) : '-'].join(' / '), base.latest_trade_date ? fmtDate(base.latest_trade_date) : ''),
      stockReportHeroMetric('20日 / 55日突破位', [base.entry_level_20 != null ? Number(base.entry_level_20).toFixed(2) : '-', base.entry_level_55 != null ? Number(base.entry_level_55).toFixed(2) : '-'].join(' / '), [base.breakout_dist_20_pct != null ? ('距20 ' + signedPct(base.breakout_dist_20_pct)) : '', base.breakout_dist_55_pct != null ? ('距55 ' + signedPct(base.breakout_dist_55_pct)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('10日 / 20日退出位', [base.exit_level_10 != null ? Number(base.exit_level_10).toFixed(2) : '-', base.exit_level_20 != null ? Number(base.exit_level_20).toFixed(2) : '-'].join(' / '), [base.exit_dist_10_pct != null ? ('距10 ' + signedPct(base.exit_dist_10_pct)) : '', base.exit_dist_20_pct != null ? ('距20 ' + signedPct(base.exit_dist_20_pct)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('S1止损 / 加仓', [base.stop_level_20_2n != null ? Number(base.stop_level_20_2n).toFixed(2) : '-', [base.add_level_20_1, base.add_level_20_2, base.add_level_20_3].filter(function (value) { return value != null; }).map(function (value) { return Number(value).toFixed(2); }).join(' / ') || '-'].join(' / ')),
      stockReportHeroMetric('S2止损 / 加仓', [base.stop_level_55_2n != null ? Number(base.stop_level_55_2n).toFixed(2) : '-', [base.add_level_55_1, base.add_level_55_2, base.add_level_55_3].filter(function (value) { return value != null; }).map(function (value) { return Number(value).toFixed(2); }).join(' / ') || '-'].join(' / '))
    ];
    var noteCallouts = [
      base.stage_reason ? { label: '阶段判断', text: base.stage_reason, tone: 'neutral' } : null,
      base.turtle_reason ? { label: '海龟说明', text: base.turtle_reason, tone: 'neutral' } : null
    ].filter(Boolean);
    return renderStockReportSection(
      '研究底稿',
      '把列表里省掉的财务、路径和执行细节都压回到表格里，展开就是一页研报。',
      '<div class="stock-report-data-grid">' +
      renderStockReportSubtable('公司质量', qualityRows, 2) +
      renderStockReportSubtable('阶段与交易位置', stageRows, 2) +
      renderStockReportSubtable('外部热度', attentionRows, 2) +
      renderStockReportSubtable('海龟执行参考', turtleRows, 1) +
      '</div>' +
      renderStockReportCallouts(noteCallouts)
    );
  }
  function renderStockDetailCardGrid(base) {
    return '<div class="stock-detail-grid">' +
      renderStockReportScoreSection(base) +
      renderStockReportDataSection(base) +
      '</div>';
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
    showView('stocks');
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
      }
    } catch (e) {
      sel.innerHTML = '<option value="">(加载失败)</option>';
    }
    renderModelMonitor();
  }

  async function renderModelMonitor() {
    await Promise.all([renderMetricsCards(), renderDailyChart(), renderRegimeChart(), renderFeatureImportance()]);
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

  window.App = { showView, setAlias, setType, toggleBlack, deleteInst, restoreInst, toggleInstDetail, toggleInstBreakdown, showL2Profile, toggleStockDetail, switchInstDim, switchStockDim, runSingleStep, loadWatchlist, loadExclusions, refreshNetwork, _api: api };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startInit, { once: true });
  } else {
    startInit();
  }
})();
