from typing import Any
from typing import Optional


__all__ = [
    'MootdxException',
    'MootdxValidationException',
    'MootdxDependencyException',
    'TdxhubModuleNotFoundError',
    'FileNeedRefresh',
]


class MootdxException(Exception):
    """Base exception for mootdx/tdxhub runtime errors."""

    def __init__(self, message: Optional[str] = None, *args: Any, **kwargs: Any) -> None:
        self.provider = kwargs.get('provider')
        self.response = kwargs.get('response')
        self.data = kwargs.get('data')
        self.message = message or kwargs.get('message') or (args[0] if args else '')
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__}: {self.message}>'


class MootdxValidationException(MootdxException, ValueError):
    """Raised when API parameters or market selectors are invalid."""


class MootdxDependencyException(MootdxException):
    """Raised when an optional runtime dependency is unavailable or broken."""


class TdxhubModuleNotFoundError(MootdxDependencyException, ModuleNotFoundError):
    """Raised when an optional import required by a feature is unavailable."""


class FileNeedRefresh(MootdxException, FileNotFoundError):
    """Raised by file cache helpers when an on-disk cache is stale."""
