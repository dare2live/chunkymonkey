"""deferred_findings 台账 — typed 读取层 (owner: backend/config/deferred_findings.yaml)。

**这是什么**: 施工中发现、但满足「不修不会写错数据 / 不挡当前判据 / 不是生产路径上
正在发生的红线违规」这三条的问题，登记进这里，而不是修掉或者只记在人的脑子里
（脑子会忘，下次重新发现要重新调研一次）。本模块只负责登记表的读取 + 校验 +
类型化——它不判断某个问题该不该登记（那是施工者/业主的判断），也不执行任何
detector（那是 detector 自己的事：一个门 id、一个脚本路径，或者 none）。

fail-closed：配置缺失/不合法（未知顶层键、未知条目键、缺必填键、id 不是 slug
或重复、日期格式不对、decision 不在词表里、detector 写成仓库路径却指向不存在
的文件）一律抛 ``ValueError``，不部分生效、不静默跳过坏条目——一份登记表如果
自己都不可信，就不配被当成"已知问题已经记下来了"的证据。

消费方: 目前只有 ``backend/tests/services/test_deferred_findings.py``。未来若有
门需要读这份台账（例如把 escalates_when 接成真的运行时检测），在这里加只读
访问函数，不要在别处重新写一份 yaml.safe_load。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

# 本文件位于 backend/services/deferred_findings.py —— parents[0]=services,
# parents[1]=backend, parents[2]=仓库根 (与 services/governance_gates.py 的
# REPO 写法一致)。
REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO / "backend" / "config" / "deferred_findings.yaml"

_TOP_LEVEL_KEYS = frozenset({"version", "decisions", "findings"})
_FINDING_KEYS = frozenset({"id", "asset", "detector", "escalates_when", "recorded", "decision"})
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# detector 写成仓库路径时的形状: 含 '/'，以 .py 或 .yaml 结尾，可选 ':<行号>' 后缀。
_DETECTOR_LINE_SUFFIX_RE = re.compile(r"^(?P<base>.+):(?P<line>\d+)$")


@dataclass(frozen=True)
class Finding:
    """登记表的一条「登记不修」条目。字段含义见 deferred_findings.yaml 头部注释。"""

    id: str
    asset: str
    detector: str
    escalates_when: str
    recorded: str
    decision: str


@dataclass(frozen=True)
class DeferredFindings:
    version: int
    decisions: tuple[str, ...]
    findings: tuple[Finding, ...]


def _is_valid_calendar_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _detector_repo_relpath(detector: str) -> str | None:
    """若 ``detector`` 长得像一个仓库路径引用 (含 '/'，剥掉可选 ':<行号>' 后缀后
    以 .py 或 .yaml 结尾)，返回去掉行号后缀的路径部分；否则返回 None (例如
    ``none``、门 id、无斜杠的裸文件名——这些不受 R10 存在性检查约束)。"""
    base = detector
    m = _DETECTOR_LINE_SUFFIX_RE.match(detector)
    if m:
        base = m.group("base")
    if "/" in base and (base.endswith(".py") or base.endswith(".yaml")):
        return base
    return None


def load_deferred_findings(
    path: Path | str | None = None,
    *,
    repo_root: Path | None = None,
) -> DeferredFindings:
    """加载并校验 ``backend/config/deferred_findings.yaml``。

    ``path`` 覆盖读取的 yaml 文件 (测试注入); ``repo_root`` 覆盖 detector
    仓库路径存在性检查所对照的根目录 (测试注入 tmp repo，不默认走仓库根—
    否则测试真实文件系统状态会让 R10 的隔离用例失去意义)。``repo_root``
    省略时默认用本文件推出的 ``REPO``。

    每条规则违反都抛 ``ValueError``，消息前缀 ``deferred_findings: R#`` 说明
    违反了哪一条 (与 vendor_scope.py 的 L# 前缀同一惯例)。
    """
    cfg_path = Path(path) if path is not None else DEFAULT_PATH
    root = Path(repo_root) if repo_root is not None else REPO
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # R1: 顶层键必须恰好是 {version, decisions, findings}——多一个少一个都 fail。
    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise ValueError(
            f"deferred_findings: R1 top-level keys must be exactly {sorted(_TOP_LEVEL_KEYS)}, "
            f"got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )

    # R2: version 是固定值 1 (未来若真的要 bump，loader 与 schema 一起改)。
    if raw.get("version") != 1:
        raise ValueError(f"deferred_findings: R2 version must be 1, got {raw.get('version')!r}")

    # R3: decisions 必须是字符串列表，条目非空且互不重复。
    decisions_raw = raw.get("decisions")
    if not isinstance(decisions_raw, list):
        raise ValueError(
            f"deferred_findings: R3 decisions must be a list, got {type(decisions_raw).__name__}"
        )
    seen_decisions: set[str] = set()
    for entry in decisions_raw:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f"deferred_findings: R3 decisions entries must be non-empty strings, got {entry!r}"
            )
        if entry in seen_decisions:
            raise ValueError(f"deferred_findings: R3 decisions has duplicate entry {entry!r}")
        seen_decisions.add(entry)
    decisions = tuple(decisions_raw)
    # 词表为空时不特殊放行——下面每条 finding 的 decision 字段仍必须落在这个
    # (此时为空的) 集合里，任何 finding 存在就必然在 R14 处 fail-closed；
    # findings 也为空时读取按空表成功返回，这是安全的 (没有条目可被误判)。
    valid_decisions = seen_decisions

    # R4: findings 必须是列表。
    findings_raw = raw.get("findings")
    if not isinstance(findings_raw, list):
        raise ValueError(
            f"deferred_findings: R4 findings must be a list, got {type(findings_raw).__name__}"
        )

    seen_ids: set[str] = set()
    findings: list[Finding] = []
    for idx, entry in enumerate(findings_raw):
        # R5: 每条 finding 必须是 mapping，且键恰好是 _FINDING_KEYS (覆盖未知键
        # 与缺必填键两种情况，同 vendor_scope.py L5 的写法)。
        if not isinstance(entry, dict) or set(entry) != _FINDING_KEYS:
            raise ValueError(
                f"deferred_findings: R5 findings[{idx}] keys must be exactly "
                f"{sorted(_FINDING_KEYS)}, got "
                f"{sorted(entry) if isinstance(entry, dict) else type(entry).__name__}"
            )

        finding_id = entry["id"]
        # R6: id 必须是小写 kebab slug。
        if not isinstance(finding_id, str) or not _ID_RE.match(finding_id):
            raise ValueError(
                f"deferred_findings: R6 findings[{idx}].id must match {_ID_RE.pattern!r}, "
                f"got {finding_id!r}"
            )
        # R7: id 全局唯一。
        if finding_id in seen_ids:
            raise ValueError(f"deferred_findings: R7 duplicate finding id {finding_id!r}")
        seen_ids.add(finding_id)

        asset = entry["asset"]
        # R8: asset 非空字符串。
        if not isinstance(asset, str) or not asset.strip():
            raise ValueError(
                f"deferred_findings: R8 findings[{finding_id!r}].asset must be a non-empty string"
            )

        detector = entry["detector"]
        # R9: detector 非空字符串。
        if not isinstance(detector, str) or not detector.strip():
            raise ValueError(
                f"deferred_findings: R9 findings[{finding_id!r}].detector must be a non-empty string"
            )
        # R10: detector 长得像仓库路径引用时，该文件必须真实存在 (fail-closed，
        # 防止台账自己先积累死引用)。
        repo_relpath = _detector_repo_relpath(detector)
        if repo_relpath is not None and not (root / repo_relpath).exists():
            raise ValueError(
                f"deferred_findings: R10 findings[{finding_id!r}].detector {detector!r} "
                f"looks like a repo path but {root / repo_relpath} does not exist"
            )

        escalates_when = entry["escalates_when"]
        # R11: escalates_when 非空字符串。
        if not isinstance(escalates_when, str) or not escalates_when.strip():
            raise ValueError(
                f"deferred_findings: R11 findings[{finding_id!r}].escalates_when must be a "
                "non-empty string"
            )

        recorded = entry["recorded"]
        # R12: recorded 必须是 YYYY-MM-DD 格式。
        if not isinstance(recorded, str) or not _DATE_RE.match(recorded):
            raise ValueError(
                f"deferred_findings: R12 findings[{finding_id!r}].recorded must match "
                f"{_DATE_RE.pattern!r}, got {recorded!r}"
            )
        # R13: recorded 必须是真实存在的日历日期 (拦 2026-02-30 这类格式对但日期
        # 不存在的值)。
        if not _is_valid_calendar_date(recorded):
            raise ValueError(
                f"deferred_findings: R13 findings[{finding_id!r}].recorded {recorded!r} is not "
                "a valid calendar date"
            )

        decision = entry["decision"]
        # R14: decision 必须落在顶层 decisions 词表里。
        if not isinstance(decision, str) or decision not in valid_decisions:
            raise ValueError(
                f"deferred_findings: R14 findings[{finding_id!r}].decision {decision!r} not in "
                f"decisions {sorted(valid_decisions)}"
            )

        findings.append(
            Finding(
                id=finding_id,
                asset=asset,
                detector=detector,
                escalates_when=escalates_when,
                recorded=recorded,
                decision=decision,
            )
        )

    return DeferredFindings(version=1, decisions=decisions, findings=tuple(findings))


__all__ = [
    "DeferredFindings",
    "Finding",
    "load_deferred_findings",
]
