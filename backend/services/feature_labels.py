"""特征名/模型名中文映射 · 评级函数

全局单一真相源, 供 API 和前端共用.
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────
# 特征 中文映射
# ──────────────────────────────────────────────────────────

FEATURE_LABELS = {
    # Pillar B 基础
    "ret_1d": "1日收益率",
    "ret_5d": "5日收益率",
    "ret_20d": "20日收益率",
    "ret_60d": "60日收益率",
    "vol_z20d": "成交量z分20日",
    "ma_ratio_5": "收盘/MA5",
    "ma_ratio_20": "收盘/MA20",
    "ma_ratio_60": "收盘/MA60",
    "ma_ratio_250": "收盘/MA250",
    "rz_balance": "融资余额",
    "rz_chg_5d_pct": "融资余额·5日变化",
    # Pillar B Alpha158 启发
    "kmid": "K线中轴",
    "klen": "K线振幅",
    "kup": "K线上影",
    "klow": "K线下影",
    "ksft": "K线偏移",
    "vol_ratio_5_20": "量比5/20日",
    "vol_std_5d": "收益率波动5日",
    "vol_std_20d": "收益率波动20日",
    "range_pos_20": "20日区间位置",
    "range_pos_60": "60日区间位置",
    "momentum_diff": "5-20日动量差",
    "amount_chg_5d": "成交额·5日变化",
    "ret_20d_rank": "20日收益·日内排名",
    "ret_60d_rank": "60日收益·日内排名",
    "vol_z20d_rank": "成交量z分·日内排名",
    "amount_chg_5d_rank": "成交额5日变化·日内排名",
    "rz_balance_rank": "融资余额·日内排名",
    "rz_chg_5d_pct_rank": "融资余额5日变化·日内排名",
    "ret_20d_tdx_l1_rel": "20日收益·行业相对",
    "ret_60d_tdx_l1_rel": "60日收益·行业相对",
    "vol_z20d_tdx_l1_rel": "成交量z分·行业相对",
    "amount_chg_5d_tdx_l1_rel": "成交额变化·行业相对",
    "rz_balance_to_amount20": "融资余额/20日成交额",
    # Pillar A 事件
    "inst_event_count_30d": "机构事件·30日",
    "inst_event_count_60d": "机构事件·60日",
    "exec_buy_count_90d": "高管增持·90日",
    "exec_buy_ge1_count_90d": "高管增持≥1%·90日",
    "lhb_inst_buy_count_30d": "龙虎榜机构买·30日",
    "lhb_inst_buy_count_60d": "龙虎榜机构买·60日",
    "jgdy_count_60d": "机构调研·60日",
    "dzjy_count_60d": "大宗交易·60日",
    "days_since_exec_buy": "距上次高管增持天数",
    "days_since_lhb": "距上次龙虎榜天数",
    # Pillar C 基本面
    "shareholder_count_qoq": "股东户数·季度变化",
    "inst_count_qoq": "机构总数·季度变化",
    "fund_count_qoq": "基金机构数·季度变化",
    "qfii_count_qoq": "QFII机构数·季度变化",
    "yjyg_lower_pct": "业绩预告·下限%",
    "yjyg_upper_pct": "业绩预告·上限%",
    "roe": "净资产收益率",
    "eps_basic": "每股收益",
    # Regime
    "hs300_ret_20d": "沪深300·20日动量",
    "hs300_ret_60d": "沪深300·60日动量",
    "regime_up": "市场·上涨",
    "regime_flat": "市场·震荡",
    "regime_down": "市场·下跌",
    "regime_flag": "市场状态",
    # Label
    "forward_ret_5d": "前向5日收益率",
    "forward_ret_10d": "前向10日收益率",
    "forward_ret_20d": "前向20日收益率",
    "forward_ret_60d": "前向60日收益率",
    "forward_ret_90d": "前向90日收益率",
    "follow_net_return_5d": "跟随净收益率5日",
    "follow_net_return_10d": "跟随净收益率10日",
    "follow_net_return_20d": "跟随净收益率20日",
    "follow_net_return_60d": "跟随净收益率60日",
    "follow_net_return_90d": "跟随净收益率90日",
    # TDX keep challenger
    "forecast_profit_yoy_mid": "TDX业绩预告利润同比中值",
    "avg_float_shares_change_pct_tdx": "TDX户均流通股变化率",
    "ocf_to_profit_tdx": "TDX经营现金流/净利润",
    "fund_shares_qoq": "TDX基金持股季度变化",
    "forecast_range_width": "TDX业绩预告区间宽度",
    "auto_general_corp_count_event_nonzero": "TDX一般法人户数事件",
    "auto_general_corp_shares_event_nonzero": "TDX一般法人持股事件",
    "auto_general_corp_count_level": "TDX一般法人户数水平",
    "auto_top10_float_holder_shares_event_nonzero": "TDX十大流通股东持股事件",
    "auto_top1_holder_shares_event_nonzero": "TDX第一大股东持股事件",
    "auto_top10_holder_shares_event_nonzero": "TDX十大股东持股事件",
    "auto_holder_count_event_nonzero": "TDX股东户数事件",
    "auto_private_equity_shares_level": "TDX私募持股水平",
    # 回测派生
    "holdout_ic": "持出期·IC",
    "holdout_rank_ic": "持出期·Rank IC",
    "holdout_top_decile_avg": "持出期·Top 10% 平均20日收益",
    "holdout_bottom_decile_avg": "持出期·Bottom 10% 平均20日收益",
    "holdout_long_short_spread": "持出期·多空价差",
    "holdout_winrate_top": "持出期·Top 10% 胜率",
    "n_features": "特征数",
}


# ──────────────────────────────────────────────────────────
# 模型名 中文映射
# ──────────────────────────────────────────────────────────

MODEL_NAME_LABELS = {
    "multidim_v1": "多维评分 v1",
    "multidim_v2": "多维评分 v2",
    "tdx_keep_challenger": "TDX keep challenger",
    "lgb": "LightGBM 基线",
}


def format_model_id(model_id: str) -> str:
    """multidim_v1_20260424_002615 → '多维评分 v1 · 2026-04-24 00:26'"""
    if not model_id:
        return "-"
    for prefix, label in MODEL_NAME_LABELS.items():
        if model_id.startswith(prefix + "_"):
            tail = model_id[len(prefix) + 1 :]
            # 20260424_002615 → 2026-04-24 00:26
            if len(tail) >= 13 and tail[:8].isdigit():
                return f"{label} · {tail[:4]}-{tail[4:6]}-{tail[6:8]} {tail[9:11]}:{tail[11:13]}"
            return f"{label} · {tail}"
    return model_id


def label_feature(name: str) -> str:
    """返回 '英文名（中文名）' 形式的组合标签"""
    zh = FEATURE_LABELS.get(name)
    return f"{name}（{zh}）" if zh else name


# ──────────────────────────────────────────────────────────
# 评级 5 档
# ──────────────────────────────────────────────────────────

GRADES = ["差", "较差", "一般", "良好", "优秀"]
GRADE_COLORS = ["#991b1b", "#c2410c", "#b45309", "#15803d", "#14532d"]


def grade(value: float, thresholds: list[float]) -> tuple[str, int, str]:
    """返回 (grade_name, grade_index 0-4, color).
    thresholds = [t_较差, t_一般, t_良好, t_优秀] 递增
    """
    if value is None:
        return "-", -1, "#94a3b8"
    idx = 0
    for t in thresholds:
        if value >= t:
            idx += 1
        else:
            break
    idx = min(idx, 4)
    return GRADES[idx], idx, GRADE_COLORS[idx]


METRIC_GRADE_THRESHOLDS = {
    # 递增: 差→较差→一般→良好→优秀
    "holdout_ic": [0.01, 0.02, 0.03, 0.05],
    "holdout_rank_ic": [0.02, 0.04, 0.06, 0.08],
    "holdout_top_decile_avg": [0.005, 0.01, 0.02, 0.03],
    "holdout_long_short_spread": [0.005, 0.01, 0.02, 0.04],
    "holdout_winrate_top": [0.48, 0.52, 0.56, 0.60],
}


def grade_metric(metric_name: str, value: float) -> dict:
    """单指标评级"""
    thresh = METRIC_GRADE_THRESHOLDS.get(metric_name)
    if thresh is None or value is None:
        return {"grade": "-", "index": -1, "color": "#94a3b8"}
    g, idx, color = grade(value, thresh)
    return {"grade": g, "index": idx, "color": color}


def composite_grade(model_meta: dict) -> dict:
    """综合评级: 5 指标 grade_index 的算术平均 → 取对应档位.

    model_meta 需含 holdout_ic / holdout_rank_ic / holdout_top_decile_avg /
                   holdout_long_short_spread / holdout_winrate_top
    """
    indices = []
    details = {}
    for m in METRIC_GRADE_THRESHOLDS.keys():
        v = model_meta.get(m)
        g = grade_metric(m, v)
        details[m] = {"value": v, "grade": g["grade"], "index": g["index"], "color": g["color"]}
        if g["index"] >= 0:
            indices.append(g["index"])
    if not indices:
        return {"grade": "-", "index": -1, "color": "#94a3b8", "detail_by_metric": details, "avg_index": None}
    avg = sum(indices) / len(indices)
    rounded = int(round(avg))
    rounded = max(0, min(4, rounded))
    return {
        "grade": GRADES[rounded],
        "index": rounded,
        "color": GRADE_COLORS[rounded],
        "avg_index": round(avg, 2),
        "detail_by_metric": details,
    }
