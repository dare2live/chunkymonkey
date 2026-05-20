/* v3 · Tab 8 — 市场感知 (Market Perception, Codex 扩展占位)

   状态: STUB only — 我 (Claude) 先 stake 标签入口, 等 Codex 在此基础上扩展.

   Codex 任务范围 (见 docs/market_perception_codex_handoff.md):
   - MVP: MarketRegimeEngine 情绪温度计 (risk_on/off + 4-5 daily features)
   - 数据源: READ-only from mart_index_daily / fact_stock_kline_daily / fact_lhb_event / mart_data_source_watermark
   - 新表: 可 CREATE mart_market_perception_daily (不动现有表)
   - API: 由 backend/routers/v3_market_perception.py 提供 (Claude stub 占位)
   - UI: 在此 jsx 扩展 — 子 tab / 卡片 / 时序图 etc.

   占位字段 (Codex 实施时按需重写):
   - regime_score: -1.0 ~ +1.0 (risk_off → risk_on)
   - breadth_state: '健康扩散' / '分化' / '杀跌'
   - volatility_state: 'low' / 'normal' / 'high'
   - sentiment_phase: 'init' / 'spread' / 'climax' / 'fade'
*/
const { useState: useStateMP, useEffect: useEffectMP } = React;

function PageMarketPerception() {
  const { UI } = window.CMV3 || {};
  const [data, setData] = useStateMP(null);
  const [loading, setLoading] = useStateMP(true);
  const [err, setErr] = useStateMP(null);

  useEffectMP(() => {
    fetch('/api/v3/market_perception/snapshot')
      .then(r => r.json())
      .then(j => { setData(j); setLoading(false); })
      .catch(e => { setErr(String(e)); setLoading(false); });
  }, []);

  if (loading) return <div style={{padding:24,color:'#888'}}>加载中…</div>;
  if (err) return <div style={{padding:24,color:'crimson'}}>市场感知 API 异常: {err}</div>;

  const d = (data && data.data) || {};
  const regime = d.regime_score;
  const regimeLabel = regime == null
    ? '—'
    : (regime > 0.3 ? 'risk_on' : regime < -0.3 ? 'risk_off' : 'mixed');

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <div style={{background:'#fff',border:'1px solid var(--line)',borderRadius:8,padding:16}}>
        <div style={{fontSize:11,color:'#888',marginBottom:8,letterSpacing:'.04em',textTransform:'uppercase'}}>
          MARKET PERCEPTION · placeholder (待 Codex 扩展)
        </div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4, 1fr)',gap:12}}>
          <Cell label="市场情绪" value={regimeLabel} sub={regime == null ? null : regime.toFixed(2)} />
          <Cell label="广度" value={d.breadth_state || '—'} />
          <Cell label="波动率" value={d.volatility_state || '—'} />
          <Cell label="阶段" value={d.sentiment_phase || '—'} />
        </div>
      </div>
      <div style={{background:'#fff',border:'1px solid var(--line)',borderRadius:8,padding:16}}>
        <div style={{fontSize:13,fontWeight:600,marginBottom:8}}>下一步 (Codex 扩展)</div>
        <ul style={{fontSize:12,color:'#444',lineHeight:1.6,paddingLeft:16}}>
          <li>子 tab: 情绪 / 广度 / 主题 / 资金 / 风格 / 龙头 / 拥挤</li>
          <li>时序图: 近 90 日 regime_score / breadth / volatility</li>
          <li>主题生命周期看板 (主线 / 支线 / 退潮)</li>
          <li>详见 docs/market_perception_codex_handoff.md</li>
        </ul>
      </div>
    </div>
  );
}

function Cell({ label, value, sub }) {
  return (
    <div style={{padding:12,background:'#fafafa',borderRadius:6}}>
      <div style={{fontSize:10,color:'#888',letterSpacing:'.04em',textTransform:'uppercase',marginBottom:4}}>{label}</div>
      <div style={{fontSize:18,fontWeight:600}}>{value}</div>
      {sub != null && <div style={{fontSize:10,color:'#888',marginTop:2}}>{sub}</div>}
    </div>
  );
}

window.CMV3 = window.CMV3 || {};
window.CMV3.PageMarketPerception = PageMarketPerception;
