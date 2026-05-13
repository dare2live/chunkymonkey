"""
AKShare 数据获取客户端

函数：月K线、日K线、交易日历。

说明：
- K 线优先走东财；失败后自动回退新浪 / 腾讯
- 缺失股票拉全历史，已存在股票走增量续拉
- 行业分类统一走 services.tdx_industry_client (通达信 tdxhy.cfg),
  本模块已不提供行业函数。
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from services.kline_source import aggregate_monthly_from_daily as _aggregate_monthly_from_daily
from services.kline_source import fetch_daily_akshare_fallbacks as _fetch_daily_akshare_fallbacks
from services.kline_source import normalize_date_value as _normalize_date_value
from services.kline_source import normalize_price_rows as _normalize_price_rows
from services.kline_source import payload_is_empty as _payload_is_empty
from services.kline_source import records_from_payload as _records_from_payload
from services.tdx_source import call_tdx_quotes_with_retry
from services.tdx_source import clear_tdxhub_unavailable as _clear_shared_tdxhub_unavailable
from services.tdx_source import get_tdxhub_unavailable_state as _get_shared_tdxhub_unavailable_state
from services.tdx_source import mark_tdxhub_unavailable as _mark_shared_tdxhub_unavailable
from services.tdx_source import tdxhub_circuit_open as _shared_tdxhub_circuit_open

logger = logging.getLogger("cm-api")
_MOOTDX_DEGRADED_TIMEOUT_THRESHOLD = 2
_MOOTDX_DEGRADED_COOLDOWN_SECONDS = 180

# 禁用代理，避免 akshare (requests) 走系统代理导致连接失败
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("all_proxy", None)
os.environ.pop("ALL_PROXY", None)
os.environ["NO_PROXY"] = "*"


class NetworkError(Exception):
    pass


def _summarize_tdxhub_attempts(attempts: list[dict]) -> str:
    failed = [item for item in attempts if not item.get("ok")]
    if not failed:
        return "tdxhub 正常"
    error_types = []
    for item in failed:
        error_type = item.get("error_type") or "error"
        if error_type not in error_types:
            error_types.append(error_type)
    return f"tdxhub {'/'.join(error_types)} ({len(failed)}服)"


def _normalize_fetch_date(value: Optional[str]) -> Optional[str]:
    return _normalize_date_value(value)


def _rows_latest_date(rows) -> Optional[str]:
    records = _records_from_payload(rows)
    if not records:
        return None
    normalized = [
        _normalize_fetch_date(row.get("date") or row.get("datetime"))
        for row in records
    ]
    normalized = [value for value in normalized if value]
    return max(normalized) if normalized else None


def _is_rows_fresh_enough(rows, expected_end_date: Optional[str]) -> bool:
    expected = _normalize_fetch_date(expected_end_date)
    latest = _rows_latest_date(rows)
    if not latest or not expected:
        return bool(latest)
    return latest >= expected


def _infer_etf_market(code: str) -> str:
    text = str(code or "").strip()
    if text.startswith("15"):
        return "sz"
    if text.startswith("51") or text.startswith("56") or text.startswith("58"):
        return "sh"
    return ""


def _normalize_etf_spot_rows(payload) -> list[dict]:
    results = []
    for raw in _records_from_payload(payload):
        row = {str(key).replace(" ", ""): value for key, value in raw.items()}
        code = str(row.get("基金代码") or "").strip()
        name = str(row.get("基金名称") or "").strip()
        market = _infer_etf_market(code)
        if not code or not market:
            continue
        results.append({
            "code": code,
            "name": name,
            "market": market,
            "asset_type": "etf",
        })
    return results


def _tdx_payload_to_kline_rows(payload, start_fmt: str, end_fmt: str) -> list[dict]:
    rows = _normalize_price_rows(payload, "tdxhub")
    return [
        row for row in rows
        if start_fmt <= row["date"] <= end_fmt
    ]


def _get_tdxhub_unavailable_state() -> dict[str, object]:
    return _get_shared_tdxhub_unavailable_state()


def _mark_tdxhub_unavailable(summary: str, attempts: list[dict], *, cooldown_seconds: Optional[int] = None) -> None:
    if cooldown_seconds is None:
        _mark_shared_tdxhub_unavailable(summary, attempts)
        return
    _mark_shared_tdxhub_unavailable(summary, attempts, cooldown_seconds=cooldown_seconds)


def _clear_tdxhub_unavailable() -> None:
    _clear_shared_tdxhub_unavailable()


def _tdxhub_circuit_open() -> bool:
    return _shared_tdxhub_circuit_open()


def _count_tdxhub_timeout_failures(attempts: list[dict]) -> int:
    total = 0
    for item in attempts or []:
        if item.get("ok"):
            continue
        error_text = str(item.get("error_type") or item.get("error") or "").lower()
        if "timeout" in error_text:
            total += 1
    return total


async def _fetch_daily_tdxhub_with_diagnostics(code: str, start_date: str, end_date: str):
    """用 tdxhub 从通达信服务器获取日K线，并返回逐服务器诊断。"""
    if _tdxhub_circuit_open():
        state = _get_tdxhub_unavailable_state()
        cached_attempts = list(state.get("attempts") or [])
        return None, None, {
            "ok": False,
            "cached": True,
            "attempts": cached_attempts,
            "summary": state.get("summary") or "tdxhub circuit open",
            "timeout_failures": _count_tdxhub_timeout_failures(cached_attempts),
            "fallback_recommended": True,
        }

    diagnostics = {
        "ok": False,
        "attempts": [],
        "summary": "tdxhub 未执行",
        "timeout_failures": 0,
        "fallback_recommended": False,
    }

    try:
        start_dt = datetime.strptime(start_date[:8], "%Y%m%d")
        end_dt = datetime.strptime(end_date[:8], "%Y%m%d")
        days_needed = max((end_dt - start_dt).days + 30, 150)
    except Exception:
        days_needed = 800

    start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

    def _fetch_on_client(client):
        records = client.bars_records(symbol=code, frequency=9, offset=min(days_needed, 800))
        if not records:
            raise ValueError("empty")

        rows = _tdx_payload_to_kline_rows(records, start_fmt, end_fmt)
        if not rows:
            raise ValueError("empty_after_filter")
        return rows

    try:
        payload, _source, attempts = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_tdx_quotes_with_retry(
                _fetch_on_client,
                action_name=f"bars[{code}]",
                collect_attempts=True,
            ),
        )
        rows = _tdx_payload_to_kline_rows(payload, start_fmt, end_fmt)
        if not rows:
            raise ValueError("empty_after_filter")
        diagnostics["attempts"] = attempts
        diagnostics["ok"] = True
        diagnostics["server"] = attempts[-1]["server"] if attempts else None
        diagnostics["elapsed_sec"] = round(sum(float(item.get("elapsed_sec") or 0.0) for item in attempts), 3)
        diagnostics["timeout_failures"] = _count_tdxhub_timeout_failures(attempts)
        diagnostics["fallback_recommended"] = diagnostics["timeout_failures"] >= _MOOTDX_DEGRADED_TIMEOUT_THRESHOLD
        diagnostics["summary"] = f"tdxhub {diagnostics['server']}"
        if diagnostics["fallback_recommended"]:
            diagnostics["summary"] = (
                f"tdxhub {diagnostics['server']} · timeout x{diagnostics['timeout_failures']}"
            )
            _mark_tdxhub_unavailable(
                f"tdxhub timeout x{diagnostics['timeout_failures']}，切换 fallback",
                attempts,
                cooldown_seconds=_MOOTDX_DEGRADED_COOLDOWN_SECONDS,
            )
        else:
            _clear_tdxhub_unavailable()
        return rows, "tdxhub", diagnostics
    except ImportError:
        diagnostics["summary"] = "tdxhub 未安装"
        return None, None, diagnostics
    except Exception as e:
        diagnostics["attempts"] = list(getattr(e, "tdx_attempts", []) or [])
        diagnostics["timeout_failures"] = _count_tdxhub_timeout_failures(diagnostics["attempts"])
        diagnostics["fallback_recommended"] = True
        diagnostics["summary"] = _summarize_tdxhub_attempts(diagnostics["attempts"]) if diagnostics["attempts"] else str(e)
        _mark_tdxhub_unavailable(diagnostics["summary"], diagnostics["attempts"])
        logger.debug(f"[tdxhub] {code} 失败: {e}")
        return None, None, diagnostics


async def _safe_akshare_call(func, *args, timeout=30, retries=2, **kwargs):
    """带重试和超时的 akshare 调用"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, lambda: func(*args, **kwargs)),
                timeout=timeout
            )
            if _payload_is_empty(result):
                return None
            return result
        except asyncio.TimeoutError:
            last_err = TimeoutError(f"超时 ({timeout}s)")
            logger.debug(f"[akshare] {func.__name__} 超时 ({attempt+1}/{retries+1})")
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "网络" in err_str or "connect" in err_str.lower() or "timeout" in err_str.lower():
                last_err = NetworkError(err_str)
            logger.debug(f"[akshare] {func.__name__} 失败 ({attempt+1}): {e}")

        if attempt < retries:
            await asyncio.sleep(2 * (attempt + 1))

    if last_err:
        raise last_err
    return None


async def _fetch_daily_tdxhub(code: str, start_date: str, end_date: str):
    """用 tdxhub 从通达信服务器获取日K线（首选数据源）"""
    rows, source, _ = await _fetch_daily_tdxhub_with_diagnostics(code, start_date, end_date)
    return rows, source


def _raise_daily_fallback_error(diagnostics: dict) -> None:
    if diagnostics.get("all_empty"):
        raise ValueError(diagnostics.get("last_error") or "all_sources_empty(eastmoney/sina/tx)")
    if diagnostics.get("last_error"):
        raise ValueError(diagnostics["last_error"])


async def _fetch_daily_with_fallback(
    code: str,
    start_date: str,
    end_date: str,
    *,
    prefer_fallback: bool = False,
):
    best_rows = []
    best_source = ""
    best_latest = None
    fallback_diagnostics = None

    def _remember_result(rows, source):
        nonlocal best_rows, best_source, best_latest
        latest = _rows_latest_date(rows)
        if not rows or not latest:
            return latest
        if not best_rows or best_latest is None or latest > best_latest:
            best_rows = rows
            best_source = source
            best_latest = latest
        return latest

    if prefer_fallback:
        rows_fb, src_fb, fallback_diagnostics = await _fetch_daily_akshare_fallbacks(
            code,
            start_date,
            end_date,
            safe_call=_safe_akshare_call,
        )
        if rows_fb:
            _remember_result(rows_fb, src_fb)
        if _is_rows_fresh_enough(rows_fb, end_date):
            return rows_fb, src_fb

    # 优先级1: tdxhub（通达信服务器，Mac原生）
    rows_m, src_m, diagnostics_m = await _fetch_daily_tdxhub_with_diagnostics(code, start_date, end_date)
    if rows_m:
        _remember_result(rows_m, src_m)
        if diagnostics_m.get("fallback_recommended"):
            logger.warning(
                f"[日K] {code} tdxhub 连续超时，后续短时回退 fallback（{diagnostics_m.get('summary') or 'tdxhub degraded'}）"
            )
        if _is_rows_fresh_enough(rows_m, end_date):
            return rows_m, src_m

    if fallback_diagnostics is None:
        rows_fb, src_fb, fallback_diagnostics = await _fetch_daily_akshare_fallbacks(
            code,
            start_date,
            end_date,
            safe_call=_safe_akshare_call,
        )
        if rows_fb:
            _remember_result(rows_fb, src_fb)
        if _is_rows_fresh_enough(rows_fb, end_date):
            return rows_fb, src_fb

    if best_rows:
        return best_rows, best_source

    _raise_daily_fallback_error(fallback_diagnostics or {})
    return None, ""


async def probe_stock_kline_fallback_preference(code: str, start_date: str, end_date: str) -> dict:
    _, _, diagnostics = await _fetch_daily_tdxhub_with_diagnostics(code, start_date, end_date)
    prefer_fallback = (
        not diagnostics.get("ok")
        or bool(diagnostics.get("cached"))
        or bool(diagnostics.get("fallback_recommended"))
    )
    return {
        "sample_code": code,
        "prefer_fallback": prefer_fallback,
        "reason": diagnostics.get("summary") or ("tdxhub unavailable" if prefer_fallback else "tdxhub healthy"),
        "elapsed_sec": float(diagnostics.get("elapsed_sec") or 0.0),
        "timeout_failures": int(diagnostics.get("timeout_failures") or 0),
    }


async def fetch_stock_kline_monthly(code: str, limit: int = 36,
                                    start_date: str = "20230101",
                                    end_date: Optional[str] = None):
    """获取月K线。东财失败时回退到日K聚合月K。

    Phase ψ.5 根因 1 残留修复: end_date 必须由调用方显式传入 (走 calendar gate).
    禁止 fallback to wall-clock now (会拉盘中半成品月 K).
    """
    import akshare as ak

    if not end_date:
        raise ValueError(
            "fetch_stock_kline_monthly: end_date is required and must be calendar-gated "
            "(use services.utils.latest_completed_trade_date upstream)"
        )

    try:
        payload = await _safe_akshare_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="monthly",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            timeout=30,
        )
        rows = _normalize_price_rows(payload, "eastmoney")
        if rows:
            from services.api_schemas import KLineDailyRow
            from pydantic import TypeAdapter, ValidationError
            try:
                tail_rows = rows[-limit:]
                TypeAdapter(list[KLineDailyRow]).validate_python(tail_rows)
                return tail_rows, "eastmoney"
            except ValidationError as e:
                logger.error(f"[月K] eastmoney 防腐层截断 - Schema校验失败: {e}")
                # Fall back implicitly
    except Exception as e:
        logger.debug(f"[月K] {code} eastmoney 失败: {e}")

    try:
        daily_rows, source = await _fetch_daily_with_fallback(code, start_date, end_date)
        monthly = _aggregate_monthly_from_daily(daily_rows)
        if monthly:
            return monthly[-limit:], f"{source}_derived_monthly"
    except Exception as e:
        logger.warning(f"[月K] {code}: {e}")
    return None, ""


async def fetch_stock_kline_daily(code: str, days: int = 150,
                                  start_date: Optional[str] = None,
                                  end_date: Optional[str] = None,
                                  prefer_fallback: bool = False):
    """获取日K线。缺失股票拉全历史，失败时自动回退新浪 / 腾讯。

    Phase ψ.5 根因 1 残留修复: end_date 必须由调用方显式传入 (走 calendar gate).
    禁止 fallback to wall-clock now (会拉盘中 tick).
    """
    if not end_date:
        raise ValueError(
            "fetch_stock_kline_daily: end_date is required and must be calendar-gated "
            "(use services.utils.latest_completed_trade_date upstream)"
        )
    start = start_date or (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    try:
        rows, source = await _fetch_daily_with_fallback(
            code,
            start,
            end_date,
            prefer_fallback=prefer_fallback,
        )
        if rows:
            return rows, source
    except Exception as e:
        logger.warning(f"[日K] {code}: {e}")
    return None, ""


async def test_kline_availability(sample_code: str = "000001") -> dict:
    """测试 K 线源可用性，并区分 tdxhub 失效与 fallback 可用。"""
    end_date = datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: 健康探测用 wall-clock 合理
    start_date = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    started_at = time.time()
    result = {
        "available": False,
        "effective_source": None,
        "detail": "",
        "sample_code": sample_code,
    }

    tdxhub_task = asyncio.create_task(_fetch_daily_tdxhub_with_diagnostics(sample_code, start_date, end_date))
    fallback_task = asyncio.create_task(_fetch_daily_akshare_fallbacks(
        sample_code,
        start_date,
        end_date,
        safe_call=_safe_akshare_call,
    ))
    (rows_m, src_m, tdxhub_diag), (rows_fb, src_fb, fallback_diag) = await asyncio.gather(tdxhub_task, fallback_task)
    result["tdxhub"] = tdxhub_diag
    if rows_m and src_m:
        result["available"] = True
        result["effective_source"] = src_m
        result["detail"] = src_m
        result["elapsed_sec"] = round(time.time() - started_at, 3)
        return result

    result["fallback"] = fallback_diag
    if rows_fb and src_fb:
        result["available"] = True
        result["effective_source"] = src_fb
        result["detail"] = f"{src_fb} fallback · {_summarize_tdxhub_attempts(tdxhub_diag.get('attempts') or [])}"
    else:
        result["detail"] = _summarize_tdxhub_attempts(tdxhub_diag.get("attempts") or [])
        if fallback_diag.get("last_error"):
            result["detail"] += f" · fallback {fallback_diag['last_error']}"
    result["elapsed_sec"] = round(time.time() - started_at, 3)
    return result


async def _fetch_etf_list_tdxhub() -> list[dict]:
    if _tdxhub_circuit_open():
        state = _get_tdxhub_unavailable_state()
        logger.warning(f"[ETF] 跳过 tdxhub ETF 列表探测：{state.get('summary') or 'tdxhub circuit open'}")
        return []

    def _fetch_on_client(client):
        results = []
        for market in [0, 1]:
            stocks = _records_from_payload(client.stocks_records(market=market))
            if not stocks:
                continue
            for row in stocks:
                code = str(row.get("code", "")).strip()
                name = str(row.get("name", "")).strip()
                if market == 0 and code.startswith("15"):
                    results.append({"code": code, "name": name, "market": "sz", "asset_type": "etf"})
                elif market == 1 and (code.startswith("51") or code.startswith("56") or code.startswith("58")):
                    results.append({"code": code, "name": name, "market": "sh", "asset_type": "etf"})
        if not results:
            raise ValueError("empty")
        return results

    try:
        results, source = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_tdx_quotes_with_retry(
                _fetch_on_client,
                action_name="stocks[etf-list]",
            ),
        )
        _clear_tdxhub_unavailable()
        logger.info(f"[ETF] ETF 列表来自 {source}: {len(results)} 只")
        return results
    except ImportError:
        return []
    except Exception as e:
        logger.warning(f"[ETF] tdxhub ETF 列表失败: {e}")
        _mark_tdxhub_unavailable(f"tdxhub stocks failed: {e}", [])

    return []


async def _fetch_etf_list_ths() -> list[dict]:
    import akshare as ak

    try:
        payload = await _safe_akshare_call(ak.fund_etf_spot_ths, timeout=25, retries=0)
        results = _normalize_etf_spot_rows(payload)
        if results:
            logger.warning(f"[ETF] tdxhub ETF 列表不可用，已回退同花顺 ETF 列表源: {len(results)} 只")
        return results
    except Exception as e:
        logger.warning(f"[ETF] 同花顺 ETF 列表回退失败: {e}")
        return []


def _coerce_etf_list_result(payload, default_source: str) -> tuple[list[dict], str]:
    if isinstance(payload, tuple) and len(payload) == 2:
        rows, source = payload
        return list(rows or []), str(source or "")
    rows = list(payload or [])
    return rows, default_source if rows else ""


# ============================================================
# ETF / 指数 K 线
# ============================================================

async def fetch_etf_list_with_source() -> tuple[list[dict], str]:
    """获取 ETF 列表，并显式返回本次有效数据源。"""
    rows, source = _coerce_etf_list_result(await _fetch_etf_list_tdxhub(), "tdxhub")
    if rows:
        return rows, source

    rows, source = _coerce_etf_list_result(await _fetch_etf_list_ths(), "ths")
    return rows, source

async def fetch_etf_list() -> list[dict]:
    """获取 ETF 列表，优先 tdxhub，失败后回退同花顺 ETF 列表源。"""
    results, _source = await fetch_etf_list_with_source()
    return results


async def fetch_etf_kline(code: str, start_date: str, end_date: str):
    """获取 ETF K 线，优先 tdxhub，失败后回退股票 K 线降级链。"""
    rows, source, tdxhub_diag = await _fetch_daily_tdxhub_with_diagnostics(code, start_date, end_date)
    if rows:
        return rows, source

    rows_fb, source_fb, diagnostics = await _fetch_daily_akshare_fallbacks(
        code,
        start_date,
        end_date,
        safe_call=_safe_akshare_call,
    )
    if rows_fb:
        logger.debug(
            f"[ETF] {code} tdxhub 不可用，回退 {source_fb}（{tdxhub_diag.get('summary') or _summarize_tdxhub_attempts(tdxhub_diag.get('attempts') or [])}）"
        )
        return rows_fb, source_fb

    if diagnostics.get("last_error"):
        logger.warning(f"[ETF] {code} ETF K 线回退失败: {diagnostics['last_error']}")
    return None, None


async def fetch_index_kline(code: str, start_date: str, end_date: str):
    """获取指数 K 线（通过 tdxhub index_bars）"""
    try:
        from datetime import datetime

        try:
            start_dt = datetime.strptime(start_date[:8], "%Y%m%d")
            end_dt = datetime.strptime(end_date[:8], "%Y%m%d")
            days_needed = max((end_dt - start_dt).days + 30, 150)
        except Exception:
            days_needed = 800

        # 判断市场: 上证指数 sh, 深证指数 sz
        if code.startswith("0") or code.startswith("3"):
            mkt = 1  # 沪市指数
        else:
            mkt = 0  # 深市指数

        payload, _source = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_tdx_quotes_with_retry(
                lambda client: client.index_bars_records(
                    frequency=9,
                    market=mkt,
                    symbol=code,
                    offset=min(days_needed, 800),
                ),
                action_name=f"index_bars[{code}]",
            ),
        )
        if _payload_is_empty(payload):
            return None, None

        start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        rows = _tdx_payload_to_kline_rows(payload, start_fmt, end_fmt)
        if not rows:
            return None, None
        return rows, "tdxhub_index"
    except Exception as e:
        logger.debug(f"[指数] {code} 失败: {e}")
        return None, None


async def fetch_trading_calendar():
    """获取交易日历"""
    import akshare as ak
    import datetime

    payload = await _safe_akshare_call(ak.tool_trade_date_hist_sina, timeout=15)
    if _payload_is_empty(payload):
        return []

    cutoff = datetime.date(2023, 1, 1)
    results = []
    for row in _records_from_payload(payload):
        value = row.get("trade_date")
        normalized = _normalize_fetch_date(value)
        if not normalized:
            continue
        try:
            if datetime.date.fromisoformat(normalized) < cutoff:
                continue
        except ValueError:
            continue
        results.append(normalized)
    return results
