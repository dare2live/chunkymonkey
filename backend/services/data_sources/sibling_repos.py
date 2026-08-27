"""Sibling vendor/protocol checkouts (miaoxiang / tdxhub / fuyao).

Physical trees live next to this git root under ``Documents/M/stock/``.
Chunkymonkey imports from those trees; it does not vendor copies.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent
_CONFIG_PATH = _BACKEND_ROOT / "config" / "sibling_repos.yaml"
_STOCK_ROOT_ENV = "CHUNKY_STOCK_ROOT"


@dataclass(frozen=True)
class SiblingRepoSpec:
    alias: str
    dirname: str
    role: str
    required: bool = True
    pythonpath: tuple[str, ...] = field(default_factory=tuple)
    required_marker: str = ""
    upstream: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SiblingRepos:
    repo_root: Path
    stock_root: Path
    repos: dict[str, SiblingRepoSpec]

    def require(self, alias: str) -> SiblingRepoSpec:
        try:
            return self.repos[alias]
        except KeyError as exc:
            known = ", ".join(sorted(self.repos))
            raise KeyError(f"unknown sibling repo {alias!r}; known: {known}") from exc

    def path_for(self, alias: str) -> Path:
        spec = self.require(alias)
        nested = self.repo_root / spec.dirname
        sibling = self.stock_root / spec.dirname
        if sibling.is_dir():
            return sibling
        if nested.is_dir():
            return nested
        return sibling

    def is_present(self, alias: str) -> bool:
        root = self.path_for(alias)
        spec = self.require(alias)
        if not root.is_dir():
            return False
        if not spec.required_marker:
            return True
        return (root / spec.required_marker).is_file()

    def pythonpath_dirs(self, alias: str) -> tuple[Path, ...]:
        root = self.path_for(alias)
        spec = self.require(alias)
        if not spec.pythonpath:
            return (root,)
        return tuple(root / rel for rel in spec.pythonpath)


def _as_tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _parse_repo(alias: str, raw: dict[str, Any]) -> SiblingRepoSpec:
    dirname = str(raw.get("dirname") or alias).strip()
    if not dirname:
        raise ValueError(f"sibling repo {alias!r} needs dirname")
    return SiblingRepoSpec(
        alias=alias,
        dirname=dirname,
        role=str(raw.get("role") or ""),
        required=bool(raw.get("required", True)),
        pythonpath=tuple(str(item) for item in _as_tuple(raw.get("pythonpath"))),
        required_marker=str(raw.get("required_marker") or ""),
        upstream=str(raw.get("upstream") or ""),
        notes=tuple(str(item) for item in _as_tuple(raw.get("notes"))),
    )


def default_stock_root(repo_root: Path | None = None) -> Path:
    root = repo_root or _REPO_ROOT
    override = os.environ.get(_STOCK_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return root.parent


def load_sibling_repos(
    path: Path | None = None,
    *,
    repo_root: Path | None = None,
    stock_root: Path | None = None,
) -> SiblingRepos:
    p = path or _CONFIG_PATH
    with open(p, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    root = repo_root or _REPO_ROOT
    repos = {
        alias: _parse_repo(alias, spec or {})
        for alias, spec in (raw.get("repos") or {}).items()
    }
    return SiblingRepos(
        repo_root=root,
        stock_root=stock_root or default_stock_root(root),
        repos=repos,
    )


_CACHED: Optional[SiblingRepos] = None


def get_sibling_repos() -> SiblingRepos:
    global _CACHED
    if _CACHED is None:
        _CACHED = load_sibling_repos()
    return _CACHED


def reload_sibling_repos() -> SiblingRepos:
    global _CACHED
    _CACHED = None
    return get_sibling_repos()


def ensure_import_path(
    alias: str,
    *,
    repos: SiblingRepos | None = None,
    strict: bool = False,
) -> Path:
    """Put sibling pythonpath dirs on ``sys.path``. Return the checkout root.

    ``strict=False`` matches the old miaoxiang insert (path may not exist yet).
    ``strict=True`` is for live Fuyao dump calls that must have the checkout.
    """
    catalog = repos or get_sibling_repos()
    root = catalog.path_for(alias)
    spec = catalog.require(alias)
    if strict and spec.required and not catalog.is_present(alias):
        raise FileNotFoundError(
            f"sibling repo {alias!r} missing at {root} "
            f"(clone {spec.upstream or spec.dirname} next to chunkymonkey)"
        )
    for directory in catalog.pythonpath_dirs(alias):
        text = str(directory)
        if text not in sys.path:
            sys.path.insert(0, text)
    return root


__all__ = [
    "SiblingRepoSpec",
    "SiblingRepos",
    "default_stock_root",
    "ensure_import_path",
    "get_sibling_repos",
    "load_sibling_repos",
    "reload_sibling_repos",
]
