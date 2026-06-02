/* ============================================================
   workbench-health.js — 工作台健康 / 连通性 widget
   负责工作台健康卡、事件成熟度、机构概览、信号概览、管线状态与网络 pills
   API: window.WorkbenchHealthWidget.refreshWorkbenchHealthBar(deps)
        window.WorkbenchHealthWidget.refreshNetwork(deps)
   ============================================================ */
(function (global) {
  'use strict';

  var runtime = {
    api: null,
    fmt: null,
    fmtDate: null,
    fmtDateTime: null,
    doc: typeof document !== 'undefined' ? document : null,
    esc: function (v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },
  };

  function initRuntime(deps) {
    deps = deps || {};
    if (typeof deps.api === 'function') runtime.api = deps.api;
    if (typeof deps.fmt === 'function') runtime.fmt = deps.fmt;
    if (typeof deps.fmtDate === 'function') runtime.fmtDate = deps.fmtDate;
    if (typeof deps.fmtDateTime === 'function') runtime.fmtDateTime = deps.fmtDateTime;
    if (deps.document) runtime.doc = deps.document;
    if (typeof deps.esc === 'function') runtime.esc = deps.esc;
  }

  function el(id) {
    return runtime.doc && typeof runtime.doc.getElementById === 'function' ? runtime.doc.getElementById(id) : null;
  }

  function escText(value) {
    return runtime.esc ? runtime.esc(value) : String(value == null ? '' : value);
  }

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function fmt(value) {
    if (typeof runtime.fmt === 'function') return runtime.fmt(value);
    return formatUtils.formatNumber(value, 0, '—');
  }

  function fmtDate(value) {
    if (typeof runtime.fmtDate === 'function') return runtime.fmtDate(value);
    return value == null ? '—' : String(value);
  }

  function fmtDateTime(value) {
    if (typeof runtime.fmtDateTime === 'function') return runtime.fmtDateTime(value);
    return value == null ? '—' : String(value);
  }

  function api(path, opts) {
    if (!runtime.api) throw new Error('WorkbenchHealthWidget api missing');
    return runtime.api(path, opts);
  }

  function setText(id, text) {
    var node = el(id);
    if (node) node.textContent = text == null ? '—' : String(text);
  }

  function setHtml(id, html) {
    var node = el(id);
    if (node) node.innerHTML = html || '';
  }

  function setChip(id, text, tone) {
    var node = el(id);
    if (!node) return;
    node.textContent = text || '';
    node.classList.remove('chip-ok', 'chip-warn', 'chip-bad');
    if (tone) node.classList.add('chip-' + tone);
  }

  function daysAgoFromYmd(ymd) {
    if (!ymd) return null;
    var s = String(ymd).replace(/[^0-9]/g, '').slice(0, 8);
    if (s.length !== 8) return null;
    var dt = new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8));
    return Math.floor((Date.now() - dt.getTime()) / 86400000);
  }

  function renderHealthFreshness(status) {
    if (!status) {
      setText('wbFreshDate', '—');
      return;
    }
    setText('wbFreshDate', fmtDate(status.latest_notice_date));
    var days = daysAgoFromYmd(status.latest_notice_date);
    setText('wbFreshAge', days == null ? '' : '距今 ' + days + ' 天');
    if (days == null) setChip('wbFreshChip', '');
    else if (days <= 3) setChip('wbFreshChip', '新鲜', 'ok');
    else if (days <= 7) setChip('wbFreshChip', '稍旧', 'warn');
    else setChip('wbFreshChip', '已过时', 'bad');
  }

  function renderHealthEvents(evStats, status) {
    if (!evStats) {
      var fallback = (status && status.total_records) || 0;
      setText('wbEvMatured', fmt(fallback));
      setText('wbEvTotal', '事件总数');
      setText('wbEvPct', '');
      return;
    }
    var total = evStats.buy_total || 0;
    var matured = evStats.buy_matured || 0;
    var pct = total ? Math.round((matured / total) * 100) : 0;
    setText('wbEvMatured', fmt(matured));
    setText('wbEvTotal', '成熟 / 总 ' + fmt(total));
    setText('wbEvPct', '成熟率 ' + pct + '%');
  }

  function renderHealthInst(inst) {
    var arr = (inst && inst.data) ? inst.data : [];
    var summary = global.TypeSummaryWidget && typeof global.TypeSummaryWidget.collectEnabledTypeSummary === 'function'
      ? global.TypeSummaryWidget.collectEnabledTypeSummary(arr, 4)
      : null;
    var active = summary ? summary.active : arr.filter(function (item) { return item.enabled && !item.blacklisted; }).length;
    var total = summary ? summary.total : arr.length;
    setText('wbInstActive', fmt(active));
    setText('wbInstTotal', '总 ' + fmt(total) + ' · 黑名单 ' + fmt(total - active));
    var chipLabel = summary ? summary.label : '';
    var wbInstTypesEl = el('wbInstTypes');
    if (wbInstTypesEl) {
      wbInstTypesEl.textContent = chipLabel;
      wbInstTypesEl.title = summary ? summary.title : '';
    }
  }

  function renderHealthSignals(sig) {
    if (!sig) {
      setText('wbSigFollow', '—');
      return;
    }
    var by = (sig.summary && sig.summary.by_action) || {};
    setText('wbSigFollow', fmt(by.follow || 0) + ' 可跟');
    setText('wbSigWatchSkip', fmt(by.watch || 0) + ' 观察 · ' + fmt(by.skip || 0) + ' 不跟');
  }

  function renderHealthPipeline(up) {
    if (!up) {
      setText('wbPipeStatus', '—');
      return;
    }
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

  function normalizeSourceName(detail, fallback) {
    if (!detail) return fallback;
    var s = String(detail).trim();
    if (/^HTTP\s*\d+$/i.test(s)) return fallback;
    s = s.split(/[\s(（]/)[0];
    s = s.replace(/_[\d.]+:\d+$/, '');
    s = s.replace(/:\d+$/, '');
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

  async function refreshWorkbenchHealthBar(deps) {
    initRuntime(deps);
    try {
      var res = await Promise.all([
        api('/api/inst/market/status').catch(function () { return null; }),
        api('/api/inst/institutions').catch(function () { return null; }),
        api('/api/signals/events/stats').catch(function () { return null; }),
        api('/api/inst/update/status').catch(function () { return null; }),
        api('/api/signals/today?freshness_days=90&limit=2000').catch(function () { return null; }),
      ]);
      renderHealthFreshness(res[0]);
      renderHealthEvents(res[2], res[0]);
      renderHealthInst(res[1]);
      renderHealthSignals(res[4]);
      renderHealthPipeline(res[3]);
    } catch (e) { /* 不阻塞 */ }
  }

  async function refreshNetwork(deps) {
    initRuntime(deps);
    setSourcePill('sourcePillHoldings', '股东源', false, '', true);
    setSourcePill('sourcePillKline', 'K线源', false, '', true);
    setSourcePill('sourcePillIndustry', '行业源', false, '', true);
    try {
      var r = await api('/api/inst/update/connectivity?force=1');
      if (!r) {
        primeNetworkPills();
        return;
      }
      setSourcePill('sourcePillHoldings', '股东源', !!r.holdings_source, normalizeSourceName(r.holdings_source_detail, 'akshare'), false);
      setSourcePill('sourcePillKline', 'K线源', !!r.kline_source, normalizeSourceName(r.kline_source_detail, 'tdxhub'), false);
      setSourcePill('sourcePillIndustry', '行业源', !!r.industry_source, normalizeSourceName(r.industry_source_detail, 'tdxhub'), false);
    } catch (e) {
      primeNetworkPills();
    }
  }

  global.WorkbenchHealthWidget = {
    refreshWorkbenchHealthBar: refreshWorkbenchHealthBar,
    refreshNetwork: refreshNetwork,
    normalizeSourceName: normalizeSourceName,
    setSourcePill: setSourcePill,
  };
})(typeof window !== 'undefined' ? window : globalThis);
