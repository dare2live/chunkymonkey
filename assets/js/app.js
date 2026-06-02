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
  var ReturnsChartWidget = window.ReturnsChartWidget || null;
  var TypeSummaryWidget = window.TypeSummaryWidget || null;
  var InstitutionScorecardWidget = window.InstitutionScorecardWidget || null;
  var ETFListWidget = window.ETFListWidget || null;
  var ETFOpportunityWidget = window.ETFOpportunityWidget || null;
  var ETFWorkbenchWidget = window.ETFWorkbenchWidget || null;
  var WorkbenchHealthWidget = window.WorkbenchHealthWidget || null;
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
      stocks: function () {
        // TopK 条带由 stock-view 自己挂 TopKStripWidget, app.js 不重复注入
        if (window.StockView) {
          if (!_stocksLoaded) { _stocksLoaded = true; window.StockView.load(); }
          else { window.StockView.reload(); }
        }
      },
      workbench: function () { window.WorkbenchView && window.WorkbenchView.show(); },
      dashboard: loadWorkbench,
      research: loadInstScorecard,
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

  function setHtml(id, html) { var n = el(id); if (n) n.innerHTML = html || ''; }
  function workbenchHealthDeps() {
    return {
      api: api,
      fmt: fmt,
      fmtDate: fmtDate,
      fmtDateTime: fmtDateTime,
    };
  }

  async function refreshWorkbenchHealthBar() {
    if (!WorkbenchHealthWidget || typeof WorkbenchHealthWidget.refreshWorkbenchHealthBar !== 'function') return;
    return WorkbenchHealthWidget.refreshWorkbenchHealthBar(workbenchHealthDeps());
  }

  async function refreshNetwork() {
    if (!WorkbenchHealthWidget || typeof WorkbenchHealthWidget.refreshNetwork !== 'function') return;
    return WorkbenchHealthWidget.refreshNetwork(workbenchHealthDeps());
  }

  function resolveStockSummary(stocks, stockSummary) {
    if (StockSummaryWidget && StockSummaryWidget.mergeStockSummary) {
      return StockSummaryWidget.mergeStockSummary(stocks || [], stockSummary || null, {
        stockGateInfo: stockGateInfo,
        stockSourceName: stockSourceName,
      });
    }
    var rows = Array.isArray(stocks) ? stocks : [];
    return {
      total: rows.length,
      followTotal: rows.filter(function (row) { return row && row.follow_gate === 'follow'; }).length,
      watchlistTotal: rows.filter(function (row) { return row && row._in_watchlist; }).length
    };
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

  // renderDashboardOverview 已随 Step 5d 工作台重塑移除。
  // 候选池/评分框架从「工作台」撤下，主入口统一为信号 v2。

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
  function evTag(type, label) { var cls = { new_entry: 'new', increase: 'up', decrease: 'down', exit: 'exit', unchanged: 'unchanged' }[type] || 'unchanged'; return '<span class="event-tag event-' + (cls) + '">' + esc(label || { new_entry: '新进', increase: '增持', decrease: '减持', exit: '退出', unchanged: '不变' }[type] || type) + '</span>' }

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
    if (!ETFWorkbenchWidget || typeof ETFWorkbenchWidget.mountEtfWorkbench !== 'function') {
      box.innerHTML = '<div class="panel"><div class="muted" style="padding:28px;text-align:center">ETF 工作台 widget 暂不可用</div></div>';
      return;
    }
    await ETFWorkbenchWidget.mountEtfWorkbench('etfWorkbenchContainer', {
      api: api,
      fmtDateTime: fmtDateTime,
      esc: esc,
      showEtfTab: showEtfTab,
    }, forceRefresh);
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

  var ModelMonitorWidget = window.ModelMonitorWidget || null;

  async function loadModelMonitor() {
    if (!ModelMonitorWidget || typeof ModelMonitorWidget.loadModelMonitor !== 'function') {
      var fallback = el('mm-comparison');
      if (fallback) fallback.innerHTML = '<div class="muted">模型监控 widget 暂不可用</div>';
      return;
    }
    return ModelMonitorWidget.loadModelMonitor({
      api: api,
      esc: esc,
      fmtDateTime: fmtDateTime,
    });
  }

  window.App = { showView, showWorkbenchTab, setAlias, setType, toggleBlack, deleteInst, restoreInst, toggleInstDetail, toggleInstBreakdown, showL2Profile, toggleStockDetail, switchInstDim, switchStockDim, runSingleStep, loadWatchlist, loadExclusions, refreshNetwork, _api: api };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startInit, { once: true });
  } else {
    startInit();
  }
})();
