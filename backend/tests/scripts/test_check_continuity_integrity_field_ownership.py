"""字段所有权契约（现行 owner=live checker；历史证据=analysis/gap_root_cause_20260708.md）。

背景: sync_registry.yaml 每域字段里, gap_tolerance(为 check_calendar_gaps 设计, 判断"整日缺失"
是否天然可容忍) 曾被泛化复用去抑制 check_cross_section 的 row_dip(判断"行数骤降但非零")——
两者是不同的失效模式, 泛化导致真实盲区: stk_surv 因日历稀疏理由早被打了 gap_tolerance, 结果它
同时存在的系统性 page_limit 截断 bug(丢 22%~87%)被这个不相关的标签连带掩盖多年, 差点无限期不
被发现。这类"字段被挪用到设计意图之外的函数"的 bug 只能靠人工审计代码才发现, 没有自动化门。

本门用 AST 静态扫描 check_continuity_integrity.py, 把"哪个 check_* 函数读了哪个容忍/豁免类字段"
固化成显式契约表, 任何未来把某字段读进未声明函数的改动(不管是新增读取还是误用已有字段)都会在
这里炸——把过去只能靠人工代码审查抓的"字段挪用"错误, 变成自动 CI 门。

契约区分两类字段:
  - 事实型(可多个 check 共享, 表达同一个客观事实): known_empty_days(此日源端确认真空,
    对"整日缺失"和"行数骤降"两种检测都成立) / dead_groups(此组永久性消亡, 对"分组缺失"和
    "分组新鲜度"两种检测都成立)。
  - 判断型(必须且只能被声明的唯一 check 使用, 是该检测方法专属的容忍判断): gap_tolerance /
    row_dip_tolerance / data_start_reviewed / freshness_group_col。这类字段绝不允许被第二个
    check 函数读取——今天的 bug 正是判断型字段被跨函数挪用。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_FILE = Path(__file__).resolve().parents[2] / "scripts" / "check_continuity_integrity.py"

# 契约: 字段 -> 允许读取它的 check_* 函数名集合。
# 不在此表内的 check_* 函数一律不许读取该字段 (新增读取必须先来改这张表, 逼一次显式决策)。
OWNERSHIP: dict[str, set[str]] = {
    # 事实型 (跨检测方法共享同一客观事实, 允许多个 owner)
    "known_empty_days": {"check_calendar_gaps", "check_cross_section"},
    "dead_groups": {"check_cross_section", "check_group_freshness"},
    # 判断型 (检测方法专属的容忍/豁免判断, 恰好一个 owner, 不得跨函数复用)
    "gap_tolerance": {"check_calendar_gaps"},
    "row_dip_tolerance": {"check_cross_section"},
    "known_group_gaps": {"check_cross_section"},
    "data_start_reviewed": {"check_declared_vs_actual"},
    "freshness_group_col": {"check_group_freshness"},
}

# 非检测逻辑本身的基础设施函数 (registry 解析/日期列探测/编排路由) 允许提及任意字段名,
# 不受本契约约束 (它们是"声明这字段存在"或"决定要不要跑某检测", 不是"解释这字段的容忍语义")。
_INFRA_FUNCS = {"load_domain_specs", "run_checks", "_resolve_date_col", "_result"}


def _check_function_bodies() -> dict[str, str]:
    tree = ast.parse(_FILE.read_text(encoding="utf-8"))
    src = _FILE.read_text(encoding="utf-8")
    bodies: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("check_") \
                and node.name not in _INFRA_FUNCS:
            bodies[node.name] = ast.get_source_segment(src, node) or ""
    return bodies


def test_all_check_functions_discovered():
    """契约表覆盖的函数集合必须是真实存在的 check_* 函数 (防字段表笔误引用已改名/已删函数)。"""
    bodies = _check_function_bodies()
    assert bodies, "check_continuity_integrity.py 里一个 check_* 函数都没扫到, AST 解析本身坏了"
    all_owners = {fn for owners in OWNERSHIP.values() for fn in owners}
    unknown = all_owners - set(bodies)
    assert not unknown, f"契约表引用了不存在的 check_* 函数: {unknown} (改名/删除后必须同步这张表)"


def test_no_field_read_outside_its_declared_owners():
    """核心红线: 任何字段只能被契约表声明的函数读取, 未声明的函数一旦读取即视为跨检测方法挪用。"""
    bodies = _check_function_bodies()
    violations = []
    for field, allowed_owners in OWNERSHIP.items():
        needle = f'"{field}"'
        actual_readers = {fn for fn, src in bodies.items() if needle in src}
        unexpected = actual_readers - allowed_owners
        if unexpected:
            violations.append(f"{field}: 被未声明的函数读取 {sorted(unexpected)} "
                               f"(契约只允许 {sorted(allowed_owners)})")
    assert not violations, (
        "字段所有权契约违反 (owner=live checker; evidence=analysis/gap_root_cause_20260708.md) — "
        "检测到判断型/事实型字段被跨 check 函数挪用, 与今天修复的 gap_tolerance/row_dip 同型:\n"
        + "\n".join(violations))


def test_judgment_fields_have_exactly_one_owner():
    """判断型字段(容忍/豁免类判断)必须恰好 1 个 owner, 事实型字段允许 >=1 个 (双检测方法共享)。

    这条比上一条更严: 上一条只挡"未声明的额外读取者", 这条挡"契约表本身把判断型字段错误
    声明成多 owner"(如果发生, 说明契约表被人手滑改宽了, 直接在这里拦, 不靠人工复查)。"""
    judgment_fields = {"gap_tolerance", "row_dip_tolerance", "known_group_gaps",
                        "data_start_reviewed", "freshness_group_col"}
    for field in judgment_fields:
        owners = OWNERSHIP[field]
        assert len(owners) == 1, (
            f"{field} 是判断型字段(容忍/豁免类), 契约声明了 {len(owners)} 个 owner {sorted(owners)}"
            f" —— 判断型字段必须恰好 1 个 owner, 多 owner 是 gap_tolerance/row_dip 同型 bug 的前兆")
