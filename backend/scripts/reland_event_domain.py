#!/usr/bin/env python3
"""事件域历史重落: 归档 + 记账 + DDL (``prepare``) / 逐日校验 (``verify``)。

grain 契约 r2 §3 (施工切片 S7)。业主对 r2 字面规格的三点裁定, 本文件按裁定实现 (与
r2 字面文本的出入见各函数头注):

  A. ``prepare`` 不建 ``mart_data_landing_dedup`` —— 该表由
     ``sync_runner._write_batch`` 在首次 artifact 写入时建 (单一写者/单一 DDL); 这里
     再建一份就成了第二个真相源。``prepare`` 只做: 归档、record_data_deletion 记账、
     给已存在表加列 (幂等)。
  B/C. ``recon_assignment_gaps.py`` 的 top_inst / block_trade 段改法见
     ``services.data_sources.assignment_gap_recon`` 与该脚本自己的头注 (不在本文件)。
  D. ``canon_exchange_row`` 的三条格式规则 (千分位逗号 / 万股两位 / 全角括号) 是外部
     交易所页面的表示事实, 写成本文件内的常量/函数, 不进 YAML —— 红线 11 管的是
     "检查什么、比较什么" 的判断规则, 不管"对方页面怎么写数字"这种格式事实, 与
     ``recon_compare._canon`` 里 date/Decimal 归一同一性质。
  E. 默认 dry-run; ``--execute``/``--record`` 才写; 写库前取现成的
     ``services.writer_lock.writer_lock``; 删除/替换记账用现成的
     ``services.data_deletion.record_data_deletion``, 不手写 INSERT
     ``mart_data_deletion_record``。
  F. 本次任务不在真实数据上跑 ``prepare``/``verify`` (worktree 里也没有 data/); 只交
     代码与测试, 用内存 DuckDB / tmp_path 验证。

用法:
  python backend/scripts/reland_event_domain.py prepare --domain block_trade --run-id r1
  python backend/scripts/reland_event_domain.py prepare --domain block_trade --run-id r1 --execute
  python backend/scripts/reland_event_domain.py verify  --domain block_trade --run-id r1
  python backend/scripts/reland_event_domain.py verify  --domain block_trade --run-id r1 \\
      --exchange-json data/scratch/exch_20230103.json --date 20230103 --record
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_deletion import record_data_deletion  # noqa: E402
from services.data_sources.assignment_gap_recon import normalize_cn_name  # noqa: E402
from services.data_sources.recon_compare import compare_rows  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.writer_lock import writer_lock  # noqa: E402

ARCHIVE_DIR = ROOT / "data" / "archive" / "lifecycle"  # gitignored (r2 §3.2), 同
                                                        # db_lifecycle_delete 的归档目录

DOMAIN_TABLES: dict[str, str] = {
    "block_trade": "raw_tushare_block_trade",
    "top_inst": "raw_tushare_top_inst",
}
# §3.2 步骤 3 (业主裁定 A): prepare 只加列, 已存在则跳过 (幂等)。类型显式给
# INTEGER/VARCHAR —— 若靠 sync_runner._write_batch 的新列推断会一律建成 VARCHAR
# (r2 §0.5 实测: sync_runner.py:1760), 必须一次性 DDL 建对, 不能等 runner 自己补。
DOMAIN_DDL_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "block_trade": (("seq", "INTEGER"), ("security_type", "VARCHAR"), ("trade_unit", "VARCHAR")),
    "top_inst": (("board_rank", "INTEGER"), ("stat_days", "VARCHAR"), ("seat_code", "VARCHAR")),
}
# 该列为 NULL = 这一天还没重落 (旧契约行还在)。block_trade 用它声明的
# multiplicity_index(seq); top_inst 用交易所自然键最后一列 board_rank —— 两者在这里
# 扮演同一个角色: "重落完成" 的机器可判标记, 与 sync_registry 是否声明
# multiplicity_index 无关 (top_inst 没有声明, kind='none')。
DOMAIN_INDEX_COL: dict[str, str] = {"block_trade": "seq", "top_inst": "board_rank"}
# 旧 ⊆ 新 (§3.4.1) 比较键: 排除该列本身天然不该纳入的轴——
#   block_trade 排除 seq: 它是落地层派生的到达顺序, 不是供应商事实, 与
#     recon_compare 从 compare_key 剔除 multiplicity_index 同一处理原则。vol 纳入
#     比较键, 但比较前两侧都先过 _vol_2dp: 旧行只有百股精度 (13.15), 新行是妙想
#     精确股数/1e4 (13.1476) —— 不做这一步会把纯精度差异误报成"缺失"。
#   top_inst 排除 board_rank (这次重落才新增的列, 旧归档行必然 NULL, 纳入会让
#     检查对所有已重落的日子恒报"缺失", 不是发现真缺陷) 与 reason (2026-09-11
#     主会话复核: 历史旧行的 reason 存在按具体数值写的措辞, 与新行的规范化榜单
#     理由字符串不是同一份文本, 纳入会把纯措辞差异当成缺失)。剩 (trade_date,
#     ts_code, exalter, side) 四列: 同一席位可以合法地同时上多个榜, 新计数
#     ≥ 旧计数即算覆盖, 不要求逐榜逐理由对应。
DOMAIN_OLD_SUBSET_KEY: dict[str, tuple[str, ...]] = {
    "block_trade": ("ts_code", "trade_date", "price", "vol", "buyer", "seller"),
    "top_inst": ("trade_date", "ts_code", "exalter", "side"),
}


def _domain_table(domain: str) -> str:
    try:
        return DOMAIN_TABLES[domain]
    except KeyError:
        raise ValueError(
            f"unknown domain {domain!r}; expected one of {sorted(DOMAIN_TABLES)}"
        ) from None


def _archive_path(table: str, run_id: str, *, archive_dir: Path | None = None) -> Path:
    return (archive_dir or ARCHIVE_DIR) / f"{table}_pre_reland_{run_id}.parquet"


# --------------------------------------------------------------------- archive --

@dataclass(frozen=True)
class ArchiveResult:
    rows: int
    sha256: str
    path: Path


def archive_table(conn: Any, table: str, out_path: Path) -> ArchiveResult:
    """COPY 整表到 parquet, 幂等 (§3.2 步骤 1)。

    ``out_path`` 已存在时不重新写, 只重新核对: 当前表行数与已归档的 parquet 行数
    必须相等, 否则抛。这既让重复调用 (同 run_id 的 prepare 被打断后重跑) 不用重复
    拷一遍全表, 也让"归档文件是另一次跑落下的陈旧快照, 与当前表状态对不上"这种
    情况报错而不是被静默当成"已经归档过了" —— 红线 1: 历史消失了都不知道, 比抛异常
    更糟。
    """
    table_rows = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        conn.execute(
            f"COPY (SELECT * FROM \"{table}\") TO '{out_path.as_posix()}' (FORMAT PARQUET)"
        )
    archived_rows = int(
        conn.execute(
            f"SELECT COUNT(*) FROM read_parquet('{out_path.as_posix()}')"
        ).fetchone()[0]
    )
    if archived_rows != table_rows:
        raise ValueError(
            f"archive row mismatch for {table}: table={table_rows} "
            f"archive={archived_rows} path={out_path}"
        )
    sha256 = hashlib.sha256(out_path.read_bytes()).hexdigest()
    return ArchiveResult(rows=archived_rows, sha256=sha256, path=out_path)


# ---------------------------------------------------------------------- prepare --

def _column_exists(conn: Any, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=? LIMIT 1",
        [table, column],
    ).fetchone()
    return row is not None


def _add_column_if_missing(conn: Any, table: str, column: str, coltype: str) -> None:
    if _column_exists(conn, table, column):
        return
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {coltype}')


def _per_year_counts(conn: Any, table: str, *, date_col: str = "trade_date") -> dict[str, int]:
    rows = conn.execute(
        f'SELECT substr(CAST("{date_col}" AS VARCHAR), 1, 4) AS yr, COUNT(*) AS n '
        f'FROM "{table}" WHERE "{date_col}" IS NOT NULL GROUP BY 1 ORDER BY 1'
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows if r[0]}


def prepare(
    domain: str,
    *,
    run_id: str,
    execute: bool,
    conn: Any = None,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """§3.2: 按域执行 归档 → record_data_deletion 记账 → DDL。

    dry-run (``execute=False``, 默认) 只报计划, 不连库不写。``execute=True`` 时
    取 ``writer_lock("reland_event_domain")`` 之后才动库; 记账 + DDL 在同一事务内
    (归档本身在事务外, 与 r2 §3.2 原文一致)。

    ``conn``/``archive_dir`` 不在 r2 §4 S7 的字面签名里 —— 加它们是为了让测试用
    内存 DuckDB / tmp_path 跑 (本任务规则 3), 不连生产库、不落到仓库的
    data/archive/lifecycle/。两者省略时退回生产路径 (``db_path('tushare_raw')`` /
    ``ARCHIVE_DIR``), 与 CLI 用法一致。
    """
    table = _domain_table(domain)
    ddl_columns = DOMAIN_DDL_COLUMNS[domain]
    plan: dict[str, Any] = {"domain": domain, "table": table, "run_id": run_id, "execute": execute}
    if not execute:
        plan["dry_run"] = True
        plan["would_add_columns"] = [f"{c} {t}" for c, t in ddl_columns]
        plan["would_archive_to"] = str(_archive_path(table, run_id, archive_dir=archive_dir))
        return plan

    owns_conn = conn is None
    with writer_lock("reland_event_domain"):
        if owns_conn:
            conn = connect(str(db_path("tushare_raw")), read_only=False)
        try:
            out_path = _archive_path(table, run_id, archive_dir=archive_dir)
            archive = archive_table(conn, table, out_path)
            bounds = conn.execute(
                f'SELECT MIN(CAST(trade_date AS VARCHAR)), MAX(CAST(trade_date AS VARCHAR)), '
                f'COUNT(DISTINCT CAST(trade_date AS VARCHAR)) FROM "{table}"'
            ).fetchone()
            key_value = (
                f"{bounds[0]}..{bounds[1]}" if bounds and bounds[0] is not None else ""
            )
            per_year = _per_year_counts(conn, table)

            conn.execute("BEGIN TRANSACTION")
            try:
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name=table,
                    delete_scope="rows_replaced_by_partition_reland",
                    key_column="trade_date",
                    key_value=key_value,
                    deleted_rows=archive.rows,
                    reason=(
                        "grain 契约: 事件域按妙想整日重落; 旧行 = 07-04 eef8da75 原地清理 + "
                        "批内去重后的去重子集 (本地=交易所六键去重键数, 少计 12-29%); "
                        f"归档 {archive.path}"
                    ),
                    verification={
                        "old_rows": archive.rows,
                        "old_dates": int(bounds[2]) if bounds and bounds[2] is not None else 0,
                        "archive_sha256": archive.sha256,
                        "per_year": per_year,
                    },
                )
                for col, coltype in ddl_columns:
                    _add_column_if_missing(conn, table, col, coltype)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            if owns_conn:
                conn.close()

    plan["archive"] = {"rows": archive.rows, "sha256": archive.sha256, "path": str(archive.path)}
    plan["added_columns"] = [f"{c} {t}" for c, t in ddl_columns]
    return plan


# ----------------------------------------------------------------- old subset --

@dataclass(frozen=True)
class SubsetReport:
    ok: bool
    missing_keys: list[Any]
    short_counts: list[Any]
    old_total: int
    new_total: int


def _row_key(row: Mapping[str, Any], key_cols: Sequence[str]) -> Any:
    if len(key_cols) == 1:
        return row.get(key_cols[0])
    return tuple(row.get(c) for c in key_cols)


def _apply_vol_2dp(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """旧⊆新比较前把两侧的 ``vol`` 都归到百股精度 (:func:`_vol_2dp`)。

    旧行 (block_trade 旧契约) 只落到百股精度 (13.15); 新行是妙想精确股数/1e4
    (13.1476)。不做这一步, `old_subset_of_new` 会把这条纯精度差异当成"新表里
    找不到这个旧 key"报出来。对没有 ``vol`` 列的行 (如 top_inst) 是无操作。
    """
    out = []
    for row in rows:
        r = dict(row)
        if "vol" in r and r["vol"] is not None:
            r["vol"] = _vol_2dp(r["vol"])
        out.append(r)
    return out


def old_subset_of_new(
    old_rows: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    key_cols: Sequence[str],
) -> SubsetReport:
    """旧 ⊆ 新 (§3.4.1): 旧的每个 key 在新里必须存在, 且 count 不少于旧。

    只报事实 (missing_keys / short_counts), 不自动处置 —— "多余的新行是不是缺陷"
    由调用方逐日裁决 (旧供应商也可能本来就错, r2 §3.4.1 原文)。
    """
    old_counter: Counter = Counter(_row_key(r, key_cols) for r in old_rows)
    new_counter: Counter = Counter(_row_key(r, key_cols) for r in new_rows)
    missing_keys: list[Any] = []
    short_counts: list[Any] = []
    for key, old_n in old_counter.items():
        new_n = new_counter.get(key, 0)
        if new_n == 0:
            missing_keys.append(key)
        elif new_n < old_n:
            short_counts.append(key)
    missing_keys.sort(key=repr)
    short_counts.sort(key=repr)
    return SubsetReport(
        ok=not missing_keys and not short_counts,
        missing_keys=missing_keys,
        short_counts=short_counts,
        old_total=sum(old_counter.values()),
        new_total=sum(new_counter.values()),
    )


# -------------------------------------------------------------- null-index gaps --

def null_index_dates(
    conn: Any, table: str, index_col: str, *, date_col: str = "trade_date"
) -> list[str]:
    """§3.4.2: 该列为 NULL 的日期 = 未重落 (旧契约行还在这些日子里)。"""
    rows = conn.execute(
        f'SELECT DISTINCT CAST("{date_col}" AS VARCHAR) FROM "{table}" '
        f'WHERE "{index_col}" IS NULL ORDER BY 1'
    ).fetchall()
    return [r[0] for r in rows]


# --------------------------------------------------------------- exchange canon --

# 千分位逗号 / 万股两位 (百股精度) / 全角括号 —— 三条都是外部交易所网页的表示事实
# (主会话实测, r2 §0.4), 不是本项目的判断规则, 故不进 YAML (红线 11 管"检查什么"，
# 这是"对方页面怎么写数字/名字")。
#
# 字段名实测来源 (主会话 2026-09-11):
#   上交所逐笔 JSONP (sqlId COMMON_SSE_XXPL_JYXXPL_DZJYXX_L_1): stockid (代码,
#     不带后缀) / tradeprice / tradeqty (万股, 最多 2 位小数) / tradeamount /
#     branchbuy / branchsell / tradedate。
#   深交所协议交易逐笔 (CATALOGID=1265): zqdh (代码, 不带后缀) / cjjg (价格) /
#     cjgsnew (万股, 带千分位逗号, 如 "3,060.00") / cjjenew (万元, 带千分位逗号) /
#     bxwmc (买方) / sxwmc (卖方) / cjrq。
#   两所代码都不带交易所后缀 ("603279"/"002969"), 本地/妙想是 "603279.SH"/
#     "002969.SZ" —— canon 时按 market 补后缀, 已带后缀 (导入/回放场景) 不重复加。
_EXCHANGE_FIELDS: dict[str, dict[str, str]] = {
    "sh": {
        "ts_code": "stockid",
        "price": "tradeprice",
        "vol": "tradeqty",
        "amount": "tradeamount",
        "buyer": "branchbuy",
        "seller": "branchsell",
    },
    "sz": {
        "ts_code": "zqdh",
        "price": "cjjg",
        "vol": "cjgsnew",
        "amount": "cjjenew",
        "buyer": "bxwmc",
        "seller": "sxwmc",
    },
}
_EXCHANGE_SUFFIX: dict[str, str] = {"sh": ".SH", "sz": ".SZ"}
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _clean_text(value: Any) -> str | None:
    """字符串字段先去 HTML 标签再 strip —— 交易所页面渲染层偶尔把高亮/链接标记
    也塞进 JSON 字段值里 (营业部名称、代码都见过), 不清掉会污染 normalize_cn_name
    的比较结果或让代码后缀判断误判。"""
    if value is None:
        return None
    text = _HTML_TAG_RE.sub("", str(value)).strip()
    return text or None


def _canon_exchange_code(value: Any, *, market: str) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    text = text.upper()
    if text.endswith((".SH", ".SZ")):
        return text
    return f"{text}{_EXCHANGE_SUFFIX[market]}"


def _strip_comma_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    return float(text)


def _vol_2dp(v: Any) -> float | None:
    """成交量归到百股精度 (交易所网页只保留 2 位小数)。

    不用内置 ``round``: 它是银行家舍入 (round-half-to-even) 且吃浮点二进制表示
    误差 —— ``round(13.145, 2)`` 在 Python 里是 13.14 不是 13.15 (13.145 的
    二进制浮点实际存的是略小于 13.145 的值)。经 ``Decimal(str(v))`` 先按十进制
    文本重建再 ``ROUND_HALF_UP``, 才是交易所页面"四舍五入保留 2 位"的真实语义。
    """
    if v is None:
        return None
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def canon_exchange_row(row: Mapping[str, Any], *, market: str) -> dict[str, Any]:
    """交易所逐笔行 -> 与本地/妙想同形状的比较行 (§3.4.3 / 业主裁定 D)。

    规则: (1) 数值去千分位逗号; (2) 成交量归到百股精度 (:func:`_vol_2dp`) ——
    交易所网页是万股保留 2 位, 本地/妙想是精确股数/1e4, 三方比对前都要归到同一
    精度, 见 :func:`compare_day_three_way`; (3) 代码按 market 补交易所后缀
    (两所页面代码都不带后缀); (4) 字符串字段先去 HTML 标签 (:func:`_clean_text`)
    再走 ``assignment_gap_recon.normalize_cn_name`` 做全角括号 -> 半角归一
    (不重复发明同一件事的第二份实现)。深市金额本来就是万元, 与本地/妙想的
    amount 直接比, 这里不做任何换算。
    """
    key = str(market).strip().lower()
    if key not in _EXCHANGE_FIELDS:
        raise ValueError(f"unknown exchange market {market!r}; expected one of {sorted(_EXCHANGE_FIELDS)}")
    fields = _EXCHANGE_FIELDS[key]
    vol = _strip_comma_float(row.get(fields["vol"]))
    return {
        "ts_code": _canon_exchange_code(row.get(fields["ts_code"]), market=key),
        "price": _strip_comma_float(row.get(fields["price"])),
        "vol": _vol_2dp(vol) if vol is not None else None,
        "amount": _strip_comma_float(row.get(fields["amount"])),
        "buyer": normalize_cn_name(_clean_text(row.get(fields["buyer"]))),
        "seller": normalize_cn_name(_clean_text(row.get(fields["seller"]))),
    }


def _canon_compare_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """三方比对前的按边归一 (§3.4.3 / 业主裁定 3): ``vol`` 统一到百股精度
    (:func:`_vol_2dp`), ``buyer``/``seller`` 都过 ``normalize_cn_name``。

    2026-09-11 主会话复核前只在交易所一侧做了括号归一 —— 本地/妙想两侧的名字也
    可能带全角括号 (同一家营业部在不同披露渠道格式不统一), 三边都要过一遍才公平,
    不能只挑交易所页面"有问题"。
    """
    r = dict(row)
    if r.get("vol") is not None:
        r["vol"] = _vol_2dp(r["vol"])
    if "buyer" in r:
        r["buyer"] = normalize_cn_name(r["buyer"])
    if "seller" in r:
        r["seller"] = normalize_cn_name(r["seller"])
    return r


def _round_vol_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [_canon_compare_row(row) for row in rows]


def compare_day_three_way(
    local: Sequence[Mapping[str, Any]],
    miaoxiang: Sequence[Mapping[str, Any]],
    exchange: Sequence[Mapping[str, Any]],
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """§3.4.3: 三对 ``compare_rows(domain="block_trade")``, 比对前三边都过
    :func:`_canon_compare_row` (vol 归到百股精度 + buyer/seller 括号归一)。

    交易所网页只有百股精度, 本地/妙想是精确股数/1e4 —— 不做精度归一会把这条纯格式
    差异误报成"缺失" (本轮变异清单要挡的正是这条)。买卖方名字三边都要归一 (不只是
    交易所那边, 本地/妙想的名字来源也可能带全角括号)。三对都用同一个 block_trade
    grain (compare_rows 会自动从比较键剔除声明的 multiplicity_index=seq, 三边都
    不需要带 seq)。
    """
    local_r = _round_vol_rows(local)
    mx_r = _round_vol_rows(miaoxiang)
    exch_r = _round_vol_rows(exchange)
    return {
        "local_vs_miaoxiang": compare_rows(
            domain="block_trade", left_rows=local_r, right_rows=mx_r,
            left_name="local", right_name="miaoxiang", registry=registry,
        ),
        "local_vs_exchange": compare_rows(
            domain="block_trade", left_rows=local_r, right_rows=exch_r,
            left_name="local", right_name="exchange", registry=registry,
        ),
        "miaoxiang_vs_exchange": compare_rows(
            domain="block_trade", left_rows=mx_r, right_rows=exch_r,
            left_name="miaoxiang", right_name="exchange", registry=registry,
        ),
    }


# ------------------------------------------------------------------------ verify --

def _select_day_rows(
    conn: Any,
    source_sql: str,
    day: str,
    columns: Sequence[str],
    *,
    date_col: str = "trade_date",
    params_prefix: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    cols_sql = ", ".join(f'"{c}"' for c in columns)
    rows = conn.execute(
        f'SELECT {cols_sql} FROM {source_sql} WHERE CAST("{date_col}" AS VARCHAR) = ?',
        [*params_prefix, day],
    ).fetchall()
    return [dict(zip(columns, r)) for r in rows]


def _table_day_rows(
    conn: Any, table: str, day: str, columns: Sequence[str], *, date_col: str = "trade_date"
) -> list[dict[str, Any]]:
    return _select_day_rows(conn, f'"{table}"', day, columns, date_col=date_col)


def _archive_day_rows(
    conn: Any, path: Path, day: str, columns: Sequence[str], *, date_col: str = "trade_date"
) -> list[dict[str, Any]]:
    return _select_day_rows(
        conn, f"read_parquet('{Path(path).as_posix()}')", day, columns, date_col=date_col
    )


def _archive_distinct_days(conn: Any, path: Path, *, date_col: str = "trade_date") -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT CAST(\"{date_col}\" AS VARCHAR) "
        f"FROM read_parquet('{Path(path).as_posix()}') ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def _run_verify_checks(
    conn: Any,
    domain: str,
    table: str,
    index_col: str,
    key_cols: Sequence[str],
    *,
    run_id: str,
    exchange_json: Path | None,
    date: str | None,
    archive_dir: Path | None,
) -> dict[str, Any]:
    null_dates = null_index_dates(conn, table, index_col)

    archive_path = _archive_path(table, run_id, archive_dir=archive_dir)
    dates_ok: list[str] = []
    dates_old_not_subset: list[dict[str, Any]] = []
    if archive_path.exists():
        for day in _archive_distinct_days(conn, archive_path):
            # _apply_vol_2dp: 旧行只有百股精度, 新行是妙想精确股数/1e4 —— 两边不
            # 归到同一精度, block_trade 的旧⊆新会把这条纯精度差异误报成缺失
            # (对没有 vol 列的 top_inst 是无操作)。
            old_rows = _apply_vol_2dp(_archive_day_rows(conn, archive_path, day, key_cols))
            new_rows = _apply_vol_2dp(_table_day_rows(conn, table, day, key_cols))
            report = old_subset_of_new(old_rows, new_rows, list(key_cols))
            if report.ok:
                dates_ok.append(day)
            else:
                dates_old_not_subset.append(
                    {
                        "date": day,
                        "missing_keys": report.missing_keys,
                        "short_counts": report.short_counts,
                    }
                )

    three_way: dict[str, Any] | None = None
    if exchange_json is not None and date is not None:
        payload = json.loads(Path(exchange_json).read_text(encoding="utf-8"))
        market = str(payload.get("market", "sh"))
        # canon_exchange_row 只做格式规则 (D), 不认识/不产出 trade_date —— 这里
        # 按 --date 显式补上, 与 local_rows (SELECT 出来的表列自带) / miaoxiang_rows
        # (payload 里存的应是已过 clean_block_trade_row 的映射行, 本就带 trade_date)
        # 对齐, compare_rows 才能在 trade_date 这个 grain 列上找到值。
        exchange_rows = []
        for r in payload.get("exchange_rows", []):
            canon_row = canon_exchange_row(r, market=market)
            canon_row["trade_date"] = date
            exchange_rows.append(canon_row)
        local_rows = _table_day_rows(
            conn, table, date, ("ts_code", "trade_date", "price", "vol", "buyer", "seller")
        )
        miaoxiang_rows = payload.get("miaoxiang_rows", [])
        three_way = compare_day_three_way(local_rows, miaoxiang_rows, exchange_rows)

    return {
        "domain": domain,
        "table": table,
        "dates_ok": dates_ok,
        "dates_old_not_subset": dates_old_not_subset,
        "dates_null": null_dates,
        "three_way": three_way,
    }


def verify(
    domain: str,
    *,
    run_id: str,
    exchange_json: Path | None = None,
    date: str | None = None,
    record: bool = False,
    conn: Any = None,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """§3.4: 逐日校验, read_only 默认; ``--record`` 才写第二条记账并取 writer lock。

    步骤: (1) 旧 ⊆ 新, 每日, 用归档 parquet 与当前表比 (:func:`old_subset_of_new`,
    ``vol`` 先经 :func:`_apply_vol_2dp` 归到同一精度); (2) NULL 日清单
    (:func:`null_index_dates`); (3) 若给了 ``--exchange-json --date``, 三方比对
    该日 (:func:`compare_day_three_way`); 输出 ``dates_ok / dates_old_not_subset
    / dates_null / three_way``。

    ``record=True`` 时锁覆盖整个校验过程, 不是只包住最后一次 record_data_deletion
    ——校验读到的状态和记的账必须是同一个快照, 否则锁只护着写、护不住"读的时候被
    另一个写者动了表"这种情况, 记账内容就和当次真实校验对不上。``record=False``
    (默认) 保持只读连接, 不取锁 (纯只读场景不该抢占写窗口)。

    ``conn``/``archive_dir`` 与 :func:`prepare` 同理, 为测试注入而加, 不在 r2 §4
    S7 的字面签名里。
    """
    table = _domain_table(domain)
    index_col = DOMAIN_INDEX_COL[domain]
    key_cols = DOMAIN_OLD_SUBSET_KEY[domain]
    owns_conn = conn is None

    if record:
        with writer_lock("reland_event_domain"):
            if owns_conn:
                conn = connect(str(db_path("tushare_raw")), read_only=False)
            try:
                result = _run_verify_checks(
                    conn, domain, table, index_col, key_cols,
                    run_id=run_id, exchange_json=exchange_json, date=date,
                    archive_dir=archive_dir,
                )
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name=table,
                    delete_scope="rows_replaced_verified",
                    reason="grain 契约重落逐日校验通过 (旧⊆新 + NULL 日清单 + 三方)",
                    verification=result,
                )
                return result
            finally:
                if owns_conn:
                    conn.close()

    if owns_conn:
        conn = connect(str(db_path("tushare_raw")), read_only=True)
    try:
        return _run_verify_checks(
            conn, domain, table, index_col, key_cols,
            run_id=run_id, exchange_json=exchange_json, date=date,
            archive_dir=archive_dir,
        )
    finally:
        if owns_conn:
            conn.close()


# ---------------------------------------------------------------------------- CLI --

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prepare = sub.add_parser("prepare", help="归档 + 记账 + DDL (dry-run 默认)")
    p_prepare.add_argument("--domain", required=True, choices=sorted(DOMAIN_TABLES))
    p_prepare.add_argument("--run-id", required=True)
    p_prepare.add_argument("--execute", action="store_true")

    p_verify = sub.add_parser("verify", help="逐日校验 (read_only 默认)")
    p_verify.add_argument("--domain", required=True, choices=sorted(DOMAIN_TABLES))
    p_verify.add_argument("--run-id", required=True)
    p_verify.add_argument("--exchange-json", type=Path, default=None)
    p_verify.add_argument("--date", default=None)
    p_verify.add_argument("--record", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        result = prepare(args.domain, run_id=args.run_id, execute=args.execute)
    else:
        result = verify(
            args.domain,
            run_id=args.run_id,
            exchange_json=args.exchange_json,
            date=args.date,
            record=args.record,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
