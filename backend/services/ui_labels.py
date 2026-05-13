"""统一 UI 标签字典 — 后端字段 → 前端短名 (中文 2-4 字)。

目的:
  - 前端不再自己 hardcode 字段中文名 (各页面各 render 各显示)
  - 字段名变动只改这一个文件
  - /api/v3/labels 端点暴露给前端, 注入 window.CMV3.LABELS
"""
from __future__ import annotations


LABELS: dict[str, str] = {
    # === 通用 ===
    "stock_code": "代码",
    "stock_name": "名称",
    "snapshot_date": "日期",
    "signal_date": "信号日",
    "buy_date": "买入日",
    "plan_date": "计划日",
    "rank_in_date": "当日排名",
    "rank": "排名",
    "built_at": "构建于",

    # === 阶段 / 形态 ===
    "fundamental_stage": "基本面阶段",
    "fundamental_stage_days": "基本面持续",
    "technical_stage": "技术面阶段",
    "technical_stage_days": "技术面持续",
    "stage": "阶段",
    "primary_type": "类型",
    "secondary_types": "副类型",
    "stock_archetype": "原型",
    "stock_gate": "门槛",

    # === 估值 ===
    "valuation_pe": "PE",
    "valuation_pe_pctile": "PE 分位",
    "valuation_upside_pct": "上行空间",
    "pe_ttm": "PE",

    # === 公式 / 信号 ===
    "formula_id": "公式",
    "formula_variant": "变体",
    "strength": "强度",
    "state": "状态",
    "horizon_days": "持仓天数",
    "holding_days": "持仓",
    "n_signals": "样本",
    "n_matured": "已到期",
    "win_rate": "胜率",
    "avg_ret": "均收益",
    "median_ret": "中位收益",
    "avg_dd": "均回撤",
    "median_dd": "中位回撤",
    "max_dd": "最大回撤",
    "max_drawdown": "最大回撤",
    "sharpe": "夏普",
    "calmar": "卡玛",
    "n_obs": "观测",

    # === per-stock optuna ===
    "vol_bin": "量能",
    "amt_bin": "额比",
    "price_pos_bin": "位置",
    "stage_bin": "阶段",
    "vol_r20": "量比",
    "amt_r20": "额比",
    "price_pos_60d": "60日位",
    "price_pos_120d": "120日位",
    "is_best_hd": "最佳持仓",
    "is_high_conviction": "高信心",
    "is_recommended": "推荐",

    # === daily formula buys ===
    "signal_strength": "信号强度",
    "historical_win_rate": "历史胜率",
    "historical_avg_ret": "历史均收益",
    "historical_avg_dd": "历史均回撤",
    "historical_sharpe": "历史夏普",
    "historical_n_signals": "历史样本",
    "recommended_holding_days": "建议持仓",
    "signal_close_price": "信号收盘",
    "buy_price_est": "建议买入价",
    "sell_target_price": "目标卖出价",
    "expected_max_dd_pct": "预期回撤",
    "expected_return_pct": "预期收益",
    "confidence_score": "置信度",

    # === paper engine ===
    "nav": "净值",
    "nav_value": "净值金额",
    "daily_ret": "日收益",
    "cum_ret": "累计收益",
    "hs300_nav": "沪深300净值",
    "hs300_cum_ret": "沪深300累计",
    "vs_hs300_cum_ret": "超额(沪深300)",
    "eqw_nav": "等权净值",
    "eqw_cum_ret": "等权累计",
    "vs_eqw_cum_ret": "超额(等权)",
    "cash": "现金",
    "cash_pct": "现金占比",
    "position_count": "持仓数",
    "position_value": "持仓市值",
    "drawdown": "回撤",
    "drawdown_pct": "回撤%",
    "max_dd_pct": "最大回撤%",
    "monthly_win": "月胜率",
    "excess_pct": "超额收益",
    "current_drawdown_pct": "当前回撤",

    # === selection ===
    "n_total": "总数",
    "n_30d": "30日数",
    "n_90d": "90日数",
    "last_select_date": "最近选中",
    "last_formula": "最近公式",
    "last_outcome": "最近结果",
    "n_signals_60d": "60日样本",
    "rolling_ic_30d": "30日IC",
    "rolling_ic_60d": "60日IC",
    "weight": "权重",
    "prev_weight": "昨日权重",
    "fwd_ret_5d": "5日收益",
    "fwd_ret_10d": "10日收益",
    "fwd_ret_30d": "30日收益",
    "fwd_max_dd_30d": "30日最大回撤",
    "days_to_t1": "达T1天数",
    "outcome_5d": "5日结果",
    "outcome_10d": "10日结果",
    "outcome_30d": "30日结果",

    # === institution ===
    "institution_id": "机构ID",
    "institution_name": "机构名称",
    "inst_type": "机构类型",
    "win_rate_30d": "30日胜率",
    "win_rate_60d": "60日胜率",
    "win_rate_90d": "90日胜率",
    "current_stock_count": "当前持仓数",
    "total_events": "总事件",
    "total_periods": "总报告期",
    "avg_gain_30d": "30日均涨",
    "avg_gain_60d": "60日均涨",
    "hold_ratio_total": "持股比",
    "hold_change_num": "持股变动",
    "holder_name": "股东名",
    "holder_rank": "股东排名",
    "report_date": "报告期",
    "institution_score": "机构评分",
    "institution_n_insts": "机构数",

    # === outcome 分类 ===
    "win": "盈利",
    "loss": "亏损",
    "flat": "持平",
    "active": "进行中",

    # === 公式变体短名 ===
    "macd_golden_cross_above_zero": "MACD 上轴金叉",
    "macd_golden_cross_below_zero": "MACD 下轴金叉",
    "macd_golden_cross": "MACD 金叉",
    "turtle_breakout_20": "海龟20突破",
    "turtle_breakout_55": "海龟55突破",
    "dynamic_ma_iterative_cross": "动态均线穿越",
    "institution_follow": "机构跟随",

    # === 上下文桶 (5 维分桶) ===
    "缩量": "缩量",
    "平量": "平量",
    "温量": "温量",
    "爆量": "爆量",
    "额减": "额减",
    "额平": "额平",
    "额温": "额温",
    "额爆": "额爆",
    "深底": "深底",
    "中位": "中位",
    "高位": "高位",
    "新高": "新高",
    "above_zero": "0轴上",
    "below_zero": "0轴下",
}


def get_labels() -> dict[str, str]:
    """返回全部标签字典 (复制一份, 避免外部修改)。"""
    return dict(LABELS)


def label_for(key: str, fallback: str | None = None) -> str:
    """单字段查询 — 找不到时返回 fallback (或原 key)。"""
    return LABELS.get(key, fallback if fallback is not None else key)
