/* Top bar, KPI cards, list panel, table — wired together */
const { useState: useStateM, useMemo: useMemoM, useEffect: useEffectM, useRef: useRefM } = React;

/* ───────────────────── Top bar ───────────────────── */
function TopBar({ status, onRefresh }) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">B</div>
        <div className="brand-text">
          <div className="brand-name">BestChoice</div>
          <div className="brand-sub">MACD 选股台</div>
        </div>
      </div>

      <div className="spacer" />

      <div className="health">
        <span className={`health-dot ${status.ready ? 'ready' : ''}`} />
        <span className="health-text">{status.message}</span>
        <span className="health-meta">数据至 {status.data_date}</span>
      </div>
      <button className="btn-ghost" onClick={onRefresh}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>
        </svg>
        重新计算
      </button>
    </header>
  );
}

/* ───────────────────── Strategy tabs + param strip ───────────────────── */
function StrategyBar({ profileId, onProfileChange }) {
  const [paramsOpen, setParamsOpen] = useStateM(false);
  const strategies = window.MOCK.STRATEGIES;
  const params = window.MOCK.PARAMS;
  const active = strategies.find(s => s.id === profileId);

  // Inline summary of key params for the active strategy
  const summary = [
    `EMA(${params.macd_fast.value},${params.macd_slow.value},${params.macd_signal.value})`,
    `持仓 ${params.holding_days.value} 天`,
    `量比 ≥ ${params.vol_ratio_min.value.toFixed(1)}`,
    `额比 ≥ ${params.amt_ratio_min.value.toFixed(1)}`,
    `位置 ≤ ${(params.price_pos_max.value * 100).toFixed(0)}%`,
  ];

  return (
    <div className="strategy-bar">
      <div className="strategy-tabs" role="tablist">
        {strategies.map(s => (
          <button key={s.id}
                  className={`s-tab ${profileId === s.id ? 'active' : ''}`}
                  onClick={() => onProfileChange(s.id)}
                  role="tab" aria-selected={profileId === s.id}>
            <span className="s-tab-name">{s.name}</span>
          </button>
        ))}
      </div>
      <div className="param-strip">
        <div className="param-strip-chips">
          {summary.map((c, i) => <span className="param-chip" key={i}>{c}</span>)}
        </div>
        <button className={`btn-text ${paramsOpen ? 'on' : ''}`} onClick={() => setParamsOpen(v => !v)}>
          {paramsOpen ? '收起说明' : '查看参数说明'}
        </button>
      </div>
      {paramsOpen && (
        <div className="param-detail">
          <div className="param-detail-grid">
            {Object.entries(params).map(([k, v]) => (
              <div className="param-detail-item" key={k}>
                <div className="pd-row">
                  <span className="pd-name">{v.label}</span>
                  <span className="pd-value mono">{v.value}</span>
                </div>
                <div className="pd-desc">{v.desc}</div>
                {(v.low_hint || v.high_hint) && (
                  <div className="pd-hints">
                    {v.low_hint && <span>↓ {v.low_hint}</span>}
                    {v.high_hint && <span>↑ {v.high_hint}</span>}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────────────────── KPI strip ───────────────────── */
function KpiStrip({ summary }) {
  const items = [
    { label: '今日推荐', value: summary.today_picks, tone: 'pick',  sub: '量价 + 历史共振' },
    { label: '刚金叉',   value: summary.just_cross,  tone: 'pos',   sub: '5 日内出现' },
    { label: '即将金叉', value: summary.imminent,    tone: 'warn',  sub: '5 日内预期穿越' },
    { label: '持仓期',   value: summary.holding,     tone: 'info',  sub: '已金叉待持仓' },
    { label: '历史有效', value: summary.with_history,tone: 'mute',  sub: `共 ${summary.total.toLocaleString()} 只`, big: false },
  ];
  return (
    <div className="kpi-strip">
      {items.map(i => (
        <div className={`kpi kpi-${i.tone}`} key={i.label}>
          <div className="kpi-label">{i.label}</div>
          <div className="kpi-value mono">{i.value.toLocaleString()}</div>
          <div className="kpi-sub">{i.sub}</div>
        </div>
      ))}
    </div>
  );
}

/* ───────────────────── List panel ───────────────────── */
const QUICK_FILTERS = [
  { id: 'all',     label: '全部'     },
  { id: 'today',   label: '今日推荐' },
  { id: 'just',    label: '刚金叉'   },
  { id: 'near',    label: '即将金叉' },
  { id: 'holding', label: '持仓期'   },
  { id: 'death',   label: '刚死叉'   },
  { id: 'wait',    label: '等待'     },
  { id: 'history', label: '历史有效' },
];

const HISTORY_OPTIONS = [
  { id: 'ok',                   label: '有效' },
  { id: 'insufficient_history', label: '样本不足' },
  { id: 'no_signal',            label: '无信号' },
  { id: 'too_few_signals',      label: '信号不足' },
];
const WIN_OPTIONS    = [ { v: 0.40, label: '≥40%' }, { v: 0.50, label: '≥50%' }, { v: 0.60, label: '≥60%' }, { v: 0.70, label: '≥70%' } ];
const RET_OPTIONS    = [ { v: 0,    label: '≥0' },   { v: 0.05, label: '≥5%' },  { v: 0.10, label: '≥10%' }, { v: 0.20, label: '≥20%' } ];
const CALMAR_OPTIONS = [ { v: 0.5,  label: '≥0.5' }, { v: 1.0,  label: '≥1.0' }, { v: 2.0,  label: '≥2.0' } ];

const COLUMNS = [
  { key: 'recommendation', label: '建议',    sortable: true, width: 96 },
  { key: 'status',         label: '状态',    sortable: true, width: 90 },
  { key: 'code',           label: '代码',    sortable: true, width: 78 },
  { key: 'name',           label: '名称',    sortable: false, width: 108 },
  { key: 'trade_ref_holding_days', label: '周期', sortable: true, width: 60, align: 'right' },
  { key: 'trade_buy_price',label: '买入价',  sortable: true, width: 72, align: 'right' },
  { key: 'trade_eval_price',label: '最新价', sortable: true, width: 72, align: 'right' },
  { key: 'trade_ref_ret',  label: '验证收益', sortable: true, width: 80, align: 'right' },
  { key: 'win_rate',       label: '胜率',    sortable: true, width: 70, align: 'right' },
  { key: 'avg_ret',        label: '均收益',  sortable: true, width: 78, align: 'right' },
  { key: 'avg_dd',         label: '均回撤',  sortable: true, width: 78, align: 'right' },
  { key: 'calmar',         label: 'Calmar',  sortable: true, width: 70, align: 'right' },
  { key: 'signal_count',   label: '信号',    sortable: true, width: 56, align: 'right' },
  { key: 'sell_hint',      label: '提示',    sortable: false, width: 112 },
];

const recOrder = (r) => {
  if (r.is_buy_point) return 1;
  return { '刚金叉': 2, '即将金叉': 3, '持仓期': 4, '刚死叉': 5, '等待': 6 }[r.status] || 9;
};
const statusOrder = (s) => ({ '刚金叉': 1, '即将金叉': 2, '持仓期': 3, '刚死叉': 4, '等待': 5 }[s] || 9);

function cmpVal(a, b, key) {
  if (key === 'recommendation') return recOrder(a) - recOrder(b);
  if (key === 'status') return statusOrder(a.status) - statusOrder(b.status);
  if (key === 'code') return String(a.code).localeCompare(b.code);
  const va = a[key], vb = b[key];
  if (va == null && vb == null) return 0;
  if (va == null) return 1;
  if (vb == null) return -1;
  return va - vb;
}

function ListPanel({ stocks, selectedCode, onSelect, density }) {
  const { StatusBadge, RecommendationTag, Num } = window.UI;
  const [quick, setQuick] = useStateM('all');
  const [search, setSearch] = useStateM('');
  const [histF, setHistF] = useStateM('');     // '' | 'ok' | 'insufficient_history' | ...
  const [minWin, setMinWin] = useStateM(null); // null | number (decimal e.g. 0.5)
  const [minRet, setMinRet] = useStateM(null);
  const [minCal, setMinCal] = useStateM(null);
  const [sort, setSort] = useStateM({ key: 'recommendation', dir: 1 });

  const filtered = useMemoM(() => {
    const q = search.trim().toLowerCase();
    const rows = stocks.filter(r => {
      if (quick === 'today'   && !r.is_buy_point) return false;
      if (quick === 'just'    && r.status !== '刚金叉')   return false;
      if (quick === 'near'    && r.status !== '即将金叉') return false;
      if (quick === 'holding' && r.status !== '持仓期')   return false;
      if (quick === 'death'   && r.status !== '刚死叉')   return false;
      if (quick === 'wait'    && r.status !== '等待')     return false;
      if (quick === 'history' && r.history_status !== 'ok') return false;
      if (q && !`${r.code} ${r.name} ${r.industry} ${r.archetype}`.toLowerCase().includes(q)) return false;
      if (histF && r.history_status !== histF) return false;
      if (minWin != null && !(r.win_rate >= minWin)) return false;
      if (minRet != null && !(r.avg_ret >= minRet)) return false;
      if (minCal != null && !(r.calmar  >= minCal)) return false;
      return true;
    });
    rows.sort((a, b) => sort.dir * cmpVal(a, b, sort.key));
    return rows;
  }, [stocks, quick, search, histF, minWin, minRet, minCal, sort]);

  const counts = useMemoM(() => ({
    all:     stocks.length,
    today:   stocks.filter(s => s.is_buy_point).length,
    just:    stocks.filter(s => s.status === '刚金叉').length,
    near:    stocks.filter(s => s.status === '即将金叉').length,
    holding: stocks.filter(s => s.status === '持仓期').length,
    death:   stocks.filter(s => s.status === '刚死叉').length,
    wait:    stocks.filter(s => s.status === '等待').length,
    history: stocks.filter(s => s.history_status === 'ok').length,
  }), [stocks]);

  const toggleSort = (key) => {
    if (!COLUMNS.find(c => c.key === key)?.sortable) return;
    setSort(s => s.key === key ? { key, dir: -s.dir } : { key, dir: ['trade_ref_ret','win_rate','avg_ret','calmar','signal_count'].includes(key) ? -1 : 1 });
  };

  // single-choice toggle helper: clicking the active option clears it
  const pickThreshold = (current, v, setter) => setter(current === v ? null : v);
  const pickEnum = (current, v, setter) => setter(current === v ? '' : v);

  const activeCount =
    (quick !== 'all' ? 1 : 0) +
    (histF ? 1 : 0) +
    (minWin != null ? 1 : 0) +
    (minRet != null ? 1 : 0) +
    (minCal != null ? 1 : 0) +
    (search ? 1 : 0);

  const resetAll = () => {
    setQuick('all'); setSearch(''); setHistF('');
    setMinWin(null); setMinRet(null); setMinCal(null);
    setSort({ key: 'recommendation', dir: 1 });
  };

  return (
    <div className="panel list-panel">
      {/* Header */}
      <div className="list-head">
        <div>
          <div className="list-title">全部股票</div>
          <div className="list-hint">按「建议」综合排序：今日推荐 → 买入窗口 → 提前关注 → 持仓 → 风险 → 等待</div>
        </div>
        <div className="list-count">
          <span className="mono">{filtered.length.toLocaleString()}</span>
          <span className="list-count-sep"> / </span>
          <span className="mono mute">{stocks.length.toLocaleString()}</span>
        </div>
      </div>

      {/* Quick filter chips — every option carries a count */}
      <div className="chips">
        {QUICK_FILTERS.map(f => (
          <button key={f.id} className={`chip ${quick === f.id ? 'active' : ''}`} onClick={() => setQuick(f.id)}>
            {f.label}
            <span className="chip-count mono">{(counts[f.id] ?? 0).toLocaleString()}</span>
          </button>
        ))}
        <div className="spacer" />
        <div className={`search-wrap ${search ? 'has-val' : ''}`}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
          <input placeholder="搜索代码 / 名称 / 行业"
                 value={search}
                 onChange={e => setSearch(e.target.value)} />
          {search && (
            <button className="search-clear" onClick={() => setSearch('')} aria-label="清除">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
            </button>
          )}
        </div>
      </div>

      {/* Combination filter — chip groups instead of dropdowns */}
      <div className="filter-shelf">
        <FilterGroup label="历史">
          <Pill active={histF === ''} onClick={() => setHistF('')}>不限</Pill>
          {HISTORY_OPTIONS.map(o => (
            <Pill key={o.id} active={histF === o.id} onClick={() => pickEnum(histF, o.id, setHistF)}>{o.label}</Pill>
          ))}
        </FilterGroup>
        <FilterGroup label="胜率">
          <Pill active={minWin === null} onClick={() => setMinWin(null)}>不限</Pill>
          {WIN_OPTIONS.map(o => (
            <Pill key={o.v} active={minWin === o.v} onClick={() => pickThreshold(minWin, o.v, setMinWin)}>{o.label}</Pill>
          ))}
        </FilterGroup>
        <FilterGroup label="均收益">
          <Pill active={minRet === null} onClick={() => setMinRet(null)}>不限</Pill>
          {RET_OPTIONS.map(o => (
            <Pill key={o.v} active={minRet === o.v} onClick={() => pickThreshold(minRet, o.v, setMinRet)}>{o.label}</Pill>
          ))}
        </FilterGroup>
        <FilterGroup label="Calmar">
          <Pill active={minCal === null} onClick={() => setMinCal(null)}>不限</Pill>
          {CALMAR_OPTIONS.map(o => (
            <Pill key={o.v} active={minCal === o.v} onClick={() => pickThreshold(minCal, o.v, setMinCal)}>{o.label}</Pill>
          ))}
        </FilterGroup>
        <div className="spacer" />
        <button className={`reset-btn ${activeCount === 0 ? 'is-disabled' : ''}`}
                onClick={resetAll}
                disabled={activeCount === 0}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>
          </svg>
          重置
          {activeCount > 0 && <span className="reset-count">{activeCount}</span>}
        </button>
      </div>

      {/* Table */}
      <div className={`table-wrap density-${density}`}>
        <table className="data-table">
          <colgroup>{COLUMNS.map(c => <col key={c.key} style={{ width: c.width }} />)}</colgroup>
          <thead>
            <tr>
              {COLUMNS.map(c => {
                const active = sort.key === c.key;
                return (
                  <th key={c.key} className={window.UI.cls(c.sortable && 'sortable', active && 'active', c.align && `align-${c.align}`)}
                      onClick={() => toggleSort(c.key)}>
                    <span className="th-label">{c.label}</span>
                    {c.sortable && (
                      <span className={`th-arrow ${active ? 'on' : ''}`}>{active ? (sort.dir > 0 ? '↑' : '↓') : '↕'}</span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={COLUMNS.length} className="empty-row">没有符合条件的股票</td></tr>
            ) : filtered.map(s => (
              <tr key={s.code} className={selectedCode === s.code ? 'selected' : ''} onClick={() => onSelect(s.code)}>
                <td><RecommendationTag row={s} /></td>
                <td><StatusBadge status={s.status} /></td>
                <td className="mono cell-code">{s.code}</td>
                <td className="cell-name">{s.name}<div className="cell-name-sub">{s.industry}</div></td>
                <td className="mono align-right">{s.trade_ref_holding_days}d</td>
                <td className="mono align-right">{s.trade_buy_price != null ? `¥${s.trade_buy_price.toFixed(2)}` : '—'}</td>
                <td className="mono align-right">{s.trade_eval_price != null ? `¥${s.trade_eval_price.toFixed(2)}` : '—'}</td>
                <td className="align-right"><Num value={s.trade_ref_ret} pct signed /></td>
                <td className="mono align-right">{s.win_rate != null ? (s.win_rate*100).toFixed(1)+'%' : '—'}</td>
                <td className="align-right"><Num value={s.avg_ret} pct signed /></td>
                <td className="align-right"><Num value={s.avg_dd} pct signed /></td>
                <td className="mono align-right">{s.calmar != null ? s.calmar.toFixed(2) : '—'}</td>
                <td className="mono align-right mute">{s.has_history ? s.signal_count : '—'}</td>
                <td className="cell-hint">
                  {s.status === '持仓期' && s.sell_hint && <span className="hint-info">{s.sell_hint}</span>}
                  {s.status === '即将金叉' && s.days_event != null && <span className="hint-warn">≤{s.days_event}天</span>}
                  {s.status === '刚金叉' && s.days_event != null && <span className="hint-pos">{s.days_event}天前</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

window.TopBar = TopBar;
window.StrategyBar = StrategyBar;
window.KpiStrip = KpiStrip;
window.ListPanel = ListPanel;

/* ───────────────────── Filter chip helpers ───────────────────── */
function FilterGroup({ label, children }) {
  return (
    <div className="fgroup">
      <span className="fgroup-label">{label}</span>
      <div className="fgroup-pills">{children}</div>
    </div>
  );
}

function Pill({ active, onClick, children }) {
  return (
    <button className={`pill ${active ? 'active' : ''}`} onClick={onClick} type="button">
      {children}
    </button>
  );
}
