# -*- coding: utf-8 -*-
"""Scrapling Spider bridge for Kuaishou."""

from media_platform.scrapling_bridge import (
    ScraplingBridgeSpec,
    dispatch_by_crawler_type,
    run_scrapling_bridge,
)

from .core import KuaishouCrawler
from .login import KuaishouLogin


async def _run_flow(crawler):
    await dispatch_by_crawler_type(
        crawler,
        detail="get_specified_videos",
        creator="get_creators_and_videos",
    )


async def run_scrapling_kuaishou_crawler() -> None:
    await run_scrapling_bridge(
        ScraplingBridgeSpec(
            platform="ks",
            spider_name="kuaishou_scrapling",
            crawler_factory=KuaishouCrawler,
            login_factory=KuaishouLogin,
            client_attr="ks_client",
            create_client_method="create_ks_client",
            start_url=lambda crawler: f"{crawler.index_url}?isHome=1",
            run_flow=_run_flow,
            allowed_domains={"kuaishou.com", "www.kuaishou.com"},
        )
    )
