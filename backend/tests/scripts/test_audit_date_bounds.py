"""audit_date_bounds.py 的回归测试 —— 全自带 fixture (tmp_path 造小 YAML +
duckdb :memory: 造小表), 不读真实 sync_registry.yaml, 不连真实数据库文件。

**极其重要**: 本项目此前多次因测试依赖宿主环境(真实 DB 文件 / git tag / 时区)连红,
CI 环境没有 data/*.duckdb。本文件里凡涉及 main() 的用例都 monkeypatch 掉
`_make_default_conn_provider`(它才是唯一会碰真实 database_manifest.yaml / 真实
db 文件路径的入口), 换成指向 :memory: 的假 provider。scan_all_domains() 本身
从不碰真实路径——它的 conn_provider 参数就是为此设计的注入点。

也不断言"今天是某个具体日期": 用固定的 `today=datetime.date(2026, 6, 1)` 注入
scan_all_domains, 越界样本值 (28240531 / 19990101 等) 距任何现实中的"今天"都
足够远, 不依赖真实系统时钟。

覆盖 (对应交付要求的 8 个用例):
  1. 正常域, 全部日期在界内 -> 0 条越界
  2. 域有早于 data_start 的值 -> 方向"早于下界"
  3. 域有远超今天的值 (28240531) -> 方向"晚于上界"
  4. 一表两个日期列(ann_date + end_date), 各自越界 -> 两条都报
  5. 格式异常值('N/A'/空串) -> 计入格式异常, 不算越界, 不崩溃
  6. 表不存在(所有候选库都没有) -> 不抛异常, 报告标明
  7. --json 输出可被 json.loads 解析
  8. main() 正常情况下返回 0
外加一个免费搭车断言: 无 freshness_date_column 且 grain 里没有 date 列的域 ->
"无可判定日期列", 不报错也不出现在越界清单里。
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "audit_date_bounds", REPO / "backend" / "scripts" / "audit_date_bounds.py")
ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ad)


TODAY = datetime.date(2026, 6, 1)  # 固定"今天", 不依赖系统真实时钟


FIXTURE_REGISTRY = textwrap.dedent("""\
    version: 1
    domains:
      dom_ok:
        source: fake
        api: fake_api
        target_table: raw_fake_ok
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        freshness_date_column: trade_date
        data_start: "20200101"

      dom_early:
        source: fake
        api: fake_api
        target_table: raw_fake_early
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        freshness_date_column: trade_date
        data_start: "20200101"

      dom_future:
        source: fake
        api: fake_api
        target_table: raw_fake_future
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        freshness_date_column: trade_date
        data_start: "20200101"

      dom_two:
        source: fake
        api: fake_api
        target_table: raw_fake_two
        grain: [ts_code, end_date]
        batch_mode: by_ann_date
        freshness_date_column: ann_date
        data_start: "20190101"

      dom_badfmt:
        source: fake
        api: fake_api
        target_table: raw_fake_badfmt
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        freshness_date_column: trade_date
        data_start: "20200101"

      dom_missing:
        source: fake
        api: fake_api
        target_table: raw_fake_missing_xyz
        grain: [ts_code, trade_date]
        batch_mode: by_trade_date
        freshness_date_column: trade_date
        data_start: "20200101"

      dom_none:
        source: fake
        api: fake_api
        target_table: raw_fake_none
        grain: [ts_code, some_id]
        batch_mode: full_refresh
    """)


def _write_registry(tmp_path: Path) -> Path:
    path = tmp_path / "sync_registry.yaml"
    path.write_text(FIXTURE_REGISTRY, encoding="utf-8")
    return path


def _build_shared_conn() -> duckdb.DuckDBPyConnection:
    """一个 :memory: 库装下全部 fixture 表, 供 provider('tushare_raw') 返回。"""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE raw_fake_ok (trade_date VARCHAR)")
    conn.execute("INSERT INTO raw_fake_ok VALUES ('20200601'), ('20250101')")

    conn.execute("CREATE TABLE raw_fake_early (trade_date VARCHAR)")
    # 19990101 早于本域 data_start(20200101) 但晚于 1990 —— **不该**被报为越界
    # (2026-08-23: 判据下界从 data_start 改为固定 19900101 的核心原因, 见脚本内实测记录);
    # 19890101 早于 A 股开市年, 才是真正的荒谬值。
    conn.execute(
        "INSERT INTO raw_fake_early VALUES ('19990101'), ('20210101'), ('19890101')"
    )

    conn.execute("CREATE TABLE raw_fake_future (trade_date VARCHAR)")
    conn.execute("INSERT INTO raw_fake_future VALUES ('28240531'), ('20210101')")

    conn.execute("CREATE TABLE raw_fake_two (ann_date VARCHAR, end_date VARCHAR)")
    conn.execute(
        "INSERT INTO raw_fake_two VALUES ('19000101','20250101'), ('20250101','28240531')"
    )

    conn.execute("CREATE TABLE raw_fake_badfmt (trade_date VARCHAR)")
    conn.execute("INSERT INTO raw_fake_badfmt VALUES ('N/A'), (''), ('20250101')")

    conn.execute("CREATE TABLE raw_fake_none (some_id VARCHAR)")
    # 注意: 故意不建 raw_fake_missing_xyz, 用于验证"表不存在"路径。
    return conn


def _shared_provider(conn):
    def provider(alias: str):
        return conn if alias == "tushare_raw" else None
    return provider


def _scan(tmp_path: Path, today=TODAY):
    path = _write_registry(tmp_path)
    import yaml
    domains = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("domains") or {}
    conn = _build_shared_conn()
    try:
        results = ad.scan_all_domains(domains, _shared_provider(conn), today=today)
    finally:
        conn.close()
    return {r["domain"]: r for r in results}


def _by(results, domain):
    return results[domain]


def _col(domain_result, name):
    return next(c for c in domain_result["columns"] if c["column"] == name)


# ── 1. 正常域, 全部在界内 -> 0 条越界 ────────────────────────────────────────

def test_normal_domain_all_in_bounds(tmp_path):
    results = _scan(tmp_path)
    dom = _by(results, "dom_ok")
    assert dom["status"] == "checked"
    col = _col(dom, "trade_date")
    assert col["below_count"] == 0
    assert col["above_count"] == 0
    assert col["bad_format_count"] == 0


# ── 2. 早于固定下界 19900101 -> "早于下界" ─────────────────────────────────────────

def test_below_bound_uses_fixed_1990_not_data_start(tmp_path):
    """下界是固定的 19900101, 不是该域的 data_start.

    dom_early 的 data_start=20200101, 表里有 19990101 / 20210101 / 19890101 三行。
    只有 19890101 (早于 A 股开市年) 该被报为越界; 19990101 虽然早于 data_start,
    但它是完全合法的历史日期 —— 报告期/解禁日等列天然可以早于采集轴起点。

    2026-08-23 回归锁: 初版拿 data_start 当下界, 实跑 44 域报出 328,266 行越界而其中
    只有 3 行是真异常 (仅 stk_holdernumber.end_date 一列就误报 6,235 行)。
    判据的价值取决于信噪比, 这条断言锁住"不按 data_start 误报"。
    """
    results = _scan(tmp_path)
    dom = _by(results, "dom_early")
    assert dom["status"] == "checked"
    col = _col(dom, "trade_date")
    assert col["below_count"] == 1, col
    assert col["above_count"] == 0
    assert col["below_samples"] == ["19890101"]
    assert "19990101" not in col["below_samples"], "早于 data_start 但合法的日期被误报"


# ── 3. 远超今天 (28240531) -> "晚于上界" ─────────────────────────────────────

def test_far_future_value_flagged_above(tmp_path):
    results = _scan(tmp_path)
    dom = _by(results, "dom_future")
    assert dom["status"] == "checked"
    col = _col(dom, "trade_date")
    assert col["above_count"] == 1
    assert col["below_count"] == 0
    assert col["above_samples"] == ["28240531"]


# ── 4. 一表两个日期列, 各自越界, 两条都报 (防"只检查第一列") ──────────────────

def test_two_date_columns_both_reported(tmp_path):
    results = _scan(tmp_path)
    dom = _by(results, "dom_two")
    assert dom["status"] == "checked"
    checked_cols = {c["column"] for c in dom["columns"]}
    assert checked_cols == {"ann_date", "end_date"}

    ann = _col(dom, "ann_date")
    assert ann["below_count"] == 1
    assert ann["above_count"] == 0
    assert ann["below_samples"] == ["19000101"]

    end = _col(dom, "end_date")
    assert end["below_count"] == 0
    assert end["above_count"] == 1
    assert end["above_samples"] == ["28240531"]


# ── 5. 格式异常值 -> 计入格式异常, 不算越界, 不崩溃 ───────────────────────────

def test_malformed_values_counted_as_bad_format_not_violation(tmp_path):
    results = _scan(tmp_path)
    dom = _by(results, "dom_badfmt")
    assert dom["status"] == "checked"
    col = _col(dom, "trade_date")
    assert col["bad_format_count"] == 2  # 'N/A' + ''
    assert col["below_count"] == 0
    assert col["above_count"] == 0


# ── 6. 表不存在 -> 不抛异常, 报告标明 ────────────────────────────────────────

def test_table_not_found_does_not_raise(tmp_path):
    results = _scan(tmp_path)
    dom = _by(results, "dom_missing")
    assert dom["status"] == "table_not_found"
    assert dom["columns"] == []


# ── 搭车: 无可判定日期列的域 -> 跳过, 不报错 ─────────────────────────────────

def test_domain_without_date_column_is_skipped(tmp_path):
    results = _scan(tmp_path)
    dom = _by(results, "dom_none")
    assert dom["status"] == "no_date_column"
    assert dom["columns"] == []


# ── 7. --json 输出可被 json.loads 解析 ───────────────────────────────────────

def test_json_output_parses(tmp_path, monkeypatch, capsys):
    path = _write_registry(tmp_path)
    conn = _build_shared_conn()

    def fake_factory():
        def provider(alias: str):
            return conn if alias == "tushare_raw" else None

        def close_all():
            conn.close()

        return provider, close_all

    monkeypatch.setattr(ad, "_make_default_conn_provider", fake_factory)

    rc = ad.main(["--registry", str(path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["total_domains"] == 7
    assert payload["checked_domains"] == 5  # 除 dom_missing / dom_none 外
    assert "dom_missing" in payload["table_not_found_domains"]
    assert "dom_none" in payload["no_date_column_domains"]
    assert isinstance(payload["domains"], list)
    two = next(d for d in payload["domains"] if d["domain"] == "dom_two")
    assert {c["column"] for c in two["columns"]} == {"ann_date", "end_date"}


# ── 8. main() 正常情况下返回 0 ───────────────────────────────────────────────

def test_main_returns_zero(tmp_path, monkeypatch, capsys):
    path = _write_registry(tmp_path)
    conn = _build_shared_conn()

    def fake_factory():
        def provider(alias: str):
            return conn if alias == "tushare_raw" else None

        def close_all():
            conn.close()

        return provider, close_all

    monkeypatch.setattr(ad, "_make_default_conn_provider", fake_factory)

    rc = ad.main(["--registry", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "日期越界审计报告" in out
    assert "晚于上界的值可能是合法的未来日期" in out


def test_main_returns_nonzero_on_missing_registry(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.yaml"
    rc = ad.main(["--registry", str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "出错" in err
