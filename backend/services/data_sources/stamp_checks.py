"""check_contract_stamp_consistency 的五类检查 (2026-09-02 拆分).

从 check_contract_stamp_consistency.py 拆出 (原 1355 行触发
minimal-module-no-new-godfile god-file 断言)。本模块放"拿 stamp_discovery 的
发现结果去核对现算契约/库内值"的检查逻辑: check①pointer / check②canonical /
check③lineage / check④ingest_batch (含 payload_hash 重算辅助) / discovery 报告
(check_discovery)、以及 channels 占位检查 (check⑥)。

check⑤ (check_file_digest_pins, 文件级摘要钉住) **没有**放在本模块 —— 原因:
它被现有单测通过 `monkeypatch.setattr(ccsc, "REPO", tmp_path)`
直接改写脚本模块的全局 REPO 来控制其行为, 而 Python 函数的全局名字查找永远
落在"定义它的那个模块"的命名空间, 不落在"调用者引用它的模块"——把它挪到本文件
会让那 4 个测试的 monkeypatch 失效 (真实 REPO 会被用来解析测试用的 tmp_path
目标文件, 断言必然改变结果)。为保持"测试断言一行不许动"且不引入脚本反向依赖
本文件的循环导入, check_file_digest_pins 连同它对 REPO 的读取原样留在
check_contract_stamp_consistency.py 里, 只把它调用的发现机制
(discover_file_digest_pins 及其 family) 挪到 stamp_discovery.py。

拆分本身是纯结构重排, 零行为变化; 各函数/常量的说明文字原样搬自原文件对应
SECTION, 未作任何逻辑改动。
"""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import Any, Callable

from services.data_sources.stamp_types import DomainSpec, DOMAIN_BY_DATASET_ID, Finding
from services.data_sources.stamp_discovery import (
    classify_hash_column,
    discover_hash_columns,
    discover_lineage_stamp_records,
)

REPO = Path(__file__).resolve().parents[3]

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


