// settings-view.js — 系统设置页 (P5)
// 数据源参数 / 派生层 schema 版本 / 危险操作 / 主题 / 关于

(function (root) {
  if (root.SettingsView) return;

  let _initialized = false;

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function buildSchemaVersionsModel(data) {
    const versions = Array.isArray(data && data.versions) ? data.versions : [];
    const byLayer = { fact: 0, mart: 0, dim_derived: 0 };
    const driftRows = [];
    const okRows = [];
    for (const version of versions) {
      if (version && version.layer) {
        byLayer[version.layer] = (byLayer[version.layer] || 0) + 1;
      }
      if (version && version.drift) driftRows.push(version);
      else okRows.push(version);
    }
    return {
      byLayer,
      versions,
      driftRows,
      okRows,
      driftCount: Number(data && data.summary && data.summary.drift_count) || 0,
      total: Number(data && data.summary && data.summary.total) || 0,
      nViews: Number(data && data.summary && data.summary.n_views) || 0,
    };
  }

  async function renderDataSourceParams() {
    const el = document.getElementById('sys-ds-params');
    if (!el) return;
    try {
      const r = await fetch('/api/data_sources/list');
      const j = await r.json();
      el.innerHTML = (j.sources || []).map(s => `
        <div style="padding:6px 0;border-bottom:1px dotted var(--cm-bg-100)">
          <span style="font-weight:600">${esc(s.display_name)}</span>
          <span style="color:var(--cm-ink-500)">优先级 ${s.priority}</span>
          <span style="float:right;color:var(--cm-ink-500)">${s.capabilities.length} caps</span>
        </div>
      `).join('') || '<div class="muted">无</div>';
    } catch (e) {
      el.innerHTML = '<div class="muted" style="color:#d33">加载失败: ' + esc(e.message) + '</div>';
    }
  }

  async function renderSchemaVersions() {
    const el = document.getElementById('sys-schema-versions');
    if (!el) return;
    el.innerHTML = '<div class="muted" style="padding:8px 0">加载中…</div>';
    try {
      const r = await fetch('/api/data_sources/schema_versions');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const model = buildSchemaVersionsModel(data);
      const { byLayer, driftRows, okRows, driftCount } = model;

      // 顶部摘要 + drift 列表 + ok 折叠
      el.innerHTML = `
        <div style="font-size:12px;margin-bottom:10px">
          <strong>${model.total}</strong> 张派生表
          (fact ${byLayer.fact} / mart ${byLayer.mart} / dim ${byLayer.dim_derived})
          + <strong>${model.nViews}</strong> view
          ${driftCount > 0
            ? `<span style="color:#d33;margin-left:10px">WARN ${driftCount} 张漂移</span>`
            : '<span style="color:#0a7;margin-left:10px">OK 全部 actual = expected</span>'}
        </div>
        ${driftCount > 0 ? `
          <div style="background:rgba(221,51,51,0.05);border-left:3px solid #d33;padding:8px 12px;margin-bottom:10px;border-radius:4px">
            <div style="font-size:12px;font-weight:600;margin-bottom:6px">漂移派生表 (建议重算或标记 baseline)</div>
            ${driftRows.map(v => `
              <div style="padding:3px 0;font-size:11px;font-family:monospace">
                <code>${esc(v.table_name)}</code>
                <span style="color:var(--cm-ink-500)"> code=${esc(v.expected_version)} db=${esc(v.actual_version || 'never_recorded')}</span>
              </div>
            `).join('')}
            <button class="chip chip-outline" style="margin-top:8px;font-size:11px" id="sys-schema-baseline-btn">标记当前 DB 为 baseline (假定 v1 数据正确)</button>
          </div>
        ` : ''}
        <details style="font-size:11px">
          <summary style="cursor:pointer;font-size:12px;font-weight:600">已对齐 ${okRows.length} 张 (展开看清单)</summary>
          <div style="margin-top:6px;max-height:300px;overflow-y:auto">
            ${okRows.map(v => `
              <div style="padding:3px 0;border-bottom:1px dotted var(--cm-bg-100);font-family:monospace;font-size:11px">
                <code>${esc(v.table_name)}</code>
                <span style="color:var(--cm-ink-500);float:right">${esc(v.expected_version)} ${v.rebuilt_at ? '@ ' + esc(v.rebuilt_at.slice(0, 16)) : ''}</span>
              </div>
            `).join('')}
          </div>
        </details>
      `;

      // 绑 baseline 按钮
      const btn = document.getElementById('sys-schema-baseline-btn');
      if (btn) btn.addEventListener('click', async () => {
        if (!confirm('把当前 DB 中所有派生表的 actual 标为 expected? 仅在你确认数据是按当前 schema 跑出来的时候用 (e.g. 刚跑过全量重算).')) return;
        try {
          const r = await fetch('/api/data_sources/schema_versions/record_baseline', { method: 'POST' });
          const j = await r.json();
          alert('已标 baseline: ' + (j.recorded || 0) + ' 张表');
          renderSchemaVersions();
        } catch (e) { alert('失败: ' + e.message); }
      });
    } catch (e) {
      el.innerHTML = '<div class="muted" style="color:#d33;padding:8px 0;font-size:12px">加载失败: ' + esc(e.message) + '</div>';
    }
  }

  function renderDangerZone() {
    const el = document.getElementById('sys-danger-zone');
    if (!el) return;
    // 系统页只放真正的"破坏性 + 全局"操作. 跳转/导航交给二级 nav.
    const buttons = [
      {
        id: 'reset-derived',
        label: '清空并重算派生层',
        action: resetDerived,
        desc: '清空 fact_* + mart_* 全部派生表, 从 raw 重算. 不可撤销 (raw 保留). 预计 10-20 分钟.',
      },
    ];
    el.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">
      ${buttons.map(b => `
        <div style="border:1px solid #d33;border-radius:6px;padding:12px;border-left:4px solid #d33;background:rgba(221,51,51,0.03)">
          <button class="chip" id="sys-btn-${esc(b.id)}" style="margin-bottom:8px;background:#fff;border:1px solid #d33;color:#d33;font-weight:600">${esc(b.label)}</button>
          <div class="muted" style="font-size:11px;line-height:1.5">${esc(b.desc)}</div>
        </div>
      `).join('')}
      </div>
      <div class="muted" style="font-size:11px;margin-top:10px;padding-top:8px;border-top:1px dashed var(--cm-ink-100)">
        <strong>区别于工作台 "计算派生层 (增量)"</strong>: 那个只对新增/变更样本算; 这里是 <strong>全量清空再重算</strong>, 用在 schema 升级 / 数据口径变化 / 修复异常之后.
      </div>
    `;
    buttons.forEach(b => {
      document.getElementById('sys-btn-' + b.id)?.addEventListener('click', b.action);
    });
  }

  async function resetDerived() {
    if (!confirm('确认清空事件、收益、画像、关系等派生层并重算吗?\n(raw 数据保留, 预计 10-20 分钟)')) return;
    try {
      const r = await fetch('/api/inst/update/reset-derived', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.ok !== false) {
        alert('已触发重算: ' + (j.message || '稍后查看工作台日志'));
      } else {
        alert('失败: ' + (j.message || j.error || 'HTTP ' + r.status));
      }
    } catch (e) {
      alert('失败: ' + e.message);
    }
  }

  function renderPrefs() {
    const el = document.getElementById('sys-prefs');
    if (!el) return;
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px">
        <label><input type="checkbox" id="sys-pref-dark" disabled> 暗黑模式 <span class="muted" style="font-size:10px">(P6)</span></label>
        <label><input type="checkbox" id="sys-pref-autorefresh" disabled> 工作台自动刷新 <span class="muted" style="font-size:10px">(P6)</span></label>
        <label>默认页面: <select disabled style="padding:3px;font-size:11px"><option>股票</option></select></label>
      </div>
    `;
  }

  async function renderAbout() {
    const el = document.getElementById('sys-about');
    if (!el) return;
    let backend = '加载中…';
    try {
      const r = await fetch('/api/inst/health/summary');
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.status === 'ok') {
        const enabled = Array.isArray(j.enabled_modules) ? j.enabled_modules.join(', ') : '';
        backend = enabled ? `OK (${enabled})` : 'OK';
      } else {
        backend = j.status || '异常';
      }
    } catch { backend = '不可达'; }
    el.innerHTML = `
      <div>chunky-monkey-v2</div>
      <div style="color:var(--cm-ink-500);font-size:11px;margin-top:4px">
        后端: ${esc(backend)}<br>
        架构方案: docs/architecture-redesign-2026-04.md<br>
        数据源: <a href="https://github.com/dare2live/tdxhub" target="_blank">tdxhub</a> + <a href="https://github.com/dare2live/aif10-scraper" target="_blank">aif10-scraper</a>
      </div>
    `;
  }

  function init() {
    renderDataSourceParams();
    renderSchemaVersions();
    renderDangerZone();
    renderPrefs();
    renderAbout();
  }

  root.SettingsView = {
    show() {
      if (!_initialized) { init(); _initialized = true; }
    },
    buildSchemaVersionsModel,
  };
})(typeof window !== 'undefined' ? window : globalThis);
