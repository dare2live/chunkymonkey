#!/usr/bin/env python3
"""十大流通股东 Phase A staging — 只读验证器 (ingest_holders_raw, 2026-09).

背景 (三份 fable 研判后定的第一刀): 十大流通股东 canonical 610,414 行；供应商
(妙想 ``RPT_F10_EH_FREEHOLDERS``) 47 字段, ``_clean`` 只取 13 输出 15 键。第一刀
范围 = Phase A: 只把供应商原行取到 staging DuckDB + 跑本验证器, **不写生产库、
不动 schema 版本**。回退 = 删 staging 文件。本脚本是"只读验证器"那一半, 独立
于负责联网取数、把原行写进 staging 的那个脚本 (另一任务, 不在本文件)。

本脚本产出六个数 —— 协调方后续所有分歧都靠这六个数裁决:

  1. 取数完整性       (check_fetch_completeness)
  2. 推导规则零例外   (check_derivation_rules)
  3. raw 对 canonical 的覆盖 (check_raw_covers_canonical)
  4. raw 内部重复组   (check_raw_internal_duplicates)
  5. 退出空洞量化     (check_exit_gap)
  6. 身份实验         (check_identity_experiment)

前四项是硬门 (FAIL 即整体非 0)；第 5 项对"canonical 退出行多于 raw 能派生出的
退出行"这一个方向判 FAIL (其余按 report_date 分层只报数, 不判)；第 6 项纯报数
(status="INFO"，协调方拿它做身份归并决策，不是本脚本能判断对错的东西)。

── 读什么、不写什么 ────────────────────────────────────────────────────────
只读三个 DuckDB: staging (本脚本的"母"连接) + 生产 smartmoney (ATTACH prod
READ_ONLY，读 ``canonical_top10_float_holders_period``) + 生产 feature_store
(ATTACH feat READ_ONLY, 读 ``mart_inst_profile``)。``build_connection`` 建完连接
后立即跑 ``require_all_read_only``: 只要 ``duckdb_databases()`` 报告任何一个非
internal 库不是 read-only, 直接 PermissionError 拒绝往下跑——不给写生产库的可能
开任何口子 (CLAUDE.md 红线: "不写任何生产库"; 本文件任务卡: "一个字节都不许写
生产库")。这条防线是机制性的 (查 DuckDB 目录表), 不是"调用方保证过了"式的口头
约定，所以能被单测直接构造一个可写连接来验证它确实拒绝。

── staging 数据契约 (本脚本假定的表结构; 取数脚本要跟它对齐) ───────────────
``raw_fetch`` (每次对某股的一次供应商拉取一行):
    stock_code   VARCHAR NOT NULL
    fetch_id     VARCHAR NOT NULL
    status       VARCHAR NOT NULL   -- 'ok' | 'error' | 'empty'
    error        VARCHAR            -- status='error' 时填
    truncated    BOOLEAN            -- 分页在到达 max_pages 时被截断 (未确认取全) 为 TRUE
    page_count   INTEGER
    row_count    INTEGER
    fetched_at   VARCHAR
    request_json VARCHAR

``raw_rows`` (供应商原行, 按类型建列; 列名用供应商原始大写键, 不是我们的小写
canonical 命名 —— 与 ``services.data_sources.holders_top10_schema.RAW_FIELDS``
的 47 项一一对应, 单一真相源在那边, 本文件只 import 用, 不重复声明):
    fetch_id     VARCHAR NOT NULL
    row_ordinal  INTEGER NOT NULL
    <47 RAW_FIELDS 列, 全部 nullable>
    row_hash     VARCHAR            -- 可选, 本验证器不依赖它
    extra_json   VARCHAR            -- 可选, 本验证器不依赖它
    PRIMARY KEY (fetch_id, row_ordinal)

用法:
    PYTHONPATH=backend python backend/scripts/check_holders_staging.py \\
        --staging data/scratch/ingest_holders_raw/<run_id>.duckdb [--json]

    --prod-db / --feature-store-db 默认走 database_manifest.yaml 的
    smartmoney / feature_store 别名 (与其它脚本一致, 不硬编码路径)。

退出码: 0 = 全部硬门 PASS; 1 = 至少一项 FAIL (第 5/6 项的 INFO 状态不影响退出码，
除非第 5 项检出"负 gap" 那种真正的矛盾, 那种情况它自己会给 FAIL)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.duck_adapter import connect as duck_connect, DuckConn  # noqa: E402
from services.database_manifest import get_database_manifest  # noqa: E402
from services.data_sources.holders_top10_schema import (  # noqa: E402
    CANONICAL_TABLE,
    RAW_FIELDS,
)
from services.holders_aif10 import (  # noqa: E402
    DEFAULT_START_PERIOD,
    _clean,
    _compact_date,
    _derive_exits,
    _safe_int,
    _safe_text,
    _share_class,
)

# ── staging schema contract (see module docstring) ─────────────────────────
RAW_FETCH_TABLE = "raw_fetch"
RAW_ROWS_TABLE = "raw_rows"
PROD_ALIAS = "prod"
FEAT_ALIAS = "feat"
MART_INST_PROFILE_TABLE = "mart_inst_profile"

RAW_COL_NAMES: tuple[str, ...] = tuple(name for name, _ in RAW_FIELDS)

REQUIRED_RAW_FETCH_COLUMNS = frozenset(
    {"stock_code", "fetch_id", "status", "error", "truncated", "page_count", "row_count"}
)
REQUIRED_RAW_ROWS_COLUMNS = frozenset({"fetch_id", "row_ordinal", *RAW_COL_NAMES})

# 供应商定期报告期末日 (MMDD)。IS_REPORT='1' 的行按实测 (§任务卡) 应恰好落在这四个。
_QUARTER_END_MMDD = ("0331", "0630", "0930", "1231")
# 显式拼成 SQL IN 列表文本 (不依赖 Python tuple repr 恰好长得像 SQL 字面量)。
_QUARTER_END_SQL_LIST = "(" + ", ".join(f"'{m}'" for m in _QUARTER_END_MMDD) + ")"


def _result(
    name: str,
    status: str,
    observed: Any,
    expected: Any,
    *,
    detail: Optional[dict] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "status": status, "observed": observed, "expected": expected}
    if detail is not None:
        out["detail"] = detail
    return out


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ── read-only guard ─────────────────────────────────────────────────────────


def attached_database_readonly_map(conn) -> dict[str, bool]:
    """{database_name: readonly} for every non-internal database on ``conn``.

    Includes the main/staging database itself, not just ATTACHed aliases.
    """

    rows = conn.execute(
        "SELECT database_name, readonly, internal FROM duckdb_databases()"
    ).fetchall()
    return {r[0]: bool(r[1]) for r in rows if not bool(r[2])}


def require_all_read_only(conn) -> dict[str, bool]:
    """Refuse to proceed unless every non-internal database on ``conn`` is read-only.

    This is THE safety mechanism behind "一个字节都不许写生产库": it does not
    trust that the caller *meant* to open things read-only, it asks DuckDB's
    own catalog (``duckdb_databases().readonly``) and raises ``PermissionError``
    the moment anything is writable. Call this immediately after building any
    connection this module will run checks against.
    """

    status = attached_database_readonly_map(conn)
    writable = sorted(name for name, readonly in status.items() if not readonly)
    if writable:
        raise PermissionError(
            "check_holders_staging refuses to run against a writable database "
            f"connection: {writable} attached/opened as read-write. Phase A rule: "
            "not a single byte may be written to production; open every database "
            "(staging included) READ_ONLY."
        )
    return status


def _require_alias(conn, alias: str) -> None:
    status = attached_database_readonly_map(conn)
    if alias not in status:
        raise RuntimeError(
            f"expected attached database alias {alias!r} not found "
            f"(attached: {sorted(status)}); ATTACH may have failed silently "
            "(duck_adapter logs a warning and continues on attach failure)"
        )


# ── staging schema validation ───────────────────────────────────────────────


def _table_columns(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"DESCRIBE {table}").fetchall()
    except Exception as exc:  # noqa: BLE001 — turn into an actionable message
        raise RuntimeError(
            f"staging table {table!r} not found or unreadable: {exc}"
        ) from exc
    return {r[0] for r in rows}


def require_staging_schema(conn) -> None:
    fetch_cols = _table_columns(conn, RAW_FETCH_TABLE)
    missing_fetch = REQUIRED_RAW_FETCH_COLUMNS - fetch_cols
    if missing_fetch:
        raise RuntimeError(
            f"{RAW_FETCH_TABLE} missing required columns: {sorted(missing_fetch)}"
        )
    rows_cols = _table_columns(conn, RAW_ROWS_TABLE)
    missing_rows = REQUIRED_RAW_ROWS_COLUMNS - rows_cols
    if missing_rows:
        raise RuntimeError(
            f"{RAW_ROWS_TABLE} missing required columns "
            f"(showing up to 10): {sorted(missing_rows)[:10]}"
        )


# ── connection builder ──────────────────────────────────────────────────────


def build_connection(
    staging_path: Path,
    prod_db_path: Path,
    feature_store_db_path: Path,
) -> DuckConn:
    """staging = main connection (read-only); prod/feat ATTACHed read-only.

    Raises PermissionError / RuntimeError before returning if anything is
    writable or the expected tables/aliases are missing — callers do not need
    to re-check.
    """

    conn = duck_connect(
        str(staging_path),
        read_only=True,
        attach={
            PROD_ALIAS: {"path": str(prod_db_path), "read_only": True},
            FEAT_ALIAS: {"path": str(feature_store_db_path), "read_only": True},
        },
    )
    require_all_read_only(conn)
    _require_alias(conn, PROD_ALIAS)
    _require_alias(conn, FEAT_ALIAS)
    require_staging_schema(conn)
    return conn


# ── 1. 取数完整性 ────────────────────────────────────────────────────────────


def check_fetch_completeness(conn) -> dict[str, Any]:
    """``raw_fetch`` 的 ok 数 / error 逐股清单 / truncated 必须 = 0。"""

    counts = conn.execute(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE status = 'ok') AS ok_count,
          COUNT(*) FILTER (WHERE status = 'error') AS error_count,
          COUNT(*) FILTER (WHERE status = 'empty') AS empty_count,
          COUNT(*) FILTER (WHERE COALESCE(truncated, FALSE)) AS truncated_count,
          COUNT(*) AS total_count
        FROM {RAW_FETCH_TABLE}
        """
    ).fetchone()
    ok_count, error_count, empty_count, truncated_count, total_count = counts

    error_rows = conn.execute(
        f"""
        SELECT stock_code, fetch_id, error
        FROM {RAW_FETCH_TABLE}
        WHERE status = 'error'
        ORDER BY stock_code
        """
    ).fetchall()
    truncated_rows = conn.execute(
        f"""
        SELECT stock_code, fetch_id, page_count, row_count
        FROM {RAW_FETCH_TABLE}
        WHERE COALESCE(truncated, FALSE)
        ORDER BY stock_code
        """
    ).fetchall()

    observed = {
        "total_count": total_count,
        "ok_count": ok_count,
        "error_count": error_count,
        "empty_count": empty_count,
        "truncated_count": truncated_count,
        "error_stocks": [
            {"stock_code": r[0], "fetch_id": r[1], "error": r[2]} for r in error_rows
        ],
        "truncated_stocks": [
            {"stock_code": r[0], "fetch_id": r[1], "page_count": r[2], "row_count": r[3]}
            for r in truncated_rows
        ],
    }
    status = "FAIL" if truncated_count > 0 else "PASS"
    expected = {"truncated_count": 0}
    return _result("fetch_completeness", status, observed, expected)


# ── 2. 三条推导规则在全市场是否零例外 ───────────────────────────────────────


def check_derivation_rules(conn) -> dict[str, Any]:
    """全市场跑 4 条此前只在样本上验过的供应商行内规则 (0 例外)。

    - (IS_REPORT='1') <> (END_DATE 是季末)
    - (IS_HOLDORG='1') <> (HOLDER_CODE IS NOT NULL)
    - 每个 HOLDER_CODE 对应的 DISTINCT HOLDER_CODE_OLD 必须 <= 1
    - NOTICE_DATE > UPDATE_DATE 的行数必须 = 0

    不可解析的日期一律按"测不出"算作违规 (fail-closed, CLAUDE.md 红线 3: 缺失
    只能传播为缺失, 不当"合规"处理), 与"实测样本上零例外"一起单列, 不混在一起
    汇报。
    """

    season_end = conn.execute(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE TRY_CAST(END_DATE AS TIMESTAMP) IS NULL) AS unparseable,
          COUNT(*) FILTER (
            WHERE TRY_CAST(END_DATE AS TIMESTAMP) IS NOT NULL
              AND (IS_REPORT = '1') <> (
                strftime(TRY_CAST(END_DATE AS TIMESTAMP), '%m%d') IN {_QUARTER_END_SQL_LIST}
              )
          ) AS mismatch
        FROM {RAW_ROWS_TABLE}
        """
    ).fetchone()

    holdorg_violations = conn.execute(
        f"""
        SELECT COUNT(*) FROM {RAW_ROWS_TABLE}
        WHERE IS_HOLDORG IS NULL OR (IS_HOLDORG = '1') <> (HOLDER_CODE IS NOT NULL)
        """
    ).fetchone()[0]

    code_old_groups = conn.execute(
        f"""
        SELECT HOLDER_CODE, COUNT(DISTINCT HOLDER_CODE_OLD) AS n
        FROM {RAW_ROWS_TABLE}
        WHERE HOLDER_CODE IS NOT NULL
        GROUP BY HOLDER_CODE
        HAVING COUNT(DISTINCT HOLDER_CODE_OLD) > 1
        ORDER BY n DESC
        LIMIT 50
        """
    ).fetchall()
    code_old_violation_count = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
          SELECT HOLDER_CODE
          FROM {RAW_ROWS_TABLE}
          WHERE HOLDER_CODE IS NOT NULL
          GROUP BY HOLDER_CODE
          HAVING COUNT(DISTINCT HOLDER_CODE_OLD) > 1
        ) t
        """
    ).fetchone()[0]

    notice_vs_update = conn.execute(
        f"""
        SELECT
          COUNT(*) FILTER (
            WHERE TRY_CAST(NOTICE_DATE AS TIMESTAMP) IS NULL
               OR TRY_CAST(UPDATE_DATE AS TIMESTAMP) IS NULL
          ) AS unparseable,
          COUNT(*) FILTER (
            WHERE TRY_CAST(NOTICE_DATE AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST(UPDATE_DATE AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST(NOTICE_DATE AS TIMESTAMP) > TRY_CAST(UPDATE_DATE AS TIMESTAMP)
          ) AS violation
        FROM {RAW_ROWS_TABLE}
        """
    ).fetchone()

    rules = {
        "is_report_matches_season_end": {
            "unparseable_end_date": season_end[0],
            "mismatch": season_end[1],
            "violations": season_end[0] + season_end[1],
        },
        "is_holdorg_matches_holder_code_present": {
            "violations": holdorg_violations,
        },
        "holder_code_old_at_most_one_per_holder_code": {
            "violations": code_old_violation_count,
            "examples": [
                {"holder_code": r[0], "distinct_holder_code_old": r[1]}
                for r in code_old_groups
            ],
        },
        "notice_date_not_after_update_date": {
            "unparseable": notice_vs_update[0],
            "violation": notice_vs_update[1],
            "violations": notice_vs_update[0] + notice_vs_update[1],
        },
    }
    total_violations = sum(r["violations"] for r in rules.values())
    observed = {"total_violations": total_violations, "rules": rules}
    expected = {"total_violations": 0}
    status = "FAIL" if total_violations > 0 else "PASS"
    return _result("derivation_rules_zero_exception", status, observed, expected)


# ── 3. raw 对现 canonical 的覆盖 ─────────────────────────────────────────────


def check_raw_covers_canonical(conn) -> dict[str, Any]:
    """canonical 入榜行 (按内容键) 必须能在 raw 中命中。

    内容键 = (stock_code, report_date, holder_rank, holder_name, share_class)
    —— 注意这不是 canonical 的 GRAIN (GRAIN 另含 holder_set/row_seq/is_exit_row),
    是任务卡钉死的比对键, 专为"翻页重复不该造成漏判"设计: 对 canonical 按内容键
    去重 (SELECT DISTINCT) 天然排除了已知的 18,036 行逐字重复副本 —— 重复副本
    的内容键本来就相同, 去重后自动只剩一份, 不需要另外写排除逻辑。

    share_class 用 ``holders_aif10._share_class`` 原函数从 raw 的 SHARES_TYPE
    重新推导 (与生产 _clean 同一份代码, 不在 SQL 里重新实现一遍防止两边分叉)。
    HOLDER_RANK 缺失的 raw 行被跳过而不是像 ``_clean`` 那样退化用 idx 兜底
    (idx 依赖同一次 fetch 内的完整顺序, 内容键比对场景下没有意义) —— 跳过的
    行数在 observed 里报出来, 不吞掉。
    """

    canon_rows = conn.execute(
        f"""
        SELECT DISTINCT stock_code, report_date, holder_rank, holder_name, share_class
        FROM {PROD_ALIAS}.{CANONICAL_TABLE}
        WHERE NOT is_exit_row
        """
    ).fetchall()
    canon_keys = {(r[0], r[1], r[2], r[3], r[4]) for r in canon_rows}

    raw_proj = conn.execute(
        f"""
        SELECT SECURITY_CODE, END_DATE, HOLDER_RANK, HOLDER_NAME, SHARES_TYPE
        FROM {RAW_ROWS_TABLE}
        """
    ).fetchall()
    raw_keys: set[tuple[Any, ...]] = set()
    skipped_unusable = 0
    for security_code, end_date, holder_rank, holder_name, shares_type in raw_proj:
        stock_code = _safe_text(security_code)
        report_date = _compact_date(end_date)
        name = _safe_text(holder_name)
        rank = _safe_int(holder_rank)
        if stock_code is None or report_date is None or name is None or rank is None:
            skipped_unusable += 1
            continue
        raw_keys.add((stock_code, report_date, rank, name, _share_class(shares_type)))

    missing = sorted(canon_keys - raw_keys)
    missing_stocks = sorted({k[0] for k in missing})
    observed = {
        "canonical_listed_content_keys": len(canon_keys),
        "raw_content_keys": len(raw_keys),
        "raw_rows_skipped_missing_key_field": skipped_unusable,
        "missing_count": len(missing),
        "missing_stock_count": len(missing_stocks),
        "missing_stocks_sample": missing_stocks[:50],
        "missing_rows_sample": [
            {
                "stock_code": k[0],
                "report_date": k[1],
                "holder_rank": k[2],
                "holder_name": k[3],
                "share_class": k[4],
            }
            for k in missing[:50]
        ],
    }
    expected = {"missing_count": 0}
    status = "FAIL" if missing else "PASS"
    return _result("raw_covers_canonical_content_keys", status, observed, expected)


# ── 4. raw 内部重复组 ────────────────────────────────────────────────────────


def check_raw_internal_duplicates(conn) -> dict[str, Any]:
    """raw 内部 (同一 fetch_id 内, 逐字相同的行) 重复组必须 = 0。

    这条要坐实"per-股票分页翻页是稳的" —— 生产库 18,036 行重复来自按
    UPDATE_DATE 拉全市场那条路径 (多股票在同一 (期, 排名) 上并列, 分页边界随
    每股票都可能不同); Phase A 按 secucode 逐股取全史, 理论上不该撞到那种翻页
    重复。这条不判"必须"结论正确, 只报数, 但按任务卡它必须 = 0 才算 PASS。

    比较键 = 全部 47 个 RAW_FIELDS 列 (不依赖可选的 row_hash 列)。
    """

    cols_sql = ", ".join(RAW_COL_NAMES)
    dup_groups = conn.execute(
        f"""
        SELECT fetch_id, COUNT(*) AS n
        FROM {RAW_ROWS_TABLE}
        GROUP BY fetch_id, {cols_sql}
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    duplicate_groups = len(dup_groups)
    duplicate_rows_total = sum(r[1] for r in dup_groups)
    extra_rows = duplicate_rows_total - duplicate_groups

    examples = conn.execute(
        f"""
        SELECT fetch_id, SECURITY_CODE, END_DATE, HOLDER_NAME, HOLDER_RANK, COUNT(*) AS n
        FROM {RAW_ROWS_TABLE}
        GROUP BY fetch_id, {cols_sql}
        HAVING COUNT(*) > 1
        ORDER BY n DESC
        LIMIT 50
        """
    ).fetchall()

    observed = {
        "duplicate_groups": duplicate_groups,
        "duplicate_rows_total": duplicate_rows_total,
        "extra_rows": extra_rows,
        "examples": [
            {
                "fetch_id": r[0],
                "stock_code": r[1],
                "end_date": r[2],
                "holder_name": r[3],
                "holder_rank": r[4],
                "count": r[5],
            }
            for r in examples
        ],
    }
    expected = {"duplicate_groups": 0}
    status = "FAIL" if duplicate_groups > 0 else "PASS"
    return _result("raw_internal_duplicates", status, observed, expected)


# ── 5. 退出空洞量化 ──────────────────────────────────────────────────────────


def check_exit_gap(conn, *, start_period: str = DEFAULT_START_PERIOD) -> dict[str, Any]:
    """raw 可派生的退出行数 − canonical 现有退出行数, 按 report_date 分层。

    退出行的派生直接调用生产的 ``_clean`` + ``_derive_exits`` (逐股跑, 与真实
    管线同一份纯函数), 不在 SQL 里重新实现一遍 period-diff 逻辑。

    本函数不对"gap 是正是负"整体判 PASS/FAIL —— 已知生产库退出行系统性偏少
    (report_date=20260630 只有 698/3,697), 正 gap 是预期中的证据缺口, 属于
    "报数"而非"判定"。但 gap < 0 (canonical 退出行数超过 raw 能派生出的数量)
    是另一种矛盾 —— raw 是这次全新拉取的证据, 不应该比现有 canonical 更"贫",
    出现负 gap 判 FAIL。
    """

    stock_codes = [
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT SECURITY_CODE FROM {RAW_ROWS_TABLE} WHERE SECURITY_CODE IS NOT NULL"
        ).fetchall()
    ]

    raw_exit_by_period: dict[str, int] = {}
    for stock_code in stock_codes:
        cur = conn.execute(
            f"SELECT * FROM {RAW_ROWS_TABLE} WHERE SECURITY_CODE = ?",
            [stock_code],
        )
        raw_dicts = [_row_to_dict(row) for row in cur.fetchall()]
        clean_rows = _clean(raw_dicts, start_period=start_period)
        if not clean_rows:
            continue
        for exit_row in _derive_exits(clean_rows):
            rd = exit_row["report_date"]
            raw_exit_by_period[rd] = raw_exit_by_period.get(rd, 0) + 1

    canon_rows = conn.execute(
        f"""
        SELECT report_date, COUNT(*)
        FROM {PROD_ALIAS}.{CANONICAL_TABLE}
        WHERE is_exit_row
        GROUP BY report_date
        """
    ).fetchall()
    canon_exit_by_period = {r[0]: r[1] for r in canon_rows}

    periods = sorted(set(raw_exit_by_period) | set(canon_exit_by_period))
    by_period = []
    negative_gap_periods = []
    for period in periods:
        raw_n = raw_exit_by_period.get(period, 0)
        canon_n = canon_exit_by_period.get(period, 0)
        gap = raw_n - canon_n
        by_period.append(
            {
                "report_date": period,
                "raw_derivable_exits": raw_n,
                "canonical_exit_rows": canon_n,
                "gap": gap,
            }
        )
        if gap < 0:
            negative_gap_periods.append(period)

    observed = {
        "by_report_date": by_period,
        "total_raw_derivable_exits": sum(raw_exit_by_period.values()),
        "total_canonical_exit_rows": sum(canon_exit_by_period.values()),
        "negative_gap_periods": negative_gap_periods,
        "stocks_considered": len(stock_codes),
    }
    expected = {"negative_gap_periods": []}
    status = "FAIL" if negative_gap_periods else "INFO"
    return _result("exit_hole_by_report_date", status, observed, expected)


# ── 6. 身份实验 ──────────────────────────────────────────────────────────────

_MORGAN_STANLEY_PATTERNS = ("摩根士丹利", "Morgan Stanley")


def check_identity_experiment(conn) -> dict[str, Any]:
    """协调方要的关键数: 换成 HOLDER_CODE 当身份键会塌缩多少 holder_name。

    - canonical 里, 在同一 HOLDER_CODE 下会被塌缩到一起的 holder_name 变体数
      (即: 该 HOLDER_CODE 在 raw 中对应 >1 个不同 HOLDER_NAME, 且这些名字确实
      出现在 canonical 里)。
    - 上面这批变体名字里, 有多少个是 mart_inst_profile 现有的 76,136 个
      holder 键之一 (受影响的下游档案数)。
    - 摩根士丹利各写法变体逐个列出, 以及它们是否共用同一个 HOLDER_CODE。

    这一项没有"对/错"的判据, 只报数 (``status="INFO"``), 供协调方做身份归并
    决策——不是本脚本的裁决范围。
    """

    code_name_rows = conn.execute(
        f"""
        SELECT DISTINCT HOLDER_CODE, HOLDER_NAME
        FROM {RAW_ROWS_TABLE}
        WHERE HOLDER_CODE IS NOT NULL AND HOLDER_NAME IS NOT NULL
        """
    ).fetchall()
    names_by_code: dict[str, set[str]] = {}
    for code, name in code_name_rows:
        names_by_code.setdefault(code, set()).add(name)
    ambiguous_codes = {code: names for code, names in names_by_code.items() if len(names) > 1}
    ambiguous_names: set[str] = set()
    for names in ambiguous_codes.values():
        ambiguous_names |= names

    if ambiguous_names:
        placeholders = ", ".join(["?"] * len(ambiguous_names))
        params = list(ambiguous_names)
        canon_affected = conn.execute(
            f"""
            SELECT COUNT(DISTINCT holder_name) FROM {PROD_ALIAS}.{CANONICAL_TABLE}
            WHERE holder_name IN ({placeholders})
            """,
            params,
        ).fetchone()[0]
        mart_affected = conn.execute(
            f"""
            SELECT COUNT(DISTINCT holder) FROM {FEAT_ALIAS}.{MART_INST_PROFILE_TABLE}
            WHERE holder IN ({placeholders})
            """,
            params,
        ).fetchone()[0]
    else:
        canon_affected = 0
        mart_affected = 0

    like_clauses = " OR ".join("HOLDER_NAME ILIKE '%' || ? || '%'" for _ in _MORGAN_STANLEY_PATTERNS)
    ms_rows = conn.execute(
        f"""
        SELECT DISTINCT HOLDER_NAME, HOLDER_CODE
        FROM {RAW_ROWS_TABLE}
        WHERE {like_clauses}
        ORDER BY HOLDER_NAME
        """,
        list(_MORGAN_STANLEY_PATTERNS),
    ).fetchall()
    ms_codes_by_name: dict[str, set[Any]] = {}
    for name, code in ms_rows:
        ms_codes_by_name.setdefault(name, set()).add(code)
    all_ms_codes = {c for codes in ms_codes_by_name.values() for c in codes if c is not None}
    ms_same_code = bool(ms_codes_by_name) and len(all_ms_codes) <= 1

    observed = {
        "ambiguous_holder_code_groups": len(ambiguous_codes),
        "canonical_holder_name_variants_collapsing": canon_affected,
        "mart_inst_profile_holders_affected": mart_affected,
        "mart_inst_profile_total_holders": conn.execute(
            f"SELECT COUNT(DISTINCT holder) FROM {FEAT_ALIAS}.{MART_INST_PROFILE_TABLE}"
        ).fetchone()[0],
        "morgan_stanley_variants": [
            {
                "holder_name": name,
                "holder_codes": sorted(c for c in codes if c is not None),
            }
            for name, codes in sorted(ms_codes_by_name.items())
        ],
        "morgan_stanley_same_code": ms_same_code,
    }
    return _result("identity_experiment", "INFO", observed, None)


# ── orchestration ────────────────────────────────────────────────────────────

CHECK_FUNCS: tuple[tuple[str, Any], ...] = (
    ("fetch_completeness", check_fetch_completeness),
    ("derivation_rules", check_derivation_rules),
    ("raw_covers_canonical", check_raw_covers_canonical),
    ("raw_internal_duplicates", check_raw_internal_duplicates),
    ("exit_gap", check_exit_gap),
    ("identity_experiment", check_identity_experiment),
)


def run_all(conn, *, start_period: str = DEFAULT_START_PERIOD) -> list[dict[str, Any]]:
    require_all_read_only(conn)
    return [
        check_fetch_completeness(conn),
        check_derivation_rules(conn),
        check_raw_covers_canonical(conn),
        check_raw_internal_duplicates(conn),
        check_exit_gap(conn, start_period=start_period),
        check_identity_experiment(conn),
    ]


def overall_status(results: Iterable[dict[str, Any]]) -> str:
    return "FAIL" if any(r["status"] == "FAIL" for r in results) else "PASS"


def _print_human(status: str, results: list[dict[str, Any]]) -> None:
    print(f"=== check_holders_staging: {status} ===")
    for r in results:
        print(f"[{r['status']:4s}] {r['name']}")
        print(f"        expected: {r['expected']}")
        observed = r["observed"]
        if isinstance(observed, dict):
            for k, v in observed.items():
                if isinstance(v, list):
                    print(f"        observed.{k}: {len(v)} 项 (前 3: {v[:3]})")
                else:
                    print(f"        observed.{k}: {v}")
        else:
            print(f"        observed: {observed}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staging", required=True, help="staging DuckDB 路径 (raw_fetch/raw_rows)")
    # evidence: 这是 --help 文案不是路径常量; default=None 时实际走 database_manifest 解析
    parser.add_argument("--prod-db", default=None, help="生产 smartmoney.duckdb 路径 (默认走 database_manifest)")
    # evidence: 同上, --help 文案; default=None 走 database_manifest
    parser.add_argument(
        # evidence: --help 文案, default=None 时走 database_manifest 解析
        "--feature-store-db", default=None, help="生产 feature_store.duckdb 路径 (默认走 database_manifest)"
    )
    parser.add_argument("--start-period", default=DEFAULT_START_PERIOD)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    manifest = get_database_manifest()
    prod_db_path = Path(args.prod_db) if args.prod_db else manifest.path_for("smartmoney")
    feat_db_path = Path(args.feature_store_db) if args.feature_store_db else manifest.path_for("feature_store")

    conn = build_connection(Path(args.staging), prod_db_path, feat_db_path)
    try:
        results = run_all(conn, start_period=args.start_period)
    finally:
        conn.close()

    status = overall_status(results)
    if args.json:
        print(json.dumps({"status": status, "checks": results}, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(status, results)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
