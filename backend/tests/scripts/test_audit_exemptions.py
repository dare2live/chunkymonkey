"""audit_exemptions.py 的回归测试 —— 全自带 fixture (tmp_path 造小 YAML), 不读真实
sync_registry.yaml, 不连数据库。

覆盖:
  1. 四种豁免各造一个, 脚本能全部识别并计数正确 (总览 + 明细)
  2. verified_low_days 的理由被正确提取 (结构性理由, 不靠注释匹配)
  3. 带尾注/续行的 gap_tolerance -> 「有理由」; 不带注释的 -> 「无理由」
  4. 上一字段的注释块不会被下一字段借走 (dom_borrow_guard, 防假绿回归)
  4. 无任何豁免的域不出现在清单里
  5. --json 输出可被 json.loads 解析且含预期字段
  外加: main() 的退出码契约 (成功恒 0 / 读取出错才非零)。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "audit_exemptions", REPO / "backend" / "scripts" / "audit_exemptions.py")
ae = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ae)


FIXTURE_REGISTRY = textwrap.dedent("""\
    version: 1
    domains:
      dom_ked:
        source: fake
        api: fake_api
        target_table: raw_fake_ked
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        known_empty_days: ["20260101", "20260102"]   # 2026-08-01 实测源端两日真空,
                                  # vendor 返回 0 行, 已核实非我方采集缺口

      dom_vld:
        source: fake
        api: fake_api
        target_table: raw_fake_vld
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        verified_low_days:
          "20260201": "2026-08-01 向 vendor 逐页核证为真实低值, 非我方采集缺口"
          "20260202": "2026-08-01 同上核证, 已两次确认非缺口"

      dom_gt_reasoned:
        source: fake
        api: fake_api
        target_table: raw_fake_gt1
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        gap_tolerance: event_sparse   # 事件稀疏域: 空日为正常业务节奏, 已核实非缺口

      dom_borrow_guard:
        source: fake
        api: fake_api
        target_table: raw_fake_borrow
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        data_start: "20200101"   # 2026-08-01 这段长注释属于 data_start,
                                  # 绝不能被下面的 row_dip_tolerance 借走
        row_dip_tolerance: true

      dom_gt_bare:
        source: fake
        api: fake_api
        target_table: raw_fake_gt2
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        gap_tolerance: annotate

      dom_rdt:
        source: fake
        api: fake_api
        target_table: raw_fake_rdt
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        row_dip_tolerance: true

      dom_none:
        source: fake
        api: fake_api
        target_table: raw_fake_none
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
    """)


def _write_registry(tmp_path: Path) -> Path:
    path = tmp_path / "sync_registry.yaml"
    path.write_text(FIXTURE_REGISTRY, encoding="utf-8")
    return path


# ── 1. 四种豁免各识别 + 计数正确 ────────────────────────────────────────────

def test_all_four_exemption_types_recognized_and_counted(tmp_path):
    path = _write_registry(tmp_path)
    records = ae.scan_exemptions(path)

    by_type: dict[str, list[dict]] = {}
    for rec in records:
        by_type.setdefault(rec["exemption_type"], []).append(rec)

    assert len(records) == 6, records
    assert len(by_type["known_empty_days"]) == 1
    assert len(by_type["verified_low_days"]) == 1
    assert len(by_type["gap_tolerance"]) == 2
    assert len(by_type["row_dip_tolerance"]) == 2  # dom_rdt + dom_borrow_guard

    ked = by_type["known_empty_days"][0]
    assert ked["domain"] == "dom_ked"
    assert ked["date_count"] == 2

    overview = ae._overview(records)
    assert overview["known_empty_days"] == {"domains": 1, "dates": 2}
    assert overview["verified_low_days"] == {"domains": 1, "dates": 2}
    assert overview["gap_tolerance"]["domains"] == 2
    assert overview["gap_tolerance"]["dates"] is None
    assert overview["row_dip_tolerance"]["domains"] == 2
    assert overview["row_dip_tolerance"]["dates"] is None


# ── 2. verified_low_days 理由正确提取 (结构性, 不靠注释) ────────────────────

def test_verified_low_days_reason_extracted_structurally(tmp_path):
    path = _write_registry(tmp_path)
    records = ae.scan_exemptions(path)
    vld = next(r for r in records if r["exemption_type"] == "verified_low_days")

    assert vld["domain"] == "dom_vld"
    assert vld["date_count"] == 2
    assert vld["has_reason"] is True
    assert "20260201" in vld["reason"]
    assert "20260202" in vld["reason"]
    assert "已两次确认非缺口" in vld["reason"]


# ── 3. gap_tolerance: 有上方注释 -> 有理由; 无注释 -> 无理由 ─────────────────

def test_gap_tolerance_with_inline_comment_has_reason(tmp_path):
    path = _write_registry(tmp_path)
    records = ae.scan_exemptions(path)
    reasoned = next(
        r for r in records
        if r["exemption_type"] == "gap_tolerance" and r["domain"] == "dom_gt_reasoned"
    )
    assert reasoned["has_reason"] is True
    assert "事件稀疏" in reasoned["reason"]
    assert reasoned["detail"] == "event_sparse"


def test_gap_tolerance_without_comment_has_no_reason(tmp_path):
    path = _write_registry(tmp_path)
    records = ae.scan_exemptions(path)
    bare = next(
        r for r in records
        if r["exemption_type"] == "gap_tolerance" and r["domain"] == "dom_gt_bare"
    )
    assert bare["has_reason"] is False
    assert bare["reason"] is None
    assert bare["detail"] == "annotate"


# ── 4. 无豁免的域不出现在清单里 ─────────────────────────────────────────────

def test_domain_without_exemptions_not_listed(tmp_path):
    path = _write_registry(tmp_path)
    records = ae.scan_exemptions(path)
    assert all(r["domain"] != "dom_none" for r in records)


# ── 5. --json 输出可被 json.loads 解析且含预期字段 ──────────────────────────

def test_json_output_parses_with_expected_fields(tmp_path, capsys):
    path = _write_registry(tmp_path)
    rc = ae.main(["--registry", str(path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert set(payload.keys()) == {"overview", "exemptions", "total_exemptions", "no_reason_count"}
    assert payload["total_exemptions"] == 6
    assert payload["no_reason_count"] == 3  # dom_gt_bare + dom_rdt + dom_borrow_guard
    assert isinstance(payload["exemptions"], list)
    for rec in payload["exemptions"]:
        assert {"domain", "exemption_type", "detail", "date_count",
                "has_reason", "reason", "has_owner", "has_expiry"} <= set(rec.keys())
    # 风险排序: 无理由且无 owner 的排最前
    assert payload["exemptions"][0]["has_reason"] is False


# ── 附加: main() 退出码契约 (audit 模式恒 0, 出错才非零) ────────────────────

def test_main_returns_zero_on_success(tmp_path, capsys):
    path = _write_registry(tmp_path)
    rc = ae.main(["--registry", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "豁免审计报告" in out
    assert "共 6 个豁免" in out


def test_main_returns_nonzero_on_missing_registry(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.yaml"
    rc = ae.main(["--registry", str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "出错" in err


# ── 4. 防假绿: 上一字段的注释块不许被下一字段借走 ────────────────────────────

def test_field_does_not_borrow_neighbour_reason(tmp_path):
    """dom_borrow_guard.row_dip_tolerance 裸奔, 上方 data_start 有长注释块.

    2026-08-23 回归锁: 原实现向上收集注释, 会把 data_start 的理由安到
    row_dip_tolerance 头上 = 无理由的豁免显示成有理由 (假绿)。真实 registry 里
    moneyflow_hsgt.known_empty_days 正是这样被误判为「已有治理」。
    """
    reg = tmp_path / "sync_registry.yaml"
    reg.write_text(FIXTURE_REGISTRY, encoding="utf-8")
    rec = next(
        r for r in ae.scan_exemptions(reg)
        if r["domain"] == "dom_borrow_guard" and r["exemption_type"] == "row_dip_tolerance"
    )
    assert rec["has_reason"] is False, f"借走了隔壁理由: {rec['reason']!r}"
    assert rec["reason"] is None
