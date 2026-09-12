#!/usr/bin/env python3
"""事件域历史重落: 归档 + 记账 + DDL (``prepare``) / 逐日验收 (``verify``)。

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
  G. (2026-09-12, 验收判据切片 V1) 逐日校验的判据从"旧⊆新"整表判定换成三分类
     (:func:`classify_old_keys`): 精确匹配 (matched) / 可用已知的"多笔被旧供应商
     合并成一笔"模式解释的差异 (explained_merged, 只对声明了 ``merged_rule`` 的域,
     目前只有 block_trade) / 无法解释 (residual, 按 ``candidate_cols`` 再找候选,
     标 kind)。旧判据 ``old_subset_of_new`` 已删 —— 它只问"新里有没有这个 key",
     两侧都不做名称归一 (三方比对路径 :func:`compare_day_three_way` 早就做了),
     block_trade 因此曾多报 2,430 个假缺失; 新判据对两侧都先过
     :func:`canon_row` (名称归一 + 精度归一) 再比。
  H. :func:`structural_checks` 的 S1-S4 是"这张表现在的样子有没有结构性缺陷"的机器
     可判闸, 与 G 的分类判据 (数据内容层面的对账) 正交: S1/S2 抓半途重落 (序号列
     NULL / 该填的 grain 列 NULL), S3 复用 ``check_grain_uniqueness`` 的通用唯一性 +
     多重度序号连续性检查, S4 是 top_inst 专属的 board_rank 连续性 (按
     (trade_date, ts_code, reason, side) 分组序号必须是 1..count 无洞 —— top_inst 在
     sync_registry 里没声明 multiplicity_index, 通用算法管不到它, 故专开 S4)。
  I. :func:`verdict` 是只读 report 的纯函数, 不重新计算任何东西: 结构检查任一 fail,
     或 residual 里出现"新表已经比交易所还缺"/"妙想缺口没登记"两类, exit_code=3
     (硬失败, ``--record`` 会拒绝写账); 干净但仍有未核实的 residual, exit_code=2
     (需要人工过一遍, 决定是旧供应商的错还是要登记进 vendor_gaps); 否则 0。V1 里
     residual 一律落 ``unverified`` —— 拿 vendor_gaps 消费 residual、判定
     old_vendor_error/miaoxiang_gap_* /new_diverges_from_exchange 是后续切片的活。
  J. :func:`load_vendor_gaps` 是"妙想缺、交易所有"这类已核实缺口的唯一登记通道,
     故意不给命令行放行旗子 (没有 ``--allow-gap`` 之类的参数) —— 免得成为绕过验收的
     后门。V1 只建通道 + 校验 + 记账时算它的 sha256 存证 (哪个登记版本在场当次校验
     通过), 分类逻辑还不读它 (H 提到的 residual_verdict_counts 里
     miaoxiang_gap_registered/unregistered 恒 0)。
  K. (V2, 验收判据切片二: 交易所证据层) ``canon_exchange_row`` 签名改为
     ``(market, trade_date, row)`` —— 直接把 ``trade_date`` 并进输出, 不再返回
     ``amount`` (交易所证据层只用 :func:`exchange_key`/:func:`gap_key` 六个可比字段
     判等, 不再做金额层面的三方比对)。旧签名 ``(row, *, market)`` 连同它唯一的调用方
     ``compare_day_three_way``/``_three_way_for_date`` (--exchange-json/--date 单日
     路径) 一并删除, 不留墓碑 —— 这是本切片规格点名允许改写的三个符号。
  L. 覆盖范围判断 (哪些代码算"这份交易所文件管得到") 统一收在私有
     :func:`_covered_codes` 里, :func:`exchange_verdicts`/:func:`ceiling_compare` 都
     调它, 不各自重写一遍规则 (免得两处判断不一致): 后缀须与 ``market`` 一致
     (.SH/.SZ); 新表里能查到 ``security_type`` 的按 ``{EQA, FDO}`` 白名单过 (BD0/其它
     不覆盖); 代码只在交易所出现、新表没有时按后缀直接算覆盖 (没有 security_type 可
     查, 也没有理由怀疑交易所自己报错); 文件带 ``codes`` 白名单时再交一遍求交集。
  M. ``verify`` 新增 ``exchange_dir``/``vendor_gaps_path``: 只有 ``block_trade`` 允许
     传 ``exchange_dir`` (``top_inst`` 传了直接 ``ValueError``, CLI 层 ``main`` 捕获后
     返回 1, 不是让异常裸抛把退出码交给解释器)。按归档里出现的每一天 × {sh, sz} 找
     ``exch_<market>_<day>.json``; 文件不存在不是失败 —— 那天那个市场的 residual 保持
     V1 的 unverified, 只在 ``report["ceiling"]["not_checked"]`` 里记一笔 (day,
     market), 供人工知道"这些天这些市场我们其实没有交易所口径可核对"。
  N. vendor_gaps 的消费点扩到三处 (读 :func:`load_vendor_gaps` 同一份登记):
     miaoxiang_gap 的 :func:`gap_key` 未登记 → ``miaoxiang_gap_unregistered`` (硬
     失败); ceiling gap (交易所有、新表没有, 覆盖范围内) 未登记 →
     ``ceiling_gap_unregistered`` (硬失败); 已登记的缺口 key 如果在新表里重新出现
     (按 :func:`exchange_key` 多重集) → ``registered_gap_present`` (硬失败 —— 说明
     供应商回补/重落已经把这个缺口填上了, 登记该删了, 但删除是头注 J 说的人工动作,
     这里只负责报警不负责自动摘)。
  O. ceiling extra (新表比交易所"多"出来的笔数) 沪深处置不同: 沪市
     (``ceiling_extra_sh``) 硬失败 —— 上交所逐笔查询是完整披露, 新表凭空多出的成交
     只能是错的; 深市 (``ceiling_extra_sz``) 只降级到"需要人工看一眼" —— 深交所
     CATALOGID=1265 只覆盖协议交易, 盘后定价没有逐笔可查, 新表比它"多"是合法的
     (业主裁定, 见 CLAUDE.md 交易所证据文件小节)。

用法:
  python backend/scripts/reland_event_domain.py prepare --domain block_trade --run-id r1
  python backend/scripts/reland_event_domain.py prepare --domain block_trade --run-id r1 --execute
  python backend/scripts/reland_event_domain.py verify  --domain block_trade --run-id r1
  python backend/scripts/reland_event_domain.py verify  --domain block_trade --run-id r1 \\
      --exchange-dir data/archive/exchange_evidence/block_trade --record
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import yaml
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.check_grain_uniqueness import check_table as _check_grain_table  # noqa: E402
from services.data_access.resolver import db_path  # noqa: E402
from services.data_deletion import record_data_deletion  # noqa: E402
from services.data_sources.assignment_gap_recon import normalize_cn_name  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.writer_lock import writer_lock  # noqa: E402

ARCHIVE_DIR = ROOT / "data" / "archive" / "lifecycle"  # gitignored (r2 §3.2), 同
                                                        # db_lifecycle_delete 的归档目录
SYNC_REGISTRY_PATH = ROOT / "backend" / "config" / "sync_registry.yaml"
VENDOR_GAPS_PATH = ROOT / "backend" / "config" / "vendor_gaps.yaml"

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
# multiplicity_index 无关 (top_inst 没有声明, kind='none')。structural_checks 的
# S1/S2 复用它当"序号列"。
DOMAIN_INDEX_COL: dict[str, str] = {"block_trade": "seq", "top_inst": "board_rank"}


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


# ------------------------------------------------------------------- canon rows --

@dataclass(frozen=True)
class MergedRule:
    tol: float = 0.01
    min_parts: int = 2


@dataclass(frozen=True)
class CanonSpec:
    key_cols: tuple[str, ...]
    name_cols: tuple[str, ...]
    vol_2dp_cols: tuple[str, ...]
    zero_null_2dp_cols: tuple[str, ...]
    merged_rule: MergedRule | None
    group_cols: tuple[str, ...]
    candidate_cols: tuple[str, ...]
    amount_close_tol: float | None


# key_cols/candidate_cols/group_cols 的取舍见各域头注 (原 DOMAIN_OLD_SUBSET_KEY 的
# 排除理由同样适用, 现在挪到这里):
#   block_trade key_cols 六列 (不含 seq —— 它是落地层派生的到达顺序, 不是供应商
#     事实); vol 纳入 key 但两侧都先经 _vol_2dp 归到百股精度, 不会把"旧行只有百股
#     精度、新行是妙想精确股数/1e4"这种纯精度差异误报成缺失。merged_rule 处理"旧
#     供应商把同席位/同价/同方向的多笔合并成一笔"这个已知模式 (group_cols 排除
#     vol —— 正是要在 vol 上找子集和)。candidate_cols 更窄 (排除 buyer/seller),
#     用于 residual 阶段找"很可能是同一笔, 只是买卖方记录不同"的候选。
#   top_inst key_cols 排除 board_rank (这次重落才新增的列, 旧归档行必然 NULL) 与
#     reason (旧行按具体数值写的措辞, 与新行的规范化榜单理由字符串不是同一份文本);
#     buy/sell 先 zero_null 再舍入 (旧行有的记 NULL 有的记 0, 新行反之)。没有
#     merged_rule (同一席位可以合法同时上多个榜, 不存在"多笔合并成一笔"这个模式)。
#     candidate_cols 排除 buy/sell, amount_close_tol 允许金额有限差异 (营业部口径
#     四舍五入误差) 仍算"很可能是同一笔"。
DOMAIN_CANON: dict[str, CanonSpec] = {
    "block_trade": CanonSpec(
        key_cols=("ts_code", "trade_date", "price", "vol", "buyer", "seller"),
        name_cols=("buyer", "seller"),
        vol_2dp_cols=("vol",),
        zero_null_2dp_cols=(),
        merged_rule=MergedRule(),
        group_cols=("ts_code", "trade_date", "price", "buyer", "seller"),
        candidate_cols=("ts_code", "trade_date", "price", "vol"),
        amount_close_tol=None,
    ),
    "top_inst": CanonSpec(
        key_cols=("trade_date", "ts_code", "exalter", "side", "buy", "sell"),
        name_cols=("exalter",),
        vol_2dp_cols=(),
        zero_null_2dp_cols=("buy", "sell"),
        merged_rule=None,
        group_cols=(),
        candidate_cols=("trade_date", "ts_code", "exalter", "side"),
        amount_close_tol=100.0,
    ),
}


def _canon_spec(domain: str) -> CanonSpec:
    try:
        return DOMAIN_CANON[domain]
    except KeyError:
        raise ValueError(
            f"unknown domain {domain!r}; expected one of {sorted(DOMAIN_CANON)}"
        ) from None


def canon_row(domain: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """按域的 :data:`CanonSpec` 归一一行 (旧行/新行都要过): 名字过
    ``normalize_cn_name`` (全角括号 -> 半角, 见 ``assignment_gap_recon``), 数量列
    归到百股精度 (:func:`_vol_2dp`, 与 :func:`canon_exchange_row` 用同一函数,
    对方页面本来就是这个精度), 零/NULL 混用的金额列先把 NULL 当 0 再舍入。其它列
    原样透传 —— 这不是"选列投影", 只是归一, 调用方后续还要用到 trade_date 之类
    不在任何一组里的列。
    """
    spec = _canon_spec(domain)
    out = dict(row)
    for col in spec.name_cols:
        if col in out:
            value = out[col]
            out[col] = None if value is None else normalize_cn_name(value)
    for col in spec.vol_2dp_cols:
        if col in out:
            value = out[col]
            out[col] = None if value is None else _vol_2dp(value)
    for col in spec.zero_null_2dp_cols:
        if col in out:
            value = out[col]
            out[col] = _vol_2dp(0 if value is None else value)
    return out


# --------------------------------------------------------------- classification --

@dataclass
class ClassReport:
    matched: Counter
    explained_merged: list[dict[str, Any]]
    residual: list[dict[str, Any]]
    old_total: int


def _matched_count(old_n: int, new_n: int) -> int:
    """精确匹配份数 = 两侧同 key 计数取小者。拆成独立函数纯粹为了 T5 能
    monkeypatch 出"一个 key 被计两次"这种坏账, 钉住下面的分区不变量真的在检查
    (而不是摆设)。"""
    return min(old_n, new_n)


def _subset_sum_within_tol(
    vols_cents: Sequence[int], target_cents: int, tol_cents: int, min_parts: int
) -> tuple[bool, list[int]]:
    """整数分子集和 DP (§3 步骤 3.a): 是否存在 ``vols_cents`` 里 >= ``min_parts``
    个元素的子集, 其和与 ``target_cents`` 之差的绝对值 <= ``tol_cents``。命中时连
    带返回该子集在 ``vols_cents`` 里的下标 (供 explained_merged 记 ``parts``)。

    按元素个数分层的 0/1 背包: ``reachable[k]`` 是"恰用 k 个元素能凑出的和 -> 该和
    对应的一组下标"。逐元素处理前先对 ``reachable`` 取快照, 保证同一元素在一次
    处理里不会被用两次 (标准 0/1 背包倒序技巧的等价写法, 这里维度是"个数"不是
    "重量")。
    """
    n = len(vols_cents)
    if n < min_parts:
        return False, []
    reachable: list[dict[int, list[int]]] = [dict() for _ in range(n + 1)]
    reachable[0][0] = []
    for idx, v in enumerate(vols_cents):
        prev = [dict(d) for d in reachable]
        for count in range(0, idx + 1):
            for s, path in prev[count].items():
                new_sum = s + v
                if new_sum not in reachable[count + 1]:
                    reachable[count + 1][new_sum] = path + [idx]
    for count in range(min_parts, n + 1):
        for s, path in reachable[count].items():
            if abs(s - target_cents) <= tol_cents:
                return True, path
    return False, []


def _residual_entry(
    spec: CanonSpec, old_row: Mapping[str, Any], key: tuple[Any, ...],
    new_canon: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """§3 步骤 4: 在新行里按 ``candidate_cols`` (比 key_cols 更窄, 通常排掉
    名字/金额列) 找候选, 再看候选里有没有"金额在容差内"的, 决定 kind。"""
    candidate_cols = spec.candidate_cols
    old_candidate_key = tuple(old_row.get(c) for c in candidate_cols)
    candidates = [
        r for r in new_canon if tuple(r.get(c) for c in candidate_cols) == old_candidate_key
    ]
    if not candidates:
        kind = "none"
    elif spec.amount_close_tol is not None and any(
        all(
            abs(
                (candidate.get(col) if candidate.get(col) is not None else 0.0)
                - (old_row.get(col) if old_row.get(col) is not None else 0.0)
            )
            <= spec.amount_close_tol
            for col in spec.zero_null_2dp_cols
        )
        for candidate in candidates
    ):
        kind = "candidate_amount_close"
    else:
        kind = "candidate"
    return {"key": list(key), "kind": kind, "candidates": [dict(c) for c in candidates]}


def classify_old_keys(
    domain: str,
    old_rows: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
) -> ClassReport:
    """§3.4 (V1): 旧行三分类, 替代旧的整表"旧⊆新"判定。

    1. 两侧逐行 :func:`canon_row`, 按 ``key_cols`` 取 key, 建 ``Counter``。
    2. 每个旧 key 取两侧计数较小者记为精确匹配 (:func:`_matched_count`); 超出
       新表能对应的份数留到下一步。
    3. 若域声明了 ``merged_rule``: 按 ``group_cols`` 把剩余旧行与全部新行分组,
       组内验三条 (子集和 DP 命中 / 组总量在容差内 / 新行数不少于旧行数) 都成立
       才判定为"能用合并规则解释", 该组内每条剩余旧行各记一条 explained_merged
       (与它们共享同一个胜出子集 —— 组级验证本就是聚合判定, 不是逐行判定)。
    4. 其余 (含 ``merged_rule`` 为 None 的域全部剩余行) 进 residual
       (:func:`_residual_entry`)。
    5. 分区不变量: matched 份数 + explained_merged 条数 + residual 条数 必须等于
       旧行总数, 否则 ``AssertionError`` —— 这是"每一行旧数据都被记账、没有一行
       被静默漏记或重复计"的最后一道机器可判闸。
    """
    spec = _canon_spec(domain)
    old_canon = [canon_row(domain, r) for r in old_rows]
    new_canon = [canon_row(domain, r) for r in new_rows]

    key_cols = spec.key_cols
    old_keys = [tuple(r.get(c) for c in key_cols) for r in old_canon]
    new_keys = [tuple(r.get(c) for c in key_cols) for r in new_canon]
    old_c: Counter = Counter(old_keys)
    new_c: Counter = Counter(new_keys)

    matched: Counter = Counter()
    for key, old_n in old_c.items():
        m = _matched_count(old_n, new_c.get(key, 0))
        if m:
            matched[key] = m

    consumed: dict[tuple[Any, ...], int] = {}
    leftover: list[tuple[dict[str, Any], tuple[Any, ...]]] = []
    for row, key in zip(old_canon, old_keys):
        used = consumed.get(key, 0)
        if used < matched.get(key, 0):
            consumed[key] = used + 1
        else:
            leftover.append((row, key))

    explained_merged: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []

    if leftover and spec.merged_rule is not None:
        rule = spec.merged_rule
        group_cols = spec.group_cols
        new_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in new_canon:
            gkey = tuple(row.get(c) for c in group_cols)
            new_groups.setdefault(gkey, []).append(row)
        leftover_groups: dict[tuple[Any, ...], list[tuple[dict[str, Any], tuple[Any, ...]]]] = {}
        for row, key in leftover:
            gkey = tuple(row.get(c) for c in group_cols)
            leftover_groups.setdefault(gkey, []).append((row, key))

        tol_cents = round(rule.tol * 100)
        for gkey, items in leftover_groups.items():
            g_old_rows = [row for row, _key in items]
            g_new_rows = new_groups.get(gkey, [])
            sum_old_cents = round(sum((row.get("vol") or 0.0) for row in g_old_rows) * 100)
            sum_new_cents = round(sum((row.get("vol") or 0.0) for row in g_new_rows) * 100)

            cond_c = len(g_new_rows) >= len(g_old_rows)
            cond_b = abs(sum_old_cents - sum_new_cents) <= tol_cents * len(g_old_rows)
            found_a = False
            winning_parts: list[dict[str, Any]] = []
            if cond_c and len(g_new_rows) >= rule.min_parts:
                vols_cents = [round((row.get("vol") or 0.0) * 100) for row in g_new_rows]
                found_a, idxs = _subset_sum_within_tol(vols_cents, sum_old_cents, tol_cents, rule.min_parts)
                if found_a:
                    winning_parts = [dict(g_new_rows[i]) for i in idxs]

            if found_a and cond_b and cond_c:
                for _row, key in items:
                    explained_merged.append({"key": list(key), "parts": [dict(p) for p in winning_parts]})
            else:
                for row, key in items:
                    residual.append(_residual_entry(spec, row, key, new_canon))
    else:
        for row, key in leftover:
            residual.append(_residual_entry(spec, row, key, new_canon))

    old_total = sum(old_c.values())
    assert sum(matched.values()) + len(explained_merged) + len(residual) == old_total, (
        "classify_old_keys partition invariant violated: "
        f"matched={sum(matched.values())} explained_merged={len(explained_merged)} "
        f"residual={len(residual)} old_total={old_total}"
    )

    return ClassReport(matched=matched, explained_merged=explained_merged, residual=residual, old_total=old_total)


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


# ------------------------------------------------------------------ structural --

def structural_checks(conn: Any, domain: str, table: str) -> dict[str, dict[str, Any]]:
    """S1-S4 (头注 H): 表现在的样子有没有结构性缺陷, 与 :func:`classify_old_keys`
    的内容对账正交。S1/S2/S3 读的 grain/multiplicity_index 是 ``sync_registry.yaml``
    里的真实声明 (不是调用方传入的, 不能被测试悄悄换成对自己有利的假 grain)。
    """
    index_col = DOMAIN_INDEX_COL[domain]
    registry_doc = yaml.safe_load(SYNC_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    entry = ((registry_doc.get("domains") or {}).get(domain)) or {}
    grain = list(entry.get("grain") or [])
    multiplicity_index = entry.get("multiplicity_index")

    # S1: 序号列 NULL 的日期 = 这天还没重落。
    null_dates = null_index_dates(conn, table, index_col)
    s1 = {"ok": not null_dates, "detail": null_dates}

    # S2: 声明的 grain 列 (去掉序号列) 一行都不该是 NULL —— NULL 说明重落写坏了
    # 一部分字段, 不是"还没重落"(那是 S1 管的)。
    base_cols = [c for c in grain if c != index_col]
    null_counts: dict[str, int] = {}
    for col in base_cols:
        n = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NULL').fetchone()[0]
        if n:
            null_counts[col] = int(n)
    s2 = {"ok": not null_counts, "detail": null_counts}

    # S3: 复用 check_grain_uniqueness 的通用唯一性 + (若声明了 multiplicity_index)
    # 序号连续性检查。
    s3_raw = _check_grain_table(conn, table, grain, multiplicity_index)
    s3 = {"ok": s3_raw.get("status") == "pass", "detail": s3_raw}

    # S4: top_inst 专属 —— 同一 (trade_date, ts_code, reason, side) 下 board_rank
    # 必须恰为 1..count, 无洞无重号。sync_registry 没给 top_inst 声明
    # multiplicity_index, S3 的通用序号连续性检查管不到它, 故专开。
    if domain == "top_inst":
        rows = conn.execute(
            f'SELECT MIN(board_rank), MAX(board_rank), COUNT(*), COUNT(DISTINCT board_rank) '
            f'FROM "{table}" GROUP BY trade_date, ts_code, reason, side'
        ).fetchall()
        bad_groups = sum(1 for mn, mx, n, dn in rows if not (mn == 1 and mx == n and dn == n))
        s4 = {"ok": bad_groups == 0, "detail": {"bad_groups": bad_groups}}
    else:
        s4 = {"ok": True, "detail": "n/a"}

    return {"S1": s1, "S2": s2, "S3": s3, "S4": s4}


# --------------------------------------------------------------- vendor gaps --

_VENDOR_GAPS_TOP_KEYS = {"version", "gaps"}
_VENDOR_GAPS_ITEM_KEYS = {"domain", "trade_date", "ts_code", "key", "evidence", "checked_at"}
_VENDOR_GAPS_CHECKED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_vendor_gaps(path: Path | None = None) -> frozenset[tuple[str, tuple[str, ...]]]:
    """加载 ``vendor_gaps.yaml`` (头注 J): "妙想缺、交易所有"这类已核实缺口的唯一
    登记通道 —— 不给命令行放行旗子, 只认这份带交易所证据的登记。

    校验失败 (根键不是恰好 ``{version, gaps}`` / ``version`` != 1 / ``gaps`` 非
    list / 任一条目的键不是恰好那 6 个规定键 / ``domain`` 不在 :data:`DOMAIN_CANON`
    / ``trade_date`` 不是 8 位数字串 / ``ts_code`` 为空 / ``key`` 不是非空字符串
    列表 / ``evidence`` 为空 / ``checked_at`` 不匹配 ``YYYY-MM-DD``) 一律
    ``ValueError``, 不静默降级或跳过坏条目 —— 这份登记表本身的完整性就是验收闸的
    一部分 (红线 11: 未知键 fail-closed)。
    """
    p = Path(path) if path is not None else VENDOR_GAPS_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: root must be a mapping, got {type(doc).__name__}")
    if set(doc.keys()) != _VENDOR_GAPS_TOP_KEYS:
        raise ValueError(
            f"{p}: top-level keys must be exactly {sorted(_VENDOR_GAPS_TOP_KEYS)}, "
            f"got {sorted(doc.keys())}"
        )
    if doc.get("version") != 1:
        raise ValueError(f"{p}: version must be 1, got {doc.get('version')!r}")
    gaps = doc.get("gaps")
    if not isinstance(gaps, list):
        raise ValueError(f"{p}: gaps must be a list, got {type(gaps).__name__}")

    out: set[tuple[str, tuple[str, ...]]] = set()
    for i, item in enumerate(gaps):
        if not isinstance(item, dict) or set(item.keys()) != _VENDOR_GAPS_ITEM_KEYS:
            got = sorted(item.keys()) if isinstance(item, dict) else type(item).__name__
            raise ValueError(
                f"{p}: gaps[{i}] keys must be exactly {sorted(_VENDOR_GAPS_ITEM_KEYS)}, got {got}"
            )
        domain = item["domain"]
        if domain not in DOMAIN_CANON:
            raise ValueError(f"{p}: gaps[{i}].domain unknown {domain!r}")
        trade_date = item["trade_date"]
        if not (isinstance(trade_date, str) and len(trade_date) == 8 and trade_date.isdigit()):
            raise ValueError(f"{p}: gaps[{i}].trade_date must be an 8-digit string, got {trade_date!r}")
        ts_code = item["ts_code"]
        if not (isinstance(ts_code, str) and ts_code):
            raise ValueError(f"{p}: gaps[{i}].ts_code must be a non-empty string, got {ts_code!r}")
        key = item["key"]
        if not (isinstance(key, list) and key and all(isinstance(k, str) for k in key)):
            raise ValueError(f"{p}: gaps[{i}].key must be a non-empty list of strings, got {key!r}")
        evidence = item["evidence"]
        if not (isinstance(evidence, str) and evidence):
            raise ValueError(f"{p}: gaps[{i}].evidence must be a non-empty string, got {evidence!r}")
        checked_at = item["checked_at"]
        if not (isinstance(checked_at, str) and _VENDOR_GAPS_CHECKED_AT_RE.match(checked_at)):
            raise ValueError(f"{p}: gaps[{i}].checked_at must match YYYY-MM-DD, got {checked_at!r}")
        out.add((domain, tuple(key)))
    return frozenset(out)


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
        "buyer": "branchbuy",
        "seller": "branchsell",
    },
    "sz": {
        "ts_code": "zqdh",
        "price": "cjjg",
        "vol": "cjgsnew",
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


def canon_exchange_row(market: str, trade_date: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """交易所逐笔行 -> 与本地/妙想同形状的比较行 (头注 K, V2 改签名)。

    规则: (1) 数值去千分位逗号; (2) 价格与成交量都归到百股/分精度
    (:func:`_vol_2dp`, Decimal ROUND_HALF_UP 到 2 位) —— 交易所网页只有 2 位小数,
    本地/妙想的价格可能是 3 位 (基金), vol 是精确股数/1e4, 不归一会把纯精度差异
    误报成缺失; (3) 代码按 market 补交易所后缀 (两所页面代码都不带后缀); (4)
    字符串字段先去 HTML 标签 (:func:`_clean_text`) 再走
    ``assignment_gap_recon.normalize_cn_name`` 做全角括号 -> 半角归一 (不重复发明
    同一件事的第二份实现)。``trade_date`` 由调用方显式传入 (证据文件本身按
    market+日期一个文件, 不用再像 V1 的 --date 单日路径那样事后补)。不再返回
    ``amount`` —— 交易所证据层只用 :func:`exchange_key`/:func:`gap_key` 六个字段
    判等 (头注 K)。
    """
    key = str(market).strip().lower()
    if key not in _EXCHANGE_FIELDS:
        raise ValueError(f"unknown exchange market {market!r}; expected one of {sorted(_EXCHANGE_FIELDS)}")
    fields = _EXCHANGE_FIELDS[key]
    price = _strip_comma_float(row.get(fields["price"]))
    vol = _strip_comma_float(row.get(fields["vol"]))
    return {
        "ts_code": _canon_exchange_code(row.get(fields["ts_code"]), market=key),
        "trade_date": trade_date,
        "price": _vol_2dp(price) if price is not None else None,
        "vol": _vol_2dp(vol) if vol is not None else None,
        "buyer": normalize_cn_name(_clean_text(row.get(fields["buyer"]))),
        "seller": normalize_cn_name(_clean_text(row.get(fields["seller"]))),
    }


# ------------------------------------------------------------- exchange evidence --

_EXCHANGE_EVIDENCE_REQUIRED_KEYS = {"market", "trade_date", "exchange_rows"}
_EXCHANGE_EVIDENCE_OPTIONAL_KEYS = {
    "source", "fetched_at", "recordcount", "source_recordcount",
    "excluded_b_share_rows", "codes",
}
_EXCHANGE_EVIDENCE_ALL_KEYS = _EXCHANGE_EVIDENCE_REQUIRED_KEYS | _EXCHANGE_EVIDENCE_OPTIONAL_KEYS
_EXCHANGE_EVIDENCE_FILENAME_RE = re.compile(r"^exch_(sh|sz)_(\d{8})\.json$")
_CODE_RE = re.compile(r"^\d{6}$")


def load_exchange_evidence(path: Path) -> dict[str, Any]:
    """加载并严格校验一份交易所证据文件 (CLAUDE.md "交易所证据文件" 小节格式钉死)。

    文件名必须是 ``exch_<sh|sz>_<YYYYMMDD>.json``; ``trade_date`` 必须与文件名里
    的日期一致 —— 这不是多余检查, 是防"文件从别的日期拷贝过来改了个名"这种人工
    整理证据时最容易犯的错。未知键 / 缺必有键 / ``market`` 非法 / ``recordcount``
    与 ``len(exchange_rows)`` 不等 / ``source_recordcount`` 与
    ``recordcount + excluded_b_share_rows`` 的关系不成立 / ``codes`` 不是
    null 或合法 6 位代码串列表, 一律 ``ValueError`` (红线 11: 未知键 fail-closed,
    这份文件的完整性本身就是证据链的一部分, 不能静默放过一条格式不对的证据)。
    """
    p = Path(path)
    m = _EXCHANGE_EVIDENCE_FILENAME_RE.match(p.name)
    if not m:
        raise ValueError(
            f"{p}: filename must match exch_<sh|sz>_<YYYYMMDD>.json, got {p.name!r}"
        )
    date_from_name = m.group(2)

    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: root must be a JSON object, got {type(doc).__name__}")

    unknown = set(doc.keys()) - _EXCHANGE_EVIDENCE_ALL_KEYS
    if unknown:
        raise ValueError(f"{p}: unknown keys {sorted(unknown)}")
    missing = _EXCHANGE_EVIDENCE_REQUIRED_KEYS - set(doc.keys())
    if missing:
        raise ValueError(f"{p}: missing required keys {sorted(missing)}")

    market = doc["market"]
    if market not in ("sh", "sz"):
        raise ValueError(f"{p}: market must be 'sh' or 'sz', got {market!r}")

    trade_date = doc["trade_date"]
    if trade_date != date_from_name:
        raise ValueError(
            f"{p}: trade_date {trade_date!r} does not match filename date {date_from_name!r}"
        )

    exchange_rows = doc["exchange_rows"]
    if not isinstance(exchange_rows, list):
        raise ValueError(f"{p}: exchange_rows must be a list, got {type(exchange_rows).__name__}")

    if "recordcount" in doc:
        recordcount = doc["recordcount"]
        if not isinstance(recordcount, int) or isinstance(recordcount, bool):
            raise ValueError(f"{p}: recordcount must be an int, got {recordcount!r}")
        if recordcount != len(exchange_rows):
            raise ValueError(
                f"{p}: recordcount={recordcount} != len(exchange_rows)={len(exchange_rows)}"
            )

    if "source_recordcount" in doc and "excluded_b_share_rows" in doc:
        if "recordcount" not in doc:
            raise ValueError(
                f"{p}: source_recordcount/excluded_b_share_rows given but recordcount is missing"
            )
        source_recordcount = doc["source_recordcount"]
        excluded_b_share_rows = doc["excluded_b_share_rows"]
        if source_recordcount != doc["recordcount"] + excluded_b_share_rows:
            raise ValueError(
                f"{p}: source_recordcount={source_recordcount} != "
                f"recordcount({doc['recordcount']}) + excluded_b_share_rows({excluded_b_share_rows})"
            )

    if "codes" in doc and doc["codes"] is not None:
        codes = doc["codes"]
        if not (isinstance(codes, list) and all(isinstance(c, str) and _CODE_RE.match(c) for c in codes)):
            raise ValueError(f"{p}: codes must be null or a list of 6-digit code strings, got {codes!r}")

    return doc


# ------------------------------------------------------------------ exchange key --

def exchange_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """交易所精度下的可比 key (钉死): ``(ts_code, trade_date, price 两位舍入,
    vol 两位舍入, normalize_cn_name(buyer), normalize_cn_name(seller))``。

    对 :func:`canon_exchange_row` 的输出、以及 :func:`canon_row` 的输出 (旧表行/
    新表行) 都适用 —— 后两者的 buyer/seller 虽然已经过 ``canon_row`` 归一, 这里
    再归一一次是幂等的; 但 price 在 :data:`DOMAIN_CANON` 里没有声明归一列
    (只有 vol 声明了), 所以新表价格 (妙想可能 3 位小数) 必须靠这里补上两位舍入
    才能跟交易所 2 位精度对齐——这正是这个函数存在的理由: 不是每个调用点都要记得
    "price 也要舍入"。
    """
    price = row.get("price")
    vol = row.get("vol")
    return (
        row.get("ts_code"),
        row.get("trade_date"),
        _vol_2dp(price) if price is not None else None,
        _vol_2dp(vol) if vol is not None else None,
        normalize_cn_name(row.get("buyer")),
        normalize_cn_name(row.get("seller")),
    )


def gap_key(xkey: tuple[Any, ...]) -> tuple[str, ...]:
    """:func:`exchange_key` 的结果 -> ``vendor_gaps.yaml`` 里 block_trade 缺口条目
    ``key`` 字段的字符串形状 (钉死): ``(ts_code, trade_date, f"{price:.2f}",
    f"{vol:.2f}", buyer, seller)``。价格/成交量固定两位小数字符串 (不是
    ``str(float)``) —— 免得 10.0 与 10.00 因为 Python float repr 不同而被当成
    两个不同的登记 key。
    """
    ts_code, trade_date, price, vol, buyer, seller = xkey
    return (str(ts_code), str(trade_date), f"{price:.2f}", f"{vol:.2f}", str(buyer), str(seller))


# --------------------------------------------------------------- exchange coverage --

def _covered_codes(
    new_day_rows: Sequence[Mapping[str, Any]],
    exchange_rows_canon: Sequence[Mapping[str, Any]],
    market: str,
    codes: Sequence[str] | None = None,
) -> set[str]:
    """头注 L: 这份交易所文件"管得到"哪些代码 (钉死的四条规则统一在这一处判断,
    :func:`exchange_verdicts`/:func:`ceiling_compare` 都调它)。
    """
    suffix = _EXCHANGE_SUFFIX[market]
    new_by_code: dict[str, list[Mapping[str, Any]]] = {}
    for row in new_day_rows:
        code = row.get("ts_code")
        if isinstance(code, str):
            new_by_code.setdefault(code, []).append(row)

    covered: set[str] = set()
    for code, rows in new_by_code.items():
        if code.upper().endswith(suffix) and any(r.get("security_type") in ("EQA", "FDO") for r in rows):
            covered.add(code)
    for row in exchange_rows_canon:
        code = row.get("ts_code")
        if isinstance(code, str) and code.upper().endswith(suffix) and code not in new_by_code:
            covered.add(code)

    if codes is not None:
        allowed = {f"{c}{suffix}" for c in codes}
        covered &= allowed
    return covered


def _code_counters(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, "Counter[tuple[Any, ...]]"]:
    by_code: dict[str, Counter] = {}
    for row in rows:
        code = row.get("ts_code")
        by_code.setdefault(code, Counter())[exchange_key(row)] += 1
    return by_code


def exchange_verdicts(
    residual_items: Sequence[Mapping[str, Any]],
    new_day_rows: Sequence[Mapping[str, Any]],
    exchange_rows_canon: Sequence[Mapping[str, Any]],
    market: str,
    codes: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """钉死的四路判定: 对受覆盖代码的每个 residual 旧行, 设 ``x = exchange_key(旧行)``,
    ``NC``/``EX`` 分别为该代码受覆盖新行/交易所行的 key Counter:

      - ``x in EX`` 且 ``NC[x] < EX[x]`` -> ``miaoxiang_gap``
      - ``x in EX`` 且 ``NC[x] >= EX[x]`` -> ``matched_at_exchange_precision``
      - ``x not in EX`` 且 ``NC == EX`` -> ``old_vendor_error``
      - ``x not in EX`` 且 ``NC != EX`` -> ``new_diverges_from_exchange``

    代码不受覆盖 (:func:`_covered_codes`) 的 residual 行不产出 (调用方按"不在
    返回列表里"计 unverified)。``residual_items`` 里每条至少要有 ``key``, 且
    ``key`` 必须是 block_trade 的六列 key_cols 顺序
    (``ts_code, trade_date, price, vol, buyer, seller`` —— 与 :data:`DOMAIN_CANON`
    里 block_trade 的声明一致, 这也是这个函数只对 block_trade 有意义的原因)。
    """
    covered = _covered_codes(new_day_rows, exchange_rows_canon, market, codes)
    nc_by_code = _code_counters(new_day_rows)
    ex_by_code = _code_counters(exchange_rows_canon)

    out: list[dict[str, Any]] = []
    for item in residual_items:
        key_list = item["key"]
        old_row = dict(zip(("ts_code", "trade_date", "price", "vol", "buyer", "seller"), key_list))
        ts_code = old_row.get("ts_code")
        if ts_code not in covered:
            continue
        x = exchange_key(old_row)
        nc = nc_by_code.get(ts_code, Counter())
        ex = ex_by_code.get(ts_code, Counter())
        if x in ex:
            reason = "miaoxiang_gap" if nc[x] < ex[x] else "matched_at_exchange_precision"
        else:
            reason = "old_vendor_error" if nc == ex else "new_diverges_from_exchange"
        out.append({"key": list(key_list), "reason": reason, "x": list(x)})
    return out


def ceiling_compare(
    new_day_rows: Sequence[Mapping[str, Any]],
    exchange_rows_canon: Sequence[Mapping[str, Any]],
    market: str,
    codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """对每个受覆盖代码算 ``gap = EX - NC`` (交易所有、新表没有) 与
    ``extra = NC - EX`` (新表比交易所多), 按 :func:`exchange_key` 多重集做差
    (``collections.Counter`` 减法, 天然只留正数差)。这条独立于 residual/
    classify_old_keys —— 哪怕新表这一码的旧行全部 matched, ceiling 仍然要单独
    核一遍"新表现在的样子是不是不多不少刚好等于交易所", 因为 matched 只看得到
    "旧行去哪了", 看不到"新表凭空多出来的行"。
    """
    covered = _covered_codes(new_day_rows, exchange_rows_canon, market, codes)
    nc_by_code = _code_counters(new_day_rows)
    ex_by_code = _code_counters(exchange_rows_canon)

    gap_rows: list[dict[str, Any]] = []
    extra_rows: list[dict[str, Any]] = []
    for code in sorted(covered):
        nc = nc_by_code.get(code, Counter())
        ex = ex_by_code.get(code, Counter())
        for key, count in (ex - nc).items():
            gap_rows.append({"key": list(key), "count": count})
        for key, count in (nc - ex).items():
            extra_rows.append({"key": list(key), "count": count})
    return {"codes_checked": sorted(covered), "gap_rows": gap_rows, "extra_rows": extra_rows}


# ------------------------------------------------------------------------ verify --

def _archive_distinct_days(conn: Any, path: Path, *, date_col: str = "trade_date") -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT CAST(\"{date_col}\" AS VARCHAR) "
        f"FROM read_parquet('{Path(path).as_posix()}') ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def _table_day_rows_all(
    conn: Any, table: str, day: str, *, date_col: str = "trade_date"
) -> list[dict[str, Any]]:
    """:func:`classify_old_keys` 要读全列 (不像三方比对那样只读固定 6 列) ——
    canon_row 只动 CanonSpec 声明的列, 其它列原样透传, residual/explained_merged
    的输出理应带全部原始上下文。"""
    cur = conn.execute(f'SELECT * FROM "{table}" WHERE CAST("{date_col}" AS VARCHAR) = ?', [day])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _archive_day_rows_all(
    conn: Any, path: Path, day: str, *, date_col: str = "trade_date"
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"SELECT * FROM read_parquet('{Path(path).as_posix()}') WHERE CAST(\"{date_col}\" AS VARCHAR) = ?",
        [day],
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _classify_all_days(
    conn: Any, domain: str, table: str, *, run_id: str, archive_dir: Path | None
) -> tuple[dict[str, int], list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """对归档 parquet 里每个日期跑一次 :func:`classify_old_keys`, 汇总成
    ``verify()`` 要的 ``classes``/``residual``/``explained_merged`` 三块 (逐条补上
    ``trade_date`` 供跨日阅读)。归档不存在 (这个 run_id 还没 ``prepare`` 过) 时
    三块都是空的, 不是错误 —— 与旧实现同语义。

    额外把每天过了 :func:`canon_row` 的新表行按 ``trade_date`` 存一份返回
    (``new_rows_by_day``) —— 交易所证据层 (V2, 头注 M) 要按天核覆盖范围/NC 计数,
    复用这里已经查过的同一批新表行, 不用再对表重新 SELECT 一遍。
    """
    archive_path = _archive_path(table, run_id, archive_dir=archive_dir)
    old_total = 0
    matched_total = 0
    explained_merged_all: list[dict[str, Any]] = []
    residual_all: list[dict[str, Any]] = []
    new_rows_by_day: dict[str, list[dict[str, Any]]] = {}
    if archive_path.exists():
        for day in _archive_distinct_days(conn, archive_path):
            old_rows = _archive_day_rows_all(conn, archive_path, day)
            new_rows = _table_day_rows_all(conn, table, day)
            new_rows_by_day[day] = [canon_row(domain, r) for r in new_rows]
            report = classify_old_keys(domain, old_rows, new_rows)
            old_total += report.old_total
            matched_total += sum(report.matched.values())
            for item in report.explained_merged:
                explained_merged_all.append({"trade_date": day, **item})
            for item in report.residual:
                residual_all.append({"trade_date": day, **item})

    classes = {
        "old_total": old_total,
        "matched": matched_total,
        "explained_merged": len(explained_merged_all),
        "residual_candidate": sum(1 for r in residual_all if r["kind"] == "candidate"),
        "residual_candidate_amount_close": sum(
            1 for r in residual_all if r["kind"] == "candidate_amount_close"
        ),
        "residual_none": sum(1 for r in residual_all if r["kind"] == "none"),
    }
    return classes, residual_all, explained_merged_all, new_rows_by_day


def _apply_exchange_evidence(
    *,
    residual_all: list[dict[str, Any]],
    explained_merged_all: list[dict[str, Any]],
    new_rows_by_day: Mapping[str, list[dict[str, Any]]],
    exchange_dir: Path,
    vendor_gaps_path: Path | None,
) -> dict[str, Any]:
    """头注 M-O: 按归档里出现的每一天 × {sh, sz} 找
    ``exch_<market>_<day>.json``。有文件就用 :func:`exchange_verdicts` 给该市场
    当天受覆盖代码的 residual 定性、给 :data:`explained_merged_all` 里对应条目打
    ``proved``、用 :func:`ceiling_compare` 核 ceiling; 没文件的 (day, market) 记进
    ``not_checked``, 不算失败。

    ``explained_merged_all``/``residual_all`` 的条目在这里被就地改写 (residual
    items 不产出新对象, 只用于按天分组; explained_merged items 命中时加一个
    ``proved`` 键) —— 调用方 (:func:`verify`) 传进来的就是它要放进最终 report 的
    同一批对象, 原地打标最省事, 不必再拷一份。
    """
    vendor_gaps = load_vendor_gaps(vendor_gaps_path)  # fail-closed: 登记表本身没坏

    residual_by_day: dict[str, list[dict[str, Any]]] = {}
    for item in residual_all:
        residual_by_day.setdefault(item["trade_date"], []).append(item)
    merged_by_day: dict[str, list[dict[str, Any]]] = {}
    for item in explained_merged_all:
        merged_by_day.setdefault(item["trade_date"], []).append(item)

    # 已登记的 block_trade 缺口 (vendor_gaps.yaml, key 是 gap_key 格式) 按
    # trade_date (key 的第 2 个元素) 分组, 供下面逐天核"是不是又冒出来了"。
    registered_by_day: dict[str, list[tuple[str, ...]]] = {}
    for gap_domain, gap_key_tuple in vendor_gaps:
        if gap_domain != "block_trade":
            continue
        registered_by_day.setdefault(gap_key_tuple[1], []).append(gap_key_tuple)

    counts = {
        "unverified": 0,
        "old_vendor_error": 0,
        "miaoxiang_gap_registered": 0,
        "miaoxiang_gap_unregistered": 0,
        "new_diverges_from_exchange": 0,
        "matched_at_exchange_precision": 0,
        "registered_gap_present": 0,
    }
    explained_merged_proved = 0
    exchange_files: list[dict[str, Any]] = []
    checked_pairs: list[list[str]] = []
    not_checked: list[list[str]] = []
    ceiling_gap_registered = 0
    ceiling_gap_unregistered = 0
    ceiling_extra_sh = 0
    ceiling_extra_sz = 0
    ceiling_gap_detail: list[dict[str, Any]] = []
    ceiling_extra_detail: list[dict[str, Any]] = []

    for day, new_rows in new_rows_by_day.items():
        for registered_key in registered_by_day.get(day, []):
            day_gap_keys = {gap_key(exchange_key(r)) for r in new_rows}
            if registered_key in day_gap_keys:
                counts["registered_gap_present"] += 1

        for market in ("sh", "sz"):
            file_path = Path(exchange_dir) / f"exch_{market}_{day}.json"
            if not file_path.exists():
                not_checked.append([day, market])
                continue
            checked_pairs.append([day, market])
            evidence = load_exchange_evidence(file_path)
            exchange_files.append(
                {"path": str(file_path), "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest()}
            )
            codes = evidence.get("codes")
            exchange_rows_canon = [
                canon_exchange_row(market, day, r) for r in evidence["exchange_rows"]
            ]

            suffix = _EXCHANGE_SUFFIX[market]
            day_residuals = [
                item for item in residual_by_day.get(day, [])
                if str(item["key"][0]).upper().endswith(suffix)
            ]
            for v in exchange_verdicts(day_residuals, new_rows, exchange_rows_canon, market, codes=codes):
                reason = v["reason"]
                if reason == "miaoxiang_gap":
                    if ("block_trade", gap_key(tuple(v["x"]))) in vendor_gaps:
                        counts["miaoxiang_gap_registered"] += 1
                    else:
                        counts["miaoxiang_gap_unregistered"] += 1
                else:
                    counts[reason] += 1

            covered = _covered_codes(new_rows, exchange_rows_canon, market, codes)
            for item in merged_by_day.get(day, []):
                ts_code = item["key"][0]
                if ts_code not in covered:
                    continue
                nc = Counter(exchange_key(r) for r in new_rows if r.get("ts_code") == ts_code)
                ex = Counter(exchange_key(r) for r in exchange_rows_canon if r.get("ts_code") == ts_code)
                if nc == ex:
                    item["proved"] = True
                    explained_merged_proved += 1

            ceiling = ceiling_compare(new_rows, exchange_rows_canon, market, codes=codes)
            for row in ceiling["gap_rows"]:
                if ("block_trade", gap_key(tuple(row["key"]))) in vendor_gaps:
                    ceiling_gap_registered += 1
                else:
                    ceiling_gap_unregistered += 1
                    ceiling_gap_detail.append({"trade_date": day, "market": market, **row})
            extra_count = sum(row["count"] for row in ceiling["extra_rows"])
            if extra_count:
                if market == "sh":
                    ceiling_extra_sh += extra_count
                else:
                    ceiling_extra_sz += extra_count
                ceiling_extra_detail.extend(
                    {"trade_date": day, "market": market, **row} for row in ceiling["extra_rows"]
                )

    # 剩下没被上面任何一次 exchange_verdicts 领走的 residual (代码后缀不是
    # .SH/.SZ、代码不受覆盖、或那天那市场压根没有证据文件), 仍然是 V1 的
    # unverified。
    handled = (
        counts["old_vendor_error"] + counts["miaoxiang_gap_registered"]
        + counts["miaoxiang_gap_unregistered"] + counts["new_diverges_from_exchange"]
        + counts["matched_at_exchange_precision"]
    )
    counts["unverified"] = len(residual_all) - handled

    return {
        "counts": counts,
        "explained_merged_proved": explained_merged_proved,
        "exchange_files": exchange_files,
        "ceiling": {
            "days_checked": checked_pairs,
            "not_checked": not_checked,
            "gap_registered": ceiling_gap_registered,
            "gap_unregistered": ceiling_gap_unregistered,
            "extra_sh": ceiling_extra_sh,
            "extra_sz": ceiling_extra_sz,
            "gap_detail": ceiling_gap_detail,
            "extra_detail": ceiling_extra_detail,
        },
    }


def verdict(report: Mapping[str, Any]) -> int:
    """头注 I/N/O: 只读 ``report`` 的纯函数, 不重新计算、不连库。

    3 (硬失败): 任一结构检查 (S1-S4) 不通过; 或 residual 里出现"新表已经比交易所
    还缺" (``new_diverges_from_exchange``) / "妙想缺口没登记"
    (``miaoxiang_gap_unregistered``); 或 ceiling 缺口没登记
    (``ceiling.gap_unregistered``) / 沪市 ceiling 多出未解释的成交
    (``ceiling.extra_sh``); 或已登记的缺口在新表里又冒出来了
    (``registered_gap_present``) —— 这几类无论有没有 unverified 剩余都直接判死,
    ``--record`` 会拒绝写账。
    2 (需要人工): 上面都干净, 但还有 ``unverified`` residual 没过 vendor_gaps
    消费, 或深市 ceiling 多出的成交 (``ceiling.extra_sz`` —— CATALOGID=1265 只覆盖
    协议交易, 新表比它多是合法的, 只需要人工看一眼不是硬伤)。
    0: 其余。

    ``report`` 里没有 ``ceiling``/``registered_gap_present`` (没传 ``exchange_dir``
    的调用, 或 V1 遗留的字面 report dict) 时按全零对待, 不影响判定。
    """
    structural = report["structural"]
    counts = report["residual_verdict_counts"]
    ceiling = report.get("ceiling") or {}
    structural_bad = any(not check["ok"] for check in structural.values())
    if (
        structural_bad
        or counts["new_diverges_from_exchange"] > 0
        or counts["miaoxiang_gap_unregistered"] > 0
        or ceiling.get("gap_unregistered", 0) > 0
        or ceiling.get("extra_sh", 0) > 0
        or counts.get("registered_gap_present", 0) > 0
    ):
        return 3
    if counts["unverified"] > 0 or ceiling.get("extra_sz", 0) > 0:
        return 2
    return 0


def verify(
    domain: str,
    *,
    run_id: str,
    record: bool = False,
    conn: Any = None,
    archive_dir: Path | None = None,
    exchange_dir: Path | None = None,
    vendor_gaps_path: Path | None = None,
) -> dict[str, Any]:
    """§3.4 (验收判据重落, 头注 G-O): 逐日三分类 + 结构检查 + (可选) 交易所证据层
    定性, 汇总出 ``exit_code`` (:func:`verdict`)。

    ``exchange_dir`` 只对 ``block_trade`` 有意义 (头注 M) —— 其它域传了直接
    ``ValueError``, 在动库/取锁之前就抛, CLI 层 (:func:`main`) 捕获后返回 1。

    ``record=True`` 时锁覆盖整个校验过程 (读的状态和记的账必须是同一个快照);
    ``exit_code != 0`` 直接 ``RuntimeError``, 不写记账 —— 校验没过就不能宣称"通过
    验收"。``exit_code == 0`` 才 :func:`load_vendor_gaps` (顺带校验这份登记表本身
    没坏) 算它的 sha256 存进记账, 写 ``rows_replaced_verified`` (verification_json
    追加 ``exchange_files``/``ceiling``, 头注 M-O)。

    ``record=False`` (默认) 保持只读连接, 不取锁。``vendor_gaps_path`` 未给时两处
    都退回仓库真实路径 :data:`VENDOR_GAPS_PATH` (与 CLI 用法一致)。

    ``conn``/``archive_dir`` 与 :func:`prepare` 同理, 为测试注入而加。
    """
    table = _domain_table(domain)
    if exchange_dir is not None and domain != "block_trade":
        raise ValueError(
            f"exchange_dir 目前只支持 domain='block_trade' (交易所证据层只覆盖沪深"
            f"逐笔), got domain={domain!r}"
        )
    owns_conn = conn is None

    def _build_report(active_conn: Any) -> dict[str, Any]:
        structural = structural_checks(active_conn, domain, table)
        classes, residual_all, explained_merged_all, new_rows_by_day = _classify_all_days(
            active_conn, domain, table, run_id=run_id, archive_dir=archive_dir
        )
        if exchange_dir is not None:
            evidence = _apply_exchange_evidence(
                residual_all=residual_all,
                explained_merged_all=explained_merged_all,
                new_rows_by_day=new_rows_by_day,
                exchange_dir=exchange_dir,
                vendor_gaps_path=vendor_gaps_path,
            )
            residual_verdict_counts = evidence["counts"]
            classes["explained_merged_proved"] = evidence["explained_merged_proved"]
            exchange_files = evidence["exchange_files"]
            ceiling = evidence["ceiling"]
        else:
            # 没给 exchange_dir: 与 V1 同语义, 全部 residual 落 unverified, 交易所
            # 相关计数/ceiling 全零, exchange_files 为空 (头注 I/M)。
            residual_verdict_counts = {
                "unverified": len(residual_all),
                "old_vendor_error": 0,
                "miaoxiang_gap_registered": 0,
                "miaoxiang_gap_unregistered": 0,
                "new_diverges_from_exchange": 0,
                "matched_at_exchange_precision": 0,
                "registered_gap_present": 0,
            }
            classes["explained_merged_proved"] = 0
            exchange_files = []
            ceiling = {
                "days_checked": [], "not_checked": [], "gap_registered": 0,
                "gap_unregistered": 0, "extra_sh": 0, "extra_sz": 0,
                "gap_detail": [], "extra_detail": [],
            }
        result: dict[str, Any] = {
            "domain": domain,
            "table": table,
            "classes": classes,
            "residual": residual_all,
            "explained_merged": explained_merged_all,
            "structural": structural,
            "residual_verdict_counts": residual_verdict_counts,
            "ceiling": ceiling,
            "exchange_files": exchange_files,
        }
        result["exit_code"] = verdict(result)
        return result

    if record:
        with writer_lock("reland_event_domain"):
            if owns_conn:
                conn = connect(str(db_path("tushare_raw")), read_only=False)
            try:
                result = _build_report(conn)
                if result["exit_code"] != 0:
                    raise RuntimeError(
                        f"verify exit_code={result['exit_code']} != 0; refusing to record "
                        f"(domain={domain} run_id={run_id})"
                    )
                load_vendor_gaps(vendor_gaps_path)  # 校验登记表本身没坏; 坏了直接 ValueError
                gaps_path = Path(vendor_gaps_path) if vendor_gaps_path is not None else VENDOR_GAPS_PATH
                vendor_gaps_sha256 = hashlib.sha256(gaps_path.read_bytes()).hexdigest()
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name=table,
                    delete_scope="rows_replaced_verified",
                    reason="grain 契约重落验收 exit_code=0 (分类+结构检查+交易所)",
                    verification={
                        "classes": result["classes"],
                        "residual_verdict_counts": result["residual_verdict_counts"],
                        "structural_ok": {k: v["ok"] for k, v in result["structural"].items()},
                        "vendor_gaps_sha256": vendor_gaps_sha256,
                        "exchange_files": result["exchange_files"],
                        "ceiling": result["ceiling"],
                    },
                )
                return result
            finally:
                if owns_conn:
                    conn.close()

    if owns_conn:
        conn = connect(str(db_path("tushare_raw")), read_only=True)
    try:
        return _build_report(conn)
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

    p_verify = sub.add_parser("verify", help="逐日验收 (read_only 默认)")
    p_verify.add_argument("--domain", required=True, choices=sorted(DOMAIN_TABLES))
    p_verify.add_argument("--run-id", required=True)
    p_verify.add_argument("--exchange-dir", type=Path, default=None)
    p_verify.add_argument("--vendor-gaps", type=Path, default=None)
    p_verify.add_argument("--record", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        result = prepare(args.domain, run_id=args.run_id, execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    try:
        result = verify(
            args.domain,
            run_id=args.run_id,
            record=args.record,
            exchange_dir=args.exchange_dir,
            vendor_gaps_path=args.vendor_gaps,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
