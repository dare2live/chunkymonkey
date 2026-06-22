"""technical_states.limits — A股涨跌停形态修正 (D3, 评审critical)。

owner=backend/services/technical_states/ + config/technical_states.yaml 涨停 段。
评审critical: 封涨停=缩量(封板供给截断), 被 vol_ratio 判'无量假突破'方向反; A股主升浪常以涨停启动。
修: 用 raw_tushare_stk_limit 硬真相源(up_limit/down_limit, **已编码板块tier ±10/20%**, 不必单独分板路由) →
  涨停日 vol_ratio 不可信→设需求proxy使放量突破不被误判 + 加 is_up_limit/is_down_limit/is_one_word 描述标志。
纯函数 (DB load 在 dossier 服务层); 涨停=日线概念, 只 enrich 日线特征。PIT: 用当日 close vs 当日 limit。
"""
from __future__ import annotations


# code↔ts_code canonical 归一单一源 = services.data_access.keys (数据层, 不变量#1);
# 此处 re-export 保留既有 importer 兼容, 不再各自定义 (消双真相源)。
from services.data_access.keys import code_to_ts_code  # noqa: F401 (re-export)


def compute_limit_flags(dates, o, h, l, c, up_limit, down_limit, eps: float = 0.003) -> dict:
    """每 bar 涨跌停标志 (PIT: 当日 close vs 当日 up/down_limit)。返回 {date: {is_up_limit,is_down_limit,is_one_word}}。
    一字板 = 涨停 且 开=高=低=收 (全天封板, 买不进)。eps=贴板容差 (复权后 limit 有微小误差)。
    """
    out = {}
    n = len(dates)
    for i in range(n):
        cc, ul, dl = c[i], up_limit[i], down_limit[i]
        if cc is None or (ul is None and dl is None):
            continue
        is_up = ul is not None and cc >= ul * (1 - eps)
        is_down = dl is not None and cc <= dl * (1 + eps)
        is_one = bool(is_up and o[i] is not None and h[i] is not None and l[i] is not None
                      and abs(o[i] - cc) <= cc * eps and (h[i] - l[i]) <= cc * eps)
        out[str(dates[i])] = {"is_up_limit": bool(is_up), "is_down_limit": bool(is_down), "is_one_word": is_one}
    return out


def enrich_features(feats: dict, limit_flags: dict, cfg: dict | None = None) -> dict:
    """涨停日修正日线特征 (评审critical) + 加 limit 标志 (float 0/1, 供 config 子态规则)。原地改 feats。
    涨停日 vol_ratio 封板截断→设需求proxy(>放量突破量比门)使放量突破不被误判'无量假突破'。
    """
    cfg = cfg or {}
    proxy = (cfg.get("涨停") or {}).get("需求proxy量比", 3.0)
    for d, f in feats.items():
        lim = limit_flags.get(d)
        f["is_up_limit"] = 1.0 if (lim and lim["is_up_limit"]) else 0.0
        f["is_down_limit"] = 1.0 if (lim and lim["is_down_limit"]) else 0.0
        f["is_one_word"] = 1.0 if (lim and lim["is_one_word"]) else 0.0
        if lim and lim["is_up_limit"]:
            vr = f.get("vol_ratio")
            f["vol_ratio"] = max(vr if vr and vr == vr else 1.0, proxy)   # 封板=最大需求, 用proxy
    return feats
