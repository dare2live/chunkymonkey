"""technical_states.limits — A股涨跌停/一字板 flags (真相源 raw_tushare_stk_limit)。

审查 keeps: stk_limit 硬真相源架构 (up/down_limit 已编码板块 tier ±5/10/20%, 免分板路由);
涨停日量比修正方向 (封板 = 需求/供给截断, 量门会误判)。
按裁决修正:
- H3 (CONFIRMED): 一字板双侧 — is_one_word = (涨停 or 跌停) 且开=高=低=收 (旧只涨停侧,
  一字跌停 = 卖不出的最典型场景永远 False, candles '一字板跌停' 分支死代码);
- medium: 触板判定在**原始价空间半 tick 精确比** (旧 0.3% 相对容差把尾盘炸板误判封板);
- medium: 不改写实测 vol_ratio/zvol — 修正写 *_eff 视图 (measured 特征不污染), 跌停侧对称 proxy;
- H4: flags 透传到产物表 buyable/sellable/is_one_word, 回测消费方"看不见"成为不可能。
PIT: 当日 close vs 当日 limit (limit 昨收盘后即知)。纯函数, DB load 在 __init__ 构建层。
"""
from __future__ import annotations


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def compute_limit_flags(dates, o, h, l, c, raw_close, up_limit, down_limit, *, price_tol: float) -> dict:
    """→ {ISO日期: {is_up_limit, is_down_limit, is_one_word}} (bool 或 None=无涨跌停数据, 不知道≠False)。

    - raw_close vs up/down_limit: **原始价空间** (A股价 2 位小数), price_tol=半 tick (config 涨跌停.价格容差);
    - o/h/l/c 可为复权价 (一字板几何 o=h=l=c 是同日同乘系数, 复权保持相等) — 相对容差 1e-9 数学常数。
    """
    out = {}
    n = len(dates)
    for i in range(n):
        k = str(dates[i])[:10]
        rc, ul, dl = raw_close[i], up_limit[i], down_limit[i]
        is_up = None if (_nan(rc) or _nan(ul)) else bool(abs(rc - ul) < price_tol)
        is_down = None if (_nan(rc) or _nan(dl)) else bool(abs(rc - dl) < price_tol)
        if is_up is None and is_down is None:
            one = None
        elif not any(_nan(x) for x in (o[i], h[i], l[i], c[i])) and c[i] > 0:
            geom = abs(o[i] - c[i]) <= c[i] * 1e-9 and (h[i] - l[i]) <= c[i] * 1e-9
            one = bool((is_up or is_down) and geom)      # H3: 双侧
        else:
            one = None
        out[k] = {"is_up_limit": is_up, "is_down_limit": is_down, "is_one_word": one}
    return out


def enrich_features(feats: dict, limit_flags: dict, cfg: dict) -> dict:
    """涨跌停日修正 **eff 视图** (vol_ratio_eff/zvol_eff) + 加 limit 标志 (float 0/1, 供条件 DSL)。

    实测 vol_ratio/zvol 不改写 (审查 medium: 改写污染 measured 特征)。封板 = 量截断 →
    eff 抬到 proxy (需求/供给对称, 跌停侧 medium 修复), 使量能轴不把封板日误判缩量。
    """
    lim_cfg = cfg["涨跌停"]
    up_proxy = float(lim_cfg["需求proxy量比"])
    down_proxy = float(lim_cfg["供给proxy量比"])
    z_proxy = float(lim_cfg["量能zproxy"])
    for k, f in feats.items():
        lim = limit_flags.get(k)
        for name in ("is_up_limit", "is_down_limit", "is_one_word"):
            v = lim.get(name) if lim else None
            f[name] = float("nan") if v is None else (1.0 if v else 0.0)
        if not lim:
            continue
        proxy = up_proxy if lim.get("is_up_limit") else down_proxy if lim.get("is_down_limit") else None
        if proxy is not None:
            vr = f.get("vol_ratio")
            f["vol_ratio_eff"] = max(vr, proxy) if not _nan(vr) else proxy
            zv = f.get("zvol")
            f["zvol_eff"] = max(zv, z_proxy) if not _nan(zv) else z_proxy
    return feats
