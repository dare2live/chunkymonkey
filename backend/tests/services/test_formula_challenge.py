from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from services.data_sources.nominal_ohlcv_schema import PROVIDER_FIELDS
from services.data_sources.security_day_partition import canonical_content_hash
from services.formula_challenge import (
    CHALLENGER_CODE,
    FROZEN_VECTORS,
    FormulaChallengeError,
    UNIVERSE_FREEZE_DAY_COUNT,
    assert_replay_before_holdout,
    compute_formula_vectors,
    entry_overlap_diagnostics,
    fixture_bars,
    frozen_ohlcv_fixture,
    load_formula_engine,
    load_one_name_pointer_bars,
    prove_formula_pit_truncation,
    refuse_legacy_paper_source,
    refuse_universe_freeze_replay,
    simulate_formula_hold_paper,
    simulate_formula_on_live_pointer,
    simulate_formula_on_named_bars,
    simulate_formula_on_snapshot,
    vector_fingerprint,
)
from services.research_runtime import DatasetSnapshot, SnapshotInputRef
from services.snapshot_nominal_bind import offline_fixture_bars
from services.strategy_lab import ComputeRequest, assess_compute, build_ingress_plan
from services.strategy_paper import StrategyPaperError, simulate_follow_hold_paper
from services.strategy_spec import (
    CHALLENGER_PNL_SOURCE,
    FROZEN_FORMULA_IDS,
    REPO,
    StrategySpecError,
    load_strategy_spec,
)


def test_unknown_formula_fails_closed() -> None:
    with pytest.raises(FormulaChallengeError, match="unknown_formula_id"):
        compute_formula_vectors("not_a_formula", frozen_ohlcv_fixture())


def test_frozen_fixture_vectors_match_bestchoice_smoke() -> None:
    fixture = frozen_ohlcv_fixture()
    for formula_id, expected in FROZEN_VECTORS.items():
        vectors = compute_formula_vectors(formula_id, fixture)
        assert vector_fingerprint(vectors["entry"], vectors["exit"]) == expected


def test_formula_smoke_fills_next_open_not_optuna_or_vwap() -> None:
    fills = simulate_formula_hold_paper("gs_raw_buy")
    filled = [row for row in fills if row.status == "filled"]
    assert filled
    fill = filled[0]
    assert fill.pnl_source == CHALLENGER_PNL_SOURCE
    assert fill.pnl_source not in {"vwap_tradable_v1", "optuna_adoption"}
    assert fill.entry_date is not None
    assert fill.exit_date is not None
    assert fill.entry_date > fill.signal_available_at
    assert all(
        row.exit_date is not None
        and row.entry_date is not None
        and row.exit_date > row.entry_date
        for row in filled
    )
    assert len(filled) > 1


def test_named_bar_replay_refuses_plain_mapping() -> None:
    bars, _days = fixture_bars()
    with pytest.raises(FormulaChallengeError, match="named_bars_must_be_offline_fixture"):
        simulate_formula_on_named_bars("gs_raw_buy", bars)


def test_named_bar_replay_refuses_holdout_partition() -> None:
    bars, _days = fixture_bars()
    bars["20250601"] = bars[next(iter(bars))]
    with pytest.raises(FormulaChallengeError, match="holdout_partition_refused"):
        simulate_formula_on_named_bars("gs_raw_buy", offline_fixture_bars(bars))


def test_snapshot_replay_refuses_bar_day_outside_snapshot() -> None:
    bars, days = fixture_bars()
    snapshot = {
        "domains": {
            "nominal_ohlcv": {
                "accepted": [{"partition": day} for day in days[:10]],
            }
        }
    }
    with pytest.raises(FormulaChallengeError, match="bars_not_in_snapshot"):
        simulate_formula_on_snapshot(
            "gs_raw_buy",
            snapshot,
            offline_fixture_bars(bars),
        )


def test_snapshot_replay_refuses_holdout_in_snapshot_days() -> None:
    snapshot = {
        "domains": {
            "nominal_ohlcv": {
                "accepted": [{"partition": "20250530"}, {"partition": "20250601"}],
            }
        }
    }
    with pytest.raises(FormulaChallengeError, match="holdout_partition_refused"):
        simulate_formula_on_snapshot(
            "gs_raw_buy",
            snapshot,
            offline_fixture_bars({"20250530": []}),
        )


def test_snapshot_replay_runs_when_days_match() -> None:
    bars, days = fixture_bars()
    snapshot = {
        "domains": {
            "nominal_ohlcv": {
                "accepted": [{"partition": day} for day in days],
            }
        }
    }
    fills = simulate_formula_on_snapshot(
        "gs_raw_buy",
        snapshot,
        offline_fixture_bars(bars),
    )
    assert any(row.status == "filled" for row in fills)
    assert all(row.pnl_source == CHALLENGER_PNL_SOURCE for row in fills)


def test_named_bar_replay_refuses_universe_freeze() -> None:
    with pytest.raises(FormulaChallengeError, match="universe_freeze_replay_not_this_knife"):
        refuse_universe_freeze_replay(n_codes=2, n_days=UNIVERSE_FREEZE_DAY_COUNT)
    refuse_universe_freeze_replay(n_codes=1, n_days=UNIVERSE_FREEZE_DAY_COUNT)
    assert_replay_before_holdout(("20250530",), holdout_start="20250601")


def test_formula_pit_future_bars_do_not_change_prefix() -> None:
    prove_formula_pit_truncation("gs_pullback_confirm")


def test_refuse_optuna_adoption_csv_as_paper() -> None:
    path = Path("bestchoice/analysis/formula_local_optuna_batch_adoption.csv")
    with pytest.raises(FormulaChallengeError, match="legacy_optuna_or_vwap_is_not_paper"):
        refuse_legacy_paper_source(path)


def test_refuse_execution_model_as_paper() -> None:
    with pytest.raises(FormulaChallengeError, match="legacy_optuna_or_vwap_is_not_paper"):
        refuse_legacy_paper_source("bestchoice/execution_model.py")
    with pytest.raises(FormulaChallengeError, match="legacy_optuna_or_vwap_is_not_paper"):
        refuse_legacy_paper_source("vwap_tradable_v1")


def test_formula_challenge_module_does_not_import_execution_model() -> None:
    source = Path(__file__).resolve().parents[2] / "services" / "formula_challenge.py"
    text = source.read_text(encoding="utf-8")
    assert "import execution_model" not in text
    assert "from execution_model" not in text
    assert "simulate_follow_hold_paper" not in text
    assert "load_snapshot_bound_nominal_bars_by_day" not in text
    assert "consume_single_touch" not in text
    pointer = Path(__file__).resolve().parents[2] / "services" / "one_name_pointer_bars.py"
    pointer_text = pointer.read_text(encoding="utf-8")
    assert "load_snapshot_bound_nominal_bars_by_day" not in pointer_text
    assert "consume_single_touch" not in pointer_text


def test_frozen_engine_load_does_not_write_pycache(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.formula_challenge as challenge

    def _pyc_set() -> set[str]:
        return {
            path.relative_to(REPO).as_posix()
            for path in (REPO / "bestchoice").rglob("*.pyc")
            if path.is_file()
        }

    # 2026-09-03: 改成**前后快照差**, 原实现断言 bestchoice/ 下不存在任何 .pyc。
    # 那问的是"存不存在", 想守的是"这次加载写没写" —— 任何人在此之前随手 import 一次
    # formula_engine 就会让它红, 且报错栽赃给 loader。本会话三次误报 (盘点 agent / 拆锁
    # worker / 主会话测信号密度), 每次都得靠 rm -rf __pycache__ 才能提交。
    # 差集判据守的强度不变 (loader 写了就红), 归因却精确了: 只怪这次调用。
    before = _pyc_set()
    monkeypatch.setattr(challenge, "_ENGINE", None)
    load_formula_engine()
    written_by_this_load = sorted(_pyc_set() - before)
    assert written_by_this_load == [], (
        "load_formula_engine() 写了字节码, 冻结引擎的 SHA256 校验会因此失真: "
        f"{written_by_this_load}"
    )


def test_formula_spec_cannot_use_follow_paper() -> None:
    spec = load_strategy_spec("formulas:gs_raw_buy")
    with pytest.raises(StrategyPaperError, match="hold_paper_requires_institution_follow_spec"):
        simulate_follow_hold_paper({}, (), spec)


def test_overlap_diagnostics_are_not_claimable() -> None:
    report = entry_overlap_diagnostics()
    assert report["claimable"] is False
    assert report["role"] == "diagnostic_overlap_only"
    assert len(report["pairs"]) == 10


def test_formula_spec_rejects_vwap_pnl() -> None:
    spec = load_strategy_spec("formulas:gs_raw_buy")
    with pytest.raises(StrategySpecError, match="follower_pnl_must_not_use_institution_alpha"):
        spec.__class__(
            package_id=spec.package_id,
            spec_id=spec.spec_id,
            candidate_generation=spec.candidate_generation,
            ranking=spec.ranking,
            sizing=spec.sizing,
            entry_kind=spec.entry_kind,
            entry_after=spec.entry_after,
            exit_kind=spec.exit_kind,
            exit_event=spec.exit_event,
            pnl_source="vwap_tradable_v1",
            paper_status=spec.paper_status,
            max_chase_days=spec.max_chase_days,
            max_hold_calendar_days=spec.max_hold_calendar_days,
            named_not_run_max_hold_calendar_days=spec.named_not_run_max_hold_calendar_days,
            applicable_states=spec.applicable_states,
            config_hash=spec.config_hash,
            frozen_artifact_sha256=spec.frozen_artifact_sha256,
        )


def test_local_smoke_formula_spec_never_claimable() -> None:
    snapshot = DatasetSnapshot(
        snapshot_id="synthetic-development-freeze",
        inputs=(
            SnapshotInputRef(
                dataset_id="tier0.market_data.nominal_ohlcv_daily",
                partitions=("20250528", "20250529", "20250530"),
                content_hash="nominal-content",
                config_hash="nominal-config",
            ),
        ),
        universe_id="traded_on_observation_date",
        config_hash="snapshot-config",
        available_at_lower="20250528",
        available_at_upper="20250530",
        content_hash="development-content",
        frozen_at="2026-07-27T00:00:00+00:00",
        source_kind="test",
        notes=("synthetic",),
    )
    admission = assess_compute(
        build_ingress_plan(snapshot, train_end="20250529", holdout_start="20250601"),
        ComputeRequest(
            stage="local_smoke",
            executor="local",
            spec_id="formulas:gs_raw_buy",
        ),
    )
    assert admission.allowed is True
    assert admission.claimable is False
    assert set(FROZEN_FORMULA_IDS)


class _PointerConn:
    def __init__(
        self,
        pointer_rows: list[tuple[Any, ...]],
        canonical_rows: list[tuple[Any, ...]],
    ) -> None:
        self._pointer_rows = pointer_rows
        self._canonical_rows = canonical_rows
        self._rows: list[tuple[Any, ...]] = []
        self.queries: list[str] = []

    def execute(self, sql: str, _params=None):
        self.queries.append(sql)
        self._rows = (
            self._canonical_rows
            if "canonical_nominal_ohlcv_daily" in sql
            else self._pointer_rows
        )
        return self

    def fetchall(self):
        return list(self._rows)


def _pointer_bar(day: str, *, ts_code: str = CHALLENGER_CODE, close: float = 10.0) -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "trade_date": date(int(day[:4]), int(day[4:6]), int(day[6:8])),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": 9.9,
        "change": close - 9.9,
        "pct_chg": (close / 9.9 - 1.0) * 100.0,
        "vol": 1000.0,
        "amount": 10000.0,
    }


def _pointer_hash(day: str, *, close: float = 10.0) -> str:
    return canonical_content_hash([_pointer_bar(day, close=close)], PROVIDER_FIELDS)


def _pointer_tuple(
    day: str, *, ts_code: str = CHALLENGER_CODE, close: float = 10.0
) -> tuple[Any, ...]:
    row = _pointer_bar(day, ts_code=ts_code, close=close)
    return tuple(row[field] for field in PROVIDER_FIELDS)


def _pointer_snap(days: list[str], *, hashes: dict[str, str] | None = None) -> dict[str, Any]:
    hashes = hashes or {d: _pointer_hash(d) for d in days}
    return {
        "domains": {
            "nominal_ohlcv": {
                "accepted": [
                    {
                        "partition": d,
                        "content_hash": hashes[d],
                        "row_count": 1,
                        "config_hash": "cfg1",
                        "contract_hash": "contract1",
                        "batch_id": f"batch-{d}",
                        "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                    }
                    for d in days
                ]
            }
        }
    }


def test_live_pointer_sql_filters_one_ts_code() -> None:
    days = ["20250529", "20250530"]
    conn = _PointerConn(
        [(day, f"batch-{day}", 1, _pointer_hash(day), "contract1", "cfg1") for day in days],
        [_pointer_tuple(day) for day in days],
    )
    bars = load_one_name_pointer_bars(
        _pointer_snap(days),
        conn,
        CHALLENGER_CODE,
        days=days,
    )
    assert set(bars) == set(days)
    canonical = next(sql for sql in conn.queries if "canonical_nominal_ohlcv_daily" in sql)
    assert "ts_code = ?" in canonical
    pointer_sql = conn.queries[0]
    assert "accepted_partition" in pointer_sql
    assert "canonical_nominal_ohlcv_daily" not in pointer_sql


def test_live_pointer_holdout_is_refused_before_query() -> None:
    conn = _PointerConn([], [])
    with pytest.raises(FormulaChallengeError, match="holdout_partition_refused"):
        load_one_name_pointer_bars(
            _pointer_snap(["20250530"]),
            conn,
            CHALLENGER_CODE,
            days=["20250601"],
        )
    assert conn.queries == []


def test_live_pointer_hash_drift_is_mismatch() -> None:
    day = "20250530"
    conn = _PointerConn(
        [(day, f"batch-{day}", 1, "drifted-hash", "contract1", "cfg1")],
        [_pointer_tuple(day)],
    )
    with pytest.raises(FormulaChallengeError, match="live_pointer_mismatch"):
        load_one_name_pointer_bars(_pointer_snap([day]), conn, CHALLENGER_CODE, days=[day])
    assert all("canonical_nominal_ohlcv_daily" not in sql for sql in conn.queries)


def test_live_pointer_wrong_code_is_refused() -> None:
    days = ["20250529", "20250530"]
    conn = _PointerConn(
        [(day, f"batch-{day}", 1, _pointer_hash(day), "contract1", "cfg1") for day in days],
        [_pointer_tuple(day, ts_code="000002.SZ") for day in days],
    )
    with pytest.raises(FormulaChallengeError, match="live_pointer_loaded_other_names"):
        load_one_name_pointer_bars(
            _pointer_snap(days),
            conn,
            CHALLENGER_CODE,
            days=days,
        )


def test_live_pointer_two_day_paper_may_have_zero_fills() -> None:
    days = ["20250529", "20250530"]
    conn = _PointerConn(
        [(day, f"batch-{day}", 1, _pointer_hash(day), "contract1", "cfg1") for day in days],
        [_pointer_tuple(day) for day in days],
    )
    fills = simulate_formula_on_live_pointer(
        "gs_raw_buy",
        _pointer_snap(days),
        conn,
        ts_code=CHALLENGER_CODE,
        days=days,
    )
    assert isinstance(fills, tuple)
    assert all(row.pnl_source == CHALLENGER_PNL_SOURCE for row in fills)
