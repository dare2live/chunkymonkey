/* Chunky Monkey v3 · BestChoice tab
 *
 * 2026-05-22 Layer 4 UI 挂载: read-only challenger data, 不动 champion.
 *
 * 数据源:
 *   - /api/v3/bestchoice/overview — counts + paper_sim KPI rows
 *   - /api/v3/bestchoice/candidates — top candidates by score
 *   - /api/v3/bestchoice/daily_picks?signal_date=... — daily picks
 *   - /api/v3/bestchoice/complementarity — Phase 4 overlap vs baseline
 *
 * 展示:
 *   1. Header: BC overview (n_candidates / n_stocks / score range / data_latest_date)
 *   2. Paper_sim KPI table (BC challenger vs V4 baseline vs Ensemble vs Stability)
 *   3. Complementarity card (shared stocks / same-day-same-stock / overlap %)
 *   4. Top candidates table (score-ranked, click → onOpenStock)
 *   5. Daily picks for latest signal_date (table with rank_in_date)
 */

const { useState: useStateBC, useEffect: useEffectBC } = React;

window.CMV3 = window.CMV3 || {};
window.CMV3.PageBestChoice = function PageBestChoice({ onOpenStock }) {
  const { UI } = window.CMV3 || {};
  const [overview, setOverview] = useStateBC(null);
  const [candidates, setCandidates] = useStateBC([]);
  const [dailyPicks, setDailyPicks] = useStateBC(null);
  const [comp, setComp] = useStateBC(null);
  const [loading, setLoading] = useStateBC(true);
  const [error, setError] = useStateBC(null);

  useEffectBC(() => {
    setLoading(true);
    Promise.all([
      fetch('/api/v3/bestchoice/overview').then(r => r.json()),
      fetch('/api/v3/bestchoice/candidates?limit=20').then(r => r.json()),
      fetch('/api/v3/bestchoice/daily_picks?limit=20').then(r => r.json()),
      fetch('/api/v3/bestchoice/complementarity').then(r => r.json()),
    ]).then(([ov, cand, dp, c]) => {
      if (ov.error) setError(ov.error);
      setOverview(ov);
      setCandidates(cand.candidates || []);
      setDailyPicks(dp);
      setComp(c);
      setLoading(false);
    }).catch(err => {
      setError(String(err));
      setLoading(false);
    });
  }, []);

  if (loading) return <div style={{ padding: 20 }}>Loading BestChoice tab ...</div>;
  if (error) return <div style={{ padding: 20, color: 'tomato' }}>Error: {error}</div>;

  const kpi = overview?.paper_sim_kpi || [];
  // dedupe by model_id, keep latest built_at
  const kpiByModel = {};
  kpi.forEach(k => {
    if (!kpiByModel[k.model_id] || k.built_at > kpiByModel[k.model_id].built_at) {
      kpiByModel[k.model_id] = k;
    }
  });
  const kpiRows = Object.values(kpiByModel);

  return (
    <div style={{ padding: 16, color: 'var(--ink-0)' }}>
      <h2 style={{ marginTop: 0 }}>BestChoice (Layer 4 read-only tab)</h2>
      <p style={{ color: 'var(--ink-3)', fontSize: 13 }}>
        BestChoice 是 sibling repo 输出的 formula × stock × params 候选池, 作为 challenger 跟 V4 champion 互补 alpha 探索.
        本 tab 仅展示 challenger 数据, 不影响 champion production.
      </p>

      {/* 1. Overview */}
      <section style={{ marginTop: 16 }}>
        <h3>Overview</h3>
        <table style={{ borderCollapse: 'collapse' }}>
          <tbody>
            <tr><td style={cellStyle}>n_candidates</td><td style={cellValueStyle}>{overview.n_candidates}</td></tr>
            <tr><td style={cellStyle}>n_stocks</td><td style={cellValueStyle}>{overview.n_stocks}</td></tr>
            <tr><td style={cellStyle}>n_formulas</td><td style={cellValueStyle}>{overview.n_formulas}</td></tr>
            <tr><td style={cellStyle}>score range</td><td style={cellValueStyle}>[{overview.score_range?.[0]}, {overview.score_range?.[1]}]</td></tr>
            <tr><td style={cellStyle}>avg win_rate</td><td style={cellValueStyle}>{(overview.avg_win_rate * 100).toFixed(2)}%</td></tr>
            <tr><td style={cellStyle}>data_latest_date</td><td style={cellValueStyle}>{overview.data_latest_date}</td></tr>
          </tbody>
        </table>
      </section>

      {/* 2. Paper_sim KPI compare */}
      <section style={{ marginTop: 24 }}>
        <h3>Paper_sim KPI (vs champion + ensemble)</h3>
        <table style={tableStyle}>
          <thead>
            <tr style={trHeadStyle}>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>Sharpe</th>
              <th style={thStyle}>Ann ret</th>
              <th style={thStyle}>Max DD</th>
              <th style={thStyle}>Win rate</th>
              <th style={thStyle}>RankIC</th>
              <th style={thStyle}>Period</th>
            </tr>
          </thead>
          <tbody>
            {kpiRows.map((k, i) => (
              <tr key={i} style={{ borderBottom: '1px solid var(--bg-2)' }}>
                <td style={tdStyle}>
                  <div style={{ fontWeight: 600 }}>{k.model_label}</div>
                  <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>{k.model_id}</div>
                </td>
                <td style={tdStyle}>{k.sharpe?.toFixed(2)}</td>
                <td style={tdStyle}>{k.ann_ret ? `${(k.ann_ret * 100).toFixed(2)}%` : '-'}</td>
                <td style={tdStyle}>{k.max_dd ? `${(k.max_dd * 100).toFixed(2)}%` : '-'}</td>
                <td style={tdStyle}>{k.win_rate ? `${(k.win_rate * 100).toFixed(1)}%` : '-'}</td>
                <td style={tdStyle}>{k.rank_ic ?? '-'}</td>
                <td style={tdStyle}>{k.period_start} → {k.period_end}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* 3. Complementarity */}
      {comp && !comp.empty && (
        <section style={{ marginTop: 24 }}>
          <h3>Complementarity (vs V4 baseline)</h3>
          <table style={{ borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={cellStyle}>comparison_id</td><td style={cellValueStyle}>{comp.comparison_id}</td></tr>
              <tr><td style={cellStyle}>BC picks / stocks</td><td style={cellValueStyle}>{comp.bc_picks} BUYs / {comp.bc_unique_stocks} stocks</td></tr>
              <tr><td style={cellStyle}>Baseline picks / stocks</td><td style={cellValueStyle}>{comp.baseline_picks} BUYs / {comp.baseline_unique_stocks} stocks</td></tr>
              <tr><td style={cellStyle}>Shared stocks</td><td style={cellValueStyle}>{comp.shared_stocks} ({comp.overlap_pct}%)</td></tr>
              <tr><td style={cellStyle}>Same-day same-stock</td><td style={cellValueStyle}>{comp.same_day_same_stock}</td></tr>
            </tbody>
          </table>
          <p style={{ fontSize: 12, color: 'var(--ink-3)' }}>
            6.6% overlap + 0 same-day same-stock = 真互补 alpha source (plan §5 Phase 4 interpretation: integrate as ensemble component).
          </p>
        </section>
      )}

      {/* 4. Top candidates */}
      <section style={{ marginTop: 24 }}>
        <h3>Top {candidates.length} candidates (by score)</h3>
        <table style={tableStyle}>
          <thead>
            <tr style={trHeadStyle}>
              <th style={thStyle}>Stock</th>
              <th style={thStyle}>Formula</th>
              <th style={thStyle}>Sell rule</th>
              <th style={thStyle}>Hold</th>
              <th style={thStyle}>Score</th>
              <th style={thStyle}>Win</th>
              <th style={thStyle}>Avg ret</th>
              <th style={thStyle}>Signals</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, i) => (
              <tr key={i} style={{ borderBottom: '1px solid var(--bg-2)', cursor: onOpenStock ? 'pointer' : 'default' }}
                  onClick={() => onOpenStock && onOpenStock(c.stock_code)}>
                <td style={tdStyle}><a style={{ color: 'var(--accent)' }}>{c.stock_code}</a></td>
                <td style={tdStyle}>{c.formula_id}</td>
                <td style={tdStyle}>{c.sell_rule}</td>
                <td style={tdStyle}>{c.holding_days}d</td>
                <td style={tdStyle}>{c.score}</td>
                <td style={tdStyle}>{(c.win_rate * 100).toFixed(0)}%</td>
                <td style={tdStyle}>{(c.avg_ret * 100).toFixed(2)}%</td>
                <td style={tdStyle}>{c.signal_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* 5. Daily picks */}
      {dailyPicks && dailyPicks.signal_date && (
        <section style={{ marginTop: 24 }}>
          <h3>Latest signal_date picks ({dailyPicks.signal_date})</h3>
          <table style={tableStyle}>
            <thead>
              <tr style={trHeadStyle}>
                <th style={thStyle}>Rank</th>
                <th style={thStyle}>Stock</th>
                <th style={thStyle}>Formula</th>
                <th style={thStyle}>Buy date</th>
                <th style={thStyle}>Hold</th>
                <th style={thStyle}>Sell rule</th>
                <th style={thStyle}>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {dailyPicks.picks.map((p, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--bg-2)', cursor: onOpenStock ? 'pointer' : 'default' }}
                    onClick={() => onOpenStock && onOpenStock(p.stock_code)}>
                  <td style={tdStyle}>{p.rank_in_date}</td>
                  <td style={tdStyle}><a style={{ color: 'var(--accent)' }}>{p.stock_code}</a></td>
                  <td style={tdStyle}>{p.formula_id}</td>
                  <td style={tdStyle}>{p.buy_date}</td>
                  <td style={tdStyle}>{p.holding_days}d</td>
                  <td style={tdStyle}>{p.sell_rule}</td>
                  <td style={tdStyle}>{p.confidence_score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <p style={{ marginTop: 32, fontSize: 11, color: 'var(--ink-3)' }}>
        Caveat: BC candidates 来自 30% holdout split + full-period Optuna, 不是 walk-forward 真 OOS.
        Sharpe 1.10 含 selection bias artifact. 跟 V4 champion ensemble (Sharpe 1.83) 后续需 walk-forward audit 解 caveat.
      </p>
    </div>
  );
};

const cellStyle = { padding: '4px 12px', color: 'var(--ink-3)', fontSize: 13 };
const cellValueStyle = { padding: '4px 12px', fontWeight: 600 };
const tableStyle = { borderCollapse: 'collapse', width: '100%', marginTop: 8 };
const trHeadStyle = { background: 'var(--bg-1)', textAlign: 'left' };
const thStyle = { padding: '8px 12px', fontWeight: 600, fontSize: 13, color: 'var(--ink-1)' };
const tdStyle = { padding: '6px 12px', fontSize: 13 };
