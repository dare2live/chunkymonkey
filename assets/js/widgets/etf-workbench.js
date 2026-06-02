/* ============================================================
   etf-workbench.js — ETF 工作台 widget
   负责 ETF 工作台总览、数据源与覆盖范围、ETF 结构、功能入口
   API: window.ETFWorkbenchWidget.mountEtfWorkbench(containerId, deps, forceRefresh)
   ============================================================ */
(function (global) {
  'use strict';

  var runtime = {
    api: null,
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

  function fmtDateTime(value) {
    if (typeof runtime.fmtDateTime === 'function') return runtime.fmtDateTime(value);
    return value == null ? '—' : String(value);
  }

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function api(path, opts) {
    if (!runtime.api) throw new Error('ETFWorkbenchWidget api missing');
    return runtime.api(path, opts);
  }

  function etfNum(value, digits) {
    return formatUtils.formatNumber(value, digits);
  }

  function statusPill(label, ok, detail) {
    var unknown = ok == null;
    var bg = unknown ? 'var(--cm-ink-100)' : (ok ? 'var(--cm-ok-100)' : 'var(--cm-bad-100)');
    var fg = unknown ? 'var(--cm-ink-700)' : (ok ? 'var(--cm-ok-500)' : 'var(--cm-bad-500)');
    var text = label + ' · ' + (unknown ? '未检测' : (ok ? '在线' : '离线'));
    if (detail) text += ' · ' + detail;
    return '<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:' + bg + ';color:' + fg + ';font-size:11px;font-weight:700">' + escText(text) + '</span>';
  }

  function workbenchLinkCard(title, desc, tag, tabName) {
    return '<div data-etf-tab="' + escText(tabName) + '" style="flex:1;min-width:220px;padding:14px;border:1px solid var(--cm-ink-100);border-radius:12px;background:var(--cm-bg);cursor:pointer">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">' +
      '<span style="font-weight:700;color:var(--cm-ink-900)">' + escText(title) + '</span>' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-ink-100);color:var(--cm-ink-700);font-size:11px;font-weight:700">' + escText(tag) + '</span>' +
      '</div>' +
      '<div style="font-size:12px;line-height:1.6;color:var(--cm-ink-700)">' + escText(desc) + '</div>' +
      '</div>';
  }

  function strategyShortcut(label, count, strategyType, tone) {
    return '<span data-etf-strategy="' + escText(strategyType) + '" style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:' + tone.bg + ';color:' + tone.fg + ';font-size:11px;font-weight:700;margin:4px 8px 0 0;cursor:pointer">' +
      escText(label + ' ' + etfNum(count || 0, 0)) +
      '</span>';
  }

  function buildWorkbenchHtml(d, deps) {
    deps = deps || {};
    var fmtDateTimeFn = typeof deps.fmtDateTime === 'function' ? deps.fmtDateTime : fmtDateTime;
    var esc = typeof deps.esc === 'function' ? deps.esc : escText;
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
      return '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-brand-50);color:var(--cm-brand-500);font-size:11px;font-weight:600">' + esc((item.source || '未知') + ' · ' + etfNum(item.count || 0, 0)) + '</span>';
    }).join('') || '<span class="muted">暂无来源明细</span>';
    var universeSourceText = source.universe_source || '暂无';
    var universeSourceUpdatedText = fmtDateTimeFn(source.universe_source_updated_at);
    var missingExamples = (source.no_kline_examples || []).length
      ? esc((source.no_kline_examples || []).join('、'))
      : '暂无';
    var strategyCounts = overview.strategy_counts || {};

    var html = '' +
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
      '<div style="margin-top:4px;font-size:12px;color:var(--cm-ink-500)"><strong>计算时间：</strong>' + esc(fmtDateTimeFn(snapshot.computed_at)) + ' · <strong>覆盖ETF：</strong>' + esc(etfNum(snapshot.etf_count, 0)) + '</div>' +
      (syncState.message ? '<div style="margin-top:8px;font-size:12px;color:var(--cm-ink-500)"><strong>最近任务：</strong>' + esc(syncState.message) + '</div>' : '') +
      '</div>' +
      '<div style="min-width:280px;flex:1">' +
      '<div class="stats-row" style="grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:0">' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(source.universe_count, 0)) + '</div><div class="stat-label">ETF池</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(source.kline_etf_count, 0)) + '</div><div class="stat-label">有日线</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(source.coverage_2023_count, 0)) + '</div><div class="stat-label">覆盖到2023</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(source.recent_only_count, 0)) + '</div><div class="stat-label">仅近期开盘</div></div>' +
      '<div class="stat-card"><div class="stat-value">' + esc(etfNum(source.no_kline_count, 0)) + '</div><div class="stat-label">暂无日线</div></div>' +
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
      '<div style="min-width:260px;flex:1"><strong>资产池来源：</strong>' + esc(universeSourceText) + '<br><strong>来源记录时间：</strong>' + esc(universeSourceUpdatedText) + '<br><strong>ETF池更新时间：</strong>' + esc(fmtDateTimeFn(source.universe_updated_at)) + '</div>' +
      '<div style="min-width:260px;flex:1"><strong>全局历史区间：</strong>' + esc((source.history_start || '-') + ' ~ ' + (source.history_end || '-')) + '<br><strong>日线覆盖率：</strong>' + esc(source.kline_coverage_ratio != null ? Number(source.kline_coverage_ratio).toFixed(2) + '%' : '-') + '<br><strong>最近成功：</strong>' + esc(fmtDateTimeFn(source.latest_kline_success_at)) + '</div>' +
      '<div style="min-width:260px;flex:1"><strong>最近尝试：</strong>' + esc(fmtDateTimeFn(source.latest_kline_attempt_at)) + '<br><strong>快照滞后：</strong>' + esc(source.snapshot_lag_minutes != null ? source.snapshot_lag_minutes + ' 分钟' : '-') + '<br><strong>同步错误数：</strong>' + esc(etfNum(source.last_error_count || 0, 0)) + '</div>' +
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

    return html;
  }

  async function mountEtfWorkbench(containerId, deps, forceRefresh) {
    initRuntime(deps);
    var box = el(containerId || 'etfWorkbenchContainer');
    if (!box) return;
    box.innerHTML = '<div class="panel"><div class="muted" style="padding:28px;text-align:center">加载 ETF 工作台...</div></div>';

    var path = '/api/etf/workbench';
    if (forceRefresh) path += '?force_refresh=true';
    var r = await api(path);
    if (r?.status !== 'ok' || !r?.data) {
      box.innerHTML = '<div class="panel"><div class="muted" style="padding:28px;text-align:center">工作台加载失败: ' + escText(r?.message || '未知错误') + '</div></div>';
      return;
    }

    box.innerHTML = buildWorkbenchHtml(r.data || {}, deps || {});
    box.querySelectorAll('[data-etf-tab]').forEach(function (node) {
      node.addEventListener('click', function () {
        if (deps && typeof deps.showEtfTab === 'function') {
          deps.showEtfTab(node.dataset.etfTab);
        }
      });
    });
  }

  global.ETFWorkbenchWidget = {
    mountEtfWorkbench: mountEtfWorkbench,
    buildWorkbenchHtml: buildWorkbenchHtml,
    etfNum: etfNum,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.ETFWorkbenchWidget = global.ETFWorkbenchWidget;
  }
})(typeof window !== 'undefined' ? window : globalThis);
