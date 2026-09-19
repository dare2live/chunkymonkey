"""cleanup_out_of_scope_rows 单测 (cut_bshare_purge, 2026-09-18).

Structure mirrors test_check_out_of_scope_rows.py: importlib-load the script
(registering it in sys.modules so its frozen dataclasses resolve on Python
3.13), inject real tmp_path DuckDB files through conn_for, never open any
data/*.duckdb. Fixtures build the org_holding / smartmoney / tushare_raw
tables with the real production writers (publish_accepted_org_holding_partition,
qfii_client.ensure_tables) so the pointer/content_hash machinery under test is
exercised for real, not stubbed.

Fixture shape (P1/P2/P3 in canonical_org_holding_detail_period):
  P1 available_date=20260731 report_date=20260630: 3 A-share + 2 B-share rows
     (900901, 200011) -> expect canonical pointer updated to row_count=3.
  P2 available_date=20260815 report_date=20260630: 1 B-share row (200011)
     -> expect pointer deleted (partition emptied).
  P3 available_date=20250430 report_date=20241231: 2 A-share rows plus two
     pool-excluded-but-not-out-of-scope codes (920001 BJ, 110001 convertible)
     -> expect entirely untouched; this partition is placed in the injected
     frozen snapshot for the non-intersecting baseline.
raw_org_holding_aif10 mirrors the same grains (report_date/available_date ISO).
smartmoney.raw_qfii_holding_quarterly: 200011 (hit) + 000001 + 920001.
tushare_raw.raw_tushare_top_list: 900901.SH/200011.SZ (hit) + 600900.SH/
9000011/920001.BJ (non-hit vectors, incl. the 7-digit and BJ edge cases).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "cleanup_out_of_scope_rows", REPO / "backend" / "scripts" / "cleanup_out_of_scope_rows.py"
)
cor = importlib.util.module_from_spec(_spec)
sys.modules["cleanup_out_of_scope_rows"] = cor  # dataclass sys.modules lookup, see check_out_of_scope_rows tests
_spec.loader.exec_module(cor)

from services import org_holding_aif10  # noqa: E402
from services import qfii_client  # noqa: E402
from services.data_deletion import ensure_data_deletion_tables  # noqa: E402
from services.data_sources.disclosure_dataset_snapshot import (  # noqa: E402
    default_snapshot_path,
)
from services.data_sources.org_holding_acceptance import (  # noqa: E402
    DOMAIN as ORG_HOLDING_DOMAIN,
    OrgHoldingLandingBatch,
    publish_accepted_org_holding_partition,
)
from services.data_sources.org_holding_contract import load_org_holding_contract  # noqa: E402
from services.data_sources.org_holding_schema import (  # noqa: E402
    CONTRACT_VERSION,
    DATASET_ID as ORG_DATASET_ID,
    SOURCE,
)
from services.data_sources.security_day_partition import sha256_text, stable_json  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.org_holding_pointer_integrity import count_org_pointer_mismatches  # noqa: E402
from services.schema_versions import ensure_schema_version_table  # noqa: E402
from services.writer_lock import WRITER_LOCK_PATH_ENV  # noqa: E402
from scripts.check_out_of_scope_rows import (  # noqa: E402
    CodePattern,
    OutOfScopeClass,
    ScanConfig,
    load_scan_config as real_load_scan_config,
)

CONFIG_PATH = REPO / "backend" / "config" / "out_of_scope_cleanup.yaml"
SCAN_CONFIG_PATH = REPO / "backend" / "config" / "out_of_scope_scan.yaml"


# ── fixture builders ────────────────────────────────────────────────────────

def _observed(y: int, m: int, d: int):
    return datetime(y, m, d, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)


def _prep_org_holding_conn(conn) -> None:
    org_holding_aif10.ensure_tables(conn)
    ensure_data_deletion_tables(conn)
    ensure_schema_version_table(conn)


def _oh_row(stock: str, holder: str, **kw: Any) -> dict[str, Any]:
    base = dict(
        stock_code=stock, holder_code=holder, fund_derivecode="",
        holder_name=f"holder-{holder}", org_type_name="fund",
        total_shares=1000.0, free_shares_ratio=1.2,
    )
    base.update(kw)
    return base


def _oh_publish(conn, contract, partition: str, report_date: str, rows: list[dict]):
    y, m, d = int(partition[:4]), int(partition[4:6]), int(partition[6:8])
    batch = OrgHoldingLandingBatch(
        batch_id=f"org_holding:{partition}",
        partition_value=partition,
        observed_at=_observed(y, m, d),
        available_at=_observed(y, m, d),
        rows=[dict(report_date=report_date, available_date=partition, **r) for r in rows],
        request={"api": "RPT_MAIN_ORGHOLDDETAIL", "available_date": partition},
        source=SOURCE, contract_version=CONTRACT_VERSION,
    )
    outcome = publish_accepted_org_holding_partition(conn, batch, contract)
    assert outcome.status == "ACCEPTED", outcome
    return outcome


_RAW_MIRROR_ROWS = [
    ("20260630", "20260731", "000001", "h1"), ("20260630", "20260731", "600519", "h2"),
    ("20260630", "20260731", "300750", "h3"), ("20260630", "20260731", "900901", "h4"),
    ("20260630", "20260731", "200011", "h5"),
    ("20260630", "20260815", "200011", "h6"),
    ("20241231", "20250430", "000002", "h7"), ("20241231", "20250430", "600000", "h8"),
    ("20241231", "20250430", "920001", "h9"), ("20241231", "20250430", "110001", "h10"),
]


def build_org_holding_db(path: Path, *, extra_raw_rows: list[tuple[str, str, str, str]] = ()) -> None:
    conn = duck_connect(str(path), read_only=False)
    try:
        _prep_org_holding_conn(conn)
        contract = load_org_holding_contract()

        # P1: 3 A-share + 2 B-share -> updated
        _oh_publish(conn, contract, "20260731", "20260630", [
            _oh_row("000001", "h1"), _oh_row("600519", "h2"), _oh_row("300750", "h3"),
            _oh_row("900901", "h4"), _oh_row("200011", "h5"),
        ])
        # P2: only 1 B-share -> deleted_empty
        _oh_publish(conn, contract, "20260815", "20260630", [_oh_row("200011", "h6")])
        # P3: A-share + pool-excluded-not-out-of-scope -> untouched
        _oh_publish(conn, contract, "20250430", "20241231", [
            _oh_row("000002", "h7"), _oh_row("600000", "h8"),
            _oh_row("920001", "h9"), _oh_row("110001", "h10"),
        ])

        for report, avail, code, holder in list(_RAW_MIRROR_ROWS) + list(extra_raw_rows):
            conn.execute(
                "INSERT INTO raw_org_holding_aif10 "
                "(report_date, available_date, stock_code, holder_code, fund_derivecode, "
                " holder_name, source) VALUES (?, ?, ?, ?, '', ?, 'miaoxiang')",
                [f"{report[:4]}-{report[4:6]}-{report[6:8]}",
                 f"{avail[:4]}-{avail[4:6]}-{avail[6:8]}", code, holder, f"holder-{holder}"],
            )
        conn.commit()
    finally:
        conn.close()


def build_smartmoney_db(path: Path, *, extra_rows: list[tuple[str, str, str]] = ()) -> None:
    conn = duck_connect(str(path), read_only=False)
    try:
        qfii_client.ensure_tables(conn)
        ensure_data_deletion_tables(conn)
        ensure_schema_version_table(conn)
        rows = [
            ("20260630", "200011", "holder-b"),
            ("20260630", "000001", "holder-a"),
            ("20260630", "920001", "holder-c"),
        ] + list(extra_rows)
        for report_date, code, holder in rows:
            conn.execute(
                "INSERT INTO raw_qfii_holding_quarterly (report_date, stock_code, holder_name, source) "
                "VALUES (?, ?, ?, 'aif10_RPT_DMSK_HOLDERS')",
                [report_date, code, holder],
            )
        conn.commit()
    finally:
        conn.close()


def build_tushare_raw_db(path: Path, *, extra_rows: list[tuple[str, str, str, float]] = ()) -> None:
    conn = duck_connect(str(path), read_only=False)
    try:
        conn.execute(
            "CREATE TABLE raw_tushare_top_list (ts_code VARCHAR, trade_date VARCHAR, "
            "reason VARCHAR, net_amount DOUBLE)"
        )
        ensure_data_deletion_tables(conn)
        ensure_schema_version_table(conn)
        rows = [
            ("900901.SH", "20260901", "reason-1", 1.0),
            ("200011.SZ", "20260902", "reason-2", 2.0),
            ("600900.SH", "20260903", "reason-3", 3.0),
            ("9000011", "20260904", "reason-4", 4.0),
            ("920001.BJ", "20260905", "reason-5", 5.0),
        ] + list(extra_rows)
        conn.executemany(
            "INSERT INTO raw_tushare_top_list (ts_code, trade_date, reason, net_amount) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def write_snapshot(path: Path, partitions: list[str]) -> None:
    snap = {
        "domains": {
            "org_holding": {
                "accepted": [
                    {"dataset_id": ORG_DATASET_ID, "partition": p, "row_count": 1, "content_hash": "dummy"}
                    for p in partitions
                ]
            }
        }
    }
    path.write_text(json.dumps(snap), encoding="utf-8")


class ConnCache:
    """conn_for closure that memoizes one connection per db alias (plan/execute share it)."""

    def __init__(self, paths: dict[str, Path]):
        self.paths = paths
        self.cache: dict[str, Any] = {}

    def __call__(self, alias: str):
        if alias not in self.cache:
            self.cache[alias] = duck_connect(str(self.paths[alias]), read_only=False)
        return self.cache[alias]

    def close_all(self) -> None:
        for c in self.cache.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass


class _FakeCursor:
    """Minimal cursor stand-in for LyingConn: carries exactly one fabricated row."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [] if self._row is None else [self._row]


class LyingConn:
    """Wraps a real DuckDB connection so ONE targeted ``execute()`` call site can
    be fed a fabricated result while every other statement -- BEGIN/COMMIT/
    ROLLBACK, the real UPDATE/DELETE that actually mutates rows, and every other
    SELECT -- reaches the real database untouched. ``trigger(sql, params)``
    returns the fabricated row to substitute for exactly that call, or ``None``
    to pass the call straight through to the real connection.

    This is what lets a test simulate execute()'s own post-write self-checks
    (readback after UPDATE, existence probe after DELETE, post-delete hit/
    non_hit recount) being handed a value that diverges from what the database
    actually holds, without needing the database itself to misbehave -- the
    on-disk state produced by the real (unforged) statements is exactly what a
    correct run would produce; only the in-process readback the self-check uses
    to convince itself of that state is lied to.
    """

    def __init__(
        self,
        real_conn: Any,
        trigger: Callable[[str, list[Any]], tuple[Any, ...] | None],
    ) -> None:
        self._real = real_conn
        self._trigger = trigger

    def execute(self, sql: str, params: list[Any] | None = None) -> Any:
        fabricated = self._trigger(sql, list(params) if params is not None else [])
        if fabricated is not None:
            return _FakeCursor(fabricated)
        return self._real.execute(sql) if params is None else self._real.execute(sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.fixture
def three_dbs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "org_holding": tmp_path / "org_holding.duckdb",
        "smartmoney": tmp_path / "smartmoney.duckdb",
        "tushare_raw": tmp_path / "tushare_raw.duckdb",
    }
    build_org_holding_db(paths["org_holding"])
    build_smartmoney_db(paths["smartmoney"])
    build_tushare_raw_db(paths["tushare_raw"])
    snap_path = tmp_path / "snapshot.json"
    write_snapshot(snap_path, ["20250430"])  # P3 only: non-intersecting baseline
    paths["snapshot"] = snap_path
    return paths


@pytest.fixture
def real_config() -> Any:
    return cor.load_cleanup_config()


def _plan_for(paths: dict[str, Path], config, cls_name: str = "b_share") -> tuple[Any, ConnCache]:
    cache = ConnCache(paths)
    snapshot_partitions = cor._load_snapshot_partitions(paths["snapshot"])
    plan_obj = cor.plan(cache, config, cls_name, snapshot_partitions=snapshot_partitions)
    return plan_obj, cache


# ── A1: predicate reuse of the audit regex, no literal prefixes in source ──

def test_a1_regex_vectors_and_no_literal_prefixes(real_config) -> None:
    scan_config = real_load_scan_config()
    regex = next(c.regex for c in scan_config.classes if c.name == "b_share")
    import duckdb

    con = duckdb.connect(":memory:")
    hits = ["900901", "900901.SH", "200011", "200011.SZ"]
    misses = ["600900.SH", "9000011", "920001", "920001.BJ", "110001"]
    for v in hits:
        assert con.execute("SELECT regexp_matches(?, ?)", [v, regex]).fetchone()[0] is True, v
    for v in misses:
        assert con.execute("SELECT regexp_matches(?, ?)", [v, regex]).fetchone()[0] is False, v

    source = (REPO / "backend" / "scripts" / "cleanup_out_of_scope_rows.py").read_text(encoding="utf-8")
    assert "900" not in source
    assert "200" not in source


# ── A2: predicate comes from the injected scan config object, not hardcoded ──

def test_a2_predicate_from_config_object(three_dbs, real_config, monkeypatch) -> None:
    fake_class = OutOfScopeClass(
        name="b_share", ruling="fake ruling for test",
        code_patterns=(CodePattern(prefix="999", suffix="SH"),),
        regex=r"^(999\d{3}(\.SH)?)$",
    )
    fake_scan_config = ScanConfig(
        security_code_columns=real_load_scan_config().security_code_columns,
        classes=(fake_class,),
        landing_exemptions=frozenset(),
    )
    monkeypatch.setattr(cor, "load_scan_config", lambda: fake_scan_config)

    # add a 999xxx vector into tushare_raw fixture db directly
    conn = duck_connect(str(three_dbs["tushare_raw"]), read_only=False)
    conn.execute(
        "INSERT INTO raw_tushare_top_list (ts_code, trade_date, reason, net_amount) VALUES (?,?,?,?)",
        ["999001.SH", "20260906", "reason-fake", 6.0],
    )
    conn.commit()
    conn.close()

    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        ts_plan = next(t for t in plan_obj.tables if t.table == "raw_tushare_top_list")
        assert ts_plan.hit == 1
        assert ts_plan.distinct_codes == ("999001.SH",)
        # real B-share vectors must be untouched under the fake class
        untouched = cache("tushare_raw").execute(
            "SELECT ts_code FROM raw_tushare_top_list WHERE ts_code IN ('900901.SH','200011.SZ')"
        ).fetchall()
        assert len(untouched) == 2
        cor.execute(cache, plan_obj, run_id="a2-run")
        remaining = {
            r[0] for r in cache("tushare_raw").execute("SELECT ts_code FROM raw_tushare_top_list").fetchall()
        }
        assert "999001.SH" not in remaining
        assert {"900901.SH", "200011.SZ"} <= remaining
    finally:
        cache.close_all()


# ── A3: canonical repoint correctness (content_hash matches an independent recompute) ──

def test_a3_canonical_repoint_correct(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        cor.execute(cache, plan_obj, run_id="a3-run")
        conn = cache("org_holding")
        pointer = conn.execute(
            "SELECT row_count, content_hash FROM accepted_partition "
            "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20260731'"
        ).fetchone()
        assert int(pointer[0]) == 3

        fields = sorted(ORG_HOLDING_DOMAIN.content_hash_fields)
        # sort by grain (as text), same ORDER BY as partition_accepted_pointer_stats
        payload_rows = conn.execute(
            f"SELECT {', '.join(fields)} FROM canonical_org_holding_detail_period "
            f"WHERE available_date = '20260731' ORDER BY {', '.join(f'CAST({g} AS VARCHAR)' for g in ORG_HOLDING_DOMAIN.grain)}"
        ).fetchall()
        payload = [dict(zip(fields, tuple(r), strict=True)) for r in payload_rows]
        expected_hash = sha256_text(stable_json(payload))
        assert str(pointer[1]) == expected_hash

        mismatches = count_org_pointer_mismatches(conn, verify_content_hash=True)
        assert mismatches == []
    finally:
        cache.close_all()


# ── A4: emptied partition drops the pointer; ingest_batch for that batch is untouched ──

def test_a4_partition_emptied_drops_pointer(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        conn = cache("org_holding")
        before_batch = conn.execute(
            "SELECT status, canonical_row_count, canonical_hash FROM ingest_batch "
            "WHERE batch_id = 'org_holding:20260815'"
        ).fetchone()
        cor.execute(cache, plan_obj, run_id="a4-run")
        pointer = conn.execute(
            "SELECT 1 FROM accepted_partition "
            "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20260815'"
        ).fetchone()
        assert pointer is None
        after_batch = conn.execute(
            "SELECT status, canonical_row_count, canonical_hash FROM ingest_batch "
            "WHERE batch_id = 'org_holding:20260815'"
        ).fetchone()
        assert tuple(after_batch) == tuple(before_batch)
    finally:
        cache.close_all()


# ── A5: unaffected partition + ingest_batch distribution + base-table sets unchanged ──

def test_a5_unaffected_partition_and_ingest_batch_untouched(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        conn = cache("org_holding")
        pointer_before = conn.execute(
            "SELECT row_count, content_hash, batch_id FROM accepted_partition "
            "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20250430'"
        ).fetchone()
        ingest_before = conn.execute(
            "SELECT contract_version, contract_hash, config_hash, source_name, status, COUNT(*) "
            "FROM ingest_batch WHERE dataset_id = ? GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5",
            [ORG_DATASET_ID],
        ).fetchall()
        base_tables_before = {
            db: {str(r[0]) for r in cache(db).execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' AND table_type='BASE TABLE'"
            ).fetchall()}
            for db in ("org_holding", "smartmoney", "tushare_raw")
        }

        cor.execute(cache, plan_obj, run_id="a5-run")

        pointer_after = conn.execute(
            "SELECT row_count, content_hash, batch_id FROM accepted_partition "
            "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20250430'"
        ).fetchone()
        assert tuple(pointer_after) == tuple(pointer_before)

        ingest_after = conn.execute(
            "SELECT contract_version, contract_hash, config_hash, source_name, status, COUNT(*) "
            "FROM ingest_batch WHERE dataset_id = ? GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5",
            [ORG_DATASET_ID],
        ).fetchall()
        assert [tuple(r) for r in ingest_after] == [tuple(r) for r in ingest_before]

        base_tables_after = {
            db: {str(r[0]) for r in cache(db).execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' AND table_type='BASE TABLE'"
            ).fetchall()}
            for db in ("org_holding", "smartmoney", "tushare_raw")
        }
        assert base_tables_after == base_tables_before
    finally:
        cache.close_all()


# ── A6: frozen snapshot guard blocks execution ──

def test_a6_snapshot_guard_blocks_execute(three_dbs, real_config) -> None:
    write_snapshot(three_dbs["snapshot"], ["20260731"])  # P1 intersects -> must block
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        assert plan_obj.executable is False
        assert plan_obj.snapshot_intersection.get(ORG_DATASET_ID) == ("20260731",)

        counts_before = {
            t.table: cor._count(cache(t.db), t.table) for t in plan_obj.tables
        }
        with pytest.raises(cor.CleanupMismatchError):
            cor.execute(cache, plan_obj, run_id="a6-run")

        for t in plan_obj.tables:
            assert cor._count(cache(t.db), t.table) == counts_before[t.table]
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            n = cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.close_all()


# ── A7: plan/execute race — a row lands between plan() and execute() ──

def test_a7_plan_execute_race_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        conn = cache("org_holding")
        # insert an extra B-share raw row bypassing the plan snapshot
        conn.execute(
            "INSERT INTO raw_org_holding_aif10 "
            "(report_date, available_date, stock_code, holder_code, fund_derivecode, holder_name, source) "
            "VALUES ('2026-06-30', '2026-07-31', '900902', 'hraced', '', 'race', 'miaoxiang')"
        )
        conn.commit()

        counts_before = {t.table: cor._count(cache(t.db), t.table) for t in plan_obj.tables}
        with pytest.raises(cor.CleanupMismatchError):
            cor.execute(cache, plan_obj, run_id="a7-run")

        for t in plan_obj.tables:
            assert cor._count(cache(t.db), t.table) == counts_before[t.table]
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            n = cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.close_all()


# ── A8: ledger accounting shape ──

def test_a8_ledger_accounting(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        cor.execute(cache, plan_obj, run_id="a8-run")
        for t in plan_obj.tables:
            rows = cache(t.db).execute(
                "SELECT deletion_run_id, table_name, delete_scope, key_column, key_value, "
                "deleted_rows, verification_json FROM mart_data_deletion_record "
                "WHERE deletion_run_id = 'a8-run' AND table_name = ?",
                [t.table],
            ).fetchall()
            if t.hit == 0:
                assert rows == [], t.table
                continue
            assert len(rows) == 1, t.table
            row = rows[0]
            assert row[2] == "rows_removed_out_of_scope_class"
            assert row[3] == t.code_column
            assert row[4] == "b_share"
            assert int(row[5]) == t.hit
            verification = json.loads(row[6])
            assert len(verification["deleted_keys"]) == t.hit
            assert all(len(k) == len(t.key_columns) for k in verification["deleted_keys"])
    finally:
        cache.close_all()


# ── A9: idempotent second execute ──

def test_a9_idempotent_second_execute(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        cor.execute(cache, plan_obj, run_id="a9-run-1")
        ledger_counts_1 = {
            db: cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            for db in ("org_holding", "smartmoney", "tushare_raw")
        }
        pointers_1 = cache("org_holding").execute(
            "SELECT partition_value, row_count, content_hash FROM accepted_partition ORDER BY 1"
        ).fetchall()

        plan_obj2, _ = _plan_for(three_dbs, real_config)
        assert plan_obj2.is_noop is True
        # reuse the SAME cache/connections for the second execute
        snapshot_partitions = cor._load_snapshot_partitions(three_dbs["snapshot"])
        plan_obj2_same_conn = cor.plan(cache, real_config, "b_share", snapshot_partitions=snapshot_partitions)
        cor.execute(cache, plan_obj2_same_conn, run_id="a9-run-2")

        ledger_counts_2 = {
            db: cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            for db in ("org_holding", "smartmoney", "tushare_raw")
        }
        assert ledger_counts_2 == ledger_counts_1
        pointers_2 = cache("org_holding").execute(
            "SELECT partition_value, row_count, content_hash FROM accepted_partition ORDER BY 1"
        ).fetchall()
        assert [tuple(r) for r in pointers_2] == [tuple(r) for r in pointers_1]
    finally:
        cache.close_all()


# ── A10: other-class rows (keys + content) untouched ──

def test_a10_other_class_rows_untouched(three_dbs, real_config) -> None:
    conn = duck_connect(str(three_dbs["org_holding"]), read_only=False)
    before = conn.execute(
        "SELECT stock_code, holder_code, total_shares FROM canonical_org_holding_detail_period "
        "WHERE stock_code NOT IN ('900901','200011') ORDER BY stock_code, holder_code"
    ).fetchall()
    conn.close()

    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        cor.execute(cache, plan_obj, run_id="a10-run")
        after = cache("org_holding").execute(
            "SELECT stock_code, holder_code, total_shares FROM canonical_org_holding_detail_period "
            "WHERE stock_code NOT IN ('900901','200011') ORDER BY stock_code, holder_code"
        ).fetchall()
        assert [tuple(r) for r in after] == [tuple(r) for r in before]
    finally:
        cache.close_all()


# ── A11: same-db same-transaction atomicity (record_data_deletion fails on 2nd call) ──

def test_a11_same_transaction_atomic(three_dbs, real_config, monkeypatch) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        canonical_before = cache("org_holding").execute(
            "SELECT COUNT(*) FROM canonical_org_holding_detail_period"
        ).fetchone()[0]
        raw_before = cache("org_holding").execute(
            "SELECT COUNT(*) FROM raw_org_holding_aif10"
        ).fetchone()[0]
        pointer_before = cache("org_holding").execute(
            "SELECT partition_value, row_count, content_hash, batch_id FROM accepted_partition ORDER BY 1"
        ).fetchall()

        calls = {"n": 0}
        real_record = cor.record_data_deletion

        def flaky_record(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on second record_data_deletion call")
            return real_record(*args, **kwargs)

        monkeypatch.setattr(cor, "record_data_deletion", flaky_record)

        with pytest.raises(RuntimeError, match="simulated failure"):
            cor.execute(cache, plan_obj, run_id="a11-run")

        assert cache("org_holding").execute(
            "SELECT COUNT(*) FROM canonical_org_holding_detail_period"
        ).fetchone()[0] == canonical_before
        assert cache("org_holding").execute(
            "SELECT COUNT(*) FROM raw_org_holding_aif10"
        ).fetchone()[0] == raw_before
        pointer_after = cache("org_holding").execute(
            "SELECT partition_value, row_count, content_hash, batch_id FROM accepted_partition ORDER BY 1"
        ).fetchall()
        assert [tuple(r) for r in pointer_after] == [tuple(r) for r in pointer_before]
    finally:
        cache.close_all()


# ── A12: unregistered residual reported, never deleted ──

def test_a12_unregistered_reported_not_deleted(three_dbs, real_config) -> None:
    conn = duck_connect(str(three_dbs["tushare_raw"]), read_only=False)
    conn.execute("CREATE TABLE raw_other (ts_code VARCHAR)")
    conn.execute("INSERT INTO raw_other VALUES ('900903.SH'), ('600900.SH')")
    conn.commit()
    conn.close()

    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        assert any(
            row["db"] == "tushare_raw" and row["table"] == "raw_other" for row in plan_obj.unregistered
        )
        cor.execute(cache, plan_obj, run_id="a12-run")
        remaining = cache("tushare_raw").execute("SELECT COUNT(*) FROM raw_other").fetchone()[0]
        assert remaining == 2  # untouched: unregistered table is never written by execute()
    finally:
        cache.close_all()

    # exit code 4 via main() with all three aliases overridden
    argv = [
        "--class", "b_share",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 4


# ── A13: loader fail-closed, one case per gating condition ──

def _write_yaml(tmp_path: Path, obj: dict, name: str = "cleanup.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(obj, allow_unicode=True), encoding="utf-8")
    return p


_VALID_PLAIN = {
    "db": "tushare_raw", "table": "raw_tushare_top_list", "code_column": "ts_code",
    "kind": "plain_delete", "key_columns": ["trade_date", "ts_code", "reason"],
    "why": "test disposition",
}
_VALID_CANONICAL = {
    "db": "org_holding", "table": "canonical_org_holding_detail_period", "code_column": "stock_code",
    "kind": "disclosure_event_canonical",
    "domain": "services.data_sources.org_holding_acceptance:DOMAIN",
    "why": "test disposition",
}


def _valid_doc(dispositions: list[dict]) -> dict:
    return {"version": 1, "dispositions": dispositions}


@pytest.mark.parametrize(
    "mutate_doc",
    [
        pytest.param(lambda d: {**d, "extra_top_key": 1}, id="unknown_top_key"),
        pytest.param(
            lambda d: _valid_doc([{**_VALID_PLAIN, "kind": "delete_everything"}]),
            id="unknown_kind",
        ),
        pytest.param(
            lambda d: _valid_doc([{**_VALID_PLAIN, "db": "no_such_alias"}]),
            id="unknown_db_alias",
        ),
        pytest.param(
            lambda d: _valid_doc([{**_VALID_PLAIN, "code_column": "not_a_code_column"}]),
            id="code_column_not_in_security_code_columns",
        ),
        pytest.param(
            lambda d: _valid_doc([{**_VALID_CANONICAL, "domain": "services.data_sources.no_such_module:DOMAIN"}]),
            id="domain_unresolvable",
        ),
        pytest.param(
            lambda d: _valid_doc([{**_VALID_CANONICAL, "table": "wrong_table_name"}]),
            id="domain_canonical_table_mismatch",
        ),
        pytest.param(
            lambda d: _valid_doc([_VALID_PLAIN, dict(_VALID_PLAIN)]),
            id="duplicate_db_table_pair",
        ),
        pytest.param(
            lambda d: _valid_doc([{**_VALID_CANONICAL, "key_columns": ["stock_code"]}]),
            id="key_columns_kind_mismatch",
        ),
    ],
)
def test_a13_loader_fail_closed(tmp_path, mutate_doc) -> None:
    base = _valid_doc([_VALID_PLAIN])
    doc = mutate_doc(base)
    path = _write_yaml(tmp_path, doc)
    with pytest.raises(cor.OutOfScopeCleanupConfigError):
        cor.load_cleanup_config(path)


def test_a13_main_exits_2_on_bad_config(tmp_path, monkeypatch) -> None:
    bad_path = _write_yaml(tmp_path, {"version": 2, "dispositions": [_VALID_PLAIN]})
    monkeypatch.setattr(cor, "CONFIG_PATH", bad_path)
    assert cor.main(["--class", "b_share"]) == 2


# ── A14: CLI db-override / resolver.db_path / missing-file guard ──

def test_a14_db_override_never_calls_resolver(three_dbs, monkeypatch) -> None:
    def _raise(*_a, **_kw):
        raise AssertionError("resolver.db_path should never be called when all aliases are overridden")

    monkeypatch.setattr(cor.resolver, "db_path", _raise)
    argv = [
        "--class", "b_share",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 0


def test_a14_missing_db_file_exits_2_without_creating_it(tmp_path, three_dbs) -> None:
    missing = tmp_path / "does_not_exist.duckdb"
    argv = [
        "--class", "b_share",
        "--db-override", f"org_holding={missing}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 2
    assert not missing.exists()


# ── A15: --class not in scan classes ──

def test_a15_unknown_class_exits_2(three_dbs) -> None:
    argv = [
        "--class", "no_such_class",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 2


# ── A16: real out_of_scope_cleanup.yaml loads ──

def test_a16_real_config_loads() -> None:
    config = cor.load_cleanup_config()
    assert len(config.dispositions) == 4
    canonical = next(d for d in config.dispositions if d.kind == "disclosure_event_canonical")
    assert canonical.domain is ORG_HOLDING_DOMAIN
    assert canonical.domain.canonical_table == "canonical_org_holding_detail_period"


# ── A17: partition format guard (mixed compact/ISO available_date) ──

def test_a17_partition_format_guard(three_dbs, real_config) -> None:
    conn = duck_connect(str(three_dbs["org_holding"]), read_only=False)
    # Insert an extra B-share row directly with an ISO-formatted available_date
    # sharing the same compact partition value as P1 (20260731).
    conn.execute(
        "INSERT INTO canonical_org_holding_detail_period "
        "(report_date, available_date, stock_code, holder_code, fund_derivecode, "
        " holder_name, org_type_name, total_shares, free_shares_ratio, available_at, "
        " ingest_batch_id, source_row_hash, contract_version, config_hash, built_at) "
        "SELECT report_date, '2026-07-31', '900903', 'hiso', fund_derivecode, holder_name, "
        "org_type_name, total_shares, free_shares_ratio, available_at, ingest_batch_id, "
        "source_row_hash, contract_version, config_hash, built_at "
        "FROM canonical_org_holding_detail_period WHERE available_date = '20260731' LIMIT 1"
    )
    conn.commit()
    conn.close()

    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        assert plan_obj.executable is False
        assert any("compact-equality" in r for r in plan_obj.reasons)
        counts_before = {t.table: cor._count(cache(t.db), t.table) for t in plan_obj.tables}
        with pytest.raises(cor.CleanupMismatchError):
            cor.execute(cache, plan_obj, run_id="a17-run")
        for t in plan_obj.tables:
            assert cor._count(cache(t.db), t.table) == counts_before[t.table]
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            n = cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.close_all()


# ── A18: pointer readback after UPDATE + zero-pointer-rows plan guard ──

def test_a18_zero_pointer_rows_blocks_plan(three_dbs, real_config) -> None:
    conn = duck_connect(str(three_dbs["org_holding"]), read_only=False)
    conn.execute(
        "DELETE FROM accepted_partition "
        "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20260815'"
    )
    conn.commit()
    conn.close()

    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        assert plan_obj.executable is False
        assert any("expected exactly 1" in r for r in plan_obj.reasons)
        with pytest.raises(cor.CleanupMismatchError):
            cor.execute(cache, plan_obj, run_id="a18-run")
    finally:
        cache.close_all()


def test_a18_update_readback_matches_recomputed_value(three_dbs, real_config) -> None:
    # Store P1's pointer partition_value in dashed (non-compact) form, matching
    # the format some other domain writers use (see disclosure_event_partition.py
    # repair-script commentary): plan()/execute() must still find and update it
    # only through the normalized WHERE, proving the readback assertion is load
    # bearing rather than a redundant no-op against an always-compact value.
    conn0 = duck_connect(str(three_dbs["org_holding"]), read_only=False)
    conn0.execute(
        "UPDATE accepted_partition SET partition_value = '2026-07-31' "
        "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20260731'"
    )
    conn0.commit()
    conn0.close()

    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        cor.execute(cache, plan_obj, run_id="a18b-run")
        conn = cache("org_holding")
        n, h = cor.partition_accepted_pointer_stats(conn, ORG_HOLDING_DOMAIN, "20260731")
        readback = conn.execute(
            "SELECT row_count, content_hash FROM accepted_partition "
            "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20260731'"
        ).fetchone()
        assert (int(readback[0]), str(readback[1])) == (n, h)
        assert n == 3
    finally:
        cache.close_all()


# ── A19: _load_snapshot_partitions fail-closed, one isolated case per branch ──
# _load_snapshot_partitions() feeds plan()'s snapshot_partitions argument, which
# drives the A6 frozen-snapshot intersection guard (see test_a6_snapshot_guard_
# blocks_execute above). A mutant that silently returns {} instead of raising on
# a missing/corrupt/malformed snapshot file would make plan() see "no frozen
# partitions anywhere" and let execute() through untouched. Each direct-call
# case below breaks exactly one of the three raise branches while the other two
# preconditions (file present, JSON parseable) still hold, so a mutant that
# disables only one branch is still caught by the case built for that branch.
# The main()-level cases additionally prove the failure is caught *before*
# plan() is ever invoked, not by some other guard downstream.

def test_a19_snapshot_missing_file_raises(tmp_path) -> None:
    missing = tmp_path / "does_not_exist_snapshot.json"
    with pytest.raises(cor.OutOfScopeCleanupConfigError, match="snapshot file missing"):
        cor._load_snapshot_partitions(missing)


def test_a19_snapshot_unreadable_json_raises(tmp_path) -> None:
    bad = tmp_path / "unreadable_snapshot.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(cor.OutOfScopeCleanupConfigError, match="unreadable snapshot"):
        cor._load_snapshot_partitions(bad)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="domains_key_absent"),
        pytest.param({"domains": ["not", "a", "dict"]}, id="domains_value_not_dict"),
    ],
)
def test_a19_snapshot_missing_domains_mapping_raises(tmp_path, body: dict) -> None:
    bad = tmp_path / "no_domains_snapshot.json"
    bad.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(cor.OutOfScopeCleanupConfigError, match="snapshot missing domains mapping"):
        cor._load_snapshot_partitions(bad)


def _forbid_plan(monkeypatch, reason: str) -> None:
    def _raise(*_a, **_kw):
        raise AssertionError(reason)

    monkeypatch.setattr(cor, "plan", _raise)


def _snapshot_argv(three_dbs: dict[str, Path], snapshot_path: Path) -> list[str]:
    return [
        "--class", "b_share",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(snapshot_path),
    ]


@pytest.mark.parametrize(
    "write_bad_snapshot",
    [
        pytest.param(lambda p: None, id="missing_file"),
        pytest.param(lambda p: p.write_text("{not valid json", encoding="utf-8"), id="unreadable_json"),
        pytest.param(lambda p: p.write_text(json.dumps({}), encoding="utf-8"), id="missing_domains"),
    ],
)
def test_a19_main_exits_2_before_plan_is_ever_called(
    three_dbs, monkeypatch, tmp_path, write_bad_snapshot
) -> None:
    snapshot_path = tmp_path / "snapshot_under_test.json"
    write_bad_snapshot(snapshot_path)  # for "missing_file" the path is never created
    _forbid_plan(monkeypatch, "plan() must not run when snapshot loading fails-closed")
    assert cor.main(_snapshot_argv(three_dbs, snapshot_path)) == 2


# ── A20: _parse_db_overrides fail-closed, one isolated case per branch ──
# Mirrors A19: _parse_db_overrides() feeds main()'s alias->path resolution
# (test_a14_db_override_never_calls_resolver above shows a valid override
# bypasses resolver.db_path() entirely). A mutant that stops raising on a
# malformed "--db-override" item would let a typo'd or empty override slip
# through main()'s CONFIG_ERROR gate. Each case isolates exactly one raise
# branch: missing "=" vs. an empty alias/path after the split.

def test_a20_db_override_missing_equals_raises() -> None:
    with pytest.raises(cor.OutOfScopeCleanupConfigError, match="malformed"):
        cor._parse_db_overrides(["org_holding_no_equals_sign"])


@pytest.mark.parametrize(
    "bad_item",
    [
        pytest.param("=x.duckdb", id="empty_alias"),
        pytest.param("org_holding=", id="empty_path"),
        pytest.param("   =x.duckdb", id="whitespace_only_alias"),
        pytest.param("org_holding=   ", id="whitespace_only_path"),
    ],
)
def test_a20_db_override_empty_alias_or_path_raises(bad_item: str) -> None:
    with pytest.raises(cor.OutOfScopeCleanupConfigError, match="malformed"):
        cor._parse_db_overrides([bad_item])


@pytest.mark.parametrize(
    "bad_item",
    [
        pytest.param("org_holding_no_equals_sign", id="missing_equals"),
        pytest.param("=x.duckdb", id="empty_alias"),
        pytest.param("org_holding=", id="empty_path"),
    ],
)
def test_a20_main_exits_2_before_plan_is_ever_called(three_dbs, monkeypatch, bad_item: str) -> None:
    # A malformed item leaves its intended alias (here "org_holding") unset in
    # the overrides dict either way, so a mutant that stops raising would fall
    # through to resolver.db_path("org_holding") for the unresolved alias --
    # which, in a worktree with no data/ directory, happens to fail closed for
    # an *unrelated* reason (file-not-found) and would silently mask this
    # exact regression. Forbidding resolver.db_path too, alongside plan(),
    # ensures this test only goes green because _parse_db_overrides itself
    # raised, not because some other guard coincidentally also returns 2.
    _forbid_plan(monkeypatch, "plan() must not run when --db-override parsing fails-closed")

    def _forbid_resolver(*_a, **_kw):
        raise AssertionError(
            "resolver.db_path must not run when --db-override parsing fails-closed"
        )

    monkeypatch.setattr(cor.resolver, "db_path", _forbid_resolver)
    argv = [
        "--class", "b_share",
        "--db-override", bad_item,
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 2


# ── B1: main() --execute real production-shaped write path (exit 0) ──
# Unlike every test above, this drives main() itself (not cor.execute() directly),
# so the writer_lock acquisition + real duck_connect() open/close + the --execute
# exit_code wiring get exercised for real, not bypassed via direct execute() calls.

def test_b1_main_execute_real_write_exit_0(three_dbs, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "b1_writer.lock"))
    argv = [
        "--class", "b_share", "--execute", "--run-id", "b1-run",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 0

    conn = duck_connect(str(three_dbs["org_holding"]), read_only=True)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM canonical_org_holding_detail_period "
            "WHERE stock_code IN ('900901', '200011')"
        ).fetchone()[0]
        assert remaining == 0
        ledger_rows = conn.execute(
            "SELECT COUNT(*) FROM mart_data_deletion_record WHERE deletion_run_id = 'b1-run'"
        ).fetchone()[0]
        assert ledger_rows > 0
    finally:
        conn.close()


# ── B2: main() --execute while another owner holds the real writer_lock (exit 3) ──
# The lock is acquired for real (not monkeypatched) via a private lock file path,
# proving writer_lock is actually wired into the --execute branch rather than a
# no-op context manager that would let the write proceed regardless.

def test_b2_main_execute_lock_busy_exit_3(three_dbs, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "b2_writer.lock"))
    argv = [
        "--class", "b_share", "--execute",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    with cor.writer_lock("other-owner-holding-the-window"):
        assert cor.main(argv) == 3

    conn = duck_connect(str(three_dbs["org_holding"]), read_only=True)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM canonical_org_holding_detail_period "
            "WHERE stock_code IN ('900901', '200011')"
        ).fetchone()[0]
        assert remaining == 3  # main() never entered plan()/execute(): lock was busy
    finally:
        conn.close()


# ── B3: main() --execute where execute() raises CleanupMismatchError (exit 1) ──

def test_b3_main_execute_mismatch_exit_1(three_dbs, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "b3_writer.lock"))

    def _raise_mismatch(*_args, **_kwargs):
        raise cor.CleanupMismatchError("forced mismatch for exit-code coverage")

    monkeypatch.setattr(cor, "execute", _raise_mismatch)
    argv = [
        "--class", "b_share", "--execute",
        "--db-override", f"org_holding={three_dbs['org_holding']}",
        "--db-override", f"smartmoney={three_dbs['smartmoney']}",
        "--db-override", f"tushare_raw={three_dbs['tushare_raw']}",
        "--snapshot", str(three_dbs["snapshot"]),
    ]
    assert cor.main(argv) == 1

    conn = duck_connect(str(three_dbs["org_holding"]), read_only=True)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM canonical_org_holding_detail_period "
            "WHERE stock_code IN ('900901', '200011')"
        ).fetchone()[0]
        assert remaining == 3  # execute() was replaced before any write happened
    finally:
        conn.close()


# ── B4: unaffected-partition pointer mutated between plan() and execute() ──
# Isolation: P3 (20250430) carries zero hit rows, so touching only its pointer
# row_count leaves every checked_before/hit/non_hit/pointer_before value plan()
# captured for every *other* table and partition unchanged -- only the dedicated
# "unaffected partition pointer changed" guard (not the earlier plan/execute race
# check, not the ingest_batch guard, not the final row-count cross-check) can
# catch this.

def test_b4_unaffected_partition_pointer_changed_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        conn = cache("org_holding")
        conn.execute(
            "UPDATE accepted_partition SET row_count = row_count + 1 "
            "WHERE replace(CAST(partition_value AS VARCHAR),'-','') = '20250430'"
        )
        with pytest.raises(cor.CleanupMismatchError, match="unaffected partition"):
            cor.execute(cache, plan_obj, run_id="b4-run")

        for t in plan_obj.tables:
            assert cor._count(cache(t.db), t.table) == t.checked_before
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            n = cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.close_all()


# ── B5: ingest_batch GROUP BY distribution mutated between plan() and execute() ──
# Isolation: changing config_hash on one already-accepted batch shifts the
# dataset's ingest_batch GROUP BY distribution without moving any canonical/raw
# row count, hit count, or accepted_partition pointer row that plan() recorded --
# only the dedicated ingest_batch-distribution guard can catch this.

def test_b5_ingest_batch_distribution_changed_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        conn = cache("org_holding")
        conn.execute(
            "UPDATE ingest_batch SET config_hash = config_hash || '_mutated_for_test' "
            "WHERE batch_id = 'org_holding:20260731'"
        )
        with pytest.raises(cor.CleanupMismatchError, match="ingest_batch GROUP BY"):
            cor.execute(cache, plan_obj, run_id="b5-run")

        for t in plan_obj.tables:
            assert cor._count(cache(t.db), t.table) == t.checked_before
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            n = cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.close_all()


# ── B6: BASE TABLE set changed mid-cleanup (isolated) ──
# Isolation: a throwaway table appearing after plan() captured the BASE TABLE
# baseline moves nothing else plan() recorded (no row/hit/pointer/ingest_batch
# count changes) -- only the dedicated BASE TABLE guard, which runs after the
# whole per-table loop for that db has already completed (proving it can still
# roll back a fully-written db), can catch it.

def test_b6_base_table_set_changed_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    try:
        conn = cache("org_holding")
        conn.execute("CREATE TABLE __cor_test_throwaway (x INTEGER)")

        with pytest.raises(cor.CleanupMismatchError, match="BASE TABLE set changed") as excinfo:
            cor.execute(cache, plan_obj, run_id="b6-run")
        assert "org_holding" in str(excinfo.value)

        for t in plan_obj.tables:
            assert cor._count(cache(t.db), t.table) == t.checked_before
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            n = cache(db).execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.close_all()


# ── B7: post-UPDATE pointer readback on accepted_partition (line ~673's
# self-check) diverges from the just-written (n, h) pair ──
# Isolation: LyingConn only fabricates the exact 2-column readback SELECT for
# P1's partition_value (20260731); the real UPDATE immediately before it still
# runs for real and writes the correct (n, h), the earlier plan/execute race
# check and the partition current_before/current_pointer checks are untouched,
# and P2/P3 and every other db are never reached because org_holding is first
# in _DB_ORDER_HINT -- only the readback comparison itself can catch a lying or
# partial UPDATE.

def test_b7_pointer_readback_after_update_mismatch_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    readback_sql = (
        f"SELECT row_count, content_hash FROM {cor.ACCEPTED_TABLE} "
        "WHERE dataset_id = ? AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?"
    )

    def trigger(sql: str, params: list[Any]) -> tuple[Any, ...] | None:
        if sql == readback_sql and len(params) == 2 and params[1] == "20260731":
            return (999, "deliberately-wrong-hash-for-test")
        return None

    try:
        real_conn = cache("org_holding")
        cache.cache["org_holding"] = LyingConn(real_conn, trigger)

        with pytest.raises(cor.CleanupMismatchError, match="pointer readback after UPDATE"):
            cor.execute(cache, plan_obj, run_id="b7-run")

        # ROLLBACK on the real connection must have undone the real UPDATE/DELETE
        # that ran before the lie was told -- and smartmoney/tushare_raw, which
        # come after org_holding in db order, must never have been touched.
        canonical_hit_rows = real_conn.execute(
            "SELECT COUNT(*) FROM canonical_org_holding_detail_period "
            "WHERE stock_code IN ('900901', '200011')"
        ).fetchone()[0]
        assert canonical_hit_rows == 3  # P1's 2 + P2's 1, all restored by ROLLBACK
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            conn = real_conn if db == "org_holding" else cache(db)
            n = conn.execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.cache["org_holding"] = real_conn
        cache.close_all()


# ── B8: pointer row still present after DELETE from accepted_partition (line
# ~695's self-check) for the partition that empties (P2, 20260815) ──
# Isolation: LyingConn only fabricates the existence-probe SELECT for P2's
# partition_value, after the real DELETE has already removed that row for
# real; P1's UPDATE+readback (the B7 self-check) runs untouched right before
# it and passes, as do the earlier race check and before-counts -- only the
# DELETE-worked existence probe can catch a DELETE that silently fails to
# remove the pointer row for an emptied partition.

def test_b8_pointer_row_still_present_after_delete_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    still_there_sql = (
        f"SELECT 1 FROM {cor.ACCEPTED_TABLE} WHERE dataset_id = ? "
        "AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?"
    )

    def trigger(sql: str, params: list[Any]) -> tuple[Any, ...] | None:
        if sql == still_there_sql and len(params) == 2 and params[1] == "20260815":
            return (1,)
        return None

    try:
        real_conn = cache("org_holding")
        cache.cache["org_holding"] = LyingConn(real_conn, trigger)

        with pytest.raises(cor.CleanupMismatchError, match="pointer row still present"):
            cor.execute(cache, plan_obj, run_id="b8-run")

        # P1 (processed before P2) really got updated then rolled back with P2's
        # DELETE when the whole db transaction rolled back.
        pointer_rows = real_conn.execute(
            "SELECT partition_value FROM accepted_partition "
            "WHERE replace(CAST(partition_value AS VARCHAR), '-', '') IN ('20260731', '20260815') "
            "ORDER BY 1"
        ).fetchall()
        assert len(pointer_rows) == 2  # both pointers back to their pre-execute state
        for db in ("org_holding", "smartmoney", "tushare_raw"):
            conn = real_conn if db == "org_holding" else cache(db)
            n = conn.execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
            assert n == 0
    finally:
        cache.cache["org_holding"] = real_conn
        cache.close_all()


# ── B9: post-delete hit_after/non_hit_after cross-check (line ~709) on a
# plain_delete table (tushare_raw.raw_tushare_top_list) ──
# Isolation: the trigger only starts fabricating _count_hit's result *after* it
# has observed the real DELETE statement for this table execute, so the
# earlier plan/execute race check (the identical _count_hit call, run before
# the delete) still sees the true value and passes; org_holding and smartmoney
# (both earlier in db order) run and commit for real and untouched -- only the
# post-delete hit_after/non_hit_after compound assertion can catch a DELETE
# whose predicate left stray matching rows behind.

def test_b9_post_delete_hit_after_mismatch_detected(three_dbs, real_config) -> None:
    plan_obj, cache = _plan_for(three_dbs, real_config)
    delete_sql = 'DELETE FROM "raw_tushare_top_list" WHERE regexp_matches(CAST("ts_code" AS VARCHAR), ?)'
    count_hit_sql = (
        'SELECT COUNT(*) FILTER (WHERE regexp_matches(CAST("ts_code" AS VARCHAR), ?)) '
        'FROM "raw_tushare_top_list"'
    )
    state = {"deleted": False}

    def trigger(sql: str, params: list[Any]) -> tuple[Any, ...] | None:
        if sql == delete_sql:
            state["deleted"] = True
            return None  # real DELETE still runs for real
        if state["deleted"] and sql == count_hit_sql:
            return (1,)  # lie: claim a hit row survived the real DELETE
        return None

    try:
        real_conn = cache("tushare_raw")
        cache.cache["tushare_raw"] = LyingConn(real_conn, trigger)

        with pytest.raises(cor.CleanupMismatchError, match="post-delete hit_after"):
            cor.execute(cache, plan_obj, run_id="b9-run")

        # ROLLBACK undid the real DELETE: all 5 original rows are back.
        remaining = real_conn.execute("SELECT COUNT(*) FROM raw_tushare_top_list").fetchone()[0]
        assert remaining == 5
        n = real_conn.execute("SELECT COUNT(*) FROM mart_data_deletion_record").fetchone()[0]
        assert n == 0
    finally:
        cache.cache["tushare_raw"] = real_conn
        cache.close_all()
