#!/usr/bin/env python3
"""smartmoney 34G 清除即拆分执行器 (runbook: analysis/db_split_runbook_20260612.md).

用户决议 2026-06-12: 立即执行。原理: DuckDB DELETE/DROP 不回收空间, 唯一回收 =
`COPY FROM DATABASE` 整库重建 (含表/视图/宏, 紧缩写入)。

安全设计 (validation FAIL 即弃新库, 旧库全程只读零改动):
  1. 前置: 磁盘余量 >= 旧库 0.9x; 无进程持旧库写锁
  2. COPY FROM DATABASE src TO dst (一条语句, 流式)
  3. validation: 全部表逐表 COUNT 比对 (一张不齐即 FAIL) + 关键 8 表全行 hash 校验
     + 视图数比对
  4. PASS → CHECKPOINT → 原子换名: 旧库 → smartmoney_v1_retired_<date>.duckdb
     (保留 14 天), 新库顶位 data/smartmoney.duckdb (manifest 路径不变零改动)
  5. 任何一步异常 → 新库删除, 旧库原样, exit 1 显式

用法: PYTHONPATH=backend python backend/scripts/db_split_execute.py [--no-rename]
  --no-rename: 只重建+验证不换名 (预演模式)
窗口: 避开 17:00-19:00 daily_update; 预计 20-60min (34G IO)。
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

log = logging.getLogger("db_split")
_REPO = Path(__file__).resolve().parents[2]


def _src_path() -> Path:
    """路径真相源 = database_manifest (零字面量)."""
    from services.database_manifest import get_database_manifest
    return Path(get_database_manifest().path_for("smartmoney"))


SRC = _src_path()
DST = SRC.parent / (SRC.stem + "_v2_building.duckdb")  # rule-compliance: ok evidence=db-rebuild-tool-needs-file-level-control-20260612

# 关键表全行 hash 校验清单 (决策/血缘/日历底座 — 行数对不够, 内容必须逐位一致)
HASH_TABLES = (
    "dim_trading_calendar",
    "mart_data_source_watermark",
    "mart_data_source_failure_queue",
    "mart_daily_recommendation",
    "mart_stock_trade_plan",
    "fact_paper_position",
    "champion_registry" ,
    "fact_concept_event",
)


def _preflight() -> None:
    free = shutil.disk_usage(_REPO).free
    src_size = SRC.stat().st_size
    if free < src_size * 0.9:
        raise SystemExit(f"磁盘余量不足: free={free/1e9:.1f}G < 0.9x src={src_size/1e9:.1f}G")
    holders = subprocess.run(["lsof", "-t", str(SRC)], capture_output=True, text=True).stdout.split()
    if holders:
        raise SystemExit(f"旧库被进程持有 (pid {holders}), 等其释放后再跑 — 不抢锁")
    if DST.exists():
        DST.unlink()
        log.info("清掉上次未完成的新库文件")


def _rebuild() -> None:
    log.info("COPY FROM DATABASE 开始 (34G, 预计 20-60min)...")
    con = duckdb.connect(str(DST))  # rule-compliance: ok evidence=db-rebuild-tool-needs-file-level-control-20260612
    try:
        # OOM 三连防 (两次实测: 整库 COPY FROM DATABASE 在 205 列×4M 行宽表上峰值不可控,
        # 6.3G/4G limit 均爆): 逐表拷贝 (峰值=单表流) + threads=2 + 关插入序 + 溢盘
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET threads=2")
        con.execute("SET memory_limit='3GB'")
        tmp = _REPO / "data" / "duckdb_tmp_split"
        tmp.mkdir(exist_ok=True)
        con.execute(f"SET temp_directory='{tmp}'")
        con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name='src' AND NOT internal"
        ).fetchall()]
        for i, t in enumerate(sorted(tables), 1):
            con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
            if i % 20 == 0:
                con.execute("CHECKPOINT")
                log.info("  逐表拷贝 %d/%d ...", i, len(tables))
        # 视图 DDL 重放 (依赖序未知 → 多轮重试, 残余在 validation 视图数比对中暴露)
        views = [r[0] for r in con.execute(
            "SELECT sql FROM duckdb_views() WHERE database_name='src' AND NOT internal").fetchall()]
        pending = list(views)
        for _ in range(3):
            nxt = []
            for sql in pending:
                try:
                    con.execute(sql)
                except Exception:  # noqa: BLE001 — 依赖序重试边界, 残余由 validation 抓
                    nxt.append(sql)
            if not nxt:
                break
            pending = nxt
        if pending:
            log.warning("视图重放残余 %d 个 (validation 将比对视图数)", len(pending))
        con.execute("CHECKPOINT")
    finally:
        con.close()
        import shutil as _sh
        _sh.rmtree(_REPO / "data" / "duckdb_tmp_split", ignore_errors=True)
    log.info("重建完成: 新库 %.1fG (旧 %.1fG)", DST.stat().st_size / 1e9, SRC.stat().st_size / 1e9)


def _validate() -> dict:
    """全表 COUNT + 关键表全行 hash + 视图数; 任一不齐 = FAIL."""
    src = duckdb.connect(str(SRC), read_only=True)  # rule-compliance: ok evidence=db-rebuild-tool-needs-file-level-control-20260612
    dst = duckdb.connect(str(DST), read_only=True)  # rule-compliance: ok evidence=db-rebuild-tool-needs-file-level-control-20260612
    try:
        src_tables = {r[0] for r in src.execute(
            "SELECT table_name FROM duckdb_tables() WHERE NOT internal").fetchall()}
        dst_tables = {r[0] for r in dst.execute(
            "SELECT table_name FROM duckdb_tables() WHERE NOT internal").fetchall()}
        missing = sorted(src_tables - dst_tables)
        if missing:
            return {"pass": False, "reason": f"新库缺表 {len(missing)}: {missing[:5]}"}
        mismatches = []
        for t in sorted(src_tables):
            n_src = src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            n_dst = dst.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            if n_src != n_dst:
                mismatches.append((t, n_src, n_dst))
        if mismatches:
            return {"pass": False, "reason": f"行数不齐 {len(mismatches)}: {mismatches[:5]}"}
        hash_fail = []
        for t in HASH_TABLES:
            if t not in src_tables:
                continue
            q = f'SELECT COUNT(*), SUM(hash(t)) FROM "{t}" t'  # hash(整行 struct); ROW(t.*) 语法实测全表崩
            if src.execute(q).fetchone() != dst.execute(q).fetchone():
                hash_fail.append(t)
        if hash_fail:
            return {"pass": False, "reason": f"关键表 hash 不一致: {hash_fail}"}
        n_views_src = src.execute("SELECT COUNT(*) FROM duckdb_views() WHERE NOT internal").fetchone()[0]
        n_views_dst = dst.execute("SELECT COUNT(*) FROM duckdb_views() WHERE NOT internal").fetchone()[0]
        return {
            "pass": n_views_src == n_views_dst,
            "reason": "" if n_views_src == n_views_dst else f"视图数 {n_views_src}!={n_views_dst}",
            "tables": len(src_tables),
            "hash_verified": [t for t in HASH_TABLES if t in src_tables],
            "views": n_views_dst,
            "src_gb": round(SRC.stat().st_size / 1e9, 2),
            "dst_gb": round(DST.stat().st_size / 1e9, 2),
        }
    finally:
        src.close()
        dst.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-rename", action="store_true", help="预演: 重建+验证不换名")
    args = ap.parse_args()

    _preflight()
    try:
        _rebuild()
        result = _validate()
    except Exception as exc:
        if DST.exists():
            DST.unlink()
        log.error("重建/验证异常, 新库已删除, 旧库零改动: %s", exc)
        return 1

    log.info("validation: %s", result)
    if not result["pass"]:
        DST.unlink()
        log.error("validation FAIL → 新库已删除, 旧库零改动: %s", result["reason"])
        return 1

    if args.no_rename:
        log.info("预演模式: 新库保留在 %s, 未换名", DST)
        return 0

    # 原子换名 (manifest 路径 data/smartmoney.duckdb 不变, 零配置改动)
    holders = subprocess.run(["lsof", "-t", str(SRC)], capture_output=True, text=True).stdout.split()
    if holders:
        log.error("换名前旧库被 pid %s 持有 — 新库保留待手动换名, 旧库零改动", holders)
        return 1
    retired = SRC.parent / f"smartmoney_v1_retired_{datetime.now(timezone.utc).strftime('%Y%m%d')}.duckdb"  # Phase ψ.5 allowlist: 归档文件名非 trade_date; rule-compliance: ok evidence=db-rebuild-rename-target
    SRC.rename(retired)
    DST.rename(SRC)
    log.info("换名完成: 旧库 → %s (保留 14 天后人工删除, 届时真正回收 %.1fG)",
             retired.name, retired.stat().st_size / 1e9)
    log.info("回收账: 旧 %.1fG → 新 %.1fG, 紧缩 %.1fG (旧文件删除后兑现)",
             result["src_gb"], result["dst_gb"], result["src_gb"] - result["dst_gb"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
