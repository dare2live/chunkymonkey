/* The detail/params right pane + the MACD chart (SVG, self-contained) */
const { useState: useStateD, useMemo: useMemoD, useRef: useRefD, useEffect: useEffectD } = React;

function MacdSparkline({ series }) {
  if (!series) return null;
  const w = 360, h = 110, pad = 6;
  const n = series.close.length;
  const xs = i => pad + (i / (n - 1)) * (w - pad * 2);
  const mn = Math.min(...series.close), mx = Math.max(...series.close);
  const ys = v => pad + (1 - (v - mn) / (mx - mn || 1)) * (h - pad * 2);
  const path = series.close.map((v, i) => `${i === 0 ? 'M' : 'L'}${xs(i).toFixed(1)} ${ys(v).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart">
      <defs>
        <linearGradient id="priceFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.18" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${path} L${xs(n-1)} ${h - pad} L${xs(0)} ${h - pad} Z`} fill="url(#priceFill)" />
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.4" />
      {series.crosses.map((c, i) => (
        <circle key={i} cx={xs(c.idx)} cy={ys(series.close[c.idx])} r="3"
                fill={c.type === 'golden' ? 'var(--pos)' : 'var(--neg)'} stroke="white" strokeWidth="1" />
      ))}
    </svg>
  );
}

function MacdBars({ series }) {
  if (!series) return null;
  const w = 360, h = 80, pad = 6;
  const n = series.bar.length;
  const xs = i => pad + (i / (n - 1)) * (w - pad * 2);
  const all = [...series.dif, ...series.dea, ...series.bar];
  const mn = Math.min(...all), mx = Math.max(...all);
  const ys = v => pad + (1 - (v - mn) / (mx - mn || 1)) * (h - pad * 2);
  const barW = (w - pad * 2) / n * 0.6;
  const difPath = series.dif.map((v, i) => `${i === 0 ? 'M' : 'L'}${xs(i).toFixed(1)} ${ys(v).toFixed(1)}`).join(' ');
  const deaPath = series.dea.map((v, i) => `${i === 0 ? 'M' : 'L'}${xs(i).toFixed(1)} ${ys(v).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart">
      <line x1={pad} x2={w-pad} y1={ys(0)} y2={ys(0)} stroke="var(--border)" strokeWidth="0.5" />
      {series.bar.map((v, i) => {
        const y1 = ys(0), y2 = ys(v);
        return <rect key={i} x={xs(i) - barW/2} y={Math.min(y1, y2)}
                     width={barW} height={Math.abs(y2 - y1)}
                     fill={v >= 0 ? 'var(--neg-soft)' : 'var(--pos-soft)'} />;
      })}
      <path d={difPath} fill="none" stroke="var(--warn)" strokeWidth="1.3" />
      <path d={deaPath} fill="none" stroke="var(--accent)" strokeWidth="1.3" />
    </svg>
  );
}

function ParamsPane() {
  const params = window.MOCK.PARAMS;
  return (
    <div className="pane-body">
      <div className="pane-section-title">策略参数</div>
      <div className="param-grid">
        {Object.entries(params).map(([k, v]) => (
          <div className="param-card" key={k}>
            <div className="param-name">{v.label}</div>
            <div className="param-value mono">{v.value}</div>
            <div className="param-desc">{v.desc}</div>
            {(v.low_hint || v.high_hint) && (
              <div className="param-hints">
                {v.low_hint && <div>↓ {v.low_hint}</div>}
                {v.high_hint && <div>↑ {v.high_hint}</div>}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="pane-section-title" style={{ marginTop: 18 }}>使用提示</div>
      <div className="hint-card">
        左侧表格按「建议」综合排序：今日推荐 → 买入窗口 → 提前关注 → 持仓观察 → 风险 → 等待。
        点击任意行可在此切换到该股票的详细分析。
      </div>
    </div>
  );
}

function DetailPane({ row }) {
  const series = useMemoD(() => row ? window.MOCK.genChartSeries(parseInt(row.code) || 1) : null, [row && row.code]);
  if (!row) return (
    <div className="pane-body empty">
      <div className="empty-icon" />
      <div>请选择左侧任意股票</div>
      <div className="empty-sub">查看 MACD 走势、买卖时机与历史回测</div>
    </div>
  );

  const { Num, StatusBadge, HISTORY_LABEL } = window.UI;
  const score = row.buy_score || 0;
  const scoreClass = score >= 60 ? 'pos' : score >= 35 ? 'warn' : 'mute';

  let recBox = { kind: 'wait', title: '等待信号', desc: '未检测到新的金叉事件，持续观察即可。' };
  if (row.is_buy_point) {
    recBox = { kind: 'pick', title: '强烈推荐买入', desc: `金叉 ${row.days_event ?? '-'} 天，量价过滤通过，历史胜率 ${row.win_rate != null ? (row.win_rate*100).toFixed(1)+'%' : '—'}，综合 ${score} 分。` };
  } else if (row.status === '刚金叉') {
    recBox = { kind: 'buy', title: '买入窗口', desc: `金叉后 ${row.days_event ?? '-'} 天，建议关注回踩低吸。${row.filter_pass ? '满足入场过滤' : '部分过滤未通过'}` };
  } else if (row.status === '即将金叉') {
    recBox = { kind: 'warn', title: '金叉预警', desc: `DIF 与 DEA 快速收敛，预计 ${row.days_event ?? '-'} 天内出现金叉。` };
  } else if (row.status === '持仓期') {
    recBox = { kind: 'hold', title: '持仓中', desc: `最近金叉 ${row.last_gc_date}，按固定周期执行。${row.sell_hint || ''}` };
  } else if (row.status === '刚死叉') {
    recBox = { kind: 'risk', title: '风险提示', desc: 'DIF 下穿 DEA，持仓风险上升，建议审视仓位。' };
  }

  const actionText =
    row.is_buy_point ? '强烈推荐买入' :
    row.status === '刚金叉' ? '可考虑买入' :
    row.status === '即将金叉' ? '提前关注' :
    row.status === '持仓期' ? '继续持有' :
    row.status === '刚死叉' ? '注意风险' : '等待';

  return (
    <div className="pane-body">
      {/* Header */}
      <div className="detail-head">
        <div>
          <div className="detail-code">
            <span className="mono">{row.code}</span>
            <span className="detail-name">{row.name}</span>
          </div>
          <div className="detail-meta">
            <StatusBadge status={row.status} />
            <span className="dot-sep">·</span>
            <span>{row.industry}</span>
            <span className="dot-sep">·</span>
            <span>{row.archetype}</span>
          </div>
        </div>
        <div className="detail-price">
          <div className="price-val mono">¥{row.cur_close.toFixed(2)}</div>
          <div className="price-meta mono">DIF {row.cur_dif.toFixed(2)} / DEA {row.cur_dea.toFixed(2)}</div>
        </div>
      </div>

      {/* Recommendation banner */}
      <div className={`rec-banner rec-${recBox.kind}`}>
        <div className="rec-title">{recBox.title}</div>
        <div className="rec-desc">{recBox.desc}</div>
      </div>

      {/* Buy/sell timing */}
      <div className="pane-section-title">买卖时机</div>
      <div className="cell-grid two">
        <div className="cell">
          <div className="cell-label">当前操作</div>
          <div className="cell-value">{actionText}</div>
        </div>
        <div className="cell">
          <div className="cell-label">最优持仓期</div>
          <div className="cell-value mono">{row.best_holding_days} 天</div>
        </div>
        <div className="cell">
          <div className="cell-label">参考买入价</div>
          <div className="cell-value mono">{row.trade_buy_price != null ? `¥${row.trade_buy_price.toFixed(2)}` : '—'}</div>
        </div>
        <div className="cell">
          <div className="cell-label">参考卖出 / 最新价</div>
          <div className="cell-value mono">{row.trade_eval_price != null ? `¥${row.trade_eval_price.toFixed(2)}` : '—'}</div>
        </div>
        <div className="cell">
          <div className="cell-label">最新验证收益</div>
          <div className="cell-value"><Num value={row.trade_ref_ret} pct signed /></div>
        </div>
        <div className="cell">
          <div className="cell-label">综合评分</div>
          <div className="cell-value">
            <span className={`mono score-${scoreClass}`}>{score}</span>
            <span className="cell-sub"> / 100</span>
            <div className="score-track"><div className={`score-fill fill-${scoreClass}`} style={{ width: Math.min(score,100) + '%' }} /></div>
          </div>
        </div>
      </div>

      {/* Chart */}
      <div className="pane-section-title">MACD 走势 · 近 90 日</div>
      <div className="chart-stack">
        <MacdSparkline series={series} />
        <MacdBars series={series} />
        <div className="chart-legend">
          <span><span className="lg-line" style={{background:'var(--accent)'}}/>收盘 / DEA</span>
          <span><span className="lg-line" style={{background:'var(--warn)'}}/>DIF</span>
          <span><span className="lg-dot" style={{background:'var(--pos)'}}/>金叉</span>
          <span><span className="lg-dot" style={{background:'var(--neg)'}}/>死叉</span>
        </div>
      </div>

      {/* Horizon table */}
      <div className="pane-section-title">持仓期分析</div>
      <table className="hp-table">
        <thead>
          <tr><th>持股天</th><th>胜率</th><th>均收益</th><th>均回撤</th><th>Calmar</th><th>样本</th></tr>
        </thead>
        <tbody>
          {[5,10,15,20,30,60].map(h => {
            const hm = row.horizons[h];
            const isBest = row.best_holding_days === h;
            return (
              <tr key={h} className={isBest ? 'best' : ''}>
                <td className="mono">{h}{isBest && <span className="star">★</span>}</td>
                <td className="mono">{hm ? (hm.win_rate*100).toFixed(1)+'%' : '—'}</td>
                <td><Num value={hm?.avg_ret} pct signed /></td>
                <td><Num value={hm?.avg_dd} pct signed /></td>
                <td className="mono">{hm ? hm.calmar.toFixed(2) : '—'}</td>
                <td className="mono mute">{hm?.n ?? '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Indicators */}
      <div className="pane-section-title">当前指标</div>
      <div className="cell-grid two">
        <Cell label="DIF 距零轴" mono>{row.gap.toFixed(2)}</Cell>
        <Cell label="额比 (20日)" mono className={row.cur_amt_r20 > 1.5 ? 'pos' : row.cur_amt_r20 < 0.8 ? 'neg' : ''}>
          {row.cur_amt_r20.toFixed(2)}×
        </Cell>
        <Cell label="价格位置 (60日)" mono>{(row.cur_price60*100).toFixed(1)}%</Cell>
        <Cell label="股东变化" mono className={row.holder_chg < 0 ? 'pos' : 'neg'}>
          {(row.holder_chg*100).toFixed(2)}%
        </Cell>
        <Cell label="最近金叉" mono>{row.last_gc_date}</Cell>
        <Cell label="F1 / F3 / F5">
          <span className="formula-chips">
            <span className={`fchip ${row.f1_hit ? 'on' : ''}`}>F1</span>
            <span className={`fchip ${row.f3_hit ? 'on' : ''}`}>F3</span>
            <span className={`fchip ${row.f5_hit ? 'on' : ''}`}>F5</span>
          </span>
        </Cell>
      </div>

      {/* History */}
      <div className="pane-section-title">历史回测绩效</div>
      <div className="cell-grid two">
        <Cell label="信号次数" mono>{row.has_history ? row.signal_count : <span className="mute">{HISTORY_LABEL[row.history_status] || '—'}</span>}</Cell>
        <Cell label="历史状态">{HISTORY_LABEL[row.history_status] || '—'}</Cell>
        <Cell label="历史胜率" mono>{row.win_rate != null ? (row.win_rate*100).toFixed(1)+'%' : '—'}</Cell>
        <Cell label="均收益"><Num value={row.avg_ret} pct signed /></Cell>
        <Cell label="均回撤"><Num value={row.avg_dd} pct signed /></Cell>
        <Cell label="Calmar" mono>{row.calmar != null ? row.calmar.toFixed(2) : '—'}</Cell>
      </div>
    </div>
  );
}

function Cell({ label, children, mono, className }) {
  return (
    <div className="cell">
      <div className="cell-label">{label}</div>
      <div className={`cell-value ${mono ? 'mono' : ''} ${className || ''}`}>{children}</div>
    </div>
  );
}

window.DetailPane = DetailPane;
window.ParamsPane = ParamsPane;
