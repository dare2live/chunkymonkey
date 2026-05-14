/* Direction C — 机构 track record
   leaderboard 风 + 行内迷你 sparkline + 抽屉看具体事件
   把"过去几次 buy 事件 60 日跟随收益"摊开 */

const { useState: useStateC, useMemo: useMemoC } = React;

const INSTS = [
  { id:1, name:'全国社保 105 组合',  alias:'社保 105',     type:'社保',   tracked:'2021-03', events:48, win:'62.5%', avgRet:'+8.4%',  med:'+5.2%',  hold:24, last:'2026-04-22', last60:'+12.8%', recent:[3,5,4,6,8,7,9,8,11,10,12,9], hot:true },
  { id:2, name:'香港中央结算',       alias:'北向',         type:'北向',   tracked:'2020-01', events:312,win:'54.2%', avgRet:'+5.1%',  med:'+2.8%',  hold:18, last:'2026-05-05', last60:'-2.4%',  recent:[5,4,6,5,4,3,5,4,6,5,4,3], hot:false },
  { id:3, name:'摩根资管 - QFII',    alias:'摩根 QFII',   type:'QFII',   tracked:'2019-06', events:62, win:'58.1%', avgRet:'+9.2%',  med:'+6.4%',  hold:42, last:'2026-05-04', last60:'+18.5%', recent:[4,6,5,7,8,6,9,8,10,9,11,12], hot:true },
  { id:4, name:'中央汇金',           alias:'国家队',       type:'国家队', tracked:'2020-04', events:24, win:'66.7%', avgRet:'+11.2%', med:'+9.1%',  hold:60, last:'2026-04-18', last60:'+14.2%', recent:[2,3,4,5,4,6,7,8,7,9,10,11], hot:false },
  { id:5, name:'国家大基金二期',     alias:'大基金 II',    type:'国家大基金',tracked:'2020-10',events:18, win:'72.2%', avgRet:'+18.5%', med:'+14.2%', hold:120,last:'2026-03-12', last60:'+22.4%', recent:[1,2,3,4,5,5,6,7,8,9,10,11], hot:true },
  { id:6, name:'王某 (个人股东)',    alias:'王某',        type:'牛散',   tracked:'2022-08', events:14, win:'71.4%', avgRet:'+15.8%', med:'+11.0%', hold:90, last:'2026-04-08', last60:'+19.8%', recent:[2,3,4,5,4,6,7,9,8,10,9,12], hot:false },
  { id:7, name:'张某 (个人股东)',    alias:'张某',        type:'牛散',   tracked:'2022-04', events:22, win:'45.5%', avgRet:'+2.1%',  med:'-1.4%',  hold:30, last:'2026-04-30', last60:'-5.2%',  recent:[5,6,4,3,5,2,4,3,5,4,3,2], hot:false },
  { id:8, name:'中国人寿保险',       alias:'人寿',        type:'保险',   tracked:'2019-01', events:84, win:'52.4%', avgRet:'+4.2%',  med:'+1.8%',  hold:36, last:'2026-04-28', last60:'+1.2%',  recent:[4,5,4,6,5,4,5,6,5,4,5,6], hot:false },
  { id:9, name:'平安人寿保险',       alias:'平安',        type:'保险',   tracked:'2019-01', events:71, win:'53.5%', avgRet:'+3.8%',  med:'+1.2%',  hold:42, last:'2026-04-22', last60:'+0.4%',  recent:[3,4,4,5,5,4,4,5,4,4,5,4], hot:false },
  { id:10,name:'中欧基金 · 葛兰',   alias:'葛兰',        type:'基金',   tracked:'2020-06', events:38, win:'48.4%', avgRet:'+1.2%',  med:'-2.4%',  hold:28, last:'2026-04-30', last60:'-8.4%',  recent:[5,4,3,2,3,2,4,3,2,3,2,1], hot:false },
];

const TYPE_TONES = {
  '社保':       { bg:'#E8F2EC', fg:'#1B5E20' },
  '北向':       { bg:'#E3F2FD', fg:'#0D47A1' },
  'QFII':       { bg:'#F3E5F5', fg:'#4A148C' },
  '国家队':     { bg:'#FFF3E0', fg:'#E65100' },
  '国家大基金': { bg:'#FBE9E7', fg:'#BF360C' },
  '牛散':       { bg:'#FFF8E1', fg:'#F57F17' },
  '保险':       { bg:'#E0F2F1', fg:'#004D40' },
  '基金':       { bg:'#F3F4F6', fg:'#374151' },
};

function TypePill({type}) {
  const t = TYPE_TONES[type] || TYPE_TONES['基金'];
  return <span className="tag" style={{background:t.bg,color:t.fg,fontFamily:'var(--f-sans)'}}>{type}</span>;
}

function MiniLine({data, tone='accent'}) {
  const max = Math.max(...data), min = Math.min(...data);
  const W = 80, H = 24;
  const xw = W / (data.length-1);
  const sy = v => H - ((v - min) / Math.max(0.001, max - min)) * (H-4) - 2;
  const path = data.map((v,i) => `${i?'L':'M'}${i*xw} ${sy(v)}`).join(' ');
  const last = data[data.length-1] >= data[0];
  const color = tone === 'accent'? 'var(--c-accent)' : last ? 'var(--c-up)' : 'var(--c-down)';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:80,height:24,display:'block'}}>
      <path d={path} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round"/>
      <circle cx={(data.length-1)*xw} cy={sy(data[data.length-1])} r="2" fill={color}/>
    </svg>
  );
}

function PerfBar({pct, max=20}) {
  // -max ~ +max
  const v = parseFloat(pct);
  const positive = v >= 0;
  const w = Math.min(100, Math.abs(v) / max * 100);
  return (
    <div style={{position:'relative',height:18,width:120,background:'var(--c-bg-2)',borderRadius:2}}>
      <div style={{position:'absolute',left:'50%',top:0,bottom:0,width:1,background:'var(--c-line-2)'}}/>
      <div style={{
        position:'absolute',top:2,bottom:2,
        left: positive?'50%':`${50-w/2}%`,
        width: `${w/2}%`,
        background: positive?'var(--c-up)':'var(--c-down)',
        opacity:0.85,
      }}/>
      <div style={{position:'absolute',inset:0,display:'flex',alignItems:'center',justifyContent:'center',fontSize:10.5,fontWeight:600,color:positive?'var(--c-up)':'var(--c-down)',fontFamily:'var(--f-mono)'}}>
        {pct}
      </div>
    </div>
  );
}

function TopbarC() {
  return (
    <header className="app-top">
      <div className="app-brand">
        <span className="mark">CM</span>
        <span>Chunky Monkey</span>
      </div>
      <div className="app-tabs">
        {[
          ['今日研究'],
          ['股票'],
          ['机构', true],
          ['ETF'],
          ['后台'],
        ].map(([l,a],i)=>(
          <button key={i} className={`app-tab ${a?'active':''}`}>{l}</button>
        ))}
      </div>
      <div className="app-spacer"/>
      <div className="app-search">
        <span style={{color:'var(--c-ink-40)'}}>⌕</span>
        <input placeholder="搜机构名 / 别名…"/>
      </div>
      <div style={{width:24,height:24,borderRadius:12,background:'var(--c-ink-100)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:11,fontWeight:600}}>DP</div>
    </header>
  );
}

function InstDrawer({inst, onClose}) {
  if (!inst) return null;
  const buys = [
    ['2026-04-22','600519 贵州茅台',  '建仓 88 万股',  '+12.8%','60d', 'open'],
    ['2026-03-15','300750 宁德时代',  '增持 412 万股','+18.2%','60d', 'closed'],
    ['2026-02-08','600036 招商银行',  '增持 540 万股','+4.1%', '60d', 'closed'],
    ['2026-01-22','002594 比亚迪',    '建仓 220 万股','+8.4%', '60d', 'closed'],
    ['2025-12-08','000333 美的集团',  '增持 102 万股','-2.1%', '60d', 'closed'],
    ['2025-11-12','600276 恒瑞医药',  '建仓 88 万股',  '+22.5%','60d', 'closed'],
    ['2025-10-04','603259 药明康德',  '建仓 60 万股',  '+15.2%','60d', 'closed'],
  ];
  return (
    <div style={{position:'absolute',inset:0,zIndex:50,display:'flex',justifyContent:'flex-end'}}>
      <div onClick={onClose} style={{position:'absolute',inset:0,background:'rgba(12,10,9,.32)'}}/>
      <div style={{position:'relative',width:680,height:'100%',background:'var(--c-surface)',borderLeft:'1px solid var(--c-line)',display:'flex',flexDirection:'column'}}>
        <div style={{padding:'14px 20px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',gap:12}}>
          <div style={{width:36,height:36,borderRadius:8,background:'var(--c-ink-100)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontWeight:600,fontSize:14}}>
            {inst.alias.slice(0,2)}
          </div>
          <div style={{flex:1}}>
            <div style={{display:'flex',alignItems:'center',gap:8}}>
              <h2 style={{fontSize:16,fontWeight:600}}>{inst.name}</h2>
              <TypePill type={inst.type}/>
            </div>
            <div className="muted" style={{fontSize:11,marginTop:2}}>
              别名 {inst.alias} · 跟踪自 {inst.tracked} · {inst.events} 个事件
            </div>
          </div>
          <button className="btn">编辑标签</button>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>✕</button>
        </div>

        <div style={{flex:1,overflow:'auto',padding:20}}>
          <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:12,marginBottom:18}}>
            <KStat k="胜率" v={inst.win} sub={`${inst.events} 次买入`} hi/>
            <KStat k="平均收益" v={inst.avgRet} sub="60 日跟随" tone={parseFloat(inst.avgRet)>0?'up':'down'} hi/>
            <KStat k="中位数" v={inst.med} sub="抗极值" tone={parseFloat(inst.med)>0?'up':'down'}/>
            <KStat k="平均持有" v={inst.hold+' 日'} sub="estimated"/>
          </div>

          <div className="cm-card" style={{padding:14,marginBottom:14}}>
            <div className="cm-section-h">
              <div>
                <h3>近 12 次买入 · 60 日累计收益分布</h3>
                <span className="desc">每根柱子 = 一次 buy 事件后 60 日收益</span>
              </div>
            </div>
            <DistChart data={inst.recent}/>
          </div>

          <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
            <div style={{padding:'10px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <div>
                <h3 style={{fontSize:13,fontWeight:600}}>事件流</h3>
                <span className="desc">最新 7 次 · 早期事件折叠</span>
              </div>
              <button className="btn btn-sm">导出全部 {inst.events}</button>
            </div>
            <table className="cm-table">
              <thead><tr>
                <th>日期</th>
                <th>股票</th>
                <th>动作</th>
                <th className="num">60d 收益</th>
                <th>状态</th>
              </tr></thead>
              <tbody>
                {buys.map((r,i) => (
                  <tr key={i}>
                    <td className="mono muted" style={{fontSize:11}}>{r[0]}</td>
                    <td style={{fontWeight:500}}>{r[1]}</td>
                    <td className="muted">{r[2]}</td>
                    <td className={`num mono ${parseFloat(r[3])>0?'up':'down'}`} style={{fontWeight:600}}>{r[3]}</td>
                    <td>
                      {r[5]==='open'
                        ? <span className="pill pill-warn"><span className="pill-dot"/>未到期</span>
                        : <span className="pill pill-ghost"><span className="pill-dot"/>已结</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function KStat({k,v,sub,tone,hi}) {
  const c = tone==='up'?'var(--c-up)':tone==='down'?'var(--c-down)':'var(--c-ink-100)';
  return (
    <div style={{padding:'12px 14px',background:hi?'var(--c-bg-2)':'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:8}}>
      <div className="muted" style={{fontSize:10.5,textTransform:'uppercase',letterSpacing:'0.04em'}}>{k}</div>
      <div className="mono" style={{fontSize:22,fontWeight:600,color:c,letterSpacing:'-0.01em',marginTop:2}}>{v}</div>
      <div className="muted-2" style={{fontSize:10.5,marginTop:2}}>{sub}</div>
    </div>
  );
}

function DistChart({data}) {
  const W = 600, H = 140;
  const max = Math.max(...data, 5);
  const xw = W / data.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:140}}>
      {/* 0 线 */}
      <line x1={0} x2={W} y1={H-30} y2={H-30} stroke="var(--c-line)" strokeWidth="1"/>
      {data.map((v,i) => {
        const h = (v / max) * (H-50);
        const x = i * xw + xw*0.15;
        const w = xw * 0.7;
        const y = H - 30 - h;
        return (
          <g key={i}>
            <rect x={x} y={y} width={w} height={h} fill={v>=5?'var(--c-up)':'var(--c-down)'} opacity="0.85"/>
            <text x={x+w/2} y={y-4} textAnchor="middle" fontSize="10" fill="var(--c-ink-55)" fontFamily="var(--f-mono)">+{v}%</text>
            <text x={x+w/2} y={H-14} textAnchor="middle" fontSize="9.5" fill="var(--c-ink-40)" fontFamily="var(--f-mono)">e{i+1}</text>
          </g>
        );
      })}
    </svg>
  );
}

function InstPage({onOpen}) {
  const [type, setType] = useStateC('all');
  const [dim, setDim]   = useStateC('overview');
  const [q, setQ]       = useStateC('');

  const types = useMemoC(() => {
    const map = {};
    INSTS.forEach(i => map[i.type] = (map[i.type]||0)+1);
    return [['all','全部',INSTS.length], ...Object.entries(map).map(([k,v])=>[k,k,v])];
  }, []);

  const filtered = INSTS.filter(i => {
    if (type !== 'all' && i.type !== type) return false;
    if (q && !i.name.includes(q) && !i.alias.includes(q)) return false;
    return true;
  });

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <span>研究</span><span className="sep">›</span><span className="here">机构</span>
          </div>
          <h1>机构 track record</h1>
          <p>312 家跟踪机构 · 24 836 次披露事件 · 把每次 buy 后 60 日收益摊开看, 自己挑可跟随的</p>
        </div>
        <div className="page-actions">
          <button className="btn">导入机构</button>
          <button className="btn">批量管理</button>
          <button className="btn btn-primary">新建组合</button>
        </div>
      </div>

      {/* 工具条 */}
      <div className="cm-card" style={{padding:'10px 12px',marginBottom:14,display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
        <div className="app-search" style={{width:240}}>
          <span style={{color:'var(--c-ink-40)'}}>⌕</span>
          <input value={q} onChange={e=>setQ(e.target.value)} placeholder="机构名 / 别名"/>
        </div>
        <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
          {types.map(([k,l,c]) => (
            <button key={k} onClick={()=>setType(k)} style={{
              padding:'4px 10px',borderRadius:14,fontSize:11,
              background: type===k?'var(--c-ink-100)':'var(--c-bg-2)',
              color: type===k?'#fff':'var(--c-ink-70)',
              fontWeight: type===k?500:400,
              border:'1px solid '+(type===k?'var(--c-ink-100)':'transparent'),
            }}>{l} <span style={{opacity:0.6,marginLeft:4,fontFamily:'var(--f-mono)'}}>{c}</span></button>
          ))}
        </div>
        <div style={{flex:1}}/>
        <div style={{display:'inline-flex',background:'var(--c-bg-2)',borderRadius:6,padding:2,gap:1}}>
          {[['overview','综合'],['returns','买入表现'],['exits','退出表现'],['risk','风险']].map(([v,l]) => (
            <button key={v} onClick={()=>setDim(v)} style={{
              height:24,padding:'0 10px',borderRadius:4,fontSize:11,
              background: dim===v?'var(--c-surface)':'transparent',
              color: dim===v?'var(--c-ink-100)':'var(--c-ink-55)',
              fontWeight: dim===v?600:500,
              boxShadow: dim===v?'var(--sh-1)':'none',
            }}>{l}</button>
          ))}
        </div>
      </div>

      {/* 排行 */}
      <div className="cm-card" style={{overflow:'hidden'}}>
        <table className="cm-table">
          <thead>
            <tr>
              <th style={{width:36}}>#</th>
              <th>机构</th>
              <th style={{width:80}}>类型</th>
              <th className="num" style={{width:72}}>事件</th>
              <th className="num" style={{width:72}}>胜率</th>
              <th className="num" style={{width:120}}>平均 60d</th>
              <th className="num" style={{width:80}}>中位数</th>
              <th style={{width:104}}>近 12 次</th>
              <th className="num" style={{width:88}}>最近 60d</th>
              <th style={{width:60}}/>
            </tr>
          </thead>
          <tbody>
            {filtered.map((i, idx) => (
              <tr key={i.id} style={{cursor:'pointer'}} onClick={() => onOpen(i)}>
                <td className="mono muted-2" style={{fontSize:11}}>{idx+1}</td>
                <td>
                  <div style={{display:'flex',alignItems:'center',gap:8}}>
                    <div style={{width:24,height:24,borderRadius:5,background:'var(--c-bg-2)',color:'var(--c-ink-70)',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:10,fontWeight:600,flexShrink:0}}>
                      {i.alias.slice(0,2)}
                    </div>
                    <div style={{minWidth:0}}>
                      <div style={{fontWeight:500,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
                        {i.alias}
                        {i.hot && <span className="pill pill-accent" style={{marginLeft:6,height:14,fontSize:9}}>近期活跃</span>}
                      </div>
                      <div className="muted-2" style={{fontSize:10.5,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{i.name}</div>
                    </div>
                  </div>
                </td>
                <td><TypePill type={i.type}/></td>
                <td className="num mono">{i.events}</td>
                <td className="num mono" style={{fontWeight:500}}>{i.win}</td>
                <td><PerfBar pct={i.avgRet}/></td>
                <td className={`num mono ${parseFloat(i.med)>=0?'up':'down'}`}>{i.med}</td>
                <td><MiniLine data={i.recent}/></td>
                <td className={`num mono ${parseFloat(i.last60)>=0?'up':'down'}`} style={{fontWeight:500}}>{i.last60}</td>
                <td><button className="btn btn-sm btn-ghost" style={{color:'var(--c-ink-55)'}}>›</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 底部小结 */}
      <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:14,marginTop:14}}>
        <SummaryCard title="本月最活跃" rows={[
          ['北向 / 香港中央结算','42 次'],
          ['全国社保 105','11 次'],
          ['摩根 QFII','9 次'],
        ]}/>
        <SummaryCard title="近 60 日表现最佳" rows={[
          ['国家大基金 II','+22.4%'],
          ['王某','+19.8%'],
          ['摩根 QFII','+18.5%'],
        ]} tone="up"/>
        <SummaryCard title="风险提示" rows={[
          ['葛兰','胜率 48%, 连续 4 次亏损'],
          ['张某','胜率 45%, 仓位变动剧烈'],
          ['人寿','样本不足 (8 次)'],
        ]} tone="warn"/>
      </div>

      <div style={{height:24}}/>
    </div>
  );
}

function SummaryCard({title, rows, tone}) {
  return (
    <div className="cm-card" style={{padding:14}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
        <h3 style={{fontSize:13,fontWeight:600}}>{title}</h3>
        <a href="#" onClick={e=>e.preventDefault()} className="muted" style={{fontSize:11}}>更多 →</a>
      </div>
      {rows.map((r,i) => (
        <div key={i} style={{display:'flex',justifyContent:'space-between',padding:'5px 0',borderBottom:i<rows.length-1?'1px solid var(--c-line)':'0',fontSize:12}}>
          <span style={{fontWeight:500}}>{r[0]}</span>
          <span className={`mono ${tone==='up'?'up':tone==='warn'?'muted':''}`} style={{fontWeight:500}}>{r[1]}</span>
        </div>
      ))}
    </div>
  );
}

function DirectionC() {
  const [open, setOpen] = useStateC(null);
  return (
    <div className="app" style={{position:'relative'}}>
      <TopbarC/>
      <main style={{flex:1,overflow:'auto',padding:'20px 28px',background:'var(--c-bg)'}}>
        <InstPage onOpen={setOpen}/>
      </main>
      <InstDrawer inst={open} onClose={() => setOpen(null)}/>
    </div>
  );
}

window.DirectionC = DirectionC;
