/* v3 · Tab 3 — 持仓 (Portfolio + Model Performance)
   Phase η+++++++ Walk-Forward Backtest 头部 (全期 800+ 日, 真实回测)
   + Paper Engine 120 日 NAV / KPI / P&L / IC / 当前持仓 (Phase δ)
*/
const { useState: useStatePF, useMemo: useMemoPF, useEffect: useEffectPF } = React;

function PagePortfolio({ onOpenStock }) {
  const { NAV_SERIES, KPIS, PL_ATTR, SIGNAL_IC, HOLDINGS, UI } = window.CMV3;

  // η+++++++ ψ.3 新 endpoint: /api/v3/portfolio/backtest (全期 800 日)
  const [backtest, setBacktest] = useStatePF(null);
  useEffectPF(() => {
    fetch('/api/v3/portfolio/backtest?sample=400')
      .then(r => r.json()).then(setBacktest).catch(() => setBacktest(null));
  }, []);

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      {/* ============ Phase η+++++++ ψ.3 — Walk-Forward Backtest 头部 ============ */}
      {backtest && backtest.ok && <WalkForwardSection bt={backtest} UI={UI}/>}

      {/* NAV 曲线 + 6 KPI (Phase δ Paper Engine, 120 日) */}
      <div style={{display:'grid',gridTemplateColumns:'2fr 1fr',gap:12}}>
        <UI.Card title="Paper NAV — 120 日" action={<UI.ApiTag>mart_paper_nav</UI.ApiTag>}>
          <NavChart series={NAV_SERIES}/>
        </UI.Card>
        <UI.Card title="6 KPI" action={<UI.ApiTag>mart_paper_nav (聚合)</UI.ApiTag>}>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
            <UI.KStat k="累计超额"   v={UI.fmtPct(KPIS.excess_pct,1)}  tone="pos"/>
            <UI.KStat k="Sharpe"     v={KPIS.sharpe.toFixed(2)}/>
            <UI.KStat k="最大回撤"   v={UI.fmtPct(KPIS.max_dd_pct,1)}   tone="neg"/>
            <UI.KStat k="月度胜率"   v={UI.fmtPct(KPIS.monthly_win,0)}/>
            <UI.KStat k="换手"       v={KPIS.turnover.toFixed(2)+'x'}/>
            <UI.KStat k="行业集中"   v={UI.fmtPct(KPIS.top_industry_pct,0)}/>
          </div>
        </UI.Card>
      </div>

      {/* P&L 归因 + 信号 IC */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
        <UI.Card title="P&L 归因 (近 30 日)" action={<UI.ApiTag>mart_decision_outcome</UI.ApiTag>}>
          {PL_ATTR.map(p => (
            <div key={p.k} style={{display:'grid',gridTemplateColumns:'90px 1fr 80px 60px',gap:10,alignItems:'center',padding:'7px 0',borderTop:'1px solid var(--line-soft)'}}>
              <div style={{fontSize:12,color:'var(--ink-1)'}}>{p.k}</div>
              <UI.ScoreBar value={p.pct*100} color={p.k.includes('alpha')?'#2f8a55':'var(--ink-2)'}/>
              <div style={{fontSize:12,fontFamily:'var(--f-mono)',textAlign:'right',fontWeight:600}}>{UI.fmtMoney(p.v)}</div>
              <div style={{fontSize:11,fontFamily:'var(--f-mono)',textAlign:'right',color:'var(--ink-3)'}}>{(p.pct*100).toFixed(0)}%</div>
            </div>
          ))}
        </UI.Card>
        <UI.Card title="6 信号 60d 滚动 RankIC" action={<UI.ApiTag>mart_signal_ic</UI.ApiTag>}>
          {SIGNAL_IC.map(s => (
            <div key={s.signal} style={{display:'grid',gridTemplateColumns:'140px 1fr 50px',gap:10,alignItems:'center',padding:'7px 0',borderTop:'1px solid var(--line-soft)'}}>
              <div style={{fontSize:12,color:'var(--ink-1)'}}>{s.signal}</div>
              <UI.ScoreBar value={s.ic*100} max={50} color={s.ic>=0.3?'#2f8a55':s.ic>=0.2?'var(--accent)':'var(--ink-3)'}/>
              <div style={{fontSize:12,fontFamily:'var(--f-mono)',textAlign:'right',fontWeight:600}}>{s.ic.toFixed(2)}</div>
            </div>
          ))}
        </UI.Card>
      </div>

      {/* 当前持仓 */}
      <UI.Card title={`当前持仓 · ${HOLDINGS.length}`} action={<UI.ApiTag>fact_paper_position</UI.ApiTag>}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
          <thead>
            <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
              <th style={{padding:'8px 6px'}}>代码 / 名称</th>
              <th style={{padding:'8px 6px'}}>Stage</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>持有</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>未实现收益</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>已实现 R</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>距 T1</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>权重</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>状态</th>
            </tr>
          </thead>
          <tbody>
            {HOLDINGS.map(h => (
              <tr key={h.code} onClick={()=>onOpenStock(h.code)} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
                <td style={{padding:'10px 6px'}}>
                  <div style={{fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--ink-0)'}}>{h.code}</div>
                  <div style={{fontSize:10,color:'var(--ink-3)'}}>{h.name}</div>
                </td>
                <td style={{padding:'10px 6px'}}><UI.StageDot stage={h.stage}/></td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{h.days}d</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:h.ret>=0?'#2f8a55':'#c4382e'}}>{UI.fmtPct(h.ret)}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:h.rr>=0?'#2f8a55':'#c4382e'}}>{h.rr>=0?'+':''}{h.rr.toFixed(1)}R</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{UI.fmtPct(h.toT1)}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600}}>{(h.weight*100).toFixed(1)}%</td>
                <td style={{padding:'10px 6px',textAlign:'right'}}>
                  {h.action==='SELL' ? <UI.Pill tone="sell" size="xs">触发卖出</UI.Pill> : <UI.Pill tone="hold" size="xs">持有</UI.Pill>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </UI.Card>
    </div>
  );
}

function WalkForwardSection({ bt, UI }) {
  const m = bt.metrics.strategy;
  const e = bt.metrics.excess;
  const b = bt.metrics.benchmark;
  const uc = bt.user_criteria;
  const period = bt.period;

  // 3 标准颜色 — 全 PASS 亮绿大字
  const allPass = bt.all_user_criteria_pass;

  return (
    <UI.Card title={`η+++++++ Walk-Forward Backtest · ${period.start} → ${period.end} (${period.n_days} 日)`}
             action={<UI.ApiTag>portfolio_backtest_nav.csv · 实时算</UI.ApiTag>}>

      {/* 用户 3 标准 PASS 大字横幅 */}
      <div style={{
          display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:10,marginBottom:12,
          padding:'10px 12px',
          background: allPass ? 'rgba(47,138,85,.06)' : 'rgba(196,56,46,.04)',
          border: `1px solid ${allPass ? 'rgba(47,138,85,.2)' : 'rgba(196,56,46,.2)'}`,
          borderRadius:6,
      }}>
        <CriterionTile label="年化收益 ≥ 30%"          c={uc.annual_return_ge_30pct}     fmt={v => `${(v*100).toFixed(1)}%`}/>
        <CriterionTile label="最大回撤 ≥ -20% (不缩水)" c={uc.max_dd_ge_neg_20pct}       fmt={v => `${(v*100).toFixed(1)}%`}/>
        <CriterionTile label="超额 vs HS300 > 0"        c={uc.excess_vs_hs300_positive}  fmt={v => `${(v*100).toFixed(1)}%`}/>
      </div>

      {/* NAV 曲线 + 8 KPI */}
      <div style={{display:'grid',gridTemplateColumns:'2.4fr 1fr',gap:12,marginBottom:12}}>
        <div>
          <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:6,letterSpacing:'.04em',textTransform:'uppercase'}}>NAV 全期曲线 (策略 vs HS300)</div>
          <WalkForwardNavChart curve={bt.nav_curve}/>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:6}}>
          <UI.KStat k="年化" v={`${(m.annual_return*100).toFixed(1)}%`} tone={m.annual_return>=0.30?'pos':null}/>
          <UI.KStat k="总收益" v={`${(m.total_return*100).toFixed(0)}%`} tone="pos"/>
          <UI.KStat k="最大回撤" v={`${(m.max_drawdown*100).toFixed(1)}%`} tone={m.max_drawdown>=-0.20?'pos':'neg'}/>
          <UI.KStat k="Calmar" v={m.calmar.toFixed(2)} tone={m.calmar>=1?'pos':null}/>
          <UI.KStat k="Sharpe" v={m.sharpe.toFixed(2)} tone={m.sharpe>=1?'pos':null}/>
          <UI.KStat k="月度胜率" v={`${(m.monthly_win_rate*100).toFixed(0)}%`}/>
          <UI.KStat k="超额收益" v={`${(e.excess_total_return*100).toFixed(0)}%`} tone={e.excess_total_return>0?'pos':'neg'}/>
          <UI.KStat k="信息比率 IR" v={(e.information_ratio||0).toFixed(2)} tone={(e.information_ratio||0)>=1?'pos':null}/>
        </div>
      </div>

      {/* regime 分层 (突出"不缩水") */}
      {bt.regime_segments && bt.regime_segments.length > 0 && (
        <div>
          <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:6,letterSpacing:'.04em',textTransform:'uppercase'}}>
            Regime 分层 — 关键: 熊市段策略是否仍能"不缩水"
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:8}}>
            {bt.regime_segments.map(s => {
              const tone = s.regime === 'bull' ? '#2f8a55'
                          : s.regime === 'bear' ? (s.cumulative_return >= -0.02 ? '#2f8a55' : '#c4382e')
                          : 'var(--accent)';
              const label = s.regime === 'bull' ? '🐂 牛市段' : s.regime === 'bear' ? '🐻 熊市段' : '〰 震荡段';
              return (
                <div key={s.regime} style={{padding:'10px 12px',background:'var(--bg-2)',borderRadius:5,borderLeft:`3px solid ${tone}`}}>
                  <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.05em',textTransform:'uppercase'}}>{label}</div>
                  <div style={{fontSize:18,fontWeight:600,fontFamily:'var(--f-mono)',color:tone,marginTop:4}}>
                    {s.cumulative_return >= 0 ? '+' : ''}{(s.cumulative_return*100).toFixed(1)}%
                  </div>
                  <div style={{fontSize:10,color:'var(--ink-3)',marginTop:2,fontFamily:'var(--f-mono)'}}>
                    {s.n_days} 日 · 日均 {(s.avg_daily_ret*100).toFixed(3)}%
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{fontSize:10,color:'var(--ink-3)',marginTop:6,fontStyle:'italic'}}>
            熊市段评定: 累计 ≥ -2% 视为"不缩水" (绿) · 用户终极目标"短期内资产最大幅度增值不缩水"
          </div>
        </div>
      )}

      {/* HS300 基准 */}
      <div style={{marginTop:12,fontSize:11,color:'var(--ink-3)',padding:'8px 12px',background:'var(--bg-2)',borderRadius:5,
                    display:'flex',gap:14}}>
        <span>HS300 基准 (同期):</span>
        <span>年化 <b style={{fontFamily:'var(--f-mono)',color:'var(--ink-2)'}}>{(b.annual_return*100).toFixed(1)}%</b></span>
        <span>max_dd <b style={{fontFamily:'var(--f-mono)',color:'var(--ink-2)'}}>{(b.max_drawdown*100).toFixed(1)}%</b></span>
        <span>Sharpe <b style={{fontFamily:'var(--f-mono)',color:'var(--ink-2)'}}>{b.sharpe.toFixed(2)}</b></span>
        <span style={{marginLeft:'auto'}}>数据源: scripts/portfolio_backtest.py</span>
      </div>
    </UI.Card>
  );
}

function CriterionTile({ label, c, fmt }) {
  const passColor = c.pass ? '#2f8a55' : '#c4382e';
  return (
    <div style={{padding:'6px 10px'}}>
      <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.04em'}}>{label}</div>
      <div style={{display:'flex',alignItems:'baseline',gap:6,marginTop:3}}>
        <span style={{fontSize:22,fontWeight:700,fontFamily:'var(--f-mono)',color:passColor}}>
          {c.pass ? '✅' : '❌'}
        </span>
        <span style={{fontSize:18,fontWeight:600,fontFamily:'var(--f-mono)',color:passColor}}>
          {fmt(c.actual)}
        </span>
        <span style={{fontSize:10,color:'var(--ink-3)',fontFamily:'var(--f-mono)'}}>
          / 目标 {fmt(c.target)}
        </span>
      </div>
    </div>
  );
}

function WalkForwardNavChart({ curve }) {
  const W = 720, H = 220, P = 24;
  if (!curve || curve.length < 2) return <div style={{padding:20,color:'var(--ink-3)'}}>—</div>;
  const s = curve.map(d => d.strategy_nav);
  const b = curve.map(d => d.benchmark_nav);
  const s0 = s[0], b0 = b[0];
  // 归一化到 1.0 起点
  const sN = s.map(v => v / s0), bN = b.map(v => v / b0);
  const all = [...sN, ...bN];
  const min = Math.min(...all), max = Math.max(...all), r = (max - min) || 1;
  const xs = i => P + (i/(curve.length-1)) * (W - P*2);
  const ys = v => H - P - ((v - min)/r) * (H - P*2);
  const path = arr => arr.map((v,i) => `${i?'L':'M'}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(' ');

  // y 轴 ticks: 1x, 1.5x, 2x, 2.5x, ...
  const ticks = [];
  for (let v = Math.floor(min*10)/10; v <= max; v += 0.5) {
    if (v >= min && v <= max) ticks.push(v);
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:'auto',display:'block',background:'var(--bg-2)',borderRadius:5}}>
      {/* y grid */}
      {ticks.map(v => (
        <g key={v}>
          <line x1={P} y1={ys(v)} x2={W-P} y2={ys(v)} stroke="var(--line)" strokeWidth="0.5" opacity="0.4"/>
          <text x={P-4} y={ys(v)+3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">{v.toFixed(1)}x</text>
        </g>
      ))}
      {/* HS300 dashed */}
      <path d={path(bN)} fill="none" stroke="var(--ink-3)" strokeWidth="1.2" strokeDasharray="4,3" opacity="0.7"/>
      {/* Strategy solid */}
      <path d={path(sN)} fill="none" stroke="var(--accent)" strokeWidth="1.8"/>
      {/* End labels */}
      <g fontSize="10" fontFamily="var(--f-mono)" fontWeight="600">
        <text x={W-4} y={ys(sN[sN.length-1])-4} textAnchor="end" fill="var(--accent)">策略 {sN[sN.length-1].toFixed(2)}x</text>
        <text x={W-4} y={ys(bN[bN.length-1])+12} textAnchor="end" fill="var(--ink-3)">HS300 {bN[bN.length-1].toFixed(2)}x</text>
      </g>
      {/* x 轴 起 / 中 / 止 日期 */}
      <g fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">
        <text x={P} y={H-6} textAnchor="start">{curve[0].date}</text>
        <text x={W/2} y={H-6} textAnchor="middle">{curve[Math.floor(curve.length/2)].date}</text>
        <text x={W-P} y={H-6} textAnchor="end">{curve[curve.length-1].date}</text>
      </g>
    </svg>
  );
}

function NavChart({ series }) {
  const W = 720, H = 200, P = 20;
  const navs = series.map(d=>d.nav), hss = series.map(d=>d.hs300), eqs = series.map(d=>d.eq);
  const all = [...navs,...hss,...eqs];
  const min = Math.min(...all), max = Math.max(...all), r = max-min;
  const xs = i => P + (i/(series.length-1))*(W-P*2);
  const ys = v => H-P - ((v-min)/r)*(H-P*2);
  const path = arr => arr.map((v,i) => `${i?'L':'M'}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(' ');

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:'auto',display:'block'}}>
      <path d={path(hss)} fill="none" stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="3,3" opacity="0.6"/>
      <path d={path(eqs)} fill="none" stroke="var(--ink-2)" strokeWidth="1" opacity="0.5"/>
      <path d={path(navs)} fill="none" stroke="var(--accent)" strokeWidth="1.6"/>
      <g fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">
        <text x={W-4} y={ys(navs[navs.length-1])-4} textAnchor="end" fill="var(--accent)" fontWeight="600">NAV</text>
        <text x={W-4} y={ys(hss[hss.length-1])+10} textAnchor="end">HS300</text>
        <text x={W-4} y={ys(eqs[eqs.length-1])-4} textAnchor="end">等权</text>
      </g>
    </svg>
  );
}

window.CMV3.PagePortfolio = PagePortfolio;
