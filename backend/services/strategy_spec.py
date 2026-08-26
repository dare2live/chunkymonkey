"""Versioned StrategySpec loader for the three MASTER packages.

This is the typed package boundary, not a StrategyRelease and not claimable
research. Profile alpha and E overnight ablation stay on their own modules.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PACKAGES_DIR = REPO / "backend" / "config" / "strategy_packages"
BESTCHOICE_ENGINE = REPO / "bestchoice" / "formula_engine.py"
BESTCHOICE_MANIFEST = REPO / "bestchoice" / "evidence_manifest.json"

ALLOWED_PACKAGES = frozenset(
    {"institution_follow_v1", "main_rally_v1", "formulas"}
)
FORBIDDEN_PNL_SOURCES = frozenset(
    {
        "alpha_c1",
        "holder_median_alpha",
        "institution_vwap",
        "institution_holding_period_return",
        "vwap_tradable_v1",
        "optuna_adoption",
        "optuna_params",
    }
)
FORMULA_ENGINE_SHA256 = (
    "5096d4778a2b8f34afd1c1f5dfcf7b1033294fa5e421b76ec46d340202a82379"
)
FROZEN_FORMULA_IDS = (
    "gs_pullback_confirm",
    "gs_raw_buy",
    "ma_base_breakout",
    "activity_breakout",
    "volume_base_breakout",
)

_FOLLOW_EXIT_KIND = "event_or_max_hold"
_FOLLOW_PNL_PREFIX = "follower_"
_FORMULA_EXIT_KIND = "formula_exit_or_max_hold"
CHALLENGER_PNL_SOURCE = "challenger_next_open_to_exit_open"
_FORMULA_PAPER_STATUS = "synthetic_smoke_ready"
_FORMULA_MAX_CHASE_DAYS = 3
_FORMULA_MAX_HOLD_CALENDAR_DAYS = 90
DISCLOSURE_COVERAGE_DOMAINS = frozenset(
    {"holders_top10", "org_holding", "stk_holdertrade"}
)
NON_DISCLOSURE_FREEZE_DOMAINS = frozenset({"nominal_ohlcv"})


class StrategySpecError(RuntimeError):
    """Package YAML is missing, unknown, or violates fail-closed construction."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StrategySpecError(message)


@dataclass(frozen=True)
class StrategySpec:
    package_id: str
    spec_id: str
    candidate_generation: str
    ranking: str
    sizing: str
    entry_kind: str
    entry_after: str
    exit_kind: str
    exit_event: str
    pnl_source: str
    paper_status: str
    max_chase_days: int
    max_hold_calendar_days: int | None
    named_not_run_max_hold_calendar_days: tuple[int, ...]
    applicable_states: tuple[str, ...]
    config_hash: str
    frozen_artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        _require(self.package_id in ALLOWED_PACKAGES, "unknown_strategy_package")
        _require(bool(self.spec_id), "missing_spec_id")
        _require(bool(self.candidate_generation), "missing_candidate_generation")
        _require(bool(self.exit_kind), "missing_exit")
        _require(
            self.pnl_source not in FORBIDDEN_PNL_SOURCES,
            "follower_pnl_must_not_use_institution_alpha",
        )
        _require(int(self.max_chase_days) >= 0, "invalid_max_chase_days")
        if self.package_id == "institution_follow_v1":
            _require(self.exit_kind == _FOLLOW_EXIT_KIND, "missing_exit")
            _require(bool(self.exit_event), "missing_exit")
            _require(
                self.max_hold_calendar_days is not None
                and int(self.max_hold_calendar_days) > 0,
                "missing_exit",
            )
            _require(
                str(self.pnl_source).startswith(_FOLLOW_PNL_PREFIX),
                "follower_pnl_must_not_use_institution_alpha",
            )
        if self.package_id == "formulas":
            _require(
                self.frozen_artifact_sha256 == FORMULA_ENGINE_SHA256,
                "formula_engine_sha256_mismatch",
            )
            _require(self.entry_kind == "next_tradable_open", "formula_entry_must_be_next_open")
            _require(self.exit_kind == _FORMULA_EXIT_KIND, "missing_exit")
            _require(
                int(self.max_chase_days) == _FORMULA_MAX_CHASE_DAYS,
                "formula_max_chase_days_must_be_3",
            )
            _require(
                self.max_hold_calendar_days is not None
                and int(self.max_hold_calendar_days) == _FORMULA_MAX_HOLD_CALENDAR_DAYS,
                "missing_exit",
            )
            _require(
                self.pnl_source == CHALLENGER_PNL_SOURCE,
                "formula_pnl_must_be_challenger_next_open",
            )
            _require(
                self.paper_status == _FORMULA_PAPER_STATUS,
                "formula_paper_status_must_be_synthetic_smoke",
            )


def _accepted_partition_days(spec: Mapping[str, Any]) -> tuple[str, ...]:
    accepted = spec.get("accepted") or ()
    if not isinstance(accepted, (list, tuple)):
        return ()
    days: set[str] = set()
    for row in accepted:
        if not isinstance(row, Mapping):
            continue
        day = "".join(ch for ch in str(row.get("partition") or "") if ch.isdigit())[:8]
        if len(day) == 8 and day.isdigit():
            days.add(day)
    return tuple(sorted(days))


def disclosure_freeze_coverage(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Coverage denominator = disclosure partitions, never freeze OHLCV span."""

    domains = payload.get("domains")
    by_domain: dict[str, tuple[str, ...]] = {}
    excluded: list[str] = []
    unclassified: list[str] = []
    union: set[str] = set()
    if isinstance(domains, Mapping):
        for name, spec in domains.items():
            key = str(name)
            if key in NON_DISCLOSURE_FREEZE_DOMAINS:
                excluded.append(key)
                continue
            if key not in DISCLOSURE_COVERAGE_DOMAINS:
                unclassified.append(key)
                continue
            if not isinstance(spec, Mapping):
                by_domain[key] = ()
                continue
            days = _accepted_partition_days(spec)
            by_domain[key] = days
            union.update(days)
    return {
        "denominator": "disclosure_freeze_partitions",
        "by_domain": {key: list(days) for key, days in sorted(by_domain.items())},
        "union_days": list(sorted(union)),
        "union_day_count": len(union),
        "excluded_domains": sorted(excluded),
        "unclassified_domains": sorted(unclassified),
    }


def disclosure_freeze_coverage_days(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Union of disclosure-domain freeze partitions, excluding OHLCV."""

    return tuple(disclosure_freeze_coverage(payload)["union_days"])


def load_source_module(module_name: str, path: Path) -> Any:
    """Import a repo file without writing __pycache__ into frozen trees."""

    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise StrategySpecError(f"{module_name}_unimportable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


def verify_frozen_challenger(*, repo: Path | str | None = None) -> None:
    """Fail closed unless bestchoice/scripts/verify_frozen_evidence.py is green."""

    root = Path(repo) if repo else REPO
    script = root / "bestchoice" / "scripts" / "verify_frozen_evidence.py"
    _require(script.is_file(), "missing_verify_frozen_evidence")
    module = load_source_module("verify_frozen_evidence", script)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            module.main()
    except (RuntimeError, AssertionError, OSError, json.JSONDecodeError) as exc:
        raise StrategySpecError(f"frozen_challenger_unverified:{exc}") from exc


def _as_mapping(raw: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise StrategySpecError(f"{label} must be a mapping")
    return raw


def _as_int(raw: Any, label: str, *, required: bool) -> int | None:
    if raw is None or raw == "":
        if required:
            raise StrategySpecError(f"{label} is required")
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise StrategySpecError(f"{label} must be an integer") from exc


def _named_holds(raw: Any) -> tuple[int, ...]:
    if raw in (None, "", ()):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise StrategySpecError("named_not_run_max_hold_calendar_days must be a list")
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError) as exc:
            raise StrategySpecError("named hold days must be integers") from exc
    return tuple(out)


def _states(raw: Any) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise StrategySpecError("applicable_states must be a list")
    return tuple(str(item) for item in raw)


def _verify_formula_engine(expected: str, *, repo: Path) -> None:
    engine = repo / "bestchoice" / "formula_engine.py"
    manifest_path = repo / "bestchoice" / "evidence_manifest.json"
    _require(engine.is_file(), "missing_formula_engine")
    digest = _sha256_file(engine)
    _require(digest == expected, "formula_engine_sha256_mismatch")
    _require(expected == FORMULA_ENGINE_SHA256, "formula_engine_sha256_mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategySpecError("formula_manifest_unreadable") from exc
    files = manifest.get("files") if isinstance(manifest, Mapping) else None
    engine_spec = files.get("formula_engine.py") if isinstance(files, Mapping) else None
    listed = engine_spec.get("sha256") if isinstance(engine_spec, Mapping) else None
    _require(listed == expected, "formula_engine_sha256_manifest_mismatch")
    ids = manifest.get("formula_ids")
    _require(
        isinstance(ids, list) and tuple(ids) == FROZEN_FORMULA_IDS,
        "formula_ids_mismatch",
    )


def _load_yaml(path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise StrategySpecError(f"strategy package unreadable: {path.name}") from exc
    mapping = _as_mapping(raw, path.name)
    if mapping.get("version") != 1:
        raise StrategySpecError(f"{path.name} must be version=1")
    return mapping, _sha256_text(text)


def _common_fields(raw: Mapping[str, Any], *, config_hash: str) -> dict[str, Any]:
    return {
        "package_id": str(raw.get("package_id") or ""),
        "candidate_generation": str(raw.get("candidate_generation") or ""),
        "ranking": str(raw.get("ranking") or ""),
        "sizing": str(raw.get("sizing") or ""),
        "entry_kind": str(raw.get("entry_kind") or ""),
        "entry_after": str(raw.get("entry_after") or ""),
        "exit_kind": str(raw.get("exit_kind") or ""),
        "exit_event": str(raw.get("exit_event") or ""),
        "pnl_source": str(raw.get("pnl_source") or ""),
        "paper_status": str(raw.get("paper_status") or ""),
        "max_chase_days": _as_int(raw.get("max_chase_days"), "max_chase_days", required=True)
        or 0,
        "max_hold_calendar_days": _as_int(
            raw.get("max_hold_calendar_days"),
            "max_hold_calendar_days",
            required=False,
        ),
        "named_not_run_max_hold_calendar_days": _named_holds(
            raw.get("named_not_run_max_hold_calendar_days")
        ),
        "applicable_states": _states(raw.get("applicable_states")),
        "config_hash": config_hash,
    }


def _load_follow(raw: Mapping[str, Any], config_hash: str) -> tuple[StrategySpec, ...]:
    fields = _common_fields(raw, config_hash=config_hash)
    spec_id = str(raw.get("spec_id") or fields["package_id"])
    return (StrategySpec(spec_id=spec_id, frozen_artifact_sha256=None, **fields),)


def _load_rally(raw: Mapping[str, Any], config_hash: str) -> tuple[StrategySpec, ...]:
    fields = _common_fields(raw, config_hash=config_hash)
    _require(
        fields["candidate_generation"] == "rally_setup_pivot_confirmed_base_days",
        "main_rally_setup_signal_mismatch",
    )
    _require(
        fields["exit_kind"] == "not_implemented_full_episode",
        "main_rally_full_episode_must_be_stub",
    )
    spec_id = str(raw.get("spec_id") or fields["package_id"])
    return (StrategySpec(spec_id=spec_id, frozen_artifact_sha256=None, **fields),)


def _load_formulas(
    raw: Mapping[str, Any],
    config_hash: str,
    *,
    repo: Path,
) -> tuple[StrategySpec, ...]:
    expected = str(raw.get("frozen_formula_engine_sha256") or "")
    _require(bool(expected), "missing_formula_engine_sha256")
    _verify_formula_engine(expected, repo=repo)
    verify_frozen_challenger(repo=repo)
    ids = raw.get("formula_ids")
    _require(isinstance(ids, list), "formula_ids_mismatch")
    _require(tuple(str(item) for item in ids) == FROZEN_FORMULA_IDS, "formula_ids_mismatch")
    fields = _common_fields(raw, config_hash=config_hash)
    fields["candidate_generation"] = "frozen_formula_id"
    _require(fields["entry_kind"] == "next_tradable_open", "formula_entry_must_be_next_open")
    _require(fields["exit_kind"] == _FORMULA_EXIT_KIND, "missing_exit")
    _require(
        int(fields["max_chase_days"]) == _FORMULA_MAX_CHASE_DAYS,
        "formula_max_chase_days_must_be_3",
    )
    _require(
        fields["max_hold_calendar_days"] == _FORMULA_MAX_HOLD_CALENDAR_DAYS,
        "missing_exit",
    )
    _require(fields["pnl_source"] == CHALLENGER_PNL_SOURCE, "formula_pnl_must_be_challenger_next_open")
    _require(
        fields["paper_status"] == _FORMULA_PAPER_STATUS,
        "formula_paper_status_must_be_synthetic_smoke",
    )
    specs = []
    for formula_id in FROZEN_FORMULA_IDS:
        specs.append(
            StrategySpec(
                spec_id=f"formulas:{formula_id}",
                frozen_artifact_sha256=expected,
                **fields,
            )
        )
    return tuple(specs)


_LOADERS = {
    "institution_follow_v1": _load_follow,
    "main_rally_v1": _load_rally,
}


def load_strategy_package(
    package_id: str,
    *,
    config_dir: Path | str | None = None,
    repo: Path | str | None = None,
) -> tuple[StrategySpec, ...]:
    wanted = str(package_id or "")
    _require(wanted in ALLOWED_PACKAGES, "unknown_strategy_package")
    packages_dir = Path(config_dir) if config_dir else DEFAULT_PACKAGES_DIR
    root = Path(repo) if repo else REPO
    if not packages_dir.is_dir():
        raise StrategySpecError("strategy_packages_dir_missing")
    path = packages_dir / f"{wanted}.yaml"
    if not path.is_file():
        raise StrategySpecError(f"missing_strategy_package:{wanted}")
    raw, config_hash = _load_yaml(path)
    declared = str(raw.get("package_id") or "")
    _require(declared == wanted, f"package_id_mismatch:{wanted}")
    if wanted == "formulas":
        return _load_formulas(raw, config_hash, repo=root)
    return _LOADERS[wanted](raw, config_hash)


def load_all_strategy_packages(
    *,
    config_dir: Path | str | None = None,
    repo: Path | str | None = None,
) -> tuple[StrategySpec, ...]:
    loaded: list[StrategySpec] = []
    for package_id in sorted(ALLOWED_PACKAGES):
        loaded.extend(
            load_strategy_package(package_id, config_dir=config_dir, repo=repo)
        )
    return tuple(loaded)


def load_strategy_spec(
    spec_id: str,
    *,
    config_dir: Path | str | None = None,
    repo: Path | str | None = None,
) -> StrategySpec:
    wanted = str(spec_id or "")
    if wanted.startswith("formulas:"):
        package_id = "formulas"
    elif wanted in ALLOWED_PACKAGES:
        package_id = wanted
    else:
        raise StrategySpecError(f"unknown_strategy_spec:{wanted}")
    for spec in load_strategy_package(package_id, config_dir=config_dir, repo=repo):
        if spec.spec_id == wanted:
            return spec
    raise StrategySpecError(f"unknown_strategy_spec:{wanted}")


__all__ = [
    "ALLOWED_PACKAGES",
    "CHALLENGER_PNL_SOURCE",
    "DISCLOSURE_COVERAGE_DOMAINS",
    "FORBIDDEN_PNL_SOURCES",
    "FORMULA_ENGINE_SHA256",
    "FROZEN_FORMULA_IDS",
    "NON_DISCLOSURE_FREEZE_DOMAINS",
    "StrategySpec",
    "StrategySpecError",
    "disclosure_freeze_coverage",
    "disclosure_freeze_coverage_days",
    "load_all_strategy_packages",
    "load_source_module",
    "load_strategy_package",
    "load_strategy_spec",
    "verify_frozen_challenger",
]
