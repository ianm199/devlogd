Below is a first-pass CLI spec focused on “give my coding agent access to browser console logs” with streaming + query last N seconds / last N logs. I’m going to start with the constraints (what’s fundamentally annoying here), then the proposed shape of the tool, and finally what we should test to make sure it works in real debugging sessions.

Base constraints (what we need to work around)
1) Browsers don’t expose DevTools logs as a normal OS stream

The Console you see in Chrome DevTools isn’t “stdout”; it’s internal events.

So the only practical way to read it programmatically is via the Chrome DevTools Protocol (CDP).

2) CDP access requires remote debugging to be enabled

You generally cannot attach to an already-running “normal” Chrome unless it was launched with remote debugging enabled.

Enabling remote debugging has security implications: CDP can do much more than read logs (navigate, run JS, read DOM, etc.).

So we must bind to localhost only, prefer safe modes, and be explicit about what we capture.

3) “Give me the last 10 seconds of console logs” is not inherently available

CDP primarily provides events going forward (from the moment you enable logging).

There is no reliable “fetch historical console buffer” API that matches what DevTools shows.

So to support “last 10 seconds / last 10 logs”, we need a local buffer:

either a daemon that’s always attached, or

a per-session buffer that starts when you run stream.

4) Logs come from multiple sources and need normalization

You’ll want at least:

console.log/info/warn/error calls (from page JS)

uncaught exceptions (stack traces)

sometimes “browser-level” log entries (network errors, deprecations, etc.)

In CDP terms, you typically combine events from:

Runtime.consoleAPICalled

Runtime.exceptionThrown

Log.entryAdded

These have different shapes; we need to normalize them into one consistent log event type.

5) Tabs/targets are messy in real life

Multiple tabs, popups, iframes, service workers, extensions, pre-rendered pages.

You need a good story for selecting the right target:

by URL substring, title regex, “active tab”, “most recently focused”, etc.

Proposed solution: devlog CLI + devlogd local daemon
Why a daemon?

Because “last 10 seconds” requires someone to have been listening and buffering already. The daemon is the piece that stays attached and keeps a ring buffer per tab/target.

devlogd (daemon): connects to Chrome via CDP, subscribes to log events, stores them in a ring buffer (memory) and optionally SQLite for persistence.

devlog (CLI): user-facing commands to list targets, attach, stream, and query buffered logs.

Security posture (default)

Only connect to CDP on 127.0.0.1.

Prefer launching a dedicated debugging Chrome profile (separate user-data-dir).

Optionally support --remote-debugging-pipe (safer than a port) when we launch Chrome ourselves.

Make it obvious when we’re in a “dangerous” mode (e.g., attaching to a CDP port that might be exposed).

CLI surface area (MVP)
Command: devlog doctor

Checks environment + gives exact next steps.

Detect Chrome path (macOS: /Applications/Google Chrome.app/...)

Check if CDP is reachable (port or pipe)

Explain how to launch Chrome in debug mode if needed

Example:

devlog doctor

Command: devlog chrome launch

Launch a new Chrome instance with remote debugging enabled (dedicated profile).
Options:

--url <url>

--headless (optional)

--profile <path> (default: ~/.devlog/chrome-profile)

--port <port> (default: 9222) or --pipe (preferred if supported)

--incognito (optional)

Example:

devlog chrome launch --url http://localhost:3000 --profile ~/.devlog/profile --port 9222

Command: devlog targets

Lists available CDP targets (tabs/pages).
Options:

--json

--filter-url <substr|regex>

--filter-title <substr|regex>

Example:

devlog targets
devlog targets --filter-url localhost:3000 --json

Command: devlogd start

Starts daemon and (optionally) auto-attaches to matching targets.
Options:

--attach-all (default false)

--auto-attach-url <regex> (e.g. localhost:3000)

--buffer-size 5000 (events per target)

--buffer-seconds 600 (time-based eviction, optional)

--persist sqlite://~/.devlog/logs.db (optional)

--include-workers (optional, off in MVP)

Example:

devlogd start --auto-attach-url "localhost:3000" --buffer-seconds 900

Command: devlog stream

Streams logs live from a selected target (and also writes into daemon buffer if daemon is running).
Options:

--target <id> (or --url <substr>, --title <substr>, --active)

--levels info,warn,error (default all)

--format pretty|json|ndjson

--timestamps on|off

--grep <regex>

--include-stack (for exceptions)

Examples:

devlog stream --url localhost:3000
devlog stream --target page_3 --levels error,warn --format ndjson

Command: devlog tail

Queries buffered logs (daemon required).
This is where “last 10 seconds / last 10 logs” lives.

Options (mutually composable):

--last 10 (last N log events)

--since 10s (duration)

--since-time 2025-12-17T10:03:00-05:00

--levels error,warn

--grep <regex>

--target ... / --url ... / --active

--format pretty|json|ndjson

Examples:

devlog tail --url localhost:3000 --since 10s
devlog tail --url localhost:3000 --last 50 --levels error
devlog tail --active --since 30s --grep "iframeResizer|postMessage" --format ndjson

Command: devlog export

Dump a time window or last N logs to a file for attaching to tickets/agents.
Options:

same selectors as tail

--out ./console.ndjson

Example:

devlog export --url localhost:3000 --since 5m --out ./debug_console.ndjson

Log event model (normalized)

Everything becomes a single schema so agents can consume it.

Core fields:

ts (ISO8601 with tz)

level (debug|info|warn|error)

kind (console|exception|browser_log)

text (rendered message)

args (structured console args if available, truncated/safe)

url, line, column (when available)

stack (for exceptions, optional)

target (id, title, url)

context (executionContextId etc., optional)

Output formats:

pretty: compact human text

json: array

ndjson: one JSON object per line (best for piping into other tools)

Implementation notes (how it works under the hood)
CDP connection

If using a port:

discover targets via http://127.0.0.1:<port>/json/list

connect to webSocketDebuggerUrl

If using pipe:

spawn Chrome + speak CDP over stdio (more secure; slightly more work)

Subscriptions

On attach:

Runtime.enable

Log.enable

listen for:

Runtime.consoleAPICalled

Runtime.exceptionThrown

Log.entryAdded

Buffering strategy

Per target:

ring buffer by count (e.g. 5000 entries)

optional time-based eviction (e.g. keep 10 minutes)

tail --since 10s is computed by filtering ts >= now-10s

IPC between CLI and daemon

Simple local-only channel:

Unix domain socket at ~/.devlog/devlogd.sock

JSON-RPC-ish:

listTargets

tail(query)

stream(query) (optional; or CLI streams directly from CDP)

What we need to test (so this actually helps debugging)
Functional correctness

Can we reliably capture console logs?

console.log, warn, error, objects, circular structures (ensure safe truncation)

Exceptions show stacks

uncaught exception

unhandled promise rejection (verify it appears; may surface differently)

“Last 10 seconds” works

generate logs continuously, query --since 10s, ensure boundaries are correct

Real-world target selection

Multiple tabs with same URL: does --active or “most recently focused” do what you expect?

SPA navigation changes URL/title: does the target metadata update?

Navigation / reload behavior

Logs across reloads:

do we keep buffering under same target?

do we re-enable Runtime/Log after navigation if needed?

Performance and safety

High-volume logs (1000+/sec):

ring buffer doesn’t explode memory

CLI tail is fast

Security defaults:

refuses non-local CDP endpoints unless --allow-remote is explicitly set

Packaging & ergonomics

Works on macOS with Chrome Stable

Clear error messages when Chrome wasn’t launched with debugging enabled

MVP cut (what I’d build first)

If you want the smallest useful slice:

devlog chrome launch

devlog targets

devlog stream --url ...

devlogd start

devlog tail --since 10s / --last 10

NDJSON output

Then we iterate into:

workers/service workers

network logs

“auto attach to any localhost:* tab”

richer filtering + correlation IDs

If you tell me your preferred implementation language (Python vs Node) and whether you’re okay launching a dedicated Chrome profile for debugging, I’ll tighten this into an even more concrete v0.1 spec (commands, flags, exact JSON schema, and a test harness plan).