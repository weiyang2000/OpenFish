# -*- coding: utf-8 -*-
"""Scrapling-based Zhihu crawler pilot.

This module keeps Zhihu's platform semantics in MediaCrawler while moving the
request scheduling and fetching flow to Scrapling Spider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional
from urllib.parse import urlencode

import config
from constant import zhihu as zhihu_constant
from model.m_zhihu import ZhihuComment, ZhihuContent, ZhihuCreator
from scrapling.fetchers import AsyncStealthySession, FetcherSession
from scrapling.spiders import Request, Response, Spider
from store import zhihu as zhihu_store
from tools import date_filter, utils
from tools.cloak_browser import _profile_dir
from var import crawler_type_var, source_keyword_var

from .field import SearchSort, SearchTime, SearchType
from .help import ZhihuExtractor, judge_zhihu_url, sign
from .login import ZhiHuLogin


class ScraplingZhihuError(RuntimeError):
    """Raised when the Scrapling Zhihu pilot cannot complete safely."""


@dataclass
class ZhihuScraplingState:
    cookie_str: str = ""
    cookie_dict: Dict[str, str] = field(default_factory=dict)
    contents_saved: int = 0
    creators_saved: int = 0
    comments_seen: int = 0
    accepted_by_keyword: Dict[str, int] = field(default_factory=dict)
    fatal_errors: list[str] = field(default_factory=list)

    def update_cookies(self, cookie_dict: Dict[str, str]) -> None:
        if not cookie_dict:
            return
        self.cookie_dict.update({key: value for key, value in cookie_dict.items() if key})
        self.cookie_str = ";".join(
            f"{key}={value}" for key, value in self.cookie_dict.items() if value is not None
        )

    @property
    def has_required_cookie(self) -> bool:
        # d_c0 is required by the existing Zhihu signing algorithm.
        return bool(self.cookie_dict.get("d_c0"))

    @property
    def has_login_cookie(self) -> bool:
        return bool(self.cookie_dict.get("z_c0"))


def _cookie_dict_from_response(cookies: Any) -> Dict[str, str]:
    if not cookies:
        return {}

    result: Dict[str, str] = {}
    if isinstance(cookies, dict):
        for key, value in cookies.items():
            if isinstance(value, dict):
                name = value.get("name") or key
                cookie_value = value.get("value")
            else:
                name = key
                cookie_value = value
            if name and cookie_value is not None:
                result[str(name)] = str(cookie_value)
        return result

    if isinstance(cookies, (list, tuple)):
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value is not None:
                result[str(name)] = str(value)
    return result


def _json_response(response: Any, *, allow_404: bool = False) -> Dict:
    if allow_404 and getattr(response, "status", None) == 404:
        return {}
    if getattr(response, "status", None) != 200:
        raise ScraplingZhihuError(
            f"Zhihu Scrapling request failed: {getattr(response, 'status', 'unknown')} "
            f"{getattr(response, 'url', '')}"
        )

    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        encoding = getattr(response, "encoding", None) or "utf-8"
        text = body.decode(encoding, errors="replace")
    else:
        text = str(body)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScraplingZhihuError(
            f"Zhihu returned non-JSON response from {getattr(response, 'url', '')}"
        ) from exc

    if isinstance(data, dict) and data.get("error"):
        raise ScraplingZhihuError(f"Zhihu API error: {data.get('error')}")
    return data if isinstance(data, dict) else {}


def _response_text(response: Any, *, allow_404: bool = False) -> str:
    if allow_404 and getattr(response, "status", None) == 404:
        return ""
    if getattr(response, "status", None) != 200:
        raise ScraplingZhihuError(
            f"Zhihu Scrapling page request failed: {getattr(response, 'status', 'unknown')} "
            f"{getattr(response, 'url', '')}"
        )

    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        encoding = getattr(response, "encoding", None) or "utf-8"
        return body.decode(encoding, errors="replace")
    return str(body)


def _search_referer(index_url: str, keyword: str) -> str:
    query = urlencode(
        {
            "q": keyword,
            "search_source": "Filter",
            "type": "content",
        }
    )
    return f"{index_url}/search?{query}"


async def run_scrapling_zhihu_crawler() -> ZhihuScraplingState:
    """Run the Scrapling Spider pilot for Zhihu crawling."""

    class ScraplingZhihuSpider(Spider):
        name = "zhihu_scrapling"
        allowed_domains = {"zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com"}
        robots_txt_obey = False
        fp_include_headers = True
        logging_level = 20

        def __init__(self) -> None:
            self.index_url = zhihu_constant.ZHIHU_URL
            self.user_agent = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
            self.timeout = 30
            self.max_notes = max(0, int(config.CRAWLER_MAX_NOTES_COUNT))
            self.start_page = max(1, int(config.START_PAGE))
            self.page_size = 20
            self._extractor = ZhihuExtractor()
            self.state = ZhihuScraplingState()
            self._bootstrap_from_config_cookies()
            super().__init__()
            self.concurrent_requests = max(1, int(getattr(config, "MAX_CONCURRENCY_NUM", 1) or 1))
            self.concurrent_requests_per_domain = self.concurrent_requests
            self.download_delay = max(0.0, float(getattr(config, "CRAWLER_MAX_SLEEP_SEC", 0) or 0))

        def configure_sessions(self, manager: Any) -> None:
            manager.add(
                "http",
                FetcherSession(
                    timeout=self.timeout,
                    retries=3,
                    retry_delay=1,
                    follow_redirects=True,
                    stealthy_headers=False,
                ),
                default=True,
            )
            manager.add(
                "browser",
                AsyncStealthySession(
                    headless=config.HEADLESS,
                    useragent=self.user_agent,
                    user_data_dir=_profile_dir(),
                    network_idle=True,
                    timeout=60_000,
                    block_webrtc=True,
                    hide_canvas=True,
                    retries=1,
                    retry_delay=1,
                ),
                lazy=True,
            )

        def _bootstrap_from_config_cookies(self) -> None:
            if not getattr(config, "COOKIES", ""):
                return
            self.state.update_cookies(utils.convert_str_cookie_to_dict(config.COOKIES))

        async def start_requests(self) -> AsyncGenerator[Any, None]:
            crawler_type_var.set(config.CRAWLER_TYPE)
            if self.state.has_required_cookie and self.state.has_login_cookie:
                async for request in self._entry_requests():
                    yield request
                return

            utils.logger.info("[ZhihuScraplingSpider] Bootstrap login cookies with Scrapling browser session")
            yield Request(
                self.index_url,
                sid="browser",
                callback=self.parse_bootstrap,
                dont_filter=True,
                wait=3_000,
                network_idle=True,
                google_search=False,
                page_action=self._bootstrap_login,
            )

        async def parse_bootstrap(self, response: Response) -> AsyncGenerator[Any, None]:
            self.state.update_cookies(_cookie_dict_from_response(response.cookies))
            if not self.state.has_required_cookie:
                raise ScraplingZhihuError(
                    "Zhihu Scrapling bootstrap did not find d_c0 cookie. "
                    "Complete Zhihu account login first or explicitly use the legacy engine."
                )
            if not self.state.cookie_dict.get("z_c0"):
                raise ScraplingZhihuError(
                    "Zhihu Scrapling bootstrap did not find z_c0 login cookie. "
                    "Complete Zhihu login or set valid config.COOKIES."
                )

            yield self._api_request(
                "/api/v4/me",
                {"include": "email,is_active,is_bind_phone"},
                callback=self.parse_pong,
                meta={"kind": "pong"},
                referer=f"{self.index_url}/search?q=python&time_interval=a_year&type=content",
            )

        async def parse_pong(self, response: Response) -> AsyncGenerator[Any, None]:
            data = _json_response(response)
            if not data.get("uid") or not data.get("name"):
                raise ScraplingZhihuError("Zhihu Scrapling cookie validation failed")

            utils.logger.info("[ZhihuScraplingSpider] Zhihu cookie validation succeeded")
            async for request in self._entry_requests():
                yield request

        async def _bootstrap_login(self, page: Any) -> None:
            cookie_dict = _cookie_dict_from_response(await page.context.cookies())
            if not cookie_dict.get("z_c0"):
                utils.logger.info("[ZhihuScraplingSpider] Login cookie z_c0 missing, starting Zhihu login")
                login_obj = ZhiHuLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=page.context,
                    context_page=page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()

            await page.goto(
                f"{self.index_url}/search?q=python&search_source=Guess&utm_content=search_hot&type=content",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(5_000)

        async def _entry_requests(self) -> AsyncGenerator[Any, None]:
            crawler_type = config.CRAWLER_TYPE
            if crawler_type == "search":
                for keyword in self._keywords():
                    yield self._search_request(keyword, self.start_page)
                return

            if crawler_type == "detail":
                for full_note_url in self._specified_note_urls():
                    request = self._note_detail_request(full_note_url)
                    if request:
                        yield request
                return

            if crawler_type == "creator":
                for creator_url in self._creator_urls():
                    yield self._creator_profile_request(creator_url)
                return

            raise ScraplingZhihuError(
                "Zhihu Scrapling spider supports crawler types: search, detail, creator"
            )

        async def parse(self, response: Response) -> AsyncGenerator[Any, None]:
            # All requests are scheduled with explicit callbacks.
            return
            yield

        async def parse_search(self, response: Response) -> AsyncGenerator[Any, None]:
            keyword = str(response.meta["keyword"])
            page = int(response.meta["page"])
            data = _json_response(response)
            content_list: List[ZhihuContent] = self._extractor.extract_contents_from_search(data)
            utils.logger.info(
                f"[ZhihuScraplingSpider] Search keyword={keyword}, page={page}, contents={len(content_list)}"
            )
            if not content_list:
                return
            if not date_filter.has_new_time_records("zhihu", "content", content_list):
                utils.logger.info("[ZhihuScraplingSpider] No new-time contents before filtering, stop crawling")
                return

            accepted = self.state.accepted_by_keyword.get(keyword, 0)
            for content in content_list:
                if accepted >= self.max_notes:
                    break
                source_keyword_var.set(keyword)
                stored = await zhihu_store.update_zhihu_content(content)
                if not stored:
                    continue

                accepted += 1
                self.state.accepted_by_keyword[keyword] = accepted
                self.state.contents_saved += 1
                yield {
                    "type": "zhihu_content",
                    "keyword": keyword,
                    "contentId": content.content_id,
                    "contentType": content.content_type,
                }

                if config.ENABLE_GET_COMMENTS:
                    yield self._root_comment_request(content)

            if accepted < self.max_notes:
                yield self._search_request(keyword, page + 1)

        async def parse_note_detail(self, response: Response) -> AsyncGenerator[Any, None]:
            note_type = str(response.meta["note_type"])
            full_note_url = str(response.meta["full_note_url"])
            html_content = _response_text(response)
            note_detail = self._extract_note_detail(note_type, html_content)
            if not note_detail:
                utils.logger.info(f"[ZhihuScraplingSpider] Note {full_note_url} not found")
                return

            source_keyword_var.set("")
            stored = await zhihu_store.update_zhihu_content(note_detail)
            if stored:
                self.state.contents_saved += 1
                yield {
                    "type": "zhihu_content",
                    "contentId": note_detail.content_id,
                    "contentType": note_detail.content_type,
                }

            if config.ENABLE_GET_COMMENTS:
                yield self._root_comment_request(note_detail)

        async def parse_creator_profile(self, response: Response) -> AsyncGenerator[Any, None]:
            url_token = str(response.meta["url_token"])
            html_content = _response_text(response)
            creator: Optional[ZhihuCreator] = self._extractor.extract_creator(url_token, html_content)
            if not creator:
                utils.logger.info(f"[ZhihuScraplingSpider] Creator {url_token} not found")
                return

            await zhihu_store.save_creator(creator)
            self.state.creators_saved += 1
            yield {
                "type": "zhihu_creator",
                "urlToken": creator.url_token,
                "userId": creator.user_id,
            }
            yield self._creator_answers_request(creator.url_token, offset=0)

        async def parse_creator_answers(self, response: Response) -> AsyncGenerator[Any, None]:
            url_token = str(response.meta["url_token"])
            offset = int(response.meta["offset"])
            limit = int(response.meta["limit"])
            data = _json_response(response)
            contents = self._extractor.extract_content_list_from_creator(data.get("data"))
            utils.logger.info(
                f"[ZhihuScraplingSpider] Creator {url_token} answers offset={offset}, contents={len(contents)}"
            )
            source_keyword_var.set("")
            for content in contents:
                stored = await zhihu_store.update_zhihu_content(content)
                if stored:
                    self.state.contents_saved += 1
                    yield {
                        "type": "zhihu_content",
                        "contentId": content.content_id,
                        "contentType": content.content_type,
                        "creator": url_token,
                    }

                if config.ENABLE_GET_COMMENTS:
                    yield self._root_comment_request(content)

            paging_info = data.get("paging", {})
            if not paging_info.get("is_end") and contents:
                yield self._creator_answers_request(url_token, offset=offset + limit, limit=limit)

        async def parse_root_comments(self, response: Response) -> AsyncGenerator[Any, None]:
            content = response.meta["content"]
            data = _json_response(response, allow_404=True)
            if not data:
                return

            comments = self._extractor.extract_comments(content, data.get("data"))
            if comments:
                await zhihu_store.batch_update_zhihu_note_comments(comments)
                self.state.comments_seen += len(comments)
                yield {
                    "type": "zhihu_comments",
                    "contentId": content.content_id,
                    "count": len(comments),
                }

            if config.ENABLE_GET_SUB_COMMENTS:
                for comment in comments:
                    if comment.sub_comment_count > 0:
                        yield self._child_comment_request(content, comment)

            paging_info = data.get("paging", {})
            if not paging_info.get("is_end"):
                offset = self._extractor.extract_offset(paging_info)
                if offset:
                    yield self._root_comment_request(content, offset=offset)

        async def parse_child_comments(self, response: Response) -> AsyncGenerator[Any, None]:
            content = response.meta["content"]
            root_comment = response.meta["root_comment"]
            data = _json_response(response, allow_404=True)
            if not data:
                return

            comments = self._extractor.extract_comments(content, data.get("data"))
            if comments:
                await zhihu_store.batch_update_zhihu_note_comments(comments)
                self.state.comments_seen += len(comments)
                yield {
                    "type": "zhihu_sub_comments",
                    "contentId": content.content_id,
                    "rootCommentId": root_comment.comment_id,
                    "count": len(comments),
                }

            paging_info = data.get("paging", {})
            if not paging_info.get("is_end"):
                offset = self._extractor.extract_offset(paging_info)
                if offset:
                    yield self._child_comment_request(content, root_comment, offset=offset)

        async def on_error(self, request: Any, error: Exception) -> None:
            message = f"{request.url}: {error}"
            self.state.fatal_errors.append(message)
            utils.logger.error(f"[ZhihuScraplingSpider] {message}")

        async def is_blocked(self, response: Response) -> bool:
            # Let callbacks parse non-200 responses so fatal errors propagate
            # with the target URL and status.
            return False

        def _keywords(self) -> list[str]:
            return [keyword.strip() for keyword in config.KEYWORDS.split(",") if keyword.strip()]

        @staticmethod
        def _specified_note_urls() -> list[str]:
            return [
                note_url.split("?")[0].strip()
                for note_url in getattr(config, "ZHIHU_SPECIFIED_ID_LIST", [])
                if note_url.strip()
            ]

        @staticmethod
        def _creator_urls() -> list[str]:
            return [
                creator_url.split("?")[0].rstrip("/").strip()
                for creator_url in getattr(config, "ZHIHU_CREATOR_URL_LIST", [])
                if creator_url.strip()
            ]

        @staticmethod
        def _creator_url_token(creator_url: str) -> str:
            return creator_url.rstrip("/").split("/")[-1]

        def _extract_note_detail(self, note_type: str, html_content: str) -> Optional[ZhihuContent]:
            if note_type == zhihu_constant.ANSWER_NAME:
                return self._extractor.extract_answer_content_from_html(html_content)
            if note_type == zhihu_constant.ARTICLE_NAME:
                return self._extractor.extract_article_content_from_html(html_content)
            if note_type == zhihu_constant.VIDEO_NAME:
                return self._extractor.extract_zvideo_content_from_html(html_content)
            return None

        def _search_request(self, keyword: str, page: int) -> Any:
            params = {
                "gk_version": "gz-gaokao",
                "t": "general",
                "q": keyword,
                "correction": 1,
                "offset": (page - 1) * self.page_size,
                "limit": self.page_size,
                "filter_fields": "",
                "lc_idx": (page - 1) * self.page_size,
                "show_all_topics": 0,
                "search_source": "Filter",
                "time_interval": SearchTime.DEFAULT.value,
                "sort": SearchSort.DEFAULT.value,
                "vertical": SearchType.DEFAULT.value,
            }
            return self._api_request(
                "/api/v4/search_v3",
                params,
                callback=self.parse_search,
                meta={"kind": "search", "keyword": keyword, "page": page},
                referer=_search_referer(self.index_url, keyword),
            )

        def _note_detail_request(self, full_note_url: str) -> Optional[Any]:
            note_type = judge_zhihu_url(full_note_url)
            if note_type == zhihu_constant.ANSWER_NAME:
                question_id = full_note_url.split("/")[-3]
                answer_id = full_note_url.split("/")[-1]
                uri = f"/question/{question_id}/answer/{answer_id}"
            elif note_type == zhihu_constant.ARTICLE_NAME:
                article_id = full_note_url.split("/")[-1]
                uri = f"/p/{article_id}"
            elif note_type == zhihu_constant.VIDEO_NAME:
                video_id = full_note_url.split("/")[-1]
                uri = f"/zvideo/{video_id}"
            else:
                utils.logger.info(f"[ZhihuScraplingSpider] Unsupported Zhihu URL: {full_note_url}")
                return None

            return self._api_request(
                uri,
                {},
                callback=self.parse_note_detail,
                meta={
                    "kind": "note_detail",
                    "full_note_url": full_note_url,
                    "note_type": note_type,
                },
                referer=full_note_url,
            )

        def _creator_profile_request(self, creator_url: str) -> Any:
            url_token = self._creator_url_token(creator_url)
            return self._api_request(
                f"/people/{url_token}",
                {},
                callback=self.parse_creator_profile,
                meta={"kind": "creator_profile", "url_token": url_token},
                referer=creator_url,
            )

        def _creator_answers_request(self, url_token: str, offset: int = 0, limit: int = 20) -> Any:
            params = {
                "include": (
                    "data[*].is_normal,admin_closed_comment,reward_info,is_collapsed,"
                    "annotation_action,annotation_detail,collapse_reason,collapsed_by,"
                    "suggest_edit,comment_count,can_comment,content,editable_content,"
                    "attachment,voteup_count,reshipment_settings,comment_permission,"
                    "created_time,updated_time,review_info,excerpt,paid_info,"
                    "reaction_instruction,is_labeled,label_info,relationship.is_authorized,"
                    "voting,is_author,is_thanked,is_nothelp;data[*].vessay_info;"
                    "data[*].author.badge[?(type=best_answerer)].topics;"
                    "data[*].author.vip_info;data[*].question.has_publishing_draft,relationship"
                ),
                "offset": offset,
                "limit": limit,
                "order_by": "created",
            }
            return self._api_request(
                f"/api/v4/members/{url_token}/answers",
                params,
                callback=self.parse_creator_answers,
                meta={
                    "kind": "creator_answers",
                    "url_token": url_token,
                    "offset": offset,
                    "limit": limit,
                },
                referer=f"{self.index_url}/people/{url_token}/answers",
            )

        def _root_comment_request(self, content: ZhihuContent, offset: str = "") -> Any:
            uri = f"/api/v4/comment_v5/{content.content_type}s/{content.content_id}/root_comment"
            return self._api_request(
                uri,
                {"order": "score", "offset": offset, "limit": 10},
                callback=self.parse_root_comments,
                meta={"kind": "root_comment", "content": content, "offset": offset},
                referer=content.content_url or self.index_url,
                dont_filter=True,
            )

        def _child_comment_request(
            self,
            content: ZhihuContent,
            root_comment: ZhihuComment,
            offset: str = "",
        ) -> Any:
            uri = f"/api/v4/comment_v5/comment/{root_comment.comment_id}/child_comment"
            return self._api_request(
                uri,
                {"order": "sort", "offset": offset, "limit": 10},
                callback=self.parse_child_comments,
                meta={
                    "kind": "child_comment",
                    "content": content,
                    "root_comment": root_comment,
                    "offset": offset,
                },
                referer=content.content_url or self.index_url,
                dont_filter=True,
            )

        def _api_request(
            self,
            uri: str,
            params: Dict[str, Any],
            *,
            callback: Callable[[Any], AsyncGenerator[Any, None]],
            meta: Dict[str, Any],
            referer: str,
            dont_filter: bool = False,
        ) -> Any:
            final_uri = uri
            if params:
                final_uri = f"{uri}?{urlencode(params)}"
            base_url = zhihu_constant.ZHIHU_ZHUANLAN_URL if "/p/" in uri else zhihu_constant.ZHIHU_URL
            headers = self._signed_headers(final_uri, referer)
            return Request(
                f"{base_url}{final_uri}",
                sid="http",
                callback=callback,
                meta=meta,
                dont_filter=dont_filter,
                headers=headers,
                timeout=self.timeout,
                follow_redirects=True,
            )

        def _signed_headers(self, final_uri: str, referer: str) -> Dict[str, str]:
            if not self.state.has_required_cookie:
                raise ScraplingZhihuError("Cannot sign Zhihu request without d_c0 cookie")
            sign_res = sign(final_uri, self.state.cookie_str)
            return {
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cookie": self.state.cookie_str,
                "priority": "u=1, i",
                "referer": referer,
                "user-agent": self.user_agent,
                "x-api-version": "3.0.91",
                "x-app-za": "OS=Web",
                "x-requested-with": "fetch",
                "x-zse-93": "101_3_3.0",
                "x-zst-81": sign_res["x-zst-81"],
                "x-zse-96": sign_res["x-zse-96"],
            }

    spider = ScraplingZhihuSpider()
    async for _ in spider.stream():
        pass

    if (
        spider.state.fatal_errors
        and spider.state.contents_saved == 0
        and spider.state.creators_saved == 0
    ):
        raise ScraplingZhihuError("; ".join(spider.state.fatal_errors[:3]))

    utils.logger.info(
        "[ZhihuScraplingSpider] Finished, "
        f"contents={spider.state.contents_saved}, "
        f"creators={spider.state.creators_saved}, "
        f"comments={spider.state.comments_seen}"
    )
    return spider.state
