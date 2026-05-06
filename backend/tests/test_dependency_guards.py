from __future__ import annotations

from services.dependency_guards import install_dependency_guards, install_py_mini_racer_safe_destructor


def test_py_mini_racer_safe_destructor_is_idempotent_and_suppresses_missing_ext():
    class FakeMiniRacer:
        pass

    assert install_py_mini_racer_safe_destructor(FakeMiniRacer) is True
    assert install_py_mini_racer_safe_destructor(FakeMiniRacer) is False

    instance = FakeMiniRacer()
    instance.ext = None
    FakeMiniRacer.__del__(instance)


def test_py_mini_racer_safe_destructor_calls_native_free_when_available():
    calls = []

    class FakeExt:
        def mr_free_context(self, ctx):
            calls.append(ctx)

    class FakeMiniRacer:
        pass

    assert install_py_mini_racer_safe_destructor(FakeMiniRacer) is True
    instance = FakeMiniRacer()
    instance.ext = FakeExt()
    instance.ctx = "ctx-a"

    FakeMiniRacer.__del__(instance)

    assert calls == ["ctx-a"]


def test_dependency_guard_installer_returns_list():
    applied = install_dependency_guards()

    assert isinstance(applied, list)
