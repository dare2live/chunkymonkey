// data-view.js — 数据页 (P2)
// 接 /api/data_sources/* registry endpoints
// 4 张数据源卡 + capability 映射表 + 更新调度 + 实时日志

(function () {
  if (window.DataView) return;

  const STATE_COLORS = { ok: '#0a0', degraded: '#d80', down: '#d33', unknown: '#888' };
  const STATE_DOTS = { ok: '🟢', degraded: '🟡', down: '🔴', unknown: '⚪' };

  let _state = {
    sources: [],
    capabilities: [],
    capFilter: '',
    healthLoading: false,
  };

  // -------- helpers ---------
  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function logLine(msg) {
    const el = document.getElementById('ds-live-log');
    if (!el) return;
    const ts = new Date().toLocaleTimeString();
    el.textContent += `\n[${ts}] ${msg}`;
    el.scrollTop = el.scrollHeight;
  }

  // -------- API ---------
  async function fetchList() {
    const r = await fetch('/api/data_sources/list');
    if (!r.ok) throw new Error('list ' + r.status);
    return r.json();
  }
  async function fetchCapabilities() {
    const r = await fetch('/api/data_sources/capabilities');
    if (!r.ok) throw new Error('caps ' + r.status);
    return r.json();
  }
  async function fetchHealth(name) {
    const r = await fetch('/api/data_sources/' + encodeURIComponent(name) + '/health');
    if (!r.ok) throw new Error('health ' + r.status);
    return r.json();
  }

  // -------- 渲染数据源卡 ---------
  function renderSourceCards() {
    const root = document.getElementById('ds-source-cards');
    if (!root) return;
    if (!_state.sources.length) {
      root.innerHTML = '<div class="muted" style="padding:24px;text-align:center;grid-column:1/-1">没有数据源</div>';
      return;
    }
    root.innerHTML = _state.sources.map(src => {
      const h = src.health || {};
      const dot = STATE_DOTS[h.state || 'unknown'];
      const color = STATE_COLORS[h.state || 'unknown'];
      const repoLink = src.repo_url
        ? `<a href="${esc(src.repo_url)}" target="_blank" style="color:var(--cm-ink-500);font-size:11px">repo↗</a>`
        : '';
      const tele = src.telemetry || {};
      return `
        <div class="panel ds-source-card" data-source="${esc(src.name)}" style="padding:14px;border-left:4px solid ${color}">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <div style="font-weight:700;font-size:14px">${dot} ${esc(src.display_name || src.name)}</div>
            <span style="font-size:11px;color:var(--cm-ink-500)">优先 ${src.priority}</span>
          </div>
          <div style="font-size:11px;color:var(--cm-ink-500);margin-bottom:8px">
            ${src.capabilities.length} 类数据 · ${tele.call_count || 0} 次调用 ${tele.fail_count ? '/ '+tele.fail_count+' 失败' : ''}
            ${tele.avg_latency_ms ? '· '+tele.avg_latency_ms+'ms' : ''}
          </div>
          <div style="font-size:11px;color:${color};margin-bottom:8px;min-height:14px">${esc(h.notes || '未检')}</div>
          <div style="display:flex;gap:6px;align-items:center">
            <button class="chip chip-outline ds-detail-btn" data-source="${esc(src.name)}" style="font-size:11px;padding:3px 8px">详情</button>
            <button class="chip chip-outline ds-health-btn" data-source="${esc(src.name)}" style="font-size:11px;padding:3px 8px">healthcheck</button>
            ${repoLink}
          </div>
          <div class="ds-detail-panel" style="display:none;margin-top:10px;padding-top:8px;border-top:1px dashed var(--cm-ink-100);font-size:11px"></div>
        </div>
      `;
    }).join('');

    // 绑事件
    root.querySelectorAll('.ds-detail-btn').forEach(b => {
      b.addEventListener('click', () => toggleDetail(b.dataset.source));
    });
    root.querySelectorAll('.ds-health-btn').forEach(b => {
      b.addEventListener('click', () => doHealthcheck(b.dataset.source));
    });
  }

  function toggleDetail(name) {
    const card = document.querySelector('.ds-source-card[data-source="' + name + '"]');
    if (!card) return;
    const panel = card.querySelector('.ds-detail-panel');
    if (panel.style.display === 'none' || !panel.style.display) {
      const src = _state.sources.find(s => s.name === name);
      if (!src) return;
      panel.innerHTML = src.capabilities.map(c => `
        <div style="padding:3px 0;border-bottom:1px dotted var(--cm-bg-100)">
          <code style="background:var(--cm-bg-100);padding:1px 4px;border-radius:3px">${esc(c.name)}</code>
          <span style="color:var(--cm-ink-500)">${esc(c.freshness)}</span> · ${esc(c.description)}
        </div>
      `).join('');
      panel.style.display = 'block';
    } else {
      panel.style.display = 'none';
    }
  }

  async function doHealthcheck(name) {
    logLine(`healthcheck: ${name} ...`);
    try {
      const h = await fetchHealth(name);
      logLine(`  ${name}: ${h.state} ${h.notes || ''} (${h.avg_latency_ms || '-'}ms)`);
      // 更新 UI 缓存
      const src = _state.sources.find(s => s.name === name);
      if (src) src.health = h;
      renderSourceCards();
    } catch (e) {
      logLine(`  ${name}: 失败 ${e.message}`);
    }
  }

  // -------- 渲染 capability 表 ---------
  function renderCapTable() {
    const root = document.getElementById('ds-capability-table');
    if (!root) return;
    const list = _state.capabilities.filter(c => {
      if (!_state.capFilter) return true;
      const f = _state.capFilter.toLowerCase();
      return c.capability.toLowerCase().includes(f)
          || (c.description || '').toLowerCase().includes(f)
          || (c.fallback_chain || []).some(s => s.includes(f));
    });
    if (!list.length) {
      root.innerHTML = '<div class="muted" style="padding:14px;text-align:center;font-size:12px">无匹配</div>';
      return;
    }
    root.innerHTML = `
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead>
          <tr style="border-bottom:1px solid var(--cm-ink-100);color:var(--cm-ink-500);font-size:11px">
            <th style="text-align:left;padding:6px 8px">capability</th>
            <th style="text-align:left;padding:6px 8px">freshness</th>
            <th style="text-align:left;padding:6px 8px">主源</th>
            <th style="text-align:left;padding:6px 8px">fallback chain</th>
            <th style="text-align:left;padding:6px 8px">描述</th>
          </tr>
        </thead>
        <tbody>
        ${list.map(c => `
          <tr style="border-bottom:1px dotted var(--cm-bg-100)">
            <td style="padding:5px 8px"><code>${esc(c.capability)}</code></td>
            <td style="padding:5px 8px;color:var(--cm-ink-500)">${esc(c.freshness)}</td>
            <td style="padding:5px 8px;font-weight:600">${esc(c.primary_source)}</td>
            <td style="padding:5px 8px;color:var(--cm-ink-500)">${esc((c.fallback_chain || []).join(' → '))}</td>
            <td style="padding:5px 8px">${esc(c.description)}</td>
          </tr>
        `).join('')}
        </tbody>
      </table>
    `;
  }

  // -------- 数据更新 step (复用 updater 接口) ---------
  async function loadStepGrid() {
    const root = document.getElementById('ds-step-grid');
    if (!root) return;
    // 用 /api/inst/update/list 列 step (假设存在; 否则只显示几个 hardcoded)
    const dataSteps = [
      { id: 'sync_raw', name: '十大流通股东', cap: 'top_free_holders' },
      { id: 'sync_market_data', name: 'K 线', cap: 'kline_daily' },
      { id: 'sync_industry', name: '行业', cap: 'industry_sw' },
      { id: 'sync_financial', name: '财务 gpcw', cap: 'financial_gpcw_8q' },
      { id: 'sync_lhb', name: '龙虎榜', cap: 'lhb_daily' },
      { id: 'sync_qfii', name: 'QFII', cap: 'qfii_holding_quarterly' },
      { id: 'sync_margin', name: '融资融券', cap: 'margin_daily' },
      { id: 'sync_surveys', name: '机构调研', cap: 'institution_survey' },
    ];
    root.innerHTML = dataSteps.map(s => `
      <div class="ds-step-chip" data-step="${esc(s.id)}" style="padding:6px 8px;border:1px solid var(--cm-ink-100);border-radius:4px;cursor:pointer;font-size:11px;transition:background 0.15s">
        <div style="font-weight:600;margin-bottom:2px">${esc(s.name)}</div>
        <div style="color:var(--cm-ink-500);font-size:10px">${esc(s.cap)}</div>
      </div>
    `).join('');
    root.querySelectorAll('.ds-step-chip').forEach(el => {
      el.addEventListener('click', () => triggerStep(el.dataset.step));
      el.addEventListener('mouseenter', () => { el.style.background = 'var(--cm-bg-50)'; });
      el.addEventListener('mouseleave', () => { el.style.background = ''; });
    });
  }

  async function triggerStep(stepId) {
    logLine(`触发 step: ${stepId} ...`);
    try {
      const r = await fetch('/api/inst/update/step/' + encodeURIComponent(stepId), { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      logLine(`  ${stepId}: ${r.ok ? 'OK' : 'FAIL'} ${JSON.stringify(j).slice(0, 120)}`);
    } catch (e) {
      logLine(`  ${stepId}: 异常 ${e.message}`);
    }
  }

  async function smartUpdate() {
    logLine('智能更新触发...');
    try {
      const r = await fetch('/api/inst/update/smart', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      logLine('  智能更新完成: ' + JSON.stringify(j).slice(0, 200));
    } catch (e) { logLine('  失败: ' + e.message); }
  }
  async function dataOnly() {
    logLine('运行 data 组...');
    try {
      const r = await fetch('/api/inst/update/sync', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      logLine('  data 组完成: ' + JSON.stringify(j).slice(0, 200));
    } catch (e) { logLine('  失败: ' + e.message); }
  }
  async function stopUpdate() {
    logLine('请求停止更新…');
    try {
      const r = await fetch('/api/inst/update/stop', { method: 'POST' });
      logLine('  ' + (r.ok ? '已停止' : '停止失败'));
    } catch (e) { logLine('  失败: ' + e.message); }
  }

  // -------- 主入口 ---------
  async function init() {
    try {
      const [listResp, capsResp] = await Promise.all([fetchList(), fetchCapabilities()]);
      _state.sources = listResp.sources || [];
      _state.capabilities = capsResp.capabilities || [];
      renderSourceCards();
      renderCapTable();
      loadStepGrid();
      logLine(`已加载 ${_state.sources.length} 源 / ${_state.capabilities.length} capability`);
    } catch (e) {
      logLine('初始化失败: ' + e.message);
    }

    // 绑全局按钮
    const filterEl = document.getElementById('ds-cap-filter');
    if (filterEl) filterEl.addEventListener('input', e => {
      _state.capFilter = e.target.value || '';
      renderCapTable();
    });
    const refreshCaps = document.getElementById('ds-refresh-caps');
    if (refreshCaps) refreshCaps.addEventListener('click', async () => {
      const r = await fetchCapabilities();
      _state.capabilities = r.capabilities || [];
      renderCapTable();
    });
    document.getElementById('ds-smart-update')?.addEventListener('click', smartUpdate);
    document.getElementById('ds-data-only')?.addEventListener('click', dataOnly);
    document.getElementById('ds-stop-update')?.addEventListener('click', stopUpdate);
  }

  let _initialized = false;
  window.DataView = {
    show() {
      if (!_initialized) { init(); _initialized = true; }
    },
    refresh: init,
  };
})();
