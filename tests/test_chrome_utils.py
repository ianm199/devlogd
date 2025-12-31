"""Tests for Chrome utilities."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from devlogd.core.exceptions import ChromeNotFoundError
from devlogd.utils.chrome import (
    build_chrome_args,
    find_chrome,
    get_default_profile_dir,
)


class TestFindChrome:
    """Tests for Chrome detection."""

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
    def test_find_chrome_macos(self) -> None:
        try:
            path = find_chrome()
            assert "Chrome" in path or "Chromium" in path
            assert Path(path).exists()
        except ChromeNotFoundError:
            pytest.skip("Chrome not installed")

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
    def test_find_chrome_linux(self) -> None:
        try:
            path = find_chrome()
            assert path is not None
        except ChromeNotFoundError:
            pytest.skip("Chrome not installed")

    def test_find_chrome_not_found(self) -> None:
        with (
            patch("devlogd.utils.chrome.MACOS_CHROME_PATHS", ["/nonexistent/chrome"]),
            patch("devlogd.utils.chrome.LINUX_CHROME_PATHS", []),
            patch("devlogd.utils.chrome.WINDOWS_CHROME_PATHS", []),
            patch("sys.platform", "darwin"),
            pytest.raises(ChromeNotFoundError),
        ):
            find_chrome()


class TestBuildChromeArgs:
    """Tests for Chrome argument building."""

    def test_default_args(self) -> None:
        args = build_chrome_args()

        assert "--remote-debugging-port=9222" in args
        assert "--no-first-run" in args
        assert "--no-default-browser-check" in args
        assert any("--user-data-dir=" in arg for arg in args)

    def test_custom_port(self) -> None:
        args = build_chrome_args(port=9333)

        assert "--remote-debugging-port=9333" in args

    def test_custom_profile(self) -> None:
        profile = Path("/custom/profile")
        args = build_chrome_args(profile_dir=profile)

        assert f"--user-data-dir={profile}" in args

    def test_headless(self) -> None:
        args = build_chrome_args(headless=True)

        assert "--headless=new" in args

    def test_incognito(self) -> None:
        args = build_chrome_args(incognito=True)

        assert "--incognito" in args

    def test_url(self) -> None:
        args = build_chrome_args(url="http://localhost:3000")

        assert "http://localhost:3000" in args

    def test_combined_args(self) -> None:
        args = build_chrome_args(
            port=9333,
            headless=True,
            incognito=True,
            url="http://test.local",
        )

        assert "--remote-debugging-port=9333" in args
        assert "--headless=new" in args
        assert "--incognito" in args
        assert "http://test.local" in args


class TestGetDefaultProfileDir:
    """Tests for default profile directory."""

    def test_default_profile_dir(self) -> None:
        path = get_default_profile_dir()

        assert isinstance(path, Path)
        assert ".devlog" in str(path)
        assert "chrome-profile" in str(path)
