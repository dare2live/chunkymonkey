// data-view.js — 数据页 (P2 v2: 接管所有数据获取 + 数据源监控)
// 职责: 把外面的数据拉进来 (raw + dim 层). 不做 fact/mart 计算.
//
// UI 三段:
//   A. 数据源 registry 卡 (4 卡, /api/data_sources/*)
//   B. capability → 源 映射表
//   C. 数据更新 (智能更新 + 9 个 sync_* step) + 实时日志

(function () {
  if (window.DataView) return;

  const STATE_DOTS = { ok: '🟢', degraded: '🟡', down: '🔴', unknown: '⚪' };
  const STATE_COLORS = { ok: '#0a0', degraded: '#d80', down: '#d33', unknown: '#888' };

  let _state = {
    sources: [],
    capabilities: [],
    routes: [],
    capFilter: '',
    routeFilter: '',
  };
  let _initialized = false;
  let _logBuffer = [];
  let _pollTimer = null;
  let _lastLogId = 0;

  // ---- helpers ----
  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function qs(id) { return document.getElementById(id); }
  function logLine(msg, level) {
    const ts = new Date().toLocaleTimeString();
    const prefix = level === 'err' ? '❌ ' : level === 'ok' ? '✓ ' : '';
    const line = `[${ts}] ${prefix}${msg}`;
    _logBuffer.push(line);
    if (_logBuffer.length > 500) _logBuffer.shift();
    const el = qs('ds-live-log');
    if (el) {
      el.textContent = _logBuffer.join('\n');
      el.scrollTop = el.scrollHeight;
    }
  }

  async function fetchJSON(url, options) {
    options = options || {};
    // 默认 8s timeout, 防 hang
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), options.timeout || 8000);
    options.signal = ctrl.signal;
    try {
      const r = await fetch(url, options);
      if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
      return await r.json();
    } finally {
      clearTimeout(tid);
    }
  }

  // ---- 数据源 卡 ----
  function renderSourceCards() {
    const root = qs('ds-source-cards');
    if (!root) return;
    if (!_state.sources.length) {
      root.innerHTML = '<div class="muted" style="padding:24px;text-align:center;grid-column:1/-1;font-size:12px">没有注册的数据源 (后端 data_sources/ registry 未加载?)</div>';
      return;
    }
    root.innerHTML = _state.sources.map(src => {
      const h = src.health || {};
      const dot = STATE_DOTS[h.state || 'unknown'];
      const color = STATE_COLORS[h.state || 'unknown'];
      const repoLink = src.repo_url
        ? `<a href="${esc(src.repo_url)}" target="_blank" style="color:var(--cm-ink-500);font-size:11px;text-decoration:none">repo↗</a>`
        : '';
      const tele = src.telemetry || {};
      const teleLine = tele.call_count > 0
        ? `${tele.call_count} 次调用` + (tele.fail_count ? ` / ${tele.fail_count} 失败` : '')
          + (tele.avg_latency_ms ? ` · ${tele.avg_latency_ms}ms 均延` : '')
        : '尚未通过 registry 调用';
      return `
        <div class="panel ds-source-card" data-source="${esc(src.name)}" style="padding:14px;border-left:4px solid ${color}">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <div style="font-weight:700;font-size:14px">${dot} ${esc(src.display_name || src.name)}</div>
            <span style="font-size:11px;color:var(--cm-ink-500)">优先 ${src.priority}</span>
          </div>
          <div style="font-size:11px;color:var(--cm-ink-500);margin-bottom:6px">
            <strong>${src.capabilities.length}</strong> 类数据 · ${esc(teleLine)}
          </div>
          <div style="font-size:11px;color:${color};margin-bottom:8px;min-height:14px">${esc(h.notes || '未检')}</div>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            <button class="chip chip-outline ds-detail-btn" data-source="${esc(src.name)}" style="font-size:11px;padding:3px 8px">详情</button>
            <button class="chip chip-outline ds-health-btn" data-source="${esc(src.name)}" style="font-size:11px;padding:3px 8px">healthcheck</button>
            ${repoLink}
          </div>
          <div class="ds-detail-panel" style="display:none;margin-top:10px;padding-top:8px;border-top:1px dashed var(--cm-ink-100);font-size:11px;max-height:300px;overflow-y:auto"></div>
        </div>
      `;
    }).join('');

    root.querySelectorAll('.ds-detail-btn').forEach(b => b.addEventListener('click', () => toggleDetail(b.dataset.source)));
    root.querySelectorAll('.ds-health-btn').forEach(b => b.addEventListener('click', () => doHealthcheck(b.dataset.source)));
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
      const h = await fetchJSON('/api/data_sources/' + encodeURIComponent(name) + '/health');
      logLine(`${name}: ${h.state} ${h.notes || ''} (${h.avg_latency_ms || '-'}ms)`, h.state === 'ok' ? 'ok' : 'err');
      const src = _state.sources.find(s => s.name === name);
      if (src) src.health = h;
      renderSourceCards();
    } catch (e) {
      logLine(`${name}: ${e.message}`, 'err');
    }
  }

  // ---- 业务数据 → 通道表 (主表, current + target 双列) ----
  function renderRoutesTable() {
    const root = qs('ds-routes-table');
    if (!root) return;
    const list = _state.routes.filter(r => {
      if (!_state.routeFilter) return true;
      const f = _state.routeFilter.toLowerCase();
      const cur = r.current || {};
      return [r.data_name, cur.source, cur.protocol, r.raw_table, r.step_id, r.notes].some(
        v => (v || '').toLowerCase().includes(f)
      );
    });
    if (!list.length) {
      root.innerHTML = '<div class="muted" style="padding:14px;text-align:center;font-size:12px">无匹配</div>';
      return;
    }
    const SRC_COLOR = { tdxhub: '#0a7', aif10: '#26b', akshare: '#888', datacenter_web: '#a40' };
    const STATUS_BADGE = {
      connected: '<span style="color:#0a7;font-weight:600">✓ 已接</span>',
      transitional: '<span style="color:#a40;font-weight:600" title="datacenter-web 直连过渡, P6 计划迁妙想">⚠ 过渡</span>',
      pending: '<span style="color:#888" title="registry 声明, 待接通">⏳ pending</span>',
    };
    root.innerHTML = `
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead style="position:sticky;top:0;background:var(--cm-surface);z-index:1">
          <tr style="border-bottom:1px solid var(--cm-ink-100);color:var(--cm-ink-500);font-size:11px">
            <th style="text-align:left;padding:6px 8px">业务数据</th>
            <th style="text-align:left;padding:6px 8px">表</th>
            <th style="text-align:left;padding:6px 8px">当前通道</th>
            <th style="text-align:left;padding:6px 8px">状态</th>
            <th style="text-align:left;padding:6px 8px">迁移目标</th>
            <th style="text-align:left;padding:6px 8px">频率</th>
          </tr>
        </thead>
        <tbody>
        ${list.map(r => {
          const cur = r.current || {};
          const tgt = r.target;
          const color = SRC_COLOR[cur.source] || '#666';
          const targetCell = tgt
            ? `<span style="color:${SRC_COLOR[tgt.source] || '#666'}">→ ${esc(tgt.source)}</span> <small class="muted">${esc(tgt.phase || '')}</small>`
            : '<span class="muted" style="font-size:11px">—</span>';
          return `
            <tr style="border-bottom:1px dotted var(--cm-bg-100)" title="${esc(r.notes || '')}">
              <td style="padding:5px 8px;font-weight:600">${esc(r.data_name)}<br><small class="muted" style="font-weight:400;font-size:10px">${esc(r.step_id || '')}</small></td>
              <td style="padding:5px 8px;color:var(--cm-ink-500);font-size:11px"><code>${esc(r.raw_table)}</code></td>
              <td style="padding:5px 8px">
                <span style="color:${color};font-weight:600">${esc(cur.source)}</span><br>
                <small class="muted" style="font-size:10px"><code>${esc(cur.protocol)}</code></small>
              </td>
              <td style="padding:5px 8px;font-size:11px">${STATUS_BADGE[cur.status] || cur.status || ''}</td>
              <td style="padding:5px 8px;font-size:11px">${targetCell}</td>
              <td style="padding:5px 8px;color:var(--cm-ink-500);font-size:11px">${esc(r.freshness)}</td>
            </tr>
          `;
        }).join('')}
        </tbody>
      </table>
    `;
  }

  // ---- capability 表 (高级/诊断用) ----
  function renderCapTable() {
    const root = qs('ds-capability-table');
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
        <thead style="position:sticky;top:0;background:var(--cm-surface);z-index:1">
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

  // ---- 数据更新调度 (从工作台搬来的智能更新 + 9 sync 单步) ----
  // step 元数据: id 必须跟 backend updater step_id 完全一致
  const SYNC_STEPS = [
    { id: 'sync_raw', name: '十大流通股东', cap: 'top_free_holders', source: 'em_datacenter' },
    { id: 'match_inst', name: '匹配跟踪机构', cap: '(派生)', source: '内部' },
    { id: 'sync_market_data', name: 'K 线日/月', cap: 'kline_daily', source: 'tdxhub' },
    { id: 'sync_industry', name: '行业 (申万)', cap: 'industry_sw', source: 'tdxhub' },
    { id: 'sync_financial', name: '财务 (gpcw)', cap: 'financial_gpcw_8q', source: 'tdxhub' },
    { id: 'sync_qfii', name: 'QFII 持仓', cap: 'qfii_holding_quarterly', source: 'em_datacenter' },
    { id: 'sync_margin', name: '融资融券', cap: 'margin_daily', source: 'em_datacenter' },
    { id: 'sync_surveys', name: '机构调研', cap: 'institution_survey', source: 'em_datacenter' },
    { id: 'sync_lhb', name: '龙虎榜', cap: 'lhb_daily', source: 'em_datacenter' },
  ];

  function renderStepGrid() {
    const root = qs('ds-step-grid');
    if (!root) return;
    root.innerHTML = SYNC_STEPS.map(s => `
      <div class="ds-step-chip" data-step="${esc(s.id)}" title="${esc(s.cap)} via ${esc(s.source)}">
        <div style="font-weight:600;font-size:12px;margin-bottom:2px">${esc(s.name)}</div>
        <div style="color:var(--cm-ink-500);font-size:10px">${esc(s.source)}</div>
      </div>
    `).join('');
    root.querySelectorAll('.ds-step-chip').forEach(el => {
      el.addEventListener('click', () => triggerStep(el.dataset.step, el));
    });
  }

  async function triggerStep(stepId, el) {
    logLine(`触发 step: ${stepId} ...`);
    if (el) el.style.background = 'var(--cm-bg-100)';
    try {
      const j = await fetchJSON('/api/inst/update/step/' + encodeURIComponent(stepId), { method: 'POST' });
      if (j.ok === false) {
        logLine(`${stepId}: ${j.message || 'busy'}`, 'err');
      } else {
        logLine(`${stepId}: 已触发 (链路 ${(j.steps || []).length} 步)`, 'ok');
        startPolling();
      }
    } catch (e) {
      logLine(`${stepId}: ${e.message}`, 'err');
    }
    if (el) setTimeout(() => { el.style.background = ''; }, 800);
  }

  async function smartUpdate() {
    logLine('智能更新触发...');
    try {
      const j = await fetchJSON('/api/inst/update/smart', { method: 'POST' });
      if (j.ok === false) {
        logLine('智能更新: ' + (j.message || 'busy'), 'err');
      } else {
        logLine('智能更新启动: ' + JSON.stringify(j).slice(0, 200), 'ok');
        startPolling();
      }
    } catch (e) {
      logLine('失败: ' + e.message, 'err');
    }
  }
  async function dataOnly() {
    logLine('运行 data 组 (sync_raw → ... → sync_lhb)...');
    try {
      const j = await fetchJSON('/api/inst/update/sync', { method: 'POST' });
      if (j.ok === false) {
        logLine('data 组: ' + (j.message || 'busy'), 'err');
      } else {
        logLine('data 组启动 OK', 'ok');
        startPolling();
      }
    } catch (e) {
      logLine('失败: ' + e.message, 'err');
    }
  }
  async function stopUpdate() {
    logLine('请求停止…');
    try {
      const j = await fetchJSON('/api/inst/update/stop', { method: 'POST' });
      logLine('停止: ' + JSON.stringify(j).slice(0, 100), j.ok === false ? 'err' : 'ok');
      stopPolling();
    } catch (e) { logLine('停止失败: ' + e.message, 'err'); }
  }

  // 轮询更新进度 (只在用户触发更新后开)
  function startPolling() {
    if (_pollTimer) return;
    logLine('开始监听后端日志…');
    _pollTimer = setInterval(async () => {
      try {
        const j = await fetchJSON('/api/inst/update/status', { timeout: 5000 });
        // logs 是 [{id, ts, msg}, ...] (递增 id)
        if (Array.isArray(j.logs)) {
          j.logs.forEach(item => {
            const id = item.id || 0;
            if (id > _lastLogId) {
              _lastLogId = id;
              const ts = item.ts ? new Date(item.ts).toLocaleTimeString() : '';
              _logBuffer.push(`[${ts}] ${item.msg || ''}`);
              if (_logBuffer.length > 500) _logBuffer.shift();
            }
          });
          const el = qs('ds-live-log');
          if (el) { el.textContent = _logBuffer.slice(-200).join('\n'); el.scrollTop = el.scrollHeight; }
        }
        if (j && j.running === false) {
          stopPolling();
          logLine('更新完成 (后端 running=false)', 'ok');
        }
      } catch (e) { /* silent, polling 容错 */ }
    }, 3000);
  }
  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  // ---- 主入口 ----
  async function init() {
    console.log('[DataView] init starting');
    logLine('数据页初始化…');

    // 立刻渲染 step grid (不依赖网络)
    renderStepGrid();

    // 并行拉 3 个 endpoint (list / capabilities / data_routes)
    const tasks = [
      fetchJSON('/api/data_sources/list').then(r => {
        _state.sources = r.sources || [];
        logLine(`数据源加载: ${_state.sources.length} 个`, 'ok');
        renderSourceCards();
      }).catch(e => {
        console.error('[DataView] list fetch fail', e);
        logLine('加载数据源列表失败: ' + e.message, 'err');
        const root = qs('ds-source-cards');
        if (root) root.innerHTML = `<div class="panel" style="padding:18px;grid-column:1/-1;border-left:4px solid #d33"><strong>加载失败</strong><br><code style="font-size:11px">${esc(e.message)}</code></div>`;
      }),
      fetchJSON('/api/data_sources/data_routes').then(r => {
        _state.routes = r.routes || [];
        const stats = r.stats || {};
        const statsEl = qs('ds-route-stats');
        if (statsEl) {
          const bs = stats.by_status || {};
          const ok = bs.connected || 0;
          const tr = bs.transitional || 0;
          const pd = bs.pending || 0;
          statsEl.textContent = `共 ${stats.total || 0}: ✓${ok} 已接 · ⚠${tr} 过渡 · ⏳${pd} 待接`;
        }
        logLine(`数据通道加载: ${_state.routes.length} 类`, 'ok');
        renderRoutesTable();
      }).catch(e => {
        console.error('[DataView] routes fetch fail', e);
        logLine('加载数据通道失败: ' + e.message, 'err');
        const root = qs('ds-routes-table');
        if (root) root.innerHTML = `<div class="muted" style="padding:18px;color:#d33"><strong>加载失败</strong>: ${esc(e.message)}</div>`;
      }),
      fetchJSON('/api/data_sources/capabilities').then(r => {
        _state.capabilities = r.capabilities || [];
        renderCapTable();
      }).catch(e => {
        console.error('[DataView] caps fetch fail', e);
      }),
    ];
    await Promise.allSettled(tasks);

    // 绑事件 (幂等)
    const routeFilterEl = qs('ds-route-filter');
    if (routeFilterEl) routeFilterEl.oninput = e => { _state.routeFilter = e.target.value || ''; renderRoutesTable(); };
    const filterEl = qs('ds-cap-filter');
    if (filterEl) filterEl.oninput = e => { _state.capFilter = e.target.value || ''; renderCapTable(); };
    const refreshCaps = qs('ds-refresh-caps');
    if (refreshCaps) refreshCaps.onclick = async () => {
      try {
        const r = await fetchJSON('/api/data_sources/capabilities');
        _state.capabilities = r.capabilities || [];
        renderCapTable();
      } catch (e) { logLine('刷新失败: ' + e.message, 'err'); }
    };
    const smart = qs('ds-smart-update'); if (smart) smart.onclick = smartUpdate;
    const dataOnlyBtn = qs('ds-data-only'); if (dataOnlyBtn) dataOnlyBtn.onclick = dataOnly;
    const stopBtn = qs('ds-stop-update'); if (stopBtn) stopBtn.onclick = stopUpdate;

    console.log('[DataView] init done');
  }

  window.DataView = {
    show() {
      if (!_initialized) {
        _initialized = true;
        // setTimeout 让 DOM 切换 view 后再 init, 防止 active class 还没切到 view-data 上
        setTimeout(() => { init().catch(e => console.error('[DataView] init err', e)); }, 0);
      }
    },
    refresh: init,
  };

  console.log('[DataView] module loaded');
})();
