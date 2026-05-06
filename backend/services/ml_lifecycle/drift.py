"""特征漂移监控 (PSI).

PSI = sum_i (p_recent_i - p_train_i) * ln(p_recent_i / p_train_i)
    p_train_i / p_recent_i 各分位区间在 train/recent 样本中的占比.

经验阈值:
  PSI < 0.10           — 分布稳定
  0.10 ≤ PSI < 0.25    — 轻微漂移
  PSI ≥ 0.25           — 显著漂移

写入 mart_feature_drift 给 UI Tab 5 显示.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime
from typing import Iterable, Optional

from services.db import get_conn

logger = logging.getLogger("cm-drift")

PSI_OK_THRESHOLD = 0.10
PSI_WARN_THRESHOLD = 0.25
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


HISTOGRAM_DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_drift_histogram (
    model_id TEXT,
    feature_table TEXT NOT NULL,
    feature TEXT NOT NULL,
    window_name TEXT NOT NULL,
    bucket_version TEXT NOT NULL,
    bucket_id INTEGER NOT NULL,
    bucket_left DOUBLE,
    bucket_right DOUBLE,
    train_count BIGINT DEFAULT 0,
    recent_count BIGINT DEFAULT 0,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (model_id, feature_table, feature, window_name, bucket_version, bucket_id)
);
CREATE INDEX IF NOT EXISTS idx_drift_hist_model_feature
    ON mart_feature_drift_histogram(model_id, feature_table, feature, bucket_version);
"""

DRIFT_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_drift (
    snapshot_at TIMESTAMP NOT NULL,
    model_id TEXT,
    feature_set_id TEXT,
    feature TEXT NOT NULL,
    psi DOUBLE,
    n_train BIGINT,
    n_recent BIGINT,
    window_days INTEGER,
    severity TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (snapshot_at, model_id, feature)
);
ALTER TABLE mart_feature_drift ADD COLUMN IF NOT EXISTS feature_set_id TEXT;
CREATE INDEX IF NOT EXISTS idx_mart_feature_drift_snapshot
    ON mart_feature_drift(snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_mart_feature_drift_severity
    ON mart_feature_drift(severity, psi DESC);
CREATE INDEX IF NOT EXISTS idx_mart_feature_drift_feature_set
    ON mart_feature_drift(model_id, feature_set_id, snapshot_at DESC);
"""


def _finite_values(values: Iterable[float]) -> list[float]:
    out = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def _linear_quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[int(pos)]
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _unique_sorted(values: Iterable[float]) -> list[float]:
    out = []
    previous = None
    for value in values:
        if previous is None or value != previous:
            out.append(value)
            previous = value
    return out


def _histogram(values: list[float], edges: list[float]) -> list[int]:
    counts = [0 for _ in range(len(edges) - 1)]
    if not counts:
        return counts
    first = edges[0]
    last = edges[-1]
    for value in values:
        if value < first or value > last:
            continue
        for idx in range(len(edges) - 1):
            left = edges[idx]
            right = edges[idx + 1]
            is_last_bin = idx == len(edges) - 2
            if left <= value < right or (is_last_bin and value == right):
                counts[idx] += 1
                break
    return counts


def _quote_ident(name: str) -> str:
    parts = str(name or "").split(".")
    if not parts or any(not IDENT_RE.match(part) for part in parts):
        raise ValueError(f"unsafe identifier: {name!r}")
    return ".".join(f'"{part}"' for part in parts)


def _iso_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _resolve_as_of_date(
    conn,
    *,
    feature_table: str,
    date_col: str,
    feature_set_filter: str,
    feature_set_params: list[str],
    as_of_date: str | None,
) -> str:
    if as_of_date:
        return _iso_date(as_of_date)
    row = conn.execute(
        f"""
        SELECT MAX(CAST({_quote_ident(date_col)} AS DATE)) AS max_date
          FROM {_quote_ident(feature_table)}
         WHERE {_quote_ident(date_col)} IS NOT NULL
           {feature_set_filter}
        """,
        feature_set_params,
    ).fetchone()
    if row and row["max_date"]:
        return _iso_date(row["max_date"])
    return datetime.utcnow().date().isoformat()


def _histogram_counts_sql(
    conn,
    *,
    feature_table: str,
    date_col: str,
    feature: str,
    edges: list[float],
    recent_window_days: int,
    as_of_date: str | None = None,
    feature_set_id: str | None = None,
) -> tuple[list[int], int]:
    if len(edges) < 2:
        return [], 0
    table_sql = _quote_ident(feature_table)
    date_sql = _quote_ident(date_col)
    feature_sql = _quote_ident(feature)
    feature_set_filter, feature_set_params = _feature_set_filter(conn, feature_table, feature_set_id)
    anchor_date = _resolve_as_of_date(
        conn,
        feature_table=feature_table,
        date_col=date_col,
        feature_set_filter=feature_set_filter,
        feature_set_params=feature_set_params,
        as_of_date=as_of_date,
    )
    exprs = []
    params = []
    for idx in range(len(edges) - 1):
        op = "<=" if idx == len(edges) - 2 else "<"
        exprs.append(
            f"SUM(CASE WHEN {feature_sql} >= ? AND {feature_sql} {op} ? THEN 1 ELSE 0 END) AS b{idx}"
        )
        params.extend([float(edges[idx]), float(edges[idx + 1])])
    cursor = conn.execute(
        f"""
        SELECT {', '.join(exprs)}, COUNT(*) AS n_recent
          FROM {table_sql}
         WHERE CAST({date_sql} AS DATE) > CAST(? AS DATE) - INTERVAL ({recent_window_days}) DAY
           AND CAST({date_sql} AS DATE) <= CAST(? AS DATE)
           {feature_set_filter}
           AND {feature_sql} IS NOT NULL
        """,
        [*params, anchor_date, anchor_date, *feature_set_params],
    )
    row = cursor.fetchone()
    if not row:
        return [0 for _ in range(len(edges) - 1)], 0
    counts = [int(row[f"b{idx}"] or 0) for idx in range(len(edges) - 1)]
    return counts, int(row["n_recent"] or 0)


def ensure_drift_histogram_schema(conn) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(HISTOGRAM_DDL)
    else:
        for stmt in HISTOGRAM_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def ensure_drift_snapshot_schema(conn) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DRIFT_SNAPSHOT_DDL)
    else:
        for stmt in DRIFT_SNAPSHOT_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
          FROM information_schema.columns
         WHERE table_name = ? AND column_name = ?
        """,
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def _feature_set_filter(conn, feature_table: str, feature_set_id: str | None) -> tuple[str, list[str]]:
    if not feature_set_id:
        return "", []
    if not _has_column(conn, feature_table, "feature_set_id"):
        raise RuntimeError(f"{feature_table} has no feature_set_id column")
    return "AND feature_set_id = ?", [str(feature_set_id)]


def _psi_from_counts(train_counts: list[int], recent_counts: list[int]) -> float:
    eps = 1e-6
    t_total = sum(train_counts)
    r_total = sum(recent_counts)
    if t_total <= 0 or r_total <= 0:
        return float("nan")
    bucket_count = max(len(train_counts), 1)
    p_t = [(count + eps) / (t_total + eps * bucket_count) for count in train_counts]
    p_r = [(count + eps) / (r_total + eps * bucket_count) for count in recent_counts]
    return sum((recent - train) * math.log(recent / train) for train, recent in zip(p_t, p_r))


def _histogram_edges(values: list[float], *, n_bins: int) -> list[float]:
    if len(values) < n_bins:
        return []
    sorted_train = sorted(values)
    qs = [idx / n_bins for idx in range(n_bins + 1)]
    quantile_edges = _unique_sorted(_linear_quantile(sorted_train, q) for q in qs)
    if len(quantile_edges) >= 3:
        return quantile_edges

    unique_values = _unique_sorted(sorted_train)
    if len(unique_values) <= 1:
        return []
    if len(unique_values) <= n_bins:
        first = unique_values[0]
        last = unique_values[-1]
        midpoints = [
            (left + right) / 2.0
            for left, right in zip(unique_values, unique_values[1:])
        ]
        pad = max((last - first) * 1e-9, 1e-9)
        return [first - pad, *midpoints, last + pad]

    first = unique_values[0]
    last = unique_values[-1]
    if first == last:
        return []
    step = (last - first) / n_bins
    return [first + step * idx for idx in range(n_bins + 1)]


def _load_cached_train_histogram(
    conn,
    *,
    model_id: str | None,
    feature_table: str,
    feature: str,
    bucket_version: str,
) -> tuple[list[float], list[int]]:
    rows = conn.execute(
        """
        SELECT bucket_left, bucket_right, train_count
          FROM mart_feature_drift_histogram
         WHERE model_id IS NOT DISTINCT FROM ?
           AND feature_table = ?
           AND feature = ?
           AND window_name = 'train'
           AND bucket_version = ?
         ORDER BY bucket_id
        """,
        (model_id, feature_table, feature, bucket_version),
    ).fetchall()
    if not rows:
        return [], []
    edges = [float(rows[0]["bucket_left"])]
    counts = []
    for row in rows:
        edges.append(float(row["bucket_right"]))
        counts.append(int(row["train_count"] or 0))
    return edges, counts


def _write_histogram_rows(
    conn,
    *,
    model_id: str | None,
    feature_table: str,
    feature: str,
    window_name: str,
    bucket_version: str,
    edges: list[float],
    counts: list[int],
) -> None:
    computed_at = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        """
        DELETE FROM mart_feature_drift_histogram
         WHERE model_id IS NOT DISTINCT FROM ?
           AND feature_table = ?
           AND feature = ?
           AND window_name = ?
           AND bucket_version = ?
        """,
        (model_id, feature_table, feature, window_name, bucket_version),
    )
    rows = []
    for idx, count in enumerate(counts):
        train_count = count if window_name == "train" else 0
        recent_count = count if window_name == "recent" else 0
        rows.append((
            model_id,
            feature_table,
            feature,
            window_name,
            bucket_version,
            idx,
            float(edges[idx]),
            float(edges[idx + 1]),
            int(train_count),
            int(recent_count),
            computed_at,
        ))
    conn.executemany(
        """
        INSERT INTO mart_feature_drift_histogram
        (model_id, feature_table, feature, window_name, bucket_version, bucket_id,
         bucket_left, bucket_right, train_count, recent_count, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def compute_psi(
    train_values: Iterable[float],
    recent_values: Iterable[float],
    *,
    n_bins: int = 10,
) -> tuple[float, int, int]:
    """计算 PSI. 返回 (psi, n_train, n_recent).

    用 train_values 的分位数 (q0,q10,...,q100) 切桶, 然后比较两组在每个桶里的占比.
    """
    t = _finite_values(train_values)
    r = _finite_values(recent_values)

    n_t, n_r = len(t), len(r)
    if n_t < n_bins or n_r < n_bins:
        return float("nan"), n_t, n_r

    # 按 train 的分位数切桶
    sorted_train = sorted(t)
    qs = [idx / n_bins for idx in range(n_bins + 1)]
    edges = [_linear_quantile(sorted_train, q) for q in qs]
    # 微小扰动避免边界相等
    edges = _unique_sorted(edges)
    if len(edges) < 3:
        return float("nan"), n_t, n_r

    # bin 计数
    t_counts = _histogram(t, edges)
    r_counts = _histogram(r, edges)

    # 防 0
    eps = 1e-6
    t_total = sum(t_counts)
    r_total = sum(r_counts)
    p_t = [(count + eps) / (t_total + eps * len(edges)) for count in t_counts]
    p_r = [(count + eps) / (r_total + eps * len(edges)) for count in r_counts]

    psi = sum((recent - train) * math.log(recent / train) for train, recent in zip(p_t, p_r))
    return psi, n_t, n_r


def compute_psi_cached(
    train_values: Iterable[float],
    recent_values: Iterable[float],
    *,
    n_bins: int = 10,
) -> tuple[float, int, int, list[float], list[int], list[int]]:
    """Return PSI plus reusable histogram state.

    This keeps the same semantics as compute_psi but exposes bucket edges and
    counts so callers can persist the train baseline and only recompute recent
    counts on daily runs.
    """
    t = _finite_values(train_values)
    r = _finite_values(recent_values)
    n_t, n_r = len(t), len(r)
    edges = _histogram_edges(t, n_bins=n_bins)
    if len(edges) < 3 or n_r < n_bins:
        return float("nan"), n_t, n_r, edges, [], []
    train_counts = _histogram(t, edges)
    recent_counts = _histogram(r, edges)
    return _psi_from_counts(train_counts, recent_counts), n_t, n_r, edges, train_counts, recent_counts


def severity_for_psi(psi: float) -> str:
    if psi is None or (isinstance(psi, float) and math.isnan(psi)):
        return "unknown"
    if psi < PSI_OK_THRESHOLD:
        return "ok"
    if psi < PSI_WARN_THRESHOLD:
        return "warn"
    return "critical"


def compute_feature_drift(
    *,
    feature_table: str = "fact_feature_panel",
    feature_set_id: Optional[str] = None,
    feature_columns: Optional[list[str]] = None,
    train_window_days: int = 365,
    recent_window_days: int = 30,
    model_id: Optional[str] = None,
    as_of_date: Optional[str] = None,
) -> list[dict]:
    """对 feature_table 的每个数值列算 PSI.

    train_window: 历史 [today-train_window-recent, today-recent] 区间作为 train 基线
    recent_window: 最近 recent_window 天作为 recent 样本
    """
    with get_conn() as conn:
        # 拉所有数值列名
        if feature_columns is None:
            cols = conn.execute(
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = '{feature_table}' "
                f"  AND data_type IN ('DOUBLE','FLOAT','REAL','BIGINT','INTEGER')"
            ).fetchall()
            feature_columns = [c[0] for c in cols if not c[0].startswith(('stock_', 'date', 'snapshot'))]

        # 找日期列
        date_col_row = conn.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{feature_table}' "
            f"  AND column_name IN ('trade_date','date','snapshot_date','as_of_date') "
            f"LIMIT 1"
        ).fetchone()
        if not date_col_row:
            logger.warning(f"[drift] {feature_table} 没有可识别的日期列, 跳过")
            return []
        date_col = date_col_row[0]
        feature_set_filter, feature_set_params = _feature_set_filter(conn, feature_table, feature_set_id)
        anchor_date = _resolve_as_of_date(
            conn,
            feature_table=feature_table,
            date_col=date_col,
            feature_set_filter=feature_set_filter,
            feature_set_params=feature_set_params,
            as_of_date=as_of_date,
        )

        results = []
        for col in feature_columns:
            try:
                # train 段
                t_rows = conn.execute(f"""
                    SELECT {col} FROM {feature_table}
                     WHERE CAST({date_col} AS DATE) BETWEEN
                           CAST(? AS DATE) - INTERVAL ({train_window_days + recent_window_days}) DAY
                       AND CAST(? AS DATE) - INTERVAL ({recent_window_days}) DAY
                       {feature_set_filter}
                       AND {col} IS NOT NULL
                """, [anchor_date, anchor_date, *feature_set_params]).fetchall()
                # recent 段
                r_rows = conn.execute(f"""
                    SELECT {col} FROM {feature_table}
                     WHERE CAST({date_col} AS DATE) > CAST(? AS DATE) - INTERVAL ({recent_window_days}) DAY
                       AND CAST({date_col} AS DATE) <= CAST(? AS DATE)
                       {feature_set_filter}
                       AND {col} IS NOT NULL
                """, [anchor_date, anchor_date, *feature_set_params]).fetchall()
                t_vals = [r[0] for r in t_rows]
                r_vals = [r[0] for r in r_rows]
                psi, n_t, n_r = compute_psi(t_vals, r_vals)
                severity = severity_for_psi(psi)
                results.append({
                    "feature": col, "psi": psi,
                    "n_train": n_t, "n_recent": n_r,
                    "severity": severity, "model_id": model_id,
                    "feature_set_id": feature_set_id,
                    "as_of_date": anchor_date,
                })
            except Exception as exc:
                logger.warning(f"[drift] {col} 失败: {exc}")
                continue

        return results


def compute_feature_drift_with_histogram_cache(
    *,
    feature_table: str = "fact_feature_panel",
    feature_set_id: Optional[str] = None,
    feature_columns: Optional[list[str]] = None,
    train_window_days: int = 365,
    recent_window_days: int = 30,
    model_id: Optional[str] = None,
    n_bins: int = 10,
    refresh_baseline: bool = False,
    as_of_date: Optional[str] = None,
) -> list[dict]:
    """Compute PSI using a persistent train histogram baseline.

    First run for a model/feature/window builds both train and recent
    histograms. Later daily runs reuse the train bucket edges/counts and only
    scan recent rows, which is the intended production path for cron_daily.
    """
    feature_set_key = feature_set_id or "*"
    bucket_version = f"{feature_table}:feature_set={feature_set_key}:train{train_window_days}:recent{recent_window_days}:bins{n_bins}:v1"
    with get_conn() as conn:
        ensure_drift_histogram_schema(conn)
        if feature_columns is None:
            cols = conn.execute(
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = '{feature_table}' "
                f"  AND data_type IN ('DOUBLE','FLOAT','REAL','BIGINT','INTEGER')"
            ).fetchall()
            feature_columns = [c[0] for c in cols if not c[0].startswith(('stock_', 'date', 'snapshot'))]

        date_col_row = conn.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{feature_table}' "
            f"  AND column_name IN ('trade_date','date','snapshot_date','as_of_date') "
            f"LIMIT 1"
        ).fetchone()
        if not date_col_row:
            logger.warning(f"[drift] {feature_table} 没有可识别的日期列, 跳过")
            return []
        date_col = date_col_row[0]
        feature_set_filter, feature_set_params = _feature_set_filter(conn, feature_table, feature_set_id)
        anchor_date = _resolve_as_of_date(
            conn,
            feature_table=feature_table,
            date_col=date_col,
            feature_set_filter=feature_set_filter,
            feature_set_params=feature_set_params,
            as_of_date=as_of_date,
        )

        results = []
        for col in feature_columns:
            try:
                edges, train_counts = ([], []) if refresh_baseline else _load_cached_train_histogram(
                    conn,
                    model_id=model_id,
                    feature_table=feature_table,
                    feature=col,
                    bucket_version=bucket_version,
                )
                n_t = sum(train_counts)
                if not edges or not train_counts:
                    t_rows = conn.execute(f"""
                        SELECT {col} FROM {feature_table}
                         WHERE CAST({date_col} AS DATE) BETWEEN
                               CAST(? AS DATE) - INTERVAL ({train_window_days + recent_window_days}) DAY
                           AND CAST(? AS DATE) - INTERVAL ({recent_window_days}) DAY
                           {feature_set_filter}
                           AND {col} IS NOT NULL
                    """, [anchor_date, anchor_date, *feature_set_params]).fetchall()
                    t_vals = [r[0] for r in t_rows]
                    edges = _histogram_edges(_finite_values(t_vals), n_bins=n_bins)
                    if len(edges) < 3:
                        results.append({
                            "feature": col,
                            "psi": float("nan"),
                            "n_train": len(t_vals),
                            "n_recent": 0,
                            "severity": "unknown",
                            "model_id": model_id,
                            "feature_set_id": feature_set_id,
                            "as_of_date": anchor_date,
                            "drift_source": "histogram_cache",
                        })
                        continue
                    train_counts = _histogram(_finite_values(t_vals), edges)
                    n_t = len(_finite_values(t_vals))
                    _write_histogram_rows(
                        conn,
                        model_id=model_id,
                        feature_table=feature_table,
                        feature=col,
                        window_name="train",
                        bucket_version=bucket_version,
                        edges=edges,
                        counts=train_counts,
                    )

                recent_counts, n_recent = _histogram_counts_sql(
                    conn,
                    feature_table=feature_table,
                    date_col=date_col,
                    feature=col,
                    edges=edges,
                    recent_window_days=recent_window_days,
                    as_of_date=anchor_date,
                    feature_set_id=feature_set_id,
                )
                _write_histogram_rows(
                    conn,
                    model_id=model_id,
                    feature_table=feature_table,
                    feature=col,
                    window_name="recent",
                    bucket_version=bucket_version,
                    edges=edges,
                    counts=recent_counts,
                )
                psi = _psi_from_counts(train_counts, recent_counts)
                severity = severity_for_psi(psi)
                results.append({
                    "feature": col,
                    "psi": psi,
                    "n_train": n_t,
                    "n_recent": n_recent,
                    "severity": severity,
                    "model_id": model_id,
                    "feature_set_id": feature_set_id,
                    "as_of_date": anchor_date,
                    "drift_source": "histogram_cache",
                })
            except Exception as exc:
                logger.warning(f"[drift] {col} histogram cache 失败: {exc}")
                continue
        conn.commit()
        return results


def write_drift_snapshot(
    drift_results: list[dict],
    *,
    snapshot_at: Optional[str] = None,
    window_days: int = 30,
) -> int:
    """把 compute_feature_drift 结果写入 mart_feature_drift."""
    if not drift_results:
        return 0
    with get_conn() as conn:
        ensure_drift_snapshot_schema(conn)
        # Use Python time here. DuckDB's now() can require optional timezone
        # modules in lightweight environments, while the snapshot only needs a
        # stable run timestamp.
        if snapshot_at is None:
            snapshot_at = datetime.utcnow().isoformat(timespec="seconds")
        for r in drift_results:
            conn.execute("""
                INSERT INTO mart_feature_drift
                  (snapshot_at, model_id, feature_set_id, feature, psi, n_train, n_recent, window_days, severity, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT (snapshot_at, model_id, feature) DO UPDATE SET
                    feature_set_id = EXCLUDED.feature_set_id,
                    psi = EXCLUDED.psi,
                    n_train = EXCLUDED.n_train,
                    n_recent = EXCLUDED.n_recent,
                    window_days = EXCLUDED.window_days,
                    severity = EXCLUDED.severity
            """, (
                snapshot_at, r.get("model_id"), r.get("feature_set_id"), r["feature"],
                None if (r["psi"] != r["psi"]) else r["psi"],  # 过滤 NaN
                r["n_train"], r["n_recent"], window_days, r["severity"],
            ))
        conn.commit()
    return len(drift_results)
