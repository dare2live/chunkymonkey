// strategy-view.js — 策略页 (P4)
// Tab 切换 + 预设管理 (后端 dim_strategy_preset 表)
// 4 个 widget 由现有 widget code 复用 (lazy mount)

(function () {
  if (window.StrategyView) return;

  let _initialized = false;
  let _activeTab = 'signals';

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // -------- preset CRUD ---------
  async function loadPresets() {
    try {
      const r = await fetch('/api/inst/strategy/preset/list');
      if (!r.ok) return [];
      const j = await r.json();
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
    const r = await fetch('/api/inst/strategy/preset/get?name=' + encodeURIComponent(name));
    if (!r.ok) return null;
    return r.json();
  }

  // -------- tab 切换 ---------
  function switchTab(tabName) {
    _activeTab = tabName;
    document.querySelectorAll('.strategy-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.strategyTab === tabName);
    });
    document.querySelectorAll('.strategy-tab-pane').forEach(p => {
      p.style.display = p.id === 'strategy-tab-' + tabName ? 'block' : 'none';
    });
    // lazy mount widget
    mountWidget(tabName);
  }

  // -------- widget mount (复用现有工作台 widget) ---------
  function mountWidget(tabName) {
    const mountId = ({
      signals: 'signals-widget-mount',
      cohort: 'cohort-widget-mount',
      backtest: 'backtest-widget-mount',
      screening: 'screening-widget-mount',
    })[tabName];
    if (!mountId) return;
    const target = document.getElementById(mountId);
    if (!target || target.dataset.mounted === '1') return;

    // 试着找现有 widget 全局函数 (如果 app.js 暴露了)
    const fn = ({
      signals: window.mountSignalParamsWidget,
      cohort: window.mountCohortFeedbackWidget,
      backtest: window.mountBacktestWidget,
      screening: window.mountScreeningWidget,
    })[tabName];

    if (typeof fn === 'function') {
      try {
        fn(target);
        target.dataset.mounted = '1';
      } catch (e) {
        target.innerHTML = `<div class="muted" style="padding:20px;color:#d33">widget 加载失败: ${esc(e.message)}</div>`;
      }
    } else {
      target.innerHTML = `
        <div class="muted" style="padding:20px;text-align:center">
          widget '${tabName}' 接入待 P4-2: app.js 现有 mount 函数未暴露到 window.
          <br><br>
          <small>临时方案: 跳到工作台页查看, 后续会把这里接通.</small>
        </div>
      `;
    }
  }

  // -------- preset selector 渲染 ---------
  async function renderPresetSelect() {
    const sel = document.getElementById('strategy-preset-select');
    if (!sel) return;
    const presets = await loadPresets();
    if (!presets.length) {
      sel.innerHTML = '<option value="">(无预设)</option>';
      return;
    }
    sel.innerHTML = presets.map(p => `<option value="${esc(p.name)}">${esc(p.name)}${p.is_default ? ' ★' : ''}</option>`).join('');
  }

  async function applyPreset(name) {
    if (!name) return;
    const data = await loadPreset(name);
    if (!data) return;
    // 触发现有 widget 重新加载参数 (调用方 app.js 应有相应 hook)
    if (window.applySignalsPreset) window.applySignalsPreset(data.payload);
    if (window.applyBacktestPreset) window.applyBacktestPreset(data.payload);
  }

  // -------- 主入口 ---------
  async function init() {
    document.querySelectorAll('.strategy-tab').forEach(t => {
      t.addEventListener('click', () => switchTab(t.dataset.strategyTab));
    });
    switchTab(_activeTab);

    await renderPresetSelect();
    document.getElementById('strategy-preset-select')?.addEventListener('change', e => applyPreset(e.target.value));
    document.getElementById('strategy-preset-save')?.addEventListener('click', async () => {
      const name = prompt('保存为预设, 命名:');
      if (!name) return;
      // 这里 payload 应该收集当前各 widget 的状态; 简化: 让后端接受一个 payload object, 前端先传空
      const payload = window.getCurrentStrategyPayload ? window.getCurrentStrategyPayload() : {};
      const r = await savePreset(name, payload);
      if (r.ok) {
        await renderPresetSelect();
        alert('已保存: ' + name);
      } else {
        alert('保存失败');
      }
    });
    document.getElementById('strategy-preset-new')?.addEventListener('click', () => {
      alert('新建: 调当前参数到默认值, 然后点"保存"');
    });
    document.getElementById('strategy-preset-delete')?.addEventListener('click', async () => {
      const sel = document.getElementById('strategy-preset-select');
      const name = sel?.value;
      if (!name) return;
      if (!confirm('删除预设 "' + name + '"?')) return;
      const r = await deletePreset(name);
      if (r.ok) {
        await renderPresetSelect();
      } else {
        alert('删除失败');
      }
    });
  }

  window.StrategyView = {
    show() {
      if (!_initialized) { init(); _initialized = true; }
      else { renderPresetSelect(); }
    },
  };
})();
