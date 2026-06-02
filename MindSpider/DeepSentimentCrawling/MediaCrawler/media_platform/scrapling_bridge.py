# -*- coding: utf-8 -*-
"""Shared Scrapling Spider bridge for MediaCrawler platform pilots."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import config
from scrapling.fetchers import AsyncStealthySession
from scrapling.spiders import Request, Response, Spider
from tools import utils
from tools.cloak_browser import _profile_dir
from var import crawler_type_var


CrawlerHook = Callable[[Any], Awaitable[None]]
CrawlerPageHook = Callable[[Any, Any], Awaitable[None]]
ClientHook = Callable[[Any, Any], Awaitable[bool]]
ClientUpdateHook = Callable[[Any, Any], Awaitable[None]]
ClientArgsHook = Callable[[Any], tuple]


@dataclass(frozen=True)
class ScraplingBridgeSpec:
    platform: str
    spider_name: str
    crawler_factory: Callable[[], Any]
    login_factory: Callable[..., Any]
    client_attr: str
    create_client_method: str
    start_url: Callable[[Any], str]
    run_flow: CrawlerHook
    allowed_domains: set[str]
    bootstrap: Optional[CrawlerPageHook] = None
    pong: Optional[ClientHook] = None
    after_login: Optional[ClientUpdateHook] = None
    create_client_args: Optional[ClientArgsHook] = None


class ScraplingBridgeError(RuntimeError):
    pass


async def _default_bootstrap(crawler: Any, page: Any) -> None:
    del crawler, page


async def _default_pong(crawler: Any, client: Any) -> bool:
    del crawler
    return await client.pong()


async def _default_after_login(crawler: Any, client: Any) -> None:
    await client.update_cookies(browser_context=crawler.browser_context)


def _default_create_client_args(crawler: Any) -> tuple:
    del crawler
    return (None,)


def _scrapling_user_data_dir(platform: str) -> str:
    original_platform = getattr(config, "PLATFORM", "")
    config.PLATFORM = platform
    try:
        return _profile_dir()
    finally:
        config.PLATFORM = original_platform


def should_use_scrapling_engine(platform: str | None = None) -> bool:
    del platform
    engine = os.getenv("CRAWLER_ENGINE", "scrapling").strip().lower()
    if engine in {"legacy", "mediacrawler", "cloak", "cloakbrowser"}:
        return False
    if engine not in {"scrapling", "spider"}:
        raise ValueError("CRAWLER_ENGINE only supports scrapling or legacy")
    return True


async def run_scrapling_bridge(spec: ScraplingBridgeSpec) -> None:
    """Run an existing MediaCrawler platform flow inside a Scrapling Spider."""

    class PlatformScraplingSpider(Spider):
        name = spec.spider_name
        allowed_domains = spec.allowed_domains
        robots_txt_obey = False
        logging_level = 20

        def __init__(self) -> None:
            self.crawler = spec.crawler_factory()
            self.error: Optional[BaseException] = None
            super().__init__()
            self.concurrent_requests = 1
            self.concurrent_requests_per_domain = 1

        def configure_sessions(self, manager: Any) -> None:
            manager.add(
                "browser",
                AsyncStealthySession(
                    headless=config.HEADLESS,
                    useragent=getattr(self.crawler, "user_agent", None),
                    user_data_dir=_scrapling_user_data_dir(spec.platform),
                    network_idle=True,
                    timeout=60_000,
                    block_webrtc=True,
                    hide_canvas=True,
                    retries=1,
                    retry_delay=1,
                ),
                default=True,
            )

        async def start_requests(self):
            if config.ENABLE_IP_PROXY:
                raise ScraplingBridgeError(
                    f"{spec.platform} Scrapling spider does not yet support "
                    "MediaCrawler IP proxy pool. Disable ENABLE_IP_PROXY or "
                    "explicitly use CRAWLER_ENGINE=legacy."
                )

            yield Request(
                spec.start_url(self.crawler),
                sid="browser",
                callback=self.parse,
                dont_filter=True,
                wait=1_000,
                network_idle=True,
                google_search=False,
                page_action=self._run_in_page,
            )

        async def parse(self, response: Response):
            del response
            if self.error:
                raise self.error
            yield {"type": f"{spec.platform}_scrapling_bridge", "status": "finished"}

        async def _run_in_page(self, page: Any) -> None:
            try:
                await self._run_platform_flow(page)
            except BaseException as exc:
                self.error = exc
                utils.logger.error(f"[{spec.spider_name}] Platform flow failed: {exc}")

        async def _run_platform_flow(self, page: Any) -> None:
            crawler = self.crawler
            crawler.browser_context = page.context
            crawler.context_page = page
            crawler.ip_proxy_pool = None

            bootstrap = spec.bootstrap or _default_bootstrap
            await bootstrap(crawler, page)

            args_hook = spec.create_client_args or _default_create_client_args
            client = await getattr(crawler, spec.create_client_method)(*args_hook(crawler))
            setattr(crawler, spec.client_attr, client)

            pong = spec.pong or _default_pong
            if not await pong(crawler, client):
                login_obj = spec.login_factory(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=crawler.browser_context,
                    context_page=crawler.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                after_login = spec.after_login or _default_after_login
                await after_login(crawler, client)

            crawler_type_var.set(config.CRAWLER_TYPE)
            await spec.run_flow(crawler)
            utils.logger.info(f"[{spec.spider_name}] {spec.platform} Scrapling bridge finished ...")

    spider = PlatformScraplingSpider()
    async for _ in spider.stream():
        pass


async def dispatch_by_crawler_type(
    crawler: Any,
    *,
    search: str = "search",
    detail: str,
    creator: str,
) -> None:
    if config.CRAWLER_TYPE == "search":
        await getattr(crawler, search)()
    elif config.CRAWLER_TYPE == "detail":
        await getattr(crawler, detail)()
    elif config.CRAWLER_TYPE == "creator":
        await getattr(crawler, creator)()


async def sleep_seconds(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)
