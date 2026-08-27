"""Observation frontend is a multi-page static site under frontend/app/."""
from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "frontend" / "app"

REQUIRED = [
    "index.html",
    "css/site.css",
    "js/core.js",
    "js/live.js",
    "js/lab.js",
    "foundation/matrix.html",
    "foundation/ops.html",
    "foundation/domain.html",
    "lab/overview.html",
    "lab/packages.html",
    "lab/experiments.html",
    "lab/expdetail.html",
    "lab/release.html",
    "lab/snapshots.html",
    "insight/market.html",
    "insight/flows.html",
    "insight/warnings.html",
    "insight/sector.html",
    "insight/briefing.html",
    "insight/screener.html",
    "insight/dossier.html",
    "insight/inst.html",
    "insight/paper.html",
]


def test_multipage_site_files_exist():
    missing = [rel for rel in REQUIRED if not (APP / rel).is_file()]
    assert missing == [], missing


def test_stock_rows_keep_dossier_click_not_xueqiu_wrap():
    live = (APP / "js" / "live.js").read_text(encoding="utf-8")
    assert "function xqMark" in live
    assert "window.DOSSIER = window.DOSSIER || null" in live
    assert '<span class="lv-name"><a class="xq" href="${XQ(r.stock_code)}"' not in live
    assert '<span class="lv-num"><a class="xq" href="${XQ(r.stock_code)}"' not in live


def test_lab_pages_hydrate_from_api_not_baked_verdicts():
    overview = (APP / "lab" / "overview.html").read_text(encoding="utf-8")
    assert "/app/js/lab.js" in overview
    assert "n130_gtv2" not in overview
    assert "+6.09%" not in overview
    core = (APP / "js" / "core.js").read_text(encoding="utf-8")
    assert 'id: "packages"' in core
    assert "extra.family" in core
    assert 'nav === "insight/dossier"' in core
    assert "String(t.dataset.code).slice(0, 6)" not in core
    live = (APP / "js" / "live.js").read_text(encoding="utf-8")
    assert "/api/v3/inst/signals" in live
    assert "/api/v3/pulse/rotation" in live
    assert "s.rs_4w * 100" not in live
    assert 'space === "lab"' in live
    lab = (APP / "js" / "lab.js").read_text(encoding="utf-8")
    assert "/api/v3/lab/overview" in lab
    assert "claimable" in lab
    assert "StrategyRelease" in lab
    assert "CAPABILITY EMPTY" in lab


def test_pages_are_real_urls_not_hash_spa():
    core = (APP / "js" / "core.js").read_text(encoding="utf-8")
    assert "location.hash" not in core
    assert "/app/${space}/${tab}.html" in core or '`${APP}/${space}/${tab}.html`' in core
    dossier = (APP / "insight" / "dossier.html").read_text(encoding="utf-8")
    assert 'data-space="insight"' in dossier
    assert 'data-tab="dossier"' in dossier
    assert "/app/js/core.js" in dossier
    assert "id=\"dossier-lhb\"" in dossier


def test_inst_deeplink_expands_even_if_unranked():
    live = (APP / "js" / "live.js").read_text(encoding="utf-8")
    assert "不在本页前 500 排名表" in live
    assert "await expandInst(h, null)" in live
    inst = (APP / "insight" / "inst.html").read_text(encoding="utf-8")
    assert "高盛国际" not in inst
    assert "UBS AG" not in inst
    assert "不回落打样户" in live


def test_observation_pages_are_live_not_baked():
    core = (APP / "js" / "core.js").read_text(encoding="utf-8")
    live = (APP / "js" / "live.js").read_text(encoding="utf-8")
    matrix = (APP / "foundation" / "matrix.html").read_text(encoding="utf-8")
    ops = (APP / "foundation" / "ops.html").read_text(encoding="utf-8")
    market = (APP / "insight" / "market.html").read_text(encoding="utf-8")
    warnings = (APP / "insight" / "warnings.html").read_text(encoding="utf-8")
    sector = (APP / "insight" / "sector.html").read_text(encoding="utf-8")
    flows = (APP / "insight" / "flows.html").read_text(encoding="utf-8")
    assert 'id: "run"' not in core
    assert 'id: "gates"' not in core
    assert "foundation/run" not in matrix
    assert "foundation/gates" not in matrix
    assert 'data-ops-start="daily_update"' in matrix
    assert "/api/v3/ops/matrix" in live
    assert "function liveMatrix" in live
    assert "DAYS60" not in live
    assert "DOM_META" not in live
    assert "不回落打样" in live
    assert "禁止从退出码" not in ops
    assert "没有 cron" not in ops
    assert "非假百分比" not in live
    assert "页面为快照非实时" not in live
    assert "软态/观测不发失败横幅" not in live
    assert "芯片概念" not in market
    assert "纺织服饰" not in warnings
    assert "医疗研发外包" not in sector
    assert "荣耀概念" not in flows
    assert not (APP / "foundation" / "run.html").exists()
    assert not (APP / "foundation" / "gates.html").exists()
