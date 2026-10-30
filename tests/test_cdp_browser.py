# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
from tools.cdp_browser import (
    CDPBrowserManager,
    clear_stale_profile_lock,
    debug_port_from_command,
    profile_owner_pid,
)


@pytest.mark.asyncio
async def test_existing_browser_connects_directly_to_devtools_browser(monkeypatch):
    monkeypatch.setattr(config, "CDP_CONNECT_EXISTING", True)
    monkeypatch.setattr(config, "BROWSER_LAUNCH_TIMEOUT", 60)

    manager = CDPBrowserManager()
    manager.debug_port = 9222
    manager._get_browser_websocket_url = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("existing browser mode must not call /json/version")
    )

    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.contexts = []

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

    await manager._connect_via_cdp(playwright)

    playwright.chromium.connect_over_cdp.assert_awaited_once_with(
        "ws://localhost:9222/devtools/browser",
        timeout=60000,
    )


@pytest.mark.asyncio
async def test_existing_browser_falls_back_to_discovered_websocket_url(monkeypatch):
    monkeypatch.setattr(config, "CDP_CONNECT_EXISTING", True)
    monkeypatch.setattr(config, "BROWSER_LAUNCH_TIMEOUT", 60)

    manager = CDPBrowserManager()
    manager.debug_port = 9222
    manager._get_browser_websocket_url = AsyncMock(  # type: ignore[method-assign]
        return_value="ws://localhost:9222/devtools/browser/generated-id"
    )

    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.contexts = []

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = AsyncMock(
        side_effect=[RuntimeError("direct websocket failed"), browser]
    )

    await manager._connect_via_cdp(playwright)

    manager._get_browser_websocket_url.assert_awaited_once_with(9222)
    assert playwright.chromium.connect_over_cdp.await_args_list[0].args == (
        "ws://localhost:9222/devtools/browser",
    )
    assert playwright.chromium.connect_over_cdp.await_args_list[0].kwargs == {
        "timeout": 60000,
    }
    assert playwright.chromium.connect_over_cdp.await_args_list[1].args == (
        "ws://localhost:9222/devtools/browser/generated-id",
    )
    assert playwright.chromium.connect_over_cdp.await_args_list[1].kwargs == {
        "timeout": 60000,
    }


@pytest.mark.asyncio
async def test_launched_browser_uses_discovered_websocket_url(monkeypatch):
    monkeypatch.setattr(config, "CDP_CONNECT_EXISTING", False)

    manager = CDPBrowserManager()
    manager.debug_port = 9223
    manager._get_browser_websocket_url = AsyncMock(  # type: ignore[method-assign]
        return_value="ws://localhost:9223/devtools/browser/generated-id"
    )

    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.contexts = []

    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

    await manager._connect_via_cdp(playwright)

    manager._get_browser_websocket_url.assert_awaited_once_with(9223)
    playwright.chromium.connect_over_cdp.assert_awaited_once_with(
        "ws://localhost:9223/devtools/browser/generated-id"
    )


def test_profile_owner_pid_reads_chrome_lock(tmp_path):
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("host-4321")
    assert profile_owner_pid(str(tmp_path)) == 4321


def test_debug_port_from_command():
    assert debug_port_from_command("/usr/bin/google-chrome --remote-debugging-port=9223 --other") == 9223
    assert debug_port_from_command("chrome") is None


def test_clear_stale_profile_lock_keeps_live_owner(tmp_path, monkeypatch):
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("host-99")
    monkeypatch.setattr("tools.cdp_browser.pid_alive", lambda pid: pid == 99)
    clear_stale_profile_lock(str(tmp_path))
    assert lock.is_symlink()


def test_clear_stale_profile_lock_removes_dead_owner(tmp_path, monkeypatch):
    lock = tmp_path / "SingletonLock"
    lock.symlink_to("host-99")
    monkeypatch.setattr("tools.cdp_browser.pid_alive", lambda pid: False)
    clear_stale_profile_lock(str(tmp_path))
    assert not lock.exists()


@pytest.mark.asyncio
async def test_watch_mode_reuses_open_profile_instead_of_launching(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_MODE", "1")
    monkeypatch.setattr(config, "CDP_CONNECT_EXISTING", False)
    monkeypatch.setattr(config, "PLATFORM", "xhs")
    monkeypatch.setattr("tools.cdp_browser.watch_user_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("tools.cdp_browser.profile_owner_pid", lambda path: 77)
    monkeypatch.setattr("tools.cdp_browser.pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "tools.cdp_browser.process_command",
        lambda pid: f"chrome --user-data-dir={tmp_path} --remote-debugging-port=9222",
    )

    manager = CDPBrowserManager()
    manager._test_cdp_connection = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager._connect_via_cdp = AsyncMock()  # type: ignore[method-assign]
    manager._launch_browser = AsyncMock(side_effect=AssertionError("must not launch a second browser"))  # type: ignore[method-assign]
    context = MagicMock()
    manager._create_browser_context = AsyncMock(return_value=context)  # type: ignore[method-assign]

    result = await manager.launch_and_connect(MagicMock(), headless=False)

    assert result is context
    assert manager.debug_port == 9222
    manager._launch_browser.assert_not_called()
    manager._connect_via_cdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_watch_cleanup_keeps_the_browser_process(monkeypatch):
    monkeypatch.setenv("WATCH_MODE", "1")
    manager = CDPBrowserManager()
    manager.launcher.cleanup = MagicMock(side_effect=AssertionError("must not kill the login browser"))
    await manager.cleanup()
    manager.launcher.cleanup.assert_not_called()
