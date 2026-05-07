(function (global) {
  'use strict';

  var endpoints = {
    overview: '/api/workbench/overview',
    research: '/api/workbench/research',
    champion: '/api/workbench/champion',
    dataSources: '/api/workbench/data-sources',
    pipelines: '/api/workbench/pipelines',
    features: '/api/workbench/features',
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
      tabButton('research', '研究', tab) +
      tabButton('champion', 'Champion', tab) +
      tabButton('recommendations', '推荐', tab) +
      tabButton('storage', '存储', tab) +
      '</div>' +
      '<button class="chip chip-outline" id="wb-refresh">刷新</button>' +
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

  function renderResearch(data) {
    var schedule = data.research_schedule || {};
    var tasks = schedule.tasks || [];
    var studies = data.model_stability || [];
    var ranker = data.ranker_profiles || [];
    var rankerPolicy = data.ranker_policy || {};
    var rankMatrixCache = data.rank_matrix_cache || {};
    var stabilityContext = data.stability_context || {};
    var stockHorizon = data.stock_horizon_profile || {};
    var shareholderPlan = data.shareholder_plan_family_eval || {};
    var temporalSynergy = data.temporal_synergy || {};

    setBody(
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
      renderShareholderPlanFamilyEval(shareholderPlan) +
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
    var clusters = data.redundancy_clusters || [];
    var conditional = data.conditional_synergies || [];
    if (!quality.run_id && !labels.length && !relevance.length && !synergies.length && !selected.length && !optuna.length && !policies.length && !gates.length && !clusters.length && !conditional.length) return renderEmpty('暂无时序协同研究');
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
        return '<tr>' +
          '<td><code>' + esc(row.run_id || '-') + '</code><div class="muted">' + esc(row.candidate_run_id || '-') + '</div></td>' +
          '<td>' + esc(row.label_name || '-') + '<div class="muted">' + fmtNum(row.candidate_horizon_days || 0) + 'd / base ' + fmtNum(row.baseline_horizon_days || 60) + 'd</div></td>' +
          '<td>' + pill(row.validation_status || '-', row.validation_status || 'info') + '<div class="muted">' + esc(row.promotion_status || '-') + '</div></td>' +
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

  function latestGateDecision(rows) {
    rows = rows || [];
    if (!rows.length) return 'none';
    return rows[0].decision || rows[0].promotion_status || 'unknown';
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
    _renderOverview: renderOverview,
    _renderDataSources: renderDataSources,
    _renderPipelines: renderPipelines,
    _renderFeatures: renderFeatures,
    _renderResearch: renderResearch,
    _renderChampion: renderChampion,
    _renderRecommendations: renderRecommendations,
    _renderStorage: renderStorage,
    _state: state
  };
})(window);
