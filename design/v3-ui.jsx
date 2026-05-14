/* v3 · 共用组件 (Pill, ScoreBar, FormulaChip, StageDot, etc.)
   全部 namespace 在 CMV3.UI 下，避免污染全局
*/
const { useState: useStateU, useMemo: useMemoU, useEffect: useEffectU } = React;

const UI = {};

// ============ 格式化 (全局统一: 默认 2 位小数) ============
UI.fmt2     = (v) => (v==null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));
UI.fmt2pct  = (v, withSign=true) => (v==null || Number.isNaN(Number(v)) ? '—' : (withSign && v>=0?'+':'') + (Number(v)*100).toFixed(2) + '%');
UI.fmt2sign = (v) => (v==null || Number.isNaN(Number(v)) ? '—' : (v>=0?'+':'') + Number(v).toFixed(2));
UI.fmtMoney = (v) => v==null ? '—' : '¥' + Number(v).toFixed(2);
UI.fmtInt   = (v) => v==null ? '—' : Math.round(Number(v)).toLocaleString();
// 旧 API: 保留向后兼容 (新代码请用 fmt2pct / fmt2)
UI.fmtPct = (v, digits=2) => (v==null ? '—' : (v>=0?'+':'') + (Number(v)*100).toFixed(digits) + '%');
UI.fmtNum = (v, d=2) => v==null ? '—' : Number(v).toFixed(d);

// ============ 标签字典 (后端字段 → 中文短名) ============
UI.label = (key) => {
  const m = (window.CMV3 && window.CMV3.LABELS) || {};
  return m[key] || key;
};

// ============ 雪球 URL: code → SH/SZ/BJ 前缀 ============
UI.xueqiuUrl = (code) => {
  if (!code) return '#';
  const c = String(code);
  let prefix = 'SH';
  if (/^(600|601|603|605|688)/.test(c)) prefix = 'SH';
  else if (/^(000|001|002|003|300|301)/.test(c)) prefix = 'SZ';
  else if (/^(4|8|9)/.test(c))             prefix = 'BJ';
  return `https://xueqiu.com/S/${prefix}${c}`;
};

// ============ StockTag (全局唯一组件, 模块化) ============
// 名称粗体 (主信息) + 代码胶囊按钮 (副, 雪球链接); 无下划线; tokens 配色
// 用法: <UI.StockTag code="600519" name="贵州茅台" size="md"/>
UI.StockTag = ({ code, name, size='md', layout='inline', nameStyle, pillStyle, onNameClick }) => {
  const sizes = {
    sm: { name: 12, pill: 9,  padPill: '1px 6px', gap: 6 },
    md: { name: 14, pill: 10, padPill: '2px 7px', gap: 7 },
    lg: { name: 16, pill: 11, padPill: '3px 9px', gap: 8 },
  };
  const sz = sizes[size] || sizes.md;
  const pillBase = {
    display: 'inline-block',
    padding: sz.padPill,
    fontSize: sz.pill,
    fontFamily: 'var(--f-mono)',
    background: 'var(--bg-2)',
    color: 'var(--ink-3)',
    borderRadius: 999,
    textDecoration: 'none',
    lineHeight: 1.3,
    border: '1px solid var(--line-soft)',
    cursor: 'pointer',
    transition: 'all 0.15s',
    fontWeight: 500,
    letterSpacing: '0.02em',
    ...(pillStyle || {}),
  };
  const codePill = code ? (
    <a href={UI.xueqiuUrl(code)} target="_blank" rel="noopener noreferrer"
       onClick={(e) => e.stopPropagation()}
       title={`在雪球查看 ${code} ${name||''}`}
       style={pillBase}
       onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(232,93,49,.08)'; e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.borderColor = 'rgba(232,93,49,.30)'; }}
       onMouseLeave={(e) => { e.currentTarget.style.background = pillBase.background; e.currentTarget.style.color = pillBase.color; e.currentTarget.style.borderColor = 'var(--line-soft)'; }}>
      {code}
    </a>
  ) : null;
  const nameBase = {
    fontWeight: 600,
    color: 'var(--ink-0)',
    fontSize: sz.name,
    cursor: onNameClick ? 'pointer' : 'default',
    ...(nameStyle || {}),
  };
  const nameEl = name ? (
    <span style={nameBase} onClick={onNameClick ? (e) => { e.stopPropagation(); onNameClick(); } : undefined}>
      {name}
    </span>
  ) : null;
  if (layout === 'stacked') {
    return (
      <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 2, alignItems: 'flex-start' }}>
        {nameEl || codePill}
        {nameEl && codePill}
      </div>
    );
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: sz.gap }}>
      {nameEl}
      {codePill}
    </span>
  );
};

// 旧 API: 重定向到 StockTag 不破坏现有调用
UI.StockNameLink = ({ code, name }) => (
  <UI.StockTag code={code} name={name === code ? null : (name || null)} size="sm" />
);

UI.Pill = ({ tone='neutral', children, size='sm' }) => {
  const tones = {
    buy:     { bg:'rgba(232,93,49,.10)',  fg:'var(--accent)', bd:'rgba(232,93,49,.30)' },
    sell:    { bg:'rgba(217,75,75,.10)',  fg:'#c4382e', bd:'rgba(217,75,75,.30)' },
    hold:    { bg:'rgba(58,140,90,.10)',  fg:'#2f8a55', bd:'rgba(58,140,90,.30)' },
    watch:   { bg:'rgba(170,140,40,.10)', fg:'#8a6f12', bd:'rgba(170,140,40,.30)' },
    neutral: { bg:'var(--bg-2)',          fg:'var(--ink-1)', bd:'var(--line)' },
    info:    { bg:'rgba(30,90,170,.08)',  fg:'#1e5aaa', bd:'rgba(30,90,170,.25)' },
  };
  const t = tones[tone] || tones.neutral;
  const pad = size === 'xs' ? '1px 6px' : '2px 8px';
  const fs  = size === 'xs' ? 10 : 11;
  return <span style={{display:'inline-flex',alignItems:'center',gap:4,padding:pad,fontSize:fs,fontWeight:600,letterSpacing:'.02em',color:t.fg,background:t.bg,border:`1px solid ${t.bd}`,borderRadius:4,whiteSpace:'nowrap'}}>{children}</span>;
};

UI.ApiTag = ({ children }) => (
  <code style={{fontSize:10,padding:'1px 5px',background:'var(--bg-2)',color:'var(--ink-3)',border:'1px solid var(--line)',borderRadius:3,fontFamily:'var(--f-mono)'}}>{children}</code>
);

UI.StageDot = ({ stage }) => {
  // stage like "2 上升" or "1.5 突破中" or "3 顶部"
  const num = parseFloat(stage);
  const color = num <= 1 ? 'var(--ink-3)' : num < 2 ? '#3a8c5a' : num < 3 ? 'var(--accent)' : '#c4382e';
  return (
    <span style={{display:'inline-flex',alignItems:'center',gap:6,fontSize:11,fontWeight:600,color:'var(--ink-1)'}}>
      <span style={{width:8,height:8,borderRadius:8,background:color}}/>
      Stage {stage}
    </span>
  );
};

UI.ScoreBar = ({ value, max=100, height=4, color='var(--accent)' }) => (
  <div style={{height,background:'var(--bg-2)',borderRadius:height/2,overflow:'hidden',minWidth:60}}>
    <div style={{height:'100%',width:`${(value/max)*100}%`,background:color,transition:'width .3s ease'}}/>
  </div>
);

UI.MiniSpark = ({ data, w=80, h=20, color='var(--accent)' }) => {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data), r = max-min || 1;
  const pts = data.map((v,i) => `${(i/(data.length-1))*w},${h - ((v-min)/r)*h}`).join(' ');
  return <svg width={w} height={h} style={{display:'block'}}><polyline fill="none" stroke={color} strokeWidth="1.5" points={pts}/></svg>;
};

UI.Card = ({ title, action, foot, children, dense=false, span=null }) => (
  <section style={{background:'#fff',border:'1px solid var(--line)',borderRadius:8,overflow:'hidden',gridColumn:span||'auto',display:'flex',flexDirection:'column'}}>
    {title && (
      <header style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'10px 14px',borderBottom:'1px solid var(--line-soft)'}}>
        <div style={{fontSize:12,fontWeight:600,color:'var(--ink-1)',letterSpacing:'.02em'}}>{title}</div>
        {action}
      </header>
    )}
    <div style={{padding:dense?10:14,flex:1,minHeight:0}}>{children}</div>
    {foot && <footer style={{padding:'8px 14px',borderTop:'1px solid var(--line-soft)',background:'var(--bg-2)',fontSize:11,color:'var(--ink-3)'}}>{foot}</footer>}
  </section>
);

UI.KStat = ({ k, v, sub, tone }) => (
  <div style={{padding:'10px 12px',background:'var(--bg-2)',borderRadius:6,border:'1px solid var(--line-soft)'}}>
    <div style={{fontSize:10,color:'var(--ink-3)',marginBottom:4,letterSpacing:'.04em',textTransform:'uppercase'}}>{k}</div>
    <div style={{fontSize:18,fontWeight:700,fontFamily:'var(--f-mono)',color:tone==='neg'?'#c4382e':tone==='pos'?'#2f8a55':'var(--ink-0)'}}>{v}</div>
    {sub && <div style={{fontSize:10,color:'var(--ink-3)',marginTop:2}}>{sub}</div>}
  </div>
);

UI.ActionPill = ({ action }) => {
  const map = { BUY:{t:'buy',l:'买入'}, SELL:{t:'sell',l:'卖出'}, HOLD:{t:'hold',l:'持有'}, WATCH:{t:'watch',l:'观察'} };
  const m = map[action] || { t:'neutral', l:action };
  return <UI.Pill tone={m.t}>{m.l}</UI.Pill>;
};

UI.FormulaChip = ({ f, formulas }) => {
  const meta = formulas.find(x => x.id === f.id) || {};
  return (
    <span title={`命中强度 ${(f.strength*100).toFixed(0)} / 历史胜率 ${(f.win*100).toFixed(0)}%`}
          style={{display:'inline-flex',alignItems:'center',gap:6,padding:'2px 6px',background:'var(--bg-2)',border:'1px solid var(--line)',borderRadius:4,fontSize:11}}>
      <span style={{fontSize:9,fontFamily:'var(--f-mono)',fontWeight:700,letterSpacing:'.04em',color:'var(--accent)',background:'#fff',border:'1px solid var(--line)',borderRadius:3,padding:'1px 4px'}}>{meta.tag}</span>
      <span style={{color:'var(--ink-1)'}}>{meta.name}</span>
      {f.state && <span style={{color:'var(--ink-3)',fontSize:10}}>· {f.state}</span>}
      <span style={{color:'var(--ink-3)',fontSize:10,fontFamily:'var(--f-mono)'}}>{(f.win*100).toFixed(0)}%</span>
    </span>
  );
};

UI.SectionHead = ({ title, sub, action }) => (
  <div style={{display:'flex',alignItems:'flex-end',justifyContent:'space-between',marginBottom:10}}>
    <div>
      <div style={{fontSize:13,fontWeight:600,color:'var(--ink-0)',letterSpacing:'.02em'}}>{title}</div>
      {sub && <div style={{fontSize:11,color:'var(--ink-3)',marginTop:2}}>{sub}</div>}
    </div>
    {action}
  </div>
);

UI.Empty = ({ children='—' }) => <span style={{color:'var(--ink-3)'}}>{children}</span>;

UI.FormulaTag = ({ id }) => {
  const f = (window.CMV3.FORMULAS || []).find(x => x.id === id);
  if (!f) return <span style={{color:'var(--ink-3)',fontSize:10}}>{id}</span>;
  return (
    <span title={f.name}
          style={{display:'inline-flex',alignItems:'center',gap:5,padding:'1px 5px 1px 1px',background:'var(--bg-2)',border:'1px solid var(--line)',borderRadius:3,fontSize:10}}>
      <span style={{fontSize:9,fontFamily:'var(--f-mono)',fontWeight:700,letterSpacing:'.04em',color:'var(--accent)',background:'#fff',border:'1px solid var(--line)',borderRadius:2,padding:'1px 4px'}}>{f.tag}</span>
      <span style={{color:'var(--ink-1)'}}>{f.name}</span>
    </span>
  );
};

window.CMV3.UI = UI;
