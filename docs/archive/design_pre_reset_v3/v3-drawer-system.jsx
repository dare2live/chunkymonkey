/* v3 · 系统抽屉 — 模型 / 机构详情 / 数据健康 (集中在一个 modal)
   也包括独立的 InstDrawer (机构详情)
*/
const { useState: useStateSys } = React;

function SystemDrawer({ onClose, openInst }) {
  const { UI } = window.CMV3;
  const [tab, setTab] = useStateSys('model');

  return (
    <div onClick={onClose} style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.32)',zIndex:100,display:'flex',justifyContent:'flex-end'}}>
      <div onClick={e=>e.stopPropagation()}
           style={{width:'min(1040px, 96vw)',height:'100%',background:'#fff',display:'flex',flexDirection:'column',boxShadow:'-12px 0 32px rgba(0,0,0,0.12)'}}>
        <div style={{padding:'16px 22px',borderBottom:'1px solid var(--line)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <div>
            <div style={{fontSize:18,fontWeight:700,color:'var(--ink-0)'}}>系统管理</div>
            <div style={{fontSize:11,color:'var(--ink-3)',marginTop:2}}>模型生命周期 · 数据健康 · 跑批审计</div>
          </div>
          <button onClick={onClose} style={{padding:'4px 10px',background:'transparent',border:'1px solid var(--line)',borderRadius:5,cursor:'pointer',color:'var(--ink-2)',fontSize:12}}>关闭 Esc</button>
        </div>
        <div style={{padding:'0 22px',borderBottom:'1px solid var(--line)',display:'flex',gap:0}}>
          {[{id:'model',l:'模型管理'},{id:'health',l:'数据健康'}].map(t => (
            <button key={t.id} onClick={()=>setTab(t.id)}
              style={{padding:'10px 14px',fontSize:12,fontWeight:600,background:'transparent',border:'none',cursor:'pointer',
                color: tab===t.id ? 'var(--ink-0)' : 'var(--ink-3)',
                borderBottom: tab===t.id ? '2px solid var(--accent)' : '2px solid transparent',marginBottom:-1}}>{t.l}</button>
          ))}
        </div>
        <div style={{flex:1,overflowY:'auto',padding:'18px 22px',background:'var(--bg-1)'}}>
          {tab==='model'  && <ModelPanel/>}
          {tab==='health' && <HealthPanel/>}
        </div>
      </div>
    </div>
  );
}

/* ============ 模型管理 ============ */
function ModelPanel() {
  const { MODELS, UI } = window.CMV3;
  const c = MODELS.champion;
  return (
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      <UI.Card title="Champion 在用" action={<UI.ApiTag>mart_model_composite_score · mart_model_registry</UI.ApiTag>}>
        <div style={{display:'grid',gridTemplateColumns:'180px 1fr',gap:24,alignItems:'flex-start'}}>
          <div>
            <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)'}}>{c.id}</div>
            <div style={{fontSize:11,color:'var(--ink-3)',marginTop:2}}>v {c.version}</div>
            <div style={{fontSize:11,color:'var(--ink-3)',marginTop:2}}>算法 · {c.algo}</div>
            <div style={{marginTop:8,padding:'4px 8px',background:'rgba(58,140,90,.10)',color:'#2f8a55',border:'1px solid rgba(58,140,90,.30)',borderRadius:4,display:'inline-block',fontSize:11,fontWeight:600}}>● 已上线 {c.days_live}d</div>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:8}}>
            <UI.KStat k="CV IC"        v={c.cv_ic.toFixed(2)}/>
            <UI.KStat k="OOS IC"       v={c.oos_ic.toFixed(2)} tone={c.oos_ic>=0.25?'pos':null}/>
            <UI.KStat k="OOS TopK Hit" v={(c.oos_topk_hit*100).toFixed(0)+'%'} tone="pos"/>
            <UI.KStat k="Max DD"       v={UI.fmtPct(c.max_dd,1)} tone="neg"/>
            <UI.KStat k="Sharpe"       v={c.sharpe.toFixed(2)}/>
          </div>
        </div>
      </UI.Card>

      <UI.Card title={`Challenger · ${MODELS.challengers.length}`} action={<UI.ApiTag>mart_model_challenger</UI.ApiTag>}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
          <thead>
            <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
              <th style={{padding:'8px 6px'}}>ID</th>
              <th style={{padding:'8px 6px'}}>算法变更</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>CV IC</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>OOS IC</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>OOS TopK</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>Max DD</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>vs Champion</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>Gate</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}></th>
            </tr>
          </thead>
          <tbody>
            {MODELS.challengers.map(m => (
              <tr key={m.id} style={{borderTop:'1px solid var(--line-soft)'}}>
                <td style={{padding:'10px 6px',fontFamily:'var(--f-mono)'}}>{m.id}</td>
                <td style={{padding:'10px 6px'}}>{m.algo}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{m.cv_ic.toFixed(2)}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600}}>{m.oos_ic.toFixed(2)}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{(m.oos_topk_hit*100).toFixed(0)}%</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:'#c4382e'}}>{UI.fmtPct(m.max_dd,1)}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:m.diff_pct>=0?'#2f8a55':'#c4382e'}}>{UI.fmtPct(m.diff_pct,1)}</td>
                <td style={{padding:'10px 6px',textAlign:'right'}}>{m.gate_pass ? <UI.Pill tone="hold" size="xs">通过</UI.Pill> : <UI.Pill tone="sell" size="xs">未过</UI.Pill>}</td>
                <td style={{padding:'10px 6px',textAlign:'right'}}>
                  {m.status==='pending_gate' && <button style={{padding:'4px 10px',background:'var(--accent)',color:'#fff',border:'none',borderRadius:4,fontSize:11,fontWeight:600,cursor:'pointer'}}>升级</button>}
                  {m.status==='rejected' && <span style={{fontSize:11,color:'var(--ink-3)'}}>已拒</span>}
                  {m.status==='archived' && <span style={{fontSize:11,color:'var(--ink-3)'}}>归档</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </UI.Card>

      <UI.Card title="特征漂移 (PSI / KS)" action={<UI.ApiTag>mart_feature_drift</UI.ApiTag>}>
        <div style={{display:'flex',gap:18,alignItems:'center'}}>
          <UI.KStat k="KS 统计量" v={MODELS.drift.ks_stat.toFixed(3)} tone={MODELS.drift.ks_pass?'pos':'neg'}/>
          <UI.KStat k="状态" v={MODELS.drift.ks_pass ? '通过' : '失败'} tone={MODELS.drift.ks_pass?'pos':'neg'}/>
          <UI.KStat k="上次检查" v={MODELS.drift.last_check.split(' ')[1]} sub={MODELS.drift.last_check.split(' ')[0]}/>
          <div style={{flex:1,fontSize:11,color:'var(--ink-2)',lineHeight:1.6,paddingLeft:12,borderLeft:'1px solid var(--line)'}}>
            阈值 KS &lt; 0.15。当前 {MODELS.drift.ks_stat.toFixed(3)} 在安全区,特征分布与训练集一致,无需重训。
          </div>
        </div>
      </UI.Card>
    </div>
  );
}

/* ============ 数据健康 ============ */
function HealthPanel() {
  const { HEALTH, UI } = window.CMV3;
  return (
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      {/* 跑批阶段 */}
      <UI.Card title="今日跑批" action={<UI.ApiTag>mart_pipeline_run_manifest</UI.ApiTag>}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:8}}>
          {HEALTH.pipeline_runs.map(r => (
            <div key={r.stage} style={{padding:10,background:'var(--bg-2)',border:`1px solid ${r.status==='warn'?'rgba(170,140,40,.3)':'var(--line-soft)'}`,borderRadius:6}}>
              <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.04em',textTransform:'uppercase'}}>{r.stage}</div>
              <div style={{display:'flex',alignItems:'center',gap:6,marginTop:6}}>
                <span style={{width:8,height:8,borderRadius:8,background:r.status==='ok'?'#2f8a55':r.status==='warn'?'#aa6f12':'#c4382e'}}/>
                <span style={{fontSize:14,fontWeight:700,fontFamily:'var(--f-mono)',color:'var(--ink-0)'}}>{r.duration_s}s</span>
              </div>
              {r.note && <div style={{fontSize:10,color:'#aa6f12',marginTop:4,lineHeight:1.4}}>{r.note}</div>}
            </div>
          ))}
        </div>
      </UI.Card>

      {/* QC 告警 */}
      {HEALTH.qc_alerts.length>0 && (
        <UI.Card title={`QC 告警 · ${HEALTH.qc_alerts.length}`} action={<UI.ApiTag>mart_business_alert</UI.ApiTag>}>
          {HEALTH.qc_alerts.map(a => (
            <div key={a.id} style={{display:'grid',gridTemplateColumns:'1fr auto auto auto',gap:14,alignItems:'center',padding:'10px 12px',background:'rgba(170,140,40,.06)',border:'1px solid rgba(170,140,40,.25)',borderRadius:6}}>
              <div>
                <div style={{fontSize:12,fontWeight:600,color:'var(--ink-1)'}}>{a.table} · {a.metric}</div>
                <div style={{fontSize:10,color:'var(--ink-3)',marginTop:2,fontFamily:'var(--f-mono)'}}>{a.id} · 已触发 {a.age_min}m</div>
              </div>
              <div style={{textAlign:'right'}}>
                <div style={{fontSize:10,color:'var(--ink-3)'}}>当前 / 阈值</div>
                <div style={{fontFamily:'var(--f-mono)',fontSize:12,fontWeight:600}}><span style={{color:'#aa6f12'}}>{(a.value*100).toFixed(2)}%</span> / {(a.threshold*100).toFixed(2)}%</div>
              </div>
              <UI.Pill tone="watch" size="xs">{a.severity}</UI.Pill>
              <button style={{padding:'4px 10px',background:'transparent',border:'1px solid var(--line)',borderRadius:4,fontSize:11,cursor:'pointer'}}>查看</button>
            </div>
          ))}
        </UI.Card>
      )}

      {/* 表新鲜度 */}
      <UI.Card title="数据表新鲜度" action={<UI.ApiTag>dim_table_metadata</UI.ApiTag>}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
          <thead>
            <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
              <th style={{padding:'8px 6px'}}>表名</th>
              <th style={{padding:'8px 6px'}}>最新分区</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>行数</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>日增量</th>
              <th style={{padding:'8px 6px',textAlign:'right'}}>QC</th>
            </tr>
          </thead>
          <tbody>
            {HEALTH.tables.map(t => (
              <tr key={t.name} style={{borderTop:'1px solid var(--line-soft)'}}>
                <td style={{padding:'10px 6px',fontFamily:'var(--f-mono)',color:'var(--ink-1)'}}>{t.name}</td>
                <td style={{padding:'10px 6px',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{t.freshness}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{t.rows.toLocaleString()}</td>
                <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{t.deltaPct==null?'—':UI.fmtPct(t.deltaPct,2)}</td>
                <td style={{padding:'10px 6px',textAlign:'right'}}>
                  {t.qc==='ok' ? <UI.Pill tone="hold" size="xs">OK</UI.Pill> : t.qc==='warn' ? <UI.Pill tone="watch" size="xs">WARN</UI.Pill> : <UI.Pill tone="sell" size="xs">FAIL</UI.Pill>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </UI.Card>
    </div>
  );
}

/* ============ 机构详情 Drawer ============ */
function InstDrawer({ instId, onClose, onOpenStock }) {
  const { INSTITUTIONS, INST_DETAIL, UI } = window.CMV3;
  const inst = INSTITUTIONS.find(i => i.id === instId);
  const detail = INST_DETAIL[instId] || INST_DETAIL.sb105; // fallback for demo
  if (!inst) return null;

  return (
    <div onClick={onClose} style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.32)',zIndex:100,display:'flex',justifyContent:'flex-end'}}>
      <div onClick={e=>e.stopPropagation()} style={{width:'min(820px, 92vw)',height:'100%',background:'#fff',display:'flex',flexDirection:'column',boxShadow:'-12px 0 32px rgba(0,0,0,0.12)'}}>
        <div style={{padding:'16px 22px',borderBottom:'1px solid var(--line)',display:'flex',alignItems:'flex-start',justifyContent:'space-between'}}>
          <div>
            <div style={{display:'flex',alignItems:'center',gap:10}}>
              <div style={{fontSize:20,fontWeight:700,color:'var(--ink-0)'}}>{inst.alias}</div>
              <UI.Pill size="xs">{inst.type}</UI.Pill>
              {inst.tracked && <UI.Pill tone="buy" size="xs">跟踪中</UI.Pill>}
            </div>
            <div style={{fontSize:11,color:'var(--ink-3)',marginTop:4}}>{inst.name}</div>
            <div style={{fontSize:11,color:'var(--ink-2)',marginTop:4,maxWidth:480}}>{detail.bio}</div>
          </div>
          <button onClick={onClose} style={{padding:'4px 10px',background:'transparent',border:'1px solid var(--line)',borderRadius:5,cursor:'pointer',color:'var(--ink-2)',fontSize:12}}>关闭 Esc</button>
        </div>
        <div style={{flex:1,overflowY:'auto',padding:'18px 22px',background:'var(--bg-1)',display:'flex',flexDirection:'column',gap:14}}>
          <UI.Card title="Track Record" dense action={<UI.ApiTag>mart_institution_profile</UI.ApiTag>}>
            <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:8}}>
              <UI.KStat k="30d 胜率" v={(inst.win30*100).toFixed(0)+'%'} tone={inst.win30>=0.6?'pos':null}/>
              <UI.KStat k="60d"      v={(inst.win60*100).toFixed(0)+'%'}/>
              <UI.KStat k="90d"      v={(inst.win90*100).toFixed(0)+'%'}/>
              <UI.KStat k="样本量"   v={detail.sample_n}/>
              <UI.KStat k="稳定度"   v={(inst.stability*100).toFixed(0)} sub="0-100"/>
            </div>
            <div style={{marginTop:12,paddingTop:12,borderTop:'1px solid var(--line-soft)'}}>
              <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:6,letterSpacing:'.04em',textTransform:'uppercase'}}>胜率 × 持有期</div>
              <div style={{display:'flex',alignItems:'flex-end',gap:6,height:60}}>
                {detail.by_horizon.map(p => (
                  <div key={p.h} style={{flex:1,display:'flex',flexDirection:'column',alignItems:'center',gap:4}}>
                    <div style={{fontSize:10,fontFamily:'var(--f-mono)',fontWeight:600,color:p.w>=0.6?'#2f8a55':'var(--ink-0)'}}>{(p.w*100).toFixed(0)}%</div>
                    <div style={{width:'80%',height:p.w*60,background:p.w>=0.6?'#2f8a55':'var(--accent)',borderRadius:'3px 3px 0 0',opacity:0.8}}/>
                    <div style={{fontSize:9,color:'var(--ink-3)',fontFamily:'var(--f-mono)'}}>{p.h}d</div>
                  </div>
                ))}
              </div>
            </div>
          </UI.Card>

          <UI.Card title={`Top 持仓 · ${detail.top_stocks.length}`} action={<UI.ApiTag>mart_institution_stock_contrib</UI.ApiTag>}>
            <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
              <thead>
                <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
                  <th style={{padding:'8px 6px'}}>代码 / 名称</th>
                  <th style={{padding:'8px 6px',textAlign:'right'}}>事件数</th>
                  <th style={{padding:'8px 6px',textAlign:'right'}}>胜率</th>
                  <th style={{padding:'8px 6px',textAlign:'right'}}>对总收益贡献</th>
                </tr>
              </thead>
              <tbody>
                {detail.top_stocks.map(s => (
                  <tr key={s.code} onClick={()=>{onClose();onOpenStock(s.code);}} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
                    <td style={{padding:'10px 6px'}}>
                      <div style={{fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--ink-0)'}}>{s.code}</div>
                      <div style={{fontSize:10,color:'var(--ink-3)'}}>{s.name}</div>
                    </td>
                    <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.n_events}</td>
                    <td style={{padding:'10px 6px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:s.win_rate>=0.65?'#2f8a55':'var(--ink-0)'}}>{(s.win_rate*100).toFixed(0)}%</td>
                    <td style={{padding:'10px 6px',textAlign:'right'}}>
                      <div style={{display:'inline-flex',alignItems:'center',gap:8}}>
                        <UI.ScoreBar value={s.contrib_pct*100} max={20} color="var(--accent)"/>
                        <span style={{fontFamily:'var(--f-mono)',fontWeight:600,fontSize:11}}>{(s.contrib_pct*100).toFixed(1)}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </UI.Card>

          <UI.Card title="近期事件" dense action={<UI.ApiTag>fact_event_buy</UI.ApiTag>}>
            {detail.recent_events.map((e,i) => (
              <div key={i} style={{display:'grid',gridTemplateColumns:'90px 1fr auto auto',gap:12,padding:'8px 0',borderTop:i?'1px solid var(--line-soft)':'none',fontSize:12,alignItems:'center'}}>
                <div style={{fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{e.date}</div>
                <div><span style={{fontFamily:'var(--f-mono)',fontWeight:600}}>{e.code}</span> · {e.name}</div>
                <UI.Pill tone={e.action==='增持'?'buy':'sell'} size="xs">{e.action}</UI.Pill>
                <div style={{fontFamily:'var(--f-mono)',color:'var(--ink-1)'}}>{e.amount}</div>
              </div>
            ))}
          </UI.Card>
        </div>
      </div>
    </div>
  );
}

window.CMV3.SystemDrawer = SystemDrawer;
window.CMV3.InstDrawer   = InstDrawer;
