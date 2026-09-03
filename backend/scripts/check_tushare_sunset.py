#!/usr/bin/env python3
"""TuShare 授权到期风险门 (tushare_sunset.yaml 与 sync_registry.yaml 对账).

owner: backend/config/tushare_sunset.yaml + 本文件。

**根因**: TuShare 授权 2026-09-10 到期，不续期。tushare.py 在 expires_at <= now 时
直接 raise TuShareAuthorizationError —— 硬停不是降级。此前三月 primitives/seed.py 的
涨跌停规则过时无人发现，正因为它零消费/零执法。**没有门的清单必然烂掉**。

**2026-09-04 收口 (/tmp/fable_doc_consolidation.md §7.5)**: 38 个域条目里出现过 18 个
不同的键，而本门此前只读 3 个 (decision/status/replacement) + 顶层 2 个
(authorization_expires/undecided_domains)——机器读 3 个，人写了 18 个。没有闭合键集的
登记表必然漂：一次执行 agent 为了记一次裁决偏离，往台账里加了 verdict_deviation/
frozen_at/must_by 三个新字段，门跑出 WARN fails=0——因为它压根不校验未知键。这次收口
把台账收敛成"机器读得到的字段 + 一个自由文本槽"，具体动作:
  - 闭合键集 (KNOWN_KEYS): 未知键 = FAIL (检查 5)
  - 字段-决策矩阵: 哪个 decision 允许哪些额外字段 (检查 6)
  - 同一 target_table 的域必须同 decision (检查 7): 物理表不可能一半冻结一半换源
  - accept / accept_outage 两个值合并成 freeze: 二者的区分点"有没有活跃消费方"
    应由 `chunkyctl lineage impact` 机器判定，手写在这份台账里已经判错三次
    (moneyflow/index_dailybasic/cyq_perf 手写 accept 但实测均有活跃消费方)
  - undecided_domains 从 domains 字典里的一个"列表伪装成条目"上移到顶层

**检查项 (0~7)**:
0. decision 值域校验: tushare_sunset.yaml 每个域条目的 decision 字段必须在
   LEGAL_DECISIONS 合法集里；缺字段和写了非法值（如历史遗留的 unknown）分别报不同的
   **FAIL** 文案，不许合并成同一种错误。

1. 覆盖完整性: sync_registry.yaml 里每个 source: tushare 的域，必须在 tushare_sunset.yaml
   里出现（要么有自己的条目，要么列在顶层 undecided_domains）。遗漏 → **FAIL** (真漂移)。

2. 反向自清: tushare_sunset.yaml 里提到的域，若在 registry 里已不是 source: tushare
   （已换源或已退役），应报 **WARN** —— 但 decision: derive/replace 且 status: done
   的条目除外（已完成的记录，正常）。

3. 到期倒计时: 比较 authorization_expires 与当天，未裁决域 = 顶层 undecided_domains
   列表 ∪ 所有 decision == "undecided" 的条目（两个来源取并集，任一来源缺了都会漏报）：
   - 未裁决域数 > 0 且距到期 > 14 天 → WARN (列出全部未裁决域名)
   - 未裁决域数 > 0 且距到期 ≤ 14 天 → WARN 但消息升级为醒目提示
   - 已过期 + 仍有未裁决域 → **FAIL**

4. 裁决执行度: 域在 registry 中仍是 source: tushare，且台账 decision ∈ {replace, derive}
   且 status != done —— 即"声明要换源却没换"。报 **WARN**（不 FAIL：业主已明确解除
   断流时限压力，这条检查的价值是让声明与实际的漂移可见，不是催今天切换）。一条汇总
   warn 列出全部命中域，不逐域刷屏。

5. 闭合键集: 每个域条目的字段必须 ⊆ KNOWN_KEYS。未知键 → **FAIL**（列出域名+未知键+
   合法键集）。理由: 一个字段能留在这张表只有两条路——(a) 至少一个 loader 读它且写错/
   缺失时有门/测试会红 (b) 它是这张表唯一的自由文本槽 evidence。不满足任一条的字段
   由本检查拒绝，逼着"记录一下"的人要么给它接一个 loader，要么把叙事写进 commit
   message 而不是这张表。

6. 字段-决策矩阵: 每个 decision 允许的额外字段集合是封闭的 (FIELD_DECISION_MATRIX)。
   例如 must_by 只有 replace 才允许——它是"换源期限"，retire/freeze 域没有"换源"这个
   动作可催。命中不允许的字段 → **FAIL**。

7. 同表同判: 若两个域在 sync_registry.yaml 里指向同一个 target_table (物理表)，它们的
   decision 必须相等。一张物理表不可能一半冻结一半换源。命中不一致 → **FAIL**。

**关键设计约束**（不许改）: 未裁决域在到期前**只报 WARN 不 FAIL**。理由: 若设成 FAIL
会立刻阻断所有提交 —— 过度阻断只会卡住诚实的提交者。但 WARN **必须列出全部未裁决域名**
—— 项目教训是「warn-only 会退化成 warn-nothing」。decision 值域校验(检查 0)、闭合键集
(检查 5)、字段-决策矩阵(检查 6)、同表同判(检查 7) 例外：这四项校验的是配置本身写错
(值域外/未知键/字段错配/同表异判)，不是"域还没裁决"，报 FAIL。

退出码: 有 fail → 非 0; 只有 warn → 0。门 group: scaffold (warn-only 组)。

用法:
    PYTHONPATH=backend python backend/scripts/check_tushare_sunset.py [--today YYYYMMDD]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SUNSET = REPO / "backend" / "config" / "tushare_sunset.yaml"
DEFAULT_REGISTRY = REPO / "backend" / "config" / "sync_registry.yaml"

# tushare_sunset.yaml legend 里定义的合法 decision 取值。"unknown" 不在其中——
# 那是历史遗留的占位字面量，不是合法裁决状态；台账里出现它必须 FAIL，不许静默通过。
# "accept" / "accept_outage" 2026-09-04 起合并为 "freeze" ——
# 二者原先的区分点"有没有活跃消费方"该由 lineage impact 机器判，不该手写进 decision。
LEGAL_DECISIONS = frozenset(
    {"replace", "derive", "freeze", "retire", "undecided"}
)

# 每个域条目允许出现的字段，闭合键集——不在这个集合里的键一律 FAIL (检查 5)。
# decision/evidence 本身不算"额外字段"，任何 decision 都可以带。
KNOWN_KEYS = frozenset(
    {"decision", "status", "replacement", "must_by", "done_at", "evidence"}
)

# 字段-决策矩阵: decision -> 该 decision 允许的额外字段 (decision/evidence 之外)。
# 命中不在允许集合里的字段 → FAIL (检查 6)。
#   replace  — 换源进行中: must_by 给期限, replacement 记目标源, status/done_at 记完成态
#   derive   — 推导退役: 可以有 replacement (记推导方式名) 与 status/done_at (记完成态)，
#              但没有 must_by —— 推导链不是"换源"，没有一个外部期限可催
#   retire / freeze / undecided — 只有 decision + evidence，不带其他字段
FIELD_DECISION_MATRIX: dict[str, frozenset[str]] = {
    "replace": frozenset({"must_by", "replacement", "status", "done_at"}),
    "derive": frozenset({"replacement", "status", "done_at"}),
    "retire": frozenset(),
    "freeze": frozenset(),
    "undecided": frozenset(),
}


class PolicyError(RuntimeError):
    """政策缺失/不可解析 — 门不得在策略坏掉时假装通过。"""


def load_sunset(path: Path) -> dict[str, Any]:
    """加载并校验 tushare_sunset.yaml。"""
    if not path.is_file():
        raise PolicyError(f"missing sunset policy: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"unreadable sunset policy: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("sunset policy root must be a mapping with version: 1")
    if "authorization_expires" not in raw:
        raise PolicyError("sunset policy missing authorization_expires")
    return raw


def load_registry(path: Path) -> dict[str, Any]:
    """加载并校验 sync_registry.yaml。"""
    if not path.is_file():
        raise PolicyError(f"missing registry: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"unreadable registry: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyError("registry root must be a mapping with version: 1")
    if not isinstance(raw.get("domains"), dict):
        raise PolicyError("registry.domains must be a dict")
    return raw


def extract_tushare_domains(registry: dict[str, Any]) -> set[str]:
    """从 registry 提取所有 source: tushare 的域名。"""
    domains: set[str] = set()
    for domain_name, domain_spec in registry.get("domains", {}).items():
        if isinstance(domain_spec, dict) and domain_spec.get("source") == "tushare":
            domains.add(domain_name)
    return domains


def validate_decisions(sunset: dict[str, Any]) -> list[str]:
    """检查 0: decision 值域校验。

    缺字段与写了非法值 (如历史遗留的 'unknown') 必须产生不同的报错文案 —— 不能像旧代码
    那样用 entry.get("decision", "unknown") 把"没写"和"写了 unknown"压成同一个值，
    那会让两种截然不同的配置错误在日志里长得一模一样，没法区分该去补字段还是改字面量。
    """
    fails: list[str] = []
    domain_entries = sunset.get("domains", {})
    for domain_name, entry in domain_entries.items():
        if not isinstance(entry, dict):
            continue
        if "decision" not in entry:
            fails.append(
                f"域 {domain_name} 缺失 decision 字段（未写，非写了非法值）。"
                f"合法取值集: {sorted(LEGAL_DECISIONS)}。"
            )
            continue
        decision_value = entry["decision"]
        if decision_value not in LEGAL_DECISIONS:
            fails.append(
                f"域 {domain_name} 的 decision 值 {decision_value!r} 不合法（字段已写但值不在合法集里）。"
                f"合法取值集: {sorted(LEGAL_DECISIONS)}。"
            )
    return fails


def validate_known_keys(sunset: dict[str, Any]) -> list[str]:
    """检查 5: 闭合键集。

    每个域条目的字段必须 ⊆ KNOWN_KEYS，否则 FAIL。这是本次收口的核心机制：此前
    checker 只读 decision/status/replacement 三个键、且对未知键零校验，导致台账
    38 个条目里累积出 18 个不同的键 (机器读 3 个，人写了 18 个)——包括与顶层
    authorization_expires 重复 4 次的 frozen_at，和一堆没有任何 loader 读过的
    叙事字段 (note/impact/risk/caveat/fallback/...)。一个字段能留在这张表只有
    两条路: (a) 至少一个 loader 读它且写错/缺失时有门/测试会红 (b) 它是这张表
    唯一的自由文本槽 evidence。不满足任一条 = 未知键 = FAIL。
    """
    fails: list[str] = []
    domain_entries = sunset.get("domains", {})
    for domain_name, entry in domain_entries.items():
        if not isinstance(entry, dict):
            continue
        unknown = sorted(set(entry.keys()) - KNOWN_KEYS)
        if unknown:
            fails.append(
                f"域 {domain_name} 出现未登记键 {unknown}（合法键集: {sorted(KNOWN_KEYS)}）。"
                "登记表键集必须闭合——未知键要么是该删的叙事字段（叙事进 commit message，"
                "不进登记表），要么需要先给它接一个 loader/门再登记，不能先写字段再补执法。"
            )
    return fails


def validate_field_decision_matrix(sunset: dict[str, Any]) -> list[str]:
    """检查 6: 字段-决策矩阵。

    每个 decision 只允许 FIELD_DECISION_MATRIX 里登记的额外字段。真实台账实测:
    must_by 曾出现在 12 个非 replace 条目 (5 个 retire + 7 个原 accept_outage) 上——
    legend 只为 replace 定义了 must_by 的语义 ("换源期限")，retire/freeze 域没有
    "换源"这个动作可催，写着 must_by 只是抄来的死字段。这里把语义收紧成机器可判的
    形状：写错组合 = 配置本身有错，FAIL，不是"域还没裁决"那种可以先 WARN 的漂移。

    只检查 KNOWN_KEYS 范围内的字段——完全陌生的键 (不在 KNOWN_KEYS 里) 已经由
    validate_known_keys (检查 5) 报过一次 FAIL，这里不重复报同一个字段两次；
    本检查专管"字段本身合法，但挂在错误的 decision 上"这一种错误形状。
    """
    fails: list[str] = []
    domain_entries = sunset.get("domains", {})
    for domain_name, entry in domain_entries.items():
        if not isinstance(entry, dict):
            continue
        decision = entry.get("decision")
        if decision not in FIELD_DECISION_MATRIX:
            # 值域本身不合法，已由 validate_decisions 报过，这里不重复报。
            continue
        allowed_extra = FIELD_DECISION_MATRIX[decision]
        present_extra = (set(entry.keys()) & KNOWN_KEYS) - {"decision", "evidence"}
        disallowed = sorted(present_extra - allowed_extra)
        if disallowed:
            allowed_desc = sorted(allowed_extra) if allowed_extra else "仅 decision/evidence"
            fails.append(
                f"域 {domain_name} 的 decision={decision!r} 不允许字段 {disallowed}"
                f"（{decision!r} 允许的额外字段: {allowed_desc}）。"
            )
    return fails


def validate_same_table_same_decision(
    sunset: dict[str, Any], registry: dict[str, Any]
) -> list[str]:
    """检查 7: 同一 target_table 的域必须同 decision。

    真实台账实测: index_member_all 与 index_member_all_hist 同指向物理表
    raw_tushare_index_member_all (is_new=Y/N 两种请求形状)，但 verdict_deviation
    这类字段要靠人抄两遍才能保持同判——这正是"一张登记表"(R3) 违反的最小形态。
    一张物理表不可能一半冻结一半换源，所以这里直接从 sync_registry.yaml 按
    target_table 分组，组内 decision 必须全部相等，不一致 → FAIL。
    """
    fails: list[str] = []
    domain_entries = sunset.get("domains", {})
    registry_domains = registry.get("domains", {})

    table_to_domains: dict[str, list[str]] = {}
    for domain_name, entry in domain_entries.items():
        if not isinstance(entry, dict):
            continue
        reg_spec = registry_domains.get(domain_name)
        if not isinstance(reg_spec, dict):
            continue
        target_table = reg_spec.get("target_table")
        if not target_table:
            continue
        table_to_domains.setdefault(target_table, []).append(domain_name)

    for target_table, names in sorted(table_to_domains.items()):
        if len(names) < 2:
            continue
        decisions = {name: domain_entries[name].get("decision") for name in names}
        if len(set(decisions.values())) > 1:
            names_sorted = sorted(names)
            detail = ", ".join(f"{n}={decisions[n]}" for n in names_sorted)
            fails.append(
                f"物理表 {target_table} 被多个域共用但 decision 不一致: {detail}。"
                "一张表不可能一半冻结一半换源，须裁成同一个 decision。"
            )
    return fails


def extract_sunset_domains(sunset: dict[str, Any]) -> dict[str, Any]:
    """从 sunset 提取所有被记录的域 (包括顶层 undecided_domains)。
    返回 dict {domain_name: (decision, status_if_any)}.

    decision 缺字段时这里存 None，不再假装成 "unknown" —— 值域是否合法由
    validate_decisions 单独判定并报错，这里只负责如实转录，不做默认值遮盖。

    undecided_domains 2026-09-04 起是顶层键 (不再嵌套在 domains 字典里假装成一个
    条目) —— 它本来就是一份名单不是一个域，此前嵌在 domains 里让 checker 要三处
    特判才能把它摘出来。
    """
    domains: dict[str, Any] = {}
    domain_entries = sunset.get("domains", {})
    for domain_name, entry in domain_entries.items():
        if isinstance(entry, dict):
            decision = entry.get("decision")
            status = entry.get("status")
            domains[domain_name] = (decision, status)

    for undecided_name in sunset.get("undecided_domains", []):
        if undecided_name not in domains:
            domains[undecided_name] = ("undecided", None)

    return domains


def run(
    sunset_path: Path = DEFAULT_SUNSET,
    registry_path: Path = DEFAULT_REGISTRY,
    today: date | None = None,
) -> tuple[list[str], list[str]]:
    """执行八项检查 (检查 0~7)。返回 (fails, warns)。"""
    if today is None:
        today = date.today()  # rule-compliance: ok evidence=与授权到期日比对的墙钟日期(非交易日判定/非交易决策锚), 墙钟即语义本身

    fails: list[str] = []
    warns: list[str] = []

    sunset = load_sunset(sunset_path)
    registry = load_registry(registry_path)

    registry_tushare = extract_tushare_domains(registry)
    sunset_domains = extract_sunset_domains(sunset)

    # ── 检查 0: decision 值域校验 ─────────────────────────────────────
    fails.extend(validate_decisions(sunset))

    # ── 检查 5: 闭合键集 ──────────────────────────────────────────────
    fails.extend(validate_known_keys(sunset))

    # ── 检查 6: 字段-决策矩阵 ─────────────────────────────────────────
    fails.extend(validate_field_decision_matrix(sunset))

    # ── 检查 7: 同表同判 ──────────────────────────────────────────────
    fails.extend(validate_same_table_same_decision(sunset, registry))

    # ── 检查 1: 覆盖完整性 ────────────────────────────────────────────
    missing_from_sunset = registry_tushare - set(sunset_domains.keys())
    if missing_from_sunset:
        fails.append(
            f"registry 有 tushare 域但 sunset.yaml 缺：{sorted(missing_from_sunset)}。"
            "每个 tushare 源的域必须在 tushare_sunset.yaml 里有决策记录或列在 undecided_domains。"
        )

    # ── 检查 2: 反向自清 ────────────────────────────────────────────
    undecided_at_top = set(sunset.get("undecided_domains", []))
    for sunset_domain, (decision, status) in sunset_domains.items():
        # 如果这个域是顶层 undecided_domains 名单里的项，跳过（它不是一个域条目）
        if sunset_domain in undecided_at_top:
            continue

        # 如果这个域在 registry 中已不是 tushare 源
        if sunset_domain not in registry_tushare:
            # 如果是 done 状态，说明已完成，不报 warn
            if status == "done":
                continue
            # 否则报 stale 豁免
            warns.append(
                f"sunset.yaml 提到的域 {sunset_domain} 在 registry 中已不是 source: tushare "
                f"(decision={decision}, status={status})。"
                "若已完成迁移，请标记 status: done；否则清理该条目防豁免清单烂掉。"
            )

    # ── 检查 3: 到期倒计时 ────────────────────────────────────────────
    expires_str = sunset.get("authorization_expires", "")
    try:
        expires_date = datetime.strptime(expires_str, "%Y-%m-%d").date()
    except ValueError:
        raise PolicyError(
            f"authorization_expires 格式不合法: {expires_str!r} "
            "(期望 YYYY-MM-DD 格式)"
        ) from None

    # 未裁决域 = 顶层 undecided_domains 列表 ∪ 所有 decision == "undecided" 的条目。
    # 旧代码只读列表；真实台账里列表恒为空、"undecided" 全部是以条目形式写的
    # (decision: undecided)，导致这个分支在生产从未触发过。extract_sunset_domains
    # 已经把两个来源都转录进 sunset_domains（列表项也被赋值 decision="undecided"），
    # 所以直接按 decision 过滤即可覆盖两个来源，不需要分别读两处。
    decision_undecided = {
        name for name, (decision, _status) in sunset_domains.items() if decision == "undecided"
    }
    undecided_list = sorted(undecided_at_top | decision_undecided)

    if undecided_list:
        days_left = (expires_date - today).days
        if days_left < 0:
            # 已过期
            fails.append(
                f"TuShare 授权已于 {expires_date} 过期（今天 {today}），"
                f"但仍有 {len(undecided_list)} 个未裁决域。"
                "这些域将立刻断流，必须立即决策或删除：\n  "
                + "\n  ".join(f"- {d}" for d in undecided_list)
            )
        elif days_left <= 14:
            # 距到期 ≤ 14 天，报醒目的 WARN
            warns.append(
                f"[紧急] TuShare 授权距到期仅 {days_left} 天（{expires_date}），"
                f"仍有 {len(undecided_list)} 个未裁决域必须立即处理：\n  "
                + "\n  ".join(f"- {d}" for d in undecided_list)
            )
        else:
            # 距到期 > 14 天，普通 WARN
            warns.append(
                f"TuShare 授权距到期 {days_left} 天（{expires_date}），"
                f"仍有 {len(undecided_list)} 个未裁决域待处理：\n  "
                + "\n  ".join(f"- {d}" for d in undecided_list)
            )

    # ── 检查 4: 裁决执行度 (声明 vs 实际的漂移) ──────────────────────
    # 检查 2 只查一个方向 (已换源却没标 done)。这里查反方向: 台账已经裁决要换
    # (replace/derive) 却 registry 里源还没换。报 WARN 不 FAIL —— 业主已解除断流时限
    # 压力，这条检查的价值是让"声明与实际不一致"可见，不是催今天必须切换。
    raw_domain_entries = sunset.get("domains", {})
    drifted: list[tuple[str, str]] = []
    for domain_name, (decision, status) in sunset_domains.items():
        if domain_name not in registry_tushare:
            continue
        if decision not in ("replace", "derive"):
            continue
        if status == "done":
            continue
        entry = raw_domain_entries.get(domain_name, {})
        label = entry.get("replacement", decision) if isinstance(entry, dict) else decision
        drifted.append((domain_name, label))

    if drifted:
        drifted.sort(key=lambda pair: pair[0])
        warns.append(
            f"声明与实际的漂移（非紧急，仅供可见性）: {len(drifted)} 个域在 "
            "tushare_sunset.yaml 里已裁决 replace/derive 但未标 status: done，"
            "registry 里 source 仍是 tushare。这是台账落后于计划、不是今天要断流：\n  "
            + "\n  ".join(f"- {name} -> {label}" for name, label in drifted)
        )

    return fails, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunset", type=Path, default=DEFAULT_SUNSET)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument(
        "--today",
        type=str,
        default=None,
        help="当天日期 (YYYYMMDD 格式，用于测试注入; 默认系统日期)",
    )
    args = ap.parse_args(argv)

    today = None
    if args.today:
        try:
            today = datetime.strptime(args.today, "%Y%m%d").date()
        except ValueError:
            print(f"[tushare-sunset] FAIL: --today 格式不合法: {args.today}", file=sys.stderr)
            print("[tushare-sunset] verdict=FAIL fails=1 warns=0")
            return 1

    try:
        fails, warns = run(args.sunset, args.registry, today)
    except PolicyError as exc:
        print(f"[tushare-sunset] FAIL: {exc}", file=sys.stderr)
        print("[tushare-sunset] verdict=FAIL fails=1 warns=0")
        return 1

    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")

    verdict = "FAIL" if fails else "WARN" if warns else "PASS"
    print(f"[tushare-sunset] verdict={verdict} fails={len(fails)} warns={len(warns)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
