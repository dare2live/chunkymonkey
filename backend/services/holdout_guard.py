"""Holdout 纪律机械门 — master plan §2.1 立法 (D1 前置件, 2026-07-02)。

owner=backend/config/holdout_policy.yaml + analysis/master_implementation_plan_20260702.md §2.1。
为何存在 (过拟合死): "根据测试结果做优化迭代"若发生在 holdout (holdout_start→今) 数据上,
第二次使用起 holdout 退化为验证集, 最终数字系统性乐观。三个门:

  1. assert_holdout_untouched(data_end_date) — 训练/调参路径守门:
     data_end_date > holdout_start = raise (迭代只许在 train 窗内做, 防迭代烧 OOS)。
  2. register_criteria(experiment, criteria_text) — 触碰前预注册判据 (先写"什么算过"再看数字),
     写 experiment_store.holdout_touch_log (registered_at 落, touched_at=NULL 表示未消耗)。
  3. touch_holdout(experiment) — 消耗一次预算: 须有该 experiment 未消耗的预注册行 (否则 raise);
     预算是**全局**的 (跨 experiment 共享 — holdout 是同一份数据, 谁 touch 都在烧,
     per-experiment 预算可被换名绕过); 耗尽 = raise; 返回剩余预算。

freeze 规则 (最终参数须在最后一次 touch 前冻结) 当前为 config 文本约定 (holdout_policy.yaml
freeze_rule); 机械执法点 = 转正门 record_verdict prereg_hash, edge 重建时接线。

库连接模式参照 backend/scripts/build_experiment_store.py (manifest path + duck_adapter);
conn 参数注入供测试 (内存库, conftest.duck_mem)。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO / "backend" / "config" / "holdout_policy.yaml"

DDL = """
CREATE TABLE IF NOT EXISTS holdout_touch_log (
    touch_id        TEXT PRIMARY KEY,
    experiment      TEXT NOT NULL,
    criteria        TEXT NOT NULL,
    criteria_sha256 TEXT NOT NULL,
    registered_at   TEXT NOT NULL,
    touched_at      TEXT
);
"""


class HoldoutPolicyError(RuntimeError):
    """holdout 纪律违规基类。"""


class HoldoutBoundaryViolation(HoldoutPolicyError):
    """训练/调参数据越过 holdout_start (迭代烧 OOS)。"""


class HoldoutPreregistrationMissing(HoldoutPolicyError):
    """touch 前没有未消耗的预注册判据 (先写判据再看数字)。"""


class HoldoutBudgetExhausted(HoldoutPolicyError):
    """holdout 全局触碰预算耗尽。"""


def load_policy(path: Path | None = None) -> dict:
    """读 holdout_policy.yaml 并校验必备键。判断规则全在 yaml, 本模块零业务 hardcode。"""
    raw = yaml.safe_load((path or POLICY_PATH).read_text(encoding="utf-8")) or {}
    for key in ("holdout_start", "touch_budget", "require_preregistration"):
        if key not in raw:
            raise ValueError(f"holdout_policy.yaml 缺必备键: {key}")
    return raw


def _norm_yyyymmdd(d) -> str:
    """date/datetime/'2025-06-01'/'20250601' -> 'YYYYMMDD' (字符串比较安全)。"""
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y%m%d")
    s = str(d).strip().replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"无法解析日期 (期望 YYYYMMDD / YYYY-MM-DD / date): {d!r}")
    return s


def _resolve_conn(conn):
    """conn 给定则直接用 (测试注入); 否则开 experiment_store (manifest 路由), 返回 (conn, owned)。"""
    if conn is not None:
        return conn, False
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect
    mf = get_database_manifest()
    return connect(str(mf.path_for("experiment_store")), read_only=False), True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_criteria(experiment: str, criteria_text: str, conn=None) -> str:
    """预注册 holdout 触碰判据 (touch 之前, 先写"什么算过"再看数字)。

    写 holdout_touch_log 一行 (touched_at=NULL = 未消耗), 返回 touch_id。
    criteria_sha256 = 冻结判据指纹 (防事后挪门柱; freeze_rule 的 pin 点)。
    """
    if not experiment or not str(experiment).strip():
        raise ValueError("experiment 不能为空")
    if not criteria_text or not str(criteria_text).strip():
        raise ValueError("预注册判据不能为空 — 先写'什么算过'再看数字 (§2.1)")
    c, owned = _resolve_conn(conn)
    try:
        c.execute(DDL)
        touch_id = uuid.uuid4().hex
        sha = hashlib.sha256(str(criteria_text).encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO holdout_touch_log VALUES (?,?,?,?,?,NULL)",
            (touch_id, str(experiment), str(criteria_text), sha, _now_iso()),
        )
        return touch_id
    finally:
        if owned:
            c.close()


def touch_holdout(experiment: str, conn=None) -> int:
    """消耗一次 holdout 触碰预算, 返回剩余预算。

    前提 (require_preregistration=true): 该 experiment 存在未消耗的预注册判据行,
    否则 HoldoutPreregistrationMissing。全局已 touch 次数 >= touch_budget 时
    HoldoutBudgetExhausted (预算跨 experiment 共享, 见模块 docstring)。
    """
    policy = load_policy()
    budget = int(policy["touch_budget"])
    c, owned = _resolve_conn(conn)
    try:
        c.execute(DDL)
        touched = c.execute(
            "SELECT count(*) FROM holdout_touch_log WHERE touched_at IS NOT NULL"
        ).fetchone()[0]
        if touched >= budget:
            raise HoldoutBudgetExhausted(
                f"holdout 触碰预算耗尽: 已 touch {touched} >= budget {budget} "
                f"(holdout 已烧完, 剩余判定只能带'系统性乐观'标签, 不可再当 OOS)"
            )
        row = c.execute(
            "SELECT touch_id FROM holdout_touch_log "
            "WHERE experiment=? AND touched_at IS NULL "
            "ORDER BY registered_at LIMIT 1",
            (str(experiment),),
        ).fetchone()
        now = _now_iso()
        if row is None:
            if bool(policy["require_preregistration"]):
                raise HoldoutPreregistrationMissing(
                    f"experiment={experiment!r} 无未消耗的预注册判据 — "
                    f"先 register_criteria (先写'什么算过'再看数字, §2.1)"
                )
            # 政策关闭预注册时仍留触碰痕迹 (审计不可少)
            touch_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO holdout_touch_log VALUES (?,?,?,?,?,?)",
                (touch_id, str(experiment), "(preregistration disabled)", "", now, now),
            )
        else:
            c.execute(
                "UPDATE holdout_touch_log SET touched_at=? WHERE touch_id=?",
                (now, row[0]),
            )
        return budget - touched - 1
    finally:
        if owned:
            c.close()


def assert_holdout_untouched(data_end_date, conn=None) -> None:
    """训练/调参路径守门: data_end_date > holdout_start = raise (防迭代烧 OOS)。

    data_end_date == holdout_start 允许 (切分日本身是 train 窗右边界)。
    conn 参数为三函数签名一致而保留, 本门是纯 config 检查不读库。
    """
    del conn  # 签名一致性保留; 边界检查不需要库
    policy = load_policy()
    hs = _norm_yyyymmdd(policy["holdout_start"])
    de = _norm_yyyymmdd(data_end_date)
    if de > hs:
        raise HoldoutBoundaryViolation(
            f"训练/调参数据越界: data_end_date={de} > holdout_start={hs} — "
            f"迭代只许在 train 窗内做; 要用 holdout 必须走 register_criteria + touch_holdout (预算制)"
        )
