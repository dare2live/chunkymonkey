(function (global) {
  'use strict';

  var endpoints = {
    overview: '/api/workbench/overview',
    research: '/api/workbench/research',
    champion: '/api/workbench/champion',
    dataSources: '/api/workbench/data-sources',
    pipelines: '/api/workbench/pipelines',
    features: '/api/workbench/features',
    delivery: '/api/workbench/delivery-readiness',
    paperSim: '/api/workbench/paper-sim/kpi-timeseries?limit=80',
    recommendations: '/api/workbench/recommendations',
    storage: '/api/workbench/storage'
  };

  var state = {
    activeTab: 'overview',
    loaded: {},
    loading: {},
    data: {},
    error: {}
  };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmt(value) {
    if (value == null || value === '') return '-';
    return esc(value);
  }

  function fmtNum(value) {
    if (value == null || value === '') return '-';
    var n = Number(value);
    if (!Number.isFinite(n)) return esc(value);
    return n.toLocaleString('zh-CN');
  }

  function fmtFloat(value, digits) {
    if (value == null || value === '') return '-';
    var n = Number(value);
    if (!Number.isFinite(n)) return esc(value);
    return n.toFixed(digits == null ? 3 : digits);
  }

  function fmtPct(value) {
    if (value == null || value === '') return '-';
    var n = Number(value);
    if (!Number.isFinite(n)) return esc(value);
    if (Math.abs(n) <= 1) n = n * 100;
    return n.toFixed(1) + '%';
  }

  function fmtDuration(value) {
    if (value == null || value === '') return '-';
    var n = Number(value);
    if (!Number.isFinite(n)) return esc(value);
    if (n >= 60) return (n / 60).toFixed(1) + 'm';
    return n.toFixed(2) + 's';
  }

  function timingSeconds(timing, key) {
    timing = timing || {};
    if (timing.seconds && timing.seconds[key + '_s'] != null) return timing.seconds[key + '_s'];
    if (timing.seconds && timing.seconds[key] != null) return timing.seconds[key];
    if (timing[key + '_s'] != null) return timing[key + '_s'];
    return timing[key];
  }

  function toneForStatus(status) {
    var s = String(status || '').toLowerCase();
    if (['ok', 'success', 'pass', 'passed', 'completed', 'champion', 'promoted', 'green'].indexOf(s) >= 0) return 'ok';
    if (['warn', 'warning', 'deferred', 'planned', 'running', 'disabled', 'shadow', 'yellow'].indexOf(s) >= 0) return 'warn';
    if (['bad', 'fail', 'failed', 'error', 'blocked', 'reject', 'rejected', 'red'].indexOf(s) >= 0) return 'bad';
    return 'info';
  }

  function pill(label, status) {
    var tone = toneForStatus(status || label);
    return '<span class="cm-pill cm-pill-' + tone + '"><span class="cm-dot cm-dot-' + tone + '"></span>' + esc(label || '-') + '</span>';
  }

  function statCard(label, value, sub, status) {
    return '<div class="stat-card wb-stat">' +
      '<div class="stat-value">' + fmt(value) + '</div>' +
      '<div class="stat-label">' + esc(label || '-') + '</div>' +
      (sub ? '<div class="wb-stat-sub">' + sub + '</div>' : '') +
      (status ? '<div class="wb-stat-status">' + pill(status, status) + '</div>' : '') +
      '</div>';
  }

  function renderStatusCounts(counts) {
    counts = counts || {};
    var keys = Object.keys(counts).sort();
    if (!keys.length) return '<span class="muted">-</span>';
    return keys.map(function (key) {
      return '<span class="wb-count-pill">' + esc(key) + ' <b>' + fmtNum(counts[key]) + '</b></span>';
    }).join('');
  }

  function renderEmpty(text) {
    return '<div class="muted" style="padding:14px 0">' + esc(text || '暂无数据') + '</div>';
  }

  function buildReadModelMeta(data) {
    var meta = (data && data.read_model) || {};
    if (!meta.source_mode) return null;
    var rows = meta.materialized_tables || [];
    var available = rows.filter(function (row) { return row && row.available; }).length;
    return {
      endpoint: meta.endpoint || '-',
      sourceMode: meta.source_mode || 'materialized_snapshot',
      recomputeLabel: meta.recompute_on_read ? 'read recompute' : 'snapshot read',
      recomputeTone: meta.recompute_on_read ? 'warn' : 'ok',
      availableCount: available,
      totalCount: rows.length,
      latestMaterializedAt: meta.latest_materialized_at || '-',
    };
  }

  function renderReadModelMeta(data) {
    var model = buildReadModelMeta(data);
    if (!model) return '';
    return '<section class="panel wb-panel wb-read-model-panel">' +
      '<div class="panel-head"><div><h3>物化结果</h3>' +
      '<div class="muted">endpoint: <code>' + esc(model.endpoint) + '</code></div></div>' +
      '<div>' + pill(model.recomputeLabel, model.recomputeTone) + '</div></div>' +
      '<div class="wb-count-row">' +
      '<span class="wb-count-pill">source <b>' + esc(model.sourceMode) + '</b></span>' +
      '<span class="wb-count-pill">tables <b>' + fmtNum(model.availableCount) + '/' + fmtNum(model.totalCount) + '</b></span>' +
      '<span class="wb-count-pill">latest <b>' + esc(model.latestMaterializedAt) + '</b></span>' +
      '</div>' +
      '<div class="muted">刷新视图只重新读取最后一次成功物化的 JSON；训练、验证和数据拉取由 pipeline/job 触发。</div>' +
      '</section>';
  }

  function renderShell(tab) {
    var root = document.getElementById('wb-overview-root');
    if (!root) return;
    root.innerHTML =
      '<div class="wb-toolbar">' +
      '<div class="view-tabs wb-tabs">' +
      tabButton('overview', '总览', tab) +
      tabButton('dataSources', '数据源', tab) +
      tabButton('pipelines', '管线', tab) +
      tabButton('features', '特征', tab) +
      tabButton('delivery', 'GO/NO-GO', tab) +
      tabButton('paperSim', 'KPI', tab) +
      tabButton('research', '研究', tab) +
      tabButton('champion', 'Champion', tab) +
      tabButton('recommendations', '推荐', tab) +
      tabButton('storage', '存储', tab) +
      '</div>' +
      '<button class="chip chip-outline" id="wb-refresh">刷新视图</button>' +
      '</div>' +
      '<div id="wb-tab-root" class="wb-tab-root"></div>';

    root.querySelectorAll('[data-wb-tab]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.activeTab = btn.getAttribute('data-wb-tab') || 'overview';
        show(false);
      });
    });
    var refresh = document.getElementById('wb-refresh');
    if (refresh) {
      refresh.addEventListener('click', function () {
        show(true);
      });
    }
  }

  function tabButton(tab, label, active) {
    return '<button class="tab-btn' + (tab === active ? ' active' : '') + '" data-wb-tab="' + esc(tab) + '">' + esc(label) + '</button>';
  }

  async function fetchTab(tab, force) {
    if (state.loading[tab]) return state.data[tab];
    if (state.loaded[tab] && !force) return state.data[tab];
    state.loading[tab] = true;
    state.error[tab] = null;
    try {
      var r = await fetch(endpoints[tab]);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      state.data[tab] = await r.json();
      state.loaded[tab] = true;
      return state.data[tab];
    } catch (error) {
      state.error[tab] = error;
      throw error;
    } finally {
      state.loading[tab] = false;
    }
  }

  function setBody(html) {
    var body = document.getElementById('wb-tab-root');
    if (body) body.innerHTML = html;
  }

  function renderLoading() {
    setBody('<div class="muted" style="padding:20px;text-align:center">加载中...</div>');
  }

  function renderError(error) {
    setBody('<section class="panel"><div style="color:var(--cm-bad-500)">加载失败: ' + esc(error && error.message || error) + '</div></section>');
  }

  function renderBlockers(blockers) {
    blockers = blockers || [];
    if (!blockers.length) return '<div class="wb-empty-ok">当前无阻塞项</div>';
    return blockers.map(function (item) {
      return '<div class="wb-blocker-row">' + pill(item.kind || 'blocker', 'bad') +
        '<span>' + fmtNum(item.count) + '</span></div>';
    }).join('');
  }

  function renderDriftTable(featureDrift) {
    var rows = (featureDrift && featureDrift.top) || [];
    if (!rows.length) return renderEmpty('暂无漂移根因结果');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>特征</th><th>Max PSI</th><th>次数</th><th>严重</th><th>建议</th><th>来源 run</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var psi = row.max_psi == null ? '-' : Number(row.max_psi).toFixed(3);
        var tone = Number(row.max_psi || 0) >= 0.5 ? 'bad' : Number(row.max_psi || 0) >= 0.25 ? 'warn' : 'ok';
        return '<tr>' +
          '<td><strong>' + esc(row.feature_name || '-') + '</strong></td>' +
          '<td>' + pill(psi, tone) + '</td>' +
          '<td>' + fmtNum(row.offender_count) + '</td>' +
          '<td>' + fmtNum(row.severe_count) + '</td>' +
          '<td>' + esc(row.recommendation || '-') + '</td>' +
          '<td><code>' + esc(row.source_run_id || '-') + '</code></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderOverview(data) {
    var latestManifest = data.latest_manifest || {};
    var research = data.research_schedule || {};
    var champion = data.champion || {};
    var champions = champion.champions || [];
    var championId = champions.length ? champions[0].model_id : '-';
    var storage = data.storage || {};

    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('最新完成交易日', data.latest_trading_day, 'calendar target', data.latest_trading_day ? 'ok' : 'missing') +
      statCard('最新运行', latestManifest.pipeline_name || '-', esc(latestManifest.run_id || ''), latestManifest.status) +
      statCard('Schema drift', fmtNum(data.schema_drift_count || 0), 'expected vs actual', data.schema_drift_count ? 'warn' : 'ok') +
      statCard('Champion', championId, renderStatusCounts(champion.counts), champions.length ? 'champion' : 'missing') +
      statCard('清理计划', storage.latest_run_id || '-', esc(storage.started_at || ''), storage.latest_status || 'none') +
      '</div>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>研究计划</h3><div class="muted">run_id: <code>' + esc(research.run_id || '-') + '</code></div></div></div>' +
      '<div class="wb-count-row">' + renderStatusCounts(research.status_counts) + '</div>' +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>当前阻塞</h3><div class="muted">schema / gate / runtime</div></div></div>' +
      renderBlockers(data.blockers) +
      '</section>' +
      '</div>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>特征漂移根因</h3><div class="muted">run_id: <code>' + esc((data.feature_drift || {}).run_id || '-') + '</code></div></div></div>' +
      renderDriftTable(data.feature_drift) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>最近运行清单</h3><div class="muted">as of: <code>' + esc(latestManifest.ended_at || latestManifest.started_at || '-') + '</code></div></div></div>' +
      '<div class="wb-run-line">' + pill(latestManifest.status || 'unknown', latestManifest.status) +
      '<span><code>' + esc(latestManifest.run_id || '-') + '</code></span>' +
      '<span>' + esc(latestManifest.pipeline_name || '-') + '</span>' +
      '<span>' + fmtDuration(latestManifest.duration_s) + '</span>' +
      (latestManifest.gate_result ? '<span>' + pill(latestManifest.gate_result, latestManifest.gate_result) + '</span>' : '') +
      '</div></section>'
    );
  }

  function toneForReturn(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return 'info';
    if (n >= 0.25) return 'ok';
    if (n >= 0) return 'warn';
    return 'bad';
  }

  function toneForDrawdown(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return 'info';
    if (n >= -0.20) return 'ok';
    if (n >= -0.25) return 'warn';
    return 'bad';
  }

  function toneForSharpe(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return 'info';
    if (n >= 2.0) return 'ok';
    if (n >= 1.5) return 'warn';
    return 'bad';
  }

  function summarizeParamDiff(diff) {
    if (!diff) return '-';
    if (typeof diff !== 'object') return String(diff).slice(0, 140);
    var parts = [];
    Object.keys(diff).sort().forEach(function (section) {
      var value = diff[section];
      if (!value || typeof value !== 'object') {
        parts.push(section);
        return;
      }
      Object.keys(value).sort().slice(0, 3).forEach(function (key) {
        var change = value[key];
        if (Array.isArray(change) && change.length === 2) {
          parts.push(section + '.' + key + ': ' + change[0] + ' → ' + change[1]);
        } else {
          parts.push(section + '.' + key);
        }
      });
    });
    return parts.length ? parts.join('; ') : '-';
  }

  function metricDelta(rows, key) {
    rows = rows || [];
    if (rows.length < 2) return null;
    var latest = Number(rows[0] && rows[0][key]);
    var previous = Number(rows[1] && rows[1][key]);
    if (!Number.isFinite(latest) || !Number.isFinite(previous)) return null;
    return latest - previous;
  }

  function renderMetricDelta(rows, key, asPct) {
    var delta = metricDelta(rows, key);
    if (delta == null) return '<span class="muted">no prior run</span>';
    var tone = delta >= 0 ? 'ok' : 'bad';
    var text = asPct ? fmtPct(delta) : fmtFloat(delta, 3);
    return '<span class="wb-kpi-delta wb-kpi-delta-' + tone + '">' + (delta >= 0 ? '+' : '') + text + ' vs prior</span>';
  }

  function renderPaperSimSparkline(rows, key) {
    rows = (rows || []).slice().reverse().filter(function (row) {
      return row && row[key] != null && Number.isFinite(Number(row[key]));
    });
    if (rows.length < 2) return '<div class="wb-kpi-sparkline wb-kpi-sparkline-empty">insufficient history</div>';
    var values = rows.map(function (row) { return Number(row[key]); });
    var min = Math.min.apply(Math, values);
    var max = Math.max.apply(Math, values);
    var spread = max - min || 1;
    var points = values.map(function (value, idx) {
      var x = rows.length === 1 ? 0 : (idx / (rows.length - 1)) * 100;
      var y = 100 - ((value - min) / spread) * 80 - 10;
      return x.toFixed(2) + ',' + y.toFixed(2);
    }).join(' ');
    return '<svg class="wb-kpi-sparkline" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">' +
      '<polyline points="' + points + '"></polyline>' +
      '</svg>';
  }

  function renderPaperSimKpiTable(rows) {
    if (!rows.length) return renderEmpty('暂无 paper_sim KPI 历史');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table wb-kpi-table">' +
      '<thead><tr><th>run</th><th>variant</th><th>ann</th><th>Sharpe</th><th>MaxDD</th><th>月胜</th><th>换手</th><th>判定</th><th>参数变化</th><th>lineage</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var pass = row.all_kpi_pass === true ? 'pass' : row.user_criteria_pass === true ? 'partial' : 'watch';
        var lineage = row.lineage_url ? '<a href="' + esc(row.lineage_url) + '" target="_blank" rel="noopener">open</a>' : '-';
        return '<tr>' +
          '<td><code>' + esc(row.sim_run_id || '-') + '</code><div class="muted">' + esc(row.built_at || '-') + '</div></td>' +
          '<td>' + esc(row.variant || '-') + '<div class="muted">' + esc((row.period_start || '-') + ' ~ ' + (row.period_end || '-')) + '</div></td>' +
          '<td>' + pill(fmtPct(row.annual_return), toneForReturn(row.annual_return)) + '</td>' +
          '<td>' + pill(fmtFloat(row.sharpe, 3), toneForSharpe(row.sharpe)) + '</td>' +
          '<td>' + pill(fmtPct(row.max_dd), toneForDrawdown(row.max_dd)) + '</td>' +
          '<td>' + fmtPct(row.monthly_win_rate) + '</td>' +
          '<td>' + fmtFloat(row.annual_turnover, 1) + 'x</td>' +
          '<td>' + pill(pass, pass === 'pass' ? 'ok' : pass === 'partial' ? 'warn' : 'info') + '</td>' +
          '<td><span class="muted">' + esc(summarizeParamDiff(row.param_diff)) + '</span><div class="muted">parent <code>' + esc(row.parent_sim_run_id || '-') + '</code></div></td>' +
          '<td>' + lineage + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderPaperSim(data) {
    var rows = data.data || [];
    var latest = data.latest || rows[0] || {};
    setBody(
      '<section class="panel wb-panel wb-kpi-hero">' +
      '<div class="panel-head"><div><h3>Paper Sim KPI Timeseries</h3>' +
      '<div class="muted">endpoint: <code>/api/workbench/paper-sim/kpi-timeseries</code></div></div>' +
      '<div>' + pill((data.meta || {}).source_mode || 'snapshot', 'ok') + '</div></div>' +
      '<div class="stats-row wb-stats-row">' +
      statCard('最新年化', fmtPct(latest.annual_return), renderMetricDelta(rows, 'annual_return', true), toneForReturn(latest.annual_return)) +
      statCard('最新 Sharpe', fmtFloat(latest.sharpe, 3), renderMetricDelta(rows, 'sharpe', false), toneForSharpe(latest.sharpe)) +
      statCard('最大回撤', fmtPct(latest.max_dd), '目标 ≥ -20%', toneForDrawdown(latest.max_dd)) +
      statCard('月胜率', fmtPct(latest.monthly_win_rate), '目标 ≥ 55%', Number(latest.monthly_win_rate) >= 0.55 ? 'ok' : 'warn') +
      statCard('历史 runs', fmtNum((data.meta || {}).row_count || rows.length), 'latest ' + esc(latest.built_at || '-'), rows.length ? 'ok' : 'missing') +
      '</div>' +
      '<div class="wb-kpi-spark-grid">' +
      '<div><div class="wb-kpi-spark-title">Annual Return</div>' + renderPaperSimSparkline(rows, 'annual_return') + '</div>' +
      '<div><div class="wb-kpi-spark-title">Sharpe</div>' + renderPaperSimSparkline(rows, 'sharpe') + '</div>' +
      '<div><div class="wb-kpi-spark-title">Max Drawdown</div>' + renderPaperSimSparkline(rows, 'max_dd') + '</div>' +
      '</div>' +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>历史 KPI 与参数血缘</h3><div class="muted">parent chain / param_diff / lineage_url</div></div></div>' +
      renderPaperSimKpiTable(rows) +
      '</section>'
    );
  }

  function renderCriteriaTable(rows) {
    rows = rows || [];
    if (!rows.length) return renderEmpty('暂无交付审计明细');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table wb-delivery-table">' +
      '<thead><tr><th>标准</th><th>当前</th><th>Verdict</th><th>关键证据</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var evidence = [];
        if (row.phase4_promote_action) evidence.push('phase4=' + row.phase4_promote_action);
        if (row.msaf_n_obs != null) evidence.push('n_obs=' + row.msaf_n_obs);
        if (row.msaf_sharpe != null) evidence.push('sharpe=' + fmtFloat(row.msaf_sharpe, 2));
        if (row.msaf_max_dd != null) evidence.push('max_dd=' + fmtPct(row.msaf_max_dd));
        if (row.policy) evidence.push('policy=' + row.policy);
        if (row.sources_wired) evidence.push('sources=' + Object.keys(row.sources_wired).filter(function (k) { return row.sources_wired[k]; }).join('+'));
        return '<tr>' +
          '<td><strong>' + esc(row.criterion || '-') + '</strong></td>' +
          '<td>' + pill(fmtPct((row.pct || 0) / 100), Number(row.pct || 0) >= 90 ? 'ok' : Number(row.pct || 0) >= 80 ? 'warn' : 'bad') + '</td>' +
          '<td>' + pill(row.verdict || '-', row.verdict || '-') + '</td>' +
          '<td><span class="muted">' + esc(evidence.join(' · ') || '-') + '</span></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function buildDeliveryModel(data) {
    data = data || {};
    var live = data.live_go_no_go || {};
    var challenger = data.challenger || {};
    var decision = challenger.decision || {};
    var challengerGate = challenger.gate || {};
    var liveGate = data.live_gate || {};
    var sources = data.sources || {};
    var institution = sources.institution_evaluation || {};
    var sourceAvailable = sources.available || {};
    var sourceWired = sources.wired || {};
    return {
      readyForDelivery: !!data.ready_for_delivery,
      verdict: data.verdict || 'NOT_READY',
      avgPct: Number(data.avg_pct || 0),
      liveGoNoGo: live,
      liveGate: liveGate,
      challenger: challenger,
      challengerDecision: decision,
      challengerGate: challengerGate,
      sources: sources,
      institution: institution,
      sourceAvailable: sourceAvailable,
      sourceWired: sourceWired,
      blockers: Array.isArray(data.blockers) ? data.blockers : [],
      criteria: Array.isArray(data.criteria) ? data.criteria : [],
    };
  }

  function renderDeliveryBlockers(rows) {
    rows = rows || [];
    if (!rows.length) return '<div class="wb-empty-ok">无剩余 milestone / reject reason</div>';
    return rows.map(function (row) {
      var tone = row.scope === 'milestone' ? 'warn' : 'bad';
      return '<div class="wb-delivery-blocker">' + pill(row.scope || 'blocker', tone) +
        '<span>' + esc(row.text || '-') + '</span></div>';
    }).join('');
  }

  function renderGateLine(label, gate) {
    gate = gate || {};
    var status = gate.passes === true ? 'pass' : gate.passes === false ? 'fail' : 'unknown';
    return '<div class="wb-delivery-gate-row">' +
      '<span>' + esc(label) + '</span>' +
      pill(status, status === 'pass' ? 'ok' : status === 'fail' ? 'bad' : 'warn') +
      '<code>' + esc(gate.reason || '-') + '</code>' +
      '</div>';
  }

  function renderDelivery(data) {
    var model = buildDeliveryModel(data);
    var live = model.liveGoNoGo;
    var challenger = model.challenger;
    var decision = model.challengerDecision;
    var challengerGate = model.challengerGate;
    var liveGate = model.liveGate;
    var institution = model.institution;
    var sourceAvailable = model.sourceAvailable;
    var sourceWired = model.sourceWired;
    setBody(
      '<section class="panel wb-panel wb-delivery-hero">' +
      '<div class="panel-head"><div><h3>GO/NO-GO Delivery Board</h3>' +
      '<div class="muted">endpoint: <code>/api/workbench/delivery-readiness</code></div></div>' +
      '<div>' + pill(model.verdict, model.readyForDelivery ? 'ok' : 'bad') + '</div></div>' +
      '<div class="stats-row wb-stats-row">' +
      statCard('交付均值', fmtPct(model.avgPct / 100), 'ready_for_delivery=' + esc(String(model.readyForDelivery)), model.readyForDelivery ? 'ok' : 'bad') +
      statCard('Live #6', fmtPct((live.pct || 0) / 100), live.ship_baseline_passed ? 'ship baseline PASS' : 'ship baseline BLOCK', live.ship_baseline_passed ? 'warn' : 'bad') +
      statCard('OOS obs', fmtNum(live.msaf_n_obs), 'target 30 / perfect 60', Number(live.msaf_n_obs || 0) >= 30 ? 'ok' : 'warn') +
      statCard('Sharpe', fmtFloat(live.msaf_sharpe, 2), 'target 2.0', Number(live.msaf_sharpe || 0) >= 2 ? 'ok' : 'warn') +
      statCard('MaxDD', fmtPct(live.msaf_max_dd), 'target ≥ -20%', Number(live.msaf_max_dd || -1) >= -0.20 ? 'ok' : 'warn') +
      '</div>' +
      '</section>' +

      '<section class="panel wb-panel wb-delivery-grid">' +
      '<div class="wb-delivery-card"><h3>Live Gate</h3>' +
      '<div class="muted">model <code>' + esc(liveGate.model_id || '-') + '</code> · action ' + esc(liveGate.promote_action || '-') + '</div>' +
      renderGateLine('PBO', liveGate.pbo) +
      renderGateLine('DSR', liveGate.dsr) +
      renderGateLine('Conservative', liveGate.conservative) +
      renderGateLine('IS-OOS', liveGate.is_oos) +
      '</div>' +
      '<div class="wb-delivery-card"><h3>Rejected Challenger</h3>' +
      '<div class="muted">model <code>' + esc(challenger.model_id || '-') + '</code></div>' +
      '<div>' + pill(decision.decision || 'unknown', decision.decision === 'hold_reject' ? 'bad' : 'warn') + '</div>' +
      renderGateLine('PBO', challengerGate.pbo) +
      '<div class="muted">obs20=' + fmtNum(challengerGate.n_obs_20d) + ' · obs5=' + fmtNum(challengerGate.n_obs_5d) + '</div>' +
      '</div>' +
      '<div class="wb-delivery-card"><h3>Source Wiring</h3>' +
      '<div class="wb-count-row">' +
      '<span class="wb-count-pill">institution available <b>' + esc(String(!!sourceAvailable.institution)) + '</b></span>' +
      '<span class="wb-count-pill">institution wired <b>' + esc(String(!!sourceWired.institution)) + '</b></span>' +
      '</div>' +
      '<div>' + pill(institution.production_decision || 'unknown', institution.production_decision === 'hold_reject' ? 'bad' : 'warn') + '</div>' +
      '<div class="muted">institution opt-in evaluation is kept out of production until retuned.</div>' +
      '</div>' +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Remaining Gaps</h3><div class="muted">milestones + reject reasons</div></div></div>' +
      renderDeliveryBlockers(model.blockers) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Delivery Criteria</h3><div class="muted">audit_delivery_readiness.py current evidence</div></div></div>' +
      renderCriteriaTable(model.criteria) +
      '</section>'
    );
  }

  function renderResearch(data) {
    var schedule = data.research_schedule || {};
    var tasks = schedule.tasks || [];
    var studies = data.model_stability || [];
    var ranker = data.ranker_profiles || [];
    var rankerPolicy = data.ranker_policy || {};
    var rankMatrixCache = data.rank_matrix_cache || {};
    var stabilityContext = data.stability_context || {};
    var stockHorizon = data.stock_horizon_profile || {};
    var shareholderPlanInitial = data.shareholder_plan_initial_feature_panel || {};
    var shareholderPlan = data.shareholder_plan_family_eval || {};
    var shareholderPlanWf = data.shareholder_plan_family_walkforward || {};
    var temporalSynergy = data.temporal_synergy || {};
    var industryPit = data.industry_pit || {};

    setBody(
      renderReadModelMeta(data) +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>研究队列</h3><div class="muted">run_id: <code>' + esc(schedule.run_id || '-') + '</code></div></div>' +
      '<div class="wb-count-row">' + renderStatusCounts(schedule.status_counts) + '</div></div>' +
      renderTaskTable(tasks) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>模型稳定性研究</h3><div class="muted">Optuna / walk-forward / drift gate</div></div></div>' +
      renderStudyTable(studies) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>稳定性上下文</h3><div class="muted">run_id: <code>' + esc(stabilityContext.run_id || '-') + '</code></div></div></div>' +
      renderStabilityContext(stabilityContext) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>个股持股周期画像</h3><div class="muted">run_id: <code>' + esc(stockHorizon.run_id || '-') + '</code> / baseline: <code>' + esc(stockHorizon.baseline_label || 'follow_net_return_60d') + '</code></div></div></div>' +
      renderStockHorizonProfile(stockHorizon) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>股东计划特征家族</h3><div class="muted">run_id: <code>' + esc(shareholderPlan.run_id || '-') + '</code></div></div></div>' +
      renderShareholderPlanInitialPanel(shareholderPlanInitial) +
      '<div style="margin-top:14px">' +
      renderShareholderPlanFamilyEval(shareholderPlan) +
      '</div>' +
      '<div style="margin-top:14px">' + renderShareholderPlanWalkforward(shareholderPlanWf) + '</div>' +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>行业 PIT 就绪度</h3><div class="muted">industry membership / strategy constraint gate</div></div></div>' +
      renderIndustryPitReadiness(industryPit) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>时序协同研究</h3><div class="muted">run_id: <code>' + esc(temporalSynergy.run_id || '-') + '</code></div></div></div>' +
      renderTemporalSynergy(temporalSynergy) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Ranker 扩展门禁</h3><div class="muted">run_id: <code>' + esc(rankerPolicy.run_id || '-') + '</code></div></div>' +
      (rankerPolicy.ranker_policy_deferred ? pill('deferred ' + rankerPolicy.ranker_policy_deferred, 'deferred') : pill('active', 'success')) +
      '</div>' +
      renderRankerPolicy(rankerPolicy) +
      '</section>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Ranker 性能</h3><div class="muted">cache / timing</div></div></div>' +
      renderRankerTable(ranker) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Rank Matrix Cache</h3><div class="muted">persisted rank windows</div></div></div>' +
      renderRankMatrixCache(rankMatrixCache) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>漂移处理优先级</h3><div class="muted">run_id: <code>' + esc(((data.feature_drift || {}).run_id) || '-') + '</code></div></div></div>' +
      renderDriftTable(data.feature_drift) +
      '</section>' +
      '</div>'
    );
  }

  function renderIndustryPitReadiness(data) {
    data = data || {};
    if (!data.run_id) return renderEmpty('暂无行业 PIT 质量证据');
    var eligible = !!data.pit_eligible;
    var blockers = data.blockers || [];
    return '<div class="wb-kv">' +
      '<div><span>状态</span><strong>' + pill(eligible ? 'eligible' : 'blocked', eligible ? 'ok' : 'bad') + '</strong></div>' +
      '<div><span>run</span><strong><code>' + esc(data.run_id || '-') + '</code></strong></div>' +
      '<div><span>信号表</span><strong><code>' + esc(data.signal_table || '-') + '</code></strong></div>' +
      '<div><span>信号窗口</span><strong>' + esc((data.min_signal_date || '-') + ' ~ ' + (data.max_signal_date || '-')) + '</strong></div>' +
      '<div><span>信号行数</span><strong>' + fmtNum(data.signal_row_count || 0) + '</strong></div>' +
      '<div><span>股票/日期</span><strong>' + fmtNum(data.signal_stock_count || 0) + ' / ' + fmtNum(data.signal_date_count || 0) + '</strong></div>' +
      '<div><span>PIT 行/股票</span><strong>' + fmtNum(data.pit_row_count || 0) + ' / ' + fmtNum(data.pit_stock_count || 0) + '</strong></div>' +
      '<div><span>历史快照</span><strong>' + fmtNum(data.history_snapshot_count || 0) + '</strong><div class="muted">' + esc((data.history_min_snapshot_date || '-') + ' ~ ' + (data.history_max_snapshot_date || '-')) + '</div></div>' +
      '<div><span>Observed/Fallback</span><strong>' + fmtNum(data.observed_pit_signal_rows || 0) + ' / ' + fmtNum(data.fallback_signal_rows || 0) + '</strong></div>' +
      '<div><span>Fallback 比例</span><strong>' + fmtPct(data.fallback_ratio) + '</strong></div>' +
      '<div><span>缺失 PIT</span><strong>' + fmtNum(data.missing_pit_rows || 0) + '</strong></div>' +
      '<div><span>阻塞</span><strong>' + (blockers.length ? blockers.map(function (b) { return pill(b, 'bad'); }).join('') : pill('none', 'ok')) + '</strong></div>' +
      '</div>';
  }

  function renderShareholderPlanInitialPanel(data) {
    data = data || {};
    var q = data.quality || {};
    if (!q.run_id) return renderEmpty('暂无初始事件研究面板');
    var timings = q.stage_timings || {};
    return '<details class="workbench-section" open>' +
      '<summary><span class="workbench-section-title">初始事件研究面板</span><span class="muted" style="font-size:11px;font-weight:400"><code>' + esc(q.run_id || '-') + '</code></span></summary>' +
      '<div class="wb-kv" style="margin-top:8px">' +
      '<div><span>面板行数</span><strong>' + fmtNum(q.panel_rows || 0) + '</strong></div>' +
      '<div><span>股票/日期</span><strong>' + fmtNum(q.stock_count || 0) + ' / ' + fmtNum(q.date_count || 0) + '</strong></div>' +
      '<div><span>事件激活</span><strong>' + fmtPct((q.active_pct || 0) / 100) + '</strong></div>' +
      '<div><span>匹配事件</span><strong>' + fmtNum(q.matched_event_rows || 0) + ' / ' + fmtNum(q.initial_event_rows || 0) + '</strong></div>' +
      '<div><span>交易日历不匹配</span><strong>' + fmtNum(q.calendar_mismatch_rows || 0) + '</strong></div>' +
      '<div><span>构建耗时</span><strong>' + fmtDuration(timings.total_s || 0) + '</strong></div>' +
      '<div><span>完整标签剔除</span><strong>' + fmtNum(q.dropped_incomplete_label_rows || 0) + '</strong></div>' +
      '<div><span>完整上下文剔除</span><strong>' + fmtNum(q.dropped_incomplete_context_rows || 0) + '</strong></div>' +
      '<div><span>窗口</span><strong>' + esc((q.min_date || '-') + ' ~ ' + (q.max_date || '-')) + '</strong></div>' +
      '<div><span>特征/标签</span><strong>' + fmtNum((q.initial_features || []).length + (q.context_features || []).length) + ' / ' + fmtNum((q.labels || []).length) + '</strong></div>' +
      '</div>' +
      '</details>';
  }

  function renderShareholderPlanFamilyEval(data) {
    data = data || {};
    var summary = data.summary || {};
    var families = data.family_summary || [];
    var top = data.top_effects || [];
    var paired = data.paired_advantages || [];
    if (!data.run_id && !families.length && !top.length && !paired.length) return renderEmpty('暂无股东计划特征家族评估');
    return '<div class="wb-kv">' +
      '<div><span>面板行数</span><strong>' + fmtNum(summary.panel_rows || 0) + '</strong></div>' +
      '<div><span>输出行数</span><strong>' + fmtNum(summary.row_count || 0) + '</strong></div>' +
      '<div><span>家族/特征</span><strong>' + fmtNum(summary.source_family_count || 0) + ' / ' + fmtNum(summary.feature_count || 0) + '</strong></div>' +
      '<div><span>标签</span><strong>' + fmtNum(summary.label_count || 0) + '</strong></div>' +
      '<div><span>built</span><strong>' + esc(summary.built_at || '-').slice(0, 19) + '</strong></div>' +
      '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderShareholderPlanFamilySummary(families) + '</div>' +
      '<div>' + renderShareholderPlanPairedTable(paired) + '</div>' +
      '</div>' +
      '<div style="margin-top:12px">' + renderShareholderPlanTopTable(top) + '</div>';
  }

  function renderShareholderPlanFamilySummary(rows) {
    if (!rows.length) return renderEmpty('暂无家族汇总');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>家族</th><th>标签</th><th>特征</th><th>激活</th><th>|RankIC|max</th><th>|Spread|max</th><th>正向</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.source_family || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtNum(row.feature_count || 0) + '</td>' +
          '<td>' + fmtPct((row.avg_nondefault_pct || 0) / 100) + '</td>' +
          '<td>' + fmtFloat(row.max_abs_rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.max_abs_spread) + '</td>' +
          '<td>' + fmtPct(row.positive_spread_share) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderShareholderPlanPairedTable(rows) {
    if (!rows.length) return renderEmpty('暂无家族对比');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>特征</th><th>标签</th><th>Initial Spread</th><th>Latest Spread</th><th>优势</th><th>激活率</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtPct(row.initial_spread) + '<div class="muted">IC ' + fmtFloat(row.initial_rank_ic, 4) + '</div></td>' +
          '<td>' + fmtPct(row.latest_spread) + '<div class="muted">IC ' + fmtFloat(row.latest_rank_ic, 4) + '</div></td>' +
          '<td>' + fmtPct(row.abs_spread_advantage) + '</td>' +
          '<td>I ' + fmtPct((row.initial_nondefault_pct || 0) / 100) + '<div class="muted">L ' + fmtPct((row.latest_nondefault_pct || 0) / 100) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderShareholderPlanTopTable(rows) {
    if (!rows.length) return renderEmpty('暂无股东计划效果');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>家族</th><th>特征</th><th>标签</th><th>Spread</th><th>RankIC</th><th>IC</th><th>激活</th><th>事件</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.source_family || '-') + '</code><div class="muted">' + esc(row.feature_purpose || '-') + '</div></td>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtPct(row.active_inactive_label_spread) + '<div class="muted">active ' + fmtPct(row.label_mean_when_active) + '</div></td>' +
          '<td>' + fmtFloat(row.rank_ic, 4) + '<div class="muted">days ' + fmtNum(row.daily_rank_ic_count || 0) + '</div></td>' +
          '<td>' + fmtFloat(row.ic, 4) + '</td>' +
          '<td>' + fmtPct((row.nondefault_pct || 0) / 100) + '</td>' +
          '<td>' + fmtNum(row.event_rows || 0) + '<div class="muted">stocks ' + fmtNum(row.distinct_event_stocks || 0) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderShareholderPlanWalkforward(data) {
    data = data || {};
    var summary = data.summary || {};
    var gates = data.gate_summary || [];
    var top = data.top_rows || [];
    var paired = data.paired_rows || [];
    if (!data.run_id && !gates.length && !top.length && !paired.length) return renderEmpty('暂无股东计划 walk-forward');
    return '<div class="panel-subhead"><h4>Walk-forward 候选验证</h4><span class="muted">run_id: <code>' + esc(data.run_id || '-') + '</code></span></div>' +
      '<div class="wb-kv">' +
      '<div><span>组合</span><strong>' + fmtNum(summary.row_count || 0) + '</strong></div>' +
      '<div><span>fold</span><strong>' + fmtNum(summary.fold_count || 0) + '</strong></div>' +
      '<div><span>标签</span><strong>' + fmtNum(summary.label_count || 0) + '</strong></div>' +
      '<div><span>状态</span><strong>' + Object.keys(summary.gate_status_counts || {}).map(function (key) { return esc(key) + ' ' + fmtNum((summary.gate_status_counts || {})[key] || 0); }).join(' / ') + '</strong></div>' +
      '<div><span>built</span><strong>' + esc(summary.built_at || '-').slice(0, 19) + '</strong></div>' +
      '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderShareholderPlanWalkforwardGateSummary(gates) + '</div>' +
      '<div>' + renderShareholderPlanWalkforwardPairs(paired) + '</div>' +
      '</div>' +
      '<div style="margin-top:12px">' + renderShareholderPlanWalkforwardTop(top) + '</div>';
  }

  function renderShareholderPlanWalkforwardGateSummary(rows) {
    if (!rows.length) return renderEmpty('暂无 walk-forward gate 汇总');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>家族</th><th>标签</th><th>Gate</th><th>特征</th><th>有效fold</th><th>SignalIC</th><th>L/S</th><th>DD</th><th>激活</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.source_family || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + pill(row.gate_status || 'unknown', row.gate_status) + '</td>' +
          '<td>' + fmtNum(row.feature_count || 0) + '</td>' +
          '<td>' + fmtNum(row.max_valid_fold_count || 0) + '</td>' +
          '<td>' + fmtFloat(row.max_signal_rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.max_long_short_spread) + '</td>' +
          '<td>' + fmtPct(row.worst_drawdown) + '</td>' +
          '<td>' + fmtPct((row.avg_active_pct || 0) / 100) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderShareholderPlanWalkforwardPairs(rows) {
    if (!rows.length) return renderEmpty('暂无 walk-forward 家族对比');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>特征</th><th>标签</th><th>Initial</th><th>Latest</th><th>L/S优势</th><th>有效fold</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtPct(row.initial_long_short_spread) + '<div class="muted">' + esc(row.initial_gate_status || '-') + ' / IC ' + fmtFloat(row.initial_signal_rank_ic, 4) + '</div></td>' +
          '<td>' + fmtPct(row.latest_long_short_spread) + '<div class="muted">' + esc(row.latest_gate_status || '-') + ' / IC ' + fmtFloat(row.latest_signal_rank_ic, 4) + '</div></td>' +
          '<td>' + fmtPct(row.long_short_advantage) + '</td>' +
          '<td>I ' + fmtNum(row.initial_valid_fold_count || 0) + '<div class="muted">L ' + fmtNum(row.latest_valid_fold_count || 0) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderShareholderPlanWalkforwardTop(rows) {
    if (!rows.length) return renderEmpty('暂无 walk-forward 明细');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>Gate</th><th>家族</th><th>特征</th><th>标签</th><th>有效fold</th><th>SignalIC</th><th>L/S</th><th>DD</th><th>激活</th><th>阻断</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var blockers = (row.blockers || []).join(', ');
        var cautions = (row.cautions || []).join(', ');
        return '<tr>' +
          '<td>' + pill(row.gate_status || 'unknown', row.gate_status) + '</td>' +
          '<td><code>' + esc(row.source_family || '-') + '</code></td>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtNum(row.valid_fold_count || 0) + ' / ' + fmtNum(row.fold_count || 0) + '</td>' +
          '<td>' + fmtFloat(row.avg_signal_adjusted_holdout_rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.avg_holdout_long_short_spread) + '<div class="muted">pos ' + fmtPct(row.positive_long_short_fold_share) + '</div></td>' +
          '<td>' + fmtPct(row.worst_holdout_long_short_max_drawdown) + '</td>' +
          '<td>' + fmtPct((row.avg_holdout_active_pct || 0) / 100) + '<div class="muted">min ' + fmtNum(row.min_holdout_active_rows || 0) + '</div></td>' +
          '<td><span class="muted">' + esc(blockers || cautions || '-') + '</span></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderDataSources(data) {
    var kline = data.kline || {};
    var primary = kline.primary || {};
    var validation = data.latest_feature_validation || {};
    var monitor = data.processing_monitor || {};
    var signalCache = data.today_signal_cache || {};
    var assetHealth = data.asset_health || {};
    var governanceCounts = assetHealth.governance_counts || {};
    var qualityCounts = governanceCounts.quality_gate_level || {};
    var tdxHealth = data.tdx_server_health || {};
    var tdxHealthSummary = tdxHealth.summary || {};
    var signalCacheTone = (signalCache.requires_refresh || signalCache.stale) ? 'warn' : (signalCache.status || 'unknown');
    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('交易日目标', data.calendar_target || '-', 'calendar gate', data.calendar_target ? 'ok' : 'missing') +
      statCard('K线主源', primary.source_name || '-', 'tier ' + fmt(primary.source_tier), kline.primary_is_tdxhub ? 'ok' : 'bad') +
      statCard('主源行数', fmtNum(primary.row_count || 0), esc(primary.last_data_date || '-'), primary.row_count ? 'ok' : 'missing') +
      statCard('TDX健康', fmtNum(tdxHealthSummary.healthy_count || 0), fmtNum(tdxHealthSummary.timeout_server_count || 0) + ' timeout servers', (tdxHealthSummary.timeout_server_count || 0) ? 'warn' : 'ok') +
      statCard('信号快照', signalCache.status || '-', fmtNum(signalCache.signal_count || 0) + ' signals', signalCacheTone) +
      statCard('Fallback', fmtNum(kline.fallback_active_count || 0), 'active sources', kline.fallback_active_count ? 'warn' : 'ok') +
      statCard('资产治理', fmtNum(((assetHealth.summary || {}).total) || 0), renderStatusCounts(qualityCounts), ((qualityCounts || {}).blocking || 0) ? 'ok' : 'info') +
      statCard('特征 fallback', fmtPct(validation.source_fallback_ratio), esc(validation.validation_id || '-'), validation.status || 'unknown') +
      statCard('清洗拒绝', fmtNum(monitor.total_rejected_rows || 0), fmtNum(monitor.run_count || 0) + ' tool runs', (monitor.total_rejected_rows || 0) ? 'warn' : 'ok') +
      '</div>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>源阻塞</h3><div class="muted">failures / primary contract</div></div></div>' +
      renderSourceBlockers(data.blockers || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>资产治理标签</h3><div class="muted">coverage / null / model eligibility</div></div></div>' +
      renderAssetGovernanceCounts(governanceCounts) +
      '</section>' +
      '</div>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>资产用途与质量契约</h3><div class="muted">should it be daily / can it enter model / how nulls are interpreted</div></div></div>' +
      renderAssetGovernanceTable(assetHealth.items || []) +
      '</section>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>今日信号快照</h3><div class="muted">materialized read model</div></div></div>' +
      renderTodaySignalCache(signalCache) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>特征源分布</h3><div class="muted">' + esc(validation.validated_at || '-') + '</div></div></div>' +
      renderSourceDistribution(validation.source_distribution || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>处理工具监控</h3><div class="muted">accepted / rejected</div></div></div>' +
      renderProcessingMonitorTable(monitor.recent_runs || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>拒绝原因</h3><div class="muted">cleaning contract</div></div></div>' +
      renderProcessingReasonTable(monitor.reason_counts || []) +
      '</section>' +
      '</div>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>数据源水位</h3><div class="muted">' + fmtNum(data.watermark_count || 0) + ' sources</div></div></div>' +
      renderWatermarkTable(data.watermarks || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>TDX K线服务器健康</h3><div class="muted">' + esc(tdxHealth.updated_at || '-') + '</div></div></div>' +
      renderTdxServerHealthTable(tdxHealth) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>TDX F10 能力矩阵</h3><div class="muted">parser / PIT source dates</div></div></div>' +
      renderTdxF10CapabilityTable(data.tdx_f10_capabilities || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>TDX F10 Source-Date Audit</h3><div class="muted">section semantics / source notice candidates</div></div></div>' +
      renderTdxF10SourceDateAudit(data.f10_source_date_audit || {}) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>TDX/F10 Source-Date DQ</h3><div class="muted">latest global gate evidence</div></div></div>' +
      renderTdxF10SourceDq(data.tdx_f10_source_dq || {}) +
      '</section>'
    );
  }

  function renderAssetGovernanceCounts(counts) {
    counts = counts || {};
    var parts = [
      ['coverage', counts.coverage_policy || {}],
      ['null', counts.null_policy || {}],
      ['model', counts.model_eligibility || {}],
      ['gate', counts.quality_gate_level || {}]
    ];
    return parts.map(function (part) {
      return '<div style="margin-bottom:10px"><div class="muted" style="margin-bottom:5px">' + esc(part[0]) + '</div>' +
        renderKeyValueCounts(part[1]) + '</div>';
    }).join('');
  }

  function renderAssetGovernanceTable(rows) {
    rows = (rows || []).filter(function (row) {
      return (row.frontend_visibility || 'governance_visible') !== 'hidden_internal';
    });
    if (!rows.length) return renderEmpty('暂无资产治理标签');
    rows = rows.slice().sort(function (a, b) {
      var gateRank = { blocking: 0, warning: 1, monitor_only: 2 };
      var ar = gateRank[a.quality_gate_level] == null ? 3 : gateRank[a.quality_gate_level];
      var br = gateRank[b.quality_gate_level] == null ? 3 : gateRank[b.quality_gate_level];
      if (ar !== br) return ar - br;
      return String(a.table_name || '').localeCompare(String(b.table_name || ''));
    }).slice(0, 80);
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>资产</th><th>健康</th><th>频率/覆盖</th><th>NULL/PIT</th><th>用途</th><th>入模/策略</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var gate = row.quality_gate_level || 'monitor_only';
        var gateTone = gate === 'blocking' ? 'bad' : gate === 'warning' ? 'warn' : 'info';
        return '<tr>' +
          '<td><code>' + esc(row.table_name || '-') + '</code><div class="muted">' + esc(row.layer || '-') + ' · ' + esc(row.asset_grain || '-') + '</div></td>' +
          '<td>' + pill(row.severity || 'unknown', row.severity || 'unknown') + '<div class="muted">' + esc(row.issue_summary || row.upstream_source || '-') + '</div></td>' +
          '<td>' + esc(row.asset_cadence || row.expected_freshness || '-') + '<div class="muted">' + esc(row.coverage_policy || '-') + '</div></td>' +
          '<td><code>' + esc(row.null_policy || '-') + '</code><div class="muted">' + esc(row.pit_policy || '-') + '</div></td>' +
          '<td>' + esc(row.intended_use || '-') + '<div class="muted">' + pill(gate, gateTone) + '</div></td>' +
          '<td>' + esc(row.model_eligibility || '-') + '<div class="muted">' + esc(row.strategy_eligibility || '-') + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderProcessingMonitorTable(rows) {
    if (!rows.length) return renderEmpty('暂无处理工具运行记录');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>tool</th><th>scope</th><th>source</th><th>status</th><th>accepted</th><th>rejected</th><th>output</th><th>ended</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + esc(row.tool_name || '-') + '<div class="muted"><code>' + esc(row.run_id || '-') + '</code></div></td>' +
          '<td>' + esc(row.scope || '-') + '</td>' +
          '<td>' + esc(row.source_name || '-') + '</td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + fmtNum(row.accepted_rows || 0) + '</td>' +
          '<td>' + fmtNum(row.rejected_rows || 0) + '</td>' +
          '<td>' + esc(row.output_table || '-') + '</td>' +
          '<td>' + esc(row.ended_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderProcessingReasonTable(rows) {
    if (!rows.length) return renderEmpty('暂无拒绝原因');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>reason</th><th>count</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr><td>' + esc(row.reason || '-') + '</td><td>' + fmtNum(row.count || 0) + '</td></tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTodaySignalCache(cache) {
    cache = cache || {};
    var step = cache.step || {};
    var status = cache.status || 'unknown';
    var statusTone = (cache.requires_refresh || cache.stale) ? 'warn' : status;
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>状态</th><th>信号</th><th>freshness</th><th>source max</th><th>built</th><th>刷新步骤</th></tr></thead><tbody>' +
      '<tr>' +
      '<td>' + pill(status, statusTone) + (cache.error ? '<div class="muted">' + esc(cache.error) + '</div>' : '') + '</td>' +
      '<td>' + fmtNum(cache.signal_count || 0) + '</td>' +
      '<td>' + (cache.freshness_days == null ? '-' : fmtNum(cache.freshness_days) + 'd') + '</td>' +
      '<td>' + esc(cache.source_max_notice_date || '-') + '<div class="muted">current ' + esc(cache.current_source_max_notice_date || '-') + '</div></td>' +
      '<td>' + esc(cache.built_at || '-') + '</td>' +
      '<td>' + pill(step.status || 'not-run', step.status || 'info') + '<div class="muted">' + fmtNum(step.records || 0) + ' rows · ' + esc(step.finished_at || step.started_at || '-') + '</div></td>' +
      '</tr></tbody></table></div>';
  }

  function renderSourceBlockers(rows) {
    if (!rows.length) return '<div class="wb-empty-ok">当前无源级阻塞</div>';
    return rows.map(function (row) {
      return '<div class="wb-blocker-row">' + pill(row.kind || 'source', 'bad') +
        '<span>' + esc(row.data_domain || '') + '</span>' +
        '<span>' + esc(row.source_name || '') + '</span>' +
        '<b>' + fmtNum(row.count || 1) + '</b></div>';
    }).join('');
  }

  function renderSourceDistribution(rows) {
    if (!rows.length) return renderEmpty('暂无特征源分布');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>source</th><th>tier</th><th>fallback</th><th>rows</th><th>pct</th><th>dates</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + esc(row.source_name || '-') + '</td>' +
          '<td>' + fmtNum(row.source_tier) + '</td>' +
          '<td>' + pill(row.is_fallback ? 'fallback' : 'primary', row.is_fallback ? 'warn' : 'ok') + '</td>' +
          '<td>' + fmtNum(row.rows) + '</td>' +
          '<td>' + fmtPct(row.row_pct) + '</td>' +
          '<td>' + fmtNum(row.dates) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderWatermarkTable(rows) {
    if (!rows.length) return renderEmpty('暂无数据源水位');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>domain</th><th>source</th><th>tier</th><th>last date</th><th>rows</th><th>fallback</th><th>fail</th><th>updated</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + esc(row.data_domain || '-') + '</td>' +
          '<td>' + esc(row.source_name || '-') + '<div class="muted">' + esc(row.parser_version || '-') + '</div></td>' +
          '<td>' + fmtNum(row.source_tier) + '</td>' +
          '<td>' + esc(row.last_data_date || '-') + '</td>' +
          '<td>' + fmtNum(row.row_count || 0) + '</td>' +
          '<td>' + pill(row.fallback_active ? 'active' : 'off', row.fallback_active ? 'warn' : 'ok') + '<div class="muted">' + esc(row.fallback_reason || '') + '</div></td>' +
          '<td>' + fmtNum(row.consecutive_failures || 0) + '</td>' +
          '<td>' + esc(row.updated_at || row.last_success_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTdxServerHealthTable(data) {
    data = data || {};
    var rows = data.servers || [];
    var summary = data.summary || {};
    if (!rows.length) return renderEmpty('暂无 TDX 服务器健康记录');
    var meta = '<div class="muted" style="margin-bottom:10px">' +
      'capabilities: ' + esc((summary.capabilities || []).join(', ') || '-') +
      ' · success ' + fmtNum(summary.total_successes || 0) +
      ' / fail ' + fmtNum(summary.total_failures || 0) +
      ' / timeout ' + fmtNum(summary.total_timeouts || 0) +
      '</div>';
    return meta + '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>server</th><th>capability</th><th>score</th><th>attempts</th><th>latency</th><th>last ok</th><th>last fail</th><th>run</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var failed = (row.failure_count || 0) > 0 || (row.timeout_count || 0) > 0;
        var status = failed ? 'warn' : ((row.success_count || 0) > 0 ? 'ok' : 'info');
        return '<tr>' +
          '<td><code>' + esc((row.server_host || '-') + ':' + (row.server_port || '-')) + '</code></td>' +
          '<td>' + esc(row.capability || '-') + '</td>' +
          '<td>' + pill(fmtFloat(row.health_score, 2), status) + '</td>' +
          '<td>ok ' + fmtNum(row.success_count || 0) + '<div class="muted">fail ' + fmtNum(row.failure_count || 0) + ' · timeout ' + fmtNum(row.timeout_count || 0) + '</div></td>' +
          '<td>' + fmtDuration(row.avg_success_elapsed_s) + '<div class="muted">last ' + fmtDuration(row.last_attempt_elapsed_s) + '</div></td>' +
          '<td>' + esc(row.last_success_at || '-') + '</td>' +
          '<td>' + esc(row.last_failure_at || '-') + '<div class="muted">' + esc(row.last_error_type || '') + '</div></td>' +
          '<td><code>' + esc(row.source_run_id || '-') + '</code></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTdxF10CapabilityTable(rows) {
    if (!rows.length) return renderEmpty('暂无 TDX F10 能力矩阵');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>模块</th><th>状态</th><th>覆盖</th><th>行数</th><th>source date</th><th>availability</th><th>parser</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + esc(row.module_name || '-') + '<div class="muted"><code>' + esc(row.module_id || '-') + '</code></div></td>' +
          '<td>' + pill(row.status || '-', row.status === 'ready' ? 'ok' : row.status) + '<div class="muted">' + esc(row.pit_risk || '-') + '</div></td>' +
          '<td>' + fmtNum(row.coverage_stock_count || 0) + '</td>' +
          '<td>' + fmtNum(row.row_count || 0) + '</td>' +
          '<td>' + esc(row.source_date_field || '-') + '<div class="muted">' + esc(row.latest_page_update_date || '-') + '</div></td>' +
          '<td>' + esc(row.availability_date_field || '-') + '<div class="muted">' + esc(row.latest_fetched_at || '-') + '</div></td>' +
          '<td>' + esc(row.parser || '-') + '<div class="muted">' + esc(row.parser_version || '-') + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTdxF10SourceDateAudit(audit) {
    audit = audit || {};
    var summary = audit.summary || {};
    var rows = audit.rows || [];
    if (!audit.run_id) return renderEmpty('暂无 F10 source-date section audit');
    var header = '<div class="wb-count-row">' +
      '<span class="wb-count-pill">raw <b>' + fmtNum(summary.raw_row_count || 0) + '</b></span>' +
      '<span class="wb-count-pill">audit rows <b>' + fmtNum(summary.audit_rows || 0) + '</b></span>' +
      '<span class="wb-count-pill">source notice <b>' + fmtNum(summary.source_notice_candidate_occurrences || 0) + '</b></span>' +
      '<span class="wb-count-pill">future source notice <b>' + fmtNum(summary.source_notice_candidate_future_occurrences || 0) + '</b></span>' +
      '<span class="wb-count-pill">future plan/event <b>' + fmtNum(summary.future_occurrence_count || 0) + '</b></span>' +
      '</div><div class="muted" style="margin-bottom:10px">run_id: <code>' + esc(audit.run_id || '-') + '</code> · built ' + esc(audit.built_at || '-') + '</div>';
    if (!rows.length) return header + renderEmpty('暂无审计明细');
    return header + '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>section</th><th>pattern</th><th>role</th><th>candidate</th><th>occurrences</th><th>future</th><th>date range</th><th>coverage</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var candidate = !!row.source_notice_candidate;
        var future = Number(row.future_occurrence_count || 0);
        var roleTone = candidate ? 'ok' : future ? 'warn' : 'info';
        return '<tr>' +
          '<td><code>' + esc(row.section_id || '-') + '</code><div class="muted">' + esc(row.section_name || '-') + '</div></td>' +
          '<td><code>' + esc(row.pattern_name || '-') + '</code></td>' +
          '<td>' + pill(row.date_role || '-', roleTone) + '</td>' +
          '<td>' + pill(candidate ? 'source_notice' : 'no', candidate ? 'ok' : 'info') + '</td>' +
          '<td>' + fmtNum(row.occurrence_count || 0) + '</td>' +
          '<td>' + pill(fmtNum(future), future ? 'warn' : 'ok') + '</td>' +
          '<td>' + esc((row.min_date || '-') + ' ~ ' + (row.max_date || '-')) + '</td>' +
          '<td>' + fmtNum(row.stock_count || 0) + ' stocks<div class="muted">' + fmtNum(row.raw_row_count || 0) + ' raw rows</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTdxF10SourceDq(dq) {
    dq = dq || {};
    var details = dq.details || [];
    if (!dq.gate_run_id) return renderEmpty('暂无 TDX/F10 source-date DQ gate');
    var header = '<div class="wb-count-row">' +
      '<span class="wb-count-pill">status <b>' + esc(dq.gate_status || '-') + '</b></span>' +
      '<span class="wb-count-pill">blockers <b>' + fmtNum(dq.blocker_count || 0) + '</b></span>' +
      '<span class="wb-count-pill">warnings <b>' + fmtNum(dq.warning_count || 0) + '</b></span>' +
      '<span class="wb-count-pill">checks <b>' + fmtNum(((dq.summary || {}).detail_count) || details.length) + '</b></span>' +
      '</div><div class="muted" style="margin-bottom:10px">gate: <code>' + esc(dq.gate_run_id || '-') + '</code> · ended ' + esc(dq.ended_at || '-') + '</div>';
    if (!details.length) return header + renderEmpty('暂无 DQ 明细');
    return header + '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>table</th><th>check</th><th>status</th><th>rows</th><th>violations</th><th>reason</th></tr></thead><tbody>' +
      details.map(function (row) {
        var status = row.status || '-';
        var tone = status === 'pass' ? 'ok' : status === 'fail' ? 'bad' : 'info';
        return '<tr>' +
          '<td><code>' + esc(row.table_name || '-') + '</code><div class="muted">' + esc(row.column_name || '-') + '</div></td>' +
          '<td>' + esc(row.check_name || '-') + '</td>' +
          '<td>' + pill(status, tone) + '<div class="muted">' + esc(row.severity || '-') + '</div></td>' +
          '<td>' + fmtNum(row.row_count || 0) + '</td>' +
          '<td>' + pill(fmtNum(row.violation_count || 0), Number(row.violation_count || 0) ? 'bad' : 'ok') + '</td>' +
          '<td>' + esc(row.reason || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderPipelines(data) {
    var recent = data.recent || [];
    var latest = recent[0] || {};
    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('最近运行', latest.pipeline_name || '-', esc(latest.run_id || '-'), latest.status || 'unknown') +
      statCard('最近状态', fmtNum(recent.length), renderStatusCounts(data.status_counts), latest.status || 'unknown') +
      statCard('最慢运行', ((data.slowest || [])[0] || {}).pipeline_name || '-', fmtDuration(((data.slowest || [])[0] || {}).duration_s), 'info') +
      statCard('阻塞运行', fmtNum((data.blockers || []).length), 'recent window', (data.blockers || []).length ? 'bad' : 'ok') +
      statCard('Gate', latest.gate_result || '-', 'latest gate', latest.gate_result || 'unknown') +
      '</div>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>最近运行</h3><div class="muted">manifest window</div></div></div>' +
      renderPipelineTable(recent) +
      '</section>' +
      '<div class="wb-grid">' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>最慢运行</h3><div class="muted">duration</div></div></div>' +
      renderPipelineTable(data.slowest || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>阻塞与失败</h3><div class="muted">status / blockers</div></div></div>' +
      renderPipelineTable(data.blockers || []) + '</section>' +
      '</div>'
    );
  }

  function renderPipelineTable(rows) {
    if (!rows.length) return renderEmpty('暂无运行记录');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>run</th><th>pipeline</th><th>status</th><th>duration</th><th>gate</th><th>blockers</th><th>started</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code></td>' +
          '<td>' + esc(row.pipeline_name || '-') + '</td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + fmtDuration(row.duration_s) + '</td>' +
          '<td>' + (row.gate_result ? pill(row.gate_result, row.gate_result) : '-') + '</td>' +
          '<td>' + fmtNum(row.blocker_count || 0) + '</td>' +
          '<td>' + esc(row.started_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderFeatures(data) {
    var registry = data.registry || {};
    var validation = data.latest_validation || {};
    var availability = data.availability_contract || {};
    var catalog = data.feature_catalog || {};
    var catalogSummary = catalog.summary || {};
    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('Registry', fmtNum(registry.feature_count || 0), 'model inputs ' + fmtNum(registry.model_input_count || 0), 'ok') +
      statCard('Labels', fmtNum(registry.label_count || 0), 'PIT lagged labels', 'info') +
      statCard('字段契约', fmtNum((availability.rows || []).length), esc(availability.source || 'registry'), (availability.rows || []).length ? 'ok' : 'missing') +
      statCard('字段资产', fmtNum(catalogSummary.total_features || 0), 'allowed ' + fmtNum(catalogSummary.allowed_features || 0), catalogSummary.critical_features ? 'warn' : 'ok') +
      statCard('Panel rows', fmtNum(validation.rows || 0), esc(validation.validation_id || '-'), validation.status || 'unknown') +
      statCard('Lineage', fmtPct(validation.source_lineage_coverage), 'source coverage', validation.source_lineage_coverage >= 1 ? 'ok' : 'warn') +
      statCard('Fallback ratio', fmtPct(validation.source_fallback_ratio), 'feature panel', validation.source_fallback_ratio ? 'warn' : 'ok') +
      '</div>' +
      '<div class="wb-grid">' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>特征组</h3><div class="muted">registry groups</div></div></div>' +
      renderKeyValueCounts(registry.group_counts || {}) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>字段角色</h3><div class="muted">model / auxiliary / risk / display</div></div></div>' +
      renderKeyValueCounts(availability.role_counts || {}) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>Panel Validation</h3><div class="muted">' + esc(validation.validated_at || '-') + '</div></div></div>' +
      renderValidationSummary(validation) + '</section>' +
      '</div>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>数据字段资产目录</h3><div class="muted">coverage / PIT risk / usage gate</div></div></div>' +
      renderFeatureCatalog(catalog) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>字段可用性契约</h3><div class="muted">source / cadence / density / null policy / usage role</div></div></div>' +
      renderFeatureAvailabilityContract(availability.rows || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>Feature Search Space</h3><div class="muted">selected / excluded</div></div></div>' +
      renderSearchSpaceTable(data.search_spaces || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>PIT 覆盖</h3><div class="muted">high / critical audit</div></div></div>' +
      renderPitCoverageTable(data.pit_coverage || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>漂移缓解候选</h3><div class="muted">rank / winsor / bucket panels</div></div></div>' +
      renderMitigationBuildTable(data.drift_mitigation_builds || []) + '</section>' +
      '<div class="wb-grid">' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>关联性 Top</h3><div class="muted">latest association run</div></div></div>' +
      renderAssociationTable(data.top_associations || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>漂移根因</h3><div class="muted">feature PSI</div></div></div>' +
      renderDriftTable(data.feature_drift) + '</section>' +
      '</div>'
    );
  }

  function renderFeatureAvailabilityContract(rows) {
    if (!rows.length) return renderEmpty('暂无字段契约');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>字段</th><th>角色</th><th>频率/密度</th><th>NULL 口径</th><th>入模</th><th>PIT lag</th><th>说明</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var modelTone = row.model_input && row.production_ready && row.enabled ? 'ok' : row.production_ready ? 'warn' : 'info';
        return '<tr>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code><div class="muted">' + esc(row.feature_group || '-') + '</div></td>' +
          '<td>' + esc(row.feature_role || '-') + '</td>' +
          '<td>' + esc(row.availability_cadence || '-') + '<div class="muted">' + esc(row.panel_density || '-') + '</div></td>' +
          '<td><code>' + esc(row.null_policy || '-') + '</code><div class="muted">' + esc(row.coverage_universe || '-') + '</div></td>' +
          '<td>' + pill(row.model_input ? 'model' : 'non-model', modelTone) + '<div class="muted">' + esc(row.expected_update_frequency || '-') + '</div></td>' +
          '<td>' + fmtNum(row.pit_release_lag_days || 0) + 'd</td>' +
          '<td>' + esc(row.notes || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderFeatureCatalog(catalog) {
    var rows = (catalog || {}).rows || [];
    var summary = (catalog || {}).summary || {};
    if (!rows.length) return renderEmpty('暂无字段资产目录');
    var header = '<div class="wb-count-row">' +
      '<span class="wb-count-pill">allowed <b>' + fmtNum(summary.allowed_features || 0) + '</b></span>' +
      '<span class="wb-count-pill">model <b>' + fmtNum(summary.model_input_features || 0) + '</b></span>' +
      '<span class="wb-count-pill">unknown <b>' + fmtNum(summary.unknown_features || 0) + '</b></span>' +
      '<span class="wb-count-pill">zero coverage <b>' + fmtNum(summary.zero_coverage_features || 0) + '</b></span>' +
      '<span class="wb-count-pill">critical <b>' + fmtNum(summary.critical_features || 0) + '</b></span>' +
      '<span class="wb-count-pill">high <b>' + fmtNum(summary.high_features || 0) + '</b></span>' +
      '</div>';
    return header + '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>字段</th><th>状态</th><th>PIT</th><th>覆盖</th><th>用途</th><th>source date</th><th>阻断原因</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var allowed = !!row.allowed_in_production_research;
        var riskTone = row.pit_risk_level === 'critical' ? 'bad' : row.pit_risk_level === 'high' ? 'warn' : 'ok';
        var gateTone = allowed ? 'ok' : row.production_blocking ? 'bad' : 'info';
        var roleText = row.model_input ? 'model' : row.label ? 'label' : row.candidate_only ? 'candidate' : 'non-model';
        return '<tr>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code><div class="muted">' + esc(row.feature_table || '-') + '</div></td>' +
          '<td>' + esc(row.registry_status || '-') + '<div class="muted">' + esc(row.feature_family || '-') + '</div></td>' +
          '<td>' + pill(row.pit_risk_level || '-', riskTone) + '<div class="muted">' + esc(row.join_policy || '-') + '</div></td>' +
          '<td>' + fmtPct((row.coverage_pct || 0) / 100) + '<div class="muted">' + fmtNum(row.non_null_rows || 0) + ' / ' + fmtNum(row.total_rows || 0) + '</div></td>' +
          '<td>' + pill(allowed ? 'allowed' : 'blocked', gateTone) + '<div class="muted">' + esc(roleText) + '</div></td>' +
          '<td>' + esc(row.source_available_date_column || '-') + '<div class="muted">' + esc(row.source_event_date_column || '-') + '</div></td>' +
          '<td>' + esc(row.reason_codes || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderKeyValueCounts(counts) {
    var keys = Object.keys(counts || {}).sort();
    if (!keys.length) return renderEmpty('暂无计数');
    return '<div class="wb-count-row">' + keys.map(function (key) {
      return '<span class="wb-count-pill">' + esc(key) + ' <b>' + fmtNum(counts[key]) + '</b></span>';
    }).join('') + '</div>';
  }

  function renderValidationSummary(row) {
    if (!row || !row.validation_id) return renderEmpty('暂无验证结果');
    return '<div class="wb-kv">' +
      '<span>status</span><b>' + pill(row.status || '-', row.status) + '</b>' +
      '<span>duplicate</span><b>' + fmtNum(row.duplicate_keys || 0) + '</b>' +
      '<span>close coverage</span><b>' + fmtPct(row.close_coverage) + '</b>' +
      '<span>blockers</span><b>' + fmtNum(row.blocker_count || 0) + '</b>' +
      '</div>';
  }

  function renderSearchSpaceTable(rows) {
    if (!rows.length) return renderEmpty('暂无 search space');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>run</th><th>panel</th><th>label</th><th>selected</th><th>excluded</th><th>groups</th><th>built</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code></td>' +
          '<td>' + esc(row.panel_table || '-') + '</td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtNum(row.selected_count || 0) + '</td>' +
          '<td>' + fmtNum(row.excluded_count || 0) + '</td>' +
          '<td>' + esc(Object.keys(row.group_counts || {}).join(', ') || '-') + '</td>' +
          '<td>' + esc(row.built_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderPitCoverageTable(rows) {
    if (!rows.length) return renderEmpty('暂无 PIT 覆盖摘要');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>audit</th><th>scope</th><th>columns</th><th>passed</th><th>failed</th><th>unknown</th><th>risk</th><th>at</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.audit_run_id || '-') + '</code><div class="muted">' + esc(row.feature_set_id || '-') + '</div></td>' +
          '<td>' + esc(row.audit_scope || '-') + '<div class="muted">' + esc(row.feature_table || '-') + '</div></td>' +
          '<td>' + fmtNum(row.total_columns || 0) + '<div class="muted">audited ' + fmtNum(row.audited_columns || 0) + '</div></td>' +
          '<td>' + fmtNum(row.passed_columns || 0) + '</td>' +
          '<td>' + fmtNum(row.failed_columns || 0) + '</td>' +
          '<td>' + pill(fmtNum(row.unknown_blocking_columns || 0), row.unknown_blocking_columns ? 'bad' : 'ok') + '</td>' +
          '<td>H ' + fmtNum(row.high_risk_columns || 0) + ' / C ' + fmtNum(row.critical_risk_columns || 0) + '</td>' +
          '<td>' + esc(row.audited_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderMitigationBuildTable(rows) {
    if (!rows.length) return renderEmpty('暂无漂移缓解候选');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>run</th><th>feature set</th><th>root cause</th><th>rows</th><th>dates</th><th>transformed</th><th>built</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var transformed = row.transformed_features || {};
        var transformedText = Object.keys(transformed).map(function (feature) {
          return feature + '→' + (transformed[feature] || []).length;
        }).join(', ');
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code></td>' +
          '<td><code>' + esc(row.output_feature_set_id || '-') + '</code><div class="muted">' + esc(row.model_selection_run_id || '-') + '</div></td>' +
          '<td><code>' + esc(row.root_cause_run_id || '-') + '</code></td>' +
          '<td>' + fmtNum(row.row_count || 0) + '</td>' +
          '<td>' + fmtNum(row.date_count || 0) + '<div class="muted">' + esc(row.min_date || '-') + ' → ' + esc(row.max_date || '-') + '</div></td>' +
          '<td>' + esc(transformedText || '-') + '</td>' +
          '<td>' + esc(row.built_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderAssociationTable(rows) {
    if (!rows.length) return renderEmpty('暂无关联性结果');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>feature</th><th>group</th><th>coverage</th><th>RankIC</th><th>spread</th><th>fallback</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><strong>' + esc(row.feature_name || '-') + '</strong></td>' +
          '<td>' + esc(row.feature_group || '-') + '</td>' +
          '<td>' + fmtPct(row.coverage_pct) + '</td>' +
          '<td>' + fmtFloat(row.rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.long_short_spread) + '</td>' +
          '<td>' + fmtPct(row.source_fallback_pct) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderStorage(data) {
    var retention = data.retention || {};
    var arch = data.architecture || {};
    var cleanup = data.architecture_cleanup || {};
    var manifest = data.latest_manifest || {};
    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('清理计划', manifest.latest_run_id || '-', esc(manifest.started_at || '-'), manifest.latest_status || 'none') +
      statCard('Retention candidates', fmtNum(retention.candidate_count || 0), retention.mode || '-', retention.candidate_count ? 'warn' : 'ok') +
      statCard('Protected models', fmtNum(retention.protected_model_count || 0), 'lifecycle/evidence', 'ok') +
      statCard('Arch cleanup', fmtNum((cleanup.candidates || []).length), renderStatusCounts(cleanup.status_counts), (cleanup.status_counts || {}).smoke_passed ? 'ok' : 'info') +
      statCard('Copy smoke', fmtNum((cleanup.smoke_counts || {}).passed || 0), 'view drops passed', (cleanup.smoke_counts || {}).passed ? 'ok' : 'none') +
      '</div>' +
      '<div class="wb-grid">' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>架构分类</h3><div class="muted">run_id: <code>' + esc(arch.run_id || '-') + '</code></div></div></div>' +
      renderKeyValueCounts(arch.classification_counts || {}) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>Retention</h3><div class="muted">dry-run only</div></div></div>' +
      (retention.error ? '<div style="color:var(--cm-bad-500)">' + esc(retention.error) + '</div>' : renderRetentionCandidates(retention.candidates || [])) +
      '</section>' +
      '</div>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>清理候选边界</h3><div class="muted">deprecated / shim / delete-after-tests</div></div></div>' +
      renderArchitectureCandidates(arch.cleanup_candidates || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>架构清理计划</h3><div class="muted">run_id: <code>' + esc(cleanup.run_id || '-') + '</code></div></div></div>' +
      renderArchitectureCleanupPlan(cleanup.candidates || []) + '</section>'
    );
  }

  function renderRetentionCandidates(rows) {
    if (!rows.length) return '<div class="wb-empty-ok">当前 dry-run 无可删候选</div>';
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>kind</th><th>table/path</th><th>rows</th><th>reason</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + esc(row.kind || '-') + '</td>' +
          '<td><code>' + esc(row.table || row.path || '-') + '</code></td>' +
          '<td>' + fmtNum(row.row_count || 0) + '</td>' +
          '<td>' + esc(row.reason || row.protection_reason || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderRecommendations(data) {
    var latest = data.latest_primary || {};
    var source = data.source_quality || {};
    var outcomes = data.outcomes || {};
    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('Primary TopK', fmtNum(latest.count || 0), esc(latest.snapshot_date || '-') + ' / ' + esc(latest.model_id || '-'), latest.count ? 'ok' : 'missing') +
      statCard('K线主源', source.kline_primary || '-', 'tdxhub primary', source.kline_primary_is_tdxhub ? 'ok' : 'bad') +
      statCard('Source fallback', fmtPct(source.source_fallback_ratio), esc(source.feature_validation_id || '-'), source.source_fallback_ratio ? 'warn' : 'ok') +
      statCard('Outcome rows', fmtNum(outcomes.count || 0), esc(outcomes.latest_outcome_known_at || '-'), outcomes.count ? 'ok' : 'missing') +
      statCard('5d hit', fmtPct(outcomes.hit_rate_5d), 'avg ' + fmtPct(outcomes.avg_ret_5d), outcomes.hit_rate_5d == null ? 'unknown' : 'info') +
      '</div>' +
      '<div class="wb-grid">' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>风险集中度</h3><div class="muted">industry / liquidity / overlap</div></div></div>' +
      renderRecommendationRisk(data.risk || []) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>Source Quality</h3><div class="muted">feature validation</div></div></div>' +
      renderRecommendationSource(source) + '</section>' +
      '</div>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>推荐清单</h3><div class="muted">model_id: <code>' + esc(latest.model_id || '-') + '</code></div></div></div>' +
      renderRecommendationTable(data.rows || []) + '</section>'
    );
  }

  function renderRecommendationSource(row) {
    return '<div class="wb-kv">' +
      '<span>kline</span><b>' + pill(row.kline_primary || '-', row.kline_primary_is_tdxhub ? 'ok' : 'bad') + '</b>' +
      '<span>lineage</span><b>' + fmtPct(row.source_lineage_coverage) + '</b>' +
      '<span>fallback</span><b>' + fmtPct(row.source_fallback_ratio) + '</b>' +
      '<span>validation</span><b>' + pill(row.feature_validation_status || '-', row.feature_validation_status) + '</b>' +
      '</div>';
  }

  function renderRecommendationRisk(rows) {
    if (!rows.length) return renderEmpty('暂无风险摘要');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>track</th><th>model</th><th>top</th><th>行业1</th><th>行业3</th><th>流动性P25</th><th>overlap</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + pill(row.track_id || '-', row.is_primary ? 'ok' : 'info') + '</td>' +
          '<td><code>' + esc(row.model_id || '-') + '</code></td>' +
          '<td>' + fmtNum(row.top_size) + '</td>' +
          '<td>' + esc(row.top1_industry || '-') + ' ' + fmtPct(row.top1_industry_share) + '</td>' +
          '<td>' + fmtPct(row.top3_industry_share) + '</td>' +
          '<td>' + fmtNum(row.top20_amount_ma20_p25) + '</td>' +
          '<td>' + fmtPct(row.overlap_with_primary) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderRecommendationTable(rows) {
    if (!rows.length) return renderEmpty('暂无推荐清单');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>Rank</th><th>股票</th><th>行业</th><th>周期</th><th>Score</th><th>Percentile</th><th>Regime</th><th>贡献分解</th><th>Top feature values</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + fmtNum(row.rank_in_date) + '</td>' +
          '<td><code>' + esc(row.stock_code || '-') + '</code><div class="muted">' + esc(row.stock_name || row.xueqiu_symbol || '-') + '</div></td>' +
          '<td>' + esc(row.tdx_l1_name || '-') + '<div class="muted">' + esc(row.tdx_l2_name || '') + '</div></td>' +
          '<td>' + fmtNum(row.selected_horizon_days || row.baseline_horizon_days || 60) + 'd ' + ((row.selected_horizon_days || 60) === 60 ? pill('baseline', 'ok') : '') +
            '<div class="muted">conf ' + fmtPct(row.selected_horizon_confidence) + '</div></td>' +
          '<td>' + fmtFloat(row.pred_score, 4) + '</td>' +
          '<td>' + fmtPct(row.percentile) + '</td>' +
          '<td>' + esc(row.regime_flag || '-') + '</td>' +
          '<td>' + renderTopFeatureContributions(row.top_feature_contributions || []) + '</td>' +
          '<td>' + renderTopFeatureValues(row.top_feature_values || [], row.top_features || []) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTopFeatureContributions(values) {
    if (!values.length) return '-';
    return '<div class="wb-feature-values">' + values.slice(0, 4).map(function (item) {
      var tone = item.direction === 'negative' ? 'warn' : (item.direction === 'positive' ? 'ok' : 'info');
      return '<span title="contribution ' + esc(fmtFloat(item.contribution, 4)) + '">' +
        '<code>' + esc(item.name || '-') + '</code> ' + pill(fmtFloat(item.contribution, 4), tone) +
      '</span>';
    }).join('') + '</div>';
  }

  function renderTopFeatureValues(values, fallbackNames) {
    if (!values.length) return esc((fallbackNames || []).join(', ') || '-');
    return '<div class="wb-feature-values">' + values.slice(0, 4).map(function (item) {
      var value = item.model_value != null ? item.model_value : item.raw_value;
      return '<span title="importance ' + esc(fmtFloat(item.importance, 3)) + '">' +
        '<code>' + esc(item.name || '-') + '</code> ' + esc(fmtFloat(value, 3)) +
      '</span>';
    }).join('') + '</div>';
  }

  function renderArchitectureCandidates(rows) {
    if (!rows.length) return '<div class="wb-empty-ok">当前无架构清理候选</div>';
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>path</th><th>area</th><th>class</th><th>notes</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.path || '-') + '</code></td>' +
          '<td>' + esc(row.module_area || '-') + '</td>' +
          '<td>' + pill(row.classification || '-', row.classification) + '</td>' +
          '<td>' + esc(row.notes || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderArchitectureCleanupPlan(rows) {
    if (!rows.length) return renderEmpty('暂无架构清理计划');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>path</th><th>action</th><th>status</th><th>smoke</th><th>reason</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.path || '-') + '</code><div class="muted">' + esc(row.classification || '-') + '</div></td>' +
          '<td>' + esc(row.action || '-') + '</td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + (row.smoke_status ? pill(row.smoke_status, row.smoke_status) : '-') + '</td>' +
          '<td>' + esc(row.reason || row.smoke_error || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTaskTable(rows) {
    if (!rows.length) return renderEmpty('暂无研究计划');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>任务</th><th>类型</th><th>优先级</th><th>状态</th><th>证据</th><th>原因</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var evidence = row.evidence_found ? (row.evidence_status || 'found') : (row.evidence_status || '-');
        return '<tr>' +
          '<td><code>' + esc(row.task_id || '-') + '</code></td>' +
          '<td>' + esc(row.task_type || '-') + '</td>' +
          '<td>' + fmtNum(row.priority) + '</td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + pill(evidence, evidence) + '<div class="muted"><code>' + esc(row.evidence_run_id || row.evidence_table || '-') + '</code></div></td>' +
          '<td>' + esc(row.reason || row.command_text || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderStudyTable(rows) {
    if (!rows.length) return renderEmpty('暂无稳定性研究结果');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>run</th><th>family</th><th>状态</th><th>目标</th><th>WF RankIC</th><th>漂移</th><th>DD</th><th>Trials</th><th>built_at</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var status = row.best_status || row.best_rejection_reason || 'unknown';
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.label_name || '-') + '</div></td>' +
          '<td>' + esc(row.model_family || '-') + '</td>' +
          '<td>' + pill(status, status) + '</td>' +
          '<td>' + fmtFloat(row.objective_score, 4) + '</td>' +
          '<td>' + fmtFloat(row.walkforward_avg_rank_ic, 4) + ' / ' + fmtFloat(row.walkforward_std_rank_ic, 4) + '</td>' +
          '<td>' + fmtFloat(row.walkforward_worst_feature_drift_psi, 3) + '</td>' +
          '<td>' + fmtPct(row.walkforward_worst_topk_drawdown) + '</td>' +
          '<td>' + fmtNum(row.trials) + ' / ' + fmtNum(row.study_total_trials) + '</td>' +
          '<td>' + esc(row.built_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalSynergy(data) {
    data = data || {};
    var quality = data.quality || {};
    var labels = data.label_summary || [];
    var relevance = data.top_relevance || [];
    var synergies = data.top_synergies || [];
    var selected = data.selected_interactions || [];
    var optuna = data.optuna_studies || [];
    var policies = data.policy_candidates || [];
    var gates = data.policy_gates || [];
    var mtmGates = data.policy_mtm_gates || [];
    var strategySweeps = data.policy_mtm_strategy_sweeps || [];
    var clusters = data.redundancy_clusters || [];
    var conditional = data.conditional_synergies || [];
    if (!quality.run_id && !labels.length && !relevance.length && !synergies.length && !selected.length && !optuna.length && !policies.length && !gates.length && !mtmGates.length && !strategySweeps.length && !clusters.length && !conditional.length) return renderEmpty('暂无时序协同研究');
    return '<div class="wb-kv">' +
      '<div><span>面板行数</span><strong>' + fmtNum(quality.panel_rows || 0) + '</strong></div>' +
      '<div><span>股票数</span><strong>' + fmtNum(quality.stock_count || 0) + '</strong></div>' +
      '<div><span>特征/标签</span><strong>' + fmtNum(quality.feature_count || 0) + ' / ' + fmtNum(quality.label_count || 0) + '</strong></div>' +
      '<div><span>未来源日期剔除</span><strong>' + fmtNum(quality.dropped_future_source_rows || 0) + '</strong></div>' +
      '<div><span>窗口</span><strong>' + esc((quality.min_signal_date || '-') + ' ~ ' + (quality.max_signal_date || '-')) + '</strong></div>' +
      '<div><span>PIT源日期</span><strong>' + esc(quality.source_date_filter_applied ? (quality.source_available_date_column || 'enabled') : 'panel-audited') + '</strong></div>' +
      '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderTemporalOptunaTable(optuna) + '</div>' +
      '<div>' + renderTemporalPolicyTable(policies) + '</div>' +
      '</div>' +
      '<div style="margin-top:12px">' + renderTemporalGateTable(gates) + '</div>' +
      '<div style="margin-top:12px">' + renderTemporalMtmGateTable(mtmGates) + '</div>' +
      '<div style="margin-top:12px">' + renderTemporalMtmStrategySweepTable(strategySweeps) + '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderTemporalLabelSummary(labels) + '</div>' +
      '<div>' + renderTemporalClusterTable(clusters) + '</div>' +
      '</div>' +
      '<div style="margin-top:12px">' + renderTemporalInteractionTable(selected, '入选交互') + '</div>' +
      '<div style="margin-top:12px">' + renderTemporalConditionalTable(conditional) + '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderTemporalRelevanceTable(relevance) + '</div>' +
      '<div>' + renderTemporalSynergyTable(synergies) + '</div>' +
      '</div>';
  }

  function renderTemporalClusterTable(rows) {
    if (!rows.length) return renderEmpty('暂无冗余聚类');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>Cluster</th><th>代表变量</th><th>成员</th><th>|corr|max</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.cluster_id || '-') + '</code><div class="muted">' + esc(row.run_id || '-') + '</div></td>' +
          '<td><code>' + esc(row.representative_feature || '-') + '</code></td>' +
          '<td>' + fmtNum(row.cluster_size || 0) + '<div class="muted">' + esc(row.members || '-') + '</div></td>' +
          '<td>' + fmtFloat(row.max_abs_corr_in_cluster, 4) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalConditionalTable(rows) {
    if (!rows.length) return renderEmpty('暂无条件交互');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>条件触发</th><th>标签</th><th>增量 Uplift</th><th>条件 Uplift</th><th>全局 Uplift</th><th>Score</th><th>Corr</th><th>状态</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.condition_feature || '-') + '</code><div class="muted">then <code>' + esc(row.response_feature || '-') + '</code></div></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.horizon_days || 0) + 'd / n ' + fmtNum(row.conditional_response_obs_count || 0) + '</div></td>' +
          '<td>' + fmtPct(row.incremental_uplift) + '</td>' +
          '<td>' + fmtPct(row.conditional_response_uplift) + '</td>' +
          '<td>' + fmtPct(row.response_uplift) + '</td>' +
          '<td>' + fmtFloat(row.interaction_score, 4) + '</td>' +
          '<td>' + fmtFloat(row.feature_corr, 3) + '</td>' +
          '<td>' + pill(row.selection_reason || '-', row.selected ? 'ok' : 'warn') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalOptunaTable(rows) {
    if (!rows.length) return renderEmpty('暂无 Optuna 组合搜索');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>Optuna run</th><th>标签</th><th>目标</th><th>Trials</th><th>特征</th><th>交互</th><th>组件</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var metrics = row.best_metrics || {};
        var selectedFeatures = row.selected_features || [];
        var selectedInteractions = row.selected_interactions || [];
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">best #' + fmtNum(row.best_trial_number || 0) + '</div></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + fmtFloat(row.objective_score, 4) + '</td>' +
          '<td>' + fmtNum(row.trials || 0) + ' / ' + fmtNum(row.study_total_trials || 0) + '</td>' +
          '<td>' + fmtNum(selectedFeatures.length) + '<div class="muted"><code>' + esc((selectedFeatures[0] || '-')) + '</code></div></td>' +
          '<td>' + fmtNum(selectedInteractions.length) + '</td>' +
          '<td>F ' + fmtFloat(metrics.feature_component, 3) + '<div class="muted">I ' + fmtFloat(metrics.interaction_component, 3) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalGateTable(rows) {
    if (!rows.length) return renderEmpty('暂无候选验证门禁');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>验证 run</th><th>标签</th><th>状态</th><th>RankIC</th><th>TopK excess</th><th>Cost adj</th><th>Turnover</th><th>DD</th><th>阻塞</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var blockers = row.blockers || [];
        var mode = row.gate_mode === 'strict_fold' ? 'strict fold' : 'metric';
        var prod = row.production_eligible ? 'production eligible' : 'not production';
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.candidate_run_id || '-') + '</div><div class="muted">' + esc(mode) + '</div></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.candidate_horizon_days || 0) + 'd / base ' + fmtNum(row.baseline_horizon_days || 60) + 'd</div></td>' +
          '<td>' + pill(row.validation_status || '-', row.validation_status || 'info') + '<div class="muted">' + esc(row.promotion_status || '-') + '</div><div class="muted">' + esc(prod) + '</div></td>' +
          '<td>' + fmtFloat(row.avg_rank_ic, 4) + '<div class="muted">std ' + fmtFloat(row.std_rank_ic, 4) + '</div></td>' +
          '<td>' + fmtPct(row.avg_top_excess_return) + '<div class="muted">worst ' + fmtPct(row.worst_top_excess_return) + '</div></td>' +
          '<td>' + fmtPct(row.avg_cost_adjusted_top_excess_return) + '<div class="muted">worst ' + fmtPct(row.worst_cost_adjusted_top_excess_return) + '</div></td>' +
          '<td>' + fmtPct(row.avg_turnover) + '<div class="muted">' + fmtFloat(row.transaction_cost_bps, 1) + ' bps / hit ' + fmtPct(row.avg_top_hit_rate) + '</div></td>' +
          '<td>' + fmtPct(row.worst_max_drawdown) + '</td>' +
          '<td>' + (blockers.length ? blockers.map(function (item) { return pill(item, 'warn'); }).join(' ') : pill('pass', 'ok')) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalMtmGateTable(rows) {
    if (!rows.length) return renderEmpty('暂无逐日盯市验证');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>MTM run</th><th>标签</th><th>入场口径</th><th>状态</th><th>收益/DD</th><th>路径</th><th>持仓</th><th>价格质量</th><th>阻塞</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var blockers = row.blockers || [];
        var prod = row.production_eligible ? 'production eligible' : 'not production';
        var quantile = row.top_quantile == null ? '-' : fmtPct(row.top_quantile);
        var topK = row.daily_top_k ? 'topK ' + fmtNum(row.daily_top_k) : 'all';
        var marketBits = [];
        if (row.min_market_hs300_ret_20d != null) marketBits.push('HS20 >= ' + fmtPct(row.min_market_hs300_ret_20d));
        if (row.min_market_hs300_ret_60d != null) marketBits.push('HS60 >= ' + fmtPct(row.min_market_hs300_ret_60d));
        var marketText = row.market_filter_enabled ? marketBits.join(' / ') : 'market all';
        var industryText = row.industry_constraints_requested
          ? 'industry L1 cap ' + fmtNum(row.max_industry_l1_active_positions || 0) + ' / PIT ' + (row.industry_pit_eligible ? 'ok' : 'blocked')
          : 'industry off';
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.candidate_run_id || '-') + '</div></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.candidate_horizon_days || 0) + 'd / base ' + fmtNum(row.baseline_horizon_days || 60) + 'd</div></td>' +
          '<td>' + esc(marketText || '-') + '<div class="muted">q ' + quantile + ' / ' + esc(topK) + '</div><div class="muted">' + esc(industryText) + '</div><div class="muted">filtered ' + fmtNum(row.market_filter_removed_signal_count || 0) + ' / topK ' + fmtNum(row.daily_top_k_filtered_count || 0) + '</div></td>' +
          '<td>' + pill(row.validation_status || '-', row.validation_status || 'info') + '<div class="muted">' + esc(row.promotion_status || '-') + '</div><div class="muted">' + esc(prod) + '</div></td>' +
          '<td>' + fmtPct(row.total_return) + '<div class="muted">ann ' + fmtPct(row.annualized_return) + ' / DD ' + fmtPct(row.max_drawdown) + '</div></td>' +
          '<td>' + fmtNum(row.date_count || 0) + 'd<div class="muted">sharpe ' + fmtFloat(row.sharpe, 3) + '</div></td>' +
          '<td>' + fmtNum(row.position_count || 0) + '<div class="muted">active ' + fmtNum(row.avg_active_positions || 0) + ' / hit ' + fmtPct(row.position_hit_rate) + '</div></td>' +
          '<td>TDX non ' + fmtNum(row.non_tdxhub_kline_count || 0) + '<div class="muted">missing ' + fmtNum(row.missing_path_price_count || 0) + ' / ff ' + fmtNum(row.forward_filled_path_price_count || 0) + '</div></td>' +
          '<td>' + (blockers.length ? blockers.map(function (item) { return pill(item, 'warn'); }).join(' ') : pill('pass', 'ok')) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalMtmStrategySweepTable(rows) {
    if (!rows.length) return renderEmpty('暂无 MTM 策略参数搜索');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>策略 sweep</th><th>变体</th><th>状态</th><th>目标</th><th>收益/DD</th><th>信号/持仓</th><th>过滤</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var blockers = row.blockers || [];
        var marketBits = [];
        if (row.min_market_hs300_ret_20d != null) marketBits.push('HS20 >= ' + fmtPct(row.min_market_hs300_ret_20d));
        if (row.min_market_hs300_ret_60d != null) marketBits.push('HS60 >= ' + fmtPct(row.min_market_hs300_ret_60d));
        var marketText = marketBits.length ? marketBits.join(' / ') : 'market all';
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.candidate_run_id || '-') + '</div></td>' +
          '<td>' + esc(row.variant_id || '-') + '<div class="muted">' + esc(marketText) + '</div></td>' +
          '<td>' + pill(row.validation_status || '-', row.validation_status || 'info') + '<div class="muted">' + (blockers.length ? blockers.map(function (item) { return esc(item); }).join(', ') : 'pass') + '</div></td>' +
          '<td>' + fmtFloat(row.objective_score, 4) + '<div class="muted">q ' + fmtPct(row.top_quantile) + '</div></td>' +
          '<td>' + fmtPct(row.total_return) + '<div class="muted">ann ' + fmtPct(row.annualized_return) + ' / DD ' + fmtPct(row.max_drawdown) + '</div><div class="muted">sharpe ' + fmtFloat(row.sharpe, 3) + '</div></td>' +
          '<td>' + fmtNum(row.signal_count || 0) + '<div class="muted">pos ' + fmtNum(row.position_count || 0) + ' / active ' + fmtNum(row.avg_active_positions || 0) + '</div></td>' +
          '<td>market ' + fmtNum(row.market_filter_removed_signal_count || 0) + '<div class="muted">topK ' + fmtNum(row.daily_top_k_filtered_count || 0) + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalPolicyTable(rows) {
    if (!rows.length) return renderEmpty('暂无候选策略');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>候选</th><th>标签</th><th>Gate</th><th>目标</th><th>特征</th><th>交互</th><th>built_at</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '</td>' +
          '<td>' + pill(row.gate_status || '-', row.gate_status || 'info') + '</td>' +
          '<td>' + fmtFloat(row.objective_score, 4) + '</td>' +
          '<td>' + fmtNum(row.selected_count || 0) + '</td>' +
          '<td>' + fmtNum(row.selected_interaction_count || 0) + '</td>' +
          '<td>' + esc(row.built_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalLabelSummary(rows) {
    if (!rows.length) return renderEmpty('暂无标签汇总');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>标签</th><th>特征数</th><th>覆盖</th><th>|RankIC|max</th><th>Spread max</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.label_name || '-') + '</code></td>' +
          '<td>' + fmtNum(row.feature_count || 0) + '</td>' +
          '<td>' + fmtPct((row.avg_coverage_pct || 0) / 100) + '</td>' +
          '<td>' + fmtFloat(row.max_abs_rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.max_directional_spread) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalRelevanceTable(rows) {
    if (!rows.length) return renderEmpty('暂无单变量相关性');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>变量</th><th>标签</th><th>RankIC</th><th>Spread</th><th>稳定</th><th>覆盖</th><th>日数</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.horizon_days || 0) + 'd</div></td>' +
          '<td>' + fmtFloat(row.rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.directional_spread) + '<div class="muted">LS ' + fmtPct(row.long_short_spread) + '</div></td>' +
          '<td>' + fmtFloat(row.stability_score, 4) + '</td>' +
          '<td>' + fmtPct((row.coverage_pct || 0) / 100) + '</td>' +
          '<td>' + fmtNum(row.daily_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalSynergyTable(rows) {
    if (!rows.length) return renderEmpty('暂无组合协同');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>组合</th><th>标签</th><th>Uplift</th><th>Score</th><th>Joint</th><th>Standalone</th><th>Corr</th><th>观测</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.feature_a || '-') + '</code><div class="muted">+ <code>' + esc(row.feature_b || '-') + '</code></div></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.horizon_days || 0) + 'd</div></td>' +
          '<td>' + fmtPct(row.joint_uplift) + '</td>' +
          '<td>' + fmtFloat(row.interaction_score, 4) + '</td>' +
          '<td>' + fmtPct(row.joint_active_label_mean) + '</td>' +
          '<td>' + fmtPct(row.best_standalone_label_mean) + '</td>' +
          '<td>' + fmtFloat(row.feature_corr, 3) + '</td>' +
          '<td>' + fmtNum(row.joint_obs_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTemporalInteractionTable(rows, title) {
    if (!rows.length) return renderEmpty('暂无入选交互');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>' + esc(title || '交互') + '</th><th>标签</th><th>原因</th><th>Uplift</th><th>Score</th><th>观测</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.feature_a || '-') + '</code><div class="muted">+ <code>' + esc(row.feature_b || '-') + '</code></div></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.horizon_days || 0) + 'd</div></td>' +
          '<td>' + pill(row.selection_reason || '-', row.selected ? 'ok' : 'warn') + '</td>' +
          '<td>' + fmtPct(row.joint_uplift) + '</td>' +
          '<td>' + fmtFloat(row.interaction_score, 4) + '</td>' +
          '<td>' + fmtNum(row.joint_obs_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderStockHorizonProfile(data) {
    data = data || {};
    var comparison = data.horizon_comparison || [];
    var distribution = data.horizon_distribution || [];
    var selectedDistribution = data.selected_horizon_distribution || [];
    var selections = data.horizon_selection || data.selected_stocks || [];
    var stocks = data.best_stocks || [];
    var effects = data.top_effects || [];
    var effectDetails = data.feature_effects_by_horizon || [];
    if (!comparison.length && !distribution.length && !selectedDistribution.length && !selections.length && !stocks.length && !effects.length && !effectDetails.length) return renderEmpty('暂无个股周期画像');
    return '<div class="wb-kv">' +
      '<div><span>画像行数</span><strong>' + fmtNum(data.profile_count || 0) + '</strong></div>' +
      '<div><span>覆盖股票</span><strong>' + fmtNum(data.best_count || 0) + '</strong></div>' +
      '<div><span>选择行数</span><strong>' + fmtNum(data.selection_count || 0) + '</strong></div>' +
      '<div><span>变量效应</span><strong>' + fmtNum(data.effect_count || 0) + '</strong></div>' +
      '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderHorizonComparisonTable(comparison) + '</div>' +
      '<div>' + renderHorizonDistributionTable(distribution) + '</div>' +
      '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderSelectedHorizonDistributionTable(selectedDistribution) + '</div>' +
      '<div>' + renderSelectedStockHorizonTable(selections) + '</div>' +
      '</div>' +
      '<div style="margin-top:12px">' + renderFeatureEffectByHorizonTable(effectDetails) + '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderHorizonEffectTable(effects) + '</div>' +
      '<div>' + renderBestStockHorizonTable(stocks) + '</div>' +
      '</div>';
  }

  function renderHorizonComparisonTable(rows) {
    if (!rows.length) return renderEmpty('暂无周期对比');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>周期对比</th><th>股票数</th><th>均值收益</th><th>路径收益</th><th>最大回撤</th><th>胜率</th><th>波动</th><th>路径观测</th><th>评分</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + fmtNum(row.horizon_days) + 'd ' + (row.is_baseline ? pill('baseline', 'ok') : '') + '<div class="muted">' + esc(row.label_name || '-') + '</div></td>' +
          '<td>' + fmtNum(row.stock_count || 0) + '</td>' +
          '<td>' + fmtPct(row.avg_return) + '</td>' +
          '<td>' + fmtPct(row.avg_compounded_return) + '<div class="muted">med ' + fmtPct(row.median_compounded_return) + '</div></td>' +
          '<td>' + fmtPct(row.avg_max_drawdown) + '<div class="muted">med ' + fmtPct(row.median_max_drawdown) + '</div></td>' +
          '<td>' + fmtPct(row.avg_win_rate) + '</td>' +
          '<td>' + fmtPct(row.avg_volatility) + '</td>' +
          '<td>' + fmtFloat(row.avg_path_obs_count, 1) + '</td>' +
          '<td>' + fmtFloat(row.avg_horizon_score, 4) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderHorizonDistributionTable(rows) {
    if (!rows.length) return renderEmpty('暂无周期分布');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>最佳周期分布</th><th>股票数</th><th>均值收益</th><th>路径收益</th><th>最大回撤</th><th>胜率</th><th>波动</th><th>评分</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + fmtNum(row.horizon_days) + 'd ' + (row.is_baseline ? pill('baseline', 'ok') : '') + '<div class="muted">' + esc(row.label_name || '-') + '</div></td>' +
          '<td>' + fmtNum(row.stock_count || 0) + '</td>' +
          '<td>' + fmtPct(row.avg_return) + '</td>' +
          '<td>' + fmtPct(row.avg_compounded_return) + '<div class="muted">med ' + fmtPct(row.median_compounded_return) + '</div></td>' +
          '<td>' + fmtPct(row.avg_max_drawdown) + '<div class="muted">med ' + fmtPct(row.median_max_drawdown) + '</div></td>' +
          '<td>' + fmtPct(row.avg_win_rate) + '</td>' +
          '<td>' + fmtPct(row.avg_volatility) + '</td>' +
          '<td>' + fmtFloat(row.avg_horizon_score, 4) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderFeatureEffectByHorizonTable(rows) {
    if (!rows.length) return renderEmpty('暂无逐项变量影响');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>周期</th><th>变量</th><th>主方向</th><th>覆盖股票</th><th>|corr|</th><th>corr</th><th>正相关占比</th><th>观测</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var direction = row.dominant_direction || '-';
        return '<tr>' +
          '<td>' + fmtNum(row.horizon_days) + 'd<div class="muted">' + esc(row.label_name || '-') + '</div></td>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + pill(direction, direction === 'negative' ? 'warn' : direction) + '</td>' +
          '<td>' + fmtNum(row.stock_count || 0) + '</td>' +
          '<td>' + fmtFloat(row.avg_abs_corr, 4) + '</td>' +
          '<td>' + fmtFloat(row.avg_corr, 4) + '</td>' +
          '<td>' + fmtPct(row.positive_share) + '</td>' +
          '<td>' + fmtFloat(row.avg_obs_count, 1) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderSelectedHorizonDistributionTable(rows) {
    if (!rows.length) return renderEmpty('暂无60d基线选择分布');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>最终选择</th><th>股票数</th><th>Gate</th><th>置信度</th><th>评分优势</th><th>收益优势</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + fmtNum(row.selected_horizon_days) + 'd ' + (row.is_baseline ? pill('baseline', 'ok') : '') + '<div class="muted">' + esc(row.selected_label || '-') + '</div></td>' +
          '<td>' + fmtNum(row.stock_count || 0) + '</td>' +
          '<td>' + pill(row.gate_status || '-', row.gate_status === 'selected' ? 'ok' : 'info') + '</td>' +
          '<td>' + fmtPct(row.avg_confidence) + '</td>' +
          '<td>' + fmtFloat(row.avg_score_advantage, 4) + '</td>' +
          '<td>' + fmtPct(row.avg_return_advantage) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderSelectedStockHorizonTable(rows) {
    if (!rows.length) return renderEmpty('暂无个股60d选择');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>股票</th><th>选择周期</th><th>置信度</th><th>评分优势</th><th>收益优势</th><th>回撤</th><th>Top 变量影响</th><th>Gate</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var baseline = row.selected_label === row.baseline_label;
        return '<tr>' +
          '<td><code>' + esc(row.stock_code || '-') + '</code></td>' +
          '<td>' + fmtNum(row.selected_horizon_days) + 'd ' + (baseline ? pill('baseline', 'ok') : '') + '<div class="muted">' + esc(row.selected_label || '-') + '</div></td>' +
          '<td>' + fmtPct(row.selected_horizon_confidence) + '</td>' +
          '<td>' + fmtFloat(row.score_advantage, 4) + '</td>' +
          '<td>' + fmtPct(row.avg_return_advantage) + '</td>' +
          '<td>' + fmtPct(row.selected_max_drawdown) + '<div class="muted">60d ' + fmtPct(row.baseline_max_drawdown) + '</div></td>' +
          '<td>' + renderSelectedFeatureEffects(row.top_feature_effects || []) + '</td>' +
          '<td>' + pill(row.gate_status || '-', row.gate_status === 'selected' ? 'ok' : 'info') + '<div class="muted">' + esc(row.fallback_reason || '') + '</div></td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderSelectedFeatureEffects(rows) {
    if (!rows.length) return '-';
    return '<div class="wb-feature-values">' + rows.slice(0, 3).map(function (row) {
      var direction = row.effect_direction || 'flat';
      return '<span title="' + esc(direction) + ' corr ' + esc(fmtFloat(row.corr, 4)) + '">' +
        '<code>' + esc(row.feature_name || '-') + '</code> ' +
        pill(fmtFloat(row.corr, 3), direction === 'negative' ? 'warn' : 'ok') +
      '</span>';
    }).join('') + '</div>';
  }

  function renderHorizonEffectTable(rows) {
    if (!rows.length) return renderEmpty('暂无变量效应');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>最佳周期 Top 变量</th><th>方向</th><th>股票数</th><th>|corr|</th><th>corr</th><th>周期范围</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var direction = row.effect_direction || '-';
        return '<tr>' +
          '<td><code>' + esc(row.feature_name || '-') + '</code></td>' +
          '<td>' + pill(direction, direction === 'negative' ? 'warn' : 'ok') + '</td>' +
          '<td>' + fmtNum(row.stock_count || 0) + '</td>' +
          '<td>' + fmtFloat(row.avg_abs_corr, 4) + '</td>' +
          '<td>' + fmtFloat(row.avg_corr, 4) + '</td>' +
          '<td>' + fmtNum(row.min_horizon_days) + 'd ~ ' + fmtNum(row.max_horizon_days) + 'd</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderBestStockHorizonTable(rows) {
    if (!rows.length) return renderEmpty('暂无样本股票');
    return '<div class="wb-table-wrap" style="margin-top:12px"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>样本股票</th><th>最佳周期</th><th>评分</th><th>均值收益</th><th>路径收益</th><th>最大回撤</th><th>胜率</th><th>波动</th><th>路径观测</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.stock_code || '-') + '</code></td>' +
          '<td>' + fmtNum(row.horizon_days) + 'd ' + (row.is_baseline ? pill('baseline', 'ok') : '') + '<div class="muted">' + esc(row.label_name || '-') + '</div></td>' +
          '<td>' + fmtFloat(row.horizon_score, 4) + '</td>' +
          '<td>' + fmtPct(row.avg_return) + '</td>' +
          '<td>' + fmtPct(row.compounded_return) + '</td>' +
          '<td>' + fmtPct(row.max_drawdown) + '</td>' +
          '<td>' + fmtPct(row.win_rate) + '</td>' +
          '<td>' + fmtPct(row.volatility) + '</td>' +
          '<td>' + fmtNum(row.path_obs_count || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderRankerTable(rows) {
    if (!rows.length) return renderEmpty('暂无 ranker 性能记录');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>run</th><th>duration</th><th>per trial</th><th>cache</th><th>hit rate</th><th>eval cache</th><th>train%</th><th>vs LGBM</th><th>rows</th><th>max group</th><th>train</th><th>drift</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var cache = row.ranker_cache || {};
        var timing = row.timing || {};
        var ratio = row.runtime_ratio_vs_regression == null ? '-' : fmtFloat(row.runtime_ratio_vs_regression, 2) + 'x';
        var evalCache = fmtPct(row.eval_cache_hit_rate) +
          '<div class="muted">matrix ' + fmtPct(row.matrix_cache_hit_rate) +
          ' / drift ' + fmtPct(row.feature_drift_cache_hit_rate) + '</div>';
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.started_at || '-') + '</div></td>' +
          '<td>' + fmtDuration(row.duration_s) + '</td>' +
          '<td>' + fmtDuration(row.duration_per_trial_s) + '<div class="muted">' + fmtNum(row.trials || 0) + ' trials</div></td>' +
          '<td>hit ' + fmtNum(cache.hits || 0) + ' / miss ' + fmtNum(cache.misses || 0) + '</td>' +
          '<td>' + fmtPct(row.cache_hit_rate) + '</td>' +
          '<td>' + evalCache + '</td>' +
          '<td>' + fmtPct(row.train_time_pct) + '</td>' +
          '<td>' + ratio + '</td>' +
          '<td>' + fmtNum(cache.cached_rows || 0) + '</td>' +
          '<td>' + fmtNum(cache.max_group_size || 0) + '</td>' +
          '<td>' + fmtDuration(timingSeconds(timing, 'train') || 0) + '</td>' +
          '<td>' + fmtDuration(timingSeconds(timing, 'metrics_feature_drift') || timingSeconds(timing, 'drift') || 0) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderRankMatrixCache(data) {
    data = data || {};
    var summary = data.summary || {};
    var benches = data.latest_benchmarks || [];
    var entries = data.cache_entries || [];
    if (!benches.length && !entries.length) return renderEmpty('暂无 rank matrix cache');
    var meta = '<div class="wb-kv">' +
      '<div><span>entries</span><strong>' + fmtNum(summary.entry_count || entries.length || 0) + '</strong></div>' +
      '<div><span>rows</span><strong>' + fmtNum(summary.total_rows || 0) + '</strong></div>' +
      '<div><span>hits</span><strong>' + fmtNum(summary.total_hits || 0) + '</strong></div>' +
      '<div><span>latest</span><strong>' + esc(summary.latest_used_at || '-') + '</strong></div>' +
      '</div>';
    var benchTable = '';
    if (benches.length) {
      benchTable = '<div class="wb-table-wrap" style="margin-top:12px"><table class="data-table data-table-compact wb-table">' +
        '<thead><tr><th>run</th><th>cache</th><th>label</th><th>features</th><th>rows</th><th>duration</th><th>rank build</th><th>gate</th></tr></thead><tbody>' +
        benches.map(function (row) {
          var cache = row.rank_matrix_cache || {};
          return '<tr>' +
            '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.built_at || '-') + '</div></td>' +
            '<td>' + pill(cache.status || 'unknown', cache.status || 'info') + '<div class="muted"><code>' + esc(cache.table_name || '-') + '</code></div></td>' +
            '<td>' + esc(row.label_name || '-') + '</td>' +
            '<td>' + fmtNum(row.feature_count || 0) + ' x ' + fmtNum(row.label_count || 0) + '</td>' +
            '<td>' + fmtNum(row.rank_matrix_rows || row.total_rows || 0) + '</td>' +
            '<td>' + fmtDuration(row.matrix_duration_s) + '</td>' +
            '<td>' + fmtDuration(row.rank_matrix_build_s) + '<div class="muted">proxy ' + fmtDuration(row.proxy_association_s) + '</div></td>' +
            '<td>' + pill(row.gate_status || '-', row.gate_status || 'unknown') + '<div class="muted">delta ' + fmtFloat(row.max_abs_rank_ic_delta, 6) + '</div></td>' +
            '</tr>';
        }).join('') +
        '</tbody></table></div>';
    }
    var entryTable = '';
    if (entries.length) {
      entryTable = '<div class="wb-table-wrap" style="margin-top:12px"><table class="data-table data-table-compact wb-table">' +
        '<thead><tr><th>cache table</th><th>panel</th><th>rows</th><th>cols</th><th>hits</th><th>build</th><th>used</th></tr></thead><tbody>' +
        entries.map(function (row) {
          return '<tr>' +
            '<td><code>' + esc(row.table_name || '-') + '</code></td>' +
            '<td>' + esc(row.panel_table || '-') + '<div class="muted">' + esc(row.feature_set_id || '-') + '</div></td>' +
            '<td>' + fmtNum(row.row_count || 0) + '</td>' +
            '<td>' + fmtNum(row.rank_column_count || 0) + '</td>' +
            '<td>' + fmtNum(row.hit_count || 0) + '</td>' +
            '<td>' + fmtDuration(row.build_duration_s) + '</td>' +
            '<td>' + esc(row.last_used_at || row.created_at || '-') + '</td>' +
            '</tr>';
        }).join('') +
        '</tbody></table></div>';
    }
    return meta + benchTable + entryTable;
  }

  function renderStabilityContext(data) {
    data = data || {};
    var summaries = data.summaries || [];
    var diagnostics = data.diagnostics || [];
    if (!summaries.length && !diagnostics.length) return renderEmpty('暂无稳定性上下文诊断');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>source run</th><th>family</th><th>Blockers</th><th>Holdout</th><th>WF</th><th>Gates</th><th>建议</th></tr></thead><tbody>' +
      summaries.map(function (row) {
        var blockers = row.main_blockers || [];
        return '<tr>' +
          '<td><code>' + esc(row.source_run_id || '-') + '</code><div class="muted">' + esc(row.label_name || '-') + '</div></td>' +
          '<td>' + esc(row.model_family || '-') + '<div class="muted">trial ' + fmtNum(row.best_trial_number) + '</div></td>' +
          '<td>' + (blockers.length ? blockers.map(function (b) { return pill(b, 'bad'); }).join('') : pill('none', 'ok')) + '</td>' +
          '<td>' + fmtFloat(row.holdout_rank_ic, 4) + (row.low_holdout_rank_ic ? '<div>' + pill('low RankIC', 'bad') + '</div>' : '') + '</td>' +
          '<td>' + fmtFloat(row.walkforward_avg_rank_ic, 4) + ' / ' + fmtFloat(row.walkforward_std_rank_ic, 4) +
            '<div class="muted">neg ' + fmtNum(row.negative_rank_ic_folds || 0) + ' / weak ' + fmtNum(row.weak_rank_ic_periods || 0) + '</div></td>' +
          '<td>' + pill('drift ' + (row.drift_gate_pass ? 'pass' : 'fail'), row.drift_gate_pass ? 'pass' : 'fail') +
            pill('dd ' + (row.drawdown_gate_pass ? 'pass' : 'fail'), row.drawdown_gate_pass ? 'pass' : 'fail') + '</td>' +
          '<td>' + esc(row.recommendation || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>' +
      renderStabilityDiagnosticTable(diagnostics);
  }

  function renderStabilityDiagnosticTable(rows) {
    if (!rows.length) return '';
    return '<div class="wb-table-wrap" style="margin-top:12px"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>窗口</th><th>诊断</th><th>RankIC</th><th>Spread</th><th>TopK</th><th>标签+</th><th>Regime</th><th>PSI</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var diag = row.diagnosis || 'unknown';
        return '<tr>' +
          '<td>' + esc(row.scope || '-') + ' #' + fmtNum(row.fold_id) +
            '<div class="muted">' + esc(row.period_start || '-') + ' ~ ' + esc(row.period_end || '-') + '</div></td>' +
          '<td>' + pill(diag, diag === 'ok' ? 'ok' : 'bad') + '</td>' +
          '<td>' + fmtFloat(row.rank_ic, 4) + '</td>' +
          '<td>' + fmtPct(row.spread) + '</td>' +
          '<td>' + fmtPct(row.topk_net_return) + '<div class="muted">DD ' + fmtPct(row.topk_max_drawdown) + '</div></td>' +
          '<td>' + fmtPct(row.label_positive_rate) + '<div class="muted">' + fmtPct(row.market_ret_mean) + '</div></td>' +
          '<td>' + esc(row.dominant_regime || '-') + '<div class="muted">' + fmtPct(row.dominant_regime_share) + '</div></td>' +
          '<td>' + fmtFloat(row.feature_drift_psi_max, 3) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderRankerPolicy(data) {
    var policy = data.policy || {};
    var tokens = policy.gate_failure_tokens || [];
    return '<div class="wb-kv">' +
      '<div><span>最大耗时比</span><strong>' + fmtFloat(policy.max_runtime_ratio_vs_regression, 2) + 'x</strong></div>' +
      '<div><span>大任务阈值</span><strong>' + fmtNum(policy.large_trial_threshold || 0) + ' trials</strong></div>' +
      '<div><span>要求历史画像</span><strong>' + esc(policy.require_prior_profile === false ? '否' : '是') + '</strong></div>' +
      '<div><span>通过状态</span><strong>' + esc(policy.passing_status || 'pass') + '</strong></div>' +
      '<div><span>失败门类</span><strong>' + esc(tokens.length ? tokens.join(', ') : '-') + '</strong></div>' +
      '<div><span>更新时间</span><strong>' + esc(data.started_at || '-') + '</strong></div>' +
      '</div>';
  }

  function renderChampion(data) {
    var lifecycle = data.lifecycle || {};
    var champions = lifecycle.champions || [];
    var topk = data.latest_primary_topk || {};
    var firstChampion = champions.length ? champions[0].model_id : '-';
    var stabilityContext = data.stability_context || {};
    var deployment = data.deployment || {};

    setBody(
      renderReadModelMeta(data) +
      '<div class="stats-row wb-stats-row">' +
      statCard('Champion', firstChampion, renderStatusCounts(lifecycle.counts), champions.length ? 'champion' : 'missing') +
      statCard('候选模型', fmtNum((data.challengers || []).length), 'lifecycle challengers', (data.challengers || []).length ? 'running' : 'none') +
      statCard('部署状态', deployment.status || 'unknown', renderDeploymentSub(deployment), deployment.status || 'unknown') +
      statCard('Primary TopK', fmtNum(topk.count || 0), esc(topk.snapshot_date || '-') + ' / ' + esc(topk.model_id || '-'), topk.count ? 'ok' : 'missing') +
      statCard('评估批次', fmtNum((data.candidate_evaluations || []).length), 'candidate evaluations', (data.candidate_evaluations || []).length ? 'completed' : 'none') +
      '</div>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Champion 阻塞上下文</h3><div class="muted">run_id: <code>' + esc(stabilityContext.run_id || '-') + '</code></div></div></div>' +
      renderStabilityContext(stabilityContext) +
      '</section>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>候选评估</h3><div class="muted">PIT / evidence / gate</div></div></div>' +
      renderEvaluationTable(data.candidate_evaluations || []) +
      '</section>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Promotion Gate</h3><div class="muted">收益 / 回撤 / 阻塞</div></div></div>' +
      renderGateTable(data.promotion_gates || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>证据包</h3><div class="muted">challenger evidence</div></div></div>' +
      renderEvidenceTable(data.evidence_bundles || []) +
      '</section>' +
      '</div>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>Lifecycle 候选</h3><div class="muted">IC / drift</div></div></div>' +
      renderChallengerTable(data.challengers || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>最新 TopK</h3><div class="muted">' + esc(topk.snapshot_date || '-') + '</div></div></div>' +
      renderTopkTable(topk.rows || []) +
      '</section>' +
      '</div>'
    );
  }

  function renderDeploymentSub(deployment) {
    deployment = deployment || {};
    var gate = deployment.latest_promotion_gate || {};
    var blockers = deployment.blockers || [];
    if (blockers.length) return blockers.map(function (b) { return pill(b, 'bad'); }).join('');
    return '<code>' + esc(gate.gate_run_id || '-') + '</code>';
  }

  function renderEvaluationTable(rows) {
    if (!rows.length) return renderEmpty('暂无候选评估');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>run</th><th>model</th><th>状态</th><th>PIT</th><th>Evidence</th><th>Gate</th><th>失败步骤</th><th>duration</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.evaluation_run_id || '-') + '</code><div class="muted">' + esc(row.started_at || '-') + '</div></td>' +
          '<td>' + esc(row.model_id || '-') + '</td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + pill(row.pit_status || '-', row.pit_status) + '<div class="muted">' + fmtNum(row.pit_violation_rows || 0) + '</div></td>' +
          '<td>' + pill(row.evidence_status || '-', row.evidence_status) + '</td>' +
          '<td>' + pill(row.gate_status || '-', row.gate_status) + '</td>' +
          '<td>' + fmtNum((row.failed_steps || []).length) + '</td>' +
          '<td>' + fmtDuration(row.duration_s) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderGateTable(rows) {
    if (!rows.length) return renderEmpty('暂无 gate 记录');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>gate</th><th>decision</th><th>challenger</th><th>RankIC</th><th>多空</th><th>MaxDD</th><th>阻塞</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.gate_run_id || '-') + '</code><div class="muted">' + esc(row.evaluated_at || '-') + '</div></td>' +
          '<td>' + pill(row.decision || row.promotion_status || '-', row.decision || row.promotion_status) + '</td>' +
          '<td>' + esc(row.challenger_model_id || '-') + '<div class="muted">vs ' + esc(row.champion_model_id || '-') + '</div></td>' +
          '<td>' + fmtFloat(row.rank_ic_challenger, 4) + ' / ' + fmtFloat(row.rank_ic_champion, 4) + '</td>' +
          '<td>' + fmtPct(row.long_short_challenger) + ' / ' + fmtPct(row.long_short_champion) + '</td>' +
          '<td>' + fmtPct(row.max_drawdown_challenger) + ' / ' + fmtPct(row.max_drawdown_champion) + '</td>' +
          '<td>' + fmtNum((row.blockers || []).length) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderEvidenceTable(rows) {
    if (!rows.length) return renderEmpty('暂无证据包');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>evidence</th><th>model</th><th>状态</th><th>步骤</th><th>Gate</th><th>阻塞</th><th>duration</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.evidence_run_id || '-') + '</code><div class="muted">' + esc(row.started_at || '-') + '</div></td>' +
          '<td>' + esc(row.model_id || '-') + '</td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + fmtNum(row.step_count || 0) + '</td>' +
          '<td>' + pill(row.gate_status || '-', row.gate_status) + '</td>' +
          '<td>' + fmtNum(row.blocker_count || 0) + '</td>' +
          '<td>' + fmtDuration(row.duration_s) + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderChallengerTable(rows) {
    if (!rows.length) return renderEmpty('暂无 challenger');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>model</th><th>状态</th><th>Holdout IC</th><th>WF IC</th><th>Drift</th><th>updated</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td><code>' + esc(row.model_id || '-') + '</code></td>' +
          '<td>' + pill(row.status || '-', row.status) + '</td>' +
          '<td>' + fmtFloat(row.ic_holdout, 4) + '</td>' +
          '<td>' + fmtFloat(row.ic_walkforward_avg, 4) + ' / ' + fmtFloat(row.ic_walkforward_std, 4) + '</td>' +
          '<td>' + fmtFloat(row.drift_score, 3) + '</td>' +
          '<td>' + esc(row.updated_at || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTopkTable(rows) {
    if (!rows.length) return renderEmpty('暂无 primary TopK');
    return '<div class="wb-table-wrap"><table class="data-table data-table-compact wb-table">' +
      '<thead><tr><th>Rank</th><th>股票</th><th>Score</th><th>Percentile</th><th>Regime</th><th>Track</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + fmtNum(row.rank_in_date) + '</td>' +
          '<td><code>' + esc(row.stock_code || '-') + '</code></td>' +
          '<td>' + fmtFloat(row.pred_score, 4) + '</td>' +
          '<td>' + fmtPct(row.percentile) + '</td>' +
          '<td>' + esc(row.regime_flag || '-') + '</td>' +
          '<td>' + esc(row.track_id || row.run_mode || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderTab(tab, data) {
    if (tab === 'dataSources') return renderDataSources(data || {});
    if (tab === 'pipelines') return renderPipelines(data || {});
    if (tab === 'features') return renderFeatures(data || {});
    if (tab === 'delivery') return renderDelivery(data || {});
    if (tab === 'paperSim') return renderPaperSim(data || {});
    if (tab === 'research') return renderResearch(data || {});
    if (tab === 'champion') return renderChampion(data || {});
    if (tab === 'recommendations') return renderRecommendations(data || {});
    if (tab === 'storage') return renderStorage(data || {});
    return renderOverview(data || {});
  }

  function setTab(tab) {
    if (endpoints[tab]) state.activeTab = tab;
    return state.activeTab;
  }

  async function show(tabOrForce, maybeForce) {
    var force = maybeForce;
    if (typeof tabOrForce === 'string') {
      setTab(tabOrForce);
    } else {
      force = tabOrForce;
    }
    var tab = state.activeTab || 'overview';
    renderShell(tab);
    renderLoading();
    try {
      var data = await fetchTab(tab, force);
      renderTab(tab, data || {});
    } catch (error) {
      renderError(error);
    }
  }

  global.WorkbenchView = {
    show: show,
    setTab: setTab,
    buildReadModelMeta: buildReadModelMeta,
    buildDeliveryModel: buildDeliveryModel,
    _renderOverview: renderOverview,
    _renderDataSources: renderDataSources,
    _renderPipelines: renderPipelines,
    _renderFeatures: renderFeatures,
    _renderDelivery: renderDelivery,
    _renderPaperSim: renderPaperSim,
    _renderResearch: renderResearch,
    _renderChampion: renderChampion,
    _renderRecommendations: renderRecommendations,
    _renderStorage: renderStorage,
    _state: state
  };
})(window);
