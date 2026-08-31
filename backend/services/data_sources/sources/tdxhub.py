"""TDXHub adapter — official sibling checkout. Unadjusted protocol/vipdoc only.

Do not call ``adjust=qfq/hfq``. That path is banned as execution SSOT.

Official HQ catalog is the TDX client's ``connect.cfg`` ``[HQHOST]`` list
(``TDXHUB_CONNECT_CFG``). ``HQ_HOSTS`` is a frozen snapshot of those official
client/broker names, not a live HTTP catalog and not random community IPs.

TCP-open is not enough: several HQ hosts accept TCP then return an empty
TDX header (``head_buf is not 0x10``). ``quotes_client`` walks hosts until
handshake + one daily bar for ``000001`` succeeds. ``mac_client`` walks the
same catalog on a *separate* raw socket until handshake + nonempty
``capital_flow`` for ``000001`` succeeds. Never runs tdxhub ``bestip`` (that
writes the tdxhub runtime config file). Never reuse the StdQuotes socket for
MAC frames.

``xdxr`` is corporate-action events (not qfq / not a daily factor).
``block`` is namespace ``tdx_block``, parallel to SW / DC / THS — names are
labels, not crosswalk keys. Both ride ``quotes_client``, never MAC.
"""
from __future__ import annotations

import json
import os
import re
import socket
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from services.data_sources.sibling_repos import ensure_import_path
from services.data_sources.tdxhub_kline_recon import fetch_unadjusted_bars

HQ_HOSTS_PROVENANCE = (
    "Frozen snapshot of official TDX client/broker HQ names "
    "(tdxpy transcribed dumped connect.cfg names + mootdx 双线主站 extras). "
    "tdxhub 292e761 2026-04-11 liveness-filtered (14/38 alive, dead commented); "
    "8ba706d 2026-04-13 merged tdxpy+mootdx to 117 and claimed all alive. "
    "Not a live official HTTP catalog (tdx.com.cn/connect.cfg 404). "
    "Live official list = local TDX client connect.cfg [HQHOST] via TDXHUB_CONNECT_CFG. "
    "TCP ping is not a TDX handshake."
)

ALIAS = "tdxhub"
_SMOKE_MARKET = 0
_SMOKE_CODE = "000001"  # rule-compliance: ok evidence=tdx-hq-handshake-ping-sz000001


def tdxhub_root() -> Path:
    return ensure_import_path(ALIAS, strict=True)


def parse_hq_server(raw: str) -> tuple[str, int]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty TDX HQ server")
    if ":" not in text:
        raise ValueError(f"TDX HQ must be ip:port, got {raw!r}")
    host, _, port_text = text.rpartition(":")
    return host.strip(), int(port_text)


def tcp_open(ip: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        sock = socket.create_connection((ip, int(port)), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def is_hq_transport_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    needles = (
        "head_buf",
        "0x10",
        "timeout",
        "timed out",
        "connect",
        "connection",
        "broken pipe",
        "reset",
        "eof",
        "recv",
    )
    return any(n in text for n in needles)


def _hq_host_table() -> list[tuple[str, str, int]]:
    """Frozen official-name snapshot in tdxhub.consts. Not the live client catalog."""
    ensure_import_path(ALIAS, strict=True)
    from tdxhub.consts import HQ_HOSTS  # noqa: E402

    return [(str(name), str(ip), int(port)) for name, ip, port in HQ_HOSTS]


def load_connect_cfg_hq(path: str | Path) -> list[tuple[str, int]]:
    """Read official TDX client ``connect.cfg`` ``[HQHOST]`` entries.

    Uses tdxhub's parser only. Does not run ``bestip`` and does not write
    the tdxhub runtime config file.
    """
    cfg = Path(path)
    if not cfg.is_file():
        raise FileNotFoundError(f"TDX connect.cfg not found: {cfg}")
    ensure_import_path(ALIAS, strict=True)
    from tdxhub.server import parse_connect_cfg  # noqa: E402

    groups = parse_connect_cfg(cfg)
    return [(str(ip), int(port)) for _name, ip, port in groups.get("HQ") or []]


def iter_hq_candidates(
    hosts: Iterable[tuple[str, str, int]] | None = None,
    *,
    explicit: tuple[str, int] | None = None,
) -> list[tuple[str, int]]:
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def _add(ip: str, port: int) -> None:
        key = (str(ip), int(port))
        if key in seen:
            return
        seen.add(key)
        ordered.append(key)

    if explicit is not None:
        _add(*explicit)
    env = os.environ.get("TDXHUB_HQ", "").strip()
    if env:
        _add(*parse_hq_server(env))
    cfg_path = os.environ.get("TDXHUB_CONNECT_CFG", "").strip()
    if cfg_path:
        for ip, port in load_connect_cfg_hq(cfg_path):
            _add(ip, port)
    for _name, ip, port in hosts if hosts is not None else _hq_host_table():
        _add(ip, port)
    return ordered


_LAST_GOOD_HOST: dict[str, tuple[str, int]] = {}

_HOST_MEMORY_PATH_ENV = "TDXHUB_HOST_MEMORY_PATH"
# data/scratch/ is gitignored (.gitignore line 33) — scratch/derived, never a
# source of truth. Losing this file just means the next process re-walks the
# candidate table once, same as before this cache existed; it is an
# optimization, never a dependency for taking data. No host IP is hardcoded
# here or anywhere else in this module — every entry is learned at runtime
# from a handshake that actually answered.
_DEFAULT_HOST_MEMORY_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "scratch" / "tdxhub_host_memory.json"
)


def _host_memory_path() -> Path:
    """Resolve the on-disk host-memory path.

    Env-overridable (``TDXHUB_HOST_MEMORY_PATH``) so tests can point this
    at a throwaway ``tmp_path`` instead of the real ``data/scratch/`` —
    otherwise state persisted by one test run would leak into the next.
    """
    override = os.environ.get(_HOST_MEMORY_PATH_ENV, "").strip()
    return Path(override) if override else _DEFAULT_HOST_MEMORY_PATH


def _parse_memory_entry(entry: Any) -> tuple[str, int] | None:
    if not isinstance(entry, dict):
        return None
    ip, port = entry.get("ip"), entry.get("port")
    if ip is None or port is None:
        return None
    try:
        return (str(ip), int(port))
    except (TypeError, ValueError):
        return None


def _read_host_memory_file() -> dict[str, Any]:
    """Best-effort read of the persisted host-memory file.

    Missing file, corrupt JSON, unreadable permissions, or a file that
    doesn't even hold a JSON object at the top level all collapse to
    "no memory" here — a bad cache file must never block taking data, it
    can only cost the same candidate walk a cold process would have paid
    anyway before this cache existed.
    """
    try:
        raw = _host_memory_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:  # rule-compliance: ok evidence=tdxhub-host-memory-read-best-effort
        return {}
    return data if isinstance(data, dict) else {}


def _write_host_memory_file(data: dict[str, Any]) -> None:
    """Best-effort atomic write of the whole host-memory file.

    Writes to a sibling temp file and ``os.replace``s it into place so a
    concurrent reader (another sync process starting up at the same
    time) can never observe a half-written file. Any failure along the
    way — missing directory (created if possible, otherwise swallowed),
    no write permission, disk full — is swallowed: the in-process
    ``_LAST_GOOD_HOST`` cache still works for this process, only
    cross-process persistence is lost for this write.
    """
    path = _host_memory_path()
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp_name, str(path))
        tmp_name = None
    except Exception:  # rule-compliance: ok evidence=tdxhub-host-memory-write-best-effort
        pass
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:  # rule-compliance: ok evidence=tdxhub-host-memory-tmp-cleanup-best-effort
                pass


def _remembered_host(protocol: str) -> tuple[str, int] | None:
    """In-memory lookup, falling back to disk and hydrating memory on hit.

    A fresh process starts with an empty ``_LAST_GOOD_HOST``; reading
    through to the persisted file here — once — is what makes the memory
    survive that restart. Once hydrated, later calls in the same process
    stay purely in-memory and never touch disk again (until
    ``forget_good_host`` evicts the slot).
    """
    key = str(protocol)
    if key in _LAST_GOOD_HOST:
        return _LAST_GOOD_HOST[key]
    parsed = _parse_memory_entry(_read_host_memory_file().get(key))
    if parsed is not None:
        _LAST_GOOD_HOST[key] = parsed
    return parsed


def _evict_host_memory_file(key: str, target: tuple[str, int] | None) -> None:
    """Mirror an in-memory eviction onto disk, same match-before-evict rule.

    Checked against whatever the *disk* currently holds, independently
    of this process's in-memory state — the two can legitimately
    disagree (e.g. a fresh process that has not hydrated from disk yet),
    and a stale on-disk host must not survive an eviction just because
    this process's memory did not happen to have it cached.
    """
    data = _read_host_memory_file()
    if key not in data:
        return
    if target is not None and _parse_memory_entry(data.get(key)) != target:
        return
    data.pop(key, None)
    _write_host_memory_file(data)


def remember_good_host(protocol: str, server: tuple[str, int]) -> None:
    """Cache the host whose handshake actually answered, keyed by protocol.

    Protocols are isolated on purpose: a std-HQ handshake succeeding on a
    host says nothing about whether the MAC frame handshake would succeed
    on the same host (different wire protocol). ``"hq"`` and ``"mac"`` each
    get their own slot and never share one — including on disk, where
    they are separate keys in the same JSON object.

    Persisted immediately (with a ``saved_at`` timestamp, for future
    diagnosis — see module docstring note on TTL) so the very next
    process — tomorrow's daily sync, a parallel backfill worker — skips
    the cold candidate walk too, not just this one.
    """
    key = str(protocol)
    value = (str(server[0]), int(server[1]))
    _LAST_GOOD_HOST[key] = value
    data = _read_host_memory_file()
    data[key] = {"ip": value[0], "port": value[1], "saved_at": time.time()}
    _write_host_memory_file(data)


def forget_good_host(protocol: str, server: tuple[str, int] | None = None) -> None:
    """Drop a protocol's cached host, in memory and on disk.

    With ``server`` given, only evicts when it still matches what is
    cached (so a failure on some *other*, non-cached host in the same
    walk cannot accidentally wipe out a still-good memory). Without it,
    unconditionally clears the slot. This is how a host that has gone
    offline stops being resurrected: the very next handshake failure
    against it evicts it from disk too, not just from this process.
    """
    key = str(protocol)
    target = None if server is None else (str(server[0]), int(server[1]))
    if target is None:
        _LAST_GOOD_HOST.pop(key, None)
    else:
        current = _LAST_GOOD_HOST.get(key)
        if current == target:
            _LAST_GOOD_HOST.pop(key, None)
    _evict_host_memory_file(key, target)


def hosts_with_memory(
    protocol: str, candidates: Iterable[tuple[str, int]]
) -> list[tuple[str, int]]:
    """Return ``candidates`` reordered so protocol's remembered host is first.

    Relative order of the rest is preserved and nothing is duplicated.
    Does not itself call ``iter_hq_candidates`` — callers build the base
    list themselves, which keeps that call monkeypatch-able per module.
    The remembered host may come from this process's own memory or, on
    a fresh process, from the persisted file (see ``_remembered_host``).
    """
    remembered = _remembered_host(protocol)
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    if remembered is not None:
        ordered.append(remembered)
        seen.add(remembered)
    for ip, port in candidates:
        key = (str(ip), int(port))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _payload_len(raw: Any) -> int:
    if raw is None:
        return 0
    if getattr(raw, "empty", None) is True:
        return 0
    try:
        return int(len(raw))
    except TypeError:
        return 0


def open_quotes(server: tuple[str, int], *, timeout: float = 8.0):
    """Connect to ``server`` and smoke unadjusted daily bars for ``000001``.

    StdQuotes re-reads ``BESTIP.HQ`` after kwargs; pin it in-process so a
    stale ``~/.tdxhub/config.json`` cannot silently steal the socket.
    Does not write that file. ``raise_exception=True`` so empty/None is not
    a silent success. Tries daily categories 9 then 4.
    """
    ensure_import_path(ALIAS, strict=True)
    from tdxhub import config  # noqa: E402
    from tdxhub.quotes import Quotes  # noqa: E402

    config.setup()
    bestip = config.get("BESTIP")
    pinned = dict(bestip) if isinstance(bestip, dict) else {}
    pinned["HQ"] = [str(server[0]), int(server[1])]
    config.set("BESTIP", pinned)
    client = Quotes.factory(
        market="std",
        server=server,
        timeout=timeout,
        heartbeat=False,
        auto_retry=True,
        raise_exception=True,
    )
    try:
        last: BaseException | None = None
        for cat in (9, 4):
            try:
                raw = client.client.get_security_bars(int(cat), _SMOKE_MARKET, _SMOKE_CODE, 0, 5)
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
            if _payload_len(raw) <= 0:
                continue
            setattr(client, "_cm_daily_category", int(cat))
            return client
        raise RuntimeError(f"empty daily bars from {server}: {last!r}")
    except Exception:
        try:
            client.close()
        except Exception:  # rule-compliance: ok evidence=tdx-socket-close-best-effort
            pass
        raise


def quotes_client(
    **kwargs: Any,
):
    """Return a std Quotes client whose HQ handshake actually answers bars."""
    timeout = float(kwargs.pop("timeout", 8))
    max_hosts = int(kwargs.pop("max_hosts", 40))
    tcp_timeout = float(kwargs.pop("tcp_timeout", 1.5))
    explicit = kwargs.pop("server", None)
    if explicit is not None:
        if not isinstance(explicit, (tuple, list)) or len(explicit) != 2:
            raise TypeError(f"server must be (ip, port), got {explicit!r}")
        return open_quotes((str(explicit[0]), int(explicit[1])), timeout=timeout)

    last: BaseException | None = None
    handshake_tries = 0
    for ip, port in hosts_with_memory("hq", iter_hq_candidates()):
        if not tcp_open(ip, port, timeout=tcp_timeout):
            continue
        handshake_tries += 1
        try:
            client = open_quotes((ip, port), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — next host
            last = exc
            forget_good_host("hq", (ip, port))
            if handshake_tries >= max_hosts:
                break
            continue
        remember_good_host("hq", (ip, port))
        return client
    raise RuntimeError(
        f"no handshake-ready TDX HQ after {handshake_tries} TCP-open hosts: {last!r}"
    )


def reader_client(*, tdxdir: str | Path, **kwargs: Any):
    ensure_import_path(ALIAS, strict=True)
    from tdxhub.reader import Reader  # noqa: E402

    return Reader.factory(market="std", tdxdir=str(tdxdir), **kwargs)


def mac_client(**kwargs: Any):
    """MAC-protocol client. Isolated from quotes_client / StdQuotes."""
    from services.data_sources.tdxhub_mac import mac_client as _mac_client

    return _mac_client(**kwargs)


def capital_flow(conn: Any, market: int, code: str, **kwargs: Any) -> dict[str, Any]:
    """Vendor imbalance proxy via MAC ``0x1218`` / ``Stock_ZJLX``. Not conserved money."""
    from services.data_sources.tdxhub_mac import capital_flow as _capital_flow

    return _capital_flow(conn, market, code, **kwargs)


def xdxr(client: Any, ts_code: str, **kwargs: Any) -> dict[str, Any]:
    """Corporate-action events via Quotes ``get_xdxr_info``. Not qfq."""
    from services.data_sources.tdxhub_xdxr import fetch_xdxr

    return fetch_xdxr(client, ts_code, **kwargs)


def block(client: Any, **kwargs: Any) -> dict[str, Any]:
    """Vendor ``tdx_block`` membership. Not SW / DC / THS; no name crosswalk."""
    from services.data_sources.tdxhub_block import fetch_block

    return fetch_block(client, **kwargs)


# ---------------------------------------------------------------------------
# TdxhubSource — sync_runner adapter for the ``daily`` domain (2026-08-31
# 授权换源 tushare -> tdxhub). Appended below the pre-existing module-level
# helpers above; none of those functions were touched.
# ---------------------------------------------------------------------------

# 无官方建议值; 仅作构造函数默认值 (跟 baostock.py DEFAULT_TIMEOUT_SECONDS 同风格),
# 真正接入 sync_registry 的域注册环节可显式传参覆盖。
TDXHUB_DAILY_MIN_SUCCESS_RATE_DEFAULT = 0.9
# 自然日回看窗口, 用来在批量按 code 拉 K 线时顺带带出前一交易日 close。20 天覆盖
# A 股最长的春节/国庆连续休市 (含相邻周末) 仍有余量; 不是"猜前一交易日"的日历算术 ——
# 真正的"前一交易日"是从窗口返回的实际 bars 里按 trade_date < target 取最后一条
# (见 _fetch_one_row), 这个常量只决定窗口开多大, 不参与任何交易日判断。
TDXHUB_DAILY_PREV_CLOSE_LOOKBACK_DAYS_DEFAULT = 20

# ---------------------------------------------------------------------------
# xdxr 跨进程磁盘缓存 — 全历史回填 1650 个交易日, 若每个交易日都对 5220 只票
# 各打一次 client.xdxr, 单日约占全市场取数一半耗时, 1650 天不可行; 但回填目标
# 都是过去的交易日, 历史除权记录不会再变, 查一次落盘就够, 见 TdxhubSource
# docstring 坑 4 及 ``TdxhubSource._xdxr_events``。data/scratch/ 已 gitignore
# (.gitignore 第 33 行) —— 跟 host-memory 缓存同一挂载点, 同一"纯优化不是
# 依赖"哲学: 丢了这个文件, 下一个进程只是重新对没落盘成功的 code 打一次网络,
# 不会不能取数。
# ---------------------------------------------------------------------------

_XDXR_CACHE_PATH_ENV = "TDXHUB_XDXR_CACHE_PATH"
_DEFAULT_XDXR_CACHE_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "scratch" / "tdxhub_xdxr_cache.json"
)

# 注入代码 (``TdxhubSource(extra_ts_codes=...)``) 的形态校验 —— 通达信协议
# ts_code 恰好是 6 位数字代码 + 交易所后缀, 三个交易所都覆盖 (含目前列不出清单、
# 只能靠外部注入的北交所 BJ, 见类 docstring 坑 2)。
_TS_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def _xdxr_cache_path() -> Path:
    """解析磁盘 xdxr 缓存路径。

    环境变量可覆盖 (``TDXHUB_XDXR_CACHE_PATH``), 跟上面 ``_host_memory_path``
    同一理由: 测试必须能把这个路径指到一次性 ``tmp_path``, 否则一次测试写下的
    状态会串到下一次、甚至写脏真实项目里的 ``data/scratch/`` 文件。
    """
    override = os.environ.get(_XDXR_CACHE_PATH_ENV, "").strip()
    return Path(override) if override else _DEFAULT_XDXR_CACHE_PATH


def _today() -> date:
    """包一层, 让测试能钉死一个确定的"今天", 不必依赖真实墙钟日期去验证
    ``cached_at`` 新鲜度规则 (见 ``TdxhubSource._xdxr_events``)。"""
    return date.today()  # rule-compliance: ok evidence=xdxr 缓存写入时刻的墙钟日期,非最新交易日; 新鲜度规则 cached_at>target 比的是'我何时查的'而不是交易日历


def _read_xdxr_cache_file() -> dict[str, Any]:
    """尽力读取落盘 xdxr 缓存。

    文件缺失、JSON 损坏、无读权限、或顶层根本不是 JSON object, 全部收敛成
    "无缓存" —— 一份坏的缓存文件绝不能挡住取数, 它最多让这个 code 多打一次
    ``client.xdxr``, 跟这份缓存不存在时冷启动要付的代价一样, 不会更差。
    """
    try:
        raw = _xdxr_cache_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:  # rule-compliance: ok evidence=tdxhub-xdxr-cache-read-best-effort
        return {}
    return data if isinstance(data, dict) else {}


def _write_xdxr_cache_file(data: dict[str, Any]) -> None:
    """尽力原子写入整份 xdxr 缓存文件。

    跟上面 ``_write_host_memory_file`` 完全同一套 ``tempfile.mkstemp`` +
    ``os.replace`` 写法, 同一理由: 并发读者 (另一个同时在跑的 sync/回填进程)
    绝不能看到半写文件。写入路上任何失败 (目录不存在/建不出来、无写权限、
    磁盘满) 全部吞掉 —— 这份缓存纯粹是优化, 不是取数的依赖; 丢一次写只是让
    下一个进程对没落盘成功的这批 code 重新打一次网络, 不影响正确性。
    """
    path = _xdxr_cache_path()
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp_name, str(path))
        tmp_name = None
    except Exception:  # rule-compliance: ok evidence=tdxhub-xdxr-cache-write-best-effort
        pass
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:  # rule-compliance: ok evidence=tdxhub-xdxr-cache-tmp-cleanup-best-effort
                pass


def _parse_xdxr_cache_entry(entry: Any) -> tuple[date, list[Any]] | None:
    """校验单个 code 的磁盘缓存条目结构完整性。

    ``entry`` 必须是 dict, 带字符串形态、可解析成合法日期的 ``cached_at``
    (紧凑 YYYYMMDD) 和 list 形态的 ``events``; 任何一处不满足都返回 ``None``
    (= 对这个 code 按未缓存处理, 照常走网络重查)。这一层校验存在的理由就是
    "单个 code 的记录损坏不能让整份缓存不可用"—— 顶层文件能读出来、是 dict,
    就已经不算"整份缓存故障", 剩下的损坏只应该局限在出问题的那一个 code。
    """
    if not isinstance(entry, dict):
        return None
    cached_at_text = entry.get("cached_at")
    events = entry.get("events")
    if not isinstance(cached_at_text, str) or not isinstance(events, list):
        return None
    try:
        cached_at = date(
            int(cached_at_text[0:4]), int(cached_at_text[4:6]), int(cached_at_text[6:8])
        )
    except (TypeError, ValueError):
        return None
    return cached_at, events


class TdxhubDailyBatchError(RuntimeError):
    """单日全市场成功率低于阈值 —— 拒绝静默半批入库, 见 ``TdxhubSource.fetch_raw``。"""


class TdxhubSource:
    """sync_runner 调用约定: ``fetch_raw(api, **params) -> list[dict]``。

    2026-08-31 ``daily``(日K) 域授权换源 tushare -> tdxhub 的落地 adapter, 与
    ``sources/fuyao.py`` 的 ``FuyaoSource``、``sources/baostock.py`` 的
    ``BaostockSource`` 同型。本类要封死的四个坑, 全部已实测 (见任务背景, 非道听途说):

    1. **按票循环, 不是按日期一把拉全市场**: 通达信协议 ``get_security_bars``
       (经 ``tdxhub_kline_recon.fetch_unadjusted_bars`` 包装) 是"给一个 code 要
       最近/某窗口的 K 线", 没有"给一个日期要全市场"的接口 —— 跟 tushare
       ``daily(trade_date=...)`` 语义相反。本类对每个交易日先建全市场代码清单
       (``client.stocks(0)``/``client.stocks(1)``), 再逐票调
       ``fetch_unadjusted_bars`` 取一个含前一根的小窗口。单只票约 59ms, 5220 只
       约 5.1 分钟 (已实测)。

    2. **通达信列不出北交所代码表**: ``client.stocks(2)`` 报错「市场代码错误,
       目前只支持沪深市场」—— 这是协议本身的限制, 不是本类的 bug。
       ``STOCKS_MARKET_BY_EXCHANGE`` 目前只含 SZ(0)/SH(1); 未来要加北交所 (代码段
       ``92``) 得另找代码来源 (如现有 tushare/扶摇代码表), 不能指望
       ``client.stocks(2)``。**代码段前缀不写死在函数体里** —— 见类常量
       ``DEFAULT_BOARD_PREFIXES``, 或构造 ``TdxhubSource(board_prefixes=...)``
       时整体覆盖 (含未来加北交所)。

       按代码取北交所日K本身是能拿到真实 OHLCV 的 (``920002.BJ`` 等已实测,
       ``protocol_market()`` 已支持 ``.BJ -> market 2``) —— 拿不到的只是"列表"
       接口, 不是"取数"接口。所以北交所走**注入**而不是等 ``stocks(2)`` 修好:
       构造 ``TdxhubSource(extra_ts_codes=[...])`` 传入的代码 (形如
       ``920002.BJ``, 由调用方从扶摇代码表/现有 canonical 数据等别处拿到) 会被
       追加进 ``_universe()`` 的结果, 与 ``client.stocks()`` 得到的沪深代码一样
       走 ``_fetch_one_row`` —— 不为 BJ 开特殊分支, 也同样受 ``min_success_rate``
       批量成功率约束 (哪怕全部注入代码都失败, 只要沪深大盘那几千只把成功率托
       住, 就不会单独因为 BJ 部分而 raise; 但每个注入代码的取数失败/成功都计入
       同一批 ``attempted``/``rows``, 不单独豁免)。非法形态 (不是
       ``\\d{6}.(SH|SZ|BJ)``) 的注入代码在**构造时**就 ``ValueError``, 不等到
       取数才炸 —— 见 ``_validate_extra_ts_codes``。

    3. **amount 单位换算**: ``fetch_unadjusted_bars`` 给的 amount 单位是元,
       tushare ``daily.amount`` 单位是千元 —— 落库前必须 ``/1000``, 不做会让
       下游按 tushare 语义读出的成交额虚高 1000 倍。vol (手) 两边同单位, 原样使用
       不换算。

    4. **pre_close 必须按当日除权事件调整, 不能直接拿前一交易日 close 当 pre_close**:
       下游 ``institution_follow_paper.py`` 拿 pre_close 判涨停开盘, 算错是真金
       白银的错。标准除权公式 (``_adjusted_pre_close``)::

           pre_close = (prev_close - fenhong/10 + peigu/10*peigujia)
                       / (1 + songzhuangu/10 + peigu/10)

       无除权事件时 pre_close = prev_close (round 到 2 位, 对齐 tushare)。事件来自
       ``client.xdxr(code)`` (``category==1`` 且 ``name=='除权除息'`` 的记录), 按
       (year, month, day) 精确匹配目标交易日。``client.xdxr`` 一次返回该票**全部**
       历史除权记录 (不是按日期查单条), 日更要连续处理 5000+ 只票, 所以本类对
       同一 code 只查一次并做进程内缓存 (``_xdxr_cache``, 见 ``_xdxr_events``) ——
       同一 ``TdxhubSource`` 实例存活期间不会对同一 code 重复打 ``client.xdxr``。

       进程内缓存只活一个进程的生命周期, 回填 1650 个交易日等于 1650 次冷启动、
       1650 遍重打 5220 次 ``client.xdxr`` —— 不可行。所以 ``_xdxr_events`` 在
       进程内 dict 之下还有一层落盘 JSON 缓存 (默认
       ``data/scratch/tdxhub_xdxr_cache.json``, 环境变量
       ``TDXHUB_XDXR_CACHE_PATH`` 可覆盖), 带 ``cached_at``(缓存生成的真实日期)。
       可用性规则是本层缓存的正确性核心, 精确到"缓存生成日 vs 目标交易日"两者
       谁在前:

           cached_at(生成日) >  target(目标交易日) -> 可用
               (生成缓存那天, target 当天的除权记录必然已经出现, 不会再变)
           cached_at                          <= target -> 必须重查
               (缓存可能早于/等于 target 当天才出现的除权记录; 日更场景
               target==今天时 cached_at(今天生成)不会大于今天, 于是永远重查,
               这是故意的 —— 今天的除权记录随时可能刚发生)

       磁盘缓存的任何故障 (文件缺失/JSON 损坏/顶层非 dict/单条目结构损坏/无读
       权限/无写权限) 一律静默降级成"这个 code 未缓存", 照常走网络, 绝不阻断
       取数 —— 见 ``_read_xdxr_cache_file``/``_write_xdxr_cache_file``/
       ``_parse_xdxr_cache_entry``。

    取不到前一根 K 线时 (新股上市首日, 或窗口 ``prev_close_lookback_days`` 天内
    确实没有更早的成交日) pre_close 退化为当日 open, 不崩、不置 None、其余字段
    正常返回。

    单只票取数异常, 或该票当日在返回窗口里没有目标日那一根 (停牌/未上市/已退市等),
    一律记录并跳过, 不让整批失败; 但成功票数 / 尝试票数低于 ``min_success_rate``
    (默认 0.9) 时整批 raise ``TdxhubDailyBatchError``, 避免"静默半批入库"落进
    ``raw_tushare_daily``。

    客户端复用: ``quotes_client()`` (握手已优化, 冷启动 0.35s, 主机记忆跨进程落盘)
    在实例内惰性建立一次, 同一实例后续调用复用同一连接, 不逐票重连; 测试可传
    ``client_factory`` 注入假客户端, 完全不碰网络。
    """

    name = ALIAS

    # 通达信 client.stocks(market) 的 market 参数取值 (已实测: 0=深市/SZ,
    # 1=沪市/SH; 2 报错「市场代码错误, 目前只支持沪深市场」, 故此表不含北交所)。
    STOCKS_MARKET_BY_EXCHANGE: dict[str, int] = {"SZ": 0, "SH": 1}

    # 代码段前缀白名单 (已实测与扶摇代码表交叉验证一致: 深市 00/30=2901 只,
    # 沪市 60/68=2319 只, 合计 5220 只)。类常量而非函数体内硬编码 —— 加北交所
    # (前缀 92) 时构造 ``TdxhubSource(board_prefixes={...})`` 整表覆盖即可,
    # 不用改这个类或 fetch_raw 的任何一行。
    DEFAULT_BOARD_PREFIXES: dict[str, tuple[str, ...]] = {
        "SZ": ("00", "30"),
        "SH": ("60", "68"),
    }

    SUPPORTED_APIS = frozenset({"daily"})

    def __init__(
        self,
        *,
        client_factory: Any | None = None,
        board_prefixes: dict[str, tuple[str, ...]] | None = None,
        min_success_rate: float = TDXHUB_DAILY_MIN_SUCCESS_RATE_DEFAULT,
        prev_close_lookback_days: int = TDXHUB_DAILY_PREV_CLOSE_LOOKBACK_DAYS_DEFAULT,
        extra_ts_codes: Sequence[str] | None = None,
    ) -> None:
        self._client_factory = client_factory  # 测试注入假客户端; 生产环境用 quotes_client
        self._client: Any = None
        self._board_prefixes: dict[str, tuple[str, ...]] = {
            exchange: tuple(prefixes)
            for exchange, prefixes in (board_prefixes or self.DEFAULT_BOARD_PREFIXES).items()
        }
        self._min_success_rate = float(min_success_rate)
        self._prev_close_lookback_days = int(prev_close_lookback_days)
        self._xdxr_cache: dict[str, list[dict[str, Any]]] = {}
        # 构造期校验, 不等到取数才炸 (坑 2 附近说明)。
        self._extra_ts_codes: tuple[str, ...] = self._validate_extra_ts_codes(extra_ts_codes)

    @staticmethod
    def _validate_extra_ts_codes(extra_ts_codes: Sequence[str] | None) -> tuple[str, ...]:
        """校验注入代码形态 (``\\d{6}.(SH|SZ|BJ)``), 任何一条不合规立刻
        ``ValueError`` —— 构造时就炸, 不要等 ``fetch_raw`` 跑到一半才发现。"""
        if not extra_ts_codes:
            return ()
        validated: list[str] = []
        for raw in extra_ts_codes:
            code = str(raw)
            if not _TS_CODE_RE.match(code):
                raise ValueError(
                    "tdxhub extra_ts_codes entry must match \\d{6}.(SH|SZ|BJ), "
                    f"got {raw!r}"
                )
            validated.append(code)
        return tuple(validated)

    def _get_client(self) -> Any:
        """惰性建立一次, 实例存活期间复用 (坑 1 附近说明: 不逐票重连)。"""
        if self._client is None:
            self._client = (
                self._client_factory() if self._client_factory is not None else quotes_client()
            )
        return self._client

    def _universe(self, client: Any) -> list[str]:
        """按 ``STOCKS_MARKET_BY_EXCHANGE``/``DEFAULT_BOARD_PREFIXES`` 过滤出沪深
        ``ts_code`` 清单, 再把构造时传入并已校验过的 ``extra_ts_codes``(通常是
        ``client.stocks(2)`` 列不出的北交所代码, 见坑 2) 追加在后面 —— 与沪深
        代码去重, 顺序确定 (沪深按现有排序规则在前, 注入代码保持调用方传入的
        原始顺序在后)。追加进来的代码走的是跟沪深完全相同的 ``_fetch_one_row``
        路径, 这里不为它们单独分支。"""
        codes: list[str] = []
        for exchange, market_id in self.STOCKS_MARKET_BY_EXCHANGE.items():
            prefixes = self._board_prefixes.get(exchange) or ()
            if not prefixes:
                continue
            for item in client.stocks(market_id) or []:
                code = str((item or {}).get("code") or "").strip()
                if code and code.startswith(prefixes):
                    codes.append(f"{code}.{exchange}")
        universe = sorted(set(codes))
        if not self._extra_ts_codes:
            return universe
        seen = set(universe)
        for extra in self._extra_ts_codes:
            if extra in seen:
                continue
            seen.add(extra)
            universe.append(extra)
        return universe

    def _xdxr_events(
        self, client: Any, code: str, target: date | None = None
    ) -> list[dict[str, Any]]:
        """同一 code 的除权事件, 三级读取: 进程内 dict -> 落盘 JSON -> 网络 (坑 4)。

        进程内 ``self._xdxr_cache`` 是一级缓存, 命中即返回, 与 ``target`` 无关
        (同一实例存活期间对同一 code 只查一次, 这一层语义不因加了磁盘层而变)。

        落盘 JSON 是二级缓存, 只在显式给出 ``target``(要取的交易日) 时才参与
        —— 可用性规则见类 docstring 坑 4 附近, 精确到"缓存生成日 vs 目标交易日"
        谁在前 (``cached_at > target`` 才可用, ``<=`` 必须重查), 不是泛泛的"新
        不新鲜"。``target=None``(仅剩两个直调该方法的旧测试在用) 完全不碰磁盘,
        是加磁盘层之前的纯进程内缓存语义, 原样保留。

        磁盘缓存的任何故障 (缺文件/JSON 损坏/顶层非 dict/这一条目结构损坏/无读
        权限/无写权限) 一律静默降级为"这个 code 未缓存", 照常走
        ``client.xdxr`` 网络查询, 绝不阻断取数——查询失败同样按"该票无可用除权
        信息"降级 (不阻塞这一票, pre_close 退化为不调整), 不重试。
        """
        if code in self._xdxr_cache:
            return self._xdxr_cache[code]

        if target is not None:
            parsed = _parse_xdxr_cache_entry(_read_xdxr_cache_file().get(code))
            if parsed is not None:
                cached_at, cached_events = parsed
                if cached_at > target:
                    self._xdxr_cache[code] = cached_events
                    return cached_events

        try:
            events = client.xdxr(code) or []
        except Exception:  # rule-compliance: ok evidence=tdxhub-xdxr-best-effort-cache
            events = []
        events = list(events)
        self._xdxr_cache[code] = events
        if target is not None:
            self._persist_xdxr_cache_entry(code, events)
        return events

    def _persist_xdxr_cache_entry(self, code: str, events: list[dict[str, Any]]) -> None:
        """把这个 code 刚查到的除权事件写回磁盘缓存 (读-改-写整份文件, 与
        模块级 ``remember_good_host`` 同风格)。写失败 (只读文件系统/磁盘满等)
        全部由 ``_write_xdxr_cache_file`` 吞掉 —— 不影响本次取数, 只影响下次
        是否要对这个 code 重新打网络。"""
        data = _read_xdxr_cache_file()
        data[code] = {"cached_at": _today().strftime("%Y%m%d"), "events": events}
        _write_xdxr_cache_file(data)

    def _matching_xdxr_event(
        self, client: Any, code: str, target: date
    ) -> dict[str, Any] | None:
        for event in self._xdxr_events(client, code, target):
            try:
                y = int(event.get("year"))
                m = int(event.get("month"))
                d = int(event.get("day"))
            except (TypeError, ValueError):
                continue
            if (y, m, d) != (target.year, target.month, target.day):
                continue
            if int(event.get("category") or 0) != 1:
                continue
            if str(event.get("name") or "") != "除权除息":
                continue
            return event
        return None

    def _adjusted_pre_close(
        self, client: Any, ts_code: str, target: date, prev_close: float
    ) -> float:
        """坑 4 的算式本体。无匹配除权事件时原样返回 prev_close (round 到 2 位)。"""
        code = ts_code.split(".", 1)[0]
        event = self._matching_xdxr_event(client, code, target)
        if event is None:
            return round(float(prev_close), 2)
        fenhong = float(event.get("fenhong") or 0)
        songzhuangu = float(event.get("songzhuangu") or 0)
        peigu = float(event.get("peigu") or 0)
        peigujia = float(event.get("peigujia") or 0)
        denominator = 1.0 + songzhuangu / 10.0 + peigu / 10.0
        if denominator <= 0:
            return round(float(prev_close), 2)
        numerator = float(prev_close) - fenhong / 10.0 + peigu / 10.0 * peigujia
        return round(numerator / denominator, 2)

    def _fetch_one_row(
        self, client: Any, ts_code: str, target: date
    ) -> dict[str, Any] | None:
        """一只票一天的行。窗口取不到目标日那一根时返回 ``None`` (调用方计入失败)。"""
        window_start = target - timedelta(days=self._prev_close_lookback_days)
        bars = fetch_unadjusted_bars(client, ts_code, start=window_start, end=target)
        if not bars:
            return None
        # bars 已由 fetch_unadjusted_bars/dedup_kline_rows 按 (ts_code, trade_date)
        # 升序去重排好 (见 tdxhub_kline_recon.dedup_kline_rows) —— 不猜前一交易日,
        # 直接扫描实际返回的 bars 找 trade_date < target 里最后 (最近) 的一条 close。
        prev_close_raw: float | None = None
        today_bar: tuple | None = None
        for bar in bars:
            bar_date = bar[1]
            if bar_date < target:
                prev_close_raw = float(bar[5])
            elif bar_date == target:
                today_bar = bar
                break
            else:
                break
        if today_bar is None:
            return None
        open_ = float(today_bar[2])
        high = float(today_bar[3])
        low = float(today_bar[4])
        close = float(today_bar[5])
        vol = float(today_bar[6])
        amount = float(today_bar[7]) / 1000.0  # 坑 3: 元 -> 千元
        if prev_close_raw is None:
            # 新股上市首日 (或窗口内确实没有更早成交日): 退化为当日 open, 不崩。
            pre_close = round(open_, 2)
        else:
            pre_close = self._adjusted_pre_close(client, ts_code, target, prev_close_raw)
        change = round(close - pre_close, 4)
        pct_chg = None if pre_close <= 0 else round(change / pre_close * 100, 4)
        return {
            "ts_code": ts_code,
            "trade_date": target.strftime("%Y%m%d"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "pre_close": pre_close,
            "change": change,
            "pct_chg": pct_chg,
            "vol": vol,
            "amount": amount,
        }

    def fetch_raw(self, api: str, **params: Any) -> list[dict[str, Any]]:
        """``api == "daily"``: ``params["trade_date"]`` (紧凑 8 位) 当日全市场沪深
        A 股日K, 返回恰好 11 key 的 ``dict`` 列表 (不含 ``built_at`` —— sync_runner
        的 ``_prepare_batch_df`` 会自己加, 见该函数 docstring)。"""
        name = str(api or "").strip()
        if name not in self.SUPPORTED_APIS:
            raise KeyError(
                f"tdxhub: unknown api {api!r} (known: {sorted(self.SUPPORTED_APIS)})"
            )
        trade_date_text = str(params.get("trade_date") or "").strip()
        if len(trade_date_text) != 8 or not trade_date_text.isdigit():
            raise ValueError(
                "tdxhub daily requires a compact YYYYMMDD trade_date, got "
                f"{params.get('trade_date')!r}"
            )
        target = date(
            int(trade_date_text[0:4]), int(trade_date_text[4:6]), int(trade_date_text[6:8])
        )
        client = self._get_client()
        codes = self._universe(client)
        rows: list[dict[str, Any]] = []
        attempted = 0
        for ts_code in codes:
            attempted += 1
            try:
                row = self._fetch_one_row(client, ts_code, target)
            except Exception:  # noqa: BLE001 — 单票失败不许拖垮整批, 见类 docstring
                row = None
            if row is not None:
                rows.append(row)
        if attempted > 0:
            success_rate = len(rows) / attempted
            if success_rate < self._min_success_rate:
                raise TdxhubDailyBatchError(
                    f"tdxhub daily {trade_date_text}: success rate {success_rate:.3f} "
                    f"({len(rows)}/{attempted}) below threshold "
                    f"{self._min_success_rate} — refusing silent half-batch"
                )
        return rows


__all__ = [
    "ALIAS",
    "TdxhubDailyBatchError",
    "TdxhubSource",
    "block",
    "capital_flow",
    "forget_good_host",
    "hosts_with_memory",
    "is_hq_transport_error",
    "iter_hq_candidates",
    "load_connect_cfg_hq",
    "mac_client",
    "open_quotes",
    "parse_hq_server",
    "quotes_client",
    "reader_client",
    "remember_good_host",
    "tcp_open",
    "tdxhub_root",
    "xdxr",
]
