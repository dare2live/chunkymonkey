"""``services.exchange_cell_verdicts`` 的隔离用例。

L1-L21: ``load_exchange_cell_verdicts()`` 的每个 fail-closed 门控条件各一个
用例, 输入构造成"其它条件全部满足, 只这一条为假"。
M1-M12: ``consume()`` 的每个分类分支各一个用例。

参照规格: scratchpad/bt_residual_classes_r1.md §6 C1 (L1-L21 / M1-M12 表格
与变异清单)。
"""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest
import yaml

import services.exchange_cell_verdicts as ecv
from services.exchange_cell_verdicts import (
    CellEntry,
    CellVerdictSet,
    Observed,
    consume,
    load_exchange_cell_verdicts,
)

# ---------------------------------------------------------------------------
# 共用夹具
# ---------------------------------------------------------------------------

_VALID_CELL = ["20230704", "sh", "688234", "70.40", "4.00"]
_EXCHANGE_ROW = [
    "广发证券股份有限公司上海分公司(对外营业部)",
    "海通证券股份有限公司沈阳大西路证券营业部",
    1,
]
_VENDOR_ROW = [
    "广发证券股份有限公司上海分公司",
    "海通证券股份有限公司沈阳大西路证券营业部",
    1,
]

CELL_A = ("20230704", "sh", "688234", "70.40", "4.00")
CELL_B = ("20230705", "sh", "688235", "10.00", "1.00")
ROW_A = ("广发证券股份有限公司上海分公司(对外营业部)", "海通证券股份有限公司沈阳大西路证券营业部")
ROW_B = ("广发证券股份有限公司上海分公司", "海通证券股份有限公司沈阳大西路证券营业部")
# 与 ROW_A 只差一个字 (营业部 -> 业务部), 模拟 N1 里"供应商/交易所写法逐行不
# 一致"的最小复现: 不是全角/半角/空白这类可归一的差异。
ROW_A_DRIFTED = ("广发证券股份有限公司上海分公司(对外业务部)", "海通证券股份有限公司沈阳大西路证券营业部")


def _valid_entry() -> dict:
    """一条"其它条件全部满足"的合法登记条目 (dict 形式, 供 YAML 序列化)。"""
    return {
        "domain": "block_trade",
        "cells": [list(_VALID_CELL)],
        "exchange_unmatched": [list(_EXCHANGE_ROW)],
        "vendor_unmatched": [list(_VENDOR_ROW)],
        "text_pairs": [[0, 0, 1]],
        "truth_side": "unknown",
        "evidence": "exch_sh_20230704.json 行 NUM=1; 妙想同日同码行 buyer 少后缀",
        "checked_at": "2026-09-12",
    }


def _write_doc(tmp_path: Path, document: dict, name: str = "verdicts.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, document: dict) -> CellVerdictSet:
    path = _write_doc(tmp_path, document)
    return load_exchange_cell_verdicts(path)


def _entry(
    cells,
    exchange_unmatched=(),
    vendor_unmatched=(),
    text_pairs=(),
    truth_side="unknown",
    evidence="test evidence",
    checked_at="2026-09-12",
    domain="block_trade",
) -> CellEntry:
    """直接构造 ``CellEntry`` (绕过 loader), 供 consume() 的隔离用例使用。"""
    return CellEntry(
        domain=domain,
        cells=tuple(cells),
        exchange_unmatched=tuple(exchange_unmatched),
        vendor_unmatched=tuple(vendor_unmatched),
        text_pairs=tuple(text_pairs),
        truth_side=truth_side,
        evidence=evidence,
        checked_at=checked_at,
    )


def _verdict_set(entries: list[CellEntry]) -> CellVerdictSet:
    by_cell: dict = {}
    for entry in entries:
        for cell in entry.cells:
            by_cell[cell] = entry
    return CellVerdictSet(entries=tuple(entries), by_cell=by_cell, sha256="test-sha")


# ---------------------------------------------------------------------------
# L1-L21: load_exchange_cell_verdicts() 门控条件
# ---------------------------------------------------------------------------


def test_L1_top_level_unknown_key(tmp_path):
    document = {"version": 1, "entries": [_valid_entry()], "notes": "extra"}
    with pytest.raises(ValueError, match="top-level keys"):
        _load(tmp_path, document)


def test_L2_version_not_one(tmp_path):
    document = {"version": 2, "entries": [_valid_entry()]}
    with pytest.raises(ValueError, match="version"):
        _load(tmp_path, document)


def test_L3_entry_unknown_key(tmp_path):
    entry = _valid_entry()
    entry["kind"] = "gap"
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match=r"entries\[0\] keys"):
        _load(tmp_path, document)


def test_L4_entry_missing_key(tmp_path):
    entry = _valid_entry()
    del entry["truth_side"]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match=r"entries\[0\] keys"):
        _load(tmp_path, document)


def test_L5_domain_without_cell_cols(tmp_path):
    entry = _valid_entry()
    entry["domain"] = "top_inst"
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="cell_cols"):
        _load(tmp_path, document)


def test_L6_cells_empty(tmp_path):
    entry = _valid_entry()
    entry["cells"] = []
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="cells"):
        _load(tmp_path, document)


def test_L7_cell_date_wrong_length(tmp_path):
    entry = _valid_entry()
    entry["cells"] = [["2023070", "sh", "688234", "70.40", "4.00"]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="trade_date"):
        _load(tmp_path, document)


def test_L8_cell_venue_invalid(tmp_path):
    entry = _valid_entry()
    entry["cells"] = [["20230704", "bj", "688234", "70.40", "4.00"]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="venue"):
        _load(tmp_path, document)


def test_L9_cell_code_wrong_length(tmp_path):
    entry = _valid_entry()
    entry["cells"] = [["20230704", "sh", "68823", "70.40", "4.00"]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="code"):
        _load(tmp_path, document)


def test_L10_cell_price_not_2dp(tmp_path):
    entry = _valid_entry()
    entry["cells"] = [["20230704", "sh", "688234", "70.4", "4.00"]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="price"):
        _load(tmp_path, document)


def test_L11_both_unmatched_empty(tmp_path):
    entry = _valid_entry()
    entry["exchange_unmatched"] = []
    entry["vendor_unmatched"] = []
    entry["text_pairs"] = []
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="explains nothing"):
        _load(tmp_path, document)


def test_L12_row_count_zero(tmp_path):
    entry = _valid_entry()
    entry["exchange_unmatched"] = [
        ["广发证券股份有限公司上海分公司(对外营业部)", "海通证券股份有限公司沈阳大西路证券营业部", 0]
    ]
    entry["vendor_unmatched"] = []
    entry["text_pairs"] = []
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="count"):
        _load(tmp_path, document)


def test_L13_row_not_normalized(tmp_path):
    entry = _valid_entry()
    entry["exchange_unmatched"] = [
        ["广发证券（分公司）", "海通证券股份有限公司沈阳大西路证券营业部", 1]
    ]
    entry["vendor_unmatched"] = []
    entry["text_pairs"] = []
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="normalize"):
        _load(tmp_path, document)


def test_L14_text_pairs_index_out_of_range(tmp_path):
    entry = _valid_entry()
    entry["text_pairs"] = [[1, 0, 1]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="text_pairs"):
        _load(tmp_path, document)


def test_L15_text_pairs_over_share(tmp_path):
    entry = _valid_entry()
    entry["text_pairs"] = [[0, 0, 2]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="text_pairs"):
        _load(tmp_path, document)


def test_L16_text_pairs_double_consume(tmp_path):
    entry = _valid_entry()
    entry["text_pairs"] = [[0, 0, 1], [0, 0, 1]]
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="text_pairs"):
        _load(tmp_path, document)


def test_L17_truth_side_invalid(tmp_path):
    entry = _valid_entry()
    entry["truth_side"] = "maybe"
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="truth_side"):
        _load(tmp_path, document)


def test_L18_evidence_empty(tmp_path):
    entry = _valid_entry()
    entry["evidence"] = ""
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="evidence"):
        _load(tmp_path, document)


def test_L19_checked_at_wrong_format(tmp_path):
    entry = _valid_entry()
    entry["checked_at"] = "2026/09/12"
    document = {"version": 1, "entries": [entry]}
    with pytest.raises(ValueError, match="checked_at"):
        _load(tmp_path, document)


def test_L20_duplicate_cell_across_entries(tmp_path):
    document = {"version": 1, "entries": [_valid_entry(), _valid_entry()]}
    with pytest.raises(ValueError, match="duplicate cell"):
        _load(tmp_path, document)


def test_L21_valid_entry_loads_and_hash_matches(tmp_path):
    document = {"version": 1, "entries": [_valid_entry()]}
    path = _write_doc(tmp_path, document)
    verdicts = load_exchange_cell_verdicts(path)
    assert tuple(_VALID_CELL) in verdicts.by_cell
    assert verdicts.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_L21_repo_yaml_loads_with_empty_entries():
    verdicts = load_exchange_cell_verdicts()
    assert verdicts.entries == ()
    assert verdicts.by_cell == {}


# ---------------------------------------------------------------------------
# M1-M12: consume() 分类分支
# ---------------------------------------------------------------------------


def test_M1_stale_cell_now_matched():
    entry = _entry(cells=[CELL_A], exchange_unmatched=[(ROW_A, 1)])
    verdicts = _verdict_set([entry])
    report = consume({}, verdicts)
    assert report.per_cell[CELL_A]["status"] == "stale"
    assert CELL_A in report.stale_cells


def test_M2_stale_gap_drifted():
    """gap 侧比登记多一行, extra 侧与登记完全一致 (text_pairs 为空, 与 M4 区分)。"""
    row_c = ("测试新增公司", "测试新增营业部")
    entry = _entry(cells=[CELL_A], exchange_unmatched=[(ROW_A, 1)], vendor_unmatched=[(ROW_B, 1)])
    verdicts = _verdict_set([entry])
    observed = {
        CELL_A: Observed(
            gap=Counter({ROW_A: 1, row_c: 1}), extra=Counter({ROW_B: 1}), exchange_all=Counter()
        )
    }
    report = consume(observed, verdicts)
    assert report.per_cell[CELL_A]["status"] == "stale"
    assert CELL_A in report.stale_cells


def test_M3_stale_extra_drifted():
    """extra 侧比登记少一行 (登记的那行已经消失), gap 侧与登记完全一致——格
    本身并非"完全 matched" (gap 侧仍有残差), 专门隔离 extra 多重集不等这一条。"""
    entry = _entry(cells=[CELL_A], exchange_unmatched=[(ROW_A, 1)], vendor_unmatched=[(ROW_B, 1)])
    verdicts = _verdict_set([entry])
    observed = {CELL_A: Observed(gap=Counter({ROW_A: 1}), extra=Counter(), exchange_all=Counter())}
    report = consume(observed, verdicts)
    assert report.per_cell[CELL_A]["status"] == "stale"
    assert CELL_A in report.stale_cells


def test_M4_contradiction_pair_references_missing_row():
    """与 M2 区分: 这里 text_pairs 非空, 且配对指向的行字符串与观测差一字。"""
    entry = _entry(
        cells=[CELL_A],
        exchange_unmatched=[(ROW_A, 1)],
        vendor_unmatched=[(ROW_B, 1)],
        text_pairs=[(0, 0, 1)],
    )
    verdicts = _verdict_set([entry])
    observed = {
        CELL_A: Observed(gap=Counter({ROW_A_DRIFTED: 1}), extra=Counter({ROW_B: 1}), exchange_all=Counter())
    }
    report = consume(observed, verdicts)
    assert report.per_cell[CELL_A]["status"] == "contradiction"
    assert CELL_A in report.stale_cells


def test_M5_text_consumed():
    entry = _entry(
        cells=[CELL_A],
        exchange_unmatched=[(ROW_A, 1)],
        vendor_unmatched=[(ROW_B, 1)],
        text_pairs=[(0, 0, 1)],
    )
    verdicts = _verdict_set([entry])
    observed = {CELL_A: Observed(gap=Counter({ROW_A: 1}), extra=Counter({ROW_B: 1}), exchange_all=Counter())}
    report = consume(observed, verdicts)
    result = report.per_cell[CELL_A]
    assert result["status"] == "consumed"
    assert result["text"] == 1
    assert result["missing"] == 0
    assert result["extra_duplicate"] == 0
    assert result["extra_phantom"] == 0
    assert CELL_A in report.consumed_cells


def test_M6_missing_consumed():
    entry = _entry(cells=[CELL_A], exchange_unmatched=[(ROW_A, 1)])
    verdicts = _verdict_set([entry])
    observed = {CELL_A: Observed(gap=Counter({ROW_A: 1}), extra=Counter(), exchange_all=Counter())}
    report = consume(observed, verdicts)
    result = report.per_cell[CELL_A]
    assert result["status"] == "consumed"
    assert result["missing"] == 1
    assert result["text"] == 0


def test_M7_extra_duplicate_subclass():
    entry = _entry(cells=[CELL_A], vendor_unmatched=[(ROW_B, 1)])
    verdicts = _verdict_set([entry])
    observed = {
        CELL_A: Observed(gap=Counter(), extra=Counter({ROW_B: 1}), exchange_all=Counter({ROW_B: 1}))
    }
    report = consume(observed, verdicts)
    result = report.per_cell[CELL_A]
    assert result["extra_duplicate"] == 1
    assert result["extra_phantom"] == 0


def test_M8_extra_phantom_subclass():
    entry = _entry(cells=[CELL_A], vendor_unmatched=[(ROW_B, 1)])
    verdicts = _verdict_set([entry])
    observed = {CELL_A: Observed(gap=Counter(), extra=Counter({ROW_B: 1}), exchange_all=Counter())}
    report = consume(observed, verdicts)
    result = report.per_cell[CELL_A]
    assert result["extra_duplicate"] == 0
    assert result["extra_phantom"] == 1


def test_M9_unregistered_temp_classification():
    verdicts = _verdict_set([])
    observed = {
        CELL_A: Observed(gap=Counter({ROW_A: 2}), extra=Counter({ROW_B: 1}), exchange_all=Counter())
    }
    report = consume(observed, verdicts)
    result = report.per_cell[CELL_A]
    assert result["status"] == "unregistered"
    assert result["text_candidate"] == 1
    assert result["missing_unregistered"] == 1
    assert result["extra_unregistered"] == 0


def test_M10_three_three_same_shape():
    entry = _entry(
        cells=[CELL_A],
        exchange_unmatched=[(ROW_A, 3)],
        vendor_unmatched=[(ROW_B, 3)],
        text_pairs=[(0, 0, 3)],
    )
    verdicts = _verdict_set([entry])
    observed = {CELL_A: Observed(gap=Counter({ROW_A: 3}), extra=Counter({ROW_B: 3}), exchange_all=Counter())}
    report = consume(observed, verdicts)
    result = report.per_cell[CELL_A]
    assert result["status"] == "consumed"
    assert result["text"] == 3
    assert result["missing"] == 0
    assert result["extra_duplicate"] == 0
    assert result["extra_phantom"] == 0


def test_M11_partition_invariant_breaks_on_bad_accounting(monkeypatch):
    entry = _entry(
        cells=[CELL_A],
        exchange_unmatched=[(ROW_A, 1)],
        vendor_unmatched=[(ROW_B, 1)],
        text_pairs=[(0, 0, 1)],
    )
    verdicts = _verdict_set([entry])
    observed = {CELL_A: Observed(gap=Counter({ROW_A: 1}), extra=Counter({ROW_B: 1}), exchange_all=Counter())}

    def _broken_consume_cell(entry, obs):
        # 故意让同一行既算 text 又算 missing (真实实现里 missing 只应统计未被
        # text_pairs 消费掉的剩余份数)。
        return {
            "status": "consumed",
            "text": 1,
            "missing": 1,
            "extra_duplicate": 0,
            "extra_phantom": 0,
            "text_candidate": 0,
            "missing_unregistered": 0,
            "extra_unregistered": 0,
        }

    monkeypatch.setattr(ecv, "_consume_cell", _broken_consume_cell)
    with pytest.raises(AssertionError, match="partition"):
        ecv.consume(observed, verdicts)


def test_M12_multiple_cells_independent():
    entry = _entry(
        cells=[CELL_A, CELL_B],
        exchange_unmatched=[(ROW_A, 1)],
        vendor_unmatched=[(ROW_B, 1)],
        text_pairs=[(0, 0, 1)],
    )
    verdicts = _verdict_set([entry])
    observed = {
        CELL_A: Observed(gap=Counter({ROW_A: 1}), extra=Counter({ROW_B: 1}), exchange_all=Counter()),
        # CELL_B 不在 observed 里 => 该格已 matched, 但登记仍声称有残差 => stale。
    }
    report = consume(observed, verdicts)
    assert report.per_cell[CELL_A]["status"] == "consumed"
    assert CELL_A in report.consumed_cells
    assert report.per_cell[CELL_B]["status"] == "stale"
    assert CELL_B in report.stale_cells
