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
    var stabilityContext = data.stability_context || {};
    var stockHorizon = data.stock_horizon_profile || {};

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
      '<div class="panel-head"><div><h3>个股持股周期画像</h3><div class="muted">run_id: <code>' + esc(stockHorizon.run_id || '-') + '</code> / baseline: <code>' + esc(stockHorizon.baseline_label || 'forward_ret_60d') + '</code></div></div></div>' +
      renderStockHorizonProfile(stockHorizon) +
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
      '<div class="panel-head"><div><h3>漂移处理优先级</h3><div class="muted">run_id: <code>' + esc(((data.feature_drift || {}).run_id) || '-') + '</code></div></div></div>' +
      renderDriftTable(data.feature_drift) +
      '</section>' +
      '</div>'
    );
  }

  function renderDataSources(data) {
    var kline = data.kline || {};
    var primary = kline.primary || {};
    var validation = data.latest_feature_validation || {};
    setBody(
      '<div class="stats-row wb-stats-row">' +
      statCard('交易日目标', data.calendar_target || '-', 'calendar gate', data.calendar_target ? 'ok' : 'missing') +
      statCard('K线主源', primary.source_name || '-', 'tier ' + fmt(primary.source_tier), kline.primary_is_tdxhub ? 'ok' : 'bad') +
      statCard('主源行数', fmtNum(primary.row_count || 0), esc(primary.last_data_date || '-'), primary.row_count ? 'ok' : 'missing') +
      statCard('Fallback', fmtNum(kline.fallback_active_count || 0), 'active sources', kline.fallback_active_count ? 'warn' : 'ok') +
      statCard('特征 fallback', fmtPct(validation.source_fallback_ratio), esc(validation.validation_id || '-'), validation.status || 'unknown') +
      '</div>' +

      '<div class="wb-grid">' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>源阻塞</h3><div class="muted">failures / primary contract</div></div></div>' +
      renderSourceBlockers(data.blockers || []) +
      '</section>' +
      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>特征源分布</h3><div class="muted">' + esc(validation.validated_at || '-') + '</div></div></div>' +
      renderSourceDistribution(validation.source_distribution || []) +
      '</section>' +
      '</div>' +

      '<section class="panel wb-panel">' +
      '<div class="panel-head"><div><h3>数据源水位</h3><div class="muted">' + fmtNum(data.watermark_count || 0) + ' sources</div></div></div>' +
      renderWatermarkTable(data.watermarks || []) +
      '</section>'
    );
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
    setBody(
      '<div class="stats-row wb-stats-row">' +
      statCard('Registry', fmtNum(registry.feature_count || 0), 'model inputs ' + fmtNum(registry.model_input_count || 0), 'ok') +
      statCard('Labels', fmtNum(registry.label_count || 0), 'PIT lagged labels', 'info') +
      statCard('Panel rows', fmtNum(validation.rows || 0), esc(validation.validation_id || '-'), validation.status || 'unknown') +
      statCard('Lineage', fmtPct(validation.source_lineage_coverage), 'source coverage', validation.source_lineage_coverage >= 1 ? 'ok' : 'warn') +
      statCard('Fallback ratio', fmtPct(validation.source_fallback_ratio), 'feature panel', validation.source_fallback_ratio ? 'warn' : 'ok') +
      '</div>' +
      '<div class="wb-grid">' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>特征组</h3><div class="muted">registry groups</div></div></div>' +
      renderKeyValueCounts(registry.group_counts || {}) + '</section>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>Panel Validation</h3><div class="muted">' + esc(validation.validated_at || '-') + '</div></div></div>' +
      renderValidationSummary(validation) + '</section>' +
      '</div>' +
      '<section class="panel wb-panel"><div class="panel-head"><div><h3>Feature Search Space</h3><div class="muted">selected / excluded</div></div></div>' +
      renderSearchSpaceTable(data.search_spaces || []) + '</section>' +
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
      '<thead><tr><th>Rank</th><th>股票</th><th>行业</th><th>Score</th><th>Percentile</th><th>Regime</th><th>Top features</th></tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' +
          '<td>' + fmtNum(row.rank_in_date) + '</td>' +
          '<td><code>' + esc(row.stock_code || '-') + '</code><div class="muted">' + esc(row.stock_name || row.xueqiu_symbol || '-') + '</div></td>' +
          '<td>' + esc(row.tdx_l1_name || '-') + '<div class="muted">' + esc(row.tdx_l2_name || '') + '</div></td>' +
          '<td>' + fmtFloat(row.pred_score, 4) + '</td>' +
          '<td>' + fmtPct(row.percentile) + '</td>' +
          '<td>' + esc(row.regime_flag || '-') + '</td>' +
          '<td>' + esc((row.top_features || []).join(', ') || '-') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table></div>';
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

  function renderStockHorizonProfile(data) {
    data = data || {};
    var comparison = data.horizon_comparison || [];
    var distribution = data.horizon_distribution || [];
    var stocks = data.best_stocks || [];
    var effects = data.top_effects || [];
    var effectDetails = data.feature_effects_by_horizon || [];
    if (!comparison.length && !distribution.length && !stocks.length && !effects.length && !effectDetails.length) return renderEmpty('暂无个股周期画像');
    return '<div class="wb-kv">' +
      '<div><span>画像行数</span><strong>' + fmtNum(data.profile_count || 0) + '</strong></div>' +
      '<div><span>覆盖股票</span><strong>' + fmtNum(data.best_count || 0) + '</strong></div>' +
      '<div><span>变量效应</span><strong>' + fmtNum(data.effect_count || 0) + '</strong></div>' +
      '</div>' +
      '<div class="wb-grid" style="margin-top:12px">' +
      '<div>' + renderHorizonComparisonTable(comparison) + '</div>' +
      '<div>' + renderHorizonDistributionTable(distribution) + '</div>' +
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
