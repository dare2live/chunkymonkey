#!/usr/bin/env python3
"""概念/板块成分与资金流每日快照自养 (E7, 策略锻造 W1).

为什么存在: dc_member/dc_index 历史仅 2025 起 (E8 探底实测), 概念隶属历史买不到,
攒一天少一天 — 本任务与任何策略 go/no-go 完全解耦, 是零成本不可逆数据资产。
套三 (题材扩散) W9 复审、概念协同/产业链扩散因子全部依赖这份积累。

落盘: data/concept_snapshots/<YYYYMMDD>/{dc_member,ths_member,moneyflow_cnt_ths,
moneyflow_ind_dc,limit_cpt_list}.parquet — parquet 文件级追加, 不写 DuckDB
(与主库写锁解耦; 积累期后由 sync_runner 统一入库)。

调度: launchd com.chunkymonkey.concept-snapshot 每日 17:40 (盘后数据就绪),
经 launchd_job_wrapper.py — 失败 ALERT flag + 系统通知 (宪法 v2 第 5 条)。

纪律 (宪法 v2 第 6 条): 0 行 = 失败重试 <=3 次退避; 任一关键源全失败 -> exit 1
让 wrapper 告警, 绝不静默落空。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "data" / "concept_snapshots"

# .env 加载 (launchd 环境无 shell profile)
_env_file = REPO / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _pro():
    import tushare as ts

    token = os.environ["TUSHARE_TOKEN"]
    pro = ts.pro_api(token)
    custom = os.environ.get("TUSHARE_HTTP_URL", "").strip()
    if custom:
        pro._DataApi__token = token
        pro._DataApi__http_url = custom
    return pro


def _fetch_retry(name: str, fn, tries: int = 3):
    """0 行/异常 -> 指数退避重试 (宪法 v2 第 6 条: 0 行当失败)."""
    for i in range(tries):
        try:
            df = fn()
            if df is not None and len(df):
                return df
        except Exception as exc:  # noqa: BLE001 — 重试边界, 最终失败上抛
            print(f"WARN {name} try{i+1}: {str(exc)[:120]}", flush=True)
        time.sleep(2 * (i + 1))
    return None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="快照日期 YYYYMMDD, 默认今天 (验证/回补用)")
    args = parser.parse_args()

    pro = _pro()
    today = args.date or datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: 快照日由下方交易日 gate 校验
    # 交易日 gate (2026-06-12 根治, calendar_gate 复查唯一真违规): 非交易日 launchd 17:40
    # 照跑会对 5 个 API 打无效请求, 且部分接口对非交易日返回邻日数据 → 生成假快照目录
    # 污染概念事件 diff (event_date 错位)。日历不可达时 fail-open 继续跑 (快照宁多勿缺,
    # 多了可去重, 缺了买不回) 但告警可见。
    if args.date is None:
        try:
            from services.database_manifest import get_database_manifest  # 路径真相源 = manifest
            from services.duck_adapter import connect as duck_connect  # 项目连接契约, 禁裸 duckdb.connect

            cal = duck_connect(str(get_database_manifest().path_for("smartmoney")), read_only=True)
            try:
                is_trading = cal.execute(
                    "SELECT COUNT(*) FROM dim_trading_calendar WHERE trade_date = ? AND is_trading = 1",
                    [f"{today[:4]}-{today[4:6]}-{today[6:]}"],
                ).fetchone()[0]
            finally:
                cal.close()
            if not is_trading:
                print(f"{today} 非交易日, 跳过快照 (交易日 gate)")
                return 0
        except Exception as exc:  # noqa: BLE001 — fail-open: 日历锁竞争不挡快照, 但必须可见
            print(f"WARN: 交易日 gate 日历不可达 ({str(exc)[:80]}), fail-open 继续快照")
    out_dir = OUT_ROOT / today
    out_dir.mkdir(parents=True, exist_ok=True)

    # (源名, 拉取函数, 是否关键源 — 关键源全败才算任务失败)
    sources = [
        ("dc_member", lambda: pro.dc_member(trade_date=today), True),
        ("moneyflow_cnt_ths", lambda: pro.moneyflow_cnt_ths(trade_date=today), True),
        ("moneyflow_ind_dc", lambda: pro.moneyflow_ind_dc(trade_date=today), True),
        ("limit_cpt_list", lambda: pro.limit_cpt_list(trade_date=today), False),
        ("dc_index", lambda: pro.dc_index(trade_date=today), False),
    ]
    ok, critical_fail = [], []
    for name, fn, critical in sources:
        df = _fetch_retry(name, fn)
        if df is not None:
            df.to_parquet(out_dir / f"{name}.parquet")
            ok.append(f"{name}({len(df)})")
        elif critical:
            critical_fail.append(name)
        time.sleep(0.5)

    # ths_member 周一全量分支已删 (2026-06-12 概念域单源化决议: 东财 dc 系唯一,
    # THS 概念族出局, "攒一天少一天" 仅对已出局的 THS 成立故不再积累; 历史 _full 快照保留为证据)

    print(f"snapshot {today}: ok={ok} critical_fail={critical_fail}", flush=True)
    if critical_fail:
        # 非交易日所有源天然空 — 用交易日历区分 (休市日 0 行是正常, 不告警)
        try:
            cal = _fetch_retry("trade_cal", lambda: pro.trade_cal(
                start_date=today, end_date=today))
            if cal is not None and len(cal) and int(cal.iloc[0].get("is_open", 1)) == 0:
                print(f"{today} 休市, 空快照正常", flush=True)
                return 0
        except Exception:  # noqa: BLE001 — 日历探测失败按交易日保守告警
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
