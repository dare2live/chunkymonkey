/* v3-data-live.jsx · 把 v3-data.jsx 的 mock 字段替换为真实 API 调用
 *
 * 加载顺序: v3-data.jsx (mock) -> v3-ui.jsx -> v3-page-*.jsx -> v3-data-live.jsx (本文件覆盖)
 *
 * 当某个 API 失败时,静默回退到 v3-data.jsx 的 mock,不破坏 UI。
 * 完成后 dispatch 'cmv3:dataReady' 让 App 触发 re-render。
 *
 * Phase α (W1) 已接通:
 *   - INSTITUTIONS    <-  /api/inst/profiles
 *   - STOCKS          <-  /api/rec/daily-topk (仅排名 + score, 完整画像 W4)
 *   - RUN_META        <-  /api/v3/run-meta
 *   - SIGNIFICANT_HOLDERS <- /api/v3/significant
 *
 * Phase α 还保留 mock 的字段 (等后续 Phase 接入):
 *   - FORMULAS        (Phase β: fact_technical_trigger)
 *   - FITNESS / STAGES (Phase β: mart_stage_formula_fitness)
 *   - NAV_SERIES / KPIS / PL_ATTR / SIGNAL_IC / HOLDINGS (Phase δ: Paper Engine)
 *   - SELECTION_BOARD (Phase ε: mart_stock_selection_summary)
 *   - INST_DETAIL     (Phase α 末尾或 Phase γ)
 *   - MODELS / HEALTH (Phase β / Phase ε)
 */

(async function loadLiveData() {
  const TAG = '[v3-data-live]';

  const fetchJson = async (url) => {
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      console.warn(`${TAG} ${url} failed:`, e.message);
      return null;
    }
  };

  // 并行拉所有 API (含 labels — UI 字段中文映射)
  const [profilesResp, dailyResp, runMetaResp, significantResp,
         formulasResp, fitnessResp, selectionsResp, labelsResp] = await Promise.all([
    fetchJson('/api/inst/profiles'),
    fetchJson('/api/rec/daily-topk?limit=100'),
    fetchJson('/api/v3/run-meta'),
    fetchJson('/api/v3/significant?limit=20'),
    fetchJson('/api/v3/formulas'),
    fetchJson('/api/v3/fitness?limit=800'),
    fetchJson('/api/v3/selections?limit=50'),
    fetchJson('/api/v3/labels'),
  ]);

  // 注入 LABELS 字典 (UI.label('win_rate') → '胜率')
  if (labelsResp && labelsResp.ok && labelsResp.data) {
    window.CMV3.LABELS = labelsResp.data;
    console.info(`${TAG} LABELS: ${Object.keys(labelsResp.data).length} 个字段中文映射`);
  }

  // Phase δ D5: 第三波 fetch — paper engine (NAV / holdings / KPIs / signal IC / pl-attr)
  // Phase ε D4: 第四波 fetch — selection (board / weights), summary 在 picture 阶段批量带
  const [paperNavResp, paperHoldingsResp, paperKpisResp, paperSignalIcResp, paperPlAttrResp,
         selBoardResp, selWeightsResp] = await Promise.all([
    fetchJson('/api/v3/paper/nav'),
    fetchJson('/api/v3/paper/holdings'),
    fetchJson('/api/v3/paper/kpis?window=120'),
    fetchJson('/api/v3/paper/signal-ic?window=60&horizon=10'),
    fetchJson('/api/v3/paper/pl-attr?window=30'),
    fetchJson('/api/v3/selection/board?limit=50'),
    fetchJson('/api/v3/selection/weights'),
  ]);

  // Phase γ D5 + Phase ε D4: STOCKS 拿到 daily-topk 后, 二次 fetch picture + trade plan + selection summary 批量丰富
  let pictureByCode = {};
  let tradePlanByCode = {};
  let selectionByCode = {};
  if (dailyResp && dailyResp.ok && Array.isArray(dailyResp.items) && dailyResp.items.length > 0) {
    const topkCodes = dailyResp.items.map(s => s.stock_code).join(',');
    const [pictureResp, tradePlanProms, selSummaryResp] = await Promise.all([
      fetchJson(`/api/v3/picture/batch?codes=${topkCodes}`),
      // trade-plan 是单股端点, 仅 fetch 前 50 (减少 round-trip)
      Promise.all(dailyResp.items.slice(0, 50).map(s =>
        fetchJson(`/api/v3/trade-plan/${s.stock_code}`).then(r => ({ code: s.stock_code, data: r }))
      )),
      // Phase ε D4: 批量 selection summary
      fetchJson(`/api/v3/selection/summary?codes=${topkCodes}`),
    ]);
    if (pictureResp && pictureResp.ok && Array.isArray(pictureResp.data)) {
      pictureResp.data.forEach(p => { pictureByCode[p.stock_code] = p; });
      console.info(`${TAG} PICTURE: ${pictureResp.data.length} 股 (来自 mart_stock_picture_daily)`);
    }
    (tradePlanProms || []).forEach(tp => {
      if (tp && tp.data && tp.data.ok && tp.data.data) {
        tradePlanByCode[tp.code] = tp.data.data;
      }
    });
    console.info(`${TAG} TRADE_PLANS: ${Object.keys(tradePlanByCode).length} 股 (来自 mart_stock_trade_plan)`);
    if (selSummaryResp && selSummaryResp.ok && Array.isArray(selSummaryResp.data)) {
      selSummaryResp.data.forEach(s => { selectionByCode[s.stock_code] = s; });
      console.info(`${TAG} SELECTION_SUMMARY: ${selSummaryResp.data.length} 股 (来自 mart_stock_selection_summary)`);
    }
  }

  if (!window.CMV3) {
    console.error(`${TAG} window.CMV3 未定义,确认 v3-data.jsx 先加载`);
    return;
  }

  // ============ INSTITUTIONS ============
  // 来自 /api/inst/profiles  -> mart_institution_profile
  // 注意 win_rate_* 后端是 0-100, v3 设计是 0-1, 需 /100
  if (profilesResp && profilesResp.ok && Array.isArray(profilesResp.data)) {
    window.CMV3.INSTITUTIONS = profilesResp.data.map(p => ({
      id: p.institution_id,
      name: p.institution_name,
      alias: p.display_name || p.institution_name,
      type: p.inst_type || '未分类',
      win30: (Number(p.win_rate_30d) || 0) / 100,
      win60: (Number(p.win_rate_60d) || 0) / 100,
      win90: (Number(p.win_rate_90d) || 0) / 100,
      stability: 0.80,  // mart_institution_profile 暂无 stability_score, hardcoded
      n_stocks: Number(p.current_stock_count) || 0,
      holdings: Number(p.current_stock_count) || 0,
      tracked: true,
    }));
    console.info(`${TAG} INSTITUTIONS: ${window.CMV3.INSTITUTIONS.length} 行 (来自 mart_institution_profile)`);
  }

  // ============ STOCKS ============
  // 来自 /api/rec/daily-topk  -> mart_daily_recommendation (rank/score)
  // Phase γ D5: 用 pictureByCode + tradePlanByCode 二次合并完整画像
  if (dailyResp && dailyResp.ok && Array.isArray(dailyResp.items)) {
    window.CMV3.STOCKS = dailyResp.items.map((s) => {
      const p = pictureByCode[s.stock_code] || {};
      const tp = tradePlanByCode[s.stock_code] || {};
      return {
        code: s.stock_code,
        name: s.stock_name || s.stock_code,
        price: p.latest_close ?? null,
        chg_pct: p.chg_pct != null ? Number(p.chg_pct) : 0,
        industry_l1: s.l1 || '—',
        primary_type: p.primary_type || '—',                        // Phase γ: fact_stock_type_daily
        secondary_types: p.secondary_types || [],
        fundamental_stage: p.fundamental_stage || '—',              // Phase γ: dim_stock_stage_latest 7 模板派生
        fundamental_stage_days: p.fundamental_stage_days,
        technical_stage: p.technical_stage || '—',                  // Phase γ: fact_stock_technical_stage
        technical_stage_days: p.technical_stage_days,
        stock_archetype: p.stock_archetype,
        valuation_upside_pct: p.valuation_upside_pct,               // Phase γ
        valuation_pe: p.valuation_pe,
        valuation_pe_pctile: p.valuation_pe_pctile,
        institution_signal: {
          score: p.institution_score || 0,
          n_insts: p.institution_n_insts || 0,
          top: p.institution_top || [],
        },
        formulas_hit: p.formulas_hit || [],                         // 待 Phase β 后续接入 formula_hits_json
        action: 'BUY',
        score: Math.round(((s.pred_score || 0) * 100)),
        rank: s.rank,
        weight_pct: null,
        // trade plan 字段 (前 50 名拉了 trade plan)
        entry_target_price: tp.entry_target_price ?? null,
        entry_aggressive_price: tp.entry_aggressive_price ?? null,
        entry_max_price: tp.entry_max_price ?? null,
        exit_target_1: tp.exit_target_1_price ?? null,
        exit_target_2: tp.exit_target_2_price ?? null,
        exit_stop: tp.exit_stop_price ?? null,
        risk_reward_ratio: tp.risk_reward_ratio ?? null,
        expected_horizon_days: tp.expected_horizon_days ?? null,
        atr_14: tp.atr_14 ?? null,
        reason: `模型预测 percentile ${((s.percentile || 0) * 100).toFixed(0)}% · regime ${s.regime_flag || '—'}`,
        similar_n: null,
        similar_win: null,
        // 优选追踪 - Phase ε 真实数据
        selection_30d: selectionByCode[s.stock_code]?.n_30d ?? null,
        selection_total: selectionByCode[s.stock_code]?.n_total ?? null,
        selection_win_rate: selectionByCode[s.stock_code]?.win_rate ?? null,
        last_outcome: selectionByCode[s.stock_code]?.last_outcome ?? null,
      };
    });
    console.info(`${TAG} STOCKS: ${window.CMV3.STOCKS.length} 行 (来自 mart_daily_recommendation, snapshot=${dailyResp.snapshot_date}) + ${Object.keys(pictureByCode).length} 画像 + ${Object.keys(tradePlanByCode).length} 计划`);
  }

  // ============ RUN_META ============
  // 来自 /api/v3/run-meta -> mart_pipeline_run_manifest + mart_daily_recommendation
  if (runMetaResp && runMetaResp.ok && runMetaResp.data) {
    const d = runMetaResp.data;
    window.CMV3.RUN_META = {
      ...window.CMV3.RUN_META,  // mock 兜底, paper 未起的字段保留 mock 演示值
      plan_date:          d.plan_date          || window.CMV3.RUN_META.plan_date,
      signal_date:        d.signal_date        || window.CMV3.RUN_META.signal_date,
      built_at:           d.built_at           || window.CMV3.RUN_META.built_at,
      duration_min:       d.duration_min       || window.CMV3.RUN_META.duration_min,
      challenger_pending: d.challenger_pending ?? window.CMV3.RUN_META.challenger_pending,
      system_alerts:      d.system_alerts      || [],
      // nav / nav_chg_pct / vs_hs300_pct / vs_eq_pct 等 Phase δ Paper Engine 起来后才有真实值
    };
    console.info(`${TAG} RUN_META: signal=${d.signal_date} plan=${d.plan_date} built=${d.built_at}`);
  }

  // ============ SIGNIFICANT_HOLDERS ============
  // 来自 /api/v3/significant -> mart_institution_profile 聚合
  if (significantResp && significantResp.ok && Array.isArray(significantResp.data)) {
    window.CMV3.SIGNIFICANT_HOLDERS = significantResp.data;
    console.info(`${TAG} SIGNIFICANT_HOLDERS: ${window.CMV3.SIGNIFICANT_HOLDERS.length} 行`);
  }

  // ============ FORMULAS ============
  // 来自 /api/v3/formulas -> mart_formula_horizon_evidence + fact_technical_trigger
  if (formulasResp && formulasResp.ok && Array.isArray(formulasResp.data)) {
    window.CMV3.FORMULAS = formulasResp.data.map(f => ({
      id: f.id,
      name: f.name,
      tag: f.tag,
      hit_today: f.hit_today,
      win_rate: f.win_rate,
      horizon: f.horizon,
      state_dist: f.state_dist,
    }));
    console.info(`${TAG} FORMULAS: ${window.CMV3.FORMULAS.length} 公式 (latest_date=${formulasResp.latest_date})`);
  }

  // ============ FITNESS (适配矩阵) ============
  if (fitnessResp && fitnessResp.ok && Array.isArray(fitnessResp.data)) {
    window.CMV3.FITNESS = fitnessResp.data.map(r => ({
      fund: r.fund,
      tech: r.tech,
      formula_id: r.formula_id,
      holding_days: r.holding_days,
      n_signals: r.n_signals,
      win_rate: r.win_rate,
      avg_ret: r.avg_ret,
      avg_dd: r.avg_dd,
      is_recommended: r.is_recommended,
    }));
    // 重建 STAGES (从 FITNESS 提取唯一 fund × tech 组合)
    const stagesSet = new Set();
    fitnessResp.data.forEach(r => stagesSet.add(`${r.fund}|||${r.tech}`));
    window.CMV3.STAGES = Array.from(stagesSet).map(s => {
      const [fund, tech] = s.split('|||');
      return { fund, tech };
    });
    console.info(`${TAG} FITNESS: ${window.CMV3.FITNESS.length} 行 / STAGES: ${window.CMV3.STAGES.length} 组合`);
  }

  // ============ SELECTION_BOARD (优选追踪) — Phase ε 真实 mart_stock_selection_summary ============
  // 优先用 selBoardResp (Phase ε mart_stock_selection_summary), fallback 老 selectionsResp (Phase α 简化版)
  const boardSource = (selBoardResp && selBoardResp.ok) ? selBoardResp : selectionsResp;
  if (boardSource && boardSource.ok && Array.isArray(boardSource.data)) {
    window.CMV3.SELECTION_BOARD = boardSource.data.map(r => ({
      code: r.code,
      name: r.name,
      n30: r.n30 || r.n_30d,
      n_total: r.n_total,
      win: r.win ?? r.win_rate,           // 真实胜率 (Phase ε 已填)
      avg_ret: r.avg_ret,                  // 真实平均 ret_30d
      last_date: r.last_date,
      last_formula: r.last_formula,
      last_outcome: r.last_outcome,
    }));
    console.info(`${TAG} SELECTION_BOARD: ${window.CMV3.SELECTION_BOARD.length} 行 (源: ${boardSource === selBoardResp ? 'mart_stock_selection_summary' : 'fallback'})`);
  }

  // ============ FORMULA_WEIGHTS (反馈环) - Phase ε ============
  if (selWeightsResp && selWeightsResp.ok && Array.isArray(selWeightsResp.data)) {
    window.CMV3.FORMULA_WEIGHTS = selWeightsResp.data.map(r => ({
      formula_id: r.formula_id,
      weight: r.weight,
      rolling_ic_30d: r.rolling_ic_30d,
      rolling_ic_60d: r.rolling_ic_60d,
      n_obs: r.n_obs,
    }));
    console.info(`${TAG} FORMULA_WEIGHTS: ${window.CMV3.FORMULA_WEIGHTS.length} 公式 (反馈环, snapshot=${selWeightsResp.snapshot_date})`);
  }

  // ============ Phase δ PAPER ENGINE 接线 ============
  // NAV_SERIES: 时间序列 (12 字段) → v3-data.jsx mock 形状 {i, nav, hs300, eq}
  if (paperNavResp && paperNavResp.ok && Array.isArray(paperNavResp.data)) {
    window.CMV3.NAV_SERIES = paperNavResp.data.map((r, i) => ({
      i,
      snapshot_date: r.snapshot_date,
      nav: r.nav_value,
      hs300: r.hs300_nav != null ? r.hs300_nav * r.nav_value : null,
      eq: r.eqw_nav != null ? r.eqw_nav * r.nav_value : null,
      cum_ret: r.cum_ret,
      drawdown: r.drawdown,
    }));
    // RUN_META 当前 nav 同步
    if (paperNavResp.latest && window.CMV3.RUN_META) {
      window.CMV3.RUN_META.nav = paperNavResp.latest.nav_value;
      window.CMV3.RUN_META.nav_chg_pct = paperNavResp.latest.cum_ret;
      window.CMV3.RUN_META.vs_hs300_pct = paperNavResp.latest.vs_hs300_cum_ret;
      window.CMV3.RUN_META.vs_eq_pct = paperNavResp.latest.vs_eqw_cum_ret;
    }
    console.info(`${TAG} NAV_SERIES: ${window.CMV3.NAV_SERIES.length} 日 (来自 mart_paper_nav)`);
  }

  // HOLDINGS: 当前持仓 → v3-data.jsx mock 形状 {code, name, days, ret, rr, stage, toT1, weight}
  if (paperHoldingsResp && paperHoldingsResp.ok && Array.isArray(paperHoldingsResp.data)) {
    window.CMV3.HOLDINGS = paperHoldingsResp.data.map(h => ({
      code: h.code,
      name: h.code,  // name 后续从 stock_picture 拿
      days: h.holding_days,
      ret: h.ret_pct,
      rr: null,  // Phase ε 接 trade_plan_snapshot 算
      stage: h.technical_stage,
      toT1: null,
      weight: h.weight_pct,
      cost: h.open_price,
      current: h.current_close,
    }));
    console.info(`${TAG} HOLDINGS: ${window.CMV3.HOLDINGS.length} 持仓 (来自 fact_paper_position)`);
  }

  // KPIS: 6 个 KPI 字段
  if (paperKpisResp && paperKpisResp.ok && paperKpisResp.data) {
    window.CMV3.KPIS = {
      ...window.CMV3.KPIS,
      excess_pct: paperKpisResp.data.excess_pct,
      sharpe: paperKpisResp.data.sharpe,
      max_dd_pct: paperKpisResp.data.max_dd_pct,
      monthly_win: paperKpisResp.data.monthly_win,
      cash_pct: paperKpisResp.data.cash_pct,
      position_count: paperKpisResp.data.position_count,
    };
    console.info(`${TAG} KPIS: sharpe=${paperKpisResp.data.sharpe} max_dd=${paperKpisResp.data.max_dd_pct}`);
  }

  // SIGNAL_IC: 每公式滚动 60 日 IC
  if (paperSignalIcResp && paperSignalIcResp.ok && Array.isArray(paperSignalIcResp.data)) {
    window.CMV3.SIGNAL_IC = paperSignalIcResp.data.map(r => ({
      signal: r.signal,
      ic: r.ic,
      n_dates: r.n_dates,
    }));
    console.info(`${TAG} SIGNAL_IC: ${window.CMV3.SIGNAL_IC.length} 公式 (60 日 IC)`);
  }

  // PL_ATTR: P&L 归因
  if (paperPlAttrResp && paperPlAttrResp.ok && paperPlAttrResp.data?.by_formula) {
    window.CMV3.PL_ATTR = paperPlAttrResp.data.by_formula;
    console.info(`${TAG} PL_ATTR: ${window.CMV3.PL_ATTR.length} 归因桶`);
  }

  // 触发 App re-render
  window.dispatchEvent(new CustomEvent('cmv3:dataReady', {
    detail: {
      institutions: window.CMV3.INSTITUTIONS?.length || 0,
      stocks: window.CMV3.STOCKS?.length || 0,
      significant: window.CMV3.SIGNIFICANT_HOLDERS?.length || 0,
      paper_nav_days: window.CMV3.NAV_SERIES?.length || 0,
      paper_holdings: window.CMV3.HOLDINGS?.length || 0,
      paper_ic_signals: window.CMV3.SIGNAL_IC?.length || 0,
      run_meta_signal_date: window.CMV3.RUN_META?.signal_date,
    },
  }));
  console.info(`${TAG} all live data loaded, dispatch cmv3:dataReady`);
})();
