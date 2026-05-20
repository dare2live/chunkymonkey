/* v3 · 市场感知 (Market Perception) */
const { useState: useStateMP, useEffect: useEffectMP } = React;

function PageMarketPerception() {
  const { UI } = window.CMV3 || {};
  const [snapshot, setSnapshot] = useStateMP(null);
  const [history, setHistory] = useStateMP([]);
  const [emotionSnapshot, setEmotionSnapshot] = useStateMP(null);
  const [emotionHistory, setEmotionHistory] = useStateMP([]);
  const [themeSnapshot, setThemeSnapshot] = useStateMP(null);
  const [themeHistory, setThemeHistory] = useStateMP([]);
  const [underReaction, setUnderReaction] = useStateMP(null);
  const [leaderFollower, setLeaderFollower] = useStateMP(null);
  const [styleSnapshot, setStyleSnapshot] = useStateMP(null);
  const [stockContext, setStockContext] = useStateMP(null);
  const [health, setHealth] = useStateMP(null);
  const [loading, setLoading] = useStateMP(true);
  const [err, setErr] = useStateMP(null);

  useEffectMP(() => {
    let alive = true;
    Promise.all([
      fetch('/api/v3/market_perception/snapshot').then(r => r.json()),
      fetch('/api/v3/market_perception/history?days=90').then(r => r.json()),
      fetch('/api/v3/market_perception/emotion/snapshot').then(r => r.json()),
      fetch('/api/v3/market_perception/emotion/history?days=90').then(r => r.json()),
      fetch('/api/v3/market_perception/theme/snapshot').then(r => r.json()),
      fetch('/api/v3/market_perception/theme/history?days=14&top_n=5').then(r => r.json()),
      fetch('/api/v3/market_perception/under_reaction/snapshot?limit=20').then(r => r.json()),
      fetch('/api/v3/market_perception/leader_follower/snapshot?limit=20').then(r => r.json()),
      fetch('/api/v3/market_perception/style/snapshot').then(r => r.json()),
      fetch('/api/v3/market_perception/stock_context/snapshot?limit=20').then(r => r.json()),
      fetch('/api/v3/market_perception/health').then(r => r.json()),
    ]).then(([snap, hist, emotionSnap, emotionHist, themeSnap, themeHist, underSnap, leaderSnap, styleSnap, contextSnap, hp]) => {
      if (!alive) return;
      if (snap.ok === false) throw new Error(snap.error || 'snapshot failed');
      if (hist.ok === false) throw new Error(hist.error || 'history failed');
      if (emotionSnap.ok === false) throw new Error(emotionSnap.error || 'emotion snapshot failed');
      if (emotionHist.ok === false) throw new Error(emotionHist.error || 'emotion history failed');
      if (themeSnap.ok === false) throw new Error(themeSnap.error || 'theme snapshot failed');
      if (themeHist.ok === false) throw new Error(themeHist.error || 'theme history failed');
      if (underSnap.ok === false) throw new Error(underSnap.error || 'under reaction snapshot failed');
      if (leaderSnap.ok === false) throw new Error(leaderSnap.error || 'leader follower snapshot failed');
      if (styleSnap.ok === false) throw new Error(styleSnap.error || 'style snapshot failed');
      if (contextSnap.ok === false) throw new Error(contextSnap.error || 'stock context snapshot failed');
      setSnapshot(snap);
      setHistory(hist.data || []);
      setEmotionSnapshot(emotionSnap);
      setEmotionHistory(emotionHist.data || []);
      setThemeSnapshot(themeSnap);
      setThemeHistory(themeHist.data || []);
      setUnderReaction(underSnap);
      setLeaderFollower(leaderSnap);
      setStyleSnapshot(styleSnap);
      setStockContext(contextSnap);
      setHealth(hp);
      setLoading(false);
    }).catch(e => {
      if (!alive) return;
      setErr(String(e));
      setLoading(false);
    });
    return () => { alive = false; };
  }, []);

  if (loading) return <div style={{padding:24,color:'var(--ink-3)'}}>加载中...</div>;
  if (err) return <div style={{padding:24,color:'crimson'}}>市场感知 API 异常: {err}</div>;

  const d = (snapshot && snapshot.data) || {};
  const e = (emotionSnapshot && emotionSnapshot.data) || {};
  const themes = (themeSnapshot && themeSnapshot.data) || [];
  const topTheme = themes[0] || {};
  const underRows = (underReaction && underReaction.data) || [];
  const topUnder = underRows[0] || {};
  const leaderRows = (leaderFollower && leaderFollower.data) || [];
  const topLeader = leaderRows[0] || {};
  const style = (styleSnapshot && styleSnapshot.data) || {};
  const contextRows = (stockContext && stockContext.data) || [];
  const topContext = contextRows[0] || {};
  const regime = d.regime_score;
  const emotion = e.emotion_score;
  const themeTone = topTheme.theme_score == null ? null : topTheme.theme_score > 0.45 ? 'pos' : topTheme.theme_score < -0.3 ? 'neg' : null;
  const regimeLabel = regime == null ? '—' : (regime > 0.3 ? 'risk_on' : regime < -0.3 ? 'risk_off' : 'mixed');
  const tone = regime == null ? null : regime > 0.3 ? 'pos' : regime < -0.3 ? 'neg' : null;
  const emotionTone = emotion == null ? null : emotion > 0.45 ? 'pos' : emotion < -0.3 ? 'neg' : null;
  const engines = health && health.engines ? Object.entries(health.engines) : [];
  const auditStatus = health && health.latest_snapshot_audit_status;
  const latestAudit = health && health.latest_audit;
  const unknownMetrics = parseUnknownMetrics(e.unknown_metrics);

  return (
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,alignItems:'stretch'}}>
        <UI.Card title="Market RegimeEngine" action={<UI.ApiTag>mart_market_perception_daily</UI.ApiTag>}
          foot={`snapshot ${d.snapshot_date || '—'} · built_at ${fmtDateTime(d.built_at)}`}>
          <div style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:12}}>
            <UI.KStat k="市场状态" v={regimeLabel} sub={regime == null ? '—' : regime.toFixed(3)} tone={tone}/>
            <UI.KStat k="广度" v={d.breadth_state || '—'} sub={d.breadth_ratio == null ? '—' : UI.fmt2pct(d.breadth_ratio, false)}/>
            <UI.KStat k="波动率" v={d.volatility_state || '—'} sub={d.hs300_vol_20d == null ? '—' : UI.fmt2pct(d.hs300_vol_20d, false)}/>
            <UI.KStat k="指数阶段" v={d.sentiment_phase || '—'} sub={d.hs300_ret_60d == null ? '—' : `HS300 60d ${UI.fmt2pct(d.hs300_ret_60d)}`}/>
          </div>
        </UI.Card>

        <UI.Card title="Market EmotionCycle" action={<UI.ApiTag>mart_market_perception_emotion_daily</UI.ApiTag>}
          foot={`snapshot ${e.snapshot_date || '—'} · built_at ${fmtDateTime(e.built_at)}`}>
          <div style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:12}}>
            <UI.KStat k="赚钱效应" v={e.emotion_state || '—'} sub={emotion == null ? '—' : emotion.toFixed(3)} tone={emotionTone}/>
            <UI.KStat k="操作偏好" v={e.action_bias || '—'} sub={e.cycle_phase || '—'} tone={emotionTone}/>
            <UI.KStat k="涨跌家数" v={`${e.up_count ?? '—'} / ${e.down_count ?? '—'}`} sub={e.market_breadth == null ? '—' : UI.fmt2pct(e.market_breadth, false)}/>
            <UI.KStat k="涨跌停" v={`${e.limit_up_count ?? '—'} / ${e.limit_down_count ?? '—'}`} sub={e.turnover_concentration == null ? '—' : `集中度 ${UI.fmt2pct(e.turnover_concentration, false)}`}/>
          </div>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,marginTop:10,paddingTop:8,borderTop:'1px solid var(--line-soft)'}}>
            <span style={{fontSize:11,color:'var(--ink-3)'}}>龙虎榜事件 {e.lhb_event_count ?? '—'} · 样本 {e.n_stocks ?? '—'}</span>
            <UI.Pill tone={unknownMetrics.length ? 'watch' : 'hold'} size="xs">unknown {unknownMetrics.length}</UI.Pill>
          </div>
        </UI.Card>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'2fr 1fr',gap:12,alignItems:'stretch'}}>
        <UI.Card title="90 日市场状态时序" action={<UI.ApiTag>/history?days=90</UI.ApiTag>}>
          <RegimeChart rows={history} emotionRows={emotionHistory}/>
        </UI.Card>
        <UI.Card title="Engine Status" action={<UI.ApiTag>/health</UI.ApiTag>}>
          <div style={{display:'flex',flexDirection:'column',gap:8}}>
            <div style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:8,paddingBottom:6,borderBottom:'1px solid var(--line-soft)'}}>
              <MiniHealth k="latest lag" v={health && health.latest_snapshot_lag_trading_days != null ? `${health.latest_snapshot_lag_trading_days}d` : '—'}/>
              <MiniHealth k="guard" v={health && health.score_guard_status ? health.score_guard_status : '—'} tone={health && health.score_guard_status === 'ok' ? 'ok' : 'warn'}/>
              <MiniHealth k="audit" v={auditStatus || '—'} tone={auditStatus === 'ok' ? 'ok' : 'warn'}/>
              <MiniHealth k="audit end" v={latestAudit && latestAudit.end_date ? latestAudit.end_date : '—'}/>
              <MiniHealth k="regime rows" v={health && health.mart_rows != null ? String(health.mart_rows) : '—'}/>
              <MiniHealth k="emotion rows" v={health && health.emotion_rows != null ? String(health.emotion_rows) : '—'}/>
              <MiniHealth k="theme rows" v={health && health.theme_rows != null ? String(health.theme_rows) : '—'}/>
              <MiniHealth k="under rows" v={health && health.under_reaction_rows != null ? String(health.under_reaction_rows) : '—'}/>
              <MiniHealth k="leader rows" v={health && health.leader_follower_rows != null ? String(health.leader_follower_rows) : '—'}/>
              <MiniHealth k="style rows" v={health && health.style_rows != null ? String(health.style_rows) : '—'}/>
              <MiniHealth k="context rows" v={health && health.stock_context_rows != null ? String(health.stock_context_rows) : '—'}/>
            </div>
            {engines.map(([name, status]) => (
              <div key={name} style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:10,padding:'7px 0',borderBottom:'1px solid var(--line-soft)'}}>
                <span style={{fontSize:12,fontWeight:600,color:'var(--ink-1)'}}>{name}</span>
                <StatusBadge status={status}/>
              </div>
            ))}
          </div>
        </UI.Card>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1fr 1.2fr',gap:12,alignItems:'stretch'}}>
        <UI.Card title="ThemeLifecycle" action={<UI.ApiTag>/theme/snapshot</UI.ApiTag>}
          foot={`snapshot ${topTheme.snapshot_date || '—'} · built_at ${fmtDateTime(topTheme.built_at)}`}>
          <div style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:12,marginBottom:10}}>
            <UI.KStat k="主线" v={topTheme.theme_name || '—'} sub={topTheme.theme_score == null ? '—' : topTheme.theme_score.toFixed(3)} tone={themeTone}/>
            <UI.KStat k="阶段" v={topTheme.lifecycle_stage || '—'} sub={topTheme.diffusion_state || '—'} tone={themeTone}/>
            <UI.KStat k="板块广度" v={topTheme.sector_breadth == null ? '—' : UI.fmt2pct(topTheme.sector_breadth, false)} sub={`涨停 ${topTheme.limit_up_count ?? '—'} · 样本 ${topTheme.n_stocks ?? '—'}`}/>
            <UI.KStat k="20日超额" v={topTheme.sector_excess_20d == null ? '—' : UI.fmt2pct(topTheme.sector_excess_20d)} sub={topTheme.sector_ret_20d == null ? '—' : `ret ${UI.fmt2pct(topTheme.sector_ret_20d)}`}/>
          </div>
          <ThemeTable rows={themes}/>
        </UI.Card>

        <UI.Card title="主题主线 / 分歧 / 退潮" action={<UI.ApiTag>/theme/history?days=14&top_n=5</UI.ApiTag>}>
          <ThemeHistory rows={themeHistory}/>
        </UI.Card>
      </div>

      <UI.Card title="FundFlow · UnderReaction" action={<UI.ApiTag>/under_reaction/snapshot?limit=20</UI.ApiTag>}
        foot={`snapshot ${topUnder.snapshot_date || '—'} · built_at ${fmtDateTime(topUnder.built_at)}`}>
        <UnderReactionTable rows={underRows}/>
      </UI.Card>

      <UI.Card title="LeaderFollower · ChainDiffusion" action={<UI.ApiTag>/leader_follower/snapshot?limit=20</UI.ApiTag>}
        foot={`snapshot ${topLeader.snapshot_date || '—'} · built_at ${fmtDateTime(topLeader.built_at)}`}>
        <LeaderFollowerTable rows={leaderRows}/>
      </UI.Card>

      <UI.Card title="StyleRotation · CrowdingRisk" action={<UI.ApiTag>/style/snapshot</UI.ApiTag>}
        foot={`snapshot ${style.snapshot_date || '—'} · built_at ${fmtDateTime(style.built_at)}`}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(4, minmax(0, 1fr))',gap:12,marginBottom:10}}>
          <UI.KStat k="风格偏好" v={style.style_bias || '—'} sub={style.style_rotation_score == null ? '—' : style.style_rotation_score.toFixed(3)} tone={style.style_rotation_score >= 0 ? 'pos' : 'neg'}/>
          <UI.KStat k="大小盘" v={fmtNum(style.size_preference_score, 3)} sub={`small ${style.small_ret_1d == null ? '—' : UI.fmt2pct(style.small_ret_1d)}`}/>
          <UI.KStat k="趋势/超跌" v={fmtNum(style.trend_preference_score, 3)} sub={`trend ${style.trend_ret_1d == null ? '—' : UI.fmt2pct(style.trend_ret_1d)}`}/>
          <UI.KStat k="拥挤风险" v={fmtNum(style.crowding_risk_score, 3)} sub={`source ${style.style_source || '—'}`} tone={style.crowding_risk_score > 0.5 ? 'neg' : 'neutral'}/>
        </div>
        <StyleDetail row={style}/>
      </UI.Card>

      <UI.Card title="StockContext · Research Only" action={<UI.ApiTag>/stock_context/snapshot?limit=20</UI.ApiTag>}
        foot={`snapshot ${topContext.snapshot_date || '—'} · built_at ${fmtDateTime(topContext.built_at)}`}>
        <StockContextTable rows={contextRows}/>
      </UI.Card>
    </div>
  );
}

function MiniHealth({ k, v, tone }) {
  const color = tone === 'ok' ? '#2f8a55' : tone === 'warn' ? '#a06a00' : 'var(--ink-1)';
  return (
    <div style={{minWidth:0}}>
      <div style={{fontSize:10,color:'var(--ink-3)',textTransform:'uppercase',letterSpacing:0,fontFamily:'var(--f-mono)'}}>{k}</div>
      <div title={v} style={{fontSize:11,fontWeight:700,color,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{v}</div>
    </div>
  );
}

function StatusBadge({ status }) {
  const UI = window.CMV3.UI;
  const tone = status === 'live' ? 'hold' : status === 'research_mvp' ? 'info' : status === 'stub' ? 'watch' : 'neutral';
  return <UI.Pill tone={tone} size="xs">{status}</UI.Pill>;
}

function ThemeTable({ rows }) {
  const UI = window.CMV3.UI;
  if (!rows || !rows.length) return <div style={{padding:12,color:'var(--ink-3)'}}>暂无主题数据</div>;
  return (
    <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
      <thead>
        <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
          <th style={{padding:'7px 4px'}}>主题</th>
          <th style={{padding:'7px 4px'}}>阶段</th>
          <th style={{padding:'7px 4px'}}>结构</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>score</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>breadth</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 8).map(r => (
          <tr key={r.theme_name} style={{borderTop:'1px solid var(--line-soft)'}}>
            <td style={{padding:'8px 4px',fontWeight:700,color:'var(--ink-0)'}}>{r.theme_name}</td>
            <td style={{padding:'8px 4px'}}><UI.Pill tone={stageTone(r.lifecycle_stage)} size="xs">{r.lifecycle_stage || '—'}</UI.Pill></td>
            <td style={{padding:'8px 4px',color:'var(--ink-2)'}}>{r.diffusion_state || '—'}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:700,color:(r.theme_score||0)>=0?'#2f8a55':'#c4382e'}}>{fmtNum(r.theme_score, 3)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.sector_breadth == null ? '—' : UI.fmt2pct(r.sector_breadth, false)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ThemeHistory({ rows }) {
  if (!rows || !rows.length) return <div style={{padding:12,color:'var(--ink-3)'}}>暂无历史主题数据</div>;
  const byDate = rows.reduce((acc, row) => {
    (acc[row.snapshot_date] = acc[row.snapshot_date] || []).push(row);
    return acc;
  }, {});
  const dates = Object.keys(byDate).sort();
  return (
    <div style={{display:'grid',gridTemplateColumns:'96px 1fr',gap:8,alignItems:'start'}}>
      {dates.slice(-10).map(date => (
        <React.Fragment key={date}>
          <div style={{fontSize:10,fontFamily:'var(--f-mono)',color:'var(--ink-3)',paddingTop:8}}>{date}</div>
          <div style={{display:'flex',flexWrap:'wrap',gap:6,padding:'6px 0',borderTop:'1px solid var(--line-soft)'}}>
            {byDate[date].slice(0, 5).map(row => (
              <span key={`${date}-${row.theme_name}`} title={`${row.lifecycle_stage} · ${row.diffusion_state} · ${fmtNum(row.theme_score, 3)}`}
                    style={{display:'inline-flex',alignItems:'center',gap:5,padding:'3px 6px',borderRadius:4,border:'1px solid var(--line)',background:'var(--bg-2)',fontSize:11}}>
                <b style={{color:'var(--ink-0)'}}>{row.theme_name}</b>
                <span style={{color:stageColor(row.lifecycle_stage)}}>{row.lifecycle_stage}</span>
                <span style={{fontFamily:'var(--f-mono)',color:'var(--ink-3)'}}>{fmtNum(row.theme_score, 2)}</span>
              </span>
            ))}
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

function UnderReactionTable({ rows }) {
  const UI = window.CMV3.UI;
  if (!rows || !rows.length) return <div style={{padding:12,color:'var(--ink-3)'}}>暂无资金预期差数据</div>;
  return (
    <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
      <thead>
        <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
          <th style={{padding:'7px 4px'}}>代码</th>
          <th style={{padding:'7px 4px'}}>主题</th>
          <th style={{padding:'7px 4px'}}>阶段</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>under</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>fund</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>price</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>5d</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>20d</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>LHB</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 12).map(r => (
          <tr key={r.stock_code} style={{borderTop:'1px solid var(--line-soft)'}}>
            <td style={{padding:'8px 4px',fontFamily:'var(--f-mono)',fontWeight:700,color:'var(--ink-0)'}}>{r.stock_code}</td>
            <td style={{padding:'8px 4px',color:'var(--ink-2)'}}>{r.theme_name || '—'}</td>
            <td style={{padding:'8px 4px'}}><UI.Pill tone={stageTone(r.lifecycle_stage)} size="xs">{r.lifecycle_stage || '—'}</UI.Pill></td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:700,color:(r.under_reaction_score||0)>=0?'#2f8a55':'#c4382e'}}>{fmtNum(r.under_reaction_score, 3)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.fund_anomaly_score, 2)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.price_reaction_score, 2)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',color:(r.ret_5d||0)>=0?'#2f8a55':'#c4382e'}}>{r.ret_5d == null ? '—' : UI.fmt2pct(r.ret_5d)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',color:(r.ret_20d||0)>=0?'#2f8a55':'#c4382e'}}>{r.ret_20d == null ? '—' : UI.fmt2pct(r.ret_20d)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.lhb_count_30d ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LeaderFollowerTable({ rows }) {
  const UI = window.CMV3.UI;
  if (!rows || !rows.length) return <div style={{padding:12,color:'var(--ink-3)'}}>暂无龙头跟随数据</div>;
  return (
    <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
      <thead>
        <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
          <th style={{padding:'7px 4px'}}>主题</th>
          <th style={{padding:'7px 4px'}}>阶段</th>
          <th style={{padding:'7px 4px'}}>leader</th>
          <th style={{padding:'7px 4px'}}>follower</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>diffusion</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>leader 5d</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>follower 1d</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>follower 5d</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 12).map(r => (
          <tr key={`${r.theme_name}-${r.leader_stock_code}-${r.follower_stock_code}`} style={{borderTop:'1px solid var(--line-soft)'}}>
            <td style={{padding:'8px 4px',fontWeight:700,color:'var(--ink-0)'}}>{r.theme_name || '—'}</td>
            <td style={{padding:'8px 4px'}}><UI.Pill tone={stageTone(r.lifecycle_stage)} size="xs">{r.lifecycle_stage || '—'}</UI.Pill></td>
            <td style={{padding:'8px 4px',fontFamily:'var(--f-mono)',fontWeight:700,color:'var(--ink-0)'}}>{r.leader_stock_code}</td>
            <td style={{padding:'8px 4px',fontFamily:'var(--f-mono)',fontWeight:700,color:'var(--ink-0)'}}>{r.follower_stock_code}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:700,color:(r.diffusion_score||0)>=0?'#2f8a55':'#c4382e'}}>{fmtNum(r.diffusion_score, 3)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',color:(r.leader_ret_5d||0)>=0?'#2f8a55':'#c4382e'}}>{r.leader_ret_5d == null ? '—' : UI.fmt2pct(r.leader_ret_5d)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',color:(r.follower_ret_1d||0)>=0?'#2f8a55':'#c4382e'}}>{r.follower_ret_1d == null ? '—' : UI.fmt2pct(r.follower_ret_1d)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',color:(r.follower_ret_5d||0)>=0?'#2f8a55':'#c4382e'}}>{r.follower_ret_5d == null ? '—' : UI.fmt2pct(r.follower_ret_5d)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.follower_amount_ratio_5_20, 2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StyleDetail({ row }) {
  const UI = window.CMV3.UI;
  if (!row || !row.snapshot_date) return <div style={{padding:12,color:'var(--ink-3)'}}>暂无风格轮动数据</div>;
  const items = [
    ['mid 1d', row.mid_ret_1d == null ? '—' : UI.fmt2pct(row.mid_ret_1d)],
    ['large 1d', row.large_ret_1d == null ? '—' : UI.fmt2pct(row.large_ret_1d)],
    ['reversal 1d', row.reversal_ret_1d == null ? '—' : UI.fmt2pct(row.reversal_ret_1d)],
    ['top turnover', row.top_decile_turnover_share == null ? '—' : UI.fmt2pct(row.top_decile_turnover_share, false)],
    ['hot share', row.hot_stock_share == null ? '—' : UI.fmt2pct(row.hot_stock_share, false)],
    ['overheat', fmtNum(row.overheat_reversal_risk, 3)],
    ['emotion', row.emotion_state || '—'],
  ];
  return (
    <div style={{display:'grid',gridTemplateColumns:'repeat(7, minmax(0, 1fr))',gap:8}}>
      {items.map(([k, v]) => <MiniHealth key={k} k={k} v={v}/>)}
    </div>
  );
}

function StockContextTable({ rows }) {
  const UI = window.CMV3.UI;
  if (!rows || !rows.length) return <div style={{padding:12,color:'var(--ink-3)'}}>暂无个股上下文数据</div>;
  return (
    <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
      <thead>
        <tr style={{textAlign:'left',color:'var(--ink-3)',fontSize:10,letterSpacing:'.04em',textTransform:'uppercase'}}>
          <th style={{padding:'7px 4px'}}>代码</th>
          <th style={{padding:'7px 4px'}}>状态</th>
          <th style={{padding:'7px 4px'}}>主题</th>
          <th style={{padding:'7px 4px'}}>leader</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>context</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>under</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>theme</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>follow</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>crowd</th>
          <th style={{padding:'7px 4px',textAlign:'right'}}>complete</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 12).map(r => (
          <tr key={r.stock_code} style={{borderTop:'1px solid var(--line-soft)'}}>
            <td style={{padding:'8px 4px',fontFamily:'var(--f-mono)',fontWeight:700,color:'var(--ink-0)'}}>{r.stock_code}</td>
            <td style={{padding:'8px 4px'}}><UI.Pill tone={contextTone(r.context_state)} size="xs">{r.context_state || '—'}</UI.Pill></td>
            <td style={{padding:'8px 4px',color:'var(--ink-2)'}}>{r.theme_name || '—'} · {r.lifecycle_stage || '—'}</td>
            <td style={{padding:'8px 4px',fontFamily:'var(--f-mono)'}}>{r.leader_stock_code || '—'}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)',fontWeight:700,color:(r.context_score||0)>=0?'#2f8a55':'#c4382e'}}>{fmtNum(r.context_score, 3)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.under_reaction_score, 2)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.theme_score, 2)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.leader_follow_score, 2)}</td>
            <td style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{fmtNum(r.crowding_risk_score, 2)}</td>
            <td title={r.missing_context_fields || ''} style={{padding:'8px 4px',textAlign:'right',fontFamily:'var(--f-mono)'}}>{r.data_completeness_score == null ? '—' : UI.fmt2pct(r.data_completeness_score, false)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function contextTone(state) {
  return state === 'context_supportive' ? 'buy'
    : state === 'context_hostile' ? 'sell'
    : 'neutral';
}

function stageTone(stage) {
  return ['主升', '高潮', '确认'].includes(stage) ? 'buy'
    : ['退潮'].includes(stage) ? 'sell'
    : ['反抽', '启动'].includes(stage) ? 'info'
    : 'neutral';
}

function stageColor(stage) {
  return ['主升', '高潮', '确认'].includes(stage) ? '#2f8a55'
    : ['退潮'].includes(stage) ? '#c4382e'
    : ['反抽', '启动'].includes(stage) ? '#1e5aaa'
    : 'var(--ink-2)';
}

function RegimeChart({ rows, emotionRows }) {
  const W = 760, H = 240, P = 30;
  if (!rows || rows.length < 2) return <div style={{padding:24,color:'var(--ink-3)'}}>暂无历史数据</div>;
  const emotionByDate = new Map((emotionRows || []).map(r => [r.snapshot_date, r]));
  const merged = rows.map(r => ({...r, emotion_score: emotionByDate.get(r.snapshot_date)?.emotion_score}));
  const vals = merged.flatMap(r => [r.regime_score, r.emotion_score, scaledBreadth(r.breadth_ratio), scaledVol(r.hs300_vol_20d)]).filter(v => Number.isFinite(v));
  const min = Math.min(-1, ...vals), max = Math.max(1, ...vals), range = max - min || 1;
  const xs = i => P + (i / (merged.length - 1)) * (W - P * 2);
  const ys = v => H - P - ((v - min) / range) * (H - P * 2);
  const path = fn => merged.map((r, originalIndex) => ({ r, originalIndex }))
    .filter(p => Number.isFinite(fn(p.r)))
    .map((p, i) => `${i ? 'L' : 'M'}${xs(p.originalIndex).toFixed(1)},${ys(fn(p.r)).toFixed(1)}`)
    .join(' ');
  const ticks = [-1, -0.5, 0, 0.5, 1];
  const mid = merged[Math.floor(merged.length / 2)];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:'auto',display:'block',background:'var(--bg-2)',borderRadius:5}}>
      {ticks.map(v => (
        <g key={v}>
          <line x1={P} y1={ys(v)} x2={W-P} y2={ys(v)} stroke="var(--line)" strokeWidth="0.6" opacity="0.55"/>
          <text x={P-6} y={ys(v)+3} textAnchor="end" fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">{v.toFixed(1)}</text>
        </g>
      ))}
      <path d={path(r => r.regime_score)} fill="none" stroke="var(--accent)" strokeWidth="1.8"/>
      <path d={path(r => r.emotion_score)} fill="none" stroke="#c35b2e" strokeWidth="1.6"/>
      <path d={path(r => scaledBreadth(r.breadth_ratio))} fill="none" stroke="#2f8a55" strokeWidth="1.4"/>
      <path d={path(r => scaledVol(r.hs300_vol_20d))} fill="none" stroke="#1e5aaa" strokeWidth="1.4" strokeDasharray="4,3"/>
      <g fontSize="10" fontFamily="var(--f-mono)" fontWeight="600">
        <text x={W-8} y={14} textAnchor="end" fill="var(--accent)">regime_score</text>
        <text x={W-8} y={28} textAnchor="end" fill="#c35b2e">emotion_score</text>
        <text x={W-8} y={42} textAnchor="end" fill="#2f8a55">breadth x2-1</text>
        <text x={W-8} y={56} textAnchor="end" fill="#1e5aaa">vol scaled</text>
      </g>
      <g fontSize="9" fill="var(--ink-3)" fontFamily="var(--f-mono)">
        <text x={P} y={H-7} textAnchor="start">{merged[0].snapshot_date}</text>
        <text x={W/2} y={H-7} textAnchor="middle">{mid.snapshot_date}</text>
        <text x={W-P} y={H-7} textAnchor="end">{merged[merged.length-1].snapshot_date}</text>
      </g>
    </svg>
  );
}

function scaledBreadth(v) {
  return v == null ? 0 : Number(v) * 2 - 1;
}

function scaledVol(v) {
  if (v == null) return 0;
  return Math.max(-1, Math.min(1, (0.25 - Number(v)) / 0.25));
}

function fmtDateTime(v) {
  if (!v) return '—';
  return String(v).replace('T', ' ').replace(/\.\d+$/, '').replace(/\+00:00$/, ' UTC');
}

function fmtNum(v, digits) {
  return v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(digits);
}

function parseUnknownMetrics(v) {
  if (!v) return [];
  if (Array.isArray(v)) return v;
  try {
    const parsed = JSON.parse(v);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return [];
  }
}

window.CMV3 = window.CMV3 || {};
window.CMV3.PageMarketPerception = PageMarketPerception;
