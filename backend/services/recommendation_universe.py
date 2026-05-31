"""Configurable investable-universe filtering for recommendations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "recommendation_universe.yaml"


@dataclass(frozen=True)
class RecommendationUniversePolicy:
    policy_id: str = "production_a_share_investable_v1"
    require_stock_name: bool = True
    respect_excluded_stocks_table: bool = True
    exclude_name_regex: tuple[str, ...] = ()
    exclude_stock_code_prefixes: tuple[str, ...] = ()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load recommendation_universe.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def load_recommendation_universe_policy(
    path: str | Path | None = None,
) -> RecommendationUniversePolicy:
    raw = _load_yaml(Path(path) if path is not None else CONFIG_PATH)
    return RecommendationUniversePolicy(
        policy_id=str(raw.get("policy_id") or "production_a_share_investable_v1"),
        require_stock_name=bool(raw.get("require_stock_name", True)),
        respect_excluded_stocks_table=bool(raw.get("respect_excluded_stocks_table", True)),
        exclude_name_regex=_as_tuple(raw.get("exclude_name_regex")),
        exclude_stock_code_prefixes=_as_tuple(raw.get("exclude_stock_code_prefixes")),
    )


def _table_exists(conn: Any, table_name: str) -> bool:
    return conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone() is not None


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except Exception:
        return row[index]


def _stock_names(conn: Any, stock_codes: list[str]) -> dict[str, str | None]:
    codes = sorted({str(code) for code in stock_codes if code})
    if not codes or not _table_exists(conn, "dim_active_a_stock"):  # rule-compliance: ok evidence=code-to-name-mapping
        return {code: None for code in codes}
    rows = conn.execute(
        """
        SELECT stock_code, stock_name
          FROM dim_active_a_stock  -- rule-compliance: ok evidence=code-to-name-mapping
         WHERE stock_code = ANY(?)
        """,
        (codes,),
    ).fetchall()
    names = {str(_row_value(row, "stock_code", 0)): _row_value(row, "stock_name", 1) for row in rows}
    for code in codes:
        names.setdefault(code, None)
    return names


def _explicit_exclusions(conn: Any, stock_codes: list[str]) -> dict[str, str]:
    codes = sorted({str(code) for code in stock_codes if code})
    if not codes or not _table_exists(conn, "excluded_stocks"):
        return {}
    rows = conn.execute(
        """
        SELECT stock_code, category, reason
          FROM excluded_stocks
         WHERE stock_code = ANY(?)
        """,
        (codes,),
    ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        category = str(_row_value(row, "category", 1) or "excluded_stocks")
        reason = str(_row_value(row, "reason", 2) or "").strip()
        out[str(_row_value(row, "stock_code", 0))] = (
            f"excluded_stocks:{category}" + (f":{reason}" if reason else "")
        )
    return out


def explain_universe_exclusions(
    conn: Any,
    stock_codes: list[str],
    *,
    policy: RecommendationUniversePolicy | None = None,
) -> dict[str, str]:
    policy = policy or load_recommendation_universe_policy()
    codes = sorted({str(code) for code in stock_codes if code})
    names = _stock_names(conn, codes)
    explicit = _explicit_exclusions(conn, codes) if policy.respect_excluded_stocks_table else {}
    regexes = [re.compile(pattern, flags=re.IGNORECASE) for pattern in policy.exclude_name_regex]
    exclusions: dict[str, str] = {}
    for code in codes:
        if code in explicit:
            exclusions[code] = explicit[code]
            continue
        if any(code.startswith(prefix) for prefix in policy.exclude_stock_code_prefixes):
            exclusions[code] = "stock_code_prefix_excluded"
            continue
        name = names.get(code)
        if policy.require_stock_name and not str(name or "").strip():
            exclusions[code] = "missing_stock_name"
            continue
        upper_name = str(name or "").upper()
        matched = next((pattern.pattern for pattern in regexes if pattern.search(upper_name)), None)
        if matched:
            exclusions[code] = f"name_regex:{matched}"
    return exclusions


def filter_investable_records(
    conn: Any,
    records: list[dict[str, Any]],
    *,
    policy: RecommendationUniversePolicy | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = policy or load_recommendation_universe_policy()
    exclusions = explain_universe_exclusions(
        conn,
        [str(row.get("stock_code") or "") for row in records],
        policy=policy,
    )
    filtered = [
        row
        for row in records
        if str(row.get("stock_code") or "") not in exclusions
    ]
    by_reason: dict[str, int] = {}
    for reason in exclusions.values():
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return filtered, {
        "policy_id": policy.policy_id,
        "input_rows": len(records),
        "kept_rows": len(filtered),
        "excluded_rows": len(records) - len(filtered),
        "excluded_by_reason": by_reason,
        "excluded_examples": dict(list(exclusions.items())[:20]),
    }
