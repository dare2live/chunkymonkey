#!/usr/bin/env python3
"""DuckDB 整库保真紧缩 (DROP 后文件不缩 — 内部块需整库重写才回收盘)。

保真 = 逐表原 DDL (含 PK/约束) + INSERT + 重建索引 + 视图按定义重建 —— **绝不用 CREATE TABLE AS SELECT**
(CTAS 丢 PK = 06-12 db_split_execute 同型坑, 约束 315→1; 仅 NULL-sql 且零约束的表才 CTAS-fallback)。
ATTACH-copy 法 (无中间 parquet, peak=old+new), 不删生产库 (验证通过才经共用的原子换名机制
`services.duckdb_file_swap.swap_in_fresh_file` 换名, 旧库按需留 bak)。

dry-run 默认 (列计划 + 对账基线); --execute 才重写 + 验证 + 换名 + 删 bak (--keep-bak 保留)。

2026-09-24 (cut_qfq_fresh_file_swap): 换名与 `build_price_kline_qfq_tushare.py` 共用同一
个原子换名函数 (M3) —— 不再各写一遍 `src.rename(bak); new.rename(src)`。换名前置两道围栏:
开头检查 `src.wal` 不得残留 (rc=7, 不建新库), 对账基线读之前记 `src` 指纹, 验证通过后连同
该指纹一起交给 `swap_in_fresh_file`, 换名期间 `src` 若被别的写者动过 (指纹变化) 或有活跃
写者, 一律拒绝换名 (`SwapRefused`, rc=8), `new` 会被删掉、`src` 与其它文件原样不动。

2026-09-25 (M11, 审查裁决 sandbox/review_20260924/ruling.md K1/M11): 建过 `new` 之后的
每一条失败路径 (对账不齐 rc=5、任何异常、`SwapRefused` rc=8) 都删掉 `new` 及 `new.wal`；
开头遇到残留的 `new`(上次失败/崩溃留下) 视为上次失败, 先清再继续重建, 不再 `return 4`
早退 (旧版永久卡在 rc=4, `store.py` 的"下次日更再试"曾经是假话——`new` 从不会自己消失)。

2026-09-25 (返修 cut_qfq_fresh_file_swap blocking review, sandbox/churn_fix_20260919):
CLI 入口 `main()` 取 `writer_lock("db_compact")` (锁忙 rc=4, 与 build_price_kline_qfq_tushare.py
的 LOCK_BUSY 同号)——`--execute` 才取, dry-run 只读不互斥。`run()` 本身不取锁: 日更 store
步骤经 `services.duckdb_compact.compact_if_bloated` 在同进程内直接调 `run()`(不走 `main()`),
它已经跑在 pipeline 自己的 writer_lock 之下, `run()` 里再取一次全局单锁会自锁。旧版无锁时,
两个并发 `--execute`（人手动跑 + 日更并跑）可以一个把另一个建到一半的 new 换名进 src、
丢表却各自互不知情地各报一次成功。

用法:
  python backend/scripts/db_compact.py --db smartmoney              # dry-run
  python backend/scripts/db_compact.py --db smartmoney --execute    # 重写紧缩 + 验证 + 换名, 换名成功后自动删 bak
  python backend/scripts/db_compact.py --db smartmoney --execute --keep-bak  # 同上但保留 _precompact_bak
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.db_compaction_rules import load_db_compaction_config  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.duckdb_file_swap import (  # noqa: E402
    SwapRefused,
    file_fingerprint,
    swap_in_fresh_file,
)
from services.writer_lock import WriterLockBusyError, writer_lock  # noqa: E402

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"


def _db_path(alias: str) -> Path:
    m = yaml.safe_load(open(MANIFEST, encoding="utf-8"))
    return REPO / m["databases"][alias]["path"]


def _rel(p: Path) -> Path:
    """显示用相对路径; p 不在 repo 下 (如临时库) 时回退原路径, 不崩。"""
    try:
        return p.relative_to(REPO)
    except ValueError:
        return p


def _discard_new(new: Path, new_wal: Path) -> None:
    """删掉 new 及 new.wal (M11: 建过 new 之后的每条失败路径都要清干净, 不留给
    下一次运行去撞 "已存在" 早退)。"""
    new.unlink(missing_ok=True)
    new_wal.unlink(missing_ok=True)


def run(alias: str, execute: bool, drop_bak: bool = True) -> int:
    src = _db_path(alias)
    # 派生兄弟文件名 (非 hardcode DB 路径; src 来自 database_manifest)
    new = src.with_name(src.stem + "_compact.duckdb")  # rule-compliance: ok evidence=derived from manifest src
    new_wal = new.with_name(new.name + ".wal")
    bak = src.with_name(src.stem + "_precompact_bak.duckdb")  # rule-compliance: ok evidence=derived from manifest src

    # 最开头: src.wal 残留 (上一个写者没干净关闭) → 拒绝, 不建新库 (M3)。
    src_wal = src.with_name(src.name + ".wal")
    if src_wal.exists():
        print(f"FAIL: {src_wal.name} 残留, 拒绝紧缩 (WAL 未清空, 换名会把它回放进新库)", file=sys.stderr)
        return 7
    # 对账基线读之前记指纹 (M3): 换名前会拿它跟当时的 src 再比一次, 抓"紧缩期间 src 被
    # 另一个写者动过"。
    expected = file_fingerprint(src)

    # baseline (read-only)
    s = duck_connect(str(src), read_only=True)
    try:
        tables = [r[0] for r in s.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()]
        views = [r[0] for r in s.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW'"
        ).fetchall()]
        base_cons = s.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
        base_idx = s.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0]
        ddls = {r[0]: r[1] for r in s.execute("SELECT table_name, sql FROM duckdb_tables WHERE schema_name='main'").fetchall()}
        view_sql = {r[0]: r[1] for r in s.execute("SELECT view_name, sql FROM duckdb_views() WHERE schema_name='main'").fetchall()}
        base_rows = {t: s.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        base_cons_t = {t: s.execute("SELECT count(*) FROM duckdb_constraints() WHERE table_name=?", [t]).fetchone()[0] for t in tables}
    finally:
        s.close()

    sz = src.stat().st_size / 1e9
    print(f"=== 整库保真紧缩 db={alias} ({_rel(src)}, {sz:.1f}G) ===")
    print(f"  对账基线: {len(tables)} 表 + {len(views)} 视图 / {base_cons} 约束 / {base_idx} 索引 / {sum(base_rows.values()):,} 行")
    free = shutil.disk_usage(REPO).free / 1e9
    print(f"  磁盘余量: {free:.0f}G (peak≈old+new≈{sz*2:.0f}G)")

    if not execute:
        print("  DRY-RUN: --execute 重写紧缩 + 验证 + 换名 (默认换名后自动删 bak；--keep-bak 保留)。")
        return 0
    if new.exists() or new_wal.exists():
        # 残留 new/new.wal (M11): 上次运行崩溃或失败时留下的, 视为上次失败——不是
        # "已存在"的早退理由, 先清干净再从头重建 (不再 return 4, 那条路径曾让每次
        # 都撞见上一次的残留、永久卡死)。必须排在磁盘余量门之前: 残留的 new 本身
        # 占着盘 (GB 级), 不先删就去比 free < min_free_disk_gb 会把"上次崩溃留下
        # 的垃圾"误判成"真的没盘", 永久卡在 rc=6 (M11 复审 repro compact_rc6.py)。
        print(f"  {new.name} 残留 (上次失败留下), 先清再重建", file=sys.stderr)
        _discard_new(new, new_wal)
        free = shutil.disk_usage(REPO).free / 1e9  # 残留已删, 磁盘余量必须重新量, 不能用清理前的旧值
    min_free_disk_gb = load_db_compaction_config().min_free_disk_gb
    if free < min_free_disk_gb:
        print(f"FAIL: 磁盘余量 {free:.0f}G < {min_free_disk_gb:.0f}G, 拒绝紧缩", file=sys.stderr)
        return 6

    try:
        t = duck_connect(str(new), read_only=False)
        try:
            t.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
            for i, tab in enumerate(tables, 1):
                ddl = ddls.get(tab)
                ncons = base_cons_t[tab]
                if ddl:
                    t.execute(ddl)
                    t.execute(f'INSERT INTO "{tab}" SELECT * FROM src."{tab}"')
                elif ncons == 0:
                    t.execute(f'CREATE TABLE "{tab}" AS SELECT * FROM src."{tab}"')  # NULL-sql 零约束安全
                else:
                    raise RuntimeError(f"表 {tab} sql=NULL 但 {ncons} 约束 — 需手动 DDL")
                for (isql,) in t.execute(
                    "SELECT sql FROM duckdb_indexes() WHERE database_name='src' AND table_name=? AND sql IS NOT NULL", [tab]
                ).fetchall():
                    if isql:
                        t.execute(isql)
                if i % 20 == 0:
                    t.execute("CHECKPOINT")
            # 视图按定义重建 (在表之后); 依赖容忍: 重试到不再有进展 (处理视图引用视图)
            pending = [v for v in views if view_sql.get(v)]
            while pending:
                progressed = False
                still = []
                for v in pending:
                    try:
                        t.execute(view_sql[v])
                        progressed = True
                    except Exception:  # 依赖的视图还没建, 下轮再试
                        still.append(v)
                pending = still
                if not progressed:  # 一轮零进展 = 真错 (非依赖序问题)
                    raise RuntimeError(f"视图重建卡死, 无法创建: {pending}")
            t.execute("CHECKPOINT")
            t.execute("DETACH src")  # 关键: information_schema/duckdb_constraints/duckdb_indexes 跨所有 attach 库计数, 不 DETACH 会双倍计 src

            # 验证 (任一不齐 → 不换名)
            new_tables = t.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'").fetchone()[0]
            new_views = t.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW'").fetchone()[0]
            new_cons = t.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
            new_idx = t.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0]
            row_bad = [tab for tab in tables if t.execute(f'SELECT count(*) FROM "{tab}"').fetchone()[0] != base_rows[tab]]
            ok = (new_tables == len(tables) and new_views == len(views) and new_cons == base_cons
                  and new_idx == base_idx and not row_bad)
            print(f"  验证: 表 {len(tables)}->{new_tables} 视图 {len(views)}->{new_views} 约束 {base_cons}->{new_cons} 索引 {base_idx}->{new_idx} 行不齐={len(row_bad)}")
            if not ok:
                print(f"FAIL: 对账不齐, 不换名; 已删 {new.name} 及 .wal. 行不齐表: {row_bad[:5]}", file=sys.stderr)
                _discard_new(new, new_wal)
                return 5
        finally:
            t.close()
    except Exception:
        # M11: 建过 new 之后的任何异常 (DDL 缺失/视图重建卡死/连接冲突等) 都必须清
        # 掉 new 及 .wal 再往外抛——不留给下一次运行去撞"已存在"。
        _discard_new(new, new_wal)
        raise

    # 换名: 经共用的原子换名机制 (M3), 不再自己 rename 两次。keep_bak 给 drop_bak=False
    # 时才有意义 (硬链接保留旧 inode, 不复制)。
    try:
        swap_in_fresh_file(new, src, expected=expected, keep_bak=(None if drop_bak else bak))
    except SwapRefused as exc:
        print(f"FAIL: 换名被拒 reason={exc.reason} detail={exc.detail}; 删 {new.name}, {src.name} 未改动", file=sys.stderr)
        _discard_new(new, new_wal)
        return 8
    except Exception:
        # M11: SwapRefused 之外的异常 (os.link/os.replace 抛出的 OSError 如 ENOSPC 等)
        # 同样必须清掉 new/.wal 再往外抛——只挡 SwapRefused 会把这类失败也变成
        # 永久残留 (compact_swap_oserror.py repro: 注入 os.replace OSError)。
        _discard_new(new, new_wal)
        raise

    new_sz = src.stat().st_size / 1e9
    saved = sz - new_sz
    if drop_bak:
        print(f"\n  紧缩完成: {sz:.1f}G → {new_sz:.1f}G (省 {saved:.1f}G). 未保留 bak (默认；--keep-bak 保留硬链接)。")
    else:
        print(f"\n  紧缩完成: {sz:.1f}G → {new_sz:.1f}G (省 {saved:.1f}G). 按 --keep-bak 保留 {bak.name} (验证 doctor 后可删)。")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="smartmoney")
    ap.add_argument("--execute", action="store_true", help="真重写 (默认 dry-run)")
    ap.add_argument(
        "--keep-bak",
        action="store_true",
        help="换名并对账通过后保留 _precompact_bak（默认自动删除，不可回滚）",
    )
    args = ap.parse_args()
    if not args.execute:
        # dry-run 只读 (对账基线 + 打印计划), 不取写锁——与 build_price_kline_qfq_tushare.py
        # 的 --check-only 同一原则: 只读路径不必互斥, 也不该被另一个真写者挡住。
        sys.exit(run(args.db, args.execute, drop_bak=not args.keep_bak))
    try:
        with writer_lock("db_compact"):
            sys.exit(run(args.db, args.execute, drop_bak=not args.keep_bak))
    except WriterLockBusyError as exc:
        print(f"[db_compact] LOCK_BUSY: {exc}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
