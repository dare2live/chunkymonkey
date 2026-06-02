/**
 * Stock report helper for research overview and stock detail panels.
 *
 * Exported as window.StockReportWidget = {
 *   renderStockResearchSummary,
 *   renderStockReportSection,
 *   renderStockReportKeyTable,
 *   renderStockReportSubtable,
 *   renderStockReportModule,
 *   renderStockReportMatrixTable,
 *   renderStockReportCallouts,
 *   renderStockInstitutionCoverageSection,
 *   renderStockReportHero,
 *   renderStockEvidenceTimeline,
 *   renderStockReportScoreSection,
 *   renderStockReportDataSection,
 *   renderStockDetailCardGrid,
 * }.
 */
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

  function fmtNum(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toLocaleString('zh-CN');
  }

  function fmtPct(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toFixed(1) + '%';
  }

  function fmtSignedPct(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return (num >= 0 ? '+' : '') + num.toFixed(1) + '%';
  }

  function fmtSignedCount(value, suffix) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return (num >= 0 ? '+' : '') + num.toFixed(1) + (suffix || '');
  }

  function fmtScore(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toFixed(1);
  }

  function fmtSignedScore(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return (num >= 0 ? '+' : '') + num.toFixed(1);
  }

  function fmtDateDigits(value) {
    if (!value) return '-';
    var digits = String(value).replace(/[^0-9]/g, '').slice(0, 8);
    return digits.length === 8 ? digits.slice(0, 4) + '-' + digits.slice(4, 6) + '-' + digits.slice(6, 8) : String(value);
  }

  function daysFromDateDigits(value) {
    if (!value) return '-';
    var digits = String(value).replace(/[^0-9]/g, '').slice(0, 8);
    if (digits.length !== 8) return '-';
    var dt = new Date(digits.slice(0, 4) + '-' + digits.slice(4, 6) + '-' + digits.slice(6, 8) + 'T00:00:00');
    if (Number.isNaN(dt.getTime())) return '-';
    var diff = Math.floor((Date.now() - dt.getTime()) / 86400000);
    return diff >= 0 ? diff + '天前' : '未来 ' + Math.abs(diff) + ' 天';
  }

  function daysBetweenDates(left, right) {
    var a = String(left || '').replace(/[^0-9]/g, '').slice(0, 8);
    var b = String(right || '').replace(/[^0-9]/g, '').slice(0, 8);
    if (a.length !== 8 || b.length !== 8) return '-';
    var ad = new Date(a.slice(0, 4) + '-' + a.slice(4, 6) + '-' + a.slice(6, 8) + 'T00:00:00');
    var bd = new Date(b.slice(0, 4) + '-' + b.slice(4, 6) + '-' + b.slice(6, 8) + 'T00:00:00');
    if (Number.isNaN(ad.getTime()) || Number.isNaN(bd.getTime())) return '-';
    return Math.abs(Math.round((bd.getTime() - ad.getTime()) / 86400000)) + '天';
  }

  function defaultStockGateInfo() {
    return { key: '', label: '-', reason: '' };
  }

  function defaultAttentionSignalMeta(signal) {
    return {
      '外部确认增强': { label: '外部确认增强', tone: 'good' },
      '关注度抬升': { label: '关注度抬升', tone: 'accent' },
      '调研活跃': { label: '调研活跃', tone: 'info' },
      '热度拥挤': { label: '热度拥挤', tone: 'warn' }
    }[signal || ''] || null;
  }

  function defaultAttentionSignalTag(signal) {
    var meta = defaultAttentionSignalMeta(signal);
    if (!meta) return '';
    return '<span class="stock-attention-pill stock-attention-pill--' + meta.tone + '">' + escText(meta.label) + '</span>';
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

  function resolveDeps(deps) {
    deps = deps || {};
    return {
      esc: pickFn(deps, 'esc', escText),
      fmt: pickFn(deps, 'fmt', fmtNum),
      fmtDate: pickFn(deps, 'fmtDate', fmtDateDigits),
      fmtDateTime: pickFn(deps, 'fmtDateTime', function (value) { return value ? String(value).replace('T', ' ').slice(0, 19) : '-'; }),
      fmtGain: pickFn(deps, 'fmtGain', function (value) {
        if (value == null || value === '') return '-';
        var num = Number(value);
        if (!Number.isFinite(num)) return '-';
        return '<span class="' + (num >= 0 ? 'gain-pos' : 'gain-neg') + '">' + (num >= 0 ? '+' : '') + num.toFixed(1) + '%</span>';
      }),
      pct: pickFn(deps, 'pct', fmtPct),
      compactNum: pickFn(deps, 'compactNum', function (value) {
        if (value == null || value === '') return '-';
        var num = Number(value);
        if (!Number.isFinite(num)) return '-';
        return num >= 1e8 ? (num / 1e8).toFixed(1) + '亿' : num >= 1e4 ? (num / 1e4).toFixed(0) + '万' : num.toFixed(0);
      }),
      fmtCurrency: pickFn(deps, 'fmtCurrency', function (value) {
        if (value == null || value === '') return '-';
        var num = Number(value);
        if (!Number.isFinite(num)) return '-';
        return '￥' + num.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
      }),
      scoreNum: pickFn(deps, 'scoreNum', fmtScore),
      signedScore: pickFn(deps, 'signedScore', fmtSignedScore),
      signedPct: pickFn(deps, 'signedPct', fmtSignedPct),
      signedCountText: pickFn(deps, 'signedCountText', fmtSignedCount),
      daysFromDateDigits: pickFn(deps, 'daysFromDateDigits', daysFromDateDigits),
      daysBetweenDates: pickFn(deps, 'daysBetweenDates', daysBetweenDates),
      resolveStockSummary: pickFn(deps, 'resolveStockSummary', function (stocks, stockSummary) {
        var rows = Array.isArray(stocks) ? stocks : [];
        if (stockSummary && typeof stockSummary === 'object') {
          return Object.assign({
            total: rows.length,
            followTotal: 0,
            watchlistTotal: 0
          }, stockSummary);
        }
        return {
          total: rows.length,
          followTotal: rows.filter(function (row) { return row && row.follow_gate === 'follow'; }).length,
          watchlistTotal: rows.filter(function (row) { return row && row._in_watchlist; }).length
        };
      }),
      stockGateInfo: pickFn(deps, 'stockGateInfo', defaultStockGateInfo),
      stockGateTag: pickFn(deps, 'stockGateTag', function (s) {
        var info = defaultStockGateInfo(s);
        return '<span class="stock-attention-pill stock-attention-pill--neutral">' + escText(info.label || '-') + '</span>';
      }),
      stockSignalNarrative: pickFn(deps, 'stockSignalNarrative', function () { return '-'; }),
      stockSignalHeadline: pickFn(deps, 'stockSignalHeadline', function () { return '-'; }),
      stockSourceName: pickFn(deps, 'stockSourceName', function () { return '-'; }),
      preferredIndustryLabel: pickFn(deps, 'preferredIndustryLabel', function () { return ''; }),
      turtleSystemLabel: pickFn(deps, 'turtleSystemLabel', function () { return ''; }),
      turtleStateTag: pickFn(deps, 'turtleStateTag', function (state) { return '<span class="stock-attention-pill stock-attention-pill--neutral">' + escText(state || '未覆盖') + '</span>'; }),
      setupEventText: pickFn(deps, 'setupEventText', function (eventType) { return eventType || ''; }),
      followGateTag: pickFn(deps, 'followGateTag', function (gate) { return escText(gate || '-'); }),
      costMethodText: pickFn(deps, 'costMethodText', function (value) { return value || '-'; }),
      instLink: pickFn(deps, 'instLink', function (id, name) { return escText(name || id || '-'); }),
      evTag: pickFn(deps, 'evTag', function (type, label) { return escText(label || type || '-'); }),
      attentionSignalTag: pickFn(deps, 'attentionSignalTag', defaultAttentionSignalTag),
      attentionSignalMeta: pickFn(deps, 'attentionSignalMeta', defaultAttentionSignalMeta),
    };
  }

  function renderStockReportSection(title, subtitle, body, extraClass) {
    if (!body) return '';
    return '<section class="stock-report-section' + (extraClass ? (' ' + extraClass) : '') + '">' +
      '<div class="stock-report-section-head"><div class="stock-report-section-title">' + escText(title || '-') + '</div>' + (subtitle ? '<div class="stock-report-section-sub">' + escText(subtitle) + '</div>' : '') + '</div>' +
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
        var valueHtml = item.valueHtml != null ? item.valueHtml : escText(item.value == null ? '-' : String(item.value));
        var noteHtml = item.noteHtml != null ? item.noteHtml : (item.note ? escText(String(item.note)) : '');
        html += '<th>' + escText(item.label || '-') + '</th>' +
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
      '<div class="stock-report-subtable-head"><div class="stock-report-subtable-title">' + escText(title || '-') + '</div>' + (note ? '<div class="stock-report-subtable-note">' + escText(note) + '</div>' : '') + '</div>' +
      renderStockReportKeyTable(rows, pairsPerRow) +
      '</div>';
  }

  function renderStockReportModule(title, note, body, extraClass) {
    if (!body) return '';
    return '<div class="stock-report-subtable stock-report-module' + (extraClass ? (' ' + extraClass) : '') + '">' +
      '<div class="stock-report-subtable-head"><div class="stock-report-subtable-title">' + escText(title || '-') + '</div>' + (note ? '<div class="stock-report-subtable-note">' + escText(note) + '</div>' : '') + '</div>' +
      '<div class="stock-report-module-body">' + body + '</div>' +
      '</div>';
  }

  function renderStockReportMatrixTable(rows) {
    var items = (rows || []).filter(Boolean);
    if (!items.length) return '';
    return '<table class="data-table data-table-compact stock-report-matrix-table"><thead><tr><th>维度</th><th>分数</th><th>当前状态</th><th>关键信号</th><th>风险 / 约束</th></tr></thead><tbody>' +
      items.map(function (item) {
        var statusHtml = item.statusHtml != null ? item.statusHtml : escText(item.status == null ? '-' : String(item.status));
        return '<tr>' +
          '<td class="stock-report-matrix-dim">' + escText(item.dim || '-') + '</td>' +
          '<td>' + escText(item.score == null ? '-' : String(item.score)) + '</td>' +
          '<td>' + statusHtml + '</td>' +
          '<td>' + escText(item.signal || '-') + '</td>' +
          '<td>' + escText(item.risk || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }

  function renderStockReportCallouts(items) {
    var rows = (items || []).filter(Boolean);
    if (!rows.length) return '';
    return '<div class="stock-report-callout-row">' + rows.map(function (item) {
      return '<div class="stock-report-callout stock-report-callout--' + escText(item.tone || 'neutral') + '">' +
        '<div class="stock-report-callout-label">' + escText(item.label || '-') + '</div>' +
        '<div class="stock-report-callout-text">' + escText(item.text || '-') + '</div>' +
        '</div>';
    }).join('') + '</div>';
  }

  function renderStockResearchSummary(stocks, sectorSummary, stockSummary, deps) {
    var u = resolveDeps(deps);
    var summary = u.resolveStockSummary(stocks, stockSummary);
    var stockSummaryBar = document.getElementById('stockSummaryBar');
    var stockListMeta = document.getElementById('stockListMeta');
    if (stockSummaryBar) {
      stockSummaryBar.innerHTML = [
        { label: '覆盖股票', value: u.fmt(summary.total), sub: '被机构持仓的 A 股', tone: 'neutral' },
        { label: '可跟执行', value: u.fmt(summary.followTotal), sub: '当前 gate=follow 的持仓', tone: 'success' },
        { label: '已纳入自选', value: u.fmt(summary.watchlistTotal || 0), sub: '去「自选股」tab 管理', tone: 'accent' },
      ].map(function (item) {
        return '<div class="stock-summary-chip stock-summary-chip--' + item.tone + '">' +
          '<span class="stock-summary-label">' + u.esc(item.label) + '</span>' +
          '<strong>' + u.esc(item.value) + '</strong>' +
          '<small>' + u.esc(item.sub) + '</small>' +
          '</div>';
      }).join('');
    }
    if (stockListMeta) {
      stockListMeta.innerHTML =
        '<div class="table-meta-bar">' +
          '<div class="table-meta-copy">' +
            '<div class="table-meta-sub">共 ' + u.fmt(summary.total) + ' 只股票 · 可跟 ' + u.fmt(summary.followTotal) + '。点击股票行可展开机构持仓明细。</div>' +
          '</div>' +
        '</div>';
    }
  }

  function renderStockInstitutionCoverageSection(base, institutions, deps) {
    var u = resolveDeps(deps);
    var rows = Array.isArray(institutions) ? institutions : [];
    var followCount = rows.filter(function (item) { return item.follow_gate === 'follow'; }).length;
    var watchCount = rows.filter(function (item) { return item.follow_gate === 'watch'; }).length;
    var observeCount = rows.filter(function (item) { return item.follow_gate === 'observe'; }).length;
    var avoidCount = rows.filter(function (item) { return item.follow_gate === 'avoid'; }).length;
    var summaryRows = [
      stockReportHeroMetric('覆盖机构', u.fmt(rows.length), followCount ? ('可跟 ' + u.fmt(followCount) + ' 家') : '暂无可跟席位'),
      stockReportHeroMetric('执行分层', [followCount ? ('可跟 ' + u.fmt(followCount)) : '', watchCount ? ('关注 ' + u.fmt(watchCount)) : '', observeCount ? ('观察 ' + u.fmt(observeCount)) : '', avoidCount ? ('回避 ' + u.fmt(avoidCount)) : ''].filter(Boolean).join(' · ') || '-'),
      stockReportHeroMetric('最新报告', base.latest_report_date ? u.fmtDate(base.latest_report_date) : '-', base.latest_report_date ? u.daysFromDateDigits(base.latest_report_date) : '暂无'),
      stockReportHeroMetric('最新公告', base.latest_notice_date ? u.fmtDate(base.latest_notice_date) : '-', base.latest_notice_date ? u.daysFromDateDigits(base.latest_notice_date) : '暂无'),
      stockReportHeroMetric('最新收盘', base.price_timeline && base.price_timeline.end_close != null ? Number(base.price_timeline.end_close).toFixed(2) : '-', base.latest_close_date ? u.fmtDate(base.latest_close_date) : '待更新'),
      stockReportHeroMetric('覆盖结构', rows.length ? ('前3家 ' + rows.slice(0, 3).map(function (item) { return item.inst_name; }).filter(Boolean).join(' / ')) : '-', rows.length ? '按当前仍在持仓的跟踪机构排序' : '暂无机构覆盖')
    ];
    var tableRows = rows.map(function (inst) {
      var instMeta = [inst.holder_rank != null ? ('席位 #' + u.fmt(inst.holder_rank)) : '', inst.current_held_days != null ? (u.fmt(Math.round(inst.current_held_days)) + '天') : ''].filter(Boolean).join(' · ');
      var costText = inst.inst_ref_cost != null ? Number(inst.inst_ref_cost).toFixed(2) : '-';
      var costNote = inst.inst_cost_method ? u.costMethodText(inst.inst_cost_method) : '';
      return '<tr>' +
        '<td><div class="stock-source-cell"><div class="stock-source-main">' + u.instLink(inst.institution_id, inst.inst_name || '-', inst.inst_type) + '</div>' + (instMeta ? '<div class="stock-source-sub">' + u.esc(instMeta) + '</div>' : '') + '</div></td>' +
        '<td>' + (inst.event_type ? u.evTag(inst.event_type) : '<span class="muted">-</span>') + '</td>' +
        '<td>' + u.followGateTag(inst.follow_gate, inst.follow_gate_reason) + '</td>' +
        '<td>' + u.fmtDate(inst.report_date) + '</td>' +
        '<td>' + u.fmtDate(inst.notice_date) + '</td>' +
        '<td>' + u.fmtGain(inst.report_return_to_now) + '</td>' +
        '<td>' + u.fmtGain(inst.notice_return_to_now) + '</td>' +
        '<td>' + u.pct(inst.premium_pct) + '</td>' +
        '<td>' + u.pct(inst.hold_ratio) + '</td>' +
        '<td><div>' + u.esc(costText) + '</div>' + (costNote ? '<div class="muted" style="font-size:10px;margin-top:3px">' + u.esc(costNote) + '</div>' : '') + '</td>' +
        '<td>' + u.compactNum(inst.hold_market_cap) + '</td>' +
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

  function stockHouseholdCountText(value, deps) {
    var u = resolveDeps(deps);
    if (value == null) return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    if (Math.abs(num) >= 10000) return (num / 10000).toFixed(1) + '万户';
    return u.fmt(Math.round(num)) + '户';
  }

  function stockInstitutionCountText(value, deps) {
    var u = resolveDeps(deps);
    if (value == null) return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return u.fmt(Math.round(num)) + '家';
  }

  function stockMoneyText(value, deps) {
    var u = resolveDeps(deps);
    if (value == null) return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return u.compactNum(num);
  }

  function renderStockTrajectoryOverlayModule(overlay, deps) {
    var u = resolveDeps(deps);
    var series = overlay && Array.isArray(overlay.series) ? overlay.series.slice().reverse() : [];
    if (!series.length) return '';
    var headerNote = [
      overlay.quarters_loaded != null ? ('已载入 ' + u.fmt(overlay.quarters_loaded) + ' 季') : '',
      overlay.latest_complete_report_date ? ('完整季度 ' + u.fmtDate(overlay.latest_complete_report_date)) : '',
      overlay.latest_available_report_date && overlay.latest_available_report_date !== overlay.latest_complete_report_date ? ('最新落库 ' + u.fmtDate(overlay.latest_available_report_date)) : ''
    ].filter(Boolean).join(' · ');
    var rows = series.slice(0, 8).map(function (item) {
      var deltaParts = [];
      if (item.holder_count_delta_pct != null) deltaParts.push('股东 ' + u.signedPct(item.holder_count_delta_pct));
      if (item.inst_total_count_delta != null && Number(item.inst_total_count_delta) !== 0) deltaParts.push('机构 ' + u.signedCountText(item.inst_total_count_delta, '家'));
      if (item.fund_count_delta != null && Number(item.fund_count_delta) !== 0) deltaParts.push('基金 ' + u.signedCountText(item.fund_count_delta, '家'));
      if (item.national_team_shares_wan_delta != null && Number(item.national_team_shares_wan_delta) !== 0) deltaParts.push('国家队 ' + u.signedCountText(item.national_team_shares_wan_delta, '万股'));
      return '<tr>' +
        '<td>' + u.esc(u.fmtDate(item.report_date)) + '</td>' +
        '<td>' + u.esc(stockHouseholdCountText(item.holder_count, deps)) + '</td>' +
        '<td>' + u.esc(stockInstitutionCountText(item.inst_total_count, deps)) + '</td>' +
        '<td>' + u.esc(stockInstitutionCountText(item.fund_count, deps)) + '</td>' +
        '<td>' + u.esc(stockInstitutionCountText(item.insurance_count, deps)) + ' / ' + u.esc(stockInstitutionCountText(item.qfii_count, deps)) + '</td>' +
        '<td>' + u.esc(item.national_team_shares_wan != null ? (Number(item.national_team_shares_wan) >= 10000 ? (Number(item.national_team_shares_wan) / 10000).toFixed(2) + '亿股' : u.fmt(Math.round(Number(item.national_team_shares_wan))) + '万股') : '-') + '</td>' +
        '<td>' + u.esc(deltaParts.join(' · ') || '首个落库季度') + '</td>' +
        '</tr>';
    }).join('');
    var body = '<div class="stock-report-inline-note">' + u.esc(overlay.capability_note || 'TDXHub 季度结构已纳入时间轴。') + '</div>' +
      '<div class="stock-report-inline-note stock-report-inline-note--muted">上图信号区已把股东人数与机构总量入图，这里保留原始季度值与环比变化，便于复核。</div>' +
      '<div class="stock-report-table-wrap"><table class="data-table data-table-compact stock-report-trajectory-table"><thead><tr><th>季度</th><th>股东人数</th><th>机构总量</th><th>基金</th><th>保险 / QFII</th><th>国家队</th><th>环比变化</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
    return renderStockReportModule('TDXHub 季度结构明细', headerNote, body, 'stock-report-module--table');
  }

  function renderStockMarketSignalModule(marginOverlay, changeSummary, deps) {
    var u = resolveDeps(deps);
    var margin = marginOverlay || {};
    var summary = changeSummary || {};
    var hasMargin = Array.isArray(margin.points) && margin.points.length;
    var hasChange = summary.event_count != null || summary.increase_count != null || summary.decrease_count != null || summary.latest_notice_date;
    if (!hasMargin && !hasChange) return '';
    var note = [
      hasMargin && margin.latest_trade_date ? ('两融 ' + u.fmtDate(margin.latest_trade_date)) : '',
      hasChange && summary.latest_notice_date ? ('最新公告 ' + u.fmtDate(summary.latest_notice_date)) : '',
      summary.window_days ? ('窗口 ' + u.fmt(summary.window_days) + ' 天') : ''
    ].filter(Boolean).join(' · ');
    var rows = [
      {
        label: '融资余额',
        value: hasMargin && margin.latest_fin_balance != null ? stockMoneyText(margin.latest_fin_balance, deps) : '-',
        delta: hasMargin ? [
          margin.fin_balance_change_20d_pct != null ? ('20日 ' + u.signedPct(margin.fin_balance_change_20d_pct)) : '',
          margin.fin_balance_change_60d_pct != null ? ('60日 ' + u.signedPct(margin.fin_balance_change_60d_pct)) : ''
        ].filter(Boolean).join(' · ') || '-' : '-',
        note: hasMargin && margin.latest_fin_balance_ratio != null ? ('占比 ' + Number(margin.latest_fin_balance_ratio).toFixed(2) + '%') : '东财日频两融'
      },
      {
        label: '融券余额',
        value: hasMargin && margin.latest_loan_balance != null ? stockMoneyText(margin.latest_loan_balance, deps) : '-',
        delta: hasMargin ? [
          margin.loan_balance_change_20d_pct != null ? ('20日 ' + u.signedPct(margin.loan_balance_change_20d_pct)) : '',
          margin.loan_balance_change_60d_pct != null ? ('60日 ' + u.signedPct(margin.loan_balance_change_60d_pct)) : ''
        ].filter(Boolean).join(' · ') || '-' : '-',
        note: hasMargin && margin.latest_loan_balance_ratio != null ? ('占比 ' + Number(margin.latest_loan_balance_ratio).toFixed(3) + '%') : '东财日频两融'
      },
      {
        label: '高管/股东增减持',
        value: hasChange ? ('增持 ' + u.fmt(summary.increase_count || 0) + ' / 减持 ' + u.fmt(summary.decrease_count || 0)) : '-',
        delta: hasChange && summary.net_event_count != null ? ('净方向 ' + u.signedCountText(summary.net_event_count, '次')) : '-',
        note: hasChange ? (summary.latest_notice_date ? ('最新公告 ' + u.fmtDate(summary.latest_notice_date)) : '最近180天事件汇总') : '-'
      }
    ].filter(function (item) {
      return item.value !== '-' || item.delta !== '-' || item.note !== '-';
    });
    if (!rows.length) return '';
    var tableHtml = '<div class="stock-report-inline-note">融资/融券在图上按日频曲线展示，增减持在图上按事件带展示；这里保留最新原始口径。</div>' +
      '<div class="stock-report-table-wrap"><table class="data-table data-table-compact stock-report-trajectory-table"><thead><tr><th>信号</th><th>当前值</th><th>变化</th><th>说明</th></tr></thead><tbody>' + rows.map(function (item) {
        return '<tr>' +
          '<td>' + u.esc(item.label) + '</td>' +
          '<td>' + u.esc(item.value) + '</td>' +
          '<td>' + u.esc(item.delta) + '</td>' +
          '<td>' + u.esc(item.note) + '</td>' +
          '</tr>';
      }).join('') + '</tbody></table></div>';
    return renderStockReportModule('两融与增减持摘要', note, tableHtml, 'stock-report-module--table');
  }

  function renderStockTdxBlockModule(blockPayload, deps) {
    var u = resolveDeps(deps);
    var payload = blockPayload || {};
    var categories = Array.isArray(payload.categories) ? payload.categories.filter(function (item) {
      return item && Array.isArray(item.blocks) && item.blocks.length;
    }) : [];
    if (!categories.length) return '';
    var note = [
      payload.total_blocks != null ? ('归属 ' + u.fmt(payload.total_blocks) + ' 个板块') : '',
      payload.updated_at ? ('同步 ' + u.fmtDateTime(payload.updated_at)) : '',
      payload.source ? ('来源 ' + payload.source) : ''
    ].filter(Boolean).join(' · ');
    var body = '<div class="stock-report-inline-note">把 sync_industry 落下来的 TDX 概念 / 风格 / 指数归属挂回详情页，成员数越小通常代表篮子更窄。</div>' + categories.map(function (category) {
      var blocks = (category.blocks || []).slice(0, 8);
      var hiddenCount = Math.max(0, Number(category.count || blocks.length) - blocks.length);
      return '<div class="stock-report-inline-note"><strong>' + u.esc(category.label || category.category || '-') + '</strong>' +
        (category.count != null ? ' · ' + u.esc(u.fmt(category.count)) + ' 个' : '') +
        '</div>' +
        '<div class="stock-price-summary-row">' + blocks.map(function (block) {
          var title = block.member_count != null ? ('成员 ' + u.fmt(block.member_count)) : '成员数未知';
          var label = block.member_count != null ? ((block.name || '-') + ' · ' + u.fmt(block.member_count)) : (block.name || '-');
          return '<span class="stock-price-summary-pill" title="' + u.esc(title) + '">' + u.esc(label) + '</span>';
        }).join('') +
        (hiddenCount > 0 ? '<span class="stock-price-summary-pill">+' + u.esc(u.fmt(hiddenCount)) + ' 个更多</span>' : '') +
        '</div>';
    }).join('');
    return renderStockReportModule('TDX 板块归属', note, body, 'stock-report-module--table');
  }

  function parseDateDigits(value) {
    var digits = String(value || '').replace(/[^0-9]/g, '').slice(0, 8);
    if (digits.length !== 8) return null;
    var dt = new Date(digits.slice(0, 4) + '-' + digits.slice(4, 6) + '-' + digits.slice(6, 8) + 'T00:00:00');
    return Number.isNaN(dt.getTime()) ? null : dt;
  }

  function normalizeTimelineDateText(value) {
    if (!value) return '';
    var digits = String(value).replace(/[^0-9]/g, '').slice(0, 8);
    return digits.length === 8 ? digits.slice(0, 4) + '-' + digits.slice(4, 6) + '-' + digits.slice(6, 8) : String(value);
  }

  function timelineEventSortValue(dateText) {
    var parsed = parseDateDigits(dateText);
    return parsed ? parsed.getTime() : 0;
  }

  function stockTimelineLaneOrder() {
    return ['report', 'notice', 'capital', 'change', 'signal', 'survey', 'research', 'news'];
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

  function stockTrajectorySeriesMeta(key) {
    return {
      holder_count: { label: '股东人数', stroke: 'var(--cm-brand-700)', fill: 'rgba(15, 118, 110, 0.12)', text: 'var(--cm-brand-700)' },
      inst_total_count: { label: '机构总量', stroke: 'var(--cm-warn-500)', fill: 'rgba(194, 65, 12, 0.12)', text: 'var(--cm-warn-500)' },
      fin_balance: { label: '融资余额', stroke: 'var(--cm-accent-vivid)', fill: 'rgba(109, 40, 217, 0.12)', text: 'var(--cm-accent-vivid)' },
      loan_balance: { label: '融券余额', stroke: 'var(--cm-bad-500)', fill: 'rgba(220, 38, 38, 0.12)', text: 'var(--cm-bad-500)' }
    }[key] || { label: key || '信号', stroke: 'var(--cm-ink-500)', fill: 'rgba(100, 116, 139, 0.12)', text: 'var(--cm-ink-700)' };
  }

  function stockTrajectorySeriesByKey(seriesList, key) {
    var items = Array.isArray(seriesList) ? seriesList : [];
    for (var i = 0; i < items.length; i += 1) {
      if (items[i] && items[i].key === key) return items[i];
    }
    return null;
  }

  function buildStockTrajectorySeries(base, deps) {
    var u = resolveDeps(deps);
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
    appendSignalSeries('holder_count', quarterlySeries, 'holder_count', function (value) { return stockHouseholdCountText(value, deps); });
    appendSignalSeries('inst_total_count', quarterlySeries, 'inst_total_count', function (value) { return stockInstitutionCountText(value, deps); });
    appendSignalSeries('fin_balance', marginPoints, 'fin_balance', function (value) { return stockMoneyText(value, deps); });
    appendSignalSeries('loan_balance', marginPoints, 'loan_balance', function (value) { return stockMoneyText(value, deps); });
    return signalSeries;
  }

  function collectStockTimelineEvents(base) {
    return Array.isArray(base && base.timeline_events) ? base.timeline_events.slice() : [];
  }

  function stockTimelineEventSortValue(dateText) {
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
      grouped[groupKey].body = grouped[groupKey].baseBody + '；另有 ' + (grouped[groupKey].count - 1) + ' 条同日事件';
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
      return stockTimelineEventSortValue(a.date) - stockTimelineEventSortValue(b.date);
    });
  }

  function renderStockTrajectorySignalLegend(signalSeries, deps) {
    var u = resolveDeps(deps);
    var items = (Array.isArray(signalSeries) ? signalSeries : []).map(function (series) {
      var meta = stockTrajectorySeriesMeta(series.key);
      var latestDate = series.latestDate ? u.fmtDate(series.latestDate) : '';
      return "<span class='stock-price-legend-item stock-price-legend-item--curve'><span class='stock-price-legend-line' style='background:" + meta.stroke + "'></span><span class='stock-price-legend-label'>" + u.esc(meta.label) + "</span><span class='stock-price-legend-value'>" + u.esc(series.latestText || '-') + "</span>" + (latestDate ? "<span class='stock-price-legend-note'>" + u.esc(latestDate) + "</span>" : '') + "</span>";
    }).join('');
    return items ? "<div class='stock-price-legend stock-price-legend--curves'>" + items + '</div>' : '';
  }

  function renderStockTimelineEventLegend(events, deps) {
    var u = resolveDeps(deps);
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
      return "<span class='stock-price-legend-item'><span class='stock-price-legend-dot' style='background:" + meta.stroke + "'></span><span class='stock-price-legend-label'>" + u.esc(meta.label) + "</span><span class='stock-price-legend-note'>" + u.esc(u.fmt(counts[key])) + "</span></span>";
    }).join('');
    return items ? "<div class='stock-price-legend stock-price-legend--events'>" + items + '</div>' : '';
  }

  function renderStockTimelineEventDigest(events, deps) {
    var u = resolveDeps(deps);
    var rows = (Array.isArray(events) ? events : []).slice().sort(function (a, b) {
      return stockTimelineEventSortValue(b.date) - stockTimelineEventSortValue(a.date);
    }).slice(0, 12);
    if (!rows.length) return '';
    var body = "<div class='stock-report-table-wrap'><table class='data-table data-table-compact stock-price-event-table'><thead><tr><th>日期</th><th>类别</th><th>事项</th><th>明细</th></tr></thead><tbody>" + rows.map(function (event) {
      var meta = stockTimelineEventMeta(event);
      var badge = "<span class='stock-price-event-badge' style='background:" + meta.fill + ";color:" + meta.text + ";border-color:" + meta.stroke + "'>" + u.esc(meta.label) + '</span>';
      return '<tr><td>' + u.esc(u.fmtDate(event.date)) + '</td><td>' + badge + '</td><td>' + u.esc(event.title || '-') + '</td><td>' + u.esc(event.body || '-') + '</td></tr>';
    }).join('') + '</tbody></table></div>';
    return renderStockReportModule('事件明细', '与图上事件带一一对应，按时间倒序展示最近 12 条。', body, 'stock-report-module--table');
  }

  function buildStockTimelineSvg(points, signalSeries, events, deps) {
    var u = resolveDeps(deps);
    if (!Array.isArray(points) || !points.length) return "<div class='muted' style='padding:26px 0;text-align:center;font-size:12px'>暂无近三年价格数据。</div>";
    var validPoints = points.filter(function (point) { return parseDateDigits(point.date); });
    if (!validPoints.length) return "<div class='muted' style='padding:26px 0;text-align:center;font-size:12px'>价格日期无效。</div>";
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
      return (index ? 'L' : 'M') + ' ' + xFor(point.date).toFixed(1) + ' ' + priceYFor(point.close).toFixed(1);
    }).join(' ');
    var priceArea = pricePath + ' L ' + xFor(validPoints[validPoints.length - 1].date).toFixed(1) + ' ' + priceBottom + ' L ' + xFor(validPoints[0].date).toFixed(1) + ' ' + priceBottom + ' Z';
    var priceGrid = [0, 0.25, 0.5, 0.75, 1].map(function (ratio) {
      var y = top + (priceBottom - top) * ratio;
      var value = (high - (high - low) * ratio).toFixed(2);
      return "<line x1='" + left + "' y1='" + y.toFixed(1) + "' x2='" + (width - right) + "' y2='" + y.toFixed(1) + "' stroke='var(--cm-ink-100)' stroke-dasharray='4 4'></line><text x='" + (left - 10) + "' y='" + (y + 4).toFixed(1) + "' text-anchor='end' fill='var(--cm-ink-300)' font-size='10'>" + u.esc(value) + '</text>';
    }).join('');
    var tickIndexes = [0, 0.25, 0.5, 0.75, 1].map(function (ratio) {
      return Math.min(validPoints.length - 1, Math.round((validPoints.length - 1) * ratio));
    }).filter(function (value, index, arr) {
      return arr.indexOf(value) === index;
    });
    var ticks = tickIndexes.map(function (idx) {
      var point = validPoints[idx];
      var x = xFor(point.date);
      return "<line x1='" + x.toFixed(1) + "' y1='" + axisLineY + "' x2='" + x.toFixed(1) + "' y2='" + (axisLineY + 8) + "' stroke='var(--cm-ink-300)'></line><text x='" + x.toFixed(1) + "' y='" + axisLabelY + "' text-anchor='middle' fill='var(--cm-ink-300)' font-size='10'>" + u.esc(u.fmtDate(point.date)) + '</text>';
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
      return "<line x1='" + left + "' y1='" + y.toFixed(1) + "' x2='" + (width - right) + "' y2='" + y.toFixed(1) + "' stroke='var(--cm-brand-100)'></line><text x='" + (width - right + 4) + "' y='" + (y + 4).toFixed(1) + "' fill='var(--cm-ink-300)' font-size='10'>" + Math.round(ratio * 100) + '</text>';
    }).join('');
    var signalPaths = normalizedSeries.map(function (series) {
      var meta = stockTrajectorySeriesMeta(series.key);
      var path = series.points.map(function (point, index) {
        return (index ? 'L' : 'M') + ' ' + xFor(point.date).toFixed(1) + ' ' + signalYFor(point.ratio).toFixed(1);
      }).join(' ');
      var lastPoint = series.points[series.points.length - 1];
      var markers = series.points.length <= 16
        ? series.points.map(function (point) {
          return "<circle cx='" + xFor(point.date).toFixed(1) + "' cy='" + signalYFor(point.ratio).toFixed(1) + "' r='2.8' fill='" + meta.stroke + "'></circle>";
        }).join('')
        : '';
      return "<path d='" + path + "' fill='none' stroke='" + meta.stroke + "' stroke-width='" + (series.key.indexOf('balance') >= 0 ? '1.9' : '2.2') + "' stroke-linecap='round' stroke-linejoin='round'></path>" + markers + "<circle cx='" + xFor(lastPoint.date).toFixed(1) + "' cy='" + signalYFor(lastPoint.ratio).toFixed(1) + "' r='4.1' fill='var(--cm-surface)' stroke='" + meta.stroke + "' stroke-width='2'></circle>";
    }).join('');
    var laneLabels = laneKeys.map(function (laneKey) {
      var meta = stockTimelineEventMeta(laneKey === 'change' ? 'change' : laneKey);
      return "<text x='12' y='" + (laneYMap[laneKey] + 4) + "' fill='" + meta.text + "' font-size='11' font-weight='700'>" + u.esc(meta.label) + "</text><line x1='" + left + "' y1='" + laneYMap[laneKey] + "' x2='" + (width - right) + "' y2='" + laneYMap[laneKey] + "' stroke='var(--cm-ink-100)'></line>";
    }).join('');
    var eventMarks = condensedEvents.map(function (event) {
      var laneKey = stockTimelineLaneKey(event);
      var laneY = laneYMap[laneKey];
      if (laneY == null) return '';
      var meta = stockTimelineEventMeta(event);
      var x = xFor(event.date);
      var shadow = event.count > 1
        ? "<rect x='" + (x - 5.5 + 2).toFixed(1) + "' y='" + (laneY - 5.5 - 2).toFixed(1) + "' width='11' height='11' rx='4' fill='" + meta.fill + "' stroke='" + meta.stroke + "' stroke-width='1.2' opacity='0.42'></rect>"
        : '';
      var tooltip = [u.fmtDate(event.date), meta.label, event.title, event.body].filter(Boolean).join(' · ');
      return "<g><title>" + u.esc(tooltip) + "</title>" + shadow + "<rect x='" + (x - 5.5).toFixed(1) + "' y='" + (laneY - 5.5).toFixed(1) + "' width='11' height='11' rx='4' fill='" + meta.fill + "' stroke='" + meta.stroke + "' stroke-width='1.6'></rect></g>";
    }).join('');
    var lastPricePoint = validPoints[validPoints.length - 1];
    return "<svg class='stock-price-chart' viewBox='0 0 " + width + ' ' + height + "' aria-label='三年价格与多信号时间线'><defs><linearGradient id='stockPriceAreaGradientV237' x1='0' x2='0' y1='0' y2='1'><stop offset='0%' stop-color='var(--cm-brand-400)' stop-opacity='0.30'></stop><stop offset='100%' stop-color='var(--cm-surface)' stop-opacity='0'></stop></linearGradient></defs><text x='12' y='18' fill='var(--cm-ink-700)' font-size='11' font-weight='700'>价格</text><text x='12' y='206' fill='var(--cm-ink-700)' font-size='11' font-weight='700'>结构 / 资金信号</text><text x='" + (width - right) + "' y='206' text-anchor='end' fill='var(--cm-ink-300)' font-size='10'>各曲线按近三年各自区间归一化</text>" + priceGrid + "<rect x='" + left + "' y='" + signalTop + "' width='" + (width - left - right) + "' height='" + (signalBottom - signalTop) + "' rx='14' fill='var(--cm-brand-50)' stroke='var(--cm-brand-100)'></rect>" + signalGrid + "<path d='" + priceArea + "' fill='url(#stockPriceAreaGradientV237)'></path><path d='" + pricePath + "' fill='none' stroke='var(--cm-brand-500)' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'></path><circle cx='" + xFor(lastPricePoint.date).toFixed(1) + "' cy='" + priceYFor(lastPricePoint.close).toFixed(1) + "' r='4.6' fill='var(--cm-surface)' stroke='var(--cm-brand-500)' stroke-width='2'></circle>" + (normalizedSeries.length ? signalPaths : "<text x='" + (left + 12) + "' y='" + (signalTop + 34) + "' fill='var(--cm-ink-300)' font-size='11'>暂无可叠加的结构 / 资金曲线</text>") + "<line x1='" + left + "' y1='" + axisLineY + "' x2='" + (width - right) + "' y2='" + axisLineY + "' stroke='var(--cm-ink-300)'></line>" + laneLabels + eventMarks + ticks + '</svg>';
  }

  function renderStockEvidenceTimeline(base, deps) {
    var timeline = base.price_timeline || {};
    var points = timeline.points || [];
    var rawEvents = collectStockTimelineEvents(base);
    var condensedEvents = condenseStockTimelineEvents(rawEvents);
    var signalSeries = buildStockTrajectorySeries(base, deps);
    var overlayModule = renderStockTrajectoryOverlayModule(base.tdx_quarterly_overlay || null, deps);
    var marketSignalModule = renderStockMarketSignalModule(base.margin_balance_overlay || null, base.shareholder_change_summary || null, deps);
    var blockModule = renderStockTdxBlockModule(base.tdx_blocks || null, deps);
    var moduleGrid = [overlayModule, marketSignalModule, blockModule].filter(Boolean).join('');
    if (!points.length && !signalSeries.length && !condensedEvents.length && !moduleGrid) return '';
    var holderSeries = stockTrajectorySeriesByKey(signalSeries, 'holder_count');
    var instSeries = stockTrajectorySeriesByKey(signalSeries, 'inst_total_count');
    var finSeries = stockTrajectorySeriesByKey(signalSeries, 'fin_balance');
    var loanSeries = stockTrajectorySeriesByKey(signalSeries, 'loan_balance');
    var shareholderSummary = base.shareholder_change_summary || {};
    var summaryPills = [
      timeline.start_date && timeline.end_date ? '<span class="stock-price-summary-pill">区间 ' + escText(fmtDateDigits(timeline.start_date) + ' - ' + fmtDateDigits(timeline.end_date)) + '</span>' : '',
      timeline.change_pct != null ? '<span class="stock-price-summary-pill">三年涨跌 ' + escText(fmtSignedPct(timeline.change_pct)) + '</span>' : '',
      timeline.end_close != null ? '<span class="stock-price-summary-pill">最新价 ' + escText(Number(timeline.end_close).toFixed(2)) + '</span>' : '',
      holderSeries ? '<span class="stock-price-summary-pill">股东 ' + escText(holderSeries.latestText || '-') + '</span>' : '',
      instSeries ? '<span class="stock-price-summary-pill">机构 ' + escText(instSeries.latestText || '-') + '</span>' : '',
      finSeries ? '<span class="stock-price-summary-pill">融资 ' + escText(finSeries.latestText || '-') + '</span>' : '',
      loanSeries ? '<span class="stock-price-summary-pill">融券 ' + escText(loanSeries.latestText || '-') + '</span>' : '',
      shareholderSummary.event_count != null ? '<span class="stock-price-summary-pill">180天增/减 ' + escText(fmtNum(shareholderSummary.increase_count || 0) + '/' + fmtNum(shareholderSummary.decrease_count || 0)) + '</span>' : '',
      condensedEvents.length ? '<span class="stock-price-summary-pill">事件带 ' + escText(String(condensedEvents.length)) + ' 条</span>' : ''
    ].filter(Boolean).join('');
    var chartNote = '<div class="stock-price-chart-note">价格区使用前复权收盘；中段信号区把股东人数、机构总量、融资余额、融券余额按各自近三年区间归一化叠加；下方事件带与明细表按同一分类口径展示。</div>';
    var signalLegend = renderStockTrajectorySignalLegend(signalSeries, deps);
    var eventLegend = renderStockTimelineEventLegend(condensedEvents, deps);
    var eventDigest = renderStockTimelineEventDigest(condensedEvents, deps);
    return renderStockReportSection(
      '价格与事件轨迹',
      '把价格、结构资金曲线和事件带压到同一条横轴上，先看共振，再下钻到明细。',
      (summaryPills ? '<div class="stock-price-summary-row">' + summaryPills + '</div>' : '') +
      chartNote +
      '<div class="stock-price-chart-wrap">' + buildStockTimelineSvg(points, signalSeries, condensedEvents, deps) + '</div>' +
      signalLegend +
      eventLegend +
      eventDigest +
      (moduleGrid ? '<div class="stock-report-trajectory-grid">' + moduleGrid + '</div>' : ''),
      'stock-report-section--trajectory'
    );
  }

  function renderStockReportHero(base, attention, deps) {
    var u = resolveDeps(deps);
    if (!base) return '';
    var insts = Array.isArray(base.institutions) ? base.institutions : [];
    var timeline = base.price_timeline || {};
    var latestReport = base.latest_report_date || (base.setup && base.setup.latest_report_date) || (insts[0] && insts[0].report_date);
    var latestNotice = base.latest_notice_date || (base.setup && base.setup.latest_notice_date) || '';
    var followCount = insts.filter(function (item) { return item.follow_gate === 'follow'; }).length;
    var industry = '';
    if (base.industry) {
      industry = base.industry;
    }
    if (!industry) industry = u.preferredIndustryLabel(base);
    var chips = [u.scoreNum(base.priority_pool_score)];
    var poolTag = base.priority_pool ? '<span class="stock-attention-pill stock-attention-pill--neutral">' + u.esc(base.priority_pool) + '</span>' : '';
    if (poolTag) chips = [poolTag];
    if (u.stockGateInfo(base).key) chips.push(u.stockGateTag(base));
    if (base.external_attention_signal) chips.push(u.attentionSignalTag(base.external_attention_signal));
    if (base.turtle_setup_state) chips.push(u.turtleStateTag(base.turtle_setup_state, true));
    if (base.stock_archetype) chips.push('<span class="stock-attention-pill stock-attention-pill--neutral">' + u.esc(base.stock_archetype) + '</span>');
    var summaryParts = [];
    if (industry) summaryParts.push(industry);
    var narrative = u.stockSignalNarrative(base);
    if (narrative && narrative !== '-') summaryParts.push(narrative);
    if (base.score_risks) summaryParts.push('风险：' + base.score_risks);
    var heroRows = [
      stockReportHeroMetric('综合优先', u.scoreNum(base.composite_priority_score), base.priority_pool || '未分池', Number(base.composite_priority_score || 0) >= 75 ? 'good' : ''),
      stockReportHeroMetric('最新价', timeline.end_close != null ? Number(timeline.end_close).toFixed(2) : '-', timeline.end_date ? u.fmtDate(timeline.end_date) : '待K线'),
      stockReportHeroMetric('三年涨跌', timeline.change_pct != null ? u.signedPct(timeline.change_pct) : '-', timeline.start_date && timeline.end_date ? (u.fmtDate(timeline.start_date) + ' -> ' + u.fmtDate(timeline.end_date)) : '近三年价格', timeline.change_pct != null && Number(timeline.change_pct) >= 0 ? 'good' : ''),
      stockReportHeroMetric('最新报告', latestReport ? u.fmtDate(latestReport) : '-', latestReport ? u.daysFromDateDigits(latestReport) : '暂无'),
      stockReportHeroMetric('公告时点', latestNotice ? u.fmtDate(latestNotice) : '-', latestNotice ? u.daysFromDateDigits(latestNotice) : '暂无'),
      stockReportHeroMetric('当前机构', u.fmt(insts.length), followCount ? ('可跟 ' + followCount + ' 家') : '待执行分层')
    ];
    return '<div class="stock-report-hero">' +
      '<div class="stock-report-hero-top">' +
        '<div class="stock-report-hero-title">' +
          '<div class="stock-report-hero-name">' + u.esc(base.stock_name || base.display_name || base.stock_code || '-') + '</div>' +
          '<div class="stock-report-hero-sub">' + u.esc(summaryParts.join(' · ') || '-') + '</div>' +
        '</div>' +
        '<div class="stock-report-hero-chips">' + chips.filter(Boolean).join('') + '</div>' +
      '</div>' +
      '<div class="stock-report-hero-metrics">' + renderStockReportKeyTable(heroRows, 3) + '</div>' +
      '</div>';
  }

  function renderStockReportScoreSection(base, deps) {
    var u = resolveDeps(deps);
    var attention = base.attention || {};
    var snapshot = attention.snapshot || {};
    var research = attention.research || {};
    var news = attention.news || {};
    var survey30 = base.attention_survey_count_30d != null ? Number(base.attention_survey_count_30d) : Number(snapshot.survey_count_30d || 0);
    var survey90 = base.attention_survey_count_90d != null ? Number(base.attention_survey_count_90d) : Number(snapshot.survey_count_90d || 0);
    var qualitySourceLabel = base.company_quality_score_source === 'quality_feature_v1' ? '质量特征快照' : '评分引擎兜底';
    var qualitySourceHint = base.quality_snapshot_date ? ('快照 ' + u.fmtDate(base.quality_snapshot_date)) : (base.quality_latest_financial_report_date ? ('财报 ' + u.fmtDate(base.quality_latest_financial_report_date)) : '');
    var rows = [
      {
        dim: '发现',
        score: u.scoreNum(base.discovery_score),
        statusHtml: (base.setup_tag ? '<span class="stock-attention-pill stock-attention-pill--neutral">' + u.esc(base.setup_tag) + '</span> ' : '') + u.stockGateTag(base),
        signal: [u.stockSourceName(base), base.setup_event_type ? u.setupEventText(base.setup_event_type) : '', base.consensus_count != null ? ('共识 ' + u.fmt(base.consensus_count)) : ''].filter(Boolean).join(' · ') || '等待新的机构动作',
        risk: base.setup_execution_reason || base.priority_pool_reason || '等待下一次明确触发'
      },
      {
        dim: '质量',
        score: u.scoreNum(base.company_quality_score),
        status: base.stock_archetype || '待分类',
        signal: [base.roe != null ? ('ROE ' + u.pct(base.roe)) : '', base.gross_margin != null ? ('毛利 ' + u.pct(base.gross_margin)) : '', base.net_profit_positive_8q != null ? ('8期净利正 ' + u.fmt(base.net_profit_positive_8q) + '/8') : '', qualitySourceLabel].filter(Boolean).join(' · ') || '等待更多财务覆盖',
        risk: base.score_risks || qualitySourceHint || '财报覆盖仍需补厚'
      },
      {
        dim: '阶段',
        score: u.scoreNum(base.stage_score),
        status: base.path_state || '待判断',
        signal: [base.return_3m != null ? ('3月 ' + u.signedPct(base.return_3m)) : '', base.return_12m != null ? ('12月 ' + u.signedPct(base.return_12m)) : '', base.amount_ratio_20_120 != null ? ('量能 ' + u.scoreNum(base.amount_ratio_20_120)) : ''].filter(Boolean).join(' · ') || '等待路径确认',
        risk: base.stage_reason || base.composite_cap_reason || '观察阶段约束'
      },
      {
        dim: '外部',
        score: u.scoreNum(base.external_attention_score),
        statusHtml: u.attentionSignalTag(base.external_attention_signal) || '<span class="stock-attention-pill stock-attention-pill--neutral">中性</span>',
        signal: [survey30 ? ('30天调研 ' + u.fmt(survey30)) : '', survey90 ? ('90天调研 ' + u.fmt(survey90)) : '', Number(research.count_90d || 0) ? ('90天研报 ' + u.fmt(research.count_90d || 0)) : '', Number(news.count_30d || 0) ? ('30天新闻 ' + u.fmt(news.count_30d || 0)) : ''].filter(Boolean).join(' · ') || '外部关注仍偏安静',
        risk: base.external_crowding_penalty != null ? ('热度折扣 ' + u.scoreNum(base.external_crowding_penalty)) : '暂无拥挤惩罚'
      },
      {
        dim: '海龟',
        score: u.scoreNum(base.turtle_execution_score),
        statusHtml: base.turtle_setup_state ? u.turtleStateTag(base.turtle_setup_state, true) : '<span class="stock-attention-pill stock-attention-pill--neutral">未覆盖</span>',
        signal: [base.turtle_preferred_system ? u.turtleSystemLabel(base.turtle_preferred_system) : '', base.breakout_dist_20_pct != null ? ('距20日位 ' + u.signedPct(base.breakout_dist_20_pct)) : '', base.exit_dist_10_pct != null ? ('距10日位 ' + u.signedPct(base.exit_dist_10_pct)) : ''].filter(Boolean).join(' · ') || '等待突破位与退出位收敛',
        risk: base.turtle_reason || '执行层暂未给出额外说明'
      }
    ];
    return renderStockReportSection('评分与判断框架', '把发现、质量、阶段、外部和海龟放回同一张研究判断表。', renderStockReportMatrixTable(rows));
  }

  function renderStockReportDataSection(base, deps) {
    var u = resolveDeps(deps);
    var attention = base.attention || {};
    var snapshot = attention.snapshot || {};
    var research = attention.research || {};
    var news = attention.news || {};
    var industry = u.preferredIndustryLabel(base);
    var survey30 = base.attention_survey_count_30d != null ? Number(base.attention_survey_count_30d) : Number(snapshot.survey_count_30d || 0);
    var survey90 = base.attention_survey_count_90d != null ? Number(base.attention_survey_count_90d) : Number(snapshot.survey_count_90d || 0);
    var qualitySourceLabel = base.company_quality_score_source === 'quality_feature_v1' ? '质量特征快照' : '评分引擎兜底';
    var qualitySourceHint = [base.quality_snapshot_date ? ('快照 ' + u.fmtDate(base.quality_snapshot_date)) : '', base.quality_latest_financial_report_date ? ('财报 ' + u.fmtDate(base.quality_latest_financial_report_date)) : ''].filter(Boolean).join(' · ');
    var qualityRows = [
      stockReportHeroMetric('质量来源', qualitySourceLabel, qualitySourceHint),
      stockReportHeroMetric('股票类型', base.stock_archetype || '待分类', industry || ''),
      stockReportHeroMetric('财务口径', base.quality_latest_financial_report_date ? u.fmtDate(base.quality_latest_financial_report_date) : '-', [base.quality_latest_indicator_report_date ? ('指标 ' + u.fmtDate(base.quality_latest_indicator_report_date)) : '', base.quality_snapshot_date ? ('快照 ' + u.fmtDate(base.quality_snapshot_date)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('ROE', base.roe != null ? u.pct(base.roe) : '-', '', base.roe != null && Number(base.roe) >= 15 ? 'good' : ''),
      stockReportHeroMetric('ROA', base.roa_ak != null ? u.pct(base.roa_ak) : '-'),
      stockReportHeroMetric('毛利率', base.gross_margin != null ? u.pct(base.gross_margin) : '-'),
      stockReportHeroMetric('现利比', base.ocf_to_profit != null ? u.scoreNum(base.ocf_to_profit) : '-'),
      stockReportHeroMetric('负债率', base.debt_ratio != null ? u.pct(base.debt_ratio) : '-'),
      stockReportHeroMetric('流动比率', base.current_ratio != null ? u.scoreNum(base.current_ratio) : '-'),
      stockReportHeroMetric('8期净利为正', base.net_profit_positive_8q != null ? (u.fmt(base.net_profit_positive_8q) + '/8') : '-'),
      stockReportHeroMetric('8期现金为正', base.operating_cashflow_positive_8q != null ? (u.fmt(base.operating_cashflow_positive_8q) + '/8') : '-'),
      stockReportHeroMetric('4期营收同比为正', base.revenue_yoy_positive_4q != null ? (u.fmt(base.revenue_yoy_positive_4q) + '/4') : '-'),
      stockReportHeroMetric('4期利润同比为正', base.profit_yoy_positive_4q != null ? (u.fmt(base.profit_yoy_positive_4q) + '/4') : '-'),
      stockReportHeroMetric('3年股本变动', base.total_shares_growth_3y != null ? u.signedPct(base.total_shares_growth_3y) : '-'),
      stockReportHeroMetric('股东人数变动', base.holder_count_change_pct != null ? u.signedPct(base.holder_count_change_pct) : '-')
    ];
    var stageRows = [
      stockReportHeroMetric('路径状态', base.path_state || '待判断', base.stage_reason || ''),
      stockReportHeroMetric('执行门槛', u.stockGateInfo(base).label || '未分层', u.stockGateInfo(base).reason || ''),
      stockReportHeroMetric('1月收益', base.return_1m != null ? u.signedPct(base.return_1m) : '-'),
      stockReportHeroMetric('3月收益', base.return_3m != null ? u.signedPct(base.return_3m) : '-'),
      stockReportHeroMetric('6月收益', base.return_6m != null ? u.signedPct(base.return_6m) : '-'),
      stockReportHeroMetric('12月收益', base.return_12m != null ? u.signedPct(base.return_12m) : '-'),
      stockReportHeroMetric('60日回撤', base.max_drawdown_60d != null ? u.pct(base.max_drawdown_60d) : '-'),
      stockReportHeroMetric('距250日线', base.dist_ma250_pct != null ? u.signedPct(base.dist_ma250_pct) : '-'),
      stockReportHeroMetric('站上250日线', base.above_ma250 != null ? (base.above_ma250 ? '是' : '否') : '-'),
      stockReportHeroMetric('20/120量能', base.amount_ratio_20_120 != null ? u.scoreNum(base.amount_ratio_20_120) : '-'),
      stockReportHeroMetric('20日波动', base.volatility_20d != null ? u.pct(base.volatility_20d) : '-'),
      stockReportHeroMetric('20日振幅', base.amplitude_20d != null ? u.pct(base.amplitude_20d) : '-'),
      stockReportHeroMetric('通用阶段', base.generic_stage_raw != null ? u.scoreNum(base.generic_stage_raw) : '-'),
      stockReportHeroMetric('类型修正', base.stage_type_adjust_raw != null ? u.signedScore(base.stage_type_adjust_raw) : '-')
    ];
    var attentionRows = [
      stockReportHeroMetric('关注指数', base.attention_focus_index != null ? u.scoreNum(base.attention_focus_index) : (snapshot.focus_index != null ? u.scoreNum(snapshot.focus_index) : '-')),
      stockReportHeroMetric('综合关注分', base.attention_composite_score != null ? u.scoreNum(base.attention_composite_score) : (snapshot.composite_score != null ? u.scoreNum(snapshot.composite_score) : '-')),
      stockReportHeroMetric('机构参与', base.attention_institution_participation != null ? u.pct(base.attention_institution_participation) : (snapshot.institution_participation != null ? u.pct(snapshot.institution_participation) : '-')),
      stockReportHeroMetric('30/90天调研', u.fmt(survey30) + ' / ' + u.fmt(survey90), snapshot.last_survey_date ? ('最新 ' + u.fmtDate(snapshot.last_survey_date)) : ''),
      stockReportHeroMetric('90天研报 / 30天新闻', u.fmt(research.count_90d || 0) + ' / ' + u.fmt(news.count_30d || 0), research.latest_date ? ('研报 ' + u.fmtDate(research.latest_date)) : (news.latest_time ? ('新闻 ' + u.fmtDate(news.latest_time)) : ''))
    ];
    var turtleRows = [
      stockReportHeroMetric('系统 / 状态', [base.turtle_preferred_system ? u.turtleSystemLabel(base.turtle_preferred_system) : '', base.turtle_setup_state || '未覆盖'].filter(Boolean).join(' / ') || '未覆盖', base.turtle_reason || ''),
      stockReportHeroMetric('ATR% / 收盘', [base.atr_14_pct != null ? u.pct(base.atr_14_pct) : '-', base.close_price != null ? Number(base.close_price).toFixed(2) : '-'].join(' / '), base.latest_trade_date ? u.fmtDate(base.latest_trade_date) : ''),
      stockReportHeroMetric('20日 / 55日突破位', [base.entry_level_20 != null ? Number(base.entry_level_20).toFixed(2) : '-', base.entry_level_55 != null ? Number(base.entry_level_55).toFixed(2) : '-'].join(' / '), [base.breakout_dist_20_pct != null ? ('距20 ' + u.signedPct(base.breakout_dist_20_pct)) : '', base.breakout_dist_55_pct != null ? ('距55 ' + u.signedPct(base.breakout_dist_55_pct)) : ''].filter(Boolean).join(' · ')),
      stockReportHeroMetric('10日 / 20日退出位', [base.exit_level_10 != null ? Number(base.exit_level_10).toFixed(2) : '-', base.exit_level_20 != null ? Number(base.exit_level_20).toFixed(2) : '-'].join(' / '), [base.exit_dist_10_pct != null ? ('距10 ' + u.signedPct(base.exit_dist_10_pct)) : '', base.exit_dist_20_pct != null ? ('距20 ' + u.signedPct(base.exit_dist_20_pct)) : ''].filter(Boolean).join(' · ')),
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

  function renderStockDetailCardGrid(base, deps) {
    return '<div class="stock-detail-grid">' +
      renderStockReportScoreSection(base, deps) +
      renderStockReportDataSection(base, deps) +
      '</div>';
  }

  global.StockReportWidget = {
    renderStockResearchSummary: renderStockResearchSummary,
    renderStockReportSection: renderStockReportSection,
    renderStockReportKeyTable: renderStockReportKeyTable,
    renderStockReportSubtable: renderStockReportSubtable,
    renderStockReportModule: renderStockReportModule,
    renderStockReportMatrixTable: renderStockReportMatrixTable,
    renderStockReportCallouts: renderStockReportCallouts,
    renderStockInstitutionCoverageSection: renderStockInstitutionCoverageSection,
    renderStockReportHero: renderStockReportHero,
    renderStockEvidenceTimeline: renderStockEvidenceTimeline,
    renderStockReportScoreSection: renderStockReportScoreSection,
    renderStockReportDataSection: renderStockReportDataSection,
    renderStockDetailCardGrid: renderStockDetailCardGrid,
  };
})(typeof window !== 'undefined' ? window : globalThis);
