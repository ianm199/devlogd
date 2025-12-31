"""Normalized log event model for devlogd.

All CDP log sources (Runtime.consoleAPICalled, Runtime.exceptionThrown, Log.entryAdded)
are normalized into this single schema.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    """Log severity level."""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class LogKind(str, Enum):
    """Source type of the log entry."""

    CONSOLE = "console"
    EXCEPTION = "exception"
    BROWSER_LOG = "browser_log"


class WatchEventKind(str, Enum):
    """Type of watch event."""

    CLICK = "click"
    NAVIGATION = "navigation"
    REQUEST = "request"
    REDIRECT = "redirect"
    MESSAGE = "message"
    RESPONSE = "response"
    FAILED = "failed"


class TargetInfo(BaseModel):
    """Information about the CDP target that produced the log."""

    id: str
    title: str
    url: str


class SourceLocation(BaseModel):
    """Source code location for the log entry."""

    url: str
    line: int
    column: int


class LogEvent(BaseModel):
    """Normalized log event from Chrome DevTools.

    This model unifies events from:
    - Runtime.consoleAPICalled (console.log, console.error, etc.)
    - Runtime.exceptionThrown (uncaught exceptions)
    - Log.entryAdded (browser-level logs, network errors, deprecations)
    """

    ts: datetime = Field(description="Timestamp in ISO8601 format with timezone")
    level: LogLevel = Field(description="Log severity level")
    kind: LogKind = Field(description="Source type of the log entry")
    text: str = Field(description="Rendered message text")
    args: list[Any] = Field(default_factory=list, description="Structured console arguments")
    source: SourceLocation | None = Field(default=None, description="Source code location")
    stack: str | None = Field(default=None, description="Stack trace for exceptions")
    target: TargetInfo | None = Field(default=None, description="Target that produced this log")
    execution_context_id: int | None = Field(default=None, description="CDP execution context ID")

    @classmethod
    def from_console_api_called(
        cls,
        event: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "LogEvent":
        """Create LogEvent from Runtime.consoleAPICalled CDP event."""
        console_type = event.get("type", "log")
        level = _map_console_type_to_level(console_type)

        args = event.get("args", [])
        text = _render_console_args(args)

        stack_trace = event.get("stackTrace", {})
        call_frames = stack_trace.get("callFrames", [])
        source = None
        if call_frames:
            frame = call_frames[0]
            source = SourceLocation(
                url=frame.get("url", ""),
                line=frame.get("lineNumber", 0),
                column=frame.get("columnNumber", 0),
            )

        ts = _parse_cdp_timestamp(event.get("timestamp"))

        return cls(
            ts=ts,
            level=level,
            kind=LogKind.CONSOLE,
            text=text,
            args=_extract_arg_values(args),
            source=source,
            target=target,
            execution_context_id=event.get("executionContextId"),
        )

    @classmethod
    def from_exception_thrown(
        cls,
        event: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "LogEvent":
        """Create LogEvent from Runtime.exceptionThrown CDP event."""
        exception_details = event.get("exceptionDetails", {})
        exception = exception_details.get("exception", {})

        text = exception.get("description", exception.get("value", "Unknown exception"))

        stack_trace = exception_details.get("stackTrace", {})
        call_frames = stack_trace.get("callFrames", [])
        stack_str = None
        source = None

        if call_frames:
            frame = call_frames[0]
            source = SourceLocation(
                url=frame.get("url", ""),
                line=frame.get("lineNumber", 0),
                column=frame.get("columnNumber", 0),
            )
            stack_str = _format_stack_trace(call_frames)

        ts = _parse_cdp_timestamp(event.get("timestamp"))

        return cls(
            ts=ts,
            level=LogLevel.ERROR,
            kind=LogKind.EXCEPTION,
            text=text,
            source=source,
            stack=stack_str,
            target=target,
            execution_context_id=exception_details.get("executionContextId"),
        )

    @classmethod
    def from_log_entry_added(
        cls,
        event: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "LogEvent":
        """Create LogEvent from Log.entryAdded CDP event."""
        entry = event.get("entry", {})

        level_str = entry.get("level", "info")
        level = _map_log_level(level_str)

        text = entry.get("text", "")
        url = entry.get("url", "")
        line = entry.get("lineNumber", 0)

        source = None
        if url:
            source = SourceLocation(url=url, line=line, column=0)

        ts = _parse_cdp_timestamp(entry.get("timestamp"))

        stack_trace = entry.get("stackTrace", {})
        call_frames = stack_trace.get("callFrames", [])
        stack_str = _format_stack_trace(call_frames) if call_frames else None

        return cls(
            ts=ts,
            level=level,
            kind=LogKind.BROWSER_LOG,
            text=text,
            source=source,
            stack=stack_str,
            target=target,
        )

    def to_ndjson(self) -> str:
        """Serialize to newline-delimited JSON."""
        return self.model_dump_json()

    def to_pretty(self) -> str:
        """Format for human-readable output."""
        ts_str = self.ts.strftime("%H:%M:%S.%f")[:-3]
        level_str = self.level.value.upper().ljust(5)

        location = ""
        if self.source and self.source.url:
            if self.kind == LogKind.BROWSER_LOG and self.level == LogLevel.ERROR:
                location = f"\n         └─ {self.source.url}"
            else:
                filename = self.source.url.split("/")[-1]
                if filename:
                    location = f" [{filename}:{self.source.line}]"

        return f"{ts_str} {level_str} {self.text}{location}"

    def to_tsv(self) -> str:
        """Format as tab-separated values for easy piping to cut/awk."""
        ts_str = self.ts.strftime("%H:%M:%S.%f")[:-3]
        level_str = self.level.value.upper()
        text_escaped = self.text.replace("\t", " ").replace("\n", "\\n")
        return f"{ts_str}\t{level_str}\t{text_escaped}"


def _map_console_type_to_level(console_type: str) -> LogLevel:
    """Map console.* method type to LogLevel."""
    mapping = {
        "log": LogLevel.INFO,
        "info": LogLevel.INFO,
        "debug": LogLevel.DEBUG,
        "warn": LogLevel.WARN,
        "warning": LogLevel.WARN,
        "error": LogLevel.ERROR,
        "assert": LogLevel.ERROR,
        "trace": LogLevel.DEBUG,
        "dir": LogLevel.INFO,
        "dirxml": LogLevel.INFO,
        "table": LogLevel.INFO,
        "count": LogLevel.INFO,
        "countReset": LogLevel.INFO,
        "time": LogLevel.INFO,
        "timeEnd": LogLevel.INFO,
        "timeLog": LogLevel.INFO,
        "group": LogLevel.INFO,
        "groupCollapsed": LogLevel.INFO,
        "groupEnd": LogLevel.INFO,
        "clear": LogLevel.INFO,
    }
    return mapping.get(console_type, LogLevel.INFO)


def _map_log_level(level: str) -> LogLevel:
    """Map Log.LogEntry level to LogLevel."""
    mapping = {
        "verbose": LogLevel.DEBUG,
        "info": LogLevel.INFO,
        "warning": LogLevel.WARN,
        "error": LogLevel.ERROR,
    }
    return mapping.get(level, LogLevel.INFO)


def _render_console_args(args: list[dict[str, Any]]) -> str:
    """Render CDP RemoteObject args to a readable string."""
    parts: list[str] = []
    for arg in args:
        obj_type = arg.get("type", "")
        subtype = arg.get("subtype", "")

        if obj_type == "string":
            parts.append(arg.get("value", ""))
        elif obj_type == "number":
            parts.append(str(arg.get("value", "")))
        elif obj_type == "boolean":
            parts.append(str(arg.get("value", "")).lower())
        elif obj_type == "undefined":
            parts.append("undefined")
        elif subtype == "null":
            parts.append("null")
        elif obj_type == "object":
            if "description" in arg:
                parts.append(arg["description"])
            elif "preview" in arg:
                parts.append(_render_preview(arg["preview"]))
            else:
                parts.append(f"[{subtype or 'object'}]")
        elif obj_type == "function":
            parts.append(arg.get("description", "[function]"))
        elif obj_type == "symbol":
            parts.append(arg.get("description", "[symbol]"))
        else:
            parts.append(arg.get("description", str(arg.get("value", ""))))

    return " ".join(parts)


def _render_preview(preview: dict[str, Any]) -> str:
    """Render a CDP ObjectPreview to string."""
    obj_type: str = preview.get("type", "")
    subtype: str = preview.get("subtype", "")
    description: str = preview.get("description", "")

    if subtype == "array":
        props = preview.get("properties", [])
        items = [p.get("value", "?") for p in props]
        overflow = "..." if preview.get("overflow", False) else ""
        return f"[{', '.join(items)}{overflow}]"

    if obj_type == "object":
        props = preview.get("properties", [])
        items = [f"{p.get('name', '?')}: {p.get('value', '?')}" for p in props]
        overflow = "..." if preview.get("overflow", False) else ""
        return "{" + ", ".join(items) + overflow + "}"

    return description


def _extract_arg_values(args: list[dict[str, Any]]) -> list[Any]:
    """Extract simple values from CDP args for structured storage."""
    values: list[Any] = []
    for arg in args:
        obj_type = arg.get("type", "")
        subtype = arg.get("subtype", "")

        if obj_type in ("string", "number", "boolean"):
            values.append(arg.get("value"))
        elif obj_type == "undefined" or subtype == "null":
            values.append(None)
        else:
            values.append(arg.get("description", f"[{obj_type}]"))

    return values


def _format_stack_trace(call_frames: list[dict[str, Any]]) -> str:
    """Format CDP stack trace call frames."""
    lines: list[str] = []
    for frame in call_frames:
        fn_name = frame.get("functionName", "(anonymous)")
        url = frame.get("url", "")
        line_num = frame.get("lineNumber", 0)
        col = frame.get("columnNumber", 0)
        lines.append(f"    at {fn_name} ({url}:{line_num}:{col})")
    return "\n".join(lines)


def _parse_cdp_timestamp(timestamp: float | None) -> datetime:
    """Parse CDP timestamp (milliseconds since epoch) to datetime."""
    if timestamp is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(timestamp / 1000.0, tz=UTC)


class WatchEvent(BaseModel):
    """Event captured during watch mode (clicks, navigation, network)."""

    ts: datetime = Field(description="Timestamp in ISO8601 format with timezone")
    kind: WatchEventKind = Field(description="Type of watch event")
    url: str = Field(description="URL involved in the event")
    method: str | None = Field(default=None, description="HTTP method for requests")
    status: int | None = Field(default=None, description="HTTP status code")
    resource_type: str | None = Field(default=None, description="Resource type (Document, XHR, etc)")
    redirect_url: str | None = Field(default=None, description="Redirect destination URL")
    element: dict[str, Any] | None = Field(default=None, description="Clicked element info")
    target: TargetInfo | None = Field(default=None, description="Target that produced this event")
    request_id: str | None = Field(default=None, description="CDP request ID for correlation")
    error_text: str | None = Field(default=None, description="Error description for failed requests")
    mime_type: str | None = Field(default=None, description="Response MIME type")
    frame_id: str | None = Field(default=None, description="Frame ID (for iframe attribution)")

    @classmethod
    def from_request_will_be_sent(
        cls,
        params: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "WatchEvent":
        """Create from Network.requestWillBeSent event."""
        request = params.get("request", {})
        redirect_response = params.get("redirectResponse")

        kind = WatchEventKind.REDIRECT if redirect_response else WatchEventKind.REQUEST

        return cls(
            ts=_parse_cdp_timestamp(params.get("timestamp")),
            kind=kind,
            url=request.get("url", ""),
            method=request.get("method"),
            resource_type=params.get("type"),
            redirect_url=request.get("url") if redirect_response else None,
            status=redirect_response.get("status") if redirect_response else None,
            target=target,
            request_id=params.get("requestId"),
            frame_id=params.get("frameId"),
        )

    @classmethod
    def from_response_received(
        cls,
        params: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "WatchEvent":
        """Create from Network.responseReceived event."""
        response = params.get("response", {})

        return cls(
            ts=_parse_cdp_timestamp(params.get("timestamp")),
            kind=WatchEventKind.RESPONSE,
            url=response.get("url", ""),
            status=response.get("status"),
            resource_type=params.get("type"),
            target=target,
            request_id=params.get("requestId"),
            frame_id=params.get("frameId"),
            mime_type=response.get("mimeType"),
        )

    @classmethod
    def from_loading_failed(
        cls,
        params: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "WatchEvent":
        """Create from Network.loadingFailed event."""
        return cls(
            ts=_parse_cdp_timestamp(params.get("timestamp")),
            kind=WatchEventKind.FAILED,
            url="",
            resource_type=params.get("type"),
            target=target,
            request_id=params.get("requestId"),
            error_text=params.get("errorText"),
        )

    @classmethod
    def from_frame_navigated(
        cls,
        params: dict[str, Any],
        target: TargetInfo | None = None,
    ) -> "WatchEvent":
        """Create from Page.frameNavigated event."""
        frame = params.get("frame", {})

        return cls(
            ts=datetime.now(UTC),
            kind=WatchEventKind.NAVIGATION,
            url=frame.get("url", ""),
            target=target,
        )

    @classmethod
    def from_click_console(
        cls,
        log_event: "LogEvent",
    ) -> "WatchEvent | None":
        """Create from a console log that matches our injected click listener format."""
        if not log_event.text.startswith("[DEVLOG_CLICK]"):
            return None

        return cls(
            ts=log_event.ts,
            kind=WatchEventKind.CLICK,
            url="",
            element={"raw": log_event.text},
            target=log_event.target,
        )

    @classmethod
    def from_message_console(
        cls,
        log_event: "LogEvent",
    ) -> "WatchEvent | None":
        """Create from a console log that matches our injected message listener format."""
        if not log_event.text.startswith("[DEVLOG_MESSAGE]"):
            return None

        return cls(
            ts=log_event.ts,
            kind=WatchEventKind.MESSAGE,
            url="",
            element={"raw": log_event.text},
            target=log_event.target,
        )

    def to_pretty(self) -> str:
        """Format for human-readable output."""
        ts_str = self.ts.strftime("%H:%M:%S.%f")[:-3]
        kind_str = self.kind.value.upper().ljust(10)

        if self.kind == WatchEventKind.CLICK:
            element_info = ""
            if self.element and "raw" in self.element:
                element_info = self.element["raw"].replace("[DEVLOG_CLICK] ", "")
            return f"{ts_str} {kind_str} {element_info}"

        if self.kind == WatchEventKind.MESSAGE:
            message_info = ""
            if self.element and "raw" in self.element:
                message_info = self.element["raw"].replace("[DEVLOG_MESSAGE] ", "")
            return f"{ts_str} {kind_str} {message_info}"

        if self.kind == WatchEventKind.REDIRECT:
            return f"{ts_str} {kind_str} {self.status} → {self.url}"

        if self.kind == WatchEventKind.NAVIGATION:
            return f"{ts_str} {kind_str} {self.url}"

        if self.kind == WatchEventKind.RESPONSE:
            resource = f" [{self.resource_type}]" if self.resource_type else ""
            return f"{ts_str} {kind_str} {self.status} {self.url}{resource}"

        if self.kind == WatchEventKind.FAILED:
            resource = f" [{self.resource_type}]" if self.resource_type else ""
            error = self.error_text if self.error_text else "failed"
            return f"{ts_str} {kind_str} {error} {self.url}{resource}"

        method = self.method if self.method else "GET"
        resource = f" [{self.resource_type}]" if self.resource_type else ""
        return f"{ts_str} {kind_str} {method} {self.url}{resource}"

    def to_ndjson(self) -> str:
        """Serialize to newline-delimited JSON."""
        return self.model_dump_json()
