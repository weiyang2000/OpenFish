# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/tieba/core.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


import asyncio
from asyncio import Task
from typing import Any, Dict, List, Optional, Tuple

import config
from base.base_crawler import AbstractCrawler
from media_platform.scrapling_bridge import should_use_scrapling_engine
from model.m_baidu_tieba import TiebaCreator, TiebaNote
from proxy.proxy_ip_pool import IpInfoModel, ProxyIpPool, create_ip_pool
from store import tieba as tieba_store
from tools import date_filter, utils
from tools.cloak_browser import launch_cloak_browser_context
from var import crawler_type_var, source_keyword_var

from .client import BaiduTieBaClient
from .field import SearchNoteType, SearchSortType
from .help import TieBaExtractor
from .login import BaiduTieBaLogin


class TieBaCrawler(AbstractCrawler):
    context_page: Any
    tieba_client: BaiduTieBaClient
    browser_context: Any

    def __init__(self) -> None:
        self.index_url = "https://tieba.baidu.com"
        self.user_agent = utils.get_user_agent()
        self._page_extractor = TieBaExtractor()

    async def start(self) -> None:
        if not should_use_scrapling_engine("tieba"):
            await self._start_legacy()
            return

        await self._start_scrapling_spider()

    async def _start_scrapling_spider(self) -> None:
        from .scrapling_spider import run_scrapling_tieba_crawler

        await run_scrapling_tieba_crawler()

    async def _start_legacy(self) -> None:
        """
        Start the crawler
        Returns:

        """
        browser_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            utils.logger.info(
                "[BaiduTieBaCrawler.start] Begin create ip proxy pool ..."
            )
            ip_proxy_pool = await create_ip_pool(
                config.IP_PROXY_POOL_COUNT, enable_validate_ip=True
            )
            ip_proxy_info: IpInfoModel = await ip_proxy_pool.get_proxy()
            browser_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)
            utils.logger.info(
                f"[BaiduTieBaCrawler.start] Init default ip proxy, value: {httpx_proxy_format}"
            )

        utils.logger.info("[BaiduTieBaCrawler] Launching browser in CloakBrowser")
        self.browser_context = await self.launch_browser(
            browser_proxy_format,
            self.user_agent,
            headless=config.HEADLESS,
        )

        # Inject anti-detection scripts - for Baidu's special detection
        await self._inject_anti_detection_scripts()

        self.context_page = await self.browser_context.new_page()

        # First visit Baidu homepage, then click Tieba link to avoid triggering security verification
        await self._navigate_to_tieba_via_baidu()

        # Create a client to interact with the baidutieba website.
        self.tieba_client = await self.create_tieba_client(
            httpx_proxy_format,
            ip_proxy_pool if config.ENABLE_IP_PROXY else None
        )

        # Check login status and perform login if necessary
        if not await self.tieba_client.pong(browser_context=self.browser_context):
            login_obj = BaiduTieBaLogin(
                login_type=config.LOGIN_TYPE,
                login_phone="",  # your phone number
                browser_context=self.browser_context,
                context_page=self.context_page,
                cookie_str=config.COOKIES,
            )
            await login_obj.begin()
            await self.tieba_client.update_cookies(browser_context=self.browser_context)

        crawler_type_var.set(config.CRAWLER_TYPE)
        if config.CRAWLER_TYPE == "search":
            # Search for notes and retrieve their comment information.
            await self.search()
            await self.get_specified_tieba_notes()
        elif config.CRAWLER_TYPE == "detail":
            # Get the information and comments of the specified post
            await self.get_specified_notes()
        elif config.CRAWLER_TYPE == "creator":
            # Get creator's information and their notes and comments
            await self.get_creators_and_notes()
        else:
            pass

        utils.logger.info("[BaiduTieBaCrawler.start] Tieba Crawler finished ...")

    async def search(self) -> None:
        """
        Search for notes and retrieve their comment information.
        Returns:

        """
        utils.logger.info(
            "[BaiduTieBaCrawler.search] Begin search baidu tieba keywords"
        )
        tieba_limit_count = 10  # tieba limit page fixed value
        max_notes = max(0, int(config.CRAWLER_MAX_NOTES_COUNT))
        start_page = config.START_PAGE
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(
                f"[BaiduTieBaCrawler.search] Current search keyword: {keyword}"
            )
            page = 1
            accepted_count = 0
            while accepted_count < max_notes:
                if page < start_page:
                    utils.logger.info(f"[BaiduTieBaCrawler.search] Skip page {page}")
                    page += 1
                    continue
                try:
                    utils.logger.info(
                        f"[BaiduTieBaCrawler.search] search tieba keyword: {keyword}, page: {page}"
                    )
                    notes_list: List[TiebaNote] = (
                        await self.tieba_client.get_notes_by_keyword(
                            keyword=keyword,
                            page=page,
                            page_size=tieba_limit_count,
                            sort=SearchSortType.TIME_DESC,
                            note_type=SearchNoteType.FIXED_THREAD,
                        )
                    )
                    if not notes_list:
                        utils.logger.info(
                            f"[BaiduTieBaCrawler.search] Search note list is empty"
                        )
                        break
                    utils.logger.info(
                        f"[BaiduTieBaCrawler.search] Note list len: {len(notes_list)}"
                    )
                    if not date_filter.has_new_time_records("tieba", "content", notes_list):
                        utils.logger.info("[BaiduTieBaCrawler.search] No new-time notes before filtering, stop crawling")
                        break
                    accepted_notes = await self.get_specified_notes(
                        note_id_list=[note_detail.note_id for note_detail in notes_list],
                        max_notes=max_notes - accepted_count,
                    )
                    accepted_count += len(accepted_notes)

                    # Sleep after page navigation
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                    utils.logger.info(f"[TieBaCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page}")

                    page += 1
                except Exception as ex:
                    utils.logger.error(
                        f"[BaiduTieBaCrawler.search] Search keywords error, current page: {page}, current keyword: {keyword}, err: {ex}"
                    )
                    break

    async def get_specified_tieba_notes(self):
        """
        Get the information and comments of the specified post by tieba name
        Returns:

        """
        tieba_limit_count = 50
        max_notes = max(0, int(config.CRAWLER_MAX_NOTES_COUNT))
        for tieba_name in config.TIEBA_NAME_LIST:
            utils.logger.info(
                f"[BaiduTieBaCrawler.get_specified_tieba_notes] Begin get tieba name: {tieba_name}"
            )
            page_number = 0
            accepted_count = 0
            while accepted_count < max_notes:
                note_list: List[TiebaNote] = (
                    await self.tieba_client.get_notes_by_tieba_name(
                        tieba_name=tieba_name, page_num=page_number
                    )
                )
                if not note_list:
                    utils.logger.info(
                        f"[BaiduTieBaCrawler.get_specified_tieba_notes] Get note list is empty"
                    )
                    break

                utils.logger.info(
                    f"[BaiduTieBaCrawler.get_specified_tieba_notes] tieba name: {tieba_name} note list len: {len(note_list)}"
                )
                if not date_filter.has_new_time_records("tieba", "content", note_list):
                    utils.logger.info("[BaiduTieBaCrawler.get_specified_tieba_notes] No new-time notes before filtering, stop crawling")
                    break
                accepted_notes = await self.get_specified_notes(
                    [note.note_id for note in note_list],
                    max_notes=max_notes - accepted_count,
                )
                accepted_count += len(accepted_notes)

                # Sleep after processing notes
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[TieBaCrawler.get_specified_tieba_notes] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after processing notes from page {page_number}")

                page_number += tieba_limit_count

    async def get_specified_notes(
        self,
        note_id_list: List[str] = config.TIEBA_SPECIFIED_ID_LIST,
        max_notes: int | None = None,
    ):
        """
        Get the information and comments of the specified post
        Args:
            note_id_list:

        Returns:

        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [
            self.get_note_detail_async_task(note_id=note_id, semaphore=semaphore)
            for note_id in note_id_list
        ]
        note_details = await asyncio.gather(*task_list)
        note_details_model: List[TiebaNote] = []
        for note_detail in note_details:
            if note_detail is not None:
                if max_notes is not None and len(note_details_model) >= max_notes:
                    break
                if not await tieba_store.update_tieba_note(note_detail):
                    continue
                note_details_model.append(note_detail)
        await self.batch_get_note_comments(note_details_model)
        return note_details_model

    async def get_note_detail_async_task(
        self, note_id: str, semaphore: asyncio.Semaphore
    ) -> Optional[TiebaNote]:
        """
        Get note detail
        Args:
            note_id: baidu tieba note id
            semaphore: asyncio semaphore

        Returns:

        """
        async with semaphore:
            try:
                utils.logger.info(
                    f"[BaiduTieBaCrawler.get_note_detail] Begin get note detail, note_id: {note_id}"
                )
                note_detail: TiebaNote = await self.tieba_client.get_note_by_id(note_id)

                # Sleep after fetching note details
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[TieBaCrawler.get_note_detail_async_task] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching note details {note_id}")

                if not note_detail:
                    utils.logger.error(
                        f"[BaiduTieBaCrawler.get_note_detail] Get note detail error, note_id: {note_id}"
                    )
                    return None
                return note_detail
            except Exception as ex:
                utils.logger.error(
                    f"[BaiduTieBaCrawler.get_note_detail] Get note detail error: {ex}"
                )
                return None
            except KeyError as ex:
                utils.logger.error(
                    f"[BaiduTieBaCrawler.get_note_detail] have not fund note detail note_id:{note_id}, err: {ex}"
                )
                return None

    async def batch_get_note_comments(self, note_detail_list: List[TiebaNote]):
        """
        Batch get note comments
        Args:
            note_detail_list:

        Returns:

        """
        if not config.ENABLE_GET_COMMENTS:
            return

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list: List[Task] = []
        for note_detail in note_detail_list:
            task = asyncio.create_task(
                self.get_comments_async_task(note_detail, semaphore),
                name=note_detail.note_id,
            )
            task_list.append(task)
        await asyncio.gather(*task_list)

    async def get_comments_async_task(
        self, note_detail: TiebaNote, semaphore: asyncio.Semaphore
    ):
        """
        Get comments async task
        Args:
            note_detail:
            semaphore:

        Returns:

        """
        async with semaphore:
            utils.logger.info(
                f"[BaiduTieBaCrawler.get_comments] Begin get note id comments {note_detail.note_id}"
            )

            # Sleep before fetching comments
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
            utils.logger.info(f"[TieBaCrawler.get_comments_async_task] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds before fetching comments for note {note_detail.note_id}")

            await self.tieba_client.get_note_all_comments(
                note_detail=note_detail,
                crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                callback=tieba_store.batch_update_tieba_note_comments,
                max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
            )

    async def get_creators_and_notes(self) -> None:
        """
        Get creator's information and their notes and comments
        Returns:

        """
        utils.logger.info(
            "[WeiboCrawler.get_creators_and_notes] Begin get weibo creators"
        )
        for creator_url in config.TIEBA_CREATOR_URL_LIST:
            creator_page_html_content = await self.tieba_client.get_creator_info_by_url(
                creator_url=creator_url
            )
            creator_info: TiebaCreator = self._page_extractor.extract_creator_info(
                creator_page_html_content
            )
            if creator_info:
                utils.logger.info(
                    f"[WeiboCrawler.get_creators_and_notes] creator info: {creator_info}"
                )
                if not creator_info:
                    raise Exception("Get creator info error")

                await tieba_store.save_creator(user_info=creator_info)

                # Get all note information of the creator
                all_notes_list = (
                    await self.tieba_client.get_all_notes_by_creator_user_name(
                        user_name=creator_info.user_name,
                        crawl_interval=0,
                        callback=tieba_store.batch_update_tieba_notes,
                        max_note_count=config.CRAWLER_MAX_NOTES_COUNT,
                        creator_page_html_content=creator_page_html_content,
                    )
                )

                await self.batch_get_note_comments(all_notes_list)

            else:
                utils.logger.error(
                    f"[WeiboCrawler.get_creators_and_notes] get creator info error, creator_url:{creator_url}"
                )

    async def _navigate_to_tieba_via_baidu(self):
        """
        Simulate real user access path:
        1. First visit Baidu homepage (https://www.baidu.com/)
        2. Wait for page to load
        3. Click "Tieba" link in top navigation bar
        4. Jump to Tieba homepage

        This avoids triggering Baidu's security verification
        """
        utils.logger.info("[TieBaCrawler] Simulating real user access path...")

        try:
            # Step 1: Visit Baidu homepage
            utils.logger.info("[TieBaCrawler] Step 1: Visiting Baidu homepage https://www.baidu.com/")
            await self.context_page.goto("https://www.baidu.com/", wait_until="domcontentloaded")

            # Step 2: Wait for page loading, using delay setting from config file
            utils.logger.info(f"[TieBaCrawler] Step 2: Waiting {config.CRAWLER_MAX_SLEEP_SEC} seconds to simulate user browsing...")
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

            # Step 3: Find and click "Tieba" link
            utils.logger.info("[TieBaCrawler] Step 3: Finding and clicking 'Tieba' link...")

            # Try multiple selectors to ensure finding the Tieba link
            tieba_selectors = [
                'a[href="http://tieba.baidu.com/"]',
                'a[href="https://tieba.baidu.com/"]',
                'a.mnav:has-text("贴吧")',
                'text=贴吧',
            ]

            tieba_link = None
            for selector in tieba_selectors:
                try:
                    tieba_link = await self.context_page.wait_for_selector(selector, timeout=5000)
                    if tieba_link:
                        utils.logger.info(f"[TieBaCrawler] Found Tieba link (selector: {selector})")
                        break
                except Exception:
                    continue

            if not tieba_link:
                utils.logger.warning("[TieBaCrawler] Tieba link not found, directly accessing Tieba homepage")
                await self.context_page.goto(self.index_url, wait_until="domcontentloaded")
                return

            # Step 4: Click Tieba link (check if it will open in a new tab)
            utils.logger.info("[TieBaCrawler] Step 4: Clicking Tieba link...")

            # Check link's target attribute
            target_attr = await tieba_link.get_attribute("target")
            utils.logger.info(f"[TieBaCrawler] Link target attribute: {target_attr}")

            if target_attr == "_blank":
                # If it's a new tab, need to wait for new page and switch
                utils.logger.info("[TieBaCrawler] Link will open in new tab, waiting for new page...")

                async with self.browser_context.expect_page() as new_page_info:
                    await tieba_link.click()

                # Get newly opened page
                new_page = await new_page_info.value
                await new_page.wait_for_load_state("domcontentloaded")

                # Close old Baidu homepage
                await self.context_page.close()

                # Switch to new Tieba page
                self.context_page = new_page
                utils.logger.info("[TieBaCrawler] Successfully switched to new tab (Tieba page)")
            else:
                # If it's same tab navigation, wait for navigation normally
                utils.logger.info("[TieBaCrawler] Link navigates in current tab...")
                async with self.context_page.expect_navigation(wait_until="domcontentloaded"):
                    await tieba_link.click()

            # Step 5: Wait for page to stabilize, using delay setting from config file
            utils.logger.info(f"[TieBaCrawler] Step 5: Page loaded, waiting {config.CRAWLER_MAX_SLEEP_SEC} seconds...")
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

            current_url = self.context_page.url
            utils.logger.info(f"[TieBaCrawler] Successfully entered Tieba via Baidu homepage! Current URL: {current_url}")

        except Exception as e:
            utils.logger.error(f"[TieBaCrawler] Failed to access Tieba via Baidu homepage: {e}")
            utils.logger.info("[TieBaCrawler] Fallback: directly accessing Tieba homepage")
            await self.context_page.goto(self.index_url, wait_until="domcontentloaded")

    async def _inject_anti_detection_scripts(self):
        """
        Inject anti-detection JavaScript scripts
        For Baidu Tieba's special detection mechanism
        """
        utils.logger.info("[TieBaCrawler] Injecting anti-detection scripts...")

        # Lightweight anti-detection script, only covering key detection points
        anti_detection_js = """
        // Override navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });

        // Override window.navigator.chrome
        if (!window.navigator.chrome) {
            window.navigator.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
        }

        // Override Permissions API
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );

        // Override plugins length (make it look like there are plugins)
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
            configurable: true
        });

        // Override languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en'],
            configurable: true
        });

        // Remove window.cdc_ and other ChromeDriver remnants
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

        console.log('[Anti-Detection] Scripts injected successfully');
        """

        await self.browser_context.add_init_script(anti_detection_js)
        utils.logger.info("[TieBaCrawler] Anti-detection scripts injected")

    async def create_tieba_client(
        self, httpx_proxy: Optional[str], ip_pool: Optional[ProxyIpPool] = None
    ) -> BaiduTieBaClient:
        """
        Create tieba client with real browser User-Agent and complete headers
        Args:
            httpx_proxy: HTTP proxy
            ip_pool: IP proxy pool

        Returns:
            BaiduTieBaClient instance
        """
        utils.logger.info("[TieBaCrawler.create_tieba_client] Begin create tieba API client...")

        # Extract User-Agent from real browser to avoid detection
        user_agent = await self.context_page.evaluate("() => navigator.userAgent")
        utils.logger.info(f"[TieBaCrawler.create_tieba_client] Extracted User-Agent from browser: {user_agent}")

        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())

        # Build complete browser request headers, simulating real browser behavior
        tieba_client = BaiduTieBaClient(
            timeout=10,
            ip_pool=ip_pool,
            default_ip_proxy=httpx_proxy,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "User-Agent": user_agent,  # Use real browser UA
                "Cookie": cookie_str,
                "Host": "tieba.baidu.com",
                "Referer": "https://tieba.baidu.com/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
            browser_page=self.context_page,  # Pass in browser page object
        )
        return tieba_client

    async def launch_browser(
        self,
        browser_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> Any:
        """
        Launch CloakBrowser and create browser context.
        Args:
            browser_proxy:
            user_agent:
            headless:

        Returns:

        """
        utils.logger.info(
            "[BaiduTieBaCrawler.launch_browser] Begin create browser context ..."
        )
        return await launch_cloak_browser_context(
            browser_proxy, user_agent, headless
        )

    async def close(self):
        """
        Close browser context
        Returns:

        """
        browser_context = getattr(self, "browser_context", None)
        if browser_context:
            await browser_context.close()
            utils.logger.info("[BaiduTieBaCrawler.close] Browser context closed ...")
