"""Tests for the LogEvent model."""

import json
from datetime import UTC, datetime

from devlogd.core.log_event import (
    LogEvent,
    LogKind,
    LogLevel,
    SourceLocation,
    TargetInfo,
)


class TestLogEventFromConsoleAPICalled:
    """Tests for LogEvent.from_console_api_called."""

    def test_simple_string_log(self) -> None:
        event = {
            "type": "log",
            "args": [{"type": "string", "value": "Hello, world!"}],
            "timestamp": 1702828800000.0,
            "executionContextId": 1,
        }

        log = LogEvent.from_console_api_called(event)

        assert log.level == LogLevel.INFO
        assert log.kind == LogKind.CONSOLE
        assert log.text == "Hello, world!"
        assert log.args == ["Hello, world!"]
        assert log.execution_context_id == 1

    def test_console_error(self) -> None:
        event = {
            "type": "error",
            "args": [{"type": "string", "value": "Something went wrong"}],
            "timestamp": 1702828800000.0,
        }

        log = LogEvent.from_console_api_called(event)

        assert log.level == LogLevel.ERROR
        assert log.text == "Something went wrong"

    def test_console_warn(self) -> None:
        event = {
            "type": "warn",
            "args": [{"type": "string", "value": "Deprecation warning"}],
            "timestamp": 1702828800000.0,
        }

        log = LogEvent.from_console_api_called(event)

        assert log.level == LogLevel.WARN

    def test_multiple_args(self) -> None:
        event = {
            "type": "log",
            "args": [
                {"type": "string", "value": "Count:"},
                {"type": "number", "value": 42},
                {"type": "boolean", "value": True},
            ],
            "timestamp": 1702828800000.0,
        }

        log = LogEvent.from_console_api_called(event)

        assert log.text == "Count: 42 true"
        assert log.args == ["Count:", 42, True]

    def test_with_stack_trace(self) -> None:
        event = {
            "type": "log",
            "args": [{"type": "string", "value": "test"}],
            "timestamp": 1702828800000.0,
            "stackTrace": {
                "callFrames": [
                    {
                        "functionName": "myFunction",
                        "url": "http://localhost:3000/app.js",
                        "lineNumber": 42,
                        "columnNumber": 10,
                    }
                ]
            },
        }

        log = LogEvent.from_console_api_called(event)

        assert log.source is not None
        assert log.source.url == "http://localhost:3000/app.js"
        assert log.source.line == 42
        assert log.source.column == 10

    def test_with_target_info(self) -> None:
        event = {
            "type": "log",
            "args": [{"type": "string", "value": "test"}],
            "timestamp": 1702828800000.0,
        }
        target = TargetInfo(id="123", title="My Page", url="http://localhost:3000")

        log = LogEvent.from_console_api_called(event, target)

        assert log.target is not None
        assert log.target.id == "123"
        assert log.target.title == "My Page"

    def test_object_with_preview(self) -> None:
        event = {
            "type": "log",
            "args": [
                {
                    "type": "object",
                    "preview": {
                        "type": "object",
                        "properties": [
                            {"name": "name", "value": "test"},
                            {"name": "count", "value": "5"},
                        ],
                        "overflow": False,
                    },
                }
            ],
            "timestamp": 1702828800000.0,
        }

        log = LogEvent.from_console_api_called(event)

        assert "name: test" in log.text
        assert "count: 5" in log.text

    def test_array_preview(self) -> None:
        event = {
            "type": "log",
            "args": [
                {
                    "type": "object",
                    "subtype": "array",
                    "preview": {
                        "type": "object",
                        "subtype": "array",
                        "properties": [
                            {"name": "0", "value": "a"},
                            {"name": "1", "value": "b"},
                        ],
                        "overflow": False,
                    },
                }
            ],
            "timestamp": 1702828800000.0,
        }

        log = LogEvent.from_console_api_called(event)

        assert log.text == "[a, b]"

    def test_undefined_and_null(self) -> None:
        event = {
            "type": "log",
            "args": [
                {"type": "undefined"},
                {"type": "object", "subtype": "null"},
            ],
            "timestamp": 1702828800000.0,
        }

        log = LogEvent.from_console_api_called(event)

        assert log.text == "undefined null"
        assert log.args == [None, None]


class TestLogEventFromExceptionThrown:
    """Tests for LogEvent.from_exception_thrown."""

    def test_basic_exception(self) -> None:
        event = {
            "timestamp": 1702828800000.0,
            "exceptionDetails": {
                "exception": {
                    "type": "object",
                    "subtype": "error",
                    "description": "Error: Something failed\n    at myFunc (app.js:10:5)",
                },
                "executionContextId": 1,
                "stackTrace": {
                    "callFrames": [
                        {
                            "functionName": "myFunc",
                            "url": "http://localhost:3000/app.js",
                            "lineNumber": 10,
                            "columnNumber": 5,
                        }
                    ]
                },
            },
        }

        log = LogEvent.from_exception_thrown(event)

        assert log.level == LogLevel.ERROR
        assert log.kind == LogKind.EXCEPTION
        assert "Something failed" in log.text
        assert log.source is not None
        assert log.source.line == 10
        assert log.stack is not None
        assert "myFunc" in log.stack

    def test_exception_without_stack(self) -> None:
        event = {
            "timestamp": 1702828800000.0,
            "exceptionDetails": {
                "exception": {
                    "value": "Uncaught string error",
                }
            },
        }

        log = LogEvent.from_exception_thrown(event)

        assert log.text == "Uncaught string error"
        assert log.stack is None


class TestLogEventFromLogEntryAdded:
    """Tests for LogEvent.from_log_entry_added."""

    def test_browser_warning(self) -> None:
        event = {
            "entry": {
                "source": "deprecation",
                "level": "warning",
                "text": "Feature X is deprecated",
                "timestamp": 1702828800000.0,
                "url": "http://localhost:3000/index.html",
                "lineNumber": 5,
            }
        }

        log = LogEvent.from_log_entry_added(event)

        assert log.level == LogLevel.WARN
        assert log.kind == LogKind.BROWSER_LOG
        assert log.text == "Feature X is deprecated"
        assert log.source is not None
        assert log.source.url == "http://localhost:3000/index.html"

    def test_network_error(self) -> None:
        event = {
            "entry": {
                "source": "network",
                "level": "error",
                "text": "Failed to load resource: 404",
                "timestamp": 1702828800000.0,
            }
        }

        log = LogEvent.from_log_entry_added(event)

        assert log.level == LogLevel.ERROR
        assert log.text == "Failed to load resource: 404"


class TestLogEventSerialization:
    """Tests for LogEvent serialization."""

    def test_to_ndjson(self) -> None:
        log = LogEvent(
            ts=datetime(2023, 12, 17, 12, 0, 0, tzinfo=UTC),
            level=LogLevel.INFO,
            kind=LogKind.CONSOLE,
            text="Test message",
        )

        ndjson = log.to_ndjson()
        parsed = json.loads(ndjson)

        assert parsed["level"] == "info"
        assert parsed["kind"] == "console"
        assert parsed["text"] == "Test message"

    def test_to_pretty(self) -> None:
        log = LogEvent(
            ts=datetime(2023, 12, 17, 12, 0, 0, tzinfo=UTC),
            level=LogLevel.ERROR,
            kind=LogKind.CONSOLE,
            text="Error occurred",
            source=SourceLocation(url="http://localhost:3000/app.js", line=42, column=5),
        )

        pretty = log.to_pretty()

        assert "ERROR" in pretty
        assert "Error occurred" in pretty
        assert "app.js:42" in pretty

    def test_roundtrip_serialization(self) -> None:
        original = LogEvent(
            ts=datetime(2023, 12, 17, 12, 0, 0, tzinfo=UTC),
            level=LogLevel.WARN,
            kind=LogKind.EXCEPTION,
            text="Warning message",
            args=["arg1", 42],
            source=SourceLocation(url="test.js", line=10, column=5),
            stack="    at test (test.js:10:5)",
            target=TargetInfo(id="123", title="Test", url="http://test"),
            execution_context_id=1,
        )

        json_str = original.model_dump_json()
        restored = LogEvent.model_validate_json(json_str)

        assert restored.level == original.level
        assert restored.kind == original.kind
        assert restored.text == original.text
        assert restored.args == original.args
        assert restored.source == original.source
        assert restored.stack == original.stack
        assert restored.target == original.target
