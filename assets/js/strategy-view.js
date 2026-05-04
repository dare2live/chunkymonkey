// strategy-view.js — 策略页 (P4 v2: 真接通 4 个 widget)
// 4 widget mount 直接调 window.{Widget}.mount(containerId)

(function () {
  if (window.StrategyView) return;

  let _initialized = false;
  let _activeTab = 'signals';
  const _mounted = { signals: false, cohort: false, backtest: false, screening: false };

  // tab → (containerId, mount fn)
  const TAB_MOUNT = {
    signals: {
      id: 'strategy-signal-params-container',
      get widget() { return window.SignalParamsWidget; },
    },
    cohort: {
      id: 'strategy-cohort-container',
      get widget() { return window.CohortCardWidget; },
    },
    backtest: {
      id: 'strategy-backtest-container',
      get widget() { return window.BacktestPanelWidget; },
    },
    screening: {
      id: 'strategy-screening-container',
      get widget() { return window.ScreeningPanelWidget; },
    },
  };

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // ---- preset CRUD ----
  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
    return r.json();
  }
  async function loadPresets() {
    try {
      const j = await fetchJSON('/api/inst/strategy/preset/list');
      return j.presets || [];
    } catch { return []; }
  }
  async function savePreset(name, payload) {
    return fetch('/api/inst/strategy/preset/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, payload }),
    });
  }
  async function deletePreset(name) {
    return fetch('/api/inst/strategy/preset/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
  }
  async function loadPreset(name) {
    try {
      return await fetchJSON('/api/inst/strategy/preset/get?name=' + encodeURIComponent(name));
    } catch { return null; }
  }

  // ---- tab ----
  function switchTab(tabName) {
    _activeTab = tabName;
    document.querySelectorAll('.strategy-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.strategyTab === tabName);
    });
    document.querySelectorAll('.strategy-tab-pane').forEach(p => {
      p.style.display = p.id === 'strategy-tab-' + tabName ? 'block' : 'none';
    });
    mountWidget(tabName);
  }

  function mountWidget(tabName) {
    if (_mounted[tabName]) return;
    const cfg = TAB_MOUNT[tabName];
    if (!cfg) return;
    const widget = cfg.widget;
    if (!widget || typeof widget.mount !== 'function') {
      const el = document.getElementById(cfg.id);
      if (el) {
        el.innerHTML = `
          <div class="muted" style="padding:20px;text-align:center;color:#d33">
            widget '${esc(tabName)}' 不可用<br>
            <small>window.${esc(cfg.widget && cfg.widget.constructor.name || '?')}.mount() 未找到</small>
          </div>
        `;
      }
      return;
    }
    try {
      widget.mount(cfg.id);
      _mounted[tabName] = true;
    } catch (e) {
      console.error('[StrategyView] mount fail', tabName, e);
      const el = document.getElementById(cfg.id);
      if (el) el.innerHTML = `<div class="muted" style="padding:20px;color:#d33">挂载失败: ${esc(e.message)}</div>`;
    }
  }

  // ---- preset selector ----
  async function renderPresetSelect() {
    const sel = document.getElementById('strategy-preset-select');
    if (!sel) return;
    const presets = await loadPresets();
    if (!presets.length) {
      sel.innerHTML = '<option value="">(无预设)</option>';
      return;
    }
    sel.innerHTML = presets.map(p => `<option value="${esc(p.name)}">${esc(p.name)}${p.is_default ? ' (默认)' : ''}</option>`).join('');
  }

  async function applyPreset(name) {
    if (!name) return;
    const data = await loadPreset(name);
    if (!data) return;
    if (window.applySignalsPreset) window.applySignalsPreset(data.payload);
    if (window.applyBacktestPreset) window.applyBacktestPreset(data.payload);
  }

  // ---- 主入口 ----
  async function init() {
    document.querySelectorAll('.strategy-tab').forEach(t => {
      t.onclick = () => switchTab(t.dataset.strategyTab);
    });
    switchTab(_activeTab);

    await renderPresetSelect();
    const sel = document.getElementById('strategy-preset-select');
    if (sel) sel.onchange = e => applyPreset(e.target.value);
    const saveBtn = document.getElementById('strategy-preset-save');
    if (saveBtn) saveBtn.onclick = async () => {
      const name = prompt('保存为预设, 命名:');
      if (!name) return;
      const payload = window.getCurrentStrategyPayload ? window.getCurrentStrategyPayload() : {};
      const r = await savePreset(name, payload);
      if (r.ok) {
        await renderPresetSelect();
        alert('已保存: ' + name);
      } else alert('保存失败');
    };
    const newBtn = document.getElementById('strategy-preset-new');
    if (newBtn) newBtn.onclick = () => alert('调当前参数到默认值, 然后点"保存"');
    const delBtn = document.getElementById('strategy-preset-delete');
    if (delBtn) delBtn.onclick = async () => {
      const name = sel?.value;
      if (!name || !confirm(`删除预设 "${name}"?`)) return;
      const r = await deletePreset(name);
      if (r.ok) await renderPresetSelect();
      else alert('删除失败');
    };
  }

  window.StrategyView = {
    show() {
      if (!_initialized) { _initialized = true; setTimeout(() => init(), 0); }
      else { renderPresetSelect(); mountWidget(_activeTab); }
    },
  };
})();
