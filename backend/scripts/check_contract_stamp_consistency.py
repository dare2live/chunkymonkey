"""check_contract_stamp_consistency — 契约戳一致性门 (2026-09-01).

# 这道门要防的四次真实事故 (今天发生, 全部实测记录):
#   第1次  改 daily/stock_st 契约 → 只打了 accepted_partition, 漏 canonical_* → 宣布"验证通过"
#   第2次  改 5 处契约          → 又只打 accepted_partition, 漏 canonical_* → 又宣布"验证通过"
#   第3次  补打 canonical       → 漏了 git 里 data/lineage/*.json 落盘快照 → CI 仍全绿
#   第4次  同步 ingest_batch    → 没算到 payload_hash 是从 contract_hash/config_hash 派生的
#          (margin_validation._batch_payload_hash) → 47,604 行改完连锁 LANDING_HASH_MISMATCH,
#          已回滚
#
# 共同根因: 改契约后, 没有一张"这个 hash 还存在于哪里"的完整地图 —— 每次"验证通过"
# 只验了当时想到的那一条路径。本门就是这张地图的机器化版本, 且**扫描面必须是发现式的**:
# 手写一张"我知道的存放点"清单, 换个地方长出同一个病 (今天已经犯了两次)。
#
# ── 四项检查 ────────────────────────────────────────────────────────────
#  ① pointer vs live contract   accepted_partition 里"当前 contract_version 组"的
#     contract_hash/config_hash 必须等于 load_*_contract() 现算值。旧 contract_version 组
#     刻意保留旧 hash (margin v2 / holders_top10 v2 等, 契约实体本就不同) —— 门不查它们。
#  ② canonical vs pointer       每张 canonical_* 表按 join 列 (通常 ingest_batch_id, 日历
#     用 generation_id) 关联 accepted_partition.batch_id, config_hash 必须与指针一致。
#     这是今天漏了两次的那一层。
#  ③ lineage 快照 vs live contract   data/lineage/**/*.json 里冻结的 dataset_id+config_hash
#     "戳记录" (发现式: 任何 dict 只要同时有 dataset_id + config_hash 两个 key 就算一条,
#     不看文件名)。可豁免 (快照就是用来锁住某一时刻的), 但豁免必须在本文件的
#     LINEAGE_SNAPSHOT_EXEMPTIONS 显式声明 + 理由, 不许代码里静默跳过。
#  ④ ingest_batch 派生链         ingest_batch.contract_hash/config_hash **不查是否等于
#     当前契约** —— 它们是落地时刻的封印, 按设计就该停在旧值 (第4次事故的教训: 有人把它们
#     "同步"到新契约, 但没有同步 payload_hash, 于是 payload_hash 的重算结果与库里存的值对不
#     上 → 这才是真正的信号)。做法: 用产线同一套哈希函数 (margin_validation._batch_payload_hash
#     / security_day_partition.stable_json+sha256_text / calendar_landing._batch_payload)
#     对每个 (dataset_id) 抽样最近 N 个 batch 现场重算 payload_hash, 与库里存的
#     payload_hash 比对。有人touch 了 contract_hash/config_hash 而没有同步重算 payload_hash
#     (或反过来只改了 payload_hash) → 重算失败 → FAIL。row_signatures 从对应 landing 表按
#     production 完全相同的格式重建 (ordinal:hash / frag:ordinal:hash), 不是猜的公式 ——
#     已对 7 个真实域逐一实测重算值与库里 payload_hash 逐字节相等 (2026-09-01 验证)。
#  ⑤ 文件级摘要钉住 (2026-09-01 补, fable 实测发现的第二层, 见 SECTION 8.5 详注)
#     除了①②③④这类"字段级"hash (contract_hash/config_hash/payload_hash 存在表列/JSON
#     字段里), 还存在"文件级"钉法: 某个 JSON 字段是**另一个文件整体字节**的 sha256
#     (典型样例 data/lineage/phase_d_experiment_runs/manifest.json 的 snapshot_hash
#     字段钉着 disclosure_dataset_snapshot.json 的全文摘要)。字段级扫描 (③) 天然扫不到
#     这层, 因为它按 dataset_id+config_hash 配对, 而文件级钉子没有 dataset_id。
#     发现式配对判据: hash 形字段 (key 含 hash, value 64 位十六进制) + 路径形字段 (value
#     是字符串、含 '/'、以已知文件后缀结尾) 在同一 JSON 节点内按 key 前缀亲缘配对, 不按
#     键名硬编码枚举 (不是维护一张 ["snapshot_hash","sha256","file_hash"] 清单)。
#     2026-09-01 实测: 10 个此类钉子里 9 个已经指向字节已变的文件而未更新 (只有 1 个
#     被某条窄测试盯着, 其余静默烂掉) —— 本检查独立发现全部 10 个, 不依赖那条窄测试。
#  ⑥ 供应商集合五处一致性 (占位接口, 见 SECTION 8.6) —— channels 尚未施工, 默认不接入
#     执行; 骨架已就位, --only 6 显式启用会报 WARN "五处 loader 待补"。
#
# ── 发现式扫描 (硬性设计要求, 不是"锦上添花") ───────────────────────────────
#   - discover_hash_columns(): 扫三个库(经 database_manifest 发现, 非硬编码库名) 的
#     information_schema.columns, 找全部 %hash% 列, 用 classify_hash_column() 分类;
#     分类不出的 → UNCLASSIFIED, 报 WARN (不静默)。
#   - discover_lineage_stamp_records(): 扫 data/lineage/ 下**全部** *.json (glob 递归,
#     不是一张写死的文件名清单), 找 dataset_id+config_hash 同时出现的 dict 节点。
#     dataset_id 不在 DOMAIN_REGISTRY 里的 → 报 WARN "发现未注册域"。
#   - discover_file_digest_pins(): 同样扫 data/lineage/ 下全部 *.json, 找"钉住其他文件
#     整文件摘要"的字段对 (见 ⑤); 配不上对的孤儿字段报 WARN, 不静默。
#
# ── 执行位置 (设计判断, 由接线方最终决定, 此处只record推荐) ──────────────────
#   建议分组 = diff_correctness (阻断), 理由见本文件底部 RECOMMENDATION 注释。
#   建议触发条件 = 本次 commit staged 路径命中 TRIGGER_PATH_GLOBS 时才做实质检查,
#   否则快速 PASS (契约文件 = *_contract.py / *_schema.py / contracts.py /
#   calendar_contract.py / calendar_schema.py + sync_registry.yaml)。
#   本脚本自身不接线 safe_commit —— 接线由另一个 agent 负责 (见交付说明)。
#
# 用法:
#   PYTHONPATH=backend python backend/scripts/check_contract_stamp_consistency.py
#   ... --json                              机器读
#   ... --only 1,2,3,4                      只跑指定检查号 (调试/演示单个缺陷用)
#   ... --sample-per-domain 20              check④ 每域抽样 batch 数 (默认见 DEFAULT_SAMPLE_PER_DOMAIN)
#   ... --db-override tushare_raw=/path.db  覆盖某 db_alias 的连接目标 (测试注入基线/造靶副本)
#   ... --lineage-dir /path/to/lineage      覆盖 data/lineage 根目录 (测试注入)
#
# 退出码: 0 = 全 PASS (允许有 WARN); 1 = 至少一项 FAIL。
"""
from __future__ import annotations

import argparse
import glob as globmod
import hashlib
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import duckdb  # noqa: E402

from services.database_manifest import get_database_manifest  # noqa: E402

DEFAULT_LINEAGE_DIR = REPO / "data" / "lineage"
DEFAULT_SAMPLE_PER_DOMAIN = 10
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


# ============================================================================
# SECTION 1 — 域注册表 (数据, 不是散落 hardcode; 与 check_grain_uniqueness.py 的
# MART_GRAINS 同一惯例: 集中一处数据表, 改契约新增域时只改这一张表)。
#
# 每条登记: 现算契约怎么 load (module:func[,固定 arg]) / db 在哪个 manifest alias /
# canonical 表叫什么、按哪列关联指针 / payload_hash 用哪套产线公式重算 (margin 是
# fragment 多请求家族; "simple" 是 holders_top10/stock_st/nominal_ohlcv/org_holding/
# stk_holdertrade 共用的单请求家族, 已实测 5 个域公式逐字节相同; calendar 独立一套
# 分页 fragment 家族)。
# ============================================================================


@dataclass(frozen=True)
class DomainSpec:
    name: str
    dataset_id: str
    db_alias: str
    loader_module: str
    loader_func: str
    canonical_table: str
    join_column: str
    payload_family: str  # "margin" | "simple" | "calendar"
    loader_arg: str | None = None
    landing_table: str | None = None  # payload_family == "simple"
    calendar_landing_table: str | None = None
    calendar_fragment_table: str | None = None

    def load_contract(self) -> Any:
        mod = importlib.import_module(self.loader_module)
        fn = getattr(mod, self.loader_func)
        return fn(self.loader_arg) if self.loader_arg is not None else fn()


DOMAIN_REGISTRY: tuple[DomainSpec, ...] = (
    DomainSpec(
        name="margin",
        dataset_id="tier0.market_data.margin_exchange_daily",
        db_alias="tushare_raw",
        loader_module="services.data_sources.contracts",
        loader_func="load_dataset_contract",
        loader_arg="margin",
        canonical_table="canonical_margin_exchange_daily",
        join_column="ingest_batch_id",
        payload_family="margin",
    ),
    DomainSpec(
        name="stock_st",
        dataset_id="tier0.security_identity.stock_st_daily",
        db_alias="tushare_raw",
        loader_module="services.data_sources.stock_st_contract",
        loader_func="load_stock_st_contract",
        canonical_table="canonical_stock_st_daily",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_tushare_stock_st",
    ),
    DomainSpec(
        name="nominal_ohlcv",
        dataset_id="tier0.market_data.nominal_ohlcv_daily",
        db_alias="tushare_raw",
        loader_module="services.data_sources.nominal_ohlcv_contract",
        loader_func="load_nominal_ohlcv_contract",
        canonical_table="canonical_nominal_ohlcv_daily",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_tushare_daily",
    ),
    DomainSpec(
        name="org_holding",
        dataset_id="tier0.disclosure.org_holding_detail_period",
        db_alias="org_holding",
        loader_module="services.data_sources.org_holding_contract",
        loader_func="load_org_holding_contract",
        canonical_table="canonical_org_holding_detail_period",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_miaoxiang_org_holding",
    ),
    DomainSpec(
        name="stk_holdertrade",
        dataset_id="tier0.disclosure.stock_holder_trade_announcement",
        db_alias="tushare_raw",
        loader_module="services.data_sources.stk_holdertrade_contract",
        loader_func="load_stk_holdertrade_contract",
        canonical_table="canonical_stk_holdertrade_announcement",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_tushare_stk_holdertrade",
    ),
    DomainSpec(
        name="holders_top10",
        dataset_id="tier0.disclosure.top10_float_holders_period",
        db_alias="smartmoney",
        loader_module="services.data_sources.holders_top10_contract",
        loader_func="load_holders_top10_contract",
        canonical_table="canonical_top10_float_holders_period",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_miaoxiang_holders_top10",
    ),
    DomainSpec(
        name="calendar",
        dataset_id="tier0.reference.sse_trading_calendar_generation",
        db_alias="tushare_raw",
        loader_module="services.data_sources.calendar_reader",
        loader_func="_contract_from_live_registry",
        canonical_table="canonical_sse_trading_calendar_generation",
        join_column="generation_id",
        payload_family="calendar",
        calendar_landing_table="landing_tushare_trade_cal",
        calendar_fragment_table="landing_tushare_trade_cal_fragment",
    ),
)
DOMAIN_BY_DATASET_ID: dict[str, DomainSpec] = {d.dataset_id: d for d in DOMAIN_REGISTRY}


# ============================================================================
# SECTION 2 — lineage 快照豁免登记 (显式声明 + 理由; 不许代码里 `if path == ...: skip`)
#
# 判定方法: 对 data/lineage/ 下**全部** JSON 做 dataset_id+config_hash 发现式扫描
# (见 discover_lineage_stamp_records), 逐条与 load_*_contract() 现算值比对。2026-09-01
# 实测扫描结果: 只有 2 个文件含"戳记录", 其中一个 (disclosure_dataset_snapshot.json)
# 当前就有真实 mismatch —— 但它是 frozen_at=2026-08-24T10:51:37Z 的 cutover 证据快照
# (holders_top10 冻结在 contract_version=2, 现网已到 3; nominal_ohlcv 的 contract_hash
# 冻结在 2026-09-01 "source/api 退出 config_hash" 改动之前), 语义就是"锁住那一刻",
# 不是忘了重生 —— 故登记豁免。另一个 (main_rally_dataset_snapshot/snapshot.json) 同样带
# frozen_at 标记但**当前恰好仍然一致**, 刻意不豁免: 提前豁免一个当前没有问题的文件等于
# 把它的未来漂移也一并静默掉, 违反"门不能因为你觉得它以后会豁免就现在放行"——它真的漂移
# 时应该在那一刻被抓到, 由那时的人决定修复还是登记豁免。
# ============================================================================

LINEAGE_SNAPSHOT_EXEMPTIONS: dict[str, str] = {
    "data/lineage/disclosure_dataset_snapshot.json": (
        "frozen_at=2026-08-24T10:51:37.776982+00:00 的 cutover 证据快照 (kind 隐含于顶层字段 "
        "cutover_allowed/shadow_overall_status)。2026-09-01 实测: holders_top10 冻结在 "
        "contract_version=2 (现网=3, config_hash/contract_hash 均漂移); nominal_ohlcv 的 "
        "contract_hash 漂移 (源于 2026-09-01 stock_st/nominal_ohlcv 契约把 source/api 移出 "
        "config_hash 的改动, config_hash 本身仍相同); org_holding/stk_holdertrade 当前仍一致。"
        "文件语义是'锁住 2026-08-24 那一刻的 shadow 验证证据', 不是活文档, 不应跟随契约演进——"
        "验证过这不是漏更新, 是设计如此。"
    ),
}


# ============================================================================
# SECTION 3 — hash 列分类规则 (发现式扫描配套的分类器; UNCLASSIFIED = 必须 WARN,
# 不能静默吞掉 —— 这正是今天漏掉的那类地方)。
# ============================================================================

# 精确 (table, column) 分类 —— 契约戳生命周期上的已知角色。
_EXACT_COLUMN_ROLES: dict[tuple[str, str], str] = {
    ("accepted_partition", "contract_hash"): "contract_stamp_pointer",
    ("accepted_partition", "config_hash"): "contract_stamp_pointer",
    ("accepted_partition", "content_hash"): "row_content_hash",
    ("ingest_batch", "contract_hash"): "ingest_batch_frozen_stamp",
    ("ingest_batch", "config_hash"): "ingest_batch_frozen_stamp",
    ("ingest_batch", "payload_hash"): "ingest_batch_derived_hash",
    ("ingest_batch", "canonical_hash"): "ingest_batch_derived_hash",
}

# 已知与"契约戳"体系无关的存放点 (另一套子系统的 hash, 例如 lineage SQL 指纹 / 增量水位
# 游标) —— 显式登记 + 理由, 而不是靠命名巧合躲过发现式扫描。
_OUT_OF_SCOPE_COLUMNS: dict[tuple[str, str], str] = {
    ("mart_data_lineage", "sql_hash"): "血缘子系统的 SQL 文本指纹, 与数据集契约身份无关",
    ("mart_lineage", "sql_hash"): "同上 (mart_lineage 是 mart_data_lineage 的同义/历史表)",
    ("mart_data_source_watermark", "last_raw_hash"): "增量同步水位游标, 不是契约戳",
}


def classify_hash_column(table: str, column: str) -> str:
    """discover_hash_columns() 配套分类器。返回值语义:
    - contract_stamp_pointer / ingest_batch_frozen_stamp / ingest_batch_derived_hash
      / canonical_stamp / row_content_hash / out_of_scope: 本门已知如何处理或明确排除。
    - UNCLASSIFIED: 发现了一个新的存 hash 的地方, 配置里没登记 —— 必须 WARN, 不许吞。
    """
    key = (table, column)
    if key in _EXACT_COLUMN_ROLES:
        return _EXACT_COLUMN_ROLES[key]
    if key in _OUT_OF_SCOPE_COLUMNS:
        return "out_of_scope"
    if table.startswith("canonical_"):
        if column == "config_hash":
            return "canonical_stamp"
        if column == "source_row_hash":
            return "row_content_hash"
        return "UNCLASSIFIED"
    if table.startswith("landing_"):
        if column in ("row_hash", "fragment_hash"):
            return "row_content_hash"
        return "UNCLASSIFIED"
    return "UNCLASSIFIED"


# ============================================================================
# SECTION 4 — 发现式扫描
# ============================================================================


@dataclass(frozen=True)
class DbUnavailable:
    """discover_databases() 的"非连接"结果, 区分两种完全不同的情况 (2026-09-02 修:
    此前两种情况被合并成同一个"不可达"字符串, 而它们的正确应对天差地别):
      - "unreachable": 库真的连不上 (文件不存在/损坏/写锁占用等) —— 可能是瞬时运维状态,
        默认降级为 WARN (与 check_grain_uniqueness.py 的 db_unreachable 同一惯例)。
      - "missing_tables": 库能正常打开, 只是没有 accepted_partition/ingest_batch 这两张表
        —— 这不是"不可达", 是结构性问题 (表被改名/退役, 或本门的 DomainSpec.db_alias
        本身配错了)。默认按 FAIL 处理 (fail-closed): 这正是本门要根治的"该查的没查,
        输出却和查了没问题一模一样"的恒绿形态, 不能用"不可达"这种听起来无害的话带过。
    """

    reason: str  # "unreachable" | "missing_tables"
    detail: str


_REQUIRED_TABLES = frozenset({"accepted_partition", "ingest_batch"})


def discover_databases(
    *,
    db_override: dict[str, str] | None = None,
    relevant_aliases: set[str] | None = None,
) -> list[tuple[str, str, Any]]:
    """经 database_manifest 发现(非硬编码库名列表)哪些 db alias 真的有
    accepted_partition + ingest_batch 表, 返回 [(alias, path, conn_or_DbUnavailable)]。

    relevant_aliases: 只探测这些 alias (run_all_checks 传 DOMAIN_REGISTRY 实际用到的
    db_alias 集合)。留 None 时探测 manifest 里全部 alias —— 仅供独立诊断/测试用,
    生产路径必须传 relevant_aliases, 否则 database_manifest.yaml 里与本门契约戳体系
    毫无关系的库 (market.duckdb/reference.duckdb/feature_store.duckdb/experiment_store.duckdb
    等, 它们从设计上就不含 accepted_partition/ingest_batch) 也会被当成"缺表"报出来,
    制造与事实不符的噪音。

    db_override: {alias: path} 测试注入 (指向 scratchpad 副本/基线备份); 传了 override
    的 alias 总会被探测, 不受 relevant_aliases 限制 (显式覆盖即显式要求测它)。
    """
    manifest = get_database_manifest()
    override = db_override or {}
    if relevant_aliases is None:
        aliases = set(manifest.databases.keys()) | set(override.keys())
    else:
        aliases = set(relevant_aliases) | set(override.keys())

    found: list[tuple[str, str, Any]] = []
    for alias in sorted(aliases):
        spec = manifest.databases.get(alias)
        path = override.get(alias)
        if path is None:
            if spec is None or not spec.path:
                found.append((
                    alias, "<unresolved>",
                    DbUnavailable(
                        "unreachable",
                        f"alias {alias!r} 未传 --db-override, 且在 database_manifest.yaml 里"
                        "不存在或是 path_glob 型条目 (多分片, 本门不支持)",
                    ),
                ))
                continue
            path = str(spec.resolve_path(repo_root=REPO))
        try:
            conn = duckdb.connect(  # rule-compliance: ok evidence=read_only 契约戳一致性审计连接, 不写
                path, read_only=True
            )
        except Exception as exc:  # noqa: BLE001 — 库不可达 (写锁占用等), 记录不崩溃
            found.append((alias, path, DbUnavailable("unreachable", str(exc)[:200])))
            continue
        try:
            tabs = {
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            }
        except Exception as exc:  # noqa: BLE001
            found.append((alias, path, DbUnavailable("unreachable", str(exc)[:200])))
            continue
        missing = _REQUIRED_TABLES - tabs
        if not missing:
            found.append((alias, path, conn))
        else:
            conn.close()
            found.append((
                alias, path,
                DbUnavailable(
                    "missing_tables",
                    f"库可达但缺表: {sorted(missing)} (库本身连接正常, 不是权限/写锁问题; "
                    "可能是表被改名/退役, 或 DomainSpec.db_alias 配错了库)",
                ),
            ))
    return found


def discover_hash_columns(conn: Any) -> list[tuple[str, str]]:
    """当前连接里全部 %hash% 列 (information_schema, 大小写不敏感)。"""
    rows = conn.execute(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE lower(column_name) LIKE '%hash%'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def discover_lineage_files(lineage_dir: Path) -> list[Path]:
    """data/lineage/ 下全部 *.json (递归 glob, 不是写死清单)。"""
    if not lineage_dir.is_dir():
        return []
    return sorted(Path(p) for p in globmod.glob(str(lineage_dir / "**" / "*.json"), recursive=True))


def _walk_stamp_records(obj: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "dataset_id" in obj and "config_hash" in obj:
            out.append(
                {
                    "pointer": path,
                    "dataset_id": obj.get("dataset_id"),
                    "config_hash": obj.get("config_hash"),
                    "contract_hash": obj.get("contract_hash"),
                    "contract_version": obj.get("contract_version"),
                }
            )
        for key, value in obj.items():
            _walk_stamp_records(value, f"{path}/{key}", out)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk_stamp_records(value, f"{path}[{index}]", out)


def discover_lineage_stamp_records(
    lineage_dir: Path,
) -> tuple[dict[Path, list[dict[str, Any]]], list[tuple[Path, str]]]:
    """扫描全部 lineage JSON, 找 dataset_id+config_hash 同时出现的节点 (发现式,
    不看文件名)。返回 (file -> [records], [(file, parse_error)])。"""
    by_file: dict[Path, list[dict[str, Any]]] = {}
    errors: list[tuple[Path, str]] = []
    for f in discover_lineage_files(lineage_dir):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append((f, str(exc)[:200]))
            continue
        records: list[dict[str, Any]] = []
        _walk_stamp_records(payload, "$", records)
        if records:
            by_file[f] = records
    return by_file, errors


# ============================================================================
# SECTION 5 — Finding 数据结构
# ============================================================================


@dataclass
class Finding:
    check: str  # "1_pointer" | "2_canonical" | "3_lineage" | "4_ingest_batch" | "discovery"
    severity: str  # "FAIL" | "WARN" | "INFO"
    domain: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "domain": self.domain,
            "detail": self.detail,
        }


# ============================================================================
# SECTION 6 — Check ①: pointer (accepted_partition) vs live contract
# ============================================================================


def check_pointer_vs_contract(conn: Any, domain: DomainSpec, contract: Any) -> list[Finding]:
    findings: list[Finding] = []
    rows = conn.execute(
        """
        SELECT contract_hash, config_hash, count(*) AS n
        FROM accepted_partition
        WHERE dataset_id = ? AND contract_version = ?
        GROUP BY 1, 2
        """,
        [domain.dataset_id, str(contract.contract_version)],
    ).fetchall()
    if not rows:
        findings.append(
            Finding(
                "1_pointer", "INFO", domain.name,
                f"accepted_partition 里没有 contract_version={contract.contract_version} 的行 "
                "(尚无该契约代下的 accepted 分区, 非错误)",
            )
        )
        return findings
    bad = [r for r in rows if r[0] != contract.contract_hash or r[1] != contract.config_hash]
    total = sum(r[2] for r in rows)
    if bad:
        for contract_hash, config_hash, n in bad:
            findings.append(
                Finding(
                    "1_pointer", "FAIL", domain.name,
                    f"accepted_partition dataset_id={domain.dataset_id} "
                    f"contract_version={contract.contract_version}: {n} 行 stamp 与现算契约不符 "
                    f"(库内 contract_hash={contract_hash[:12]}.. config_hash={config_hash[:12]}.. "
                    f"vs 现算 contract_hash={contract.contract_hash[:12]}.. "
                    f"config_hash={contract.config_hash[:12]}..)",
                )
            )
    else:
        findings.append(
            Finding(
                "1_pointer", "PASS" if False else "INFO", domain.name,  # noqa: SIM211 (PASS 不入 findings 汇总, 见 main)
                f"accepted_partition {total} 行 (contract_version={contract.contract_version}) 与现算契约一致",
            )
        )
    return findings


# ============================================================================
# SECTION 7 — Check ②: canonical vs pointer
# ============================================================================


def check_canonical_vs_pointer(conn: Any, domain: DomainSpec, contract: Any) -> list[Finding]:
    findings: list[Finding] = []
    tabs = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ?",
            [domain.canonical_table],
        ).fetchall()
    }
    if domain.canonical_table not in tabs:
        findings.append(
            Finding(
                "2_canonical", "WARN", domain.name,
                f"canonical 表 {domain.canonical_table} 在 db_alias={domain.db_alias} 里不存在 "
                "(登记表可能过期, 或该域尚未建表)",
            )
        )
        return findings
    cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [domain.canonical_table],
        ).fetchall()
    }
    if domain.join_column not in cols or "config_hash" not in cols:
        findings.append(
            Finding(
                "2_canonical", "WARN", domain.name,
                f"canonical 表 {domain.canonical_table} 缺 {domain.join_column} 或 config_hash 列 "
                f"(实际列: {sorted(cols)}) —— 无法核对, 登记表 join_column 可能需要更新",
            )
        )
        return findings
    rows = conn.execute(
        f"""
        SELECT c.config_hash AS canon_hash, p.config_hash AS ptr_hash, count(*) AS n
        FROM "{domain.canonical_table}" c
        JOIN accepted_partition p ON c."{domain.join_column}" = p.batch_id
        WHERE p.dataset_id = ? AND p.contract_version = ?
          AND c.config_hash <> p.config_hash
        GROUP BY 1, 2
        """,
        [domain.dataset_id, str(contract.contract_version)],
    ).fetchall()
    if rows:
        for canon_hash, ptr_hash, n in rows:
            findings.append(
                Finding(
                    "2_canonical", "FAIL", domain.name,
                    f"{domain.canonical_table}: {n} 行 config_hash={canon_hash[:12]}.. "
                    f"与其 accepted_partition 指针 config_hash={ptr_hash[:12]}.. 不符 "
                    f"(contract_version={contract.contract_version}) —— 契约改了但 canonical 没重打",
                )
            )
    else:
        total = conn.execute(
            f"""
            SELECT count(*) FROM "{domain.canonical_table}" c
            JOIN accepted_partition p ON c."{domain.join_column}" = p.batch_id
            WHERE p.dataset_id = ? AND p.contract_version = ?
            """,
            [domain.dataset_id, str(contract.contract_version)],
        ).fetchone()[0]
        findings.append(
            Finding(
                "2_canonical", "INFO", domain.name,
                f"{domain.canonical_table}: {total} 行与 accepted_partition 指针 config_hash 一致",
            )
        )
    return findings


# ============================================================================
# SECTION 8 — Check ③: lineage 快照 vs live contract
# ============================================================================


def check_lineage_snapshots(
    lineage_dir: Path, exemptions: dict[str, str] | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    exemptions = exemptions if exemptions is not None else LINEAGE_SNAPSHOT_EXEMPTIONS
    by_file, parse_errors = discover_lineage_stamp_records(lineage_dir)
    for f, err in parse_errors:
        findings.append(Finding("3_lineage", "WARN", None, f"{f}: JSON 解析失败 ({err})"))

    contract_cache: dict[str, tuple[Any, str | None]] = {}

    def _contract_for(dataset_id: str) -> tuple[Any, str | None]:
        if dataset_id not in contract_cache:
            domain = DOMAIN_BY_DATASET_ID.get(dataset_id)
            if domain is None:
                contract_cache[dataset_id] = (None, None)
            else:
                try:
                    contract_cache[dataset_id] = (domain.load_contract(), None)
                except Exception as exc:  # noqa: BLE001
                    contract_cache[dataset_id] = (None, str(exc)[:200])
        return contract_cache[dataset_id]

    for f, records in by_file.items():
        try:
            rel = f.relative_to(REPO)
        except ValueError:
            rel = f
        rel_str = str(rel)
        exempt_reason = exemptions.get(rel_str)
        dataset_ids = sorted({r["dataset_id"] for r in records if r.get("dataset_id")})
        unregistered = [d for d in dataset_ids if d not in DOMAIN_BY_DATASET_ID]
        for d in unregistered:
            findings.append(
                Finding(
                    "discovery", "WARN", d,
                    f"{rel_str} 里发现未注册域的契约戳记录 (dataset_id={d}) —— "
                    "DOMAIN_REGISTRY 没有它, 若这是新增数据域契约请补登记",
                )
            )
        if exempt_reason is not None:
            findings.append(
                Finding(
                    "3_lineage", "INFO", None,
                    f"{rel_str}: 豁免 ({len(records)} 条戳记录不核对) — {exempt_reason}",
                )
            )
            continue
        mismatches = 0
        for rec in records:
            dataset_id = rec.get("dataset_id")
            if dataset_id not in DOMAIN_BY_DATASET_ID:
                continue  # 已在上面报过 WARN, 无法核对 (没有登记的加载器)
            contract, load_err = _contract_for(dataset_id)
            if load_err is not None:
                findings.append(
                    Finding(
                        "3_lineage", "WARN", dataset_id,
                        f"{rel_str}: 无法现算 {dataset_id} 的契约来核对 ({load_err})",
                    )
                )
                continue
            expected_config = contract.config_hash
            actual_config = rec.get("config_hash")
            if actual_config != expected_config:
                mismatches += 1
        if mismatches:
            findings.append(
                Finding(
                    "3_lineage", "FAIL", None,
                    f"{rel_str}: {mismatches}/{len(records)} 条戳记录的 config_hash 与现算契约不符, "
                    "且该文件未在 LINEAGE_SNAPSHOT_EXEMPTIONS 登记豁免 —— "
                    "契约改了但这份落盘快照没有重生, 或者它其实需要补登记为豁免",
                )
            )
        else:
            findings.append(
                Finding(
                    "3_lineage", "INFO", None,
                    f"{rel_str}: {len(records)} 条戳记录与现算契约一致",
                )
            )
    return findings


# ============================================================================
# SECTION 8.5 — Check ⑤: 文件级摘要钉住 (2026-09-01 补, fable 实测发现的第二层)
#
# 契约戳有**两个层级**, 本门原先只覆盖第一层:
#   ① 字段级: contract_hash / config_hash / payload_hash 存在表列和 JSON 字段里 (① ② ③ ④)
#   ② 文件级: 某个文件里钉着"另一个文件全文字节的 sha256" (典型键名 snapshot_hash +
#      snapshot_relpath, 或 b0_artifact_hash + b0_artifact_relpath) —— 改了被钉文件的
#      字节 (哪怕只是常规重生), 这层摘要立刻失效, 而它长得完全不像 dataset_id+config_hash
#      的"契约戳", 字段级扫描天然扫不到它。
#
# 2026-09-01 实测: data/lineage/phase_d_experiment_runs/manifest.json 的 snapshot_hash
# 钉着 disclosure_dataset_snapshot.json 的整文件 sha256, 目前已经对不上 (该快照文件被
# 另一个 agent 重生过, 字节变了); phase_e_experiment_verdicts/ 5 个 + phase_f_experiment_
# verdicts/ 4 个文件同款钉法, 其中只有 1 个此前被某条窄测试 (test_committed_artifact_
# matches_live_sources) 盯着, 其余 9 个完全没有测试在看 —— 静默烂掉。
#
# 发现方法 (不许按键名硬编码清单, 用结构判据):
#   1. 对每个 JSON dict 节点, 收集"hash 形字段" (key 含 'hash' 且 value 是 64 位小写十六进制)
#      和"路径形字段" (value 是字符串, 含 '/', 且以常见文件后缀结尾, 例如 .json)。
#   2. 按 key 前缀亲缘配对 (剥掉 hash 字段的 'hash'/'_hash' 后缀、路径字段的 'relpath'/
#      '_relpath'/'path'/'_path' 后缀, 前缀相同则配对); 若一个节点只有恰好 1 个 hash 形 +
#      1 个路径形字段, 也配对 (无歧义场景, 不强求前缀相同)。
#   3. 配不上对的 hash 形/路径形字段单独报 WARN (不是"找不到就算了", 是"这里有个不认识的
#      东西, 人来判断")。
#   4. 每一对: 把路径形字段的值当仓库相对路径解出目标文件, 现算它的整文件 sha256, 与
#      钉住的值比对。目标文件不存在 / 不可读 → WARN。摘要不吻合 → FAIL (不区分"是否有
#      别的窄测试也在盯" —— 本门自己就是那张完整地图, 不依赖运气式的窄测试覆盖)。
# ============================================================================

_PATH_LIKE_SUFFIXES = (".json", ".yaml", ".yml", ".csv", ".parquet", ".py", ".md", ".duckdb")


def _hash_like_fields(node: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in node.items()
        if isinstance(v, str) and "hash" in k.lower() and SHA256_HEX_RE.match(v)
    ]


def _path_like_fields(node: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in node.items()
        if isinstance(v, str) and "/" in v and v.lower().endswith(_PATH_LIKE_SUFFIXES)
    ]


def _stem_hash_key(key: str) -> str:
    k = key.lower()
    for suf in ("_hash", "hash"):
        if k.endswith(suf):
            return k[: -len(suf)].rstrip("_")
    return k


def _stem_path_key(key: str) -> str:
    k = key.lower()
    for suf in ("_relpath", "relpath", "_path", "path"):
        if k.endswith(suf):
            return k[: -len(suf)].rstrip("_")
    return k


@dataclass(frozen=True)
class DigestPinCandidate:
    file: Path
    pointer: str
    hash_key: str
    hash_value: str
    path_key: str
    target_relpath: str


def _walk_digest_pin_candidates(
    obj: Any, path: str, source_file: Path, out: list[DigestPinCandidate], orphans: list[str]
) -> None:
    if isinstance(obj, dict):
        hash_fields = _hash_like_fields(obj)
        path_fields = _path_like_fields(obj)
        if hash_fields and path_fields:
            paired_hash_keys: set[str] = set()
            paired_path_keys: set[str] = set()
            if len(hash_fields) == 1 and len(path_fields) == 1:
                hk, hv = hash_fields[0]
                pk, pv = path_fields[0]
                out.append(DigestPinCandidate(source_file, path, hk, hv, pk, pv))
                paired_hash_keys.add(hk)
                paired_path_keys.add(pk)
            else:
                for hk, hv in hash_fields:
                    stem = _stem_hash_key(hk)
                    matches = [(pk, pv) for pk, pv in path_fields if _stem_path_key(pk) == stem]
                    if len(matches) == 1:
                        pk, pv = matches[0]
                        out.append(DigestPinCandidate(source_file, path, hk, hv, pk, pv))
                        paired_hash_keys.add(hk)
                        paired_path_keys.add(pk)
            for hk, _ in hash_fields:
                if hk not in paired_hash_keys:
                    orphans.append(f"{source_file}:{path}/{hk} 是 hash 形字段但配不到路径形兄弟字段")
            for pk, _ in path_fields:
                if pk not in paired_path_keys:
                    orphans.append(f"{source_file}:{path}/{pk} 是路径形字段但配不到 hash 形兄弟字段")
        for key, value in obj.items():
            _walk_digest_pin_candidates(value, f"{path}/{key}", source_file, out, orphans)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk_digest_pin_candidates(value, f"{path}[{index}]", source_file, out, orphans)


def discover_file_digest_pins(
    lineage_dir: Path,
) -> tuple[list[DigestPinCandidate], list[str]]:
    """发现式扫描: lineage 下全部 JSON 里"钉住另一个文件整文件 sha256"的字段对。"""
    candidates: list[DigestPinCandidate] = []
    orphans: list[str] = []
    for f in discover_lineage_files(lineage_dir):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 解析错误已在 check_lineage_snapshots 报过, 这里不重复
            continue
        _walk_digest_pin_candidates(payload, "$", f, candidates, orphans)
    return candidates, orphans


def check_file_digest_pins(lineage_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    candidates, orphans = discover_file_digest_pins(lineage_dir)
    for msg in orphans:
        findings.append(Finding("discovery", "WARN", None, f"文件级摘要钉住发现式扫描: {msg}"))
    seen: set[tuple[Path, str, str]] = set()
    for c in candidates:
        dedup_key = (c.file, c.hash_value, c.target_relpath)
        if dedup_key in seen:
            continue  # 同一对 (钉住值, 目标路径) 在同文件出现多次 (如 manifest+每域 verdict 重复引用同一快照) 只需判一次
        seen.add(dedup_key)
        target = REPO / c.target_relpath
        try:
            rel_source = c.file.relative_to(REPO)
        except ValueError:
            rel_source = c.file
        if not target.is_file():
            findings.append(
                Finding(
                    "5_file_digest", "WARN", None,
                    f"{rel_source}:{c.pointer}/{c.hash_key} 钉着 {c.target_relpath}, "
                    "但该文件在仓库里不存在 (路径可能已改名/移动, 或钉住的是历史路径)",
                )
            )
            continue
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except Exception as exc:  # noqa: BLE001
            findings.append(
                Finding("5_file_digest", "WARN", None, f"{rel_source}: 无法读取 {c.target_relpath} 现算摘要 ({exc})")
            )
            continue
        if actual != c.hash_value:
            findings.append(
                Finding(
                    "5_file_digest", "FAIL", None,
                    f"{rel_source}:{c.pointer}/{c.hash_key}={c.hash_value[:16]}.. 钉住的整文件摘要与 "
                    f"{c.target_relpath} 现算摘要={actual[:16]}.. 不符 —— 被钉文件的字节已经变了 "
                    "(常规重生也算), 这份钉住的引用没有跟着更新",
                )
            )
        else:
            findings.append(
                Finding(
                    "5_file_digest", "INFO", None,
                    f"{rel_source}:{c.pointer}/{c.hash_key} 与 {c.target_relpath} 现算摘要一致",
                )
            )
    return findings


# ============================================================================
# SECTION 8.6 — Check ⑥: 供应商集合一致性 (占位接口, channels 未施工, 不接入默认执行)
#
# fable 的 P1 channels 方案交付后, 每域的"供应商集合"会同时存在于五处, 必须相等/满足子集
# 规则, 否则某处漏改就会出现"注册表说可以但适配器不认"这类静默分裂:
#   ① sync_registry.yaml 的 domains.<domain>.source / fallback_channels   (owner, 真相源)
#   ② 各域 *_schema.py 的 DOMAIN.source / DOMAIN.allowed_sources
#   ③ formal_boundaries 里 adapter 声明的可用来源
#   ④ .moth/assertions/claims.yaml 里的字面允许集
#   ⑤ tushare_sunset.yaml 的 replacement / status (授权到期后的替代源声明)
#
# 明确排除: ingest_batch.source_name (served_by) 不是复制值, 是**事件证据** —— 哪个批次
# 实际是哪个供应商送达的, 落地即封印, 永不重打 (与本门 check④ "ingest_batch 的
# contract_hash/config_hash 不该被同步"同一条原则: 它记录的是"当时发生了什么", 不是
# "现在配置是什么")。任何未来实现都不得把 source_name 拉进比对集合。
#
# 现状 (2026-09-01): fallback_channels / allowed_sources 均未在代码库出现 —— channels
# 尚未施工, 本函数只占位, **不接入 run_all_checks 默认执行** (--only 不含 '6' 时不跑),
# 避免对着不存在的字段做无意义比较。等 channels 落地后, 把每处的读取逻辑填进
# _CHANNEL_SET_SOURCES 五个 loader 位置即可让本门自动开始比对 —— 扫描骨架
# (五处都读到即比较, 读不到即报 WARN "该处尚未实现供应商集合读取") 已经在这里,
# 不必等 channels 出现后再现造一道新门。
# ============================================================================


def check_channel_set_consistency(*, enabled: bool = False) -> list[Finding]:
    if not enabled:
        return [
            Finding(
                "6_channel_set", "INFO", None,
                "供应商集合五处一致性检查尚未接入 (channels 未施工: fallback_channels/"
                "allowed_sources 尚未出现在代码库)。骨架已就位于 check_channel_set_consistency(), "
                "五处 loader 待 channels 落地后填入; ingest_batch.source_name 按设计永不纳入比对。",
            )
        ]
    # TODO(channels P1): 五处 loader 落地后在此实现真实比对, 目前留空占位不误报。
    return [
        Finding(
            "6_channel_set", "WARN", None,
            "check_channel_set_consistency(enabled=True) 被调用但五处 loader 尚未实现 —— "
            "占位骨架, 需要在 channels 落地时补全",
        )
    ]


# ============================================================================
# SECTION 9 — Check ④: ingest_batch 派生链 (payload_hash 重算)
#
# 三套家族, 全部导入产线真实哈希函数 (不重新实现, 避免"自己造的公式看起来在工作"
# ——mio 协议 11.5 的反例正是这种自信陷阱)。已对 2026-09-01 真实数据逐域实测:
# 现算 payload_hash 与库里存的值逐字节相等。
# ============================================================================


def _recompute_payload_hash_simple(
    conn: Any, domain: DomainSpec, row: dict[str, Any]
) -> tuple[str | None, str | None]:
    """holders_top10 / stock_st / nominal_ohlcv / org_holding / stk_holdertrade 共用公式。
    返回 (recomputed_hash, error)。"""
    try:
        from services.data_sources.security_day_partition import (  # noqa: PLC0415
            sha256_text,
            stable_json,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"import security_day_partition failed: {exc}"
    sig_rows = conn.execute(
        f'SELECT row_ordinal, row_hash FROM "{domain.landing_table}" '
        "WHERE batch_id = ? ORDER BY row_ordinal",
        [row["batch_id"]],
    ).fetchall()
    expected_n = row.get("landing_row_count")
    if not sig_rows and expected_n:
        # landing_row_count>0 但 landing 表里一行都读不到 —— 真的缺证据 (不是"批次本来就是空的"),
        # 可能是保留期策略已清理该批次原始行 (例如 holders_landing_retention.py)。
        return None, (
            f"landing 表 {domain.landing_table} 里找不到 batch_id={row['batch_id']} 的行, "
            f"但该批次登记 landing_row_count={expected_n} —— 证据缺失 (可能是保留期策略已清理原始行) "
            "—— 无法核对不等于有问题, 但也无法确认; 增大 --sample-per-domain 采样更晚近批次可缓解"
        )
    signatures = [f"{ordinal}:{row_hash}" for ordinal, row_hash in sig_rows]
    try:
        request = json.loads(row["request_json"])
    except Exception as exc:  # noqa: BLE001
        return None, f"request_json 解析失败: {exc}"
    candidate = stable_json(
        {
            "partition": row["partition_value"],
            "source": row["source_name"],
            "contract_version": row["contract_version"],
            "contract_hash": row["contract_hash"],
            "config_hash": row["config_hash"],
            "observed_at": row["observed_at"].astimezone(timezone.utc).isoformat(),
            "available_at": row["available_at"].astimezone(timezone.utc).isoformat(),
            "request": request,
            "row_signatures": signatures,
        }
    )
    return sha256_text(candidate), None


def _recompute_payload_hash_margin(conn: Any, domain: DomainSpec, row: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        from services.data_sources.margin_validation import _batch_payload_hash  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return None, f"import margin_validation failed: {exc}"
    sig_rows = conn.execute(
        "SELECT fragment_exchange_id, fragment_ordinal, row_ordinal, row_hash "
        "FROM landing_tushare_margin WHERE batch_id = ? ORDER BY fragment_ordinal, row_ordinal",
        [row["batch_id"]],
    ).fetchall()
    expected_n = row.get("landing_row_count")
    if not sig_rows and expected_n:
        return None, (
            f"landing_tushare_margin 里找不到 batch_id={row['batch_id']} 的行, "
            f"但该批次登记 landing_row_count={expected_n} —— 证据缺失"
        )
    signatures = [f"{frag}:{ordv}:{h}" for (_exch, frag, ordv, h) in sig_rows]
    try:
        requests = json.loads(row["request_json"])
        outcomes = json.loads(row["fragment_outcomes_json"])
    except Exception as exc:  # noqa: BLE001
        return None, f"request/outcome JSON 解析失败: {exc}"
    recomputed = _batch_payload_hash(
        partition=row["partition_value"],
        source=row["source_name"],
        contract_version=row["contract_version"],
        contract_hash=row["contract_hash"],
        config_hash=row["config_hash"],
        observed_at=row["observed_at"],
        available_at=row["available_at"],
        requests=requests,
        outcomes=outcomes,
        row_signatures=signatures,
    )
    return recomputed, None


def _recompute_payload_hash_calendar(conn: Any, domain: DomainSpec, row: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        from types import SimpleNamespace  # noqa: PLC0415

        from services.data_sources.calendar_landing import (  # noqa: PLC0415
            _LandedFragment,
            _batch_payload,
            _sha256,
            _stable_json,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"import calendar_landing failed: {exc}"
    frags = conn.execute(
        f'SELECT fragment_ordinal, request_offset, request_limit, request_json, outcome, '
        f'row_count, fragment_hash, completed_at, error_type, error_detail '
        f'FROM "{domain.calendar_fragment_table}" WHERE batch_id = ? ORDER BY fragment_ordinal',
        [row["batch_id"]],
    ).fetchall()
    if not frags:
        return None, f"{domain.calendar_fragment_table} 里找不到 batch_id={row['batch_id']} 的分片"
    fragments = []
    for (ford, roff, rlim, req_json, outcome, rcount, fhash, completed_at, etype, edetail) in frags:
        rows = conn.execute(
            f'SELECT row_ordinal, payload_json, row_hash FROM "{domain.calendar_landing_table}" '
            "WHERE batch_id = ? AND fragment_ordinal = ? ORDER BY row_ordinal",
            [row["batch_id"], ford],
        ).fetchall()
        try:
            rows_tuple = tuple((ro, json.loads(pj), rh) for ro, pj, rh in rows)
            req = json.loads(req_json)
        except Exception as exc:  # noqa: BLE001
            return None, f"fragment/row JSON 解析失败: {exc}"
        fragments.append(
            _LandedFragment(
                ordinal=ford, request_offset=roff, request_limit=rlim, request=req,
                outcome=outcome, row_count=rcount, fragment_hash=fhash,
                completed_at=completed_at.astimezone(timezone.utc),
                error_type=etype, error_detail=edetail, rows=rows_tuple,
            )
        )
    contract_stub = SimpleNamespace(
        contract_version=row["contract_version"], contract_hash=row["contract_hash"],
        config_hash=row["config_hash"], writer_id=row["writer_id"], source=row["source_name"],
    )
    payload = _batch_payload(
        batch_id=row["batch_id"],
        observed_at=row["observed_at"].astimezone(timezone.utc),
        contract=contract_stub,
        fragments=fragments,
    )
    return _sha256(_stable_json(payload)), None


_RECOMPUTE_BY_FAMILY: dict[str, Callable[[Any, DomainSpec, dict[str, Any]], tuple[str | None, str | None]]] = {
    "simple": _recompute_payload_hash_simple,
    "margin": _recompute_payload_hash_margin,
    "calendar": _recompute_payload_hash_calendar,
}


def check_ingest_batch_derivation(
    conn: Any, domain: DomainSpec, sample_per_domain: int
) -> list[Finding]:
    """检④: 抽样最近 N 个 batch, 现算 payload_hash 与库存值比对。**不**检查
    contract_hash/config_hash 是否等于当前契约 —— 那是设计上允许停留在落地时刻的封印。"""
    findings: list[Finding] = []
    recompute = _RECOMPUTE_BY_FAMILY.get(domain.payload_family)
    if recompute is None:
        findings.append(
            Finding("4_ingest_batch", "WARN", domain.name, f"未知 payload_family={domain.payload_family!r}")
        )
        return findings

    rows = conn.execute(
        """
        SELECT batch_id, partition_value, source_name, contract_version, contract_hash,
               config_hash, request_json, fragment_outcomes_json, observed_at, available_at,
               payload_hash, writer_id, landing_row_count
        FROM ingest_batch
        WHERE dataset_id = ?
        ORDER BY COALESCE(accepted_at, landed_at) DESC
        LIMIT ?
        """,
        [domain.dataset_id, sample_per_domain],
    ).fetchall()
    if not rows:
        findings.append(Finding("4_ingest_batch", "INFO", domain.name, "ingest_batch 无该 dataset_id 的行"))
        return findings
    cols = [
        "batch_id", "partition_value", "source_name", "contract_version", "contract_hash",
        "config_hash", "request_json", "fragment_outcomes_json", "observed_at", "available_at",
        "payload_hash", "writer_id", "landing_row_count",
    ]
    checked = 0
    for values in rows:
        row = dict(zip(cols, values, strict=True))
        recomputed, err = recompute(conn, domain, row)
        if err is not None:
            findings.append(
                Finding(
                    "4_ingest_batch", "WARN", domain.name,
                    f"batch_id={row['batch_id']}: 无法重算 payload_hash ({err})",
                )
            )
            continue
        checked += 1
        if recomputed != row["payload_hash"]:
            findings.append(
                Finding(
                    "4_ingest_batch", "FAIL", domain.name,
                    f"batch_id={row['batch_id']}: payload_hash 重算不吻合 "
                    f"(库内={row['payload_hash'][:16]}.. 现算={recomputed[:16] if recomputed else None}..) "
                    "—— contract_hash/config_hash/request/observed_at/available_at 其中之一被改动过, "
                    "且未随之重算 payload_hash (ingest_batch 是落地时刻的封印, 不应被后续同步触碰)",
                )
            )
    if checked:
        findings.append(
            Finding(
                "4_ingest_batch", "INFO", domain.name,
                f"抽样 {checked}/{len(rows)} 个 batch, payload_hash 现算与库内值一致",
            )
        )
    return findings


# ============================================================================
# SECTION 10 — Discovery 报告 (未分类 hash 列)
# ============================================================================


def check_discovery(conn: Any, db_alias: str) -> list[Finding]:
    findings: list[Finding] = []
    for table, column in discover_hash_columns(conn):
        role = classify_hash_column(table, column)
        if role == "UNCLASSIFIED":
            findings.append(
                Finding(
                    "discovery", "WARN", None,
                    f"db_alias={db_alias} 表 {table}.{column} 含 hash 但未在 "
                    "classify_hash_column() 分类 —— 需要人工判断这是不是契约戳的新存放点",
                )
            )
    return findings


# ============================================================================
# SECTION 11 — 编排 + CLI
# ============================================================================


def run_all_checks(
    *,
    only: set[str] | None = None,
    sample_per_domain: int = DEFAULT_SAMPLE_PER_DOMAIN,
    db_override: dict[str, str] | None = None,
    lineage_dir: Path = DEFAULT_LINEAGE_DIR,
    lineage_exemptions: dict[str, str] | None = None,
    domains: tuple[DomainSpec, ...] = DOMAIN_REGISTRY,
    allow_missing_db: bool = False,
) -> list[Finding]:
    """allow_missing_db: 2026-09-02 修 (fable 验收发现的恒绿口子) —— 默认 False:
    某个域的 db_alias 库缺 accepted_partition/ingest_batch 表时, 该域检查①②④视为 FAIL
    (无法验证 != 通过, fail-closed)。显式传 True 才降级为 WARN (仅供已知、有意的排查场景
    使用, 不应该是任何自动化路径的默认值)。"""
    only = only or {"1", "2", "3", "4", "5"}
    findings: list[Finding] = []

    relevant_aliases = {d.db_alias for d in domains}
    db_entries = discover_databases(db_override=db_override, relevant_aliases=relevant_aliases)
    conn_by_alias: dict[str, Any] = {}
    db_unavailable_by_alias: dict[str, DbUnavailable] = {}
    for alias, path, conn_or_status in db_entries:
        if isinstance(conn_or_status, DbUnavailable):
            db_unavailable_by_alias[alias] = conn_or_status
            if conn_or_status.reason == "missing_tables":
                severity = "WARN" if allow_missing_db else "FAIL"
            else:
                severity = "WARN"  # 真不可达 (写锁/瞬时) 默认不阻断, 与 grain_uniqueness 同惯例
            findings.append(
                Finding("discovery", severity, None, f"db_alias={alias} ({path}): {conn_or_status.detail}")
            )
            continue
        conn_by_alias[alias] = conn_or_status
        findings.extend(check_discovery(conn_or_status, alias))

    contract_by_domain: dict[str, tuple[Any, str | None]] = {}
    for domain in domains:
        try:
            contract_by_domain[domain.name] = (domain.load_contract(), None)
        except Exception as exc:  # noqa: BLE001
            contract_by_domain[domain.name] = (None, str(exc)[:200])

    for domain in domains:
        conn = conn_by_alias.get(domain.db_alias)
        contract, load_err = contract_by_domain[domain.name]
        if conn is None:
            status = db_unavailable_by_alias.get(domain.db_alias)
            reason = status.reason if status is not None else "unknown"
            if reason == "missing_tables":
                severity = "WARN" if allow_missing_db else "FAIL"
                msg = (
                    f"db_alias={domain.db_alias} 缺 accepted_partition/ingest_batch 表 "
                    f"(见上方 discovery finding) —— 该域检查①②④全部无法验证, 按 fail-closed 处理"
                )
            else:
                severity = "WARN"
                msg = f"db_alias={domain.db_alias} 不可达 (见上方 discovery finding), 跳过该域全部检查"
            findings.append(Finding("1_pointer", severity, domain.name, msg))
            continue
        if load_err is not None:
            findings.append(
                Finding("1_pointer", "WARN", domain.name, f"现算契约失败, 跳过该域检查①②④ ({load_err})")
            )
        else:
            if "1" in only:
                findings.extend(check_pointer_vs_contract(conn, domain, contract))
            if "2" in only:
                findings.extend(check_canonical_vs_pointer(conn, domain, contract))
        if "4" in only:
            findings.extend(check_ingest_batch_derivation(conn, domain, sample_per_domain))

    if "3" in only:
        findings.extend(check_lineage_snapshots(lineage_dir, lineage_exemptions))
    if "5" in only:
        findings.extend(check_file_digest_pins(lineage_dir))
    if "6" in only:
        findings.extend(check_channel_set_consistency(enabled=True))

    for conn in conn_by_alias.values():
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="契约戳一致性门 — 发现式扫描四类今天真实发生过的漂移")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--only", default="1,2,3,4,5",
        help="只跑指定检查号, 逗号分隔 (调试/演示单个缺陷); 6=供应商集合占位检查, 默认不含 (channels 未施工)",
    )
    ap.add_argument("--sample-per-domain", type=int, default=DEFAULT_SAMPLE_PER_DOMAIN)
    ap.add_argument(
        "--db-override", action="append", default=[],
        help="alias=/path/to.duckdb, 可重复 (测试注入基线/造靶副本)",
    )
    ap.add_argument("--lineage-dir", type=Path, default=DEFAULT_LINEAGE_DIR)
    ap.add_argument(
        "--allow-missing-db", action="store_true",
        help="域的 db_alias 库缺 accepted_partition/ingest_batch 表时降级为 WARN (默认 FAIL, "
        "fail-closed)。仅供已知、有意的排查场景显式开启, 不要在自动化路径默认打开。",
    )
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    db_override: dict[str, str] = {}
    for item in args.db_override:
        if "=" not in item:
            raise SystemExit(f"--db-override 格式错: {item!r} (需 alias=/path)")
        alias, path = item.split("=", 1)
        db_override[alias.strip()] = path.strip()

    findings = run_all_checks(
        only=only,
        sample_per_domain=args.sample_per_domain,
        db_override=db_override,
        lineage_dir=args.lineage_dir,
        allow_missing_db=args.allow_missing_db,
    )

    fails = [f for f in findings if f.severity == "FAIL"]
    warns = [f for f in findings if f.severity == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [f.to_dict() for f in findings],
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                },
                ensure_ascii=False, indent=1,
            )
        )
    else:
        for f in findings:
            if f.severity == "INFO":
                continue
            print(f"[{f.severity}] ({f.check}) domain={f.domain}: {f.detail}")
        print(
            f"contract-stamp-consistency: {len(findings)} findings, "
            f"{len(fails)} FAIL, {len(warns)} WARN"
        )
        if not fails and not warns:
            print("[contract-stamp-consistency] PASS")

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())


# ============================================================================
# RECOMMENDATION (非本脚本执法, 供接线方参考; 本脚本本身不改 governance_gates.yaml
# / commit_tiers.yaml / safe_commit.sh —— 那些文件今天有并发 agent 在改, 边界见交付说明)
#
# 建议分组: diff_correctness (阻断), 不是 system_health。
#
# 张力: governance_gates.yaml 开头"谁受害、何时受害"的分组哲学下, 本门读的是 DB 里的
# 真实数据状态 (accepted_partition/canonical_*/ingest_batch 行, lineage 落盘快照),
# 表面上很像 grain_uniqueness/continuity 那类"数据地基是否健康"的 system_health 检查
# (受害者=系统运行时, 应该挂 daily_update 而不是拦 commit)。
#
# 但判据不是"是否读 DB", 是"这次要查的状态是不是这次 diff 造成的、且伤害在 commit 那一刻
# 就已经完成":
#   - grain_uniqueness 查的是"库里现在有没有重复组" —— 与哪次 commit 无关, 是随时间推移
#     各种写入累积的结果, 受害时刻是下次 MERGE 跑批 (未来), 不是任何一次 commit。
#   - 本门查的是"这次改契约的 diff, 有没有连带打全 canonical/lineage/pointer" ——
#     如果没打全, 错误状态在 git commit 落地那一刻就已经完整存在 (下一个 pull 这份代码的
#     人立刻就会读到一个自相矛盾的契约声明), 不需要等"系统跑起来"才发作。这与已经登记为
#     diff_correctness 的 lineage_drift 同型 ("漂移的成因是这次 diff 改了 registry/schema
#     却没重生血缘——纯 diff 正确性"), 也与 moth_invariants 同型 ("违反它意味着数据/平台
#     此刻就是错的")。
#   - 触发条件本身就是"诊断变量"而非"数据地基是否健康"的问题: 只在 staged diff 命中契约
#     定义文件时才做实质检查 (见文件头 TRIGGER_PATH_GLOBS 建议), 其余绝大多数 commit
#     是 O(1) 空转 —— 这正是 diff_correctness 组的形状 (查这次改动本身对不对), 不是
#     system_health 组的形状 (无条件周期性扫全库)。
#   - fable 的窗口论据: 今天两次事故都发生在"改契约"与"补戳"之间的窗口; 若挂
#     system_health (daily_update 夜间跑), 这个窗口在白天整个开发 session 里都不设防,
#     而错误状态一旦被下一次 commit 叠加 (比如再改一处契约), 排查成本随时间推移非线性
#     上升。拦在 commit 就是拦在成本最低的时刻。
#
# 建议触发路径清单 (data-driven, 供接线方抄入 commit_tiers.yaml 触发判断或 safe_commit.sh):
#   backend/services/data_sources/*_contract.py
#   backend/services/data_sources/*_schema.py
#   backend/services/data_sources/contracts.py
#   backend/services/data_sources/calendar_contract.py
#   backend/services/data_sources/calendar_schema.py
#   backend/services/data_sources/accepted_schema.py
#   backend/services/data_sources/availability.py
#   backend/services/market_schema.py
#   backend/config/sync_registry.yaml
# 命中以上任一路径时才需要跑 check①②④ (它们依赖现算契约); check③ 一直很快 (~1.5s 扫
# 3600+ 文件), 可以无条件跑。
# ============================================================================
