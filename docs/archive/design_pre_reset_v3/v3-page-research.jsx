/* Chunky Monkey v3 — 股票研究 + 机构研究 + ETF */

const { useState: useStateR, useMemo: useMemoR } = React;

/* ============================================================
   Page 2 · 股票研究 (NEW)
   字段对齐: dim_stock + fact_holders_event + mart_holder_position
   ============================================================ */
function PageStockResearch({ openInst, currentStock, setCurrentStock }) {
  const { PICKS, INST_DB, getInst, ApiTag, RolePill, MiniSpark, fmtPct } = window.CMV3;
  const stock = currentStock || PICKS[0];
  const supporters = (stock.supporters || []).map(getInst);
  const [tab, setTab] = useStateR('overview');

  return (
    <div>
      <window.PageHead
        crumbs={[['研究'],['股票研究', true]]}
        title="股票研究"
        sub="多维度透视一只股票 — 机构持仓 / 评分明细 / 历史事件 / 基本面"
        api={<ApiTag path={`/api/stock/${stock.stock_code}/research`}/>}
      />

      {/* 搜索 + 切换 */}
      <div style={{display:'flex',gap:10,marginBottom:14,alignItems:'center',flexWrap:'wrap'}}>
        <div className="app-search" style={{width:280}}>
          <span style={{color:'var(--c-ink-40)'}}>⌕</span>
          <input placeholder="代码 / 名称"/>
        </div>
        <span className="muted" style={{fontSize:12}}>快速切换</span>
        {PICKS.slice(0,7).map(p => (
          <button key={p.stock_code} onClick={() => setCurrentStock(p)} style={{
            padding:'4px 10px',borderRadius:14,fontSize:11,
            background: stock.stock_code === p.stock_code ? 'var(--c-ink-100)' : 'var(--c-bg-2)',
            color: stock.stock_code === p.stock_code ? '#fff' : 'var(--c-ink-70)',
            fontWeight: 500,
          }}>{p.stock_name}</button>
        ))}
      </div>

      {/* 股票头 */}
      <div className="cm-card" style={{padding:'16px 20px',marginBottom:14}}>
        <div style={{display:'flex',alignItems:'flex-end',gap:14}}>
          <div>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
              <span className="tag" style={{background:'var(--c-accent-bg)',color:'var(--c-accent-fg)'}}>沪</span>
              <span className="mono muted-2" style={{fontSize:13}}>
                <window.CMV3.UI.StockNameLink code={stock.stock_code} name={stock.stock_code}/>
              </span>
              <span className="pill pill-accent">champion · rank #{stock.rank}</span>
              <span className="pill"><span className="pill-dot"/>已加自选</span>
            </div>
            <div style={{display:'flex',alignItems:'baseline',gap:14}}>
              <h1 style={{fontSize:24,fontWeight:600,letterSpacing:'-0.01em'}}>
                <window.CMV3.UI.StockNameLink code={stock.stock_code} name={stock.stock_name}/>
              </h1>
              <span className="muted" style={{fontSize:12}}>{stock.sector} · 上证 50 · 沪深 300</span>
            </div>
          </div>
          <div style={{flex:1}}/>
          <div style={{textAlign:'right'}}>
            <div className="mono" style={{fontSize:28,fontWeight:600,color:stock.chg_pct>=0?'var(--c-up)':'var(--c-down)',letterSpacing:'-0.02em'}}>¥{stock.price}</div>
            <div className="mono" style={{fontSize:12,color:stock.chg_pct>=0?'var(--c-up)':'var(--c-down)'}}>{fmtPct(stock.chg_pct)}</div>
            <div className="muted" style={{fontSize:11,marginTop:2}}>15:00 · 收盘</div>
          </div>
        </div>

        {/* 模型预期收益 — 关键字段 */}
        <div style={{marginTop:14,padding:'12px 14px',background:'var(--c-bg-2)',borderRadius:8,display:'flex',alignItems:'center',gap:20}}>
          <div style={{flexShrink:0}}>
            <div className="muted" style={{fontSize:10,textTransform:'uppercase',letterSpacing:'0.04em'}}>champion_v3 预期收益</div>
            <div style={{display:'flex',alignItems:'baseline',gap:6,marginTop:2}}>
              <span className="mono" style={{fontSize:24,fontWeight:600,color:'var(--c-ink-100)'}}>{stock.score.toFixed(1)}</span>
              <span className="muted-2" style={{fontSize:11}}>/100</span>
            </div>
          </div>
          <div style={{flex:1,display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:14}}>
            {[
              ['expected_gain_10d', '10d', stock.expected_gain_10d],
              ['expected_gain_30d', '30d', stock.expected_gain_30d],
              ['expected_gain_60d', '60d', stock.expected_gain_60d],
              ['expected_gain_90d', '90d', stock.expected_gain_90d],
            ].map(([k,l,v]) => (
              <div key={k}>
                <div className="muted-2 mono" style={{fontSize:10}}>{l}</div>
                <div className="mono" style={{fontSize:18,fontWeight:600,color:v>=0?'var(--c-up)':'var(--c-down)'}}>{fmtPct(v)}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 标签 */}
      <div style={{borderBottom:'1px solid var(--c-line)',display:'flex',gap:0,marginBottom:14}}>
        {[
          ['overview','概览'],
          ['holders','机构持仓'],
          ['score','评分明细'],
          ['events','事件流'],
          ['fundamentals','基本面'],
        ].map(([id,l]) => (
          <button key={id} onClick={() => setTab(id)} style={{
            padding:'8px 14px',fontSize:13,
            color: tab===id?'var(--c-ink-100)':'var(--c-ink-55)',
            fontWeight: tab===id?600:500,
            borderBottom: tab===id?'2px solid var(--c-ink-100)':'2px solid transparent',
            marginBottom:-1,
          }}>{l}</button>
        ))}
      </div>

      {tab === 'overview' && <StockOverview stock={stock} supporters={supporters} openInst={openInst}/>}
      {tab === 'holders'  && <StockHolders stock={stock} supporters={supporters} openInst={openInst}/>}
      {tab === 'score'    && <StockScore stock={stock}/>}
      {tab === 'events'   && <StockEvents stock={stock}/>}
      {tab === 'fundamentals' && <StockFundamentals stock={stock}/>}

      <div style={{height:24}}/>
    </div>
  );
}

function StockOverview({ stock, supporters, openInst }) {
  const { ApiTag, RolePill, fmtPct } = window.CMV3;
  return (
    <div style={{display:'grid',gridTemplateColumns:'1.4fr 1fr',gap:14}}>
      <div className="cm-card" style={{padding:14}}>
        <div className="cm-section-h">
          <div>
            <h3>价格 + 机构事件叠加</h3>
            <span className="desc">120 日 K 线 · 红线 = buy 事件, 灰线 = 减持/大宗</span>
          </div>
          <ApiTag path={`/api/stock/${stock.stock_code}/kline?days=120&overlay=events`}/>
        </div>
        <KLineMock/>
      </div>

      <div className="cm-card" style={{padding:14}}>
        <div className="cm-section-h">
          <div>
            <h3>支持机构 · {supporters.length}</h3>
            <span className="desc">近 30 日内 buy 事件</span>
          </div>
          <ApiTag path={`/api/stock/${stock.stock_code}/supporters`}/>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {supporters.map(inst => (
            <div key={inst.inst_uid} onClick={() => openInst(inst)} style={{padding:'8px 10px',border:'1px solid var(--c-line)',borderRadius:6,fontSize:12,cursor:'pointer',display:'grid',gridTemplateColumns:'1fr 60px 60px',gap:10,alignItems:'center'}}>
              <div style={{minWidth:0}}>
                <div style={{display:'flex',gap:6,alignItems:'center'}}>
                  <span style={{fontWeight:500}}>{inst.alias}</span>
                  <RolePill role={inst.role}/>
                </div>
                <div className="muted-2 mono" style={{fontSize:10}}>{inst.buy_event_count} 事件 · trust {inst.trust_weight.toFixed(2)}</div>
              </div>
              <div>
                <div className="muted" style={{fontSize:10}}>胜率</div>
                <div className="mono" style={{fontSize:12,fontWeight:600}}>{Math.round(inst.win_rate*100)}%</div>
              </div>
              <div>
                <div className="muted" style={{fontSize:10}}>avg 60d</div>
                <div className="mono" style={{fontSize:12,fontWeight:600,color:inst.buy_avg_gain_60d>=0?'var(--c-up)':'var(--c-down)'}}>{fmtPct(inst.buy_avg_gain_60d)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StockHolders({ stock, supporters, openInst }) {
  const { ApiTag, RolePill } = window.CMV3;
  // 模拟 10 大股东 4 季度差分
  const rows = [
    ['IS_NORTH',       '北向',     '8 924 万', '8 932 万', '+8 万',     'flat'],
    ['IS_SHEHUI_105',  '社保',     '1 240 万', '1 322 万', '+82 万',    'up'],
    ['IS_SHEHUI_117',  '社保',     '892 万',  '892 万',   '0',         'flat'],
    ['IS_HUIJIN',      '国家队',   '—',      '420 万',   '建仓',      'up'],
    ['IS_JPM_QFII',    'QFII',     '188 万',  '224 万',   '+36 万',    'up'],
    ['IS_GLAN',        '基金',     '522 万',  '498 万',   '-24 万',    'down'],
    ['IS_LIFE_INS',    '保险',     '320 万',  '320 万',   '0',         'flat'],
  ];
  return (
    <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
      <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <div>
          <h3 style={{fontSize:13,fontWeight:600}}>十大股东 — 近 4 季差分</h3>
          <span className="desc">来自 fact_holders_event · 报告期之间的持仓变化</span>
        </div>
        <ApiTag path={`/api/stock/${stock.stock_code}/holders?periods=4`}/>
      </div>
      <table className="cm-table">
        <thead><tr>
          <th>机构 (inst_uid)</th>
          <th style={{width:88}}>类型</th>
          <th className="num" style={{width:100}}>2025Q4</th>
          <th className="num" style={{width:100}}>2026Q1</th>
          <th className="num" style={{width:90}}>变化</th>
          <th style={{width:80}}>方向</th>
        </tr></thead>
        <tbody>
          {rows.map((r,i) => (
            <tr key={i}>
              <td><span className="mono" style={{fontSize:11.5,fontWeight:500}}>{r[0]}</span></td>
              <td><RolePill role={r[1]}/></td>
              <td className="num mono">{r[2]}</td>
              <td className="num mono">{r[3]}</td>
              <td className={`num mono ${r[5]==='up'?'up':r[5]==='down'?'down':'muted'}`} style={{fontWeight:500}}>{r[4]}</td>
              <td>
                {r[5]==='up'? <span className="pill pill-up"><span className="pill-dot"/>买入</span>
                 : r[5]==='down'? <span className="pill pill-down"><span className="pill-dot"/>卖出</span>
                 : <span className="pill"><span className="pill-dot"/>不变</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StockScore({ stock }) {
  const { ApiTag, fmtPct } = window.CMV3;
  return (
    <div className="cm-card" style={{padding:14}}>
      <div className="cm-section-h">
        <div>
          <h3>champion_v3 评分明细 (因子分解)</h3>
          <span className="desc">每项 = 该特征对最终分的归一化贡献</span>
        </div>
        <ApiTag path={`/api/recommendation/daily-topk/${stock.stock_code}/explain`}/>
      </div>
      <div style={{display:'flex',flexDirection:'column',gap:6,marginTop:10}}>
        {stock.factor_breakdown.map((f, i) => <FeatureRowS key={i} f={f}/>)}
      </div>
      <div style={{marginTop:14,padding:'10px 12px',background:'var(--c-bg-2)',borderRadius:6,fontSize:12,color:'var(--c-ink-70)'}}>
        <strong style={{color:'var(--c-ink-100)'}}>反事实分析:</strong> 如果剔除某家支持机构 (例如汇金),
        机构共识贡献从 +38 降至 +24, 综合分预计 92.3 → 78.5, 排名跌到 #3。
      </div>
    </div>
  );
}

function FeatureRowS({ f }) {
  const [name, value, note] = f;
  const positive = value >= 0;
  const max = 50;
  const w = Math.min(100, Math.abs(value) / max * 100);
  return (
    <div style={{display:'grid',gridTemplateColumns:'220px 1fr 60px',gap:12,alignItems:'center',fontSize:12}}>
      <div>
        <div className="mono" style={{fontSize:11.5,color:'var(--c-ink-100)'}}>{name}</div>
        <div className="muted-2" style={{fontSize:10}}>{note}</div>
      </div>
      <div style={{position:'relative',height:16,background:'var(--c-bg-2)',borderRadius:2}}>
        <div style={{position:'absolute',left:'50%',top:0,bottom:0,width:1,background:'var(--c-line-2)'}}/>
        <div style={{position:'absolute',top:1,bottom:1,left: positive ? '50%' : `${50 - w/2}%`,width: `${w/2}%`,background: positive ? 'var(--c-accent)' : 'var(--c-bad)',borderRadius:1}}/>
      </div>
      <div className="num mono" style={{fontSize:13,fontWeight:600,color: positive ? 'var(--c-accent-fg)' : 'var(--c-bad)'}}>
        {positive ? '+' : ''}{value}
      </div>
    </div>
  );
}

function StockEvents({ stock }) {
  const { ApiTag } = window.CMV3;
  const events = [
    ['2026-05-05','北向',         'IS_NORTH',       'buy',  '+82 万股',  '已结'],
    ['2026-05-04','社保 105',     'IS_SHEHUI_105',  'buy',  '+12 万股',  '未到期'],
    ['2026-05-03','大宗 (折价)',  '—',              'block','50 万股 · 折 2.4%', '—'],
    ['2026-04-28','汇金',         'IS_HUIJIN',      'buy',  '+420 万股', '未到期'],
    ['2026-04-22','摩根 QFII',   'IS_JPM_QFII',    'buy',  '+36 万股',  '已结'],
    ['2026-04-15','高管',         '—',              'exec', '减持 8 万股', '—'],
  ];
  return (
    <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
      <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <div>
          <h3 style={{fontSize:13,fontWeight:600}}>近期事件流</h3>
          <span className="desc">fact_holders_event + fact_block_trade + fact_exec_change</span>
        </div>
        <ApiTag path={`/api/stock/${stock.stock_code}/events?days=30`}/>
      </div>
      <table className="cm-table">
        <thead><tr>
          <th style={{width:90}}>日期</th>
          <th>主体</th>
          <th>inst_uid</th>
          <th style={{width:80}}>类型</th>
          <th className="num" style={{width:140}}>变动</th>
          <th style={{width:80}}>跟随状态</th>
        </tr></thead>
        <tbody>
          {events.map((r,i) => (
            <tr key={i}>
              <td className="mono muted" style={{fontSize:11}}>{r[0]}</td>
              <td style={{fontWeight:500}}>{r[1]}</td>
              <td className="mono muted-2" style={{fontSize:10.5}}>{r[2]}</td>
              <td>
                <span className="pill" style={{
                  background: r[3]==='buy'?'var(--c-accent-bg)':r[3]==='block'?'#FEF3C7':'#FEE2E2',
                  color: r[3]==='buy'?'var(--c-accent-fg)':r[3]==='block'?'#92400E':'#991B1B',
                }}>{r[3]}</span>
              </td>
              <td className="num mono" style={{fontWeight:500}}>{r[4]}</td>
              <td>{r[5]==='未到期'?<span className="pill pill-warn"><span className="pill-dot"/>未到期</span>:<span className="muted-2" style={{fontSize:11}}>{r[5]}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StockFundamentals({ stock }) {
  const { ApiTag } = window.CMV3;
  return (
    <div className="cm-card" style={{padding:14}}>
      <div className="cm-section-h">
        <div>
          <h3>基本面快照</h3>
          <span className="desc">来自 dim_stock + 财务披露</span>
        </div>
        <ApiTag path={`/api/stock/${stock.stock_code}/fundamentals`}/>
      </div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(6,1fr)',gap:0,marginTop:12,border:'1px solid var(--c-line)',borderRadius:8,background:'var(--c-surface)',overflow:'hidden'}}>
        {[
          ['市盈率 (TTM)','25.4'],
          ['市净率',     '8.2'],
          ['ROE',        '32.1%'],
          ['毛利率',     '92.0%'],
          ['净利率',     '48.4%'],
          ['股息率',     '1.6%'],
          ['市值',       '2.11 万亿'],
          ['流通市值',   '1.92 万亿'],
          ['52w 区间',   '1420 ~ 1820'],
          ['β',          '0.84'],
          ['每股收益',   '52.8'],
          ['每股净资产', '205.4'],
        ].map(([k,v],i) => (
          <div key={i} style={{padding:'10px 12px',borderRight:(i%6<5)?'1px solid var(--c-line)':'0',borderTop:i>=6?'1px solid var(--c-line)':'0'}}>
            <div className="muted" style={{fontSize:10,marginBottom:2}}>{k}</div>
            <div className="mono" style={{fontSize:14,fontWeight:500,color:'var(--c-ink-100)'}}>{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function KLineMock() {
  const days = 90;
  const arr = useMemoR(() => {
    let p = 1500; const out = [];
    for (let i=0;i<days;i++) {
      const o = p + (Math.random()-0.5)*8;
      const c = o + (Math.random()-0.45)*22;
      const h = Math.max(o,c) + Math.random()*8;
      const l = Math.min(o,c) - Math.random()*8;
      out.push({o,c,h,l}); p = c;
    }
    return out;
  }, []);
  const events = [12,28,42,55,68,75,82];
  const cuts   = [22,49,71];
  const all = arr.flatMap(d=>[d.h,d.l]);
  const max = Math.max(...all), min = Math.min(...all);
  const W = 720, H = 240;
  const xw = W / days;
  const sy = v => H - ((v - min) / (max - min)) * (H - 20) - 10;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:240,display:'block'}}>
      {[0,1,2,3,4].map(i => <line key={i} x1={0} x2={W} y1={(H/4)*i+5} y2={(H/4)*i+5} stroke="var(--c-line)" strokeWidth="0.5" strokeDasharray="2 3"/>)}
      {arr.map((d,i) => {
        const x = i*xw + xw/2;
        const up = d.c >= d.o;
        const color = up ? 'var(--c-up)' : 'var(--c-down)';
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={sy(d.h)} y2={sy(d.l)} stroke={color} strokeWidth="1"/>
            <rect x={x - xw*0.32} y={sy(Math.max(d.o,d.c))} width={xw*0.64} height={Math.max(1, Math.abs(sy(d.o)-sy(d.c)))} fill={color}/>
          </g>
        );
      })}
      {events.map(i => { const x = i*xw + xw/2; return (<g key={'ev'+i}><line x1={x} x2={x} y1={H-4} y2={H-12} stroke="var(--c-up)" strokeWidth="1.5"/><circle cx={x} cy={H-14} r="3" fill="var(--c-up)"/></g>); })}
      {cuts.map(i => { const x = i*xw + xw/2; return (<g key={'ct'+i}><line x1={x} x2={x} y1={4} y2={12} stroke="var(--c-ink-55)" strokeWidth="1.5"/><circle cx={x} cy={14} r="3" fill="var(--c-ink-55)"/></g>); })}
    </svg>
  );
}

/* ============================================================
   Page 3 · 机构研究  (NEW dense layout - 修空白)
   ============================================================ */
function PageInstResearch({ openInst }) {
  const { INST_DB, RolePill, MiniSpark, ApiTag, fmtPct } = window.CMV3;
  const [role, setRole] = useStateR('all');
  const [minEvents, setMinEvents] = useStateR(8);
  const [hp, setHp] = useStateR('60d');
  const hpDef = window.HOLDING_PERIODS.find(p => p.key === hp);
  const roles = ['all', ...new Set(INST_DB.map(i => i.role))];

  const filtered = INST_DB
    .filter(i => (role === 'all' || i.role === role) && i.buy_event_count >= minEvents)
    .sort((a,b) => (b.win_rate * b.trust_weight) - (a.win_rate * a.trust_weight));

  return (
    <div>
      <window.PageHead
        crumbs={[['研究'],['机构研究', true]]}
        title={`机构研究 · ${filtered.length} / ${INST_DB.length}`}
        sub={`排序 = win_rate × trust_weight · 样本不足 ${minEvents} 次自动过滤 · 点击进入完整 track record`}
        api={<ApiTag path="/api/institution/list?role=&min_events="/>}
        actions={[['导入机构'],['建立机构组合','primary']]}
      />

      {/* 工具条 */}
      <div className="cm-card" style={{padding:'10px 14px',marginBottom:14,display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
        <span className="muted" style={{fontSize:12}}>类型</span>
        <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
          {roles.map(t => (
            <button key={t} onClick={() => setRole(t)} style={{
              padding:'4px 10px',borderRadius:14,fontSize:11,
              background: role===t?'var(--c-ink-100)':'var(--c-bg-2)',
              color: role===t?'#fff':'var(--c-ink-70)',
              fontWeight: 500,
            }}>{t==='all'?'全部':t}</button>
          ))}
        </div>
        <div style={{width:1,height:18,background:'var(--c-line)'}}/>
        <span className="muted" style={{fontSize:12}}>持有期</span>
        <window.Segmented value={hp} onChange={setHp} options={window.HOLDING_PERIODS.map(p=>[p.key,p.label])}/>
        <div style={{flex:1}}/>
        <span className="muted" style={{fontSize:12}}>最少事件</span>
        <input type="range" min="1" max="40" value={minEvents} onChange={e=>setMinEvents(+e.target.value)} style={{width:120}}/>
        <span className="mono" style={{fontSize:12,fontWeight:600,minWidth:24}}>{minEvents}</span>
      </div>

      {/* 列表 — 紧凑布局, 去掉机构/类型之间的空白 */}
      <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
        <table className="cm-table" style={{tableLayout:'fixed',width:'100%'}}>
          <colgroup>
            <col style={{width:32}}/>
            <col/>
            <col style={{width:80}}/>
            <col style={{width:64}}/>
            <col style={{width:64}}/>
            <col style={{width:80}}/>
            <col style={{width:80}}/>
            <col style={{width:80}}/>
            <col style={{width:64}}/>
            <col style={{width:80}}/>
            <col style={{width:24}}/>
          </colgroup>
          <thead><tr>
            <th>#</th>
            <th>机构</th>
            <th>类型</th>
            <th className="num">事件</th>
            <th className="num">胜率</th>
            <th className="num">avg 10d</th>
            <th className="num">avg 30d</th>
            <th className="num">avg 60d</th>
            <th className="num">avg 90d</th>
            <th>近 12 次</th>
            <th/>
          </tr></thead>
          <tbody>
            {filtered.map((i, idx) => (
              <tr key={i.inst_uid} style={{cursor:'pointer'}} onClick={() => openInst(i)}>
                <td className="mono muted-2" style={{fontSize:11}}>{idx+1}</td>
                <td style={{minWidth:0}}>
                  <div style={{display:'flex',alignItems:'center',gap:8,minWidth:0}}>
                    <div style={{width:24,height:24,borderRadius:5,background:'var(--c-bg-2)',color:'var(--c-ink-70)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:10,fontWeight:600,flexShrink:0}}>
                      {i.alias.slice(0,2)}
                    </div>
                    <div style={{minWidth:0,flex:1}}>
                      <div style={{fontWeight:500,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{i.alias}</div>
                      <div className="muted-2 mono" style={{fontSize:10,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{i.inst_uid}</div>
                    </div>
                  </div>
                </td>
                <td><RolePill role={i.role}/></td>
                <td className="num mono">{i.buy_event_count}</td>
                <td className="num mono" style={{fontWeight:600,color:i.win_rate>=0.6?'var(--c-accent)':i.win_rate>=0.5?'var(--c-warn)':'var(--c-bad)'}}>
                  {Math.round(i.win_rate*100)}%
                </td>
                <td className={`num mono ${i.buy_avg_gain_10d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_10d)}</td>
                <td className={`num mono ${i.buy_avg_gain_30d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_30d)}</td>
                <td className={`num mono ${i.buy_avg_gain_60d>=0?'up':'down'}`} style={{fontWeight:600}}>{fmtPct(i.buy_avg_gain_60d)}</td>
                <td className={`num mono ${i.buy_avg_gain_90d>=0?'up':'down'}`}>{fmtPct(i.buy_avg_gain_90d)}</td>
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
   Page 4 · ETF  (NEW)
   ============================================================ */
function PageETF() {
  const { ETFS, PICKS, ApiTag, fmtPct } = window.CMV3;
  const [sort, setSort] = useStateR('overlap');
  const sorted = useMemoR(() => {
    const arr = [...ETFS];
    if (sort === 'overlap') arr.sort((a,b) => b.overlap_weight - a.overlap_weight);
    if (sort === 'chg')     arr.sort((a,b) => b.chg_pct - a.chg_pct);
    if (sort === 'aum')     arr.sort((a,b) => parseFloat(b.aum) - parseFloat(a.aum));
    return arr;
  }, [sort]);
  return (
    <div>
      <window.PageHead
        crumbs={[['研究'],['ETF', true]]}
        title={`ETF · ${ETFS.length} 只`}
        sub="按 ETF 持仓与今日推荐股票的重叠程度排序 — 想做组合化跟随时可以直接买 ETF"
        api={<ApiTag path="/api/etf/list?with_overlap=daily-topk"/>}
        actions={[['导入 ETF 池'],['建立 ETF 组合','primary']]}
      />

      <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:12}}>
        <span className="muted" style={{fontSize:12}}>排序</span>
        <window.Segmented value={sort} onChange={setSort} options={[['overlap','与推荐重叠'],['chg','今日涨幅'],['aum','规模']]}/>
        <div style={{flex:1}}/>
        <span className="muted-2 mono" style={{fontSize:11}}>重叠 = 推荐股票在 ETF 中的权重之和</span>
      </div>

      <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
        <table className="cm-table">
          <thead><tr>
            <th style={{width:120}}>ETF</th>
            <th style={{width:90}}>规模 AUM</th>
            <th className="num" style={{width:90}}>今日</th>
            <th>与今日推荐重叠</th>
            <th className="num" style={{width:90}}>重叠权重</th>
            <th style={{width:30}}/>
          </tr></thead>
          <tbody>
            {sorted.map(e => (
              <tr key={e.etf_code} style={{cursor:'pointer'}}>
                <td>
                  <div style={{display:'flex',alignItems:'center',gap:8}}>
                    <div style={{width:24,height:24,borderRadius:5,background:'var(--c-bg-2)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:10,fontWeight:600,color:'var(--c-ink-70)',flexShrink:0}}>{e.etf_code.slice(0,2)}</div>
                    <div>
                      <div style={{fontWeight:500}}>{e.etf_name}</div>
                      <div className="muted-2 mono" style={{fontSize:10}}>{e.etf_code} · {e.top_holdings} 持仓</div>
                    </div>
                  </div>
                </td>
                <td className="mono muted" style={{fontSize:11.5}}>{e.aum}</td>
                <td className={`num mono ${e.chg_pct>=0?'up':'down'}`} style={{fontWeight:600}}>{fmtPct(e.chg_pct)}</td>
                <td>
                  {e.overlap_with_picks.length === 0
                    ? <span className="muted-2" style={{fontSize:11}}>无重叠</span>
                    : <div style={{display:'flex',flexWrap:'wrap',gap:4}}>
                        {e.overlap_with_picks.map(code => {
                          const p = PICKS.find(p => p.stock_code === code);
                          return p ? <span key={code} className="pill pill-accent" style={{height:18,fontSize:10}}>
                            #{p.rank} {p.stock_name}
                          </span> : null;
                        })}
                      </div>
                  }
                </td>
                <td className="num mono" style={{fontWeight:600,color:e.overlap_weight>=0.3?'var(--c-accent-fg)':'var(--c-ink-100)'}}>
                  {(e.overlap_weight*100).toFixed(0)}%
                </td>
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

window.PageStockResearch = PageStockResearch;
window.PageInstResearch  = PageInstResearch;
window.PageETF           = PageETF;
