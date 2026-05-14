/* Chunky Monkey v3 — 机构研究 + ETF 页 */

const { useState: useStateI } = React;

/* ============================================================
   Page 3 · 机构研究 (rename from 机构榜)
   关键改进: 紧凑布局, 字段不留大空白; 10/30/60/90/120d 全收益期
   ============================================================ */
function PageInstitutions({ openInst }) {
  const { INST_DB, RolePill, MiniSpark, ApiTag, fmtPct } = window.CMV3;
  const [role, setRole] = useStateI('all');
  const [minEvents, setMinEvents] = useStateI(8);
  const [holdingPeriod, setHoldingPeriod] = useStateI('60d');
  const [sortKey, setSortKey] = useStateI('composite');

  const roles = ['all', ...new Set(INST_DB.map(i => i.role))];
  const gainKey = `buy_avg_gain_${holdingPeriod}`;

  const filtered = INST_DB
    .filter(i => (role === 'all' || i.role === role) && i.buy_event_count >= minEvents)
    .sort((a,b) => {
      if (sortKey === 'composite') return (b.win_rate * b.trust_weight) - (a.win_rate * a.trust_weight);
      if (sortKey === 'win_rate')  return b.win_rate - a.win_rate;
      if (sortKey === 'gain')      return b[gainKey] - a[gainKey];
      if (sortKey === 'events')    return b.buy_event_count - a.buy_event_count;
      return 0;
    });

  return (
    <div>
      <window.PageHead
        crumbs={[['研究'],['机构研究', true]]}
        title="机构研究"
        sub={`研究 ${INST_DB.length} 家机构的 track record。默认按"胜率 × 信任度"复合分排序, 自动过滤样本数 < ${minEvents} 的机构。`}
        api={<ApiTag path="/api/institution/rank"/>}
        actions={[['导入新机构'],['建立机构组合','primary']]}
      />

      {/* 筛选条 */}
      <div className="cm-card" style={{padding:'10px 14px',marginBottom:14,display:'flex',gap:14,alignItems:'center',flexWrap:'wrap'}}>
        <span className="muted" style={{fontSize:11}}>类型</span>
        <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
          {roles.map(t => (
            <button key={t} onClick={() => setRole(t)} style={{
              padding:'4px 10px',borderRadius:14,fontSize:11,
              background: role===t?'var(--c-ink-100)':'var(--c-bg-2)',
              color: role===t?'#fff':'var(--c-ink-70)',
              fontWeight: role===t?500:400,
            }}>{t==='all'?'全部':t}</button>
          ))}
        </div>
        <div style={{width:1,alignSelf:'stretch',background:'var(--c-line)'}}/>
        <span className="muted" style={{fontSize:11}}>持有期</span>
        <div style={{display:'inline-flex',background:'var(--c-bg-2)',borderRadius:5,padding:2}}>
          {['10d','30d','60d','90d','120d'].map(p => (
            <button key={p} onClick={() => setHoldingPeriod(p)} style={{
              padding:'3px 9px',borderRadius:3,fontSize:11,fontFamily:'var(--f-mono)',
              background: holdingPeriod===p?'var(--c-surface)':'transparent',
              color: holdingPeriod===p?'var(--c-ink-100)':'var(--c-ink-55)',
              fontWeight: holdingPeriod===p?600:400,
              boxShadow: holdingPeriod===p?'var(--sh-1)':'none',
            }}>{p}</button>
          ))}
        </div>
        <div style={{width:1,alignSelf:'stretch',background:'var(--c-line)'}}/>
        <span className="muted" style={{fontSize:11}}>最少样本</span>
        <input type="range" min="1" max="40" value={minEvents} onChange={e=>setMinEvents(+e.target.value)} style={{width:100}}/>
        <span className="mono" style={{fontSize:11,fontWeight:600,minWidth:20}}>{minEvents}</span>
        <div style={{flex:1}}/>
        <span className="muted-2 mono" style={{fontSize:11}}>{filtered.length} / {INST_DB.length} 家</span>
      </div>

      {/* 紧凑表格 — 信息密度高 */}
      <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
        <table className="cm-table cm-table-dense">
          <thead><tr>
            <th style={{width:30}}>#</th>
            <th style={{width:240}}>机构 (inst_uid / inst_name)</th>
            <th style={{width:64}}>类型</th>
            <th className="num sortable" style={{width:54}} onClick={()=>setSortKey('events')}>事件</th>
            <th className="num sortable" style={{width:60}} onClick={()=>setSortKey('win_rate')}>胜率</th>
            <th className="num">avg 10d</th>
            <th className="num">avg 30d</th>
            <th className="num sortable" onClick={()=>setSortKey('gain')}>avg {holdingPeriod}</th>
            <th className="num">avg 90d</th>
            <th className="num">avg 120d</th>
            <th className="num" style={{width:54}}>trust</th>
            <th style={{width:82}}>近 12 次</th>
            <th style={{width:20}}/>
          </tr></thead>
          <tbody>
            {filtered.map((i, idx) => (
              <tr key={i.inst_uid} style={{cursor:'pointer'}} onClick={() => openInst(i)}>
                <td className="mono muted-2" style={{fontSize:11}}>{idx+1}</td>
                <td>
                  <div style={{fontWeight:500,fontSize:12}}>{i.alias}</div>
                  <div className="muted-2 mono" style={{fontSize:10,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',maxWidth:230}}>
                    {i.inst_uid} · {i.inst_name}
                  </div>
                </td>
                <td><RolePill role={i.role}/></td>
                <td className="num mono">{i.buy_event_count}</td>
                <td className="num mono" style={{fontWeight:600,color:i.win_rate>=0.6?'var(--c-accent)':i.win_rate>=0.5?'var(--c-warn)':'var(--c-bad)'}}>
                  {Math.round(i.win_rate*100)}%
                </td>
                <td className={`num mono ${i.buy_avg_gain_10d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_10d)}</td>
                <td className={`num mono ${i.buy_avg_gain_30d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_30d)}</td>
                <td className={`num mono ${i[gainKey]>=0?'up':'down'}`} style={{fontWeight:600,background: sortKey==='gain' ? 'var(--c-bg-2)' : 'transparent'}}>
                  {fmtPct(i[gainKey])}
                </td>
                <td className={`num mono ${i.buy_avg_gain_90d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_90d)}</td>
                <td className={`num mono ${i.buy_avg_gain_120d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_120d)}</td>
                <td className="num mono">{i.trust_weight.toFixed(2)}</td>
                <td><MiniSpark data={i.recent12}/></td>
                <td style={{color:'var(--c-ink-40)'}}>›</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{height:24}}/>
    </div>
  );
}

/* ============================================================
   Page 4 · ETF (NEW — 来自 etf_router)
   ============================================================ */
function PageETF() {
  const { ETF_LIST, ApiTag, fmtPct, MiniSpark } = window.CMV3;
  const [selected, setSelected] = useStateI(ETF_LIST[0].etf_code);
  const e = ETF_LIST.find(x => x.etf_code === selected);

  return (
    <div>
      <window.PageHead
        crumbs={[['研究'],['ETF', true]]}
        title="ETF 跟随分析"
        sub={'把「跟随机构」的逻辑套到 ETF — 看哪些 ETF 的成分股集中被高 trust 机构买入。适合不想选单股的用户。'}
        api={<ApiTag path="/api/etf/list"/>}
        actions={[['同步 ETF 列表'],['计算 ETF 评分','primary']]}
      />

      <div style={{display:'grid',gridTemplateColumns:'380px 1fr',gap:14}}>
        {/* 左: ETF 列表 */}
        <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
          <div style={{padding:'10px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
            <h3 style={{fontSize:13,fontWeight:600}}>ETF · {ETF_LIST.length}</h3>
            <ApiTag path="/api/etf/list"/>
          </div>
          <div>
            {ETF_LIST.map(etf => (
              <div key={etf.etf_code} onClick={() => setSelected(etf.etf_code)} style={{
                padding:'12px 14px',borderBottom:'1px solid var(--c-line)',cursor:'pointer',
                background: selected===etf.etf_code ? 'var(--c-bg-2)' : 'var(--c-surface)',
                borderLeft: selected===etf.etf_code ? '2px solid var(--c-accent)' : '2px solid transparent',
              }}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline'}}>
                  <div style={{fontWeight:500,fontSize:13}}>{etf.etf_name}</div>
                  <span className="mono muted-2" style={{fontSize:10}}>{etf.etf_code}</span>
                </div>
                <div style={{display:'flex',gap:10,marginTop:4,fontSize:11}}>
                  <span className="muted">机构覆盖 <span className="mono" style={{color:'var(--c-ink-85)',fontWeight:500}}>{etf.inst_coverage}</span></span>
                  <span className="muted">评分 <span className="mono" style={{color:'var(--c-accent-fg)',fontWeight:600}}>{etf.follow_score}</span></span>
                  <span className={`mono ${etf.gain_60d>=0?'up':'down'}`} style={{marginLeft:'auto'}}>{fmtPct(etf.gain_60d)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 右: 详情 */}
        <div style={{display:'flex',flexDirection:'column',gap:14}}>
          <div className="cm-card" style={{padding:16}}>
            <div className="cm-section-h">
              <div>
                <h3 style={{fontSize:15}}>{e.etf_name} · {e.etf_code}</h3>
                <span className="desc">{e.theme} · 规模 {e.aum}</span>
              </div>
              <ApiTag path={`/api/etf/${e.etf_code}/profile`}/>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:8,marginTop:12}}>
              <window.KStatSm k="跟随评分"   v={e.follow_score} sub="0~100" hi/>
              <window.KStatSm k="机构覆盖"   v={e.inst_coverage} sub="高 trust 机构数"/>
              <window.KStatSm k="成分股命中" v={`${e.holdings_hit}/${e.total_holdings}`} sub="在 picks 中"/>
              <window.KStatSm k="60d 收益"   v={fmtPct(e.gain_60d)} tone={e.gain_60d>=0?'up':'down'}/>
              <window.KStatSm k="管理费"     v={e.fee} sub="年化"/>
            </div>
          </div>

          <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
            <div style={{padding:'10px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
              <h3 style={{fontSize:13,fontWeight:600}}>成分股 · 机构买入命中</h3>
              <ApiTag path={`/api/etf/${e.etf_code}/holdings`}/>
            </div>
            <table className="cm-table">
              <thead><tr>
                <th>代码</th><th>名称</th>
                <th className="num">权重</th>
                <th className="num">机构数</th>
                <th className="num">评分</th>
                <th className="num">60d</th>
              </tr></thead>
              <tbody>
                {e.holdings.map(h => (
                  <tr key={h.stock_code}>
                    <td className="mono">{h.stock_code}</td>
                    <td style={{fontWeight:500}}>{h.stock_name}</td>
                    <td className="num mono">{(h.weight*100).toFixed(2)}%</td>
                    <td className="num mono" style={{fontWeight:h.inst_count>=2?600:400,color:h.inst_count>=2?'var(--c-accent)':'var(--c-ink-55)'}}>{h.inst_count}</td>
                    <td className="num mono" style={{fontWeight:600}}>{h.score || '—'}</td>
                    <td className={`num mono ${h.gain_60d>=0?'up':'down'}`}>{h.gain_60d ? fmtPct(h.gain_60d) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div style={{height:24}}/>
    </div>
  );
}

window.PageInstitutions = PageInstitutions;
window.PageETF = PageETF;
