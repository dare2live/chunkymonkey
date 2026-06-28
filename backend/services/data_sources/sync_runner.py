"""sync_runner — sync_registry.yaml 驱动的通用数据域同步器 (架构稿 §3.3).

一个 registry 条目 = 一个数据域, 零域专属代码。职责:
  1. 按 batch_mode 切批 (交易日历驱动, 不 hardcode 日期)
  2. 调 source adapter fetch_raw (api 字段镜像, 不加工)
  3. 写 raw 表 (target_db 库, MERGE on grain, 加 built_at) — 幂等重跑
  4. watermark (mart_data_source_watermark) + 失败入队 (failure_queue) — 复用既有服务
  5. 0 行 = 失败重试 (宪法 v2 第 6 条; allow_empty_batch 条目除外)

写锁纪律: raw 表写 tushare_raw.duckdb (manifest 注册), 与 smartmoney 主库锁解耦;
watermark/failure_queue 在 smartmoney (既有表), 写入窗口短。

用法:
    PYTHONPATH=backend python -m services.data_sources.sync_runner \
        --domain moneyflow --backfill              # 从 data_start 回填到最新
    ... --domain moneyflow                          # 增量 (watermark 之后)
    ... --all-due                                   # 全部到期域 (daily_update 集成)
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("sync_runner")

_REPO = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO / "backend" / "config" / "sync_registry.yaml"
SOURCE_TIER_TUSHARE = 2  # evidence: tushare = tier-2 源 (source_watermarks DOMAIN_SPECS sync:* 域全 tier 2; SLA_DAYS tier2=2d)


def load_registry(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or _REGISTRY_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "domains" not in raw:
        raise ValueError("sync_registry.yaml: 缺 domains")
    return raw


def _domain_spec(registry: dict[str, Any], domain: str) -> dict[str, Any]:
    spec = dict(registry["defaults"] or {})
    entry = registry["domains"].get(domain)
    if entry is None:
        raise KeyError(f"sync_registry: 未注册的数据域 '{domain}' — 新域必须先加 registry 条目 (宪法 v2 第 7/9 条)")
    spec.update(entry)
    spec["domain"] = domain
    return spec


def _adapter(source_name: str):
    from services.data_sources import get_registry  # 延迟 import 防环

    src = get_registry().get_source(source_name)
    if src is None:
        raise KeyError(f"data_sources registry: 未注册 source '{source_name}'")
    return src


def _target_conn(spec: dict[str, Any]):
    """raw 库写连接 (manifest 解析路径); 表不存在由首批数据建."""
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    db_alias = spec.get("target_db", "tushare_raw")
    path = get_database_manifest().path_for(db_alias)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return connect(str(path), read_only=False)


def _smartmoney_conn():
    from services.db import get_conn

    return get_conn()


def _warn_if_clamped(domain: str, start_d: str, days: list[str]) -> None:
    """回填/增量起点早于交易日历首日 = 更早段被静默 clamp — 必须显式可见.

    反例 (CLAUDE.md §4.5, 2026-06-12): registry data_start=20050104 被日历起点
    2023-01-03 clamp, top_list 等 2005-2022 全军未落零告警; 验收必须按落库
    min(trade_date) 对账 data_start, 不是"跑完没报错"。
    """
    first = days[0] if days else None
    if first is not None and start_d < first:
        log.warning(
            "domain=%s 起点 %s 早于交易日历首日 %s — 更早段被日历 clamp 不会拉取; "
            "落库验收按 min(trade_date) 对账 data_start",
            domain, start_d, first,
        )
    elif not days:
        log.warning("domain=%s 请求窗口起点 %s 在交易日历内零交易日", domain, start_d)


def _existing_ts_codes(spec: dict[str, Any]) -> set[str]:
    """target 表已有数据的 ts_code 集 (断点续拉跳过用)。planning 期 tushare_raw 未被本 run 写锁, read_only 查。"""
    import duckdb

    from services.database_manifest import get_database_manifest
    table = spec["target_table"]
    try:
        path = get_database_manifest().path_for(spec.get("target_db", "tushare_raw"))
        con = duckdb.connect(str(path), read_only=True)  # rule-compliance: ok evidence=只读查已拉ts_code供断点续拉, 非业务阈值/非主库写
    except Exception as exc:
        log.warning("[resume] 无法读 %s 查已拉 ts_code (回退全量拉): %s", table, str(exc)[:80])
        return set()
    try:
        if not con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone():
            return set()
        rows = con.execute(f"SELECT DISTINCT ts_code FROM {table} WHERE ts_code IS NOT NULL").fetchall()
        return {str(r[0]) for r in rows}
    finally:
        con.close()


def _latest_expected_report_period(today: str) -> str:
    """A股最新**应已披露**的季报期 (YYYYMMDD), 交易日历级真相: 取截止日<=today的最新报告期。
    截止: Q1(0331)→4/30 · 半年(0630)→8/31 · Q3(0930)→10/31 · 年报(1231)→次年4/30 (与Q1同日, Q1期更新)。
    (today=YYYYMMDD; 用于 by_report_period 增量: 存量股 MAX(end_date) < 此期 = 该补新期)。
    """
    y = int(today[:4])
    # (截止日YYYYMMDD, 报告期YYYYMMDD) 按报告期新→旧; 命中首个 today>=截止 即最新应披露期
    for deadline, period in (
        (f"{y}1031", f"{y}0930"),     # 今年Q3
        (f"{y}0831", f"{y}0630"),     # 今年半年
        (f"{y}0430", f"{y}0331"),     # 今年Q1 (4/30截止, 同日去年年报但Q1期更新)
        (f"{y}0430", f"{y-1}1231"),   # 去年年报
        (f"{y-1}1031", f"{y-1}0930"), # 去年Q3 (今年Q1披露前的最新)
    ):
        if today >= deadline:
            return period
    return f"{y-1}0930"  # 兜底 (理论不达, today<去年10/31 = 跨年极早期)


def _stocks_up_to_date(spec: dict[str, Any], target_period: str, period_col: str = "end_date") -> set[str]:
    """target 表里 MAX(period_col) >= target_period 的 ts_code (=已有最新应披露期, 增量跳过)。
    read_only 查 (planning 期 raw 未本run写锁)。表无/列无 → 空集 (全拉)。"""
    import duckdb

    from services.database_manifest import get_database_manifest
    table = spec["target_table"]
    try:
        path = get_database_manifest().path_for(spec.get("target_db", "tushare_raw"))
        con = duckdb.connect(str(path), read_only=True)  # rule-compliance: ok evidence=只读查每股最新报告期供增量跳过, 非业务阈值/非主库写
    except Exception as exc:  # noqa: BLE001
        log.warning("[increment] 无法读 %s 查最新期 (回退全拉): %s", table, str(exc)[:80])
        return set()
    try:
        if not con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]).fetchone():
            return set()
        cols = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if period_col not in cols or "ts_code" not in cols:
            return set()
        rows = con.execute(
            f"SELECT ts_code, MAX({period_col}) FROM {table} WHERE ts_code IS NOT NULL GROUP BY ts_code"
        ).fetchall()
        return {str(r[0]) for r in rows if r[1] is not None and str(r[1]) >= target_period}
    finally:
        con.close()


def _by_ts_code_batches(spec: dict[str, Any], *, resume: bool = False, backfill: bool = False) -> list[dict[str, Any]]:
    """按股循环批清单 (单股接口如 stk_factor_pro/fina_mainbz)。

    股票清单真相源 = services.universe.get_active_universe (单一计算点): 白名单 60/00/30/68
    + 非退市 (K线真相源); 默认非 ST。2026-06-17 用户: 排除列表=交易日历级硬真相源, 数据拉取也走它
    — 不拉排除股 (北交所/ST/三板/退市) 的逐股数据 (原内联 tdxhub 45日活跃+前缀 = 第二套 universe
    定义且漏 ST 排除, 已退役)。
    **include_st (2026-06-23)**: 域可声明 `include_st: true` 把活跃 ST 股纳入拉取 — 用于**参考/展示数据**
    (如 top10_floatholders 十大流通股东, dossier 展示任意股含 ST; 否则 ST 股 holder 缺口致删旧源时丢数据,
    见 analysis/非tushare源_双轨_holders_20260623.md)。策略信号类域保持默认排 ST。
    """
    from services.universe import get_active_universe

    fixed = dict(spec.get("fixed_params") or {})
    # mythos §10 data_start 参数口径: 部分 by_ts_code 接口 (如 stk_holdernumber) 不传 start_date
    # 只返回最近 ~8 期 (实测 600519 无日期 8 行 vs start_date=2019 给 38 行全史) → 回填必须显式传
    # start_date=data_start 才拿全史; 否则回填"成功"但只覆盖近期 (94min 白跑 0 净新增, 2026-06-24 实测踩坑)。
    # 增量 (非 backfill) 不传, 拿最近期即可 (覆盖新季); 对本就返全史的接口 (top10 by_ts_code) 传亦无害 (下界=K线对齐)。
    if backfill and spec.get("data_start") and "start_date" not in fixed:
        fixed["start_date"] = str(spec["data_start"])
    include_st = bool(spec.get("include_st", False))
    conn0 = _smartmoney_conn()
    try:
        codes = get_active_universe(conn0, include_st=include_st)  # 白名单+非退市 (排除列表硬真相源); include_st 按域
    finally:
        conn0.close()

    def _ts(code: str) -> str | None:
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        return None  # 白名单补集 (北交所/三板) 不在 universe

    batch = [{"ts_code": t, **fixed} for c in sorted(codes) if (t := _ts(c))]

    # by_report_period 增量 (2026-06-23, 修谄媚死: 旧 by_ts_code 全量重拉或resume跳整股=存量股新季永不补):
    # 季报类事件域 (十大股东/财报) 用交易日历算"最新应披露报告期", 跳过已有该期的股, 只抓缺新期的股。
    # = 用户"十大股东从上一公告日到获取日扫增量"。backfill 时全拉 (不增量)。
    if spec.get("increment_mode") == "by_report_period" and not backfill:
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: 报告期增量参照日(算最新应披露季报期, 非trade-date end-date)
        target_period = _latest_expected_report_period(today)
        period_col = spec.get("period_col", "end_date")
        up_to_date = _stocks_up_to_date(spec, target_period, period_col)
        n0 = len(batch)
        batch = [b for b in batch if b["ts_code"] not in up_to_date]
        log.info("[increment by_report_period] %s 目标期=%s: 跳过 %d 已最新股, 剩 %d 待抓新期",
                 spec["domain"], target_period, n0 - len(batch), len(batch))
    elif resume:
        done = _existing_ts_codes(spec)
        if done:
            n0 = len(batch)
            batch = [b for b in batch if b["ts_code"] not in done]
            log.info("[resume] %s 跳过 %d 已拉 ts_code, 剩 %d 待拉 (断点续拉)",
                     spec["domain"], n0 - len(batch), len(batch))
    return batch


def _quarter_ends(start: str, end: str) -> list[str]:
    """报告期列表 (季末日 YYYYMMDD): [start, end] 内所有 0331/0630/0930/1231.

    用于 by_period 批 (财报快报 express_vip / 按报告期整批拉的接口 — ann_date/trade_date 不可批)。
    覆盖式快照接口重拉某 period 拿最新修订态, MERGE on grain 幂等。
    """
    sy, ey = int(start[:4]), int(end[:4])
    return [
        p
        for y in range(sy, ey + 1)
        for md in ("0331", "0630", "0930", "1231")
        if start <= (p := f"{y}{md}") <= end
    ]


def _trading_days(start: str, end: str | None = None) -> list[str]:
    """交易日列表 (YYYYMMDD), 真相源 = 项目交易日历 (L0)."""
    # §9 拆库: dim_trading_calendar 迁 reference。不再开 smartmoney conn (latest_completed_trade_date
    #   已内部 reference 路由; 日历查询经 dim_read_conn(None,...) 直开 reference RO)。Stage E 物删 smartmoney
    #   副本后此处不受影响 (本就读 reference)。
    from services.utils import latest_completed_trade_date
    from services.data_access import resolver

    end_d = end or latest_completed_trade_date().replace("-", "")
    cal, cal_own = resolver.dim_read_conn(None, "dim_trading_calendar")  # rule-compliance: ok evidence=calendar truth-source in reference db
    try:
        rows = cal.execute(
            """
            SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS d
            FROM dim_trading_calendar
            WHERE is_trading AND replace(CAST(trade_date AS VARCHAR), '-', '') BETWEEN ? AND ?
            ORDER BY 1
            """,
            [start, end_d],
        ).fetchall()
    finally:
        if cal_own:
            cal.close()
    return [r[0] for r in rows]


class QuotaExhaustedError(RuntimeError):
    """信道账户级配额/反刷量墙命中 — 必须立即停链, 不可重试 (重试只会延长冷却)."""


# 代理网关 (jiaoch.site) 限流分两类 (2026-06-16 用户纠偏 + advrecv backfill 实测报错原文区分):
#   (1) 真·当日/账户级墙 (反刷量/防攻击): 命中后逐日续戳加重判定 → 必须熔断停链, 不可重试。
#       明确措辞 = "今日请求已达上限 / 请明天再试 / 攻击 / 封禁"。
#   (2) 瞬态限流 (每分钟单接口 120/多接口 200/并发上限 2/流量峰值): 等几分钟即恢复 → 退避重试, 绝不停链。
#       措辞 = "并发请求过多 / 请稍后重试 / 访问频率"。用户原话: "过几分钟重试就可以了"。
# 旧 bug (本次根因): 过宽标记 "请求过多" 把瞬态的"并发请求过多, 请稍后重试"误判成当日墙 → 误熔断停链
#   (advrecv backfill 两次 0 行停链, 主会话两次误判"配额墙")。修法: 只有明确当日/账户级措辞才算墙;
#   其余 (瞬态限流/超时/0 行) 一律退避重试, 终败入 failure_queue 由 drain 补 (mythos §10), 不停全链。
_HARD_WALL_MARKERS = ("今日请求已达上限", "请明天再试", "明日再试", "攻击", "封禁", "黑名单")
_TRANSIENT_RATELIMIT_MARKERS = (
    "并发请求过多", "请稍后重试", "稍后重试", "稍后再试", "访问频率", "频率超限", "请求过于频繁",
)


def _is_transient_ratelimit(msg: str) -> bool:
    return any(m in msg for m in _TRANSIENT_RATELIMIT_MARKERS)


def _is_quota_wall(msg: str) -> bool:
    """仅明确"当日/账户级"措辞算墙 (停链); 瞬态限流/超时/0 行都不算 → 退避重试。"""
    return any(m in msg for m in _HARD_WALL_MARKERS)


class _RateLimiter:
    """tinyshare 代理限流 **主动节流** (撞墙前先睡, 优于反应式退避): 单接口 per_interface/分钟 +
    全接口合计 total/分钟 滑窗。配置来源 sync_registry.yaml `defaults.rate_limit` (no-hardcode, 用户
    2026-06-17/19: 单接口120/多接口200/并发2)。runner 链串行单线程调用 → 并发=1 < max_concurrency;
    max_concurrency 配置守界, 未来并行需配 semaphore。reactive 退避 (_is_transient_ratelimit) 保留作兜底。"""
    _WINDOW = 60.0

    def __init__(self, per_interface_per_min: int, total_per_min: int):
        self.per_api = max(1, int(per_interface_per_min))
        self.total = max(1, int(total_per_min))
        self._api_calls: dict[str, deque] = defaultdict(deque)
        self._all_calls: deque = deque()
        self._lock = threading.Lock()

    @classmethod
    def _evict(cls, dq: deque, now: float) -> None:
        while dq and now - dq[0] > cls._WINDOW:
            dq.popleft()

    def acquire(self, api: str) -> None:
        """阻塞直到本次调用不超 单接口/全接口 每分钟上限, 然后登记本次调用时间。"""
        while True:
            with self._lock:
                now = time.time()
                self._evict(self._all_calls, now)
                dq = self._api_calls[api]
                self._evict(dq, now)
                wait = 0.0
                if len(dq) >= self.per_api:
                    wait = max(wait, self._WINDOW - (now - dq[0]))
                if len(self._all_calls) >= self.total:
                    wait = max(wait, self._WINDOW - (now - self._all_calls[0]))
                if wait <= 0:
                    ts = time.time()
                    dq.append(ts)
                    self._all_calls.append(ts)
                    return
            log.debug("[rate-limit] 主动节流 api=%s 睡 %.1fs (单接口%d/分 全%d/分)", api, wait, self.per_api, self.total)
            time.sleep(wait + 0.05)  # 锁外睡, 不阻塞其他线程窗口推进


_RATE_LIMITER: "_RateLimiter | None" = None
_RATE_LIMITER_INIT = False


def _get_rate_limiter(spec: dict[str, Any]) -> "_RateLimiter | None":
    """从 spec.rate_limit (defaults 合并) 懒初始化全局单例; 未配置 = 不节流 (向后兼容)。"""
    global _RATE_LIMITER, _RATE_LIMITER_INIT
    if _RATE_LIMITER_INIT:
        return _RATE_LIMITER
    _RATE_LIMITER_INIT = True
    cfg = spec.get("rate_limit") or {}
    per_api = cfg.get("per_interface_per_min")
    total = cfg.get("total_per_min")
    if per_api and total:
        _RATE_LIMITER = _RateLimiter(per_api, total)
        log.info("[rate-limit] 主动节流启用: 单接口 %s/分 + 全接口 %s/分 (并发上限 %s)",
                 per_api, total, cfg.get("max_concurrency"))
    return _RATE_LIMITER


def _fetch_with_retry(adapter, spec: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """0 行/异常 → 退避重试; 终败返回 None (调用方入 failure_queue).

    配额/反刷量墙命中 → 抛 QuotaExhaustedError 立即上抛 (不消耗重试, 不逐日续戳),
    由 run_domain/drain/main 熔断停链。
    """
    retry = spec.get("retry") or {}
    attempts = int(retry.get("max_attempts", 3))
    backoffs = list(retry.get("backoff_seconds", [5, 30, 120]))
    # 瞬态限流 (每分钟级窗口) 需等几分钟才恢复, 普通退避太短 → 专用更长退避。
    # evidence: 用户 2026-06-16 "tushare 代理每分钟单接口120/多接口200, 过几分钟重试就可以了"。
    transient_backoffs = list(retry.get("transient_backoff_seconds", [60, 120, 180]))
    allow_empty = bool(spec.get("allow_empty_batch"))
    rate_limiter = _get_rate_limiter(spec)
    last_err: str | None = None
    for i in range(attempts):
        try:
            if rate_limiter is not None:
                rate_limiter.acquire(spec["api"])   # 主动节流: 撞墙前先睡 (config-driven)
            rows = adapter.fetch_raw(spec["api"], **params)
            if rows:
                return rows
            if allow_empty:
                return []
            last_err = "zero_rows"
        except Exception as exc:  # noqa: BLE001 — 重试边界
            last_err = str(exc)[:200]
            if _is_quota_wall(last_err):
                raise QuotaExhaustedError(
                    f"信道配额/反刷量墙命中 domain={spec['domain']} params={params} err={last_err}"
                ) from exc
            # 瞬态限流/超时 → 不停链, 退避重试 (瞬态限流用更长退避等窗口恢复)
        if i < attempts - 1:
            wait = backoffs[min(i, len(backoffs) - 1)]
            if last_err and _is_transient_ratelimit(last_err):
                wait = max(wait, transient_backoffs[min(i, len(transient_backoffs) - 1)])
                log.warning("瞬态限流退避 %ds domain=%s params=%s (非当日墙, 不停链): %s",
                            wait, spec["domain"], params, last_err)
            time.sleep(wait)
    log.warning("fetch 终败 domain=%s params=%s err=%s", spec["domain"], params, last_err)
    return None


def _page_signature(page: list[dict[str, Any]]) -> int:
    """整页内容的序不敏感签名 (行序无关): 网关全量响应行序可能逐次漂移."""
    return hash(frozenset(
        tuple(sorted((k, str(v)) for k, v in row.items())) for row in page
    ))


def _fetch_paged(adapter, spec: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """带 offset 分页的 fetch: registry 声明 page_limit 的域逐页拉到末页 (< limit 即止).

    单页上限不分页 = 静默截断丢数据 (top_inst 1000 整 / stk_surv 100 实测反例, 宪法第 6 条)。
    任何中间页终败 → 整批返回 None (当日整体失败重试) — 部分页写入会伪装成完整日,
    比整体失败更危险。50 页硬上限防接口异常死循环。
    """
    limit = int(spec.get("page_limit") or 0)
    if not limit:
        return _fetch_with_retry(adapter, spec, params)
    all_rows: list[dict[str, Any]] = []
    offset = 0
    prev_page: list[dict[str, Any]] | None = None
    for _ in range(50):  # 50 页 × page_limit 远超任何单日真实量, 防御性边界
        page = _fetch_with_retry(adapter, spec, {**params, "limit": limit, "offset": offset})
        if page is None:
            return None  # 中间页失败不交部分结果
        if offset == 0 and len(page) > limit:
            # 首页行数 > limit = 网关无视 limit 直接给全量 (top_inst 实测 1231 > 1000),
            # 收下即完整数据, 不烧第二发探页
            log.info("网关无视 limit 返回全量 n=%d > %d, 单页收齐 domain=%s params=%s",
                     len(page), limit, spec["domain"], params)
            return page
        # 相同页守卫 (2026-06-12 实测 + 三判官修订): vendor 网关可能同时无视 limit/offset,
        # 每页返回相同全量 (top_inst 20180112 四参数组合同返 1231 行, 86 天 x50 调用白烧实证)。
        # 序不敏感比较 (判官抓的盲区): 网关返回行序不稳定时, 首/末行位置比较会失明 —
        # 改为整页内容集合签名, 行序无关。
        if prev_page is not None and page and prev_page and (
            len(page) == len(prev_page)
            and _page_signature(page) == _page_signature(prev_page)
        ):
            if len(page) % limit == 0:
                # 行数恰为 limit 整倍数 + offset 失效 = 无法区分 "全量" 与 "网关硬截断",
                # fail-closed 判失败 (dc_member 整 5000 pin 反例: 接受即静默截断)
                log.warning("分页相同页且行数整倍数, 疑网关截断+offset 失效 domain=%s params=%s n=%d",
                            spec["domain"], params, len(page))
                return None
            # 行数非整倍数 = 网关返回的就是全量 (limit 也被无视), 首页即完整数据
            log.info("网关 offset 失效但返回全量 (n=%d 非 %d 整倍数), 取首页 domain=%s params=%s",
                     len(page), limit, spec["domain"], params)
            return prev_page
        all_rows.extend(page)
        if len(page) < limit:
            return all_rows
        prev_page = page
        offset += limit
        time.sleep(0.4)  # rule-compliance: ok evidence=同 run_domain 节流口径 vendor-gateway-2026-06-11
    log.warning("分页超 50 页防御上限 domain=%s params=%s", spec["domain"], params)
    return None


_SAMPLE_DIR = _REPO / "backend" / "tests" / "fixtures" / "domain_samples"


def _capture_domain_sample(spec: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """域真实样本存档 (字段语义契约, 根因 A 根治).

    WHY: dc_member 方向反事故 — registry grain 只声明键集合无字段语义, 消费代码
    fixture 用抽象命名 (C1/600000) 时测试与实现会一致地错。首批写入时把前 5 行
    真实数据存进 git, 任何消费代码的测试必须可用真实形态 — 抽象 fixture 失去借口。
    幂等: 样本文件已存在则跳过 (样本是注册时刻快照, 不随数据漂移)。失败不挡写入。
    """
    path = _SAMPLE_DIR / f"{spec['domain']}.json"
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sample = {
            "domain": spec["domain"],
            "api": spec.get("api"),
            "grain": spec.get("grain"),
            "note": "首批真实样本 (字段语义契约): 消费代码 fixture 必须用本形态, 禁抽象命名",
            "rows": rows[:5],
        }
        path.write_text(json.dumps(sample, ensure_ascii=False, indent=1, default=str),
                        encoding="utf-8")
        log.info("域样本已存档 %s (%d 行)", path.name, min(5, len(rows)))
    except Exception as exc:  # noqa: BLE001 — 样本存档失败不挡数据写入, 但必须可见
        log.warning("域样本存档失败 %s: %s", spec["domain"], str(exc)[:120])


def _write_batch(conn, spec: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    """MERGE on grain: DELETE 同 grain 旧行 + INSERT, 加 built_at (幂等)."""
    if not rows:
        return 0
    _capture_domain_sample(spec, rows)
    import pandas as pd

    df = pd.DataFrame(rows)
    df["built_at"] = datetime.now(timezone.utc).isoformat()
    table = spec["target_table"]
    grain: list[str] = list(spec["grain"])
    missing = [g for g in grain if g not in df.columns]
    if missing:
        raise ValueError(f"{table}: api 返回缺 grain 列 {missing} — registry 条目或上游 schema 变了")

    # 批内去重 (复审 HIGH 根因, 2026-06-22): API/分页可返回同 grain 重复行 (limit_list_d 实测单日插14次,
    # 23116 重复行膨胀涨停家数 14x)。DELETE-INSERT MERGE 只跨批去重不去批内 → 必须先 drop_duplicates(grain),
    # 否则批内重复直接累积入库 (grain 无 DB 唯一约束兜底)。keep='last' 取最新一条。
    _ndup = len(df)
    df = df.drop_duplicates(subset=grain, keep="last")
    if len(df) < _ndup:
        log.info("[dedup] %s 批内去重 %d 行 (同 grain 重复)", table, _ndup - len(df))

    # universe 写入门 (2026-06-17 用户: 排除列表=交易日历级硬真相源, 排除股不入库):
    # stock-level 域 (universe_filter=true) 写前丢非白名单前缀行 (北交所8x/9x/三板4x); index/concept
    # 域 (sw_daily/dc_index/moneyflow_ind_dc/index_*) 不设此标不受影响。by_trade_date 域重拉全市场
    # 时防排除股回潮 (与一次性 purge 配套, 让清理生效)。
    if spec.get("universe_filter"):
        from services.universe import ACTIVE_A_SHARE_PREFIXES
        # 默认 A股个股白名单(60/00/30/68); 域可 universe_filter_prefixes 覆盖 (非个股 universe, 如 ETF
        # =15/51/56/58 场内; config-driven 不 hardcode; ETF 是独立 universe 不进 services.universe 个股真相源)。
        prefixes = set(spec.get("universe_filter_prefixes") or ACTIVE_A_SHARE_PREFIXES)
        ucol = spec.get("universe_filter_col") or grain[0]
        if ucol in df.columns:
            _n0 = len(df)
            df = df[df[ucol].astype(str).str[:2].isin(prefixes)]
            if len(df) < _n0:
                log.info("[universe-filter] %s 丢 %d 非白名单前缀行 (keep prefixes=%s)", table, _n0 - len(df), sorted(prefixes))
            # 静默失败门 (2026-06-28 谄媚死根治): universe_filter 丢光整个非空批 = 几乎必是 filter 列漏配
            #   (如 top_list/top_inst 缺 universe_filter_col → 用 grain[0]=trade_date 过滤前缀'20'全丢)。
            #   绝不静默 return 0 让 watermark 假进 — raise 让该批记 failure (诚实失败 > 谄媚成功)。
            if df.empty and _n0 > 0:
                raise ValueError(
                    f"universe_filter 把 {table} 整批 {_n0} 行全丢 (filter_col={ucol!r}, prefixes={sorted(prefixes)}); "
                    f"几乎必是 universe_filter_col 漏配 (该列值不像股票代码)。拒绝静默返0防谄媚死 — "
                    f"修 sync_registry 该域 universe_filter_col 指向真股票代码列 (如 ts_code/con_code)。"
                )
            if df.empty:
                return 0

    # duck_adapter 包装层挡住 DataFrame replacement scan, 显式注册视图
    raw_con = getattr(conn, "_con", conn)
    raw_con.register("df", df)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM df LIMIT 0")
    # 列演进: api 新增列时表自动加列 (raw 镜像语义)
    existing = {r[0] for r in conn.execute(
        f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'"
    ).fetchall()}
    for col in df.columns:
        if col not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" VARCHAR')
    key = " AND ".join(f't."{g}" = s."{g}"' for g in grain)
    conn.execute(f"DELETE FROM {table} t WHERE EXISTS (SELECT 1 FROM df s WHERE {key})")
    cols = ", ".join(f'"{c}"' for c in df.columns)
    try:
        conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM df")
    except Exception as exc:
        # 首批类型推断陷阱 (2026-06-13 chain9 三案实证): 首批列全 NULL/小整数 → 表列被
        # 建成 INT32, 后续真实数据溢出/字符串进不去 (suspend_timing '09:30-10:00' /
        # dc_index level '东财二级行业' / fina_mainbz bz_profit 164.7 亿)。
        # 修法: 按本批 df 的真实 dtype 对冲突列做单调加宽 (int->BIGINT, float->DOUBLE,
        # 其余->VARCHAR), 重试一次; 仍失败则抛出 (fail-closed, 不静默丢行)。
        if "Conversion" not in type(exc).__name__ and "Conversion" not in str(exc):
            raw_con.unregister("df")
            raise
        col_types = {r[0]: r[1] for r in conn.execute(
            f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}'"
        ).fetchall()}
        widened = []
        for col in df.columns:
            dtype = str(df[col].dtype).lower()  # pandas 3.x 字符串列 dtype='str' (非 'object')
            cur = col_types.get(col, "")
            target = None
            if dtype.startswith("int") and cur in ("INTEGER", "SMALLINT", "TINYINT"):
                target = "BIGINT"
            elif dtype.startswith("float") and cur in ("INTEGER", "SMALLINT", "TINYINT", "BIGINT"):
                target = "DOUBLE"
            elif dtype in ("object", "str", "string") and cur not in ("VARCHAR", ""):
                target = "VARCHAR"
            if target:
                conn.execute(f'ALTER TABLE {table} ALTER COLUMN "{col}" SET DATA TYPE {target}')
                widened.append(f"{col}:{cur}->{target}")
        if not widened:
            raw_con.unregister("df")
            raise
        log.warning("首批类型推断加宽 table=%s %s (重试 INSERT)", table, widened)
        conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM df")
    raw_con.unregister("df")
    return len(df)


def _last_watermark_date(domain: str, source: str) -> str | None:
    conn = _smartmoney_conn()
    try:
        from services.source_watermarks import ensure_source_watermark_schema

        ensure_source_watermark_schema(conn)
        row = conn.execute(
            "SELECT last_data_date FROM mart_data_source_watermark WHERE data_domain = ? AND source_name = ?",
            [f"sync:{domain}", source],
        ).fetchone()
        return str(row[0]).replace("-", "") if row and row[0] else None
    finally:
        conn.close()


def _record_outcome(spec: dict[str, Any], *, ok: bool, last_date: str | None,
                    rows: int, error: str | None = None) -> None:
    from services.source_watermarks import record_source_failure, resolve_source_failures, upsert_watermark

    conn = _smartmoney_conn()
    try:
        domain_key = f"sync:{spec['domain']}"
        if ok:
            upsert_watermark(conn, {
                "data_domain": domain_key,
                "source_name": spec["source"],
                "source_tier": SOURCE_TIER_TUSHARE,
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "last_data_date": last_date,
                "row_count": rows,
                "parser_version": "sync_runner_v1",
            })
            resolve_source_failures(conn, data_domain=domain_key, source_name=spec["source"], commit=True)
        else:
            record_source_failure(
                conn,
                data_domain=domain_key,
                source_name=spec["source"],
                source_tier=SOURCE_TIER_TUSHARE,
                error_type="sync_batch_failed",
                last_error=error,
                commit=True,
            )
        try:
            conn.commit()
        except Exception:  # noqa: BLE001 — duckdb autocommit 兼容
            pass
    finally:
        conn.close()


def _calendar_days(start: str, end: str) -> list[str]:
    """全日历日列表 (YYYYMMDD, 含周末) [start, end]。用于 by_ann_date — 公告日可落任意日 (含周末, 实测18%),
    不能用交易日历过滤 (会漏周末公告)。增量 watermark→today 仅几日, 空日 fetch 返0行廉价。"""
    import datetime as _dt
    s = _dt.date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    e = _dt.date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    out, d = [], s
    while d <= e:
        out.append(d.strftime("%Y%m%d"))
        d += _dt.timedelta(days=1)
    return out


def run_domain(domain: str, *, backfill: bool = False, start: str | None = None,
               end: str | None = None, resume: bool = False,
               registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """同步单个数据域. 返回 {domain, batches, rows, failed_batches}.

    resume: by_ts_code 域跳过 target 表已有数据的 ts_code (full-history 单股拉断点续拉, 省重拉)。
    """
    reg = registry or load_registry()
    spec = _domain_spec(reg, domain)
    # socket read timeout (config-driven 防 hung socket 死等; tinyshare 无响应→限时失败→退避重试不卡死)
    socket.setdefaulttimeout(float(spec.get("fetch_timeout_seconds", 120)))
    adapter = _adapter(spec["source"])

    if spec["batch_mode"] == "full_refresh":
        batches: list[dict[str, Any]] = [{}]
    elif spec["batch_mode"] == "by_date_range":
        # 小表 (如大盘资金流 1 行/日) 一次调用拉全范围; 单次上限由 API 决定 (registry 选用前确认)
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        from services.utils import latest_completed_trade_date

        conn0 = _smartmoney_conn()
        try:
            end_d = end or latest_completed_trade_date(conn0).replace("-", "")
        finally:
            conn0.close()
        batches = [{"start_date": start_d, "end_date": end_d}]
    elif spec["batch_mode"] == "by_ts_code":
        batches = _by_ts_code_batches(spec, resume=resume, backfill=backfill)
    elif spec["batch_mode"] == "by_code_list":
        # 显式代码清单循环 (指数/申万行业等 — code 源非 market 股票表): 每 code 一批,
        # code_param 指定参数名 (ts_code/l1_code...), fixed_params 合并 (如指数日线的 start/end)。
        # 用途: index_daily 基准指数 (无视全市场, 只拉 benchmark 代码) / index_member_all 按申万 l1
        # 循环避开无参 5000 整截断 (2026-06-13 实测无参全拉 = 整 5000 = top_inst/dc_member 同型截断反例)。
        code_param = spec.get("code_param", "ts_code")
        fixed = dict(spec.get("fixed_params") or {})
        # ranged by_code_list (指数日线/指标全史回填): end_date 动态化 = latest_completed_trade_date,
        # 防 §4.4 红线"钉死日期" (hardcode end_date 致 benchmark 永久 stale, daily_update 推不过去).
        # 仅当 fixed 有 start_date 且未显式 end_date 时注入; --end 覆盖; MERGE on grain 幂等可全史重拉.
        if "start_date" in fixed and "end_date" not in fixed:
            from services.utils import latest_completed_trade_date
            conn0 = _smartmoney_conn()
            try:
                fixed["end_date"] = end or latest_completed_trade_date(conn0).replace("-", "")
            finally:
                conn0.close()
        batches = [{code_param: c, **fixed} for c in spec["code_list"]]
    elif spec["batch_mode"] == "by_trade_date":
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        days = _trading_days(start_d, end)
        _warn_if_clamped(domain, start_d, days)
        # 增量模式跳过 watermark 当天 (已写过)
        if not backfill and len(days) > 1 and days[0] == (start or _last_watermark_date(domain, spec["source"]) or ""):
            days = days[1:]
        # date_param: API 日期参数名 (默认 trade_date; dividend 用 ex_date / report_rc 用
        # report_date — 锚定列同名, raw 表镜像后 drain 也按它扫 gap)
        date_param = spec.get("date_param", "trade_date")
        batches = [{date_param: d} for d in days]
    elif spec["batch_mode"] == "by_ann_date":
        # 按公告日抓全市场 (十大股东 etc): tushare 支持 ann_date 查全市场, 覆盖季报披露 + ad-hoc 非季末更新
        # (实测 600388 报告期 20231011=非季末 ad-hoc; 全库 1810 非季末期/2902股)。watermark=最新已抓公告日,
        # 增量抓 (watermark, today] 全日历日 (公告日含周末); 峰值日 6000 截断由 _fetch_paged page_limit 分页。
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        from services.utils import latest_completed_trade_date
        conn0 = _smartmoney_conn()
        try:
            end_d = end or latest_completed_trade_date(conn0).replace("-", "")
        finally:
            conn0.close()
        days = _calendar_days(start_d, end_d)
        if not backfill and len(days) > 1 and days[0] == (start or _last_watermark_date(domain, spec["source"]) or ""):
            days = days[1:]  # 增量跳 watermark 当天 (已写过)
        date_param = spec.get("date_param", "ann_date")
        batches = [{date_param: d} for d in days]
    elif spec["batch_mode"] == "by_period":
        # 报告期循环 (财报快报 express_vip: 按报告期整批, ann_date/trade_date 不可批 — 实弹证伪)。
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        from services.utils import latest_completed_trade_date

        conn0 = _smartmoney_conn()
        try:
            end_d = end or latest_completed_trade_date(conn0).replace("-", "")
        finally:
            conn0.close()
        date_param = spec.get("date_param", "period")
        batches = [{date_param: p} for p in _quarter_ends(start_d, end_d)]
    else:
        raise NotImplementedError(f"batch_mode {spec['batch_mode']} 未实现 (by_ts_code/by_month 按需加)")

    conn = _target_conn(spec)
    total_rows, failed, last_ok_date = 0, [], None
    min_rows = int(spec.get("min_rows_per_batch", 0))
    quota_halt = False
    try:
        for params in batches:
            try:
                rows = _fetch_paged(adapter, spec, params)
            except QuotaExhaustedError as exc:
                # 熔断: 配额墙命中, 停止本域剩余批 (已写批由 finally 保留), 上抛停全链
                log.error("配额熔断 domain=%s 已写 %d 行后停止剩余 %d 批: %s",
                          domain, total_rows, len(batches) - len(failed), exc)
                quota_halt = True
                break
            if rows is None:
                failed.append(params)
                continue
            if rows and len(rows) < min_rows:
                log.warning("batch %s 行数 %d < min_rows_per_batch %d (可疑, 仍写入并记 failure)",
                            params, len(rows), min_rows)
                failed.append({**params, "suspect": "below_min_rows"})
            n = _write_batch(conn, spec, rows)
            total_rows += n
            date_key = spec.get("date_param", "trade_date")
            if params.get(date_key):
                last_ok_date = params[date_key]
            elif params.get("end_date"):
                last_ok_date = params["end_date"]
            time.sleep(0.4)  # rule-compliance: ok evidence=vendor-gateway-conn-refused-backoff-2026-06-11
    finally:
        conn.close()

    # 严格判定: 任一批失败即非 ok — 旧宽松口径 (部分成功=True) 使日志 'ok': True
    # 掩盖 29 批失败 (Fable-5 复查 #14 双标问题); 与 _record_outcome 判定统一
    ok = len(failed) == 0 and not quota_halt
    err_payload = json.dumps(failed[:5]) if failed else None
    if quota_halt:
        err_payload = "quota_wall_halt"
    _record_outcome(spec, ok=ok, last_date=last_ok_date, rows=total_rows, error=err_payload)
    result = {"domain": domain, "batches": len(batches), "rows": total_rows,
              "failed_batches": len(failed), "last_date": last_ok_date, "ok": ok}
    if quota_halt:
        result["quota_halt"] = True
    log.info("sync %s", result)
    if quota_halt:
        # 上抛由 main 熔断全链 (已写批与 watermark 已落盘, 可恢复)
        raise QuotaExhaustedError(f"domain={domain} 配额墙停链 (已写 {total_rows} 行)")
    return result


def drain_domain(domain: str, *, registry: dict[str, Any] | None = None,
                 conn=None, adapter=None, trading_days: list[str] | None = None,
                 max_dates: int | None = None, record: bool = True) -> dict[str, Any]:
    """日历 gap 重放 — 应有交易日 − raw 表实有日期 = 缺口, 逐日重拉.

    真相源 = 交易日历 + target_table 本身 (宪法第 1 条), 不依赖 failure_queue 中间
    记录 — queue 按 (域,源,错误类型) 聚合不存日期, 且漏拉/调度漏跑/历史空洞它
    根本看不见。gap 扫描三类全覆盖。

    仅支持 by_trade_date 域 (缺口语义 = 缺整天); 其他 batch_mode 显式返回
    unsupported, 不静默跳过。allow_empty_batch 域的"重查确认空"与终败分开报。
    conn/adapter/trading_days/record 可注入 (单测); 生产路径全走真相源。
    """
    reg = registry or load_registry()
    spec = _domain_spec(reg, domain)
    if spec.get("batch_mode") != "by_trade_date":
        return {"domain": domain, "status": "unsupported", "batch_mode": spec.get("batch_mode")}
    if spec.get("allow_empty_batch"):
        # 空日合法的域 gap 不可判定: 缺口 vs 真空无法区分, 空日会被每日重查永不收敛
        # (dividend 自 2005 年 ~3500 个无除权日会吃光重拉名额 + 永远 partial 告警疲劳)。
        # 这类域走 watermark 增量 (main --drain 分支 fallback run_domain)。
        return {"domain": domain, "status": "drain_inapplicable", "reason": "allow_empty 域走增量"}
    expected = trading_days if trading_days is not None else _trading_days(
        str(spec["data_start"]).replace("-", ""))
    # 只修"今日之前"的确定性缺口: 当日数据到位时刻由 available_after 管 (多在 18:00),
    # 17:00 链里 drain 当日必然假失败。这里**不能**用 latest_completed_trade_date —
    # 它 16:00 截断后会返回今天, 正好defeat本排除 (gate triage 误判: 此处不是 end_date 用途)。
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")  # Phase ψ.5 allowlist: 排除当日非 end_date, 防拉未发布数据
    expected = [d for d in expected if d < today]
    own_conn = conn is None
    conn = conn or _target_conn(spec)
    table = spec["target_table"]
    refilled_rows, still_failed = 0, []
    actual: set[str] = set()
    min_rows = int(spec.get("min_rows_per_batch", 0))
    # "完整日"口径 = 行数达 min_rows 的日; 不足日视同缺口重拉 (MERGE 幂等, 重拉安全)。
    # 复审 HIGH: 旧版 DISTINCT 把 vendor 截断批 (在表但残缺) 当完整, 会洗白 run_domain
    # 标记的 suspect 日且永无重拉机制。
    threshold = min_rows
    try:
        has_table = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
        date_col = spec.get("date_param", "trade_date")  # raw 表镜像 api 字段, 锚定列与参数同名
        if has_table:
            actual = {str(r[0]).replace("-", "")
                      for r in conn.execute(
                          f'SELECT "{date_col}" FROM "{table}" GROUP BY 1 HAVING COUNT(*) >= ?',
                          [threshold]).fetchall()}
        gap = [d for d in expected if d not in actual]
        truncated = max_dates is not None and len(gap) > max_dates
        # 截断取最新优先 (gap 升序取尾部): backlog 超限时昨日数据必须先落地
        # (复审: 最老优先会让永败日卡住头部名额, 最新数据永远轮不到)
        todo = gap if max_dates is None else gap[len(gap) - min(max_dates, len(gap)):]
        if todo:
            adapter = adapter or _adapter(spec["source"])
        for d in todo:
            try:
                rows = _fetch_paged(adapter, spec, {date_col: d})
            except QuotaExhaustedError:
                # 熔断: 已修批保留, 未修日留在 gap 下轮补; 上抛停全链 (不逐日续戳反刷量)
                log.error("配额熔断 drain domain=%s 已补 %d 行后停止 (剩 %d 缺口日未修)",
                          domain, refilled_rows, len(todo) - todo.index(d))
                raise
            if not rows:  # None=终败; [] 理论不可达 (allow_empty 域已前置排除), 防御同终败
                still_failed.append(d)
                continue
            refilled_rows += _write_batch(conn, spec, rows)
            if min_rows and len(rows) < min_rows:
                still_failed.append(d)  # 截断批: 写入 (聊胜于无) 但不算修好, 下轮仍在 gap
            time.sleep(0.4)  # rule-compliance: ok evidence=同 run_domain 节流口径 vendor-gateway-2026-06-11
    finally:
        if own_conn:
            conn.close()
    status = "clean" if not gap else ("drained" if not still_failed and not truncated else "partial")
    _refilled_days = len(todo) - len(still_failed)
    # 空补告警 (2026-06-28 谄媚死根治, 防 top_list 式静默丢光): 抓到 gap 天却 0 行落库 = 可疑
    #   (universe_filter 漏配/写入静默丢)。源端真空洞 (cyq_perf/ths_hot 单日 0 行) 也会触发, 故 WARN 不硬降级,
    #   但可见 → 不再"drained 成功"假象不留痕。(真 filter-drops-all 已被 _write_batch universe_filter raise 拦成 still_failed。)
    if _refilled_days > 0 and refilled_rows == 0:
        log.warning("[empty-drain] %s 抓到 %d 个 gap 天却 0 行落库 — 查 universe_filter 漏配 or 源端真空洞 (status=%s)",
                    domain, _refilled_days, status)
    result = {"domain": domain, "status": status, "expected_days": len(expected),
              "gap_days": len(gap), "refilled_days": _refilled_days,
              "refilled_rows": refilled_rows,
              "still_failed": still_failed[:20], "truncated": truncated}
    if record:  # 送达 (宪法第 5 条): 仍有缺口 → 记 failure; 清干净 → resolve
        _record_outcome(spec, ok=status in ("clean", "drained"),
                        last_date=max(actual | set(todo)) if (actual or todo) else None,
                        rows=refilled_rows,
                        error=json.dumps({"drain_still_failed": still_failed[:10]}) if still_failed else None)
    log.info("drain %s", result)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", help="sync_registry 域名")
    parser.add_argument("--all-due", action="store_true", help="同步全部注册域 (daily_update 集成入口)")
    parser.add_argument("--backfill", action="store_true", help="从 data_start 全量回填")
    parser.add_argument("--resume", action="store_true", help="by_ts_code 域断点续拉 (跳过 target 已有 ts_code)")
    parser.add_argument("--start", default=None, help="覆盖起始日 YYYYMMDD")
    parser.add_argument("--end", default=None, help="覆盖结束日 YYYYMMDD")
    parser.add_argument("--drain", action="store_true",
                        help="日历 gap 重放 (by_trade_date 域): 应有交易日 − 实有 = 缺口逐日重拉")
    parser.add_argument("--max-dates", type=int, default=None, help="drain 单域单次重拉日数上限 (限流边界)")
    args = parser.parse_args()

    reg = load_registry()
    domains = list(reg["domains"]) if args.all_due else ([args.domain] if args.domain else [])
    if not domains:
        parser.error("--domain 或 --all-due 必选其一")

    if args.drain:
        results = []
        for d in domains:
            try:
                res = drain_domain(d, registry=reg, max_dates=args.max_dates)
                # by_report_period 域 (十大股东/财报 by_ts_code): 不再"归专门调度"漏掉 — 走增量 run_domain,
                # _by_ts_code_batches 按交易日历最新应披露期跳过已最新股, 只抓缺新期的股 (修谄媚死: 日常流停更财报/股东)。
                ts_code_incremental = (
                    res.get("status") == "unsupported"
                    and res.get("batch_mode") == "by_ts_code"
                    and (reg["domains"].get(d) or {}).get("increment_mode") == "by_report_period"
                )
                fallback_incremental = (
                    res.get("status") == "drain_inapplicable"
                    or (res.get("status") == "unsupported"
                        and res.get("batch_mode") in ("by_date_range", "full_refresh", "by_ann_date"))
                    or ts_code_incremental
                )
                if fallback_incremental:
                    # 非按日域 + allow_empty 域无 gap 语义 → 增量 run_domain (watermark/报告期 起点)。
                    # 复审 HIGH: drain-only 接线下这些域否则零自动同步 = 静默停更同型复发。
                    # by_ts_code 无 increment_mode (如 stk_factor_pro 日频全市场) 仍 unsupported, 归专门调度。
                    res = run_domain(d, registry=reg)
                    res["mode"] = "incremental_fallback"
                results.append(res)
            except QuotaExhaustedError as exc:
                # 熔断: 配额墙 = 账户级, 续跑其余域只会延长冷却 → 停全链 (区别于单域写锁错)
                log.error("配额熔断停链 (drain): %s — 剩 %d 域不跑", exc, len(domains) - domains.index(d) - 1)
                results.append({"domain": d, "status": "quota_halt", "error": str(exc)[:200]})
                break
            except Exception as exc:  # noqa: BLE001 — 单域异常 (如写锁被占) 不挡其余域, 显式入结果非静默
                results.append({"domain": d, "status": "error", "error": str(exc)[:200]})
        print(json.dumps(results, ensure_ascii=False, indent=1))
        if any(r.get("status") == "quota_halt" for r in results):
            return 2  # 配额墙专用退出码 (调用方区分'信道墙'与'数据失败')
        bad = any(r.get("status") in ("partial", "error") or r.get("failed_batches") for r in results)
        return 1 if bad else 0

    results = []
    for d in domains:
        try:
            results.append(run_domain(d, backfill=args.backfill, start=args.start, end=args.end, resume=args.resume, registry=reg))
        except QuotaExhaustedError as exc:
            log.error("配额熔断停链: %s — 剩 %d 域不跑", exc, len(domains) - domains.index(d) - 1)
            results.append({"domain": d, "status": "quota_halt", "error": str(exc)[:200]})
            break
    print(json.dumps(results, ensure_ascii=False, indent=1))
    if any(r.get("status") == "quota_halt" for r in results):
        return 2
    return 0 if all(r.get("failed_batches") == 0 for r in results) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
