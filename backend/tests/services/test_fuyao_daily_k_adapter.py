"""Offline tests for the fuyao dump + baostock daily_k_dump adapter (刀2).

All three legs — the dump downloader, the baostock reference source, and the
config file — are exercised without any network access:
  * the "downloader" is a fake object whose ``.fetch(kind)`` returns a tiny
    real Parquet file written to ``tmp_path`` (so parsing/filtering is
    exercised for real, only the HTTP download is faked);
  * the "baostock session" is either a bare fake object implementing
    ``fetch_raw(api, **params)`` (for pure row-classification assertions) or
    a real ``BaostockSource(bs_module=<fake baostock module>)`` (for the
    circuit-breaker assertion, reusing baostock.py's real login/lock/retry
    machinery exactly like ``test_baostock_adapter.py`` does);
  * ``load_nominal_ohlcv_acquire_rules()`` with no ``path`` argument reads
    the real ``backend/config/nominal_ohlcv_acquire.yaml`` — at least one
    test per rule 11 must do this without monkeypatching the config.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


from services.data_sources.sources import baostock as _bao_mod


@pytest.fixture(autouse=True)
def _isolate_session_lock_path(monkeypatch, tmp_path):
    """熔断用例会驱动真实 BaostockSource._ensure_login -> _acquire_process_lock;
    不隔离就会抢仓库真实的 data/scratch/baostock_session.lock, 与生产取数互踩
    (2026-09-18 实测: 后台 fuyao+baostock 实取数持锁期间, 本文件与
    test_baostock_adapter.py 共 4 个用例假红, 挡住一次提交)。"""
    monkeypatch.setenv(_bao_mod._SESSION_LOCK_PATH_ENV, str(tmp_path / "baostock_session.lock"))


from services.data_sources.nominal_ohlcv_acquire_rules import (
    load_nominal_ohlcv_acquire_rules,
)
from services.data_sources.nominal_ohlcv_contract import nominal_ohlcv_contract_for_spec
from services.data_sources.nominal_ohlcv_schema import PROVIDER_FIELDS
from services.data_sources.security_day_capture import (
    ProviderPage,
    build_security_day_landing_batch,
    capture_security_day_provider_rows,
)
from services.data_sources.sources.baostock import (
    BSERR_BLACKLIST_USER,
    BSERR_SUCCESS,
    BaostockQueryError,
    BaostockSource,
)
from services.data_sources.sources.fuyao import FuyaoSource, shanghai_midnight_ms
from services.data_sources.sources.fuyao_daily_k import (
    BaostockCircuitOpenError,
    FuyaoDailyKAdapter,
    FuyaoDailyKDeps,
    FuyaoDailyKError,
    SurvivorGateResult,
    compute_survivor_gate,
    default_deps,
    fetch_daily_k_dump_rows,
)
from services.data_sources.sync_runner import load_registry

DAY = "20260901"  # first real dump day per F4/§1; also the incremental_floor


# ---------------------------------------------------------------------------
# fixtures: real tiny parquet dumps + fake downloader/baostock
# ---------------------------------------------------------------------------


def _write_dump_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    """Writes a real Parquet file with the fuyao dump's own column names
    (F4: thscode/currency/interval/adjusted/date_ms/open_price/high_price/
    low_price/close_price/volume/turnover) — same shape
    ``fuyao_kline_recon.py::load_fuyao_kline`` reads."""

    import duckdb

    conn = duckdb.connect()
    try:
        conn.execute(
            """
            CREATE TABLE t (
                thscode VARCHAR, currency VARCHAR, interval VARCHAR, adjusted VARCHAR,
                date_ms BIGINT, open_price DOUBLE, high_price DOUBLE, low_price DOUBLE,
                close_price DOUBLE, volume BIGINT, turnover BIGINT
            )
            """
        )
        conn.executemany(
            "INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r["thscode"],
                    r.get("currency", "CNY"),
                    r.get("interval", "1d"),
                    r.get("adjusted", "none"),
                    int(r["date_ms"]),
                    float(r["open_price"]),
                    float(r["high_price"]),
                    float(r["low_price"]),
                    float(r["close_price"]),
                    int(r["volume"]),
                    int(r["turnover"]),
                )
                for r in rows
            ],
        )
        conn.execute(f"COPY t TO '{path.as_posix()}' (FORMAT PARQUET)")
    finally:
        conn.close()


def _dump_row(ts_code: str, trade_date: str, *, close: float, open_: float | None = None,
              volume: int = 12345, turnover: int = 987654) -> dict[str, Any]:
    return {
        "thscode": ts_code,
        "date_ms": shanghai_midnight_ms(trade_date),
        "open_price": open_ if open_ is not None else close,
        "high_price": close + 0.1,
        "low_price": close - 0.1,
        "close_price": close,
        "volume": volume,
        "turnover": turnover,
    }


@dataclass
class _FakeDownloadedDump:
    """Mirrors ``marketdb.providers.dump.DownloadedDump``'s field names
    (``path``/``release_tag``/``release_key``) — the adapter reads those
    exact attributes off whatever the downloader returns, real or fake.
    Defaults are non-``None`` placeholders distinct per instance's actual
    value below, so any test that doesn't care about release identity keeps
    passing unchanged; the isolated provenance test overrides them."""

    path: Path
    release_tag: str = "fake-release-tag"
    release_key: str = "fake-release-key"


class _FakeDownloader:
    """``.fetch(kind)`` returns a pre-written local parquet path (plus a fake
    release identity) per kind and logs which kinds were requested, in order —
    the seam assertion 6 checks."""

    def __init__(
        self,
        paths: dict[Any, Path],
        *,
        releases: dict[Any, tuple[str, str]] | None = None,
    ) -> None:
        self._paths = paths
        self._releases = releases or {}
        self.requested_kinds: list[Any] = []

    def fetch(self, kind: Any) -> _FakeDownloadedDump:
        self.requested_kinds.append(kind)
        if kind not in self._paths:
            raise AssertionError(f"unexpected dump kind requested: {kind!r}")
        if kind in self._releases:
            release_tag, release_key = self._releases[kind]
            return _FakeDownloadedDump(
                path=self._paths[kind], release_tag=release_tag, release_key=release_key
            )
        return _FakeDownloadedDump(path=self._paths[kind])


# The real enum (not a fake stand-in): its members are what
# ``sources/fuyao.py::dump_kinds()`` actually returns in production, and the
# adapter reads ``.value`` off them for the request_meta tag — a fake with
# plain-string members would silently skip exercising that.
from marketdb.providers.dump import DownloadKind as _DummyDumpKinds


class _FakeBaostockLeg:
    """Bare fake baostock session for row-classification assertions — no real
    ``BaostockSource`` involved (that machinery is exercised separately by
    the circuit-breaker test, which needs its login/lock behavior for real)."""

    def __init__(self, rows_by_code: dict[str, list[dict[str, Any]]] | None = None,
                 *, raise_for_code: dict[str, Exception] | None = None) -> None:
        self._rows_by_code = rows_by_code or {}
        self._raise_for_code = raise_for_code or {}
        self.calls: list[dict[str, Any]] = []

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        assert api == "query_history_k_data_plus"
        self.calls.append(dict(params))
        code = params["code"]
        if code in self._raise_for_code:
            raise self._raise_for_code[code]
        return list(self._rows_by_code.get(code, []))


def _rules():
    return load_nominal_ohlcv_acquire_rules()


def _deps(*, dump_paths: dict[Any, Path], baostock: Any) -> FuyaoDailyKDeps:
    return FuyaoDailyKDeps(
        downloader=_FakeDownloader(dump_paths),
        dump_kinds=_DummyDumpKinds,
        baostock_source=baostock,
        rules=_rules(),
    )


def _adapter(*, dump_paths: dict[Any, Path], baostock: Any) -> tuple[FuyaoDailyKAdapter, _FakeDownloader]:
    deps = _deps(dump_paths=dump_paths, baostock=baostock)
    adapter = FuyaoDailyKAdapter(lambda: deps)
    return adapter, deps.downloader  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. unit conversion
# ---------------------------------------------------------------------------


def test_unit_conversion_volume_and_turnover(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    # BJ code sidesteps baostock entirely — isolates the unit-conversion path
    # from the reference-lookup path (each gating condition tested alone).
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", DAY, close=10.0, volume=12345, turnover=987654)])
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=_FakeBaostockLeg())

    rows = adapter.fetch_rows(DAY)

    assert len(rows) == 1
    row = rows[0]
    assert row["vol"] == pytest.approx(123.45)
    assert row["amount"] == pytest.approx(987.654)


# ---------------------------------------------------------------------------
# 2-4. SH/SZ reference classification (matching / mismatched / unavailable)
# ---------------------------------------------------------------------------


def test_sh_row_with_matching_reference_gets_provider_baostock(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({
        "sh.600000": [{"date": "2026-09-01", "close": "9.35", "preclose": "9.16"}],
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    assert len(rows) == 1
    row = rows[0]
    assert row["pre_close_origin"] == "provider_baostock"
    assert row["pre_close"] == pytest.approx(9.16)
    assert row["change"] == pytest.approx(0.19)
    assert row["pct_chg"] == pytest.approx(2.0742)


def test_pct_chg_half_value_boundary_rounds_correctly(tmp_path):
    """6.41/6.40: 精确值 (6.41-6.40)/6.40*100 == 0.15625, ROUND_HALF_UP 应进到
    0.1563。先在 float 上算 (6.41-6.40)/6.40*100 得到 0.15624999999999667 (float
    二进制误差, 差一个 ULP), 再喂 Decimal(str(..)) 量化会错舍成 0.1562 —— 必须全程
    Decimal 才对。这条用例专测这个半值边界, 与上面整数值边界的用例分开隔离。"""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=6.41)])
    baostock = _FakeBaostockLeg({
        "sh.600000": [{"date": "2026-09-01", "close": "6.41", "preclose": "6.40"}],
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    row = rows[0]
    assert row["pre_close_origin"] == "provider_baostock"
    assert row["change"] == pytest.approx(0.01)
    assert row["pct_chg"] == pytest.approx(0.1563)


def test_reference_api_is_read_from_config_not_hardcoded(tmp_path):
    """回归钉子: adapter 必须真的从 rules.reference_api 读端点名, 不能退化成在
    ``_query``/``compute_survivor_gate`` 里各自另抄一份字面量 —— 把配置换成白名单
    里的另一个端点 (query_daily_history_k_AStock), fake baostock 收到的 api 参数
    必须跟着变, 而不是仍然收到写死的 query_history_k_data_plus。"""
    data = _acquire_yaml_dict()
    data["reference"]["api"] = "query_daily_history_k_AStock"
    path = _write_acquire_yaml(tmp_path, data)
    rules = load_nominal_ohlcv_acquire_rules(path)

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])

    class _RecordingBaostockLeg:
        def __init__(self) -> None:
            self.seen_apis: list[str] = []

        def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
            self.seen_apis.append(api)
            return [{"date": "2026-09-01", "close": "9.35", "preclose": "9.16"}]

    baostock = _RecordingBaostockLeg()
    deps = FuyaoDailyKDeps(
        downloader=_FakeDownloader({_DummyDumpKinds.DAILY_K_10D: ten_d}),
        dump_kinds=_DummyDumpKinds,
        baostock_source=baostock,
        rules=rules,
    )
    adapter = FuyaoDailyKAdapter(lambda: deps)

    adapter.fetch_rows(DAY)

    assert baostock.seen_apis == ["query_daily_history_k_AStock"]

    # compute_survivor_gate is the second call site with its own literal —
    # exercise it too so both are pinned by the same config-swap.
    gate_baostock = _RecordingBaostockLeg()
    compute_survivor_gate(
        canonical_codes=["600001.SH"],
        dump_codes=[],
        baostock_source=gate_baostock,
        rules=rules,
        window_dates=("20260901", "20260902"),
    )
    assert gate_baostock.seen_apis == ["query_daily_history_k_AStock"]


def test_default_deps_reads_cache_dir_from_config_not_hardcoded(tmp_path, monkeypatch):
    """回归钉子: ``default_deps()`` 传给 ``dump_downloader`` 的 cache_dir 必须来自
    ``rules.dump_cache_dir`` (仓库相对, 用 _REPO_ROOT 解析), 不能退化成模块顶层
    另写一份字面量路径 —— 换一个配置里的 cache_dir, dump_downloader 收到的必须
    跟着变。"""
    data = _acquire_yaml_dict()
    data["dump"]["cache_dir"] = "data/scratch/fuyao_dumps_alt_for_test"
    path = _write_acquire_yaml(tmp_path, data)
    rules = load_nominal_ohlcv_acquire_rules(path)

    captured: dict[str, Any] = {}

    def _fake_dump_downloader(*, api_key: str, cache_dir: Path, **kwargs: Any):
        captured["cache_dir"] = cache_dir
        return object()

    monkeypatch.setattr(
        "services.data_sources.sources.fuyao.dump_downloader", _fake_dump_downloader
    )
    monkeypatch.setattr(
        "services.data_sources.sources.fuyao.resolve_api_key", lambda: "fake-key-for-test"
    )
    monkeypatch.setattr(
        "services.data_sources.sources.baostock.BaostockSource", lambda: object()
    )

    default_deps(rules=rules)

    from services.data_sources.sources.fuyao_daily_k import _REPO_ROOT

    assert captured["cache_dir"] == _REPO_ROOT / "data/scratch/fuyao_dumps_alt_for_test"


def test_sh_row_with_mismatched_close_gets_null_and_mismatch_origin(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({
        # close differs by 0.15, far outside reference.close_tolerance (0.005).
        "sh.600000": [{"date": "2026-09-01", "close": "9.50", "preclose": "9.16"}],
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    row = rows[0]
    assert row["pre_close_origin"] == "unknown_reference_mismatch"
    assert row["pre_close"] is None
    assert row["change"] is None
    assert row["pct_chg"] is None


def test_sh_row_with_no_reference_row_gets_unavailable_origin(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    # Fake baostock has no rows at all for this code (empty prefetch window
    # AND the single-day fallback both come back empty).
    baostock = _FakeBaostockLeg({})
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    row = rows[0]
    assert row["pre_close_origin"] == "unknown_reference_unavailable"
    assert row["pre_close"] is None
    assert row["change"] is None
    assert row["pct_chg"] is None


# ---------------------------------------------------------------------------
# 5. BJ row never touches baostock
# ---------------------------------------------------------------------------


def test_bj_row_gets_unknown_no_reference_and_never_calls_baostock(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(
        ten_d,
        [
            _dump_row("600000.SH", DAY, close=9.35),
            _dump_row("920819.BJ", DAY, close=10.0),
        ],
    )
    baostock = _FakeBaostockLeg({
        "sh.600000": [{"date": "2026-09-01", "close": "9.35", "preclose": "9.16"}],
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    by_code = {r["ts_code"]: r for r in rows}
    bj_row = by_code["920819.BJ"]
    assert bj_row["pre_close_origin"] == "unknown_no_reference_bj"
    assert bj_row["pre_close"] is None
    assert bj_row["change"] is None
    assert bj_row["pct_chg"] is None
    # The SH row's lookup must be the only baostock call — no "bj." code ever
    # reaches the fake baostock session.
    called_codes = [c["code"] for c in baostock.calls]
    assert all(not code.startswith("bj.") for code in called_codes)
    assert "sh.600000" in called_codes


# ---------------------------------------------------------------------------
# 6. dump-kind selection order
# ---------------------------------------------------------------------------


def test_dump_kind_selection_uses_10d_when_it_covers_the_date(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    full = tmp_path / "full.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", DAY, close=10.0)])
    _write_dump_parquet(full, [_dump_row("920819.BJ", "20260801", close=9.0)])
    adapter, downloader = _adapter(
        dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d, _DummyDumpKinds.DAILY_K: full},
        baostock=_FakeBaostockLeg(),
    )

    rows = adapter.fetch_rows(DAY)

    assert len(rows) == 1
    assert downloader.requested_kinds == [_DummyDumpKinds.DAILY_K_10D]


def test_dump_kind_selection_falls_back_to_full_dump_when_10d_lacks_the_date(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    full = tmp_path / "full.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", "20260805", close=9.0)])  # not DAY
    _write_dump_parquet(full, [_dump_row("920819.BJ", DAY, close=10.0)])
    adapter, downloader = _adapter(
        dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d, _DummyDumpKinds.DAILY_K: full},
        baostock=_FakeBaostockLeg(),
    )

    rows = adapter.fetch_rows(DAY)

    assert len(rows) == 1
    assert downloader.requested_kinds == [_DummyDumpKinds.DAILY_K_10D, _DummyDumpKinds.DAILY_K]


def test_dump_kind_selection_returns_empty_when_neither_kind_has_the_date(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    full = tmp_path / "full.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", "20260805", close=9.0)])
    _write_dump_parquet(full, [_dump_row("920819.BJ", "20260806", close=9.0)])
    adapter, downloader = _adapter(
        dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d, _DummyDumpKinds.DAILY_K: full},
        baostock=_FakeBaostockLeg(),
    )

    rows = adapter.fetch_rows(DAY)

    assert rows == []
    assert downloader.requested_kinds == [_DummyDumpKinds.DAILY_K_10D, _DummyDumpKinds.DAILY_K]


# ---------------------------------------------------------------------------
# 6b. build_page() dump release provenance (blocking 发现修复回归钉子)
# ---------------------------------------------------------------------------


def test_build_page_reports_the_downloader_real_release_identity(tmp_path):
    """返修 (blocking 发现修复): ``build_page()`` 的 request_meta 之前
    ``dump_release_key`` 恒为 ``None``、``dump_release_tag`` 被 dump *kind*
    (哪个文件) 冒充——真正的每次发布身份 (presigned URL 里的 release_tag/
    release_key, 见 ``marketdb.providers.dump.DumpDownloader.fetch``) 被
    ``_DumpCache`` 就地丢弃。这条用真实的 fake downloader 注入一组已知
    release_tag/release_key, 断言它们原样出现在 ``ProviderPage.request_meta``
    里, 且 ``dump_kind`` (哪个文件) 与 ``dump_release_tag`` (哪次发布) 是两个
    不同的键, 不再互相冒充。"""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", DAY, close=10.0)])
    downloader = _FakeDownloader(
        {_DummyDumpKinds.DAILY_K_10D: ten_d},
        releases={
            _DummyDumpKinds.DAILY_K_10D: (
                "2026-09-01T03-15-00Z.parquet",
                "fuyao-market-dump/daily_k/a_share_daily_k_1d_none_10y",
            )
        },
    )
    deps = FuyaoDailyKDeps(
        downloader=downloader,
        dump_kinds=_DummyDumpKinds,
        baostock_source=_FakeBaostockLeg(),
        rules=_rules(),
    )
    adapter = FuyaoDailyKAdapter(lambda: deps)

    page = adapter.build_page(DAY)

    assert len(page.rows) == 1
    assert page.request_meta["dump_release_tag"] == "2026-09-01T03-15-00Z.parquet"
    assert page.request_meta["dump_release_key"] == (
        "fuyao-market-dump/daily_k/a_share_daily_k_1d_none_10y"
    )
    assert page.request_meta["dump_kind"] == str(_DummyDumpKinds.DAILY_K_10D.value)
    assert page.request_meta["reference_source"] == _rules().reference_source


def test_build_page_reports_none_release_identity_when_no_dump_covers_the_date(tmp_path):
    """隔离用例: 两个 dump kind 都不覆盖该日, 不该伪造 release 身份——
    dump_release_key/dump_release_tag/dump_kind 都必须是 None, 不是空字符串或
    上一次成功请求残留的值。"""

    ten_d = tmp_path / "10d.parquet"
    full = tmp_path / "full.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", "20260805", close=9.0)])
    _write_dump_parquet(full, [_dump_row("920819.BJ", "20260806", close=9.0)])
    downloader = _FakeDownloader({_DummyDumpKinds.DAILY_K_10D: ten_d, _DummyDumpKinds.DAILY_K: full})
    deps = FuyaoDailyKDeps(
        downloader=downloader,
        dump_kinds=_DummyDumpKinds,
        baostock_source=_FakeBaostockLeg(),
        rules=_rules(),
    )
    adapter = FuyaoDailyKAdapter(lambda: deps)

    page = adapter.build_page(DAY)

    assert page.rows == []
    assert page.request_meta["dump_release_key"] is None
    assert page.request_meta["dump_release_tag"] is None
    assert page.request_meta["dump_kind"] is None


# ---------------------------------------------------------------------------
# 7. incremental floor
# ---------------------------------------------------------------------------


def test_trade_date_before_incremental_floor_raises(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", "20260831", close=10.0)])
    adapter, downloader = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=_FakeBaostockLeg())

    with pytest.raises(FuyaoDailyKError, match="incremental_floor"):
        adapter.fetch_rows("20260831")
    # Floor check happens before any dump kind is even requested.
    assert downloader.requested_kinds == []


# ---------------------------------------------------------------------------
# 8. sh_sz unknown-row threshold (isolated at exactly the boundary)
# ---------------------------------------------------------------------------


def _pool_codes(n: int) -> list[str]:
    return [f"60000{i}.SH" for i in range(n)]


def test_unknown_rows_at_threshold_passes(tmp_path):
    rules = _rules()
    limit = rules.sh_sz_max_unknown_rows
    codes = _pool_codes(limit + 1)  # limit unknown + 1 known == limit+1 rows total
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row(c, DAY, close=9.35) for c in codes])
    # First code resolves cleanly; the rest have no baostock reference at all.
    baostock_map = {
        f"sh.{codes[0].split('.')[0]}": [{"date": "2026-09-01", "close": "9.35", "preclose": "9.16"}],
    }
    baostock = _FakeBaostockLeg(baostock_map)
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    unknown_n = sum(1 for r in rows if r["pre_close_origin"] != "provider_baostock")
    assert unknown_n == limit
    assert len(rows) == limit + 1


def test_unknown_rows_over_threshold_raises_and_rejects_whole_day(tmp_path):
    rules = _rules()
    limit = rules.sh_sz_max_unknown_rows
    codes = _pool_codes(limit + 1)  # all unknown -> limit+1 > limit
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row(c, DAY, close=9.35) for c in codes])
    baostock = _FakeBaostockLeg({})  # nobody resolves
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    with pytest.raises(FuyaoDailyKError, match="sh_sz_max_unknown_rows"):
        adapter.fetch_rows(DAY)


# ---------------------------------------------------------------------------
# 9. baostock circuit breaker: raise-once, never touch the network again
# ---------------------------------------------------------------------------


class _FakeBaostockModule:
    """Mirrors ``test_baostock_adapter.py::_FakeBaostock`` minimally: only the
    ``login`` path this test needs, with an explicit call counter."""

    def __init__(self) -> None:
        self.login_calls = 0
        self.login_error_code = BSERR_SUCCESS

    def login(self):
        self.login_calls += 1

        class _Result:
            pass

        result = _Result()
        result.error_code = self.login_error_code
        result.error_msg = "blacklisted" if self.login_error_code != BSERR_SUCCESS else ""
        return result


def test_baostock_session_failure_breaks_circuit_and_is_not_retried(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    fake_bs = _FakeBaostockModule()
    fake_bs.login_error_code = BSERR_BLACKLIST_USER
    real_baostock_source = BaostockSource(bs_module=fake_bs)
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=real_baostock_source)

    with pytest.raises(BaostockCircuitOpenError):
        adapter.fetch_rows(DAY)
    assert fake_bs.login_calls == 1

    with pytest.raises(BaostockCircuitOpenError):
        adapter.fetch_rows(DAY)
    # The second fetch_rows call must not touch baostock's login again.
    assert fake_bs.login_calls == 1


def test_per_code_baostock_query_error_does_not_break_the_circuit(tmp_path):
    """Isolation for the opposite condition: a code-specific (non-session)
    BaostockQueryError must degrade to unknown_reference_unavailable for that
    one row and must NOT open the circuit for the rest of the batch."""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(
        ten_d,
        [_dump_row("600000.SH", DAY, close=9.35), _dump_row("600001.SH", DAY, close=8.00)],
    )
    from services.data_sources.sources.baostock import BSERR_CODE_INVALIED

    baostock = _FakeBaostockLeg(
        {"sh.600001": [{"date": "2026-09-01", "close": "8.00", "preclose": "7.90"}]},
        raise_for_code={
            "sh.600000": BaostockQueryError("bad code", code=BSERR_CODE_INVALIED),
        },
    )
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    by_code = {r["ts_code"]: r for r in rows}
    assert by_code["600000.SH"]["pre_close_origin"] == "unknown_reference_unavailable"
    assert by_code["600001.SH"]["pre_close_origin"] == "provider_baostock"


def test_transient_network_baostock_failure_opens_circuit_not_treated_as_per_code(tmp_path):
    """返修 (blocking 发现 #2): baostock 0.9.3 把 socket 超时/断连吞成的错误码
    (BSERR_RECVSOCK_FAIL, 分类 TRANSIENT_NETWORK) 之前被当成"单码没有参考"逐行
    吞掉 —— 必须和 ACCOUNT_PERMISSION 一样立刻熔断: 第二个池内代码永远不该被查。
    """

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(
        ten_d,
        [_dump_row("600000.SH", DAY, close=9.35), _dump_row("600001.SH", DAY, close=8.00)],
    )
    from services.data_sources.sources.baostock import BSERR_RECVSOCK_FAIL

    baostock = _FakeBaostockLeg(
        {"sh.600001": [{"date": "2026-09-01", "close": "8.00", "preclose": "7.90"}]},
        raise_for_code={
            "sh.600000": BaostockQueryError("recv failed", code=BSERR_RECVSOCK_FAIL),
        },
    )
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    with pytest.raises(BaostockCircuitOpenError):
        adapter.fetch_rows(DAY)
    # Circuit opens on the very first (network-level) failure — the second
    # pool code must never be reached at all.
    assert all(call["code"] != "sh.600001" for call in baostock.calls)
    # And the failing code itself was only queried once (prefetch window) —
    # a session-level failure must not fall through to the single-day
    # fallback query like a genuine per-code miss would.
    assert sum(1 for call in baostock.calls if call["code"] == "sh.600000") == 1


def test_unknown_threshold_stops_querying_baostock_as_soon_as_it_is_exceeded(tmp_path):
    """返修 (blocking 发现 #2): 阈值门必须逐行累加、超限立即停 —— 第 limit+1 个
    unknown 一出现就该 raise, 池内剩下的代码永远不该被查。"""

    rules = _rules()
    limit = rules.sh_sz_max_unknown_rows
    codes = _pool_codes(limit + 4)  # strictly more codes than the threshold allows past
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row(c, DAY, close=9.35) for c in codes])
    baostock = _FakeBaostockLeg({})  # nobody resolves -> every row is unknown
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    with pytest.raises(FuyaoDailyKError, match="sh_sz_max_unknown_rows"):
        adapter.fetch_rows(DAY)

    queried_codes = {call["code"] for call in baostock.calls}
    expected_codes = {f"sh.{c.split('.')[0]}" for c in codes[: limit + 1]}
    assert queried_codes == expected_codes


def test_same_miss_across_two_fetch_rows_calls_only_queries_baostock_once(tmp_path):
    """返修 (blocking 发现 #2): 负缓存 —— 已确认没有的 (码,日) 在同一 adapter 实例
    上第二次 fetch_rows(同一 trade_date) 时不许再触网 (模拟 sync_runner 对同一天
    的重试/重跑, adapter 状态跨调用持续存在)。"""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({})  # clean empty response, no exception raised
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows1 = adapter.fetch_rows(DAY)
    calls_after_first = len(baostock.calls)
    assert calls_after_first > 0
    assert rows1[0]["pre_close_origin"] == "unknown_reference_unavailable"

    rows2 = adapter.fetch_rows(DAY)
    assert len(baostock.calls) == calls_after_first  # negative cache hit, no new network calls
    assert rows2[0]["pre_close_origin"] == "unknown_reference_unavailable"


# ---------------------------------------------------------------------------
# 9b. baostock daily-k reservoir drain (ST 契约 v2 刀2, spec 附录 B4/B6)
# ---------------------------------------------------------------------------


def test_drain_returns_one_reservoir_row_per_code_per_date_then_empties(tmp_path):
    """假 baostock 源返回 2 码 x 3 日 -> drain() 6 行, 各带 fetched_at/请求窗口/
    fields; 再 drain 一次 -> []。"""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(
        ten_d,
        [_dump_row("600000.SH", DAY, close=9.35), _dump_row("000001.SZ", DAY, close=10.0)],
    )
    three_dates_600000 = [
        {"date": "2026-08-30", "code": "sh.600000", "close": "9.30", "preclose": "9.28",
         "volume": "100", "tradestatus": "1", "isST": "0"},
        {"date": "2026-09-01", "code": "sh.600000", "close": "9.35", "preclose": "9.30",
         "volume": "100", "tradestatus": "1", "isST": "0"},
        {"date": "2026-09-02", "code": "sh.600000", "close": "9.40", "preclose": "9.35",
         "volume": "100", "tradestatus": "1", "isST": "0"},
    ]
    three_dates_000001 = [
        {"date": "2026-08-30", "code": "sz.000001", "close": "10.0", "preclose": "9.95",
         "volume": "50", "tradestatus": "1", "isST": "1"},
        {"date": "2026-09-01", "code": "sz.000001", "close": "10.1", "preclose": "10.0",
         "volume": "50", "tradestatus": "1", "isST": "1"},
        {"date": "2026-09-02", "code": "sz.000001", "close": "10.2", "preclose": "10.1",
         "volume": "50", "tradestatus": "1", "isST": "1"},
    ]
    baostock = _FakeBaostockLeg({
        "sh.600000": three_dates_600000,
        "sz.000001": three_dates_000001,
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    adapter.fetch_rows(DAY)
    drained = adapter.drain_baostock_daily_k_rows()

    assert len(drained) == 6
    by_code_date = {(r.ts_code, r.trade_date): r for r in drained}
    assert set(by_code_date) == {
        ("600000.SH", "20260830"), ("600000.SH", "20260901"), ("600000.SH", "20260902"),
        ("000001.SZ", "20260830"), ("000001.SZ", "20260901"), ("000001.SZ", "20260902"),
    }
    for row in drained:
        assert row.fetched_at is not None
        assert row.request_start == DAY
        assert "isST" in row.fields_csv.split(",")
        assert row.fetch_context == f"daily_adapter:{DAY}"

    # B6: fields 参数 (记录在假 baostock 上) 含 isST
    assert all("isST" in call["fields"].split(",") for call in baostock.calls)

    assert adapter.drain_baostock_daily_k_rows() == []


def test_required_st_codes_supplement_queries_code_missing_from_dump(tmp_path):
    """B1-c (返修规格逐字对应): 假 dump 只有 600000.SH, required(D) 还要
    000009.SZ (当天停牌中的 ST 股, 结构上不进 dump) -> 假 baostock 被查到这只
    代码的 D, drain 出的行含它; 但它**不**出现在这一天的 OHLCV 输出行里 (不伪
    造停牌股当天的成交)。变异: 去掉停牌补查 (即让
    ``_supplement_required_st_codes`` 直接返回而不查) -> 本用例红
    (drained 不再含 000009.SZ)。"""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({
        "sh.600000": [
            {"date": "2026-09-01", "code": "sh.600000", "close": "9.35", "preclose": "9.30",
             "volume": "100", "tradestatus": "1", "isST": "0"},
        ],
        "sz.000009": [
            {"date": "2026-09-01", "code": "sz.000009", "close": "5.00", "preclose": "5.00",
             "volume": "0", "tradestatus": "0", "isST": "1"},
        ],
    })
    required_calls: list[str] = []

    def _required(trade_date: str):
        required_calls.append(trade_date)
        return frozenset({"600000.SH", "000009.SZ"}), "ok"

    deps = FuyaoDailyKDeps(
        downloader=_FakeDownloader({_DummyDumpKinds.DAILY_K_10D: ten_d}),
        dump_kinds=_DummyDumpKinds,
        baostock_source=baostock,
        rules=_rules(),
        required_st_codes_provider=_required,
    )
    adapter = FuyaoDailyKAdapter(lambda: deps)

    page = adapter.build_page(DAY)
    assert {r["ts_code"] for r in page.rows} == {"600000.SH"}  # 不伪造停牌股当天的成交行
    assert page.request_meta["st_backfill_supplement"] == {
        "status": "ok", "codes_required": 2, "codes_missing_from_dump": 1, "codes_queried": 1,
    }
    assert required_calls == [DAY]

    drained = adapter.drain_baostock_daily_k_rows()
    drained_codes = {r.ts_code for r in drained}
    assert "000009.SZ" in drained_codes  # 补查到的行照常进水库证据
    supplemented = next(r for r in drained if r.ts_code == "000009.SZ")
    assert supplemented.payload.get("isST") == "1"


def test_required_st_codes_supplement_absent_provider_is_not_configured(tmp_path):
    """没有注入 ``required_st_codes_provider`` (旧测试的默认构造方式) -> 视同
    "无额外代码", meta 记 status=not_configured, 不查任何补充代码, 不崩。"""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({
        "sh.600000": [
            {"date": "2026-09-01", "code": "sh.600000", "close": "9.35", "preclose": "9.30",
             "volume": "100", "tradestatus": "1", "isST": "0"},
        ],
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    page = adapter.build_page(DAY)
    assert page.request_meta["st_backfill_supplement"] == {
        "status": "not_configured", "codes_required": 0, "codes_missing_from_dump": 0, "codes_queried": 0,
    }


def test_default_required_st_codes_for_date_degrades_on_connection_failure(monkeypatch):
    """B1 修法: 生产默认实现拿不到只读连接 (无库/测试环境) -> (空集合,
    'no_connection:<异常类名>'), 不崩、不重试。"""

    from services.data_sources.sources import fuyao_daily_k as mod

    def _boom(_alias):
        raise RuntimeError("no such database in this sandbox")

    monkeypatch.setattr("services.data_access.resolver.connect_ro", _boom)

    codes, status = mod._default_required_st_codes_for_date(DAY)
    assert codes == frozenset()
    assert status == "no_connection:RuntimeError"


def test_negative_cache_miss_does_not_produce_reservoir_rows(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({})  # clean empty response — negative cache
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)
    assert rows[0]["pre_close_origin"] == "unknown_reference_unavailable"
    assert adapter.drain_baostock_daily_k_rows() == []


def test_drain_before_ensure_deps_returns_empty_list(tmp_path):
    """本次运行还没查过 baostock (deps 惰性构造, _baostock_cache 仍是 None) ->
    drain 返回 [], 不强行触发构造。"""

    deps = _deps(dump_paths={}, baostock=_FakeBaostockLeg())
    adapter = FuyaoDailyKAdapter(lambda: deps)
    assert adapter.drain_baostock_daily_k_rows() == []


def test_fuyao_source_drain_returns_empty_when_daily_k_dump_never_touched():
    from services.data_sources.sources.fuyao import FuyaoSource

    source = FuyaoSource()
    assert source.drain_baostock_daily_k_rows() == []


def test_fuyao_source_drain_delegates_to_adapter(monkeypatch, tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("600000.SH", DAY, close=9.35)])
    baostock = _FakeBaostockLeg({
        "sh.600000": [
            {"date": "2026-09-01", "code": "sh.600000", "close": "9.35", "preclose": "9.30",
             "volume": "100", "tradestatus": "1", "isST": "0"},
        ],
    })
    deps = _deps(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)
    monkeypatch.setattr("services.data_sources.sources.fuyao_daily_k.default_deps", lambda: deps)

    from services.data_sources.sources.fuyao import FuyaoSource

    source = FuyaoSource()
    source.fetch_raw("daily_k_dump", trade_date=DAY)
    drained = source.drain_baostock_daily_k_rows()
    assert len(drained) == 1
    assert drained[0].ts_code == "600000.SH"
    assert source.drain_baostock_daily_k_rows() == []


# ---------------------------------------------------------------------------
# 10. output row key set
# ---------------------------------------------------------------------------


def test_output_row_keys_match_provider_fields_plus_origin(tmp_path):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(
        ten_d,
        [_dump_row("600000.SH", DAY, close=9.35), _dump_row("920819.BJ", DAY, close=10.0)],
    )
    baostock = _FakeBaostockLeg({
        "sh.600000": [{"date": "2026-09-01", "close": "9.35", "preclose": "9.16"}],
    })
    adapter, _dl = _adapter(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=baostock)

    rows = adapter.fetch_rows(DAY)

    expected_keys = set(PROVIDER_FIELDS) | {"pre_close_origin"}
    for row in rows:
        assert set(row) == expected_keys


# ---------------------------------------------------------------------------
# 11. registry wiring: contract factory + config_hash stability + live-adapter gate
# ---------------------------------------------------------------------------


def test_registry_wiring_builds_contract_without_raising_and_keeps_config_hash():
    registry = load_registry()
    spec = dict(registry["domains"]["daily"])
    spec["domain"] = "daily"
    spec.setdefault("target_db", "tushare_raw")

    contract = nominal_ohlcv_contract_for_spec(spec)

    assert contract.source == "fuyao"
    assert contract.api == "daily_k_dump"
    assert contract.config_hash[:8] == "2e6150f5"


def test_require_live_adapter_accepts_fuyao_and_rejects_tdxhub_for_daily():
    from services.data_sources.formal_boundaries import FormalBoundaryError, require_live_adapter

    assert require_live_adapter("fuyao", domain="daily") == "fuyao"
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("tdxhub", domain="daily")


def test_fetch_raw_dispatches_daily_k_dump_to_the_new_module(monkeypatch, tmp_path):
    """FuyaoSource.fetch_raw("daily_k_dump", ...) reaches this module's
    dispatch target (not the pool/auction/ticker branches)."""

    calls: list[tuple[Any, dict]] = []

    def _fake_fetch(source, **params):
        calls.append((source, params))
        return [{"stub": True}]

    monkeypatch.setattr(
        "services.data_sources.sources.fuyao_daily_k.fetch_daily_k_dump_rows", _fake_fetch
    )
    src = FuyaoSource()
    result = src.fetch_raw("daily_k_dump", trade_date=DAY)
    assert result == [{"stub": True}]
    assert calls and calls[0][0] is src
    assert calls[0][1] == {"trade_date": DAY}


# ---------------------------------------------------------------------------
# 12 (L11/L12): vendor_scope + db_invariants for the new (source, api)
# ---------------------------------------------------------------------------


def test_vendor_scope_covers_fuyao_daily_k_dump():
    from services.data_sources.vendor_scope import load_vendor_scope

    scope = load_vendor_scope()
    assert "fuyao.daily_k_dump" in scope.dispositions
    disposition = scope.dispositions["fuyao.daily_k_dump"]["b_share"]
    assert disposition.mode == "population_disjoint"


# The other half of assertion 12 (check_db_invariants PASS/FAIL) is exercised
# at its natural home in backend/tests/scripts/test_check_db_invariants.py::
# test_nominal_ohlcv_accepted_sources_pass_fuyao_accepted /
# test_nominal_ohlcv_accepted_sources_fail_on_akshare_even_alongside_fuyao
# (loads the real db_invariants.yaml spec the same way every other test in
# that file does) — not duplicated here to avoid two competing importlib
# loaders for the same script module in the same test run.


# ---------------------------------------------------------------------------
# fetch_daily_k_dump_rows: state (dump-kind cache) persists on the FuyaoSource
# instance across separate top-level calls, exactly like sync_runner's
# singleton _adapter("fuyao") reuse within one chunkyctl sync run.
# ---------------------------------------------------------------------------


def test_fetch_daily_k_dump_rows_caches_adapter_state_on_the_source_instance(tmp_path, monkeypatch):
    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(
        ten_d,
        [
            _dump_row("920819.BJ", DAY, close=10.0),
            _dump_row("920819.BJ", "20260902", close=10.5),
        ],
    )
    deps = _deps(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=_FakeBaostockLeg())
    monkeypatch.setattr("services.data_sources.sources.fuyao_daily_k.default_deps", lambda: deps)

    class _StubSource:
        pass

    source = _StubSource()
    rows1 = fetch_daily_k_dump_rows(source, trade_date=DAY)
    rows2 = fetch_daily_k_dump_rows(source, trade_date="20260902")

    assert len(rows1) == 1
    assert len(rows2) == 1
    # Same source instance -> same adapter -> the 10d parquet is only ever
    # downloaded once across both trade_date calls (process-lifetime cache).
    assert deps.downloader.requested_kinds == [_DummyDumpKinds.DAILY_K_10D]
    assert getattr(source, "_daily_k_adapter", None) is not None


def test_fetch_daily_k_dump_rows_requires_trade_date():
    class _StubSource:
        pass

    with pytest.raises(FuyaoDailyKError, match="trade_date"):
        fetch_daily_k_dump_rows(_StubSource())


def test_fetch_daily_k_dump_rows_returns_provider_page_with_real_release_and_sha256(
    tmp_path, monkeypatch
):
    """返修 (blocking 发现 #1): the ``FuyaoSource.fetch_raw`` dispatch target
    must return a :class:`ProviderPage` carrying the dump's real release
    identity and a real (non-``None``) content sha256 — not the always-``None``
    placeholder the previous cut left on the production dispatch path."""

    ten_d = tmp_path / "10d.parquet"
    _write_dump_parquet(ten_d, [_dump_row("920819.BJ", DAY, close=10.0)])
    deps = _deps(dump_paths={_DummyDumpKinds.DAILY_K_10D: ten_d}, baostock=_FakeBaostockLeg())
    monkeypatch.setattr("services.data_sources.sources.fuyao_daily_k.default_deps", lambda: deps)

    class _StubSource:
        pass

    page = fetch_daily_k_dump_rows(_StubSource(), trade_date=DAY)

    assert isinstance(page, ProviderPage)
    assert len(page) == 1  # __len__/__bool__ keep the sync_runner retry contract intact
    assert bool(page)
    import hashlib

    assert page.request_meta["dump_sha256"] == hashlib.sha256(ten_d.read_bytes()).hexdigest()
    assert page.request_meta["dump_kind"] == _DummyDumpKinds.DAILY_K_10D.value
    assert page.request_meta["reference_source"] == "baostock"


def test_fetch_daily_k_dump_rows_page_metadata_survives_the_full_acquire_and_capture_chain():
    """返修 (blocking 发现 #1): the exact production chain
    (``resolve_security_day_acquire`` -> the ``_acquired_rows`` closure shape
    used by ``sync_runner`` -> ``capture_security_day_provider_rows``) must
    deliver the fuyao adapter's page-level request_meta into
    ``ingest_batch.request_json`` — not just this module's direct ``build_page``
    caller. Uses the stock_st domain fixture (see the ProviderPage section
    below) purely as the shared capture-layer plumbing under test; the point
    here is the acquire-layer round trip, not daily's own row rules."""

    from services.data_sources.security_day_acquire import (
        ACQUIRE_MODE_PROVIDER_TUSHARE,
        resolve_security_day_acquire,
    )

    def _fetch_rows(_request):
        return ProviderPage(
            rows=[_stock_st_row()],
            request_meta={
                "dump_release_tag": "daily-k-10d",
                "dump_sha256": "deadbeef",
                "reference_source": "baostock",
            },
        )

    acquired = resolve_security_day_acquire(
        ACQUIRE_MODE_PROVIDER_TUSHARE, "stock_st", trade_date="20260901", fetch_rows=_fetch_rows
    )
    assert acquired.request_meta["dump_sha256"] == "deadbeef"

    # Exact shape of sync_runner's own `_acquired_rows` closure (both call
    # sites) — a plain list unless request_meta is non-empty, in which case a
    # ProviderPage carries it the rest of the way.
    def _acquired_rows(_params):
        if acquired.request_meta:
            return ProviderPage(rows=list(acquired.rows), request_meta=dict(acquired.request_meta))
        return list(acquired.rows)

    from datetime import datetime, timezone

    batch = capture_security_day_provider_rows(
        _stock_st_domain(),
        trade_date="20260901",
        fetch_rows=_acquired_rows,
        observed_at=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
    )
    assert batch.request["dump_sha256"] == "deadbeef"
    assert batch.request["dump_release_tag"] == "daily-k-10d"


# ---------------------------------------------------------------------------
# survivor gate (§1): legitimate suspension / real gap / BJ unverified
# ---------------------------------------------------------------------------


WINDOW = ("20260901", "20260902")


def test_survivor_gate_all_suspended_is_legitimate():
    baostock = _FakeBaostockLeg({
        "sh.600001": [
            {"date": "2026-09-01", "tradestatus": "0", "volume": "0"},
            {"date": "2026-09-02", "tradestatus": "0", "volume": "0"},
        ],
    })
    result = compute_survivor_gate(
        canonical_codes=["600001.SH"],
        dump_codes=[],
        baostock_source=baostock,
        rules=_rules(),
        window_dates=WINDOW,
    )
    assert result == SurvivorGateResult(
        legitimate_suspension=("600001.SH",), real_gap=(), unverified=()
    )


def test_survivor_gate_real_trading_gap_raises():
    baostock = _FakeBaostockLeg({
        "sh.600001": [
            {"date": "2026-09-01", "tradestatus": "1", "volume": "12300"},
            {"date": "2026-09-02", "tradestatus": "0", "volume": "0"},
        ],
    })
    with pytest.raises(FuyaoDailyKError, match="真漏"):
        compute_survivor_gate(
            canonical_codes=["600001.SH"],
            dump_codes=[],
            baostock_source=baostock,
            rules=_rules(),
            window_dates=WINDOW,
        )


def test_survivor_gate_bj_member_is_unverified_not_legitimate_or_gap():
    result = compute_survivor_gate(
        canonical_codes=["920819.BJ"],
        dump_codes=[],
        baostock_source=_FakeBaostockLeg(),
        rules=_rules(),
        window_dates=WINDOW,
    )
    assert result.unverified == ("920819.BJ",)
    assert result.legitimate_suspension == ()
    assert result.real_gap == ()


def test_survivor_gate_excludes_codes_present_in_dump():
    """Isolation: a code that IS in dump_codes is not a survivor at all —
    only the set difference matters, not mere presence in canonical_codes."""

    result = compute_survivor_gate(
        canonical_codes=["600001.SH"],
        dump_codes=["600001.SH"],
        baostock_source=_FakeBaostockLeg(),
        rules=_rules(),
        window_dates=WINDOW,
    )
    assert result == SurvivorGateResult(legitimate_suspension=(), real_gap=(), unverified=())


# ---------------------------------------------------------------------------
# ProviderPage: request_meta lands in ingest_batch.request_json, plain
# sequences still work unchanged (backward compatibility, security_day_capture.py)
# ---------------------------------------------------------------------------


def _stock_st_domain():
    """ProviderPage is a capability of the shared land→accept mechanics
    (security_day_capture.py), not specific to the daily domain — exercising
    it against stock_st's DOMAIN (whose provider_fields are just ts_code/
    trade_date/name/type/type_name, no pre_close validator) keeps this
    section about the capture-layer plumbing, not daily's own row rules
    (already covered by the fetch_rows()-level tests above)."""

    from services.data_sources.stock_st_schema import DOMAIN as STOCK_ST_DOMAIN

    return STOCK_ST_DOMAIN


def _stock_st_row(ts_code: str = "600000.SH", trade_date: str = "20260901") -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "name": "平安银行",
        "type": "L",
        "type_name": "其他风险警示",
        # 2026-09-18 v2: st_origin 是 stock_st 的域级增补列, land 阶段就要求逐行给出
        # (project_security_day_provider_row), 与本节测的 ProviderPage/request_meta
        # 管道机制本身无关, 只是借用 stock_st DOMAIN 做夹具时必须满足它的契约。
        "st_origin": "provider_tushare_stock_st",
    }


def test_provider_page_request_meta_folds_into_landing_batch_request():
    domain = _stock_st_domain()
    rows = [_stock_st_row()]
    from datetime import datetime, timezone

    batch = build_security_day_landing_batch(
        domain,
        trade_date="20260901",
        rows=rows,
        observed_at=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
        batch_id="test:provider_page:1",
        request_meta={"dump_release_tag": "daily-k-10d", "reference_source": "baostock"},
    )
    assert batch.request["api"] == domain.api
    assert batch.request["trade_date"] == "20260901"
    assert batch.request["dump_release_tag"] == "daily-k-10d"
    assert batch.request["reference_source"] == "baostock"


def test_provider_page_request_meta_cannot_collide_with_reserved_keys():
    domain = _stock_st_domain()
    rows = [_stock_st_row()]
    from datetime import datetime, timezone

    from services.data_sources.security_day_partition import SecurityDayError

    with pytest.raises(SecurityDayError, match="request_meta_collides"):
        build_security_day_landing_batch(
            domain,
            trade_date="20260901",
            rows=rows,
            observed_at=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
            batch_id="test:provider_page:2",
            request_meta={"api": "should-not-override"},
        )


def test_capture_accepts_provider_page_and_plain_sequence_identically():
    domain = _stock_st_domain()
    rows = [_stock_st_row()]
    from datetime import datetime, timezone

    def _fetch_rows_page(_request):
        return ProviderPage(rows=rows, request_meta={"reference_source": "baostock"})

    def _fetch_rows_plain(_request):
        return rows

    observed_at = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
    batch_page = capture_security_day_provider_rows(
        domain, trade_date="20260901", fetch_rows=_fetch_rows_page, observed_at=observed_at
    )
    batch_plain = capture_security_day_provider_rows(
        domain, trade_date="20260901", fetch_rows=_fetch_rows_plain, observed_at=observed_at
    )
    assert batch_page.request["reference_source"] == "baostock"
    assert "reference_source" not in batch_plain.request
    assert batch_page.rows == batch_plain.rows


# ---------------------------------------------------------------------------
# nominal_ohlcv_acquire.yaml loader: isolated validation cases for the three
# rules explicitly required by 修订9 (column_map target set / positive
# divisors / zoneinfo-parseable timezone), each mutated alone.
# ---------------------------------------------------------------------------


def _acquire_yaml_dict():
    import yaml

    from services.data_sources.nominal_ohlcv_acquire_rules import _CONFIG_PATH

    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


def _write_acquire_yaml(tmp_path, data) -> Path:
    import yaml

    path = tmp_path / "nominal_ohlcv_acquire.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_column_map_target_set_must_equal_dump_sourced_provider_fields(tmp_path):
    data = _acquire_yaml_dict()
    data["dump"]["column_map"]["thscode"] = "wrong_target_column"
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="column_map"):
        load_nominal_ohlcv_acquire_rules(path)


def test_vol_divisor_must_be_positive(tmp_path):
    data = _acquire_yaml_dict()
    data["dump"]["vol_divisor"] = -100
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="vol_divisor"):
        load_nominal_ohlcv_acquire_rules(path)


def test_amount_divisor_must_be_positive(tmp_path):
    data = _acquire_yaml_dict()
    data["dump"]["amount_divisor"] = 0
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="amount_divisor"):
        load_nominal_ohlcv_acquire_rules(path)


def test_dump_timezone_must_be_parseable_by_zoneinfo(tmp_path):
    data = _acquire_yaml_dict()
    data["dump"]["timezone"] = "Not/AZone"
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="zoneinfo"):
        load_nominal_ohlcv_acquire_rules(path)


def test_real_config_file_loads_without_monkeypatching():
    """Rule 11's mandated 'at least one test reads the real config file, not
    monkeypatched' — every other loader test in this file mutates a tmp_path
    copy; this one reads the actual repo file via the default path."""

    rules = load_nominal_ohlcv_acquire_rules()
    assert rules.dump_incremental_floor == "20260901"
    assert set(rules.dump_column_map.values()) == {
        "ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount",
    }
    assert rules.dump_timezone == "Asia/Shanghai"


def test_prefetch_window_days_must_be_positive_int(tmp_path):
    data = _acquire_yaml_dict()
    data["reference"]["prefetch_window_days"] = 0
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="prefetch_window_days"):
        load_nominal_ohlcv_acquire_rules(path)


def test_exchange_suffix_map_accepts_a_newly_registered_exchange(tmp_path):
    """返修 (blocking 发现修复) 回归钉子: 之前这条用例反过来断言加一个新交易所后缀
    (BJ) 会被拒——但"哪些交易所有 baostock 参考"是供应商事实, 会随 baostock 覆盖范围
    变(业主 09-16: 名单类参数不许把值当结构钉死)。loader 现在只校验形状, 加 BJ 必须
    正常通过而不是报错; BJ 分流本身仍由 ``_is_bj_ts_code``/``_exchange_prefix_code``
    在适配器里处理, 不靠这里的键集合当白名单。"""
    data = _acquire_yaml_dict()
    data["reference"]["exchange_suffix_to_baostock_prefix"]["BJ"] = "bj."
    path = _write_acquire_yaml(tmp_path, data)

    rules = load_nominal_ohlcv_acquire_rules(path)

    assert rules.exchange_suffix_to_baostock_prefix["BJ"] == "bj."
    assert rules.exchange_suffix_to_baostock_prefix["SH"] == "sh."
    assert rules.exchange_suffix_to_baostock_prefix["SZ"] == "sz."


def test_exchange_suffix_map_cannot_be_empty(tmp_path):
    data = _acquire_yaml_dict()
    data["reference"]["exchange_suffix_to_baostock_prefix"] = {}
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="exchange_suffix_to_baostock_prefix"):
        load_nominal_ohlcv_acquire_rules(path)


def test_exchange_suffix_map_key_must_be_an_uppercase_suffix(tmp_path):
    """隔离用例: 只违反键格式这一条 (其余键/值都合法) —— 小写后缀必须被拒。"""
    data = _acquire_yaml_dict()
    data["reference"]["exchange_suffix_to_baostock_prefix"]["sh"] = "sh."
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="exchange_suffix_to_baostock_prefix"):
        load_nominal_ohlcv_acquire_rules(path)


def test_exchange_suffix_map_value_must_be_non_empty_string(tmp_path):
    """隔离用例: 只违反值非空这一条 (键格式合法) —— 空字符串前缀必须被拒。"""
    data = _acquire_yaml_dict()
    data["reference"]["exchange_suffix_to_baostock_prefix"]["SH"] = ""
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="exchange_suffix_to_baostock_prefix"):
        load_nominal_ohlcv_acquire_rules(path)


def test_baostock_fields_must_be_non_empty_and_unique(tmp_path):
    data = _acquire_yaml_dict()
    data["reference"]["baostock_fields"] = ["date", "date"]
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="baostock_fields"):
        load_nominal_ohlcv_acquire_rules(path)


def test_change_pct_round_digits_must_be_positive_int(tmp_path):
    data = _acquire_yaml_dict()
    data["reference"]["change_pct_round_digits"] = 0
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="change_pct_round_digits"):
        load_nominal_ohlcv_acquire_rules(path)


def test_adjusted_filter_value_must_be_non_empty_string(tmp_path):
    data = _acquire_yaml_dict()
    data["dump"]["adjusted_filter_value"] = ""
    path = _write_acquire_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="adjusted_filter_value"):
        load_nominal_ohlcv_acquire_rules(path)


# ---------------------------------------------------------------------------
# data_audit.py: null_first_pre_close_n is reported, never gates status
# ---------------------------------------------------------------------------


def _audit_conn_with_kline(rows: list[tuple]):
    import duckdb

    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS tushare_raw")
    conn.execute(
        "CREATE TABLE tushare_raw.canonical_nominal_ohlcv_daily "
        "(ts_code VARCHAR, trade_date DATE, close DOUBLE, pre_close DOUBLE, "
        "vol DOUBLE, amount DOUBLE)"
    )
    conn.executemany(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES (?,?,?,?,?,?)",
        rows,
    )
    return conn


def test_null_first_pre_close_n_reported_on_pass_without_changing_status():
    from datetime import date

    from services.data_audit import _check_kline_code_succession
    from services.security_identity import CodeChangeSet

    conn = _audit_conn_with_kline(
        [
            ("111111.SZ", date(2024, 1, 2), 10.0, 9.9, 100.0, 1000.0),
            # New code's first-day pre_close is NULL -> this candidate is
            # invisible to the main tolerance-based query (NULL comparison is
            # always false in SQL WHERE), so it never appears as
            # "unregistered" — but must still be counted here.
            ("222222.SZ", date(2024, 1, 3), 10.0, None, 100.0, 1000.0),
        ]
    )
    result = _check_kline_code_succession(
        conn, ccs=CodeChangeSet(events=(), by_new={}, by_old={}, sha256="test-sha256")
    )
    assert result.status == "PASS"
    assert "null_first_pre_close_n=1" in result.detail


def test_null_first_pre_close_n_is_zero_when_no_candidate_has_a_null_pre_close():
    from datetime import date

    from services.data_audit import _check_kline_code_succession
    from services.security_identity import CodeChangeSet

    conn = _audit_conn_with_kline(
        [
            ("111111.SZ", date(2024, 1, 2), 10.0, 9.9, 100.0, 1000.0),
            ("222222.SZ", date(2024, 1, 3), 10.0, 9.9, 100.0, 1000.0),
        ]
    )
    result = _check_kline_code_succession(
        conn, ccs=CodeChangeSet(events=(), by_new={}, by_old={}, sha256="test-sha256")
    )
    assert "null_first_pre_close_n=0" in result.detail
