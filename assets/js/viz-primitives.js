/* viz-primitives.js - small SVG helpers for dense utility screens */
(function (global) {
  'use strict';

  function esc(value) {
    return (value == null ? '' : String(value))
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function num(value, fallback) {
    var n = Number(value);
    return Number.isFinite(n) ? n : (fallback || 0);
  }

  function sparkline(series, opts) {
    opts = opts || {};
    var values = (series || []).map(function (v) { return num(v, null); }).filter(function (v) { return v != null; });
    var w = opts.width || 120;
    var h = opts.height || 32;
    if (!values.length) return '<svg class="cm-mini-chart" viewBox="0 0 ' + w + ' ' + h + '"><text x="6" y="' + (h / 2 + 4) + '">NA</text></svg>';
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = max === min ? 1 : max - min;
    var step = values.length > 1 ? w / (values.length - 1) : w;
    var points = values.map(function (v, i) {
      var x = i * step;
      var y = h - ((v - min) / span) * (h - 4) - 2;
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    return '<svg class="cm-mini-chart" viewBox="0 0 ' + w + ' ' + h + '"><polyline points="' + points + '" fill="none" stroke="var(--cm-accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></svg>';
  }

  function miniBar(value, max, opts) {
    if (typeof max === 'object') {
      opts = max;
      max = 100;
    }
    opts = opts || {};
    var denominator = Math.max(1, num(max, 100));
    var raw = num(value, 0);
    var pct = Math.max(0, Math.min(100, raw / denominator * 100));
    var label = opts.label == null ? String(raw) : opts.label;
    var color = opts.color || 'var(--cm-brand-500)';
    return '<div class="cm-mini-bar" title="' + esc(label) + '"><div class="cm-mini-bar-fill" style="width:' + pct.toFixed(1) + '%;background:' + esc(color) + '"></div></div>';
  }

  function stackedBar(parts, opts) {
    opts = opts || {};
    var total = (parts || []).reduce(function (s, p) { return s + num(p.value, 0); }, 0);
    if (!total) return '<div class="cm-stacked-bar cm-stacked-bar-empty"><span></span></div>';
    return '<div class="cm-stacked-bar">' + parts.map(function (p) {
      var pct = num(p.value, 0) / total * 100;
      var cls = 'cm-stacked-bar-seg' + (p.className ? ' ' + p.className : '');
      var color = p.color || 'var(--cm-brand-500)';
      return '<span class="' + esc(cls) + '" style="width:' + pct.toFixed(1) + '%;background:' + esc(color) + '" title="' + esc((p.label || '') + ' ' + p.value) + '"></span>';
    }).join('') + '</div>';
  }

  function heatmap(cells, opts) {
    opts = opts || {};
    return '<div class="cm-health-heatmap">' + (cells || []).map(function (c) {
      return '<button class="cm-heat-cell cm-heat-' + esc(c.tone || 'info') + '" data-key="' + esc(c.key || '') + '" title="' + esc(c.title || '') + '"><span>' + esc(c.label || c.key || '') + '</span><b>' + esc(c.value == null ? '-' : c.value) + '</b></button>';
    }).join('') + '</div>';
  }

  function gateRail(steps) {
    return '<div class="cm-gate-rail">' + (steps || []).map(function (s) {
      return '<div class="cm-gate-step cm-gate-' + esc(s.tone || 'info') + '"><span>' + esc(s.label || '') + '</span><b>' + esc(s.status || 'NA') + '</b></div>';
    }).join('') + '</div>';
  }

  function timeline(events) {
    return '<div class="cm-timeline">' + (events || []).map(function (e) {
      return '<span class="cm-timeline-dot cm-timeline-dot-' + esc(e.tone || 'info') + '" title="' + esc((e.date || '') + ' ' + (e.label || '') + ' ' + (e.detail || '')) + '"></span>';
    }).join('') + '</div>';
  }

  function waterfall(parts) {
    return stackedBar(parts, {});
  }

  global.CMViz = {
    sparkline: sparkline,
    stackedBar: stackedBar,
    miniBar: miniBar,
    heatmap: heatmap,
    timeline: timeline,
    gateRail: gateRail,
    waterfall: waterfall,
  };
})(typeof window !== 'undefined' ? window : this);
