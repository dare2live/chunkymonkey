"""check_contract_stamp_consistency 的发现式扫描机制 (2026-09-02 拆分).

从 check_contract_stamp_consistency.py 拆出 (原 1355 行触发
minimal-module-no-new-godfile god-file 断言)。本模块是三段互不相干职责里
"最值得能被独立推理和测试的部分"——发现式扫描: 库/hash 列发现、lineage JSON
戳记录发现、文件级摘要钉住配对发现。不含"拿发现结果去核对现算契约"的检查逻辑
(那部分在 stamp_checks.py)。

拆分本身是纯结构重排, 零行为变化; 各函数/常量的说明文字原样搬自原文件对应
SECTION, 未作任何逻辑改动。
"""
from __future__ import annotations

import glob as globmod
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from services.database_manifest import get_database_manifest
from services.data_sources.stamp_types import DbUnavailable

REPO = Path(__file__).resolve().parents[3]
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# ============================================================================
# SECTION 4 — 发现式扫描
# ============================================================================


_REQUIRED_TABLES = frozenset({"accepted_partition", "ingest_batch"})


def discover_databases(
    *,
    db_override: dict[str, str] | None = None,
    relevant_aliases: set[str] | None = None,
) -> list[tuple[str, str, Any]]:
    """经 database_manifest 发现(非硬编码库名列表)哪些 db alias 真的有
    accepted_partition + ingest_batch 表, 返回 [(alias, path, conn_or_DbUnavailable)]。

    relevant_aliases: 只探测这些 alias (run_all_checks 传 DOMAIN_REGISTRY 实际用到的
    db_alias 集合)。留 None 时探测 manifest 里全部 alias —— 仅供独立诊断/测试用,
    生产路径必须传 relevant_aliases, 否则 database_manifest.yaml 里与本门契约戳体系
    毫无关系的库 (market.duckdb/reference.duckdb/feature_store.duckdb/experiment_store.duckdb
    等, 它们从设计上就不含 accepted_partition/ingest_batch) 也会被当成"缺表"报出来,
    制造与事实不符的噪音。

    db_override: {alias: path} 测试注入 (指向 scratchpad 副本/基线备份); 传了 override
    的 alias 总会被探测, 不受 relevant_aliases 限制 (显式覆盖即显式要求测它)。
    """
    manifest = get_database_manifest()
    override = db_override or {}
    if relevant_aliases is None:
        aliases = set(manifest.databases.keys()) | set(override.keys())
    else:
        aliases = set(relevant_aliases) | set(override.keys())

    found: list[tuple[str, str, Any]] = []
    for alias in sorted(aliases):
        spec = manifest.databases.get(alias)
        path = override.get(alias)
        if path is None:
            if spec is None or not spec.path:
                found.append((
                    alias, "<unresolved>",
                    DbUnavailable(
                        "unreachable",
                        f"alias {alias!r} 未传 --db-override, 且在 database_manifest.yaml 里"
                        "不存在或是 path_glob 型条目 (多分片, 本门不支持)",
                    ),
                ))
                continue
            path = str(spec.resolve_path(repo_root=REPO))
        try:
            conn = duckdb.connect(  # rule-compliance: ok evidence=read_only 契约戳一致性审计连接, 不写
                path, read_only=True
            )
        except Exception as exc:  # noqa: BLE001 — 库不可达 (写锁占用等), 记录不崩溃
            found.append((alias, path, DbUnavailable("unreachable", str(exc)[:200])))
            continue
        try:
            tabs = {
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            }
        except Exception as exc:  # noqa: BLE001
            found.append((alias, path, DbUnavailable("unreachable", str(exc)[:200])))
            continue
        missing = _REQUIRED_TABLES - tabs
        if not missing:
            found.append((alias, path, conn))
        else:
            conn.close()
            found.append((
                alias, path,
                DbUnavailable(
                    "missing_tables",
                    f"库可达但缺表: {sorted(missing)} (库本身连接正常, 不是权限/写锁问题; "
                    "可能是表被改名/退役, 或 DomainSpec.db_alias 配错了库)",
                ),
            ))
    return found


def discover_hash_columns(conn: Any) -> list[tuple[str, str]]:
    """当前连接里全部 %hash% 列 (information_schema, 大小写不敏感)。"""
    rows = conn.execute(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE lower(column_name) LIKE '%hash%'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ============================================================================
# SECTION 3 — hash 列分类规则 (发现式扫描配套的分类器; UNCLASSIFIED = 必须 WARN,
# 不能静默吞掉 —— 这正是今天漏掉的那类地方)。
# ============================================================================

# 精确 (table, column) 分类 —— 契约戳生命周期上的已知角色。
_EXACT_COLUMN_ROLES: dict[tuple[str, str], str] = {
    ("accepted_partition", "contract_hash"): "contract_stamp_pointer",
    ("accepted_partition", "config_hash"): "contract_stamp_pointer",
    ("accepted_partition", "content_hash"): "row_content_hash",
    ("ingest_batch", "contract_hash"): "ingest_batch_frozen_stamp",
    ("ingest_batch", "config_hash"): "ingest_batch_frozen_stamp",
    ("ingest_batch", "payload_hash"): "ingest_batch_derived_hash",
    ("ingest_batch", "canonical_hash"): "ingest_batch_derived_hash",
}

# 已知与"契约戳"体系无关的存放点 (另一套子系统的 hash, 例如 lineage SQL 指纹 / 增量水位
# 游标) —— 显式登记 + 理由, 而不是靠命名巧合躲过发现式扫描。
_OUT_OF_SCOPE_COLUMNS: dict[tuple[str, str], str] = {
    ("mart_data_lineage", "sql_hash"): "血缘子系统的 SQL 文本指纹, 与数据集契约身份无关",
    ("mart_lineage", "sql_hash"): "同上 (mart_lineage 是 mart_data_lineage 的同义/历史表)",
    ("mart_data_source_watermark", "last_raw_hash"): "增量同步水位游标, 不是契约戳",
}


def classify_hash_column(table: str, column: str) -> str:
    """discover_hash_columns() 配套分类器。返回值语义:
    - contract_stamp_pointer / ingest_batch_frozen_stamp / ingest_batch_derived_hash
      / canonical_stamp / row_content_hash / out_of_scope: 本门已知如何处理或明确排除。
    - UNCLASSIFIED: 发现了一个新的存 hash 的地方, 配置里没登记 —— 必须 WARN, 不许吞。
    """
    key = (table, column)
    if key in _EXACT_COLUMN_ROLES:
        return _EXACT_COLUMN_ROLES[key]
    if key in _OUT_OF_SCOPE_COLUMNS:
        return "out_of_scope"
    if table.startswith("canonical_"):
        if column == "config_hash":
            return "canonical_stamp"
        if column == "source_row_hash":
            return "row_content_hash"
        return "UNCLASSIFIED"
    if table.startswith("landing_"):
        if column in ("row_hash", "fragment_hash"):
            return "row_content_hash"
        return "UNCLASSIFIED"
    return "UNCLASSIFIED"


def discover_lineage_files(lineage_dir: Path) -> list[Path]:
    """data/lineage/ 下全部 *.json (递归 glob, 不是写死清单)。"""
    if not lineage_dir.is_dir():
        return []
    return sorted(Path(p) for p in globmod.glob(str(lineage_dir / "**" / "*.json"), recursive=True))


def _walk_stamp_records(obj: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "dataset_id" in obj and "config_hash" in obj:
            out.append(
                {
                    "pointer": path,
                    "dataset_id": obj.get("dataset_id"),
                    "config_hash": obj.get("config_hash"),
                    "contract_hash": obj.get("contract_hash"),
                    "contract_version": obj.get("contract_version"),
                }
            )
        for key, value in obj.items():
            _walk_stamp_records(value, f"{path}/{key}", out)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk_stamp_records(value, f"{path}[{index}]", out)


def discover_lineage_stamp_records(
    lineage_dir: Path,
) -> tuple[dict[Path, list[dict[str, Any]]], list[tuple[Path, str]]]:
    """扫描全部 lineage JSON, 找 dataset_id+config_hash 同时出现的节点 (发现式,
    不看文件名)。返回 (file -> [records], [(file, parse_error)])。"""
    by_file: dict[Path, list[dict[str, Any]]] = {}
    errors: list[tuple[Path, str]] = []
    for f in discover_lineage_files(lineage_dir):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append((f, str(exc)[:200]))
            continue
        records: list[dict[str, Any]] = []
        _walk_stamp_records(payload, "$", records)
        if records:
            by_file[f] = records
    return by_file, errors


# ============================================================================
# SECTION 8.5 — Check ⑤: 文件级摘要钉住 (2026-09-01 补, fable 实测发现的第二层)
#
# 契约戳有**两个层级**, 本门原先只覆盖第一层:
#   ① 字段级: contract_hash / config_hash / payload_hash 存在表列和 JSON 字段里 (① ② ③ ④)
#   ② 文件级: 某个文件里钉着"另一个文件全文字节的 sha256" (典型键名 snapshot_hash +
#      snapshot_relpath, 或 b0_artifact_hash + b0_artifact_relpath) —— 改了被钉文件的
#      字节 (哪怕只是常规重生), 这层摘要立刻失效, 而它长得完全不像 dataset_id+config_hash
#      的"契约戳", 字段级扫描天然扫不到它。
#
# 2026-09-01 实测: data/lineage/phase_d_experiment_runs/manifest.json 的 snapshot_hash
# 钉着 disclosure_dataset_snapshot.json 的整文件 sha256, 目前已经对不上 (该快照文件被
# 另一个 agent 重生过, 字节变了); phase_e_experiment_verdicts/ 5 个 + phase_f_experiment_
# verdicts/ 4 个文件同款钉法, 其中只有 1 个此前被某条窄测试 (test_committed_artifact_
# matches_live_sources) 盯着, 其余 9 个完全没有测试在看 —— 静默烂掉。
#
# 发现方法 (不许按键名硬编码清单, 用结构判据):
#   1. 对每个 JSON dict 节点, 收集"hash 形字段" (key 含 'hash' 且 value 是 64 位小写十六进制)
#      和"路径形字段" (value 是字符串, 含 '/', 且以常见文件后缀结尾, 例如 .json)。
#   2. 按 key 前缀亲缘配对 (剥掉 hash 字段的 'hash'/'_hash' 后缀、路径字段的 'relpath'/
#      '_relpath'/'path'/'_path' 后缀, 前缀相同则配对); 若一个节点只有恰好 1 个 hash 形 +
#      1 个路径形字段, 也配对 (无歧义场景, 不强求前缀相同)。
#   3. 配不上对的 hash 形/路径形字段单独报 WARN (不是"找不到就算了", 是"这里有个不认识的
#      东西, 人来判断")。
#   4. 每一对: 把路径形字段的值当仓库相对路径解出目标文件, 现算它的整文件 sha256, 与
#      钉住的值比对。目标文件不存在 / 不可读 → WARN。摘要不吻合 → FAIL (不区分"是否有
#      别的窄测试也在盯" —— 本门自己就是那张完整地图, 不依赖运气式的窄测试覆盖)。
# ============================================================================

_PATH_LIKE_SUFFIXES = (".json", ".yaml", ".yml", ".csv", ".parquet", ".py", ".md", ".duckdb")


def _hash_like_fields(node: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in node.items()
        if isinstance(v, str) and "hash" in k.lower() and SHA256_HEX_RE.match(v)
    ]


def _path_like_fields(node: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in node.items()
        if isinstance(v, str) and "/" in v and v.lower().endswith(_PATH_LIKE_SUFFIXES)
    ]


def _stem_hash_key(key: str) -> str:
    k = key.lower()
    for suf in ("_hash", "hash"):
        if k.endswith(suf):
            return k[: -len(suf)].rstrip("_")
    return k


def _stem_path_key(key: str) -> str:
    k = key.lower()
    for suf in ("_relpath", "relpath", "_path", "path"):
        if k.endswith(suf):
            return k[: -len(suf)].rstrip("_")
    return k


@dataclass(frozen=True)
class DigestPinCandidate:
    file: Path
    pointer: str
    hash_key: str
    hash_value: str
    path_key: str
    target_relpath: str


def _walk_digest_pin_candidates(
    obj: Any, path: str, source_file: Path, out: list[DigestPinCandidate], orphans: list[str]
) -> None:
    if isinstance(obj, dict):
        hash_fields = _hash_like_fields(obj)
        path_fields = _path_like_fields(obj)
        if hash_fields and path_fields:
            paired_hash_keys: set[str] = set()
            paired_path_keys: set[str] = set()
            if len(hash_fields) == 1 and len(path_fields) == 1:
                hk, hv = hash_fields[0]
                pk, pv = path_fields[0]
                out.append(DigestPinCandidate(source_file, path, hk, hv, pk, pv))
                paired_hash_keys.add(hk)
                paired_path_keys.add(pk)
            else:
                for hk, hv in hash_fields:
                    stem = _stem_hash_key(hk)
                    matches = [(pk, pv) for pk, pv in path_fields if _stem_path_key(pk) == stem]
                    if len(matches) == 1:
                        pk, pv = matches[0]
                        out.append(DigestPinCandidate(source_file, path, hk, hv, pk, pv))
                        paired_hash_keys.add(hk)
                        paired_path_keys.add(pk)
            for hk, _ in hash_fields:
                if hk not in paired_hash_keys:
                    orphans.append(f"{source_file}:{path}/{hk} 是 hash 形字段但配不到路径形兄弟字段")
            for pk, _ in path_fields:
                if pk not in paired_path_keys:
                    orphans.append(f"{source_file}:{path}/{pk} 是路径形字段但配不到 hash 形兄弟字段")
        for key, value in obj.items():
            _walk_digest_pin_candidates(value, f"{path}/{key}", source_file, out, orphans)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk_digest_pin_candidates(value, f"{path}[{index}]", source_file, out, orphans)


def discover_file_digest_pins(
    lineage_dir: Path,
) -> tuple[list[DigestPinCandidate], list[str]]:
    """发现式扫描: lineage 下全部 JSON 里"钉住另一个文件整文件 sha256"的字段对。"""
    candidates: list[DigestPinCandidate] = []
    orphans: list[str] = []
    for f in discover_lineage_files(lineage_dir):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 解析错误已在 check_lineage_snapshots 报过, 这里不重复
            continue
        _walk_digest_pin_candidates(payload, "$", f, candidates, orphans)
    return candidates, orphans


