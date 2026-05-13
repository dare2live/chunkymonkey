/* Chunky Monkey v3 · 股票视图 (Phase η)
 *
 * 输入: stock_code (URL 参数 or state)
 * 数据源: /api/v3/view/stock/{code}
 * 展示:
 *   1. 股票主画像 (mart_stock_picture_daily)
 *   2. 每公式表现 (mart_stock_formula_optuna best 桶)
 *   3. 今日 T+1 买入推荐 (mart_daily_formula_buys)
 *   4. 当前机构持仓 (fact_top10_holder_period)
 *   5. selection 历史摘要 (mart_stock_selection_summary)
 */

const { useState: useStateS, useEffect: useEffectS } = React;

window.CMV3 = window.CMV3 || {};
window.CMV3.PageStockView = function PageStockView({ initialCode, onOpenInst }) {
  const { UI, STOCKS } = window.CMV3;
  const [code, setCode] = useStateS(initialCode || STOCKS?.[0]?.code || '600519');
  const [data, setData] = useStateS(null);
  const [buySignals, setBuySignals] = useStateS(null);  // Phase η+++++ buy_signal
  const [stageOptimal, setStageOptimal] = useStateS(null);  // Phase η+++++++ ψ.3 stage × formula 矩阵
  const [loading, setLoading] = useStateS(false);
  const [error, setError] = useStateS(null);

  useEffectS(() => {
    if (!code) return;
    setLoading(true); setError(null);
    Promise.all([
      fetch(`/api/v3/view/stock/${code}`).then(r => r.json()),
      fetch(`/api/v3/view/stock/${code}/buy-signals`).then(r => r.json()).catch(() => null),
      fetch(`/api/v3/view/stock/${code}/stage-optimal`).then(r => r.json()).catch(() => null),
    ]).then(([d, bs, so]) => { setData(d); setBuySignals(bs); setStageOptimal(so); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [code]);

  return (
    <div>
      {/* 股票切换器 */}
      <div className="cm-card" style={{padding:'12px 16px',marginBottom:14,display:'flex',gap:12,alignItems:'center'}}>
        <span style={{fontSize:13,color:'var(--ink-3)'}}>股票代码:</span>
        <input value={code} onChange={e => setCode(e.target.value.trim())}
               style={{fontFamily:'var(--f-mono)',fontSize:14,padding:'6px 10px',border:'1px solid var(--line)',borderRadius:5,width:120}}/>
        <span style={{fontSize:11,color:'var(--ink-3)'}}>快速:</span>
        {['600519','000001','300750','688066','601318'].map(c =>
          <button key={c} onClick={()=>setCode(c)}
                  style={{padding:'4px 10px',fontSize:11,fontFamily:'var(--f-mono)',
                          background: code===c ? 'var(--ink-0)' : 'var(--bg-2)',
                          color: code===c ? '#fff' : 'var(--ink-2)',
                          border:'none',borderRadius:4,cursor:'pointer'}}>{c}</button>
        )}
        {data?.picture && <UI.StockNameLink code={code} name={data.picture.stock_archetype || code}/>}
      </div>

      {loading && <div style={{padding:24,textAlign:'center',color:'var(--ink-3)'}}>加载中...</div>}
      {error && <div style={{padding:16,color:'#c4382e',background:'rgba(217,75,75,.05)',borderRadius:6}}>错误: {error}</div>}

      {data && !loading && (
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
          {/* ============ 主画像 ============ */}
          <UI.Card title="主画像" action={<UI.ApiTag>mart_stock_picture_daily</UI.ApiTag>}>
            {data.picture ? (
              <div style={{fontSize:12,lineHeight:1.7}}>
                <div><b>{code}</b> · {data.picture.stock_archetype || '—'} · 最新 {data.picture.snapshot_date}</div>
                <div style={{color:'var(--ink-2)',margin:'6px 0'}}>
                  收盘 <span style={{fontFamily:'var(--f-mono)'}}>¥{data.picture.latest_close?.toFixed(2)}</span>
                  · 涨跌 <span style={{fontFamily:'var(--f-mono)',color: data.picture.chg_pct>=0?'var(--c-up)':'var(--c-down)'}}>{(data.picture.chg_pct*100).toFixed(2)}%</span>
                </div>
                <div>
                  <UI.Pill tone="info">基本面: {data.picture.fundamental_stage} ({data.picture.fundamental_stage_days}d)</UI.Pill>
                  &nbsp;
                  <UI.Pill tone="info">技术面: {data.picture.technical_stage} ({data.picture.technical_stage_days}d)</UI.Pill>
                </div>
                <div style={{marginTop:6}}>
                  类型: <b>{data.picture.primary_type || '—'}</b>
                  &nbsp;· PE: <span style={{fontFamily:'var(--f-mono)'}}>{data.picture.valuation_pe?.toFixed(2) || '—'}</span>
                  &nbsp;· 机构信号: <span style={{fontFamily:'var(--f-mono)'}}>{data.picture.institution_score?.toFixed(2) || 0}</span> ({data.picture.institution_n_insts || 0} 家)
                </div>
              </div>
            ) : <div style={{color:'var(--ink-3)',fontSize:12}}>无画像数据</div>}
          </UI.Card>

          {/* ============ selection 历史摘要 ============ */}
          <UI.Card title="历史优选追踪" action={<UI.ApiTag>mart_stock_selection_summary</UI.ApiTag>}>
            {data.selection_summary ? (
              <div style={{fontSize:12,lineHeight:1.7}}>
                <div>选中次数: <b>{data.selection_summary.n_total}</b> 总 · 近 30d <b>{data.selection_summary.n_30d}</b> · 近 90d <b>{data.selection_summary.n_90d}</b></div>
                <div>胜率: <b style={{color: data.selection_summary.win_rate >= 0.5 ? 'var(--c-up)' : 'var(--c-down)'}}>
                  {data.selection_summary.win_rate != null ? UI.fmt2pct(data.selection_summary.win_rate, false).replace("+","") : '—'}
                </b>
                · 平均收益: <span style={{fontFamily:'var(--f-mono)'}}>
                  {data.selection_summary.avg_ret != null ? UI.fmt2pct(data.selection_summary.avg_ret, false).replace("+","") : '—'}
                </span></div>
                <div>最近选中: {data.selection_summary.last_select_date} · 公式 <b>{data.selection_summary.last_formula}</b> · 结果 <UI.Pill tone={
                  data.selection_summary.last_outcome === 'win' ? 'buy' :
                  data.selection_summary.last_outcome === 'loss' ? 'sell' : 'neutral'
                }>{data.selection_summary.last_outcome}</UI.Pill></div>
              </div>
            ) : <div style={{color:'var(--ink-3)',fontSize:12}}>该股暂无优选历史</div>}
          </UI.Card>

          {/* ============ 公式表现 (跨公式 best 桶) ============ */}
          <UI.Card title={`每公式表现 (${data.formula_performance?.length || 0})`}
                   action={<UI.ApiTag>mart_stock_formula_optuna</UI.ApiTag>}
                   span={2}>
            {data.formula_performance?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>公式</th>
                    <th>持仓</th>
                    <th>上下文</th>
                    <th style={{textAlign:'right'}}>n</th>
                    <th style={{textAlign:'right'}}>胜率</th>
                    <th style={{textAlign:'right'}}>均收益</th>
                    <th style={{textAlign:'right'}}>均DD</th>
                    <th style={{textAlign:'right'}}>Calmar</th>
                  </tr>
                </thead>
                <tbody>
                  {data.formula_performance.slice(0,15).map((f,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)',fontSize:10}}>{f.formula_variant}</td>
                      <td style={{fontFamily:'var(--f-mono)'}}>{f.holding_days}d</td>
                      <td style={{color:'var(--ink-2)',fontSize:10}}>{f.context_bucket}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{f.n_signals}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: f.win_rate>=0.6 ? 'var(--c-up)' : 'var(--ink-2)'}}>{(f.win_rate*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: f.avg_ret>=0 ? 'var(--c-up)' : 'var(--c-down)'}}>{(f.avg_ret*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: 'var(--c-down)'}}>{(f.avg_dd*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{f.calmar?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>该股暂无 per-stock 回测数据 (mart_stock_formula_optuna 桶 n<3)</div>}
          </UI.Card>

          {/* ============ 今日 T+1 推荐 ============ */}
          <UI.Card title={`T+1 买入推荐 (${data.today_buys?.length || 0})`}
                   action={<UI.ApiTag>mart_daily_formula_buys</UI.ApiTag>}
                   span={2}>
            {data.today_buys?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>信号日</th>
                    <th>公式</th>
                    <th>上下文</th>
                    <th style={{textAlign:'right'}}>胜率</th>
                    <th style={{textAlign:'right'}}>预期收益</th>
                    <th style={{textAlign:'right'}}>预期DD</th>
                    <th style={{textAlign:'right'}}>持仓</th>
                    <th style={{textAlign:'right'}}>买入价</th>
                    <th style={{textAlign:'right'}}>目标价</th>
                  </tr>
                </thead>
                <tbody>
                  {data.today_buys.map((b,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)'}}>{b.signal_date}</td>
                      <td style={{fontFamily:'var(--f-mono)',fontSize:10}}>{b.formula}</td>
                      <td style={{color:'var(--ink-2)',fontSize:10}}>{b.context}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{(b.win_rate*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{(b.avg_ret*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{(b.avg_dd*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{b.holding_days}d</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>¥{b.buy_price?.toFixed(2)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>¥{b.sell_target?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>该股今日无公式触发, 或历史置信度不足</div>}
          </UI.Card>

          {/* ============ 当前机构持仓 ============ */}
          <UI.Card title={`当前机构持仓 (top 10)`}
                   action={<UI.ApiTag>fact_top10_holder_period</UI.ApiTag>}
                   span={2}>
            {data.holders?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>排名</th>
                    <th>机构名称</th>
                    <th style={{textAlign:'right'}}>持股比</th>
                    <th style={{textAlign:'right'}}>变动</th>
                    <th>报告期</th>
                  </tr>
                </thead>
                <tbody>
                  {data.holders.slice(0,10).map((h,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)',cursor: onOpenInst ? 'pointer' : 'default'}}
                        onClick={() => onOpenInst && onOpenInst(h.name_norm || h.name)}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)'}}>{h.rank}</td>
                      <td>{h.name}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{h.share_pct?.toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',
                                  color: (h.share_change||0)>=0 ? 'var(--c-up)' : 'var(--c-down)'}}>
                        {h.share_change ? (h.share_change>=0?'+':'') + h.share_change.toFixed(2) : '—'}
                      </td>
                      <td style={{fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{h.report_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>该股暂无机构持仓数据</div>}
          </UI.Card>
        </div>
      )}

      {/* Phase η+++++++ ψ.3 — Stage × Formula 9-dim Optuna 寻优矩阵 */}
      {stageOptimal && stageOptimal.ok && stageOptimal.n_stage_aware > 0 && (
        <UI.Card title={`Stage × Formula 9-dim 寻优矩阵 (η+++++++)`}
                 action={<UI.ApiTag>mart_per_stock_stage_strategy_optimal</UI.ApiTag>}>
          <div style={{display:'flex',gap:14,fontSize:11,marginBottom:10,padding:'6px 12px',
                        background:'var(--bg-2)',borderRadius:5,color:'var(--ink-2)'}}>
            <div>stage-aware tier-1: <b style={{color:'var(--accent)'}}>{stageOptimal.n_stage_aware}</b> 个 stage × 公式组合</div>
            <div>cross-stage fallback: <b>{stageOptimal.n_cross_stage_fallback}</b></div>
            <div style={{marginLeft:'auto',color:'var(--ink-3)',fontSize:10}}>每行 = 该 stage 下该公式的 9 维寻优最优参数</div>
          </div>
          {stageOptimal.best_per_stage?.length > 0 && (
            <div style={{marginBottom:14}}>
              <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:6,letterSpacing:'.04em',textTransform:'uppercase'}}>各 stage 最佳 (按 Sharpe)</div>
              <div style={{display:'grid',gridTemplateColumns:`repeat(${Math.min(5,stageOptimal.best_per_stage.length)},1fr)`,gap:8}}>
                {stageOptimal.best_per_stage.map(b => (
                  <div key={b.stage} style={{padding:'8px 10px',background:'var(--bg-2)',borderRadius:5,borderLeft:'3px solid var(--accent)'}}>
                    <div style={{fontSize:10,color:'var(--ink-3)',letterSpacing:'.04em'}}>STAGE {b.stage}</div>
                    <div style={{fontSize:11,fontWeight:600,marginTop:3}}>{UI.label(b.formula_variant)}</div>
                    <div style={{display:'flex',gap:8,marginTop:4,fontSize:11,fontFamily:'var(--f-mono)'}}>
                      <span>hp={b.params.hp}d</span>
                      <span style={{color: (b.metrics.sharpe||0)>=1 ? 'var(--c-up)' : 'var(--ink-2)'}}>
                        Sh{b.metrics.sharpe?.toFixed(2)}
                      </span>
                      <span style={{color:(b.metrics.win_rate||0)>=0.6?'var(--c-up)':'var(--ink-2)'}}>
                        win{((b.metrics.win_rate||0)*100).toFixed(0)}%
                      </span>
                      <span style={{color:'var(--ink-3)'}}>n={b.metrics.n_traded}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
            <thead>
              <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                <th style={{padding:'6px 4px'}}>stage</th>
                <th>公式</th>
                <th style={{textAlign:'right'}}>hp</th>
                <th style={{textAlign:'right'}}>stop</th>
                <th style={{textAlign:'right'}}>target</th>
                <th style={{textAlign:'right'}}>trail</th>
                <th style={{textAlign:'right'}}>offset</th>
                <th style={{textAlign:'right'}}>n</th>
                <th style={{textAlign:'right'}}>胜率</th>
                <th style={{textAlign:'right'}}>均收益</th>
                <th style={{textAlign:'right'}}>均DD</th>
                <th style={{textAlign:'right'}}>Sharpe</th>
                <th style={{textAlign:'right'}}>Calmar</th>
                <th>退出分布</th>
              </tr>
            </thead>
            <tbody>
              {stageOptimal.stage_aware_matrix.map((r,i) => {
                const ex = r.exit_breakdown;
                return (
                  <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                    <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--ink-1)'}}>{r.stage}</td>
                    <td style={{fontSize:10}}>{UI.label(r.formula_variant)}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.params.hp}d</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{r.params.stop_pct ? `${(r.params.stop_pct*100).toFixed(1)}%` : '—'}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{r.params.target_pct ? `+${(r.params.target_pct*100).toFixed(1)}%` : '—'}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.params.trailing_pct ? `${(r.params.trailing_pct*100).toFixed(1)}%` : '—'}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>T+{r.params.buy_offset}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.metrics.n_traded}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:(r.metrics.win_rate||0)>=0.6?'var(--c-up)':'var(--ink-2)'}}>{((r.metrics.win_rate||0)*100).toFixed(0)}%</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:(r.metrics.avg_ret||0)>=0?'var(--c-up)':'var(--c-down)'}}>{((r.metrics.avg_ret||0)*100).toFixed(1)}%</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{((r.metrics.avg_dd||0)*100).toFixed(1)}%</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:(r.metrics.sharpe||0)>=1?'var(--c-up)':'var(--ink-2)'}}>{r.metrics.sharpe?.toFixed(2) || '—'}</td>
                    <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.metrics.calmar?.toFixed(2) || '—'}</td>
                    <td style={{fontSize:9,color:'var(--ink-3)',fontFamily:'var(--f-mono)'}}>
                      tgt{((ex.target||0)*100).toFixed(0)}/stp{((ex.stop||0)*100).toFixed(0)}/tr{((ex.trailing||0)*100).toFixed(0)}/hp{((ex.hp||0)*100).toFixed(0)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </UI.Card>
      )}

      {/* Phase η+++++ 买点综合判定 KPI + 各公式买点 tier */}
      {buySignals && buySignals.ok && buySignals.signals && buySignals.signals.length > 0 && (
        <UI.Card title={`今日买点综合判定 (${buySignals.signals.length} 个公式触发)`}
                 action={<UI.ApiTag>mart_stock_formula_buy_signal_daily</UI.ApiTag>}>
          {buySignals.kpi && (
            <div style={{display:'flex',gap:18,fontSize:12,marginBottom:12,padding:'8px 12px',
                          background:'var(--bg-2)',borderRadius:5}}>
              <div>最强公式: <b>{UI.label(buySignals.kpi.best_formula)}</b></div>
              <div>tier: <UI.Pill tone={buySignals.kpi.best_tier==='STRONG_BUY'?'buy':
                                          buySignals.kpi.best_tier==='BUY'?'info':'neutral'}>
                {buySignals.kpi.best_tier}</UI.Pill></div>
              <div>score: <b style={{fontFamily:'var(--f-mono)',color:'var(--accent)'}}>{buySignals.kpi.best_score?.toFixed(1)}</b></div>
              <div>历史 Sharpe: <span style={{fontFamily:'var(--f-mono)'}}>{buySignals.kpi.best_sharpe?.toFixed(2) || '—'}</span></div>
              <div>胜率: <span style={{fontFamily:'var(--f-mono)'}}>{buySignals.kpi.best_win_rate ? `${(buySignals.kpi.best_win_rate*100).toFixed(0)}%` : '—'}</span></div>
            </div>
          )}
          <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
            <thead>
              <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                <th style={{padding:'8px 4px'}}>公式</th>
                <th>tier</th>
                <th style={{textAlign:'right'}}>score</th>
                <th style={{textAlign:'right'}}>hp</th>
                <th style={{textAlign:'right'}}>stop</th>
                <th style={{textAlign:'right'}}>target</th>
                <th style={{textAlign:'right'}}>trailing</th>
                <th style={{textAlign:'right'}}>T+N</th>
                <th>理由</th>
              </tr>
            </thead>
            <tbody>
              {buySignals.signals.map(s => (
                <tr key={s.formula_variant} style={{borderTop:'1px solid var(--line-soft)'}}>
                  <td style={{padding:'6px 4px',fontSize:10}}>{UI.label(s.formula_variant)}</td>
                  <td>
                    <UI.Pill tone={s.tier==='STRONG_BUY'?'buy':s.tier==='BUY'?'info':'neutral'}>
                      {s.tier==='STRONG_BUY'?'强买':s.tier==='BUY'?'买入':s.tier==='WATCH'?'观察':'无'}
                    </UI.Pill>
                  </td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600}}>{s.score?.toFixed(1)}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.optimal?.hp || '—'}d</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{s.optimal?.stop_pct ? `${(s.optimal.stop_pct*100).toFixed(1)}%` : '—'}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{s.optimal?.target_pct ? `+${(s.optimal.target_pct*100).toFixed(1)}%` : '—'}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.optimal?.trailing_pct ? `${(s.optimal.trailing_pct*100).toFixed(1)}%` : '—'}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.optimal?.buy_offset || '—'}</td>
                  <td style={{fontSize:10,color:'var(--ink-2)'}}>{s.reasoning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </UI.Card>
      )}
    </div>
  );
};
