/* ============================================================
   institution-scorecard.js — 机构评分卡 widget
   负责机构实力评分 / 可跟性评分 / 机构评分参数面板
   API: window.InstitutionScorecardWidget.mountScorecard(ids, deps)
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

  var formatUtils = (typeof globalThis !== 'undefined' && globalThis.WidgetFormatUtils) || global.WidgetFormatUtils;
  if (!formatUtils) throw new Error('WidgetFormatUtils missing');

  function fmtNum(value, digits) {
    return formatUtils.formatNumber(value, digits == null ? 0 : digits);
  }

  function fmtScore(value) {
    return formatUtils.formatNumber(value, 1);
  }

  function fmtGain(value) {
    var formatted = formatUtils.formatPercent(value, 1, false, true, '-');
    if (formatted === '-') return formatted;
    return '<span class="' + (Number(value) >= 0 ? 'gain-pos' : 'gain-neg') + '">' + formatted + '</span>';
  }

  function resolveDeps(deps) {
    deps = deps || {};
    return {
      esc: pickFn(deps, 'esc', escText),
      fmt: pickFn(deps, 'fmt', fmtNum),
      fmtGain: pickFn(deps, 'fmtGain', fmtGain),
      scoreNum: pickFn(deps, 'scoreNum', fmtScore),
      priorityPoolTag: pickFn(deps, 'priorityPoolTag', function (pool) {
        return '<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;background:var(--cm-ink-50);color:var(--cm-ink-500);font-size:11px;font-weight:700">' + escText(pool || '未分池') + '</span>';
      }),
      api: pickFn(deps, 'api', null),
    };
  }

  function renderScorecardMiniCard(label, value, sub, d) {
    return '<div class="scorecard-mini-card">' +
      '<div class="scorecard-mini-label">' + d.esc(label || '-') + '</div>' +
      '<div class="scorecard-mini-value">' + value + '</div>' +
      '<div class="scorecard-mini-sub">' + d.esc(sub || '-') + '</div>' +
      '</div>';
  }

  function renderConfidenceSummary(items, d) {
    if (!items || !items.length) return '-';
    return items.map(function (item) {
      return (item.confidence || '未标注') + ' ' + d.fmt(item.total || 0);
    }).join(' · ');
  }

  function renderInstFrameworkRules(framework, deps) {
    var d = resolveDeps(deps);
    var formulaCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">固定口径</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item"><b>综合优先分</b>：' + d.esc(framework.formula || '-') + '</div>' +
      '<div class="score-rule-item"><b>' + d.esc((framework.effective_forecast || {}).label || '生效预测分') + '</b>：' + d.esc((framework.effective_forecast || {}).formula || '-') + '</div>' +
      '<div class="score-rule-item">' + d.esc((framework.effective_forecast || {}).meaning || '-') + '</div>' +
      '</div>' +
      '</div>';
    var capsCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">封顶与门槛</div>' +
      '<div class="score-rule-list">' + (framework.caps || []).map(function (item) {
        return '<div class="score-rule-item">' + d.esc(item) + '</div>';
      }).join('') + '</div>' +
      '</div>';
    var overlay = framework.external_overlay || {};
    var overlayCard = (overlay.summary || (overlay.items || []).length)
      ? '<div class="score-rule-card">' +
        '<div class="score-rule-title">' + d.esc(overlay.label || '外部关注叠加层') + '</div>' +
        '<div class="score-rule-list">' +
        (overlay.summary ? '<div class="score-rule-item">' + d.esc(overlay.summary) + '</div>' : '') +
        (overlay.items || []).map(function (item) {
          return '<div class="score-rule-item">' + d.esc(item) + '</div>';
        }).join('') +
        '</div>' +
        '</div>'
      : '';
    var poolRows = (framework.pools || []).map(function (item) {
      return '<tr><td>' + d.priorityPoolTag(item.label) + '</td><td>' + d.esc(item.gate || '-') + '</td><td>' + d.esc(item.meaning || '-') + '</td></tr>';
    }).join('');
    var poolCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">池子规则</div>' +
      '<table class="score-pool-table"><thead><tr><th>池子</th><th>门槛</th><th>含义</th></tr></thead><tbody>' + poolRows + '</tbody></table>' +
      '</div>';
    return '<div class="score-rule-grid">' + formulaCard + capsCard + overlayCard + '</div>' + poolCard;
  }

  function renderInstScorecardStats(stats, deps) {
    var d = resolveDeps(deps);
    if (!stats) return '';
    var summary = stats.summary || {};
    var typeTop = Array.isArray(stats.type_top) ? stats.type_top : [];
    var hintTop = Array.isArray(stats.hint_top) ? stats.hint_top : [];
    var confidence = stats.confidence || {};

    var summaryCards = '<div class="scorecard-stats-grid">' +
      renderScorecardMiniCard('机构样本', d.fmt(summary.total || 0), '当前已生成画像的机构数', d) +
      renderScorecardMiniCard('买入口径', d.fmt(summary.buy_basis_count || 0), 'fallback ' + d.fmt(summary.fallback_basis_count || 0), d) +
      renderScorecardMiniCard('高置信机构分', d.fmt(summary.quality_high_conf_count || 0), '均分 ' + d.scoreNum(summary.avg_quality_score), d) +
      renderScorecardMiniCard('高置信可跟分', d.fmt(summary.follow_high_conf_count || 0), '均分 ' + d.scoreNum(summary.avg_followability_score), d) +
      renderScorecardMiniCard('高分机构', d.fmt(summary.quality_strong_count || 0), 'quality ≥ 65', d) +
      renderScorecardMiniCard('高可跟机构', d.fmt(summary.followability_strong_count || 0), 'followability ≥ 65', d) +
      renderScorecardMiniCard('安全跟随机构', d.fmt(summary.safe_follow_inst_count || 0), '均安全样本 ' + d.fmt(summary.avg_safe_follow_event_count || 0), d) +
      renderScorecardMiniCard('平均溢价', d.fmtGain(summary.avg_premium_pct), '均买入样本 ' + d.fmt(summary.avg_buy_event_count || 0), d) +
      '</div>';

    var typeTable = typeTop.length
      ? '<div class="score-rule-card">' +
        '<div class="score-rule-title">机构类型分布</div>' +
        '<table class="score-pool-table"><thead><tr><th>类型</th><th>机构数</th><th>均机构分</th><th>均可跟分</th></tr></thead><tbody>' +
        typeTop.map(function (item) {
          return '<tr><td>' + d.esc(item.inst_type || '未分类') + '</td><td>' + d.fmt(item.total) + '</td><td>' + d.scoreNum(item.avg_quality_score) + '</td><td>' + d.scoreNum(item.avg_followability_score) + '</td></tr>';
        }).join('') +
        '</tbody></table>' +
        '</div>'
      : '';

    var confidenceCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">置信分层</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item"><b>机构分</b>：' + renderConfidenceSummary(confidence.quality || [], d) + '</div>' +
      '<div class="score-rule-item"><b>可跟分</b>：' + renderConfidenceSummary(confidence.followability || [], d) + '</div>' +
      '</div>' +
      '</div>';

    var hintCard = hintTop.length
      ? '<div class="score-rule-card">' +
        '<div class="score-rule-title">可跟性提示分布</div>' +
        '<div class="score-rule-list">' + hintTop.map(function (item) {
          return '<div class="score-rule-item">' + d.esc(item.followability_hint || '未标注') + ' · ' + d.fmt(item.total) + ' 家</div>';
        }).join('') + '</div>' +
        '</div>'
      : '';

    return '<div class="score-rule-card" style="margin-bottom:12px">' +
      '<div class="score-rule-title">当前样本摘要</div>' +
      '<div class="scorecard-note">机构评分卡现在会直接展示真实机构画像分布，帮助判断这套机构评分与可跟性评分在当前样本中的覆盖、置信和主流提示结构。</div>' +
      '</div>' +
      summaryCards +
      '<div class="score-rule-grid" style="margin-top:12px">' + typeTable + confidenceCard + '</div>' +
      hintCard;
  }

  function renderStockFrameworkLayer(layer, deps) {
    var d = resolveDeps(deps);
    return '<div class="score-framework-card">' +
      '<div class="score-framework-head">' +
      '<div class="score-framework-title">' + d.esc(layer.label || '-') + '</div>' +
      '<span class="score-framework-weight">' + d.esc(String(layer.weight || 0)) + '%</span>' +
      '</div>' +
      '<div class="score-framework-role">' + d.esc(layer.role || '-') + '</div>' +
      '<div class="score-framework-summary">' + d.esc(layer.summary || '-') + '</div>' +
      '<div class="score-framework-list">' + (layer.items || []).map(function (item) {
        return '<div class="score-framework-item">' + d.esc(item) + '</div>';
      }).join('') + '</div>' +
      '</div>';
  }

  function renderStockFrameworkRules(framework, deps) {
    var d = resolveDeps(deps);
    var formulaCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">固定口径</div>' +
      '<div class="score-rule-list">' +
      '<div class="score-rule-item"><b>综合优先分</b>：' + d.esc(framework.formula || '-') + '</div>' +
      '<div class="score-rule-item"><b>' + d.esc((framework.effective_forecast || {}).label || '生效预测分') + '</b>：' + d.esc((framework.effective_forecast || {}).formula || '-') + '</div>' +
      '<div class="score-rule-item">' + d.esc((framework.effective_forecast || {}).meaning || '-') + '</div>' +
      '</div>' +
      '</div>';
    var capsCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">封顶与门槛</div>' +
      '<div class="score-rule-list">' + (framework.caps || []).map(function (item) {
        return '<div class="score-rule-item">' + d.esc(item) + '</div>';
      }).join('') + '</div>' +
      '</div>';
    var overlay = framework.external_overlay || {};
    var overlayCard = (overlay.summary || (overlay.items || []).length)
      ? '<div class="score-rule-card">' +
        '<div class="score-rule-title">' + d.esc(overlay.label || '外部关注叠加层') + '</div>' +
        '<div class="score-rule-list">' +
        (overlay.summary ? '<div class="score-rule-item">' + d.esc(overlay.summary) + '</div>' : '') +
        (overlay.items || []).map(function (item) {
          return '<div class="score-rule-item">' + d.esc(item) + '</div>';
        }).join('') +
        '</div>' +
        '</div>'
      : '';
    var poolRows = (framework.pools || []).map(function (item) {
      return '<tr><td>' + d.priorityPoolTag(item.label) + '</td><td>' + d.esc(item.gate || '-') + '</td><td>' + d.esc(item.meaning || '-') + '</td></tr>';
    }).join('');
    var poolCard = '<div class="score-rule-card">' +
      '<div class="score-rule-title">池子规则</div>' +
      '<table class="score-pool-table"><thead><tr><th>池子</th><th>门槛</th><th>含义</th></tr></thead><tbody>' + poolRows + '</tbody></table>' +
      '</div>';
    return '<div class="score-rule-grid">' + formulaCard + capsCard + overlayCard + '</div>' + poolCard;
  }

  function renderScoreParamCard(containerId, framework, config, defaults, deps) {
    var d = resolveDeps(deps);
    var items = framework?.editable_factors || [];
    return '<div class="score-rule-card" id="' + d.esc(containerId) + '">' +
      '<div class="score-rule-title">' + d.esc(framework?.title || '评分参数') + '</div>' +
      '<div class="score-param-list">' + items.map(function (item) {
        var current = config && config[item.key] != null ? config[item.key] : (defaults && defaults[item.key] != null ? defaults[item.key] : 0);
        var def = defaults && defaults[item.key] != null ? defaults[item.key] : 0;
        return '<div class="score-param-item">' +
          '<div class="score-param-head">' +
          '<div class="score-param-title">' + d.esc(item.label || item.key) + '</div>' +
          '<input type="number" class="score-input" data-key="' + d.esc(item.key) + '" value="' + d.esc(String(current)) + '" min="0" max="100">' +
          '</div>' +
          '<div class="score-param-desc">' + d.esc(item.description || '-') + '</div>' +
          '<div class="score-param-sub">默认 ' + d.esc(String(def)) + (item.source ? ' · 来源 ' + d.esc(item.source) : '') + '</div>' +
          '</div>';
      }).join('') + '</div>' +
      '</div>';
  }

  function resolveContainerIds(containerIds) {
    containerIds = containerIds || {};
    if (typeof containerIds === 'string') {
      return {
        frameworkId: containerIds,
        statsId: containerIds + '-stats',
        paramsId: containerIds + '-params',
      };
    }
    return {
      frameworkId: containerIds.frameworkId || 'instScorecardFramework',
      statsId: containerIds.statsId || 'instScorecardStats',
      paramsId: containerIds.paramsId || 'instScorecardParams',
    };
  }

  async function fetchJson(api, path) {
    if (api) return api(path);
    var r = await fetch(path, { cache: 'no-store', headers: { 'Content-Type': 'application/json' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  }

  async function mountScorecard(containerIds, deps) {
    deps = deps || {};
    var d = resolveDeps(deps);
    var ids = resolveContainerIds(containerIds);
    var frameworkEl = document.getElementById(ids.frameworkId);
    var statsEl = document.getElementById(ids.statsId);
    var paramsEl = document.getElementById(ids.paramsId);
    if (!frameworkEl || !statsEl || !paramsEl) return;

    frameworkEl.innerHTML = '<div class="score-rule-card"><div class="score-rule-title">机构评分双框架</div><div class="scorecard-note">加载机构评分卡中...</div></div>';
    statsEl.innerHTML = '';
    paramsEl.innerHTML = '';

    try {
      var rs = await Promise.all([
        fetchJson(d.api, '/api/inst/scoring/framework/institution'),
        fetchJson(d.api, '/api/inst/scoring/config/institution'),
        fetchJson(d.api, '/api/inst/scoring/framework/followability'),
        fetchJson(d.api, '/api/inst/scoring/config/followability')
      ]);
      var instFw = rs[0]?.ok ? (rs[0].data || {}) : {};
      var instStats = rs[0]?.ok ? (rs[0].stats || {}) : {};
      var instCfg = rs[1]?.ok ? rs[1] : {};
      var followFw = rs[2]?.ok ? (rs[2].data || {}) : {};
      var followCfg = rs[3]?.ok ? rs[3] : {};

      frameworkEl.innerHTML =
        '<div style="background:var(--cm-bg);border:1px solid var(--cm-ink-100);border-radius:12px;padding:14px 16px;margin-bottom:14px;font-size:12px;color:var(--cm-ink-700);line-height:1.7">' +
        '<div style="font-size:14px;font-weight:700;color:var(--cm-ink-900);margin-bottom:6px">机构评分双框架</div>' +
        '机构页当前同时维护“机构实力评分”和“可跟性评分”。前者回答机构信号本身好不好，后者回答普通跟随者是否容易复现。' +
        '</div>' +
        '<div class="score-framework-grid">' + (instFw.layers || []).map(function (layer) { return renderStockFrameworkLayer(layer, d); }).join('') + '</div>' +
        renderStockFrameworkRules(instFw, d) +
        '<div class="score-framework-grid" style="margin-top:14px">' + (followFw.layers || []).map(function (layer) { return renderStockFrameworkLayer(layer, d); }).join('') + '</div>' +
        renderStockFrameworkRules(followFw, d);

      statsEl.innerHTML = renderInstScorecardStats(instStats, d);

      paramsEl.innerHTML =
        '<div class="score-rule-grid">' +
        renderScoreParamCard('instInstitutionParams', instFw, instCfg.config || {}, instCfg.defaults || {}, d) +
        renderScoreParamCard('instFollowabilityParams', followFw, followCfg.config || {}, followCfg.defaults || {}, d) +
        '</div>';
    } catch (e) {
      frameworkEl.innerHTML = '<div class="score-rule-card"><div class="score-rule-title">机构评分双框架</div><div class="scorecard-note">加载失败: ' + d.esc(e.message || e) + '</div></div>';
    }
  }

  global.InstitutionScorecardWidget = {
    mountScorecard: mountScorecard,
    renderScoreParamCard: renderScoreParamCard,
    renderInstFrameworkRules: renderInstFrameworkRules,
    renderInstScorecardStats: renderInstScorecardStats,
    renderStockFrameworkLayer: renderStockFrameworkLayer,
    renderStockFrameworkRules: renderStockFrameworkRules,
  };
  if (typeof globalThis !== 'undefined') {
    globalThis.InstitutionScorecardWidget = global.InstitutionScorecardWidget;
  }
})(typeof window !== 'undefined' ? window : this);
