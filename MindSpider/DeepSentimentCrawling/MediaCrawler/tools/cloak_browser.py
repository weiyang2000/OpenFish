# -*- coding: utf-8 -*-
"""CloakBrowser launch helpers."""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

import config

CLOAK_LOGGER = logging.getLogger("cloakbrowser")
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


def _logger() -> logging.Logger:
    try:
        from tools import utils
    except ModuleNotFoundError:
        return CLOAK_LOGGER

    return utils.logger


def _optional_string(name: str) -> Optional[str]:
    value = getattr(config, name, "")
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def _list_config(name: str) -> Optional[list[str]]:
    value = getattr(config, name, [])
    if not value:
        return None

    if isinstance(value, str):
        items: list[str] = []
        for chunk in value.split(","):
            items.extend(part for part in chunk.split() if part)
        return items or None

    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]

    raise TypeError(f"{name} must be a string, list, or tuple")


def _profile_dir() -> str:
    """Use a separate profile from stock Chrome to avoid profile lock/version issues."""
    browser_data_dir = os.getenv("BETTAFISH_CRAWLER_BROWSER_DATA_DIR")
    if browser_data_dir:
        return os.path.join(
            browser_data_dir,
            f"cloak_{config.USER_DATA_DIR % config.PLATFORM}",
        )

    return os.path.join(
        os.getcwd(),
        "browser_data",
        f"cloak_{config.USER_DATA_DIR % config.PLATFORM}",
    )


def _launch_options(
    browser_proxy: Optional[Dict[str, Any]],
    user_agent: Optional[str],
    headless: bool,
) -> dict[str, Any]:
    binary_path = _optional_string("CLOAKBROWSER_BINARY_PATH")
    if binary_path:
        os.environ["CLOAKBROWSER_BINARY_PATH"] = binary_path

    options: dict[str, Any] = {
        "headless": headless,
        "proxy": browser_proxy,
        "viewport": DEFAULT_VIEWPORT,
        "accept_downloads": True,
        "stealth_args": bool(getattr(config, "CLOAKBROWSER_STEALTH_ARGS", True)),
        "geoip": bool(getattr(config, "CLOAKBROWSER_GEOIP", False)),
        "humanize": bool(getattr(config, "CLOAKBROWSER_HUMANIZE", False)),
        "human_preset": getattr(config, "CLOAKBROWSER_HUMAN_PRESET", "default"),
    }

    if user_agent:
        options["user_agent"] = user_agent

    for option_name, config_name in (
        ("locale", "CLOAKBROWSER_LOCALE"),
        ("timezone", "CLOAKBROWSER_TIMEZONE"),
    ):
        value = _optional_string(config_name)
        if value:
            options[option_name] = value

    extra_args = _list_config("CLOAKBROWSER_EXTRA_ARGS")
    if extra_args:
        options["args"] = extra_args

    extension_paths = _list_config("CLOAKBROWSER_EXTENSION_PATHS")
    if extension_paths:
        options["extension_paths"] = extension_paths

    return options


async def launch_cloak_browser_context(
    browser_proxy: Optional[Dict[str, Any]],
    user_agent: Optional[str],
    headless: bool,
) -> Any:
    """Launch CloakBrowser and return its browser context."""
    try:
        from cloakbrowser import launch_context_async, launch_persistent_context_async
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "CloakBrowser is enabled but not installed. Run `uv sync` in "
            "MindSpider/DeepSentimentCrawling/MediaCrawler or install `cloakbrowser`."
        ) from exc

    options = _launch_options(browser_proxy, user_agent, headless)
    if config.SAVE_LOGIN_STATE:
        user_data_dir = _profile_dir()
        os.makedirs(user_data_dir, exist_ok=True)
        _logger().info(
            f"[CloakBrowser] Launching persistent stealth browser: {user_data_dir}"
        )
        return await launch_persistent_context_async(user_data_dir, **options)

    _logger().info("[CloakBrowser] Launching ephemeral stealth browser context")
    return await launch_context_async(**options)
