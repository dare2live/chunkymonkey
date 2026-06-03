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
    stock_view_js = (REPO / "assets/js/stock-view.js").read_text(encoding="utf-8")

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
    assert "'assets/js/widgets/stock-list-rows.js'" in index
    assert "'assets/js/widgets/stock-list-controls.js'" in index
    assert "'assets/js/widgets/institution-scorecard.js'" in index
    assert "'assets/js/widgets/etf-analysis.js'" in index
    assert "'assets/js/widgets/etf-list.js'" in index
    assert "'assets/js/widgets/etf-opportunity.js'" in index
    assert "'assets/js/widgets/etf-sector-rotation.js'" in index
    assert "'assets/js/widgets/etf-strategy-compare.js'" in index
    assert "'assets/js/widgets/etf-analysis.js'" in index
    assert "'assets/js/widgets/etf-workbench.js'" in index
    assert "'assets/js/widgets/model-monitor.js'" in index
    assert "'assets/js/widgets/format-utils.js'" in index
    assert "'assets/js/widgets/institution-scorecard.js'" in index
    assert "'assets/js/widgets/topk-strip.js'" in index
    assert "'assets/js/widgets/workbench-health.js'" in index
    assert "'assets/js/widgets/screening-panel.js'" in index
    assert "'assets/js/widgets/multidim-badge.js'" in index
    assert "'assets/js/settings-view.js'" in index
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
    assert index.index("'assets/js/widgets/stock-list-rows.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/institution-scorecard.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/etf-analysis.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/etf-list.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/etf-opportunity.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/etf-sector-rotation.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/etf-strategy-compare.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/etf-opportunity.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/etf-list.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/etf-workbench.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/etf-analysis.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/workbench-health.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/institution-scorecard.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/topk-strip.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/signal-params.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/cohort-card.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/backtest-panel.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/screening-panel.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/multidim-badge.js'")
    assert index.index("'assets/js/widgets/topk-strip.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/etf-workbench.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/model-monitor.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/model-monitor.js'")
    assert index.index("'assets/js/widgets/format-utils.js'") < index.index("'assets/js/widgets/grid-optimizer.js'")
    assert index.index("'assets/js/widgets/workbench-health.js'") < index.index("'assets/js/app.js'")
    assert index.index("'assets/js/settings-view.js'") < index.index("'assets/js/app.js'")
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
    assert "function setActiveState(" in app_js
    assert "function bindNodeClicks(" in app_js
    assert "document.querySelectorAll('.nav-group-btn').forEach" not in app_js
    assert "document.querySelectorAll('.nav-sub-bar .nav-btn').forEach" not in app_js
    assert "document.querySelectorAll('.stock-tabs .tab-btn').forEach" not in app_js
    assert "ReturnsChartWidget" in app_js
    assert "TypeSummaryWidget" in app_js
    assert "StockSummaryWidget" in app_js
    assert "StockListRowsWidget" in app_js
    assert "StockListControlsWidget" in app_js
    assert "InstitutionScorecardWidget" in app_js
    assert "ETFAnalysisWidget" in app_js
    assert "ETFListWidget" in app_js
    assert "ETFOpportunityWidget" in app_js
    assert "ETFWorkbenchWidget" in app_js
    assert "ModelMonitorWidget" in app_js
    assert "WorkbenchHealthWidget" in app_js
    assert "function loadEtfList()" in app_js
    assert "function loadEtfOpportunity()" in app_js
    assert "function loadEtfWorkbench(" in app_js
    assert "function loadModelMonitor()" in app_js
    assert "function refreshWorkbenchHealthBar()" in app_js
    assert "function refreshNetwork()" in app_js
    assert "function loadStocks()" not in app_js
    assert "function loadResearch()" not in app_js
    assert "function loadIndustryOverviewSummary()" not in app_js
    assert "function renderStockResearchSummary(" not in app_js
    assert "function renderStockInstitutionCoverageSection(" not in app_js
    assert "function renderStockReportHero(" not in app_js
    assert "function renderStockEvidenceTimeline(" not in app_js
    assert "function renderStockDetailCardGrid(" not in app_js
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
    assert "function buildStockIndex(" in stock_view_js
    assert "state.stockIndex = buildStockIndex(state.byStock, state.screeningMap, state.turtleMap);" in stock_view_js
    assert "global.StockView = { load, reload, openDrawer, _buildStockIndex: buildStockIndex }" in stock_view_js
    assert "topCountEntries(" not in app_js
    assert "local.topIndustries = topCountEntries" not in app_js
    assert "function turtleSystemLabel(" not in app_js
    assert "function turtleStateMeta(" not in app_js
    assert "function turtleStateTag(" not in app_js
    assert "function instLink(" not in app_js
    assert "function evTag(" not in app_js
    assert "function stockScoreValue(" not in app_js
    assert "function stockScoreBandMeta(" not in app_js
    assert "function stockScoreSubtext(" not in app_js
    assert "function stockSortRows(" not in app_js
    assert "function resolveStockSummary(" not in app_js
    assert "function summaryChip(" not in app_js
    assert "function hasAttentionCoverage(" not in app_js
    assert "function hasTurtleCoverage(" not in app_js
    assert "function attentionSummaryTone(" not in app_js
    assert "function stockScreeningInline(" not in app_js
    assert "function screeningHitCount(" not in app_js
    assert "function summaryRow(" not in app_js
    assert "function heroMetricCard(" not in app_js
    assert "function typeTag(" not in app_js
    assert "_buildEtfTradeTimelineHtml(" not in app_js
    assert "_buildNavCurveSvg(" not in app_js
    assert "function metricCard(" not in app_js
    assert "function etfPctCell(" not in app_js
    assert "function etfOverviewTone(" not in app_js
    assert "function etfWatchTags(" not in app_js
    assert "function bindEtfActionLinks(" not in app_js
    assert "function _loadEtfOpportunityMining(" not in app_js
    assert "function etfStrategyTone(" not in app_js
    assert "function etfSetupTone(" not in app_js
    assert "function etfCatColor(" not in app_js
    assert "function xueqiuPillLink(" not in app_js
    assert "function scheduleSortableTables(" not in app_js
    assert "function sortableCellMeta(" not in app_js
    assert "function makeSortable(" not in app_js
    assert "function renderModelComparison(" not in app_js
    assert "function renderPromotionGate(" not in app_js
    assert "function renderTdxValidation(" not in app_js
    assert "function renderMetricsCards(" not in app_js
    assert "function renderDailyChart(" not in app_js
    assert "function renderRegimeChart(" not in app_js
    assert "function renderFeatureImportance(" not in app_js
    assert "function ensureFeatureLabels(" not in app_js
    assert "function etfNum(" not in app_js
    assert "function statusPill(" not in app_js
    assert "function workbenchLinkCard(" not in app_js
    assert "function strategyShortcut(" not in app_js
    assert "function buildWorkbenchHtml(" not in app_js
    assert "window.FeatureLabels" not in app_js
    assert "modelMonitorState" not in app_js
    assert "function setText(" not in app_js
    assert "function setChip(" not in app_js
    assert "function normalizeSourceName(" not in app_js
    assert "function setSourcePill(" not in app_js
    assert "function primeNetworkPills(" not in app_js
    assert "function checkNetwork(" not in app_js
    assert "function renderHealthFreshness(" not in app_js
    assert "function renderHealthEvents(" not in app_js
    assert "function renderHealthInst(" not in app_js
    assert "function renderHealthSignals(" not in app_js
    assert "function renderHealthPipeline(" not in app_js
    assert "ETF 列表 widget 暂不可用" in app_js
    assert "function renderTabEvidence(content, s)" in stock_view_js
    assert "const followEvents = s.events.filter(e => e.action === 'follow');" not in stock_view_js
    assert "const watchEvents = s.events.filter(e => e.action === 'watch');" not in stock_view_js
    assert "if (ev.action === 'follow' || ev.action === 'watch') candidates.push(ev);" in stock_view_js
    assert "buildStockFilterMetaByCode" in stock_list_controls_js
    assert "applyStockFilters" in stock_list_controls_js
    assert "/api/workbench/data-sources" in data_view_js
    assert "/api/data_health/snapshot" not in data_view_js
    assert "/api/data_health/sources" not in data_view_js
    assert "function buildAuditResultsModel(" in data_view_js
    assert "function buildRoutesTableModel(" in data_view_js
    assert "function buildSourceCardsModel(" in data_view_js
    assert "function buildAssetHealthIndex(" in data_view_js
    assert "function routeHealth(route, asset)" in data_view_js
    assert "const assetIndex = buildAssetHealthIndex(" in data_view_js
    assert "const model = buildRoutesTableModel(" in data_view_js
    assert "const model = buildSourceCardsModel(" in data_view_js
    assert "assetIndex.get(route.raw_table) || null" in data_view_js
    assert "const list = _state.routes.filter(r => {" not in data_view_js
    assert "root.innerHTML = tdxPriority + _state.sources.map(src => {" not in data_view_js
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
    assert "renderPaperSim" in workbench_js
    assert "buildPaperSimModel" in workbench_js
    assert "buildPaperSimModel(data)" in workbench_js
    assert "/api/workbench/recommendations" in workbench_js
    assert "/api/workbench/storage" in workbench_js
    assert "data-wb-tab" in workbench_js
    assert "数据源" in workbench_js
    assert "TDX K线服务器健康" in workbench_js
    assert "renderTdxServerHealthTable" in workbench_js
    assert "function buildTdxServerHealthModel(" in workbench_js
    assert "renderTdxServerHealthTable(model.tdxServerHealth)" in workbench_js
    assert "TDX F10 Source-Date Audit" in workbench_js
    assert "renderTdxF10SourceDateAudit" in workbench_js
    assert "buildTdxF10SourceDateAuditModel" in workbench_js
    assert "buildTdxF10SourceDateAuditModel(data)" in workbench_js
    assert "TDX/F10 Source-Date DQ" in workbench_js
    assert "renderTdxF10SourceDq" in workbench_js
    assert "buildTdxF10SourceDateDqModel" in workbench_js
    assert "buildTdxF10SourceDateDqModel(data)" in workbench_js
    assert "管线" in workbench_js
    assert "特征" in workbench_js
    assert "GO/NO-GO" in workbench_js
    assert "renderOverview" in workbench_js
    assert "buildOverviewModel" in workbench_js
    assert "buildOverviewModel(data)" in workbench_js
    assert "renderDelivery" in workbench_js
    assert "buildDeliveryModel" in workbench_js
    assert "buildDeliveryModel(data)" in workbench_js
    assert "renderPipelines" in workbench_js
    assert "buildPipelinesModel" in workbench_js
    assert "buildPipelinesModel(data)" in workbench_js
    assert "renderChampion" in workbench_js
    assert "buildChampionModel" in workbench_js
    assert "buildChampionModel(data)" in workbench_js
    assert "buildStabilityContextModel" in workbench_js
    assert "buildStabilityContextModel(data.stability_context || {})" in workbench_js
    assert "renderDataSources" in workbench_js
    assert "buildDataSourcesModel" in workbench_js
    assert "buildDataSourcesModel(data)" in workbench_js
    assert "renderTodaySignalCache" in workbench_js
    assert "function buildTodaySignalCacheModel(" in workbench_js
    assert "renderTodaySignalCache(model.signalCacheModel)" in workbench_js
    assert "renderAssetGovernanceTable" in workbench_js
    assert "function buildAssetGovernanceTableModel(" in workbench_js
    assert "renderAssetGovernanceTable(model.assetGovernanceTable)" in workbench_js
    assert "renderProcessingMonitorTable" in workbench_js
    assert "function buildProcessingMonitorModel(" in workbench_js
    assert "renderProcessingMonitorTable(model.processingMonitor)" in workbench_js
    assert "renderFeatures" in workbench_js
    assert "buildFeaturesModel" in workbench_js
    assert "buildFeaturesModel(data)" in workbench_js
    assert "renderTemporalSynergy" in workbench_js
    assert "buildTemporalSynergyModel" in workbench_js
    assert "buildTemporalSynergyModel(data)" in workbench_js
    assert "renderRankMatrixCache" in workbench_js
    assert "buildRankMatrixCacheModel" in workbench_js
    assert "buildRankMatrixCacheModel(data)" in workbench_js
    assert "renderRecommendations" in workbench_js
    assert "buildRecommendationsModel" in workbench_js
    assert "buildRecommendationsModel(data)" in workbench_js
    assert "renderStorage" in workbench_js
    assert "buildStorageModel" in workbench_js
    assert "buildStorageModel(data)" in workbench_js
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
    assert "Walk-forward 候选验证" in workbench_js
    assert "renderShareholderPlanWalkforward" in workbench_js
    assert "刷新视图" in workbench_js
    assert "renderReadModelMeta" in workbench_js
    assert "buildReadModelMeta" in workbench_js
    assert "buildReadModelMeta(data)" in workbench_js
    assert "renderResearch" in workbench_js
    assert "buildResearchModel" in workbench_js
    assert "buildResearchModel(data)" in workbench_js
    assert "function latestGateDecision(" not in workbench_js
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
