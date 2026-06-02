/* ============================================================
   model-monitor.js — 模型监控 widget
   负责模型比较、promotion gate、TDX validation、metrics cards 与三张图表
   API: window.ModelMonitorWidget.loadModelMonitor(deps)
   ============================================================ */
(function (global) {
  'use strict';

  var featureLabels = global.FeatureLabels || { features: {}, models: {}, loaded: false };
  var modelMonitorState = { modelId: null, regime: '', _bound: false };
  var runtime = {
    api: null,
    esc: function (v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },
    fmtDateTime: null,
    doc: typeof document !== 'undefined' ? document : null,
  };

  function el(id) {
    return runtime.doc && typeof runtime.doc.getElementById === 'function' ? runtime.doc.getElementById(id) : null;
  }

  function escText(value) {
    return runtime.esc ? runtime.esc(value) : String(value == null ? '' : value);
  }

  function formatNumber(value, digits) {
    return value == null ? '-' : Number(value).toFixed(digits == null ? 3 : digits);
  }

  function formatPercent(value, digits) {
    return value == null ? '-' : (Number(value) * 100).toFixed(digits == null ? 2 : digits) + '%';
  }

  function initRuntime(deps) {
    deps = deps || {};
    if (typeof deps.api === 'function') runtime.api = deps.api;
    if (typeof deps.esc === 'function') runtime.esc = deps.esc;
    if (typeof deps.fmtDateTime === 'function') runtime.fmtDateTime = deps.fmtDateTime;
    if (deps.document) runtime.doc = deps.document;
    if (deps.state) modelMonitorState = deps.state;
    if (deps.featureLabels) setFeatureLabels(deps.featureLabels);
    if (global.FeatureLabels) featureLabels = global.FeatureLabels;
    global.FeatureLabels = featureLabels;
  }

  function api(path, opts) {
    if (!runtime.api) throw new Error('ModelMonitorWidget api missing');
    return runtime.api(path, opts);
  }

  function setFeatureLabels(next) {
    next = next || {};
    featureLabels = {
      features: next.features || {},
      models: next.models || {},
      loaded: !!next.loaded,
    };
    global.FeatureLabels = featureLabels;
    return featureLabels;
  }

  async function ensureFeatureLabels() {
    if (featureLabels.loaded) return featureLabels;
    try {
      var res = await api('/api/rec/labels');
      if (res && res.ok) {
        setFeatureLabels({
          features: res.features || {},
          models: res.models || {},
          loaded: true,
        });
      }
    } catch (e) {}
    return featureLabels;
  }

  function labelFeature(name) {
    var zh = featureLabels.features && featureLabels.features[name];
    return zh ? name + '（' + zh + '）' : name;
  }

  function labelModelId(mid) {
    if (!mid) return '-';
    for (var prefix in (featureLabels.models || {})) {
      if (mid.indexOf(prefix + '_') === 0) {
        var tail = mid.slice(prefix.length + 1);
        if (tail.length >= 13 && /^\d{8}/.test(tail)) {
          return featureLabels.models[prefix] + ' · ' +
            tail.slice(0, 4) + '-' + tail.slice(4, 6) + '-' + tail.slice(6, 8) + ' ' +
            tail.slice(9, 11) + ':' + tail.slice(11, 13);
        }
        return featureLabels.models[prefix] + ' · ' + tail;
      }
    }
    return mid;
  }

  function loadModelHistorySelect(sel) {
    if (!sel) return Promise.resolve();
    return (async function () {
      try {
        var res = await api('/api/rec/model-history?limit=20');
        if (res && res.ok) {
          sel.innerHTML = (res.items || []).map(function (m) {
            var cname = m.model_name_cn || m.model_id;
            var grade = m.composite_grade && m.composite_grade.grade || '-';
            return '<option value="' + m.model_id + '">' + cname + '  · 综合：' + grade + '</option>';
          }).join('');
          if (res.items && res.items[0]) modelMonitorState.modelId = res.items[0].model_id;
          try {
            var cmp = await api('/api/rec/model-comparison');
            if (cmp && cmp.ok && cmp.champion && cmp.champion.model_id) {
              modelMonitorState.modelId = cmp.champion.model_id;
              sel.value = modelMonitorState.modelId;
            }
          } catch (e) {}
        }
      } catch (e) {
        sel.innerHTML = '<option value="">(加载失败)</option>';
      }
    })();
  }

  async function loadModelMonitor(deps) {
    initRuntime(deps);
    await ensureFeatureLabels();
    if (modelMonitorState._bound) return renderModelMonitor();
    modelMonitorState._bound = true;

    var sel = el('mm-model-select');
    var regSel = el('mm-regime-select');
    var btn = el('mm-refresh');
    if (sel) sel.addEventListener('change', function () { modelMonitorState.modelId = sel.value; renderModelMonitor(); });
    if (regSel) regSel.addEventListener('change', function () { modelMonitorState.regime = regSel.value; });
    if (btn) btn.addEventListener('click', renderModelMonitor);

    await loadModelHistorySelect(sel);
    return renderModelMonitor();
  }

  async function renderModelMonitor() {
    return Promise.all([
      renderModelComparison(),
      renderPromotionGate(),
      renderTdxValidation(),
      renderMetricsCards(),
      renderDailyChart(),
      renderRegimeChart(),
      renderFeatureImportance(),
    ]);
  }

  async function renderModelComparison() {
    var box = el('mm-comparison'); if (!box) return;
    try {
      var res = await api('/api/rec/model-comparison');
      if (!res || !res.ok) { box.innerHTML = ''; return; }
      var c = res.champion || {}, ch = res.challenger || {};
      var shadow = res.shadow_topk || {};
      var evidence = res.evidence_bundle || {};
      box.innerHTML =
        '<div class="cm-action-grid cm-action-grid-tight">' +
        '<div class="cm-action-card"><div class="cm-action-title">Champion · 正式推荐</div><div style="font-weight:700;font-size:13px">' + escText(labelModelId(c.model_id || '-')) + '</div>' +
        '<div class="muted" style="font-size:11px;margin-top:4px">默认推荐只取 lifecycle champion · RankIC ' + formatNumber(c.holdout_rank_ic) + ' · L/S ' + formatPercent(c.holdout_long_short_spread) + '</div></div>' +
        '<div class="cm-action-card"><div class="cm-action-title">最新 Challenger · Shadow 实验</div><div style="font-weight:700;font-size:13px">' + escText(labelModelId(ch.model_id || '尚未训练 challenger')) + '</div>' +
        '<div class="muted" style="font-size:11px;margin-top:4px">Not promoted · RankIC ' + formatNumber(ch.holdout_rank_ic) + ' · L/S ' + formatPercent(ch.holdout_long_short_spread) + '</div>' +
        '<div class="muted" style="font-size:11px;margin-top:2px">shadow topK ' + (shadow.rows || shadow.row_count || 0) + ' rows · ' + (shadow.snapshot_date || shadow.latest_snapshot || '-') + '</div>' +
        (evidence.evidence_run_id ? '<div class="muted" style="font-size:11px;margin-top:2px">evidence ' + escText(evidence.status || '-') + ' · ' + escText(evidence.evidence_run_id) + '</div>' : '') + '</div>' +
        '</div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">model comparison error: ' + escText(e.message) + '</div>';
    }
  }

  async function renderPromotionGate() {
    var box = el('mm-promotion-gate'); if (!box) return;
    try {
      var res = await api('/api/rec/model-comparison');
      if (!res || !res.ok) { box.innerHTML = ''; return; }
      var gate = res.promotion_gate || {};
      var status = gate.promotion_status || 'WAIT';
      var tone = status === 'PASS' ? 'ok' : status === 'FAIL' ? 'bad' : 'warn';
      var blockers = gate.blockers || gate.reasons || gate.reason || '';
      if (Array.isArray(blockers)) {
        blockers = blockers.map(function (b) {
          if (typeof b === 'string') return b;
          return [b.gate, b.status, b.reason].filter(Boolean).join(': ');
        }).join('；');
      }
      box.innerHTML =
        '<div class="cm-status-strip">' +
        '<div class="cm-status-item cm-status-' + tone + '"><span>Promotion Gate</span><b>' + escText(status) + '</b></div>' +
        '<div class="cm-status-item"><span>Decision</span><b>' + escText(gate.decision || 'keep_shadow') + '</b></div>' +
        '<div class="cm-status-item"><span>默认推荐</span><b>' + (res.selection_fallback ? 'fallback' : 'champion-only') + '</b></div>' +
        '<div class="cm-status-item"><span>发布规则</span><b>PASS 也只标记 promote-ready</b></div>' +
        (blockers ? '<div class="cm-status-item cm-status-warn"><span>阻塞项</span><b>' + escText(String(blockers).slice(0, 120)) + '</b></div>' : '') +
        '</div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">promotion gate error: ' + escText(e.message) + '</div>';
    }
  }

  async function renderTdxValidation() {
    var box = el('mm-tdx-validation'); if (!box) return;
    try {
      var res = await api('/api/rec/tdx-feature-validation');
      if (!res || !res.ok) { box.innerHTML = ''; return; }
      var keep = (res.manual || []).filter(function (r) { return r.decision === 'keep'; });
      var watchDrop = (res.manual || []).filter(function (r) { return r.decision !== 'keep'; }).slice(0, 8);
      var sources = (res.sources || []).map(function (s) {
        return '<span style="display:inline-block;margin:2px 6px 2px 0;padding:3px 7px;border:1px solid var(--cm-ink-100);border-radius:4px;font-size:11px">' +
          escText(s.data_domain) + ': <b>' + escText(s.preferred_source) + '</b>' +
          (s.fallback_1 ? ' / ' + escText(s.fallback_1) : '') +
          (s.fallback_2 ? ' / ' + escText(s.fallback_2) : '') + '</span>';
      }).join('');
      var keepRows = keep.map(function (r) {
        return '<tr><td>' + escText(labelFeature(r.feature_name)) + '</td><td>' + formatNumber(r.mean_rank_ic) + '</td><td>' + formatNumber(r.fold_same_sign_rate) + '</td><td>' + formatNumber(r.coverage_pct) + '%</td><td>' + (r.pit_violation_rows || 0) + '</td></tr>';
      }).join('');
      var wdRows = watchDrop.map(function (r) {
        return '<tr><td>' + escText(r.decision) + '</td><td>' + escText(labelFeature(r.feature_name)) + '</td><td>' + escText(r.primary_reason || '-') + '</td></tr>';
      }).join('');
      box.innerHTML =
        '<div class="panel" style="padding:12px">' +
        '<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-bottom:8px">' +
        '<h4 style="margin:0;font-size:13px">TDX keep 特征验证与数据源切换</h4>' +
        '<span class="muted" style="font-size:11px">PIT violations: manual ' + ((res.pit && res.pit.tdx_f10_gpcw_v1 && res.pit.tdx_f10_gpcw_v1.violation_rows) || 0) + '</span></div>' +
        '<div style="margin-bottom:8px">' + sources + '</div>' +
        '<div class="mm-validation-grid">' +
        '<div class="cm-table-scroll"><table class="mini-table"><thead><tr><th>keep feature</th><th>RankIC</th><th>same sign</th><th>coverage</th><th>PIT</th></tr></thead><tbody>' + keepRows + '</tbody></table></div>' +
        '<div class="cm-table-scroll"><table class="mini-table"><thead><tr><th>decision</th><th>feature</th><th>reason</th></tr></thead><tbody>' + wdRows + '</tbody></table></div>' +
        '</div></div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">tdx validation error: ' + escText(e.message) + '</div>';
    }
  }

  async function renderMetricsCards() {
    var box = el('mm-metrics');
    var cbox = el('mm-composite');
    if (!box) return;
    var mid = modelMonitorState.modelId;
    if (!mid) { box.innerHTML = '<div class="muted" style="padding:20px">无已训练模型</div>'; if (cbox) cbox.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">' + (res && res.message || 'load err') + '</div>'; return; }
      var m = res.meta || {};

      var comp = m.composite_grade || {};
      if (cbox) {
        cbox.innerHTML =
          '<div class="panel" style="padding:14px;display:flex;gap:18px;align-items:center;background:var(--cm-macaron-cream);border-left:4px solid ' +
          (comp.color || 'var(--cm-ink-300)') + '">' +
          '<div style="font-size:28px;font-weight:700;color:' + (comp.color || 'var(--cm-ink-300)') + ';min-width:120px">' +
          (comp.grade || '-') + '</div>' +
          '<div style="flex:1">' +
          '<div style="font-size:12px;color:var(--cm-ink-500)">综合评级（5 档：差/较差/一般/良好/优秀）· ' +
          (m.model_name_cn || mid) + '</div>' +
          '<div style="font-size:11px;color:var(--cm-ink-500);margin-top:2px">平均档位 ' + (comp.avg_index == null ? '-' : comp.avg_index.toFixed(2)) + ' / 4 · 特征数 ' + (m.n_features || '-') + ' · 创建 ' + (m.created_at ? m.created_at.slice(0, 19) : '-') + '</div>' +
          '</div>' +
          '</div>';
      }

      var mg = m.metric_grades || {};
      function gradeChip(g) {
        if (!g || g.index < 0) return '<div class="wb-card-chip" style="background:var(--cm-ink-50);color:var(--cm-ink-500)">-</div>';
        return '<div class="wb-card-chip" style="background:' + g.color + '22;color:' + g.color + ';font-weight:600">' + g.grade + '</div>';
      }
      var portfolioItems = (res.portfolio && res.portfolio.items) || [];
      var p30 = portfolioItems.find(function (x) { return x.curve_type === 'model_top20' && Number(x.cost_bps) === 30; }) ||
        portfolioItems.find(function (x) { return x.curve_type === 'model_top20'; }) || {};
      var wf = (res.walkforward && res.walkforward.summary) || {};
      var dq = res.data_quality || {};
      var liveCards =
        '<div class="wb-card"><div class="wb-card-label">net top20（组合·扣成本）</div><div class="wb-card-value">' + formatPercent(p30.annualized_return) + '</div><div class="wb-card-sub">MaxDD ' + formatPercent(p30.max_drawdown) + ' · Sharpe ' + formatNumber(p30.sharpe, 2) + '</div><div class="wb-card-chip">30bps</div></div>' +
        '<div class="wb-card"><div class="wb-card-label">walk-forward RankIC</div><div class="wb-card-value">' + formatNumber(wf.rank_ic_mean, 3) + '</div><div class="wb-card-sub">正折率 ' + formatPercent(wf.rank_ic_positive_ratio) + ' · 折数 ' + (wf.fold_count || '-') + '</div><div class="wb-card-chip">稳定性</div></div>' +
        '<div class="wb-card"><div class="wb-card-label">feature schema</div><div class="wb-card-value">' + (m.feature_schema_version || '-') + '</div><div class="wb-card-sub">label ' + (m.label_name || '-') + ' · ' + ((m.feature_cols || []).length || m.n_features || '-') + ' 列</div><div class="wb-card-chip">schema</div></div>' +
        '<div class="wb-card"><div class="wb-card-label">panel freshness</div><div class="wb-card-value">' + (dq.latest_panel_date || '-') + '</div><div class="wb-card-sub">' + (dq.codes || '-') + ' 股 · ' + (dq.dates || '-') + ' 日 · label ' + (dq.label_rows || '-') + '</div><div class="wb-card-chip">data</div></div>';
      box.innerHTML =
        liveCards +
        '<div class="wb-card"><div class="wb-card-label">holdout_ic（持出期·IC）</div><div class="wb-card-value">' + formatNumber(m.holdout_ic, 3) + '</div><div class="wb-card-sub">门槛 优秀>0.05 良好>0.03</div>' + gradeChip(mg.holdout_ic) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_rank_ic（持出期·Rank IC）</div><div class="wb-card-value">' + formatNumber(m.holdout_rank_ic, 3) + '</div><div class="wb-card-sub">门槛 优秀>0.08 良好>0.06</div>' + gradeChip(mg.holdout_rank_ic) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_top_decile_avg（Top 10% 平均20日收益）</div><div class="wb-card-value">' + formatPercent(m.holdout_top_decile_avg) + '</div><div class="wb-card-sub">门槛 优秀>3% 良好>2%</div>' + gradeChip(mg.holdout_top_decile_avg) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_long_short_spread（多空价差）</div><div class="wb-card-value">' + formatPercent(m.holdout_long_short_spread) + '</div><div class="wb-card-sub">门槛 优秀>4% 良好>2%</div>' + gradeChip(mg.holdout_long_short_spread) + '</div>' +
        '<div class="wb-card"><div class="wb-card-label">holdout_winrate_top（Top 10% 胜率）</div><div class="wb-card-value">' + formatPercent(m.holdout_winrate_top) + '</div><div class="wb-card-sub">门槛 优秀>60% 良好>56%</div>' + gradeChip(mg.holdout_winrate_top) + '</div>';
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + e.message + '</div>';
    }
  }

  function svgLine(series, w, h, pad) {
    pad = pad || 30;
    if (!series.length) return '<text x="' + (w / 2) + '" y="' + (h / 2) + '" text-anchor="middle" fill="var(--cm-ink-300)" font-size="12">无数据</text>';
    var vals = series.map(function (d) { return d.v; }).filter(function (v) { return v != null && !isNaN(v); });
    if (!vals.length) return '<text x="' + (w / 2) + '" y="' + (h / 2) + '" text-anchor="middle" fill="var(--cm-ink-300)" font-size="12">无数值</text>';
    var minV = Math.min.apply(null, vals), maxV = Math.max.apply(null, vals);
    var range = maxV - minV || 0.01;
    var W = w - pad * 2, H = h - pad * 2;
    var pts = series.map(function (d, i) {
      var x = pad + (series.length === 1 ? W / 2 : i / (series.length - 1) * W);
      var y = pad + H - (d.v == null || isNaN(d.v) ? 0 : (d.v - minV) / range * H);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var zeroY = pad + H - (0 - minV) / range * H;
    var zeroLine = minV < 0 && maxV > 0 ? '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + zeroY + '" y2="' + zeroY + '" stroke="var(--cm-ink-100)" stroke-dasharray="3,3"/>' : '';
    return zeroLine +
      '<polyline points="' + pts + '" fill="none" stroke="var(--cm-brand-400)" stroke-width="1.5"/>' +
      '<text x="' + pad + '" y="' + (pad - 6) + '" fill="var(--cm-ink-500)" font-size="10">max ' + maxV.toFixed(3) + '</text>' +
      '<text x="' + pad + '" y="' + (h - 8) + '" fill="var(--cm-ink-500)" font-size="10">min ' + minV.toFixed(3) + '</text>' +
      '<text x="' + (w - pad) + '" y="' + (h - 8) + '" fill="var(--cm-ink-500)" font-size="10" text-anchor="end">n=' + series.length + '</text>';
  }

  async function renderDailyChart() {
    var box = el('mm-chart-daily'); if (!box) return;
    var mid = modelMonitorState.modelId; if (!mid) { box.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">load err</div>'; return; }
      var series = (res.daily_series || []).map(function (d) { return { t: d.date, v: d.spread }; });
      box.innerHTML = '<svg viewBox="0 0 600 220" style="width:100%;height:100%">' + svgLine(series, 600, 220, 30) + '</svg>';
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + escText(e.message) + '</div>';
    }
  }

  async function renderRegimeChart() {
    var box = el('mm-chart-regime'); if (!box) return;
    var mid = modelMonitorState.modelId; if (!mid) { box.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">load err</div>'; return; }
      var rows = (res.regime_breakdown || []).filter(function (r) { return r.regime_flag; });
      if (!rows.length) { box.innerHTML = '<div class="muted" style="text-align:center;padding:40px">holdout 期无分组数据</div>'; return; }
      var w = 600, h = 220, pad = 40;
      var barW = (w - pad * 2) / (rows.length * 2 + rows.length);
      var allV = rows.flatMap(function (r) { return [r.top_avg || 0, r.bot_avg || 0]; });
      var maxAbs = Math.max.apply(null, allV.map(Math.abs)) || 0.01;
      var midY = h / 2;
      var svg = '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + midY + '" y2="' + midY + '" stroke="var(--cm-ink-100)"/>';
      rows.forEach(function (r, i) {
        var x = pad + i * barW * 3;
        var hTop = Math.abs(r.top_avg || 0) / maxAbs * (h / 2 - pad);
        var hBot = Math.abs(r.bot_avg || 0) / maxAbs * (h / 2 - pad);
        var topY = (r.top_avg || 0) >= 0 ? midY - hTop : midY;
        var botY = (r.bot_avg || 0) >= 0 ? midY - hBot : midY;
        svg += '<rect x="' + x + '" y="' + topY + '" width="' + barW + '" height="' + hTop + '" fill="var(--stock-down)"/>';
        svg += '<rect x="' + (x + barW) + '" y="' + botY + '" width="' + barW + '" height="' + hBot + '" fill="var(--stock-up)"/>';
        svg += '<text x="' + (x + barW) + '" y="' + (h - pad / 2) + '" text-anchor="middle" font-size="11" fill="var(--cm-ink-700)">' + r.regime_flag + '</text>';
        svg += '<text x="' + (x + barW / 2) + '" y="' + (topY - 2) + '" text-anchor="middle" font-size="9" fill="var(--cm-ok-500)">' + ((r.top_avg || 0) * 100).toFixed(1) + '%</text>';
        svg += '<text x="' + (x + barW * 1.5) + '" y="' + (botY + hBot + 10) + '" text-anchor="middle" font-size="9" fill="var(--cm-bad-500)">' + ((r.bot_avg || 0) * 100).toFixed(1) + '%</text>';
      });
      svg += '<text x="' + pad + '" y="' + (pad / 2) + '" font-size="10" fill="var(--cm-ok-500)">■ top-decile</text>';
      svg += '<text x="' + (pad + 80) + '" y="' + (pad / 2) + '" font-size="10" fill="var(--cm-bad-500)">■ bot-decile</text>';
      box.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:100%">' + svg + '</svg>';
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + escText(e.message) + '</div>';
    }
  }

  async function renderFeatureImportance() {
    var box = el('mm-chart-fi'); if (!box) return;
    var mid = modelMonitorState.modelId; if (!mid) { box.innerHTML = ''; return; }
    try {
      var res = await api('/api/rec/model-performance?model_id=' + encodeURIComponent(mid));
      if (!res || !res.ok) { box.innerHTML = '<div class="muted">load err</div>'; return; }
      var fi = (res.meta && res.meta.feature_importance) || [];
      if (!fi.length) { box.innerHTML = '<div class="muted">无 feature importance 数据</div>'; return; }
      var maxV = Math.max.apply(null, fi.map(function (x) { return x.importance || 0; })) || 1;
      var html = '<div style="display:grid;grid-template-columns:auto 1fr auto;gap:4px 10px;font-size:12px;align-items:center">';
      fi.forEach(function (x, i) {
        var pct = (x.importance / maxV) * 100;
        var zh = x.label_cn || featureLabels.features[x.name] || '';
        html += '<div class="muted">#' + (i + 1) + '</div>';
        html += '<div style="display:flex;align-items:center;gap:8px">' +
          '<div style="font-weight:500;white-space:nowrap;min-width:240px"><span style="color:var(--cm-ink-900)">' + escText(x.name) + '</span>' +
          (zh ? '<span style="color:var(--cm-ink-500);font-weight:400;margin-left:6px">（' + escText(zh) + '）</span>' : '') + '</div>' +
          '<div style="flex:1;background:var(--cm-ink-50);height:14px;border-radius:3px;overflow:hidden">' +
          '<div style="width:' + pct.toFixed(1) + '%;height:100%;background:var(--cm-brand-500)"></div>' +
          '</div></div>';
        html += '<div style="color:var(--cm-ink-500);font-family:monospace">' + Math.round(x.importance) + '</div>';
      });
      html += '</div>';
      box.innerHTML = html;
    } catch (e) {
      box.innerHTML = '<div class="muted">error: ' + escText(e.message) + '</div>';
    }
  }

  global.ModelMonitorWidget = {
    loadModelMonitor: loadModelMonitor,
    renderModelMonitor: renderModelMonitor,
    renderModelComparison: renderModelComparison,
    renderPromotionGate: renderPromotionGate,
    renderTdxValidation: renderTdxValidation,
    renderMetricsCards: renderMetricsCards,
    renderDailyChart: renderDailyChart,
    renderRegimeChart: renderRegimeChart,
    renderFeatureImportance: renderFeatureImportance,
    labelFeature: labelFeature,
    labelModelId: labelModelId,
    setFeatureLabels: setFeatureLabels,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.ModelMonitorWidget = global.ModelMonitorWidget;
  }
})(typeof window !== 'undefined' ? window : this);
