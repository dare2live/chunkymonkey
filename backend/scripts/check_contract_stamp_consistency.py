"""check_contract_stamp_consistency — 契约戳一致性门 (2026-09-01).

# 这道门要防的四次真实事故 (今天发生, 全部实测记录):
#   第1次  改 daily/stock_st 契约 → 只打了 accepted_partition, 漏 canonical_* → 宣布"验证通过"
#   第2次  改 5 处契约          → 又只打 accepted_partition, 漏 canonical_* → 又宣布"验证通过"
#   第3次  补打 canonical       → 漏了 git 里 data/lineage/*.json 落盘快照 → CI 仍全绿
#   第4次  同步 ingest_batch    → 没算到 payload_hash 是从 contract_hash/config_hash 派生的
#          (margin_validation._batch_payload_hash) → 47,604 行改完连锁 LANDING_HASH_MISMATCH,
#          已回滚
#
# 共同根因: 改契约后, 没有一张"这个 hash 还存在于哪里"的完整地图 —— 每次"验证通过"
# 只验了当时想到的那一条路径。本门就是这张地图的机器化版本, 且**扫描面必须是发现式的**:
# 手写一张"我知道的存放点"清单, 换个地方长出同一个病 (今天已经犯了两次)。
#
# ── 四项检查 ────────────────────────────────────────────────────────────
#  ① pointer vs live contract   accepted_partition 里"当前 contract_version 组"的
#     contract_hash/config_hash 必须等于 load_*_contract() 现算值。旧 contract_version 组
#     刻意保留旧 hash (margin v2 / holders_top10 v2 等, 契约实体本就不同) —— 门不查它们。
#  ② canonical vs pointer       每张 canonical_* 表按 join 列 (通常 ingest_batch_id, 日历
#     用 generation_id) 关联 accepted_partition.batch_id, config_hash 必须与指针一致。
#     这是今天漏了两次的那一层。
#  ③ lineage 快照 vs live contract   data/lineage/**/*.json 里冻结的 dataset_id+config_hash
#     "戳记录" (发现式: 任何 dict 只要同时有 dataset_id + config_hash 两个 key 就算一条,
#     不看文件名)。可豁免 (快照就是用来锁住某一时刻的), 但豁免必须在本文件的
#     LINEAGE_SNAPSHOT_EXEMPTIONS 显式声明 + 理由, 不许代码里静默跳过。
#  ④ ingest_batch 派生链         ingest_batch.contract_hash/config_hash **不查是否等于
#     当前契约** —— 它们是落地时刻的封印, 按设计就该停在旧值 (第4次事故的教训: 有人把它们
#     "同步"到新契约, 但没有同步 payload_hash, 于是 payload_hash 的重算结果与库里存的值对不
#     上 → 这才是真正的信号)。做法: 用产线同一套哈希函数 (margin_validation._batch_payload_hash
#     / security_day_partition.stable_json+sha256_text / calendar_landing._batch_payload)
#     对每个 (dataset_id) 抽样最近 N 个 batch 现场重算 payload_hash, 与库里存的
#     payload_hash 比对。有人touch 了 contract_hash/config_hash 而没有同步重算 payload_hash
#     (或反过来只改了 payload_hash) → 重算失败 → FAIL。row_signatures 从对应 landing 表按
#     production 完全相同的格式重建 (ordinal:hash / frag:ordinal:hash), 不是猜的公式 ——
#     已对 7 个真实域逐一实测重算值与库里 payload_hash 逐字节相等 (2026-09-01 验证)。
#  ⑤ 文件级摘要钉住 (2026-09-01 补, fable 实测发现的第二层, 见 SECTION 8.5 详注)
#     除了①②③④这类"字段级"hash (contract_hash/config_hash/payload_hash 存在表列/JSON
#     字段里), 还存在"文件级"钉法: 某个 JSON 字段是**另一个文件整体字节**的 sha256
#     (典型样例 data/lineage/phase_d_experiment_runs/manifest.json 的 snapshot_hash
#     字段钉着 disclosure_dataset_snapshot.json 的全文摘要)。字段级扫描 (③) 天然扫不到
#     这层, 因为它按 dataset_id+config_hash 配对, 而文件级钉子没有 dataset_id。
#     发现式配对判据: hash 形字段 (key 含 hash, value 64 位十六进制) + 路径形字段 (value
#     是字符串、含 '/'、以已知文件后缀结尾) 在同一 JSON 节点内按 key 前缀亲缘配对, 不按
#     键名硬编码枚举 (不是维护一张 ["snapshot_hash","sha256","file_hash"] 清单)。
#     2026-09-01 实测: 10 个此类钉子里 9 个已经指向字节已变的文件而未更新 (只有 1 个
#     被某条窄测试盯着, 其余静默烂掉) —— 本检查独立发现全部 10 个, 不依赖那条窄测试。
#  ⑥ 供应商集合五处一致性 (占位接口, 见 SECTION 8.6) —— channels 尚未施工, 默认不接入
#     执行; 骨架已就位, --only 6 显式启用会报 WARN "五处 loader 待补"。
#
# ── 发现式扫描 (硬性设计要求, 不是"锦上添花") ───────────────────────────────
#   - discover_hash_columns(): 扫三个库(经 database_manifest 发现, 非硬编码库名) 的
#     information_schema.columns, 找全部 %hash% 列, 用 classify_hash_column() 分类;
#     分类不出的 → UNCLASSIFIED, 报 WARN (不静默)。
#   - discover_lineage_stamp_records(): 扫 data/lineage/ 下**全部** *.json (glob 递归,
#     不是一张写死的文件名清单), 找 dataset_id+config_hash 同时出现的 dict 节点。
#     dataset_id 不在 DOMAIN_REGISTRY 里的 → 报 WARN "发现未注册域"。
#   - discover_file_digest_pins(): 同样扫 data/lineage/ 下全部 *.json, 找"钉住其他文件
#     整文件摘要"的字段对 (见 ⑤); 配不上对的孤儿字段报 WARN, 不静默。
#
# ── 执行位置 (设计判断, 由接线方最终决定, 此处只record推荐) ──────────────────
#   建议分组 = diff_correctness (阻断), 理由见本文件底部 RECOMMENDATION 注释。
#   建议触发条件 = 本次 commit staged 路径命中 TRIGGER_PATH_GLOBS 时才做实质检查,
#   否则快速 PASS (契约文件 = *_contract.py / *_schema.py / contracts.py /
#   calendar_contract.py / calendar_schema.py + sync_registry.yaml)。
#   本脚本自身不接线 safe_commit —— 接线由另一个 agent 负责 (见交付说明)。
#
# 用法:
#   PYTHONPATH=backend python backend/scripts/check_contract_stamp_consistency.py
#   ... --json                              机器读
#   ... --only 1,2,3,4                      只跑指定检查号 (调试/演示单个缺陷用)
#   ... --sample-per-domain 20              check④ 每域抽样 batch 数 (默认见 DEFAULT_SAMPLE_PER_DOMAIN)
#   ... --db-override tushare_raw=/path.db  覆盖某 db_alias 的连接目标 (测试注入基线/造靶副本)
#   ... --lineage-dir /path/to/lineage      覆盖 data/lineage 根目录 (测试注入)
#
# 退出码: 0 = 全 PASS (允许有 WARN); 1 = 至少一项 FAIL。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.data_sources.stamp_types import (  # noqa: E402
    DbUnavailable,
    DomainSpec,
    DOMAIN_BY_DATASET_ID,
    DOMAIN_REGISTRY,
    Finding,
)
from services.data_sources.stamp_discovery import (  # noqa: E402
    classify_hash_column,
    discover_databases,
    discover_file_digest_pins,
    discover_lineage_stamp_records,
)
from services.data_sources.stamp_checks import (  # noqa: E402
    check_canonical_vs_pointer,
    check_channel_set_consistency,
    check_discovery,
    check_ingest_batch_derivation,
    check_lineage_snapshots,
    check_pointer_vs_contract,
)

DEFAULT_LINEAGE_DIR = REPO / "data" / "lineage"
DEFAULT_SAMPLE_PER_DOMAIN = 10


# ============================================================================
# SECTION 8.5b — Check ⑤: 文件级摘要钉住 (发现机制在 stamp_discovery.py,
# 本函数原样留在本文件 —— 见 stamp_checks.py 模块顶部说明: 现有单测用
# `monkeypatch.setattr(ccsc, "REPO", tmp_path)` 直接改写本模块的全局 REPO 来
# 控制它解析目标文件的基准目录, 而 Python 全局名字查找永远落在函数的定义模块,
# 挪到别处会让那 4 个测试的 monkeypatch 失效。发现式配对判据/五处说明详见
# stamp_discovery.py 的 SECTION 8.5 注释。
# ============================================================================


def check_file_digest_pins(lineage_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    candidates, orphans = discover_file_digest_pins(lineage_dir)
    for msg in orphans:
        findings.append(Finding("discovery", "WARN", None, f"文件级摘要钉住发现式扫描: {msg}"))
    seen: set[tuple[Path, str, str]] = set()
    for c in candidates:
        dedup_key = (c.file, c.hash_value, c.target_relpath)
        if dedup_key in seen:
            continue  # 同一对 (钉住值, 目标路径) 在同文件出现多次 (如 manifest+每域 verdict 重复引用同一快照) 只需判一次
        seen.add(dedup_key)
        target = REPO / c.target_relpath
        try:
            rel_source = c.file.relative_to(REPO)
        except ValueError:
            rel_source = c.file
        if not target.is_file():
            findings.append(
                Finding(
                    "5_file_digest", "WARN", None,
                    f"{rel_source}:{c.pointer}/{c.hash_key} 钉着 {c.target_relpath}, "
                    "但该文件在仓库里不存在 (路径可能已改名/移动, 或钉住的是历史路径)",
                )
            )
            continue
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except Exception as exc:  # noqa: BLE001
            findings.append(
                Finding("5_file_digest", "WARN", None, f"{rel_source}: 无法读取 {c.target_relpath} 现算摘要 ({exc})")
            )
            continue
        if actual != c.hash_value:
            findings.append(
                Finding(
                    "5_file_digest", "FAIL", None,
                    f"{rel_source}:{c.pointer}/{c.hash_key}={c.hash_value[:16]}.. 钉住的整文件摘要与 "
                    f"{c.target_relpath} 现算摘要={actual[:16]}.. 不符 —— 被钉文件的字节已经变了 "
                    "(常规重生也算), 这份钉住的引用没有跟着更新",
                )
            )
        else:
            findings.append(
                Finding(
                    "5_file_digest", "INFO", None,
                    f"{rel_source}:{c.pointer}/{c.hash_key} 与 {c.target_relpath} 现算摘要一致",
                )
            )
    return findings


# ============================================================================
# SECTION 11 — 编排 + CLI
# ============================================================================


def run_all_checks(
    *,
    only: set[str] | None = None,
    sample_per_domain: int = DEFAULT_SAMPLE_PER_DOMAIN,
    db_override: dict[str, str] | None = None,
    lineage_dir: Path = DEFAULT_LINEAGE_DIR,
    lineage_exemptions: dict[str, str] | None = None,
    domains: tuple[DomainSpec, ...] = DOMAIN_REGISTRY,
    allow_missing_db: bool = False,
) -> list[Finding]:
    """allow_missing_db: 2026-09-02 修 (fable 验收发现的恒绿口子) —— 默认 False:
    某个域的 db_alias 库缺 accepted_partition/ingest_batch 表时, 该域检查①②④视为 FAIL
    (无法验证 != 通过, fail-closed)。显式传 True 才降级为 WARN (仅供已知、有意的排查场景
    使用, 不应该是任何自动化路径的默认值)。"""
    only = only or {"1", "2", "3", "4", "5"}
    findings: list[Finding] = []

    relevant_aliases = {d.db_alias for d in domains}
    db_entries = discover_databases(db_override=db_override, relevant_aliases=relevant_aliases)
    conn_by_alias: dict[str, Any] = {}
    db_unavailable_by_alias: dict[str, DbUnavailable] = {}
    for alias, path, conn_or_status in db_entries:
        if isinstance(conn_or_status, DbUnavailable):
            db_unavailable_by_alias[alias] = conn_or_status
            if conn_or_status.reason == "missing_tables":
                severity = "WARN" if allow_missing_db else "FAIL"
            else:
                severity = "WARN"  # 真不可达 (写锁/瞬时) 默认不阻断, 与 grain_uniqueness 同惯例
            findings.append(
                Finding("discovery", severity, None, f"db_alias={alias} ({path}): {conn_or_status.detail}")
            )
            continue
        conn_by_alias[alias] = conn_or_status
        findings.extend(check_discovery(conn_or_status, alias))

    contract_by_domain: dict[str, tuple[Any, str | None]] = {}
    for domain in domains:
        try:
            contract_by_domain[domain.name] = (domain.load_contract(), None)
        except Exception as exc:  # noqa: BLE001
            contract_by_domain[domain.name] = (None, str(exc)[:200])

    for domain in domains:
        conn = conn_by_alias.get(domain.db_alias)
        contract, load_err = contract_by_domain[domain.name]
        if conn is None:
            status = db_unavailable_by_alias.get(domain.db_alias)
            reason = status.reason if status is not None else "unknown"
            if reason == "missing_tables":
                severity = "WARN" if allow_missing_db else "FAIL"
                msg = (
                    f"db_alias={domain.db_alias} 缺 accepted_partition/ingest_batch 表 "
                    f"(见上方 discovery finding) —— 该域检查①②④全部无法验证, 按 fail-closed 处理"
                )
            else:
                severity = "WARN"
                msg = f"db_alias={domain.db_alias} 不可达 (见上方 discovery finding), 跳过该域全部检查"
            findings.append(Finding("1_pointer", severity, domain.name, msg))
            continue
        if load_err is not None:
            findings.append(
                Finding("1_pointer", "WARN", domain.name, f"现算契约失败, 跳过该域检查①②④ ({load_err})")
            )
        else:
            if "1" in only:
                findings.extend(check_pointer_vs_contract(conn, domain, contract))
            if "2" in only:
                findings.extend(check_canonical_vs_pointer(conn, domain, contract))
        if "4" in only:
            findings.extend(check_ingest_batch_derivation(conn, domain, sample_per_domain))

    if "3" in only:
        findings.extend(check_lineage_snapshots(lineage_dir, lineage_exemptions))
    if "5" in only:
        findings.extend(check_file_digest_pins(lineage_dir))
    if "6" in only:
        findings.extend(check_channel_set_consistency(enabled=True))

    for conn in conn_by_alias.values():
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="契约戳一致性门 — 发现式扫描四类今天真实发生过的漂移")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--only", default="1,2,3,4,5",
        help="只跑指定检查号, 逗号分隔 (调试/演示单个缺陷); 6=供应商集合占位检查, 默认不含 (channels 未施工)",
    )
    ap.add_argument("--sample-per-domain", type=int, default=DEFAULT_SAMPLE_PER_DOMAIN)
    ap.add_argument(
        "--db-override", action="append", default=[],
        help="alias=/path/to.duckdb, 可重复 (测试注入基线/造靶副本)",
    )
    ap.add_argument("--lineage-dir", type=Path, default=DEFAULT_LINEAGE_DIR)
    ap.add_argument(
        "--allow-missing-db", action="store_true",
        help="域的 db_alias 库缺 accepted_partition/ingest_batch 表时降级为 WARN (默认 FAIL, "
        "fail-closed)。仅供已知、有意的排查场景显式开启, 不要在自动化路径默认打开。",
    )
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    db_override: dict[str, str] = {}
    for item in args.db_override:
        if "=" not in item:
            raise SystemExit(f"--db-override 格式错: {item!r} (需 alias=/path)")
        alias, path = item.split("=", 1)
        db_override[alias.strip()] = path.strip()

    findings = run_all_checks(
        only=only,
        sample_per_domain=args.sample_per_domain,
        db_override=db_override,
        lineage_dir=args.lineage_dir,
        allow_missing_db=args.allow_missing_db,
    )

    fails = [f for f in findings if f.severity == "FAIL"]
    warns = [f for f in findings if f.severity == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [f.to_dict() for f in findings],
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                },
                ensure_ascii=False, indent=1,
            )
        )
    else:
        for f in findings:
            if f.severity == "INFO":
                continue
            print(f"[{f.severity}] ({f.check}) domain={f.domain}: {f.detail}")
        print(
            f"contract-stamp-consistency: {len(findings)} findings, "
            f"{len(fails)} FAIL, {len(warns)} WARN"
        )
        if not fails and not warns:
            print("[contract-stamp-consistency] PASS")

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())


# ============================================================================
# RECOMMENDATION (非本脚本执法, 供接线方参考; 本脚本本身不改 governance_gates.yaml
# / commit_tiers.yaml / safe_commit.sh —— 那些文件今天有并发 agent 在改, 边界见交付说明)
#
# 建议分组: diff_correctness (阻断), 不是 system_health。
#
# 张力: governance_gates.yaml 开头"谁受害、何时受害"的分组哲学下, 本门读的是 DB 里的
# 真实数据状态 (accepted_partition/canonical_*/ingest_batch 行, lineage 落盘快照),
# 表面上很像 grain_uniqueness/continuity 那类"数据地基是否健康"的 system_health 检查
# (受害者=系统运行时, 应该挂 daily_update 而不是拦 commit)。
#
# 但判据不是"是否读 DB", 是"这次要查的状态是不是这次 diff 造成的、且伤害在 commit 那一刻
# 就已经完成":
#   - grain_uniqueness 查的是"库里现在有没有重复组" —— 与哪次 commit 无关, 是随时间推移
#     各种写入累积的结果, 受害时刻是下次 MERGE 跑批 (未来), 不是任何一次 commit。
#   - 本门查的是"这次改契约的 diff, 有没有连带打全 canonical/lineage/pointer" ——
#     如果没打全, 错误状态在 git commit 落地那一刻就已经完整存在 (下一个 pull 这份代码的
#     人立刻就会读到一个自相矛盾的契约声明), 不需要等"系统跑起来"才发作。这与已经登记为
#     diff_correctness 的 lineage_drift 同型 ("漂移的成因是这次 diff 改了 registry/schema
#     却没重生血缘——纯 diff 正确性"), 也与 moth_invariants 同型 ("违反它意味着数据/平台
#     此刻就是错的")。
#   - 触发条件本身就是"诊断变量"而非"数据地基是否健康"的问题: 只在 staged diff 命中契约
#     定义文件时才做实质检查 (见文件头 TRIGGER_PATH_GLOBS 建议), 其余绝大多数 commit
#     是 O(1) 空转 —— 这正是 diff_correctness 组的形状 (查这次改动本身对不对), 不是
#     system_health 组的形状 (无条件周期性扫全库)。
#   - fable 的窗口论据: 今天两次事故都发生在"改契约"与"补戳"之间的窗口; 若挂
#     system_health (daily_update 夜间跑), 这个窗口在白天整个开发 session 里都不设防,
#     而错误状态一旦被下一次 commit 叠加 (比如再改一处契约), 排查成本随时间推移非线性
#     上升。拦在 commit 就是拦在成本最低的时刻。
#
# 建议触发路径清单 (data-driven, 供接线方抄入 commit_tiers.yaml 触发判断或 safe_commit.sh):
#   backend/services/data_sources/*_contract.py
#   backend/services/data_sources/*_schema.py
#   backend/services/data_sources/contracts.py
#   backend/services/data_sources/calendar_contract.py
#   backend/services/data_sources/calendar_schema.py
#   backend/services/data_sources/accepted_schema.py
#   backend/services/data_sources/availability.py
#   backend/services/market_schema.py
#   backend/config/sync_registry.yaml
# 命中以上任一路径时才需要跑 check①②④ (它们依赖现算契约); check③ 一直很快 (~1.5s 扫
# 3600+ 文件), 可以无条件跑。
# ============================================================================
