/* ============================================================
   etf-list.js — ETF 全量筛选 widget
   负责 /api/etf/list 的分类过滤、策略过滤、表格渲染和行内深度分析入口
   API: window.ETFListWidget.mountEtfList({ tableId, filterId, state, rows, deepPanelId, onAnalyze }, deps)
   ============================================================ */
(function (global) {
  'use strict';

  function escText(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function etfPctCell(value, invert) {
    var formatted = formatUtils.formatPercent(value, 2, false, true, '<span class="muted">-</span>');
    if (formatted === '<span class="muted">-</span>') return formatted;
    var num = Number(value);
    var positive = invert ? num <= 0 : num >= 0;
    var cls = positive ? 'gain-pos' : 'gain-neg';
    return '<span class="' + cls + '">' + formatted + '</span>';
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

  function cleanCode(value) {
    return String(value || '').replace(/[^0-9]/g, '').slice(0, 6);
  }

  function inferMarket(code) {
    var clean = cleanCode(code);
    if (!clean) return 'NA';
    if (clean[0] === '6') return 'SH';
    if ('0123'.indexOf(clean[0]) >= 0) return 'SZ';
    if ('489'.indexOf(clean[0]) >= 0) return 'BJ';
    return 'NA';
  }

  function xueqiuPillLink(code, text, compact) {
    var clean = cleanCode(code);
    var prefix = '';
    if (global.SecurityIdentity && typeof global.SecurityIdentity.xueqiuUrl === 'function') {
      var href = global.SecurityIdentity.xueqiuUrl({ code: clean });
      if (href) {
        prefix = href.slice('https://xueqiu.com/S/'.length, 'https://xueqiu.com/S/'.length + 2);
        return '<a class="cm-security-code" href="' + href + '" target="_blank" rel="noopener">' + escText(prefix) + ': ' + escText(text || clean || '-') + '</a>';
      }
    }
    prefix = inferMarket(clean);
    if (prefix === 'NA') {
      return '<span class="cm-security-code cm-security-code-na">' + escText(text || clean || '-') + '</span>';
    }
    return '<a class="cm-security-code" href="https://xueqiu.com/S/' + prefix + escText(clean) + '" target="_blank" rel="noopener">' + escText(prefix) + ': ' + escText(text || clean || '-') + '</a>';
  }

  function collectEtfCategoryCounts(rows) {
    var counts = {};
    (rows || []).forEach(function (row) {
      var cat = row && row.category ? row.category : '其他';
      counts[cat] = (counts[cat] || 0) + 1;
    });
    return counts;
  }

  function sortEtfCategories(categories) {
    var order = ['宽基', '医疗健康', '半导体', '新能源', '消费', '金融', '军工', '数字科技', '高端制造', '汽车', '电力公用', '地产建筑', '周期资源', '交通物流', '红利策略', '行业·其他', '跨境', '商品', '债券', '货币'];
    return (categories || []).slice().sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  }

  function applyEtfListFilters(rows, state) {
    var filtered = rows || [];
    if (!state) return filtered.slice();
    if (state.categoryFilter && state.categoryFilter !== 'all') {
      filtered = filtered.filter(function (row) { return row.category === state.categoryFilter; });
    }
    if (state.strategyFilter && state.strategyFilter !== 'all') {
      filtered = filtered.filter(function (row) { return row.strategy_type === state.strategyFilter; });
    }
    return filtered;
  }

  function buildEtfFilterBarHtml(state, rows) {
    var safeState = state || {};
    var safeRows = rows || [];
    var categoryCounts = collectEtfCategoryCounts(safeRows);
    var categories = sortEtfCategories(Object.keys(categoryCounts));
    var html = '<div class="type-filter">';
    html += '<span class="type-tag' + (safeState.categoryFilter === 'all' ? ' active' : '') + '" data-etfcat="all">全部 (' + safeRows.length + ')</span>';
    categories.forEach(function (cat) {
      var color = etfCatColor(cat);
      html += '<span class="type-tag' + (safeState.categoryFilter === cat ? ' active' : '') + '" data-etfcat="' + escText(cat) + '" style="--tc:' + color + '">' + escText(cat) + ' (' + categoryCounts[cat] + ')</span>';
    });
    html += '</div>';
    var stratTypes = ['买入持有', '网格交易', '防守停泊', '暂不参与'];
    html += '<div class="type-filter" style="margin-top:4px">';
    html += '<span class="type-tag' + (safeState.strategyFilter === 'all' ? ' active' : '') + '" data-etfstrat="all" style="font-size:11px">策略:全部</span>';
    stratTypes.forEach(function (strategy) {
      var tone = etfStrategyTone(strategy);
      html += '<span class="type-tag' + (safeState.strategyFilter === strategy ? ' active' : '') + '" data-etfstrat="' + escText(strategy) + '" style="font-size:11px;--tc:' + tone.fg + '">' + escText(strategy) + '</span>';
    });
    html += '</div>';
    return html;
  }

  function buildEtfListRowHtml(row) {
    var catColor = etfCatColor(row.category);
    var trendColor = row.trend_status === '多头' ? 'var(--stock-up)' : (row.trend_status === '空头' ? 'var(--stock-down)' : 'var(--cm-ink-500)');
    var strategyTone = etfStrategyTone(row.strategy_type);
    var setupTone = etfSetupTone(row.setup_state);
    var rotationText = row.rotation_score != null ? formatUtils.formatNumber(row.rotation_score, 1) + (row.rotation_bucket === 'leader' ? ' · 前排' : row.rotation_bucket === 'blacklist' ? ' · 回避' : '') : '—';
    var excessColor = row.backtest_excess_pct == null ? 'var(--cm-ink-500)' : (row.backtest_excess_pct >= 0 ? 'var(--cm-ok-500)' : 'var(--cm-bad-500)');
    return '<tr style="cursor:pointer" data-etf-code="' + escText(row.code) + '">' +
      '<td style="font-weight:600">' + escText(row.name) + '</td>' +
      '<td>' + xueqiuPillLink(row.code, row.code, true) + '</td>' +
      '<td><span style="padding:2px 8px;border-radius:999px;background:' + catColor + '14;color:' + catColor + ';font-size:11px;font-weight:600">' + escText(row.category || '其他') + '</span></td>' +
      '<td>' + etfPctCell(row.relative_strength_4w, false) + '</td>' +
      '<td>' + etfPctCell(row.relative_strength_12w, false) + '</td>' +
      '<td>' + escText(rotationText) + '</td>' +
      '<td>' + (row.backtest_return_pct != null ? (row.backtest_return_pct > 0 ? '+' : '') + Number(row.backtest_return_pct).toFixed(2) + '%' : '<span class="muted">-</span>') + '</td>' +
      '<td>' + (row.buy_hold_return_pct != null ? (row.buy_hold_return_pct > 0 ? '+' : '') + Number(row.buy_hold_return_pct).toFixed(2) + '%' : '<span class="muted">-</span>') + '</td>' +
      '<td style="color:' + excessColor + ';font-weight:700">' + (row.backtest_excess_pct != null ? (row.backtest_excess_pct > 0 ? '+' : '') + Number(row.backtest_excess_pct).toFixed(2) + '%' : '<span class="muted">-</span>') + '</td>' +
      '<td><span style="padding:2px 8px;border-radius:999px;background:' + setupTone.bg + ';color:' + setupTone.fg + ';font-size:11px;font-weight:600">' + escText(row.setup_state || '-') + '</span></td>' +
      '<td><span style="padding:2px 8px;border-radius:999px;background:' + strategyTone.bg + ';color:' + strategyTone.fg + ';font-size:11px;font-weight:600" title="' + escText(row.strategy_reason || '') + '">' + escText(row.strategy_type || '-') + '</span></td>' +
      '<td>' + (row.grid_step_pct != null ? escText(formatUtils.formatNumber(row.grid_step_pct, 1) + '%') : '<span class="muted">-</span>') + '</td>' +
      '<td style="color:' + trendColor + '">' + escText(row.trend_status || '-') + '</td>' +
      '</tr>';
  }

  function buildEtfListTableHtml(rows) {
    var safeRows = rows || [];
    var body = safeRows.map(buildEtfListRowHtml).join('');
    return '<table class="data-table"><thead><tr><th>名称</th><th>代码</th><th>分类</th><th>4周相强</th><th>12周相强</th><th>轮动分</th><th>网格收益</th><th>持有收益</th><th>超额</th><th>日线结构</th><th>策略类型</th><th>参考步长</th><th>趋势</th></tr></thead><tbody>' + body + '</tbody></table>';
  }

  function buildSortableCellMeta(cell) {
    if (!cell) return { kind: 'text', raw: '', value: '' };
    var datasetValue = cell.dataset.sortValue || cell.querySelector('[data-sort-value]')?.dataset.sortValue || '';
    var raw = String(datasetValue || cell.textContent || '').trim();
    if (!raw) return { kind: 'text', raw: '', value: '' };
    var dateMatch = raw.match(/(\d{4})[-\/]?(\d{2})[-\/]?(\d{2})/);
    if (dateMatch) {
      return { kind: 'date', raw: raw, value: Number(dateMatch[1] + dateMatch[2] + dateMatch[3]) };
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
          var ma = buildSortableCellMeta(ca), mb = buildSortableCellMeta(cb);
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

  function bindRowClicks(container, deepPanelId, onAnalyze) {
    if (!container) return;
    container.querySelectorAll('tr[data-etf-code]').forEach(function (row) {
      row.addEventListener('click', function () {
        var code = row.dataset.etfCode;
        var prev = container.querySelector('.etf-analysis-row');
        if (prev) prev.remove();
        var analysisRow = document.createElement('tr');
        analysisRow.className = 'etf-analysis-row';
        var td = document.createElement('td');
        td.colSpan = 13;
        td.id = deepPanelId || 'etfListAnalysisPanel';
        td.style.padding = '0';
        td.style.background = 'var(--bg-subtle)';
        analysisRow.appendChild(td);
        row.parentNode.insertBefore(analysisRow, row.nextSibling);
        if (typeof onAnalyze === 'function') {
          onAnalyze(code, td.id);
        } else if (global.loadEtfDeepAnalysis) {
          global.loadEtfDeepAnalysis(code, td.id);
        }
      });
    });
  }

  function mountEtfList(config) {
    config = config || {};
    var tableId = config.tableId || 'etfTableContainer';
    var filterId = config.filterId || 'etfCategoryFilter';
    var deepPanelId = config.deepPanelId || 'etfListAnalysisPanel';
    var state = config.state || {};
    var rows = Array.isArray(config.rows) ? config.rows.slice() : [];
    state.categoryFilter = state.categoryFilter || 'all';
    state.strategyFilter = state.strategyFilter || 'all';
    var tableEl = document.getElementById(tableId);
    var filterEl = document.getElementById(filterId);
    if (!tableEl) return;

    function render() {
      var filtered = applyEtfListFilters(rows, state);
      if (filterEl) {
        filterEl.innerHTML = buildEtfFilterBarHtml(state, rows);
        filterEl.querySelectorAll('[data-etfcat]').forEach(function (tag) {
          tag.addEventListener('click', function () {
            state.categoryFilter = tag.dataset.etfcat;
            render();
          });
        });
        filterEl.querySelectorAll('[data-etfstrat]').forEach(function (tag) {
          tag.addEventListener('click', function () {
            state.strategyFilter = tag.dataset.etfstrat;
            render();
          });
        });
      }
      tableEl.innerHTML = buildEtfListTableHtml(filtered);
      scheduleSortableTables(tableEl);
      bindRowClicks(tableEl, deepPanelId, config.onAnalyze);
    }

    render();
  }

  global.ETFListWidget = {
    collectEtfCategoryCounts: collectEtfCategoryCounts,
    sortEtfCategories: sortEtfCategories,
    applyEtfListFilters: applyEtfListFilters,
    buildEtfFilterBarHtml: buildEtfFilterBarHtml,
    buildEtfListRowHtml: buildEtfListRowHtml,
    buildEtfListTableHtml: buildEtfListTableHtml,
    mountEtfList: mountEtfList,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.ETFListWidget = global.ETFListWidget;
  }
})(typeof window !== 'undefined' ? window : this);
