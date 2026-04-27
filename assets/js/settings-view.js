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
    // 系统页只放真正的"破坏性 + 全局"操作. 跳转/导航交给二级 nav.
    const buttons = [
      {
        id: 'reset-derived',
        label: '🔥 清空并重算派生层',
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
