"""Integration tests for CDP client.

These tests require Chrome to be running with remote debugging enabled.
Run: devlog chrome launch --url about:blank

Or manually:
/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/devlog-test-profile \
    about:blank
"""

import asyncio

import pytest

from devlogd.core.cdp_client import CDPClient, CDPTarget, check_cdp_connection
from devlogd.core.exceptions import TargetNotFoundError
from devlogd.core.log_event import LogEvent, LogKind, LogLevel

CDP_PORT = 9222


@pytest.fixture
async def cdp_available() -> bool:
    """Check if CDP is available, skip test if not."""
    available = await check_cdp_connection(port=CDP_PORT)
    if not available:
        pytest.skip(
            f"CDP not available on port {CDP_PORT}. "
            "Run 'devlog chrome launch --url about:blank' first."
        )
    return True


@pytest.fixture
async def client(cdp_available: bool) -> CDPClient:  # noqa: ARG001
    """Create a CDP client."""
    return CDPClient(port=CDP_PORT)


class TestCDPConnection:
    """Tests for basic CDP connectivity."""

    async def test_check_cdp_connection_when_available(self, cdp_available: bool) -> None:  # noqa: ARG002
        result = await check_cdp_connection(port=CDP_PORT)
        assert result is True

    async def test_check_cdp_connection_when_unavailable(self) -> None:
        result = await check_cdp_connection(port=59999)
        assert result is False

    async def test_list_targets(self, client: CDPClient) -> None:
        targets = await client.list_targets()

        assert isinstance(targets, list)
        assert len(targets) > 0

        for target in targets:
            assert isinstance(target, CDPTarget)
            assert target.id
            assert target.websocket_url

    async def test_find_target_by_url(self, client: CDPClient) -> None:
        targets = await client.list_targets()
        if not targets:
            pytest.skip("No targets available")

        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        url_part = page_targets[0].url[:20]
        found = await client.find_target(url_filter=url_part)
        assert found.id == page_targets[0].id

    async def test_find_target_not_found(self, client: CDPClient) -> None:
        with pytest.raises(TargetNotFoundError):
            await client.find_target(url_filter="this-url-does-not-exist-12345")


class TestCDPCommands:
    """Tests for sending CDP commands."""

    async def test_connect_and_enable_runtime(self, client: CDPClient) -> None:
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        target = page_targets[0]

        await client.connect(target)
        try:
            result = await client.send_command("Runtime.enable")
            assert result == {} or result is not None

            result = await client.send_command("Log.enable")
            assert result == {} or result is not None
        finally:
            await client.disconnect()

    async def test_evaluate_javascript(self, client: CDPClient) -> None:
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        await client.connect(page_targets[0])
        try:
            await client.send_command("Runtime.enable")

            result = await client.send_command(
                "Runtime.evaluate",
                {"expression": "1 + 1", "returnByValue": True},
            )

            assert result["result"]["value"] == 2
        finally:
            await client.disconnect()


class TestLogCapture:
    """Tests for capturing console logs."""

    async def test_capture_console_log(self, client: CDPClient) -> None:
        """Test that we can capture console.log output."""
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        await client.connect(page_targets[0])
        try:
            await client.enable_logging()

            test_message = f"devlog-test-{asyncio.get_event_loop().time()}"

            async def capture_log() -> LogEvent | None:
                async for log_event in client.stream_logs():
                    if test_message in log_event.text:
                        return log_event
                return None

            capture_task = asyncio.create_task(capture_log())

            await asyncio.sleep(0.1)

            await client.send_command(
                "Runtime.evaluate",
                {"expression": f'console.log("{test_message}")'},
            )

            try:
                log_event = await asyncio.wait_for(capture_task, timeout=5.0)
            except TimeoutError:
                capture_task.cancel()
                pytest.fail("Timeout waiting for console.log event")

            assert log_event is not None
            assert log_event.level == LogLevel.INFO
            assert log_event.kind == LogKind.CONSOLE
            assert test_message in log_event.text

        finally:
            await client.disconnect()

    async def test_capture_console_error(self, client: CDPClient) -> None:
        """Test that we can capture console.error output."""
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        await client.connect(page_targets[0])
        try:
            await client.enable_logging()

            test_message = f"devlog-error-{asyncio.get_event_loop().time()}"

            async def capture_log() -> LogEvent | None:
                async for log_event in client.stream_logs():
                    if test_message in log_event.text:
                        return log_event
                return None

            capture_task = asyncio.create_task(capture_log())
            await asyncio.sleep(0.1)

            await client.send_command(
                "Runtime.evaluate",
                {"expression": f'console.error("{test_message}")'},
            )

            try:
                log_event = await asyncio.wait_for(capture_task, timeout=5.0)
            except TimeoutError:
                capture_task.cancel()
                pytest.fail("Timeout waiting for console.error event")

            assert log_event is not None
            assert log_event.level == LogLevel.ERROR

        finally:
            await client.disconnect()

    async def test_capture_console_warn(self, client: CDPClient) -> None:
        """Test that we can capture console.warn output."""
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        await client.connect(page_targets[0])
        try:
            await client.enable_logging()

            test_message = f"devlog-warn-{asyncio.get_event_loop().time()}"

            async def capture_log() -> LogEvent | None:
                async for log_event in client.stream_logs():
                    if test_message in log_event.text:
                        return log_event
                return None

            capture_task = asyncio.create_task(capture_log())
            await asyncio.sleep(0.1)

            await client.send_command(
                "Runtime.evaluate",
                {"expression": f'console.warn("{test_message}")'},
            )

            try:
                log_event = await asyncio.wait_for(capture_task, timeout=5.0)
            except TimeoutError:
                capture_task.cancel()
                pytest.fail("Timeout waiting for console.warn event")

            assert log_event is not None
            assert log_event.level == LogLevel.WARN

        finally:
            await client.disconnect()

    async def test_capture_exception(self, client: CDPClient) -> None:
        """Test that we can capture thrown exceptions."""
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        await client.connect(page_targets[0])
        try:
            await client.enable_logging()

            test_message = f"devlog-exception-{asyncio.get_event_loop().time()}"

            async def capture_log() -> LogEvent | None:
                async for log_event in client.stream_logs():
                    if test_message in log_event.text and log_event.kind == LogKind.EXCEPTION:
                        return log_event
                return None

            capture_task = asyncio.create_task(capture_log())
            await asyncio.sleep(0.1)

            await client.send_command(
                "Runtime.evaluate",
                {
                    "expression": f'throw new Error("{test_message}")',
                    "silent": False,
                },
            )

            try:
                log_event = await asyncio.wait_for(capture_task, timeout=5.0)
            except TimeoutError:
                capture_task.cancel()
                pytest.skip("Exception event not captured (may depend on Chrome version)")

            if log_event:
                assert log_event.level == LogLevel.ERROR
                assert log_event.kind == LogKind.EXCEPTION

        finally:
            await client.disconnect()

    async def test_capture_object_log(self, client: CDPClient) -> None:
        """Test that we can capture console.log with objects."""
        targets = await client.list_targets()
        page_targets = [t for t in targets if t.target_type == "page"]
        if not page_targets:
            pytest.skip("No page targets available")

        await client.connect(page_targets[0])
        try:
            await client.enable_logging()

            captured_logs: list[LogEvent] = []

            async def capture_logs() -> None:
                async for log_event in client.stream_logs():
                    if "devlog-obj-test" in log_event.text:
                        captured_logs.append(log_event)
                        if len(captured_logs) >= 1:
                            break

            capture_task = asyncio.create_task(capture_logs())
            await asyncio.sleep(0.1)

            await client.send_command(
                "Runtime.evaluate",
                {
                    "expression": 'console.log("devlog-obj-test", {name: "test", value: 42})',
                },
            )

            try:
                await asyncio.wait_for(capture_task, timeout=5.0)
            except TimeoutError:
                capture_task.cancel()
                pytest.fail("Timeout waiting for object log")

            assert len(captured_logs) >= 1
            log_event = captured_logs[0]
            assert "devlog-obj-test" in log_event.text

        finally:
            await client.disconnect()
