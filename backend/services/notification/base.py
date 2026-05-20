"""Notification driver base classes and config helpers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseDriver(ABC):
    """Abstract notification driver."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    def send(self, subject: str, body: str, severity: str) -> bool:
        """Send one notification."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {}
    text = cfg_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        parsed = _parse_simple_yaml(text)
        return parsed if isinstance(parsed, dict) else {}


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none"}:
        return None
    try:
        return int(text)
    except ValueError:  # rule-compliance: ok evidence=narrow-int-fallback-to-float
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _prepare_yaml_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    return lines


def _parse_simple_yaml(text: str) -> Any:
    lines = _prepare_yaml_lines(text)
    if not lines:
        return {}
    value, _ = _parse_block(lines, 0, lines[0][0])
    return value


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    is_list = lines[index][1].startswith("- ")
    if is_list:
        values: list[Any] = []
        while index < len(lines):
            current_indent, content = lines[index]
            if current_indent != indent or not content.startswith("- "):
                break
            item = content[2:].strip()
            index += 1
            if not item:
                if index < len(lines) and lines[index][0] > current_indent:
                    nested, index = _parse_block(lines, index, lines[index][0])
                    values.append(nested)
                else:
                    values.append(None)
            elif ":" in item and not item.startswith(("http://", "https://")):
                key, raw_value = item.split(":", 1)
                values.append({key.strip(): _parse_scalar(raw_value.strip())})
            else:
                values.append(_parse_scalar(item))
        return values, index

    values: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent != indent or content.startswith("- "):
            break
        if ":" not in content:
            index += 1
            continue
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            values[key] = _parse_scalar(raw_value)
        elif index < len(lines) and lines[index][0] > current_indent:
            nested, index = _parse_block(lines, index, lines[index][0])
            values[key] = nested
        else:
            values[key] = {}
    return values, index
