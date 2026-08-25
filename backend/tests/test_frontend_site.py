"""Observation frontend is a multi-page static site under frontend/app/."""
from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "frontend" / "app"

REQUIRED = [
    "index.html",
    "css/site.css",
    "js/core.js",
    "js/live.js",
    "foundation/matrix.html",
    "foundation/ops.html",
    "foundation/run.html",
    "foundation/gates.html",
    "foundation/domain.html",
    "lab/overview.html",
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
