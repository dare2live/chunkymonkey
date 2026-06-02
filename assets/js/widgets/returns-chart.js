/**
 * Returns chart helper.
 *
 * Exported as window.ReturnsChartWidget = { buildReturnsSvg } for reuse in
 * app.js and contract smoke tests.
 */
(function (global) {
  'use strict';

  function sampleValues(values, maxPoints) {
    if (!Array.isArray(values) || values.length <= maxPoints) return (values || []).slice();
    var step = Math.ceil(values.length / maxPoints);
    var sampled = [];
    var sum = 0;
    var count = 0;
    for (var i = 0; i < values.length; i += 1) {
      sum += Number(values[i] || 0);
      count += 1;
      if (count === step || i === values.length - 1) {
        sampled.push(sum / count);
        sum = 0;
        count = 0;
      }
    }
    return sampled;
  }

  function buildReturnsSvg(gains, width, height) {
    if (!gains || gains.length < 2) {
      return '<div class="muted" style="height:' + height + 'px;display:flex;align-items:center;justify-content:center;font-size:11px">数据不足</div>';
    }
    var vals = [];
    for (var i = 0; i < gains.length; i += 1) {
      vals.push(Number((gains[i] && gains[i].gain_30d) || 0));
    }
    vals = sampleValues(vals, 60);
    var mn = vals[0];
    var mx = vals[0];
    var maxIdx = 0;
    var minIdx = 0;
    for (var j = 1; j < vals.length; j += 1) {
      var v = vals[j];
      if (v > mx) { mx = v; maxIdx = j; }
      if (v < mn) { mn = v; minIdx = j; }
    }
    if (mx === mn) { mx = mn + 1; }
    var pad = 4;
    var w = width - pad * 2;
    var h = height - pad * 2;
    var denom = mx - mn;
    var pathD = '';
    var prevX = 0;
    var prevY = 0;
    var maxPoint = null;
    var minPoint = null;
    for (var k = 0; k < vals.length; k += 1) {
      var curr = vals[k];
      var x = pad + (vals.length === 1 ? 0 : k / (vals.length - 1) * w);
      var y = pad + (1 - (curr - mn) / denom) * h;
      if (k === 0) {
        pathD = 'M ' + x.toFixed(1) + ' ' + y.toFixed(1);
      } else {
        var cpx = (prevX + x) / 2;
        pathD += ' C ' + cpx.toFixed(1) + ' ' + prevY.toFixed(1) + ', ' + cpx.toFixed(1) + ' ' + y.toFixed(1) + ', ' + x.toFixed(1) + ' ' + y.toFixed(1);
      }
      if (k === maxIdx) maxPoint = { x: x, y: y, value: curr };
      if (k === minIdx) minPoint = { x: x, y: y, value: curr };
      prevX = x;
      prevY = y;
    }
    var zeroY = (pad + (1 - (0 - mn) / denom) * h).toFixed(1);
    return '<svg viewBox="0 0 ' + width + ' ' + height + '" style="width:100%;height:' + height + 'px">' +
      '<line x1="' + pad + '" y1="' + zeroY + '" x2="' + (width - pad) + '" y2="' + zeroY + '" stroke="var(--cm-ink-100)" stroke-dasharray="3"/>' +
      '<path d="' + pathD + '" fill="none" stroke="var(--cm-brand-400)" stroke-width="1.5"/>' +
      '<circle cx="' + maxPoint.x.toFixed(1) + '" cy="' + maxPoint.y.toFixed(1) + '" r="3" fill="var(--stock-down)"/>' +
      '<text x="' + (maxPoint.x + 4).toFixed(1) + '" y="' + (maxPoint.y - 3).toFixed(1) + '" font-size="9" fill="var(--stock-down)">+' + maxPoint.value.toFixed(1) + '%</text>' +
      '<circle cx="' + minPoint.x.toFixed(1) + '" cy="' + minPoint.y.toFixed(1) + '" r="3" fill="var(--stock-up)"/>' +
      '<text x="' + (minPoint.x + 4).toFixed(1) + '" y="' + (minPoint.y + 10).toFixed(1) + '" font-size="9" fill="var(--stock-up)">' + minPoint.value.toFixed(1) + '%</text>' +
      '</svg>';
  }

  global.ReturnsChartWidget = {
    buildReturnsSvg: buildReturnsSvg,
  };
})(typeof window !== 'undefined' ? window : globalThis);
