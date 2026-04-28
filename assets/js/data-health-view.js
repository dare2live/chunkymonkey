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
      snapshot: null,           // /snapshot 返回
      sources: null,            // /sources 返回 (缓存)
      sourcesAt: 0,             // sources 拉取时间
      activeTab: 'health',      // 'health' | 'sources'
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
      tbody.innerHTML = ss.sources.map((s) => {
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
    },

    switchTab(tab) {
      this.state.activeTab = tab;
      document.querySelectorAll('.dh-tab-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-dh-tab') === tab);
      });
      const panelHealth = document.getElementById('dh-panel-health');
      const panelSources = document.getElementById('dh-panel-sources');
      if (panelHealth) panelHealth.style.display = tab === 'health' ? '' : 'none';
      if (panelSources) panelSources.style.display = tab === 'sources' ? '' : 'none';
      if (tab === 'sources' && !this.state.sources) {
        // lazy load sources
        fetch('/api/data_health/sources').then((r) => r.json()).then((data) => {
          this.state.sources = data;
          this.state.sourcesAt = Date.now();
          this.renderSourcesTable();
        });
      }
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
