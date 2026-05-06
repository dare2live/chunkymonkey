"""Narrow runtime guards for third-party dependency defects."""
from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger("cm-api.dependency_guards")


def install_py_mini_racer_safe_destructor(mini_racer_cls: Any | None = None) -> bool:
    """Guard py_mini_racer cleanup when its native extension fails to initialize."""
    if mini_racer_cls is None:
        try:
            from py_mini_racer.py_mini_racer import MiniRacer as mini_racer_cls
        except Exception:
            return False

    if getattr(mini_racer_cls, "_cm_safe_del_patched", False):
        return False

    def _safe_del(self: Any) -> None:
        try:
            ext = getattr(self, "ext", None)
            if ext is None:
                return
            free_context = getattr(ext, "mr_free_context", None)
            if free_context is None:
                return
            free_context(getattr(self, "ctx", None))
        except Exception:
            return

    mini_racer_cls.__del__ = _safe_del
    mini_racer_cls._cm_safe_del_patched = True
    return True


def install_dependency_guards() -> list[str]:
    applied = []
    if install_py_mini_racer_safe_destructor():
        applied.append("py_mini_racer_safe_destructor")
    if applied:
        logger.info("[dependency] installed guards: %s", ", ".join(applied))
    return applied
