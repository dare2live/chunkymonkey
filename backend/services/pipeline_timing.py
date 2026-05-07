"""Small helpers for auditable pipeline stage timing."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator


class PipelineTimer:
    """Collect stage timings in the manifest shape expected by DQ gates."""

    def __init__(self) -> None:
        self.stage_timings: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - started)

    def record(self, name: str, duration_s: float) -> None:
        self.stage_timings[name] = round(float(duration_s), 3)

    def summary(self, payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        payload = dict(payload or {})
        payload.update(extra)
        payload["stage_timings"] = dict(self.stage_timings)
        return payload
