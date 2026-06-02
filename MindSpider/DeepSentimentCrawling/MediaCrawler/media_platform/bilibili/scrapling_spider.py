# -*- coding: utf-8 -*-
"""Scrapling Spider bridge for Bilibili."""

import config
from tools import utils

from media_platform.scrapling_bridge import ScraplingBridgeSpec, run_scrapling_bridge

from .core import BilibiliCrawler
from .help import parse_creator_info_from_url
from .login import BilibiliLogin


async def _run_flow(crawler):
    if config.CRAWLER_TYPE == "search":
        await crawler.search()
    elif config.CRAWLER_TYPE == "detail":
        await crawler.get_specified_videos(config.BILI_SPECIFIED_ID_LIST)
    elif config.CRAWLER_TYPE == "creator":
        if config.CREATOR_MODE:
            for creator_url in config.BILI_CREATOR_ID_LIST:
                try:
                    creator_info = parse_creator_info_from_url(creator_url)
                    await crawler.get_creator_videos(int(creator_info.creator_id))
                except ValueError as exc:
                    utils.logger.error(
                        f"[BilibiliScraplingSpider] Failed to parse creator URL: {exc}"
                    )
        else:
            await crawler.get_all_creator_details(config.BILI_CREATOR_ID_LIST)


async def run_scrapling_bilibili_crawler() -> None:
    await run_scrapling_bridge(
        ScraplingBridgeSpec(
            platform="bili",
            spider_name="bilibili_scrapling",
            crawler_factory=BilibiliCrawler,
            login_factory=BilibiliLogin,
            client_attr="bili_client",
            create_client_method="create_bilibili_client",
            start_url=lambda crawler: crawler.index_url,
            run_flow=_run_flow,
            allowed_domains={"bilibili.com", "www.bilibili.com", "api.bilibili.com"},
        )
    )
