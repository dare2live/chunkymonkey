/* Chunky Monkey v3 · 机构视图 (Phase η)
 *
 * 输入: institution_id (from INSTITUTIONS or 搜索)
 * 数据源: /api/v3/view/institution/{id}
 * 展示:
 *   1. 机构主 profile (mart_institution_profile)
 *   2. 当前持仓 (fact_top10_holder_period)
 *   3. 跟随回测胜率 (fact_institution_follow_backtest, 若有)
 */

const { useState: useStateI, useEffect: useEffectI } = React;

window.CMV3 = window.CMV3 || {};
window.CMV3.PageInstitutionView = function PageInstitutionView({ onOpenStock }) {
  const { UI, INSTITUTIONS } = window.CMV3;
  const [instId, setInstId] = useStateI(INSTITUTIONS?.[0]?.id || null);
  const [data, setData] = useStateI(null);
  const [loading, setLoading] = useStateI(false);
  const [filter, setFilter] = useStateI('');

  useEffectI(() => {
    if (!instId) return;
    setLoading(true);
    fetch(`/api/v3/view/institution/${instId}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [instId]);

  const filteredInsts = (INSTITUTIONS || []).filter(i =>
    !filter || i.name.includes(filter) || i.id.includes(filter)
  );

  return (
    <div style={{display:'grid',gridTemplateColumns:'280px 1fr',gap:14}}>
      {/* 机构列表 (左侧) */}
      <UI.Card title="机构清单" action={<UI.ApiTag>mart_institution_profile</UI.ApiTag>}>
        <input value={filter} onChange={e=>setFilter(e.target.value)}
               placeholder="搜索机构..."
               style={{width:'100%',padding:'6px 10px',fontSize:12,border:'1px solid var(--line)',borderRadius:5,marginBottom:8}}/>
        <div style={{maxHeight:600,overflowY:'auto'}}>
          {filteredInsts.slice(0,100).map(i => (
            <div key={i.id} onClick={() => setInstId(i.id)}
                 style={{padding:'8px 10px',cursor:'pointer',borderTop:'1px solid var(--line-soft)',
                         background: instId===i.id ? 'var(--bg-2)' : 'transparent'}}>
              <div style={{fontSize:12,fontWeight:600}}>{i.alias || i.name}</div>
              <div style={{fontSize:10,color:'var(--ink-3)',marginTop:2}}>
                {i.type} · win60 <span style={{fontFamily:'var(--f-mono)'}}>{((i.win60||0)*100).toFixed(2)}%</span> · {i.n_stocks || 0} 股
              </div>
            </div>
          ))}
        </div>
      </UI.Card>

      {/* 机构详情 (右侧) */}
      <div>
        {loading && <div style={{padding:24,textAlign:'center',color:'var(--ink-3)'}}>加载中...</div>}
        {data && !loading && data.profile && (
          <div style={{display:'grid',gap:14}}>
            {/* profile 卡 */}
            <UI.Card title={data.profile.name} action={<UI.Pill tone="info">{data.profile.type}</UI.Pill>}>
              <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:12,fontSize:11}}>
                <UI.KStat k="win 30d" v={`${((data.profile.win_rate_30d||0)).toFixed(2)}%`}/>
                <UI.KStat k="win 60d" v={`${((data.profile.win_rate_60d||0)).toFixed(2)}%`}/>
                <UI.KStat k="win 90d" v={`${((data.profile.win_rate_90d||0)).toFixed(2)}%`}/>
                <UI.KStat k="当前持股数" v={data.profile.current_stock_count}/>
                <UI.KStat k="总事件" v={data.profile.total_events}/>
                <UI.KStat k="总报告期" v={data.profile.total_periods}/>
                <UI.KStat k="均涨幅 30d" v={`${((data.profile.avg_gain_30d||0)).toFixed(2)}%`}/>
                <UI.KStat k="均涨幅 60d" v={`${((data.profile.avg_gain_60d||0)).toFixed(2)}%`}/>
              </div>
            </UI.Card>

            {/* 跟随回测 (若有) */}
            <UI.Card title="跟随回测" action={<UI.ApiTag>fact_institution_follow_backtest</UI.ApiTag>}>
              {data.follow_backtest ? (
                <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:12,fontSize:11}}>
                  <UI.KStat k="跟随交易数" v={data.follow_backtest.n_trades}/>
                  <UI.KStat k="跟随胜率" v={`${(data.follow_backtest.win_rate*100).toFixed(2)}%`}/>
                  <UI.KStat k="均收益" v={`${(data.follow_backtest.avg_pnl*100).toFixed(2)}%`}/>
                  <UI.KStat k="Sharpe" v={data.follow_backtest.sharpe?.toFixed(2)}/>
                  <UI.KStat k="均最大回撤" v={`${(data.follow_backtest.avg_max_dd*100).toFixed(2)}%`}/>
                </div>
              ) : <div style={{color:'var(--ink-3)',fontSize:12}}>暂无跟随回测数据 (跑 build_follow_backtest.py 可生成)</div>}
            </UI.Card>

            {/* 当前持仓 */}
            <UI.Card title={`当前持仓 (${data.holdings?.length || 0})`} action={<UI.ApiTag>fact_top10_holder_period.latest</UI.ApiTag>}>
              {data.holdings?.length ? (
                <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                  <thead>
                    <tr style={{textAlign:'left',color:'var(--ink-3)',borderBottom:'1px solid var(--line)'}}>
                      <th style={{padding:'6px 4px'}}>股票</th>
                      <th style={{textAlign:'right'}}>持股比</th>
                      <th style={{textAlign:'right'}}>变动</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.holdings.slice(0,40).map((h,i) => (
                      <tr key={i} style={{borderTop:'1px solid var(--line-soft)',cursor:'pointer'}}
                          onClick={() => onOpenStock && onOpenStock(h.stock_code)}>
                        <td style={{padding:'6px 4px'}}>
                          <UI.StockTag code={h.stock_code} name={h.stock_name} size="sm"/>
                        </td>
                        <td style={{textAlign:'right',fontFamily:'var(--f-mono)'}}>{UI.fmt2(h.share_pct)}%</td>
                        <td style={{textAlign:'right',fontFamily:'var(--f-mono)',
                                    color: (h.share_change||0)>=0 ? 'var(--c-up)' : 'var(--c-down)'}}>
                          {h.share_change ? UI.fmt2sign(h.share_change) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <div style={{color:'var(--ink-3)',fontSize:12}}>该机构暂无持仓数据</div>}
            </UI.Card>
          </div>
        )}
        {!instId && <div style={{padding:24,color:'var(--ink-3)',textAlign:'center'}}>← 左侧选择一个机构</div>}
      </div>
    </div>
  );
};
