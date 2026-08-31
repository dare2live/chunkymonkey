"""Fuyao (HiThink finance) adapter — official sibling checkout, not a vendored copy.

Dump sign+download lives in ``../fuyao/python/marketdb/providers/dump.py``.
This module only locates that tree and re-exports the downloader. It does not
import or run their ``marketdb`` DuckDB warehouse. REST calendar/identity
calls go through ``rest_json`` (stdlib urllib), not their ``marketdb``.

``FuyaoSource.fetch_raw`` is the sync_runner seam for ``source: fuyao`` registry
domains. Pagination for limit pools is ``page``/``size`` (1–200), never
``limit``/``offset``; callers never pass either (``_reject_limit_offset``).
The ticker list (``meta-tickers-list``) is the one exception to page/size:
the vendor itself paginates that endpoint with ``limit``/``offset``, but
pagination there is still fully internal to the adapter — callers still
never pass those keys. Formal LIVE_ADAPTER remains tushare; this class is
not routed through ``require_live_adapter``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from services.data_sources.sibling_repos import ensure_import_path

ALIAS = "fuyao"
API_BASE_URL = "https://fuyao.aicubes.cn"
_SHANGHAI = timezone(timedelta(hours=8))
MAX_PAGE_SIZE = 200
MAX_PAGES = 50
BANNED_PAGE_KEYS = frozenset({"limit", "offset"})
LIMIT_UP_REQUIRED_FIELDS = (
    "is_st",
    "seal_money",
    "limit_up_time",
    "continue_day_cnt",
)

POOL_API_PATHS = {
    "limit-up-pool": "/api/a-share/special-data/limit-up-pool",
    "limit-down-pool": "/api/a-share/special-data/limit-down-pool",
    "limit-break-pool": "/api/a-share/special-data/limit-break-pool",
}
AUCTION_SNAPSHOT_PATH = "/api/a-share/auction/snapshot"
AUCTION_BENCHMARK_PATH = "/api/a-share/auction/short-term-benchmark"
AUCTION_API_PATHS = {
    "auction-snapshot": AUCTION_SNAPSHOT_PATH,
    "auction-short-term-benchmark": AUCTION_BENCHMARK_PATH,
}

# ── Ticker list (stock_basic candidate source; capability only, not yet wired
#    into sync_registry.yaml — that switch is a separate cut). This endpoint
#    is limit/offset-paginated by the vendor itself (unlike the page/size pool
#    APIs above); pagination is still fully internal to the adapter, callers
#    never pass limit/offset (see _reject_limit_offset).
META_TICKERS_LIST_PATH = "/api/meta/tickers/list"
TICKER_API_PATHS = {
    "meta-tickers-list": META_TICKERS_LIST_PATH,
}
# Server-side + defensive client-side filter (mirrors the double-check already
# used by assignment_gap_recon.parse_fuyao_tickers). Module-level so it is not
# buried inside a function body.
TICKER_LIST_ASSET_TYPE = "a-share"
TICKER_LIST_PAGE_SIZE = 500
# Real full a-share universe (asset_type filtered) paged @500 is ~12 pages;
# the unfiltered full instrument universe (index/etf/lof/fx included) has been
# observed at 164 pages. 300 is a generous safety net above either.
TICKER_LIST_MAX_PAGES = 300
# tushare stock_basic `market` is board-level (主板/创业板/科创板/北交所);
# fuyao `exchange` is venue-level (SH/SZ/BJ). These are different axes — see
# ticker_exchange_to_market() docstring for exactly what is/isn't preserved.
TUSHARE_MARKET_BJ_LABEL = "北交所"


class FuyaoRestError(RuntimeError):
    def __init__(self, message: str, *, http: int | None = None, code: int | None = None):
        super().__init__(message)
        self.http = http
        self.code = code


class FuyaoPaginationError(ValueError):
    """Limit-pool pagination must use page/size, not TuShare limit/offset."""


class FuyaoMissingFieldsError(ValueError):
    """A required vendor field was absent from a landed row."""


def fuyao_root() -> Path:
    return ensure_import_path(ALIAS, strict=True)


def dump_downloader(*, api_key: str, cache_dir: Path, **kwargs: Any):
    """Official Parquet dump client (daily-k / daily-k-10d / adjustment-factors)."""
    ensure_import_path(ALIAS, strict=True)
    from marketdb.providers.dump import DumpDownloader  # noqa: E402

    return DumpDownloader(
        api_base_url=API_BASE_URL,
        api_key=api_key,
        cache_dir=Path(cache_dir),
        **kwargs,
    )


def dump_kinds():
    ensure_import_path(ALIAS, strict=True)
    from marketdb.providers.dump import DownloadKind  # noqa: E402

    return DownloadKind


def resolve_api_key() -> str | None:
    """Env ``HITHINK_FINANCE_API_KEY`` then official user-level credentials.env."""
    ensure_import_path(ALIAS, strict=True)
    from marketdb.credentials import resolve_api_key as _resolve  # noqa: E402

    return _resolve()


def rest_json(
    path: str,
    *,
    api_key: str,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    """GET an official REST path. Envelope ``code=0`` required. Does not use marketdb."""
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None}
    )
    url = API_BASE_URL.rstrip("/") + path
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={"X-api-key": api_key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            http = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raise FuyaoRestError(
            f"http {exc.code} {path}",
            http=int(exc.code),
        ) from exc
    except urllib.error.URLError as ext:
        raise FuyaoRestError(f"transport {path}: {ext}") from ext
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FuyaoRestError(f"non-json {path} http={http}") from exc
    code = int(payload.get("code") or 0)
    if code != 0:
        raise FuyaoRestError(
            f"code={code} message={payload.get('message')}",
            http=http,
            code=code,
        )
    return payload.get("data")


def compact_trade_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text[:10]
    digits = text.replace("-", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return digits
    return None


def shanghai_midnight_ms(yyyymmdd: str) -> int:
    compact = compact_trade_date(yyyymmdd)
    if not compact:
        raise ValueError(f"bad trade_date {yyyymmdd!r}")
    day = datetime.strptime(compact, "%Y%m%d").replace(tzinfo=_SHANGHAI)
    return int(day.timestamp() * 1000)


def shanghai_day_from_ms(ms: Any) -> str | None:
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(value / 1000, tz=_SHANGHAI).strftime("%Y%m%d")


def dashed_date(compact: str) -> str:
    c = compact_trade_date(compact)
    if not c:
        raise ValueError(f"bad date {compact!r}")
    return f"{c[:4]}-{c[4:6]}-{c[6:8]}"


def classify_fuyao_failure(exc: BaseException) -> str:
    """Failure classes stay separate (eng_gov §6). One HTTP 404 is not offline."""
    http = getattr(exc, "http", None)
    code = getattr(exc, "code", None)
    msg = str(exc).lower()
    if http in (401, 403) or code in (2002, 2004):
        return "auth"
    if http == 404:
        return "http_404"
    if code in (3001, 3004):
        return "product_mismatch"
    if code == 3002:
        return "not_ready"
    if code in (1001, 1002, 1003):
        return "missing_fields"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if (
        "transport" in msg
        or "connection" in msg
        or "nameresolution" in msg
        or "nodename nor servname" in msg
    ):
        return "connection_failure"
    if isinstance(exc, FuyaoMissingFieldsError):
        return "missing_fields"
    return "error"


def resolve_api_path(api: str) -> str:
    name = str(api or "").strip()
    if name.startswith("/"):
        return name
    if name in POOL_API_PATHS:
        return POOL_API_PATHS[name]
    if name in AUCTION_API_PATHS:
        return AUCTION_API_PATHS[name]
    if name in TICKER_API_PATHS:
        return TICKER_API_PATHS[name]
    known = sorted({*POOL_API_PATHS, *AUCTION_API_PATHS, *TICKER_API_PATHS})
    raise KeyError(f"fuyao: unknown api {api!r}; known={known}")


def _reject_limit_offset(params: dict[str, Any]) -> None:
    present = sorted(k for k in BANNED_PAGE_KEYS if k in params)
    if present:
        raise FuyaoPaginationError(
            "fuyao pagination is page/size (1-200), not limit/offset; "
            f"got {present}"
        )


def _page_size(params: dict[str, Any]) -> int:
    raw = params.get("size")
    size = MAX_PAGE_SIZE if raw is None else int(raw)
    if size < 1 or size > MAX_PAGE_SIZE:
        raise FuyaoPaginationError(
            f"fuyao size must be 1-{MAX_PAGE_SIZE}, got {size}"
        )
    return size


def flatten_limit_pool_items(
    items: Any,
    *,
    trade_date: str,
    require_limit_up_fields: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ts = str(item.get("thscode") or "").strip().upper()
        if not ts:
            continue
        if require_limit_up_fields:
            missing = [f for f in LIMIT_UP_REQUIRED_FIELDS if f not in item]
            if missing:
                raise FuyaoMissingFieldsError(
                    f"limit-up-pool missing fields {missing} on {ts}"
                )
        row = dict(item)
        row["ts_code"] = ts
        row["trade_date"] = trade_date
        rows.append(row)
    return rows


def ticker_exchange_to_market(exchange: Any) -> str:
    """Map fuyao ticker-list ``exchange`` (SH/SZ/BJ) to a tushare
    ``stock_basic``-shaped ``market`` value.

    ONLY guarantee preserved: the downstream predicate ``market != '北交所'``
    that ``security_master.refresh_active_a_stock_master()`` uses to exclude
    北交所 (backend/services/security_master.py:60-66) keeps working
    unchanged. Nothing more.

    Tushare's ``market`` is board-level semantics (主板/创业板/科创板/北交所).
    Fuyao's ``exchange`` is venue-level (SH/SZ/BJ). These are two different
    axes — this function does NOT translate one into the other. For the
    non-BJ branch it passes the raw exchange code through verbatim (honest
    pass-through of what the vendor actually said) rather than fabricating a
    board guess. Do not read a returned ``"SH"``/``"SZ"`` here as a tushare
    board value. If a future consumer needs true board semantics, fetch or
    derive it separately — do not extend this mapping to pretend equivalence.
    """
    code = str(exchange or "").strip().upper()
    if code == "BJ":
        return TUSHARE_MARKET_BJ_LABEL
    return code


def normalize_ticker_rows(items: Any) -> list[dict[str, Any]]:
    """Normalize fuyao ``/api/meta/tickers/list`` rows into the shape
    consumed from ``raw_tushare_stock_basic`` today (see
    ``security_master.py:60-66``): ``ts_code`` / ``symbol`` / ``name`` /
    ``market`` only.

    Only ``asset_type == TICKER_LIST_ASSET_TYPE`` rows survive — the vendor
    table also carries indices/ETF/LOF/FX rows that must not leak into a
    stock identity table.

    Tushare ``stock_basic`` columns with zero downstream consumption
    (area/industry/cnspell/list_date/act_name/act_ent_type) are intentionally
    NOT produced here — omitted, not fabricated as ``None`` or guessed
    values. sync_runner's batch writer (``_write_batch``) derives table
    columns from whatever keys are present in the rows (auto
    ``ALTER TABLE ... ADD COLUMN``), so omitting them is sufficient; it does
    not require column parity with the old tushare table.
    """
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("asset_type") or "") != TICKER_LIST_ASSET_TYPE:
            continue
        ts_code = str(item.get("thscode") or "").strip().upper()
        if not ts_code:
            continue
        rows.append(
            {
                "ts_code": ts_code,
                "symbol": str(item.get("ticker") or "").strip(),
                "name": item.get("name"),
                "market": ticker_exchange_to_market(item.get("exchange")),
            }
        )
    return rows


def auction_event_date(payload: Any, *, requested: str | None = None) -> str | None:
    """Query date for auction benchmark. Response ``timestamp`` is not event time."""
    data = payload if isinstance(payload, dict) else {}
    dashed = str(data.get("date") or "").strip()
    compact = compact_trade_date(dashed) or shanghai_day_from_ms(data.get("date_ms"))
    if compact:
        return compact
    return compact_trade_date(requested)


def flatten_auction_benchmark(
    payload: Any, *, requested: str | None = None
) -> list[dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    event = auction_event_date(data, requested=requested)
    rows: list[dict[str, Any]] = []
    for item in data.get("item") or []:
        if not isinstance(item, dict):
            continue
        ts = str(item.get("thscode") or "").strip().upper()
        if not ts:
            continue
        row = dict(item)
        row["ts_code"] = ts
        if event:
            row["trade_date"] = event
        if data.get("date_ms") is not None:
            row["date_ms"] = data.get("date_ms")
        # Preserve assembly time under a name that cannot be mistaken for the event.
        if data.get("timestamp") is not None:
            row["response_timestamp"] = data.get("timestamp")
        rows.append(row)
    return rows


def flatten_auction_snapshot(payload: Any) -> list[dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    rows: list[dict[str, Any]] = []
    for item in data.get("item") or []:
        if not isinstance(item, dict):
            continue
        ts = str(item.get("thscode") or "").strip().upper()
        if not ts:
            continue
        row = dict(item)
        row["ts_code"] = ts
        row["auction_phase"] = data.get("auction_phase")
        row["data_status"] = data.get("data_status")
        if data.get("timestamp") is not None:
            row["response_timestamp"] = data.get("timestamp")
        rows.append(row)
    return rows


RestFn = Callable[..., Any]


class FuyaoSource:
    """sync_runner adapter: ``fetch_raw(api, **params) -> list[dict]``."""

    def __init__(
        self,
        *,
        rest: RestFn | None = None,
        api_key: str | None = None,
    ) -> None:
        self._rest = rest or rest_json
        self._api_key = api_key

    def _key(self) -> str:
        if self._api_key:
            return self._api_key
        key = resolve_api_key()
        if not key:
            raise FuyaoRestError("fuyao api key missing", http=401)
        return key

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        _reject_limit_offset(params)
        path = resolve_api_path(api)
        if path in POOL_API_PATHS.values():
            return self._fetch_limit_pool(path, params)
        if path == AUCTION_BENCHMARK_PATH:
            return self._fetch_auction_benchmark(params)
        if path == AUCTION_SNAPSHOT_PATH:
            return self._fetch_auction_snapshot(params)
        if path == META_TICKERS_LIST_PATH:
            return self._fetch_ticker_list(params)
        raise KeyError(f"fuyao: unknown api {api!r}")

    def _fetch_limit_pool(
        self, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        size = _page_size(params)
        trade_date = compact_trade_date(
            params.get("trade_date") or params.get("date")
        )
        date_ms = params.get("date_ms")
        if date_ms is None and trade_date:
            date_ms = shanghai_midnight_ms(trade_date)
        if trade_date is None and date_ms is not None:
            trade_date = shanghai_day_from_ms(date_ms)
        if not trade_date:
            raise ValueError("fuyao limit pool requires trade_date or date_ms")
        require_up = path.endswith("limit-up-pool")
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            query: dict[str, Any] = {"page": page, "size": size, "date_ms": int(date_ms)}
            data = self._rest(path, api_key=self._key(), params=query)
            items = (data or {}).get("item") if isinstance(data, dict) else None
            page_rows = flatten_limit_pool_items(
                items or [],
                trade_date=trade_date,
                require_limit_up_fields=require_up,
            )
            rows.extend(page_rows)
            pagination = (data or {}).get("pagination") if isinstance(data, dict) else {}
            pages = int((pagination or {}).get("pages") or 1)
            total = int((pagination or {}).get("total") or 0)
            if not page_rows:
                break
            if page >= pages:
                break
            if total and len(rows) >= total:
                break
        else:
            raise FuyaoPaginationError(
                f"fuyao {path} exceeded {MAX_PAGES} pages without exhausting pagination"
            )
        return rows

    def _fetch_auction_benchmark(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        date = str(params.get("date") or "").strip()
        if not date:
            td = compact_trade_date(params.get("trade_date"))
            date = dashed_date(td) if td else ""
        query: dict[str, Any] = {}
        if date:
            query["date"] = date
        data = self._rest(
            AUCTION_BENCHMARK_PATH, api_key=self._key(), params=query or None
        )
        requested = compact_trade_date(date) if date else None
        return flatten_auction_benchmark(data, requested=requested)

    def _fetch_auction_snapshot(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        thscodes = str(params.get("thscodes") or "").strip()
        if not thscodes:
            raise ValueError("auction-snapshot requires thscodes")
        query = {
            "thscodes": thscodes,
            "stage": str(params.get("stage") or "final"),
        }
        data = self._rest(
            AUCTION_SNAPSHOT_PATH, api_key=self._key(), params=query
        )
        return flatten_auction_snapshot(data)

    def _fetch_ticker_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Page through /api/meta/tickers/list to exhaustion.

        Vendor pagination here is limit/offset (unlike the pool APIs'
        page/size), and is driven entirely internally — ``params`` has
        already passed ``_reject_limit_offset`` in ``fetch_raw`` and is not
        consulted for pagination. Termination: a page returning fewer than
        ``TICKER_LIST_PAGE_SIZE`` items is the last page. Because a page can
        land exactly on the page-size boundary, one extra (empty) request may
        be needed to confirm exhaustion — this is expected, not a bug.
        """
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(TICKER_LIST_MAX_PAGES):
            query = {
                "asset_type": TICKER_LIST_ASSET_TYPE,
                "limit": TICKER_LIST_PAGE_SIZE,
                "offset": offset,
            }
            data = self._rest(META_TICKERS_LIST_PATH, api_key=self._key(), params=query)
            items = (data or {}).get("item") if isinstance(data, dict) else None
            items = items or []
            for row in normalize_ticker_rows(items):
                ts_code = row["ts_code"]
                if ts_code in seen:
                    continue
                seen.add(ts_code)
                rows.append(row)
            if len(items) < TICKER_LIST_PAGE_SIZE:
                break
            offset += TICKER_LIST_PAGE_SIZE
        else:
            raise FuyaoPaginationError(
                f"fuyao {META_TICKERS_LIST_PATH} exceeded {TICKER_LIST_MAX_PAGES} "
                "pages without exhausting pagination"
            )
        return rows


__all__ = [
    "ALIAS",
    "API_BASE_URL",
    "AUCTION_API_PATHS",
    "FuyaoMissingFieldsError",
    "FuyaoPaginationError",
    "FuyaoRestError",
    "FuyaoSource",
    "LIMIT_UP_REQUIRED_FIELDS",
    "MAX_PAGE_SIZE",
    "META_TICKERS_LIST_PATH",
    "POOL_API_PATHS",
    "TICKER_API_PATHS",
    "TICKER_LIST_ASSET_TYPE",
    "TICKER_LIST_MAX_PAGES",
    "TICKER_LIST_PAGE_SIZE",
    "TUSHARE_MARKET_BJ_LABEL",
    "auction_event_date",
    "classify_fuyao_failure",
    "compact_trade_date",
    "dump_downloader",
    "dump_kinds",
    "flatten_auction_benchmark",
    "flatten_auction_snapshot",
    "flatten_limit_pool_items",
    "fuyao_root",
    "normalize_ticker_rows",
    "resolve_api_key",
    "resolve_api_path",
    "rest_json",
    "shanghai_day_from_ms",
    "shanghai_midnight_ms",
    "ticker_exchange_to_market",
]
