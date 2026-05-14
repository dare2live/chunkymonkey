/* v3 · Tab 1 — 今日 (Today's Brief)
   核心: 跑批状态条 + 4 行动桶 (BUY/SELL/HOLD/WATCH) + 风险监控
*/
const { useState: useStateT } = React;

function PageToday({ onOpenStock }) {
  const { RUN_META, STOCKS, RISK, FORMULAS, UI } = window.CMV3;

  const buys   = STOCKS.filter(s => s.action === 'BUY');
  const sells  = STOCKS.filter(s => s.action === 'SELL');
  const holds  = STOCKS.filter(s => s.action === 'HOLD');
  const watches= STOCKS.filter(s => s.action === 'WATCH');

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      {/* ============ 跑批摘要条 ============ */}
      <div style={{background:'#fff',border:'1px solid var(--line)',borderRadius:8,padding:'12px 16px',display:'flex',alignItems:'center',gap:24,flexWrap:'wrap'}}>
        <div>
          <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.06em',textTransform:'uppercase'}}>计划交易日 (T+1)</div>
          <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)'}}>{RUN_META.plan_date}</div>
          <div style={{fontSize:10,color:'var(--ink-3)',marginTop:2}}>信号日 {RUN_META.signal_date} · 跑批 {RUN_META.duration_min}m</div>
        </div>
        <div style={{width:1,height:36,background:'var(--line)'}}/>
        <div>
          <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.06em',textTransform:'uppercase'}}>Paper NAV</div>
          <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)'}}>{UI.fmtMoney(RUN_META.nav)}</div>
          <div style={{fontSize:10,fontFamily:'var(--f-mono)',color:RUN_META.nav_chg_pct>=0?'#2f8a55':'#c4382e',marginTop:2}}>
            {UI.fmtPct(RUN_META.nav_chg_pct,2)} 今日
          </div>
        </div>
        <div style={{width:1,height:36,background:'var(--line)'}}/>
        <div>
          <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.06em',textTransform:'uppercase'}}>vs HS300</div>
          <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:'#2f8a55'}}>{UI.fmtPct(RUN_META.vs_hs300_pct,1)}</div>
          <div style={{fontSize:10,color:'var(--ink-3)',marginTop:2}}>等权 {UI.fmtPct(RUN_META.vs_eq_pct,1)}</div>
        </div>
        <div style={{flex:1}}/>
        {RUN_META.challenger_pending > 0 && (
          <div style={{padding:'8px 14px',background:'rgba(232,93,49,.08)',border:'1px solid rgba(232,93,49,.30)',borderRadius:6}}>
            <div style={{fontSize:10,color:'var(--accent)',fontWeight:600,letterSpacing:'.04em'}}>! CHALLENGER 待审</div>
            <div style={{fontSize:12,color:'var(--ink-1)',marginTop:2}}>{RUN_META.challenger_pending} 个候选模型超过 champion</div>
          </div>
        )}
        <UI.ApiTag>mart_pipeline_run_manifest</UI.ApiTag>
      </div>

      {/* ============ 4 行动桶 ============ */}
      <div style={{display:'grid',gridTemplateColumns:'1.4fr .8fr',gap:12}}>
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          <ActionBucket title="买入候选" tone="buy" rows={buys} onOpenStock={onOpenStock}/>
          <ActionBucket title="持有 / 跟进" tone="hold" rows={holds} onOpenStock={onOpenStock}/>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          <ActionBucket title="卖出 / 减仓" tone="sell" rows={sells} onOpenStock={onOpenStock} compact/>
          <ActionBucket title="观察池" tone="watch" rows={watches} onOpenStock={onOpenStock} compact/>
          <RiskCard risk={RISK}/>
        </div>
      </div>

      {/* ============ 今日 7 公式命中 ============ */}
      <UI.Card title="今日 7 公式命中" action={<UI.ApiTag>fact_technical_trigger</UI.ApiTag>}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:8}}>
          {FORMULAS.map(f => (
            <div key={f.id} style={{padding:10,border:'1px solid var(--line-soft)',borderRadius:6,background:'var(--bg-2)'}}>
              <div style={{display:'flex',alignItems:'center',gap:6,fontSize:12,fontWeight:600,color:'var(--ink-1)'}}>
                <span style={{fontSize:9,fontFamily:'var(--f-mono)',fontWeight:700,color:'var(--accent)',background:'#fff',border:'1px solid var(--line)',borderRadius:3,padding:'1px 4px'}}>{f.tag}</span>{f.name}
              </div>
              <div style={{display:'flex',alignItems:'baseline',gap:8,marginTop:6}}>
                <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)'}}>{f.hit_today}</div>
                <div style={{fontSize:10,color:'var(--ink-3)'}}>命中 / 胜率 {(f.win_rate*100).toFixed(0)}% · {f.horizon}d</div>
              </div>
            </div>
          ))}
        </div>
      </UI.Card>
    </div>
  );
}

function ActionBucket({ title, tone, rows, onOpenStock, compact=false }) {
  const { UI } = window.CMV3;
  return (
    <UI.Card title={`${title} · ${rows.length}`}
             action={<UI.Pill tone={tone}>{tone.toUpperCase()}</UI.Pill>}>
      {rows.length === 0 ? (
        <div style={{padding:'16px 0',textAlign:'center',color:'var(--ink-3)',fontSize:12}}>今日无</div>
      ) : (
        <div style={{display:'flex',flexDirection:'column'}}>
          {rows.map((s, i) => (
            <div key={s.code} onClick={() => onOpenStock(s.code)}
                 style={{display:'grid',gridTemplateColumns: compact ? '52px 1fr auto' : '52px 1fr 110px 1fr 80px',gap:10,alignItems:'center',padding:'10px 0',borderTop:i?'1px solid var(--line-soft)':'none',cursor:'pointer'}}>
              <div>
                <UI.StockTag code={s.code} name={s.name} size="sm" layout="stacked"/>
              </div>
              {!compact && (
                <div>
                  <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:3}}>
                    <UI.StageDot stage={s.technical_stage}/>
                    <span style={{fontSize:10,color:'var(--ink-3)'}}>· {s.primary_type}</span>
                  </div>
                  <div style={{fontSize:11,color:'var(--ink-2)',lineHeight:1.4}}>{s.reason}</div>
                </div>
              )}
              {compact && (
                <div style={{fontSize:11,color:'var(--ink-2)',lineHeight:1.4}}>
                  <div style={{display:'flex',alignItems:'center',gap:6,marginBottom:2}}><UI.StageDot stage={s.technical_stage}/></div>
                  {s.reason}
                </div>
              )}
              {!compact && (
                <div>
                  <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:2}}>分 / RR</div>
                  <div style={{fontSize:13,fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--ink-0)'}}>{s.score} · {s.risk_reward_ratio?.toFixed(2) || '—'}</div>
                </div>
              )}
              {!compact && (
                <div>
                  <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:2}}>建仓 / T1 / 止损</div>
                  <div style={{fontSize:11,fontFamily:'var(--f-mono)',color:'var(--ink-1)'}}>
                    {s.entry_target_price?.toFixed(2) || '—'} / <span style={{color:'#2f8a55'}}>{s.exit_target_1?.toFixed(2) || '—'}</span> / <span style={{color:'#c4382e'}}>{s.exit_stop?.toFixed(2) || '—'}</span>
                  </div>
                </div>
              )}
              {!compact && (
                <div style={{textAlign:'right'}}>
                  <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:2}}>权重</div>
                  <div style={{fontSize:12,fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--ink-0)'}}>{s.weight_pct ? s.weight_pct.toFixed(1)+'%' : '—'}</div>
                </div>
              )}
              {compact && <div style={{textAlign:'right',fontSize:11,fontFamily:'var(--f-mono)',color:'var(--ink-1)'}}>{s.score}</div>}
            </div>
          ))}
        </div>
      )}
    </UI.Card>
  );
}

function RiskCard({ risk }) {
  const { UI } = window.CMV3;
  return (
    <UI.Card title="组合风险" action={<UI.ApiTag>mart_portfolio_risk_snapshot</UI.ApiTag>}>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
        <UI.KStat k="当前回撤"      v={UI.fmtPct(risk.current_dd_pct,1)}     tone="neg"/>
        <UI.KStat k="本月换手"      v={risk.monthly_turnover.toFixed(2)+'x'}/>
        <UI.KStat k="最重行业"      v={risk.top_industry}                     sub={UI.fmtPct(risk.top_industry_pct,0)}/>
        <UI.KStat k="Stage 2 占比" v={UI.fmtPct(risk.stage2_pct,0)}           sub={'现金 '+UI.fmtPct(risk.cash_pct,0)}/>
      </div>
    </UI.Card>
  );
}

window.CMV3.PageToday = PageToday;
