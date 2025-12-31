"""Chrome detection and launch utilities."""

import asyncio
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from devlogd.core.exceptions import ChromeNotFoundError

MACOS_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

LINUX_CHROME_PATHS = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]

WINDOWS_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


@dataclass
class ChromeInfo:
    """Information about a Chrome installation."""

    path: str
    version: str | None


def find_chrome() -> str:
    """Find the Chrome executable path."""
    if sys.platform == "darwin":
        for path in MACOS_CHROME_PATHS:
            if Path(path).exists():
                return path
    elif sys.platform == "linux":
        for name in LINUX_CHROME_PATHS:
            path = shutil.which(name)
            if path:
                return path
    elif sys.platform == "win32":
        for path in WINDOWS_CHROME_PATHS:
            if Path(path).exists():
                return path

    raise ChromeNotFoundError(
        "Chrome not found. Please install Google Chrome or specify the path manually."
    )


def get_chrome_version(chrome_path: str) -> str | None:
    """Get the version of Chrome at the given path."""
    try:
        if sys.platform == "darwin" or sys.platform == "linux":
            result = subprocess.run(
                [chrome_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
    except Exception:
        pass
    return None


def get_default_profile_dir() -> Path:
    """Get the default devlog Chrome profile directory."""
    return Path.home() / ".devlog" / "chrome-profile"


def build_chrome_args(
    *,
    port: int = 9222,
    profile_dir: Path | None = None,
    url: str | None = None,
    headless: bool = False,
    incognito: bool = False,
    fast: bool = False,
    disable_gpu: bool = True,
) -> list[str]:
    """Build Chrome launch arguments for debugging."""
    args = [
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-default-apps",
        "--disable-component-update",
        "--disable-client-side-phishing-detection",
    ]

    profile = profile_dir if profile_dir else get_default_profile_dir()
    args.append(f"--user-data-dir={profile}")

    if headless:
        args.append("--headless=new")

    if incognito:
        args.append("--incognito")

    if fast:
        args.append("--disable-extensions")

    if disable_gpu:
        args.append("--disable-gpu")

    if url:
        args.append(url)

    return args


def find_devlog_chrome_processes(port: int = 9222) -> list[int]:
    """Find PIDs of Chrome processes running with devlog profile or specified port."""
    pids: list[int] = []
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"remote-debugging-port={port}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line:
                    pids.append(int(line))
    except Exception:
        pass
    return pids


def kill_devlog_chrome(port: int = 9222) -> int:
    """Kill any Chrome processes using the specified debug port. Returns count killed."""
    pids = find_devlog_chrome_processes(port)
    killed = 0
    for pid in pids:
        try:
            subprocess.run(["kill", "-9", str(pid)], check=False)
            killed += 1
        except Exception:
            pass
    return killed


def remove_profile_lock(profile_dir: Path | None = None) -> bool:
    """Remove the Chrome profile lock file if it exists."""
    profile = profile_dir if profile_dir else get_default_profile_dir()
    lock_file = profile / "SingletonLock"
    socket_file = profile / "SingletonSocket"
    cookie_file = profile / "SingletonCookie"

    removed = False
    for f in [lock_file, socket_file, cookie_file]:
        if f.exists() or f.is_symlink():
            try:
                f.unlink()
                removed = True
            except Exception:
                pass
    return removed


async def launch_chrome(
    *,
    chrome_path: str | None = None,
    port: int = 9222,
    profile_dir: Path | None = None,
    url: str | None = None,
    headless: bool = False,
    incognito: bool = False,
    fast: bool = False,
    disable_gpu: bool = True,
    wait_for_ready: bool = True,
    kill_existing: bool = False,
) -> subprocess.Popen[bytes]:
    """Launch Chrome with remote debugging enabled.

    Returns the subprocess.Popen object for the Chrome process.
    """
    from devlogd.core.cdp_client import check_cdp_connection

    if await check_cdp_connection(port=port):
        if kill_existing:
            kill_devlog_chrome(port)
            await asyncio.sleep(0.5)
        else:
            raise ChromeNotFoundError(
                f"CDP already running on port {port}. "
                f"Use --kill-existing to replace it, or use a different --port."
            )

    path = chrome_path if chrome_path else find_chrome()
    args = build_chrome_args(
        port=port,
        profile_dir=profile_dir,
        url=url,
        headless=headless,
        incognito=incognito,
        fast=fast,
        disable_gpu=disable_gpu,
    )

    profile = profile_dir if profile_dir else get_default_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    remove_profile_lock(profile)

    process = subprocess.Popen(
        [path, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    if wait_for_ready:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if process.poll() is not None:
                raise ChromeNotFoundError(
                    f"Chrome exited immediately (code {process.returncode}). "
                    "Try removing ~/.devlog/chrome-profile and retrying."
                )
            if await check_cdp_connection(port=port):
                break
        else:
            process.terminate()
            raise ChromeNotFoundError(
                f"Chrome started but CDP not available on port {port} after 10 seconds. "
                "Try: devlog chrome launch --kill-existing"
            )

    return process
