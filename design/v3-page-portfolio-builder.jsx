/* Chunky Monkey v3 · 组合构建器 (Phase η++)
 *
 * 3 个 risk profile (短/中/长), 每个 tab 显示当日 T+1 推荐列表 + 仓位 + 卖出规则
 * 数据源:
 *   - /api/v3/portfolio/profiles      → 参数表
 *   - /api/v3/portfolio/recommendations?profile=...  → 推荐列表
 */

const { useState: useStateB, useEffect: useEffectB } = React;

window.CMV3 = window.CMV3 || {};
window.CMV3.PagePortfolioBuilder = function PagePortfolioBuilder({ onOpenStock }) {
  const { UI } = window.CMV3;
  const [profile, setProfile] = useStateB('mid');
  const [profilesMeta, setProfilesMeta] = useStateB([]);
  const [data, setData] = useStateB(null);
  const [loading, setLoading] = useStateB(false);
  const [buySignals, setBuySignals] = useStateB(null);  // Phase η+++++ 形态识别

  useEffectB(() => {
    fetch('/api/v3/portfolio/profiles').then(r => r.json()).then(d => {
      if (d.ok) setProfilesMeta(d.data || []);
    });
    // Phase η+++++: 拉买点判定 (STRONG_BUY + BUY)
    fetch('/api/v3/portfolio/buy-signals?limit=50').then(r => r.json()).then(d => {
      if (d.ok) setBuySignals(d);
    });
  }, []);

  useEffectB(() => {
    setLoading(true);
    fetch(`/api/v3/portfolio/recommendations?profile=${profile}&limit=20`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [profile]);

  const meta = profilesMeta.find(p => p.profile_id === profile);

  return (
    <div>
      {/* 3 profile tab + meta */}
      <div className="cm-card" style={{padding:'12px 16px',marginBottom:14,display:'flex',gap:12,alignItems:'center',flexWrap:'wrap'}}>
        <span style={{fontSize:13,color:'var(--ink-3)'}}>风险偏好:</span>
        {profilesMeta.map(p => (
          <button key={p.profile_id} onClick={() => setProfile(p.profile_id)}
                  style={{padding:'6px 14px',fontSize:13,fontWeight:600,
                          background: profile===p.profile_id ? 'var(--ink-0)' : 'var(--bg-2)',
                          color: profile===p.profile_id ? '#fff' : 'var(--ink-2)',
                          border:'none',borderRadius:6,cursor:'pointer'}}>
            {p.label}
          </button>
        ))}
        {meta && (
          <span style={{fontSize:11,color:'var(--ink-3)',marginLeft:'auto',display:'flex',gap:14}}>
            <span>持仓数 ≤ <b>{meta.max_positions}</b></span>
            <span>单股 ≤ {UI.fmt2pct(meta.stock_cap_pct, false)}</span>
            <span>Kelly × {UI.fmt2(meta.kelly_fraction)}</span>
            <span>Wilson ≥ {UI.fmt2pct(meta.min_wilson_win, false)}</span>
            <span>n ≥ {meta.min_n_signals}</span>
          </span>
        )}
      </div>

      {loading && <div style={{padding:24,textAlign:'center',color:'var(--ink-3)'}}>加载中...</div>}

      {data && !loading && (
        <>
          {/* 总仓位汇总 */}
          <UI.Card title={`今日 T+1 推荐 (${data.total_positions} 只)`}
                   action={<UI.ApiTag>mart_daily_position_recommendation</UI.ApiTag>}>
            <div style={{display:'flex',gap:18,fontSize:12,marginBottom:10}}>
              <div>信号日: <b style={{fontFamily:'var(--f-mono)'}}>{data.signal_date || '—'}</b></div>
              <div>买入日: <b style={{fontFamily:'var(--f-mono)'}}>{data.data?.[0]?.buy_date || '—'}</b></div>
              <div>总仓位: <b style={{fontFamily:'var(--f-mono)',color:'var(--accent)'}}>{UI.fmt2pct(data.total_position_pct, false)}</b></div>
              <div>现金占比: <b style={{fontFamily:'var(--f-mono)'}}>{UI.fmt2pct(data.cash_pct, false)}</b></div>
            </div>

            {data.data?.length ? (
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'8px 4px'}}>#</th>
                    <th>股票</th>
                    <th>公式 · 阶段</th>
                    <th>匹配</th>
                    <th>调研</th>
                    <th style={{textAlign:'right'}}>n</th>
                    <th style={{textAlign:'right'}}>胜率</th>
                    <th style={{textAlign:'right'}}>Wilson</th>
                    <th style={{textAlign:'right'}}>预期收益</th>
                    <th style={{textAlign:'right'}}>预期DD</th>
                    <th style={{textAlign:'right'}}>持仓</th>
                    <th style={{textAlign:'right'}}>仓位</th>
                    <th style={{textAlign:'right'}}>买入价</th>
                    <th style={{textAlign:'right'}}>目标价</th>
                    <th style={{textAlign:'right'}}>止损价</th>
                    <th>tier</th>
                  </tr>
                </thead>
                <tbody>
                  {data.data.map(r => (
                    <tr key={r.rank}
                        onClick={() => onOpenStock && onOpenStock(r.stock_code)}
                        style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
                      <td style={{padding:'8px 4px',fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{r.rank}</td>
                      <td><UI.StockTag code={r.stock_code} size="sm"/></td>
                      <td style={{fontSize:10,color:'var(--ink-2)'}}>
                        <div>{UI.label(r.formula_variant)}</div>
                        <div style={{color:'var(--ink-3)'}}>{r.vol_bin}·{r.amt_bin}·{r.price_pos_bin}·阶段{r.stage_bin}</div>
                      </td>
                      <td>
                        <UI.Pill tone={r.match_tier==='A_bucket'?'buy':'neutral'}
                                 title={r.match_tier==='A_bucket'?'5维桶精确匹配 (n≥10)':'跨桶聚合 (per-stock × variant × hp)'}>
                          {r.match_tier==='A_bucket'?'桶':'聚'}
                        </UI.Pill>
                      </td>
                      <td>
                        {r.survey_bin && r.survey_bin !== '冷' ? (
                          <UI.Pill tone={r.survey_bin==='狂'?'buy':r.survey_bin==='热'?'info':'neutral'}
                                   title={`60日调研 ${r.survey_count_60d||0}次, score 乘子 ×${(r.sentiment_mult||1).toFixed(2)}`}>
                            {r.survey_bin}{r.sentiment_mult>1?` ×${r.sentiment_mult.toFixed(2)}`:''}
                          </UI.Pill>
                        ) : (
                          <span style={{color:'var(--ink-3)',fontSize:10}}>—</span>
                        )}
                      </td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.n_signals}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{UI.fmt2pct(r.raw_win_rate, false)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--ink-2)'}}>{UI.fmt2pct(r.wilson_win_rate, false)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{UI.fmt2pct(r.avg_ret)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{UI.fmt2pct(r.avg_dd)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.holding_days}d</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--accent)'}}>{UI.fmt2pct(r.position_pct, false)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{UI.fmtMoney(r.buy_price)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>{UI.fmtMoney(r.sell_target_price)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>{UI.fmtMoney(r.stop_price)}</td>
                      <td><UI.Pill tone={r.confidence_tier===1?'buy':r.confidence_tier===2?'info':'neutral'}>T{r.confidence_tier}</UI.Pill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div style={{padding:24,textAlign:'center',color:'var(--ink-3)',fontSize:12}}>
                该 profile 今日无符合阈值的推荐 (尝试切换其他 profile)
              </div>
            )}
          </UI.Card>

          {/* Phase η+++++: 形态识别买点判定 全表展示 */}
          {buySignals && buySignals.data && (
            <UI.Card title={`形态扫描 — 全市场买点判定 (${buySignals.signal_date})`}
                     action={<UI.ApiTag>mart_stock_formula_buy_signal_daily</UI.ApiTag>}>
              <div style={{display:'flex',gap:14,fontSize:11,marginBottom:10,color:'var(--ink-2)'}}>
                {buySignals.tier_distribution && Object.entries(buySignals.tier_distribution).map(([t, n]) => (
                  <div key={t}>
                    <UI.Pill tone={t==='STRONG_BUY'?'buy':t==='BUY'?'info':'neutral'}>
                      {t}
                    </UI.Pill>
                    <span style={{marginLeft:6,fontFamily:'var(--f-mono)'}}>{n}</span>
                  </div>
                ))}
              </div>
              <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                <thead>
                  <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                    <th style={{padding:'8px 4px'}}>tier</th>
                    <th>股票</th>
                    <th>公式</th>
                    <th style={{textAlign:'right'}}>score</th>
                    <th style={{textAlign:'right'}}>Sharpe</th>
                    <th style={{textAlign:'right'}}>胜率</th>
                    <th style={{textAlign:'right'}}>hp</th>
                    <th>理由</th>
                  </tr>
                </thead>
                <tbody>
                  {buySignals.data.filter(s => s.tier !== 'NO_SIGNAL').slice(0, 20).map(s => (
                    <tr key={`${s.stock_code}-${s.formula_variant}`}
                        onClick={() => onOpenStock && onOpenStock(s.stock_code)}
                        style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}>
                      <td style={{padding:'8px 4px'}}>
                        <UI.Pill tone={s.tier==='STRONG_BUY'?'buy':s.tier==='BUY'?'info':'neutral'}>
                          {s.tier==='STRONG_BUY'?'强买':s.tier==='BUY'?'买入':'观察'}
                        </UI.Pill>
                      </td>
                      <td><UI.StockTag code={s.stock_code} size="sm"/></td>
                      <td style={{fontSize:10,color:'var(--ink-2)'}}>{UI.label(s.formula_variant)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:600,color:'var(--accent)'}}>{s.score?.toFixed(1)}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.historical?.sharpe?.toFixed(2) ?? '—'}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.historical?.win_rate ? `${(s.historical.win_rate*100).toFixed(0)}%` : '—'}</td>
                      <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{s.optimal?.hp ?? '—'}d</td>
                      <td style={{fontSize:10,color:'var(--ink-2)'}}>{s.reasoning}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </UI.Card>
          )}

          {/* 卖出规则说明卡 */}
          <UI.Card title="卖出规则" action={<UI.ApiTag>services/portfolio_sizer/sell_rules.py</UI.ApiTag>}>
            <div style={{fontSize:11,color:'var(--ink-2)',lineHeight:1.7}}>
              <div>① <b>止损</b>: 价格触达 <span style={{fontFamily:'var(--f-mono)',color:'var(--c-down)'}}>止损价</span> → 全平 (优先级最高)</div>
              <div>② <b>移动止盈</b>: 涨到 <span style={{fontFamily:'var(--f-mono)',color:'var(--c-up)'}}>目标价</span> 启动 trailing, 回撤超 max(2%, 目标×20%) → 全平</div>
              <div>③ <b>到期</b>: 持仓 = hp 天数 → 全平 (按当日收盘)</div>
              <div>④ <b>加仓</b>: 距首次买入 ≥3 交易日 + 当前价 ≤ 首次买入价 + 总仓 ≤ {meta?.stock_cap_pct ? UI.fmt2pct(meta.stock_cap_pct, false) : '20%'}</div>
            </div>
          </UI.Card>
        </>
      )}
    </div>
  );
};
