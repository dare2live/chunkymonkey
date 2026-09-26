"""E0 formal land→validate→accept for miaoxiang holders_top10 (tracer).

Requires disclosure execution handoff. Holders fact plane retired 2026-07-26 —
formal land→accept only. Never publishes DatasetSnapshot readiness.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any
from zoneinfo import ZoneInfo

from services.data_sources.accepted_schema import (
    ACCEPTED_PARTITION_DDL,
    ACCEPTED_TABLE,
    INGEST_BATCH_DDL,
    INGEST_BATCH_TABLE,
    verify_accepted_evidence_schema,
)
from services.data_sources.holders_top10_contract import (
    HoldersTop10Contract,
    load_holders_top10_contract,
    verify_holders_top10_contract,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    GRAIN,
    LANDING_TABLE,
    ENRICHMENT_FIELDS,
    PROVIDER_FIELDS,
    SCHEMA_CONTRACT,
    SOURCE,
    WRITER_ID,
)
from services.data_sources.holders_top10_skip_land import (
    find_accepted_batch_with_same_payload,
)
from services.data_sources.security_day_partition import (
    sha256_text,
    stable_json,
)


class HoldersTop10AcceptanceError(RuntimeError):
    """holders_top10 formal acceptance cannot proceed safely."""


class HoldersTop10ValidationError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class HoldersTop10LandingBatch:
    batch_id: str
    partition_value: str
    observed_at: datetime | str
    available_at: datetime | str | None
    rows: Iterable[Mapping[str, Any]]
    request: Mapping[str, Any]
    source: str = SOURCE
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True)
class HoldersTop10AcceptanceOutcome:
    status: str
    batch_id: str
    partition_value: str
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None
    # merge_new_grains 专用 (B1; 其它两种 delete_scope 恒为 0): 本批新增的观测
    # 行数 / 已存在 (GRAIN 命中) 因而只持有不写的行数 / 因组内出现新观测而重算
    # 替换掉的旧退出行数。
    inserted_rows: int = 0
    held_rows: int = 0
    exit_rows_replaced: int = 0


def _require_handoff(
    contract: HoldersTop10Contract,
    handoff: HoldersTop10Contract | None,
) -> HoldersTop10Contract:
    if handoff is None or handoff is not contract:
        raise HoldersTop10AcceptanceError(
            "holders_top10 formal land/accept requires disclosure execution_handoff "
            "(propagate_disclosure_execution_contract); naked writes are forbidden"
        )
    return verify_holders_top10_contract(handoff)


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise HoldersTop10AcceptanceError(f"invalid notice_date partition={value!r}")
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise HoldersTop10AcceptanceError(
            f"invalid notice_date partition={value!r}"
        ) from exc
    return compact


def _aware(value: datetime | str | None, field: str) -> datetime:
    if value is None:
        raise HoldersTop10AcceptanceError(f"{field} is required (fail closed)")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HoldersTop10AcceptanceError(f"invalid {field}={value!r}") from exc
    else:
        raise HoldersTop10AcceptanceError(f"invalid {field} type={type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HoldersTop10AcceptanceError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _notice_cutoff(partition: str) -> datetime:
    """Earliest legal visibility: notice_date 00:00 Asia/Shanghai."""

    local = datetime(
        int(partition[:4]),
        int(partition[4:6]),
        int(partition[6:8]),
        0,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    return local.astimezone(timezone.utc)


def _columns(conn, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE {table}").fetchall()
    }


def _canonical_column_sql(field: Mapping[str, Any]) -> str:
    name = str(field["name"])
    parts = [name, str(field["duckdb_type"])]
    if not bool(field["nullable"]):
        parts.append("NOT NULL")
    return " ".join(parts)


def ensure_holders_top10_acceptance_schema(conn) -> None:
    fields = tuple(SCHEMA_CONTRACT["fields"])
    columns_sql = ",\n        ".join(_canonical_column_sql(field) for field in fields)
    pk_sql = ", ".join(SCHEMA_CONTRACT["primary_key"])
    ddl = (
        INGEST_BATCH_DDL,
        f"""
        CREATE TABLE IF NOT EXISTS {LANDING_TABLE} (
            batch_id VARCHAR NOT NULL,
            row_ordinal INTEGER NOT NULL,
            request_json VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            row_hash VARCHAR NOT NULL,
            PRIMARY KEY (batch_id, row_ordinal)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {CANONICAL_TABLE} (
            {columns_sql},
            PRIMARY KEY ({pk_sql})
        )
        """,
        ACCEPTED_PARTITION_DDL,
    )
    expected_landing = {
        "batch_id",
        "row_ordinal",
        "request_json",
        "payload_json",
        "row_hash",
    }
    expected_canonical = {str(field["name"]) for field in fields}
    field_by_name = {str(field["name"]): field for field in fields}
    conn.execute("BEGIN TRANSACTION")
    try:
        for statement in ddl:
            conn.execute(statement)
        verify_accepted_evidence_schema(
            conn, error_type=HoldersTop10AcceptanceError
        )
        landing_cols = set(_columns(conn, LANDING_TABLE))
        if landing_cols != expected_landing:
            raise HoldersTop10AcceptanceError(
                f"{LANDING_TABLE} schema drift: "
                f"missing={sorted(expected_landing - landing_cols)} "
                f"extra={sorted(landing_cols - expected_landing)}"
            )
        canonical_cols = set(_columns(conn, CANONICAL_TABLE))
        # Forward-migrate nullable enrichment columns onto pre-v2 canary tables.
        for name in sorted(expected_canonical - canonical_cols):
            field = field_by_name[name]
            if not bool(field.get("nullable", True)):
                raise HoldersTop10AcceptanceError(
                    f"{CANONICAL_TABLE} missing non-null column {name!r}; "
                    "refusing silent widen"
                )
            conn.execute(
                f"ALTER TABLE {CANONICAL_TABLE} ADD COLUMN "
                f"{name} {field['duckdb_type']}"
            )
        canonical_cols = set(_columns(conn, CANONICAL_TABLE))
        if canonical_cols != expected_canonical:
            raise HoldersTop10AcceptanceError(
                f"{CANONICAL_TABLE} schema drift: "
                f"missing={sorted(expected_canonical - canonical_cols)} "
                f"extra={sorted(canonical_cols - expected_canonical)}"
            )
        # 2026-09-08: 主键也要比, 不只比列集合。
        # 此前这道漂移检查只问「列对不对」, 而 GRAIN 变了列一列没变 ——
        # 生产表带着旧 6 列 PK, 检查照样全绿, 直到某天两版同粒度撞车才炸。
        # 这是「门问的问题 ≠ 它想守的东西」的又一例: 它想守的是「表结构与契约一致」,
        # 实际只问了一半。加了 notice_date 进 GRAIN 之后这半边必须补上。
        pk_rows = conn.execute(
            "SELECT constraint_text FROM duckdb_constraints() "
            "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
            [CANONICAL_TABLE],
        ).fetchall()
        expected_pk = f"PRIMARY KEY({', '.join(SCHEMA_CONTRACT['primary_key'])})"
        actual_pk = str(pk_rows[0][0]).strip() if pk_rows else "(none)"
        if actual_pk.replace(" ", "") != expected_pk.replace(" ", ""):
            raise HoldersTop10AcceptanceError(
                f"{CANONICAL_TABLE} primary key drift: actual={actual_pk!r} "
                f"expected={expected_pk!r}; run backend/scripts/migrate_holders_top10_pk.py"
            )
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise


def _call(after_step: Callable[[str], None] | None, step: str) -> None:
    if after_step is not None:
        after_step(step)


def _validate_provider_row(
    row: Mapping[str, Any], *, partition: str
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise HoldersTop10ValidationError("INVALID_ROW", "provider row must be a mapping")
    missing = [name for name in PROVIDER_FIELDS if name not in row]
    if missing:
        raise HoldersTop10ValidationError(
            "MISSING_FIELDS", f"missing provider fields: {missing}"
        )
    notice = row.get("notice_date")
    if notice is None or str(notice).strip() == "":
        raise HoldersTop10ValidationError(
            "MISSING_NOTICE_DATE", "notice_date is required for availability axis"
        )
    notice_compact = _partition(notice)
    if notice_compact != partition:
        raise HoldersTop10ValidationError(
            "PARTITION_MISMATCH",
            f"row notice_date={notice_compact} partition={partition}",
        )
    stock = str(row.get("stock_code") or "").strip()
    if not stock:
        raise HoldersTop10ValidationError("INVALID_STOCK", "stock_code required")
    report = _partition(row.get("report_date"))
    holder_set = str(row.get("holder_set") or "").strip()
    if not holder_set:
        raise HoldersTop10ValidationError("EMPTY_TEXT", "holder_set cannot be empty")
    holder_name = str(row.get("holder_name") or "").strip()
    if not holder_name:
        raise HoldersTop10ValidationError("EMPTY_TEXT", "holder_name cannot be empty")
    try:
        holder_rank = int(row["holder_rank"])
        row_seq = int(row["row_seq"])
    except (TypeError, ValueError) as exc:
        raise HoldersTop10ValidationError(
            "INVALID_NUMERIC", "holder_rank/row_seq must be int"
        ) from exc
    ratio = row.get("hold_ratio_float")
    if ratio is not None:
        try:
            ratio = float(ratio)
        except (TypeError, ValueError) as exc:
            raise HoldersTop10ValidationError(
                "INVALID_NUMERIC", f"hold_ratio_float={ratio!r}"
            ) from exc
    is_exit = row.get("is_exit_row")
    if not isinstance(is_exit, bool):
        raise HoldersTop10ValidationError(
            "INVALID_EXIT_FLAG", "is_exit_row must be bool"
        )
    # schema v3 / contract v4 (2026-09-07): 身份键上 canonical。
    # is_holder_org 与 is_exit_row 同样严 —— 它是 holder_code 那一列 NULL 的唯一解释项,
    # 松掉它, canonical 上的空 holder_code 就分不清「个人」和「机构但没取到」。
    is_org = row.get("is_holder_org")
    if not isinstance(is_org, bool):
        raise HoldersTop10ValidationError(
            "INVALID_HOLDER_ORG_FLAG", f"is_holder_org must be bool; got {is_org!r}"
        )
    holder_code_raw = row.get("holder_code")
    holder_code = (
        None
        if holder_code_raw is None or str(holder_code_raw).strip() == ""
        else str(holder_code_raw).strip()
    )
    # 供应商只给机构编码, 个人恒空 —— 实测 2018-12-31 起两侧无例外
    # (机构 829,249 行空 0 条 / 个人 620,073 行空 620,073 条)。
    # 机构却没有 code = 供应商行为变了, 这时候静默放行会让身份键悄悄退化成名字。
    if is_org and holder_code is None:
        raise HoldersTop10ValidationError(
            "MISSING_ORG_HOLDER_CODE",
            f"is_holder_org=True 但 holder_code 为空 (holder_name={holder_name!r})",
        )
    enrichment: dict[str, Any] = {}
    for name in ENRICHMENT_FIELDS:
        value = row.get(name)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            enrichment[name] = None
            continue
        if name == "shares_approx":
            try:
                enrichment[name] = int(value)
            except (TypeError, ValueError) as exc:
                raise HoldersTop10ValidationError(
                    "INVALID_NUMERIC", f"shares_approx={value!r}"
                ) from exc
        elif name == "hold_change_num":
            try:
                enrichment[name] = float(value)
            except (TypeError, ValueError) as exc:
                raise HoldersTop10ValidationError(
                    "INVALID_NUMERIC", f"hold_change_num={value!r}"
                ) from exc
        else:
            enrichment[name] = str(value).strip()
    if enrichment.get("holder_name_norm") is None:
        enrichment["holder_name_norm"] = holder_name
    return {
        "stock_code": stock,
        "report_date": report,
        "holder_set": holder_set,
        "holder_rank": holder_rank,
        "row_seq": row_seq,
        "holder_name": holder_name,
        "holder_code": holder_code,
        "is_holder_org": is_org,
        "hold_ratio_float": ratio,
        "notice_date": notice_compact,
        "is_exit_row": is_exit,
        **enrichment,
    }


def land_holders_top10_batch(
    conn,
    batch: HoldersTop10LandingBatch,
    contract: HoldersTop10Contract,
    *,
    handoff: HoldersTop10Contract | None = None,
    after_step: Callable[[str], None] | None = None,
) -> str:
    """Tx-A: persist provider rows without universe filtering."""

    contract = _require_handoff(contract, handoff)
    ensure_holders_top10_acceptance_schema(conn)
    batch_id = str(batch.batch_id or "").strip()
    if not batch_id:
        raise HoldersTop10AcceptanceError("batch_id must be non-empty")
    partition = _partition(batch.partition_value)
    if str(batch.contract_version) != contract.contract_version:
        raise HoldersTop10AcceptanceError(
            f"batch contract_version={batch.contract_version!r} "
            f"current={contract.contract_version!r}"
        )
    if str(batch.source) != SOURCE:
        raise HoldersTop10AcceptanceError(
            f"batch source={batch.source!r} current={SOURCE!r}"
        )
    observed_at = _aware(batch.observed_at, "observed_at")
    available_at = _aware(batch.available_at, "available_at")
    if observed_at != available_at:
        raise HoldersTop10AcceptanceError(
            "available_at must equal observed_at for holders_top10 tracer "
            "(provider publication clock unavailable independently)"
        )
    rows = list(batch.rows)
    if not rows:
        raise HoldersTop10AcceptanceError("holders_top10 landing rejects empty rows")
    request = dict(batch.request)
    request_json = stable_json(request)
    landing_rows: list[tuple[Any, ...]] = []
    signatures: list[str] = []
    for ordinal, row in enumerate(rows, start=1):
        payload_json = stable_json(row)
        row_hash = sha256_text(payload_json)
        signatures.append(f"{ordinal}:{row_hash}")
        landing_rows.append((batch_id, ordinal, request_json, payload_json, row_hash))
    payload_hash = sha256_text(
        stable_json(
            {
                "partition": partition,
                "source": batch.source,
                "contract_version": batch.contract_version,
                "contract_hash": contract.contract_hash,
                "config_hash": contract.config_hash,
                "observed_at": observed_at.isoformat(),
                "available_at": available_at.isoformat(),
                "request": request,
                "row_signatures": signatures,
            }
        )
    )
    accepted_same = find_accepted_batch_with_same_payload(
        conn,
        partition=partition,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        row_signatures=signatures,
    )
    if accepted_same is not None:
        _call(after_step, "skip_accepted_same_payload")
        return accepted_same

    existing = conn.execute(
        f"SELECT payload_hash, status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [batch_id],
    ).fetchone()
    if existing is not None:
        if existing[0] == payload_hash:
            return batch_id
        raise HoldersTop10AcceptanceError(
            f"batch_id {batch_id!r} already exists with different payload"
        )

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"""
            INSERT INTO {INGEST_BATCH_TABLE} (
                batch_id, dataset_id, contract_version, contract_hash, config_hash,
                writer_id, partition_value, source_name, status, request_json,
                fragment_outcomes_json, expected_fragment_count, completed_fragment_count,
                failed_fragment_count, landing_row_count, payload_hash, observed_at,
                available_at, landed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'LANDED', ?, ?, 1, 1, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                batch_id,
                DATASET_ID,
                contract.contract_version,
                contract.contract_hash,
                contract.config_hash,
                WRITER_ID,
                partition,
                batch.source,
                request_json,
                stable_json([{"status": "success", "row_count": len(rows)}]),
                len(landing_rows),
                payload_hash,
                observed_at,
                available_at,
            ],
        )
        _call(after_step, "after_batch_insert")
        conn.executemany(
            f"""
            INSERT INTO {LANDING_TABLE} (
                batch_id, row_ordinal, request_json, payload_json, row_hash
            ) VALUES (?, ?, ?, ?, ?)
            """,
            landing_rows,
        )
        _call(after_step, "after_landing_insert")
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise
    _call(after_step, "after_landing_commit")
    return batch_id


def _load_batch(conn, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT status, partition_value, observed_at, available_at, contract_version,
               contract_hash, config_hash, canonical_hash, canonical_row_count,
               request_json, fragment_outcomes_json, expected_fragment_count,
               completed_fragment_count, failed_fragment_count, landing_row_count,
               payload_hash, source_name, writer_id
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ? AND dataset_id = ?
        """,
        [batch_id, DATASET_ID],
    ).fetchone()
    if row is None:
        raise HoldersTop10AcceptanceError(f"unknown batch_id={batch_id!r}")
    keys = (
        "status",
        "partition_value",
        "observed_at",
        "available_at",
        "contract_version",
        "contract_hash",
        "config_hash",
        "canonical_hash",
        "canonical_row_count",
        "request_json",
        "fragment_outcomes_json",
        "expected_fragment_count",
        "completed_fragment_count",
        "failed_fragment_count",
        "landing_row_count",
        "payload_hash",
        "source_name",
        "writer_id",
    )
    return dict(zip(keys, row, strict=True))


def _reject(
    conn,
    batch_id: str,
    *,
    code: str,
    detail: str,
) -> HoldersTop10AcceptanceOutcome:
    batch = _load_batch(conn, batch_id)
    partition = _partition(batch["partition_value"])
    conn.execute(
        f"""
        UPDATE {INGEST_BATCH_TABLE}
           SET status = 'REJECTED',
               validated_at = CURRENT_TIMESTAMP,
               rejection_code = ?,
               rejection_detail = ?
         WHERE batch_id = ?
        """,
        [code, detail[:500], batch_id],
    )
    return HoldersTop10AcceptanceOutcome(
        status="REJECTED",
        batch_id=batch_id,
        partition_value=partition,
        rejection_code=code,
    )


# content_hash 的字段表。两个算 hash 的地方 (_canonical_content_hash 按内存批次算、
# partition_pointer_stats 按库内分区算) **必须用同一份**, 否则两边的 hash 不可比,
# 而它们本来就是要互相对账的。2026-09-08 提成常量: 此前两处各抄一份, 我改 GRAIN 时
# 只改到一处就会让「批次 hash」与「分区 hash」永久不等且没有任何东西会红。
# notice_date 进 GRAIN 后不再重复列 —— dict 本来就按键去重, 所以 payload 与 hash 不变,
# 去掉只是别让 SELECT 里挂一个重复列。
_HASH_FIELDS: tuple[str, ...] = tuple(GRAIN) + tuple(
    f for f in ("holder_name", "hold_ratio_float", "notice_date") if f not in GRAIN
)


def _canonical_content_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    fields = list(_HASH_FIELDS)
    payload = [
        {name: row[name] for name in fields}
        for row in sorted(
            rows,
            key=lambda item: tuple(str(item[k]) for k in GRAIN),
        )
    ]
    return sha256_text(stable_json(payload))


def partition_pointer_stats(conn, partition: str) -> tuple[int, str]:
    """整个 notice_date 分区现算 row_count + content_hash (给 accepted 指针用)。

    2026-09-07 加。原本指针直接用本批次的 row_count/content_hash —— 那在
    「一个分区只由一个批次构成」时成立, 按股回填打破了这个前提: 同一个 notice_date
    会被多只股票各自的批次分别写入, 指针若只描述最后一批, 就与 canonical 里的实际内容
    对不上。照 ``disclosure_event_partition.partition_accepted_pointer_stats`` 的形态。

    2026-09-19 (cut_bshare_s3): 去下划线改公开名, 让 cleanup_out_of_scope_rows.py
    的 holders_top10_canonical kind 能像 org 那条用 partition_accepted_pointer_stats
    一样, 用 writer 自己的函数重打受影响分区的指针 (它不是 DisclosureEventDomain 域,
    没有那条共享路径可用)。
    """
    fields = list(_HASH_FIELDS)
    order = ", ".join(GRAIN)
    rows = conn.execute(
        f"SELECT {', '.join(fields)} FROM {CANONICAL_TABLE} "
        f"WHERE notice_date = ? ORDER BY {order}",
        [partition],
    ).fetchall()
    payload = [dict(zip(fields, r)) for r in rows]
    return len(payload), sha256_text(stable_json(payload))


def _candidate_rows(
    conn,
    batch_id: str,
    partition: str,
    *,
    available_at: datetime,
    contract: HoldersTop10Contract,
) -> tuple[dict[str, Any], ...]:
    cutoff = _notice_cutoff(partition)
    if available_at < cutoff:
        raise HoldersTop10ValidationError(
            "FORGED_AVAILABLE_AT",
            f"available_at={available_at.isoformat()} precedes notice_date "
            f"cutoff={cutoff.isoformat()}",
        )
    landed = conn.execute(
        f"""
        SELECT row_ordinal, payload_json, row_hash
          FROM {LANDING_TABLE}
         WHERE batch_id = ?
         ORDER BY row_ordinal
        """,
        [batch_id],
    ).fetchall()
    if not landed:
        raise HoldersTop10ValidationError("EMPTY_LANDING", "landing has zero rows")
    built_at = datetime.now(timezone.utc)
    seen: set[tuple[Any, ...]] = set()
    canonical: list[dict[str, Any]] = []
    for row_ordinal, payload_json, row_hash in landed:
        payload = json.loads(str(payload_json))
        if sha256_text(stable_json(payload)) != str(row_hash):
            raise HoldersTop10ValidationError(
                "ROW_HASH_MISMATCH", f"row_ordinal={row_ordinal}"
            )
        provider = _validate_provider_row(payload, partition=partition)
        key = tuple(provider[name] for name in GRAIN)
        if key in seen:
            raise HoldersTop10ValidationError(
                "DUPLICATE_GRAIN", f"duplicate grain={key}"
            )
        seen.add(key)
        canonical.append(
            {
                **provider,
                "available_at": available_at,
                "ingest_batch_id": batch_id,
                "source_row_hash": str(row_hash),
                "contract_version": contract.contract_version,
                "config_hash": contract.config_hash,
                "built_at": built_at,
            }
        )
    return tuple(canonical)


def accept_holders_top10_batch(
    conn,
    batch_id: str,
    contract: HoldersTop10Contract,
    *,
    handoff: HoldersTop10Contract | None = None,
    after_step: Callable[[str], None] | None = None,
    delete_scope: str = "partition",
) -> HoldersTop10AcceptanceOutcome:
    """Tx-B: validate landing, then atomically replace canonical + pointer.

    ``delete_scope`` 决定这一批替换 canonical 的**哪一块**, 必须与批次实际覆盖的范围一致:

    - ``"partition"``(默认, 日更路径): 删掉整个 notice_date 分区再写回。
      日更按公告日全市场拉, 一个批次**就是**那一天的全部内容, 所以整分区替换是对的 ——
      某只股从重拉结果里消失时, 它的旧行应当一并消失。
    - ``"stocks_in_batch"``(按股回填路径): 只删本批次涉及的 stock_code。
      按股回填时一个批次只含一只股, 用 ``"partition"`` 会把同一公告日其他股票的行一起抹掉,
      跑完 5,212 只之后每个分区只剩最后写的那一只。
    - ``"merge_new_grains"``(日更 / 回补路径, 刀 B1): 观测行只增不删 —— 批内
      GRAIN 已存在的行只持有(不 UPDATE、不 DELETE、不动 ``ingest_batch_id``),
      不存在的行插入; 派生行(退出行)只对批内有新观测的 ``(stock_code,
      report_date)`` 组重算并整组替换, 没有新观测的组(held-only)一概不碰 ——
      "供应商后来给的东西"从"替换我们 D 日的观测"变成"带取数时间的新观察"
      (spec_holders_pagination.md §4.1 模型规则 1)。

    2026-09-07 加 ``stocks_in_batch``。此前只有 ``"partition"`` 一种行为, 而
    ``org_holding`` 早已有 ``merge_grains`` 与
    ``canonical_delete_scope='report_dates_in_batch'`` 两级控制 —— 这是一个域
    没跟上另一个域的改进, 不是普遍缺陷 (``margin`` 按 trade_date 删没问题,
    一个交易日就是一个批次的完整内容)。
    """
    if delete_scope not in {"partition", "stocks_in_batch", "merge_new_grains"}:
        raise HoldersTop10AcceptanceError(
            f"unknown delete_scope={delete_scope!r}; "
            "allowed={'partition', 'stocks_in_batch', 'merge_new_grains'}"
        )

    contract = _require_handoff(contract, handoff)
    ensure_holders_top10_acceptance_schema(conn)
    batch = _load_batch(conn, batch_id)
    status = str(batch["status"])
    partition = _partition(batch["partition_value"])
    if status == "ACCEPTED":
        pointer = conn.execute(
            f"""
            SELECT row_count, content_hash FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND partition_value = ? AND batch_id = ?
            """,
            [DATASET_ID, partition, batch_id],
        ).fetchone()
        if pointer is None:
            raise HoldersTop10AcceptanceError(
                "accepted batch missing accepted_partition pointer"
            )
        return HoldersTop10AcceptanceOutcome(
            status="ACCEPTED",
            batch_id=batch_id,
            partition_value=partition,
            row_count=int(pointer[0]),
            content_hash=str(pointer[1]),
        )
    if status == "REJECTED":
        return HoldersTop10AcceptanceOutcome(
            status="REJECTED",
            batch_id=batch_id,
            partition_value=partition,
            rejection_code=str(
                conn.execute(
                    f"SELECT rejection_code FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
                    [batch_id],
                ).fetchone()[0]
            ),
        )
    if status != "LANDED":
        raise HoldersTop10AcceptanceError(f"batch status={status!r} not acceptible")
    # 2026-09-02: 不再拿 batch 的 contract_hash / config_hash (落地时刻的冻结封印) 与 handoff
    # 契约比相等 —— 指纹算法重打之后遗留的 LANDED 批次会全部被判 drift 而卡死。声明身份
    # (contract_version / source) 已在 land 时校验; 指针照旧打现算契约的戳。§15.6。

    available_at = _aware(batch["available_at"], "available_at")
    try:
        canonical = _candidate_rows(
            conn,
            batch_id,
            partition,
            available_at=available_at,
            contract=contract,
        )
    except HoldersTop10ValidationError as exc:
        return _reject(conn, batch_id, code=exc.code, detail=exc.detail)

    observed_at = _aware(batch["observed_at"], "observed_at")
    accepted_at = datetime.now(timezone.utc)
    if accepted_at < available_at:
        accepted_at = available_at
    field_names = [str(f["name"]) for f in SCHEMA_CONTRACT["fields"]]
    insert_cols = ", ".join(field_names)
    placeholders = ", ".join("?" for _ in field_names)

    if delete_scope == "merge_new_grains":
        return _accept_merge_new_grains(
            conn,
            batch_id=batch_id,
            partition=partition,
            canonical=canonical,
            contract=contract,
            observed_at=observed_at,
            available_at=available_at,
            accepted_at=accepted_at,
            field_names=field_names,
            insert_cols=insert_cols,
            placeholders=placeholders,
            after_step=after_step,
        )

    content_hash = _canonical_content_hash(canonical)
    row_count = len(canonical)
    values = [tuple(row[name] for name in field_names) for row in canonical]

    conn.execute("BEGIN TRANSACTION")
    try:
        if delete_scope == "stocks_in_batch":
            batch_stocks = sorted({str(row["stock_code"]) for row in canonical})
            marks = ", ".join("?" for _ in batch_stocks)
            conn.execute(
                f"DELETE FROM {CANONICAL_TABLE} "
                f"WHERE notice_date = ? AND stock_code IN ({marks})",
                [partition, *batch_stocks],
            )
        else:
            conn.execute(
                f"DELETE FROM {CANONICAL_TABLE} WHERE notice_date = ?",
                [partition],
            )
        _call(after_step, "after_canonical_delete")
        conn.executemany(
            f"INSERT INTO {CANONICAL_TABLE} ({insert_cols}) VALUES ({placeholders})",
            values,
        )
        _call(after_step, "after_canonical_insert")
        if delete_scope == "stocks_in_batch":
            # 分区由多个批次拼成, 指针必须描述合并后的整个分区而不是最后一批。
            row_count, content_hash = partition_pointer_stats(conn, partition)
        conn.execute(
            f"""
            INSERT INTO {ACCEPTED_TABLE} (
                dataset_id, partition_value, batch_id, contract_version,
                contract_hash, config_hash, row_count, content_hash,
                observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dataset_id, partition_value) DO UPDATE SET
                batch_id = excluded.batch_id,
                contract_version = excluded.contract_version,
                contract_hash = excluded.contract_hash,
                config_hash = excluded.config_hash,
                row_count = excluded.row_count,
                content_hash = excluded.content_hash,
                observed_at = excluded.observed_at,
                available_at = excluded.available_at,
                accepted_at = excluded.accepted_at
            """,
            [
                DATASET_ID,
                partition,
                batch_id,
                contract.contract_version,
                contract.contract_hash,
                contract.config_hash,
                row_count,
                content_hash,
                observed_at,
                available_at,
                accepted_at,
            ],
        )
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'ACCEPTED',
                   validated_at = CURRENT_TIMESTAMP,
                   accepted_at = ?,
                   canonical_row_count = ?,
                   canonical_hash = ?,
                   rejection_code = NULL,
                   rejection_detail = NULL
             WHERE batch_id = ?
            """,
            [accepted_at, row_count, content_hash, batch_id],
        )
        _call(after_step, "after_accept_update")
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise
    _call(after_step, "after_accept_commit")
    return HoldersTop10AcceptanceOutcome(
        status="ACCEPTED",
        batch_id=batch_id,
        partition_value=partition,
        row_count=row_count,
        content_hash=content_hash,
    )


def _accept_merge_new_grains(
    conn,
    *,
    batch_id: str,
    partition: str,
    canonical: tuple[dict[str, Any], ...],
    contract: HoldersTop10Contract,
    observed_at: datetime,
    available_at: datetime,
    accepted_at: datetime,
    field_names: list[str],
    insert_cols: str,
    placeholders: str,
    after_step: Callable[[str], None] | None,
) -> HoldersTop10AcceptanceOutcome:
    """``delete_scope="merge_new_grains"`` (刀 B1): 只增不删 + 组级退出重算。

    1. 批内观测行 (``is_exit_row=False``): GRAIN 在 canonical 里不存在 -> 插入;
       存在且 ``holder_name`` 相同 -> 持有 (不 UPDATE、不 DELETE, ``ingest_batch_id``
       不动); 存在但 ``holder_name`` 不同 -> ``ROW_SEQ_COLLISION`` (写方续号出错,
       fail-closed, B25b)。
    2. 批内退出行 (``is_exit_row=True``): 它们的 ``(stock_code, report_date)``
       必须包含于 touched (= 本批真正插入了新观测行的组) -- 否则
       ``EXIT_GROUP_NOT_TOUCHED`` (B27): 写方只该对有新观测的组派生, 不许对
       held-only 组重派生退出。通过后, 先删掉 touched 组在本分区已有的旧退出行,
       再插入批内退出行; ``exit_rows_replaced`` = 删除数。
    3. 指针整分区现算 (``partition_pointer_stats``), 与 ``stocks_in_batch`` 同理。
    """
    observation_rows = [row for row in canonical if not row["is_exit_row"]]
    exit_candidate_rows = [row for row in canonical if row["is_exit_row"]]

    affected_stocks = sorted({str(row["stock_code"]) for row in canonical})
    existing_grain_holder: dict[tuple[Any, ...], str] = {}
    if affected_stocks:
        marks = ", ".join("?" for _ in affected_stocks)
        existing_rows = conn.execute(
            f"SELECT {', '.join(GRAIN)}, holder_name FROM {CANONICAL_TABLE} "
            f"WHERE notice_date = ? AND stock_code IN ({marks})",
            [partition, *affected_stocks],
        ).fetchall()
        # zip() 而不是切片索引 existing_row[:len(GRAIN)] —— 部分连接封装的 Row
        # 类型 (如 services.duck_adapter.Row) 只实现了按 int/str 单键取值,
        # 不支持切片, 会抛 KeyError(slice(...))。
        row_field_order = list(GRAIN) + ["holder_name"]
        for existing_row in existing_rows:
            mapped = dict(zip(row_field_order, existing_row, strict=True))
            key = tuple(mapped[name] for name in GRAIN)
            existing_grain_holder[key] = str(mapped["holder_name"])

    to_insert_observation: list[dict[str, Any]] = []
    held_rows = 0
    for row in observation_rows:
        key = tuple(row[name] for name in GRAIN)
        if key in existing_grain_holder:
            if existing_grain_holder[key] == row["holder_name"]:
                held_rows += 1
                continue
            return _reject(
                conn,
                batch_id,
                code="ROW_SEQ_COLLISION",
                detail=(
                    f"grain={key!r} 已存在但 holder_name 不同: "
                    f"existing={existing_grain_holder[key]!r} batch={row['holder_name']!r}"
                ),
            )
        to_insert_observation.append(row)

    touched = {
        (str(row["stock_code"]), str(row["report_date"])) for row in to_insert_observation
    }
    exit_pairs = {
        (str(row["stock_code"]), str(row["report_date"])) for row in exit_candidate_rows
    }
    untouched_exit_pairs = sorted(exit_pairs - touched)
    if untouched_exit_pairs:
        return _reject(
            conn,
            batch_id,
            code="EXIT_GROUP_NOT_TOUCHED",
            detail=f"批内退出行涉及未被新观测触及的组: {untouched_exit_pairs!r}",
        )

    rows_to_insert = to_insert_observation + exit_candidate_rows

    conn.execute("BEGIN TRANSACTION")
    try:
        exit_rows_replaced = 0
        if touched:
            touched_list = sorted(touched)
            conditions = " OR ".join(
                "(stock_code = ? AND report_date = ?)" for _ in touched_list
            )
            params: list[Any] = [partition]
            for stock_code, report_date in touched_list:
                params.extend([stock_code, report_date])
            exit_rows_replaced = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {CANONICAL_TABLE} "
                    f"WHERE notice_date = ? AND is_exit_row AND ({conditions})",
                    params,
                ).fetchone()[0]
                or 0
            )
            conn.execute(
                f"DELETE FROM {CANONICAL_TABLE} "
                f"WHERE notice_date = ? AND is_exit_row AND ({conditions})",
                params,
            )
        _call(after_step, "after_canonical_delete")
        if rows_to_insert:
            values = [tuple(row[name] for name in field_names) for row in rows_to_insert]
            conn.executemany(
                f"INSERT INTO {CANONICAL_TABLE} ({insert_cols}) VALUES ({placeholders})",
                values,
            )
        _call(after_step, "after_canonical_insert")
        # 分区由多次 delta 批次拼成, 指针必须描述合并后的整个分区。
        row_count, content_hash = partition_pointer_stats(conn, partition)
        conn.execute(
            f"""
            INSERT INTO {ACCEPTED_TABLE} (
                dataset_id, partition_value, batch_id, contract_version,
                contract_hash, config_hash, row_count, content_hash,
                observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dataset_id, partition_value) DO UPDATE SET
                batch_id = excluded.batch_id,
                contract_version = excluded.contract_version,
                contract_hash = excluded.contract_hash,
                config_hash = excluded.config_hash,
                row_count = excluded.row_count,
                content_hash = excluded.content_hash,
                observed_at = excluded.observed_at,
                available_at = excluded.available_at,
                accepted_at = excluded.accepted_at
            """,
            [
                DATASET_ID,
                partition,
                batch_id,
                contract.contract_version,
                contract.contract_hash,
                contract.config_hash,
                row_count,
                content_hash,
                observed_at,
                available_at,
                accepted_at,
            ],
        )
        # merge 模式的 canonical_row_count 口径 = 本批插入 + 插入的退出行数
        # (不是 stocks_in_batch 那种整分区口径, V12 已知两种口径不同, 各自 docstring 声明)。
        canonical_row_count = len(rows_to_insert)
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'ACCEPTED',
                   validated_at = CURRENT_TIMESTAMP,
                   accepted_at = ?,
                   canonical_row_count = ?,
                   canonical_hash = ?,
                   rejection_code = NULL,
                   rejection_detail = NULL
             WHERE batch_id = ?
            """,
            [accepted_at, canonical_row_count, content_hash, batch_id],
        )
        _call(after_step, "after_accept_update")
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise
    _call(after_step, "after_accept_commit")
    return HoldersTop10AcceptanceOutcome(
        status="ACCEPTED",
        batch_id=batch_id,
        partition_value=partition,
        row_count=row_count,
        content_hash=content_hash,
        inserted_rows=len(to_insert_observation),
        held_rows=held_rows,
        exit_rows_replaced=exit_rows_replaced,
    )


def publish_accepted_holders_top10_partition(
    conn,
    batch: HoldersTop10LandingBatch,
    contract: HoldersTop10Contract | None = None,
) -> HoldersTop10AcceptanceOutcome:
    """Deprecated fused helper: thin alias to caller-only S1→S2 transport.

    Production writers use ``disclosure_dual_write`` →
    ``land_then_accept_disclosure_partition``. Kept for older tests.
    """

    from services.data_sources.disclosure_transport import (
        land_then_accept_disclosure_partition,
    )

    _ = verify_holders_top10_contract(contract or load_holders_top10_contract())
    return land_then_accept_disclosure_partition(
        "holders_top10",
        conn,
        partition=str(batch.partition_value),
        rows=list(batch.rows),
        observed_at=batch.observed_at,
        available_at=batch.available_at,
        batch_id=str(batch.batch_id),
        request=dict(batch.request),
    )


def runtime_surface() -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        "writer_id": WRITER_ID,
        "production_write": "formal_only",
        "legacy_mirror": "retired",
        "legacy_direct_write": "retired",
        "dataset_snapshot": "canary_scope_freezable_when_cutover_allowed",
        "provider_sync": "fixture_or_authorized_manual_only",
    }


__all__ = [
    "HoldersTop10AcceptanceError",
    "HoldersTop10AcceptanceOutcome",
    "HoldersTop10LandingBatch",
    "HoldersTop10ValidationError",
    "accept_holders_top10_batch",
    "ensure_holders_top10_acceptance_schema",
    "land_holders_top10_batch",
    "partition_pointer_stats",
    "publish_accepted_holders_top10_partition",
    "runtime_surface",
]
