import logging


logger = logging.getLogger("cm-api")


def _patch_py_mini_racer_destructor() -> bool:
    try:
        from py_mini_racer.py_mini_racer import MiniRacer
    except Exception:
        return False

    if getattr(MiniRacer, "_cm_safe_del_patched", False):
        return False

    def _safe_del(self):
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

    MiniRacer.__del__ = _safe_del
    MiniRacer._cm_safe_del_patched = True
    return True


def apply_runtime_patches() -> None:
    patched = []
    if _patch_py_mini_racer_destructor():
        patched.append("py_mini_racer.__del__")
    if patched:
        logger.info("[runtime] applied patches: %s", ", ".join(patched))