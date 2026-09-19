"""Shared date-partition land→accept mechanics for security-grained Tier0 facts.

Used by nominal OHLCV and same-day ST membership.  Not a plugin framework:
each domain still owns schema identity, contract factory, and public runtime.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any

from services.data_sources.accepted_schema import (
    ACCEPTED_PARTITION_DDL,
    ACCEPTED_TABLE,
    INGEST_BATCH_DDL,
    INGEST_BATCH_TABLE,
    verify_accepted_evidence_schema,
)
from services.data_sources.availability import (
    AvailabilityPolicy,
    SyncWindowError,
    publication_cutoff,
)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze(item) for item in value)
    return value


def schema_contract_hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        _plain(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(
        _plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SecurityDayDomain:
    """Immutable domain identity for one security×date accepted dataset."""

    domain: str
    dataset_id: str
    schema_id: str
    schema_version: str
    writer_id: str
    landing_table: str
    canonical_table: str
    provider_fields: tuple[str, ...]
    numeric_fields: tuple[str, ...]
    non_null_numeric_fields: tuple[str, ...]
    text_fields: tuple[str, ...]
    grain: tuple[str, ...]
    partition_field: str
    source: str
    api: str
    target_db: str
    compatibility_table: str
    contract_version: str
    coverage_start: str
    available_after_legacy: str
    availability_axis: str
    availability_rule: str
    availability_at: str
    population_kind: str
    population_label: str
    population_usage: str
    min_rows: int
    schema_payload: Mapping[str, Any]
    schema_hash: str
    # 2026-09-08: 声明「哪个数值字段 > 0 才算这一行真的发生了」。None = 该域没有活跃度概念
    # (如 stock_st: 是不是 ST 与有没有成交无关)。
    # 立此字段的原因: 供货商换到通达信后, 停牌日开始被落成一行 (tushare 七年从不落),
    # 于是「有名义 K 线 = 在交易」这个 universe 判据在不改规则的情况下改变了含义。
    # 判据要说它真正的意思, 而不同域的"真正发生了"是不同字段, 所以进 domain 规格不硬编码。
    activity_field: str | None = None
    # 2026-09-13: 域级增补列 —— 在 schema_payload["fields"] 里、但**不在** provider_fields 里
    # 的那些列。与 lineage_fields() 的六列不同: 那六列是所有 SecurityDay 域共有的血缘列,
    # 这里是**单域语义列** (daily 的 pre_close_origin 不该长在 stock_st 上)。
    # 实测依据: canonical_content_hash 只按 provider_fields 算, 同一批行带不带增补列算出的
    # content_hash 逐位相同 —— 所以加列不动既有 859 万行指纹, 也不动两份冻结快照。
    # 值由 accept 逐行从 landing payload 取, 缺键即 MISSING_ENRICHMENT 拒绝 (不按批兜底:
    # 同一批内不同行可以不同源, 按批兜底会把"这一行从哪来"压成"这一批从哪来")。
    enrichment_fields: tuple[str, ...] = ()
    # 2026-09-16: 域级逐行校验钩子。默认 None = 该域没有需要额外校验的 enrichment 值
    # (stock_st 不受影响, 它从不设置本字段)。daily 域在 _candidate_rows 把 enrichment
    # 拼进 provider 行之后调用它, 传入 {**provider, **enrichment} —— 校验规则本身
    # (pre_close_origin 取值集、kind==unknown⇔pre_close IS NULL) 定义在
    # nominal_ohlcv_acquire_rules.py, 这里只留一个通用调用点, 不在共享的 land→accept
    # 机制里 import 单域规则模块 (照本文件开篇"不是插件框架"的纪律: 每个域仍自己拥有
    # 校验逻辑, 共享机制只负责在正确的时刻调用它)。违反时必须抛
    # SecurityDayValidationError, 与其它逐行校验走同一条 REJECTED 路径。
    enrichment_validator: Callable[[Mapping[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        # canonical 行由 {**provider, **enrichment, <lineage 六键>} 合成, 后写的键**静默**
        # 覆盖先写的: enrichment 撞 provider 会悄悄改掉供应商原值, lineage 撞 enrichment 会
        # 悄悄丢掉增补值 —— 两种都不报错、不留痕。实测过这个覆盖行为确实发生。
        # 所以在**构造那一刻**就拒绝, 而不是等某天某行数据对不上才去查
        # (同 SecurityDayLandingBatch 删掉 source 默认值的理由: 把静默错变成构造期的显式错)。
        from services.data_sources.security_day_reader import lineage_fields

        enrichment = set(self.enrichment_fields)
        clash_provider = enrichment & set(self.provider_fields)
        clash_lineage = enrichment & {str(f["name"]) for f in lineage_fields()}
        if clash_provider or clash_lineage:
            raise ValueError(
                f"{self.domain}: enrichment_fields 与既有列重名 —— "
                f"撞 provider_fields={sorted(clash_provider)} "
                f"撞 lineage_fields={sorted(clash_lineage)}; "
                "canonical 行合成时后者会静默覆盖前者, 必须改名"
            )
        declared = {str(f["name"]) for f in self.schema_payload["fields"]}
        missing = enrichment - declared
        if missing:
            raise ValueError(
                f"{self.domain}: enrichment_fields {sorted(missing)} 不在 "
                "schema_payload['fields'] 里 —— 声明了却没有列可落, accept 会 KeyError"
            )

    @property
    def availability_policy(self) -> AvailabilityPolicy:
        from services.data_sources.availability import availability_policy_from_mapping

        return availability_policy_from_mapping(
            {
                "axis": self.availability_axis,
                "rule": self.availability_rule,
                "at": self.availability_at,
            },
            owner=self.domain,
        )


class SecurityDayError(RuntimeError):
    """Security-day acceptance control state cannot publish safely."""


class SecurityDayValidationError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SecurityDayLandingBatch:
    batch_id: str
    partition_value: str
    observed_at: datetime | str
    available_at: datetime | str
    rows: Iterable[Mapping[str, Any]]
    request: Mapping[str, Any]
    # 2026-09-01: 去掉 source 的默认值 "tushare"。本 dataclass 被 daily 与 stock_st 共用,
    # 而两域已不同源 (daily -> tdxhub, stock_st 仍 tushare), 默认值等于假设"所有 SecurityDay
    # 域同源" —— 该假设已不成立, 且失败方式是**静默**的: 漏传就默默拿到 tushare, 直到
    # accept 时才以 "batch source=... current=..." 炸出来。两个生产调用点本就显式传
    # (capture.py source=domain.source / transport.py source=batch.source), 只有测试在吃
    # 这个默认值 —— 删掉它把静默错变成构造期的显式错。
    source: str
    # 2026-09-13: contract_version 的默认值 "1" 同理删掉 —— 它是上面 source 默认值那个坑的
    # 另一半, 当时没一起删。daily 契约升到 v2 之后两域的合法值已经不同 (daily="2",
    # stock_st="1"), 默认值等于替调用方静默挑一个; 实测 10 个测试构造点都在吃它, 表现为
    # land 时 "batch contract_version='1' current='2'"。两个生产构造点本就显式传。
    contract_version: str


@dataclass(frozen=True)
class SecurityDayAcceptanceOutcome:
    status: str
    batch_id: str
    partition_value: str
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None


@dataclass(frozen=True)
class SecurityDayAcceptedPartition:
    dataset_id: str
    partition_value: str
    batch_id: str
    contract_hash: str
    config_hash: str
    content_hash: str
    row_count: int
    available_at: datetime
    accepted_at: datetime
    ts_codes: frozenset[str]
    # 分区里**全部**成员 (与 row_count / content_hash 对账的那一个, 不许过滤)。
    # active_ts_codes 是它的子集: domain.activity_field > 0 的那些。域没声明 activity_field
    # 时两者恒等。派生自同一批已过 content_hash 校验的行, 所以它和 ts_codes 同源同证。
    active_ts_codes: frozenset[str]


def _canonical_column_sql(field: Mapping[str, Any]) -> str:
    name = str(field["name"])
    parts = [name, str(field["duckdb_type"])]
    if not bool(field["nullable"]):
        parts.append("NOT NULL")
    return " ".join(parts)


def _columns(conn, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE {table}").fetchall()
    }


def _canonical_constraint_contract(conn, table: str) -> dict[str, Any]:
    """读出一张 canonical 表**实际**的约束 (不是 DDL 声称的)。

    照 margin_schema._constraint_contract 的形态 —— 那是本仓已有的同型实现, 不另造一套。
    """

    rows = conn.execute(
        """
        SELECT constraint_type, constraint_column_names
          FROM duckdb_constraints()
         WHERE table_name = ?
        """,
        [table],
    ).fetchall()
    primary_keys: set[tuple[str, ...]] = set()
    not_null: set[str] = set()
    for constraint_type, columns in rows:
        kind = str(constraint_type).upper()
        key = tuple(str(column) for column in (columns or []))
        if kind == "PRIMARY KEY":
            primary_keys.add(key)
        elif kind == "NOT NULL" and len(key) == 1:
            not_null.add(key[0])
    return {"primary_keys": primary_keys, "not_null": not_null}


def ensure_security_day_schema(conn, domain: SecurityDayDomain) -> None:
    """Create and verify landing/canonical/evidence tables for one domain."""

    fields = tuple(domain.schema_payload["fields"])
    columns_sql = ",\n        ".join(_canonical_column_sql(field) for field in fields)
    pk_sql = ", ".join(domain.schema_payload["primary_key"])
    ddl = (
        INGEST_BATCH_DDL,
        f"""
        CREATE TABLE IF NOT EXISTS {domain.landing_table} (
            batch_id VARCHAR NOT NULL,
            row_ordinal INTEGER NOT NULL,
            request_json VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            row_hash VARCHAR NOT NULL,
            PRIMARY KEY (batch_id, row_ordinal)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {domain.canonical_table} (
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
    conn.execute("BEGIN TRANSACTION")
    try:
        for statement in ddl:
            conn.execute(statement)
        verify_accepted_evidence_schema(conn, error_type=SecurityDayError)
        landing_cols = set(_columns(conn, domain.landing_table))
        if landing_cols != expected_landing:
            raise SecurityDayError(
                f"{domain.landing_table} schema drift: "
                f"missing={sorted(expected_landing - landing_cols)} "
                f"extra={sorted(landing_cols - expected_landing)}"
            )
        canonical_cols = set(_columns(conn, domain.canonical_table))
        if canonical_cols != expected_canonical:
            raise SecurityDayError(
                f"{domain.canonical_table} schema drift: "
                f"missing={sorted(expected_canonical - canonical_cols)} "
                f"extra={sorted(canonical_cols - expected_canonical)}"
            )
        # 2026-09-13: 只比列名集合守不住"约束漂移"。CREATE TABLE IF NOT EXISTS 对**既有**表
        # 是 no-op, 所以改 schema 里某列的 nullable 既不会被发现、也不会让表跟着变 —— 契约
        # 说可空而物理表仍 NOT NULL, 第一个 NULL 值进来才在 INSERT 处炸, 且错误信息与根因无关。
        # 照 margin_schema.ensure_margin_acceptance_schema 的形态补上 (本仓已有的同型实现)。
        # 实测: 补上这段后 daily(17/17) 与 stock_st(11/11) 今天都恰好一致, 不是靠放宽通过的。
        constraints = _canonical_constraint_contract(conn, domain.canonical_table)
        expected_not_null = {
            str(field["name"]) for field in fields if not bool(field["nullable"])
        }
        if constraints["not_null"] != expected_not_null:
            raise SecurityDayError(
                f"{domain.canonical_table} nullability drift: "
                f"表上非空={sorted(constraints['not_null'] - expected_not_null)} "
                f"契约要求非空但表上可空={sorted(expected_not_null - constraints['not_null'])} "
                "—— 契约与物理表不一致; 改 schema 的 nullable 必须同时 ALTER 既有表"
            )
        expected_primary = {tuple(domain.schema_payload["primary_key"])}
        if constraints["primary_keys"] != expected_primary:
            raise SecurityDayError(
                f"{domain.canonical_table} primary-key drift: "
                f"actual={sorted(constraints['primary_keys'])} "
                f"expected={sorted(expected_primary)}"
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


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise SecurityDayError(f"invalid partition={value!r}")
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise SecurityDayError(f"invalid partition={value!r}") from exc
    return compact


def _aware(value: datetime | str, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SecurityDayError(f"invalid {field}={value!r}") from exc
    else:
        raise SecurityDayError(f"invalid {field} type={type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SecurityDayError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _call(after_step: Callable[[str], None] | None, step: str) -> None:
    if after_step is not None:
        after_step(step)


def canonical_content_hash(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> str:
    payload = [
        {name: row[name] for name in fields}
        for row in sorted(rows, key=lambda item: str(item["ts_code"]))
    ]
    return sha256_text(stable_json(payload))


def _validate_provider_row(
    domain: SecurityDayDomain,
    row: Mapping[str, Any],
    *,
    partition: str,
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise SecurityDayValidationError("INVALID_ROW", "provider row must be a mapping")
    missing = [name for name in domain.provider_fields if name not in row]
    if missing:
        raise SecurityDayValidationError(
            "MISSING_FIELDS", f"missing provider fields: {missing}"
        )
    # 2026-09-18: 逐行文本可空 (stock_st v2 的 name 列) —— 哪些列可空由 schema 声明,
    # 不是 domain.text_fields 本身的成员资格决定的 (type/type_name 也在 text_fields
    # 里但仍是 NOT NULL)。可空性与"是不是文本列"是两个正交的轴。
    nullable_fields = {
        str(f["name"]) for f in domain.schema_payload["fields"] if f.get("nullable")
    }
    out: dict[str, Any] = {}
    for name in domain.provider_fields:
        value = row[name]
        if name == "trade_date":
            compact = _partition(value)
            if compact != partition:
                raise SecurityDayValidationError(
                    "PARTITION_MISMATCH",
                    f"row trade_date={compact} partition={partition}",
                )
            out[name] = date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
            continue
        if name == "ts_code":
            code = str(value or "").strip()
            if len(code) < 8 or "." not in code:
                raise SecurityDayValidationError(
                    "INVALID_TS_CODE", f"invalid ts_code={value!r}"
                )
            out[name] = code
            continue
        if name in domain.numeric_fields:
            if value is None:
                if name in domain.non_null_numeric_fields:
                    raise SecurityDayValidationError(
                        "NULL_NUMERIC", f"{name} cannot be null"
                    )
                out[name] = None
                continue
            try:
                out[name] = float(value)
            except (TypeError, ValueError) as exc:
                raise SecurityDayValidationError(
                    "INVALID_NUMERIC", f"{name}={value!r}"
                ) from exc
            continue
        if value is None:
            if name in nullable_fields:
                out[name] = None
                continue
            if name in domain.text_fields:
                raise SecurityDayValidationError("EMPTY_TEXT", f"{name} cannot be empty")
            value = ""
        text = str(value).strip()
        # "" 仍拒 (即便声明可空): 适配器必须显式传 None 才算"未报告", 传空字符串
        # 是另一种错误 (供应商给了空值, 不是没给)。
        if not text and name in domain.text_fields:
            raise SecurityDayValidationError("EMPTY_TEXT", f"{name} cannot be empty")
        out[name] = text
    return out


def land_security_day_batch(
    conn,
    domain: SecurityDayDomain,
    batch: SecurityDayLandingBatch,
    *,
    contract_hash: str,
    config_hash: str,
    after_step: Callable[[str], None] | None = None,
) -> str:
    """Tx-A: persist the complete provider response without universe filtering."""

    ensure_security_day_schema(conn, domain)
    batch_id = str(batch.batch_id or "").strip()
    if not batch_id:
        raise SecurityDayError("batch_id must be non-empty")
    partition = _partition(batch.partition_value)
    if str(batch.contract_version) != domain.contract_version:
        raise SecurityDayError(
            f"batch contract_version={batch.contract_version!r} "
            f"current={domain.contract_version!r}"
        )
    if str(batch.source) != domain.source:
        raise SecurityDayError(
            f"batch source={batch.source!r} current={domain.source!r}"
        )
    observed_at = _aware(batch.observed_at, "observed_at")
    available_at = _aware(batch.available_at, "available_at")
    if available_at < observed_at:
        raise SecurityDayError(
            "available_at cannot precede observed_at "
            f"(available_at={available_at.isoformat()} "
            f"observed_at={observed_at.isoformat()})"
        )
    rows = list(batch.rows)
    if not rows:
        raise SecurityDayError(f"{domain.domain} landing rejects empty provider rows")
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
                "contract_hash": contract_hash,
                "config_hash": config_hash,
                "observed_at": observed_at.isoformat(),
                "available_at": available_at.isoformat(),
                "request": request,
                "row_signatures": signatures,
            }
        )
    )
    existing = conn.execute(
        f"SELECT payload_hash, status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [batch_id],
    ).fetchone()
    if existing is not None:
        if existing[0] == payload_hash:
            return batch_id
        raise SecurityDayError(
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
                domain.dataset_id,
                domain.contract_version,
                contract_hash,
                config_hash,
                domain.writer_id,
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
            INSERT INTO {domain.landing_table} (
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


def _load_batch(conn, domain: SecurityDayDomain, batch_id: str) -> dict[str, Any]:
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
        [batch_id, domain.dataset_id],
    ).fetchone()
    if row is None:
        raise SecurityDayError(f"unknown batch_id={batch_id!r}")
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


def _validate_publication_time(
    domain: SecurityDayDomain,
    partition: str,
    available_at: datetime,
) -> None:
    try:
        cutoff = publication_cutoff(
            domain.availability_policy,
            partition_value=partition,
            trading_day_values=(partition,),
        ).astimezone(timezone.utc)
    except SyncWindowError as exc:
        raise SecurityDayValidationError(
            "INVALID_PUBLICATION_WINDOW", str(exc)
        ) from exc
    if available_at < cutoff:
        raise SecurityDayValidationError(
            "PREMATURE_PUBLICATION",
            f"available_at={available_at.isoformat()} cutoff={cutoff.isoformat()}",
        )


def _candidate_rows(
    conn,
    domain: SecurityDayDomain,
    batch_id: str,
    partition: str,
    *,
    available_at: datetime,
    contract_version: str,
    config_hash: str,
) -> tuple[dict[str, Any], ...]:
    landed = conn.execute(
        f"""
        SELECT row_ordinal, payload_json, row_hash
          FROM {domain.landing_table}
         WHERE batch_id = ?
         ORDER BY row_ordinal
        """,
        [batch_id],
    ).fetchall()
    if not landed:
        raise SecurityDayValidationError("EMPTY_LANDING", "landing has zero rows")
    built_at = datetime.now(timezone.utc)
    seen: set[str] = set()
    canonical: list[dict[str, Any]] = []
    for row_ordinal, payload_json, row_hash in landed:
        payload = json.loads(str(payload_json))
        if sha256_text(stable_json(payload)) != str(row_hash):
            raise SecurityDayValidationError(
                "ROW_HASH_MISMATCH", f"row_ordinal={row_ordinal}"
            )
        provider = _validate_provider_row(domain, payload, partition=partition)
        code = str(provider["ts_code"])
        if code in seen:
            raise SecurityDayValidationError(
                "DUPLICATE_GRAIN", f"duplicate ts_code={code}"
            )
        seen.add(code)
        enrichment: dict[str, Any] = {}
        for name in domain.enrichment_fields:
            value = payload.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise SecurityDayValidationError(
                    "MISSING_ENRICHMENT",
                    f"{name} 不在 landing payload 里 (ts_code={code}); 适配器必须逐行给出该值。"
                    "不按批兜底: 同一批内不同行可以不同源, 按批兜底会把「这一行的值从哪来」"
                    "压成「这一批从哪来」, 那是编造血缘。契约升版前落地的历史批次 payload "
                    "没有此键, 它们不从 landing 重生成 —— 其值由一次性回填按 "
                    "ingest_batch.source_name 补齐 (见重打脚本说明)。",
                )
            enrichment[name] = value
        if domain.enrichment_validator is not None:
            domain.enrichment_validator({**provider, **enrichment})
        canonical.append(
            {
                **provider,
                **enrichment,
                "available_at": available_at,
                "ingest_batch_id": batch_id,
                "source_row_hash": str(row_hash),
                "contract_version": contract_version,
                "config_hash": config_hash,
                "built_at": built_at,
            }
        )
    if len(canonical) < domain.min_rows:
        raise SecurityDayValidationError(
            "BELOW_MIN_ROWS",
            f"row_count={len(canonical)} min_rows={domain.min_rows}",
        )
    return tuple(canonical)


def accept_security_day_batch(
    conn,
    domain: SecurityDayDomain,
    batch_id: str,
    *,
    contract_hash: str,
    config_hash: str,
    after_step: Callable[[str], None] | None = None,
) -> SecurityDayAcceptanceOutcome:
    """Tx-B: validate landing, then atomically replace canonical + pointer."""

    ensure_security_day_schema(conn, domain)
    batch = _load_batch(conn, domain, batch_id)
    status = str(batch["status"])
    partition = _partition(batch["partition_value"])
    if status == "ACCEPTED":
        pointer = conn.execute(
            f"""
            SELECT row_count, content_hash FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND partition_value = ? AND batch_id = ?
            """,
            [domain.dataset_id, partition, batch_id],
        ).fetchone()
        if pointer is None:
            raise SecurityDayError("accepted batch missing accepted_partition pointer")
        return SecurityDayAcceptanceOutcome(
            "ACCEPTED",
            batch_id,
            partition,
            int(pointer[0]),
            str(pointer[1]),
        )
    if status == "REJECTED":
        return SecurityDayAcceptanceOutcome(
            "REJECTED",
            batch_id,
            partition,
            rejection_code="ALREADY_REJECTED",
        )
    if status != "LANDED":
        raise SecurityDayError(f"batch {batch_id!r} not LANDED: {status!r}")

    # 2026-09-02: 只比声明身份 (contract_version / writer_id)。batch 的 contract_hash /
    # config_hash / source_name 是落地时刻的冻结证据 (payload_hash 从它们派生), 指纹算法重打
    # 或换源之后必然与现算契约不等 —— 与之比相等会把遗留的 LANDED 批次全部判成 CONTRACT_DRIFT
    # 并写成 REJECTED。指针 (accepted_partition) 与 canonical 行照旧打**现算**契约的戳。
    # 见本文件同型注释 (git log --grep landing_seal_vs_contract_restamp)。
    wiring_mismatch = (
        str(batch["contract_version"]) != domain.contract_version
        or str(batch["writer_id"]) != domain.writer_id
    )
    if wiring_mismatch:
        code, detail = "CONTRACT_DRIFT", "landed contract_version/writer_id is no longer current"
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'REJECTED', validated_at = CURRENT_TIMESTAMP,
                   rejection_code = ?, rejection_detail = ?
             WHERE batch_id = ? AND status = 'LANDED'
            """,
            [code, detail, batch_id],
        )
        return SecurityDayAcceptanceOutcome(
            "REJECTED", batch_id, partition, rejection_code=code
        )

    available_at = _aware(batch["available_at"], "available_at")
    try:
        _validate_publication_time(domain, partition, available_at)
        canonical_rows = _candidate_rows(
            conn,
            domain,
            batch_id,
            partition,
            available_at=available_at,
            contract_version=domain.contract_version,
            config_hash=config_hash,
        )
        content_hash = canonical_content_hash(canonical_rows, domain.provider_fields)
    except SecurityDayValidationError as exc:
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                f"""
                UPDATE {INGEST_BATCH_TABLE}
                   SET status = 'REJECTED', validated_at = CURRENT_TIMESTAMP,
                       rejection_code = ?, rejection_detail = ?
                 WHERE batch_id = ? AND status = 'LANDED'
                """,
                [exc.code, exc.detail[:1000], batch_id],
            )
            _call(after_step, "after_rejection_update")
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
        return SecurityDayAcceptanceOutcome(
            "REJECTED", batch_id, partition, rejection_code=exc.code
        )

    field_names = [str(field["name"]) for field in domain.schema_payload["fields"]]
    placeholders = ", ".join("?" for _ in field_names)
    columns = ", ".join(field_names)
    insert_rows = [tuple(row[name] for name in field_names) for row in canonical_rows]
    observed_at = _aware(batch["observed_at"], "observed_at")
    accepted_at = datetime.now(timezone.utc)
    if accepted_at < available_at:
        accepted_at = available_at

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"DELETE FROM {domain.canonical_table} WHERE trade_date = ?",
            [date(int(partition[:4]), int(partition[4:6]), int(partition[6:8]))],
        )
        _call(after_step, "after_canonical_delete")
        conn.executemany(
            f"INSERT INTO {domain.canonical_table} ({columns}) VALUES ({placeholders})",
            insert_rows,
        )
        _call(after_step, "after_canonical_insert")
        conn.execute(
            f"""
            DELETE FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND partition_value = ?
            """,
            [domain.dataset_id, partition],
        )
        conn.execute(
            f"""
            INSERT INTO {ACCEPTED_TABLE} (
                dataset_id, partition_value, batch_id, contract_version, contract_hash,
                config_hash, row_count, content_hash, observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                domain.dataset_id,
                partition,
                batch_id,
                domain.contract_version,
                contract_hash,
                config_hash,
                len(canonical_rows),
                content_hash,
                observed_at,
                available_at,
                accepted_at,
            ],
        )
        _call(after_step, "after_accepted_pointer")
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'ACCEPTED',
                   validated_at = CURRENT_TIMESTAMP,
                   accepted_at = ?,
                   canonical_hash = ?,
                   canonical_row_count = ?
             WHERE batch_id = ? AND status = 'LANDED'
            """,
            [accepted_at, content_hash, len(canonical_rows), batch_id],
        )
        _call(after_step, "after_batch_accepted")
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
    return SecurityDayAcceptanceOutcome(
        "ACCEPTED",
        batch_id,
        partition,
        len(canonical_rows),
        content_hash,
    )


__all__ = [
    "SecurityDayAcceptanceOutcome",
    "SecurityDayAcceptedPartition",
    "SecurityDayDomain",
    "SecurityDayError",
    "SecurityDayLandingBatch",
    "SecurityDayValidationError",
    "accept_security_day_batch",
    "canonical_content_hash",
    "ensure_security_day_schema",
    "land_security_day_batch",
    "schema_contract_hash",
    "sha256_text",
    "stable_json",
    "_aware",
    "_freeze",
    "_plain",
]
