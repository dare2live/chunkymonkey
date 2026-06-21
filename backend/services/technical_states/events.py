"""technical_states.events — 事件催化维度 (档案 L3 属性背景, ⑤b)。

真相源 (PIT 锚见各 loader; dossier 服务层 load):
  raw_tushare_top_list (龙虎榜每日明细: net_amount 净买额/reason 上榜原因) — 锚 trade_date (盘后即知)
  raw_tushare_top_inst (龙虎榜机构席位: side/net_buy/exalter 营业部) — 锚 trade_date
  raw_tushare_block_trade (大宗交易: price 成交价/amount 成交额) — 锚 trade_date
  raw_tushare_share_float (解禁: float_date 解禁日/float_ratio 解禁占比) — 锚 ann_date 公告日 (天然前瞻 PIT: 公告日已知未来解禁日 = 0 泄露)
事件催化 = 为什么近期异动 (游资/机构上榜=资金关注; 大宗折溢价=大资金态度; 解禁=未来抛压前瞻风控)。
状态以 sign/count 为主 (抗 vendor 金额单位不确定); 龙虎榜是主升浪资金标志。描述性档案维度。纯函数。
"""
from __future__ import annotations

from datetime import date, timedelta


def lhb_signal(lhb_rows, inst_rows, *, cfg=None) -> dict | None:
    """龙虎榜: 近 N 日上榜次数 + 净买额方向 + 机构席位净买 → 资金异动 (游资抢筹/机构进出)。
    lhb_rows=[(trade_date, net_amount, reason)] (≤t); inst_rows=[(trade_date, net_buy)] (≤t, 机构专用席位)。
    """
    if not lhb_rows and not inst_rows:
        return None
    c = (cfg or {}).get("事件") or {}
    n = len(lhb_rows)
    net_sum = sum(r[1] for r in lhb_rows if r[1] is not None)        # 净买额合计 (vendor 原单位)
    inst_net = sum(r[1] for r in inst_rows if r[1] is not None)      # 机构席位净买合计
    last_reason = lhb_rows[0][2] if lhb_rows else None               # 最近上榜原因 (rows DESC)
    if not n:
        state = "无龙虎榜"
    elif net_sum > 0 and inst_net > 0:
        state = "机构游资共振抢筹"
    elif net_sum > 0:
        state = "资金净买上榜"
    elif net_sum < 0:
        state = "资金净卖上榜"
    else:
        state = "上榜资金分歧"
    return {"上榜次数": n, "净买额万": round(net_sum / 1e4, 0) if net_sum else 0.0,   # 元 → 万元
            "机构净买万": round(inst_net / 1e4, 0) if inst_net else 0.0,
            "最近原因": last_reason, "龙虎榜状态": state}


def block_signal(bt_rows, close_by_date, *, cfg=None) -> dict | None:
    """大宗交易: 近 N 日成交价 vs 当日收盘 折溢价率 + 总额 → 折价抛压/溢价承接 (大资金态度)。
    bt_rows=[(trade_date, price, amount)] (≤t); close_by_date={date: close}。
    """
    if not bt_rows:
        return None
    c = (cfg or {}).get("事件") or {}
    disc_thr = c.get("大宗折价门", -2.0)        # 平均折溢价% < 此 = 折价抛压
    prem_thr = c.get("大宗溢价门", 2.0)         # > 此 = 溢价承接
    prems, total_amt = [], 0.0
    for td, price, amt in bt_rows:
        cl = close_by_date.get(td)
        if cl and price:
            prems.append((price / cl - 1.0) * 100.0)
        total_amt += amt or 0.0
    avg_prem = sum(prems) / len(prems) if prems else None
    state = ("折价抛压" if (avg_prem is not None and avg_prem < disc_thr)
             else "溢价承接" if (avg_prem is not None and avg_prem > prem_thr)
             else "平价大宗" if avg_prem is not None else "无大宗")
    # amount 实测=万元 (600519 单笔 17572万=1.76亿); 总额转亿 (/1e4) 展示
    return {"大宗笔数": len(bt_rows), "折溢价": round(avg_prem, 2) if avg_prem is not None else None,
            "大宗总额亿": round(total_amt / 1e4, 2) if total_amt else 0.0, "大宗状态": state}


def unlock_signal(float_rows, as_of, *, cfg=None) -> dict | None:
    """解禁 (前瞻事件, ann_date≤t 已知未来 float_date, 0 泄露): 未来 N 日内解禁占比 → 解禁压力预警。
    float_rows=[(float_date, float_ratio)] (均 ann_date<=as_of)。as_of=ISO 决策日。
    """
    c = (cfg or {}).get("事件") or {}
    horizon = int(c.get("解禁前瞻日", 90))      # 看未来 N 日内解禁
    warn_ratio = c.get("解禁预警占比", 3.0)     # 未来解禁占流通% > 此 = 压力预警
    asof = (as_of or date.today().isoformat()).replace("-", "")
    end = (date.fromisoformat(f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}") + timedelta(days=horizon)).strftime("%Y%m%d")
    upcoming = [(fd, fr) for fd, fr in float_rows if fd and asof < str(fd) <= end]   # 未来 horizon 内 (>t 防已解禁)
    if not upcoming:
        return {"临近解禁": None, "解禁占比": None, "解禁预警": False}
    upcoming.sort()
    ratio_sum = sum(fr for _, fr in upcoming if fr is not None)
    return {"临近解禁": str(upcoming[0][0]), "解禁占比": round(ratio_sum, 2) if ratio_sum else None,
            "解禁预警": bool(ratio_sum and ratio_sum > warn_ratio)}
