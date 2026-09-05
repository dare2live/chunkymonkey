"""信号层的纯函数测试 + 策略登记 fail-closed。

跑全量的口径正确性由一次实测钉住 (见 commit 0637b0ba4 之后那次的 message):
gs_raw_buy 在全市场的 n_valid=403,309 / 胜率 49.9773% / 恰好 0 占比 0.7166%,
与设计文档独立复算过的三个数逐位相同。那是 live 数据, 不在离线层跑;
这里测的是**不依赖数据库就能测错的部分** —— 分段逻辑与登记表校验。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from services.casebook import signals as sg


# ── 分段: codes 已排序, 变化点即边界 ──────────────────────────────────────────

def test_segments_splits_on_code_change() -> None:
    codes = np.array(["A", "A", "A", "B", "C", "C"])
    assert sg._segments(codes) == [("A", 0, 3), ("B", 3, 4), ("C", 4, 6)]


def test_segments_single_code() -> None:
    assert sg._segments(np.array(["A", "A"])) == [("A", 0, 2)]


def test_segments_empty() -> None:
    assert sg._segments(np.array([], dtype=object)) == []


def test_segments_covers_every_row_exactly_once() -> None:
    """分段必须是**划分**: 不重不漏。漏一段 = 那只股票整个没跑公式且不报错。"""
    codes = np.array(sum(([c] * n for c, n in [("A", 3), ("B", 1), ("C", 5), ("D", 2)]), []))
    segs = sg._segments(codes)
    covered = sorted(i for _, s, e in segs for i in range(s, e))
    assert covered == list(range(codes.size))
    assert len(segs) == 4


def test_segments_does_not_merge_nonadjacent_same_code() -> None:
    """输入若没按 code 排序, 同名会被切成两段 —— 这是**期望行为**, 不是 bug:
    调用方保证 ORDER BY code, date; 若没排序, 切两段会让下游立刻发现, 好过静默拼接。"""
    codes = np.array(["A", "B", "A"])
    assert sg._segments(codes) == [("A", 0, 1), ("B", 1, 2), ("A", 2, 3)]


# ── 策略登记 fail-closed ─────────────────────────────────────────────────────

def test_real_registry_loads() -> None:
    specs = sg.load_strategies()
    assert specs, "casebook.yaml 里应有已登记策略"
    assert all(s.kind == "frozen_formula" for s in specs)
    assert all(s.params is None for s in specs), "本系统不拟合参数, params 应为 null"
    assert len({s.strategy_id for s in specs}) == len(specs), "strategy_id 必须唯一"


def _write(tmp_path: Path, strategies: object) -> Path:
    raw = yaml.safe_load(sg._CONFIG.read_text(encoding="utf-8"))
    raw["strategies"] = strategies
    p = tmp_path / "casebook.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "strategies,hit",
    [
        ({"x": {"kind": "tdx_text", "formula_id": "x"}}, "kind"),
        ({"x": {"kind": "frozen_formula"}}, "缺 formula_id"),
        ({"x": {"kind": "frozen_formula", "formula_id": "x", "surprise": 1}}, "未知键"),
        ({"x": {"kind": "frozen_formula", "formula_id": "x", "params": [1, 2]}}, "params"),
        ({"x": "not-a-mapping"}, "必须是 mapping"),
        ("not-a-mapping", "必须是 mapping"),
    ],
)
def test_registry_is_fail_closed(tmp_path: Path, strategies: object, hit: str) -> None:
    """一条登记错了, 它产出的判例全是错的 —— 而错的判例长得和对的一模一样。

    特别是 `kind: tdx_text`: goal.md 边界段明写**不建通达信文本求值器**
    (公式在对话里转换成 Python 后再进系统)。登记表必须挡住它, 不靠人记得别写。
    """
    with pytest.raises(ValueError, match=hit):
        sg.load_strategies(_write(tmp_path, strategies))


def test_empty_registry_is_rejected_at_build(monkeypatch) -> None:
    """空登记不是"没策略跑一遍空的", 是配置坏了 —— 没有策略就没有判例可查。"""
    with pytest.raises(ValueError, match="为空"):
        sg.build(strategies=())
