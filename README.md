## Devlog
I created this tool so that when using coding agents like Claude Code or codex I wanted the agents to able to launch frontends, read console logs, run commands in the console,
view network calls, read the DOM etc. This lets coding agents go deeper on issues without having to do lots of copy and pasting. In general, it's very important to close
the feedback loop on these kind of things. This tool is mean to be highly extensible so I have found it helpful to when an agent isn't debugging something effectively but
it gets the answer eventually have it give feedback on where the tool is lacking. That is how some of the tools in this came along like the iframe specific tool

## Quick Start

```bash
uv sync
uv run devlog chrome launch --fast --url http://localhost:3000
uv run devlog stream --url localhost
```

## Examples

### Stream console logs
```bash
devlog stream --url myapp                  # All logs
devlog stream --url myapp --levels error   # Only errors
devlog stream --url myapp --for 30s        # Capture for 30 seconds
```

### Debug 404s and failed network requests
```bash
devlog network --url myapp --errors
```
```
00:05:33.639 RESPONSE   404 http://localhost:9999/api/missing [Fetch]
00:05:34.123 FAILED     net::ERR_CONNECTION_REFUSED http://localhost:8080/api [XHR]
```

### Debug iframes (Sandpack, CodeSandbox, etc.)
```bash
devlog frames --url myapp                           # List all iframes
devlog eval "document.body.innerHTML" --iframe 0    # Run JS in first iframe
devlog eval "location.href" --iframe sandpack       # Match iframe by URL
```

### Run JavaScript in a tab
```bash
devlog eval "document.title" --url myapp
devlog eval "localStorage" --json
devlog eval "document.querySelector('button').click()"
```

### Watch clicks and navigation
```bash
devlog watch --url myapp
```
```
00:01:23.456 CLICK      {"tag":"BUTTON","text":"Submit"}
00:01:23.567 REQUEST    GET http://example.com/api/submit [Fetch]
00:01:23.678 NAVIGATION http://example.com/success
```

### Export logs for analysis
```bash
devlog stream --url myapp --format ndjson > logs.jsonl
devlog network --url myapp --format ndjson > network.jsonl
```

## All Commands

```bash
devlog doctor              # Check Chrome installation and CDP connectivity
devlog targets             # List available Chrome tabs/pages
devlog stream              # Stream console logs in real-time
devlog network             # Stream network requests with status codes
devlog watch               # Watch clicks and navigation
devlog frames              # List all iframes in a page
devlog eval "<js>"         # Execute JavaScript in a tab
devlog chrome launch       # Launch Chrome with debugging enabled
devlog chrome kill         # Kill debug Chrome instances
```

Run `devlog <command> --help` for detailed options
