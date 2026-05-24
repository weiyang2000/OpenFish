# -*- coding: utf-8 -*-
"""Tests for CloakBrowser launch helpers."""

from __future__ import annotations

import sys
import types
import asyncio
from pathlib import Path

import pytest

import config
from tools import cloak_browser


class FakeBrowser:
    version = "CloakBrowser test"

    def is_connected(self) -> bool:
        return True


class FakeContext:
    browser = FakeBrowser()

    async def close(self) -> None:
        return None


def _install_fake_cloakbrowser(monkeypatch: pytest.MonkeyPatch, calls: dict) -> None:
    module = types.ModuleType("cloakbrowser")

    async def launch_context_async(**kwargs):
        calls["context"] = kwargs
        return FakeContext()

    async def launch_persistent_context_async(user_data_dir, **kwargs):
        calls["persistent"] = {
            "user_data_dir": user_data_dir,
            "kwargs": kwargs,
        }
        return FakeContext()

    module.launch_context_async = launch_context_async
    module.launch_persistent_context_async = launch_persistent_context_async
    monkeypatch.setitem(sys.modules, "cloakbrowser", module)


@pytest.fixture(autouse=True)
def cloak_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "SAVE_LOGIN_STATE", True, raising=False)
    monkeypatch.setattr(config, "USER_DATA_DIR", "%s_user_data_dir", raising=False)
    monkeypatch.setattr(config, "PLATFORM", "xhs", raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_STEALTH_ARGS", True, raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_GEOIP", False, raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_HUMANIZE", False, raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_HUMAN_PRESET", "default", raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_EXTRA_ARGS", [], raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_EXTENSION_PATHS", [], raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_BINARY_PATH", "", raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_LOCALE", "", raising=False)
    monkeypatch.setattr(config, "CLOAKBROWSER_TIMEZONE", "", raising=False)


def test_launch_cloak_browser_context_uses_persistent_profile(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {}
    _install_fake_cloakbrowser(monkeypatch, calls)

    context = asyncio.run(
        cloak_browser.launch_cloak_browser_context(
            browser_proxy={"server": "http://proxy:8080"},
            user_agent="Test UA",
            headless=False,
        )
    )

    assert isinstance(context, FakeContext)
    assert "context" not in calls
    persistent_call = calls["persistent"]
    assert persistent_call["user_data_dir"].endswith(
        "browser_data/cloak_xhs_user_data_dir"
    )
    assert persistent_call["kwargs"] == {
        "headless": False,
        "proxy": {"server": "http://proxy:8080"},
        "viewport": {"width": 1920, "height": 1080},
        "accept_downloads": True,
        "stealth_args": True,
        "geoip": False,
        "humanize": False,
        "human_preset": "default",
        "user_agent": "Test UA",
    }


def test_launch_cloak_browser_context_can_use_ephemeral_context(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict = {}
    _install_fake_cloakbrowser(monkeypatch, calls)
    monkeypatch.setattr(config, "SAVE_LOGIN_STATE", False, raising=False)

    asyncio.run(cloak_browser.launch_cloak_browser_context(None, None, True))

    assert "persistent" not in calls
    assert calls["context"]["headless"] is True
