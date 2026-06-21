/* Chunky Monkey v3 — 所有页面 */
const { useState: useStateV3, useMemo: useMemoV3 } = React;

const HOLDING_PERIODS = [
  { key:'10d',  label:'10d',  field:'expected_gain_10d',  inst_field:'buy_avg_gain_10d' },
  { key:'30d',  label:'30d',  field:'expected_gain_30d',  inst_field:'buy_avg_gain_30d' },
  { key:'60d',  label:'60d',  field:'expected_gain_60d',  inst_field:'buy_avg_gain_60d' },
  { key:'90d',  label:'90d',  field:'expected_gain_90d',  inst_field:'buy_avg_gain_90d' },
  { key:'120d', label:'120d', field:null,                 inst_field:'buy_avg_gain_120d' }, // picks 没有 120d 预期, 只在机构维度展示
];

/* ============================================================
   Page 1 · 今日推荐
   ============================================================ */
function PageTodayPicks({ openInst, openStock }) {
  const { PICKS, MODELS, getInst, MiniSpark, InstChip, ApiTag, fmtPct } = window.CMV3;
  const [expanded, setExpanded] = useStateV3(PICKS[0].stock_code);
  const [sort, setSort]         = useStateV3('score');
  const [hp, setHp]             = useStateV3('60d');
  const hpDef = HOLDING_PERIODS.find(p => p.key === hp);

  const picksSorted = useMemoV3(() => {
    const arr = [...PICKS];
    if (sort === 'score')   arr.sort((a,b) => b.score - a.score);
    if (sort === 'consensus') arr.sort((a,b) => b.supporters.length - a.supporters.length);
    if (sort === 'expected') arr.sort((a,b) => (b[hpDef.field]||0) - (a[hpDef.field]||0));
    return arr;
  }, [sort, hp]);

  return (
    <div>
      <HealthBar/>
      <PageHead
        crumbs={[['研究'],['今日推荐', true]]}
        title={`今日推荐 · ${PICKS.length} 只`}
        sub={`champion: ${MODELS.champion.model_id} (${MODELS.champion.model_version}) · 基于 ${INST_COUNT()} 家跟踪机构 · 滚动 30d 胜率 ${Math.round(MODELS.champion.rolling30_winrate*100)}%`}
        api={<ApiTag path="/api/recommendation/daily-topk?rec_date=2026-05-06&top_n=7"/>}
        actions={[['导出 CSV'],['订阅推送'],['回测当前榜单','primary']]}
      />

      {/* 工具条 */}
      <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:12,flexWrap:'wrap'}}>
        <span className="muted" style={{fontSize:12}}>排序</span>
        <Segmented value={sort} onChange={setSort} options={[['score','综合分'],['consensus','共识 (机构数)'],['expected','预期收益']]}/>
        <span className="muted" style={{fontSize:12,marginLeft:14}}>持有期</span>
        <Segmented value={hp} onChange={setHp} options={HOLDING_PERIODS.filter(p=>p.field).map(p=>[p.key,p.label])}/>
        <div style={{flex:1}}/>
        <span className="muted-2 mono" style={{fontSize:11}}>
          特征窗口 30d · 持有期 {hpDef.label} · 最小样本 8 次
        </span>
      </div>

      {/* 排行 */}
      <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
        {picksSorted.map((p, i) => (
          <PickRow key={p.stock_code}
            pick={p} idx={i+1}
            hpField={hpDef.field}
            hpLabel={hpDef.label}
            expanded={expanded === p.stock_code}
            onToggle={() => setExpanded(expanded === p.stock_code ? null : p.stock_code)}
            openInst={openInst}
            openStock={openStock}
            isLast={i === picksSorted.length-1}/>
        ))}
      </div>
    </div>
  );
}

function INST_COUNT() { return window.CMV3.INST_DB.length; }

function HealthBar() {
  const { TABLES, PIPELINE, MODELS, StatusDot, ApiTag } = window.CMV3;
  // 顶部健康概要 — 只看 4 个 status
  const dataStatus = TABLES.some(t => t.freshness_status==='bad') ? 'bad' : TABLES.some(t => t.freshness_status==='warn') ? 'warn' : 'ok';
  const pipeStatus = PIPELINE.some(p => p.status==='bad') ? 'bad' : PIPELINE.some(p => p.status==='warn') ? 'warn' : 'ok';
  const _thOk = window.CMV3.CONFIG?.thresholds?.model_health_winrate_ok;  // 阈值出口唯一 = /api/v3/config
  const modelStatus = _thOk == null ? 'warn' : (MODELS.champion.rolling30_winrate >= _thOk ? 'ok' : 'warn');
  const items = [
    { k:'数据', s:dataStatus,  detail:`${TABLES.length} 张表 · ${TABLES.filter(t=>t.freshness_status!=='ok').length} 待处理` },
    { k:'链路', s:pipeStatus,  detail:`${PIPELINE.length} 阶段 · ${PIPELINE.filter(p=>p.status==='warn').length} warn` },
    { k:'模型', s:modelStatus, detail:`champion ${MODELS.champion.model_id} · 滚动 30d ${Math.round(MODELS.champion.rolling30_winrate*100)}%` },
    { k:'信号', s:'ok',        detail:'7 条已下发 04:48' },
  ];
  const overall = items.some(i => i.s==='bad') ? 'bad' : items.some(i => i.s==='warn') ? 'warn' : 'ok';
  const overallText = overall==='ok' ? '系统健康' : overall==='warn' ? '存在告警 · 信号可用需注意' : '存在故障 · 暂停跟随';
  const overallTone = overall==='ok' ? 'var(--c-accent)' : overall==='warn' ? 'var(--c-warn)' : 'var(--c-bad)';
  return (
    <div style={{
      background:'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:8,
      padding:'10px 14px',display:'flex',alignItems:'center',gap:14,marginBottom:14,
      borderLeft:`3px solid ${overallTone}`,
    }}>
      <div style={{minWidth:140}}>
        <div style={{fontSize:10,color:'var(--c-ink-55)',textTransform:'uppercase',letterSpacing:'0.04em'}}>SYSTEM</div>
        <div style={{fontSize:13,fontWeight:600,color:overallTone}}>{overallText}</div>
      </div>
      <div style={{width:1,alignSelf:'stretch',background:'var(--c-line)'}}/>
      {items.map((it,i) => (
        <div key={i} style={{flex:1,display:'flex',alignItems:'center',gap:10,minWidth:0}}>
          <StatusDot status={it.s}/>
          <div style={{minWidth:0,flex:1}}>
            <div style={{fontSize:11,color:'var(--c-ink-55)'}}>{it.k}</div>
            <div style={{fontSize:12,fontWeight:500,color:'var(--c-ink-85)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{it.detail}</div>
          </div>
        </div>
      ))}
      <ApiTag path="/api/data_health/snapshot"/>
    </div>
  );
}

function PageHead({crumbs, title, sub, api, actions=[]}) {
  return (
    <div className="page-head">
      <div>
        <div className="crumbs">
          {crumbs.map((c,i) => (
            <React.Fragment key={i}>
              {i>0 && <span className="sep">›</span>}
              <span className={c[1]?'here':''}>{c[0]}</span>
            </React.Fragment>
          ))}
        </div>
        <div style={{display:'flex',alignItems:'baseline',gap:10,flexWrap:'wrap'}}>
          <h1>{title}</h1>
          {api}
        </div>
        {sub && <p>{sub}</p>}
      </div>
      {actions.length > 0 && (
        <div className="page-actions">
          {actions.map(([l, kind], i) => (
            <button key={i} className={`btn ${kind==='primary'?'btn-primary':''}`}>{l}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function Segmented({value, onChange, options}) {
  return (
    <div style={{display:'inline-flex',background:'var(--c-bg-2)',borderRadius:6,padding:2,gap:1}}>
      {options.map(([v,l]) => (
        <button key={v} onClick={() => onChange(v)} style={{
          height:26,padding:'0 12px',borderRadius:4,fontSize:12,
          background: value===v?'var(--c-surface)':'transparent',
          color: value===v?'var(--c-ink-100)':'var(--c-ink-55)',
          fontWeight: value===v?600:500,
          boxShadow: value===v?'var(--sh-1)':'none',
        }}>{l}</button>
      ))}
    </div>
  );
}

function PickRow({ pick, idx, hpField, hpLabel, expanded, onToggle, openInst, openStock, isLast }) {
  const { getInst, InstChip, fmtPct } = window.CMV3;
  const insts = pick.supporters.map(getInst);
  const avgWin = insts.reduce((s,i) => s + i.win_rate, 0) / insts.length;
  const expectedGain = pick[hpField];

  return (
    <div style={{borderBottom: isLast ? '0' : '1px solid var(--c-line)'}}>
      <div onClick={onToggle} style={{
        display:'grid',
        gridTemplateColumns:'40px minmax(170px,1.1fr) 88px minmax(240px,1.4fr) 92px 92px 70px 28px',
        gap:12,padding:'14px 16px',alignItems:'center',cursor:'pointer',
        background: expanded ? 'var(--c-bg-2)' : 'var(--c-surface)',
      }}>
        <div style={{
          width:32,height:32,borderRadius:8,
          background: idx <= 3 ? 'var(--c-ink-100)' : 'var(--c-bg-2)',
          color: idx <= 3 ? '#fff' : 'var(--c-ink-55)',
          display:'inline-flex',alignItems:'center',justifyContent:'center',
          fontSize:14,fontWeight:600,fontFamily:'var(--f-mono)',
        }}>{idx}</div>

        <div style={{minWidth:0}}
             onClick={(e) => { e.stopPropagation(); openStock && openStock(pick); }}>
          <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:2}}>
            <span style={{fontSize:15,fontWeight:600,color:'var(--c-ink-100)'}}>
              <window.CMV3.UI.StockNameLink code={pick.stock_code} name={pick.stock_name}/>
            </span>
            <span className="mono muted-2" style={{fontSize:11}}>
              <window.CMV3.UI.StockNameLink code={pick.stock_code} name={pick.stock_code}/>
            </span>
          </div>
          <div className="muted" style={{fontSize:11,display:'flex',gap:6}}>
            <span>{pick.sector}</span><span>·</span>
            <span className="mono">¥{pick.price}</span>
            <span className="mono" style={{color: pick.chg_pct >= 0 ? 'var(--c-up)' : 'var(--c-down)'}}>
              {fmtPct(pick.chg_pct)}
            </span>
          </div>
        </div>

        <div>
          <div style={{display:'flex',alignItems:'baseline',gap:4}}>
            <span className="mono" style={{fontSize:22,fontWeight:600,color:'var(--c-ink-100)',letterSpacing:'-0.02em'}}>{pick.score.toFixed(1)}</span>
            <span className="muted-2" style={{fontSize:10}}>/100</span>
          </div>
          <div style={{height:3,background:'var(--c-bg-2)',borderRadius:2,marginTop:2}}>
            <div style={{width:`${pick.score}%`,height:'100%',background:'var(--c-accent)',borderRadius:2}}/>
          </div>
        </div>

        <div style={{display:'flex',flexWrap:'wrap',gap:5,alignItems:'center'}}>
          {insts.slice(0, 3).map(inst => (
            <InstChip key={inst.inst_uid} inst={inst} size="sm"
              onClick={(e) => { e.stopPropagation(); openInst(inst); }}/>
          ))}
          {insts.length > 3 && <span className="muted-2" style={{fontSize:11}}>+{insts.length-3}</span>}
        </div>

        <div>
          <div className="muted" style={{fontSize:10,marginBottom:2}}>共识 · 加权胜率</div>
          <div className="mono" style={{fontSize:12,fontWeight:500,color:'var(--c-ink-100)'}}>
            {pick.supporters.length} 家 · {Math.round(avgWin*100)}%
          </div>
        </div>

        <div>
          <div className="muted" style={{fontSize:10,marginBottom:2}}>预期 {hpLabel}</div>
          <div className="mono" style={{fontSize:12,fontWeight:600,color: expectedGain >= 0 ? 'var(--c-up)' : 'var(--c-down)'}}>
            {fmtPct(expectedGain)}
          </div>
        </div>

        <div><RiskPill level={pick.risk}/></div>

        <div style={{textAlign:'right',color:'var(--c-ink-40)',fontSize:14,fontFamily:'monospace'}}>
          {expanded ? '−' : '+'}
        </div>
      </div>

      {expanded && <PickExpanded pick={pick} insts={insts} openInst={openInst} openStock={openStock}/>}
    </div>
  );
}

function RiskPill({level}) {
  const map = {
    low:  { fg:'var(--c-accent-fg)', bg:'var(--c-accent-bg)', text:'低' },
    med:  { fg:'#92400E', bg:'#FEF3C7', text:'中' },
    high: { fg:'#991B1B', bg:'#FEE2E2', text:'高' },
  };
  const t = map[level] || map.med;
  return <span style={{display:'inline-flex',padding:'2px 8px',borderRadius:10,background:t.bg,color:t.fg,fontSize:11,fontWeight:500}}>{t.text}风险</span>;
}

function PickExpanded({ pick, insts, openInst, openStock }) {
  const { fmtPct, ApiTag } = window.CMV3;
  return (
    <div style={{padding:'18px 22px',background:'var(--c-bg-2)',borderTop:'1px solid var(--c-line)',display:'grid',gridTemplateColumns:'1.2fr 1fr',gap:24}}>
      <div>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <h3 style={{fontSize:13,fontWeight:600}}>champion_v3 评分拆解 · factor_breakdown</h3>
          <ApiTag path={`/api/recommendation/daily-topk/${pick.stock_code}/explain`}/>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {pick.factor_breakdown.map((f, i) => <FeatureRow key={i} f={f}/>)}
        </div>

        <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:8,marginTop:14}}>
          {[
            ['10d', pick.expected_gain_10d],
            ['30d', pick.expected_gain_30d],
            ['60d', pick.expected_gain_60d],
            ['90d', pick.expected_gain_90d],
          ].map(([l,v]) => (
            <div key={l} style={{padding:'8px 10px',background:'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:6}}>
              <div className="muted" style={{fontSize:10}}>预期 {l}</div>
              <div className="mono" style={{fontSize:14,fontWeight:600,color:v>=0?'var(--c-up)':'var(--c-down)'}}>{fmtPct(v)}</div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <h3 style={{fontSize:13,fontWeight:600}}>支持机构 · {insts.length} 家</h3>
          <ApiTag path={`/api/recommendation/daily-topk/${pick.stock_code}/supporters`}/>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {insts.map(inst => <InstSubrow key={inst.inst_uid} inst={inst} onOpen={() => openInst(inst)}/>)}
        </div>
        <div style={{marginTop:14,display:'flex',gap:8}}>
          <button className="btn" onClick={() => openStock(pick)}>查看股票研究 →</button>
          <button className="btn">加入自选</button>
          <button className="btn btn-primary" style={{marginLeft:'auto'}}>跟随建仓</button>
        </div>
      </div>
    </div>
  );
}

function FeatureRow({ f }) {
  const [name, value, note] = f;
  const positive = value >= 0;
  const max = 50;
  const w = Math.min(100, Math.abs(value) / max * 100);
  return (
    <div style={{display:'grid',gridTemplateColumns:'200px 1fr 50px',gap:10,alignItems:'center',fontSize:12}}>
      <div style={{minWidth:0}}>
        <div className="mono" style={{fontSize:11,color:'var(--c-ink-100)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{name}</div>
        <div className="muted-2" style={{fontSize:10,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{note}</div>
      </div>
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
      <div className="num mono" style={{fontSize:12,fontWeight:600,color: positive ? 'var(--c-accent-fg)' : 'var(--c-bad)'}}>
        {positive ? '+' : ''}{value}
      </div>
    </div>
  );
}

function InstSubrow({ inst, onOpen }) {
  const { RolePill, MiniSpark } = window.CMV3;
  const _cfg = window.CMV3.CONFIG?.thresholds;  // config 拉不到 → 中性灰, 不许内置回退值 (第二真相源)
  const winColor = _cfg == null ? 'var(--ink-2)'
    : inst.win_rate >= _cfg.inst_winrate_green ? 'var(--c-accent)'
    : inst.win_rate >= _cfg.inst_winrate_yellow ? 'var(--c-warn)' : 'var(--c-bad)';
  return (
    <div onClick={onOpen} style={{
      padding:'10px 12px',background:'var(--c-surface)',
      border:'1px solid var(--c-line)',borderRadius:6,
      display:'grid',gridTemplateColumns:'1fr 70px 80px 60px 14px',gap:10,alignItems:'center',cursor:'pointer',fontSize:12,
    }}>
      <div style={{minWidth:0}}>
        <div style={{display:'flex',alignItems:'center',gap:8}}>
          <span style={{fontWeight:500}}>{inst.alias}</span>
          <RolePill role={inst.role}/>
        </div>
        <div className="muted-2 mono" style={{fontSize:10,marginTop:1,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{inst.inst_uid} · {inst.buy_event_count} 事件</div>
      </div>
      <div>
        <div className="muted" style={{fontSize:10}}>胜率</div>
        <div className="mono" style={{fontSize:13,fontWeight:600,color:winColor}}>{Math.round(inst.win_rate*100)}%</div>
      </div>
      <div>
        <div className="muted" style={{fontSize:10}}>avg 60d</div>
        <div className="mono" style={{fontSize:13,fontWeight:600,color:inst.buy_avg_gain_60d>=0?'var(--c-up)':'var(--c-down)'}}>
          {inst.buy_avg_gain_60d>=0?'+':''}{(inst.buy_avg_gain_60d*100).toFixed(1)}%
        </div>
      </div>
      <MiniSpark data={inst.recent12} width={60} height={20}/>
      <span style={{color:'var(--c-ink-40)'}}>›</span>
    </div>
  );
}

window.PageTodayPicks = PageTodayPicks;
window.PageHead = PageHead;
window.Segmented = Segmented;
window.HOLDING_PERIODS = HOLDING_PERIODS;
