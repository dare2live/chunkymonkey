"""LHB 退出实验 — 预注册一致性 + 合成 fixture 端到端 (数学与排除口径回归)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "experiment_lhb_exit.py"
VENV_PY = str(REPO / ".venv" / "bin" / "python")  # subprocess 必须用 venv 解释器 (homebrew python 无 duckdb)
# venv 是 --system-site-packages, duckdb 实际在 ~/Library/Python (user site), 解析依赖真实 HOME —
# 覆盖 HOME 会切断 user-site → ModuleNotFoundError。env 必须继承 os.environ。
SUB_ENV = {**os.environ, "PYTHONPATH": str(REPO / "backend")}


def test_prereg_consistency_machine_check():
    # 验收项: 脚本常量与冻结 prereg 文档逐字一致 (改任一边必须同步另一边)
    r = subprocess.run([VENV_PY, str(SCRIPT), "--check-prereg"],
                       capture_output=True, text=True, env=SUB_ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def _mk_fixture(db_path: Path):
    """合成 35 交易日 x 10 股: 事件股上榜后 20 日跌 10% (退出占优), 同分位对照横盘.

    ntile(5) 对 10 行每桶 2 只 — 事件股 (mv 1.00e9) 与对照 000601.SZ (mv 1.01e9) 同落 q3,
    且对照事件日涨幅 9.5 在 ±1pp 带内; 其余 8 只涨幅 0 出带。3 只股票的旧 fixture 每股
    独占一个分位, 物理上永远 0 对照 (除零回归的根因)。
    """
    con = duckdb.connect(str(db_path))
    days = [f"202001{d:02d}" for d in range(2, 28)] + [f"202002{d:02d}" for d in range(1, 10)]
    con.execute("CREATE TABLE raw_tushare_trade_cal (exchange VARCHAR, cal_date VARCHAR, is_open VARCHAR)")
    for d in days:
        con.execute("INSERT INTO raw_tushare_trade_cal VALUES ('SSE', ?, '1')", [d])
    con.execute("CREATE TABLE raw_tushare_top_list (trade_date VARCHAR, ts_code VARCHAR, pct_change DOUBLE, float_values DOUBLE, reason VARCHAR, net_amount DOUBLE)")
    con.execute("INSERT INTO raw_tushare_top_list VALUES ('20200103','000600.SZ', 9.9, 1e9, 'x', 1.0)")
    con.execute("CREATE TABLE raw_tushare_daily (trade_date VARCHAR, ts_code VARCHAR, open DOUBLE, pct_chg DOUBLE)")
    con.execute("CREATE TABLE raw_tushare_adj_factor (trade_date VARCHAR, ts_code VARCHAR, adj_factor DOUBLE)")
    con.execute("CREATE TABLE raw_tushare_daily_basic (trade_date VARCHAR, ts_code VARCHAR, circ_mv DOUBLE)")
    # (code, 横盘 open, 事件日 pct, circ_mv) — mv 升序 ntile(5): q1={602,603} q2={604,605}
    # q3={事件 1.00e9, 对照 1.01e9} q4={606,607} q5={608,609}
    others = [("000602.SZ", 0.2e9), ("000603.SZ", 0.4e9), ("000604.SZ", 0.6e9),
              ("000605.SZ", 0.8e9), ("000606.SZ", 1.2e9), ("000607.SZ", 1.4e9),
              ("000608.SZ", 1.6e9), ("000609.SZ", 1.8e9)]
    for i, d in enumerate(days):
        # 事件股: 事件日后线性下跌 (t+1 open=100, t+21 open=90 → exit_gain=+10pp)
        e_open = 100.0 if i <= 2 else max(90.0, 100.0 - (i - 2) * 0.5)
        rows = [("000600.SZ", e_open, 9.9 if d == "20200103" else 0.0, 1.00e9),
                ("000601.SZ", 50.0, 9.5 if d == "20200103" else 0.0, 1.01e9)]
        rows += [(c, 30.0, 0.0, mv) for c, mv in others]
        for code, op, pct, mv in rows:
            con.execute("INSERT INTO raw_tushare_daily VALUES (?,?,?,?)", [d, code, op, pct])
            con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,1.0)", [d, code])
            con.execute("INSERT INTO raw_tushare_daily_basic VALUES (?,?,?)", [d, code, mv])
    con.close()


def test_end_to_end_synthetic_verdict(tmp_path):
    db = tmp_path / "toy.duckdb"
    _mk_fixture(db)
    r = subprocess.run(
        [VENV_PY, str(SCRIPT), "--skip-gate", "--db", str(db), "--out-dir", str(tmp_path)],
        capture_output=True, text=True, env=SUB_ENV, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    payload = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
    assert payload["n_events_judged"] == 1
    # E 从 t+1 open 100 → t+21 open 90: exit_gain = +10pp; 对照横盘 0 → net ≈ +10pp
    assert payload["J1"]["net_pp"] == pytest.approx(10.0, abs=0.6)
    # 单事件单年: J2 1/7 必不过 → 合成 fixture 验证判负路径物理可达 (可红性)
    assert payload["verdict"] == "REJECT"
    assert payload["J2"]["pass"] is False
