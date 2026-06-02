from __future__ import annotations

from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_workbench_frontend_entrypoint_is_registered():
    index = (REPO / "index.html").read_text(encoding="utf-8")
    app_js = (REPO / "assets/js/app.js").read_text(encoding="utf-8")
    stock_list_controls_js = (REPO / "assets/js/widgets/stock-list-controls.js").read_text(encoding="utf-8")
    data_view_js = (REPO / "assets/js/data-view.js").read_text(encoding="utf-8")
    workbench_js = (REPO / "assets/js/workbench-view.js").read_text(encoding="utf-8")

    assert not (REPO / "assets/js/data-health-view.js").exists()
    assert 'data-view="workbench"' in index
    assert 'id="view-workbench"' in index
    assert 'id="view-data-health"' not in index
    assert "dh-panel-" not in index
    assert "'assets/js/workbench-view.js'" in index
    assert "window.App.showView('workbench')" in index
    assert 'data-legacy-surface="data-health"' not in index
    assert "'assets/js/data-health-view.js'" not in index
    assert "'assets/js/widgets/returns-chart.js'" in index
    assert "'assets/js/widgets/type-summary.js'" in index
    assert "'assets/js/widgets/stock-summary.js'" in index
    assert "'assets/js/widgets/stock-report.js'" in index
    assert "'assets/js/widgets/stock-list-rows.js'" in index
    assert "'assets/js/widgets/stock-list-controls.js'" in index
    assert "'assets/js/widgets/institution-scorecard.js'" in index
    assert "'assets/js/widgets/etf-analysis.js'" in index
    assert 'id="instScorecardFramework"' in index
    assert 'id="instScorecardStats"' in index
    assert 'id="instScorecardParams"' in index
    assert 'id="itab-btn-manage"' not in index
    assert 'id="instSearch"' not in index
    assert 'id="instTypeFilter"' not in index
    assert 'id="instListContainer"' not in index
    assert 'id="instMgmtTable"' not in index
    assert 'id="searchHolderType"' not in index
    assert 'id="btnSearchInst"' not in index
    assert 'id="btnImportChecked"' not in index
    assert 'id="btnBatchAlias"' not in index
    assert index.index("'assets/js/widgets/stock-report.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/stock-list-rows.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/institution-scorecard.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/etf-analysis.js'") < index.index("'assets/js/app.js'")
    assert 'onclick="window.App.showView(\'data-health\')"' not in index
    assert "高级数据健康" not in index
    assert "window.App.showWorkbenchTab('dataSources')" in index
    assert "window.App.showWorkbenchTab('pipelines')" in index
    assert "workbench: function () { window.WorkbenchView && window.WorkbenchView.show(); }" in app_js
    assert "function showWorkbenchTab(tab)" in app_js
    assert "function loadLegacyDataHealth()" not in app_js
    assert "'data-health': loadLegacyDataHealth" not in app_js
    assert "'assets/js/data-health-view.js'" not in app_js
    assert "showWorkbenchTab" in app_js
    assert "showView('workbench')" in app_js
    assert "ReturnsChartWidget" in app_js
    assert "TypeSummaryWidget" in app_js
    assert "StockSummaryWidget" in app_js
    assert "StockReportWidget" in app_js
    assert "function renderStockReportSection(" not in app_js
    assert "function renderStockReportScoreSection(" not in app_js
    assert "StockListRowsWidget" in app_js
    assert "StockListControlsWidget" in app_js
    assert "InstitutionScorecardWidget" in app_js
    assert "ETFAnalysisWidget" in app_js
    assert "function loadResearch()" in app_js
    assert "function loadInstMgmt" not in app_js
    assert "function buildDeepAnalysisHtml(" not in app_js
    assert "function renderScoreParamCard(" not in app_js
    assert "function renderInstFrameworkRules(" not in app_js
    assert "function renderInstScorecardStats(" not in app_js
    assert "function renderStockFrameworkLayer(" not in app_js
    assert "function renderStockFrameworkRules(" not in app_js
    assert "searchInst" not in app_js
    assert "importChecked" not in app_js
    assert "batchAlias" not in app_js
    assert "batchType" not in app_js
    assert "batchMerge" not in app_js
    assert "batchBlack" not in app_js
    assert "batchDelete" not in app_js
    assert "stockCompositeSummary(" not in app_js
    assert "stockCompositeCell(" not in app_js
    assert "stockResearchCell(" not in app_js
    assert "stockDateSummaryCell(" not in app_js
    assert "stockHolderCoverageCell(" not in app_js
    assert "stockDimensionCell(" not in app_js
    assert "stockAttentionVerdictCell(" not in app_js
    assert "stockSignalCell(" not in app_js
    assert "stockExecutionCell(" not in app_js
    assert "sourceInstitutionCell(" not in app_js
    assert "stockReportCell(" not in app_js
    assert "function loadInstScorecard(" in app_js
    assert "_buildEtfTradeTimelineHtml(" not in app_js
    assert "_buildNavCurveSvg(" not in app_js
    assert "function metricCard(" not in app_js
    assert "buildStockFilterMetaByCode" in stock_list_controls_js
    assert "applyStockFilters" in stock_list_controls_js
    assert "/api/workbench/data-sources" in data_view_js
    assert "/api/data_health/snapshot" not in data_view_js
    assert "/api/data_health/sources" not in data_view_js
    assert "function buildAssetHealthIndex(" in data_view_js
    assert "function routeHealth(route, asset)" in data_view_js
    assert "const assetIndex = buildAssetHealthIndex(" in data_view_js
    assert "assetIndex.get(r.raw_table) || null" in data_view_js
    assert "details.filter(r => (r.issues || []).length > 0)" not in data_view_js
    assert "details.filter(r => !(r.issues || []).length)" not in data_view_js
    assert "Math.max(...tierEntries.map" not in data_view_js
    assert "/api/workbench/overview" in workbench_js
    assert "/api/workbench/research" in workbench_js
    assert "/api/workbench/champion" in workbench_js
    assert "/api/workbench/data-sources" in workbench_js
    assert "/api/workbench/pipelines" in workbench_js
    assert "/api/workbench/features" in workbench_js
    assert "/api/workbench/delivery-readiness" in workbench_js
    assert "/api/workbench/recommendations" in workbench_js
    assert "/api/workbench/storage" in workbench_js
    assert "data-wb-tab" in workbench_js
    assert "数据源" in workbench_js
    assert "TDX K线服务器健康" in workbench_js
    assert "renderTdxServerHealthTable" in workbench_js
    assert "TDX F10 Source-Date Audit" in workbench_js
    assert "renderTdxF10SourceDateAudit" in workbench_js
    assert "TDX/F10 Source-Date DQ" in workbench_js
    assert "renderTdxF10SourceDq" in workbench_js
    assert "管线" in workbench_js
    assert "特征" in workbench_js
    assert "GO/NO-GO" in workbench_js
    assert "renderDelivery" in workbench_js
    assert "推荐" in workbench_js
    assert "存储" in workbench_js
    assert "稳定性上下文" in workbench_js
    assert "Champion 阻塞上下文" in workbench_js
    assert "部署状态" in workbench_js
    assert "renderDeploymentSub" in workbench_js
    assert "timingSeconds" in workbench_js
    assert "per trial" in workbench_js
    assert "hit rate" in workbench_js
    assert "train%" in workbench_js
    assert "vs LGBM" in workbench_js
    assert "runtime_ratio_vs_regression" in workbench_js
    assert "eval cache" in workbench_js
    assert "feature_drift_cache_hit_rate" in workbench_js
    assert "Rank Matrix Cache" in workbench_js
    assert "renderRankMatrixCache" in workbench_js
    assert "Walk-forward 候选验证" in workbench_js
    assert "renderShareholderPlanWalkforward" in workbench_js
    assert "刷新视图" in workbench_js
    assert "renderReadModelMeta" in workbench_js
    assert "materialized_snapshot" in workbench_js
    assert "pipeline/job" in workbench_js
    assert "setTab: setTab" in workbench_js
    assert "global.WorkbenchView" in workbench_js


def test_workbench_frontend_has_no_stale_hard_coded_evidence_ids():
    workbench_js = (REPO / "assets/js/workbench-view.js").read_text(encoding="utf-8")
    stale_patterns = [
        r"\b20\d{2}-\d{2}-\d{2}\b",
        r"\b20\d{6}\b",
        r"\bmodel_stability_[A-Za-z0-9_]+",
        r"\branker_perf_[A-Za-z0-9_]+",
        r"\barchitecture_(?:inventory|cleanup)_[A-Za-z0-9_]+",
        r"\bworkbench_api_latency_[A-Za-z0-9_]+",
    ]

    for pattern in stale_patterns:
        assert not re.search(pattern, workbench_js), pattern
