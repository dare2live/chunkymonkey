/* Chunky Monkey v3 · mock data
   字段命名严格对齐 开发手册.md
   每条数据都标注来源表（注释中），避免"展示但无代码支撑"
*/

const CMV3 = {};

/* ============ 跑批元信息 (mart_pipeline_run_manifest) ============ */
CMV3.RUN_META = {
  plan_date:     '2026-05-09',          // T+1
  signal_date:   '2026-05-08',          // T
  built_at:      '2026-05-08 17:42:18',
  duration_min:  48,
  nav:           1032450,
  nav_chg_pct:   0.0082,
  vs_hs300_pct:  0.018,
  vs_eq_pct:     0.022,
  challenger_pending: 1,                 // mart_model_composite_score
  system_alerts: [],                     // mart_business_alert
};

/* ============ 7 公式 (fact_technical_trigger.formula_id) ============ */
CMV3.FORMULAS = [
  { id:'turtle_20',                   name:'海龟突破法 20 日',  tag:'TT', hit_today:23, win_rate:0.68, horizon:5,  state_dist:null },
  { id:'macd_golden_cross',           name:'MACD 金叉',         tag:'MA', hit_today:47, win_rate:0.78, horizon:10, state_dist:{'刚金叉':12,'持仓中':35} },
  { id:'ma_breakout_long_low',        name:'长期低位突破',      tag:'BO', hit_today:8,  win_rate:0.72, horizon:20, state_dist:null },
  { id:'iterative_signal_filtered',   name:'多级迭代信号',      tag:'IT', hit_today:15, win_rate:0.65, horizon:15, state_dist:null },
  { id:'dynamic_ma_iterative_cross',  name:'动态均线迭代金叉',  tag:'DM', hit_today:11, win_rate:0.61, horizon:15, state_dist:null },
  { id:'sector_dual_confirm',         name:'板块动量 + 双确认', tag:'SD', hit_today:31, win_rate:0.65, horizon:20, state_dist:null },
  { id:'institution_buy_general',     name:'机构介入 (画像版)', tag:'IG', hit_today:19, win_rate:0.70, horizon:60, state_dist:null },
  { id:'institution_pair_backtest',   name:'机构-股票配对回测', tag:'IP', hit_today:7,  win_rate:0.74, horizon:60, state_dist:null },
];

/* ============ 股票池 — 完整画像 (fact_stock_type_daily + mart_stock_trade_plan + mart_stock_selection_summary) ============ */
function mkStock(o) {
  return {
    // 基础 — dim_active_a_stock
    code: o.code, name: o.name, price: o.price, chg_pct: o.chg ?? 0,
    // 六维画像
    industry_l1: o.industry,                          // dim_stock_tdx_industry
    primary_type: o.primaryType,                      // fact_stock_type_daily.primary_type
    secondary_types: o.secondaryTypes || [],
    fundamental_stage: o.fundStage,                   // dim_stock_stage_latest
    fundamental_stage_days: o.fundStageDays,
    technical_stage: o.techStage,                     // 新建 fact_technical_stage
    technical_stage_days: o.techStageDays,
    valuation_upside_pct: o.upside,                   // mart_valuation_aggregate
    valuation_pe: o.pe, valuation_pe_pctile: o.pePctile,
    institution_signal: o.instSignal,                 // mart_institution_signal
    formulas_hit: o.formulasHit || [],                // fact_technical_trigger
    // 决策
    action: o.action, score: o.score, rank: o.rank,
    weight_pct: o.weightPct,
    // Trade plan — mart_stock_trade_plan
    entry_target_price: o.entryTarget, entry_aggressive_price: o.entryAgg, entry_max_price: o.entryMax,
    exit_target_1: o.exitT1, exit_target_2: o.exitT2, exit_stop: o.exitStop,
    risk_reward_ratio: o.rr, expected_horizon_days: o.horizon,
    // 理由
    reason: o.reason,                                 // reason_codes_json → 白话
    similar_n: o.similarN, similar_win: o.similarWin, // mart_cohort_outcome
    // 优选追踪 — mart_stock_selection_summary
    selection_30d: o.sel30, selection_total: o.selTotal, selection_win_rate: o.selWinRate,
    last_outcome: o.lastOutcome,
    // 持仓 — fact_paper_position
    holding_days: o.holdingDays, holding_return_pct: o.holdRet, rr_realized: o.rrReal,
    // 风险
    risk_note: o.riskNote,
  };
}

CMV3.STOCKS = [
  mkStock({
    code:'600519', name:'贵州茅台', price:1681.20, chg:0.0142, industry:'白酒',
    primaryType:'价值修复', secondaryTypes:['业绩驱动'],
    fundStage:'温和验证', fundStageDays:28, techStage:'2 上升', techStageDays:28,
    upside:18, pe:28, pePctile:0.32,
    instSignal:{score:82, n_insts:3, top:['社保 105','汇金','摩根 QFII']},
    formulasHit:[
      {id:'turtle_20', strength:0.82, win:0.68, horizon:5},
      {id:'macd_golden_cross', strength:0.75, win:0.78, horizon:10, state:'刚金叉'},
      {id:'sector_dual_confirm', strength:0.70, win:0.65, horizon:20},
    ],
    action:'BUY', score:92, rank:1, weightPct:4.5,
    entryTarget:1712, entryAgg:1745, entryMax:1768, exitT1:1850, exitT2:1980, exitStop:1640,
    rr:2.85, horizon:60,
    reason:'跟踪机构 5 月初已介入 + 当前处于震荡阶段即将突破',
    similarN:132, similarWin:0.78,
    sel30:3, selTotal:8, selWinRate:0.75, lastOutcome:'win',
  }),
  mkStock({
    code:'300750', name:'宁德时代', price:218.40, chg:0.0215, industry:'电池',
    primaryType:'技术突破', secondaryTypes:['业绩驱动'],
    fundStage:'温和验证', fundStageDays:42, techStage:'1.5 突破中', techStageDays:6,
    upside:22, pe:24, pePctile:0.18,
    instSignal:{score:88, n_insts:3, top:['大基金 II','摩根 QFII','王某']},
    formulasHit:[
      {id:'turtle_20', strength:0.88, win:0.68, horizon:5},
      {id:'macd_golden_cross', strength:0.71, win:0.78, horizon:10, state:'刚金叉'},
      {id:'institution_buy_general', strength:0.85, win:0.70, horizon:60},
    ],
    action:'BUY', score:88, rank:2, weightPct:4.2,
    entryTarget:212, entryAgg:218, entryMax:225, exitT1:240, exitT2:265, exitStop:198,
    rr:2.42, horizon:20,
    reason:'国家大基金 II 入场 + 突破 60D 平台 + 量能放大',
    similarN:86, similarWin:0.72,
    sel30:2, selTotal:5, selWinRate:0.80, lastOutcome:'win',
  }),
  mkStock({
    code:'600036', name:'招商银行', price:38.71, chg:0.0035, industry:'银行',
    primaryType:'价值修复',
    fundStage:'温和验证', fundStageDays:64, techStage:'2 上升', techStageDays:18,
    upside:16, pe:6.2, pePctile:0.12,
    instSignal:{score:76, n_insts:4, top:['汇金','社保 105','社保 117','人寿']},
    formulasHit:[
      {id:'institution_buy_general', strength:0.92, win:0.70, horizon:60},
      {id:'sector_dual_confirm', strength:0.62, win:0.65, horizon:20},
    ],
    action:'BUY', score:81, rank:3, weightPct:3.8,
    entryTarget:38.20, entryAgg:38.71, entryMax:39.50, exitT1:42, exitT2:45.50, exitStop:36.20,
    rr:2.10, horizon:60,
    reason:'4 家机构同时增持 + 股息率 5.4% + 银行板块拐点',
    similarN:204, similarWin:0.65,
    sel30:4, selTotal:12, selWinRate:0.67, lastOutcome:'win',
  }),
  mkStock({
    code:'002594', name:'比亚迪', price:245.12, chg:-0.0042, industry:'汽车',
    primaryType:'周期复苏',
    fundStage:'已充分演绎', fundStageDays:88, techStage:'3 顶部', techStageDays:14,
    upside:6, pe:32, pePctile:0.78,
    instSignal:{score:32, n_insts:0, top:[]},
    formulasHit:[],
    action:'SELL', score:42, rank:null, weightPct:0,
    holdingDays:62, holdRet:-0.032, rrReal:-0.4,
    reason:'Stage 2→3 转换 + 跌破 MA30 + 已亏 -3.2%',
    sel30:0, selTotal:6, selWinRate:0.50, lastOutcome:'loss',
    riskNote:'触发硬止损',
  }),
  mkStock({
    code:'603259', name:'药明康德', price:85.40, chg:0.0192, industry:'CXO',
    primaryType:'事件驱动',
    fundStage:'温和验证', fundStageDays:12, techStage:'2 上升', techStageDays:8,
    upside:24, pe:22, pePctile:0.28,
    instSignal:{score:68, n_insts:2, top:['社保 117','王某']},
    formulasHit:[
      {id:'macd_golden_cross', strength:0.78, win:0.78, horizon:10, state:'刚金叉'},
      {id:'institution_pair_backtest', strength:0.81, win:0.74, horizon:60},
    ],
    action:'HOLD', score:77, rank:4, weightPct:3.2,
    holdingDays:18, holdRet:0.058, rrReal:0.9,
    reason:'已建仓 18 天 / +5.8% / 距 target 1 还有 +12%',
    sel30:2, selTotal:4, selWinRate:0.75, lastOutcome:'active',
  }),
  // WATCH samples
  mkStock({ code:'000333', name:'美的集团', price:68.9,  chg:0.011,  industry:'家电',
    primaryType:'价值修复', fundStage:'温和验证', fundStageDays:48, techStage:'1.5 突破中', techStageDays:4,
    upside:14, pe:14, pePctile:0.42, instSignal:{score:62,n_insts:2,top:['社保 105','摩根 QFII']},
    formulasHit:[{id:'turtle_20',strength:0.66,win:0.68,horizon:5}],
    action:'WATCH', score:72, rank:5, reason:'新鲜度高但样本偏少, 待形态确认',
    sel30:1, selTotal:3, selWinRate:0.67, lastOutcome:'win', }),
  mkStock({ code:'601318', name:'中国平安', price:49.22, chg:-0.003, industry:'保险',
    primaryType:'价值修复', fundStage:'温和验证', fundStageDays:36, techStage:'1 底部', techStageDays:22,
    upside:28, pe:8, pePctile:0.08, instSignal:{score:42,n_insts:1,top:['社保 105']},
    formulasHit:[{id:'ma_breakout_long_low',strength:0.58,win:0.72,horizon:20}],
    action:'WATCH', score:64, rank:6, reason:'估值低 + 单机构, 等技术确认',
    sel30:0, selTotal:2, selWinRate:0.50, lastOutcome:'loss', }),
];

/* ============ 当前持仓 (fact_paper_position) ============ */
CMV3.HOLDINGS = [
  { code:'600519', name:'贵州茅台', days:32, ret:0.083,  rr:1.9, stage:'2 上升', toT1:-0.021, weight:0.048 },
  { code:'300750', name:'宁德时代', days:8,  ret:0.024,  rr:0.5, stage:'1.5 突破', toT1:-0.082, weight:0.042 },
  { code:'600036', name:'招商银行', days:46, ret:0.052,  rr:1.2, stage:'2 上升', toT1:-0.034, weight:0.044 },
  { code:'603259', name:'药明康德', days:18, ret:0.058,  rr:0.9, stage:'2 上升', toT1:-0.041, weight:0.032 },
  { code:'002594', name:'比亚迪',   days:62, ret:-0.032, rr:-0.4,stage:'3 顶部', toT1: 0.018, weight:0.038, action:'SELL' },
];

/* ============ NAV 曲线 (mart_paper_nav) ============ */
CMV3.NAV_SERIES = (() => {
  const arr = []; let nav = 1000000, hs = 1.00, eq = 1.00;
  for (let i=0; i<120; i++) {
    nav *= (1 + (Math.sin(i/8)*0.005 + (Math.random()-0.45)*0.012));
    hs  *= (1 + (Math.sin(i/10)*0.004 + (Math.random()-0.5)*0.010));
    eq  *= (1 + (Math.sin(i/9)*0.0035 + (Math.random()-0.5)*0.008));
    arr.push({ i, nav, hs300: hs*1000000, eq: eq*1000000 });
  }
  arr[arr.length-1].nav = 1032450; return arr;
})();

/* ============ 6 信号 60d 滚动 Rank IC (mart_signal_ic) ============ */
CMV3.SIGNAL_IC = [
  { signal:'fundamental_stage', ic:0.42 },
  { signal:'technical_stage',   ic:0.38 },
  { signal:'ranking_short',     ic:0.31 },
  { signal:'institution',       ic:0.22 },
  { signal:'ranking_long',      ic:0.18 },
  { signal:'valuation',         ic:0.15 },
];

/* ============ P&L 归因 (mart_decision_outcome) ============ */
CMV3.PL_ATTR = [
  { k:'alpha 贡献',    v:18420, pct:0.58 },
  { k:'行业 beta',     v:8210,  pct:0.26 },
  { k:'风格 beta',     v:3100,  pct:0.10 },
  { k:'流动性 / 成本', v:1800,  pct:0.05 },
];

/* ============ 6 KPI (mart_paper_nav 聚合) ============ */
CMV3.KPIS = {
  excess_pct: 0.052, sharpe: 0.78, max_dd_pct: -0.081,
  monthly_win: 0.62, turnover: 1.45, top_industry_pct: 0.28,
};

/* ============ 风险监控 (mart_portfolio_risk_snapshot) ============ */
CMV3.RISK = {
  current_dd_pct: -0.043, monthly_turnover: 1.45,
  top_industry: '半导体', top_industry_pct: 0.28,
  stage2_pct: 0.76, cash_pct: 0.10,
};

/* ============ 机构 (mart_institution_profile) ============ */
CMV3.INSTITUTIONS = [
  { id:'sb105',  name:'全国社保 105 组合', alias:'社保 105', type:'社保',     win30:0.62, win60:0.62, win90:0.58, stability:0.84, n_stocks:42, tracked:true  },
  { id:'huijin', name:'中央汇金',          alias:'汇金',     type:'国家队',   win30:0.71, win60:0.67, win90:0.65, stability:0.92, n_stocks:38, tracked:true  },
  { id:'big2',   name:'国家大基金二期',    alias:'大基金 II',type:'国家大基金',win30:0.78, win60:0.72, win90:0.68, stability:0.88, n_stocks:24, tracked:true  },
  { id:'jpm',    name:'摩根资管 QFII',    alias:'摩根 QFII',type:'QFII',     win30:0.55, win60:0.58, win90:0.56, stability:0.78, n_stocks:62, tracked:true  },
  { id:'sb117',  name:'全国社保 117 组合', alias:'社保 117', type:'社保',     win30:0.61, win60:0.60, win90:0.57, stability:0.82, n_stocks:48, tracked:true  },
  { id:'wang',   name:'王某 (个人股东)',   alias:'王某',     type:'牛散',     win30:0.74, win60:0.71, win90:0.66, stability:0.65, n_stocks:14, tracked:true  },
  { id:'gic',    name:'新加坡 GIC',        alias:'GIC',      type:'QFII',     win30:0.62, win60:0.60, win90:0.58, stability:0.80, n_stocks:38, tracked:false },
  { id:'lifeIns',name:'中国人寿',          alias:'人寿',     type:'保险',     win30:0.52, win60:0.52, win90:0.51, stability:0.74, n_stocks:84, tracked:false },
];

/* ============ 历史优选 (mart_stock_selection_outcome × 单股) ============ */
CMV3.SELECTION_HISTORY = [
  { selectDate:'2026-04-15', formula:'MACD 金叉',     horizon:60, retPct: 0.123,  ddPct:-0.021, daysToT1:18, outcome:'win'  },
  { selectDate:'2026-03-08', formula:'海龟突破 20',   horizon:20, retPct: 0.087,  ddPct:-0.015, daysToT1:12, outcome:'win'  },
  { selectDate:'2026-02-12', formula:'板块动量',      horizon:15, retPct:-0.023,  ddPct:-0.058, daysToT1:null, outcome:'loss' },
  { selectDate:'2026-01-09', formula:'MACD 金叉',     horizon:60, retPct: 0.156,  ddPct:-0.042, daysToT1:25, outcome:'win'  },
  { selectDate:'2025-11-22', formula:'机构介入',      horizon:60, retPct: 0.094,  ddPct:-0.038, daysToT1:32, outcome:'win'  },
  { selectDate:'2025-09-08', formula:'MACD 金叉',     horizon:60, retPct: 0.072,  ddPct:-0.024, daysToT1:28, outcome:'win'  },
  { selectDate:'2025-07-15', formula:'海龟突破 20',   horizon:20, retPct:-0.054,  ddPct:-0.078, daysToT1:null, outcome:'loss' },
  { selectDate:'2025-05-04', formula:'MACD 金叉',     horizon:60, retPct: 0.108,  ddPct:-0.032, daysToT1:22, outcome:'win'  },
];

/* ============ 形态×公式 适配矩阵 (mart_stage_formula_fitness) ============ */
CMV3.FITNESS = (() => {
  const stages = [
    { fund:'温和验证',   tech:'1.5' },
    { fund:'温和验证',   tech:'2'   },
    { fund:'未充分演绎', tech:'1.5' },
    { fund:'未充分演绎', tech:'2'   },
    { fund:'已演绎',     tech:'3'   },
    { fund:'失效破坏',   tech:'4'   },
  ];
  const fIds = CMV3.FORMULAS.map(f => f.id);
  const out = [];
  stages.forEach((s, si) => {
    fIds.forEach((fid, fi) => {
      // 失效破坏行整体样本不足
      if (s.fund === '失效破坏') return;
      // 部分长期低位突破 / 多级迭代在某些形态样本太少
      if (fid === 'ma_breakout_long_low' && s.fund !== '未充分演绎') return;
      const base = 0.45 + ((si*7 + fi*13) % 30) / 100;
      const winRate = Math.min(0.82, Math.max(0.38, base + (s.tech==='2'?0.08:0) + (s.fund==='温和验证'?0.05:0)));
      const horizons = [10, 20, 30, 60];
      const bestH = horizons[(si+fi) % horizons.length];
      const n = 30 + ((si*11 + fi*17) % 220);
      out.push({
        fund: s.fund, tech: s.tech, formula_id: fid, holding_days: bestH,
        n_signals: n, win_rate: winRate,
        avg_ret: (winRate - 0.5) * (bestH/30) * 0.06,
        avg_dd: -((1-winRate) * (bestH/30) * 0.04),
      });
    });
  });
  // 标记每个 (fund,tech) 下的 best
  stages.forEach(s => {
    const rows = out.filter(r => r.fund===s.fund && r.tech===s.tech);
    if (!rows.length) return;
    rows.sort((a,b) => b.win_rate - a.win_rate);
    rows[0].is_recommended = true;
  });
  return out;
})();
CMV3.STAGES = [
  { fund:'温和验证',   tech:'1.5' },
  { fund:'温和验证',   tech:'2'   },
  { fund:'未充分演绎', tech:'1.5' },
  { fund:'未充分演绎', tech:'2'   },
  { fund:'已演绎',     tech:'3'   },
  { fund:'失效破坏',   tech:'4'   },
];

/* ============ 全市场显著股东 (mart_significant_holder_all) ============ */
CMV3.SIGNIFICANT_HOLDERS = [
  { id:'sh_north',     name:'北向资金',         type:'北向', holdings:156, win60:0.65, stability:0.92, last_action:'2026-05-08 增持 12 只', tracked:true },
  { id:'sh_huijin',    name:'中央汇金',         type:'国家队', holdings:48,  win60:0.58, stability:0.88, last_action:'2026-05-05 增持 ETF',    tracked:true },
  { id:'sh_yfd_blue',  name:'易方达蓝筹',       type:'公募', holdings:28,  win60:0.72, stability:0.85, last_action:'2026-04-15 新建 600519', tracked:true },
  { id:'sh_qfii_msg',  name:'摩根 QFII',        type:'QFII', holdings:32,  win60:0.68, stability:0.79, last_action:'2026-04-22 减持 招行',    tracked:false },
  { id:'sh_pingan',    name:'平安人寿',         type:'保险', holdings:42,  win60:0.59, stability:0.81, last_action:'2026-04-30 增持 4 只',    tracked:false },
  { id:'sh_zhang',     name:'章建平 (牛散)',    type:'牛散', holdings:8,   win60:0.62, stability:0.66, last_action:'2026-04-18 新建 化工股',  tracked:false },
  { id:'sh_sb105',     name:'社保 105 组合',    type:'社保', holdings:35,  win60:0.62, stability:0.84, last_action:'2026-05-06 增持 茅台',    tracked:true },
];

/* ============ 全市场优选追踪聚合 ============ */
CMV3.SELECTION_BOARD = [
  { code:'600519', name:'贵州茅台',   n30:3, n_total:8,  win:0.75, avg_ret:0.093, last_outcome:'win',    last_date:'2026-04-15', last_formula:'macd_golden_cross' },
  { code:'002594', name:'比亚迪',     n30:2, n_total:6,  win:0.67, avg_ret:0.072, last_outcome:'active',  last_date:'2026-05-02', last_formula:'turtle_20' },
  { code:'601012', name:'隆基绿能',   n30:4, n_total:11, win:0.55, avg_ret:0.041, last_outcome:'loss',    last_date:'2026-04-28', last_formula:'sector_dual_confirm' },
  { code:'000333', name:'美的集团',   n30:1, n_total:5,  win:0.80, avg_ret:0.108, last_outcome:'win',     last_date:'2026-03-12', last_formula:'macd_golden_cross' },
  { code:'600036', name:'招商银行',   n30:2, n_total:7,  win:0.71, avg_ret:0.052, last_outcome:'win',     last_date:'2026-04-08', last_formula:'institution_pair_backtest' },
  { code:'300750', name:'宁德时代',   n30:3, n_total:9,  win:0.56, avg_ret:0.038, last_outcome:'active',  last_date:'2026-05-05', last_formula:'iterative_signal_filtered' },
  { code:'601318', name:'中国平安',   n30:0, n_total:4,  win:0.50, avg_ret:-0.012,last_outcome:'loss',    last_date:'2026-03-22', last_formula:'turtle_20' },
  { code:'000858', name:'五粮液',     n30:2, n_total:6,  win:0.83, avg_ret:0.121, last_outcome:'win',     last_date:'2026-04-20', last_formula:'macd_golden_cross' },
  { code:'600276', name:'恒瑞医药',   n30:1, n_total:3,  win:0.67, avg_ret:0.058, last_outcome:'win',     last_date:'2026-04-02', last_formula:'ma_breakout_long_low' },
  { code:'002415', name:'海康威视',   n30:2, n_total:5,  win:0.40, avg_ret:-0.022,last_outcome:'loss',    last_date:'2026-04-25', last_formula:'dynamic_ma_iterative_cross' },
];

window.CMV3 = CMV3;

/* ============ 模型管理 (champion + challenger) ============ */
CMV3.MODELS = {
  champion: {
    id:'champion_v3', version:'2026.05.01', algo:'composite_v3', deployed_at:'2026-05-01',
    cv_ic:0.31, oos_ic:0.28, oos_topk_hit:0.62, max_dd:-0.081, sharpe:0.78, days_live:9,
  },
  challengers: [
    { id:'ch_2026_05_07_a', algo:'composite_v3 + valuation_pctile', cv_ic:0.34, oos_ic:0.30, oos_topk_hit:0.65, max_dd:-0.074, status:'pending_gate', gate_pass:true,  diff_pct:0.072 },
    { id:'ch_2026_05_03_b', algo:'composite_v3 + sector_momentum',  cv_ic:0.29, oos_ic:0.26, oos_topk_hit:0.59, max_dd:-0.092, status:'rejected',     gate_pass:false, diff_pct:-0.048 },
    { id:'ch_2026_04_28_c', algo:'composite_v3 - inst_north',       cv_ic:0.30, oos_ic:0.27, oos_topk_hit:0.61, max_dd:-0.085, status:'archived',     gate_pass:false, diff_pct:-0.018 },
  ],
  drift: { ks_stat:0.12, ks_pass:true, last_check:'2026-05-08 17:02' },
};

/* ============ 数据健康 ============ */
CMV3.HEALTH = {
  tables: [
    { name:'raw_inst_holding',         freshness:'2026-05-08 16:42', rows:1284200, deltaPct:0.0042, qc:'ok' },
    { name:'raw_quote_daily',           freshness:'2026-05-08 17:01', rows:8420000, deltaPct:0.0008, qc:'ok' },
    { name:'fact_event_buy',            freshness:'2026-05-08 17:18', rows:62400,   deltaPct:0.0125, qc:'ok' },
    { name:'fact_technical_trigger',    freshness:'2026-05-08 17:22', rows:158200,  deltaPct:0.0088, qc:'ok' },
    { name:'fact_stock_type_daily',     freshness:'2026-05-08 17:30', rows:4800,    deltaPct:0.0021, qc:'warn' },
    { name:'mart_daily_recommendation', freshness:'2026-05-08 17:42', rows:7,       deltaPct:null,    qc:'ok' },
    { name:'mart_paper_nav',            freshness:'2026-05-08 17:43', rows:120,     deltaPct:null,    qc:'ok' },
    { name:'mart_institution_profile',  freshness:'2026-05-08 17:38', rows:312,     deltaPct:0.0014, qc:'ok' },
  ],
  pipeline_runs: [
    { run_id:'2026-05-08', stage:'ingest',     status:'ok',  duration_s:412 },
    { run_id:'2026-05-08', stage:'fact_build', status:'ok',  duration_s:684 },
    { run_id:'2026-05-08', stage:'mart_build', status:'ok',  duration_s:1208 },
    { run_id:'2026-05-08', stage:'score',      status:'ok',  duration_s:362 },
    { run_id:'2026-05-08', stage:'qc',         status:'warn',duration_s:48, note:'fact_stock_type_daily 主类型缺失率 0.3% > 阈值' },
  ],
  qc_alerts: [
    { id:'qc_001', table:'fact_stock_type_daily', metric:'primary_type_null_rate', value:0.003, threshold:0.002, severity:'warn', age_min:18 },
  ],
};

/* ============ 机构详情 (子页面用) ============ */
CMV3.INST_DETAIL = {
  sb105: {
    bio: '社保 105 组合 · 由南方基金管理 · 偏稳健 · 关注白酒 / 银行 / 必选消费',
    win30:0.62, win60:0.62, win90:0.58, sample_n:42, stability:0.84,
    by_horizon:[{h:10,w:0.52},{h:30,w:0.58},{h:60,w:0.62},{h:90,w:0.58},{h:120,w:0.55}],
    top_stocks: [
      { code:'600519', name:'贵州茅台', n_events:6, win_rate:0.83, contrib_pct:0.18 },
      { code:'600036', name:'招商银行', n_events:5, win_rate:0.80, contrib_pct:0.14 },
      { code:'000333', name:'美的集团', n_events:4, win_rate:0.75, contrib_pct:0.11 },
      { code:'601318', name:'中国平安', n_events:4, win_rate:0.50, contrib_pct:0.04 },
      { code:'601012', name:'隆基绿能', n_events:3, win_rate:0.67, contrib_pct:0.06 },
    ],
    recent_events: [
      { date:'2026-05-06', code:'600519', name:'贵州茅台', action:'增持', amount:'1.2 亿股' },
      { date:'2026-04-22', code:'600036', name:'招商银行', action:'增持', amount:'8400 万股' },
      { date:'2026-04-10', code:'000333', name:'美的集团', action:'减持', amount:'2200 万股' },
    ],
  },
};
