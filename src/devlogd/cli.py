"""CLI entry point for devlog."""

import asyncio
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from devlogd.core.cdp_client import CDPClient, check_cdp_connection
from devlogd.core.exceptions import CDPConnectionError, ChromeNotFoundError, TargetNotFoundError
from devlogd.core.log_event import LogLevel, WatchEventKind
from devlogd.utils.chrome import (
    find_chrome,
    get_chrome_version,
    kill_devlog_chrome,
    launch_chrome,
)

console = Console()
app = typer.Typer(
    name="devlog",
    help="""Capture Chrome DevTools console logs via CDP.

Typical workflow:
    devlog chrome launch --url http://localhost:3000
    devlog stream --url localhost

Console & Errors:
    devlog stream --url myapp                  # Stream console logs
    devlog stream --url myapp --levels error   # Only errors

Network Debugging:
    devlog network --url myapp                 # All network requests
    devlog network --url myapp --errors        # Only 404s and failures

Iframe Debugging:
    devlog frames --url myapp                  # List all iframes
    devlog eval "location.href" --iframe 0    # Run JS in first iframe

JavaScript Evaluation:
    devlog eval "document.title" --url myapp
    devlog eval "localStorage" --json
    """,
    no_args_is_help=True,
)

chrome_app = typer.Typer(help="Launch and manage Chrome with CDP debugging enabled.")
app.add_typer(chrome_app, name="chrome")


@app.command()
def doctor(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port to check")] = 9222,
) -> None:
    """Check environment and CDP connectivity."""
    console.print("[bold]devlog doctor[/bold]\n")

    try:
        chrome_path = find_chrome()
        version = get_chrome_version(chrome_path)
        console.print(f"[green]✓[/green] Chrome found: {chrome_path}")
        if version:
            console.print(f"  Version: {version}")
    except ChromeNotFoundError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(code=1) from None

    console.print()

    cdp_ok = asyncio.run(check_cdp_connection(port=port))
    if cdp_ok:
        console.print(f"[green]✓[/green] CDP available on port {port}")
    else:
        console.print(f"[yellow]![/yellow] CDP not available on port {port}")
        console.print("\n[dim]To enable CDP, launch Chrome with:[/dim]")
        console.print(f'  devlog chrome launch --port {port} --url "http://localhost:3000"')


@app.command()
def targets(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    url_filter: Annotated[str | None, typer.Option("--url", help="Filter by URL substring")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """List available CDP targets (tabs/pages)."""

    async def _list() -> None:
        client = CDPClient(port=port)
        try:
            all_targets = await client.list_targets()
        except CDPConnectionError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        if url_filter:
            all_targets = [t for t in all_targets if url_filter in t.url]

        if json_output:
            import json

            data = [
                {
                    "id": t.id,
                    "type": t.target_type,
                    "title": t.title,
                    "url": t.url,
                }
                for t in all_targets
            ]
            console.print_json(json.dumps(data))
            return

        if not all_targets:
            console.print("[yellow]No targets found[/yellow]")
            return

        table = Table(title="CDP Targets")
        table.add_column("ID", style="dim")
        table.add_column("Type")
        table.add_column("Title", max_width=40)
        table.add_column("URL", max_width=60)

        for t in all_targets:
            table.add_row(t.id[:12], t.target_type, t.title[:40], t.url[:60])

        console.print(table)

    asyncio.run(_list())


@app.command()
def stream(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    url_filter: Annotated[
        str | None, typer.Option("--url", help="Filter target by URL substring match")
    ] = None,
    target_id: Annotated[str | None, typer.Option("--target", help="Target ID")] = None,
    levels: Annotated[
        str | None, typer.Option("--levels", help="Comma-separated levels: debug,info,warn,error")
    ] = None,
    format_type: Annotated[
        str, typer.Option("--format", "-f", help="Output format: pretty, json, ndjson, tsv")
    ] = "pretty",
    lines: Annotated[
        int | None, typer.Option("--lines", "-n", help="Exit after N log lines")
    ] = None,
    duration: Annotated[
        str | None, typer.Option("--for", help="Exit after duration (e.g. 30s, 5m)")
    ] = None,
) -> None:
    """Stream console logs from a Chrome tab in real-time.

    Examples:
        devlog stream --url localhost:3000
        devlog stream --url myapp --lines 100
        devlog stream --for 30s --format ndjson
    """
    level_set: set[LogLevel] | None = None
    if levels:
        level_set = {LogLevel(lvl.strip().lower()) for lvl in levels.split(",")}

    timeout_seconds: float | None = None
    if duration:
        timeout_seconds = _parse_duration(duration)

    async def _stream() -> None:
        client = CDPClient(port=port)

        try:
            target = await client.find_target(target_id=target_id, url_filter=url_filter)
        except TargetNotFoundError:
            console.print("[red]Error:[/red] No matching targets found.")
            console.print("\n[dim]Hints:[/dim]")
            console.print("  - Check available targets: [bold]devlog targets[/bold]")
            console.print("  - Launch Chrome with a URL: [bold]devlog chrome launch --url <url>[/bold]")
            if url_filter:
                console.print(f"  - The --url filter uses substring matching (searched for: '{url_filter}')")
            raise typer.Exit(code=1) from None
        except CDPConnectionError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("\n[dim]Hint: Launch Chrome with CDP enabled:[/dim]")
            console.print(f"  devlog chrome launch --port {port} --url <url>")
            raise typer.Exit(code=1) from None

        console.print(f"[dim]Connecting to: {target.title} ({target.url})[/dim]")

        try:
            await client.connect(target)
            await client.enable_logging()

            stop_msg = "Ctrl+C to stop"
            if lines:
                stop_msg = f"capturing {lines} lines"
            elif timeout_seconds:
                stop_msg = f"capturing for {duration}"
            console.print(f"[dim]Streaming logs ({stop_msg})...[/dim]\n")

            line_count = 0
            start_time = asyncio.get_event_loop().time()

            async for log_event in client.stream_logs():
                if timeout_seconds:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= timeout_seconds:
                        break

                if level_set and log_event.level not in level_set:
                    continue

                if format_type == "ndjson":
                    print(log_event.to_ndjson())
                elif format_type == "json":
                    console.print_json(log_event.model_dump_json())
                elif format_type == "tsv":
                    print(log_event.to_tsv())
                else:
                    level_colors = {
                        LogLevel.DEBUG: "dim",
                        LogLevel.INFO: "white",
                        LogLevel.WARN: "yellow",
                        LogLevel.ERROR: "red",
                    }
                    color = level_colors.get(log_event.level, "white")
                    console.print(f"[{color}]{log_event.to_pretty()}[/{color}]")

                line_count += 1
                if lines and line_count >= lines:
                    break

        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
        finally:
            await client.disconnect()

    asyncio.run(_stream())


def _parse_duration(duration_str: str) -> float:
    """Parse duration string like '30s', '5m', '1h' to seconds."""
    duration_str = duration_str.strip().lower()
    if duration_str.endswith("s"):
        return float(duration_str[:-1])
    elif duration_str.endswith("m"):
        return float(duration_str[:-1]) * 60
    elif duration_str.endswith("h"):
        return float(duration_str[:-1]) * 3600
    else:
        return float(duration_str)


@app.command()
def watch(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    url_filter: Annotated[
        str | None, typer.Option("--url", help="Filter target by URL substring match")
    ] = None,
    target_id: Annotated[str | None, typer.Option("--target", help="Target ID")] = None,
    clicks: Annotated[
        bool, typer.Option("--clicks/--no-clicks", help="Capture click events")
    ] = True,
    network: Annotated[
        bool, typer.Option("--network/--no-network", help="Capture network/navigation events")
    ] = True,
    all_requests: Annotated[
        bool, typer.Option("--all", "-a", help="Show all requests, not just Document navigations")
    ] = False,
    format_type: Annotated[
        str, typer.Option("--format", "-f", help="Output format: pretty, ndjson")
    ] = "pretty",
    duration: Annotated[
        str | None, typer.Option("--for", help="Exit after duration (e.g. 30s, 5m)")
    ] = None,
) -> None:
    """Watch for clicks and navigation/redirects in a Chrome tab.

    Useful for debugging redirect flows and understanding what happens when
    you click on elements.

    Examples:
        devlog watch --url myapp                    # Watch clicks and navigations
        devlog watch --url myapp --no-clicks        # Only network/navigation
        devlog watch --url myapp --all              # Include XHR, images, etc.
    """
    timeout_seconds: float | None = None
    if duration:
        timeout_seconds = _parse_duration(duration)

    async def _watch() -> None:
        client = CDPClient(port=port)

        try:
            target = await client.find_target(target_id=target_id, url_filter=url_filter)
        except TargetNotFoundError:
            console.print("[red]Error:[/red] No matching targets found.")
            console.print("\n[dim]Hints:[/dim]")
            console.print("  - Check available targets: [bold]devlog targets[/bold]")
            console.print("  - Launch Chrome with a URL: [bold]devlog chrome launch --url <url>[/bold]")
            raise typer.Exit(code=1) from None
        except CDPConnectionError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("\n[dim]Hint: Launch Chrome with CDP enabled:[/dim]")
            console.print(f"  devlog chrome launch --port {port} --url <url>")
            raise typer.Exit(code=1) from None

        console.print(f"[dim]Connecting to: {target.title} ({target.url})[/dim]")

        try:
            await client.connect(target)
            await client.enable_logging()

            if network:
                await client.enable_network()
                await client.enable_page()

            if clicks:
                await client.inject_click_listener()
            await client.inject_message_listener()

            features = []
            if clicks:
                features.append("clicks")
            if network:
                features.append("navigation")
            features.append("messages")
            console.print(f"[dim]Watching for {', '.join(features)}... (Ctrl+C to stop)[/dim]\n")

            start_time = asyncio.get_event_loop().time()

            async for watch_event in client.stream_watch(
                clicks=clicks,
                network=network,
                document_only=not all_requests,
            ):
                if timeout_seconds:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= timeout_seconds:
                        break

                if format_type == "ndjson":
                    print(watch_event.to_ndjson())
                else:
                    kind_colors = {
                        WatchEventKind.CLICK: "cyan",
                        WatchEventKind.NAVIGATION: "green",
                        WatchEventKind.REQUEST: "white",
                        WatchEventKind.REDIRECT: "yellow",
                        WatchEventKind.MESSAGE: "magenta",
                    }
                    color = kind_colors.get(watch_event.kind, "white")
                    console.print(f"[{color}]{watch_event.to_pretty()}[/{color}]")

        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
        finally:
            await client.disconnect()

    asyncio.run(_watch())


@app.command()
def network(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    url_filter: Annotated[
        str | None, typer.Option("--url", help="Filter target by URL substring match")
    ] = None,
    target_id: Annotated[str | None, typer.Option("--target", help="Target ID")] = None,
    errors_only: Annotated[
        bool, typer.Option("--errors", "-e", help="Only show failed requests and 4xx/5xx responses")
    ] = False,
    responses_only: Annotated[
        bool, typer.Option("--responses", "-r", help="Only show responses (not request starts)")
    ] = False,
    types: Annotated[
        str | None, typer.Option("--types", "-t", help="Filter by resource types: Document,XHR,Fetch,Script,etc")
    ] = None,
    format_type: Annotated[
        str, typer.Option("--format", "-f", help="Output format: pretty, ndjson")
    ] = "pretty",
    duration: Annotated[
        str | None, typer.Option("--for", help="Exit after duration (e.g. 30s, 5m)")
    ] = None,
) -> None:
    """Stream network requests with full URLs and status codes.

    Shows all network activity including requests from iframes. This is useful
    for debugging 404 errors, CORS issues, and understanding what resources
    are being loaded.

    Examples:
        devlog network --url myapp                    # All network activity
        devlog network --url myapp --errors           # Only 4xx/5xx and failures
        devlog network --url myapp --types XHR,Fetch  # Only API calls
    """
    timeout_seconds: float | None = None
    if duration:
        timeout_seconds = _parse_duration(duration)

    resource_type_set: set[str] | None = None
    if types:
        resource_type_set = {t.strip() for t in types.split(",")}

    status_filter: set[int] | None = None
    if errors_only:
        status_filter = set(range(400, 600))

    async def _network() -> None:
        client = CDPClient(port=port)

        try:
            target = await client.find_target(target_id=target_id, url_filter=url_filter)
        except TargetNotFoundError:
            console.print("[red]Error:[/red] No matching targets found.")
            console.print("\n[dim]Hints:[/dim]")
            console.print("  - Check available targets: [bold]devlog targets[/bold]")
            console.print("  - Launch Chrome with a URL: [bold]devlog chrome launch --url <url>[/bold]")
            raise typer.Exit(code=1) from None
        except CDPConnectionError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("\n[dim]Hint: Launch Chrome with CDP enabled:[/dim]")
            console.print(f"  devlog chrome launch --port {port} --url <url>")
            raise typer.Exit(code=1) from None

        console.print(f"[dim]Connecting to: {target.title} ({target.url})[/dim]")

        try:
            await client.connect(target)
            await client.enable_network()

            stop_msg = "Ctrl+C to stop"
            if timeout_seconds:
                stop_msg = f"capturing for {duration}"
            console.print(f"[dim]Streaming network ({stop_msg})...[/dim]\n")

            start_time = asyncio.get_event_loop().time()

            async for event in client.stream_network(
                include_requests=not responses_only and not errors_only,
                include_responses=True,
                include_failures=True,
                resource_types=resource_type_set,
                status_filter=status_filter if not errors_only else None,
            ):
                if timeout_seconds:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= timeout_seconds:
                        break

                if errors_only:
                    is_error = (
                        event.kind == WatchEventKind.FAILED
                        or (event.status and event.status >= 400)
                    )
                    if not is_error:
                        continue

                if format_type == "ndjson":
                    print(event.to_ndjson())
                else:
                    kind_colors = {
                        WatchEventKind.REQUEST: "dim",
                        WatchEventKind.RESPONSE: "green",
                        WatchEventKind.REDIRECT: "yellow",
                        WatchEventKind.FAILED: "red",
                    }
                    if event.status and event.status >= 400:
                        color = "red"
                    else:
                        color = kind_colors.get(event.kind, "white")
                    console.print(f"[{color}]{event.to_pretty()}[/{color}]")

        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
        finally:
            await client.disconnect()

    asyncio.run(_network())


@app.command("eval")
def eval_js(
    expression: Annotated[str, typer.Argument(help="JavaScript expression to evaluate")],
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    url_filter: Annotated[
        str | None, typer.Option("--url", help="Filter target by URL substring match")
    ] = None,
    target_id: Annotated[str | None, typer.Option("--target", help="Target ID")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output result as JSON")] = False,
    iframe: Annotated[
        str | None, typer.Option("--iframe", "-i", help="Run in iframe (URL substring or index like '0', '1')")
    ] = None,
) -> None:
    """Execute JavaScript in a Chrome tab and print the result.

    Examples:
        devlog eval "document.title" --url myapp
        devlog eval "window.location.href" --json
        devlog eval "document.body.innerHTML" --url myapp --iframe sandpack
        devlog eval "location.href" --iframe 0   # First iframe
    """

    async def _eval() -> None:
        client = CDPClient(port=port)

        try:
            target = await client.find_target(target_id=target_id, url_filter=url_filter)
        except TargetNotFoundError:
            console.print("[red]Error:[/red] No matching targets found.")
            console.print("\n[dim]Hints:[/dim]")
            console.print("  - Check available targets: [bold]devlog targets[/bold]")
            console.print("  - Launch Chrome with a URL: [bold]devlog chrome launch --url <url>[/bold]")
            raise typer.Exit(code=1) from None
        except CDPConnectionError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("\n[dim]Hint: Launch Chrome with CDP enabled:[/dim]")
            console.print(f"  devlog chrome launch --port {port} --url <url>")
            raise typer.Exit(code=1) from None

        try:
            await client.connect(target)
            await client.send_command("Runtime.enable")
            await client.enable_page()

            context_id: int | None = None

            if iframe is not None:
                if iframe.isdigit():
                    frame_id = await client.find_iframe_context(index=int(iframe))
                else:
                    frame_id = await client.find_iframe_context(url_filter=iframe)

                if frame_id is None:
                    console.print(f"[red]Error:[/red] No iframe found matching '{iframe}'")
                    console.print("\n[dim]Hint: List frames with:[/dim]")
                    console.print('  devlog eval "Array.from(document.querySelectorAll(\'iframe\')).map(f => f.src)"')
                    raise typer.Exit(code=1) from None

                world_result = await client.send_command(
                    "Page.createIsolatedWorld",
                    {"frameId": frame_id, "grantUniveralAccess": True},
                )
                context_id = world_result.get("executionContextId")

            if context_id:
                result = await client.evaluate_in_context(expression, context_id)
            else:
                result = await client.send_command(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )

            if "exceptionDetails" in result:
                exc = result["exceptionDetails"]
                err_text = exc.get("exception", {}).get("description", exc.get("text", "Error"))
                console.print(f"[red]Exception:[/red] {err_text}")
                raise typer.Exit(code=1) from None

            value = result.get("result", {})

            if json_output:
                import json
                console.print_json(json.dumps(value))
            else:
                val_type = value.get("type", "")
                if val_type == "undefined":
                    console.print("[dim]undefined[/dim]")
                elif "value" in value:
                    console.print(value["value"])
                elif "description" in value:
                    console.print(value["description"])
                else:
                    console.print(value)

        finally:
            await client.disconnect()

    asyncio.run(_eval())


@app.command()
def frames(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    url_filter: Annotated[
        str | None, typer.Option("--url", help="Filter target by URL substring match")
    ] = None,
    target_id: Annotated[str | None, typer.Option("--target", help="Target ID")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """List all frames (iframes) in a Chrome tab.

    Shows the frame tree including main frame and all nested iframes.
    Useful for finding iframe indices or URLs to use with 'devlog eval --iframe'.

    Examples:
        devlog frames --url myapp
        devlog frames --url myapp --json
    """

    async def _frames() -> None:
        client = CDPClient(port=port)

        try:
            target = await client.find_target(target_id=target_id, url_filter=url_filter)
        except TargetNotFoundError:
            console.print("[red]Error:[/red] No matching targets found.")
            console.print("\n[dim]Hints:[/dim]")
            console.print("  - Check available targets: [bold]devlog targets[/bold]")
            console.print("  - Launch Chrome with a URL: [bold]devlog chrome launch --url <url>[/bold]")
            raise typer.Exit(code=1) from None
        except CDPConnectionError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("\n[dim]Hint: Launch Chrome with CDP enabled:[/dim]")
            console.print(f"  devlog chrome launch --port {port} --url <url>")
            raise typer.Exit(code=1) from None

        try:
            await client.connect(target)
            await client.enable_page()

            frame_tree = await client.get_frame_tree()

            if json_output:
                import json
                console.print_json(json.dumps(frame_tree))
                return

            def print_frame(frame_node: dict[str, Any], depth: int = 0, index: list[int] | None = None) -> None:
                if index is None:
                    index = []
                frame = frame_node.get("frame", {})
                indent = "  " * depth
                prefix = "├─" if depth > 0 else ""

                url = frame.get("url", "about:blank")
                name = frame.get("name", "")
                frame_id = frame.get("id", "")[:8]

                if depth == 0:
                    console.print("[bold]Main Frame[/bold]")
                    console.print(f"  URL: {url}")
                    console.print(f"  ID:  {frame_id}")
                else:
                    idx_str = f"[{len(index) - 1}]"
                    name_str = f' name="{name}"' if name else ""
                    console.print(f"{indent}{prefix} [cyan]iframe {idx_str}[/cyan]{name_str}")
                    console.print(f"{indent}   URL: {url}")
                    console.print(f"{indent}   ID:  {frame_id}")

                child_frames = frame_node.get("childFrames", [])
                for i, child in enumerate(child_frames):
                    print_frame(child, depth + 1, index + [i])

            print_frame(frame_tree)

            child_frames: list[dict[str, Any]] = []
            def collect_children(node: dict[str, Any]) -> None:
                for child in node.get("childFrames", []):
                    child_frames.append(child.get("frame", {}))
                    collect_children(child)
            collect_children(frame_tree)

            if child_frames:
                console.print("\n[dim]Use with eval:[/dim]")
                console.print('  devlog eval "location.href" --iframe 0')
            else:
                console.print("\n[dim]No iframes found in this page[/dim]")

        finally:
            await client.disconnect()

    asyncio.run(_frames())


@chrome_app.command("launch")
def chrome_launch(
    url: Annotated[str | None, typer.Option("--url", help="URL to open")] = None,
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
    headless: Annotated[bool, typer.Option("--headless", help="Run headless")] = False,
    incognito: Annotated[bool, typer.Option("--incognito", help="Run in incognito")] = False,
    fast: Annotated[
        bool, typer.Option("--fast", "-f", help="Disable extensions for faster startup")
    ] = False,
    gpu: Annotated[
        bool, typer.Option("--gpu/--no-gpu", help="Enable GPU acceleration (disabled by default due to CDP conflicts on Apple Silicon)")
    ] = False,
    kill_existing: Annotated[
        bool, typer.Option("--kill-existing", "-k", help="Kill existing debug Chrome first")
    ] = False,
) -> None:
    """Launch Chrome with remote debugging enabled."""

    async def _launch() -> None:
        try:
            console.print(f"[dim]Launching Chrome with CDP on port {port}...[/dim]")
            process = await launch_chrome(
                port=port,
                url=url,
                headless=headless,
                incognito=incognito,
                fast=fast,
                disable_gpu=not gpu,
                kill_existing=kill_existing,
            )
            console.print(f"[green]✓[/green] Chrome launched (PID: {process.pid})")
            console.print(f"  CDP available at: http://127.0.0.1:{port}")
            console.print("\n[dim]Use 'devlog targets' to see available tabs[/dim]")
        except ChromeNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

    asyncio.run(_launch())


@chrome_app.command("kill")
def chrome_kill(
    port: Annotated[int, typer.Option("--port", "-p", help="CDP port")] = 9222,
) -> None:
    """Kill any Chrome instances running with debug port."""
    killed = kill_devlog_chrome(port)
    if killed > 0:
        console.print(f"[green]✓[/green] Killed {killed} Chrome process(es) on port {port}")
    else:
        console.print(f"[yellow]No Chrome processes found using port {port}[/yellow]")


if __name__ == "__main__":
    app()
