/* Mock dataset modeled after the real /api/data response */
const STRATEGIES = [
  { id: 'tdx_12_26_9',     name: '通达信参数 · EMA(12,26,9)',     meta: 'current_model_vwap · 持仓5天' },
  { id: 'macd_12_26_9',    name: '参数组 M · EMA(12,26,9)',       meta: 'current_model_vwap · 持仓15天' },
  { id: 'macd_10_22_8_h15',name: '基准策略 · EMA(10,22,8)',       meta: 'current_model_vwap · 持仓15天' },
  { id: 'optuna_best',     name: 'Optuna 最优 · EMA(10/22/8)',    meta: 'current_model_vwap · 持仓10天' },
  { id: 'macd_14_30_11',   name: '参数组 L · EMA(14,30,11)',      meta: 'current_model_vwap · 持仓15天' },
];

const SUMMARY = { today_picks: 4, just_cross: 12, imminent: 18, holding: 9, with_history: 1842, total: 5108 };

const PARAMS = {
  macd_fast:    { label: '快线 EMA',  value: 12,   desc: '快速 EMA 周期，越短越灵敏', low_hint: '<10：噪音多',  high_hint: '>14：滞后明显' },
  macd_slow:    { label: '慢线 EMA',  value: 26,   desc: '慢速 EMA 周期，定义中期趋势', low_hint: '<22：易反复',  high_hint: '>30：响应慢' },
  macd_signal:  { label: '信号线',    value: 9,    desc: 'DEA 平滑参数', low_hint: '<7：信号毛糙', high_hint: '>11：太平滑' },
  holding_days: { label: '持仓周期',  value: 5,    desc: '金叉后默认卖出天数', low_hint: '<3：换手过频', high_hint: '>10：资金占用' },
  vol_ratio_min:{ label: '量比下限',  value: 1.0,  desc: '量比过滤（≥1 表示放量）', low_hint: '过松：拦不住缩量' },
  amt_ratio_min:{ label: '额比下限',  value: 1.0,  desc: '当日额/20日均额', low_hint: '过松：噪声大', high_hint: '过严：错过买点' },
  price_pos_max:{ label: '价格位置',  value: 0.70, desc: '收盘/60日最高，≤上限才入选', low_hint: '过严：候选少', high_hint: '过松：追高风险' },
  min_signals:  { label: '最小样本',  value: 5,    desc: '历史回测最少金叉次数', low_hint: '<3：统计不可信', high_hint: '>10：候选少' },
};

const INDUSTRIES = ['机械设备','有色金属','石油石化','传媒','轻工制造','电力设备','汽车','纺织服饰','建筑装饰','医药生物','电子','计算机','银行','食品饮料','基础化工','国防军工','农林牧渔','交通运输','钢铁'];
const ARCHETYPES = ['大盘价值','大盘均衡','大盘成长','中盘价值','中盘均衡','中盘成长','小盘价值','小盘均衡','小盘成长'];
const STATUSES = ['刚金叉','即将金叉','持仓期','刚死叉','等待'];

/* Seeded RNG so the table is stable across reloads */
function mulberry32(a) {
  return function() {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(42);
const pick = (arr) => arr[Math.floor(rand() * arr.length)];
const range = (lo, hi) => lo + rand() * (hi - lo);

const SEED_NAMES = [
  ['002009','天奇股份','机械设备'],['600489','中金黄金','有色金属'],['600028','中国石化','石油石化'],
  ['300781','因赛集团','传媒'],['002969','嘉美包装','轻工制造'],['002623','亚玛顿','电力设备'],
  ['001311','多利科技','汽车'],['600916','中国黄金','纺织服饰'],['300492','华图山鼎','建筑装饰'],
  ['001288','运机集团','机械设备'],['603008','ST喜临门','轻工制造'],['688399','硕世生物','医药生物'],
  ['688169','石头科技','电子'],['688102','斯瑞新材','有色金属'],['688480','赛恩斯','基础化工'],
  ['688047','龙芯中科','计算机'],['300788','中信出版','传媒'],['002865','钧达股份','电力设备'],
  ['605287','德才股份','建筑装饰'],['688636','智明达','国防军工'],['688522','纳睿雷达','国防军工'],
  ['688375','国博电子','国防军工'],['688311','盟升电子','国防军工'],['688283','坤恒顺维','国防军工'],
  ['600519','贵州茅台','食品饮料'],['000858','五粮液','食品饮料'],['601318','中国平安','银行'],
  ['300750','宁德时代','电力设备'],['000333','美的集团','轻工制造'],['002475','立讯精密','电子'],
  ['600036','招商银行','银行'],['601899','紫金矿业','有色金属'],['300059','东方财富','计算机'],
  ['688981','中芯国际','电子'],['600276','恒瑞医药','医药生物'],['002594','比亚迪','汽车'],
];

function genHorizons(seed) {
  const r2 = mulberry32(seed);
  const out = {};
  [5,10,15,20,30,60].forEach(h => {
    out[h] = {
      win_rate: 0.42 + r2() * 0.42,
      avg_ret: -0.04 + r2() * 0.24,
      avg_dd: -(0.03 + r2() * 0.14),
      calmar: 0.2 + r2() * 3.5,
      n: Math.floor(8 + r2() * 50),
    };
  });
  return out;
}

const STOCKS = SEED_NAMES.map(([code, name, ind], i) => {
  const r = mulberry32(i * 13 + 7);
  const status = (() => {
    const v = r();
    if (v < 0.05) return '刚金叉';
    if (v < 0.20) return '即将金叉';
    if (v < 0.45) return '持仓期';
    if (v < 0.52) return '刚死叉';
    return '等待';
  })();
  const has_history = r() > 0.18;
  const horizons = genHorizons(i + 1);
  const best_hp = [5,10,15,20,30,60][Math.floor(r() * 6)];
  const best = horizons[best_hp];
  const win_rate = has_history ? best.win_rate : null;
  const avg_ret  = has_history ? best.avg_ret : null;
  const avg_dd   = has_history ? best.avg_dd : null;
  const calmar   = has_history ? best.calmar : null;
  const sigcnt   = has_history ? Math.floor(8 + r() * 45) : (r() > 0.5 ? 2 : 0);
  const dif = -1.5 + r() * 3.5;
  const dea = dif - (-0.6 + r() * 1.2);
  const cur_close = 5 + r() * 80;
  const cur_amt_r20 = 0.6 + r() * 2.2;
  const cur_price60 = 0.32 + r() * 0.58;
  const days_event = status === '即将金叉' ? Math.ceil(r() * 5) : status === '刚金叉' ? Math.ceil(r() * 5) : null;
  const trade_buy_price  = has_history ? cur_close * (0.93 + r() * 0.05) : null;
  const trade_eval_price = has_history ? cur_close : null;
  const trade_ref_ret    = (has_history && trade_buy_price) ? (trade_eval_price - trade_buy_price) / trade_buy_price : null;
  const buy_score = Math.floor(20 + r() * 70);
  const is_buy = status === '刚金叉' && has_history && win_rate > 0.55 && calmar > 1.2 && r() > 0.55;
  const sell_hint = status === '持仓期' ? `还需 ${Math.ceil(r() * 8)} 天到期` : null;

  const last_gc_day = Math.floor(r() * 90);
  const last_gc_date = (() => {
    const d = new Date(2026, 4, 15 - last_gc_day);
    return d.toISOString().slice(0,10);
  })();

  return {
    code, name, industry: ind,
    archetype: pick(ARCHETYPES),
    status, has_history,
    history_status: has_history ? 'ok' : (sigcnt > 0 ? 'too_few_signals' : (r() > 0.5 ? 'insufficient_history' : 'no_signal')),
    signal_count: sigcnt,
    win_rate, avg_ret, avg_dd, calmar,
    horizons, best_holding_days: best_hp,
    cur_dif: dif, cur_dea: dea, cur_close,
    gap: dif,
    cur_amt_r20, cur_price60,
    dif_positive: dif > 0,
    days_event, last_gc_date,
    holder_chg: -0.08 + r() * 0.16,
    f1_hit: r() > 0.55, f3_hit: r() > 0.65, f5_hit: r() > 0.45,
    formula_hit_count: 0, // filled below
    trade_buy_price, trade_eval_price, trade_ref_ret,
    trade_ref_holding_days: best_hp,
    trade_buy_date: has_history ? last_gc_date : null,
    trade_eval_date: has_history ? new Date(2026, 4, 16).toISOString().slice(0,10) : null,
    trade_reached_target: has_history && r() > 0.5,
    trade_remaining_days: Math.ceil(r() * 5),
    buy_score, is_buy_point: is_buy, sell_hint,
    filter_pass: r() > 0.35,
  };
}).map(s => ({ ...s, formula_hit_count: (s.f1_hit?1:0) + (s.f3_hit?1:0) + (s.f5_hit?1:0) }));

/* MACD chart series for the detail panel — one shared sample */
function genChartSeries(seed = 1) {
  const r = mulberry32(seed);
  const n = 90;
  const dates = [], close = [], dif = [], dea = [], bar = [], crosses = [];
  let price = 20 + r() * 30;
  let d = 0, ema = 0;
  for (let i = 0; i < n; i++) {
    price *= 1 + (r() - 0.48) * 0.035;
    close.push(+price.toFixed(2));
    const date = new Date(2026, 4, 15);
    date.setDate(date.getDate() - (n - 1 - i));
    dates.push(date.toISOString().slice(5,10));
    d = d * 0.78 + (r() - 0.5) * 0.6;
    ema = ema * 0.85 + d * 0.15;
    dif.push(+d.toFixed(3));
    dea.push(+ema.toFixed(3));
    bar.push(+(d - ema).toFixed(3));
    if (i > 0) {
      const prevDif = dif[i-1], prevDea = dea[i-1];
      if (prevDif < prevDea && d > ema) crosses.push({ idx: i, type: 'golden' });
      if (prevDif > prevDea && d < ema) crosses.push({ idx: i, type: 'death' });
    }
  }
  return { dates, close, dif, dea, bar, crosses };
}

window.MOCK = { STRATEGIES, SUMMARY, PARAMS, STOCKS, genChartSeries };
