/* ============================================================
   etf-analysis.js — ETF 深度分析 widget
   负责 /api/etf/analysis/{code} 的详情面板、买卖点时间轴和净值曲线
   API: window.ETFAnalysisWidget.mountDeepAnalysis(code, panelId, deps)
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

  function fmtNum(value, digits) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toFixed(digits == null ? 2 : digits);
  }

  function fmtCurrency(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return '￥' + num.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }

  function fmtSignedPct(value) {
    if (value == null || value === '') return '-';
    var num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return (num >= 0 ? '+' : '') + num.toFixed(1) + '%';
  }

  function etfNum(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return '-';
    return Number(value).toFixed(digits == null ? 1 : digits);
  }

  function etfStrategyTone(kind) {
    if (kind === '买入持有' || kind === '趋势持有') return { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' };
    if (kind === '网格交易' || kind === '网格候选') return { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' };
    if (kind === '防守停泊') return { bg: 'var(--cm-bg)', fg: 'var(--cm-ink-700)' };
    if (kind === '暂不参与') return { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' };
    return { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' };
  }

  function defaultIdentityBlock(code, name) {
    return '<div class="security-identity security-identity--hero" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<span style="font-size:18px;font-weight:700">' + escText(name || code || '-') + '</span>' +
      '<span class="muted" style="font-size:12px">' + escText(code || '-') + '</span>' +
      '</div>';
  }

  function resolveDeps(deps) {
    deps = deps || {};
    return {
      esc: pickFn(deps, 'esc', escText),
      fmt: pickFn(deps, 'fmt', fmtNum),
      fmtCurrency: pickFn(deps, 'fmtCurrency', fmtCurrency),
      signedPct: pickFn(deps, 'signedPct', fmtSignedPct),
      etfNum: pickFn(deps, 'etfNum', etfNum),
      etfStrategyTone: pickFn(deps, 'etfStrategyTone', etfStrategyTone),
      securityIdentityBlock: pickFn(deps, 'securityIdentityBlock', defaultIdentityBlock),
      scheduleSortableTables: pickFn(deps, 'scheduleSortableTables', function () {}),
    };
  }

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
      if (th.querySelector('input[type="checkbox"]') || th.style.width === '30px') return;
      th.style.cursor = 'pointer';
      th.addEventListener('click', function () {
        var tbody = tableEl.querySelector('tbody');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        var asc = th.dataset.sortDir !== 'asc';
        heads.forEach(function (h) { h.dataset.sortDir = ''; h.textContent = h.textContent.replace(/ [▲▼]/, ''); });
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
    var container = typeof root === 'string' ? document.getElementById(root) : root;
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

  function statusPill(label, passed, title) {
    var bg = passed ? 'var(--cm-ok-100)' : 'var(--cm-bad-100)';
    var fg = passed ? 'var(--cm-ok-500)' : 'var(--cm-bad-500)';
    return '<span title="' + escText(title || '') + '" style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:' + bg + ';color:' + fg + ';font-size:11px;font-weight:700">' + escText(label) + '</span>';
  }

  function ledgerMetric(label, value, note) {
    return '<div style="min-width:110px;flex:1 1 110px">' +
      '<div class="muted" style="font-size:10px;margin-bottom:4px">' + escText(label) + '</div>' +
      '<div style="font-size:13px;font-weight:700;color:var(--text)">' + value + '</div>' +
      (note ? '<div class="muted" style="font-size:10px;margin-top:3px">' + escText(note) + '</div>' : '') +
      '</div>';
  }

  function ledgerCard(title, target, isGrid, fmt, pct) {
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
      '<div><div style="font-size:13px;font-weight:700">' + escText(title) + '</div>' +
      (subtitle.length ? '<div class="muted" style="font-size:11px;margin-top:3px">' + escText(subtitle.join(' · ')) + '</div>' : '') +
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
      (failures.length ? '<div style="margin-top:10px;font-size:11px;color:var(--danger);line-height:1.6">淘汰/异常原因：' + escText(failures.join('；')) + '</div>' : '') +
      (isGrid && target.hard_gate_passed === false && target.hard_gate_reason ? '<div style="margin-top:8px;font-size:11px;color:var(--danger);line-height:1.6">硬约束结论：' + escText(target.hard_gate_reason) + '</div>' : '') +
      '</div>';
  }

  function buildEtfTradeTimelineHtml(dailyPrices, trades, stepPct, deps) {
    var d = resolveDeps(deps);
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
      xLabelHtml += '<text x="' + x.toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle" font-size="10" fill="var(--muted)">' + escText((prices[index].date || '').slice(0, 10)) + '</text>';
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
        '价格 ' + d.etfNum(trade.price, 2),
        '份额 ' + d.fmt(trade.units || 0),
        '金额 ' + d.fmtCurrency(trade.notional),
        trade.realized_pnl_pct != null ? ('盈亏 ' + d.signedPct(trade.realized_pnl_pct)) : '',
        trade.note || ''
      ].filter(Boolean).join(' | ');
      return '<g><circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="5" fill="' + color + '" stroke="var(--cm-surface)" stroke-width="1.5"></circle><title>' + escText(title) + '</title></g>';
    }).join('');

    var realizedTotal = (trades || []).reduce(function (sum, trade) {
      return sum + (trade.realized_pnl || 0);
    }, 0);
    var buyCount = (trades || []).filter(function (trade) { return trade.side === 'buy'; }).length;
    var sellCount = (trades || []).filter(function (trade) { return trade.side === 'sell'; }).length;

    var tradeRows = (trades || []).slice().reverse().map(function (trade) {
      var tone = trade.side === 'buy' ? { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)', label: '买入' } : { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)', label: '卖出' };
      return '<tr>' +
        '<td>' + escText((trade.date || '').slice(0, 10)) + '</td>' +
        '<td><span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:' + tone.bg + ';color:' + tone.fg + ';font-size:11px;font-weight:700">' + tone.label + '</span></td>' +
        '<td>' + d.etfNum(trade.price, 2) + '</td>' +
        '<td>' + d.fmt(trade.units || 0) + '</td>' +
        '<td>' + d.fmtCurrency(trade.notional) + '</td>' +
        '<td>' + d.fmtCurrency(trade.fee) + '</td>' +
        '<td>' + (trade.realized_pnl != null ? d.fmtCurrency(trade.realized_pnl) : '<span class="muted">-</span>') + '</td>' +
        '<td>' + (trade.realized_pnl_pct != null ? d.signedPct(trade.realized_pnl_pct) : '<span class="muted">-</span>') + '</td>' +
        '<td>' + escText(trade.note || '') + '</td>' +
        '</tr>';
    }).join('');

    return '<div style="margin-bottom:14px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:8px">' +
      '<div style="font-weight:600;font-size:13px">日线买卖点时间轴 <span class="muted" style="font-size:11px">红买绿卖 · 步长 ' + escText(stepPct != null ? d.etfNum(stepPct, 1) + '%' : '-') + '</span></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-bad-100);color:var(--cm-bad-500);font-size:11px;font-weight:700">买入 ' + d.fmt(buyCount) + '</span>' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-ok-100);color:var(--cm-ok-500);font-size:11px;font-weight:700">卖出 ' + d.fmt(sellCount) + '</span>' +
      '<span style="padding:3px 8px;border-radius:999px;background:var(--cm-brand-50);color:var(--cm-brand-500);font-size:11px;font-weight:700">已实现盈亏 ' + d.fmtCurrency(realizedTotal) + '</span>' +
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

  function buildNavCurveSvg(gridCurve, bhCurve, stepPct, deps) {
    var W = 580, H = 200, padL = 50, padR = 20, padT = 20, padB = 30;
    var plotW = W - padL - padR, plotH = H - padT - padB;

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

    var yTicks = 5;
    var yLines = '';
    for (var i = 0; i <= yTicks; i++) {
      var val = minNav + (maxNav - minNav) * (i / yTicks);
      var y = padT + plotH - (i / yTicks) * plotH;
      yLines += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '" stroke="var(--line)" stroke-width="0.5" stroke-dasharray="3,3"/>';
      yLines += '<text x="' + (padL - 4) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end" font-size="10" fill="var(--muted)">' + val.toFixed(2) + '</text>';
    }

    var xTicks = [0, Math.floor((gridCurve.length - 1) / 2), gridCurve.length - 1].map(function (i) {
      return '<text x="' + (padL + (i / Math.max(gridCurve.length - 1, 1)) * plotW).toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle" font-size="10" fill="var(--muted)">' + escText((gridCurve[i] && gridCurve[i].date) ? String(gridCurve[i].date).slice(0, 10) : '-') + '</text>';
    }).join('');

    var legend =
      '<text x="' + (padL + 8) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-brand-400)" font-weight="600">— 网格 ' + (stepPct == null ? '-' : stepPct) + '%</text>' +
      '<text x="' + (padL + 120) + '" y="' + (padT + 14) + '" font-size="11" fill="var(--cm-ink-300)" font-weight="600">— 买入持有</text>';

    return '<svg viewBox="0 0 ' + W + ' ' + H + '" class="esc-chart">' +
      '<rect x="' + padL + '" y="' + padT + '" width="' + (W - padL - padR) + '" height="' + (H - padT - padB) +
      '" fill="var(--cm-surface)" stroke="var(--cm-ink-100)"></rect>' +
      yLines +
      toPath(bhCurve, 'var(--cm-ink-300)') +
      toPath(gridCurve, 'var(--cm-brand-400)') +
      xTicks +
      legend +
      '</svg>';
  }

  function buildDeepAnalysisHtml(code, data, deps, panelId) {
    var d = resolveDeps(deps);
    var containerId = panelId || (code + '-analysis');
    var info = data.info || {};
    var optimizerSummary = data.optimizer_summary || {};
    var verdict = data.verdict || {};
    var best = data.best_step || {};
    var bh = data.buy_hold || {};
    var tradeabilityOk = !info.tradeability_status || info.tradeability_status === 'ok';
    var issueHtml = '';
    if (!tradeabilityOk) {
      issueHtml = '<div style="margin-bottom:14px;padding:14px;border:1px solid var(--cm-bad-100);border-radius:14px;background:var(--cm-bad-100);color:var(--cm-bad-500);font-size:12px;line-height:1.8">' +
        '<div style="font-weight:700;margin-bottom:6px">该产品已从 ETF 可交易池中剔除</div>' +
        '<div>' + escText(info.tradeability_reason || '该产品未通过 ETF 基础交易性检查。') + '</div>' +
        '</div>';
    }

    var ratingColors = { '强烈推荐': { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' }, '推荐': { bg: 'var(--cm-brand-100)', fg: 'var(--cm-brand-500)' }, '中性': { bg: 'var(--cm-warn-100)', fg: 'var(--cm-warn-500)' }, '谨慎': { bg: 'var(--cm-bad-100)', fg: 'var(--cm-bad-500)' } };
    var rc = ratingColors[verdict.rating] || ratingColors['中性'];
    var recStrategy = data.recommended_strategy || '';
    var recStrategyTone = d.etfStrategyTone(recStrategy);

    var headerHtml =
      '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">' +
      d.securityIdentityBlock(code, info.name || code, { wrapperClass: 'security-identity security-identity--hero', includeMarketLink: true, includeXueqiuPill: true }) +
      '<span style="padding:3px 12px;border-radius:var(--radius-pill);background:' + rc.bg + ';color:' + rc.fg + ';font-size:12px;font-weight:700">' + escText(verdict.rating || '分析中') + '</span>' +
      (recStrategy ? '<span style="padding:3px 10px;border-radius:var(--radius-pill);background:' + recStrategyTone.bg + ';color:' + recStrategyTone.fg + ';font-size:11px;font-weight:700">推荐: ' + escText(recStrategy) + '</span>' : '') +
      (info.strategy_type ? '<span style="padding:2px 8px;border-radius:var(--radius-pill);background:var(--primary-light);color:var(--primary);font-size:11px;font-weight:600">' + escText(info.strategy_type) + '</span>' : '') +
      (info.setup_state ? '<span style="padding:2px 8px;border-radius:var(--radius-pill);background:var(--line-light);color:var(--text-2);font-size:11px;font-weight:600">' + escText(info.setup_state) + '</span>' : '') +
      '</div>';

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
        '<div class="muted" style="font-size:10px;margin-bottom:4px">' + escText(label) + '</div>' +
        '<div style="font-size:14px;font-weight:700;color:' + gColor + '">' + gv + (unit || '') + '</div>' +
        '<div style="font-size:11px;color:' + bColor + '">' + bv + (unit || '') + '</div>' +
        '</div>';
    }

    function buildComparisonHtml() {
      if (!tradeabilityOk || (!Object.keys(best).length && !Object.keys(bh).length)) return '';
      return '<div style="margin-bottom:14px">' +
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
        '<span style="padding:2px 8px;border-radius:999px;background:var(--cm-brand-100);color:var(--cm-brand-700);font-size:11px;font-weight:700">候选步长 ' + d.fmt(optimizerSummary.candidate_step_count || 0) + '</span>' +
        '<span style="padding:2px 8px;border-radius:999px;background:var(--cm-ok-100);color:var(--cm-ok-500);font-size:11px;font-weight:700">通过硬约束 ' + d.fmt(optimizerSummary.valid_step_count || 0) + '</span>' +
        '<span style="padding:2px 8px;border-radius:999px;background:var(--cm-bad-100);color:var(--cm-bad-500);font-size:11px;font-weight:700">淘汰 ' + d.fmt(optimizerSummary.rejected_step_count || 0) + '</span>' +
        '<span style="padding:2px 8px;border-radius:999px;background:' + (optimizerSummary.grid_available ? 'var(--cm-ok-100)' : 'var(--cm-warn-100)') + ';color:' + (optimizerSummary.grid_available ? 'var(--cm-ok-500)' : 'var(--cm-warn-500)') + ';font-size:11px;font-weight:700">' + (optimizerSummary.grid_available ? '存在可执行网格' : '无可执行网格') + '</span>' +
        '</div>' +
        '<div class="muted" style="font-size:11px;line-height:1.7">' + (optimizerSummary.model_rules || []).map(function (rule) { return '· ' + escText(rule); }).join('<br>') + '</div>' +
        '</div>';
    }

    function buildLedgerHtml() {
      if (!tradeabilityOk || (!Object.keys(best).length && !Object.keys(bh).length)) return '';
      var pct = function (value) { return value == null ? '-' : (Number(value) * 100).toFixed(2) + '%'; };
      var fmt = function (value) { return value == null ? '-' : Number(value).toFixed(3); };
      return '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">实盘账本检验</div>' +
        '<div style="display:flex;gap:12px;flex-wrap:wrap">' +
        ledgerCard('网格策略账本', best, true, fmt, pct) +
        ledgerCard('买入持有账本', bh, false, fmt, pct) +
        '</div></div>';
    }

    function buildStepsHtml() {
      if (!tradeabilityOk || !data.all_steps || !data.all_steps.length) return '';
      var bestStep = best.step_pct;
      var html = '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">全步长回测对比</div>' +
        '<div style="overflow-x:auto"><table class="data-table" style="font-size:11px"><thead><tr>' +
        '<th>步长</th><th>可执行</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>Sharpe</th><th>Calmar</th><th>胜率</th><th>买卖次数</th><th>淘汰/说明</th>' +
        '</tr></thead><tbody>';
      data.all_steps.forEach(function (s) {
        var isBest = s.step_pct === bestStep;
        var rowStyle = isBest ? 'background:var(--primary-light);font-weight:600' : (!s.hard_gate_passed ? 'background:var(--cm-bad-100)' : '');
        var gateHtml = statusPill(s.hard_gate_passed ? '通过' : '淘汰', !!s.hard_gate_passed, s.hard_gate_reason || '');
        html += '<tr style="' + rowStyle + '">' +
          '<td>' + s.step_pct + '%' + (isBest ? ' (best)' : '') + '</td>' +
          '<td>' + gateHtml + '</td>' +
          '<td style="color:' + (s.return_pct >= 0 ? 'var(--danger)' : 'var(--success)') + '">' + d.signedPct(s.return_pct) + '</td>' +
          '<td>' + (s.annual_return_pct != null ? s.annual_return_pct.toFixed(1) + '%' : '-') + '</td>' +
          '<td>' + (s.max_drawdown_pct != null ? s.max_drawdown_pct.toFixed(2) + '%' : '-') + '</td>' +
          '<td>' + (s.sharpe != null ? s.sharpe.toFixed(2) : '-') + '</td>' +
          '<td>' + (s.calmar != null ? s.calmar.toFixed(1) : '-') + '</td>' +
          '<td>' + (s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '-') + '</td>' +
          '<td>' + (s.buy_count || 0) + '买 / ' + (s.sell_count || 0) + '卖</td>' +
          '<td style="min-width:220px">' + escText(s.hard_gate_reason || '通过实盘硬约束') + '</td>' +
          '</tr>';
      });
      html += '</tbody></table></div></div>';
      return html;
    }

    function buildCurveHtml() {
      if (!tradeabilityOk || !best.curve || best.curve.length <= 2 || !bh.curve || bh.curve.length <= 2) return '';
      return '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">净值走势对比</div>' +
        buildNavCurveSvg(best.curve, bh.curve, best.step_pct, d) +
        '</div>';
    }

    function buildTimelineHtml() {
      return tradeabilityOk ? buildEtfTradeTimelineHtml(data.daily_prices || [], best.trades || [], best.step_pct, d) : '';
    }

    function buildPeriodHtml() {
      if (!tradeabilityOk || !data.multi_period || !data.multi_period.length) return '';
      var html = '<div style="margin-bottom:14px">' +
        '<div style="font-weight:600;font-size:13px;margin-bottom:8px">多周期稳定性检验</div>' +
        '<div style="overflow-x:auto"><table class="data-table" style="font-size:11px"><thead><tr>' +
        '<th>窗口</th><th>天数</th><th>网格收益</th><th>持有收益</th><th>超额</th><th>网格DD</th><th>持有DD</th><th>最优步长</th>' +
        '</tr></thead><tbody>';
      data.multi_period.forEach(function (p) {
        var gb = p.best;
        var pbh = p.buy_hold;
        var gridR = gb ? gb.return_pct : null;
        var bhR = pbh ? pbh.return_pct : null;
        var excess = (gridR != null && bhR != null) ? (gridR - bhR).toFixed(2) : '-';
        var excessColor = excess !== '-' ? (parseFloat(excess) >= 0 ? 'var(--danger)' : 'var(--success)') : 'var(--muted)';
        html += '<tr>' +
          '<td style="font-weight:600">' + escText(p.window) + '</td>' +
          '<td>' + p.days + '</td>' +
          '<td style="color:' + ((gridR || 0) >= 0 ? 'var(--danger)' : 'var(--success)') + '">' + (gridR != null ? d.signedPct(gridR) : '-') + '</td>' +
          '<td style="color:' + ((bhR || 0) >= 0 ? 'var(--danger)' : 'var(--success)') + '">' + (bhR != null ? d.signedPct(bhR) : '-') + '</td>' +
          '<td style="color:' + excessColor + ';font-weight:600">' + (excess !== '-' ? (parseFloat(excess) >= 0 ? '+' : '') + excess + '%' : '-') + '</td>' +
          '<td>' + (gb ? (gb.max_drawdown_pct != null ? gb.max_drawdown_pct.toFixed(2) + '%' : '-') : '-') + '</td>' +
          '<td>' + (pbh ? (pbh.max_drawdown_pct != null ? pbh.max_drawdown_pct.toFixed(2) + '%' : '-') : '-') + '</td>' +
          '<td>' + (gb ? gb.step_pct + '%' : '-') + '</td>' +
          '</tr>';
      });
      html += '</tbody></table></div></div>';
      return html;
    }

    function buildVerdictHtml() {
      if (!verdict.lines || !verdict.lines.length) return '';
      return '<div style="padding:12px 14px;background:' + rc.bg + ';border-radius:var(--radius);border:1px solid ' + rc.fg + '22">' +
        '<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:' + rc.fg + '">量化基金经理结论 · ' + escText(verdict.rating) + '</div>' +
        verdict.lines.map(function (line) {
          return '<div style="font-size:12px;line-height:1.7;color:' + rc.fg + '">· ' + escText(line) + '</div>';
        }).join('') +
        '</div>';
    }

      return '<div class="panel" style="margin-top:14px">' +
      '<div class="panel-head" style="justify-content:space-between">' +
      '<span style="font-weight:600">深度量化分析</span>' +
      '<button class="chip chip-ghost chip-sm" onclick="document.getElementById(\'' + escText(containerId) + '\').innerHTML=\'\'">关闭</button>' +
      '</div>' +
      headerHtml + issueHtml +
      '<div id="' + escText(containerId) + '-strategy-compare" style="margin-bottom:14px"></div>' +
      optimizerHtml + (tradeabilityOk ? buildComparisonHtml() : '') + buildLedgerHtml() + buildCurveHtml() + buildTimelineHtml() + buildStepsHtml() + buildPeriodHtml() + buildVerdictHtml() +
      '</div>';
  }

  async function mountDeepAnalysis(code, panelId, deps) {
    deps = deps || {};
    panelId = panelId || 'etfDeepAnalysisPanel';
    var panel = document.getElementById(panelId);
    if (!panel) return;
    panel.innerHTML = '<div class="panel" style="margin-top:14px"><div class="muted" style="padding:20px;text-align:center">加载 ' + escText(code) + ' 深度分析中...</div></div>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    try {
      var r = await fetch('/api/etf/analysis/' + encodeURIComponent(code));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var payload = await r.json();
      if (payload?.status !== 'ok' || !payload?.data) {
        panel.innerHTML = '<div class="panel" style="margin-top:14px"><div class="muted" style="padding:20px;text-align:center">分析失败: ' + escText(payload?.message || payload?.detail || '未知错误') + '</div></div>';
        return;
      }
      var html = buildDeepAnalysisHtml(code, payload.data, deps, panelId);
      panel.innerHTML = html;
      scheduleSortableTables(panel);
      if (global.ETFStrategyCompareWidget) {
        global.ETFStrategyCompareWidget.mount(panelId + '-strategy-compare', { code: code });
      }
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
      panel.innerHTML = '<div class="panel" style="margin-top:14px"><div class="muted" style="padding:20px;text-align:center">分析失败: ' + escText(e.message || e) + '</div></div>';
    }
  }

  global.ETFAnalysisWidget = {
    mountDeepAnalysis: mountDeepAnalysis,
    buildEtfTradeTimelineHtml: buildEtfTradeTimelineHtml,
    buildNavCurveSvg: buildNavCurveSvg,
    buildDeepAnalysisHtml: buildDeepAnalysisHtml,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.ETFAnalysisWidget = global.ETFAnalysisWidget;
  }
})(typeof window !== 'undefined' ? window : this);
