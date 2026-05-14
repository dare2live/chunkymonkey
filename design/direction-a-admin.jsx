/* Direction A — 数据健康 后台
   Linear-style: 左导航 + 顶导航 + 主表格 + 抽屉 */

const { useState, useMemo, useEffect } = React;

// ============== mock data ============================================
const NAV = [
  { group: '研究', items: [
    { id: 'today',     label: '今日研究',  glyph: '今' },
    { id: 'stocks',    label: '股票',      glyph: '股', count: 4823 },
    { id: 'inst',      label: '机构',      glyph: '机', count: 312 },
    { id: 'etf',       label: 'ETF',       glyph: 'E' },
  ]},
  { group: '后台', items: [
    { id: 'lineage',   label: '数据链路',  glyph: 'L', active: true },
    { id: 'health',    label: '数据健康',  glyph: 'H', count: 7, countTone: 'bad' },
    { id: 'lab',       label: '模型实验室',glyph: 'M' },
    { id: 'strategy',  label: '策略 / 回测', glyph: 'S' },
    { id: 'ops',       label: '系统运维',  glyph: 'O' },
  ]},
  { group: '设置', items: [
    { id: 'settings',  label: '系统设置',  glyph: '⚙' },
  ]},
];

const SOURCES = [
  { id: 'tdxhub',    label: 'tdxhub',         tier: 1, role: '主源 · 行情/F10/通达信',     status: 'ok',    coverage: 84, freshness: '11 分钟前',  rows: '1.24 亿', rps: 142 },
  { id: 'miaoxiang', label: '妙想 F10',       tier: 2, role: '补源 · 东财 72 reportName',  status: 'ok',    coverage: 56, freshness: '38 分钟前',  rows: '8 720 万', rps: 31 },
  { id: 'akshare',   label: 'akshare',        tier: 3, role: '兜底 · 龙虎榜 / 行业',        status: 'warn',  coverage: 22, freshness: '2 小时前',   rows: '2 380 万', rps: 4 },
  { id: 'manual',    label: 'manual',         tier: 4, role: '手工 · 标签 / 别名 / 黑名单', status: 'ok',    coverage: 12, freshness: '昨日 22:04', rows: '14 230',   rps: '—' },
];

// 47 张表的快照
const ASSET_ROWS = [
  ['fact_holders_event',        'fact',  'tdxhub > 妙想',  '12 384 211', '2026-05-06', 'green',  '0.4h', '6h',  'build_tdx_holders'],
  ['mart_topk_recommendation',  'mart',  'champion_v3',     '420',        '2026-05-06', 'green',  '0.1h', '24h', 'run_daily_topk'],
  ['fact_executive_trade',      'fact',  'tdxhub',          '38 921',     '2026-05-06', 'green',  '0.2h', '24h', 'build_executive_trade'],
  ['fact_lhb_event',            'fact',  'akshare',         '94 102',     '2026-05-05', 'yellow', '28h',  '24h', 'build_lhb_events'],
  ['raw_kline_daily_tdx',       'raw',   'tdxhub',          '198 412 003','2026-05-06', 'green',  '0.3h', '6h',  'sync_kline_daily'],
  ['raw_orderbook_snapshot',    'raw',   'tdxhub',          '24 933 122', '2026-05-06', 'green',  '5min', '15m', 'sync_orderbook'],
  ['mart_institution_score',    'mart',  'fact_holders +…', '8 841',      '2026-05-06', 'green',  '0.9h', '24h', 'run_scoring'],
  ['fact_industry_overview',    'fact',  '妙想',            '186 702',    '2026-05-04', 'red',    '52h',  '24h', 'build_industry_overview'],
  ['raw_aif10_capability',      'raw',   '妙想',            '6 420 819',  '2026-05-06', 'green',  '1.1h', '12h', 'sync_aif10'],
  ['mart_etf_sector_rotation',  'mart',  'raw_kline +…',    '1 092',      '2026-05-06', 'green',  '0.7h', '24h', 'build_etf_rotation'],
  ['fact_qfii_holdings',        'fact',  'akshare',         '422 188',    '2026-05-04', 'yellow', '36h',  '24h', 'sync_qfii'],
  ['raw_capital_flow',          'raw',   'akshare',         '8 820 401',  '2026-05-06', 'green',  '0.6h', '12h', 'sync_capital'],
  ['mart_feature_panel',        'mart',  'fact_*',          '14 921 088', '2026-05-06', 'green',  '2.8h', '24h', 'build_feature_panel'],
  ['fact_holders_resolver',     'fact',  'tdxhub > 妙想',   '4 218 102',  '2026-05-06', 'green',  '0.5h', '24h', 'rebuild_holder_events'],
  ['raw_executive_announce',    'raw',   '妙想',            '212 084',    '2026-05-06', 'green',  '4h',   '24h', 'sync_executive'],
  ['fact_drift_psi',            'fact',  'mart_feature',    '2 880',      '2026-05-06', 'yellow', '12h',  '24h', 'compute_drift'],
  ['mart_screening_universe',   'mart',  'mart_feature +…', '3 218',      '2026-05-06', 'green',  '0.8h', '24h', 'build_universe'],
  ['raw_block_trades',          'raw',   '妙想 > akshare',  '184 920',    '2026-05-06', 'green',  '1h',   '24h', 'sync_block'],
  ['fact_pipeline_manifest',    'sys',   'manifest',        '12 304',     '2026-05-06', 'green',  '0.1h', '—',   'pipeline_lock'],
  ['raw_industry_member',       'raw',   '通达信公式',      '7 211',      '2026-04-29', 'red',    '7d',   '7d',  'sync_industry'],
];

const STEPS = [
  ['sync_kline_daily',       'tdxhub',   'ok',    '0:42'],
  ['sync_orderbook',         'tdxhub',   'ok',    '5:11'],
  ['sync_aif10',             '妙想',     'ok',    '1:08'],
  ['sync_executive',         '妙想',     'ok',    '0:18'],
  ['sync_capital',           'akshare',  'ok',    '0:24'],
  ['sync_qfii',              'akshare',  'warn',  '4:02'],
  ['build_holders_event',    'derive',   'ok',    '0:11'],
  ['build_executive_trade',  'derive',   'ok',    '0:09'],
  ['build_lhb_events',       'derive',   'warn',  '—'],
  ['build_feature_panel',    'derive',   'ok',    '2:48'],
  ['compute_drift',          'derive',   'ok',    '0:19'],
  ['build_universe',         'derive',   'ok',    '0:46'],
  ['run_scoring',            'model',    'ok',    '0:53'],
  ['run_daily_topk',         'model',    'ok',    '0:07'],
  ['build_evidence_bundle',  'model',    'idle',  '—'],
  ['storage_retention',      'ops',      'ok',    '0:03'],
];

// ============================================================
function StatusDot({ tone }) {
  const map = { green:'#1B7A6B', yellow:'#B8860B', red:'#B91C1C', gray:'#A8A29E', ok:'#1B7A6B', warn:'#B8860B', bad:'#B91C1C', idle:'#A8A29E' };
  return <span style={{display:'inline-block',width:8,height:8,borderRadius:4,background:map[tone]||'#A8A29E',flexShrink:0}}/>;
}

function FreshnessBar({ pct }) {
  // pct = age/sla %
  const tone = pct < 60 ? 'green' : pct < 120 ? 'yellow' : 'red';
  const bg = { green:'#1B7A6B', yellow:'#B8860B', red:'#B91C1C' }[tone];
  return (
    <div style={{display:'flex',alignItems:'center',gap:6}}>
      <div style={{width:48,height:4,background:'var(--c-ink-25)',borderRadius:2,overflow:'hidden'}}>
        <div style={{width:Math.min(pct,100)+'%',height:'100%',background:bg}}/>
      </div>
      <span className="mono" style={{fontSize:'10.5px',color:'var(--c-ink-55)'}}>{pct}%</span>
    </div>
  );
}

function NavRail({ activeView, setView }) {
  return (
    <aside className="app-side">
      {NAV.map(group => (
        <div key={group.group} className="side-section">
          <h5>{group.group}</h5>
          {group.items.map(it => (
            <div key={it.id}
              className={`side-item ${activeView===it.id?'active':''}`}
              onClick={() => setView(it.id)}>
              <span className="glyph">{it.glyph}</span>
              <span>{it.label}</span>
              {it.count !== undefined && (
                <span className="count" style={it.countTone==='bad'?{color:'var(--c-bad)'}:{}}>
                  {it.count}
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
      <div style={{flex:1}}/>
      <div className="side-section" style={{marginTop:'auto',borderTop:'1px solid var(--c-line)',paddingTop:12}}>
        <div style={{display:'flex',alignItems:'center',gap:8,fontSize:'var(--t-xs)',color:'var(--c-ink-55)'}}>
          <span style={{display:'inline-block',width:8,height:8,borderRadius:4,background:'var(--c-ok)'}}/>
          <span>champion_v3 · running</span>
        </div>
        <div style={{fontSize:'var(--t-xs)',color:'var(--c-ink-40)',marginTop:4}}>
          管线 16/16 · 上次 06:12
        </div>
      </div>
    </aside>
  );
}

function TopBar() {
  return (
    <header className="app-top">
      <div className="app-brand">
        <span className="mark">CM</span>
        <span>Chunky Monkey</span>
        <span className="muted-2" style={{fontWeight:400,fontSize:'var(--t-xs)',marginLeft:6}}>v3.7.0</span>
      </div>
      <div className="app-spacer"/>
      <div className="app-search">
        <span style={{color:'var(--c-ink-40)'}}>⌕</span>
        <input placeholder="搜索表 / 机构 / 股票 / 命令…"/>
        <span className="kbd">⌘</span><span className="kbd">K</span>
      </div>
      <button className="btn btn-ghost" title="通知"><span style={{fontSize:14}}>◔</span></button>
      <button className="btn btn-ghost" title="帮助"><span style={{fontSize:14,fontWeight:600}}>?</span></button>
      <div style={{width:24,height:24,borderRadius:12,background:'var(--c-ink-100)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontSize:11,fontWeight:600}}>DP</div>
    </header>
  );
}

// =================== 主体 — 数据健康页 =================================
function HealthPage({ onOpenAsset }) {
  const [layer, setLayer] = useState('all');
  const [severity, setSeverity] = useState('all');
  const [q, setQ] = useState('');

  const filtered = useMemo(() => {
    return ASSET_ROWS.filter(r => {
      if (layer !== 'all' && r[1] !== layer) return false;
      if (severity !== 'all' && r[5] !== severity) return false;
      if (q && !r[0].toLowerCase().includes(q.toLowerCase()) && !r[2].toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
  }, [layer, severity, q]);

  const counts = useMemo(() => {
    const by = { green:0, yellow:0, red:0 };
    ASSET_ROWS.forEach(r => by[r[5]]++);
    return by;
  }, []);

  return (
    <div>
      <div className="crumbs">
        <span>后台</span><span className="sep">›</span>
        <span>数据链路</span><span className="sep">›</span>
        <span className="here">数据健康</span>
      </div>

      <div className="page-head">
        <div>
          <h1>数据健康</h1>
          <p>47 个数据资产、4 个外部源的新鲜度、SLA 与 writer 状态。red 7 处需关注。</p>
        </div>
        <div className="page-actions">
          <button className="btn"><span style={{fontSize:11}}>↻</span> 刷新快照</button>
          <button className="btn"><span style={{fontSize:11}}>⛶</span> 导出 CSV</button>
          <button className="btn btn-primary"><span style={{fontSize:11}}>▶</span> 立即重跑</button>
        </div>
      </div>

      {/* KPI 行 */}
      <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:12,marginBottom:20}}>
        <KPI label="alive" value={counts.green} sub="正常采集中" tone="ok" big spark="up"/>
        <KPI label="stale" value={counts.yellow} sub="超过 SLA 但仍有数据" tone="warn"/>
        <KPI label="broken" value={counts.red} sub="超过 2× SLA" tone="bad"/>
        <KPI label="fallback active" value="2" sub="tier 2/3 顶替主源" tone="muted"/>
      </div>

      {/* 数据源带 */}
      <div className="cm-card" style={{padding:'14px 16px',marginBottom:20}}>
        <div className="cm-section-h">
          <div>
            <h3>外部数据源</h3>
            <span className="desc">tdxhub > 妙想 > akshare 优先级；manual 仅供标签 / 黑名单</span>
          </div>
          <button className="btn btn-sm btn-ghost">查看 capability →</button>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10}}>
          {SOURCES.map(s => <SourceCard key={s.id} s={s}/>)}
        </div>
      </div>

      {/* 资产表 + 工具条 */}
      <div className="cm-card" style={{overflow:'hidden'}}>
        <div style={{padding:'10px 12px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>
          <Seg value={layer} onChange={setLayer} options={[
            {v:'all',label:'全部',count:ASSET_ROWS.length},
            {v:'raw',label:'raw',count:ASSET_ROWS.filter(r=>r[1]==='raw').length},
            {v:'fact',label:'fact',count:ASSET_ROWS.filter(r=>r[1]==='fact').length},
            {v:'mart',label:'mart',count:ASSET_ROWS.filter(r=>r[1]==='mart').length},
            {v:'sys',label:'sys',count:ASSET_ROWS.filter(r=>r[1]==='sys').length},
          ]}/>
          <div className="app-search" style={{width:240}}>
            <span style={{color:'var(--c-ink-40)'}}>⌕</span>
            <input value={q} onChange={e=>setQ(e.target.value)} placeholder="搜表名 / writer…"/>
          </div>
          <select className="input" value={severity} onChange={e=>setSeverity(e.target.value)}>
            <option value="all">所有严重度</option>
            <option value="green">green</option>
            <option value="yellow">yellow</option>
            <option value="red">red</option>
          </select>
          <div style={{flex:1}}/>
          <span className="muted" style={{fontSize:11}}>{filtered.length} / {ASSET_ROWS.length}</span>
        </div>
        <div style={{maxHeight:480,overflow:'auto'}}>
          <table className="cm-table">
            <thead>
              <tr>
                <th style={{width:32}}/>
                <th>资产</th>
                <th style={{width:60}}>层</th>
                <th>来源</th>
                <th className="num" style={{width:120}}>行数</th>
                <th style={{width:108}}>最新</th>
                <th style={{width:140}}>新鲜度 / SLA</th>
                <th>writer</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => {
                const [name, layer, src, rows, latest, sev, age, sla, writer] = r;
                const ageH = parseFloat(age) || 0;
                const slaH = parseFloat(sla) || 24;
                const pct = Math.round(ageH / slaH * 100);
                return (
                  <tr key={name} onClick={() => onOpenAsset(name)} style={{cursor:'pointer'}}>
                    <td><StatusDot tone={sev}/></td>
                    <td className="mono" style={{fontWeight:500,color:'var(--c-ink-100)'}}>{name}</td>
                    <td><span className="tag">{layer}</span></td>
                    <td className="muted">{src}</td>
                    <td className="num">{rows}</td>
                    <td className="mono muted">{latest}</td>
                    <td><FreshnessBar pct={pct}/></td>
                    <td className="mono muted" style={{fontSize:'var(--t-xs)'}}>{writer}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 第二排:派生管线 + 实时日志 */}
      <div style={{display:'grid',gridTemplateColumns:'1.4fr 1fr',gap:14,marginTop:14}}>
        <div className="cm-card" style={{padding:14}}>
          <div className="cm-section-h">
            <div>
              <h3>今日管线 — 16 步</h3>
              <span className="desc">DAG 依赖序、warn 一处:akshare QFII 慢响应</span>
            </div>
            <div style={{display:'flex',gap:6}}>
              <button className="btn btn-sm">数据组</button>
              <button className="btn btn-sm btn-primary">智能更新</button>
            </div>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:6}}>
            {STEPS.map(([id, group, status, dur]) => (
              <div key={id} style={{
                padding:'8px 10px',border:'1px solid var(--c-line)',borderRadius:6,
                background: status==='warn'?'var(--c-warn-bg)': status==='idle'?'var(--c-bg-2)':'var(--c-surface)',
                fontSize:11
              }}>
                <div style={{display:'flex',alignItems:'center',gap:6,marginBottom:2}}>
                  <StatusDot tone={status}/>
                  <span className="mono" style={{fontWeight:500,color:'var(--c-ink-100)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{id}</span>
                </div>
                <div style={{display:'flex',justifyContent:'space-between',color:'var(--c-ink-55)',fontSize:10.5}}>
                  <span>{group}</span><span className="mono">{dur}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="cm-card" style={{padding:14,display:'flex',flexDirection:'column'}}>
          <div className="cm-section-h">
            <div>
              <h3>实时日志</h3>
              <span className="desc">tail -f · 自动滚动</span>
            </div>
            <div style={{display:'flex',gap:6}}>
              <button className="btn btn-sm btn-ghost">复制</button>
              <button className="btn btn-sm btn-ghost">清空</button>
            </div>
          </div>
          <pre style={{
            flex:1, minHeight:200, maxHeight:280, overflow:'auto',
            margin:0, padding:10, fontSize:11, lineHeight:1.65,
            fontFamily:'var(--f-mono)', color:'var(--c-ink-70)',
            background:'var(--c-bg-2)', borderRadius:6,
          }}>
{`[06:12:04] sync_kline_daily          ok  0:42  rows=+1.2M
[06:12:46] sync_orderbook             ok  5:11  rows=+24M
[06:17:57] sync_aif10                  ok  1:08  rows=+86k
[06:19:05] sync_executive              ok  0:18  rows=+412
[06:19:23] sync_capital                ok  0:24  rows=+1.8M
[06:19:47] sync_qfii                  warn 4:02  retry=2 cause=ratelimit
[06:23:49] build_holders_event        ok  0:11  events=+38
[06:24:00] build_executive_trade      ok  0:09  events=+12
[06:24:09] build_lhb_events          warn —    skip cause=stale_input
[06:24:09] build_feature_panel       ok  2:48  features=712 cols
[06:26:57] compute_drift             ok  0:19  flagged=4
[06:27:16] build_universe            ok  0:46  candidates=3218
[06:28:02] run_scoring               ok  0:53  topk=420
[06:28:55] run_daily_topk            ok  0:07  written
[06:29:02] storage_retention         ok  0:03  pruned=12 tables
[06:29:05] ▌`}
          </pre>
        </div>
      </div>

      <div style={{height:24}}/>
    </div>
  );
}

function KPI({label, value, sub, tone='muted', big, spark}) {
  const tones = {
    ok:   {color:'var(--c-ok)',  bg:'var(--c-ok-bg)'},
    warn: {color:'var(--c-warn)',bg:'var(--c-warn-bg)'},
    bad:  {color:'var(--c-bad)', bg:'var(--c-bad-bg)'},
    muted:{color:'var(--c-ink-70)', bg:'var(--c-bg-2)'},
  };
  const t = tones[tone];
  return (
    <div className="cm-card" style={{padding:'14px 16px'}}>
      <div style={{display:'flex',alignItems:'center',gap:6,fontSize:'var(--t-xs)',color:'var(--c-ink-55)',textTransform:'uppercase',letterSpacing:'0.04em',marginBottom:8}}>
        <StatusDot tone={tone==='ok'?'green':tone==='warn'?'yellow':tone==='bad'?'red':'gray'}/>
        <span>{label}</span>
      </div>
      <div style={{display:'flex',alignItems:'baseline',gap:8}}>
        <div className="mono" style={{fontSize:big?'28px':'22px',fontWeight:600,color:'var(--c-ink-100)',letterSpacing:'-0.01em'}}>{value}</div>
        {spark && <div className="spark up"><i style={{height:6}}/><i style={{height:9}}/><i style={{height:7}}/><i style={{height:11}}/><i style={{height:13}}/></div>}
      </div>
      <div style={{fontSize:'var(--t-xs)',color:'var(--c-ink-55)',marginTop:4}}>{sub}</div>
    </div>
  );
}

function SourceCard({ s }) {
  const tone = s.status === 'ok' ? 'green' : s.status === 'warn' ? 'yellow' : 'red';
  return (
    <div style={{
      padding:'10px 12px', border:'1px solid var(--c-line)', borderRadius:8,
      background: 'var(--c-bg-2)',
    }}>
      <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
        <StatusDot tone={tone}/>
        <span className="mono" style={{fontWeight:600,fontSize:'var(--t-md)',color:'var(--c-ink-100)'}}>{s.label}</span>
        <span className="tag" style={{marginLeft:'auto'}}>tier {s.tier}</span>
      </div>
      <div style={{fontSize:'var(--t-xs)',color:'var(--c-ink-55)',marginBottom:8,minHeight:30}}>{s.role}</div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:6,fontSize:10.5}}>
        <Stat k="cap" v={s.coverage+'%'}/>
        <Stat k="rps" v={s.rps}/>
        <Stat k="rows" v={s.rows}/>
      </div>
      <div style={{fontSize:10.5,color:'var(--c-ink-40)',marginTop:8,display:'flex',justifyContent:'space-between'}}>
        <span>{s.freshness}</span>
        <a className="muted" href="#" onClick={e=>e.preventDefault()}>详情 →</a>
      </div>
    </div>
  );
}

function Stat({k,v}) {
  return (
    <div>
      <div style={{color:'var(--c-ink-40)',fontSize:9.5,textTransform:'uppercase',letterSpacing:'0.04em'}}>{k}</div>
      <div className="mono" style={{color:'var(--c-ink-85)',fontWeight:500}}>{v}</div>
    </div>
  );
}

function Seg({value, onChange, options}) {
  return (
    <div style={{display:'inline-flex',background:'var(--c-bg-2)',borderRadius:6,padding:2,gap:1}}>
      {options.map(o => (
        <button key={o.v}
          onClick={() => onChange(o.v)}
          style={{
            height:24,padding:'0 10px',borderRadius:4,fontSize:11,
            background: value===o.v?'var(--c-surface)':'transparent',
            color: value===o.v?'var(--c-ink-100)':'var(--c-ink-55)',
            fontWeight: value===o.v?600:500,
            boxShadow: value===o.v?'var(--sh-1)':'none',
            display:'inline-flex',alignItems:'center',gap:6,
          }}>
          {o.label}
          <span style={{fontSize:10,color:'var(--c-ink-40)',fontFamily:'var(--f-mono)'}}>{o.count}</span>
        </button>
      ))}
    </div>
  );
}

// ========== 抽屉 ===========================================
function AssetDrawer({ asset, onClose }) {
  if (!asset) return null;
  const row = ASSET_ROWS.find(r => r[0] === asset);
  if (!row) return null;
  const [name, layer, src, rows, latest, sev, age, sla, writer] = row;
  return (
    <div style={{position:'absolute',inset:0,zIndex:50,display:'flex',justifyContent:'flex-end'}}>
      <div onClick={onClose} style={{position:'absolute',inset:0,background:'rgba(12,10,9,.32)'}}/>
      <div style={{
        position:'relative', width:520, height:'100%',
        background:'var(--c-surface)', borderLeft:'1px solid var(--c-line)',
        display:'flex',flexDirection:'column',
        animation:'slideIn 200ms ease-out',
      }}>
        <div style={{padding:'14px 18px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'flex-start',gap:8}}>
          <div style={{flex:1}}>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
              <StatusDot tone={sev}/>
              <span className="tag">{layer}</span>
              <span className="muted-2" style={{fontSize:10.5}}>来源 {src}</span>
            </div>
            <div className="mono" style={{fontSize:'var(--t-lg)',fontWeight:600,color:'var(--c-ink-100)'}}>{name}</div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>✕</button>
        </div>

        <div style={{flex:1,overflow:'auto',padding:18}}>
          <div style={{display:'grid',gridTemplateColumns:'repeat(2,1fr)',gap:12,marginBottom:18}}>
            <KV k="行数" v={rows}/>
            <KV k="最新数据" v={latest}/>
            <KV k="新鲜度" v={age}/>
            <KV k="SLA" v={sla}/>
            <KV k="writer" v={writer} mono/>
            <KV k="严重度" v={<span className={`pill pill-${sev==='green'?'ok':sev==='yellow'?'warn':'bad'}`}><span className="pill-dot"/>{sev}</span>}/>
          </div>

          <h4 style={{fontSize:'var(--t-sm)',fontWeight:600,marginBottom:8,color:'var(--c-ink-100)'}}>近 14 天行数趋势</h4>
          <SparkBars/>

          <h4 style={{fontSize:'var(--t-sm)',fontWeight:600,margin:'18px 0 8px',color:'var(--c-ink-100)'}}>上下游</h4>
          <Lineage name={name}/>

          <h4 style={{fontSize:'var(--t-sm)',fontWeight:600,margin:'18px 0 8px',color:'var(--c-ink-100)'}}>最近 5 次运行</h4>
          <table className="cm-table" style={{fontSize:'var(--t-xs)'}}>
            <thead><tr>
              <th>时间</th><th className="num">耗时</th><th className="num">行变化</th><th>状态</th>
            </tr></thead>
            <tbody>
              {[
                ['2026-05-06 06:23','11s','+38','ok'],
                ['2026-05-05 06:23','12s','+42','ok'],
                ['2026-05-04 06:23','9s', '+18','ok'],
                ['2026-05-03 06:23','—',  '0',  'skip'],
                ['2026-05-02 06:23','13s','+51','ok'],
              ].map((r,i) => <tr key={i}>
                <td className="mono muted" style={{fontSize:10.5}}>{r[0]}</td>
                <td className="num mono">{r[1]}</td>
                <td className="num mono">{r[2]}</td>
                <td><span className={`pill ${r[3]==='ok'?'pill-ok':'pill-ghost'}`}><span className="pill-dot"/>{r[3]}</span></td>
              </tr>)}
            </tbody>
          </table>
        </div>

        <div style={{padding:'12px 18px',borderTop:'1px solid var(--c-line)',display:'flex',gap:8}}>
          <button className="btn">查看 SQL</button>
          <button className="btn">查看依赖图</button>
          <div style={{flex:1}}/>
          <button className="btn btn-primary">立即重跑</button>
        </div>
      </div>
    </div>
  );
}

function KV({k,v,mono}){
  return (
    <div>
      <div style={{fontSize:10.5,color:'var(--c-ink-40)',textTransform:'uppercase',letterSpacing:'0.04em',marginBottom:2}}>{k}</div>
      <div className={mono?'mono':''} style={{fontSize:'var(--t-md)',color:'var(--c-ink-100)',fontWeight:500}}>{v}</div>
    </div>
  );
}

function SparkBars(){
  const data = [42,48,40,55,52,61,58,49,53,60,57,62,65,68];
  const max = Math.max(...data);
  return (
    <div style={{display:'flex',alignItems:'flex-end',gap:3,height:60,padding:'0 2px',background:'var(--c-bg-2)',borderRadius:6,padding:8}}>
      {data.map((d,i)=>(
        <div key={i} style={{flex:1,height:`${d/max*100}%`,background:i>=12?'var(--c-accent)':'var(--c-ink-25)',borderRadius:1,minHeight:2}}/>
      ))}
    </div>
  );
}

function Lineage({name}){
  return (
    <div style={{display:'flex',alignItems:'center',gap:10,padding:'10px 12px',background:'var(--c-bg-2)',borderRadius:6,fontSize:11}}>
      <div className="mono" style={{padding:'4px 8px',background:'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:4}}>raw_holders_tdx</div>
      <span style={{color:'var(--c-ink-40)'}}>→</span>
      <div className="mono" style={{padding:'4px 8px',background:'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:4}}>raw_holders_aif10</div>
      <span style={{color:'var(--c-ink-40)'}}>→</span>
      <div className="mono" style={{padding:'4px 8px',background:'var(--c-ink-100)',color:'#fff',borderRadius:4,fontWeight:600}}>{name}</div>
      <span style={{color:'var(--c-ink-40)'}}>→</span>
      <div className="mono" style={{padding:'4px 8px',background:'var(--c-surface)',border:'1px solid var(--c-line)',borderRadius:4}}>mart_institution_score</div>
    </div>
  );
}

// ============== ROOT =====================================
function DirectionA() {
  const [view, setView] = useState('lineage');
  const [openAsset, setOpenAsset] = useState(null);

  return (
    <div className="app" style={{position:'relative'}}>
      <TopBar/>
      <div className="app-body">
        <NavRail activeView={view} setView={setView}/>
        <main className="app-main">
          <HealthPage onOpenAsset={setOpenAsset}/>
        </main>
      </div>
      <AssetDrawer asset={openAsset} onClose={() => setOpenAsset(null)}/>
    </div>
  );
}

window.DirectionA = DirectionA;
