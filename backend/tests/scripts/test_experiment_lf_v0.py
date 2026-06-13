"""LF V0 实验 — 预注册一致性 + 合成 fixture 端到端 (数学/去连发/盲点6/PIT 双锚回归)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "experiment_lf_v0.py"
VENV_PY = str(REPO / ".venv" / "bin" / "python")  # subprocess 必须用 venv 解释器 (homebrew python 无 duckdb)
# venv 是 --system-site-packages, duckdb 在 ~/Library/Python user-site, 解析依赖真实 HOME —
# 覆盖 HOME 切断 user-site → ModuleNotFoundError (LHB 同款反例)。env 必须继承 os.environ。
SUB_ENV = {**os.environ, "PYTHONPATH": str(REPO / "backend")}


def test_prereg_consistency_machine_check():
    # 验收项: 脚本常量与冻结 prereg 文档逐字一致 (改任一边必须同步另一边)
    r = subprocess.run([VENV_PY, str(SCRIPT), "--check-prereg"],
                       capture_output=True, text=True, env=SUB_ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def _mk_fixture(db_path: Path):
    """合成 35 交易日: 概念 BK0001.DC 于 20250106 出 2 涨停龙头 (L1/L2), follower F1
    随后 5 日 +5% (传导为真), 带内对照 C1/C2 横盘 → excess 应 = +5.0pp 整
    (双边成本对 follower 与对照同扣, 差分约掉)。membership 全期不变 → PIT 双锚留存 1.0。
    """
    con = duckdb.connect(str(db_path))
    days = [f"202501{d:02d}" for d in range(2, 28)] + [f"202502{d:02d}" for d in range(1, 10)]
    con.execute("CREATE TABLE raw_tushare_trade_cal (exchange VARCHAR, cal_date VARCHAR, is_open VARCHAR)")
    for d in days:
        con.execute("INSERT INTO raw_tushare_trade_cal VALUES ('SSE', ?, '1')", [d])
    con.execute("CREATE TABLE raw_tushare_dc_member (trade_date VARCHAR, ts_code VARCHAR, con_code VARCHAR, name VARCHAR)")
    con.execute('CREATE TABLE raw_tushare_limit_list_d (trade_date VARCHAR, ts_code VARCHAR, "limit" VARCHAR, pct_chg DOUBLE)')
    con.execute("INSERT INTO raw_tushare_limit_list_d VALUES ('20250106','000001.SZ','U',10.0), ('20250106','000002.SZ','U',10.0)")
    # 连发: 同概念 20250108 再触发 (距 20250106 两个交易日, 在 [t-4,t-1] 冷却窗内) → 必须被去连发吞掉
    con.execute("INSERT INTO raw_tushare_limit_list_d VALUES ('20250108','000001.SZ','U',10.0), ('20250108','000002.SZ','U',10.0)")
    con.execute("CREATE TABLE raw_tushare_daily (trade_date VARCHAR, ts_code VARCHAR, open DOUBLE, pct_chg DOUBLE)")
    con.execute("CREATE TABLE raw_tushare_adj_factor (trade_date VARCHAR, ts_code VARCHAR, adj_factor DOUBLE)")
    con.execute("CREATE TABLE raw_tushare_stk_limit (trade_date VARCHAR, ts_code VARCHAR, up_limit DOUBLE)")
    # 概念成员: L1/L2 (龙头) + F1 (follower) + F2 (t+1 一字不可成交) 全期不变; C1/C2/D1 非成员
    stocks = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000010.SZ", "000011.SZ", "000012.SZ"]
    event_pct = {"000001.SZ": 10.0, "000002.SZ": 10.0, "000003.SZ": 3.0, "000004.SZ": 3.0,
                 "000010.SZ": 2.8, "000011.SZ": 3.2, "000012.SZ": 9.0}  # D1 出带
    rising = {"000003.SZ", "000012.SZ"}  # D1 也上涨: 若带匹配失效漏进对照, net 会偏离 5.0 被断言抓到
    for i, d in enumerate(days):
        for m in ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"):
            con.execute("INSERT INTO raw_tushare_dc_member VALUES (?,?,?,'x')", [d, "BK0001.DC", m])
        for code in stocks:
            if code in rising:
                op = 100.0 + min(5.0, max(0.0, float(i - 5)))  # t+1(idx5)=100 → t+6(idx10)=105
            elif code == "000004.SZ":
                op = 100.0
            else:
                op = 50.0
            pct = event_pct[code] if d == "20250106" else 0.0
            # 盲点6: F2 在 t+1 (20250107) up_limit == open → 一字, 必须被排除
            ul = 100.0 if (code == "000004.SZ" and d == "20250107") else 200.0
            con.execute("INSERT INTO raw_tushare_daily VALUES (?,?,?,?)", [d, code, op, pct])
            con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,1.0)", [d, code])
            con.execute("INSERT INTO raw_tushare_stk_limit VALUES (?,?,?)", [d, code, ul])
    con.close()


def test_end_to_end_synthetic_verdict(tmp_path):
    db = tmp_path / "toy.duckdb"
    _mk_fixture(db)
    r = subprocess.run(
        [VENV_PY, str(SCRIPT), "--skip-gate", "--db", str(db),
         "--smart-db", str(tmp_path / "absent.duckdb"), "--out-dir", str(tmp_path)],
        capture_output=True, text=True, env=SUB_ENV, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    payload = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
    # 20250108 连发被去连发吞掉 → 仍 1 事件; F2 一字被盲点6过滤 → 仍 1 follower
    assert payload["n_events"] == 1
    assert payload["n_follower_samples"] == 1
    assert payload["excluded"]["no_t1_tradable"] >= 1
    # F1 fwd5 = +5pp - cost; 对照横盘 = 0 - cost; 差分成本约掉 → excess = +5.0pp 整
    assert payload["J1"]["net_pp"] == pytest.approx(5.0, abs=0.01)
    # membership 全期不变 → t-1 锚同样本 → 留存 1.0
    assert payload["PIT_dual_anchor"]["retention"] == pytest.approx(1.0, abs=0.01)
    # 单事件 < 30 → J3 不足 → INCONCLUSIVE (非判负, prereg 原文), 判负路径由 J3 钉死可达
    assert payload["verdict"] == "INCONCLUSIVE"
    assert payload["J3"]["pass"] is False


def test_band_controls_equals_naive_differential():
    """两指针对照选择 (_band_controls) 与朴素全带排序 (_controls_naive) 逐字等价.

    红线 (2026-06-13 性能根治): 替排序的两指针 tie-break (同 |diff| 跨边界按 code /
    同 pct 组内 code 序) 易错 = '测试绿但判决偏'。随机面板 1000 case 差分, 任一不等即红。
    """
    import importlib.util
    import random as _r
    from pathlib import Path as _P
    spec = importlib.util.spec_from_file_location("exp_lf", _P(__file__).resolve().parents[2] / "scripts" / "experiment_lf_v0.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

    rng = _r.Random(424242)
    mismatches = 0
    for case in range(1000):
        # 随机当日面板: 30-200 股, pct 含大量重复 (制造同 |diff| 与同 pct 组 tie)
        n = rng.randint(30, 200)
        codes = [f"{rng.randint(1, 999999):06d}.SZ" for _ in range(n)]
        codes = list(dict.fromkeys(codes))  # 去重 code
        # pct 从小集合抽 → 强制大量同 pct / 对称 pct
        pcts = {c: rng.choice([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, round(rng.uniform(-3, 3), 1)]) for c in codes}
        by_day = {"D": pcts}
        members = set(rng.sample(codes, k=min(len(codes), rng.randint(0, 5))))
        # fwd5: ~15% 无效 (None) 制造 take 需跳过的情形
        fwd = {c: (None if rng.random() < 0.15 else round(rng.uniform(-10, 10), 4)) for c in codes}
        fwd5_fn = lambda c: fwd.get(c)
        follower_pct = rng.choice(list(pcts.values()))

        group_pcts, groups = m._day_groups(by_day, "D", {})
        fast = m._band_controls(group_pcts, groups, follower_pct, members, m.N_CONTROLS, fwd5_fn)
        naive = m._controls_naive(by_day, "D", follower_pct, members, fwd5_fn, m.N_CONTROLS)
        if fast != naive:
            mismatches += 1
            if mismatches <= 3:
                print(f"case {case}: follower_pct={follower_pct} fast={fast} naive={naive}")
    assert mismatches == 0, f"{mismatches}/1000 差分不等 — 两指针 tie-break 偏离朴素"
