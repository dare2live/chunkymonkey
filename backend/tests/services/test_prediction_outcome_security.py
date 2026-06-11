"""安全防回退测试 — security 修复组 (2026-06-11 体检).

覆盖三处 HIGH/MEDIUM 安全问题:
1. prediction_outcome.model_performance_summary: model_id f-string 拼 SQL = 注入面
   → 参数化 execute(sql, params) + 白名单校验.
2. main.py CORS: allow_origins=["*"] + 写端点零鉴权 → 默认收敛到本机 origin.
3. start.command: uvicorn --host 0.0.0.0 默认暴露写接口到局域网 → 默认 127.0.0.1.

红线: 这些测试是 red→green 防回退. 删/改默认值前必须先看本文件。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import duck_mem
from services.prediction_outcome import (
    _is_safe_model_id,
    ensure_table,
    model_performance_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 1. SQL 注入 — 参数化 + 白名单
# ---------------------------------------------------------------------------

def _seed(conn) -> None:
    ensure_table(conn)
    # 两个模型各一行 outcome (snapshot_date 用今天附近, lookback 默认 90 天能覆盖)
    from datetime import date
    today = date.today().isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO mart_prediction_outcome
           (snapshot_date, stock_code, model_id, rank_in_date, pred_score,
            entry_price, ret_5d, ret_10d, ret_30d, hit_5d, hit_30d)
           VALUES
           (?, '600000', 'lambdamart_v3.2', 1, 0.9, 10.0, 0.05, 0.08, 0.12, TRUE, TRUE),
           (?, '000001', 'ensemble_2026-05-01', 1, 0.8, 20.0, -0.02, 0.01, 0.03, FALSE, TRUE)
        """,
        [today, today],
    )
    conn.commit()


def test_injection_payload_does_not_break_sql_or_leak():
    """经典注入 payload 不能破 SQL, 也不能绕过 model_id 过滤拿到全部行.

    payload 试图闭合引号注出全表: ' OR '1'='1
    旧 f-string 版本会把 WHERE model_id = '' OR '1'='1' 拼进去 → 返回全部模型.
    修复后白名单先拒掉 (含空格/引号), 返回空 summaries, 绝不返回别的模型数据.
    """
    conn = duck_mem()
    _seed(conn)
    payload = "' OR '1'='1"
    result = model_performance_summary(conn, model_id=payload)
    assert result["summaries"] == [], "注入 payload 必须被白名单拒绝, 不能返回任何行"
    conn.close()


def test_drop_table_payload_is_inert():
    """DROP TABLE 注入 payload 不能执行 — 表必须仍在."""
    conn = duck_mem()
    _seed(conn)
    payload = "x'; DROP TABLE mart_prediction_outcome; --"
    res = model_performance_summary(conn, model_id=payload)
    assert res["summaries"] == []
    # 表还在 (没被 DROP) → 用合法查询验证仍可读
    ok = model_performance_summary(conn, model_id="lambdamart_v3.2")
    assert len(ok["summaries"]) == 1
    assert ok["summaries"][0]["model_id"] == "lambdamart_v3.2"
    conn.close()


def test_legitimate_model_id_filter_works():
    """合法 model_id 参数化查询正常返回该模型, 且只返回该模型."""
    conn = duck_mem()
    _seed(conn)
    res = model_performance_summary(conn, model_id="ensemble_2026-05-01")
    assert len(res["summaries"]) == 1
    assert res["summaries"][0]["model_id"] == "ensemble_2026-05-01"
    # 无 filter → 两个模型都在
    all_res = model_performance_summary(conn, model_id=None)
    assert {s["model_id"] for s in all_res["summaries"]} == {
        "lambdamart_v3.2",
        "ensemble_2026-05-01",
    }
    conn.close()


@pytest.mark.parametrize(
    "bad",
    [
        "' OR '1'='1",
        "a; DROP TABLE x",
        "a' --",
        "a OR 1=1",
        "a b",            # 空格
        "a\"b",           # 双引号
        "a%b",            # 通配符
        "",               # 空串
        "x" * 200,        # 超长
    ],
)
def test_whitelist_rejects_injection_chars(bad):
    assert _is_safe_model_id(bad) is False


@pytest.mark.parametrize(
    "good",
    ["lambdamart_v3.2", "ensemble_2026-05-01", "model-A_1", "abc", "A1"],
)
def test_whitelist_allows_legit_ids(good):
    assert _is_safe_model_id(good) is True


# ---------------------------------------------------------------------------
# 2. CORS 默认不再是通配符 *
# ---------------------------------------------------------------------------

def test_main_cors_not_wildcard_by_default():
    """main.py 不能再用 allow_origins=['*'] 硬编码 (CSRF 写接口风险)."""
    src = (REPO_ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    assert 'allow_origins=["*"]' not in src, "CORS 默认通配符已修复, 不可回退"
    assert "_resolve_cors_origins" in src


def test_cors_default_origins_are_loopback_only(monkeypatch):
    """默认 (无 CM_CORS_ORIGINS env) 解析出的 origin 只含本机 loopback."""
    import importlib
    monkeypatch.delenv("CM_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("CM_PORT", "8000")
    main = importlib.import_module("main")
    origins = main._resolve_cors_origins()
    assert origins == ["http://localhost:8000", "http://127.0.0.1:8000"]
    assert "*" not in origins


def test_cors_env_override(monkeypatch):
    """显式 CM_CORS_ORIGINS 可放开到指定 origin (逗号分隔)."""
    import importlib
    monkeypatch.setenv("CM_CORS_ORIGINS", "http://192.168.1.10:8000, http://host:9000")
    main = importlib.import_module("main")
    origins = main._resolve_cors_origins()
    assert origins == ["http://192.168.1.10:8000", "http://host:9000"]


# ---------------------------------------------------------------------------
# 3. start.command 默认绑 127.0.0.1, 不再硬编码 0.0.0.0
# ---------------------------------------------------------------------------

def test_start_command_does_not_hardcode_0_0_0_0():
    src = (REPO_ROOT / "start.command").read_text(encoding="utf-8")
    # uvicorn --host 行必须用 $HOST 变量, 不能硬编码 0.0.0.0
    assert "--host 0.0.0.0" not in src, "start.command 不能再硬编码 0.0.0.0"
    assert re.search(r'--host\s+"\$HOST"', src), "uvicorn 必须用 $HOST 变量"


def test_start_command_default_host_is_loopback():
    src = (REPO_ROOT / "start.command").read_text(encoding="utf-8")
    assert 'HOST="${CM_HOST:-127.0.0.1}"' in src, "默认 host 必须是 127.0.0.1"
