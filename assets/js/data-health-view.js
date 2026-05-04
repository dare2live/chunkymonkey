/**
 * data-health-view.js — view-data-health 视图渲染逻辑
 *
 * 后端契约: /api/data_health/snapshot, /sources, /asset/{table}, /snapshot/red
 * UI: 全局健康条 + 2 个 tab (健康看板 + 数据源总览) + 单表抽屉
 *
 * 数据加载策略:
 *   - 进入视图时一次拉 /snapshot (含 items + summary + by_layer + fallback_active)
 *   - 切到 sources tab 才拉 /sources (可缓存 5 min)
 *   - 点击行抽屉时 lazy 拉 /asset/{table}
 *   - 点 [刷新] 重拉 /snapshot
 *   - 点 [立即重跑] POST /api/data_health/run-snapshot (Phase E5 后端补)
 */

(function () {
  'use strict';

  const DataHealthView = {
    state: {
      snapshot: null,
      sources: null,            // /sources (缓存)
      sourcesAt: 0,
      clients: null,            // /clients (缓存)
      clientsAt: 0,
      lineage: null,            // /lineage (缓存)
      lineageAt: 0,
      drift: null,              // /drift (缓存)
      driftAt: 0,
      models: null,             // /models (缓存)
      modelsAt: 0,
      activeTab: 'health',      // health | sources | clients | lineage | drift | cleanup | models
      filter: { layer: '', severity: '', search: '' },
    },

    init() {
      // 不在 init 时立刻 fetch — 等 view 真切到 data-health 才加载
      this.bindUI();
    },

    bindUI() {
      // tab 切换
      document.querySelectorAll('.dh-tab-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          const tab = e.currentTarget.getAttribute('data-dh-tab');
          this.switchTab(tab);
        });
      });

      // 刷新按钮
      const refreshBtn = document.getElementById('dh-refresh');
      if (refreshBtn) {
        refreshBtn.addEventListener('click', () => this.load(/* force */ true));
      }
      // 立即重跑 (TODO: 后端 endpoint 还没建, 先 alert 提示)
      const rerunBtn = document.getElementById('dh-run-snapshot');
      if (rerunBtn) {
        rerunBtn.addEventListener('click', () => {
          alert('请运行: python3 backend/scripts/data_health_snapshot.py\n(后端按需触发端点 W0+ 加)');
        });
      }
      // 过滤
      ['dh-filter-layer', 'dh-filter-severity', 'dh-filter-search'].forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('input', () => {
          this.state.filter.layer = document.getElementById('dh-filter-layer').value;
          this.state.filter.severity = document.getElementById('dh-filter-severity').value;
          this.state.filter.search = document.getElementById('dh-filter-search').value.trim().toLowerCase();
          this.renderHealthTable();
        });
      });
      // 抽屉关闭
      const closeBtn = document.getElementById('dh-asset-close');
      const overlay = document.getElementById('dh-asset-overlay');
      const close = () => {
        const drawer = document.getElementById('dh-asset-drawer');
        if (drawer) drawer.style.display = 'none';
      };
      if (closeBtn) closeBtn.addEventListener('click', close);
      if (overlay) overlay.addEventListener('click', close);
    },

    /** 进入视图时调用 (由 app.js 路由派发) */
    async show() {
      if (!this.state.snapshot) {
        await this.load();
      }
      this.renderAll();
    },

    async load(force = false) {
      const tbody = document.getElementById('dh-health-tbody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="9" style="padding:30px;text-align:center" class="muted">加载中...</td></tr>';
      try {
        const r = await fetch('/api/data_health/snapshot');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        this.state.snapshot = await r.json();
        // sources 缓存 5 min
        if (force || !this.state.sources || (Date.now() - this.state.sourcesAt > 5 * 60 * 1000)) {
          const r2 = await fetch('/api/data_health/sources');
          if (r2.ok) {
            this.state.sources = await r2.json();
            this.state.sourcesAt = Date.now();
          }
        }
        this.renderAll();
      } catch (e) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="9" style="padding:30px;text-align:center;color:#c33">加载失败: ${e.message}</td></tr>`;
      }
    },

    renderAll() {
      this.renderGlobalBar();
      this.renderHealthTable();
      this.renderSourcesTable();
    },

    renderGlobalBar() {
      const s = this.state.snapshot;
      if (!s) return;
      const summary = s.summary || {};
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v != null ? v : '--'; };
      set('dh-cnt-green', summary.green || 0);
      set('dh-cnt-yellow', summary.yellow || 0);
      set('dh-cnt-red', summary.red || 0);
      set('dh-cnt-fallback', (s.fallback_active || []).length);
      set('dh-snapshot-at', s.snapshot_at ? this.fmtDateTime(s.snapshot_at) : '无快照, 请先跑脚本');
    },

    renderHealthTable() {
      const s = this.state.snapshot;
      if (!s || !s.items) return;
      const tbody = document.getElementById('dh-health-tbody');
      if (!tbody) return;
      const f = this.state.filter;
      const filtered = s.items.filter((it) => {
        if (f.layer && it.layer !== f.layer) return false;
        if (f.severity && it.severity !== f.severity) return false;
        if (f.search) {
          const blob = `${it.table_name} ${it.writer_module || ''} ${it.upstream_source || ''}`.toLowerCase();
          if (!blob.includes(f.search)) return false;
        }
        return true;
      });
      const stats = document.getElementById('dh-table-stats');
      if (stats) stats.textContent = `显示 ${filtered.length} / ${s.items.length} 张表`;

      // 排序: red 在前, 然后 yellow, 然后按 layer + table_name
      const order = { red: 0, yellow: 1, green: 2, unknown: 3 };
      filtered.sort((a, b) => {
        const sv = (order[a.severity] || 9) - (order[b.severity] || 9);
        if (sv !== 0) return sv;
        if (a.layer !== b.layer) return a.layer.localeCompare(b.layer);
        return a.table_name.localeCompare(b.table_name);
      });

      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="padding:30px;text-align:center" class="muted">无匹配项</td></tr>';
        return;
      }
      tbody.innerHTML = filtered.map((it) => {
        const dot = it.severity === 'red' ? '🔴'
          : it.severity === 'yellow' ? '🟡'
          : it.severity === 'green' ? '🟢'
          : '⚪';
        const fresh = (it.freshness_hours != null) ? `${it.freshness_hours.toFixed(1)}h` : '—';
        const freshClass = (it.freshness_ok === false) ? 'style="color:#c33"' : '';
        const writer = it.writer_module ? `<code style="font-size:11px">${this.shortPath(it.writer_module)}</code>`
          : `<span class="muted" style="font-style:italic">无 writer</span>`;
        const issue = it.issue_summary || '';
        return `<tr class="dh-row" data-table="${this.esc(it.table_name)}" style="cursor:pointer;border-bottom:1px solid var(--cm-ink-50,#f0f0f0)">
          <td style="padding:6px 12px;font-size:14px">${dot}</td>
          <td style="padding:6px 12px"><strong>${this.esc(it.table_name)}</strong></td>
          <td style="padding:6px 12px"><span class="dh-layer-${it.layer}">${it.layer}</span></td>
          <td style="padding:6px 12px;text-align:right">${this.fmtNum(it.row_count)}</td>
          <td style="padding:6px 12px;font-family:monospace;font-size:11px">${it.last_data_date || '—'}</td>
          <td style="padding:6px 12px;text-align:right" ${freshClass}>${fresh}</td>
          <td style="padding:6px 12px;text-align:right;font-size:11px" class="muted">${it.sla_hours || '—'}</td>
          <td style="padding:6px 12px">${writer}</td>
          <td style="padding:6px 12px;font-size:11px;color:#a66">${this.esc(issue)}</td>
        </tr>`;
      }).join('');

      // 行点击 → 抽屉
      tbody.querySelectorAll('.dh-row').forEach((tr) => {
        tr.addEventListener('click', () => {
          const tn = tr.getAttribute('data-table');
          this.openAssetDrawer(tn);
        });
      });
    },

    renderSourcesTable() {
      const tbody = document.getElementById('dh-sources-tbody');
      if (!tbody) return;
      const ss = this.state.sources;
      if (!ss || !ss.sources) {
        tbody.innerHTML = '<tr><td colspan="8" style="padding:30px;text-align:center" class="muted">加载中...</td></tr>';
        return;
      }
      if (ss.sources.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="padding:30px;text-align:center" class="muted">尚未注册任何 upstream_source</td></tr>';
        return;
      }
      const priorityRows = (ss.source_priorities || []).map((p) => {
        return `<tr style="background:var(--cm-brand-50,#eef6ff);border-bottom:1px solid var(--cm-ink-50,#f0f0f0)">
          <td style="padding:6px 12px"><span style="padding:2px 6px;background:#e6f7e6;color:#2a8a2a;border-radius:3px;font-size:11px">业务分工</span></td>
          <td style="padding:6px 12px" colspan="7">
            <b>${this.esc(p.data_domain)}</b>:
            主供 <code>${this.esc(p.preferred_source || '-')}</code>
            ${p.fallback_1 ? ` / 补源 <code>${this.esc(p.fallback_1)}</code>` : ''}
            ${p.fallback_2 ? ` / 兜底 <code>${this.esc(p.fallback_2)}</code>` : ''}
            <span class="muted" style="margin-left:8px;font-size:11px">${this.esc(p.reason || '')}</span>
          </td>
        </tr>`;
      }).join('');
      const sourceRows = ss.sources.map((s) => {
        const tier = s.source_tier;
        const tierBadge = tier === 1 ? '<span style="padding:2px 6px;background:#e6f7e6;color:#2a8a2a;border-radius:3px;font-size:11px">tier 1 主</span>'
          : tier === 2 ? '<span style="padding:2px 6px;background:#fff4d4;color:#a67c00;border-radius:3px;font-size:11px">tier 2 备</span>'
          : tier === 3 ? '<span style="padding:2px 6px;background:#fde8e8;color:#a02a2a;border-radius:3px;font-size:11px">tier 3 兜底</span>'
          : '<span class="muted" style="font-size:11px">派生</span>';
        const maxFresh = s.max_freshness_h != null ? `${s.max_freshness_h.toFixed(1)}h` : '—';
        return `<tr style="border-bottom:1px solid var(--cm-ink-50,#f0f0f0)">
          <td style="padding:6px 12px">${tierBadge}</td>
          <td style="padding:6px 12px"><code style="font-size:12px">${this.esc(s.upstream_source || '—')}</code></td>
          <td style="padding:6px 12px;text-align:right">${s.asset_count}</td>
          <td style="padding:6px 12px;text-align:right;font-family:monospace">${this.fmtNum(s.total_rows)}</td>
          <td style="padding:6px 12px;text-align:right">${s.green_count || 0}</td>
          <td style="padding:6px 12px;text-align:right;color:${(s.yellow_count || 0) > 0 ? '#a67c00' : '#888'}">${s.yellow_count || 0}</td>
          <td style="padding:6px 12px;text-align:right;color:${(s.red_count || 0) > 0 ? '#c33' : '#888'}">${s.red_count || 0}</td>
          <td style="padding:6px 12px;text-align:right;font-size:11px" class="muted">${maxFresh}</td>
        </tr>`;
      }).join('');
      tbody.innerHTML = priorityRows + sourceRows;
    },

    switchTab(tab) {
      this.state.activeTab = tab;
      document.querySelectorAll('.dh-tab-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-dh-tab') === tab);
      });
      const panels = {
        health:   document.getElementById('dh-panel-health'),
        sources:  document.getElementById('dh-panel-sources'),
        clients:  document.getElementById('dh-panel-clients'),
        lineage:  document.getElementById('dh-panel-lineage'),
        drift:    document.getElementById('dh-panel-drift'),
        cleanup:  document.getElementById('dh-panel-cleanup'),
        models:   document.getElementById('dh-panel-models'),
      };
      Object.entries(panels).forEach(([key, el]) => {
        if (el) el.style.display = tab === key ? '' : 'none';
      });

      if (tab === 'sources' && !this.state.sources) {
        fetch('/api/data_health/sources').then((r) => r.json()).then((data) => {
          this.state.sources = data;
          this.state.sourcesAt = Date.now();
          this.renderSourcesTable();
        });
      }
      if (tab === 'clients' && !this.state.clients) {
        fetch('/api/data_health/clients').then((r) => r.json()).then((data) => {
          this.state.clients = data;
          this.state.clientsAt = Date.now();
          this.renderClientsTable();
        });
      }
      if (tab === 'lineage' && !this.state.lineage) {
        fetch('/api/data_health/lineage').then((r) => r.json()).then((data) => {
          this.state.lineage = data;
          this.state.lineageAt = Date.now();
          this.renderLineageTable();
        });
      }
      if (tab === 'drift' && !this.state.drift) {
        fetch('/api/data_health/drift').then((r) => r.json()).then((data) => {
          this.state.drift = data;
          this.state.driftAt = Date.now();
          this.renderDriftTable();
        });
      }
      if (tab === 'cleanup') {
        // cleanup 直接复用 snapshot 的 red_list, 不需要单独 fetch
        this.renderCleanupTable();
      }
      if (tab === 'models' && !this.state.models) {
        fetch('/api/data_health/models').then((r) => r.json()).then((data) => {
          this.state.models = data;
          this.state.modelsAt = Date.now();
          this.renderModelsPanel();
        });
      }
    },

    renderDriftTable() {
      const data = this.state.drift;
      const tbody = document.getElementById('dh-drift-tbody');
      if (!tbody || !data) return;
      const items = (data.items || []);
      const summary = data.summary || {};
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v != null ? v : '--'; };
      set('dh-drift-cnt-ok', summary.ok || 0);
      set('dh-drift-cnt-warn', summary.warn || 0);
      set('dh-drift-cnt-critical', summary.critical || 0);
      set('dh-drift-snap', data.snapshot_at ? this.fmtDateTime(data.snapshot_at) : (data.note || '无快照'));
      if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="padding:30px;text-align:center" class="muted">' + (data.note || '空') + '</td></tr>';
        return;
      }
      tbody.innerHTML = items.map((r) => {
        const sev = r.severity || 'unknown';
        const dot = sev === 'critical' ? '🔴' : sev === 'warn' ? '🟡' : sev === 'ok' ? '🟢' : '⚪';
        const psi = r.psi != null ? r.psi.toFixed(4) : '—';
        const psiColor = sev === 'critical' ? '#c33' : sev === 'warn' ? '#a67c00' : '#2a7a2a';
        return `<tr style="border-bottom:1px solid var(--cm-ink-50,#f0f0f0)">
          <td style="padding:6px 12px">${dot}</td>
          <td style="padding:6px 12px"><code style="font-size:12px">${this.esc(r.feature)}</code></td>
          <td style="padding:6px 12px;text-align:right;font-family:monospace;color:${psiColor};font-weight:600">${psi}</td>
          <td style="padding:6px 12px;text-align:right;font-family:monospace">${this.fmtNum(r.n_train)}</td>
          <td style="padding:6px 12px;text-align:right;font-family:monospace">${this.fmtNum(r.n_recent)}</td>
          <td style="padding:6px 12px;text-align:right;font-size:11px" class="muted">${r.window_days || '—'}d</td>
          <td style="padding:6px 12px;font-size:10px" class="muted">${this.esc(r.model_id || '—')}</td>
        </tr>`;
      }).join('');
    },

    renderCleanupTable() {
      const tbody = document.getElementById('dh-cleanup-tbody');
      if (!tbody) return;
      const s = this.state.snapshot;
      const reds = (s && s.red_list) || [];
      if (reds.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="padding:30px;text-align:center" class="muted">没有 red 表 ✓</td></tr>';
        return;
      }
      // 按 layer + freshness 排序 (最旧的最先看到)
      const sorted = reds.slice().sort((a, b) => {
        if (a.layer !== b.layer) return (a.layer || '').localeCompare(b.layer || '');
        return (b.freshness_hours || 0) - (a.freshness_hours || 0);
      });
      tbody.innerHTML = sorted.map((r) => {
        const issue = r.issue_summary || '—';
        const isOrphan = issue.includes('orphan_no_writer');
        const isEmpty = issue.includes('stale_empty');
        const writer = r.writer_module ? `<code style="font-size:10px">${this.esc(this.shortPath(r.writer_module))}</code>` : '<span class="muted" style="font-size:11px">—</span>';
        const layerBadge = r.layer ? `<span class="dh-layer-${r.layer}">${r.layer}</span>` : '';
        return `<tr style="border-bottom:1px solid var(--cm-ink-50,#f0f0f0)">
          <td style="padding:6px 12px">🔴</td>
          <td style="padding:6px 12px"><a href="javascript:DataHealthView.openAssetDrawer('${this.esc(r.table_name)}')" style="text-decoration:none;color:var(--cm-link,#0a6cb3)"><code style="font-size:11px">${this.esc(r.table_name)}</code></a></td>
          <td style="padding:6px 12px">${layerBadge}</td>
          <td style="padding:6px 12px;text-align:right;font-family:monospace">${this.fmtNum(r.row_count)}</td>
          <td style="padding:6px 12px;font-size:11px">${this.esc(r.last_data_date || '—')}</td>
          <td style="padding:6px 12px;text-align:right;font-size:11px" class="muted">${r.freshness_hours != null ? r.freshness_hours.toFixed(1) + 'h' : '—'}</td>
          <td style="padding:6px 12px">${writer}</td>
          <td style="padding:6px 12px;font-size:11px;color:${isOrphan || isEmpty ? '#a02a2a' : '#666'}">${this.esc(issue)}</td>
        </tr>`;
      }).join('');
    },

    renderModelsPanel() {
      const data = this.state.models;
      const champBox = document.getElementById('dh-model-champion-card');
      const chBox = document.getElementById('dh-model-challengers');
      const rtBox = document.getElementById('dh-model-retired');
      if (!data || !champBox) return;

      // Champion 大卡
      const champ = data.champion;
      if (champ) {
        const ic = champ.ic_holdout != null ? champ.ic_holdout.toFixed(4) : '—';
        const drift = champ.drift_score != null ? champ.drift_score.toFixed(3) : '—';
        const deployed = champ.deployed_at ? this.fmtDateTime(champ.deployed_at) : '—';
        champBox.innerHTML = `
          <div class="panel" style="padding:14px 18px;border-left:4px solid #2a7a2a;background:linear-gradient(90deg,#e8f4ec 0%,transparent 100%)">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
              <span class="dh-pill dh-pill-green" style="font-size:13px">👑 CHAMPION</span>
              <code style="font-size:13px;font-weight:600">${this.esc(champ.model_id)}</code>
            </div>
            <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12px">
              <div><span class="muted">IC holdout</span> <strong>${ic}</strong></div>
              <div><span class="muted">drift score</span> <strong>${drift}</strong></div>
              <div><span class="muted">deployed</span> ${deployed}</div>
              <div><span class="muted">promoted from</span> ${this.esc(champ.promoted_from || '初始')}</div>
            </div>
            ${champ.deploy_decision_notes ? `<div class="muted" style="margin-top:6px;font-size:11px">📝 ${this.esc(champ.deploy_decision_notes)}</div>` : ''}
          </div>`;
      } else {
        champBox.innerHTML = '<div class="panel" style="padding:14px;text-align:center" class="muted">没有 champion. 先跑 <code>backend/scripts/bootstrap_model_lifecycle.py</code></div>';
      }

      // Challengers
      const challengers = data.challengers || [];
      if (challengers.length > 0) {
        chBox.innerHTML = `
          <div class="panel" style="padding:12px 14px">
            <div style="font-size:13px;font-weight:600;margin-bottom:8px">⚔ Challengers (${challengers.length})</div>
            ${challengers.map((m) => {
              const ic = m.ic_holdout != null ? m.ic_holdout.toFixed(4) : '—';
              return `<div style="margin:4px 0;font-size:12px">
                <code>${this.esc(m.model_id)}</code>
                <span class="muted" style="margin-left:8px">IC=${ic}</span>
              </div>`;
            }).join('')}
          </div>`;
      } else {
        chBox.innerHTML = '<div class="muted" style="font-size:12px;padding:6px 14px">⚔ 没有正在评估的 challenger</div>';
      }

      // Retired
      const retired = data.retired || [];
      if (retired.length > 0) {
        rtBox.innerHTML = `
          <details>
            <summary style="cursor:pointer;font-size:12px;padding:6px 14px" class="muted">📦 Retired models (${retired.length}) — 点击展开</summary>
            <div class="panel" style="padding:0;overflow-x:auto;margin-top:6px">
              <table style="width:100%;border-collapse:collapse;font-size:11px">
                <thead style="background:var(--cm-ink-50,#f6f6f6)">
                  <tr>
                    <th style="text-align:left;padding:6px 10px">model_id</th>
                    <th style="text-align:right;padding:6px 10px">IC holdout</th>
                    <th style="text-align:left;padding:6px 10px">retired_at</th>
                  </tr>
                </thead>
                <tbody>
                  ${retired.map((m) => `<tr style="border-top:1px solid var(--cm-ink-50,#f0f0f0)">
                    <td style="padding:6px 10px"><code>${this.esc(m.model_id)}</code></td>
                    <td style="padding:6px 10px;text-align:right;font-family:monospace">${m.ic_holdout != null ? m.ic_holdout.toFixed(4) : '—'}</td>
                    <td style="padding:6px 10px" class="muted">${m.retired_at ? this.fmtDateTime(m.retired_at) : '—'}</td>
                  </tr>`).join('')}
                </tbody>
              </table>
            </div>
          </details>`;
      } else {
        rtBox.innerHTML = '';
      }
    },

    renderLineageTable() {
      const data = this.state.lineage;
      const tbody = document.getElementById('dh-lineage-tbody');
      if (!tbody || !data) return;
      const items = (data.lineages || []).slice().sort((a, b) =>
        (a.output_table || '').localeCompare(b.output_table || '')
      );
      if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="padding:30px;text-align:center" class="muted">空</td></tr>';
        return;
      }
      tbody.innerHTML = items.map((l) => {
        const sevDot = l.output_severity === 'red' ? '🔴' :
                       l.output_severity === 'yellow' ? '🟡' :
                       l.output_severity === 'green' ? '🟢' : '⚪';
        const status = l.last_status || 'pending';
        const statusBadge =
          status === 'ok'      ? '<span style="padding:2px 6px;background:#e8f4ec;color:#1f7a3a;border-radius:3px;font-size:11px">ok</span>' :
          status === 'failed'  ? '<span style="padding:2px 6px;background:#fde8e8;color:#a02a2a;border-radius:3px;font-size:11px">failed</span>' :
                                  '<span style="padding:2px 6px;background:#f0f0f0;color:#666;border-radius:3px;font-size:11px">pending</span>';
        const inputs = (l.input_tables || []).map((t) => `<code style="font-size:10px;color:#666">${this.esc(t)}</code>`).join(' · ');
        const lastRun = l.last_run_at ? this.fmtDateTime(l.last_run_at) : '<span class="muted">未跑</span>';
        const runtimeStr = l.last_runtime_s != null ? `${l.last_runtime_s.toFixed(1)}s` : '—';
        const rowsStr = l.last_row_count != null ? this.fmtNum(l.last_row_count) : '—';
        const hashChange = l.sql_hash_changed
          ? ' <span style="padding:1px 4px;background:#fff4d4;color:#a67c00;border-radius:3px;font-size:10px" title="SQL 已变更, 上次运行的 hash 跟当前不一致">SQL 变更</span>'
          : '';
        const errorTooltip = l.last_error ? ` title="${this.esc(l.last_error)}"` : '';
        return `<tr style="border-bottom:1px solid var(--cm-ink-50,#f0f0f0)"${errorTooltip}>
          <td style="padding:6px 12px">${sevDot}</td>
          <td style="padding:6px 12px"><code style="font-size:11px;font-weight:600">${this.esc(l.lineage_id)}</code><br><span class="muted" style="font-size:10px">${this.esc(l.description || '')}</span></td>
          <td style="padding:6px 12px"><code style="font-size:11px">${this.esc(l.output_table)}</code>${hashChange}</td>
          <td style="padding:6px 12px">${inputs}</td>
          <td style="padding:6px 12px;font-size:11px" class="muted">${this.esc(l.schedule || 'on-demand')}</td>
          <td style="padding:6px 12px">${statusBadge}</td>
          <td style="padding:6px 12px;text-align:right;font-family:monospace">${rowsStr}</td>
          <td style="padding:6px 12px;text-align:right;font-size:11px" class="muted">${runtimeStr}</td>
          <td style="padding:6px 12px;font-size:11px" class="muted">${lastRun}</td>
        </tr>`;
      }).join('');
    },

    renderClientsTable() {
      const data = this.state.clients;
      const tbody = document.getElementById('dh-clients-tbody');
      if (!tbody || !data) return;
      const clients = (data.clients || []).slice().sort((a, b) => {
        if (a.source_tier !== b.source_tier) return a.source_tier - b.source_tier;
        return (a.client_id || '').localeCompare(b.client_id || '');
      });
      if (clients.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="padding:30px;text-align:center" class="muted">空</td></tr>';
        return;
      }
      tbody.innerHTML = clients.map((c) => {
        const tier = c.source_tier;
        const tierBadge = tier === 1 ? '<span style="padding:2px 6px;background:#e8f4ec;color:#1f7a3a;border-radius:3px;font-size:11px">tier 1 主</span>'
          : tier === 2 ? '<span style="padding:2px 6px;background:#fff4d4;color:#a67c00;border-radius:3px;font-size:11px">tier 2 备</span>'
          : tier === 3 ? '<span style="padding:2px 6px;background:#fde8e8;color:#a02a2a;border-radius:3px;font-size:11px">tier 3 兜底</span>'
          : '<span class="muted" style="font-size:11px">派生</span>';
        const hs = c.health_summary || {};
        const healthHtml = `
          <span style="color:#2a7a2a">🟢 ${hs.green || 0}</span>
          <span style="color:#a67c00;margin-left:4px">🟡 ${hs.yellow || 0}</span>
          <span style="color:#c33;margin-left:4px">🔴 ${hs.red || 0}</span>`;
        const writes = (c.writes || []).map((w) => {
          const sev = w.severity || 'unknown';
          const dot = sev === 'red' ? '🔴' : sev === 'yellow' ? '🟡' : sev === 'green' ? '🟢' : '⚪';
          return `<div style="margin:2px 0">${dot} <code style="font-size:11px">${this.esc(w.table)}</code> <span class="muted" style="font-size:10px">· ${this.esc(w.purpose || '')} · ${this.esc(w.freshness)}/${w.sla_hours}h</span></div>`;
        }).join('');
        const fb = (c.fallback_chain || []).join(' → ') || '—';
        return `<tr style="border-bottom:1px solid var(--cm-ink-50,#f0f0f0);vertical-align:top">
          <td style="padding:6px 12px">${tierBadge}</td>
          <td style="padding:6px 12px"><code style="font-size:12px;font-weight:600">${this.esc(c.client_id)}</code><br><span class="muted" style="font-size:10px">${this.esc(c.description)}</span></td>
          <td style="padding:6px 12px">${writes}</td>
          <td style="padding:6px 12px;font-size:11px" class="muted">${this.esc(fb)}</td>
          <td style="padding:6px 12px;text-align:right">${(c.writes || []).length}</td>
          <td style="padding:6px 12px;text-align:right;font-size:11px">${healthHtml}</td>
          <td style="padding:6px 12px;font-size:11px" class="muted"><code>${this.esc(c.sync_step_id || '—')}</code></td>
        </tr>`;
      }).join('');
    },

    async openAssetDrawer(tableName) {
      const drawer = document.getElementById('dh-asset-drawer');
      const title = document.getElementById('dh-asset-title');
      const body = document.getElementById('dh-asset-body');
      if (!drawer || !body) return;
      drawer.style.display = '';
      if (title) title.textContent = tableName;
      body.innerHTML = '<div class="muted" style="padding:30px;text-align:center">加载中...</div>';
      try {
        const r = await fetch(`/api/data_health/asset/${encodeURIComponent(tableName)}`);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        body.innerHTML = this.renderAssetDetail(d);
      } catch (e) {
        body.innerHTML = `<div style="color:#c33;padding:30px;text-align:center">加载失败: ${e.message}</div>`;
      }
    },

    renderAssetDetail(d) {
      const a = d.asset || {};
      const ls = d.latest_snapshot || {};
      const trend = d.trend || [];
      const sevDot = ls.severity === 'red' ? '🔴' : ls.severity === 'yellow' ? '🟡' : ls.severity === 'green' ? '🟢' : '⚪';
      const readers = Array.isArray(a.reader_modules) ? a.reader_modules : [];
      const readersHtml = readers.length === 0
        ? '<span class="muted">无读者 (orphan_no_reader 候选)</span>'
        : readers.map((r) => `<div><code style="font-size:11px">${this.esc(this.shortPath(r))}</code></div>`).join('');
      let trendHtml = '';
      if (trend.length > 0) {
        trendHtml = '<div style="font-size:11px" class="muted">最近 ' + trend.length + ' 次快照: ' +
          trend.slice(0, 7).map((t) => {
            const dot = t.severity === 'red' ? '🔴' : t.severity === 'yellow' ? '🟡' : '🟢';
            return `<span title="${t.snapshot_at}">${dot}</span>`;
          }).join(' ') + '</div>';
      }
      return `
        <div style="font-size:14px;margin-bottom:14px">
          ${sevDot} <strong>${this.esc(a.table_name)}</strong>
          <span class="muted" style="margin-left:8px">layer=<code>${a.layer || '—'}</code> · schema_version=<code>${a.schema_version || 'v1'}</code></span>
        </div>

        <h4 style="margin:12px 0 6px;font-size:13px">声明 (dim_data_asset)</h4>
        <table style="width:100%;font-size:12px">
          <tr><td class="muted" style="width:140px;vertical-align:top">purpose</td><td>${this.esc(a.purpose) || '<span class="muted">未填 (建议手工补)</span>'}</td></tr>
          <tr><td class="muted" style="vertical-align:top">writer</td><td><code style="font-size:11px">${this.esc(a.writer_module) || '<span class="muted">无 writer</span>'}</code></td></tr>
          <tr><td class="muted" style="vertical-align:top">readers (${readers.length})</td><td>${readersHtml}</td></tr>
          <tr><td class="muted" style="vertical-align:top">upstream</td><td><code style="font-size:11px">${this.esc(a.upstream_source) || '—'}</code></td></tr>
          <tr><td class="muted">source_tier</td><td>${a.source_tier ?? '—'}</td></tr>
          <tr><td class="muted">expected_freshness</td><td>${this.esc(a.expected_freshness) || '—'}</td></tr>
          <tr><td class="muted">SLA</td><td>${a.sla_hours ?? '—'} 小时</td></tr>
          <tr><td class="muted" style="vertical-align:top">consumed_by_views</td><td>${
            Array.isArray(a.consumed_by_views) && a.consumed_by_views.length > 0
              ? a.consumed_by_views.map((v) => `<code style="font-size:11px">${v}</code>`).join(', ')
              : '<span class="muted">未填</span>'
          }</td></tr>
        </table>

        <h4 style="margin:14px 0 6px;font-size:13px">最新快照 (mart_data_health)</h4>
        ${ls.snapshot_at ? `
        <table style="width:100%;font-size:12px">
          <tr><td class="muted" style="width:140px">severity</td><td>${sevDot} <strong>${ls.severity}</strong></td></tr>
          <tr><td class="muted">row_count</td><td>${this.fmtNum(ls.row_count)}</td></tr>
          <tr><td class="muted">last_data_date</td><td><code>${ls.last_data_date || '—'}</code></td></tr>
          <tr><td class="muted">freshness</td><td>${ls.freshness_hours != null ? ls.freshness_hours.toFixed(1) + 'h' : '—'} (SLA ${a.sla_hours || '—'}h)</td></tr>
          <tr><td class="muted">source_tier_dist</td><td><code style="font-size:11px">${this.esc(ls.source_tier_dist) || '—'}</code></td></tr>
          <tr><td class="muted" style="vertical-align:top">issue</td><td style="color:#a66">${this.esc(ls.issue_summary) || '<span class="muted">无</span>'}</td></tr>
          <tr><td class="muted">snapshot_at</td><td class="muted" style="font-size:11px">${this.fmtDateTime(ls.snapshot_at)}</td></tr>
        </table>
        ${trendHtml}
        ` : '<div class="muted">尚无快照, 请运行 data_health_snapshot.py</div>'}
      `;
    },

    // helpers
    esc(s) { if (s == null) return ''; return String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); },
    fmtNum(n) { if (n == null) return '—'; return Number(n).toLocaleString('zh-CN'); },
    fmtDateTime(s) { if (!s) return '—'; return String(s).replace('T', ' ').slice(0, 19); },
    shortPath(p) { if (!p) return ''; return p.replace(/^backend\//, '').replace(/^.+?\/services\//, 'services/').replace(/^.+?\/scripts\//, 'scripts/').replace(/^.+?\/routers\//, 'routers/'); },
  };

  window.DataHealthView = DataHealthView;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => DataHealthView.init());
  } else {
    DataHealthView.init();
  }
})();
