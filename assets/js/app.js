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
  var WorkbenchHealthWidget = window.WorkbenchHealthWidget || null;
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

  function setActiveState(root, selector, isActive) {
    if (!root) return;
    root.querySelectorAll(selector).forEach(function (node) {
      node.classList.toggle('active', !!isActive(node));
    });
  }

  function bindNodeClicks(selector, handler) {
    document.querySelectorAll(selector).forEach(function (node) {
      node.addEventListener('click', function () { handler(node); });
    });
  }

  // ============================================================
  // Navigation
  // ============================================================
  // ============================================================
  // Navigation — 股东挖掘
  // ============================================================
  function showGroup(name) {
    AppNav.setCurrentGroup(name);
    setActiveState(document, '.nav-group-btn', function (b) { return b.dataset.group === name; });
    var subHolder = el('nav-sub-holder');
    if (subHolder) subHolder.style.display = name === 'holder' ? '' : 'none';
    if (name === 'holder') {
      showView('workbench');
    }
  }

  function showView(name) {
    setActiveState(document, '.view', function (v) { return v.id === 'view-' + name; });
    // 更新子导航按钮的 active 状态（仅更新当前板块的子导航）
    var subBar = el('nav-sub-holder');
    if (subBar) {
      setActiveState(subBar, '.nav-btn', function (b) { return b.dataset.view === name; });
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

  // 顶层板块切换
  bindNodeClicks('.nav-group-btn', function (b) { showGroup(b.dataset.group); });

  // 子导航按钮
  bindNodeClicks('.nav-sub-bar .nav-btn', function (b) {
    showView(b.dataset.view);
  });

  // Dashboard sub-tabs
  // 工作台不再有 tabs，排除规则和网络检测在页面加载时一起执行

  // Step 5d：机构页已收束为 scorecard + 研究说明，不再保留列表/管理子面板。
  // 批量管理挪至工作台的折叠区，首次展开时延迟加载。

  // Stock sub-tabs
  bindNodeClicks('.stock-tabs .tab-btn', function (btn) {
    setActiveState(document, '.stock-tabs .tab-btn', function (b) { return b === btn; });
    setActiveState(document, '.stab-content', function (c) { return c.id === 'stab-' + btn.dataset.stab; });
    setStockSearchContext(btn.dataset.stab);
    loadActiveStockSubtab();
  });

  // ============================================================
  // Health
  // ============================================================
  async function checkHealth() {
    var badge = el('statusBadge'), r = await api('/health');
    if (r && r.status === 'ok') {
      badge.textContent = 'Online'; badge.className = 'logo-status online';
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
  // 手动任务触发 (2026-06-12 自动调度退役: daily_update/概念快照改按钮手动)
  // ============================================================
  var _opsPollTimer = null;
  var _opsButtonsRendered = false;
  function renderOpsButtons(jobs) {
    var group = el('opsJobsButtons');
    if (!group || _opsButtonsRendered) return;
    jobs.forEach(function (j, idx) {
      var btn = document.createElement('button');
      btn.className = idx === 0 ? 'chip chip-primary' : 'chip chip-outline';
      btn.textContent = j.job;
      btn.title = j.label || j.job;
      btn.addEventListener('click', function () { runOpsJob(j.job, btn); });
      group.appendChild(btn);
    });
    _opsButtonsRendered = jobs.length > 0;
  }
  async function refreshOpsJobs() {
    var box = el('opsJobsStatus');
    if (!box) return;
    var r = await api('/api/v3/ops/jobs');
    if (!r || !r.jobs) { box.textContent = '手动任务状态不可用 (后端未启动?)'; return; }
    renderOpsButtons(r.jobs);  // 注册表驱动: MANUAL_JOBS 加条目 -> 按钮自动出现
    var anyRunning = false;
    box.innerHTML = r.jobs.map(function (j) {
      var flags = Object.keys(j.alert_flags || {}).filter(function (k) { return j.alert_flags[k]; });
      var state;
      if (j.running) { state = '运行中'; anyRunning = true; }
      else if (flags.length) { state = '上次有失败/降级 flag'; }
      else { state = '空闲'; }
      return esc(j.job) + ': ' + esc(state);
    }).join('<br>');
    if (anyRunning && !_opsPollTimer) {
      _opsPollTimer = setInterval(refreshOpsJobs, 30000);
    } else if (!anyRunning && _opsPollTimer) {
      clearInterval(_opsPollTimer); _opsPollTimer = null;
    }
  }
  async function runOpsJob(job, btn) {
    if (btn) { btn.disabled = true; }
    var r = await api('/api/v3/ops/jobs/' + job + '/run', { method: 'POST' });
    if (r && r.started) {
      showToast(job + ' 已启动 (pid ' + r.pid + '), 后台运行', 'success');
    }
    if (btn) { btn.disabled = false; }
    refreshOpsJobs();
  }

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
    refreshOpsJobs();
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
