/* Tiny presentational primitives + helpers shared across the app */
const { useState, useMemo, useEffect, useRef } = React;

const fmtPct = (v, d = 2, na = '—') => {
  if (v == null || Number.isNaN(v)) return na;
  return (v * 100).toFixed(d) + '%';
};
const fmtSignedPct = (v, d = 2, na = '—') => {
  if (v == null || Number.isNaN(v)) return na;
  const s = (v * 100).toFixed(d) + '%';
  return v >= 0 ? '+' + s : s;
};
const fmtNum = (v, d = 2, na = '—') => {
  if (v == null || Number.isNaN(v)) return na;
  return Number(v).toFixed(d);
};
const cls = (...xs) => xs.filter(Boolean).join(' ');

const STATUS_META = {
  '刚金叉':   { color: 'pos',   dot: 'pos',   short: '刚金叉' },
  '即将金叉': { color: 'warn',  dot: 'warn',  short: '即将' },
  '持仓期':   { color: 'info',  dot: 'info',  short: '持仓' },
  '刚死叉':   { color: 'neg',   dot: 'neg',   short: '刚死叉' },
  '等待':     { color: 'mute',  dot: 'mute',  short: '等待' },
};

const HISTORY_LABEL = {
  ok: '历史有效',
  insufficient_history: '样本不足',
  no_signal: '无有效信号',
  too_few_signals: '信号不足',
  pending: '历史待补',
  none: '无历史',
};

function StatusDot({ kind, label, withLabel = true }) {
  return (
    <span className="status">
      <span className={cls('status-dot', `dot-${kind}`)} />
      {withLabel && <span className="status-label">{label}</span>}
    </span>
  );
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || STATUS_META['等待'];
  return <StatusDot kind={meta.dot} label={status} />;
}

function RecommendationTag({ row }) {
  if (row.is_buy_point)        return <span className="rec rec-pick">今日推荐</span>;
  if (row.status === '刚金叉')   return <span className="rec rec-buy">买入窗口</span>;
  if (row.status === '即将金叉') return <span className="rec rec-warn">提前关注</span>;
  if (row.status === '持仓期')   return <span className="rec rec-hold">持仓观察</span>;
  if (row.status === '刚死叉')   return <span className="rec rec-risk">风险提示</span>;
  return <span className="rec rec-wait">等待</span>;
}

function Num({ value, d = 2, signed = false, pct = false, prefix = '', na = '—', mono = true }) {
  if (value == null || Number.isNaN(value)) return <span className="num na">{na}</span>;
  const n = Number(value);
  let body;
  if (pct) body = (signed && n > 0 ? '+' : '') + (n * 100).toFixed(d) + '%';
  else body = (signed && n > 0 ? '+' : '') + prefix + n.toFixed(d);
  const klass = signed ? (n > 0 ? 'num pos' : n < 0 ? 'num neg' : 'num') : 'num';
  return <span className={cls(klass, mono && 'mono')}>{body}</span>;
}

window.UI = { fmtPct, fmtSignedPct, fmtNum, cls, STATUS_META, HISTORY_LABEL,
              StatusDot, StatusBadge, RecommendationTag, Num };
