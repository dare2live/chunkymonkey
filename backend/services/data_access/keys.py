"""canonical 主键转换 (数据模块顶层设计 v2 不变量#1: 统一主键 (code6, as_of))。

SERVE 读层是 code↔ts_code 归一的**单一真相源** (此前散在 technical_states.limits, 位置反了:
那是消费层, 主键归一是数据层关注点)。消费层从此 import, 方向正确。

约定: 项目内部统一用 **6 位 code** (price_kline 口径); tushare 接口用 **ts_code** (600519.SH)。
北交所/三板已由 universe 排除, 此处只管 SH/SZ 后缀。
"""
from __future__ import annotations

_SH_PREFIX2 = ("60", "68")
_SH_PREFIX3 = ("510", "511", "512", "513", "515", "588")  # 沪市 ETF/基金号段


def code_to_ts_code(code: str) -> str:
    """6 位 code (000513) → tushare ts_code (000513.SZ)。60/68 + 51x/588 → SH, 其余 → SZ。"""
    code = str(code).strip()
    if code[:2] in _SH_PREFIX2 or code[:3] in _SH_PREFIX3:
        return f"{code}.SH"
    return f"{code}.SZ"


def ts_code_to_code(ts_code: str) -> str:
    """tushare ts_code (000513.SZ) → 6 位 code (000513)。"""
    return str(ts_code).split(".")[0]


def to_iso(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD; 已是 ISO 或非 8 位则原样返回。"""
    s = str(yyyymmdd)
    if len(s) == 8 and "-" not in s and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def to_yyyymmdd(iso: str) -> str:
    """YYYY-MM-DD → YYYYMMDD; 去横杠。"""
    return str(iso).replace("-", "")
