/* Chunky Monkey v2 · 跟随机构选股
   产品姿态: 顶部数据健康摘要 → 主区"今日可跟随股票"排行 → 副区机构面板
   核心问题: 模型基于机构持仓信号, 跑出今天最值得跟随的股票
*/

const { useState, useMemo, useEffect } = React;

/* =============== 数据 mock =============== */

// 机构: 评估其历史 buy 事件 60d 表现
const INST_DB = [
  { id:'sb105',  name:'全国社保 105 组合',  alias:'社保 105',     type:'社保',     events:48, win:0.625, avg60:0.084, med60:0.052, recent:[3,5,4,6,8,7,9,8,11,10,12,9],  trust:0.92 },
  { id:'huijin', name:'中央汇金',           alias:'汇金',         type:'国家队',   events:24, win:0.667, avg60:0.112, med60:0.091, recent:[2,3,4,5,4,6,7,8,7,9,10,11],   trust:0.95 },
  { id:'big2',   name:'国家大基金二期',     alias:'大基金 II',    type:'国家大基金',events:18, win:0.722, avg60:0.185, med60:0.142, recent:[1,2,3,4,5,5,6,7,8,9,10,11],   trust:0.90 },
  { id:'jpm',    name:'摩根资管 - QFII',    alias:'摩根 QFII',   type:'QFII',     events:62, win:0.581, avg60:0.092, med60:0.064, recent:[4,6,5,7,8,6,9,8,10,9,11,12],  trust:0.86 },
  { id:'sb117',  name:'全国社保 117 组合',  alias:'社保 117',     type:'社保',     events:42, win:0.595, avg60:0.072, med60:0.041, recent:[3,4,5,4,6,5,7,8,7,9,8,10],    trust:0.88 },
  { id:'wang',   name:'王某 (个人股东)',    alias:'王某',         type:'牛散',     events:14, win:0.714, avg60:0.158, med60:0.110, recent:[2,3,4,5,4,6,7,9,8,10,9,12],   trust:0.72 },
  { id:'cic',    name:'中投 - 主权',        alias:'中投',         type:'国家队',   events:22, win:0.591, avg60:0.061, med60:0.034, recent:[3,4,4,5,5,6,5,7,6,8,7,9],     trust:0.82 },
  { id:'ms',     name:'摩根士丹利 - QFII',  alias:'摩根 MS',     type:'QFII',     events:54, win:0.537, avg60:0.058, med60:0.028, recent:[4,5,4,6,5,7,6,5,7,6,8,7],     trust:0.78 },
  { id:'gic',    name:'新加坡 GIC',         alias:'GIC',          type:'QFII',     events:38, win:0.605, avg60:0.094, med60:0.058, recent:[3,4,5,6,5,7,6,8,7,9,8,10],    trust:0.84 },
  { id:'lifeIns',name:'中国人寿保险',       alias:'人寿',         type:'保险',     events:84, win:0.524, avg60:0.042, med60:0.018, recent:[4,5,4,6,5,4,5,6,5,4,5,6],     trust:0.74 },
];

// 今日推荐股票 — 每只附 model attribution
const PICKS = [
  {
    code:'600519', name:'贵州茅台', sector:'白酒', price:1681.20, chg:0.0142, score:92, rank:1,
    insts:['sb105','huijin','jpm'],
    instCount:3, totalSharesAdded:'+2.4M', costAvg:1620,
    features: [
      { k:'机构共识 (3 家高分机构买入)', v:+38 },
      { k:'机构样本质量 (avg trust 0.91)', v:+12 },
      { k:'买入新鲜度 (5 日内)',          v:+18 },
      { k:'仓位变动 (+2.4M 股 / 流通 0.18%)', v:+14 },
      { k:'基本面 (ROE 32%)',              v:+10 },
      { k:'估值偏高',                     v:-6 },
      { k:'行业拥挤度',                   v:+0 },
    ],
    ret60_hist: 0.082, // 同样组合机构在历史上类似配置下的 60d 平均收益
    risk: 'low', tags: ['共识强','样本足','低估值压力小']
  },
  {
    code:'300750', name:'宁德时代', sector:'电池', price:218.40, chg:0.0215, score:88, rank:2,
    insts:['big2','jpm','wang'],
    instCount:3, totalSharesAdded:'+5.8M', costAvg:212,
    features: [
      { k:'国家大基金 II 建仓 (高 trust)', v:+32 },
      { k:'机构样本质量 (avg trust 0.83)', v:+10 },
      { k:'买入新鲜度 (3 日内)',          v:+22 },
      { k:'仓位变动 (+5.8M 股 / 流通 0.32%)', v:+18 },
      { k:'技术面 (突破 60D 平台)',        v:+8 },
      { k:'估值合理',                     v:+4 },
      { k:'存在大宗折价',                 v:-6 },
    ],
    ret60_hist: 0.118,
    risk: 'low', tags: ['国家队入场','新鲜度高']
  },
  {
    code:'600036', name:'招商银行', sector:'银行', price:38.71, chg:0.0035, score:81, rank:3,
    insts:['huijin','sb105','sb117','lifeIns'],
    instCount:4, totalSharesAdded:'+12.0M', costAvg:38.2,
    features: [
      { k:'4 家机构同时增持 (共识)',       v:+34 },
      { k:'机构样本质量 (avg trust 0.87)', v:+12 },
      { k:'买入新鲜度 (4 日内)',          v:+12 },
      { k:'仓位变动 (+12M 股)',           v:+12 },
      { k:'股息率 5.4%',                  v:+8 },
      { k:'银行板块拥挤',                 v:-3 },
    ],
    ret60_hist: 0.041,
    risk: 'low', tags: ['多机构共识','防御']
  },
  {
    code:'000333', name:'美的集团', sector:'家电', price:68.90, chg:0.0108, score:79, rank:4,
    insts:['sb105','jpm'],
    instCount:2, totalSharesAdded:'+1.2M', costAvg:67.8,
    features: [
      { k:'2 家高 trust 机构买入',         v:+24 },
      { k:'机构样本质量 (avg trust 0.89)', v:+12 },
      { k:'买入新鲜度 (2 日内)',          v:+24 },
      { k:'仓位变动 (+1.2M 股)',          v:+8 },
      { k:'基本面 (毛利稳定)',            v:+8 },
      { k:'样本数偏少',                   v:-3 },
    ],
    ret60_hist: 0.058,
    risk: 'med', tags: ['新鲜度高','样本偏少']
  },
  {
    code:'603259', name:'药明康德', sector:'CXO', price:85.40, chg:0.0192, score:77, rank:5,
    insts:['sb117','wang'],
    instCount:2, totalSharesAdded:'+880K', costAvg:84.0,
    features: [
      { k:'社保 117 + 牛散同步建仓',       v:+22 },
      { k:'机构样本质量 (avg trust 0.80)', v:+8 },
      { k:'买入新鲜度 (3 日内)',          v:+20 },
      { k:'仓位变动 (+88 万股)',          v:+8 },
      { k:'技术面回暖',                   v:+6 },
      { k:'板块波动率高',                 v:-5 },
      { k:'样本数偏少 (2 家)',            v:-4 },
    ],
    ret60_hist: 0.094,
    risk: 'med', tags: ['新建仓','波动']
  },
  {
    code:'002594', name:'比亚迪', sector:'汽车', price:245.12, chg:-0.0042, score:74, rank:6,
    insts:['big2','gic'],
    instCount:2, totalSharesAdded:'+2.2M', costAvg:240,
    features: [
      { k:'大基金 + GIC 共同增持',         v:+24 },
      { k:'机构样本质量 (avg trust 0.87)', v:+10 },
      { k:'买入新鲜度 (6 日内)',          v:+10 },
      { k:'仓位变动',                     v:+10 },
      { k:'高管减持反向信号',             v:-8 },
      { k:'估值合理',                     v:+4 },
      { k:'样本数偏少',                   v:-3 },
    ],
    ret60_hist: 0.062,
    risk: 'med', tags: ['内外信号矛盾']
  },
  {
    code:'601318', name:'中国平安', sector:'保险', price:49.22, chg:-0.0031, score:62, rank:7,
    insts:['sb105'],
    instCount:1, totalSharesAdded:'+340K', costAvg:48.8,
    features: [
      { k:'1 家机构小幅增持',              v:+8 },
      { k:'机构样本质量 (trust 0.92)',    v:+10 },
      { k:'买入新鲜度 (8 日)',             v:+4 },
      { k:'北向连续减持反向',             v:-12 },
      { k:'板块估值低',                   v:+6 },
      { k:'样本不足',                     v:-8 },
    ],
    ret60_hist: -0.012,
    risk: 'high', tags: ['样本单薄','反向信号']
  },
];

// 数据健康 — 顶部摘要
const HEALTH = {
  ingest:    { status:'ok',   detail:'最近批 06:14, 5 个源全部成功' },
  warehouse: { status:'ok',   detail:'fact_holders_event 8 分钟前更新, 行数 +124' },
  model:     { status:'warn', detail:'champion_v3 回测样本数 -8% (vs 上周)' },
  signal:    { status:'ok',   detail:'04:48 推送 7 只, 已下发' },
};

// 每天的事件数 mock — sparkline
const EVENTS_30D = [82,75,90,88,72,98,104,95,82,78,88,102,96,84,78,90,88,82,76,94,108,98,86,92,88,80,74,86,98,124];

// 模型回测 — 滚动 30d 胜率
const MODEL_30D = [0.58,0.59,0.61,0.60,0.62,0.63,0.62,0.64,0.65,0.63,0.62,0.61,0.63,0.64,0.66,0.65,0.63,0.61,0.62,0.63,0.62,0.60,0.59,0.60,0.62,0.63,0.62,0.61,0.60,0.59];

const TYPE_TONES = {
  '社保':       { bg:'#E8F2EC', fg:'#1B5E20' },
  '北向':       { bg:'#E3F2FD', fg:'#0D47A1' },
  'QFII':       { bg:'#F3E5F5', fg:'#4A148C' },
  '国家队':     { bg:'#FFF3E0', fg:'#E65100' },
  '国家大基金': { bg:'#FBE9E7', fg:'#BF360C' },
  '牛散':       { bg:'#FFF8E1', fg:'#F57F17' },
  '保险':       { bg:'#E0F2F1', fg:'#004D40' },
  '基金':       { bg:'#F3F4F6', fg:'#374151' },
};

function getInst(id) { return INST_DB.find(i => i.id === id); }

/* =============== 共享原子 =============== */

function TypePill({type}) {
  const t = TYPE_TONES[type] || TYPE_TONES['基金'];
  return <span className="tag" style={{background:t.bg,color:t.fg,fontFamily:'var(--f-sans)'}}>{type}</span>;
}

function StatusDot({status}) {
  const map = { ok:'var(--c-accent)', warn:'var(--c-warn)', bad:'var(--c-bad)' };
  return <span style={{display:'inline-block',width:6,height:6,borderRadius:3,background:map[status]||'var(--c-ink-40)'}}/>;
}

function MiniSpark({data, height=24, width=80, tone}) {
  const max = Math.max(...data), min = Math.min(...data);
  const xw = width / Math.max(1, data.length-1);
  const sy = v => height - ((v - min) / Math.max(0.001, max-min)) * (height-4) - 2;
  const path = data.map((v,i) => `${i?'L':'M'}${i*xw} ${sy(v)}`).join(' ');
  const last = data[data.length-1] >= data[0];
  const color = tone || (last ? 'var(--c-up)' : 'var(--c-down)');
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{width,height,display:'block'}}>
      <path d={path} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round"/>
      <circle cx={(data.length-1)*xw} cy={sy(data[data.length-1])} r="2" fill={color}/>
    </svg>
  );
}

function InstChip({inst, size='md', onClick}) {
  if (!inst) return null;
  const t = TYPE_TONES[inst.type] || TYPE_TONES['基金'];
  const winPct = Math.round(inst.win*100);
  const tonePill = inst.win >= 0.6 ? 'var(--c-accent)' : inst.win >= 0.5 ? 'var(--c-warn)' : 'var(--c-bad)';
  return (
    <button onClick={onClick} style={{
      display:'inline-flex',alignItems:'center',gap:6,
      height:size==='sm'?22:26,padding:'0 8px',borderRadius:13,
      background:t.bg,color:t.fg,
      fontSize:size==='sm'?11:12,fontWeight:500,
      border:'1px solid '+t.bg,
    }} onMouseEnter={e=>e.currentTarget.style.borderColor=t.fg+'30'}
       onMouseLeave={e=>e.currentTarget.style.borderColor=t.bg}>
      <span>{inst.alias}</span>
      <span style={{
        display:'inline-flex',alignItems:'center',gap:3,
        background:'rgba(255,255,255,0.55)',padding:'1px 5px',borderRadius:7,
        fontSize:10,fontFamily:'var(--f-mono)',color:tonePill,fontWeight:600,
      }}>
        <span style={{width:4,height:4,borderRadius:2,background:tonePill}}/>
        {winPct}%
      </span>
    </button>
  );
}

window.CMV2 = {
  INST_DB, PICKS, HEALTH, EVENTS_30D, MODEL_30D, TYPE_TONES,
  getInst, TypePill, StatusDot, MiniSpark, InstChip,
};
