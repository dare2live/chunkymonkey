"""S6 pulse serve-read — drill/members/margin L0 leaf via DataAccess boundary.

Router must stay free of inline ``raw_*`` / ATTACH. This module owns the typed
display leaf for membership + vendor flow (million-row; not mart-mirrored) and
B-ext margin shadow probes. Connections go through ``data_access.resolver``;
table identity comes from ``data_access.yaml`` entities where registered.

Form overlay remains ``resolve_tier12_production_read`` (accepted cutover /
legacy fact_stock_form_daily). Fail closed on attach/lock — never fabricate rows.
"""
from __future__ import annotations

from typing import Any

from services import market_pulse as mp
from services.data_access import resolver
from services.data_access.spec import load_registry
from services.duck_adapter import connect as duck_connect
from services.market_pulse_tier12_read import overlay_pulse_form_from_production_read
from services.tier12_consumer_cutover import resolve_tier12_production_read

# Test hooks: production keeps default artifact/config paths.
_TIER12_ARTIFACT_ROOT = None
_TIER12_CUTOVER_CONFIG = None
_TIER12_CONFIG_PATH = None

_REG = None


def _reg():
    global _REG
    if _REG is None:
        _REG = load_registry()
    return _REG


def _table(entity: str) -> str:
    """Physical table for a registered SERVE entity."""
    return _reg().entity(entity).table


def _tr(entity: str) -> str:
    """Qualified table: ``tr.<raw>`` on attach, bare smartmoney publication."""
    ent = _reg().entity(entity)
    if ent.db == "tushare_raw":
        return f"tr.{ent.table}"
    if ent.db == "smartmoney":
        return ent.table
    raise ValueError(
        f"unsupported data_access db for serve SQL: {ent.db!r} (entity={entity})"
    )


def open_members_conn():
    """成分下钻: smartmoney 主库 (B1 fact_dc_member_daily) + READ_ONLY ATTACH tushare_raw AS tr.

    DC membership reads the observation-date publication on smartmoney; SW
    index_member_all still resolves on the tr attach. sync 写锁窗口内 ATTACH
    拒连 → 前端失败态重试, 不降级伪造。
    """
    con = duck_connect(resolver.db_path("smartmoney"), read_only=True)
    try:
        raw_path = resolver.db_path("tushare_raw").replace("'", "''")
        con.execute(f"ATTACH IF NOT EXISTS '{raw_path}' AS tr (READ_ONLY)")
        yield con
    finally:
        con.close()


def open_drill_conn():
    """下钻: smartmoney 主库 + READ_ONLY ATTACH tushare_raw AS tr."""
    con = duck_connect(resolver.db_path("smartmoney"), read_only=True)
    try:
        raw_path = resolver.db_path("tushare_raw").replace("'", "''")
        con.execute(f"ATTACH IF NOT EXISTS '{raw_path}' AS tr (READ_ONLY)")
        yield con
    finally:
        con.close()


def load_margin_rows_for_shadow(conn, day: str) -> tuple[list[dict[str, Any]], str | None]:
    """Return (rows, issue). issue set when margin L0 cannot be read."""
    margin = _tr("margin")

    def _query() -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT exchange_id, rzrqye
            FROM {margin}
            WHERE trade_date = ?
            """,
            [day],
        ).fetchall()
        return [{"exchange_id": r[0], "rzrqye": r[1]} for r in rows]

    try:
        return _query(), None
    except Exception:  # noqa: BLE001 — missing tr schema expected on pulse-only conn
        pass  # rule-compliance: ok evidence=B-ext shadow fail-closed probe
    try:
        raw_path = resolver.db_path("tushare_raw").replace("'", "''")
        conn.execute(f"ATTACH IF NOT EXISTS '{raw_path}' AS tr (READ_ONLY)")
        return _query(), None
    except Exception:  # noqa: BLE001 — attach/lock/missing DB must not invent shadow READY
        # rule-compliance: ok evidence=B-ext shadow fail-closed attach
        return [], "margin_raw_not_attached"


def load_accepted_margin_rows_for_shadow(
    conn, day: str, *, contract_version: str = "3"
) -> tuple[list[dict[str, Any]], str | None]:
    """Return accepted canonical SSE+SZSE margin rows for promote-gate shadow.

    Prefers ``contract_version`` for the day; if that generation lacks both
    SSE+SZSE, falls back to the highest other generation that has both (so
    history is rebuild-safe). Never includes BSE. Fail-closed: missing
    attach/table/day → empty + issue.
    """
    day_norm = str(day or "").replace("-", "")
    if len(day_norm) != 8 or not day_norm.isdigit():
        return [], "invalid_trade_date"
    iso = f"{day_norm[:4]}-{day_norm[4:6]}-{day_norm[6:8]}"
    preferred = str(contract_version)

    def _query() -> list[dict[str, Any]]:
        pick = conn.execute(
            """
            WITH core AS (
                SELECT CAST(contract_version AS VARCHAR) AS cv,
                       UPPER(CAST(exchange_id AS VARCHAR)) AS exchange_id,
                       rzrqye
                FROM tr.canonical_margin_exchange_daily
                WHERE CAST(trade_date AS VARCHAR) IN (?, ?)
                  AND UPPER(CAST(exchange_id AS VARCHAR)) IN ('SSE', 'SZSE')
            ),
            day_cv AS (
                SELECT cv FROM core
                GROUP BY cv
                HAVING COUNT(DISTINCT exchange_id) >= 2
            ),
            pick AS (
                SELECT COALESCE(
                    MAX(CASE WHEN cv = ? THEN cv END),
                    MAX(cv)
                ) AS cv
                FROM day_cv
            )
            SELECT c.exchange_id, c.rzrqye
            FROM core c
            JOIN pick p ON p.cv = c.cv
            ORDER BY c.exchange_id
            """,
            [day_norm, iso, preferred],
        ).fetchall()
        return [{"exchange_id": str(r[0]).upper(), "rzrqye": r[1]} for r in pick]

    try:
        return _query(), None
    except Exception:  # noqa: BLE001 — pulse-only conn may lack tr
        pass
    try:
        raw_path = resolver.db_path("tushare_raw").replace("'", "''")
        conn.execute(f"ATTACH IF NOT EXISTS '{raw_path}' AS tr (READ_ONLY)")
        return _query(), None
    except Exception:  # noqa: BLE001 — fail closed
        return [], "accepted_margin_not_attached"


_LEAF_COLS = [
    "ts_code",
    "stock_code",
    "name",
    "trade_date",
    "net_amount",
    "cum_net",
    "flow_z",
    "flow_streak",
    "flow_regime",
    "form_name",
    "is_breakout_event",
    "limit_times",
    "pct_change",
]


def drill_leaf_rows(
    conn,
    cfg: dict[str, Any],
    mem_sql: str,
    mem_params: list[Any],
    flow_sql: str,
    as_of: str,
) -> list[dict[str, Any]]:
    """成分股叶子: flow annotate + form production-read + limit_list L0."""
    ann = mp._flow_annotate_sql(cfg, flow_sql, "ts_code")
    lt = mp._clean_num("lim.limit_times")
    day = "".join(ch for ch in str(as_of) if ch.isdigit())[:8]
    use_legacy_form = True
    if len(day) == 8 and day != "99999999":
        read = resolve_tier12_production_read(
            day,
            config=_TIER12_CUTOVER_CONFIG,
            artifact_root=_TIER12_ARTIFACT_ROOT,
            config_path=_TIER12_CONFIG_PATH,
        )
        use_legacy_form = bool(read.uses_legacy)
    if use_legacy_form:
        form_cte = """
        form AS (
            SELECT stock_code, form_name, is_breakout_event FROM fact_stock_form_daily
            WHERE stock_code IN (SELECT substr(ts_code, 1, 6) FROM mem) AND trade_date <= ?
            QUALIFY ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) = 1
        )"""
        form_select = "f.form_name, f.is_breakout_event"
        form_join = "LEFT JOIN form f ON f.stock_code = substr(m.ts_code, 1, 6)"
        sql_params = [*mem_params, as_of]
    else:
        form_cte = (
            "form AS (SELECT NULL::VARCHAR AS stock_code, NULL::VARCHAR AS form_name, "
            "NULL::BOOLEAN AS is_breakout_event WHERE FALSE)"
        )
        form_select = (
            "CAST(NULL AS VARCHAR) AS form_name, CAST(NULL AS BOOLEAN) AS is_breakout_event"
        )
        form_join = ""
        sql_params = list(mem_params)
    limit_tbl = _tr("limit_list_d")
    rows = conn.execute(
        f"""
        WITH mem AS ({mem_sql}),
        latest AS (
            SELECT * FROM ({ann})
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) = 1
        ),
        {form_cte}
        SELECT m.ts_code, substr(m.ts_code, 1, 6) AS stock_code, m.name,
               l.trade_date, l.net_amount, l._cum_net AS cum_net,
               l.flow_z, l.flow_streak, l.flow_regime,
               {form_select},
               TRY_CAST({lt} AS INTEGER) AS limit_times,
               l.pct_change
        FROM mem m
        LEFT JOIN latest l ON l.ts_code = m.ts_code
        {form_join}
        LEFT JOIN {limit_tbl} lim
          ON lim.ts_code = m.ts_code AND lim.trade_date = l.trade_date AND lim."limit" = 'U'
        ORDER BY (l._cum_net IS NULL), l._cum_net DESC, m.ts_code""",
        sql_params,
    ).fetchall()
    rows = [dict(zip(_LEAF_COLS, r)) for r in rows]
    if len(day) == 8 and day != "99999999" and not use_legacy_form:
        rows, _ = overlay_pulse_form_from_production_read(
            rows,
            day,
            config=_TIER12_CUTOVER_CONFIG,
            artifact_root=_TIER12_ARTIFACT_ROOT,
            config_path=_TIER12_CONFIG_PATH,
        )
    return rows


def sw_code_level(conn, code: str) -> tuple[str | None, dict[str, Any]]:
    """判申万码层级 + 取血缘名 (index_member_all 一行足够)。"""
    tbl = _tr("index_member_all")
    for lvl, col in (("L1", "l1_code"), ("L2", "l2_code"), ("L3", "l3_code")):
        r = conn.execute(
            f"""
            SELECT l1_code, l1_name, l2_code, l2_name, l3_code, l3_name
            FROM {tbl} WHERE {col} = ? LIMIT 1""",
            [code],
        ).fetchone()
        if r:
            return lvl, dict(
                zip(
                    ["l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name"],
                    r,
                )
            )
    return None, {}


def sw_child_codes(conn, code: str, lvl: str) -> list[tuple[str, str]]:
    """L1→L2 or L2→L3 child codes from index_member_all."""
    tbl = _tr("index_member_all")
    cc, cn, parent = (
        ("l2_code", "l2_name", "l1_code")
        if lvl == "L1"
        else ("l3_code", "l3_name", "l2_code")
    )
    return conn.execute(
        f"""
        SELECT {cc} AS code, MAX({cn}) AS name FROM {tbl}
        WHERE {parent} = ? AND {cc} IS NOT NULL
        GROUP BY 1 ORDER BY 1""",
        [code],
    ).fetchall()


def dc_member_snap(conn, sector_code: str, as_of: str) -> str | None:
    tbl = _tr("dc_member")
    row = conn.execute(
        f"SELECT MAX(trade_date) FROM {tbl} WHERE ts_code = ? AND trade_date <= ?",
        [sector_code, as_of],
    ).fetchone()
    return row[0] if row else None


def dc_member_mem_sql() -> str:
    from services.universe import sql_where_active_a_share

    tbl = _tr("dc_member")
    return (
        f"SELECT con_code AS ts_code, MAX(name) AS name FROM {tbl} "
        f"WHERE ts_code = ? AND trade_date = ? AND {sql_where_active_a_share('con_code')} "
        "GROUP BY 1"
    )


def dc_flow_sql(as_of: str) -> str:
    tbl = _tr("moneyflow_dc")
    return f"""
        SELECT f.ts_code, f.trade_date,
               TRY_CAST(f.net_amount AS DOUBLE) * 10000.0 AS net_amount,
               TRY_CAST(f.pct_change AS DOUBLE) AS pct_change
        FROM {tbl} f
        WHERE f.ts_code IN (SELECT ts_code FROM mem)
          AND f.trade_date <= {mp._sql_str(as_of)}"""


def sw_member_mem_sql() -> str:
    from services.universe import sql_where_active_a_share

    tbl = _tr("index_member_all")
    return (
        f"SELECT ts_code, MAX(name) AS name FROM {tbl} "
        f"WHERE l3_code = ? AND in_date <= ? "
        f"AND (out_date IS NULL OR CAST(out_date AS VARCHAR) > ?) "
        f"AND {sql_where_active_a_share('ts_code')} GROUP BY 1"
    )


def sw_l1_member_mem_sql() -> str:
    """L1 board → member stocks via PIT view ``l1_code`` (no L3 fan-out).

    Cap 4D residual: SW sector board is L1; leaf membership historically keyed
    off ``l3_code``. The PIT publication already carries ``l1_code`` on every
    member row, so L1→stock rollup is a direct as-of filter — not a new
    aggregation taxonomy.
    """
    from services.universe import sql_where_active_a_share

    tbl = _tr("index_member_all")
    return (
        f"SELECT ts_code, MAX(name) AS name FROM {tbl} "
        f"WHERE l1_code = ? AND in_date <= ? "
        f"AND (out_date IS NULL OR CAST(out_date AS VARCHAR) > ?) "
        f"AND {sql_where_active_a_share('ts_code')} GROUP BY 1"
    )


def sw_flow_sql(as_of: str) -> str:
    mf = _tr("moneyflow")
    daily = _tr("daily")
    return f"""
        SELECT f.ts_code, f.trade_date,
               TRY_CAST(f.net_mf_amount AS DOUBLE) * 10000.0 AS net_amount,
               TRY_CAST(d.pct_chg AS DOUBLE) AS pct_change
        FROM {mf} f
        LEFT JOIN {daily} d
          ON d.ts_code = f.ts_code AND d.trade_date = f.trade_date
        WHERE f.ts_code IN (SELECT ts_code FROM mem)
          AND f.trade_date <= {mp._sql_str(as_of)}"""


def list_sector_members(conn, *, chain: str, sector_code: str) -> dict[str, Any]:
    """板块成分下钻 payload (dc 最新快照 / sw is_new 当前成分)。"""
    from services.universe import sql_where_active_a_share

    if chain in mp.DC_CHAINS:
        dc = _tr("dc_member")
        as_of_row = conn.execute(
            f"SELECT MAX(trade_date) FROM {dc} WHERE ts_code = ?",
            [sector_code],
        ).fetchone()
        as_of = as_of_row[0] if as_of_row else None
        if as_of is None:
            return {
                "status": "ok",
                "chain": chain,
                "sector_code": sector_code,
                "as_of": None,
                "members": [],
            }
        rows = conn.execute(
            f"""
            SELECT con_code, name FROM {dc}
            WHERE ts_code = ? AND trade_date = ?
              AND {sql_where_active_a_share("con_code")}
            ORDER BY con_code""",
            [sector_code, as_of],
        ).fetchall()
        return {
            "status": "ok",
            "chain": chain,
            "sector_code": sector_code,
            "as_of": as_of,
            "members": [{"con_code": r[0], "name": r[1]} for r in rows],
        }
    sw = _tr("index_member_all")
    rows = conn.execute(
        f"""
        SELECT DISTINCT ts_code, name FROM {sw}
        WHERE l1_code = ? AND is_new = 'Y'
          AND {sql_where_active_a_share("ts_code")}
        ORDER BY ts_code""",
        [sector_code],
    ).fetchall()
    return {
        "status": "ok",
        "chain": chain,
        "sector_code": sector_code,
        "as_of": None,
        "members": [{"con_code": r[0], "name": r[1]} for r in rows],
    }


__all__ = [
    "open_members_conn",
    "open_drill_conn",
    "load_margin_rows_for_shadow",
    "load_accepted_margin_rows_for_shadow",
    "drill_leaf_rows",
    "sw_code_level",
    "sw_child_codes",
    "dc_member_snap",
    "dc_member_mem_sql",
    "dc_flow_sql",
    "sw_member_mem_sql",
    "sw_l1_member_mem_sql",
    "sw_flow_sql",
    "list_sector_members",
]
