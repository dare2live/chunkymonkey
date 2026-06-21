/* Chunky Monkey v3 · 公式视图 (Phase η)
 *
 * 输入: formula_id (+ optional variant)
 * 数据源: /api/v3/view/formula/{id}
 * 展示:
 *   1. 全局 horizon_evidence (跨 variant × hd 表现)
 *   2. Top 30 高胜率 5 维分桶
 *   3. 最适合该公式的 Top 10 股票
 *   4. 今日触发的股票列表
 */

const { useState: useStateF, useEffect: useEffectF } = React;

window.CMV3 = window.CMV3 || {};
window.CMV3.PageFormulaView = function PageFormulaView({ onOpenStock }) {
  const { UI, FORMULAS } = window.CMV3;
  const [formulaId, setFormulaId] = useStateF(FORMULAS?.[0]?.id || 'macd_golden_cross');
  const [variant, setVariant] = useStateF(null);
  const [data, setData] = useStateF(null);
  const [stageFitness, setStageFitness] = useStateF(null);  // Phase η++++++
  const [loading, setLoading] = useStateF(false);

  useEffectF(() => {
    if (!formulaId) return;
    setLoading(true);
    const v = variant ? `?variant=${variant}` : '';
    const variantPath = variant || formulaId;
    Promise.all([
      fetch(`/api/v3/view/formula/${formulaId}${v}`).then(r => r.json()),
      fetch(`/api/v3/view/formula/${variantPath}/stage-fitness`).then(r => r.json()).catch(() => null),
    ]).then(([d, sf]) => { setData(d); setStageFitness(sf); setLoading(false); })
      .catch(e => { setLoading(false); });
  }, [formulaId, variant]);

  return (
    <div>
      {/* 公式 + variant 选择 */}
      <div className="cm-card" style={{padding:'12px 16px',marginBottom:14,display:'flex',gap:12,alignItems:'center',flexWrap:'wrap'}}>
        <span style={{fontSize:13,color:'var(--ink-3)'}}>公式:</span>
        {(FORMULAS || []).map(f =>
          <button key={f.id} onClick={() => { setFormulaId(f.id); setVariant(null); }}
                  style={{padding:'6px 12px',fontSize:12,
                          background: formulaId===f.id ? 'var(--ink-0)' : 'var(--bg-2)',
                          color: formulaId===f.id ? '#fff' : 'var(--ink-2)',
                          border:'none',borderRadius:5,cursor:'pointer'}}>{f.name}</button>
        )}
        {data?.global_evidence?.length > 0 && (
          <>
            <span style={{fontSize:11,color:'var(--ink-3)',marginLeft:12}}>variant:</span>
            <button onClick={() => setVariant(null)}
                    style={{padding:'4px 8px',fontSize:11,
                            background: !variant ? 'var(--accent)' : 'var(--bg-2)',
                            color: !variant ? '#fff' : 'var(--ink-2)',
                            border:'none',borderRadius:4,cursor:'pointer'}}>全部</button>
            {[...new Set(data.global_evidence.map(e => e.variant))].map(v =>
              <button key={v} onClick={() => setVariant(v)}
                      style={{padding:'4px 8px',fontSize:11,
                              background: variant===v ? 'var(--accent)' : 'var(--bg-2)',
                              color: variant===v ? '#fff' : 'var(--ink-2)',
                              border:'none',borderRadius:4,cursor:'pointer'}}>{v.replace(formulaId+'_','')}</button>
            )}
          </>
        )}
      </div>

      {loading && <div style={{padding:24,textAlign:'center',color:'var(--ink-3)'}}>加载中...</div>}

      {data && !loading && (
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
          {/* ============ 全局 horizon_evidence ============ */}
          <UI.Card title={`全局回测证据 (跨 variant × hd, ${data.global_evidence?.length || 0} 行)`}
                   action={<UI.ApiTag>mart_formula_horizon_evidence</UI.ApiTag>}
                   span={2}>
            {data.global_evidence?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>variant</th>
                    <th>持仓</th>
                    <th style={{textAlign:'right'}}>信号数</th>
                    <th style={{textAlign:'right'}}>已到期</th>
                    <th style={{textAlign:'right'}}>胜率</th>
                    <th style={{textAlign:'right'}}>均收益</th>
                    <th style={{textAlign:'right'}}>均DD</th>
                    <th style={{textAlign:'right'}}>Sharpe</th>
                  </tr>
                </thead>
                <tbody>
                  {data.global_evidence.map((e,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)',fontSize:10}}>{e.variant?.replace(formulaId+'_','')}</td>
                      <td style={{fontFamily:'var(--f-mono)'}}>{e.holding_days}d</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{e.n_signals?.toLocaleString()}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{e.n_matured?.toLocaleString()}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: e.win_rate>=0.5?'var(--c-up)':'var(--ink-2)'}}>{(e.win_rate*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: e.avg_ret>=0?'var(--c-up)':'var(--c-down)'}}>{(e.avg_ret*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{(e.avg_dd*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{e.sharpe?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>无 horizon_evidence 数据</div>}
          </UI.Card>

          {/* ============ Top 30 高胜率 5 维分桶 ============ */}
          <UI.Card title={`Top 30 高胜率 5 维分桶 (${data.top_buckets?.length || 0})`}
                   action={<UI.ApiTag>mart_stock_formula_optuna</UI.ApiTag>}
                   span={2}>
            {data.top_buckets?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>variant</th>
                    <th>持仓</th>
                    <th>5 维上下文</th>
                    <th style={{textAlign:'right'}}>股数</th>
                    <th style={{textAlign:'right'}}>均胜率</th>
                    <th style={{textAlign:'right'}}>均收益</th>
                    <th style={{textAlign:'right'}}>均DD</th>
                    <th style={{textAlign:'right'}}>均Calmar</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_buckets.map((b,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)',fontSize:10}}>{b.variant?.replace(formulaId+'_','')}</td>
                      <td style={{fontFamily:'var(--f-mono)'}}>{b.holding_days}d</td>
                      <td style={{color:'var(--ink-2)',fontSize:10}}>{b.vol_bin}·{b.amt_bin}·{b.price_pos_bin}·阶段{b.stage_bin}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{b.n_stocks}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: b.avg_win_rate>=0.6?'var(--c-up)':'var(--ink-2)'}}>{(b.avg_win_rate*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color: b.avg_ret>=0?'var(--c-up)':'var(--c-down)'}}>{(b.avg_ret*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{(b.avg_dd*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{b.avg_calmar?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>无分桶数据 (需 ≥3 股满足 high_conviction)</div>}
          </UI.Card>

          {/* ============ 最适合该公式的 Top 10 股票 ============ */}
          <UI.Card title={`最适合该公式的 Top 10 股票`}
                   action={<UI.ApiTag>per-stock optuna (calmar desc)</UI.ApiTag>}
                   span={2}>
            {data.top_stocks?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>股票</th>
                    <th>variant</th>
                    <th>持仓</th>
                    <th style={{textAlign:'right'}}>n</th>
                    <th style={{textAlign:'right'}}>胜率</th>
                    <th style={{textAlign:'right'}}>均收益</th>
                    <th style={{textAlign:'right'}}>Calmar</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_stocks.map((s,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}
                        onClick={() => onOpenStock && onOpenStock(s.stock_code)}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)',fontWeight:600}}>
                        <UI.StockNameLink code={s.stock_code} name={s.stock_code}/>
                      </td>
                      <td style={{fontFamily:'var(--f-mono)',fontSize:10}}>{s.variant?.replace(formulaId+'_','')}</td>
                      <td style={{fontFamily:'var(--f-mono)'}}>{s.holding_days}d</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.n_signals}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{(s.win_rate*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{(s.avg_ret*100).toFixed(2)}%</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.calmar?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>暂无 high-conviction 股票</div>}
          </UI.Card>

          {/* ============ 今日触发的股票 ============ */}
          <UI.Card title={`今日触发 (${data.today_triggers?.length || 0})`}
                   action={<UI.ApiTag>fact_technical_trigger.latest</UI.ApiTag>}
                   span={2}>
            {data.today_triggers?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'6px 4px'}}>股票</th>
                    <th>variant</th>
                    <th style={{textAlign:'right'}}>strength</th>
                    <th>信号日</th>
                  </tr>
                </thead>
                <tbody>
                  {data.today_triggers.slice(0,30).map((t,i) => (
                    <tr key={i} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}
                        onClick={() => onOpenStock && onOpenStock(t.stock_code)}>
                      <td style={{padding:'6px 4px',fontFamily:'var(--f-mono)',fontWeight:600}}>
                        <UI.StockNameLink code={t.stock_code} name={t.stock_code}/>
                      </td>
                      <td style={{fontFamily:'var(--f-mono)',fontSize:10}}>{t.variant?.replace(formulaId+'_','')}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{t.strength?.toFixed(3)}</td>
                      <td style={{fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{t.signal_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div style={{color:'var(--ink-3)',fontSize:12,padding:8}}>今日无触发</div>}
          </UI.Card>
        </div>
      )}

      {/* Phase η++++++ stage 适配矩阵 (fund × tech) — 数据驱动 best hp 偏好 */}
      {stageFitness && stageFitness.ok && stageFitness.best_per_stage_combo && stageFitness.best_per_stage_combo.length > 0 && (
        <UI.Card title={`形态适配矩阵 — fund_stage × tech_stage (best hp per 组合)`}
                 action={<UI.ApiTag>mart_stage_formula_fitness</UI.ApiTag>}>
          <div style={{fontSize:11,color:'var(--ink-3)',marginBottom:8}}>
            数据驱动每个 (基本面阶段 × 技术阶段) 组合下最佳 hp + 实测 Sharpe/Calmar.
            <UI.Pill tone="info" style={{marginLeft:6}}>共 {stageFitness.total_combos} 桶</UI.Pill>
            <UI.Pill tone="buy" style={{marginLeft:6}}>推荐 {stageFitness.recommended_combos}</UI.Pill>
          </div>
          <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
            <thead>
              <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                <th style={{padding:'8px 4px'}}>基本面阶段</th>
                <th>技术阶段</th>
                <th style={{textAlign:'right'}}>best hp</th>
                <th style={{textAlign:'right'}}>n</th>
                <th style={{textAlign:'right'}}>win</th>
                <th style={{textAlign:'right'}}>ret</th>
                <th style={{textAlign:'right'}}>dd</th>
                <th style={{textAlign:'right'}}>Sharpe</th>
                <th style={{textAlign:'right'}}>Calmar</th>
                <th>推荐</th>
              </tr>
            </thead>
            <tbody>
              {stageFitness.best_per_stage_combo.sort((a,b)=>(b.sharpe||0)-(a.sharpe||0)).map((r,i) => (
                <tr key={i} style={{borderTop:'1px solid var(--line-soft)'}}>
                  <td style={{padding:'6px 4px'}}>{r.fundamental_stage}</td>
                  <td>阶段{r.technical_stage}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.holding_days}d</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.n_signals}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.win_rate ? `${(r.win_rate*100).toFixed(0)}%` : '—'}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:r.avg_ret>0?'var(--c-up)':'var(--c-down)'}}>{r.avg_ret ? `${(r.avg_ret*100).toFixed(1)}%` : '—'}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{r.avg_dd ? `${(r.avg_dd*100).toFixed(1)}%` : '—'}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:r.sharpe>0.5?'var(--accent)':undefined}}>{r.sharpe?.toFixed(2)}</td>
                  <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.calmar?.toFixed(2)}</td>
                  <td>{r.is_recommended ? <UI.Pill tone="buy">推</UI.Pill> : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </UI.Card>
      )}
    </div>
  );
};
