"""
AKShare 数据获取客户端

函数：月K线、日K线、交易日历、行业分类。

说明：
- K 线优先走东财；失败后自动回退新浪 / 腾讯
- 缺失股票拉全历史，已存在股票走增量续拉
- 行业分类仅接受 TDX 研究行业，异常时返回阻断原因，不做跨源回退
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from services.kline_source import aggregate_monthly_from_daily as _aggregate_monthly_from_daily
from services.kline_source import fetch_daily_akshare_fallbacks as _fetch_daily_akshare_fallbacks
from services.kline_source import normalize_price_frame as _normalize_price_frame
from services.tdx_source import call_tdx_quotes_with_retry
from services.tdx_source import clear_mootdx_unavailable as _clear_shared_mootdx_unavailable
from services.tdx_source import get_mootdx_unavailable_state as _get_shared_mootdx_unavailable_state
from services.tdx_source import mark_mootdx_unavailable as _mark_shared_mootdx_unavailable
from services.tdx_source import mootdx_circuit_open as _shared_mootdx_circuit_open

logger = logging.getLogger("cm-api")
_MOOTDX_DEGRADED_TIMEOUT_THRESHOLD = 2
_MOOTDX_DEGRADED_COOLDOWN_SECONDS = 180
_TDX_RESEARCH_LEVELS: tuple[tuple[str, str], ...] = (
    ("16", "sw_level1"),
    ("17", "sw_level2"),
    ("18", "sw_level3"),
)
_TDX_RESEARCH_INIT_GUARD = threading.Lock()
_TDX_RESEARCH_INITIALIZED = False

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


def _summarize_mootdx_attempts(attempts: list[dict]) -> str:
    failed = [item for item in attempts if not item.get("ok")]
    if not failed:
        return "mootdx 正常"
    error_types = []
    for item in failed:
        error_type = item.get("error_type") or "error"
        if error_type not in error_types:
            error_types.append(error_type)
    return f"mootdx {'/'.join(error_types)} ({len(failed)}服)"


def _normalize_fetch_date(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        digits = digits[:8]
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(text) >= 10 and "-" in text:
        return text[:10]
    return None


def _frame_latest_date(df: Optional[pd.DataFrame]) -> Optional[str]:
    if df is None or df.empty or "date" not in df.columns:
        return None
    normalized = [_normalize_fetch_date(value) for value in df["date"].tolist()]
    normalized = [value for value in normalized if value]
    return max(normalized) if normalized else None


def _is_frame_fresh_enough(df: Optional[pd.DataFrame], expected_end_date: Optional[str]) -> bool:
    expected = _normalize_fetch_date(expected_end_date)
    latest = _frame_latest_date(df)
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


def _normalize_etf_spot_frame(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    frame = df.copy()
    frame.columns = [str(col).replace(" ", "") for col in frame.columns]
    results = []
    for _, row in frame.iterrows():
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


def _get_mootdx_unavailable_state() -> dict[str, object]:
    return _get_shared_mootdx_unavailable_state()


def _mark_mootdx_unavailable(summary: str, attempts: list[dict], *, cooldown_seconds: Optional[int] = None) -> None:
    if cooldown_seconds is None:
        _mark_shared_mootdx_unavailable(summary, attempts)
        return
    _mark_shared_mootdx_unavailable(summary, attempts, cooldown_seconds=cooldown_seconds)


def _clear_mootdx_unavailable() -> None:
    _clear_shared_mootdx_unavailable()


def _mootdx_circuit_open() -> bool:
    return _shared_mootdx_circuit_open()


def _count_mootdx_timeout_failures(attempts: list[dict]) -> int:
    total = 0
    for item in attempts or []:
        if item.get("ok"):
            continue
        error_text = str(item.get("error_type") or item.get("error") or "").lower()
        if "timeout" in error_text:
            total += 1
    return total


async def _fetch_daily_mootdx_with_diagnostics(code: str, start_date: str, end_date: str):
    """用 mootdx 从通达信服务器获取日K线，并返回逐服务器诊断。"""
    if _mootdx_circuit_open():
        state = _get_mootdx_unavailable_state()
        cached_attempts = list(state.get("attempts") or [])
        return None, None, {
            "ok": False,
            "cached": True,
            "attempts": cached_attempts,
            "summary": state.get("summary") or "mootdx circuit open",
            "timeout_failures": _count_mootdx_timeout_failures(cached_attempts),
            "fallback_recommended": True,
        }

    diagnostics = {
        "ok": False,
        "attempts": [],
        "summary": "mootdx 未执行",
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
        df = client.bars(symbol=code, frequency=9, offset=min(days_needed, 800))
        if df is None or df.empty:
            raise ValueError("empty")

        df = df.rename(columns={"vol": "volume"})
        df = df.loc[:, ~df.columns.duplicated()]
        df["date"] = df.index.strftime("%Y-%m-%d") if hasattr(df.index, "strftime") else df["datetime"].astype(str).str[:10]
        df = df[(df["date"] >= start_fmt) & (df["date"] <= end_fmt)]
        if df.empty:
            raise ValueError("empty_after_filter")

        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col not in df.columns:
                df[col] = None
        return df[["date", "open", "high", "low", "close", "volume", "amount"]]

    try:
        df, _source, attempts = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_tdx_quotes_with_retry(
                _fetch_on_client,
                action_name=f"bars[{code}]",
                collect_attempts=True,
            ),
        )
        diagnostics["attempts"] = attempts
        diagnostics["ok"] = True
        diagnostics["server"] = attempts[-1]["server"] if attempts else None
        diagnostics["elapsed_sec"] = round(sum(float(item.get("elapsed_sec") or 0.0) for item in attempts), 3)
        diagnostics["timeout_failures"] = _count_mootdx_timeout_failures(attempts)
        diagnostics["fallback_recommended"] = diagnostics["timeout_failures"] >= _MOOTDX_DEGRADED_TIMEOUT_THRESHOLD
        diagnostics["summary"] = f"mootdx {diagnostics['server']}"
        if diagnostics["fallback_recommended"]:
            _TDX_RESEARCH_LEVELS: tuple[tuple[str, str], ...] = (
                ("16", "sw_level1"),
                ("17", "sw_level2"),
                ("18", "sw_level3"),
            )
            _TDX_RESEARCH_INIT_GUARD = threading.Lock()
            _TDX_RESEARCH_INITIALIZED = False
            diagnostics["summary"] = (
                f"mootdx {diagnostics['server']} · timeout x{diagnostics['timeout_failures']}"
            )
            _mark_mootdx_unavailable(
                f"mootdx timeout x{diagnostics['timeout_failures']}，切换 fallback",
                attempts,
                cooldown_seconds=_MOOTDX_DEGRADED_COOLDOWN_SECONDS,
            )
        else:
            _clear_mootdx_unavailable()
        return df, "mootdx", diagnostics
    except ImportError:
        diagnostics["summary"] = "mootdx 未安装"
        return None, None, diagnostics
    except Exception as e:
        diagnostics["attempts"] = list(getattr(e, "tdx_attempts", []) or [])
        diagnostics["timeout_failures"] = _count_mootdx_timeout_failures(diagnostics["attempts"])
        diagnostics["fallback_recommended"] = True
        diagnostics["summary"] = _summarize_mootdx_attempts(diagnostics["attempts"]) if diagnostics["attempts"] else str(e)
        _mark_mootdx_unavailable(diagnostics["summary"], diagnostics["attempts"])
        logger.debug(f"[mootdx] {code} 失败: {e}")
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
            if result is None or (isinstance(result, pd.DataFrame) and result.empty):
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
async def _fetch_daily_mootdx(code: str, start_date: str, end_date: str):
    """用 mootdx 从通达信服务器获取日K线（首选数据源）"""
    df, source, _ = await _fetch_daily_mootdx_with_diagnostics(code, start_date, end_date)
    return df, source


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
    import akshare as ak

    best_df = None
    best_source = ""
    best_latest = None
    fallback_diagnostics = None

    def _remember_result(df, source):
        nonlocal best_df, best_source, best_latest
        latest = _frame_latest_date(df)
        if df is None or df.empty or not latest:
            return latest
        if best_df is None or best_latest is None or latest > best_latest:
            best_df = df
            best_source = source
            best_latest = latest
        return latest

    if prefer_fallback:
        df_fb, src_fb, fallback_diagnostics = await _fetch_daily_akshare_fallbacks(
            code,
            start_date,
            end_date,
            safe_call=_safe_akshare_call,
        )
        if df_fb is not None and not df_fb.empty:
            _remember_result(df_fb, src_fb)
        if _is_frame_fresh_enough(df_fb, end_date):
            return df_fb, src_fb

    # 优先级1: mootdx（通达信服务器，Mac原生）
    df_m, src_m, diagnostics_m = await _fetch_daily_mootdx_with_diagnostics(code, start_date, end_date)
    if df_m is not None and not df_m.empty:
        _remember_result(df_m, src_m)
        if diagnostics_m.get("fallback_recommended"):
            logger.warning(
                f"[日K] {code} mootdx 连续超时，后续短时回退 fallback（{diagnostics_m.get('summary') or 'mootdx degraded'}）"
            )
        if _is_frame_fresh_enough(df_m, end_date):
            return df_m, src_m

    if fallback_diagnostics is None:
        df_fb, src_fb, fallback_diagnostics = await _fetch_daily_akshare_fallbacks(
            code,
            start_date,
            end_date,
            safe_call=_safe_akshare_call,
        )
        if df_fb is not None and not df_fb.empty:
            _remember_result(df_fb, src_fb)
        if _is_frame_fresh_enough(df_fb, end_date):
            return df_fb, src_fb

    if best_df is not None and not best_df.empty:
        return best_df, best_source

    _raise_daily_fallback_error(fallback_diagnostics or {})
    return None, ""


async def probe_stock_kline_fallback_preference(code: str, start_date: str, end_date: str) -> dict:
    _, _, diagnostics = await _fetch_daily_mootdx_with_diagnostics(code, start_date, end_date)
    prefer_fallback = (
        not diagnostics.get("ok")
        or bool(diagnostics.get("cached"))
        or bool(diagnostics.get("fallback_recommended"))
    )
    return {
        "sample_code": code,
        "prefer_fallback": prefer_fallback,
        "reason": diagnostics.get("summary") or ("mootdx unavailable" if prefer_fallback else "mootdx healthy"),
        "elapsed_sec": float(diagnostics.get("elapsed_sec") or 0.0),
        "timeout_failures": int(diagnostics.get("timeout_failures") or 0),
    }


async def fetch_stock_kline_monthly(code: str, limit: int = 36,
                                    start_date: str = "20230101",
                                    end_date: Optional[str] = None):
    """获取月K线。东财失败时回退到日K聚合月K。"""
    import akshare as ak

    end_date = end_date or datetime.now().strftime("%Y%m%d")

    try:
        df = await _safe_akshare_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="monthly",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            timeout=30,
        )
        norm = _normalize_price_frame(df, "eastmoney")
        if norm is not None and not norm.empty:
            from services.api_schemas import KLineDailyRow
            from pydantic import TypeAdapter, ValidationError
            try:
                tail_df = norm.tail(limit)
                TypeAdapter(list[KLineDailyRow]).validate_python(tail_df.to_dict('records'))
                return tail_df, "eastmoney"
            except ValidationError as e:
                logger.error(f"[月K] eastmoney 防腐层截断 - Schema校验失败: {e}")
                # Fall back implicitly
    except Exception as e:
        logger.debug(f"[月K] {code} eastmoney 失败: {e}")

    try:
        daily_df, source = await _fetch_daily_with_fallback(code, start_date, end_date)
        monthly = _aggregate_monthly_from_daily(daily_df)
        if monthly is not None and not monthly.empty:
            return monthly.tail(limit), f"{source}_derived_monthly"
    except Exception as e:
        logger.warning(f"[月K] {code}: {e}")
    return None, ""


async def fetch_stock_kline_daily(code: str, days: int = 150,
                                  start_date: Optional[str] = None,
                                  end_date: Optional[str] = None,
                                  prefer_fallback: bool = False):
    """获取日K线。缺失股票拉全历史，失败时自动回退新浪 / 腾讯。"""
    end_date = end_date or datetime.now().strftime("%Y%m%d")
    start = start_date or (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    try:
        df, source = await _fetch_daily_with_fallback(
            code,
            start,
            end_date,
            prefer_fallback=prefer_fallback,
        )
        if df is not None and not df.empty:
            return df, source
    except Exception as e:
        logger.warning(f"[日K] {code}: {e}")
    return None, ""


async def test_kline_availability(sample_code: str = "000001") -> dict:
    """测试 K 线源可用性，并区分 mootdx 失效与 fallback 可用。"""
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    started_at = time.time()
    result = {
        "available": False,
        "effective_source": None,
        "detail": "",
        "sample_code": sample_code,
    }

    mootdx_task = asyncio.create_task(_fetch_daily_mootdx_with_diagnostics(sample_code, start_date, end_date))
    fallback_task = asyncio.create_task(_fetch_daily_akshare_fallbacks(
        sample_code,
        start_date,
        end_date,
        safe_call=_safe_akshare_call,
    ))
    (df_m, src_m, mootdx_diag), (df_fb, src_fb, fallback_diag) = await asyncio.gather(mootdx_task, fallback_task)
    result["mootdx"] = mootdx_diag
    if df_m is not None and not df_m.empty and src_m:
        result["available"] = True
        result["effective_source"] = src_m
        result["detail"] = src_m
        result["elapsed_sec"] = round(time.time() - started_at, 3)
        return result

    result["fallback"] = fallback_diag
    if df_fb is not None and not df_fb.empty and src_fb:
        result["available"] = True
        result["effective_source"] = src_fb
        result["detail"] = f"{src_fb} fallback · {_summarize_mootdx_attempts(mootdx_diag.get('attempts') or [])}"
    else:
        result["detail"] = _summarize_mootdx_attempts(mootdx_diag.get("attempts") or [])
        if fallback_diag.get("last_error"):
            result["detail"] += f" · fallback {fallback_diag['last_error']}"
    result["elapsed_sec"] = round(time.time() - started_at, 3)
    return result


def _normalize_tdx_sector_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        head, tail = text.split(".", 1)
        digits = "".join(ch for ch in head if ch.isdigit())
        suffix = "".join(ch for ch in tail if ch.isalpha())
        if digits and suffix:
            return f"{digits}.{suffix}"
        return digits
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits


def _normalize_tdx_stock_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def _coerce_tdx_code_name_rows(payload) -> list[dict[str, str]]:
    if payload is None:
        return []
    rows = []
    for item in list(payload):
        code = ""
        name = ""
        if isinstance(item, dict):
            code = item.get("Code") or item.get("code") or item.get("证券代码") or item.get("代码") or ""
            name = item.get("Name") or item.get("name") or item.get("证券名称") or item.get("名称") or ""
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            code, name = item[0], item[1]
        sector_code = _normalize_tdx_sector_code(code)
        sector_name = str(name or "").strip()
        if not sector_code or not sector_name:
            continue
        rows.append({"code": sector_code, "name": sector_name})
    return rows


def _coerce_tdx_code_list(payload) -> list[str]:
    if payload is None:
        return []
    codes = []
    for item in list(payload):
        code = item
        if isinstance(item, dict):
            code = item.get("Code") or item.get("code") or item.get("证券代码") or item.get("代码") or ""
        elif isinstance(item, (list, tuple)) and item:
            code = item[0]
        stock_code = _normalize_tdx_stock_code(code)
        if stock_code:
            codes.append(stock_code)
    return codes


def _get_tdx_research_client():
    try:
        from tqcenter import tq
    except ImportError as exc:
        raise ImportError("tqcenter not installed") from exc

    global _TDX_RESEARCH_INITIALIZED
    with _TDX_RESEARCH_INIT_GUARD:
        if not _TDX_RESEARCH_INITIALIZED:
            initialize = getattr(tq, "initialize", None)
            if callable(initialize):
                initialize(__file__)
            _TDX_RESEARCH_INITIALIZED = True
    return tq


async def _call_tdx_research_api(method_name: str, *args, **kwargs):
    loop = asyncio.get_running_loop()

    def _invoke():
        client = _get_tdx_research_client()
        method = getattr(client, method_name, None)
        if not callable(method):
            raise AttributeError(f"tqcenter missing {method_name}")
        return method(*args, **kwargs)

    return await loop.run_in_executor(None, _invoke)


async def _fetch_tdx_research_sector_rows(level_code: str) -> list[dict[str, str]]:
    rows = _coerce_tdx_code_name_rows(
        await _call_tdx_research_api("get_stock_list", level_code, list_type=1)
    )
    if not rows:
        raise ValueError(f"tdx research level {level_code} empty")
    return rows


async def _fetch_tdx_research_sector_members(sector_code: str) -> list[str]:
    return list(
        dict.fromkeys(
            _coerce_tdx_code_list(
                await _call_tdx_research_api(
                    "get_stock_list_in_sector",
                    sector_code,
                    block_type=0,
                    list_type=0,
                )
            )
        )
    )


async def _collect_tdx_level_assignments(level_code: str, field_name: str) -> tuple[dict[str, str], dict[str, str]]:
    sectors = await _fetch_tdx_research_sector_rows(level_code)
    assignments: dict[str, str] = {}
    sector_codes: dict[str, str] = {}
    duplicate_examples: list[str] = []
    duplicate_count = 0

    logger.info(f"[行业][TDX] 拉取 {field_name}: {len(sectors)} 个板块")
    for index, sector in enumerate(sectors, start=1):
        members = await _fetch_tdx_research_sector_members(sector["code"])
        for stock_code in members:
            current_code = sector_codes.get(stock_code)
            if current_code and current_code != sector["code"]:
                duplicate_count += 1
                if len(duplicate_examples) < 10:
                    duplicate_examples.append(
                        f"{stock_code}:{current_code}->{sector['code']}"
                    )
                continue
            assignments.setdefault(stock_code, sector["name"])
            sector_codes.setdefault(stock_code, sector["code"])
        if index % 20 == 0 or index == len(sectors):
            logger.info(
                f"[行业][TDX] {field_name} 进度: {index}/{len(sectors)}, 已覆盖 {len(assignments)} 只股票"
            )

    if duplicate_count:
        sample = ", ".join(duplicate_examples)
        raise ValueError(
            f"tdx_research_industry_duplicate:{field_name}:{duplicate_count}:{sample}"
        )
    return assignments, sector_codes


async def _test_tdx_industry_availability() -> tuple[bool, str]:
    try:
        level1_rows, level3_rows = await asyncio.gather(
            _fetch_tdx_research_sector_rows("16"),
            _fetch_tdx_research_sector_rows("18"),
        )
        if level1_rows and level3_rows:
            return True, "tdx_research_industry"
        return False, "tdx_research_industry_empty"
    except Exception as exc:
        return False, f"tdx_research_industry_error:{str(exc)[:160]}"


async def _fetch_tdx_research_industry_all() -> list[dict]:
    level_values: dict[str, dict[str, str]] = {}
    level_codes: dict[str, dict[str, str]] = {}

    for level_code, field_name in _TDX_RESEARCH_LEVELS:
        assignments, sector_codes = await _collect_tdx_level_assignments(level_code, field_name)
        if not assignments:
            raise ValueError(f"tdx_research_industry_empty:{field_name}")
        level_values[field_name] = assignments
        level_codes[field_name] = sector_codes

    all_codes = sorted(set().union(*(set(mapping.keys()) for mapping in level_values.values())))
    results = []
    for stock_code in all_codes:
        results.append({
            "stock_code": stock_code,
            "sw_level1": level_values["sw_level1"].get(stock_code, ""),
            "sw_level2": level_values["sw_level2"].get(stock_code, ""),
            "sw_level3": level_values["sw_level3"].get(stock_code, ""),
            "sw_code": (
                level_codes["sw_level3"].get(stock_code)
                or level_codes["sw_level2"].get(stock_code)
                or level_codes["sw_level1"].get(stock_code)
                or ""
            ),
        })

    logger.info(f"[行业][TDX] 完成: {len(results)} 只股票的研究行业映射")
    return results


async def fetch_sw_industry_all_with_source() -> tuple[list[dict], str]:
    try:
        rows = await _fetch_tdx_research_industry_all()
        if rows:
            return rows, "tdx_research_industry"
        return [], "tdx_research_industry_empty"
    except Exception as exc:
        reason = f"tdx_research_industry_error:{str(exc)[:160]}"
        logger.error(f"[行业][TDX] 研究行业不可用: {reason}")
        return [], reason


async def test_industry_availability() -> tuple[bool, str]:
    """测试行业源可用性；只接受通达信研究行业。"""
    return await _test_tdx_industry_availability()


async def fetch_sw_industry_all():
    rows, _source = await fetch_sw_industry_all_with_source()
    return rows


async def _fetch_etf_list_mootdx() -> list[dict]:
    if _mootdx_circuit_open():
        state = _get_mootdx_unavailable_state()
        logger.warning(f"[ETF] 跳过 mootdx ETF 列表探测：{state.get('summary') or 'mootdx circuit open'}")
        return []

    def _fetch_on_client(client):
        results = []
        for market in [0, 1]:
            stocks = client.stocks(market=market)
            if stocks is None or stocks.empty:
                continue
            for _, row in stocks.iterrows():
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
        _clear_mootdx_unavailable()
        logger.info(f"[ETF] ETF 列表来自 {source}: {len(results)} 只")
        return results
    except ImportError:
        return []
    except Exception as e:
        logger.warning(f"[ETF] mootdx ETF 列表失败: {e}")
        _mark_mootdx_unavailable(f"mootdx stocks failed: {e}", [])

    return []


async def _fetch_etf_list_ths() -> list[dict]:
    import akshare as ak

    try:
        df = await _safe_akshare_call(ak.fund_etf_spot_ths, timeout=25, retries=0)
        results = _normalize_etf_spot_frame(df)
        if results:
            logger.warning(f"[ETF] mootdx ETF 列表不可用，已回退同花顺 ETF 列表源: {len(results)} 只")
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
    rows, source = _coerce_etf_list_result(await _fetch_etf_list_mootdx(), "mootdx")
    if rows:
        return rows, source

    rows, source = _coerce_etf_list_result(await _fetch_etf_list_ths(), "ths")
    return rows, source

async def fetch_etf_list() -> list[dict]:
    """获取 ETF 列表，优先 mootdx，失败后回退同花顺 ETF 列表源。"""
    results, _source = await fetch_etf_list_with_source()
    return results


async def fetch_etf_kline(code: str, start_date: str, end_date: str):
    """获取 ETF K 线，优先 mootdx，失败后回退股票 K 线降级链。"""
    df, source, mootdx_diag = await _fetch_daily_mootdx_with_diagnostics(code, start_date, end_date)
    if df is not None and not df.empty:
        return df, source

    df_fb, source_fb, diagnostics = await _fetch_daily_akshare_fallbacks(
        code,
        start_date,
        end_date,
        safe_call=_safe_akshare_call,
    )
    if df_fb is not None and not df_fb.empty:
        logger.debug(
            f"[ETF] {code} mootdx 不可用，回退 {source_fb}（{mootdx_diag.get('summary') or _summarize_mootdx_attempts(mootdx_diag.get('attempts') or [])}）"
        )
        return df_fb, source_fb

    if diagnostics.get("last_error"):
        logger.warning(f"[ETF] {code} ETF K 线回退失败: {diagnostics['last_error']}")
    return None, None


async def fetch_index_kline(code: str, start_date: str, end_date: str):
    """获取指数 K 线（通过 mootdx index_bars）"""
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

        df, _source = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_tdx_quotes_with_retry(
                lambda client: client.index_bars(
                    frequency=9,
                    market=mkt,
                    symbol=code,
                    offset=min(days_needed, 800),
                ),
                action_name=f"index_bars[{code}]",
            ),
        )
        if df is None or df.empty:
            return None, None

        df = df.rename(columns={"vol": "volume"})
        start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        df["date"] = df.index.strftime("%Y-%m-%d") if hasattr(df.index, 'strftime') else df["datetime"].astype(str).str[:10]
        df = df[(df["date"] >= start_fmt) & (df["date"] <= end_fmt)]
        if df.empty:
            return None, None
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col not in df.columns:
                df[col] = None
        return df[["date", "open", "high", "low", "close", "volume", "amount"]], "mootdx_index"
    except Exception as e:
        logger.debug(f"[指数] {code} 失败: {e}")
        return None, None


async def fetch_trading_calendar():
    """获取交易日历"""
    import akshare as ak
    import datetime

    df = await _safe_akshare_call(ak.tool_trade_date_hist_sina, timeout=15)
    if df is None:
        return []

    cutoff = datetime.date(2023, 1, 1)
    df = df[df['trade_date'] >= cutoff]
    return [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]
            for d in df['trade_date']]
