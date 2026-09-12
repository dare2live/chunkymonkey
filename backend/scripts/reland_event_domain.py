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
     (需要人工过一遍, 决定是旧供应商的错、还是要登记进格级登记表, 见 J); 否则 0。
     (这一段 V1 时写的是"登记进 vendor_gaps"与"residual 一律落 unverified, 拿
     vendor_gaps 消费是后续切片的活" —— C2 起唯一登记通道是 J 说的
     ``exchange_cell_verdicts.yaml``, 消费逻辑见 N, 那两句已不成立, 故改写。)
  J. (2026-09-12, 格级验收判据切片 C2, 取代 V1/V2 的 ``vendor_gaps.yaml``) 缺口/
     多出/文本差异的唯一登记通道现在是格级登记表
     ``backend/config/exchange_cell_verdicts.yaml`` (``services.exchange_cell_
     verdicts.load_exchange_cell_verdicts``/``consume``, 该模块已独立提交, 本文件
     只调用, 不重写)。取代理由 (``scratchpad/bt_residual_classes_r1.md`` §0/§3):
     ``vendor_gaps.yaml`` 的语义只能表达"妙想整行缺一笔", 装不下"同一笔两侧都在只是
     席位名写法不同" (text) 与"供应商同一笔报了两次" (extra_duplicate/phantom) 这两类
     残差 —— 这两类在同一个 T 格 (trade_date, venue, code6, price2dp, vol2dp) 里可能
     和缺口同时出现, 分两份登记会导致同一格被两处登记、互相不知道对方消费了哪一行。
     命令行仍然不给放行旗子, 理由不变: 一次性 flag 钉不住"两侧当时到底是什么行",
     下次供应商数据变了同一个 flag 还会继续放行一个它从没见过的新形状。
  K. (V2, 验收判据切片二: 交易所证据层) ``canon_exchange_row`` 签名改为
     ``(market, trade_date, row)`` —— 直接把 ``trade_date`` 并进输出, 不再返回
     ``amount`` (交易所证据层只用 :func:`exchange_key` 六个可比字段判等, 不再做
     金额层面的三方比对; V2 当时还有 ``gap_key`` 把这六个字段转成登记 key 字符串,
     C2 把它连同它服务的 ``vendor_gaps.yaml`` 一起删除, 见 J)。旧签名
     ``(row, *, market)`` 连同它唯一的调用方 ``compare_day_three_way``/
     ``_three_way_for_date`` (--exchange-json/--date 单日路径) 一并删除, 不留墓碑
     —— 这是本切片规格点名允许改写的三个符号。
  L. 覆盖范围判断 (哪些代码算"这份交易所文件管得到") 统一收在私有
     :func:`_covered_codes` 里, :func:`exchange_verdicts` 与 (C2 起) :func:`_apply_
     exchange_evidence` 里 :func:`cell_compare` 的调用方都调它, 不各自重写一遍规则
     (免得两处判断不一致): 后缀须与 ``market`` 一致 (.SH/.SZ); 新表里能查到
     ``security_type`` 的按 ``{EQA, FDO}`` 白名单过 (BD0/其它不覆盖); 代码只在
     交易所出现、新表没有时按后缀直接算覆盖 (没有 security_type 可查, 也没有理由
     怀疑交易所自己报错); 文件带 ``codes`` 白名单时再交一遍求交集。C2 起
     :func:`_apply_exchange_evidence` 在调它之前先按 :func:`resolve_venue` 把行的
     ``ts_code`` 规范化成确认场所的后缀形式 (头注 P), 所以这里的"后缀"判断对
     ``.OF`` 这类原本无后缀的行同样生效, 不再是 V2 遗留的盲区 (bt_residual_
     classes_r1.md N5)。
  M. ``verify`` 新增 ``exchange_dir`` (C2 起另加 ``cell_verdicts_path``/
     ``code_changes_path``/``emit_candidates_path``, 取代 V1/V2 的
     ``vendor_gaps_path``, 见 J): 只有 ``block_trade`` 允许传 ``exchange_dir``
     (``top_inst`` 传了直接 ``ValueError``, CLI 层 ``main`` 捕获后返回 1, 不是让
     异常裸抛把退出码交给解释器)。按归档里出现的每一天 × {sh, sz} 找
     ``exch_<market>_<day>.json``; 文件不存在不是失败 —— 那天那个市场的残差保持
     unverified/无格可比, 只在 ``report["not_checked"]`` 里记一笔 (day, market),
     供人工知道"这些天这些市场我们其实没有交易所口径可核对" (C2 起 ``not_checked``
     非空本身也会把 exit_code 降到 2, 见 :func:`verdict`, 免得空目录也能判 0)。
  N. (C2 取代) 格级比较 (:func:`cell_compare`) 取代 :func:`ceiling_compare`
     (已删除, 不留墓碑): 按 ``(trade_date, venue, code6, price2dp, vol2dp)`` 分格,
     每格两侧未匹配行是多重集 (``services.exchange_cell_verdicts.Observed``), 交给
     ``consume()`` 用格级登记表判 consumed/unregistered/stale/contradiction。未登记
     缺口 (``missing_unregistered``) 或任一格 stale/contradiction 都是硬失败 (exit
     3); 未登记的文本差异/多出的成交 (``text_candidate``/``extra_unregistered``) 只
     是待裁决 (exit 2) —— 不再有"沪判死/深看一眼"的整市场级差别对待 (见 O)。
     旧的四路判定 :func:`exchange_verdicts` (miaoxiang_gap/old_vendor_error/
     new_diverges_from_exchange/matched_at_exchange_precision) 保留、逻辑不变, 只
     是它的 miaoxiang_gap "是否已登记" 现在改查格级 ``ConsumptionReport`` (该旧行
     的 exchange_key 是否落在某个已被 consumed 且判定为 missing 的格里), 不再查
     vendor_gaps; ``new_diverges_from_exchange`` 不再单独影响退出码 (它的机器含义
     已被格级比较完整覆盖, 双判会让同一事实产生两个不同退出码, 见 :func:`verdict`)。
  O. (C2 取代) 沪深不再整市场级差别对待"新表比交易所多"的成交: 深交所盘后定价没有
     逐笔可查确实是真实存在的合法差异, 但那是**逐格**的事实 (某笔恰好是盘后定价),
     不是"深市所有多出的成交都从轻发落"——旧版按市场整片降级会让深市真正的重复/错误
     行也被放过。现在一律走格级登记, `truth_side: vendor` 且带证据 (写明是盘后定价
     报表里的哪一行) 才能把某笔多出的成交标记为"待发布层保留", 其余未登记的多出成交
     一律待裁决 (exit 2), 不因市场而异。证据文件的取数入口与格式钉在
     :func:`load_exchange_evidence` 的头注里 (本文件), 不在任何文档小节 —— 别处
     没有第二份定义。
  P. (C2 新增) 场所归属 (R-V, ``resolve_venue``): 供应商行的场所 = 代码后缀
     (.SH/.SZ/.BJ) 优先; 后缀缺失 (如 ``.OF``) 时退回 ``vendor_market`` 字段
     (``TRADE_MARKET_OLD``, 适配器已落) 按 :data:`_VENDOR_MARKET_MAP` 映射; 两者都
     缺 → unresolved, 两者都有但不一致 → conflict。unresolved/conflict 的行既不进
     任何格比较, 也会把该 (日, code6) 在交易所侧的对应行挡在比较之外 (计入
     ``venue.blocked_exchange_rows``, 不判 missing —— 免得我们自己的归属缺陷冒充
     供应商缺口), 只在 ``venue.unresolved``/``venue.conflict`` 计数, 退出码降到 2
     (待裁决), 不静默放过也不误判为硬失败。映射表本身是供应商表示事实 (裁定 D 同
     类), 写成 Python 常量, 不进 YAML (CLAUDE.md 红线 11: YAML 只管"检查什么"，这是
     "供应商行上那个字段的取值该翻成哪个市场"这种事实)。
  Q. (C2 新增) 身份层 (R-I, :func:`identity_pass`): 换码事件登记
     (``backend/config/security_code_changes.yaml``, ``services.security_identity.
     load_security_code_changes``) 在旧行/新行两侧各自独立施加 (asof_identity_r1.md
     §3.3, §6 ID5: 归档旧表也有回写 twin, 不能只处理新表一侧)。同一天、同一自然键
     (block_trade: price/vol/buyer/seller) 下, 若换码事件的新代码与旧代码各出现一次
     全同的行, 判定为"重复回写" (twin, 丢弃新代码那一行); 若只出现新代码没有旧代码
     的对应行, 判定为"remap" (把该行的 ts_code 改记成旧代码, 原代码存进
     ``vendor_code``)。事件表没给 (``--code-changes`` 缺省) 时身份层整体
     ``skipped``, 不阻断验收 —— 未被识别的 twin 会在格级比较里表现成一格
     "新表多出的行" (``extra_unregistered``), 待裁决而不是静默吞掉。

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
from services.exchange_cell_verdicts import (  # noqa: E402
    CellKey,
    CellVerdictSet,
    Observed,
    consume,
    load_exchange_cell_verdicts,
)
from services.security_identity import CodeChangeSet, load_security_code_changes  # noqa: E402
from services.writer_lock import writer_lock  # noqa: E402

ARCHIVE_DIR = ROOT / "data" / "archive" / "lifecycle"  # gitignored (r2 §3.2), 同
                                                        # db_lifecycle_delete 的归档目录
SYNC_REGISTRY_PATH = ROOT / "backend" / "config" / "sync_registry.yaml"

DOMAIN_TABLES: dict[str, str] = {
    "block_trade": "raw_tushare_block_trade",
    "top_inst": "raw_tushare_top_inst",
}
# §3.2 步骤 3 (业主裁定 A): prepare 只加列, 已存在则跳过 (幂等)。类型显式给
# INTEGER/VARCHAR —— 若靠 sync_runner._write_batch 的新列推断会一律建成 VARCHAR
# (r2 §0.5 实测: sync_runner.py:1760), 必须一次性 DDL 建对, 不能等 runner 自己补。
DOMAIN_DDL_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "block_trade": (
        ("seq", "INTEGER"), ("security_type", "VARCHAR"), ("trade_unit", "VARCHAR"),
        ("vendor_market", "VARCHAR"),
    ),
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
    # C2 (bt_residual_classes_r1.md §6): 格级比较用的两组列。cell_cols 是 T 格粒度
    # (与 code6/trade_date/venue 一起构成 CellKey); text_cols 是格内"文本差异"比较
    # 的列 (席位名)。这两个字段与 services.exchange_cell_verdicts._DOMAIN_CELL_COLS
    # 是有意分开声明的两份真相 (该模块不连库、不导入脚本层, 解耦), 用
    # test_domain_cell_cols_matches_registry_declaration 钉住两处不漂。空元组
    # (top_inst) 表示该域未声明格列, 登记 loader 对它 fail-closed (L5)。
    cell_cols: tuple[str, ...]
    text_cols: tuple[str, ...]


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
        cell_cols=("price", "vol"),
        text_cols=("buyer", "seller"),
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
        cell_cols=(),
        text_cols=(),
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


# ------------------------------------------------------------------ venue (R-V) --

# 供应商场所字段 (``vendor_market``, 落自妙想 ``TRADE_MARKET_OLD``) -> 场所代号的
# 映射。这是供应商自己对"这笔成交在哪个场所"的一手声明 (与 K 线/事件表无关), 属于
# 头注 D 同类的表示事实, 写成 Python 常量, 不进 YAML (红线 11 管"检查什么"，不管
# "对方接口这个字段的取值该翻成哪个场所")。
_VENDOR_MARKET_MAP: dict[str, str] = {"CNSESH": "sh", "CNSESZ": "sz", "CNSEBJ": "bj"}
_CODE_SUFFIX_VENUE: dict[str, str] = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


def resolve_venue(ts_code: Any, vendor_market: Any) -> tuple[str | None, str]:
    """R-V (头注 P): 代码后缀优先; 后缀缺失 (如 ``.OF``) 退回 ``vendor_market`` 映射;
    两者都缺 -> ``unresolved``；两者都有但翻译出的场所不一致 -> ``conflict``
    (此时仍以后缀为准返回 venue, 调用方按 status 判断要不要用这个 venue)。

    返回 ``(venue, status)``, ``status`` ∈ {"suffix", "by_vendor_market", "conflict",
    "unresolved"}。不做"按 6 位码在交易所文件里找得到就归该场所"这种反推 —— 那是拿
    比对结果反推归属 (bt_residual_classes_r1.md §2.1 R-V 末段), 只用供应商自己给的
    两个一手字段。
    """
    suffix_venue: str | None = None
    if isinstance(ts_code, str):
        upper = ts_code.upper()
        if "." in upper:
            suffix_venue = _CODE_SUFFIX_VENUE.get(upper.rsplit(".", 1)[-1])
    mapped_venue = (
        _VENDOR_MARKET_MAP.get(vendor_market) if isinstance(vendor_market, str) else None
    )
    if suffix_venue is not None:
        if mapped_venue is not None and mapped_venue != suffix_venue:
            return suffix_venue, "conflict"
        return suffix_venue, "suffix"
    if mapped_venue is not None:
        return mapped_venue, "by_vendor_market"
    return None, "unresolved"


# ---------------------------------------------------------------- identity (R-I) --

# 每个域的换码 twin/remap 判定用的"自然键" (asof_identity_r1.md §3.3 R1, IdentitySpec
# .natural_key)。S1 (services/security_identity.py 里给两个域声明 IDENTITY_SPECS)
# 与本文件是并行施工的两个切片 (asof S1 未合入前本文件不依赖它), 这里独立声明一份同
# 名概念 —— 与 CanonSpec.cell_cols/_DOMAIN_CELL_COLS 的两处真相同一性质 (有意解耦,
# 不是遗漏同步); S1 合入后若两处取值不一致, 属于后续切片要接线的事, 不在本片处理。
_IDENTITY_NATURAL_KEY: dict[str, tuple[str, ...]] = {
    "block_trade": ("price", "vol", "buyer", "seller"),
    "top_inst": ("exalter", "side", "reason", "board_rank", "buy", "sell"),
}


@dataclass(frozen=True)
class IdentityReport:
    dropped: list[dict[str, Any]]
    remapped: list[dict[str, Any]]
    skipped: bool


def identity_pass(
    domain: str, rows: Sequence[Mapping[str, Any]], events: CodeChangeSet | None
) -> tuple[list[dict[str, Any]], IdentityReport]:
    """R-I (头注 Q, asof_identity_r1.md §3.3 R1/R2, bt_residual_classes_r1.md §6 C2
    ``identity_pass`` 签名)。纯函数, 不开库; 调用方对旧行/新行各自独立调用一次
    (asof §6 ID5: 归档旧表也可能有回写 twin, 双侧都要过)。

    对每一行: 若其 ``ts_code`` 是某条事件的 ``new_code`` 且 ``trade_date`` 早于
    ``effective_date`` (R1/R2 只处理生效前; d >= effective 的行原样放行, 交给 R3/R4
    在发布层判定, 与本函数无关):

    - 同一天内能找到一行 ``ts_code == old_code`` 且按该域 ``_IDENTITY_NATURAL_KEY``
      全部列都相等的行 (逐个消费, 不重复配对同一行两次) -> 判定为 twin, 该行丢弃
      (``dropped``);
    - 找不到 -> 判定为 remap: 该行 ``ts_code`` 改写成 ``old_code``, 原代码存进
      ``vendor_code`` (``remapped``)。

    ``events`` 为 ``None`` (未给 ``--code-changes``) 或该域没有声明自然键时,
    ``skipped=True``, 行原样返回 —— 未被识别的 twin 会在后续格级比较里表现为一格
    "新表多出的行" (待裁决), 不会被静默吞掉。
    """
    natural_key = _IDENTITY_NATURAL_KEY.get(domain, ())
    skipped = events is None
    by_new: Mapping[str, Any] = {} if skipped else dict(getattr(events, "by_new", {}) or {})
    if skipped or not natural_key or not by_new:
        return list(rows), IdentityReport(dropped=[], remapped=[], skipped=skipped)

    canon = [canon_row(domain, r) for r in rows]
    by_day: dict[Any, list[int]] = {}
    for idx, c in enumerate(canon):
        by_day.setdefault(c.get("trade_date"), []).append(idx)

    drop_set: set[int] = set()
    remap_to: dict[int, str] = {}
    dropped: list[dict[str, Any]] = []
    remapped: list[dict[str, Any]] = []

    for day, idxs in by_day.items():
        by_code_key: dict[tuple[Any, tuple[Any, ...]], list[int]] = {}
        for idx in idxs:
            c = canon[idx]
            k = tuple(c.get(col) for col in natural_key)
            by_code_key.setdefault((c.get("ts_code"), k), []).append(idx)

        consumed_twins: set[int] = set()
        for idx in idxs:
            c = canon[idx]
            code = c.get("ts_code")
            event = by_new.get(code)
            if event is None:
                continue
            if day is None or not (str(day) < event.effective_date):
                continue  # d >= effective (or day missing): 不进身份层 (R3/R4 之外)
            key = tuple(c.get(col) for col in natural_key)
            candidates = by_code_key.get((event.old_code, key), [])
            twin_idx = next((t for t in candidates if t not in consumed_twins and t != idx), None)
            if twin_idx is not None:
                consumed_twins.add(twin_idx)
                drop_set.add(idx)
                dropped.append(
                    {"vendor_code": code, "old_code": event.old_code, "trade_date": day, "key": list(key)}
                )
            else:
                remap_to[idx] = event.old_code
                remapped.append(
                    {"vendor_code": code, "old_code": event.old_code, "trade_date": day, "key": list(key)}
                )

    out: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if idx in drop_set:
            continue
        if idx in remap_to:
            new_row = dict(row)
            new_row["vendor_code"] = row.get("ts_code")
            new_row["ts_code"] = remap_to[idx]
            out.append(new_row)
        else:
            out.append(row)

    return out, IdentityReport(dropped=dropped, remapped=remapped, skipped=False)


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
    ``amount`` —— 交易所证据层只用 :func:`exchange_key` 六个字段判等 (头注 K)。
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
    """加载并严格校验一份交易所证据文件 (格式由本函数钉死, 没有第二份定义)。

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


# --------------------------------------------------------------- exchange coverage --

def _covered_codes(
    new_day_rows: Sequence[Mapping[str, Any]],
    exchange_rows_canon: Sequence[Mapping[str, Any]],
    market: str,
    codes: Sequence[str] | None = None,
) -> set[str]:
    """头注 L: 这份交易所文件"管得到"哪些代码 (钉死的四条规则统一在这一处判断,
    :func:`exchange_verdicts`/:func:`cell_compare` 的调用方 (:func:`_apply_exchange_
    evidence`) 都调它)。
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


def _venue_normalize_row(row: Mapping[str, Any], *, code6: str, market: str) -> dict[str, Any]:
    """把一行的 ``ts_code`` 改写成"该行已经确认属于 ``market`` 这个场所"的规范
    形状 (``{code6}.{SH|SZ}``), 原始供应商代码存进 ``vendor_code`` (不覆盖
    :func:`identity_pass` 可能已经写过的 ``vendor_code``)。这样 :func:`_covered_codes`
    /:func:`exchange_verdicts`/:func:`_code_counters` 这些按后缀字符串比较的既有
    函数可以原样复用在 (venue, code6) 已经解析好的行上 (含 ``.OF`` 这类原本没有
    后缀的行), 不需要重写它们成 code6 版本 —— 头注 N/P: R-V 解析出的 venue 才是
    比较用的场所, 不是供应商原始后缀。
    """
    out = dict(row)
    out.setdefault("vendor_code", row.get("ts_code"))
    out["ts_code"] = f"{code6}{_EXCHANGE_SUFFIX[market]}"
    return out


def _cell_key_of(row: Mapping[str, Any], *, venue: str) -> CellKey | None:
    """一行 (已按 venue 规范化 ts_code, 见 :func:`_venue_normalize_row`) -> 它所属
    的 T 格 :data:`CellKey`。缺代码/日期/价/量任一项时返回 ``None`` (调用方跳过,
    不产出一个残缺格)。"""
    ts_code = row.get("ts_code")
    trade_date = row.get("trade_date")
    price = row.get("price")
    vol = row.get("vol")
    if not isinstance(ts_code, str) or len(ts_code) < 6 or trade_date is None:
        return None
    if price is None or vol is None:
        return None
    code6 = ts_code[:6]
    return (str(trade_date), venue, code6, f"{_vol_2dp(price):.2f}", f"{_vol_2dp(vol):.2f}")


def cell_compare(
    new_rows: Sequence[Mapping[str, Any]],
    exchange_rows_canon: Sequence[Mapping[str, Any]],
    venue: str,
) -> dict[CellKey, Observed]:
    """C2 (bt_residual_classes_r1.md §3 C2): 取代 :func:`ceiling_compare` (已删除)。
    按 :data:`CellKey` (trade_date, venue, code6, price2dp, vol2dp) 分格比较两侧
    的 (buyer, seller) 多重集, 返回给 ``services.exchange_cell_verdicts.consume``
    消费的 ``Mapping[CellKey, Observed]``。

    调用方必须已经把 ``new_rows``/``exchange_rows_canon`` 过滤 + 规范化到"确认属于
    ``venue`` 这个场所"(:func:`resolve_venue` + :func:`_venue_normalize_row`,
    :func:`_covered_codes` 覆盖范围判断) —— 这个函数本身不做场所判断, 只管分格,
    不重复调用方已经做过的判定 (venue.unresolved/conflict 计数是同一份判定, 不在
    这里第二次做)。
    """
    new_by_cell: dict[CellKey, Counter] = {}
    for row in new_rows:
        cell = _cell_key_of(row, venue=venue)
        if cell is None:
            continue
        new_by_cell.setdefault(cell, Counter())[(row.get("buyer"), row.get("seller"))] += 1

    exch_by_cell: dict[CellKey, Counter] = {}
    for row in exchange_rows_canon:
        cell = _cell_key_of(row, venue=venue)
        if cell is None:
            continue
        exch_by_cell.setdefault(cell, Counter())[(row.get("buyer"), row.get("seller"))] += 1

    result: dict[CellKey, Observed] = {}
    for cell in set(new_by_cell) | set(exch_by_cell):
        nc = new_by_cell.get(cell, Counter())
        ex = exch_by_cell.get(cell, Counter())
        result[cell] = Observed(gap=ex - nc, extra=nc - ex, exchange_all=Counter(ex))
    return result


def _cell_missing_rows(entry: Any) -> "Counter[tuple[str, str]]":
    """一条已登记 entry 的 ``exchange_unmatched`` 按 ``text_pairs`` 消费后剩余的
    "missing" 行多重集 (:func:`services.exchange_cell_verdicts._consume_cell` 同一份
    逻辑的只读重放, 用于 :func:`exchange_verdicts` 的 miaoxiang_gap 是否已登记查询,
    头注 N —— 不改 ``exchange_cell_verdicts.py``, 只在这里按公开字段
    ``exchange_unmatched``/``text_pairs`` 重算, 不引入第二份状态)。"""
    ex_consumed = [0] * len(entry.exchange_unmatched)
    for ex_idx, _vn_idx, count in entry.text_pairs:
        ex_consumed[ex_idx] += count
    missing: Counter = Counter()
    for (row, count), consumed in zip(entry.exchange_unmatched, ex_consumed):
        remaining = count - consumed
        if remaining > 0:
            missing[row] += remaining
    return missing


def emit_candidate_verdicts(
    observed_by_cell: Mapping[CellKey, Observed],
    verdicts: CellVerdictSet,
    path: Path,
    *,
    domain: str = "block_trade",
) -> None:
    """``--emit-candidates``: 把全部未登记 (不在 ``verdicts.by_cell`` 里) 且仍有
    残差 (gap 或 extra 非空) 的格按 §3.1 形状写出 YAML 骨架, 供人工逐条判定后搬进
    仓库登记表。

    骨架里 ``truth_side``/``evidence`` 给占位值 (合法但明显是待填), ``checked_at``
    留 ``None`` —— 特意只让 ``checked_at`` 这一项无法通过
    :func:`services.exchange_cell_verdicts.load_exchange_cell_verdicts` (V23), 逼
    人工填一个真实核对日期才能让骨架变成生效的登记, 不会有人误把骨架原样提交。
    """
    entries: list[dict[str, Any]] = []
    for cell in sorted(observed_by_cell.keys()):
        if cell in verdicts.by_cell:
            continue
        obs = observed_by_cell[cell]
        if not obs.gap and not obs.extra:
            continue
        entries.append(
            {
                "domain": domain,
                "cells": [list(cell)],
                "exchange_unmatched": [[b, s, c] for (b, s), c in sorted(obs.gap.items())],
                "vendor_unmatched": [[b, s, c] for (b, s), c in sorted(obs.extra.items())],
                "text_pairs": [],
                "truth_side": "unknown",
                "evidence": "TODO: fill in evidence before registering (emitted skeleton)",
                "checked_at": None,
            }
        )
    doc = {"version": 1, "entries": entries}
    Path(path).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


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
    conn: Any, domain: str, table: str, *, run_id: str, archive_dir: Path | None,
    ccs: CodeChangeSet | None = None,
) -> tuple[
    dict[str, int], list[dict[str, Any]], list[dict[str, Any]],
    dict[str, list[dict[str, Any]]], dict[str, Any],
]:
    """对归档 parquet 里每个日期跑一次 :func:`classify_old_keys`, 汇总成
    ``verify()`` 要的 ``classes``/``residual``/``explained_merged`` 三块 (逐条补上
    ``trade_date`` 供跨日阅读)。归档不存在 (这个 run_id 还没 ``prepare`` 过) 时
    三块都是空的, 不是错误 —— 与旧实现同语义。

    额外把每天过了 :func:`canon_row` 的新表行按 ``trade_date`` 存一份返回
    (``new_rows_by_day``) —— 交易所证据层 (V2, 头注 M) 要按天核覆盖范围/NC 计数,
    复用这里已经查过的同一批新表行, 不用再对表重新 SELECT 一遍。

    C2 (头注 Q): 旧行/新行在 :func:`classify_old_keys` 之前各自先过一次
    :func:`identity_pass` (``ccs`` 为 ``None`` 时是 no-op, ``identity_pass`` 自己
    报告 ``skipped``), 逐日累加成 ``identity_totals``
    (``dropped_old/dropped_new/remapped_old/remapped_new/skipped``) 一并返回。
    """
    archive_path = _archive_path(table, run_id, archive_dir=archive_dir)
    old_total = 0
    matched_total = 0
    explained_merged_all: list[dict[str, Any]] = []
    residual_all: list[dict[str, Any]] = []
    new_rows_by_day: dict[str, list[dict[str, Any]]] = {}
    identity_totals: dict[str, Any] = {
        "dropped_old": 0, "dropped_new": 0, "remapped_old": 0, "remapped_new": 0,
        "skipped": ccs is None,
    }
    if archive_path.exists():
        for day in _archive_distinct_days(conn, archive_path):
            old_rows_raw = _archive_day_rows_all(conn, archive_path, day)
            new_rows_raw = _table_day_rows_all(conn, table, day)
            old_rows, old_id_report = identity_pass(domain, old_rows_raw, ccs)
            new_rows, new_id_report = identity_pass(domain, new_rows_raw, ccs)
            identity_totals["dropped_old"] += len(old_id_report.dropped)
            identity_totals["dropped_new"] += len(new_id_report.dropped)
            identity_totals["remapped_old"] += len(old_id_report.remapped)
            identity_totals["remapped_new"] += len(new_id_report.remapped)
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
    return classes, residual_all, explained_merged_all, new_rows_by_day, identity_totals


def _apply_exchange_evidence(
    *,
    residual_all: list[dict[str, Any]],
    explained_merged_all: list[dict[str, Any]],
    new_rows_by_day: Mapping[str, list[dict[str, Any]]],
    exchange_dir: Path,
    cell_verdicts_path: Path | None,
) -> dict[str, Any]:
    """C2 (头注 M-Q, bt_residual_classes_r1.md §3/§6): 按归档里出现的每一天先做
    场所归属 (R-V, :func:`resolve_venue`), 再按 {sh, sz} 找
    ``exch_<market>_<day>.json``。有文件就: (1) 用 :func:`exchange_verdicts` 给该
    市场当天受覆盖代码的旧 residual 定性 (逻辑不变, 只是 miaoxiang_gap 的登记查询
    改走格级 :class:`~services.exchange_cell_verdicts.ConsumptionReport`, 头注 N);
    (2) 给 :data:`explained_merged_all` 里对应条目打 ``proved``; (3) 用
    :func:`cell_compare` 产出该市场的 :class:`Observed` 格, 汇总进全局
    ``observed_by_cell`` 交给 :func:`~services.exchange_cell_verdicts.consume` 统一
    消费 (取代 :func:`ceiling_compare`)。没文件的 (day, market) 记进
    ``not_checked``, 不算失败。

    ``explained_merged_all``/``residual_all`` 的条目在这里被就地改写 (residual
    items 不产出新对象, 只用于按天分组; explained_merged items 命中时加一个
    ``proved`` 键) —— 调用方 (:func:`verify`) 传进来的就是它要放进最终 report 的
    同一批对象, 原地打标最省事, 不必再拷一份。
    """
    verdicts = load_exchange_cell_verdicts(cell_verdicts_path)  # fail-closed: 登记表本身没坏

    residual_by_day: dict[str, list[dict[str, Any]]] = {}
    for item in residual_all:
        residual_by_day.setdefault(item["trade_date"], []).append(item)
    merged_by_day: dict[str, list[dict[str, Any]]] = {}
    for item in explained_merged_all:
        merged_by_day.setdefault(item["trade_date"], []).append(item)

    counts = {
        "unverified": 0,
        "old_vendor_error": 0,
        "miaoxiang_gap_registered": 0,
        "miaoxiang_gap_unregistered": 0,
        "new_diverges_from_exchange": 0,
        "matched_at_exchange_precision": 0,
    }
    explained_merged_proved = 0
    exchange_files: list[dict[str, Any]] = []
    checked_pairs: list[list[str]] = []
    not_checked: list[list[str]] = []
    venue_counts = {
        "by_vendor_market": 0, "unresolved": 0, "conflict": 0,
        "blocked_exchange_rows": 0, "not_covered_bj": 0,
    }
    observed_by_cell: dict[CellKey, Observed] = {}
    # 每 (day, market) 处理过的上下文, 留给 pass 2 (旧 exchange_verdicts 四路 + 合笔
    # proved 检查) 复用, 不重新算一遍场所/覆盖范围。
    per_day_market: dict[tuple[str, str], dict[str, Any]] = {}

    for day, new_rows in new_rows_by_day.items():
        by_market: dict[str, list[dict[str, Any]]] = {"sh": [], "sz": []}
        blocked_code6: set[str] = set()
        for row in new_rows:
            venue, status = resolve_venue(row.get("ts_code"), row.get("vendor_market"))
            ts_code = row.get("ts_code")
            code6 = str(ts_code)[:6] if isinstance(ts_code, str) and len(ts_code) >= 6 else None
            if status == "by_vendor_market":
                venue_counts["by_vendor_market"] += 1
            elif status == "conflict":
                venue_counts["conflict"] += 1
                if code6:
                    blocked_code6.add(code6)
                continue
            elif status == "unresolved":
                venue_counts["unresolved"] += 1
                if code6:
                    blocked_code6.add(code6)
                continue
            if venue == "bj":
                venue_counts["not_covered_bj"] += 1
                continue
            if venue in by_market and code6:
                by_market[venue].append(_venue_normalize_row(row, code6=code6, market=venue))

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

            market_new_rows = by_market[market]
            covered = _covered_codes(market_new_rows, exchange_rows_canon, market, codes)

            # 这天有 unresolved/conflict 的供应商行 -> 它同 code6 的交易所行既不能
            # 判 missing (我们自己的归属缺陷不该冒充供应商缺口), 也不参与格比较;
            # 挡下来单独计数 (头注 P)。
            blocked_suffix = {f"{c6}{_EXCHANGE_SUFFIX[market]}" for c6 in blocked_code6}
            for row in exchange_rows_canon:
                if row.get("ts_code") in blocked_suffix:
                    venue_counts["blocked_exchange_rows"] += 1
            covered -= blocked_suffix

            filtered_new = [r for r in market_new_rows if r.get("ts_code") in covered]
            filtered_exch = [r for r in exchange_rows_canon if r.get("ts_code") in covered]
            observed_by_cell.update(cell_compare(filtered_new, filtered_exch, market))

            per_day_market[(day, market)] = {
                "market_new_rows": market_new_rows,
                "exchange_rows_canon": exchange_rows_canon,
                "codes": codes,
                "covered": covered,
            }

    consumption = consume(observed_by_cell, verdicts)

    # 分区不变量 (V25): 用 consume() 自己的加权公式独立重算一遍, monkeypatch 掉
    # consume() 本身 (绕过它内部的断言) 也会在这里被抓到。stale/contradiction 格
    # 两侧都不计入 (与 exchange_cell_verdicts.consume 自己的不变量同口径, 头注 3.2
    # 钉子 4/consume 文档: 那类格的定义就是"登记跟观测对不上", 没有什么可以拿来
    # 配平, C1 自己的不变量也不把它们算进去)。
    t = consumption.totals
    consumption_weighted = (
        t.get("text", 0) * 2 + t.get("missing", 0) + t.get("extra_duplicate", 0)
        + t.get("extra_phantom", 0) + t.get("text_candidate", 0) * 2
        + t.get("missing_unregistered", 0) + t.get("extra_unregistered", 0)
    )
    stale_set = set(consumption.stale_cells)
    observed_total = sum(
        sum(obs.gap.values()) + sum(obs.extra.values())
        for cell, obs in observed_by_cell.items()
        if cell not in stale_set
    )
    if consumption_weighted != observed_total:
        raise AssertionError(
            "reland_event_domain cell-level partition invariant broken: "
            f"consumption sums to {consumption_weighted}, observed gap+extra sums to {observed_total}"
        )

    # pass 2: 旧 exchange_verdicts 四路 (逻辑不变, miaoxiang_gap 的登记查询改走
    # 格级 consumption, 头注 N) + explained_merged proved 检查 (逻辑完全不变)。
    for (day, market), ctx in per_day_market.items():
        market_new_rows = ctx["market_new_rows"]
        exchange_rows_canon = ctx["exchange_rows_canon"]
        codes = ctx["codes"]
        covered = ctx["covered"]
        suffix = _EXCHANGE_SUFFIX[market]
        day_residuals = [
            item for item in residual_by_day.get(day, [])
            if str(item["key"][0]).upper().endswith(suffix)
        ]
        for v in exchange_verdicts(day_residuals, market_new_rows, exchange_rows_canon, market, codes=codes):
            reason = v["reason"]
            if reason == "miaoxiang_gap":
                ts_code, trade_date, price, vol, buyer, seller = v["x"]
                code6 = str(ts_code)[:6]
                cell = (str(trade_date), market, code6, f"{price:.2f}", f"{vol:.2f}")
                per_cell = consumption.per_cell.get(cell)
                registered = False
                if per_cell is not None and per_cell.get("status") == "consumed":
                    entry = verdicts.by_cell.get(cell)
                    if entry is not None:
                        registered = _cell_missing_rows(entry).get((buyer, seller), 0) > 0
                if registered:
                    counts["miaoxiang_gap_registered"] += 1
                else:
                    counts["miaoxiang_gap_unregistered"] += 1
            else:
                counts[reason] += 1

        for item in merged_by_day.get(day, []):
            ts_code = item["key"][0]
            if ts_code not in covered:
                continue
            nc = Counter(exchange_key(r) for r in market_new_rows if r.get("ts_code") == ts_code)
            ex = Counter(exchange_key(r) for r in exchange_rows_canon if r.get("ts_code") == ts_code)
            if nc == ex:
                item["proved"] = True
                explained_merged_proved += 1

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
        "venue": venue_counts,
        "consumption": {
            "totals": dict(consumption.totals),
            "consumed_cells": [list(c) for c in consumption.consumed_cells],
            "stale_cells": [list(c) for c in consumption.stale_cells],
        },
        "not_checked": not_checked,
        "registry_sha256": verdicts.sha256,
        "observed_by_cell": observed_by_cell,
        "verdicts": verdicts,
    }


def verdict(report: Mapping[str, Any]) -> int:
    """头注 I/N-Q (C2 取代, bt_residual_classes_r1.md §4): 只读 ``report`` 的纯
    函数, 不重新计算、不连库。

    3 (硬失败): 任一结构检查 (S1-S4) 不通过; 或格级 :class:`ConsumptionReport`
    有未登记的缺口 (``consumption.totals.missing_unregistered``); 或有格的登记
    失效 (``consumption.stale_cells`` 非空, 覆盖 stale 与 contradiction 两种)。
    这几类无论有没有 ``unverified`` 剩余都直接判死, ``--record`` 会拒绝写账。

    2 (需要人工): 上面都干净, 但还有 (老 residual 路径的) ``unverified`` 没被格级
    比较覆盖到; 或格级比较里有未登记的文本差异/多出的成交
    (``consumption.totals.text_candidate``/``extra_unregistered``); 或场所归属
    解析不出/冲突 (``venue.unresolved``/``venue.conflict``); 或有 (日, 市场) 没有
    交易所证据文件可核 (``not_checked``)。

    0: 其余 —— 注意 ``new_diverges_from_exchange`` (旧四路判定) 不再单独影响退出
    码: 它的机器含义已被格级比较完整覆盖 (该格自己会按残差类定性), 双判会让同一
    事实产生两个不同退出码 (bt_residual_classes_r1.md §4 末段)。

    ``report`` 里没有 ``consumption``/``venue``/``not_checked`` (没传
    ``exchange_dir`` 的调用) 时按全零/空对待, 不影响判定。
    """
    structural = report["structural"]
    counts = report["residual_verdict_counts"]
    consumption = report.get("consumption") or {}
    totals = consumption.get("totals") or {}
    stale_cells = consumption.get("stale_cells") or []
    venue = report.get("venue") or {}
    not_checked = report.get("not_checked") or []

    structural_bad = any(not check["ok"] for check in structural.values())
    if (
        structural_bad
        or totals.get("missing_unregistered", 0) > 0
        or len(stale_cells) > 0
    ):
        return 3
    if (
        counts.get("unverified", 0) > 0
        or totals.get("text_candidate", 0) > 0
        or totals.get("extra_unregistered", 0) > 0
        or venue.get("unresolved", 0) > 0
        or venue.get("conflict", 0) > 0
        or bool(not_checked)
    ):
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
    cell_verdicts_path: Path | None = None,
    code_changes_path: Path | None = None,
    emit_candidates_path: Path | None = None,
) -> dict[str, Any]:
    """§3.4 (验收判据重落, C2 取代 V1/V2 的 ceiling+vendor_gaps 设计, 头注 G-Q):
    逐日三分类 + 结构检查 + 身份层 (R-I) + (可选) 交易所证据层的格级比较 (R-V +
    :func:`cell_compare` + 格级登记), 汇总出 ``exit_code`` (:func:`verdict`)。

    ``exchange_dir`` 只对 ``block_trade`` 有意义 (头注 M) —— 其它域传了直接
    ``ValueError``, 在动库/取锁之前就抛, CLI 层 (:func:`main`) 捕获后返回 1。
    ``code_changes_path`` 缺省 (``None``) 时身份层整体 ``skipped`` (头注 Q), 与
    ``exchange_dir`` 是否给出无关 —— 身份层 (R-I) 对旧/新行分类 (`classify_old_
    keys`) 一样适用, 不依赖交易所证据。``emit_candidates_path`` 给出时把当次未登记
    的残差格写成 YAML 骨架 (:func:`emit_candidate_verdicts`), 只读, 不影响
    ``exit_code``。

    ``record=True`` 时锁覆盖整个校验过程 (读的状态和记的账必须是同一个快照);
    ``exit_code != 0`` 直接 ``RuntimeError``, 不写记账 —— 校验没过就不能宣称"通过
    验收"。``exit_code == 0`` 时把格级登记表 sha256 (``registry_sha256``)、消费/
    失效的格 (``consumed_cells``/``stale_cells``)、身份层与场所归属计数
    (``identity``/``venue``)、证据覆盖情况 (``not_checked``/``exchange_files``)
    一并写进 ``rows_replaced_verified`` 的 ``verification_json`` (头注 N/Q 钉子 4:
    每次 verify 都重算, 不缓存)。

    ``record=False`` (默认) 保持只读连接, 不取锁。``cell_verdicts_path`` 未给时
    退回仓库真实路径 (``load_exchange_cell_verdicts`` 的默认路径), 与 CLI 用法
    一致; 登记表本身**总是**加载校验一遍 (即使没传 ``exchange_dir`` —— 配置本身
    的完整性与是否用到它是两件事)。

    ``conn``/``archive_dir`` 与 :func:`prepare` 同理, 为测试注入而加。
    """
    table = _domain_table(domain)
    if exchange_dir is not None and domain != "block_trade":
        raise ValueError(
            f"exchange_dir 目前只支持 domain='block_trade' (交易所证据层只覆盖沪深"
            f"逐笔), got domain={domain!r}"
        )
    owns_conn = conn is None
    ccs = load_security_code_changes(code_changes_path) if code_changes_path is not None else None

    def _build_report(active_conn: Any) -> dict[str, Any]:
        structural = structural_checks(active_conn, domain, table)
        classes, residual_all, explained_merged_all, new_rows_by_day, identity_totals = _classify_all_days(
            active_conn, domain, table, run_id=run_id, archive_dir=archive_dir, ccs=ccs
        )
        if exchange_dir is not None:
            evidence = _apply_exchange_evidence(
                residual_all=residual_all,
                explained_merged_all=explained_merged_all,
                new_rows_by_day=new_rows_by_day,
                exchange_dir=exchange_dir,
                cell_verdicts_path=cell_verdicts_path,
            )
            if emit_candidates_path is not None:
                emit_candidate_verdicts(
                    evidence["observed_by_cell"], evidence["verdicts"], emit_candidates_path
                )
            residual_verdict_counts = evidence["counts"]
            classes["explained_merged_proved"] = evidence["explained_merged_proved"]
            exchange_files = evidence["exchange_files"]
            venue = evidence["venue"]
            consumption = evidence["consumption"]
            not_checked = evidence["not_checked"]
            registry_sha256 = evidence["registry_sha256"]
        else:
            # 没给 exchange_dir: 全部 residual 落 unverified (V1 同语义), 格级
            # 比较/场所归属都没有观测数据可算, 全零/空 —— 但登记表本身仍然照常
            # 加载校验一遍 (它的完整性与本次要不要用它是两件事)。
            residual_verdict_counts = {
                "unverified": len(residual_all),
                "old_vendor_error": 0,
                "miaoxiang_gap_registered": 0,
                "miaoxiang_gap_unregistered": 0,
                "new_diverges_from_exchange": 0,
                "matched_at_exchange_precision": 0,
            }
            classes["explained_merged_proved"] = 0
            exchange_files = []
            venue = {
                "by_vendor_market": 0, "unresolved": 0, "conflict": 0,
                "blocked_exchange_rows": 0, "not_covered_bj": 0,
            }
            consumption = {
                "totals": {
                    "text": 0, "missing": 0, "extra_duplicate": 0, "extra_phantom": 0,
                    "text_candidate": 0, "missing_unregistered": 0, "extra_unregistered": 0,
                },
                "consumed_cells": [], "stale_cells": [],
            }
            not_checked = []
            loaded_verdicts = load_exchange_cell_verdicts(cell_verdicts_path)
            registry_sha256 = loaded_verdicts.sha256
            if emit_candidates_path is not None:
                emit_candidate_verdicts({}, loaded_verdicts, emit_candidates_path)
        result: dict[str, Any] = {
            "domain": domain,
            "table": table,
            "classes": classes,
            "residual": residual_all,
            "explained_merged": explained_merged_all,
            "structural": structural,
            "residual_verdict_counts": residual_verdict_counts,
            "identity": identity_totals,
            "venue": venue,
            "consumption": consumption,
            "not_checked": not_checked,
            "registry_sha256": registry_sha256,
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
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name=table,
                    delete_scope="rows_replaced_verified",
                    reason="grain 契约重落验收 exit_code=0 (格级比较+身份层+结构检查)",
                    verification={
                        "classes": result["classes"],
                        "residual_verdict_counts": result["residual_verdict_counts"],
                        "structural_ok": {k: v["ok"] for k, v in result["structural"].items()},
                        "registry_sha256": result["registry_sha256"],
                        "consumed_cells": result["consumption"]["consumed_cells"],
                        "stale_cells": result["consumption"]["stale_cells"],
                        "identity": result["identity"],
                        "venue": result["venue"],
                        "not_checked": result["not_checked"],
                        "exchange_files": result["exchange_files"],
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
    p_verify.add_argument("--cell-verdicts", type=Path, default=None)
    p_verify.add_argument("--code-changes", type=Path, default=None)
    p_verify.add_argument("--emit-candidates", type=Path, default=None)
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
            cell_verdicts_path=args.cell_verdicts,
            code_changes_path=args.code_changes,
            emit_candidates_path=args.emit_candidates,
        )
    except (ValueError, AssertionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
