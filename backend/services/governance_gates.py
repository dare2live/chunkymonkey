"""治理门分布策略的 typed 读取层 (owner: backend/config/governance_gates.yaml)。

goal.md「治理体系重构」P1：把 safe_commit 的 19 道门按「谁受害、何时受害」分成
三组 —— ``diff_correctness`` (这次 diff 本身错 → commit 阻断) /
``system_health`` (数据/策略/钱 → 运行时自检) / ``scaffold`` (下一个开发者 →
提示不阻断)。

本模块只负责「读 + 校验 + 类型化」，不执行任何门。消费方：

* ``backend/scripts/gate_policy.py``      —— safe_commit 的 shell 面 + 人读表
* ``backend/services/pipeline/store.py``  —— daily_update 运行时自检
* ``backend/tests/scripts/test_governance_gates.py`` —— 与两处门名副本的漂移锁

fail-closed：配置缺失/不合法一律抛 :class:`GatePolicyError`，由调用方退回
「全部阻断」。绝不因为策略文件坏掉而让某道门静默变成 warn。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO / "backend" / "config" / "governance_gates.yaml"

GROUP_DIFF = "diff_correctness"
GROUP_SYSTEM = "system_health"
GROUP_SCAFFOLD = "scaffold"
KNOWN_GROUPS = (GROUP_DIFF, GROUP_SYSTEM, GROUP_SCAFFOLD)

_GROUP_REQUIRED_FIELDS = ("victim", "harm_at", "commit_behavior", "runtime_behavior", "why")
_COMMIT_BEHAVIOR = {GROUP_DIFF: "block", GROUP_SYSTEM: "not_run", GROUP_SCAFFOLD: "warn"}


class GatePolicyError(RuntimeError):
    """策略文件缺失 / 不可解析 / 违反硬不变量 —— 调用方必须 fail closed。"""


@dataclass(frozen=True)
class GateSpec:
    """一道 commit 门的分组标签。"""

    name: str
    group: str
    checks: str
    why: str


@dataclass(frozen=True)
class RuntimeCheckSpec:
    """daily_update 运行时自检的一条。"""

    id: str
    from_gate: str | None
    script: str
    args: tuple[str, ...]
    degraded_msg: str
    skip_when_dry: bool

    def rendered_args(self, *, date: str) -> list[str]:
        return [a.replace("{date}", date) for a in self.args]

    def rendered_degraded_msg(self, *, date: str) -> str:
        return " ".join(self.degraded_msg.replace("{date}", date).split())


@dataclass(frozen=True)
class ScaffoldFixSpec:
    """脚手架批量修的一条 (regenerate = 机器能修; report = 只能报清单)。"""

    id: str
    from_gate: str
    kind: str
    command: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class GateRegistry:
    gates: tuple[GateSpec, ...]
    runtime_checks: tuple[RuntimeCheckSpec, ...]
    scaffold_fixes: tuple[ScaffoldFixSpec, ...]
    groups: Mapping[str, Mapping[str, Any]]

    def group_of(self, gate: str) -> str:
        for spec in self.gates:
            if spec.name == gate:
                return spec.group
        raise GatePolicyError(f"unknown gate: {gate!r}")

    def names_in_group(self, group: str) -> list[str]:
        if group not in KNOWN_GROUPS:
            raise GatePolicyError(f"unknown group: {group!r}")
        return [g.name for g in self.gates if g.group == group]

    @property
    def gate_names(self) -> list[str]:
        return [g.name for g in self.gates]


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GatePolicyError(f"{what} must be a non-empty string")
    return value.strip()


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise GatePolicyError(f"missing gate registry: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GatePolicyError(f"unreadable gate registry: {exc}") from exc
    if not isinstance(raw, dict):
        raise GatePolicyError("gate registry root must be a mapping")
    if raw.get("version") != 1:
        raise GatePolicyError("gate registry version must be 1")
    return raw


def _parse_groups(raw: dict[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    groups = raw.get("groups")
    if not isinstance(groups, dict):
        raise GatePolicyError("groups must be a mapping")
    if set(groups) != set(KNOWN_GROUPS):
        raise GatePolicyError(f"groups must be exactly {sorted(KNOWN_GROUPS)}")
    for name, body in groups.items():
        if not isinstance(body, dict):
            raise GatePolicyError(f"groups.{name} must be a mapping")
        for field in _GROUP_REQUIRED_FIELDS:
            _require_str(body.get(field), f"groups.{name}.{field}")
        expected = _COMMIT_BEHAVIOR[name]
        if body["commit_behavior"] != expected:
            raise GatePolicyError(
                f"groups.{name}.commit_behavior must be {expected!r} "
                f"(commit 后果由分组定义，不可逐门改写)"
            )
    return groups


def _parse_gates(raw: dict[str, Any]) -> tuple[GateSpec, ...]:
    gates = raw.get("commit_gates")
    if not isinstance(gates, dict) or not gates:
        raise GatePolicyError("commit_gates must be a non-empty mapping")
    out: list[GateSpec] = []
    for name, body in gates.items():
        name = _require_str(name, "commit_gates key")
        if not isinstance(body, dict):
            raise GatePolicyError(f"commit_gates.{name} must be a mapping")
        group = _require_str(body.get("group"), f"commit_gates.{name}.group")
        if group not in KNOWN_GROUPS:
            raise GatePolicyError(f"commit_gates.{name}.group unknown: {group!r}")
        out.append(
            GateSpec(
                name=name,
                group=group,
                checks=_require_str(body.get("checks"), f"commit_gates.{name}.checks"),
                why=_require_str(body.get("why"), f"commit_gates.{name}.why"),
            )
        )
    return tuple(out)


def _parse_runtime_checks(
    raw: dict[str, Any], gate_names: set[str]
) -> tuple[RuntimeCheckSpec, ...]:
    rows = raw.get("runtime_checks")
    if not isinstance(rows, list) or not rows:
        raise GatePolicyError("runtime_checks must be a non-empty list")
    seen: set[str] = set()
    out: list[RuntimeCheckSpec] = []
    for index, body in enumerate(rows):
        if not isinstance(body, dict):
            raise GatePolicyError(f"runtime_checks[{index}] must be a mapping")
        check_id = _require_str(body.get("id"), f"runtime_checks[{index}].id")
        if check_id in seen:
            raise GatePolicyError(f"duplicate runtime_checks id: {check_id!r}")
        seen.add(check_id)
        from_gate = body.get("from_gate")
        if from_gate is not None:
            from_gate = _require_str(from_gate, f"runtime_checks[{check_id}].from_gate")
            if from_gate not in gate_names:
                raise GatePolicyError(
                    f"runtime_checks[{check_id}].from_gate unknown gate: {from_gate!r}"
                )
        script = _require_str(body.get("script"), f"runtime_checks[{check_id}].script")
        if not (REPO / script).is_file():
            raise GatePolicyError(
                f"runtime_checks[{check_id}].script does not exist: {script}"
            )
        args = body.get("args")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise GatePolicyError(f"runtime_checks[{check_id}].args must be a list of strings")
        skip_when_dry = body.get("skip_when_dry")
        if not isinstance(skip_when_dry, bool):
            raise GatePolicyError(f"runtime_checks[{check_id}].skip_when_dry must be a bool")
        out.append(
            RuntimeCheckSpec(
                id=check_id,
                from_gate=from_gate,
                script=script,
                args=tuple(args),
                degraded_msg=_require_str(
                    body.get("degraded_msg"), f"runtime_checks[{check_id}].degraded_msg"
                ),
                skip_when_dry=skip_when_dry,
            )
        )
    return tuple(out)


def _parse_scaffold_fixes(
    raw: dict[str, Any], gate_group: Mapping[str, str]
) -> tuple[ScaffoldFixSpec, ...]:
    rows = raw.get("scaffold_fix")
    if not isinstance(rows, list) or not rows:
        raise GatePolicyError("scaffold_fix must be a non-empty list")
    out: list[ScaffoldFixSpec] = []
    for index, body in enumerate(rows):
        if not isinstance(body, dict):
            raise GatePolicyError(f"scaffold_fix[{index}] must be a mapping")
        fix_id = _require_str(body.get("id"), f"scaffold_fix[{index}].id")
        from_gate = _require_str(body.get("from_gate"), f"scaffold_fix[{fix_id}].from_gate")
        if gate_group.get(from_gate) != GROUP_SCAFFOLD:
            raise GatePolicyError(
                f"scaffold_fix[{fix_id}].from_gate must be a scaffold gate: {from_gate!r}"
            )
        kind = _require_str(body.get("kind"), f"scaffold_fix[{fix_id}].kind")
        if kind not in {"regenerate", "report"}:
            raise GatePolicyError(f"scaffold_fix[{fix_id}].kind must be regenerate|report")
        command = body.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(c, str) for c in command
        ):
            raise GatePolicyError(f"scaffold_fix[{fix_id}].command must be a non-empty str list")
        if not (REPO / command[0]).is_file():
            raise GatePolicyError(
                f"scaffold_fix[{fix_id}].command[0] does not exist: {command[0]}"
            )
        out.append(
            ScaffoldFixSpec(
                id=fix_id,
                from_gate=from_gate,
                kind=kind,
                command=tuple(command),
                note=_require_str(body.get("note"), f"scaffold_fix[{fix_id}].note"),
            )
        )
    return tuple(out)


def run_runtime_checks(
    run: "Callable[[RuntimeCheckSpec, list[str]], int]",
    *,
    date: str,
    dry: bool = False,
    registry: GateRegistry | None = None,
) -> list[dict[str, Any]]:
    """按登记表逐条跑运行时自检；``run`` 只负责执行并返回退出码。

    调用方注入执行器：``store.py`` 用 ``ctx.run_script`` (带 writer lease + 日志)，
    ``gate_policy.py`` 用裸 subprocess。共享的是「跑哪些、参数怎么渲染、dry 跳哪些、
    失败该说什么」—— 也就是策略，而策略只有登记表一个存放处。
    """
    reg = registry or load_registry()
    rows: list[dict[str, Any]] = []
    for spec in reg.runtime_checks:
        msg = spec.rendered_degraded_msg(date=date)
        if dry and spec.skip_when_dry:
            rows.append({"id": spec.id, "status": "skipped_dry", "exit": None, "degraded_msg": msg})
            continue
        code = int(run(spec, spec.rendered_args(date=date)))
        rows.append(
            {
                "id": spec.id,
                "from_gate": spec.from_gate,
                "status": "pass" if code == 0 else "fail",
                "exit": code,
                "degraded_msg": msg,
            }
        )
    return rows


def load_registry(path: Path | None = None) -> GateRegistry:
    """读 + 校验策略；任何不合法一律抛 GatePolicyError (调用方 fail closed)。"""
    raw = _load_raw(path or DEFAULT_REGISTRY)
    groups = _parse_groups(raw)
    gates = _parse_gates(raw)
    gate_group = {g.name: g.group for g in gates}
    runtime_checks = _parse_runtime_checks(raw, set(gate_group))
    scaffold_fixes = _parse_scaffold_fixes(raw, gate_group)

    # 硬不变量：system_health 组的每道门都必须真的挂在运行时自检上，
    # 否则「从 commit 路径摘掉」就等于把检查删了 (P1.2 的全部意义)。
    wired = {c.from_gate for c in runtime_checks if c.from_gate}
    orphan = sorted(
        name for name, group in gate_group.items()
        if group == GROUP_SYSTEM and name not in wired
    )
    if orphan:
        raise GatePolicyError(
            f"system_health gates not wired into runtime_checks: {orphan} "
            "(不许从 commit 路径摘掉却无人接手)"
        )
    return GateRegistry(
        gates=gates,
        runtime_checks=runtime_checks,
        scaffold_fixes=scaffold_fixes,
        groups=groups,
    )
