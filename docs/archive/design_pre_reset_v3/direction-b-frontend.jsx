/* Direction B — 前台克制
   单股深度页 + TopK 列表叠加, 雪球风的内容密度 + Linear 风的克制框架 */

const { useState: useStateB, useMemo: useMemoB } = React;

const TOPK = [
  ['600519','贵州茅台',  '白酒',     1681.20, +1.42, 92, 'champion', '社保 +0.4%', 0],
  ['000858','五 粮 液',  '白酒',      138.55, +0.83, 88, 'champion', '社保增持',  0],
  ['300750','宁德时代',  '电池',      218.40, +2.15, 86, 'both',     'QFII 新进', 1],
  ['002594','比亚迪',    '汽车',      245.12, -0.42, 82, 'champion', '高管增持',  0],
  ['600036','招商银行',  '银行',       38.71, +0.35, 81, 'both',     '社保稳持',  0],
  ['000333','美 的集团',  '家电',       68.90, +1.08, 79, 'champion', 'QFII 增持', 0],
  ['601318','中国平安',  '保险',       49.22, -0.31, 77, 'champion', '北向减持',  1],
  ['600276','恒瑞医药',  '医药',       42.18, +0.58, 76, 'challenger','基金新进', 1],
  ['603259','药明康德',  'CXO',         85.40, +1.92, 75, 'both',     '社保增持',  0],
  ['600887','伊利股份',  '乳业',       29.68, +0.20, 73, 'champion', '稳定持仓',  0],
];

const EVENTS = [
  { date: '05-06', kind: 'add',   inst: '社保 105 组合', stock: '600519 贵州茅台', delta: '+82 万股', tone: 'up' },
  { date: '05-06', kind: 'new',   inst: 'QFII · 摩根资管', stock: '300750 宁德时代', delta: '建仓 412 万股', tone: 'up' },
  { date: '05-05', kind: 'cut',   inst: '香港中央结算',     stock: '601318 中国平安', delta: '-1 280 万股', tone: 'down' },
  { date: '05-05', kind: 'add',   inst: '中央汇金',         stock: '600036 招商银行', delta: '+540 万股', tone: 'up' },
  { date: '05-04', kind: 'exec',  inst: '高管 · 王某',     stock: '002594 比亚迪',   delta: '减持 12 万股', tone: 'down' },
  { date: '05-04', kind: 'block', inst: '大宗 · 折价 4.2%', stock: '300750 宁德时代', delta: '218 元 · 50 万股', tone: 'down' },
  { date: '05-03', kind: 'new',   inst: '社保 117 组合',   stock: '603259 药明康德', delta: '建仓 88 万股', tone: 'up' },
];

const KIND_META = {
  add:   { label: '增持', tone: 'up'   },
  cut:   { label: '减持', tone: 'down' },
  new:   { label: '新进', tone: 'up'   },
  exec:  { label: '高管', tone: 'down' },
  block: { label: '大宗', tone: 'down' },
};

// 自选 + 最近研究
const WATCH = ['600519','300750','002594','600036'];

// =============== 顶部 chrome (复用 A 的精神, 但更轻) ====================
function TopBarB({ activePage, setActivePage }) {
  const tabs = [
    { id:'today',  label:'今日研究' },
    { id:'stock',  label:'单股深度', active:true },
    { id:'inst',   label:'机构' },
    { id:'etf',    label:'ETF' },
    { id:'admin',  label:'后台' },
  ];
  return (
    <header className="app-top">
      <div className="app-brand">
        <span className="mark">CM</span>
        <span>Chunky Monkey</span>
      </div>
      <div className="app-tabs">
        {tabs.map(t => (
          <button key={t.id}
            className={`app-tab ${activePage===t.id?'active':''}`}
            onClick={() => setActivePage(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="app-spacer"/>
      <div className="app-search">
        <span style={{color:'var(--c-ink-40)'}}>⌕</span>
        <input placeholder="代码 / 名称 / 机构…"/>
        <span className="kbd">⌘K</span>
      </div>
      <button className="btn btn-ghost"><span className="pill pill-ok"><span className="pill-dot"/>data 11min</span></button>
      <div style={{width:24,height:24,borderRadius:12,background:'var(--c-ink-100)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:11,fontWeight:600}}>DP</div>
    </header>
  );
}

// =============== 单股深度页 ============================================
function StockDeep() {
  const [tab, setTab] = useStateB('overview');
  return (
    <div style={{display:'grid',gridTemplateColumns:'1fr 320px',gap:20,height:'100%'}}>
      {/* 主区 */}
      <div style={{minWidth:0}}>
        {/* 股票头 */}
        <div style={{display:'flex',alignItems:'flex-end',gap:14,marginBottom:14}}>
          <div>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
              <span className="tag" style={{background:'var(--c-accent-bg)',color:'var(--c-accent-fg)'}}>沪</span>
              <span className="mono muted-2" style={{fontSize:13}}>600519</span>
              <span className="pill pill-accent">champion · 92</span>
              <span className="pill"><span className="pill-dot"/>已加自选</span>
            </div>
            <div style={{display:'flex',alignItems:'baseline',gap:14}}>
              <h1 style={{fontSize:26,fontWeight:600,letterSpacing:'-0.01em'}}>贵州茅台</h1>
              <span className="muted" style={{fontSize:12}}>白酒 · 上证 50 · 沪深 300</span>
            </div>
          </div>
          <div style={{flex:1}}/>
          <div style={{textAlign:'right'}}>
            <div className="mono up" style={{fontSize:32,fontWeight:600,letterSpacing:'-0.02em'}}>1681.20</div>
            <div className="up mono" style={{fontSize:13}}>+23.50  +1.42%</div>
            <div className="muted" style={{fontSize:11,marginTop:2}}>15:00 · 收盘</div>
          </div>
        </div>

        {/* 关键指标 + 涨跌 */}
        <div style={{display:'grid',gridTemplateColumns:'repeat(6,1fr)',gap:0,marginBottom:14,border:'1px solid var(--c-line)',borderRadius:8,background:'var(--c-surface)',overflow:'hidden'}}>
          {[
            ['今开','1659.00'],
            ['最高','1685.00'],
            ['最低','1655.20'],
            ['成交','42.1 亿'],
            ['市盈率','25.4'],
            ['市值','2.11 万亿'],
          ].map(([k,v],i) => (
            <div key={i} style={{padding:'10px 14px',borderRight:i<5?'1px solid var(--c-line)':'0'}}>
              <div className="muted" style={{fontSize:'var(--t-xs)',marginBottom:2}}>{k}</div>
              <div className="mono" style={{fontSize:'var(--t-md)',fontWeight:500,color:'var(--c-ink-100)'}}>{v}</div>
            </div>
          ))}
        </div>

        {/* tabs */}
        <div style={{borderBottom:'1px solid var(--c-line)',display:'flex',gap:0,marginBottom:14}}>
          {[
            ['overview','概览'],
            ['kline','K 线'],
            ['holders','机构持仓'],
            ['events','事件流'],
            ['signals','信号 / 评分'],
            ['fundamentals','基本面'],
          ].map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} style={{
              padding:'8px 14px', fontSize:13,
              color: tab===id?'var(--c-ink-100)':'var(--c-ink-55)',
              fontWeight: tab===id?600:500,
              borderBottom: tab===id?'2px solid var(--c-ink-100)':'2px solid transparent',
              marginBottom:-1,
            }}>{label}</button>
          ))}
        </div>

        {/* K-line + holders 主区 */}
        <div className="cm-card" style={{padding:14,marginBottom:14}}>
          <div className="cm-section-h">
            <div>
              <h3>价格 + 机构事件叠加</h3>
              <span className="desc">120 日 · 红线为机构买入事件 · 灰线为大宗 / 高管减持</span>
            </div>
            <div style={{display:'flex',gap:4}}>
              {['1月','3月','1年','3年'].map((s,i) =>
                <button key={i} className="btn btn-sm" style={i===1?{background:'var(--c-bg-2)',fontWeight:600}:{}}>{s}</button>
              )}
            </div>
          </div>
          <KLineChart/>
        </div>

        {/* 持仓变动 */}
        <div className="cm-card" style={{padding:14,marginBottom:14}}>
          <div className="cm-section-h">
            <div>
              <h3>十大股东 — 近 4 季差分</h3>
              <span className="desc">来自 fact_holders_event · 持仓报告期之间的差分推断</span>
            </div>
            <a href="#" onClick={e=>e.preventDefault()} className="muted" style={{fontSize:12}}>看全部 22 家 →</a>
          </div>
          <table className="cm-table">
            <thead>
              <tr>
                <th>机构</th>
                <th>类型</th>
                <th className="num">2025Q4</th>
                <th className="num">2026Q1</th>
                <th className="num">变化</th>
                <th>方向</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['香港中央结算',    '北向',  '8 924 万', '8 932 万', '+8 万',    'flat'],
                ['全国社保 105',    '社保',  '1 240 万', '1 322 万', '+82 万',   'up'],
                ['全国社保 117',    '社保',  '892 万',  '892 万',   '0',        'flat'],
                ['中央汇金',        '国家队','—',      '420 万',   '建仓',     'up'],
                ['摩根大通 - QFII', 'QFII',  '188 万',  '224 万',   '+36 万',   'up'],
                ['财通基金',        '基金',  '522 万',  '498 万',   '-24 万',   'down'],
                ['张某 (个人)',     '牛散',  '320 万',  '320 万',   '0',        'flat'],
              ].map((r,i) => (
                <tr key={i}>
                  <td style={{fontWeight:500}}>{r[0]}</td>
                  <td><span className="tag">{r[1]}</span></td>
                  <td className="num mono">{r[2]}</td>
                  <td className="num mono">{r[3]}</td>
                  <td className={`num mono ${r[5]==='up'?'up':r[5]==='down'?'down':'muted'}`} style={{fontWeight:500}}>{r[4]}</td>
                  <td>{r[5]==='up'? <span className="pill pill-up"><span className="pill-dot"/>买入</span>
                       : r[5]==='down'? <span className="pill pill-down"><span className="pill-dot"/>卖出</span>
                       : <span className="pill"><span className="pill-dot"/>不变</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 评分拆解 */}
        <div className="cm-card" style={{padding:14}}>
          <div className="cm-section-h">
            <div>
              <h3>champion_v3 评分拆解 — 92 / 100</h3>
              <span className="desc">每个维度的归一化贡献 · 鼠标悬停看具体特征</span>
            </div>
            <span className="pill pill-accent">topK rank #1</span>
          </div>
          <ScoreBreakdown/>
        </div>
      </div>

      {/* 右侧:今日 TopK + 事件流 */}
      <aside style={{display:'flex',flexDirection:'column',gap:14,minWidth:0,overflow:'auto'}}>
        <div className="cm-card" style={{padding:'12px 14px'}}>
          <div className="cm-section-h" style={{marginBottom:8}}>
            <div>
              <h3 style={{fontSize:13}}>今日 TopK · 05-06</h3>
              <span className="desc">champion_v3 · 420 候选取前 10</span>
            </div>
          </div>
          <div style={{display:'flex',flexDirection:'column'}}>
            {TOPK.slice(0,8).map((s,i) => (
              <div key={s[0]} style={{
                padding:'7px 8px', borderRadius:4, cursor:'pointer',
                background: i===0?'var(--c-accent-bg)':'transparent',
                display:'grid',gridTemplateColumns:'18px 1fr auto auto',gap:8,alignItems:'center',
                fontSize:12,
              }} onMouseEnter={e=>{if(i!==0)e.currentTarget.style.background='var(--c-bg-2)'}}
                 onMouseLeave={e=>{if(i!==0)e.currentTarget.style.background='transparent'}}>
                <span className="mono muted-2" style={{fontSize:10.5}}>#{i+1}</span>
                <div style={{minWidth:0}}>
                  <div style={{fontWeight:500,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{s[1]}</div>
                  <div className="mono muted-2" style={{fontSize:10}}>{s[0]} · {s[2]}</div>
                </div>
                <div className="mono" style={{fontSize:11,fontWeight:500,color:s[4]>=0?'var(--c-up)':'var(--c-down)'}}>
                  {s[4]>=0?'+':''}{s[4]}%
                </div>
                <div className="mono" style={{
                  width:30,textAlign:'right',fontSize:11,fontWeight:600,
                  color: s[6]==='champion'?'var(--c-accent-fg)':'var(--c-ink-70)',
                }}>{s[5]}</div>
              </div>
            ))}
          </div>
          <div style={{borderTop:'1px solid var(--c-line)',marginTop:8,paddingTop:8,display:'flex',justifyContent:'space-between',fontSize:11}}>
            <a href="#" onClick={e=>e.preventDefault()} className="muted">看全部 420 →</a>
            <span className="muted-2 mono">06:28 推送</span>
          </div>
        </div>

        <div className="cm-card" style={{padding:'12px 14px'}}>
          <div className="cm-section-h" style={{marginBottom:8}}>
            <div>
              <h3 style={{fontSize:13}}>近期机构事件</h3>
              <span className="desc">买入 / 卖出 / 大宗 / 高管</span>
            </div>
            <select className="input" style={{height:22,fontSize:11,padding:'0 6px'}}>
              <option>全部</option>
            </select>
          </div>
          <div style={{display:'flex',flexDirection:'column',gap:0}}>
            {EVENTS.map((e,i) => (
              <div key={i} style={{padding:'8px 0',borderBottom:i<EVENTS.length-1?'1px solid var(--c-line)':'0',fontSize:12}}>
                <div style={{display:'flex',alignItems:'center',gap:6,marginBottom:3}}>
                  <span className="mono muted-2" style={{fontSize:10}}>{e.date}</span>
                  <span className={`pill ${e.tone==='up'?'pill-up':'pill-down'}`} style={{height:16,fontSize:10}}>{KIND_META[e.kind].label}</span>
                  <span style={{fontWeight:500}}>{e.inst}</span>
                </div>
                <div style={{display:'flex',justifyContent:'space-between',gap:8}}>
                  <span className="muted" style={{fontSize:11}}>{e.stock}</span>
                  <span className={`mono ${e.tone==='up'?'up':'down'}`} style={{fontSize:11,fontWeight:500}}>{e.delta}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="cm-card" style={{padding:'12px 14px'}}>
          <div className="cm-section-h" style={{marginBottom:6}}>
            <div><h3 style={{fontSize:13}}>自选 · 4</h3></div>
            <a href="#" onClick={e=>e.preventDefault()} className="muted" style={{fontSize:11}}>编辑</a>
          </div>
          {WATCH.map(c => {
            const s = TOPK.find(t => t[0] === c) || ['—','—','—',0,0,0,'',''];
            return (
              <div key={c} style={{display:'grid',gridTemplateColumns:'1fr auto auto',gap:8,padding:'5px 0',fontSize:12,alignItems:'center'}}>
                <div>
                  <div style={{fontWeight:500}}>{s[1]}</div>
                  <div className="mono muted-2" style={{fontSize:10}}>{s[0]}</div>
                </div>
                <div className="mono" style={{fontSize:11}}>{s[3]}</div>
                <div className="mono" style={{fontSize:11,fontWeight:500,color:s[4]>=0?'var(--c-up)':'var(--c-down)'}}>{s[4]>=0?'+':''}{s[4]}%</div>
              </div>
            );
          })}
        </div>
      </aside>
    </div>
  );
}

function KLineChart() {
  // 120 日 mock K线
  const days = 90;
  const arr = useMemoB(() => {
    let p = 1500; const out = [];
    for (let i=0;i<days;i++) {
      const o = p + (Math.random()-0.5)*8;
      const c = o + (Math.random()-0.45)*22;
      const h = Math.max(o,c) + Math.random()*8;
      const l = Math.min(o,c) - Math.random()*8;
      out.push({o,c,h,l});
      p = c;
    }
    return out;
  }, []);
  const events = [12,28,42,55,68,75,82]; // 事件位置
  const cuts   = [22,49,71];
  const all = arr.flatMap(d=>[d.h,d.l]);
  const max = Math.max(...all), min = Math.min(...all);
  const W = 720, H = 240;
  const xw = W / days;
  const sy = v => H - ((v - min) / (max - min)) * (H - 20) - 10;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:240,display:'block'}}>
      {/* 网格 */}
      {[0,1,2,3,4].map(i => <line key={i} x1={0} x2={W} y1={(H/4)*i+5} y2={(H/4)*i+5} stroke="var(--c-line)" strokeWidth="0.5" strokeDasharray="2 3"/>)}
      {/* 蜡烛 */}
      {arr.map((d,i) => {
        const x = i*xw + xw/2;
        const up = d.c >= d.o;
        const color = up ? 'var(--c-up)' : 'var(--c-down)';
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={sy(d.h)} y2={sy(d.l)} stroke={color} strokeWidth="1"/>
            <rect x={x - xw*0.32} y={sy(Math.max(d.o,d.c))}
                  width={xw*0.64} height={Math.max(1, Math.abs(sy(d.o)-sy(d.c)))}
                  fill={up?color:color} opacity={up?1:1}/>
          </g>
        );
      })}
      {/* 事件标记 */}
      {events.map(i => {
        const x = i*xw + xw/2;
        return (
          <g key={'ev'+i}>
            <line x1={x} x2={x} y1={H-4} y2={H-12} stroke="var(--c-up)" strokeWidth="1.5"/>
            <circle cx={x} cy={H-14} r="3" fill="var(--c-up)"/>
          </g>
        );
      })}
      {cuts.map(i => {
        const x = i*xw + xw/2;
        return (
          <g key={'ct'+i}>
            <line x1={x} x2={x} y1={4} y2={12} stroke="var(--c-ink-55)" strokeWidth="1.5"/>
            <circle cx={x} cy={14} r="3" fill="var(--c-ink-55)"/>
          </g>
        );
      })}
    </svg>
  );
}

function ScoreBreakdown() {
  const dims = [
    { k: '机构持仓质量', score: 28, max: 30, top: ['社保稳定增持 +12','QFII 新进 +8','北向稳定 +6'] },
    { k: '基本面',       score: 22, max: 25, top: ['ROE 32% +8','现金流 +7','毛利稳定 +6'] },
    { k: '动量 / 技术',  score: 18, max: 20, top: ['20D MA 上穿 +6','RSI 65 +5','量价配合 +5'] },
    { k: '资金流',       score: 14, max: 15, top: ['北向净流入 +6','大单买 +4','融资余额 +4'] },
    { k: '风险',         score: -10,max: 10, top: ['估值偏高 -4','行业拥挤 -3','大盘压力 -3'] },
  ];
  return (
    <div style={{display:'flex',flexDirection:'column',gap:8}}>
      {dims.map(d => {
        const pct = (d.score / d.max) * 100;
        const positive = d.score >= 0;
        return (
          <div key={d.k} style={{display:'grid',gridTemplateColumns:'160px 1fr 60px',gap:12,alignItems:'center',padding:'4px 0'}}>
            <div style={{fontSize:12,fontWeight:500}}>{d.k}</div>
            <div style={{position:'relative',height:18,background:'var(--c-bg-2)',borderRadius:3}}>
              <div style={{
                position:'absolute',top:0,bottom:0,
                left: positive ? '0' : `${50+pct/2}%`,
                width: `${Math.abs(pct)}%`,
                background: positive ? 'var(--c-accent)' : 'var(--c-bad)',
                borderRadius:3,
              }}/>
              <div style={{position:'absolute',inset:0,display:'flex',alignItems:'center',padding:'0 8px',fontSize:10.5,color:'var(--c-ink-55)',gap:8,overflow:'hidden'}}>
                {d.top.map((t,i)=> <span key={i} style={{whiteSpace:'nowrap'}}>{t}</span>)}
              </div>
            </div>
            <div className="mono num" style={{fontSize:13,fontWeight:600,color:positive?'var(--c-ink-100)':'var(--c-bad)'}}>
              {positive?'+':''}{d.score}<span className="muted-2" style={{fontSize:10,marginLeft:2}}>/{d.max}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ============== ROOT ===========================
function DirectionB() {
  const [page, setPage] = useStateB('stock');
  return (
    <div className="app">
      <TopBarB activePage={page} setActivePage={setPage}/>
      <main style={{flex:1,overflow:'auto',padding:'20px 28px',background:'var(--c-bg)'}}>
        <StockDeep/>
      </main>
    </div>
  );
}

window.DirectionB = DirectionB;
