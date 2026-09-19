"""institution_profile 状态机单测 — 成本/收益正确性证伪门 (promote 纪律)。

覆盖: 新进→增持(加权平均成本)→减持(部分了结)→退出(清仓) 全链数值 + seeded/多轮 episode/无价跳过
+ 2026-07-03 审计修2: share_class 混流过滤 / 源重复键去重 (SQL 级) + 状态机三缺陷 (unit 级)。
"""
import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.institution_profile import (
    build_episodes,
    build_profiles,
    run_episode_state_machine,
)


def _row(holder="H", stock="600000", period="20240331", status="新进", is_exit=False,
         shares=None, chg=None, htype="QFII", notice="20240430", c1=10.0, c2=10.0, c3=10.0):
    return (holder, stock, period, status, is_exit, shares, chg, htype, notice, c1, c2, c3)


def test_full_lifecycle_weighted_cost_and_realized():
    """新进1000股@10 → 增持1000股@20 (成本加权→15) → 减持500股@30 (realized+7500) → 退出@40 (剩1500股×25)。"""
    rows = [
        _row(period="20240331", status="新进", shares=1000, c1=10.0, c2=10.0, c3=10.0),
        _row(period="20240630", status="增持", shares=2000, chg=1000, c1=20.0, c2=20.0, c3=20.0),
        _row(period="20240930", status="减持", shares=1500, chg=-500, c1=30.0, c2=30.0, c3=30.0),
        _row(period="20241231", status="退出", is_exit=True, shares=1500, chg=-1500, c1=40.0, c2=40.0, c3=40.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 1, "closed": 1, "seeded": 0, "no_price_skip": 0,
                     "unpriced_close": 0, "superseded": 0}
    ep = eps[0]
    assert ep["status"] == "closed"
    assert ep["cost_c1"] == pytest.approx(15.0)          # (1000×10 + 1000×20) / 2000
    assert ep["peak_shares"] == 2000
    # realized = 减持 500×(30−15)=7500 + 退出 1500×(40−15)=37500 = 45000
    assert ep["realized_c1"] == pytest.approx(45000.0)
    # 收益口径: realized/(cost×peak) = 45000/(15×2000) = 150%
    assert ep["realized_c1"] / (ep["cost_c1"] * ep["peak_shares"]) == pytest.approx(1.5)


def test_seeded_from_change_flagged():
    """首见即增持 (前期第11名进前十) → 按新进开仓且 seeded=True (画像排除)。"""
    rows = [_row(status="增持", shares=800, chg=300, c1=12.0)]
    eps, stats = run_episode_state_machine(rows)
    assert stats["seeded"] == 1
    assert eps[0]["seeded"] is True and eps[0]["status"] == "holding"
    assert eps[0]["cost_c1"] == 12.0 and eps[0]["shares"] == 800


def test_multi_round_episodes_same_holder_stock():
    """退出后再新进 = 独立新 episode (易方达×茅台 3 轮实证形态)。"""
    rows = [
        _row(period="20230331", status="新进", shares=100, c1=10.0),
        _row(period="20230630", status="退出", is_exit=True, shares=100, chg=-100, c1=12.0),
        _row(period="20240331", status="新进", shares=200, c1=8.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 2, "closed": 1, "seeded": 0, "no_price_skip": 0,
                     "unpriced_close": 0, "superseded": 0}
    closed = [e for e in eps if e["status"] == "closed"][0]
    holding = [e for e in eps if e["status"] == "holding"][0]
    assert closed["realized_c1"] == pytest.approx(100 * 2.0)
    assert holding["cost_c1"] == 8.0 and holding["shares"] == 200


def test_no_price_skips_event_not_episode():
    """窗口无K线 (c1=None) 的事件跳过, 不影响已开 episode。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20240630", status="增持", shares=200, chg=100, c1=None),  # 停牌窗口
        _row(period="20240930", status="退出", is_exit=True, shares=100, chg=-100, c1=20.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats["no_price_skip"] == 1
    ep = eps[0]
    # 增持事件被跳过 → shares 仍 100, 成本仍 10, 退出 realized=100×10
    assert ep["shares"] == 100 and ep["cost_c1"] == 10.0
    assert ep["realized_c1"] == pytest.approx(1000.0)


def test_exit_without_open_is_noop():
    """无开仓的孤儿退出行 (数据起点截断) → 不产生 episode。"""
    eps, stats = run_episode_state_machine([_row(status="退出", is_exit=True, shares=100, chg=-100)])
    assert eps == [] and stats["closed"] == 0


# ── 2026-07-03 审计修2c: 状态机三缺陷证伪门 ─────────────────────────────────────


def test_exit_with_null_window_price_closes_unpriced():
    """退出行窗口无价 (c1=None) 也必须关闭 episode (修前被无价跳过吞掉 → 幽灵 holding)。
    status='unpriced_close': 最终腿 PnL 不可测不计 (不知道≠0), 已实现部分保留, 不进 'closed' 评级。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20240630", status="减持", shares=50, chg=-50, c1=20.0),  # realized 50×10=500
        _row(period="20240930", status="退出", is_exit=True, shares=50, chg=-50,
             c1=None, c2=None, c3=None),   # 停牌窗口无价
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats["unpriced_close"] == 1 and stats["closed"] == 0
    assert len(eps) == 1
    ep = eps[0]
    assert ep["status"] == "unpriced_close", "修前: 退出被吞, status 残留 holding"
    assert ep["close_date"] == "20240930"
    assert ep["realized_c1"] == pytest.approx(500.0)   # 只有已实现部分, 最终腿不估价


def test_new_entry_over_open_episode_supersedes_not_overwrites():
    """已开 episode 再见'新进' (中间退出披露缺失) → 旧 episode 按当期窗口价关闭
    (status='superseded', 不进评级) 再开新 — 修前 dict 直接覆盖 = 旧 episode 静默丢失。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20241231", status="新进", shares=200, c1=16.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 2, "closed": 0, "seeded": 0, "no_price_skip": 0,
                     "unpriced_close": 0, "superseded": 1}
    assert len(eps) == 2, "修前: 只剩 1 个 episode (旧的被覆盖丢失)"
    old = [e for e in eps if e["status"] == "superseded"][0]
    new = [e for e in eps if e["status"] == "holding"][0]
    assert old["close_date"] == "20241231"
    assert old["realized_c1"] == pytest.approx(100 * (16.0 - 10.0))  # 按当期窗口价关闭
    assert new["cost_c1"] == 16.0 and new["shares"] == 200 and new["seeded"] is False


# ── 2026-07-03 审计修2a/2b: build_episodes 源查询 (SQL 级证伪门) ─────────────────


def _sql_conn():
    """内存库模拟生产 ATTACH sm/tr (CREATE SCHEMA 两部名同解析) + 手造 period_windows。

    E0: rebuild reads canonical-only (holders fact retired 2026-07-26).
    """
    c = duck_mem()
    c.executescript("""
    CREATE SCHEMA sm; CREATE SCHEMA tr;
    CREATE TABLE sm.canonical_top10_float_holders_period (
        stock_code TEXT, report_date TEXT, holder_set TEXT, holder_rank INTEGER,
        row_seq INTEGER, holder_name TEXT, hold_ratio_float DOUBLE, notice_date TEXT,
        is_exit_row BOOLEAN, holder_name_norm TEXT, share_class TEXT,
        shares_approx BIGINT, change_status TEXT, hold_change_num DOUBLE,
        holder_type TEXT,
        -- 2026-09-08 Step 4: 身份三桶的输入列
        holder_code TEXT, is_holder_org BOOLEAN);
    -- 自然人白名单 (牛散名录)。build_episodes 对它 fail closed: 缺表即拒绝继续,
    -- 所以 fixture 必须显式给出 —— "还没建名录"与"名录里没有这个人"是两件事。
    CREATE TABLE sm.dim_holder_name_tag (
        holder_name TEXT, tag TEXT, known_from TEXT,
        identity_confidence TEXT, identity_grade TEXT);
    CREATE TABLE period_windows (
        stock_code TEXT, report_date TEXT, prev_period TEXT, w_start TEXT, w_end TEXT,
        c1_vwap DOUBLE, c2_eod DOUBLE, c3_lhb DOUBLE, c3_eff DOUBLE);
    CREATE TABLE sm.fact_index_daily (
        trade_date TEXT, ts_code TEXT, close DOUBLE,
        available_at TIMESTAMPTZ, source_table TEXT, built_at TIMESTAMPTZ);
    CREATE TABLE tr.v_sw_industry_pit (stock_code TEXT, l1_name TEXT, in_date TEXT, out_date TEXT);
    """)
    c.executemany("INSERT INTO period_windows VALUES (?,?,?,?,?,?,?,?,?)", [
        ("600000", "20240331", None, "2024-01-01", "2024-03-31", 10.0, 10.0, None, 10.0),
        ("600000", "20240630", "20240331", "2024-03-31", "2024-06-30", 20.0, 20.0, None, 20.0),
    ])
    return c


# 具名列而非位置: DDL 加列时位置 INSERT 会当场炸 (本轮第三次踩), 物理列顺序不是契约。
_HOLDER_COLS = (
    "stock_code", "report_date", "holder_set", "holder_rank", "row_seq", "holder_name",
    "hold_ratio_float", "notice_date", "is_exit_row", "holder_name_norm", "share_class",
    "shares_approx", "change_status", "hold_change_num", "holder_type",
    "holder_code", "is_holder_org",
)
_HOLDER_ROW_N = len(_HOLDER_COLS)
_HOLDER_INSERT = (
    "INSERT INTO sm.canonical_top10_float_holders_period ("
    + ", ".join(_HOLDER_COLS) + ") VALUES (" + ",".join("?" * _HOLDER_ROW_N) + ")"
)


def test_build_episodes_filters_non_a_share_class():
    """share_class != 'A' (B/H 股行) 混入 A 股 qfq 价计价 = 价格错配 → 源查询硬滤。"""
    c = _sql_conn()
    try:
        c.execute("INSERT INTO sm.dim_holder_name_tag VALUES "
                  "('章建平','niusan','20260820','no_evidence_either_way','name_only_untrusted')")
        c.executemany(_HOLDER_INSERT, [
            ("600000", "20240331", "free", 1, 1, "基金一号", 1.0, "20240430", False, "基金一号", "A", 100, "新进", None, "基金", "C1", True),
            ("600000", "20240630", "free", 1, 1, "基金一号", 1.0, "20240730", True, "基金一号", "A", 100, "退出", -100, "基金", "C1", True),
            ("600000", "20240331", "free", 2, 1, "港资股东", 1.0, "20240430", False, "港资股东", "H", 50, "新进", None, "QFII", "C2", True),
            ("600000", "20240331", "free", 3, 1, "B股东", 1.0, "20240430", False, "B股东", "B", 50, "新进", None, "法人", "C3", True),
        ])
        build_episodes(c)
        holders = {r[0] for r in c.execute("SELECT DISTINCT holder FROM fact_inst_episode").fetchall()}
        assert holders == {"基金一号"}, f"B/H 行必须被滤 (修前混入): {holders}"
        ep = c.execute("SELECT status, realized_c1 FROM fact_inst_episode").fetchone()
        assert ep[0] == "closed" and ep[1] == pytest.approx(100 * (20.0 - 10.0))
    finally:
        c.close()


def test_build_episodes_dedups_source_duplicate_keys():
    """源 (holder, stock, report_date, is_exit_row) 双行 (实测 60 组) → QUALIFY 稳定序取 1 行;
    修前双行双计 → 修2c 语义下第二行还会 supersede 出 2 个 episode。"""
    c = _sql_conn()
    try:
        c.execute("INSERT INTO sm.dim_holder_name_tag VALUES "
                  "('章建平','niusan','20260820','no_evidence_either_way','name_only_untrusted')")
        c.executemany(_HOLDER_INSERT, [
            # 同一持有人的重复行必须给**同一个 holder_code** —— 去重的前提就是它们是同一身份。
            # (2026-09-08 补这两列时先按 rank 给了 C5/C6, 于是两行成了两个身份、去不掉重,
            #  测试当场抓住。这正是它该抓的: 身份键错了, 去重就失效。)
            ("600000", "20240331", "free", 5, 1, "基金二号", 1.0, "20240430", False, "基金二号", "A", 100, "新进", None, "基金", "C5", True),
            ("600000", "20240331", "free", 6, 1, "基金二号", 1.0, "20240430", False, "基金二号", "A", 999, "新进", None, "基金", "C5", True),
        ])
        build_episodes(c)
        rows = c.execute("SELECT status, shares FROM fact_inst_episode WHERE holder = '基金二号'").fetchall()
        assert len(rows) == 1, f"重复键必须只计一次 (修前 2 个 episode): {rows}"
        assert rows[0][0] == "holding"
        assert rows[0][1] == pytest.approx(100.0), "稳定序: holder_rank 最小的主行胜出"
    finally:
        c.close()


# ── 2026-09-12 业主裁定: holder_display 必须确定性选择, 不许 ANY_VALUE 任选变体 ──
#
# 根因实测: 对 institution_profile 做一次全量重建 (源数据未变), mart_inst_profile
# 行数/identity_key 体系完全不变, 但 holder 列取值集合变了 (320 个名字消失/315 个
# 新出现) —— DuckDB 的 any_value(holder_name_norm) 不保证跨次运行稳定。
# 判据: 同一 identity_key 下按该名字在源表里的出现行数降序, 行数相同按名字字典序升序,
# 取第一个。下面两条用例的 fixture 故意让"该赢的名字"既不是插入顺序里第一行也不是
# 最后一行, 使"扫描顺序碰运气"式实现 (ANY_VALUE 的典型退化) 拿不到正确答案。
#
# 断言读 fact_inst_episode (产出) 而不是 _ep_identity (2026-09-18 cut_lineage_drift §2.4:
# _ep_identity 改成了 CREATE OR REPLACE TEMP TABLE, 随连接关闭即消失——断言该断产出,
# 不该断一张连名字都带着"内部草稿"记号的中间表)。fact_inst_episode.holder 即
# _ep_identity.holder_display 的最终落点 (build_episodes 里 `i.holder_display AS holder`),
# n_name_variants 原样带出。同一 identity_key 下每行的这两列必须完全一致, 用
# _episode_holder_and_variants 顺带校验这条 (不是隐藏假设)。


def _episode_holder_and_variants(conn, identity_key: str) -> tuple:
    """同一 identity_key 下 fact_inst_episode 的 (holder, n_name_variants) 必须唯一
    (它们由 _ep_identity 按 identity_key 分组算出, 组内单值) —— 不唯一本身就是 bug。"""
    rows = {
        tuple(r) for r in conn.execute(
            "SELECT holder, n_name_variants FROM fact_inst_episode WHERE identity_key = ?",
            [identity_key],
        ).fetchall()
    }
    assert len(rows) == 1, (
        f"identity_key={identity_key!r} 的 (holder, n_name_variants) 在同批 episode 间不一致: {rows}"
    )
    return next(iter(rows))


def test_holder_display_deterministic_by_occurrence_count_then_name():
    """出现行数更多的写法必须赢, 且与插入顺序无关: "Fund-Zeta" 出现 3 次但后插入,
    "Fund-Alpha" 只出现 1 次且先插入 —— 若只看插入顺序或字母序会错选 Fund-Alpha。
    同一份源数据重建两遍, holder_display 必须逐位相同 (不许因扫描顺序不同而漂移)。
    """
    c = _sql_conn()
    try:
        c.execute("INSERT INTO sm.dim_holder_name_tag VALUES "
                  "('章建平','niusan','20260820','no_evidence_either_way','name_only_untrusted')")
        c.executemany(_HOLDER_INSERT, [
            ("600000", "20240331", "free", 1, 1, "Fund-Alpha", 1.0, "20240430", False,
             "Fund-Alpha", "A", 100, "新进", None, "基金", "C9", True),
            ("600000", "20240331", "free", 2, 1, "Fund-Zeta", 1.0, "20240430", False,
             "Fund-Zeta", "A", 100, "新进", None, "基金", "C9", True),
            ("600000", "20240331", "free", 2, 2, "Fund-Zeta", 1.0, "20240430", False,
             "Fund-Zeta", "A", 100, "新进", None, "基金", "C9", True),
            ("600000", "20240331", "free", 2, 3, "Fund-Zeta", 1.0, "20240430", False,
             "Fund-Zeta", "A", 100, "新进", None, "基金", "C9", True),
        ])
        build_episodes(c)
        row1 = _episode_holder_and_variants(c, "code:C9")
        assert row1 == ("Fund-Zeta", 2), (
            f"应选出现行数最多的写法 (Fund-Zeta ×3 > Fund-Alpha ×1), 实得 {row1}"
        )

        # 重建第二遍 (同一份源数据不变): 结果必须逐位相同, 不许漂移。
        build_episodes(c)
        row2 = _episode_holder_and_variants(c, "code:C9")
        assert row2 == row1, f"同一份数据两次重建 holder_display 必须一致, 第二遍得 {row2}"
    finally:
        c.close()


def test_holder_display_tie_break_alphabetical():
    """出现行数相同时按名字字典序升序取第一个。用真实 fixture 双向验证正确结果
    (两个名字各出现 2 次, 正确答案是字典序更小的 "Fund-One", 与哪个先插入无关)。
    """
    c = _sql_conn()
    try:
        c.execute("INSERT INTO sm.dim_holder_name_tag VALUES "
                  "('章建平','niusan','20260820','no_evidence_either_way','name_only_untrusted')")
        c.executemany(_HOLDER_INSERT, [
            ("600000", "20240331", "free", 1, 1, "Fund-Two", 1.0, "20240430", False,
             "Fund-Two", "A", 100, "新进", None, "基金", "C10", True),
            ("600000", "20240331", "free", 1, 2, "Fund-Two", 1.0, "20240430", False,
             "Fund-Two", "A", 100, "新进", None, "基金", "C10", True),
            ("600000", "20240331", "free", 2, 1, "Fund-One", 1.0, "20240430", False,
             "Fund-One", "A", 100, "新进", None, "基金", "C10", True),
            ("600000", "20240331", "free", 2, 2, "Fund-One", 1.0, "20240430", False,
             "Fund-One", "A", 100, "新进", None, "基金", "C10", True),
        ])
        build_episodes(c)
        row = _episode_holder_and_variants(c, "code:C10")
        assert row == ("Fund-One", 2), f"行数相同(各2次)时字典序更小的 Fund-One 应赢, 实得 {row}"
    finally:
        c.close()


# ── H2 (cut_lineage_drift §2.4): _ep_* 不许在物理库里永久隐身 ──────────────────


def test_no_persistent_ep_prefixed_tables_after_build_episodes():
    """跑完一次 build_episodes 后, 库里不能有任何 `_ep_` 开头的**持久**表 (temporary=False)。

    这几张是 build_episodes 内部的一次性草稿表 (_ep_capital_role/_ep_raw/_ep_niusan/
    _ep_identity), 全部改成 CREATE OR REPLACE TEMP TABLE ——TEMP 表只在创建它的连接里
    可见、随连接关闭消失; institution_profile.rebuild_all() 每次都新开连接、用完即关,
    所以物理文件里永远不该留下它们的持久副本。用 duckdb_tables().temporary 而不是
    "表存在与否"来判, 因为同一连接里查 information_schema/duckdb_tables() 本来就能看到
    自己建的 TEMP 表 (这是正常的, 不是没生效)——真正该判的是"是不是持久的"。
    """
    c = _sql_conn()
    try:
        c.execute("INSERT INTO sm.dim_holder_name_tag VALUES "
                  "('章建平','niusan','20260820','no_evidence_either_way','name_only_untrusted')")
        c.executemany(_HOLDER_INSERT, [
            ("600000", "20240331", "free", 1, 1, "Fund-One", 1.0, "20240430", False,
             "Fund-One", "A", 100, "新进", None, "基金", "C11", True),
        ])
        build_episodes(c)
        persistent_ep_tables = [
            r[0] for r in c.execute(
                "SELECT table_name FROM duckdb_tables() "
                "WHERE table_name LIKE '\\_ep\\_%' ESCAPE '\\' AND NOT temporary"
            ).fetchall()
        ]
        assert persistent_ep_tables == [], (
            f"发现持久 (非 TEMP) 的 _ep_ 表: {persistent_ep_tables} —— institution_profile "
            "的 _ep_* 草稿表必须是 CREATE OR REPLACE TEMP TABLE"
        )
    finally:
        c.close()


def test_holder_display_tie_break_rule_present_in_sql():
    """去掉 tie-break 规则 (ORDER BY 只留 n DESC, 不按字典序) 这条变异单独在这条用例
    上变红 —— 2026-09-12 实测: 并列名次在无 tie-break 时靠 DuckDB 内部执行计划决定
    谁排第一, 同一对名字换一种查询复杂度就可能翻转赢家 (不可靠, 不能作为behavior 判据);
    真正能钉住"这条规则确实存在"的是 SQL 结构本身必须同时按 n DESC 与
    holder_name_norm ASC 排序, 缺了字典序 tie-break 这条正则就找不到它。
    """
    src = inspect.getsource(build_episodes)
    assert re.search(
        r"ORDER BY\s+n\s+DESC\s*,\s*holder_name_norm\s+ASC", src
    ), (
        "_ep_identity 的 best_name 排序必须显式写 'n DESC, holder_name_norm ASC' "
        "(出现行数降序 + 名字字典序升序 tie-break), 不能只有 n DESC"
    )


def test_holder_display_feeds_deterministically_into_profile_marts():
    """检查 fact_inst_episode.holder 与 mart_inst_profile/mart_inst_profile_dim 是否
    走同一条选名逻辑 (2026-09-12 要求 3): 三张表对同一 identity_key 的 holder 必须
    完全一致 —— mart_inst_profile(_dim) 的 ANY_VALUE(holder) 不是重复实现一遍"选变体",
    而是在读一个已经确定性选好、组内单值的列, 单一计算点仍只有 _ep_identity 一处。
    """
    c = _sql_conn()
    try:
        c.execute("INSERT INTO sm.dim_holder_name_tag VALUES "
                  "('章建平','niusan','20260820','no_evidence_either_way','name_only_untrusted')")
        c.executemany(_HOLDER_INSERT, [
            ("600000", "20240331", "free", 1, 1, "Fund-Alpha", 1.0, "20240430", False,
             "Fund-Alpha", "A", 100, "新进", None, "基金", "C9", True),
            ("600000", "20240331", "free", 2, 1, "Fund-Zeta", 1.0, "20240430", False,
             "Fund-Zeta", "A", 100, "新进", None, "基金", "C9", True),
            ("600000", "20240331", "free", 2, 2, "Fund-Zeta", 1.0, "20240430", False,
             "Fund-Zeta", "A", 100, "新进", None, "基金", "C9", True),
        ])
        build_episodes(c)
        build_profiles(c)
        ep_holder = [tuple(r) for r in c.execute(
            "SELECT DISTINCT holder FROM fact_inst_episode WHERE identity_key = 'code:C9'"
        ).fetchall()]
        assert ep_holder == [("Fund-Zeta",)]
        prof_holder = tuple(c.execute(
            "SELECT holder FROM mart_inst_profile WHERE identity_key = 'code:C9'"
        ).fetchone())
        assert prof_holder == ("Fund-Zeta",)
        dim_holders = {
            r[0] for r in c.execute(
                "SELECT DISTINCT holder FROM mart_inst_profile_dim WHERE identity_key = 'code:C9'"
            ).fetchall()
        }
        assert dim_holders in (set(), {"Fund-Zeta"}), (
            "mart_inst_profile_dim 若有该 identity_key 的行, holder 必须与 fact_inst_episode 一致"
        )
    finally:
        c.close()


# ── 2026-07-23 coverage lift: display profile for every episode holder ─────────


def test_build_profiles_includes_holding_only_and_keeps_metrics_null():
    """holding-only / passive / thin holders get display rows; alpha stays NULL."""
    c = duck_mem()
    try:
        c.execute("""
            CREATE TABLE fact_inst_episode (
                holder VARCHAR, stock VARCHAR, holder_type VARCHAR,
                open_date VARCHAR, close_date VARCHAR, status VARCHAR,
                seeded BOOLEAN, is_passive BOOLEAN,
                ret_c1 DOUBLE, alpha_c1 DOUBLE, sw_l1_at_open VARCHAR,
                -- 2026-09-08 Step 4: 档案按 identity_key 聚而非按显示名 ——
                -- 同名不同身份按名字聚会被合成一个档案, 正是换键要消灭的。
                identity_key VARCHAR, identity_kind VARCHAR, identity_grade VARCHAR
            )
        """)
        c.executemany(
            "INSERT INTO fact_inst_episode "
            "(holder, stock, holder_type, open_date, close_date, status, seeded, "
            " is_passive, ret_c1, alpha_c1, sw_l1_at_open, identity_key) "
            # identity_key := holder: 本用例测的是"每个 holder 出一行档案", 身份分层不是
            # 它的被测对象; 用 holder 当 key 保持它原来的语义, 不引入无关的分桶。
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                # rankable closed ×11 → ranked
                *[
                    ("牛散A", f"60000{i}", "个人", "20200101", "20200630",
                     "closed", False, False, 0.1, 0.05, "银行", "牛散A")
                    for i in range(11)
                ],
                # holding only → display, metrics NULL
                ("持有中B", "600100", "基金", "20240101", None,
                 "holding", False, False, None, None, "煤炭", "持有中B"),
                # passive only → display, metrics_status=passive_product
                ("被动ETF", "600200", "基金", "20240101", "20240630",
                 "closed", False, True, 0.2, 0.1, "电子", "被动ETF"),
                # empty name → dropped
                ("", "600300", "个人", "20240101", None,
                 "holding", False, False, None, None, None, ""),
                (None, "600301", "个人", "20240101", None,
                 "holding", False, False, None, None, None, None),
                # seeded closed only → no_closed_alpha (not rankable)
                ("种子C", "600400", "个人", "20200101", "20200630",
                 "closed", True, False, 0.3, 0.2, "医药", "种子C"),
            ],
        )
        out = build_profiles(c)
        assert out["profiles"] == 4  # A/B/ETF/C — empty dropped
        rows = {
            r[0]: r
            for r in c.execute(
                "SELECT holder, n_closed, median_alpha, low_sample, "
                "n_episodes, is_passive_holder, metrics_status "
                "FROM mart_inst_profile"
            ).fetchall()
        }
        assert set(rows) == {"牛散A", "持有中B", "被动ETF", "种子C"}
        assert rows["牛散A"][1] == 11 and rows["牛散A"][2] == pytest.approx(0.05)
        assert rows["牛散A"][3] is False and rows["牛散A"][6] == "ranked"
        assert rows["持有中B"][1] == 0 and rows["持有中B"][2] is None
        assert rows["持有中B"][3] is True and rows["持有中B"][6] == "holding_only"
        assert rows["被动ETF"][1] == 0 and rows["被动ETF"][2] is None
        assert rows["被动ETF"][5] is True and rows["被动ETF"][6] == "passive_product"
        assert rows["种子C"][1] == 0 and rows["种子C"][2] is None
        assert rows["种子C"][6] == "no_closed_alpha"
        # dims stay rankable-only (牛散A only)
        dim_holders = {
            r[0]
            for r in c.execute("SELECT DISTINCT holder FROM mart_inst_profile_dim").fetchall()
        }
        assert dim_holders == {"牛散A"}
    finally:
        c.close()
