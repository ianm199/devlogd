# devlogd - Chrome DevTools Console Log Capture

CLI tool for capturing Chrome DevTools console logs programmatically via CDP (Chrome DevTools Protocol).

## Quick Start

```bash
uv sync
uv run devlog chrome launch --fast --url https://example.com
uv run devlog stream --url example
```

## Commands

```bash
devlog doctor              # Check Chrome installation and CDP connectivity
devlog targets             # List available Chrome tabs/pages
devlog stream --url <str>  # Stream console logs from a tab in real-time
devlog watch --url <str>   # Watch for clicks and navigation/redirects
devlog network --url <str> # Stream network requests with status codes
devlog frames --url <str>  # List all frames (iframes) in a page
devlog eval "<js>" --url   # Execute JavaScript in a tab and print result
devlog chrome launch       # Launch Chrome with remote debugging enabled
devlog chrome kill         # Kill debug Chrome instances
```

## Debugging Recipes

### Debug 404 errors and failed requests
```bash
devlog network --url myapp --errors
```
Shows only failed requests (4xx/5xx) with full URLs:
```
00:05:33.639 RESPONSE   404 http://localhost:9999/api/missing-endpoint [Fetch]
00:05:34.123 FAILED     net::ERR_CONNECTION_REFUSED http://localhost:8080/api [XHR]
```

### Debug iframe issues (Sandpack, CodeSandbox, etc.)
```bash
devlog frames --url myapp
devlog eval "document.body.innerHTML" --iframe 0
devlog eval "location.href" --iframe sandpack
```
The `frames` command lists all iframes with indices. Use `--iframe` with eval to run JS inside an iframe by index or URL match.

### Capture console errors with source URLs
```bash
devlog stream --url myapp --levels error
```
Browser errors now show the full URL that caused them:
```
ERROR Failed to load resource: the server responded with a status of 404 ()
      └─ http://localhost:9999/missing-resource.js
```

### Filter API calls only
```bash
devlog network --url myapp --types XHR,Fetch
```

### Debug redirect chains
```bash
devlog watch --url myapp
```
Shows navigation flow with redirects:
```
00:01:23.456 REQUEST    GET http://example.com/old-path [Document]
00:01:23.567 REDIRECT   301 → http://example.com/new-path
00:01:23.678 NAVIGATION http://example.com/new-path
```

### Export logs as JSON for analysis
```bash
devlog stream --url myapp --format ndjson > logs.jsonl
devlog network --url myapp --format ndjson > network.jsonl
```

## Command Options

**devlog chrome launch**
- `--url <url>` - URL to open
- `--port <port>` - CDP port (default: 9222)
- `--fast` / `-f` - Disable extensions for faster startup
- `--headless` - Run headless
- `--incognito` - Run in incognito mode
- `--kill-existing` / `-k` - Kill existing debug Chrome first
- `--gpu` / `--no-gpu` - Enable/disable GPU (disabled by default due to CDP conflicts on Apple Silicon)

**devlog stream**
- `--url <str>` - Filter target by URL substring match
- `--target <id>` - Target by ID
- `--levels <levels>` - Comma-separated: debug,info,warn,error
- `--format <fmt>` - Output format: pretty, json, ndjson, tsv
- `--lines` / `-n` - Exit after N log lines
- `--for <duration>` - Exit after duration (e.g. 30s, 5m, 1h)

**devlog watch**
- `--url <str>` - Filter target by URL substring match
- `--target <id>` - Target by ID
- `--clicks` / `--no-clicks` - Enable/disable click capture (default: on)
- `--network` / `--no-network` - Enable/disable network/navigation capture (default: on)
- `--all` / `-a` - Show all requests, not just Document navigations
- `--format <fmt>` - Output format: pretty, ndjson
- `--for <duration>` - Exit after duration (e.g. 30s, 5m)

**devlog network**
- `--url <str>` - Filter target by URL substring match
- `--target <id>` - Target by ID
- `--errors` / `-e` - Only show failed requests and 4xx/5xx responses
- `--responses` / `-r` - Only show responses (not request starts)
- `--types <types>` - Filter by resource types (e.g. Document,XHR,Fetch,Script,Stylesheet,Image)
- `--format <fmt>` - Output format: pretty, ndjson
- `--for <duration>` - Exit after duration (e.g. 30s, 5m)

**devlog frames**
- `--url <str>` - Filter target by URL substring match
- `--target <id>` - Target by ID
- `--json` - Output as JSON

**devlog eval**
- `<expression>` - JavaScript to evaluate (positional arg)
- `--url <str>` - Filter target by URL substring match
- `--target <id>` - Target by ID
- `--json` - Output result as JSON
- `--iframe` / `-i` - Run in iframe (URL substring or index like '0', '1')

## Architecture

```
src/devlogd/
├── cli.py              # Typer CLI commands
├── daemon.py           # Daemon stub (not yet implemented)
├── core/
│   ├── cdp_client.py   # CDP WebSocket client (target discovery, commands, event streaming)
│   ├── log_event.py    # Normalized LogEvent/WatchEvent models
│   └── exceptions.py   # Custom exceptions
└── utils/
    └── chrome.py       # Chrome detection, launch, kill utilities
```

## Key Concepts

### CDP Connection Flow
1. Chrome must be launched with `--remote-debugging-port=<port>`
2. Targets discovered via `http://127.0.0.1:<port>/json/list`
3. Connect to target via WebSocket (`webSocketDebuggerUrl`)
4. Enable domains: `Runtime.enable`, `Log.enable`, `Network.enable`, `Page.enable`
5. Listen for events: `Runtime.consoleAPICalled`, `Runtime.exceptionThrown`, `Log.entryAdded`, `Network.responseReceived`, etc.

### CDP Domains Used
- **Runtime** - Console logs, exceptions, JavaScript evaluation
- **Log** - Browser-level logs (deprecations, network errors)
- **Network** - Request/response capture with status codes and timing
- **Page** - Navigation events, frame tree for iframe support

### LogEvent Model
All CDP log sources normalized into single schema:
- `ts` - Timestamp (ISO8601)
- `level` - debug/info/warn/error
- `kind` - console/exception/browser_log
- `text` - Rendered message
- `args` - Structured console arguments
- `source` - Source location (url, line, column)
- `stack` - Stack trace for exceptions
- `target` - Target info (id, title, url)

### WatchEvent Model
Network and interaction events:
- `ts` - Timestamp
- `kind` - request/response/redirect/failed/click/navigation/message
- `url` - Request URL
- `status` - HTTP status code (for responses)
- `resource_type` - Document, XHR, Fetch, Script, etc.
- `error_text` - Error description for failed requests
- `frame_id` - Frame ID for iframe attribution

## Chrome Profile

Uses isolated profile at `~/.devlog/chrome-profile` to avoid conflicts with main Chrome.

Chrome launch flags (optimized for startup speed):
- `--remote-debugging-port=<port>`
- `--disable-background-networking`
- `--disable-sync`
- `--disable-translate`
- `--disable-default-apps`
- `--disable-component-update`
- `--disable-client-side-phishing-detection`
- `--disable-gpu` (default, fixes CDP + GPU conflicts on Apple Silicon)
- `--disable-extensions` (with `--fast` flag)

## Testing

```bash
uv run pytest tests/test_log_event.py tests/test_chrome_utils.py -v  # Unit tests
uv run devlog chrome launch --url about:blank  # Start Chrome first
uv run pytest tests/test_cdp_integration.py -v  # Integration tests (requires Chrome)
```

## Known Limitations

1. **Cannot attach to normal Chrome** - Chrome must be launched with `--remote-debugging-port`. You cannot attach to an already-running Chrome that wasn't started with this flag.

2. **Cold Turkey / Security Software** - Blocking software may:
   - Kill Chrome processes with debug flags
   - Block port binding for debug ports
   - Workaround: Use Chrome extension approach (not yet implemented)

3. **Profile isolation** - Cannot use debug profile simultaneously with main Chrome using same profile. The devlog profile is separate.

4. **Daemon not implemented** - `devlog tail --since 10s` requires the daemon for buffering. Currently only live streaming works.

5. **Cross-origin iframes** - The `--iframe` option works for same-origin and srcdoc iframes. Cross-origin iframes from different domains have additional security restrictions.

## Development

```bash
uv run ruff check src          # Linting
uv run mypy                    # Type checking
uv run pytest                  # All tests
```

## Future Work

- `devlogd start` - Daemon with ring buffer for historical queries
- `devlog tail --since 10s` - Query buffered logs
- `devlog export` - Export logs to file
- Chrome extension approach for bypassing security software
- Worker/service worker support
- WebSocket event capture for HMR debugging
- Request/response body capture
