from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_stock_report_widget_is_registered_before_app_js():
    index = (REPO / "index.html").read_text(encoding="utf-8")
    app_js = (REPO / "assets/js/app.js").read_text(encoding="utf-8")
    widget_js = (REPO / "assets/js/widgets/stock-report.js").read_text(encoding="utf-8")

    assert "'assets/js/widgets/stock-report.js'" in index
    assert index.index("'assets/js/widgets/stock-report.js'") < index.index("'assets/js/app.js'")
    assert "window.StockReportWidget" in widget_js
    assert "renderStockResearchSummary" in widget_js
    assert "renderStockReportSection" in widget_js
    assert "renderStockReportHero" in widget_js
    assert "renderStockEvidenceTimeline" in widget_js
    assert "renderStockReportScoreSection" in widget_js
    assert "renderStockReportDataSection" in widget_js
    assert "renderStockDetailCardGrid" in widget_js
    assert "StockReportWidget" in app_js

