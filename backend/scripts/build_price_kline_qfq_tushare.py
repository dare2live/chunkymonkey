"""Build the current TuShare-history qfq (前复权) analysis序列 — 建到新文件后原子换名。

派生 serving/research 面, 非 nominal execution-price truth。每次重建戳
batch_id/ingested_at/factor_as_of/config_hash。

2026-09-08 重写 (raw_tushare_adj_factor 冻结于 20260828, tushare 授权 09-10 到期硬停):
  - 因子改自算 services.adjust_factor.hfq_sql, 不再 JOIN raw_tushare_adj_factor。
  - OHLCV 唯一来源 canonical_nominal_ohlcv_daily; legacy raw_tushare_daily UNION 兜底与
    --allow-legacy-fill 一并删除 (accepted 已覆盖全窗, 兜底不再需要)。
  - 增量路径整段删除 (实测全量 CTAS ~4.6s/840MB, 增量 5 张 temp 表已不划算)。
  - 锚点改为「该股自己最后一根 K 线的 hfq_factor」而非任何全局 max 日期 —— 旧版锚在
    raw_tushare_adj_factor 各自 per-code 最新行, 该表在个别股票退市/停牌后仍被刷出晚于该股
    最后一条 canonical K 线的因子行, 致锚定日错位 (600069.SH 等 6 股末行偏离 nominal 达
    89.7%)。自算因子只依赖 canonical 自身, 每只股"最后一行"天然就是它自己的最后一行, 两表
    覆盖范围不同这个前提被消灭, 而非打补丁绕过。
  - 2026-09-24 (cut_qfq_fresh_file_swap): 同文件 DROP+CTAS+CREATE INDEX 隔日制造
    ~807MB 空洞 (旧块入 free list, 索引块与表块交错落位让文件永久钉在 2×; 详见
    sandbox/churn_fix_20260919/spec_derived_rebuild_churn.md §1.1/E1)。改为建到
    `<live>_build.duckdb`、CTAS 按 (code,date) 聚簇 (`ORDER BY`)、不建索引 (计划器
    从不选它, E3 EXPLAIN 两种点查均 SEQ_SCAN)、cross_check+free_blocks==0 通过后
    经 `services.duckdb_file_swap.swap_in_fresh_file` 原子换名。换名前置三道围栏
    (残留 live.wal / live 指纹在建库期间变了 / live 有活跃写者) 任一不过即拒绝且
    不动生产文件——旧版"cross_check 不过时新表已经上线"的窗口随之消失。

qfq[t] = nominal[t] × hfq_factor[t] / hfq_factor[该股最后一行]; hfq_factor 定义见
services.adjust_factor (baostock"涨跌幅复权法": ratio[t]=close[t-1]/pre_close[t], 首日=1,
hfq_factor=∏ratio)。单位: volume 手×100=股, amount 千元×1000=元 (field_dictionary.yaml)。

缺失传播 (红线3): 某股某日 ratio 不可算 (pre_close/close 缺失或越界) → 该股此后 hfq_factor
全 NULL, 行仍保留、价格列 NULL、ratio_status 说明原因, 不做 latest fallback、不填 0。

--from-accepted / --full 现恒为空操作 flag, 只为兼容 scripts/chunkyctl 帮助文案与
backend/services/derive_runtime.py 现有调用保留, 不再改变输出。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services import market_schema  # noqa: E402
from services.adjust_factor import AdjustFactorConfig, hfq_sql, load_config  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.duckdb_file_swap import (  # noqa: E402
    SwapRefused,
    file_fingerprint,
    swap_in_fresh_file,
)
from services.universe import sql_where_active_a_share  # noqa: E402
from services.writer_lock import WriterLockBusyError, writer_lock  # noqa: E402

MARKET_DB = "data/market.duckdb"  # rule-compliance: ok evidence=回测K线库, 一次性 build 脚本
TUSHARE_DB = str(REPO / "data" / "tushare_raw.duckdb")  # rule-compliance: ok evidence=tushare raw 源库 (ATTACH read-only)
TARGET = "price_kline_qfq_tushare"
SOURCE_RELATION = "tr.canonical_nominal_ohlcv_daily"
# 真相源起点防护: 真实数据 2019-01-02 起, 此处只是防未来上游误回填更早历史时悄悄扩窗。
START_DATE = "2019-01-01"


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _default_batch_id(ingested_at: str) -> str:
    stamp = ingested_at.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
    return f"qfq:{stamp}:self_computed_full"


def _rebase(col: str) -> str:
    """col × 行级 hfq_factor / 该股锚定(自己最后一行)因子; 因子链任一环 unknown 则 NULL。"""
    return (
        f"CASE WHEN h.hfq_factor IS NULL OR lt.latest_factor IS NULL OR lt.latest_factor = 0 "
        f"THEN NULL ELSE {col} * h.hfq_factor / lt.latest_factor END"
    )


def build_select_sql(cfg: AdjustFactorConfig, *, batch_id: str, ingested_at: str) -> str:
    """全量 SELECT: 因子来自 adjust_factor.hfq_sql (自算), OHLCV 来自 canonical。"""
    hfq = hfq_sql(cfg, SOURCE_RELATION)
    bid_sql = _sql_literal(batch_id)
    built_sql = _sql_literal(ingested_at)
    chash_sql = _sql_literal(cfg.config_hash)
    return f"""
    WITH hfq AS (
        {hfq}
    ),
    latest AS (
        SELECT ts_code, hfq_factor AS latest_factor, trade_date AS anchor_date
        FROM hfq
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) = 1
    )
    SELECT
        substr(h.ts_code, 1, 6)              AS code,
        strftime(h.trade_date, '%Y-%m-%d')   AS date,
        {_rebase('c.open')}                  AS open,
        {_rebase('c.high')}                  AS high,
        {_rebase('c.low')}                   AS low,
        {_rebase('h.close')}                 AS close,
        c.vol    * 100.0                     AS volume,
        c.amount * 1000.0                    AS amount,
        h.ratio_status                       AS ratio_status,
        h.hfq_factor                         AS hfq_factor,
        '{bid_sql}'                          AS batch_id,
        CAST('{built_sql}' AS TIMESTAMP)     AS ingested_at,
        strftime(lt.anchor_date, '%Y-%m-%d') AS factor_as_of,
        '{chash_sql}'                        AS config_hash
    FROM hfq h
    JOIN latest lt
      ON lt.ts_code = h.ts_code
    JOIN {SOURCE_RELATION} c
      ON c.ts_code = h.ts_code AND c.trade_date = h.trade_date
    WHERE h.trade_date >= DATE '{START_DATE}'
    """


def build_full(
    conn,
    *,
    cfg: AdjustFactorConfig | None = None,
    batch_id: str | None = None,
    ingested_at: str | None = None,
) -> dict[str, Any]:
    """唯一构建路径: DROP+CTAS, 按 (code,date) 物理聚簇, 不建索引。

    2026-09-24: 删掉 CREATE INDEX (计划器从不选它, 只贡献空洞——spec E3/E1);
    CTAS 加 ORDER BY code,date 换 zone map (单股查询 12ms→2.8ms, 全扫查询更快)。
    conn 现在总是指向全新的 build 文件 (main() 里的 `<live>_build.duckdb`),
    DROP IF EXISTS 在新文件里是空操作, 保留只是为了注入连接的测试路径与语义一致。
    """
    cfg = cfg or load_config()
    built_at = ingested_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bid = batch_id or _default_batch_id(built_at)
    conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
    conn.execute(f"DROP TABLE IF EXISTS {TARGET}")
    conn.execute(
        f"CREATE TABLE {TARGET} AS "
        f"SELECT * FROM ({build_select_sql(cfg, batch_id=bid, ingested_at=built_at)}) q "
        "ORDER BY code, date"
    )
    n = int(conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0])
    return {"rows": n, "batch_id": bid, "ingested_at": built_at, "config_hash": cfg.config_hash}


def cross_check(conn) -> dict[str, Any]:
    """5 条集合级自完整性检查 (2026-09-08 重写 4 条 — 全部比集合/等式, 不比 MAX/COUNT
    门面数字; 2026-09-24 补第 5 条 duplicate_grain_n —— 旧 4 条的 qfq_set 用了
    DISTINCT, 重复行按构造看不见, grain 唯一性此前无人守)。"""
    active_pred = sql_where_active_a_share("ts_code")

    # 1) (code,date) 集合恒等于 canonical A股 >= START_DATE 的集合 —— 比集合不比 COUNT,
    #    同计数、不同成员的退化会被 COUNT 放过, 这里的 EXCEPT 抓得住。
    set_diff = conn.execute(f"""
        WITH canonical_set AS (
            SELECT DISTINCT substr(ts_code, 1, 6) AS code, strftime(trade_date, '%Y-%m-%d') AS date
            FROM {SOURCE_RELATION}
            WHERE trade_date >= DATE '{START_DATE}' AND {active_pred}
        ),
        qfq_set AS (
            SELECT DISTINCT code, date FROM {TARGET}
        )
        SELECT
            (SELECT count(*) FROM (SELECT code, date FROM canonical_set EXCEPT SELECT code, date FROM qfq_set)),
            (SELECT count(*) FROM (SELECT code, date FROM qfq_set EXCEPT SELECT code, date FROM canonical_set))
    """).fetchone()

    # 2) 每股末行 close == 该日 nominal close (锚点定义的直接推论; 容差 1e-9)。
    anchor_mismatch_n = conn.execute(f"""
        WITH qfq_last AS (
            SELECT code, close FROM (
                SELECT code, close, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) rn
                FROM {TARGET}
            ) WHERE rn = 1
        ),
        nominal_last AS (
            SELECT code, close FROM (
                SELECT substr(ts_code, 1, 6) AS code, close,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM {SOURCE_RELATION}
                WHERE {active_pred}
            ) WHERE rn = 1
        )
        SELECT count(*)
        FROM qfq_last q JOIN nominal_last n ON n.code = q.code
        WHERE q.close IS NULL OR n.close IS NULL OR abs(q.close - n.close) > 1e-9
    """).fetchone()[0]

    # 3) hfq_factor 为 NULL 的行数 (今天全市场无此情形; >0 说明因子链断裂扩大, 需查)。
    null_factor_n = conn.execute(f"SELECT count(*) FROM {TARGET} WHERE hfq_factor IS NULL").fetchone()[0]

    # 4) max(date) 对齐真相源。
    dates = conn.execute(f"""
        SELECT (SELECT max(date) FROM {TARGET}),
               (SELECT strftime(max(trade_date), '%Y-%m-%d') FROM {SOURCE_RELATION})
    """).fetchone()

    # 5) grain 唯一性: (code,date) 重复行数 —— 上面 1) 的 qfq_set 用 DISTINCT 收窄过,
    #    对重复行是盲的 (同一 (code,date) 出现两行, DISTINCT 后集合看着仍然相等)。
    duplicate_grain_n = conn.execute(f"""
        SELECT count(*) - count(DISTINCT (code, date)) FROM {TARGET}
    """).fetchone()[0]

    return {
        "set_missing_in_qfq": int(set_diff[0]),
        "set_extra_in_qfq": int(set_diff[1]),
        "anchor_close_mismatch_n": int(anchor_mismatch_n),
        "null_factor_n": int(null_factor_n),
        "qfq_max_date": dates[0],
        "canonical_max_date": dates[1],
        "duplicate_grain_n": int(duplicate_grain_n),
    }


def _cross_check_ok(cc: dict[str, Any]) -> bool:
    return (
        cc["set_missing_in_qfq"] == 0
        and cc["set_extra_in_qfq"] == 0
        and cc["anchor_close_mismatch_n"] == 0
        and cc["null_factor_n"] == 0
        and cc["duplicate_grain_n"] == 0
        and cc["qfq_max_date"] == cc["canonical_max_date"]
    )


def _print_cross_check(cc: dict[str, Any]) -> bool:
    print(
        f"[sanity] set_missing={cc['set_missing_in_qfq']} set_extra={cc['set_extra_in_qfq']} "
        f"anchor_mismatch={cc['anchor_close_mismatch_n']} null_factor={cc['null_factor_n']} "
        f"duplicate_grain={cc['duplicate_grain_n']} "
        f"qfq_max={cc['qfq_max_date']} canonical_max={cc['canonical_max_date']}",
        flush=True,
    )
    ok = _cross_check_ok(cc)
    print(
        f"[verdict] {'PASS 自完整性检查通过' if ok else 'REVIEW 集合/锚点/因子/日期/重复行对不齐, 先查再消费'}"
    )
    return ok


def _copy_secondary_tables(conn) -> None:
    """从只读 ATTACH 的 prev (=旧 live) 拷贝除 TARGET 外的每张 BASE TABLE。

    原 DDL (含 PK/约束, 如 dim_schema_version 的主键) + 原样行 + 原索引; 不用
    CTAS (会丢约束, 同 db_compact.py 的既有教训)。TARGET 自己的旧索引 (已删的
    idx_{TARGET}_cd) 天然被 `table_name != TARGET` 排除, 不会被拷回来。
    """
    tables = conn.execute(
        "SELECT table_name, sql FROM duckdb_tables() "
        "WHERE database_name = 'prev' AND table_name != ? ORDER BY table_name",
        [TARGET],
    ).fetchall()
    for tname, tsql in tables:
        if not tsql:
            raise RuntimeError(f"prev.{tname} 无原 DDL (sql=NULL), 需要手动处理, 不猜测建表")
        conn.execute(tsql)
        conn.execute(f'INSERT INTO "{tname}" SELECT * FROM prev."{tname}"')
        for (isql,) in conn.execute(
            "SELECT sql FROM duckdb_indexes() WHERE database_name = 'prev' "
            "AND table_name = ? AND sql IS NOT NULL",
            [tname],
        ).fetchall():
            conn.execute(isql)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true", help="只对账不重建 (只读, 不取写锁)")
    ap.add_argument(
        "--full",
        action="store_true",
        help="兼容旧 CLI — 全量 DROP+CTAS 现在是唯一行为, 本 flag 不改变任何输出",
    )
    ap.add_argument(
        "--from-accepted",
        action="store_true",
        help="兼容旧 CLI — OHLCV 现恒等于 accepted canonical_nominal_ohlcv_daily, 本 flag 不改变任何输出",
    )
    args = ap.parse_args(argv)

    live = Path(MARKET_DB)
    build = live.with_name(live.stem + "_build.duckdb")  # rule-compliance: ok evidence=从 manifest 路径派生, 同 db_compact.py:49-50
    build_wal = build.with_name(build.name + ".wal")

    if args.check_only:
        conn = connect(str(live), read_only=True)
        try:
            conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
            cc = cross_check(conn)
        finally:
            conn.close()
        return 0 if _print_cross_check(cc) else 2

    def _discard_build() -> None:
        if build_wal.exists():
            build_wal.unlink()
        if build.exists():
            build.unlink()

    try:
        with writer_lock("build_price_kline_qfq_tushare"):
            # 残留 build/build.wal (上次崩溃产物) 先删——它们只属于本脚本, 谁跑谁清。
            # 必须在拿到写锁之后才清: 清理前若还没排到队, 可能删掉另一个正持锁写者
            # 尚未关闭连接的在建 build 文件 (竞态——两个进程都跑本脚本时, 后来者会在
            # 前者还没写完时删掉它的 build, 导致前者最后 swap 时误判 build_not_closed)。
            _discard_build()
            # expected 必须在打开 build 连接、ATTACH prev 之前记 (M1); 拿到写锁之后
            # 才读, 把"读 live 指纹"与"没人能再抢到写窗口"钉在同一时刻, 收紧竞态窗口。
            expected = file_fingerprint(live) if live.exists() else None
            conn = connect(str(build), read_only=False)
            try:
                conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
                detail = build_full(conn)
                print(
                    f"[build] {TARGET}: {detail['rows']:,} 行 | batch_id={detail['batch_id']} | "
                    f"config_hash={detail['config_hash'][:12]}…",
                    flush=True,
                )
                conn.executescript(market_schema.ANALYSIS_KLINE_QFQ_VIEW_DDL)
                if live.exists():
                    conn.execute(f"ATTACH '{live}' AS prev (READ_ONLY)")
                    try:
                        _copy_secondary_tables(conn)
                    finally:
                        conn.execute("DETACH prev")
                cc = cross_check(conn)
                conn.execute("CHECKPOINT")
                fb_row = conn.execute(
                    "SELECT free_blocks FROM pragma_database_size() WHERE database_name = ?",
                    [build.stem],
                ).fetchone()
                free_blocks = int(fb_row[0]) if fb_row else None
            finally:
                conn.close()

            ok = _print_cross_check(cc)
            if not ok or free_blocks != 0:
                print(f"[swap] SKIPPED free_blocks={free_blocks}, 生产文件未动", flush=True)
                _discard_build()
                return 2

            try:
                swap_in_fresh_file(build, live, expected=expected)
            except SwapRefused as exc:
                print(
                    f"[swap] REFUSED reason={exc.reason} detail={exc.detail}, 生产文件未动",
                    file=sys.stderr,
                )
                _discard_build()
                return 3
    except WriterLockBusyError as exc:
        print(f"[build_price_kline_qfq_tushare] LOCK_BUSY: {exc}", file=sys.stderr)
        return 4

    print(f"[swap] PASS {live} 已原子换名", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
