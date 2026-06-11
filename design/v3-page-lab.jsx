/* v3 · Tab 2 — 选股台 (Picking Lab)
   5 子 tab: 综合优选 / 公式库 / 适配矩阵 / 机构视图 / 优选追踪
*/
const { useState: useStatePL, useMemo: useMemoPL } = React;

function PageLab({ onOpenStock }) {
  const { UI } = window.CMV3;
  const [tab, setTab] = useStatePL('composite');

  const TABS = [
    { id:'composite', label:'综合优选'  },
    { id:'formula',   label:'公式库'    },
    { id:'fitness',   label:'适配矩阵'  },
    { id:'inst',      label:'机构视图'  },
    { id:'selection', label:'优选追踪'  },
  ];

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <div style={{background:'#fff',border:'1px solid var(--line)',borderRadius:8,padding:'4px',display:'inline-flex',alignSelf:'flex-start',gap:2}}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  style={{padding:'6px 14px',fontSize:12,fontWeight:600,letterSpacing:'.02em',borderRadius:5,border:'none',cursor:'pointer',
                    background: tab===t.id ? 'var(--ink-0)' : 'transparent',
                    color: tab===t.id ? '#fff' : 'var(--ink-2)'}}>
            {t.label}
          </button>
        ))}
      </div>

      {tab==='composite' && <CompositeBoard onOpenStock={onOpenStock}/>}
      {tab==='formula'   && <FormulaLibrary/>}
      {tab==='fitness'   && <FitnessMatrix/>}
      {tab==='inst'      && <InstView/>}
      {tab==='selection' && <SelectionBoard onOpenStock={onOpenStock}/>}
    </div>
  );
}

/* ============ 综合优选 (Top 50) ============ */
function CompositeBoard({ onOpenStock }) {
  const { STOCKS, UI } = window.CMV3;
  const rows = [...STOCKS].sort((a,b) => (b.score||0)-(a.score||0));
  return (
    <UI.Card title={`今日候选 · Top ${rows.length}`} action={<UI.ApiTag>mart_daily_recommendation · composite_score</UI.ApiTag>}>
      <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
        <thead><tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
          <th style={{padding:'8px 6px',width:32}}>#</th>
          <th style={{padding:'8px 6px'}}>代码 / 名称</th>
          <th style={{padding:'8px 6px'}}>行业</th>
          <th style={{padding:'8px 6px'}}>类型</th>
          <th style={{padding:'8px 6px'}}>形态</th>
          <th style={{padding:'8px 6px'}}>主公式</th>
          <th style={{padding:'8px 6px',textAlign:'right'}}>综合分</th>
          <th style={{padding:'8px 6px',textAlign:'right'}}>优选 ∑ (胜率)</th>
        </tr></thead>
        <tbody>{rows.map((s,i) => {
          const primary = (s.formulas_hit||[])[0];
          const fundShort = (s.fundamental_stage||'').slice(0,2);
          return (
            <tr key={s.code} onClick={()=>onOpenStock(s.code)} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
              <td style={{padding:'10px 6px',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{i+1}</td>
              <td style={{padding:'10px 6px'}}>
                <UI.StockTag code={s.code} name={s.name} size="sm" layout="stacked"/>
              </td>
              <td style={{padding:'10px 6px',color:'var(--ink-2)'}}>{s.industry_l1}</td>
              <td style={{padding:'10px 6px'}}><UI.Pill size="xs">{s.primary_type}</UI.Pill></td>
              <td style={{padding:'10px 6px',fontFamily:'var(--f-mono)',fontSize:11,color:'var(--ink-2)'}}>{fundShort}/{(s.technical_stage||'').split(' ')[0]}</td>
              <td style={{padding:'10px 6px'}}>{primary ? <UI.FormulaTag id={primary.id}/> : <span style={{color:'var(--ink-3)'}}>—</span>}</td>
              <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:700,color:s.score>=0.8?'#2f8a55':'var(--ink-0)'}}>{(s.score||0).toFixed(2)}</td>
              <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.selection_total||0} <span style={{color:'var(--ink-3)'}}>({s.selection_win_rate?((s.selection_win_rate*100)|0)+'%':'—'})</span></td>
            </tr>
          );
        })}</tbody>
      </table>
    </UI.Card>
  );
}

/* ============ 公式库 ============ */
function FormulaLibrary() {
  const { FORMULAS, UI } = window.CMV3;
  return (
    <UI.Card title={`${FORMULAS.length} 公式 · 当前注册`} action={<UI.ApiTag>fact_technical_trigger · dim_formula_registry</UI.ApiTag>}>
      <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:10}}>
        {FORMULAS.map(f => (
          <div key={f.id} style={{padding:12,border:'1px solid var(--line)',borderRadius:6,background:'var(--bg-1)'}}>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
              <span style={{fontSize:11,fontFamily:'var(--f-mono)',fontWeight:700,letterSpacing:'.04em',color:'var(--accent)',background:'#fff',border:'1px solid var(--line)',borderRadius:4,padding:'3px 7px'}}>{f.tag}</span>
              <div>
                <div style={{fontSize:13,fontWeight:600,color:'var(--ink-0)'}}>{f.name}</div>
                <div style={{fontSize:10,fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{f.id}</div>
              </div>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:6,marginTop:8,paddingTop:8,borderTop:'1px solid var(--line-soft)'}}>
              <div><div style={{fontSize:10,color:'var(--ink-3)'}}>今日命中</div><div style={{fontSize:14,fontFamily:'var(--f-mono)',fontWeight:600}}>{f.hit_today}</div></div>
              <div><div style={{fontSize:10,color:'var(--ink-3)'}}>胜率</div><div style={{fontSize:14,fontFamily:'var(--f-mono)',fontWeight:600,color:f.win_rate>=0.65?'#2f8a55':'var(--ink-0)'}}>{(f.win_rate*100).toFixed(0)}%</div></div>
              <div><div style={{fontSize:10,color:'var(--ink-3)'}}>评估期</div><div style={{fontSize:14,fontFamily:'var(--f-mono)',fontWeight:600}}>{f.horizon}d</div></div>
            </div>
            {f.state_dist && (
              <div style={{marginTop:8,paddingTop:8,borderTop:'1px solid var(--line-soft)',fontSize:11,color:'var(--ink-2)'}}>
                状态 · {Object.entries(f.state_dist).map(([k,v]) => `${k} ${v}`).join(' / ')}
              </div>
            )}
          </div>
        ))}
      </div>
    </UI.Card>
  );
}

/* ============ 适配矩阵 (mart_stage_formula_fitness) ============ */
function FitnessMatrix() {
  const { FITNESS, FORMULAS, STAGES, UI } = window.CMV3;
  const [view, setView] = useStatePL('A'); // A: stage→formula, B: formula→stage

  const lookup = (fund, tech, fid) => FITNESS.find(r => r.fund===fund && r.tech===tech && r.formula_id===fid);

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <UI.Card
        title="形态 × 公式 适配矩阵"
        action={
          <div style={{display:'flex',gap:12,alignItems:'center'}}>
            <UI.ApiTag>mart_stage_formula_fitness</UI.ApiTag>
            <div style={{display:'inline-flex',background:'var(--bg-2)',borderRadius:5,padding:2,gap:2}}>
              <button onClick={()=>setView('A')} style={{padding:'4px 10px',fontSize:11,fontWeight:600,border:'none',borderRadius:4,cursor:'pointer',background:view==='A'?'var(--ink-0)':'transparent',color:view==='A'?'#fff':'var(--ink-2)'}}>视图 A · 形态→公式</button>
              <button onClick={()=>setView('B')} style={{padding:'4px 10px',fontSize:11,fontWeight:600,border:'none',borderRadius:4,cursor:'pointer',background:view==='B'?'var(--ink-0)':'transparent',color:view==='B'?'#fff':'var(--ink-2)'}}>视图 B · 公式→形态</button>
            </div>
          </div>
        }
      >
        <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:10,lineHeight:1.5}}>
          {view==='A'
            ? '问"我这种形态买啥公式"。每格显示胜率 / 最佳持仓周期 · ★ = 该形态最优 (is_recommended) · — = 样本不足 (n<30)'
            : '问"这公式适合什么形态"。每行显示该公式在每个形态的胜率分布,从高到低。'}
        </div>

        {view==='A' && (
          <div style={{overflowX:'auto'}}>
            <table style={{width:'100%',borderCollapse:'collapse',fontSize:11,minWidth:880}}>
              <thead>
                <tr>
                  <th style={{textAlign:'left',padding:'8px 6px',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase',position:'sticky',left:0,background:'#fff',borderRight:'1px solid var(--line)'}}>形态 \ 公式</th>
                  {FORMULAS.map(f => (
                    <th key={f.id} style={{padding:'8px 6px',color:'var(--ink-3)',fontSize:10,fontFamily:'var(--f-mono)',fontWeight:600,textAlign:'center',minWidth:62}} title={f.name}>{f.tag}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {STAGES.map(s => (
                  <tr key={s.fund+s.tech} style={{borderTop:'1px solid var(--line-soft)'}}>
                    <td style={{padding:'8px 6px',whiteSpace:'nowrap',position:'sticky',left:0,background:'#fff',borderRight:'1px solid var(--line)'}}>
                      <div style={{fontSize:11,fontWeight:600,color:'var(--ink-1)'}}>{s.fund}</div>
                      <div style={{fontSize:10,color:'var(--ink-3)',fontFamily:'var(--f-mono)'}}>tech {s.tech}</div>
                    </td>
                    {FORMULAS.map(f => {
                      const r = lookup(s.fund, s.tech, f.id);
                      if (!r) return <td key={f.id} style={{padding:'8px 6px',textAlign:'center',color:'var(--ink-3)'}}>—</td>;
                      const intensity = Math.max(0, Math.min(1, (r.win_rate - 0.4) / 0.45));
                      const bg = r.is_recommended
                        ? 'rgba(58,140,90,0.18)'
                        : r.win_rate >= 0.6 ? `rgba(58,140,90,${0.05+intensity*0.12})` : 'transparent';
                      return (
                        <td key={f.id} style={{padding:'6px 4px',textAlign:'center',background:bg,borderLeft:'1px solid var(--line-soft)'}} title={`n=${r.n_signals} · avg ret ${(r.avg_ret*100).toFixed(1)}% · dd ${(r.avg_dd*100).toFixed(1)}%`}>
                          <div style={{fontFamily:'var(--f-mono)',fontSize:11,fontWeight:600,color:r.is_recommended?'#2f8a55':'var(--ink-0)'}}>{(r.win_rate*100).toFixed(0)}%{r.is_recommended ? ' ★' : ''}</div>
                          <div style={{fontFamily:'var(--f-mono)',fontSize:9,color:'var(--ink-3)'}}>{r.holding_days}d</div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {view==='B' && (
          <div style={{display:'flex',flexDirection:'column',gap:14}}>
            {FORMULAS.map(f => {
              const rows = FITNESS.filter(r => r.formula_id===f.id).sort((a,b) => b.win_rate - a.win_rate);
              if (!rows.length) return null;
              return (
                <div key={f.id} style={{padding:12,background:'var(--bg-2)',border:'1px solid var(--line-soft)',borderRadius:6}}>
                  <div style={{display:'flex',alignItems:'baseline',gap:8,marginBottom:8}}>
                    <UI.FormulaTag id={f.id}/>
                    <span style={{fontSize:13,fontWeight:600}}>{f.name}</span>
                    <span style={{fontSize:10,color:'var(--ink-3)',fontFamily:'var(--f-mono)'}}>{f.id}</span>
                  </div>
                  <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill, minmax(170px, 1fr))',gap:6}}>
                    {rows.map((r,i) => (
                      <div key={i} style={{padding:'6px 8px',background:'#fff',border:`1px solid ${r.is_recommended?'rgba(58,140,90,.4)':'var(--line-soft)'}`,borderRadius:4}}>
                        <div style={{fontSize:11,color:'var(--ink-1)'}}>{r.fund} <span style={{color:'var(--ink-3)'}}>· tech {r.tech}</span></div>
                        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginTop:4}}>
                          <span style={{fontFamily:'var(--f-mono)',fontSize:13,fontWeight:700,color:r.win_rate>=0.65?'#2f8a55':'var(--ink-0)'}}>{(r.win_rate*100).toFixed(0)}%{r.is_recommended?' ★':''}</span>
                          <span style={{fontFamily:'var(--f-mono)',fontSize:10,color:'var(--ink-3)'}}>{r.holding_days}d · n {r.n_signals}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </UI.Card>
    </div>
  );
}

/* ============ 机构视图 (4 子标签) ============ */
function InstView() {
  const { INSTITUTIONS, SIGNIFICANT_HOLDERS, UI } = window.CMV3;
  const [sub, setSub] = useStatePL('tracked');
  const openInst = (id) => window.dispatchEvent(new CustomEvent('cmv3:openInst', { detail: id }));

  const SUB = [
    { id:'tracked',  label:'跟踪列表' },
    { id:'sig',      label:'全市场显著' },
    { id:'compare',  label:'横向对比' },
  ];

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <div style={{display:'inline-flex',background:'var(--bg-2)',borderRadius:5,padding:2,gap:2,alignSelf:'flex-start'}}>
        {SUB.map(t => (
          <button key={t.id} onClick={()=>setSub(t.id)} style={{padding:'5px 12px',fontSize:11,fontWeight:600,border:'none',borderRadius:4,cursor:'pointer',background:sub===t.id?'var(--ink-0)':'transparent',color:sub===t.id?'#fff':'var(--ink-2)'}}>{t.label}</button>
        ))}
        <span style={{fontSize:10,color:'var(--ink-3)',alignSelf:'center',marginLeft:8}}>详情:点击任意行</span>
      </div>

      {sub==='tracked' && (
        <UI.Card title={`跟踪中 · ${INSTITUTIONS.filter(i=>i.tracked).length}`} action={<UI.ApiTag>mart_institution_profile · is_tracked=true</UI.ApiTag>}>
          <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead><tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
              <th style={{padding:'8px 6px'}}>名称</th>
              <th style={{padding:'8px 6px'}}>类型</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>Win 30d</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>Win 60d</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>Win 90d</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>稳定度</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>持仓数</th>
            </tr></thead>
            <tbody>{INSTITUTIONS.filter(i=>i.tracked).map(i => (
              <tr key={i.id} onClick={()=>openInst(i.id)} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
                <td style={{padding:'10px 6px',fontWeight:600}}>{i.alias}<div style={{fontSize:10,color:'var(--ink-3)',fontWeight:400}}>{i.name}</div></td>
                <td style={{padding:'10px 6px'}}><UI.Pill size="xs">{i.type}</UI.Pill></td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{(i.win30*100).toFixed(0)}%</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:i.win60>=0.6?'#2f8a55':'var(--ink-0)'}}>{(i.win60*100).toFixed(0)}%</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{(i.win90*100).toFixed(0)}%</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{i.stability == null ? '—' : (i.stability*100).toFixed(0)}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{i.holdings||'—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </UI.Card>
      )}

      {sub==='sig' && (
        <UI.Card title={`全市场显著股东 · ${SIGNIFICANT_HOLDERS.length}`} action={<UI.ApiTag>mart_significant_holder_all</UI.ApiTag>}>
          <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead><tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
              <th style={{padding:'8px 6px'}}>名称</th>
              <th style={{padding:'8px 6px'}}>类型</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>持仓数</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>Win 60d</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>稳定度</th>
              <th style={{padding:'8px 6px'}}>近期动作</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}></th>
            </tr></thead>
            <tbody>{SIGNIFICANT_HOLDERS.map(h => (
              <tr key={h.id} style={{borderTop:'1px solid var(--line-soft)'}}>
                <td style={{padding:'10px 6px',fontWeight:600}}>{h.name}</td>
                <td style={{padding:'10px 6px'}}><UI.Pill size="xs">{h.type}</UI.Pill></td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{h.holdings}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:h.win60>=0.6?'#2f8a55':'var(--ink-0)'}}>{(h.win60*100).toFixed(0)}%</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{h.stability == null ? '—' : (h.stability*100).toFixed(0)}</td>
                <td style={{padding:'10px 6px',fontSize:11,color:'var(--ink-2)'}}>{h.last_action}</td>
                <td style={{padding:'10px 6px',textAlign:'right'}}>
                  {h.tracked
                    ? <span style={{fontSize:10,color:'var(--ink-3)'}}>已跟踪</span>
                    : <button style={{padding:'3px 8px',background:'var(--accent)',color:'#fff',border:'none',borderRadius:4,fontSize:10,fontWeight:600,cursor:'pointer'}}>加入跟踪</button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </UI.Card>
      )}

      {sub==='compare' && (
        <UI.Card title="横向对比" action={<UI.ApiTag>(client-side join)</UI.ApiTag>}>
          <div style={{padding:16,textAlign:'center',color:'var(--ink-3)',fontSize:12,lineHeight:1.6}}>
            选择 2-5 个机构对比 win_rate / stability / 行业偏好 / 重叠持仓 (Venn)<br/>
            <span style={{fontSize:10}}>—— 占位 ——</span>
          </div>
        </UI.Card>
      )}
    </div>
  );
}

/* ============ 优选追踪 (全市场聚合) ============ */
function SelectionBoard({ onOpenStock }) {
  const { SELECTION_BOARD, FORMULAS, UI } = window.CMV3;
  const [sortBy, setSortBy] = useStatePL('n30');
  const rows = [...SELECTION_BOARD].sort((a,b) => (b[sortBy]||0) - (a[sortBy]||0));

  const fmtFormula = (id) => {
    const f = FORMULAS.find(x => x.id===id);
    return f ? f.tag : id;
  };

  return (
    <UI.Card
      title="全市场优选追踪"
      action={
        <div style={{display:'flex',gap:12,alignItems:'center'}}>
          <UI.ApiTag>mart_stock_selection_summary</UI.ApiTag>
          <div style={{display:'inline-flex',background:'var(--bg-2)',borderRadius:5,padding:2,gap:2}}>
            {[{k:'n30',l:'近30d次数'},{k:'win',l:'胜率'},{k:'avg_ret',l:'平均收益'}].map(o => (
              <button key={o.k} onClick={()=>setSortBy(o.k)} style={{padding:'4px 9px',fontSize:10,fontWeight:600,border:'none',borderRadius:4,cursor:'pointer',background:sortBy===o.k?'var(--ink-0)':'transparent',color:sortBy===o.k?'#fff':'var(--ink-2)'}}>{o.l}</button>
            ))}
          </div>
        </div>
      }
    >
      <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
        <thead><tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
          <th style={{padding:'8px 6px'}}>代码 / 名称</th>
          <th style={{padding:'8px 6px',textAlign:'right'}}>近 30d 次数</th>
          <th style={{padding:'8px 6px',textAlign:'right'}}>历史总次数</th>
          <th style={{padding:'8px 6px',textAlign:'right'}}>历史胜率</th>
          <th style={{padding:'8px 6px',textAlign:'right'}}>平均累计收益</th>
          <th style={{padding:'8px 6px'}}>最近一次</th>
          <th style={{padding:'8px 6px'}}>结果</th>
        </tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.code} onClick={()=>onOpenStock(r.code)} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
            <td style={{padding:'10px 6px'}}><div style={{fontFamily:'var(--f-mono)',fontWeight:600}}>{r.code}</div><div style={{fontSize:10,color:'var(--ink-3)'}}>{r.name}</div></td>
            <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600}}>{r.n30}</td>
            <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.n_total}</td>
            <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:r.win>=0.65?'#2f8a55':r.win<0.5?'#c4382e':'var(--ink-0)'}}>{(r.win*100).toFixed(0)}%</td>
            <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:r.avg_ret>=0?'#2f8a55':'#c4382e'}}>{UI.fmtPct(r.avg_ret,1)}</td>
            <td style={{padding:'10px 6px'}}>
              <div style={{fontFamily:'var(--f-mono)',fontSize:10,color:'var(--ink-3)'}}>{r.last_date}</div>
              <UI.FormulaTag id={r.last_formula}/>
            </td>
            <td style={{padding:'10px 6px'}}>
              <UI.Pill tone={r.last_outcome==='win'?'buy':r.last_outcome==='loss'?'sell':'watch'} size="xs">
                {r.last_outcome==='win'?'胜':r.last_outcome==='loss'?'负':'活跃'}
              </UI.Pill>
            </td>
          </tr>
        ))}</tbody>
      </table>
    </UI.Card>
  );
}

window.CMV3.PageLab = PageLab;
