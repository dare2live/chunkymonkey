/* widgets/screening-panel.js — 选股扫描卡片（工作台折叠区）
 *
 * window.ScreeningPanelWidget = { mount(containerId) }
 *
 * 解耦评分体系（Phase 1）后，TDX 选股 + 海龟执行特征 退出智能更新管线，
 * 改由本卡片手动触发。复用 App.runSingleStep（走 /update/step/{id} 主进度区）。
 */
(function (global) {
  'use strict';

  var SUMMARY_URL = '/api/screening/summary';
  var STATUS_URL = '/api/inst/update/status';

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function fmtDate(s) {
    if (!s) return '—';
    return String(s).slice(0, 10);
  }

  async function fetchJSON(url, opts) {
    var resp = await fetch(url, opts || {});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  }

  function renderCard(s) {
    s = s || {};
    var f1 = s.f1_hits || 0, f3 = s.f3_hits || 0, f5 = s.f5_hits || 0;
    var any = s.any_hit || 0;
    var total = s.total_stocks || 0;
    var hitPct = total > 0 ? formatUtils.formatPercent(any / total, 1, true, false, '—') : '—';
    var screenDate = fmtDate(s.screen_date);
    var turtleDate = fmtDate(s.turtle_snapshot_date);
    var turtleN = s.turtle_feature_count || 0;
    var brk = s.turtle_breakout_count || 0;
    var pre = s.turtle_pre_breakout_count || 0;
    var exit = s.turtle_exit_count || 0;
    return '' +
      '<div class="sig-bt-summary">' +
        '<div class="sig-bt-card sig-bt-follow">' +
          '<div class="sig-bt-lbl">TDX 命中（任一公式）</div>' +
          '<div class="sig-bt-val">' + formatUtils.formatNumber(any, 0) + '</div>' +
          '<div class="sig-bt-sub">/' + formatUtils.formatNumber(total, 0) + ' · ' + hitPct + ' · 扫描日 ' + esc(screenDate) + '</div>' +
        '</div>' +
        '<div class="sig-bt-card">' +
          '<div class="sig-bt-lbl">F1 / F3 / F5</div>' +
          '<div class="sig-bt-val">' + formatUtils.formatNumber(f1, 0) + ' · ' + formatUtils.formatNumber(f3, 0) + ' · ' + formatUtils.formatNumber(f5, 0) + '</div>' +
          '<div class="sig-bt-sub">F1 长期低位突破 · F3 多级回撤 · F5 连跌反转</div>' +
        '</div>' +
        '<div class="sig-bt-card">' +
          '<div class="sig-bt-lbl">海龟突破 · 待突破 · 退出</div>' +
          '<div class="sig-bt-val">' + formatUtils.formatNumber(brk, 0) + ' · ' + formatUtils.formatNumber(pre, 0) + ' · ' + formatUtils.formatNumber(exit, 0) + '</div>' +
          '<div class="sig-bt-sub">覆盖 ' + formatUtils.formatNumber(turtleN, 0) + ' 只 · 快照日 ' + esc(turtleDate) + '</div>' +
        '</div>' +
      '</div>';
  }

  async function loadSummary() {
    try {
      var r = await fetchJSON(SUMMARY_URL);
      return r || {};
    } catch (e) {
      return {};
    }
  }

  // 等当前 update job 跑完（_is_running 转 false 或步骤进入终态）。
  function waitForFinish(stepIds, onTick) {
    var watched = (stepIds || []).slice();
    var terminal = { completed: 1, failed: 1, skipped: 1, stopped: 1, blocked: 1, partial: 1 };
    return new Promise(function (resolve) {
      var idle = 0;
      var timer = setInterval(async function () {
        try {
          var st = await fetchJSON(STATUS_URL);
          if (typeof onTick === 'function') onTick(st);
          var allDone = (st && st.steps || []).every(function (row) {
            if (!watched.length || watched.indexOf(row.step_id) === -1) return true;
            return !!terminal[row.status];
          });
          var notRunning = !st || !st.is_running;
          if (allDone && notRunning) {
            idle += 1;
            if (idle >= 2) { clearInterval(timer); resolve(st); }
          } else {
            idle = 0;
          }
        } catch (e) { /* swallow */ }
      }, 1500);
    });
  }

  async function runStep(stepId, stepName) {
    if (!global.App || typeof global.App.runSingleStep !== 'function') {
      throw new Error('App.runSingleStep 不可用');
    }
    await global.App.runSingleStep(stepId, stepName);
    await waitForFinish([stepId]);
  }

  function setBusy(container, busy, hint) {
    var btns = container.querySelectorAll('button');
    btns.forEach(function (b) { b.disabled = !!busy; });
    var h = container.querySelector('[data-screening-hint]');
    if (h) h.textContent = hint || '';
  }

  function bindButtons(container, refresh) {
    container.querySelector('[data-screening-run-all]')?.addEventListener('click', async function () {
      setBusy(container, true, '运行中：TDX 选股 → 海龟特征…');
      try {
        await runStep('calc_screening', 'TDX选股筛选');
        await runStep('build_turtle_features', '海龟执行特征');
        await refresh();
      } catch (e) {
        setBusy(container, false, '失败：' + (e && e.message || e));
        return;
      }
      setBusy(container, false, '完成。');
    });
    container.querySelector('[data-screening-run-tdx]')?.addEventListener('click', async function () {
      setBusy(container, true, '运行中：TDX 选股…');
      try {
        await runStep('calc_screening', 'TDX选股筛选');
        await refresh();
      } catch (e) {
        setBusy(container, false, '失败：' + (e && e.message || e));
        return;
      }
      setBusy(container, false, '完成。');
    });
    container.querySelector('[data-screening-run-turtle]')?.addEventListener('click', async function () {
      setBusy(container, true, '运行中：海龟特征…');
      try {
        await runStep('build_turtle_features', '海龟执行特征');
        await refresh();
      } catch (e) {
        setBusy(container, false, '失败：' + (e && e.message || e));
        return;
      }
      setBusy(container, false, '完成。');
    });
    container.querySelector('[data-screening-refresh]')?.addEventListener('click', refresh);
  }

  async function mount(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<div class="muted" style="padding:10px">加载中…</div>';
    var summary = await loadSummary();

    async function refresh() {
      var s = await loadSummary();
      var head = container.querySelector('[data-screening-head]');
      if (head) head.innerHTML = renderCard(s);
    }

    container.innerHTML = '' +
      '<div data-screening-head>' + renderCard(summary) + '</div>' +
      '<div class="chip-group" style="margin-top:12px">' +
        '<button class="chip chip-primary" data-screening-run-all>一键扫描</button>' +
        '<button class="chip chip-outline" data-screening-run-tdx>仅 TDX 选股</button>' +
        '<button class="chip chip-outline" data-screening-run-turtle>仅海龟特征</button>' +
        '<button class="chip chip-ghost chip-sm" data-screening-refresh>刷新摘要</button>' +
      '</div>' +
      '<div class="muted" style="margin-top:8px;font-size:12px">' +
        '<span data-screening-hint></span> ' +
        '已迁出智能更新，需要时手动触发。运行进度见上方"运行日志"。' +
      '</div>';

    bindButtons(container, refresh);
  }

  global.ScreeningPanelWidget = { mount };
  if (typeof globalThis !== 'undefined') {
    globalThis.ScreeningPanelWidget = global.ScreeningPanelWidget;
  }
})(typeof window !== 'undefined' ? window : this);
