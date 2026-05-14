/* v3 · 单股画像 Drawer
   6 维画像 + 7 公式命中 + Trade plan + 优选追踪 + 历史 outcome
*/
const { useState: useStateD, useMemo: useMemoD } = React;

function StockDrawer({ code, onClose }) {
  const { STOCKS, FORMULAS, SELECTION_HISTORY, UI } = window.CMV3;
  const stock = STOCKS.find(s => s.code === code);
  const [tab, setTab] = useStateD('profile');

  if (!stock) return null;

  return (
    <div onClick={onClose} style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.32)',zIndex:100,display:'flex',justifyContent:'flex-end'}}>
      <div onClick={e=>e.stopPropagation()}
           style={{width:'min(880px, 92vw)',height:'100%',background:'#fff',display:'flex',flexDirection:'column',boxShadow:'-12px 0 32px rgba(0,0,0,0.12)'}}>
        {/* 头部 */}
        <div style={{padding:'16px 22px',borderBottom:'1px solid var(--line)',display:'flex',alignItems:'flex-start',justifyContent:'space-between'}}>
          <div>
            <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:4}}>
              <div style={{fontSize:22,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)'}}>{stock.code}</div>
              <div style={{fontSize:18,fontWeight:600,color:'var(--ink-1)'}}>{stock.name}</div>
              <UI.ActionPill action={stock.action}/>
            </div>
            <div style={{display:'flex',alignItems:'center',gap:10,fontSize:12,color:'var(--ink-3)'}}>
              <span style={{fontFamily:'var(--f-mono)',fontSize:16,fontWeight:600,color:'var(--ink-0)'}}>¥{stock.price.toFixed(2)}</span>
              <span style={{fontFamily:'var(--f-mono)',color:stock.chg_pct>=0?'#2f8a55':'#c4382e'}}>{UI.fmtPct(stock.chg_pct)}</span>
              <span>· {stock.industry_l1}</span>
            </div>
          </div>
          <button onClick={onClose} style={{padding:'4px 10px',background:'transparent',border:'1px solid var(--line)',borderRadius:5,cursor:'pointer',color:'var(--ink-2)',fontSize:12}}>关闭 Esc</button>
        </div>

        {/* tabs */}
        <div style={{padding:'0 22px',borderBottom:'1px solid var(--line)',display:'flex',gap:0}}>
          {[
            {id:'profile',  label:'画像 + 评分'},
            {id:'plan',     label:'交易计划'},
            {id:'history',  label:'优选历史'},
          ].map(t => (
            <button key={t.id} onClick={()=>setTab(t.id)}
              style={{padding:'10px 14px',fontSize:12,fontWeight:600,background:'transparent',border:'none',cursor:'pointer',
                color: tab===t.id ? 'var(--ink-0)' : 'var(--ink-3)',
                borderBottom: tab===t.id ? '2px solid var(--accent)' : '2px solid transparent',
                marginBottom:-1}}>
              {t.label}
            </button>
          ))}
        </div>

        {/* body */}
        <div style={{flex:1,overflowY:'auto',padding:'18px 22px',background:'var(--bg-1)'}}>
          {tab==='profile' && <ProfilePanel stock={stock} formulas={FORMULAS}/>}
          {tab==='plan'    && <PlanPanel stock={stock}/>}
          {tab==='history' && <HistoryPanel stock={stock} history={SELECTION_HISTORY}/>}
        </div>
      </div>
    </div>
  );
}

/* === 画像 + 评分 panel === */
function ProfilePanel({ stock, formulas }) {
  const { UI } = window.CMV3;
  const dims = [
    { k:'1. 主类型',        v: stock.primary_type,        sub: stock.secondary_types?.length ? '+ '+stock.secondary_types.join(' / ') : null },
    { k:'2. 基本面阶段',    v: stock.fundamental_stage,   sub: `${stock.fundamental_stage_days} 天` },
    { k:'3. 技术阶段',      v: `Stage ${stock.technical_stage}`, sub: `${stock.technical_stage_days} 天`, stage:stock.technical_stage },
    { k:'4. 估值',         v: `+${stock.valuation_upside_pct}% 上行`, sub: `PE ${stock.valuation_pe} · 分位 ${(stock.valuation_pe_pctile*100).toFixed(0)}%` },
    { k:'5. 机构信号',      v: `${stock.institution_signal.score} 分`, sub: `${stock.institution_signal.n_insts} 家 · ${stock.institution_signal.top.join(' / ')}` },
    { k:'6. 公式命中',      v: `${stock.formulas_hit.length} / 7`, sub: stock.formulas_hit.length>0 ? '详见下' : '无命中' },
  ];

  return (
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      {/* 6 维画像 */}
      <UI.Card title="六维画像" dense action={<UI.ApiTag>fact_stock_type_daily</UI.ApiTag>}>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:8}}>
          {dims.map(d => (
            <div key={d.k} style={{padding:10,background:'var(--bg-2)',border:'1px solid var(--line-soft)',borderRadius:6}}>
              <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:3,letterSpacing:'.04em'}}>{d.k}</div>
              <div style={{fontSize:13,fontWeight:600,color:'var(--ink-0)'}}>
                {d.stage ? <UI.StageDot stage={d.stage}/> : d.v}
              </div>
              {d.sub && <div style={{fontSize:10,color:'var(--ink-3)',marginTop:3}}>{d.sub}</div>}
            </div>
          ))}
        </div>
      </UI.Card>

      {/* 评分拆解 */}
      <UI.Card title="评分拆解" dense action={<span><span style={{fontSize:24,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)',marginRight:8}}>{stock.score}</span><UI.ApiTag>mart_stock_score_decompose</UI.ApiTag></span>}>
        {[
          { k:'基本面 (温和验证)',  pts: 22, max: 25 },
          { k:'技术 (Stage 1.5→2)', pts: 20, max: 25 },
          { k:'估值 (+22% 上行)',   pts: 18, max: 20 },
          { k:'机构 (3 家)',        pts: 17, max: 20 },
          { k:'公式 (3 命中)',      pts:  9, max: 10 },
          { k:'流动性 / 风险扣减',  pts: -4, max:  0 },
        ].map(s => (
          <div key={s.k} style={{display:'grid',gridTemplateColumns:'180px 1fr 60px',gap:10,alignItems:'center',padding:'6px 0',borderTop:'1px solid var(--line-soft)'}}>
            <div style={{fontSize:11,color:'var(--ink-1)'}}>{s.k}</div>
            <UI.ScoreBar value={Math.abs(s.pts)} max={Math.max(s.max,5)} color={s.pts>=0?'var(--accent)':'#c4382e'}/>
            <div style={{fontSize:12,fontFamily:'var(--f-mono)',fontWeight:600,textAlign:'right',color:s.pts>=0?'var(--ink-0)':'#c4382e'}}>{s.pts>=0?'+':''}{s.pts}</div>
          </div>
        ))}
      </UI.Card>

      {/* 7 公式命中详情 */}
      <UI.Card title="公式命中" dense action={<UI.ApiTag>fact_technical_trigger</UI.ApiTag>}>
        {stock.formulas_hit.length === 0 ? (
          <UI.Empty>当前无公式命中</UI.Empty>
        ) : stock.formulas_hit.map(f => {
          const meta = formulas.find(x => x.id === f.id);
          return (
            <div key={f.id} style={{display:'grid',gridTemplateColumns:'auto 1fr 60px 60px 70px',gap:10,alignItems:'center',padding:'8px 0',borderTop:'1px solid var(--line-soft)'}}>
              <div style={{fontSize:20}}>{meta?.emoji}</div>
              <div>
                <div style={{fontSize:12,fontWeight:600,color:'var(--ink-1)'}}>{meta?.name}</div>
                {f.state && <div style={{fontSize:10,color:'var(--ink-3)'}}>状态 · {f.state}</div>}
              </div>
              <div style={{textAlign:'right'}}>
                <div style={{fontSize:10,color:'var(--ink-3)'}}>强度</div>
                <div style={{fontSize:12,fontFamily:'var(--f-mono)',fontWeight:600}}>{(f.strength*100).toFixed(0)}</div>
              </div>
              <div style={{textAlign:'right'}}>
                <div style={{fontSize:10,color:'var(--ink-3)'}}>历史胜率</div>
                <div style={{fontSize:12,fontFamily:'var(--f-mono)',fontWeight:600,color:f.win>=0.65?'#2f8a55':'var(--ink-0)'}}>{(f.win*100).toFixed(0)}%</div>
              </div>
              <div style={{textAlign:'right'}}>
                <div style={{fontSize:10,color:'var(--ink-3)'}}>持有期</div>
                <div style={{fontSize:12,fontFamily:'var(--f-mono)'}}>{f.horizon}d</div>
              </div>
            </div>
          );
        })}
      </UI.Card>

      {/* 同类历史 */}
      {stock.similar_n && (
        <UI.Card title="同类历史" dense action={<UI.ApiTag>mart_cohort_outcome</UI.ApiTag>}>
          <div style={{display:'flex',alignItems:'center',gap:18,padding:'6px 4px'}}>
            <div>
              <div style={{fontSize:10,color:'var(--ink-3)'}}>样本量</div>
              <div style={{fontSize:18,fontFamily:'var(--f-mono)',fontWeight:700}}>{stock.similar_n}</div>
            </div>
            <div>
              <div style={{fontSize:10,color:'var(--ink-3)'}}>60d 胜率</div>
              <div style={{fontSize:18,fontFamily:'var(--f-mono)',fontWeight:700,color:'#2f8a55'}}>{(stock.similar_win*100).toFixed(0)}%</div>
            </div>
            <div style={{flex:1,fontSize:11,color:'var(--ink-2)',lineHeight:1.5}}>
              过去 24 个月,『{stock.primary_type} + Stage {stock.technical_stage} + {stock.institution_signal.n_insts} 家机构介入』组合共出现 {stock.similar_n} 次,60 日盈利样本占 {(stock.similar_win*100).toFixed(0)}%
            </div>
          </div>
        </UI.Card>
      )}
    </div>
  );
}

/* === 交易计划 === */
function PlanPanel({ stock }) {
  const { UI } = window.CMV3;
  if (stock.action === 'SELL' || stock.action === 'HOLD') {
    return (
      <UI.Card title="持仓状态" action={<UI.ApiTag>fact_paper_position</UI.ApiTag>}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:8}}>
          <UI.KStat k="持有"           v={stock.holding_days + 'd'}/>
          <UI.KStat k="未实现收益"     v={UI.fmtPct(stock.holding_return_pct)}     tone={stock.holding_return_pct>=0?'pos':'neg'}/>
          <UI.KStat k="已实现 R"       v={(stock.rr_realized>=0?'+':'')+stock.rr_realized.toFixed(1)+'R'} tone={stock.rr_realized>=0?'pos':'neg'}/>
          <UI.KStat k="评分变化"       v={stock.score} sub={stock.action==='SELL'?'触发硬规则':'继续持有'}/>
        </div>
        <div style={{marginTop:12,padding:12,background:stock.action==='SELL'?'rgba(217,75,75,.06)':'var(--bg-2)',border:`1px solid ${stock.action==='SELL'?'rgba(217,75,75,.25)':'var(--line)'}`,borderRadius:6,fontSize:12,color:'var(--ink-1)'}}>
          <strong>{stock.action==='SELL' ? '卖出原因' : '持有原因'}</strong>: {stock.reason}
          {stock.risk_note && <div style={{marginTop:6,fontSize:11,color:'#c4382e'}}>风险 · {stock.risk_note}</div>}
        </div>
      </UI.Card>
    );
  }
  return (
    <UI.Card title="交易计划" action={<UI.ApiTag>mart_stock_trade_plan</UI.ApiTag>}>
      {/* Entry zones */}
      <div style={{marginBottom:14}}>
        <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:6,letterSpacing:'.04em'}}>建仓价位</div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:8}}>
          <PriceBox label="保守 (3 跌停内)" price={stock.entry_target_price} tone="ok"/>
          <PriceBox label="标的 (现价)" price={stock.entry_aggressive_price} tone="accent"/>
          <PriceBox label="激进上限 (+5%)" price={stock.entry_max_price} tone="warn"/>
        </div>
      </div>
      {/* Exit zones */}
      <div style={{marginBottom:14}}>
        <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:6,letterSpacing:'.04em'}}>止盈 / 止损</div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:8}}>
          <PriceBox label="T1 (轻仓出 50%)" price={stock.exit_target_1} tone="ok"/>
          <PriceBox label="T2 (清仓)" price={stock.exit_target_2} tone="ok"/>
          <PriceBox label="止损" price={stock.exit_stop} tone="neg"/>
        </div>
      </div>
      {/* RR / horizon */}
      <div style={{display:'flex',gap:18,padding:'12px 0',borderTop:'1px solid var(--line-soft)'}}>
        <div>
          <div style={{fontSize:10,color:'var(--ink-3)'}}>风险回报比</div>
          <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:'#2f8a55'}}>{stock.risk_reward_ratio?.toFixed(2)} R</div>
        </div>
        <div>
          <div style={{fontSize:10,color:'var(--ink-3)'}}>预期持有</div>
          <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)'}}>{stock.expected_horizon_days}d</div>
        </div>
        <div>
          <div style={{fontSize:10,color:'var(--ink-3)'}}>建议仓位</div>
          <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)'}}>{stock.weight_pct?.toFixed(1)}%</div>
        </div>
      </div>
      <div style={{marginTop:8,padding:12,background:'var(--bg-2)',borderRadius:6,fontSize:12,color:'var(--ink-1)',lineHeight:1.6}}>
        <strong style={{color:'var(--accent)'}}>建仓理由</strong> · {stock.reason}
      </div>
    </UI.Card>
  );
}

function PriceBox({ label, price, tone }) {
  const colors = { ok:'#2f8a55', accent:'var(--accent)', warn:'#aa6f12', neg:'#c4382e' };
  return (
    <div style={{padding:10,background:'var(--bg-2)',border:'1px solid var(--line-soft)',borderRadius:6}}>
      <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:3}}>{label}</div>
      <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:colors[tone]}}>{price?.toFixed(2) || '—'}</div>
    </div>
  );
}

/* === 优选历史 === */
function HistoryPanel({ stock, history }) {
  const { UI } = window.CMV3;
  const wins = history.filter(h => h.outcome==='win').length;
  return (
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      <UI.Card title={`优选追踪 — 全局历史样本`} dense action={<UI.ApiTag>mart_stock_selection_summary</UI.ApiTag>}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:8}}>
          <UI.KStat k="近 30d 入选"  v={stock.selection_30d ?? '—'}/>
          <UI.KStat k="累计入选"     v={stock.selection_total ?? '—'}/>
          <UI.KStat k="胜率"         v={UI.fmtPct(stock.selection_win_rate,0)} tone={(stock.selection_win_rate||0)>=0.6?'pos':null}/>
          <UI.KStat k="上次结局"     v={stock.last_outcome === 'win' ? '盈利' : stock.last_outcome === 'loss' ? '亏损' : '持仓中'} tone={stock.last_outcome==='win'?'pos':stock.last_outcome==='loss'?'neg':null}/>
        </div>
      </UI.Card>

      <UI.Card title={`历次入选 outcome · ${history.length} 条 · 胜 ${wins} 负 ${history.length-wins}`} action={<UI.ApiTag>mart_stock_selection_outcome</UI.ApiTag>}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
          <thead>
            <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
              <th style={{padding:'8px 6px'}}>入选日</th>
              <th style={{padding:'8px 6px'}}>触发公式</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>持有期</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>实际收益</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>最大回撤</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>到 T1 天数</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>结局</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h, i) => (
              <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                <td style={{padding:'8px 6px',fontFamily:'var(--f-mono)'}}>{h.selectDate}</td>
                <td style={{padding:'8px 6px'}}>{h.formula}</td>
                <td style={{padding:'8px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{h.horizon}d</td>
                <td style={{padding:'8px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:h.retPct>=0?'#2f8a55':'#c4382e'}}>{UI.fmtPct(h.retPct)}</td>
                <td style={{padding:'8px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:'#c4382e'}}>{UI.fmtPct(h.ddPct)}</td>
                <td style={{padding:'8px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{h.daysToT1 ?? '—'}</td>
                <td style={{padding:'8px 6px',textAlign:'right'}}>
                  {h.outcome === 'win' ? <UI.Pill tone="hold" size="xs">盈利</UI.Pill> : <UI.Pill tone="sell" size="xs">亏损</UI.Pill>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </UI.Card>
    </div>
  );
}

window.CMV3.StockDrawer = StockDrawer;
