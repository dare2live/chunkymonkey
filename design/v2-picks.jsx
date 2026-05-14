/* v2 主页 — 今日可跟随股票 (Today's Picks)
   核心: 排行 + 谁在买 + 评分拆解 + 历史可比收益 */

const { useState: useStateP, useMemo: useMemoP } = React;

function TodayPicks({ openInst }) {
  const { PICKS, HEALTH, EVENTS_30D, MODEL_30D, INST_DB, getInst, MiniSpark, InstChip } = window.CMV2;
  const [expanded, setExpanded] = useStateP(PICKS[0].code);
  const [sort, setSort]         = useStateP('score');

  const picksSorted = useMemoP(() => {
    const arr = [...PICKS];
    if (sort === 'score')   arr.sort((a,b) => b.score - a.score);
    if (sort === 'fresh')   arr.sort((a,b) => b.score - a.score); // simplified
    if (sort === 'consensus') arr.sort((a,b) => b.instCount - a.instCount);
    return arr;
  }, [sort]);

  return (
    <div>
      {/* 顶部:数据 + 模型 健康摘要 (一行) */}
      <HealthBar/>

      {/* 主标题 */}
      <div className="page-head" style={{marginTop:14}}>
        <div>
          <div className="crumbs">
            <span>研究</span><span className="sep">›</span><span className="here">今日可跟随股票</span>
          </div>
          <h1>今日可跟随股票 · 7 只</h1>
          <p>champion_v3 模型基于 312 家机构最近 30 日的 buy 事件 + 历史 60d 跟随收益, 给出今天最值得跟随的股票。每只股票的评分都可拆解, 每家买入机构都可点击看 track record。</p>
        </div>
        <div className="page-actions">
          <button className="btn">导出 CSV</button>
          <button className="btn">订阅推送</button>
          <button className="btn btn-primary">回测当前榜单</button>
        </div>
      </div>

      {/* 工具条 */}
      <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:12}}>
        <span className="muted" style={{fontSize:12}}>排序</span>
        <div style={{display:'inline-flex',background:'var(--c-bg-2)',borderRadius:6,padding:2,gap:1}}>
          {[['score','综合分'],['consensus','共识 (机构数)'],['fresh','新鲜度']].map(([v,l]) => (
            <button key={v} onClick={() => setSort(v)} style={{
              height:26,padding:'0 12px',borderRadius:4,fontSize:12,
              background: sort===v?'var(--c-surface)':'transparent',
              color: sort===v?'var(--c-ink-100)':'var(--c-ink-55)',
              fontWeight: sort===v?600:500,
              boxShadow: sort===v?'var(--sh-1)':'none',
            }}>{l}</button>
          ))}
        </div>
        <div style={{flex:1}}/>
        <span className="muted-2 mono" style={{fontSize:11}}>样本窗口 30d · 持有期 60d · 最小样本 8 次</span>
      </div>

      {/* 排行 */}
      <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
        {picksSorted.map((p, i) => (
          <PickRow
            key={p.code}
            pick={p}
            rank={i+1}
            expanded={expanded === p.code}
            onToggle={() => setExpanded(expanded === p.code ? null : p.code)}
            openInst={openInst}
            isLast={i === picksSorted.length-1}
          />
        ))}
      </div>
    </div>
  );
}

/* -------- 健康摘要条 --------- */
function HealthBar() {
  const { HEALTH, EVENTS_30D, MODEL_30D, MiniSpark } = window.CMV2;
  const items = [
    { k:'数据采集', s:HEALTH.ingest,    extra:null },
    { k:'数据仓库', s:HEALTH.warehouse, extra:<MiniSpark data={EVENTS_30D} width={60} height={18} tone="var(--c-ink-55)"/> },
    { k:'模型',     s:HEALTH.model,     extra:<MiniSpark data={MODEL_30D} width={60} height={18} tone="var(--c-warn)"/> },
    { k:'信号下发', s:HEALTH.signal,    extra:null },
  ];
  const overall = items.some(i => i.s.status === 'bad') ? 'bad'
                : items.some(i => i.s.status === 'warn') ? 'warn' : 'ok';
  const overallText = overall === 'ok' ? '今日数据 + 模型均健康' : overall === 'warn' ? '存在告警, 信号可用但需注意' : '存在故障, 建议暂停跟随';
  const overallTone = overall === 'ok' ? 'var(--c-accent)' : overall === 'warn' ? 'var(--c-warn)' : 'var(--c-bad)';
  return (
    <div style={{
      background:'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:8,
      padding:'10px 14px',display:'flex',alignItems:'center',gap:14,
      borderLeft:`3px solid ${overallTone}`,
    }}>
      <div style={{display:'flex',flexDirection:'column',minWidth:160}}>
        <div style={{fontSize:11,color:'var(--c-ink-55)',textTransform:'uppercase',letterSpacing:'0.04em'}}>SYSTEM HEALTH</div>
        <div style={{fontSize:13,fontWeight:600,color:overallTone}}>{overallText}</div>
      </div>
      <div style={{width:1,alignSelf:'stretch',background:'var(--c-line)'}}/>
      {items.map((it,i) => (
        <div key={i} style={{flex:1,display:'flex',alignItems:'center',gap:10,minWidth:0}}>
          <window.CMV2.StatusDot status={it.s.status}/>
          <div style={{minWidth:0,flex:1}}>
            <div style={{fontSize:11,color:'var(--c-ink-55)'}}>{it.k}</div>
            <div style={{fontSize:12,fontWeight:500,color:'var(--c-ink-85)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{it.s.detail}</div>
          </div>
          {it.extra}
        </div>
      ))}
      <button className="btn btn-sm" style={{whiteSpace:'nowrap'}}>查看链路</button>
    </div>
  );
}

/* -------- 推荐股票一行 --------- */
function PickRow({ pick, rank, expanded, onToggle, openInst, isLast }) {
  const { getInst, InstChip } = window.CMV2;
  const insts = pick.insts.map(getInst);
  const avgTrust = insts.reduce((s,i) => s + i.trust, 0) / insts.length;
  const avgWin   = insts.reduce((s,i) => s + i.win,   0) / insts.length;

  return (
    <div style={{borderBottom: isLast ? '0' : '1px solid var(--c-line)'}}>
      {/* 主行 */}
      <div onClick={onToggle} style={{
        display:'grid',
        gridTemplateColumns:'40px minmax(180px,1.2fr) 100px minmax(280px,1.6fr) 108px 108px 80px 32px',
        gap:14,padding:'14px 16px',alignItems:'center',cursor:'pointer',
        background: expanded ? 'var(--c-bg-2)' : 'var(--c-surface)',
      }} onMouseEnter={e => { if (!expanded) e.currentTarget.style.background='var(--c-bg)'; }}
         onMouseLeave={e => { if (!expanded) e.currentTarget.style.background='var(--c-surface)'; }}>
        {/* rank */}
        <div style={{
          width:32,height:32,borderRadius:8,
          background: rank <= 3 ? 'var(--c-ink-100)' : 'var(--c-bg-2)',
          color: rank <= 3 ? '#fff' : 'var(--c-ink-55)',
          display:'inline-flex',alignItems:'center',justifyContent:'center',
          fontSize:14,fontWeight:600,fontFamily:'var(--f-mono)',
        }}>{rank}</div>

        {/* 股票 */}
        <div style={{minWidth:0}}>
          <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:2}}>
            <span style={{fontSize:15,fontWeight:600,color:'var(--c-ink-100)'}}>{pick.name}</span>
            <span className="mono muted-2" style={{fontSize:11}}>{pick.code}</span>
          </div>
          <div className="muted" style={{fontSize:11,display:'flex',gap:6}}>
            <span>{pick.sector}</span>
            <span>·</span>
            <span className="mono">¥{pick.price}</span>
            <span className="mono" style={{color: pick.chg >= 0 ? 'var(--c-up)' : 'var(--c-down)'}}>
              {pick.chg >= 0 ? '+' : ''}{(pick.chg*100).toFixed(2)}%
            </span>
          </div>
        </div>

        {/* 评分 */}
        <div>
          <div style={{display:'flex',alignItems:'baseline',gap:4}}>
            <span className="mono" style={{fontSize:24,fontWeight:600,color:'var(--c-ink-100)',letterSpacing:'-0.02em'}}>{pick.score}</span>
            <span className="muted-2" style={{fontSize:10}}>/100</span>
          </div>
          <div style={{height:3,background:'var(--c-bg-2)',borderRadius:2,marginTop:2}}>
            <div style={{width:`${pick.score}%`,height:'100%',background:'var(--c-accent)',borderRadius:2}}/>
          </div>
        </div>

        {/* 谁在买 */}
        <div style={{display:'flex',flexWrap:'wrap',gap:5,alignItems:'center'}}>
          {insts.slice(0, 3).map(inst => (
            <InstChip key={inst.id} inst={inst} size="sm"
              onClick={(e) => { e.stopPropagation(); openInst(inst); }}/>
          ))}
          {insts.length > 3 && (
            <span className="muted-2" style={{fontSize:11}}>+{insts.length-3}</span>
          )}
        </div>

        {/* 共识强度 */}
        <div>
          <div className="muted" style={{fontSize:10,marginBottom:2}}>共识 (机构 · 平均胜率)</div>
          <div className="mono" style={{fontSize:12,fontWeight:500,color:'var(--c-ink-100)'}}>
            {pick.instCount} 家 · {Math.round(avgWin*100)}%
          </div>
        </div>

        {/* 同类历史 60d */}
        <div>
          <div className="muted" style={{fontSize:10,marginBottom:2}}>同类历史 60d</div>
          <div className="mono" style={{fontSize:12,fontWeight:500,color: pick.ret60_hist >= 0 ? 'var(--c-up)' : 'var(--c-down)'}}>
            {pick.ret60_hist >= 0 ? '+' : ''}{(pick.ret60_hist*100).toFixed(1)}%
          </div>
        </div>

        {/* 风险 */}
        <div>
          <RiskPill level={pick.risk}/>
        </div>

        {/* 展开 */}
        <div style={{textAlign:'right',color:'var(--c-ink-40)',fontSize:14,fontFamily:'monospace'}}>
          {expanded ? '−' : '+'}
        </div>
      </div>

      {/* 展开区 */}
      {expanded && <PickExpanded pick={pick} insts={insts} openInst={openInst}/>}
    </div>
  );
}

function RiskPill({level}) {
  const map = {
    low:  { fg:'var(--c-accent-fg)', bg:'var(--c-accent-bg)', text:'低风险' },
    med:  { fg:'#92400E', bg:'#FEF3C7', text:'中风险' },
    high: { fg:'#991B1B', bg:'#FEE2E2', text:'高风险' },
  };
  const t = map[level] || map.med;
  return <span style={{
    display:'inline-flex',padding:'2px 8px',borderRadius:10,
    background:t.bg,color:t.fg,fontSize:11,fontWeight:500,
  }}>{t.text}</span>;
}

/* -------- 展开:评分拆解 + 机构详情 + 反事实 --------- */
function PickExpanded({ pick, insts, openInst }) {
  return (
    <div style={{
      padding:'18px 22px',background:'var(--c-bg-2)',
      borderTop:'1px solid var(--c-line)',
      display:'grid',gridTemplateColumns:'1.2fr 1fr',gap:24,
    }}>
      {/* 左: 评分拆解 */}
      <div>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <h3 style={{fontSize:13,fontWeight:600}}>champion_v3 评分拆解</h3>
          <span className="muted-2" style={{fontSize:11}}>每项 = 该特征对最终分的归一化贡献</span>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {pick.features.map((f, i) => (
            <FeatureRow key={i} f={f}/>
          ))}
        </div>

        <div style={{
          marginTop:14,padding:'10px 12px',background:'var(--c-surface)',
          border:'1px solid var(--c-line)',borderRadius:6,
          display:'flex',justifyContent:'space-between',alignItems:'center',
        }}>
          <div style={{fontSize:12,color:'var(--c-ink-55)'}}>
            模型解读: <span style={{color:'var(--c-ink-85)'}}>
              {pick.tags.join(' · ')}
            </span>
          </div>
          <button className="btn btn-sm">查看反事实</button>
        </div>
      </div>

      {/* 右: 买入机构详情 */}
      <div>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <h3 style={{fontSize:13,fontWeight:600}}>买入机构 · {insts.length} 家</h3>
          <span className="muted-2" style={{fontSize:11}}>近 30 日内同向操作</span>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {insts.map(inst => (
            <InstSubrow key={inst.id} inst={inst} onOpen={() => openInst(inst)}/>
          ))}
        </div>

        {/* 行动 */}
        <div style={{
          marginTop:14,display:'flex',gap:8,
        }}>
          <button className="btn">加入自选</button>
          <button className="btn">设置提醒</button>
          <button className="btn btn-primary" style={{marginLeft:'auto'}}>跟随建仓 →</button>
        </div>
      </div>
    </div>
  );
}

function FeatureRow({ f }) {
  const positive = f.v >= 0;
  const max = 50;
  const w = Math.min(100, Math.abs(f.v) / max * 100);
  return (
    <div style={{display:'grid',gridTemplateColumns:'180px 1fr 50px',gap:10,alignItems:'center',fontSize:12}}>
      <div style={{color:'var(--c-ink-70)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{f.k}</div>
      <div style={{position:'relative',height:14,background:'var(--c-bg)',borderRadius:2}}>
        <div style={{position:'absolute',left:'50%',top:0,bottom:0,width:1,background:'var(--c-line-2)'}}/>
        <div style={{
          position:'absolute',top:1,bottom:1,
          left: positive ? '50%' : `${50 - w/2}%`,
          width: `${w/2}%`,
          background: positive ? 'var(--c-accent)' : 'var(--c-bad)',
          borderRadius:1,
        }}/>
      </div>
      <div className="num mono" style={{
        fontSize:12,fontWeight:600,
        color: positive ? 'var(--c-accent-fg)' : 'var(--c-bad)',
      }}>
        {positive ? '+' : ''}{f.v}
      </div>
    </div>
  );
}

function InstSubrow({ inst, onOpen }) {
  const winColor = inst.win >= 0.6 ? 'var(--c-accent)' : inst.win >= 0.5 ? 'var(--c-warn)' : 'var(--c-bad)';
  return (
    <div onClick={onOpen} style={{
      padding:'10px 12px',background:'var(--c-surface)',
      border:'1px solid var(--c-line)',borderRadius:6,
      display:'grid',gridTemplateColumns:'1fr 80px 80px 60px 14px',gap:10,
      alignItems:'center',cursor:'pointer',fontSize:12,
    }} onMouseEnter={e => e.currentTarget.style.borderColor='var(--c-line-2)'}
       onMouseLeave={e => e.currentTarget.style.borderColor='var(--c-line)'}>
      <div>
        <div style={{display:'flex',alignItems:'center',gap:8}}>
          <span style={{fontWeight:500}}>{inst.alias}</span>
          <window.CMV2.TypePill type={inst.type}/>
        </div>
        <div className="muted-2" style={{fontSize:10.5,marginTop:1}}>{inst.name} · {inst.events} 个历史事件</div>
      </div>
      <div>
        <div className="muted" style={{fontSize:10}}>胜率</div>
        <div className="mono" style={{fontSize:13,fontWeight:600,color:winColor}}>{Math.round(inst.win*100)}%</div>
      </div>
      <div>
        <div className="muted" style={{fontSize:10}}>平均 60d</div>
        <div className="mono" style={{fontSize:13,fontWeight:600,color:inst.avg60>=0?'var(--c-up)':'var(--c-down)'}}>
          {inst.avg60>=0?'+':''}{(inst.avg60*100).toFixed(1)}%
        </div>
      </div>
      <window.CMV2.MiniSpark data={inst.recent} width={60} height={20}/>
      <span style={{color:'var(--c-ink-40)'}}>›</span>
    </div>
  );
}

window.TodayPicks = TodayPicks;
window.HealthBar  = HealthBar;
