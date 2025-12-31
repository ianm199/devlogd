"""Custom exceptions for devlogd."""


class DevlogError(Exception):
    """Base exception for all devlogd errors."""


class CDPConnectionError(DevlogError):
    """Failed to connect to Chrome DevTools Protocol."""


class ChromeNotFoundError(DevlogError):
    """Chrome executable not found or not running with debugging enabled."""


class TargetNotFoundError(DevlogError):
    """Requested target (tab/page) not found."""


class CDPProtocolError(DevlogError):
    """CDP returned an error response."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"CDP error {code}: {message}")
