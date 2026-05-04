/* security-identity.js - shared market code, Xueqiu link and display helper */
(function (global) {
  'use strict';

  function esc(value) {
    return (value == null ? '' : String(value))
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function cleanCode(value) {
    return String(value || '').replace(/[^0-9]/g, '').slice(0, 6);
  }

  function normalizeMarket(value) {
    var v = String(value || '').trim().toUpperCase();
    if (v === 'XSHG' || v === 'SHSE' || v === 'SSE' || v === 'SH') return 'SH';
    if (v === 'XSHE' || v === 'SZSE' || v === 'SZ') return 'SZ';
    if (v === 'BSE' || v === 'BJSE' || v === 'BJ') return 'BJ';
    return '';
  }

  function inferMarket(code) {
    var c = cleanCode(code);
    if (!c) return 'NA';
    if (c[0] === '6') return 'SH';
    if ('0123'.indexOf(c[0]) >= 0) return 'SZ';
    if ('489'.indexOf(c[0]) >= 0) return 'BJ';
    return 'NA';
  }

  function marketPrefix(security) {
    security = security || {};
    return normalizeMarket(security.exchange || security.market || security.market_code) || inferMarket(security.stock_code || security.stockCode || security.code);
  }

  function formatMarketCode(security) {
    security = security || {};
    var code = cleanCode(security.stock_code || security.stockCode || security.code);
    var prefix = marketPrefix(security);
    return (prefix || 'NA') + ': ' + (code || '------');
  }

  function xueqiuUrl(security) {
    security = security || {};
    var code = cleanCode(security.stock_code || security.stockCode || security.code);
    var prefix = marketPrefix(security);
    if (!code || prefix === 'NA') return '';
    return 'https://xueqiu.com/S/' + prefix + code;
  }

  function nameOf(security) {
    security = security || {};
    return security.stock_name || security.stockName || security.name || security.security_name || '名称待补';
  }

  function renderCodeLink(security, opts) {
    opts = opts || {};
    var text = formatMarketCode(security);
    var href = xueqiuUrl(security);
    var title = href ? '打开雪球 ' + text : '无法判断市场前缀: ' + text;
    var clickAttr = opts.stopPropagation ? ' onclick="event.stopPropagation()"' : '';
    if (!href) {
      return '<span class="cm-security-code cm-security-code-na" title="' + esc(title) + '">' + esc(text) + '</span>';
    }
    return '<a class="cm-security-code" href="' + esc(href) + '" target="_blank" rel="noopener" title="' + esc(title) + '"' + clickAttr + '>' + esc(text) + '</a>';
  }

  function renderSecurityIdentity(security, opts) {
    opts = opts || {};
    security = security || {};
    var classes = ['cm-security-identity'];
    if (opts.className) classes.push(opts.className);
    var nameClass = opts.nameClass || 'cm-security-name';
    var name = nameOf(security);
    var code = cleanCode(security.stock_code || security.stockCode || security.code);
    var action = opts.clickAction
      ? ' onclick="' + opts.clickAction + '"'
      : '';
    var nameHtml = '<span class="' + esc(nameClass) + '"' + action + '>' + esc(name) + '</span>';
    return '<div class="' + classes.join(' ') + '" data-security-code="' + esc(code) + '">' +
      nameHtml +
      '<div class="cm-security-meta">' + renderCodeLink(security, { stopPropagation: opts.stopPropagation !== false }) + '</div>' +
      '</div>';
  }

  global.SecurityIdentity = {
    cleanCode: cleanCode,
    inferMarket: inferMarket,
    marketPrefix: marketPrefix,
    formatMarketCode: formatMarketCode,
    xueqiuUrl: xueqiuUrl,
    renderCodeLink: renderCodeLink,
    renderSecurityIdentity: renderSecurityIdentity,
    nameOf: nameOf,
  };
})(typeof window !== 'undefined' ? window : this);
