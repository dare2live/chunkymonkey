"""dossier HTTP 路由 — 股票档案视图后端 (前缀 /api/dossier)。

owner=docs/stock_dossier_master_design.md (P2)。把 form 维度解读器暴露给前端档案视图:
  GET /stock/{code}    单股多TF形态解读 + 趋势线 + 可调参数 (overrides 查询参数=前端调参 before/after)
  GET /screen          列出符合某形态的股票 + mini 趋势线
  GET /tunables        可调边界参数 (前端滑块来源, 含耦合关系)
  GET /view            自包含 HTML 档案视图 (无 node, 同 design/*.html 风格)
overrides 编码: 查询参数 ov="上升通道.均线斜率:3.0,放量突破.量比:2.5" (经边界耦合同步)。
"""
from __future__ import annotations

import pathlib
import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from services.dossier import compare_distribution, interpret_stock, screen_pattern
from services.technical_states import list_tunables, load_config

router = APIRouter()
_VIEW_HTML = pathlib.Path(__file__).resolve().parent / "static" / "dossier_view.html"


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_overrides(ov: str | None) -> dict | None:
    """ov='状态.指标:值,状态.指标:值' → {param: float}。非数值 token 显式跳过(无 try/except 静默吞错)。"""
    if not ov:
        return None
    out = {}
    for part in ov.split(","):
        if ":" not in part:
            continue
        k, v = part.rsplit(":", 1)
        v = v.strip()
        if _NUM_RE.match(v):                # 显式校验数值, 非数值 token 跳过 (前端查询参数防御)
            out[k.strip()] = float(v)
    return out or None


@router.get("/stock/{code}")
def get_stock(code: str, end: str | None = None, ov: str | None = None):
    """单股档案 (form 维度): 多TF 解读 + 趋势线 + 可调参数。ov=前端调参 (before/after)。"""
    d = interpret_stock(code, end=end, overrides=_parse_overrides(ov))
    if d is None:
        return {"error": f"{code} 无 K线数据 (排除股/未上市/已退市?)"}
    return d


@router.get("/screen")
def get_screen(pattern: str, end: str | None = None, limit: int = 40, scan: int = 300, ov: str | None = None):
    """列出最新主态==pattern 的股票 + mini 趋势线。"""
    return screen_pattern(pattern, end=end, limit=limit, scan=scan, overrides=_parse_overrides(ov))


@router.get("/tunables")
def get_tunables():
    """可调边界参数 (前端滑块, 当前值=探索默认值) + 状态描述 + 默认值版本。"""
    cfg = load_config()
    return {"tunables": list_tunables(cfg),
            "states": {s: spec.get("描述", "") for s, spec in cfg["状态"].items()},
            "defaults_version": cfg.get("meta", {}).get("version"),
            "defaults_source": cfg.get("meta", {}).get("tuned_on", "")}


@router.get("/compare")
def get_compare(ov: str, end: str | None = None, scan: int = 200):
    """**全体分布对比**: 默认参数 vs 调整后参数, 每形态股票数 Δ + 翻转股票 (调参的全体层面影响)。"""
    ovr = _parse_overrides(ov)
    if not ovr:
        return {"error": "无有效调参 (ov 为空)"}
    return compare_distribution(ovr, end=end, scan=scan)


@router.get("/view", response_class=HTMLResponse)
def get_view():
    """自包含 HTML 档案视图。"""
    if _VIEW_HTML.exists():
        return _VIEW_HTML.read_text(encoding="utf-8")
    return "<h3>dossier_view.html 未找到</h3>"
