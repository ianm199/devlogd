"""Chrome DevTools Protocol client for devlogd.

Provides async connection to Chrome via CDP for log capture.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import websockets
from websockets import ConnectionClosed
from websockets.asyncio.client import ClientConnection

from devlogd.core.exceptions import (
    CDPConnectionError,
    CDPProtocolError,
    TargetNotFoundError,
)
from devlogd.core.log_event import LogEvent, TargetInfo, WatchEvent


@dataclass
class CDPTarget:
    """Represents a Chrome DevTools Protocol target (tab/page/worker)."""

    id: str
    title: str
    url: str
    target_type: str
    websocket_url: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CDPTarget":
        """Create from CDP /json/list response."""
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            url=data.get("url", ""),
            target_type=data.get("type", "page"),
            websocket_url=data["webSocketDebuggerUrl"],
        )

    def to_target_info(self) -> TargetInfo:
        """Convert to TargetInfo for log events."""
        return TargetInfo(id=self.id, title=self.title, url=self.url)


class CDPClient:
    """Async client for Chrome DevTools Protocol.

    Handles target discovery, WebSocket connection, and CDP messaging.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9222) -> None:
        self.host = host
        self.port = port
        self._base_url = f"http://{host}:{port}"
        self._ws: ClientConnection | None = None
        self._message_id = 0
        self._pending_commands: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._current_target: CDPTarget | None = None

    async def list_targets(self) -> list[CDPTarget]:
        """List all available CDP targets."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self._base_url}/json/list", timeout=5.0)
                response.raise_for_status()
                data = response.json()
                return [CDPTarget.from_json(item) for item in data]
            except httpx.ConnectError as e:
                raise CDPConnectionError(
                    f"Cannot connect to Chrome at {self._base_url}. "
                    "Is Chrome running with --remote-debugging-port?"
                ) from e
            except httpx.HTTPStatusError as e:
                raise CDPConnectionError(f"CDP endpoint returned error: {e}") from e

    async def find_target(
        self,
        *,
        target_id: str | None = None,
        url_filter: str | None = None,
        title_filter: str | None = None,
    ) -> CDPTarget:
        """Find a target matching the given criteria."""
        targets = await self.list_targets()

        if not targets:
            raise TargetNotFoundError("No targets available")

        if target_id:
            for t in targets:
                if t.id == target_id:
                    return t
            raise TargetNotFoundError(f"Target {target_id} not found")

        page_targets = [t for t in targets if t.target_type == "page"]

        if url_filter:
            for t in page_targets:
                if url_filter in t.url:
                    return t
            raise TargetNotFoundError(f"No target matching URL '{url_filter}'")

        if title_filter:
            for t in page_targets:
                if title_filter in t.title:
                    return t
            raise TargetNotFoundError(f"No target matching title '{title_filter}'")

        if page_targets:
            return page_targets[0]

        return targets[0]

    async def connect(self, target: CDPTarget) -> None:
        """Connect to a specific target via WebSocket."""
        try:
            self._ws = await websockets.connect(target.websocket_url)
            self._current_target = target
            self._recv_task = asyncio.create_task(self._receive_loop())
        except Exception as e:
            raise CDPConnectionError(f"Failed to connect to target: {e}") from e

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._current_target = None
        self._pending_commands.clear()

    async def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and wait for response."""
        if not self._ws:
            raise CDPConnectionError("Not connected to any target")

        self._message_id += 1
        msg_id = self._message_id

        message = {"id": msg_id, "method": method}
        if params:
            message["params"] = params

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending_commands[msg_id] = future

        await self._ws.send(json.dumps(message))

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except TimeoutError:
            self._pending_commands.pop(msg_id, None)
            raise CDPConnectionError(f"Timeout waiting for response to {method}") from None

    async def enable_logging(self) -> None:
        """Enable Runtime and Log domains to receive log events."""
        await self.send_command("Runtime.enable")
        await self.send_command("Log.enable")

    async def disable_logging(self) -> None:
        """Disable Runtime and Log domains."""
        await self.send_command("Runtime.disable")
        await self.send_command("Log.disable")

    async def enable_network(self) -> None:
        """Enable Network domain to receive network events."""
        await self.send_command("Network.enable")

    async def enable_page(self) -> None:
        """Enable Page domain to receive navigation events."""
        await self.send_command("Page.enable")

    async def inject_click_listener(self) -> None:
        """Inject a click listener that logs clicks to console with our prefix."""
        script = """
        (function() {
            if (window.__devlog_click_listener__) return;
            window.__devlog_click_listener__ = true;
            document.addEventListener('click', function(e) {
                var t = e.target;
                var info = {
                    tag: t.tagName,
                    id: t.id || null,
                    className: t.className || null,
                    href: t.href || null,
                    text: (t.textContent || '').slice(0, 100).trim()
                };
                console.log('[DEVLOG_CLICK]', JSON.stringify(info));
            }, true);
        })();
        """
        await self.send_command("Runtime.evaluate", {"expression": script})

    async def inject_message_listener(self) -> None:
        """Inject a postMessage listener that logs messages to console with our prefix."""
        script = """
        (function() {
            if (window.__devlog_message_listener__) return;
            window.__devlog_message_listener__ = true;
            window.addEventListener('message', function(e) {
                var data = e.data;
                var dataStr;
                try {
                    dataStr = typeof data === 'string' ? data : JSON.stringify(data);
                } catch (err) {
                    dataStr = String(data);
                }
                console.log('[DEVLOG_MESSAGE]', e.origin, dataStr);
            });
        })();
        """
        await self.send_command("Runtime.evaluate", {"expression": script})

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield CDP events as they arrive."""
        while True:
            try:
                event = await self._event_queue.get()
                yield event
            except asyncio.CancelledError:
                break

    async def stream_logs(self) -> AsyncIterator[LogEvent]:
        """Stream normalized LogEvents from the connected target."""
        target_info = self._current_target.to_target_info() if self._current_target else None

        async for event in self.stream_events():
            method = event.get("method", "")
            params = event.get("params", {})

            log_event: LogEvent | None = None

            if method == "Runtime.consoleAPICalled":
                log_event = LogEvent.from_console_api_called(params, target_info)
            elif method == "Runtime.exceptionThrown":
                log_event = LogEvent.from_exception_thrown(params, target_info)
            elif method == "Log.entryAdded":
                log_event = LogEvent.from_log_entry_added(params, target_info)

            if log_event:
                yield log_event

    async def stream_watch(
        self,
        clicks: bool = True,
        network: bool = True,
        document_only: bool = True,
    ) -> AsyncIterator[WatchEvent]:
        """Stream watch events (clicks, navigation, network requests).

        Args:
            clicks: Include click events (requires inject_click_listener to be called first)
            network: Include network/navigation events
            document_only: Only show Document-type requests (page navigations), not XHR/images/etc
        """
        target_info = self._current_target.to_target_info() if self._current_target else None

        async for event in self.stream_events():
            method = event.get("method", "")
            params = event.get("params", {})

            if method == "Runtime.consoleAPICalled":
                log_event = LogEvent.from_console_api_called(params, target_info)
                if clicks:
                    watch_event = WatchEvent.from_click_console(log_event)
                    if watch_event:
                        yield watch_event
                message_event = WatchEvent.from_message_console(log_event)
                if message_event:
                    yield message_event

            if network and method == "Network.requestWillBeSent":
                resource_type = params.get("type", "")
                if document_only and resource_type != "Document":
                    continue
                yield WatchEvent.from_request_will_be_sent(params, target_info)

            if network and method == "Page.frameNavigated":
                frame = params.get("frame", {})
                if frame.get("parentId"):
                    continue
                yield WatchEvent.from_frame_navigated(params, target_info)

    async def stream_network(
        self,
        include_requests: bool = True,
        include_responses: bool = True,
        include_failures: bool = True,
        resource_types: set[str] | None = None,
        status_filter: set[int] | None = None,
    ) -> AsyncIterator[WatchEvent]:
        """Stream network events with request/response correlation.

        Args:
            include_requests: Include request start events
            include_responses: Include response events (with status codes)
            include_failures: Include failed requests
            resource_types: Filter by resource type (Document, XHR, Fetch, Script, etc)
            status_filter: Only show responses with these status codes (e.g., {404, 500})
        """
        target_info = self._current_target.to_target_info() if self._current_target else None
        pending_requests: dict[str, dict[str, Any]] = {}

        async for event in self.stream_events():
            method = event.get("method", "")
            params = event.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId", "")
                resource_type = params.get("type", "")

                if resource_types and resource_type not in resource_types:
                    continue

                pending_requests[request_id] = {
                    "url": params.get("request", {}).get("url", ""),
                    "method": params.get("request", {}).get("method", "GET"),
                    "type": resource_type,
                    "frame_id": params.get("frameId"),
                }

                if include_requests:
                    yield WatchEvent.from_request_will_be_sent(params, target_info)

            elif method == "Network.responseReceived":
                request_id = params.get("requestId", "")
                response = params.get("response", {})
                status = response.get("status", 0)

                if status_filter and status not in status_filter:
                    continue

                resource_type = params.get("type", "")
                if resource_types and resource_type not in resource_types:
                    continue

                if include_responses:
                    yield WatchEvent.from_response_received(params, target_info)

                pending_requests.pop(request_id, None)

            elif method == "Network.loadingFailed":
                request_id = params.get("requestId", "")
                request_info = pending_requests.pop(request_id, {})

                resource_type = params.get("type", "")
                if resource_types and resource_type not in resource_types:
                    continue

                if include_failures:
                    watch_event = WatchEvent.from_loading_failed(params, target_info)
                    watch_event.url = request_info.get("url", "")
                    watch_event.method = request_info.get("method")
                    watch_event.frame_id = request_info.get("frame_id")
                    yield watch_event

    async def get_frame_tree(self) -> dict[str, Any]:
        """Get the frame tree for the current page (main frame + iframes)."""
        result = await self.send_command("Page.getFrameTree")
        frame_tree: dict[str, Any] = result.get("frameTree", {})
        return frame_tree

    async def get_execution_contexts(self) -> list[dict[str, Any]]:
        """Get all execution contexts (main frame + iframes + workers)."""
        contexts: list[dict[str, Any]] = []

        async def collect_contexts() -> None:
            async for event in self.stream_events():
                if event.get("method") == "Runtime.executionContextCreated":
                    contexts.append(event.get("params", {}).get("context", {}))

        collect_task = asyncio.create_task(collect_contexts())
        await asyncio.sleep(0.1)
        collect_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collect_task

        return contexts

    async def evaluate_in_context(
        self,
        expression: str,
        context_id: int,
    ) -> dict[str, Any]:
        """Evaluate JavaScript in a specific execution context (e.g., iframe)."""
        return await self.send_command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "contextId": context_id,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )

    async def find_iframe_context(
        self,
        url_filter: str | None = None,
        index: int = 0,
    ) -> int | None:
        """Find an iframe's execution context by URL filter or index.

        Args:
            url_filter: Substring to match against iframe URL
            index: If no url_filter, return the Nth iframe (0-indexed)

        Returns:
            The contextId for the iframe, or None if not found
        """
        result = await self.send_command("Page.getFrameTree")
        frame_tree = result.get("frameTree", {})

        iframe_frames: list[dict[str, Any]] = []
        self._collect_child_frames(frame_tree, iframe_frames)

        if url_filter:
            for frame in iframe_frames:
                if url_filter in frame.get("url", ""):
                    return frame.get("id")
        elif index < len(iframe_frames):
            return iframe_frames[index].get("id")

        return None

    def _collect_child_frames(
        self,
        frame_tree: dict[str, Any],
        result: list[dict[str, Any]],
    ) -> None:
        """Recursively collect child frames from a frame tree."""
        for child in frame_tree.get("childFrames", []):
            child_frame = child.get("frame", {})
            result.append(child_frame)
            self._collect_child_frames(child, result)

    async def _receive_loop(self) -> None:
        """Background task to receive WebSocket messages."""
        if not self._ws:
            return

        try:
            async for raw_message in self._ws:
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")

                message = json.loads(raw_message)

                if "id" in message:
                    msg_id = message["id"]
                    future = self._pending_commands.pop(msg_id, None)
                    if future and not future.done():
                        if "error" in message:
                            err = message["error"]
                            future.set_exception(
                                CDPProtocolError(err.get("code", -1), err.get("message", ""))
                            )
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    await self._event_queue.put(message)

        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


async def check_cdp_connection(host: str = "127.0.0.1", port: int = 9222) -> bool:
    """Check if CDP is reachable at the given host:port."""
    client = CDPClient(host=host, port=port)
    try:
        await client.list_targets()
        return True
    except CDPConnectionError:
        return False
