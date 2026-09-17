"""chain_run — doctor 节：登记不阻塞分区落库，阻塞「宣布地基完成」。

背景 (fable_review_chain_run_stop_rule.md 任务 1): ``run_outcome == success`` 在
现有代码里已经等价于「那次运行全部证据全绿」(system_health 任一 FAIL/UNVERIFIED
都会让 store.py 把它记成 degraded), 所以本节不用重新解析八份 audit JSON —— 难点
只在「选中哪一份是 D 日那次真运行」与「那份报告是不是真的」。

七条判据 R1-R7, 缺一即 FAIL (哪一条不满足写进 ``failed_rule``):
  R1 报告文件存在、可解析、内容 date == D (不信文件名, 不 glob, 不看 mtime)
  R2 dry_run == 0
  R3 log 字段等于按 date 算出的日志路径签名 (只查路径签名不查文件是否还在, 见
     context.py 的 DEGRADED_FLAG 用途注释 —— 重启会清 /tmp)
  R4 skip_sync == 0 (键不存在也 FAIL —— fail-closed)
  R5 run_outcome == "success" (其余三态和未知值都 FAIL)
  R6 D 日证据实体齐全: watermark_sla_before_{D} + watermark_sla_{D} + governance_gates
     里六条 runtime_checks 渲染出的 --json-out 路径, 全部存在
  R7 accepted 前沿 >= 按运行结束时刻(watermark_sla_{D}.run_at)应有的最近交易日
     (把「链跑了」和「数据到了」钉在一起; skip-sync 绕不过的一条)

三态语义:
  PASS       R1-R7 全过。
  FAIL       任一条不满足 (含「文件不存在」——这是 FAIL 不是 UNVERIFIED)。
  UNVERIFIED 判据本身算不出 (registry 不可用 / 日历不可达 / 前沿读取失败等)。

R7 的前沿函数与日历函数都可注入 (测试一律注入，不开任何库)；默认实现见
``_default_frontier_fn`` / ``_default_calendar_fn``，复用仓库已有的只读连接工具与
``services.calendar`` 写侧同一口径函数，不自造第二套口径。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .evidence_paths import load_pipeline_evidence_paths
from .run_outcome import OUTCOME_SUCCESS

STATE_PASS = "PASS"
STATE_FAIL = "FAIL"
STATE_UNVERIFIED = "UNVERIFIED"


def _compact(date_str: str) -> str:
    """'YYYY-MM-DD' / 'YYYYMMDD' -> 'YYYYMMDD' (accepted_partition 与日历函数两种格式并存)。"""
    return date_str.replace("-", "")


def runtime_check_json_out_paths(registry: Any, *, date: str) -> list[str]:
    """从 governance_gates 登记表的 runtime_checks 渲染出的 --json-out 相对路径。

    只查存在性，不另抄一份清单 —— governance_gates.yaml 才是这份清单的唯一定义处
    (fable_review 指定用 ``RuntimeCheckSpec.rendered_args(date=D)`` 渲染, 不写死)。
    grain_uniqueness / foundation_live 两条没有 --json-out (args=[]), 自然被跳过。
    """
    paths: list[str] = []
    for spec in registry.runtime_checks:
        rendered = spec.rendered_args(date=date)
        for i, arg in enumerate(rendered):
            if arg == "--json-out" and i + 1 < len(rendered):
                paths.append(rendered[i + 1])
    return paths


def _default_registry_loader() -> Any:
    from services.governance_gates import load_registry

    return load_registry()


def _default_frontier_fn() -> str | None:
    """accepted_partition 的 daily 前沿 (read_only, 不复刻第二套判据)。

    dataset_id / 库别名复用 nominal_ohlcv_schema.DOMAIN —— 换源/改库时这里跟着变,
    不在本模块或配置里另抄一份 (CLAUDE.md #11: 别处已定义的参数复用那一处)。
    """
    from services.data_access import resolver
    from services.data_sources.accepted_schema import ACCEPTED_TABLE
    from services.data_sources.nominal_ohlcv_schema import DOMAIN

    conn = resolver.connect_ro(DOMAIN.target_db)
    try:
        row = conn.execute(
            f"SELECT MAX(partition_value) FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [DOMAIN.dataset_id],
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


def _default_calendar_fn(run_at: datetime) -> str | None:
    """写侧同一口径 (15:05 阈值)，不自造第二套日历判断。"""
    from services.calendar import latest_completed_for_kline_write

    return latest_completed_for_kline_write(now=run_at)


def evaluate_chain_run(
    run_date: str,
    *,
    repo: Path,
    degraded_flag_parent: Path | None = None,
    frontier_fn: Callable[[], str | None] | None = None,
    calendar_fn: Callable[[datetime], str | None] | None = None,
    registry_loader: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Evaluate R1-R7 for run_date D against evidence rooted at ``repo``.

    Returns ``{"date", "state", "failed_rule", "reason"}``. ``state`` is one of
    PASS/FAIL/UNVERIFIED (see module docstring). ``failed_rule``/``reason`` are
    ``None`` on PASS.
    """
    result: dict[str, Any] = {"date": run_date}

    def _fail(rule: str, reason: str) -> dict[str, Any]:
        result.update(state=STATE_FAIL, failed_rule=rule, reason=reason)
        return result

    def _unverified(rule: str, reason: str) -> dict[str, Any]:
        result.update(state=STATE_UNVERIFIED, failed_rule=rule, reason=reason)
        return result

    paths = load_pipeline_evidence_paths()

    # R1: report exists, parses, content date == D (never glob/mtime).
    report_path = repo / paths.daily_report_rel(date=run_date)
    if not report_path.is_file():
        return _fail("R1", f"report file not found: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return _fail("R1", f"report file unreadable/not JSON: {report_path} ({exc})")
    if not isinstance(report, dict) or str(report.get("date")) != run_date:
        return _fail(
            "R1",
            f"report content date={report.get('date')!r} does not match D={run_date!r} "
            f"(filename is not trusted)",
        )

    # R2: dry_run == 0.
    dry_run = report.get("dry_run")
    if isinstance(dry_run, bool) or not isinstance(dry_run, int) or dry_run != 0:
        return _fail("R2", f"dry_run={dry_run!r} (must be int 0)")

    # R3: log path signature (path only; file itself may be gone after a reboot).
    # 文件名模板从 evidence_paths 配置读 (CLAUDE.md #11: 不持有第二份字面量副本) ——
    # 目录部分仍是运行时 flag_parent, 不是参数。
    flag_parent = degraded_flag_parent
    if flag_parent is None:
        from .context import DEGRADED_FLAG

        flag_parent = DEGRADED_FLAG.parent
    expected_log = str(flag_parent / paths.daily_update_log_name(date=run_date))
    if str(report.get("log")) != expected_log:
        return _fail(
            "R3", f"log={report.get('log')!r} does not match expected signature {expected_log!r}"
        )

    # R4: skip_sync == 0; missing key fails closed.
    if "skip_sync" not in report:
        return _fail("R4", "skip_sync key missing from report (fail-closed)")
    skip_sync = report.get("skip_sync")
    if isinstance(skip_sync, bool) or not isinstance(skip_sync, int) or skip_sync != 0:
        return _fail("R4", f"skip_sync={skip_sync!r} (must be int 0)")

    # R5: run_outcome == success; every other value (typed or unknown) fails.
    run_outcome = report.get("run_outcome")
    if run_outcome != OUTCOME_SUCCESS:
        return _fail("R5", f"run_outcome={run_outcome!r} (must be {OUTCOME_SUCCESS!r})")

    # R6: D-day evidence entities all exist (report says success but scripts never ran?).
    try:
        registry = (registry_loader or _default_registry_loader)()
    except Exception as exc:  # noqa: BLE001 — registry 不可用是「算不出」不是「不满足」
        return _unverified("R6", f"runtime-check registry unavailable: {exc}")
    sibling_rel_paths = [
        paths.watermark_sla_before_rel(date=run_date),
        paths.watermark_sla_rel(date=run_date),
        *runtime_check_json_out_paths(registry, date=run_date),
    ]
    missing = [rel for rel in sibling_rel_paths if not (repo / rel).is_file()]
    if missing:
        return _fail("R6", f"missing D-day evidence file(s): {missing}")

    # R7: accepted frontier >= expected trade date as of run_at (skip-sync 绕不过).
    sla_path = repo / paths.watermark_sla_rel(date=run_date)
    try:
        sla_payload = json.loads(sla_path.read_text(encoding="utf-8"))
        run_at = datetime.fromisoformat(str(sla_payload["run_at"]))
    except (OSError, UnicodeDecodeError, ValueError, KeyError) as exc:
        return _unverified("R7", f"watermark_sla run_at unreadable: {sla_path} ({exc})")

    calendar = calendar_fn or _default_calendar_fn
    try:
        expected = calendar(run_at)
    except Exception as exc:  # noqa: BLE001 — 日历不可达是「算不出」不是「不满足」
        return _unverified("R7", f"calendar lookup failed: {exc}")
    if not expected:
        return _unverified("R7", "calendar lookup returned no expected trade date")

    frontier = frontier_fn or _default_frontier_fn
    try:
        actual = frontier()
    except Exception as exc:  # noqa: BLE001 — 库打不开/写锁是「算不出」不是「不满足」
        return _unverified("R7", f"frontier lookup failed: {exc}")
    if not actual:
        return _unverified("R7", "frontier lookup returned no accepted partition")

    if _compact(actual) < _compact(expected):
        return _fail(
            "R7",
            f"accepted frontier {actual!r} < expected {expected!r} "
            f"(run_at={run_at.isoformat()})",
        )

    result.update(state=STATE_PASS, failed_rule=None, reason=None)
    return result
