/* signal-adapter.js · Step 6 架构适配层
 *
 * 目标：
 *   1. 所有 signals_v2 数据出入口的唯一管道（UI 不直接 fetch /api/signals/*）
 *   2. 参数改了 → 通过事件总线通知所有订阅者（列表 / cohort 卡 / 回测 / 抽屉）刷新
 *   3. UI 消费的是"标准化展示对象"，不依赖后端字段路径，后端加/删评分维度前端 0 改动
 *   4. 每次配置变更前自动快照（未来支持对比 / 回滚）
 *
 * 不做：
 *   · 不缓存（每次 fetch 最新数据；缓存让"参数变化同步"变复杂）
 *   · 不做 DOM（纯数据层；UI 订阅 onChange 自己决定怎么渲染）
 */

(function (global) {
  'use strict';

  const BASE = '';
  const listeners = new Map();   // event -> Set<callback>

  async function apiGet(path) {
    const r = await fetch(BASE + path, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return await r.json();
  }
  async function apiPost(path, body) {
    const r = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body == null ? null : JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return await r.json();
  }

  // ─── 事件总线 ──────────────────────────────────────────
  function on(event, cb) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(cb);
    return () => listeners.get(event).delete(cb);
  }
  function emit(event, payload) {
    const set = listeners.get(event);
    if (set) set.forEach(cb => { try { cb(payload); } catch (e) { console.error(e); } });
  }

  // ─── 标准化转换：单个 signals_v2 事件 → 展示对象 ──────
  // UI 层只认这个结构。后端加字段不影响 UI（除非想展示新字段）。
  function eventToView(raw) {
    if (!raw) return null;
    return {
      id: raw.event_id,
      stockCode: raw.stock_code,
      stockName: raw.stock_name,
      industry: raw.industry,
      institutionId: raw.institution_id,
      institutionName: raw.institution_name,
      institutionType: raw.institution_type || (raw.rule_breakdown?.checks || []).find(c => c.key === 'inst_type')?.raw,
      eventType: raw.event_type,
      noticeDate: raw.notice_date,
      noticeDateSource: raw.notice_date_source || 'unknown',
      sourceNoticeDate: raw.source_notice_date || null,
      availabilityDeadline: raw.availability_deadline || null,
      premiumPct: raw.premium_pct,
      action: raw.action,                          // follow / watch / skip
      reasonLabel: raw.reason_label,
      shortEV: raw.short && raw.short.stats ? {
        pct: raw.short.stats.ev_pct,
        n: raw.short.stats.n,
        winRate: raw.short.stats.win_rate,
      } : null,
      longEV: raw.long && raw.long.stats ? {
        pct: raw.long.stats.ev_pct,
        n: raw.long.stats.n,
        winRate: raw.long.stats.win_rate,
      } : null,
      realizedPct: raw.realized_return_pct,
      // 硬规则体检，维度数据驱动 — UI 直接遍历渲染，后端加 D9 这里自动有
      ruleChecks: (raw.rule_breakdown && raw.rule_breakdown.checks) || [],
      ruleTriggered: raw.rule_breakdown && raw.rule_breakdown.triggered,
      raw,    // 保留原始引用，调试 / 特殊字段兜底用
    };
  }

  // ─── 按股票聚合多个事件（股票视图主列表用）───────────
  function aggregateByStock(rawSignals) {
    const groups = new Map();
    const actionRank = { follow: 0, watch: 1, skip: 2 };
    (rawSignals || []).forEach(raw => {
      const code = raw.stock_code;
      if (!code) return;
      if (!groups.has(code)) {
        groups.set(code, {
          stockCode: code,
          stockName: raw.stock_name,
          industry: raw.industry,
          events: [],
          institutions: new Set(),
          actionCounts: { follow: 0, watch: 0, skip: 0 },
          noticeSourceCounts: { source_notice: 0, page_update_date: 0, regulatory_deadline: 0, unknown: 0 },
        });
      }
      const g = groups.get(code);
      const view = eventToView(raw);
      g.events.push(view);
      g.institutions.add(raw.institution_id);
      if (g.actionCounts[raw.action] !== undefined) g.actionCounts[raw.action] += 1;
      const src = raw.notice_date_source || 'unknown';
      if (g.noticeSourceCounts[src] === undefined) g.noticeSourceCounts[src] = 0;
      g.noticeSourceCounts[src] += 1;
    });

    return Array.from(groups.values()).map(g => {
      const sorted = [...g.events].sort((a, b) => {
        const ra = actionRank[a.action] ?? 9;
        const rb = actionRank[b.action] ?? 9;
        if (ra !== rb) return ra - rb;
        return String(b.noticeDate || '').localeCompare(String(a.noticeDate || ''));
      });
      const top = sorted[0];
      const premiums = g.events.map(e => e.premiumPct).filter(v => v != null);
      const longEVs = g.events.map(e => e.longEV?.pct).filter(v => v != null);
      const shortEVs = g.events.map(e => e.shortEV?.pct).filter(v => v != null);
      const avg = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
      const best = arr => arr.length ? Math.max(...arr) : null;
      return {
        stockCode: g.stockCode,
        stockName: g.stockName,
        industry: g.industry,
        instCount: g.institutions.size,
        eventCount: g.events.length,
        actionCounts: g.actionCounts,
        noticeSourceCounts: g.noticeSourceCounts,
        bestAction: top ? top.action : 'none',
        topEvent: top,
        events: sorted,
        premiumAvg: avg(premiums),
        longEVBest: best(longEVs),
        shortEVBest: best(shortEVs),
        latestNotice: sorted.reduce((m, e) => {
          const d = String(e.noticeDate || '');
          return d > m ? d : m;
        }, ''),
      };
    }).sort((a, b) => {
      if (b.actionCounts.follow !== a.actionCounts.follow) return b.actionCounts.follow - a.actionCounts.follow;
      if (b.instCount !== a.instCount) return b.instCount - a.instCount;
      return String(b.latestNotice).localeCompare(String(a.latestNotice));
    });
  }

  // ─── 公开 API ──────────────────────────────────────────

  async function fetchSignals(freshnessDays = 90) {
    const d = await apiGet(`/api/signals/today?freshness_days=${freshnessDays}&limit=2000`);
    return {
      summary: d.summary || null,
      events: (d.signals || []).map(eventToView),
      raw: d.signals || [],        // aggregate 需要原始字段
      byStock: aggregateByStock(d.signals || []),
    };
  }

  async function fetchCohort(lookbackDays = 180) {
    return await apiGet(`/api/signals/cohort/recent?lookback_days=${lookbackDays}`);
  }

  // 选股 + 海龟 → byStock 增强（股票视图列 / 多选筛选用）
  // 返回 { screening: Map<code, row>, turtle: Map<code, row> }
  // 失败静默：前端列回退为 '—'，不阻塞主表渲染
  async function fetchScreeningEnrichment() {
    const out = { screening: new Map(), turtle: new Map() };
    const [scr, tur] = await Promise.all([
      apiGet('/api/screening/results?limit=5000').catch(() => null),
      apiGet('/api/screening/turtle-states').catch(() => null),
    ]);
    if (scr && Array.isArray(scr.data)) {
      scr.data.forEach(r => { if (r && r.stock_code) out.screening.set(r.stock_code, r); });
    }
    if (tur && Array.isArray(tur.data)) {
      tur.data.forEach(r => { if (r && r.stock_code) out.turtle.set(r.stock_code, r); });
    }
    return out;
  }

  async function fetchConfig() {
    return await apiGet('/api/signals/config');
  }

  async function fetchEventStats() {
    return await apiGet('/api/signals/events/stats');
  }

  async function fetchInstTrackRecord(institutionId) {
    return await apiGet(`/api/signals/institution/${encodeURIComponent(institutionId)}`);
  }

  async function fetchSimilarEvents(eventId, limit = 50) {
    return await apiGet(`/api/signals/event/${encodeURIComponent(eventId)}/similar?limit=${limit}`);
  }

  async function fetchBacktest(opts = {}) {
    let q = '';
    if (opts.startDate) q += `&start_date=${opts.startDate}`;
    if (opts.endDate)   q += `&end_date=${opts.endDate}`;
    return await apiGet('/api/signals/backtest' + (q ? '?' + q.slice(1) : ''));
  }

  async function updateConfig(patch) {
    // 快照当前配置（未来 C7 实现参数历史对比 / 回滚）
    try {
      const prev = await fetchConfig();
      const snap = {
        ts: new Date().toISOString(),
        before: prev.current,
        patch,
      };
      const history = JSON.parse(localStorage.getItem('cm_config_history') || '[]');
      history.unshift(snap);
      localStorage.setItem('cm_config_history', JSON.stringify(history.slice(0, 20)));
    } catch (e) { /* 非关键路径 */ }

    const result = await apiPost('/api/signals/config', patch);
    emit('config:changed', patch);
    return result;
  }

  async function resetConfig() {
    const result = await apiPost('/api/signals/config/reset');
    emit('config:changed', 'reset');
    return result;
  }

  function getConfigHistory() {
    try { return JSON.parse(localStorage.getItem('cm_config_history') || '[]'); }
    catch { return []; }
  }

  // ─── 导出 ──────────────────────────────────────────────
  global.SignalAdapter = {
    fetchSignals,
    fetchCohort,
    fetchScreeningEnrichment,
    fetchConfig,
    fetchEventStats,
    fetchInstTrackRecord,
    fetchSimilarEvents,
    fetchBacktest,
    updateConfig,
    resetConfig,
    getConfigHistory,
    // 事件总线
    on,
    emit,
    // 工具（暴露让调用方也能做转换，复用不重写）
    eventToView,
    aggregateByStock,
  };
})(window);
