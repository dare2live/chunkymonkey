"""read-model 展示服务契约 — seed §7.1 展示层 (2026-06-23 档A seam, data_module_toplevel_design §7)。

每档案 (股票/机构/公式) 暴露一个 **(stock_code, as_of)-键的去规范化展示切片**, 由 SERVE 读层喂数
(不裸查库)。单档案视图读自己的切片; 跨档案立体视图 (股票×机构×公式 cube) 在本层 JOIN 切片
(by stock_code+as_of) = 组合非耦合 (P4/档B 建, 本档只立 seam 契约)。

§7.2 写锁安全: 全程 read_only (经 SERVE/dossier, DataAccess.get 只读 attach); 展示读稳定切片不 live
裸查热写库 → 与 sync 写窗时序解耦。§7.1 试金石: 加一档案进 cube = 加一个切片函数, cube 视图零改各档案。

# serve-fed: 本模块经 services.dossier (SERVE 读层装配器, 全 DataAccess.get) 取数, 0 内联裸查 raw
#   (不变量4 单概念单真相源 / 单一读路); read-model 是 SERVE 之上的展示投影, 非第二取数路径。
"""
from __future__ import annotations

from services import dossier


def stock_slice(code: str, as_of: str | None = None) -> dict:
    """股票档案 read-model 切片 — (stock_code, as_of) 键的去规范化展示数据, SERVE 喂数。

    复用 dossier (SERVE 合规装配器) 的 PIT 切片; 展示视图/cube 读本切片不重查档案内部 (seam 契约)。
    as_of=None → SERVE 默认锚 (latest_closed); 传 as_of → ≤as_of PIT 切片 (holders 等带 asof 锚)。
    去规范化: 各维度并列在一个 (code, as_of) 字典下, 视图直渲染不再各自跨库取。
    """
    capital_net, capital_flow = dossier.load_capital(code)
    return {
        "stock_code": code,
        "as_of": as_of,
        "kline": dossier.load_one(code, end=as_of),          # OHLCV PIT 序列 (≤as_of)
        "holders_top10": dossier.load_top10_holders(code, as_of=as_of),  # 十大流通股东 (ann_date≤as_of)
        "capital_net": capital_net,                           # 资金流净额序列
        "capital_flow": capital_flow,                         # 资金流总额序列
        "cyq": dossier.load_cyq(code),                        # 筹码分布/获利盘
        "limits": dossier.load_limits(code),                 # 涨跌停价
    }
