#!/usr/bin/env python3
"""生命周期删除执行器 (owner=docs/engineering_governance.md §6/§10)。

按删除 manifest (yaml) 安全删除 L1 探索数据 / 死表, 内置 4 道闸:
  1. live 守护: 每张表删前 word-boundary grep live 服务面 (daily_update 脚本集 + serving/ensemble/scoring/
     recommendation/pipeline + backend/scripts/ 治理脚本目录); 命中 = REFUSE 不删
     (safe-by-construction; 2026-06-14 抓出 v6_kpi_compare workflow 漏判; 2026-07-06 补
     backend/scripts/ + pipeline/ 扫描范围, 堵住"删表不删治理脚本引用"的结构性盲区)。
  2. 冷归档: action=archive 的表先 COPY TO parquet (verify 文件) 再删 — 防失去 PIT/leakage 再审计能力。
  3. 留痕: 每张删除写 mart_data_deletion_record (行数/schema/reason/归档路径/时间) — validation artifacts 不静默消失。
  4. 残留扫描: 删后扫所有 VIEW 定义有无悬挂引用已删表。

dry-run 默认 (列计划 + 跑 live 守护); --execute 才归档+留痕+DROP。DROP 不回收文件块 → 之后须跑 db_compact。

用法:
  python backend/scripts/db_lifecycle_delete.py --manifest analysis/lifecycle_delete_manifest_20260614.yaml
  python backend/scripts/db_lifecycle_delete.py --manifest <m> --execute
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

MANIFEST_DB = REPO / "backend" / "config" / "database_manifest.yaml"


def _db_path(alias: str) -> Path:
    m = yaml.safe_load(open(MANIFEST_DB, encoding="utf-8"))
    return REPO / m["databases"][alias]["path"]


def _live_surface() -> list[Path]:
    """daily_update 调用脚本集 + serving/ensemble/scoring/recommendation + backend/scripts/
    (治理/审计/build_* 一次性脚本) + backend/services/pipeline/ (真实当前调用图) = live 服务面。

    2026-07-06 全面数据审计根因根治（历史证据=analysis/comprehensive_data_module_audit_20260706.md；
    现行 owner=docs/engineering_governance.md
    pit_leakage_spotcheck 维度): 原实现只对 `scripts/daily_update.sh` 做正则抓已调用脚本名 +
    `serving/recommendation/scoring/ensemble` 四个目录——**结构性排除 `backend/scripts/` 整个
    目录本身**, 导致 data_quality.py 这类"表已删但治理脚本仍用 SQL 字符串引用"的死引用完全
    检测不到 (审计诊断: 这是"残留反复出现"的核心机制之一, 非偶然)。另: `daily_update.sh`
    2026-06-23 重设计后已委托 `services.pipeline.run` 模块调用 (不再直接 shell 出
    `backend/scripts/*.py`), 原正则现在恒抓不到东西（历史执行面 verifier 的同型
    "PASS by vacuity" 问题）—— 改为直接扫 `backend/services/pipeline/` (真实当前调用
    图, ctx.run_script(...) 的实际调用点) 而非解析已过期的 wrapper 脚本文本。
    """
    paths: list[Path] = [REPO / "scripts" / "daily_update.sh"]
    for d in ("serving", "recommendation", "scoring", "ensemble", "pipeline"):
        paths += list((REPO / "backend" / "services" / d).rglob("*.py"))
    paths += list((REPO / "backend" / "scripts").glob("*.py"))
    return [p for p in paths if p.exists()]


def _load_surface(surface: list[Path]) -> list[tuple[Path, str]]:
    """读 live 面文件入内存 (一次), 供 word-boundary 匹配 (不依赖 rg, subprocess PATH 无 rg)。"""
    out = []
    for p in surface:
        try:
            out.append((p, p.read_text(encoding="utf-8", errors="ignore")))
        except Exception:  # noqa: BLE001
            pass
    return out


def _is_live_cited(table: str, corpus: list[tuple[Path, str]]) -> str | None:
    """word-boundary 正则 (下划线算词字符 → base 名不误配 _v4 后缀); 返回首个命中 'file:line' 或 None。"""
    pat = re.compile(r"\b" + re.escape(table) + r"\b")
    for p, content in corpus:
        m = pat.search(content)
        if m:
            line = content[: m.start()].count("\n") + 1
            return f"{p.relative_to(REPO)}:{line}"
    return None


def _ensure_deletion_record(conn) -> None:
    """确保留痕表存在 (M2 Stage E: 非 smartmoney 库如 etf/market 首次物删时无此表; schema 对齐中央表)。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mart_data_deletion_record ("
        "record_id VARCHAR, deletion_run_id VARCHAR, table_name VARCHAR, "
        "delete_scope VARCHAR, key_column VARCHAR, key_value VARCHAR, "
        "deleted_rows BIGINT, deleted_files BIGINT, deleted_bytes BIGINT, "
        "reason VARCHAR, verification_json VARCHAR, deleted_at VARCHAR)"
    )


def _next_seq(conn) -> int:
    n = conn.execute(
        "SELECT count(*) FROM mart_data_deletion_record WHERE deletion_run_id LIKE 'lifecycle_%'"
    ).fetchone()[0]
    return n


def run(manifest_path: Path, execute: bool, force: bool = False) -> int:
    man = yaml.safe_load(open(manifest_path, encoding="utf-8"))
    entries = man["entries"]
    run_id = man.get("run_id", "lifecycle_adhoc")
    archive_dir = REPO / man.get("archive_dir", "data/archive/lifecycle")

    db_alias = man.get("db", "smartmoney")  # M2 Stage E: 支持非 smartmoney 库 (etf/market) 物删; 留痕入该库自身 deletion_record
    db = _db_path(db_alias)
    print(f"=== 生命周期删除 manifest={manifest_path.name} ({len(entries)} 表) db={db_alias} ===")

    # 1. live 守护 (全部先过一遍, 命中即剔除); --force 跳过 (用于有意删除 live 层, 如地基-reset)
    refused = []
    if force:
        print(f"  ** --force: 跳过 live 守护 (有意删除含 live 层) | run_id={run_id} **")
    else:
        surface = _live_surface()
        corpus = _load_surface(surface)
        print(f"  live 守护面: {len(surface)} 文件 | run_id={run_id} | archive_dir={archive_dir.relative_to(REPO)}")
        for e in entries:
            hit = _is_live_cited(e["table"], corpus)
            if hit:
                refused.append((e["table"], hit))
    todo = [e for e in entries if e["table"] not in {t for t, _ in refused}]
    if refused:
        print(f"\n  [live 守护] REFUSE {len(refused)} 张 (命中 live 服务面, 不删):")
        for t, h in refused:
            print(f"    REFUSE {t}  ← {h}")
    n_arch = sum(1 for e in todo if e["action"] == "archive")
    print(f"\n  待执行: {len(todo)} 表 (drop={len(todo)-n_arch} archive={n_arch})")

    if not execute:
        print("\n  DRY-RUN: --execute 才归档+留痕+DROP。DROP 后须跑 db_compact 回收盘。")
        return 0

    conn = duck_connect(str(db), read_only=False)
    conn.execute("SET enable_progress_bar=false")
    _ensure_deletion_record(conn)  # 非 smartmoney 库首次物删可能无留痕表
    dropped, archived, errors = [], [], []
    try:
        seq = _next_seq(conn)
        for i, e in enumerate(todo, 1):
            t = e["table"]
            if i % 25 == 0:
                conn.execute("CHECKPOINT")  # 防连续 DROP 后 catalog 缓存 stale (反例: 2026-06-14 reset 第144张 count(*) 假崩)
            # 存在性 + 行/列捕获 (包 try: 缓存 stale 时跳过不崩整轮)
            try:
                if conn.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name=?", [t]
                ).fetchone()[0] == 0:
                    errors.append((t, "表不存在 (已删?)"))
                    continue
                rows = conn.execute(f'SELECT count(*) FROM main."{t}"').fetchone()[0]
                cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
            except Exception as ex:  # noqa: BLE001
                errors.append((t, f"行/列捕获失败(跳过) {str(ex)[:60]}"))
                continue
            arch_path = ""
            # 2. 冷归档 (archive 动作; 事务外先做, 失败则跳过 drop)
            if e["action"] == "archive":
                archive_dir.mkdir(parents=True, exist_ok=True)
                pq = archive_dir / f"{t}.parquet"
                try:
                    conn.execute(f"COPY \"{t}\" TO '{pq}' (FORMAT PARQUET)")
                    if not pq.exists() or pq.stat().st_size == 0:
                        errors.append((t, "归档 parquet 空/缺失, 跳过 drop"))
                        continue
                    arch_path = str(pq.relative_to(REPO))
                    archived.append((t, rows, arch_path))
                except Exception as ex:  # 归档失败不删
                    errors.append((t, f"归档失败 {str(ex)[:60]}, 跳过 drop"))
                    continue
            # 3+drop: 留痕 + DROP 同事务
            is_view = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name=? AND table_type='VIEW'", [t]
            ).fetchone()[0] > 0
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.execute(
                    "INSERT INTO mart_data_deletion_record (record_id, deletion_run_id, table_name, "
                    "delete_scope, key_column, key_value, deleted_rows, deleted_files, deleted_bytes, "
                    "reason, verification_json, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, strftime(now(),'%Y-%m-%dT%H:%M:%S'))",
                    [f"{run_id}_{seq:03d}", run_id, t, "view_drop" if is_view else "table_drop", "", "", rows, 0, 0,
                     e.get("reason", "")[:400],
                     json.dumps({"bucket": e.get("bucket"), "cols": cols, "archive": arch_path}, ensure_ascii=False)],
                )
                conn.execute(f'DROP VIEW "{t}"' if is_view else f'DROP TABLE "{t}"')
                conn.execute("COMMIT")
                seq += 1
                if e["action"] != "archive":
                    dropped.append((t, rows))
            except Exception as ex:
                try: conn.execute("ROLLBACK")
                except Exception: pass
                errors.append((t, f"DROP 失败 {str(ex)[:80]}"))
        conn.execute("CHECKPOINT")

        # 4. 残留扫描: 悬挂视图引用
        gone = {t for t, _ in dropped} | {t for t, _, _ in archived}
        views = conn.execute(
            "SELECT view_name, sql FROM duckdb_views() WHERE schema_name='main'"
        ).fetchall()
        dangling = [(v, t) for v, vsql in views for t in gone if vsql and t in (vsql or "")]
    finally:
        conn.close()

    print(f"\n  执行完成: DROP {len(dropped)} 张 / ARCHIVE+DROP {len(archived)} 张 / 错误 {len(errors)}")
    if archived:
        print("  已归档 (parquet 留底):")
        for t, r, p in archived:
            print(f"    {r:>10,}  {t} → {p}")
    if errors:
        print("  错误/跳过:")
        for t, m in errors:
            print(f"    {t}: {m}")
    if dangling:
        print(f"  !! 悬挂视图引用 {len(dangling)} (需手动修):")
        for v, t in dangling:
            print(f"    VIEW {v} 引用已删 {t}")
    else:
        print("  残留扫描: 0 悬挂视图引用。")
    print(f"\n  留痕: mart_data_deletion_record (run_id={run_id})。DROP 不回收盘 → 跑 db_compact 缩盘。")
    return 0 if not errors else 6


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--execute", action="store_true", help="真删 (默认 dry-run)")
    ap.add_argument("--force", action="store_true", help="跳过 live 守护 (有意删除含 live 层, 如地基-reset)")
    args = ap.parse_args()
    sys.exit(run(REPO / args.manifest if not Path(args.manifest).is_absolute() else Path(args.manifest), args.execute, args.force))


if __name__ == "__main__":
    main()
