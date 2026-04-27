// settings-view.js — 系统设置页 (P5)
// 数据源参数 / 派生层 schema 版本 / 危险操作 / 主题 / 关于

(function () {
  if (window.SettingsView) return;

  let _initialized = false;

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
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
    // 占位: 项目目前没有 schema_version 列, 后续 (P6) 加. 这里展示已知派生层表
    el.innerHTML = `
      <div style="padding:5px 0;border-bottom:1px dotted var(--cm-bg-100)">
        <code>fact_institution_event</code>
        <span style="color:var(--cm-ink-500);float:right">v?? (未跟踪)</span>
      </div>
      <div style="padding:5px 0;border-bottom:1px dotted var(--cm-bg-100)">
        <code>fact_setup_snapshot</code>
        <span style="color:var(--cm-ink-500);float:right">v?? (未跟踪)</span>
      </div>
      <div style="padding:5px 0;border-bottom:1px dotted var(--cm-bg-100)">
        <code>mart_*</code>
        <span style="color:var(--cm-ink-500);float:right">v?? (未跟踪)</span>
      </div>
      <div class="muted" style="padding-top:6px;font-size:11px">
        TODO P6: 给派生层加 _schema_version 列 + UI 跟踪.
      </div>
    `;
  }

  function renderDangerZone() {
    const el = document.getElementById('sys-danger-zone');
    if (!el) return;
    const buttons = [
      { id: 'recompute-all', label: '🔥 全量重算派生数据', action: recomputeAll },
      { id: 'clean-mart', label: '清空 mart_*', action: () => alert('待实现 P6') },
      { id: 'clean-fact', label: '清空 fact_*', action: () => alert('待实现 P6') },
      { id: 'reset-modules', label: '模块开关重置', action: () => alert('待实现 P6') },
    ];
    el.innerHTML = buttons.map(b => `<button class="chip chip-outline" id="sys-btn-${esc(b.id)}" style="border-color:#d33;color:#d33">${esc(b.label)}</button>`).join('');
    buttons.forEach(b => {
      document.getElementById('sys-btn-' + b.id)?.addEventListener('click', b.action);
    });
  }

  async function recomputeAll() {
    if (!confirm('确认全量重算派生数据? 不可撤销 (raw 不动).')) return;
    try {
      const r = await fetch('/api/inst/update/recompute_all', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      alert(r.ok ? '已触发: ' + JSON.stringify(j).slice(0, 200) : '失败');
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
      if (r.ok) backend = 'OK';
    } catch { backend = '不可达'; }
    el.innerHTML = `
      <div>chunky-monkey-v2</div>
      <div style="color:var(--cm-ink-500);font-size:11px;margin-top:4px">
        后端: ${esc(backend)}<br>
        架构方案: <a href="https://github.com/dare2live/chunky-monkey-v2/blob/main/docs/architecture-redesign-2026-04.md" target="_blank">docs/architecture-redesign-2026-04.md</a><br>
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

  window.SettingsView = {
    show() {
      if (!_initialized) { init(); _initialized = true; }
    },
  };
})();
