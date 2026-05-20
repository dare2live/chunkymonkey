/* v3 · 市场感知 (Market Perception) */
const { useState: useStateMP, useEffect: useEffectMP } = React;

function PageMarketPerception() {
  const { UI } = window.CMV3 || {};
  const [snapshot, setSnapshot] = useStateMP(null);
  const [history, setHistory] = useStateMP([]);
  const [health, setHealth] = useStateMP(null);
  const [loading, setLoading] = useStateMP(true);
  const [err, setErr] = useStateMP(null);

  useEffectMP(() => {
    let alive = true;
    Promise.all([
      fetch('/api/v3/market_perception/snapshot').then(r => r.json()),
      fetch('/api/v3/market_perception/history?days=90').then(r => r.json()),
      fetch('/api/v3/market_perception/health').then(r => r.json()),
    ]).then(([snap, hist, hp]) => {
      if (!alive) return;
      if (snap.ok === false) throw new Error(snap.error || 'snapshot failed');
      if (hist.ok === false) throw new Error(hist.error || 'history failed');
      setSnapshot(snap);
      setHistory(hist.data || []);
      setHealth(hp);
      setLoading(false);
    }).catch(e => {
      if (!alive) return;
      setErr(String(e));
      setLoading(false);
    });
    return () => { alive = false; };
  }, []);

  if (loading) return <div style={{padding:24,color:'var(--ink-3)'}}>加载中...</div>;
  if (err) return <div style={{padding:24,color:'crimson'}}>市场感知 API 异常: {err}</div>;

  const d = (snapshot && snapshot.data) || {};
  const regime = d.regime_score;
  const regimeLabel = regime == null ? '—' : (regime > 0.3 ? 'risk_on' : regime < -0.3 ? 'risk_off' : 'mixed');
  const tone = regime == null ? null : regime > 0.3 ? 'pos' : regime < -0.3 ? 'neg' : null;
  const engines = health && health.engines ? Object.entries(health.engines) : [];

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <UI.Card title="Market RegimeEngine" action={<UI.ApiTag>mart_market_perception_daily</UI.ApiTag>}
        foot={`snapshot ${d.snapshot_date || '—'} · built_at ${fmtDateTime(d.built_at)}`}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4, minmax(0, 1fr))',gap:12}}>
          <UI.KStat k="市场情绪" v={regimeLabel} sub={regime == null ? '—' : regime.toFixed(3)} tone={tone}/>
          <UI.KStat k="广度" v={d.breadth_state || '—'} sub={d.breadth_ratio == null ? '—' : UI.fmt2pct(d.breadth_ratio, false)}/>
          <UI.KStat k="波动率" v={d.volatility_state || '—'} sub={d.hs300_vol_20d == null ? '—' : UI.fmt2pct(d.hs300_vol_20d, false)}/>
          <UI.KStat k="阶段" v={d.sentiment_phase || '—'} sub={d.hs300_ret_60d == null ? '—' : `HS300 60d ${UI.fmt2pct(d.hs300_ret_60d)}`}/>
        </div>
      </UI.Card>

      <div style={{display:'grid',gridTemplateColumns:'2fr 1fr',gap:12,alignItems:'stretch'}}>
        <UI.Card title="90 日市场状态时序" action={<UI.ApiTag>/history?days=90</UI.ApiTag>}>
          <RegimeChart rows={history}/>
        </UI.Card>
        <UI.Card title="Engine Status" action={<UI.ApiTag>/health</UI.ApiTag>}>
          <div style={{display:'flex',flexDirection:'column',gap:8}}>
            {engines.map(([name, status]) => (
              <div key={name} style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,padding:'7px 0',borderBottom:'1px solid var(--line-soft)'}}>
                <span style={{fontSize:12,fontWeight:600,color:'var(--ink-1)'}}>{name}</span>
                <StatusBadge status={status}/>
              </div>
            ))}
          </div>
        </UI.Card>
      </div>
    </div>
  );
}

function StatusBadge({ status }) {
  const UI = window.CMV3.UI;
  const tone = status === 'live' ? 'hold' : status === 'stub' ? 'watch' : 'neutral';
  return <UI.Pill tone={tone} size="xs">{status}</UI.Pill>;
}

function RegimeChart({ rows }) {
  const W = 760, H = 240, P = 30;
  if (!rows || rows.length < 2) return <div style={{padding:24,color:'var(--ink-3)'}}>暂无历史数据</div>;
  const vals = rows.flatMap(r => [r.regime_score, scaledBreadth(r.breadth_ratio), scaledVol(r.hs300_vol_20d)]).filter(v => Number.isFinite(v));
  const min = Math.min(-1, ...vals), max = Math.max(1, ...vals), range = max - min || 1;
  const xs = i => P + (i / (rows.length - 1)) * (W - P * 2);
  const ys = v => H - P - ((v - min) / range) * (H - P * 2);
  const path = fn => rows.map((r, i) => `${i ? 'L' : 'M'}${xs(i).toFixed(1)},${ys(fn(r)).toFixed(1)}`).join(' ');
  const ticks = [-1, -0.5, 0, 0.5, 1];
  const mid = rows[Math.floor(rows.length / 2)];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:'auto',display:'block',background:'var(--bg-2)',borderRadius:5}}>
      {ticks.map(v => (
        <g key={v}>
          <line x1={P} y1={ys(v)} x2={W-P} y2={ys(v)} stroke="var(--line)" strokeWidth="0.6" opacity="0.55"/>
          <text x={P-6} y={ys(v)+3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">{v.toFixed(1)}</text>
        </g>
      ))}
      <path d={path(r => r.regime_score)} fill="none" stroke="var(--accent)" strokeWidth="1.8"/>
      <path d={path(r => scaledBreadth(r.breadth_ratio))} fill="none" stroke="#2f8a55" strokeWidth="1.4"/>
      <path d={path(r => scaledVol(r.hs300_vol_20d))} fill="none" stroke="#1e5aaa" strokeWidth="1.4" strokeDasharray="4,3"/>
      <g fontSize="10" fontFamily="var(--f-mono)" fontWeight="600">
        <text x={W-8} y={14} textAnchor="end" fill="var(--accent)">regime_score</text>
        <text x={W-8} y={28} textAnchor="end" fill="#2f8a55">breadth x2-1</text>
        <text x={W-8} y={42} textAnchor="end" fill="#1e5aaa">vol scaled</text>
      </g>
      <g fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">
        <text x={P} y={H-7} textAnchor="start">{rows[0].snapshot_date}</text>
        <text x={W/2} y={H-7} textAnchor="middle">{mid.snapshot_date}</text>
        <text x={W-P} y={H-7} textAnchor="end">{rows[rows.length-1].snapshot_date}</text>
      </g>
    </svg>
  );
}

function scaledBreadth(v) {
  return v == null ? 0 : Number(v) * 2 - 1;
}

function scaledVol(v) {
  if (v == null) return 0;
  return Math.max(-1, Math.min(1, (0.25 - Number(v)) / 0.25));
}

function fmtDateTime(v) {
  if (!v) return '—';
  return String(v).replace('T', ' ').replace(/\.\d+$/, '').replace(/\+00:00$/, ' UTC');
}

window.CMV3 = window.CMV3 || {};
window.CMV3.PageMarketPerception = PageMarketPerception;
