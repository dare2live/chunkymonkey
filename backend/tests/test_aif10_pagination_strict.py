"""严格翻页引擎判据 (2026-09-25 刀 A, spec_holders_pagination.md §3.2/§7.2)。

用例名即节点名; 每条用例的输入"其它条件全部满足、只违反它"; 断言精确的
``reason`` 字符串或精确异常类, 不接受父类宽断言 (memory 形态一)。``_FakeClient``
只实现 ``get_v1`` 一个方法 (与 ``AIF10Client.get_v1`` 同签名), 从不触网。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from aif10_scraper import batch
from aif10_scraper.client import AIF10UnknownCodeError
from aif10_scraper.pagination import (
    DEFAULT_MAX_PAGES_PER_QUERY,
    PaginationIntegrityError,
    PaginationPolicy,
    fetch_pages_strict,
)

PAGE_SIZE = 3

BASE_POLICY = PaginationPolicy(
    sort_columns="END_DATE,SECURITY_CODE,HOLDER_RANK,HOLDER_NAME",
    sort_types="-1,1,1,1",
    identity_columns=("SECURITY_CODE", "END_DATE", "HOLDER_RANK", "HOLDER_NAME"),
    row_tolerance_rows=0,
    duplicates="error",
    drift_refetch=0,
)


def _row(i: int, *, end_date: str = "2026-06-30", rank: int = 1, **overrides) -> dict:
    row = {
        "SECURITY_CODE": f"{i:06d}",
        "END_DATE": end_date,
        "HOLDER_RANK": rank,
        "HOLDER_NAME": f"holder-{i}",
        "HOLD_NUM": 1000 + i,
    }
    row.update(overrides)
    return row


def _page(rows: list[dict], *, code: int = 0, count: int | None = None, pages: int) -> dict:
    return {
        "code": code,
        "message": "ok" if code == 0 else ("返回数据为空" if code == 9201 else "x"),
        "success": code == 0,
        "pages": pages,
        "count": count if count is not None else len(rows),
        "data": rows,
    }


class _SeqClient:
    """按调用顺序依次返回预置页; 记录每次调用的完整 kwargs。"""

    def __init__(self, pages: list[dict]):
        self._pages = list(pages)
        self.calls: list[dict] = []

    def get_v1(self, report_name, **kwargs):
        self.calls.append({"report_name": report_name, **kwargs})
        idx = len(self.calls) - 1
        if idx >= len(self._pages):
            raise AssertionError(
                f"_SeqClient exhausted after {len(self._pages)} pages, "
                f"got extra call #{idx + 1}: {kwargs}"
            )
        return self._pages[idx]


# ---------------------------------------------------------------------------
# TieDriftClient — 真实形状假供应商 (并列排序键 + 跨页位移, 照 probe §3.3)
# ---------------------------------------------------------------------------

_TIE_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6"]
_TIE_DRIFTED_ORDER = ["S1", "S2", "S4", "S3", "S5", "S6"]
_TIE_ROWS = {
    key: {
        "SECURITY_CODE": f"{i:06d}",
        "END_DATE": "2026-06-30",
        "HOLDER_RANK": 1,
        "HOLDER_NAME": f"holder-{key}",
    }
    for i, key in enumerate(_TIE_ORDER, start=1)
}


class TieDriftClient:
    """6 行全部 (END_DATE=2026-06-30, HOLDER_RANK=1) 并列。请求的 ``sort_columns``
    不含 ``SECURITY_CODE`` 时, 供应商内部顺序在两次请求间漂移 (page2 用
    ``_TIE_DRIFTED_ORDER`` 切片) —— S3 相邻页重复、S4 永远不出现,
    ``count``/``pages`` 全程稳定 (6/2)。含 ``SECURITY_CODE`` 时两次都按稳定
    顺序切片, 漂移消失。"""

    def __init__(self):
        self.calls: list[dict] = []

    def get_v1(self, report_name, *, page, page_size, sort_columns, sort_types, **kwargs):
        self.calls.append({"page": page, "sort_columns": sort_columns})
        drifts = "SECURITY_CODE" not in sort_columns
        if page == 1:
            keys = _TIE_ORDER[0:3]
        elif page == 2:
            keys = _TIE_DRIFTED_ORDER[3:6] if drifts else _TIE_ORDER[3:6]
        else:
            raise AssertionError("TieDriftClient only serves 2 pages")
        data = [_TIE_ROWS[k] for k in keys]
        return {
            "code": 0,
            "message": "ok",
            "success": True,
            "pages": 2,
            "count": 6,
            "data": data,
        }


# ---------------------------------------------------------------------------
# E0 — 事实钉: 旧判据 (HEAD 84afe240 的 assess_pagination_land) 在这个输入下放行
# ---------------------------------------------------------------------------


def test_E0_tie_drift_old_criterion_passes():
    """旧判据只比总数: landed(6) < expected(6) 恒为假, 所以 08-28/04-30 那种
    "总数对但内容错" (36.4%/47.1% 的行被跨页漂移吞掉) 的丢法完全看不见。

    可执行事实: 从 git 历史里把 HEAD 84afe240 那份 pagination.py 的
    ``assess_pagination_land`` 取出来, 用本用例的输入 (expected=6, landed=6)
    真的跑一遍, 断言它的 ``truncated`` 确实是 ``False`` —— 不是复述, 是重放。
    找不到那个提交 (浅克隆等) 时退化为纯逻辑断言 (6 < 6 恒为假)。
    """
    assert (6 < 6) is False  # 旧判据的核心比较式, 无论如何都成立

    repo_root = Path(__file__).resolve().parents[2]
    old_sha = "84afe2407c96e0ab781ad1a08191c1dd99f730a8"
    try:
        proc = subprocess.run(
            ["git", "show", f"{old_sha}:backend/aif10_scraper/pagination.py"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:  # noqa: BLE001 — git 不可用时退化为上面的纯逻辑断言
        return
    if proc.returncode != 0 or not proc.stdout.strip():
        return
    namespace: dict = {}
    exec(compile(proc.stdout, f"<git:{old_sha}:pagination.py>", "exec"), namespace)  # noqa: S102
    old_assess = namespace["assess_pagination_land"]
    verdict = old_assess(expected_count=6, landed_rows=6, page_size=PAGE_SIZE)
    assert verdict.truncated is False


# ---------------------------------------------------------------------------
# E1/E2 — 新引擎能不能挡住并列排序漂移
# ---------------------------------------------------------------------------


def test_E1_tie_drift_caught_by_identical_duplicates():
    client = TieDriftClient()
    policy = replace(
        BASE_POLICY,
        sort_columns="END_DATE,HOLDER_RANK",
        sort_types="-1,1",
        identity_columns=(),
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    exc = excinfo.value
    assert exc.reason == "identical_duplicates"
    assert exc.ledger.raw_rows == 6
    assert exc.ledger.unique_rows == 5
    assert exc.ledger.page_sizes == (3, 3)
    assert exc.ledger.counts_seen == (6, 6)
    assert exc.ledger.passes == 1


def test_E1b_tie_drift_identical_duplicates_never_retried_even_when_reachable():
    """E1 本身 ``drift_refetch=0``: 重取守卫 ``passes < 1 + policy.drift_refetch``
    恒为 ``1 < 1`` = False, 根本走不到 ``exc.reason in _DRIFT_RETRY_REASONS`` 那一步
    —— spec §7.2 E1 变异行「把 identical_duplicates 加进重取集合 → E1 红于 passes」
    在 E1 原用例上不可观测 (返修实证: 168 个 cut-A 测试全绿也测不出这条变异,
    blocking finding)。本用例把 ``drift_refetch=1``, 让重取守卫真正可达
    (仿 E22 用 ``drift_refetch=1`` 隔离「short_page 不重取」的做法), 才能证明
    ``identical_duplicates`` 确实不在 ``_DRIFT_RETRY_REASONS`` 里: 只 1 遍就报错、
    假客户端只被调 2 次 (``TieDriftClient`` 每遍固定漂移同一种重复, 若被误重取,
    会在第二遍再次触发同一错误, 使 ``passes``==2 且调用 4 次)。"""
    client = TieDriftClient()
    policy = replace(
        BASE_POLICY,
        sort_columns="END_DATE,HOLDER_RANK",
        sort_types="-1,1",
        identity_columns=(),
        drift_refetch=1,
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    exc = excinfo.value
    assert exc.reason == "identical_duplicates"
    assert exc.ledger.passes == 1
    assert len(client.calls) == 2


def test_E2_unique_sort_makes_tie_drift_vanish():
    client = TieDriftClient()
    rows, ledger = fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert len(rows) == 6
    assert ledger.identical_duplicates == 0
    assert ledger.unique_rows == 6


# ---------------------------------------------------------------------------
# E3/E4 — 9201 的两种含义 (第 1 页合法空 / 中途非法)
# ---------------------------------------------------------------------------


def test_E3_empty_page1_legal():
    client = _SeqClient([_page([], code=9201, count=0, pages=0)])
    rows, ledger = fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert rows == []
    assert ledger.count_declared == 0


def test_E4_empty_code_mid_fetch():
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([], code=9201, count=0, pages=0),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "empty_code_mid_fetch"
    assert excinfo.value.ledger.passes == 1


# ---------------------------------------------------------------------------
# E5 — 假客户端给未知 code, 引擎自己也要 fail-closed
# ---------------------------------------------------------------------------


def test_E5_unknown_code_from_fake_client_fail_closed():
    client = _SeqClient([_page([_row(1), _row(2)], code=7, count=2, pages=1)])
    with pytest.raises(AIF10UnknownCodeError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.code == 7
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# E6/E13 — 第 1 页就能判定的两种失败 (在取第 2 页之前抛)
# ---------------------------------------------------------------------------


def test_E6_pages_count_inconsistent():
    client = _SeqClient([_page([_row(1), _row(2), _row(3)], count=6, pages=3)])
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "pages_count_inconsistent"
    assert len(client.calls) == 1


def test_E13_page_cap_exceeded():
    rows = [_row(i) for i in range(1, PAGE_SIZE + 1)]
    client = _SeqClient([_page(rows, count=600, pages=200)])
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(
            client,
            "RPT_X",
            page_size=PAGE_SIZE,
            policy=BASE_POLICY,
            max_pages_per_query=100,
        )
    assert excinfo.value.reason == "page_cap_exceeded"
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# E7/E8 — 翻页途中总数/页数漂移 (容差 0, 不重取)
# ---------------------------------------------------------------------------


def test_E7_count_drift():
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=7, pages=2),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "count_drift"


def test_E8_pages_drift():
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=6, pages=3),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "pages_drift"


# ---------------------------------------------------------------------------
# E9/E10 — 页长 (非末页 / 末页)
# ---------------------------------------------------------------------------


def test_E9_short_page():
    # 页 1 (非末页, pages=2>1) 只 2 行而非 page_size=3 —— 页长立即检查
    # (在取第 2 页之前), 假客户端只被调 1 次。
    client = _SeqClient([_page([_row(1), _row(2)], count=6, pages=2)])
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "short_page"
    assert len(client.calls) == 1


def test_E10_last_page_size_mismatch():
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=5, pages=2),
            _page([_row(4), _row(5), _row(6)], count=5, pages=2),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "last_page_size_mismatch"


# ---------------------------------------------------------------------------
# E11/E12/E12b — 身份键
# ---------------------------------------------------------------------------


def test_E11_identity_conflict():
    row3a = _row(3, HOLD_NUM=100)
    row3b = _row(3, HOLD_NUM=200)  # 身份四列相同, HOLD_NUM 不同
    client = _SeqClient(
        [
            _page([_row(1), _row(2), row3a], count=6, pages=2),
            _page([row3b, _row(5), _row(6)], count=6, pages=2),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    exc = excinfo.value
    assert exc.reason == "identity_conflict"
    assert exc.ledger.identical_duplicates == 0


def test_E12_identity_column_empty_value():
    bad_row = _row(3, HOLDER_NAME="")
    client = _SeqClient(
        [
            _page([_row(1), _row(2), bad_row], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=6, pages=2),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "identity_column_missing"


def test_E12b_identity_column_absent():
    bad_row = _row(3)
    del bad_row["HOLDER_NAME"]
    client = _SeqClient(
        [
            _page([_row(1), _row(2), bad_row], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=6, pages=2),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "identity_column_missing"


# ---------------------------------------------------------------------------
# E14 — 调用方声明的部分取数 (跳过 6~7 步)
# ---------------------------------------------------------------------------


def test_E14_partial_by_caller():
    client = _SeqClient([_page([_row(1), _row(2), _row(3)], count=6, pages=2)])
    rows, ledger = fetch_pages_strict(
        client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY, max_pages=1
    )
    assert len(rows) == 3
    assert ledger.partial_by_caller is True


# ---------------------------------------------------------------------------
# E15/E16 — 容差语义 (成对: 唯一差别是容差)
# ---------------------------------------------------------------------------


def _tolerance_input():
    return [
        _page([_row(1), _row(2), _row(3)], count=6, pages=2),
        _page([_row(4), _row(5)], count=5, pages=2),
    ]


def test_E15_tolerance_absorbs_drift():
    policy = replace(BASE_POLICY, row_tolerance_rows=2)
    client = _SeqClient(_tolerance_input())
    rows, ledger = fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    assert len(rows) == 5
    assert ledger.raw_rows == 5
    assert "count_drift" in ledger.reasons
    assert "last_page_size_mismatch" in ledger.reasons


def test_E16_tolerance_zero_rejects_same_input():
    client = _SeqClient(_tolerance_input())
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=BASE_POLICY)
    assert excinfo.value.reason == "count_drift"


# ---------------------------------------------------------------------------
# E17 — duplicates: allow
# ---------------------------------------------------------------------------


def test_E17_duplicates_allow():
    policy = replace(BASE_POLICY, identity_columns=(), duplicates="allow")
    dup_row = _row(2)
    client = _SeqClient([_page([_row(1), dup_row, dict(dup_row)], count=3, pages=1)])
    rows, ledger = fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    assert len(rows) == 3
    assert ledger.identical_duplicates == 1


# ---------------------------------------------------------------------------
# E18 — 静态: 没有宽松调用方
# ---------------------------------------------------------------------------


def test_E18_no_lenient_callers_static():
    # parents[0]=backend/tests, parents[1]=backend (本文件在 backend/tests/ 下)。
    # 之前误用 parents[2] (worktree 根), 扫的是不存在的 <root>/services (0 文件) 与
    # 顶层 <root>/scripts (1 个 .py, 不是 backend/scripts 的 97 个) —— 整条门按构造
    # 恒绿, 加一个调用也测不出来 (blocking finding, 2026-09-25 返修)。
    backend_root = Path(__file__).resolve().parents[1]
    banned = re.compile(r"\b(iter_pages|fetch_all_pages_concurrent|fetch_report)\(")
    offenders: list[str] = []
    scanned = 0
    for base in ("services", "scripts"):
        for path in (backend_root / base).rglob("*.py"):
            scanned += 1
            text = path.read_text(encoding="utf-8")
            for match in banned.finditer(text):
                offenders.append(f"{path.relative_to(backend_root)}:{match.group(1)}")
    # 防「全部跳过」假绿 (与 L6 同型断言): 两个目录真实各有 283/97 个 .py 文件。
    assert scanned > 100, f"only scanned {scanned} files — directories likely wrong"
    assert offenders == []


# ---------------------------------------------------------------------------
# E19 — reasons 闭合集合
# ---------------------------------------------------------------------------


def test_E19_reasons_closed_set():
    assert PaginationIntegrityError.REASONS == {
        "empty_code_mid_fetch",
        "pages_count_inconsistent",
        "count_drift",
        "pages_drift",
        "short_page",
        "last_page_size_mismatch",
        "raw_rows_ne_count",
        "identical_duplicates",
        "identity_conflict",
        "identity_column_missing",
        "page_cap_exceeded",
    }


# ---------------------------------------------------------------------------
# E20/E21/E22 — 漂移整日重取
# ---------------------------------------------------------------------------


def test_E20_count_drift_refetched_once_then_clean():
    policy = replace(BASE_POLICY, drift_refetch=1)
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=7, pages=2),  # 第一遍漂移
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=6, pages=2),  # 第二遍干净
        ]
    )
    rows, ledger = fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    assert len(rows) == 6
    assert ledger.passes == 2
    assert ledger.reasons == ("refetch_after_count_drift",)
    assert len(client.calls) == 4


def test_E21_count_drift_twice_raises():
    policy = replace(BASE_POLICY, drift_refetch=1)
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=7, pages=2),
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),
            _page([_row(4), _row(5), _row(6)], count=8, pages=2),
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    assert excinfo.value.reason == "count_drift"
    assert excinfo.value.ledger.passes == 2


def test_E22_short_page_not_refetched():
    # pages=2>1 -> 页 1 是非末页; 只 2 行 (!=page_size=3) 立即触发 short_page,
    # 即使 drift_refetch=1 也不重取 (short_page 不在漂移重取集合里)。
    policy = replace(BASE_POLICY, drift_refetch=1)
    client = _SeqClient([_page([_row(1), _row(2)], count=6, pages=2)])
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    assert excinfo.value.reason == "short_page"
    assert excinfo.value.ledger.passes == 1
    assert len(client.calls) == 1


def test_E23_page1_9201_after_known_nonempty_drift_raises():
    """漂移重取后, 第 1 页返回 9201 但**前一遍已经证实这一天非空**
    (count_1=6>0) —— §1.1 的「第 1 页 9201 = 合法空」只在单遍内成立
    (一次全新的翻页, 之前没有任何证据), 不能延伸到「上一遍已经拿到过真实
    行、这一遍才漂移成 9201」这个跨遍已知事实 (blocking finding, 2026-09-25
    返修: 之前的代码对每一遍都独立判断, 第 2 遍页 1 一见 9201 就直接判定合法
    空、把已经证实存在的一整天数据当空丢掉, 不报错也不重取)。"""
    policy = replace(BASE_POLICY, drift_refetch=1)
    client = _SeqClient(
        [
            _page([_row(1), _row(2), _row(3)], count=6, pages=2),  # pass1 页1: 非空
            _page([_row(4), _row(5), _row(6)], count=7, pages=2),  # pass1 页2: count 漂移 -> 触发重取
            _page([], code=9201, count=0, pages=0),  # pass2 页1: 9201 —— 不是合法空
        ]
    )
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_pages_strict(client, "RPT_X", page_size=PAGE_SIZE, policy=policy)
    exc = excinfo.value
    assert exc.reason == "empty_code_mid_fetch"
    assert exc.ledger.passes == 2
    assert len(client.calls) == 3


# ---------------------------------------------------------------------------
# E24 — 静态: 不为刀 B 预留运行路径
# ---------------------------------------------------------------------------


def test_E24_no_cut_b_run_path_reserved_in_batch_static():
    """``fetch_all_pages_strict`` 已经返回 ``(rows, PageLedger)``, 要留证据的
    调用方 (刀 B 的 holders) 直接调它即可; ``batch.py`` 不需要也不应该另开
    一个 ``fetch_all_pages_with_ledger`` 转发壳子 —— 施工规格附录第 3 条写死
    「本刀不要为刀 B 预留任何运行路径」(blocking finding, 2026-09-25 返修:
    该函数曾以「刀 B 要用」为由留在 batch.py 里, 零调用方、零测试, 是给刀 B
    预留的运行路径, 已删)。"""
    import aif10_scraper
    import aif10_scraper.batch as batch_module

    assert not hasattr(batch_module, "fetch_all_pages_with_ledger")
    assert "fetch_all_pages_with_ledger" not in aif10_scraper.__all__


# ---------------------------------------------------------------------------
# H1/H2 — 返修 fix_holders_A_r3.md: 直接调 batch.fetch_all_pages /
# fetch_all_pages_sharded 的隔离用例 (mutverify blocking: 6 个允许测试文件
# 里没有任何一处直接调这两个函数, "把 fetch_all_pages 改回吞错误"、"把
# fetch_all_pages_sharded 的合并容差判据废掉" 这两个变异 168/221 个 cut-A
# 用例全绿, 无一个探测到)。报表用真实注册报表 "RPT_F10_EH_FREEHOLDERS"
# (registry.py 已登记 sort_columns/sort_types, batch.py 的两个入口都先经
# _resolve_sort -> get_report, 未注册报表连测试都跑不起来)。
# ---------------------------------------------------------------------------


def test_H1_fetch_all_pages_does_not_swallow_pagination_integrity_error():
    """spec §3.3: ``fetch_all_pages`` 不捕 ``PaginationIntegrityError`` ——
    旧版"截断只打 warning、返回部分行"的分支已删除。直接调 ``batch.fetch_all_pages``
    (不经内层 ``fetch_pages_strict`` 单测), 用一次 ``pages_count_inconsistent``
    触发, 断言异常原样冒出 (精确类 + 精确 reason), 假客户端只被调 1 次
    (第 1 页即可判定, 不会为了"多试一次"再翻页)。

    变异 -> 预期红: 把 ``fetch_all_pages`` 改回
    ``try: ... except PaginationIntegrityError: logger.warning(...); return land.rows``
    (旧行为) -> 本用例从"抛出"变成"正常返回", ``pytest.raises`` 捕不到异常, 红。
    """
    client = _SeqClient([_page([_row(1), _row(2), _row(3)], count=6, pages=3)])
    with pytest.raises(PaginationIntegrityError) as excinfo:
        batch.fetch_all_pages(
            "RPT_F10_EH_FREEHOLDERS",
            page_size=PAGE_SIZE,
            client=client,
        )
    assert excinfo.value.reason == "pages_count_inconsistent"
    assert len(client.calls) == 1


class _ShardProbeClient:
    """给 ``fetch_all_pages_sharded`` 用的假客户端: 单分片场景下, 分片规划的
    探针 (``plan_security_code_shards`` 里的 ``_probe_v1``) 与分片自身取数
    都请求同一个 page=1 (因为 ``max_pages=1`` 让分片内只取第 1 页就以
    ``partial_by_caller`` 停下, 从不请求 page=2) —— 两次请求都返回同一份
    "总数 6、本页 3 行" 的页, 制造"落地 3 行 vs 声明总数 6"的合并期落差,
    且不触发分片自身 (固定 STRICT 容差 0) 的完整性判据: 第 1 页 3 行 ==
    page_size, 页长检查通过; ``partial_by_caller=True`` 跳过总数比对。"""

    def __init__(self, rows: list[dict], *, count: int, pages: int):
        self._rows = rows
        self._count = count
        self._pages = pages
        self.calls: list[dict] = []

    def get_v1(self, report_name, *, page, **kwargs):
        self.calls.append({"page": page, **kwargs})
        if page != 1:
            raise AssertionError(
                f"_ShardProbeClient 只应被请求 page=1 (max_pages=1 单分片), 收到 page={page}"
            )
        return {
            "code": 0,
            "message": "ok",
            "success": True,
            "pages": self._pages,
            "count": self._count,
            "data": list(self._rows),
        }


def _sharded_merge_gap_input() -> _ShardProbeClient:
    # 供应商声明 6 行 (page_size=3 -> pages=2), 但 max_pages=1 只取第 1 页
    # (3 行) 就停 -> 合并后 fetched_rows=3, provider_count(expected_total)=6,
    # 落差恒为 3, 与容差值配对成 E15/E16 同型的"只差一个数字"用例。
    return _ShardProbeClient([_row(1), _row(2), _row(3)], count=6, pages=2)


def test_H2_sharded_merge_tolerance_exceeded_flags_truncated():
    """spec §3.3: ``fetch_all_pages_sharded`` 合并后总量比对改用
    ``policy.row_tolerance_rows`` 直接比 ``expected_total`` 与
    ``len(all_rows)`` (取代旧版 ``assess_pagination_land`` 的
    ``0.002``/``500`` 硬编码容差)。落差恒为 3 (见 ``_sharded_merge_gap_input``),
    容差取 2 -> 恰好超出容差 1 行 -> ``truncated=True`` 且 reasons 含
    ``sharded_raw_rows_ne_count``。

    变异 -> 预期红: 删掉 ``abs(len(all_rows) - expected_total) > tolerance``
    这段判据 (或把 ``>`` 改成永远不成立的比较, 相当于关闭截断判定) ->
    ``truncated`` 变回 ``False``、``land_reasons`` 变空, 本用例红。
    """
    client = _sharded_merge_gap_input()
    policy = replace(BASE_POLICY, row_tolerance_rows=2)
    result = batch.fetch_all_pages_sharded(
        "RPT_F10_EH_FREEHOLDERS",
        page_size=PAGE_SIZE,
        max_pages=1,
        client=client,
        policy=policy,
    )
    assert result["fetched_rows"] == 3
    assert result["provider_count"] == 6
    assert result["truncated"] is True
    assert any(r.startswith("sharded_raw_rows_ne_count") for r in result["land_reasons"])


def test_H2b_sharded_merge_within_tolerance_not_flagged():
    """H2 的对照组: 同一份输入 (落差恒为 3), 容差改成 3 (与落差相等, 不超出)
    -> 不触发。与 H2 唯一差别是 ``row_tolerance_rows`` 的值, 证明触发点就是
    这个数字比较本身, 不是别的副作用。"""
    client = _sharded_merge_gap_input()
    policy = replace(BASE_POLICY, row_tolerance_rows=3)
    result = batch.fetch_all_pages_sharded(
        "RPT_F10_EH_FREEHOLDERS",
        page_size=PAGE_SIZE,
        max_pages=1,
        client=client,
        policy=policy,
    )
    assert result["fetched_rows"] == 3
    assert result["provider_count"] == 6
    assert result["truncated"] is False
    assert result["land_reasons"] == []
