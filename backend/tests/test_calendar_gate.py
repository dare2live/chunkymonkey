"""Phase ψ.5 根因修复 — calendar gate 单测 + AST lint.

两个用途:
  1. 行为: 验证 services.utils.latest_completed_trade_date 在盘中/收盘后/非交易日的语义.
  2. 静态: 防 K 线 / 信号 sync 退回 wall-clock now() 当 end_date.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from services.duck_adapter import connect
from services.utils import latest_completed_trade_date


REPO_ROOT = Path(__file__).resolve().parents[2]


# ===================== 行为 =====================

@pytest.fixture()
def cal_conn():
    """In-memory DuckDB with a minimal dim_trading_calendar."""
    conn = connect(":memory:")
    conn.execute(
        """CREATE TABLE dim_trading_calendar (
              trade_date VARCHAR PRIMARY KEY,
              is_trading BIGINT
        )"""
    )
    rows = [
        ("2026-05-08", 1),  # Fri
        ("2026-05-09", 0),  # Sat
        ("2026-05-10", 0),  # Sun
        ("2026-05-11", 1),  # Mon
        ("2026-05-12", 1),  # Tue
        ("2026-05-13", 1),  # Wed (target "today")
        ("2026-05-14", 1),  # Thu
    ]
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, ?)", rows)
    yield conn
    conn.close()


# UTC+8 for tests (utils uses _MARKET_TZ = Asia/Shanghai)
_TZ = timezone(timedelta(hours=8))


def test_midsession_today_returns_previous(cal_conn):
    """盘中 (今天交易日 + < 16:00 默认 cutoff) → 返回昨日."""
    now = datetime(2026, 5, 13, 14, 30, tzinfo=_TZ)
    assert latest_completed_trade_date(cal_conn, now=now) == "2026-05-12"


def test_after_close_returns_today(cal_conn):
    """收盘后 (今天交易日 + >= 16:00) → 返回今天."""
    now = datetime(2026, 5, 13, 16, 0, tzinfo=_TZ)
    assert latest_completed_trade_date(cal_conn, now=now) == "2026-05-13"


def test_weekend_returns_friday(cal_conn):
    """周六 / 周日 (非交易日) → 返回上周五."""
    assert latest_completed_trade_date(cal_conn, now=datetime(2026, 5, 9, 11, 0, tzinfo=_TZ)) == "2026-05-08"
    assert latest_completed_trade_date(cal_conn, now=datetime(2026, 5, 10, 23, 59, tzinfo=_TZ)) == "2026-05-08"


def test_just_before_cutoff_returns_prev(cal_conn):
    """15:59 < 16:00 → 盘中, 返回昨日."""
    now = datetime(2026, 5, 13, 15, 59, tzinfo=_TZ)
    assert latest_completed_trade_date(cal_conn, now=now) == "2026-05-12"


def test_custom_close_hour_15_30_equivalent(cal_conn):
    """自定义 close_hour=15 (15:00 即视为已收盘): 14:30 仍盘中, 15:00 已收盘."""
    now = datetime(2026, 5, 13, 14, 30, tzinfo=_TZ)
    assert latest_completed_trade_date(cal_conn, now=now, close_hour=15) == "2026-05-12"
    now = datetime(2026, 5, 13, 15, 0, tzinfo=_TZ)
    assert latest_completed_trade_date(cal_conn, now=now, close_hour=15) == "2026-05-13"


# ===================== AST lint =====================

def _gather_scan_paths() -> list:
    """Phase ψ.5: 扩 lint 覆盖整个 services + scripts + routers, 而不是固定 5 个文件.

    rglob 动态收集. 任何新加进去的 .py 自动入扫. 排除 __pycache__ + 测试自身.
    """
    base_dirs = [
        REPO_ROOT / "backend" / "services",
        REPO_ROOT / "backend" / "scripts",
        REPO_ROOT / "backend" / "routers",
    ]
    out = []
    for d in base_dirs:
        for p in sorted(d.rglob("*.py")):
            if "__pycache__" in str(p):
                continue
            out.append(p)
    return out


SCAN_PATHS = _gather_scan_paths()


def _is_wall_clock_date_string(node: ast.AST) -> bool:
    """检测 `datetime.now().strftime("%Y-%m-%d")` / `_date.today().isoformat()` 这类表达式."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "strftime":
        if not (node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and ("%Y%m%d" in node.args[0].value or "%Y-%m-%d" in node.args[0].value)):
            return False
        inner = func.value
        if not isinstance(inner, ast.Call):
            return False
        inner_func = inner.func
        if isinstance(inner_func, ast.Attribute):
            return inner_func.attr in {"now", "utcnow", "today"}
        if isinstance(inner_func, ast.Name):
            return inner_func.id in {"now", "utcnow", "today"}
        return False
    if func.attr == "isoformat":
        inner = func.value
        if not isinstance(inner, ast.Call):
            return False
        inner_func = inner.func
        return isinstance(inner_func, ast.Attribute) and inner_func.attr == "today"
    return False


def _find_violations(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if _is_wall_clock_date_string(node):
            line = getattr(node, "lineno", -1)
            snippet = src.splitlines()[line - 1].strip() if line > 0 else "?"
            # allowlist: runtime / log 时间戳, manifest run_id, batch_id, model_id 等
            # — 这些都是 wall-clock 合法用途 (不是 trade_date 写入).
            if any(tok in snippet for tok in (
                "started_at", "finished_at", "built_at",
                "heartbeat", "manifest", "run_id", "--run-id",
                "isoformat(timespec=", "updated_at",
                "profiled_at", "log.info(f", "logger.info(f",
                "batch_id", "model_id", "stamp =", "f\"hs300_benchmark_",
                "test_kline_availability", "snapshot_lag",
                # 唯一 identifier (Optuna study / paper_sim comparison / model 时间戳):
                "study_name", "comparison_id", "model_date",
                # snapshot/ingest 类用 wall-clock 标 ingest 时间合理 (不是 trade_date):
                "snapshot_date", "source_available_date",
                # 健康检查 / audit 用 wall-clock 看物理时间是合理的:
                "_days_lag(", "fetched_at <=",
                # 显式 Phase ψ.5 allowlist 注释:
                "Phase ψ.5 allowlist",
            )):
                continue
            violations.append((line, snippet))
    return violations


@pytest.mark.parametrize("path", SCAN_PATHS, ids=lambda p: p.name)
def test_no_wall_clock_as_end_date(path: Path):
    """K 线 / 信号 sync 入口禁止用 wall-clock now/today 当 end_date —
    必须走 services.utils.latest_completed_trade_date."""
    assert path.exists(), f"scan target missing: {path}"
    violations = _find_violations(path)
    if violations:
        lines = "\n".join(f"  L{ln}: {snip}" for ln, snip in violations)
        pytest.fail(
            f"{path.name}: 发现 wall-clock 当 end_date 用法 ({len(violations)} 处), "
            f"应改用 services.utils.latest_completed_trade_date:\n{lines}"
        )
