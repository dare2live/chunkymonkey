from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_institution_scorecard_widget_renders_expected_outputs() -> None:
    script = r"""
require(process.argv[1]);
require(process.argv[2]);
const widget = globalThis.InstitutionScorecardWidget;
if (!widget || typeof widget.renderInstScorecardStats !== 'function' || typeof widget.renderInstFrameworkRules !== 'function' || typeof widget.renderScoreParamCard !== 'function' || typeof widget.renderStockFrameworkLayer !== 'function' || typeof widget.renderStockFrameworkRules !== 'function') {
  throw new Error('InstitutionScorecardWidget exports missing');
}
const statsHtml = widget.renderInstScorecardStats({
  summary: {
    total: 12,
    buy_basis_count: 7,
    fallback_basis_count: 5,
    quality_high_conf_count: 4,
    follow_high_conf_count: 3,
    quality_strong_count: 2,
    followability_strong_count: 6,
    safe_follow_inst_count: 8,
    avg_safe_follow_event_count: 9,
    avg_premium_pct: 1.2,
    avg_buy_event_count: 4,
  },
  type_top: [{ inst_type: '券商', total: 5, avg_quality_score: 68.2, avg_followability_score: 55.3 }],
  hint_top: [{ followability_hint: '低拥挤', total: 6 }],
  confidence: { quality: [{ confidence: 'high', total: 4 }], followability: [{ confidence: 'mid', total: 5 }] },
});
if (!statsHtml.includes('当前样本摘要') || !statsHtml.includes('机构类型分布') || !statsHtml.includes('可跟性提示分布') || !statsHtml.includes('平均溢价')) {
  throw new Error('scorecard stats markup missing expected labels');
}
const rulesHtml = widget.renderInstFrameworkRules({
  formula: '0.7 * A + 0.3 * B',
  effective_forecast: { label: '预测分', formula: '0.6 * X', meaning: '用于前瞻约束' },
  caps: ['cap-a', 'cap-b'],
  external_overlay: { label: '外部层', summary: 'summary', items: ['item-a'] },
  pools: [{ label: 'A池', gate: 'follow', meaning: '高质量' }],
});
if (!rulesHtml.includes('固定口径') || !rulesHtml.includes('封顶与门槛') || !rulesHtml.includes('池子规则') || !rulesHtml.includes('外部层')) {
  throw new Error('framework rules markup missing expected labels');
}
const paramHtml = widget.renderScoreParamCard('instInstitutionParams', {
  title: '机构实力评分',
  editable_factors: [{ key: 'sample_weight', label: '样本权重', description: '事件数越多越稳定', source: 'facts' }],
}, { sample_weight: 80 }, { sample_weight: 60 });
if (!paramHtml.includes('机构实力评分') || !paramHtml.includes('样本权重') || !paramHtml.includes('默认 60') || !paramHtml.includes('facts')) {
  throw new Error('param card markup missing expected labels');
}
const layerHtml = widget.renderStockFrameworkLayer({
  label: '机构层',
  weight: 40,
  role: '主框架',
  summary: '说明',
  items: ['item-a', 'item-b'],
});
if (!layerHtml.includes('机构层') || !layerHtml.includes('40%') || !layerHtml.includes('item-a')) {
  throw new Error('layer markup missing expected labels');
}
const frameworkHtml = widget.renderStockFrameworkRules({
  formula: 'score',
  effective_forecast: { label: '预测分', formula: 'forecast', meaning: 'meaning' },
  caps: ['cap-a'],
  external_overlay: { label: '叠加层', items: ['overlay-a'] },
  pools: [{ label: 'B池', gate: 'watch', meaning: '说明' }],
});
if (!frameworkHtml.includes('综合优先分') || !frameworkHtml.includes('池子规则') || !frameworkHtml.includes('B池')) {
  throw new Error('stock framework markup missing expected labels');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/format-utils.js"), str(REPO / "assets/js/widgets/institution-scorecard.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
