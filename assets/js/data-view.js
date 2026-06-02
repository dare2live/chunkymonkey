// data-view.js — 数据页 (P2 v2: 接管所有数据获取 + 数据源监控)
// 职责: 把外面的数据拉进来 (raw + dim 层). 不做 fact/mart 计算.
//
// UI 三段:
//   A. 数据源 registry 卡 (4 卡, /api/data_sources/*)
//   B. capability → 源 映射表
//   C. 数据更新 (智能更新 + 9 个 sync_* step) + 实时日志

(function () {
  if (window.CMDataView) return;

  const global = typeof window !== 'undefined' ? window : globalThis;
  const STATE_LABELS = { ok: 'OK', degraded: 'DEGRADED', down: 'DOWN', unknown: 'UNKNOWN' };
  const STATE_COLORS = { ok: '#0a0', degraded: '#d80', down: '#d33', unknown: '#888' };

  let _state = {
    sources: [],
    capabilities: [],
    routes: [],
    health: null,
    sourceHealth: null,
    schemaVersions: null,
    tdxValidation: null,
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
    const prefix = level === 'err' ? 'FAIL ' : level === 'ok' ? 'OK ' : '';
    const line = `[${ts}] ${prefix}${msg}`;
    _logBuffer.push(line);
    if (_logBuffer.length > 500) _logBuffer.shift();
    const el = qs('ds-live-log');
    if (el) {
      el.textContent = _logBuffer.join('\n');
      el.scrollTop = el.scrollHeight;
    }
  }

  function statusFromCounts(summary) {
    summary = summary || {};
    if ((summary.red || 0) > 0) return 'bad';
    if ((summary.yellow || 0) > 0 || (summary.unknown || 0) > 0) return 'warn';
    if ((summary.green || 0) > 0) return 'ok';
    return 'info';
  }
  function dotHtml(tone) {
    return `<span class="cm-dot cm-dot-${esc(tone || 'info')}"></span>`;
  }
  function pillHtml(label, tone, title) {
    return `<span class="cm-pill cm-pill-${esc(tone || 'info')}"${title ? ` title="${esc(title)}"` : ''}>${dotHtml(tone)}${esc(label)}</span>`;
  }
  function sourcePill(source) {
    const s = source || 'unknown';
    const tone = s.startsWith('tdxhub') || s.startsWith('miaoxiang') || s.startsWith('aif10') ? 'ok'
      : s.startsWith('akshare') ? 'warn'
      : 'info';
    return `<span class="cm-pill cm-pill-${tone}">${esc(s)}</span>`;
  }
  function severityTone(sev) {
    if (sev === 'green' || sev === 'ok') return 'ok';
    if (sev === 'yellow' || sev === 'warn') return 'warn';
    if (sev === 'red' || sev === 'critical' || sev === 'down') return 'bad';
    return 'info';
  }
  function severityLabel(sev) {
    return (sev || 'unknown').toUpperCase();
  }
  function findAssetHealth(tableName) {
    if (!tableName || !_state.health || !Array.isArray(_state.health.items)) return null;
    return _state.health.items.find(x => x.table_name === tableName) || null;
  }
  function buildAssetHealthIndex(items) {
    const index = new Map();
    (items || []).forEach(item => {
      if (item && item.table_name && !index.has(item.table_name)) index.set(item.table_name, item);
    });
    return index;
  }
  function routeHealth(route, asset) {
    const resolvedAsset = asset || findAssetHealth(route && route.raw_table);
    if (!resolvedAsset) return { label: 'UNKNOWN', tone: 'info', title: 'health snapshot 未覆盖' };
    return {
      label: severityLabel(resolvedAsset.severity),
      tone: severityTone(resolvedAsset.severity),
      title: resolvedAsset.issue_summary || '',
      freshness: resolvedAsset.freshness_hours != null ? `${resolvedAsset.freshness_hours.toFixed(1)}h` : '—',
    };
  }
  function fallbackStatus(route) {
    const cur = (route && route.current) || {};
    if (cur.status === 'connected' && !route.target) return { label: '未启用', tone: 'ok' };
    if (route.target) return { label: '迁移/兜底可用', tone: 'warn' };
    if (cur.status === 'pending') return { label: '待接通', tone: 'info' };
    return { label: '按链路兜底', tone: 'info' };
  }

  function renderLinkOverview() {
    const root = qs('ds-link-overview');
    if (!root) return;
    const health = _state.health || {};
    const summary = health.summary || {};
    const tdx = _state.tdxValidation || {};
    const manual = tdx.manual || [];
    const keep = manual.filter(r => r.decision === 'keep').length;
    const watch = manual.filter(r => r.decision === 'watch').length;
    const drop = manual.filter(r => r.decision === 'drop').length;
    const pit = (tdx.pit && tdx.pit.tdx_f10_gpcw_v1 && tdx.pit.tdx_f10_gpcw_v1.violation_rows) || 0;
    const sourceRows = (_state.sourceHealth && _state.sourceHealth.sources) || [];
    const sourceLabel = sourceRows.length
      ? sourceRows.slice(0, 4).map(s => `${s.upstream_source}:${s.green_count || 0}/${s.asset_count || 0}`).join(' · ')
      : '待加载';
    const nodes = [
      ['tdxhub', '主供 K线/复权/gpcw/F10', 'ok', 'miaoxiang 补源；akshare 兜底'],
      ['raw', '原始层', statusFromCounts(summary), `${summary.green || 0} green / ${summary.yellow || 0} yellow / ${summary.red || 0} red`],
      ['fact', '事实层', statusFromCounts((health.by_layer || {}).fact), '机构、行情、财务派生'],
      ['feature panel', '特征面板', keep >= 5 && pit === 0 ? 'ok' : 'warn', `keep ${keep} / watch ${watch} / drop ${drop} · PIT ${pit}`],
      ['model', '模型', 'info', 'champion 默认；challenger shadow'],
      ['recommendation', '推荐', 'info', '正式 TopK 不混入 shadow'],
    ];
    root.innerHTML = `<div class="cm-section-head">
      <div>
        <h3>数据链路总览</h3>
        <p class="muted">tdxhub -> raw -> fact -> feature panel -> model -> recommendation</p>
      </div>
      <span class="cm-muted-note">快照 ${esc(health.snapshot_at || '--')}</span>
    </div>
    <div class="cm-stepper">
      ${nodes.map(n => `<div class="cm-step cm-step-${n[2]}">
        <span>${esc(n[0])}</span>
        <b>${esc(n[1])}</b>
        <small>${esc(n[3])}</small>
      </div>`).join('')}
    </div>
    <div class="cm-status-strip" style="margin-top:12px">
      <div class="cm-status-item cm-status-ok"><span>tdxhub 主供</span><b>K线 / 复权 / gpcw / 股东 F10</b></div>
      <div class="cm-status-item cm-status-info"><span>miaoxiang 补源</span><b>主营 / 估值 / 一致预期 / 调研 / 复杂事件</b></div>
      <div class="cm-status-item"><span>akshare 兜底</span><b>临时不可用和未迁出接口</b></div>
      <div class="cm-status-item"><span>数据源健康</span><b>${esc(sourceLabel)}</b></div>
    </div>`;
  }

  async function fetchJSON(url, options) {
    options = options || {};
    // 数据链路页会并发拉健康快照、source 聚合和 routes；本地 DuckDB
    // 冷启动或浏览器 QA 并发时 8s 偶发过短，保留超时但放宽到 20s。
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), options.timeout || 20000);
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
    const tdxPriority = `
      <div class="panel" style="padding:12px;grid-column:1/-1;border-left:4px solid var(--cm-brand-500)">
        <div style="font-weight:700;font-size:13px;margin-bottom:6px">TDX-first 数据源分工</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--cm-ink-700)">
          <span style="padding:3px 8px;border:1px solid var(--cm-ink-100);border-radius:4px"><b>tdxhub 主供</b>: K线、复权、gpcw、股东 F10</span>
          <span style="padding:3px 8px;border:1px solid var(--cm-ink-100);border-radius:4px"><b>miaoxiang 保留</b>: 主营构成、估值/同行、一致预期、调研、复杂事件</span>
          <span style="padding:3px 8px;border:1px solid var(--cm-ink-100);border-radius:4px"><b>akshare 兜底</b>: 临时不可用和未迁出历史接口</span>
        </div>
      </div>`;
    root.innerHTML = tdxPriority + _state.sources.map(src => {
      const h = src.health || {};
      const tone = h.state === 'ok' ? 'ok' : h.state === 'degraded' ? 'warn' : h.state === 'down' ? 'bad' : 'info';
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
            <div style="display:flex;align-items:center;gap:7px;font-weight:700;font-size:14px">${dotHtml(tone)} ${esc(src.display_name || src.name)}</div>
            <span style="font-size:11px;color:var(--cm-ink-500)">优先 ${src.priority}</span>
          </div>
          <div style="font-size:11px;color:var(--cm-ink-500);margin-bottom:6px">
            <strong>${src.capabilities.length}</strong> 类数据 · ${esc(teleLine)}
          </div>
          <div style="font-size:11px;color:${color};margin-bottom:8px;min-height:14px">${esc(STATE_LABELS[h.state || 'unknown'] || 'UNKNOWN')} · ${esc(h.notes || '未检')}</div>
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

  // ---- 数据审计渲染 ----
  function renderAuditResults(data) {
    const el = qs('ds-audit-results');
    const statsEl = qs('ds-audit-stats');
    if (!el) return;
    const details = data.details || [];
    const issues = [];
    const okList = [];
    details.forEach(r => {
      if ((r.issues || []).length > 0) issues.push(r);
      else okList.push(r);
    });
    if (statsEl) {
      const errMark = data.n_error > 0 ? `<span style="color:#d33">${data.n_error} error</span> · ` : '';
      const warnMark = data.n_warn > 0 ? `<span style="color:#a40">${data.n_warn} warn</span> · ` : '';
      statsEl.innerHTML = `${errMark}${warnMark}<span style="color:#0a7">${data.n_ok} ok</span> / ${data.n_tables} 张 · ${esc(data.run_at || '').slice(0, 16)}`;
    }
    if (!issues.length && !okList.length) {
      el.innerHTML = '<div class="muted" style="padding:10px 0">无审计结果</div>';
      return;
    }
    el.innerHTML = `
      ${issues.length > 0 ? `
        <div style="border-left:3px solid #a40;padding:6px 10px;background:rgba(170,68,0,0.05);border-radius:4px;margin-bottom:8px">
          <div style="font-weight:600;font-size:12px;margin-bottom:6px">${issues.length} 张表有问题</div>
          ${issues.map(r => `
            <div style="padding:4px 0;border-bottom:1px dotted var(--cm-bg-100)">
              <code style="font-size:11px">${esc(r.table)}</code>
              <span class="muted" style="font-size:10px"> · rows=${r.n_rows}</span>
              ${(r.issues || []).map(i => {
                const c = i.level === 'error' ? '#d33' : i.level === 'warn' ? '#a40' : 'var(--cm-ink-500)';
                return `<div style="font-size:10px;color:${c};padding-left:14px">[${esc(i.level)}] ${esc(i.msg)}</div>`;
              }).join('')}
            </div>
          `).join('')}
        </div>
      ` : ''}
      <details style="font-size:11px">
        <summary style="cursor:pointer;font-size:12px;font-weight:600;padding:4px 0">已通过 ${okList.length} 张</summary>
        <div style="max-height:240px;overflow-y:auto;margin-top:4px">
          ${okList.map(r => `
            <div style="padding:2px 0;font-size:11px;font-family:monospace">
              <code>${esc(r.table)}</code>
              <span class="muted" style="float:right">${r.n_rows} rows</span>
            </div>
          `).join('')}
        </div>
      </details>
    `;
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
    const assetIndex = buildAssetHealthIndex((_state.health && _state.health.items) || []);
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
    const STATUS_BADGE = {
      connected: pillHtml('CONNECTED', 'ok'),
      transitional: pillHtml('TRANSITIONAL', 'warn', '已下架或迁移评估中'),
      pending: pillHtml('PENDING', 'info', 'registry 声明, 待接通'),
    };
    root.innerHTML = `
      <div class="cm-table-scroll">
      <table class="cm-route-table">
        <thead>
          <tr>
            <th>业务数据</th>
            <th>落库表</th>
            <th>当前源</th>
            <th>协议</th>
            <th>连接</th>
            <th>fallback 源</th>
            <th>fallback 状态</th>
            <th>健康</th>
            <th>刷新步骤</th>
            <th>修复入口</th>
          </tr>
        </thead>
        <tbody>
        ${list.map(r => {
          const cur = r.current || {};
          const tgt = r.target || {};
          const asset = assetIndex.get(r.raw_table) || null;
          const health = routeHealth(r, asset);
          const fb = fallbackStatus(r);
          const freshness = health.freshness || (asset && asset.freshness_hours != null ? `${asset.freshness_hours.toFixed(1)}h` : '—');
          const repairLabel = r.step_id ? '运行 step' : '查看资产';
          return `
            <tr style="border-bottom:1px dotted var(--cm-bg-100)" title="${esc(r.notes || '')}">
              <td><strong>${esc(r.data_name)}</strong><br><small class="muted">${esc(r.notes || '')}</small></td>
              <td><code>${esc(r.raw_table)}</code></td>
              <td>${sourcePill(cur.source)}</td>
              <td><code>${esc(cur.protocol || '—')}</code></td>
              <td>${STATUS_BADGE[cur.status] || esc(cur.status || '—')}</td>
              <td>${tgt.source ? `${sourcePill(tgt.source)}<br><small class="muted">${esc(tgt.phase || '')}</small>` : '<span class="muted">—</span>'}</td>
              <td>${pillHtml(fb.label, fb.tone)}</td>
              <td>${pillHtml(health.label, health.tone, health.title)}<br><small class="muted">fresh ${esc(freshness)}</small></td>
              <td><code>${esc(r.step_id || '—')}</code></td>
              <td><button class="chip chip-outline chip-sm cm-repair-btn" data-route-repair="${esc(r.step_id || '')}" data-route-table="${esc(r.raw_table || '')}">${repairLabel}</button></td>
            </tr>
          `;
        }).join('')}
        </tbody>
      </table>
      </div>
    `;
    root.querySelectorAll('[data-route-repair]').forEach(btn => {
      btn.addEventListener('click', () => {
        const step = btn.dataset.routeRepair;
        if (step) triggerStep(step, btn);
        else logLine(`查看资产: ${btn.dataset.routeTable || ''}`);
      });
    });
  }

  function renderHealthHeatmap() {
    const root = qs('ds-health-heatmap');
    if (!root) return;
    const health = _state.health || {};
    const summary = health.summary || {};
    const byLayer = health.by_layer || {};
    const total = (summary.green || 0) + (summary.yellow || 0) + (summary.red || 0) + (summary.unknown || 0);
    const layers = Object.keys(byLayer).sort();
    const bar = global.CMViz && global.CMViz.stackedBar
      ? global.CMViz.stackedBar([
        { value: summary.green || 0, color: 'var(--cm-ok-500)', label: `green ${summary.green || 0}` },
        { value: summary.yellow || 0, color: 'var(--cm-warn-500)', label: `yellow ${summary.yellow || 0}` },
        { value: summary.red || 0, color: 'var(--cm-bad-500)', label: `red ${summary.red || 0}` },
        { value: summary.unknown || 0, color: 'var(--cm-ink-300)', label: `unknown ${summary.unknown || 0}` },
      ])
      : '';
    root.innerHTML = `<div class="cm-section-head">
      <div>
        <h3>资产健康热力</h3>
        <p class="muted">按层展示 green/yellow/red/unknown 分布，优先暴露阻塞刷新链路的资产。</p>
      </div>
      <span class="cm-muted-note">${esc(total || 0)} assets</span>
    </div>
    <div class="cm-health-grid">
      <div class="cm-health-card">
        <h4>全局</h4>
        ${bar}
        <div class="cm-health-row"><span>green</span>${global.CMViz ? global.CMViz.miniBar(summary.green || 0, total || 1, { color: 'var(--cm-ok-500)' }) : ''}<b>${summary.green || 0}</b></div>
        <div class="cm-health-row"><span>yellow</span>${global.CMViz ? global.CMViz.miniBar(summary.yellow || 0, total || 1, { color: 'var(--cm-warn-500)' }) : ''}<b>${summary.yellow || 0}</b></div>
        <div class="cm-health-row"><span>red</span>${global.CMViz ? global.CMViz.miniBar(summary.red || 0, total || 1, { color: 'var(--cm-bad-500)' }) : ''}<b>${summary.red || 0}</b></div>
      </div>
      ${layers.map(layer => {
        const row = byLayer[layer] || {};
        const layerTotal = (row.green || 0) + (row.yellow || 0) + (row.red || 0) + (row.unknown || 0);
        return `<div class="cm-health-card">
          <h4>${esc(layer)}</h4>
          <div class="cm-health-row"><span>green</span>${global.CMViz ? global.CMViz.miniBar(row.green || 0, layerTotal || 1, { color: 'var(--cm-ok-500)' }) : ''}<b>${row.green || 0}</b></div>
          <div class="cm-health-row"><span>yellow</span>${global.CMViz ? global.CMViz.miniBar(row.yellow || 0, layerTotal || 1, { color: 'var(--cm-warn-500)' }) : ''}<b>${row.yellow || 0}</b></div>
          <div class="cm-health-row"><span>red</span>${global.CMViz ? global.CMViz.miniBar(row.red || 0, layerTotal || 1, { color: 'var(--cm-bad-500)' }) : ''}<b>${row.red || 0}</b></div>
        </div>`;
      }).join('')}
    </div>`;
  }

  function renderSourcePriority() {
    const root = qs('ds-source-priority');
    if (!root) return;
    const data = _state.sourceHealth || {};
    const rows = data.sources || data.source_priorities || [];
    root.innerHTML = `<div class="cm-section-head">
      <div>
        <h3>源优先级与连通性</h3>
        <p class="muted">展示每个上游当前承载的资产健康分布，用于判断主源和补源是否符合预期。</p>
      </div>
      <span class="cm-muted-note">${rows.length || 0} sources</span>
    </div>
    <div class="cm-table-scroll">
      <table class="cm-compact-table" style="min-width:760px">
        <thead><tr><th>源</th><th>资产数</th><th>green</th><th>yellow</th><th>red</th><th>健康占比</th></tr></thead>
        <tbody>${rows.length ? rows.map(r => {
          const source = r.upstream_source || r.source || r.client_id || 'unknown';
          const green = r.green_count ?? r.green ?? 0;
          const yellow = r.yellow_count ?? r.yellow ?? 0;
          const red = r.red_count ?? r.red ?? 0;
          const total = r.asset_count ?? r.total ?? (green + yellow + red);
          return `<tr>
            <td>${sourcePill(source)}</td>
            <td>${esc(total)}</td>
            <td>${green}</td>
            <td>${yellow}</td>
            <td>${red}</td>
            <td>${global.CMViz ? global.CMViz.stackedBar([
              { value: green, color: 'var(--cm-ok-500)', label: 'green' },
              { value: yellow, color: 'var(--cm-warn-500)', label: 'yellow' },
              { value: red, color: 'var(--cm-bad-500)', label: 'red' },
            ]) : ''}</td>
          </tr>`;
        }).join('') : '<tr><td colspan="6" class="muted" style="padding:16px;text-align:center">源健康聚合待加载</td></tr>'}</tbody>
      </table>
    </div>`;
  }

  function renderFallbackPanel() {
    const root = qs('ds-fallback-panel');
    if (!root) return;
    const health = _state.health || {};
    const sourceHealth = _state.sourceHealth || {};
    const active = health.fallback_active || sourceHealth.fallback_active || [];
    const tiers = health.source_tier_distribution || sourceHealth.source_tier_distribution || {};
    const tierEntries = Object.entries(tiers);
    const tierMax = tierEntries.reduce((max, [, count]) => Math.max(max, Number(count) || 0), 1);
    const routeFallbacks = _state.routes.filter(r => r.target || (r.current && r.current.status !== 'connected')).slice(0, 8);
    root.innerHTML = `<div class="cm-section-head">
      <div>
        <h3>Fallback 状态</h3>
        <p class="muted">正式链路优先使用主源；fallback 只作为可解释的兜底或迁移过渡。</p>
      </div>
      <span class="cm-muted-note">${active.length || routeFallbacks.length || 0} active/transition</span>
    </div>
    <div class="cm-health-grid">
      <div class="cm-health-card">
        <h4>Tier 分布</h4>
        ${tierEntries.length ? tierEntries.map(([tier, count]) => `
          <div class="cm-health-row"><span>${esc(tier)}</span>${global.CMViz ? global.CMViz.miniBar(count || 0, tierMax, { color: 'var(--cm-brand-500)' }) : ''}<b>${esc(count)}</b></div>
        `).join('') : '<div class="muted" style="font-size:12px">暂无 tier 聚合</div>'}
      </div>
      <div class="cm-health-card">
        <h4>过渡队列</h4>
        ${(active.length ? active : routeFallbacks).slice(0, 8).map(x => {
          const dataName = x.data_name || x.table_name || x.asset || x.raw_table || 'unknown';
          const source = (x.current && x.current.source) || x.source || x.upstream_source || 'unknown';
          const target = (x.target && x.target.source) || x.fallback_source || x.target_source || '';
          return `<div style="padding:5px 0;border-bottom:1px dotted var(--cm-bg-100);font-size:12px">
            <strong>${esc(dataName)}</strong>
            <span class="muted"> ${sourcePill(source)} ${target ? '-> ' + sourcePill(target) : ''}</span>
          </div>`;
        }).join('') || '<div class="muted" style="font-size:12px">暂无 fallback 触发</div>'}
      </div>
    </div>`;
  }

  function renderDriftQueue() {
    const root = qs('ds-drift-queue');
    if (!root) return;
    const data = _state.schemaVersions || {};
    const summary = data.summary || {};
    const versions = data.versions || [];
    const driftRows = versions.filter(v => v.drift);
    root.innerHTML = `<div class="cm-section-head">
      <div>
        <h3>Schema Drift 队列</h3>
        <p class="muted">派生/实验表 drift 会影响 data_health_snapshot 的资产级 red/yellow，优先在这里收口。</p>
      </div>
      <span class="cm-muted-note">${summary.drift_count || driftRows.length || 0} drift</span>
    </div>
    <div class="cm-table-scroll">
      <table class="cm-compact-table" style="min-width:760px">
        <thead><tr><th>表</th><th>层</th><th>expected</th><th>actual</th><th>最近重算</th><th>处理</th></tr></thead>
        <tbody>${driftRows.length ? driftRows.slice(0, 12).map(v => `
          <tr>
            <td><code>${esc(v.table_name)}</code></td>
            <td>${esc(v.layer || '—')}</td>
            <td><code>${esc(v.expected_version || '—')}</code></td>
            <td><code>${esc(v.actual_version || 'never_recorded')}</code></td>
            <td class="muted">${esc(v.rebuilt_at ? v.rebuilt_at.slice(0, 16) : '—')}</td>
            <td><button class="chip chip-outline chip-sm" data-drift-table="${esc(v.table_name)}">查看设置</button></td>
          </tr>
        `).join('') : '<tr><td colspan="6" class="muted" style="padding:16px;text-align:center">schema versions 已对齐</td></tr>'}</tbody>
      </table>
    </div>`;
    root.querySelectorAll('[data-drift-table]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (window.App && typeof window.App.showView === 'function') window.App.showView('settings');
        logLine(`schema drift: ${btn.dataset.driftTable || ''}`);
      });
    });
  }

  function renderDataCockpitPanels() {
    renderHealthHeatmap();
    renderSourcePriority();
    renderFallbackPanel();
    renderDriftQueue();
    renderRoutesTable();
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
    { id: 'sync_raw', name: '十大流通股东', cap: 'tdx_f10_holders', source: 'tdxhub' },
    { id: 'match_inst', name: '匹配跟踪机构', cap: '(派生)', source: '内部' },
    { id: 'sync_market_data', name: 'K 线日/月', cap: 'kline_daily', source: 'tdxhub' },
    { id: 'sync_industry', name: '行业 (TDX)', cap: 'tdx_industry', source: 'tdxhub' },
    { id: 'sync_financial', name: '财务 (gpcw)', cap: 'financial_gpcw_8q', source: 'tdxhub' },
    { id: 'sync_qfii', name: 'QFII 持仓', cap: 'qfii_holding_quarterly', source: 'aif10' },
    { id: 'sync_margin', name: '融资融券', cap: 'margin_daily', source: 'akshare' },
    { id: 'sync_surveys', name: '机构调研', cap: 'institution_survey', source: 'aif10' },
    { id: 'sync_lhb', name: '龙虎榜', cap: 'lhb_daily', source: 'aif10' },
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

  // P0-1 fix: 防按钮连点 / 竞态. 触发后 set busy, polling running=false 时 release.
  // backend _is_running 全局锁是后端兜底, 前端 button disable 是第一道防线.
  let _updateBusy = false;
  function _setUpdateButtonsBusy(busy) {
    _updateBusy = busy;
    ['ds-smart', 'ds-data', 'ds-stop'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.disabled = busy && id !== 'ds-stop';   // stop 始终可点
        if (busy && id !== 'ds-stop') el.style.opacity = '0.5';
        else el.style.opacity = '';
      }
    });
    document.querySelectorAll('.ds-step-chip').forEach(el => {
      el.style.pointerEvents = busy ? 'none' : '';
      el.style.opacity = busy ? '0.4' : '';
    });
  }

  async function smartUpdate() {
    if (_updateBusy) { logLine('更新进行中, 请等待或点 停止', 'err'); return; }
    _setUpdateButtonsBusy(true);
    logLine('智能更新触发...');
    try {
      const j = await fetchJSON('/api/inst/update/smart', { method: 'POST' });
      if (j.ok === false) {
        logLine('智能更新: ' + (j.message || 'busy'), 'err');
        _setUpdateButtonsBusy(false);
      } else {
        logLine('智能更新启动: ' + JSON.stringify(j).slice(0, 200), 'ok');
        startPolling();
      }
    } catch (e) {
      logLine('失败: ' + e.message, 'err');
      _setUpdateButtonsBusy(false);
    }
  }
  async function dataOnly() {
    if (_updateBusy) { logLine('更新进行中, 请等待或点 停止', 'err'); return; }
    _setUpdateButtonsBusy(true);
    logLine('运行 data 组 (sync_raw → ... → sync_lhb)...');
    try {
      const j = await fetchJSON('/api/inst/update/sync', { method: 'POST' });
      if (j.ok === false) {
        logLine('data 组: ' + (j.message || 'busy'), 'err');
        _setUpdateButtonsBusy(false);
      } else {
        logLine('data 组启动 OK', 'ok');
        startPolling();
      }
    } catch (e) {
      logLine('失败: ' + e.message, 'err');
      _setUpdateButtonsBusy(false);
    }
  }
  async function stopUpdate() {
    logLine('请求停止…');
    try {
      const j = await fetchJSON('/api/inst/update/stop', { method: 'POST' });
      logLine('停止: ' + JSON.stringify(j).slice(0, 100), j.ok === false ? 'err' : 'ok');
      stopPolling();
      _setUpdateButtonsBusy(false);
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
              _logBuffer.push(`[${ts}] ${item.message || item.msg || ''}`);
              if (_logBuffer.length > 500) _logBuffer.shift();
            }
          });
          const el = qs('ds-live-log');
          if (el) { el.textContent = _logBuffer.slice(-200).join('\n'); el.scrollTop = el.scrollHeight; }
        }
        if (j && j.running === false) {
          stopPolling();
          _setUpdateButtonsBusy(false);
          logLine('更新完成 (后端 running=false)', 'ok');
        }
      } catch (e) {
        // P2-1 fix: polling 错误可见化, 不再 silent 吞 (audit 2026-05-21 push back)
        if (!_pollErrCount) _pollErrCount = 0;
        _pollErrCount++;
        if (_pollErrCount === 1 || _pollErrCount % 5 === 0) {
          logLine('轮询警告 (#' + _pollErrCount + '): ' + (e.message || '网络/超时'), 'err');
        }
      }
    }, 3000);
  }
  let _pollErrCount = 0;
  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  // ---- 主入口 ----
  async function init() {
    console.log('[DataView] init starting');
    logLine('数据页初始化…');

    // 立刻渲染 step grid (不依赖网络)
    renderStepGrid();
    renderLinkOverview();

    // 并行拉 endpoint，顶部总览失败不阻塞原数据页。
    const tasks = [
      fetchJSON('/api/workbench/data-sources').then(r => {
        _state.health = r.asset_health || {};
        _state.sourceHealth = r.source_health || {};
        renderLinkOverview();
        renderDataCockpitPanels();
      }).catch(e => {
        console.error('[DataView] workbench data-source health fail', e);
      }),
      fetchJSON('/api/rec/tdx-feature-validation').then(r => {
        _state.tdxValidation = r && r.ok ? r : null;
        renderLinkOverview();
      }).catch(e => {
        console.error('[DataView] tdx validation fail', e);
      }),
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
          statsEl.textContent = `共 ${stats.total || 0}: ${ok} 已接 · ${tr} 过渡 · ${pd} 待接`;
        }
        logLine(`数据通道加载: ${_state.routes.length} 类`, 'ok');
        renderDataCockpitPanels();
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
      fetchJSON('/api/data_sources/schema_versions').then(r => {
        _state.schemaVersions = r;
        renderDataCockpitPanels();
      }).catch(e => {
        console.error('[DataView] schema versions fail', e);
      }),
    ];
    await Promise.allSettled(tasks);

    // 加载最近一次审计 (启动不阻塞主路径)
    fetchJSON('/api/data_sources/data_audit/last').then(r => {
      if (r.last) renderAuditResults(r.last);
    }).catch(() => {});

    // 绑事件 (幂等)
    const auditBtn = qs('ds-audit-run');
    if (auditBtn) auditBtn.onclick = async () => {
      auditBtn.disabled = true; auditBtn.textContent = '审计中…';
      try {
        const r = await fetchJSON('/api/data_sources/data_audit/run', { method: 'POST', timeout: 30000 });
        renderAuditResults({
          run_at: new Date().toISOString().slice(0, 19),
          n_tables: r.summary.n_tables,
          n_ok: r.summary.n_ok,
          n_warn: r.summary.n_warn,
          n_error: r.summary.n_error,
          details: r.results,
        });
        logLine(`审计完成: ${r.summary.n_ok} ok / ${r.summary.n_warn} warn / ${r.summary.n_error} error`, 'ok');
      } catch (e) {
        logLine('审计失败: ' + e.message, 'err');
      } finally {
        auditBtn.disabled = false; auditBtn.textContent = '立即跑';
      }
    };
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

  window.CMDataView = {
    show() {
      _initialized = true;
      // setTimeout 让 DOM 切换 view 后再 init；重复进入时刷新轻量状态，避免一次失败后卡在加载态。
      setTimeout(() => { init().catch(e => console.error('[DataView] init err', e)); }, 0);
    },
    refresh: init,
  };

  console.log('[DataView] module loaded');
})();
