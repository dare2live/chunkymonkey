/* v2 — 机构抽屉 + 数据健康页 + 主壳 */

const { useState: useStateS, useMemo: useMemoS } = React;

/* ============== 机构详情抽屉 ============== */
function InstDrawer({ inst, onClose }) {
  if (!inst) return null;
  const { PICKS, getInst, MiniSpark } = window.CMV2;
  const sampleScore = inst.events >= 20 ? 'good' : inst.events >= 10 ? 'fair' : 'low';
  const sampleLabel = sampleScore === 'good' ? '充足' : sampleScore === 'fair' ? '一般' : '偏少';
  const sampleColor = sampleScore === 'good' ? 'var(--c-accent)' : sampleScore === 'fair' ? 'var(--c-warn)' : 'var(--c-bad)';

  // 该机构最近 7 次 buy mock (close + 60d ret)
  const recentTrades = [
    { date:'2026-04-22', stock:'600519 贵州茅台', delta:'+82 万股', ret:'+12.8%', closed:false },
    { date:'2026-03-15', stock:'300750 宁德时代', delta:'+412 万股', ret:'+18.2%', closed:true },
    { date:'2026-02-08', stock:'600036 招商银行', delta:'+540 万股', ret:'+4.1%',  closed:true },
    { date:'2026-01-22', stock:'002594 比亚迪',   delta:'+220 万股', ret:'+8.4%',  closed:true },
    { date:'2025-12-08', stock:'000333 美的集团', delta:'+102 万股', ret:'-2.1%',  closed:true },
    { date:'2025-11-12', stock:'600276 恒瑞医药', delta:'+88 万股',  ret:'+22.5%', closed:true },
    { date:'2025-10-04', stock:'603259 药明康德', delta:'+60 万股',  ret:'+15.2%', closed:true },
  ];

  // 60d 收益分布柱状
  const ret60 = [12.8,18.2,4.1,8.4,-2.1,22.5,15.2,-4.2,9.6,3.2,-1.4,6.8].slice(0,inst.events>10?12:8);

  return (
    <div style={{position:'absolute',inset:0,zIndex:50}}>
      <div onClick={onClose} style={{position:'absolute',inset:0,background:'rgba(12,10,9,.42)',animation:'cmFade 0.16s ease-out'}}/>
      <div style={{
        position:'absolute',top:0,right:0,bottom:0,width:760,
        background:'var(--c-surface)',borderLeft:'1px solid var(--c-line)',
        boxShadow:'-12px 0 32px rgba(0,0,0,0.08)',
        display:'flex',flexDirection:'column',
        animation:'cmSlide 0.18s ease-out',
      }}>
        {/* head */}
        <div style={{padding:'16px 22px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',gap:12}}>
          <div style={{width:40,height:40,borderRadius:10,background:'var(--c-ink-100)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontWeight:600,fontSize:14,letterSpacing:'-0.02em'}}>
            {inst.alias.slice(0,2)}
          </div>
          <div style={{flex:1,minWidth:0}}>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:2}}>
              <h2 style={{fontSize:17,fontWeight:600,letterSpacing:'-0.01em'}}>{inst.alias}</h2>
              <window.CMV2.TypePill type={inst.type}/>
              <span className="pill" style={{height:18,fontSize:10}}>
                <span className="pill-dot" style={{background:sampleColor}}/>
                样本{sampleLabel}
              </span>
            </div>
            <div className="muted" style={{fontSize:11.5}}>
              {inst.name} · {inst.events} 个历史 buy 事件 · 模型 trust 权重 {inst.trust.toFixed(2)}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose} style={{fontSize:18,width:32,padding:0}}>×</button>
        </div>

        {/* 主体 */}
        <div style={{flex:1,overflow:'auto',padding:'18px 22px'}}>
          {/* 关键指标 */}
          <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10,marginBottom:18}}>
            <KStat k="历史胜率"  v={`${Math.round(inst.win*100)}%`} sub={`${inst.events} 次买入`} hi tone={inst.win>=0.6?'up':inst.win>=0.5?'neutral':'down'}/>
            <KStat k="平均 60d"  v={`${inst.avg60>=0?'+':''}${(inst.avg60*100).toFixed(1)}%`} sub="跟随收益" hi tone={inst.avg60>=0?'up':'down'}/>
            <KStat k="中位 60d"  v={`${inst.med60>=0?'+':''}${(inst.med60*100).toFixed(1)}%`} sub="抗极值" tone={inst.med60>=0?'up':'down'}/>
            <KStat k="信任度"    v={inst.trust.toFixed(2)} sub="模型权重 0~1"/>
          </div>

          {/* 模型如何使用这家机构 */}
          <div style={{
            padding:'12px 14px',background:'var(--c-bg-2)',borderRadius:8,marginBottom:18,
            display:'flex',gap:14,alignItems:'center',
          }}>
            <div style={{flexShrink:0,width:32,height:32,borderRadius:16,background:'var(--c-accent)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontWeight:600,fontSize:13}}>i</div>
            <div style={{fontSize:12,color:'var(--c-ink-70)',lineHeight:1.55}}>
              <strong style={{color:'var(--c-ink-100)'}}>模型如何看待该机构: </strong>
              基于 {inst.events} 次历史 buy 事件,
              {inst.events >= 20 ? '样本充足, ' : '样本偏少, 模型自动降权, '}
              胜率 {Math.round(inst.win*100)}% {inst.win>=0.6?'高于阈值 60%, 计入"高 trust" 池':'未达高 trust 门槛'},
              其参与的股票在评分时获得 <strong className="mono" style={{color:'var(--c-accent-fg)'}}>×{(inst.trust*1.0).toFixed(2)}</strong> 的权重加成。
            </div>
          </div>

          {/* 60d 收益分布 */}
          <div className="cm-card" style={{padding:14,marginBottom:14}}>
            <div className="cm-section-h">
              <div>
                <h3>近 {ret60.length} 次买入 · 60d 跟随收益分布</h3>
                <span className="desc">每根柱 = 一次 buy 事件后 60 日收益 · 红跌绿涨 (A 股惯例)</span>
              </div>
              <span className="mono" style={{fontSize:11,color:'var(--c-ink-55)'}}>
                胜 {ret60.filter(r=>r>0).length} · 负 {ret60.filter(r=>r<=0).length}
              </span>
            </div>
            <DistChart data={ret60}/>
          </div>

          {/* 事件流 */}
          <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
            <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <h3 style={{fontSize:13,fontWeight:600}}>近期 buy 事件</h3>
              <button className="btn btn-sm">导出全部 {inst.events} 条</button>
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
                {recentTrades.map((r,i) => (
                  <tr key={i}>
                    <td className="mono muted" style={{fontSize:11}}>{r.date}</td>
                    <td style={{fontWeight:500}}>{r.stock}</td>
                    <td className="muted">{r.delta}</td>
                    <td className={`num mono ${parseFloat(r.ret)>=0?'up':'down'}`} style={{fontWeight:600}}>{r.ret}</td>
                    <td>
                      {r.closed
                        ? <span className="pill pill-ghost"><span className="pill-dot"/>已结</span>
                        : <span className="pill pill-warn"><span className="pill-dot"/>未到期</span>}
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
  const W = 700, H = 130;
  const max = Math.max(...data.map(Math.abs), 5);
  const xw = W / data.length;
  const zeroY = H/2 + 6;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:130}}>
      <line x1={0} x2={W} y1={zeroY} y2={zeroY} stroke="var(--c-line-2)" strokeWidth="1"/>
      {data.map((v,i) => {
        const h = Math.abs(v) / max * (H/2 - 12);
        const x = i * xw + xw*0.18;
        const w = xw * 0.64;
        const y = v >= 0 ? zeroY - h : zeroY;
        return (
          <g key={i}>
            <rect x={x} y={y} width={w} height={h} fill={v>=0?'var(--c-up)':'var(--c-down)'} opacity="0.9"/>
            <text x={x+w/2} y={v>=0 ? y-4 : y+h+11}
                  textAnchor="middle" fontSize="9.5"
                  fill={v>=0?'var(--c-up)':'var(--c-down)'} fontFamily="var(--f-mono)" fontWeight="500">
              {v>=0?'+':''}{v.toFixed(1)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ============== 数据健康 页 ============== */

const TABLES = [
  { name:'fact_holders_event',     rows:'2.4M', updated:'8 min ago',  fresh:'ok',   delta:'+124 rows',   note:'ingest 06:14 批次' },
  { name:'fact_block_trade',       rows:'186K', updated:'8 min ago',  fresh:'ok',   delta:'+18 rows',    note:'' },
  { name:'fact_exec_change',       rows:'42K',  updated:'14 min ago', fresh:'ok',   delta:'+4 rows',     note:'' },
  { name:'dim_institution',        rows:'312',  updated:'昨日 23:30', fresh:'ok',   delta:'0',           note:'每日 SCD-2' },
  { name:'mart_holder_position',   rows:'8.2M', updated:'24 min ago', fresh:'warn', delta:'+512 rows',   note:'比预期晚 12 分钟' },
  { name:'champion_v3_score',      rows:'420',  updated:'1 hr ago',   fresh:'ok',   delta:'today rebuilt', note:'每日 04:48' },
  { name:'strategy_signal',        rows:'7',    updated:'1 hr ago',   fresh:'ok',   delta:'今日 7 信号', note:'已下发' },
  { name:'mart_inst_track_record', rows:'312',  updated:'2 hr ago',   fresh:'ok',   delta:'rolling 3y',  note:'' },
  { name:'fact_north_flow',        rows:'18M',  updated:'2 hr ago',   fresh:'bad',  delta:'同步失败',    note:'港交所连接超时, 已 retry 3 次' },
];

const PIPELINE = [
  { id:'ingest',    name:'数据采集',    status:'ok',   lag:'0 min',  detail:'5/5 源完成', tasks:5 },
  { id:'warehouse', name:'数据仓库',    status:'warn', lag:'12 min', detail:'mart 层延迟', tasks:9 },
  { id:'feature',   name:'特征构建',    status:'ok',   lag:'0 min',  detail:'46 个特征 OK', tasks:46 },
  { id:'model',     name:'模型评分',    status:'warn', lag:'0 min',  detail:'回测样本 -8%', tasks:1 },
  { id:'signal',    name:'信号下发',    status:'ok',   lag:'0 min',  detail:'7 信号已推送', tasks:7 },
];

const QC_CHECKS = [
  { name:'空机构 ID',      level:'ok',   value:0,    expect:'= 0', },
  { name:'重复事件 hash',  level:'ok',   value:0,    expect:'= 0', },
  { name:'机构持仓 < 0',   level:'ok',   value:0,    expect:'= 0', },
  { name:'仓位变动 > 50%', level:'warn', value:14,   expect:'< 10' },
  { name:'报告期错位',     level:'ok',   value:2,    expect:'< 5'  },
  { name:'股票代码无效',   level:'ok',   value:0,    expect:'= 0'  },
  { name:'机构未在 dim 表',level:'bad',  value:8,    expect:'= 0'  },
];

function DataHealth() {
  const [tab, setTab] = useStateS('overview');
  return (
    <div>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <span>系统</span><span className="sep">›</span><span className="here">数据健康</span>
          </div>
          <h1>数据健康</h1>
          <p>"信号能不能跟"先看这里。表新鲜度、链路状态、质量校验、模型衰减 — 任何一个亮黄都意味着今天的推荐需要打折看。</p>
        </div>
        <div className="page-actions">
          <button className="btn">手动触发</button>
          <button className="btn">告警规则</button>
          <button className="btn btn-primary">查看 Airflow</button>
        </div>
      </div>

      {/* 链路 */}
      <div className="cm-card" style={{padding:16,marginBottom:14}}>
        <div className="cm-section-h" style={{marginBottom:14}}>
          <div>
            <h3>数据链路 · 今日批次</h3>
            <span className="desc">ingest → warehouse → feature → model → signal</span>
          </div>
          <span className="muted-2 mono" style={{fontSize:11}}>批次开始 06:14 · 当前 07:12 · 持续 58 min</span>
        </div>
        <Pipeline/>
      </div>

      {/* 主体 */}
      <div style={{display:'grid',gridTemplateColumns:'1.4fr 1fr',gap:14}}>
        {/* 表新鲜度 */}
        <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
          <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)'}}>
            <h3 style={{fontSize:13,fontWeight:600}}>表新鲜度 · {TABLES.length} 张</h3>
            <span className="desc">绿 = 在 SLA 内, 黄 = 接近 SLA, 红 = 超出</span>
          </div>
          <table className="cm-table">
            <thead><tr>
              <th>表</th>
              <th className="num">行数</th>
              <th>更新</th>
              <th className="num">变化</th>
              <th>备注</th>
            </tr></thead>
            <tbody>
              {TABLES.map((t,i) => (
                <tr key={i}>
                  <td>
                    <div style={{display:'flex',alignItems:'center',gap:8}}>
                      <window.CMV2.StatusDot status={t.fresh}/>
                      <span className="mono" style={{fontSize:11.5,fontWeight:500}}>{t.name}</span>
                    </div>
                  </td>
                  <td className="num mono">{t.rows}</td>
                  <td className="mono muted" style={{fontSize:11}}>{t.updated}</td>
                  <td className="num mono muted-2" style={{fontSize:11}}>{t.delta}</td>
                  <td className="muted-2" style={{fontSize:11}}>{t.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 右栏: QC + 模型衰减 */}
        <div style={{display:'flex',flexDirection:'column',gap:14}}>
          <div className="cm-card" style={{padding:14}}>
            <div className="cm-section-h" style={{marginBottom:10}}>
              <div>
                <h3>数据质量校验</h3>
                <span className="desc">每日 06:14 自动跑</span>
              </div>
              <span className="pill pill-warn"><span className="pill-dot"/>1 红 1 黄</span>
            </div>
            <div style={{display:'flex',flexDirection:'column',gap:0}}>
              {QC_CHECKS.map((c,i) => (
                <div key={i} style={{
                  display:'grid',gridTemplateColumns:'1fr 60px 60px',gap:10,
                  padding:'7px 0',
                  borderBottom: i<QC_CHECKS.length-1 ? '1px solid var(--c-line)' : '0',
                  fontSize:12,alignItems:'center',
                }}>
                  <div style={{display:'flex',alignItems:'center',gap:8}}>
                    <window.CMV2.StatusDot status={c.level}/>
                    <span>{c.name}</span>
                  </div>
                  <span className="num mono muted" style={{fontSize:11}}>{c.expect}</span>
                  <span className={`num mono ${c.level==='bad'?'down':c.level==='warn'?'':''}`} style={{
                    fontWeight:600,
                    color: c.level==='bad'?'var(--c-bad)':c.level==='warn'?'var(--c-warn)':'var(--c-ink-100)',
                  }}>{c.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="cm-card" style={{padding:14}}>
            <div className="cm-section-h" style={{marginBottom:8}}>
              <div>
                <h3>模型滚动表现</h3>
                <span className="desc">champion_v3 · 30 日滚动胜率</span>
              </div>
              <span className="mono" style={{fontSize:13,fontWeight:600,color:'var(--c-ink-100)'}}>59.0%</span>
            </div>
            <ModelRollChart/>
            <div style={{marginTop:8,padding:'8px 10px',background:'var(--c-warn-bg)',borderRadius:4,fontSize:11.5,color:'var(--c-ink-70)',display:'flex',gap:8}}>
              <span style={{color:'var(--c-warn)',fontWeight:600,flexShrink:0}}>⚠</span>
              <span>近 5 日胜率从 65% 下滑到 59%, 接近告警下限 (55%)。建议关注模型是否需要再训练。</span>
            </div>
          </div>

          <div className="cm-card" style={{padding:14}}>
            <div className="cm-section-h" style={{marginBottom:8}}>
              <div>
                <h3>每日事件量</h3>
                <span className="desc">最近 30 日, 异常掉零会很危险</span>
              </div>
              <span className="mono" style={{fontSize:13,fontWeight:600,color:'var(--c-ink-100)'}}>124 today</span>
            </div>
            <window.CMV2.MiniSpark data={window.CMV2.EVENTS_30D} width={300} height={50} tone="var(--c-accent)"/>
          </div>
        </div>
      </div>

      <div style={{height:24}}/>
    </div>
  );
}

function Pipeline() {
  return (
    <div style={{display:'grid',gridTemplateColumns:`repeat(${PIPELINE.length}, 1fr)`,gap:0,position:'relative'}}>
      {/* 连线 */}
      <svg style={{position:'absolute',left:0,right:0,top:32,height:2,width:'100%',pointerEvents:'none'}} viewBox="0 0 100 1" preserveAspectRatio="none">
        <line x1="6" x2="94" y1="0.5" y2="0.5" stroke="var(--c-line-2)" strokeWidth="1.5" strokeDasharray="0.5 0.5"/>
      </svg>
      {PIPELINE.map((s,i) => {
        const color = s.status==='ok'?'var(--c-accent)':s.status==='warn'?'var(--c-warn)':'var(--c-bad)';
        const bg    = s.status==='ok'?'var(--c-accent-bg)':s.status==='warn'?'var(--c-warn-bg)':'var(--c-bad-bg)';
        return (
          <div key={s.id} style={{display:'flex',flexDirection:'column',alignItems:'center',gap:6,position:'relative',zIndex:1}}>
            <div style={{
              width:64,height:64,borderRadius:14,
              background:bg,border:'2px solid '+color,
              display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
              fontFamily:'var(--f-mono)',fontWeight:600,
            }}>
              <span className="mono" style={{fontSize:18,color:color,lineHeight:1}}>{s.tasks}</span>
              <span style={{fontSize:9,color:color,marginTop:2,letterSpacing:'0.05em',textTransform:'uppercase'}}>tasks</span>
            </div>
            <div style={{textAlign:'center'}}>
              <div style={{fontSize:12,fontWeight:600,color:'var(--c-ink-100)'}}>{s.name}</div>
              <div className="muted" style={{fontSize:10.5,marginTop:2}}>{s.detail}</div>
              {s.lag !== '0 min' && <div className="mono" style={{fontSize:10,color:color,marginTop:2}}>+{s.lag} 延迟</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ModelRollChart() {
  const data = window.CMV2.MODEL_30D;
  const W = 300, H = 60;
  const max = 0.7, min = 0.5;
  const xw = W / (data.length-1);
  const sy = v => H - ((v - min) / (max - min)) * (H-8) - 4;
  const path = data.map((v,i) => `${i?'L':'M'}${i*xw} ${sy(v)}`).join(' ');
  const area = path + ` L ${(data.length-1)*xw} ${H} L 0 ${H} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:60}}>
      {/* threshold 0.55 */}
      <line x1={0} x2={W} y1={sy(0.55)} y2={sy(0.55)} stroke="var(--c-warn)" strokeWidth="1" strokeDasharray="2 3" opacity="0.6"/>
      <text x={W-2} y={sy(0.55)-2} textAnchor="end" fontSize="9" fill="var(--c-warn)" fontFamily="var(--f-mono)">55%</text>
      <path d={area} fill="var(--c-accent)" opacity="0.08"/>
      <path d={path} fill="none" stroke="var(--c-accent)" strokeWidth="1.6" strokeLinejoin="round"/>
      <circle cx={(data.length-1)*xw} cy={sy(data[data.length-1])} r="3" fill="var(--c-accent)" stroke="#fff" strokeWidth="1.5"/>
    </svg>
  );
}

window.InstDrawer = InstDrawer;
window.DataHealth = DataHealth;
