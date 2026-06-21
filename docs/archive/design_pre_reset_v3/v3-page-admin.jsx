/* Chunky Monkey v3 — 模型管理 + 数据管理 + 系统管理 + 机构抽屉 */

const { useState: useStateA, useMemo: useMemoA } = React;

/* ============================================================
   Page 5 · 模型管理 (NEW — 从 v2 数据健康里独立)
   ============================================================ */
function PageModel() {
  const { MODELS, ApiTag, MiniSpark } = window.CMV3;
  const { champion, candidates, rolling30, feature_importance } = MODELS;

  return (
    <div>
      <window.PageHead
        crumbs={[['系统'],['模型管理', true]]}
        title="模型管理"
        sub="champion + challenger 生命周期 — 训练 / OOT / promote-gate / 部署都在这。当前 champion 表现衰减或挑战者通过 gate 时, 这里是核心动作面。"
        api={<ApiTag path="/api/recommendation/model/registry"/>}
        actions={[['训练新挑战者'],['查看 MLflow','primary']]}
      />

      {/* Champion 卡 */}
      <div className="cm-card" style={{padding:16,marginBottom:14}}>
        <div className="cm-section-h">
          <div>
            <h3 style={{fontSize:14}}>当前 Champion · {champion.model_id}</h3>
            <span className="desc">version {champion.model_version} · 部署于 {champion.deployed_at}</span>
          </div>
          <span className="pill pill-accent"><span className="pill-dot"/>healthy</span>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:10,marginTop:12}}>
          <KStat k="OOT Top10 Hit" v={(champion.oot_top10_hit*100).toFixed(1)+'%'} sub="模型选 top10 的准确率" hi tone="up"/>
          <KStat k="OOT Top50 Hit" v={(champion.oot_top50_hit*100).toFixed(1)+'%'} sub="top50 准确率"/>
          <KStat k="校准度 (Brier)" v={champion.oot_calibration.toFixed(2)} sub="1.00 = 完美"/>
          <KStat k="特征数" v={champion.features} sub="见 feature_store"/>
          <KStat k="特征漂移 PSI" v={champion.drift_psi.toFixed(2)} sub={champion.drift_psi<0.1?'稳定 (<0.1)':'漂移 (>=0.1)'} tone={champion.drift_psi<0.1?'up':'down'}/>
        </div>

        <div style={{display:'grid',gridTemplateColumns:'1.4fr 1fr',gap:14,marginTop:14}}>
          <div className="cm-card" style={{padding:12,background:'var(--c-bg)',border:'1px solid var(--c-line)'}}>
            <div className="cm-section-h" style={{marginBottom:8}}>
              <div>
                <h3 style={{fontSize:12}}>滚动 30d 胜率</h3>
                <span className="desc">champion 上线后每日 backtest</span>
              </div>
              <ApiTag path="/api/recommendation/model/champion/rolling"/>
            </div>
            <ModelRollChart data={rolling30}/>
            <div style={{marginTop:8,padding:'8px 10px',background:'var(--c-warn-bg)',borderRadius:4,fontSize:11.5,color:'var(--c-ink-70)',display:'flex',gap:8}}>
              <span style={{color:'var(--c-warn)',fontWeight:600,flexShrink:0}}>⚠</span>
              <span>近 5 日胜率从 65% 下滑到 59%, 接近告警下限 (55%)。建议关注是否需要重训。</span>
            </div>
          </div>

          <div className="cm-card" style={{padding:12,background:'var(--c-bg)',border:'1px solid var(--c-line)'}}>
            <div className="cm-section-h" style={{marginBottom:8}}>
              <div>
                <h3 style={{fontSize:12}}>特征重要度</h3>
                <span className="desc">top 10 of {champion.features}</span>
              </div>
              <ApiTag path="/api/recommendation/model/champion/features"/>
            </div>
            <div style={{display:'flex',flexDirection:'column',gap:4}}>
              {feature_importance.map(([f,imp]) => (
                <div key={f} style={{display:'grid',gridTemplateColumns:'150px 1fr 36px',gap:8,alignItems:'center'}}>
                  <span className="mono" style={{fontSize:10.5,color:'var(--c-ink-85)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{f}</span>
                  <div style={{height:8,background:'var(--c-bg-2)',borderRadius:1,position:'relative'}}>
                    <div style={{position:'absolute',inset:0,width:`${imp/0.2*100}%`,background:'var(--c-accent)',borderRadius:1}}/>
                  </div>
                  <span className="num mono" style={{fontSize:10.5,color:'var(--c-ink-70)'}}>{(imp*100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Challenger 列表 */}
      <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
        <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <div>
            <h3 style={{fontSize:13,fontWeight:600}}>候选挑战者 · {candidates.length}</h3>
            <span className="desc">通过 promote-gate (Δtop10 ≥ +1pp + drift &lt; 0.1 + calibration ≥ 0.9) 后可一键 promote 为 champion</span>
          </div>
          <ApiTag path="/api/recommendation/model/candidates"/>
        </div>
        <table className="cm-table">
          <thead><tr>
            <th>model_id</th>
            <th>训练时间</th>
            <th className="num" style={{width:90}}>OOT top10</th>
            <th className="num" style={{width:90}}>校准</th>
            <th className="num" style={{width:80}}>PSI</th>
            <th className="num" style={{width:120}}>vs champion</th>
            <th style={{width:90}}>gate</th>
            <th style={{width:160}}>动作</th>
          </tr></thead>
          <tbody>
            {candidates.map(c => (
              <tr key={c.model_id}>
                <td>
                  <div style={{fontWeight:500}}>{c.model_id}</div>
                  <div className="muted-2 mono" style={{fontSize:10}}>{c.model_version}</div>
                </td>
                <td className="mono muted" style={{fontSize:11}}>{c.trained_at}</td>
                <td className="num mono" style={{fontWeight:600}}>{(c.oot_top10_hit*100).toFixed(1)}%</td>
                <td className="num mono">{c.oot_calibration.toFixed(2)}</td>
                <td className="num mono" style={{color:c.drift_psi<0.1?'var(--c-accent)':'var(--c-bad)'}}>{c.drift_psi.toFixed(2)}</td>
                <td className={`num mono ${c.delta_vs_champion.startsWith('+')?'up':'down'}`} style={{fontWeight:500}}>{c.delta_vs_champion}</td>
                <td>
                  {c.gate_status === 'pass'  && <span className="pill pill-up"><span className="pill-dot"/>pass</span>}
                  {c.gate_status === 'fail'  && <span className="pill pill-down"><span className="pill-dot"/>fail</span>}
                  {c.gate_status === 'review' && <span className="pill pill-warn"><span className="pill-dot"/>review</span>}
                </td>
                <td>
                  {c.status === 'ready_to_promote' && <button className="btn btn-sm btn-primary">Promote</button>}
                  {c.status === 'awaiting_review'  && <button className="btn btn-sm">Review</button>}
                  {c.status === 'rejected'         && <button className="btn btn-sm btn-ghost" style={{color:'var(--c-ink-55)'}}>已拒绝</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{height:24}}/>
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

function ModelRollChart({data}) {
  const W = 300, H = 80;
  const max = 0.7, min = 0.5;
  const xw = W / (data.length-1);
  const sy = v => H - ((v - min) / (max - min)) * (H-12) - 6;
  const path = data.map((v,i) => `${i?'L':'M'}${i*xw} ${sy(v)}`).join(' ');
  const area = path + ` L ${(data.length-1)*xw} ${H} L 0 ${H} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:80}}>
      <line x1={0} x2={W} y1={sy(0.55)} y2={sy(0.55)} stroke="var(--c-warn)" strokeWidth="1" strokeDasharray="2 3" opacity="0.6"/>
      <text x={W-2} y={sy(0.55)-2} textAnchor="end" fontSize="9" fill="var(--c-warn)" fontFamily="var(--f-mono)">55%</text>
      <path d={area} fill="var(--c-accent)" opacity="0.08"/>
      <path d={path} fill="none" stroke="var(--c-accent)" strokeWidth="1.6" strokeLinejoin="round"/>
      <circle cx={(data.length-1)*xw} cy={sy(data[data.length-1])} r="3" fill="var(--c-accent)" stroke="#fff" strokeWidth="1.5"/>
    </svg>
  );
}

/* ============================================================
   Page 6 · 数据管理 (重命名 + 去除模型部分)
   ============================================================ */
function PageDataMgmt() {
  const { TABLES, PIPELINE, QC_CHECKS, EVENTS_30D, StatusDot, MiniSpark, ApiTag } = window.CMV3;
  return (
    <div>
      <window.PageHead
        crumbs={[['系统'],['数据管理', true]]}
        title="数据管理"
        sub="数据采集 + 数据仓库 + 质量校验 + 事件量。任何一处亮黄都意味着今日推荐需要打折看, 红则建议暂停跟随。"
        api={<ApiTag path="/api/data_health/snapshot"/>}
        actions={[['手动触发批次'],['告警规则'],['查看 Airflow','primary']]}
      />

      {/* 链路 */}
      <div className="cm-card" style={{padding:16,marginBottom:14}}>
        <div className="cm-section-h" style={{marginBottom:14}}>
          <div>
            <h3>数据链路 · 今日批次</h3>
            <span className="desc">ingest → warehouse → feature → recommend → signal</span>
          </div>
          <span className="muted-2 mono" style={{fontSize:11}}>批次 06:14 → 当前 07:12 · 58 min</span>
        </div>
        <Pipeline data={PIPELINE}/>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1.4fr 1fr',gap:14,marginBottom:14}}>
        {/* 表新鲜度 */}
        <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
          <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            <div>
              <h3 style={{fontSize:13,fontWeight:600}}>表新鲜度 · {TABLES.length} 张</h3>
              <span className="desc">绿 = 在 SLA 内, 黄 = 接近, 红 = 超出</span>
            </div>
            <ApiTag path="/api/data_health/tables"/>
          </div>
          <table className="cm-table">
            <thead><tr>
              <th>表名 (table_name)</th>
              <th className="num" style={{width:70}}>行数</th>
              <th style={{width:100}}>更新</th>
              <th className="num" style={{width:80}}>变化</th>
              <th style={{width:140}}>备注</th>
            </tr></thead>
            <tbody>
              {TABLES.map((t,i) => (
                <tr key={i}>
                  <td>
                    <div style={{display:'flex',alignItems:'center',gap:8}}>
                      <StatusDot status={t.freshness_status}/>
                      <span className="mono" style={{fontSize:11.5,fontWeight:500}}>{t.table_name}</span>
                    </div>
                  </td>
                  <td className="num mono" style={{fontSize:11}}>{t.row_count}</td>
                  <td className="mono muted" style={{fontSize:11}}>{t.last_updated}</td>
                  <td className="num mono muted-2" style={{fontSize:11}}>{t.delta_rows}</td>
                  <td className="muted-2" style={{fontSize:11}}>{t.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 右栏: QC + 事件量 */}
        <div style={{display:'flex',flexDirection:'column',gap:14}}>
          <div className="cm-card" style={{padding:14}}>
            <div className="cm-section-h" style={{marginBottom:10}}>
              <div>
                <h3>数据质量校验</h3>
                <span className="desc">每日 06:14 自动跑</span>
              </div>
              <ApiTag path="/api/data_health/qc"/>
            </div>
            <div>
              {QC_CHECKS.map((c,i) => (
                <div key={i} style={{
                  display:'grid',gridTemplateColumns:'1fr 60px 60px',gap:10,
                  padding:'7px 0',borderBottom: i<QC_CHECKS.length-1 ? '1px solid var(--c-line)' : '0',
                  fontSize:12,alignItems:'center',
                }}>
                  <div style={{display:'flex',alignItems:'center',gap:8}}>
                    <StatusDot status={c.level}/>
                    <span>{c.check_name}</span>
                  </div>
                  <span className="num mono muted" style={{fontSize:11}}>{c.expected}</span>
                  <span className="num mono" style={{fontWeight:600,color:c.level==='bad'?'var(--c-bad)':c.level==='warn'?'var(--c-warn)':'var(--c-ink-100)'}}>{c.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="cm-card" style={{padding:14}}>
            <div className="cm-section-h" style={{marginBottom:8}}>
              <div>
                <h3>每日 holder 事件量</h3>
                <span className="desc">最近 30 日, 异常掉零会很危险</span>
              </div>
              <ApiTag path="/api/data_health/events?days=30"/>
            </div>
            <div style={{display:'flex',alignItems:'flex-end',gap:14}}>
              <MiniSpark data={EVENTS_30D} width={260} height={50} tone="var(--c-accent)"/>
              <div>
                <div className="muted-2 mono" style={{fontSize:10}}>今日</div>
                <div className="mono" style={{fontSize:18,fontWeight:600,color:'var(--c-ink-100)'}}>124</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style={{height:24}}/>
    </div>
  );
}

function Pipeline({data}) {
  return (
    <div style={{display:'grid',gridTemplateColumns:`repeat(${data.length}, 1fr)`,gap:0,position:'relative'}}>
      <svg style={{position:'absolute',left:0,right:0,top:32,height:2,width:'100%',pointerEvents:'none'}} viewBox="0 0 100 1" preserveAspectRatio="none">
        <line x1="6" x2="94" y1="0.5" y2="0.5" stroke="var(--c-line-2)" strokeWidth="1.5" strokeDasharray="0.5 0.5"/>
      </svg>
      {data.map(s => {
        const color = s.status==='ok'?'var(--c-accent)':s.status==='warn'?'var(--c-warn)':'var(--c-bad)';
        const bg    = s.status==='ok'?'var(--c-accent-bg)':s.status==='warn'?'var(--c-warn-bg)':'var(--c-bad-bg)';
        return (
          <div key={s.stage} style={{display:'flex',flexDirection:'column',alignItems:'center',gap:6,position:'relative',zIndex:1}}>
            <div style={{
              width:64,height:64,borderRadius:14,background:bg,border:'2px solid '+color,
              display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
            }}>
              <span className="mono" style={{fontSize:18,color:color,lineHeight:1,fontWeight:600}}>{s.tasks}</span>
              <span style={{fontSize:9,color:color,marginTop:2,letterSpacing:'0.05em',textTransform:'uppercase'}}>tasks</span>
            </div>
            <div style={{textAlign:'center'}}>
              <div style={{fontSize:12,fontWeight:600,color:'var(--c-ink-100)'}}>{s.name}</div>
              <div className="muted" style={{fontSize:10.5,marginTop:2}}>{s.detail}</div>
              {s.lag_min > 0 && <div className="mono" style={{fontSize:10,color:color,marginTop:2}}>+{s.lag_min}min 延迟</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================
   Page 7 · 系统管理 (重命名)
   ============================================================ */
function PageSystem() {
  const { ApiTag } = window.CMV3;
  return (
    <div>
      <window.PageHead
        crumbs={[['系统'],['系统管理', true]]}
        title="系统管理"
        sub="用户与权限 / API token / 告警通道 / 任务调度。这里不做数据展示, 只做配置。"
      />

      <div style={{display:'grid',gridTemplateColumns:'repeat(2,1fr)',gap:14}}>
        <ConfigCard
          title="用户与权限"
          desc="3 个角色 (admin / researcher / viewer) · 8 个账号"
          api="/api/system/users"
          actions={[['新建用户','primary'],['编辑角色']]}
          items={[
            ['dp (你)',           'admin',      '所有权限',      'ok'],
            ['analyst-zhao',      'researcher', '读 + 跟随建仓', 'ok'],
            ['viewer-li',         'viewer',     '只读',         'ok'],
            ['service-ml-bot',    'system',     '模型训练 API', 'ok'],
          ]}
        />

        <ConfigCard
          title="API Token"
          desc="供外部回测系统、Notebook 调用"
          api="/api/system/tokens"
          actions={[['生成新 token','primary']]}
          items={[
            ['research-nb',  'researcher', '最后使用 12 min ago', 'ok'],
            ['backtest-job', 'system',     '最后使用 2 hr ago',   'ok'],
            ['external-bi',  'viewer',     '90 天未使用',         'warn'],
          ]}
        />

        <ConfigCard
          title="告警通道"
          desc="数据 / 模型 / 信号 任意分级告警"
          api="/api/system/alert_channels"
          actions={[['新建通道','primary']]}
          items={[
            ['Slack #cm-alerts',    'critical+warn', '上次 06:18 · 北向源失败', 'ok'],
            ['企业微信 群机器人',   'critical',      '上次 04:50 · 信号已下发', 'ok'],
            ['Email dp@xxx.com',    'critical',      '5 日未触发',              'ok'],
          ]}
        />

        <ConfigCard
          title="任务调度"
          desc="Airflow DAG 概览"
          api="/api/system/airflow/dags"
          actions={[['打开 Airflow','primary']]}
          items={[
            ['daily_ingest',         '06:00 daily',  '上次 06:14 · success', 'ok'],
            ['daily_mart_build',     '06:30 daily',  '上次 06:48 · success', 'ok'],
            ['daily_recommendation', '07:00 daily',  '上次 07:08 · 12min 延迟', 'warn'],
            ['weekly_retrain',       'Mon 02:00',    '上周一 · success',     'ok'],
          ]}
        />
      </div>
      <div style={{height:24}}/>
    </div>
  );
}

function ConfigCard({title, desc, api, actions, items}) {
  const { ApiTag, StatusDot } = window.CMV3;
  return (
    <div className="cm-card" style={{padding:14}}>
      <div className="cm-section-h">
        <div>
          <h3>{title}</h3>
          <span className="desc">{desc}</span>
        </div>
        <ApiTag path={api}/>
      </div>
      <div style={{display:'flex',flexDirection:'column',marginTop:8}}>
        {items.map((r,i) => (
          <div key={i} style={{
            display:'grid',gridTemplateColumns:'1fr 100px 1fr',gap:10,
            padding:'8px 0',borderBottom: i<items.length-1 ? '1px solid var(--c-line)' : '0',
            fontSize:12,alignItems:'center',
          }}>
            <div style={{display:'flex',alignItems:'center',gap:8,minWidth:0}}>
              <StatusDot status={r[3]}/>
              <span style={{fontWeight:500,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{r[0]}</span>
            </div>
            <span className="muted-2 mono" style={{fontSize:10.5}}>{r[1]}</span>
            <span className="muted" style={{fontSize:11.5,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{r[2]}</span>
          </div>
        ))}
      </div>
      <div style={{display:'flex',gap:8,marginTop:12}}>
        {actions.map(([l,k],i) => (
          <button key={i} className={`btn btn-sm ${k==='primary'?'btn-primary':''}`}>{l}</button>
        ))}
      </div>
    </div>
  );
}

/* ============================================================
   机构详情抽屉
   ============================================================ */
function InstDrawer({ inst, onClose }) {
  if (!inst) return null;
  const { ApiTag, RolePill, fmtPct, MiniSpark } = window.CMV3;
  const sampleScore = inst.buy_event_count >= 20 ? 'good' : inst.buy_event_count >= 10 ? 'fair' : 'low';
  const sampleLabel = sampleScore === 'good' ? '充足' : sampleScore === 'fair' ? '一般' : '偏少';
  const sampleColor = sampleScore === 'good' ? 'var(--c-accent)' : sampleScore === 'fair' ? 'var(--c-warn)' : 'var(--c-bad)';
  const recentTrades = [
    { date:'2026-04-22', stock:'600519 贵州茅台', delta:'+82 万股', ret:'+12.8%', closed:false },
    { date:'2026-03-15', stock:'300750 宁德时代', delta:'+412 万股', ret:'+18.2%', closed:true },
    { date:'2026-02-08', stock:'600036 招商银行', delta:'+540 万股', ret:'+4.1%',  closed:true },
    { date:'2026-01-22', stock:'002594 比亚迪',   delta:'+220 万股', ret:'+8.4%',  closed:true },
    { date:'2025-12-08', stock:'000333 美的集团', delta:'+102 万股', ret:'-2.1%',  closed:true },
    { date:'2025-11-12', stock:'600276 恒瑞医药', delta:'+88 万股',  ret:'+22.5%', closed:true },
    { date:'2025-10-04', stock:'603259 药明康德', delta:'+60 万股',  ret:'+15.2%', closed:true },
  ];
  const ret60Sample = [12.8,18.2,4.1,8.4,-2.1,22.5,15.2,-4.2,9.6,3.2,-1.4,6.8].slice(0, inst.buy_event_count>10?12:8);

  return (
    <div style={{position:'absolute',inset:0,zIndex:50}}>
      <div onClick={onClose} style={{position:'absolute',inset:0,background:'rgba(12,10,9,.42)'}}/>
      <div style={{position:'absolute',top:0,right:0,bottom:0,width:760,background:'var(--c-surface)',borderLeft:'1px solid var(--c-line)',boxShadow:'-12px 0 32px rgba(0,0,0,0.08)',display:'flex',flexDirection:'column'}}>
        <div style={{padding:'16px 22px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',gap:12}}>
          <div style={{width:40,height:40,borderRadius:10,background:'var(--c-ink-100)',color:'#fff',display:'inline-flex',alignItems:'center',justifyContent:'center',fontWeight:600,fontSize:14}}>
            {inst.alias.slice(0,2)}
          </div>
          <div style={{flex:1,minWidth:0}}>
            <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:2}}>
              <h2 style={{fontSize:17,fontWeight:600}}>{inst.alias}</h2>
              <RolePill role={inst.role}/>
              <span className="pill" style={{height:18,fontSize:10}}>
                <span className="pill-dot" style={{background:sampleColor}}/>
                样本{sampleLabel}
              </span>
            </div>
            <div className="muted mono" style={{fontSize:11}}>{inst.inst_uid} · {inst.inst_name}</div>
          </div>
          <ApiTag path={`/api/institution/${inst.inst_uid}/profile`}/>
          <button className="btn btn-sm btn-ghost" onClick={onClose} style={{fontSize:18,width:32,padding:0}}>×</button>
        </div>

        <div style={{flex:1,overflow:'auto',padding:'18px 22px'}}>
          {/* 关键指标 — 10d/30d/60d/90d/120d 全部展示 */}
          <div style={{display:'grid',gridTemplateColumns:'repeat(6,1fr)',gap:8,marginBottom:14}}>
            <KStat k="历史胜率" v={`${Math.round(inst.win_rate*100)}%`} sub={`${inst.buy_event_count} 次`} hi tone={inst.win_rate>=0.6?'up':inst.win_rate>=0.5?'neutral':'down'}/>
            <KStat k="avg 10d"  v={fmtPct(inst.buy_avg_gain_10d)}  sub="" tone={inst.buy_avg_gain_10d>=0?'up':'down'}/>
            <KStat k="avg 30d"  v={fmtPct(inst.buy_avg_gain_30d)}  sub="" tone={inst.buy_avg_gain_30d>=0?'up':'down'}/>
            <KStat k="avg 60d"  v={fmtPct(inst.buy_avg_gain_60d)}  sub="" hi tone={inst.buy_avg_gain_60d>=0?'up':'down'}/>
            <KStat k="avg 90d"  v={fmtPct(inst.buy_avg_gain_90d)}  sub="" tone={inst.buy_avg_gain_90d>=0?'up':'down'}/>
            <KStat k="avg 120d" v={fmtPct(inst.buy_avg_gain_120d)} sub="" tone={inst.buy_avg_gain_120d>=0?'up':'down'}/>
          </div>

          <div style={{padding:'12px 14px',background:'var(--c-bg-2)',borderRadius:8,marginBottom:14,fontSize:12,color:'var(--c-ink-70)',lineHeight:1.55}}>
            <strong style={{color:'var(--c-ink-100)'}}>模型如何使用该机构: </strong>
            基于 {inst.buy_event_count} 次历史 buy 事件,
            {inst.buy_event_count >= 20 ? '样本充足, ' : '样本偏少, 模型自动降权, '}
            胜率 {Math.round(inst.win_rate*100)}% {inst.win_rate>=0.6?'高于阈值 60%, 计入"高 trust" 池':'未达高 trust 门槛'},
            其参与的股票在评分时获得 <strong className="mono" style={{color:'var(--c-accent-fg)'}}>×{inst.trust_weight.toFixed(2)}</strong> 权重加成。
          </div>

          <div className="cm-card" style={{padding:14,marginBottom:14}}>
            <div className="cm-section-h">
              <div>
                <h3>近 {ret60Sample.length} 次 · 60d 跟随收益分布</h3>
                <span className="desc">每根柱 = 一次 buy 事件后 60 日收益</span>
              </div>
              <ApiTag path={`/api/institution/${inst.inst_uid}/buy_events?holding_period=60d`}/>
            </div>
            <DistChart data={ret60Sample}/>
          </div>

          <div className="cm-card" style={{padding:0,overflow:'hidden'}}>
            <div style={{padding:'12px 14px',borderBottom:'1px solid var(--c-line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <h3 style={{fontSize:13,fontWeight:600}}>近期 buy 事件</h3>
              <ApiTag path={`/api/institution/${inst.inst_uid}/buy_events`}/>
            </div>
            <table className="cm-table">
              <thead><tr>
                <th>日期</th><th>股票</th><th>动作</th>
                <th className="num">60d 收益</th><th>状态</th>
              </tr></thead>
              <tbody>
                {recentTrades.map((r,i) => (
                  <tr key={i}>
                    <td className="mono muted" style={{fontSize:11}}>{r.date}</td>
                    <td style={{fontWeight:500}}>{r.stock}</td>
                    <td className="muted">{r.delta}</td>
                    <td className={`num mono ${parseFloat(r.ret)>=0?'up':'down'}`} style={{fontWeight:600}}>{r.ret}</td>
                    <td>{r.closed ? <span className="pill pill-ghost"><span className="pill-dot"/>已结</span> : <span className="pill pill-warn"><span className="pill-dot"/>未到期</span>}</td>
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
            <text x={x+w/2} y={v>=0 ? y-4 : y+h+11} textAnchor="middle" fontSize="9.5" fill={v>=0?'var(--c-up)':'var(--c-down)'} fontFamily="var(--f-mono)" fontWeight="500">
              {v>=0?'+':''}{v.toFixed(1)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

window.PageModel = PageModel;
window.PageDataMgmt = PageDataMgmt;
window.PageSystem = PageSystem;
window.InstDrawerV3 = InstDrawer;
