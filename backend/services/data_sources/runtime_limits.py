"""Shared process limits for synchronous provider requests."""

from __future__ import annotations

import math
import socket
import threading
from typing import Any


def fetch_socket_timeout_seconds(spec: dict[str, Any]) -> float:
    """Return the required registry-owned provider timeout."""

    if "fetch_timeout_seconds" not in spec:
        raise ValueError("fetch_timeout_seconds is required")
    raw = spec["fetch_timeout_seconds"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("fetch_timeout_seconds must be a positive number")
    seconds = float(raw)
    if (
        not math.isfinite(seconds)
        or seconds <= 0
        or seconds > threading.TIMEOUT_MAX
    ):
        raise ValueError("fetch_timeout_seconds must be a positive number")
    return seconds


def apply_fetch_socket_timeout(spec: dict[str, Any]) -> float:
    """Apply the registry-owned provider timeout and return the proven value."""

    seconds = fetch_socket_timeout_seconds(spec)
    socket.setdefaulttimeout(seconds)
    return seconds


__all__ = ["apply_fetch_socket_timeout", "fetch_socket_timeout_seconds"]
